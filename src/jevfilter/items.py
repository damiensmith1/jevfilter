"""Item matching helpers: which caller-owned item is content about?

Items are the caller's records (the library never stores them): a mapping
or object with `id`, `fields`, and optionally `status` and `summary`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .result import Field, TopicResult
from .topic import Topic


@dataclass(frozen=True)
class ItemView:
    id: Any
    fields: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    summary: Any = None

    @property
    def label(self) -> str:
        return f"item {self.id}"

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {"fields": self.fields}
        if self.status is not None:
            d["status"] = self.status
        if self.summary is not None:
            d["summary"] = self.summary
        return d


def as_items(items: Iterable[Any]) -> list[ItemView]:
    out: list[ItemView] = []
    seen: set[Any] = set()
    for item in items:
        get = item.get if isinstance(item, Mapping) else lambda k, d=None, i=item: getattr(i, k, d)
        item_id = get("id")
        if item_id is None:
            raise ValueError(f"every item needs an `id`; got {item!r}")
        if item_id in seen:
            raise ValueError(f"item id {item_id!r} appears more than once")
        seen.add(item_id)
        fields = get("fields") or {}
        if not isinstance(fields, Mapping):
            raise ValueError(f"item {item_id!r}: `fields` must be a mapping")
        out.append(ItemView(item_id, dict(fields), get("status"), get("summary")))
    return out


def content_values(
    topic: Topic,
    result: TopicResult | None,
    fields: Mapping[str, Any] | None,
) -> dict[str, str]:
    """The content's own field values, from a judge result and/or `fields=`."""
    values: dict[str, Any] = {}
    if result is not None:
        if result.topic != topic.name:
            raise ValueError(f"result is for topic {result.topic!r}, not {topic.name!r}")
        values.update({k: f.value for k, f in result.fields.items()})
    if fields:
        values.update({k: (v.value if isinstance(v, Field) else v) for k, v in fields.items()})
    return {k: v for k, v in values.items() if isinstance(v, str) and v.strip()}


def prefilter(topic: Topic, items: list[ItemView], values: Mapping[str, str]) -> list[ItemView]:
    """Keep items whose `match_on` fields equal the content's (normalised).

    A `match_on` field the content has no value for doesn't filter.
    """
    keys = [k for k in (topic.track.match_on if topic.track else ()) if k in values]
    return [
        item
        for item in items
        if all(normalize(item.fields.get(k)) == normalize(values[k]) for k in keys)
    ]


_SUFFIXES = re.compile(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|gmbh|plc)$")


def normalize(value: Any) -> str:
    """Case-, accent-, punctuation- and company-suffix-insensitive form."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _SUFFIXES.sub("", text).strip()
