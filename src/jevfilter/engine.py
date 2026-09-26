"""The judge pipeline: compile → plan → guard → execute → interpret → decide → report.

`Filter` runs it synchronously; `AsyncFilter` runs the same stages with an
async backend and adds `judge_many`. Only execution differs between them.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from . import registry, wording
from .budget import DEFAULT_PRICE_PER_MTOK, Budget
from .content import Content, as_content
from .defaults import default_async_judge, default_judge
from .errors import JevFilterError, JudgeError
from .facets import BUILTIN, Facet
from .items import as_items, content_values, prefilter
from .judges.base import (
    Answer,
    AsyncJudge,
    ChoiceAnswer,
    Judge,
    NoulAnswer,
    Question,
    Response,
    answer_to_dict,
)
from .plan import Limits, Request, Truncate, estimate_tokens, plan_requests
from .policy import Policy, ThresholdPolicy, TopicAnswers
from .result import ItemMatch, Result, Score, TopicResult
from .topic import Topic, Topics, TopicSource, as_topics
from .version import __version__

__all__ = ["AsyncFilter", "Filter", "Plan", "estimate_tokens"]

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


@dataclass
class _Batch:
    """Every response for one piece of content, merged."""

    responses: list[Response] = field(default_factory=list)
    degraded: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        # A FallbackJudge marks answers it had to get from its secondary.
        if any(r.extra.get("fallback") for r in self.responses):
            self.degraded = True
            if self.error is None:
                errors = [r.extra.get("primary_error") for r in self.responses]
                self.error = next((e for e in errors if e), None)

    @property
    def failed(self) -> bool:
        return self.error is not None and not self.responses

    @property
    def answers(self) -> dict[str, Answer]:
        return {qid: a for r in self.responses for qid, a in r.answers.items()}

    @property
    def input_tokens(self) -> int | None:
        counts = [r.input_tokens for r in self.responses if r.input_tokens is not None]
        return sum(counts) if counts else None

    @property
    def models(self) -> list[str]:
        return list(dict.fromkeys(r.model for r in self.responses if r.model))

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(r.request_id for r in self.responses if r.request_id)


@dataclass
class _MatchPlan:
    topic: Topic
    labels: dict[str, Any]  # option label → item id
    qid: str
    requests: list[Request]
    warnings: list[str]


@dataclass(frozen=True)
class Plan:
    """What `judge()` would send, without sending it."""

    requests: list[dict[str, Any]]
    estimated_input_tokens: int
    cost_usd: float
    warnings: tuple[str, ...] = ()
    followup_requests: list[dict[str, Any]] = field(default_factory=list)
    """With `speculative=False`: the second-stage requests if every topic
    passed membership (the worst case). Only matching topics are asked."""
    max_cost_usd: float = 0.0
    """`cost_usd` plus every follow-up request."""


class Filter:
    """Topics plus engine configuration. `judge(content)` → `Result`.

    ```python
    f = Filter(Topic.load("topics/"), budget=Budget(usd=1.00, per_minute=60))
    r = f.judge({"from": "...", "subject": "...", "body": "..."})
    ```
    """

    def __init__(
        self,
        topics: TopicSource | Topics,
        *,
        judge: Judge | AsyncJudge | None = None,
        policy: Policy | None = None,
        on_error: OnError = "raise",
        budget: Budget | None = None,
        limits: Limits | None = None,
        truncate: Truncate = "head",
        price_per_mtok: float | None = None,
        include_requests: bool = False,
        speculative: bool = True,
    ):
        self.topics = as_topics(topics)
        if not self.topics:
            raise ValueError("Filter needs at least one topic")
        if isinstance(on_error, str) and on_error not in ("raise", "review"):
            raise ValueError("on_error must be 'raise', 'review' or a Judge")
        self._backend = judge
        self.policy: Policy = policy or ThresholdPolicy()
        self.on_error = on_error
        self.budget = budget
        self.limits = limits or Limits()
        self.truncate = truncate
        if price_per_mtok is None:
            price_per_mtok = budget.price_per_mtok if budget else DEFAULT_PRICE_PER_MTOK
        self.price_per_mtok = price_per_mtok
        self.include_requests = include_requests
        self.speculative = speculative

    @property
    def backend(self) -> Any:
        """The judge backend: the one given, else the `configure()`d default."""
        return self._backend or default_judge()

    # -- public -------------------------------------------------------------

    def judge(self, content: Any) -> Result:
        """Judge one piece of content (text, a dict, or `Content`) against every topic.

        With `speculative=False`, membership is asked first and the other
        facets only for topics that might belong (two round trips, fewer
        tokens when most topics don't match).
        """
        compiled = self._compile(as_content(content))
        if self.speculative:
            requests, warnings = self._plan(compiled)
            return self._report(compiled, requests, warnings, self._run(requests))
        first, w1 = self._plan(compiled, self._first_stage(compiled))
        b1 = self._run(first)
        if b1.failed:
            return self._report(compiled, first, w1, b1)
        second, w2 = self._plan(compiled, self._second_stage(compiled, b1.answers))
        b2 = self._run(second) if second else _Batch()
        return self._report(compiled, first + second, w1 + w2, _merge(b1, b2))

    def explain(self, content: Any) -> Plan:
        """The exact request payloads and an estimated cost, without calling the backend."""
        compiled = self._compile(as_content(content))
        followup: list[Request] = []
        if self.speculative:
            requests, warnings = self._plan(compiled)
        else:
            first = self._first_stage(compiled)
            requests, warnings = self._plan(compiled, first)
            rest = {q: v for q, v in compiled.questions.items() if q not in first}
            followup, w2 = self._plan(compiled, rest)
            warnings += w2
        payloads = [r.payload() for r in requests]
        extra = [r.payload() for r in followup]
        tokens = sum(estimate_tokens(p) for p in payloads)
        extra_tokens = sum(estimate_tokens(p) for p in extra)
        return Plan(
            requests=payloads,
            estimated_input_tokens=tokens,
            cost_usd=tokens * self.price_per_mtok / 1e6,
            warnings=tuple(dict.fromkeys(compiled.warnings + warnings)),
            followup_requests=extra,
            max_cost_usd=(tokens + extra_tokens) * self.price_per_mtok / 1e6,
        )

    def match_item(
        self,
        content: Any,
        topic: Topic | str,
        items: Iterable[Any],
        *,
        result: TopicResult | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> ItemMatch:
        """Which of `items` is `content` about, or a new one?

        Items whose `track.match_on` fields differ from the content's (taken
        from `result` and/or `fields=`) are dropped in code first. If none
        remain the answer is "new" with no Jev call; otherwise Jev picks one
        of the survivors or "new".
        """
        prepared = self._prepare_match(as_content(content), topic, items, result, fields)
        if isinstance(prepared, ItemMatch):
            return prepared
        return self._finish_match(prepared, self._run(prepared.requests))

    # -- 1. compile / 2. plan -----------------------------------------------

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

    def _plan(
        self, compiled: _Compiled, questions: Mapping[str, Question] | None = None
    ) -> tuple[list[Request], list[str]]:
        qs = compiled.questions if questions is None else questions
        return plan_requests(compiled.state, qs, self.limits, self.truncate)

    def _first_stage(self, compiled: _Compiled) -> dict[str, Question]:
        """Membership, plus any facet marked `when: always` (not speculative)."""
        out = {}
        for qid, q in compiled.questions.items():
            route = compiled.routes[qid]
            t = self.topics[route.topic]
            item = route.local if route.facet in ("fields", "scores", "flags") else None
            if route.facet == "membership" or t.when_for(route.facet, item).mode == "always":
                out[qid] = q
        return out

    def _second_stage(
        self, compiled: _Compiled, answers: Mapping[str, Answer]
    ) -> dict[str, Question]:
        """The remaining questions, for topics whose membership isn't a clear no."""
        keep = set()
        for t in self.topics.values():
            a = answers.get(f"{t.name}/membership")
            reject = t.thresholds.get("reject", getattr(self.policy, "reject", 0.3))
            if isinstance(a, NoulAnswer) and a.p >= reject:
                keep.add(t.name)
        return {
            qid: q
            for qid, q in compiled.questions.items()
            if qid not in answers and compiled.routes[qid].topic in keep
        }

    # -- 3/4. guard + execute -----------------------------------------------

    def _run(self, requests: list[Request]) -> _Batch:
        try:
            return _Batch([self._ask(self.backend, r, guard=True) for r in requests])
        except JudgeError as e:
            if self.on_error == "raise":
                raise
            if self.on_error == "review":
                return _Batch(degraded=True, error=str(e))
            fallback = self.on_error
            return _Batch(
                [self._ask(fallback, r, guard=False) for r in requests],
                degraded=True,
                error=str(e),
            )

    def _ask(self, judge: Any, request: Request, *, guard: bool) -> Response:
        if guard and self.budget is not None:
            self.budget.check()
        response = judge.ask(request.state, request.questions)
        if self.budget is not None:
            self.budget.record(response.input_tokens)
        return response

    # -- 5/6/7. interpret, decide, report -----------------------------------

    def _cost(self, tokens: int | None) -> float | None:
        return tokens * self.price_per_mtok / 1e6 if tokens is not None else None

    def _report(
        self,
        compiled: _Compiled,
        requests: list[Request],
        plan_warnings: list[str],
        batch: _Batch,
    ) -> Result:
        warnings = list(dict.fromkeys(compiled.warnings + plan_warnings))
        if batch.error:
            warnings.append(f"backend_error: {batch.error}")
        if len(batch.models) > 1:
            warnings.append(f"requests were answered by different models: {batch.models}")
        topics: dict[str, TopicResult] = {}
        if batch.failed:
            for t in self.topics.values():
                topics[t.name] = TopicResult(
                    t.name, "review", 0.0, ("backend_error",), topic_version=t.version
                )
        else:
            grouped = _group(compiled.routes, batch.answers)
            for t in self.topics.values():
                topics[t.name] = self._topic_result(t, grouped.get(t.name, {}))

        answers = batch.answers
        return Result(
            topics=topics,
            model=batch.models[0] if batch.models else None,
            request_ids=batch.request_ids,
            input_tokens=batch.input_tokens,
            cost_usd=self._cost(batch.input_tokens),
            wording_version=wording.WORDING_VERSION,
            jevfilter_version=__version__,
            degraded=batch.degraded,
            warnings=tuple(warnings),
            raw={qid: answer_to_dict(a) for qid, a in answers.items()},
            requests=[r.payload() for r in requests] if self.include_requests else None,
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

    # -- item matching ------------------------------------------------------

    def _topic(self, topic: Topic | str) -> Topic:
        if isinstance(topic, Topic):
            return topic
        try:
            return self.topics[topic]
        except KeyError:
            raise ValueError(f"unknown topic {topic!r}") from None

    def _match_base(self, t: Topic, **kw: Any) -> dict[str, Any]:
        return {
            "topic": t.name,
            "topic_version": t.version,
            "wording_version": wording.WORDING_VERSION,
            "jevfilter_version": __version__,
            **kw,
        }

    def _prepare_match(
        self,
        content: Content,
        topic: Topic | str,
        items: Iterable[Any],
        result: TopicResult | None,
        fields: Mapping[str, Any] | None,
    ) -> ItemMatch | _MatchPlan:
        t = self._topic(topic)
        values = content_values(t, result, fields)
        survivors = prefilter(t, as_items(items), values)
        if not survivors:
            return ItemMatch(None, "match", **self._match_base(t))
        labels = {s.label: s.id for s in survivors}
        question = wording.item_match(t, {s.label: s.describe() for s in survivors}, values)
        qid = f"{t.name}/item"
        requests, warnings = plan_requests(
            content.as_state(), {qid: question}, self.limits, self.truncate
        )
        return _MatchPlan(t, labels, qid, requests, warnings)

    def _finish_match(self, plan: _MatchPlan, batch: _Batch) -> ItemMatch:
        warnings = list(plan.warnings)
        if batch.error:
            warnings.append(f"backend_error: {batch.error}")
        base = self._match_base(
            plan.topic,
            asked=True,
            candidates=tuple(plan.labels.values()),
            model=batch.models[0] if batch.models else None,
            request_ids=batch.request_ids,
            input_tokens=batch.input_tokens,
            cost_usd=self._cost(batch.input_tokens),
            degraded=batch.degraded,
            warnings=tuple(warnings),
        )
        if batch.failed:
            return ItemMatch(None, "review", reasons=("backend_error",), **base)
        answer = batch.answers.get(plan.qid)
        if not isinstance(answer, ChoiceAnswer):
            raise JudgeError(f"no item-match answer for topic {plan.topic.name!r}")

        def key(label: str) -> str:
            return str(plan.labels[label]) if label in plan.labels else label

        item_id = None if answer.choice == wording.NEW_ITEM else plan.labels.get(answer.choice)
        min_conf = plan.topic.thresholds.get(
            "min_confidence", getattr(self.policy, "min_confidence", 0.5)
        )
        low = answer.confidence < min_conf
        return ItemMatch(
            item_id,
            "review" if low else "match",
            confidence=answer.confidence,
            probabilities={key(k): p for k, p in answer.probabilities.items()},
            reasons=("low_confidence:item",) if low else (),
            **base,
        )


class AsyncFilter(Filter):
    """`Filter` with an async API and `judge_many` for batches.

    Uses an async judge (`AsyncJevJudge` by default); a sync `Judge` such as
    `FakeJudge` also works and runs in a worker thread. A piece of content
    that needs several requests sends them concurrently.
    """

    @property
    def backend(self) -> Any:
        return self._backend or default_async_judge()

    async def judge(self, content: Any) -> Result:  # type: ignore[override]
        compiled = self._compile(as_content(content))
        if self.speculative:
            requests, warnings = self._plan(compiled)
            return self._report(compiled, requests, warnings, await self._run_async(requests))
        first, w1 = self._plan(compiled, self._first_stage(compiled))
        b1 = await self._run_async(first)
        if b1.failed:
            return self._report(compiled, first, w1, b1)
        second, w2 = self._plan(compiled, self._second_stage(compiled, b1.answers))
        b2 = await self._run_async(second) if second else _Batch()
        return self._report(compiled, first + second, w1 + w2, _merge(b1, b2))

    async def judge_many(
        self,
        contents: Iterable[Any],
        *,
        concurrency: int = 8,
        return_exceptions: bool = False,
    ) -> list[Any]:
        """Judge many pieces of content, at most `concurrency` at once.

        Results come back in input order. With `return_exceptions=True`,
        failures (e.g. `BudgetExceeded`) are returned in place instead of
        raised, so one refusal doesn't lose the rest of a batch.
        """
        if concurrency < 1:
            raise ValueError("concurrency must be ≥ 1")
        gate = asyncio.Semaphore(concurrency)

        async def one(content: Any) -> Result:
            async with gate:
                return await self.judge(content)

        return await asyncio.gather(
            *(one(c) for c in contents), return_exceptions=return_exceptions
        )

    async def match_item(  # type: ignore[override]
        self,
        content: Any,
        topic: Topic | str,
        items: Iterable[Any],
        *,
        result: TopicResult | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> ItemMatch:
        prepared = self._prepare_match(as_content(content), topic, items, result, fields)
        if isinstance(prepared, ItemMatch):
            return prepared
        return self._finish_match(prepared, await self._run_async(prepared.requests))

    async def _run_async(self, requests: list[Request]) -> _Batch:
        async def all_with(judge: Any, guard: bool) -> list[Response]:
            outcomes = await asyncio.gather(
                *(self._ask_async(judge, r, guard=guard) for r in requests),
                return_exceptions=True,
            )
            for o in outcomes:
                if isinstance(o, BaseException):
                    raise o
            return list(outcomes)  # type: ignore[arg-type]

        try:
            return _Batch(await all_with(self.backend, True))
        except JudgeError as e:
            if self.on_error == "raise":
                raise
            if self.on_error == "review":
                return _Batch(degraded=True, error=str(e))
            return _Batch(await all_with(self.on_error, False), degraded=True, error=str(e))

    async def _ask_async(self, judge: Any, request: Request, *, guard: bool) -> Response:
        if guard and self.budget is not None:
            self.budget.check()
        if inspect.iscoroutinefunction(judge.ask):
            response = await judge.ask(request.state, request.questions)
        else:
            response = await asyncio.to_thread(judge.ask, request.state, request.questions)
        if self.budget is not None:
            self.budget.record(response.input_tokens)
        return response


# -- helpers ----------------------------------------------------------------


def _merge(first: _Batch, second: _Batch) -> _Batch:
    """Combine two stages. If the second failed outright, the whole result fails."""
    if second.failed:
        return second
    return _Batch(
        first.responses + second.responses,
        degraded=first.degraded or second.degraded,
        error=first.error or second.error,
    )


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
