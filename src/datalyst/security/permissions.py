from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from datalyst.core.exceptions import PermissionDeniedError
from datalyst.core.models import DataSchema, ForeignKey, Role, Table, User


@dataclass(frozen=True)
class Principal:
    user: User
    grants: Mapping[int, tuple[str, ...] | None] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.user.role == Role.ADMIN

    def can_use(self, source_id: int) -> bool:
        return self.is_admin or source_id in self.grants

    def require_admin(self) -> None:
        if not self.is_admin:
            raise PermissionDeniedError("Only admins can do this.")

    def require_source(self, source_id: int) -> None:
        if not self.can_use(source_id):
            raise PermissionDeniedError("You do not have access to this data source.")

    def can_see(self, source_id: int, qualified_name: str) -> bool:
        if self.is_admin:
            return True
        if source_id not in self.grants:
            return False
        patterns = self.grants[source_id]
        return patterns is None or any(
            fnmatchcase(qualified_name.lower(), pattern.lower()) for pattern in patterns
        )

    def visible_schema(self, source_id: int, schema: DataSchema) -> DataSchema:
        if self.is_admin or self.grants.get(source_id, ()) is None:
            return schema
        visible = {
            t.qualified_name
            for t in schema.tables
            if self.can_see(source_id, t.qualified_name)
        }
        return DataSchema(
            tables=[
                _without_hidden_joins(t, visible)
                for t in schema.tables
                if t.qualified_name in visible
            ]
        )


def merge_grants(
    rows: Iterable[tuple[int, list[str] | None]],
) -> dict[int, tuple[str, ...] | None]:
    merged: dict[int, tuple[str, ...] | None] = {}
    for source_id, patterns in rows:
        if source_id in merged and merged[source_id] is None:
            continue
        if patterns is None:
            merged[source_id] = None
        else:
            merged[source_id] = tuple(
                dict.fromkeys([*(merged.get(source_id) or ()), *patterns])
            )
    return merged


def _without_hidden_joins(table: Table, visible: set[str]) -> Table:
    kept: list[ForeignKey] = [
        fk for fk in table.foreign_keys if fk.references_table in visible
    ]
    if len(kept) == len(table.foreign_keys):
        return table
    return table.model_copy(update={"foreign_keys": kept})
