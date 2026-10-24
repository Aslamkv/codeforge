"""The Internet plugin: web search and page reading through a backend."""

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

lazy from .fetcher import Fetcher
lazy from .jobs import journaled
from .messages import say
from .settings import Config, first_line, is_number, number_from


class Internet:
    """The Internet plugin: web search and web fetch through the backend the setting "web" names
    (one of backends()), and, only when set, its "web_fallback": what the first cannot answer, the
    fallback is asked, and a first backend that is not usable (no key) leaves the fallback alone.
    Used only when asked: by tinker learn, by the recall phase of a run started with --web, and by research
    TOPIC from a chat (channels); nothing else goes online. Asked for and not usable, it says why before
    anything starts. Its calls never raise: what fails gives None and why() says why. A moment's
    trouble (a timeout, a rate limit) is asked again once; a backend that cannot be reached twice in
    a row is not asked again by the same command, and once none is left the run carries on without."""
    RETRY_PAUSE_SECONDS = 3
    FAILURES_BEFORE_GONE = 2
    LEARN_LIMITS = {     # the settings of learning from the web: (a whole number?, the least it may be)
        "learn_rate": (False, 1), "learn_backoff": (False, 0), "learn_cutoff": (False, 0), "learn_pages": (True, 1),
        "learn_min_pages": (True, 1), "learn_searches": (True, 1), "fetch_workers": (True, 1),
        "fetch_timeout": (False, 1), "fetch_max_bytes": (True, 1000), "fetch_domain_delay": (False, 0),
        "fetch_fallbacks": (True, 0), "docs_pages": (True, 1), "tool_docs_pages": (True, 0),
        "docs_latest_days": (False, 0)}

    def __init__(self):
        self.errors, self.failures, self.codes = {}, {}, {}  # codes: the HTTP status of a key's last failure
        self.backends_used, reasons = self.ready_backends()
        self.gone = "" if self.backends_used else ("; ".join(reasons) or "no web backend (tinker use-web NAME)")
        self.skipped = reasons  # backends set but not usable (said once, when the plugin is in use)

    def ready_backends(self):
        """([the backends set, in order, that can be used], [why each of the others cannot])."""
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
        return ready, reasons

    @staticmethod
    def required(option):
        """The plugin, for the option that asks for it: its settings checked and a backend ready
        (else an error that says why, before anything starts)."""
        Internet.check()
        internet = Internet()
        if not internet.available():
            raise SystemExit(f"{option} needs the Internet plugin: {internet.problem()}")
        for reason in internet.skipped:
            say.warn(f"Web: {reason}; using {internet.describe()}")
        return internet

    @staticmethod
    def state():
        """"ready" (with the backends in use), or why the plugin would not work."""
        try:
            Internet.check()
        except SystemExit as exc:
            return f"not usable: {exc}"
        internet = Internet()
        if not internet.available():
            return internet.problem()
        skipped = f"; not usable: {'; '.join(internet.skipped)}" if internet.skipped else ""
        return f"ready: {internet.describe()}{skipped}"

    @staticmethod
    def check():
        """web, web_fallback, web_timeout, learn_missing and the limits of learning are usable."""
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
        """The web backends, by the name the setting "web" gives one: add a new one here."""
        return {"ollama": OllamaWeb, "duckduckgo": DuckDuckGoWeb}

    def available(self):
        return not self.gone

    def problem(self, key=None):
        return self.gone or self.errors.get(key) or "no answer"

    def rate_limited(self, key):
        """Whether the last failure for this key was a rate limit (HTTP 429)."""
        return self.codes.get(key) == 429

    def describe(self):
        return " then ".join(backend.name for backend in self.backends_used)

    @property
    def backend(self):
        return self.backends_used[0] if self.backends_used else None

    @staticmethod
    def page_fetcher(log=None):
        """The plugin's own page fetcher (fetcher.Fetcher), set up from the fetch_* settings: pages read
        straight from their sites, many at once. log hears of every request."""
        return Fetcher(int(Config.get("fetch_workers")), float(Config.get("fetch_timeout")),
                       int(Config.get("fetch_max_bytes")), float(Config.get("fetch_domain_delay")), log=log)

    @staticmethod
    def download(url, max_bytes):
        """(bytes, why it failed) of a file at a web address (a book to read), by the plugin's own fetcher: no web
        backend is needed for it."""
        return Internet.page_fetcher().download(url, max_bytes)

    def search(self, query, count):
        """[{title, url, content}] the web search finds (web addresses only), or None. In a job, a search it made
        before is answered from its journal."""
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
        """{title, url, content} of a web page, its main text, or None: from the backends in turn. In a job, a page
        it fetched before is given from its journal."""
        return journaled({"fetch": url}, lambda: self.fetched(url))

    def fetched(self, url):
        usable = lambda page: (isinstance(page, dict) and bool(str(page.get("content") or "").strip())
                               or "its fetch gave no text")
        page = self.call(url, lambda backend: backend.fetch(url), usable)
        if page is None:
            return None
        return {"title": str(page.get("title") or ""), "url": url, "content": str(page.get("content") or "")}

    def call(self, key, action, usable):
        """What the first backend that answers gives (usable(result): True, or why not); None when
        none does (errors[key] says why)."""
        reasons = []
        self.codes.pop(key, None)
        for backend in list(self.backends_used):
            result, why = self.ask_backend(backend, action, usable, key)
            if not why:
                self.codes.pop(key, None)  # answered: an earlier try's rate limit no longer counts
                return result
            reasons.append(f"{backend.name}: {why}")
        self.errors[key] = "; ".join(reasons) or self.gone or "no answer"
        return None

    def ask_backend(self, backend, action, usable, key):
        """(result, "") from one backend, asked again once after a moment's trouble, or (None, why
        not). A network failure counts towards leaving the backend (lost)."""
        for attempt in (1, 2):
            try:
                result = action(backend)
            except WebError as exc:
                self.codes[key] = exc.code
                if exc.retry and attempt == 1:
                    time.sleep(self.RETRY_PAUSE_SECONDS)
                    continue
                if exc.network:
                    self.lost(backend, str(exc))
                return None, str(exc)
            except Exception as exc:  # a backend's bug never stops Tinker
                return None, f"failed: {type(exc).__name__}: {exc}"
            self.failures[backend.name] = 0
            verdict = usable(result)
            return (result, "") if verdict is True else (None, verdict)
        return None, "no answer"

    def lost(self, backend, why):
        """A network failure: after FAILURES_BEFORE_GONE in a row, that backend is not asked again; once no
        backend is left, the plugin is gone."""
        self.failures[backend.name] = self.failures.get(backend.name, 0) + 1
        if self.failures[backend.name] >= self.FAILURES_BEFORE_GONE and backend in self.backends_used:
            self.backends_used.remove(backend)
            say.warn(f"Web: not asking {backend.name} again ({first_line(why, 120)})")
            if not self.backends_used:
                self.gone = why


Config.checks.append(Internet.check)  # tinker config checks the web's settings too


class WebBackend:
    """What a web backend gives the Internet plugin. A new one subclasses this, implements its
    three methods, and is added to Internet.backends() under the name the setting "web" uses.
    search() and fetch() raise WebError when they cannot answer."""

    name = ""  # the name the setting "web" gives it (set when the plugin takes it)

    def __init__(self, timeout):
        self.timeout = timeout

    def unready(self):
        """Why it cannot be used ("" when it can), found without going online (a missing key)."""
        raise NotImplementedError

    def search(self, query, count):
        """[{"title", "url", "content"}]: up to count results for the query."""
        raise NotImplementedError

    def fetch(self, url):
        """{"title", "url", "content"}: the page's main text."""
        raise NotImplementedError


class OllamaWeb(WebBackend):
    """Ollama's web search and web fetch (ollama.com/api/web_search and /api/web_fetch), with the
    API key of a free ollama.com account (ollama.com/settings/keys) in the OLLAMA_API_KEY
    environment variable, as Ollama's own tools take it."""
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
        return data["results"]  # the plugin keeps what is usable (Internet.search)

    def fetch(self, url):
        return self.post("/web_fetch", {"url": url})

    def post(self, path, payload):
        """The JSON ollama.com answers with; WebError when it does not."""
        request = urllib.request.Request(self.BASE + path, data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {self.key}",
                                                  "Content-Type": "application/json", "User-Agent": "Tinker"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(self.MAX_ANSWER_BYTES)
        except urllib.error.HTTPError as exc:
            raise self.refused(exc.code, path)
        except Exception as exc:  # no answer at all: timeout, refused, no network
            reason, timed_out = unreached(exc)
            raise WebError(f"ollama.com cannot be reached ({reason})", network=True, retry=timed_out)
        return self.json_object(body)

    def refused(self, code, path):
        """The WebError of an HTTP error: a key refused, a rate limit or the service down is the network
        failing; a fetch's 5xx is that page, not the service."""
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
    """Web search without a key or an account: DuckDuckGo's HTML results page; a page is fetched
    from its own server and its main text taken from its HTML (no scripts, styles, menus). Only
    the standard library: nothing to install."""
    SEARCH = "https://html.duckduckgo.com/html/"
    USER_AGENT = "Mozilla/5.0 (compatible; Tinker)"
    MAX_BYTES = 2_000_000
    RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', re.S)
    SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    TAG_RE = re.compile(r"<[^>]+>")

    def unready(self):
        return ""

    def search(self, query, count):
        body = self.get(self.SEARCH, self.timeout, form={"q": query})[0]  # a GET is taken for a bot more often
        results = self.results(body)
        if not results and self.bot_check(body):
            raise WebError("DuckDuckGo answered with its bot check instead of results (too many searches; it "
                           "passes after a while)", retry=True)
        return results[:count]

    @staticmethod
    def bot_check(body):
        """Whether the page is DuckDuckGo asking whether a person searches, not its results."""
        return "anomaly" in body.lower() or "result__a" not in body

    def results(self, body):
        """The results of a results page, without its ads (they link to DuckDuckGo itself)."""
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
        """{"title", "url", "content"} of a page read from its own server: HTML as its main text,
        plain text and Markdown as they are."""
        body, kind = cls.get(url, timeout)
        if cls.is_html(body, kind):
            text = PageText.of(body)
            return {"title": text.title, "url": url, "content": text.content()}
        if kind.startswith("text/") or kind in ("", "application/json", "application/xml"):
            return {"title": "", "url": url, "content": body}
        raise WebError(f"{url} is not a text page ({kind})")

    @staticmethod
    def is_html(body, kind):
        """Whether a page is HTML, by its content type or, when a server names none, by how it starts."""
        return "html" in kind or body.lstrip()[:200].lower().startswith(("<!doctype html", "<html"))

    @classmethod
    def get(cls, url, timeout, form=None):
        """(text, content type) of a GET (a POST of the form, if given); WebError when it fails."""
        data = urllib.parse.urlencode(form).encode("utf-8") if form else None
        headers = {"User-Agent": cls.USER_AGENT, "Accept-Language": "en"}
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                kind = (response.headers.get_content_type() or "").lower()
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read(cls.MAX_BYTES).decode(charset, errors="replace"), kind
        except urllib.error.HTTPError as exc:
            raise WebError(f"{urllib.parse.urlsplit(url).netloc} answered HTTP {exc.code}",
                           retry=exc.code == 429 or exc.code >= 500, code=exc.code)
        except Exception as exc:  # no answer: timeout, refused, no network
            reason, timed_out = unreached(exc)
            host = urllib.parse.urlsplit(url).netloc
            raise WebError(f"{host} cannot be reached ({reason})", network=host in cls.SEARCH and not timed_out,
                           retry=timed_out)

    @staticmethod
    def target(href):
        """The address a result links to (DuckDuckGo wraps it in its own redirect)."""
        if href.startswith("//"):
            href = "https:" + href
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        return query["uddg"][0] if "uddg" in query else (href if is_web_address(href) else "")

    @classmethod
    def plain(cls, fragment):
        return " ".join(html.unescape(cls.TAG_RE.sub("", fragment)).split())


class PageText(html.parser.HTMLParser):
    """A web page's main text from its HTML: what is in <main> or <article> when it has one, else
    the body; never scripts, styles, menus, headers, footers or forms. Headings, list items, code
    blocks and paragraphs keep their own lines (code keeps its layout)."""
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "template",
            "iframe", "select"}
    BLOCKS = {"p", "div", "section", "article", "main", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
              "pre", "table", "ul", "ol", "dl", "dt", "dd", "blockquote", "hr"}
    VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
    SKIP_ROLES = {"navigation", "banner", "contentinfo", "search", "menu", "menubar", "toolbar", "dialog"}
    EMPTY_LINE_RE = re.compile(r"[-*#|»›>·•\s]*")  # a line left of a menu: bullets and separators only

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.skipping, self.pre = "", 0, 0
        self.in_title, self.depth = False, 0
        self.all, self.main, self.main_depth = [], [], None  # main_depth: the depth <main>/<article> opened at

    @classmethod
    def of(cls, markup):
        parser = cls()
        try:
            parser.feed(markup)
            parser.close()
        except Exception:  # broken HTML: what was read so far
            pass
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
            if not PageText.EMPTY_LINE_RE.fullmatch(line):
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
            self.in_title = True  # the page's own title (an icon's <title> is skipped with its <svg>)
        attributes = dict(attrs)
        if self.skipping or self.not_content(tag, attributes):
            self.skipping += 1
            return
        if self.opens_main(tag, attributes) and self.main_depth is None:
            self.main_depth = self.depth
        if tag == "pre":
            self.pre += 1
        if tag in self.BLOCKS:
            self.add("\n" + self.line_mark(tag))

    @staticmethod
    def opens_main(tag, attributes):
        """Whether a tag opens the page's main content."""
        return tag in ("main", "article") or attributes.get("role") == "main"

    def not_content(self, tag, attributes):
        """A script, a menu, a header or footer, or something hidden."""
        return tag in self.SKIP or attributes.get("role") in self.SKIP_ROLES \
            or attributes.get("aria-hidden") == "true" or "hidden" in attributes

    @staticmethod
    def line_mark(tag):
        """How a block's line begins: a heading's #s, a list item's dash."""
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
            if self.main_depth is not None and self.depth == self.main_depth:
                self.main_depth = -1  # closed: what follows is not main any more
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
        """Text with its whitespace runs made one space (a run at either end kept as one)."""
        return (" " if data[0].isspace() else "") + " ".join(data.split()) + (" " if data[-1].isspace() else "")

    def add(self, text):
        self.all.append(text)
        if self.main_depth is not None and self.main_depth > 0:
            self.main.append(text)


class WebError(Exception):
    """A web backend that could not answer; network: the backend cannot be reached at all (no
    network, its service is down, its key refused), so it is not asked again once that repeats;
    retry: it may answer if asked again in a moment (a timeout, its rate limit, a server error);
    code: the HTTP status it answered with, if any."""

    def __init__(self, message, network=False, retry=False, code=None):
        super().__init__(message)
        self.network, self.retry, self.code = network, retry, code


def unreached(exc):
    """(why a request had no answer at all, whether it ran out of time)."""
    reason = first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)
    return reason, isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in reason


def is_web_address(url):
    return str(url).startswith(("http://", "https://"))
