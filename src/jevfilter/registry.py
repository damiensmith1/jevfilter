"""Name → implementation registries for custom facets (and, later, extractors)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# Topic keys owned by the topic format itself; custom facets can't use them.
RESERVED_KEYS = frozenset(
    {
        "name",
        "description",
        "exclude",
        "examples",
        "categories",
        "fields",
        "scores",
        "composites",
        "flags",
        "track",
        "thresholds",
        "when",
        "meta",
        "membership",
    }
)

_facets: dict[str, Any] = {}


def facet(name: str) -> Callable[[T], T]:
    """Register a custom facet class (or instance) under a topic key.

    ```python
    @jevfilter.facet("pii")
    class ContainsPII(NoulFacet):
        instructions = "Does `content` contain personal data?"
    ```
    """
    if name in RESERVED_KEYS:
        raise ValueError(f"{name!r} is a built-in topic key and can't be a custom facet name")
    if not name or "/" in name:
        raise ValueError(f"invalid facet name {name!r}")

    def register(obj: T) -> T:
        instance = obj() if isinstance(obj, type) else obj
        try:
            instance.key = name  # lets a facet know which topic key it serves
        except AttributeError:
            pass
        _facets[name] = instance
        return obj

    return register


def get_facet(name: str) -> Any | None:
    return _facets.get(name)


def facet_names() -> list[str]:
    return sorted(_facets)


def unregister_facet(name: str) -> None:
    _facets.pop(name, None)
