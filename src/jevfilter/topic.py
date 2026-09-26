"""Topic definitions: validation, loading, round-tripping and versioning.

The schema is documented in docs/topic-format.md. A `Topic` keeps the exact
data it was built from (so `to_dict` / `to_yaml` round-trip it unchanged) and
exposes validated, typed views of each section.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union

from . import registry
from .errors import TopicError

FACET_LISTS = ("fields", "scores", "flags", "composites")
THRESHOLD_KEYS = ("accept", "reject", "min_confidence")
TRACK_KEYS = ("match_on", "statuses", "terminal", "stale_after_days")


@dataclass(frozen=True)
class Category:
    name: str
    description: Any
    children: dict[str, Category] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass(frozen=True)
class FieldSpec:
    name: str
    about: str | None = None
    kind: str | None = None
    required: bool = False


@dataclass(frozen=True)
class ScoreSpec:
    name: str
    about: str | None
    levels: tuple[Any, ...]

    def label(self, level: int) -> str:
        value = self.levels[level]
        return value if isinstance(value, str) else str(level)


@dataclass(frozen=True)
class Track:
    match_on: tuple[str, ...] = ()
    statuses: dict[str, tuple[str, ...]] = field(default_factory=dict)
    terminal: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stale_after_days: float | None = None


@dataclass(frozen=True)
class When:
    """When a facet's answer is used. `matched` is the default."""

    mode: Literal["always", "matched", "category"] = "matched"
    categories: tuple[str, ...] = ()


TopicSource = Union[str, Path, Mapping[str, Any], "Topic", Iterable[Any]]


class Topic:
    """A named, plain-English definition content can belong to.

    ```python
    Topic(name="Receipts", description="Receipts and order confirmations")
    Topic.from_dict({...})
    Topic.load("topics/")      # → Topics
    ```
    """

    def __init__(self, name: str | None = None, description: Any = None, **rest: Any):
        data = {"name": name, "description": description, **rest}
        self._init({k: v for k, v in data.items() if v is not None})

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Topic:
        """Validate a plain dict (e.g. from a form or a parsed file)."""
        if not isinstance(data, Mapping):
            raise TopicError([f"a topic must be a mapping, got {type(data).__name__}"])
        t = cls.__new__(cls)
        t._init(dict(data))
        return t

    @staticmethod
    def load(source: TopicSource) -> Topics:
        """Load topics from a directory, a file, a dict, a Topic, or a list of these."""
        return Topics(_load(source))

    def _init(self, data: dict[str, Any]) -> None:
        self._data = copy.deepcopy(data)
        v = _Validator(self._data)
        self.name: str = v.name
        self.description: Any = self._data.get("description")
        self.exclude: Any = self._data.get("exclude")
        self.examples: dict[str, list[str]] = v.examples
        self.categories: dict[str, Category] = v.categories
        self.fields: dict[str, FieldSpec] = v.fields
        self.scores: dict[str, ScoreSpec] = v.scores
        self.composites: dict[str, dict[str, float]] = v.composites
        self.flags: dict[str, Any] = v.flags
        self.track: Track | None = v.track
        self.thresholds: dict[str, float] = v.thresholds
        self.when: dict[str, When] = v.when
        self.custom: dict[str, Any] = v.custom
        self.meta: Any = self._data.get("meta")
        self.warnings: tuple[str, ...] = tuple(v.warnings)
        v.raise_if_invalid()

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The definition exactly as given (a deep copy)."""
        return copy.deepcopy(self._data)

    def to_json(self, path: str | Path | None = None, **kwargs: Any) -> str:
        text = json.dumps(self._data, indent=2, ensure_ascii=False, **kwargs) + "\n"
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def to_yaml(self, path: str | Path | None = None) -> str:
        """Serialise to YAML (needs `jevfilter[yaml]`); write it if `path` is given."""
        yaml = _yaml()
        text = yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True, width=88)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    @property
    def version(self) -> str:
        """SHA-256 of the canonical JSON of every key except `meta`."""
        judged = {k: v for k, v in self._data.items() if k != "meta"}
        canonical = json.dumps(judged, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # -- helpers ------------------------------------------------------------

    def category(self, path: str) -> Category | None:
        """Look up a category by name or `parent/child` path."""
        node: Category | None = None
        level = self.categories
        for part in path.split("/"):
            node = level.get(part)
            if node is None:
                return None
            level = node.children
        return node

    def when_for(self, facet: str, item: str | None = None) -> When:
        if item is not None and item in self.when:
            return self.when[item]
        return self.when.get(facet, When())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Topic) and self._data == other._data

    def __hash__(self) -> int:
        return hash(self.version)

    def __repr__(self) -> str:
        return f"Topic(name={self.name!r})"


class Topics(Mapping[str, Topic]):
    """An ordered set of uniquely named topics."""

    def __init__(self, topics: Iterable[Topic] = ()):
        self._by_name: dict[str, Topic] = {}
        for t in topics:
            if t.name in self._by_name:
                raise TopicError([f"Topic {t.name!r} is defined more than once"])
            self._by_name[t.name] = t

    def __getitem__(self, name: str) -> Topic:
        return self._by_name[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    def __repr__(self) -> str:
        return f"Topics({list(self._by_name)})"


def as_topics(source: TopicSource | Topics) -> Topics:
    return source if isinstance(source, Topics) else Topic.load(source)


# -- loading ----------------------------------------------------------------


def _load(source: Any) -> list[Topic]:
    if isinstance(source, Topic):
        return [source]
    if isinstance(source, Topics):
        return list(source.values())
    if isinstance(source, Mapping):
        if "topics" in source and "name" not in source:
            return _load(source["topics"])
        return [Topic.from_dict(source)]
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            files = sorted(
                p for p in path.iterdir() if p.suffix in (".yaml", ".yml", ".json") and p.is_file()
            )
            return [t for p in files for t in _load_file(p)]
        return _load_file(path)
    if isinstance(source, Iterable):
        return [t for item in source for t in _load(item)]
    raise TypeError(f"can't load topics from {type(source).__name__}")


def _load_file(path: Path) -> list[Topic]:
    if not path.exists():
        raise FileNotFoundError(f"no topic file or directory at {path}")
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else _yaml().safe_load(text)
    try:
        return _load(data)
    except TopicError as e:
        raise TopicError([f"{path.name}: {p}" for p in e.problems]) from None


def _yaml() -> Any:
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "YAML support needs PyYAML: pip install 'jevfilter[yaml]' (or use JSON)"
        ) from None
    return yaml


# -- validation -------------------------------------------------------------


class _Validator:
    """Parses a topic dict, collecting every problem before raising."""

    def __init__(self, data: dict[str, Any]):
        self.d = data
        self.problems: list[str] = []
        self.warnings: list[str] = []
        raw_name = data.get("name")
        self.name = raw_name if isinstance(raw_name, str) else "?"
        self.examples: dict[str, list[str]] = {}
        self.categories: dict[str, Category] = {}
        self.fields: dict[str, FieldSpec] = {}
        self.scores: dict[str, ScoreSpec] = {}
        self.composites: dict[str, dict[str, float]] = {}
        self.flags: dict[str, Any] = {}
        self.track: Track | None = None
        self.thresholds: dict[str, float] = {}
        self.when: dict[str, When] = {}
        self.custom: dict[str, Any] = {}
        self._run()

    def err(self, msg: str) -> None:
        self.problems.append(f"Topic {self.name!r}: {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(f"Topic {self.name!r}: {msg}")

    def raise_if_invalid(self) -> None:
        if self.problems:
            raise TopicError(self.problems)

    def _run(self) -> None:
        d = self.d
        self._name()
        if _blank(d.get("description")):
            self.err("`description` is required: say in plain English what belongs")
        elif not isinstance(d["description"], (str, Mapping)):
            self.err("`description` must be text or a mapping")
        if "exclude" in d and not (_is_text(d["exclude"]) or _is_text_list(d["exclude"])):
            self.err("`exclude` must be text or a list of text")
        self._examples()
        self._categories()
        self._fields()
        self._scores()
        self._flags()
        self._composites()
        self._unique_names()
        self._thresholds()
        self._track()
        self._custom()
        self._when()

    def _name(self) -> None:
        name = self.d.get("name")
        if not isinstance(name, str) or not name.strip():
            self.err("`name` is required and must be text")
        elif "/" in name:
            self.err("`name` can't contain '/' (it's used in question IDs)")

    def _examples(self) -> None:
        ex = self.d.get("examples")
        if ex is None:
            return
        if not isinstance(ex, Mapping) or set(ex) - {"match", "no"}:
            self.err("`examples` must be a mapping with `match` and/or `no` lists")
            return
        for key, items in ex.items():
            if not _is_text_list(items):
                self.err(f"`examples.{key}` must be a list of text")
            else:
                self.examples[key] = list(items)

    def _categories(self) -> None:
        cats = self.d.get("categories")
        if cats is None:
            return
        self.categories = self._category_level(cats, "categories")
        if len(self.categories) == 1:
            self.warn("`categories` has one option, so the answer is always that option")

    def _category_level(self, level: Any, where: str) -> dict[str, Category]:
        if not isinstance(level, Mapping) or not level:
            self.err(f"`{where}` must be a non-empty mapping of name → description")
            return {}
        out: dict[str, Category] = {}
        seen: dict[str, str] = {}
        for name, value in level.items():
            if not _valid_item_name(name):
                self.err(f"`{where}` has an invalid name {name!r} (text, no '/')")
                continue
            if isinstance(value, Mapping) and "children" in value:
                extra = set(value) - {"description", "children"}
                if extra:
                    self.err(f"`{where}.{name}` has unknown keys {sorted(extra)}")
                desc = value.get("description")
                children = self._category_level(value["children"], f"{where}.{name}.children")
                out[name] = Category(name, desc, children)
            else:
                desc = value.get("description") if isinstance(value, Mapping) else value
                out[name] = Category(name, desc)
            if isinstance(desc, str):
                if desc in seen:
                    self.warn(f"`{where}.{name}` has the same description as `{seen[desc]}`")
                seen[desc] = name
        return out

    def _fields(self) -> None:
        for name, value in self._named("fields").items():
            if value is None:
                value = {}
            if not isinstance(value, Mapping):
                self.err(f"`fields.{name}` must be a mapping like {{kind: org, about: ...}}")
                continue
            extra = set(value) - {"kind", "about", "required"}
            if extra:
                self.err(f"`fields.{name}` has unknown keys {sorted(extra)}")
            required = value.get("required", False)
            if not isinstance(required, bool):
                self.err(f"`fields.{name}.required` must be true or false")
            for key in ("kind", "about"):
                if key in value and not _is_text(value[key]):
                    self.err(f"`fields.{name}.{key}` must be text")
            self.fields[name] = FieldSpec(
                name, value.get("about"), value.get("kind"), required is True
            )

    def _scores(self) -> None:
        for name, value in self._named("scores").items():
            if not isinstance(value, Mapping) or "levels" not in value:
                self.err(f"`scores.{name}` must be a mapping with `levels` (low → high)")
                continue
            extra = set(value) - {"about", "levels"}
            if extra:
                self.err(f"`scores.{name}` has unknown keys {sorted(extra)}")
            levels = value["levels"]
            if not isinstance(levels, list) or len(levels) < 2:
                self.err(f"`scores.{name}.levels` must list at least two levels, low → high")
                continue
            self.scores[name] = ScoreSpec(name, value.get("about"), tuple(levels))

    def _flags(self) -> None:
        for name, value in self._named("flags").items():
            if _blank(value):
                self.err(f"`flags.{name}` needs a yes/no condition in plain English")
            else:
                self.flags[name] = value

    def _composites(self) -> None:
        for name, weights in self._named("composites").items():
            if not isinstance(weights, Mapping) or not weights:
                self.err(f"`composites.{name}` must map score names to weights")
                continue
            ok = True
            for score, w in weights.items():
                if score not in self.scores:
                    self.err(f"`composites.{name}` uses unknown score {score!r}")
                    ok = False
                elif isinstance(w, bool) or not isinstance(w, (int, float)):
                    self.err(f"`composites.{name}.{score}` weight must be a number")
                    ok = False
            if ok:
                self.composites[name] = {k: float(v) for k, v in weights.items()}

    def _named(self, key: str) -> Mapping[str, Any]:
        value = self.d.get(key)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            self.err(f"`{key}` must be a mapping of name → definition")
            return {}
        ok = {}
        for name, item in value.items():
            if _valid_item_name(name):
                ok[name] = item
            else:
                self.err(f"`{key}` has an invalid name {name!r} (text, no '/')")
        return ok

    def _unique_names(self) -> None:
        owner: dict[str, str] = {}
        for key in FACET_LISTS:
            for name in self._named_quiet(key):
                if name in owner:
                    self.err(
                        f"{name!r} is used in both `{owner[name]}` and `{key}`; "
                        "names must be unique within a topic"
                    )
                elif name in registry.RESERVED_KEYS:
                    self.err(f"`{key}.{name}` uses a reserved name")
                owner[name] = key

    def _named_quiet(self, key: str) -> list[str]:
        value = self.d.get(key)
        return list(value) if isinstance(value, Mapping) else []

    def _thresholds(self) -> None:
        th = self.d.get("thresholds")
        if th is None:
            return
        if not isinstance(th, Mapping):
            self.err("`thresholds` must be a mapping")
            return
        for key, value in th.items():
            if key not in THRESHOLD_KEYS:
                self.err(f"`thresholds.{key}` is unknown; use {', '.join(THRESHOLD_KEYS)}")
            elif (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                self.err(f"`thresholds.{key}` must be a number from 0 to 1")
            else:
                self.thresholds[key] = float(value)
        if self.thresholds.get("reject", 0) > self.thresholds.get("accept", 1):
            self.err("`thresholds.reject` can't be above `thresholds.accept`")

    def _track(self) -> None:
        tr = self.d.get("track")
        if tr is None:
            return
        if not isinstance(tr, Mapping):
            self.err("`track` must be a mapping")
            return
        for key in set(tr) - set(TRACK_KEYS):
            self.err(f"`track.{key}` is unknown; use {', '.join(TRACK_KEYS)}")
        match_on = tr.get("match_on", [])
        if not _is_text_list(match_on):
            self.err("`track.match_on` must be a list of field names")
            match_on = []
        for f in match_on:
            if f not in self.fields:
                self.err(f"`track.match_on` names unknown field {f!r}")
        statuses = self._status_map(tr.get("statuses", {}), "statuses")
        terminal = self._status_map(tr.get("terminal", {}), "terminal")
        for s in set(statuses) & set(terminal):
            self.err(f"status {s!r} is in both `track.statuses` and `track.terminal`")
        stale = tr.get("stale_after_days")
        if stale is not None and (
            isinstance(stale, bool) or not isinstance(stale, (int, float)) or stale <= 0
        ):
            self.err("`track.stale_after_days` must be a positive number")
            stale = None
        self.track = Track(tuple(match_on), statuses, terminal, stale)

    def _status_map(self, value: Any, key: str) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, Mapping):
            self.err(f"`track.{key}` must map status → list of categories")
            return {}
        out = {}
        for status, cats in value.items():
            if isinstance(cats, str):
                cats = [cats]
            if not _is_text_list(cats):
                self.err(f"`track.{key}.{status}` must be a list of categories")
                continue
            for c in cats:
                if self.categories and _find_category(self.categories, c) is None:
                    self.err(f"`track.{key}.{status}` names unknown category {c!r}")
            out[status] = tuple(cats)
        if out and not self.categories:
            self.err(f"`track.{key}` needs `categories` to move items between statuses")
        return out

    def _custom(self) -> None:
        known = set(registry.RESERVED_KEYS) - {"membership"}
        for key, value in self.d.items():
            if key in known:
                continue
            if registry.get_facet(key) is not None:
                self.custom[key] = value
                continue
            options = sorted(known | set(registry.facet_names()))
            close = difflib.get_close_matches(key, options, n=1)
            hint = f" (did you mean `{close[0]}`?)" if close else ""
            self.err(f"unknown key `{key}`{hint}; custom facets must be registered first")

    def _when(self) -> None:
        wh = self.d.get("when")
        if wh is None:
            return
        if not isinstance(wh, Mapping):
            self.err("`when` must map facet names to conditions")
            return
        targets = {"categories", *self.fields, *self.scores, *self.flags, *self.custom}
        targets |= {k for k in FACET_LISTS[:3] if k in self.d}
        for name, cond in wh.items():
            if name not in targets:
                self.err(f"`when.{name}` doesn't name a facet of this topic")
                continue
            parsed = self._condition(name, cond)
            if parsed is not None:
                self.when[name] = parsed

    def _condition(self, name: str, cond: Any) -> When | None:
        if cond == "always":
            return When("always")
        if isinstance(cond, Mapping) and len(cond) == 1:
            if "matched" in cond and isinstance(cond["matched"], bool):
                return When("matched") if cond["matched"] else When("always")
            if "category" in cond:
                cats = [cond["category"]] if isinstance(cond["category"], str) else cond["category"]
                if not _is_text_list(cats) or not cats:
                    self.err(f"`when.{name}.category` must be a category or list of categories")
                    return None
                if not self.categories:
                    self.err(f"`when.{name}` uses categories but the topic has none")
                    return None
                for c in cats:
                    if _find_category(self.categories, c) is None:
                        self.err(f"`when.{name}` names unknown category {c!r}")
                return When("category", tuple(cats))
        self.err(f"`when.{name}` must be `always`, `{{matched: true}}` or `{{category: [...]}}`")
        return None


def _find_category(cats: Mapping[str, Category], name: str) -> Category | None:
    """Find a category by leaf name or `a/b` path anywhere in the tree."""
    if "/" in name:
        head, _, rest = name.partition("/")
        node = cats.get(head)
        return _find_category(node.children, rest) if node else None
    for c in cats.values():
        if c.name == name:
            return c
        found = _find_category(c.children, name)
        if found:
            return found
    return None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == {}


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_text_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_text(v) for v in value)


def _valid_item_name(name: Any) -> bool:
    return isinstance(name, str) and bool(name.strip()) and "/" not in name
