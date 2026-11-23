from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from datalyst.core.exceptions import ConfigurationError

MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ConfigurationError(
            f"Passwords need at least {MIN_PASSWORD_LENGTH} characters."
        )


def hash_password(password: str) -> str:
    check_password_strength(password)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
