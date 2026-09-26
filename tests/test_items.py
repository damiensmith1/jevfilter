import json

import pytest

import jevfilter as jf
from jevfilter.items import normalize
from jevfilter.judges import FakeJudge

ITEMS = [
    {"id": 3, "fields": {"company": "Acme", "role": "Backend Engineer"}, "status": "applied"},
    {"id": 7, "fields": {"company": "ACME, Inc.", "role": "Data Engineer"}, "status": "contacted"},
    {
        "id": 9,
        "fields": {"company": "Initech", "role": "Analyst"},
        "status": "applied",
        "summary": "Phone screen booked",
    },
]
EMAIL = {"subject": "Next steps for Data Engineer", "body": "..."}


def matcher(script=None, **kw):
    fake = FakeJudge(script or {})
    return jf.Filter(jf.Topic.load("tests/topics"), judge=fake, **kw), fake


def test_prefilter_then_jev_picks_among_survivors():
    f, fake = matcher({"Jobs/item": {"item 7": 0.9, "item 3": 0.05, "new": 0.05}})
    m = f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Acme"})
    assert m.item_id == 7 and m.outcome == "match" and m.asked and not m.is_new
    assert m.candidates == (3, 7)  # Initech filtered out in code
    assert m.probabilities == {"3": 0.05, "7": 0.9, "new": 0.05}
    q = fake.calls[0][1]["Jobs/item"]
    assert list(q.criteria) == ["item 3", "item 7", "new"]
    assert q.criteria["item 7"] == {"fields": ITEMS[1]["fields"], "status": "contacted"}
    assert q.instructions["values_in_content"] == {"company": "Acme"}
    assert m.request_ids == ("fake-1",) and m.cost_usd > 0 and m.topic == "Jobs"


def test_no_survivors_is_new_without_calling_jev():
    f, fake = matcher()
    m = f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Globex"})
    assert m.is_new and m.outcome == "match" and not m.asked and fake.calls == []
    assert f.match_item(EMAIL, "Jobs", [], fields={"company": "Acme"}).is_new


def test_single_survivor_is_still_asked():
    # Same company doesn't mean same role, so one survivor still gets a Jev check.
    f, fake = matcher({"Jobs/item": "new"})
    m = f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Initech"})
    assert m.is_new and m.asked and len(fake.calls) == 1
    assert fake.calls[0][1]["Jobs/item"].criteria["item 9"]["summary"] == "Phone screen booked"


def test_values_from_a_judge_result():
    f, _ = matcher(
        {"Jobs/membership": 0.95, "Jobs/fields/company": "Initech", "Jobs/item": "item 9"}
    )
    r = f.judge(jf.Content(EMAIL, candidates={"Jobs": {"company": ["Initech", "Acme"]}}))
    m = f.match_item(EMAIL, "Jobs", ITEMS, result=r["Jobs"])
    assert m.candidates == (9,) and m.item_id == 9
    with pytest.raises(ValueError, match="not 'Jobs'"):
        f.match_item(EMAIL, "Jobs", ITEMS, result=r["Receipts"])


def test_unknown_content_value_does_not_filter():
    f, _ = matcher({"Jobs/item": "item 9"})
    m = f.match_item(EMAIL, "Jobs", ITEMS)
    assert m.candidates == (3, 7, 9)


def test_low_confidence_goes_to_review():
    f, _ = matcher({"Jobs/item": {"item 3": 0.45, "item 7": 0.4, "new": 0.15}})
    m = f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Acme"})
    assert m.outcome == "review" and m.reasons == ("low_confidence:item",) and m.item_id == 3


def test_backend_error_follows_on_error():
    f, _ = matcher(on_error="review")
    f._backend = FakeJudge(fail=RuntimeError("down"))
    m = f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Acme"})
    assert m.outcome == "review" and m.reasons == ("backend_error",) and m.degraded
    raising = jf.Filter(jf.Topic.load("tests/topics"), judge=FakeJudge(fail=RuntimeError("x")))
    with pytest.raises(jf.JudgeError):
        raising.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Acme"})


def test_budget_applies_to_item_matching():
    budget = jf.Budget(usd=0)
    f, fake = matcher(budget=budget)
    with pytest.raises(jf.BudgetExceeded):
        f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Acme"})
    assert fake.calls == []


def test_items_can_be_objects_and_ids_strings():
    class Job:
        def __init__(self, id, company):
            self.id, self.fields, self.status = id, {"company": company}, None

    f, fake = matcher({"Jobs/item": "item a1"})
    m = f.match_item(
        EMAIL, "Jobs", [Job("a1", "Acme"), Job("b2", "Acme")], fields={"company": "Acme"}
    )
    assert m.item_id == "a1"
    assert fake.calls[0][1]["Jobs/item"].criteria["item a1"] == {"fields": {"company": "Acme"}}


@pytest.mark.parametrize(
    "items, message",
    [
        ([{"fields": {}}], "needs an `id`"),
        ([{"id": 1}, {"id": 1}], "more than once"),
        ([{"id": 1, "fields": ["x"]}], "must be a mapping"),
    ],
)
def test_item_validation(items, message):
    f, _ = matcher()
    with pytest.raises(ValueError, match=message):
        f.match_item(EMAIL, "Jobs", items)


def test_unknown_topic():
    f, _ = matcher()
    with pytest.raises(ValueError, match="unknown topic"):
        f.match_item(EMAIL, "Nope", ITEMS)


def test_item_match_round_trips():
    f, _ = matcher({"Jobs/item": "item 7"})
    m = f.match_item(EMAIL, "Jobs", ITEMS, fields={"company": "Acme"})
    assert jf.ItemMatch.from_dict(json.loads(json.dumps(m.to_dict()))) == m


@pytest.mark.parametrize(
    "a, b",
    [
        ("Acme", "ACME"),
        ("Acme, Inc.", "acme"),
        ("Acme Corp", "acme"),
        ("  Café   Nero ", "cafe nero"),
        ("Initech LLC", "initech"),
    ],
)
def test_normalize(a, b):
    assert normalize(a) == normalize(b)


def test_normalize_keeps_different_names_apart():
    assert normalize("Acme Robotics") != normalize("Acme")
    assert normalize(None) == ""
