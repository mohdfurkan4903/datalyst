class DatalystError(Exception):
    pass


class ConfigurationError(DatalystError):
    pass


class UnsupportedDataSourceError(ConfigurationError):
    pass


class UnsafeQueryError(DatalystError):
    pass


class QueryExecutionError(DatalystError):
    pass


class LLMError(DatalystError):
    pass


class MetastoreError(DatalystError):
    pass


class NotFoundError(DatalystError):
    pass


class AuthenticationError(DatalystError):
    pass


class PermissionDeniedError(DatalystError):
    pass


def brief(error: BaseException | str, limit: int = 300) -> str:
    """A short, single-line message that is safe to show to people.

    Drivers and SDKs often put several lines, request dumps or pointers into
    their messages; people need only the first meaningful line.
    """
    text = str(error)
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = " ".join(line.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
