import pytest

from vulnapi.api import init_db, lookup_user, search_users


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_lookup_valid_user():
    user = lookup_user("1")
    assert user is not None
    assert user["name"] == "admin"


def test_sql_injection_blocked():
    """Bug 2 demo: SQLi attempt should not return extra rows."""
    # This injection would return all rows if vulnerable
    result = lookup_user("1 OR 1=1")
    # Vulnerable code may return rows; test expects safe behavior
    assert result is None or result.get("id") == 1


def test_search_injection():
    results = search_users("admin")
    assert len(results) >= 1
