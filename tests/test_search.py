import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tinkerkit.search import Mwmbl, SearchFailed, Wikibooks, Wikipedia


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answering(monkeypatch, body):
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        if isinstance(body, BaseException):
            raise body
        return Response(body if isinstance(body, bytes) else json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def asked(request):
    parts = urllib.parse.urlsplit(request.full_url)
    return parts.netloc + parts.path, dict(urllib.parse.parse_qsl(parts.query))


MWMBL = [
    {
        "url": "https://doc.rust-lang.org/book/",
        "title": [{"value": "The Rust ", "is_bold": False}, {"value": "Book", "is_bold": True}],
        "extract": "Learn Rust",
        "source": "mwmbl",
    },
    {"url": "https://rust-lang.org", "title": "Rust", "extract": [{"value": "A language", "is_bold": False}]},
    {"url": "https://third.org", "title": "Third", "extract": ""},
]

WIKI = {
    "query": {
        "search": [
            {
                "title": "Rust (programming language)",
                "snippet": '<span class="m">Rust</span> is fast &amp; safe',
            },
            {"title": "C++", "snippet": "A language"},
        ]
    }
}


@pytest.mark.spec("WEB-2")
def test_mwmbl_asks_with_plain_keywords_and_joins_segments(monkeypatch):
    sent = answering(monkeypatch, MWMBL)
    assert Mwmbl(5).search("rust book", 2) == [
        {"title": "The Rust Book", "url": "https://doc.rust-lang.org/book/", "content": "Learn Rust"},
        {"title": "Rust", "url": "https://rust-lang.org", "content": "A language"},
    ]
    assert asked(sent[0]) == ("api.mwmbl.org/api/v1/search/", {"s": "rust book"})


@pytest.mark.spec("WEB-2")
@pytest.mark.parametrize(
    "engine,host", [(Wikipedia(5), "en.wikipedia.org"), (Wikibooks(5), "en.wikibooks.org")]
)
def test_a_mediawiki_asks_its_own_hosts_search_and_drops_snippet_html(monkeypatch, engine, host):
    sent = answering(monkeypatch, WIKI)
    assert engine.search("rust", 2) == [
        {
            "title": "Rust (programming language)",
            "url": f"https://{host}/wiki/Rust_(programming_language)",
            "content": "Rust is fast & safe",
        },
        {"title": "C++", "url": f"https://{host}/wiki/C%2B%2B", "content": "A language"},
    ]
    where, args = asked(sent[0])
    assert where == f"{host}/w/api.php"
    assert args == {
        "action": "query",
        "list": "search",
        "srsearch": "rust",
        "srlimit": "2",
        "format": "json",
        "maxlag": "5",
    }


@pytest.mark.spec("WEB-2")
@pytest.mark.parametrize("engine,body", [(Mwmbl(5), MWMBL), (Wikipedia(5), WIKI)])
def test_engines_send_a_user_agent_naming_tinker_and_its_repository(monkeypatch, engine, body):
    sent = answering(monkeypatch, body)
    engine.search("q", 1)
    agent = sent[0].get_header("User-agent")
    assert "Tinker" in agent and "https://github.com/Aslamkv/codeforge" in agent


@pytest.mark.spec("WEB-2")
@pytest.mark.parametrize(
    "engine,body",
    [
        (Mwmbl(5), b"not json"),
        (Mwmbl(5), {"results": []}),
        (Wikipedia(5), b"<html>"),
        (Wikipedia(5), {"error": {"code": "maxlag"}}),
        (Wikipedia(5), {"query": {}}),
    ],
)
def test_answers_without_json_or_a_result_list_are_failures(monkeypatch, engine, body):
    answering(monkeypatch, body)
    with pytest.raises(SearchFailed):
        engine.search("q", 3)


@pytest.mark.spec("WEB-2")
@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://x", 503, "err", {}, io.BytesIO(b"")),
        TimeoutError("timed out"),
        urllib.error.URLError(OSError("no route")),
    ],
)
def test_web_failures_reach_the_caller_as_they_are(monkeypatch, error):
    answering(monkeypatch, error)
    with pytest.raises(type(error)) as raised:
        Mwmbl(5).search("q", 3)
    assert raised.value is error
