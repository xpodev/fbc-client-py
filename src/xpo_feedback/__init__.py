"""Client for the Xpo Feedback Center.

    from xpo_feedback import FeedbackClient

    feedback = FeedbackClient("<software key>", base_url="https://feedback.xpo.example")
    feedback.send_feedback("Love the new export!", rating=5)
"""

from xpo_feedback.client import FeedbackClient, FeedbackError, User
from xpo_feedback.logging import BreadcrumbHandler, ErrorReportingHandler

__all__ = [
    "BreadcrumbHandler",
    "ErrorReportingHandler",
    "FeedbackClient",
    "FeedbackError",
    "User",
]
__version__ = "0.1.0"
