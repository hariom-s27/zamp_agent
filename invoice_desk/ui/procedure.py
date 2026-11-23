"""Page, Procedure (round 4, change request step 1): propose a change to one of six numbers,
replay it against every past document on scratch books, and save it. Nothing goes in force."""
from __future__ import annotations

import json
from decimal import Decimal

import streamlit as st

from .. import changes as cr
from .. import textutil as T
from . import common as C
from . import ui_kit as K

NONE = "(no change)"


def load_procedure_example(store, pipeline) -> None:
    from .. import samples
    by_id = {s["id"]: s for s in samples.load_manifest()}
    for sid in ("01_happy_path_furniture", "07_E4a_tax_inclusive_within_tolerance",
                "08_E4b_tax_inclusive_over_tolerance"):
        s = by_id[sid]
        pipeline.run(samples.sample_bytes(s), samples.received_name(s), source="cli")


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Procedure")
    st.caption(cr.CAPTION)
    if not rules.changes.enabled:
        st.info(cr.OFF)
        return
    head = st.container(horizontal=True, vertical_alignment="center")
    head.badge(f"In force: rule book {rules.version}", icon=":material/rule:", color="gray")
    head.markdown("No change request is in force.")
    st.caption(cr.STEP_TWO)

    st.markdown("#### Propose a change " + K.actor_chip("person", "One person proposes"))
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
    if not store.list_runs():
        st.info("There are no past invoices on record to replay against. Process invoices first, or load the example invoices.")
        if st.button("Load three example invoices", key="cr_load_example", icon=":material/play_circle:"):
            load_procedure_example(store, C.get_pipeline(store=store))
            st.rerun()
    if st.button("Replay against past invoices", key="cr_replay", icon=":material/replay:"):
        try:
            bar = st.progress(0.0, text="Starting")

            def step(name: str, i: int, n: int) -> None:
                done = (i - 1 + (n if name == "change" else 0)) / (2 * n)
                which = "with the rules now" if name == "now" else "with the change"
                bar.progress(done, text=f"Checking invoice {i} of {n}, {which}")

            st.session_state["cr_result"] = cr.replay(store, C.get_master(), rules, proposed, C.business_date(),
                                                      progress=step)
            bar.progress(1.0, text="Done")
            st.session_state["cr_for"] = {k: str(v) for k, v in proposed.items()}
        except cr.ChangeError as exc:
            st.error(str(exc))
    result = st.session_state.get("cr_result")
    if result and st.session_state.get("cr_for") == {k: str(v) for k, v in proposed.items()}:
        with st.container(border=True):
            C.text_block(cr.describe(result["changes"]))
            st.markdown(f"**{result['summary']}**")
            st.markdown(K.chip_md("hold" if result["differ"] else "resolved",
                                  T.plural(len(result["differ"]), "decision") + " would change" if result["differ"]
                                  else "No decision would change"))
        if result["differ"]:
            C.table({"Document": C.display_name(d["file_name"]), "Vendor": d["vendor"],
                     "Amount": C.money(d["amount"]) if d["amount"] else "-",
                     "Decision now": C.verdict_display_label(d["now"], icon=True),
                     "With the change": C.verdict_display_label(d["with_change"], icon=True),
                     "Reasons that go away": "; ".join(rules.reason(c).title for c in d["gone"]) or "-",
                     "Reasons that appear": "; ".join(rules.reason(c).title for c in d["new"]) or "-"}
                    for d in result["differ"])
        C.text_block(result["direction"])
        if result["limit_hit"]:
            st.warning(f"The test stopped at {rules.changes.replay_max_documents} invoices, the most it checks at once.")
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
        st.markdown("#### Waiting for a second person " + K.chip_md("due_soon", "Not in force"))
        for row in [r for r in saved if r["status"] == "PROPOSED"]:
            with st.container(border=True):
                diff = json.loads(row["changes_json"])
                replay = json.loads(row["replay_json"] or "{}")
                st.markdown(f"**{row['change_id']}**, proposed by {row['proposed_by']}")
                C.text_block(cr.describe(diff))
                C.text_block(f"Why: {row['reason']}")
                C.text_block(f"{replay.get('summary', '')} {replay.get('direction', '')}")
                st.caption(cr.STEP_TWO)
        st.markdown("#### History")
        C.table({"Change": r["change_id"], "What": cr.describe(json.loads(r["changes_json"])),
                 "Proposed by": r["proposed_by"], "Status": r["status"].lower()} for r in saved)
