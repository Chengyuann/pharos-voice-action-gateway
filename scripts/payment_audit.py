#!/usr/bin/env python3
"""Export or verify local audit chains; keep trusted tips outside mutable storage."""
import argparse
import json
import sqlite3
from pathlib import Path
from payment_ledger import PaymentLedger, verify_audit_chain, verify_report_files
from pharos_voice_action_gateway import strict_json_loads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("database", type=Path)
    export.add_argument("--output", required=True, type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("file", type=Path)
    verify.add_argument("--expected-tip", help="Previously saved trusted tip; required to detect truncation")
    verify.add_argument("--reports", type=Path, help="Also verify referenced report JSON files in this directory")
    args = parser.parse_args()
    try:
        if args.command == "export":
            if not args.database.is_file():
                raise ValueError("Existing local audit database is required")
            chain = PaymentLedger(args.database).export()
            with args.output.open("x") as stream:
                json.dump(chain, stream, ensure_ascii=False, indent=2)
            print(json.dumps(verify_audit_chain(chain), indent=2))
        else:
            if args.file.stat().st_size > 20_000_000:
                raise ValueError("Audit export exceeds 20 MB")
            chain = strict_json_loads(args.file.read_text())
            result = verify_audit_chain(chain, args.expected_tip)
            if args.reports:
                result.update(verify_report_files(chain, args.reports))
            print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
