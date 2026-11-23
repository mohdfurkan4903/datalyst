from fastapi import APIRouter

from datalyst.api.deps import CurrentUser, Services
from datalyst.api.schemas import (
    LoginRequest,
    PasswordChange,
    SetupRequest,
    TokenResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginRequest, app: Services) -> TokenResponse:
    user, token = app.auth.login(body.email, body.password)
    return _token(app, user, token)


@router.get("/setup")
def setup_status(app: Services) -> dict:
    """Whether this installation still needs its first admin."""
    return {"needs_setup": app.auth.needs_setup()}


@router.post("/setup", status_code=201)
def set_up(body: SetupRequest, app: Services) -> TokenResponse:
    """Create the first admin. Works only while no users exist."""
    user, token = app.auth.set_up(body.email, body.password, body.name)
    return _token(app, user, token)


def _token(app, user, token: str) -> TokenResponse:
    return TokenResponse(
        access_token=token,
        expires_in=int(app.auth.tokens.lifetime.total_seconds()),
        user=user,
    )


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {
        "user": user.user,
        "admin": user.is_admin,
        "sources": sorted(user.grants) if not user.is_admin else None,
    }


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, user: CurrentUser, app: Services) -> None:
    app.auth.change_password(user.user, body.current_password, body.new_password)
