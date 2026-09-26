"""Typed errors raised by jevfilter."""

from __future__ import annotations


class JevFilterError(Exception):
    """Base class for every jevfilter error."""


class TopicError(JevFilterError, ValueError):
    """A topic definition is invalid. `problems` lists every issue found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems) if len(problems) > 1 else problems[0])


class JudgeError(JevFilterError):
    """The judge backend failed to answer."""
