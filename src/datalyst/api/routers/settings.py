from typing import Literal

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from datalyst.api.deps import Admin, Services
from datalyst.api.schemas import ConnectionTest, SettingsUpdate
from datalyst.services.configuration import check_connection

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def list_settings(_: Admin, app: Services) -> list[dict]:
    """Every option with its effective value and where it comes from.

    Secret values are never returned; ``is_set`` tells whether one is stored.
    """
    return app.config.describe()


@router.put("")
def update_settings(body: SettingsUpdate, admin: Admin, app: Services) -> list[dict]:
    app.config.update(body.values, body.reset, admin.user.id)
    return app.config.describe()


@router.get("/export", response_class=PlainTextResponse)
def export_settings(
    _: Admin,
    app: Services,
    format: Literal["ini", "env"] = "ini",
    section: str | None = None,
    include_sources: bool = True,
    include_descriptions: bool = True,
) -> PlainTextResponse:
    """The effective configuration as text, like ``airflow config list``.

    Secrets are always replaced with ``< hidden >``.
    """
    text = app.config.export(
        format,
        section=section,
        include_sources=include_sources,
        include_descriptions=include_descriptions,
        boot=app.boot,
    )
    filename = "datalyst.cfg" if format == "ini" else "datalyst.env"
    return PlainTextResponse(
        text, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/test")
def test_connection(body: ConnectionTest, _: Admin, app: Services) -> dict:
    """Send one small request to the configured model."""
    return check_connection(app.config.current(), body.target)
