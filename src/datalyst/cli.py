from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from datalyst.bootstrap import build_service
from datalyst.config import get_settings
from datalyst.connectors import create_datasource
from datalyst.core.exceptions import ConfigurationError, DatalystError
from datalyst.core.interfaces import DataSource
from datalyst.core.models import QueryResult
from datalyst.services import AnalystService

app = typer.Typer(help="Datalyst: talk to your data.", no_args_is_help=True)
console = Console()


@app.command(help="Ask a question; the agent writes and runs the query.")
def ask(
    question: Annotated[str, typer.Argument(help="Your question in plain English.")],
    show_sql: Annotated[bool, typer.Option(help="Print the generated query.")] = True,
) -> None:
    with _service() as service, console.status("Thinking..."):
        analysis = service.ask(question)

    if show_sql and analysis.query:
        console.print(Syntax(analysis.query, "sql", word_wrap=True))
    if analysis.error:
        console.print(f"[red]{analysis.error}[/red]")
        raise typer.Exit(1)
    _print_result(analysis.result)
    console.print(
        f"[dim]{analysis.attempts} attempt(s), {analysis.duration_ms:.0f} ms[/dim]"
    )


@app.command(help="Run SQL directly through the guardrails, without the LLM.")
def query(sql: Annotated[str, typer.Argument(help="A read-only SQL query.")]) -> None:
    with _datasource() as datasource:
        _print_result(datasource.execute(datasource.validate(sql)))


@app.command(help="Show the tables and columns the agent can see.")
def schema() -> None:
    with _datasource() as datasource:
        tables = datasource.get_schema().tables
    for table in tables:
        console.print(f"[bold]{table.qualified_name}[/bold]")
        for column in table.columns:
            marker = " [yellow]PK[/yellow]" if column.name in table.primary_key else ""
            console.print(f"  {column.name} [dim]{column.data_type}[/dim]{marker}")
        for fk in table.foreign_keys:
            console.print(
                f"  [cyan]{fk.column} -> {fk.references_table}.{fk.references_column}[/cyan]"
            )


@contextmanager
def _service() -> Iterator[AnalystService]:
    with _errors_as_exit_codes():
        service = build_service()
        try:
            yield service
        finally:
            service.close()


@contextmanager
def _datasource() -> Iterator[DataSource]:
    with _errors_as_exit_codes(), create_datasource(get_settings()) as datasource:
        yield datasource


@contextmanager
def _errors_as_exit_codes() -> Iterator[None]:
    try:
        yield
    except ValidationError as e:
        console.print(f"[red]Invalid configuration:[/red] {e}")
        raise typer.Exit(2) from e
    except ConfigurationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    except DatalystError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e


def _print_result(result: QueryResult | None) -> None:
    if result is None:
        return
    table = Table(*result.columns, header_style="bold")
    for row in result.rows:
        table.add_row(*("NULL" if value is None else str(value) for value in row))
    console.print(table)
    suffix = " (truncated)" if result.truncated else ""
    console.print(
        f"[dim]{result.row_count} row(s) in {result.elapsed_ms:.0f} ms{suffix}[/dim]"
    )
