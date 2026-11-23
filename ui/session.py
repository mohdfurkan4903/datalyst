import os
import re
import uuid
from collections.abc import Callable
from typing import Any

import streamlit as st

from ui.client import ApiError, DatalystClient

API_URL = os.getenv("DATALYST_UI_API_URL", "http://localhost:8000")
SESSION_COOKIE = "datalyst_session"
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9._-]*")


def client() -> DatalystClient:
    if "client" not in st.session_state:
        st.session_state.client = DatalystClient(API_URL)
    return st.session_state.client


def signed_in() -> bool:
    return bool(st.session_state.get("user"))


def is_admin() -> bool:
    return bool(st.session_state.get("admin"))


def current_user() -> dict:
    return st.session_state.get("user") or {}


def sign_in(email: str, password: str) -> None:
    data = client().login(email, password)
    me = client().me()
    st.session_state.user = data["user"]
    st.session_state.admin = me["admin"]
    st.session_state.cookie = (data["access_token"], data["expires_in"])


def set_up(email: str, password: str, name: str) -> None:
    data = client().set_up(email, password, name)
    st.session_state.user = data["user"]
    st.session_state.admin = True
    st.session_state.cookie = (data["access_token"], data["expires_in"])


def needs_setup() -> bool:
    try:
        return bool(client().setup_status().get("needs_setup"))
    except ApiError:
        return False


def sign_out() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.cookie = ("", 0)


def restore_session() -> None:
    if signed_in():
        return
    token = st.context.cookies.get(SESSION_COOKIE)
    if not token or st.session_state.get("cookie") == ("", 0):
        return
    session = client()
    session.token = token
    try:
        me = session.me()
    except ApiError:
        session.token = None
        st.session_state.cookie = ("", 0)
        return
    st.session_state.user = me["user"]
    st.session_state.admin = me["admin"]


def sync_cookie() -> None:
    pending = st.session_state.pop("cookie", None)
    if pending is None:
        return
    token, max_age = pending
    if not TOKEN_SHAPE.fullmatch(token):
        return
    with st.container(key=f"cookie-{uuid.uuid4().hex}"):
        st.html(
            "<script>document.cookie = "
            f"'{SESSION_COOKIE}={token}; Max-Age={int(max_age)}; Path=/; SameSite=Strict'"
            " + (location.protocol === 'https:' ? '; Secure' : '');</script>",
            unsafe_allow_javascript=True,
        )


def call(action: Callable[[], Any], success: str | None = None) -> Any:
    try:
        result = action()
    except ApiError as e:
        if e.unauthorized and signed_in():
            sign_out()
            st.warning("Your session ended. Please sign in again.")
            st.rerun()
        st.error(e.message)
        return None
    if success:
        st.toast(success)
    return result


def pick_source(key: str = "source") -> dict | None:
    sources = call(client().sources) or []
    if not sources:
        st.info(
            "No data sources available yet."
            + (" Add one under Sources." if is_admin() else " Ask an admin for access.")
        )
        return None
    names = [s["name"] for s in sources]
    remembered = st.session_state.get("active_source")
    index = names.index(remembered) if remembered in names else 0
    chosen = st.selectbox("Data source", names, index=index, key=key)
    st.session_state.active_source = chosen
    return next(s for s in sources if s["name"] == chosen)
