from datetime import datetime, timedelta, timezone

import pytest

import jevfilter as jf
from jevfilter import track


@pytest.fixture
def t(jobs):
    return jobs  # statuses: contacted, applied, interviewing; terminal: rejected


def test_status_for(t):
    assert track.status_for(t, "applied") == "applied"
    assert track.status_for(t, "rejection") == "rejected"
    assert track.status_for(t, jf.Choice("interview", 0.9)) == "interviewing"
    assert track.status_for(t, "unlisted") is None
    assert track.status_for(t, None) is None


def test_initial_status(t):
    assert track.initial_status(t, "applied") == "applied"
    assert track.initial_status(t, "rejection") == "rejected"
    assert track.initial_status(t, None) == "contacted"  # first status
    assert track.initial_status(t, "unlisted") == "contacted"


@pytest.mark.parametrize(
    "current, category, expected",
    [
        (None, "applied", "applied"),
        ("contacted", "applied", "applied"),  # forward
        ("interviewing", "applied", "interviewing"),  # never backward
        ("applied", "applied", "applied"),
        ("applied", "unlisted", "applied"),  # links without moving
        ("applied", None, "applied"),
        ("contacted", "rejection", "rejected"),  # terminal from anywhere
        ("rejected", "interview", "rejected"),  # sticky without last_stage
    ],
)
def test_next_status(t, current, category, expected):
    assert track.next_status(t, current, category) == expected


def test_terminal_reopens_only_on_a_later_stage(t):
    assert track.next_status(t, "rejected", "interview", last_stage="applied") == "interviewing"
    assert track.next_status(t, "rejected", "applied", last_stage="applied") == "rejected"
    assert track.next_status(t, "rejected", "recruiter", last_stage="interviewing") == "rejected"


def test_next_status_validates(t):
    with pytest.raises(ValueError, match="unknown status"):
        track.next_status(t, "archived", "applied")
    with pytest.raises(ValueError, match="last_stage"):
        track.next_status(t, "rejected", "applied", last_stage="rejected")


def test_is_stale(t):
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    assert not track.is_stale(t, now - timedelta(days=20), now)
    assert track.is_stale(t, now - timedelta(days=21), now)
    assert not track.is_stale(t, now - timedelta(days=90), now, status="rejected")
    assert track.is_stale(t, datetime.now(timezone.utc) - timedelta(days=30))


def test_is_terminal(t):
    assert track.is_terminal(t, "rejected")
    assert not track.is_terminal(t, "applied") and not track.is_terminal(t, None)


def test_topics_without_track():
    plain = jf.Topic(name="P", description="d")
    with pytest.raises(ValueError, match="no `track`"):
        track.next_status(plain, None, "x")
    no_stale = jf.Topic(
        name="N", description="d", categories={"a": "A", "b": "B"}, track={"statuses": {"s": ["a"]}}
    )
    assert not track.is_stale(no_stale, datetime(2000, 1, 1), datetime(2026, 1, 1))


def test_nested_category_paths_match_leaves():
    t = jf.Topic(
        name="S",
        description="d",
        categories={"hw": {"description": "x", "children": {"broken": "B", "ok": "O"}}, "sw": "S"},
        track={"statuses": {"open": ["broken"]}, "terminal": {"closed": ["hw/ok"]}},
    )
    assert track.status_for(t, "hw/broken") == "open"
    assert track.status_for(t, "hw/ok") == "closed"
