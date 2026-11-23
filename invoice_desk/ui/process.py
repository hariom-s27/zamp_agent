"""Page 2, Process invoice: choose or upload a PDF and watch the nine steps run.

Reserve, reset, fill: the result areas are reserved before the run, cleared with one element
the moment a run starts, the steps run live, the result is stored and the page reruns. The
finished view (decision card, step timeline, what was checked) is drawn from the stored
record and never runs the pipeline.
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import streamlit as st

from .. import samples as sample_lib
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
    if kind == "skipped":
        lines = lines or ["This step was not needed for this document."]
    return K.Stage(number=stage.number, title=C.stage_display_title(stage.key),
                   result=kind, summary=plain(C.screen_words(stage.summary)) or C.stage_display_help(stage.key),
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
            rows.append({"Reference": result.run_id, "Scenario": sample["title"], "Expected": want, "Decision": result.verdict,
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
        st.caption(f"Sample scenario: {sample['title']}. Expected decision: {want} ({titles}). This is that decision.")
    else:
        st.warning(f"Sample scenario: {sample['title']}. Expected decision: {want} ({titles}). This run differs, which "
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
        return f"Same file as {result.resubmission_of}, not booked again"
    if d.verdict == "APPROVE":
        return "Everything matches"
    if d.reasons:
        more = f", and {len(d.reasons) - 1} more" if len(d.reasons) > 1 else ""
        return f"{d.reasons[0].title}{more}"
    return d.headline.split(":", 1)[-1].strip()


def decision_card(result: RunResult, key: str) -> None:
    d, inv = result.decision, result.invoice
    with st.container(border=True, key=f"verdict_{key}"):
        K.status_chip(CARD.get(d.verdict, "hold"), C.verdict_display_label(d.verdict))
        st.markdown(f"### {plain(card_title(result))}")
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


def evidence_rows(result: RunResult) -> list[dict]:
    """What was read against what is on record. Every row comes from the stored record."""
    inv, rows = result.invoice, []

    def where(name: str) -> str:
        f = inv.fields.get(name) if inv else None
        if f is None or f.source is None:
            return "-"
        quote = f': "{plain(f.source.snippet)}"' if f.source.snippet else ""
        return f"Page {f.source.page}{quote}"

    vendor = result.vendor or {}
    if inv is not None and inv.get("vendor_name"):
        rows.append({"field": "Vendor", "on_invoice": plain(inv.get("vendor_name")),
                     "expected": plain(vendor.get("name") if vendor.get("vendor_id") else "not in our records"),
                     "source": where("vendor_name"), "ok": bool(vendor.get("vendor_id"))})
    po = result.po or {}
    if po.get("po_number"):
        rows.append({"field": "Purchase order", "on_invoice": plain(inv.get("po_number") if inv else None) or "-",
                     "expected": plain(po["po_number"]), "source": where("po_number"), "ok": True})
    for stage in result.stages:
        for f in stage.findings:
            if f.expected or f.observed:
                rows.append({"field": plain(f.title or f.code), "on_invoice": plain(f.observed) or "-",
                             "expected": plain(f.expected) or "-",
                             "source": C.stage_display_title(stage.key), "ok": f.action == "NOTE"})
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


def page() -> None:
    store = C.get_store()
    pipeline = C.get_pipeline(store=store)
    samples = C.manifest()
    st.title("Process an invoice")
    st.caption("PDF in, decision out. Samples and uploads run through the same pipeline; nothing on this page is "
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
            s = by_id[sample_id]
            done = seen.get(hashes.get(sample_id, ""))
            return (f"{s['order']:02d}  {s['title']}  [{s.get('tag', 'Sample')}]" +
                    (f"   (processed: {done['run_id']} {done['verdict']})" if done else ""))

        pick, button = st.columns([0.74, 0.26], vertical_alignment="bottom")
        chosen = pick.selectbox("Document", list(by_id), format_func=label, key="proc_sample")
        go = button.button("Process this invoice", type="primary", width="stretch", key="proc_go")
        sample = by_id[chosen]
        st.caption(f"{sample['description']} {expected_text(sample, hashes.get(chosen, ''))}")
        done = seen.get(hashes.get(chosen, ""))
        if done:
            st.caption(f"Already received as {done['run_id']}. Sending the same file again returns that record and books "
                       f"nothing twice. For a clean start use 'Start a fresh demo' on the Guided demo page.")
        if going_live == "sample":
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
        if result is not None:
            st.session_state["current_run"] = result.run_id
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
