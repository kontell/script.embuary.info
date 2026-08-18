"""The HTTP client that replaced requests.

Run against a real socket rather than a mock. What was actually being replaced
was urllib3's connection handling, and the parts of it that matter here -- a
pooled connection the server has since closed, a gzipped body, a redirect --
are exactly the parts a mock would assert away rather than exercise.

Plain HTTP throughout: TLS adds a certificate to the fixture and tests nothing
these call sites depend on.
"""

import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from resources.lib.http_session import HttpSession, Response


class Handler(BaseHTTPRequestHandler):
    """Routes enough to cover what the add-on asks of a server."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None, head_only=False):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _route(self, head_only=False):
        server = self.server
        server.seen.append((self.command, self.path, self.headers.get("X-Test")))

        if self.path == "/json":
            self._send(200, b'{"hello": "world"}', head_only=head_only)

        elif self.path == "/gzip":
            body = gzip.compress(b'{"hello": "gzipped"}')
            self._send(200, body, {"Content-Encoding": "gzip"}, head_only=head_only)

        elif self.path == "/redirect":
            self._send(302, b"", {"Location": "/json"}, head_only=head_only)

        elif self.path == "/loop":
            self._send(302, b"", {"Location": "/loop"}, head_only=head_only)

        elif self.path == "/notfound":
            self._send(404, b"nope", head_only=head_only)

        elif self.path == "/boom":
            self._send(500, b"boom", head_only=head_only)

        elif self.path == "/close":
            """Answers, then hangs up. The next request on this connection is
            the stale-keep-alive case.
            """
            self._send(200, b"bye", {"Connection": "close"}, head_only=head_only)
            self.close_connection = True

        else:
            self._send(404, b"", head_only=head_only)

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route(head_only=True)


@pytest.fixture
def server():
    """Threading, not plain HTTPServer.

    HTTP/1.1 keep-alive plus a single-threaded server means the first client to
    connect holds the server until it hangs up, so the concurrency test below
    times out against a fixture limitation rather than anything in the client.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.seen = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def session():
    s = HttpSession()
    yield s
    s.close()


########################
""" Response
"""


def test_ok_follows_requests_semantics():
    assert Response(200, b"", "u").ok
    assert Response(302, b"", "u").ok
    assert not Response(404, b"", "u").ok
    assert not Response(500, b"", "u").ok


########################
""" Requests
"""


def test_get_returns_status_text_and_json(server, session):
    _, base = server
    response = session.get(base + "/json")

    assert response.status_code == 200
    assert response.ok
    assert response.text == '{"hello": "world"}'
    assert response.json() == {"hello": "world"}


def test_gzip_is_negotiated_and_decoded(server, session):
    """requests did this invisibly. Dropping it would have traded bandwidth for
    nothing on exactly the slow connections this is meant to help.
    """
    _, base = server
    response = session.get(base + "/gzip")

    assert response.json() == {"hello": "gzipped"}


def test_error_statuses_come_back_rather_than_raising(server, session):
    _, base = server

    assert session.get(base + "/notfound").status_code == 404
    assert session.get(base + "/boom").status_code == 500
    assert not session.get(base + "/boom").ok


def test_head_sends_head_and_reads_no_body(server, session):
    """The trailer check is the only HEAD caller and only reads the status."""
    httpd, base = server
    response = session.head(base + "/json")

    assert response.status_code == 200
    assert ("HEAD", "/json", None) in httpd.seen


def test_custom_headers_reach_the_server(server, session):
    """Trakt sends its api key this way."""
    httpd, base = server
    session.get(base + "/json", headers={"X-Test": "trakt-key"})

    assert ("GET", "/json", "trakt-key") in httpd.seen


########################
""" Redirects
"""


def test_redirects_are_followed(server, session):
    _, base = server
    response = session.get(base + "/redirect")

    assert response.status_code == 200
    assert response.json() == {"hello": "world"}


def test_a_redirect_loop_gives_up_rather_than_spinning(server, session):
    _, base = server
    response = session.get(base + "/loop")

    assert response.status_code == 302


########################
""" Connection reuse, which is why the session exists at all
"""


def test_connections_are_reused_between_requests(server, session):
    """The whole point of holding a session: one TCP and TLS setup for the
    twenty-odd requests a page makes, not twenty.
    """
    _, base = server

    for _ in range(5):
        assert session.get(base + "/json").ok

    pooled = [c for pool in session._idle.values() for c in pool]
    assert len(pooled) == 1


def test_a_connection_the_server_closed_is_retried_not_failed(server, session):
    """The case urllib3 handled invisibly.

    A keep-alive connection can be dropped by the server at any time, and
    http.client only finds out on the *next* request. Without the retry this is
    an intermittent failure that looks like a flaky network.
    """
    _, base = server

    assert session.get(base + "/close").ok
    assert session.get(base + "/json").ok
    assert session.get(base + "/json").ok


def test_a_dead_host_raises_rather_than_retrying_forever(session):
    """A fresh connection that fails is a real failure. Only a pooled one gets
    a second chance.
    """
    with pytest.raises(Exception):
        session.get("http://127.0.0.1:1/json", timeout=1)


def test_the_pool_is_safe_to_use_from_several_threads(server, session):
    """The trailer check runs this from a ThreadPoolExecutor."""
    _, base = server
    results = []
    errors = []

    def hit():
        try:
            results.append(session.get(base + "/json").status_code)
        except Exception as error:  # pragma: no cover - only on a real failure
            errors.append(error)

    threads = [threading.Thread(target=hit) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors
    assert results == [200] * 12


def test_close_empties_the_pool(server, session):
    _, base = server
    session.get(base + "/json")

    session.close()

    assert session._idle == {}
