from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from datalyst.core.models import Table, TableFingerprint, TableRef


@dataclass(frozen=True)
class FingerprintDiff:
    new: list[TableRef] = field(default_factory=list)
    changed: list[TableRef] = field(default_factory=list)
    removed: list[TableRef] = field(default_factory=list)
    unchanged: list[TableRef] = field(default_factory=list)

    @property
    def to_sync(self) -> list[TableRef]:
        return [*self.new, *self.changed]


def diff_fingerprints(
    current: Iterable[TableFingerprint], stored: Mapping[TableRef, str | None]
) -> FingerprintDiff:
    new, changed, unchanged = [], [], []
    seen = set()
    for fingerprint in sorted(current, key=lambda f: f.table.qualified_name):
        seen.add(fingerprint.table)
        if fingerprint.table not in stored:
            new.append(fingerprint.table)
        elif stored[fingerprint.table] != fingerprint.fingerprint:
            changed.append(fingerprint.table)
        else:
            unchanged.append(fingerprint.table)
    removed = sorted(
        (ref for ref in stored if ref not in seen), key=lambda r: r.qualified_name
    )
    return FingerprintDiff(
        new=new, changed=changed, removed=removed, unchanged=unchanged
    )


def diff_columns(before: Mapping[str, str], after: Table) -> dict[str, object]:
    current = {column.name: column.data_type for column in after.columns}
    changes: dict[str, object] = {}
    if added := [name for name in current if name not in before]:
        changes["added"] = added
    if removed := [name for name in before if name not in current]:
        changes["removed"] = removed
    retyped = {
        name: [before[name], data_type]
        for name, data_type in current.items()
        if name in before and before[name] != data_type
    }
    if retyped:
        changes["retyped"] = retyped
    return changes


def chunked(items: list[TableRef], size: int) -> list[list[TableRef]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
