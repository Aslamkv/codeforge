"""The Internet plugin: web search and page reading through a backend."""

import html
import html.parser
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .settings import CONFIG_FILE, Config, first_line


class Internet:
    """The Internet plugin: web search and web fetch through the backend config.json's "web" names
    (one of backends()), and, only when set, its "web_fallback": what the first cannot answer, the
    fallback is asked, and a first backend that is not usable (no key) leaves the fallback alone.
    Used only when asked: by tinker learn, by the recall phase of a run started with --web, and by research
    TOPIC from a chat (channels); nothing else goes online. Asked for and not usable, it says why before
    anything starts. Its calls never raise: what fails gives None and why() says why. A moment's
    trouble (a timeout, a rate limit) is asked again once; a backend that cannot be reached twice in
    a row is not asked again by the same command, and once none is left the run carries on without."""
    RETRY_PAUSE = 3      # seconds before asking again after a moment's trouble
    GONE_AFTER = 2       # network failures in a row after which a backend is not asked again

    def __init__(self):
        self.errors, self.backends_used, self.failures = {}, [], {}
        reasons = []
        timeout = float(Config.get("web_timeout"))
        for name in dict.fromkeys(n for n in (Config.get("web"), Config.get("web_fallback")) if n):
            backend = self.backends()[name](timeout)
            why = backend.unready()
            if why:
                reasons.append(f"{name}: {why}")
            else:
                backend.name = name
                self.backends_used.append(backend)
        self.gone = "" if self.backends_used else ("; ".join(reasons) or f"no web backend ({CONFIG_FILE.name}: web)")
        self.skipped = reasons  # backends set but not usable (said once, when the plugin is in use)

    @property
    def backend(self):
        return self.backends_used[0] if self.backends_used else None

    @staticmethod
    def required(option):
        """The plugin, for the option that asks for it: its settings checked and a backend ready
        (else an error that says why, before anything starts)."""
        Internet.check()
        internet = Internet()
        if not internet.available():
            raise SystemExit(f"{option} needs the Internet plugin: {internet.why()}")
        for reason in internet.skipped:
            print(f"  web: {reason}; using {internet.describe()}")
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
            return internet.why()
        skipped = f"; not usable: {'; '.join(internet.skipped)}" if internet.skipped else ""
        return f"ready: {internet.describe()}{skipped}"

    def describe(self):
        return " then ".join(b.name for b in self.backends_used)

    @staticmethod
    def backends():
        """The web backends, by the name config.json's "web" gives one: add a new one here."""
        return {"ollama": OllamaWeb, "duckduckgo": DuckDuckGoWeb}

    @staticmethod
    def check():
        """web, web_fallback, web_timeout and learn_missing in config.json are usable."""
        timeout, missing = Config.get("web_timeout"), Config.get("learn_missing")
        for key in ("web", "web_fallback"):
            if Config.get(key) not in ("", *Internet.backends()):
                raise SystemExit(f"{key} in {CONFIG_FILE} must be one of {', '.join(map(repr, Internet.backends()))} "
                                 f'or "" (none); it is {Config.get(key)!r}')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise SystemExit(f"web_timeout in {CONFIG_FILE} must be a number of seconds above 0; it is {timeout!r}")
        if isinstance(missing, bool) or not isinstance(missing, int) or missing < 1:
            raise SystemExit(f"learn_missing in {CONFIG_FILE} must be a whole number, 1 or more (the topics a run "
                             f"with --web may learn); it is {missing!r}")

    def available(self):
        return not self.gone

    def why(self, key=None):
        return self.gone or self.errors.get(key) or "no answer"

    def search(self, query, count):
        """[{title, url, content}] the web search finds (web addresses only), or None."""
        found = self.call(query, lambda backend: backend.search(query, count),
                          lambda r: isinstance(r, list) or "its search gave no list")
        if found is None:
            return None
        return [{"title": str(r.get("title") or ""), "url": str(r["url"]), "content": str(r.get("content") or "")}
                for r in found if isinstance(r, dict) and str(r.get("url", "")).startswith(("http://", "https://"))]

    def fetch(self, url):
        """{title, url, content} of a web page, its main text, or None: from the backends in turn."""
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
        for backend in list(self.backends_used):
            result, why = self.ask_backend(backend, action, usable)
            if not why:
                return result
            reasons.append(f"{backend.name}: {why}")
        self.errors[key] = "; ".join(reasons) or self.gone or "no answer"
        return None

    def ask_backend(self, backend, action, usable):
        """(result, "") from one backend, asked again once after a moment's trouble, or (None, why
        not). A network failure counts towards leaving the backend (lost)."""
        for attempt in (1, 2):
            try:
                result = action(backend)
            except WebError as exc:
                if exc.retry and attempt == 1:
                    time.sleep(self.RETRY_PAUSE)
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
        """A network failure: after GONE_AFTER in a row, that backend is not asked again; once no
        backend is left, the plugin is gone."""
        self.failures[backend.name] = self.failures.get(backend.name, 0) + 1
        if self.failures[backend.name] >= self.GONE_AFTER and backend in self.backends_used:
            self.backends_used.remove(backend)
            print(f"  web: not asking {backend.name} again ({first_line(why, 120)})")
            if not self.backends_used:
                self.gone = why


class WebBackend:
    """What a web backend gives the Internet plugin. A new one subclasses this, implements its
    three methods, and is added to Internet.backends() under the name config.json's "web" uses.
    search() and fetch() raise WebError when they cannot answer."""

    name = ""  # the name config.json gives it (set when the plugin takes it)

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
    MAX_BYTES = 2_000_000  # the most of one answer read

    def __init__(self, timeout):
        super().__init__(timeout)
        self.key = os.environ.get(self.KEY_ENV, "").strip()

    def unready(self):
        return "" if self.key else f"no Ollama API key: set {self.KEY_ENV} (a free key from ollama.com/settings/keys)"

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
                body = response.read(self.MAX_BYTES)
        except urllib.error.HTTPError as exc:
            hint = {401: f": check {self.KEY_ENV}", 403: f": check {self.KEY_ENV}", 429: ": its rate limit, try later"}
            raise WebError(f"ollama.com answered HTTP {exc.code}{hint.get(exc.code, '')}",
                           network=exc.code in (401, 403, 429) or (exc.code >= 500 and path != "/web_fetch"),
                           retry=exc.code == 429 or exc.code >= 500)  # a fetch's 5xx: that page, not the service
        except Exception as exc:  # no answer at all: timeout, refused, no network
            reason = first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in reason
            raise WebError(f"ollama.com cannot be reached ({reason})", network=True, retry=timed_out)
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
    USER_AGENT = "Mozilla/5.0 (compatible; Tinker; +https://github.com/codeforge)"
    MAX_BYTES = 2_000_000
    RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', re.S)
    SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    TAG_RE = re.compile(r"<[^>]+>")

    def unready(self):
        return ""

    def search(self, query, count):
        body = self.get(self.SEARCH, self.timeout, form={"q": query})[0]  # a GET is taken for a bot more often
        results = []
        for href, title, rest in self.RESULT_RE.findall(body):
            url = self.target(html.unescape(href))
            snippet = self.SNIPPET_RE.search(rest)
            if url and "duckduckgo.com/y.js" not in url:  # its ads link to itself
                results.append({"url": url, "title": self.plain(title),
                                "content": self.plain(snippet.group(1)) if snippet else ""})
        if not results and ("anomaly" in body.lower() or "result__a" not in body):
            raise WebError("DuckDuckGo answered with its bot check instead of results (too many searches; it "
                           "passes after a while)", retry=True)
        return results[:count]

    def fetch(self, url):
        return self.read_page(url, self.timeout)

    @classmethod
    def read_page(cls, url, timeout):
        """{"title", "url", "content"} of a page read from its own server: HTML as its main text,
        plain text and Markdown as they are."""
        body, kind = cls.get(url, timeout)
        if "html" in kind or body.lstrip()[:200].lower().startswith(("<!doctype html", "<html")):
            text = PageText.of(body)
            return {"title": text.title, "url": url, "content": text.content()}
        if kind.startswith("text/") or kind in ("", "application/json", "application/xml"):
            return {"title": "", "url": url, "content": body}
        raise WebError(f"{url} is not a text page ({kind})")

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
                           retry=exc.code == 429 or exc.code >= 500)
        except Exception as exc:  # no answer: timeout, refused, no network
            reason = first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)
            host = urllib.parse.urlsplit(url).netloc
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in reason
            raise WebError(f"{host} cannot be reached ({reason})", network=host in cls.SEARCH and not timed_out,
                           retry=timed_out)

    @staticmethod
    def target(href):
        """The address a result links to (DuckDuckGo wraps it in its own redirect)."""
        if href.startswith("//"):
            href = "https:" + href
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        return query["uddg"][0] if "uddg" in query else (href if href.startswith(("http://", "https://")) else "")

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
        if self.skipping or tag in self.SKIP or attributes.get("role") in self.SKIP_ROLES \
                or attributes.get("aria-hidden") == "true" or "hidden" in attributes:
            self.skipping += 1
            return
        if (tag in ("main", "article") or attributes.get("role") == "main") and self.main_depth is None:
            self.main_depth = self.depth
        if tag == "pre":
            self.pre += 1
        if tag in self.BLOCKS:
            self.add("\n" + ("#" * int(tag[1]) + " " if tag in ("h1", "h2", "h3", "h4", "h5", "h6") else
                             "- " if tag == "li" else ""))

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
        if self.pre or not data.strip():
            self.add(data if self.pre else (" " if data else ""))
        else:
            self.add((" " if data[0].isspace() else "") + " ".join(data.split()) + (" " if data[-1].isspace() else ""))

    def add(self, text):
        self.all.append(text)
        if self.main_depth is not None and self.main_depth > 0:
            self.main.append(text)


class WebError(Exception):
    """A web backend that could not answer; network: the backend cannot be reached at all (no
    network, its service is down, its key refused), so it is not asked again once that repeats;
    retry: it may answer if asked again in a moment (a timeout, its rate limit, a server error)."""

    def __init__(self, message, network=False, retry=False):
        super().__init__(message)
        self.network, self.retry = network, retry
