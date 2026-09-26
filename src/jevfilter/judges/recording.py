"""Record real answers once, replay them forever.

A cassette is a JSONL file: one line per request, keyed by a hash of the
exact state and questions. Replaying is deterministic and free, so tests
and threshold sweeps don't call the API.

Cassettes contain the content that was judged. Don't commit ones recorded
from private data (e.g. real email).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import JudgeError
from .base import Judge, Question, Response, answer_from_dict, answer_to_dict


def request_key(state: Any, questions: Mapping[str, Question]) -> str:
    """Stable hash of a request: same state + questions → same key."""
    payload = {
        "state": state,
        "questions": {qid: q.to_dict() for qid, q in questions.items()},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict[str, Response]:
    out: dict[str, Response] = {}
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            r = entry["response"]
            out[entry["key"]] = Response(
                answers={qid: answer_from_dict(a) for qid, a in r["answers"].items()},
                model=r.get("model"),
                input_tokens=r.get("input_tokens"),
                request_id=r.get("request_id"),
            )
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f"{path}:{n}: not a valid cassette line ({e})") from None
    return out


class ReplayJudge:
    """Answer only from a cassette. An unrecorded request is a `JudgeError`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"no cassette at {self.path}")
        self._responses = _load(self.path)

    def __len__(self) -> int:
        return len(self._responses)

    def ask(self, state: Any, questions: Mapping[str, Question]) -> Response:
        key = request_key(state, questions)
        if key not in self._responses:
            raise JudgeError(
                f"request {key[:12]}… is not in {self.path.name}; record it with RecordingJudge"
            )
        return self._responses[key]


class RecordingJudge:
    """Wrap a (sync) judge and append each new request/answer to a cassette.

    Requests already in the cassette are replayed without calling `inner`,
    so re-running only pays for what's new. `rerecord=True` always calls
    `inner` and replaces the stored answer.
    """

    def __init__(self, inner: Judge, path: str | Path, *, rerecord: bool = False):
        if inspect.iscoroutinefunction(getattr(inner, "ask", None)):
            raise TypeError("RecordingJudge wraps a sync judge; use JevJudge, not AsyncJevJudge")
        self.inner = inner
        self.path = Path(path)
        self.rerecord = rerecord
        self._responses = _load(self.path)
        self._lock = threading.Lock()
        self.recorded = 0
        self.replayed = 0

    def ask(self, state: Any, questions: Mapping[str, Question]) -> Response:
        key = request_key(state, questions)
        with self._lock:
            if not self.rerecord and key in self._responses:
                self.replayed += 1
                return self._responses[key]
        response = self.inner.ask(state, questions)
        entry = {
            "key": key,
            "state": state,
            "questions": {qid: q.to_dict() for qid, q in questions.items()},
            "response": {
                "answers": {qid: answer_to_dict(a) for qid, a in response.answers.items()},
                "model": response.model,
                "input_tokens": response.input_tokens,
                "request_id": response.request_id,
            },
        }
        with self._lock:
            self._responses[key] = response
            self.recorded += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return response
