lazy import datetime
lazy import re
lazy import urllib.parse
from collections import namedtuple

lazy from .passages import Cleaner, Links, Page
lazy from .settings import MAX_MAP_LINKS, SITE_MAP_FILES, Config, Home, now_text, words


DocPages = namedtuple("DocPages", "pages version_key from_cache")
Root = namedtuple("Root", "page version_key")
Candidate = namedtuple("Candidate", "version_key address")
MapLink = namedtuple("MapLink", "address title")


class DocSite:
    def __init__(self, fetcher):
        self.fetcher = fetcher

    def pages(self, slug, docs, version, wanted, cap):
        for key in version_keys(version):
            kept = DocCache.get(slug, key)
            if kept is not None:
                return DocPages(kept, key, True)
        root, key = self.resolve(docs, version)
        if root is None:
            return DocPages([], "", False)
        pages = self.read(root, key, wanted, cap, version)
        if pages:
            DocCache.put(slug, key, root.final_url, pages)
        return DocPages(pages, key, False)

    def resolve(self, docs, version):
        for key, url in candidates(docs, version):
            root = self.fetcher.fetch_all([url], kind="probe")[0]
            if root.ok:
                return Root(root, key)
        return Root(None, "")

    def read(self, root, key, wanted, cap, version=""):
        base, key = base_of(root.final_url), matched(key, version)
        fetched = [root] + self.fetched(self.map(root, base), wanted, key, cap - 1, base, {root.final_url})
        deeper = [*under(Links.of(got.text, got.final_url), base) for got in fetched if got.ok]
        fetched += self.fetched(deeper, wanted, key, cap - len(fetched), base, {got.final_url for got in fetched})
        return [page for page in (self.page_of(got) for got in fetched) if page.blocks]

    def fetched(self, links, wanted, key, room, base, seen):
        picked = [url for url in pick(links, wanted, key, room + len(seen), base) if url not in seen][:max(room, 0)]
        return self.fetcher.fetch_all(picked) if picked else []

    def map(self, root, base):
        for name in SITE_MAP_FILES:
            got = self.fetcher.fetch_all([urllib.parse.urljoin(base, name)], kind="probe")[0]
            listed = under(map_links(name, got.text), base) if got.ok else []
            if listed:
                return listed[:MAX_MAP_LINKS]
        return under(Links.of(root.text, root.final_url), base)[:MAX_MAP_LINKS]

    @staticmethod
    def page_of(got):
        return Cleaner.page(got.final_url, got.text, got.kind, now_text()) if got.ok else Page(got.url, now_text(), [])


class DocCache:
    @staticmethod
    def get(slug, key):
        kept = Home.store().get("sources", f"{slug}@{key}")
        if not isinstance(kept, dict) or not kept.get("pages"):
            return None
        if key == "latest" and DocCache.stale(kept):
            return None
        return [Page.of_dict(page) for page in kept["pages"]]

    @staticmethod
    def stale(kept):
        return DocCache.age_days(kept.get("kept", "")) > float(Config.get("docs_latest_days"))

    @staticmethod
    def put(slug, key, root, pages):
        Home.store().put("sources", f"{slug}@{key}", {"root": root, "version": key, "kept": now_text(),
                                                      "pages": [page.as_dict() for page in pages]})

    @staticmethod
    def age_days(kept):
        try:
            return (datetime.datetime.now() - datetime.datetime.strptime(kept, "%Y-%m-%d %H:%M")).days
        except ValueError:
            return float("inf")


def version_keys(version):
    numbers = re.findall(r"\d+", version or "")
    return list(dict.fromkeys(".".join(numbers[:parts]) for parts in (len(numbers), 2, 1)
                              if 0 < parts <= len(numbers))) + ["latest"]


def matched(key, version):
    if key != "latest":
        return key
    numbers = re.findall(r"\d+", version or "")
    return ".".join(numbers[:2]) if len(numbers) >= 2 else key


def candidates(docs, version):
    versioned = docs.get("versioned") or ""
    exact = [Candidate(key, versioned.replace("{version}", key)) for key in version_keys(version)[:-1]
             if "{version}" in versioned]
    return exact + ([Candidate("latest", docs["root"])] if docs.get("root") else [])


def map_links(name, text):
    if name.endswith(".txt"):
        return [MapLink(url, title) for title, url in re.findall(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", text)]
    return [MapLink(url, "") for url in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)]


def under(links, base):
    site = urllib.parse.urlsplit(base).netloc
    same_site = [MapLink(url, title) for url, title in dict(links).items() if urllib.parse.urlsplit(url).netloc == site]
    below = [link for link in same_site if link.address.startswith(base)]
    return below or same_site


def pick(links, wanted, key, cap, base):
    ranked = [rank(links, set(words(slot_words)), key, base) for slot_words in wanted if slot_words.strip()]
    picked = []
    for turn in range(max((len(slot_links) for slot_links in ranked), default=0)):
        for ranking in ranked:
            if turn < len(ranking) and ranking[turn] not in picked and len(picked) < cap:
                picked.append(ranking[turn])
    return picked


def rank(links, slot_words, key, base):
    scored = []
    for index, (url, title) in enumerate(links):
        shared = len(slot_words & set(words(below(url, base) + " " + title)))
        if shared and not names_other_version(url, key, base):
            scored.append((-shared, -names_version(url, key, base), index, url))
    return [url for *_, url in sorted(scored)]


def names_version(url, key, base):
    return key != "latest" and re.search(rf"(?<![\d.]){re.escape(key)}(?![\d])", below(url, base)) is not None


def names_other_version(url, key, base):
    named = re.findall(r"(?<![\d.])\d+\.\d+(?:\.\d+)*(?![\d])", below(url, base))
    return any(not (key + ".").startswith(version + ".") and not (version + ".").startswith(key + ".")
               for version in named) if key != "latest" else False


def below(url, base):
    return url[len(base):] if url.startswith(base) else urllib.parse.urlsplit(url).path


def base_of(url):
    return url if url.endswith("/") else url.rsplit("/", 1)[0] + "/"
