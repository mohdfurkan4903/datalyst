import re

from langchain_core.messages import BaseMessage

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return _THINK_BLOCK.sub("", content).strip()
