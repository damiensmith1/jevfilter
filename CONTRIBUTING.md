# Contributing

Thanks for helping. Changes come in through pull requests from forks:

1. Fork the repo and create a branch in your fork.
2. `uv sync`, make your change, and add tests.
3. Check it locally: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
4. Open a pull request against `main`.

## Rules on this repository

- `main` can only change through a pull request. Force-pushes and deletion
  are blocked.
- Every PR needs an approving review from the code owner
  ([@damiensmith1](https://github.com/damiensmith1)), all review threads
  resolved, and passing CI (lint, tests on Python 3.10–3.13, build).
  A new push after approval needs a fresh approval.
- Only the maintainer can create branches or `v*` release tags in this
  repository.
- CI on PRs from outside contributors runs only after the maintainer
  approves it. Workflows get a read-only token.

Tests must never call the real Jev API; use `FakeJudge`. Live checks live
in `tests/live/` and are opt-in (`JEVFILTER_LIVE=1`).

Please report security issues privately; see [SECURITY.md](SECURITY.md).
