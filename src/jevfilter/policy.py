"""Decision policy: raw answers → outcome + machine-readable reasons."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .result import Choice, Field, Outcome, Score
from .topic import Topic


@dataclass(frozen=True)
class TopicAnswers:
    """The interpreted answers for one topic, before a decision."""

    p: float
    category: Choice | None = None
    fields: dict[str, Field] = field(default_factory=dict)
    scores: dict[str, Score] = field(default_factory=dict)
    flags: dict[str, float] = field(default_factory=dict)
    facets: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reasons: tuple[str, ...] = ()


class Policy(Protocol):
    def decide(self, topic: Topic, answers: TopicAnswers) -> Decision: ...


@dataclass(frozen=True)
class ThresholdPolicy:
    """Membership `p ≥ accept` → match, `p < reject` → no, otherwise review.

    A match drops to review when a used Choice (category or field) has
    confidence below `min_confidence`, or a required field has no value.
    A topic's own `thresholds` override these defaults.
    """

    accept: float = 0.7
    reject: float = 0.3
    min_confidence: float = 0.5

    def __post_init__(self) -> None:
        if not 0 <= self.reject <= self.accept <= 1:
            raise ValueError("ThresholdPolicy needs 0 ≤ reject ≤ accept ≤ 1")

    def decide(self, topic: Topic, answers: TopicAnswers) -> Decision:
        accept = topic.thresholds.get("accept", self.accept)
        reject = topic.thresholds.get("reject", self.reject)
        min_conf = topic.thresholds.get("min_confidence", self.min_confidence)

        if answers.p < reject:
            return Decision("no", ("not_member",))
        if answers.p < accept:
            return Decision("review", ("membership_uncertain",))

        reasons: list[str] = []
        if answers.category is not None and answers.category.confidence < min_conf:
            reasons.append("low_confidence:category")
        for name, f in answers.fields.items():
            spec = topic.fields.get(name)
            if f.value is None:
                if spec is not None and spec.required:
                    reasons.append(f"field_missing:{name}")
            elif f.confidence < min_conf:
                reasons.append(f"low_confidence:{name}")
        return Decision("review", tuple(reasons)) if reasons else Decision("match")
