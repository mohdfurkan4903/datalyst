from datalyst.core.exceptions import AuthenticationError, NotFoundError
from datalyst.core.models import Role, User
from datalyst.metastore import Metastore
from datalyst.security import (
    Principal,
    TokenService,
    hash_password,
    merge_grants,
    needs_rehash,
    verify_password,
)

_DUMMY_HASH = hash_password("timing-equaliser-password")


class AuthService:
    def __init__(self, metastore: Metastore, tokens: TokenService) -> None:
        self._metastore = metastore
        self._tokens = tokens

    @property
    def tokens(self) -> TokenService:
        return self._tokens

    def login(self, email: str, password: str) -> tuple[User, str]:
        found = self._metastore.users.credentials(email)
        if found is None:
            verify_password(_DUMMY_HASH, password)
            raise AuthenticationError("Wrong email or password.")
        user, password_hash = found
        if not verify_password(password_hash, password) or not user.active:
            raise AuthenticationError("Wrong email or password.")
        rehashed = hash_password(password) if needs_rehash(password_hash) else None
        self._metastore.users.record_login(user.id, rehashed)
        return user, self._tokens.issue(user)

    def authenticate(self, token: str) -> Principal:
        claims = self._tokens.verify(token)
        try:
            user = self._metastore.users.get(claims.user_id)
        except NotFoundError as e:
            raise AuthenticationError("This account no longer exists.") from e
        if not user.active or user.token_version != claims.version:
            raise AuthenticationError("Session ended; sign in again.")
        return self.principal(user)

    def principal(self, user: User) -> Principal:
        grants = (
            {}
            if user.role == Role.ADMIN
            else merge_grants(self._metastore.groups.grants_for(user.id))
        )
        return Principal(user=user, grants=grants)

    def change_password(self, user: User, current: str, new: str) -> None:
        found = self._metastore.users.credentials(user.email)
        if found is None or not verify_password(found[1], current):
            raise AuthenticationError("The current password is wrong.")
        self._metastore.users.set_password(user.id, hash_password(new))

    def create_user(
        self, email: str, password: str, name: str = "", role: Role = Role.ANALYST
    ) -> User:
        return self._metastore.users.create(email, hash_password(password), name, role)
