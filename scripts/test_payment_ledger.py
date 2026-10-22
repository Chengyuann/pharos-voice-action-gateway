from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import pytest
from payment_ledger import PaymentLedger, BudgetDenied, verify_audit_chain, load_budget_policy
from pharos_voice_action_gateway import sha256_json

NOW = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)


def issue(ledger, n=1, amount=60, now=NOW, scope='mcp:1672:0x'+'3'*40+':USDC'):
    return ledger.authorize(draft={'id': f'{n:032x}'}, action_id=f'{n+100:032x}',
        charges=[{'scope': scope, 'asset':scope.rsplit(':',1)[-1], 'units':str(amount), 'limit_units':'100'}],
        window_seconds=60, now=now)


def test_concurrent_budget_has_no_check_then_insert_race(tmp_path):
    ledger = PaymentLedger(tmp_path/'ledger.sqlite3')
    def worker(n):
        try:return issue(ledger, n)
        except BudgetDenied:return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker, (1,2)))
    assert sum(isinstance(x,dict) for x in results)==1
    assert results.count('denied')==1
    exported=ledger.export()
    assert [e['kind'] for e in exported['entries']].count('draft_budget_reserved')==1
    assert verify_audit_chain(exported)['verified']


def test_idempotency_bound_to_details_and_cancellation_never_refunds(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    assert issue(ledger,amount=100)['status']=='counted'
    assert issue(ledger,amount=100)['status']=='already_counted'
    with pytest.raises(ValueError,match='reused'):issue(ledger,amount=1)
    ledger.append('action_cancelled',{'action_id':'a'*32},NOW)
    with pytest.raises(BudgetDenied):issue(ledger,n=2,amount=1)
    assert issue(ledger,n=3,amount=100,now=NOW+timedelta(seconds=60))['status']=='counted'


def test_network_sender_and_demo_are_distinct_scopes(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    issue(ledger,amount=100)
    for i,scope in enumerate(['demo:1672:0x'+'3'*40+':USDC','mcp:1672:0x'+'4'*40+':USDC','live:688689:0x'+'3'*40+':PHRS'],2):
        assert issue(ledger,n=i,amount=100,scope=scope)['status']=='counted'


def test_clock_rollback_and_rewrite_fail_closed(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    issue(ledger)
    with pytest.raises(ValueError,match='backwards'):issue(ledger,n=2,now=NOW-timedelta(seconds=1))
    with sqlite3.connect(ledger.path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM p1_audit')
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE p1_issuances SET charges='[]'")


def test_external_tip_detects_truncation_and_rehash(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    ledger.append('one',{'x':1},NOW);ledger.append('two',{'x':2},NOW)
    chain=ledger.export(); tip=chain['tip']
    assert verify_audit_chain(chain,tip)['verified']
    tampered=deepcopy(chain);tampered['entries'][0]['payload']['x']=3
    with pytest.raises(ValueError):verify_audit_chain(tampered)
    truncated=deepcopy(chain);truncated['entries'].pop();truncated['tip']=truncated['entries'][-1]['entry_hash']
    assert verify_audit_chain(truncated)['verified']
    with pytest.raises(ValueError):verify_audit_chain(truncated,tip)
    rewritten=deepcopy(truncated);entry=rewritten['entries'][0];entry['payload']={'x':9}
    entry['entry_hash']='0x'+sha256_json({k:v for k,v in entry.items() if k!='entry_hash'})
    rewritten['tip']=entry['entry_hash']
    assert verify_audit_chain(rewritten)['verified']
    with pytest.raises(ValueError):verify_audit_chain(rewritten,tip)


def test_atomic_multiasset_charge(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    kwargs=dict(draft={'id':'a'*32}, action_id='b'*32, now=NOW,window_seconds=60,
                charges=[{'scope':'mcp:1672:0x'+'3'*40+':USDC','asset':'USDC','units':'1','limit_units':'10'},
                         {'scope':'mcp:1672:0x'+'3'*40+':PROS','asset':'PROS','units':'20','limit_units':'10'}])
    with pytest.raises(BudgetDenied):ledger.authorize(**kwargs)
    assert not ledger.export()['entries']
    with ledger.transaction() as db:assert db.execute('SELECT count(*) FROM p1_issuances').fetchone()[0]==0


def test_budget_policy_config_is_valid():
    assert load_budget_policy()['counting_basis']=='unsigned_draft_issued'


def test_tampered_budget_projection_blocks_even_after_guard_removed(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    issue(ledger)
    with sqlite3.connect(ledger.path) as db:
        db.execute('DROP TRIGGER p1_issuances_no_update')
        db.execute("UPDATE p1_issuances SET charges='[]'")
    with pytest.raises(ValueError,match='do not match'):issue(ledger,n=2)
    with pytest.raises(ValueError,match='do not match'):ledger.export()


def test_parallel_append_produces_unbroken_chain(tmp_path):
    ledger=PaymentLedger(tmp_path/'ledger.sqlite3')
    # Initialize schema before parallel work, as a service does.
    ledger.export()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n:ledger.append('event',{'n':n},NOW),range(24)))
    chain=ledger.export()
    assert verify_audit_chain(chain)['entries']==24
    assert sorted(entry['payload']['n'] for entry in chain['entries'])==list(range(24))
