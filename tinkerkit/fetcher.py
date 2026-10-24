"""Web pages fetched from their own servers with the standard library only: the pages of one site one after
another over one kept-alive connection, the sites in parallel. Each site's robots.txt is honoured, one site is
asked at most once per domain delay, redirects and bytes are bounded, gzip is asked for, and the text is decoded
by its charset. One page failing never stops the others. (Measured on docs.python.org, 12 pages: a new
connection per page with a 0.5s delay took 6.5s; one kept-alive connection took 0.2s.)"""

lazy import codecs
lazy import concurrent.futures
import http.client
import re
lazy import ssl
lazy import threading
lazy import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
lazy import zlib
from dataclasses import asdict, dataclass

lazy from .jobs import journaled
from .settings import FETCH_USER_AGENT, MAX_REDIRECTS, first_line


@dataclass
class Fetched:
    """What one address gave: its HTTP status (None when there was no answer), the address it ended at after
    redirects, its text (decoded), its content type, and why it failed ("" when it did not)."""
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
        """Whether the site's robots.txt refused it (then nothing else may fetch it either)."""
        return self.error == self.ROBOTS


class Fetcher:
    """Fetches pages: up to workers sites at once, each page within timeout seconds and max_bytes, with a clear
    User-Agent. log(kind, result, seconds, url), if given, hears of every request: kind "fetch", "robots" or
    "probe" (an address that may not exist: a site map, a version's documentation), result "ok", "429",
    "missing" (a robots.txt or a probe the site does not have) or "failed"."""
    META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)
    REDIRECTS = (301, 302, 303, 307, 308)

    def __init__(self, workers, timeout, max_bytes, domain_delay, log=None, user_agent=FETCH_USER_AGENT):
        self.workers, self.timeout, self.max_bytes, self.domain_delay = workers, timeout, max_bytes, domain_delay
        self.log, self.user_agent = log, user_agent
        self.robots = {}  # site -> its robots.txt rules, read once
        self.next_turn, self.turn_lock = {}, threading.Lock()

    def fetch_all(self, urls, kind="fetch"):
        """What each address gave (Fetched), in the order given: each site's pages over one connection, the
        sites in parallel. kind: how they are logged."""
        sites = {}
        for index, url in enumerate(urls):
            sites.setdefault(site_of(url), []).append(index)
        results = [None] * len(urls)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(self.workers, len(sites) or 1))) as pool:
            fetched_sites = pool.map(lambda found: self.fetch_site([urls[at] for at in found], kind), sites.values())
            for indexes, got in zip(sites.values(), fetched_sites):
                for index, fetched in zip(indexes, got):
                    results[index] = fetched
        return results

    def fetch_site(self, urls, kind):
        """One site's pages, one after another over one kept-alive connection."""
        with Connections(self.timeout) as connections:
            return [self.fetch(url, connections, kind) for url in urls]

    def fetch(self, url, connections, kind):
        """What the address gave; in a job, a page it fetched before is given from its journal (a failure is
        fetched again)."""
        return journaled({"page": url, "kind": kind}, lambda: self.fetched(url, connections, kind),
                         keep=lambda fetched: fetched.ok, dump=asdict, load=lambda kept: Fetched(**kept))

    def fetched(self, url, connections, kind):
        try:
            if not self.allowed(url, connections):
                return Fetched(url, final_url=url, error=Fetched.ROBOTS)
            return self.get(url, connections, kind)
        except Exception as exc:  # one page's trouble never stops the others
            return Fetched(url, final_url=url, error=f"{type(exc).__name__}: {first_line(str(exc), 160)}")

    def allowed(self, url, connections):
        """Whether the site's robots.txt lets this User-Agent fetch the address (read once per site)."""
        site = site_of(url)
        if site not in self.robots:
            self.robots[site] = self.read_robots(site, connections)
        return self.robots[site].can_fetch(self.user_agent, url)

    def read_robots(self, site, connections):
        """A site's robots.txt rules, read as urllib.robotparser reads them: refused (401, 403) allows
        nothing; missing, or a site that does not answer it, allows everything (its pages then fail on their own)."""
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
        """One request, in the site's turn, logged."""
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
        """Waits until the site may be asked again: one request per domain delay to each site."""
        host = urllib.parse.urlsplit(url).netloc
        with self.turn_lock:
            now = time.monotonic()
            turn = max(now, self.next_turn.get(host, 0.0))
            self.next_turn[host] = turn + self.domain_delay
        if turn > now:
            time.sleep(turn - now)

    def request(self, url, connections):
        """The page, following at most MAX_REDIRECTS redirects (to any site, over its own connection)."""
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
        text = self.decoded(unzipped(body, headers.get("Content-Encoding", ""), self.max_bytes),
                            headers.get_content_charset())
        return Fetched(url, status, final_url, text, (headers.get_content_type() or "").lower())

    def through_proxy(self, url):
        """The page through the proxy the environment sets (urllib honours it; a kept-alive connection would not)."""
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Language": "en"})
        try:
            with urllib.request.build_opener(LimitedRedirects).open(request, timeout=self.timeout) as response:
                body = response.read(self.max_bytes)
                return Fetched(url, response.status, response.geturl(),
                               self.decoded(body, response.headers.get_content_charset()),
                               (response.headers.get_content_type() or "").lower())
        except urllib.error.HTTPError as exc:  # a redirect past MAX_REDIRECTS is one too
            return Fetched(url, exc.code, exc.geturl() or url, error=f"HTTP {exc.code}")

    def download(self, url, max_bytes):
        """(bytes, why it failed) of a file at a web address (a book: binary, never decoded), at most max_bytes, through
        the proxy the environment sets; redirects are followed (at most MAX_REDIRECTS)."""
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.build_opener(LimitedRedirects).open(request, timeout=self.timeout) as response:
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            return b"", f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return b"", first_line(str(getattr(exc, "reason", exc)), 160)
        if len(body) > max_bytes:
            return b"", f"it is larger than {max_bytes // 1_000_000} MB"
        return body, ""

    @classmethod
    def decoded(cls, body, charset):
        """The body as text, in the charset its headers name, else the one its <meta> names, else UTF-8."""
        found = cls.META_CHARSET.search(body[:4096])
        for name in (charset, found.group(1).decode("ascii", "replace") if found else None, "utf-8"):
            if name and known_codec(name):
                return body.decode(name, errors="replace")
        return body.decode("utf-8", errors="replace")


class Connections:
    """One worker's kept-alive connections, by site (a redirect may lead to another site), closed at the end."""
    DROPPED = (http.client.RemoteDisconnected, http.client.CannotSendRequest, ConnectionResetError, BrokenPipeError)

    def __init__(self, timeout):
        self.timeout, self.open = timeout, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for connection in self.open.values():
            connection.close()
        return False

    def exchange(self, url, headers, max_bytes):
        """(status, headers, body of at most max_bytes) of a GET; a kept-alive connection the site closed
        meanwhile is opened again once."""
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
            self.open[key] = (http.client.HTTPSConnection(parts.netloc, timeout=self.timeout,
                                                          context=ssl.create_default_context())
                              if parts.scheme == "https" else
                              http.client.HTTPConnection(parts.netloc, timeout=self.timeout))
        return self.open[key]

    @staticmethod
    def response(connection, path, headers, max_bytes):
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(max_bytes)
        if not response.isclosed():  # a body left unread spoils the connection: the next request opens a new one
            connection.close()
        return response.status, response.headers, body


class LimitedRedirects(urllib.request.HTTPRedirectHandler):
    """Follows at most MAX_REDIRECTS redirects for one page."""
    max_redirections = MAX_REDIRECTS


def unzipped(body, encoding, max_bytes):
    """The body as the site sent it before compressing (gzip or deflate), at most max_bytes; a body cut short
    gives what it holds."""
    encoding = encoding.lower().strip()
    if encoding not in ("gzip", "x-gzip", "deflate"):
        return body
    for window in ((16 + zlib.MAX_WBITS,) if "gzip" in encoding else (zlib.MAX_WBITS, -zlib.MAX_WBITS)):
        try:
            return zlib.decompressobj(window).decompress(body, max_bytes)
        except zlib.error:
            continue
    return body


def site_of(url):
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def behind_proxy(url):
    """Whether the environment sends this address through a proxy."""
    parts = urllib.parse.urlsplit(url)
    return bool(urllib.request.getproxies().get(parts.scheme)) and not urllib.request.proxy_bypass(parts.hostname or "")


def known_codec(name):
    try:
        codecs.lookup(name)
        return True
    except LookupError:
        return False
