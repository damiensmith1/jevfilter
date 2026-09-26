---
title: Background
tags: [jevfilter, jev, typesafe, library]
status: draft
---

# Background

## The recurring problem

A piece of content arrives — an email, an event, a ticket, a document —
and code needs to decide things about it that only a person could
previously judge:

- Does it belong to any of these user-defined topics? (several may apply)
- Which category is it?
- Which company / person / order is it about?
- Which thing we're already tracking does it concern?
- How urgent / relevant / severe is it?

Keyword rules can't express meaning. Generative LLM prompts can, but
return prose to parse, drift between runs, and give no usable confidence.

## Why Jev

TypeSafe's **Jev** is a System One model: it takes a *state* and a set of
*questions* and returns typed answers with calibrated probabilities —
**Noul** (probability of yes), **Choice** (one of a set, with a
distribution), **Score** (a position on ordered levels). All questions in
a request see the same state and are evaluated independently and in
parallel, so asking many questions at once costs little extra latency.
Input tokens are billed ($0.042 per million at the time of writing),
output is free — an estimated few cents per thousand typical emails.

That's the right primitive, but it's low level. Every project ends up
writing the same layer on top of it.

## Where the pattern came from

- [semantic-pubsub-jev](https://github.com/damiensmith1/semantic-pubsub-jev) (Go) — subscribers state interests in plain
  English; Jev decides who gets each message. Measured and settled:
  - batch all conditions into one request (1 → 100 questions: 193ms →
    224ms)
  - one Noul per condition, because several can match
  - keep raw probabilities, not just the thresholded boolean
  - pin the model version when measuring; record which version answered
  - two spend ceilings (rate and total) that refuse rather than block
  - an explicit failure policy (fail open vs closed)
- `jev-gmail-filter` (a Gmail app built on this library) (Python prototype) — user-defined topics filter
  Gmail, with categories, tracked items and status pipelines. Added:
  topics as files, selecting field values from code-extracted candidates
  rather than generating them, a review band for uncertain answers, and
  matching an email to an existing tracked item. A live run on four
  synthetic job emails classified all of them correctly, including
  rejecting a job-alert digest.

## What jevfilter is

An open-source Python library that turns "judge this content against
these plain-English definitions" into a few lines of code, and scales up
to categories, fields, scores, tracked items and hierarchies without
changing tools. It owns the layer both projects rebuilt: turning
definitions into questions, packing them into requests, applying
decision policy, controlling spend, handling failure, and making results
inspectable and testable.

It is not an app, and it doesn't know about email.

## Goals

- **Easy** — a first result in three lines; definitions in plain English.
- **Extensible** — every moving part (backend, extractors, facets,
  policy) is a small protocol you can replace.
- **Powerful** — speculative fan-out, many topics per request,
  hierarchical categories, composite scores, entity/item matching,
  tracking rules, batch processing, evaluation against labelled data.

## Prior art

- `typesafe-sdk` — the official client. jevfilter builds on it.
- Gmail filters / rule engines — keyword and header matching, no meaning.
- LLM classification prompts — generated text, uncalibrated.
- TypeSafe cookbooks (fan-out, composite scoring, hierarchical
  classification, value extraction) — patterns jevfilter packages as
  reusable building blocks.
