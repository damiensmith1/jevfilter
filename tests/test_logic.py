"""Engine rules beyond the happy path: when, composites, candidates, facets."""

import pytest

import jevfilter as jf
from jevfilter.facets import ChoiceFacet, ScoreFacet
from jevfilter.judges import FakeJudge
from jevfilter.registry import unregister_facet


def run(topic, script, content="x"):
    fake = FakeJudge(script)
    return jf.Filter(topic, judge=fake).judge(content), fake


def test_composite_is_weighted_sum_of_normalized_scores():
    t = jf.Topic(
        name="T",
        description="d",
        scores={
            "urgency": {"levels": ["low", "mid", "high"]},
            "value": {"levels": ["low", "high"]},
        },
        composites={"priority": {"urgency": 0.5, "value": 2}},
    )
    r, _ = run(t, {"T/membership": 0.9, "T/scores/urgency": 1, "T/scores/value": 1})
    assert r["T"].composites["priority"] == pytest.approx(0.5 * 0.5 + 2 * 1.0)


def test_composite_skipped_when_a_score_is_dropped():
    t = jf.Topic(
        name="T",
        description="d",
        categories={"a": "A", "b": "B"},
        scores={"s": {"levels": ["l", "h"]}, "u": {"levels": ["l", "h"]}},
        composites={"c": {"s": 1, "u": 1}},
        when={"u": {"category": "b"}},
    )
    r, _ = run(t, {"T/membership": 0.9, "T/categories": "a"})
    assert set(r["T"].scores) == {"s"} and r["T"].composites == {}


def test_facet_level_when_applies_to_every_item():
    t = jf.Topic(
        name="T",
        description="d",
        flags={"x": "X", "y": "Y"},
        when={"flags": "always", "y": {"matched": True}},
    )
    r, fake = run(t, {"T/membership": 0.0, "T/flags/x": 0.8, "T/flags/y": 0.8})
    assert r["T"].flags == {"x": 0.8}  # item-level `when` beats facet-level
    assert "premise" not in fake.calls[0][1]["T/flags/x"].instructions
    assert "premise" in fake.calls[0][1]["T/flags/y"].instructions


def test_when_category_on_a_field():
    t = jf.Topic(
        name="T",
        description="d",
        categories={"a": "A", "b": "B"},
        fields={"company": {"required": True}},
        when={"company": {"category": ["b"]}},
    )
    cands = jf.Content("x", candidates={"T": {"company": ["Acme"]}})
    r, _ = run(t, {"T/membership": 0.9, "T/categories": "a"}, cands)
    # dropped, so a missing required field doesn't force review
    assert r["T"].fields == {} and r["T"].outcome == "match"
    r, _ = run(t, {"T/membership": 0.9, "T/categories": "b", "T/fields/company": "Acme"}, cands)
    assert r["T"].fields["company"].value == "Acme"


def test_review_keeps_facets_for_a_person_to_see():
    t = jf.Topic(name="T", description="d", categories={"a": "A", "b": "B"})
    r, _ = run(t, {"T/membership": 0.5, "T/categories": "b"})
    assert r["T"].outcome == "review" and r["T"].category.value == "b"


def test_candidate_named_none_of_these_is_ignored():
    t = jf.Topic(name="T", description="d", fields={"f": {}})
    _, fake = run(t, {}, jf.Content("x", candidates={"T": {"f": ["A", "none of these"]}}))
    assert list(fake.calls[0][1]["T/fields/f"].criteria) == ["A", "none of these"]


def test_topics_are_judged_independently_in_order():
    topics = [jf.Topic(name=n, description=n) for n in ("B", "A", "C")]
    r, _ = run(topics, {"A/membership": 0.9, "C/membership": 0.5})
    assert [t.topic for t in r] == ["B", "A", "C"]
    assert [t.outcome for t in r] == ["no", "match", "review"]
    assert [t.topic for t in r.matches] == ["A"] and [t.topic for t in r.review] == ["C"]


def test_requests_only_included_on_request():
    r, _ = run(jf.Topic(name="T", description="d"), {})
    assert r.requests is None and "requests" not in r.to_dict()


def test_custom_choice_and_score_facets():
    @jf.facet("tone")
    class Tone(ChoiceFacet):
        instructions = "What tone does `content` have?"
        criteria = {"friendly": None, "hostile": None}

    @jf.facet("effort")
    class Effort(ScoreFacet):
        instructions = "How much effort would replying take?"
        levels = ("none", "some", "lots")

    try:
        t = jf.Topic.from_dict(
            {"name": "T", "description": "d", "tone": {}, "effort": {}, "when": {"tone": "always"}}
        )
        r, fake = run(t, {"T/membership": 0.1, "T/tone": "hostile", "T/effort": 2})
        assert r["T"].facets == {
            "tone": jf.Choice("hostile", 1.0, {"friendly": 0.0, "hostile": 1.0})
        }
        assert "premise" not in fake.calls[0][1]["T/tone"].instructions
        r, _ = run(t, {"T/membership": 0.9, "T/effort": 2})
        assert r["T"].facets["effort"].level == "lots"
        assert jf.Result.from_dict(r.to_dict()) == r
    finally:
        unregister_facet("tone")
        unregister_facet("effort")


def test_unregistering_a_facet_after_loading_is_an_error():
    from jevfilter.facets import NoulFacet

    @jf.facet("tmp")
    class Tmp(NoulFacet):
        instructions = "?"

    t = jf.Topic.from_dict({"name": "T", "description": "d", "tmp": {}})
    unregister_facet("tmp")
    with pytest.raises(jf.JevFilterError, match="no longer registered"):
        run(t, {})


def test_filter_argument_checks():
    with pytest.raises(ValueError):
        jf.Filter([], judge=FakeJudge())
    with pytest.raises(ValueError):
        jf.Filter(jf.Topic(name="T", description="d"), on_error="ignore")


def test_score_result_details():
    t = jf.Topic(name="T", description="d", scores={"u": {"levels": ["low", "mid", "high"]}})
    r, _ = run(t, {"T/membership": 0.9, "T/scores/u": {0: 0.2, 1: 0.7, 2: 0.1}})
    s = r["T"].scores["u"]
    assert s.value == pytest.approx(0.9)
    assert s.level == "mid" and s.confidence == 0.7
    assert s.probabilities == {"low": 0.2, "mid": 0.7, "high": 0.1}
    assert s.normalized == pytest.approx(0.45)


def test_non_text_score_levels_use_indexes():
    t = jf.Topic(
        name="T", description="d", scores={"u": {"levels": [{"level": "low"}, {"level": "high"}]}}
    )
    r, _ = run(t, {"T/membership": 0.9, "T/scores/u": 1})
    assert r["T"].scores["u"].level == "1"
