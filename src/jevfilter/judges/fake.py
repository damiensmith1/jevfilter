"""A scripted judge for tests and examples. Never touches the network."""

from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatchcase
from typing import Any

from ..errors import JudgeError
from .base import Answer, ChoiceAnswer, NoulAnswer, Question, Response, ScoreAnswer


class FakeJudge:
    """Answer from a script keyed by question ID (glob patterns allowed).

    Script values by question kind:

    - noul: a probability, e.g. `0.95`
    - choice: a label (`"applied"`, confidence 1.0) or `{label: probability}`
    - score: an expected score (`1.5`) or `{level_index: probability}`
    - any kind: a callable `(state, question) -> value`, or an `Answer`

    Unscripted questions get `default_noul` (0.0), the first choice option,
    or score 0. Every call is recorded in `calls`.
    """

    def __init__(
        self,
        script: Mapping[str, Any] | None = None,
        *,
        default_noul: float = 0.0,
        model: str = "fake",
        tokens_per_char: float = 0.25,
        fail: Exception | None = None,
    ):
        self.script = dict(script or {})
        self.default_noul = default_noul
        self.model = model
        self.tokens_per_char = tokens_per_char
        self.fail = fail
        self.calls: list[tuple[Any, dict[str, Question]]] = []

    def ask(self, state: Any, questions: Mapping[str, Question]) -> Response:
        self.calls.append((state, dict(questions)))
        if self.fail is not None:
            raise JudgeError(str(self.fail)) from self.fail
        answers = {qid: self._answer(qid, q, state) for qid, q in questions.items()}
        size = len(repr(state)) + sum(len(repr(q.to_dict())) for q in questions.values())
        return Response(
            answers=answers,
            model=self.model,
            input_tokens=int(size * self.tokens_per_char),
            request_id=f"fake-{len(self.calls)}",
        )

    def _lookup(self, qid: str) -> Any:
        if qid in self.script:
            return self.script[qid]
        for pattern, value in self.script.items():
            if fnmatchcase(qid, pattern):
                return value
        return None

    def _answer(self, qid: str, q: Question, state: Any) -> Answer:
        value = self._lookup(qid)
        if callable(value) and not isinstance(value, type):
            value = value(state, q)
        if isinstance(value, (NoulAnswer, ChoiceAnswer, ScoreAnswer)):
            return value
        if q.kind == "noul":
            return NoulAnswer(float(self.default_noul if value is None else value))
        if q.kind == "choice":
            return _choice(q, value)
        return _score(q, value)


def _choice(q: Question, value: Any) -> ChoiceAnswer:
    labels = list(q.criteria)
    if value is None:
        value = labels[0]
    probs = {value: 1.0} if isinstance(value, str) else {k: float(v) for k, v in value.items()}
    unknown = set(probs) - set(labels)
    if unknown:
        raise ValueError(f"FakeJudge: {sorted(unknown)} not among options {labels}")
    probs = {label: probs.get(label, 0.0) for label in labels}
    best = max(probs, key=probs.__getitem__)
    return ChoiceAnswer(best, probs[best], probs)


def _score(q: Question, value: Any) -> ScoreAnswer:
    n = len(q.criteria)
    if value is None:
        value = 0
    if isinstance(value, Mapping):
        probs = {int(k): float(v) for k, v in value.items()}
    else:
        # Spread an expected score over its two neighbouring levels.
        x = min(max(float(value), 0.0), n - 1.0)
        lo = int(x)
        hi = min(lo + 1, n - 1)
        probs = {lo: 1.0 - (x - lo)}
        if hi != lo:
            probs[hi] = x - lo
    probs = {i: probs.get(i, 0.0) for i in range(n)}
    expected = sum(i * p for i, p in probs.items())
    return ScoreAnswer(expected, max(probs.values()), probs)
