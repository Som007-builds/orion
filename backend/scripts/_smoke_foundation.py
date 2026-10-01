"""Smoke test for Phase 0-3 foundation. Run: python scripts/_smoke_foundation.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.sqlite import get_connection, init_db, schema_version
from app.db.duckdb_client import DuckDBClient
from app.services.ledger import Ledger, LedgerAction

init_db()
conn = get_connection()

tables = [
    r["name"]
    for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
]
triggers = [
    r["name"]
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
]

print(f"schema_version : {schema_version()}")
print(f"journal_mode   : {conn.execute('PRAGMA journal_mode').fetchone()[0]}")
print(f"sqlite tables  : {len(tables)}")
for t in tables:
    print(f"   - {t}")
print(f"triggers       : {triggers}")

duck = DuckDBClient()
duck.init()
with duck.reader() as r:
    ev = [
        row[0]
        for row in r.execute(
            "SELECT table_name FROM information_schema.tables ORDER BY table_name"
        ).fetchall()
    ]
print(f"evidence tables: {len(ev)}")
for t in ev:
    print(f"   - {t}")

# --- ledger chain ---
ledger = Ledger()
for i in range(3):
    ledger.append("smoke@test", LedgerAction.SUBMISSION_RECEIVED, {"i": i}, entity_id="CSE-TEST")
result = ledger.verify()
print(f"\nledger verify  : valid={result.valid} checked={result.entries_checked} head={result.head_hash[:16]}…")

# --- append-only enforcement ---
conn.execute("BEGIN")
try:
    conn.execute("UPDATE ledger_entry SET actor='tamper' WHERE seq=1")
    print("APPEND-ONLY    : FAIL — UPDATE succeeded")
except Exception as exc:
    conn.execute("ROLLBACK")
    print(f"append-only    : UPDATE blocked ({exc})")

conn.execute("BEGIN")
try:
    conn.execute("DELETE FROM ledger_entry WHERE seq=1")
    print("APPEND-ONLY    : FAIL — DELETE succeeded")
except Exception as exc:
    conn.execute("ROLLBACK")
    print(f"append-only    : DELETE blocked ({exc})")

# --- tamper detection ---
conn.execute("PRAGMA writable_schema=OFF")
try:
    conn.execute("DROP TRIGGER ledger_no_update")
    conn.execute("UPDATE ledger_entry SET actor='tamper' WHERE seq=1")
    conn.executescript(
        "CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger_entry "
        "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    )
    broken = ledger.verify()
    print(f"tamper detect  : valid={broken.valid} first_break_seq={broken.first_break_seq} reason={broken.break_reason}")
except Exception as exc:
    print(f"tamper test    : skipped ({exc})")