"""Packing questions into requests that fit Jev's context limits.

Jev allows 64k tokens per request (state + every question) and 32k for the
state plus the longest question. Tokens are estimated as characters ÷ 4 with
a safety margin, questions are packed first-fit in order, and the state is
resent with each request. State that can't fit is truncated per `truncate`,
with a warning.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import JevFilterError
from .judges.base import Question

Truncate = Literal["head", "head_tail", "error"] | Callable[[Any, int], Any]


@dataclass(frozen=True)
class Limits:
    """Context limits. `safety` leaves headroom for the rough token estimate."""

    request_tokens: int = 64_000
    state_and_question_tokens: int = 32_000
    safety: float = 0.9

    def __post_init__(self) -> None:
        if not 0 < self.safety <= 1:
            raise ValueError("safety must be in (0, 1]")
        if self.state_and_question_tokens > self.request_tokens:
            raise ValueError("state_and_question_tokens can't exceed request_tokens")


DEFAULT_LIMITS = Limits()


@dataclass
class Request:
    state: Any
    questions: dict[str, Question] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "questions": {qid: q.to_dict() for qid, q in self.questions.items()},
        }


def estimate_tokens(obj: Any) -> int:
    """Rough input-token estimate: characters of the JSON ÷ 4."""
    return len(json.dumps(obj, ensure_ascii=False)) // 4 + 1


def plan_requests(
    state: Any,
    questions: Mapping[str, Question],
    limits: Limits | None = None,
    truncate: Truncate = "head",
) -> tuple[list[Request], list[str]]:
    """Split `questions` into as few requests as fit. Returns (requests, warnings)."""
    limits = limits or DEFAULT_LIMITS
    if not questions:
        return [], []
    warnings: list[str] = []
    request_budget = int(limits.request_tokens * limits.safety)
    pair_budget = int(limits.state_and_question_tokens * limits.safety)

    sizes = {qid: estimate_tokens({qid: q.to_dict()}) for qid, q in questions.items()}
    longest_id = max(sizes, key=sizes.__getitem__)
    longest = sizes[longest_id]
    state_budget = min(pair_budget, request_budget) - longest
    if state_budget <= 0:
        raise JevFilterError(
            f"question {longest_id!r} alone is ~{longest} tokens, over the "
            f"{pair_budget}-token limit; shorten its definition"
        )

    state_tokens = estimate_tokens(state)
    if state_tokens > state_budget:
        state = _truncate(state, state_budget, truncate)
        new_tokens = estimate_tokens(state)
        warnings.append(
            f"content truncated from ~{state_tokens} to ~{new_tokens} tokens to fit Jev's limits"
        )
        state_tokens = new_tokens

    capacity = request_budget - state_tokens
    requests: list[Request] = []
    room: list[int] = []
    for qid, q in questions.items():
        for i, left in enumerate(room):
            if sizes[qid] <= left:
                requests[i].questions[qid] = q
                room[i] -= sizes[qid]
                break
        else:
            requests.append(Request(state, {qid: q}))
            room.append(capacity - sizes[qid])
    return requests, warnings


def _truncate(state: Any, max_tokens: int, how: Truncate) -> Any:
    if how == "error":
        raise JevFilterError(
            f"content is ~{estimate_tokens(state)} tokens, over the ~{max_tokens}-token "
            "limit, and truncate='error'"
        )
    if callable(how):
        out = how(state, max_tokens)
        if estimate_tokens(out) > max_tokens:
            raise JevFilterError("the truncate callable returned state that is still too long")
        return out
    if how not in ("head", "head_tail"):
        raise ValueError("truncate must be 'head', 'head_tail', 'error' or a callable")
    # Repeatedly shorten the longest string in the state until it fits.
    state = json.loads(json.dumps(state))
    for _ in range(100):
        excess = estimate_tokens(state) - max_tokens
        if excess <= 0:
            return state
        path, text = _longest_string(state)
        if path is None or not text:
            break
        keep = max(len(text) - excess * 4 - 16, 0)
        if path == ():
            state = _cut(text, keep, how)
        else:
            _set(state, path, _cut(text, keep, how))
    raise JevFilterError("content can't be truncated enough to fit; it has too little text")


def _cut(text: str, keep: int, how: str) -> str:
    if keep <= 0:
        return ""
    if how == "head":
        return text[:keep] + " …"
    half = keep // 2
    return text[:half] + " … " + text[len(text) - (keep - half) :]


def _longest_string(obj: Any, path: tuple[Any, ...] = ()) -> tuple[Any, str]:
    best: tuple[Any, str] = (None, "")
    if isinstance(obj, str):
        return path, obj
    items = (
        obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else ()
    )
    for key, value in items:
        p, s = _longest_string(value, (*path, key))
        if p is not None and len(s) > len(best[1]):
            best = (p, s)
    return best


def _set(obj: Any, path: tuple[Any, ...], value: str) -> None:
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value
