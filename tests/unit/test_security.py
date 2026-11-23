from datetime import timedelta
from decimal import Decimal

import jwt
import pytest

from datalyst.api.errors import status_for
from datalyst.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DatalystError,
    LLMError,
    NotFoundError,
    PermissionDeniedError,
    QueryExecutionError,
    UnsafeQueryError,
    UnsupportedDataSourceError,
)
from datalyst.core.models import (
    Column,
    DataSchema,
    ForeignKey,
    QueryResult,
    Role,
    Table,
    User,
)
from datalyst.security import (
    Principal,
    TokenService,
    generate_secret_key,
    hash_password,
    merge_grants,
    verify_password,
)
from datalyst.security.tokens import signing_key

ADMIN = User(id=1, email="a@x.io", role=Role.ADMIN)
ANALYST = User(id=2, email="b@x.io", role=Role.ANALYST, token_version=3)


def test_password_hashing():
    hashed = hash_password("correct horse battery")

    assert hashed.startswith("$argon2")
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong password!!")
    assert not verify_password("not-a-hash", "anything")


def test_short_passwords_are_rejected():
    with pytest.raises(ConfigurationError, match="at least 10"):
        hash_password("short")


def test_tokens_round_trip():
    tokens = TokenService(generate_secret_key())

    claims = tokens.verify(tokens.issue(ANALYST))

    assert (claims.user_id, claims.role, claims.version) == (2, Role.ANALYST, 3)


def test_expired_tampered_and_foreign_tokens_are_rejected():
    secret = generate_secret_key()
    expired = TokenService(secret, timedelta(seconds=-1)).issue(ANALYST)
    with pytest.raises(AuthenticationError, match="expired"):
        TokenService(secret).verify(expired)

    other = TokenService(generate_secret_key()).issue(ANALYST)
    with pytest.raises(AuthenticationError, match="Invalid"):
        TokenService(secret).verify(other)

    forged = jwt.encode({"sub": "1", "role": "admin"}, signing_key(secret), "HS256")
    with pytest.raises(AuthenticationError):
        TokenService(secret).verify(forged)


SCHEMA = DataSchema(
    tables=[
        Table(
            schema_name="public",
            name="orders",
            columns=[Column(name="customer_id", data_type="int")],
            foreign_keys=[
                ForeignKey(
                    column="customer_id",
                    references_table="hr.salaries",
                    references_column="id",
                )
            ],
        ),
        Table(schema_name="public", name="order_items"),
        Table(schema_name="hr", name="salaries"),
    ]
)


def names(schema):
    return [t.qualified_name for t in schema.tables]


def test_admins_see_everything():
    principal = Principal(user=ADMIN)

    assert principal.can_use(99)
    assert principal.visible_schema(99, SCHEMA) is SCHEMA


def test_analysts_only_use_granted_sources():
    principal = Principal(user=ANALYST, grants={1: None})

    assert principal.can_use(1)
    assert not principal.can_use(2)
    assert principal.visible_schema(1, SCHEMA) is SCHEMA
    with pytest.raises(PermissionDeniedError):
        principal.require_source(2)
    with pytest.raises(PermissionDeniedError):
        principal.require_admin()


def test_table_patterns_hide_tables_and_joins_to_them():
    principal = Principal(user=ANALYST, grants={1: ("public.order*",)})

    visible = principal.visible_schema(1, SCHEMA)

    assert names(visible) == ["public.orders", "public.order_items"]
    assert visible.tables[0].foreign_keys == []
    assert SCHEMA.tables[0].foreign_keys
    assert not principal.can_see(1, "hr.salaries")
    assert principal.can_see(1, "PUBLIC.ORDERS")


def test_merge_grants_unions_patterns_and_full_access_wins():
    merged = merge_grants(
        [
            (1, ["public.a*"]),
            (1, ["public.b*", "public.a*"]),
            (2, ["x.*"]),
            (2, None),
            (2, ["y"]),
        ]
    )

    assert merged == {1: ("public.a*", "public.b*"), 2: None}


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AuthenticationError("x"), 401),
        (PermissionDeniedError("x"), 403),
        (NotFoundError("x"), 404),
        (UnsupportedDataSourceError("x"), 400),
        (UnsafeQueryError("x"), 400),
        (QueryExecutionError("x"), 422),
        (LLMError("x"), 502),
        (DatalystError("x"), 500),
    ],
)
def test_error_status_codes(error, status):
    assert status_for(error) == status


def test_query_results_serialize_to_plain_json():
    result = QueryResult(columns=["n"], rows=[[Decimal("1.25")]], row_count=1)

    assert result.model_dump(mode="json")["rows"] == [[1.25]]
    assert result.rows == [[Decimal("1.25")]]
