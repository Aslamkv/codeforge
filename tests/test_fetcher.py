import gzip
import http.server
import socket
import threading
import time

import pytest

from tinkerkit.fetcher import Fetcher
from tinkerkit.settings import MAX_REDIRECTS

KEEP_ALIVE_PROTOCOL = "HTTP/1.1"
REAL_CONNECT = socket.socket.connect


class Site:
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


def fetcher(max_bytes=100_000):
    return Fetcher(workers=4, timeout=5, max_bytes=max_bytes)


@pytest.mark.spec("FET-1")
def test_sites_are_fetched_in_parallel_in_order_and_one_failure_blocks_none(site):
    together = threading.Barrier(2, timeout=3)
    one, other = (
        site({"/together": (200, HTML, b"<p>a</p>")}, together),
        site({"/together": (200, HTML, b"<p>b</p>")}, together),
    )
    got = fetcher().fetch_all(
        [f"{one.base}/together", f"{one.base}/gone", "http://127.0.0.1:1/refused", f"{other.base}/together"]
    )
    assert [fetched.text for fetched in got] == ["<p>a</p>", "", "", "<p>b</p>"]
    assert (
        got[0].ok
        and got[0].status == 200
        and got[0].kind == "text/html"
        and got[0].data == b"<p>a</p>"
        and got[0].final_url.endswith("/together")
    )
    assert got[1].status == 404 and got[1].error == "HTTP 404" and got[1].data == b""
    assert got[2].status is None and got[2].error
    assert all(agent == Fetcher(1, 1, 1).user_agent for _, agent, _ in one.seen)


@pytest.mark.spec("FET-1")
def test_one_sites_pages_share_one_kept_alive_connection_and_come_gzipped(site):
    web = site(
        {
            "/a": (200, HTML, b"<p>a</p>"),
            "/b": (200, {**HTML, "Content-Encoding": "gzip"}, gzip.compress(b"<p>b</p>")),
            "/c": (200, HTML, b"<p>c</p>"),
        }
    )
    got = fetcher().fetch_all([f"{web.base}/a", f"{web.base}/b", f"{web.base}/c"])
    assert [fetched.text for fetched in got] == ["<p>a</p>", "<p>b</p>", "<p>c</p>"]
    assert got[1].data == b"<p>b</p>"
    assert len(web.clients) == 1 and web.paths() == ["/a", "/b", "/c"]


@pytest.mark.spec("FET-1")
def test_text_is_decoded_by_its_charset_and_a_page_over_max_bytes_fails(site):
    web = site(
        {
            "/latin": (200, {"Content-Type": "text/html; charset=iso-8859-1"}, "café".encode("latin-1")),
            "/meta": (
                200,
                {"Content-Type": "text/html"},
                '<meta charset="windows-1252"><p>naïve</p>'.encode("cp1252"),
            ),
            "/plain": (200, {"Content-Type": "text/plain"}, "über".encode()),
            "/big": (200, HTML, b"x" * 5000),
            "/big-zipped": (200, {**HTML, "Content-Encoding": "gzip"}, gzip.compress(b"x" * 5000)),
        }
    )
    got = fetcher(max_bytes=1000).fetch_all(
        [f"{web.base}/{path}" for path in ("latin", "meta", "plain", "big", "big-zipped")]
    )
    assert (
        got[0].text == "café"
        and "naïve" in got[1].text
        and got[2].text == "über"
        and got[2].data == "über".encode()
        and got[2].kind == "text/plain"
    )
    for big in got[3:]:
        assert not big.ok and big.error == "larger than 1000 bytes" and big.data == b"" and big.text == ""


@pytest.mark.spec("FET-1")
def test_redirects_are_followed_up_to_the_limit(site):
    hops = {
        f"/hop{number}": (302, {"Location": f"/hop{number + 1}"}, b"") for number in range(MAX_REDIRECTS + 2)
    }
    web = site({"/old": (301, {"Location": "/new"}, b""), "/new": (200, HTML, b"<p>new</p>"), **hops})
    moved, looping = fetcher().fetch_all([f"{web.base}/old", f"{web.base}/hop0"])
    assert moved.ok and moved.final_url == f"{web.base}/new" and moved.text == "<p>new</p>"
    assert not looping.ok and looping.error.startswith("HTTP 30")
    assert f"/hop{MAX_REDIRECTS + 1}" not in web.paths()


@pytest.mark.spec("FET-1")
def test_each_page_says_how_long_it_took(site):
    web = site({"/a": (200, HTML, b"a")})
    assert all(
        fetched.seconds >= 0 for fetched in fetcher().fetch_all([f"{web.base}/a", "http://127.0.0.1:1/"])
    )
