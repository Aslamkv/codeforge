import ast
import io
import os
import shutil
import sys
from pathlib import Path

import pytest
from conftest import ACCENT, DIM, RESET, Terminal

from tinkerkit.messages import Message, Messages, human_duration, say
from tinkerkit.project import Tracker, project_store
from tinkerkit.store import Log

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.spec("MSG-1")
def test_each_kind_says_its_words_after_its_icon(capsys):
    say.done("Tests written")
    say.warn("No network")
    say.fail("Could not open it")
    say.retry("Asking again")
    say.skip("Nothing found")
    say.stop("Stopped")
    say.tip("tinker memory")
    say.item("a fact")
    say.step("Searching…", "🔎")
    say.info("A fact")
    say.text("PROJECT  STATUS")
    assert capsys.readouterr().out.splitlines() == [
        "✅ Tests written",
        "⚠️  No network",
        "❌ Could not open it",
        "🔁 Asking again",
        "➖ Nothing found",
        "🛑 Stopped",
        "💡 tinker memory",
        "• a fact",
        "🔎 Searching…",
        "A fact",
        "PROJECT  STATUS",
    ]


@pytest.mark.spec("MSG-2")
def test_sections_nest_and_a_top_title_stands_apart(capsys):
    with say.part("Learning Flask", "📚"):
        say.step("Searching the web…", "🔎")
        with say.section():
            say.done("Read 3 pages")
    say.summary("Learned Flask", [("🧩", "Facts kept", "12")])
    say.took("Spec written", 64)
    assert capsys.readouterr().out.splitlines() == [
        "",
        "📚 Learning Flask",
        "   🔎 Searching the web…",
        "      ✅ Read 3 pages",
        "",
        "✨ Learned Flask",
        "   🧩 Facts kept: 12",
        "✅ Spec written (1m 4s)",
    ]


@pytest.mark.spec("MSG-3")
def test_followers_are_given_each_message_as_data(capsys):
    heard = []
    say.follow(heard.append)
    a_follower_that_fails = lambda message: 1 / 0
    say.follow(a_follower_that_fails)
    with say.section():
        say.warn("No network")
    assert heard == [Message("warn", "No network", 1, "⚠️")] and "No network" in capsys.readouterr().out


@pytest.mark.spec("MSG-3")
def test_a_tracker_keeps_what_is_said_as_its_latest_and_in_the_output_log(home_store, project_dir):
    tracker = Tracker("calc")
    store = project_store(project_dir)
    tracker.project(store, "abc123", project_dir)
    say.follow(tracker.heard)
    with say.section():
        say.done("Wrote calc.py (12 lines)")
    a_terminal_clock = "Writing calc.py… 12s"
    say.live(a_terminal_clock)
    assert tracker.state["latest"] == "✅ Wrote calc.py (12 lines)" and "live" not in tracker.state
    assert [entry["line"] for _, entry in store.entries("output")] == ["   ✅ Wrote calc.py (12 lines)"]


@pytest.mark.spec("MSG-3")
def test_what_a_command_works_on_its_tally_and_what_it_found_reach_the_tracker(home_store, capsys):
    tracker = Tracker("reading")
    say.follow(tracker.heard)
    with say.working_on("Reading chapter 1, Habits (1 of 2)"):
        assert tracker.state["working"] == "Reading chapter 1, Habits (1 of 2)"
    say.tally("1/2 chapters · 4 things learned")
    say.found("Small habits compound like interest")
    state = tracker.state
    assert state["working"] == "" and state["tally"] == "1/2 chapters · 4 things learned"
    assert state["found"] == "Small habits compound like interest"
    assert capsys.readouterr().out == "💡 Small habits compound like interest\n"


@pytest.mark.spec("MSG-4")
def test_the_live_line_is_drawn_on_a_terminal_only(monkeypatch):
    piped, heard = io.StringIO(), []
    monkeypatch.setattr(sys, "__stdout__", piped)
    say.follow(heard.append)
    say.live("thinking… 12s")
    say.end_live()
    assert piped.getvalue() == "" and heard[0].kind == "live" and heard[0].text == "thinking… 12s"


@pytest.fixture
def terminal(monkeypatch):
    terminal = Terminal()
    monkeypatch.setattr(sys, "__stdout__", terminal)
    monkeypatch.setenv("NO_COLOR", "1")
    return terminal


@pytest.mark.spec("MSG-4")
def test_on_a_terminal_the_next_message_clears_the_live_line(terminal, clock, capsys):
    say.live("Writing the spec")
    clock[0] += 64
    say.live("Writing the spec")
    say.done("Answered")
    assert terminal.getvalue().startswith(
        "\r   ✻ Tinkering… (0s) · Writing the spec\033[K\r   ✶ Tinkering… (1m 4s) · Writing the spec\033[K"
    )
    assert terminal.getvalue().endswith("\r\033[K") and capsys.readouterr().out == "✅ Answered\n"


@pytest.mark.spec("MSG-4")
def test_the_live_line_counts_from_when_the_work_began(terminal, clock):
    say.now_on("Writing tests")
    clock[0] += 12
    with say.working_on("Writing calc.py"):
        clock[0] += 3
        say.live("Writing calc.py")
    say.now_on("")
    clock[0] += 30
    say.now_on("Running tests")
    drawn = terminal.getvalue().split("\033[K")
    assert drawn[2] == "\r   ✳ Tinkering… (15s) · Writing calc.py"
    assert drawn[-2] == "\r   ✳ Tinkering… (0s) · Running tests"


@pytest.mark.spec("MSG-4")
@pytest.mark.parametrize(
    "colorterm,accent",
    [("truecolor", "\033[38;2;70;130;180m"), ("24bit", "\033[38;2;70;130;180m"), ("", ACCENT)],
)
def test_the_live_star_is_steel_blue_and_what_goes_on_is_dim(terminal, clock, monkeypatch, colorterm, accent):
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("COLORTERM", colorterm)
    say.live("Writing tests")
    assert terminal.getvalue() == f"\r   {accent}✻{RESET} Tinkering… (0s){DIM} · Writing tests{RESET}\033[K"


@pytest.mark.spec("MSG-4")
def test_the_live_line_is_cut_to_the_terminal_width_by_what_shows(terminal, clock, monkeypatch):
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("COLORTERM", "")
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback: os.terminal_size((23, 10)))
    say.live("Writing tests")
    assert terminal.getvalue() == f"\r   {ACCENT}✻{RESET} Tinkering… (0s){DIM} ·{RESET}\033[K"


class Screen:
    def __init__(self):
        self.shown = []

    def print(self, text):
        self.shown.append(("print", text))

    def show_live(self, text, began):
        self.shown.append(("live", text, began))

    def end_live(self):
        self.shown.append(("end",))


@pytest.mark.spec("MSG-4")
def test_the_live_line_goes_to_the_screen_said_things_write_through(terminal, clock):
    screen = Screen()
    say.write_through(screen.print, screen)
    say.live("Writing tests")
    clock[0] += 5
    say.live("Running tests")
    say.done("Answered")
    assert screen.shown == [
        ("live", "Writing tests", 100.0),
        ("live", "Running tests", 100.0),
        ("end",),
        ("print", "✅ Answered"),
    ]
    assert terminal.getvalue() == ""


@pytest.mark.spec("MSG-5")
def test_a_detail_goes_to_the_log_only(home_store, capsys):
    say.detail("dropped: no log is kept yet")
    log = Log(home_store, "calls #1")
    say.keep_log(log)
    say.detail("prompt 812 tok", model="m")
    entries = [entry for _, entry in home_store.entries("calls #1")]
    assert capsys.readouterr().out == ""
    assert len(entries) == 1 and entries[0]["note"] == "prompt 812 tok" and entries[0]["model"] == "m"


@pytest.mark.spec("MSG-6")
@pytest.mark.parametrize(
    "seconds,text", [(42.5, "42s"), (1762.08, "29m 22s"), (4000, "1h 6m 40s"), (0, "0s")]
)
def test_human_duration(seconds, text):
    assert human_duration(seconds) == text


def printing(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]


@pytest.mark.spec("MSG-7")
def test_only_messages_prints():
    paths = [ROOT / "tinker.py", *(ROOT / "tinkerkit").glob("*.py")]
    found = {path.name: printing(path) for path in paths if path.stem != "messages" and printing(path)}
    assert found == {}
    assert isinstance(say, Messages)
