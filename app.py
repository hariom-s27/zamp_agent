"""Invoice Desk: the Streamlit app.

    streamlit run app.py

Pages: Guided demo (one scenario per click), Inbox (every message sorted and owned, round 4), Payments (the payment-run proposal), Procedure (change requests),
Process invoice (live stages), Review
queue (human decisions), Cases (what happens after a hold), Dashboard (history, status,
outputs), Business case, Rules and data, How it works. The pages only display; all the work is done by the `invoice_desk` package, which
the tests and the command line use too.
"""
from __future__ import annotations

import streamlit as st

from invoice_desk.ui import common as C
from invoice_desk.ui import cases, dashboard, demo, guide, inbox, payments, procedure, process, reference, review, value

st.set_page_config(page_title=C.APP_NAME, page_icon=":material/receipt_long:", layout="wide",
                   initial_sidebar_state="expanded")

store = C.get_store()
waiting = sum(1 for r in store.list_runs() if r["review_status"] == "PENDING")
PAGES = {
    "demo": st.Page(demo.page, title="Guided demo", icon=":material/slideshow:", default=True),
    "inbox": st.Page(inbox.page, title="Inbox", icon=":material/inbox:", url_path="inbox"),
    "process": st.Page(process.page, title="Process invoice", icon=":material/play_circle:", url_path="process"),
    "review": st.Page(review.page, title=f"Review queue ({waiting})" if waiting else "Review queue",
                      icon=":material/fact_check:", url_path="review"),
    "cases": st.Page(cases.page, title="Cases", icon=":material/assignment_late:", url_path="cases"),
    "payments": st.Page(payments.page, title="Payments", icon=":material/payments:", url_path="payments"),
    "procedure": st.Page(procedure.page, title="Procedure", icon=":material/tune:", url_path="procedure"),
    "dashboard": st.Page(dashboard.page, title="Dashboard", icon=":material/monitoring:", url_path="dashboard"),
    "value": st.Page(value.page, title="Business case", icon=":material/savings:", url_path="value"),
    "reference": st.Page(reference.page, title="Rules and data", icon=":material/rule:", url_path="rules"),
    "guide": st.Page(guide.page, title="How it works", icon=":material/help:", url_path="guide"),
}
SECTIONS = {
    "See it work": [PAGES["demo"], PAGES["process"]],
    "Work the desk": [PAGES["inbox"], PAGES["cases"], PAGES["review"], PAGES["payments"]],
    "Results": [PAGES["dashboard"], PAGES["value"]],
    "Reference": [PAGES["reference"], PAGES["procedure"], PAGES["guide"]],
}
current = st.navigation(SECTIONS)
target = st.session_state.pop("goto", None)
if target in PAGES:
    st.switch_page(PAGES[target])

C.inject_css()
with st.sidebar:
    st.markdown(f"### {C.APP_NAME}")
    st.caption("Invoice processing from PDF to decision, for Nilgiri Precision Components Pvt Ltd (a fictional buyer).")
    C.sidebar_status(C.get_pipeline(store=store))
    st.caption("Synthetic data only. No real vendors, bank accounts or invoices. Demo controls (fresh start, pace, "
               "AI assist mode) are on the Guided demo page.")

current.run()
