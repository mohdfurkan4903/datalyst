import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from datalyst.bootstrap import (
    build_auth,
    build_catalog,
    build_retriever,
    build_service,
    build_worker,
    open_datasource,
    open_metastore,
)
from datalyst.catalog import Candidates, SchemaCatalog
from datalyst.catalog.sqlrefs import catalog_problems, tables_in_sql
from datalyst.config import get_settings
from datalyst.connectors import create_datasource, datasource_kind
from datalyst.core.exceptions import ConfigurationError, DatalystError
from datalyst.core.interfaces import DataSource
from datalyst.core.models import (
    DataSchema,
    DataSourceInfo,
    GlossaryTerm,
    LLMDataPolicy,
    QueryResult,
    Role,
    SyncSummary,
    VerifiedQuery,
)
from datalyst.llm import create_embeddings
from datalyst.metastore import Metastore
from datalyst.security import generate_secret_key, hash_password
from datalyst.services import AnalystService
from datalyst.sync import (
    check_source_request,
    describe_source_request,
    embed_source_request,
    profile_table_request,
)

app = typer.Typer(help="Datalyst: talk to your data.", no_args_is_help=True)
source_app = typer.Typer(help="Manage data sources.", no_args_is_help=True)
glossary_app = typer.Typer(
    help="Business terms the agent should follow.", no_args_is_help=True
)
examples_app = typer.Typer(
    help="Verified question and SQL pairs.", no_args_is_help=True
)
app.add_typer(source_app, name="source")
user_app = typer.Typer(help="Manage people who can sign in.", no_args_is_help=True)
group_app = typer.Typer(help="Manage groups and data access.", no_args_is_help=True)
app.add_typer(glossary_app, name="glossary")
app.add_typer(user_app, name="user")
app.add_typer(group_app, name="group")
app.add_typer(examples_app, name="examples")
console = Console()

SourceOption = Annotated[
    str | None,
    typer.Option("--source", "-s", help="Data source name; optional if only one."),
]


@app.command(help="Ask a question; the agent writes and runs the query.")
def ask(
    question: Annotated[str, typer.Argument(help="Your question in plain English.")],
    source: SourceOption = None,
    show_sql: Annotated[bool, typer.Option(help="Print the generated query.")] = True,
) -> None:
    with _service(source) as service, console.status("Thinking..."):
        analysis = service.ask(question)

    if analysis.rewritten_question:
        console.print(f"[dim]Interpreted as: {analysis.rewritten_question}[/dim]")
    if analysis.tables:
        console.print(f"[dim]Tables: {', '.join(analysis.tables)}[/dim]")
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
def query(
    sql: Annotated[str, typer.Argument(help="A read-only SQL query.")],
    source: SourceOption = None,
) -> None:
    with _source(source) as (_, datasource):
        _print_result(datasource.execute(datasource.validate(sql)))


@app.command(help="Show the tables and columns the agent can see.")
def schema(
    source: SourceOption = None,
    refresh: Annotated[
        bool, typer.Option(help="Re-read the schema from the database.")
    ] = False,
    search: Annotated[
        str | None,
        typer.Option(help="Show what retrieval finds for this question."),
    ] = None,
) -> None:
    settings = get_settings()
    with _metastore() as metastore:
        info = metastore.sources.resolve(source)
        with open_datasource(settings, metastore, info) as datasource:
            catalog = build_catalog(settings, metastore, info, datasource)
            data_schema = catalog.get(refresh=refresh)
            version = catalog.version
            if search:
                retriever = build_retriever(
                    settings, metastore, info, create_embeddings(settings)
                )
                candidates = retriever.candidates(data_schema, search)
                _print_candidates(candidates)
                data_schema = retriever.assemble(
                    data_schema, candidates.tables[: retriever.top_k]
                )
    _print_schema(data_schema)
    console.print(
        f"[dim]{len(data_schema.tables)} table(s) in '{info.name}', "
        f"catalog version {version}[/dim]"
    )


def _print_candidates(candidates: Candidates) -> None:
    table = Table("rank", "table", "score", header_style="bold")
    for rank, name in enumerate(candidates.tables[:15], start=1):
        table.add_row(str(rank), name, f"{candidates.scores.get(name, 0):.4f}")
    console.print(table)
    for hint in candidates.value_hints:
        console.print(f"[green]value[/green] {hint.text} ({hint.score:.2f})")
    for term in candidates.glossary:
        console.print(f"[cyan]term[/cyan] {term.term}: {term.definition}")
    for example in candidates.examples:
        console.print(f"[magenta]example[/magenta] {example.question}")


@app.command(help="Create or upgrade the metastore tables.")
def migrate() -> None:
    with _errors_as_exit_codes(), open_metastore(get_settings()) as metastore:
        applied = metastore.migrate()
    if applied:
        console.print(f"Applied migrations: {', '.join(applied)}")
    else:
        console.print("Metastore is up to date.")


@app.command(help="Run the background sync worker until stopped.")
def worker(
    once: Annotated[bool, typer.Option(help="Process queued jobs, then exit.")] = False,
    log_level: Annotated[str, typer.Option(help="Logging level.")] = "INFO",
) -> None:
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    with _metastore() as metastore:
        sync_worker = build_worker(settings, metastore)
        if once:
            try:
                processed = sync_worker.drain()
            finally:
                sync_worker.close()
            console.print(f"Processed {processed} job(s).")
            return
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        sync_worker.run(stop)


@app.command(help="Show LLM calls and tokens used in the last days.")
def usage(
    source: SourceOption = None,
    days: Annotated[int, typer.Option(help="How many days back.")] = 30,
) -> None:
    with _metastore() as metastore:
        source_id = metastore.sources.get_by_name(source).id if source else None
        rows = metastore.usage.summary(source_id, timedelta(days=days))
    table = Table("purpose", "model", "calls", "input tokens", "output tokens")
    for row in rows:
        table.add_row(
            row["purpose"],
            row["model"],
            str(row["calls"]),
            f"{row['input_tokens']:,}",
            f"{row['output_tokens']:,}",
        )
    console.print(table)


@app.command(help="Run the HTTP API.")
def api(
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 1,
    log_level: Annotated[str, typer.Option(help="Logging level.")] = "info",
) -> None:
    import uvicorn

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "datalyst.api:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_level=log_level,
    )


@user_app.command("create", help="Create a user; prompts for the password.")
def user_create(
    email: Annotated[str, typer.Argument()],
    name: Annotated[str, typer.Option(help="Display name.")] = "",
    admin: Annotated[bool, typer.Option("--admin", help="Give admin rights.")] = False,
    password: Annotated[
        str,
        typer.Option(prompt=True, hide_input=True, confirmation_prompt=True),
    ] = "",
) -> None:
    settings = get_settings()
    with _metastore() as metastore:
        user = build_auth(settings, metastore).create_user(
            email, password, name, Role.ADMIN if admin else Role.ANALYST
        )
    console.print(f"Created {user.role} {user.email} (id {user.id}).")


@user_app.command("list", help="List users.")
def user_list() -> None:
    with _metastore() as metastore:
        users = metastore.users.all()
    table = Table("id", "email", "name", "role", "active", "last login")
    for user in users:
        table.add_row(
            str(user.id),
            user.email,
            user.name,
            user.role,
            "yes" if user.active else "no",
            _when(user.last_login_at),
        )
    console.print(table)


@user_app.command("password", help="Set a user's password; signs them out everywhere.")
def user_password(
    email: Annotated[str, typer.Argument()],
    password: Annotated[
        str,
        typer.Option(prompt=True, hide_input=True, confirmation_prompt=True),
    ] = "",
) -> None:
    with _metastore() as metastore:
        user = metastore.users.get_by_email(email)
        metastore.users.set_password(user.id, hash_password(password))
    console.print(f"Password changed for {email}.")


@group_app.command("create", help="Create a group.")
def group_create(name: Annotated[str, typer.Argument()]) -> None:
    with _metastore() as metastore:
        group = metastore.groups.create(name)
    console.print(f"Created group '{group.name}' (id {group.id}).")


@group_app.command("add", help="Add a user to a group.")
def group_add(
    group: Annotated[str, typer.Argument()], email: Annotated[str, typer.Argument()]
) -> None:
    with _metastore() as metastore:
        metastore.groups.add_member(
            metastore.groups.get_by_name(group).id,
            metastore.users.get_by_email(email).id,
        )
    console.print(f"Added {email} to '{group}'.")


@group_app.command("grant", help="Let a group query a data source.")
def group_grant(
    group: Annotated[str, typer.Argument()],
    source: Annotated[str, typer.Argument()],
    tables: Annotated[
        list[str] | None,
        typer.Option(
            "--tables", help="Table patterns such as public.ord_*; default all."
        ),
    ] = None,
) -> None:
    with _metastore() as metastore:
        metastore.groups.grant(
            metastore.groups.get_by_name(group).id,
            metastore.sources.get_by_name(source).id,
            tables or None,
        )
    scope = ", ".join(tables) if tables else "all tables"
    console.print(f"'{group}' can now query {source} ({scope}).")


@group_app.command("list", help="List groups, members and grants.")
def group_list() -> None:
    with _metastore() as metastore:
        groups = metastore.groups.all()
        emails = {u.id: u.email for u in metastore.users.all()}
        names = {s.id: s.name for s in metastore.sources.all()}
    for group in groups:
        console.print(f"[bold]{group.name}[/bold]")
        console.print(
            "  members: " + (", ".join(emails[m] for m in group.members) or "none")
        )
        for source_id, patterns in group.grants.items():
            scope = ", ".join(patterns) if patterns else "all tables"
            console.print(f"  {names.get(source_id, source_id)}: {scope}")


@app.command("secret-key", help="Print a new key for DATALYST_SECRET_KEY.")
def secret_key() -> None:
    console.print(generate_secret_key(), soft_wrap=True)


@source_app.command("add", help="Register a database; tests the connection first.")
def source_add(
    name: Annotated[str, typer.Argument(help="Short name, e.g. sales.")],
    url: Annotated[
        str,
        typer.Option(prompt=True, hide_input=True, help="Read-only connection URL."),
    ],
    schema: Annotated[
        list[str] | None,
        typer.Option("--schema", help="Schema to include; repeat for several."),
    ] = None,
    llm_data: Annotated[
        LLMDataPolicy,
        typer.Option(help="What data may be sent to the LLM for descriptions."),
    ] = LLMDataPolicy.ROWS,
    sync_interval: Annotated[
        int, typer.Option(help="Seconds between schema change checks.")
    ] = 300,
    sync: Annotated[bool, typer.Option(help="Read the schema right away.")] = True,
) -> None:
    settings = get_settings()
    with _metastore() as metastore:
        kind = datasource_kind(url)
        with create_datasource(url, settings, schema) as datasource:
            datasource.execute("SELECT 1")
        info = metastore.sources.create(
            name,
            kind,
            url,
            include_schemas=schema,
            llm_data_policy=llm_data,
            sync_interval_seconds=sync_interval,
        )
        console.print(f"Added data source '{info.name}' ({info.kind}).")
        if sync:
            with open_datasource(settings, metastore, info) as datasource:
                summary = build_catalog(settings, metastore, info, datasource).sync()
            _print_sync(summary)


@source_app.command("list", help="Show registered data sources.")
def source_list() -> None:
    with _metastore() as metastore:
        sources = metastore.sources.all()
    if not sources:
        console.print("No data sources yet. Add one with: datalyst source add")
        return
    table = Table("name", "kind", "status", "schemas", "llm data", "last synced")
    for info in sources:
        table.add_row(
            info.name,
            info.kind,
            info.status,
            ", ".join(info.include_schemas) or "all",
            info.llm_data_policy,
            f"{info.last_synced_at:%Y-%m-%d %H:%M}" if info.last_synced_at else "never",
        )
    console.print(table)


@source_app.command("sync", help="Check a data source for schema changes now.")
def source_sync(
    name: Annotated[str, typer.Argument()],
    background: Annotated[
        bool, typer.Option(help="Queue the check for the worker instead.")
    ] = False,
) -> None:
    if background:
        with _metastore() as metastore:
            info = metastore.sources.get_by_name(name)
            queued = metastore.jobs.enqueue(check_source_request(info.id))
        console.print("Queued a check." if queued else "A check is already queued.")
        return
    with _catalog(name) as (_, catalog):
        _print_sync(catalog.sync())


@source_app.command("status", help="Show sync state, queued jobs and recent changes.")
def source_status(name: Annotated[str, typer.Argument()]) -> None:
    with _metastore() as metastore:
        info = metastore.sources.get_by_name(name)
        counts = metastore.jobs.counts(info.id)
        changes = metastore.catalog.recent_changes(info.id, 10)
        jobs = metastore.jobs.recent(info.id, 5)
        workers = metastore.jobs.workers()

    color = {"ready": "green", "error": "red"}.get(info.status, "yellow")
    console.print(f"[bold]{info.name}[/bold] [{color}]{info.status}[/{color}]")
    if info.status_detail:
        console.print(f"  [red]{info.status_detail}[/red]")
    console.print(
        f"  catalog version {info.catalog_version} · "
        f"last checked {_when(info.last_checked_at)} · "
        f"last changed {_when(info.last_synced_at)} · "
        f"next check {_when(info.next_check_at)}"
    )
    console.print(
        "  jobs: "
        + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none")
    )
    console.print(f"  workers running: {len(workers)}")
    if jobs:
        table = Table("job", "kind", "status", "attempts", "error", header_style="bold")
        for job in jobs:
            table.add_row(
                str(job.id), job.kind, job.status, str(job.attempts), job.error or ""
            )
        console.print(table)
    if changes:
        table = Table("when", "table", "change", "details", header_style="bold")
        for change in changes:
            table.add_row(
                _when(change["detected_at"]),
                f"{change['schema_name']}.{change['table_name']}",
                change["change"],
                ", ".join(f"{k}: {v}" for k, v in change["details"].items()),
            )
        console.print(table)


@source_app.command("relationships", help="Show declared and inferred joins.")
def source_relationships(
    name: Annotated[str, typer.Argument()],
    inferred_only: Annotated[
        bool, typer.Option("--inferred", help="Only inferred joins.")
    ] = False,
) -> None:
    with _metastore() as metastore:
        info = metastore.sources.get_by_name(name)
        rows = metastore.catalog.relationships(info.id)
    rows = [r for r in rows if not inferred_only or r["origin"] == "inferred"]
    table = Table("child", "parent", "origin", "confidence", "status", "evidence")
    for row in rows:
        evidence = row["evidence"]
        table.add_row(
            row["child"],
            row["parent"],
            row["origin"],
            f"{row['confidence']:.2f}",
            row["status"],
            f"{evidence.get('reason', '')}; {evidence.get('matched', '?')}/"
            f"{evidence.get('sampled', '?')} values found"
            if evidence
            else "",
        )
    console.print(table)
    console.print(f"[dim]{len(rows)} relationship(s)[/dim]")


@source_app.command("profile", help="Queue profiling of every table for the worker.")
def source_profile(name: Annotated[str, typer.Argument()]) -> None:
    with _metastore() as metastore:
        info = metastore.sources.get_by_name(name)
        refs = [t.ref for t in metastore.catalog.load_schema(info.id).tables]
        queued = metastore.jobs.enqueue_many(
            profile_table_request(info.id, ref) for ref in refs
        )
    console.print(f"Queued profiling for {queued} table(s). Run: datalyst worker")


@source_app.command("describe", help="Queue LLM descriptions for changed tables.")
def source_describe(
    name: Annotated[str, typer.Argument()],
    force: Annotated[
        bool, typer.Option(help="Describe every table again, even unchanged ones.")
    ] = False,
) -> None:
    with _metastore() as metastore:
        info = metastore.sources.get_by_name(name)
        if force:
            metastore.catalog.clear_description_keys(info.id)
        queued = metastore.jobs.enqueue(describe_source_request(info.id))
    console.print(
        "Queued. Run: datalyst worker" if queued else "Descriptions are already queued."
    )


@source_app.command("remove", help="Delete a data source and its catalog.")
def source_remove(
    name: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
) -> None:
    with _metastore() as metastore:
        info = metastore.sources.get_by_name(name)
        if not yes:
            typer.confirm(
                f"Delete '{info.name}' and everything Datalyst knows about it?",
                abort=True,
            )
        metastore.sources.delete(info.id)
    console.print(f"Removed data source '{name}'.")


@glossary_app.command("add", help="Add or update a business term.")
def glossary_add(
    term: Annotated[str, typer.Argument(help="The word or phrase users say.")],
    definition: Annotated[str, typer.Argument(help="What it means for this data.")],
    source: SourceOption = None,
    sql: Annotated[
        str | None, typer.Option(help="Optional SQL fragment, e.g. a filter.")
    ] = None,
) -> None:
    with _metastore() as metastore:
        info = metastore.sources.resolve(source)
        saved = metastore.knowledge.save_term(
            info.id, GlossaryTerm(term=term, definition=definition, sql_hint=sql)
        )
    console.print(f"Saved term '{saved.term}'.")


@glossary_app.command("list", help="List business terms.")
def glossary_list(source: SourceOption = None) -> None:
    with _metastore() as metastore:
        info = metastore.sources.resolve(source)
        terms = metastore.knowledge.glossary(info.id)
    table = Table("term", "definition", "sql", header_style="bold")
    for term in terms:
        table.add_row(term.term, term.definition, term.sql_hint or "")
    console.print(table)


@glossary_app.command("remove", help="Remove a business term.")
def glossary_remove(
    term: Annotated[str, typer.Argument()], source: SourceOption = None
) -> None:
    with _metastore() as metastore:
        metastore.knowledge.delete_term(metastore.sources.resolve(source).id, term)
    console.print(f"Removed '{term}'.")


@examples_app.command("add", help="Add a verified example; the SQL is checked and run.")
def examples_add(
    question: Annotated[str, typer.Argument()],
    sql: Annotated[str, typer.Argument()],
    source: SourceOption = None,
) -> None:
    settings = get_settings()
    with _metastore() as metastore:
        info = metastore.sources.resolve(source)
        with open_datasource(settings, metastore, info) as datasource:
            catalog = build_catalog(settings, metastore, info, datasource).get()
            query = datasource.validate(sql)
            if problems := catalog_problems(query, catalog):
                raise ConfigurationError(" ".join(problems))
            result = datasource.execute(query)
        saved = metastore.knowledge.save_example(
            info.id,
            VerifiedQuery(
                question=question, sql=query, tables=tables_in_sql(query, catalog)
            ),
        )
        metastore.jobs.enqueue(embed_source_request(info.id, timedelta(0)))
    console.print(
        f"Saved example {saved.id} ({result.row_count} row(s); tables: "
        f"{', '.join(saved.tables)})."
    )


@examples_app.command("list", help="List verified examples.")
def examples_list(source: SourceOption = None) -> None:
    with _metastore() as metastore:
        examples = metastore.knowledge.examples(metastore.sources.resolve(source).id)
    for example in examples:
        console.print(f"[bold]{example.id}. {example.question}[/bold]")
        console.print(Syntax(example.sql, "sql", word_wrap=True))


@examples_app.command("remove", help="Remove a verified example.")
def examples_remove(
    example_id: Annotated[int, typer.Argument()], source: SourceOption = None
) -> None:
    with _metastore() as metastore:
        metastore.knowledge.delete_example(
            metastore.sources.resolve(source).id, example_id
        )
    console.print(f"Removed example {example_id}.")


def _print_sync(summary: SyncSummary) -> None:
    console.print(f"Checked {summary.tables} table(s).")
    if not summary.changed:
        console.print("No schema changes.")
    if summary.added:
        console.print(f"[green]Added:[/green] {', '.join(summary.added)}")
    if summary.altered:
        console.print(f"[yellow]Altered:[/yellow] {', '.join(summary.altered)}")
    if summary.removed:
        console.print(f"[red]Removed:[/red] {', '.join(summary.removed)}")


def _print_schema(data_schema: DataSchema) -> None:
    for table in data_schema.tables:
        note = f" [dim]-- {table.description}[/dim]" if table.description else ""
        console.print(f"[bold]{table.qualified_name}[/bold]{note}")
        for column in table.columns:
            text = f"  {column.name} [dim]{column.data_type}[/dim]"
            if column.name in table.primary_key:
                text += " [yellow]PK[/yellow]"
            if profile := column.profile:
                if profile.semantic_type:
                    hint = profile.semantic_type.value.replace("_", " ")
                    if profile.format:
                        hint += f" {profile.format}"
                    text += f" [magenta]{hint}[/magenta]"
                if profile.min_value is not None:
                    text += f" [dim]{profile.min_value}..{profile.max_value}[/dim]"
            if column.sample_values:
                text += f" [green]{', '.join(column.sample_values)}[/green]"
            if column.description:
                text += f" [dim]-- {column.description}[/dim]"
            console.print(text)
        for fk in table.foreign_keys:
            console.print(
                f"  [cyan]{fk.column} -> {fk.references_table}.{fk.references_column}[/cyan]"
            )


@contextmanager
def _metastore() -> Iterator[Metastore]:
    with _errors_as_exit_codes(), open_metastore(get_settings()) as metastore:
        if pending := metastore.pending_migrations():
            raise ConfigurationError(
                f"Metastore needs migrations ({', '.join(pending)}). "
                "Run: datalyst migrate"
            )
        yield metastore


@contextmanager
def _source(name: str | None) -> Iterator[tuple[DataSourceInfo, DataSource]]:
    with _metastore() as metastore:
        info = metastore.sources.resolve(name)
        with open_datasource(get_settings(), metastore, info) as datasource:
            yield info, datasource


@contextmanager
def _catalog(name: str | None) -> Iterator[tuple[DataSourceInfo, SchemaCatalog]]:
    with _metastore() as metastore:
        info = metastore.sources.resolve(name)
        with open_datasource(get_settings(), metastore, info) as datasource:
            yield info, build_catalog(get_settings(), metastore, info, datasource)


@contextmanager
def _service(name: str | None) -> Iterator[AnalystService]:
    with _metastore() as metastore:
        service = build_service(get_settings(), metastore, name)
        try:
            yield service
        finally:
            service.close()


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


def _when(moment: datetime | None) -> str:
    return f"{moment.astimezone():%Y-%m-%d %H:%M:%S}" if moment else "never"


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
