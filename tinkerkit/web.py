import html
import html.parser
lazy import json
lazy import os
import re
lazy import socket
lazy import time
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request
from collections import namedtuple

lazy from .fetcher import Fetcher
lazy from .jobs import journaled
from .messages import say
from .settings import Config, first_line, is_number, number_from

MAIN_CLOSED = -1

Unreached = namedtuple("Unreached", "why timed_out")
ReadyBackends = namedtuple("ReadyBackends", "ready reasons")
BackendAnswer = namedtuple("BackendAnswer", "result why")
Got = namedtuple("Got", "text content_type")
Limit = namedtuple("Limit", "whole least")


class Internet:
    RETRY_PAUSE_SECONDS = 3
    FAILURES_BEFORE_GONE = 2
    LEARN_LIMITS = {
        "learn_rate": Limit(False, 1), "learn_backoff": Limit(False, 0), "learn_cutoff": Limit(False, 0),
        "learn_pages": Limit(True, 1), "learn_min_pages": Limit(True, 1), "learn_searches": Limit(True, 1),
        "fetch_workers": Limit(True, 1), "fetch_timeout": Limit(False, 1), "fetch_max_bytes": Limit(True, 1000),
        "fetch_domain_delay": Limit(False, 0), "fetch_fallbacks": Limit(True, 0), "docs_pages": Limit(True, 1),
        "tool_docs_pages": Limit(True, 0), "docs_latest_days": Limit(False, 0)}

    def __init__(self):
        self.errors, self.failures, self.last_failure_status = {}, {}, {}
        self.backends_used, reasons = self.ready_backends()
        self.gone = "" if self.backends_used else ("; ".join(reasons) or "no web backend (tinker use-web NAME)")
        self.unusable_backends = reasons

    def ready_backends(self):
        ready, reasons = [], []
        timeout = float(Config.get("web_timeout"))
        for name in dict.fromkeys(name for name in (Config.get("web"), Config.get("web_fallback")) if name):
            backend = self.backends()[name](timeout)
            why = backend.unready()
            if why:
                reasons.append(f"{name}: {why}")
            else:
                backend.name = name
                ready.append(backend)
        return ReadyBackends(ready, reasons)

    @staticmethod
    def required(option):
        Internet.check()
        internet = Internet()
        if not internet.available():
            raise SystemExit(f"{option} needs the Internet plugin: {internet.problem()}")
        for reason in internet.unusable_backends:
            say.warn(f"Web: {reason}; using {internet.describe()}")
        return internet

    @staticmethod
    def state():
        try:
            Internet.check()
        except SystemExit as exc:
            return f"not usable: {exc}"
        internet = Internet()
        if not internet.available():
            return internet.problem()
        skipped = f"; not usable: {'; '.join(internet.unusable_backends)}" if internet.unusable_backends else ""
        return f"ready: {internet.describe()}{skipped}"

    @staticmethod
    def check():
        for key in ("web", "web_fallback"):
            Internet.check_backend_name(key)
        Internet.check_timeout()
        Internet.check_learn_missing()
        Internet.check_learn_limits()

    @staticmethod
    def check_backend_name(key):
        if Config.get(key) not in ("", *Internet.backends()):
            raise SystemExit(f"{key} must be one of {', '.join(map(repr, Internet.backends()))} "
                             f'or "" (none); it is {Config.get(key)!r}')

    @staticmethod
    def check_timeout():
        timeout = Config.get("web_timeout")
        if not is_number(timeout) or timeout <= 0:
            raise SystemExit(f"web_timeout must be a number of seconds above 0; it is {timeout!r}")

    @staticmethod
    def check_learn_missing():
        missing = Config.get("learn_missing")
        if not number_from(missing, 1):
            raise SystemExit(f"learn_missing must be a whole number, 1 or more (the topics a run "
                             f"with --web may learn); it is {missing!r}")

    @staticmethod
    def check_learn_limits():
        for key, (whole, least) in Internet.LEARN_LIMITS.items():
            value = Config.get(key)
            if not number_from(value, least, whole):
                kind = "a whole number" if whole else "a number"
                raise SystemExit(f"{key} must be {kind}, {least} or more; it is {value!r}")

    @staticmethod
    def backends():
        return {"ollama": OllamaWeb, "duckduckgo": DuckDuckGoWeb}

    def available(self):
        return not self.gone

    def problem(self, key=None):
        return self.gone or self.errors.get(key) or "no answer"

    def rate_limited(self, key):
        return self.last_failure_status.get(key) == 429

    def describe(self):
        return " then ".join(backend.name for backend in self.backends_used)

    @property
    def backend(self):
        return self.backends_used[0] if self.backends_used else None

    @staticmethod
    def page_fetcher(log=None):
        return Fetcher(int(Config.get("fetch_workers")), float(Config.get("fetch_timeout")),
                       int(Config.get("fetch_max_bytes")), float(Config.get("fetch_domain_delay")), log=log)

    @staticmethod
    def download(url, max_bytes):
        return Internet.page_fetcher().download(url, max_bytes)

    def search(self, query, count):
        return journaled({"search": query, "count": count}, lambda: self.searched(query, count))

    def searched(self, query, count):
        found = self.call(query, lambda backend: backend.search(query, count),
                          lambda result: isinstance(result, list) or "its search gave no list")
        if found is None:
            return None
        return [self.as_result(result) for result in found if isinstance(result, dict)
                and is_web_address(result.get("url", ""))]

    @staticmethod
    def as_result(found):
        return {"title": str(found.get("title") or ""), "url": str(found["url"]),
                "content": str(found.get("content") or "")}

    def fetch(self, url):
        return journaled({"fetch": url}, lambda: self.fetched(url))

    def fetched(self, url):
        usable = lambda page: (isinstance(page, dict) and bool(str(page.get("content") or "").strip())
                               or "its fetch gave no text")
        page = self.call(url, lambda backend: backend.fetch(url), usable)
        if page is None:
            return None
        return {"title": str(page.get("title") or ""), "url": url, "content": str(page.get("content") or "")}

    def call(self, key, action, usable):
        reasons = []
        self.last_failure_status.pop(key, None)
        for backend in list(self.backends_used):
            result, why = self.ask_backend(backend, action, usable, key)
            if not why:
                self.last_failure_status.pop(key, None)
                return result
            reasons.append(f"{backend.name}: {why}")
        self.errors[key] = "; ".join(reasons) or self.gone or "no answer"
        return None

    def ask_backend(self, backend, action, usable, key):
        for attempt in (1, 2):
            try:
                result = action(backend)
            except WebError as exc:
                self.last_failure_status[key] = exc.code
                if exc.retry and attempt == 1:
                    time.sleep(self.RETRY_PAUSE_SECONDS)
                    continue
                if exc.network:
                    self.lost(backend, str(exc))
                return BackendAnswer(None, str(exc))
            except Exception as exc:
                return BackendAnswer(None, f"failed: {type(exc).__name__}: {exc}")
            self.failures[backend.name] = 0
            verdict = usable(result)
            return BackendAnswer(result, "") if verdict is True else BackendAnswer(None, verdict)
        return BackendAnswer(None, "no answer")

    def lost(self, backend, why):
        self.failures[backend.name] = self.failures.get(backend.name, 0) + 1
        if self.failures[backend.name] >= self.FAILURES_BEFORE_GONE and backend in self.backends_used:
            self.backends_used.remove(backend)
            say.warn(f"Web: not asking {backend.name} again ({first_line(why, 120)})")
            if not self.backends_used:
                self.gone = why



class WebBackend:
    name = ""

    def __init__(self, timeout):
        self.timeout = timeout

    def unready(self):
        raise NotImplementedError

    def search(self, query, count):
        raise NotImplementedError

    def fetch(self, url):
        raise NotImplementedError


class OllamaWeb(WebBackend):
    BASE = "https://ollama.com/api"
    KEY_ENV = "OLLAMA_API_KEY"
    MAX_ANSWER_BYTES = 2_000_000

    def __init__(self, timeout):
        super().__init__(timeout)
        self.key = os.environ.get(self.KEY_ENV, "").strip()

    def unready(self):
        return "" if self.key else (f"no Ollama API key: set {self.KEY_ENV} (a free key from "
                                    f"ollama.com/settings/keys), or tinker use-web duckduckgo (no key)")

    def search(self, query, count):
        data = self.post("/web_search", {"query": query, "max_results": count})
        if not isinstance(data.get("results"), list):
            raise WebError("ollama.com's web search answered without results")
        return data["results"]

    def fetch(self, url):
        return self.post("/web_fetch", {"url": url})

    def post(self, path, payload):
        request = urllib.request.Request(self.BASE + path, data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {self.key}",
                                                  "Content-Type": "application/json", "User-Agent": "Tinker"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(self.MAX_ANSWER_BYTES)
        except urllib.error.HTTPError as exc:
            raise self.refused(exc.code, path)
        except Exception as exc:
            reason, timed_out = unreached(exc)
            raise WebError(f"ollama.com cannot be reached ({reason})", network=True, retry=timed_out)
        return self.json_object(body)

    def refused(self, code, path):
        hint = {401: f": check {self.KEY_ENV}", 403: f": check {self.KEY_ENV}", 429: ": its rate limit, try later"}
        return WebError(f"ollama.com answered HTTP {code}{hint.get(code, '')}",
                        network=code in (401, 403, 429) or (code >= 500 and path != "/web_fetch"),
                        retry=code == 429 or code >= 500, code=code)

    @staticmethod
    def json_object(body):
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            raise WebError("ollama.com did not answer with JSON")
        if not isinstance(data, dict):
            raise WebError("ollama.com answered with something else than a JSON object")
        return data


class DuckDuckGoWeb(WebBackend):
    SEARCH = "https://html.duckduckgo.com/html/"
    USER_AGENT = "Mozilla/5.0 (compatible; Tinker)"
    MAX_BYTES = 2_000_000
    RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', re.S)
    SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    TAG_RE = re.compile(r"<[^>]+>")

    def unready(self):
        return ""

    def search(self, query, count):
        body = self.get(self.SEARCH, self.timeout, form={"q": query})[0]
        results = self.results(body)
        if not results and self.bot_check(body):
            raise WebError("DuckDuckGo answered with its bot check instead of results (too many searches; it "
                           "passes after a while)", retry=True)
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
                results.append({"url": url, "title": self.plain(title),
                                "content": self.plain(snippet.group(1)) if snippet else ""})
        return results

    def fetch(self, url):
        return self.read_page(url, self.timeout)

    @classmethod
    def read_page(cls, url, timeout):
        body, kind = cls.get(url, timeout)
        if cls.is_html(body, kind):
            text = PageText.of(body)
            return {"title": text.title, "url": url, "content": text.content()}
        if kind.startswith("text/") or kind in ("", "application/json", "application/xml"):
            return {"title": "", "url": url, "content": body}
        raise WebError(f"{url} is not a text page ({kind})")

    @staticmethod
    def is_html(body, kind):
        return "html" in kind or body.lstrip()[:200].lower().startswith(("<!doctype html", "<html"))

    @classmethod
    def get(cls, url, timeout, form=None):
        data = urllib.parse.urlencode(form).encode("utf-8") if form else None
        headers = {"User-Agent": cls.USER_AGENT, "Accept-Language": "en"}
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                kind = (response.headers.get_content_type() or "").lower()
                charset = response.headers.get_content_charset() or "utf-8"
                return Got(response.read(cls.MAX_BYTES).decode(charset, errors="replace"), kind)
        except urllib.error.HTTPError as exc:
            raise WebError(f"{urllib.parse.urlsplit(url).netloc} answered HTTP {exc.code}",
                           retry=exc.code == 429 or exc.code >= 500, code=exc.code)
        except Exception as exc:
            reason, timed_out = unreached(exc)
            host = urllib.parse.urlsplit(url).netloc
            raise WebError(f"{host} cannot be reached ({reason})", network=host in cls.SEARCH and not timed_out,
                           retry=timed_out)

    @staticmethod
    def target(href):
        if href.startswith("//"):
            href = "https:" + href
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        return query["uddg"][0] if "uddg" in query else (href if is_web_address(href) else "")

    @classmethod
    def plain(cls, fragment):
        return " ".join(html.unescape(cls.TAG_RE.sub("", fragment)).split())


class PageText(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "template",
            "iframe", "select"}
    BLOCKS = {"p", "div", "section", "article", "main", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
              "pre", "table", "ul", "ol", "dl", "dt", "dd", "blockquote", "hr"}
    VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
    SKIP_ROLES = {"navigation", "banner", "contentinfo", "search", "menu", "menubar", "toolbar", "dialog"}
    BULLETS_AND_SEPARATORS_RE = re.compile(r"[-*#|»›>·•\s]*")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.skipping, self.pre = "", 0, 0
        self.in_title, self.depth = False, 0
        self.all, self.main, self.main_opened_at = [], [], None

    @classmethod
    def of(cls, markup):
        parser = cls()
        read_what_it_can(parser, markup)
        return parser

    def content(self):
        main = self.lines(self.main)
        return main if len(main) >= 400 else self.lines(self.all)

    @staticmethod
    def lines(pieces):
        text = "".join(pieces)
        out, blank = [], False
        for line in text.split("\n"):
            line = line.rstrip()
            if not PageText.BULLETS_AND_SEPARATORS_RE.fullmatch(line):
                out.append(line)
                blank = False
            elif not blank and out:
                out.append("")
                blank = True
        return "\n".join(out).strip()

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag == "br":
                self.add("\n")
            return
        self.depth += 1
        if tag == "title" and not self.title and not self.skipping:
            self.in_title = True
        attributes = dict(attrs)
        if self.skipping or self.not_content(tag, attributes):
            self.skipping += 1
            return
        if self.opens_main(tag, attributes) and self.main_opened_at is None:
            self.main_opened_at = self.depth
        if tag == "pre":
            self.pre += 1
        if tag in self.BLOCKS:
            self.add("\n" + self.line_mark(tag))

    @staticmethod
    def opens_main(tag, attributes):
        return tag in ("main", "article") or attributes.get("role") == "main"

    def not_content(self, tag, attributes):
        return tag in self.SKIP or attributes.get("role") in self.SKIP_ROLES \
            or attributes.get("aria-hidden") == "true" or "hidden" in attributes

    @staticmethod
    def line_mark(tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return "#" * int(tag[1]) + " "
        return "- " if tag == "li" else ""

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag == "title":
            self.in_title = False
        if self.skipping:
            self.skipping -= 1
        else:
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
            if tag in self.BLOCKS and tag != "li":
                self.add("\n")
            if self.main_opened_at is not None and self.depth == self.main_opened_at:
                self.main_opened_at = MAIN_CLOSED
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data.strip()
        if self.skipping or self.in_title:
            return
        if self.pre:
            self.add(data)
        elif not data.strip():
            self.add(" " if data else "")
        else:
            self.add(self.collapsed(data))

    @staticmethod
    def collapsed(data):
        return (" " if data[0].isspace() else "") + " ".join(data.split()) + (" " if data[-1].isspace() else "")

    def add(self, text):
        self.all.append(text)
        if self.main_opened_at is not None and self.main_opened_at > 0:
            self.main.append(text)


class WebError(Exception):
    def __init__(self, message, network=False, retry=False, code=None):
        super().__init__(message)
        self.network, self.retry, self.code = network, retry, code


def unreached(exc):
    reason = first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)
    return Unreached(reason, isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in reason)


def is_web_address(url):
    return str(url).startswith(("http://", "https://"))


def read_what_it_can(parser, markup):
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
