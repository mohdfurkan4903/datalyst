import pandas as pd
import streamlit as st

from ui.session import call, client, current_user, succeeded

ROLES = {"analyst": "User", "admin": "Admin"}


def render() -> None:
    st.title("Users")
    users = call(client().users) or []
    if users:
        _table(users)
        _manage(users)
    _add_user()


def _table(users: list[dict]) -> None:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Name": u["name"] or "—",
                    "Email": u["email"],
                    "Role": ROLES.get(u["role"], u["role"]),
                    "Status": "Active" if u["active"] else "Blocked",
                    "Last sign-in": _time(u.get("last_login_at")),
                }
                for u in users
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def _manage(users: list[dict]) -> None:
    by_email = {u["email"]: u for u in users}
    email = st.selectbox("Manage a user", list(by_email))
    user = by_email[email]
    is_me = user["id"] == current_user().get("id")
    if is_me:
        st.caption("This is you. Another admin can block or remove your account.")

    block, role, remove = st.columns(3)
    if user["active"]:
        if block.button("Block", width="stretch", disabled=is_me, help=_BLOCK_HELP):
            _update(user, "User blocked", active=False)
    elif block.button("Unblock", width="stretch"):
        _update(user, "User unblocked", active=True)

    other_role = "analyst" if user["role"] == "admin" else "admin"
    if role.button(
        f"Make {ROLES[other_role].lower()}", width="stretch", disabled=is_me
    ):
        _update(user, "Role changed", role=other_role)

    with remove.popover("Remove", width="stretch", disabled=is_me):
        st.write(f"Remove **{user['email']}**? This cannot be undone.")
        if st.button(
            "Remove user", type="primary", key=f"remove-{user['id']}"
        ) and succeeded(lambda: client().delete_user(user["id"]), "User removed"):
            st.rerun()

    with st.form(f"password-{user['id']}", clear_on_submit=True):
        password = st.text_input("Set a new password (10+ characters)", type="password")
        if st.form_submit_button("Set password") and password:
            _update(user, "Password changed", password=password)


_BLOCK_HELP = "A blocked user is signed out at once and cannot sign in."


def _update(user: dict, message: str, **fields) -> None:
    if call(lambda: client().update_user(user["id"], **fields), message):
        st.rerun()


def _add_user() -> None:
    with st.expander("Add a user"), st.form("add-user", clear_on_submit=True):
        name = st.text_input("Name")
        email = st.text_input("Email")
        password = st.text_input("Password (10+ characters)", type="password")
        role = st.radio("Role", list(ROLES), format_func=ROLES.get, horizontal=True)
        if st.form_submit_button("Add user", type="primary") and email and password:
            created = call(
                lambda: client().create_user(
                    email=email, name=name, password=password, role=role
                ),
                "User added",
            )
            if created:
                st.rerun()


def _time(value: str | None) -> str:
    if not value:
        return "never"
    return pd.to_datetime(value).tz_convert(None).strftime("%Y-%m-%d %H:%M")
