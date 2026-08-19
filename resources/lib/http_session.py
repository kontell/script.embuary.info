#!/usr/bin/python
# coding: utf-8

########################

import json
import threading
import zlib

from urllib.parse import urljoin, urlsplit

########################

""" A small HTTP client, in place of `requests`.

    `import requests` measured 825 ms inside Kodi 21's interpreter -- half the
    time it took to open a movie page, and paid on every single launch. It is
    not a filesystem cost and bytecode caching does not touch it; it is the
    package's module bodies executing. `http.client`, `ssl` and `json` cost
    nothing measurable by comparison, and `zlib` is a C module.

    Interpreter reuse would have amortised it, but it does not apply to
    RunScript -- see docs/measurements.md -- so the info dialog paid it every
    time and importing less was the only lever left.

    What this keeps from the previous `requests.Session`:

      * Connection reuse per host. That was the point of the Session, and it
        matters more on a low-end ARM SoC than on a desktop, where a TLS
        handshake is tens of milliseconds of real CPU rather than a rounding
        error. A movie page talks to two or three hosts and makes twenty-odd
        requests.
      * gzip, which requests negotiated invisibly. Dropping it would have
        quietly traded CPU time for bandwidth on exactly the slow connections
        this is meant to help.
      * Thread safety, because the trailer check runs this from a pool.

    What it deliberately does not have: cookies, auth, sessions, streaming,
    multipart, or automatic retries. Nothing here wanted any of it -- the four
    call sites do a plain GET or HEAD and read a body -- and each one is a way
    for this to become the thing it replaced.
"""

""" http.client and ssl are imported on the first request rather than at module
    scope, and the SSL context is built with them.

    Measured on the Android TV box this add-on exists for: importing this module
    cost 96 ms of a 600 ms page open, nearly all of it `ssl` loading OpenSSL and
    reading the system trust store. A page whose TheMovieDB responses are already
    cached makes no request at all, so that was the transport being paid for and
    never used -- the same shape as the xml.etree and concurrent.futures imports
    already deferred into the paths that need them.

    Guarded by a lock because the trailer check builds its pool of threads
    before any of them has made a request, so several can arrive here at once.
"""
_transport_lock = threading.Lock()
_client = None
_ssl = None


def _transport():
    """The http.client and ssl modules, imported on first use."""
    global _client, _ssl

    if _client is None:
        with _transport_lock:
            if _client is None:
                import http.client
                import ssl

                _ssl = ssl
                _client = http.client

    return _client, _ssl


DEFAULT_TIMEOUT = 5

""" How many idle connections to keep per host. The old adapter allowed eight;
    nothing here talks to more than a handful of hosts and the trailer pool is
    the only thing that runs wide, so this is sized for it.
"""
MAX_IDLE_PER_HOST = 8

MAX_REDIRECTS = 3

USER_AGENT = "script.embuary.info"


class Response(object):
    """Just enough of a requests Response for the callers there are.

    `ok` follows requests: anything below 400. The callers test `ok`,
    `status_code`, `text` and `json()`, and nothing else.
    """

    __slots__ = ("status_code", "url", "headers", "content")

    def __init__(self, status_code, content, url, headers=None):
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = headers or {}

    @property
    def ok(self):
        return self.status_code < 400

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


def _decode(body, encoding):
    """Undo whatever Content-Encoding the server used.

    zlib rather than gzip: the gzip module pulls in io, struct and os, and all
    that is wanted here is the raw inflate. The 16 tells zlib to expect a gzip
    header rather than a zlib one.
    """
    if not body or not encoding:
        return body

    encoding = encoding.lower()

    try:
        if encoding == "gzip":
            return zlib.decompress(body, 16 + zlib.MAX_WBITS)

        if encoding == "deflate":
            return zlib.decompress(body)

    except zlib.error:
        """A body we cannot inflate is worth handing back as-is rather than
        losing: the caller will fail to parse it and say so, which is a better
        failure than an empty response that looks like a network problem.
        """
        return body

    return body


class HttpSession(object):
    """Connection-reusing HTTP client, safe to share across threads."""

    def __init__(self):
        """Idle connections per (scheme, host, port).

        Handing a connection out removes it from the pool, so two threads can
        never hold the same one -- which is the whole of what http.client needs
        from us, since a connection is only unsafe while it is in use.
        """
        self._idle = {}
        self._lock = threading.Lock()
        self._context = None

    def get(self, url, timeout=DEFAULT_TIMEOUT, headers=None):
        return self.request("GET", url, timeout=timeout, headers=headers)

    def head(self, url, timeout=DEFAULT_TIMEOUT, headers=None):
        return self.request("HEAD", url, timeout=timeout, headers=headers)

    def close(self):
        with self._lock:
            connections = [c for pool in self._idle.values() for c in pool]
            self._idle.clear()

        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass

    def request(self, method, url, timeout=DEFAULT_TIMEOUT, headers=None):
        """One request, following redirects.

        Redirects are followed because the callers assume it: OMDb is reached
        over http and YouTube's thumbnail host answers a HEAD with a redirect
        often enough to matter, and requests followed both without being asked.
        """
        for _ in range(MAX_REDIRECTS + 1):
            response = self._send(method, url, timeout, headers)

            if response.status_code not in (301, 302, 303, 307, 308):
                return response

            location = response.headers.get("location")

            if not location:
                return response

            url = urljoin(url, location)

            """ 303, and 302 in practice, turn a non-GET into a GET. Only HEAD
                and GET are ever sent here, so the only case is HEAD -> HEAD,
                which is what we want anyway.
            """

        return response

    def _send(self, method, url, timeout, headers):
        parts = urlsplit(url)
        key = (parts.scheme, parts.hostname, parts.port)

        target = parts.path or "/"
        if parts.query:
            target = "%s?%s" % (target, parts.query)

        request_headers = {
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "User-Agent": USER_AGENT,
        }
        if headers:
            request_headers.update(headers)

        """ Two attempts, and the second only when the first used a pooled
            connection. A server is free to have dropped a keep-alive
            connection since it was last used, and http.client surfaces that as
            an exception on the *next* request rather than at close time. That
            is precisely the case urllib3 handled invisibly, so doing without
            it means handling it here; a fresh connection failing is a real
            failure and is raised.
        """
        last_error = None

        for attempt in (1, 2):
            connection, reused = self._checkout(key, timeout)

            try:
                connection.request(method, target, headers=request_headers)
                raw = connection.getresponse()
                body = raw.read()

                response = Response(
                    raw.status,
                    _decode(body, raw.getheader("Content-Encoding")),
                    url,
                    {name.lower(): value for name, value in raw.getheaders()},
                )

                if raw.will_close:
                    self._discard(connection)
                else:
                    self._checkin(key, connection)

                return response

            except Exception as error:
                last_error = error
                self._discard(connection)

                if not reused:
                    raise

        raise last_error

    def _checkout(self, key, timeout):
        """A connection for `key`, and whether it came from the pool."""
        with self._lock:
            pool = self._idle.get(key)

            if pool:
                return pool.pop(), True

        scheme, host, port = key
        client, ssl_module = _transport()

        if scheme == "https":
            if self._context is None:
                self._context = ssl_module.create_default_context()

            connection = client.HTTPSConnection(
                host, port, timeout=timeout, context=self._context
            )
        else:
            connection = client.HTTPConnection(host, port, timeout=timeout)

        return connection, False

    def _checkin(self, key, connection):
        with self._lock:
            pool = self._idle.setdefault(key, [])

            if len(pool) < MAX_IDLE_PER_HOST:
                pool.append(connection)
                return

        self._discard(connection)

    @staticmethod
    def _discard(connection):
        try:
            connection.close()
        except Exception:
            pass
