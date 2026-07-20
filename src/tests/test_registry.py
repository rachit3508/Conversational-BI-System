"""Tests for the engine registry.

Everything except the SELECT cases is offline -- validation and caching are proved
without touching the server, so a wrong database name is still a clean error when the
instance is unreachable.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.database.registry import (
    POOL_RECYCLE,
    configured_databases,
    default_database,
    dispose_all,
    get_engine,
)
from src.exception.exception import CustomException


@pytest.fixture(scope="module", autouse=True)
def _close_pools():
    """Return the process to a clean state so pools do not leak across modules."""
    yield
    dispose_all()


def test_configured_databases_is_not_empty():
    assert configured_databases()


def test_default_database_is_configured():
    assert default_database() in configured_databases()


def test_get_engine_returns_an_engine():
    assert isinstance(get_engine(), Engine)


def test_engine_is_created_once_and_reused():
    """Acceptance 1: two calls hand back the same instance, so the pool is shared."""
    assert get_engine() is get_engine()


def test_case_insensitive_name_shares_one_engine():
    """SQL Server names are case insensitive; the cache must not split on spelling."""
    name = configured_databases()[0]
    assert get_engine(name.lower()) is get_engine(name.upper())


def test_distinct_databases_get_distinct_engines():
    databases = configured_databases()
    if len(databases) < 2:
        pytest.skip("needs at least two configured databases")

    assert get_engine(databases[0]) is not get_engine(databases[1])


def test_unknown_database_raises_custom_exception():
    """Acceptance 2: a typo is refused up front, not as a driver login failure."""
    with pytest.raises(CustomException) as excinfo:
        get_engine("database_that_does_not_exist")

    message = str(excinfo.value)
    assert "database_that_does_not_exist" in message
    # The message lists the valid choices so the caller can correct the name.
    assert configured_databases()[0] in message


def test_pool_is_configured_to_survive_idle_disconnects():
    pool = get_engine().pool
    assert pool._pre_ping is True
    assert pool._recycle == POOL_RECYCLE


@pytest.mark.parametrize("database", configured_databases())
def test_select_succeeds_against_each_configured_database(database):
    """Acceptance 3: a real SELECT runs, and lands on the database that was asked for."""
    with get_engine(database).connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        assert conn.execute(text("SELECT DB_NAME()")).scalar() == database
