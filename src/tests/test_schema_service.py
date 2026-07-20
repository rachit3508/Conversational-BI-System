"""Tests for schema reflection and caching.

Reflection is checked against live INFORMATION_SCHEMA counts rather than hardcoded
numbers, so the assertions cannot quietly drift as the databases change.
"""

import time

import pytest
from sqlalchemy import text

from src.database.registry import dispose_all, get_engine
from src.database.schema_service import (
    get_schema,
    get_schema_prompt,
    invalidate_all,
    invalidate_schema,
)
from src.exception.exception import CustomException

DEFAULT_DB = "MedicareSales2"
# BikeStores is the interesting one: three schemas and every foreign key on the instance.
MULTI_SCHEMA_DB = "BikeStores"


@pytest.fixture(scope="module", autouse=True)
def _clean_up():
    yield
    invalidate_all()
    dispose_all()


def _count(database: str, sql: str) -> int:
    with get_engine(database).connect() as conn:
        return conn.execute(text(sql)).scalar()


def test_first_call_reflects_and_second_call_is_cached():
    """Acceptance 1: the cached lookup is far under the 100ms budget."""
    invalidate_schema(DEFAULT_DB)

    first = get_schema(DEFAULT_DB)

    started = time.perf_counter()
    second = get_schema(DEFAULT_DB)
    cached_ms = (time.perf_counter() - started) * 1000

    assert second is first, "second call should serve the cached object, not re-reflect"
    assert cached_ms < 100, f"cached lookup took {cached_ms:.1f}ms"


def test_invalidate_forces_re_reflection():
    """Acceptance 3: after invalidation the next call rebuilds from the database."""
    before = get_schema(DEFAULT_DB)

    invalidate_schema(DEFAULT_DB)
    after = get_schema(DEFAULT_DB)

    assert after is not before
    assert after["reflected_at"] > before["reflected_at"]
    # Same database, so the content should be identical -- only the freshness differs.
    assert after["tables"] == before["tables"]


def test_invalidating_an_uncached_database_is_a_no_op():
    invalidate_schema("TEST")
    invalidate_schema("TEST")


def test_schema_lists_every_table_and_column():
    """Acceptance 2, checked against the live catalogue rather than fixed numbers."""
    for database in (DEFAULT_DB, MULTI_SCHEMA_DB):
        schema = get_schema(database)

        # sysdiagrams is SSMS bookkeeping and is deliberately not reflected, so the
        # live counts have to exclude it too.
        expected_tables = _count(
            database,
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME <> 'sysdiagrams'",
        )
        expected_columns = _count(
            database,
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS c "
            "JOIN INFORMATION_SCHEMA.TABLES t "
            "  ON t.TABLE_NAME = c.TABLE_NAME AND t.TABLE_SCHEMA = c.TABLE_SCHEMA "
            "WHERE t.TABLE_TYPE = 'BASE TABLE' AND t.TABLE_NAME <> 'sysdiagrams'",
        )
        reflected_columns = sum(len(t["columns"]) for t in schema["tables"])

        assert schema["table_count"] == expected_tables
        assert reflected_columns == expected_columns


def test_every_column_carries_a_data_type():
    for table in get_schema(MULTI_SCHEMA_DB)["tables"]:
        for column in table["columns"]:
            assert column["type"], f"{table['qualified_name']}.{column['name']}"


def test_multi_schema_database_is_fully_reflected():
    """Guards the default-schema trap: a plain reflect() would return only dbo."""
    schemas = {table["schema"] for table in get_schema(MULTI_SCHEMA_DB)["tables"]}
    assert {"production", "sales"} <= schemas


def test_foreign_key_hints_are_captured():
    tables = {t["qualified_name"]: t for t in get_schema(MULTI_SCHEMA_DB)["tables"]}
    orders = tables["sales.orders"]

    targets = {
        (fk["column"], f"{fk['references_table']}.{fk['references_column']}")
        for fk in orders["foreign_keys"]
    }
    assert ("customer_id", "sales.customers.customer_id") in targets


def test_primary_keys_are_captured():
    tables = {t["qualified_name"]: t for t in get_schema(MULTI_SCHEMA_DB)["tables"]}
    assert tables["sales.customers"]["primary_key"] == ["customer_id"]


def test_views_are_excluded():
    """rachit has 8 views; none of them should look like a table to the LLM."""
    views = {
        row.casefold()
        for row in _view_names("rachit")
    }
    reflected = {t["name"].casefold() for t in get_schema("rachit")["tables"]}
    assert not views & reflected


def _view_names(database: str) -> list[str]:
    with get_engine(database).connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                text("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS")
            )
        ]


def test_schema_is_json_serialisable():
    """The representation must survive a round trip -- no SQLAlchemy objects in it."""
    import json

    restored = json.loads(json.dumps(get_schema(MULTI_SCHEMA_DB)))
    assert restored == get_schema(MULTI_SCHEMA_DB)


def test_prompt_string_contains_every_table_and_column():
    schema = get_schema(MULTI_SCHEMA_DB)
    prompt = get_schema_prompt(MULTI_SCHEMA_DB)

    assert schema["database"] in prompt

    for table in schema["tables"]:
        assert table["qualified_name"] in prompt
        for column in table["columns"]:
            assert column["name"] in prompt


def test_prompt_string_is_compact_and_carries_join_hints():
    prompt = get_schema_prompt(MULTI_SCHEMA_DB)

    assert "-> sales.customers.customer_id" in prompt  # an FK arrow
    assert " PK" in prompt
    # No repr noise from the reflected objects leaking into the prompt.
    assert "Column(" not in prompt
    assert "MetaData" not in prompt
    # Collation is stripped -- it is pure token cost on every turn.
    assert "COLLATE" not in prompt
    # Comfortably smaller than full DDL would be for a 10-table database.
    assert len(prompt) < 8000


def test_case_insensitive_name_hits_one_cache_entry():
    assert get_schema(MULTI_SCHEMA_DB.lower()) is get_schema(MULTI_SCHEMA_DB.upper())


def test_unknown_database_raises_custom_exception():
    """Refused by the registry before any reflection is attempted."""
    with pytest.raises(CustomException):
        get_schema("database_that_does_not_exist")
