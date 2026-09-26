"""The default judge used by helpers and by `Filter` when none is given."""

from __future__ import annotations

from typing import Any

from .judges.base import Judge

_default: Judge | None = None


def configure(
    *,
    api_key: str | None = None,
    model: str | None = None,
    judge: Judge | None = None,
    **client_kwargs: Any,
) -> None:
    """Set the default judge for helpers and filters created without `judge=`.

    ```python
    jf.configure(api_key="...", model="jev-1.13.0")   # a JevJudge
    jf.configure(judge=FakeJudge({...}))               # any Judge
    ```

    Without this, a `JevJudge` reading `TYPESAFE_API_KEY` from the
    environment is created on first use.
    """
    global _default
    if judge is not None:
        if api_key is not None or model is not None or client_kwargs:
            raise ValueError("pass either judge= or JevJudge options, not both")
        _default = judge
        return
    from .judges.jev import JevJudge

    _default = JevJudge(model=model, api_key=api_key, **client_kwargs)


def default_judge() -> Judge:
    global _default
    if _default is None:
        from .judges.jev import JevJudge

        _default = JevJudge()
    return _default


def reset() -> None:
    """Forget the configured default (mainly for tests)."""
    global _default
    _default = None
