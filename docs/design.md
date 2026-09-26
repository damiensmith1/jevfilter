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

Helpers call the same `Judge` backend and question-wording module as
topics, without building a topic (no membership question is wasted).

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
+ the longest question. The planner estimates tokens as characters ÷ 4
with a 0.9 safety margin (`Limits`), and first-fit packs questions, in
order, into as few requests as possible, resending the state per request.
State over its budget is truncated per `truncate=`: `head` (default)
repeatedly shortens the longest string in the state, `head_tail` keeps
both ends, a callable does its own, and `error` refuses. The result
carries a warning. A single question over the limit is an error.

If any request for a piece of content fails, the failure policy applies
to the whole piece (a fallback judge re-asks every request). `AsyncFilter`
sends a piece's requests concurrently.

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

`match_item(content, topic, items, *, result=None, fields=None)`. The
content's own field values come from a `judge` result and/or `fields=`.

1. **Pre-filter in code** — keep items whose `match_on` fields equal the
   content's values, compared case-, accent-, punctuation- and
   company-suffix-insensitively (`Acme, Inc.` = `acme`). A `match_on`
   field the content has no value for doesn't filter. No survivors → "new"
   without calling Jev (`asked=False`).
2. **Choose** — one Choice over the survivors (described by fields,
   status and caller-supplied summary) plus "new", with the content's
   values in the instructions. Asked even for a single survivor: the same
   company can still be a different role.
3. **Decide** — confidence below `min_confidence` → `review`
   (`low_confidence:item`). Budget and failure policy apply as for `judge`.

Returns an `ItemMatch` (serialisable). Skipping matching when content is
already linked (e.g. the same email thread) is the consumer's shortcut.

## Tracking rules

Pure functions over a topic's `track` config (no storage, no Jev):

- `track.status_for(topic, category)` — the status a category moves to, or None
- `track.initial_status(topic, category)` — where the category maps, else
  the first status
- `track.next_status(topic, current, category, *, last_stage=None)` —
  forward-only through `statuses`; `terminal` always applies; categories
  not listed link without moving. A terminal item stays terminal unless
  the caller passes its last pipeline status as `last_stage` and the new
  status is later than that (the library can't know an item's history).
- `track.is_stale(topic, last_activity, now=None, *, status=None)` — no
  activity for `stale_after_days`; terminal items never go stale.
- `track.is_terminal(topic, status)`

Nested category paths (`hw/ok`) match their leaf name in `track` lists.

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
request (including each split request and item match), so overshoot is
bounded to one request. A passing check reserves its slot in the rate
window, so concurrent callers can't all slip through. One `Budget` is
thread-safe and can be shared by many filters. Price is configurable
(defaults to Jev's published $0.042/Mtok) and also sets the filter's cost
reporting. `BudgetExceeded` is never turned into `review` by `on_error`:
refusing is the point. In `judge_many`, `return_exceptions=True` returns
refusals in place so the rest of a batch survives.

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

`jevfilter.eval` runs topics over a labelled dataset (JSONL: content,
optional candidates, and expected match / category / fields per topic; a
topic not listed is expected not to match) and reports, per topic and
overall:

- membership: precision over automatic matches, recall (a positive sent
  to review counts as not yet found), review rate, accuracy of automatic
  decisions
- category confusion and accuracy; per-field accuracy
- calibration: 10 reliability buckets, Brier score, ECE
- cost, tokens, requests
- optional threshold sweep (accept × reject grid, reject ≤ accept),
  re-deciding membership from stored probabilities with no new calls;
  `format()` shows only the non-dominated rows

Backend errors are left out of calibration and the sweep. Pass
`results=` to re-score a previous run; use `RecordingJudge` so re-running
costs nothing.

## CLI

Installed with the package (argparse, no extra dependencies):

- `jevfilter try topics/ "some text"` — judge text, pretty-print result
- `jevfilter explain topics/ --file email.json` — payloads + cost; sends nothing
- `jevfilter lint topics/` — validate and warn (exit 2 on errors)
- `jevfilter eval topics/ data.jsonl --sweep` — evaluation report

`try` / `eval` take `--model`, `--max-usd`, `--record` / `--replay`,
`--json`, and `--env-file` (the CLI reads a `.env` only when told to).
Exit codes: 0 ok, 1 backend / budget error, 2 invalid input.

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
  judges/          jev (sync + async), fake, recording / replay
  extract/         extractors + registry
  items.py         match_item
  track.py         tracking rules
  budget.py  errors.py
  eval.py          evaluation + threshold sweep
  cli.py           jevfilter command
tests/  examples/  docs/
```

## Packaging and release

- `pyproject.toml`, src layout, `py.typed`. Built and published with
  **uv** (`uv build`, `uv publish`).
- GitHub Actions: lint + tests on push; publish to PyPI on version tag
  via **trusted publishing** (no stored token). TestPyPI skipped: CI and
  the release workflow check the build, and a bad release is fixed with a
  new version.
- SemVer; 0.x until `jev-gmail-filter` (a Gmail app built on this library) has run on it for real.
- First release is 0.1.0 (no separate 0.0.1 name reservation).

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
- Python ≥ 3.10.
- Helper names `choose` / `check` / `rate`.
- API key: `TYPESAFE_API_KEY` from the environment (via the SDK), or
  `jf.configure(api_key=, model=)` / `JevJudge(api_key=)`. The library
  never reads `.env` files (stateless, no file access).
- Question instructions are JSON objects: an optional `premise` (for
  speculative facets), the `task`, facet details, and the `topic` block
  (`name`, `description`, `does_not_include`). Membership adds
  `examples` as `belongs` / `does_not_belong`.
- A field with no candidates is not asked; it counts as "none of these"
  and the result carries a warning.
- `Content(candidates=...)` is keyed topic → field; topic `"*"` applies
  to every topic. `Content(context=...)` is sent as state beside `content`.
- `track` stays in jevfilter (pure rules over the topic's `track` config),
  even though it never calls Jev: it's small, already part of the topic
  format, and useful to any app that tracks items.
- `match_item` asks Jev even when one item survives the pre-filter.
- `AsyncFilter` accepts sync judges (run in a thread); `AsyncJevJudge`
  keeps one client per event loop.
- CLI and eval ship in the core package: they need no extra
  dependencies, so `[cli]` / `[eval]` extras would only add friction.
- `RecordingJudge` wraps sync judges only and replays requests it has
  already recorded (pay only for new ones); `ReplayJudge` never calls out.

## Open questions

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

## Status

Built (0.1.0, plus 0.2.0 below):

- `Topic`: validation (all problems at once, typo hints), warnings,
  YAML / JSON / dict loading, round-trip, version.
- `Filter.judge` and `explain`, single request; facets: membership,
  flat categories, fields (from supplied candidates), scores, flags,
  composites, `when`, custom facets (`NoulFacet` / `ChoiceFacet` /
  `ScoreFacet`).
- `ThresholdPolicy`; failure policy (`raise` / `review` / fallback judge).
- `Result` / `TopicResult` `to_dict` / `from_dict`.
- `JevJudge`, `FakeJudge`; helpers `choose` / `check` / `rate`.
- `jf.configure()` for the default judge / API key.
- CI (GitHub Actions): ruff, tests on Python 3.10–3.13, `uv build`.
- Release workflow: a `v*` tag checks the version, tests, builds, scans
  the built files for secrets / local paths, then publishes via PyPI
  trusted publishing (environment `pypi`). TestPyPI skipped.
- Opt-in live tests (`JEVFILTER_LIVE=1`); passed on `jev-1.13.0`.

Added in 0.2.0:

- `Budget` (spend + rate caps, shared, thread-safe), `BudgetExceeded`.
- Request packing / splitting and truncation (`Limits`, `truncate=`).
- `match_item` → `ItemMatch`; `track.*` rules.
- `AsyncFilter` (`judge`, `judge_many`, `match_item`), `AsyncJevJudge`.
- Live tests for item matching and an async batch under a budget, passed
  on `jev-1.13.0`.

Added in 0.3.0:

- `RecordingJudge` / `ReplayJudge` (JSONL cassettes).
- `jevfilter.eval`: metrics, calibration, threshold sweep.
- `jevfilter` CLI: `try`, `explain`, `lint`, `eval`.
- Checked live on `jev-1.13.0`: CLI `try`, and `eval` recorded then
  replayed with no API calls.

Not yet: judging nested categories (parsed, but rejected at judge time),
extractors, `KeywordJudge` / `FallbackJudge`.

## Testing approach

- Unit tests per pipeline stage with `FakeJudge`.
- Golden tests: exact question payloads built from sample topics
  (catches accidental wording changes).
- Replay tests: recorded real responses (`ReplayJudge`) for end-to-end
  behaviour without network.
- Opt-in live suite (`JEVFILTER_LIVE=1`) on a small fixture set, printing
  spend. Never in CI.
