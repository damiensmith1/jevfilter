import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge

EMAIL = {"from": "careers@acme.example", "subject": "Application received", "body": "..."}
CANDIDATES = {"Jobs": {"company": ["Acme", "Initech"], "role": ["Backend Engineer"]}}


def judge_with(topics, script, **kw):
    fake = FakeJudge(script)
    return jf.Filter(topics, judge=fake, **kw), fake


def test_one_request_with_stable_question_ids(topics):
    f, fake = judge_with(topics, {})
    f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    assert len(fake.calls) == 1
    state, questions = fake.calls[0]
    assert state == {"content": EMAIL}
    assert list(questions) == [
        "Jobs/membership",
        "Jobs/categories",
        "Jobs/fields/company",
        "Jobs/fields/role",
        "Jobs/scores/urgency",
        "Jobs/flags/needs_reply",
        "Receipts/membership",
    ]


def test_match_with_every_facet(topics):
    f, _ = judge_with(
        topics,
        {
            "Jobs/membership": 0.95,
            "Jobs/categories": {"interview": 0.9, "recruiter": 0.1},
            "Jobs/fields/company": "Acme",
            "Jobs/fields/role": "none of these",
            "Jobs/scores/urgency": 3,
            "Jobs/flags/needs_reply": 0.8,
            "Receipts/membership": 0.02,
        },
    )
    r = f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    jobs = r["Jobs"]
    assert jobs.outcome == "match" and jobs.reasons == ()
    assert jobs.category == jf.Choice(
        "interview", 0.9, {"applied": 0.0, "recruiter": 0.1, "interview": 0.9, "rejection": 0.0}
    )
    assert jobs.fields["company"].value == "Acme"
    assert jobs.fields["role"].value is None  # not required, so still a match
    assert jobs.scores["urgency"].level == "Today"
    assert jobs.composites == {"priority": 1.0}
    assert jobs.flags == {"needs_reply": 0.8}
    assert r.matches == [jobs]
    assert r["Receipts"].outcome == "no" and r["Receipts"].reasons == ("not_member",)
    assert r.model == "fake" and r.request_ids == ("fake-1",)
    assert r.input_tokens > 0 and r.cost_usd == pytest.approx(r.input_tokens * 0.042 / 1e6)
    assert r.wording_version == jf.WORDING_VERSION
    assert jobs.topic_version == topics["Jobs"].version


def test_review_band(topics):
    f, _ = judge_with(topics, {"Jobs/membership": 0.5})
    r = f.judge("hi")
    assert r["Jobs"].outcome == "review"
    assert r["Jobs"].reasons == ("membership_uncertain",)
    assert r.review == [r["Jobs"]]


def test_required_field_missing_sends_to_review(topics):
    f, _ = judge_with(topics, {"Jobs/membership": 0.9, "Jobs/fields/company": "none of these"})
    r = f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    assert r["Jobs"].outcome == "review"
    assert "field_missing:company" in r["Jobs"].reasons


def test_no_candidates_warns_and_counts_as_missing(topics):
    f, fake = judge_with(topics, {"Jobs/membership": 0.9})
    r = f.judge(EMAIL)
    assert "Jobs/fields/company" not in fake.calls[0][1]
    assert any("Jobs/fields/company: no candidates" in w for w in r.warnings)
    assert "field_missing:company" in r["Jobs"].reasons


def test_low_confidence_category(topics):
    f, _ = judge_with(
        topics,
        {
            "Jobs/membership": 0.9,
            "Jobs/categories": {"applied": 0.4, "recruiter": 0.35, "interview": 0.25},
            "Jobs/fields/company": "Acme",
        },
    )
    r = f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    assert r["Jobs"].reasons == ("low_confidence:category",)


def test_when_category_drops_inapplicable_facets(topics):
    f, _ = judge_with(
        topics,
        {"Jobs/membership": 0.9, "Jobs/categories": "applied", "Jobs/fields/company": "Acme"},
    )
    r = f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    assert "urgency" not in r["Jobs"].scores  # only for recruiter / interview
    assert r["Jobs"].composites == {}


def test_facets_dropped_when_topic_not_matched(topics):
    f, _ = judge_with(topics, {"Jobs/membership": 0.1, "Jobs/categories": "interview"})
    jobs = f.judge(EMAIL)["Jobs"]
    assert jobs.category is None and jobs.fields == {} and jobs.flags == {}


def test_always_facet_kept_when_not_matched():
    t = jf.Topic(
        name="T", description="d", flags={"urgent": "It's urgent"}, when={"urgent": "always"}
    )
    f, fake = judge_with(t, {"T/membership": 0.0, "T/flags/urgent": 0.9})
    r = f.judge("x")
    assert r["T"].flags == {"urgent": 0.9}
    instructions = fake.calls[0][1]["T/flags/urgent"].instructions
    assert "premise" not in instructions


def test_speculative_questions_state_their_premise(topics):
    f, fake = judge_with(topics, {})
    f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    q = fake.calls[0][1]["Jobs/categories"]
    assert q.instructions["premise"].startswith("Assume `content` belongs")
    assert q.instructions["topic"]["name"] == "Jobs"
    assert q.criteria["applied"] == "Confirms I submitted an application."


def test_topic_threshold_overrides():
    t = jf.Topic(name="T", description="d", thresholds={"accept": 0.95})
    f, _ = judge_with(t, {"T/membership": 0.9})
    assert f.judge("x")["T"].outcome == "review"


def test_custom_policy(topics):
    class AlwaysMatch:
        def decide(self, topic, answers):
            return jf.Decision("match")

    f, _ = judge_with(topics, {}, policy=AlwaysMatch())
    assert all(t.outcome == "match" for t in f.judge("x"))


def test_on_error_raise(topics):
    f = jf.Filter(topics, judge=FakeJudge(fail=RuntimeError("down")))
    with pytest.raises(jf.JudgeError, match="down"):
        f.judge("x")


def test_on_error_review(topics):
    f = jf.Filter(topics, judge=FakeJudge(fail=RuntimeError("down")), on_error="review")
    r = f.judge("x")
    assert r.degraded
    assert all(t.outcome == "review" and t.reasons == ("backend_error",) for t in r)
    assert any("down" in w for w in r.warnings)


def test_on_error_fallback_judge(topics):
    fallback = FakeJudge({"Receipts/membership": 0.9}, model="fallback")
    f = jf.Filter(topics, judge=FakeJudge(fail=RuntimeError("down")), on_error=fallback)
    r = f.judge("x")
    assert r.degraded and r.model == "fallback"
    assert r["Receipts"].outcome == "match"


def test_result_round_trips(topics):
    f, _ = judge_with(
        topics,
        {
            "Jobs/membership": 0.95,
            "Jobs/categories": "interview",
            "Jobs/fields/company": "Acme",
            "Jobs/scores/urgency": 2.5,
        },
        include_requests=True,
    )
    r = f.judge(jf.Content(EMAIL, candidates=CANDIDATES))
    import json

    d = json.loads(json.dumps(r.to_dict()))
    again = jf.Result.from_dict(d)
    assert again == r
    assert again["Jobs"].scores["urgency"].normalized == pytest.approx(2.5 / 3)
    assert again.requests[0]["questions"]["Jobs/membership"]["type"] == "noul"
    assert again.raw["Jobs/membership"] == {"type": "noul", "p": 0.95}


def test_explain_sends_nothing(topics):
    fake = FakeJudge()
    plan = jf.Filter(topics, judge=fake).explain(jf.Content(EMAIL, candidates=CANDIDATES))
    assert fake.calls == []
    assert len(plan.requests) == 1
    assert "Jobs/fields/company" in plan.requests[0]["questions"]
    assert plan.estimated_input_tokens > 0 and plan.cost_usd > 0


def test_context_is_sent_with_content(topics):
    f, fake = judge_with(topics, {})
    f.judge(jf.Content("hi", context={"reader": "a job seeker"}))
    assert fake.calls[0][0] == {"content": "hi", "context": {"reader": "a job seeker"}}


def test_custom_facet_end_to_end():
    from jevfilter.facets import NoulFacet
    from jevfilter.registry import unregister_facet

    @jf.facet("pii")
    class PII(NoulFacet):
        instructions = "Does `content` contain personal data such as addresses?"

    try:
        t = jf.Topic.from_dict({"name": "Support", "description": "Support requests", "pii": {}})
        f, fake = judge_with(t, {"Support/membership": 0.9, "Support/pii": 0.7})
        r = f.judge("My address is 1 Main St")
        assert r["Support"].facets == {"pii": 0.7}
        assert fake.calls[0][1]["Support/pii"].instructions["task"].startswith("Does `content`")
        assert jf.Result.from_dict(r.to_dict()) == r
    finally:
        unregister_facet("pii")


def test_nested_categories_not_judged_yet():
    t = jf.Topic(
        name="S",
        description="d",
        categories={"hw": {"description": "x", "children": {"a": "A", "b": "B"}}, "sw": "y"},
    )
    with pytest.raises(jf.JevFilterError, match="nested categories"):
        jf.Filter(t, judge=FakeJudge()).judge("x")
