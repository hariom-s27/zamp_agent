"""Page 1, Guided demo: the process shown one scenario at a time.

One button runs the next scenario through the real pipeline with the live stage view,
slowed down enough for an audience to follow. The scenarios go from a clean invoice
through each edge case, the fraud control and the AI assist to bad input. Nothing here is
staged: every step is an ordinary run, stored like any other, and the page compares its
decision with the expected one after the fact.

The demo always starts from the same books ("Start a fresh demo"), and on a host that
gives each visitor a private sandbox it cannot be disturbed by anyone else.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from .. import config, demo_steps
from .. import samples as sample_lib
from ..reader import sha256_of
from . import common as C
from . import process

STEPS = demo_steps.STEPS


def _samples() -> dict[str, dict]:
    return {s["id"]: s for s in C.manifest()}


def step_outcome(step: dict, result, done: dict) -> tuple[bool, str]:
    """(as expected, the expectation in words) for one finished step."""
    return demo_steps.outcome(step, result, done, C.get_rules(), C.manifest())


def run_step(area, step: dict, store) -> Optional[dict]:
    """Run one scenario through the pipeline with the live view, and note how it ended."""
    sample = _samples()[step["sample"]]
    pipeline = C.get_pipeline(store=store)
    result = process.run_live(area, pipeline, sample_lib.sample_bytes(sample), process.sample_name(sample), "sample",
                              process.pace_seconds())
    if result is None:
        return None
    done = st.session_state.setdefault("demo_done", {})
    ok, want = step_outcome(step, result, done)
    done[step["key"]] = {"run_id": result.run_id, "verdict": result.verdict, "ok": ok, "want": want}
    st.session_state["demo_last"] = step["key"]
    st.session_state["current_run"] = result.run_id
    return done[step["key"]]


def rail_html(done: dict, nxt: Optional[dict]) -> str:
    """The eleven scenarios as one row of chips, coloured by the decision each one got."""
    chips = []
    for number, step in enumerate(STEPS, 1):
        state = done.get(step["key"])
        if state:
            cls = {"APPROVE": "st-ok", "HOLD": "st-attention", "REJECT": "st-blocked"}[state["verdict"]]
            word = C.VERDICT[state["verdict"]][0].lower() + (", as expected" if state["ok"] else ", NOT expected")
        elif step is nxt:
            cls, word = "st-running", "next"
        else:
            cls, word = "st-pending", "waiting"
        chips.append(f"<div class='idk-chip {cls}'><span class='n'>{number}</span>{C.esc(step['short'])}"
                     f"<span class='s'>{C.esc(word)}</span></div>")
    return "<div class='idk-rail wide'>" + "".join(chips) + "</div>"


def _step_html(number: int, step: dict, state: Optional[dict], is_next: bool) -> str:
    sample = _samples()[step["sample"]]
    if state:
        label, colour, background, border = C.VERDICT[state["verdict"]]
        mark = "as expected" if state["ok"] else "NOT as expected"
        out = (f"<span style='color:{colour}'>{C.esc(state['run_id'])} &nbsp; {C.esc(label)}</span>"
               f"<br><span class='idk-small'>{C.esc(mark)}</span>")
        style = f"background:{background};border-color:{border}"
    else:
        out, style = ("<span class='idk-small'>next</span>" if is_next else ""), ""
    return (f"<div class='idk-step{' next' if is_next else ''}' style='{style}'><div class='num'>{number}</div>"
            f"<div class='body'><span class='title'>{C.esc(step['title'])}</span>"
            f"<span class='idk-tag'>{C.esc(sample.get('tag', ''))}</span>"
            f"<div class='watch'>{C.esc(step['watch'])}</div></div><div class='out'>{out}</div></div>")


def _settings(store) -> None:
    """Demo and admin controls: the things a presenter sets, kept away from the work pages."""
    with st.expander("Demo settings and admin"):
        left, right = st.columns(2, gap="large")
        with left:
            modes = C.ai_modes()
            chosen = st.selectbox("AI assist mode", modes, index=modes.index(C.ai_mode()), key="ai_mode_pick",
                                  format_func=lambda m: {"fixture": "FIXTURE: replay recorded answers (no network, no key)",
                                                         "cached-live": "CACHED-LIVE: call Claude on a cache miss, then store the answer",
                                                         "off": "OFF: rules only"}[m])
            if chosen != C.ai_mode():          # kept in the session apart from the widget, so every page uses it
                st.session_state["ai_mode"] = chosen
                st.rerun()
            cfg = C.get_llm().cfg
            st.caption(cfg.explanation)
            if "cached-live" not in modes:
                st.caption("Live calls are not offered here: " +
                           ("no API key is configured." if not config.env("ANTHROPIC_API_KEY") else
                            "this is a shared demo and LLM_ALLOW_LIVE is not set."))
            else:
                from ..llm import SpendLedger
                ledger = SpendLedger()
                st.caption(f"Cost caps: ${cfg.per_invoice_cap_usd:.2f} per invoice, ${cfg.daily_cap_usd:.2f} per day. "
                           f"Spent today: ${ledger.spent_today():.4f} in {ledger.calls_today()} call(s). When a cap "
                           f"is reached the document is handled by the rules alone.")
                if C.ai_mode() == "cached-live":
                    fresh = st.toggle("Ask Claude afresh on every run", value=bool(st.session_state.get("ai_fresh")),
                                      key="ai_fresh_pick",
                                      help="For showing a live call. Normally an answer that is already stored is "
                                           "reused and nothing is sent. With this on, the document is sent to Claude "
                                           "again on each run, inside the caps. If the call cannot be made, the "
                                           "stored answer is used and the record says so. The recordings in "
                                           "fixtures/llm are never changed from here.")
                    if fresh != bool(st.session_state.get("ai_fresh")):
                        st.session_state["ai_fresh"] = fresh
                        st.rerun()
                    if fresh:
                        st.caption("Each run now makes a live call. Press 'Start a fresh demo' first: a file that is "
                                   "already on record is not read a second time.")
        with right:
            pinned = bool(config.env("INVOICE_DESK_TODAY"))
            on = st.toggle("Process on the demo date (21-Nov-2026)", value=st.session_state.get("demo_clock", True),
                           key="demo_clock_pick", disabled=pinned,
                           help="The samples are dated around this day. With the switch off, invoices are processed "
                                "on today's real date, and the dated checks (older than a year, dated in the future) "
                                "may answer differently.")
            if on != st.session_state.get("demo_clock", True):
                st.session_state["demo_clock"] = on
                st.rerun()
            st.caption(f"Business date in force: {C.business_date():%d-%b-%Y}" + (" (set by INVOICE_DESK_TODAY)." if pinned else "."))
            books = ("Your books are private to this browser session." if config.sandbox_mode() == "session" else
                     "The books are shared by everyone using this installation.")
            st.caption(books + " 'Start a fresh demo' puts them back to the starting state: no records, no reviews, "
                               "and the ten invoices that were posted before this tool existed.")
            if st.button("Reset the books now", key="demo_reset", icon=":material/restart_alt:"):
                C.reset_books(store)
                st.session_state["flash_demo"] = "The books are back at their starting state."
                st.rerun()


def _try_your_own(area, store) -> None:
    st.subheader("Try your own invoice")
    st.write("Upload any PDF. It goes through the same nine stages, live. This demo company has a made-up vendor list "
             "and PO register, so a real invoice from a vendor it has never heard of is held for exactly that reason "
             "(vendor not on the approved list). That is the control working, and you can see what was read from "
             "your document in stage 3.")
    upload = st.file_uploader("Invoice PDF, text or scanned (do not upload confidential documents: the file is stored "
                              "with its record on the machine hosting this demo)", key="demo_upload")
    if st.button("Process my file", type="primary", disabled=upload is None, key="demo_upload_go") and upload is not None:
        result = process.run_live(area, C.get_pipeline(store=store), upload.getvalue(), upload.name, "upload",
                                  process.pace_seconds())
        if result is not None:
            st.session_state["current_run"], st.session_state["demo_last"] = result.run_id, "upload"
        st.rerun()


def page() -> None:
    store = C.get_store()
    st.title("Guided demo")
    st.caption("Eleven scenarios, one button each: a clean invoice, each edge case, the fraud control, the AI assist "
               "and bad input. Every step is a real run through the pipeline, stored and auditable like any other.")
    flash = st.session_state.pop("flash_demo", None)
    if flash:
        st.success(flash)

    done: dict = st.session_state.setdefault("demo_done", {})
    pending = [s for s in STEPS if s["key"] not in done]
    nxt = pending[0] if pending else None
    stale = bool(store.list_runs()) and not done          # records exist that this demo did not make

    bar = st.columns([0.2, 0.3, 0.2, 0.3], vertical_alignment="bottom")
    fresh = bar[0].button("Start a fresh demo", icon=":material/restart_alt:", width="stretch", key="demo_fresh",
                          type="primary" if (stale or not pending) else "secondary")
    go = bar[1].button(f"Run step {len(done) + 1}: {nxt['title'][:34]}" if nxt else "All steps done", type="primary",
                       disabled=nxt is None or stale, width="stretch", key="demo_next", icon=":material/play_arrow:")
    run_all = bar[2].button("Run all remaining", disabled=nxt is None or stale, width="stretch", key="demo_all",
                            icon=":material/fast_forward:")
    with bar[3]:
        process.pace_picker("demo_pace")
    if stale:
        st.warning("These books already hold records from outside the guided demo, so the scenarios would not meet "
                   "their starting state. Press 'Start a fresh demo' first.")
    if fresh:
        C.reset_books(store)
        st.session_state["flash_demo"] = "Fresh demo: the books are at their starting state and no step has run yet."
        st.rerun()
    if run_all:
        st.session_state["demo_auto"] = True

    st.progress(len(done) / len(STEPS), text=f"{len(done)} of {len(STEPS)} steps run" +
                (f", {sum(1 for d in done.values() if d['ok'])} as expected" if done else ""))
    area = st.empty()
    auto = st.session_state.get("demo_auto", False)
    if nxt is not None and not stale and (go or auto):
        outcome = run_step(area, nxt, store)
        if outcome is None or len(st.session_state["demo_done"]) >= len(STEPS):
            st.session_state["demo_auto"] = False
        st.rerun()
    if auto and nxt is None:
        st.session_state["demo_auto"] = False

    st.markdown(rail_html(done, None if stale else nxt), unsafe_allow_html=True)
    last = st.session_state.get("demo_last")
    step = next((s for s in STEPS if s["key"] == last), None)
    current = st.session_state.get("current_run")
    result = store.get_run(current) if current else None
    left, right = st.columns(2, gap="large")
    with left:
        if step is not None and step["key"] in done:
            state = done[step["key"]]
            st.markdown(f"**Step {STEPS.index(step) + 1}: {C.md_safe(step['title'])}**")
            (st.success if state["ok"] else st.error)(
                f"Decision {state['verdict']}. Expected: {state['want']}. " +
                ("This is the expected decision." if state["ok"] else "This is NOT the expected decision; start a fresh demo."))
            st.markdown(f"<div class='idk-text'><b>What to notice.</b> {C.esc(step['watch'])}</div>", unsafe_allow_html=True)
            if step.get("follow") == "review":
                if st.button("Open the review queue to act on this hold", key=f"demo_follow_{step['key']}",
                             icon=":material/fact_check:"):
                    st.session_state["goto"] = "review"
                    st.rerun()
        elif last == "upload" and result is not None:
            st.markdown("**Your file**")
        else:
            st.markdown(C.neutral_banner_html("Ready", "Press 'Run step 1' to begin",
                                              "Each step shows the nine stages running, then the decision with its "
                                              "reasons, the evidence, who has to act, and the audit entry."),
                        unsafe_allow_html=True)
    with right:
        if nxt is not None and not stale:
            st.markdown(f"**Next, step {STEPS.index(nxt) + 1}: {C.md_safe(nxt['title'])}**")
            st.markdown(f"<div class='idk-text'>{C.esc(nxt['watch'])}</div>", unsafe_allow_html=True)
        elif nxt is None:
            st.markdown("**All eleven scenarios have run.**")
            st.markdown("<div class='idk-text'>The Review queue holds the invoices waiting for a person, and the "
                        "Dashboard shows the history and re-checks the audit trail. Both holds from the demo can be "
                        "acted on there: the split PO (approval is refused, with the reason) and the bank change "
                        "(two different approvers).</div>", unsafe_allow_html=True)
        letter = _samples().get("10_ai_assist_letter_invoice")
        if letter and C.ai_mode() == "fixture" and not C.get_llm().has_recording(sha256_of(sample_lib.sample_bytes(letter))):
            st.caption("About step 9: no recorded Claude answer for the letter invoice is in fixtures/llm yet, so in "
                       "FIXTURE mode the rules alone decide it (a hold, the safe outcome). Record one with "
                       "`python -m scripts.record_llm_fixtures` and your API key; see the README.")
    if result is not None and result.decision is not None and (last == "upload" or (step is not None and step["key"] in done)):
        C.render_run(result, store, key="demo")
    with st.expander("All eleven scenarios and what each one shows", expanded=not done and not stale):
        st.markdown("".join(_step_html(n, each, done.get(each["key"]), each is nxt and not stale)
                            for n, each in enumerate(STEPS, 1)), unsafe_allow_html=True)

    st.divider()
    _try_your_own(area, store)
    _settings(store)
