import logging
import os
import threading
import time
from collections.abc import Mapping
from typing import Any

from datalyst.config import (
    OPTIONS,
    SECTIONS,
    BootSettings,
    Option,
    Resolved,
    Settings,
    Source,
    export_env,
    export_ini,
    option,
    plain_value,
    resolve,
)
from datalyst.core.exceptions import ConfigurationError, DatalystError
from datalyst.metastore import Metastore

logger = logging.getLogger(__name__)


class ConfigService:
    """Effective settings for this process, reloaded when an admin changes them.

    Every process (API workers, sync workers) checks the metastore's settings
    version at most every ``refresh_seconds`` and rebuilds what depends on it.
    """

    def __init__(
        self,
        metastore: Metastore,
        environ: Mapping[str, str] | None = None,
        refresh_seconds: float = 3.0,
    ) -> None:
        self._metastore = metastore
        self._environ = os.environ if environ is None else environ
        self._refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._version = -1
        self._checked = 0.0
        self._stored: dict[str, Any] = {}
        self._resolved: Resolved | None = None

    def current(self) -> Settings:
        return self.resolved().settings

    def resolved(self) -> Resolved:
        self.refresh()
        assert self._resolved is not None
        return self._resolved

    def version(self) -> int:
        self.refresh()
        return self._version

    def refresh(self, force: bool = False) -> bool:
        """Reload if the stored settings changed; True when they did."""
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._resolved
                and now - self._checked < self._refresh_seconds
            ):
                return False
            self._checked = now
            version = self._metastore.settings.version()
            if version == self._version and self._resolved is not None:
                return False
            stored = self._metastore.settings.values()
            try:
                resolved = resolve(stored, self._environ)
            except ConfigurationError:
                if self._resolved is None:
                    raise
                logger.exception(
                    "Stored settings are invalid; keeping the previous ones"
                )
                return False
            changed = self._resolved is not None
            self._stored, self._resolved, self._version = stored, resolved, version
        if changed:
            logger.info("Settings changed (version %d)", version)
        return changed

    def describe(self) -> list[dict[str, Any]]:
        resolved = self.resolved()
        return [_describe(item, resolved) for item in OPTIONS]

    def update(
        self,
        values: Mapping[str, Any],
        reset: list[str] | None = None,
        user_id: int | None = None,
    ) -> Resolved:
        """Save changed settings; values equal to the default are reset to it."""
        self.refresh(force=True)
        locked = self.resolved().sources
        stored = dict(self._stored)
        names = [*values, *(reset or [])]
        for name in names:
            item = option(name)
            if locked[item.name] == Source.ENVIRONMENT:
                raise ConfigurationError(
                    f"{item.name} is set by the {item.env_var} environment variable "
                    "and cannot be changed here."
                )
        for name in reset or []:
            stored.pop(name, None)
        for name, value in values.items():
            stored[name] = None if value == "" else value
        candidate = resolve(
            {name: value for name, value in stored.items() if value is not None},
            self._environ,
        )
        to_save: dict[str, Any] = {}
        to_remove: set[str] = set(reset or [])
        for name in values:
            item = option(name)
            value = plain_value(candidate.settings, item)
            if value is None or value == item.default:
                to_remove.add(name)
            else:
                to_save[name] = value
        self._metastore.settings.save(
            to_save,
            {item.name for item in OPTIONS if item.secret},
            to_remove,
            user_id,
        )
        self.refresh(force=True)
        return self.resolved()

    def export(
        self,
        fmt: str = "ini",
        *,
        section: str | None = None,
        include_descriptions: bool = True,
        include_sources: bool = False,
        include_secrets: bool = False,
        boot: BootSettings | None = None,
    ) -> str:
        resolved = self.resolved()
        if fmt == "env":
            return export_env(resolved, include_secrets=include_secrets)
        if fmt != "ini":
            raise ConfigurationError("Format must be 'ini' or 'env'.")
        return export_ini(
            resolved,
            section=section,
            include_descriptions=include_descriptions,
            include_sources=include_sources,
            include_secrets=include_secrets,
            boot=boot,
        )


def _describe(item: Option, resolved: Resolved) -> dict[str, Any]:
    value = plain_value(resolved.settings, item)
    return {
        "name": item.name,
        "section": item.section,
        "section_title": SECTIONS[item.section],
        "key": item.key,
        "type": item.type.value,
        "description": item.description,
        "choices": list(item.choices),
        "default": None if item.secret else item.default,
        "value": None if item.secret else value,
        "is_set": value not in (None, ""),
        "source": resolved.sources[item.name].value,
        "env_var": item.env_var,
    }


def check_connection(settings: Settings, target: str) -> dict[str, Any]:
    """Make one small call to the configured LLM or embedding model."""
    from datalyst.llm import create_chat_model, create_embeddings
    from datalyst.llm.messages import message_text

    started = time.perf_counter()
    try:
        if target == "embeddings":
            embeddings = create_embeddings(settings)
            if embeddings is None:
                raise ConfigurationError("Embeddings are off: choose a provider first.")
            vector = embeddings.embed_query("orders per customer")
            detail = f"{len(vector)}-dimension vectors"
        elif target in ("llm", "describe"):
            chosen = settings.describe_settings() if target == "describe" else settings
            reply = create_chat_model(chosen).invoke("Reply with the single word OK.")
            detail = message_text(reply).strip()[:80] or "empty reply"
        else:
            raise ConfigurationError("Test 'llm', 'describe' or 'embeddings'.")
    except DatalystError as e:
        return {"ok": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 - provider SDKs raise their own types
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:500]}
    return {
        "ok": True,
        "detail": detail,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
