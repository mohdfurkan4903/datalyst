import streamlit as st

from ui.results import show_answer, show_result
from ui.session import call, client, pick_source, succeeded

SUGGESTIONS_SHOWN = 3
NEW_CHAT = None


FILE_TYPES = ["csv", "tsv", "txt", "xlsx", "xlsm"]
INPUT_HINT = "Ask a question about your data, or attach CSV and Excel files with +"


def render() -> None:
    st.title("Ask")
    # A source can only be chosen before the picker is drawn.
    if "switch_source" in st.session_state:
        st.session_state["ask-source"] = st.session_state.pop("switch_source")
    source = pick_source("ask-source")
    state = _state(source["id"]) if source else None
    if source and state is not None:
        _show_chat(source, state)

    pending = st.session_state.pop("pending_question", None)
    entry = st.chat_input(INPUT_HINT, accept_file="multiple", file_type=FILE_TYPES)
    text = (entry.text if entry else None) or pending
    files = list(entry.files) if entry else []
    if files:
        _attach(source, state, files, text)
    elif text:
        if source is None or state is None:
            st.info("Attach a CSV or Excel file with + to get started.")
            return
        _ask(source, state, text)


def _show_chat(source: dict, state: dict) -> None:
    _chat_bar(source["id"], state)
    st.caption(
        "Ask in plain English. Datalyst writes a read-only query, runs it and shows "
        "the result. Follow-up questions build on this chat."
    )
    if not state["past"] and not state["messages"]:
        _suggestions(source["id"])

    for entry in state["past"]:
        _past_entry(entry)
    for index, message in enumerate(state["messages"]):
        with st.chat_message(message["role"]):
            if message.get("files"):
                st.markdown("📎 " + ", ".join(message["files"]))
            elif message["role"] == "user":
                st.markdown(message["text"])
            elif message.get("upload"):
                _show_upload(message["upload"])
            else:
                show_answer(message["answer"], f"{source['id']}-{index}")


def _ask(source: dict, state: dict, question: str) -> None:
    state["messages"].append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Working on it…"):
        answer = call(
            lambda: client().ask(source["id"], question, state["conversation"])
        )
    if answer is not None:
        state["messages"].append({"role": "assistant", "answer": answer})
        if answer.get("conversation_id"):
            state["conversation"] = answer["conversation_id"]
            state["select"] = answer["conversation_id"]
    st.rerun()


def _attach(source: dict | None, state: dict | None, files: list, text: str | None):
    """Files attached with +: they join the user's personal source, and the chat
    moves there so the question can be asked right away."""
    with st.chat_message("assistant"), st.spinner("Reading your files…"):
        result = call(
            lambda: client().attach_files([(f.name, f.getvalue()) for f in files])
        )
    if result is None:
        return
    personal = result["source"]
    if source is None or personal["id"] != source["id"]:
        st.session_state["switch_source"] = personal["name"]
        state = _state(personal["id"])
        state.update(conversation=NEW_CHAT, past=[], messages=[], select=NEW_CHAT)
    state["messages"].append({"role": "user", "files": [f.name for f in files]})
    state["messages"].append({"role": "assistant", "upload": result["tables"]})
    if text:
        st.session_state.pending_question = text
    st.rerun()


def _show_upload(tables: list[dict]) -> None:
    for table in tables:
        verb = "Updated" if table["replaced"] else "Added"
        columns = ", ".join(table["columns"][:8])
        more = "…" if len(table["columns"]) > 8 else ""
        st.markdown(
            f"{verb} **{table['table']}**: {table['rows']:,} rows ({columns}{more})"
        )
    st.caption("Ask anything about them. Attach more files with + to combine them.")


def _state(source_id: int) -> dict:
    """What this browser tab shows for a source: the open chat and its new messages."""
    chats = st.session_state.setdefault("chat_state", {})
    return chats.setdefault(
        source_id, {"conversation": NEW_CHAT, "past": [], "messages": []}
    )


def _picker_key(source_id: int) -> str:
    return f"chat-picker-{source_id}"


def _chat_bar(source_id: int, state: dict) -> None:
    chats = call(lambda: client().conversations(source_id)) or []
    titles = {c["id"]: c["title"] for c in chats}
    options = [NEW_CHAT, *titles]
    if state["conversation"] not in options:
        state["conversation"] = NEW_CHAT
    key = _picker_key(source_id)
    # A widget's value can only be set before it is drawn.
    if "select" in state:
        st.session_state[key] = state.pop("select")
    elif st.session_state.get(key) not in options:
        st.session_state[key] = state["conversation"]
    picker, delete = st.columns([5, 1], vertical_alignment="bottom")
    chosen = picker.selectbox(
        "Chat",
        options,
        format_func=lambda c: "New chat" if c is NEW_CHAT else titles[c],
        key=key,
    )
    if chosen != state["conversation"]:
        _open(state, chosen)
        st.rerun()
    if chosen is not NEW_CHAT:
        with delete.popover("Delete", width="stretch"):
            st.write("Delete this chat? Datalyst will no longer remember it.")
            if st.button(
                "Delete chat", type="primary", key=f"delete-chat-{chosen}"
            ) and succeeded(lambda: client().delete_conversation(chosen)):
                _open(state, NEW_CHAT)
                st.rerun()


def _open(state: dict, conversation_id: int | None) -> None:
    state["conversation"] = conversation_id
    state["messages"] = []
    state["past"] = []
    if conversation_id is not None:
        opened = call(lambda: client().conversation(conversation_id))
        state["past"] = (opened or {}).get("entries", [])


def _past_entry(entry: dict) -> None:
    with st.chat_message("user"):
        st.markdown(entry["question"])
    with st.chat_message("assistant"):
        if entry.get("error"):
            show = st.warning if entry["status"] == "declined" else st.error
            show(entry["error"])
        if not entry.get("sql"):
            return
        shown = st.session_state.setdefault("shown_results", {})
        with st.expander("SQL"):
            st.code(entry["sql"], language="sql")
        if entry["id"] in shown:
            show_result(shown[entry["id"]], f"past-{entry['id']}")
        elif entry["status"] == "success" and st.button(
            f"Show the result ({entry.get('row_count', 0)} rows)",
            key=f"rerun-{entry['id']}",
        ):
            result = call(lambda: client().run_sql(entry["source_id"], entry["sql"]))
            if result is not None:
                shown[entry["id"]] = result
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
