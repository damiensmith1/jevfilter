"""`jevfilter` command line: try, explain, lint, eval.

```sh
jevfilter try topics/ "Your application to Acme was received"
jevfilter explain topics/ --file email.json
jevfilter lint topics/
jevfilter eval topics/ labelled.jsonl --sweep --record runs/cassette.jsonl
jevfilter eval topics/ labelled.jsonl --holdout 0.3 --replay runs/cassette.jsonl
```

The API key comes from `TYPESAFE_API_KEY`. Nothing reads `.env` unless you
pass `--env-file`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .budget import Budget
from .content import Content
from .engine import Filter
from .errors import BudgetExceeded, JevFilterError, JudgeError, TopicError
from .result import Result
from .topic import Topic
from .version import __version__


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if getattr(args, "env_file", None):
            _load_env_file(Path(args.env_file))
        return args.run(args)
    except TopicError as e:
        print("invalid topics:\n  " + "\n  ".join(e.problems), file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (JudgeError, BudgetExceeded, JevFilterError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jevfilter", description="Judge content against plain-English topics with Jev."
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, content: bool = True) -> None:
        sp.add_argument("topics", help="topic directory or file")
        if content:
            sp.add_argument("text", nargs="?", help="text to judge; '-' reads stdin")
            sp.add_argument("--file", help="read content from a file (.json is parsed)")
            sp.add_argument("--candidates", help='JSON: {"Topic": {"field": ["value", ...]}}')
        sp.add_argument("--json", action="store_true", help="print JSON")

    def staged(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--staged",
            action="store_true",
            help="ask membership first, other questions only for topics that may match",
        )

    def backend(sp: argparse.ArgumentParser) -> None:
        staged(sp)
        sp.add_argument("--model", help="pin a model version, e.g. jev-1.13.0")
        sp.add_argument("--max-usd", type=float, help="refuse to spend more than this")
        sp.add_argument("--env-file", help="load TYPESAFE_API_KEY from this file")
        rec = sp.add_mutually_exclusive_group()
        rec.add_argument("--record", help="record answers to this cassette (JSONL)")
        rec.add_argument("--replay", help="answer only from this cassette; no API calls")

    t = sub.add_parser("try", help="judge one piece of content and show the result")
    common(t)
    backend(t)
    t.set_defaults(run=_try)

    x = sub.add_parser("explain", help="show the requests and estimated cost; sends nothing")
    common(x)
    x.add_argument("--payloads", action="store_true", help="print the full request payloads")
    staged(x)
    x.set_defaults(run=_explain)

    lint = sub.add_parser("lint", help="validate topic files and show warnings")
    common(lint, content=False)
    lint.set_defaults(run=_lint)

    e = sub.add_parser("eval", help="score topics against labelled JSONL examples")
    common(e, content=False)
    e.add_argument("data", help="labelled examples (JSONL)")
    e.add_argument("--sweep", action="store_true", help="also sweep membership thresholds")
    e.add_argument(
        "--holdout",
        type=float,
        metavar="FRACTION",
        help="tune the sweep on the rest and score it on this share (implies --sweep)",
    )
    e.add_argument("--seed", type=int, default=0, help="seed for the --holdout split")
    backend(e)
    e.set_defaults(run=_eval)
    return p


# -- commands ---------------------------------------------------------------


def _try(args: argparse.Namespace) -> int:
    f = _filter(args)
    r = f.judge(_content(args))
    if args.json:
        _print_json(r.to_dict())
    else:
        print(_format_result(r))
    return 0


def _explain(args: argparse.Namespace) -> int:
    f = Filter(Topic.load(args.topics), judge=_Refuse(), speculative=not args.staged)
    plan = f.explain(_content(args))
    if args.json:
        _print_json(
            {
                "requests": plan.requests,
                "estimated_input_tokens": plan.estimated_input_tokens,
                "cost_usd": plan.cost_usd,
                "followup_requests": plan.followup_requests,
                "max_cost_usd": plan.max_cost_usd,
                "warnings": list(plan.warnings),
            }
        )
        return 0
    print(
        f"{len(plan.requests)} request(s), ~{plan.estimated_input_tokens} input tokens, "
        f"~${plan.cost_usd:.6f}"
    )
    for i, req in enumerate(plan.requests, 1):
        print(f"  request {i}: {', '.join(req['questions'])}")
    if plan.followup_requests:
        print(
            f"then, only for topics that may match: up to {len(plan.followup_requests)} "
            f"more request(s), ~${plan.max_cost_usd:.6f} total at most"
        )
    for w in plan.warnings:
        print(f"warning: {w}")
    if args.payloads:
        _print_json(plan.requests)
    return 0


def _lint(args: argparse.Namespace) -> int:
    topics = Topic.load(args.topics)
    warnings = [w for t in topics.values() for w in t.warnings]
    if args.json:
        _print_json({"topics": list(topics), "warnings": warnings})
    else:
        print(f"{len(topics)} topic(s) OK: {', '.join(topics)}")
        for w in warnings:
            print(f"warning: {w}")
    return 0


def _eval(args: argparse.Namespace) -> int:
    from .eval import evaluate, load_examples

    report = evaluate(
        _filter(args),
        load_examples(args.data),
        sweep=args.sweep or args.holdout is not None,
        holdout=args.holdout,
        seed=args.seed,
    )
    if args.json:
        _print_json(report.to_dict())
    else:
        print(report.format())
    return 0


# -- helpers ----------------------------------------------------------------


def _filter(args: argparse.Namespace) -> Filter:
    from .judges.jev import JevJudge
    from .judges.recording import RecordingJudge, ReplayJudge

    judge: Any
    if args.replay:
        judge = ReplayJudge(args.replay)
    elif args.record:
        judge = RecordingJudge(JevJudge(model=args.model), args.record)
    else:
        judge = JevJudge(model=args.model)
    budget = Budget(usd=args.max_usd) if args.max_usd is not None else None
    return Filter(Topic.load(args.topics), judge=judge, budget=budget, speculative=not args.staged)


def _content(args: argparse.Namespace) -> Content:
    if args.file and args.text:
        raise ValueError("pass either text or --file, not both")
    if args.file:
        path = Path(args.file)
        raw = path.read_text(encoding="utf-8")
        state: Any = json.loads(raw) if path.suffix == ".json" else raw
    elif args.text == "-":
        state = sys.stdin.read()
    elif args.text:
        state = args.text
    else:
        raise ValueError("give text to judge, '-' for stdin, or --file")
    candidates = json.loads(args.candidates) if args.candidates else None
    return Content(state, candidates=candidates)


def _format_result(r: Result) -> str:
    lines = []
    for t in r:
        line = f"{t.topic}: {t.outcome} (p={t.p:.2f})"
        if t.reasons:
            line += f" {', '.join(t.reasons)}"
        lines.append(line)
        if t.category is not None:
            lines.append(f"  category: {t.category.value} ({t.category.confidence:.2f})")
        for name, fv in t.fields.items():
            lines.append(f"  {name}: {fv.value if fv.value is not None else '—'}")
        for name, s in t.scores.items():
            lines.append(f"  {name}: {s.level} ({s.value:.2f})")
        for name, p in t.flags.items():
            lines.append(f"  {name}: {p:.2f}")
    cost = f"${r.cost_usd:.6f}" if r.cost_usd is not None else "-"
    lines.append(f"model {r.model}, {r.input_tokens} tokens, {cost}")
    lines += [f"warning: {w}" for w in r.warnings]
    return "\n".join(lines)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _load_env_file(path: Path) -> None:
    """Set variables from a KEY=VALUE file, without overriding the environment."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip("\"'"))


class _Refuse:
    """Backend for `explain`: it must never be called."""

    def ask(self, state: Any, questions: Any) -> Any:  # pragma: no cover
        raise AssertionError("explain must not call the backend")


if __name__ == "__main__":
    raise SystemExit(main())
