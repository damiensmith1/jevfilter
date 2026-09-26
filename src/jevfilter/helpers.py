"""Level 1: one-call helpers. No topics, no config.

```python
jf.choose("I was charged twice", ["billing", "bug", "feature request"])
jf.check("Send the form by Friday?", {"needs_reply": "The sender wants a reply"})
jf.rate("The site is down", "severity", ["cosmetic", "degraded", "blocking"])
```
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import wording
from .content import as_content
from .defaults import default_judge
from .facets import to_choice, to_score
from .judges.base import ChoiceAnswer, Judge, NoulAnswer, Question, ScoreAnswer
from .result import Choice, Score


def _ask(content: Any, questions: dict[str, Question], judge: Judge | None) -> Any:
    backend = judge or default_judge()
    return backend.ask(as_content(content).as_state(), questions).answers


def choose(
    content: Any,
    options: Sequence[str] | Mapping[str, Any],
    *,
    instructions: Any = None,
    judge: Judge | None = None,
) -> Choice:
    """Pick the option that best fits. `options` may map label → description."""
    opts = dict(options) if isinstance(options, Mapping) else {o: None for o in options}
    if len(opts) < 2:
        raise ValueError("choose() needs at least two options")
    a = _ask(content, {"choice": wording.choose(opts, instructions)}, judge)["choice"]
    assert isinstance(a, ChoiceAnswer)
    return to_choice(a)


def check(
    content: Any,
    conditions: Sequence[str] | Mapping[str, Any],
    *,
    judge: Judge | None = None,
) -> dict[str, float]:
    """Probability that each condition holds. Several may be true at once."""
    conds = dict(conditions) if isinstance(conditions, Mapping) else {c: c for c in conditions}
    if not conds:
        raise ValueError("check() needs at least one condition")
    answers = _ask(content, {n: wording.check(c) for n, c in conds.items()}, judge)
    out = {}
    for name in conds:
        a = answers[name]
        assert isinstance(a, NoulAnswer)
        out[name] = a.p
    return out


def rate(
    content: Any,
    dimension: str,
    levels: Sequence[Any],
    *,
    judge: Judge | None = None,
) -> Score:
    """Place content on ordered `levels` (low → high)."""
    if len(levels) < 2:
        raise ValueError("rate() needs at least two levels")
    a = _ask(content, {"score": wording.rate(dimension, levels)}, judge)["score"]
    assert isinstance(a, ScoreAnswer)
    return to_score(a, tuple(levels))
