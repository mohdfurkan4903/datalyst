import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

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
def files_dir(tmp_path):
    return tmp_path / "files"


@pytest.fixture
def config(metastore, files_dir):
    environ = {
        "DATALYST__FILES__DIR": str(files_dir),
        "DATALYST__FILES__MAX_UPLOAD_MB": "1",
        "DATALYST__MEMORY__MAX_ITEMS": "3",
    }
    return ConfigService(metastore, environ=environ, refresh_seconds=0)


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


def test_asking_a_mysql_source(api, admin, analyst, replies, mysql_shop):
    created = api.post(
        "/api/sources",
        headers=admin,
        json={"name": "mysql-shop", "url": mysql_shop["read_only"]},
    )
    assert created.status_code == 201, created.text
    source = created.json()["id"]
    assert created.json()["kind"] == "mysql"

    replies(
        sql_reply("SELECT COUNT(*) FROM `order_lines`"),
        sql_reply("SELECT `status`, COUNT(*) AS n FROM `orders` GROUP BY `status`"),
    )
    answer = api.post(
        f"/api/sources/{source}/ask",
        headers=analyst,
        json={"question": "Orders per status?"},
    ).json()

    assert answer["attempts"] == 2
    assert answer["result"]["row_count"] == 4
    assert "order_lines" in replies.llm.prompts[1]
    assert mysql_shop["database"] + ".orders" in answer["tables"]
    assert "MySQL" in replies.llm.prompts[0]
    blocked = api.post(
        f"/api/sources/{source}/query",
        headers=analyst,
        json={"sql": "SELECT SLEEP(5)"},
    )
    assert blocked.status_code == 400


ORDERS_CSV = b"""Order ID,Customer,Total
1,Ann,10.5
2,Bob,20
3,Ann,7.25
"""


def upload(api, headers, name, filename="orders.csv", content=ORDERS_CSV):
    return api.post(
        "/api/sources/upload",
        headers=headers,
        data={"name": name},
        files={"file": (filename, content, "text/csv")},
    )


def test_uploaded_csv_files_become_a_source_people_can_ask(
    api, admin, analyst, replies, files_dir
):
    created = upload(api, admin, "sales-files")
    assert created.status_code == 201, created.text
    source = created.json()["id"]
    assert created.json()["kind"] == "files"
    assert len(list(files_dir.iterdir())) == 1

    replies(
        sql_reply("SELECT * FROM main.orderz"),
        sql_reply(
            "SELECT customer, SUM(total) AS spent FROM main.orders GROUP BY 1 ORDER BY 1"
        ),
    )
    answer = api.post(
        f"/api/sources/{source}/ask",
        headers=analyst,
        json={"question": "Spend per customer?"},
    ).json()

    assert answer["attempts"] == 2
    assert answer["result"]["rows"] == [["Ann", 17.75], ["Bob", 20.0]]
    assert 'Table "main.orderz" does not exist' in replies.llm.prompts[1]
    assert "DuckDB" in replies.llm.prompts[0]
    blocked = api.post(
        f"/api/sources/{source}/query",
        headers=analyst,
        json={"sql": "SELECT * FROM read_csv('/etc/passwd')"},
    )
    assert blocked.status_code == 400


def workbook_bytes():
    workbook = Workbook()
    customers = workbook.active
    customers.title = "Customers"
    customers.append(["Customer ID", "Name"])
    customers.append([1, "Ann"])
    customers.append([2, "Bob"])
    orders = workbook.create_sheet("Orders")
    orders.append(["Order ID", "Customer ID", "Total"])
    for row in ((10, 1, 10.5), (11, 1, 7.25), (12, 2, 20)):
        orders.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_an_uploaded_excel_workbook_gives_one_table_per_sheet(
    api, admin, analyst, replies
):
    created = upload(api, admin, "workbook", "shop.xlsx", workbook_bytes())
    assert created.status_code == 201, created.text
    source = created.json()["id"]

    replies(
        sql_reply(
            "SELECT c.name, SUM(o.total) AS spent FROM main.customers c "
            "JOIN main.orders o ON o.customer_id = c.customer_id GROUP BY 1 ORDER BY 1"
        )
    )
    answer = api.post(
        f"/api/sources/{source}/ask",
        headers=analyst,
        json={"question": "Spend per customer?"},
    ).json()

    assert answer["result"]["rows"] == [["Ann", 17.75], ["Bob", 20.0]]
    tables = [
        f["table"]
        for f in api.get(f"/api/sources/{source}/files", headers=admin).json()
    ]
    assert tables == ["customers", "orders"]


def test_files_can_be_added_replaced_and_removed(api, admin, analyst, files_dir):
    source = upload(api, admin, "sales-files").json()["id"]

    added = api.post(
        f"/api/sources/{source}/files",
        headers=admin,
        files={"file": ("customers.csv", b"id,name\n1,Ann\n", "text/csv")},
    )
    assert added.status_code == 201, added.text
    assert added.json()[0]["table"] == "customers"
    again = api.post(
        f"/api/sources/{source}/files",
        headers=admin,
        files={"file": ("customers.csv", b"id,name\n1,Ann\n2,Bob\n", "text/csv")},
    )
    assert again.json()[0]["replaced"] is True
    listed = api.get(f"/api/sources/{source}/files", headers=admin).json()
    assert [f["table"] for f in listed] == ["customers", "orders"]
    assert api.get(f"/api/sources/{source}/files", headers=analyst).status_code == 403

    assert (
        api.delete(f"/api/sources/{source}/files/customers", headers=admin).status_code
        == 204
    )
    assert (
        api.delete(f"/api/sources/{source}/files/customers", headers=admin).status_code
        == 404
    )
    assert [
        f["table"]
        for f in api.get(f"/api/sources/{source}/files", headers=admin).json()
    ] == ["orders"]

    assert api.delete(f"/api/sources/{source}", headers=admin).status_code == 204
    assert list(files_dir.iterdir()) == []


def test_bad_uploads_are_refused_and_leave_nothing_behind(api, admin, shop, files_dir):
    too_big = b"a,b\n" + b"1,2\n" * 400_000
    for filename, content, message in (
        ("empty.csv", b"a,b\n", "no data rows"),
        ("notes.pdf", b"%PDF", "Cannot read"),
        ("big.csv", too_big, "larger than"),
    ):
        response = upload(api, admin, "bad-upload", filename, content)
        assert response.status_code == 400, response.text
        assert message in response.json()["error"]["message"]
    assert upload(api, admin, "Not A Valid Name").status_code == 400
    assert not files_dir.exists() or list(files_dir.iterdir()) == []
    assert [s["name"] for s in api.get("/api/sources", headers=admin).json()] == [
        "shop"
    ]
    # Only uploaded-file sources hold files.
    assert api.get(f"/api/sources/{shop}/files", headers=admin).status_code == 400


COUNT_ORDERS = "SELECT count(*) AS n FROM public.orders"


def ask(api, headers, source, question, conversation_id=None):
    body = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = api.post(f"/api/sources/{source}/ask", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_follow_up_questions_see_the_earlier_part_of_the_chat(
    api, admin, analyst, shop, replies
):
    replies(
        sql_reply(COUNT_ORDERS),
        sql_reply("SELECT count(*) AS n FROM public.orders WHERE status = 'paid'"),
    )

    first = ask(api, analyst, shop, "How many orders are there?")
    second = ask(
        api, analyst, shop, "and only the paid ones?", first["conversation_id"]
    )

    assert first["conversation_id"] == second["conversation_id"]
    assert "Conversation so far (oldest first)" not in replies.llm.prompts[0]
    follow_up = replies.llm.prompts[1]
    assert "Conversation so far (oldest first)" in follow_up
    assert "Question: How many orders are there?" in follow_up
    assert f"Query: {COUNT_ORDERS}" in follow_up
    assert "1 row(s), for example" in follow_up and "[[2000]]" in follow_up

    chats = api.get("/api/conversations", headers=analyst).json()
    assert [(c["id"], c["title"], c["turns"]) for c in chats] == [
        (first["conversation_id"], "How many orders are there?", 2)
    ]
    opened = api.get(f"/api/conversations/{first['conversation_id']}", headers=analyst)
    assert [e["question"] for e in opened.json()["entries"]] == [
        "How many orders are there?",
        "and only the paid ones?",
    ]
    # Chats are private to their owner.
    assert api.get("/api/conversations", headers=admin).json() == []
    other = api.get(f"/api/conversations/{first['conversation_id']}", headers=admin)
    assert other.status_code == 404
    stolen = api.post(
        f"/api/sources/{shop}/ask",
        headers=admin,
        json={"question": "hi", "conversation_id": first["conversation_id"]},
    )
    assert stolen.status_code == 404

    gone = api.delete(f"/api/conversations/{first['conversation_id']}", headers=analyst)
    assert gone.status_code == 204
    assert api.get("/api/conversations", headers=analyst).json() == []
    # The audit trail of what was asked stays.
    assert len(api.get("/api/history", headers=analyst).json()) == 2


def test_rows_are_only_shown_to_the_model_when_the_source_allows_it(
    api, admin, analyst, shop, replies
):
    api.patch(f"/api/sources/{shop}", headers=admin, json={"llm_data_policy": "stats"})
    replies(sql_reply(COUNT_ORDERS), sql_reply(COUNT_ORDERS))

    first = ask(api, analyst, shop, "How many orders are there?")
    ask(api, analyst, shop, "again please", first["conversation_id"])

    follow_up = replies.llm.prompts[1]
    assert f"Query: {COUNT_ORDERS}" in follow_up
    assert "Result: 1 row(s)" in follow_up and "for example" not in follow_up
    assert "[[2000]]" not in follow_up


def test_a_chat_belongs_to_one_source(api, admin, analyst, shop, replies):
    replies(sql_reply(COUNT_ORDERS))
    chat = ask(api, analyst, shop, "How many orders?")["conversation_id"]
    other = api.post(
        "/api/sources",
        headers=admin,
        json={"name": "second", "url": api.database_url},
    ).json()["id"]

    response = api.post(
        f"/api/sources/{other}/ask",
        headers=analyst,
        json={"question": "hi", "conversation_id": chat},
    )

    assert response.status_code == 404
    assert (
        api.get(f"/api/conversations?source_id={other}", headers=analyst).json() == []
    )


def test_the_assistant_learns_what_the_user_asks_it_to_remember(
    api, admin, analyst, shop, replies
):
    replies(
        "REMEMBER (source): Revenue means the total of paid orders.\nNO_QUESTION",
        sql_reply(COUNT_ORDERS),
    )

    told = ask(api, analyst, shop, "Remember that revenue means paid orders")

    assert told["memory_notes"] == [
        "Remembered: Revenue means the total of paid orders."
    ]
    assert told["query"] is None and told["result"] is None and told["error"] is None
    assert told["conversation_id"]
    memories = api.get("/api/memories", headers=analyst).json()
    assert [(m["text"], m["origin"], m["source_id"]) for m in memories] == [
        ("Revenue means the total of paid orders.", "agent", shop)
    ]
    # Nothing was run, so nothing is in the history.
    assert api.get("/api/history", headers=analyst).json() == []

    # A later chat, with no memory of the first, still knows it.
    ask(api, analyst, shop, "What was revenue last month?")
    assert len(replies.llm.prompts) == 2
    prompt = replies.llm.prompts[1]
    assert "About this user (saved" in prompt
    assert "- Revenue means the total of paid orders." in prompt
    assert api.get("/api/memories", headers=admin).json() == []


def test_a_message_can_teach_and_ask_at_once_and_memories_can_be_forgotten(
    api, analyst, shop, replies
):
    replies(
        "REMEMBER (all): Show amounts in EUR.",
        sql_reply(COUNT_ORDERS),
    )

    answer = ask(
        api, analyst, shop, "From now on show amounts in EUR. How many orders?"
    )

    assert answer["memory_notes"] == ["Remembered: Show amounts in EUR."]
    assert answer["result"]["rows"] == [[2000]]
    assert "Show amounts in EUR." in replies.llm.prompts[1]
    memory = api.get("/api/memories", headers=analyst).json()[0]
    assert memory["source_id"] is None

    replies(f"FORGET: {memory['id']}\nNO_QUESTION")
    forgotten = ask(api, analyst, shop, "Forget the thing about EUR")
    assert forgotten["memory_notes"] == ["Forgot: Show amounts in EUR."]
    assert api.get("/api/memories", headers=analyst).json() == []


def test_a_failing_model_never_blocks_a_question(api, analyst, shop, replies):
    replies(sql_reply(COUNT_ORDERS))
    original = replies.llm.invoke
    calls = []

    def flaky(messages):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("model down")
        return original(messages)

    replies.llm.invoke = flaky

    answer = ask(
        api, analyst, shop, "Remember that I like short answers. How many orders?"
    )

    assert answer["memory_notes"] == []
    assert answer["result"]["rows"] == [[2000]]


def test_people_manage_their_own_memories(api, admin, analyst, shop):
    added = api.post(
        "/api/memories",
        headers=analyst,
        json={"text": "  Fiscal year starts in   April ", "source_id": None},
    )
    assert added.status_code == 201, added.text
    assert added.json()["origin"] == "user"
    assert added.json()["text"] == "Fiscal year starts in April"
    scoped = api.post(
        "/api/memories",
        headers=analyst,
        json={"text": "Ignore test customers", "source_id": shop},
    ).json()
    again = api.post(
        "/api/memories", headers=analyst, json={"text": "fiscal year starts in april"}
    ).json()
    assert again["id"] == added.json()["id"]
    assert len(api.get("/api/memories", headers=analyst).json()) == 2
    for_the_source = api.get(f"/api/memories?source_id={shop}", headers=analyst).json()
    assert sorted(m["text"] for m in for_the_source) == [
        "Fiscal year starts in April",
        "Ignore test customers",
    ]

    edited = api.put(
        f"/api/memories/{scoped['id']}",
        headers=analyst,
        json={"text": "Ignore demo accounts"},
    )
    assert edited.json()["text"] == "Ignore demo accounts"
    # Nobody else can see, change or delete them.
    assert api.get("/api/memories", headers=admin).json() == []
    assert (
        api.put(
            f"/api/memories/{scoped['id']}", headers=admin, json={"text": "x"}
        ).status_code
        == 404
    )
    assert api.delete(f"/api/memories/{scoped['id']}", headers=admin).status_code == 404

    assert (
        api.delete(f"/api/memories/{scoped['id']}", headers=analyst).status_code == 204
    )
    assert api.delete("/api/memories", headers=analyst).status_code == 204
    assert api.get("/api/memories", headers=analyst).json() == []


def test_memory_has_a_limit_and_makes_room_by_forgetting_what_it_learned(
    api, analyst, shop, replies
):
    for text in ("one", "two", "three"):
        assert (
            api.post("/api/memories", headers=analyst, json={"text": text}).status_code
            == 201
        )
    full = api.post("/api/memories", headers=analyst, json={"text": "four"})
    assert full.status_code == 400 and "full" in full.json()["error"]["message"]

    replies("REMEMBER (all): Learned fact\nNO_QUESTION")
    told = ask(api, analyst, shop, "Remember my team is sales")
    assert told["memory_notes"] == [
        "Memory is full. Remove something on the Memory page to save more."
    ]

    api.delete("/api/memories", headers=analyst)
    api.post("/api/memories", headers=analyst, json={"text": "kept"})
    replies(
        "REMEMBER (all): first learned\nNO_QUESTION",
        "REMEMBER (all): second learned\nNO_QUESTION",
        "REMEMBER (all): third learned\nNO_QUESTION",
    )
    for message in ("Remember a", "Remember b", "Remember c"):
        ask(api, analyst, shop, message)
    assert sorted(
        m["text"] for m in api.get("/api/memories", headers=analyst).json()
    ) == [
        "kept",
        "second learned",
        "third learned",
    ]


def test_memory_can_be_switched_off(api, admin, analyst, shop, replies):
    api.put("/api/settings", headers=admin, json={"values": {"memory.enabled": False}})
    replies(sql_reply(COUNT_ORDERS), sql_reply(COUNT_ORDERS))
    api.post("/api/memories", headers=analyst, json={"text": "A saved fact"})

    first = ask(api, analyst, shop, "Remember that this is ignored. How many orders?")
    ask(api, analyst, shop, "again", first["conversation_id"])

    assert first["conversation_id"] is None and first["memory_notes"] == []
    assert len(replies.llm.prompts) == 2
    assert all(
        "About this user (saved" not in p
        and "Conversation so far (oldest first)" not in p
        for p in replies.llm.prompts
    )


def test_removing_a_user_removes_their_chats_and_memories(
    api, admin, analyst, shop, replies, metastore
):
    replies(sql_reply(COUNT_ORDERS))
    ask(api, analyst, shop, "How many orders?")
    api.post("/api/memories", headers=analyst, json={"text": "A fact"})
    uid = user_id(api, admin)

    api.delete(f"/api/users/{uid}", headers=admin)

    assert metastore.memories.list(uid) == []
    assert metastore.conversations.list(uid) == []


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
        # A user's own chats and memories.
        ("DELETE", "/api/conversations/{conversation_id}"),
        ("POST", "/api/memories"),
        ("PUT", "/api/memories/{memory_id}"),
        ("DELETE", "/api/memories/{memory_id}"),
        ("DELETE", "/api/memories"),
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
                "conversation_id": 1,
                "memory_id": 1,
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
