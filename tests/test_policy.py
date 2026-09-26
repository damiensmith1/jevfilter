import pytest

import jevfilter as jf
from jevfilter.policy import TopicAnswers

T = jf.Topic(
    name="T",
    description="d",
    fields={"company": {"required": True}, "role": {}},
)
POLICY = jf.ThresholdPolicy()


def decide(p, topic=T, **kw):
    return POLICY.decide(topic, TopicAnswers(p=p, **kw))


@pytest.mark.parametrize(
    "p, outcome, reasons",
    [
        (1.0, "match", ()),
        (0.7, "match", ()),  # accept is inclusive
        (0.69, "review", ("membership_uncertain",)),
        (0.3, "review", ("membership_uncertain",)),  # reject is exclusive
        (0.29, "no", ("not_member",)),
        (0.0, "no", ("not_member",)),
    ],
)
def test_membership_bands(p, outcome, reasons):
    assert decide(p, topic=jf.Topic(name="T", description="d")) == jf.Decision(outcome, reasons)


def test_min_confidence_is_inclusive():
    ok = decide(0.9, category=jf.Choice("a", 0.5), fields={"company": jf.Field("Acme", 0.9)})
    assert ok.outcome == "match"
    low = decide(0.9, category=jf.Choice("a", 0.49), fields={"company": jf.Field("Acme", 0.9)})
    assert low.reasons == ("low_confidence:category",)


def test_every_reason_is_reported():
    d = decide(
        0.9,
        category=jf.Choice("a", 0.2),
        fields={"company": jf.Field(None, 0.9), "role": jf.Field("Engineer", 0.1)},
    )
    assert d.outcome == "review"
    assert d.reasons == ("low_confidence:category", "field_missing:company", "low_confidence:role")


def test_optional_field_may_be_missing():
    d = decide(0.9, fields={"company": jf.Field("Acme", 0.9), "role": jf.Field(None, 0.99)})
    assert d.outcome == "match"


def test_confidence_not_checked_unless_matched():
    d = decide(0.5, category=jf.Choice("a", 0.1), fields={"company": jf.Field(None, 0)})
    assert d.reasons == ("membership_uncertain",)


def test_topic_overrides_each_threshold():
    strict = jf.Topic(
        name="S",
        description="d",
        thresholds={"accept": 0.9, "reject": 0.6, "min_confidence": 0.8},
    )
    assert decide(0.85, topic=strict).outcome == "review"
    assert decide(0.55, topic=strict).outcome == "no"
    assert decide(0.95, topic=strict, category=jf.Choice("a", 0.75)).outcome == "review"


def test_policy_defaults_are_configurable():
    lenient = jf.ThresholdPolicy(accept=0.5, reject=0.1, min_confidence=0.2)
    assert (
        lenient.decide(jf.Topic(name="L", description="d"), TopicAnswers(p=0.5)).outcome == "match"
    )


@pytest.mark.parametrize("kw", [{"accept": 0.2, "reject": 0.5}, {"accept": 1.5}, {"reject": -0.1}])
def test_invalid_policy(kw):
    with pytest.raises(ValueError):
        jf.ThresholdPolicy(**kw)
