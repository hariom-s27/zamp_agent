"""Invoice Desk: the Streamlit app.

    streamlit run app.py

Five pages: Process invoice (live stages), Dashboard (history, status, outputs), Review
queue (human decisions), Rules and data, How it works. The pages only display; all the
work is done by the `invoice_desk` package, which the tests and the command line use too.
"""
from __future__ import annotations

import streamlit as st

from invoice_desk.ui import common as C
from invoice_desk.ui import dashboard, guide, process, reference, review

st.set_page_config(page_title=C.APP_NAME, page_icon=":material/receipt_long:", layout="wide",
                   initial_sidebar_state="expanded")

store = C.get_store()
waiting = sum(1 for r in store.list_runs() if r["review_status"] == "PENDING")
PAGES = {
    "process": st.Page(process.page, title="Process invoice", icon=":material/play_circle:", default=True),
    "dashboard": st.Page(dashboard.page, title="Dashboard", icon=":material/monitoring:", url_path="dashboard"),
    "review": st.Page(review.page, title=f"Review queue ({waiting})" if waiting else "Review queue",
                      icon=":material/fact_check:", url_path="review"),
    "reference": st.Page(reference.page, title="Rules and data", icon=":material/rule:", url_path="rules"),
    "guide": st.Page(guide.page, title="How it works", icon=":material/help:", url_path="guide"),
}
current = st.navigation(list(PAGES.values()))
target = st.session_state.pop("goto", None)
if target in PAGES:
    st.switch_page(PAGES[target])

C.inject_css()
with st.sidebar:
    st.markdown(f"### {C.APP_NAME}")
    st.caption("Invoice processing from PDF to decision, for Nilgiri Precision Components Pvt Ltd (fictional).")
    st.markdown(C.mode_lines(C.get_pipeline(store=store)), unsafe_allow_html=True)
    st.slider("Viewing pause per stage (seconds)", 0.0, 1.5, 0.4, 0.1, key="pause",
              help="Stages finish in milliseconds. The pause only slows the display so each stage can be followed; "
                   "the timings shown are the real processing times.")
    with st.popover("Reset demo data", width="stretch"):
        st.write("Clears every run, review and receipt and restores the seeded history (10 invoices posted before "
                 "this tool). Use it before a demo so each sample meets the documented starting state.")
        if st.button("Yes, reset now", key="reset_go"):
            store.reset()
            for key in ("current_run", "batch", "dash_run", "wi_result"):
                st.session_state.pop(key, None)
            st.session_state["flash"] = "Demo data reset to the seeded history."
            st.session_state["goto"] = "process"
            st.rerun()
    st.caption("Synthetic data only. No real vendors, bank accounts or invoices.")

current.run()
