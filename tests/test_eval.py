import json

import pytest

import jevfilter as jf
from jevfilter.eval import Counts, Example, evaluate, frontier, load_examples
from jevfilter.judges import FakeJudge

TOPICS = [
    jf.Topic(
        name="Jobs",
        description="d",
        categories={"applied": "A", "interview": "I"},
        fields={"company": {}},
    ),
    jf.Topic(name="Receipts", description="r"),
]


# Membership probability and category are scripted from the content text.
def p_for(state, q):
    return float(state["content"].split("p=")[1].split()[0])


EXAMPLES = [
    # (content, expected Jobs?, expected category)
    ("p=0.95 cat=applied", True, "applied"),
    ("p=0.90 cat=interview", True, "interview"),
    ("p=0.80 cat=applied", True, "interview"),  # wrong category
    ("p=0.50 cat=applied", True, "applied"),  # review
    ("p=0.20 cat=applied", True, None),  # missed
    ("p=0.75 cat=applied", False, None),  # false positive
    ("p=0.10 cat=applied", False, None),
    ("p=0.40 cat=applied", False, None),  # review
]


def make_examples():
    out = []
    for text, jobs, cat in EXAMPLES:
        exp = {"Jobs": {"match": jobs}}
        if cat:
            exp["Jobs"]["category"] = cat
        if jobs:
            exp["Jobs"]["fields"] = {"company": "Acme"}
        out.append(
            {
                "content": text,
                "expected": exp,
                "candidates": {"Jobs": {"company": ["Acme", "Other"]}},
            }
        )
    return out


def judge():
    return FakeJudge(
        {
            "Jobs/membership": p_for,
            "Jobs/categories": lambda s, q: s["content"].split("cat=")[1],
            "Jobs/fields/company": "Acme",
            "Receipts/membership": 0.0,
        }
    )


def test_membership_metrics():
    report = evaluate(jf.Filter(TOPICS, judge=judge()), make_examples())
    c = report.topics["Jobs"].membership
    assert (c.tp, c.fp, c.tn, c.fn, c.review_pos, c.review_neg) == (3, 1, 1, 1, 1, 1)
    assert c.precision == pytest.approx(3 / 4)
    assert c.recall == pytest.approx(3 / 5)
    assert c.review_rate == pytest.approx(2 / 8)
    assert c.decided_accuracy == pytest.approx(4 / 6)
    assert report.topics["Receipts"].membership.tn == 8
    assert report.examples == 8 and report.requests == 8 and report.cost_usd > 0


def test_category_confusion_and_field_accuracy():
    report = evaluate(jf.Filter(TOPICS, judge=judge()), make_examples())
    jobs = report.topics["Jobs"]
    # labelled matches that got a category: 3 matched + 1 review (the missed one is "no")
    assert jobs.category_confusion == {
        "applied": {"applied": 2},
        "interview": {"interview": 1, "applied": 1},
    }
    assert jobs.category_accuracy == pytest.approx(3 / 4)
    assert jobs.field_accuracy("company") == pytest.approx(4 / 5)  # the "no" one has no fields


def test_calibration():
    report = evaluate(jf.Filter(TOPICS, judge=judge()), make_examples())
    assert sum(b.count for b in report.calibration) == 16  # 8 examples × 2 topics
    assert report.brier is not None and 0 <= report.brier <= 1
    assert report.ece is not None and 0 <= report.ece <= 1
    top = report.calibration[-1]
    assert top.count == 2 and top.observed == 1.0  # p 0.90, 0.95 both true


def test_sweep_needs_no_new_calls():
    fake = judge()
    report = evaluate(jf.Filter(TOPICS, judge=fake), make_examples(), sweep=True)
    assert len(fake.calls) == 8
    row = next(r for r in report.sweep if r.accept == 0.7 and r.reject == 0.3)
    base = report.overall
    assert row.precision == pytest.approx(base.precision)
    assert row.review_rate == pytest.approx(base.review_rate)
    strict = next(r for r in report.sweep if r.accept == 0.85 and r.reject == 0.85)
    assert strict.review_rate == 0 and strict.precision == 1.0
    assert all(r.reject <= r.accept for r in report.sweep)
    assert frontier(report.sweep)


def test_rescore_from_saved_results():
    f = jf.Filter(TOPICS, judge=judge())
    exs = make_examples()
    first = evaluate(f, exs)
    results = [f.judge(Example.from_dict(e).as_content()) for e in exs]
    again = evaluate(
        jf.Filter(TOPICS, judge=FakeJudge(fail=RuntimeError("no calls"))), exs, results=results
    )
    assert again.to_dict() == first.to_dict()
    with pytest.raises(ValueError, match="line up"):
        evaluate(f, exs, results=results[:2])


def test_report_formats_and_serialises():
    report = evaluate(jf.Filter(TOPICS, judge=judge()), make_examples(), sweep=True)
    text = report.format()
    assert "Jobs" in text and "overall" in text and "threshold sweep" in text and "Brier" in text
    json.dumps(report.to_dict())


def test_load_examples(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in make_examples()) + "\n\n")
    assert len(load_examples(path)) == 8
    path.write_text('{"content": "x", "expected": {"Jobs": {"category": "a"}}}\n')
    with pytest.raises(ValueError, match="data.jsonl:1: .*match"):
        load_examples(path)
    with pytest.raises(ValueError, match="needs `content`"):
        Example.from_dict({"expected": {}})


def test_backend_errors_are_left_out_of_calibration():
    f = jf.Filter(TOPICS, judge=FakeJudge(fail=RuntimeError("down")), on_error="review")
    report = evaluate(f, make_examples(), sweep=True)
    assert report.calibration == [] and report.brier is None
    assert report.overall.review_rate == 1.0


def test_counts_with_no_data():
    c = Counts()
    assert c.precision is None and c.recall is None and c.review_rate is None
