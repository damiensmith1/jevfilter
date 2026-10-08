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
the category / field confidence rules). It runs over all topics pooled
and over each topic on its own, since topics are calibrated differently;
a topic's best row goes in its `thresholds: {accept, reject}`.

With `holdout`, the sweep tunes on part of the examples and re-scores
every row on the rest, so the chosen thresholds aren't judged on the data
they were picked from.
"""

from __future__ import annotations

import json
import random
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
    """Metrics at one accept / reject pair: on the tuning examples, and on
    the held-out ones when `holdout` was given. `topic` None means pooled."""

    accept: float
    reject: float
    precision: float | None
    recall: float | None
    review_rate: float | None
    decided_accuracy: float | None
    topic: str | None = None
    held_out: Counts | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in vars(self).items() if k != "held_out"}
        d["held_out"] = None if self.held_out is None else self.held_out.to_dict()
        return d


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
    topic_sweeps: dict[str, list[SweepRow]] = field(default_factory=dict)
    tune_examples: int | None = None
    held_out_examples: int | None = None

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
            "sweep": [r.to_dict() for r in self.sweep],
            "topic_sweeps": {k: [r.to_dict() for r in v] for k, v in self.topic_sweeps.items()},
            "tune_examples": self.tune_examples,
            "held_out_examples": self.held_out_examples,
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
            lines += ["", "threshold sweep (membership only; best trade-offs):"]
            if self.held_out_examples is not None:
                lines.append(
                    f"  tuned on {self.tune_examples} examples, "
                    f"checked on {self.held_out_examples} held out"
                )
            sweeps = {"all topics": self.sweep, **self.topic_sweeps}
            for name, rows in sweeps.items():
                lines += ["", f"  {name}:", "    " + _sweep_header(self.held_out_examples)]
                lines += ["    " + _sweep_line(r) for r in frontier(rows)]
        return "\n".join(lines)


def evaluate(
    filter: Filter,
    examples: Iterable[Example | Mapping[str, Any]],
    *,
    sweep: bool = False,
    holdout: float | None = None,
    seed: int = 0,
    results: list[Result] | None = None,
) -> Report:
    """Judge every example with `filter` and score the outcomes.

    Pass `results` (from an earlier run, in the same order) to re-score
    without judging again. `holdout` (a fraction, e.g. 0.3) keeps that
    share of examples out of the sweep's tuning and scores every row on
    it; the split is stratified by expected matches and fixed by `seed`.
    """
    if holdout is not None and not sweep:
        raise ValueError("holdout only applies to the sweep; pass sweep=True")
    if holdout is not None and not 0 < holdout < 1:
        raise ValueError("holdout must be between 0 and 1")
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
        names = list(filter.topics)
        tune, held = list(range(len(exs))), None
        if holdout is not None:
            tune, held = _split(exs, names, holdout, seed)
            report.tune_examples, report.held_out_examples = len(tune), len(held)
        report.sweep = _sweep(exs, results, names, tune, held)
        report.topic_sweeps = {n: _sweep(exs, results, [n], tune, held, topic=n) for n in names}
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


def _split(
    exs: list[Example], names: list[str], holdout: float, seed: int
) -> tuple[list[int], list[int]]:
    """Indices for tuning and held out, stratified by which topics should match."""
    groups: dict[tuple[str, ...], list[int]] = {}
    for i, ex in enumerate(exs):
        groups.setdefault(tuple(n for n in names if ex.expects_match(n)), []).append(i)
    # Spread each group evenly over [0, 1) in random order, then hold out the
    # first share overall: every group is represented in proportion.
    rng = random.Random(seed)
    spread = []
    for key in sorted(groups):
        idx = groups[key]
        rng.shuffle(idx)
        spread += [((j + rng.random()) / len(idx), i) for j, i in enumerate(idx)]
    spread.sort()
    k = round(len(exs) * holdout)
    if not 0 < k < len(exs):
        raise ValueError(f"holdout={holdout} leaves no examples on one side; label more")
    return sorted(i for _, i in spread[k:]), sorted(i for _, i in spread[:k])


def _sweep(
    exs: list[Example],
    results: list[Result],
    names: list[str],
    tune: list[int],
    held: list[int] | None = None,
    topic: str | None = None,
) -> list[SweepRow]:
    def pairs(indices: list[int]) -> list[tuple[float, bool]]:
        out = []
        for i in indices:
            for name in names:
                tr = results[i].topics.get(name)
                if tr is not None and "backend_error" not in tr.reasons:
                    out.append((tr.p, exs[i].expects_match(name)))
        return out

    def counts(ps: list[tuple[float, bool]], accept: float, reject: float) -> Counts:
        c = Counts()
        for p, truth in ps:
            c.add("match" if p >= accept else "no" if p < reject else "review", truth)
        return c

    tune_pairs = pairs(tune)
    held_pairs = None if held is None else pairs(held)
    rows = []
    grid = [round(x * 0.05, 2) for x in range(1, 20)]
    for accept in grid:
        for reject in grid:
            if reject > accept:
                continue
            c = counts(tune_pairs, accept, reject)
            rows.append(
                SweepRow(
                    accept,
                    reject,
                    c.precision,
                    c.recall,
                    c.review_rate,
                    c.decided_accuracy,
                    topic=topic,
                    held_out=None if held_pairs is None else counts(held_pairs, accept, reject),
                )
            )
    return rows


def _sweep_header(held_out: int | None) -> str:
    head = f"{'accept':>6} {'reject':>6} {'prec':>6} {'recall':>6} {'review':>6}"
    if held_out is not None:
        head += f"  | held out {'prec':>6} {'recall':>6} {'review':>6}"
    return head


def _sweep_line(r: SweepRow) -> str:
    line = (
        f"{r.accept:>6.2f} {r.reject:>6.2f} {_pct(r.precision):>6} "
        f"{_pct(r.recall):>6} {_pct(r.review_rate):>6}"
    )
    if r.held_out is not None:
        h = r.held_out
        line += f"  |          {_pct(h.precision):>6} {_pct(h.recall):>6} {_pct(h.review_rate):>6}"
    return line


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
