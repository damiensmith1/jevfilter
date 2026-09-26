---
title: Design
tags: [jevfilter, design]
status: draft
---

# Design

See [background](background.md) (why), [requirements](requirements.md) (what), [topic-format](topic-format.md)
(definition schema), [api](api.md) (usage by example).

## Layers

Three layers, each built on the one below, so simple use stays simple and
nothing powerful needs a different tool.

```
 ┌───────────────────────────────────────────────────────────────┐
 │ 1. Helpers      choose() · check() · rate()                   │  one call, no setup
 ├───────────────────────────────────────────────────────────────┤
 │ 2. Topics       Topic · Filter.judge() · match_item() · track │  declarative
 ├───────────────────────────────────────────────────────────────┤
 │ 3. Engine       Facet · Plan · Judge · Policy · Budget        │  extension points
 └───────────────────────────────────────────────────────────────┘
                              │
                        typesafe-sdk  →  Jev
```

Helpers build throwaway topics and call the same engine, so behaviour
(budget, failure policy, provenance) is identical at every layer.

## Core concepts

- **Content** — what's being judged: a string or JSON-able dict (Jev's
  *state*). Optionally wrapped in `Content(state, candidates=…,
  context=…)` to carry field candidates and extra context.
- **Topic** — a named, plain-English definition: `description`
  (required), `exclude`, and optional **facets**. Immutable, validated,
  versioned. See [topic-format](topic-format.md).
- **Facet** — one kind of judgment a topic asks for. Built-in facets:

  | Facet | Primitive | Answers |
  |-------|-----------|---------|
  | `membership` (implicit) | Noul | does content belong to the topic? |
  | `categories` | Choice (or tree of Choices) | which category? |
  | `fields` | Choice over candidates + "none of these" | which value? |
  | `scores` | Score | how much, on described levels? |
  | `flags` | Noul | does this extra condition hold? |

  Facets other than membership are **speculative** by default: asked in
  the same request, used only if their `when` condition holds (default:
  topic matched).
- **Filter** — holds topics plus engine configuration (judge, policy,
  budget). `judge(content)` → `Result`.
- **Result** — per-topic `TopicResult`s (outcome, probability, category,
  fields, scores, flags, reasons) plus provenance (model, request IDs,
  tokens, cost, raw answers).
- **Item** — a caller-owned record (dict or object with `id`, `fields`,
  `status`) used for item matching and tracking. The library never
  stores items.

## The judge pipeline

```
content + topics
   │
   ├─1 compile   each topic's facets → Question specs (stable IDs)
   ├─2 plan      pack specs into requests within limits
   ├─3 guard     budget check
   ├─4 execute   Judge backend (Jev by default), concurrently
   ├─5 interpret facets read their answers → typed values
   ├─6 decide    Policy → outcome per topic + reasons; apply `when`
   └─7 report    Result (plain data; caller persists it)
```

Each stage is a plain function or protocol, testable alone. `explain()`
runs stages 1–2 only and returns the planned payloads and cost estimate.

### Question IDs

`<topic>/membership`, `<topic>/categories`, and `<topic>/<facet>/<name>`
for named facets (e.g. `Jobs/fields/company`, `Jobs/flags/needs_reply`;
custom facets follow the same pattern). Jev doesn't see
IDs, so every question's `instructions` restate the topic and field in
full. IDs are only for routing answers back.

### Question wording

Built from the definition, using structured instructions (Jev accepts
JSON in `instructions` and `criteria`):

```json
{
  "topic": {"name": "Jobs", "description": "…", "exclude": "…"},
  "question": "Does `content` belong to `topic`?"
}
```

Speculative facets state their premise: *"Assuming `content` belongs to
`topic`, which category…"*. Templates live in one module so wording can
be tuned and evaluated centrally; a facet may override its template.

### Packing and splitting

Jev limits: 64k tokens per request (state + all questions); 32k for state
+ the longest question. The planner estimates tokens (proposed:
characters ÷ 4, calibrated against reported `input_tokens`) and
first-fit packs questions into as few requests as possible, resending the
state per request. State over the 32k budget is truncated per a
configurable strategy (`head`, `head_tail`, or a callable) with a
warning in the result.

### Hierarchical categories

A category may have children. Resolution follows the TypeSafe
hierarchical-classification cookbook:

- **greedy** (default): one Choice per level, top child each step
- **beam** (`beam=K`): keep K paths, score by geometric-mean edge
  probability, ask each frontier in parallel

The first level is asked speculatively in the main request; deeper levels
need follow-up requests (their options depend on earlier answers).

### Composite scores

A topic can define `composites`: weighted sums of normalised scores
(score ÷ max level). Weights are code-side, so changing them never
re-calls Jev.

## Decision policy

`Policy` turns raw answers into outcomes. The default `ThresholdPolicy`:

- membership `p ≥ accept` → `match`; `p < reject` → `no`; else `review`
  (defaults 0.7 / 0.3)
- a used Choice facet with `confidence < min_confidence` (0.5), or a
  required field answered "none of these" → topic becomes `review`, with
  the reason
- per-topic overrides from the definition

Every non-`match` outcome carries machine-readable reasons
(`membership_uncertain`, `field_missing:company`,
`low_confidence:category`, `backend_error`…). Custom policies implement
one method: `decide(topic, answers) -> Decision`.

Thresholds are starting points; `jevfilter eval` sweeps them against
labelled data.

## Item matching

`match_item(content, topic, items)`:

1. **Pre-filter in code** — keep items whose `match_on` fields equal the
   content's selected field values (normalised). Zero or one survivor →
   answer without calling Jev.
2. **Choose** — one Choice over surviving items (described by fields,
   status and caller-supplied summary) plus "new".
3. **Decide** — low confidence → `review`.

Consumers can pass a thread / conversation key to skip matching entirely
when content is already linked.

## Tracking rules

Pure functions over a topic's `track` config (no storage, no Jev):

- `track.initial_status(topic, category)`
- `track.next_status(topic, current, category)` — forward-only through
  `statuses`; `terminal` always applies; terminal reopens only on a later
  stage
- `track.is_stale(topic, last_activity, now)`

## Extension points

All are small `typing.Protocol`s; built-ins are ordinary implementations.

| Protocol | Method(s) | Built-ins |
|----------|-----------|-----------|
| `Judge` | `ask(state, questions) -> Answers` (+ async) | `JevJudge`, `FakeJudge` (scripted), `KeywordJudge`, `RecordingJudge` / `ReplayJudge`, `FallbackJudge(primary, secondary)` |
| `Facet` | `questions(topic, content)`, `interpret(answers)` | membership, categories, fields, scores, flags |
| `Extractor` | `extract(content, field) -> list[str]` | `org`, `title`, `email`, `known_values` (registry by field `kind`) |
| `Policy` | `decide(topic, answers)` | `ThresholdPolicy` |
| `Budget` | `check()`, `record(tokens)` | `Budget(usd, per_minute)` |

Custom facets register by name so they can be used from YAML:

```python
@jevfilter.facet("sentiment")
class Sentiment(ScoreFacet): ...
```

Extractors register the same way (`@jevfilter.extractor("order_number")`).

## Budget

Carried over from [semantic-pubsub-jev](https://github.com/damiensmith1/semantic-pubsub-jev): a rate cap stops loops fast; a
spend cap stops slow bleeds. Both **refuse** (`BudgetExceeded`) rather
than block. Spend = reported `input_tokens` × price, checked before each
call, so overshoot is bounded to one request. One `Budget` can be shared
by many filters. Price is configurable (defaults to Jev's published
$0.042/Mtok).

## Failure policy

`on_error=`:

- `"raise"` (default) — propagate a typed `JudgeError`
- `"review"` — every topic `review` with reason `backend_error`
- a `Judge` — fall back to it (e.g. `KeywordJudge`), results marked
  `degraded=True`

The SDK already retries 429s with backoff and honours `retry-after`;
jevfilter doesn't add a second retry layer.

## Versioning

Topic version = SHA-256 of canonical JSON of judgment-affecting fields
(everything except `meta`). Each result records the topic version, model
and jevfilter **wording version** (bumped whenever question templates
change), so callers can tell which stored results are out of date and
choose to re-judge — e.g. the Gmail app's "rescan after editing a topic".

## Evaluation

`jevfilter.eval` (extra `[eval]`) runs topics over a labelled dataset
(JSONL: content + expected topics / categories / fields) and reports:
per-topic precision / recall, category confusion, calibration (reliability
buckets), review rate, cost — and a threshold sweep showing
accuracy vs review rate. Uses `RecordingJudge` so re-running a sweep
costs nothing.

## CLI

Extra `[cli]`:

- `jevfilter try topics/ "some text"` — judge text, pretty-print result
- `jevfilter explain topics/ file.txt` — show payloads + cost estimate
- `jevfilter lint topics/` — validate and warn
- `jevfilter eval topics/ data.jsonl` — evaluation report

## Package layout

```
src/jevfilter/
  __init__.py      public API re-exports
  helpers.py       choose / check / rate
  topic.py         Topic model, loading, validation, versioning
  facets/          membership, categories, fields, scores, flags, registry
  wording.py       question templates
  plan.py          packing, splitting, token estimates
  engine.py        pipeline, Filter / AsyncFilter
  policy.py        ThresholdPolicy, Decision, reasons
  result.py        Result, TopicResult, provenance
  judges/          jev, fake, keyword, recording, fallback
  extract/         extractors + registry
  items.py         match_item
  track.py         tracking rules
  budget.py  errors.py
  eval/            (extra)
  cli.py           (extra)
tests/  examples/  docs/
```

## Packaging and release

- `pyproject.toml`, src layout, `py.typed`. Built and published with
  **uv** (`uv build`, `uv publish`).
- GitHub Actions: lint + tests on push; publish to PyPI on version tag
  via **trusted publishing** (no stored token). TestPyPI first.
- SemVer; 0.x until `jev-gmail-filter` (a Gmail app built on this library) has run on it for real.
- Reserve the PyPI name early with a minimal 0.0.1.

## Consumer check: jev-gmail-filter

Every need of `jev-gmail-filter` (a Gmail app built on this library) maps to a library feature or stays in
the app:

| Gmail app need | Covered by |
|----------------|------------|
| User topics as files, edited in a UI | `Topic.load`, `from_dict` validation, `to_yaml` round-trip |
| Email in several topics, or none | one Noul per topic in `judge` |
| Category per topic (job stage) | `categories` facet |
| Company / role from the email | `fields` + `org` / `title` extractors |
| Unsure → review queue | `review` outcome + reasons |
| Match email to existing job | `match_item` (code pre-filter, then Jev) |
| Status pipeline, 21-day stale | `track.next_status`, `track.is_stale` |
| Backscan cost estimate | `explain()` / estimates, `Budget` |
| Rescan after editing a topic | topic `version` + wording version on results |
| Backfill many emails fast | `AsyncFilter.judge_many` |
| Gmail label names | topic `meta` |
| Store emails, results, items, reviews | app (SQLite) via `Result.to_dict()` |
| Same-thread shortcut, seen-email check | app |
| Gmail polling, OAuth, labels, UI | app |

## Decisions

- Standalone repo and PyPI package `jevfilter`, MIT licence.
- Stateless library: no storage, cache or hooks. Sources, storage and UI
  belong to consumers, which get plain serialisable results back.
- Built on `typesafe-sdk`, not a reimplementation of the HTTP client.
- One Noul per topic for membership (several can match).
- Field values are selected from candidates, never generated.
- Goals: easy, extensible, powerful (three layers above).

## Open questions

- Python floor: 3.10 (matches `typesafe-sdk`, wider reach) vs 3.12
  (nicer typing)? Proposed: 3.10.
- Speculative facets vs a second request: speculative is one round trip
  but more input tokens per topic. Measure with 5–10 topics; maybe make
  it a per-topic switch.
- Token estimation: chars ÷ 4 heuristic, or is there a count endpoint?
- Is `KeywordJudge` good enough to ship as a fallback, or only as a
  test fake?
- Should `Content` support multi-part state (e.g. an email thread as an
  array) out of the box?
- Default extractors: ship `org` / `title` in core, or as a separate
  extra since they're English- and domain-flavoured?
- Naming: `choose` / `check` / `rate` for helpers — clear enough?

## Testing approach

- Unit tests per pipeline stage with `FakeJudge`.
- Golden tests: exact question payloads built from sample topics
  (catches accidental wording changes).
- Replay tests: recorded real responses (`ReplayJudge`) for end-to-end
  behaviour without network.
- Opt-in live suite (`JEVFILTER_LIVE=1`) on a small fixture set, printing
  spend. Never in CI.
