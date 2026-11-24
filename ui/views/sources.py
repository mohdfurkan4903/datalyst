import pandas as pd
import streamlit as st

from ui.session import call, client, succeeded

POLICIES = {
    "rows": "Statistics, common values and a few sample rows (best descriptions)",
    "stats": "Statistics and common values, no rows",
    "names": "Table and column names only, no data",
}
FILE_TYPES = ["csv", "tsv", "txt"]
STATUS = {
    "ready": "🟢 Ready",
    "error": "🔴 Error",
    "new": "🟡 New",
    "syncing": "🟡 Syncing",
}


def render() -> None:
    st.title("Data sources")
    sources = call(client().sources) or []
    if sources:
        _table(sources)
        names = [s["name"] for s in sources]
        chosen = st.selectbox("Manage a source", names)
        _manage(next(s for s in sources if s["name"] == chosen))
    else:
        st.info("No data sources yet. Add your first database below.")
    _add_source()


def _table(sources: list[dict]) -> None:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Name": s["name"],
                    "Type": "Uploaded files" if s["kind"] == "files" else "Database",
                    "Status": STATUS.get(s["status"], s["status"]),
                    "Who can use it": "All users"
                    if s["open_to_all"]
                    else "Selected users",
                    "Last checked": _time(s.get("last_checked_at")),
                }
                for s in sources
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def _manage(source: dict) -> None:
    status = call(lambda: client().source_status(source["id"])) or {}
    info = status.get("source", source)
    if info.get("status_detail"):
        st.error(info["status_detail"])
    if status and not status.get("workers"):
        st.warning(
            "The background worker is not running, so new tables and schema "
            "changes are not picked up."
        )

    sync, remove, _ = st.columns([1, 1, 2])
    if sync.button("Sync now", width="stretch"):
        call(lambda: client().sync_source(source["id"]), "Sync started")
    with remove.popover("Delete", width="stretch"):
        st.write(
            f"Delete **{source['name']}** and everything Datalyst learned about it?"
        )
        if st.button(
            "Delete source", type="primary", key=f"delete-{source['id']}"
        ) and succeeded(lambda: client().delete_source(source["id"]), "Deleted"):
            st.rerun()

    if source["kind"] == "files":
        _files(source)
    _access(source)

    with st.expander("Connection and privacy"), st.form(f"edit-{source['id']}"):
        policy = st.radio(
            "What may be sent to the AI model to describe tables",
            list(POLICIES),
            index=list(POLICIES).index(source["llm_data_policy"]),
            format_func=POLICIES.get,
        )
        url = (
            ""
            if source["kind"] == "files"
            else st.text_input(
                "New connection URL (leave empty to keep the current one)",
                type="password",
            )
        )
        if st.form_submit_button("Save"):
            fields = {"llm_data_policy": policy}
            if url:
                fields["url"] = url.strip()
            if call(lambda: client().update_source(source["id"], **fields), "Saved"):
                st.rerun()


def _files(source: dict) -> None:
    files = call(lambda: client().source_files(source["id"])) or []
    st.subheader("Files")
    st.caption(
        "Each file is a table named after the file. Uploading a file with the "
        "same name replaces that table."
    )
    if files:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Table": f["table"],
                        "Size": f"{f['size_bytes'] / 1024:,.0f} KB",
                        "Updated": _time(f["updated_at"]),
                    }
                    for f in files
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        with st.popover("Remove a table"):
            table = st.selectbox("Table", [f["table"] for f in files])
            if st.button(
                "Remove table", type="primary", key=f"remove-file-{source['id']}"
            ) and succeeded(
                lambda: client().delete_source_file(source["id"], table), "Removed"
            ):
                st.rerun()
    with st.form(f"add-file-{source['id']}", clear_on_submit=True):
        upload = st.file_uploader("Add a file", type=FILE_TYPES)
        if st.form_submit_button("Upload") and upload is not None:
            added = call(
                lambda: client().add_source_file(
                    source["id"], upload.name, upload.getvalue()
                ),
                "File added; it appears within a minute",
            )
            if added:
                st.rerun()


def _access(source: dict) -> None:
    access = call(lambda: client().source_access(source["id"]))
    users = [u for u in call(client().users) or [] if u["role"] != "admin"]
    if access is None:
        return
    st.subheader("Who can use it")
    with st.form(f"access-{source['id']}"):
        everyone = st.radio(
            "Access",
            [True, False],
            index=0 if access["open_to_all"] else 1,
            format_func=lambda open_: "All users" if open_ else "Only selected users",
            horizontal=True,
            label_visibility="collapsed",
        )
        by_label = {f"{u['name'] or u['email']} ({u['email']})": u["id"] for u in users}
        chosen = st.multiselect(
            "Selected users",
            list(by_label),
            default=[k for k, v in by_label.items() if v in access["user_ids"]],
            help="Used only when access is limited. Admins can always use every source.",
        )
        if st.form_submit_button("Save access"):
            saved = call(
                lambda: client().set_source_access(
                    source["id"], everyone, [by_label[k] for k in chosen]
                ),
                "Access saved",
            )
            if saved:
                st.rerun()


def _add_source() -> None:
    with st.expander("Add a data source"):
        database, upload = st.tabs(["Database", "Upload a file"])
        with database:
            _add_database()
        with upload:
            _upload_source()


def _upload_source() -> None:
    with st.form("upload-source"):
        st.caption(
            "Upload a CSV file to ask questions about it. You can add more files "
            "to the source afterwards."
        )
        name = st.text_input("Name", placeholder="sales-2024", key="upload-name")
        file = st.file_uploader("CSV file", type=FILE_TYPES)
        policy = st.radio(
            "What may be sent to the AI model to describe tables",
            list(POLICIES),
            format_func=POLICIES.get,
            key="upload-policy",
        )
        if (
            st.form_submit_button("Upload and add", type="primary")
            and name
            and file is not None
        ):
            created = call(
                lambda: client().upload_source(
                    name.strip(), file.name, file.getvalue(), policy
                ),
                "Source added; its tables appear within a minute",
            )
            if created:
                st.rerun()


def _add_database() -> None:
    with st.form("add-source"):
        st.caption(
            "Use a database login that can only read. Datalyst blocks writes too, "
            "but a read-only login is the strongest guarantee."
        )
        name = st.text_input("Name", placeholder="sales")
        url = st.text_input(
            "Connection URL",
            type="password",
            placeholder="postgresql://user:password@host:5432/database  or  mysql://user:password@host:3306/database",
        )
        policy = st.radio(
            "What may be sent to the AI model to describe tables",
            list(POLICIES),
            format_func=POLICIES.get,
        )
        if st.form_submit_button("Test and add", type="primary") and name and url:
            created = call(
                lambda: client().create_source(
                    name=name.strip(), url=url.strip(), llm_data_policy=policy
                ),
                "Source added; its tables appear within a minute",
            )
            if created:
                st.rerun()


def _time(value: str | None) -> str:
    if not value:
        return "never"
    return pd.to_datetime(value).tz_convert(None).strftime("%Y-%m-%d %H:%M")
