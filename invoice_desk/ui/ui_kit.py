"""Reusable UI pieces. Native Streamlit 1.67 only: no custom CSS, no HTML."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import streamlit as st

# word, palette colour, Material icon. Colour is always the third cue, never the only one.
STATUS = {
    "cleared": ("Cleared", "green", ":material/check_circle:"),
    "hold": ("On hold", "orange", ":material/pause_circle:"),
    "rejected": ("Rejected", "red", ":material/cancel:"),
    "passed": ("Passed", "green", ":material/check_circle:"),
    "flagged": ("Flagged", "orange", ":material/flag:"),
    "stopped": ("Stopped", "red", ":material/block:"),
    "skipped": ("Not needed", "gray", ":material/remove:"),
    "overdue": ("Overdue", "red", ":material/alarm:"),
    "due_soon": ("Due soon", "orange", ":material/schedule:"),
    "on_track": ("On track", "gray", ":material/schedule:"),
    "resolved": ("Resolved", "green", ":material/task_alt:"),
}
ACTOR = {
    "ai": ("AI read", "violet", ":material/auto_awesome:"),
    "rules": ("Rules", "gray", ":material/rule:"),
    "person": ("Person", "primary", ":material/person:"),
}


def chip_md(kind: str, text: str | None = None, table: dict = STATUS) -> str:
    """Badge as a Markdown string, for labels, captions and table cells."""
    word, colour, icon = table[kind]
    safe = (text or word).replace("[", "\\[").replace("]", "\\]")
    return f":{colour}-badge[{icon} {safe}]"


def status_chip(kind: str, text: str | None = None, help: str | None = None) -> None:
    word, colour, icon = STATUS[kind]
    st.badge(text or word, icon=icon, color=colour, help=help)


def actor_chip(kind: str, name: str | None = None) -> str:
    return chip_md(kind, name, ACTOR)


def inr(amount: float) -> str:
    """Indian digit grouping: 1234567.5 -> Rs 12,34,568 (whole rupees)."""
    n = int(round(abs(amount)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if amount < 0 else "") + "₹" + s


def inr_short(amount: float) -> str:
    """Headline form: Rs 6.8 lakh, Rs 2.31 crore."""
    a = abs(amount)
    sign = "-" if amount < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} crore"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.1f} lakh"
    return sign + inr(a)


def due_label(due: date, today: date) -> tuple[str, str]:
    """(status kind, words). Words carry the meaning; the colour only repeats it."""
    d = (due - today).days
    if d < 0:
        return "overdue", f"Overdue by {-d} day{'s' if d != -1 else ''}"
    if d == 0:
        return "due_soon", "Due today"
    if d <= 2:
        return "due_soon", f"Due in {d} day{'s' if d != 1 else ''}"
    return "on_track", f"Due {due.strftime('%d %b')}"


@dataclass
class Stage:
    number: int
    title: str  # plain English, past tense: "Checked the maths and tax"
    result: str  # passed | flagged | stopped | skipped
    summary: str  # one line a finance person can read
    actor: str = "rules"  # rules | ai
    seconds: float | None = None
    lines: list[str] = field(default_factory=list)


_ICON = {k: STATUS[k][2] for k in ("passed", "flagged", "stopped", "skipped")}


def stage_card(s: Stage) -> None:
    """One finished stage as a timeline step. Call for consecutive stages with nothing in between."""
    took = f" · {s.seconds:.1f} s" if s.seconds is not None else ""
    who = f" {actor_chip(s.actor)}" if s.actor != "rules" else ""
    label = f"**{s.number}. {s.title}** · {s.summary}{took}{who}"
    with st.expander(label, icon=_ICON[s.result], type="step", expanded=s.result in ("flagged", "stopped")):
        st.markdown(chip_md(s.result))
        for line in s.lines or [s.summary if s.result != "passed" else "All checks passed."]:
            st.markdown(f"- {line}")


def run_stages_live(stages, pace: float = 0.0) -> list[Stage]:
    """Draw stages while they happen. `stages` yields (title, work); work() returns a Stage.

    Each step gets one line of content, otherwise the connecting line stops. When the last
    stage ends, the caller stores the result and reruns so the page settles into stage_card().
    """
    import time

    done = []
    for number, (title, work) in enumerate(stages, start=1):
        box = st.status(f"**{number}. {title}**", type="step", state="running", expanded=False)
        box.caption("Working on it")
        result = work()
        if pace:
            time.sleep(pace)
        state = "error" if result.result == "stopped" else "complete"
        box.update(label=f"**{number}. {result.title}** · {result.summary}", state=state)
        done.append(result)
    return done


def evidence_panel(rows: list[dict]) -> None:
    """rows: field, on_invoice, expected, source, ok. A Markdown table wraps text and never hides a column."""
    out = ["| Check | Result | On the invoice | In our records | Where we read it |", "|---|---|---|---|---|"]
    for r in rows:
        result = chip_md("passed", "Match") if r["ok"] else chip_md("flagged", "Does not match")
        cells = [r["field"], result, r["on_invoice"], r["expected"], r["source"]]
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    st.markdown("\n".join(out))


def case_card(case: dict, today: date) -> bool:
    """Returns True when the user opens the case."""
    kind, words = due_label(case["due"], today)
    with st.container(border=True, key=f"case-{case['id']}"):
        top = st.container(horizontal=True, vertical_alignment="center")
        top.markdown(f"**{case['vendor']}** · {case['invoice_no']} · {inr(case['amount'])}")
        top.markdown(chip_md(kind, words))
        st.markdown(case["question"])
        foot = st.container(horizontal=True, vertical_alignment="center")
        foot.markdown(f":gray-badge[:material/assignment_ind: Waiting on {case['owner']}]")
        if case.get("msme_day"):
            foot.markdown(f":gray-badge[:material/gavel: Small supplier · day {case['msme_day']} of 45]")
        return foot.button("Open case", key=f"open-{case['id']}", type="secondary")


def kpi_card(label: str, value: str, note: str, *, help: str, delta: str | None = None,
             good_when: str = "up") -> None:
    """One number, one line of context. Call inside st.grid(...): the bordered container is one cell."""
    with st.container(border=True, height="stretch"):
        st.metric(label, value, delta=delta, help=help,
                  delta_color="normal" if good_when == "up" else "inverse")
        st.caption(note)
