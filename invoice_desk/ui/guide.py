"""Page 5, How it works: the process on one page, for someone seeing the app cold."""
from __future__ import annotations

import streamlit as st

from .. import samples as sample_lib
from ..pipeline import STAGE_HELP, STAGES
from . import common as C

FLOW = """
digraph G {
  rankdir=LR; nodesep=0.25; ranksep=0.35;
  node [shape=box, style="rounded,filled", fillcolor="#f2f4f7", color="#7b8794", fontname="Helvetica", fontsize=11];
  edge [color="#7b8794", arrowsize=0.7];
  pdf [label="Invoice PDF\\n(text or scan)", shape=note, fillcolor="#ffffff"];
  s1 [label="1 Intake"]; s2 [label="2 Read"]; s3 [label="3 Extract"]; s4 [label="4 Validate"]; s5 [label="5 Vendor"];
  s6 [label="6 PO match"]; s7 [label="7 Duplicates"];
  s8 [label="8 Decide\\n(rules.yaml)", fillcolor="#e7f0fb", color="#5b9be6"];
  s9 [label="9 Post and verify"];
  a [label="APPROVE\\nposted for payment", fillcolor="#e6f4ec", color="#1a8a52"];
  h [label="HOLD\\nreview queue, named owner", fillcolor="#fff3d1", color="#d99a00"];
  r [label="REJECT\\nnothing booked", fillcolor="#fdeaea", color="#c9343d"];
  pdf -> s1 -> s2 -> s3 -> s4 -> s5 -> s6 -> s7 -> s8 -> s9;
  s9 -> a; s9 -> h; s9 -> r;
}
"""


def page() -> None:
    st.title("How it works")
    st.caption("An accounts payable desk for a fictional buyer, Nilgiri Precision Components Pvt Ltd. All vendors, "
               "purchase orders and invoices are synthetic test data.")
    st.graphviz_chart(FLOW, width="stretch")
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Three outcomes")
        st.markdown(
            "- **APPROVE**: everything matched inside tolerance. The invoice is posted to the ledger and uses PO balance.\n"
            "- **HOLD**: something needs a person. The hold names the owner and asks one specific question. Clearing "
            "it is controlled too: the PO is checked again at approval, and a changed bank account takes two people.\n"
            "- **REJECT**: nothing can be owed on this document (a duplicate, a blocked vendor, not an invoice). The "
            "message to the vendor is drafted.")
        st.subheader("The nine stages")
        C.table({"Stage": f"{n} {title}", "What it does": STAGE_HELP[key]} for n, (key, title) in enumerate(STAGES, 1))
    with right:
        st.subheader("What decides")
        st.markdown(
            "- **The decision** is a function of what was found and of `rules.yaml`, nothing else. No model takes "
            "part in it. The same file and the same rules give the same answer every time.\n"
            "- **Reading** is done by rules: label vocabulary, page geometry and arithmetic.\n"
            "- **Scans and photographs** are straightened and read twice with Tesseract OCR. A figure is trusted "
            "when the arithmetic confirms it; an invoice number or a date, which no arithmetic can confirm, only "
            "when both reads agree.\n"
            "- **AI assist** is used where rules cannot read: an invoice written as a letter. The model "
            "copies text; code checks that each value is on the page, parses it, and checks the invoice's arithmetic. "
            "The model's own confidence can lower trust and never raise it. Modes: recorded answers (the default, no "
            "network), live with a cache and cost caps, or off.\n"
            "- **Every decision**, by the system or by a person, is written to a tamper-evident audit trail.")
        st.subheader("Limits, stated plainly")
        st.markdown(
            "- Built for Indian GST tax invoices in English, one purchase order per invoice. Credit notes, pro-forma "
            "and foreign-currency invoices are recognised and routed to a person, not processed.\n"
            "- Measured on 49 invoices made with other tools than the samples (docs/robustness_report.md): 45 "
            "reached the right answer. The other 4, all photographs, were held for a person because a value could "
            "not be read with certainty. None was approved wrongly.\n"
            "- Photograph quality matters. What cannot be confirmed is held, not guessed.\n"
            "- Vendor and PO data come from CSV files that stand in for an ERP. Reviewer names are typed, where a "
            "real deployment would take them from the company login.")
    st.subheader("The sample inbox")
    C.table({"#": f"{s['order']:02d}", "Scenario": s["title"], "Kind": s.get("tag", ""),
             "Expected decision": sample_lib.expected(s)[0] + (
                 f" by the rules alone, {s['with_llm']['expected_verdict']} with AI assist" if "with_llm" in s else ""),
             "What it shows": s["description"]} for s in C.manifest())
