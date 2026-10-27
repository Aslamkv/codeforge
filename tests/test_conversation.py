import contextlib
import io
import json
import os
import readline
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from conftest import ACCENT, BOLD, DIM, GREY, MEASURED, RESET, Terminal

import tinkerkit.conversation as conversation
from tinkerkit.conversation import (
    Channel,
    ChannelError,
    ConsoleChannel,
    ConsoleScreen,
    Desk,
    Markup,
    Message,
    Progress,
    Questions,
    Reporter,
    Shell,
    Talk,
    after_word,
    built_project,
    command_of,
    described,
    first_lines,
    first_words,
    pieces,
    running,
    text_of,
    welcome,
)
from tinkerkit.jobs import began_at
from tinkerkit.ledger import DONE_NOTE_KEPT_SECONDS, Ledger
from tinkerkit.model import Models, Ollama
from tinkerkit.project import Runs, Tracker
from tinkerkit.settings import Home


def wait_until(condition, timeout=15):
    ends = time.monotonic() + timeout
    while time.monotonic() < ends:
        if condition():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


class Recorder(Channel):
    KIND = "recorder"

    def __init__(self, name="rec", chats=("1",), inbox=()):
        super().__init__(name, {"allowed": list(chats)}, {})
        self.log, self.next, self.inbox, self.newest = [], 100, list(inbox), {}
        self.lock = threading.Lock()

    def send(self, chat, text):
        with self.lock:
            self.next += 1
            self.log.append(("send", chat, self.next, text))
            self.newest[chat] = self.next
            return self.next

    def edit(self, chat, sent, text):
        with self.lock:
            self.log.append(("edit", chat, sent, text))
            return sent

    def delete(self, chat, sent):
        self.log.append(("delete", chat, sent, ""))

    def typing(self, chat):
        self.log.append(("typing", chat, None, ""))

    def latest(self, chat, sent):
        return self.newest.get(chat) == sent

    def receive(self, timeout):
        messages, self.inbox = self.inbox, []
        return messages

    def texts(self, action=None):
        return [
            text for kind, _, _, text in list(self.log) if action in (None, kind) and kind in ("send", "edit")
        ]


@pytest.mark.spec("CNV-1")
def test_markup_plain():
    text = "**bold**, *it*, _also_, ~~gone~~, `x_y*`, [docs](https://x.org/a_b), my_var_name, \\*lit\\*"
    assert Markup.plain(text) == "bold, it, also, gone, x_y*, docs (https://x.org/a_b), my_var_name, *lit*"
    assert Markup.plain("# Title\n- one\n* two") == "Title\n• one\n• two"
    assert Markup.plain("```python\nprint(1)\n```") == "print(1)"


@pytest.mark.spec("CNV-1")
def test_markup_html():
    assert (
        Markup.html("**b** & <x> `a<b` [l](https://x.org/?a=1&b=2)")
        == '<b>b</b> &amp; &lt;x&gt; <code>a&lt;b</code> <a href="https://x.org/?a=1&amp;b=2">l</a>'
    )
    assert Markup.html("# Title\n- a") == "<b>Title</b>\n• a"
    assert Markup.html("```sh\nls -l\n```") == "<pre>ls -l</pre>"
    assert Markup.html("```output\n1 & 2\n```") == "<blockquote expandable>1 &amp; 2</blockquote>"
    assert Markup.html("~~old~~ *new*") == "<s>old</s> <i>new</i>"


@pytest.mark.spec("CNV-1")
def test_markup_ansi_and_literal(monkeypatch):
    not_a_terminal = io.StringIO()
    monkeypatch.setattr(sys, "stdout", not_a_terminal)
    assert Markup.ansi("**b**") == "b"
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.setattr(sys, "stdout", tty)
    assert Markup.ansi("**b** `c`") == "\033[1mb\033[22m \033[36mc\033[39m"
    output = Markup.ansi("```output\n" + "\n".join(str(number) for number in range(40)) + "\n```")
    assert "\033[2m  │ 29\033[22m" in output and "… 10 more lines" in output and "│ 30" not in output
    monkeypatch.setenv("NO_COLOR", "1")
    assert Markup.ansi("**b**") == "b"
    assert Markup.literal("a*b_c`d~e\\f") == "a\\*b\\_c\\`d\\~e\\\\f"
    assert Markup.plain(Markup.literal("**not bold**")) == "**not bold**"
    assert Markup.fenced("a ``` b") == "a ˋˋˋ b"


@pytest.mark.spec("CNV-1")
def test_markup_of_a_t_string():
    said, bold, line, out = "*hi*", "**ok**", "ls -a", "a ``` b"
    assert Markup.of(t"🔎 {said} {bold:md} {line:code}") == "🔎 \\*hi\\* **ok** `ls -a`"
    assert Markup.of(t"{out:block}") == "```\na ˋˋˋ b\n```"
    with pytest.raises(ValueError):
        Markup.of(t"{said:bold}")


@pytest.mark.spec("CNV-2")
def test_columns():
    table = (
        "NAME                 ID              SIZE      MODIFIED\n"
        "qwen3:8b             500a1f067a9f    5.2 GB    2 days ago\n"
        "llama3.2:latest      a80c4f17acd5    2.0 GB    3 weeks ago"
    )
    assert Markup.columns(table) == (
        "NAME | ID | SIZE | MODIFIED\nqwen3:8b | 500a1f067a9f | 5.2 GB | 2 days ago\n"
        "llama3.2:latest | a80c4f17acd5 | 2.0 GB | 3 weeks ago"
    )
    for text in ("just some words here\nand more words there", "one line only with words", "a\nb"):
        assert Markup.columns(text) == text


@pytest.mark.spec("CNV-3")
def test_pieces_split_between_lines_and_lose_no_text():
    text = "\n".join(f"line {number:03}" for number in range(100))
    parts = pieces(text, size=100)
    assert all(len(part) <= 100 for part in parts) and "\n".join(parts) == text
    assert pieces("") == [] and pieces("one") == ["one"]
    a_long_line = "short\n" + "x" * 250 + "\nend"
    assert "".join("".join(pieces(a_long_line, size=100)).split()) == "".join(a_long_line.split())


@pytest.mark.spec("CNV-5")
@pytest.mark.parametrize(
    "text,command",
    [
        ("status", "status"),
        ("/Status", "status"),
        ("status abc123", "status"),
        ("status of my build please", ""),
        ("task a calculator", "task"),
        ("models are getting better", ""),
        ("models", "models"),
        ("hello", ""),
        ("", ""),
        ("confirm", "confirm"),
        ("confirm abc123", "confirm"),
        ("research the new python", "research"),
        ("/use_model", "use-model"),
        ("/status@tinker_bot", "status"),
    ],
)
def test_command_of(text, command):
    assert command_of(text, Desk.COMMANDS) == command


@pytest.mark.spec("CNV-6")
def test_said_removes_tinker_before_a_command():
    channel = Recorder()
    assert (
        channel.said("Build it: tinker resume abc (or tinker   amend)")
        == "Build it: resume abc (or tinker   amend)"
    )
    assert channel.said("the tinker tool and /x/tinker task") == "the tinker tool and /x/tinker task"
    channel.commands_without_tinker = False
    assert channel.said("tinker resume abc") == "tinker resume abc"


@pytest.mark.spec("CNV-6")
def test_show_edits_sends_and_deletes(monkeypatch):
    channel = Recorder()
    first = channel.show("1", None, "hello", status=True)
    assert channel.log[0][:3] == ("send", "1", first[0]) and "🕒 _updated" in channel.log[0][3]
    monkeypatch.setattr(conversation, "pieces", lambda text, size=3500: [text[:10], text[10:]])
    two = channel.show("1", first, "a longer message now")
    assert two[0] == first[0] and channel.log[-2][0] == "edit" and channel.log[-1][0] == "send"
    monkeypatch.setattr(conversation, "pieces", lambda text, size=3500: [text])
    assert channel.show("1", two, "short") == [first[0]] and channel.log[-1] == ("delete", "1", two[1], "")


@pytest.mark.spec("CNV-6")
def test_show_done_comes_to_the_bottom():
    channel = Recorder()
    old = channel.show("1", None, "working")
    channel.send("1", "the person said something")
    new = channel.show("1", old, "done", done=True)
    assert new != old and ("delete", "1", old[0], "") in channel.log
    kept = channel.show("1", new, "done again", done=True)
    assert kept == new


@pytest.mark.spec("CNV-7")
def test_listened_filters_chats_and_says_a_refusal_once(monkeypatch, capsys):
    channel = Recorder(chats=("1",), inbox=[Message("1", "hi"), Message("2", "stranger")])
    assert [message.text for message in channel.listened(0)] == ["hi"]
    monkeypatch.setattr(conversation.time, "sleep", lambda seconds: None)

    def refused(timeout):
        raise ChannelError("409 Conflict")

    channel.receive = refused
    assert channel.listened(5) == [] and channel.listened(5) == []
    assert capsys.readouterr().out.count("409 Conflict") == 1


@pytest.mark.spec("CNV-7")
def test_only_one_process_claims_a_channel():
    one, two = Recorder("bot"), Recorder("bot")
    assert one.claim({"pid": 123}) is True
    try:
        assert two.claim({"pid": 456}) is False and two.holder() == {"pid": 123}
    finally:
        one.claimed.close()
    assert Recorder("other").holder() == {}


CALC_WRITING = "🔨 **calc**\n✅ step one\n✻ Tinkering… (3s) · **Writing the code**\n… detail"
CALC_WRITING_LATER = "🔨 **calc**\n✅ step one\n✶ Tinkering… (9s) · **Writing the code**\n… other detail"
CALC_VERIFYING = (
    "🔨 **calc**\n✅ step one\n✅ step two\n✳ Tinkering… · **Verifying**\n📊 1/2 spec points pass"
)
CALC_DONE = "🏁 **calc**\n✅ **Done**"
CALC_WRITING_NOW = "🔨 calc\n✻ Tinkering… · **Writing**"
MARK = f"{ACCENT}● {RESET}"


def piped_console(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    return ConsoleChannel(), out


@pytest.mark.spec("CNV-8")
def test_console_without_a_live_line_prints_settled_lines_once_and_each_new_doing_line(monkeypatch):
    console, out = piped_console(monkeypatch)
    shown = console.show("console", None, CALC_WRITING, doing="Writing the code")
    console.show("console", shown, CALC_WRITING_LATER, doing="Writing the code", tokens=50)
    console.show("console", shown, CALC_VERIFYING, doing="Verifying")
    console.show("console", shown, CALC_DONE, done=True)
    assert out.getvalue().splitlines() == [
        "🔨 calc",
        "✅ step one",
        "Writing the code",
        "✅ step two",
        "Verifying",
        "🏁 calc",
        "✅ Done",
    ]


@pytest.mark.spec("CNV-8")
def test_console_prints_only_what_a_done_message_grew_by(monkeypatch):
    console, out = piped_console(monkeypatch)
    shown = console.show("console", None, "Done\n\nline one", done=True)
    console.show("console", shown, "Done\n\nline one\nline two", done=True)
    console.show("console", shown, "Done\n\nline one\nline two", done=True)
    assert out.getvalue().splitlines() == ["Done", "", "line one", "line two"]


def colour_terminal(monkeypatch, columns=10):
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    resized_to(monkeypatch, columns)
    return terminal


def resized_to(monkeypatch, columns, lines=24):
    monkeypatch.setattr(conversation.shutil, "get_terminal_size", lambda: os.terminal_size((columns, lines)))


def typing(monkeypatch, *lines):
    prompts, typed = [], list(lines)

    def typed_line(prompt):
        prompts.append(prompt)
        if not typed:
            raise EOFError
        return typed.pop(0)

    monkeypatch.setattr("builtins.input", typed_line)
    return prompts


def drawn_after(step):
    sys.stdout.seek(0)
    sys.stdout.truncate()
    step()
    return sys.stdout.getvalue()


def ruled_console(monkeypatch, columns=10):
    colour_terminal(monkeypatch, columns)
    console = ConsoleChannel()
    console.screen.reading = True
    return console


def ruled_screen(monkeypatch, columns=10):
    return ruled_console(monkeypatch, columns).screen


def reading_console(monkeypatch, columns=10):
    colour_terminal(monkeypatch, columns)
    monkeypatch.setattr(ConsoleScreen, "TICK_SECONDS", 60)
    typing(monkeypatch)
    ConsoleChannel().receive(1)


def above_the_rule(up, change):
    return f"\0337\033[{up}F{change}\0338"


def unwrapped(rows):
    return f"\033[?7l{rows}\033[?7h"


def room_made(rows, above):
    return f"{'\033D' * rows}\0337\033[{rows + above + 1}A\033[{rows}L"


def printed_above(text, rows=1, block_rows=0):
    return f"{room_made(rows, block_rows)}\r{text}\0338"


def pulled_up(rows, kept):
    return f"\0337\033[{kept + rows}F\033[{rows}M\0338\033[{rows}A"


def shown_again(line, columns=10):
    return f"{GREY}{f'› {line}'.ljust(columns)}{RESET}"


def rule_drawn(columns):
    return f"{DIM}{'─' * columns}{RESET}"


def cleared_to_end(rows):
    return [f"{row}\033[K" for row in rows]


def block_and_rule(block, columns):
    return unwrapped("\033[E".join([*cleared_to_end(block), rule_drawn(columns)]))


def sent_back_shown(line, rows=1, block=(), columns=10):
    shown = f"{shown_again(line, columns)}\033[E{block_and_rule(block, columns)}"
    return above_the_rule(rows + len(block) + 1, shown)


def stacked(line, count):
    return "\n".join([line] * count)


@pytest.mark.spec("CNV-8")
def test_console_marks_each_message_and_indents_its_lines_on_a_colour_terminal(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    console = ConsoleChannel()
    console.send("console", "Done\nline one")
    shown = console.show("console", None, "Report\nline one", done=True)
    console.show("console", shown, "Report\nline one\nline two", done=True)
    assert terminal.getvalue() == (
        f"\r\033[K{MARK}Done\n  line one\n\r\033[K{MARK}Report\n  line one\n\r\033[K  line two\n"
    )


@pytest.mark.spec("CNV-8")
def test_console_shows_your_line_again_highlighted_without_ruling_each_prompt(monkeypatch):
    console = ruled_console(monkeypatch)
    prompts = typing(monkeypatch, "hello there", "", "exit")
    console.read()
    assert prompts == ["› "] * 4
    assert sys.stdout.getvalue() == (
        sent_back_shown("hello there", rows=2) + pulled_up(1, kept=0) + pulled_up(1, kept=0)
    )
    assert [console.lines.get() for _ in range(4)] == ["hello there", "", "exit", None]


@pytest.mark.spec("CNV-8")
def test_console_keeps_the_lines_typed_for_up_and_down_across_restarts(monkeypatch, tinker_home):
    colour_terminal(monkeypatch)
    monkeypatch.setattr(ConsoleScreen, "TICK_SECONDS", 60)
    typing(monkeypatch, "first", "", "second", "exit")
    console = ConsoleChannel()
    console.receive(1)
    while console.lines.get(timeout=1) is not None:
        pass
    readline.clear_history()
    reading_console(monkeypatch)
    assert typed_before() == ["first", "second"]
    assert (tinker_home / "prompt_history").exists()


def typed_before():
    return [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)]


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize(("line", "columns", "rows"), [("hi", 20, 1), ("x" * 25, 10, 3)])
def test_console_shows_a_line_sent_again_right_above_the_rule_by_all_its_rows(
    monkeypatch, line, columns, rows
):
    console = ruled_console(monkeypatch, columns)
    assert drawn_after(lambda: console.show_again(line)) == sent_back_shown(line, rows, columns=columns)


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize("line", ["", "  ", "exit"])
def test_console_drops_a_quiet_line_by_pulling_the_prompt_up(monkeypatch, line):
    console = ruled_console(monkeypatch)
    assert drawn_after(lambda: console.show_again(line)) == pulled_up(1, kept=0)


@pytest.mark.spec("CNV-8")
def test_console_sends_a_line_back_above_the_live_block(monkeypatch, clock):
    console = ruled_console(monkeypatch, columns=20)
    console.screen.show_live("Learning", 100.0, under=["one"])
    shown = drawn_after(lambda: console.show_again("hi"))
    assert shown == sent_back_shown("hi", block=console.screen.drawn, columns=20)


def model_picker(monkeypatch, console, chosen, models=({"name": "m"}, {"name": "n"})):
    shown = []

    def pick(choices, start):
        shown.append((choices, start, console.screen.printing.locked()))
        return chosen

    monkeypatch.setattr(conversation.say, "pick", pick)
    monkeypatch.setattr(
        Ollama, "installed_models", staticmethod(lambda: None if models is None else list(models))
    )
    return shown


@pytest.mark.spec("CNV-8")
def test_console_picks_the_model_for_use_model_typed_alone(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    Models.keep("n", MEASURED)
    shown = model_picker(monkeypatch, console, 1)
    typing(monkeypatch, " use-model ", "use-model n", "use-models")
    console.read()
    assert shown == [([("m", True), ("n", False)], 0, True)]
    assert [console.lines.get() for _ in range(4)] == ["use-model n", "use-model n", "use-models", None]


@pytest.mark.spec("CNV-8")
def test_console_sends_nothing_when_the_pick_is_cancelled(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    model_picker(monkeypatch, console, None)
    typing(monkeypatch, "use-model")
    console.read()
    assert console.lines.get() == "" and console.receive(0) == []


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize("models", [(), None])
def test_console_passes_use_model_on_when_there_is_nothing_to_pick(monkeypatch, home_store, models):
    console = ruled_console(monkeypatch)
    shown = model_picker(monkeypatch, console, 0, models)
    typing(monkeypatch, "use-model")
    console.read()
    assert shown == [] and console.lines.get() == "use-model"


@pytest.mark.spec("CNV-8")
def test_console_passes_piped_use_model_on(monkeypatch, home_store):
    colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    console = ConsoleChannel()
    shown = model_picker(monkeypatch, console, 0)
    typing(monkeypatch, "use-model")
    console.read()
    assert shown == [] and console.lines.get() == "use-model"


@pytest.mark.spec("CNV-8")
def test_console_leaves_piped_input_unruled_and_unrepeated(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    prompts = typing(monkeypatch, "hello there")
    ConsoleChannel().read()
    assert prompts == [ConsoleScreen.PROMPT] * 2
    assert terminal.getvalue() == ""


@pytest.mark.spec("CNV-8")
def test_console_prints_above_the_rule_without_touching_the_prompt(monkeypatch):
    ruled_console(monkeypatch).send("console", "hi")
    assert sys.stdout.getvalue() == printed_above(f"{MARK}hi")


@pytest.mark.spec("CNV-8")
def test_what_tinker_says_while_you_type_prints_above_the_prompt(monkeypatch):
    reading_console(monkeypatch)
    said = drawn_after(lambda: conversation.say.text("[12:00:00] tinker_bot: hello"))
    assert said == printed_above("[12:00:00] tinker_bot: hello", rows=3)


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize(
    ("text", "printed"),
    [
        (f"{MARK}hi there, friend\n  🔨 calc", printed_above(f"{MARK}hi there, friend\n  🔨 calc", rows=3)),
        (
            stacked("line", 30),
            printed_above(stacked("line", 22), rows=22) + printed_above(stacked("line", 8), rows=8),
        ),
        (
            f"{ACCENT}{'x' * 225}{RESET}",
            printed_above(stacked("x" * 10, 22), rows=22) + printed_above("x" * 5),
        ),
    ],
    ids=["wrapped", "taller-than-the-screen", "one-line-taller-than-the-room"],
)
def test_console_prints_a_message_above_the_prompt_in_pieces_by_what_shows_of_it(monkeypatch, text, printed):
    ruled_screen(monkeypatch).print(text)
    assert sys.stdout.getvalue() == printed


LIVE_COLUMNS = 40
UNDER = ["📊 3 page(s) read", "… Read 2 of 6 page(s) found"]


GLOWING_T = f"\033[38;5;153mT{RESET}{ACCENT}inkering…{RESET}"


def live_drawn(text, elapsed="0s", star="✻", word=f"{ACCENT}Tinkering…{RESET}"):
    return f"{ACCENT}{star}{RESET} {word} ({elapsed}){DIM} · {text}{RESET}"


def under_drawn(line):
    return f"  {DIM}{line}{RESET}"


def block_drawn(text, under, elapsed="0s", star="✻", word=f"{ACCENT}Tinkering…{RESET}"):
    return [live_drawn(text, elapsed, star, word), *map(under_drawn, under)]


def block_grown(by):
    return room_made(by, above=0) + "\0338"


def block_changed(rows):
    return above_the_rule(len(rows) + 1, unwrapped("\033[E".join(cleared_to_end(rows))))


def block_shrunk(by, rows_left):
    return pulled_up(by, kept=rows_left + 1)


def block_taken_out(rows):
    return block_shrunk(rows, rows_left=0)


def live_changed(text, elapsed="0s", star="✻"):
    return block_changed([live_drawn(text, elapsed, star)])


def live_shown(text):
    return block_grown(1) + live_changed(text)


def live_console(monkeypatch):
    return ruled_console(monkeypatch, LIVE_COLUMNS)


def live_screen(monkeypatch):
    return live_console(monkeypatch).screen


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize("under", [[], UNDER], ids=["live-line", "with-lines-under"])
def test_console_shows_its_live_block_dim_and_indented_above_the_rule_and_prompt(monkeypatch, clock, under):
    screen = live_screen(monkeypatch)
    shown = drawn_after(lambda: screen.show_live("Learning", 100.0, under=under))
    assert shown == block_grown(1 + len(under)) + block_changed(block_drawn("Learning", under))


@pytest.mark.spec("CNV-8")
def test_console_changes_its_live_line_in_place_without_touching_what_you_type(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Writing tests", 100.0)
    clock[0] += 64
    changed = drawn_after(lambda: screen.show_live("Running tests", 100.0))
    assert changed == live_changed("Running tests", "1m 4s")


@pytest.mark.spec("CNV-8")
def test_console_changes_its_live_block_in_place_and_turns_its_star_and_glow_on_each_tick(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=UNDER)
    clock[0] += 1
    changed = drawn_after(lambda: screen.show_live("Learning", 100.0, under=["📊 4 page(s) read", "…"]))
    assert changed == block_changed(block_drawn("Learning", ["📊 4 page(s) read", "…"], "1s"))
    assert drawn_after(screen.tick) == block_changed(
        block_drawn("Learning", ["📊 4 page(s) read", "…"], "1s", star="✶", word=GLOWING_T)
    )


@pytest.mark.spec("CNV-8")
def test_the_live_block_ticks_about_eight_times_a_second():
    assert round(1 / ConsoleScreen.TICK_SECONDS) == 8


@pytest.mark.spec("CNV-8")
def test_a_shrinking_live_block_pulls_the_rule_and_prompt_up(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=UNDER)
    shrunk = drawn_after(lambda: screen.show_live("Writing", 100.0))
    assert shrunk == block_shrunk(2, rows_left=1) + live_changed("Writing")


@pytest.mark.spec("CNV-8")
def test_a_growing_live_block_pushes_the_rule_and_prompt_down(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0)
    grown = drawn_after(lambda: screen.show_live("Learning", 100.0, under=UNDER))
    assert grown == block_grown(2) + block_changed(block_drawn("Learning", UNDER))


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize("under", [[], UNDER], ids=["live-line", "with-lines-under"])
def test_console_prints_above_its_live_block_and_takes_it_out_for_good(monkeypatch, clock, under):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=under)
    rows = 1 + len(under)
    assert drawn_after(lambda: screen.print("hello")) == printed_above("hello", block_rows=rows)
    assert drawn_after(screen.end_live) == block_taken_out(rows)
    assert screen.under == []
    assert drawn_after(screen.tick) == "" and drawn_after(screen.end_live) == ""


@pytest.mark.spec("CNV-8")
def test_the_lines_under_the_live_line_are_cut_to_the_terminal_width(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=["x" * 60])
    assert under_drawn("x" * (LIVE_COLUMNS - 3)) + "\033[K" in sys.stdout.getvalue()


@pytest.fixture
def started_screen(monkeypatch):
    resize_handler = signal.getsignal(signal.SIGWINCH)

    def started():
        colour_terminal(monkeypatch)
        monkeypatch.setattr(ConsoleScreen, "TICK_SECONDS", 60)
        ConsoleScreen().start()

    yield started
    signal.signal(signal.SIGWINCH, resize_handler)


@pytest.mark.spec("CNV-8")
def test_console_starts_by_drawing_the_rule_where_the_cursor_is(started_screen):
    started_screen()
    assert sys.stdout.getvalue() == unwrapped(rule_drawn(10)) + "\n"


def stale_rows_cleared(rows, typed_rows=0):
    return above_the_rule(rows + typed_rows, "\033[E".join(["\033[K"] * rows))


def redrawn_with_rule(block, columns, typed_rows=0):
    return above_the_rule(len(block) + 1 + typed_rows, block_and_rule(block, columns))


RULE_REDRAWN_NARROWER = stale_rows_cleared(2) + pulled_up(1, kept=1) + redrawn_with_rule([], 5)


@pytest.mark.spec("CNV-8")
@pytest.mark.parametrize(
    ("columns", "lines", "repaired"),
    [
        (
            30,
            24,
            stale_rows_cleared(5)
            + pulled_up(2, kept=3)
            + redrawn_with_rule(block_drawn("Go", ["x" * 27]), 30),
        ),
        (60, 24, stale_rows_cleared(3) + redrawn_with_rule(block_drawn("Go", ["x" * 30]), 60)),
        (
            LIVE_COLUMNS,
            10,
            stale_rows_cleared(3) + redrawn_with_rule(block_drawn("Go", ["x" * 30]), LIVE_COLUMNS),
        ),
    ],
    ids=["narrower", "wider", "shorter"],
)
def test_a_resized_window_draws_the_block_and_rule_again_at_its_size_right_above_the_prompt(
    monkeypatch, clock, columns, lines, repaired
):
    screen = live_screen(monkeypatch)
    screen.show_live("Go", 100.0, under=["x" * 30])
    resized_to(monkeypatch, columns, lines)
    assert drawn_after(screen.tick).startswith(repaired)


@pytest.mark.spec("CNV-8")
def test_a_resized_window_without_a_block_draws_only_the_rule_again(monkeypatch):
    screen = ruled_screen(monkeypatch)
    resized_to(monkeypatch, 5)
    assert drawn_after(screen.tick) == RULE_REDRAWN_NARROWER
    assert drawn_after(screen.tick) == ""


def drawn_soon(expected, seconds=2.0):
    deadline = time.monotonic() + seconds
    while expected not in sys.stdout.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    return sys.stdout.getvalue()


@pytest.mark.spec("CNV-8")
def test_a_resize_signal_draws_the_rule_again_without_waiting_for_the_tick(monkeypatch, started_screen):
    started_screen()
    resized_to(monkeypatch, 5)
    signal.raise_signal(signal.SIGWINCH)
    assert redrawn_with_rule([], 5) in drawn_soon(redrawn_with_rule([], 5))


@pytest.mark.spec("CNV-8")
def test_a_print_right_after_a_resize_draws_the_rule_again_first(monkeypatch):
    screen = ruled_screen(monkeypatch)
    resized_to(monkeypatch, 5)
    printed = drawn_after(lambda: screen.print("hello"))
    assert printed == RULE_REDRAWN_NARROWER + printed_above("hello")


@pytest.mark.spec("CNV-8")
def test_a_line_sent_right_after_a_resize_draws_the_rule_again_above_it(monkeypatch):
    console = ruled_console(monkeypatch)
    resized_to(monkeypatch, 5)
    sent = drawn_after(lambda: console.show_again(""))
    assert sent == (
        stale_rows_cleared(2, typed_rows=1)
        + pulled_up(1, kept=2)
        + redrawn_with_rule([], 5, typed_rows=1)
        + pulled_up(1, kept=0)
    )


@pytest.mark.spec("CNV-8")
def test_an_unruled_screen_does_nothing_when_resized(monkeypatch):
    screen = ruled_screen(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    resized_to(monkeypatch, 5)
    assert drawn_after(screen.tick) == ""


@pytest.mark.spec("CNV-8")
def test_console_without_colour_shows_no_live_line(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    screen.show_live("Writing tests", 100.0)
    screen.tick()
    screen.end_live()
    assert sys.stdout.getvalue() == ""


@pytest.mark.spec("CNV-8")
def test_once_the_console_reads_live_draws_in_its_live_line(monkeypatch, clock):
    reading_console(monkeypatch, LIVE_COLUMNS)
    assert drawn_after(lambda: conversation.say.live("Writing tests")) == live_shown("Writing tests")
    said = drawn_after(lambda: conversation.say.text("Answered"))
    assert said == block_taken_out(1) + printed_above("Answered")


@pytest.mark.spec("CNV-8")
def test_console_prints_a_jobs_settled_lines_once_and_shows_what_goes_on_in_its_live_line(monkeypatch, clock):
    console = live_console(monkeypatch)
    writing = "🔨 calc\n✅ step one\n✻ Tinkering… (3s) · **Writing the code**\n… detail"
    shown = console.show("console", None, writing, doing="Writing the code")
    assert sys.stdout.getvalue() == (
        f"{printed_above(f'{MARK}🔨 calc\n  ✅ step one', rows=2)}"
        f"{block_grown(2)}{block_changed(block_drawn('Writing the code', ['… detail']))}"
    )
    clock[0] += 5
    verifying = "🔨 calc\n✅ step one\n✅ step two\n✶ Tinkering… · Verifying\n📊 1/2"
    grew = drawn_after(lambda: console.show("console", shown, verifying, doing="Verifying"))
    assert grew == (
        f"{printed_above('  ✅ step two', block_rows=2)}"
        f"{block_changed(block_drawn('Verifying', ['📊 1/2'], '5s'))}"
    )
    done = drawn_after(lambda: console.show("console", shown, "🏁 calc\n✅ Done", done=True))
    assert done == f"{block_taken_out(2)}{printed_above('  🏁 calc\n  ✅ Done', rows=2)}"


@pytest.mark.spec("CNV-8")
def test_console_shows_a_message_without_a_doing_line_in_its_live_line_until_done(monkeypatch, clock):
    console = live_console(monkeypatch)
    shown = console.show("console", None, "⏳ **Getting ready**\nI answer soon.")
    assert sys.stdout.getvalue() == live_shown("⏳ Getting ready")
    done = drawn_after(lambda: console.show("console", shown, "Answer", done=True))
    assert done == block_taken_out(1) + printed_above(f"{MARK}Answer")


@pytest.mark.spec("CNV-8")
def test_a_done_message_leaves_a_live_line_it_does_not_own(monkeypatch, clock):
    console = live_console(monkeypatch)
    console.show("console", None, CALC_WRITING_NOW, doing="Writing")
    done = drawn_after(lambda: console.show("console", None, "Answer", done=True))
    assert done == printed_above(f"{MARK}Answer", block_rows=1)
    assert console.screen.live == ("Writing", 100.0, {})


@pytest.mark.spec("CNV-8")
def test_a_done_message_forgets_how_it_went(monkeypatch, clock):
    console = live_console(monkeypatch)
    shown = console.show("console", None, CALC_WRITING_NOW, doing="Writing")
    console.show("console", shown, "🏁 calc", done=True)
    assert console.began == {} and console.doings == {} and console.live_owner is None


@pytest.mark.spec("CNV-8")
def test_a_jobs_live_line_counts_from_when_the_job_began(monkeypatch, clock):
    monkeypatch.setattr(time, "time", lambda: 5000.0)
    console = live_console(monkeypatch)
    console.show("console", None, CALC_WRITING_NOW, began=4936.0, doing="Writing")
    assert console.screen.live == ("Writing", 36.0, {})


@pytest.mark.spec("CNV-8")
def test_a_jobs_live_line_shows_its_tokens_and_thinking(monkeypatch, clock):
    console = live_console(monkeypatch)
    line = "🔨 calc\n✻ Tinkering… (↓ 1.2k tokens · thinking) · **Writing**"
    console.show("console", None, line, doing="Writing", tokens=1200, thinking=True, thought_seconds=None)
    facts = {"tokens": 1200, "thinking": True, "thought_seconds": None}
    assert console.screen.live == ("Writing", 100.0, facts)
    assert f"Tinkering…{RESET} (0s · ↓ 1.2k tokens" in sys.stdout.getvalue()


@pytest.mark.spec("CNV-8")
def test_progress_says_when_the_work_began_counting_earlier_runs():
    progress = Progress("calc", before=60.0)
    assert progress.work_began is None
    progress.take({"started": 1000.0})
    assert progress.work_began == 940.0


@pytest.mark.spec("CNV-8")
def test_console_prints_plain_without_colour(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    prompts = typing(monkeypatch, "hello")
    console = ConsoleChannel()
    console.read()
    console.send("console", "Done\nline one")
    assert prompts == [ConsoleScreen.PROMPT] * 2
    assert terminal.getvalue() == "\r\033[KDone\nline one\n"


def framed(width, *rows):
    inner = width - 4
    side = lambda text, style, shown: (
        f"{ACCENT}│ {RESET}{style}{text}{RESET}{' ' * (inner - shown)}{ACCENT} │{RESET}\n"
    )
    return (
        f"{ACCENT}╭{'─' * (width - 2)}╮{RESET}\n"
        + "".join(side(*row) for row in rows)
        + f"{ACCENT}╰{'─' * (width - 2)}╯{RESET}\n"
    )


@pytest.mark.spec("CNV-8")
def test_console_frames_the_banner_as_wide_as_the_terminal_up_to_64_columns(monkeypatch):
    terminal = colour_terminal(monkeypatch, 80)
    ConsoleChannel().banner(["🔧 Tinker", "model qwen3", "help lists commands"])
    assert terminal.getvalue() == framed(
        64, ("🔧 Tinker", BOLD, 9), ("model qwen3", DIM, 11), ("help lists commands", DIM, 19)
    )


@pytest.mark.spec("CNV-8")
def test_console_cuts_banner_text_that_is_too_long(monkeypatch):
    terminal = colour_terminal(monkeypatch, 14)
    ConsoleChannel().banner(["🔧 Tinker tool", "help lists commands"])
    assert terminal.getvalue() == framed(14, ("🔧 Tinker", BOLD, 9), ("help lists", DIM, 10))


@pytest.mark.spec("CNV-8")
def test_console_draws_no_banner_without_colour_or_a_terminal(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ConsoleChannel().banner(["🔧 Tinker"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    ConsoleChannel().banner(["🔧 Tinker"])
    assert terminal.getvalue() == ""


@pytest.mark.spec("CNV-8")
def test_the_banner_names_tinker_the_model_the_projects_folder_and_the_chats(monkeypatch):
    settings = {"model": "qwen3"}
    monkeypatch.setattr(conversation.Config, "get", lambda key: settings.get(key))
    root = Path.home() / "Projects"
    with_chats = Desk([ConsoleChannel(), Recorder("telegram")], root)
    assert with_chats.banner_lines() == [
        "🔧 Tinker",
        "model qwen3 · projects ~/Projects · chats telegram",
        "help lists commands · exit quits",
    ]
    settings.clear()
    alone = Desk([ConsoleChannel()], "/tmp")
    assert alone.banner_lines()[1] == f"model no model yet · projects {Path('/tmp').resolve()}"


@pytest.mark.spec("CNV-8")
def test_the_conversation_opens_with_the_banner_before_the_status(monkeypatch):
    terminal = colour_terminal(monkeypatch, 80)
    typing(monkeypatch)
    desk = Desk([ConsoleChannel()], Path.cwd())
    monkeypatch.setattr(desk.talk, "warm", lambda: None)
    desk.run()
    shown = terminal.getvalue()
    assert shown.startswith(f"{ACCENT}╭") and shown.index("🔧 Tinker") < shown.index("╰") < shown.index(
        "idle"
    )


@pytest.mark.spec("CNV-8")
def test_console_receives_lines_until_exit():
    console = ConsoleChannel()
    console.screen.reading = True
    for line in ("hello", "  ", "exit"):
        console.lines.put(line)
    assert [message.text for message in console.receive(0)] == ["hello"]
    assert console.receive(0) == [] and not console.closed
    assert console.receive(0) == [] and console.closed
    eof = ConsoleChannel()
    eof.screen.reading = True
    eof.lines.put(None)
    assert eof.receive(0) == [] and eof.closed


def snapshot(**state):
    base = {
        "title": "calc: a calculator",
        "run": "abc123",
        "folder": "/tmp/projects/calc",
        "step": None,
        "doing": Tracker.STARTING,
        "began": None,
        "steps": [],
        "passing": None,
        "working": "",
        "tally": "",
        "found": "",
        "latest": "",
        "waiting": None,
        "ended": None,
        "before": 0.0,
        "seconds": 0.0,
    }
    return dict(base, **state)


@pytest.mark.spec("CNV-17")
def test_progress_shows_the_tracker():
    progress = Progress("a calculator", phase="✅ **Confirmed:** building it")
    assert progress.text().splitlines() == [
        "🔨 **a calculator**",
        "✻ Tinkering… · ✅ **Confirmed:** building it",
    ]
    before_any_step = snapshot()
    assert progress.take(before_any_step) and "Tinkering… · ✅ **Confirmed:** building it" in progress.text()
    progress.take(
        snapshot(
            job=7,
            step="verify",
            doing=Tracker.DOING_AND_DONE["verify"][0],
            started=time.time() - 65,
            steps=["🧠 Recalled what Tinker has learned", "📝 Spec written"],
            passing=[2, 3],
            latest="wrote calc.py",
        )
    )
    assert progress.text().splitlines() == [
        "🔨 **calc: a calculator** (Job #7)",
        "✅ 🧠 Recalled what Tinker has learned",
        "✅ 📝 Spec written",
        "✻ Tinkering… (1m 5s) · **🔁 Fixing until every test passes**",
        "📊 2/3 spec points pass",
        "… wrote calc.py",
    ]
    assert (
        progress.brief() == "**calc: a calculator** (🔁 Fixing until every test passes, 2/3 spec points pass)"
    )
    assert not progress.take(progress.snapshot)


@pytest.mark.spec("CNV-17")
def test_progress_says_what_the_run_works_on_how_far_it_got_and_what_it_found():
    progress = Progress("Reading Atomic Habits")
    progress.take(
        snapshot(
            title="Reading Atomic Habits",
            step="chapters",
            doing=Tracker.DOING_AND_DONE["chapters"][0],
            started=time.time() - 12,
            tokens=1234,
            thought_seconds=4.0,
            steps=["🧭 Chose the chapters to read: 3 core chapter(s) of 20"],
            working="Reading chapter 3, The 1% Rule (2 of 7)",
            tally="1/7 chapters · 4 things learned",
            found="Small habits compound like interest",
            latest="📖 Chapter 1: 4 things learned",
        )
    )
    assert progress.text().splitlines() == [
        "🔨 **Reading Atomic Habits**",
        "✅ 🧭 Chose the chapters to read: 3 core chapter(s) of 20",
        "✻ Tinkering… (12s · ↓ 1.2k tokens · thought for 4s) · **📚 Reading chapter 3, The 1% Rule (2 of 7)**",
        "📊 1/7 chapters · 4 things learned",
        "💡 Small habits compound like interest",
    ]
    assert progress.brief() == (
        "**Reading Atomic Habits** (📚 Reading chapter 3, The 1% Rule (2 of 7), "
        "1/7 chapters · 4 things learned)"
    )
    progress.turn()
    assert progress.text().splitlines()[2].startswith("✶ Tinkering…") and not progress.take(progress.snapshot)


@pytest.mark.spec("CNV-17")
def test_progress_says_while_the_model_thinks_and_edits_for_a_new_token_count_only_with_the_clock():
    progress = Progress("calc")
    progress.take(snapshot(tokens=950, thinking=True, thought_seconds=4.0))
    assert "✻ Tinkering… (↓ 950 tokens · thinking) · " in progress.text()
    assert not progress.take(dict(progress.snapshot, tokens=2000))
    assert progress.take(dict(progress.snapshot, thinking=False))


@pytest.mark.spec("CNV-17")
def test_progress_ending_of_a_done_run():
    progress = Progress("calc", before=60)
    progress.take(
        snapshot(
            seconds=30.0,
            ended={
                "status": "done",
                "headline": "Done. All 2 spec tests pass (SPEC.md).",
                "details": [["Run it", "python3 calc.py"]],
                "next": [
                    ["➕ Add a feature", 'tinker add @abc123 "..."'],
                    ["🐞 fix a bug", 'tinker fix @abc123 "..."'],
                ],
            },
        )
    )
    assert progress.time_taken() == "1m 30s (90 seconds)" and not progress.waiting()
    assert progress.ending().splitlines() == [
        "🏁 **calc: a calculator**",
        "✅ **Done. All 2 spec tests pass (SPEC.md).**",
        "⏱ **Done in 1m 30s (90 seconds)**",
        "▶️ Run it: `python3 calc.py`",
        '➕ Add a feature: `tinker add @abc123 "..."` · 🐞 fix a bug: `tinker fix @abc123 "..."`',
    ]
    progress.take(
        snapshot(
            ended={"status": "blocked", "headline": "Stopped: 1/2 tests pass", "details": [], "next": []}
        )
    )
    assert progress.ending().splitlines()[1:3] == [
        "⚠️ **Stopped: 1/2 tests pass**",
        "⏱ _so far: 1m 0s (60 seconds)_",
    ]


PLAN = {
    "name": "calc",
    "run": "abc123",
    "language": "Python",
    "start": "python3 calc.py",
    "kind": "project",
    "changes": ["+ C3 calc.sub(5, 3) is 2", "~ C1 calc.add(2, 3) is 5", "- adds"],
    "criteria": [["C1", "calc.add(2, 3) is 5"]],
    "non_goals": ["a GUI"],
    "folder": "/tmp/x/calc",
}


@pytest.mark.spec("CNV-17")
def test_progress_ending_of_a_plan_that_waits():
    progress = Progress("calc")
    progress.take(snapshot(waiting=PLAN))
    assert progress.waiting()
    lines = progress.ending().splitlines()
    assert lines[:2] == ["📋 **Plan for calc** · run `abc123`", "🧰 Python · ▶️ `python3 calc.py`"]
    for line in (
        "✏️ **What changed**",
        "➕ C3 calc.sub(5, 3) is 2",
        "🔄 C1 calc.add(2, 3) is 5",
        "➖ ~~adds~~",
        "🧪 **Tested before any code is written**",
        "**C1** calc.add(2, 3) is 5",
        "🚫 _Not included:_ a GUI",
        "📁 `calc`",
        "👉 `confirm` to build it · `amend WHAT` to change it",
    ):
        assert line in lines, line
    assert lines[-1].startswith("⏱ _so far:")
    assert Progress.plan(dict(PLAN, kind="fix")).startswith("🐞 **Bug shown for calc**")


@pytest.mark.spec("CNV-18")
def test_reporter_shows_the_tracker_and_ends_with_its_ending(home_store, monkeypatch):
    chats = ("1", "2")
    channel = Recorder(chats=chats)
    no_background_edits = 1000
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", no_background_edits)
    reporter = Reporter([channel], "a calculator", pid=4242)
    reporter.update()
    one_per_chat = len(chats)
    assert len(channel.texts("send")) == one_per_chat
    keep_job(home_store, 1, 4242, snapshot(step="spec", doing=Tracker.DOING_AND_DONE["spec"][0]))
    reporter.refresh()
    assert "📝 Writing the spec" in channel.texts()[-1] and channel.log[-1][0] == "edit"
    edits = len(channel.log)
    reporter.refresh()
    assert len(channel.log) == edits
    reporter.finish(snapshot(ended={"status": "done", "headline": "Done.", "details": [], "next": []}))
    assert reporter.ended and "✅ **Done.**" in channel.texts()[-1]
    assert set(reporter.sent) == {("rec", "1"), ("rec", "2")}


@pytest.mark.spec("CNV-18")
def test_reporter_edits_for_new_tokens_only_with_the_clock_but_the_console_counts_them_live(
    home_store, monkeypatch, clock
):
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", 1000)
    chat, console = Recorder(), live_console(monkeypatch)
    reporter = Reporter([chat, console], "a calculator", pid=4242)
    keep_job(home_store, 1, 4242, snapshot(step="spec", doing=Tracker.DOING_AND_DONE["spec"][0], tokens=10))
    reporter.refresh()
    edits = len(chat.log)
    keep_job(home_store, 1, 4242, snapshot(step="spec", doing=Tracker.DOING_AND_DONE["spec"][0], tokens=900))
    reporter.refresh()
    assert len(chat.log) == edits
    assert console.screen.live[2]["tokens"] == 900


@pytest.mark.spec("CNV-18")
def test_progress_edits_come_within_about_two_seconds():
    assert conversation.PROGRESS_EDIT_SECONDS <= 2 < conversation.CLOCK_ONLY_EDIT_SECONDS


@pytest.mark.spec("CNV-18")
def test_a_reporter_without_a_tracker_shows_its_lines(monkeypatch):
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", 1000)
    channel = Recorder()
    learning = Reporter([channel], "learn rust", pid=999999)
    learning.printed("  reading 4 pages")
    assert learning.progress.snapshot["latest"] == "reading 4 pages"


@pytest.mark.spec("CNV-20")
def test_an_unreadable_json_answer_is_taken_as_empty(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    for unreadable in ("{bad}", "[1]", "none"):
        fake_ollama.reply(unreadable)
        assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300)
    fake_ollama.reply('Sure: {"command": "ls", "does": "lists", "flags": [}')
    assert "```\nls\n```" in shell.propose(channel, Message("1", "x"), "", 300)


@pytest.mark.spec("CNV-20")
def test_talk_reply(measured_model, fake_ollama, monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_CONTEXT_MESSAGES", 4)
    talk, channel = Talk("task; status"), Recorder()
    for number in range(3):
        fake_ollama.reply(f"answer {number}")
        assert talk.reply(channel, Message("1", f"question {number}")) == f"answer {number}"
    messages = fake_ollama.calls[-1]["messages"]
    assert messages[0]["role"] == "system" and "task; status" in messages[0]["content"]
    assert [message["content"] for message in messages[1:]] == [
        "answer 0",
        "question 1",
        "answer 1",
        "question 2",
    ]
    assert fake_ollama.calls[-1]["think"] is False and fake_ollama.calls[-1]["options"]["num_predict"] == 1024
    from tinkerkit.model import OllamaError

    fake_ollama.reply(OllamaError("runner failed"))
    assert talk.reply(channel, Message("1", "x")).startswith("⚠️ **The model did not answer:**")


@pytest.mark.spec("CNV-20")
def test_talk_without_a_model_says_so():
    assert Talk("cmds").reply(Recorder(), Message("1", "hi")).startswith("⚠️ **No model is in use yet**")
    assert (
        Talk.busy_note([{"brief": "**calc**"}])
        == "⏸ **Pausing** **calc** to answer you.\nIt goes on once you have your answer."
    )
    assert "Getting ready to answer" in Talk.busy_note([])


@pytest.mark.spec("CNV-22")
def test_shell_proposes_one_command(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    fake_ollama.reply('{"command": "rm -rf build", "does": "deletes the build folder"}')
    shown = shell.propose(channel, Message("1", "clean up"), "", 300)
    assert "```\nrm -rf build\n```" in shown and "It changes or deletes things" in shown
    proposed = shell.take(channel, "1")
    assert (
        proposed == {"line": "rm -rf build", "asked": "clean up", "shown": None}
        and shell.take(channel, "1") is None
    )
    fake_ollama.reply('{"command": "ls\\nrm x", "does": "two"}')
    assert shell.propose(channel, Message("1", "x"), "", 300).startswith("🤷 **No command found:**")
    fake_ollama.reply("{}")
    assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300)
    fake_ollama.reply('{"command": "df -h", "does": "shows the free space"}')
    assert "It changes" not in shell.propose(channel, Message("1", "disk"), "", 300)


@pytest.mark.spec("CNV-22")
def test_shell_runs_the_confirmed_command(tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    shell.shell = "/bin/sh"
    shell.run(channel, "1", {"line": "pwd; echo oops >&2; exit 3", "asked": "where", "shown": None})
    final = channel.texts()[-1]
    assert (
        final.startswith("❌ `pwd; echo oops >&2; exit 3` · _exit 3_")
        and str(tmp_path) in final
        and "oops" in final
    )
    assert "```output" in final
    shell.drop(channel, "1", {"line": "ls", "shown": None})
    assert "🚫 _Not run:_ `ls`" in channel.texts()[-1]


def keep_job(home_store, number, pid, progress, **job):
    kept = {"number": number, "command": "task", "title": "task", "state": "running", "pid": pid, **job}
    home_store.put("jobs", str(number), dict(kept, progress=progress) if progress else kept)


@pytest.mark.spec("CNV-16")
def test_running(home_store):
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    keep_job(home_store, 1, ended.pid, snapshot(title="gone"))
    keep_job(home_store, 2, os.getpid(), snapshot(title="mine"))
    keep_job(home_store, 3, os.getppid(), snapshot(title="calc"))
    keep_job(home_store, 4, os.getppid(), None)
    keep_job(home_store, 5, os.getppid(), snapshot(title="done"), state="finished")
    assert [run["title"] for run in running()] == ["calc"] and running()[0]["pid"] == os.getppid()
    assert running(skip={os.getppid()}) == []
    keep_job(home_store, 3, os.getppid(), snapshot(title="calc"), pid_began="Sat Oct 24 16:32:00 2026")
    assert running() == []
    keep_job(home_store, 3, os.getppid(), snapshot(title="calc"), pid_began=began_at(os.getppid()))
    keep_job(home_store, 6, os.getppid(), snapshot(title="paused"), state="paused")
    assert [run["title"] for run in running()] == ["calc", "paused"]
    shown = described(running()[0])
    assert shown["title"] == "calc" and shown["brief"].startswith("**calc**") and shown["pid"] == os.getppid()


FAKE_TINKER = """
import json, os, signal, sys, time
sys.path.insert(0, os.environ["TINKER_SOURCE"])
from tinkerkit.store import Store
store = Store(os.environ["FAKE_TINKER_DB"])
args = sys.argv[1:]
folder = sys.argv[0].rsplit("/", 1)[0] + "/calc"
with open(sys.argv[0] + ".calls", "a") as calls:
    calls.write(" ".join(args) + "\\n")
if args[:1] == ["help"]:
    print("- `task WHAT`\\n- `status`")
    sys.exit(0)
state = {"title": "calc: a calculator", "run": "abc123", "folder": folder, "step": "spec", "doing": "📝 Writing the spec",
         "began": time.time(), "steps": [], "passing": None, "live": "", "latest": "", "waiting": None, "ended": None,
         "before": 0.0, "seconds": 1.0}
job = {"number": os.getpid(), "command": "task", "title": "task", "state": "running", "pid": os.getpid()}
store.put("jobs", str(os.getpid()), dict(job, progress=state))
print("Plan: the spec (project)", flush=True)
if args[:1] == ["slow"]:
    signal.signal(signal.SIGINT, lambda *a: (print("Interrupted.", flush=True), sys.exit(1)))
    time.sleep(30)
if args[:1] == ["task"]:
    state["waiting"] = {"name": "calc", "run": "abc123", "language": "Python", "start": "python3 calc.py",
                        "kind": "project", "criteria": [["C1", "calc.add(2, 3) is 5"]], "folder": folder}
elif args[:1] == ["research"]:
    state["ended"] = {"status": "done", "headline": "Researched it.", "details": [], "next": [],
                      "text": "**Python 3.14** came out in October [1]."}
else:
    state["ended"] = {"status": "done", "headline": "Done. All 1 spec tests pass (SPEC.md).", "details": [], "next": []}
store.put("jobs", str(os.getpid()), dict(job, progress=state))
"""


@pytest.fixture
def desk(tmp_path, monkeypatch, home_store):
    script = tmp_path / "fake_tinker.py"
    script.write_text(textwrap.dedent(FAKE_TINKER))
    monkeypatch.setenv("TINKER_SOURCE", str(Path(__file__).resolve().parent.parent))
    monkeypatch.setenv("FAKE_TINKER_DB", str(home_store.path))
    monkeypatch.setattr(conversation, "tinker_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", 0.2)
    channel = Recorder(chats=("1",))
    desk = Desk([channel], tmp_path)
    desk.ran = []
    ran = lambda words: desk.ran.append(words) or f"output of {' '.join(words)}"
    monkeypatch.setattr(desk, "tinker", ran)
    monkeypatch.setattr(desk, "printed", ran)
    yield desk, channel, script
    desk.stop_task()
    if desk.last_task:
        desk.last_task.wait(10)
    if desk.reporter:
        desk.reporter.ended = True


def say(desk, channel, text):
    desk.hear(channel, Message("1", text, "you", id=len(channel.log) + 1000))


def calls_of(script):
    path = Path(str(script) + ".calls")
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.spec("CNV-11")
def test_desk_answers_commands(desk):
    desk, channel, _ = desk
    assert desk.talk.commands == "task WHAT; status"
    replies = {
        text: desk.answer(Message("1", text), channel)
        for text in (
            "task",
            "status",
            "stop",
            "stop #9",
            "resume",
            "resume #9",
            "resume abc123",
            "channel add",
            "channel remove bot",
            "channel start",
            "models",
            "/Runs",
        )
    }
    assert replies["task"].startswith("✍️ Write `task WHAT TO BUILD`")
    assert (
        replies["status"] == "🟢 **Tinker** · idle: send a task"
        and replies["stop"] == "💤 No job is running."
    )
    assert replies["stop #9"] == "🤷 Job #9 is not running or paused: `jobs` lists them."
    assert replies["resume"] == "🤷 No job is stopped (jobs lists them)."
    assert replies["resume #9"] == "🤷 No job #9 (jobs lists them)."
    assert replies["resume abc123"].startswith("✍️ Write `resume [#JOB | @PROJECT]`")
    assert [replies[text] for text in ("channel add", "channel remove bot", "channel start")] == [
        f"🖥 `channel {action}` asks in a terminal: `tinker channel {action}` there"
        for action in ("add", "remove", "start")
    ]
    assert replies["models"] == "⚙️ **models**\n```\noutput of models\n```"
    assert replies["/Runs"] == "📚 **Runs**\n```\noutput of runs\n```"


@pytest.mark.spec("CNV-11")
def test_a_command_that_deletes_and_exit_ask_first(desk):
    desk, channel, _ = desk
    assert desk.answer(Message("1", "clear memory"), channel) is None
    assert desk.ran == [["clear", "memory"]] and "👉 `confirm` to delete it" in channel.texts()[-1]
    assert desk.answer(Message("1", "exit"), channel) is None and "**Stop Tinker?**" in channel.texts()[-1]
    say(desk, channel, "confirm")
    wait_until(lambda: channel.closed)


@pytest.mark.spec("CNV-11")
def test_a_deletion_preview_is_what_tinker_printed_without_its_errors(tmp_path, monkeypatch, home_store):
    script = tmp_path / "deletes.py"
    script.write_text(
        "import sys\nprint('Delete these runs:')\nprint('  • abc123')\n"
        "sys.exit('Nothing deleted: run tinker clear runs in a terminal to confirm, or add --yes.')\n"
    )
    monkeypatch.setattr(conversation, "tinker_command", lambda: [sys.executable, str(script)])
    channel = Recorder(chats=("1",))
    desk = Desk([channel], tmp_path)
    desk.answer(Message("1", "clear runs"), channel)
    shown = channel.texts()[-1]
    assert "Delete these runs:" in shown and "abc123" in shown
    assert "Nothing deleted" not in shown and "👉 `confirm` to delete it" in shown


@pytest.mark.spec("CNV-11")
def test_a_task_runs_as_its_own_tinker_and_its_plan_waits(desk, home_store):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting_plan is not None)
    assert calls_of(script) == ["help", "task a calculator --confirm"]
    assert (
        desk.waiting_plan["run"] == "abc123"
        and desk.waiting_plan["stage"] == "spec"
        and desk.waiting_plan["new"]
    )
    assert desk.waiting_plan["plan"].startswith("📋 **Plan for calc** · run `abc123`")
    assert desk.status().startswith("🙋 **Tinker** · calc: a calculator waits for you")


@pytest.mark.spec("CNV-18")
def test_research_in_a_chat_is_the_research_job_ending_with_its_answer(desk):
    desk, channel, script = desk
    say(desk, channel, "research the newest Python")
    wait_until(lambda: not desk.busy())
    assert calls_of(script) == ["help", "research the newest Python"]
    wait_until(lambda: "came out in October" in channel.texts()[-1])
    shown = channel.texts()[-1].split("\n\n🕒")[0]
    assert "✅ **Researched it.**" in shown and shown.endswith("**Python 3.14** came out in October [1].")


@pytest.mark.spec("CNV-12")
def test_confirm_asks_a_new_projects_folder_once_then_builds(desk):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting_plan is not None)
    say(desk, channel, "confirm")
    assert desk.waiting_plan["stage"] == "name" and any(
        "📁 **Its folder:** `calc`" in text for text in channel.texts()[-3:]
    )
    say(desk, channel, "confirm")
    wait_until(lambda: "resume @abc123" in calls_of(script))
    wait_until(lambda: not desk.busy())
    assert desk.waiting_plan is None


@pytest.mark.spec("CNV-11")
def test_a_confirmed_plan_is_kept_without_its_next_step_lines(desk):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting_plan is not None)
    say(desk, channel, "confirm")
    say(desk, channel, "rename sums")
    wait_until(lambda: "resume @abc123" in calls_of(script))
    kept = desk.reporter.plan_on_top.splitlines()
    assert kept[0].startswith("📋 **Plan for calc** · run `abc123`") and kept[-1] == "📁 `sums`"
    assert not [line for line in kept if line.startswith(("👉", "⏱"))]


def a_features_plan(root):
    return {
        "run": "abc123",
        "folder": root / "calc",
        "title": "calc: history",
        "plan": "📋 plan",
        "so_far": "1 second (1 seconds)",
        "stage": "spec",
        "new": False,
    }


@pytest.mark.spec("CNV-12")
def test_confirm_of_a_feature_builds_at_once_and_amend_changes_it(desk):
    desk, channel, script = desk
    desk.waiting_plan = a_features_plan(desk.root)
    say(desk, channel, "amend also keep the date")
    wait_until(lambda: "amend @abc123 also keep the date" in calls_of(script))
    wait_until(lambda: not desk.busy())
    desk.waiting_plan = a_features_plan(desk.root)
    say(desk, channel, "confirm @calc")
    wait_until(lambda: "resume @abc123" in calls_of(script))


@pytest.mark.spec("CNV-12")
def test_what_waits_for_confirm_is_cancelled_by_anything_else(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kwargs: None)
    desk.answer(Message("1", "clear memory"), channel)
    say(desk, channel, "no thanks")
    assert "🚫 _Not done:_ `clear memory`" in channel.texts()[-1] and desk.ran == [["clear", "memory"]]
    desk.answer(Message("1", "clear memory"), channel)
    say(desk, channel, "confirm")
    wait_until(lambda: ["clear", "memory", "--yes"] in desk.ran)
    assert desk.decide("confirm", ["confirm"]).startswith("🤷 No plan waits")
    assert desk.rename({}, ["rename", "x"]).startswith("🤷 No plan waits")
    assert desk.decide("amend", ["amend"]).startswith("✍️ Write `amend [@PROJECT] CHANGE`")


@pytest.mark.spec("CNV-12")
def test_a_chat_has_one_question_and_a_new_one_cancels_the_one_before(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kwargs: None)
    desk.answer(Message("1", "clear memory"), channel)
    say(desk, channel, "clear runs")
    assert any(text.startswith("🚫 _Not done:_ `clear memory`") for text in channel.texts())
    assert len(desk.questions.by_chat) == 1
    say(desk, channel, "confirm")
    wait_until(lambda: ["clear", "runs", "--yes"] in desk.ran)
    assert ["clear", "memory", "--yes"] not in desk.ran and desk.questions.by_chat == {}
    desk.shell.name(channel, Message("1", "list files"), desk.running_now)
    say(desk, channel, "confirm")
    assert channel.texts()[-1].startswith("⏳ The command is still being worked out")
    say(desk, channel, "hello")
    assert desk.questions.waiting(channel, "1").naming


@pytest.mark.spec("CNV-13")
def test_a_command_sent_alone_asks_for_its_words(desk, monkeypatch):
    desk, channel, _ = desk
    answered = []
    monkeypatch.setattr(desk, "answer", lambda message, channel: answered.append(message.text))
    say(desk, channel, "/fix")
    assert answered == [] and channel.texts()[-1].startswith("✍️ **fix** · fix a bug")
    say(desk, channel, "typing 2/0 crashes")
    say(desk, channel, "ask")
    starts_with_a_command_word = "help me"
    say(desk, channel, starts_with_a_command_word)
    say(desk, channel, "/read")
    a_file_to_read = "read /tmp/book.epub"
    say(desk, channel, a_file_to_read)
    say(desk, channel, "/teach")
    a_command_of_its_own = "/status"
    say(desk, channel, a_command_of_its_own)
    say(desk, channel, "status")
    assert answered == ["fix typing 2/0 crashes", "ask help me", "read /tmp/book.epub", "/status", "status"]
    assert desk.questions.by_chat == {}


@pytest.mark.spec("CNV-14")
def test_a_glance_goes_soon_and_history_shows_things_again(desk, monkeypatch):
    desk, channel, _ = desk
    glances = []
    monkeypatch.setattr(
        channel, "glance", lambda chat, asked, sent, keep=None: glances.append((chat, asked, sent, keep))
    )
    monkeypatch.setattr(channel, "clear", lambda: 3)
    desk.hear(channel, Message("1", "jobs", "you", id=7))
    assert glances == [("1", 7, [101], None)]
    assert desk.answer(Message("1", "history"), channel) == "📜 **History:** nothing shown in the chats yet"
    Ledger.shown("rec", "1", None, [5], "🔨 **calc**\n▸ build", "task a calculator")
    listed = desk.answer(Message("1", "history"), channel)
    assert "`#1`" in listed and "🔨 calc" in listed and "_you: task a calculator_" in listed
    again = desk.answer(Message("1", "history #1"), channel)
    assert again.startswith("📜 **#1**") and "💬 _you:_ task a calculator" in again
    assert again.endswith("🔨 **calc**\n▸ build") and "No #9" in desk.answer(
        Message("1", "history #9"), channel
    )
    assert desk.answer(Message("1", "clear chat"), channel) is None
    assert (
        channel.texts()[-1].startswith("🧹 **Chat cleared:** 3 messages taken out")
        and glances[-1][3] == DONE_NOTE_KEPT_SECONDS
    )
    monkeypatch.setattr(desk, "tinker", lambda words: Home.clear(["history"], yes=True) or "Cleared history.")
    desk.answer(Message("1", "clear history"), channel)
    shown = len(channel.texts())
    say(desk, channel, "confirm")
    wait_until(lambda: len(channel.texts()) > shown)
    assert channel.texts()[-1].startswith("🧹 **clear history: done, and the chat:** 3 messages taken out")
    nothing_left = ([], 1)
    assert Ledger.page(1) == nothing_left


@pytest.mark.spec("CNV-15")
def test_follow_reads_the_tasks_tracker(tmp_path, home_store):
    desk = Desk.__new__(Desk)
    desk.echo_to_terminal, desk.messages_by_run, desk.seconds_by_run, desk.waiting_plan = False, {}, {}, None
    desk.say_status = lambda *args, **kwargs: None
    reporter = Reporter([Recorder()], "a calculator", pid=4343)
    keep_job(home_store, 1, 4343, snapshot(folder="/tmp/projects/calc", waiting=PLAN))
    process = type(
        "P",
        (),
        {
            "stdout": io.StringIO("Plan: the spec\n📋 printed text is never read\n"),
            "pid": 4343,
            "wait": lambda self: 0,
        },
    )()
    task = {"run": None, "folder": tmp_path, "title": "a calculator", "arguments": ["task"]}
    desk.follow(process, reporter, task)
    assert task["run"] == "abc123" and task["folder"] == Path("/tmp/projects/calc")
    assert task["title"] == "calc: a calculator" == reporter.progress.title
    assert desk.waiting_plan["run"] == "abc123" and desk.messages_by_run["abc123"] is reporter.sent
    assert reporter.tracked()["waiting"] == PLAN


@pytest.mark.spec("CNV-4")
def test_helpers(tmp_path):
    assert first_lines("a\nb\nc", 2) == "a\nb\n… 1 more lines" and first_lines("a", 2) == "a"
    assert first_words("  a \n b  c ", 3) == "a b"
    assert text_of(b"caf\xc3\xa9") == "café" and text_of(None) == "" and text_of("x") == "x"
    assert after_word(Message("1", "research the python news", "me")).text == "the python news"


def built(tmp_path, name, run_id, run, status="done", updated="2026-10-01 10:00"):
    from tinkerkit.project import project_store

    folder = tmp_path / name
    folder.mkdir(parents=True)
    Runs.save(dict(Runs.new_record("t", folder, None), id=run_id, status=status), folder)
    store = project_store(folder)
    store.put("run", "record", dict(store.get("run", "record"), updated=updated))
    store.put("spec", "plan", {"toolchain": {"run": run, "activate": ""}})
    return folder


@pytest.mark.spec("CNV-4")
def test_built_project(tmp_path):
    built(tmp_path, "calc", "aaa111", "x", updated="2026-10-01 10:00")
    built(tmp_path, "notes", "bbb222", "x", updated="2026-10-02 10:00")
    built(tmp_path, "todo", "ccc333", "x", status="blocked", updated="2026-10-03 10:00")
    last_built = "bbb222"
    assert built_project("")["id"] == last_built
    assert built_project("calc")["id"] == "aaa111" and built_project("ccc333")["id"] == "ccc333"
    assert built_project("ccc") is None and built_project("nothing") is None


def pausing_into(order):
    @contextlib.contextmanager
    def pausing():
        order.append("paused")
        yield
        order.append("goes on")

    return pausing


@pytest.mark.spec("CNV-21", "JOB-10")
def test_a_chat_pauses_the_job_that_runs_before_it_is_answered(desk, monkeypatch):
    desk, channel, _ = desk
    order = []
    monkeypatch.setattr(desk.talk, "pausing_jobs", pausing_into(order))
    monkeypatch.setattr(desk.talk, "reply", lambda *asked: order.append("answered") or "the answer")
    say(desk, channel, "hello there")
    wait_until(lambda: any("the answer" in text for text in channel.texts()))
    assert order == ["paused", "answered", "goes on"]
    assert Desk([channel], desk.root).talk.pausing_jobs is conversation.paused_for_you


ECHO_APP = "import sys\nprint('args:', sys.argv[1:])\nprint('input:', sys.stdin.read().strip())\n"


@pytest.mark.spec("CNV-23")
def test_run_a_built_project(desk, tmp_path):
    desk, channel, _ = desk
    folder = built(tmp_path, "calc", "aaa111", f'"{sys.executable}" app.py')
    (folder / "app.py").write_text(ECHO_APP)
    desk.run_app(channel, "1", ['@calc add "two words" <<< hello there'])
    final = channel.texts()[-1]
    assert final.startswith("✅ **calc** `") and "add 'two words'` · _exit 0_" in final
    assert (
        "_input:_ `hello there`" in final
        and "args: ['add', 'two words']" in final
        and "input: hello there" in final
    )
    no_project_named = ["5 + 3"]
    desk.run_app(channel, "1", no_project_named)
    assert "args: ['5', '+', '3']" in channel.texts()[-1]


@pytest.mark.spec("CNV-23")
def test_run_with_nothing_to_run(desk, tmp_path):
    desk, channel, _ = desk
    desk.run_app(channel, "1", ["x"])
    assert channel.texts()[-1].startswith("🤷 **No project is built yet:**")
    built(tmp_path, "lib", "bbb222", "")
    desk.run_app(channel, "1", ["@lib"])
    assert channel.texts()[-1].startswith("🤷 **lib has no run command:**")
    desk.run_app(channel, "1", ["@nothing 1 2"])
    assert channel.texts()[-1].startswith("🤷 **No project @nothing:**")
    a_folders_name_as_an_argument = ["lib 1 2"]
    desk.run_app(channel, "1", a_folders_name_as_an_argument)
    assert "lib" in channel.texts()[-1] and "has no run command" in channel.texts()[-1]


class Listening(Recorder):
    def receive(self, timeout):
        time.sleep(0.05)
        return [self.inbox.pop(0)] if self.inbox else []


def goodbyes_handed(monkeypatch):
    handed = []
    monkeypatch.setattr(
        conversation,
        "tinker_process",
        lambda arguments, output=None: handed.append((arguments[:2], json.loads(arguments[2]), output)),
    )
    return handed


@pytest.mark.spec("CNV-9", "CNV-11")
def test_the_status_is_sent_anew_as_tinker_starts_and_the_goodbye_is_handed_on(desk, home_store, monkeypatch):
    monkeypatch.setattr(conversation, "ENDED_LOOK_SECONDS", 0.05)
    handed = goodbyes_handed(monkeypatch)
    status_before = 55
    home_store.put("listening", "bot", {"1": status_before})
    exit_said, confirm_said = Message("1", "exit", id=1), Message("1", "confirm", id=2)
    channel = Listening("bot", inbox=[exit_said, confirm_said])
    collected = []
    channel.collect = lambda: collected.append(True)
    Desk([channel], Path.cwd()).run()
    first_status, the_question_to_stop = 101, 102
    status = [(kind, sent, text) for kind, _, sent, text in channel.log if "**Tinker** ·" in text]
    assert [(kind, sent) for kind, sent, _ in status] == [("send", first_status)]
    assert status[0][2].startswith("🟢 **Tinker** · idle: send a task") and channel.closed
    deleted = {sent for kind, _, sent, _ in channel.log if kind == "delete"}
    assert deleted == {status_before, exit_said.id, confirm_said.id}
    assert not any("Stopping" in text for text in channel.texts())
    assert home_store.get("listening", "bot") == {"1": first_status} and collected
    goodbye = {
        "status": "💤 **Tinker** · not listening",
        "channels": ["bot"],
        "question": {"channel": "bot", "chat": "1", "messages": [the_question_to_stop]},
    }
    assert handed == [(["channel", "goodbye"], goodbye, subprocess.DEVNULL)]
    channel.claimed.close()


@pytest.mark.spec("CNV-9")
def test_a_terminal_alone_hands_no_goodbye_on(monkeypatch):
    handed = goodbyes_handed(monkeypatch)
    console, out = piped_console(monkeypatch)
    Desk([console], Path.cwd()).say_goodbye()
    assert handed == [] and "not listening" in out.getvalue()


@pytest.mark.spec("CNV-9")
@pytest.mark.parametrize("slow", ["collect", "status", "adopt_exercise"])
def test_the_terminal_reads_and_exits_at_once_while_periodic_work_waits(monkeypatch, slow):
    monkeypatch.setattr(conversation, "STATUS_LOOK_SECONDS", 0)
    monkeypatch.setattr(conversation, "EXPIRED_MESSAGES_SWEEP_SECONDS", 0)
    colour_terminal(monkeypatch, 80)
    console = ConsoleChannel()
    desk = Desk([console], Path.cwd())
    monkeypatch.setattr(desk.talk, "warm", lambda: None)
    status = desk.status
    waits, released = threading.Event(), threading.Event()

    def waiting(then=lambda: None, passing=0):
        calls = []

        def wait():
            calls.append(True)
            if len(calls) > passing:
                waits.set()
                released.wait()
            return then()

        return wait

    typed = ["exit"]

    def exit_once_it_waits(prompt):
        assert waits.wait(timeout=5)
        if not typed:
            raise EOFError
        return typed.pop()

    monkeypatch.setattr("builtins.input", exit_once_it_waits)
    patched = {"collect": (console, waiting()), "status": (desk, waiting(status, passing=1))}
    owner, stand_in = patched.get(slow, (desk, waiting()))
    monkeypatch.setattr(owner, slow, stand_in)
    running = threading.Thread(target=desk.run, daemon=True)
    running.start()
    assert waits.wait(timeout=5)
    started = time.monotonic()
    running.join(timeout=5)
    ended_in = time.monotonic() - started
    released.set()
    assert not running.is_alive() and ended_in < 1 and console.closed


def desk_with_a_started_chat(channel):
    listening = Desk([channel], Path.cwd())
    listening.chats_started.add(channel)
    return listening


@pytest.mark.spec("CNV-9")
def test_every_status_is_sent_anew_the_one_before_taken_out(desk):
    channel = Recorder("bot")
    listening = desk_with_a_started_chat(channel)
    listening.say_status("🔄 **Tinker** · working on calc")
    listening.say_status("🔄 **Tinker** · working on learn go")
    listening.say_status("🟢 **Tinker** · idle: send a task")
    log = [(kind, sent) for kind, _, sent, _ in channel.log]
    assert log[0] == ("send", 101) and "edit" not in {kind for kind, _ in log}
    assert sorted(log[1:3]) == [("delete", 101), ("send", 102)]
    assert sorted(log[3:]) == [("delete", 102), ("send", 103)]


@pytest.mark.spec("CNV-8")
def test_the_terminal_leaves_out_what_tinker_works_on_while_a_job_shows_its_progress(desk, monkeypatch):
    chat, console, shown = Recorder("bot"), ConsoleChannel(), []
    monkeypatch.setattr(console, "show", lambda chat, sent, text, done=False: shown.append(text) or [1])
    listening = desk_with_a_started_chat(chat)
    listening.channels.append(console)
    listening.consoles.append(console)
    monkeypatch.setattr(listening, "keep_job_at_bottom", lambda: None)
    monkeypatch.setattr(listening, "busy", lambda: True)
    listening.say_status("🔄 **Tinker** · working on calc")
    monkeypatch.setattr(listening, "busy", lambda: False)
    listening.say_status("🟢 **Tinker** · idle: send a task")
    assert shown == ["🟢 **Tinker** · idle: send a task"]
    assert any("working on calc" in text for text in chat.texts("send"))


@pytest.mark.spec("CNV-9")
def test_a_job_says_its_status_as_it_starts_and_ends(desk, monkeypatch):
    channel = Recorder("bot")
    listening = desk_with_a_started_chat(channel)
    listening.say_status()

    class Running:
        pid, stdout, code = os.getpid(), [], None

        def poll(self):
            return self.code

        def wait(self, timeout=None):
            return 0

    job = Running()
    monkeypatch.setattr(listening, "spawn", lambda arguments: job)
    monkeypatch.setattr(listening, "follow", lambda process, reporter, task: None)
    listening.start_task(["learn", "go"], Path.cwd(), "learn go")
    sent = channel.texts("send")
    assert sent[0].startswith("🟢 **Tinker** · idle")
    assert sent[1].startswith("🔄 **Tinker** · working on learn go\n\n🕒") and "learn go" in sent[2]
    listening.reporter.progress.snapshot["doing"] = "building"
    listening.reporter.update()
    assert listening.status() == "🔄 **Tinker** · working on learn go"
    listening.say_status("🔄 **Tinker** · working on learn go; calc in a terminal")
    sent = channel.texts("send")
    assert "calc in a terminal" in sent[-2] and "building" in sent[-1]
    job.code = 0
    listening.ended(job, listening.reporter, listening.task)
    assert listening.said_status.startswith("🟢 **Tinker** · idle")


@pytest.mark.spec("CNV-9")
def test_a_run_in_a_terminal_shows_its_title_and_never_how_far_it_got(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    state = snapshot(title="calc", doing="🔨 Building", passing=[1, 2], pid_began=began_at(os.getppid()))
    keep_job(home_store, 1, os.getppid(), state)
    assert desk.status() == "🔄 **Tinker** · working in a terminal on calc"


@pytest.mark.spec("CNV-9")
def test_desk_refuses_a_chat_another_tinker_listens_to(desk):
    _, _, script = desk
    holder = Recorder("bot")
    assert holder.claim({"pid": 999})
    try:
        with pytest.raises(SystemExit, match="bot is listened to by another Tinker \\(process 999\\)"):
            Desk([Recorder("bot")], Path.cwd()).run()
    finally:
        holder.claimed.close()


@pytest.mark.spec("CNV-24")
def test_one_shot_commands(monkeypatch, capsys):
    monkeypatch.setattr(conversation, "commands_brief", lambda: "cmds")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert conversation.one_shot("chat", "") == "tinker chat needs words after it, e.g. tinker chat hello"
    assert (
        conversation.one_shot("command", "") == "tinker command needs words after it, e.g. tinker command ..."
    )
    assert conversation.one_shot("history", "") is None
    assert "nothing shown in the chats yet" in capsys.readouterr().out
    conversation.one_shot("chat", "hello")
    assert "No model is in use yet" in capsys.readouterr().out


@pytest.mark.spec("CNV-24")
def test_one_shot_command_runs_nothing_without_a_yes(
    monkeypatch, measured_model, fake_ollama, capsys, tmp_path
):
    monkeypatch.setattr(conversation, "commands_brief", lambda: "cmds")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.chdir(tmp_path)
    fake_ollama.reply('{"command": "touch made.txt", "does": "makes a file"}')
    conversation.one_shot("command", "make a file")
    out = capsys.readouterr().out
    assert "touch made.txt" in out and "Not run:" in out and not (tmp_path / "made.txt").exists()


@pytest.fixture
def attached(monkeypatch, home_store):
    import signal

    handler = signal.getsignal(conversation.JOB_STOP_SIGNAL)
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", 1000)
    started = []
    channel = Recorder()

    def start(title="calc: a calculator"):
        tracker = Tracker(title)
        tracker.publish()
        started.append(conversation.Attached([channel], title, tracker).start())
        return started[-1]

    yield start, channel
    for task in started:
        task.reporter.ended = True
    signal.signal(conversation.JOB_STOP_SIGNAL, handler)


@pytest.mark.spec("CNV-19")
def test_a_terminal_task_reports_and_stops_on_a_signal(attached, home_store):
    start, channel = attached
    task = start()
    assert channel.texts()[0].startswith("🔨 **calc: a calculator**")
    task.tracker.phase("recall")
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), conversation.JOB_STOP_SIGNAL)
        time.sleep(1)
    task.end()
    assert "🧠 Recalling what Tinker has learned" in channel.texts()[-1]


@pytest.mark.spec("CNV-10")
def test_start_is_answered_with_a_welcome(desk):
    desk, channel, script = desk
    say(desk, channel, "/start")
    text = channel.texts()[-1]
    assert text.startswith("👋 **Hi, I'm Tinker!**")
    for section in (
        "🔨 **Build something**",
        "📂 **Your projects**",
        "💬 **Ask me anything**",
        "⏳ **While I work**",
    ):
        assert section in text
    assert "`task a calculator in Python`" in text and "normal chat" in text and "`help`" in text
    assert calls_of(script) == ["help"] and desk.last_task is None
    a_start_link_with_a_payload = "/start abcdef"
    say(desk, channel, a_start_link_with_a_payload)
    assert channel.texts()[-1] == welcome()


@pytest.mark.spec("CNV-25")
def test_memory_graph_is_sent_as_a_file(desk, monkeypatch):
    desk, channel, _ = desk
    sent = []
    channel.send_file = lambda chat, path, caption: sent.append((chat, path, caption))
    page = conversation.Home.graph_page()

    def make_page(words):
        desk.ran.append(words)
        page.write_text("<!doctype html>")
        return "Tinker's memory: 2 lessons; 3 links"

    monkeypatch.setattr(desk, "tinker", make_page)
    assert command_of("memory --graph") == "memory"
    assert desk.answer(Message("1", "memory --graph"), channel) is None
    assert desk.ran == [["memory", "--graph"]]
    assert sent == [("1", page, "🧠 **Tinker's memory**\nTinker's memory: 2 lessons; 3 links")]
    monkeypatch.setattr(desk, "tinker", lambda words: "Error: the home cannot be read")
    assert (
        desk.answer(Message("1", "memory --graph"), channel)
        == "🧠 **memory --graph**\n```\nError: the home cannot be read\n```"
        and len(sent) == 1
    )


@pytest.mark.spec("GRF-6", "GRF-10", "CMD-1")
def test_memory_with_words_is_browsed_in_a_chat(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk, "tinker", lambda words: desk.ran.append(words) or "[2d35d6] ✅ lesson    Seed")
    assert command_of("memory python lessons 2") == "memory"
    answer = desk.answer(Message("1", "memory python lessons 2"), channel)
    assert desk.ran == [["memory", "python", "lessons", "2"]]
    assert answer == "🧠 **memory python lessons 2**\n```\n[2d35d6] ✅ lesson    Seed\n```"
    desk.answer(Message("1", "memory show [2d35d6]"), channel)
    assert desk.ran[-1] == ["memory", "show", "[2d35d6]"]


@pytest.mark.spec("CNV-25")
def test_the_terminal_opens_the_file_and_other_channels_send_where_it_is(monkeypatch, tmp_path):
    opened, printed = [], []
    monkeypatch.setattr(conversation.webbrowser, "open", opened.append)
    console = ConsoleChannel()
    monkeypatch.setattr(console.screen, "print", printed.append)
    page = tmp_path / "memory.html"
    console.send_file("console", page, "🧠 **Tinker's memory**")
    assert opened == [page.as_uri()] and f"Opened in your browser: {page}" in Markup.plain(printed[0])
    plain = Recorder()
    plain.send_file("1", page, "🧠 **Tinker's memory**")
    assert plain.texts() == [f"🧠 **Tinker's memory**\n📄 `{page}`"]


@pytest.mark.spec("JOB-10")
def test_a_command_line_run_once_confirmed_pauses_the_job_while_its_output_is_read(tmp_path, monkeypatch):
    order = []
    talk = Talk("cmds", pausing_jobs=pausing_into(order))
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: ("m", {}, None)))
    monkeypatch.setattr(
        Models, "quick_reply", staticmethod(lambda *asked: order.append("read") or "it says hi")
    )
    channel = Recorder()
    Shell(talk, tmp_path, Questions()).run(
        channel, "1", {"line": "echo hi", "asked": "say hi", "shown": None}
    )
    assert order == ["paused", "read", "goes on"] and "it says hi" in channel.texts()[-1]


@pytest.mark.spec("RDG-13")
def test_the_reading_list_is_answered_at_once_in_a_chat(desk):
    desk, channel, _ = desk
    assert (
        desk.answer(Message("1", "read list"), channel) == "📚 **read list**\n```\noutput of read list\n```"
    )
    assert desk.answer(Message("1", "read show #2"), channel).startswith("📚 **read show #2**")
    assert desk.ran == [["read", "list"], ["read", "show", "#2"]]
