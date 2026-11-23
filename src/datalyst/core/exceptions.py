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
