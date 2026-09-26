import threading

import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_spend_cap_refuses_once_reached():
    b = jf.Budget(usd=0.001)
    b.check()
    assert b.record(20_000) == pytest.approx(20_000 * 0.042 / 1e6)
    b.check()  # $0.00084 < $0.001
    b.record(10_000)
    with pytest.raises(jf.BudgetExceeded, match="spend cap"):
        b.check()
    assert b.remaining_usd == 0.0 and b.requests == 2 and b.input_tokens == 30_000


def test_rate_cap_uses_a_sliding_minute():
    clock = Clock()
    b = jf.Budget(per_minute=2, clock=clock)
    b.check()
    b.check()
    with pytest.raises(jf.BudgetExceeded, match="rate cap"):
        b.check()
    clock.t = 59.9
    with pytest.raises(jf.BudgetExceeded):
        b.check()
    clock.t = 60.0
    b.check()  # the first call has left the window


def test_no_limits_never_refuses():
    b = jf.Budget()
    for _ in range(1000):
        b.check()
        b.record(1_000_000)
    assert b.remaining_usd is None


def test_custom_price():
    assert jf.Budget(price_per_mtok=1.0).record(2_000_000) == 2.0


@pytest.mark.parametrize("kw", [{"usd": -1}, {"per_minute": 0}])
def test_invalid_budget(kw):
    with pytest.raises(ValueError):
        jf.Budget(**kw)


def test_concurrent_checks_respect_rate_cap():
    b = jf.Budget(per_minute=50)
    passed = []

    def worker():
        for _ in range(10):
            try:
                b.check()
                passed.append(1)
            except jf.BudgetExceeded:
                pass

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(passed) == 50


def test_filter_checks_before_sending_and_records_after():
    fake = FakeJudge({"T/membership": 0.9})
    budget = jf.Budget(per_minute=1)
    f = jf.Filter(jf.Topic(name="T", description="d"), judge=fake, budget=budget)
    r = f.judge("x")
    assert budget.requests == 1 and budget.input_tokens == r.input_tokens
    with pytest.raises(jf.BudgetExceeded):
        f.judge("y")
    assert len(fake.calls) == 1  # refused before sending


def test_budget_refusal_is_not_swallowed_by_on_error():
    budget = jf.Budget(usd=0)
    f = jf.Filter(
        jf.Topic(name="T", description="d"), judge=FakeJudge(), budget=budget, on_error="review"
    )
    with pytest.raises(jf.BudgetExceeded):
        f.judge("x")


def test_one_budget_shared_by_two_filters():
    budget = jf.Budget(per_minute=2)
    a = jf.Filter(jf.Topic(name="A", description="a"), judge=FakeJudge(), budget=budget)
    b = jf.Filter(jf.Topic(name="B", description="b"), judge=FakeJudge(), budget=budget)
    a.judge("x")
    b.judge("x")
    with pytest.raises(jf.BudgetExceeded):
        a.judge("x")


def test_filter_uses_budget_price_for_cost():
    budget = jf.Budget(price_per_mtok=1.0)
    r = jf.Filter(jf.Topic(name="T", description="d"), judge=FakeJudge(), budget=budget).judge("x")
    assert r.cost_usd == pytest.approx(r.input_tokens / 1e6)
