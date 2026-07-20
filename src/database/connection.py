"""SQL Server connection management.

Owns connection concerns only -- building the URL, creating and caching engines,
and verifying reachability. Query execution belongs in a separate module.

Uses Windows (trusted) authentication against a local instance, so no username or
password is ever handled here. Settings come from ``.env`` via python-dotenv.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import SQLAlchemyError

from src.exception.exception import CustomException
from src.logging.logger import logger

load_dotenv()

DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_TRUST_SERVER_CERTIFICATE = os.getenv("DB_TRUST_SERVER_CERTIFICATE", "yes")

# One engine (and therefore one connection pool) per database name.
_engines: dict[str, Engine] = {}


def _build_url(database: str) -> URL:
    """Build the connection URL for ``database``.

    ``URL.create`` is used rather than string formatting because the instance name
    contains a backslash and the driver name contains spaces -- both need escaping.
    """
    if not DB_SERVER:
        raise CustomException("DB_SERVER is not set. Copy .env.example to .env.")

    return URL.create(
        "mssql+pyodbc",
        host=DB_SERVER,
        database=database,
        query={
            "driver": DB_DRIVER,
            "trusted_connection": "yes",
            # Driver 18 defaults to Encrypt=yes; a local instance has no trusted
            # certificate, so without this every connection fails on SSL.
            "TrustServerCertificate": DB_TRUST_SERVER_CERTIFICATE,
        },
    )


def get_engine(database: str | None = None) -> Engine:
    """Return a pooled engine for ``database``, defaulting to ``DB_NAME`` from .env.

    Engines are cached per database name so repeated calls reuse the same pool.
    """
    target = database or DB_NAME

    if not target:
        raise CustomException("DB_NAME is not set and no database was passed.")

    if target in _engines:
        return _engines[target]

    try:
        # The URL embeds connection details and is never logged.
        engine = create_engine(_build_url(target), pool_pre_ping=True)
    except SQLAlchemyError as e:
        raise CustomException(e) from e

    logger.info("Created engine for database '%s' on server '%s'", target, DB_SERVER)
    _engines[target] = engine
    return engine


def test_connection(database: str | None = None) -> bool:
    """Open a connection and run ``SELECT 1``. Returns True when reachable."""
    target = database or DB_NAME

    try:
        with get_engine(target).connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        raise CustomException(e) from e

    logger.info("Connection to database '%s' verified", target)
    return True


def list_databases() -> list[str]:
    """Return the user databases on the server, for the database picker."""
    query = text(
        "SELECT name FROM sys.databases "
        "WHERE database_id > 4 ORDER BY name"  # skip master/tempdb/model/msdb
    )

    try:
        with get_engine().connect() as conn:
            databases = [row[0] for row in conn.execute(query)]
    except SQLAlchemyError as e:
        raise CustomException(e) from e

    logger.info("Found %d user database(s)", len(databases))
    return databases
