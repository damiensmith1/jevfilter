"""Question templates: the only place definitions become model-facing text.

Jev doesn't see question IDs, so every question restates the topic in full.
Bump `WORDING_VERSION` whenever a template changes; results record it so
callers can tell which stored results were judged with older wording.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .judges.base import Question
from .topic import Category, FieldSpec, ScoreSpec, Topic

WORDING_VERSION = 1

NONE_OF_THESE = "none of these"

_PREMISE = "Assume `content` belongs to the topic defined here."


def topic_block(topic: Topic) -> dict[str, Any]:
    block: dict[str, Any] = {"name": topic.name, "description": topic.description}
    if topic.exclude is not None:
        block["does_not_include"] = topic.exclude
    return block


def _task(topic: Topic, task: str, speculative: bool, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if speculative:
        out["premise"] = _PREMISE
    out["task"] = task
    out.update(extra)
    out["topic"] = topic_block(topic)
    return out


def membership(topic: Topic) -> Question:
    instructions: dict[str, Any] = {
        "task": "Decide whether `content` belongs to the topic defined here.",
        "topic": topic_block(topic),
    }
    if topic.examples:
        instructions["examples"] = {
            "belongs": topic.examples.get("match", []),
            "does_not_belong": topic.examples.get("no", []),
        }
    return Question(
        "noul",
        instructions,
        {
            "true": "`content` fits the topic's description.",
            "false": (
                "`content` does not fit the topic's description, "
                "or it is something the topic does not include."
            ),
        },
    )


def categories(topic: Topic, options: Mapping[str, Category], speculative: bool) -> Question:
    return Question(
        "choice",
        _task(topic, "Which category best describes `content`?", speculative),
        {name: _category_criterion(c) for name, c in options.items()},
    )


def _category_criterion(c: Category) -> Any:
    """Plain description, or an object when the category has examples / exclusions."""
    if not c.examples and c.exclude is None:
        return c.description
    out: dict[str, Any] = {"description": c.description}
    if c.examples:
        out["examples"] = list(c.examples)
    if c.exclude is not None:
        out["does_not_include"] = c.exclude
    return out


def field(topic: Topic, spec: FieldSpec, candidates: Sequence[str], speculative: bool) -> Question:
    about: dict[str, Any] = {"name": spec.name}
    if spec.about:
        about["about"] = spec.about
    criteria: dict[str, Any] = {c: None for c in candidates}
    criteria[NONE_OF_THESE] = (
        f"`content` doesn't give its {spec.name}, or it isn't among the other options."
    )
    return Question(
        "choice",
        _task(
            topic,
            f"Which option is the {spec.name} that `content` refers to?",
            speculative,
            field=about,
        ),
        criteria,
    )


def score(topic: Topic, spec: ScoreSpec, speculative: bool) -> Question:
    task = f"Rate `content` on {spec.name}."
    extra = {"dimension": spec.about} if spec.about else {}
    return Question("score", _task(topic, task, speculative, **extra), list(spec.levels))


def flag(topic: Topic, condition: Any, speculative: bool) -> Question:
    return Question(
        "noul",
        _task(
            topic,
            "Is this statement about `content` true?",
            speculative,
            statement=condition,
        ),
    )


def custom(topic: Topic, instructions: Any, speculative: bool) -> Any:
    return _task(topic, instructions, speculative)


# -- helpers (no topic) -----------------------------------------------------


def choose(options: Mapping[str, Any], instructions: Any = None) -> Question:
    return Question(
        "choice", instructions or "Which option best describes `content`?", dict(options)
    )


def check(condition: Any) -> Question:
    return Question(
        "noul", {"task": "Is this statement about `content` true?", "statement": condition}
    )


def rate(dimension: str, levels: Sequence[Any]) -> Question:
    return Question("score", f"Rate `content` on {dimension}.", list(levels))


# -- item matching ----------------------------------------------------------

NEW_ITEM = "new"


def item_match(
    topic: Topic,
    options: Mapping[str, Any],
    content_fields: Mapping[str, Any],
) -> Question:
    """Choice over tracked items (label → description) plus "new"."""
    instructions: dict[str, Any] = {
        "task": (
            "`content` belongs to the topic defined here. Which of the tracked "
            'items listed as options is it about? Choose "new" if it is about '
            "a different one."
        ),
        "topic": topic_block(topic),
    }
    if content_fields:
        instructions["values_in_content"] = dict(content_fields)
    criteria = dict(options)
    criteria[NEW_ITEM] = f"A different {topic.name} item that is not among the other options."
    return Question("choice", instructions, criteria)
