"""Commands for running and operating a Datalyst installation.

Everything people do day to day (connecting databases, asking questions, managing
users and settings) happens in the web UI. The CLI only starts the services and
covers tasks an operator needs without the UI.
"""

import getpass
import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from pydantic import ValidationError

from datalyst.bootstrap import build_worker, open_metastore, open_ready_metastore
from datalyst.config import (
    OPTIONS_BY_NAME,
    SECTIONS,
    get_boot_settings,
    option,
    plain_value,
)
from datalyst.core.exceptions import ConfigurationError, DatalystError
from datalyst.metastore import Metastore
from datalyst.security import hash_password
from datalyst.services import ConfigService

app = typer.Typer(
    help="Datalyst: an AI data analyst for your databases. "
    "Run the services here; everything else happens in the web UI.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Show the effective configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

LogLevel = Annotated[str, typer.Option(help="Logging level.")]


@app.command(help="Create or upgrade the metastore tables.")
def migrate() -> None:
    with _errors_as_exit_codes(), open_metastore(get_boot_settings()) as metastore:
        applied = metastore.migrate()
    typer.echo(
        f"Applied migrations: {', '.join(applied)}"
        if applied
        else "Metastore is up to date."
    )


@app.command(help="Run the HTTP API.")
def api(
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 1,
    log_level: LogLevel = "info",
) -> None:
    import uvicorn

    _configure_logging(log_level)
    uvicorn.run(
        "datalyst.api:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_level=log_level.lower(),
    )


@app.command(help="Run the background sync worker until stopped.")
def worker(
    once: Annotated[bool, typer.Option(help="Process queued jobs, then exit.")] = False,
    log_level: LogLevel = "info",
) -> None:
    _configure_logging(log_level)
    with _metastore() as metastore:
        sync_worker = build_worker(metastore, ConfigService(metastore))
        if once:
            try:
                processed = sync_worker.drain()
            finally:
                sync_worker.close()
            typer.echo(f"Processed {processed} job(s).")
            return
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        sync_worker.run(stop)


@app.command(
    "reset-password",
    help="Set a user's password when nobody can sign in; ends their sessions.",
)
def reset_password(
    email: Annotated[str, typer.Argument(help="The user's email.")],
) -> None:
    password = getpass.getpass("New password (10+ characters): ")
    if password != getpass.getpass("Again: "):
        typer.echo("The passwords do not match.", err=True)
        raise typer.Exit(1)
    with _metastore() as metastore:
        user = metastore.users.get_by_email(email)
        metastore.users.set_password(user.id, hash_password(password))
    typer.echo(f"Password changed for {user.email}.")


@config_app.command(
    "list",
    help="Print the effective configuration, like 'airflow config list'. "
    "Secrets are hidden.",
)
def config_list(
    section: Annotated[str | None, typer.Option(help="Only this section.")] = None,
    include_sources: Annotated[
        bool, typer.Option(help="Show where each value comes from.")
    ] = False,
    include_descriptions: Annotated[
        bool, typer.Option(help="Describe each option.")
    ] = False,
    env: Annotated[
        bool,
        typer.Option("--env", help="Print changed options as environment variables."),
    ] = False,
) -> None:
    boot = get_boot_settings()
    with _metastore() as metastore:
        config = ConfigService(metastore)
        text = config.export(
            "env" if env else "ini",
            section=section,
            include_sources=include_sources,
            include_descriptions=include_descriptions,
            boot=boot,
        )
    typer.echo(text, nl=False)


@config_app.command("get-value", help="Print one option's effective value.")
def config_get_value(
    section: Annotated[str, typer.Argument(help=f"One of: {', '.join(SECTIONS)}.")],
    key: Annotated[str, typer.Argument(help="Option name within the section.")],
) -> None:
    with _metastore() as metastore:
        item = option(f"{section}.{key}")
        if item.secret:
            typer.echo("Secret values are not printed.", err=True)
            raise typer.Exit(1)
        value = plain_value(ConfigService(metastore).current(), item)
    typer.echo(
        ""
        if value is None
        else str(value).lower()
        if isinstance(value, bool)
        else value
    )


@config_app.command("options", help="List every option with its environment variable.")
def config_options() -> None:
    for item in OPTIONS_BY_NAME.values():
        typer.echo(f"{item.name:34} {item.env_var:44} {item.description}")


@contextmanager
def _metastore() -> Iterator[Metastore]:
    with (
        _errors_as_exit_codes(),
        open_ready_metastore(get_boot_settings()) as metastore,
    ):
        yield metastore


@contextmanager
def _errors_as_exit_codes() -> Iterator[None]:
    try:
        yield
    except ValidationError as e:
        typer.echo(f"Invalid configuration: {e}", err=True)
        raise typer.Exit(2) from e
    except ConfigurationError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    except DatalystError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
