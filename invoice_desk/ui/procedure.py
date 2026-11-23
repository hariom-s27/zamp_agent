"""Page, Procedure (round 4, change request step 1): propose a change to one of six numbers,
replay it against every past document on scratch books, and save it. Nothing goes in force."""
from __future__ import annotations

import json
from decimal import Decimal

import streamlit as st

from .. import changes as cr
from . import common as C

NONE = "(no change)"


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Procedure")
    st.caption(cr.CAPTION)
    if not rules.changes.enabled:
        st.info(cr.OFF)
        return
    st.markdown(f"**In force now:** rules.yaml version {rules.version}. No change request is in force.")
    st.caption(cr.STEP_TWO)

    st.subheader("Propose a change")
    keys = list(rules.changes.allowed)
    proposed: dict[str, Decimal] = {}
    for n in range(rules.changes.max_keys_per_request):
        c1, c2 = st.columns([0.6, 0.4])
        key = c1.selectbox(f"What to change ({n + 1})", [NONE] + keys, key=f"cr_key_{n}",
                           format_func=lambda k: k if k == NONE else cr.label(k).capitalize())
        if key != NONE:
            current = cr._get(rules, key)
            text = c2.text_input(f"New value (now {current})", key=f"cr_val_{n}")
            if text.strip():
                try:
                    proposed[key] = Decimal(text.replace(",", "").strip())
                except Exception:
                    st.error(f"The new value for the {cr.label(key)} must be a number.")
    why = st.text_input("Why", key="cr_why")
    if st.button("Replay against past invoices", key="cr_replay", icon=":material/replay:"):
        try:
            with st.spinner("Replaying every past document on scratch books..."):
                st.session_state["cr_result"] = cr.replay(store, C.get_master(), rules, proposed, C.business_date())
            st.session_state["cr_for"] = {k: str(v) for k, v in proposed.items()}
        except cr.ChangeError as exc:
            st.error(str(exc))
    result = st.session_state.get("cr_result")
    if result and st.session_state.get("cr_for") == {k: str(v) for k, v in proposed.items()}:
        C.text_block(cr.describe(result["changes"]))
        st.markdown(f"**{result['summary']}**")
        if result["differ"]:
            C.table({"Document": d["file_name"], "Vendor": d["vendor"], "Amount": C.money(d["amount"]) if d["amount"] else "-",
                     "Decision now": d["now"], "With the change": d["with_change"],
                     "Reasons that disappear": ", ".join(d["gone"]) or "-",
                     "Reasons that appear": ", ".join(d["new"]) or "-"} for d in result["differ"])
        C.text_block(result["direction"])
        if result["limit_hit"]:
            st.warning(f"Replay stopped at {rules.changes.replay_max_documents} documents (changes.replay_max_documents).")
        st.caption(cr.LIMIT_LINE)
        with st.form(key="cr_save_form"):
            who = st.text_input("Your name", key="cr_name")
            st.caption(cr.NAMES)
            if st.form_submit_button("Save as a change request", type="primary"):
                try:
                    cid = cr.save(store, rules, proposed, who, why, result)
                    st.success(f"{cid} saved as proposed. Nothing about how invoices are decided has changed.")
                except cr.ChangeError as exc:
                    st.error(str(exc))
        st.download_button("Download the proposal (YAML difference)",
                           data=cr.yaml_difference(rules, proposed, st.session_state.get("cr_name", ""), why),
                           file_name="change_request.yaml", mime="text/yaml", key="cr_yaml")

    saved = store.change_requests()
    if saved:
        st.subheader("Waiting for a second person")
        for row in [r for r in saved if r["status"] == "PROPOSED"]:
            with st.container(border=True):
                diff = json.loads(row["changes_json"])
                replay = json.loads(row["replay_json"] or "{}")
                st.markdown(f"**{row['change_id']}**, proposed by {C.esc(row['proposed_by'])}", unsafe_allow_html=True)
                C.text_block(cr.describe(diff))
                C.text_block(f"Why: {row['reason']}")
                C.text_block(f"{replay.get('summary', '')} {replay.get('direction', '')}")
                st.caption(cr.STEP_TWO)
        st.subheader("History")
        C.table({"Change": r["change_id"], "What": cr.describe(json.loads(r["changes_json"])),
                 "Proposed by": r["proposed_by"], "Status": r["status"].lower()} for r in saved)
