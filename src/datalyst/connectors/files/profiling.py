from datalyst.core.models import ColumnRef, TableRef

SAMPLE_SCAN_ROWS = 200_000


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def identifier(table: TableRef) -> str:
    return f"{quote(table.schema_name)}.{quote(table.name)}"


def sample_rows_sql(table: TableRef, limit: int) -> str:
    return (
        f"SELECT * FROM (SELECT * FROM {identifier(table)} "
        f"LIMIT {SAMPLE_SCAN_ROWS}) USING SAMPLE {int(limit)} ROWS"
    )


def top_values_sql(column: ColumnRef, scan_rows: int, limit: int) -> str:
    return f"""
        SELECT CAST(v AS VARCHAR) AS value, COUNT(*) AS count
        FROM (SELECT {quote(column.column)} AS v FROM {identifier(column.table)}
              LIMIT {int(scan_rows)}) AS scanned
        WHERE v IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC, 1
        LIMIT {int(limit)}
    """


def containment_sql(
    child: ColumnRef, parent: ColumnRef, scan_rows: int, sample_size: int
) -> str:
    return f"""
        WITH child_values AS (
            SELECT DISTINCT v
            FROM (SELECT {quote(child.column)} AS v FROM {identifier(child.table)}
                  LIMIT {int(scan_rows)}) AS scanned
            WHERE v IS NOT NULL
            LIMIT {int(sample_size)}
        )
        SELECT COUNT(*) AS sampled,
               COUNT(*) FILTER (
                   WHERE EXISTS (
                       SELECT 1 FROM {identifier(parent.table)} AS p
                       WHERE p.{quote(parent.column)} = cv.v
                   )
               ) AS matched
        FROM child_values AS cv
    """
