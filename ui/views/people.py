import pandas as pd
import streamlit as st

from ui.session import call, client, current_user


def render() -> None:
    st.title("People & access")
    users_tab, groups_tab, usage_tab = st.tabs(
        ["Users", "Groups & access", "LLM usage"]
    )
    with users_tab:
        _users()
    with groups_tab:
        _groups()
    with usage_tab:
        _usage()


def _users() -> None:
    users = call(client().users) or []
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Email": u["email"],
                    "Name": u["name"],
                    "Role": u["role"],
                    "Active": u["active"],
                    "Last sign-in": (
                        pd.to_datetime(u["last_login_at"])
                        .tz_convert(None)
                        .strftime("%Y-%m-%d %H:%M")
                        if u.get("last_login_at")
                        else "never"
                    ),
                }
                for u in users
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    with st.expander("Add a user"), st.form("add-user", clear_on_submit=True):
        email = st.text_input("Email")
        name = st.text_input("Name")
        password = st.text_input("Temporary password (10+ characters)", type="password")
        role = st.radio("Role", ["analyst", "admin"], horizontal=True)
        if st.form_submit_button("Create user") and email and password:
            call(
                lambda: client().create_user(
                    email=email, name=name, password=password, role=role
                ),
                "User created",
            )
            st.rerun()
    if not users:
        return
    with st.expander("Change a user"):
        by_email = {u["email"]: u for u in users}
        email = st.selectbox("User", list(by_email))
        user = by_email[email]
        with st.form(f"edit-{user['id']}"):
            role = st.radio(
                "Role",
                ["analyst", "admin"],
                index=["analyst", "admin"].index(user["role"]),
                horizontal=True,
            )
            active = st.checkbox("Can sign in", value=user["active"])
            password = st.text_input("New password (optional)", type="password")
            if st.form_submit_button("Save"):
                fields = {"role": role, "active": active}
                if password:
                    fields["password"] = password
                call(lambda: client().update_user(user["id"], **fields), "User updated")
                st.rerun()
        is_me = user["id"] == current_user().get("id")
        if st.button(
            "Delete user",
            disabled=is_me,
            help="You cannot delete yourself" if is_me else None,
        ):
            call(lambda: client().delete_user(user["id"]), "User deleted")
            st.rerun()


def _groups() -> None:
    groups = call(client().groups) or []
    users = {u["id"]: u["email"] for u in call(client().users) or []}
    sources = {s["id"]: s["name"] for s in call(client().sources) or []}
    st.caption(
        "Analysts can only use data sources granted to one of their groups. "
        "Limit a grant to table patterns such as public.ord_* to hide everything else."
    )
    for group in groups:
        with st.expander(f"{group['name']} · {len(group['members'])} member(s)"):
            st.markdown(
                "**Members:** "
                + (", ".join(users.get(m, str(m)) for m in group["members"]) or "none")
            )
            for source_id, patterns in group["grants"].items():
                scope = ", ".join(patterns) if patterns else "all tables"
                st.markdown(f"**{sources.get(int(source_id), source_id)}:** {scope}")
            left, right = st.columns(2)
            with left.form(f"member-{group['id']}"):
                email = st.selectbox(
                    "Add member",
                    [e for i, e in users.items() if i not in group["members"]],
                )
                if st.form_submit_button("Add") and email:
                    user_id = next(i for i, e in users.items() if e == email)
                    call(
                        lambda g=group["id"], u=user_id: client().add_member(g, u),
                        "Member added",
                    )
                    st.rerun()
            with right.form(f"grant-{group['id']}"):
                source = st.selectbox("Grant source", list(sources.values()))
                patterns = st.text_input(
                    "Table patterns (comma separated, empty for all)"
                )
                if st.form_submit_button("Grant") and source:
                    source_id = next(i for i, n in sources.items() if n == source)
                    chosen = [
                        p.strip() for p in patterns.split(",") if p.strip()
                    ] or None
                    call(
                        lambda g=group["id"], s=source_id, c=chosen: client().grant(
                            g, s, c
                        ),
                        "Access granted",
                    )
                    st.rerun()
            if group["members"]:
                remove = st.selectbox(
                    "Remove member",
                    ["—", *(users.get(m, str(m)) for m in group["members"])],
                    key=f"remove-{group['id']}",
                )
                if remove != "—" and st.button(
                    "Remove", key=f"remove-btn-{group['id']}"
                ):
                    user_id = next(i for i, e in users.items() if e == remove)
                    call(
                        lambda g=group["id"], u=user_id: client().remove_member(g, u),
                        "Removed",
                    )
                    st.rerun()
            if group["grants"]:
                revoke = st.selectbox(
                    "Revoke source",
                    ["—", *(sources.get(int(s), str(s)) for s in group["grants"])],
                    key=f"revoke-{group['id']}",
                )
                if revoke != "—" and st.button(
                    "Revoke", key=f"revoke-btn-{group['id']}"
                ):
                    source_id = next(i for i, n in sources.items() if n == revoke)
                    call(
                        lambda g=group["id"], s=source_id: client().revoke(g, s),
                        "Access revoked",
                    )
                    st.rerun()
    with st.form("add-group", clear_on_submit=True):
        name = st.text_input("New group name", placeholder="finance")
        if st.form_submit_button("Create group") and name:
            call(lambda: client().create_group(name), "Group created")
            st.rerun()


def _usage() -> None:
    days = st.slider("Last days", 1, 90, 30)
    rows = call(lambda: client().usage(days)) or []
    if not rows:
        st.info("No LLM calls recorded in this period.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Purpose": r["purpose"],
                    "Model": r["model"],
                    "Calls": r["calls"],
                    "Input tokens": r["input_tokens"],
                    "Output tokens": r["output_tokens"],
                }
                for r in rows
            ]
        ),
        width="stretch",
        hide_index=True,
    )
