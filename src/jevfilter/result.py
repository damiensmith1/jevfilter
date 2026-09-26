"""Plain, serialisable results. The caller stores them however it likes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["match", "review", "no"]


@dataclass(frozen=True)
class Choice:
    """One option picked from a set, with the full distribution."""

    value: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "choice", **_fields(self)}


@dataclass(frozen=True)
class Field:
    """A value selected from candidates. `value` is None for "none of these"."""

    value: str | None
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "field", **_fields(self)}


@dataclass(frozen=True)
class Score:
    """A position on ordered levels. `value` is the expected level (0-based)."""

    value: float
    level: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    max_level: int = 1

    @property
    def normalized(self) -> float:
        """`value` scaled to 0–1."""
        return self.value / self.max_level if self.max_level else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"type": "score", **_fields(self)}


_TYPES = {"choice": Choice, "field": Field, "score": Score}


def dump_value(v: Any) -> Any:
    if isinstance(v, (Choice, Field, Score)):
        return v.to_dict()
    if isinstance(v, Mapping):
        return {k: dump_value(x) for k, x in v.items()}
    return v


def load_value(v: Any) -> Any:
    if isinstance(v, Mapping):
        cls = _TYPES.get(v.get("type"))  # type: ignore[arg-type]
        if cls is not None:
            return cls(**{k: x for k, x in v.items() if k != "type"})
        return {k: load_value(x) for k, x in v.items()}
    return v


@dataclass
class TopicResult:
    """How one piece of content relates to one topic."""

    topic: str
    outcome: Outcome
    p: float
    reasons: tuple[str, ...] = ()
    category: Choice | None = None
    fields: dict[str, Field] = field(default_factory=dict)
    scores: dict[str, Score] = field(default_factory=dict)
    flags: dict[str, float] = field(default_factory=dict)
    composites: dict[str, float] = field(default_factory=dict)
    facets: dict[str, Any] = field(default_factory=dict)
    topic_version: str = ""

    @property
    def matched(self) -> bool:
        return self.outcome == "match"

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "outcome": self.outcome,
            "p": self.p,
            "reasons": list(self.reasons),
            "category": dump_value(self.category),
            "fields": dump_value(self.fields),
            "scores": dump_value(self.scores),
            "flags": dict(self.flags),
            "composites": dict(self.composites),
            "facets": dump_value(self.facets),
            "topic_version": self.topic_version,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TopicResult:
        return cls(
            topic=d["topic"],
            outcome=d["outcome"],
            p=d["p"],
            reasons=tuple(d.get("reasons", ())),
            category=load_value(d.get("category")),
            fields=load_value(d.get("fields", {})),
            scores=load_value(d.get("scores", {})),
            flags=dict(d.get("flags", {})),
            composites=dict(d.get("composites", {})),
            facets=load_value(d.get("facets", {})),
            topic_version=d.get("topic_version", ""),
        )


@dataclass
class Result:
    """Every topic's result for one piece of content, plus provenance."""

    topics: dict[str, TopicResult]
    model: str | None = None
    request_ids: tuple[str, ...] = ()
    input_tokens: int | None = None
    cost_usd: float | None = None
    wording_version: int = 0
    jevfilter_version: str = ""
    degraded: bool = False
    warnings: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)
    requests: list[dict[str, Any]] | None = None

    @property
    def matches(self) -> list[TopicResult]:
        return [t for t in self.topics.values() if t.outcome == "match"]

    @property
    def review(self) -> list[TopicResult]:
        return [t for t in self.topics.values() if t.outcome == "review"]

    def __getitem__(self, topic: str) -> TopicResult:
        return self.topics[topic]

    def __iter__(self) -> Iterator[TopicResult]:
        return iter(self.topics.values())

    def __len__(self) -> int:
        return len(self.topics)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able data; restore with `Result.from_dict`."""
        d: dict[str, Any] = {
            "topics": [t.to_dict() for t in self.topics.values()],
            "model": self.model,
            "request_ids": list(self.request_ids),
            "input_tokens": self.input_tokens,
            "cost_usd": self.cost_usd,
            "wording_version": self.wording_version,
            "jevfilter_version": self.jevfilter_version,
            "degraded": self.degraded,
            "warnings": list(self.warnings),
            "raw": self.raw,
        }
        if self.requests is not None:
            d["requests"] = self.requests
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Result:
        topics = [TopicResult.from_dict(t) for t in d.get("topics", [])]
        return cls(
            topics={t.topic: t for t in topics},
            model=d.get("model"),
            request_ids=tuple(d.get("request_ids", ())),
            input_tokens=d.get("input_tokens"),
            cost_usd=d.get("cost_usd"),
            wording_version=d.get("wording_version", 0),
            jevfilter_version=d.get("jevfilter_version", ""),
            degraded=d.get("degraded", False),
            warnings=tuple(d.get("warnings", ())),
            raw=dict(d.get("raw", {})),
            requests=d.get("requests"),
        )


def _fields(obj: Any) -> dict[str, Any]:
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in vars(obj).items()}


@dataclass
class ItemMatch:
    """Which tracked item content is about. `item_id` None means a new item.

    `asked` is False when code alone decided (no item survived the
    `match_on` pre-filter), so no Jev call was made.
    """

    item_id: Any | None
    outcome: Literal["match", "review"]
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    asked: bool = False
    candidates: tuple[Any, ...] = ()
    topic: str = ""
    topic_version: str = ""
    model: str | None = None
    request_ids: tuple[str, ...] = ()
    input_tokens: int | None = None
    cost_usd: float | None = None
    wording_version: int = 0
    jevfilter_version: str = ""
    degraded: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def is_new(self) -> bool:
        return self.item_id is None

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in vars(self).items()}
        for key in ("reasons", "candidates", "request_ids", "warnings"):
            d[key] = list(d[key])
        d["probabilities"] = dict(self.probabilities)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ItemMatch:
        d = dict(d)
        for key in ("reasons", "candidates", "request_ids", "warnings"):
            d[key] = tuple(d.get(key, ()))
        return cls(**d)
