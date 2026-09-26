---
title: API
tags: [jevfilter, api]
status: current (0.4.0) — sections marked *planned* are not built yet
---

# API by example

The public API, from simplest to most advanced. See [design](design.md) for
the internals and [topic-format](topic-format.md) for definitions.

## Install

```sh
pip install jevfilter            # core
pip install "jevfilter[yaml]"      # YAML topic files
export TYPESAFE_API_KEY=...
```

## API key and model

jevfilter uses `TYPESAFE_API_KEY` from the environment by default. It
never reads `.env` files; load one yourself (e.g. `python-dotenv`) if you
keep the key there. To set it in code:

```python
import jevfilter as jf
from jevfilter.judges import JevJudge

jf.configure(api_key=key, model="jev-1.13.0")   # default for helpers and filters
f = Filter(topics, judge=JevJudge(api_key=key))  # or per filter
jf.choose("...", ["a", "b"], judge=my_judge)     # or per call
```

## Level 1 — helpers

```python
import jevfilter as jf

jf.choose("I was charged twice", ["billing", "bug", "feature request"])
# Choice(value='billing', confidence=0.97, probabilities={...})

jf.check("Can you send the signed form by Friday?", {
    "needs_reply": "The sender is asking me to reply or send something",
    "has_deadline": "A specific deadline is mentioned",
})
# {'needs_reply': 0.98, 'has_deadline': 0.95}

jf.rate("The site is down for all customers", "severity",
        ["cosmetic", "degraded", "blocking"])
# Score(value=1.96, level='blocking', confidence=0.93, ...)
```

Descriptions can be dicts (`{"billing": "Charges, refunds…"}`) for
clarity.

## Level 2 — topics

```python
from jevfilter import Filter, Topic

topics = Topic.load("topics/")     # dir, file, or list of dicts
f = Filter(topics)

r = f.judge({"from": "...", "subject": "...", "body": "..."})

for t in r.matches:                # outcome == "match"
    print(t.topic, t.p, t.category, t.flags, t.scores)
for t in r.review:                 # needs a person
    print(t.topic, t.reasons)

r.cost_usd, r.input_tokens, r.model
```

Topics in code:

```python
Topic(
    name="Receipts",
    description="Receipts and order confirmations for things I bought",
    fields={"merchant": {"kind": "org"}},
)
```

### Fields and candidates

```python
from jevfilter import Content

r = f.judge(Content(email, candidates={"Jobs": {"company": ["Acme", "Initech"]}}))
r["Jobs"].fields["company"]        # Field(value='Acme', confidence=0.99)
```

A field with no candidates isn't asked: it counts as "none of these"
and the result carries a warning. *Planned:* built-in extractors that
find candidates by field `kind`.

### Items and tracking

```python
from jevfilter import track

r = f.judge(Content(email, candidates=...))
m = f.match_item(email, "Jobs", items=[
    {"id": 3, "fields": {"company": "Acme", "role": "Backend Engineer"}, "status": "applied"},
    {"id": 7, "fields": {"company": "Acme", "role": "Data Engineer"}, "status": "contacted"},
], result=r["Jobs"])  # or fields={"company": "Acme"}
m.item_id          # 7, or None for a new item (m.is_new)
m.outcome          # "match" | "review"
m.asked            # False when code alone decided (no item shared `match_on`)

jobs = topics["Jobs"]
track.initial_status(jobs, "applied")                          # "applied"
track.next_status(jobs, current="applied", category="interview")   # "interviewing"
track.next_status(jobs, "rejected", "offer", last_stage="interviewing")  # reopens: "offer"
track.is_stale(jobs, last_activity=dt, now=now, status="applied")     # bool
```

Items are your records (dicts or objects with `id`, `fields`, optional
`status` and `summary`); jevfilter never stores them. Items whose
`match_on` fields differ from the content's are dropped in code first
(case, accents, punctuation and suffixes like "Inc." are ignored). If none
remain the answer is "new" with no Jev call; otherwise Jev picks one or
"new", even when only one remains, since the same company can mean a
different role.

### Many items

```python
f = AsyncFilter(topics, budget=Budget(usd=0.50, per_minute=120))
results = await f.judge_many(emails, concurrency=8, return_exceptions=True)
# in input order; a BudgetExceeded comes back in place instead of raising
m = await f.match_item(email, "Jobs", items, result=r["Jobs"])
```

`AsyncFilter` uses `AsyncJevJudge` by default; a sync judge (e.g.
`FakeJudge`) also works, run in a thread.

### Inspect before sending

```python
plan = f.explain(email)
plan.requests      # exact payloads (several if it had to split)
plan.cost_usd      # estimate
plan.warnings      # e.g. content truncated to fit
```

### Long content and many topics

Questions are packed into as few requests as fit Jev's limits (64k tokens
per request, 32k for content plus the longest question). Content that
can't fit is truncated, with a warning on the result:

```python
Filter(topics, truncate="head")        # default; or "head_tail", "error", or a callable
Filter(topics, limits=Limits(request_tokens=64_000, state_and_question_tokens=32_000))
```

## Level 3 — engine

```python
from jevfilter import Filter, Budget, ThresholdPolicy
from jevfilter.judges import FallbackJudge, JevJudge

budget = Budget(usd=1.00, per_minute=60)       # share across filters; refuses with
                                               # BudgetExceeded rather than blocking

f = Filter(
    topics,
    judge=FallbackJudge(JevJudge(model="jev-1.13.0"), JevJudge()),  # pinned → latest
    policy=ThresholdPolicy(accept=0.8, reject=0.2, min_confidence=0.6),
    budget=budget,
    on_error="review",                          # or "raise" / another Judge
    speculative=False,                          # see below
)
```

### Speculative or staged

By default every topic's questions go in one request: one round trip, but
you pay for category / field / score / flag questions of topics the
content doesn't belong to. `speculative=False` asks membership first,
then the rest only for topics that might belong (at or above `reject`):
two round trips, fewer tokens when most content matches no topic (31%
fewer billed tokens on a 10-email mix where 3 matched, with identical
accuracy). Outcomes are the same. `explain()` then shows the first request plus the worst-case
`followup_requests` and `max_cost_usd`. CLI: `--staged`.

### Saving results (your code)

```python
r = f.judge(email)
db.insert(r.to_dict())      # plain, JSON-able; restore with Result.from_dict
```

### Editing topics (e.g. from a UI)

```python
t = Topic.from_dict(form_data)      # validates, raises with clear messages
t.to_yaml("topics/jobs.yaml")       # round-trips unchanged
t.version                           # changes → offer a rescan
```

### Custom facet

```python
import jevfilter as jf
from jevfilter.facets import NoulFacet

@jf.facet("pii")
class ContainsPII(NoulFacet):
    instructions = "Does `content` contain personal data such as addresses or ID numbers?"
```

```yaml
# usable from topic files
name: Support
description: Customer support requests.
pii: {}
```

### Custom extractor (*planned*)

```python
@jf.extractor("order_number")
def order_numbers(content, field):
    return re.findall(r"#\d{5,}", content.text)
```

### Testing without the API

```python
from jevfilter.judges import FakeJudge

fake = FakeJudge({"Jobs/membership": 0.95, "Jobs/categories": "applied"})
r = Filter(topics, judge=fake).judge("...")
```

Record once, replay forever:

```python
from jevfilter.judges import JevJudge, RecordingJudge, ReplayJudge

judge = RecordingJudge(JevJudge(), "tests/cassettes/jobs.jsonl")  # calls Jev only for new requests
judge = ReplayJudge("tests/cassettes/jobs.jsonl")                  # never calls Jev
```

A cassette is JSONL keyed by a hash of each request's exact state and
questions. It contains the content that was judged, so don't commit
cassettes recorded from private data.

## Evaluate

Label some examples (JSONL), then score your topics and sweep thresholds:

```json
{"content": {...}, "candidates": {"Jobs": {"company": ["Acme"]}},
 "expected": {"Jobs": {"match": true, "category": "applied", "fields": {"company": "Acme"}}}}
```

```python
from jevfilter.eval import evaluate, load_examples

report = evaluate(f, load_examples("labelled.jsonl"), sweep=True)
print(report.format())      # precision, recall, review rate, category accuracy,
report.to_dict()            # calibration, cost, and the best threshold trade-offs
```

A topic missing from `expected` should not match. Use a `RecordingJudge`
so re-running (or passing `results=` from a previous run) costs nothing.

## CLI

```sh
jevfilter try topics/ "Your application was sent to Acme" --candidates '{"Jobs": {"company": ["Acme"]}}'
jevfilter try topics/ --file email.json --json          # .json files are parsed
jevfilter explain topics/ --file email.json --payloads  # requests + cost; sends nothing
jevfilter lint topics/                                  # exit 2 on invalid topics
jevfilter eval topics/ labelled.jsonl --sweep --record runs/cassette.jsonl
jevfilter eval topics/ labelled.jsonl --replay runs/cassette.jsonl   # free re-run
```

Options for `try` / `eval`: `--model`, `--max-usd` (spend cap),
`--record` / `--replay`, `--env-file .env` (the only way the CLI reads a
`.env`), `--json`.
