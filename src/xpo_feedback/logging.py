"""Integrations with the standard :mod:`logging` module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xpo_feedback.client import FeedbackClient

_LEVEL_NAMES = {
    logging.CRITICAL: "fatal",
    logging.ERROR: "error",
    logging.WARNING: "warning",
    logging.INFO: "info",
    logging.DEBUG: "info",
}


def _level(record: logging.LogRecord) -> str:
    for threshold in sorted(_LEVEL_NAMES, reverse=True):
        if record.levelno >= threshold:
            return _LEVEL_NAMES[threshold]
    return "info"


class BreadcrumbHandler(logging.Handler):
    """Keep recent log lines as breadcrumbs, sent along with the next error report.

        logging.getLogger().addHandler(BreadcrumbHandler(feedback))
    """

    def __init__(self, client: "FeedbackClient", level: int = logging.INFO) -> None:
        super().__init__(level)
        self.client = client

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("xpo_feedback"):
            return
        try:
            self.client.add_breadcrumb(
                record.getMessage(), category=record.name, level=_level(record)
            )
        except Exception:
            self.handleError(record)


class ErrorReportingHandler(logging.Handler):
    """Report log records at ERROR and above (with their exception, if any).

        logging.getLogger().addHandler(ErrorReportingHandler(feedback))
    """

    def __init__(self, client: "FeedbackClient", level: int = logging.ERROR) -> None:
        super().__init__(level)
        self.client = client

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("xpo_feedback"):
            return
        try:
            extra = {"logger": record.name}
            if record.exc_info and record.exc_info[1] is not None:
                self.client.capture_exception(
                    record.exc_info[1], level=_level(record), metadata={**extra, "log_message": record.getMessage()}
                )
            else:
                self.client.capture_message(record.getMessage(), level=_level(record), metadata=extra)
        except Exception:
            self.handleError(record)
