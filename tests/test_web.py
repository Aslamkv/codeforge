import io
import json
import time
import urllib.error
import urllib.request
from email.message import Message

import pytest

from tinkerkit.fetcher import Fetched, Fetcher
from tinkerkit.jobs import Jobs
from tinkerkit.settings import Config, Home
from tinkerkit.web import Internet, OllamaWeb, RequestLog, WebBackend, WebError, WebPage


@pytest.fixture
def ollama_key(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")


def scripted(monkeypatch, backend, *answers):
    asked, left = [], list(answers)

    def search(self, query, count):
        asked.append(query)
        answer = left.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(backend, "search", search)
    return asked


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


def raising(error):
    def urlopen(request, timeout):
        raise error

    return urlopen


FOUND = [{"title": "T", "url": "https://x.org", "content": "c"}]


@pytest.mark.spec("WEB-1")
def test_the_standard_search_without_an_ollama_key():
    internet = Internet()
    assert internet.ollama is None and type(internet.standard) is WebBackend
    assert Internet.state() == "the standard search (no OLLAMA_API_KEY)"


@pytest.mark.spec("WEB-1")
def test_ollama_search_first_with_its_key(ollama_key):
    internet = Internet()
    assert type(internet.ollama) is OllamaWeb and type(internet.standard) is WebBackend
    assert Internet.state() == "ollama.com search, the standard search as fallback"


RESULTS = """<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Fdocs&amp;rut=1">Python <b>Docs</b></a>
<a class="result__snippet" href="x">The <b>official</b> docs</a>
<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">An ad</a>
<a class="result__a" href="https://example.com/page">Example</a>
</body></html>"""


def answering(monkeypatch, body):
    monkeypatch.setattr(WebBackend, "get", classmethod(lambda cls, url, timeout, form=None: body))


@pytest.mark.spec("WEB-2")
def test_the_standard_search_reads_duckduckgos_results(monkeypatch):
    answering(monkeypatch, RESULTS)
    backend = WebBackend(5)
    assert backend.search("python", 5) == [
        {"url": "https://python.org/docs", "title": "Python Docs", "content": "The official docs"},
        {"url": "https://example.com/page", "title": "Example", "content": ""},
    ]
    assert len(backend.search("python", 1)) == 1


@pytest.mark.spec("WEB-2")
def test_a_bot_check_page_is_a_failure(monkeypatch):
    answering(monkeypatch, "<html>anomaly</html>")
    with pytest.raises(WebError, match="bot check") as error:
        WebBackend(5).search("q", 3)
    assert not error.value.retry and not error.value.unreachable


@pytest.mark.spec("WEB-2")
@pytest.mark.parametrize(
    "error,retry,unreachable",
    [
        (http_error(503), True, False),
        (http_error(429), False, False),
        (http_error(404), False, False),
        (TimeoutError("timed out"), True, False),
        (urllib.error.URLError(OSError("no route")), False, True),
    ],
)
def test_the_standard_searchs_failures(monkeypatch, error, retry, unreachable):
    monkeypatch.setattr(urllib.request, "urlopen", raising(error))
    with pytest.raises(WebError) as raised:
        WebBackend.get("https://html.duckduckgo.com/html/", 5)
    assert (raised.value.retry, raised.value.unreachable) == (retry, unreachable)


@pytest.mark.spec("WEB-2")
def test_ollama_web_posts_json_with_its_key(monkeypatch, ollama_key):
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        return Response(json.dumps({"results": [{"url": "https://r"}]}))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert OllamaWeb(5).search("python", 2) == [{"url": "https://r"}]
    request = sent[0]
    assert request.full_url == "https://ollama.com/api/web_search" and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data) == {"query": "python", "max_results": 2}


@pytest.mark.spec("WEB-2")
def test_ollama_answers_without_json_or_a_result_list_are_failures(monkeypatch, ollama_key):
    answers = iter([Response("not json"), Response("[1]"), Response("{}")])
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: next(answers))
    for _ in range(3):
        with pytest.raises(WebError):
            OllamaWeb(5).search("q", 1)


@pytest.mark.spec("WEB-3")
@pytest.mark.parametrize(
    "error",
    [
        http_error(401),
        http_error(403),
        http_error(429),
        http_error(503),
        TimeoutError("timed out"),
        urllib.error.URLError(OSError("no route")),
    ],
)
def test_any_ollama_failure_asks_the_standard_search_at_once(monkeypatch, ollama_key, capsys, error):
    monkeypatch.setattr(urllib.request, "urlopen", raising(error))
    standard = scripted(monkeypatch, WebBackend, FOUND, FOUND)
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    internet = Internet()
    assert internet.search("q", 3) == FOUND and standard == ["q"] and slept == []
    assert "Web: ollama.com search failed" in capsys.readouterr().out
    ollama = scripted(monkeypatch, OllamaWeb, FOUND)
    assert internet.search("r", 3) == FOUND and ollama == ["r"] and standard == ["q"]


@pytest.mark.spec("WEB-3")
def test_a_standard_search_that_times_out_or_fails_on_the_server_is_asked_once_more(monkeypatch):
    for trouble in (WebError("timed out", retry=True), WebError("HTTP 503", retry=True)):
        asked = scripted(monkeypatch, WebBackend, trouble, FOUND)
        assert Internet().search("q", 3) == FOUND and asked == ["q", "q"]
        asked = scripted(monkeypatch, WebBackend, trouble, trouble)
        assert Internet().search("q", 3) == [] and asked == ["q", "q"]
    asked = scripted(monkeypatch, WebBackend, WebError("bot check"))
    assert Internet().search("q", 3) == [] and asked == ["q"]


@pytest.mark.spec("WEB-3")
def test_search_keeps_web_addresses_as_strings(monkeypatch):
    found = [
        {"title": "T", "url": "https://x.org", "content": None},
        {"url": "ftp://x"},
        "junk",
        {"title": 5, "url": "http://y.org"},
    ]
    scripted(monkeypatch, WebBackend, found)
    assert Internet().search("q", 3) == [
        {"title": "T", "url": "https://x.org", "content": ""},
        {"title": "5", "url": "http://y.org", "content": ""},
    ]


@pytest.mark.spec("WEB-3")
def test_a_search_bug_is_a_failure_never_raised(monkeypatch):
    scripted(monkeypatch, WebBackend, KeyError("oops"))
    assert Internet().search("q", 3) == []


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
    asked = scripted(monkeypatch, WebBackend, WebError("html.duckduckgo.com: no route", unreachable=True))
    fetcher, _ = reading(monkeypatch, {})
    internet = Internet()
    assert internet.search("q", 3) == [] and internet.search("r", 3) == []
    [page] = internet.fetch(["https://a.org"])
    assert asked == ["q"] and fetcher.asked == []
    assert page.url == "https://a.org" and page.data == b"" and page.blocks == [] and page.why
    assert capsys.readouterr().out.count("Web: not reachable (html.duckduckgo.com: no route)") == 1


@pytest.mark.spec("WEB-4")
def test_a_timeout_is_not_offline(monkeypatch):
    timed_out = WebError("timed out", retry=True)
    asked = scripted(monkeypatch, WebBackend, timed_out, timed_out, FOUND)
    internet = Internet()
    assert internet.search("q", 3) == [] and internet.search("r", 3) == FOUND and asked == ["q", "q", "r"]


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
    log("search", "ollama", "HTTP 429", 0.5, "q")
    log("search", "standard", "", 0.4, "q")
    log("fetch", "standard", "HTTP 404", 1.0, "https://a")
    lines = logged()
    assert [{key: line[key] for key in ("kind", "by", "result", "why", "target")} for line in lines] == [
        {"kind": "search", "by": "ollama", "result": "failed", "why": "HTTP 429", "target": "q"},
        {"kind": "search", "by": "standard", "result": "ok", "why": "", "target": "q"},
        {"kind": "fetch", "by": "standard", "result": "failed", "why": "HTTP 404", "target": "https://a"},
    ]
    assert all(set(line) == {"at", "kind", "by", "result", "why", "seconds", "target"} for line in lines)
    assert log.counts()["search"] == {"requests": 2, "failed": 1, "ok": 1}
    assert log.text() == "search 2 (1 failed), fetch 1 (1 failed)" and RequestLog().text() == "none"


@pytest.mark.spec("WEB-6")
def test_internet_logs_its_searches_and_fetches(monkeypatch, ollama_key):
    scripted(monkeypatch, OllamaWeb, WebError("HTTP 429"))
    scripted(monkeypatch, WebBackend, FOUND)
    reading(monkeypatch, {"https://a": got("https://a", "words", "text/plain")})
    internet = Internet()
    internet.search("q", 3)
    internet.fetch(["https://a"])
    lines = logged()
    assert [(line["kind"], line["by"], line["result"], line["target"]) for line in lines] == [
        ("search", "ollama", "failed", "q"),
        ("search", "standard", "ok", "q"),
        ("fetch", "standard", "ok", "https://a"),
    ]
    assert internet.requests.text() == "search 2 (1 failed), fetch 1"


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
    asked = scripted(monkeypatch, WebBackend, FOUND, FOUND)
    fetcher, _ = reading(
        monkeypatch,
        {"https://a": got("https://a", b"\x89PNG", "image/png"), "https://b": got("https://b", "<p>hi</p>")},
    )
    with pytest.raises(KeyboardInterrupt), Jobs.running(["ask", "hi"]):
        first = Internet().fetch(["https://a", "https://b"])
        Internet().search("q", 3)
        raise KeyboardInterrupt
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
        assert Internet().fetch(["https://a", "https://b"]) == first
        assert Internet().search("q", 3) == FOUND
    assert asked == ["q"] and len(fetcher.asked) == 1
