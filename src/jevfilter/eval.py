"""Evaluate topics against labelled examples, and sweep thresholds.

Dataset: JSONL, one example per line.

```json
{"content": {...}, "candidates": {"Jobs": {"company": ["Acme"]}},
 "expected": {"Jobs": {"match": true, "category": "applied", "fields": {"company": "Acme"}}}}
```

A topic missing from `expected` is expected not to match. `category` and
`fields` are optional; only what's labelled is scored.

Outcomes: `match` and `no` are automatic decisions; `review` is deferred
to a person. Precision is over automatic matches; recall counts a
positive sent to review as not (yet) found; the review rate is reported
separately, so the trade-off between them is visible.

The threshold sweep re-decides membership from the stored probabilities,
so it never calls Jev again. It covers membership thresholds only (not
the category / field confidence rules).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .content import Content
from .engine import Filter
from .result import Result


@dataclass(frozen=True)
class Example:
    content: Any
    expected: dict[str, dict[str, Any]]
    candidates: dict[str, Any] | None = None
    context: Any = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Example:
        if "content" not in d:
            raise ValueError("an example needs `content`")
        expected = d.get("expected", {})
        if not isinstance(expected, Mapping):
            raise ValueError("`expected` must map topic → {match, category, fields}")
        for topic, exp in expected.items():
            if not isinstance(exp, Mapping) or not isinstance(exp.get("match"), bool):
                raise ValueError(f"`expected.{topic}` needs `match: true|false`")
        return cls(
            d["content"],
            {k: dict(v) for k, v in expected.items()},
            d.get("candidates"),
            d.get("context"),
        )

    def as_content(self) -> Content:
        return Content(self.content, candidates=self.candidates, context=self.context)

    def expects_match(self, topic: str) -> bool:
        return bool(self.expected.get(topic, {}).get("match", False))


def load_examples(path: str | Path) -> list[Example]:
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(Example.from_dict(json.loads(line)))
        except ValueError as e:
            raise ValueError(f"{Path(path).name}:{n}: {e}") from None
    return out


@dataclass
class Counts:
    """Membership counts for one topic. `review_*` split reviews by truth."""

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    review_pos: int = 0
    review_neg: int = 0

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn + self.review_pos + self.review_neg

    @property
    def precision(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fn + self.review_pos)

    @property
    def review_rate(self) -> float | None:
        return _ratio(self.review_pos + self.review_neg, self.n)

    @property
    def decided_accuracy(self) -> float | None:
        return _ratio(self.tp + self.tn, self.tp + self.fp + self.tn + self.fn)

    def add(self, outcome: str, truth: bool) -> None:
        if outcome == "review":
            if truth:
                self.review_pos += 1
            else:
                self.review_neg += 1
        elif outcome == "match":
            if truth:
                self.tp += 1
            else:
                self.fp += 1
        elif truth:
            self.fn += 1
        else:
            self.tn += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            **vars(self),
            "precision": self.precision,
            "recall": self.recall,
            "review_rate": self.review_rate,
            "decided_accuracy": self.decided_accuracy,
        }


@dataclass
class TopicReport:
    topic: str
    membership: Counts = field(default_factory=Counts)
    category_confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    field_hits: dict[str, list[int]] = field(default_factory=dict)  # name → [correct, total]

    @property
    def category_accuracy(self) -> float | None:
        total = sum(sum(row.values()) for row in self.category_confusion.values())
        right = sum(row.get(exp, 0) for exp, row in self.category_confusion.items())
        return _ratio(right, total)

    def field_accuracy(self, name: str) -> float | None:
        right, total = self.field_hits.get(name, [0, 0])
        return _ratio(right, total)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "membership": self.membership.to_dict(),
            "category_accuracy": self.category_accuracy,
            "category_confusion": self.category_confusion,
            "field_accuracy": {n: self.field_accuracy(n) for n in self.field_hits},
        }


@dataclass(frozen=True)
class Bucket:
    low: float
    high: float
    count: int
    mean_p: float
    observed: float


@dataclass(frozen=True)
class SweepRow:
    accept: float
    reject: float
    precision: float | None
    recall: float | None
    review_rate: float | None
    decided_accuracy: float | None


@dataclass
class Report:
    examples: int
    topics: dict[str, TopicReport]
    calibration: list[Bucket]
    brier: float | None
    ece: float | None
    cost_usd: float
    input_tokens: int
    requests: int
    sweep: list[SweepRow] = field(default_factory=list)

    @property
    def overall(self) -> Counts:
        total = Counts()
        for t in self.topics.values():
            for k in vars(total):
                setattr(total, k, getattr(total, k) + getattr(t.membership, k))
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "examples": self.examples,
            "overall": self.overall.to_dict(),
            "topics": {k: v.to_dict() for k, v in self.topics.items()},
            "calibration": [vars(b) for b in self.calibration],
            "brier": self.brier,
            "ece": self.ece,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "requests": self.requests,
            "sweep": [vars(r) for r in self.sweep],
        }

    def format(self) -> str:
        """A plain-text report for terminals."""
        lines = [f"{self.examples} examples, {self.requests} requests, ${self.cost_usd:.6f}", ""]
        lines.append(f"{'topic':<20} {'prec':>6} {'recall':>6} {'review':>6} {'acc':>6}  category")
        for t in [*self.topics.values(), None]:
            name, c = ("overall", self.overall) if t is None else (t.topic, t.membership)
            cat = "" if t is None else _pct(t.category_accuracy)
            lines.append(
                f"{name:<20} {_pct(c.precision):>6} {_pct(c.recall):>6} "
                f"{_pct(c.review_rate):>6} {_pct(c.decided_accuracy):>6}  {cat}"
            )
        for t in self.topics.values():
            for name in t.field_hits:
                lines.append(f"  {t.topic}.{name}: {_pct(t.field_accuracy(name))} correct")
        lines += ["", f"calibration: Brier {_num(self.brier)}, ECE {_num(self.ece)}"]
        for b in self.calibration:
            if b.count:
                lines.append(
                    f"  p {b.low:.1f}–{b.high:.1f}: n={b.count:<4} "
                    f"mean p {b.mean_p:.2f}, observed {b.observed:.2f}"
                )
        if self.sweep:
            lines += [
                "",
                "threshold sweep (membership only; best trade-offs):",
                f"  {'accept':>6} {'reject':>6} {'prec':>6} {'recall':>6} {'review':>6}",
            ]
            for r in frontier(self.sweep):
                lines.append(
                    f"  {r.accept:>6.2f} {r.reject:>6.2f} {_pct(r.precision):>6} "
                    f"{_pct(r.recall):>6} {_pct(r.review_rate):>6}"
                )
        return "\n".join(lines)


def evaluate(
    filter: Filter,
    examples: Iterable[Example | Mapping[str, Any]],
    *,
    sweep: bool = False,
    results: list[Result] | None = None,
) -> Report:
    """Judge every example with `filter` and score the outcomes.

    Pass `results` (from an earlier run, in the same order) to re-score
    without judging again.
    """
    exs = [e if isinstance(e, Example) else Example.from_dict(e) for e in examples]
    if results is None:
        results = [filter.judge(e.as_content()) for e in exs]
    elif len(results) != len(exs):
        raise ValueError("results must line up with examples")

    topics = {name: TopicReport(name) for name in filter.topics}
    pairs: list[tuple[float, bool]] = []
    for ex, res in zip(exs, results, strict=True):
        for name, report in topics.items():
            tr = res.topics.get(name)
            if tr is None:
                continue
            truth = ex.expects_match(name)
            report.membership.add(tr.outcome, truth)
            if "backend_error" not in tr.reasons:
                pairs.append((tr.p, truth))
            exp = ex.expected.get(name, {})
            if truth and "category" in exp and tr.category is not None:
                row = report.category_confusion.setdefault(exp["category"], {})
                row[tr.category.value] = row.get(tr.category.value, 0) + 1
            if truth:
                for fname, want in (exp.get("fields") or {}).items():
                    got = tr.fields.get(fname)
                    hits = report.field_hits.setdefault(fname, [0, 0])
                    hits[0] += int(got is not None and got.value == want)
                    hits[1] += 1

    buckets, brier, ece = _calibration(pairs)
    report = Report(
        examples=len(exs),
        topics=topics,
        calibration=buckets,
        brier=brier,
        ece=ece,
        cost_usd=sum(r.cost_usd or 0.0 for r in results),
        input_tokens=sum(r.input_tokens or 0 for r in results),
        requests=sum(len(r.request_ids) for r in results),
    )
    if sweep:
        report.sweep = _sweep(exs, results, list(filter.topics))
    return report


def _calibration(pairs: list[tuple[float, bool]], bins: int = 10):
    if not pairs:
        return [], None, None
    buckets = []
    ece = 0.0
    for i in range(bins):
        low, high = i / bins, (i + 1) / bins
        inside = [(p, t) for p, t in pairs if low <= p < high or (i == bins - 1 and p == 1.0)]
        if inside:
            mean_p = sum(p for p, _ in inside) / len(inside)
            observed = sum(t for _, t in inside) / len(inside)
            ece += len(inside) / len(pairs) * abs(mean_p - observed)
        else:
            mean_p = observed = 0.0
        buckets.append(Bucket(low, high, len(inside), mean_p, observed))
    brier = sum((p - t) ** 2 for p, t in pairs) / len(pairs)
    return buckets, brier, ece


def _sweep(exs: list[Example], results: list[Result], names: list[str]) -> list[SweepRow]:
    rows = []
    grid = [round(x * 0.05, 2) for x in range(1, 20)]
    for accept in grid:
        for reject in grid:
            if reject > accept:
                continue
            c = Counts()
            for ex, res in zip(exs, results, strict=True):
                for name in names:
                    tr = res.topics.get(name)
                    if tr is None or "backend_error" in tr.reasons:
                        continue
                    outcome = "match" if tr.p >= accept else "no" if tr.p < reject else "review"
                    c.add(outcome, ex.expects_match(name))
            rows.append(
                SweepRow(accept, reject, c.precision, c.recall, c.review_rate, c.decided_accuracy)
            )
    return rows


def frontier(rows: list[SweepRow]) -> list[SweepRow]:
    """Sweep rows no other row beats on precision, recall and review rate at once."""

    def key(r: SweepRow) -> tuple[float, float, float]:
        return (r.precision or 0.0, r.recall or 0.0, -(r.review_rate or 0.0))

    keys = [key(r) for r in rows]
    best = []
    for i, k in enumerate(keys):
        dominated = any(
            all(o >= s for o, s in zip(other, k, strict=True)) and other != k for other in keys
        )
        if not dominated and k not in [key(b) for b in best]:
            best.append(rows[i])
    return sorted(best, key=lambda r: (r.review_rate or 0.0, -(r.precision or 0.0)))


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _num(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"
