import re

from langchain_core.messages import BaseMessage

from datalyst.agent.prompts import REFUSAL_MARKER

_CODE_BLOCK = re.compile(r"```[a-zA-Z]*[ \t]*\n?(.*?)```", re.DOTALL)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return _THINK_BLOCK.sub("", content).strip()


def extract_query(text: str) -> str:
    match = _CODE_BLOCK.search(text)
    return (match.group(1) if match else text).strip()


def extract_refusal(text: str) -> str | None:
    for candidate in (text, extract_query(text)):
        if candidate.startswith(REFUSAL_MARKER):
            reason = candidate.removeprefix(REFUSAL_MARKER).strip()
            return reason or "the question cannot be answered"
    return None
