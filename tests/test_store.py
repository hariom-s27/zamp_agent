"""Run history, the ledger and the hash-chained receipts."""
import sqlite3
from decimal import Decimal

from invoice_desk.store import GENESIS, Store, canonical, receipt_hash

from .test_po_match import CHAIR, post


def test_a_new_store_is_seeded_with_the_posted_history(store):
    entries = store.ledger_entries()
    assert len(entries) == 10 and {e["origin"] for e in entries} == {"history"}
    usage = store.po_usage("PO-2026-0193")
    assert usage["posted"][1] == {"qty": Decimal("16"), "amount": Decimal("400000.00")}
    assert usage["posted_amount"] == Decimal("400000.00") and [e["invoice_no"] for e in usage["invoices"]] == ["ASS/2026/0871", "ASS/2026/0902"]


def test_seeding_happens_once(tmp_path):
    Store(tmp_path / "a.db")
    again = Store(tmp_path / "a.db")
    assert len(again.ledger_entries()) == 10


def test_ap_references_are_sequential_and_carry_the_business_year(store):
    assert [store.allocate_run_id(2026) for _ in range(3)] == ["AP-2026-0001", "AP-2026-0002", "AP-2026-0003"]
    assert store.allocate_run_id(2027) == "AP-2027-0004"          # the running number never restarts, so it is unique on its own


def test_po_usage_separates_posted_from_pending(store):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="A/1")
    post(store, "T2", "V001", "PO-2026-0147", [(1, CHAIR, 5, "22500.00")], number="A/2", status="PENDING")
    post(store, "T3", "V001", "PO-2026-0147", [(1, CHAIR, 7, "31500.00")], number="A/3", status="REJECTED")
    usage = store.po_usage("PO-2026-0147")
    assert usage["posted"][1]["qty"] == 10 and usage["pending"][1]["qty"] == 5
    assert usage["posted_amount"] == Decimal("45000.00") and usage["pending_amount"] == Decimal("22500.00")
    assert len(usage["invoices"]) == 2


def test_find_by_file_ignores_rejected_entries(store):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 1, "4500.00")], sha="a" * 64, status="REJECTED")
    assert store.find_by_file("a" * 64) is None
    post(store, "T2", "V001", "PO-2026-0147", [(1, CHAIR, 1, "4500.00")], sha="b" * 64)
    assert store.find_by_file("b" * 64)["ledger_id"] == "T2"


def test_canonical_form_ignores_key_order():
    assert canonical({"b": 1, "a": [1, 2]}) == canonical({"a": [1, 2], "b": 1})
    assert receipt_hash({"x": Decimal("1.50")}) == receipt_hash({"x": "1.50"})


def test_receipts_form_a_chain(store):
    a = store.append_receipt("decision", "AP-2026-0001", {"verdict": "APPROVE"})
    b = store.append_receipt("decision", "AP-2026-0002", {"verdict": "HOLD"})
    c = store.append_receipt("review", "AP-2026-0002", {"decision": "APPROVE"})
    assert a["prev_hash"] == GENESIS and b["prev_hash"] == a["hash"] and c["prev_hash"] == b["hash"]
    assert store.verify_chain() == {"ok": True, "count": 3, "problems": []}
    assert [r["kind"] for r in store.receipts("AP-2026-0002")] == ["decision", "review"]


def _sql(store, statement, *args):
    conn = sqlite3.connect(store.path)
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def test_editing_a_stored_receipt_is_detected(store):
    for n in range(3):
        store.append_receipt("decision", f"AP-2026-000{n + 1}", {"verdict": "HOLD", "total": "100.00"})
    _sql(store, "UPDATE receipts SET body_json = REPLACE(body_json, 'HOLD', 'APPROVE') WHERE seq = 2")
    check = store.verify_chain()
    assert not check["ok"] and "receipt 2" in check["problems"][0] and "content does not match its hash" in check["problems"][0]


def test_rewriting_a_receipt_together_with_its_hash_breaks_the_next_link(store):
    for n in range(3):
        store.append_receipt("decision", f"AP-2026-000{n + 1}", {"verdict": "HOLD"})
    forged = dict(store.receipts()[1]["body"], verdict="APPROVE")
    _sql(store, "UPDATE receipts SET body_json = ?, hash = ? WHERE seq = 2", canonical(forged), receipt_hash(forged))
    check = store.verify_chain()
    assert not check["ok"] and any("receipt 3" in p and "does not link" in p for p in check["problems"])


def test_deleting_a_receipt_is_detected(store):
    for n in range(3):
        store.append_receipt("decision", f"AP-2026-000{n + 1}", {"verdict": "HOLD"})
    _sql(store, "DELETE FROM receipts WHERE seq = 2")
    assert not store.verify_chain()["ok"]


def test_reset_returns_to_the_seeded_state(store):
    store.allocate_run_id()
    store.append_receipt("decision", "AP-2026-0001", {"verdict": "HOLD"})
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 1, "4500.00")])
    store.add_override("V001", "bank_account", "123")
    store.add_review("AP-2026-0001", "APPROVE", "A. Reviewer", "", {})
    store.reset()
    assert store.receipts() == [] and store.overrides() == [] and store.reviews() == [] and store.list_runs() == []
    assert len(store.ledger_entries()) == 10 and store.allocate_run_id(2026) == "AP-2026-0001"
    assert store.append_receipt("decision", "AP-2026-0001", {})["prev_hash"] == GENESIS


def test_files_are_stored_by_fingerprint(store):
    store.save_file("abc", b"%PDF-1.4 one")
    store.save_file("abc", b"something else")          # same fingerprint: the first copy is kept
    assert store.load_file("abc") == b"%PDF-1.4 one" and store.load_file("missing") is None


def test_invoice_number_can_be_recorded_later(store):
    post(store, "T1", "V003", "PO-2026-0212", [(1, "Stretch wrap", 1, "640.00")], number="")
    store.set_ledger_invoice_no("T1", "MPP/26-27/0388")
    assert store.get_ledger("T1")["invoice_key"] == "MPP2627388"
