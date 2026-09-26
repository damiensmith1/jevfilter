---
title: Topic format
tags: [jevfilter, spec]
status: draft
---

# Topic format

The language-neutral schema for topic definitions. YAML, JSON and Python
dicts all use it. Kept independent of the Python API so other
implementations (e.g. a Go port for [semantic-pubsub-jev](https://github.com/damiensmith1/semantic-pubsub-jev)) can share
topic files. See [design](design.md) for how each part becomes Jev questions.

## Minimal

```yaml
name: Receipts
description: Receipts, invoices and order confirmations for things I bought.
```

That's a complete topic: one membership question.

## All keys

| Key | Type | Required | Meaning |
|-----|------|----------|---------|
| `name` | string | yes | Unique within a set. Used in results and question IDs. |
| `description` | string \| object | yes | What belongs, in plain English. |
| `exclude` | string \| list | no | What looks similar but doesn't belong. |
| `examples` | object | no | `{match: [...], no: [...]}` short examples, included in the membership question. |
| `categories` | map | no | name → description (string) or nested `{description, children}`. |
| `fields` | map | no | name → `{kind, about, required}`. Values selected from candidates. |
| `scores` | map | no | name → `{about, levels: [...]}`, levels low → high. |
| `composites` | map | no | name → `{score_name: weight, ...}`. Computed in code. |
| `flags` | map | no | name → yes/no condition in plain English. |
| `track` | object | no | Status pipeline for items (below). |
| `thresholds` | object | no | Overrides: `accept`, `reject`, `min_confidence`. |
| `when` | map | no | facet name → condition (below). |
| `meta` | any | no | Consumer data. Ignored by judging; not part of the version. |
| *custom facet* | any | no | Key = a facet name registered in code (e.g. `pii`); value = that facet's config. |

Any other key is a validation error (catches typos like `catagories`).

## Full example

```yaml
name: Jobs
description: >
  My own job search: applications I submitted, and recruiters, HR or
  hiring teams contacting me about a specific role.
exclude: Job alerts, recommendation digests, newsletters, marketing.

categories:
  applied: Confirms I submitted an application.
  recruiter: A recruiter or hiring manager reaches out about a role.
  hiring_contact: Other HR / hiring-team mail about a role.
  interview: Invites me to an interview or phone screen, or confirms / reschedules one.
  assessment: Asks me to complete a take-home, coding test or online assessment.
  rejection: Tells me I'm not moving forward.
  offer: Extends an offer or discusses offer terms.

fields:
  company: {kind: org, about: The hiring company, not a job board or ATS., required: true}
  role: {kind: title, about: The job title.}

flags:
  needs_reply: The sender is asking me to reply, schedule, or send something.

scores:
  urgency:
    about: How soon I need to act.
    levels: [No action needed, This week, Within a day or two, Today]

track:
  match_on: [company]
  statuses:
    contacted: [recruiter]
    applied: [applied]
    assessment: [assessment]
    interviewing: [interview]
    offer: [offer]
  terminal:
    rejected: [rejection]
  stale_after_days: 21

when:
  urgency: {category: [recruiter, interview, assessment, offer]}

meta:
  gmail_label: Jobs
```

## Categories

Flat:

```yaml
categories:
  billing: Charges, invoices, refunds, subscriptions.
  bug: Something is broken or producing errors.
```

Nested (hierarchical; resolved greedily or by beam search):

```yaml
categories:
  hardware:
    description: Physical devices.
    children:
      laptop: Laptops and notebooks.
      phone: Mobile phones.
  software: Apps and services.
```

A result reports the leaf and the full path (`hardware/laptop`).

## Fields

- `kind` picks the candidate extractor (`org`, `title`, `email`,
  `person`, custom). Callers can always pass candidates directly instead.
- `about` describes the field; it goes into the Choice question.
- `required: true` → "none of these" sends the topic to `review`.

## `when`

Limits when a facet's answer is used (it is still asked, speculatively,
unless the engine is configured otherwise):

```yaml
when:
  urgency: {category: [interview, offer]}   # only for these categories
  company: {matched: true}                  # default for all facets
  needs_reply: always                       # even if topic didn't match
```

## `track`

- `statuses` — ordered pipeline; each status lists categories that move
  an item there.
- `terminal` — statuses reachable from anywhere (e.g. rejected).
- Categories not listed (e.g. `hiring_contact`) link content without
  changing status.
- `match_on` — fields that must agree before item matching asks Jev.
- `stale_after_days` — item is stale after this long with no activity.

## Versioning

Version = SHA-256 of the canonical JSON of every key except `meta`.
Editing `meta` never changes the version; editing anything else does.

## Files

A directory of `*.yaml` / `*.json` files, one topic per file, loaded by
glob. A single file may also hold a list under `topics:`.
