import streamlit as st

from ui.results import show_answer
from ui.session import call, client, pick_source

SUGGESTIONS_SHOWN = 3


def render() -> None:
    st.title("Ask")
    source = pick_source("ask-source")
    if source is None:
        return
    chats = st.session_state.setdefault("chats", {})
    chat = chats.setdefault(source["id"], [])

    toolbar = st.columns([4, 1])
    toolbar[0].caption(
        "Ask in plain English. Datalyst writes a read-only query, runs it and shows the result."
    )
    if chat and toolbar[1].button("New chat", width="stretch"):
        chat.clear()
        st.rerun()

    if not chat:
        _suggestions(source["id"])

    for index, message in enumerate(chat):
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["text"])
            else:
                show_answer(message["answer"], f"{source['id']}-{index}")

    pending = st.session_state.pop("pending_question", None)
    question = st.chat_input("Ask a question about your data") or pending
    if question:
        chat.append({"role": "user", "text": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"), st.spinner("Working on it…"):
            answer = call(lambda: client().ask(source["id"], question))
        if answer is not None:
            chat.append({"role": "assistant", "answer": answer})
        st.rerun()


def _suggestions(source_id: int) -> None:
    examples = call(lambda: client().examples(source_id)) or []
    if not examples:
        return
    st.caption("Try one of these")
    columns = st.columns(min(len(examples), SUGGESTIONS_SHOWN))
    for column, example in zip(columns, examples[:SUGGESTIONS_SHOWN], strict=False):
        if column.button(
            example["question"],
            key=f"suggest-{example['id']}",
            width="stretch",
        ):
            st.session_state.pending_question = example["question"]
            st.rerun()
