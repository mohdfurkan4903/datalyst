from datalyst.agent import AnalystAgent
from datalyst.services import AnalystService
from tests.unit.fakes import FakeDataSource, fake_llm, sql_reply


def test_ask_maps_agent_state_to_analysis_result():
    datasource = FakeDataSource()
    service = AnalystService(
        datasource, AnalystAgent(fake_llm(sql_reply("SELECT 1")), datasource)
    )

    analysis = service.ask("anything")

    assert analysis.success
    assert analysis.question == "anything"
    assert analysis.query == "SELECT 1"
    assert analysis.attempts == 1
    assert analysis.duration_ms > 0


def test_schema_is_loaded_once_and_cached():
    datasource = FakeDataSource()
    llm = fake_llm(sql_reply("SELECT 1"), sql_reply("SELECT 2"))
    service = AnalystService(datasource, AnalystAgent(llm, datasource))

    service.ask("first")
    service.ask("second")
    assert datasource.schema_loads == 1

    service.schema(refresh=True)
    assert datasource.schema_loads == 2
