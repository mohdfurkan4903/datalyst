from typing import Any

import streamlit as st

from ui.session import call, client

TABS = {
    "llm": "Model",
    "describe": "Descriptions",
    "embeddings": "Embeddings",
    "agent": "Answers",
    "retrieval": "Retrieval",
    "query": "Queries",
    "sync": "Worker",
    "auth": "Sign-in",
}
TESTABLE = {"llm", "describe", "embeddings"}
LABELS = {"api_key": "API key", "base_url": "Base URL", "top_k": "Tables per question"}
SOURCE_NOTES = {
    "default": "default",
    "metastore": "set here",
    "env": "set by environment variable",
}


MAIN_SECTIONS = ("llm", "embeddings")


def render() -> None:
    st.title("Settings")
    if st.session_state.pop("welcome", False):
        st.success(
            "Datalyst is set up. Choose the AI model and add its API key, then add "
            "a database under **Data sources**."
        )
    rows = call(client().settings)
    if rows is None:
        return
    by_section: dict[str, list[dict]] = {}
    for row in rows:
        by_section.setdefault(row["section"], []).append(row)

    main = [name for name in MAIN_SECTIONS if name in by_section]
    advanced = [name for name in by_section if name not in MAIN_SECTIONS]
    tabs = st.tabs([*(TABS.get(name, name) for name in main), "Advanced"])
    for tab, section in zip(tabs, main, strict=False):
        with tab:
            _section(section, by_section[section])
    with tabs[-1]:
        st.caption(
            "Changes apply within a few seconds, with no restart. An option set by "
            "an environment variable (`DATALYST__SECTION__KEY`) is locked here."
        )
        for section in advanced:
            st.subheader(TABS.get(section, section))
            _section(section, by_section[section])
        st.subheader("Export")
        _downloads()


def _section(section: str, options: list[dict]) -> None:
    st.caption(options[0]["section_title"])
    _section_form(section, options)
    if section in TESTABLE:
        _test_button(section)


def _downloads() -> None:
    left, right, _ = st.columns([1, 1, 2])
    ini = call(lambda: client().export_settings("ini"))
    env = call(lambda: client().export_settings("env"))
    if ini is not None:
        left.download_button(
            "Download config",
            ini,
            file_name="datalyst.cfg",
            mime="text/plain",
            help="Every option with its value and where it comes from, like "
            "'airflow config list'. Secrets are hidden.",
            width="stretch",
        )
    if env is not None:
        right.download_button(
            "Download .env",
            env,
            file_name="datalyst.env",
            mime="text/plain",
            help="The options changed from their defaults, as DATALYST__* variables. "
            "Secrets are left out.",
            width="stretch",
        )


def _section_form(section: str, options: list[dict]) -> None:
    with st.form(f"settings-{section}", clear_on_submit=True, border=False):
        entered: dict[str, Any] = {}
        removed: list[str] = []
        for row in options:
            value, remove = _widget(row)
            entered[row["name"]] = value
            if remove:
                removed.append(row["name"])
        save, reset = st.columns([1, 1])
        submitted = save.form_submit_button("Save", type="primary", width="stretch")
        reset_all = reset.form_submit_button(
            "Reset section to defaults", width="stretch"
        )

    if submitted:
        changes = {
            name: value
            for name, value in entered.items()
            if _changed(next(r for r in options if r["name"] == name), value)
        }
        if not changes and not removed:
            st.info("Nothing changed.")
            return
        if call(lambda: client().update_settings(changes, removed), "Settings saved"):
            st.rerun()
    if reset_all:
        names = [row["name"] for row in options if row["source"] == "metastore"]
        if call(lambda: client().update_settings({}, names), "Section reset"):
            st.rerun()


def _widget(row: dict) -> tuple[Any, bool]:
    locked = row["source"] == "env"
    label = LABELS.get(row["key"], row["key"].replace("_", " ").capitalize())
    note = SOURCE_NOTES[row["source"]]
    help_text = f"{row['description']}\n\nEnvironment variable: `{row['env_var']}` · now: {note}"
    key = f"setting-{row['name']}"
    kind, value = row["type"], row["value"]

    if kind == "secret":
        placeholder = (
            "Saved; type a new one to replace it" if row["is_set"] else "Not set"
        )
        typed = st.text_input(
            label,
            type="password",
            placeholder=placeholder,
            help=help_text,
            disabled=locked,
            key=key,
        )
        remove = False
        if row["is_set"] and not locked:
            remove = st.checkbox(f"Remove the saved {label}", key=f"{key}-remove")
        return typed, remove
    if kind == "boolean":
        return st.toggle(
            label, value=bool(value), help=help_text, disabled=locked, key=key
        ), False
    if kind == "integer":
        return (
            st.number_input(
                label,
                value=int(value),
                step=1,
                help=help_text,
                disabled=locked,
                key=key,
            ),
            False,
        )
    if kind == "number":
        return (
            st.number_input(
                label, value=float(value), help=help_text, disabled=locked, key=key
            ),
            False,
        )
    if row["choices"]:
        optional = row["default"] is None
        choices = ([""] if optional else []) + list(row["choices"])
        if value and value not in choices:
            choices.append(value)
        return (
            st.selectbox(
                label,
                choices,
                index=choices.index(value or ""),
                format_func=lambda c: c or "(none)",
                help=help_text,
                disabled=locked,
                key=key,
            ),
            False,
        )
    return (
        st.text_input(
            label, value=value or "", help=help_text, disabled=locked, key=key
        ),
        False,
    )


def _changed(row: dict, entered: Any) -> bool:
    if row["source"] == "env":
        return False
    if row["type"] == "secret":
        return bool(entered)
    return (entered if entered != "" else None) != row["value"]


def _test_button(section: str) -> None:
    if st.button("Test connection", key=f"test-{section}"):
        with st.spinner("Calling the model..."):
            result = call(lambda: client().test_settings(section))
        if not result:
            return
        if result["ok"]:
            st.success(f"Works: {result['detail']} ({result['duration_ms']} ms)")
        else:
            st.error(result["detail"])
