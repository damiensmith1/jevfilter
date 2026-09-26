"""Opt-in checks against real Jev. Run with: JEVFILTER_LIVE=1 uv run pytest tests/live -s

Loads TYPESAFE_API_KEY from the repo's .env if it isn't already set (the
library itself never reads .env). Prints spend. Never run in CI.
"""

import os
from pathlib import Path

import pytest

import jevfilter as jf
from jevfilter.judges import JevJudge

pytestmark = pytest.mark.skipif(
    os.environ.get("JEVFILTER_LIVE") != "1", reason="set JEVFILTER_LIVE=1 to call real Jev"
)

TOPICS = Path(__file__).parent.parent / "topics"

APPLIED = {
    "from": "no-reply@greenhouse.example",
    "subject": "Thank you for applying to Acme Robotics",
    "body": "Hi Damien, thanks for applying for the Backend Engineer role at Acme Robotics. "
    "Our team will review your application and be in touch.",
}
INTERVIEW = {
    "from": "sam@initech.example",
    "subject": "Interview for Data Engineer",
    "body": "Hi Damien, we'd love to set up a 30 minute phone screen for the Data Engineer "
    "role at Initech. Could you reply with times that work tomorrow?",
}
DIGEST = {
    "from": "jobs-noreply@jobboard.example",
    "subject": "25 new jobs for you",
    "body": "Recommended for you: Software Engineer at Globex, Data Analyst at Hooli, and more.",
}
RECEIPT = {
    "from": "orders@shop.example",
    "subject": "Your order #10492 has shipped",
    "body": "Thanks for your purchase. 1x USB-C cable, total $12.99.",
}
CANDIDATES = {
    "Jobs": {
        "company": ["Acme Robotics", "Initech", "Globex", "Hooli", "Greenhouse"],
        "role": ["Backend Engineer", "Data Engineer", "Software Engineer", "Data Analyst"],
    }
}


def _load_env() -> None:
    env = Path(__file__).parents[2] / ".env"
    if os.environ.get("TYPESAFE_API_KEY") or not env.exists():
        return
    for line in env.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "TYPESAFE_API_KEY":
            os.environ["TYPESAFE_API_KEY"] = value.strip().strip("\"'")


@pytest.fixture(scope="module")
def live_filter():
    _load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("no TYPESAFE_API_KEY")
    return jf.Filter(jf.Topic.load(TOPICS), judge=JevJudge())


SPEND: list[float] = []


def _judge(f, email):
    r = f.judge(jf.Content(email, candidates=CANDIDATES))
    SPEND.append(r.cost_usd or 0.0)
    for t in r:
        print(f"  {t.topic}: {t.outcome} p={t.p:.3f} {t.reasons}", end="")
        if t.category:
            print(f" category={t.category.value}({t.category.confidence:.2f})", end="")
        if t.fields:
            print(f" fields={ {k: v.value for k, v in t.fields.items()} }", end="")
        print()
    print(f"  model={r.model} tokens={r.input_tokens} cost=${r.cost_usd:.6f}")
    return r


def test_application_confirmation(live_filter):
    r = _judge(live_filter, APPLIED)
    assert r["Jobs"].matched
    assert r["Jobs"].category.value == "applied"
    assert r["Jobs"].fields["company"].value == "Acme Robotics"
    assert r["Receipts"].outcome == "no"


def test_interview_request(live_filter):
    r = _judge(live_filter, INTERVIEW)
    assert r["Jobs"].matched
    assert r["Jobs"].category.value == "interview"
    assert r["Jobs"].fields["company"].value == "Initech"
    assert "urgency" in r["Jobs"].scores
    assert r["Jobs"].flags["needs_reply"] > 0.5


def test_job_alert_digest_is_not_a_job(live_filter):
    r = _judge(live_filter, DIGEST)
    assert r["Jobs"].outcome != "match"


def test_receipt(live_filter):
    r = _judge(live_filter, RECEIPT)
    assert r["Receipts"].matched
    assert r["Jobs"].outcome == "no"


def test_helpers():
    _load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("no TYPESAFE_API_KEY")
    judge = JevJudge()
    c = jf.choose(
        "I was charged twice this month", ["billing", "bug", "feature request"], judge=judge
    )
    flags = jf.check(
        "Can you send the signed form by Friday?",
        {
            "needs_reply": "The sender is asking for a reply or something to be sent",
            "is_spam": "This is unsolicited advertising",
        },
        judge=judge,
    )
    s = jf.rate(
        "The site is down for all customers",
        "severity",
        ["cosmetic", "degraded", "blocking"],
        judge=judge,
    )
    print(f"  choose={c.value}({c.confidence:.2f}) check={flags} rate={s.level}({s.value:.2f})")
    assert c.value == "billing"
    assert flags["needs_reply"] > 0.5 > flags["is_spam"]
    assert s.level == "blocking"


def teardown_module():
    if SPEND:
        print(f"\nLive spend: ${sum(SPEND):.6f} over {len(SPEND)} filter requests")


FOLLOW_UP = {
    "from": "sam@initech.example",
    "subject": "Re: Interview for Data Engineer",
    "body": "Hi Damien, confirming your Data Engineer phone screen tomorrow at 2pm.",
}
TRACKED = [
    {"id": 1, "fields": {"company": "Initech", "role": "Data Engineer"}, "status": "contacted"},
    {"id": 2, "fields": {"company": "Initech", "role": "Office Manager"}, "status": "applied"},
    {
        "id": 3,
        "fields": {"company": "Acme Robotics", "role": "Backend Engineer"},
        "status": "applied",
    },
]


def test_match_item_picks_the_right_role(live_filter):
    m = live_filter.match_item(FOLLOW_UP, "Jobs", TRACKED, fields={"company": "Initech"})
    SPEND.append(m.cost_usd or 0.0)
    print(f"  item={m.item_id} conf={m.confidence:.2f} candidates={m.candidates} ${m.cost_usd:.6f}")
    assert m.candidates == (1, 2)
    assert m.item_id == 1 and m.outcome == "match"


def test_match_item_new_role(live_filter):
    email = {
        **FOLLOW_UP,
        "subject": "Security Engineer role at Initech",
        "body": "Hi Damien, I'm a recruiter at Initech. "
        "Would you be open to our Security Engineer role?",
    }
    m = live_filter.match_item(email, "Jobs", TRACKED, fields={"company": "Initech"})
    SPEND.append(m.cost_usd or 0.0)
    print(f"  item={m.item_id} conf={m.confidence:.2f} probs={m.probabilities}")
    assert m.is_new


def test_async_batch_with_budget():
    import asyncio

    _load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("no TYPESAFE_API_KEY")
    budget = jf.Budget(usd=0.01, per_minute=30)
    f = jf.AsyncFilter(jf.Topic.load(TOPICS), budget=budget)
    emails = [APPLIED, INTERVIEW, DIGEST, RECEIPT]
    results = asyncio.run(f.judge_many([jf.Content(e, candidates=CANDIDATES) for e in emails]))
    SPEND.append(budget.spent_usd)
    outcomes = [{t.topic: t.outcome for t in r} for r in results]
    print(f"  outcomes={outcomes} spent=${budget.spent_usd:.6f}")
    assert [r["Jobs"].outcome for r in results][:2] == ["match", "match"]
    assert results[2]["Jobs"].outcome != "match" and results[3]["Receipts"].matched
    assert budget.requests == 4
