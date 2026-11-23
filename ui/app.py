import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.client import ApiError
from ui.session import (
    client,
    current_user,
    is_admin,
    needs_setup,
    restore_session,
    set_up,
    sign_in,
    sign_out,
    signed_in,
    sync_cookie,
)
from ui.views import ask, catalog, history, knowledge, people, settings, sources

st.set_page_config(page_title="Datalyst", page_icon="📊", layout="wide")
restore_session()
sync_cookie()


def login_page() -> None:
    _, middle, _ = st.columns([1, 1.2, 1])
    with middle:
        st.title("Datalyst")
        st.caption("Ask your data questions in plain English.")
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", width="stretch", type="primary"):
                try:
                    sign_in(email, password)
                except ApiError as e:
                    st.error(e.message)
                else:
                    st.rerun()


def setup_page() -> None:
    _, middle, _ = st.columns([1, 1.2, 1])
    with middle:
        st.title("Welcome to Datalyst")
        st.caption(
            "Create the first admin account. You will then choose the AI model and "
            "connect your databases, all from this web interface."
        )
        with st.form("setup"):
            name = st.text_input("Your name")
            email = st.text_input("Email")
            password = st.text_input("Password (10+ characters)", type="password")
            again = st.text_input("Password again", type="password")
            if st.form_submit_button(
                "Create admin account", width="stretch", type="primary"
            ):
                if password != again:
                    st.error("The passwords do not match.")
                    return
                try:
                    set_up(email, password, name)
                except ApiError as e:
                    st.error(e.message)
                else:
                    st.session_state.welcome = True
                    st.session_state.go_to_settings = True
                    st.rerun()


def account_page() -> None:
    st.title("Account")
    user = current_user()
    st.markdown(
        f"**{user.get('name') or user.get('email')}** · {user.get('email')} · {user.get('role')}"
    )
    with st.form("password", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password (10+ characters)", type="password")
        again = st.text_input("New password again", type="password")
        if st.form_submit_button("Change password"):
            if new != again:
                st.error("The new passwords do not match.")
            else:
                try:
                    client().change_password(current, new)
                except ApiError as e:
                    st.error(e.message)
                else:
                    sign_out()
                    st.success("Password changed. Sign in again with the new password.")
                    st.rerun()
    if st.button("Sign out"):
        sign_out()
        st.rerun()


if not signed_in():
    first_run = needs_setup()
    st.navigation(
        [
            st.Page(setup_page, title="Set up", icon="🚀")
            if first_run
            else st.Page(login_page, title="Sign in", icon="🔑")
        ],
        position="hidden",
    ).run()
else:
    pages = {
        "Analyse": [
            st.Page(ask.render, title="Ask", icon="💬", default=True),
            st.Page(history.render, title="History", icon="🕘", url_path="history"),
        ],
        "Knowledge": [
            st.Page(catalog.render, title="Catalog", icon="📚", url_path="catalog"),
            st.Page(
                knowledge.render,
                title="Glossary & examples",
                icon="📖",
                url_path="knowledge",
            ),
        ],
    }
    if is_admin():
        pages["Administration"] = [
            st.Page(sources.render, title="Data sources", icon="🗄️", url_path="sources"),
            st.Page(
                people.render, title="People & access", icon="👥", url_path="people"
            ),
            settings_page := st.Page(
                settings.render, title="Settings", icon="⚙️", url_path="settings"
            ),
        ]
    pages["You"] = [
        st.Page(account_page, title="Account", icon="👤", url_path="account")
    ]
    with st.sidebar:
        user = current_user()
        st.caption(f"Signed in as {user.get('email')}")
    navigation = st.navigation(pages)
    if st.session_state.pop("go_to_settings", False) and is_admin():
        st.switch_page(settings_page)
    navigation.run()
