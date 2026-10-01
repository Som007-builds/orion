"""Prove the air gap instead of asserting it.

Runs the whole pipeline -- startup, ingest, indicators, scoring, export
sanitisation -- with every outbound network primitive replaced by a recorder that
also raises. Anything that tries to leave the machine fails loudly and is named
in the report, so a pass is evidence rather than a claim.

Two independent checks, because each can be fooled on its own:

* **dynamic** -- the exercised code paths attempt no connection at all, and the
  set of loaded modules afterwards contains nothing that could make one.
* **static** -- every import and egress-capable call site in `app/` is scanned, so
  a latent call on a path this script does not exercise is still reported.

The dynamic half only covers the paths it runs. That limitation is real, which is
why the static half exists and why its result is printed rather than summarised.

Usage:  python scripts/sovereignty_check.py
Exit 0 on pass, 1 on any finding.
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import socket
import sys
import tempfile
import traceback
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# A scratch lake. The check must not touch the operator's real data, and must not
# be the reason a real submission gets rejected twice.
WORK = Path(tempfile.mkdtemp(prefix="orion_sovereignty_"))
os.environ.setdefault("ORION_ENV", "dev")
os.environ["ORION_DATA_DIR"] = str(WORK / "data")
os.environ["ORION_SQLITE_PATH"] = str(WORK / "data" / "sqlite" / "orion.db")
os.environ["ORION_PARQUET_DIR"] = str(WORK / "data" / "parquet")

# ------------------------------------------------- libraries that can leave ----
# Anything here being *loaded* during a full pipeline run is a finding on its
# own: the capability was present even if this particular path never pulled the
# trigger.
EGRESS_MODULES = frozenset({
    "requests", "urllib3", "httpx", "aiohttp", "http.client", "urllib.request",
    "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc",
    "boto3", "botocore", "google", "azure", "paramiko", "fabric",
    "docker", "kubernetes", "pymongo", "redis", "celery", "rq",
    "openai", "anthropic", "huggingface_hub", "transformers", "torch",
    "sentry_sdk", "opentelemetry", "statsd", "datadog", "newrelic",
})

# Static scan: bare names that can only mean an outbound call.
#
# Kept deliberately tiny. A wider list of "suspicious" names produced 42 false
# positives on this codebase's own `get_connection` -- the SQLite accessor -- and a
# scanner that cries wolf gets switched off, which is worse than no scanner. The
# import check and the attribute check below carry the real weight; this is a
# cheap net for a direct `urlopen` in a function that never imports urllib by name.
EGRESS_CALLS = frozenset({"urlopen", "urlretrieve"})
EGRESS_ATTRIBUTES = frozenset({"connect", "connect_ex", "sendto", "sendmsg"})

# Base expressions that make an attribute call demonstrably a local one. A
# `.connect` on a duckdb or sqlite handle opens a file, not a socket.
LOCAL_BASES = ("duckdb", "sqlite", "database", "get_connection", "con", "db.")


class EgressDenied(RuntimeError):
    """Raised in place of any outbound connection attempt."""


class EgressGuard:
    """Replaces every outbound network primitive with a recorder that raises.

    Recording *and* raising, rather than only recording, is deliberate: an attempt
    that merely got logged could still have completed, and a sovereignty claim
    built on a log line is worth less than the log line.
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._saved: dict[str, object] = {}

    def _deny(self, kind: str, detail: str) -> None:
        self.attempts.append(f"{kind}: {detail}")
        raise EgressDenied(
            f"outbound {kind} attempted: {detail}. Orion is air-gapped by design; "
            "this is a defect, not a configuration problem."
        )

    # -- patches -----------------------------------------------------------
    def _connect(self, sock, address):  # type: ignore[no-untyped-def]
        self._deny("socket.connect", repr(address))

    def _connect_ex(self, sock, address):  # type: ignore[no-untyped-def]
        self._deny("socket.connect_ex", repr(address))

    def _create_connection(self, address, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._deny("socket.create_connection", repr(address))

    def _getaddrinfo(self, host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        # DNS is itself an outbound query. A resolver lookup is the first half of
        # every exfiltration path, so it is refused before the socket exists.
        self._deny("dns.getaddrinfo", f"{host!r}:{port!r}")

    def _gethostbyname(self, host):  # type: ignore[no-untyped-def]
        self._deny("dns.gethostbyname", repr(host))

    def _gethostbyname_ex(self, host):  # type: ignore[no-untyped-def]
        self._deny("dns.gethostbyname_ex", repr(host))

    def _sendto(self, sock, data, *args):  # type: ignore[no-untyped-def]
        # UDP has no handshake, so a sendto can leave the host with nothing ever
        # looking like a connection. It is the quietest way out of an air gap.
        self._deny("socket.sendto", f"{len(data)} bytes")

    def _urlopen(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._deny("urllib.urlopen", repr(args[:1]))

    def __enter__(self) -> "EgressGuard":
        self._saved = {
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
            "gethostbyname": socket.gethostbyname,
            "gethostbyname_ex": socket.gethostbyname_ex,
            "sendto": socket.socket.sendto,
            "urlopen": None,
        }
        socket.socket.connect = self._connect          # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex    # type: ignore[method-assign]
        socket.socket.sendto = self._sendto            # type: ignore[method-assign]
        socket.create_connection = self._create_connection   # type: ignore[assignment]
        socket.getaddrinfo = self._getaddrinfo          # type: ignore[assignment]
        socket.gethostbyname = self._gethostbyname      # type: ignore[assignment]
        socket.gethostbyname_ex = self._gethostbyname_ex  # type: ignore[assignment]
        try:
            import urllib.request

            self._saved["urlopen"] = urllib.request.urlopen
            urllib.request.urlopen = self._urlopen       # type: ignore[assignment]
        except ImportError:  # pragma: no cover - stdlib always present
            pass
        return self

    def __exit__(self, *exc: object) -> None:
        socket.socket.connect = self._saved["connect"]        # type: ignore[method-assign]
        socket.socket.connect_ex = self._saved["connect_ex"]  # type: ignore[method-assign]
        socket.socket.sendto = self._saved["sendto"]          # type: ignore[method-assign]
        socket.create_connection = self._saved["create_connection"]  # type: ignore[assignment]
        socket.getaddrinfo = self._saved["getaddrinfo"]      # type: ignore[assignment]
        socket.gethostbyname = self._saved["gethostbyname"]  # type: ignore[assignment]
        socket.gethostbyname_ex = self._saved["gethostbyname_ex"]  # type: ignore[assignment]
        if self._saved.get("urlopen") is not None:
            import urllib.request

            urllib.request.urlopen = self._saved["urlopen"]  # type: ignore[assignment]


# ------------------------------------------------------------- static scan ----
def static_scan() -> list[tuple[str, int, str]]:
    """Import and egress-call sites across `app/`, reported with file and line.

    Deliberately an AST walk rather than a regex: a regex over source text cannot
    tell `import requests` from the word "requests" in a docstring, and a scanner
    that cries wolf gets switched off.
    """
    findings: list[tuple[str, int, str]] = []
    classified: list[tuple[str, int, str]] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover
            findings.append((str(path), exc.lineno or 0, f"unparseable: {exc}"))
            continue
        rel = path.relative_to(BACKEND)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in EGRESS_MODULES or alias.name in EGRESS_MODULES:
                        findings.append(
                            (str(rel), node.lineno, f"import {alias.name}")
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                if mod in EGRESS_MODULES or (node.module or "") in EGRESS_MODULES:
                    findings.append(
                        (str(rel), node.lineno, f"from {node.module} import ...")
                    )
            elif isinstance(node, ast.Attribute) and node.attr in EGRESS_ATTRIBUTES:
                # Every `.connect` / `.sendto` is collected, then the ones with a
                # demonstrably local base are classified out. Filtering on the
                # *variable's name* was the first attempt and it missed `s.connect`
                # entirely -- a name-based guess has no recall, and a scanner with
                # no recall reports "clean" while the hole is wide open. Reporting
                # the whole set and classifying the local ones keeps the recall at
                # 100% and leaves the judgement visible to whoever reads the list.
                base = ast.unparse(node.value)
                lowered = base.lower()
                if any(tok in lowered for tok in LOCAL_BASES):
                    classified.append((str(rel), node.lineno, f".{node.attr} on {base!r}"))
                    continue
                findings.append((str(rel), node.lineno, f".{node.attr} on {base!r}"))
            elif isinstance(node, ast.Name) and node.id in EGRESS_CALLS:
                findings.append((str(rel), node.lineno, f"call/name {node.id}"))
    static_scan.last_classified = classified  # type: ignore[attr-defined]
    return findings


# Counted separately so the report can say how many sites were examined and
# classified local, rather than implying nothing resembling a connect() exists.
static_scan.last_classified = []  # type: ignore[attr-defined]


# ------------------------------------------------------------- the pipeline ----
def run_pipeline() -> list[str]:
    """startup -> ingest -> indicators -> scoring -> export sanitisation.

    Each step is the real service, not a mock. A sovereignty check that exercises
    stand-ins proves the stand-ins are air-gapped.
    """
    import csv

    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.duckdb_client import get_duckdb
    from app.db.sqlite import get_connection, init_db
    from app.services.ingestion_service import get_ingestion_service
    from app.services.policy_profile import get_policy_service
    from app.services.rules_engine import get_rules_engine
    from app.services.scoring_service import get_scoring_service
    from app.services.upload_security import export_safe_cell

    steps: list[str] = []
    PS, PE = date(2026, 1, 1), date(2026, 1, 31)

    init_db()
    get_duckdb().init()
    steps.append("startup: sqlite + duckdb initialised")

    policy, _, _ = get_policy_service().activate("nccipc_default", actor="sovereignty")
    steps.append(f"policy: {policy.profile_id} v{policy.version} activated")

    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO sector_ref (sector_ref, display_name) VALUES ('BANK','Banking')")
    for i in range(9):
        conn.execute(
            "INSERT OR IGNORE INTO entity (entity_id, name, sector, soc_model, "
            "coverage_type, declared_open, declared_close, size_tier, created_at) "
            "VALUES (?,?,'BANK','in-house','24x7','08:00','20:00','large',datetime('now'))",
            (f"ent_p{i}", f"Peer {i}"),
        )
    conn.execute(
        "INSERT OR IGNORE INTO entity (entity_id, name, sector, soc_model, coverage_type, "
        "declared_open, declared_close, size_tier, created_at) "
        "VALUES ('ent_a','Alpha Bank','BANK','in-house','24x7','08:00','20:00','large',datetime('now'))"
    )
    conn.commit()

    svc = get_ingestion_service()

    def cases(n: int, rapid: bool):
        rows = []
        for i in range(n):
            day = 3 + (i % 24)
            sev = "critical" if i % 5 == 0 else "high"
            dur = (150 if i % 3 else 840) if rapid else (6 * 3600 + (i % 6) * 3600)
            note = "no" if rapid else "Escalated to L2 after full review of the alert evidence"
            rows.append([
                f"case_{i:04d}", f"2026-01-{day:02d} 09:00:00",
                f"2026-01-{day:02d} {9 + dur // 3600:02d}:{(dur % 3600) // 60:02d}:00",
                f"analyst{i % 3}", f"analyst{i % 3}", sev,
                "false_positive" if (rapid and i % 2) else "true_positive",
                "Cause identified", note, "human",
            ])
        return rows

    def alerts(n: int):
        return [
            [f"alt_{i:04d}", f"2026-01-{3 + i % 24:02d} 08:30:00", f"Rule {i % 5}",
             "high", f"rule_{i % 5}", f"10.0.1.{i % 200}", f"HOST-DC{i % 7:02d}",
             "TA0001", "T1059.001", "open", "false"]
            for i in range(n)
        ]

    def esc(rows):
        out = []
        for j, (case_id, _sev) in enumerate([(r[0], r[5]) for r in rows if r[5] == "critical"][:2]):
            out.append([f"esc_{j:04d}", case_id, "2026-01-05 10:00:00", "NCIIPC",
                        "critical_impact", "2026-01-05 12:00:00", "acknowledged"])
        return out

    def blob(header, rows) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue().encode()

    CASE_H = ["case_id", "opened_at", "closed_at", "assigned_to", "closed_by", "urgency",
              "disposition", "root_cause", "notes", "closed_by_type"]
    ALERT_H = ["_id", "timestamp", "search_name", "urgency", "search_id", "src", "dest",
               "mitre_tactic", "mitre_technique", "status", "auto_closed"]
    ESC_H = ["escalation_id", "case_id", "ts", "target", "reason_code",
             "acknowledged_ts", "outcome"]
    MAP = {
        "alert_record.csv": {"_id": "alert_id", "timestamp": "event_ts",
                             "search_name": "title", "urgency": "severity_raw",
                             "search_id": "rule_id", "src": "source_ip_pseudo",
                             "dest": "destination_asset_id",
                             "mitre_tactic": "mitre_tactic",
                             "mitre_technique": "mitre_technique_id",
                             "status": "status", "auto_closed": "auto_closed_flag"},
        "case_record.csv": {"case_id": "case_id", "opened_at": "created_at",
                            "closed_at": "closed_at", "assigned_to": "analyst_pseudo",
                            "closed_by": "closed_by_pseudo",
                            "closed_by_type": "actor_type", "urgency": "severity_norm",
                            "disposition": "disposition",
                            "root_cause": "root_cause_action", "notes": "note_ref"},
        "escalation.csv": {"escalation_id": "escalation_id", "case_id": "case_id",
                           "ts": "ts", "target": "target", "reason_code": "reason_code",
                           "acknowledged_ts": "acknowledged_ts", "outcome": "outcome"},
    }

    for eid in [f"ent_p{i}" for i in range(9)] + ["ent_a"]:
        rapid = eid == "ent_a"
        rows = cases(60, rapid)
        files = [("alert_record.csv", blob(ALERT_H, alerts(50))),
                 ("case_record.csv", blob(CASE_H, rows))]
        mapping = {k: v for k, v in MAP.items() if k != "escalation.csv"}
        if not rapid:
            files.append(("escalation.csv", blob(ESC_H, esc(rows))))
            mapping["escalation.csv"] = MAP["escalation.csv"]
        acc = svc.accept_upload(eid, files, PS, PE, actor="sovereignty")
        svc.approve_mapping(acc.submission_id, mapping, actor="supervisor.1")
        res = svc.run_job(acc.job_id)
        steps.append(f"ingest: {eid} loaded {res.n_loaded} rows")

    results = get_rules_engine().run_all("ent_a", PS, PE)
    fired = sum(1 for r in results.values() if r.value is not None)
    steps.append(f"indicators: {len(results)} evaluated, {fired} produced a value")

    scores = get_scoring_service().score_period(PS, PE, persist=True)
    steps.append(f"scoring: {len(scores)} entities scored and persisted")

    sample = export_safe_cell("=cmd|'/c calc'!A1")
    steps.append(f"export: sanitised a live formula cell -> {sample!r}")

    return steps


# ------------------------------------------------------------------- report ----
def main() -> int:
    print("=" * 72)
    print("ORION SOVEREIGNTY CHECK -- proof, not assertion")
    print("=" * 72)
    print(f"backend : {BACKEND}")
    print(f"scratch : {WORK}")
    print(f"python  : {sys.version.split()[0]}")
    print()

    failures: list[str] = []

    print("--- 1. static scan of app/ ------------------------------------------")
    findings = static_scan()
    if findings:
        for path, line, what in findings:
            print(f"  FINDING  {path}:{line}  {what}")
        failures.append(f"{len(findings)} egress-capable site(s) in app/")
    else:
        n_files = len(list((BACKEND / "app").rglob("*.py")))
        print(f"  clean: {n_files} modules, no egress import or call site")
    local_sites = getattr(static_scan, "last_classified", [])
    print(f"  {len(local_sites)} .connect/.sendto site(s) examined and classified "
          f"local (duckdb/sqlite handles open files, not sockets)")
    for path, line, what in local_sites:
        print(f"    local  {path}:{line}  {what}")
    print()

    print("--- 1b. the scanner is not vacuous ----------------------------------")
    # A scanner that reports "clean" is only worth what its recall is worth.
    # Plant the three shapes it is supposed to catch and confirm it catches them,
    # then delete the plant. A scanner that silently stopped matching would
    # otherwise turn this whole file into a rubber stamp.
    probe_dir = BACKEND / "app" / "_sovereignty_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    try:
        (probe_dir / "__init__.py").write_text("", encoding="utf-8")
        (probe_dir / "plant_import.py").write_text(
            "import requests\n", encoding="utf-8")
        (probe_dir / "plant_from.py").write_text(
            "from urllib.request import urlopen\n", encoding="utf-8")
        (probe_dir / "plant_attr.py").write_text(
            "def f(s):\n    s.connect(('10.0.0.1', 443))\n", encoding="utf-8")
        (probe_dir / "plant_local.py").write_text(
            "import duckdb\n\ndef f():\n    return duckdb.connect('x.db')\n",
            encoding="utf-8")
        (probe_dir / "plant_clean.py").write_text(
            "import json\nimport sqlite3\n", encoding="utf-8")
        caught = static_scan()
        local = getattr(static_scan, "last_classified", [])
        for expected in ("plant_import.py", "plant_from.py", "plant_attr.py"):
            hit = any(expected in str(p) for p, _l, _w in caught)
            print(f"  {'caught' if hit else 'MISSED'}  {expected}")
            if not hit:
                failures.append(f"static scanner missed a planted {expected}")
        if not any("plant_local.py" in str(p) for p, _l, _w in local):
            print("  MISSED  plant_local.py (duckdb.connect must classify as local)")
            failures.append("static scanner did not classify duckdb.connect as local")
        else:
            print("  local   plant_local.py (duckdb.connect classified local, not a finding)")
        if any("plant_clean.py" in str(p) for p, _l, _w in caught):
            failures.append("static scanner flagged an innocuous import")
        else:
            print("  clean   plant_clean.py (no false positive on json/sqlite3)")
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    print()

    print("--- 2. live pipeline with egress denied -----------------------------")
    guard = EgressGuard()
    steps: list[str] = []
    error: str | None = None
    try:
        with guard:
            steps = run_pipeline()
    except EgressDenied as exc:
        error = f"EGRESS ATTEMPTED: {exc}"
    except Exception as exc:  # noqa: BLE001
        error = f"pipeline failed: {type(exc).__name__}: {exc}"
        traceback.print_exc()

    for step in steps:
        print(f"  {step}")
    if error:
        print(f"  ERROR  {error}")
        failures.append(error)
    else:
        print(f"  {len(steps)} pipeline stages completed with egress denied")
    print()

    print("--- 3. outbound attempts --------------------------------------------")
    if guard.attempts:
        for attempt in guard.attempts:
            print(f"  ATTEMPT  {attempt}")
        failures.append(f"{len(guard.attempts)} outbound attempt(s)")
    else:
        print("  zero outbound connection attempts")
    print()

    print("--- 4. egress-capable modules left loaded ---------------------------")
    loaded = {
        name.split(".")[0]
        for name in sys.modules
        if name.split(".")[0] in EGRESS_MODULES
    }
    if loaded:
        for name in sorted(loaded):
            print(f"  LOADED  {name}")
        failures.append(f"egress-capable module(s) imported during the run: {sorted(loaded)}")
    else:
        print(f"  none of {len(EGRESS_MODULES)} known egress modules were imported")
    print()

    print("--- 5. the guard is not vacuous -------------------------------------")
    # A check that cannot fail proves nothing. Prove the guard actually denies.
    probe: list[str] = []
    try:
        with EgressGuard() as sanity:
            socket.create_connection(("example.invalid", 80), timeout=0.01)
    except EgressDenied:
        probe.append("denied socket.create_connection")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"guard did not deny cleanly: {type(exc).__name__}: {exc}")
    try:
        with EgressGuard() as sanity:
            socket.getaddrinfo("example.invalid", 80)
    except EgressDenied:
        probe.append("denied dns.getaddrinfo")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"guard did not deny DNS: {type(exc).__name__}: {exc}")
    try:
        with EgressGuard() as sanity:
            import urllib.request

            urllib.request.urlopen("http://example.invalid/", timeout=0.01)
    except EgressDenied:
        probe.append("denied urllib.request.urlopen")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"guard did not deny urlopen: {type(exc).__name__}: {exc}")
    if len(probe) == 3:
        for item in probe:
            print(f"  {item} (as expected)")
        print("  the guard denies real calls, so the zero above is meaningful")
    print()

    shutil.rmtree(WORK, ignore_errors=True)

    print("=" * 72)
    if failures:
        print(f"SOVEREIGNTY CHECK FAILED ({len(failures)} finding(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SOVEREIGNTY CHECK PASSED -- zero outbound connections, no egress imports")
    print("Scope: the pipeline paths above plus an AST scan of app/.")
    print("Not covered: any code path not exercised here, and the OS-level")
    print("firewall. This is evidence for the air-gap design, not a substitute")
    print("for network-segmentation policy at the deployment site.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
