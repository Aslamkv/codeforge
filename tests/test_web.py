import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from tinkerkit import web
from tinkerkit.fetcher import Fetched, Fetcher
from tinkerkit.settings import WEB_BACKENDS, Config
from tinkerkit.web import (
    DuckDuckGoWeb,
    Internet,
    OllamaWeb,
    PageText,
    RateLimited,
    Throttle,
    WebBackend,
    WebError,
)


class Scripted(WebBackend):
    def __init__(self, timeout, answers=(), ready=""):
        super().__init__(timeout)
        self.answers, self.ready, self.calls = list(answers), ready, 0

    def unready(self):
        return self.ready

    def next(self):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def search(self, query, count):
        return self.next()

    def fetch(self, url):
        return self.next()


def internet_with(monkeypatch, *backends, web_name="a", fallback=""):
    named = dict(zip(("a", "b"), backends))
    monkeypatch.setattr(
        Internet,
        "backends",
        staticmethod(
            lambda: {name: (lambda timeout, backend=backend: backend) for name, backend in named.items()}
        ),
    )
    Config.save(web=web_name, web_fallback=fallback)
    return Internet()


class FakeClock:
    def __init__(self):
        self.at, self.slept = 0.0, []

    def monotonic(self):
        return self.at

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.at += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(Internet, "RETRY_PAUSE_SECONDS", 0)
    fake = FakeClock()
    monkeypatch.setattr(web, "time", fake)
    return fake


@pytest.mark.spec("WEB-1")
def test_backends():
    assert Internet.backends() == {"ollama": OllamaWeb, "duckduckgo": DuckDuckGoWeb}


@pytest.mark.spec("WEB-2")
def test_web_then_fallback_skipping_repeats_and_empty(monkeypatch):
    first, second = Scripted(1), Scripted(1)
    internet = internet_with(monkeypatch, first, second, web_name="a", fallback="b")
    assert internet.backends_used == [first, second] and internet.describe() == "a then b"
    assert internet_with(monkeypatch, first, second, web_name="a", fallback="a").backends_used == [first]
    assert internet_with(monkeypatch, first, second, web_name="", fallback="b").backends_used == [second]


@pytest.mark.spec("WEB-2")
def test_a_backend_not_ready_is_skipped_with_its_reason(monkeypatch):
    first, second = Scripted(1, ready="no key"), Scripted(1)
    internet = internet_with(monkeypatch, first, second, web_name="a", fallback="b")
    assert (
        internet.backends_used == [second]
        and internet.unusable_backends == ["a: no key"]
        and internet.available()
    )


@pytest.mark.spec("WEB-2")
def test_no_backend_left_is_not_available(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, ready="no key"), web_name="a")
    assert not internet.available() and internet.problem() == "a: no key"
    assert not internet_with(monkeypatch, Scripted(1), web_name="").available()


@pytest.mark.spec("WEB-1")
def test_the_web_backends_are_the_ones_the_settings_name():
    assert tuple(Internet.backends()) == WEB_BACKENDS


@pytest.mark.spec("WEB-3")
def test_required_names_the_option_when_no_backend_works():
    Config.save(web="ollama")
    with pytest.raises(SystemExit, match="--web needs the Internet plugin: ollama: no Ollama API key"):
        Internet.required("--web")


@pytest.mark.spec("WEB-3")
def test_required_and_state_with_a_backend_ready(capsys):
    Config.save(web="ollama", web_fallback="duckduckgo")
    internet = Internet.required("tinker learn")
    assert internet.describe() == "duckduckgo"
    assert "Web: ollama: no Ollama API key" in capsys.readouterr().out
    assert Internet.state().startswith("ready: duckduckgo; not usable: ollama")


@pytest.mark.spec("WEB-3")
def test_state_when_not_usable():
    Config.save(web="ollama")
    assert Internet.state().startswith("ollama: no Ollama API key")
    Config.save(web="nonsense")
    assert Internet.state().startswith("not usable: web must be one of")


@pytest.mark.spec("WEB-4")
def test_search_keeps_web_addresses_as_strings(monkeypatch):
    found = [
        {"title": "T", "url": "https://x.org", "content": None},
        {"url": "ftp://x"},
        "junk",
        {"title": 5, "url": "http://y.org"},
    ]
    internet = internet_with(monkeypatch, Scripted(1, [found]))
    assert internet.search("q", 3) == [
        {"title": "T", "url": "https://x.org", "content": ""},
        {"title": "5", "url": "http://y.org", "content": ""},
    ]


@pytest.mark.spec("WEB-4")
def test_fetch_needs_text_and_keeps_the_address_asked(monkeypatch):
    internet = internet_with(
        monkeypatch, Scripted(1, [{"title": "P", "url": "other", "content": "text"}, {"content": "   "}])
    )
    assert internet.fetch("https://a") == {"title": "P", "url": "https://a", "content": "text"}
    assert internet.fetch("https://b") is None
    assert "its fetch gave no text" in internet.problem("https://b")


@pytest.mark.spec("WEB-4")
def test_search_gives_none_with_why_when_nothing_answers(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, [WebError("down")]))
    assert internet.search("q", 3) is None and internet.problem("q") == "a: down"


@pytest.mark.spec("WEB-5")
def test_the_next_backend_is_asked_when_one_fails(monkeypatch):
    first, second = Scripted(1, [WebError("a broke")]), Scripted(1, [[{"url": "https://ok"}]])
    internet = internet_with(monkeypatch, first, second, web_name="a", fallback="b")
    assert internet.search("q", 1) == [{"title": "", "url": "https://ok", "content": ""}]


@pytest.mark.spec("WEB-5")
def test_a_moments_trouble_is_asked_again_once(monkeypatch):
    first = Scripted(1, [WebError("rate", retry=True), [{"url": "https://ok"}]])
    assert internet_with(monkeypatch, first).search("q", 1) and first.calls == 2
    second = Scripted(1, [WebError("rate", retry=True), WebError("rate", retry=True)])
    assert internet_with(monkeypatch, second).search("q", 1) is None and second.calls == 2


@pytest.mark.spec("WEB-5")
def test_a_backend_bug_is_a_failure_never_raised(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, [KeyError("oops")]))
    assert internet.search("q", 1) is None
    assert "failed: KeyError" in internet.problem("q")


@pytest.mark.spec("WEB-6")
def test_a_backend_lost_twice_in_a_row_is_left(monkeypatch, capsys):
    gone = WebError("unreachable", network=True)
    backend = Scripted(1, [gone, gone])
    internet = internet_with(monkeypatch, backend)
    internet.search("1", 1)
    assert internet.available()
    internet.search("2", 1)
    assert not internet.available() and internet.problem() == "unreachable"
    assert "not asking a again" in capsys.readouterr().out


@pytest.mark.spec("WEB-6")
def test_a_success_resets_the_count(monkeypatch):
    gone = WebError("unreachable", network=True)
    backend = Scripted(1, [gone, [{"url": "https://x"}], gone, gone])
    internet = internet_with(monkeypatch, backend)
    for _ in range(3):
        internet.search("q", 1)
    assert internet.available()
    internet.search("q", 1)
    assert not internet.available()


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


@pytest.mark.spec("WEB-8")
def test_ollama_web_needs_its_key(monkeypatch):
    assert "OLLAMA_API_KEY" in OllamaWeb(5).unready()
    monkeypatch.setenv("OLLAMA_API_KEY", " k ")
    assert OllamaWeb(5).unready() == ""


@pytest.mark.spec("WEB-8")
def test_ollama_web_posts_json_with_its_key(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")
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


@pytest.mark.spec("WEB-8")
@pytest.mark.parametrize(
    "code,path,network,retry",
    [
        (401, "/web_search", True, False),
        (403, "/web_fetch", True, False),
        (429, "/web_search", False, False),
        (500, "/web_search", True, True),
        (503, "/web_fetch", False, True),
        (404, "/web_fetch", False, False),
    ],
)
def test_ollama_web_http_errors(monkeypatch, code, path, network, retry):
    def urlopen(request, timeout):
        raise http_error(code)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(WebError) as error:
        OllamaWeb(5).post(path, {})
    assert (error.value.network, error.value.retry) == (network, retry)
    assert f"HTTP {code}" in str(error.value)


@pytest.mark.spec("WEB-8")
def test_ollama_web_unusable_answers(monkeypatch):
    answers = iter([Response("not json"), Response("[1]"), Response("{}")])
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: next(answers))
    with pytest.raises(WebError, match="did not answer with JSON"):
        OllamaWeb(5).fetch("https://x")
    with pytest.raises(WebError, match="JSON object"):
        OllamaWeb(5).fetch("https://x")
    with pytest.raises(WebError, match="without results"):
        OllamaWeb(5).search("q", 1)


@pytest.mark.spec("WEB-8")
def test_ollama_web_unreachable_is_a_network_failure():
    with pytest.raises(WebError, match="cannot be reached") as error:
        OllamaWeb(5).post("/web_search", {})
    assert error.value.network


RESULTS = """<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Fdocs&amp;rut=1">Python <b>Docs</b></a>
<a class="result__snippet" href="x">The <b>official</b> docs</a>
<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">An ad</a>
<a class="result__a" href="https://example.com/page">Example</a>
</body></html>"""


@pytest.mark.spec("WEB-9")
def test_duckduckgo_reads_its_results(monkeypatch):
    monkeypatch.setattr(
        DuckDuckGoWeb, "get", classmethod(lambda cls, url, timeout, form=None: (RESULTS, "text/html"))
    )
    backend = DuckDuckGoWeb(5)
    assert backend.unready() == ""
    assert backend.search("python", 5) == [
        {"url": "https://python.org/docs", "title": "Python Docs", "content": "The official docs"},
        {"url": "https://example.com/page", "title": "Example", "content": ""},
    ]
    assert len(backend.search("python", 1)) == 1


@pytest.mark.spec("WEB-9")
def test_duckduckgo_bot_check_is_worth_asking_again(monkeypatch):
    monkeypatch.setattr(
        DuckDuckGoWeb, "get", classmethod(lambda cls, url, timeout, form=None: ("anomaly", "text/html"))
    )
    with pytest.raises(WebError, match="bot check") as error:
        DuckDuckGoWeb(5).search("q", 3)
    assert error.value.retry


HELLO = "<body><p>Hello there</p></body>"


class Pages:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def fetch_all(self, urls, kind="fetch"):
        self.asked += urls
        return [self.pages[url] for url in urls]


@pytest.mark.spec("WEB-10")
def test_duckduckgo_reads_pages_through_the_fetcher_by_content_type(monkeypatch):
    pages = Pages(
        {
            "https://h": Fetched(
                "https://h", 200, "https://h", f"<html><title>T</title>{HELLO}</html>", "text/html"
            ),
            "https://t": Fetched("https://t", 200, "https://t", "plain words", "text/plain"),
            "https://j": Fetched("https://j", 200, "https://j", '{"a": 1}', "application/json"),
            "https://i": Fetched("https://i", 200, "https://i", "\x89PNG", "image/png"),
        }
    )
    monkeypatch.setattr(Internet, "page_fetcher", staticmethod(lambda log=None: pages))
    backend = DuckDuckGoWeb(5)
    assert backend.fetch("https://h") == {"title": "T", "url": "https://h", "content": "Hello there"}
    assert backend.fetch("https://t")["content"] == "plain words"
    assert backend.fetch("https://j")["content"] == '{"a": 1}'
    with pytest.raises(WebError, match="not a text page"):
        backend.fetch("https://i")
    assert pages.asked == ["https://h", "https://t", "https://j", "https://i"]


@pytest.mark.spec("WEB-10")
def test_a_page_the_fetcher_could_not_get_is_a_web_error_with_its_status(monkeypatch):
    pages = Pages(
        {
            "https://down": Fetched("https://down", 503, "https://down", error="HTTP 503"),
            "https://gone": Fetched("https://gone", 404, "https://gone", error="HTTP 404"),
            "https://no": Fetched("https://no", final_url="https://no", error=Fetched.ROBOTS),
        }
    )
    monkeypatch.setattr(Internet, "page_fetcher", staticmethod(lambda log=None: pages))
    errors = {}
    for url in pages.pages:
        with pytest.raises(WebError) as error:
            DuckDuckGoWeb(5).fetch(url)
        errors[url] = error.value
    assert errors["https://down"].code == 503 and errors["https://down"].retry
    assert errors["https://gone"].code == 404 and not errors["https://gone"].retry
    assert errors["https://no"].code is None and not errors["https://no"].retry
    assert "robots.txt" in str(errors["https://no"])
    assert not any(error.network for error in errors.values())


@pytest.mark.spec("WEB-9")
def test_duckduckgo_search_errors(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout: (_ for _ in ()).throw(http_error(503))
    )
    with pytest.raises(WebError, match="answered HTTP 503") as error:
        DuckDuckGoWeb.get("https://site.org/x", 5)
    assert error.value.retry and not error.value.network


ARTICLE = (
    "<article><h2>Install</h2><p>"
    + "Run pip install thing to get it. " * 15
    + "</p><ul><li>one</li><li>two</li></ul>"
    "<pre>def f():\n    return 1</pre></article>"
)


@pytest.mark.spec("WEB-11")
def test_page_text_takes_the_main_part_without_the_noise():
    page = PageText.of(f"""<html><head><title>My Page</title><style>p{{}}</style><script>x()</script></head>
        <body><nav><a>Home</a></nav><header>Site header</header><div hidden>secret</div>
        <div aria-hidden="true">icons</div><div role="navigation">menu</div>{ARTICLE}
        <aside>related</aside><footer>(c) site</footer><form>search</form></body></html>""")
    text = page.content()
    assert page.title == "My Page"
    assert text.startswith("## Install\n")
    assert "- one\n- two" in text and "def f():\n    return 1" in text
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
        assert noise not in text


@pytest.mark.spec("WEB-11")
def test_page_text_uses_the_whole_body_when_main_is_short():
    page = PageText.of("<body><p>Intro outside</p><main><p>short</p></main><p>after</p></body>")
    assert page.content() == "Intro outside\n\nshort\n\nafter"


@pytest.mark.spec("WEB-11")
def test_page_text_survives_broken_html():
    page = PageText.of("<body><p>kept text<p>more <b>bold</i></div></div></body>")
    assert "kept text" in page.content() and "more bold" in page.content()


@pytest.mark.spec("WEB-7")
def test_a_rate_limit_is_told_apart_from_other_failures(monkeypatch):
    Config.save(web_backoff=0)
    internet = internet_with(
        monkeypatch,
        Scripted(1, [WebError("busy", code=429), WebError("down", code=503), [{"url": "https://ok"}]]),
    )
    assert internet.search("q", 1) is None and internet.rate_limited("q")
    assert internet.search("r", 1) is None and not internet.rate_limited("r")
    answered_later = internet.search("q", 1)
    assert answered_later and not internet.rate_limited("q")

    def urlopen(request, timeout):
        raise http_error(429)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(WebError) as error:
        OllamaWeb(5).post("/web_search", {})
    assert error.value.code == 429 and not error.value.retry
    with pytest.raises(WebError) as error:
        DuckDuckGoWeb.get("https://site.org/x", 5)
    assert error.value.code == 429 and not error.value.retry


@pytest.mark.spec("WEB-7")
def test_the_page_fetcher_follows_the_fetch_settings():
    Config.save(fetch_workers=3, fetch_timeout=7, fetch_max_bytes=5000, fetch_domain_delay=0.25)
    fetcher = Internet.page_fetcher(log=print)
    assert isinstance(fetcher, Fetcher) and fetcher.log is print
    assert (fetcher.workers, fetcher.timeout, fetcher.max_bytes, fetcher.domain_delay) == (3, 7.0, 5000, 0.25)


@pytest.mark.spec("WEB-12")
def test_the_throttle_lets_rate_requests_a_minute_through(clock):
    throttle = Throttle(2, 20, clock=clock.monotonic, sleep=clock.sleep)
    for _ in range(3):
        throttle.call(lambda: "ok", lambda: False)
    half_a_minute_for_the_third = [30.0]
    assert clock.slept == half_a_minute_for_the_third
    clock.slept.clear()
    unlimited = Throttle(None, 20, clock=clock.monotonic, sleep=clock.sleep)
    for _ in range(100):
        unlimited.call(lambda: "ok", lambda: False)
    assert clock.slept == []


@pytest.mark.spec("WEB-12")
def test_the_throttle_waits_out_429_within_its_backoff(clock):
    with pytest.raises(RateLimited):
        Throttle(60, 20, clock=clock.monotonic, sleep=clock.sleep).call(lambda: None, lambda: True)
    assert clock.slept == [1.0, 2.0, 4.0, 8.0, 5.0] and sum(clock.slept) <= 20
    answers = iter([True, False])
    assert (
        Throttle(60, 20, clock=clock.monotonic, sleep=clock.sleep).call(lambda: "ok", lambda: next(answers))
        == "ok"
    )
    clock.slept.clear()
    with pytest.raises(RateLimited):
        Throttle(60, 0, clock=clock.monotonic, sleep=clock.sleep).call(lambda: None, lambda: True)
    assert clock.slept == []


@pytest.mark.spec("WEB-12")
def test_each_backend_has_its_own_rate():
    assert OllamaWeb.RATE == 8 and DuckDuckGoWeb.RATE is None


@pytest.mark.spec("WEB-12", "WEB-5")
def test_internet_waits_out_a_429(monkeypatch, clock):
    busy = WebError("busy", network=True, code=429)
    backend = Scripted(1, [busy, busy, [{"url": "https://ok"}]])
    internet = internet_with(monkeypatch, backend)
    assert internet.search("q", 1) and backend.calls == 3 and not internet.rate_limited("q")
    assert clock.slept == [1.0, 2.0] and internet.failures["a"] == 0


@pytest.mark.spec("WEB-12")
def test_a_backend_still_rate_limited_fails_after_web_backoff(monkeypatch, clock):
    Config.save(web_backoff=5)
    busy = WebError("busy", network=True, code=429)
    first, second = Scripted(1, [busy] * 10), Scripted(1, [[{"url": "https://ok"}]])
    internet = internet_with(monkeypatch, first, web_name="a")
    assert internet.search("q", 1) is None and internet.rate_limited("q")
    assert internet.problem("q") == "a: still rate limited after 5 seconds" and sum(clock.slept) <= 5
    assert internet.failures["a"] == 1
    internet = internet_with(monkeypatch, Scripted(1, [busy] * 10), second, web_name="a", fallback="b")
    assert internet.search("q", 1) and not internet.rate_limited("q")


@pytest.mark.spec("WEB-12")
def test_internet_asks_each_backend_at_its_rate(monkeypatch, clock):
    class Rated(Scripted):
        RATE = 2

    backend = Rated(1, [[{"url": "https://ok"}]] * 3)
    internet = internet_with(monkeypatch, backend)
    for query in ("1", "2", "3"):
        internet.search(query, 1)
    assert clock.slept == [30.0]
