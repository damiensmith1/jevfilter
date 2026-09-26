---
title: Requirements
tags: [jevfilter, requirements]
status: draft
---

# Requirements

See [background](background.md) for why, [design](design.md) for how, [topic-format](topic-format.md) for the
definition schema and [api](api.md) for usage.

## Ease of use

- **E1 — Three-line start.** One-call helpers for the common cases:
  "which of these labels?", "which of these conditions hold?", "how much
  of this?". No config files, no classes required.
- **E2 — Plain-English definitions.** A topic needs only a name and a
  description. Everything else is optional and additive.
- **E3 — Definitions as code or files.** Build topics in Python, or load
  YAML / JSON / dicts. Same schema either way.
- **E4 — Helpful errors.** Validation errors name the topic, field and
  fix. Warnings for likely mistakes (duplicate category descriptions,
  empty candidate lists, a Choice with one option).
- **E5 — Inspectable.** Show the exact request that would be sent and
  its estimated cost, without sending it.
- **E6 — Sync and async** with the same API shape.
- **E7 — Works with no API key in tests** via built-in fake judges.
- **E8 — Round-trip definitions.** Topics serialise back to YAML / dict
  unchanged, so apps can offer topic editing in a UI and write the file.
- **E9 — Serialisable results.** `Result.to_dict()` / `from_dict()` with
  everything an app needs to store (probabilities, reasons, provenance,
  topic versions).

## Judging

- **J1 — Membership.** Per topic: probability the content belongs, and an
  outcome `match` / `review` / `no`. Any number of topics may match.
- **J2 — Categories.** Pick one category per matching topic, with
  probabilities and confidence. Categories may be nested (hierarchies).
- **J3 — Fields.** Select a value for each named field from supplied
  candidates plus "none of these" — never generated.
- **J4 — Scores.** Rate content on ordered, described levels (urgency,
  relevance, severity); optional weighted composites.
- **J5 — Flags.** Extra yes/no conditions attached to a topic (e.g.
  "needs a reply").
- **J6 — Conditional facets.** A facet can apply only when membership or
  a category holds; answers for inapplicable facets are discarded.
- **J7 — Item matching.** Given content and existing items, pick which
  item it concerns, or "new". Cheap code-side pre-filtering first.
- **J8 — Tracking rules.** Status pipelines, terminal states and
  staleness as pure functions of a topic's config.

## Performance and cost

- **P1 — Pack.** All questions for one piece of content go in as few
  requests as possible (one, where it fits Jev's context limits).
- **P2 — Split.** Automatically split when a request would exceed limits.
- **P3 — Batch.** Judge many items concurrently with a concurrency limit.
- **P4 — Budget.** Spend cap (USD) and rate cap (calls/min), shareable
  across filters; over-limit calls are refused with a typed error.
- **P5 — Cost reporting.** Every result carries input tokens and cost;
  estimates available before sending.

## Reliability

- **R1 — Failure policy.** On backend error: raise, mark everything
  `review`, or fall back to another judge — caller's choice.
- **R2 — Model pinning.** Pin a model version; every result records the
  version that answered.
- **R3 — Versioned definitions.** Each topic has a stable version derived
  from its judgment-affecting content.
- **R4 — Full provenance.** Results carry raw answers, request IDs,
  question payloads (on request) and reasons for each outcome.

## Extensibility

- **X1 — Pluggable backend** (`Judge` protocol): Jev, fakes, recording /
  replay, fallback, custom.
- **X2 — Pluggable facets**: new kinds of per-topic judgments that emit
  questions and interpret answers.
- **X3 — Pluggable candidate extractors** by field kind.
- **X4 — Pluggable decision policy** (thresholds and review rules).
- **X5 — Consumer metadata**: topics carry arbitrary `meta` for the app
  (labels, colours, routing) that doesn't affect judgments or versions.

## Quality tooling

- **Q1 — Evaluate.** Run topics over a labelled dataset; report accuracy,
  confusion, calibration, review rate and cost; sweep thresholds.
- **Q2 — CLI.** Try a topic on text, lint topic files, estimate cost, run
  evaluations.
- **Q3 — Record / replay** real responses for deterministic tests.

## Non-functional

- Python ≥ 3.10 (matches `typesafe-sdk`) — proposed.
- Core depends only on `typesafe-sdk` and `pydantic`. YAML, CLI and eval
  extras are optional installs (`jevfilter[yaml]`, `[cli]`, `[eval]`).
- **Stateless:** no storage, no cache, no hooks, no files written (except
  test cassettes when explicitly recording), no network except the
  backend. Callers get plain results back and persist them however they
  like. Logging uses the standard `logging` module.
- Fully typed (`py.typed`); public API documented with examples.
- Open source, **MIT**, published to **PyPI** as `jevfilter`.
- Tests never call the real API. Live checks are opt-in and report spend.

## Non-goals

- Sources and transports (Gmail, Slack, queues) — consumers own these.
- Storage, caching or hooks of any kind — items, results and history
  belong to the caller.
- UI.
- Generating text (summaries, replies, extraction by generation).
- Training or fine-tuning models.
- A Go port for now; [topic-format](topic-format.md) is kept language-neutral so one is
  possible later.

## Consumers

- `jev-gmail-filter` (a Gmail app built on this library) — first consumer.
- [semantic-pubsub-jev](https://github.com/damiensmith1/semantic-pubsub-jev) — same pattern in Go; would consume the topic
  format if ported.
