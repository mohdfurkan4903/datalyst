import logging
from dataclasses import dataclass, field

from datalyst.core.exceptions import QueryExecutionError
from datalyst.core.interfaces import CatalogIntrospector
from datalyst.core.models import (
    ColumnProfile,
    ColumnRef,
    Containment,
    DataSchema,
    InferredRelationship,
    SemanticType,
    Table,
    ValueCount,
)
from datalyst.enrichment.profiler import (
    TOP_VALUES_KEPT,
    VALUE_INDEX_MAX_DISTINCT,
    profile_table,
    wants_value_index,
)
from datalyst.enrichment.relationships import infer_relationships

logger = logging.getLogger(__name__)

PROMPT_VALUE_TYPES = frozenset(
    {
        SemanticType.CATEGORY,
        SemanticType.BOOLEAN_FLAG,
        SemanticType.COUNTRY_CODE,
        SemanticType.CURRENCY_CODE,
    }
)


@dataclass
class TableProfile:
    profiles: dict[str, ColumnProfile]
    values: dict[str, list[ValueCount]] = field(default_factory=dict)
    sample_values: dict[str, list[str]] = field(default_factory=dict)


def profile(
    introspector: CatalogIntrospector,
    table: Table,
    sample_rows: int = 500,
    value_scan_rows: int = 20_000,
) -> TableProfile:
    stats = introspector.column_stats(table.ref)
    sample = introspector.sample_rows(table.ref, sample_rows)
    first_pass = profile_table(table, stats, sample)

    values: dict[str, list[ValueCount]] = {}
    for column in table.columns:
        if not wants_value_index(column, first_pass[column.name]):
            continue
        try:
            values[column.name] = introspector.top_values(
                ColumnRef(table=table.ref, column=column.name),
                limit=VALUE_INDEX_MAX_DISTINCT,
                scan_rows=value_scan_rows,
            )
        except QueryExecutionError as e:
            logger.warning(
                "Skipping values of %s.%s: %s", table.qualified_name, column.name, e
            )

    profiles = profile_table(table, stats, sample, values)
    sample_values = {
        name: [v.value for v in p.top_values[:TOP_VALUES_KEPT]]
        for name, p in profiles.items()
        if p.semantic_type in PROMPT_VALUE_TYPES and p.top_values
    }
    return TableProfile(profiles=profiles, values=values, sample_values=sample_values)


def relationships(
    introspector: CatalogIntrospector, schema: DataSchema, sample_size: int = 1000
) -> list[InferredRelationship]:
    def contained(child: ColumnRef, parent: ColumnRef) -> Containment:
        try:
            return introspector.containment(child, parent, sample_size)
        except QueryExecutionError as e:
            logger.info(
                "Cannot compare %s with %s: %s",
                child.qualified_name,
                parent.qualified_name,
                e,
            )
            return Containment(sampled=0, matched=0)

    return infer_relationships(schema, contained)
