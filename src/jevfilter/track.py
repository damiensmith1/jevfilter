"""Tracking rules: pure functions over a topic's `track` config.

No storage and no Jev calls. The caller stores each item's status and
last activity; these functions say what the next status is and whether an
item has gone quiet.

- Statuses move forward only, in the order listed under `statuses`.
- A `terminal` status (e.g. rejected) applies from anywhere.
- A terminal item stays terminal, unless the caller passes the item's last
  pipeline status as `last_stage` and the new category is later than it.
- Categories not listed anywhere link content without changing status.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .result import Choice
from .topic import Topic, Track


def _track(topic: Topic) -> Track:
    if topic.track is None:
        raise ValueError(f"Topic {topic.name!r} has no `track` config")
    return topic.track


def _category_name(category: Any) -> str | None:
    if category is None:
        return None
    return category.value if isinstance(category, Choice) else str(category)


def _lists(entries: tuple[str, ...], category: str) -> bool:
    leaf = category.rsplit("/", 1)[-1]
    return category in entries or leaf in entries


def status_for(topic: Topic, category: Any) -> str | None:
    """The status a category moves an item to, or None if it doesn't move it."""
    tr = _track(topic)
    name = _category_name(category)
    if name is None:
        return None
    for status, cats in tr.terminal.items():
        if _lists(cats, name):
            return status
    for status, cats in tr.statuses.items():
        if _lists(cats, name):
            return status
    return None


def is_terminal(topic: Topic, status: str | None) -> bool:
    return status is not None and status in _track(topic).terminal


def initial_status(topic: Topic, category: Any = None) -> str | None:
    """Status for a new item: where its category maps, else the first status."""
    tr = _track(topic)
    return status_for(topic, category) or next(iter(tr.statuses), None)


def next_status(
    topic: Topic,
    current: str | None,
    category: Any,
    *,
    last_stage: str | None = None,
) -> str | None:
    """The item's status after content with `category` arrives."""
    tr = _track(topic)
    if current is None:
        return initial_status(topic, category)
    order = list(tr.statuses)
    if current not in order and current not in tr.terminal:
        raise ValueError(f"Topic {topic.name!r}: unknown status {current!r}")
    if last_stage is not None and last_stage not in order:
        raise ValueError(f"Topic {topic.name!r}: last_stage {last_stage!r} isn't a pipeline status")

    target = status_for(topic, category)
    if target is None or target == current:
        return current
    if target in tr.terminal:
        return target
    if current in tr.terminal:
        reopen = last_stage is not None and order.index(target) > order.index(last_stage)
        return target if reopen else current
    return target if order.index(target) > order.index(current) else current


def is_stale(
    topic: Topic,
    last_activity: datetime,
    now: datetime | None = None,
    *,
    status: str | None = None,
) -> bool:
    """True when nothing arrived for `stale_after_days`. Terminal items never go stale."""
    tr = _track(topic)
    if tr.stale_after_days is None or is_terminal(topic, status):
        return False
    now = now or datetime.now(last_activity.tzinfo)
    return now - last_activity >= timedelta(days=tr.stale_after_days)
