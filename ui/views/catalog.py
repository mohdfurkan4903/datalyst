import pandas as pd
import streamlit as st

from ui.session import call, client, is_admin, pick_source

ORIGIN_LABELS = {"human": "✍️ person", "ai": "🤖 AI", None: ""}
STATUS_LABELS = {"approved": "approved", "auto": "auto", "stale": "⚠️ stale", None: ""}


def render() -> None:
    st.title("Catalog")
    source = pick_source("catalog-source")
    if source is None:
        return
    tabs = st.tabs(
        ["Tables", "Joins", "Needs review"] if is_admin() else ["Tables", "Joins"]
    )
    with tabs[0]:
        _tables(source)
    with tabs[1]:
        _joins(source)
    if is_admin():
        with tabs[2]:
            _stale(source)


def _tables(source: dict) -> None:
    tables = call(lambda: client().tables(source["id"])) or []
    if not tables:
        st.info("The catalog is empty. It fills in after the first sync.")
        return
    search = st.text_input(
        "Search tables, columns and descriptions", key="catalog-search"
    )
    shown = [t for t in tables if _matches(t, search)]
    st.caption(f"{len(shown)} of {len(tables)} table(s)")
    names = [f"{t['schema_name']}.{t['name']}" for t in shown]
    if not names:
        return
    chosen = st.selectbox("Table", names, key="catalog-table")
    detail = call(lambda: client().table(source["id"], chosen))
    if detail:
        _table_detail(source, chosen, detail)


def _table_detail(source: dict, name: str, detail: dict) -> None:
    st.subheader(name)
    meta = [detail["kind"].replace("_", " ")]
    if detail.get("row_estimate") is not None:
        meta.append(f"about {detail['row_estimate']:,} rows")
    origin = ORIGIN_LABELS.get(detail.get("description_origin"), "")
    if origin:
        meta.append(f"description by {origin}")
    if detail.get("description_status") == "stale":
        meta.append("⚠️ changed since described")
    st.caption(" · ".join(meta))

    description = detail.get("description") or detail.get("comment") or ""
    if is_admin():
        edited = st.text_area("Description", description, key=f"desc-{name}", height=90)
        if (
            st.button("Save description", key=f"save-desc-{name}")
            and edited != description
        ):
            call(
                lambda: client().set_table_description(
                    source["id"], name, edited.strip()
                ),
                "Description saved",
            )
    elif description:
        st.markdown(description)
    if detail.get("synonyms"):
        st.caption("Also called: " + ", ".join(detail["synonyms"]))

    frame = pd.DataFrame(
        [
            {
                "Column": c["name"],
                "Type": c["data_type"],
                "Key": "PK" if c["is_primary_key"] else "",
                "Meaning": c["semantic_type"] or "",
                "Description": c["description"] or c["comment"] or "",
                "By": ORIGIN_LABELS.get(c["description_origin"], ""),
                "Status": STATUS_LABELS.get(c["description_status"], ""),
                "Values": ", ".join(c["sample_values"][:8]),
            }
            for c in detail["columns"]
        ]
    )
    if not is_admin():
        st.dataframe(frame, width="stretch", hide_index=True)
        return
    edited_frame = st.data_editor(
        frame,
        width="stretch",
        hide_index=True,
        disabled=[c for c in frame.columns if c != "Description"],
        key=f"columns-{name}",
    )
    changed = edited_frame[edited_frame["Description"] != frame["Description"]]
    if not changed.empty and st.button(
        f"Save {len(changed)} column description(s)", key=f"save-cols-{name}"
    ):
        for _, row in changed.iterrows():
            call(
                lambda row=row: client().set_column_description(
                    source["id"], name, row["Column"], row["Description"].strip()
                )
            )
        st.toast("Column descriptions saved")
        st.rerun()


def _joins(source: dict) -> None:
    joins = call(lambda: client().relationships(source["id"])) or []
    if not joins:
        st.info("No joins known yet.")
        return
    origin = st.radio(
        "Show",
        ["All", "Inferred", "Declared", "Added by people", "Rejected"],
        horizontal=True,
    )
    wanted = {
        "Inferred": lambda j: j["origin"] == "inferred" and j["status"] == "active",
        "Declared": lambda j: j["origin"] == "declared",
        "Added by people": lambda j: j["origin"] == "human",
        "Rejected": lambda j: j["status"] == "rejected",
    }.get(origin, lambda j: True)
    shown = [j for j in joins if wanted(j)]
    frame = pd.DataFrame(
        [
            {
                "id": j["id"],
                "From": j["child"],
                "To": j["parent"],
                "Origin": j["origin"],
                "Confidence": round(j["confidence"], 2),
                "Status": j["status"],
                "Evidence": _evidence(j["evidence"]),
            }
            for j in shown
        ]
    )
    if frame.empty:
        st.caption("Nothing to show.")
    else:
        st.dataframe(frame.drop(columns=["id"]), width="stretch", hide_index=True)
    if not is_admin():
        return
    with st.expander("Review a join"):
        labels = {f"{j['child']} → {j['parent']} ({j['status']})": j for j in joins}
        picked = st.selectbox("Join", list(labels))
        if picked:
            join = labels[picked]
            left, right = st.columns(2)
            if left.button("Reject", disabled=join["status"] == "rejected"):
                call(
                    lambda: client().set_relationship_status(
                        source["id"], join["id"], "rejected"
                    ),
                    "Join rejected",
                )
                st.rerun()
            if right.button("Restore", disabled=join["status"] == "active"):
                call(
                    lambda: client().set_relationship_status(
                        source["id"], join["id"], "active"
                    ),
                    "Join restored",
                )
                st.rerun()
    with st.expander("Add a join by hand"):
        child = st.text_input("From column", placeholder="public.orders.customer_ref")
        parent = st.text_input("To column", placeholder="public.customers.id")
        if st.button("Add join") and child and parent:
            call(
                lambda: client().add_relationship(source["id"], child, parent),
                "Join added",
            )
            st.rerun()


def _stale(source: dict) -> None:
    items = call(lambda: client().stale(source["id"])) or []
    if not items:
        st.success("Nothing needs review. Descriptions match the current schema.")
        return
    st.caption("These descriptions were written before the table or column changed.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Table": i["table_name"],
                    "Column": i["column_name"] or "(table)",
                    "Description": i["description"],
                    "By": ORIGIN_LABELS.get(i["description_origin"], ""),
                }
                for i in items
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def _matches(table: dict, search: str) -> bool:
    if not search:
        return True
    needle = search.lower()
    haystack = [
        table["name"],
        table.get("description") or "",
        *table.get("synonyms", []),
    ]
    for column in table["columns"]:
        haystack += [column["name"], column.get("description") or ""]
    return any(needle in text.lower() for text in haystack)


def _evidence(evidence: dict) -> str:
    if not evidence:
        return ""
    reason = evidence.get("reason", "")
    if "sampled" in evidence:
        return f"{reason}; {evidence.get('matched')}/{evidence.get('sampled')} values found"
    return reason
