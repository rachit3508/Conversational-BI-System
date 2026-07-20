"""Project-wide logging configuration.

Import ``logger`` from this module instead of calling ``logging.getLogger``
directly so every part of the system writes to the same timestamped log file.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = f"{datetime.now().strftime('%Y_%m_%d_%H_%M_%S')}.log"
LOG_FILE_PATH = LOG_DIR / LOG_FILE

LOG_FORMAT = "[ %(asctime)s ] %(levelname)s %(name)s - %(module)s:%(lineno)d - %(message)s"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def get_logger(name: str = "conversational_bi") -> logging.Logger:
    """Return a configured logger that writes to both file and stdout."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger.setLevel(LOG_LEVEL)
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


logger = get_logger()
