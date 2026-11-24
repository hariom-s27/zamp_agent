"""Page 2, Process invoice: choose or upload a PDF and watch the nine steps run.

Reserve, reset, fill: the result areas are reserved before the run, cleared with one element
the moment a run starts, the steps run live, the result is stored and the page reruns. The
finished view (decision card, step timeline, what was checked) is drawn from the stored
record and never runs the pipeline.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import pandas as pd
import streamlit as st

from .. import samples as sample_lib
from .. import textutil as T
from ..models import RunResult, StageResult
from ..pipeline import STAGES, Pipeline
from ..reader import sha256_of
from ..store import Store
from . import common as C
from . import ui_kit as K

SAMPLE, UPLOAD = "From the sample inbox", "Upload a file"
READING, FAST = "Reading speed", "Fast"
RESULT = {"ok": "passed", "attention": "flagged", "blocked": "stopped", "error": "stopped", "skipped": "skipped"}
CARD = {"APPROVE": "cleared", "HOLD": "hold", "REJECT": "rejected"}


def pace_options() -> dict[str, float]:
    """Seconds each step stays on screen while it runs. The reading-speed pause is set in rules.yaml."""
    return {READING: C.get_rules().display.stage_pause_seconds, FAST: 0.0}


def pace_seconds() -> float:
    options = pace_options()
    return options.get(st.session_state.get("pace", READING), options[READING])


def pace_picker(key: str) -> None:
    """The pace is kept in the session under 'pace', apart from the widget, so it survives a
    visit to a page that does not show the control."""
    options = pace_options()
    current = st.session_state.get("pace", READING)
    current = current if current in options else READING
    chosen = st.segmented_control("Pace of the live view", list(options), default=current, key=key,
                                  help="How long each step stays on screen while it runs. The steps themselves "
                                       "finish in a fraction of a second; this only slows the display so an audience "
                                       "can follow it. The times shown are the real processing times.")
    if chosen and chosen != current:
        st.session_state["pace"] = chosen


def plain(text) -> str:
    """Text from a document, safe in Markdown: no HTML, no stray formatting."""
    out = str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in "\\`*_[]$|#":
        out = out.replace(ch, "\\" + ch)
    return out


def amount_text(total) -> str:
    """A stored total for a card: rupees in Indian grouping; anything else (another currency) as written."""
    if total in (None, ""):
        return ""
    try:
        return K.inr(float(total))
    except (TypeError, ValueError):
        return plain(total)


def ai_count(result: Optional[RunResult]) -> tuple[int, int]:
    """(values read by rules, values read by AI) on this invoice."""
    inv = result.invoice if result else None
    if inv is None:
        return 0, 0
    methods = [f.source.method if f.source else "text" for f in inv.fields.values() if f.present]
    methods += [i.source.method if i.source else "text" for i in inv.line_items]
    ai = sum(m == "llm" for m in methods)
    return len(methods) - ai, ai


def to_stage(stage: StageResult, result: Optional[RunResult]) -> K.Stage:
    """A stored stage in plain words, for the timeline."""
    kind = RESULT.get(stage.status, "passed")
    lines = [f"{plain(f.title or 'Finding')}: {plain(C.screen_words(f.summary))}" for f in stage.findings]
    actor, note = "rules", None
    if stage.key == "extract":
        rules_read, ai_read = ai_count(result)
        if ai_read:
            actor, note = "ai", f"AI read {ai_read}"
            lines.insert(0, f"Rules read {rules_read} values. AI read {ai_read} that the layout hid. Each one is "
                            f"checked against a quote from the page.")
    summary = plain(C.screen_words(stage.summary)) or C.stage_display_help(stage.key)
    again = result.resubmission_of if result is not None else None
    if again and stage.key == "intake":
        kind = "stopped"
        summary = f"Duplicate file: the same file as {plain(again)}. Stopped here, nothing booked again"
        lines = [f"This exact file was received before as {plain(again)}. It is not read, checked or booked a "
                 f"second time. Open the original record to see its current decision."]
    elif again and kind == "skipped":
        summary = "Not run: duplicate file"
        lines = [f"Stopped at step 1. See {plain(again)} for this step."]
    if kind == "skipped":
        lines = lines or ["This step was not needed for this document."]
    return K.Stage(number=stage.number, title=C.stage_display_title(stage.key),
                   result=kind, summary=summary,
                   actor=actor, seconds=stage.duration_ms / 1000, lines=lines, actor_note=note)


@st.cache_data(show_spinner=False)
def _sample_hashes() -> dict[str, str]:
    """Fingerprint of every bundled sample, to recognise one when it is processed."""
    return {s["id"]: sha256_of(sample_lib.sample_bytes(s)) for s in sample_lib.load_manifest()}


def first_runs(store: Store) -> dict[str, dict]:
    """The earliest record of each file in the history, keyed by file fingerprint."""
    first: dict[str, dict] = {}
    for row in store.list_runs():          # newest first, so the last write per key is the oldest record
        first[row["file_sha256"]] = row
    return first


sample_name = sample_lib.received_name


def expected_text(sample: dict, sha256: str) -> str:
    """What this sample should end as in this session. A sample that the AI assist changes (the letter) has two
    right answers, and which one applies depends on the mode and on whether Claude's answer for it is stored."""
    rules = C.get_rules()

    def said(verdict: str, reasons) -> str:
        return verdict + (f" ({', '.join(sorted(rules.reason(code).title for code in reasons))})" if reasons else "")

    alone = said(sample["expected_verdict"], sample["expected_reasons"])
    if "with_llm" not in sample:
        return f"Expected decision: {alone}."
    helped = said(sample["with_llm"]["expected_verdict"], sample["with_llm"]["expected_reasons"])
    llm = C.get_llm()
    if llm.cfg.mode == "off":
        return f"Expected decision: {alone}, because AI assist is off. With AI assist: {helped}."
    if llm.has_recording(sha256):
        return (f"Expected decision: {helped}, because the AI's answer for this file is stored and will be replayed. "
                f"By the rules alone: {alone}.")
    if llm.cfg.can_call:
        return (f"Expected decision: {helped}, once the AI has read it (one live call; the answer is then kept). "
                f"By the rules alone: {alone}.")
    return (f"Expected decision: {alone}. No AI answer is recorded for this file yet, so the rules decide alone. "
            f"With a recorded answer: {helped}.")


def run_live(area, pipeline: Pipeline, content: bytes, name: str, source: str, pause: float) -> Optional[RunResult]:
    """Run the real pipeline and draw each step as it starts and as it finishes.

    Reset first: the slot that held the last answer is emptied, so the previous invoice's decision is
    never on screen while the next one runs. The caller stores the result and reruns."""
    result: Optional[RunResult] = None
    live: Optional[RunResult] = None
    boxes: dict = {}
    keys = [k for k, _ in STAGES]
    sha = sha256_of(content)
    area.empty()
    if pause > 0:
        time.sleep(0.1)            # let the browser draw the empty slot before the new steps arrive
    with area.container():
        left, right = st.columns([5, 4], gap="large")
        with left:
            st.markdown("**The document**")
            preview = st.empty()
            with preview.container():
                C.render_preview(content, sha, None, key="live_before")
        with right:
            st.skeleton(height=190)                       # the decision card fills in when the run ends
            st.markdown("**How it was handled**")
            st.caption(f"Working on {plain(C.display_name(name))}. Nothing is decided until step 8.")
            for kind, payload in pipeline.iter_run(content, name, source):
                if kind == "begin":
                    live = payload
                elif kind == "start":
                    number = keys.index(payload) + 1
                    boxes[payload] = st.status(f"**{number}. {C.stage_display_title(payload)}**", type="step",
                                               state="running", expanded=False)
                    boxes[payload].caption(C.stage_display_help(payload))   # one line keeps the timeline joined
                    if pause > 0:
                        time.sleep(pause)
                elif kind == "stage":
                    step = to_stage(payload, live)
                    if step.result == "skipped" and live is not None and live.resubmission_of:
                        box = boxes.get(payload.key)
                        if box is not None:
                            box.update(label=f"**{step.number}. {step.title}** · {step.summary}", state="complete")
                        continue
                    box = boxes.get(payload.key) or st.status("", type="step", expanded=False)
                    box.update(label=f"**{step.number}. {step.title}** · {step.summary}",
                               state="error" if step.result == "stopped" else "complete")
                    if payload.key == "extract" and live is not None and live.invoice is not None:
                        with preview.container():       # show where each value was read, as soon as it is known
                            C.render_preview(content, sha, live, key="live_after")
                elif kind == "result":
                    result = payload
    return result


def run_batch(area, store: Store) -> None:
    """Start from the seeded books, then process every bundled sample in its documented order."""
    C.reset_books(store)
    pipeline = C.get_pipeline(store=store)
    samples = C.manifest()
    rows: list[dict] = []
    with area.container():
        st.markdown(f"**Processing all {len(samples)} sample documents in order**")
        st.caption("The books were put back to their starting state first, so every document meets the situation it "
                   "was written for.")
        bar = st.progress(0.0, text="Starting")
        slot = st.empty()
        for i, sample in enumerate(samples, 1):
            bar.progress((i - 1) / len(samples), text=f"{i} of {len(samples)}: {sample['title']}")
            result = pipeline.run(sample_lib.sample_bytes(sample), sample_name(sample), "sample")
            ok, want, _ = sample_lib.check(sample, result)
            rows.append({"Reference": result.run_id, "Scenario": sample["title"], "Expected": C.screen_words(want),
                         "Decision": C.verdict_display_label(result.verdict, icon=True),
                         "As expected": "yes" if ok else "NO",
                         "Reasons": "; ".join(f.title for f in result.decision.reasons) or "-",
                         "Time": C.took(result.duration_ms)})
            slot.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=35 * (len(samples) + 1) + 3)
        bar.progress(1.0, text="Done")
    st.session_state["batch"] = rows
    st.session_state.pop("current_run", None)


def expectation_note(result: RunResult, store: Store) -> None:
    """For a bundled sample, say what it is there to show and whether the decision is the expected one."""
    hashes = _sample_hashes()
    sample = next((s for s in C.manifest() if hashes.get(s["id"]) == result.file_sha256), None)
    if sample is None or result.resubmission_of:
        return
    ok, want, reasons = sample_lib.check(sample, result)
    titles = ", ".join(sorted(C.get_rules().reason(code).title for code in reasons)) or "no exception"
    if ok:
        st.caption(f"Sample scenario: {sample['title']}. Expected: {C.screen_words(want)} ({titles}). This is that "
                   f"decision.")
    else:
        st.warning(f"Sample scenario: {sample['title']}. Expected: {C.screen_words(want)} ({titles}). This run differs, which "
                   f"usually means the books were not at their starting state (earlier invoices change what a PO has "
                   f"left) or the AI assist mode is different. 'Start a fresh demo' on the Guided demo page resets both.")


def _empty_state() -> None:
    left, right = st.columns([5, 4], gap="large")
    with left:
        st.markdown("**The document**")
        st.info("Choose a sample or upload a PDF. Samples are safe to run as often as you like.",
                icon=":material/description:")
    with right:
        st.markdown("**How it will be handled**")
        for n, (key, _) in enumerate(STAGES, 1):
            st.markdown(f":gray[{n}. {C.stage_display_title(key)}]  \n:gray[{C.stage_display_help(key)}]")


def card_title(result: RunResult) -> str:
    """The decision card's title is the reason, not the status: the status is the chip above it."""
    d = result.decision
    if result.resubmission_of:
        return "Duplicate file: not booked"
    if d.verdict == "APPROVE":
        return "Everything matches"
    if d.reasons:
        more = f", and {len(d.reasons) - 1} more" if len(d.reasons) > 1 else ""
        return f"{d.reasons[0].title}{more}"
    return d.headline.split(":", 1)[-1].strip()


def decision_card(result: RunResult, key: str) -> None:
    d, inv = result.decision, result.invoice
    with st.container(border=True, key=f"verdict_{key}"):
        if not result.resubmission_of:
            K.status_chip(CARD.get(d.verdict, "hold"), C.verdict_display_label(d.verdict))
        st.markdown(f"### {plain(card_title(result))}")
        if result.resubmission_of:
            st.caption(f"This exact file was already received as {result.resubmission_of}. Nothing was booked again.")
            showing = st.session_state.get("proc_original") == result.run_id
            if st.button(f"{'Hide' if showing else 'Open'} the original record, {result.resubmission_of}",
                         type="primary", key=f"card_original_{key}", icon=":material/content_copy:"):
                st.session_state["proc_original"] = None if showing else result.run_id
                st.rerun()
            return
        facts = [(result.vendor or {}).get("name") or (inv.get("vendor_name") if inv else None),
                 inv.get("invoice_number") if inv else None]
        facts = [plain(f) for f in facts if f] or [plain(C.display_name(result.file_name))]
        if inv is not None and inv.total is not None:
            facts.append(K.inr(float(inv.total)))
        st.markdown(" · ".join(facts))
        if d.verdict == "APPROVE" and not result.resubmission_of:
            st.markdown("Passed every check. Nobody had to touch it.")
        elif d.verdict == "REJECT":
            st.markdown("Nothing was booked for payment." +
                        (" The message to the sender is drafted and ready." if d.vendor_draft else ""))
        if d.owners and d.verdict == "HOLD":
            st.markdown(" ".join(f":gray-badge[:material/assignment_ind: Waiting on {plain(o)}]" for o in d.owners))
        if d.next_action:
            st.markdown(f"**Next:** {plain(C.screen_words(d.next_action))}")
        if d.verdict == "HOLD":
            if st.button("Open the case", type="primary", key=f"card_case_{key}"):
                st.session_state["goto"] = "cases"
                st.rerun()
        elif d.verdict == "APPROVE":
            if st.button("See it in the history", key=f"card_history_{key}"):
                st.session_state["goto"], st.session_state["dash_run"] = "dashboard", result.run_id
                st.rerun()
        elif d.vendor_draft:
            with st.popover("Draft a reply", icon=":material/edit_note:"):
                st.code(d.vendor_draft, language=None, wrap_lines=True)


UNITS = re.compile(r"(\d+(?:\.\d+)?) Nos\b")
VENDOR_CODES = {"VENDOR_NOT_APPROVED", "VENDOR_MATCHED_BY_NAME", "MISSING_VENDOR_ID", "GSTIN_INVALID",
                "VENDOR_NAME_VARIANT", "VENDOR_BLOCKED"}
PO_CODES = {"PO_NOT_FOUND", "NO_PO_FOUND", "PO_CLOSED", "PO_VENDOR_MISMATCH", "LINE_NOT_ON_PO", "PO_INFERRED",
            "OVER_PO_BALANCE"}
PRICE_CODES = {"PRICE_VARIANCE", "PRICE_WITHIN_TOLERANCE"}
QTY_CODES = {"QTY_OVER_PO", "GOODS_NOT_RECEIVED"}
TAX_CODES = {"TAX_RATE_UNUSUAL", "TAX_TYPE_MISMATCH", "BUYER_GSTIN_MISSING"}
DUP_CODES = {"DUPLICATE_FILE", "DUPLICATE_INVOICE_NUMBER", "DUPLICATE_CONTENT", "POSSIBLE_DUPLICATE"}
BANK_CODES = {"BANK_DETAILS_CHANGED"}
PO_IDENTITY = PO_CODES - {"OVER_PO_BALANCE"}


def same_value(a, b) -> bool:
    """The two sides of a row name the same thing: same text, ignoring case, spaces and escaping."""
    def norm(x) -> str:
        return "".join(str(x or "").replace("\\", "").split()).casefold()
    return bool(norm(a)) and norm(a) not in ("-",) and norm(a) == norm(b)


def tidy(text) -> str:
    """Engine wording for a table cell a finance person reads: '5 Nos' is '5 units', 'per Nos' is 'per unit'."""
    out = C.screen_words(str(text or ""))
    out = out.replace("per Nos", "per unit")
    out = UNITS.sub(lambda m: f"{m.group(1)} unit{'' if m.group(1) in ('1', '1.0') else 's'}", out)
    return out


def _finding_cells(f) -> tuple[str, str]:
    """(on the invoice, in our records) for one finding, in real values."""
    observed, expected = tidy(f.observed), tidy(f.expected)
    if f.code == "GOODS_NOT_RECEIVED":
        got = (f.expected or "").removeprefix("billed quantity up to ").strip()
        observed = f"{observed} billed" if observed and observed[0].isdigit() else observed
        expected = "no goods receipt yet" if got in ("0", "0.00", "") else f"{got} received"
    return plain(observed) or "-", plain(expected) or "-"


def evidence_rows(result: RunResult) -> list[dict]:
    """What was checked: one row for every check that ran (vendor, PO, prices, quantities, tax, duplicates,
    bank details), the value on the invoice, the value on record, and the page or line it was read from.
    Every row comes from the stored record."""
    inv, rows = result.invoice, []
    ran = {stage.key for stage in result.stages if stage.status != "skipped"}
    findings = [f for stage in result.stages for f in stage.findings]

    def where(name: str) -> str:
        f = inv.fields.get(name) if inv else None
        if f is None or f.source is None or not f.present:
            return "Not on the page"
        quote = f': "{plain(f.source.snippet)}"' if f.source.snippet else ""
        return f"Page {f.source.page}{quote}"

    def line_where(f) -> str:
        items = inv.line_items if inv else []
        number = f.evidence.get("po_line") if f is not None else None
        item = next((i for i in items if number is not None and str(i.line_no) == str(number)), None)
        if item is not None:
            page = item.source.page if item.source else 1
            return f"Page {page}, line {item.line_no}: {plain(item.description)}"
        if items:
            page = items[0].source.page if items[0].source else 1
            return f"Page {page}, the item lines ({T.plural(len(items), 'line')})"
        return "The item lines"

    def finding_row(label: str, f, source: str) -> dict:
        on_invoice, expected = _finding_cells(f)
        return {"field": label, "on_invoice": on_invoice, "expected": expected, "source": source,
                "ok": f.action == "NOTE"}

    def group(codes: set) -> list:
        return [f for f in findings if f.code in codes]

    used: set[int] = set()
    vendor = result.vendor or {}
    if "vendor" in ran and inv is not None:
        found = [f for f in group(VENDOR_CODES) if f.action != "NOTE"]
        rows.append({"field": "Vendor", "on_invoice": plain(inv.get("vendor_name")) or "-",
                     "expected": plain(vendor.get("name") if vendor.get("vendor_id") else "not in our records"),
                     "source": where("vendor_name"), "ok": bool(vendor.get("vendor_id")) and not found})
        used |= {id(f) for f in group(VENDOR_CODES)}
    po = result.po or {}
    if "po_match" in ran and inv is not None:
        quoted = inv.get("po_reference")
        found = [f for f in group(PO_CODES) if f.action != "NOTE"]
        identity = [f for f in found if f.code in PO_IDENTITY]      # a PO balance problem has its own row
        rows.append({"field": "Purchase order", "on_invoice": plain(quoted) if quoted else "No PO quoted",
                     "expected": plain(po.get("po_number")) or "No matching PO", "source": where("po_reference"),
                     "ok": bool(po.get("po_number")) and not identity and
                     (not quoted or same_value(quoted, po.get("po_number")))})
        for f in found:
            if f.expected or f.observed:
                rows.append(finding_row(f.title or "Purchase order", f,
                                        where("total") if f.code == "OVER_PO_BALANCE" else where("po_reference")))
        used |= {id(f) for f in group(PO_CODES)}
        prices = group(PRICE_CODES)
        for f in prices:
            rows.append(finding_row("Price", f, line_where(f)))
        if not prices and po.get("po_number"):
            rows.append({"field": "Prices", "on_invoice": f"{T.plural(len(inv.line_items), 'line')}",
                         "expected": "at the PO price", "source": line_where(None), "ok": True})
        qty = group(QTY_CODES)
        for f in qty:
            rows.append(finding_row("Quantity" if f.code == "QTY_OVER_PO" else "Goods received", f, line_where(f)))
        if not qty and po.get("po_number"):
            rows.append({"field": "Quantities", "on_invoice": f"{T.plural(len(inv.line_items), 'line')}",
                         "expected": "within the PO and the goods received", "source": line_where(None), "ok": True})
        used |= {id(f) for f in prices + qty}
    if "validate" in ran and inv is not None:
        tax = group(TAX_CODES)
        for f in tax:
            rows.append(finding_row("Tax (GST)", f, where("tax_total") if inv.fields.get("tax_total") else where("total")))
        if not tax:
            rows.append({"field": "Tax (GST)", "on_invoice": plain(T.fmt_inr(inv.get("tax_total"))) if inv.get("tax_total") else "as charged",
                         "expected": "right rate and type for the two GSTINs", "source": where("vendor_gstin"), "ok": True})
        used |= {id(f) for f in tax}
    if "duplicates" in ran:
        dup = group(DUP_CODES)
        for f in dup:
            original = f.evidence.get("original_invoice_no") or f.evidence.get("original_run_id") or "an earlier invoice"
            rows.append(finding_row("Duplicates", f, f"Compared with {plain(original)} in the books"))
        if not dup:
            rows.append({"field": "Duplicates", "on_invoice": plain(inv.get("invoice_number")) if inv and inv.get("invoice_number") else "-",
                         "expected": "no earlier invoice like this", "source": "Compared with every invoice in the books",
                         "ok": True})
        used |= {id(f) for f in dup}
    if "vendor" in ran and inv is not None:
        bank = group(BANK_CODES)
        for f in bank:
            account = str(f.evidence.get("invoice_account") or "")
            on_file = str(f.evidence.get("master_account") or "")
            rows.append({"field": "Bank details", "on_invoice": f"Account ending {account[-4:]}" if account else "-",
                         "expected": f"Account ending {on_file[-4:]} on file" if on_file else "-",
                         "source": where("bank_account"), "ok": False})
        if not bank:
            account = str(inv.get("bank_account") or "")
            rows.append({"field": "Bank details", "on_invoice": f"Account ending {account[-4:]}" if account else "No account printed",
                         "expected": "the account on file", "source": where("bank_account"), "ok": True})
        used |= {id(f) for f in bank}
    for row in rows:                                    # equal values on both sides always read as a match
        if not row["ok"] and same_value(row["on_invoice"], row["expected"]):
            row["ok"] = True
    for stage in result.stages:                         # anything else that was found, with its real values
        for f in stage.findings:
            if id(f) in used or f.action == "NOTE" or not (f.expected or f.observed):
                continue
            field = {"MISSING_INVOICE_NUMBER": "invoice_number", "MISSING_TOTAL": "total",
                     "MISSING_INVOICE_DATE": "invoice_date"}.get(f.code, "")
            rows.append(finding_row(f.title or "Check", f, where(field) if field else
                                    ("The whole document" if stage.key in ("read", "extract") else line_where(None))))
    return rows


def finished_view(result: RunResult, store: Store, key: str) -> None:
    """The settled page, drawn from the stored record: never runs the pipeline."""
    beside = st.session_state.get(f"beside_{key}", True)    # the same switch as in the full record
    left, right = st.columns([5, 4], gap="large") if beside else (None, st.container())
    if left is not None:
        with left:
            st.markdown("**The document**")
            C.render_preview(store.load_file(result.file_sha256), result.file_sha256, result, key=f"card_{key}")
    with right:
        decision_card(result, key)
        if result.resubmission_of and st.session_state.get("proc_original") == result.run_id:
            original = store.get_run(result.resubmission_of)
            if original is not None and original.decision is not None:
                st.markdown(f"**The original record, {result.resubmission_of}**")
                C.render_run(original, store, key=f"orig_{key}")
        head = st.container(horizontal=True, vertical_alignment="bottom")
        head.markdown("**How it was handled**")
        head.caption(f"{C.took(result.duration_ms)} in all")
        for stage in result.stages:
            K.stage_card(to_stage(stage, result))
        if pace_seconds() > 0:
            st.caption(f"Shown at reading speed. The real run took {C.took(result.duration_ms)}.")
    st.markdown("**What was checked**")
    rows = evidence_rows(result)
    if rows:
        K.evidence_panel(rows)
    else:
        st.caption("Nothing to compare: the file was refused before its details could be read.")
    st.caption("Every step is done by fixed rules unless it is marked AI. AI never makes the decision.")
    if st.session_state.get("proc_from_demo") == result.run_id:
        if st.button("Back to the guided demo", key=f"back_demo_{key}", icon=":material/arrow_back:"):
            st.session_state["goto"] = "demo"
            st.rerun()


def page() -> None:
    store = C.get_store()
    pipeline = C.get_pipeline(store=store)
    samples = C.manifest()
    st.title("Process an invoice")
    st.caption("PDF in, decision out. Samples and uploads go through the same nine steps; nothing on this page is "
               "worked out in advance.")

    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

    going_live = st.session_state.pop("proc_live", None)    # set by the Process buttons; popped before any layout
    seen = first_runs(store)
    hashes = _sample_hashes()
    content: Optional[bytes] = None
    name, source = "", "upload"
    top = st.columns([0.62, 0.38], vertical_alignment="bottom")
    source_choice = top[0].radio("Where the document comes from", [SAMPLE, UPLOAD], horizontal=True, key="proc_source",
                                 label_visibility="collapsed")
    with top[1]:
        pace_picker("proc_pace")
    if source_choice == SAMPLE:
        by_id = {s["id"]: s for s in samples}

        def label(sample_id: str) -> str:
            """Fixed for each sample. A label that changed after a run (say, to add "processed") made Streamlit
            treat the dropdown as new and fall back to the first sample; whether it was processed is in the caption."""
            s = by_id[sample_id]
            return f"{s['order']:02d}  {s['title']}  [{s.get('tag', 'Sample')}]"

        pick, button = st.columns([0.74, 0.26], vertical_alignment="bottom")
        chosen = pick.selectbox("Document", list(by_id), format_func=label, key="proc_sample")
        go = button.button("Process this invoice", type="primary", width="stretch", key="proc_go")
        sample = by_id[chosen]
        st.caption(C.screen_words(f"{sample['description']} {expected_text(sample, hashes.get(chosen, ''))}"))
        previous = st.session_state.get("proc_prev_sample")
        if previous is not None and previous != chosen and not going_live:   # a new choice clears the last answer
            st.session_state.pop("current_run", None)
            st.session_state.pop("batch", None)
        st.session_state["proc_prev_sample"] = chosen
        done = seen.get(hashes.get(chosen, ""))
        if done and done["run_id"] in st.session_state.get("proc_mine", []) and not going_live:
            st.caption(f"Processed in this session as {done['run_id']}. Sending the same file again is stopped at "
                       f"step 1 as a duplicate and links back to {done['run_id']}.")
        elif done and not going_live:
            st.caption(f"Already received as {done['run_id']}. Sending the same file again is stopped at step 1 as a "
                       f"duplicate. For a clean start use 'Start a fresh demo' on the Guided demo page.")
        if going_live == "sample":
            sample = by_id.get(st.session_state.pop("proc_run_sample", chosen), sample)     # the one shown when pressed
            content, name, source = sample_lib.sample_bytes(sample), sample_name(sample), "sample"
    else:
        upload = st.file_uploader("Invoice PDF (text or scanned). Any file is accepted: one that is not a readable "
                                  "invoice is refused with a reason instead of an error.", key="proc_upload")
        st.caption("This is a demonstration system with a made-up vendor list and PO register. An invoice from a "
                   "vendor that is not on that list is held for exactly that reason. Uploaded files are stored with "
                   "the record on the machine that hosts the app, so do not upload real invoices.")
        go = st.button("Process this file", type="primary", disabled=upload is None, key="proc_go_upload")
        if going_live == "upload" and upload is not None:
            content, name, source = upload.getvalue(), upload.name, "upload"
    if go:                           # trigger from a flag, not from the button: the run starts at the top of a rerun
        st.session_state["proc_live"] = "sample" if source_choice == SAMPLE else "upload"
        if source_choice == SAMPLE:
            st.session_state["proc_run_sample"] = chosen
        st.rerun()

    with st.expander("Process the whole sample inbox in one go"):
        st.write("Puts the books back to their starting state, then processes every sample document in order and "
                 "compares each decision with the expected one. Records, reviews and audit entries made so far in "
                 "this demo are cleared first.")
        batch = st.button(f"Start fresh and process all {len(samples)} samples", key="proc_batch")
    if st.session_state.pop("auto_batch", False):
        batch = True

    area = st.empty()
    if batch:
        run_batch(area, store)
        st.rerun()
    if content is not None:
        st.session_state.pop("batch", None)
        result = run_live(area, pipeline, content, name, source, pace_seconds())
        st.session_state["proc_runs"] = st.session_state.get("proc_runs", 0) + 1
        adopt_for = st.session_state.pop("demo_adopt", None)
        if result is not None:
            st.session_state["current_run"] = result.run_id
            st.session_state.setdefault("proc_mine", []).append(result.run_id)
            if adopt_for:
                from . import demo
                demo.adopt(result, adopt_for)
                st.session_state["proc_from_demo"] = result.run_id
        st.rerun()

    with area.container():
        rows = st.session_state.get("batch")
        if rows:
            good = sum(r["As expected"] == "yes" for r in rows)
            st.markdown(f"**{good} of {len(rows)} documents got the expected decision**")
            st.caption("Select a row to open that record below. The Dashboard has the full history; the Review queue "
                       "has the invoices that are on hold.")
            event = st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", on_select="rerun",
                                 selection_mode="single-row", key="batch_table", height=35 * (len(rows) + 1) + 3)
            picked = (event.selection.rows if event and event.selection else [])
            if picked:
                st.session_state["current_run"] = rows[picked[0]]["Reference"]
        current = st.session_state.get("current_run")
        result = store.get_run(current) if current else None
        if result is not None and result.decision is not None:
            expectation_note(result, store)
            decision_tab, record_tab = st.tabs(["Decision", "Full record"])
            with decision_tab:
                finished_view(result, store, key="proc")
            with record_tab:
                C.render_run(result, store, key="proc")
        elif not rows:
            st.session_state.pop("current_run", None)
            _empty_state()
