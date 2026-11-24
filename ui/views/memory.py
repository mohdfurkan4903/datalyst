import streamlit as st

from ui.session import call, client, succeeded

ORIGINS = {"user": "You added it", "agent": "Learned from a chat"}


def render() -> None:
    st.title("Memory")
    st.caption(
        "What Datalyst remembers about you in every chat: your definitions and "
        "preferences. Only you can see this. You can also say "
        "“remember that revenue means paid orders” in a chat."
    )
    sources = {s["id"]: s["name"] for s in call(client().sources) or []}
    memories = call(client().memories) or []
    if not memories:
        st.info("Nothing remembered yet.")
    for memory in memories:
        _row(memory, sources)
    if memories:
        _clear()
    _add(sources)


def _row(memory: dict, sources: dict[int, str]) -> None:
    text, scope, delete = st.columns([5, 3, 2], vertical_alignment="center")
    text.write(memory["text"])
    where = (
        "All data sources"
        if memory["source_id"] is None
        else sources.get(memory["source_id"], "A removed source")
    )
    scope.caption(f"{where} · {ORIGINS.get(memory['origin'], memory['origin'])}")
    if delete.button(
        "Forget", key=f"forget-{memory['id']}", width="stretch"
    ) and succeeded(lambda: client().delete_memory(memory["id"])):
        st.rerun()


def _clear() -> None:
    with st.popover("Forget everything"):
        st.write(
            "Remove everything Datalyst remembers about you? This cannot be undone."
        )
        if st.button("Forget everything", type="primary", key="clear-memory") and (
            succeeded(client().clear_memories, "Memory cleared")
        ):
            st.rerun()


def _add(sources: dict[int, str]) -> None:
    with st.expander("Add a memory"), st.form("add-memory", clear_on_submit=True):
        text = st.text_input(
            "What should Datalyst remember?",
            max_chars=300,
            placeholder="Revenue means the total of paid and shipped orders",
        )
        everywhere = st.radio(
            "Applies to",
            [True, False],
            format_func=lambda all_: "All data sources" if all_ else "One data source",
            horizontal=True,
        )
        source = st.selectbox(
            "Data source (when it applies to one)",
            list(sources),
            format_func=sources.get,
        )
        if st.form_submit_button("Remember", type="primary") and text.strip():
            saved = call(
                lambda: client().add_memory(
                    text.strip(), None if everywhere else source
                ),
                "Remembered",
            )
            if saved:
                st.rerun()
