"""Integration tests for src/database/connection.py.

These hit the real local SQL Server instance rather than mocking it -- the point of
the suite is to prove the machine can actually reach the database, so a failure here
should mean something is genuinely wrong with the connection setup.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.database.connection import (
    DB_NAME,
    get_engine,
    list_databases,
)
# Aliased: imported as-is, pytest would collect the helper itself as a test case
# and then fail looking for a fixture named "database".
from src.database.connection import test_connection as check_connection
from src.exception.exception import CustomException


def test_default_connection_succeeds():
    """The database named in .env is reachable."""
    assert check_connection() is True


def test_engine_is_returned():
    engine = get_engine()
    assert isinstance(engine, Engine)


def test_engines_are_cached_per_database():
    """Repeated calls reuse one pool instead of opening a new one each time."""
    assert get_engine() is get_engine()
    assert get_engine("BikeStores") is not get_engine()


def test_connection_targets_the_expected_database():
    """The engine is actually bound to DB_NAME, not silently falling back to master."""
    with get_engine().connect() as conn:
        current = conn.execute(text("SELECT DB_NAME()")).scalar()
    assert current == DB_NAME


def test_database_override_reaches_a_different_database():
    with get_engine("BikeStores").connect() as conn:
        current = conn.execute(text("SELECT DB_NAME()")).scalar()
    assert current == "BikeStores"


def test_list_databases_excludes_system_databases():
    databases = list_databases()
    assert DB_NAME in databases
    assert not {"master", "tempdb", "model", "msdb"} & set(databases)


def test_unknown_database_raises_custom_exception():
    """Driver errors are wrapped, so callers never see a raw pyodbc error."""
    with pytest.raises(CustomException):
        check_connection("database_that_does_not_exist")
