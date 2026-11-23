"""Page 5, How it works: the process on one page, for someone seeing the app cold."""
from __future__ import annotations

import streamlit as st

from .. import samples as sample_lib
from ..pipeline import STAGES
from .. import config
from . import common as C
from . import ui_kit as K

RESULTS = config.ROOT / "robustness" / "results.json"
ROBUST_LABELS = [("approved-correct", "Cleared correctly"), ("held-correct", "Held correctly"),
                 ("misread-held", "Misread, held for a person"), ("misread-approved", "Misread and cleared")]


def robustness_numbers(path=None) -> dict | None:
    """The four measured outcomes on the 49-document test, read from the results file; None if it is missing."""
    import json
    try:
        summary = json.loads((path or RESULTS).read_text(encoding="utf-8"))["summary"]["All documents"]
        return {"documents": int(summary["documents"]), **{k: int(summary[k]) for k, _ in ROBUST_LABELS}}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def robustness_figures(path=None) -> None:
    numbers = robustness_numbers(path)
    if numbers is None:
        st.caption("Test results are not available.")
        return
    st.markdown(f"**Measured on {numbers['documents']} difficult documents made with other tools than the samples**")
    cols = st.columns(4)
    for col, (key, label) in zip(cols, ROBUST_LABELS):
        col.metric(label, numbers[key], border=True)

def _flow() -> str:
    """The nine steps under the same names as the Process invoice timeline."""
    from ..pipeline import STAGES as KEYS
    ai = ', fillcolor="#efe7fb", color="#7c3aed"'
    nodes = " ".join(f's{n} [label="{n} {C.stage_display_title(k)}"{ai if k == "extract" else ""}];'
                     for n, (k, _) in enumerate(KEYS, 1))
    chain = " -> ".join(f"s{n}" for n in range(1, len(KEYS) + 1))
    return f"""
digraph G {{
  rankdir=LR; nodesep=0.25; ranksep=0.35;
  node [shape=box, style="rounded,filled", fillcolor="#f2f4f7", color="#7b8794", fontname="Helvetica", fontsize=11];
  edge [color="#7b8794", arrowsize=0.7];
  pdf [label="Invoice PDF\\n(text or scan)", shape=note, fillcolor="#ffffff"];
  {nodes}
  a [label="Cleared\\nposted for payment", fillcolor="#e6f4ec", color="#1a8a52"];
  h [label="On hold\\na named person, one question", fillcolor="#fff3d1", color="#d99a00"];
  r [label="Rejected\\nnothing booked", fillcolor="#fdeaea", color="#c9343d"];
  pdf -> {chain};
  s{len(KEYS)} -> a; s{len(KEYS)} -> h; s{len(KEYS)} -> r;
}}
"""


def page() -> None:
    st.title("How it works")
    st.caption("An accounts payable desk for a fictional buyer, Nilgiri Precision Components Pvt Ltd. All vendors, "
               "purchase orders and invoices are synthetic test data.")
    cols = st.columns(3, gap="large")
    with cols[0]:
        with st.container(border=True, height="stretch"):
            st.markdown(K.actor_chip("ai", "AI reads"))
            st.markdown("Only where the layout defeats the rules, such as an invoice written as a letter. Every value "
                        "must match a quote from the page, and the invoice's own arithmetic, before it is used.")
    with cols[1]:
        with st.container(border=True, height="stretch"):
            st.markdown(K.actor_chip("rules", "Rules decide"))
            st.markdown(f"{len(C.get_rules().reasons)} written reasons, every limit in one rule book. The same "
                        f"invoice gets the same answer, every time.")
    with cols[2]:
        with st.container(border=True, height="stretch"):
            st.markdown(K.actor_chip("person", "People approve"))
            st.markdown("Anything that leaves, any bank change, any payment run. By name, on the record.")
    st.caption("AI never makes the payment decision.")

    st.subheader("The nine steps")
    st.graphviz_chart(_flow(), width="stretch")
    C.table({"Step": f"{n} {C.stage_display_title(key)}", "What it does": C.stage_display_help(key)}
            for n, (key, _) in enumerate(STAGES, 1))
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Three outcomes")
        st.markdown(
            "- **Cleared**: everything matched inside the limits. The invoice is posted for payment and uses PO "
            "balance.\n"
            "- **On hold**: something needs a person. The hold names the owner and asks one specific question. "
            "Clearing it is controlled too: the PO is checked again at approval, and a changed bank account takes "
            "two people.\n"
            "- **Rejected**: nothing can be owed on this document (a duplicate, a blocked vendor, not an invoice). "
            "The message to the vendor is drafted for a person to send.")
        st.subheader("What decides")
        st.markdown(
            "- **The decision** comes from what was found and from the rule book, nothing else. No AI takes part "
            "in it.\n"
            "- **Reading** is done by rules: the words on the page, where they sit, and the arithmetic.\n"
            "- **Scans and photographs** are straightened and read twice. A figure is trusted when the arithmetic "
            "confirms it; an invoice number or a date, which no arithmetic can confirm, only when both reads agree.\n"
            "- **AI assist** is used where rules cannot read: an invoice written as a letter. The AI copies text; "
            "the desk checks that each value is on the page, and checks the invoice's arithmetic. The AI's own "
            "confidence can lower trust and never raise it.\n"
            "- **Every decision**, by the desk or by a person, is written to an audit trail that shows any later "
            "change.")
    with right:
        st.subheader("How well it reads")
        numbers = robustness_numbers()
        if numbers is None:
            st.caption("Test results are not available.")
        else:
            robustness_figures()
            st.markdown(f"With a test this size, the true rate of \"misread and cleared\" could be up to 7%. Every "
                        f"misread so far was held for a person: {numbers['misread-held']} of {numbers['documents']}, "
                        f"all photographs.")
        st.subheader("What it does not do")
        st.markdown(
            "- Pay anyone, or change a bank account.\n"
            "- Send an email without a named person approving it.\n"
            "- Read a live mailbox or a live ERP in this demo: vendor and PO records come from files.\n"
            "- Process credit notes, pro-forma or foreign-currency invoices: they are recognised and sent to a "
            "person.\n"
            "- Guess. What cannot be confirmed is held.")
    st.subheader("The sample inbox")
    C.table({"#": f"{s['order']:02d}", "Scenario": s["title"], "Kind": s.get("tag", ""),
             "Expected": C.verdict_display_label(sample_lib.expected(s)[0]) + (
                 f" by the rules alone, {C.verdict_display_label(s['with_llm']['expected_verdict'])} with AI assist"
                 if "with_llm" in s else ""),
             "What it shows": C.screen_words(s["description"])} for s in C.manifest())
