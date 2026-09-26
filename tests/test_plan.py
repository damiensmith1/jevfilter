import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge, Question
from jevfilter.plan import Limits, estimate_tokens, plan_requests


def q(size: int) -> Question:
    return Question("noul", "x" * size)


def test_everything_fits_in_one_request():
    requests, warnings = plan_requests({"content": "hi"}, {"a": q(10), "b": q(10)})
    assert len(requests) == 1 and list(requests[0].questions) == ["a", "b"] and warnings == []


def test_questions_split_first_fit_and_state_is_resent():
    limits = Limits(request_tokens=200, state_and_question_tokens=100, safety=1.0)
    questions = {f"q{i}": q(160) for i in range(5)}  # ~45 tokens each
    requests, _ = plan_requests({"content": "hi"}, questions, limits)
    assert len(requests) > 1
    assert [qid for r in requests for qid in r.questions] == list(questions)
    for r in requests:
        assert r.state == {"content": "hi"}
        assert estimate_tokens(r.payload()) <= 200


def test_long_content_is_truncated_with_a_warning():
    limits = Limits(request_tokens=400, state_and_question_tokens=200, safety=1.0)
    state = {"content": {"subject": "Hello", "body": "word " * 2000}}
    requests, warnings = plan_requests(state, {"a": q(20)}, limits)
    assert estimate_tokens(requests[0].state) <= 200 - estimate_tokens({"a": q(20).to_dict()})
    assert requests[0].state["content"]["subject"] == "Hello"  # only the longest string is cut
    assert requests[0].state["content"]["body"].endswith(" …")
    assert "truncated" in warnings[0]


def test_head_tail_keeps_both_ends():
    limits = Limits(request_tokens=400, state_and_question_tokens=200, safety=1.0)
    body = "START " + "middle " * 2000 + " END"
    requests, _ = plan_requests({"content": body}, {"a": q(20)}, limits, truncate="head_tail")
    text = requests[0].state["content"]
    assert text.startswith("START") and text.endswith("END") and " … " in text


def test_truncate_error_and_callable():
    limits = Limits(request_tokens=400, state_and_question_tokens=200, safety=1.0)
    state = {"content": "x" * 5000}
    with pytest.raises(jf.JevFilterError, match="truncate='error'"):
        plan_requests(state, {"a": q(20)}, limits, truncate="error")
    requests, _ = plan_requests(
        state, {"a": q(20)}, limits, truncate=lambda s, n: {"content": "short"}
    )
    assert requests[0].state == {"content": "short"}
    with pytest.raises(jf.JevFilterError, match="still too long"):
        plan_requests(state, {"a": q(20)}, limits, truncate=lambda s, n: s)
    with pytest.raises(ValueError):
        plan_requests(state, {"a": q(20)}, limits, truncate="tail")


def test_a_single_oversized_question_is_an_error():
    limits = Limits(request_tokens=400, state_and_question_tokens=200, safety=1.0)
    with pytest.raises(jf.JevFilterError, match="shorten its definition"):
        plan_requests({"content": "hi"}, {"big": q(2000)}, limits)


def test_limits_validation():
    with pytest.raises(ValueError):
        Limits(safety=0)
    with pytest.raises(ValueError):
        Limits(request_tokens=10, state_and_question_tokens=20)


SPLIT = Limits(request_tokens=600, state_and_question_tokens=300, safety=1.0)


def many_topics(n: int) -> list[jf.Topic]:
    return [
        jf.Topic(name=f"T{i}", description="A fairly long description of what belongs. " * 3)
        for i in range(n)
    ]


def test_filter_splits_and_merges_answers():
    limits = SPLIT
    fake = FakeJudge({"T3/membership": 0.9})
    f = jf.Filter(many_topics(12), judge=fake, limits=limits)
    plan = f.explain("hello")
    assert fake.calls == [] and len(plan.requests) > 1
    r = f.judge("hello")
    assert len(fake.calls) == len(plan.requests)
    assert len(r.request_ids) == len(fake.calls)
    per_request = [FakeJudge().ask(s, qs).input_tokens for s, qs in fake.calls]
    assert r.input_tokens == sum(per_request)
    assert [t.topic for t in r.matches] == ["T3"]
    assert len(r.raw) == 12


def test_explain_reports_truncation():
    limits = Limits(request_tokens=1000, state_and_question_tokens=500, safety=1.0)
    f = jf.Filter(jf.Topic(name="T", description="d"), judge=FakeJudge(), limits=limits)
    plan = f.explain("word " * 5000)
    assert any("truncated" in w for w in plan.warnings)
    r = f.judge("word " * 5000)
    assert any("truncated" in w for w in r.warnings)


def test_different_models_across_requests_warn():
    class Flaky(FakeJudge):
        def ask(self, state, questions):
            res = super().ask(state, questions)
            res.model = f"jev-{len(self.calls)}"
            return res

    limits = SPLIT
    r = jf.Filter(many_topics(12), judge=Flaky(), limits=limits).judge("x")
    assert r.model == "jev-1"
    assert any("different models" in w for w in r.warnings)


def test_failure_in_any_request_applies_on_error():
    class FailSecond(FakeJudge):
        def ask(self, state, questions):
            if len(self.calls) == 1:
                self.calls.append((state, dict(questions)))
                raise jf.JudgeError("second request failed")
            return super().ask(state, questions)

    limits = SPLIT
    r = jf.Filter(many_topics(12), judge=FailSecond(), limits=limits, on_error="review").judge("x")
    assert r.degraded and all(t.outcome == "review" for t in r)
