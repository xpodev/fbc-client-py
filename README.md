# xpo-feedback: Python client for the Xpo Feedback Center

Send **feedback**, **support requests**, **crash/error reports** and **usage analytics**
from Xpo software to the Xpo Feedback Center.

- No dependencies (standard library only), Python 3.9+
- Thread-safe; analytics are batched on a background thread
- Error reporting never raises, so it can't break your application
- Works with any text, including Hebrew and other non-Latin scripts

> TypeScript/JavaScript? See [fbc-client-ts](https://github.com/xpodev/fbc-client-ts).

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Keys: public or protected](#keys-public-or-protected)
- [Configuration](#configuration)
- [The end user](#the-end-user)
- [Feedback](#feedback)
- [Support requests](#support-requests)
- [Errors and crashes](#errors-and-crashes)
- [Analytics](#analytics)
- [Checking what a key can do](#checking-what-a-key-can-do)
- [Error handling](#error-handling)
- [Recipes](#recipes)
- [Limits](#limits)
- [API reference](#api-reference)
- [Versioning](#versioning)
- [Development](#development)

## Installation

Install straight from this repository, pinned to a release tag (needs `git` on the
machine):

```bash
pip install "xpo-feedback @ git+https://github.com/xpodev/fbc-client-py@v0.1.0"
```

In `requirements.txt`:

```text
xpo-feedback @ git+https://github.com/xpodev/fbc-client-py@v0.1.0
```

In `pyproject.toml`:

```toml
dependencies = ["xpo-feedback @ git+https://github.com/xpodev/fbc-client-py@v0.1.0"]
```

The distribution is called `xpo-feedback`; the import name is `xpo_feedback`.

## Quick start

```python
from xpo_feedback import FeedbackClient

feedback = FeedbackClient(
    "<software key from the Feedback Center dashboard>",
    base_url="https://feedback.xpo.example",
    release="3.2.1",           # your application's version
    environment="production",
)

feedback.install_excepthook()                    # report crashes automatically
feedback.send_feedback("Love the new export!", rating=5)
feedback.track("report_exported", {"format": "xlsx"})
```

Create **one client per process** and reuse it. It holds the breadcrumb buffer and
the analytics queue, and it flushes pending analytics when the process exits.

## Keys: public or protected

Each key is created in the Feedback Center dashboard for one piece of software
(optionally for one customer) and comes in two kinds:

| | Public key | Protected key |
|---|---|---|
| Use it in | Software running on end-user machines: desktop apps, scripts you ship | Servers that Xpo or the customer control |
| Secret? | No. Anyone can extract it | Yes. Load it from a secret store or environment variable |
| Shown in the dashboard as | **Public** (unverified sender) | **Verified** |
| Rate limit | 60 requests/min per key **and** client IP | 1200 requests/min per key |

A key also has a fixed set of **capabilities**: `feedback`, `support`, `errors` and
`analytics`. Calling a method the key isn't allowed to use fails with HTTP 403 (see
[Error handling](#error-handling)). Use [`key_info()`](#checking-what-a-key-can-do)
to find out what a key can do.

Never hard-code a protected key in source code:

```python
import os
feedback = FeedbackClient(os.environ["XPO_FEEDBACK_KEY"], base_url=os.environ["XPO_FEEDBACK_URL"])
```

## Configuration

```python
FeedbackClient(
    key,                           # str, required
    *,
    base_url,                      # str, required: e.g. "https://feedback.xpo.example"
    release=None,                  # your app version; sent with errors and as context.app_version
    environment=None,              # e.g. "production", "staging"; sent with errors
    context=None,                  # dict merged into the context of every report
    timeout=5.0,                   # seconds per HTTP attempt
    retries=2,                     # extra attempts on network errors, HTTP 429 and 5xx
    max_breadcrumbs=50,            # breadcrumbs kept for the next error report
    analytics_batch_size=20,       # flush analytics when this many are queued (max 100)
    analytics_flush_interval=10.0, # ...or after this many seconds
    anonymous_id=None,             # stable id for analytics; random per client if omitted
)
```

**Context** is free-form information about where a report came from, shown in the
dashboard next to each report. The client fills in `platform`, `os`, `runtime` and
`sdk` automatically, plus `app_version` when you pass `release`. Add your own:

```python
feedback = FeedbackClient(key, base_url=url, context={"tenant": "acme", "site": "warehouse-2"})
```

Every method also accepts `context=` for one-off additions; those are merged on top.
The server adds the HTTP `User-Agent` (and the browser `Origin`, when there is one).

**Retries** wait about 0.5 s, then 1 s, doubling up to 4 s, with some jitter. They
apply only to network errors, HTTP 429 and HTTP 5xx; other errors are not retried.

## The end user

Attach the person using your software to everything sent afterwards:

```python
feedback.set_user(id="42", email="dana@acme.example", name="Dana Cohen")
feedback.set_user()   # clear it, e.g. on sign-out
```

Or pass a `User` to a single call:

```python
from xpo_feedback import User
feedback.send_feedback("...", user=User(email="dana@acme.example"))
```

All fields are optional. With a public key the dashboard labels this information
*self-reported*: it's whatever your software sent, and nobody verified it.

## Feedback

```python
ticket_id = feedback.send_feedback(
    "Exporting to Excel takes almost a minute",
    category="bug",        # free text, max 64 chars, e.g. "bug", "idea", "praise"
    rating=3,              # optional 1–5
    metadata={"screen": "reports", "rows": 5200},
)
```

Returns the new ticket's id (a UUID string). Raises `FeedbackError` on failure, so
you can tell the user (see [Error handling](#error-handling)).

## Support requests

```python
feedback.request_support(
    "Can't sign in",
    "After resetting my password the login page says the password is wrong.",
    user=User(name="Dana Cohen", email="dana@acme.example"),  # how Xpo can reply
    category="login",
)
```

Support requests show up in the dashboard's **Support** inbox. Include a way to
contact the user, or Xpo won't be able to answer. Returns the ticket id; raises
`FeedbackError` on failure.

## Errors and crashes

### Uncaught exceptions

```python
feedback.install_excepthook()
```

This reports uncaught exceptions in the main thread and in other threads as `fatal`,
then calls the previously installed hooks, so the usual traceback still prints.
`KeyboardInterrupt` is ignored.

### Handled exceptions

```python
try:
    import_orders(path)
except Exception:
    feedback.capture_exception()          # uses the exception being handled
    raise                                 # or handle it however you like

feedback.capture_exception(err, level="warning", metadata={"file": path})
```

The report includes the exception type, message and full traceback, plus `release`,
`environment`, the current user, context and recent breadcrumbs.

### Messages

For notable conditions that aren't exceptions:

```python
feedback.capture_message("Disk almost full", level="warning", metadata={"free_mb": 120})
```

Levels: `"fatal"`, `"error"`, `"warning"`, `"info"`.

### Grouping

The dashboard groups reports of the same problem into one **issue**. By default the
grouping key comes from the exception type and stack trace; line numbers, numeric ids
and hex addresses are ignored, so the same bug in a later release still lands in the
same issue. To group differently, pass your own `fingerprint`:

```python
feedback.capture_exception(err, fingerprint="payment-gateway-timeout")
```

If an issue was marked *resolved* and happens again, it reopens automatically.

### Breadcrumbs

Breadcrumbs are a short trail of what happened before an error. The most recent
ones (50 by default) are attached to the next error report:

```python
feedback.add_breadcrumb("Opened order 1042", category="navigation")
feedback.add_breadcrumb("GET /api/stock -> 503", category="http", level="warning", data={"ms": 3100})
```

### `logging` integration

```python
import logging
from xpo_feedback import BreadcrumbHandler, ErrorReportingHandler

root = logging.getLogger()
root.addHandler(BreadcrumbHandler(feedback))       # INFO+ log lines become breadcrumbs
root.addHandler(ErrorReportingHandler(feedback))   # ERROR+ log lines are reported
```

`ErrorReportingHandler` reports `logger.exception(...)` calls with their traceback,
and other `ERROR`/`CRITICAL` records as messages. Both handlers ignore the client's
own `xpo_feedback` logger. Pass `level=` to either handler to change its threshold.

### Errors never raise

`capture_exception` and `capture_message` return the event id, or `None` if the report
couldn't be sent (no network, key without the `errors` capability, and so on). The
failure is logged as a warning on the `xpo_feedback` logger.

They send **synchronously**. When the server is unreachable the worst case is about
`timeout × (retries + 1)` plus the retry waits: roughly 17 seconds with the defaults.
In latency-sensitive code (for example, inside a web request), lower `timeout` and
`retries`, or report from a background thread:

```python
import threading
threading.Thread(target=feedback.capture_exception, args=(err,), daemon=True).start()
```

## Analytics

```python
feedback.track("report_exported", {"format": "xlsx", "rows": 5200})
feedback.track("item_scanned", user_id="42")   # overrides the current user's id
```

`track()` queues the event and returns immediately. A background thread sends batches
when `analytics_batch_size` events are queued or every `analytics_flush_interval`
seconds. Each event carries the client's `anonymous_id` and `session_id`, so the
dashboard can count unique users even without a user id.

- `feedback.flush()` sends everything queued, now.
- `feedback.close()` flushes and stops the background thread. It's registered with
  `atexit`, so a normal interpreter exit flushes automatically. Events tracked after
  `close()` are dropped.
- If the server is unreachable, events stay queued (up to 10,000; oldest dropped
  first) and are retried on the next flush. A batch the server rejects outright (for
  example, the key lacks `analytics`) is dropped.

To count the same person across runs of a desktop app, persist an id and pass it in:

```python
feedback = FeedbackClient(key, base_url=url, anonymous_id=load_or_create_install_id())
```

## Checking what a key can do

```python
info = feedback.key_info()
# {"key_id": "...", "application": "inventory-pro", "application_name": "Inventory Pro",
#  "customer": "Acme Ltd" or None, "kind": "public" or "protected",
#  "capabilities": ["errors", "feedback"]}

show_support_button = "support" in info["capabilities"]
```

Raises `FeedbackError` on failure (for example, 401 for a revoked key).

## Error handling

| Method | On failure |
|---|---|
| `send_feedback`, `request_support`, `key_info` | Raise `FeedbackError` |
| `capture_exception`, `capture_message` | Return `None` and log a warning |
| `track`, `flush`, `close` | Never raise; events are kept for retry or dropped, as described above |

`FeedbackError` has a readable message and `status`, the HTTP status code (`None` when
the server couldn't be reached):

```python
from xpo_feedback import FeedbackError

try:
    feedback.send_feedback(text, rating=rating)
except FeedbackError as err:
    if err.status == 429:
        show("Too many messages, please try again in a minute.")
    elif err.status is None:
        show("Can't reach the server. Check your connection.")
    else:
        show(f"Sending failed: {err}")
```

| Status | Meaning |
|---|---|
| 401 | Missing, invalid or revoked key |
| 403 | The key lacks the capability, or the request's browser origin isn't allowed for the key |
| 413 | The request body is too large |
| 422 | Invalid input (for example, an empty message or a rating outside 1–5); the message says which field |
| 429 | Rate limited |
| 5xx | Server problem (already retried) |

## Recipes

### Desktop or command-line app

```python
feedback = FeedbackClient(PUBLIC_KEY, base_url=URL, release=__version__)
feedback.install_excepthook()

def on_feedback_dialog_submit(text: str, stars: int) -> None:
    try:
        feedback.send_feedback(text, rating=stars)
        show_message("Thanks for your feedback!")
    except FeedbackError:
        show_message("Couldn't send your feedback. Please try again later.")
```

### Flask

```python
from flask import Flask, got_request_exception, request

app = Flask(__name__)

def report(sender, exception, **extra):
    feedback.capture_exception(exception, context={"path": request.path, "method": request.method})

got_request_exception.connect(report, app)
```

### Django

```python
# myapp/middleware.py
class FeedbackCenterMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        feedback.capture_exception(exception, context={"path": request.path, "method": request.method})
        return None   # let Django continue its normal error handling
```

Add `"myapp.middleware.FeedbackCenterMiddleware"` to `MIDDLEWARE`. Alternatively, attach
`ErrorReportingHandler` to the `django.request` logger.

### FastAPI

```python
import asyncio
from fastapi import FastAPI, Request

app = FastAPI()

@app.middleware("http")
async def report_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        # capture_exception blocks while sending, so run it off the event loop
        await asyncio.to_thread(
            feedback.capture_exception, exc, context={"path": request.url.path, "method": request.method}
        )
        raise
```

### Forwarding reports from your server

A web app's browser code can use a public key directly. To get **verified** reports
instead, have the browser send to your own backend and forward them with a protected
key. Because the user comes from your session, it's trustworthy too:

```python
from xpo_feedback import User

@app.post("/api/feedback")
def forward_feedback(body: FeedbackForm, user: CurrentUser):
    feedback.send_feedback(
        body.message,
        rating=body.rating,
        user=User(id=str(user.id), email=user.email, name=user.name),
    )
```

### Hebrew and other languages

All text is sent as UTF-8 and stored as-is. The dashboard shows mixed Hebrew/English
content in the right direction, and search works in any language:

```python
feedback.send_feedback("הייצוא לאקסל איטי מאוד", category="באג", rating=3)
```

## Limits

Enforced by the server; values over a limit are rejected with HTTP 422.

| Field | Limit |
|---|---|
| Feedback `message` | 10,000 characters |
| Support `subject` / `message` | 255 / 20,000 characters |
| `category` | 64 characters |
| `rating` | Integer 1–5 |
| Error `message` / stack trace | 10,000 / 200,000 characters |
| Breadcrumbs per report | 200 (the client keeps 50 by default) |
| Breadcrumb message | 2,000 characters (the client truncates longer ones) |
| Events per batch / event name | 100 / 128 characters |
| Request body | 512 KB (default server setting) |

Error and event timestamps more than 10 minutes in the future or 30 days in the past
are replaced with the time the server received them.

## API reference

### `FeedbackClient(key, *, base_url, ...)`

See [Configuration](#configuration) for the arguments.

**Attributes:** `base_url`, `release`, `environment`, `context` (a dict you may update),
`user` (`User` or `None`), `anonymous_id`, `session_id` (random per client).

| Method | Returns | Notes |
|---|---|---|
| `set_user(id=None, email=None, name=None)` | `None` | No arguments clears the user |
| `key_info()` | `dict` | Raises `FeedbackError` |
| `send_feedback(message, *, category=None, rating=None, user=None, context=None, metadata=None)` | Ticket id (`str`) | Raises `FeedbackError` |
| `request_support(subject, message, *, category=None, user=None, context=None, metadata=None)` | Ticket id (`str`) | Raises `FeedbackError` |
| `capture_exception(exc=None, *, level="error", fingerprint=None, user=None, context=None, metadata=None)` | Event id (`str`) or `None` | `exc` defaults to the exception being handled; returns `None` if there is none |
| `capture_message(message, *, level="info", fingerprint=None, user=None, context=None, metadata=None)` | Event id (`str`) or `None` | |
| `add_breadcrumb(message, *, category=None, level=None, data=None)` | `None` | |
| `install_excepthook()` | `None` | Hooks `sys.excepthook` and `threading.excepthook` |
| `track(name, properties=None, *, user_id=None)` | `None` | Queued |
| `flush()` | `None` | Sends queued analytics |
| `close()` | `None` | Flushes and stops; also runs at exit |

The client is also a context manager: `with FeedbackClient(...) as feedback:` closes it
on exit.

### `User(id=None, email=None, name=None)`

A dataclass describing the end user.

### `FeedbackError(message, status=None)`

Raised by `send_feedback`, `request_support` and `key_info`. `status` is the HTTP status
code, or `None` if the server couldn't be reached.

### `BreadcrumbHandler(client, level=logging.INFO)`

A `logging.Handler` that records log lines as breadcrumbs.

### `ErrorReportingHandler(client, level=logging.ERROR)`

A `logging.Handler` that reports log records as errors.

## Versioning

Releases are tagged `vX.Y.Z`; see [CHANGELOG.md](CHANGELOG.md). Always pin a tag when
installing. The client talks to the Feedback Center's `/v1` API, which only changes in
backward-compatible ways, so older clients keep working against newer servers.

## Development

```bash
uv run pytest                  # tests use a fake HTTP server; no Feedback Center needed
uv run --python 3.9 pytest     # oldest supported Python
```
