import asyncio
import json

import pytest

import jevfilter as jf
from jevfilter.judges import FakeJudge, Question, RecordingJudge, ReplayJudge, request_key

TOPIC = jf.Topic(name="T", description="d", categories={"a": "A", "b": "B"})
SCRIPT = {"T/membership": 0.9, "T/categories": {"a": 0.2, "b": 0.8}}


def test_record_then_replay_gives_identical_results(tmp_path):
    cassette = tmp_path / "c.jsonl"
    live = FakeJudge(SCRIPT)
    recorded = jf.Filter(TOPIC, judge=RecordingJudge(live, cassette)).judge("hello")
    replayed = jf.Filter(TOPIC, judge=ReplayJudge(cassette)).judge("hello")
    assert replayed == recorded
    assert len(live.calls) == 1


def test_recording_only_pays_for_new_requests(tmp_path):
    cassette = tmp_path / "c.jsonl"
    live = FakeJudge(SCRIPT)
    judge = RecordingJudge(live, cassette)
    f = jf.Filter(TOPIC, judge=judge)
    f.judge("a")
    f.judge("a")
    again = RecordingJudge(live, cassette)
    jf.Filter(TOPIC, judge=again).judge("a")
    jf.Filter(TOPIC, judge=again).judge("b")
    assert len(live.calls) == 2  # "a" once, "b" once
    assert (judge.recorded, judge.replayed, again.recorded, again.replayed) == (1, 1, 1, 1)
    assert len(cassette.read_text().splitlines()) == 2


def test_rerecord_replaces_answers(tmp_path):
    cassette = tmp_path / "c.jsonl"
    jf.Filter(TOPIC, judge=RecordingJudge(FakeJudge({"T/membership": 0.1}), cassette)).judge("x")
    rerecorder = RecordingJudge(FakeJudge({"T/membership": 0.95}), cassette, rerecord=True)
    jf.Filter(TOPIC, judge=rerecorder).judge("x")
    assert jf.Filter(TOPIC, judge=ReplayJudge(cassette)).judge("x")["T"].p == 0.95


def test_replay_misses_are_errors(tmp_path):
    cassette = tmp_path / "c.jsonl"
    jf.Filter(TOPIC, judge=RecordingJudge(FakeJudge(SCRIPT), cassette)).judge("recorded")
    f = jf.Filter(TOPIC, judge=ReplayJudge(cassette))
    with pytest.raises(jf.JudgeError, match="not in c.jsonl"):
        f.judge("never recorded")
    with pytest.raises(FileNotFoundError):
        ReplayJudge(tmp_path / "missing.jsonl")


def test_cassette_is_readable_jsonl(tmp_path):
    cassette = tmp_path / "sub" / "c.jsonl"
    jf.Filter(TOPIC, judge=RecordingJudge(FakeJudge(SCRIPT), cassette)).judge("hello")
    entry = json.loads(cassette.read_text())
    assert entry["state"] == {"content": "hello"}
    assert entry["questions"]["T/membership"]["type"] == "noul"
    assert entry["response"]["answers"]["T/membership"] == {"type": "noul", "p": 0.9}
    assert len(ReplayJudge(cassette)) == 1


def test_bad_cassette_line(tmp_path):
    cassette = tmp_path / "c.jsonl"
    cassette.write_text('{"key": "x"}\n')
    with pytest.raises(ValueError, match="c.jsonl:1"):
        ReplayJudge(cassette)


def test_request_key_is_stable_and_specific():
    q = {"a": Question("noul", "?")}
    assert request_key({"content": "x"}, q) == request_key({"content": "x"}, dict(q))
    assert request_key({"content": "x"}, q) != request_key({"content": "y"}, q)
    assert request_key({"content": "x"}, q) != request_key({"content": "x"}, {"b": q["a"]})


def test_replay_works_in_async_filter(tmp_path):
    cassette = tmp_path / "c.jsonl"
    jf.Filter(TOPIC, judge=RecordingJudge(FakeJudge(SCRIPT), cassette)).judge("x")
    r = asyncio.run(jf.AsyncFilter(TOPIC, judge=ReplayJudge(cassette)).judge("x"))
    assert r["T"].matched


def test_recording_rejects_async_inner(tmp_path):
    class AsyncOnly:
        async def ask(self, state, questions): ...

    with pytest.raises(TypeError, match="sync judge"):
        RecordingJudge(AsyncOnly(), tmp_path / "c.jsonl")
