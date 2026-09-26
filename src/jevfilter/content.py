"""What gets judged."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Content:
    """Content to judge, plus optional field candidates and context.

    `state` is text or a JSON-able dict. `candidates` maps topic name → field
    name → candidate values (use `"*"` as the topic for every topic).
    `context` is extra JSON-able state the model can read (e.g. who the
    reader is); it is sent alongside `content`.
    """

    state: Any
    candidates: Mapping[str, Mapping[str, Sequence[str]]] | None = None
    context: Any = None

    def as_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {"content": self.state}
        if self.context is not None:
            state["context"] = self.context
        return state

    def candidates_for(self, topic: str, field: str) -> list[str]:
        if not self.candidates:
            return []
        values = [
            *self.candidates.get(topic, {}).get(field, ()),
            *self.candidates.get("*", {}).get(field, ()),
        ]
        out: list[str] = []
        for v in values:
            v = v.strip() if isinstance(v, str) else v
            if isinstance(v, str) and v and v not in out:
                out.append(v)
        return out


def as_content(content: Any) -> Content:
    return content if isinstance(content, Content) else Content(content)
