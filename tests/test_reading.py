"""RDG: reading what the person gives: stored whole on the reading list first, then every chapter learned from, the
core ones first, saved as it is read; read #N "WHAT" looks again."""

import io
import json
import zipfile

import pytest

import tinker
from tinker import Cli, Commands
from tinkerkit import conversation, reading
from tinkerkit.fetcher import Fetcher
from tinkerkit.jobs import is_job
from tinkerkit.knowledge import Library
from tinkerkit.model import Ollama
from tinkerkit.reading import (
    Book, Chapter, Reader, Readings, book_bytes, excerpt, in_order, index_terms, kind_of, opened, read_points,
    read_survey, show, show_list, stored,
)
from tinkerkit.settings import FACT_CHARS, READ_CHUNKS, READ_POINTS, READ_TERMS, Home
from conftest import MODEL

CONTAINER = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
             '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
             '</rootfiles></container>')
FOREWORD = "This book teaches how to write small programs that stay easy to change. Its heart is naming and testing."
NAMING = "Name each function after what it does, so that its callers read like prose and need no comment at all."
TESTING = "Write one test for each behaviour before the code, and run every test after each change you make."
INDEX = "- naming, 3, 7, 12\n- testing, 4–6, 9\n- coupling, 11"
NAV = ('<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
       '<nav epub:type="landmarks"><ol><li><a href="text/cover.xhtml">Cover</a></li></ol></nav>'
       '<nav epub:type="toc"><ol><li><a href="text/foreword.xhtml">Foreword</a></li>'
       '<li><a href="text/part1.xhtml">Part I</a><ol><li><a href="text/part1.xhtml#naming">Naming</a></li>'
       '<li><a href="text/part1.xhtml#testing">Testing</a></li></ol></li>'
       '<li><a href="text/index.xhtml">Index</a></li></ol></nav></body></html>')
NCX = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
       '<navPoint id="a"><navLabel><text>Foreword</text></navLabel><content src="text/foreword.xhtml"/></navPoint>'
       '<navPoint id="b"><navLabel><text>Part I</text></navLabel><content src="text/part1.xhtml"/>'
       '<navPoint id="c"><navLabel><text>Naming</text></navLabel><content src="text/part1.xhtml#naming"/></navPoint>'
       '<navPoint id="d"><navLabel><text>Testing</text></navLabel><content src="text/part1.xhtml#testing"/></navPoint>'
       '</navPoint><navPoint id="e"><navLabel><text>Index</text></navLabel><content src="text/index.xhtml"/>'
       '</navPoint></navMap></ncx>')
DOCUMENTS = {"cover.xhtml": "<h1>The Craft</h1>",
             "foreword.xhtml": f"<h1>Foreword</h1><p>{FOREWORD}</p>",
             "part1.xhtml": "<h1>Part I</h1><p>The basics that every small program needs from its first line.</p>"
                            f"<h2 id='naming'>Naming</h2><p>{NAMING}</p><h2 id='testing'>Testing</h2><p>{TESTING}</p>",
             "index.xhtml": "<h1>Index</h1><ul>" + "".join(f"<li>{line[2:]}</li>" for line in INDEX.splitlines())
                            + "</ul>"}

def epub(nav=True):
    """An EPUB of The Craft (cover, foreword, a part of two chapters in one document, an index): its contents in an
    EPUB 3 navigation document, else in an EPUB 2 NCX."""
    manifest = "".join(f'<item id="d{n}" href="text/{name}" media-type="application/xhtml+xml"/>'
                       for n, name in enumerate(DOCUMENTS))
    manifest += ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>' if nav
                 else '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    spine = "".join(f'<itemref idref="d{n}"/>' for n in range(len(DOCUMENTS)))
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Craft of Small Programs</dc:title>'
           f'<dc:creator>A. Writer</dc:creator></metadata><manifest>{manifest}</manifest>'
           f'<spine{"" if nav else " toc=\"ncx\""}>{spine}</spine></package>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", CONTAINER)
        book.writestr("OEBPS/content.opf", opf)
        book.writestr("OEBPS/nav.xhtml" if nav else "OEBPS/toc.ncx", NAV if nav else NCX)
        for name, body in DOCUMENTS.items():
            book.writestr(f"OEBPS/text/{name}", f"<html><head><title>x</title><style>p{{}}</style></head>"
                                                f"<body>{body}</body></html>")
    return buffer.getvalue()

MARKDOWN = f"""# The Craft of Small Programs

## Foreword

{FOREWORD}

## Naming

{NAMING}

## Stories

Once a team wrote a very long program, and then another one, and then a third one after that.

## Testing

{TESTING}

## Index

{INDEX}
"""

def titles(book):
    return [(chapter.title, chapter.kind) for chapter in book.chapters]

@pytest.mark.spec("RDG-1")
def test_a_book_is_read_by_what_its_bytes_hold():
    assert opened(epub(), "anything.txt").title == "The Craft of Small Programs"
    assert opened(MARKDOWN.encode(), "craft.md").title == "The Craft of Small Programs"
    assert opened(b"Some words\n\nand more words.", "my_little-book.txt").title == "my little book"
    with pytest.raises(SystemExit, match="This is a PDF.*Calibre"):
        opened(b"%PDF-1.7 ...", "book.epub")
    with pytest.raises(SystemExit, match="a web page, not something to read.*download link"):
        opened(b"<!DOCTYPE html><html><body>Sign in</body></html>", "book.epub")
    with pytest.raises(SystemExit, match="not a book Tinker can read"):
        opened(b"\x89PNG\r\n\x1a\n\x00\x00", "cover.png")
    with pytest.raises(SystemExit, match="not an EPUB"):
        opened(b"PK\x03\x04 broken", "book.epub")

@pytest.mark.spec("RDG-2")
@pytest.mark.parametrize("nav", [True, False], ids=["epub3-nav", "epub2-ncx"])
def test_an_epub_is_split_where_its_contents_say(nav):
    book = opened(epub(nav), "book.epub")
    assert book.author == "A. Writer"
    assert titles(book) == [("Foreword", "front"), ("Part I", "body"), ("Naming", "body"), ("Testing", "body"),
                            ("Index", "index")]
    naming = book.chapters[2]
    assert NAMING in naming.text and TESTING not in naming.text and "p{}" not in naming.text

@pytest.mark.spec("RDG-2")
def test_an_epub_with_no_usable_contents_is_split_at_its_headings(monkeypatch):
    monkeypatch.setattr(reading.Epub, "contents", lambda self: [])
    book = opened(epub(), "book.epub")
    assert [title for title, _ in titles(book)] == ["Foreword", "Part I", "Naming", "Testing", "Index"]

@pytest.mark.spec("RDG-3")
def test_markdown_and_plain_text_are_split_into_chapters():
    assert titles(opened(MARKDOWN.encode(), "craft.md")) == [
        ("Foreword", "front"), ("Naming", "body"), ("Stories", "body"), ("Testing", "body"), ("Index", "index")]
    plain = (f"Contents\nChapter 1 Naming\nChapter 2 Testing\n\nChapter 1 Naming\n{NAMING}\n\n"
             f"Chapter 2 Testing\n{TESTING}")
    assert [title for title, _ in titles(opened(plain.encode(), "craft.txt"))] == [
        "Chapter 1 Naming", "Chapter 2 Testing"]
    parts = opened(("A long plain paragraph of words with no heading at all.\n\n" * 800).encode(), "long.txt")
    assert [chapter.title for chapter in parts.chapters][:2] == ["Part 1", "Part 2"]

@pytest.mark.spec("RDG-4")
def test_chapters_have_kinds_and_the_index_names_what_matters():
    assert [kind_of(title) for title in ("Index", "Table of Contents", "Acknowledgements", "Preface",
                                          "Introduction", "3. Naming things")] == \
           ["index", "raw", "raw", "front", "front", "body"]
    assert index_terms(opened(MARKDOWN.encode(), "craft.md")) == [("naming", 3), ("testing", 3), ("coupling", 1)]

def craft(tmp_path, name="craft.md", text=MARKDOWN):
    """The Craft, as a Markdown file: 1 Foreword, 2 Naming, 3 Stories, 4 Testing, 5 Index."""
    path = tmp_path / name
    path.write_text(text)
    return str(path)

def survey_reply(software=True, **reads):
    reads = reads or {"4": "core", "2": "supporting"}
    return json.dumps({"summary": "Small programs stay easy to change when named well and tested first.",
                       "software": software, "terms": ["naming", "testing"],
                       "chapters": [{"chapter": int(n), "read": read, "look_for": f"what chapter {n} teaches"}
                                    for n, read in reads.items()]})

def points(*texts, phase="code"):
    return json.dumps({"points": [{"text": text, "phase": phase, "section": ""} for text in texts]})

class Steps:
    """A tracker that only remembers the steps it was moved through, and what came of each."""

    def __init__(self):
        self.steps, self.outcomes, self.state = [], {}, {"step": None}

    def phase(self, phase):
        self.steps.append(phase)
        self.state["step"] = phase

    def outcome(self, text):
        self.outcomes[self.state["step"]] = text

def labels(fake_ollama):
    return [call["label"].split(" (")[0] for call in fake_ollama.calls]

def prompts(fake_ollama):
    return [call["messages"][1]["content"] for call in fake_ollama.calls]

@pytest.mark.spec("RDG-5")
def test_a_source_is_stored_whole_before_anything_is_read(tmp_path, home_store, capsys):
    assert stored(craft(tmp_path)) == (1, True)
    entry = Readings.get(1)
    assert (entry["title"], entry["source"], entry["topic"], entry["survey"]) == (
        "The Craft of Small Programs", str(tmp_path / "craft.md"), "read-1", None)
    assert [(c["title"], c["state"]) for c in entry["chapters"]] == [
        ("Foreword", "unread"), ("Naming", "unread"), ("Stories", "unread"), ("Testing", "unread"), ("Index", "raw")]
    assert entry["chapters"][1]["text"].endswith(NAMING)
    assert stored(craft(tmp_path, "same-text.md")) == (1, False)
    assert "Already on the reading list as #1" in capsys.readouterr().out
    assert stored(craft(tmp_path, "other.md", MARKDOWN + "\nOne more line of the second edition.")) == (2, True)
    assert list(Readings.all()) == [2, 1]

@pytest.mark.spec("RDG-6")
def test_the_survey_orders_every_chapter_and_skips_none(tmp_path, home_store):
    book = opened(MARKDOWN.encode(), "craft.md")
    survey = read_survey(dict(json.loads(survey_reply()), terms=[f"t{n}" for n in range(30)]), book)
    assert survey["software"] is True and len(survey["terms"]) == READ_TERMS
    assert survey["order"] == {"4": {"read": "core", "look_for": "what chapter 4 teaches"},
                               "2": {"read": "supporting", "look_for": "what chapter 2 teaches"}}
    assert [c.number for c in in_order(book.chapters, survey)] == [4, 2, 1, 3, 5]
    odd = read_survey(json.loads(survey_reply(**{"1": "skip"})), book)
    assert odd["order"]["1"]["read"] == "other"
    assert read_survey(dict(json.loads(survey_reply()), software="yes"), book)["software"] is False
    with pytest.raises(ValueError, match="no summary"):
        read_survey({"chapters": []}, book)

def read_craft(tmp_path, make_model, fake_ollama, software=True):
    """The Craft stored and read whole: chapter 4 is core, 2 supporting, then 1 and 3."""
    stored(craft(tmp_path))
    fake_ollama.reply(survey_reply(software), when="reading plan")
    fake_ollama.reply(points("Write a test for each behaviour first", phase="tests"), when="chapter 4")
    fake_ollama.reply(points("Name each function after what it does"), when="chapter 2")
    fake_ollama.reply(points("Small programs stay easy to change", phase="design"), when="chapter 1")
    fake_ollama.reply(points("Write a test for each behaviour first", phase="tests"), when="chapter 3")
    if software:
        fake_ollama.reply(json.dumps({"rules": [{"phase": "code", "text": "Name functions by what they do",
                                                 "when": "", "cites": [1]}], "declare": None}), when="Condense")
    tracker = Steps()
    return Reader(make_model(), tracker).read(1), tracker

@pytest.mark.spec("RDG-7")
def test_every_chapter_is_read_in_order_and_saved_as_it_is_read(tmp_path, home_store, make_model, fake_ollama):
    ending, tracker = read_craft(tmp_path, make_model, fake_ollama)
    assert labels(fake_ollama) == ["Read: the reading plan", "Read: chapter 4", "Read: chapter 2", "Read: chapter 1",
                                   "Read: chapter 3", "Condense the facts"]
    sent = prompts(fake_ollama)
    assert "1. Foreword (front" in sent[0] and FOREWORD in sent[0] and "testing (3)" in sent[0]
    assert TESTING in sent[1] and "- Write a test for each behaviour first" in sent[2]
    assert all("coupling, 11" not in prompt for prompt in sent[1:])  # the index is kept, never read
    topic = Library.read("read-1")
    assert topic["kind"] == "reading" and topic["reading"] == 1 and topic["software"] is True
    assert [(f["text"], f["chapter"]) for f in topic["facts"]] == [  # chapter 3 repeated chapter 4: kept once
        ("Write a test for each behaviour first", 4), ("Name each function after what it does", 2),
        ("Small programs stay easy to change", 1)]
    assert [r["text"] for r in topic["rules"]] == ["Name functions by what they do"]
    assert {c["state"] for c in Readings.get(1)["chapters"][:4]} == {"read"}
    assert tracker.steps == ["survey", "chapters", "condense"]
    assert tracker.outcomes["survey"] == "1 core chapter(s) of 5, read first"
    assert tracker.outcomes["chapters"] == "4 chapter(s), 3 things learned"
    assert ("Chapters read", "4 of 4") in ending["details"]
    assert ending["headline"] == "Read #1 The Craft of Small Programs"
    fake_ollama.calls.clear()
    assert Reader(make_model()).read(1)["details"][0] == ("Read now", "0 chapter(s), 0 new fact(s)")
    assert fake_ollama.calls == []

@pytest.mark.spec("RDG-7")
def test_a_stopped_reading_goes_on_with_the_chapters_not_read(tmp_path, home_store, make_model, fake_ollama):
    stored(craft(tmp_path))
    fake_ollama.reply(survey_reply(), when="reading plan")
    fake_ollama.reply(points("Write a test for each behaviour first", phase="tests"), when="chapter 4")
    fake_ollama.reply(KeyboardInterrupt(), when="chapter 2")
    with pytest.raises(KeyboardInterrupt):
        Reader(make_model()).read(1)
    assert [c["state"] for c in Readings.get(1)["chapters"]] == ["unread", "unread", "unread", "read", "raw"]
    assert [f["text"] for f in Library.read("read-1")["facts"]] == ["Write a test for each behaviour first"]
    fake_ollama.calls.clear()
    for chapter in (2, 1, 3):
        fake_ollama.reply(points(), when=f"chapter {chapter}")
    fake_ollama.reply(json.dumps({"rules": [{"phase": "tests", "text": "Test first", "cites": [1]}]}), when="Condense")
    Reader(make_model()).read(1)  # complete now: its rules are made, though this pass found nothing new
    assert labels(fake_ollama) == ["Read: chapter 2", "Read: chapter 1", "Read: chapter 3", "Condense the facts"]

@pytest.mark.spec("RDG-7")
def test_a_long_chapter_is_read_in_every_part(tmp_path, home_store, make_model, fake_ollama, monkeypatch):
    long = "\n\n".join(f"Paragraph {n} of a long chapter about naming things well." for n in range(40))
    stored(craft(tmp_path, text=f"# Long\n\n## One\n\n{long}\n\n## Two\n\nA short second chapter, with more words."))
    monkeypatch.setattr(reading, "READ_TEXT_SHARE", 0.05)
    fake_ollama.reply(survey_reply(software=False, **{"1": "core"}), when="reading plan")
    fake_ollama.reply(points(), times=20)
    Reader(make_model()).read(1)
    parts = [label for label in labels(fake_ollama) if label.startswith("Read: chapter 1")]
    assert len(parts) > 1 and parts[:2] == ["Read: chapter 1 part 1", "Read: chapter 1 part 2"]
    shown = "".join(prompts(fake_ollama)[1:1 + len(parts)])
    assert "Paragraph 0 " in shown and "Paragraph 39 " in shown

@pytest.mark.spec("RDG-8")
def test_read_again_with_a_focus_looks_where_it_is_asked(tmp_path, home_store, make_model, fake_ollama, monkeypatch):
    read_craft(tmp_path, make_model, fake_ollama)
    fake_ollama.calls.clear()
    monkeypatch.setattr(reading, "READ_FOCUS_CHAPTERS", 2)
    fake_ollama.reply(points("A long program was written three times"), when="chapter 3")
    fake_ollama.reply(points("Run every test after each change", phase="tests"), when="chapter 4")
    fake_ollama.reply(json.dumps({"rules": [{"phase": "tests", "text": "Run every test", "cites": [1]}]}),
                      when="Condense")
    ending = Reader(make_model()).read(1, "chapter 3, and when to run every test")
    assert labels(fake_ollama) == ["Read: chapter 3", "Read: chapter 4", "Condense the facts"]
    assert "## What the person asks you to look for\nchapter 3, and when to run every test" in prompts(fake_ollama)[0]
    assert ("Read now", "2 chapter(s), 2 new fact(s)") in ending["details"]
    assert Library.read("read-1")["facts"][-1]["text"] == "Run every test after each change"

@pytest.mark.spec("RDG-9")
def test_a_chapter_gives_points_with_their_phase():
    reply = {"points": [{"text": "Name functions by what they do", "phase": "code", "section": "Names"},
                        {"text": "x" * (FACT_CHARS + 50), "phase": "nowhere"}, {"text": " "}, "not a point"]
             + [{"text": f"point {n}", "phase": "tests"} for n in range(READ_POINTS)]}
    found = read_points(reply, software=True)
    assert len(found) == READ_POINTS
    assert found[0] == {"text": "Name functions by what they do", "phase": "code", "section": "Names"}
    assert found[1]["phase"] == "book" and len(found[1]["text"]) == FACT_CHARS
    assert {p["phase"] for p in read_points(reply, software=False)} == {"book"}
    with pytest.raises(ValueError, match="points"):
        read_points({"facts": []}, software=True)

@pytest.mark.spec("RDG-9")
def test_what_is_not_about_software_is_kept_for_asking_alone(tmp_path, home_store, make_model, fake_ollama):
    ending, tracker = read_craft(tmp_path, make_model, fake_ollama, software=False)
    topic = Library.read("read-1")
    assert {f["phase"] for f in topic["facts"]} == {"book"} and topic["rules"] == []
    assert "condense" not in tracker.steps and "Condense the facts" not in labels(fake_ollama)
    assert ("Builds", "never use it: it is not about building software") in ending["details"]

@pytest.mark.spec("RDG-10")
def test_a_source_comes_from_a_file_or_a_web_address(tmp_path, monkeypatch):
    path = tmp_path / "craft.md"
    path.write_text("words")
    assert book_bytes(str(path)) == b"words"
    with pytest.raises(SystemExit, match="No file at"):
        book_bytes(str(tmp_path / "missing.epub"))
    asked = []
    monkeypatch.setattr(reading.Internet, "download", staticmethod(
        lambda url, limit: asked.append(url) or ((b"book", "") if url.endswith("ok") else (b"", "HTTP 404"))))
    assert book_bytes("https://example.org/ok") == b"book"
    with pytest.raises(SystemExit, match="Could not download https://example.org/gone: HTTP 404"):
        book_bytes("https://example.org/gone")
    assert asked == ["https://example.org/ok", "https://example.org/gone"]
    monkeypatch.setattr(reading, "READ_MAX_BYTES", 2)
    with pytest.raises(SystemExit, match="larger than"):
        book_bytes(str(path))

@pytest.mark.spec("RDG-10")
def test_the_fetcher_downloads_bytes_as_they_are_within_its_limit(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"PK\x03\x04\x00\xff binary")
    fetcher = Fetcher(1, 5, 1000, 0)
    assert fetcher.download(path.as_uri(), 100) == (b"PK\x03\x04\x00\xff binary", "")
    assert fetcher.download(path.as_uri(), 5) == (b"", "it is larger than 0 MB")
    assert fetcher.download((tmp_path / "missing").as_uri(), 100)[1]

@pytest.mark.spec("RDG-11")
def test_the_readings_find_what_a_source_says(tmp_path, home_store):
    stored(craft(tmp_path))
    assert Readings.about("what does The Craft of Small Programs say?", []) == [1]
    assert Readings.about("anything", [{"group": "read-1"}, {"group": "python"}]) == [1]
    assert Readings.about("anything", [{"group": "python"}]) == []
    found = Readings.passages([1], "when to run every test")
    assert found[0] == {"source": "The Craft of Small Programs, chapter 4: Testing", "text": f"## Testing\n\n{TESTING}"}

@pytest.mark.spec("RDG-12")
def test_tinker_read_stores_then_reads_as_its_own_job(tmp_path, measured_model, fake_ollama, monkeypatch, capsys):
    assert is_job(["read", "book.epub"]) and is_job(["read", "#2", "retries"]) and "read" in conversation.LONG
    assert Cli.parse(["read", "~/Books/the", "craft.epub"]).read == ["~/Books/the craft.epub"]
    assert Cli.parse(["read", "#2", '"retries,', 'when"']).read == ["#2", "retries, when"]
    assert Cli.parse(["read", "#2"]).read == ["#2"]
    with pytest.raises(SystemExit):
        Cli.parse(["read", "show"])
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    retargeted, ended = [], []
    monkeypatch.setattr(tinker.Jobs, "retarget", staticmethod(lambda arguments: retargeted.append(arguments)))
    monkeypatch.setattr(Commands, "tracker_ended", staticmethod(lambda tracker, ending, took: ended.append(
        (tracker.state["title"], ending, tracker.state["steps"]))))
    monkeypatch.chdir(tmp_path)
    craft(tmp_path)
    fake_ollama.reply(survey_reply(software=False, **{"2": "core"}), when="reading plan")
    fake_ollama.reply(points("Name each function after what it does"), times=4)
    Commands(Cli.parse(["read", "craft.md"])).run()
    assert retargeted == [["read", "#1"]]
    title, ending, steps = ended[0]
    assert title == "Reading #1 The Craft of Small Programs" and ending["status"] == "done"
    assert ('Look again', 'tinker read #1 "what to look for"') in ending["next"]
    assert any(step.startswith("🧭 Chose the chapters to read") for step in steps)
    assert "Time taken" in capsys.readouterr().out
    assert Home.store().entries("read")
    Commands(Cli.parse(["read", "craft.md"])).run()  # stored before: said, and nothing read
    assert len(ended) == 1 and "Already on the reading list as #1" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No reading #9"):
        Commands(Cli.parse(["read", "#9"])).run()

@pytest.mark.spec("RDG-13")
def test_the_reading_list_is_shown_and_cleared(tmp_path, home_store, make_model, fake_ollama, capsys, monkeypatch):
    assert not is_job(["read", "list"]) and not is_job(["read", "show", "#1"])
    show_list()
    assert "Nothing on the reading list" in capsys.readouterr().out
    read_craft(tmp_path, make_model, fake_ollama, software=False)
    stored(craft(tmp_path, "other.md", MARKDOWN + "\nA second edition."))
    capsys.readouterr()
    show_list()
    listed = capsys.readouterr().out
    assert listed.index("#2 The Craft") < listed.index("#1 The Craft") and "4 of 4 chapters read · 3 fact(s)" in listed
    show(1)
    shown = capsys.readouterr().out
    assert "✅ 2. Naming (1 fact(s))" in shown and "📄 5. Index (text only)" in shown
    assert "Small programs stay" in shown
    with pytest.raises(SystemExit, match="Nothing deleted: run tinker clear read #1"):
        tinker.Cli.cleared(["read", "#1"], yes=False)
    tinker.Cli.cleared(["read", "#1"], yes=True)
    assert Readings.get(1) is None and Library.read("read-1") is None and list(Readings.all()) == [2]
    with pytest.raises(SystemExit, match="reading numbers alone"):
        tinker.Cli.cleared(["read", "memory"], yes=True)
    tinker.Cli.cleared(["read"], yes=True)
    assert Readings.all() == {}
