from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from datalyst.agent.parsing import extract_query, extract_refusal, message_text
from datalyst.agent.prompts import REPAIR_PROMPT, USER_PROMPT, render_system_prompt
from datalyst.agent.state import AgentState, ErrorKind
from datalyst.core.exceptions import LLMError, QueryExecutionError, UnsafeQueryError
from datalyst.core.interfaces import DataSource

GENERATE, VALIDATE, EXECUTE = "generate", "validate", "execute"


class AnalystAgent:
    def __init__(
        self, llm: BaseChatModel, datasource: DataSource, max_attempts: int = 3
    ) -> None:
        self._llm = llm
        self._datasource = datasource
        self._max_attempts = max_attempts
        self._graph = self._build_graph()

    def run(self, question: str, schema: str) -> AgentState:
        return self._graph.invoke(
            {"question": question, "schema": schema, "attempts": 0}
        )

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(AgentState)
        graph.add_node(GENERATE, self._generate)
        graph.add_node(VALIDATE, self._validate)
        graph.add_node(EXECUTE, self._execute)

        graph.add_edge(START, GENERATE)
        graph.add_conditional_edges(
            GENERATE, self._route_after_generate, [VALIDATE, END]
        )
        graph.add_conditional_edges(
            VALIDATE, self._route_after_validate, [EXECUTE, GENERATE, END]
        )
        graph.add_conditional_edges(EXECUTE, self._route_after_execute, [GENERATE, END])
        return graph.compile()

    def _generate(self, state: AgentState) -> AgentState:
        messages = list(state.get("messages") or self._initial_messages(state))
        if state.get("error"):
            messages.append(HumanMessage(REPAIR_PROMPT.format(error=state["error"])))

        try:
            response = self._llm.invoke(messages)
        except Exception as e:
            raise LLMError(f"LLM call failed: {e}") from e

        text = message_text(response)
        messages.append(AIMessage(text))
        update: AgentState = {
            "messages": messages,
            "attempts": state.get("attempts", 0) + 1,
            "result": None,
        }

        if reason := extract_refusal(text):
            return {
                **update,
                "query": None,
                "error": f"Declined: {reason}",
                "error_kind": ErrorKind.REFUSED,
            }
        return {
            **update,
            "query": extract_query(text),
            "error": None,
            "error_kind": None,
        }

    def _validate(self, state: AgentState) -> AgentState:
        try:
            query = self._datasource.validate(state.get("query") or "")
        except UnsafeQueryError as e:
            return {"error": str(e), "error_kind": ErrorKind.UNSAFE}
        return {"query": query}

    def _execute(self, state: AgentState) -> AgentState:
        try:
            result = self._datasource.execute(state["query"])
        except QueryExecutionError as e:
            return {"error": str(e), "error_kind": ErrorKind.EXECUTION}
        return {"result": result}

    def _route_after_generate(self, state: AgentState) -> str:
        return END if state.get("error_kind") == ErrorKind.REFUSED else VALIDATE

    def _route_after_validate(self, state: AgentState) -> str:
        if not state.get("error"):
            return EXECUTE
        return GENERATE if self._has_attempts_left(state) else END

    def _route_after_execute(self, state: AgentState) -> str:
        if state.get("error") and self._has_attempts_left(state):
            return GENERATE
        return END

    def _has_attempts_left(self, state: AgentState) -> bool:
        return state.get("attempts", 0) < self._max_attempts

    def _initial_messages(self, state: AgentState) -> list[BaseMessage]:
        system = render_system_prompt(
            self._datasource.dialect, self._datasource.query_language
        )
        user = USER_PROMPT.format(schema=state["schema"], question=state["question"])
        return [SystemMessage(system), HumanMessage(user)]
