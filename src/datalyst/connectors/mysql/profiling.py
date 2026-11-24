from datalyst.core.models import ColumnRef, TableKind, TableRef

FULL_SCAN_MAX_ROWS = 50_000
SAMPLE_OVERSHOOT = 20


def quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def identifier(table: TableRef) -> str:
    return f"{quote(table.schema_name)}.{quote(table.name)}"


def sample_rows_sql(
    table: TableRef, kind: TableKind, row_estimate: int | None, limit: int
) -> tuple[str, list[int]]:
    """Random rows for small tables, a random pick from the first rows of big ones."""
    source = identifier(table)
    if kind != TableKind.TABLE:
        return f"SELECT * FROM {source} LIMIT %s", [limit]
    if row_estimate and row_estimate > FULL_SCAN_MAX_ROWS:
        query = (
            f"SELECT * FROM (SELECT * FROM {source} LIMIT %s) AS head "
            "ORDER BY RAND() LIMIT %s"
        )
        return query, [limit * SAMPLE_OVERSHOOT, limit]
    return f"SELECT * FROM {source} ORDER BY RAND() LIMIT %s", [limit]


def top_values_sql(column: ColumnRef, scan_rows: int, limit: int) -> tuple[str, list]:
    return (
        f"""
        SELECT CAST(v AS CHAR) AS value, COUNT(*) AS count
        FROM (SELECT {quote(column.column)} AS v FROM {identifier(column.table)}
              LIMIT %s) AS scanned
        WHERE v IS NOT NULL
        GROUP BY value
        ORDER BY count DESC, value
        LIMIT %s
        """,
        [scan_rows, limit],
    )


def containment_sql(
    child: ColumnRef, parent: ColumnRef, scan_rows: int, sample_size: int
) -> tuple[str, list[int]]:
    return (
        f"""
        SELECT COUNT(*) AS sampled,
               COALESCE(SUM(EXISTS(
                   SELECT 1 FROM {identifier(parent.table)} AS p
                   WHERE p.{quote(parent.column)} = cv.v
               )), 0) AS matched
        FROM (
            SELECT DISTINCT v
            FROM (SELECT {quote(child.column)} AS v FROM {identifier(child.table)}
                  LIMIT %s) AS scanned
            WHERE v IS NOT NULL
            LIMIT %s
        ) AS cv
        """,
        [scan_rows, sample_size],
    )
