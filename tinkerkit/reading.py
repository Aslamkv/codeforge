import collections
import dataclasses
import re
from collections import namedtuple
from dataclasses import dataclass
lazy import html.parser
lazy import io
lazy import posixpath
lazy import urllib.parse
lazy import xml.etree.ElementTree as ET
lazy import zipfile
lazy from pathlib import Path

from .messages import say
lazy from .knowledge import Embeddings, Library, Retriever, mentions, relevant_chunks
lazy from .principles import Inducer, Principles
lazy from .project import Tracked, ending
lazy from .prompts import Part, Prompts
lazy from .settings import (
    BOOK_PASSAGES_SHOWN,
    FACT_PHASES,
    MAX_PASSAGE_CHARS,
    READ_CHAPTER_TEXT_SHARE,
    READ_FACTS_PER_CHAPTER,
    READ_FOCUS_CHAPTERS,
    READ_FOCUS_CHUNKS,
    READ_INDEX_TERMS,
    READ_KEY_TERMS,
    READ_MAX_BYTES,
    READ_MIN_CHAPTERS,
    READ_WINDOW_CHARS,
    Config,
    Home,
    Numbered,
    confirm_deletion,
)
lazy from .text import field, fingerprint, first_line, now_text, split_text, words
lazy from .web import Internet, is_web_address

CHAPTER_ROLE_ORDER = ("core", "supporting", "other")
READINGS = Numbered("readings", "reading numbers")
READ_STEPS = ("survey", "chapters", "condense", "done")
CHAPTER_STATE_ICONS = {"read": "✅", "unread": "⏳", "raw": "📄"}
ABOUT_CHAPTER_TITLES = (
    "foreword",
    "preface",
    "introduction",
    "prologue",
    "about this book",
    "how to read",
    "how to use",
    "overview",
    "summary",
    "who this book is for",
)
UNREAD_MATTER_TITLES = (
    "cover",
    "title page",
    "copyright",
    "dedication",
    "contents",
    "acknowledg",
    "about the author",
    "also by",
    "praise for",
    "colophon",
    "bibliography",
    "references",
    "endnotes",
    "further reading",
    "list of figures",
    "list of tables",
)
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
CHAPTER_LINE_RE = re.compile(
    r"^[ \t]*(?:(?:chapter|part|book|section|appendix)[ \t]+[\w.-]+|preface|foreword|"
    r"introduction|prologue|epilogue|conclusion|index|contents)\b[^\n]{0,80}$",
    re.I | re.M,
)
NAMED_CHAPTER_RE = re.compile(r"\bchapters?\s+(\d+)", re.I)
INDEX_LINE_RE = re.compile(r"^[-\s]*([^\d\n,;:]{2,60}?)[\s,;:]+((?:\d+(?:\s*[-–]\s*\d+)?[\s,;]*)+)$", re.M)
HTML_RE = re.compile(r"\s*(<!doctype html|<html)", re.I)
PDF_REFUSED = (
    "This is a PDF. Tinker reads EPUB, Markdown and plain text: convert it first (Calibre's "
    "`ebook-convert book.pdf book.epub` keeps its chapters), then tinker read book.epub."
)
WEB_PAGE = (
    "That gave a web page, not something to read. A share link (Google Drive, Dropbox, iCloud) needs its "
    "download link: Dropbox's ends in ?dl=1."
)


IndexTerm = namedtuple("IndexTerm", "term references")
Located = namedtuple("Located", "path fragment")
NcxEntry = namedtuple("NcxEntry", "depth title src")
Added = namedtuple("Added", "number new")
SpineText = namedtuple("SpineText", "text starts")
ContentsEntry = namedtuple("ContentsEntry", "depth title where")
ParsedText = namedtuple("ParsedText", "text ids")


@dataclass
class Chapter:
    number: int
    title: str
    text: str
    kind: str
    state: str = "unread"
    principles: list = dataclasses.field(default_factory=list)

    def size(self):
        return len(words(self.text))


@dataclass
class Book:
    title: str
    author: str
    chapters: list

    @classmethod
    def of(cls, entry):
        return cls(entry["title"], entry["author"], [Chapter(**chapter) for chapter in entry["chapters"]])

    def of_kind(self, *kinds):
        return [chapter for chapter in self.chapters if chapter.kind in kinds]

    def in_state(self, *states):
        return [chapter for chapter in self.chapters if chapter.state in states]


def stored(source):
    say.step(f"Storing {first_line(source, 80)}…", "📥")
    book = opened(book_bytes(source), Path(urllib.parse.urlsplit(source).path).name or source)
    if not book.of_kind("body", "front"):
        raise SystemExit(f"Found nothing to read in {source}; nothing stored.")
    number, new = Readings.add(book, source)
    with say.section():
        if not new:
            say.info(
                f"Already on the reading list as #{number}: read #{number} goes on with it, "
                f'read #{number} "WHAT" reads it again with a focus',
                "📚",
            )
            return number, False
        say.done(
            f"#{number} {book.title}{f' by {book.author}' if book.author else ''}: "
            f"{len(book.chapters)} chapter(s), {len(book.in_state('raw'))} kept as text only"
        )
    return number, True


class Reader(Tracked):
    STEPS = READ_STEPS

    def __init__(self, model, tracker=None):
        self.model, self.tracker = model, tracker

    def read(self, number, focus=""):
        entry = Readings.get(number)
        book = Book.of(entry)
        survey = entry["survey"] or self.surveyed(entry, book)
        chapters = focused(book, focus) if focus else in_order(book.in_state("unread"), survey)
        if not chapters:
            say.info(
                f'Every chapter of #{number} is read: read #{number} "WHAT" reads it again with a focus', "📚"
            )
            return read_ending(Readings.get(number), 0, [])
        found = self.chapters_read(entry, survey, chapters, focus)
        return read_ending(Readings.get(number), len(found), chapters)

    def surveyed(self, entry, book):
        self.step("survey", "Reading its contents, foreword and index…", "🧭")
        with say.working_on(f"Getting to know {entry['title']} from its {len(book.chapters)} chapters"):
            survey = self.model.ask_until_usable(
                Prompts.READ_SURVEY_SYSTEM,
                survey_parts(book),
                "Read: the reading plan",
                lambda reply: read_survey(reply.json(), book),
                think=think(),
            ).needed(f"{self.model.name} could not make a reading plan; its text stays stored.")
        Readings.surveyed(entry, survey)
        core = [str(chapter.number) for chapter in book.chapters if order_of(survey, chapter) == "core"]
        with say.section():
            say.found(survey["summary"])
            say.info(f"Core chapters, read first: {', '.join(core) or 'none named'}", "🧭")
        self.outcome(f"{len(core)} core chapter(s) of {len(book.chapters)}, read first" if core else "")
        return survey

    def chapters_read(self, entry, survey, chapters, focus):
        self.step(
            "chapters",
            f"Reading {len(chapters)} chapter(s)" + (f" for: {first_line(focus, 60)}" if focus else "") + "…",
            "📚",
        )
        found = []
        say.tally(f"0/{len(chapters)} chapters")
        for done, chapter in enumerate(chapters, 1):
            with say.working_on(
                f"Reading chapter {chapter.number}, {chapter.title} ({done} of {len(chapters)})"
            ):
                deltas = self.chapter_read(entry, survey, chapter, focus)
            changed = Readings.learned(entry["number"], chapter.number, deltas)
            found = list(dict.fromkeys(found + [principle.id for principle in changed]))
            self.say_chapter(chapter, changed, f"{done}/{len(chapters)}", found)
        self.outcome(f"{len(chapters)} chapter(s), {learned(len(found))}")
        return found

    @staticmethod
    def say_chapter(chapter, changed, done, found):
        say.info(
            f"Chapter {chapter.number}, {chapter.title}: {learned(len(changed))}", CHAPTER_STATE_ICONS["read"]
        )
        if changed:
            say.found(first_line(changed[0].english(), 200))
        say.tally(f"{done} chapters · {learned(len(found)) if found else 'nothing learned yet'}")

    def chapter_read(self, entry, survey, chapter, focus):
        room = int(self.model.limit_chars(Prompts.INDUCE_SYSTEM) * READ_CHAPTER_TEXT_SHARE)
        look_for = survey["order"].get(str(chapter.number), {}).get("look_for") or "what it teaches"
        texts = [excerpt(chapter.text, room, focus)] if focus else split_text(chapter.text, room)
        inducer = Inducer(self.model, Principles(Embeddings.configured()))
        deltas = []
        for part, text in enumerate(texts, 1):
            label = f"Read: chapter {chapter.number}" + (f" part {part}" if len(texts) > 1 else "")
            passage = {"text": text, "source": f"#{entry['number']} chapter {chapter.number}"}
            deltas += inducer.induce(
                [passage], entry["topic"], think(), look_for=focus or look_for, label=label
            )
        return [read_phase(delta, survey["software"]) for delta in deltas][:READ_FACTS_PER_CHAPTER]


class Readings:
    @staticmethod
    def add(book, source):
        key = fingerprint("\n\n".join(chapter.text for chapter in book.chapters))[:16]
        known = Readings.with_fingerprint(key)
        if known is not None:
            return Added(known, False)
        number = READINGS.next_number()
        chapters = [dataclasses.asdict(Readings.unread(chapter)) for chapter in book.chapters]
        Readings.save(
            {
                "number": number,
                "title": book.title,
                "author": book.author,
                "source": source,
                "fingerprint": key,
                "added": now_text(),
                "topic": f"read-{number}",
                "survey": None,
                "chapters": chapters,
            }
        )
        return Added(number, True)

    @staticmethod
    def with_fingerprint(key):
        return next((number for number, entry in Readings.all().items() if entry["fingerprint"] == key), None)

    @staticmethod
    def unread(chapter):
        return dataclasses.replace(chapter, state="raw" if chapter.kind in ("index", "raw") else "unread")

    @staticmethod
    def get(number):
        return READINGS.get(number)

    @staticmethod
    def all():
        return {entry["number"]: entry for entry in READINGS.newest()}

    @staticmethod
    def save(entry):
        READINGS.put(entry)

    @staticmethod
    def surveyed(entry, survey):
        Readings.save(dict(entry, survey=survey))
        Library.save(
            entry["topic"],
            {
                "topic": entry["title"],
                "name": entry["title"],
                "kind": "reading",
                "reading": entry["number"],
                "author": entry["author"],
                "summary": survey["summary"],
                "software": survey["software"],
                "terms": survey["terms"],
                "source": entry["source"],
                "learned": now_text(),
                "facts": [],
                "rules": [],
            },
        )

    @staticmethod
    def learned(number, chapter, deltas):
        entry = Readings.get(number)
        changed = Principles(Embeddings.configured()).apply(deltas, "")
        for kept in entry["chapters"]:
            if kept["number"] == chapter:
                kept["principles"] = list(
                    dict.fromkeys(kept.get("principles", []) + [principle.id for principle in changed])
                )
                kept["state"] = "read"
        Readings.save(entry)
        return changed

    @staticmethod
    def forget(number):
        entry = Readings.get(number)
        READINGS.delete(number)
        Home.store().delete("learned", entry["topic"])
        Principles(None).forget_scope(entry["topic"])

    @staticmethod
    def about(text, items):
        readings = Readings.all()
        named = [
            number for number, entry in readings.items() if entry["title"] and mentions(text, entry["title"])
        ]
        topics = {entry["topic"]: number for number, entry in readings.items()}
        shown = [topics[item["group"]] for item in items if item.get("group") in topics]
        return list(dict.fromkeys(named + shown))

    @staticmethod
    def passages(numbers, query, count=BOOK_PASSAGES_SHOWN):
        found = Readings.chapter_passages(numbers)
        order = Retriever(None).by_words(query, [passage["text"] for passage in found])
        return [found[index] for index in order[:count]]

    @staticmethod
    def chapter_passages(numbers):
        found = []
        for number in numbers:
            entry = Readings.get(number) or {"chapters": []}
            for chapter in entry["chapters"]:
                source = f"{entry['title']}, chapter {chapter['number']}: {chapter['title']}"
                found += [
                    {"source": source, "text": piece}
                    for piece in split_text(chapter["text"], MAX_PASSAGE_CHARS)
                ]
        return found


def read_ending(entry, new, chapters):
    book, topic = Book.of(entry), Library.read(entry["topic"]) or {}
    readable = [chapter for chapter in book.chapters if chapter.state != "raw"]
    details = [
        ("Read now", f"{len(chapters)} chapter(s), {new} principle(s) changed"),
        ("Chapters read", f"{len(book.in_state('read'))} of {len(readable)}"),
        ("Kept", f"{len(Principles(None).kept(entry['topic']))} principle(s)"),
        (
            "Builds",
            "use it when a request needs it"
            if topic.get("software")
            else "never use it: it is not about building software",
        ),
    ]
    for label, value in details:
        say.done(f"{label}: {value}")
    number = entry["number"]
    return ending(
        "done",
        f"Read #{number} {entry['title']}",
        details,
        [
            ("Ask about it", f"tinker ask what does {entry['title']} say about …"),
            ("Look again", f'tinker read #{number} "what to look for"'),
            ("See it", f"tinker read show #{number}"),
        ],
    )


def show_list(page=1):
    readings, pages = Numbered.page(READINGS.newest(), page)
    if not readings:
        say.info("Nothing on the reading list: tinker read FILE | URL adds one (in a chat: attach it).", "📭")
        return
    with say.part("Reading list", "📚"):
        for entry in readings:
            say.item(f"#{entry['number']} {entry['title']}: {progress(entry)} · added {entry['added']}")
    if page < pages:
        say.info(f"Page {page} of {pages}: tinker read list {page + 1} for more.", "👉")
    say.info("tinker read show #N shows one; tinker read #N goes on with it.", "👉")


def show(number):
    entry = known_reading(number)
    with say.part(
        f"#{number} {entry['title']}" + (f" by {entry['author']}" if entry["author"] else ""), "📘"
    ):
        say.info(f"From {entry['source']}, added {entry['added']}: {progress(entry)}", "📥")
        if entry["survey"]:
            say.info(entry["survey"]["summary"], "📝")
        for chapter in Book.of(entry).chapters:
            note = {"raw": "text only", "unread": "not read yet"}.get(
                chapter.state, f"{len(chapter.principles)} principle(s)"
            )
            say.item(f"{CHAPTER_STATE_ICONS[chapter.state]} {chapter.number}. {chapter.title} ({note})")
    say.info(
        f'tinker read #{number} goes on · tinker read #{number} "WHAT" looks again · tinker clear read '
        f"#{number} deletes it",
        "👉",
    )


def forgotten(numbers, yes=False):
    numbers = [known_reading(number)["number"] for number in numbers] or list(Readings.all())
    if not numbers:
        say.info("Nothing to clear: the reading list is empty.")
        return
    confirm_deletion(
        "This deletes:",
        [
            f"#{number} {entry['title']}: its text and what was learned from it ({progress(entry)})"
            for number, entry in ((number, Readings.get(number)) for number in numbers)
        ],
        "tinker clear read " + " ".join(f"#{number}" for number in numbers),
        yes,
    )
    for number in numbers:
        Readings.forget(number)
    say.done(f"Cleared {', '.join(f'#{number}' for number in numbers)}.")


def known_reading(number):
    entry = Readings.get(number)
    if entry is None:
        raise SystemExit(f"No reading #{number}: tinker read list shows them.")
    return entry


def progress(entry):
    book = Book.of(entry)
    readable = [chapter for chapter in book.chapters if chapter.state != "raw"]
    return f"{len(book.in_state('read'))} of {len(readable)} chapters read · {len(Principles(None).kept(entry['topic']))} principle(s)"


def learned(count):
    if not count:
        return "nothing new"
    return f"{count} thing{'s' if count > 1 else ''} learned"


def think():
    return Config.get("learn_think")


def in_order(chapters, survey):
    return sorted(
        chapters, key=lambda chapter: (CHAPTER_ROLE_ORDER.index(order_of(survey, chapter)), chapter.number)
    )


def order_of(survey, chapter):
    return survey["order"].get(str(chapter.number), {}).get("read", "other")


def focused(book, focus):
    readable = [chapter for chapter in book.chapters if chapter.state != "raw"]
    named = {int(number) for number in NAMED_CHAPTER_RE.findall(focus)}
    ranked = Retriever(None).by_words(focus, [chapter.text for chapter in readable])
    chosen = [chapter for chapter in readable if chapter.number in named] + [
        readable[index] for index in ranked
    ]
    return sorted(
        {chapter.number: chapter for chapter in chosen[:READ_FOCUS_CHAPTERS]}.values(),
        key=lambda chapter: chapter.number,
    )


def book_bytes(source):
    if is_web_address(source):
        [page] = Internet().fetch([source], READ_MAX_BYTES)
        if page.why:
            raise SystemExit(f"Could not download {source}: {page.why}")
        return page.data
    path = Path(source).expanduser()
    if not path.is_file():
        raise SystemExit(f"No file at {source}: give a book's file or its web address.")
    if path.stat().st_size > READ_MAX_BYTES:
        raise SystemExit(f"{source} is larger than {READ_MAX_BYTES // 1_000_000} MB; nothing read.")
    return path.read_bytes()


def opened(data, name):
    title = re.sub(r"[-_]+", " ", Path(name).stem).strip() or "Untitled book"
    if data.startswith(b"%PDF"):
        raise SystemExit(PDF_REFUSED)
    if data.startswith(b"PK\x03\x04"):
        return Epub(data).book(title)
    if b"\x00" in data[:4096]:
        raise SystemExit(f"{name} is not a book Tinker can read: it reads EPUB, Markdown and plain text.")
    text = data.decode("utf-8-sig", errors="replace")
    if HTML_RE.match(text):
        raise SystemExit(WEB_PAGE)
    return Book(lone_title(text) or title, "", headed_chapters(text) or plain_chapters(text))


def lone_title(text):
    headings = [(len(match.group(1)), match.group(2)) for match in HEADING_RE.finditer(text)]
    top = [
        title
        for level, title in headings
        if level == min((heading_level for heading_level, _ in headings), default=0)
    ]
    return top[0].strip() if len(top) == 1 and len(headings) > 1 else ""


def headed_chapters(text):
    headings = [(len(match.group(1)), match.group(2), match.start()) for match in HEADING_RE.finditer(text)]
    if lone_title(text):
        headings = [heading for heading in headings if heading[0] != min(level for level, _, _ in headings)]
    depth = chapter_depth([level for level, _, _ in headings])
    return sliced(text, {start: title for level, title, start in headings if level <= depth})


def plain_chapters(text):
    marks = {match.start(): match.group(0).strip() for match in CHAPTER_LINE_RE.finditer(text)}
    if len(marks) >= 2:
        return sliced(text, marks)
    return numbered(
        [(f"Part {number}", piece) for number, piece in enumerate(split_text(text, READ_WINDOW_CHARS), 1)]
    )


def chapter_depth(depths):
    for depth in sorted(set(depths)):
        if sum(level <= depth for level in depths) >= READ_MIN_CHAPTERS:
            return depth
    return max(depths, default=1)


def sliced(text, marks):
    if not marks:
        return []
    starts = sorted(marks)
    pieces = [("Opening pages", text[: starts[0]])] if starts[0] > 0 else []
    pieces += [(marks[start], text[start:end]) for start, end in zip(starts, starts[1:] + [len(text)])]
    return numbered(pieces)


def numbered(pieces):
    pieces = [(" ".join(title.split()), tidy(text)) for title, text in pieces]
    pieces = [(title, text) for title, text in pieces if len(words(text)) > len(words(title)) + 3]
    return [Chapter(number, title, text, kind_of(title)) for number, (title, text) in enumerate(pieces, 1)]


def tidy(text):
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def kind_of(title):
    name = title.lower()
    if re.search(r"\bindex\b", name):
        return "index"
    if any(word in name for word in UNREAD_MATTER_TITLES):
        return "raw"
    return "front" if any(word in name for word in ABOUT_CHAPTER_TITLES) else "body"


def index_terms(book, limit=READ_INDEX_TERMS):
    counts = collections.Counter()
    for chapter in book.of_kind("index"):
        for match in INDEX_LINE_RE.finditer(chapter.text):
            counts[match.group(1).strip()] += len(re.findall(r"\d+", match.group(2)))
    return [IndexTerm(*counted) for counted in counts.most_common(limit)]


def survey_parts(book):
    contents = "\n".join(
        f"{chapter.number}. {chapter.title} ({chapter.kind}, {chapter.size()} words)"
        for chapter in book.chapters
    )
    about = "\n\n".join(f"## {chapter.title}\n{chapter.text}" for chapter in book.of_kind("front"))
    terms = ", ".join(f"{term} ({count})" for term, count in index_terms(book))
    return [
        Part("The book", book.title + (f" by {book.author}" if book.author else ""), True),
        Part("Its contents", contents, True, exact=True),
        Part("What it says about itself", about or "(no foreword, preface or introduction)", True),
        Part("The terms its index refers to most (how often)", terms),
    ]


def read_survey(data, book):
    summary = field(data, "summary")
    if not summary:
        raise ValueError("no summary")
    numbers, order = {chapter.number for chapter in book.chapters}, {}
    for item in data.get("chapters") or []:
        if isinstance(item, dict) and type(item.get("chapter")) is int and item["chapter"] in numbers:
            order[str(item["chapter"])] = {
                "read": item.get("read") if item.get("read") in CHAPTER_ROLE_ORDER else "other",
                "look_for": first_line(str(item.get("look_for") or ""), 200),
            }
    terms = [first_line(str(term), 60) for term in data.get("terms") or [] if str(term).strip()][
        :READ_KEY_TERMS
    ]
    return {"summary": summary, "software": data.get("software") is True, "terms": terms, "order": order}


def excerpt(text, room, wanted):
    if len(text) <= room:
        return text
    return "\n\n[…]\n\n".join(relevant_chunks(text, room // READ_FOCUS_CHUNKS, wanted, READ_FOCUS_CHUNKS))


def read_phase(delta, software):
    if delta["kind"] != "add":
        return delta
    return dict(delta, phase=delta["phase"] if software and delta["phase"] in FACT_PHASES else "book")


class Epub:
    def __init__(self, data):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            self.opf_path = self.xml("META-INF/container.xml").find(".//{*}rootfile").get("full-path")
            self.opf = self.xml(self.opf_path)
        except (zipfile.BadZipFile, KeyError, AttributeError, TypeError, ET.ParseError) as exc:
            raise SystemExit(f"This is not an EPUB Tinker can read ({first_line(str(exc), 120)}).") from None
        self.manifest = {item.get("id"): item for item in self.opf.iterfind(".//{*}manifest/{*}item")}

    def book(self, title):
        text, starts = self.spine_text()
        chapters = (
            contents_chapters(text, starts, self.contents()) or headed_chapters(text) or plain_chapters(text)
        )
        return Book(self.metadata("title") or title, self.metadata("creator"), chapters)

    def xml(self, path):
        return ET.fromstring(self.zip.read(path))

    def text_of(self, path):
        return self.zip.read(path).decode("utf-8", errors="replace")

    def metadata(self, name):
        found = self.opf.find(f".//{{*}}metadata/{{*}}{name}")
        return " ".join((found.text or "").split()) if found is not None else ""

    def spine_text(self):
        parts, starts, size = [], {}, 0
        for ref in self.opf.iterfind(".//{*}spine/{*}itemref"):
            item = self.manifest.get(ref.get("idref"))
            path = located(item.get("href"), self.opf_path)[0] if item is not None else ""
            if path not in self.zip.namelist():
                continue
            text, ids = XhtmlText.of(self.text_of(path))
            starts[(path, "")] = size
            starts.update({(path, found): size + at for found, at in ids.items()})
            parts.append(text + "\n\n")
            size += len(text) + 2
        return SpineText("".join(parts), starts)

    def contents(self):
        nav = next(
            (item for item in self.manifest.values() if "nav" in (item.get("properties") or "").split()), None
        )
        if nav is not None:
            document = located(nav.get("href"), self.opf_path)[0]
            entries = NavEntries.of(self.text_of(document))
        else:
            ncx = next(
                (
                    item
                    for item in self.manifest.values()
                    if item.get("media-type") == "application/x-dtbncx+xml"
                ),
                None,
            )
            if ncx is None:
                return []
            document = located(ncx.get("href"), self.opf_path)[0]
            entries = list(ncx_entries(self.xml(document).find(".//{*}navMap")))
        return [ContentsEntry(depth, title, located(href, document)) for depth, title, href in entries]


def located(href, document):
    path, _, fragment = urllib.parse.unquote(href or "").partition("#")
    return Located(
        posixpath.normpath(posixpath.join(posixpath.dirname(document), path)) if path else document, fragment
    )


def contents_chapters(text, starts, contents):
    depth = chapter_depth([entry_depth for entry_depth, _, _ in contents])
    marks = {}
    for entry_depth, title, (path, fragment) in contents:
        start = starts.get((path, fragment), starts.get((path, "")))
        if entry_depth <= depth and start is not None:
            marks.setdefault(start, title)
    return sliced(text, marks)


def ncx_entries(parent, depth=1):
    if parent is None:
        return
    for point in parent.findall("{*}navPoint"):
        label, content = point.find("{*}navLabel/{*}text"), point.find("{*}content")
        if label is not None and content is not None:
            yield NcxEntry(depth, " ".join((label.text or "").split()), content.get("src"))
        yield from ncx_entries(point, depth + 1)


class XhtmlText(html.parser.HTMLParser):
    BLOCKS = {
        "p",
        "div",
        "section",
        "article",
        "aside",
        "blockquote",
        "pre",
        "table",
        "tr",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
        "hr",
        "header",
        "footer",
        "body",
    }
    HEADINGS = {f"h{level}": level for level in range(1, 7)}
    HIDDEN = {"head", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.size, self.ids, self.hidden = [], 0, {}, 0

    @classmethod
    def of(cls, markup):
        parser = cls()
        parser.feed(markup)
        parser.close()
        return ParsedText("".join(parser.parts), parser.ids)

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
            return
        found = dict(attrs).get("id")
        if found:
            self.ids.setdefault(found, self.size)
        if tag in self.HEADINGS:
            self.add("\n\n" + "#" * self.HEADINGS[tag] + " ")
        elif tag in self.BLOCKS:
            self.add("\n\n")
        elif tag == "li":
            self.add("\n- ")
        elif tag == "br":
            self.add("\n")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.HEADINGS or tag in self.BLOCKS:
            self.add("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.add(re.sub(r"\s+", " ", data))

    def add(self, text):
        self.parts.append(text)
        self.size += len(text)


class NavEntries(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.navs, self.depth, self.link, self.words, self.inside = [], 0, None, [], False

    @classmethod
    def of(cls, markup):
        parser = cls()
        parser.feed(markup)
        parser.close()
        tocs = [entries for toc, entries in parser.navs if toc and entries] or [
            nav_entries for _, nav_entries in parser.navs if nav_entries
        ]
        return tocs[0] if tocs else []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            self.navs.append(("toc" in f"{attrs.get('epub:type', '')} {attrs.get('role', '')}", []))
            self.inside, self.depth = True, 0
        elif self.inside and tag == "ol":
            self.depth += 1
        elif self.inside and tag == "a":
            self.link, self.words = attrs.get("href") or "", []

    def handle_endtag(self, tag):
        if tag == "nav":
            self.inside = False
        elif self.inside and tag == "ol":
            self.depth -= 1
        elif self.inside and tag == "a" and self.link is not None:
            title = " ".join("".join(self.words).split())
            if title and self.link:
                self.navs[-1][1].append((max(self.depth, 1), title, self.link))
            self.link = None

    def handle_data(self, data):
        if self.link is not None:
            self.words.append(data)
