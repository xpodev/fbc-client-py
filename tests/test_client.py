from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from xpo_feedback import BreadcrumbHandler, ErrorReportingHandler, FeedbackClient, FeedbackError


class FakeServer:
    """Records requests and answers with canned responses."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail_next: list[int] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _respond(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                server.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                self._respond(200, {"application": "demo", "kind": "public", "capabilities": ["feedback"]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                server.requests.append({"method": "POST", "path": self.path, "body": body, "headers": dict(self.headers)})
                if server.fail_next:
                    status = server.fail_next.pop(0)
                    return self._respond(status, {"detail": "nope"})
                if self.path == "/v1/events":
                    return self._respond(202, {"accepted": len(body["events"])})
                self._respond(201, {"id": f"id-{len(server.requests)}", "group_id": "g"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def posts(self, path: str) -> list[dict]:
        return [r["body"] for r in self.requests if r["method"] == "POST" and r["path"] == path]


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.httpd.shutdown()


@pytest.fixture
def client(server):
    c = FeedbackClient("tok", base_url=server.url + "/", release="1.2.3", environment="test", retries=1)
    c._flush_interval = 3600  # flush manually in tests
    yield c
    c.close()


def test_send_feedback(server, client):
    client.set_user(id="u1", email="u@example.com")
    ticket_id = client.send_feedback("Great", rating=5, category="praise", metadata={"screen": "home"})
    assert ticket_id.startswith("id-")
    req = server.requests[-1]
    assert req["path"] == "/v1/feedback"
    assert req["headers"]["Authorization"] == "Bearer tok"
    body = req["body"]
    assert body["message"] == "Great" and body["rating"] == 5
    assert body["user"] == {"id": "u1", "email": "u@example.com"}
    assert body["context"]["app_version"] == "1.2.3"
    assert body["metadata"] == {"screen": "home"}


def test_request_support(server, client):
    client.request_support("Locked out", "Help me", context={"tenant": "acme"})
    body = server.posts("/v1/support")[0]
    assert body["subject"] == "Locked out" and body["context"]["tenant"] == "acme"


def test_user_facing_calls_raise(server, client):
    server.fail_next = [403]
    with pytest.raises(FeedbackError) as info:
        client.send_feedback("x")
    assert info.value.status == 403 and "nope" in str(info.value)


def test_retries_server_errors(server, client):
    server.fail_next = [503]
    assert client.send_feedback("x")
    assert len(server.posts("/v1/feedback")) == 2


def test_capture_exception_with_breadcrumbs(server, client):
    client.add_breadcrumb("opened settings", category="ui")
    try:
        int("N/A")
    except ValueError:
        event_id = client.capture_exception()
    assert event_id
    body = server.posts("/v1/errors")[0]
    assert body["type"] == "ValueError"
    assert "invalid literal" in body["message"]
    assert "Traceback" in body["stacktrace"]
    assert body["release"] == "1.2.3" and body["environment"] == "test"
    assert body["breadcrumbs"][0]["message"] == "opened settings"


def test_telemetry_never_raises(server, client):
    server.fail_next = [500, 500]
    assert client.capture_message("disk almost full", level="warning") is None
    bad = FeedbackClient("tok", base_url="http://127.0.0.1:9", retries=0, timeout=0.5)
    assert bad.capture_message("x") is None
    bad.track("x")
    bad.close()


def test_capture_exception_without_active_exception(client):
    assert client.capture_exception() is None


def test_track_batches_and_flushes(server, client):
    client.set_user(id="u9")
    for i in range(3):
        client.track("clicked", {"i": i})
    assert server.posts("/v1/events") == []
    client.flush()
    batch = server.posts("/v1/events")[0]
    assert [e["properties"]["i"] for e in batch["events"]] == [0, 1, 2]
    assert batch["events"][0]["user_id"] == "u9"
    assert batch["events"][0]["anonymous_id"] == client.anonymous_id


def test_track_keeps_events_when_server_down(server, client):
    client.track("a")
    server.fail_next = [503, 503]
    client.flush()
    assert len(client._events) == 1
    client.flush()
    assert len(client._events) == 0


def test_close_flushes(server, client):
    client.track("bye")
    client.close()
    assert server.posts("/v1/events")[0]["events"][0]["name"] == "bye"
    client.track("ignored after close")
    assert client._events == []


def test_logging_handlers(server, client):
    logger = logging.getLogger("myapp")
    logger.setLevel(logging.INFO)
    crumbs, errors = BreadcrumbHandler(client), ErrorReportingHandler(client)
    logger.addHandler(crumbs)
    logger.addHandler(errors)
    try:
        logger.info("loading order 42")
        try:
            {}["missing"]
        except KeyError:
            logger.exception("order failed")
    finally:
        logger.removeHandler(crumbs)
        logger.removeHandler(errors)
    body = server.posts("/v1/errors")[0]
    assert body["type"] == "KeyError"
    assert body["metadata"]["log_message"] == "order failed"
    assert body["breadcrumbs"][0]["message"] == "loading order 42"


def test_key_info(server, client):
    assert client.key_info()["application"] == "demo"


def test_hebrew_payloads(server, client):
    client.set_user(name="יעל כהן")
    client.send_feedback("הייצוא איטי מאוד", category="באג", metadata={"מסך": "ייצוא"})
    try:
        raise ValueError("ערך לא חוקי בשדה 'כמות'")
    except ValueError:
        client.capture_exception()
    client.track("דוח_יוצא", {"פורמט": "אקסל"})
    client.flush()
    fb = server.posts("/v1/feedback")[0]
    assert fb["message"] == "הייצוא איטי מאוד" and fb["user"]["name"] == "יעל כהן"
    assert fb["metadata"] == {"מסך": "ייצוא"}
    err = server.posts("/v1/errors")[0]
    assert err["message"] == "ערך לא חוקי בשדה 'כמות'"
    assert "ערך לא חוקי" in err["stacktrace"]
    assert server.posts("/v1/events")[0]["events"][0]["name"] == "דוח_יוצא"
