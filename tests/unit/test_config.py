import pytest

from datalyst.config import (
    HIDDEN,
    OPTIONS,
    Settings,
    Source,
    export_env,
    export_ini,
    option,
    resolve,
)
from datalyst.core.exceptions import ConfigurationError


def test_every_option_maps_to_a_setting():
    assert {item.field for item in OPTIONS} == set(Settings.model_fields)
    assert option("llm.api_key").env_var == "DATALYST__LLM__API_KEY"
    assert option("llm.api_key").secret
    assert option("sync.batch_size").type == "integer"
    assert option("describe.enabled").type == "boolean"


def test_unknown_option_is_rejected():
    with pytest.raises(ConfigurationError, match="Unknown setting"):
        option("llm.colour")


def test_environment_wins_over_metastore_over_default():
    resolved = resolve(
        {"llm.provider": "groq", "llm.model": "stored-model"},
        {"DATALYST__LLM__MODEL": "env-model", "DATALYST__SYNC__BATCH_SIZE": "50"},
    )

    assert resolved.settings.llm_provider == "groq"
    assert resolved.settings.llm_model == "env-model"
    assert resolved.settings.sync_batch_size == 50
    assert resolved.sources["llm.provider"] == Source.METASTORE
    assert resolved.sources["llm.model"] == Source.ENVIRONMENT
    assert resolved.sources["llm.temperature"] == Source.DEFAULT


def test_blank_environment_variable_clears_an_optional_value():
    resolved = resolve({"llm.base_url": "http://x"}, {"DATALYST__LLM__BASE_URL": ""})
    assert resolved.settings.llm_base_url is None


def test_invalid_values_name_the_option():
    with pytest.raises(ConfigurationError, match="sync.batch_size"):
        resolve({"sync.batch_size": 0}, {})


def test_ini_export_hides_secrets_and_shows_sources():
    resolved = resolve({"llm.api_key": "sk-secret", "llm.provider": "groq"}, {})

    text = export_ini(resolved, include_sources=True)

    assert "sk-secret" not in text
    assert f"api_key = {HIDDEN}" in text
    assert "provider = groq # source: metastore" in text
    assert "[retrieval]" in text
    assert "# Environment variable: DATALYST__LLM__MODEL" in text


def test_ini_export_of_one_section():
    text = export_ini(resolve({}, {}), section="sync", include_descriptions=False)
    assert text.startswith("[sync]\nbatch_size = 200\n")
    assert "[llm]" not in text
    with pytest.raises(ConfigurationError, match="Unknown section"):
        export_ini(resolve({}, {}), section="nope")


def test_env_export_lists_only_changed_options():
    resolved = resolve({"llm.provider": "groq", "llm.api_key": "sk-secret"}, {})

    text = export_env(resolved)

    assert "DATALYST__LLM__PROVIDER=groq" in text
    assert "sk-secret" not in text
    assert "# DATALYST__LLM__API_KEY=" in text
    assert "DATALYST__SYNC__BATCH_SIZE" not in text
