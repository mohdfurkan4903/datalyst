from dataclasses import dataclass, field

from datalyst.core.exceptions import PermissionDeniedError
from datalyst.core.models import Role, User


@dataclass(frozen=True)
class Principal:
    """A signed-in user and the data sources they may use."""

    user: User
    sources: frozenset[int] = field(default_factory=frozenset)

    @property
    def is_admin(self) -> bool:
        return self.user.role == Role.ADMIN

    def can_use(self, source_id: int) -> bool:
        return self.is_admin or source_id in self.sources

    def require_admin(self) -> None:
        if not self.is_admin:
            raise PermissionDeniedError("Only admins can do this.")

    def require_source(self, source_id: int) -> None:
        if not self.can_use(source_id):
            raise PermissionDeniedError("You do not have access to this data source.")
