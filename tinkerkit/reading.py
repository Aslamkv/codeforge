import collections
import dataclasses
import hashlib
import re
import sys
from collections import namedtuple
lazy import base64
lazy import shutil
lazy import subprocess
lazy import tempfile
lazy import urllib.parse
lazy from pathlib import Path

from .messages import next_page_line, say
lazy from .formats import Book, opened
lazy from .jobs import job_number
lazy from .knowledge import Library, mentions, relevant_chunks
lazy from .model import Models, Ollama, OllamaError
lazy from .principles import Inducer, Principles
lazy from .project import Tracked, ending
lazy from .prompts import Part, Prompts
lazy from .retrieval import Embeddings, Retriever, traits_part
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
    Home,
    Numbered,
    confirm_deletion,
)
lazy from .text import field, first_line, now_text, split_text
lazy from .web import WHOLE, Internet, is_web_address

CHAPTER_ROLE_ORDER = ("core", "supporting", "other")
READINGS = Numbered("readings", "reading numbers")
READ_STEPS = ("survey", "chapters", "done")
CHAPTER_STATE_ICONS = {"read": "✅", "unread": "⏳", "raw": "📄"}
KIND_ICONS = {"book": "📕", "document": "📄", "image": "🖼", "audio": "🎧"}
NAMED_CHAPTER_RE = re.compile(r"\bchapters?\s+(\d+)", re.I)
INDEX_LINE_RE = re.compile(r"^[-\s]*([^\d\n,;:]{2,60}?)[\s,;:]+((?:\d+(?:\s*[-–]\s*\d+)?[\s,;]*)+)$", re.M)

IndexTerm = namedtuple("IndexTerm", "term references")
Kept = namedtuple("Kept", "kind name size fingerprint")


def kept(source, beyond=False):
    say.step(f"Storing {first_line(source, 80)}…", "📥")
    name = source_name(source)
    data = source_bytes(source, beyond)
    key = hashlib.sha1(data).hexdigest()[:16]
    known = Readings.with_fingerprint(key)
    if known is not None:
        entry = Readings.get(known)
        if entry.get("file"):
            without_downloaded_copy(source, Home.reading_file(entry["file"]))
        with say.section():
            say.info(
                f"Already on the reading list as #{known} ({kind_of(entry)}): {next_after_keeping(known)}",
                "📚",
            )
        return known, False
    found = opened(data, name)
    try:
        book = sensed(found, name)
    except Unsensed as refusal:
        number = stored(Book(found.title, found.author, []), source, found, data, key)
        with say.section():
            say.warn(f"#{number} {found.title} kept with no chapters: {refusal}")
        return number, True
    if not book.of_kind("body", "front"):
        raise SystemExit(f"Found nothing to read in {source}; nothing stored.")
    number = stored(book, source, found, data, key)
    with say.section():
        say.done(
            f"#{number} ({found.kind}) {book.title}{f' by {book.author}' if book.author else ''}: "
            f"{len(book.chapters)} chapter(s), {len(book.in_state('raw'))} kept as text only"
        )
        say.info(f"Next: {next_after_keeping(number)}", "📚")
    return number, True


def heard_again(number):
    entry = known_reading(number)
    if entry["chapters"] or kind_of(entry) != "audio" or not entry.get("file"):
        return
    name = source_name(entry["source"])
    found = opened(Home.reading_file(entry["file"]).read_bytes(), name)
    try:
        book = sensed(found, name)
    except Unsensed as refusal:
        with say.section():
            say.warn(f"#{number} {entry['title']} kept with no chapters: {refusal}")
        return
    chapters = [dataclasses.asdict(Readings.unread(chapter)) for chapter in book.chapters]
    Readings.save(dict(entry, chapters=chapters))


def source_name(source):
    return Path(urllib.parse.urlsplit(source).path).name or source


def next_after_keeping(number):
    return f"ask about it in a chat, or read #{number} learn to learn from it"


def sensed(found, name):
    if not found.readable:
        raise Unsensed(f"{name} is not a file Tinker can read")
    return found.sensed(Senses(name))


def stored(book, source, found, data, key):
    file = Home.reading_file(f"{key}{Path(found.name).suffix.lower()}")
    file.write_bytes(data)
    without_downloaded_copy(source, file)
    return Readings.add(book, source, Kept(found.kind, file.name, len(data), key))


def without_downloaded_copy(source, file):
    copy = Path(source).resolve()
    if copy.parent == file.parent.resolve() and copy != file.resolve() and copy.is_file():
        copy.unlink()


class Unsensed(Exception):
    pass


class TooLarge(SystemExit):
    pass


class Senses:
    REPLY_TOKENS = 4096
    FFMPEG_INSTALL = {"darwin": "brew install ffmpeg"}
    FFMPEG_INSTALL_ELSEWHERE = "sudo apt install ffmpeg"

    def __init__(self, name):
        self.name = name

    def seen(self, image):
        self.need_sight(f"{self.name} is an image: describing it needs a model that sees")
        return self.looked(Prompts.SEE_IMAGE, image)

    def scanned(self, images):
        if not images:
            raise Unsensed(f"{self.name} has no text and no page image Tinker can read")
        self.need_sight(f"{self.name} is a scan: reading its pages needs a model that sees")
        return [self.looked(Prompts.READ_PAGE, image) for image in images]

    def heard(self, data, suffix):
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            install = self.FFMPEG_INSTALL.get(sys.platform, self.FFMPEG_INSTALL_ELSEWHERE)
            raise Unsensed(f"{self.name} is a recording: audio needs ffmpeg, installed with `{install}`")
        if Models.hearing() is None:
            self.get_hearing()
        try:
            return Models.transcript(self.wav(ffmpeg, data, suffix))
        except subprocess.CalledProcessError as error:
            raise Unsensed(f"{self.name} is a recording: {self.ffmpeg_failure(error)}")
        except OllamaError as error:
            raise Unsensed(f"{self.name} is a recording: {error}")

    @staticmethod
    def ffmpeg_failure(error):
        lines = [line.strip() for line in error.stderr.decode(errors="replace").splitlines() if line.strip()]
        if not lines:
            return f"ffmpeg failed with exit code {error.returncode}"
        return f"ffmpeg failed: {lines[-1]}"

    def get_hearing(self):
        name = Models.audio_offer()
        if not say.agreed(Models.audio_question(name)):
            raise Unsensed(f"{self.name} is a recording: no model that hears was got")
        try:
            Ollama.pull(name)
        except OllamaError as error:
            raise Unsensed(f"{self.name} is a recording: {error}")

    def need_sight(self, reason):
        if "vision" not in Models.senses():
            raise Unsensed(f"{reason} (vision) as the quick model")

    def looked(self, prompt, image):
        message = {"role": "user", "content": prompt, "images": [base64.b64encode(image).decode()]}
        return Models.quick_reply([message], self.REPLY_TOKENS)

    @staticmethod
    def wav(ffmpeg, data, suffix):
        with tempfile.NamedTemporaryFile(suffix=suffix) as recording:
            recording.write(data)
            recording.flush()
            command = [ffmpeg, "-nostdin", "-loglevel", "error", "-i", recording.name]
            command += ["-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"]
            return subprocess.run(command, capture_output=True, check=True).stdout


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
                f'Every chapter of #{number} is read: read #{number} learn "WHAT" reads it again with a focus',
                "📚",
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
            deltas += inducer.induce([passage], entry["topic"], look_for=focus or look_for, label=label)
        return [read_phase(delta, survey["software"]) for delta in deltas][:READ_FACTS_PER_CHAPTER]


class Readings:
    @staticmethod
    def add(book, source, file):
        number = READINGS.next_number()
        chapters = [dataclasses.asdict(Readings.unread(chapter)) for chapter in book.chapters]
        Readings.save(
            {
                "number": number,
                "kind": file.kind,
                "title": book.title,
                "author": book.author,
                "file": file.name,
                "source": source,
                "size": file.size,
                "fingerprint": file.fingerprint,
                "added": now_text(),
                "learned": False,
                "topic": f"read-{number}",
                "survey": None,
                "chapters": chapters,
            }
        )
        return number

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
    def renamed(number, title):
        Readings.save(dict(Readings.get(number), title=title))

    @staticmethod
    def forget(number):
        entry = Readings.get(number)
        if entry.get("file"):
            Home.reading_file(entry["file"]).unlink(missing_ok=True)
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


def answered_inside(number, question):
    entry = Readings.get(number)
    system = {
        "role": "system",
        "content": Prompts.INSIDE_READING.format(title=entry["title"]) + traits_part(question),
    }
    return Models.quick_reply([system, inside_question(entry, question)], Senses.REPLY_TOKENS)


class Shelf:
    def entered(self, words):
        entry = self.found(words)
        return (entry["number"], entry["title"]) if entry and entry["chapters"] else None

    @staticmethod
    def found(words):
        number = job_number(words[0]) if len(words) == 1 else None
        if number:
            return Readings.get(number)
        source = " ".join(words)
        return next((entry for entry in Readings.all().values() if entry["source"] == source), None)

    @staticmethod
    def exists(number):
        return Readings.get(number) is not None

    @staticmethod
    def answered(number, text):
        return answered_inside(number, text)


def inside_question(entry, question):
    passages = Readings.passages([entry["number"]], question)
    shown = "\n\n".join(f"[{passage['source']}]\n{passage['text']}" for passage in passages)
    message = {"role": "user", "content": f"Passages:\n{shown}\n\nQuestion: {question}"}
    if kind_of(entry) == "image" and "vision" in Models.senses():
        image = Home.reading_file(entry["file"]).read_bytes()
        message["images"] = [base64.b64encode(image).decode()]
    return message


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
            ("Look again", f'tinker read #{number} learn "what to look for"'),
            ("See it", f"tinker read show #{number}"),
        ],
    )


def show_list(page=1, inside=None):
    readings, pages = Numbered.page(READINGS.newest(), page)
    if not readings:
        say.info("Nothing on the reading list: tinker read FILE | URL adds one (in a chat: attach it).", "📭")
        return
    with say.part("Reading list", "📚"):
        for entry in readings:
            say.item(listed(entry, inside))
    if page < pages:
        say.info(next_page_line(page, pages, f"read list {page + 1}"), "👉")
    say.info("tinker read show #N shows one; tinker read #N goes on with it.", "👉")


def listed(entry, inside=None):
    size = f" · {byte_size(entry['size'])}" if entry.get("size") else ""
    mark = " ✓" if entry.get("learned") else ""
    here = " ← inside" if entry["number"] == inside else ""
    return (
        f"#{entry['number']} {KIND_ICONS[kind_of(entry)]} {entry['title']}: {progress(entry)}{size} · added "
        f"{day_added(entry)}{mark}{here}"
    )


def day_added(entry):
    return entry["added"].split()[0]


def kind_of(entry):
    return entry.get("kind", "book")


def byte_size(count):
    for unit in ("B", "KB", "MB"):
        if count < 1000:
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1000
    return f"{count:.1f} GB"


def show(number):
    entry = known_reading(number)
    with say.part(
        f"#{number} {entry['title']}" + (f" by {entry['author']}" if entry["author"] else ""), "📘"
    ):
        kept_as = [kind_of(entry), entry.get("file"), entry.get("size") and byte_size(entry["size"])]
        say.info(f"{KIND_ICONS[kind_of(entry)]} {' · '.join(part for part in kept_as if part)}", "🗂")
        say.info(f"From {entry['source']}, added {day_added(entry)}: {progress(entry)}", "📥")
        if entry["survey"]:
            say.info(entry["survey"]["summary"], "📝")
        else:
            say.info(f"No summary yet: tinker read #{number} learn writes one", "📝")
        for chapter in Book.of(entry).chapters:
            note = {"raw": "text only", "unread": "not read yet"}.get(
                chapter.state, f"{len(chapter.principles)} principle(s)"
            )
            say.item(f"{CHAPTER_STATE_ICONS[chapter.state]} {chapter.number}. {chapter.title} ({note})")
    say.info(
        f'tinker read #{number} learn goes on · tinker read #{number} learn "WHAT" looks again · tinker read '
        f"rename #{number} TITLE renames it · tinker read forget #{number} deletes it",
        "👉",
    )


def renamed(number, title):
    known_reading(number)
    Readings.renamed(number, title)
    say.done(f"Renamed: #{number} {title}")


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


def source_bytes(source, beyond=False):
    if is_web_address(source):
        [page] = Internet().fetch([source], WHOLE if beyond else READ_MAX_BYTES)
        if page.why.startswith("larger than"):
            raise TooLarge(f"Could not download {source}: {page.why}; nothing read.")
        if page.why:
            raise SystemExit(f"Could not download {source}: {page.why}")
        return page.data
    path = Path(source).expanduser()
    if not path.is_file():
        raise SystemExit(f"No file at {source}: give a book's file or its web address.")
    if path.stat().st_size > READ_MAX_BYTES and not beyond:
        raise TooLarge(f"{source} is larger than {READ_MAX_BYTES // 1_000_000} MB; nothing read.")
    return path.read_bytes()


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
