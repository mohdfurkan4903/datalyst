from datalyst.agent import AnalystAgent
from datalyst.catalog import SchemaCatalog
from datalyst.services import AnalystService
from tests.unit.fakes import (
    FakeCatalogStore,
    FakeDataSource,
    FakeSynchronizer,
    fake_llm,
    sql_reply,
)


def make_service(datasource, llm):
    store = FakeCatalogStore()
    catalog = SchemaCatalog(store, 1, FakeSynchronizer(store, 1, datasource))
    return AnalystService(datasource, catalog, AnalystAgent(llm, datasource))


def test_ask_maps_agent_state_to_analysis_result():
    service = make_service(FakeDataSource(), fake_llm(sql_reply("SELECT 1")))

    analysis = service.ask("anything")

    assert analysis.success
    assert analysis.question == "anything"
    assert analysis.query == "SELECT 1"
    assert analysis.attempts == 1
    assert analysis.tables == ["public.orders"]
    assert analysis.rewritten_question is None
    assert analysis.duration_ms > 0


def test_schema_is_synced_once_and_reused():
    datasource = FakeDataSource()
    service = make_service(
        datasource, fake_llm(sql_reply("SELECT 1"), sql_reply("SELECT 2"))
    )

    service.ask("first")
    service.ask("second")
    assert datasource.schema_loads == 1

    service.schema(refresh=True)
    assert datasource.schema_loads == 2
