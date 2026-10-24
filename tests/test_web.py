"""WEB: the Internet plugin and its backends."""

import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from tinkerkit.settings import Config
from tinkerkit.fetcher import Fetcher
from tinkerkit.web import DuckDuckGoWeb, Internet, OllamaWeb, PageText, WebBackend, WebError


class Scripted(WebBackend):
    """A backend that answers from a script: each call takes the next answer (a value, or an exception)."""

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
    """An Internet plugin whose config names backends a and b (given as Scripted instances)."""
    named = dict(zip(("a", "b"), backends))
    monkeypatch.setattr(Internet, "backends", staticmethod(lambda: {name: (lambda timeout, backend=backend: backend)
                                                                    for name, backend in named.items()}))
    Config.save(web=web_name, web_fallback=fallback)
    return Internet()


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(Internet, "RETRY_PAUSE_SECONDS", 0)


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
    assert internet.backends_used == [second] and internet.unusable_backends == ["a: no key"] and internet.available()


@pytest.mark.spec("WEB-2")
def test_no_backend_left_is_not_available(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, ready="no key"), web_name="a")
    assert not internet.available() and internet.problem() == "a: no key"
    assert not internet_with(monkeypatch, Scripted(1), web_name="").available()


@pytest.mark.spec("WEB-3")
@pytest.mark.parametrize("key,value", [("web", "bing"), ("web_fallback", "x"), ("web_timeout", 0),
                                       ("web_timeout", True), ("learn_missing", 0), ("learn_missing", 1.5),
                                       ("learn_missing", True)])
def test_check_rejects_unusable_web_settings(key, value):
    Config.save(**{key: value})
    with pytest.raises(SystemExit, match=key):
        Internet.check()


@pytest.mark.spec("WEB-3")
def test_check_accepts_the_defaults_and_duckduckgo():
    Internet.check()
    Config.save(web="duckduckgo", web_fallback="ollama", web_timeout=2.5, learn_missing=3)
    Internet.check()


@pytest.mark.spec("WEB-4")
def test_required_names_the_option_when_no_backend_works():
    Config.save(web="ollama")  # no OLLAMA_API_KEY in tests
    with pytest.raises(SystemExit, match="--web needs the Internet plugin: ollama: no Ollama API key"):
        Internet.required("--web")


@pytest.mark.spec("WEB-4")
def test_required_and_state_with_a_backend_ready(capsys):
    Config.save(web="ollama", web_fallback="duckduckgo")
    internet = Internet.required("tinker learn")
    assert internet.describe() == "duckduckgo"
    assert "Web: ollama: no Ollama API key" in capsys.readouterr().out
    assert Internet.state().startswith("ready: duckduckgo; not usable: ollama")


@pytest.mark.spec("WEB-4")
def test_state_when_not_usable():
    Config.save(web="ollama")
    assert Internet.state().startswith("ollama: no Ollama API key")
    Config.save(web="nonsense")
    assert Internet.state().startswith("not usable: web must be one of")


@pytest.mark.spec("WEB-5")
def test_search_keeps_web_addresses_as_strings(monkeypatch):
    found = [{"title": "T", "url": "https://x.org", "content": None}, {"url": "ftp://x"}, "junk",
             {"title": 5, "url": "http://y.org"}]
    internet = internet_with(monkeypatch, Scripted(1, [found]))
    assert internet.search("q", 3) == [{"title": "T", "url": "https://x.org", "content": ""},
                                       {"title": "5", "url": "http://y.org", "content": ""}]


@pytest.mark.spec("WEB-5")
def test_fetch_needs_text_and_keeps_the_address_asked(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, [{"title": "P", "url": "other", "content": "text"},
                                                       {"content": "   "}]))
    assert internet.fetch("https://a") == {"title": "P", "url": "https://a", "content": "text"}
    assert internet.fetch("https://b") is None
    assert "its fetch gave no text" in internet.problem("https://b")


@pytest.mark.spec("WEB-5")
def test_search_gives_none_with_why_when_nothing_answers(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, [WebError("down")]))
    assert internet.search("q", 3) is None and internet.problem("q") == "a: down"


@pytest.mark.spec("WEB-6")
def test_the_next_backend_is_asked_when_one_fails(monkeypatch):
    first, second = Scripted(1, [WebError("a broke")]), Scripted(1, [[{"url": "https://ok"}]])
    internet = internet_with(monkeypatch, first, second, web_name="a", fallback="b")
    assert internet.search("q", 1) == [{"title": "", "url": "https://ok", "content": ""}]


@pytest.mark.spec("WEB-6")
def test_a_moments_trouble_is_asked_again_once(monkeypatch):
    first = Scripted(1, [WebError("rate", retry=True), [{"url": "https://ok"}]])
    assert internet_with(monkeypatch, first).search("q", 1) and first.calls == 2
    second = Scripted(1, [WebError("rate", retry=True), WebError("rate", retry=True)])
    assert internet_with(monkeypatch, second).search("q", 1) is None and second.calls == 2


@pytest.mark.spec("WEB-6")
def test_a_backend_bug_is_a_failure_never_raised(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, [KeyError("oops")]))
    assert internet.search("q", 1) is None
    assert "failed: KeyError" in internet.problem("q")


@pytest.mark.spec("WEB-7")
def test_a_backend_lost_twice_in_a_row_is_left(monkeypatch, capsys):
    gone = WebError("unreachable", network=True)
    backend = Scripted(1, [gone, gone])
    internet = internet_with(monkeypatch, backend)
    internet.search("1", 1)
    assert internet.available()
    internet.search("2", 1)
    assert not internet.available() and internet.problem() == "unreachable"
    assert "not asking a again" in capsys.readouterr().out


@pytest.mark.spec("WEB-7")
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
    """What urlopen gives: a readable body with headers."""

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
@pytest.mark.parametrize("code,path,network,retry", [(401, "/web_search", True, False), (403, "/web_fetch", True, False),
                                                     (429, "/web_search", True, True), (500, "/web_search", True, True),
                                                     (503, "/web_fetch", False, True), (404, "/web_fetch", False, False)])
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
        OllamaWeb(5).post("/web_search", {})  # the network is off in tests
    assert error.value.network


RESULTS = """<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Fdocs&amp;rut=1">Python <b>Docs</b></a>
<a class="result__snippet" href="x">The <b>official</b> docs</a>
<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">An ad</a>
<a class="result__a" href="https://example.com/page">Example</a>
</body></html>"""


@pytest.mark.spec("WEB-9")
def test_duckduckgo_reads_its_results(monkeypatch):
    monkeypatch.setattr(DuckDuckGoWeb, "get", classmethod(lambda cls, url, timeout, form=None: (RESULTS, "text/html")))
    backend = DuckDuckGoWeb(5)
    assert backend.unready() == ""
    assert backend.search("python", 5) == [
        {"url": "https://python.org/docs", "title": "Python Docs", "content": "The official docs"},
        {"url": "https://example.com/page", "title": "Example", "content": ""}]
    assert len(backend.search("python", 1)) == 1


@pytest.mark.spec("WEB-9")
def test_duckduckgo_bot_check_is_worth_asking_again(monkeypatch):
    monkeypatch.setattr(DuckDuckGoWeb, "get", classmethod(lambda cls, url, timeout, form=None: ("anomaly", "text/html")))
    with pytest.raises(WebError, match="bot check") as error:
        DuckDuckGoWeb(5).search("q", 3)
    assert error.value.retry


@pytest.mark.spec("WEB-10")
def test_read_page_by_content_type(monkeypatch):
    pages = {"https://h": ("<html><title>T</title><body><p>Hello there</p></body></html>", "text/html"),
             "https://t": ("plain words", "text/plain"), "https://j": ('{"a": 1}', "application/json"),
             "https://i": ("\x89PNG", "image/png")}
    monkeypatch.setattr(DuckDuckGoWeb, "get", classmethod(lambda cls, url, timeout, form=None: pages[url]))
    assert DuckDuckGoWeb.read_page("https://h", 5) == {"title": "T", "url": "https://h", "content": "Hello there"}
    assert DuckDuckGoWeb.read_page("https://t", 5)["content"] == "plain words"
    assert DuckDuckGoWeb.read_page("https://j", 5)["content"] == '{"a": 1}'
    with pytest.raises(WebError, match="not a text page"):
        DuckDuckGoWeb.read_page("https://i", 5)


@pytest.mark.spec("WEB-10")
def test_duckduckgo_get_errors(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: (_ for _ in ()).throw(http_error(503)))
    with pytest.raises(WebError, match="answered HTTP 503") as error:
        DuckDuckGoWeb.get("https://site.org/x", 5)
    assert error.value.retry and not error.value.network


@pytest.mark.spec("WEB-10")
def test_a_line_of_bullets_and_separators_only_is_left_of_a_menu():
    assert all(PageText.BULLETS_AND_SEPARATORS_RE.fullmatch(line) for line in ("• · »", "---", "", "|"))
    assert not PageText.BULLETS_AND_SEPARATORS_RE.fullmatch("Home")


ARTICLE = "<article><h2>Install</h2><p>" + "Run pip install thing to get it. " * 15 + "</p><ul><li>one</li><li>two</li></ul>" \
          "<pre>def f():\n    return 1</pre></article>"


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
    for noise in ("Home", "Site header", "secret", "icons", "menu", "related", "(c) site", "search", "x()", "p{}"):
        assert noise not in text


@pytest.mark.spec("WEB-11")
def test_page_text_uses_the_whole_body_when_main_is_short():
    page = PageText.of("<body><p>Intro outside</p><main><p>short</p></main><p>after</p></body>")
    assert page.content() == "Intro outside\n\nshort\n\nafter"


@pytest.mark.spec("WEB-11")
def test_page_text_survives_broken_html():
    page = PageText.of("<body><p>kept text<p>more <b>bold</i></div></div></body>")
    assert "kept text" in page.content() and "more bold" in page.content()


@pytest.mark.spec("WEB-12")
def test_a_rate_limit_is_told_apart_from_other_failures(monkeypatch):
    internet = internet_with(monkeypatch, Scripted(1, [WebError("busy", code=429), WebError("down", code=503),
                                                       [{"url": "https://ok"}]]))
    assert internet.search("q", 1) is None and internet.rate_limited("q")
    assert internet.search("r", 1) is None and not internet.rate_limited("r")
    assert internet.search("q", 1) and not internet.rate_limited("q")  # a later answer clears it
    waited = internet_with(monkeypatch, Scripted(1, [WebError("busy", retry=True, code=429), [{"url": "https://ok"}]]))
    assert waited.search("q", 1) and not waited.rate_limited("q")  # asked again within the call, and answered
    def urlopen(request, timeout):
        raise http_error(429)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(WebError) as error:
        OllamaWeb(5).post("/web_search", {})
    assert error.value.code == 429


@pytest.mark.spec("WEB-12")
def test_the_page_fetcher_follows_the_fetch_settings():
    Config.save(fetch_workers=3, fetch_timeout=7, fetch_max_bytes=5000, fetch_domain_delay=0.25)
    fetcher = Internet.page_fetcher(log=print)
    assert isinstance(fetcher, Fetcher) and fetcher.log is print
    assert (fetcher.workers, fetcher.timeout, fetcher.max_bytes, fetcher.domain_delay) == (3, 7.0, 5000, 0.25)


@pytest.mark.spec("WEB-12")
@pytest.mark.parametrize("key,value", [("learn_rate", 0), ("learn_pages", 2.5), ("learn_min_pages", 0),
                                       ("learn_backoff", -1), ("fetch_workers", True), ("fetch_max_bytes", "big")])
def test_check_rejects_learn_limits_out_of_range(key, value):
    Config.save(**{key: value})
    with pytest.raises(SystemExit, match=key):
        Internet.check()
