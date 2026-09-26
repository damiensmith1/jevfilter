from pathlib import Path

import pytest

import jevfilter as jf

TOPICS = Path(__file__).parent / "topics"


@pytest.fixture
def topics() -> jf.Topics:
    return jf.Topic.load(TOPICS)


@pytest.fixture
def jobs(topics) -> jf.Topic:
    return topics["Jobs"]
