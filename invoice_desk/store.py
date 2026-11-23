"""SQLite persistence: run history, the invoice ledger, the receipt chain, human reviews
and what was learned from them.

One short-lived connection per operation (safe with Streamlit's threads and with Windows
file locking). `reset()` clears tables instead of deleting the file for the same reason.

Writes that belong together go inside `with store.transaction():`. Every store method
called in that block uses one connection, and the block commits as a whole or not at
all. Posting a decision (ledger entry, receipt, run record) and recording a review are
done that way, so the ledger and the decision can never disagree.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import sqlite3
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config
from .models import RunResult
from .textutil import invoice_no_key, to_decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, seq INTEGER UNIQUE, created_at TEXT, file_name TEXT, file_sha256 TEXT,
    source TEXT, verdict TEXT, reason_codes TEXT, vendor_id TEXT, vendor_name TEXT, invoice_no TEXT,
    invoice_date TEXT, total TEXT, po_number TEXT, duration_ms REAL, scanned INTEGER DEFAULT 0,
    review_status TEXT DEFAULT '', result_json TEXT, resubmission_of TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ledger (
    ledger_id TEXT PRIMARY KEY, run_id TEXT, origin TEXT, vendor_id TEXT, invoice_no TEXT, invoice_key TEXT,
    invoice_date TEXT, po_number TEXT, taxable TEXT, tax TEXT, total TEXT, status TEXT, file_sha256 TEXT,
    created_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS ledger_lines (
    ledger_id TEXT, po_line_no INTEGER, description TEXT, qty TEXT, unit_price TEXT, amount TEXT);
CREATE TABLE IF NOT EXISTS receipts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, run_id TEXT, created_at TEXT, hash TEXT,
    prev_hash TEXT, body_json TEXT, signature TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, decided_at TEXT, reviewer TEXT, decision TEXT,
    note TEXT, checks_json TEXT);
CREATE TABLE IF NOT EXISTS vendor_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, kind TEXT, value TEXT, extra TEXT, source_run TEXT,
    created_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
TABLES = ["runs", "ledger", "ledger_lines", "receipts", "reviews", "vendor_overrides", "meta"]
# columns added after the first release: a database made by an older version is upgraded in place
MIGRATIONS = [("runs", "resubmission_of", "TEXT DEFAULT ''"), ("receipts", "signature", "TEXT DEFAULT ''")]
GENESIS = "GENESIS"


def canonical(body: dict) -> str:
    """One fixed text form of a JSON object, so the same content always hashes the same."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def receipt_hash(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def signing_enabled() -> bool:
    return bool(config.env("RECEIPT_SIGNING_KEY"))


def sign(digest: str) -> str:
    """HMAC of a receipt hash with a key that lives outside the database, or '' with no key."""
    key = config.env("RECEIPT_SIGNING_KEY")
    return hmac.new(key.encode("utf-8"), digest.encode("ascii"), hashlib.sha256).hexdigest() if key else ""


class Store:
    def __init__(self, path: Optional[Path | str] = None, data_dir: Optional[Path] = None):
        self.path = str(path or config.db_path())
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self._local = threading.local()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            for table, column, kind in MIGRATIONS:
                if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        if self.meta("seeded") is None:
            self.seed_history()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._local, "conn", None)
        if active is not None:              # inside store.transaction(): join it, commit nothing yet
            yield active
            return
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All store calls inside the block commit together, or none of them do."""
        if getattr(self._local, "conn", None) is not None:      # already inside one: the outer block decides
            yield
            return
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._local.conn = conn
            yield
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            self._local.conn = None
            conn.close()

    @staticmethod
    def _begin(conn: sqlite3.Connection) -> None:
        """Take the write lock now (so two writers cannot both read the same 'latest' row),
        unless a transaction is already open on this connection."""
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

    # ---------------------------------------------------------------- meta, seed, reset

    def meta(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def seed_history(self) -> int:
        """Load invoices that were posted before this tool existed (data/invoice_history*.csv)."""
        head, lines = self.data_dir / "invoice_history.csv", self.data_dir / "invoice_history_lines.csv"
        count = 0
        if head.is_file():
            with open(head, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            line_rows: list[dict] = []
            if lines.is_file():
                with open(lines, newline="", encoding="utf-8-sig") as f:
                    line_rows = list(csv.DictReader(f))
            with self._conn() as conn:
                for r in rows:
                    conn.execute(
                        "INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["ledger_id"], None, "history", r["vendor_id"], r["invoice_no"],
                         invoice_no_key(r["invoice_no"]), r["invoice_date"], r["po_number"], r["taxable_amount"],
                         r["tax_amount"], r["total_amount"], r.get("status") or "POSTED", "",
                         r["invoice_date"] + "T00:00:00+00:00", "posted before this tool was introduced"))
                    count += 1
                for r in line_rows:
                    conn.execute("INSERT INTO ledger_lines VALUES (?,?,?,?,?,?)",
                                 (r["ledger_id"], int(r["po_line_no"]), r["description"], r.get("qty") or "",
                                  r.get("unit_price") or "", r["amount"]))
        self.set_meta("seeded", config.now_utc().isoformat())
        return count

    def reset(self) -> None:
        """Back to the seeded state: no runs, no reviews, history ledger only."""
        with self._conn() as conn:
            for table in TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('receipts', 'reviews', 'vendor_overrides')")
        self.seed_history()

    # ---------------------------------------------------------------- files

    @property
    def files_dir(self) -> Path:
        folder = Path(self.path).parent / "files"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_file(self, sha256: str, content: bytes) -> None:
        path = self.files_dir / f"{sha256}.pdf"
        if not path.exists():
            path.write_bytes(content)

    def load_file(self, sha256: str) -> Optional[bytes]:
        path = self.files_dir / f"{sha256}.pdf"
        return path.read_bytes() if path.is_file() else None

    def save_attachment(self, content: bytes) -> str:
        """Keep a supporting document (proof of a bank change, say) and return its fingerprint."""
        sha256 = hashlib.sha256(content).hexdigest()
        path = self.files_dir / f"att_{sha256}"
        if not path.exists():
            path.write_bytes(content)
        return sha256

    def load_attachment(self, sha256: str) -> Optional[bytes]:
        path = self.files_dir / f"att_{sha256}"
        return path.read_bytes() if path.is_file() else None

    # ---------------------------------------------------------------- runs

    def allocate_run_id(self, year: Optional[int] = None) -> str:
        """Reserve the next AP reference (AP-2026-0001, AP-2026-0002, ...) in one transaction.
        The year is the business year; the running number never restarts, so it is unique on its own."""
        year = year or config.today().year
        with self._conn() as conn:
            self._begin(conn)
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM runs").fetchone()["n"]
            run_id = f"AP-{year}-{seq:04d}"
            conn.execute("INSERT INTO runs (run_id, seq, created_at) VALUES (?, ?, ?)",
                         (run_id, seq, config.now_utc().isoformat()))
        return run_id

    def save_run(self, result: RunResult) -> None:
        inv = result.invoice
        decision = result.decision
        codes = [r.code for r in decision.reasons] if decision else []
        vendor = result.vendor or {}
        total = (inv.get("total") if inv else "") or ""
        currency = (inv.get("currency") if inv else None) or "INR"
        if total and currency != "INR":
            total = f"{currency} {total}"       # never mixed into rupee totals on the dashboard
        with self._conn() as conn:
            conn.execute(
                """UPDATE runs SET created_at=?, file_name=?, file_sha256=?, source=?, verdict=?, reason_codes=?,
                   vendor_id=?, vendor_name=?, invoice_no=?, invoice_date=?, total=?, po_number=?, duration_ms=?,
                   scanned=?, review_status=?, result_json=?, resubmission_of=? WHERE run_id=?""",
                (result.created_at, result.file_name, result.file_sha256, result.source,
                 decision.verdict if decision else None, ",".join(codes), vendor.get("vendor_id") or "",
                 vendor.get("name") or (inv.get("vendor_name") if inv else "") or "",
                 (inv.get("invoice_number") if inv else "") or "", (inv.get("invoice_date") if inv else "") or "",
                 total, (result.po or {}).get("po_number") or "",
                 result.duration_ms, 1 if any(p.get("kind", "").startswith("scan") for p in result.pages) else 0,
                 "PENDING" if decision and decision.verdict == "HOLD" and not result.resubmission_of else "",
                 result.model_dump_json(), result.resubmission_of or "", result.run_id))

    def list_runs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT run_id, seq, created_at, file_name, file_sha256, source, verdict, reason_codes, vendor_id,
                          vendor_name, invoice_no, invoice_date, total, po_number, duration_ms, scanned, review_status,
                          resubmission_of
                   FROM runs WHERE verdict IS NOT NULL ORDER BY seq DESC""").fetchall()
        return [dict(r) for r in rows]

    def original_run_for_file(self, sha256: str) -> Optional[RunResult]:
        """The latest run that actually read this exact file (re-submissions point at it)."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT result_json FROM runs WHERE file_sha256 = ? AND verdict IS NOT NULL
                   AND COALESCE(resubmission_of, '') = '' ORDER BY seq DESC LIMIT 1""", (sha256,)).fetchone()
        return RunResult.model_validate_json(row["result_json"]) if row and row["result_json"] else None

    def get_run(self, run_id: str) -> Optional[RunResult]:
        with self._conn() as conn:
            row = conn.execute("SELECT result_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row or not row["result_json"]:
            return None
        return RunResult.model_validate_json(row["result_json"])

    def run_row(self, run_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_review_status(self, run_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE runs SET review_status = ? WHERE run_id = ?", (status, run_id))

    # ---------------------------------------------------------------- ledger

    def add_ledger(self, entry: dict, lines: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry["ledger_id"], entry.get("run_id"), entry.get("origin", "run"), entry.get("vendor_id") or "",
                 entry.get("invoice_no") or "", invoice_no_key(entry.get("invoice_no")), entry.get("invoice_date") or "",
                 entry.get("po_number") or "", str(entry.get("taxable") or ""), str(entry.get("tax") or ""),
                 str(entry.get("total") or ""), entry["status"], entry.get("file_sha256") or "",
                 entry.get("created_at") or config.now_utc().isoformat(), entry.get("note") or ""))
            conn.execute("DELETE FROM ledger_lines WHERE ledger_id = ?", (entry["ledger_id"],))
            for ln in lines:
                conn.execute("INSERT INTO ledger_lines VALUES (?,?,?,?,?,?)",
                             (entry["ledger_id"], ln.get("po_line_no"), ln.get("description") or "",
                              str(ln.get("qty") if ln.get("qty") is not None else ""),
                              str(ln.get("unit_price") if ln.get("unit_price") is not None else ""),
                              str(ln.get("amount") if ln.get("amount") is not None else "")))

    def _entry(self, row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
        entry = dict(row)
        for key in ("taxable", "tax", "total"):
            entry[key] = to_decimal(entry[key])
        lines = conn.execute("SELECT * FROM ledger_lines WHERE ledger_id = ? ORDER BY rowid", (row["ledger_id"],)).fetchall()
        entry["lines"] = [{"po_line_no": ln["po_line_no"], "description": ln["description"],
                           "qty": to_decimal(ln["qty"]), "unit_price": to_decimal(ln["unit_price"]),
                           "amount": to_decimal(ln["amount"])} for ln in lines]
        return entry

    def ledger_entries(self, vendor_id: Optional[str] = None, po_number: Optional[str] = None,
                       statuses: Optional[tuple[str, ...]] = None) -> list[dict]:
        sql, args = "SELECT * FROM ledger WHERE 1=1", []
        if vendor_id:
            sql, args = sql + " AND vendor_id = ?", args + [vendor_id]
        if po_number:
            sql, args = sql + " AND po_number = ?", args + [po_number]
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY invoice_date, created_at, ledger_id", args).fetchall()
            return [self._entry(r, conn) for r in rows]

    def get_ledger(self, ledger_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM ledger WHERE ledger_id = ?", (ledger_id,)).fetchone()
            return self._entry(row, conn) if row else None

    def set_ledger_status(self, ledger_id: str, status: str, note: str = "") -> None:
        with self._conn() as conn:
            conn.execute("UPDATE ledger SET status = ?, note = CASE WHEN ? = '' THEN note ELSE ? END WHERE ledger_id = ?",
                         (status, note, note, ledger_id))

    def set_ledger_invoice_no(self, ledger_id: str, invoice_no: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE ledger SET invoice_no = ?, invoice_key = ? WHERE ledger_id = ?",
                         (invoice_no, invoice_no_key(invoice_no), ledger_id))

    def find_by_file(self, sha256: str) -> Optional[dict]:
        entries = [e for e in self.ledger_entries(statuses=("POSTED", "PENDING")) if e["file_sha256"] == sha256]
        return entries[0] if entries else None

    def po_usage(self, po_number: str) -> dict:
        """What has been billed against a PO: posted and pending, per line and in total,
        with the invoices behind it in date order (the running total shown to reviewers)."""
        usage: dict[str, Any] = {"posted": {}, "pending": {}, "posted_amount": Decimal("0"),
                                 "pending_amount": Decimal("0"), "invoices": []}
        for entry in self.ledger_entries(po_number=po_number, statuses=("POSTED", "PENDING")):
            bucket = "posted" if entry["status"] == "POSTED" else "pending"
            usage[f"{bucket}_amount"] += entry["taxable"] or Decimal("0")
            for ln in entry["lines"]:
                slot = usage[bucket].setdefault(ln["po_line_no"], {"qty": Decimal("0"), "amount": Decimal("0")})
                slot["qty"] += ln["qty"] or Decimal("0")
                slot["amount"] += ln["amount"] or Decimal("0")
            usage["invoices"].append(entry)
        return usage

    # ---------------------------------------------------------------- receipts

    def append_receipt(self, kind: str, run_id: str, body: dict) -> dict:
        """Add a receipt to the chain. Its hash covers its content and the previous hash."""
        with self._conn() as conn:
            self._begin(conn)
            last = conn.execute("SELECT hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
            body = dict(body)
            body["kind"] = kind
            body["run_id"] = run_id
            body["prev_hash"] = last["hash"] if last else GENESIS
            body.setdefault("created_at", config.now_utc().isoformat())
            digest = receipt_hash(body)
            signature = sign(digest)
            conn.execute("INSERT INTO receipts (kind, run_id, created_at, hash, prev_hash, body_json, signature) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (kind, run_id, body["created_at"], digest, body["prev_hash"], canonical(body), signature))
        return {**body, "hash": digest}

    def receipts(self, run_id: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT * FROM receipts", []
        if run_id:
            sql, args = sql + " WHERE run_id = ?", [run_id]
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [{"seq": r["seq"], "kind": r["kind"], "run_id": r["run_id"], "created_at": r["created_at"],
                 "hash": r["hash"], "prev_hash": r["prev_hash"], "body": json.loads(r["body_json"]),
                 "signature": r["signature"] or ""} for r in rows]

    def verify_chain(self) -> dict:
        """Recompute every receipt hash and check each link. Any edit to a stored receipt,
        or any removed receipt, shows up as a problem. When a signing key is configured
        (RECEIPT_SIGNING_KEY, kept outside the database) each signature is checked too,
        which also catches a rewrite of the whole chain by someone without the key."""
        problems: list[str] = []
        previous = GENESIS
        items = self.receipts()
        key_set = signing_enabled()
        for r in items:
            if receipt_hash(r["body"]) != r["hash"]:
                problems.append(f"receipt {r['seq']} ({r['run_id']}): content does not match its hash")
            if r["body"].get("prev_hash") != previous or r["prev_hash"] != previous:
                problems.append(f"receipt {r['seq']} ({r['run_id']}): does not link to the receipt before it")
            if key_set and r["signature"] and not hmac.compare_digest(r["signature"], sign(r["hash"])):
                problems.append(f"receipt {r['seq']} ({r['run_id']}): signature does not match the signing key")
            if key_set and not r["signature"]:
                problems.append(f"receipt {r['seq']} ({r['run_id']}): not signed, although a signing key is set")
            previous = r["hash"]
        return {"ok": not problems, "count": len(items), "problems": problems}

    def chain_head(self) -> str:
        """The latest receipt hash. Noted down outside the database, it pins the whole history."""
        items = self.receipts()
        return items[-1]["hash"] if items else GENESIS

    # ---------------------------------------------------------------- reviews and learning

    def add_review(self, run_id: str, decision: str, reviewer: str, note: str, checks: dict) -> None:
        with self._conn() as conn:
            conn.execute("INSERT INTO reviews (run_id, decided_at, reviewer, decision, note, checks_json) VALUES (?,?,?,?,?,?)",
                         (run_id, config.now_utc().isoformat(), reviewer, decision, note, json.dumps(checks)))

    def reviews(self, run_id: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT * FROM reviews", []
        if run_id:
            sql, args = sql + " WHERE run_id = ?", [run_id]
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY review_id", args).fetchall()]

    def replace_run_record(self, result: RunResult) -> None:
        """Store a changed run record (a refreshed hold) without touching its review status."""
        decision = result.decision
        codes = [r.code for r in decision.reasons] if decision else []
        with self._conn() as conn:
            conn.execute("UPDATE runs SET verdict = ?, reason_codes = ?, result_json = ? WHERE run_id = ?",
                         (decision.verdict if decision else None, ",".join(codes), result.model_dump_json(), result.run_id))

    def add_override(self, vendor_id: str, kind: str, value: str, extra: str = "", source_run: str = "") -> None:
        with self._conn() as conn:
            conn.execute("INSERT INTO vendor_overrides (vendor_id, kind, value, extra, source_run, created_at) VALUES (?,?,?,?,?,?)",
                         (vendor_id, kind, value, extra, source_run, config.now_utc().isoformat()))

    def overrides(self, vendor_id: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT * FROM vendor_overrides WHERE 1=1", []
        if vendor_id:
            sql, args = sql + " AND vendor_id = ?", args + [vendor_id]
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]
