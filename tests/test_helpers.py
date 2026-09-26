import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge


def test_choose():
    fake = FakeJudge({"choice": {"billing": 0.97, "bug": 0.03}})
    c = jf.choose("I was charged twice", ["billing", "bug", "feature request"], judge=fake)
    assert c.value == "billing" and c.confidence == 0.97
    q = fake.calls[0][1]["choice"]
    assert q.criteria == {"billing": None, "bug": None, "feature request": None}


def test_choose_with_descriptions():
    fake = FakeJudge({"choice": "bug"})
    c = jf.choose("It crashes", {"billing": "Charges", "bug": "Something broke"}, judge=fake)
    assert c.value == "bug"
    assert fake.calls[0][1]["choice"].criteria["bug"] == "Something broke"


def test_check():
    fake = FakeJudge({"needs_reply": 0.98, "has_deadline": 0.95})
    out = jf.check(
        "Can you send the signed form by Friday?",
        {"needs_reply": "The sender wants a reply", "has_deadline": "A deadline is mentioned"},
        judge=fake,
    )
    assert out == {"needs_reply": 0.98, "has_deadline": 0.95}


def test_rate():
    fake = FakeJudge({"score": 2})
    s = jf.rate("The site is down", "severity", ["cosmetic", "degraded", "blocking"], judge=fake)
    assert s.level == "blocking" and s.value == 2 and s.normalized == 1.0


def test_helpers_validate_input():
    with pytest.raises(ValueError):
        jf.choose("x", ["only"], judge=FakeJudge())
    with pytest.raises(ValueError):
        jf.rate("x", "d", ["one"], judge=FakeJudge())
