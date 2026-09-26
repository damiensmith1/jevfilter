"""Category examples / exclusions, FallbackJudge and the token estimate."""

import pytest

import jevfilter as jf
from jevfilter import wording
from jevfilter.judges import FakeJudge, FallbackJudge
from jevfilter.plan import CHARS_PER_TOKEN, estimate_tokens

CATS = {
    "applied": {
        "description": "Confirms I submitted an application.",
        "examples": ["Thanks for applying", "We received your application"],
        "exclude": "A recruiter reaching out first",
    },
    "recruiter": "A recruiter reaches out about a role.",
}


def test_category_examples_and_exclude_parse_and_round_trip():
    t = jf.Topic(name="Jobs", description="d", categories=CATS)
    applied = t.categories["applied"]
    assert applied.examples == ("Thanks for applying", "We received your application")
    assert applied.exclude == "A recruiter reaching out first"
    assert jf.Topic.from_dict(t.to_dict()) == t


def test_category_criteria_use_an_object_only_when_needed():
    t = jf.Topic(name="Jobs", description="d", categories=CATS)
    q = wording.categories(t, t.categories, speculative=True)
    assert q.criteria == {
        "applied": {
            "description": "Confirms I submitted an application.",
            "examples": ["Thanks for applying", "We received your application"],
            "does_not_include": "A recruiter reaching out first",
        },
        "recruiter": "A recruiter reaches out about a role.",
    }


def test_plain_categories_are_unchanged():
    t = jf.Topic(name="T", description="d", categories={"a": "A", "b": {"description": "B"}})
    q = wording.categories(t, t.categories, speculative=False)
    assert q.criteria == {"a": "A", "b": "B"}


@pytest.mark.parametrize(
    "value, message",
    [
        ({"description": "x", "examples": "one"}, "examples` must be a list"),
        ({"description": "x", "exclude": 3}, "exclude` must be text"),
        ({"description": "x", "hint": "y"}, "unknown keys"),
    ],
)
def test_category_validation(value, message):
    with pytest.raises(jf.TopicError, match=message):
        jf.Topic(name="T", description="d", categories={"a": value, "b": "B"})


def test_fallback_judge_uses_secondary_and_marks_degraded():
    primary = FakeJudge(fail=RuntimeError("pinned model gone"))
    secondary = FakeJudge({"T/membership": 0.9}, model="jev-latest")
    judge = FallbackJudge(primary, secondary)
    r = jf.Filter(jf.Topic(name="T", description="d"), judge=judge).judge("x")
    assert r["T"].matched and r.model == "jev-latest" and r.degraded
    assert any("pinned model gone" in w for w in r.warnings)
    assert judge.fallbacks == 1


def test_fallback_judge_passes_through_when_primary_works():
    judge = FallbackJudge(FakeJudge({"T/membership": 0.9}), FakeJudge())
    r = jf.Filter(jf.Topic(name="T", description="d"), judge=judge).judge("x")
    assert not r.degraded and judge.fallbacks == 0 and judge.secondary.calls == []


def test_fallback_judge_both_fail():
    judge = FallbackJudge(FakeJudge(fail=RuntimeError("a")), FakeJudge(fail=RuntimeError("b")))
    with pytest.raises(jf.JudgeError, match="primary failed.*fallback failed"):
        jf.Filter(jf.Topic(name="T", description="d"), judge=judge).judge("x")


def test_fallback_judge_needs_sync_judges():
    class AsyncOnly:
        async def ask(self, state, questions): ...

    with pytest.raises(TypeError, match="secondary is async"):
        FallbackJudge(FakeJudge(), AsyncOnly())


def test_token_estimate_is_calibrated():
    assert CHARS_PER_TOKEN == 2.7
    text = "x" * 270
    assert estimate_tokens(text) == int(272 / 2.7) + 1  # JSON adds the quotes
    assert estimate_tokens(text, chars_per_token=4) == 69
