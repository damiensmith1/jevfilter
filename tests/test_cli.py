import json

import pytest

from jevfilter import cli
from jevfilter.judges import FakeJudge

TOPICS = "tests/topics"


@pytest.fixture
def fake_jev(monkeypatch):
    """Route the CLI's JevJudge to a FakeJudge."""
    fake = FakeJudge(
        {"Jobs/membership": 0.95, "Jobs/categories": "applied", "Jobs/fields/company": "Acme"}
    )

    class FakeJev:
        def __init__(self, model=None, **kw):
            self.model = model

        def ask(self, state, questions):
            return fake.ask(state, questions)

    monkeypatch.setattr("jevfilter.judges.jev.JevJudge", FakeJev)
    return fake


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_try_prints_a_readable_result(capsys, fake_jev):
    code, out, _ = run(
        capsys,
        "try",
        TOPICS,
        "Your application to Acme was received",
        "--candidates",
        '{"Jobs": {"company": ["Acme"]}}',
    )
    assert code == 0
    assert "Jobs: match (p=0.95)" in out and "category: applied" in out and "company: Acme" in out
    assert "Receipts: no" in out and "tokens" in out


def test_try_json_and_file_and_stdin(capsys, fake_jev, tmp_path, monkeypatch):
    email = tmp_path / "email.json"
    email.write_text(json.dumps({"subject": "hi"}))
    code, out, _ = run(capsys, "try", TOPICS, "--file", str(email), "--json")
    assert code == 0 and json.loads(out)["topics"][0]["topic"] == "Jobs"
    assert fake_jev.calls[-1][0] == {"content": {"subject": "hi"}}
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("from stdin"))
    run(capsys, "try", TOPICS, "-")
    assert fake_jev.calls[-1][0] == {"content": "from stdin"}


def test_try_record_then_replay(capsys, fake_jev, tmp_path):
    cassette = tmp_path / "c.jsonl"
    _, recorded, _ = run(capsys, "try", TOPICS, "hello", "--record", str(cassette))
    calls = len(fake_jev.calls)
    code, replayed, _ = run(capsys, "try", TOPICS, "hello", "--replay", str(cassette))
    assert code == 0 and replayed == recorded and len(fake_jev.calls) == calls


def test_try_budget(capsys, fake_jev):
    code, _, err = run(capsys, "try", TOPICS, "hello", "--max-usd", "0")
    assert code == 1 and "spend cap" in err


def test_explain_sends_nothing(capsys):
    code, out, _ = run(capsys, "explain", TOPICS, "hello", "--payloads")
    assert code == 0 and "1 request(s)" in out and "Jobs/membership" in out
    assert '"state"' in out
    code, out, _ = run(capsys, "explain", TOPICS, "hello", "--json")
    assert json.loads(out)["estimated_input_tokens"] > 0


def test_lint(capsys, tmp_path):
    code, out, _ = run(capsys, "lint", TOPICS)
    assert code == 0 and "2 topic(s) OK" in out
    (tmp_path / "bad.yaml").write_text("name: Bad\ncatagories: {a: b}\n")
    code, _, err = run(capsys, "lint", str(tmp_path))
    assert code == 2 and "did you mean `categories`" in err and "description" in err
    (tmp_path / "bad.yaml").write_text("name: One\ndescription: d\ncategories: {a: A}\n")
    code, out, _ = run(capsys, "lint", str(tmp_path), "--json")
    assert code == 0 and "one option" in json.loads(out)["warnings"][0]


def test_eval(capsys, fake_jev, tmp_path):
    data = tmp_path / "data.jsonl"
    data.write_text(
        json.dumps({"content": "a", "expected": {"Jobs": {"match": True, "category": "applied"}}})
        + "\n"
        + json.dumps({"content": "b", "expected": {}})
        + "\n"
    )
    code, out, _ = run(capsys, "eval", TOPICS, str(data), "--sweep")
    assert code == 0 and "2 examples" in out and "threshold sweep" in out
    code, out, _ = run(capsys, "eval", TOPICS, str(data), "--json")
    assert json.loads(out)["topics"]["Jobs"]["category_accuracy"] == 1.0


def test_env_file_does_not_override(capsys, fake_jev, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nexport JF_TEST_A='from file'\nJF_TEST_B=file\n")
    monkeypatch.setenv("JF_TEST_B", "from env")
    monkeypatch.delenv("JF_TEST_A", raising=False)
    run(capsys, "try", TOPICS, "x", "--env-file", str(env))
    import os

    assert os.environ["JF_TEST_A"] == "from file" and os.environ["JF_TEST_B"] == "from env"
    monkeypatch.delenv("JF_TEST_A")


@pytest.mark.parametrize(
    "argv, message",
    [
        (["try", TOPICS], "give text"),
        (["try", TOPICS, "x", "--file", "f"], "not both"),
        (["try", "no/such/dir", "x"], "no topic file"),
    ],
)
def test_usage_errors(capsys, fake_jev, argv, message):
    code, _, err = run(capsys, *argv)
    assert code == 2 and message in err


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "jevfilter" in capsys.readouterr().out
