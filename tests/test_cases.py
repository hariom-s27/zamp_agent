"""The case desk (round 3): every hold becomes a case with one owner, one question and a
clock. Cases are derived from held runs by a reconciler, so the pipeline is not touched;
opening, closing and escalating a case write to case_events only, never to the audit chain."""
import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from invoice_desk import cases
from invoice_desk import samples as sample_lib
from invoice_desk.review import apply_review
from invoice_desk.store import Store

from .conftest import TODAY, sample_bytes

C1, C2 = "c1_invoice_before_goods", "c2_small_supplier_late_invoice"


def case_sample(sample_id: str) -> dict:
    return next(s for s in sample_lib.load_case_manifest() if s["id"] == sample_id)


def run_case_sample(pipeline, sample_id: str):
    sample = case_sample(sample_id)
    return pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "test")


def rules_with(rules, **changes):
    """A copy of the rules with some cases settings changed."""
    copy = rules.model_copy(deep=True)
    for key, value in changes.items():
        setattr(copy.cases, key, value)
    return copy


# ---------------------------------------------------------------- opening a case

def test_the_invoice_before_goods_is_held_for_stores_and_for_nothing_else(pipeline):
    run = run_case_sample(pipeline, C1)
    assert run.verdict == "HOLD" and [f.code for f in run.decision.reasons] == ["GOODS_NOT_RECEIVED"]
    assert run.decision.owners == ["Stores"]


def test_a_hold_opens_exactly_one_case_with_its_owner_question_and_clock(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C1)
    assert cases.sync(store, master, rules, TODAY) == {"opened": 1, "closed": 0, "released": 0, "escalated": 0}
    [case] = store.list_cases()
    assert case["case_id"] == "CASE-" + run.file_sha256[:8].upper() == cases.case_id_for(run.file_sha256)
    assert case["run_id"] == run.run_id and store.case_for_run(run.run_id)["case_id"] == case["case_id"]
    assert (case["state"], case["owner"], case["waiting_on"]) == ("WAITING", "Stores", "Stores")
    assert case["question"] == run.decision.questions[0] and case["reason_codes"] == "GOODS_NOT_RECEIVED"
    assert case["opened_on"] == "2026-11-21" and case["due_on"] == "2026-11-23"        # Stores: 2 days in rules.yaml
    assert case["priority"] == "normal" and case["escalated_to"] == ""
    # V101 is a small enterprise, but one billed line has no delivery on record: counted from the invoice date
    assert case["statutory_due"] == "2026-12-19" and "invoice date" in case["statutory_basis"]
    assert [e["kind"] for e in store.case_events(case["case_id"])] == ["opened"]
    assert [r["kind"] for r in store.receipts(run.run_id)] == ["decision"]               # no receipt for a case


def test_sync_twice_changes_nothing(pipeline, store, master, rules):
    run_case_sample(pipeline, C1)
    run_case_sample(pipeline, C2)
    cases.sync(store, master, rules, TODAY)
    before = [(c, store.case_events(c["case_id"])) for c in store.list_cases()]
    receipts = len(store.receipts())
    assert cases.sync(store, master, rules, TODAY) == {"opened": 0, "closed": 0, "released": 0, "escalated": 0}
    assert [(c, store.case_events(c["case_id"])) for c in store.list_cases()] == before
    assert len(store.receipts()) == receipts


def test_an_approved_or_rejected_run_and_a_resubmission_open_no_case(pipeline, store, master, rules):
    assert pipeline.run(sample_bytes("01_happy_path_furniture"), "a.pdf", "test").verdict == "APPROVE"
    assert pipeline.run(sample_bytes("05_E2_disguised_duplicate"), "b.pdf", "test").verdict == "REJECT"
    first = run_case_sample(pipeline, C1)
    again = run_case_sample(pipeline, C1)
    assert again.resubmission_of == first.run_id
    cases.sync(store, master, rules, TODAY)
    assert [c["run_id"] for c in store.list_cases()] == [first.run_id]


def test_a_case_closes_when_a_person_decides_the_hold_in_the_review_queue(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C2)
    cases.sync(store, master, rules, TODAY)
    apply_review(store, run.run_id, "REJECT", "R. Iyer", "Re-issue at the PO price", master=master, rules=rules)
    assert cases.sync(store, master, rules, TODAY)["closed"] == 1
    case = store.case_for_run(run.run_id)
    assert (case["state"], case["resolution"], case["resolved_on"]) == ("RESOLVED", "rejected", "2026-11-21")
    last = store.case_events(case["case_id"])[-1]
    assert last["kind"] == "closed_by_review" and last["actor"] == "R. Iyer"
    assert store.list_cases(open_only=True) == []


def test_no_move_leaves_a_resolved_case_and_every_unlisted_move_is_refused():
    events = {event for _, event in cases.MOVES} | {"open", "escalate", "anything"}
    for state in cases.STATES:
        for event in events:
            if (state, event) in cases.MOVES:
                assert cases.move(state, event) == cases.MOVES[(state, event)] in cases.STATES
            else:
                with pytest.raises(cases.CaseError):
                    cases.move(state, event)
    assert not [key for key in cases.MOVES if key[0] == "RESOLVED"]


def test_cases_switched_off_write_nothing(pipeline, store, master, rules):
    run_case_sample(pipeline, C2)
    off = rules_with(rules, enabled=False)
    assert cases.sync(store, master, off, TODAY) == {"opened": 0, "closed": 0, "released": 0, "escalated": 0}
    assert store.list_cases() == []


# ---------------------------------------------------------------- clocks and escalation

def test_a_small_supplier_three_days_from_the_statutory_date_is_urgent_and_escalated_on_arrival(pipeline, store,
                                                                                                 master, rules):
    run = run_case_sample(pipeline, C2)
    assert [f.code for f in run.decision.reasons] == ["PRICE_VARIANCE"]
    assert cases.sync(store, master, rules, TODAY) == {"opened": 1, "closed": 0, "released": 0, "escalated": 1}
    case = store.case_for_run(run.run_id)
    assert (case["owner"], case["waiting_on"], case["due_on"]) == ("Procurement", "Procurement", "2026-11-24")
    assert case["statutory_due"] == "2026-11-24" and case["priority"] == "urgent"
    assert (case["escalated_to"], case["escalated_on"]) == ("Finance controller", "2026-11-21")
    assert [e["kind"] for e in store.case_events(case["case_id"])] == ["opened", "escalated"]


def test_the_statutory_date_uses_the_delivery_date_when_known_and_says_so_when_it_is_not(master, rules):
    small, micro = master.vendors["V101"], master.vendors["V102"]
    delivered = [master.pos["PO-2026-1042"].line(1)]
    clock = cases.statutory_clock(micro, delivered, date(2026, 10, 12), Decimal("94400.00"), rules, TODAY)
    assert clock["due"] == date(2026, 11, 24) and clock["days_left"] == 3
    assert clock["basis"] == "counted from the delivery recorded on 10 Oct 2026"
    assert clock["interest_per_day"] == 45 and clock["approximate"] is True     # 94,400 x 3 x 5.75% / 365 = 44.61
    po = master.pos["PO-2026-1041"]
    clock = cases.statutory_clock(small, [po.line(1), po.line(2)], date(2026, 11, 19), Decimal("175112.00"), rules, TODAY)
    assert clock["due"] == date(2026, 12, 19) and clock["days_left"] == 28
    assert clock["basis"] == ("counted from the invoice date because no delivery date is on record; "
                              "the legal clock starts when the goods are accepted")
    no_terms = micro.model_copy(update={"payment_terms_days": 0})
    long_terms = micro.model_copy(update={"payment_terms_days": 90})
    assert cases.statutory_clock(no_terms, delivered, date(2026, 10, 12), Decimal("1"), rules, TODAY)["due"] == \
        date(2026, 10, 25)                                                       # 15 days with no agreement
    assert cases.statutory_clock(long_terms, delivered, date(2026, 10, 12), Decimal("1"), rules, TODAY)["due"] == \
        date(2026, 11, 24)                                                       # never beyond 45


def test_a_medium_or_unflagged_supplier_has_no_statutory_clock(master, rules):
    line = [master.pos["PO-2026-1042"].line(1)]
    assert cases.statutory_clock(master.vendors["V001"], line, TODAY, Decimal("1000"), rules, TODAY) is None
    medium = master.vendors["V102"].model_copy(update={"msme_category": "medium"})
    assert cases.statutory_clock(medium, line, TODAY, Decimal("1000"), rules, TODAY) is None


def test_an_overdue_case_escalates_once_to_the_named_role(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C1)
    cases.sync(store, master, rules, TODAY)
    assert cases.sync(store, master, rules, date(2026, 11, 23))["escalated"] == 0     # due today: not yet overdue
    assert cases.sync(store, master, rules, date(2026, 11, 24))["escalated"] == 1
    case = store.case_for_run(run.run_id)
    assert (case["escalated_to"], case["escalated_on"]) == ("Procurement", "2026-11-24")   # Stores escalate to Procurement
    assert cases.sync(store, master, rules, date(2026, 11, 30))["escalated"] == 0
    assert [e["kind"] for e in store.case_events(case["case_id"])] == ["opened", "escalated"]


# ---------------------------------------------------------------- the store

def test_a_fresh_demo_clears_cases_messages_and_goods_receipts(store):
    store.add_case({"case_id": "CASE-00000001", "run_id": "AP-2026-0001", "opened_on": "2026-11-21", "state": "WAITING",
                    "waiting_on": "Stores", "owner": "Stores", "question": "q", "reason_codes": "GOODS_NOT_RECEIVED",
                    "priority": "normal", "due_on": "2026-11-23", "statutory_due": "", "statutory_basis": ""})
    store.add_case_event("CASE-00000001", "opened", "Invoice Desk", "Case opened")
    store.add_message({"message_id": "CASE-00000001-01", "case_id": "CASE-00000001", "direction": "out",
                       "status": "written", "approved_by": "A", "sender": "a@x", "recipient": "b@y", "subject": "s",
                       "body": "b", "eml_sha256": "0" * 64})
    store.add_goods_receipt("PO-2026-1041", 2, Decimal("60"), "2026-11-21", "A. Kulkarni", "DN-1")
    store.reset()
    assert store.list_cases() == [] and store.case_events("CASE-00000001") == []
    assert store.messages("CASE-00000001") == [] and store.goods_receipts() == []
    store.add_case_event("CASE-00000002", "opened", "Invoice Desk", "Case opened")
    store.add_goods_receipt("PO-2026-1041", 2, Decimal("1"), "2026-11-21", "A. Kulkarni", "DN-2")
    assert store.case_events("CASE-00000002")[0]["id"] == 1 and store.goods_receipts()[0]["id"] == 1


def test_a_round_2_database_opens_with_the_new_tables_and_keeps_its_records(tmp_path, rules, master):
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from .conftest import LLM_OFF
    path = tmp_path / "round2.db"
    old = Store(path)
    run = Pipeline(store=old, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY,
                   ocr_mode="fixture").run(sample_bytes("04_E1_split_po_overbilling"), "a.pdf", "test")
    with sqlite3.connect(path) as conn:                       # what a round 2 database looks like
        for table in ("cases", "case_events", "messages", "goods_receipts"):
            conn.execute(f"DROP TABLE {table}")
    receipts = old.receipts()
    reopened = Store(path)
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"cases", "case_events", "messages", "goods_receipts"} <= tables
    assert reopened.get_run(run.run_id).verdict == "HOLD" and reopened.receipts() == receipts
    assert reopened.verify_chain()["ok"] and reopened.list_cases() == []
    assert cases.sync(reopened, master, rules, TODAY)["opened"] == 1


def test_the_eighteen_samples_give_the_same_decisions_and_receipts_with_cases_on_and_off(tmp_path, rules, master,
                                                                                         manifest):
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from .conftest import LLM_OFF

    def run_all(name: str, with_cases: bool) -> list:
        store = Store(tmp_path / f"{name}.db")
        pipeline = Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY,
                            ocr_mode="fixture")
        seen = []
        for sample in manifest:
            run = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "test")
            if with_cases:
                cases.sync(store, master, rules, TODAY)
            seen.append((sample["id"], run.verdict, sorted(f.code for f in run.decision.reasons),
                         [r["kind"] for r in store.receipts(run.run_id)], store.run_row(run.run_id)["review_status"]))
        return seen

    assert run_all("on", True) == run_all("off", False)


# ---------------------------------------------------------------- goods receipts and release by rule (checkpoint 3)

def receive(store, master, run, qty, line=2, name="A. Kulkarni", ref="DN-5531"):
    return cases.record_goods_receipt(store, master, "PO-2026-1041", line, Decimal(qty), date(2026, 11, 21), name, ref,
                                      for_run=run.run_id)


def test_with_no_receipt_recorded_the_master_is_the_same_object(store, master):
    assert cases.master_with_receipts(master, store) is master


def test_a_goods_receipt_needs_a_known_po_line_a_positive_quantity_and_a_name(pipeline, store, master):
    run = run_case_sample(pipeline, C1)
    for po, line, qty, name in (("PO-2026-9999", 1, "1", "A"), ("PO-2026-1041", 9, "1", "A"),
                                ("PO-2026-1041", 2, "0", "A"), ("PO-2026-1041", 2, "-5", "A"), ("PO-2026-1041", 2, "5", "  ")):
        with pytest.raises(cases.CaseError):
            cases.record_goods_receipt(store, master, po, line, Decimal(qty), TODAY, name, "DN-1", for_run=run.run_id)
    assert store.goods_receipts() == [] and [r["kind"] for r in store.receipts(run.run_id)] == ["decision"]


def test_a_full_goods_receipt_releases_the_invoice_by_rule_and_posts_it(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C1)
    cases.sync(store, master, rules, TODAY)
    receive(store, master, run, "60")
    live = cases.master_with_receipts(master, store)
    assert live is not master and live.pos["PO-2026-1041"].line(2).qty_received == Decimal("60")
    assert live.pos["PO-2026-1041"].line(2).received_on == TODAY and master.pos["PO-2026-1041"].line(2).qty_received == 0
    assert cases.sync(store, master, rules, TODAY)["released"] == 1
    assert store.get_ledger(run.ledger_id)["status"] == "POSTED"
    assert store.run_row(run.run_id)["review_status"] == "RELEASED"
    assert [r["kind"] for r in store.receipts(run.run_id)] == ["decision", "goods_receipt", "release"]
    release = store.receipts(run.run_id)[-1]["body"]
    assert release["actor"] == "rule" and release["decision"] == "RELEASED"
    assert release["reasons_cleared"] == ["GOODS_NOT_RECEIVED"] and release["reviews_receipt"] == run.receipt["hash"]
    assert release["ledger"] == {"ledger_id": run.ledger_id, "status": "POSTED"}
    assert store.reviews(run.run_id)[-1]["reviewer"] == "Invoice Desk (rule)" and store.verify_chain()["ok"]
    case = store.case_for_run(run.run_id)
    assert (case["state"], case["resolution"], case["resolved_on"]) == ("RESOLVED", "released by rule", "2026-11-21")
    assert [e["kind"] for e in store.case_events(case["case_id"])] == ["opened", "goods_receipt", "released_by_rule"]
    with pytest.raises(Exception, match="not waiting for review"):          # nothing left for a person to decide
        apply_review(store, run.run_id, "REJECT", "R. Iyer", "late", master=master, rules=rules)
    assert cases.sync(store, master, rules, TODAY) == {"opened": 0, "closed": 0, "released": 0, "escalated": 0}


def test_a_partial_goods_receipt_keeps_the_invoice_waiting_and_says_how_much_is_missing(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C1)
    cases.sync(store, master, rules, TODAY)
    receive(store, master, run, "30")
    assert cases.sync(store, master, rules, TODAY)["released"] == 0
    case = store.case_for_run(run.run_id)
    assert case["state"] == "WAITING" and case["waiting_on"] == "Stores"
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING" and store.run_row(run.run_id)["review_status"] == "PENDING"
    events = store.case_events(case["case_id"])
    assert [e["kind"] for e in events] == ["opened", "goods_receipt", "partial_receipt"]
    assert events[-1]["summary"] == "30 of 60 received. The invoice still waits for the remaining 30."
    assert cases.try_resume(store, master, rules, case, TODAY) == {
        "released": False, "why": "30 of 60 received. The invoice still waits for the remaining 30."}
    cases.sync(store, master, rules, TODAY)
    assert len(store.case_events(case["case_id"])) == 3                       # said once, not on every sync
    receive(store, master, run, "30", ref="DN-5540")
    assert cases.sync(store, master, rules, TODAY)["released"] == 1


def test_release_by_rule_never_happens_when_any_other_reason_is_on_the_hold(pipeline, store, master, rules):
    from invoice_desk.models import Finding
    run = run_case_sample(pipeline, C1)
    extra = Finding(code="PRICE_VARIANCE", stage="po_match", summary="price over the PO", action="HOLD",
                    owner="Procurement", title="Price is above the PO beyond tolerance")
    run.decision.reasons.append(extra)
    store.replace_run_record(run)
    cases.sync(store, master, rules, TODAY)
    case = store.case_for_run(run.run_id)
    assert case["reason_codes"] == "GOODS_NOT_RECEIVED,PRICE_VARIANCE"
    receive(store, master, run, "60")
    assert cases.sync(store, master, rules, TODAY)["released"] == 0
    outcome = cases.try_resume(store, master, rules, store.case_for_run(run.run_id), TODAY)
    assert outcome["released"] is False and "PRICE_VARIANCE" in outcome["why"]
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING" and "release" not in [
        r["kind"] for r in store.receipts(run.run_id)]


def test_release_by_rule_rechecks_the_po_like_a_human_approval(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C1)
    cases.sync(store, master, rules, TODAY)
    store.add_ledger({"ledger_id": "L-OTHER", "run_id": "", "vendor_id": "V101", "invoice_no": "KBD/26-27/1180",
                      "invoice_date": "2026-11-20", "po_number": "PO-2026-1041", "taxable": "37200.00",
                      "tax": "6696.00", "total": "43896.00", "status": "POSTED"},
                     [{"po_line_no": 2, "description": "Timing belt HTD 8M-1200", "qty": "30",
                       "unit_price": "1240.00", "amount": "37200.00"}])     # another invoice took 30 belts first
    receive(store, master, run, "90")                                        # 90 delivered: the receipt covers both ...
    assert cases.sync(store, master, rules, TODAY)["released"] == 0          # ... but the PO has room for 60 only
    case = store.case_for_run(run.run_id)
    refusals = [e for e in store.case_events(case["case_id"]) if e["kind"] == "resume_refused"]
    assert len(refusals) == 1 and "overbill" in refusals[0]["summary"] and "PO-2026-1041" in refusals[0]["summary"]
    cases.sync(store, master, rules, TODAY)
    assert len([e for e in store.case_events(case["case_id"]) if e["kind"] == "resume_refused"]) == 1
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING" and case["state"] == "WAITING"


def test_auto_resume_can_be_switched_off_in_the_rules(pipeline, store, master, rules):
    never = rules_with(rules, auto_resume=[])
    run = run_case_sample(pipeline, C1)
    cases.sync(store, master, never, TODAY)
    receive(store, master, run, "60")
    assert cases.sync(store, master, never, TODAY)["released"] == 0
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"
    assert cases.try_resume(store, master, never, store.case_for_run(run.run_id), TODAY)["released"] is False
    assert store.case_for_run(run.run_id)["state"] == "WAITING"


# ---------------------------------------------------------------- approve and send to the outbox (checkpoint 4)

def c2_case(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C2)
    cases.sync(store, master, rules, TODAY)
    return run, store.case_for_run(run.run_id)


def read_eml(path):
    import email
    import email.policy
    return email.message_from_bytes(path.read_bytes(), policy=email.policy.default)


def test_the_outbox_is_a_folder_beside_the_database_so_each_sandbox_has_its_own(tmp_path):
    one, two = Store(tmp_path / "a" / "x.db"), Store(tmp_path / "b" / "x.db")
    assert cases.outbox_dir(one) == tmp_path / "a" / "outbox" and cases.outbox_dir(one).is_dir()
    assert cases.outbox_dir(two) == tmp_path / "b" / "outbox"


def test_no_message_is_written_without_a_named_approver(pipeline, store, master, rules):
    run, case = c2_case(pipeline, store, master, rules)
    receipts = len(store.receipts())
    for nobody in ("", "   "):
        with pytest.raises(cases.CaseError, match="name"):
            cases.approve_message(store, master, rules, case["case_id"], nobody, TODAY)
    assert list(cases.outbox_dir(store).iterdir()) == [] and store.messages(case["case_id"]) == []
    assert len(store.receipts()) == receipts and store.case_for_run(run.run_id) == case


def test_a_message_needs_a_drafted_note_and_a_vendor_email_on_file(pipeline, store, master, rules):
    run = run_case_sample(pipeline, C1)                       # goods not received: the vendor has nothing to do
    cases.sync(store, master, rules, TODAY)
    assert run.decision.vendor_draft is None
    with pytest.raises(cases.CaseError, match="no message"):
        cases.approve_message(store, master, rules, store.case_for_run(run.run_id)["case_id"], "S. Rao", TODAY)
    _, case = c2_case(pipeline, store, master, rules)
    no_email = master.model_copy(deep=True)
    no_email.vendors["V102"].email = ""
    with pytest.raises(cases.CaseError, match="email"):
        cases.approve_message(store, no_email, rules, case["case_id"], "S. Rao", TODAY)
    with pytest.raises(cases.CaseError):
        cases.approve_message(store, master, rules, "CASE-NOTTHERE", "S. Rao", TODAY)
    assert list(cases.outbox_dir(store).iterdir()) == [] and store.messages(case["case_id"]) == []


def test_an_approved_message_is_a_valid_email_in_the_outbox_with_the_token_and_one_receipt(pipeline, store, master,
                                                                                         rules):
    import hashlib
    run, case = c2_case(pipeline, store, master, rules)
    draft = run.decision.vendor_draft
    assert draft and draft.startswith("Subject: ")            # the engine drafts a note for a price variance
    own_subject, body = draft.split("\n", 1)[0][len("Subject: "):], draft.split("\n", 1)[1].lstrip("\n")
    sent = cases.approve_message(store, master, rules, case["case_id"], "  S. Rao  ", TODAY)

    path = cases.outbox_dir(store) / f"{case['case_id']}-01.eml"
    assert sent["path"] == path and list(cases.outbox_dir(store).iterdir()) == [path]
    mail = read_eml(path)
    assert mail["From"] == "ap@nilgiri-precision.example" and mail["To"] == "billing@nandihillspack.example"
    assert mail["Subject"] == f"[{case['case_id']}] {own_subject}"
    assert mail.get_body(("plain",)).get_content().strip() == body.strip()       # the drafted text, unchanged
    assert sent["eml_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    [message] = store.messages(case["case_id"])
    assert (message["message_id"], message["direction"], message["status"]) == (f"{case['case_id']}-01", "out", "written")
    assert (message["approved_by"], message["sender"], message["recipient"]) == (
        "S. Rao", "ap@nilgiri-precision.example", "billing@nandihillspack.example")
    assert message["subject"] == mail["Subject"] and message["eml_sha256"] == sent["eml_sha256"]

    assert [r["kind"] for r in store.receipts(run.run_id)] == ["decision", "message"]
    receipt = store.receipts(run.run_id)[-1]["body"]
    assert receipt["reviewer"] == "S. Rao" and receipt["decision"] == "message approved and written to the outbox"
    assert receipt["note"] == mail["Subject"] and receipt["to"] == "billing@nandihillspack.example"
    assert receipt["eml_sha256"] == sent["eml_sha256"] and store.verify_chain()["ok"]

    after = store.case_for_run(run.run_id)
    assert (after["state"], after["waiting_on"], after["owner"]) == ("WAITING", "vendor", "Procurement")
    assert after["due_on"] == "2026-11-24"                     # a vendor has 3 days in rules.yaml
    last = store.case_events(case["case_id"])[-1]
    assert last["kind"] == "message_approved" and last["actor"] == "S. Rao"
    assert store.run_row(run.run_id)["review_status"] == "PENDING"     # a message decides nothing


def test_the_same_message_is_not_approved_twice_and_a_resolved_case_sends_nothing(pipeline, store, master, rules):
    run, case = c2_case(pipeline, store, master, rules)
    cases.approve_message(store, master, rules, case["case_id"], "S. Rao", TODAY)
    with pytest.raises(cases.CaseError, match="already"):
        cases.approve_message(store, master, rules, case["case_id"], "S. Rao", TODAY)
    assert len(store.messages(case["case_id"])) == 1 and len(list(cases.outbox_dir(store).iterdir())) == 1
    apply_review(store, run.run_id, "REJECT", "R. Iyer", "Re-issue at the PO price", master=master, rules=rules)
    cases.sync(store, master, rules, TODAY)
    with pytest.raises(cases.CaseError, match="resolved"):
        cases.approve_message(store, master, rules, case["case_id"], "S. Rao", TODAY)
    assert [r["kind"] for r in store.receipts(run.run_id)].count("message") == 1


def test_a_vendor_who_does_not_reply_in_time_escalates_once_to_the_ap_analyst(pipeline, store, master, rules):
    run, case = c2_case(pipeline, store, master, rules)       # already escalated once: the legal clock
    cases.approve_message(store, master, rules, case["case_id"], "S. Rao", TODAY)
    assert cases.sync(store, master, rules, date(2026, 11, 24))["escalated"] == 0       # vendor's reply due today
    assert cases.sync(store, master, rules, date(2026, 11, 25))["escalated"] == 1
    after = store.case_for_run(run.run_id)
    assert (after["escalated_to"], after["escalated_on"]) == ("AP analyst", "2026-11-25")
    assert cases.sync(store, master, rules, date(2026, 11, 30))["escalated"] == 0
    assert [e["kind"] for e in store.case_events(case["case_id"])] == ["opened", "escalated", "message_approved",
                                                                        "escalated"]


def test_a_receipt_counts_once_when_the_caller_already_added_the_receipts_to_the_master(pipeline, store, master, rules):
    """The Cases page passes sync a master with the receipts already on it; release must not count them twice."""
    run = run_case_sample(pipeline, C1)
    cases.sync(store, master, rules, TODAY)
    receive(store, master, run, "30")
    live = cases.master_with_receipts(master, store)
    assert cases.master_with_receipts(live, store).pos["PO-2026-1041"].line(2).qty_received == Decimal("30")
    assert cases.sync(store, live, rules, TODAY)["released"] == 0
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"
    receive(store, master, run, "30", ref="DN-5540")                # recorded after `live` was built: still counted
    assert cases.master_with_receipts(live, store).pos["PO-2026-1041"].line(2).qty_received == Decimal("60")
    assert cases.sync(store, live, rules, TODAY)["released"] == 1
