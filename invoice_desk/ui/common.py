"""Shared pieces of the Streamlit UI: data access, formatting and the renderers that the
Process page, the Dashboard and the Review queue all use, so a run looks the same
wherever it is shown.

Text that comes out of a document (vendor names, descriptions, file names) is never
passed to markdown or HTML unescaped: an uploaded PDF is untrusted input.
"""
from __future__ import annotations

import html
import io
import json
import re
import secrets
from datetime import date
from typing import Any, Iterable, Optional

import pandas as pd
import streamlit as st

from .. import config, reader
from .. import samples as sample_lib
from .. import textutil as T
from ..llm import LlmAssist
from ..masterdata import MasterData, load_master
from ..models import Finding, RunResult, StageResult
from ..pipeline import STAGE_HELP, STAGES, Pipeline
from ..rules import Rules, load_rules
from ..store import Store

APP_NAME = "Invoice Desk"
VERDICT = {  # verdict -> (label, text colour, background, border)
    "APPROVE": ("Approved", "#0b5d36", "#e6f4ec", "#1a8a52"),
    "HOLD": ("On hold", "#7a4d00", "#fff3d1", "#d99a00"),
    "REJECT": ("Rejected", "#8f1d24", "#fdeaea", "#c9343d"),
}
STATUS = {  # stage status -> (symbol, label, css class)
    "pending": ("", "waiting", "st-pending"),
    "running": ("", "running", "st-running"),
    "ok": ("\u2713", "passed", "st-ok"),
    "attention": ("!", "hold raised", "st-attention"),
    "blocked": ("\u2715", "reject raised", "st-blocked"),
    "skipped": ("\u2013", "skipped", "st-skipped"),
    "error": ("\u2715", "error, held", "st-blocked"),
}
DECIDE_LABEL = {"ok": "approve", "attention": "hold", "blocked": "reject"}
ACTION_CLASS = {"REJECT": "f-reject", "HOLD": "f-hold", "NOTE": "f-note"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px;}
.idk-banner {border-radius: 10px; padding: 16px 20px; margin: 4px 0 14px 0; border-left: 8px solid;}
.idk-banner .tag {font-size: 0.8rem; letter-spacing: .03em; font-weight: 700; opacity: .85;}
.idk-banner .headline {font-size: 1.45rem; font-weight: 700; line-height: 1.25; margin: 2px 0 8px 0;}
.idk-banner .why {font-size: 0.98rem; line-height: 1.5;}
.idk-banner .next {font-size: 0.9rem; margin-top: 8px; opacity: .9;}
.idk-neutral {background: #f2f4f7; border-color: #7b8794; color: #27313c;}
.idk-rail {display: flex; gap: 6px; margin: 6px 0 14px 0; flex-wrap: wrap;}
.idk-chip {flex: 1 1 0; min-width: 96px; border-radius: 8px; padding: 7px 9px; border: 1px solid #d5dae1;
           background: #fff; font-size: 0.8rem; line-height: 1.2;}
.idk-chip .n {font-weight: 700; margin-right: 4px;}
.idk-chip .s {display: block; font-size: 0.72rem; opacity: .8; margin-top: 2px;}
.st-pending {color: #7b8794; background: #f6f7f9;}
.st-running {color: #0b4f9e; background: #e7f0fb; border-color: #5b9be6 !important; box-shadow: 0 0 0 2px #cfe2fa;}
.st-ok {color: #0b5d36; background: #e6f4ec; border-color: #9bd0b3 !important;}
.st-attention {color: #7a4d00; background: #fff3d1; border-color: #e3bd5b !important;}
.st-blocked {color: #8f1d24; background: #fdeaea; border-color: #e3a0a5 !important;}
.st-skipped {color: #7b8794; background: #f6f7f9; border-style: dashed !important;}
.idk-row {display: flex; align-items: flex-start; gap: 12px; border: 1px solid #d5dae1; border-radius: 8px;
          padding: 9px 12px; margin-bottom: 6px;}
.idk-row .num {flex: 0 0 26px; height: 26px; border-radius: 13px; text-align: center; line-height: 26px;
               font-weight: 700; font-size: 0.85rem; background: rgba(0,0,0,.08);}
.idk-row .body {flex: 1 1 auto; min-width: 0;}
.idk-row .title {font-weight: 700;}
.idk-row .sum {font-size: 0.9rem; overflow-wrap: anywhere;}
.idk-row .ms {flex: 0 0 auto; font-size: 0.8rem; opacity: .75; white-space: nowrap; padding-top: 3px;}
.idk-spin {display: inline-block; width: 12px; height: 12px; border: 2px solid #5b9be6; border-top-color: transparent;
           border-radius: 50%; animation: idkspin .7s linear infinite; vertical-align: -1px; margin-right: 6px;}
@keyframes idkspin {to {transform: rotate(360deg);}}
.idk-find {border-left: 5px solid; border-radius: 6px; padding: 8px 12px; margin: 6px 0; font-size: 0.92rem; line-height: 1.45;}
.idk-find .t {font-weight: 700;}
.idk-find .meta {font-size: 0.82rem; opacity: .85; margin-top: 3px;}
.f-reject {background: #fdeaea; border-color: #c9343d; color: #5c1217;}
.f-hold {background: #fff3d1; border-color: #d99a00; color: #5a3900;}
.f-note {background: #eaf2fb; border-color: #5b9be6; color: #12365f;}
.idk-text {font-size: 0.92rem; line-height: 1.5; margin: 2px 0 10px 0; overflow-wrap: anywhere;}
.idk-small {font-size: 0.82rem; color: #5b6673;}
.idk-pill {display: inline-block; border-radius: 10px; padding: 1px 9px; font-size: 0.75rem; font-weight: 700;
           border: 1px solid; margin-right: 6px;}
.idk-mode {font-size: 0.83rem; line-height: 1.55;}
.idk-mode b {display: inline-block; min-width: 92px; font-weight: 600; color: #5b6673;}
.idk-status {border: 1px solid #d5dae1; border-radius: 8px; padding: 8px 10px; font-size: 0.84rem; line-height: 1.5;
             background: #f6f7f9; margin: 4px 0 8px 0;}
.idk-status .dot {display: inline-block; width: 8px; height: 8px; border-radius: 4px; background: #1a8a52; margin-right: 6px;}
.idk-step {border: 1px solid #d5dae1; border-radius: 8px; padding: 9px 12px; margin-bottom: 6px; display: flex; gap: 12px;
           align-items: flex-start;}
.idk-step .num {flex: 0 0 26px; height: 26px; border-radius: 13px; text-align: center; line-height: 26px; font-weight: 700;
                font-size: 0.85rem; background: rgba(0,0,0,.08);}
.idk-step .body {flex: 1 1 auto; min-width: 0;}
.idk-step .title {font-weight: 700;}
.idk-step .watch {font-size: 0.88rem; margin-top: 2px;}
.idk-step .out {flex: 0 0 auto; font-size: 0.8rem; font-weight: 700; white-space: nowrap; padding-top: 3px;}
.idk-step.next {border-color: #5b9be6; box-shadow: 0 0 0 2px #cfe2fa;}
.idk-rail.wide .idk-chip {min-width: 70px; font-size: 0.76rem; padding: 6px 7px;}
.idk-rail.wide .idk-chip .s {font-size: 0.68rem;}
.idk-tag {display: inline-block; border-radius: 4px; padding: 0 6px; font-size: 0.7rem; font-weight: 700; letter-spacing: .02em;
          background: #e9edf2; color: #3b4652; margin-left: 6px; vertical-align: 1px;}
</style>
"""


# --------------------------------------------------------------------------- escaping

def esc(text: Any) -> str:
    """For HTML that this module builds."""
    return html.escape("" if text is None else str(text), quote=True)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~$:])")


def md_safe(text: Any) -> str:
    """For text placed inside a markdown label (expander titles and the like)."""
    return _MD_SPECIAL.sub(r"\\\1", "" if text is None else str(text)).replace("\n", " ")


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- data access

@st.cache_resource(show_spinner=False)
def _rules_cached(stamp: float) -> Rules:
    return load_rules()


def get_rules() -> Rules:
    """rules.yaml, reloaded whenever the file changes on disk."""
    return _rules_cached(config.RULES_PATH.stat().st_mtime)


@st.cache_resource(show_spinner=False)
def _master_cached(stamp: tuple) -> MasterData:
    return load_master()


def get_master() -> MasterData:
    files = [config.DATA_DIR / "vendors.csv", config.DATA_DIR / "purchase_orders.csv"]
    return _master_cached(tuple(f.stat().st_mtime for f in files))


SANDBOX_ID = re.compile(r"[0-9a-f]{12}")


def sandbox_id() -> str:
    """This visitor's sandbox on a host that gives every session its own books. The id lives
    in the session and in the page address (?s=...), so a refresh comes back to the same books."""
    current = st.session_state.get("sandbox_id")
    if not current:
        asked = str(st.query_params.get("s", ""))
        current = asked if SANDBOX_ID.fullmatch(asked) else secrets.token_hex(6)
        st.session_state["sandbox_id"] = current
        config.clean_sandboxes(keep=current)
    if st.query_params.get("s") != current:
        st.query_params["s"] = current
    return current


def get_store() -> Store:
    """The books: one shared database, or this visitor's own (config.sandbox_mode)."""
    if config.sandbox_mode() == "session":
        return Store(config.sandbox_db(sandbox_id()))
    return Store()


def business_date() -> date:
    """The date invoices are processed on. INVOICE_DESK_TODAY wins; otherwise the date the
    samples were written for, unless the demo clock was switched off on the Guided demo page."""
    if not st.session_state.get("demo_clock", True) and not config.env("INVOICE_DESK_TODAY"):
        return config.today()
    return sample_lib.business_date()


def ai_modes() -> list[str]:
    """The AI assist modes this screen may offer."""
    return ["fixture"] + (["cached-live"] if config.live_allowed() else []) + ["off"]


def ai_mode() -> str:
    """The mode in force: the one chosen on screen if it is allowed, else the configured one."""
    configured = config.llm_config().mode
    if configured == "cached-live" and not config.live_allowed():
        configured = "fixture"
    chosen = st.session_state.get("ai_mode", configured)
    return chosen if chosen in ai_modes() else configured


HTTP_CLIENT = None       # tests put an SDK http client with a fake transport here; None means the real API


def fresh_answers() -> bool:
    """The presenter asked for a fresh answer from Claude on every run, and live calls are possible."""
    return bool(st.session_state.get("ai_fresh")) and ai_mode() == "cached-live" and config.live_allowed()


def get_llm() -> LlmAssist:
    llm = LlmAssist(config.llm_config(ai_mode()), http_client=HTTP_CLIENT)
    llm.fresh = fresh_answers() and llm.cfg.can_call
    return llm


def get_pipeline(rules: Optional[Rules] = None, store: Optional[Store] = None) -> Pipeline:
    return Pipeline(store=store or get_store(), rules=rules or get_rules(), master=get_master(), llm=get_llm(),
                    today=business_date())


def reset_books(store: Store) -> None:
    """Back to the seeded history, and forget what this session was looking at."""
    store.reset()
    for key in ("current_run", "batch", "dash_run", "wi_result", "demo_done", "demo_last", "demo_auto"):
        st.session_state.pop(key, None)


@st.cache_data(show_spinner=False)
def manifest() -> list[dict]:
    return sample_lib.load_manifest()


def money(value: Any) -> str:
    return T.fmt_inr(value)


def took(ms: Any) -> str:
    """Processing time the way a person says it: 'under a second', '4 seconds'."""
    seconds = float(ms or 0) / 1000
    if seconds < 1:
        return "under a second"
    return f"{seconds:.0f} seconds" if seconds >= 10 else f"{seconds:.1f} seconds".replace(".0 ", " ")


def display_name(file_name: str) -> str:
    """Bundled samples are stored under tidy file names; show the name the attachment arrived with."""
    known = {str(s["file"]).split("/")[-1]: s.get("received_as") for s in manifest()}
    return known.get(file_name) or file_name


# --------------------------------------------------------------------------- small building blocks

def table(rows: Iterable[dict], height: Optional[int] = None, key: Optional[str] = None) -> None:
    """A read-only table. Everything is shown as text, exactly as the engine reported it."""
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        st.caption("Nothing to show.")
        return
    frame = frame.astype(object).where(frame.notna(), "").astype(str)
    kwargs: dict[str, Any] = {"hide_index": True, "width": "stretch"}
    if height is None and len(frame) > 10:      # show every row of a medium table instead of a scroll box
        height = min(35 * (len(frame) + 1) + 3, 740)
    if height:
        kwargs["height"] = height
    if key:
        kwargs["key"] = key
    st.dataframe(frame, **kwargs)


def text_block(text: str) -> None:
    st.markdown(f"<div class='idk-text'>{esc(text)}</div>", unsafe_allow_html=True)


def render_blocks(blocks: list[dict]) -> None:
    """Evidence blocks produced by a stage: tables, key/value lists and plain text."""
    for block in blocks:
        title = block.get("title")
        if title:
            st.markdown(f"<div class='idk-small'><b>{esc(title)}</b></div>", unsafe_allow_html=True)
        if block.get("type") in ("table", "kv"):
            table(block.get("rows") or [])
        else:
            text_block(block.get("text") or "")


def finding_html(f: Finding, with_summary: bool = True) -> str:
    cls = ACTION_CLASS.get(f.action, "f-note")
    kind = {"REJECT": "Reject", "HOLD": "Hold", "NOTE": "Note"}.get(f.action, "Note")
    owner = f" &nbsp;|&nbsp; owner: {esc(f.owner)}" if f.owner and f.action != "NOTE" else ""
    parts = [f"<div class='idk-find {cls}'><span class='t'>{esc(kind)}: {esc(f.title or f.code)}</span> "
             f"<span class='idk-small'>[{esc(f.code)}]{owner}</span>"]
    if with_summary:
        parts.append(f"<br>{esc(f.summary)}")
    meta = []
    if f.expected:
        meta.append(f"<b>Expected:</b> {esc(f.expected)}")
    if f.observed:
        meta.append(f"<b>Found:</b> {esc(f.observed)}")
    if meta:
        parts.append(f"<div class='meta'>{' &nbsp;|&nbsp; '.join(meta)}</div>")
    if f.question and f.action != "NOTE":
        parts.append(f"<div class='meta'><b>Question:</b> {esc(f.question)}</div>")
    parts.append("</div>")
    return "".join(parts)


def render_findings(findings: list[Finding], with_summary: bool = True) -> None:
    if findings:
        st.markdown("".join(finding_html(f, with_summary) for f in findings), unsafe_allow_html=True)


# --------------------------------------------------------------------------- stages

def _stage_label(key: str, status: str, label: str) -> str:
    """Stage 8 shows the decision itself, stage 9 that the record was written."""
    if key == "decide":
        return DECIDE_LABEL.get(status, label)
    if key == "post" and status == "ok":
        return "recorded"
    return label


def rail_html(states: dict[str, str]) -> str:
    """The nine stages as a row of chips; `states` maps stage key to a status."""
    chips = []
    for number, (key, title) in enumerate(STAGES, 1):
        status = states.get(key, "pending")
        symbol, label, cls = STATUS.get(status, STATUS["pending"])
        label = _stage_label(key, status, label)
        lead = "<span class='idk-spin'></span>" if status == "running" else (esc(symbol) + " " if symbol else "")
        chips.append(f"<div class='idk-chip {cls}'><span class='n'>{number}</span>{esc(title)}"
                     f"<span class='s'>{lead}{esc(label)}</span></div>")
    return "<div class='idk-rail'>" + "".join(chips) + "</div>"


def stage_row_html(number: int, title: str, status: str, summary: str = "", duration_ms: Optional[float] = None,
                   findings: Optional[list[Finding]] = None) -> str:
    """One stage as a row: used while a run is in progress."""
    symbol, label, cls = STATUS.get(status, STATUS["pending"])
    label = _stage_label(STAGES[number - 1][0], status, label)
    lead = "<span class='idk-spin'></span>" if status == "running" else ""
    time_text = f"{duration_ms:.0f} ms" if duration_ms is not None else ""
    extra = "".join(finding_html(f) for f in (findings or []))
    return (f"<div class='idk-row {cls}'><div class='num'>{number}</div><div class='body'>"
            f"<span class='title'>{esc(title)}</span> <span class='idk-small'>{lead}{esc(label)}</span>"
            f"<div class='sum'>{esc(summary)}</div>{extra}</div><div class='ms'>{esc(time_text)}</div></div>")


def stage_states(result: RunResult) -> dict[str, str]:
    return {s.key: s.status for s in result.stages}


def render_stage(stage: StageResult, expanded: bool = False) -> None:
    """One finished stage as an expander with its evidence inside."""
    colour = {"ok": "green", "attention": "orange", "blocked": "red", "error": "red"}.get(stage.status, "gray")
    symbol = STATUS.get(stage.status, STATUS["pending"])[0] or "•"
    label = (f":{colour}[**{symbol} {stage.number} {md_safe(stage.title)}**]   {md_safe(stage.summary)} "
             f"  :gray[{stage.duration_ms:.0f} ms]")
    with st.expander(label, expanded=expanded):
        st.caption(STAGE_HELP.get(stage.key, ""))
        render_findings(stage.findings)
        render_blocks(stage.blocks)
        if stage.status == "skipped":
            st.caption("This stage did not run for this document.")


def render_stages(result: RunResult, expand_issues: bool = True) -> None:
    for stage in result.stages:
        open_it = expand_issues and stage.status in ("attention", "blocked", "error") and stage.key != "decide"
        render_stage(stage, expanded=open_it)


# --------------------------------------------------------------------------- the decision

def verdict_banner_html(result: RunResult) -> str:
    d = result.decision
    label, colour, background, border = VERDICT[d.verdict]
    return (f"<div class='idk-banner' style='color:{colour};background:{background};border-color:{border}'>"
            f"<div class='tag'>{esc(result.run_id)} &nbsp;|&nbsp; {esc(display_name(result.file_name))} &nbsp;|&nbsp; "
            f"{esc(label)}, processed in {esc(took(result.duration_ms))}</div>"
            f"<div class='headline'>{esc(d.headline)}</div>"
            f"<div class='why'>{esc(d.explanation)}</div>"
            f"<div class='next'><b>Next:</b> {esc(d.next_action)}</div></div>")


def neutral_banner_html(tag: str, headline: str, text: str = "") -> str:
    return (f"<div class='idk-banner idk-neutral'><div class='tag'>{esc(tag)}</div>"
            f"<div class='headline'>{esc(headline)}</div><div class='why'>{esc(text)}</div></div>")


def vendor_note_card(result: RunResult, key: str) -> None:
    """The drafted message to the vendor: ready to copy, never sent by the system."""
    d = result.decision
    if not d.vendor_draft:
        return
    with st.expander("Message to the vendor (drafted for you; a person sends it)", expanded=d.verdict == "REJECT"):
        vendor = result.vendor or {}
        to = vendor.get("email")
        if to:
            st.markdown(f"<div class='idk-small'><b>To:</b> {esc(to)} (the address in the vendor master)</div>",
                        unsafe_allow_html=True)
        st.code(d.vendor_draft, language=None, wrap_lines=True)
        st.caption("Copy it with the icon at the top right of the box, or download it. Every figure in it comes from "
                   "this invoice record.")
        llm = get_llm()
        reworded = st.session_state.get(f"note_ai_{result.run_id}")
        if llm.cfg.mode != "off":
            from ..decide import note_must_keep
            stored = llm.find("vendor_note", __import__("hashlib").sha256(d.vendor_draft.encode("utf-8")).hexdigest(),
                              llm.cfg.model) is not None
            label = "Show Claude's rewording" if stored else "Reword with Claude"
            if (stored or llm.cfg.can_call) and st.button(label, key=f"note_btn_{key}", icon=":material/edit_note:"):
                reworded = llm.draft_vendor_note(d.vendor_draft, note_must_keep(result.decision_ctx, d.vendor_draft))
                st.session_state[f"note_ai_{result.run_id}"] = reworded
        if reworded:
            if reworded.get("accepted"):
                st.markdown("**Reworded by Claude** (checked by code: it adds no figure, date or reference that the "
                            "draft above does not have, and keeps the invoice number, PO and total)")
                st.code(reworded["text"], language=None, wrap_lines=True)
            else:
                st.info("Claude's rewording was not used: " + (reworded.get("why") or reworded.get("error") or
                                                               reworded.get("note") or "no answer was available") +
                        ". The draft above stands.")


def render_decision_details(result: RunResult, key: str) -> None:
    """Reasons with owners and questions, the message to the vendor, and the downloads."""
    d = result.decision
    if d.reasons:
        many = len(d.reasons) > 1
        st.markdown(f"**{'Reasons, owners and the questions to answer' if many else 'Reason, owner and the question to answer'}**")
        # with one reason the banner already carries its full text; with several, each card repeats its own
        render_findings(d.reasons, with_summary=many)
    if d.also_noted:
        with st.expander(f"Also found: {len(d.also_noted)} further point(s) that would hold this document on their own"):
            st.caption("The strictest action wins, so these did not set the decision. They stay on the record in "
                       "case the rejection is disputed.")
            render_findings(d.also_noted)
    if d.notes:
        with st.expander(f"Noted for the record: {len(d.notes)} point(s) that did not affect the decision",
                         expanded=not d.reasons):
            render_findings(d.notes)
    vendor_note_card(result, key)
    columns = st.columns([0.2, 0.2, 0.2, 0.4])
    columns[0].download_button("Full record (JSON)", data=result.model_dump_json(indent=2), icon=":material/download:",
                               file_name=f"{result.run_id}_record.json", mime="application/json",
                               key=f"dl_run_{key}", on_click="ignore", width="stretch")
    columns[1].download_button("Audit receipt (JSON)", data=json.dumps(result.receipt or {}, indent=2, default=str),
                               icon=":material/download:", file_name=f"{result.run_id}_receipt.json",
                               mime="application/json", key=f"dl_receipt_{key}", on_click="ignore", width="stretch")
    if d.vendor_draft:
        columns[2].download_button("Vendor message (TXT)", data=d.vendor_draft, icon=":material/download:",
                                   file_name=f"{result.run_id}_vendor_message.txt", mime="text/plain",
                                   key=f"dl_note_{key}", on_click="ignore", width="stretch")


# --------------------------------------------------------------------------- page preview

@st.cache_data(show_spinner=False, max_entries=64)
def _preview_png(sha256: str, page_number: int, angle: float, boxes: tuple, page_width: float,
                 _content: bytes, prep: str = "") -> Optional[bytes]:
    """The page as an image with the places the values were read from outlined. `prep` is
    how a photographed page was straightened at read time (JSON), so the boxes land on the words."""
    from PIL import ImageDraw
    scale = 1.6
    try:
        image = reader.render_page_image(_content, page_number, scale=scale, angle=angle,
                                         prep=json.loads(prep) if prep else None)
    except Exception:
        return None
    if page_width > 0:
        scale = image.width / page_width          # an outsized page is rendered smaller than asked
    draw = ImageDraw.Draw(image, "RGBA")
    colours = {"field": ((16, 110, 190, 255), (16, 110, 190, 36)), "line": ((20, 140, 80, 255), (20, 140, 80, 30)),
               "ai": ((124, 58, 237, 255), (124, 58, 237, 34))}
    for x0, top, x1, bottom, kind in boxes:
        outline, fill = colours.get(kind, colours["field"])
        draw.rectangle([x0 * scale - 2, top * scale - 2, x1 * scale + 2, bottom * scale + 2], outline=outline, fill=fill, width=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def source_boxes(result: RunResult, page_number: int) -> tuple:
    boxes = []
    inv = result.invoice
    if inv is None:
        return ()
    for field in inv.fields.values():
        src = field.source
        if field.present and src and src.bbox and src.page == page_number and len(src.bbox) == 4:
            boxes.append((*[round(float(v), 1) for v in src.bbox], "ai" if src.method == "llm" else "field"))
    for item in inv.line_items:
        src = item.source
        if src and src.bbox and src.page == page_number and len(src.bbox) == 4:
            boxes.append((*[round(float(v), 1) for v in src.bbox], "ai" if src.method == "llm" else "line"))
    return tuple(boxes)


def render_preview(content: Optional[bytes], sha256: str, result: Optional[RunResult] = None, key: str = "pv") -> None:
    """The document itself, with source boxes once the fields have been read."""
    if content is None:
        st.caption("The original file is not stored for this run.")
        return
    if not content:
        st.info("No preview: the file is empty (0 bytes).")
        return
    pages = (result.pages if result else None) or [{"number": 1, "angle": 0.0, "kind": "text"}]
    numbers = [p["number"] for p in pages][:get_rules().intake.max_pages]
    page_number = numbers[0]
    if len(numbers) > 1:
        page_number = st.selectbox("Page", numbers, key=f"{key}_page")
    info = next((p for p in pages if p["number"] == page_number), pages[0])
    boxes = source_boxes(result, page_number) if result else ()
    prep = info.get("prep") or {}
    straightened = bool(prep.get("quad") or prep.get("turns") or prep.get("crop"))
    png = _preview_png(sha256, page_number, float(info.get("angle") or 0.0), boxes, float(info.get("width") or 0.0), content,
                       json.dumps(prep, sort_keys=True) if straightened else "")
    if png is None:
        st.info("No preview: this file could not be opened as a PDF.")
        return
    st.image(png, width="stretch")
    notes = []
    if boxes:
        notes.append("Blue boxes: header fields. Green boxes: line items. Each is where a value was read.")
    if any(box[4] == "ai" for box in boxes):
        notes.append("Purple boxes: values Claude read. Each was found again on the page by code, inside the sentence "
                     "Claude quoted, before it was used.")
    if str(info.get("kind", "")).startswith("scan"):
        how = "recorded OCR output" if info.get("ocr_source") == "recorded" else "OCR"
        if straightened:
            notes.append(f"Photographed page, shown as it was read: cut out of its background, perspective undone"
                         f"{', turned upright' if prep.get('turns') else ''}. Read with {how}.")
        else:
            notes.append(f"Scanned page, shown straightened by {abs(float(info.get('angle') or 0)):.1f} degrees and read with {how}.")
    if notes:
        st.caption(" ".join(notes))


# --------------------------------------------------------------------------- one whole run

EVENT = {"decision": "Decision recorded by the system", "review": "Decision by a reviewer",
         "review_refused": "Approval refused by the system", "resubmission": "Same file received again"}


def audit_trail(result: RunResult, store: Store) -> None:
    """Who did what to this invoice, in order, with the technical proof one click further in."""
    receipts = store.receipts(result.run_id)
    if not receipts:
        return
    with st.expander(f"Tamper-evident audit trail: {len(receipts)} entr{'y' if len(receipts) == 1 else 'ies'}"):
        rows = []
        for r in receipts:
            body = r["body"]
            what = body.get("verdict") or body.get("decision", "")
            if body.get("decision") == "FIRST_APPROVAL":
                what = "first of two approvals (bank account change)"
            elif body.get("outcome") == "refused":
                what = "approval refused: " + str(body.get("reason", ""))
            elif body.get("approvers"):
                what = f"{what} by two people: {' and '.join(body['approvers'])}"
            rows.append({"When": config.to_ist_text(r["created_at"]), "Event": EVENT.get(r["kind"], r["kind"]),
                         "By": body.get("reviewer") or "Invoice Desk", "Outcome": str(what),
                         "Note": body.get("note") or "", "Entry fingerprint": r["hash"][:12]})
        table(rows)
        st.caption("Every decision, by the system or by a person, is written once and never edited. Each entry carries "
                   "a fingerprint computed from its content and from the entry before it, so changing or removing any "
                   "past entry is detectable. The Dashboard re-checks the whole trail on every visit.")
        if st.toggle("Technical details (for an auditor or an engineer)", key=f"audit_tech_{result.run_id}"):
            table({"#": r["seq"], "Kind": r["kind"], "SHA-256 of this entry": r["hash"],
                   "SHA-256 of the entry before it": r["prev_hash"], "Signed": "yes" if r.get("signature") else "no"}
                  for r in receipts)
            receipt = result.receipt or {}
            table([{"Item": "Document fingerprint (SHA-256 of the file)", "Value": result.file_sha256},
                   {"Item": "Rules file fingerprint", "Value": str((receipt.get("rules") or {}).get("sha256", ""))},
                   {"Item": "Master data fingerprint", "Value": str(receipt.get("master_data_sha256", ""))}])


def render_run(result: RunResult, store: Store, key: str) -> None:
    """A finished run: the decision, then every stage with its evidence beside the document."""
    st.markdown(verdict_banner_html(result), unsafe_allow_html=True)
    if result.resubmission_of:
        st.info(f"This exact file was already received as {result.resubmission_of}. Nothing was read or booked a second "
                f"time: the answer above is that record's, as it stands now. Open {result.resubmission_of} in the "
                f"Dashboard for the full evidence.", icon=":material/content_copy:")
    st.markdown(rail_html(stage_states(result)), unsafe_allow_html=True)
    render_decision_details(result, key)
    beside = st.toggle("Show the document beside the stages", value=True, key=f"beside_{key}",
                       help="Turn off to give the evidence tables the full width.")
    left, right = st.columns([0.58, 0.42], gap="large") if beside else (st.container(), None)
    with left:
        st.markdown("**The nine stages** (open any stage for its evidence)")
        render_stages(result)
        audit_trail(result, store)
    if right is not None:
        with right:
            st.markdown("**The document**")
            render_preview(store.load_file(result.file_sha256), result.file_sha256, result, key=f"pv_{key}")


REVIEW_STATUS = {"PENDING": "waiting for review", "APPROVED": "approved on review", "REJECTED": "rejected on review",
                 "HANDLED": "closed, processed manually"}


def run_table_rows(runs: list[dict], rules: Rules) -> list[dict]:
    rows = []
    for r in runs:
        codes = [c for c in (r.get("reason_codes") or "").split(",") if c]
        total = r.get("total") or ""
        again = r.get("resubmission_of")
        rows.append({
            "Reference": r["run_id"], "Decision": r["verdict"],
            "Status": f"same file as {again}" if again else REVIEW_STATUS.get(r.get("review_status") or "", "-"),
            "Vendor": r.get("vendor_name") or "-", "Invoice no.": r.get("invoice_no") or "-",
            "Total": "-" if not total else money(total) if T.to_decimal(total) is not None else total,
            "PO": r.get("po_number") or "-",
            "Reasons": "; ".join(rules.reason(c).title for c in codes) or "-", "File": display_name(r["file_name"]),
            "Received": config.to_ist_text(r["created_at"]),
            "Read": "scan (OCR)" if r.get("scanned") else "text", "Time": took(r.get("duration_ms"))})
    return rows


AI_MODE_LABEL = {"fixture": "recorded answers (no network)", "cached-live": "live Claude, stored answers first",
                 "off": "off (rules only)"}


def sidebar_status(pipeline: Pipeline) -> None:
    """One line a user needs, and the build details behind a click."""
    modes = pipeline.modes()
    ai = "live Claude, a fresh answer on every run" if fresh_answers() else AI_MODE_LABEL[modes["llm_mode"]]
    st.markdown(f"<div class='idk-status'><span class='dot'></span><b>Ready.</b> Decisions by rules "
                f"(version {esc(modes['rules_version'])}). AI assist: {esc(ai)}.</div>",
                unsafe_allow_html=True)
    with st.expander("Technical details"):
        ocr = modes["ocr"] if modes["ocr"] != "not installed" else "not installed (recorded output for known scans)"
        fixed = "set by INVOICE_DESK_TODAY" if config.env("INVOICE_DESK_TODAY") else "pinned for the demo"
        pinned = bool(config.env("INVOICE_DESK_TODAY")) or st.session_state.get("demo_clock", True)
        clock = T.fmt_date(modes["today"]) + (f" ({fixed})" if pinned else " (today)")
        books = "private to this browser session" if config.sandbox_mode() == "session" else "shared on this machine"
        lines = [("Reader", "rules, deterministic"), ("AI assist", modes["llm"]), ("OCR engine", ocr),
                 ("Rules", f"version {modes['rules_version']}"), ("Business date", clock), ("Books", books)]
        st.markdown("<div class='idk-mode'>" + "".join(f"<div><b>{esc(k)}</b> {esc(v)}</div>" for k, v in lines) + "</div>",
                    unsafe_allow_html=True)
