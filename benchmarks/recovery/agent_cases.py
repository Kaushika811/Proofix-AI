"""Small, explicit defects for local agent-path evaluation.

The two numeric cases are newly seeded fixtures, not independent real-world
repositories. They broaden the behavior shapes without implying model quality.
"""

from dataclasses import dataclass
from pathlib import Path

from benchmarks.generator.benchmark import AUTH_TEST

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AgentCase:
    name: str
    source_path: str
    source: str
    correct_source: str
    incorrect_source: str
    test: str
    description: str
    expected_behavior: str
    prior_generator_fixture: bool = False

    @property
    def issue(self) -> dict[str, str]:
        return {
            "description": self.description,
            "target_test": "tests/test_reported_issue.py::test_reported_issue",
            "expected_behavior": self.expected_behavior,
            "target_file": self.source_path,
        }


def cases() -> tuple[AgentCase, ...]:
    auth = (ROOT / "vulnapi/vulnapi/auth.py").read_text(encoding="utf-8")
    expiry_bug = '    # Missing: if payload.get("exp", 0) < time.time(): return False\n    return True'
    expiry_fix = '    if payload.get("exp", 0) < time.time():\n        return False\n    return True'
    if auth.count(expiry_bug) != 1:
        raise ValueError("The reviewed expiry source changed; re-review the benchmark case")
    return (
        AgentCase(
            name="expired_token",
            source_path="vulnapi/auth.py",
            source=auth,
            correct_source=auth.replace("import json\n", "import json\nimport time\n").replace(expiry_bug, expiry_fix),
            incorrect_source=auth.replace("    return True\n", "    return False\n", 1),
            test=AUTH_TEST,
            description="Expired tokens are accepted. Reject expired tokens and retain valid tokens.",
            expected_behavior="An expired token returns False and an unexpired token returns True.",
            prior_generator_fixture=True,
        ),
        AgentCase(
            name="pagination_off_by_one",
            source_path="paging.py",
            source=(
                "def page_items(items: list[int], page: int, size: int) -> list[int]:\n"
                "    if page < 1 or size < 1:\n"
                "        raise ValueError('page and size must be positive')\n"
                "    start = page * size\n"
                "    return items[start:start + size]\n"
            ),
            correct_source=(
                "def page_items(items: list[int], page: int, size: int) -> list[int]:\n"
                "    if page < 1 or size < 1:\n"
                "        raise ValueError('page and size must be positive')\n"
                "    start = (page - 1) * size\n"
                "    return items[start:start + size]\n"
            ),
            incorrect_source=(
                "def page_items(items: list[int], page: int, size: int) -> list[int]:\n"
                "    if page < 1 or size < 1:\n"
                "        raise ValueError('page and size must be positive')\n"
                "    start = 0\n"
                "    return items[start:start + size]\n"
            ),
            test=(
                "from paging import page_items\n\n"
                "def test_reported_issue():\n"
                "    items = [10, 20, 30, 40, 50]\n"
                "    assert page_items(items, 1, 2) == [10, 20]\n"
                "    assert page_items(items, 2, 2) == [30, 40]\n"
            ),
            description="The first page skips the first items because pagination uses a zero-based offset for a one-based page number.",
            expected_behavior="For [10, 20, 30, 40, 50] and size 2, page 1 returns [10, 20] and page 2 returns [30, 40].",
        ),
        AgentCase(
            name="discount_amount",
            source_path="pricing.py",
            source=(
                "def discounted_total(cents: int, percent: int) -> int:\n"
                "    if cents < 0 or not 0 <= percent <= 100:\n"
                "        raise ValueError('invalid discount')\n"
                "    return cents - percent\n"
            ),
            correct_source=(
                "def discounted_total(cents: int, percent: int) -> int:\n"
                "    if cents < 0 or not 0 <= percent <= 100:\n"
                "        raise ValueError('invalid discount')\n"
                "    return cents - cents * percent // 100\n"
            ),
            incorrect_source=(
                "def discounted_total(cents: int, percent: int) -> int:\n"
                "    if cents < 0 or not 0 <= percent <= 100:\n"
                "        raise ValueError('invalid discount')\n"
                "    return 1500\n"
            ),
            test=(
                "from pricing import discounted_total\n\n"
                "def test_reported_issue():\n"
                "    assert discounted_total(2000, 25) == 1500\n"
                "    assert discounted_total(2000, 0) == 2000\n"
            ),
            description="A percentage discount subtracts the percentage as cents instead of calculating its share of the amount.",
            expected_behavior="A 25 percent discount on 2000 cents returns 1500 cents; a zero percent discount returns 2000 cents.",
        ),
    )
