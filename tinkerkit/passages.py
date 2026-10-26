import html.parser
import re
from collections import Counter
from dataclasses import dataclass

lazy from .settings import MAX_PASSAGE_CHARS, MENU_LINK_SHARE, MIN_USABLE_PAGE_CHARS, NEAR_DUPLICATE_OVERLAP
lazy from .text import host, split_text, words


@dataclass
class Block:
    kind: str
    text: str
    level: int = 0


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
        return Page(url, fetched, MainBlocks.of(text) if is_html(text, kind) else text_blocks(text))

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
        counts = Counter(
            line
            for page in pages
            for line in {site_line(page, block) for block in page.blocks if block.kind != "code"}
        )
        return [
            Page(
                page.url,
                page.fetched,
                [
                    block
                    for block in page.blocks
                    if block.kind == "code" or counts[site_line(page, block)] < 2
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
    SKIP = {
        "head",
        "script",
        "style",
        "noscript",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "svg",
        "button",
        "template",
        "iframe",
        "select",
    }
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCKS = HEADINGS | {
        "p",
        "div",
        "section",
        "article",
        "main",
        "li",
        "tr",
        "pre",
        "table",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "blockquote",
    }
    VOID = {
        "br",
        "hr",
        "img",
        "input",
        "meta",
        "link",
        "area",
        "base",
        "col",
        "embed",
        "source",
        "track",
        "wbr",
    }
    SKIP_ROLES = {"navigation", "banner", "contentinfo", "search", "menu", "menubar", "toolbar", "dialog"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipping, self.pre, self.links, self.depth = 0, 0, 0, 0
        self.kind, self.level, self.pieces, self.link_chars = "text", 0, [], 0
        self.all, self.main, self.main_opened_at = [], [], None
        self.title, self.in_title = "", False

    @classmethod
    def of(cls, markup):
        return cls.parsed(markup).content()

    @classmethod
    def parsed(cls, markup):
        parser = cls()
        read_what_it_can(parser, markup)
        parser.flush()
        return parser

    def content(self):
        main_chars = sum(len(block.text) for block in self.main if block.kind != "heading")
        return self.main if main_chars >= MIN_USABLE_PAGE_CHARS else self.all

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        self.depth += 1
        self.in_title = self.in_title or (tag == "title" and not self.title)
        attributes = dict(attrs)
        if self.skipping or self.not_content(tag, attributes):
            self.skipping += 1
            return
        if opens_main(tag, attributes) and self.main_opened_at is None:
            self.main_opened_at = self.depth
        if tag == "a":
            self.links += 1
        if tag in self.BLOCKS and not self.pre:
            self.flush()
            self.kind, self.level = block_kind(tag), int(tag[1]) if tag in self.HEADINGS else 0
        if tag == "pre":
            self.pre += 1

    def not_content(self, tag, attributes):
        return (
            tag in self.SKIP
            or attributes.get("role") in self.SKIP_ROLES
            or attributes.get("aria-hidden") == "true"
            or "hidden" in attributes
        )

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag == "title":
            self.in_title = False
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
        if self.in_title:
            self.title += data.strip()
        if self.skipping or self.in_title:
            return
        text = data if self.pre else collapsed(data) if data.strip() else (" " if data else "")
        self.pieces.append(text)
        if self.links and not self.pre:
            self.link_chars += len(data.strip())

    def flush(self):
        text = (
            "".join(self.pieces).strip("\n")
            if self.kind == "code"
            else " ".join("".join(self.pieces).split())
        )
        is_menu = self.kind in ("text", "item") and mostly_links(self.link_chars, text)
        if text.strip() and not is_menu:
            block = Block(self.kind, text, self.level)
            self.all.append(block)
            if self.main_opened_at is not None and self.main_opened_at > 0:
                self.main.append(block)
        self.kind, self.level, self.pieces, self.link_chars = "text", 0, [], 0


MAIN_CLOSED = -1


def block_kind(tag):
    if tag == "pre":
        return "code"
    if tag in MainBlocks.HEADINGS:
        return "heading"
    return "item" if tag == "li" else "text"


def opens_main(tag, attributes):
    return tag in ("main", "article") or attributes.get("role") == "main"


def collapsed(data):
    return (" " if data[0].isspace() else "") + " ".join(data.split()) + (" " if data[-1].isspace() else "")


def read_what_it_can(parser, markup):
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass


MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")


def is_html(text, kind):
    return "html" in kind or text.lstrip()[:200].lower().startswith(("<!doctype html", "<html"))


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


def normal(text):
    return " ".join(words(text))


def site_line(page, block):
    return host(page.url), normal(block.text)
