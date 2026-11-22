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
    st.subheader("Bundled samples")
    C.table({"#": f"{s['order']:02d}", "Sample": s["title"], "Documented outcome": sample_lib.expected(s)[0],
             "What it shows": s["description"]} for s in C.manifest())
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("The nine stages")
        C.table({"Stage": f"{n} {title}", "What it does": STAGE_HELP[key]} for n, (key, title) in enumerate(STAGES, 1))
        st.subheader("Three outcomes")
        st.markdown(
            "- **APPROVE**: everything matched inside tolerance. The invoice is posted to the ledger and uses PO balance.\n"
            "- **HOLD**: something needs a person. The hold names the owner and asks one specific question.\n"
            "- **REJECT**: nothing can be owed on this document (a duplicate, a blocked vendor, not an invoice).")
    with right:
        st.subheader("What decides")
        st.markdown(
            "- **Reading** is done by rules: label vocabulary, page geometry and arithmetic. The same file always "
            "reads the same way.\n"
            "- **Scans** are straightened and read with Tesseract OCR. Arithmetic then cross-checks the read: "
            "quantity times price, the sum of the lines, tax and the total must all agree.\n"
            "- **An LLM is optional** and only fills gaps the rules could not read. A value is accepted only if its "
            "quote is on the page and states the value. It never takes part in the decision.\n"
            "- **The decision** is a pure function of the findings and `rules.yaml`. No model, no clock, no database.\n"
            "- **Every decision** writes a receipt into a hash chain, and a human review joins the same chain.")
        st.subheader("Limits, stated plainly")
        st.markdown(
            "- The reader is built for Indian GST tax invoices in English with one purchase order per invoice.\n"
            "- A free-form invoice with no table (sample 10) is held by the rules alone; the LLM assist reads it when "
            "a key is configured.\n"
            "- Scan quality matters. A misread that arithmetic cannot confirm is held, not guessed.\n"
            "- Vendor and PO data come from CSV files that stand in for an ERP.")
