import re
from collections.abc import Callable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from datalyst.agent.memory import (
    EXTRACT_PROMPT,
    MemoryUpdate,
    parse_memory_update,
    render_conversation,
    render_extract_input,
    render_memories,
)
from datalyst.agent.parsing import (
    extract_query,
    extract_refusal,
    message_text,
    parse_rewrite,
)
from datalyst.agent.prompts import (
    HEALED_PROMPT,
    REPAIR_PROMPT,
    USER_PROMPT,
    parse_selection,
    render_candidates,
    render_context,
    render_rewrite_prompt,
    render_schema,
    render_select_prompt,
    render_system_prompt,
)
from datalyst.agent.state import AgentState, ErrorKind
from datalyst.catalog import Candidates, SchemaRetriever
from datalyst.catalog.sqlrefs import catalog_problems, referenced_tables, resolve_tables
from datalyst.core.exceptions import LLMError, QueryExecutionError, UnsafeQueryError
from datalyst.core.interfaces import DataSource
from datalyst.core.models import DataSchema, Memory, Turn

REWRITE, RETRIEVE, SELECT, GENERATE, VALIDATE, EXECUTE, HEAL = (
    "rewrite",
    "retrieve",
    "select",
    "generate",
    "validate",
    "execute",
    "heal",
)

SCHEMA_MISSING = re.compile(r"\b(relation|column)\b.+\bdoes not exist", re.IGNORECASE)

Healer = Callable[[list[str]], DataSchema]


class AnalystAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        datasource: DataSource,
        retriever: SchemaRetriever | None = None,
        max_attempts: int = 3,
        rewrite_question: bool = True,
        select_above: int = 12,
        healer: Healer | None = None,
    ) -> None:
        self._llm = llm
        self._datasource = datasource
        self._retriever = retriever or SchemaRetriever()
        self._max_attempts = max_attempts
        self._rewrite_question = rewrite_question
        self._select_above = select_above
        self._healer = healer
        self._graph = self._build_graph()

    def run(
        self,
        question: str,
        catalog: DataSchema,
        turns: Sequence[Turn] = (),
        memories: Sequence[str] = (),
    ) -> AgentState:
        return self._graph.invoke(
            {
                "question": question,
                "conversation": render_conversation(turns),
                "memories": render_memories(memories),
                "catalog": catalog,
                "attempts": 0,
                "healed": False,
            }
        )

    def extract_memory(self, question: str, existing: Sequence[Memory]) -> MemoryUpdate:
        """What this message asks to remember or forget, if anything."""
        messages = [
            SystemMessage(EXTRACT_PROMPT),
            HumanMessage(render_extract_input(question, existing)),
        ]
        return parse_memory_update(
            message_text(self._invoke(messages)), [m.id for m in existing]
        )

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(AgentState)
        for name, node in (
            (REWRITE, self._rewrite),
            (RETRIEVE, self._retrieve),
            (SELECT, self._select),
            (GENERATE, self._generate),
            (VALIDATE, self._validate),
            (EXECUTE, self._execute),
            (HEAL, self._heal),
        ):
            graph.add_node(name, node)

        graph.add_conditional_edges(START, self._route_start, [REWRITE, RETRIEVE])
        graph.add_edge(REWRITE, RETRIEVE)
        graph.add_conditional_edges(
            RETRIEVE, self._route_after_retrieve, [SELECT, GENERATE]
        )
        graph.add_edge(SELECT, GENERATE)
        graph.add_conditional_edges(
            GENERATE, self._route_after_generate, [VALIDATE, END]
        )
        graph.add_conditional_edges(
            VALIDATE, self._route_after_validate, [EXECUTE, GENERATE, END]
        )
        graph.add_conditional_edges(
            EXECUTE, self._route_after_execute, [HEAL, GENERATE, END]
        )
        graph.add_edge(HEAL, GENERATE)
        return graph.compile()

    def _rewrite(self, state: AgentState) -> AgentState:
        asked = state["question"]
        if state.get("conversation"):
            asked = f"{state['conversation']}\n\nQuestion: {asked}"
        messages = [
            SystemMessage(render_rewrite_prompt(self._datasource.dialect)),
            HumanMessage(asked),
        ]
        rewrite = parse_rewrite(message_text(self._invoke(messages)))
        return {"rewritten_question": rewrite.question, "search_terms": rewrite.terms}

    def _retrieve(self, state: AgentState) -> AgentState:
        terms = [state.get("rewritten_question") or "", *state.get("search_terms", [])]
        candidates = self._retriever.candidates(
            state["catalog"], state["question"], [t for t in terms if t]
        )
        if self._should_select(candidates):
            return {"candidates": candidates, "selecting": True}
        chosen = (
            candidates.tables
            if not candidates.searched
            else candidates.tables[: self._retriever.top_k]
        )
        return {
            "candidates": candidates,
            "selecting": False,
            **self._context(state["catalog"], chosen, candidates),
        }

    def _select(self, state: AgentState) -> AgentState:
        candidates = state["candidates"]
        asked = state.get("rewritten_question") or state["question"]
        listing = render_candidates(
            state["catalog"], candidates.tables, candidates.value_hints
        )
        messages = [
            SystemMessage(render_select_prompt(self._datasource.dialect)),
            HumanMessage(f"Candidates:\n{listing}\n\nQuestion: {asked}"),
        ]
        chosen = parse_selection(
            message_text(self._invoke(messages)), candidates.tables
        )
        if not chosen:
            chosen = candidates.tables[: self._retriever.top_k]
        return {
            "selected": chosen,
            **self._context(state["catalog"], chosen, candidates),
        }

    def _generate(self, state: AgentState) -> AgentState:
        messages = list(state.get("messages") or self._initial_messages(state))
        if state.get("error"):
            if state.get("heal_schema"):
                prompt = HEALED_PROMPT.format(
                    error=state["error"], schema=state["heal_schema"]
                )
            else:
                prompt = REPAIR_PROMPT.format(error=state["error"])
            messages.append(HumanMessage(prompt))

        text = message_text(self._invoke(messages))
        messages.append(AIMessage(text))
        update: AgentState = {
            "messages": messages,
            "attempts": state.get("attempts", 0) + 1,
            "result": None,
            "heal_schema": None,
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
        if problems := catalog_problems(
            query,
            state["catalog"],
            self._datasource.sql_dialect,
            self._datasource.default_schema,
        ):
            return {"error": " ".join(problems), "error_kind": ErrorKind.SCHEMA}
        return {"query": query}

    def _execute(self, state: AgentState) -> AgentState:
        try:
            result = self._datasource.execute(state["query"])
        except QueryExecutionError as e:
            return {"error": str(e), "error_kind": ErrorKind.EXECUTION}
        return {"result": result}

    def _heal(self, state: AgentState) -> AgentState:
        query = state.get("query") or ""
        refs = referenced_tables(query, self._datasource.sql_dialect)
        found, missing = resolve_tables(
            refs, state["catalog"], self._datasource.default_schema
        )
        assert self._healer is not None
        catalog = self._healer([*found, *missing])
        involved = [name for name in found if catalog.get_table(name)]
        chosen = list(dict.fromkeys([*state.get("tables", []), *involved]))
        context = self._context(catalog, chosen, state.get("candidates"))
        current = DataSchema(
            tables=[t for t in catalog.tables if t.qualified_name in involved]
        )
        return {
            "catalog": catalog,
            "healed": True,
            "heal_schema": render_schema(current),
            **context,
        }

    def _route_start(self, state: AgentState) -> str:
        if self._rewrite_question and self._retriever.needs_search(state["catalog"]):
            return REWRITE
        return RETRIEVE

    def _route_after_retrieve(self, state: AgentState) -> str:
        return SELECT if state.get("selecting") else GENERATE

    def _route_after_generate(self, state: AgentState) -> str:
        return END if state.get("error_kind") == ErrorKind.REFUSED else VALIDATE

    def _route_after_validate(self, state: AgentState) -> str:
        if not state.get("error"):
            return EXECUTE
        return GENERATE if self._has_attempts_left(state) else END

    def _route_after_execute(self, state: AgentState) -> str:
        if not state.get("error") or not self._has_attempts_left(state):
            return END
        if (
            self._healer is not None
            and not state.get("healed")
            and SCHEMA_MISSING.search(state["error"])
        ):
            return HEAL
        return GENERATE

    def _should_select(self, candidates: Candidates) -> bool:
        return candidates.searched and len(candidates.tables) > self._select_above

    def _context(
        self, catalog: DataSchema, chosen: list[str], candidates: Candidates | None
    ) -> AgentState:
        schema = self._retriever.assemble(catalog, chosen)
        extras = (
            render_context(
                candidates.glossary, candidates.value_hints, candidates.examples
            )
            if candidates
            else ""
        )
        return {
            "schema": render_schema(schema),
            "context": extras,
            "tables": [table.qualified_name for table in schema.tables],
        }

    def _has_attempts_left(self, state: AgentState) -> bool:
        return state.get("attempts", 0) < self._max_attempts

    def _invoke(self, messages: list[BaseMessage]) -> BaseMessage:
        try:
            return self._llm.invoke(messages)
        except Exception as e:
            raise LLMError(f"LLM call failed: {e}") from e

    def _initial_messages(self, state: AgentState) -> list[BaseMessage]:
        system = render_system_prompt(
            self._datasource.dialect,
            self._datasource.query_language,
            default_schema=self._datasource.default_schema,
            text_date_function=self._datasource.text_date_function,
        )
        extra = "\n\n".join(
            part for part in (state.get("memories"), state.get("conversation")) if part
        )
        context = state.get("context", "")
        if extra:
            context = f"{context}\n{extra}\n"
        user = USER_PROMPT.format(
            schema=state["schema"], context=context, question=state["question"]
        )
        return [SystemMessage(system), HumanMessage(user)]
