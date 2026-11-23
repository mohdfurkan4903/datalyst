import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel

from datalyst.core.exceptions import AuthenticationError
from datalyst.core.models import Role, User

ALGORITHM = "HS256"
ISSUER = "datalyst"


class TokenClaims(BaseModel):
    user_id: int
    role: Role
    version: int
    expires_at: datetime


def signing_key(secret_key: str) -> str:
    return hashlib.sha256(f"datalyst-auth:{secret_key}".encode()).hexdigest()


class TokenService:
    def __init__(
        self,
        secret_key: str,
        lifetime: timedelta | Callable[[], timedelta] = timedelta(hours=8),
    ) -> None:
        self._key = signing_key(secret_key)
        self._lifetime = lifetime

    @property
    def lifetime(self) -> timedelta:
        return self._lifetime() if callable(self._lifetime) else self._lifetime

    def issue(self, user: User) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": str(user.id),
                "role": user.role.value,
                "ver": user.token_version,
                "iat": now,
                "exp": now + self.lifetime,
                "iss": ISSUER,
            },
            self._key,
            algorithm=ALGORITHM,
        )

    def verify(self, token: str) -> TokenClaims:
        try:
            data = jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"require": ["sub", "exp", "iat", "iss"]},
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthenticationError("Session expired; sign in again.") from e
        except jwt.PyJWTError as e:
            raise AuthenticationError("Invalid session token.") from e
        return TokenClaims(
            user_id=int(data["sub"]),
            role=Role(data["role"]),
            version=int(data.get("ver", 0)),
            expires_at=datetime.fromtimestamp(data["exp"], UTC),
        )
