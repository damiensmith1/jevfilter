"""The default backend: TypeSafe's Jev via `typesafe-sdk`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import JudgeError
from .base import ChoiceAnswer, NoulAnswer, Question, Response, ScoreAnswer


class JevJudge:
    """Ask Jev. Pass `model=` to pin a version (e.g. `"jev-1.13.0"`).

    `client` may be any object with the `TypeSafeClient.system_one` signature;
    otherwise one is created from the environment (`TYPESAFE_API_KEY`).
    """

    def __init__(self, model: str | None = None, client: Any = None, **client_kwargs: Any):
        self.model = model
        self._client = client
        self._client_kwargs = client_kwargs

    @property
    def client(self) -> Any:
        if self._client is None:
            from typesafe_sdk import TypeSafeClient

            self._client = TypeSafeClient(**self._client_kwargs)
        return self._client

    def ask(self, state: Any, questions: Mapping[str, Question]) -> Response:
        from typesafe_sdk import TypeSafeError

        try:
            res = self.client.system_one(
                state=state,
                questions={qid: _to_sdk(q) for qid, q in questions.items()},
                model=self.model,
            )
        except TypeSafeError as e:
            raise JudgeError(f"Jev request failed: {e}") from e
        answers: dict[str, Any] = {}
        for qid, a in res.answers.items():
            if a.type == "noul":
                answers[qid] = NoulAnswer(a.noul)
            elif a.type == "choice":
                answers[qid] = ChoiceAnswer(a.choice, a.confidence, dict(a.probabilities))
            else:
                answers[qid] = ScoreAnswer(
                    a.score, a.confidence, {int(k): v for k, v in a.probabilities.items()}
                )
        missing = set(questions) - set(answers)
        if missing:
            raise JudgeError(f"Jev returned no answer for: {', '.join(sorted(missing))}")
        return Response(
            answers=answers,
            model=res.model,
            input_tokens=res.usage.input_tokens,
            request_id=_request_id(res),
        )


def _to_sdk(q: Question) -> Any:
    from typesafe_sdk import Choice, Noul, Score

    if q.kind == "noul":
        return Noul(instructions=q.instructions, criteria=q.criteria)
    if q.kind == "choice":
        return Choice(instructions=q.instructions, criteria=q.criteria)
    return Score(instructions=q.instructions, criteria=q.criteria)


def _request_id(res: Any) -> str | None:
    try:
        return res.request_id
    except Exception:  # the SDK raises when the header is absent
        return None
