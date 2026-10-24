import html.parser
import re
from dataclasses import dataclass
lazy import urllib.parse

from .web import MAIN_CLOSED, PageText, read_what_it_can
lazy from .settings import (
    MAX_PASSAGE_CHARS,
    MENU_LINK_SHARE,
    MIN_USABLE_PAGE_CHARS,
    NEAR_DUPLICATE_OVERLAP,
    split_text,
    words,
)


@dataclass
class Block:
    kind: str
    text: str


@dataclass
class Page:
    url: str
    fetched: str
    blocks: list

    def chars(self):
        return sum(len(block.text) for block in self.blocks if block.kind != "heading")

    def usable(self):
        return self.chars() >= MIN_USABLE_PAGE_CHARS

    def as_dict(self):
        return {
            "url": self.url,
            "fetched": self.fetched,
            "blocks": [[block.kind, block.text] for block in self.blocks],
        }

    @staticmethod
    def of_dict(kept):
        return Page(kept["url"], kept["fetched"], [Block(kind, text) for kind, text in kept["blocks"]])


@dataclass
class Passage:
    url: str
    fetched: str
    heading: str
    text: str
    id: str = ""

    def searched(self):
        return f"{self.heading}\n{self.text}".strip()

    def listing(self):
        return f"[{self.id}] {self.url}" + (f" > {self.heading}" if self.heading else "") + f"\n{self.text}"


class Cleaner:
    TINY_PASSAGE_CHARS = 30

    @staticmethod
    def page(url, text, kind, fetched):
        is_html = "html" in kind or text.lstrip()[:200].lower().startswith(("<!doctype html", "<html"))
        return Page(url, fetched, MainBlocks.of(text) if is_html else text_blocks(text))

    def pool(self, pages, known=()):
        kept, seen = [], [shingles(passage.text) for passage in known]
        for passage in (*self.under_headings(page) for page in self.without_site_repeats(pages)):
            marks = shingles(passage.text)
            if len(passage.text) >= self.TINY_PASSAGE_CHARS and not any(
                repeats(marks, other) for other in seen
            ):
                kept.append(passage)
                seen.append(marks)
        for number, passage in enumerate(kept, len(known) + 1):
            passage.id = f"P{number}"
        return kept

    @staticmethod
    def without_site_repeats(pages):
        counts = {}
        for page in pages:
            for key in {
                (site(page.url), normal(block.text)) for block in page.blocks if block.kind != "code"
            }:
                counts[key] = counts.get(key, 0) + 1
        return [
            Page(
                page.url,
                page.fetched,
                [
                    block
                    for block in page.blocks
                    if block.kind == "code" or counts[(site(page.url), normal(block.text))] < 2
                ],
            )
            for page in pages
        ]

    @staticmethod
    def under_headings(page):
        passages, heading, held = [], "", []

        def close():
            if held:
                passages.append(Passage(page.url, page.fetched, heading, "\n\n".join(held)))
                held.clear()

        for block in page.blocks:
            if block.kind == "heading":
                close()
                heading = block.text
                continue
            for piece in [block.text] if block.kind == "code" else split_text(block.text, MAX_PASSAGE_CHARS):
                if held and len("\n\n".join(held)) + 2 + len(piece) > MAX_PASSAGE_CHARS:
                    close()
                held.append(piece)
        close()
        return passages


class MainBlocks(html.parser.HTMLParser):
    SKIP = PageText.SKIP | {"head"}
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCKS = PageText.BLOCKS - {"br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipping, self.pre, self.links, self.depth = 0, 0, 0, 0
        self.kind, self.pieces, self.link_chars = "text", [], 0
        self.all, self.main, self.main_opened_at = [], [], None

    @classmethod
    def of(cls, markup):
        parser = cls()
        read_what_it_can(parser, markup)
        parser.flush()
        main_chars = sum(len(block.text) for block in parser.main if block.kind != "heading")
        return parser.main if main_chars >= MIN_USABLE_PAGE_CHARS else parser.all

    def handle_starttag(self, tag, attrs):
        if tag in PageText.VOID:
            return
        self.depth += 1
        attributes = dict(attrs)
        if self.skipping or self.not_content(tag, attributes):
            self.skipping += 1
            return
        if PageText.opens_main(tag, attributes) and self.main_opened_at is None:
            self.main_opened_at = self.depth
        if tag == "a":
            self.links += 1
        if tag in self.BLOCKS and not self.pre:
            self.flush()
            self.kind = "code" if tag == "pre" else "heading" if tag in self.HEADINGS else "text"
        if tag == "pre":
            self.pre += 1

    def not_content(self, tag, attributes):
        return (
            tag in self.SKIP
            or attributes.get("role") in PageText.SKIP_ROLES
            or attributes.get("aria-hidden") == "true"
            or "hidden" in attributes
        )

    def handle_endtag(self, tag):
        if tag in PageText.VOID:
            return
        if self.skipping:
            self.skipping -= 1
        else:
            if tag == "pre" and self.pre:
                self.pre -= 1
            if tag == "a" and self.links:
                self.links -= 1
            if tag in self.BLOCKS and not self.pre:
                self.flush()
            if self.main_opened_at is not None and self.depth == self.main_opened_at:
                self.main_opened_at = MAIN_CLOSED
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.skipping:
            return
        text = data if self.pre else PageText.collapsed(data) if data.strip() else (" " if data else "")
        self.pieces.append(text)
        if self.links and not self.pre:
            self.link_chars += len(data.strip())

    def flush(self):
        text = (
            "".join(self.pieces).strip("\n")
            if self.kind == "code"
            else " ".join("".join(self.pieces).split())
        )
        if text.strip() and (self.kind != "text" or not mostly_links(self.link_chars, text)):
            self.all.append(Block(self.kind, text))
            if self.main_opened_at is not None and self.main_opened_at > 0:
                self.main.append(Block(self.kind, text))
        self.kind, self.pieces, self.link_chars = "text", [], 0


class Links(html.parser.HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.found, self.href, self.text = base, {}, None, []

    @classmethod
    def of(cls, markup, base):
        parser = cls(base)
        read_what_it_can(parser, markup)
        return list(parser.found.items())

    def handle_starttag(self, tag, attrs):
        if tag == "a" and dict(attrs).get("href"):
            self.href, self.text = dict(attrs)["href"], []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            url = urllib.parse.urldefrag(urllib.parse.urljoin(self.base, self.href))[0]
            if url.startswith(("http://", "https://")):
                self.found.setdefault(url, " ".join("".join(self.text).split()))
            self.href = None


MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")


def text_blocks(text):
    blocks, held, fence = [], [], None

    def close(kind="text"):
        paragraph = "\n".join(held).strip("\n") if kind == "code" else " ".join(" ".join(held).split())
        linked = sum(len(link) for link in MARKDOWN_LINK.findall(paragraph))
        if paragraph.strip() and (kind == "code" or not mostly_links(linked, paragraph)):
            blocks.append(Block(kind, paragraph))
        held.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            close("code" if fence else "text")
            fence = None if fence else line
        elif fence:
            held.append(line)
        elif re.prefixmatch(r"#{1,6} ", line.strip()):
            close()
            blocks.append(Block("heading", line.strip().lstrip("#").strip()))
        elif not line.strip():
            close()
        else:
            held.append(line)
    close("code" if fence else "text")
    return blocks


def mostly_links(link_chars, text):
    return link_chars >= MENU_LINK_SHARE * len(text)


def shingles(text):
    found = words(text)
    return {tuple(found[start : start + 3]) for start in range(len(found) - 2)} or {tuple(found)}


def repeats(a, b):
    return len(a & b) >= NEAR_DUPLICATE_OVERLAP * len(a | b) if a | b else True


def site(url):
    return urllib.parse.urlsplit(url).netloc.lower()


def normal(text):
    return " ".join(words(text))
