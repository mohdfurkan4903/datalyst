import pandas as pd
import streamlit as st

from ui.session import call, client, is_admin, pick_source


def render() -> None:
    st.title("Glossary & examples")
    source = pick_source("knowledge-source")
    if source is None:
        return
    glossary_tab, examples_tab = st.tabs(["Business terms", "Verified examples"])
    with glossary_tab:
        _glossary(source)
    with examples_tab:
        _examples(source)


def _glossary(source: dict) -> None:
    st.caption(
        "Terms the agent follows when a question uses them, for example what counts as revenue."
    )
    terms = call(lambda: client().glossary(source["id"])) or []
    if terms:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Term": t["term"],
                        "Meaning": t["definition"],
                        "SQL": t["sql_hint"] or "",
                    }
                    for t in terms
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No business terms yet.")
    if not is_admin():
        return
    with st.form("term", clear_on_submit=True):
        st.markdown("**Add or update a term**")
        term = st.text_input("Term", placeholder="revenue")
        definition = st.text_area(
            "What it means", placeholder="Paid and shipped orders, in dollars"
        )
        sql_hint = st.text_input(
            "SQL fragment (optional)", placeholder="status IN ('paid', 'shipped')"
        )
        if st.form_submit_button("Save term") and term and definition:
            call(
                lambda: client().save_term(source["id"], term, definition, sql_hint),
                "Term saved",
            )
            st.rerun()
    if terms:
        remove = st.selectbox("Remove a term", ["—", *(t["term"] for t in terms)])
        if remove != "—" and st.button("Remove term"):
            call(lambda: client().delete_term(source["id"], remove), "Term removed")
            st.rerun()


def _examples(source: dict) -> None:
    st.caption("Trusted questions with their SQL. Similar questions reuse them.")
    examples = call(lambda: client().examples(source["id"])) or []
    if not examples:
        st.info("No verified examples yet. Save good answers from Ask or History.")
    for example in examples:
        with st.expander(example["question"]):
            st.code(example["sql"], language="sql")
            meta = ", ".join(example["tables"])
            if example.get("created_by"):
                meta += f" · by {example['created_by']}"
            st.caption(meta)
            if is_admin() and st.button(
                "Remove", key=f"remove-example-{example['id']}"
            ):
                call(
                    lambda e=example: client().delete_example(source["id"], e["id"]),
                    "Removed",
                )
                st.rerun()
    if not is_admin():
        return
    with st.form("example", clear_on_submit=True):
        st.markdown("**Add an example**")
        question = st.text_input("Question")
        sql = st.text_area("SQL", height=140)
        if st.form_submit_button("Check and save") and question and sql:
            call(
                lambda: client().add_example(source["id"], question, sql),
                "Example saved",
            )
            st.rerun()
