"""Backend-neutral questions, answers and the `Judge` protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Kind = Literal["noul", "choice", "score"]


@dataclass(frozen=True)
class Question:
    """One question for a System One model.

    `criteria` is `{"true": ..., "false": ...}` for a noul, a label → description
    mapping for a choice, and an ordered list of levels for a score.
    """

    kind: Kind
    instructions: Any
    criteria: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            d["criteria"] = self.criteria
        return d


@dataclass(frozen=True)
class NoulAnswer:
    p: float
    kind: Literal["noul"] = "noul"


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]
    kind: Literal["choice"] = "choice"


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    probabilities: dict[int, float]
    kind: Literal["score"] = "score"


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass
class Response:
    """Answers to one request, keyed by question ID, plus provenance."""

    answers: dict[str, Answer]
    model: str | None = None
    input_tokens: int | None = None
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Judge(Protocol):
    """Answers questions about a state. Implement this to plug in a backend."""

    def ask(self, state: Any, questions: Mapping[str, Question]) -> Response: ...


def answer_to_dict(a: Answer) -> dict[str, Any]:
    if isinstance(a, NoulAnswer):
        return {"type": "noul", "p": a.p}
    if isinstance(a, ChoiceAnswer):
        return {
            "type": "choice",
            "choice": a.choice,
            "confidence": a.confidence,
            "probabilities": dict(a.probabilities),
        }
    return {
        "type": "score",
        "score": a.score,
        "confidence": a.confidence,
        "probabilities": {str(k): v for k, v in a.probabilities.items()},
    }


def answer_from_dict(d: Mapping[str, Any]) -> Answer:
    if d["type"] == "noul":
        return NoulAnswer(d["p"])
    if d["type"] == "choice":
        return ChoiceAnswer(d["choice"], d["confidence"], dict(d["probabilities"]))
    return ScoreAnswer(
        d["score"], d["confidence"], {int(k): v for k, v in d["probabilities"].items()}
    )
