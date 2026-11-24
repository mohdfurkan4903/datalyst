import pytest
from fastapi.testclient import TestClient

from datalyst.agent import AnalystAgent
from datalyst.api import build_services, create_app
from datalyst.catalog import SchemaCatalog
from datalyst.config import BootSettings
from datalyst.connectors import create_datasource
from datalyst.core.models import Role
from datalyst.services import AnalystRegistry, AnalystService, ConfigService
from datalyst.sync import SourceSynchronizer
from tests.unit.fakes import RecordingLLM, sql_reply

pytestmark = pytest.mark.integration

ADMIN_PASSWORD = "admin-password-123"
ANALYST_PASSWORD = "analyst-password-123"


class Replies:
    def __init__(self) -> None:
        self.queue: list[str] = []
        self.llm = RecordingLLM()

    def __call__(self, *replies: str) -> None:
        self.llm._replies.extend(replies)


@pytest.fixture
def replies():
    return Replies()


@pytest.fixture
def config(metastore):
    return ConfigService(metastore, environ={}, refresh_seconds=0)


@pytest.fixture
def api(metastore, config, database_url, replies):
    boot = BootSettings(_env_file=None)

    def factory(source):
        datasource = create_datasource(
            metastore.sources.connection_url(source.id),
            config.current(),
            source.include_schemas,
        )
        synchronizer = SourceSynchronizer(
            metastore.catalog, metastore.sources, source.id, datasource
        )
        catalog = SchemaCatalog(metastore.catalog, source.id, synchronizer)
        return AnalystService(
            datasource, catalog, AnalystAgent(replies.llm, datasource)
        )

    registry = AnalystRegistry(metastore, factory, settings_version=config.version)
    services = build_services(boot, metastore, config, registry)
    services.auth.create_user("admin@example.com", ADMIN_PASSWORD, "Ada", Role.ADMIN)
    services.auth.create_user("analyst@example.com", ANALYST_PASSWORD, "Ann")
    with TestClient(create_app(services)) as client:
        client.database_url = database_url
        yield client
    registry.close()


def login(client, email, password):
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin(api):
    return login(api, "admin@example.com", ADMIN_PASSWORD)


@pytest.fixture
def analyst(api):
    return login(api, "analyst@example.com", ANALYST_PASSWORD)


@pytest.fixture
def shop(api, admin):
    response = api.post(
        "/api/sources", headers=admin, json={"name": "shop", "url": api.database_url}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def user_id(api, admin, email="analyst@example.com"):
    users = {u["email"]: u["id"] for u in api.get("/api/users", headers=admin).json()}
    return users[email]


def test_health_and_ready(api):
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/ready").json() == {"status": "ready"}


def test_login_and_sessions(api, admin):
    bad = api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "nope"}
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["type"] == "AuthenticationError"
    assert api.get("/api/auth/me").status_code == 401
    assert (
        api.get("/api/auth/me", headers={"Authorization": "Bearer junk"}).status_code
        == 401
    )

    me = api.get("/api/auth/me", headers=admin).json()
    assert me["user"]["email"] == "admin@example.com"
    assert me["admin"] is True


def test_analysts_cannot_use_admin_endpoints(api, analyst):
    assert api.get("/api/users", headers=analyst).status_code == 403
    assert (
        api.post(
            "/api/sources", headers=analyst, json={"name": "x", "url": "postgresql://x"}
        ).status_code
        == 403
    )
    assert api.get("/api/history?everyone=true", headers=analyst).status_code == 403


def test_source_creation_tests_the_connection(api, admin):
    response = api.post(
        "/api/sources",
        headers=admin,
        json={
            "name": "broken",
            "url": "postgresql://nobody:x@localhost:1/none?connect_timeout=1",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"].startswith("Could not connect")
    unsupported = api.post(
        "/api/sources", headers=admin, json={"name": "m", "url": "mongodb://h/db"}
    )
    assert unsupported.status_code == 400


def test_sources_can_be_limited_to_picked_users(api, admin, analyst, shop):
    assert [s["name"] for s in api.get("/api/sources", headers=analyst).json()] == [
        "shop"
    ]
    limited = api.put(
        f"/api/sources/{shop}/access",
        headers=admin,
        json={"open_to_all": False, "user_ids": []},
    )
    assert limited.json() == {"open_to_all": False, "user_ids": []}
    assert api.get("/api/sources", headers=analyst).json() == []
    assert (
        api.post(
            f"/api/sources/{shop}/ask", headers=analyst, json={"question": "hi"}
        ).status_code
        == 403
    )
    assert (
        api.put(
            f"/api/sources/{shop}/access",
            headers=analyst,
            json={"open_to_all": True},
        ).status_code
        == 403
    )

    picked = user_id(api, admin)
    api.put(
        f"/api/sources/{shop}/access",
        headers=admin,
        json={"open_to_all": False, "user_ids": [picked]},
    )
    assert api.get(f"/api/sources/{shop}/access", headers=admin).json() == {
        "open_to_all": False,
        "user_ids": [picked],
    }
    assert len(api.get("/api/sources", headers=analyst).json()) == 1


def test_agent_repairs_unknown_tables_and_writes_are_refused(
    api, analyst, shop, replies
):
    replies(
        sql_reply("SELECT count(*) FROM public.order_lines"),
        sql_reply("SELECT status, count(*) AS n FROM public.orders GROUP BY status"),
    )
    answer = api.post(
        f"/api/sources/{shop}/ask",
        headers=analyst,
        json={"question": "Orders per status?"},
    ).json()

    assert answer["attempts"] == 2
    assert answer["result"]["row_count"] == 4
    assert 'Table "public.order_lines" does not exist' in replies.llm.prompts[1]

    unknown = api.post(
        f"/api/sources/{shop}/query",
        headers=analyst,
        json={"sql": "SELECT * FROM public.nope"},
    )
    assert unknown.status_code == 400
    allowed = api.post(
        f"/api/sources/{shop}/query",
        headers=analyst,
        json={"sql": "SELECT sum(id)::numeric / 2 AS half FROM public.orders"},
    )
    assert allowed.json()["rows"] == [[1000500.0]]
    unsafe = api.post(
        f"/api/sources/{shop}/query",
        headers=analyst,
        json={"sql": "DELETE FROM public.orders"},
    )
    assert unsafe.status_code == 400


def test_history_feedback_and_promotion(api, admin, analyst, shop, replies):
    replies(sql_reply("SELECT count(*) AS orders FROM public.orders"))
    answer = api.post(
        f"/api/sources/{shop}/ask",
        headers=analyst,
        json={"question": "How many orders?"},
    ).json()

    history = api.get("/api/history", headers=analyst).json()
    assert [h["id"] for h in history] == [answer["id"]]
    assert history[0]["status"] == "success"
    assert history[0]["row_count"] == 1
    assert api.get("/api/history", headers=admin).json() == []
    assert len(api.get("/api/history?everyone=true", headers=admin).json()) == 1

    feedback = api.post(
        f"/api/history/{answer['id']}/feedback", headers=analyst, json={"value": 1}
    )
    assert feedback.status_code == 204
    assert (
        api.get(f"/api/history/{answer['id']}", headers=analyst).json()["feedback"] == 1
    )

    assert (
        api.post(f"/api/history/{answer['id']}/promote", headers=analyst).status_code
        == 403
    )
    example = api.post(f"/api/history/{answer['id']}/promote", headers=admin).json()
    assert example["tables"] == ["public.orders"]
    assert example["created_by"] == "admin@example.com"
    assert [
        e["question"]
        for e in api.get(f"/api/sources/{shop}/examples", headers=analyst).json()
    ] == ["How many orders?"]


def test_declined_and_failed_answers_are_recorded(api, admin, analyst, shop, replies):
    replies("CANNOT_ANSWER: read-only access only")
    declined = api.post(
        f"/api/sources/{shop}/ask", headers=analyst, json={"question": "Delete orders"}
    ).json()
    assert declined["error"].startswith("Declined")

    failed = api.post(
        f"/api/sources/{shop}/ask", headers=analyst, json={"question": "Anything"}
    )
    assert failed.status_code == 502

    statuses = [h["status"] for h in api.get("/api/history", headers=analyst).json()]
    assert statuses == ["error", "declined"]


def test_curation(api, admin, shop):
    assert api.get(f"/api/sources/{shop}/tables", headers=admin).status_code == 200
    put = api.put(
        f"/api/sources/{shop}/tables/public.orders/description",
        headers=admin,
        json={"description": "Every checkout, one row each"},
    )
    assert put.status_code == 204
    api.put(
        f"/api/sources/{shop}/tables/orders/columns/status/description",
        headers=admin,
        json={"description": "Where the order is in its lifecycle"},
    )

    table = api.get(f"/api/sources/{shop}/tables/orders", headers=admin).json()
    assert (table["description"], table["description_origin"]) == (
        "Every checkout, one row each",
        "human",
    )
    status = next(c for c in table["columns"] if c["name"] == "status")
    assert status["description_status"] == "approved"
    listed = api.get(f"/api/sources/{shop}/tables", headers=admin).json()
    assert (
        next(t for t in listed if t["name"] == "orders")["description"]
        == "Every checkout, one row each"
    )

    relationships = api.get(f"/api/sources/{shop}/relationships", headers=admin).json()
    first = relationships[0]
    assert (
        api.patch(
            f"/api/sources/{shop}/relationships/{first['id']}",
            headers=admin,
            json={"status": "rejected"},
        ).status_code
        == 204
    )
    added = api.post(
        f"/api/sources/{shop}/relationships",
        headers=admin,
        json={"child": "public.order_items.order_id", "parent": "public.orders.id"},
    )
    assert added.status_code == 201
    missing = api.get(f"/api/sources/{shop}/tables/public.nope", headers=admin)
    assert missing.status_code == 404


def test_glossary(api, admin, analyst, shop):
    saved = api.put(
        f"/api/sources/{shop}/glossary/revenue",
        headers=admin,
        json={
            "definition": "Paid and shipped orders",
            "sql_hint": "status IN ('paid','shipped')",
        },
    )
    assert saved.json()["created_by"] == "admin@example.com"
    assert [
        t["term"]
        for t in api.get(f"/api/sources/{shop}/glossary", headers=analyst).json()
    ] == ["revenue"]
    assert (
        api.put(
            f"/api/sources/{shop}/glossary/x", headers=analyst, json={"definition": "y"}
        ).status_code
        == 403
    )
    assert (
        api.delete(f"/api/sources/{shop}/glossary/revenue", headers=admin).status_code
        == 204
    )


def test_password_change_and_blocking_end_sessions(api, admin, analyst):
    changed = api.post(
        "/api/auth/password",
        headers=analyst,
        json={
            "current_password": ANALYST_PASSWORD,
            "new_password": "brand-new-password",
        },
    )
    assert changed.status_code == 204
    assert api.get("/api/auth/me", headers=analyst).status_code == 401

    fresh = login(api, "analyst@example.com", "brand-new-password")
    blocked = api.patch(
        f"/api/users/{user_id(api, admin)}", headers=admin, json={"active": False}
    )
    assert blocked.json()["active"] is False
    assert api.get("/api/auth/me", headers=fresh).status_code == 401
    assert (
        api.post(
            "/api/auth/login",
            json={"email": "analyst@example.com", "password": "brand-new-password"},
        ).status_code
        == 401
    )

    api.patch(f"/api/users/{user_id(api, admin)}", headers=admin, json={"active": True})
    assert login(api, "analyst@example.com", "brand-new-password")


def test_admins_remove_users(api, admin, analyst):
    removed = api.delete(f"/api/users/{user_id(api, admin)}", headers=admin)

    assert removed.status_code == 204
    assert api.get("/api/auth/me", headers=analyst).status_code == 401
    assert [u["email"] for u in api.get("/api/users", headers=admin).json()] == [
        "admin@example.com"
    ]


def test_admins_cannot_block_themselves(api, admin):
    me = user_id(api, admin, "admin@example.com")

    response = api.patch(f"/api/users/{me}", headers=admin, json={"active": False})

    assert response.status_code == 400
    assert "yourself" in response.json()["error"]["message"]


def test_users_cannot_change_anything_admins_manage(api, analyst, shop):
    """Every endpoint that changes shared state is refused for non-admins."""
    allowed = {
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/setup"),
        ("POST", "/api/auth/password"),
        ("POST", "/api/sources/{source_id}/ask"),
        ("POST", "/api/sources/{source_id}/query"),
        ("POST", "/api/history/{entry_id}/feedback"),
    }
    checked = 0
    for route_path, operations in api.app.openapi()["paths"].items():
        for method in {m.upper() for m in operations} - {"GET", "HEAD"}:
            if (method, route_path) in allowed:
                continue
            path = route_path
            for name, value in {
                "source_id": shop,
                "user_id": 1,
                "entry_id": 1,
                "example_id": 1,
                "relationship_id": 1,
                "term": "x",
                "table": "public.orders",
                "column": "id",
            }.items():
                path = path.replace("{" + name + "}", str(value))
            response = api.request(method, path, headers=analyst, json={})
            assert response.status_code == 403, (method, route_path, response.text)
            checked += 1
    assert checked >= 15


def test_server_errors_do_not_reveal_details(api, admin, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("password=hunter2 at /srv/secret.py line 3")

    monkeypatch.setattr(api.app.state.services.metastore.users, "all", broken)
    quiet = TestClient(api.app, raise_server_exceptions=False)
    response = quiet.get("/api/users", headers=admin)

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["type"] == "InternalError"
    assert "hunter2" not in response.text and "secret.py" not in response.text
    assert error["message"].startswith("Something went wrong")
    assert error["reference"]


def test_the_last_admin_is_protected(api, admin):
    users = {u["email"]: u["id"] for u in api.get("/api/users", headers=admin).json()}
    admin_id = users["admin@example.com"]

    demote = api.patch(
        f"/api/users/{admin_id}", headers=admin, json={"role": "analyst"}
    )
    assert demote.status_code == 400
    assert api.delete(f"/api/users/{admin_id}", headers=admin).status_code == 400

    created = api.post(
        "/api/users",
        headers=admin,
        json={"email": "new@example.com", "password": "short"},
    )
    assert created.status_code == 400


def test_settings_are_admin_only(api, analyst):
    assert api.get("/api/settings", headers=analyst).status_code == 403


def test_admin_changes_settings_in_the_ui(api, admin):
    response = api.put(
        "/api/settings",
        json={"values": {"llm.provider": "groq", "llm.api_key": "gsk-abc"}},
        headers=admin,
    )
    assert response.status_code == 200, response.text
    rows = {row["name"]: row for row in response.json()}
    assert rows["llm.provider"]["value"] == "groq"
    assert rows["llm.api_key"] == rows["llm.api_key"] | {"value": None, "is_set": True}

    bad = api.put("/api/settings", json={"values": {"llm.colour": "x"}}, headers=admin)
    assert bad.status_code == 400


def test_config_export_hides_secrets(api, admin):
    api.put("/api/settings", json={"values": {"llm.api_key": "gsk-abc"}}, headers=admin)

    ini = api.get("/api/settings/export", headers=admin)
    env = api.get("/api/settings/export?format=env", headers=admin)

    assert ini.status_code == 200
    assert "gsk-abc" not in ini.text and "gsk-abc" not in env.text
    assert "[llm]" in ini.text and "api_key = < hidden >" in ini.text
    assert "attachment" in ini.headers["content-disposition"]


def test_setup_is_closed_once_users_exist(api):
    assert api.get("/api/auth/setup").json() == {"needs_setup": False}
    response = api.post(
        "/api/auth/setup",
        json={"email": "intruder@example.com", "password": "long-enough-pass"},
    )
    assert response.status_code == 400
