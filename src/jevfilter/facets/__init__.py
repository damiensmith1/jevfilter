"""Facets: the kinds of judgment a topic asks for.

A facet turns a topic's config into questions and reads its answers back.
Built-ins cover categories, fields, scores and flags; register your own with
`@jevfilter.facet("name")` and use it as a topic key.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol

from .. import wording
from ..content import Content
from ..errors import JevFilterError
from ..judges.base import Answer, ChoiceAnswer, NoulAnswer, Question, ScoreAnswer
from ..result import Choice, Field, Score
from ..topic import Topic


class Facet(Protocol):
    """Questions are keyed by a local name ("" for a single question); the
    engine prefixes them into question IDs: `<topic>/<facet>[/<name>]`."""

    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]: ...

    def interpret(self, topic: Topic, config: Any, answers: Mapping[str, Answer]) -> Any: ...


def _speculative(topic: Topic, facet: str, item: str | None = None) -> bool:
    return topic.when_for(facet, item).mode != "always"


def to_choice(a: ChoiceAnswer) -> Choice:
    return Choice(a.choice, a.confidence, dict(a.probabilities))


def to_score(a: ScoreAnswer, levels: tuple[Any, ...]) -> Score:
    def label(i: int) -> str:
        return levels[i] if isinstance(levels[i], str) else str(i)

    best = max(a.probabilities, key=a.probabilities.__getitem__)
    return Score(
        value=a.score,
        level=label(best),
        confidence=a.confidence,
        probabilities={label(i): p for i, p in sorted(a.probabilities.items())},
        max_level=len(levels) - 1,
    )


# -- built-ins --------------------------------------------------------------


class CategoriesFacet:
    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        if any(not c.is_leaf for c in topic.categories.values()):
            raise JevFilterError(
                f"Topic {topic.name!r}: nested categories aren't supported by this "
                "version of jevfilter yet; use a flat list"
            )
        return {"": wording.categories(topic, topic.categories, _speculative(topic, "categories"))}

    def interpret(self, topic: Topic, config: Any, answers: Mapping[str, Answer]) -> Choice:
        a = answers[""]
        assert isinstance(a, ChoiceAnswer)
        return to_choice(a)


class FieldsFacet:
    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        out = {}
        for name, spec in topic.fields.items():
            candidates = content.candidates_for(topic.name, name)
            candidates = [c for c in candidates if c != wording.NONE_OF_THESE]
            if candidates:
                out[name] = wording.field(
                    topic, spec, candidates, _speculative(topic, "fields", name)
                )
        return out

    def warnings(self, topic: Topic, content: Content) -> list[str]:
        return [
            f"{topic.name}/fields/{name}: no candidates, so it wasn't asked"
            for name in topic.fields
            if not content.candidates_for(topic.name, name)
        ]

    def interpret(
        self, topic: Topic, config: Any, answers: Mapping[str, Answer]
    ) -> dict[str, Field]:
        out = {}
        for name in topic.fields:
            a = answers.get(name)
            if isinstance(a, ChoiceAnswer):
                value = None if a.choice == wording.NONE_OF_THESE else a.choice
                out[name] = Field(value, a.confidence, dict(a.probabilities))
            else:
                out[name] = Field(None, 0.0, {})
        return out


class ScoresFacet:
    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        return {
            name: wording.score(topic, spec, _speculative(topic, "scores", name))
            for name, spec in topic.scores.items()
        }

    def interpret(
        self, topic: Topic, config: Any, answers: Mapping[str, Answer]
    ) -> dict[str, Score]:
        out = {}
        for name, spec in topic.scores.items():
            a = answers.get(name)  # unasked in the second stage when the topic didn't pass
            if isinstance(a, ScoreAnswer):
                out[name] = to_score(a, spec.levels)
        return out


class FlagsFacet:
    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        return {
            name: wording.flag(topic, cond, _speculative(topic, "flags", name))
            for name, cond in topic.flags.items()
        }

    def interpret(
        self, topic: Topic, config: Any, answers: Mapping[str, Answer]
    ) -> dict[str, float]:
        out = {}
        for name in topic.flags:
            a = answers.get(name)  # unasked in the second stage when the topic didn't pass
            if isinstance(a, NoulAnswer):
                out[name] = a.p
        return out


BUILTIN: dict[str, Facet] = {
    "categories": CategoriesFacet(),
    "fields": FieldsFacet(),
    "scores": ScoresFacet(),
    "flags": FlagsFacet(),
}


# -- bases for custom facets ------------------------------------------------


class _SingleQuestionFacet:
    """Custom facet asking one question. Set `instructions` (and criteria/levels)."""

    key: str = ""  # set by @jevfilter.facet(...)
    instructions: ClassVar[Any] = None

    def _instructions(self, topic: Topic) -> Any:
        if self.instructions is None:
            raise JevFilterError(f"{type(self).__name__} needs `instructions`")
        return wording.custom(topic, self.instructions, _speculative(topic, self.key))


class NoulFacet(_SingleQuestionFacet):
    """A yes/no custom facet; its result is the probability of yes."""

    criteria: ClassVar[Mapping[str, Any] | None] = None

    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        return {"": Question("noul", self._instructions(topic), self.criteria)}

    def interpret(self, topic: Topic, config: Any, answers: Mapping[str, Answer]) -> float:
        a = answers[""]
        assert isinstance(a, NoulAnswer)
        return a.p


class ChoiceFacet(_SingleQuestionFacet):
    """A custom facet picking one of `criteria` (label → description)."""

    criteria: ClassVar[Mapping[str, Any]] = {}

    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        if not self.criteria:
            raise JevFilterError(f"{type(self).__name__} needs `criteria`")
        return {"": Question("choice", self._instructions(topic), dict(self.criteria))}

    def interpret(self, topic: Topic, config: Any, answers: Mapping[str, Answer]) -> Choice:
        a = answers[""]
        assert isinstance(a, ChoiceAnswer)
        return to_choice(a)


class ScoreFacet(_SingleQuestionFacet):
    """A custom facet rating content on ordered `levels` (low → high)."""

    levels: ClassVar[tuple[Any, ...]] = ()

    def questions(self, topic: Topic, config: Any, content: Content) -> dict[str, Question]:
        if len(self.levels) < 2:
            raise JevFilterError(f"{type(self).__name__} needs at least two `levels`")
        return {"": Question("score", self._instructions(topic), list(self.levels))}

    def interpret(self, topic: Topic, config: Any, answers: Mapping[str, Answer]) -> Score:
        a = answers[""]
        assert isinstance(a, ScoreAnswer)
        return to_score(a, tuple(self.levels))


__all__ = [
    "BUILTIN",
    "ChoiceFacet",
    "Facet",
    "NoulFacet",
    "ScoreFacet",
]
