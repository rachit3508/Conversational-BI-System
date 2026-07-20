"""Schema reflection and caching.

The LLM needs to know what tables and columns exist before it can write SQL, but
reflecting a database takes hundreds of milliseconds -- far too slow to repeat on every
chat turn. Each database is therefore reflected once and cached in memory; later turns
read from the cache until something calls :func:`invalidate_schema`.

Two representations are kept deliberately separate, per the design:

* the raw SQLAlchemy ``MetaData`` -- full fidelity, retained for later layers that need
  real ``Table`` objects (query validation, safe query building);
* a compact dict and an even more compact prompt string -- what actually gets shown to
  the LLM, trimmed to the tokens that help it write correct joins.

Note that ``MetaData().reflect(bind=engine)`` on its own only covers the *default*
schema. BikeStores keeps most of its tables in ``production`` and ``sales``, so
reflection here iterates every non-system schema instead.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import MetaData, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import Table

from src.database.registry import canonical_database, get_engine
from src.exception.exception import CustomException
from src.logging.logger import logger

# SQL Server ships these in every database; none of them hold user data.
_SYSTEM_SCHEMAS = {
    "sys",
    "INFORMATION_SCHEMA",
    "guest",
    "db_owner",
    "db_accessadmin",
    "db_securityadmin",
    "db_ddladmin",
    "db_backupoperator",
    "db_datareader",
    "db_datawriter",
    "db_denydatareader",
    "db_denydatawriter",
}


# Created by SSMS to store database diagrams; carries no business data and would only
# tempt the LLM into querying it.
_IGNORED_TABLES = {"sysdiagrams"}


def _type_name(column) -> str:
    """Render a column type compactly, e.g. ``VARCHAR(5)``.

    SQL Server reflection appends ``COLLATE "SQL_Latin1_General_CP1_CI_AS"`` to every
    string column. That is dead weight in the prompt -- roughly fifteen tokens per
    column, re-sent on every chat turn, and it tells the LLM nothing it can act on.
    """
    return str(column.type).split(" COLLATE ")[0]


@dataclass
class _CachedSchema:
    """One database's reflected schema in all three forms."""

    metadata: MetaData
    schema: dict
    prompt: str
    reflected_at: datetime


_cache: dict[str, _CachedSchema] = {}
_lock = threading.Lock()


def _user_schemas(engine) -> list[str]:
    """Return the schemas in the database that can hold user tables."""
    return [
        name
        for name in inspect(engine).get_schema_names()
        if name not in _SYSTEM_SCHEMAS
    ]


def _reflect(database: str) -> MetaData:
    """Reflect every user schema in ``database`` into a single MetaData."""
    engine = get_engine(database)
    metadata = MetaData()

    try:
        for schema in _user_schemas(engine):
            # views=False: only base tables. Views would otherwise appear as tables the
            # LLM believes it can filter and join like any other.
            metadata.reflect(bind=engine, schema=schema, views=False)
    except SQLAlchemyError as e:
        raise CustomException(e) from e

    return metadata


def _describe_table(table: Table) -> dict:
    """Convert a reflected Table into plain JSON-ready data."""
    columns = []
    foreign_keys = []

    for column in table.columns:
        # Reflected columns carry at most one FK each in practice; take the first as the
        # join hint and record every one of them in foreign_keys.
        references = None

        for fk in column.foreign_keys:
            target = fk.target_fullname  # e.g. "sales.customers.customer_id"
            references = references or target
            target_table, _, target_column = target.rpartition(".")
            foreign_keys.append(
                {
                    "column": column.name,
                    "references_table": target_table,
                    "references_column": target_column,
                }
            )

        columns.append(
            {
                "name": column.name,
                "type": _type_name(column),
                "nullable": bool(column.nullable),
                "primary_key": bool(column.primary_key),
                "references": references,
            }
        )

    return {
        "schema": table.schema,
        "name": table.name,
        "qualified_name": f"{table.schema}.{table.name}",
        "primary_key": [c.name for c in table.primary_key.columns],
        "columns": columns,
        "foreign_keys": foreign_keys,
    }


def _describe(database: str, metadata: MetaData, reflected_at: datetime) -> dict:
    """Build the serialisable schema representation from reflected metadata."""
    # Sorted so the dict and the prompt string are stable between runs, which keeps the
    # prompt diffable and friendly to LLM response caching.
    tables = [
        _describe_table(table)
        for table in sorted(metadata.tables.values(), key=lambda t: (t.schema, t.name))
        if table.name not in _IGNORED_TABLES
    ]

    return {
        "database": database,
        "reflected_at": reflected_at.isoformat(),
        "table_count": len(tables),
        "tables": tables,
    }


def _to_prompt(schema: dict) -> str:
    """Render the schema as the compact text block handed to the LLM.

    Kept deliberately terse -- this is re-sent on every chat turn, so each token costs.
    Nullability is marked only when a column *is* nullable, and primary/foreign keys are
    inlined next to the column because join correctness depends on them.
    """
    lines = [f"DATABASE: {schema['database']}", ""]

    for table in schema["tables"]:
        lines.append(table["qualified_name"])

        for column in table["columns"]:
            parts = [f"  {column['name']}", column["type"]]

            if column["primary_key"]:
                parts.append("PK")
            if column["references"]:
                parts.append(f"-> {column['references']}")
            if column["nullable"]:
                parts.append("NULL")

            lines.append(" ".join(parts))

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _load(database: str) -> _CachedSchema:
    """Return the cached entry for ``database``, reflecting it if this is the first ask."""
    target = canonical_database(database)

    # Fast path: a cache hit must not pay for the lock. This is the common case -- every
    # chat turn after the first one.
    cached = _cache.get(target)
    if cached is not None:
        logger.debug("Schema cache hit for database '%s'", target)
        return cached

    with _lock:
        # Re-check: another thread may have reflected it while we waited.
        cached = _cache.get(target)
        if cached is not None:
            return cached

        started = time.perf_counter()
        metadata = _reflect(target)
        reflected_at = datetime.now()

        schema = _describe(target, metadata, reflected_at)
        entry = _CachedSchema(
            metadata=metadata,
            schema=schema,
            prompt=_to_prompt(schema),
            reflected_at=reflected_at,
        )
        _cache[target] = entry

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "Reflected schema for database '%s': %d table(s) in %.0f ms",
        target,
        schema["table_count"],
        elapsed_ms,
    )
    return entry


def get_schema(db_name: str | None = None) -> dict:
    """Return the serialisable schema for ``db_name``, reflecting on first use.

    The returned dict is the cached instance, not a copy -- copying it on every chat turn
    would work against the sub-100ms target. Treat it as read-only.
    """
    return _load(db_name).schema


def get_schema_prompt(db_name: str | None = None) -> str:
    """Return the compact schema text for ``db_name``, for embedding in an LLM prompt."""
    return _load(db_name).prompt


def get_metadata(db_name: str | None = None) -> MetaData:
    """Return the raw reflected ``MetaData``, for callers needing real Table objects."""
    return _load(db_name).metadata


def invalidate_schema(db_name: str) -> None:
    """Drop the cached schema for ``db_name`` so the next call re-reflects.

    Invalidating a database that was never cached is a no-op rather than an error -- the
    caller's intent (make sure the next read is fresh) is satisfied either way.
    """
    target = canonical_database(db_name)

    with _lock:
        if _cache.pop(target, None) is not None:
            logger.info("Invalidated cached schema for database '%s'", target)


def invalidate_all() -> None:
    """Clear every cached schema. For admin refresh and test teardown."""
    with _lock:
        count = len(_cache)
        _cache.clear()

    logger.info("Invalidated %d cached schema(s)", count)
