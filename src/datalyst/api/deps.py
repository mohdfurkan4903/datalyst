from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from datalyst.config import BootSettings
from datalyst.core.exceptions import AuthenticationError
from datalyst.metastore import Metastore
from datalyst.security import Principal
from datalyst.services import (
    AnalystRegistry,
    AuthService,
    ConfigService,
    QuestionService,
)


@dataclass
class AppServices:
    metastore: Metastore
    config: ConfigService
    auth: AuthService
    registry: AnalystRegistry
    questions: QuestionService
    boot: BootSettings | None = None


_bearer = HTTPBearer(auto_error=False)


def services(request: Request) -> AppServices:
    return request.app.state.services


def principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError("Sign in first.")
    return services(request).auth.authenticate(credentials.credentials)


def admin(user: Annotated[Principal, Depends(principal)]) -> Principal:
    user.require_admin()
    return user


Services = Annotated[AppServices, Depends(services)]
CurrentUser = Annotated[Principal, Depends(principal)]
Admin = Annotated[Principal, Depends(admin)]
