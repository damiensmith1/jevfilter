"""Chain two judges: ask the second only when the first fails."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from ..errors import JudgeError
from .base import Judge, Question, Response


class FallbackJudge:
    """Ask `primary`; if it raises `JudgeError`, ask `secondary`.

    ```python
    FallbackJudge(JevJudge(model="jev-1.13.0"), JevJudge())   # pinned → latest
    ```

    Answers from `secondary` are marked `extra["fallback"]` and the filter
    reports the result as `degraded`. Both judges must be sync (an
    `AsyncFilter` runs sync judges in a thread).
    """

    def __init__(self, primary: Judge, secondary: Judge):
        for name, judge in (("primary", primary), ("secondary", secondary)):
            if inspect.iscoroutinefunction(getattr(judge, "ask", None)):
                raise TypeError(f"FallbackJudge needs sync judges; {name} is async")
        self.primary = primary
        self.secondary = secondary
        self.fallbacks = 0

    def ask(self, state: Any, questions: Mapping[str, Question]) -> Response:
        try:
            return self.primary.ask(state, questions)
        except JudgeError as first:
            try:
                response = self.secondary.ask(state, questions)
            except JudgeError as second:
                raise JudgeError(
                    f"primary failed ({first}); fallback failed ({second})"
                ) from second
            self.fallbacks += 1
            response.extra = {**response.extra, "fallback": True, "primary_error": str(first)}
            return response
