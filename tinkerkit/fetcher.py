import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from dataclasses import dataclass
lazy import codecs
lazy import concurrent.futures
lazy import ssl
lazy import time
lazy import zlib

from .settings import FETCH_USER_AGENT, MAX_REDIRECTS
from .text import first_line

RawResponse = namedtuple("RawResponse", "status headers body")


@dataclass
class Fetched:
    url: str
    status: int | None = None
    final_url: str = ""
    text: str = ""
    kind: str = ""
    error: str = ""
    data: bytes = b""
    seconds: float = 0.0

    @property
    def ok(self):
        return not self.error


class Fetcher:
    META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)
    REDIRECTS = (301, 302, 303, 307, 308)

    def __init__(self, workers, timeout, max_bytes, user_agent=FETCH_USER_AGENT):
        self.workers, self.timeout, self.max_bytes, self.user_agent = workers, timeout, max_bytes, user_agent

    def fetch_all(self, urls):
        sites = {}
        for index, url in enumerate(urls):
            sites.setdefault(origin(url), []).append(index)
        results = [None] * len(urls)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(self.workers, len(sites) or 1))
        ) as pool:
            fetched_sites = pool.map(
                lambda found: self.fetch_site([urls[at] for at in found]), sites.values()
            )
            for indexes, got in zip(sites.values(), fetched_sites):
                for index, fetched in zip(indexes, got):
                    results[index] = fetched
        return results

    def fetch_site(self, urls):
        with Connections(self.timeout) as connections:
            return [self.timed(url, connections) for url in urls]

    def timed(self, url, connections):
        started = time.monotonic()
        fetched = self.fetched(url, connections)
        fetched.seconds = time.monotonic() - started
        return fetched

    def fetched(self, url, connections):
        try:
            return self.through_proxy(url) if behind_proxy(url) else self.request(url, connections)
        except Exception as exc:
            return Fetched(url, final_url=url, error=f"{type(exc).__name__}: {first_line(str(exc), 160)}")

    def request(self, url, connections):
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            status, headers, body = connections.exchange(target, self.asked(), self.max_bytes + 1)
            if status not in self.REDIRECTS or not headers.get("Location"):
                return self.answered(url, target, status, headers, body)
            target = urllib.parse.urljoin(target, headers["Location"])
        return Fetched(url, status, target, error=f"HTTP {status} (more than {MAX_REDIRECTS} redirects)")

    def asked(self):
        return {"User-Agent": self.user_agent, "Accept-Language": "en", "Accept-Encoding": "gzip, deflate"}

    def answered(self, url, final_url, status, headers, body):
        if status >= 400:
            return Fetched(url, status, final_url, error=f"HTTP {status}")
        data = unzipped(body, headers.get("Content-Encoding", ""), self.max_bytes + 1)
        return self.read(url, status, final_url, data, headers)

    def through_proxy(self, url):
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept-Language": "en"}
        )
        try:
            with urllib.request.build_opener(LimitedRedirects).open(
                request, timeout=self.timeout
            ) as response:
                data = response.read(self.max_bytes + 1)
                return self.read(url, response.status, response.geturl(), data, response.headers)
        except urllib.error.HTTPError as exc:
            return Fetched(url, exc.code, exc.geturl() or url, error=f"HTTP {exc.code}")

    def read(self, url, status, final_url, data, headers):
        if len(data) > self.max_bytes:
            return Fetched(url, status, final_url, error=f"larger than {self.max_bytes} bytes")
        text = self.decoded(data, headers.get_content_charset())
        return Fetched(url, status, final_url, text, (headers.get_content_type() or "").lower(), data=data)

    @classmethod
    def decoded(cls, body, charset):
        found = cls.META_CHARSET.search(body[:4096])
        for name in (charset, found.group(1).decode("ascii", "replace") if found else None, "utf-8"):
            if name and known_codec(name):
                return body.decode(name, errors="replace")
        return body.decode("utf-8", errors="replace")


class Connections:
    DROPPED = (
        http.client.RemoteDisconnected,
        http.client.CannotSendRequest,
        ConnectionResetError,
        BrokenPipeError,
    )

    def __init__(self, timeout):
        self.timeout, self.open = timeout, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for connection in self.open.values():
            connection.close()
        return False

    def exchange(self, url, headers, max_bytes):
        parts = urllib.parse.urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            return self.response(self.connection_to(parts), path, headers, max_bytes)
        except self.DROPPED:
            self.open.pop((parts.scheme, parts.netloc)).close()
            return self.response(self.connection_to(parts), path, headers, max_bytes)

    def connection_to(self, parts):
        key = (parts.scheme, parts.netloc)
        if key not in self.open:
            self.open[key] = (
                http.client.HTTPSConnection(
                    parts.netloc, timeout=self.timeout, context=ssl.create_default_context()
                )
                if parts.scheme == "https"
                else http.client.HTTPConnection(parts.netloc, timeout=self.timeout)
            )
        return self.open[key]

    @staticmethod
    def response(connection, path, headers, max_bytes):
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(max_bytes)
        body_left_unread = not response.isclosed()
        if body_left_unread:
            connection.close()
        return RawResponse(response.status, response.headers, body)


class LimitedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS


def unzipped(body, encoding, max_bytes):
    encoding = encoding.lower().strip()
    if encoding not in ("gzip", "x-gzip", "deflate"):
        return body
    for window in (16 + zlib.MAX_WBITS,) if "gzip" in encoding else (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompressobj(window).decompress(body, max_bytes)
        except zlib.error:
            continue
    return body


def origin(url):
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def behind_proxy(url):
    parts = urllib.parse.urlsplit(url)
    return bool(urllib.request.getproxies().get(parts.scheme)) and not urllib.request.proxy_bypass(
        parts.hostname or ""
    )


def known_codec(name):
    try:
        codecs.lookup(name)
        return True
    except LookupError:
        return False
