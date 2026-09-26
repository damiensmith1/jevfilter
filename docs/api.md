---
title: API
tags: [jevfilter, api]
status: draft — proposed, not implemented
---

# API by example

Proposed public API, from simplest to most advanced. See [design](design.md) for
the internals and [topic-format](topic-format.md) for definitions.

## Install

```sh
pip install jevfilter            # core
pip install "jevfilter[yaml,cli,eval]"
export TYPESAFE_API_KEY=...
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

Omit `candidates` to use the extractor registered for each field's
`kind`.

### Items and tracking

```python
from jevfilter import track

jobs = topics["Jobs"]
m = f.match_item(email, jobs, items=[
    {"id": 3, "fields": {"company": "Acme", "role": "Backend Engineer"}, "status": "applied"},
    {"id": 7, "fields": {"company": "Acme", "role": "Data Engineer"}, "status": "contacted"},
])
m.item_id          # 3, or None for a new item
m.outcome          # "match" | "review"

track.next_status(jobs, current="applied", category="interview")   # "interviewing"
track.is_stale(jobs, last_activity=dt, now=now)                   # bool
```

### Many items

```python
results = await AsyncFilter(topics).judge_many(emails, concurrency=8)
```

### Inspect before sending

```python
plan = f.explain(email)
plan.requests      # exact payloads
plan.cost_usd      # estimate
```

## Level 3 — engine

```python
from jevfilter import Filter, Budget, ThresholdPolicy
from jevfilter.judges import JevJudge, KeywordJudge

budget = Budget(usd=1.00, per_minute=60)       # share across filters

f = Filter(
    topics,
    judge=JevJudge(model="jev-1.13.0"),         # pin the version
    policy=ThresholdPolicy(accept=0.8, reject=0.2, min_confidence=0.6),
    budget=budget,
    on_error=KeywordJudge(),                    # or "raise" / "review"
)
```

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

### Custom extractor

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
judge = RecordingJudge(JevJudge(), path="tests/cassettes/jobs.jsonl")
judge = ReplayJudge("tests/cassettes/jobs.jsonl")
```

## CLI

```sh
jevfilter try topics/ "Your application was sent to Acme"
jevfilter explain topics/ email.txt
jevfilter lint topics/
jevfilter eval topics/ labelled.jsonl --sweep
```
