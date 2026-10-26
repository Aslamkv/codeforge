import io
import json
import threading
import time
import urllib.error

import pytest

from tinkerkit.fetcher import Fetched, Fetcher
from tinkerkit.jobs import Jobs
from tinkerkit.search import Mwmbl, SearchFailed, Wikibooks, Wikipedia
from tinkerkit.settings import SEARCHES_PER_ENGINE, Config, Home
from tinkerkit.web import Internet, RequestLog, WebPage


class Engine:
    def __init__(self, name, *answers):
        self.name, self.answers, self.asked = name, list(answers), []

    def search(self, query, count):
        self.asked.append(query)
        answer = self.answer(query)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def answer(self, query):
        return self.answers.pop(0)


class Echo(Engine):
    def __init__(self, name, pause=0.0):
        super().__init__(name)
        self.pause, self.running, self.most, self.lock = pause, 0, 0, threading.Lock()

    def answer(self, query):
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(self.pause)
        with self.lock:
            self.running -= 1
        return [found(f"{self.name}/{query}")]


def found(path, title="T"):
    return {"title": title, "url": f"https://{path}", "content": "c"}


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


NO_ROUTE = urllib.error.URLError(OSError("no route"))
FOUND = [found("x.org")]


@pytest.mark.spec("WEB-1")
def test_internet_searches_with_mwmbl_wikipedia_and_wikibooks():
    Config.save(web_timeout=7)
    engines = Internet().engines
    assert [type(engine) for engine in engines] == [Mwmbl, Wikipedia, Wikibooks]
    assert all(engine.timeout == 7.0 for engine in engines)
    assert Internet.state() == "searches mwmbl, wikipedia, wikibooks"


@pytest.mark.spec("WEB-3")
def test_search_interleaves_the_engines_results_by_rank():
    first = Engine("a", [found("a1"), found("same"), found("a3")])
    second = Engine("b", [found("same", "B"), found("b2")])
    results = Internet([first, second]).search("q", 4)
    assert [result["url"] for result in results] == ["https://a1", "https://same", "https://b2", "https://a3"]
    assert results[1]["title"] == "B" and first.asked == second.asked == ["q"]


@pytest.mark.spec("WEB-3")
def test_search_keeps_web_addresses_as_strings():
    answer = [
        {"title": "T", "url": "https://x.org", "content": None},
        {"url": "ftp://x"},
        "junk",
        {"title": 5, "url": "http://y.org"},
        {"url": "https://z.org"},
    ]
    assert Internet([Engine("a", answer)]).search("q", 2) == [
        {"title": "T", "url": "https://x.org", "content": ""},
        {"title": "5", "url": "http://y.org", "content": ""},
    ]


@pytest.mark.spec("WEB-3")
def test_search_asks_every_engine_at_once_with_no_pause(monkeypatch):
    together = threading.Barrier(3, timeout=5)

    class Waiting(Engine):
        def answer(self, query):
            together.wait()
            return [found(self.name)]

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    engines = [Waiting(name) for name in ("a", "b", "c")]
    assert len(Internet(engines).search("q", 5)) == 3 and slept == []


@pytest.mark.spec("WEB-3")
@pytest.mark.parametrize(
    "trouble", [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out")), http_error(503)]
)
def test_an_engine_that_times_out_or_fails_on_the_server_is_asked_once_more(trouble):
    engine = Engine("a", trouble, FOUND)
    assert Internet([engine]).search("q", 3) == FOUND and engine.asked == ["q", "q"]
    engine = Engine("a", trouble, trouble)
    assert Internet([engine]).search("q", 3) == [] and engine.asked == ["q", "q"]


@pytest.mark.spec("WEB-3")
@pytest.mark.parametrize(
    "trouble", [http_error(404), http_error(429), SearchFailed("no JSON"), KeyError("oops")]
)
def test_any_other_failure_gives_nothing_from_that_engine(trouble):
    failing, answering = Engine("a", trouble), Engine("b", FOUND)
    assert Internet([failing, answering]).search("q", 3) == FOUND
    assert failing.asked == ["q"] and Internet([Engine("a", trouble)]).search("q", 3) == []


@pytest.mark.spec("WEB-3")
def test_search_all_gives_each_querys_results_in_order():
    engines = [Echo("a"), Echo("b")]
    assert Internet(engines).search_all(["q", "r"], 3) == [
        [found("a/q"), found("b/q")],
        [found("a/r"), found("b/r")],
    ]


@pytest.mark.spec("WEB-3")
def test_at_most_searches_per_engine_go_to_one_engine_at_a_time():
    engine = Echo("a", pause=0.05)
    queries = [f"q{number}" for number in range(SEARCHES_PER_ENGINE * 3)]
    results = Internet([engine]).search_all(queries, 3)
    assert results == [[found(f"a/{query}")] for query in queries]
    assert engine.most == SEARCHES_PER_ENGINE


class Pages:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def fetch_all(self, urls):
        self.asked.append(list(urls))
        return [self.pages[url] for url in urls]


def reading(monkeypatch, pages):
    fetcher, sizes = Pages(pages), []
    monkeypatch.setattr(Internet, "fetcher", lambda self, max_bytes: sizes.append(max_bytes) or fetcher)
    return fetcher, sizes


def got(url, body, kind="text/html", final_url=None):
    data = body if isinstance(body, bytes) else body.encode()
    return Fetched(url, 200, final_url or url, data.decode("utf-8", "replace"), kind, data=data, seconds=0.1)


@pytest.mark.spec("WEB-4")
def test_an_unreachable_web_is_offline_after_saying_so_once(monkeypatch, capsys):
    engines = [Engine("a", NO_ROUTE, NO_ROUTE), Engine("b", NO_ROUTE, NO_ROUTE)]
    fetcher, _ = reading(monkeypatch, {})
    internet = Internet(engines)
    assert internet.search_all(["q", "r"], 3) == [[], []]
    asked = [list(engine.asked) for engine in engines]
    assert internet.search("s", 3) == [] and [engine.asked for engine in engines] == asked
    [page] = internet.fetch(["https://a.org"])
    assert all("q" in engine_asked for engine_asked in asked) and fetcher.asked == []
    assert page.url == "https://a.org" and page.data == b"" and page.blocks == [] and page.why
    assert capsys.readouterr().out.count("Web: not reachable (") == 1


@pytest.mark.spec("WEB-4")
def test_a_timeout_or_one_unreachable_engine_is_not_offline():
    timed_out = TimeoutError("timed out")
    engine = Engine("a", timed_out, timed_out, FOUND)
    internet = Internet([engine])
    assert (
        internet.search("q", 3) == [] and internet.search("r", 3) == FOUND and engine.asked == ["q", "q", "r"]
    )
    internet = Internet([Engine("a", NO_ROUTE, FOUND), Engine("b", FOUND, FOUND)])
    assert internet.search("q", 3) == FOUND and internet.search("r", 3) == FOUND and not internet.offline


ARTICLE = (
    "<article><h2>Install</h2><p>"
    + "Run pip install thing to get it. " * 15
    + "</p><ul><li>one</li><li>two</li></ul>"
    "<pre>def f():\n    return 1</pre></article>"
)


@pytest.mark.spec("WEB-5")
def test_fetch_gives_a_page_per_address_in_order(monkeypatch):
    fetcher, sizes = reading(
        monkeypatch,
        {
            "https://h": got("https://h", f"<html><title>T</title><body>{ARTICLE}</body></html>"),
            "https://t": got("https://t", "plain words", "text/plain"),
            "https://m": got("https://m", "# Title\n\nsome text", "text/markdown"),
            "https://j": got("https://j", '{"a": 1}', "application/json"),
            "https://x": got("https://x", "<a>1</a>", "application/xml"),
            "https://i": got("https://i", b"\x89PNG", "image/png"),
            "https://gone": Fetched("https://gone", 404, "https://gone", error="HTTP 404"),
        },
    )
    urls = ["https://h", "https://t", "https://m", "https://j", "https://x", "https://i", "https://gone"]
    pages = Internet().fetch(urls)
    assert [page.url for page in pages] == urls and fetcher.asked == [urls] and sizes == [None]
    html, plain, markdown, json_page, xml, image, gone = pages
    assert (
        html.title == "T"
        and html.kind == "text/html"
        and html.why == ""
        and html.text.startswith("## Install")
    )
    assert plain.text == "plain words" and markdown.blocks[0].kind == "heading" and '"a": 1' in json_page.text
    assert xml.blocks and image.data == b"\x89PNG" and image.blocks == [] and image.why == ""
    assert gone.data == b"" and gone.blocks == [] and gone.why == "HTTP 404"


@pytest.mark.spec("WEB-5")
def test_a_page_has_the_address_it_was_redirected_to(monkeypatch):
    reading(
        monkeypatch,
        {
            "https://old": got("https://old", "plain words", "text/plain", "https://new"),
            "https://gone": Fetched("https://gone", 404, "https://elsewhere", error="HTTP 404"),
        },
    )
    moved, gone = Internet().fetch(["https://old", "https://gone"])
    assert moved.url == "https://new" and gone.url == "https://gone"


@pytest.mark.spec("WEB-5")
def test_fetch_reads_at_most_max_bytes_and_asks_no_page_again(monkeypatch):
    fetcher, sizes = reading(monkeypatch, {"https://a": Fetched("https://a", error="larger than 10 bytes")})
    internet = Internet()
    assert internet.fetch(["https://a"], max_bytes=10)[0].why == "larger than 10 bytes"
    assert sizes == [10] and fetcher.asked == [["https://a"]]


@pytest.mark.spec("WEB-5")
def test_the_fetcher_follows_the_fetch_settings():
    Config.save(fetch_workers=3, fetch_timeout=7, fetch_max_bytes=5000)
    fetcher = Internet().fetcher(None)
    assert isinstance(fetcher, Fetcher)
    assert (fetcher.workers, fetcher.timeout, fetcher.max_bytes) == (3, 7.0, 5000)
    assert Internet().fetcher(100).max_bytes == 100


def logged():
    return [json.loads(line) for line in Home.web_log().read_text().splitlines()]


@pytest.mark.spec("WEB-6")
def test_every_request_is_logged_and_counted():
    log = RequestLog()
    log("search", "mwmbl", "HTTP 429", 0.5, "q")
    log("search", "wikipedia", "", 0.4, "q")
    log("fetch", "fetcher", "HTTP 404", 1.0, "https://a")
    lines = logged()
    assert [{key: line[key] for key in ("kind", "by", "result", "why", "target")} for line in lines] == [
        {"kind": "search", "by": "mwmbl", "result": "failed", "why": "HTTP 429", "target": "q"},
        {"kind": "search", "by": "wikipedia", "result": "ok", "why": "", "target": "q"},
        {"kind": "fetch", "by": "fetcher", "result": "failed", "why": "HTTP 404", "target": "https://a"},
    ]
    assert all(set(line) == {"at", "kind", "by", "result", "why", "seconds", "target"} for line in lines)
    assert log.counts()["search"] == {"requests": 2, "failed": 1, "ok": 1}
    assert log.text() == "search 2 (1 failed), fetch 1 (1 failed)" and RequestLog().text() == "none"


@pytest.mark.spec("WEB-6")
def test_internet_logs_every_engine_request_and_fetch(monkeypatch):
    reading(monkeypatch, {"https://a": got("https://a", "words", "text/plain")})
    internet = Internet([Engine("mwmbl", http_error(503), FOUND), Engine("wikipedia", http_error(404))])
    internet.search("q", 3)
    internet.fetch(["https://a"])
    lines = sorted((line["kind"], line["by"], line["result"], line["target"]) for line in logged())
    assert lines == [
        ("fetch", "fetcher", "ok", "https://a"),
        ("search", "mwmbl", "failed", "q"),
        ("search", "mwmbl", "ok", "q"),
        ("search", "wikipedia", "failed", "q"),
    ]
    assert internet.requests.text() == "search 3 (2 failed), fetch 1"


def page_text(markup):
    return WebPage.of(got("https://p", markup)).text


@pytest.mark.spec("WEB-7")
def test_page_text_takes_the_main_part_without_the_noise():
    page = WebPage.of(
        got(
            "https://p",
            f"""<html><head><title>My Page</title><style>p{{}}</style><script>x()</script></head>
        <body><nav><a>Home</a></nav><header>Site header</header><div hidden>secret</div>
        <div aria-hidden="true">icons</div><div role="navigation">menu</div>{ARTICLE}
        <aside>related</aside><footer>(c) site</footer><form>search</form></body></html>""",
        )
    )
    assert page.title == "My Page"
    assert page.text.startswith("## Install\n")
    assert "- one\n- two" in page.text and "def f():\n    return 1" in page.text
    for noise in (
        "Home",
        "Site header",
        "secret",
        "icons",
        "menu",
        "related",
        "(c) site",
        "search",
        "x()",
        "p{}",
    ):
        assert noise not in page.text


@pytest.mark.spec("WEB-7")
def test_page_text_uses_the_whole_body_when_main_is_short():
    text = page_text("<body><p>Intro outside</p><main><p>short</p></main><p>after</p></body>")
    assert text == "Intro outside\n\nshort\n\nafter"


@pytest.mark.spec("WEB-7")
def test_page_text_survives_broken_html():
    text = page_text("<body><p>kept text<p>more <b>bold</i></div></div></body>")
    assert "kept text" in text and "more bold" in text


@pytest.mark.spec("JOB-3")
def test_searches_and_fetches_go_through_the_journal(home_store, monkeypatch):
    engine = Engine("a", FOUND, FOUND)
    fetcher, _ = reading(
        monkeypatch,
        {"https://a": got("https://a", b"\x89PNG", "image/png"), "https://b": got("https://b", "<p>hi</p>")},
    )
    with pytest.raises(KeyboardInterrupt), Jobs.running(["ask", "hi"]):
        first = Internet([engine]).fetch(["https://a", "https://b"])
        Internet([engine]).search("q", 3)
        raise KeyboardInterrupt
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
        assert Internet([engine]).fetch(["https://a", "https://b"]) == first
        assert Internet([engine]).search("q", 3) == FOUND
    assert engine.asked == ["q"] and len(fetcher.asked) == 1
