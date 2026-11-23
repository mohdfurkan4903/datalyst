import pandas as pd
import streamlit as st

from ui.results import feedback_bar
from ui.session import call, client, is_admin

STATUS_ICONS = {"success": "✅", "declined": "🚫", "error": "⚠️"}
FEEDBACK_ICONS = {1: "👍", -1: "👎"}


def render() -> None:
    st.title("History")
    sources = {s["id"]: s["name"] for s in call(client().sources) or []}
    filters = st.columns([2, 2, 1])
    names = ["All sources", *sources.values()]
    chosen = filters[0].selectbox("Source", names)
    source_id = next((i for i, n in sources.items() if n == chosen), None)
    everyone = is_admin() and filters[1].toggle("Everyone's questions")
    limit = filters[2].number_input(
        "Show", min_value=10, max_value=200, value=50, step=10
    )

    entries = (
        call(
            lambda: client().history(
                source_id=source_id, everyone=everyone or None, limit=limit
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
                "Rows": e["row_count"],
                "Seconds": round((e["duration_ms"] or 0) / 1000, 1),
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
        st.caption("Select a row to see its SQL and rerun it.")
        return
    entry = entries[rows[0]]
    st.subheader(entry["question"])
    if entry.get("rewritten_question"):
        st.caption(f"Interpreted as: {entry['rewritten_question']}")
    if entry.get("error"):
        st.warning(entry["error"])
    if entry.get("sql"):
        st.code(entry["sql"], language="sql")
        if entry.get("tables"):
            st.caption("Tables: " + ", ".join(entry["tables"]))
        if st.button("Run this SQL again") and entry["source_id"] is not None:
            result = call(lambda: client().run_sql(entry["source_id"], entry["sql"]))
            if result:
                from ui.results import show_result

                show_result(result, f"history-{entry['id']}")
    feedback_bar(
        entry["id"], f"history-{entry['id']}", can_promote=entry["status"] == "success"
    )
