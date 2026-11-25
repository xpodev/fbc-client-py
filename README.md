# xpo-feedback (Python)

Client for the Xpo Feedback Center. No dependencies; Python 3.9+.

```bash
pip install "xpo-feedback @ git+https://github.com/xpodev/feedback-center#subdirectory=clients/python"
```

```python
from xpo_feedback import FeedbackClient

feedback = FeedbackClient(
    "<software key>",
    base_url="https://feedback.xpo.example",
    release="3.2.1",            # your app version
    environment="production",
)

# Optional: who is using the software (unverified when using a public key)
feedback.set_user(id="42", email="dana@acme.example", name="Dana")

# Feedback & support (raise FeedbackError on failure so your UI can react)
feedback.send_feedback("Exporting is slow", category="bug", rating=3)
feedback.request_support("Can't log in", "Reset link doesn't work")

# Errors (never raise)
feedback.install_excepthook()           # report uncaught exceptions
try:
    risky()
except Exception:
    feedback.capture_exception()        # report a handled exception
feedback.capture_message("Disk almost full", level="warning")

# Analytics (queued and sent in batches in the background)
feedback.track("report_exported", {"format": "xlsx"})

# What may this key do?
feedback.key_info()   # {"kind": "public", "capabilities": ["feedback", "errors"], ...}
```

## Logging integration

```python
import logging
from xpo_feedback import BreadcrumbHandler, ErrorReportingHandler

root = logging.getLogger()
root.addHandler(BreadcrumbHandler(feedback))      # INFO+ lines become breadcrumbs
root.addHandler(ErrorReportingHandler(feedback))  # ERROR+ lines are reported
```

Breadcrumbs (the last 50 by default) are attached to the next error report, so you
see what led up to a crash.

## Keys

Use a **public** key in software running on end-user devices (anyone can extract
it, so the dashboard marks those submissions as *Public*). Use a **protected** key
only on a server you or the customer control, and load it from a secret store.
