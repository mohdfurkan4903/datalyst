from datetime import timedelta

import pytest

from datalyst.bootstrap import build_worker
from datalyst.core.exceptions import ConfigurationError
from datalyst.services import ConfigService

pytestmark = pytest.mark.integration


@pytest.fixture
def config(metastore):
    return ConfigService(metastore, environ={}, refresh_seconds=0)


def test_defaults_until_an_admin_changes_something(config):
    assert config.current().llm_provider == "openai"
    assert {row["source"] for row in config.describe()} == {"default"}


def test_update_stores_values_and_never_returns_secrets(config, metastore):
    config.update(
        {"llm.provider": "groq", "llm.api_key": "gsk-123", "sync.batch_size": "50"}
    )

    assert config.current().llm_provider == "groq"
    assert config.current().llm_api_key.get_secret_value() == "gsk-123"
    assert config.current().sync_batch_size == 50
    described = {row["name"]: row for row in config.describe()}
    assert described["llm.api_key"]["value"] is None
    assert described["llm.api_key"]["is_set"] is True
    assert described["llm.provider"]["source"] == "metastore"


def test_default_values_and_reset_remove_the_stored_row(config, metastore):
    config.update({"llm.provider": "groq", "llm.model": "m"})
    config.update({"llm.provider": "openai"}, reset=["llm.model"])

    assert metastore.settings.values() == {}
    assert config.current().llm_model == "gpt-4.1-mini"


def test_invalid_values_are_not_saved(config, metastore):
    with pytest.raises(ConfigurationError, match="sync.batch_size"):
        config.update({"sync.batch_size": 0})
    assert metastore.settings.values() == {}


def test_environment_variables_lock_options(metastore):
    config = ConfigService(
        metastore, environ={"DATALYST__LLM__MODEL": "pinned"}, refresh_seconds=0
    )

    with pytest.raises(ConfigurationError, match="DATALYST__LLM__MODEL"):
        config.update({"llm.model": "other"})
    assert config.current().llm_model == "pinned"


def test_other_processes_see_changes(metastore, config):
    other = ConfigService(metastore, environ={}, refresh_seconds=0)
    before = other.version()

    config.update({"auth.token_minutes": 60})

    assert other.version() > before
    assert other.current().auth_token_minutes == 60


def test_worker_picks_up_new_settings(metastore, config):
    worker = build_worker(metastore, config)
    try:
        assert worker._lease == timedelta(seconds=900)
        config.update({"sync.lease_seconds": 120, "sync.batch_size": 7})
        worker.tick()
        assert worker._lease == timedelta(seconds=120)
        assert worker._context.batch_size == 7
    finally:
        worker.close()
