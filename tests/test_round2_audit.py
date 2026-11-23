"""Round 2. One or more tests for every finding in docs/AUDIT_REPORT.md. Each test was
written first, run against the round 1 code to see it fail, and only then was the code
changed. The audit's numbering is kept in the docstrings."""
import pytest

from invoice_desk import config
from invoice_desk.llm import LlmAssist
from invoice_desk.pipeline import Pipeline
from invoice_desk.review import ReviewError, apply_review, po_position, review_state
from invoice_desk.store import Store
from scripts import generate_test_data as G

from .conftest import LLM_OFF, TODAY, sample_bytes
from .test_po_match import post

GAS = "Portable Multi-Gas Detector, 4 Gas"


def reasons(result) -> set[str]:
    return {f.code for f in result.decision.reasons}


def gas_invoice(number: str, qty: int, day: int) -> bytes:
    """An Aravalli Shield invoice for gas detectors that quotes no PO, so the PO is
    inferred and the invoice is held for a person to confirm (PO-2026-0193: 20 ordered,
    16 already billed, 4 left)."""
    return G.render_invoice_pdf(G.spec(
        id="x", layout="grid", vendor="V004", invoice_no=number, invoice_date=G.date(2026, 11, day), po_ref=None,
        lines=[G.line(GAS, "9027", qty, "Nos", "25000.00")]))


# ---------------------------------------------------------------- bug 1: approval never re-checked the PO

def test_two_approvals_from_the_review_queue_can_never_overbill_a_po(pipeline, store):
    """Audit bug 1, as reproduced there: two held invoices of 4 units each against a PO with
    4 units left. Each fits on its own, so each was held only for the inferred PO."""
    first = pipeline.run(gas_invoice("ASS/2026/0951", 4, 1), "a.pdf", "test")
    second = pipeline.run(gas_invoice("ASS/2026/0977", 4, 18), "b.pdf", "test")
    assert first.verdict == second.verdict == "HOLD"
    assert reasons(first) == reasons(second) == {"PO_INFERRED"}

    apply_review(store, first.run_id, "APPROVE", "P. Deshmukh", "Belongs to PO-2026-0193")
    with pytest.raises(ReviewError, match="24 of 20"):
        apply_review(store, second.run_id, "APPROVE", "P. Deshmukh", "Belongs to PO-2026-0193")

    usage = store.po_usage("PO-2026-0193")
    assert usage["posted"][1]["qty"] == 20 and usage["posted_amount"] == 500000      # at the PO, never above it
    assert store.get_ledger(second.ledger_id)["status"] == "PENDING"
    assert store.run_row(second.run_id)["review_status"] == "PENDING"


def test_a_refused_approval_shows_why_and_leaves_a_fresh_hold_on_the_record(pipeline, store):
    first = pipeline.run(gas_invoice("ASS/2026/0951", 4, 1), "a.pdf", "test")
    second = pipeline.run(gas_invoice("ASS/2026/0977", 3, 18), "b.pdf", "test")
    apply_review(store, first.run_id, "APPROVE", "P. Deshmukh")
    with pytest.raises(ReviewError) as refused:
        apply_review(store, second.run_id, "APPROVE", "P. Deshmukh")
    message = str(refused.value)
    assert "23 of 20" in message and "PO-2026-0193" in message and "₹5,75,000.00" in message   # the numbers, in words a reviewer can act on
    assert "amend" in message.lower() and "reject" in message.lower()                               # and what to do next

    refreshed = store.get_run(second.run_id)                       # the hold on record now says why it cannot be approved
    assert {"QTY_OVER_PO", "OVER_PO_BALANCE"} <= {f.code for f in refreshed.decision.reasons}
    assert set(store.run_row(second.run_id)["reason_codes"].split(",")) >= {"QTY_OVER_PO", "OVER_PO_BALANCE"}
    kinds = [r["kind"] for r in store.receipts(second.run_id)]
    assert kinds == ["decision", "review_refused"] and store.verify_chain()["ok"]                   # the attempt is in the audit trail
    refusal = store.receipts(second.run_id)[-1]["body"]
    assert refusal["reviewer"] == "P. Deshmukh" and refusal["actor"] == "human"


def test_an_invoice_held_for_overbilling_cannot_be_approved_past_the_po(pipeline, store):
    """E1 itself: the reviewer cannot wave through what the process held for the PO."""
    run = pipeline.run(sample_bytes("04_E1_split_po_overbilling"), "04.pdf", "test")
    with pytest.raises(ReviewError, match="21 of 20"):
        apply_review(store, run.run_id, "APPROVE", "P. Deshmukh", "Vendor says five were delivered")
    assert store.po_usage("PO-2026-0193")["posted_amount"] == 400000
    apply_review(store, run.run_id, "REJECT", "P. Deshmukh", "Vendor to re-invoice four units")     # rejecting stays possible
    assert store.get_ledger(run.ledger_id)["status"] == "REJECTED"


def test_when_the_po_is_amended_the_same_approval_goes_through(pipeline, store, master, rules):
    """The way out of the hold is a PO amendment, which the re-check then sees."""
    run = pipeline.run(sample_bytes("04_E1_split_po_overbilling"), "04.pdf", "test")
    amended = master.model_copy(deep=True)
    line = amended.pos["PO-2026-0193"].lines[0]
    line.qty_ordered, line.line_amount = line.qty_ordered + 1, line.line_amount + line.unit_price
    apply_review(store, run.run_id, "APPROVE", "P. Deshmukh", "PO amended to 21 units", master=amended, rules=rules)
    assert store.get_ledger(run.ledger_id)["status"] == "POSTED"
    assert store.po_usage("PO-2026-0193")["posted"][1]["qty"] == 21


def test_po_position_counts_only_what_is_posted_and_uses_the_po_tolerances(store, master, rules):
    post(store, "T1", "V003", "PO-2026-0212", [(1, "Stretch wrap film roll 23 micron", 100, "64000.00")], number="MPP/1")
    post(store, "T2", "V003", "PO-2026-0212", [(1, "Stretch wrap film roll 23 micron", 20, "12800.00")], number="MPP/2", status="PENDING")
    post(store, "T3", "V003", "PO-2026-0212", [(1, "Stretch wrap film roll 23 micron", 21, "13440.00")], number="MPP/3", status="PENDING")
    fits = po_position(store, master, rules, store.get_ledger("T2"))
    over = po_position(store, master, rules, store.get_ledger("T3"))
    assert fits["problems"] == [] and fits["po_number"] == "PO-2026-0212"
    assert [p["code"] for p in over["problems"]] == ["QTY_OVER_PO"] and "121 of 120" in over["problems"][0]["text"]
    assert po_position(store, master, rules, {"po_number": "", "lines": []})["problems"] == []     # no PO: nothing to re-check


# ---------------------------------------------------------------- bug 2 and bug 4: posting was not atomic

def _fail_first_call(monkeypatch, target, name):
    real, calls = getattr(target, name), {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(f"{name} failed (simulated)")
        return real(*args, **kwargs)
    monkeypatch.setattr(target, name, flaky)
    return calls


@pytest.mark.parametrize("step", ["append_receipt", "save_run", "save_file"])
def test_a_failure_after_the_ledger_write_rolls_the_posting_back(step, pipeline, store, monkeypatch):
    """Audit bug 2 (receipt, file) and bug 4 (run record). Before the fix the ledger kept a
    POSTED entry under a decision that said HOLD, or the run vanished from the dashboard."""
    calls = _fail_first_call(monkeypatch, store, step)
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert calls["n"] >= 1
    assert r.verdict == "HOLD" and reasons(r) == {"POSTING_FAILED"}
    assert store.po_usage("PO-2026-0147")["posted_amount"] == 0                  # the PO balance was not used
    posted = [e for e in store.ledger_entries(statuses=("POSTED",)) if e["origin"] == "run"]
    assert posted == []
    stored = store.get_run(r.run_id)
    assert stored is not None and stored.verdict == "HOLD"                       # the dashboard and the ledger agree
    assert store.run_row(r.run_id)["review_status"] == "PENDING"
    assert r.receipt and r.receipt["verdict"] == "HOLD" and store.verify_chain()["ok"]


def test_when_nothing_can_be_written_the_run_still_ends_in_a_hold_and_nothing_is_posted(pipeline, store, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("database is read-only (simulated)")
    monkeypatch.setattr(store, "append_receipt", broken)
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert r.verdict == "HOLD" and "POSTING_FAILED" in reasons(r) and len(r.stages) == 9
    assert [e for e in store.ledger_entries(statuses=("POSTED",)) if e["origin"] == "run"] == []
    assert any("could not be recorded" in f.summary for f in r.decision.reasons)   # and the person is told, not left guessing


def test_a_failed_review_changes_nothing(pipeline, store, monkeypatch):
    """A review is several writes (ledger, status, review row, receipt). They commit together or not at all."""
    run = pipeline.run(sample_bytes("06_E3_scan_no_number_implied_po"), "06.pdf", "test")

    def broken(*args, **kwargs):
        raise OSError("receipt store unavailable (simulated)")
    monkeypatch.setattr(store, "append_receipt", broken)
    with pytest.raises(OSError):
        apply_review(store, run.run_id, "APPROVE", "A. Kulkarni", "Number from vendor", {"invoice_number": "MPP/26-27/0388"})
    monkeypatch.undo()
    entry = store.get_ledger(run.ledger_id)
    assert entry["status"] == "PENDING" and not entry["invoice_no"]              # the number was not half-written
    assert store.run_row(run.run_id)["review_status"] == "PENDING" and store.reviews(run.run_id) == []


# ---------------------------------------------------------------- bug 3: one person could approve a bank change

def test_a_bank_change_needs_two_different_people(pipeline, store):
    """Audit bug 3. The hold's own question said a second person must approve; nothing enforced it."""
    run = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "09.pdf", "test")
    first = apply_review(store, run.run_id, "APPROVE", "N. Rao", "Called 080-5550-0118, spoke to Ms Hegde, accounts head, 21 Nov 15:10",
                         {"bank_verified": True})
    assert first["decision"] == "FIRST_APPROVAL" and first["awaiting"] == "second approver"
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"                 # nothing payable after one approval
    assert store.run_row(run.run_id)["review_status"] == "PENDING"
    assert store.overrides("V001", "bank_account") == []                          # and the new account is not trusted yet
    assert review_state(store, run.run_id)["first_approval"]["reviewer"] == "N. Rao"

    for same_person in ("N. Rao", "n rao", "  N.RAO "):
        with pytest.raises(ReviewError, match="different person"):
            apply_review(store, run.run_id, "APPROVE", same_person, "", {"bank_second_check": True})
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"

    final = apply_review(store, run.run_id, "APPROVE", "S. Menon", "Call record checked", {"bank_second_check": True})
    assert final["decision"] == "APPROVE" and final["approvers"] == ["N. Rao", "S. Menon"]
    assert store.get_ledger(run.ledger_id)["status"] == "POSTED" and store.run_row(run.run_id)["review_status"] == "APPROVED"
    assert store.overrides("V001", "bank_account")[0]["value"] == "770031245566"
    assert [r["kind"] for r in store.receipts(run.run_id)] == ["decision", "review", "review"] and store.verify_chain()["ok"]


def test_the_first_approver_must_record_the_call_and_the_second_must_confirm_it(pipeline, store):
    run = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "09.pdf", "test")
    with pytest.raises(ReviewError, match="call-back"):
        apply_review(store, run.run_id, "APPROVE", "N. Rao", "Looks fine")                          # no call-back confirmed
    with pytest.raises(ReviewError, match="who you spoke to"):
        apply_review(store, run.run_id, "APPROVE", "N. Rao", "  ", {"bank_verified": True})         # call claimed, nothing recorded
    apply_review(store, run.run_id, "APPROVE", "N. Rao", "Called the number on file, spoke to Ms Hegde", {"bank_verified": True})
    with pytest.raises(ReviewError, match="call record"):
        apply_review(store, run.run_id, "APPROVE", "S. Menon")                                      # second approver must confirm they read it
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"


def test_either_person_can_still_reject_a_bank_change_and_nothing_is_learned(pipeline, store):
    run = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "09.pdf", "test")
    apply_review(store, run.run_id, "APPROVE", "N. Rao", "Called, could not reach the accounts head, left a message", {"bank_verified": True})
    apply_review(store, run.run_id, "REJECT", "S. Menon", "Vendor called back: they never changed their account")
    assert store.get_ledger(run.ledger_id)["status"] == "REJECTED" and store.overrides("V001", "bank_account") == []


def test_proof_of_the_bank_change_is_kept_with_the_review(pipeline, store):
    run = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "09.pdf", "test")
    letter = b"%PDF-1.4 cancelled cheque and mandate letter (test bytes)"
    receipt = apply_review(store, run.run_id, "APPROVE", "N. Rao", "Called the number on file, spoke to Ms Hegde",
                           {"bank_verified": True}, evidence=("mandate_letter.pdf", letter))
    kept = receipt["evidence"]
    assert kept["name"] == "mandate_letter.pdf" and len(kept["sha256"]) == 64 and kept["bytes"] == len(letter)
    assert store.load_attachment(kept["sha256"]) == letter                        # the file itself is kept, and its fingerprint is in the chain


# ---------------------------------------------------------------- determinism: the same file twice

def test_the_same_file_again_gets_the_same_answer_and_is_booked_once(pipeline, store):
    """Audit claims table, "same invoice, same answer", point (c)."""
    first = pipeline.run(sample_bytes("01_happy_path_furniture"), "a.pdf", "test")
    again = pipeline.run(sample_bytes("01_happy_path_furniture"), "renamed_copy.pdf", "test")
    assert first.verdict == again.verdict == "APPROVE" and reasons(again) == reasons(first)
    assert again.resubmission_of == first.run_id and first.resubmission_of is None
    assert first.run_id in again.decision.explanation and "not booked again" in again.decision.explanation
    assert again.ledger_id is None and store.po_usage("PO-2026-0147")["posted_amount"] == 381300   # booked once
    assert len([e for e in store.ledger_entries() if e["origin"] == "run"]) == 1
    assert [s.key for s in again.stages] == [s.key for s in first.stages]                            # still nine stages on screen
    assert [s.status for s in again.stages[1:7]] == ["skipped"] * 6
    assert [r["kind"] for r in store.receipts(again.run_id)] == ["resubmission"] and store.verify_chain()["ok"]


def test_a_held_invoice_sent_again_points_at_the_first_and_is_not_queued_twice(pipeline, store):
    first = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "a.pdf", "test")
    again = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "a.pdf", "test")
    assert again.verdict == "HOLD" and reasons(again) == {"BANK_DETAILS_CHANGED"} and again.resubmission_of == first.run_id
    assert [r["review_status"] for r in store.list_runs()] == ["", "PENDING"]                       # one item in the queue
    with pytest.raises(ReviewError, match="re-submission"):
        apply_review(store, again.run_id, "REJECT", "N. Rao")                                        # decisions belong to the first run


def test_a_file_that_was_never_booked_is_read_afresh_when_the_rules_have_changed(store, rules, master):
    """Replaying an old answer is right only while nothing that produced it has changed."""
    def run_with(r):
        return Pipeline(store=store, rules=r, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture").run(
            sample_bytes("bad_inputs/quotation_not_invoice.pdf"), "q.pdf", "test")
    first = run_with(rules)
    same = run_with(rules)
    assert same.resubmission_of == first.run_id and same.verdict == first.verdict == "REJECT"
    changed = rules.model_copy(deep=True)
    changed.sha256 = "0" * 64                                                                       # as if rules.yaml had been edited
    fresh = run_with(changed)
    assert fresh.resubmission_of is None and fresh.verdict == "REJECT" and [s.status for s in fresh.stages[1:3]] != ["skipped"] * 2


def test_without_the_intake_check_the_duplicate_stage_still_refuses_the_same_file(store, rules, master):
    """Belt and braces: the old tier 1 check stays behind the new one."""
    p = Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture",
                 replay_resubmissions=False)
    first = p.run(sample_bytes("01_happy_path_furniture"), "a.pdf", "test")
    second = p.run(sample_bytes("01_happy_path_furniture"), "b.pdf", "test")
    assert first.verdict == "APPROVE" and second.verdict == "REJECT" and reasons(second) == {"DUPLICATE_FILE"}
    assert store.po_usage("PO-2026-0147")["posted_amount"] == 381300


def test_three_fresh_databases_give_three_identical_records(rules, master, tmp_path):
    """Same file, same rules, same data, same business date: the same record, word for word."""
    seen = []
    for n in range(3):
        p = Pipeline(store=Store(tmp_path / f"{n}.db"), rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY,
                     ocr_mode="fixture")
        r = p.run(sample_bytes("06_E3_scan_no_number_implied_po"), "06.pdf", "test")
        body = dict(r.receipt)
        for volatile in ("created_at", "hash", "prev_hash"):
            body.pop(volatile, None)
        seen.append((r.verdict, r.decision.model_dump_json(), store_free(body)))
    assert seen[0] == seen[1] == seen[2]


def store_free(body: dict) -> str:
    import json
    return json.dumps(body, sort_keys=True, default=str)


# ---------------------------------------------------------------- E4 and E2: what the samples really show

def _price_finding(result, code):
    return next(f for f in result.decision.reasons + result.decision.notes if f.code == code)


def test_e4_sits_on_both_sides_of_the_price_tolerance(pipeline, rules):
    """Audit claims table: E4 was 0.11% and 1.56% against a 1% limit, and E4b also tripped the PO balance."""
    tol = rules.tolerances.unit_price
    inside = pipeline.run(sample_bytes("07_E4a_tax_inclusive_within_tolerance"), "07.pdf", "test")
    outside = pipeline.run(sample_bytes("08_E4b_tax_inclusive_over_tolerance"), "08.pdf", "test")
    assert inside.verdict == "APPROVE" and outside.verdict == "HOLD"
    assert reasons(outside) == {"PRICE_VARIANCE"}                                  # nothing else trips: not the quantity, not the PO balance

    note = _price_finding(inside, "PRICE_WITHIN_TOLERANCE")
    hold = _price_finding(outside, "PRICE_VARIANCE")
    assert "0.95%" in note.summary and "₹488.16" in note.summary              # just under 1% and just under Rs 500
    assert float(hold.evidence["pct"]) > float(tol.pct) and float(hold.evidence["pct"]) - float(tol.pct) < 0.05
    assert 0 < float(hold.evidence["line_extra"]) - float(tol.abs_inr) < 20       # just over both limits
    # the two invoices differ by two rupees on the printed unit price and nothing else that matters
    assert [(i.qty, i.unit_price) for i in inside.invoice.line_items] == [(16, 3812)]
    assert [(i.qty, i.unit_price) for i in outside.invoice.line_items] == [(16, 3814)]


def test_between_the_two_e4_samples_one_rupee_shows_why_both_limits_exist(rules, master, store):
    """Rs 3,813 printed is 0.98% over (inside the percentage) but Rs 501.76 on the line (over the rupee limit)."""
    from invoice_desk.po_match import match_po
    from .helpers import codes, make_invoice
    from decimal import Decimal
    inv = make_invoice(vendor="V005", number="SIS/2399", inv_date="2026-11-17", po="PO-2026-0229",
                       lines=(("Laser toner cartridge 26A compatible", 16, "3231.36"),),      # 3813 / 1.18
                       bank_account="117700452981", ifsc="CUCB0001177")
    _, findings, _, _ = match_po(inv, master.vendors["V005"], master, store, rules)
    assert codes(findings) == {"PRICE_VARIANCE"}
    f = findings[0]
    assert Decimal(f.evidence["pct"]) < rules.tolerances.unit_price.pct and Decimal(f.evidence["line_extra"]) == Decimal("501.76")


def test_e2_is_rejected_on_the_match_itself_while_the_po_still_has_room(pipeline, store, master):
    """Audit claims table: E2 rejected only because its PO was already full."""
    r = pipeline.run(sample_bytes("05_E2_disguised_duplicate"), "05.pdf", "test")
    assert r.verdict == "REJECT" and reasons(r) == {"DUPLICATE_CONTENT"}
    f = r.decision.reasons[0]
    assert f.evidence["po_has_room"] is True and f.evidence["line_overlap"] == 1.0
    po = master.pos["PO-2026-0171"]
    assert all(line.qty_ordered > 8 for line in po.lines)                          # the PO really has room for another 8
    assert "QTY_OVER_PO" not in {x.code for x in r.decision.also_noted}
    assert "DQI/26-27/1187" in f.summary and "2 day(s) apart" in f.summary
    assert "second delivery" in f.summary                                          # says what would have cleared it


def test_a_content_duplicate_with_room_on_the_po_is_rejected_and_one_with_unreadable_lines_is_asked_about(master, store, rules):
    from .helpers import codes, make_invoice
    from .test_duplicates import check
    wrap = "Stretch wrap film roll 23 micron"
    post(store, "T1", "V003", "PO-2026-0212", [(1, wrap, 20, "12800.00")], number="MPP/100", inv_date="2026-11-05")
    same = make_invoice(vendor="V003", number="MPP/107", inv_date="2026-11-10", po="PO-2026-0212", lines=((wrap, 20, "640.00"),))
    findings, rows = check(same, "V003", master, store, rules)
    assert codes(findings) == {"DUPLICATE_CONTENT"} and rules.reason("DUPLICATE_CONTENT").action == "REJECT"
    assert findings[0].evidence["po_has_room"] is True and rows[2]["Result"] == "FOUND"

    unread = make_invoice(vendor="V003", number="MPP/108", inv_date="2026-11-10", po="PO-2026-0212", lines=((wrap, 20, "640.00"),))
    unread.line_items = []                                                         # same total and date, but nothing to compare line by line
    findings, rows = check(unread, "V003", master, store, rules)
    assert codes(findings) == {"POSSIBLE_DUPLICATE"} and rules.reason("POSSIBLE_DUPLICATE").action == "HOLD"
    assert rows[2]["Result"] == "REVIEW"


# ---------------------------------------------------------------- partly-true claim: "every threshold is in rules.yaml"

def test_every_threshold_the_process_uses_is_in_rules_yaml(rules):
    """Audit claims table, last row. These five groups were constants in the code."""
    from invoice_desk import po_match, reader, second_read, vendor
    m = rules.matching
    assert (m.vendor_name_match, m.vendor_name_same, m.vendor_name_doubt, m.line_pairing_min) == (0.90, 0.999, 0.60, 0.72)
    w = rules.po_inference.weights
    assert (w.wording, w.price, w.quantity) == (0.65, 0.25, 0.10) and abs(w.wording + w.price + w.quantity - 1) < 1e-9
    assert (rules.reading.scan_agreed_confidence, rules.reading.scan_doubt_confidence) == (0.92, 0.50)
    assert (rules.intake.max_file_mb, rules.intake.max_pages) == (15, 20)
    for module, names in ((vendor, ("NAME_MATCH", "NAME_SAME", "NAME_DOUBT")), (po_match, ("LINE_MATCH",)),
                          (second_read, ("AGREED", "DOUBT")), (reader, ("MAX_BYTES",))):
        for name in names:
            assert not hasattr(module, name), f"{module.__name__}.{name} is still a constant in the code"


def test_the_thresholds_moved_to_rules_yaml_are_really_used(pipeline, rules, master, store, tmp_path):
    """Changing the number in the rules changes the behaviour: they are not decoration."""
    strict = rules.model_copy(deep=True)
    strict.intake.max_pages = 1
    two_pages = G.render_multipage_invoice_pdf(G.spec(
        id="long", layout="classic", vendor="V001", invoice_no="TOF/26-27/0500", invoice_date=G.date(2026, 11, 18),
        po_ref="PO-2026-0147", po_date=G.date(2026, 10, 20),
        lines=[G.line(f"{d} lot {i}", h, i + 1, u, f"{100 + i * 37.25:.2f}") for i, (d, h, u) in enumerate(G.VARIANT_ITEMS * 2, 1)][:14]))
    p = Pipeline(store=Store(tmp_path / "s.db"), rules=strict, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture")
    assert reasons(p.run(two_pages, "two.pdf", "test")) == {"FILE_TOO_LARGE"}

    loose = rules.model_copy(deep=True)
    loose.matching.vendor_name_same = 0.90                    # a near-identical printed name is no longer noted as a variant
    q = Pipeline(store=Store(tmp_path / "l.db"), rules=loose, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture")
    r = q.run(sample_bytes("09_fraud_guard_bank_change"), "09.pdf", "test")
    assert "VENDOR_NAME_VARIANT" not in {f.code for f in r.decision.notes}
    base = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "09.pdf", "test")
    assert "VENDOR_NAME_VARIANT" in {f.code for f in base.decision.notes}


# ---------------------------------------------------------------- weak spot in the tests themselves

def test_the_live_ocr_tests_accept_only_reading_holds():
    """Audit section 6: the set of "reading" holds contained business exceptions, so a live-OCR
    test passed on almost any hold."""
    from .conftest import READING_HOLDS
    business = {"PRICE_VARIANCE", "QTY_OVER_PO", "OVER_PO_BALANCE", "BANK_DETAILS_CHANGED", "BUNDLED_PARTIAL",
                "GOODS_NOT_RECEIVED", "DUPLICATE_CONTENT", "POSSIBLE_DUPLICATE", "VENDOR_BLOCKED"}
    assert READING_HOLDS & business == set()


def test_bad_input_files_are_bundled_so_they_can_be_tried_live(manifest):
    """Audit claims table: only the corrupt and the empty file existed as files."""
    bad = {s["id"]: s for s in manifest if s["group"] == "bad_input"}
    for wanted in ("password_protected", "too_many_pages", "not_a_pdf", "truncated"):
        match = [s for s in bad.values() if wanted in s["id"]]
        assert match and (config.SAMPLES_DIR / match[0]["file"]).is_file(), wanted


# ---------------------------------------------------------------- bug 5: "left blank" was claimed without looking

def _page_with_field(draw=None) -> bytes:
    """A one-page PDF with 'Bill No.' and an empty space beside it; `draw` may put something there."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 11)
    c.drawString(60, 700, "Bill No.")
    c.drawString(400, 700, "Date 12-11-2026")
    if draw:
        draw(c)
    c.save()
    return buf.getvalue()


FIELD = [110, 842 - 712, 390, 842 - 696]        # the space beside the label, in page points from the top


def test_ink_beside_a_label_is_measured_and_ruled_lines_do_not_count():
    from invoice_desk.reader import ink_share

    def underline(c):
        c.setLineWidth(0.8)
        c.line(112, 698, 385, 698)                      # the dotted or ruled line of a form field

    def scribble(c):
        c.setLineWidth(1.4)
        path = c.beginPath()
        path.moveTo(118, 699)
        for n in range(1, 40):
            path.lineTo(118 + n * 4, 699 + (9 if n % 2 else 0) + (n % 5))
        c.drawPath(path)

    empty = ink_share(_page_with_field(), 1, FIELD, 595.27)
    ruled = ink_share(_page_with_field(underline), 1, FIELD, 595.27)
    written = ink_share(_page_with_field(scribble), 1, FIELD, 595.27)
    assert empty == 0.0 and ruled is not None and ruled < 0.008        # an empty box, with or without its rule
    assert written > 0.03                                               # something is there
    assert ink_share(_page_with_field(), 1, [700, 10, 705, 12], 595.27) is None      # off the page: not judged
    assert ink_share(b"not a pdf", 1, FIELD, 595.27) is None


def test_the_vendor_is_blamed_for_a_blank_number_only_when_the_box_is_seen_to_be_empty(rules):
    """Audit bug 5: on a scan where OCR missed the number, the drafted note told the vendor they left it blank."""
    from invoice_desk import extract
    from invoice_desk.validate import validate
    from .helpers import make_invoice

    def hold_for(reason):
        inv = make_invoice(invoice_number=None)
        inv.fields["invoice_number"].missing_reason = reason
        return next(f for f in validate(inv, rules, TODAY)[0] if f.code == "MISSING_INVOICE_NUMBER")

    proven, unread, unchecked = hold_for(extract.BLANK_PROVEN), hold_for(extract.BLANK_UNREAD), hold_for(extract.BLANK_UNCHECKED)
    assert "left blank" in proven.summary and proven.vendor_issue and "left blank" in proven.vendor_issue
    assert unread.vendor_issue is None and "could not be read" in unread.summary and "document image" in unread.question
    assert unchecked.vendor_issue is None and "left blank" not in unchecked.summary


def test_a_title_is_not_mistaken_for_a_number_label(pipeline):
    """Found while fixing bug 5: on a scan, the title 'TAX INVOICE' is within the spelling slack of the label
    'tax invoice no', so any scan with no number read was reported as 'left blank'."""
    blank = pipeline.run(sample_bytes("06_E3_scan_no_number_implied_po"), "06.pdf", "test")
    number = blank.invoice.fields["invoice_number"]
    assert number.source.snippet == "Bill No." and "left blank" in number.missing_reason      # the real label, and a box seen to be empty
    share = float(number.notes[0].split("%")[0])                                              # scan noise only
    assert share < 0.8 and "of the space beside the label is ink" in number.notes[0]
    assert "invoice number is left blank" in blank.decision.vendor_draft


@pytest.mark.skipif(not __import__("invoice_desk.reader", fromlist=["x"]).get_engine().available, reason="Tesseract is not installed")
def test_a_scan_with_something_unreadable_in_the_number_box_does_not_blame_the_vendor(rules, master, store):
    """End to end on a live scan: the number box holds a scrawl that OCR cannot read."""
    import io
    import pypdfium2 as pdfium
    from PIL import ImageDraw
    from invoice_desk import reader
    spec = G.spec(id="x", layout="typewriter", vendor="V003", invoice_no=None, invoice_date=G.date(2026, 11, 12),
                  po_ref=None, lines=[G.line("Stretch wrap film roll 23 micron", "3920", 120, "Roll", "640.00"),
                                      G.line("BOPP packing tape 48 mm (carton of 72)", "3919", 25, "Carton", "2340.00"),
                                      G.line("Bubble wrap roll 1 m x 100 m", "3921", 40, "Roll", "1150.00")])
    text_pdf = G.render_invoice_pdf(spec)
    label = next(c for line in reader.read_pdf(text_pdf).lines for c in line.cells if c.text.startswith("Bill No"))
    pdf = pdfium.PdfDocument(text_pdf)
    scale = 220 / 72
    image = pdf[0].render(scale=scale).to_pil().convert("L")
    pdf.close()
    draw = ImageDraw.Draw(image)
    x, y = (label.x1 + 14) * scale, (label.top + label.bottom) / 2 * scale
    for n in range(26):                                   # a looping scrawl, like a signature across the box
        draw.line([(x + n * 9, y - 13 + (n % 3) * 9), (x + n * 9 + 16, y + 12 - (n % 4) * 6)], fill=30, width=4)
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PDF", resolution=220)
    p = Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="live")
    r = p.run(out.getvalue(), "scrawled.pdf", "test")
    number = r.invoice.fields["invoice_number"]
    assert r.verdict == "HOLD"
    if not number.present:                                # the engine read nothing there: the hold must not blame the vendor
        hold = next(f for f in r.decision.reasons if f.code == "MISSING_INVOICE_NUMBER")
        assert "could not be read" in number.missing_reason and hold.vendor_issue is None
        assert "left blank" not in (r.decision.vendor_draft or "")
    else:                                                 # the engine read the scrawl as text: then it must not be trusted
        assert {"LOW_CONFIDENCE_READ", "MISSING_INVOICE_NUMBER"} & reasons(r) or number.confidence < rules.reading.min_confidence_critical
