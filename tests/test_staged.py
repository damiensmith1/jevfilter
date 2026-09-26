import asyncio

import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge

EMAIL = jf.Content("x", candidates={"Jobs": {"company": ["Acme"]}})


def staged(script, **kw):
    fake = FakeJudge(script)
    return jf.Filter(jf.Topic.load("tests/topics"), judge=fake, speculative=False, **kw), fake


def test_membership_first_then_only_matching_topics():
    f, fake = staged(
        {
            "Jobs/membership": 0.9,
            "Receipts/membership": 0.05,
            "Jobs/categories": "interview",
            "Jobs/fields/company": "Acme",
        }
    )
    r = f.judge(EMAIL)
    assert len(fake.calls) == 2
    assert set(fake.calls[0][1]) == {"Jobs/membership", "Receipts/membership"}
    assert "Jobs/categories" in fake.calls[1][1]
    assert all(q.startswith("Jobs/") for q in fake.calls[1][1])
    assert r["Jobs"].matched and r["Jobs"].category.value == "interview"
    assert r["Receipts"].outcome == "no"
    assert len(r.request_ids) == 2


def test_same_outcomes_as_speculative():
    script = {
        "Jobs/membership": 0.9,
        "Receipts/membership": 0.5,
        "Jobs/categories": "applied",
        "Jobs/fields/company": "Acme",
        "Jobs/scores/urgency": 2,
        "Jobs/flags/needs_reply": 0.7,
    }
    spec = jf.Filter(jf.Topic.load("tests/topics"), judge=FakeJudge(script)).judge(EMAIL)
    two, _ = staged(script)
    assert two.judge(EMAIL).topics == spec.topics


def test_no_second_request_when_nothing_could_match():
    f, fake = staged({"*/membership": 0.01})
    r = f.judge(EMAIL)
    assert len(fake.calls) == 1 and all(t.outcome == "no" for t in r)


def test_review_band_topics_get_their_facets():
    f, fake = staged({"Jobs/membership": 0.5, "Jobs/categories": "applied"})
    r = f.judge(EMAIL)
    assert r["Jobs"].outcome == "review" and r["Jobs"].category.value == "applied"
    assert len(fake.calls) == 2


def test_topic_reject_threshold_decides_the_second_stage():
    t = jf.Topic(name="T", description="d", flags={"x": "X"}, thresholds={"reject": 0.6})
    f = jf.Filter(t, judge=FakeJudge({"T/membership": 0.5}), speculative=False)
    f.judge("x")
    assert len(f.backend.calls) == 1


def test_always_facets_are_asked_up_front():
    t = jf.Topic(
        name="T", description="d", flags={"urgent": "U", "other": "O"}, when={"urgent": "always"}
    )
    fake = FakeJudge({"T/membership": 0.0, "T/flags/urgent": 0.9})
    r = jf.Filter(t, judge=fake, speculative=False).judge("x")
    assert set(fake.calls[0][1]) == {"T/membership", "T/flags/urgent"}
    assert len(fake.calls) == 1 and r["T"].flags == {"urgent": 0.9}


def test_fewer_tokens_when_most_topics_dont_match():
    script = {"*/membership": 0.01}
    spec = jf.Filter(jf.Topic.load("tests/topics"), judge=FakeJudge(script)).judge(EMAIL)
    two, _ = staged(script)
    assert two.judge(EMAIL).input_tokens < spec.input_tokens / 2


def test_second_stage_failure_follows_on_error():
    class FailSecond(FakeJudge):
        def ask(self, state, questions):
            if self.calls:
                self.calls.append((state, dict(questions)))
                raise jf.JudgeError("second failed")
            return super().ask(state, questions)

    f = jf.Filter(
        jf.Topic.load("tests/topics"),
        judge=FailSecond({"Jobs/membership": 0.9}),
        speculative=False,
        on_error="review",
    )
    r = f.judge(EMAIL)
    assert r.degraded and all(t.reasons == ("backend_error",) for t in r)
    raising = jf.Filter(
        jf.Topic.load("tests/topics"), judge=FailSecond({"Jobs/membership": 0.9}), speculative=False
    )
    with pytest.raises(jf.JudgeError):
        raising.judge(EMAIL)


def test_first_stage_failure_is_reported():
    f = jf.Filter(
        jf.Topic.load("tests/topics"),
        judge=FakeJudge(fail=RuntimeError("down")),
        speculative=False,
        on_error="review",
    )
    assert f.judge(EMAIL).degraded


def test_explain_shows_worst_case_followup():
    f, fake = staged({})
    plan = f.explain(EMAIL)
    assert fake.calls == []
    assert set(plan.requests[0]["questions"]) == {"Jobs/membership", "Receipts/membership"}
    assert "Jobs/categories" in plan.followup_requests[0]["questions"]
    assert plan.max_cost_usd > plan.cost_usd
    spec = jf.Filter(jf.Topic.load("tests/topics"), judge=FakeJudge()).explain(EMAIL)
    assert spec.followup_requests == [] and spec.max_cost_usd == spec.cost_usd


def test_async_staged():
    f = jf.AsyncFilter(
        jf.Topic.load("tests/topics"),
        judge=FakeJudge({"Jobs/membership": 0.9, "Jobs/fields/company": "Acme"}),
        speculative=False,
    )
    r = asyncio.run(f.judge(EMAIL))
    assert r["Jobs"].matched and len(r.request_ids) == 2
    none = jf.AsyncFilter(jf.Topic.load("tests/topics"), judge=FakeJudge(), speculative=False)
    assert len(asyncio.run(none.judge(EMAIL)).request_ids) == 1
    failing = jf.AsyncFilter(
        jf.Topic.load("tests/topics"),
        judge=FakeJudge(fail=RuntimeError("x")),
        speculative=False,
        on_error="review",
    )
    assert asyncio.run(failing.judge(EMAIL)).degraded
