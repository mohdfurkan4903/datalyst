from cryptography.fernet import Fernet, InvalidToken

from datalyst.core.exceptions import ConfigurationError


def generate_secret_key() -> str:
    return Fernet.generate_key().decode()


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as e:
            raise ConfigurationError(
                "DATALYST_SECRET_KEY is not a valid key. "
                "Create one with: datalyst secret-key"
            ) from e

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise ConfigurationError(
                "Cannot decrypt a stored secret; DATALYST_SECRET_KEY has changed."
            ) from e
