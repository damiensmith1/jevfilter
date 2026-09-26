import asyncio
import time

import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge, Question, Response
from jevfilter.plan import Limits


class AsyncFake:
    """An async judge that records how many calls overlap."""

    def __init__(self, delay=0.02, script=None):
        self.inner = FakeJudge(script or {})
        self.delay = delay
        self.active = 0
        self.peak = 0

    async def ask(self, state, questions) -> Response:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        return self.inner.ask(state, questions)


def topic():
    return jf.Topic(name="T", description="d")


def test_async_judge_matches_sync():
    script = {"T/membership": 0.9}
    sync = jf.Filter(topic(), judge=FakeJudge(script)).judge("x")
    result = asyncio.run(jf.AsyncFilter(topic(), judge=AsyncFake(script=script)).judge("x"))
    assert result.topics == sync.topics


def test_judge_many_keeps_order_and_limits_concurrency():
    judge = AsyncFake(
        script={"T/membership": lambda state, q: 0.9 if "yes" in state["content"] else 0.1}
    )
    f = jf.AsyncFilter(topic(), judge=judge)
    contents = [f"{'yes' if i % 2 else 'no'} {i}" for i in range(20)]
    results = asyncio.run(f.judge_many(contents, concurrency=4))
    assert [r["T"].outcome for r in results] == ["no" if i % 2 == 0 else "match" for i in range(20)]
    assert judge.peak == 4


def test_judge_many_runs_in_parallel():
    f = jf.AsyncFilter(topic(), judge=AsyncFake(delay=0.05))
    start = time.perf_counter()
    asyncio.run(f.judge_many(["x"] * 10, concurrency=10))
    assert time.perf_counter() - start < 0.3  # 10 × 50ms sequentially would be 0.5s


def test_sync_judge_runs_in_a_thread():
    results = asyncio.run(
        jf.AsyncFilter(topic(), judge=FakeJudge({"T/membership": 0.9})).judge_many(["a", "b"])
    )
    assert all(r["T"].matched for r in results)


def test_budget_refusals_in_a_batch():
    f = jf.AsyncFilter(topic(), judge=AsyncFake(), budget=jf.Budget(per_minute=3))
    out = asyncio.run(f.judge_many(["x"] * 5, concurrency=1, return_exceptions=True))
    assert sum(isinstance(o, jf.Result) for o in out) == 3
    assert sum(isinstance(o, jf.BudgetExceeded) for o in out) == 2
    with pytest.raises(jf.BudgetExceeded):
        asyncio.run(f.judge_many(["x"]))


def test_split_requests_are_sent_concurrently():
    topics = [jf.Topic(name=f"T{i}", description="A long description. " * 8) for i in range(10)]
    judge = AsyncFake(delay=0.02)
    limits = Limits(request_tokens=600, state_and_question_tokens=300, safety=1.0)
    r = asyncio.run(jf.AsyncFilter(topics, judge=judge, limits=limits).judge("x"))
    assert len(r.request_ids) > 1 and judge.peak == len(r.request_ids)


def test_async_on_error_and_fallback():
    class Down:
        async def ask(self, state, questions):
            raise jf.JudgeError("down")

    review = asyncio.run(jf.AsyncFilter(topic(), judge=Down(), on_error="review").judge("x"))
    assert review.degraded and review["T"].reasons == ("backend_error",)
    fb = jf.AsyncFilter(topic(), judge=Down(), on_error=FakeJudge({"T/membership": 0.9}))
    r = asyncio.run(fb.judge("x"))
    assert r.degraded and r["T"].matched
    with pytest.raises(jf.JudgeError):
        asyncio.run(jf.AsyncFilter(topic(), judge=Down()).judge("x"))


def test_async_match_item():
    f = jf.AsyncFilter(
        jf.Topic.load("tests/topics"), judge=AsyncFake(script={"Jobs/item": "item 1"})
    )
    items = [{"id": 1, "fields": {"company": "Acme"}}, {"id": 2, "fields": {"company": "Acme"}}]
    m = asyncio.run(f.match_item("x", "Jobs", items, fields={"company": "Acme"}))
    assert m.item_id == 1 and m.asked
    new = asyncio.run(f.match_item("x", "Jobs", items, fields={"company": "Globex"}))
    assert new.is_new and not new.asked


def test_invalid_concurrency():
    with pytest.raises(ValueError):
        asyncio.run(jf.AsyncFilter(topic(), judge=FakeJudge()).judge_many(["x"], concurrency=0))


def test_async_jev_judge_converts(monkeypatch):
    from types import SimpleNamespace

    from jevfilter.judges import AsyncJevJudge

    class Client:
        async def system_one(self, state, questions, model=None):
            return SimpleNamespace(
                answers={"n": SimpleNamespace(type="noul", noul=0.8)},
                model="jev-1.13.0",
                usage=SimpleNamespace(input_tokens=10),
                request_id="req_a",
            )

    judge = AsyncJevJudge(model="jev-1.13.0", client=Client())
    res = asyncio.run(judge.ask("x", {"n": Question("noul", "?")}))
    assert res.answers["n"].p == 0.8 and res.request_id == "req_a"


def test_async_jev_judge_client_per_loop(monkeypatch):
    import typesafe_sdk

    from jevfilter.judges import AsyncJevJudge

    made = []

    class Client:
        def __init__(self, **kw):
            made.append(kw)

    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", Client)
    judge = AsyncJevJudge(api_key="k")

    async def get():
        return judge._client_for_loop(), judge._client_for_loop()

    a1, a2 = asyncio.run(get())
    b1, _ = asyncio.run(get())
    assert a1 is a2 and a1 is not b1 and made == [{"api_key": "k"}, {"api_key": "k"}]


def test_default_async_judge_follows_configure():
    from jevfilter import defaults
    from jevfilter.judges import AsyncJevJudge

    try:
        fake = FakeJudge({"T/membership": 0.9})
        jf.configure(judge=fake)
        assert asyncio.run(jf.AsyncFilter(topic()).judge("x"))["T"].matched
        jf.configure(api_key="k", model="jev-1.13.0")
        judge = defaults.default_async_judge()
        assert isinstance(judge, AsyncJevJudge) and judge.model == "jev-1.13.0"
    finally:
        defaults.reset()
