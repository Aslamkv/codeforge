"""A topic's documentation site, read with no model call: the root of the installed version's documentation
found (the full version, then major.minor, then major, then the unversioned root), its map read (llms.txt,
sitemap.xml, else the root page's own links), the pages each slot needs picked by the words of their address and
title (never one of another version), fetched, then the same from the links of the pages read, one level on;
cleaned; and kept per topic
and version (DocCache), so learning it again fetches nothing."""

lazy import datetime
lazy import re
lazy import urllib.parse

lazy from .passages import Cleaner, Links, Page
lazy from .settings import DOC_MAPS, MAX_MAP_LINKS, Config, Home, now_text, words


class DocSite:
    """Reads documentation sites through the plugin's fetcher. docs: {"root", "versioned"}, the site's root and
    its address for one version ({version} in it), as the plan gives them."""

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def pages(self, slug, docs, version, wanted, cap):
        """(the cleaned pages, the docs version they are for, whether they came from the cache): the documentation
        kept for this version when there is a copy, else read from the site and kept. wanted: the words of the
        pages to take, one string per slot. ([], "", False) when the site cannot be read."""
        for key in version_keys(version):
            kept = DocCache.get(slug, key)
            if kept is not None:
                return kept, key, True
        root, key = self.resolve(docs, version)
        if root is None:
            return [], "", False
        pages = self.read(root, key, wanted, cap, version)
        if pages:
            DocCache.put(slug, key, root.final_url, pages)
        return pages, key, False

    def resolve(self, docs, version):
        """(the root page of the first of the version's addresses that answers, its version key), or (None, "")."""
        for key, url in candidates(docs, version):
            root = self.fetcher.fetch_all([url], kind="probe")[0]
            if root.ok:
                return root, key
        return None, ""

    def read(self, root, key, wanted, cap, version=""):
        """The site's pages the slots want, at most cap, the root first, each one cleaned: picked from the site's
        map, then from the links of the pages read (an index's pages: its version's what's new behind a what's
        new index), one level on. The latest documentation is matched to the version installed (major.minor)."""
        base, key = base_of(root.final_url), matched(key, version)
        fetched = [root] + self.fetched(self.map(root, base), wanted, key, cap - 1, base, {root.final_url})
        deeper = [*under(Links.of(got.text, got.final_url), base) for got in fetched if got.ok]
        fetched += self.fetched(deeper, wanted, key, cap - len(fetched), base, {got.final_url for got in fetched})
        return [page for page in (self.page_of(got) for got in fetched) if page.blocks]

    def fetched(self, links, wanted, key, room, base, seen):
        """The pages picked from these links that were not fetched yet, at most room."""
        picked = [url for url in pick(links, wanted, key, room + len(seen), base) if url not in seen][:max(room, 0)]
        return self.fetcher.fetch_all(picked) if picked else []

    def map(self, root, base):
        """(address, title) of the site's pages under the root: from its llms.txt, else its sitemap.xml, else the
        root page's own links."""
        for name in DOC_MAPS:
            got = self.fetcher.fetch_all([urllib.parse.urljoin(base, name)], kind="probe")[0]
            listed = under(map_links(name, got.text), base) if got.ok else []
            if listed:
                return listed[:MAX_MAP_LINKS]
        return under(Links.of(root.text, root.final_url), base)[:MAX_MAP_LINKS]

    @staticmethod
    def page_of(got):
        return Cleaner.page(got.final_url, got.text, got.kind, now_text()) if got.ok else Page(got.url, now_text(), [])


class DocCache:
    """Documentation pages fetched and cleaned, kept in Tinker's "sources" as slug@version: learning again reads
    them from here, with no web. A copy of unversioned ("latest") documentation is read again after
    docs_latest_days; a version's own is kept until tinker clear docs."""

    @staticmethod
    def get(slug, key):
        """The pages kept for this topic and version, or None (none kept, or a stale copy of the latest)."""
        kept = Home.store().get("sources", f"{slug}@{key}")
        if not isinstance(kept, dict) or not kept.get("pages"):
            return None
        if key == "latest" and DocCache.stale(kept):
            return None
        return [Page.of_dict(page) for page in kept["pages"]]

    @staticmethod
    def stale(kept):
        """Whether pages kept are older than the docs_latest_days setting allows."""
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
    """The keys a version's documentation may be under, the most exact first: 3.15.1, 3.15, 3, then latest."""
    numbers = re.findall(r"\d+", version or "")
    return list(dict.fromkeys(".".join(numbers[:parts]) for parts in (len(numbers), 2, 1)
                              if 0 < parts <= len(numbers))) + ["latest"]


def matched(key, version):
    """The version pages are matched to: the documentation's own, or for the latest the one installed (3.15)."""
    if key != "latest":
        return key
    numbers = re.findall(r"\d+", version or "")
    return ".".join(numbers[:2]) if len(numbers) >= 2 else key


def candidates(docs, version):
    """(version key, address) to try, the most exact first; the root last, as "latest"."""
    versioned = docs.get("versioned") or ""
    exact = [(key, versioned.replace("{version}", key)) for key in version_keys(version)[:-1] if "{version}" in versioned]
    return exact + ([("latest", docs["root"])] if docs.get("root") else [])


def map_links(name, text):
    """(address, title) listed in a site map: llms.txt's Markdown links, or sitemap.xml's <loc> addresses."""
    if name.endswith(".txt"):
        return [(url, title) for title, url in re.findall(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", text)]
    return [(url, "") for url in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)]


def under(links, base):
    """The links on the base's site and under its path, each once (all of the site's when none is under it)."""
    site = urllib.parse.urlsplit(base).netloc
    same_site = [(url, title) for url, title in dict(links).items() if urllib.parse.urlsplit(url).netloc == site]
    below = [(url, title) for url, title in same_site if url.startswith(base)]
    return below or same_site


def pick(links, wanted, key, cap, base):
    """The addresses the slots want, taken in turn (each slot's best, then each one's second, ...), each once, at
    most cap: a page counts for a slot by the words its address (below the base) and title share with the slot's
    (wanted: one string per slot); one that names the version comes first."""
    ranked = [rank(links, set(words(slot_words)), key, base) for slot_words in wanted if slot_words.strip()]
    picked = []
    for turn in range(max((len(slot_links) for slot_links in ranked), default=0)):
        for ranking in ranked:
            if turn < len(ranking) and ranking[turn] not in picked and len(picked) < cap:
                picked.append(ranking[turn])
    return picked


def rank(links, slot_words, key, base):
    """A slot's addresses, the most words shared first; one that names another version is never taken."""
    scored = []
    for index, (url, title) in enumerate(links):
        shared = len(slot_words & set(words(below(url, base) + " " + title)))
        if shared and not names_other_version(url, key, base):
            scored.append((-shared, -names_version(url, key, base), index, url))
    return [url for *_, url in sorted(scored)]


def names_version(url, key, base):
    """Whether an address names this version below the base (3.15 in .../3.15/whatsnew/3.15.html)."""
    return key != "latest" and re.search(rf"(?<![\d.]){re.escape(key)}(?![\d])", below(url, base)) is not None


def names_other_version(url, key, base):
    """Whether an address names a version other than this one below the base (3.14 in whatsnew/3.14.html)."""
    named = re.findall(r"(?<![\d.])\d+\.\d+(?:\.\d+)*(?![\d])", below(url, base))
    return any(not (key + ".").startswith(version + ".") and not (version + ".").startswith(key + ".")
               for version in named) if key != "latest" else False


def below(url, base):
    """The part of an address below the base (its whole path when it is elsewhere)."""
    return url[len(base):] if url.startswith(base) else urllib.parse.urlsplit(url).path


def base_of(url):
    """The folder an address is in: the base its site's pages are under."""
    return url if url.endswith("/") else url.rsplit("/", 1)[0] + "/"
