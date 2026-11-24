import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from datalyst.core.exceptions import UnsafeQueryError


def _expression_types(*names: str) -> tuple[type[exp.Expression], ...]:
    return tuple(t for name in names if (t := getattr(exp, name, None)) is not None)


ALLOWED_ROOTS = _expression_types("Select", "Union", "Intersect", "Except", "Subquery")

FORBIDDEN_NODES = _expression_types(
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",
    "Into",
    "Lock",
    "Copy",
    "Grant",
    "Revoke",
    "Set",
    "Transaction",
    "Commit",
    "Rollback",
)


class SelectOnlyValidator:
    """Accepts one read-only SELECT and refuses anything that could change state."""

    def __init__(
        self, dialect: str, forbidden_functions: frozenset[str] = frozenset()
    ) -> None:
        self._dialect = dialect
        self._forbidden_functions = forbidden_functions

    def validate(self, query: str) -> str:
        sql = query.strip().rstrip(";").strip()
        if not sql:
            raise UnsafeQueryError("Query is empty.")

        root = self._parse_single_statement(sql)
        if not isinstance(root, ALLOWED_ROOTS):
            raise UnsafeQueryError(
                f"Only SELECT queries are allowed, got {root.key.upper()}."
            )

        for node in root.walk():
            if isinstance(node, FORBIDDEN_NODES):
                raise UnsafeQueryError(f"Forbidden operation: {node.key.upper()}.")
            if (
                isinstance(node, exp.Func)
                and (name := _function_name(node)) in self._forbidden_functions
            ):
                raise UnsafeQueryError(f"Forbidden function: {name}().")

        return sql

    def _parse_single_statement(self, sql: str) -> exp.Expression:
        try:
            statements = [
                s for s in sqlglot.parse(sql, read=self._dialect) if s is not None
            ]
        except ParseError as e:
            raise UnsafeQueryError(f"Could not parse query: {e}") from e

        if len(statements) != 1:
            raise UnsafeQueryError(
                f"Exactly one statement is allowed, got {len(statements)}."
            )
        return statements[0]


def _function_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
