from types import SimpleNamespace

import pytest

import jevfilter as jf
from jevfilter.judges import ChoiceAnswer, FakeJudge, JevJudge, NoulAnswer, Question, ScoreAnswer


class StubClient:
    """Stands in for TypeSafeClient: records the call, returns SDK-shaped answers."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.calls = []

    def system_one(self, state, questions, model=None):
        self.calls.append((state, questions, model))
        if self.error:
            raise self.error
        return SimpleNamespace(
            answers=self.answers,
            model="jev-1.13.0",
            usage=SimpleNamespace(input_tokens=321),
            request_id="req_1",
        )


def test_jev_judge_converts_questions_and_answers():
    from typesafe_sdk import Choice, Noul, Score

    client = StubClient(
        {
            "n": SimpleNamespace(type="noul", noul=0.9),
            "c": SimpleNamespace(
                type="choice", choice="a", confidence=0.8, probabilities={"a": 0.8, "b": 0.2}
            ),
            "s": SimpleNamespace(
                type="score", score=1.2, confidence=0.7, probabilities={0: 0.1, 1: 0.6, 2: 0.3}
            ),
        }
    )
    judge = JevJudge(model="jev-1.13.0", client=client)
    res = judge.ask(
        {"content": "x"},
        {
            "n": Question("noul", "Is it?", {"true": "yes", "false": "no"}),
            "c": Question("choice", "Which?", {"a": None, "b": "B"}),
            "s": Question("score", "How much?", ["low", "mid", "high"]),
        },
    )
    _, sent, model = client.calls[0]
    assert model == "jev-1.13.0"
    assert (
        isinstance(sent["n"], Noul)
        and isinstance(sent["c"], Choice)
        and isinstance(sent["s"], Score)
    )
    assert res.answers == {
        "n": NoulAnswer(0.9),
        "c": ChoiceAnswer("a", 0.8, {"a": 0.8, "b": 0.2}),
        "s": ScoreAnswer(1.2, 0.7, {0: 0.1, 1: 0.6, 2: 0.3}),
    }
    assert (res.model, res.input_tokens, res.request_id) == ("jev-1.13.0", 321, "req_1")


def test_jev_judge_wraps_sdk_errors():
    from typesafe_sdk import TypeSafeError

    judge = JevJudge(client=StubClient(error=TypeSafeError("boom")))
    with pytest.raises(jf.JudgeError, match="boom"):
        judge.ask("x", {"n": Question("noul", "?")})


def test_jev_judge_missing_answer():
    judge = JevJudge(client=StubClient({}))
    with pytest.raises(jf.JudgeError, match="no answer for: n"):
        judge.ask("x", {"n": Question("noul", "?")})


def test_fake_judge_globs_and_defaults():
    fake = FakeJudge({"*/membership": 0.8}, default_noul=0.1)
    res = fake.ask("x", {"A/membership": Question("noul", "?"), "A/flags/x": Question("noul", "?")})
    assert res.answers["A/membership"].p == 0.8
    assert res.answers["A/flags/x"].p == 0.1


def test_fake_judge_rejects_unknown_choice():
    with pytest.raises(ValueError, match="not among options"):
        FakeJudge({"c": "zzz"}).ask("x", {"c": Question("choice", "?", {"a": None, "b": None})})


def test_fake_score_spreads_between_levels():
    res = FakeJudge({"s": 1.25}).ask("x", {"s": Question("score", "?", ["a", "b", "c"])})
    a = res.answers["s"]
    assert a.score == pytest.approx(1.25) and a.probabilities == {0: 0.0, 1: 0.75, 2: 0.25}


def test_jev_judge_passes_api_key_to_client(monkeypatch):
    import typesafe_sdk

    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", Client)
    assert isinstance(JevJudge(api_key="k-123", timeout=5.0).client, Client)
    assert seen == {"api_key": "k-123", "timeout": 5.0}


def test_jev_judge_client_and_options_are_exclusive():
    with pytest.raises(ValueError):
        JevJudge(api_key="k", client=StubClient())


def test_configure_sets_default_for_helpers_and_filters():
    from jevfilter import defaults

    fake = FakeJudge({"choice": "b", "T/membership": 0.9})
    try:
        jf.configure(judge=fake)
        assert jf.choose("x", ["a", "b"]).value == "b"
        assert jf.Filter(jf.Topic(name="T", description="d")).judge("x")["T"].matched
        jf.configure(api_key="k-123", model="jev-1.13.0")
        assert isinstance(defaults.default_judge(), JevJudge)
        assert defaults.default_judge().model == "jev-1.13.0"
        with pytest.raises(ValueError):
            jf.configure(judge=fake, api_key="k")
    finally:
        defaults.reset()
