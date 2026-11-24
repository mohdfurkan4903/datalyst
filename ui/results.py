import pandas as pd
import plotly.express as px
import streamlit as st

from ui.charts import choose_chart
from ui.session import call, client, is_admin, succeeded


def result_frame(result: dict) -> pd.DataFrame:
    return pd.DataFrame(result["rows"], columns=result["columns"])


def show_result(result: dict | None, key: str) -> None:
    if not result:
        return
    frame = result_frame(result)
    spec = choose_chart(result["columns"], result["rows"])
    if spec.kind == "metric":
        st.metric(spec.y[0].replace("_", " ").title(), _format(frame.iloc[0, 0]))
        return
    if spec.kind in ("line", "bar") and len(frame) > 1:
        chart_tab, table_tab = st.tabs(["Chart", "Table"])
        with chart_tab:
            draw = px.line if spec.kind == "line" else px.bar
            y = spec.y[0] if len(spec.y) == 1 else spec.y
            figure = draw(frame, x=spec.x, y=y, color=spec.color)
            if spec.kind == "line":
                figure.update_traces(mode="lines+markers")
            figure.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10}, height=360
            )
            st.plotly_chart(figure, width="stretch", key=f"chart-{key}")
        with table_tab:
            _table(frame, result, key)
    else:
        _table(frame, result, key)


def show_answer(answer: dict, key: str) -> None:
    if answer.get("rewritten_question"):
        st.caption(f"Interpreted as: {answer['rewritten_question']}")
    if answer.get("error"):
        if answer["error"].startswith("Declined"):
            st.warning(answer["error"])
        else:
            st.error(answer["error"])
    show_result(answer.get("result"), key)
    if answer.get("query"):
        with st.expander("SQL and details"):
            st.code(answer["query"], language="sql")
            details = [
                f"{answer.get('attempts', 0)} attempt(s)",
                f"{answer.get('duration_ms', 0) / 1000:.1f} s",
            ]
            if answer.get("tables"):
                details.append("tables: " + ", ".join(answer["tables"]))
            st.caption(" · ".join(details))
    if answer.get("id"):
        feedback_bar(answer["id"], key, can_promote=bool(answer.get("result")))


def feedback_bar(entry_id: int, key: str, can_promote: bool) -> None:
    given = st.session_state.setdefault("feedback", {})
    columns = st.columns(
        [1, 1, 3] if not (is_admin() and can_promote) else [1, 1, 1.4, 1.6]
    )
    for column, value, icon, hint in (
        (columns[0], 1, "👍 Correct", "This answer is right"),
        (columns[1], -1, "👎 Wrong", "This answer is wrong"),
    ):
        clicked = column.button(icon, key=f"feedback-{value}-{key}", help=hint)
        if clicked and succeeded(lambda v=value: client().feedback(entry_id, v)):
            given[entry_id] = value
    if (
        is_admin()
        and can_promote
        and columns[2].button(
            "Save as example",
            key=f"promote-{key}",
            help="Reuse this SQL for similar questions",
        )
    ):
        call(lambda: client().promote(entry_id), "Saved as a verified example")
    if entry_id in given:
        columns[-1].caption("Thanks, feedback saved." if given[entry_id] else "")


def _table(frame: pd.DataFrame, result: dict, key: str) -> None:
    st.dataframe(frame, width="stretch", hide_index=True, key=f"table-{key}")
    note = f"{result['row_count']} row(s) in {result.get('elapsed_ms', 0):.0f} ms"
    if result.get("truncated"):
        note += " · truncated"
    left, right = st.columns([3, 1])
    left.caption(note)
    right.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode(),
        file_name="datalyst-result.csv",
        mime="text/csv",
        key=f"csv-{key}",
    )


def _format(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)
