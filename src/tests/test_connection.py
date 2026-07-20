"""Integration tests for src/database/connection.py.

These hit the real local SQL Server instance rather than mocking it -- the point of
the suite is to prove the machine can actually reach the database, so a failure here
should mean something is genuinely wrong with the connection setup.
"""

import pytest
from sqlalchemy import text

from src.database.connection import build_url, check_connection, list_databases
from src.database.registry import configured_databases, default_database, get_engine
from src.exception.exception import CustomException


def test_default_connection_succeeds():
    """The default database from .env is reachable."""
    assert check_connection() is True


def test_every_configured_database_is_reachable():
    for database in configured_databases():
        assert check_connection(database) is True


def test_connection_targets_the_expected_database():
    """The engine is actually bound to the default, not silently falling back to master."""
    with get_engine().connect() as conn:
        current = conn.execute(text("SELECT DB_NAME()")).scalar()
    assert current == default_database()


def test_list_databases_excludes_system_databases():
    databases = list_databases()
    assert default_database() in databases
    assert not {"master", "tempdb", "model", "msdb"} & set(databases)


def test_configured_databases_all_exist_on_the_server():
    """Catches a stale DB_NAMES entry pointing at a database that was dropped."""
    on_server = {name.casefold() for name in list_databases()}
    missing = [db for db in configured_databases() if db.casefold() not in on_server]
    assert not missing, f"configured but absent from the server: {missing}"


def test_url_carries_the_settings_the_local_instance_needs():
    url = build_url("BikeStores")
    assert url.database == "BikeStores"
    assert url.query["trusted_connection"] == "yes"
    # Driver 18 encrypts by default and a local instance has no trusted certificate.
    assert url.query["TrustServerCertificate"] == "yes"


def test_unknown_database_raises_custom_exception():
    """Driver errors are wrapped, so callers never see a raw pyodbc error."""
    with pytest.raises(CustomException):
        check_connection("database_that_does_not_exist")
