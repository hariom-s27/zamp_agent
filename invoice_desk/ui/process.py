"""Page 2, Process invoice: choose or upload a PDF and watch the nine stages run.

While a run is in progress the stage rows update one by one (waiting, running, done,
with each stage's summary, timing and findings). When it finishes the page shows the
stored record, which is the same view the Dashboard opens later.
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import streamlit as st

from .. import samples as sample_lib
from ..models import RunResult
from ..pipeline import STAGE_HELP, STAGES, Pipeline
from ..reader import sha256_of
from ..store import Store
from . import common as C

SAMPLE, UPLOAD = "From the sample inbox", "Upload a file"
PACE = {"Presentation": 0.9, "Normal": 0.4, "Fast": 0.0}       # seconds each stage stays on screen while it runs


def pace_seconds() -> float:
    return PACE.get(st.session_state.get("pace", "Normal"), 0.4)


def pace_picker(key: str) -> None:
    """The pace is kept in the session under 'pace', apart from the widget, so it survives a
    visit to a page that does not show the control."""
    current = st.session_state.get("pace", "Normal")
    chosen = st.segmented_control("Pace of the live view", list(PACE), default=current, key=key,
                                  help="How long each stage stays highlighted while it runs. The stages themselves "
                                       "finish in a fraction of a second; this only slows the display so an audience "
                                       "can follow it. The times shown are the real processing times.")
    if chosen and chosen != current:
        st.session_state["pace"] = chosen


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
    """Run the real pipeline and draw each stage as it starts and as it finishes."""
    states: dict[str, str] = {}
    result: Optional[RunResult] = None
    live: Optional[RunResult] = None
    sha = sha256_of(content)
    with area.container():
        banner, rail = st.empty(), st.empty()
        left, right = st.columns([0.58, 0.42], gap="large")
        with left:
            st.markdown("**The nine stages, live**")
            slots = {key: st.empty() for key, _ in STAGES}
        with right:
            st.markdown("**The document**")
            preview = st.empty()
        banner.markdown(C.neutral_banner_html("Processing", name, "Registering the file."), unsafe_allow_html=True)
        rail.markdown(C.rail_html(states), unsafe_allow_html=True)
        for number, (key, title) in enumerate(STAGES, 1):
            slots[key].markdown(C.stage_row_html(number, title, "pending", STAGE_HELP[key]), unsafe_allow_html=True)
        with preview.container():
            C.render_preview(content, sha, None, key="live_before")
        for kind, payload in pipeline.iter_run(content, name, source):
            if kind == "begin":
                live = payload
                banner.markdown(C.neutral_banner_html(f"{payload.run_id} | processing", name,
                                                      "Each stage below runs in order. Nothing is decided until stage 8."),
                                unsafe_allow_html=True)
            elif kind == "start":
                number = [k for k, _ in STAGES].index(payload) + 1
                states[payload] = "running"
                rail.markdown(C.rail_html(states), unsafe_allow_html=True)
                slots[payload].markdown(C.stage_row_html(number, dict(STAGES)[payload], "running", STAGE_HELP[payload]),
                                        unsafe_allow_html=True)
                if pause > 0:
                    time.sleep(pause)
            elif kind == "stage":
                states[payload.key] = payload.status
                rail.markdown(C.rail_html(states), unsafe_allow_html=True)
                slots[payload.key].markdown(
                    C.stage_row_html(payload.number, payload.title, payload.status, payload.summary,
                                     payload.duration_ms, payload.findings), unsafe_allow_html=True)
                if payload.key == "extract" and live is not None and live.invoice is not None:
                    with preview.container():       # show where each value was read, as soon as it is known
                        C.render_preview(content, sha, live, key="live_after")
            elif kind == "result":
                result = payload
        if result is not None:
            banner.markdown(C.verdict_banner_html(result), unsafe_allow_html=True)
            if pause > 0:
                time.sleep(min(pause, 0.6))
    return result


def run_batch(area, store: Store) -> None:
    """Start from the seeded books, then process every bundled sample in its documented order."""
    C.reset_books(store)
    pipeline = C.get_pipeline(store=store)
    samples = C.manifest()
    rows: list[dict] = []
    with area.container():
        st.markdown(C.neutral_banner_html("Sample inbox", f"Processing all {len(samples)} sample documents in order",
                                          "The books were put back to their starting state first, so every document "
                                          "meets the situation it was written for."), unsafe_allow_html=True)
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
    st.markdown(C.neutral_banner_html("Ready", "Choose a document from the sample inbox or upload a PDF, then press Process",
                                      "Every stage is shown as it runs. The decision comes with its reasons, the "
                                      "evidence behind each one, the person who has to act, and an audit entry."),
                unsafe_allow_html=True)
    st.markdown(C.rail_html({}), unsafe_allow_html=True)
    C.table({"Stage": f"{n} {title}", "What it does": STAGE_HELP[key]} for n, (key, title) in enumerate(STAGES, 1))


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
        if go:
            content, name, source = sample_lib.sample_bytes(sample), sample_name(sample), "sample"
    else:
        upload = st.file_uploader("Invoice PDF (text or scanned). Any file is accepted: one that is not a readable "
                                  "invoice is refused with a reason instead of an error.", key="proc_upload")
        st.caption("This is a demonstration system with a made-up vendor list and PO register. An invoice from a "
                   "vendor that is not on that list is held for exactly that reason. Uploaded files are stored with "
                   "the record on the machine that hosts the app, so do not upload real invoices.")
        go = st.button("Process this file", type="primary", disabled=upload is None, key="proc_go_upload")
        if go and upload is not None:
            content, name, source = upload.getvalue(), upload.name, "upload"

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
    if go and content is not None:
        st.session_state.pop("batch", None)
        result = run_live(area, pipeline, content, name, source, pace_seconds())
        if result is not None:
            st.session_state["current_run"] = result.run_id
        st.rerun()

    with area.container():
        rows = st.session_state.get("batch")
        if rows:
            good = sum(r["As expected"] == "yes" for r in rows)
            st.markdown(C.neutral_banner_html("Sample inbox processed",
                                              f"{good} of {len(rows)} documents got the expected decision",
                                              "Select a row to open that record below. The Dashboard has the full "
                                              "history; the Review queue has the invoices that are on hold."),
                        unsafe_allow_html=True)
            event = st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", on_select="rerun",
                                 selection_mode="single-row", key="batch_table", height=35 * (len(rows) + 1) + 3)
            picked = (event.selection.rows if event and event.selection else [])
            if picked:
                st.session_state["current_run"] = rows[picked[0]]["Reference"]
        current = st.session_state.get("current_run")
        result = store.get_run(current) if current else None
        if result is not None and result.decision is not None:
            expectation_note(result, store)
            C.render_run(result, store, key="proc")
        elif not rows:
            st.session_state.pop("current_run", None)
            _empty_state()
