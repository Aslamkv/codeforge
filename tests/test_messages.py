import ast
import io
import os
import shutil
import sys
from pathlib import Path

import pytest
from conftest import ACCENT, DIM, RESET, Terminal

from tinkerkit import project as project_module
from tinkerkit.messages import Message, Messages, human_duration, live_line, say, short_count, tinkering
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


@pytest.mark.spec("MSG-3")
def test_tokens_and_thinking_reach_the_tracker(home_store, monkeypatch, capsys):
    now = [100.0]
    monkeypatch.setattr(project_module.time, "time", lambda: now[0])
    tracker = Tracker("reading")
    say.follow(tracker.heard)
    with say.working_on("Reading chapter 1, Habits (1 of 2)"):
        say.thinking(True)
        say.tokens(30)
        assert tracker.state["thinking"] and tracker.state["tokens"] == 30
        now[0] = 112.0
        say.thinking(False)
        say.tokens(12)
    state = tracker.state
    assert not state["thinking"] and state["thought_seconds"] == 12.0 and state["tokens"] == 42
    with say.working_on("Reading chapter 2, Tools (2 of 2)"):
        assert tracker.state["thought_seconds"] is None and tracker.state["tokens"] == 42
    assert capsys.readouterr().out == ""


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


@pytest.fixture
def colored_terminal(terminal, monkeypatch):
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("COLORTERM", "")
    return terminal


def drawn_lines(terminal):
    return terminal.getvalue().split("\033[K")


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
    drawn = drawn_lines(terminal)
    assert drawn[2] == "\r   ✳ Tinkering… (15s) · Writing calc.py"
    assert drawn[-2] == "\r   ✳ Tinkering… (0s) · Running tests"


STEEL_BLUE = "\033[38;2;70;130;180m"
LIGHT_STEEL_BLUE = "\033[38;2;176;196;222m"
GLOW = "\033[38;5;153m"


@pytest.mark.spec("MSG-4")
@pytest.mark.parametrize("colorterm,accent", [("truecolor", STEEL_BLUE), ("24bit", STEEL_BLUE), ("", ACCENT)])
def test_the_live_star_is_steel_blue_and_what_goes_on_is_dim(
    colored_terminal, clock, monkeypatch, colorterm, accent
):
    monkeypatch.setenv("COLORTERM", colorterm)
    say.live("Writing tests")
    assert (
        colored_terminal.getvalue()
        == f"\r   {accent}✻{RESET} {accent}Tinkering…{RESET} (0s){DIM} · Writing tests{RESET}\033[K"
    )


@pytest.mark.spec("MSG-4")
@pytest.mark.parametrize(
    "frame,before,glowing,after",
    [
        (0, "", "", "Tinkering…"),
        (1, "", "T", "inkering…"),
        (4, "T", "ink", "ering…"),
        (12, "Tinkering", "…", ""),
        (13, "", "", "Tinkering…"),
    ],
)
def test_a_glow_moves_through_tinkering_one_letter_each_frame(
    clock, monkeypatch, frame, before, glowing, after
):
    monkeypatch.delenv("COLORTERM", raising=False)
    shown = live_line("Writing tests", 100.0, frame, 80, Terminal())
    word = "".join(
        f"{style}{text}{RESET}"
        for text, style in ((before, ACCENT), (glowing, GLOW), (after, ACCENT))
        if text
    )
    assert shown.split(" ", 1)[1].startswith(f"{word} (0s)")


@pytest.mark.spec("MSG-4")
def test_the_glow_is_light_steel_blue_on_a_truecolor_terminal(clock, monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    shown = live_line("Writing tests", 100.0, 1, 80, Terminal())
    assert f"{LIGHT_STEEL_BLUE}T{RESET}{STEEL_BLUE}inkering…{RESET}" in shown


@pytest.mark.spec("MSG-4")
def test_without_colour_the_live_line_has_no_glow(clock):
    assert (
        live_line("Writing tests", 100.0, 4, 80, io.StringIO())
        == f"{Messages.STAR[4]} Tinkering… (0s) · Writing tests"
    )


@pytest.mark.spec("MSG-4")
def test_the_live_line_is_cut_to_the_terminal_width_by_what_shows(colored_terminal, clock, monkeypatch):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback: os.terminal_size((23, 10)))
    say.live("Writing tests")
    assert (
        colored_terminal.getvalue()
        == f"\r   {ACCENT}✻{RESET} {ACCENT}Tinkering…{RESET} (0s){DIM} ·{RESET}\033[K"
    )


@pytest.mark.spec("MSG-4")
def test_the_live_line_shows_the_tokens_and_thinking_heard_since_the_work_began(terminal, clock):
    say.now_on("Writing tests")
    say.tokens(1000)
    say.tokens(200)
    say.thinking(True)
    say.live("Writing tests")
    clock[0] += 12
    say.thinking(False)
    say.live("Writing tests")
    say.now_on("")
    say.now_on("Running tests")
    drawn = drawn_lines(terminal)
    assert drawn[1] == "\r   ✶ Tinkering… (0s · ↓ 1.2k tokens · thinking) · Writing tests"
    assert drawn[2] == "\r   ✳ Tinkering… (12s · ↓ 1.2k tokens · thought for 12s) · Writing tests"
    assert drawn[-2] == "\r   ✢ Tinkering… (0s) · Running tests"


@pytest.mark.spec("MSG-4")
@pytest.mark.parametrize(
    "count,short", [(0, "0"), (950, "950"), (1234, "1.2k"), (12_345, "12k"), (1_234_567, "1.2M")]
)
def test_the_live_line_counts_tokens_in_short(count, short):
    assert short_count(count) == short


@pytest.mark.spec("MSG-4")
def test_the_tinkering_line_leaves_out_empty_facts():
    assert tinkering(None, "Writing") == "Tinkering… · Writing"
    assert tinkering(64, "Writing", thought_seconds=12) == "Tinkering… (1m 4s · thought for 12s) · Writing"


NO_FACTS = {"tokens": 0, "thinking": False, "thought_seconds": None}


class Screen:
    def __init__(self):
        self.shown = []

    def print(self, text):
        self.shown.append(("print", text))

    def show_live(self, text, began, **facts):
        self.shown.append(("live", text, began, facts))

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
        ("live", "Writing tests", 100.0, NO_FACTS),
        ("live", "Running tests", 100.0, NO_FACTS),
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
