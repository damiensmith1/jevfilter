import json

import pytest

import jevfilter as jf
from jevfilter.topic import When


def test_minimal_topic():
    t = jf.Topic(name="Receipts", description="Receipts for things I bought")
    assert t.name == "Receipts"
    assert t.categories == {} and t.fields == {}
    assert t.to_dict() == {"name": "Receipts", "description": "Receipts for things I bought"}


def test_load_directory_by_glob(topics):
    assert list(topics) == ["Jobs", "Receipts"]


def test_parsed_views(jobs):
    assert list(jobs.categories) == ["applied", "recruiter", "interview", "rejection"]
    assert jobs.fields["company"].required is True
    assert jobs.fields["role"].kind == "title"
    assert jobs.scores["urgency"].levels[-1] == "Today"
    assert jobs.track.match_on == ("company",)
    assert jobs.track.terminal == {"rejected": ("rejection",)}
    assert jobs.when["urgency"] == When("category", ("recruiter", "interview"))
    assert jobs.when_for("fields", "company") == When()


def test_round_trip_is_unchanged(jobs, tmp_path):
    again = jf.Topic.from_dict(jobs.to_dict())
    assert again == jobs and again.version == jobs.version
    path = tmp_path / "jobs.yaml"
    jobs.to_yaml(path)
    assert jf.Topic.load(path)["Jobs"].to_dict() == jobs.to_dict()
    assert json.loads(jobs.to_json()) == jobs.to_dict()


def test_version_ignores_meta_only(jobs):
    d = jobs.to_dict()
    d["meta"] = {"gmail_label": "Something else"}
    assert jf.Topic.from_dict(d).version == jobs.version
    d["description"] = "Different"
    assert jf.Topic.from_dict(d).version != jobs.version
    assert len(jobs.version) == 64


def test_file_with_topics_list(tmp_path):
    (tmp_path / "all.json").write_text(
        json.dumps(
            {"topics": [{"name": "A", "description": "a"}, {"name": "B", "description": "b"}]}
        )
    )
    assert list(jf.Topic.load(tmp_path)) == ["A", "B"]


def test_duplicate_names_rejected():
    with pytest.raises(jf.TopicError, match="more than once"):
        jf.Topic.load([{"name": "A", "description": "a"}, {"name": "A", "description": "b"}])


def test_typo_key_suggests_fix():
    with pytest.raises(jf.TopicError, match="did you mean `categories`"):
        jf.Topic.from_dict({"name": "A", "description": "a", "catagories": {"x": "y"}})


def test_all_problems_reported_at_once():
    with pytest.raises(jf.TopicError) as e:
        jf.Topic.from_dict(
            {
                "name": "Bad",
                "fields": {"company": {"required": "yes"}},
                "scores": {"urgency": {"levels": ["only one"]}},
                "thresholds": {"accept": 2},
            }
        )
    problems = e.value.problems
    assert any("`description` is required" in p for p in problems)
    assert any("required` must be true or false" in p for p in problems)
    assert any("at least two levels" in p for p in problems)
    assert any("thresholds.accept" in p for p in problems)
    assert all(p.startswith("Topic 'Bad':") for p in problems)


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"name": "a/b"}, "can't contain '/'"),
        ({"composites": {"p": {"missing": 1}}}, "unknown score"),
        ({"track": {"match_on": ["company"]}}, "unknown field"),
        (
            {"categories": {"a": "A", "b": "B"}, "track": {"statuses": {"x": ["c"]}}},
            "unknown category",
        ),
        ({"when": {"nothing": "always"}}, "doesn't name a facet"),
        ({"when": {"flags": "sometimes"}, "flags": {"x": "y"}}, "must be `always`"),
        ({"flags": {"x": "X"}, "scores": {"x": {"levels": ["a", "b"]}}}, "names must be unique"),
        ({"thresholds": {"accept": 0.2, "reject": 0.5}}, "can't be above"),
        ({"examples": {"yes": ["a"]}}, "`examples` must be"),
    ],
)
def test_validation_errors(extra, message):
    with pytest.raises(jf.TopicError, match=message):
        jf.Topic.from_dict({"name": "T", "description": "d", **extra})


def test_warnings_for_likely_mistakes():
    t = jf.Topic(name="T", description="d", categories={"only": "Only one"})
    assert any("one option" in w for w in t.warnings)
    t = jf.Topic(name="T", description="d", categories={"a": "Same", "b": "Same"})
    assert any("same description" in w for w in t.warnings)


def test_nested_categories_parse(tmp_path):
    t = jf.Topic(
        name="Support",
        description="Support requests",
        categories={
            "hardware": {
                "description": "Devices",
                "children": {"laptop": "Laptops", "phone": "Phones"},
            },
            "software": "Apps",
        },
    )
    assert t.category("hardware/laptop").description == "Laptops"
    assert t.categories["software"].is_leaf


def test_custom_facet_key_allowed_once_registered():
    from jevfilter.facets import NoulFacet
    from jevfilter.registry import unregister_facet

    with pytest.raises(jf.TopicError, match="unknown key `pii`"):
        jf.Topic.from_dict({"name": "S", "description": "d", "pii": {}})

    @jf.facet("pii")
    class PII(NoulFacet):
        instructions = "Does `content` contain personal data?"

    try:
        t = jf.Topic.from_dict({"name": "S", "description": "d", "pii": {}})
        assert t.custom == {"pii": {}}
    finally:
        unregister_facet("pii")


def test_reserved_facet_names():
    with pytest.raises(ValueError):
        jf.facet("categories")


@pytest.mark.parametrize(
    "data, message",
    [
        ({"name": ""}, "`name` is required"),
        ({"name": "T", "description": "   "}, "`description` is required"),
        ({"name": "T", "description": 3}, "must be text or a mapping"),
        ({"exclude": 5}, "`exclude` must be text"),
        ({"examples": {"match": "one"}}, "`examples.match` must be a list"),
        ({"categories": {}}, "non-empty mapping"),
        ({"categories": {"a/b": "x", "c": "y"}}, "invalid name"),
        (
            {"categories": {"a": {"description": "x", "children": {"b": "B"}, "x": 1}}},
            "unknown keys",
        ),
        ({"fields": {"f": "text"}}, "`fields.f` must be a mapping"),
        ({"fields": {"f": {"size": 1}}}, "unknown keys"),
        ({"fields": {"f": {"kind": 3}}}, "`fields.f.kind` must be text"),
        ({"fields": ["f"]}, "`fields` must be a mapping"),
        ({"scores": {"s": ["a", "b"]}}, "must be a mapping with `levels`"),
        ({"scores": {"s": {"levels": ["a", "b"], "x": 1}}}, "unknown keys"),
        ({"flags": {"f": ""}}, "needs a yes/no condition"),
        ({"flags": {"categories": "X"}}, "reserved name"),
        ({"composites": {"c": {}}}, "must map score names"),
        (
            {"scores": {"s": {"levels": ["a", "b"]}}, "composites": {"c": {"s": "high"}}},
            "weight must be a number",
        ),
        ({"thresholds": [0.5]}, "`thresholds` must be a mapping"),
        ({"thresholds": {"accept": True}}, "number from 0 to 1"),
        ({"thresholds": {"maybe": 0.5}}, "`thresholds.maybe` is unknown"),
        ({"track": []}, "`track` must be a mapping"),
        ({"track": {"stages": {}}}, "`track.stages` is unknown"),
        ({"track": {"match_on": "company"}}, "list of field names"),
        ({"track": {"statuses": {"a": ["x"]}}}, "needs `categories`"),
        ({"track": {"statuses": []}}, "must map status"),
        (
            {"categories": {"a": "A", "b": "B"}, "track": {"statuses": {"s": [1]}}},
            "list of categories",
        ),
        (
            {
                "categories": {"a": "A", "b": "B"},
                "track": {"statuses": {"s": "a"}, "terminal": {"s": "b"}},
            },
            "in both",
        ),
        ({"track": {"stale_after_days": 0}}, "positive number"),
        ({"when": []}, "`when` must map"),
        ({"when": {"categories": {"category": "x"}}}, "topic has none"),
        (
            {"categories": {"a": "A", "b": "B"}, "when": {"categories": {"category": []}}},
            "must be a category",
        ),
        (
            {"categories": {"a": "A", "b": "B"}, "when": {"categories": {"category": "z"}}},
            "unknown category",
        ),
    ],
)
def test_more_validation_errors(data, message):
    with pytest.raises(jf.TopicError, match=message):
        jf.Topic.from_dict({"name": "T", "description": "d", **data})


def test_valid_track_and_when_variants():
    t = jf.Topic(
        name="T",
        description="d",
        categories={
            "hw": {"description": "x", "children": {"laptop": "L", "phone": "P"}},
            "sw": "S",
        },
        track={"statuses": {"open": "hw/laptop"}, "terminal": {"done": ["sw"]}},
        when={"categories": {"matched": False}},
    )
    assert t.track.statuses == {"open": ("hw/laptop",)}
    assert t.when_for("categories").mode == "always"
    assert t.category("hw/missing") is None and t.category("nope") is None
    assert repr(t) == "Topic(name='T')" and hash(t) == hash(jf.Topic.from_dict(t.to_dict()))


def test_loading_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        jf.Topic.load(tmp_path / "missing.yaml")
    with pytest.raises(TypeError):
        jf.Topic.load(42)
    with pytest.raises(jf.TopicError, match="must be a mapping"):
        jf.Topic.from_dict(["not", "a", "dict"])
    (tmp_path / "bad.yaml").write_text("name: Bad\n")
    with pytest.raises(jf.TopicError, match=r"^bad.yaml: Topic 'Bad'"):
        jf.Topic.load(tmp_path)


def test_load_accepts_topics_and_mixed_lists(topics):
    extra = jf.Topic(name="Extra", description="e")
    assert list(jf.Topic.load([topics, extra, {"name": "D", "description": "d"}])) == [
        "Jobs",
        "Receipts",
        "Extra",
        "D",
    ]
    assert repr(jf.Topic.load(extra)) == "Topics(['Extra'])"
