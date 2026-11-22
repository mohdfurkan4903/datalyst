from datalyst.agent import AnalystAgent
from datalyst.config import Settings, get_settings
from datalyst.connectors import create_datasource
from datalyst.llm import create_chat_model
from datalyst.services import AnalystService


def build_service(settings: Settings | None = None) -> AnalystService:
    settings = settings or get_settings()
    datasource = create_datasource(settings)
    agent = AnalystAgent(
        create_chat_model(settings),
        datasource,
        max_attempts=settings.agent_max_attempts,
    )
    return AnalystService(datasource, agent)
