from __future__ import annotations

import atexit
import json
import logging
import platform
import random
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Dict, List, Optional, Type
from urllib import error as urlerror
from urllib import request as urlrequest

log = logging.getLogger("xpo_feedback")

_SDK = "xpo-feedback-python/0.1.0"
_LEVELS = ("fatal", "error", "warning", "info")


class FeedbackError(Exception):
    """Raised when the Feedback Center rejects a request or can't be reached."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class User:
    """The end user of your software. With a public key this is unverified."""

    id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None

    def to_json(self) -> Dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeedbackClient:
    """Sends feedback, support requests, errors and analytics to the Feedback Center.

    Methods a user directly triggers (``send_feedback``, ``request_support``,
    ``key_info``) raise :class:`FeedbackError` on failure so your UI can react.
    Telemetry methods (``capture_exception``, ``capture_message``, ``track``)
    never raise: reporting must never break your application.
    """

    def __init__(
        self,
        key: str,
        *,
        base_url: str,
        release: Optional[str] = None,
        environment: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
        retries: int = 2,
        max_breadcrumbs: int = 50,
        analytics_batch_size: int = 20,
        analytics_flush_interval: float = 10.0,
        anonymous_id: Optional[str] = None,
    ) -> None:
        if not key:
            raise ValueError("A software key is required")
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.release = release
        self.environment = environment
        self.timeout = timeout
        self.retries = retries
        self.user: Optional[User] = None
        self.anonymous_id = anonymous_id or uuid.uuid4().hex
        self.session_id = uuid.uuid4().hex
        self.context: Dict[str, Any] = {
            "platform": sys.platform,
            "os": platform.platform(terse=True),
            "runtime": f"python {platform.python_version()}",
            "sdk": _SDK,
        }
        if release:
            self.context["app_version"] = release
        self.context.update(context or {})

        self._breadcrumbs: deque = deque(maxlen=max_breadcrumbs)
        self._events: List[Dict[str, Any]] = []
        self._events_lock = threading.Lock()
        self._batch_size = max(1, min(analytics_batch_size, 100))
        self._flush_interval = analytics_flush_interval
        self._flusher: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._closed = False
        atexit.register(self.close)

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(body, default=str).encode() if body is not None else None
        req = urlrequest.Request(
            f"{self.base_url}/v1{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "User-Agent": _SDK,
            },
        )
        attempt = 0
        while True:
            try:
                with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urlerror.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt >= self.retries:
                    raise FeedbackError(self._describe(exc), exc.code) from None
            except (urlerror.URLError, OSError) as exc:
                if attempt >= self.retries:
                    raise FeedbackError(f"Feedback Center unreachable: {exc}") from None
            attempt += 1
            time.sleep(min(4.0, 0.5 * 2 ** (attempt - 1)) * (0.8 + random.random() * 0.4))

    @staticmethod
    def _describe(exc: urlerror.HTTPError) -> str:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = None
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        return f"{exc.code}: {detail or exc.reason}"

    def _merge_context(self, extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {**self.context, **(extra or {})}

    def _user_json(self, user: Optional[User]) -> Optional[Dict[str, str]]:
        u = user or self.user
        return u.to_json() if u else None

    # ------------------------------------------------------------------ identity

    def set_user(
        self, id: Optional[str] = None, email: Optional[str] = None, name: Optional[str] = None
    ) -> None:
        """Attach an end user to everything sent afterwards. Call with no args to clear."""
        self.user = User(id=id, email=email, name=name) if (id or email or name) else None

    def key_info(self) -> Dict[str, Any]:
        """Return what this key may do: ``{"application", "kind", "capabilities", ...}``."""
        return self._request("GET", "/key")

    # ------------------------------------------------------------------ feedback & support

    def send_feedback(
        self,
        message: str,
        *,
        category: Optional[str] = None,
        rating: Optional[int] = None,
        user: Optional[User] = None,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Send end-user feedback. ``rating`` is 1–5. Returns the ticket id."""
        body = {
            "message": message,
            "category": category,
            "rating": rating,
            "user": self._user_json(user),
            "context": self._merge_context(context),
            "metadata": metadata or {},
        }
        return self._request("POST", "/feedback", body)["id"]

    def request_support(
        self,
        subject: str,
        message: str,
        *,
        category: Optional[str] = None,
        user: Optional[User] = None,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Open a support request. Include the user's email so Xpo can reply."""
        body = {
            "subject": subject,
            "message": message,
            "category": category,
            "user": self._user_json(user),
            "context": self._merge_context(context),
            "metadata": metadata or {},
        }
        return self._request("POST", "/support", body)["id"]

    # ------------------------------------------------------------------ errors

    def add_breadcrumb(
        self,
        message: str,
        *,
        category: Optional[str] = None,
        level: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record something that happened; the latest ones are sent with the next error."""
        crumb: Dict[str, Any] = {"message": str(message)[:2000], "timestamp": _now()}
        if category:
            crumb["category"] = category
        if level:
            crumb["level"] = level
        if data:
            crumb["data"] = data
        self._breadcrumbs.append(crumb)

    def capture_exception(
        self,
        exc: Optional[BaseException] = None,
        *,
        level: str = "error",
        fingerprint: Optional[str] = None,
        user: Optional[User] = None,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Report an exception (defaults to the one currently being handled).

        Returns the event id, or ``None`` if it couldn't be sent.
        """
        if exc is None:
            exc = sys.exc_info()[1]
            if exc is None:
                return None
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return self._send_error(
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "stacktrace": stack,
                "level": level if level in _LEVELS else "error",
                "fingerprint": fingerprint,
            },
            user,
            context,
            metadata,
        )

    def capture_message(
        self,
        message: str,
        *,
        level: str = "info",
        fingerprint: Optional[str] = None,
        user: Optional[User] = None,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Report a notable condition that isn't an exception."""
        return self._send_error(
            {
                "message": message,
                "level": level if level in _LEVELS else "info",
                "fingerprint": fingerprint,
            },
            user,
            context,
            metadata,
        )

    def _send_error(
        self,
        payload: Dict[str, Any],
        user: Optional[User],
        context: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        body = {
            **payload,
            "release": self.release,
            "environment": self.environment,
            "timestamp": _now(),
            "breadcrumbs": list(self._breadcrumbs),
            "user": self._user_json(user),
            "context": self._merge_context(context),
            "metadata": metadata or {},
        }
        try:
            return self._request("POST", "/errors", body)["id"]
        except Exception as exc:  # never let reporting crash the app
            log.warning("Could not report error to Feedback Center: %s", exc)
            return None

    def install_excepthook(self) -> None:
        """Report uncaught exceptions (main thread and other threads) as fatal errors."""
        previous = sys.excepthook

        def hook(
            exc_type: Type[BaseException], exc: BaseException, tb: Optional[TracebackType]
        ) -> None:
            if not issubclass(exc_type, KeyboardInterrupt):
                self.capture_exception(exc, level="fatal")
            previous(exc_type, exc, tb)

        sys.excepthook = hook

        previous_thread_hook = threading.excepthook

        def thread_hook(args: Any) -> None:
            if args.exc_value is not None and not isinstance(args.exc_value, KeyboardInterrupt):
                self.capture_exception(args.exc_value, level="fatal")
            previous_thread_hook(args)

        threading.excepthook = thread_hook

    # ------------------------------------------------------------------ analytics

    def track(
        self,
        name: str,
        properties: Optional[Dict[str, Any]] = None,
        *,
        user_id: Optional[str] = None,
    ) -> None:
        """Queue an analytics event. Events are sent in batches in the background."""
        if self._closed:
            return
        event = {
            "name": name,
            "timestamp": _now(),
            "anonymous_id": self.anonymous_id,
            "session_id": self.session_id,
            "user_id": user_id or (self.user.id if self.user else None),
            "properties": properties or {},
        }
        with self._events_lock:
            self._events.append(event)
            full = len(self._events) >= self._batch_size
            if len(self._events) > 10_000:  # server unreachable for a long time: drop oldest
                del self._events[: len(self._events) - 10_000]
        self._ensure_flusher()
        if full:
            self._wake.set()

    def flush(self) -> None:
        """Send all queued analytics events now."""
        while True:
            with self._events_lock:
                batch = self._events[:100]
                del self._events[:100]
            if not batch:
                return
            try:
                self._request("POST", "/events", {"events": batch, "context": self.context})
            except FeedbackError as exc:
                if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                    log.warning("Feedback Center rejected analytics batch: %s", exc)
                    continue  # bad data or no capability: drop it
                with self._events_lock:
                    self._events[:0] = batch  # keep for the next attempt
                log.debug("Analytics flush failed: %s", exc)
                return

    def _ensure_flusher(self) -> None:
        if self._flusher is None or not self._flusher.is_alive():
            self._flusher = threading.Thread(
                target=self._flush_loop, name="xpo-feedback-flush", daemon=True
            )
            self._flusher.start()

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._flush_interval)
            self._wake.clear()
            try:
                self.flush()
            except Exception:  # pragma: no cover - defensive
                log.exception("Analytics flush crashed")

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Flush pending analytics and stop the background thread."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._wake.set()
        try:
            self.flush()
        except Exception:
            pass

    def __enter__(self) -> "FeedbackClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
