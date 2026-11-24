import pandas as pd
import streamlit as st

from ui.results import feedback_bar
from ui.session import call, client, is_admin

STATUS_ICONS = {"success": "✅", "declined": "🚫", "error": "⚠️"}
FEEDBACK_ICONS = {1: "👍", -1: "👎"}


def render() -> None:
    st.title("History")
    sources = {s["id"]: s["name"] for s in call(client().sources) or []}
    filters = st.columns([2, 2])
    names = ["All sources", *sources.values()]
    chosen = filters[0].selectbox("Source", names)
    source_id = next((i for i, n in sources.items() if n == chosen), None)
    everyone = is_admin() and filters[1].toggle("Everyone's questions")

    entries = (
        call(
            lambda: client().history(
                source_id=source_id, everyone=everyone or None, limit=100
            )
        )
        or []
    )
    if not entries:
        st.info("No questions yet.")
        return

    frame = pd.DataFrame(
        [
            {
                "When": pd.to_datetime(e["created_at"]).tz_convert(None),
                "Source": sources.get(e["source_id"], "—"),
                "Question": e["question"],
                "Status": f"{STATUS_ICONS.get(e['status'], '')} {e['status']}",
                "Feedback": FEEDBACK_ICONS.get(e["feedback"], ""),
            }
            for e in entries
        ]
    )
    selection = st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "When": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
        },
    )
    rows = selection.selection.rows if selection else []
    if not rows:
        st.caption("Select a question to see how it was answered.")
        return
    entry = entries[rows[0]]
    st.subheader(entry["question"])
    if entry.get("rewritten_question"):
        st.caption(f"Interpreted as: {entry['rewritten_question']}")
    if entry.get("error"):
        st.warning(entry["error"])
    if entry.get("sql"):
        st.code(entry["sql"], language="sql")
    feedback_bar(
        entry["id"], f"history-{entry['id']}", can_promote=entry["status"] == "success"
    )
