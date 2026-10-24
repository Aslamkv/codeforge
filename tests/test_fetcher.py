"""The page fetcher, against a local web site (http.server on 127.0.0.1): no real internet."""

import gzip
import http.server
import socket
import threading
import time

import pytest

from tinkerkit.fetcher import Fetched, Fetcher
from tinkerkit.settings import MAX_REDIRECTS

KEEP_ALIVE_PROTOCOL = "HTTP/1.1"
REAL_CONNECT = socket.socket.connect


class Site:
    """A local web site: routes {path: (status, headers, body)}; it keeps each request's path, User-Agent and time."""

    def __init__(self, routes, barrier=None):
        self.routes, self.seen, self.barrier, self.clients = routes, [], barrier, set()
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = KEEP_ALIVE_PROTOCOL

            def do_GET(self):
                site.seen.append((self.path, self.headers.get("User-Agent"), time.monotonic()))
                site.clients.add(self.client_address)
                if site.barrier and self.path.startswith("/together"):
                    site.barrier.wait()
                status, headers, body = site.routes.get(self.path, (404, {}, b"not here"))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def paths(self):
        return [path for path, _, _ in self.seen]


@pytest.fixture
def site(monkeypatch):
    """A local site; connections to 127.0.0.1 are allowed, everything else stays off."""
    def local_only(sock, address):
        if address[0] != "127.0.0.1":
            raise OSError(f"network is off in tests ({address})")
        return REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", local_only)
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    made = []

    def make(routes, barrier=None):
        made.append(Site(routes, barrier))
        return made[-1]
    yield make
    for each in made:
        each.server.shutdown()


HTML = {"Content-Type": "text/html; charset=utf-8"}


def fetcher(**given):
    settings = {"workers": 4, "timeout": 5, "max_bytes": 100_000, "domain_delay": 0.0, **given}
    return Fetcher(settings.pop("workers"), settings.pop("timeout"), settings.pop("max_bytes"),
                   settings.pop("domain_delay"), **settings)


@pytest.mark.spec("LRN-4")
def test_sites_are_fetched_in_parallel_in_order_and_one_failure_blocks_none(site):
    together = threading.Barrier(2, timeout=3)
    one, other = site({"/together": (200, HTML, b"<p>a</p>")}, together), site({"/together": (200, HTML, b"<p>b</p>")}, together)
    logged = []
    got = fetcher(log=lambda *entry: logged.append(entry)).fetch_all(
        [f"{one.base}/together", f"{one.base}/gone", "http://127.0.0.1:1/refused", f"{other.base}/together"])
    assert [fetched.text for fetched in got] == ["<p>a</p>", "", "", "<p>b</p>"]
    assert got[0].ok and got[0].status == 200 and got[0].kind == "text/html" and got[0].final_url.endswith("/together")
    assert got[1].status == 404 and got[1].error == "HTTP 404"
    assert got[2].status is None and got[2].error
    assert {(kind, result) for kind, result, _, _ in logged} >= {("fetch", "ok"), ("fetch", "failed"), ("robots", "missing")}
    assert all(agent == Fetcher(1, 1, 1, 0).user_agent for _, agent, _ in one.seen)


@pytest.mark.spec("LRN-4")
def test_one_sites_pages_share_one_kept_alive_connection_and_come_gzipped(site):
    web = site({"/a": (200, HTML, b"<p>a</p>"), "/b": (200, {**HTML, "Content-Encoding": "gzip"}, gzip.compress(b"<p>b</p>")),
                "/c": (200, HTML, b"<p>c</p>")})
    got = fetcher().fetch_all([f"{web.base}/a", f"{web.base}/b", f"{web.base}/c"])
    assert [fetched.text for fetched in got] == ["<p>a</p>", "<p>b</p>", "<p>c</p>"]
    assert len(web.clients) == 1 and web.paths() == ["/robots.txt", "/a", "/b", "/c"]
    logged = []
    fetcher(log=lambda *entry: logged.append(entry)).fetch_all([f"{web.base}/llms.txt"], kind="probe")
    assert [(kind, result) for kind, result, _, _ in logged] == [("robots", "missing"), ("probe", "missing")]


@pytest.mark.spec("LRN-4")
def test_text_is_decoded_by_its_charset_and_cut_at_max_bytes(site):
    web = site({"/latin": (200, {"Content-Type": "text/html; charset=iso-8859-1"}, "café".encode("latin-1")),
                "/meta": (200, {"Content-Type": "text/html"},
                          '<meta charset="windows-1252"><p>naïve</p>'.encode("cp1252")),
                "/plain": (200, {"Content-Type": "text/plain"}, "über".encode("utf-8")),
                "/big": (200, HTML, b"x" * 5000)})
    got = fetcher(max_bytes=1000).fetch_all([f"{web.base}/{path}" for path in ("latin", "meta", "plain", "big")])
    assert got[0].text == "café" and "naïve" in got[1].text and got[2].text == "über" and got[2].kind == "text/plain"
    assert len(got[3].text) == 1000


@pytest.mark.spec("LRN-4")
def test_redirects_are_followed_up_to_the_limit(site):
    hops = {f"/hop{number}": (302, {"Location": f"/hop{number + 1}"}, b"") for number in range(MAX_REDIRECTS + 2)}
    web = site({"/old": (301, {"Location": "/new"}, b""), "/new": (200, HTML, b"<p>new</p>"), **hops})
    moved, looping = fetcher().fetch_all([f"{web.base}/old", f"{web.base}/hop0"])
    assert moved.ok and moved.final_url == f"{web.base}/new" and moved.text == "<p>new</p>"
    assert not looping.ok and looping.error.startswith("HTTP 30")
    assert f"/hop{MAX_REDIRECTS + 1}" not in web.paths()


@pytest.mark.spec("LRN-4")
def test_one_site_is_asked_once_per_domain_delay(site):
    web = site({"/a": (200, HTML, b"a"), "/b": (200, HTML, b"b")})
    fetcher(domain_delay=0.2).fetch_all([f"{web.base}/a", f"{web.base}/b"])
    times = sorted(at for _, _, at in web.seen)
    robots_then_the_two_pages = 3
    assert len(times) == robots_then_the_two_pages and all(later - earlier >= 0.19 for earlier, later in zip(times, times[1:]))


@pytest.mark.spec("LRN-5")
def test_robots_txt_is_honoured(site):
    web = site({"/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /private\n"),
                "/public": (200, HTML, b"open"), "/private/page": (200, HTML, b"secret")})
    public, private = fetcher().fetch_all([f"{web.base}/public", f"{web.base}/private/page"])
    assert public.ok and private.refused and private.error == Fetched.ROBOTS
    read_once_per_site = web.paths().count("/robots.txt") == 1
    assert "/private/page" not in web.paths() and read_once_per_site


@pytest.mark.spec("LRN-5")
def test_a_missing_robots_txt_allows_all_and_a_refused_one_allows_none(site):
    open_site = site({"/page": (200, HTML, b"open")})
    closed_site = site({"/robots.txt": (403, {}, b"no"), "/page": (200, HTML, b"open")})
    allowed, refused = fetcher().fetch_all([f"{open_site.base}/page", f"{closed_site.base}/page"])
    assert allowed.ok and refused.refused and "/page" not in closed_site.paths()
