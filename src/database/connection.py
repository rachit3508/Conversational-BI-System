"""SQL Server connection concerns -- building URLs and verifying reachability.

Engine creation and caching live in :mod:`src.database.registry`; this module only
describes *how* to reach a database, not *which* engines exist. Query execution belongs
in a separate module again.

Uses Windows (trusted) authentication against a local instance, so no username or
password is ever handled here. Settings come from ``.env`` via python-dotenv.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from src.exception.exception import CustomException
from src.logging.logger import logger

load_dotenv()

DB_SERVER = os.getenv("DB_SERVER")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_TRUST_SERVER_CERTIFICATE = os.getenv("DB_TRUST_SERVER_CERTIFICATE", "yes")


def build_url(database: str) -> URL:
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


def check_connection(database: str | None = None) -> bool:
    """Open a connection and run ``SELECT 1``. Returns True when reachable."""
    # Imported here rather than at module scope: registry imports build_url from this
    # module, and a top-level import back would make the two modules circular.
    from src.database.registry import get_engine

    engine = get_engine(database)

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        raise CustomException(e) from e

    logger.info("Connection to database '%s' verified", engine.url.database)
    return True


def list_databases() -> list[str]:
    """Return the user databases present on the server.

    This is what the *server* holds; ``registry.configured_databases`` is what the user
    is allowed to pick. Useful for spotting a database that exists but is not yet
    configured in ``DB_NAMES``.
    """
    from src.database.registry import get_engine

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
