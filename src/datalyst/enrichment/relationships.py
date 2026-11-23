import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from datalyst.core.models import (
    Column,
    ColumnRef,
    Containment,
    DataSchema,
    InferredRelationship,
    Table,
    TableKind,
)
from datalyst.enrichment.profiler import name_tokens, type_family

MIN_NAME_SCORE = 0.5
MIN_CONTAINMENT = 0.98
KEY_FAMILIES = frozenset({"integer", "text", "uuid"})

KEY_SUFFIXES = frozenset(
    {"id", "cd", "code", "no", "num", "nbr", "key", "ref", "fk", "sk"}
)
GENERIC_TABLE_TOKENS = frozenset(
    {
        "mstr",
        "master",
        "ref",
        "refs",
        "lkp",
        "lookup",
        "dim",
        "fact",
        "tbl",
        "table",
        "hdr",
        "header",
        "dtl",
        "detail",
        "data",
        "info",
        "list",
        "map",
        "xref",
        "main",
    }
)
ROLE_TOKENS = frozenset(
    {
        "from",
        "to",
        "src",
        "source",
        "dst",
        "dest",
        "target",
        "parent",
        "child",
        "mgr",
        "manager",
        "prev",
        "next",
        "orig",
        "original",
        "primary",
        "secondary",
        "asgn",
        "assigned",
        "assignee",
        "owner",
        "appr",
        "approved",
        "approver",
        "created",
        "updated",
        "modified",
        "by",
        "home",
        "billing",
        "shipping",
        "old",
        "new",
        "reporting",
        "reports",
    }
)
PERSON_TABLE_TOKENS = frozenset(
    {"user", "usr", "emp", "employee", "staff", "person", "member", "agent"}
)
NOISE_TABLE = re.compile(
    r"(^|_)(tmp|temp|bkp|backup|bak|old|copy|stg|staging|archive|arch|test)(_|$|\d)"
)

ContainmentCheck = Callable[[ColumnRef, ColumnRef], Containment]


@dataclass(frozen=True)
class Candidate:
    child: ColumnRef
    parent: ColumnRef
    name_score: float
    reason: str


def stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def table_stems(table: Table) -> set[str]:
    return {stem(t) for t in name_tokens(table.name)} - GENERIC_TABLE_TOKENS


def is_noise_table(table: Table) -> bool:
    return bool(NOISE_TABLE.search(table.name.lower()))


def name_score(
    child_table: Table, child: Column, parent_table: Table, parent: Column
) -> tuple[float, str]:
    if child_table.ref == parent_table.ref and child.name == parent.name:
        return 0.0, "same column"
    if child.name.lower() == "id":
        return 0.0, "generic id"
    if child.name == parent.name and parent.name.lower() != "id":
        return 1.0, "same name"

    tokens = [stem(t) for t in name_tokens(child.name)]
    core = {t for t in tokens if t not in KEY_SUFFIXES and t not in ROLE_TOKENS}
    parent_stems = table_stems(parent_table)
    parent_core = {stem(t) for t in name_tokens(parent.name)} - KEY_SUFFIXES

    if not core:
        if "by" in tokens and parent_stems & PERSON_TABLE_TOKENS:
            return 0.6, "actor column"
        return 0.0, "no name overlap"
    if not parent_core:
        if core == parent_stems:
            return 0.9, "table name + id"
        if len(core) == len(parent_stems) and _abbreviates(core, parent_stems):
            return 0.6, "abbreviated table name + id"
        return 0.0, "no name overlap"
    if core <= parent_core:
        return 0.8, "name tokens match"
    if _abbreviates(core, parent_core):
        return 0.6, "abbreviation"
    return 0.0, "no name overlap"


def candidates(schema: DataSchema) -> list[Candidate]:
    tables = [
        t for t in schema.tables if t.kind == TableKind.TABLE and not is_noise_table(t)
    ]
    parents = [(table, column) for table in tables for column in _parent_keys(table)]
    found = []
    for child_table in tables:
        declared = {fk.column for fk in child_table.foreign_keys}
        for child in child_table.columns:
            family = type_family(child.data_type)
            if family not in KEY_FAMILIES or child.name in declared:
                continue
            for parent_table, parent in parents:
                if type_family(parent.data_type) != family:
                    continue
                if _is_reverse_of_key(child_table, child, parent_table, parent):
                    continue
                score, reason = name_score(child_table, child, parent_table, parent)
                if score >= MIN_NAME_SCORE:
                    found.append(
                        Candidate(
                            child=ColumnRef(table=child_table.ref, column=child.name),
                            parent=ColumnRef(
                                table=parent_table.ref, column=parent.name
                            ),
                            name_score=score,
                            reason=reason,
                        )
                    )
    return found


def infer_relationships(
    schema: DataSchema, containment: ContainmentCheck
) -> list[InferredRelationship]:
    by_child: dict[ColumnRef, list[Candidate]] = {}
    for candidate in candidates(schema):
        by_child.setdefault(candidate.child, []).append(candidate)

    accepted = []
    for options in by_child.values():
        best: tuple[float, InferredRelationship] | None = None
        for candidate in sorted(options, key=lambda c: -c.name_score):
            result = containment(candidate.child, candidate.parent)
            if result.sampled == 0 or result.ratio < MIN_CONTAINMENT:
                continue
            confidence = round(
                min(1.0, 0.5 + 0.5 * candidate.name_score) * result.ratio, 3
            )
            relationship = InferredRelationship(
                child=candidate.child,
                parent=candidate.parent,
                confidence=confidence,
                evidence={
                    "name_score": candidate.name_score,
                    "reason": candidate.reason,
                    "sampled": result.sampled,
                    "matched": result.matched,
                },
            )
            if best is None or confidence > best[0]:
                best = (confidence, relationship)
        if best:
            accepted.append(best[1])
    return sorted(
        accepted, key=lambda r: (r.child.qualified_name, r.parent.qualified_name)
    )


def _parent_keys(table: Table) -> Iterable[Column]:
    single_key = table.primary_key[0] if len(table.primary_key) == 1 else None
    for column in table.columns:
        if type_family(column.data_type) not in KEY_FAMILIES:
            continue
        unique = column.profile is not None and column.profile.unique
        if column.name == single_key or (unique and not _mostly_null(column)):
            yield column


def _is_reverse_of_key(
    child_table: Table, child: Column, parent_table: Table, parent: Column
) -> bool:
    child_is_key = child_table.primary_key == [child.name]
    parent_is_key = parent_table.primary_key == [parent.name]
    return child_is_key and not parent_is_key


def _mostly_null(column: Column) -> bool:
    profile = column.profile
    return bool(profile and profile.null_fraction and profile.null_fraction > 0.5)


def _abbreviates(core: set[str], targets: set[str]) -> bool:
    return any(
        len(short) >= 3 and long != short and long.startswith(short)
        for token in core
        for target in targets
        for short, long in ((token, target), (target, token))
    )
