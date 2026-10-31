import io
import json
import zipfile

import pytest
from conftest import MODEL

from tinkerkit import cli, commands, reading, settings
from tinkerkit.cli import Cli, Commands
from tinkerkit.jobs import is_job
from tinkerkit.knowledge import Library
from tinkerkit.model import Ollama
from tinkerkit.principles import Principles
from tinkerkit.project import Tracker
from tinkerkit.reading import (
    Reader,
    Readings,
    book_bytes,
    excerpt,
    in_order,
    index_terms,
    kind_of,
    opened,
    read_phase,
    read_survey,
    show,
    show_list,
    stored,
)
from tinkerkit.settings import READ_KEY_TERMS, READ_MAX_BYTES
from tinkerkit.web import WebPage

CONTAINER = (
    '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)
FOREWORD = (
    "This book teaches how to write small programs that stay easy to change. Its heart is naming and testing."
)
NAMING = (
    "Name each function after what it does, so that its callers read like prose and need no comment at all."
)
TESTING = "Write one test for each behaviour before the code, and run every test after each change you make."
INDEX = "- naming, 3, 7, 12\n- testing, 4–6, 9\n- coupling, 11"
NAV = (
    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
    '<nav epub:type="landmarks"><ol><li><a href="text/cover.xhtml">Cover</a></li></ol></nav>'
    '<nav epub:type="toc"><ol><li><a href="text/foreword.xhtml">Foreword</a></li>'
    '<li><a href="text/part1.xhtml">Part I</a><ol><li><a href="text/part1.xhtml#naming">Naming</a></li>'
    '<li><a href="text/part1.xhtml#testing">Testing</a></li></ol></li>'
    '<li><a href="text/index.xhtml">Index</a></li></ol></nav></body></html>'
)
NCX = (
    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
    '<navPoint id="a"><navLabel><text>Foreword</text></navLabel><content src="text/foreword.xhtml"/></navPoint>'
    '<navPoint id="b"><navLabel><text>Part I</text></navLabel><content src="text/part1.xhtml"/>'
    '<navPoint id="c"><navLabel><text>Naming</text></navLabel><content src="text/part1.xhtml#naming"/></navPoint>'
    '<navPoint id="d"><navLabel><text>Testing</text></navLabel><content src="text/part1.xhtml#testing"/></navPoint>'
    '</navPoint><navPoint id="e"><navLabel><text>Index</text></navLabel><content src="text/index.xhtml"/>'
    "</navPoint></navMap></ncx>"
)
DOCUMENTS = {
    "cover.xhtml": "<h1>The Craft</h1>",
    "foreword.xhtml": f"<h1>Foreword</h1><p>{FOREWORD}</p>",
    "part1.xhtml": "<h1>Part I</h1><p>The basics that every small program needs from its first line.</p>"
    f"<h2 id='naming'>Naming</h2><p>{NAMING}</p><h2 id='testing'>Testing</h2><p>{TESTING}</p>",
    "index.xhtml": "<h1>Index</h1><ul>"
    + "".join(f"<li>{line[2:]}</li>" for line in INDEX.splitlines())
    + "</ul>",
}


def epub(nav=True):
    manifest = "".join(
        f'<item id="d{number}" href="text/{name}" media-type="application/xhtml+xml"/>'
        for number, name in enumerate(DOCUMENTS)
    )
    manifest += (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        if nav
        else '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    )
    spine = "".join(f'<itemref idref="d{number}"/>' for number in range(len(DOCUMENTS)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Craft of Small Programs</dc:title>'
        f"<dc:creator>A. Writer</dc:creator></metadata><manifest>{manifest}</manifest>"
        f"<spine{'' if nav else ' toc="ncx"'}>{spine}</spine></package>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", CONTAINER)
        book.writestr("OEBPS/content.opf", opf)
        book.writestr("OEBPS/nav.xhtml" if nav else "OEBPS/toc.ncx", NAV if nav else NCX)
        for name, body in DOCUMENTS.items():
            book.writestr(
                f"OEBPS/text/{name}",
                f"<html><head><title>x</title><style>p{{}}</style></head><body>{body}</body></html>",
            )
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


@pytest.mark.spec("RDG-2")
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


@pytest.mark.spec("RDG-3")
@pytest.mark.parametrize("nav", [True, False], ids=["epub3-nav", "epub2-ncx"])
def test_an_epub_is_split_where_its_contents_say(nav):
    book = opened(epub(nav), "book.epub")
    assert book.author == "A. Writer"
    assert titles(book) == [
        ("Foreword", "front"),
        ("Part I", "body"),
        ("Naming", "body"),
        ("Testing", "body"),
        ("Index", "index"),
    ]
    naming = book.chapters[2]
    assert NAMING in naming.text and TESTING not in naming.text and "p{}" not in naming.text


@pytest.mark.spec("RDG-3")
def test_an_epub_with_no_usable_contents_is_split_at_its_headings(monkeypatch):
    monkeypatch.setattr(reading.Epub, "contents", lambda self: [])
    book = opened(epub(), "book.epub")
    assert [title for title, _ in titles(book)] == ["Foreword", "Part I", "Naming", "Testing", "Index"]


@pytest.mark.spec("RDG-4")
def test_markdown_and_plain_text_are_split_into_chapters():
    assert titles(opened(MARKDOWN.encode(), "craft.md")) == [
        ("Foreword", "front"),
        ("Naming", "body"),
        ("Stories", "body"),
        ("Testing", "body"),
        ("Index", "index"),
    ]
    plain = (
        f"Contents\nChapter 1 Naming\nChapter 2 Testing\n\nChapter 1 Naming\n{NAMING}\n\n"
        f"Chapter 2 Testing\n{TESTING}"
    )
    assert [title for title, _ in titles(opened(plain.encode(), "craft.txt"))] == [
        "Chapter 1 Naming",
        "Chapter 2 Testing",
    ]
    parts = opened(("A long plain paragraph of words with no heading at all.\n\n" * 800).encode(), "long.txt")
    assert [chapter.title for chapter in parts.chapters][:2] == ["Part 1", "Part 2"]


@pytest.mark.spec("RDG-5")
def test_chapters_have_kinds_and_the_index_names_what_matters():
    assert [
        kind_of(title)
        for title in (
            "Index",
            "Table of Contents",
            "Acknowledgements",
            "Preface",
            "Introduction",
            "3. Naming things",
        )
    ] == ["index", "raw", "raw", "front", "front", "body"]
    assert index_terms(opened(MARKDOWN.encode(), "craft.md")) == [
        ("naming", 3),
        ("testing", 3),
        ("coupling", 1),
    ]


def craft(tmp_path, name="craft.md", text=MARKDOWN):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def survey_reply(software=True, **reads):
    reads = reads or {"4": "core", "2": "supporting"}
    return json.dumps(
        {
            "summary": "Small programs stay easy to change when named well and tested first.",
            "software": software,
            "terms": ["naming", "testing"],
            "chapters": [
                {"chapter": int(number), "read": read, "look_for": f"what chapter {number} teaches"}
                for number, read in reads.items()
            ],
        }
    )


def added(*texts, phase="code"):
    return json.dumps(
        {"deltas": [{"kind": "add", "when": "", "do": text, "phase": phase, "cites": [1]} for text in texts]}
    )


def kept_rules():
    return [principle.do for principle in Principles(None).kept("read-1")]


class Steps:
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


@pytest.mark.spec("RDG-6")
def test_a_source_is_stored_whole_before_anything_is_read(tmp_path, home_store, capsys):
    assert stored(craft(tmp_path)) == (1, True)
    entry = Readings.get(1)
    assert (entry["title"], entry["source"], entry["topic"], entry["survey"]) == (
        "The Craft of Small Programs",
        str(tmp_path / "craft.md"),
        "read-1",
        None,
    )
    assert [(chapter["title"], chapter["state"]) for chapter in entry["chapters"]] == [
        ("Foreword", "unread"),
        ("Naming", "unread"),
        ("Stories", "unread"),
        ("Testing", "unread"),
        ("Index", "raw"),
    ]
    assert entry["chapters"][1]["text"].endswith(NAMING)
    assert stored(craft(tmp_path, "same-text.md")) == (1, False)
    assert "Already on the reading list as #1" in capsys.readouterr().out
    assert stored(craft(tmp_path, "other.md", MARKDOWN + "\nOne more line of the second edition.")) == (
        2,
        True,
    )
    assert list(Readings.all()) == [2, 1]


@pytest.mark.spec("RDG-7")
def test_the_survey_orders_every_chapter_and_skips_none(tmp_path, home_store):
    book = opened(MARKDOWN.encode(), "craft.md")
    survey = read_survey(dict(json.loads(survey_reply()), terms=[f"t{number}" for number in range(30)]), book)
    assert survey["software"] is True and len(survey["terms"]) == READ_KEY_TERMS
    assert survey["order"] == {
        "4": {"read": "core", "look_for": "what chapter 4 teaches"},
        "2": {"read": "supporting", "look_for": "what chapter 2 teaches"},
    }
    assert [chapter.number for chapter in in_order(book.chapters, survey)] == [4, 2, 1, 3, 5]
    odd = read_survey(json.loads(survey_reply(**{"1": "skip"})), book)
    assert odd["order"]["1"]["read"] == "other"
    assert read_survey(dict(json.loads(survey_reply()), software="yes"), book)["software"] is False
    with pytest.raises(ValueError, match="no summary"):
        read_survey({"chapters": []}, book)


def read_craft(tmp_path, make_model, fake_ollama, software=True):
    stored(craft(tmp_path))
    fake_ollama.reply(survey_reply(software), when="reading plan")
    fake_ollama.reply(added("Write a test for each behaviour first", phase="tests"), when="chapter 4")
    fake_ollama.reply(added("Name each function after what it does"), when="chapter 2")
    fake_ollama.reply(added("Small programs stay easy to change", phase="design"), when="chapter 1")
    fake_ollama.reply(added("Write a test for each behaviour first", phase="tests"), when="chapter 3")
    tracker = Steps()
    return Reader(make_model(), tracker).read(1), tracker


@pytest.mark.spec("RDG-8")
def test_every_chapter_is_read_in_order_and_saved_as_it_is_read(
    tmp_path, home_store, make_model, fake_ollama
):
    ending, tracker = read_craft(tmp_path, make_model, fake_ollama)
    assert labels(fake_ollama) == [
        "Read: the reading plan",
        "Read: chapter 4",
        "Read: chapter 2",
        "Read: chapter 1",
        "Read: chapter 3",
    ]
    sent = prompts(fake_ollama)
    assert "1. Foreword (front" in sent[0] and FOREWORD in sent[0] and "testing (3)" in sent[0]
    assert TESTING in sent[1] and "Write a test for each behaviour first" in sent[2]
    the_index = "coupling, 11"
    assert all(the_index not in prompt for prompt in sent[1:])
    topic = Library.read("read-1")
    assert topic["kind"] == "reading" and topic["reading"] == 1 and topic["software"] is True
    assert "facts" not in topic and "rules" not in topic and Library.items({"read-1": topic})
    assert sorted(kept_rules()) == [
        "Name each function after what it does",
        "Small programs stay easy to change",
        "Write a test for each behaviour first",
    ]
    entry = Readings.get(1)
    assert entry
    chapters = entry["chapters"]
    assert {chapter["state"] for chapter in chapters[:4]} == {"read"}
    chapter_3s_repeat_strengthens_chapter_4s = chapters[3]["principles"]
    assert chapters[2]["principles"] == chapter_3s_repeat_strengthens_chapter_4s
    testing = Principles(None).get(chapters[3]["principles"][0])
    assert testing and testing.phase == "tests"
    assert tracker.steps == ["survey", "chapters"]
    assert tracker.outcomes["survey"] == "1 core chapter(s) of 5, read first"
    assert tracker.outcomes["chapters"] == "4 chapter(s), 3 things learned"
    assert ("Chapters read", "4 of 4") in ending["details"]
    assert ending["headline"] == "Read #1 The Craft of Small Programs"
    fake_ollama.calls.clear()
    assert Reader(make_model()).read(1)["details"][0] == ("Read now", "0 chapter(s), 0 principle(s) changed")
    assert fake_ollama.calls == []


@pytest.mark.spec("RDG-8")
def test_a_stopped_reading_goes_on_with_the_chapters_not_read(tmp_path, home_store, make_model, fake_ollama):
    stored(craft(tmp_path))
    fake_ollama.reply(survey_reply(), when="reading plan")
    fake_ollama.reply(added("Write a test for each behaviour first", phase="tests"), when="chapter 4")
    fake_ollama.reply(KeyboardInterrupt(), when="chapter 2")
    with pytest.raises(KeyboardInterrupt):
        Reader(make_model()).read(1)
    assert [chapter["state"] for chapter in Readings.get(1)["chapters"]] == [
        "unread",
        "unread",
        "unread",
        "read",
        "raw",
    ]
    assert kept_rules() == ["Write a test for each behaviour first"]
    fake_ollama.calls.clear()
    for chapter in (2, 1, 3):
        fake_ollama.reply(added(), when=f"chapter {chapter}")
    Reader(make_model()).read(1)
    assert labels(fake_ollama) == ["Read: chapter 2", "Read: chapter 1", "Read: chapter 3"]


@pytest.mark.spec("RDG-8")
def test_a_long_chapter_is_read_in_every_part(tmp_path, home_store, make_model, fake_ollama, monkeypatch):
    long = "\n\n".join(
        f"Paragraph {number} of a long chapter about naming things well." for number in range(40)
    )
    stored(
        craft(
            tmp_path, text=f"# Long\n\n## One\n\n{long}\n\n## Two\n\nA short second chapter, with more words."
        )
    )
    monkeypatch.setattr(reading, "READ_CHAPTER_TEXT_SHARE", 0.05)
    fake_ollama.reply(survey_reply(software=False, **{"1": "core"}), when="reading plan")
    fake_ollama.reply(added(), times=20)
    Reader(make_model()).read(1)
    parts = [label for label in labels(fake_ollama) if label.startswith("Read: chapter 1")]
    assert len(parts) > 1 and parts[:2] == ["Read: chapter 1 part 1", "Read: chapter 1 part 2"]
    shown = "".join(prompts(fake_ollama)[1 : 1 + len(parts)])
    assert "Paragraph 0 " in shown and "Paragraph 39 " in shown


@pytest.mark.spec("RDG-9")
def test_read_again_with_a_focus_looks_where_it_is_asked(
    tmp_path, home_store, make_model, fake_ollama, monkeypatch
):
    read_craft(tmp_path, make_model, fake_ollama)
    fake_ollama.calls.clear()
    monkeypatch.setattr(reading, "READ_FOCUS_CHAPTERS", 2)
    fake_ollama.reply(added("A long program was written three times"), when="chapter 3")
    fake_ollama.reply(added("Run every test after each change", phase="tests"), when="chapter 4")
    ending = Reader(make_model()).read(1, "chapter 3, and when to run every test")
    assert labels(fake_ollama) == ["Read: chapter 3", "Read: chapter 4"]
    assert "Look for" in prompts(fake_ollama)[0]
    assert "chapter 3, and when to run every test" in prompts(fake_ollama)[0]
    assert ("Read now", "2 chapter(s), 2 principle(s) changed") in ending["details"]
    assert ("Kept", f"{len(kept_rules())} principle(s)") in ending["details"]
    assert "Run every test after each change" in kept_rules()


@pytest.mark.spec("RDG-9")
def test_a_focus_excerpt_keeps_the_start_and_the_chunks_that_match():
    paragraphs = (
        ["intro about the book " * 10]
        + [f"filler {number} " * 25 for number in range(6)]
        + ["install with pip " * 10, "run tests with pytest " * 10]
    )
    text = "\n\n".join(paragraphs)
    pieces = excerpt(text, 750, "pip pytest").split("\n\n[…]\n\n")
    assert [piece.split()[0] for piece in pieces] == ["intro", "install", "run"]
    assert excerpt("short chapter", 750, "x") == "short chapter"


@pytest.mark.spec("RDG-10")
def test_a_principle_keeps_its_building_phase_only_from_a_software_source():
    code = {"kind": "add", "do": "Name functions by what they do", "phase": "code"}
    anywhere = {"kind": "add", "do": "Small programs stay easy to change", "phase": "any"}
    strengthen = {"kind": "strengthen", "principle": "p1"}
    assert read_phase(code, software=True) == code
    assert read_phase(anywhere, software=True)["phase"] == "book"
    assert read_phase(code, software=False)["phase"] == "book"
    assert read_phase(strengthen, software=False) == strengthen


@pytest.mark.spec("RDG-10")
def test_what_is_not_about_software_is_kept_for_asking_alone(tmp_path, home_store, make_model, fake_ollama):
    ending, tracker = read_craft(tmp_path, make_model, fake_ollama, software=False)
    assert {principle.phase for principle in Principles(None).kept("read-1")} == {"book"}
    assert "condense" not in tracker.steps
    assert ("Builds", "never use it: it is not about building software") in ending["details"]


@pytest.mark.spec("RDG-1")
def test_a_source_comes_from_a_file_or_a_web_address(tmp_path, monkeypatch):
    path = tmp_path / "craft.md"
    path.write_text("words")
    assert book_bytes(str(path)) == b"words"
    with pytest.raises(SystemExit, match="No file at"):
        book_bytes(str(tmp_path / "missing.epub"))
    asked = []

    def fetch(internet, urls, max_bytes=None):
        asked.append((urls, max_bytes))
        ok = urls[0].endswith("ok")
        return [
            WebPage(urls[0], "application/epub+zip", b"book", [], "", "")
            if ok
            else WebPage.failed(urls[0], "HTTP 404")
        ]

    monkeypatch.setattr(reading.Internet, "__init__", lambda internet: None)
    monkeypatch.setattr(reading.Internet, "fetch", fetch)
    assert book_bytes("https://example.org/ok") == b"book"
    with pytest.raises(SystemExit, match="Could not download https://example.org/gone: HTTP 404"):
        book_bytes("https://example.org/gone")
    assert asked == [
        (["https://example.org/ok"], READ_MAX_BYTES),
        (["https://example.org/gone"], READ_MAX_BYTES),
    ]
    monkeypatch.setattr(reading, "READ_MAX_BYTES", 2)
    with pytest.raises(SystemExit, match="larger than"):
        book_bytes(str(path))


@pytest.mark.spec("RDG-11")
def test_the_readings_find_what_a_source_says(tmp_path, home_store):
    stored(craft(tmp_path))
    assert Readings.about("what does The Craft of Small Programs say?", []) == [1]
    assert Readings.about("anything", [{"group": "read-1"}, {"group": "python"}]) == [1]
    assert Readings.about("anything", [{"group": "python"}]) == []
    found = Readings.passages([1], "when to run every test")
    assert found[0] == {
        "source": "The Craft of Small Programs, chapter 4: Testing",
        "text": f"## Testing\n\n{TESTING}",
    }


@pytest.mark.spec("RDG-12")
def test_tinker_read_stores_then_reads_as_its_own_job(
    tmp_path, measured_model, fake_ollama, monkeypatch, capsys
):
    assert (
        is_job(["read", "book.epub"])
        and is_job(["read", "#2", "retries"])
        and commands.kind_of(["read", "book.epub"]) == "job"
    )
    assert Cli.parse(["read", "~/Books/the", "craft.epub"]).read == ["~/Books/the craft.epub"]
    assert Cli.parse(["read", "#2", '"retries,', 'when"']).read == ["#2", "retries, when"]
    assert Cli.parse(["read", "#2"]).read == ["#2"]
    with pytest.raises(SystemExit):
        Cli.parse(["read", "show"])
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    retargeted, ended = [], []
    monkeypatch.setattr(cli.Jobs, "retarget", staticmethod(lambda arguments: retargeted.append(arguments)))
    monkeypatch.setattr(
        Tracker,
        "end",
        lambda tracker, ending, took: ended.append((tracker.state["title"], ending, tracker.state["steps"])),
    )
    monkeypatch.chdir(tmp_path)
    craft(tmp_path)
    fake_ollama.reply(survey_reply(software=False, **{"2": "core"}), when="reading plan")
    fake_ollama.reply(added("Name each function after what it does"), times=4)
    Commands(Cli.parse(["read", "craft.md"])).run()
    assert retargeted == [["read", "#1"]]
    title, ending, steps = ended[0]
    assert title == "Reading #1 The Craft of Small Programs" and ending["status"] == "done"
    assert ("Look again", 'tinker read #1 "what to look for"') in ending["next"]
    assert any(step.startswith("🧭 Chose the chapters to read") for step in steps)
    assert "Time taken" in capsys.readouterr().out
    stored_before = Cli.parse(["read", "craft.md"])
    Commands(stored_before).run()
    assert len(ended) == 1 and "Already on the reading list as #1" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No reading #9"):
        Commands(Cli.parse(["read", "#9"])).run()


@pytest.mark.spec("RDG-13")
def test_the_reading_list_is_shown_and_cleared(
    tmp_path, home_store, make_model, fake_ollama, capsys, monkeypatch
):
    assert not is_job(["read", "list"]) and not is_job(["read", "show", "#1"])
    show_list()
    assert "Nothing on the reading list" in capsys.readouterr().out
    read_craft(tmp_path, make_model, fake_ollama, software=False)
    stored(craft(tmp_path, "other.md", MARKDOWN + "\nA second edition."))
    capsys.readouterr()
    show_list()
    listed = capsys.readouterr().out
    assert (
        listed.index("#2 The Craft") < listed.index("#1 The Craft")
        and "4 of 4 chapters read · 3 principle(s)" in listed
    )
    show(1)
    shown = capsys.readouterr().out
    assert "✅ 2. Naming (1 principle(s))" in shown and "📄 5. Index (text only)" in shown
    assert "Small programs stay" in shown
    with pytest.raises(SystemExit, match="Nothing deleted: run tinker clear read #1"):
        cli.Cli.cleared(["read", "#1"], yes=False)
    cli.Cli.cleared(["read", "#1"], yes=True)
    assert Readings.get(1) is None and Library.read("read-1") is None and list(Readings.all()) == [2]
    assert Principles(None).all() == []
    with pytest.raises(SystemExit, match="reading numbers alone"):
        cli.Cli.cleared(["read", "memory"], yes=True)
    cli.Cli.cleared(["read"], yes=True)
    assert Readings.all() == {}


@pytest.mark.spec("RDG-13")
def test_the_reading_list_comes_a_page_at_a_time(tmp_path, home_store, capsys, monkeypatch):
    monkeypatch.setattr(settings, "PER_PAGE", 1)
    stored(craft(tmp_path))
    stored(craft(tmp_path, "other.md", MARKDOWN + "\nA second edition."))
    capsys.readouterr()
    show_list()
    first = capsys.readouterr().out
    assert "#2 The Craft" in first and "#1 " not in first and "tinker read list 2 for more" in first
    show_list(2)
    assert "#1 The Craft" in capsys.readouterr().out
    assert Cli.parse(["read", "list", "2"]).read == ["list", "2"]
    with pytest.raises(SystemExit):
        Cli.parse(["read", "list", "two"])
