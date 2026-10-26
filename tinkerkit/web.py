import base64
import re
from collections import namedtuple
lazy import json
lazy import threading
lazy import time
lazy import urllib.error
lazy from collections import Counter
lazy from concurrent.futures import ThreadPoolExecutor
lazy from itertools import zip_longest

from .messages import say
from .settings import MAX_PLANNED_SEARCHES, SEARCHES_PER_ENGINE, Config, Home
from .text import first_line
lazy from .fetcher import Fetcher
lazy from .jobs import journaled
lazy from .passages import Block, MainBlocks, is_html, text_blocks
lazy from .search import Mwmbl, SearchFailed, Wikibooks, Wikipedia

TEXT_KINDS = (
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/json",
    "application/xml",
    "text/xml",
)


class Internet:
    def __init__(self, engines=None):
        timeout = float(Config.get("web_timeout"))
        self.engines = engines or [engine(timeout) for engine in search_engines()]
        self.slots = {engine.name: threading.Semaphore(SEARCHES_PER_ENGINE) for engine in self.engines}
        self.requests = RequestLog()
        self.offline = ""
        self.offline_lock = threading.Lock()

    @staticmethod
    def state():
        return "searches " + ", ".join(engine.name for engine in search_engines())

    def search(self, query, count, engines=None):
        return self.search_all([{"query": query, "engines": engines or []}], count)[0]

    def search_all(self, searches, count):
        searches = self.planned(searches, count)
        answers = journaled(
            {"search": searches, "count": count},
            lambda: self.searched_all(searches),
            keep=lambda answers: None not in answers,
        )
        return [answer or [] for answer in answers]

    def planned(self, searches, count):
        planned = [self.normalized(search, count) for search in searches]
        return [search for search in planned if search["query"]][:MAX_PLANNED_SEARCHES]

    def normalized(self, search, count):
        if not isinstance(search, dict):
            search = {"query": search}
        query = search.get("query")
        return {
            "query": query.strip() if isinstance(query, str) else "",
            "engines": self.known_engines(search.get("engines")),
            "count": planned_count(search.get("count"), count),
        }

    def known_engines(self, asked):
        names = [engine.name for engine in self.engines]
        if isinstance(asked, str):
            asked = [asked]
        if not isinstance(asked, list):
            asked = []
        wanted = {name.strip().lower() for name in asked if isinstance(name, str)}
        return [name for name in names if name in wanted] or names

    def searched_all(self, searches):
        if self.offline:
            return [None for _ in searches]
        with ThreadPoolExecutor(max_workers=max(1, len(searches) * len(self.engines))) as pool:
            asked = [
                [
                    pool.submit(self.answered, engine, search["query"], search["count"])
                    for engine in self.engines
                    if engine.name in search["engines"]
                ]
                for search in searches
            ]
            return [
                self.merged([future.result() for future in futures], search["count"])
                for search, futures in zip(searches, asked)
            ]

    def merged(self, answers, count):
        found = [answer for answer in answers if not isinstance(answer, Unanswered)]
        if found:
            return interleaved(found, count)
        if all(answer.unreachable for answer in answers):
            self.go_offline("; ".join(answer.why for answer in answers))
        return None

    def answered(self, engine, query, count):
        answer = self.asked(engine, query, count)
        if isinstance(answer, Unanswered) and answer.again:
            return self.asked(engine, query, count)
        return answer

    def asked(self, engine, query, count):
        with self.slots[engine.name]:
            if self.offline:
                return Unanswered(self.offline, again=False, unreachable=True)
            started = time.monotonic()
            try:
                found = engine.search(query, count)
            except Exception as exc:
                failure = Unanswered.of(engine.name, exc)
                self.requests("search", engine.name, failure.why, time.monotonic() - started, query)
                return failure
            self.requests("search", engine.name, "", time.monotonic() - started, query)
            return found

    def go_offline(self, why):
        with self.offline_lock:
            if self.offline:
                return
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
            self.requests("fetch", "fetcher", fetched.error, fetched.seconds, fetched.url)
        return pages

    def fetcher(self, max_bytes):
        return Fetcher(
            int(Config.get("fetch_workers")),
            float(Config.get("fetch_timeout")),
            int(max_bytes or Config.get("fetch_max_bytes")),
        )


def search_engines():
    return (Mwmbl, Wikipedia, Wikibooks)


def planned_count(asked, count):
    if isinstance(asked, bool):
        return count
    try:
        asked = int(asked)
    except TypeError, ValueError:
        return count
    return min(asked, count) if asked > 0 else count


def interleaved(answers, count):
    ranked = [result for rank in zip_longest(*map(results, answers)) for result in rank if result]
    unique = {}
    for result in ranked:
        unique.setdefault(result["url"], result)
    return list(unique.values())[:count]


def results(found):
    return [
        {
            "title": str(result.get("title") or ""),
            "url": str(result["url"]),
            "content": str(result.get("content") or ""),
        }
        for result in found
        if isinstance(result, dict) and is_web_address(result.get("url", ""))
    ]


class Unanswered(namedtuple("Unanswered", "why again unreachable")):
    @classmethod
    def of(cls, name, exc):
        if isinstance(exc, urllib.error.HTTPError):
            return cls(f"{name} answered HTTP {exc.code}", again=exc.code >= 500, unreachable=False)
        if timed_out(exc):
            return cls(f"{name}: {unreached(exc)}", again=True, unreachable=False)
        if isinstance(exc, urllib.error.URLError):
            return cls(f"{name}: {unreached(exc)}", again=False, unreachable=True)
        if isinstance(exc, SearchFailed):
            return cls(str(exc), again=False, unreachable=False)
        return cls(f"{name} failed: {type(exc).__name__}: {exc}", again=False, unreachable=False)


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


def unreached(exc):
    return first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)


def timed_out(exc):
    return isinstance(getattr(exc, "reason", exc), TimeoutError) or "timed out" in unreached(exc)


def is_web_address(url):
    return str(url).startswith(("http://", "https://"))
