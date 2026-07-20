"""Custom exception type carrying file, line and original-error context."""

import sys
from types import TracebackType
from typing import Optional

from src.logging.logger import logger


def _error_detail(error: BaseException, exc_tb: Optional[TracebackType]) -> str:
    """Build a message pointing at the deepest frame where the error was raised."""
    if exc_tb is None:
        return f"Error occurred: {error}"

    while exc_tb.tb_next is not None:
        exc_tb = exc_tb.tb_next

    frame = exc_tb.tb_frame
    return (
        f"Error occurred in script [{frame.f_code.co_filename}] "
        f"at line [{exc_tb.tb_lineno}] with message [{error}]"
    )


class CustomException(Exception):
    """Wraps any exception with the location it originated from.

    Usage::

        try:
            ...
        except Exception as e:
            raise CustomException(e) from e
    """

    def __init__(self, error: BaseException | str, error_detail: object = sys) -> None:
        exc_tb = sys.exc_info()[2]
        self.error = error
        self.message = _error_detail(
            error if isinstance(error, BaseException) else Exception(error), exc_tb
        )
        super().__init__(self.message)
        logger.error(self.message)

    def __str__(self) -> str:
        return self.message
