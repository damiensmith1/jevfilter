"""The default judge used by helpers and by filters created without `judge=`."""

from __future__ import annotations

from typing import Any

from .judges.base import AsyncJudge, Judge

_default: Judge | None = None
_async_default: AsyncJudge | Judge | None = None
_options: dict[str, Any] = {}


def configure(
    *,
    api_key: str | None = None,
    model: str | None = None,
    judge: Judge | None = None,
    **client_kwargs: Any,
) -> None:
    """Set the default judge for helpers and filters created without `judge=`.

    ```python
    jf.configure(api_key="...", model="jev-1.13.0")   # Jev (sync and async)
    jf.configure(judge=FakeJudge({...}))               # any Judge
    ```

    Without this, Jev is used with `TYPESAFE_API_KEY` from the environment.
    """
    global _default, _async_default, _options
    if judge is not None:
        if api_key is not None or model is not None or client_kwargs:
            raise ValueError("pass either judge= or JevJudge options, not both")
        _default = _async_default = judge
        _options = {}
        return
    from .judges.jev import JevJudge

    _options = {"model": model, "api_key": api_key, **client_kwargs}
    _default = JevJudge(**_options)
    _async_default = None


def default_judge() -> Judge:
    global _default
    if _default is None:
        from .judges.jev import JevJudge

        _default = JevJudge(**_options)
    return _default


def default_async_judge() -> AsyncJudge | Judge:
    """The configured judge, or an `AsyncJevJudge` with the configured options."""
    global _async_default
    if _async_default is None:
        from .judges.jev import AsyncJevJudge

        _async_default = AsyncJevJudge(**_options)
    return _async_default


def reset() -> None:
    """Forget the configured default (mainly for tests)."""
    global _default, _async_default, _options
    _default = _async_default = None
    _options = {}
