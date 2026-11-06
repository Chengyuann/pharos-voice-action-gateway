from pathlib import Path
import subprocess


def test_sandbox_exact_amount_and_policy():
    root = Path(__file__).resolve().parents[1]
    code = """
const assert=require('node:assert/strict');
const {previewExample:p}=require('./site/sandbox.js');
assert.equal(p('I would like to send one USDC to Alice.').status,'allowed');
assert.equal(p('Send 1 or 2 USDC to Alice').status,'clarify');
assert.equal(p('Send 6 USDC to Alice').status,'blocked');
assert.equal(p('Send 1 USDC to Alice',{hour:17}).status,'blocked');
assert.equal(p('Send 1 USDC to Mallory').status,'blocked');
assert.equal(p('Send 0.0000001 USDC to Alice').status,'blocked');
assert.equal(p('Send 0.000000000000000001 PROS to Alice').status,'allowed');
assert.equal(p('Do not send 1 USDC to Alice').status,'blocked');
assert.notEqual(p('Send 1 USDC to Alice and send 2 to Bob').status,'allowed');
assert.notEqual(p('<script>alert(1)</script>').status,'allowed');
"""
    subprocess.run(["node", "-e", code], cwd=root, check=True)
