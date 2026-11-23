from psycopg import sql

from datalyst.core.models import ColumnRef, ColumnStats, TableKind, TableRef

FULL_SCAN_MAX_ROWS = 50_000
SAMPLE_OVERSHOOT = 20

COLUMN_STATS_SQL = """
SELECT attname AS column_name,
       null_frac AS null_fraction,
       n_distinct AS distinct_estimate,
       most_common_vals::text::text[] AS common_values,
       most_common_freqs AS common_frequencies,
       histogram_bounds::text::text[] AS histogram_bounds,
       avg_width AS average_width,
       correlation
FROM pg_stats
WHERE schemaname = %(schema)s AND tablename = %(table)s
ORDER BY inherited DESC
"""

RELATION_INFO_SQL = """
SELECT c.relkind,
       CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS row_estimate
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
"""


def identifier(table: TableRef) -> sql.Identifier:
    return sql.Identifier(table.schema_name, table.name)


def sampled_source(
    table: TableRef, kind: TableKind, row_estimate: int | None, wanted: int
) -> sql.Composable:
    source = identifier(table)
    if (
        kind != TableKind.TABLE
        or not row_estimate
        or row_estimate <= FULL_SCAN_MAX_ROWS
    ):
        return source
    percent = min(100.0, max(0.01, wanted * SAMPLE_OVERSHOOT * 100 / row_estimate))
    return sql.SQL("{} TABLESAMPLE SYSTEM ({})").format(
        source, sql.Literal(round(percent, 4))
    )


def sample_rows_sql(source: sql.Composable, randomize: bool) -> sql.Composable:
    order = sql.SQL(" ORDER BY random()") if randomize else sql.SQL("")
    return sql.SQL("SELECT * FROM {}{} LIMIT %(limit)s").format(source, order)


def top_values_sql(source: sql.Composable, column: ColumnRef) -> sql.Composable:
    return sql.SQL(
        """
        SELECT v::text AS value, count(*) AS count
        FROM (SELECT {column} AS v FROM {source} LIMIT %(scan_rows)s) AS scanned
        WHERE v IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC, 1
        LIMIT %(limit)s
        """
    ).format(column=sql.Identifier(column.column), source=source)


def containment_sql(
    child_source: sql.Composable, child: ColumnRef, parent: ColumnRef
) -> sql.Composable:
    return sql.SQL(
        """
        WITH child_values AS (
            SELECT DISTINCT v
            FROM (SELECT {child_column} AS v FROM {child_source}
                  LIMIT %(scan_rows)s) AS scanned
            WHERE v IS NOT NULL
            LIMIT %(sample_size)s
        )
        SELECT count(*) AS sampled,
               count(*) FILTER (
                   WHERE EXISTS (
                       SELECT 1 FROM {parent_table} AS p WHERE p.{parent_column} = cv.v
                   )
               ) AS matched
        FROM child_values AS cv
        """
    ).format(
        child_column=sql.Identifier(child.column),
        child_source=child_source,
        parent_table=identifier(parent.table),
        parent_column=sql.Identifier(parent.column),
    )


def build_column_stats(rows: list[dict], row_estimate: int | None) -> list[ColumnStats]:
    seen: dict[str, ColumnStats] = {}
    for row in rows:
        name = row["column_name"]
        if name in seen:
            continue
        seen[name] = ColumnStats(
            column=name,
            null_fraction=row["null_fraction"],
            distinct=_distinct(row["distinct_estimate"], row_estimate),
            common_values=row["common_values"] or [],
            common_frequencies=row["common_frequencies"] or [],
            histogram_bounds=row["histogram_bounds"] or [],
            average_width=row["average_width"],
            correlation=row["correlation"],
        )
    return list(seen.values())


def _distinct(estimate: float | None, row_estimate: int | None) -> float | None:
    if estimate is None:
        return None
    if estimate >= 0:
        return estimate
    return round(-estimate * row_estimate) if row_estimate else None
