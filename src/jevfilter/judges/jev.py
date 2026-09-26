"""The default backend: TypeSafe's Jev via `typesafe-sdk`."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ..errors import JudgeError
from .base import ChoiceAnswer, NoulAnswer, Question, Response, ScoreAnswer


class _JevOptions:
    def __init__(
        self,
        model: str | None,
        api_key: str | None,
        client: Any,
        client_kwargs: dict[str, Any],
    ):
        if client is not None and (api_key is not None or client_kwargs):
            raise ValueError("pass either client= or client options (api_key=, ...), not both")
        self.model = model
        self._client = client
        self._client_kwargs = {**client_kwargs, **({"api_key": api_key} if api_key else {})}


class JevJudge(_JevOptions):
    """Ask Jev.

    - `model` pins a version (e.g. `"jev-1.13.0"`); default is the SDK's.
    - `api_key` defaults to the `TYPESAFE_API_KEY` environment variable.
      jevfilter never reads `.env` files; load one yourself if you use it.
    - `client` may be any object with the `TypeSafeClient.system_one`
      signature; other keyword arguments go to `TypeSafeClient`.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        client: Any = None,
        **client_kwargs: Any,
    ):
        super().__init__(model, api_key, client, client_kwargs)

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
                state=state, questions=_to_sdk_all(questions), model=self.model
            )
        except TypeSafeError as e:
            raise JudgeError(f"Jev request failed: {e}") from e
        return _response(res, questions)


class AsyncJevJudge(_JevOptions):
    """Async twin of `JevJudge`, used by `AsyncFilter`. Same options.

    `client` may be any object with an async `system_one`. Without one, a
    `AsyncTypeSafeClient` is created per event loop (clients can't be
    shared across loops).
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        client: Any = None,
        **client_kwargs: Any,
    ):
        super().__init__(model, api_key, client, client_kwargs)
        self._loop: Any = None
        self._loop_client: Any = None

    def _client_for_loop(self) -> Any:
        if self._client is not None:
            return self._client
        loop = asyncio.get_running_loop()
        # Compare the loop object itself: ids can be reused once a loop is gone.
        if self._loop is not loop:
            from typesafe_sdk import AsyncTypeSafeClient

            self._loop = loop
            self._loop_client = AsyncTypeSafeClient(**self._client_kwargs)
        return self._loop_client

    async def ask(self, state: Any, questions: Mapping[str, Question]) -> Response:
        from typesafe_sdk import TypeSafeError

        try:
            res = await self._client_for_loop().system_one(
                state=state, questions=_to_sdk_all(questions), model=self.model
            )
        except TypeSafeError as e:
            raise JudgeError(f"Jev request failed: {e}") from e
        return _response(res, questions)


def _to_sdk_all(questions: Mapping[str, Question]) -> dict[str, Any]:
    from typesafe_sdk import Choice, Noul, Score

    out: dict[str, Any] = {}
    for qid, q in questions.items():
        if q.kind == "noul":
            out[qid] = Noul(instructions=q.instructions, criteria=q.criteria)
        elif q.kind == "choice":
            out[qid] = Choice(instructions=q.instructions, criteria=q.criteria)
        else:
            out[qid] = Score(instructions=q.instructions, criteria=q.criteria)
    return out


def _response(res: Any, questions: Mapping[str, Question]) -> Response:
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


def _request_id(res: Any) -> str | None:
    try:
        return res.request_id
    except Exception:  # the SDK raises when the header is absent
        return None
