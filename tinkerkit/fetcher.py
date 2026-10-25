import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import namedtuple
from dataclasses import asdict, dataclass
lazy import codecs
lazy import concurrent.futures
lazy import ssl
lazy import threading
lazy import time
lazy import zlib

from .settings import FETCH_USER_AGENT, MAX_REDIRECTS, first_line
lazy from .jobs import journaled

Download = namedtuple("Download", "data why_failed")
RawResponse = namedtuple("RawResponse", "status headers body")


@dataclass
class Fetched:
    url: str
    status: int | None = None
    final_url: str = ""
    text: str = ""
    kind: str = ""
    error: str = ""
    ROBOTS = "its robots.txt does not allow it"

    @property
    def ok(self):
        return not self.error

    @property
    def refused(self):
        return self.error == self.ROBOTS


class Fetcher:
    META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)
    REDIRECTS = (301, 302, 303, 307, 308)

    def __init__(self, workers, timeout, max_bytes, domain_delay, log=None, user_agent=FETCH_USER_AGENT):
        self.workers, self.timeout, self.max_bytes, self.domain_delay = (
            workers,
            timeout,
            max_bytes,
            domain_delay,
        )
        self.log, self.user_agent = log, user_agent
        self.robots_by_site = {}
        self.next_turn, self.turn_lock = {}, threading.Lock()

    def fetch_all(self, urls, kind="fetch"):
        sites = {}
        for index, url in enumerate(urls):
            sites.setdefault(origin(url), []).append(index)
        results = [None] * len(urls)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(self.workers, len(sites) or 1))
        ) as pool:
            fetched_sites = pool.map(
                lambda found: self.fetch_site([urls[at] for at in found], kind), sites.values()
            )
            for indexes, got in zip(sites.values(), fetched_sites):
                for index, fetched in zip(indexes, got):
                    results[index] = fetched
        return results

    def fetch_site(self, urls, kind):
        with Connections(self.timeout) as connections:
            return [self.fetch(url, connections, kind) for url in urls]

    def fetch(self, url, connections, kind):
        return journaled(
            {"page": url, "kind": kind},
            lambda: self.fetched(url, connections, kind),
            keep=lambda fetched: fetched.ok,
            dump=asdict,
            load=lambda kept: Fetched(**kept),
        )

    def fetched(self, url, connections, kind):
        try:
            if not self.allowed(url, connections):
                return Fetched(url, final_url=url, error=Fetched.ROBOTS)
            return self.get(url, connections, kind)
        except Exception as exc:
            return Fetched(url, final_url=url, error=f"{type(exc).__name__}: {first_line(str(exc), 160)}")

    def allowed(self, url, connections):
        site = origin(url)
        if site not in self.robots_by_site:
            self.robots_by_site[site] = self.read_robots(site, connections)
        return self.robots_by_site[site].can_fetch(self.user_agent, url)

    def read_robots(self, site, connections):
        rules = urllib.robotparser.RobotFileParser(site + "/robots.txt")
        got = self.get(site + "/robots.txt", connections, kind="robots")
        if got.status in (401, 403):
            rules.disallow_all = True
        elif got.ok:
            rules.parse(got.text.splitlines())
        else:
            rules.allow_all = True
        return rules

    def get(self, url, connections, kind="fetch"):
        self.wait_turn(url)
        started = time.monotonic()
        fetched = self.through_proxy(url) if behind_proxy(url) else self.request(url, connections)
        if self.log:
            self.log(kind, self.result_of(fetched, kind), time.monotonic() - started, url)
        return fetched

    @staticmethod
    def result_of(fetched, kind):
        if fetched.ok:
            return "ok"
        if fetched.status == 429:
            return "429"
        return "missing" if kind in ("robots", "probe") and fetched.status == 404 else "failed"

    def wait_turn(self, url):
        host = urllib.parse.urlsplit(url).netloc
        with self.turn_lock:
            now = time.monotonic()
            turn = max(now, self.next_turn.get(host, 0.0))
            self.next_turn[host] = turn + self.domain_delay
        if turn > now:
            time.sleep(turn - now)

    def request(self, url, connections):
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            status, headers, body = connections.exchange(target, self.asked(), self.max_bytes)
            if status not in self.REDIRECTS or not headers.get("Location"):
                return self.answered(url, target, status, headers, body)
            target = urllib.parse.urljoin(target, headers["Location"])
        return Fetched(url, status, target, error=f"HTTP {status} (more than {MAX_REDIRECTS} redirects)")

    def asked(self):
        return {"User-Agent": self.user_agent, "Accept-Language": "en", "Accept-Encoding": "gzip, deflate"}

    def answered(self, url, final_url, status, headers, body):
        if status >= 400:
            return Fetched(url, status, final_url, error=f"HTTP {status}")
        text = self.decoded(
            unzipped(body, headers.get("Content-Encoding", ""), self.max_bytes), headers.get_content_charset()
        )
        return Fetched(url, status, final_url, text, (headers.get_content_type() or "").lower())

    def through_proxy(self, url):
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept-Language": "en"}
        )
        try:
            with urllib.request.build_opener(LimitedRedirects).open(
                request, timeout=self.timeout
            ) as response:
                body = response.read(self.max_bytes)
                return Fetched(
                    url,
                    response.status,
                    response.geturl(),
                    self.decoded(body, response.headers.get_content_charset()),
                    (response.headers.get_content_type() or "").lower(),
                )
        except urllib.error.HTTPError as exc:
            return Fetched(url, exc.code, exc.geturl() or url, error=f"HTTP {exc.code}")

    def download(self, url, max_bytes):
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.build_opener(LimitedRedirects).open(
                request, timeout=self.timeout
            ) as response:
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            return Download(b"", f"HTTP {exc.code}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Download(b"", first_line(str(getattr(exc, "reason", exc)), 160))
        if len(body) > max_bytes:
            return Download(b"", f"it is larger than {max_bytes // 1_000_000} MB")
        return Download(body, "")

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
