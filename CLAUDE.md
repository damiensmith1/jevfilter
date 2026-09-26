# jevfilter

Open-source (MIT) Python library for checking a message against
plain-English **topics** with TypeSafe's **Jev**: membership, category,
field selection from candidates, item matching, tracking rules,
thresholds, budget and failure policy. Stateless — no storage, cache,
hooks, sources or UI; callers persist the plain results. Published to
PyPI as `jevfilter`. First consumer: `jev-gmail-filter`, a Gmail app.

## Context

- **Model:** Jev via `typesafe-sdk` (`client.system_one`). Typed answers
  with calibrated probabilities; input tokens billed ($0.042/Mtok), output
  free.
- **Docs:** <https://docs.typesafe.ai> — append `.md` to any page path for
  its Markdown source.
- Pattern origin: <https://github.com/damiensmith1/semantic-pubsub-jev> (Go).
- Project docs: everything under `docs/` — background, requirements,
  design, `topic-format.md` (language-neutral definition schema),
  `api.md` (usage by example; see design.md "Status" for what's built).
- Goals: **easy** (three-line start), **extensible** (every part a small
  protocol), **powerful** (fan-out, hierarchies, scores, items, eval).

## Conventions

- Python ≥ 3.10 (matches `typesafe-sdk`), src layout, typed public API.
- Tooling: `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format .`.
  Check the floor with `uv run --python 3.10 --isolated --with pytest --with pyyaml pytest`.
- Keep it stateless and source-agnostic. Nothing Gmail-specific here.
- `TYPESAFE_API_KEY` lives in `.env` (gitignored). Never log or commit it.
- Tests stub Jev. Live tests are opt-in and never run in CI.
- Commits are atomic and explain *why*. No co-author trailers.

## Keeping docs in sync

Everything under docs/ is this project's source of truth, not a one-time
snapshot — including any file added there after initial setup, not just
background.md/requirements.md/design.md. In the SAME turn as a code
change (not a followup), update the relevant doc when you:
- resolve or add an open question in design.md
- make or change an architecture/approach decision
- add, change, or drop a requirement or non-goal
- learn something that changes the "why" in background.md
- create a new doc under docs/ for a topic that doesn't fit the above

Don't fabricate a decision that wasn't actually made. If it's unclear
whether something is doc-worthy, ask instead of guessing.
