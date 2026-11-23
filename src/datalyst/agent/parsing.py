import re
from dataclasses import dataclass

from datalyst.agent.prompts import REFUSAL_MARKER
from datalyst.llm.messages import message_text

_CODE_BLOCK = re.compile(r"```[a-zA-Z]*[ \t]*\n?(.*?)```", re.DOTALL)
_REWRITE_FIELD = re.compile(
    r"^\W*(QUESTION|TERMS)\W*:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_TERM_SEPARATOR = re.compile(r"[,;\n]")

__all__ = ["extract_query", "extract_refusal", "message_text", "parse_rewrite"]


def extract_query(text: str) -> str:
    match = _CODE_BLOCK.search(text)
    return (match.group(1) if match else text).strip()


def extract_refusal(text: str) -> str | None:
    for candidate in (text, extract_query(text)):
        if candidate.startswith(REFUSAL_MARKER):
            reason = candidate.removeprefix(REFUSAL_MARKER).strip()
            return reason or "the question cannot be answered"
    return None


@dataclass(frozen=True)
class Rewrite:
    question: str | None
    terms: list[str]


def parse_rewrite(text: str) -> Rewrite:
    fields = {
        label.upper(): value.strip() for label, value in _REWRITE_FIELD.findall(text)
    }
    terms = [term.strip() for term in _TERM_SEPARATOR.split(fields.get("TERMS", ""))]
    return Rewrite(
        question=fields.get("QUESTION") or None,
        terms=[term for term in terms if term],
    )
