# jevfilter

[![CI](https://github.com/damiensmith1/jevfilter/actions/workflows/ci.yml/badge.svg)](https://github.com/damiensmith1/jevfilter/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/jevfilter)](https://pypi.org/project/jevfilter/)

Judge content against plain-English definitions with TypeSafe's
[Jev](https://docs.typesafe.ai). Describe what you care about in a few
words; get back typed answers with calibrated probabilities: which topics
match, which category, which company, how urgent.

jevfilter is stateless: no storage, no cache and no network except Jev.
You get plain results back and store them however you like.

> Pre-alpha. The API may change before 1.0.

## Install

```sh
pip install "jevfilter[yaml]"
export TYPESAFE_API_KEY=...
```

On PyPI: <https://pypi.org/project/jevfilter/>

Python 3.10+. You can also set the key in code with
`jf.configure(api_key=..., model="jev-1.13.0")`. jevfilter never reads
`.env` files, so load one yourself if you use it.

## Quick start

```python
import jevfilter as jf

jf.choose("I was charged twice", ["billing", "bug", "feature request"])
# Choice(value='billing', confidence=0.96, ...)

jf.check("Can you send the form by Friday?", {"needs_reply": "The sender wants a reply"})
# {'needs_reply': 0.98}

jf.rate("The site is down for everyone", "severity", ["cosmetic", "degraded", "blocking"])
# Score(value=2.0, level='blocking', ...)
```

## Topics

A topic only needs a name and a description. Categories, fields, scores
and flags are optional.

```yaml
# topics/jobs.yaml
name: Jobs
description: Applications I submitted, and recruiters contacting me about a role.
exclude: Job alerts, digests, newsletters.
categories:
  applied: Confirms I submitted an application.
  interview: Invites me to an interview.
  rejection: Tells me I'm not moving forward.
fields:
  company: {about: The hiring company, required: true}
flags:
  needs_reply: The sender is asking me to reply.
```

```python
f = jf.Filter(jf.Topic.load("topics/"))

email = {"from": "...", "subject": "...", "body": "..."}
r = f.judge(jf.Content(email, candidates={"Jobs": {"company": ["Acme", "Initech"]}}))

for t in r.matches:
    print(t.topic, t.p, t.category.value, t.fields["company"].value, t.flags)
for t in r.review:            # uncertain: let a person decide
    print(t.topic, t.reasons)

r.cost_usd                    # every result reports its cost
db.save(r.to_dict())          # plain JSON; restore with jf.Result.from_dict
```

Each topic comes back as `match`, `review` or `no`. All questions for one
piece of content go to Jev in a single request. Field values are picked
from the candidates you pass in, never generated. `f.explain(email)` shows
the exact request and its estimated cost without sending it.

## Tracked items and batches

```python
# Which of your tracked items is this email about? (you store the items)
m = f.match_item(email, "Jobs", my_jobs, result=r["Jobs"])
m.item_id                     # an id from my_jobs, or None for a new one

jf.track.next_status(topics["Jobs"], "applied", "interview")   # "interviewing"

# Many emails at once, with a spend cap that refuses instead of overspending
af = jf.AsyncFilter(topics, budget=jf.Budget(usd=0.50, per_minute=120))
results = await af.judge_many(emails, concurrency=8)
```

See [docs/topic-format.md](docs/topic-format.md) for every topic option
(scores, composites, `when`, thresholds, `meta`) and [docs/api.md](docs/api.md)
for the rest of the API.

## Testing without an API key

```python
from jevfilter.judges import FakeJudge

fake = FakeJudge({"Jobs/membership": 0.95, "Jobs/categories": "applied"})
r = jf.Filter(topics, judge=fake).judge("...")
```

## Development

```sh
uv sync
uv run pytest                                   # unit tests, no network
JEVFILTER_LIVE=1 uv run pytest tests/live -s    # real Jev, prints spend
uv run ruff check . && uv run ruff format .
```

## License

MIT
