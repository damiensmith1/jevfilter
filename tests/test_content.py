import jevfilter as jf


def test_state_wraps_content():
    assert jf.Content("hi").as_state() == {"content": "hi"}
    assert jf.Content({"a": 1}, context={"me": "x"}).as_state() == {
        "content": {"a": 1},
        "context": {"me": "x"},
    }


def test_candidates_are_cleaned_and_merged():
    c = jf.Content(
        "x",
        candidates={
            "Jobs": {"company": [" Acme ", "Initech", "", "Acme"]},
            "*": {"company": ["Globex", "Initech"], "role": ["Engineer"]},
        },
    )
    assert c.candidates_for("Jobs", "company") == ["Acme", "Initech", "Globex"]
    assert c.candidates_for("Other", "company") == ["Globex", "Initech"]
    assert c.candidates_for("Jobs", "role") == ["Engineer"]
    assert c.candidates_for("Jobs", "missing") == []
    assert jf.Content("x").candidates_for("Jobs", "company") == []
