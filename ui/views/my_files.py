import streamlit as st

from ui.session import call, client, current_user, succeeded
from ui.views.sources import STATUS, files_panel, upload_form


def render() -> None:
    st.title("My files")
    st.caption(
        "Upload CSV or Excel files and ask questions about them on the Ask page. "
        "Only you and the admins can see your files."
    )
    me = current_user().get("id")
    mine = [
        s
        for s in call(client().sources) or []
        if s["kind"] == "files" and s.get("owner_id") == me
    ]
    for source in mine:
        status = STATUS.get(source["status"], source["status"])
        with st.expander(f"{source['name']} · {status}", expanded=len(mine) == 1):
            _manage(source)
    with st.expander("Upload new files", expanded=not mine):
        upload_form()


def _manage(source: dict) -> None:
    if source.get("status_detail"):
        st.error(source["status_detail"])
    elif source["status"] == "new":
        st.info(
            "Datalyst is reading your files. They are ready to ask about in a minute."
        )
    files_panel(source)
    with st.popover("Delete this upload"):
        st.write(
            f"Delete **{source['name']}** and its files? Your chats about it stay in History."
        )
        if st.button(
            "Delete", type="primary", key=f"delete-mine-{source['id']}"
        ) and succeeded(lambda: client().delete_source(source["id"]), "Deleted"):
            st.rerun()
