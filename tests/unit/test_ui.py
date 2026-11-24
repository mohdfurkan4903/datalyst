import json

import httpx
import pytest

from ui.charts import ChartSpec, choose_chart, looks_temporal, numeric_columns
from ui.client import ApiError, DatalystClient


@pytest.mark.parametrize(
    ("columns", "rows", "expected"),
    [
        (["n"], [[42]], ChartSpec("metric", y=["n"])),
        (
            ["status", "n"],
            [["paid", 3], ["shipped", 5]],
            ChartSpec("bar", x="status", y=["n"]),
        ),
        (
            ["month", "revenue"],
            [["2025-01", 10.5], ["2025-02", 12.0]],
            ChartSpec("line", x="month", y=["revenue"]),
        ),
        (
            ["day", "orders"],
            [["2025-01-01T00:00:00", 3], ["2025-01-02T00:00:00", 4]],
            ChartSpec("line", x="day", y=["orders"]),
        ),
        (
            ["month", "channel", "revenue"],
            [["202501", "WEB", 1.0], ["202501", "APP", 2.0], ["202502", "WEB", 3.0]],
            ChartSpec("line", x="month", y=["revenue"], color="channel"),
        ),
        (
            ["year", "revenue"],
            [[2024, 1.0], [2025, 2.0]],
            ChartSpec("line", x="year", y=["revenue"]),
        ),
        (["name", "email"], [["a", "a@x"], ["b", "b@x"]], ChartSpec("none")),
        (["cust_id", "name"], [[1, "a"], [2, "b"]], ChartSpec("none")),
        (["name", "total"], [["a", 1]], ChartSpec("none")),
        ([], [], ChartSpec("none")),
    ],
)
def test_choose_chart(columns, rows, expected):
    assert choose_chart(columns, rows) == expected


def test_too_many_categories_skip_the_bar_chart():
    rows = [[f"c{i}", i] for i in range(80)]

    assert choose_chart(["name", "n"], rows).kind == "none"


def test_helpers():
    assert numeric_columns(["a", "b", "c"], [[1, "x", None], [2.5, "y", True]]) == ["a"]
    assert looks_temporal("period", ["2025-01", "2025-02"])
    assert not looks_temporal("name", ["alice", "bob"])
    assert looks_temporal("order_year", ["2024", "2025"])


def make_client(handler) -> DatalystClient:
    return DatalystClient("http://api.test", transport=httpx.MockTransport(handler))


def test_login_stores_the_token_and_sends_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.method, request.url.path, request.headers.get("authorization"))
        )
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content) == {"email": "a@x.io", "password": "pw"}
            return httpx.Response(200, json={"access_token": "tok", "user": {"id": 1}})
        return httpx.Response(200, json=[{"id": 1, "name": "shop"}])

    client = make_client(handler)
    client.login("a@x.io", "pw")
    assert client.sources() == [{"id": 1, "name": "shop"}]
    assert seen == [
        ("POST", "/api/auth/login", None),
        ("GET", "/api/sources", "Bearer tok"),
    ]


def test_api_errors_carry_status_and_message():
    def handler(request):
        return httpx.Response(
            401,
            json={
                "error": {"type": "AuthenticationError", "message": "Sign in first."}
            },
        )

    with pytest.raises(ApiError) as caught:
        make_client(handler).sources()
    assert caught.value.unauthorized
    assert caught.value.message == "Sign in first."


def test_raw_error_bodies_are_never_shown():
    def page(request):
        return httpx.Response(
            502, text="<html>Traceback (most recent call last)</html>"
        )

    with pytest.raises(ApiError) as caught:
        make_client(page).sources()

    assert caught.value.status == 502
    assert "Traceback" not in caught.value.message


def test_no_content_and_unreachable_server():
    assert make_client(lambda r: httpx.Response(204)).delete_source(1) is None

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiError, match="Cannot reach"):
        make_client(down).sources()
