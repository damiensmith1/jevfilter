"""jevfilter: judge content against plain-English definitions with Jev.

```python
import jevfilter as jf

jf.choose("I was charged twice", ["billing", "bug", "feature request"])

f = jf.Filter(jf.Topic.load("topics/"))
r = f.judge("Your application to Acme was received")
```
"""

from . import track
from .budget import Budget
from .content import Content
from .defaults import configure
from .engine import AsyncFilter, Filter, Plan
from .errors import BudgetExceeded, JevFilterError, JudgeError, TopicError
from .helpers import check, choose, rate
from .plan import Limits
from .policy import Decision, Policy, ThresholdPolicy, TopicAnswers
from .registry import facet
from .result import Choice, Field, ItemMatch, Result, Score, TopicResult
from .topic import Topic, Topics
from .version import __version__
from .wording import WORDING_VERSION

__all__ = [
    "WORDING_VERSION",
    "AsyncFilter",
    "Budget",
    "BudgetExceeded",
    "Choice",
    "Content",
    "Decision",
    "Field",
    "Filter",
    "ItemMatch",
    "JevFilterError",
    "JudgeError",
    "Limits",
    "Plan",
    "Policy",
    "Result",
    "Score",
    "ThresholdPolicy",
    "Topic",
    "TopicAnswers",
    "TopicError",
    "TopicResult",
    "Topics",
    "__version__",
    "check",
    "configure",
    "choose",
    "facet",
    "rate",
    "track",
]
