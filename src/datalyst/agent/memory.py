"""Long-term memory: spotting things a user wants remembered, and showing the
assistant what it already knows about them."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from datalyst.core.models import Memory, Turn

MAX_FACT_LENGTH = 200
QUERY_SHOWN = 600
PREVIEW_SHOWN = 300
MEMORIES_SHOWN_CHARS = 4000

# Only a message that sounds like it states something lasting is sent to the
# model for a closer look, so ordinary questions cost nothing extra.
CUES = re.compile(
    r"\b(remember|forget|from now on|going forward|in future|in the future|always|never|"
    r"by default|default to|keep in mind|bear in mind|note that|for future|"
    r"(my|our) (fiscal|financial|reporting|working|week|quarter|year|team|region|role|"
    r"company|department|name|manager|timezone)|"
    r"i am|i'm|i work|we (use|call|define|count|measure|treat|report)|"
    r"(means|stands for|refers to|is defined as)|call (it|them|that|those)|i prefer|"
    r"don'?t (show|include|use|count))\b",
    re.IGNORECASE,
)
SECRETS = re.compile(
    r"(password|passwd|secret|api[ _-]?key|access[ _-]?key|token|bearer|private key|"
    r"credit card|\b\d{13,19}\b)",
    re.IGNORECASE,
)

EXTRACT_PROMPT = """\
You keep the long-term memory of a data-analysis assistant for one user. Read the \
user's latest message and decide what, if anything, should be remembered for \
future chats.

Remember only lasting things the user states about themselves or their data:
- a definition of a business term ("revenue means paid and shipped orders")
- a standing preference ("show amounts in EUR", "answer in short tables")
- a standing rule or filter ("ignore test customers", "our fiscal year starts in April")
- a fact about their role or focus ("I work in the EMEA sales team")
Do not remember one-off questions, anything that only applies to this one question, \
or secrets such as passwords, keys and card numbers. Write each memory as a short, \
standalone statement of at most 200 characters.

Existing memories are numbered. If the user asks to forget or replace one, name its \
number.

Reply with lines only, from this list:
REMEMBER (all): <statement>      true for every data source
REMEMBER (source): <statement>   true only for the data source being asked about
FORGET: <number>
NO_QUESTION                      the message only gives memory instructions and \
asks nothing to be looked up in the data
NOTHING                          nothing to remember or forget"""


@dataclass
class NewMemory:
    text: str
    all_sources: bool


@dataclass
class MemoryUpdate:
    remember: list[NewMemory] = field(default_factory=list)
    forget: list[int] = field(default_factory=list)
    only_instruction: bool = False

    @property
    def empty(self) -> bool:
        return not self.remember and not self.forget


def mentions_memory(question: str) -> bool:
    return bool(CUES.search(question))


def render_extract_input(question: str, existing: Sequence[Memory]) -> str:
    known = (
        "\n".join(f"{m.id}. {m.text}" for m in existing)
        if existing
        else "(nothing remembered yet)"
    )
    return f"Existing memories:\n{known}\n\nLatest message:\n{question}"


_LINE = re.compile(
    r"^[\s\-*\d.)]*REMEMBER\s*\(\s*(all|source)\s*\)\s*:\s*(.+)$", re.IGNORECASE
)
_FORGET = re.compile(r"^[\s\-*]*FORGET\s*:\s*#?(\d+)", re.IGNORECASE)


def parse_memory_update(text: str, valid_ids: Sequence[int] = ()) -> MemoryUpdate:
    update = MemoryUpdate()
    for raw in text.splitlines():
        line = raw.strip()
        if remembered := _LINE.match(line):
            fact = " ".join(remembered.group(2).split()).strip("\"'` ")
            if (
                fact
                and len(fact) <= MAX_FACT_LENGTH
                and not SECRETS.search(fact)
                and all(fact.lower() != n.text.lower() for n in update.remember)
            ):
                update.remember.append(
                    NewMemory(fact, remembered.group(1).lower() == "all")
                )
        elif forgotten := _FORGET.match(line):
            number = int(forgotten.group(1))
            if number in valid_ids and number not in update.forget:
                update.forget.append(number)
        elif line.upper().startswith("NO_QUESTION"):
            update.only_instruction = True
    return update


def render_memories(memories: Sequence[str]) -> str:
    lines, used = [], 0
    for memory in memories:
        if used + len(memory) > MEMORIES_SHOWN_CHARS:
            break
        lines.append(f"- {memory}")
        used += len(memory)
    if not lines:
        return ""
    return "About this user (saved from earlier chats):\n" + "\n".join(lines)


def render_conversation(turns: Sequence[Turn]) -> str:
    if not turns:
        return ""
    lines = ["Conversation so far (oldest first):"]
    for number, turn in enumerate(turns, start=1):
        lines.append(f"{number}. Question: {turn.question}")
        if turn.declined:
            lines.append(f"   Not answered: {turn.declined}")
        if turn.query:
            query = " ".join(turn.query.split())
            if len(query) > QUERY_SHOWN:
                query = query[:QUERY_SHOWN] + "…"
            lines.append(f"   Query: {query}")
        if turn.row_count is not None:
            result = f"{turn.row_count} row(s)"
            if turn.preview:
                result += f", for example {turn.preview[:PREVIEW_SHOWN]}"
            lines.append(f"   Result: {result}")
    return "\n".join(lines)
