import pandas as pd
import streamlit as st

from ui.session import call, client

POLICIES = {
    "rows": "Statistics, common values and a few sample rows (best descriptions)",
    "stats": "Statistics and common values, no rows",
    "names": "Table and column names only, no data",
}
STATUS_COLORS = {"ready": "🟢", "error": "🔴", "new": "🟡", "syncing": "🟡"}


def render() -> None:
    st.title("Data sources")
    sources = call(client().sources) or []
    if sources:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Name": s["name"],
                        "Status": f"{STATUS_COLORS.get(s['status'], '')} {s['status']}",
                        "Schemas": ", ".join(s["include_schemas"]) or "all",
                        "Data sent to LLM": s["llm_data_policy"],
                        "Last checked": _time(s.get("last_checked_at")),
                        "Last change": _time(s.get("last_synced_at")),
                    }
                    for s in sources
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        names = [s["name"] for s in sources]
        chosen = st.selectbox("Manage", names)
        _manage(next(s for s in sources if s["name"] == chosen))
    else:
        st.info("No data sources yet. Add your first database below.")
    _add_form()


def _manage(source: dict) -> None:
    status = call(lambda: client().source_status(source["id"])) or {}
    info = status.get("source", source)
    if info.get("status_detail"):
        st.error(info["status_detail"])
    jobs = status.get("jobs", {})
    metrics = st.columns(4)
    metrics[0].metric("Catalog version", info.get("catalog_version", 0))
    metrics[1].metric("Queued jobs", jobs.get("queued", 0))
    metrics[2].metric("Failed jobs", jobs.get("failed", 0))
    metrics[3].metric("Workers running", len(status.get("workers", [])))
    if not status.get("workers"):
        st.warning("No sync worker is running. Start one with: datalyst worker")

    actions = st.columns(3)
    if actions[0].button("Check for schema changes", width="stretch"):
        call(lambda: client().sync_source(source["id"]), "Check queued")
    if actions[1].button("Refresh AI descriptions", width="stretch"):
        call(lambda: client().describe_source(source["id"]), "Descriptions queued")
    if actions[2].button("Re-describe everything", width="stretch"):
        call(lambda: client().describe_source(source["id"], force=True), "Queued")

    changes = status.get("recent_changes") or []
    if changes:
        st.markdown("**Recent schema changes**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "When": _time(c["detected_at"]),
                        "Table": f"{c['schema_name']}.{c['table_name']}",
                        "Change": c["change"],
                        "Details": ", ".join(
                            f"{k}: {v}" for k, v in c["details"].items()
                        ),
                    }
                    for c in changes
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    failed = [j for j in status.get("recent_jobs", []) if j["status"] == "failed"]
    if failed:
        with st.expander(f"{len(failed)} failed job(s)"):
            for job in failed:
                st.caption(f"{job['kind']} {job['target']}: {job['error']}")

    with st.expander("Settings"):
        with st.form(f"settings-{source['id']}"):
            policy = st.radio(
                "What may be sent to the LLM to describe tables",
                list(POLICIES),
                index=list(POLICIES).index(source["llm_data_policy"]),
                format_func=POLICIES.get,
            )
            interval = st.number_input(
                "Check for schema changes every (seconds)",
                min_value=60,
                value=source["sync_interval_seconds"],
                step=60,
            )
            url = st.text_input(
                "New connection URL (leave empty to keep the current one)",
                type="password",
            )
            if st.form_submit_button("Save settings"):
                fields = {
                    "llm_data_policy": policy,
                    "sync_interval_seconds": int(interval),
                }
                if url:
                    fields["url"] = url
                call(
                    lambda: client().update_source(source["id"], **fields),
                    "Settings saved",
                )
                st.rerun()
        confirm = st.text_input(f"Type {source['name']} to delete this source")
        if st.button(
            "Delete source", type="primary", disabled=confirm != source["name"]
        ):
            call(lambda: client().delete_source(source["id"]), "Source deleted")
            st.rerun()


def _add_form() -> None:
    with st.expander("Add a data source", expanded=False), st.form("add-source"):
        st.caption(
            "Use a database user that can only read. Datalyst also blocks writes itself, "
            "but a read-only user is the strongest guarantee."
        )
        name = st.text_input("Name", placeholder="sales")
        url = st.text_input(
            "Connection URL",
            type="password",
            placeholder="postgresql://readonly_user:password@host:5432/database",
        )
        schemas = st.text_input("Schemas to include (comma separated, empty for all)")
        policy = st.radio(
            "What may be sent to the LLM to describe tables",
            list(POLICIES),
            format_func=POLICIES.get,
        )
        if st.form_submit_button("Test connection and add") and name and url:
            created = call(
                lambda: client().create_source(
                    name=name.strip(),
                    url=url.strip(),
                    include_schemas=[
                        s.strip() for s in schemas.split(",") if s.strip()
                    ],
                    llm_data_policy=policy,
                ),
                "Source added; the worker will read its schema shortly",
            )
            if created:
                st.rerun()


def _time(value: str | None) -> str:
    if not value:
        return "never"
    return pd.to_datetime(value).tz_convert(None).strftime("%Y-%m-%d %H:%M")
