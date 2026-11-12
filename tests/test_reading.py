import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FOREWORD, MARKDOWN, MODEL, NAMING, TESTING
from test_formats import pdf

from tinkerkit import cli, commands, reading, settings
from tinkerkit.cli import Cli, Commands
from tinkerkit.formats import opened
from tinkerkit.jobs import is_job
from tinkerkit.knowledge import Library
from tinkerkit.model import Models, Ollama, OllamaError
from tinkerkit.principles import Principles
from tinkerkit.project import Tracker
from tinkerkit.reading import (
    Reader,
    Readings,
    Shelf,
    TooLarge,
    answered_inside,
    excerpt,
    heard_again,
    in_order,
    index_terms,
    kept,
    read_phase,
    read_survey,
    show,
    show_list,
    source_bytes,
)
from tinkerkit.settings import READ_KEY_TERMS, READ_MAX_BYTES
from tinkerkit.web import WHOLE, WebPage

PNG = b"\x89PNG\r\n\x1a\n\x00 whiteboard"
OGG = b"OggS\x00\x02 a talk"


@pytest.mark.spec("RDG-5")
def test_the_index_names_what_matters():
    assert index_terms(opened(MARKDOWN.encode(), "craft.md").book()) == [
        ("naming", 3),
        ("testing", 3),
        ("coupling", 1),
    ]


def craft(tmp_path, name="craft.md", text=MARKDOWN):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def second_edition(tmp_path):
    return craft(tmp_path, "other.md", MARKDOWN + "\nA second edition.")


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
    assert kept(craft(tmp_path)) == (1, True)
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
    key = hashlib.sha1(MARKDOWN.encode()).hexdigest()[:16]
    assert (entry["kind"], entry["file"], entry["size"], entry["fingerprint"], entry["learned"]) == (
        "document",
        f"{key}.md",
        len(MARKDOWN.encode()),
        key,
        False,
    )
    assert settings.Home.reading_file(f"{key}.md").read_text() == MARKDOWN
    assert kept(craft(tmp_path, "same-text.md")) == (1, False)
    assert "Already on the reading list as #1" in capsys.readouterr().out
    assert kept(second_edition(tmp_path)) == (2, True)
    assert list(Readings.all()) == [2, 1]


@pytest.mark.spec("CHN-9")
def test_a_file_downloaded_into_the_reading_folder_leaves_no_second_copy(home_store, capsys):
    key = hashlib.sha1(MARKDOWN.encode()).hexdigest()[:16]
    downloaded = settings.Home.reading_file("My Book.md")
    downloaded.write_text(MARKDOWN)
    assert kept(str(downloaded))[0] == 1
    assert sorted(path.name for path in downloaded.parent.iterdir()) == [f"{key}.md"]
    downloaded.write_text(MARKDOWN)
    assert kept(str(downloaded)) == (1, False)
    assert sorted(path.name for path in downloaded.parent.iterdir()) == [f"{key}.md"]
    assert kept(str(settings.Home.reading_file(f"{key}.md"))) == (1, False)
    assert settings.Home.reading_file(f"{key}.md").read_text() == MARKDOWN


@pytest.mark.spec("RDG-2")
def test_a_file_tinker_cannot_read_is_kept_with_no_chapters(tmp_path, home_store, capsys):
    kept_with_no_chapters(
        written(tmp_path, "x.bin", b"\x00\x01binary"), capsys, "x.bin is not a file Tinker can read"
    )
    assert Readings.get(1)["title"] == "x"


def kept_with_no_chapters(path, capsys, reason, number=1):
    assert kept(path) == (number, True)
    entry = Readings.get(number)
    assert entry["chapters"] == []
    assert settings.Home.reading_file(entry["file"]).read_bytes() == Path(path).read_bytes()
    assert re.search(rf"#{number} \S.* kept with no chapters: {reason}", capsys.readouterr().out)


def sensing(monkeypatch, senses=("vision",), hearing="hears", ffmpeg="/usr/bin/ffmpeg"):
    asked, converted = [], []

    def quick_reply(messages, tokens):
        asked.append(messages)
        return f"What the model read, call {len(asked)}."

    def run(command, **options):
        converted.append(command)
        return subprocess.CompletedProcess(command, 0, b"RIFF16k")

    monkeypatch.setattr(Models, "senses", staticmethod(lambda: senses))
    monkeypatch.setattr(Models, "hearing", staticmethod(lambda: hearing))
    monkeypatch.setattr(Models, "quick_reply", staticmethod(quick_reply))
    monkeypatch.setattr(Models, "transcript", staticmethod(lambda wav: f"Transcript of {wav.decode()}."))
    monkeypatch.setattr(shutil, "which", lambda name: ffmpeg if name == "ffmpeg" else None)
    monkeypatch.setattr(subprocess, "run", run)
    return asked, converted


def written(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.spec("RDG-8")
def test_an_image_is_described_by_the_model_that_sees_it(tmp_path, home_store, monkeypatch):
    asked, _ = sensing(monkeypatch)
    assert kept(written(tmp_path, "whiteboard.png", PNG)) == (1, True)
    [messages] = asked
    assert messages[-1]["images"] == [base64.b64encode(PNG).decode()]
    assert [(c["title"], c["text"]) for c in Readings.get(1)["chapters"]] == [
        ("whiteboard", "What the model read, call 1.")
    ]


@pytest.mark.spec("CNV-25")
def test_a_question_inside_a_reading_is_answered_from_its_passages(tmp_path, home_store, monkeypatch):
    asked, _ = sensing(monkeypatch)
    kept(craft(tmp_path))
    passage = Readings.passages([1], "naming")[0]
    assert answered_inside(1, "What about naming?") == "What the model read, call 1."
    [[system, user]] = asked
    assert system["role"] == "system" and Readings.get(1)["title"] in system["content"]
    assert passage["text"] in user["content"] and "What about naming?" in user["content"]
    assert "images" not in user


@pytest.mark.spec("CNV-25")
def test_a_question_inside_an_image_shows_it_only_to_a_model_that_sees(tmp_path, home_store, monkeypatch):
    asked, _ = sensing(monkeypatch)
    kept(written(tmp_path, "whiteboard.png", PNG))
    answered_inside(1, "What is drawn?")
    assert asked[-1][-1]["images"] == [base64.b64encode(PNG).decode()]
    monkeypatch.setattr(Models, "senses", staticmethod(lambda: ()))
    answered_inside(1, "What is drawn?")
    assert "images" not in asked[-1][-1]


@pytest.mark.spec("CNV-25")
def test_a_reading_with_chapters_is_entered_by_its_number_or_the_source_just_kept(tmp_path, home_store):
    source = craft(tmp_path)
    kept(source)
    title = Readings.get(1)["title"]
    assert Shelf().entered(["#1"]) == (1, title)
    assert Shelf().entered([source]) == (1, title)
    assert Shelf().entered(["#1", "learn"]) is None
    assert Shelf().entered(["#2"]) is None
    assert Shelf().entered([str(tmp_path / "other.md")]) is None
    assert Shelf().exists(1) and not Shelf().exists(2)


@pytest.mark.spec("CNV-25")
def test_a_reading_kept_with_no_chapters_is_not_entered(tmp_path, home_store, capsys):
    path = written(tmp_path, "x.bin", b"\x00\x01binary")
    kept(path)
    assert Shelf().entered(["#1"]) is None
    assert Shelf().entered([path]) is None


@pytest.mark.spec("RDG-8")
def test_an_image_without_a_model_that_sees_is_kept_with_no_chapters(
    tmp_path, home_store, monkeypatch, capsys
):
    sensing(monkeypatch, senses=())
    kept_with_no_chapters(
        written(tmp_path, "whiteboard.png", PNG),
        capsys,
        "whiteboard.png is an image: .*needs a model that sees",
    )


@pytest.mark.spec("RDG-7")
def test_a_scan_is_read_by_the_model_that_sees_one_call_a_page(tmp_path, home_store, monkeypatch):
    asked, _ = sensing(monkeypatch)
    images = {
        0: (b"\xff\xd8\xff\xe0 one", b"DCTDecode", False),
        1: (b"\xff\xd8\xff\xe0 two", b"DCTDecode", False),
    }
    page = (b"q /Im1 Do Q", False)
    assert kept(written(tmp_path, "scan.pdf", pdf([page, page], images=images))) == (1, True)
    assert [messages[-1]["images"] for messages in asked] == [
        [base64.b64encode(b"\xff\xd8\xff\xe0 one").decode()],
        [base64.b64encode(b"\xff\xd8\xff\xe0 two").decode()],
    ]
    text = "\n".join(chapter["text"] for chapter in Readings.get(1)["chapters"])
    assert "call 1" in text and "call 2" in text


@pytest.mark.spec("RDG-7")
def test_a_scan_is_kept_with_no_chapters_without_a_model_that_sees_or_a_page_image(
    tmp_path, home_store, monkeypatch, capsys
):
    sensing(monkeypatch, senses=())
    images = {0: (b"\xff\xd8\xff\xe0 one", b"DCTDecode", False)}
    scan = written(tmp_path, "scan.pdf", pdf([(b"q /Im1 Do Q", False)], images=images))
    kept_with_no_chapters(scan, capsys, "scan.pdf is a scan: .*needs a model that sees")
    blank = written(tmp_path, "blank.pdf", pdf([(b"q /Im1 Do Q", False)]))
    kept_with_no_chapters(blank, capsys, "blank.pdf has no text and no page image", 2)


@pytest.mark.spec("RDG-8")
def test_a_recording_is_turned_into_16k_mono_wav_and_transcribed(tmp_path, home_store, monkeypatch):
    _, converted = sensing(monkeypatch)
    assert kept(written(tmp_path, "talk.ogg", OGG)) == (1, True)
    [command] = converted
    assert command[0] == "/usr/bin/ffmpeg" and " ".join(command).count("-ar 16000 -ac 1") == 1
    assert [(c["title"], c["text"]) for c in Readings.get(1)["chapters"]] == [
        ("talk", "Transcript of RIFF16k.")
    ]
    assert (Readings.get(1)["kind"], Readings.get(1)["file"].endswith(".ogg")) == ("audio", True)
    assert kept(written(tmp_path, "again.OGG", OGG)) == (1, False)
    assert len(converted) == 1


@pytest.mark.spec("RDG-8")
@pytest.mark.parametrize(
    ("platform", "install"), [("darwin", "brew install ffmpeg"), ("linux", "sudo apt install ffmpeg")]
)
def test_a_recording_without_ffmpeg_is_kept_with_no_chapters_and_says_how_to_install_it(
    tmp_path, home_store, monkeypatch, capsys, platform, install
):
    sensing(monkeypatch, ffmpeg=None)
    monkeypatch.setattr(sys, "platform", platform)
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG), capsys, f"talk.ogg is a recording: audio needs ffmpeg.*{install}"
    )


@pytest.mark.spec("RDG-8")
def test_a_recording_ffmpeg_or_the_transcript_fails_on_is_kept_with_no_chapters(
    tmp_path, home_store, monkeypatch, capsys
):
    sensing(monkeypatch)

    def failing_run(command, **options):
        raise subprocess.CalledProcessError(1, command, b"", b"Probing stream 0\nInvalid data found\n\n  \n")

    monkeypatch.setattr(subprocess, "run", failing_run)
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG),
        capsys,
        "talk.ogg is a recording: ffmpeg failed: Invalid data found",
    )
    sensing(monkeypatch)

    def failing_transcript(wav):
        raise OllamaError("the model that hears is not answering")

    monkeypatch.setattr(Models, "transcript", staticmethod(failing_transcript))
    kept_with_no_chapters(
        written(tmp_path, "other.ogg", OGG + b"2"),
        capsys,
        "other.ogg is a recording: the model that hears",
        2,
    )


@pytest.mark.spec("RDG-8")
def test_a_recording_ffmpeg_fails_on_silently_is_told_its_exit_code(
    tmp_path, home_store, monkeypatch, capsys
):
    sensing(monkeypatch)

    def failing_run(command, **options):
        raise subprocess.CalledProcessError(69, command, b"", b"\n  \n")

    monkeypatch.setattr(subprocess, "run", failing_run)
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG), capsys, "talk.ogg is a recording: ffmpeg failed with exit code 69"
    )


@pytest.mark.spec("RDG-8")
def test_read_of_a_recording_kept_with_no_chapters_tries_its_transcript_again(
    tmp_path, home_store, monkeypatch, capsys
):
    sensing(monkeypatch, ffmpeg=None)
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG), capsys, "talk.ogg is a recording: audio needs ffmpeg"
    )
    heard_again(1)
    assert Readings.get(1)["chapters"] == []
    assert re.search(
        r"#1 \S.* kept with no chapters: talk.ogg is a recording: audio needs ffmpeg", capsys.readouterr().out
    )
    _, converted = sensing(monkeypatch)
    heard_again(1)
    assert [(c["title"], c["text"], c["state"]) for c in Readings.get(1)["chapters"]] == [
        ("talk", "Transcript of RIFF16k.", "unread")
    ]
    heard_again(1)
    assert len(converted) == 1


@pytest.mark.spec("RDG-8")
def test_read_does_not_hear_again_an_entry_that_is_not_a_recording_kept_empty(
    tmp_path, home_store, monkeypatch
):
    _, converted = sensing(monkeypatch)
    kept(craft(tmp_path))
    before = Readings.get(1)
    heard_again(1)
    assert Readings.get(1) == before and converted == []


@pytest.mark.spec("RDG-8")
def test_a_recording_without_a_model_that_hears_is_kept_with_no_chapters(
    tmp_path, home_store, monkeypatch, capsys
):
    pulled = offering(monkeypatch, tty=False)
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG), capsys, "talk.ogg is a recording: no model that hears was got"
    )
    assert pulled == []


def offering(monkeypatch, tty=True, answer="y", failure=None):
    sensing(monkeypatch, hearing=None)
    pulled = []

    def pull(name):
        pulled.append(name)
        if failure:
            raise OllamaError(failure)

    monkeypatch.setattr(Models, "audio_offer", staticmethod(lambda: "gemma3n:e2b"))
    monkeypatch.setattr(Models, "audio_question", staticmethod(lambda name: f"Get {name}?"))
    monkeypatch.setattr(Ollama, "pull", staticmethod(pull))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr("builtins.input", lambda question: answer)
    return pulled


@pytest.mark.spec("MOD-30")
def test_a_yes_to_the_offer_pulls_the_model_then_transcribes(tmp_path, home_store, monkeypatch):
    pulled = offering(monkeypatch)
    assert kept(written(tmp_path, "talk.ogg", OGG)) == (1, True)
    assert pulled == ["gemma3n:e2b"]
    assert [c["text"] for c in Readings.get(1)["chapters"]] == ["Transcript of RIFF16k."]


@pytest.mark.spec("MOD-30")
def test_a_no_to_the_offer_never_pulls_and_keeps_it_with_no_chapters(
    tmp_path, home_store, monkeypatch, capsys
):
    pulled = offering(monkeypatch, answer="n")
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG), capsys, "talk.ogg is a recording: no model that hears was got"
    )
    assert pulled == []


@pytest.mark.spec("MOD-30")
def test_a_failed_pull_is_said_and_keeps_it_with_no_chapters(tmp_path, home_store, monkeypatch, capsys):
    offering(monkeypatch, failure="Could not get gemma3n:e2b: disk full")
    kept_with_no_chapters(
        written(tmp_path, "talk.ogg", OGG),
        capsys,
        "talk.ogg is a recording: Could not get gemma3n:e2b: disk full",
    )


@pytest.mark.spec("RDG-9")
def test_the_survey_orders_every_chapter_and_skips_none(tmp_path, home_store):
    book = opened(MARKDOWN.encode(), "craft.md").book()
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
    kept(craft(tmp_path))
    fake_ollama.reply(survey_reply(software), when="reading plan")
    fake_ollama.reply(added("Write a test for each behaviour first", phase="tests"), when="chapter 4")
    fake_ollama.reply(added("Name each function after what it does"), when="chapter 2")
    fake_ollama.reply(added("Small programs stay easy to change", phase="design"), when="chapter 1")
    fake_ollama.reply(added("Write a test for each behaviour first", phase="tests"), when="chapter 3")
    tracker = Steps()
    return Reader(make_model(), tracker).read(1), tracker


@pytest.mark.spec("RDG-10")
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


@pytest.mark.spec("RDG-10")
def test_a_stopped_reading_goes_on_with_the_chapters_not_read(tmp_path, home_store, make_model, fake_ollama):
    kept(craft(tmp_path))
    fake_ollama.reply(survey_reply(), when="reading plan")
    fake_ollama.reply(added("Write a test for each behaviour first", phase="tests"), when="chapter 4")
    fake_ollama.reply(KeyboardInterrupt(), when="chapter 2")
    with pytest.raises(KeyboardInterrupt):
        Reader(make_model()).read(1)
    assert [chapter["state"] for chapter in Readings.get(1)["chapters"]] == ["unread"] * 3 + ["read", "raw"]
    assert kept_rules() == ["Write a test for each behaviour first"]
    fake_ollama.calls.clear()
    for chapter in (2, 1, 3):
        fake_ollama.reply(added(), when=f"chapter {chapter}")
    Reader(make_model()).read(1)
    assert labels(fake_ollama) == ["Read: chapter 2", "Read: chapter 1", "Read: chapter 3"]


@pytest.mark.spec("RDG-10")
def test_a_long_chapter_is_read_in_every_part(tmp_path, home_store, make_model, fake_ollama, monkeypatch):
    long = "\n\n".join(
        f"Paragraph {number} of a long chapter about naming things well." for number in range(40)
    )
    text = f"# Long\n\n## One\n\n{long}\n\n## Two\n\nA short second chapter, with more words."
    kept(craft(tmp_path, text=text))
    monkeypatch.setattr(reading, "READ_CHAPTER_TEXT_SHARE", 0.05)
    fake_ollama.reply(survey_reply(software=False, **{"1": "core"}), when="reading plan")
    fake_ollama.reply(added(), times=20)
    Reader(make_model()).read(1)
    parts = [label for label in labels(fake_ollama) if label.startswith("Read: chapter 1")]
    assert len(parts) > 1 and parts[:2] == ["Read: chapter 1 part 1", "Read: chapter 1 part 2"]
    shown = "".join(prompts(fake_ollama)[1 : 1 + len(parts)])
    assert "Paragraph 0 " in shown and "Paragraph 39 " in shown


@pytest.mark.spec("RDG-11")
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


@pytest.mark.spec("RDG-11")
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


@pytest.mark.spec("RDG-12")
def test_a_principle_keeps_its_building_phase_only_from_a_software_source():
    code = {"kind": "add", "do": "Name functions by what they do", "phase": "code"}
    anywhere = {"kind": "add", "do": "Small programs stay easy to change", "phase": "any"}
    strengthen = {"kind": "strengthen", "principle": "p1"}
    assert read_phase(code, software=True) == code
    assert read_phase(anywhere, software=True)["phase"] == "book"
    assert read_phase(code, software=False)["phase"] == "book"
    assert read_phase(strengthen, software=False) == strengthen


@pytest.mark.spec("RDG-12")
def test_what_is_not_about_software_is_kept_for_asking_alone(tmp_path, home_store, make_model, fake_ollama):
    ending, tracker = read_craft(tmp_path, make_model, fake_ollama, software=False)
    assert {principle.phase for principle in Principles(None).kept("read-1")} == {"book"}
    assert ("Builds", "never use it: it is not about building software") in ending["details"]


@pytest.mark.spec("RDG-1")
def test_a_source_comes_from_a_file_or_a_web_address(tmp_path, monkeypatch):
    path = tmp_path / "craft.md"
    path.write_text("words")
    assert source_bytes(str(path)) == b"words"
    with pytest.raises(SystemExit, match="No file at"):
        source_bytes(str(tmp_path / "missing.epub"))
    asked = []

    def fetch(internet, urls, max_bytes=None):
        asked.append((urls, max_bytes))
        if urls[0].endswith("ok"):
            return [WebPage(urls[0], "application/epub+zip", b"book", [], "", "")]
        if urls[0].endswith("big"):
            return [WebPage.failed(urls[0], f"larger than {max_bytes} bytes")]
        return [WebPage.failed(urls[0], "HTTP 404")]

    monkeypatch.setattr(reading.Internet, "__init__", lambda internet: None)
    monkeypatch.setattr(reading.Internet, "fetch", fetch)
    assert source_bytes("https://example.org/ok") == b"book"
    with pytest.raises(SystemExit, match="Could not download https://example.org/gone: HTTP 404"):
        source_bytes("https://example.org/gone")
    assert asked == [
        (["https://example.org/ok"], READ_MAX_BYTES),
        (["https://example.org/gone"], READ_MAX_BYTES),
    ]
    monkeypatch.setattr(reading, "READ_MAX_BYTES", 2)
    with pytest.raises(TooLarge, match="larger than"):
        source_bytes(str(path))
    with pytest.raises(TooLarge, match="Could not download https://example.org/big: larger than 2 bytes"):
        source_bytes("https://example.org/big")
    assert source_bytes(str(path), beyond=True) == b"words"
    source_bytes("https://example.org/ok", beyond=True)
    assert asked[-1] == (["https://example.org/ok"], WHOLE)


@pytest.mark.spec("RDG-1")
def test_a_source_too_large_is_kept_only_when_a_yes_answers_the_one_question(
    tmp_path, home_store, monkeypatch, capsys
):
    path = craft(tmp_path)
    monkeypatch.setattr(reading, "READ_MAX_BYTES", 2)
    questions, answers = [], [False, True]
    monkeypatch.setattr(reading.say, "agreed", lambda question: questions.append(question) or answers.pop(0))
    with pytest.raises(TooLarge, match="nothing read"):
        Commands(Cli.parse(["read", path])).run()
    assert len(questions) == 1 and "keep it anyway" in questions[0] and Readings.all() == {}
    Commands(Cli.parse(["read", path])).run()
    assert len(questions) == 2 and list(Readings.all()) == [1]
    assert "#1 (" in capsys.readouterr().out


@pytest.mark.spec("RDG-13")
def test_the_readings_find_what_a_source_says(tmp_path, home_store):
    kept(craft(tmp_path))
    assert Readings.about("what does The Craft of Small Programs say?", []) == [1]
    assert Readings.about("anything", [{"group": "read-1"}, {"group": "python"}]) == [1]
    assert Readings.about("anything", [{"group": "python"}]) == []
    found = Readings.passages([1], "when to run every test")
    assert found[0] == {
        "source": "The Craft of Small Programs, chapter 4: Testing",
        "text": f"## Testing\n\n{TESTING}",
    }


@pytest.mark.spec("RDG-14")
def test_tinker_read_keeps_and_read_learn_is_its_own_job(
    tmp_path, measured_model, fake_ollama, monkeypatch, capsys
):
    assert not is_job(["read", "book.epub"]) and not is_job(["read", "#2"])
    assert is_job(["read", "#2", "learn"]) and commands.kind_of(["read", "#2", "learn", "retries"]) == "job"
    assert Cli.parse(["read", "~/Books/the", "craft.epub"]).read == ["~/Books/the craft.epub"]
    assert Cli.parse(["read", "#2", "learn", '"retries,', 'when"']).read == ["#2", "learn", "retries, when"]
    assert Cli.parse(["read", "#2", "learn"]).read == ["#2", "learn"]
    assert Cli.parse(["read", "#2"]).read == ["#2"]
    for wrong in (["read", "show"], ["read", "#2", "retries"]):
        with pytest.raises(SystemExit):
            Cli.parse(wrong)
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
    Commands(Cli.parse(["read", "craft.md"])).run()
    kept_out = capsys.readouterr().out
    assert re.search(r"#1 \(\w+\) The Craft of Small Programs.*: \d+ chapter", kept_out)
    assert "ask about it in a chat, or read #1 learn" in kept_out and not ended and not retargeted
    Commands(Cli.parse(["read", "#1"])).run()
    assert "#1 The Craft of Small Programs" in capsys.readouterr().out and not ended
    fake_ollama.reply("medium")
    fake_ollama.reply(survey_reply(software=False, **{"2": "core"}), when="reading plan")
    fake_ollama.reply(added("Name each function after what it does"), times=4)
    Commands(Cli.parse(["read", "#1", "learn"])).run()
    title, ending, steps = ended[0]
    assert title == "Reading #1 The Craft of Small Programs" and ending["status"] == "done"
    assert ("Look again", 'tinker read #1 learn "what to look for"') in ending["next"]
    assert any(step.startswith("🧭 Chose the chapters to read") for step in steps)
    assert "Time taken" in capsys.readouterr().out and not retargeted
    Commands(Cli.parse(["read", "craft.md"])).run()
    again = capsys.readouterr().out
    assert len(ended) == 1 and "Already on the reading list as #1 (" in again and "read #1 learn" in again
    with pytest.raises(SystemExit, match="No reading #9"):
        Commands(Cli.parse(["read", "#9", "learn"])).run()


@pytest.mark.spec("RDG-14")
def test_read_learn_needs_a_reading_with_chapters(tmp_path, home_store, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    written(tmp_path, "x.bin", b"\x00\x01binary")
    Commands(Cli.parse(["read", "x.bin"])).run()
    with pytest.raises(SystemExit, match="#1 x has no chapters to learn from"):
        Commands(Cli.parse(["read", "#1", "learn"])).run()


@pytest.mark.spec("RDG-15")
def test_the_reading_list_is_shown_and_cleared(
    tmp_path, home_store, make_model, fake_ollama, capsys, monkeypatch
):
    assert not is_job(["read", "list"]) and not is_job(["read", "show", "#1"])
    show_list()
    assert "Nothing on the reading list" in capsys.readouterr().out
    read_craft(tmp_path, make_model, fake_ollama, software=False)
    kept(second_edition(tmp_path))
    capsys.readouterr()
    Readings.save(dict(Readings.get(1), learned="2026-11-12 10:00"))
    show_list()
    listed = capsys.readouterr().out
    first, second = (re.search(rf"#{number} 📄 The Craft.*", listed)[0] for number in (1, 2))
    assert listed.index(second) < listed.index(first) and "4 of 4 chapters read · 3 principle(s)" in first
    assert " B · " in first and first.endswith("✓") and not second.endswith("✓")
    show(1)
    shown = capsys.readouterr().out
    assert "✅ 2. Naming (1 principle(s))" in shown and "📄 5. Index (text only)" in shown
    assert "Small programs stay" in shown and "📄 document · " in shown and "craft.md" in shown
    assert "tinker read rename #1 TITLE" in shown and "tinker read forget #1" in shown
    file = settings.Home.reading_file(Readings.get(1)["file"])
    assert file.exists()
    with pytest.raises(SystemExit, match="Nothing deleted: run tinker clear read #1"):
        cli.Cli.cleared(["read", "#1"], yes=False)
    cli.Cli.cleared(["read", "#1"], yes=True)
    assert Readings.get(1) is None and Library.read("read-1") is None and list(Readings.all()) == [2]
    assert Principles(None).all() == [] and not file.exists()
    with pytest.raises(SystemExit, match="reading numbers alone"):
        cli.Cli.cleared(["read", "memory"], yes=True)
    cli.Cli.cleared(["read"], yes=True)
    assert Readings.all() == {}


@pytest.mark.spec("RDG-15")
def test_a_reading_shows_the_day_it_was_added_and_says_when_it_has_no_summary(
    tmp_path, home_store, make_model, fake_ollama, capsys
):
    read_craft(tmp_path, make_model, fake_ollama, software=False)
    kept(second_edition(tmp_path))
    Readings.save(dict(Readings.get(1), added="2026-11-12 10:00"))
    Readings.save(dict(Readings.get(2), added="2026-11-11", survey=None))
    capsys.readouterr()
    show_list()
    listed = capsys.readouterr().out
    assert "added 2026-11-12" in listed and "added 2026-11-11" in listed and "10:00" not in listed
    show(1)
    shown = capsys.readouterr().out
    assert "added 2026-11-12:" in shown and "10:00" not in shown and "No summary yet" not in shown
    show(2)
    shown = capsys.readouterr().out
    assert "added 2026-11-11:" in shown
    assert "📝 No summary yet: tinker read #2 learn writes one" in shown


@pytest.mark.spec("RDG-15")
def test_a_reading_is_renamed_and_forgotten(tmp_path, home_store, capsys, monkeypatch):
    kept(craft(tmp_path))
    kept(second_edition(tmp_path))
    assert not is_job(["read", "rename", "#1", "Small"]) and not is_job(["read", "forget", "#1"])
    assert Cli.parse(["read", "rename", "#1", "Small", "Craft"]).read == ["rename", "#1", "Small Craft"]
    assert Cli.parse(["read", "forget", "#1", "#2"]).read == ["forget", "#1", "#2"]
    for wrong in (
        ["read", "rename", "#1"],
        ["read", "rename", "one", "x"],
        ["read", "forget"],
        ["read", "forget", "x"],
    ):
        with pytest.raises(SystemExit):
            Cli.parse(wrong)
    Commands(Cli.parse(["read", "rename", "#1", "Small", "Craft"])).run()
    assert Readings.get(1)["title"] == "Small Craft" and "#1 Small Craft" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No reading #9"):
        Commands(Cli.parse(["read", "rename", "#9", "x"])).run()
    file = settings.Home.reading_file(Readings.get(1)["file"])
    with pytest.raises(SystemExit, match="Nothing deleted: run tinker clear read #1"):
        Commands(Cli.parse(["read", "forget", "#1"])).run()
    Commands(Cli.parse(["read", "forget", "#1", "--yes"])).run()
    assert Readings.get(1) is None and not file.exists() and list(Readings.all()) == [2]


@pytest.mark.spec("RDG-15")
def test_an_old_reading_without_kind_or_file_is_listed_and_forgotten(tmp_path, home_store, capsys):
    kept(craft(tmp_path))
    old = {key: value for key, value in Readings.get(1).items() if key not in ("kind", "file", "size")}
    Readings.save(old)
    show_list()
    assert "#1 📕 The Craft" in capsys.readouterr().out
    show(1)
    assert "📕 book" in capsys.readouterr().out
    assert kept(craft(tmp_path, "same-text.md")) == (1, False)
    assert "Already on the reading list as #1 (book)" in capsys.readouterr().out
    reading.forgotten([1], yes=True)
    assert Readings.get(1) is None


@pytest.mark.spec("RDG-15")
def test_the_reading_list_comes_a_page_at_a_time(tmp_path, home_store, capsys, monkeypatch):
    monkeypatch.setattr(settings, "PER_PAGE", 1)
    kept(craft(tmp_path))
    kept(second_edition(tmp_path))
    capsys.readouterr()
    show_list()
    first = capsys.readouterr().out
    assert "#2 📄 The Craft" in first and "#1 " not in first and "tinker read list 2 for more" in first
    show_list(2)
    assert "#1 📄 The Craft" in capsys.readouterr().out
    assert Cli.parse(["read", "list", "2"]).read == ["list", "2"]
    with pytest.raises(SystemExit):
        Cli.parse(["read", "list", "two"])


@pytest.mark.spec("RDG-15")
def test_in_a_chat_the_reading_list_marks_the_one_the_person_is_inside(tmp_path, home_store, capsys):
    kept(craft(tmp_path))
    kept(second_edition(tmp_path))
    capsys.readouterr()
    show_list(inside=1)
    lines = capsys.readouterr().out.splitlines()
    marked = [line for line in lines if "← inside" in line]
    assert len(marked) == 1 and "#1 " in marked[0] and marked[0].endswith(" ← inside")
    show_list()
    assert "← inside" not in capsys.readouterr().out
