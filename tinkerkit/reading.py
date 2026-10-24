"""Reading what the person gives (tinker read SOURCE): a book, an article, a document. Its whole text is stored first,
numbered on the reading list (#1, #2 ...), so it is never sent twice. Then every chapter is read, the core ones first
(its contents, foreword and index tell which), each building on what was learned before, and saved as it is read:
what it teaches is a topic of the library (kind: reading), so asking and teaching reach it, and a source about
building software guides the builds that need it. The text stays: an ask or a teach finds what the source itself
says, and read #N "WHAT" reads it again with a focus. EPUB, Markdown and plain text are read; a PDF is refused."""

import collections
import dataclasses
lazy import html.parser
lazy import io
lazy import posixpath
import re
lazy import urllib.parse
lazy import xml.etree.ElementTree as ET
lazy import zipfile
from dataclasses import dataclass
lazy from pathlib import Path

from .messages import say
lazy from .knowledge import Learner, Library, Retriever, mentions
lazy from .prompts import Part, Prompts
lazy from .settings import (
    Config, FACT_CHARS, FACT_PHASES, Home, PASSAGE_CHARS, READ_CHAPTERS, READ_CHUNKS, READ_FOCUS_CHAPTERS,
    READ_INDEX_TERMS, READ_MAX_BYTES, READ_PASSAGES, READ_POINTS, READ_TERMS, READ_TEXT_SHARE, READ_WINDOW_CHARS,
    confirmed, fingerprint, first_line, now_text, split_text, words,
)
lazy from .web import Internet, is_web_address

ORDER = ("core", "supporting", "other")  # how the survey orders the chapters: what the source is about first
STATES = {"read": "✅", "unread": "⏳", "raw": "📄"}  # a chapter's state, as a reading is shown
FRONT = ("foreword", "preface", "introduction", "prologue", "about this book", "how to read", "how to use",
         "overview", "summary", "who this book is for")  # a chapter that says what the source is about
RAW = ("cover", "title page", "copyright", "dedication", "contents", "acknowledg", "about the author", "also by",
       "praise for", "colophon", "bibliography", "references", "endnotes", "further reading", "list of figures",
       "list of tables")  # front or back matter: kept as text, never read
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
CHAPTER_LINE_RE = re.compile(r"^[ \t]*(?:(?:chapter|part|book|section|appendix)[ \t]+[\w.-]+|preface|foreword|"
                             r"introduction|prologue|epilogue|conclusion|index|contents)\b[^\n]{0,80}$", re.I | re.M)
NAMED_CHAPTER_RE = re.compile(r"\bchapters?\s+(\d+)", re.I)
INDEX_LINE_RE = re.compile(r"^[-\s]*([^\d\n,;:]{2,60}?)[\s,;:]+((?:\d+(?:\s*[-–]\s*\d+)?[\s,;]*)+)$", re.M)
HTML_RE = re.compile(r"\s*(<!doctype html|<html)", re.I)
PDF_REFUSED = ("This is a PDF. Tinker reads EPUB, Markdown and plain text: convert it first (Calibre's "
               "`ebook-convert book.pdf book.epub` keeps its chapters), then tinker read book.epub.")
WEB_PAGE = ("That gave a web page, not something to read. A share link (Google Drive, Dropbox, iCloud) needs its "
            "download link: Dropbox's ends in ?dl=1.")


@dataclass
class Chapter:
    """One chapter: its number, title and text; its kind (front: it says what the source is about, body, index, or
    raw: front or back matter); its state (unread, read, or raw: kept as text, never read)."""
    number: int
    title: str
    text: str
    kind: str
    state: str = "unread"

    def size(self):
        return len(words(self.text))


@dataclass
class Book:
    """What is read: a book, an article or a document, as its chapters."""
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
    """read SOURCE: the source's whole text stored on the reading list, before anything is read (no model); (its
    number, whether it is new). The same text sent again is the one stored before."""
    say.step(f"Storing {first_line(source, 80)}…", "📥")
    book = opened(book_bytes(source), Path(urllib.parse.urlsplit(source).path).name or source)
    if not book.of_kind("body", "front"):
        raise SystemExit(f"Found nothing to read in {source}; nothing stored.")
    number, new = Readings.add(book, source)
    with say.section():
        if not new:
            say.info(f"Already on the reading list as #{number}: read #{number} goes on with it, "
                     f'read #{number} "WHAT" reads it again with a focus', "📚")
            return number, False
        say.done(f"#{number} {book.title}{f' by {book.author}' if book.author else ''}: "
                 f"{len(book.chapters)} chapter(s), {len(book.in_state('raw'))} kept as text only")
    return number, True


class Reader:
    """read #N [WHAT]: reading #N learned from. SURVEY (one call, once): what it is about, whether it teaches how to
    build software, its key terms, and the order to read its chapters in (core, supporting, other), from its
    contents, what it says about itself and its index. CHAPTERS (a call per part of each chapter): every unread
    chapter, in that order, each given what was learned before, and saved as soon as it is read, so a reading
    stopped goes on where it was. With WHAT: the chapters most about it (and those it names) read again with it in
    mind. CONDENSE: once every chapter is read, a source about building software has its facts made into rules."""

    def __init__(self, model, tracker=None):
        self.model, self.tracker = model, tracker

    def read(self, number, focus=""):
        """Reading #number learned from (its unread chapters, or with a focus the chapters most about it); the
        ending."""
        entry = Readings.get(number)
        book = Book.of(entry)
        survey = entry["survey"] or self.surveyed(entry, book)
        chapters = focused(book, focus) if focus else in_order(book.in_state("unread"), survey)
        if not chapters:
            say.info(f"Every chapter of #{number} is read: read #{number} \"WHAT\" reads it again with a focus", "📚")
            return ending(Readings.get(number), 0, [])
        found = self.chapters_read(entry, book, survey, chapters, focus)
        self.condensed(Readings.get(number), survey, found)
        return ending(Readings.get(number), len(found), chapters)

    def step(self, phase, said, icon):
        say.step(said, icon)
        if self.tracker is not None and self.tracker.state["step"] != phase:
            self.tracker.phase(phase)

    def surveyed(self, entry, book):
        """SURVEY: {summary, software, terms, order: {chapter number: {read, look_for}}}, kept with the reading (as
        JSON keeps it: each number as text); its
        topic made, with nothing learned yet."""
        self.step("survey", "Reading its contents, foreword and index…", "🧭")
        with say.working_on(f"Getting to know {entry['title']} from its {len(book.chapters)} chapters"):
            survey = self.model.ask_until_usable(Prompts.READ_SURVEY_SYSTEM, survey_parts(book),
                                                 "Read: the reading plan",
                                                 lambda reply: read_survey(reply.json(), book), think=think())
        if survey is None:
            raise SystemExit(f"{self.model.name} could not make a reading plan; its text stays stored.")
        Readings.surveyed(entry, survey)
        core = [str(c.number) for c in book.chapters if order_of(survey, c) == "core"]
        with say.section():
            say.found(survey["summary"])
            say.info(f"Core chapters, read first: {', '.join(core) or 'none named'}", "🧭")
        self.outcome(f"{len(core)} core chapter(s) of {len(book.chapters)}, read first" if core else "")
        return survey

    def chapters_read(self, entry, book, survey, chapters, focus):
        """CHAPTERS: each chapter read and saved at once; the facts found."""
        self.step("chapters", f"Reading {len(chapters)} chapter(s)" + (f" for: {first_line(focus, 60)}" if focus
                                                                       else "") + "…", "📚")
        found = []
        say.tally(f"0/{len(chapters)} chapters")
        for done, chapter in enumerate(chapters, 1):
            with say.working_on(f"Reading chapter {chapter.number}, {chapter.title} ({done} of {len(chapters)})"):
                facts = self.chapter_read(entry, book, survey, chapter, focus)
            found += Readings.learned(entry["number"], chapter.number, facts)
            say.info(f"Chapter {chapter.number}, {chapter.title}: {learned(len(facts))}", STATES["read"])
            if facts:
                say.found(first_line(facts[0]["text"], 200))
            say.tally(f"{done}/{len(chapters)} chapters · {learned(len(found)) if found else 'nothing learned yet'}")
        self.outcome(f"{len(chapters)} chapter(s), {learned(len(found))}")
        return found

    def outcome(self, text):
        if self.tracker is not None:
            self.tracker.outcome(text)

    def chapter_read(self, entry, book, survey, chapter, focus):
        """What one chapter teaches: each part of it in turn (with a focus, its parts most about it), given the
        source, what was learned before (the latest kept when the prompt is full) and what to look for."""
        room = int(self.model.limit_chars(Prompts.READ_CHAPTER_SYSTEM) * READ_TEXT_SHARE)
        look_for = survey["order"].get(str(chapter.number), {}).get("look_for") or "what it teaches"
        texts = [excerpt(chapter.text, room, focus)] if focus else split_text(chapter.text, room)
        found = []
        for part, text in enumerate(texts, 1):
            known = Readings.facts(entry) + [fact["text"] for fact in found]
            label = f"Read: chapter {chapter.number}" + (f" part {part}" if len(texts) > 1 else "")
            points = self.model.ask_until_usable(Prompts.READ_CHAPTER_SYSTEM,
                                                 chapter_parts(book, survey, chapter, text, look_for, known, focus),
                                                 label, lambda reply: read_points(reply.json(), survey["software"]),
                                                 attempts=2, think=think())
            found += [dict(point, chapter=chapter.number, slot=f"chapter {chapter.number}") for point in points or []]
        return found

    def condensed(self, entry, survey, found):
        """CONDENSE: once every chapter is read, a source about building software has the facts that change how
        software is built made into its rules (made again when something new was found); none for any other."""
        topic = Library.read(entry["topic"]) or {}
        building = [fact for fact in topic.get("facts", []) if fact["phase"] in FACT_PHASES]
        if not (survey["software"] and building) or Book.of(entry).in_state("unread"):
            return
        if topic.get("rules") and not found:
            return
        learner = Learner(self.model, None, None)
        learner.tracker = self.tracker
        rules = learner.condense({"name": entry["title"], "kind": "reading"}, building, think=think())
        Readings.ruled(entry, rules)


class Readings:
    """The reading list, in Tinker's "readings" by number: each source's title, author, where it came from, the
    fingerprint of its text, when it was added, its chapters (text, kind and state), its survey once made, and its
    topic: the slug of what was learned from it, in the library."""

    @staticmethod
    def add(book, source):
        """(its number on the reading list, whether it is new): a text added before is that one."""
        key = fingerprint("\n\n".join(chapter.text for chapter in book.chapters))[:16]
        known = next((n for n, entry in Readings.all().items() if entry["fingerprint"] == key), None)
        if known is not None:
            return known, False
        number = Home.store().update("reading numbers", "last", lambda last: (last or 0) + 1)
        chapters = [dataclasses.asdict(dataclasses.replace(c, state="raw" if c.kind in ("index", "raw") else "unread"))
                    for c in book.chapters]
        Readings.save({"number": number, "title": book.title, "author": book.author, "source": source,
                       "fingerprint": key, "added": now_text(), "topic": f"read-{number}", "survey": None,
                       "chapters": chapters})
        return number, True

    @staticmethod
    def get(number):
        return Home.store().get("readings", str(number))

    @staticmethod
    def all():
        """{number: entry} of the reading list, the newest first."""
        found = {int(key): entry for key, entry in Home.store().items("readings").items()}
        return dict(sorted(found.items(), reverse=True))

    @staticmethod
    def save(entry):
        Home.store().put("readings", str(entry["number"]), entry)

    @staticmethod
    def surveyed(entry, survey):
        """The survey kept with the reading, and its topic made (nothing learned yet)."""
        Readings.save(dict(entry, survey=survey))
        Library.save(entry["topic"], {"topic": entry["title"], "name": entry["title"], "kind": "reading",
                                      "reading": entry["number"], "author": entry["author"],
                                      "summary": survey["summary"], "software": survey["software"],
                                      "terms": survey["terms"], "source": entry["source"], "learned": now_text(),
                                      "facts": [], "rules": []})

    @staticmethod
    def learned(number, chapter, facts):
        """What one chapter taught, saved at once: its new facts (one already kept is not kept again) joined to the
        topic, and the chapter marked read; the new facts."""
        entry = Readings.get(number)
        known = set(Readings.facts(entry))
        new = [fact for fact in {fact["text"]: fact for fact in facts}.values() if fact["text"] not in known]
        Library.save(entry["topic"], dict(Library.read(entry["topic"]), learned=now_text(),
                                          facts=Library.read(entry["topic"])["facts"] + new))
        for kept in entry["chapters"]:
            if kept["number"] == chapter:
                kept["state"] = "read"
        Readings.save(entry)
        return new

    @staticmethod
    def ruled(entry, rules):
        if rules:
            Library.save(entry["topic"], dict(Library.read(entry["topic"]), rules=rules))

    @staticmethod
    def facts(entry):
        """The texts of the facts learned from a reading so far."""
        return [fact["text"] for fact in (Library.read(entry["topic"]) or {}).get("facts", [])]

    @staticmethod
    def forget(number):
        """A reading deleted: its text, and what was learned from it."""
        entry = Readings.get(number)
        Home.store().delete("readings", str(number))
        Home.store().delete("learned", entry["topic"])

    @staticmethod
    def about(text, items):
        """The numbers of the readings a question or a teaching is about: those it names by title, then those whose
        items it was shown."""
        readings = Readings.all()
        named = [n for n, entry in readings.items() if entry["title"] and mentions(text, entry["title"])]
        topics = {entry["topic"]: n for n, entry in readings.items()}
        shown = [topics[item["group"]] for item in items if item.get("group") in topics]
        return list(dict.fromkeys(named + shown))

    @staticmethod
    def passages(numbers, query, count=READ_PASSAGES):
        """[{source, text}] of the readings' passages most about the query (by its words), the best first, each with
        its title and chapter."""
        found = []
        for number in numbers:
            entry = Readings.get(number) or {"chapters": []}
            for chapter in entry["chapters"]:
                source = f"{entry['title']}, chapter {chapter['number']}: {chapter['title']}"
                found += [{"source": source, "text": piece} for piece in split_text(chapter["text"], PASSAGE_CHARS)]
        order = Retriever(None).by_words(query, [passage["text"] for passage in found])
        return [found[index] for index in order[:count]]


def ending(entry, new, chapters):
    """How a reading pass ended, for its tracker: what it read, what is kept, and what to do next."""
    book, topic = Book.of(entry), Library.read(entry["topic"]) or {}
    readable = [c for c in book.chapters if c.state != "raw"]
    details = [("Read now", f"{len(chapters)} chapter(s), {new} new fact(s)"),
               ("Chapters read", f"{len(book.in_state('read'))} of {len(readable)}"),
               ("Kept", f"{len(topic.get('facts', []))} fact(s), {len(topic.get('rules', []))} rule(s)"),
               ("Builds", "use it when a request needs it" if topic.get("software")
                else "never use it: it is not about building software")]
    for label, value in details:
        say.done(f"{label}: {value}")
    number = entry["number"]
    return {"status": "done", "headline": f"Read #{number} {entry['title']}", "details": details,
            "next": [("Ask about it", f"tinker ask what does {entry['title']} say about …"),
                     ("Look again", f'tinker read #{number} "what to look for"'),
                     ("See it", f"tinker read show #{number}")]}


def show_list():
    """read list: each reading, the newest first: how much of it is read, and what was learned."""
    readings = Readings.all()
    if not readings:
        say.info("Nothing on the reading list: tinker read FILE | URL adds one (in a chat: attach it).", "📭")
        return
    with say.part("Reading list", "📚"):
        for number, entry in readings.items():
            say.item(f"#{number} {entry['title']}: {progress(entry)} · added {entry['added']}")
    say.info("tinker read show #N shows one; tinker read #N goes on with it.", "👉")


def show(number):
    """read show #N: one reading: where it came from, what it is about, each chapter with its state and facts."""
    entry = known_reading(number)
    topic = Library.read(entry["topic"]) or {}
    facts = collections.Counter(fact.get("chapter") for fact in topic.get("facts", []))
    with say.part(f"#{number} {entry['title']}" + (f" by {entry['author']}" if entry["author"] else ""), "📘"):
        say.info(f"From {entry['source']}, added {entry['added']}: {progress(entry)}", "📥")
        if entry["survey"]:
            say.info(entry["survey"]["summary"], "📝")
        for chapter in Book.of(entry).chapters:
            note = {"raw": "text only", "unread": "not read yet"}.get(chapter.state, f"{facts[chapter.number]} fact(s)")
            say.item(f"{STATES[chapter.state]} {chapter.number}. {chapter.title} ({note})")
    say.info(f'tinker read #{number} goes on · tinker read #{number} "WHAT" looks again · tinker clear read '
             f"#{number} deletes it", "👉")


def forgotten(numbers, yes=False):
    """clear read [#N...]: those readings (every one, with none named) deleted, their text and what was learned from
    them, once the person agrees."""
    numbers = [known_reading(n)["number"] for n in numbers] or list(Readings.all())
    if not numbers:
        say.info("Nothing to clear: the reading list is empty.")
        return
    say.warn("This deletes:")
    with say.section():
        for number in numbers:
            entry = Readings.get(number)
            say.item(f"#{number} {entry['title']}: its text and what was learned from it ({progress(entry)})")
    if not confirmed_delete(numbers, yes):
        say.info("Nothing deleted.")
        return
    for number in numbers:
        Readings.forget(number)
    say.done(f"Cleared {', '.join(f'#{n}' for n in numbers)}.")


def confirmed_delete(numbers, yes):
    return confirmed("Delete them?", "tinker clear read " + " ".join(f"#{n}" for n in numbers), yes)


def known_reading(number):
    entry = Readings.get(number)
    if entry is None:
        raise SystemExit(f"No reading #{number}: tinker read list shows them.")
    return entry


def progress(entry):
    """How much of a reading is read, and what was learned: "3 of 5 chapters read · 12 fact(s)"."""
    book = Book.of(entry)
    readable = [c for c in book.chapters if c.state != "raw"]
    return f"{len(book.in_state('read'))} of {len(readable)} chapters read · {len(Readings.facts(entry))} fact(s)"


def learned(count):
    """What a chapter taught, as the person reads it: "nothing new", "1 thing learned", "4 things learned"."""
    if not count:
        return "nothing new"
    return f"{count} thing{'s' if count > 1 else ''} learned"


def think():
    """The think setting of reading's calls: learning's."""
    return Config.get("learn_think")


def chapter_parts(book, survey, chapter, text, look_for, known, focus):
    """A chapter's prompt: the source, what was learned before, what the person asks to look for, the chapter."""
    return [Part("The source", f"{book.title}: {survey['summary']}\nIts key terms: "
                               f"{', '.join(survey['terms']) or 'none named'}", True),
            Part("What you learned from it before", "\n".join(f"- {text}" for text in known), keep="tail"),
            Part("What the person asks you to look for", focus, bool(focus)),
            Part(f"Chapter {chapter.number}: {chapter.title}", f"Look for: {look_for}", True),
            Part("Its text", text, True)]


def in_order(chapters, survey):
    """Chapters in the survey's order: the core ones, then the supporting ones, then the others, each in the
    source's order."""
    return sorted(chapters, key=lambda c: (ORDER.index(order_of(survey, c)), c.number))


def order_of(survey, chapter):
    return survey["order"].get(str(chapter.number), {}).get("read", "other")


def focused(book, focus):
    """The chapters to read again for what the person asks: those it names ("chapter 7"), then those most about it
    by its words, READ_FOCUS_CHAPTERS in all, in the source's order; never the raw ones."""
    readable = [c for c in book.chapters if c.state != "raw"]
    named = {int(n) for n in NAMED_CHAPTER_RE.findall(focus)}
    ranked = Retriever(None).by_words(focus, [c.text for c in readable])
    chosen = [c for c in readable if c.number in named] + [readable[i] for i in ranked]
    return sorted({c.number: c for c in chosen[:READ_FOCUS_CHAPTERS]}.values(), key=lambda c: c.number)


def book_bytes(source):
    """The bytes of the book at a web address (downloaded) or in a file."""
    if is_web_address(source):
        data, why = Internet.download(source, READ_MAX_BYTES)
        if why:
            raise SystemExit(f"Could not download {source}: {why}")
        return data
    path = Path(source).expanduser()
    if not path.is_file():
        raise SystemExit(f"No file at {source}: give a book's file or its web address.")
    if path.stat().st_size > READ_MAX_BYTES:
        raise SystemExit(f"{source} is larger than {READ_MAX_BYTES // 1_000_000} MB; nothing read.")
    return path.read_bytes()


def opened(data, name):
    """The book in a file's bytes, by what they hold (never by the file's name): an EPUB, else Markdown or plain text.
    A PDF, a web page or any other file is refused, with what to do."""
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
    """The title of a text whose top heading is used once (# Title, then ## chapters); "" otherwise."""
    headings = [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(text)]
    top = [title for level, title in headings if level == min((l for l, _ in headings), default=0)]
    return top[0].strip() if len(top) == 1 and len(headings) > 1 else ""


def headed_chapters(text):
    """The chapters of a text marked with Markdown headings (#), down to the level that gives READ_CHAPTERS (a lone
    top heading is its title, not a chapter); [] when it has no headings."""
    headings = [(len(m.group(1)), m.group(2), m.start()) for m in HEADING_RE.finditer(text)]
    if lone_title(text):
        headings = [heading for heading in headings if heading[0] != min(level for level, _, _ in headings)]
    depth = chapter_depth([level for level, _, _ in headings])
    return sliced(text, {start: title for level, title, start in headings if level <= depth})


def plain_chapters(text):
    """The chapters of a plain text: at its chapter lines ("Chapter 3", "Preface"), else parts of READ_WINDOW_CHARS."""
    marks = {m.start(): m.group(0).strip() for m in CHAPTER_LINE_RE.finditer(text)}
    if len(marks) >= 2:
        return sliced(text, marks)
    return numbered([(f"Part {n}", piece) for n, piece in enumerate(split_text(text, READ_WINDOW_CHARS), 1)])


def chapter_depth(depths):
    """The depth of the contents (or of the headings) that chapters are split at: the shallowest that gives at least
    READ_CHAPTERS of them, else the deepest (a book of three parts is split at their chapters)."""
    for depth in sorted(set(depths)):
        if sum(d <= depth for d in depths) >= READ_CHAPTERS:
            return depth
    return max(depths, default=1)


def sliced(text, marks):
    """The chapters that start at the marks ({offset: title}), each to where the next starts; what comes before the
    first is a chapter of its own (the opening pages). [] with no marks."""
    if not marks:
        return []
    starts = sorted(marks)
    pieces = [("Opening pages", text[:starts[0]])] if starts[0] > 0 else []
    pieces += [(marks[start], text[start:end]) for start, end in zip(starts, starts[1:] + [len(text)])]
    return numbered(pieces)


def numbered(pieces):
    """Chapters numbered from 1, of the (title, text) pieces that hold more than a heading, each with its kind."""
    pieces = [(" ".join(title.split()), tidy(text)) for title, text in pieces]
    pieces = [(title, text) for title, text in pieces if len(words(text)) > len(words(title)) + 3]
    return [Chapter(n, title, text, kind_of(title)) for n, (title, text) in enumerate(pieces, 1)]


def tidy(text):
    """Text with its lines' spaces collapsed and at most one blank line in a row."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def kind_of(title):
    """A chapter's kind, by its title: index, raw (front or back matter), front (it says what the source is about)
    or body."""
    name = title.lower()
    if re.search(r"\bindex\b", name):
        return "index"
    if any(word in name for word in RAW):
        return "raw"
    return "front" if any(word in name for word in FRONT) else "body"


def index_terms(book, limit=READ_INDEX_TERMS):
    """[(term, references)] of the book's index, the most referred to first: what the book itself says matters."""
    counts = collections.Counter()
    for chapter in book.of_kind("index"):
        for match in INDEX_LINE_RE.finditer(chapter.text):
            counts[match.group(1).strip()] += len(re.findall(r"\d+", match.group(2)))
    return counts.most_common(limit)


def survey_parts(book):
    """The survey's prompt: the book, its contents (each chapter's kind and length), what it says about itself and
    the terms its index refers to most."""
    contents = "\n".join(f"{c.number}. {c.title} ({c.kind}, {c.size()} words)" for c in book.chapters)
    about = "\n\n".join(f"## {c.title}\n{c.text}" for c in book.of_kind("front"))
    terms = ", ".join(f"{term} ({count})" for term, count in index_terms(book))
    return [Part("The book", book.title + (f" by {book.author}" if book.author else ""), True),
            Part("Its contents", contents, True, exact=True),
            Part("What it says about itself", about or "(no foreword, preface or introduction)", True),
            Part("The terms its index refers to most (how often)", terms)]


def read_survey(data, book):
    """{summary, software, terms, order: {chapter number, as text: {read, look_for}}} (read: core, supporting or
    other; a chapter the reply leaves out is other: read last, never skipped). ValueError without a summary."""
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("no summary")
    numbers, order = {c.number for c in book.chapters}, {}
    for item in data.get("chapters") or []:
        if isinstance(item, dict) and type(item.get("chapter")) is int and item["chapter"] in numbers:
            order[str(item["chapter"])] = {"read": item.get("read") if item.get("read") in ORDER else "other",
                                      "look_for": first_line(str(item.get("look_for") or ""), 200)}
    terms = [first_line(str(term), 60) for term in data.get("terms") or [] if str(term).strip()][:READ_TERMS]
    return {"summary": summary, "software": data.get("software") is True, "terms": terms, "order": order}


def excerpt(text, room, wanted):
    """A chapter as its prompt shows it: whole when it fits room; else its opening and the parts most about what is
    wanted, READ_CHUNKS in all, in the chapter's order."""
    if len(text) <= room:
        return text
    chunks = split_text(text, room // READ_CHUNKS)
    matched = [index for index in Retriever(None).by_words(wanted, chunks) if index]
    chosen = [0] + (matched + [i for i in range(1, len(chunks)) if i not in matched])[:READ_CHUNKS - 1]
    return "\n\n[…]\n\n".join(chunks[index] for index in sorted(chosen))


def read_points(data, software):
    """[{text, phase, section}] of a chapter's reply, at most READ_POINTS. A point that does not change how software
    is built, and every point of a source not about building it, has the phase book: no build is shown it."""
    if not isinstance(data.get("points"), list):
        raise ValueError('no "points" list')
    points = []
    for point in data["points"]:
        text = str(point.get("text") or "").strip() if isinstance(point, dict) else ""
        if text:
            phase = str(point.get("phase") or "").strip().lower()
            points.append({"text": text[:FACT_CHARS], "phase": phase if software and phase in FACT_PHASES else "book",
                           "section": first_line(str(point.get("section") or ""), 80)})
    return points[:READ_POINTS]


class Epub:
    """An EPUB: a zip of XHTML documents read in its spine's order; its contents (EPUB 3's navigation document, else
    EPUB 2's NCX) mark where each chapter starts."""

    def __init__(self, data):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            self.opf_path = self.xml("META-INF/container.xml").find(".//{*}rootfile").get("full-path")
            self.opf = self.xml(self.opf_path)
        except (zipfile.BadZipFile, KeyError, AttributeError, TypeError, ET.ParseError) as exc:
            raise SystemExit(f"This is not an EPUB Tinker can read ({first_line(str(exc), 120)}).") from None
        self.manifest = {item.get("id"): item for item in self.opf.iterfind(".//{*}manifest/{*}item")}

    def book(self, title):
        """The book: its title and author, and its chapters where its contents mark them (else at its headings,
        else in parts)."""
        text, starts = self.spine_text()
        chapters = contents_chapters(text, starts, self.contents()) or headed_chapters(text) or plain_chapters(text)
        return Book(self.metadata("title") or title, self.metadata("creator"), chapters)

    def xml(self, path):
        return ET.fromstring(self.zip.read(path))

    def text_of(self, path):
        return self.zip.read(path).decode("utf-8", errors="replace")

    def metadata(self, name):
        found = self.opf.find(f".//{{*}}metadata/{{*}}{name}")
        return " ".join((found.text or "").split()) if found is not None else ""

    def spine_text(self):
        """(the text of the spine's documents in order, where each starts: {(path, ""): offset, (path, id): offset})."""
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
        return "".join(parts), starts

    def contents(self):
        """[(depth, title, (path, fragment))] of the table of contents: EPUB 3's navigation document, else the NCX."""
        nav = next((item for item in self.manifest.values() if "nav" in (item.get("properties") or "").split()), None)
        if nav is not None:
            document = located(nav.get("href"), self.opf_path)[0]
            entries = NavEntries.of(self.text_of(document))
        else:
            ncx = next((item for item in self.manifest.values()
                        if item.get("media-type") == "application/x-dtbncx+xml"), None)
            if ncx is None:
                return []
            document = located(ncx.get("href"), self.opf_path)[0]
            entries = list(ncx_entries(self.xml(document).find(".//{*}navMap")))
        return [(depth, title, located(href, document)) for depth, title, href in entries]


def located(href, document):
    """(the path in the zip, the fragment) of an address relative to a document of the EPUB."""
    path, _, fragment = urllib.parse.unquote(href or "").partition("#")
    return posixpath.normpath(posixpath.join(posixpath.dirname(document), path)) if path else document, fragment


def contents_chapters(text, starts, contents):
    """The chapters the contents mark in the text: each entry down to the depth that gives READ_CHAPTERS, from where
    its target starts (its document's start when the fragment is not found) to where the next one does."""
    depth = chapter_depth([entry_depth for entry_depth, _, _ in contents])
    marks = {}
    for entry_depth, title, (path, fragment) in contents:
        start = starts.get((path, fragment), starts.get((path, "")))
        if entry_depth <= depth and start is not None:
            marks.setdefault(start, title)
    return sliced(text, marks)


def ncx_entries(parent, depth=1):
    """(depth, title, src) of each navPoint under parent, in order (EPUB 2's contents)."""
    if parent is None:
        return
    for point in parent.findall("{*}navPoint"):
        label, content = point.find("{*}navLabel/{*}text"), point.find("{*}content")
        if label is not None and content is not None:
            yield depth, " ".join((label.text or "").split()), content.get("src")
        yield from ncx_entries(point, depth + 1)


class XhtmlText(html.parser.HTMLParser):
    """A book's XHTML document as text: blocks apart, headings marked as Markdown's (#), list items as "- "; the head,
    scripts and styles left out. ids: where each element with an id starts in the text."""
    BLOCKS = {"p", "div", "section", "article", "aside", "blockquote", "pre", "table", "tr", "ul", "ol", "dl", "dt",
              "dd", "figure", "figcaption", "hr", "header", "footer", "body"}
    HEADINGS = {f"h{level}": level for level in range(1, 7)}
    HIDDEN = {"head", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.size, self.ids, self.hidden = [], 0, {}, 0

    @classmethod
    def of(cls, markup):
        """(the text, {id: where it starts})."""
        parser = cls()
        parser.feed(markup)
        parser.close()
        return "".join(parser.parts), parser.ids

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
    """The entries of an EPUB 3 navigation document's table of contents (its toc nav, else its first one), as
    (depth, title, href): depth 1 at the top."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.navs, self.depth, self.link, self.words, self.inside = [], 0, None, [], False

    @classmethod
    def of(cls, markup):
        parser = cls()
        parser.feed(markup)
        parser.close()
        tocs = [entries for toc, entries in parser.navs if toc and entries] or [e for _, e in parser.navs if e]
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
