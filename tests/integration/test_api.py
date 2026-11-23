import pytest
from fastapi.testclient import TestClient

from datalyst.agent import AnalystAgent
from datalyst.api import build_services, create_app
from datalyst.catalog import SchemaCatalog
from datalyst.config import Settings
from datalyst.connectors import create_datasource
from datalyst.core.models import Role
from datalyst.security import generate_secret_key
from datalyst.services import AnalystRegistry, AnalystService
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
def api(metastore, metastore_url, database_url, replies):
    settings = Settings(
        _env_file=None, metastore_url=metastore_url, secret_key=generate_secret_key()
    )

    def factory(source):
        datasource = create_datasource(
            metastore.sources.connection_url(source.id),
            settings,
            source.include_schemas,
        )
        synchronizer = SourceSynchronizer(
            metastore.catalog, metastore.sources, source.id, datasource
        )
        catalog = SchemaCatalog(metastore.catalog, source.id, synchronizer)
        return AnalystService(
            datasource, catalog, AnalystAgent(replies.llm, datasource)
        )

    registry = AnalystRegistry(metastore, factory)
    services = build_services(settings, metastore, registry)
    services.auth.create_user("admin@example.com", ADMIN_PASSWORD, "Ada", Role.ADMIN)
    services.auth.create_user("analyst@example.com", ANALYST_PASSWORD, "Ann")
    with TestClient(create_app(settings, services)) as client:
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


def grant(api, admin, shop, patterns=None):
    group = api.post("/api/groups", headers=admin, json={"name": "sales"}).json()
    users = {u["email"]: u["id"] for u in api.get("/api/users", headers=admin).json()}
    assert (
        api.put(
            f"/api/groups/{group['id']}/members/{users['analyst@example.com']}",
            headers=admin,
        ).status_code
        == 204
    )
    assert (
        api.put(
            f"/api/groups/{group['id']}/sources/{shop}",
            headers=admin,
            json={"table_patterns": patterns},
        ).status_code
        == 204
    )
    return group["id"]


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
    assert response.status_code == 422
    unsupported = api.post(
        "/api/sources", headers=admin, json={"name": "m", "url": "mongodb://h/db"}
    )
    assert unsupported.status_code == 400


def test_sources_are_hidden_until_granted(api, admin, analyst, shop):
    assert api.get("/api/sources", headers=analyst).json() == []
    assert (
        api.post(
            f"/api/sources/{shop}/ask", headers=analyst, json={"question": "hi"}
        ).status_code
        == 403
    )

    grant(api, admin, shop)

    assert [s["name"] for s in api.get("/api/sources", headers=analyst).json()] == [
        "shop"
    ]


def test_table_grants_limit_what_the_agent_can_use(api, admin, analyst, shop, replies):
    grant(api, admin, shop, ["public.orders"])
    tables = api.get(f"/api/sources/{shop}/tables", headers=analyst).json()
    assert [t["name"] for t in tables] == ["orders"]
    assert tables[0]["foreign_keys"] == []

    replies(
        sql_reply("SELECT count(*) FROM public.customers"),
        sql_reply("SELECT status, count(*) AS n FROM public.orders GROUP BY status"),
    )
    answer = api.post(
        f"/api/sources/{shop}/ask",
        headers=analyst,
        json={"question": "Orders per status?"},
    ).json()

    assert answer["attempts"] == 2
    assert answer["tables"] == ["public.orders"]
    assert answer["result"]["row_count"] == 4
    assert (
        "customers"
        not in replies.llm.prompts[0].split("Question:")[0].split("Schema:")[1]
    )
    assert 'Table "public.customers" does not exist' in replies.llm.prompts[1]

    blocked = api.post(
        f"/api/sources/{shop}/query",
        headers=analyst,
        json={"sql": "SELECT * FROM public.customers"},
    )
    assert blocked.status_code == 400
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
    grant(api, admin, shop)
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
    grant(api, admin, shop)
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
    grant(api, admin, shop)
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


def test_password_change_and_deactivation_end_sessions(api, admin, analyst):
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
    users = {u["email"]: u["id"] for u in api.get("/api/users", headers=admin).json()}
    api.patch(
        f"/api/users/{users['analyst@example.com']}",
        headers=admin,
        json={"active": False},
    )
    assert api.get("/api/auth/me", headers=fresh).status_code == 401
    assert (
        api.post(
            "/api/auth/login",
            json={"email": "analyst@example.com", "password": "brand-new-password"},
        ).status_code
        == 401
    )


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
