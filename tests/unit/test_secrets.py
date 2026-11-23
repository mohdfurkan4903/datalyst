import pytest

from datalyst.core.exceptions import ConfigurationError
from datalyst.security import SecretBox, generate_secret_key


def test_round_trip():
    box = SecretBox(generate_secret_key())
    token = box.encrypt("postgresql://u:secret@h/db")

    assert "secret" not in token
    assert box.decrypt(token) == "postgresql://u:secret@h/db"


def test_wrong_key_cannot_decrypt():
    token = SecretBox(generate_secret_key()).encrypt("x")

    with pytest.raises(ConfigurationError, match="changed"):
        SecretBox(generate_secret_key()).decrypt(token)


def test_invalid_key_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="secret-key"):
        SecretBox("not-a-key")
