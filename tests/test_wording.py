"""Golden tests: exact question payloads. A failure here means the model will
see different wording, which needs a WORDING_VERSION bump."""

import jevfilter as jf
from jevfilter import wording

TOPIC = jf.Topic(
    name="Receipts",
    description="Receipts for things I bought.",
    exclude=["Marketing from shops"],
    examples={"match": ["Your order shipped"], "no": ["20% off today"]},
    categories={"order": "An order confirmation.", "refund": "A refund."},
    fields={"merchant": {"about": "The shop."}},
    scores={"amount": {"about": "How much was spent.", "levels": ["Small", "Large"]}},
    flags={"return_window": "Mentions a return deadline."},
)
BLOCK = {
    "name": "Receipts",
    "description": "Receipts for things I bought.",
    "does_not_include": ["Marketing from shops"],
}
PREMISE = "Assume `content` belongs to the topic defined here."


def test_wording_version():
    assert jf.WORDING_VERSION == 1


def test_membership():
    q = wording.membership(TOPIC)
    assert q.to_dict() == {
        "type": "noul",
        "instructions": {
            "task": "Decide whether `content` belongs to the topic defined here.",
            "topic": BLOCK,
            "examples": {"belongs": ["Your order shipped"], "does_not_belong": ["20% off today"]},
        },
        "criteria": {
            "true": "`content` fits the topic's description.",
            "false": "`content` does not fit the topic's description, "
            "or it is something the topic does not include.",
        },
    }


def test_membership_without_optional_parts():
    q = wording.membership(jf.Topic(name="A", description="a"))
    assert q.instructions == {
        "task": "Decide whether `content` belongs to the topic defined here.",
        "topic": {"name": "A", "description": "a"},
    }


def test_categories():
    q = wording.categories(TOPIC, TOPIC.categories, speculative=True)
    assert q.to_dict() == {
        "type": "choice",
        "instructions": {
            "premise": PREMISE,
            "task": "Which category best describes `content`?",
            "topic": BLOCK,
        },
        "criteria": {"order": "An order confirmation.", "refund": "A refund."},
    }


def test_field_lists_candidates_then_none():
    q = wording.field(TOPIC, TOPIC.fields["merchant"], ["Shop A", "Shop B"], speculative=True)
    assert q.instructions == {
        "premise": PREMISE,
        "task": "Which option is the merchant that `content` refers to?",
        "field": {"name": "merchant", "about": "The shop."},
        "topic": BLOCK,
    }
    assert list(q.criteria) == ["Shop A", "Shop B", "none of these"]
    assert q.criteria["Shop A"] is None


def test_score():
    q = wording.score(TOPIC, TOPIC.scores["amount"], speculative=False)
    assert q.to_dict() == {
        "type": "score",
        "instructions": {
            "task": "Rate `content` on amount.",
            "dimension": "How much was spent.",
            "topic": BLOCK,
        },
        "criteria": ["Small", "Large"],
    }


def test_flag():
    q = wording.flag(TOPIC, "Mentions a return deadline.", speculative=True)
    assert q.to_dict() == {
        "type": "noul",
        "instructions": {
            "premise": PREMISE,
            "task": "Is this statement about `content` true?",
            "statement": "Mentions a return deadline.",
            "topic": BLOCK,
        },
    }


def test_helper_wording():
    assert wording.choose({"a": None}).instructions == "Which option best describes `content`?"
    assert wording.choose({"a": None}, "Pick one").instructions == "Pick one"
    assert wording.check("It rains").instructions == {
        "task": "Is this statement about `content` true?",
        "statement": "It rains",
    }
    assert wording.rate("severity", ["a", "b"]).to_dict() == {
        "type": "score",
        "instructions": "Rate `content` on severity.",
        "criteria": ["a", "b"],
    }
