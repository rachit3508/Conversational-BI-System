"""Engine registry -- the single owner of every SQLAlchemy Engine in the process.

SQLAlchemy engines are meant to be long lived: each one holds a connection pool, so
creating one per request would defeat pooling entirely. This module creates exactly one
engine per configured database, lazily on first use, and hands the same instance back on
every later call.

Only databases listed in ``DB_NAMES`` can be reached. Anything else is refused here,
before a connection is attempted, so a typo surfaces as a clear ``CustomException``
naming the valid choices rather than a driver-level login failure.
"""

import os
import threading

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.database.connection import build_url
from src.exception.exception import CustomException
from src.logging.logger import logger

load_dotenv()

# Pool sizing. Defaults suit a handful of concurrent BI users against one instance;
# raise DB_POOL_SIZE if the frontend starts holding connections for long queries.
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
# Retire connections after 30 minutes. SQL Server and any firewall in between will drop
# an idle TCP connection without telling us; pre_ping catches the ones already dead,
# recycle avoids handing them out in the first place.
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

# Keyed by the canonical spelling from DB_NAMES, guarded by _lock: a Streamlit or web
# frontend calls get_engine from multiple threads, and two racing misses would otherwise
# build two pools for the same database.
_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def configured_databases() -> list[str]:
    """Return the databases the user is allowed to pick, in configured order."""
    raw = os.getenv("DB_NAMES", "")
    databases = [name.strip() for name in raw.split(",") if name.strip()]

    if databases:
        return databases

    # Fall back to the single-database form used before DB_NAMES existed.
    fallback = os.getenv("DB_DEFAULT") or os.getenv("DB_NAME")

    if not fallback:
        raise CustomException(
            "No databases configured. Set DB_NAMES in .env (see .env.example)."
        )

    return [fallback.strip()]


def default_database() -> str:
    """Return the database used when a caller does not name one."""
    databases = configured_databases()
    configured = os.getenv("DB_DEFAULT") or os.getenv("DB_NAME")

    if not configured:
        return databases[0]

    return _canonical(configured.strip(), databases)


def _canonical(database: str, databases: list[str] | None = None) -> str:
    """Resolve ``database`` to its configured spelling, or raise if it is not configured.

    SQL Server database names are case insensitive under the default collation, so
    "bikestores" and "BikeStores" must resolve to the same cache entry rather than
    building two pools against the same database.
    """
    databases = configured_databases() if databases is None else databases

    for name in databases:
        if name.casefold() == database.casefold():
            return name

    raise CustomException(
        f"Unknown database '{database}'. Configured: {', '.join(databases)}."
    )


def get_engine(database: str | None = None) -> Engine:
    """Return the pooled engine for ``database``, creating it on first use.

    Defaults to :func:`default_database`. Raises ``CustomException`` for any database
    not listed in ``DB_NAMES``.
    """
    target = _canonical(database) if database else default_database()

    # Fast path: no lock needed for a hit, dict reads are atomic under the GIL.
    engine = _engines.get(target)
    if engine is not None:
        return engine

    with _lock:
        # Re-check: another thread may have created it while we waited for the lock.
        engine = _engines.get(target)
        if engine is not None:
            return engine

        try:
            # build_url embeds connection details, so it is never logged.
            engine = create_engine(
                build_url(target),
                pool_pre_ping=True,
                pool_recycle=POOL_RECYCLE,
                pool_size=POOL_SIZE,
                max_overflow=MAX_OVERFLOW,
                pool_timeout=POOL_TIMEOUT,
            )
        except SQLAlchemyError as e:
            raise CustomException(e) from e

        _engines[target] = engine

    logger.info("Created engine for database '%s'", target)
    return engine


def dispose_all() -> None:
    """Close every pooled connection and empty the cache. For shutdown and teardown."""
    with _lock:
        for name, engine in _engines.items():
            engine.dispose()
            logger.info("Disposed engine for database '%s'", name)

        _engines.clear()
