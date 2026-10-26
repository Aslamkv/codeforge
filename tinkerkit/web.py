import base64
import html
import re
from collections import namedtuple
lazy import json
lazy import os
lazy import threading
lazy import time
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request
lazy from collections import Counter

from .messages import say
from .settings import Config, Home
from .text import first_line
lazy from .fetcher import Fetcher
lazy from .jobs import journaled
lazy from .passages import Block, MainBlocks, is_html, text_blocks

TEXT_KINDS = (
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/json",
    "application/xml",
    "text/xml",
)


class Internet:
    def __init__(self):
        timeout = float(Config.get("web_timeout"))
        self.ollama = OllamaWeb(timeout) if OllamaWeb.key() else None
        self.standard = WebBackend(timeout)
        self.requests = RequestLog()
        self.offline = ""

    @staticmethod
    def state():
        if OllamaWeb.key():
            return "ollama.com search, the standard search as fallback"
        return f"the standard search (no {OllamaWeb.KEY_ENV})"

    def search(self, query, count):
        return journaled({"search": query, "count": count}, lambda: self.searched(query, count)) or []

    def searched(self, query, count):
        if self.offline:
            return None
        found = self.ollama_searched(query, count) if self.ollama else None
        if found is None:
            found = self.standard_searched(query, count)
        return None if found is None else results(found, count)

    def ollama_searched(self, query, count):
        try:
            return self.asked("ollama", query, lambda: self.ollama.search(query, count))
        except WebError as exc:
            say.warn(f"Web: ollama.com search failed ({exc}); asking the standard search")
            return None

    def standard_searched(self, query, count, again=True):
        try:
            return self.asked("standard", query, lambda: self.standard.search(query, count))
        except WebError as exc:
            if exc.unreachable:
                self.go_offline(str(exc))
            elif exc.retry and again:
                return self.standard_searched(query, count, again=False)
            return None

    def asked(self, by, query, search):
        started = time.monotonic()
        try:
            found = search()
        except Exception as exc:
            failure = exc if isinstance(exc, WebError) else WebError(f"failed: {type(exc).__name__}: {exc}")
            self.requests("search", by, str(failure), time.monotonic() - started, query)
            raise failure
        self.requests("search", by, "", time.monotonic() - started, query)
        return found

    def go_offline(self, why):
        self.offline = why
        say.warn(f"Web: not reachable ({why})")

    def fetch(self, urls, max_bytes=None):
        urls = list(urls)
        return journaled(
            {"fetch": urls, "max_bytes": max_bytes},
            lambda: self.fetched(urls, max_bytes),
            keep=lambda pages: not self.offline,
            dump=lambda pages: [page.kept() for page in pages],
            load=lambda kept: [WebPage.from_kept(page) for page in kept],
        )

    def fetched(self, urls, max_bytes):
        if self.offline:
            return [WebPage.failed(url, f"the web is not reachable ({self.offline})") for url in urls]
        pages = []
        for fetched in self.fetcher(max_bytes).fetch_all(urls):
            pages.append(WebPage.of(fetched))
            self.requests("fetch", "standard", fetched.error, fetched.seconds, fetched.url)
        return pages

    def fetcher(self, max_bytes):
        return Fetcher(
            int(Config.get("fetch_workers")),
            float(Config.get("fetch_timeout")),
            int(max_bytes or Config.get("fetch_max_bytes")),
        )


def results(found, count):
    return [
        {
            "title": str(result.get("title") or ""),
            "url": str(result["url"]),
            "content": str(result.get("content") or ""),
        }
        for result in found
        if isinstance(result, dict) and is_web_address(result.get("url", ""))
    ][:count]


class WebPage(namedtuple("WebPage", "url kind data blocks title why")):
    @classmethod
    def of(cls, fetched):
        if not fetched.ok:
            return cls.failed(fetched.url, fetched.error)
        title, blocks = readable(fetched.text, fetched.kind)
        return cls(fetched.final_url or fetched.url, fetched.kind, fetched.data, blocks, title, "")

    @classmethod
    def failed(cls, url, why):
        return cls(url, "", b"", [], "", why)

    @property
    def text(self):
        return PageText(self.title, self.blocks).content()

    def kept(self):
        return {
            **self._asdict(),
            "data": base64.b64encode(self.data).decode("ascii"),
            "blocks": [[block.kind, block.text, block.level] for block in self.blocks],
        }

    @classmethod
    def from_kept(cls, kept):
        return cls(
            **{
                **kept,
                "data": base64.b64decode(kept["data"]),
                "blocks": [Block(*block) for block in kept["blocks"]],
            }
        )


def readable(text, kind):
    if is_html(text, kind):
        parsed = MainBlocks.parsed(text)
        return parsed.title, parsed.content()
    if kind in TEXT_KINDS:
        return "", text_blocks(text)
    return "", []


class RequestLog:
    def __init__(self):
        self.path, self.tally, self.lock = Home.web_log(), {}, threading.Lock()

    def __call__(self, kind, by, why, seconds, target):
        result = "failed" if why else "ok"
        line = json.dumps(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "kind": kind,
                "by": by,
                "result": result,
                "why": why,
                "seconds": round(seconds, 3),
                "target": target,
            }
        )
        with self.lock:
            self.tally.setdefault(kind, Counter()).update(requests=1, **{result: 1})
            with self.path.open("a") as log:
                log.write(line + "\n")

    def counts(self):
        return {kind: dict(counts) for kind, counts in self.tally.items()}

    def text(self):
        if not self.tally:
            return "none"
        return ", ".join(
            f"{kind} {counts['requests']}" + (f" ({counts['failed']} failed)" if counts["failed"] else "")
            for kind, counts in self.tally.items()
        )


class WebBackend:
    SEARCH = "https://html.duckduckgo.com/html/"
    USER_AGENT = "Mozilla/5.0 (compatible; Tinker)"
    MAX_BYTES = 2_000_000
    RESULT_RE = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', re.S
    )
    SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    TAG_RE = re.compile(r"<[^>]+>")

    def __init__(self, timeout):
        self.timeout = timeout

    def search(self, query, count):
        body = self.get(self.SEARCH, self.timeout, form={"q": query})
        results = self.results(body)
        if not results and self.bot_check(body):
            raise WebError("DuckDuckGo answered with its bot check instead of results")
        return results[:count]

    @staticmethod
    def bot_check(body):
        return "anomaly" in body.lower() or "result__a" not in body

    def results(self, body):
        results = []
        for href, title, rest in self.RESULT_RE.findall(body):
            url = self.target(html.unescape(href))
            snippet = self.SNIPPET_RE.search(rest)
            if url and "duckduckgo.com/y.js" not in url:
                results.append(
                    {
                        "url": url,
                        "title": self.plain(title),
                        "content": self.plain(snippet.group(1)) if snippet else "",
                    }
                )
        return results

    @classmethod
    def get(cls, url, timeout, form=None):
        data = urllib.parse.urlencode(form).encode("utf-8") if form else None
        headers = {"User-Agent": cls.USER_AGENT, "Accept-Language": "en"}
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read(cls.MAX_BYTES).decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            raise WebError(f"{host(url)} answered HTTP {exc.code}", retry=exc.code >= 500)
        except Exception as exc:
            raise WebError(
                f"{host(url)}: {unreached(exc)}", retry=timed_out(exc), unreachable=not timed_out(exc)
            )

    @staticmethod
    def target(href):
        if href.startswith("//"):
            href = "https:" + href
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        return query["uddg"][0] if "uddg" in query else (href if is_web_address(href) else "")

    @classmethod
    def plain(cls, fragment):
        return " ".join(html.unescape(cls.TAG_RE.sub("", fragment)).split())


class OllamaWeb(WebBackend):
    BASE = "https://ollama.com/api"
    KEY_ENV = "OLLAMA_API_KEY"
    HINTS = {401: ": check OLLAMA_API_KEY", 403: ": check OLLAMA_API_KEY", 429: ": its rate limit"}

    @classmethod
    def key(cls):
        return os.environ.get(cls.KEY_ENV, "").strip()

    def search(self, query, count):
        data = self.post("/web_search", {"query": query, "max_results": count})
        if not isinstance(data.get("results"), list):
            raise WebError("ollama.com's web search answered without results")
        return data["results"]

    def post(self, path, payload):
        request = urllib.request.Request(
            self.BASE + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.key()}",
                "Content-Type": "application/json",
                "User-Agent": "Tinker",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(self.MAX_BYTES)
        except urllib.error.HTTPError as exc:
            raise WebError(f"ollama.com answered HTTP {exc.code}{self.HINTS.get(exc.code, '')}")
        except Exception as exc:
            raise WebError(f"ollama.com: {unreached(exc)}")
        return self.json_object(body)

    @staticmethod
    def json_object(body):
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            raise WebError("ollama.com did not answer with JSON")
        if not isinstance(data, dict):
            raise WebError("ollama.com answered with something else than a JSON object")
        return data


class PageText:
    SEPARATORS_ONLY_RE = re.compile(r"[-*#|»›>·•\s]*")

    def __init__(self, title, blocks):
        self.title = title
        self.blocks = [block for block in blocks if not self.SEPARATORS_ONLY_RE.fullmatch(block.text)]

    def content(self):
        text = ""
        for before, block in zip([None, *self.blocks], self.blocks):
            together = before is not None and before.kind == block.kind == "item"
            text += ("\n" if together else "\n\n" if text else "") + self.line(block)
        return text

    @staticmethod
    def line(block):
        if block.kind == "heading":
            return "#" * max(1, block.level) + " " + block.text
        return "- " + block.text if block.kind == "item" else block.text


class WebError(Exception):
    def __init__(self, message, retry=False, unreachable=False):
        super().__init__(message)
        self.retry, self.unreachable = retry, unreachable


def unreached(exc):
    return first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)


def timed_out(exc):
    return isinstance(exc, TimeoutError) or "timed out" in unreached(exc)


def host(url):
    return urllib.parse.urlsplit(url).netloc


def is_web_address(url):
    return str(url).startswith(("http://", "https://"))
