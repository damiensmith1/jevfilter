"""The judge pipeline: compile → plan → execute → interpret → decide → report.

This version sends every question for one piece of content in a single
request. Packing / splitting across Jev's context limits comes later.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from . import registry, wording
from .content import Content, as_content
from .errors import JevFilterError, JudgeError
from .facets import BUILTIN, Facet
from .judges.base import Answer, Judge, NoulAnswer, Question, Response, answer_to_dict
from .policy import Policy, ThresholdPolicy, TopicAnswers
from .result import Result, Score, TopicResult
from .topic import Topic, Topics, TopicSource, as_topics
from .version import __version__

DEFAULT_PRICE_PER_MTOK = 0.042
"""Jev's published input price (USD per million tokens) when this was written."""

OnError = Literal["raise", "review"] | Judge


@dataclass(frozen=True)
class _Route:
    topic: str
    facet: str  # "membership", a built-in facet key or a custom facet key
    local: str  # the facet's own question name ("" for a single question)


@dataclass
class _Compiled:
    state: dict[str, Any]
    questions: dict[str, Question]
    routes: dict[str, _Route]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Plan:
    """What `judge()` would send, without sending it."""

    requests: list[dict[str, Any]]
    estimated_input_tokens: int
    cost_usd: float
    warnings: tuple[str, ...] = ()


class Filter:
    """Topics plus engine configuration. `judge(content)` → `Result`.

    ```python
    f = Filter(Topic.load("topics/"))
    r = f.judge({"from": "...", "subject": "...", "body": "..."})
    ```
    """

    def __init__(
        self,
        topics: TopicSource | Topics,
        *,
        judge: Judge | None = None,
        policy: Policy | None = None,
        on_error: OnError = "raise",
        price_per_mtok: float = DEFAULT_PRICE_PER_MTOK,
        include_requests: bool = False,
    ):
        self.topics = as_topics(topics)
        if not self.topics:
            raise ValueError("Filter needs at least one topic")
        if isinstance(on_error, str) and on_error not in ("raise", "review"):
            raise ValueError("on_error must be 'raise', 'review' or a Judge")
        self._backend = judge
        self.policy: Policy = policy or ThresholdPolicy()
        self.on_error = on_error
        self.price_per_mtok = price_per_mtok
        self.include_requests = include_requests

    @property
    def backend(self) -> Judge:
        """The judge backend (Jev unless one was given)."""
        if self._backend is None:
            from .judges.jev import JevJudge

            self._backend = JevJudge()
        return self._backend

    # -- public -------------------------------------------------------------

    def judge(self, content: Any) -> Result:
        """Judge one piece of content (text, a dict, or `Content`) against every topic."""
        c = as_content(content)
        compiled = self._compile(c)
        response, degraded, error = self._execute(compiled)
        return self._report(compiled, response, degraded, error)

    def explain(self, content: Any) -> Plan:
        """The exact request payloads and an estimated cost, without calling the backend."""
        compiled = self._compile(as_content(content))
        request = _payload(compiled)
        tokens = estimate_tokens(request)
        return Plan(
            requests=[request],
            estimated_input_tokens=tokens,
            cost_usd=tokens * self.price_per_mtok / 1e6,
            warnings=tuple(compiled.warnings),
        )

    # -- 1. compile ---------------------------------------------------------

    def _compile(self, content: Content) -> _Compiled:
        compiled = _Compiled(content.as_state(), {}, {})
        for t in self.topics.values():
            qid = f"{t.name}/membership"
            compiled.questions[qid] = wording.membership(t)
            compiled.routes[qid] = _Route(t.name, "membership", "")
            for key, facet, config in _facets_of(t):
                for local, q in facet.questions(t, config, content).items():
                    qid = f"{t.name}/{key}/{local}" if local else f"{t.name}/{key}"
                    compiled.questions[qid] = q
                    compiled.routes[qid] = _Route(t.name, key, local)
                warn = getattr(facet, "warnings", None)
                if warn is not None:
                    compiled.warnings.extend(warn(t, content))
        return compiled

    # -- 3/4. execute -------------------------------------------------------

    def _execute(self, compiled: _Compiled) -> tuple[Response | None, bool, str | None]:
        try:
            return self.backend.ask(compiled.state, compiled.questions), False, None
        except JudgeError as e:
            if self.on_error == "raise":
                raise
            if self.on_error == "review":
                return None, True, str(e)
            fallback = self.on_error
            return fallback.ask(compiled.state, compiled.questions), True, str(e)

    # -- 5/6/7. interpret, decide, report -----------------------------------

    def _report(
        self,
        compiled: _Compiled,
        response: Response | None,
        degraded: bool,
        error: str | None,
    ) -> Result:
        warnings = list(compiled.warnings)
        if error:
            warnings.append(f"backend_error: {error}")
        topics: dict[str, TopicResult] = {}
        if response is None:
            for t in self.topics.values():
                topics[t.name] = TopicResult(
                    t.name, "review", 0.0, ("backend_error",), topic_version=t.version
                )
        else:
            grouped = _group(compiled.routes, response.answers)
            for t in self.topics.values():
                topics[t.name] = self._topic_result(t, grouped.get(t.name, {}))

        tokens = response.input_tokens if response else None
        return Result(
            topics=topics,
            model=response.model if response else None,
            request_ids=tuple(r for r in [response.request_id if response else None] if r),
            input_tokens=tokens,
            cost_usd=tokens * self.price_per_mtok / 1e6 if tokens is not None else None,
            wording_version=wording.WORDING_VERSION,
            jevfilter_version=__version__,
            degraded=degraded,
            warnings=tuple(warnings),
            raw={qid: answer_to_dict(a) for qid, a in response.answers.items()} if response else {},
            requests=[_payload(compiled)] if self.include_requests else None,
        )

    def _topic_result(self, t: Topic, answers: dict[str, dict[str, Answer]]) -> TopicResult:
        membership = answers.get("membership", {}).get("")
        if not isinstance(membership, NoulAnswer):
            raise JudgeError(f"no membership answer for topic {t.name!r}")

        values: dict[str, Any] = {}
        for key, facet, config in _facets_of(t):
            if key in answers or key == "fields":
                values[key] = facet.interpret(t, config, answers.get(key, {}))

        # `when: {category: [...]}` is independent of the outcome, so apply it first.
        category = values.get("categories")
        _filter(t, values, lambda w: w.mode != "category" or _in(category, w.categories))

        custom = {k: v for k, v in values.items() if k not in BUILTIN}
        draft = TopicAnswers(
            p=membership.p,
            category=values.get("categories"),
            fields=values.get("fields", {}),
            scores=values.get("scores", {}),
            flags=values.get("flags", {}),
            facets=custom,
        )
        decision = self.policy.decide(t, draft)

        # Everything but `always` facets is only used when content belongs.
        if decision.outcome == "no":
            _filter(t, values, lambda w: w.mode == "always")

        scores: dict[str, Score] = values.get("scores", {})
        return TopicResult(
            topic=t.name,
            outcome=decision.outcome,
            p=membership.p,
            reasons=decision.reasons,
            category=values.get("categories"),
            fields=values.get("fields", {}),
            scores=scores,
            flags=values.get("flags", {}),
            composites=_composites(t, scores),
            facets={k: v for k, v in values.items() if k not in BUILTIN},
            topic_version=t.version,
        )


# -- helpers ----------------------------------------------------------------


def _facets_of(t: Topic) -> list[tuple[str, Facet, Any]]:
    out: list[tuple[str, Facet, Any]] = []
    for key in BUILTIN:
        config = getattr(t, key)
        if config:
            out.append((key, BUILTIN[key], config))
    for key, config in t.custom.items():
        facet = registry.get_facet(key)
        if facet is None:
            raise JevFilterError(f"Topic {t.name!r}: custom facet {key!r} is no longer registered")
        out.append((key, facet, config))
    return out


def _group(
    routes: Mapping[str, _Route], answers: Mapping[str, Answer]
) -> dict[str, dict[str, dict[str, Answer]]]:
    grouped: dict[str, dict[str, dict[str, Answer]]] = {}
    for qid, a in answers.items():
        route = routes.get(qid)
        if route is not None:
            grouped.setdefault(route.topic, {}).setdefault(route.facet, {})[route.local] = a
    return grouped


def _filter(t: Topic, values: dict[str, Any], keep: Any) -> None:
    """Drop facet answers (or items within them) whose `when` fails `keep`."""
    for key in list(values):
        value = values[key]
        if key in ("fields", "scores", "flags"):
            values[key] = {n: v for n, v in value.items() if keep(t.when_for(key, n))}
        elif not keep(t.when_for(key)):
            del values[key]


def _in(category: Any, names: tuple[str, ...]) -> bool:
    return category is not None and category.value in names


def _composites(t: Topic, scores: Mapping[str, Score]) -> dict[str, float]:
    out = {}
    for name, weights in t.composites.items():
        if all(s in scores for s in weights):
            out[name] = sum(w * scores[s].normalized for s, w in weights.items())
    return out


def _payload(compiled: _Compiled) -> dict[str, Any]:
    return {
        "state": compiled.state,
        "questions": {qid: q.to_dict() for qid, q in compiled.questions.items()},
    }


def estimate_tokens(payload: Any) -> int:
    """Rough input-token estimate: characters ÷ 4."""
    return len(json.dumps(payload, ensure_ascii=False)) // 4 + 1
