---
title: Releasing
tags: [jevfilter, release]
status: current
---

# Releasing

Releases publish to [PyPI](https://pypi.org/project/jevfilter/) from
GitHub Actions using **trusted publishing**: PyPI trusts
`.github/workflows/release.yml` in this repo, running in the `pypi`
environment. No token is stored anywhere.

## Steps

1. Make sure `main` is green in CI.
2. Bump `version` in `pyproject.toml` ([SemVer](https://semver.org); 0.x
   while the API settles), then refresh the lockfile:

   ```sh
   uv version 0.2.0        # or edit pyproject.toml, then: uv lock
   ```

3. Commit and push:

   ```sh
   git commit -am "Release 0.2.0" && git push
   ```

4. Tag and push the tag. The tag must be `v` + the exact version:

   ```sh
   git tag v0.2.0 && git push origin v0.2.0
   ```

5. Watch the **Release** run in the Actions tab. When it's green the
   version is live; check with `pip install jevfilter==0.2.0`.

## What the release workflow checks

Nothing is uploaded unless every step passes:

- the tag matches the `pyproject.toml` version
- lint and the full test suite
- `uv build` (wheel + sdist)
- the built files contain no `.env` / key files, local paths or API keys
- the wheel installs and imports in a clean environment

## If something goes wrong

- **Workflow failed:** nothing was published. Fix it, delete the tag
  (`git tag -d v0.2.0 && git push origin :v0.2.0`), and tag again.
- **Bad release published:** PyPI never lets a version number be reused.
  Release a fixed `0.2.1`; *yank* the bad one on PyPI (Manage → Releases)
  so installers skip it.
- **Secret published:** rotate the secret first, then delete the release
  on PyPI. Assume anything uploaded was copied by mirrors.

## Who can release

Only the repository admin: `v*` tags are protected by a ruleset, and the
`pypi` environment only accepts deployments from `v*` tags. See
[CONTRIBUTING](../CONTRIBUTING.md) for the rest of the repository rules.
