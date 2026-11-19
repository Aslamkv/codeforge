import io
import os
import readline
import signal
import sys
import threading
import time

import pytest
from conftest import ACCENT, BOLD, DIM, GREY, MEASURED, RESET, Terminal
from test_channels import BotAPI, every_telegram_talks_to

from tinkerkit.chat.channels import Channels
from tinkerkit.chat.console import (
    MODEL_PULL_HINT,
    Channel,
    ChannelError,
    ConsoleChannel,
    ConsoleScreen,
    PromptDrawer,
    typed_before,
)
from tinkerkit.chat.markup import Markup, Message
from tinkerkit.commands import MenuRow, slash_matches, synopsis_words
from tinkerkit.messages import DIMMED, Text, say, styled, visible_width
from tinkerkit.model.models import Models, Ollama
from tinkerkit.settings import Config


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

    def offer_fresh(self, chat, shown):
        self.log.append(("fresh", chat, shown, ""))

    def offer_next(self, chat, shown, words):
        self.log.append(("next", chat, shown, words))

    def now_inside(self, chat, mode):
        self.log.append(("inside", chat, None, mode))

    def offer_leave(self, chat, shown, label):
        self.log.append(("leave", chat, shown, label))

    def offer_answers(self, chat, shown):
        self.log.append(("answers", chat, shown, ""))

    def send_file(self, chat, path, caption):
        self.log.append(("file", chat, path, caption))
        return super().send_file(chat, path, caption)

    def send_photo(self, chat, path, caption):
        self.log.append(("photo", chat, path, caption))

    def latest(self, chat, sent):
        return self.newest.get(chat) == sent

    def receive(self, timeout):
        messages, self.inbox = self.inbox, []
        return messages

    def texts(self, action=None, chat=None):
        return [
            text
            for kind, at, _, text in list(self.log)
            if action in (None, kind) and chat in (None, at) and kind in ("send", "edit")
        ]


@pytest.mark.spec("CNV-4")
def test_said_writes_tinker_before_a_command_as_slash():
    for channel in (Recorder(), ConsoleChannel()):
        assert (
            channel.said("Build it: tinker resume abc (or tinker   amend)")
            == "Build it: /resume abc (or tinker   amend)"
        )
        assert channel.said("the tinker tool and /x/tinker task") == "the tinker tool and /x/tinker task"


@pytest.mark.spec("CNV-4")
def test_show_edits_sends_and_deletes(monkeypatch):
    channel = Recorder()
    first = channel.show("1", None, "hello", status=True)
    assert channel.log[0][:3] == ("send", "1", first[0]) and "🕒 _updated" in channel.log[0][3]
    monkeypatch.setattr("tinkerkit.chat.console.pieces", lambda text, size=3500: [text[:10], text[10:]])
    two = channel.show("1", first, "a longer message now")
    assert two[0] == first[0] and channel.log[-2][0] == "edit" and channel.log[-1][0] == "send"
    monkeypatch.setattr("tinkerkit.chat.console.pieces", lambda text, size=3500: [text])
    assert channel.show("1", two, "short") == [first[0]] and channel.log[-1] == ("delete", "1", two[1], "")


@pytest.mark.spec("CNV-4")
def test_show_done_comes_to_the_bottom():
    channel = Recorder()
    old = channel.show("1", None, "working")
    channel.send("1", "the person said something")
    new = channel.show("1", old, "done", done=True)
    assert new != old and ("delete", "1", old[0], "") in channel.log
    kept = channel.show("1", new, "done again", done=True)
    assert kept == new


@pytest.mark.spec("CNV-5")
def test_listened_filters_chats_and_says_a_refusal_once(monkeypatch, capsys):
    channel = Recorder(chats=("1",), inbox=[Message("1", "hi"), Message("2", "stranger")])
    assert [message.text for message in channel.listened(0)] == ["hi"]
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    def refused(timeout):
        raise ChannelError("409 Conflict")

    channel.receive = refused
    assert channel.listened(5) == [] and channel.listened(5) == []
    assert capsys.readouterr().out.count("409 Conflict") == 1


@pytest.mark.spec("CNV-5")
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


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
def test_console_prints_only_what_a_done_message_grew_by(monkeypatch):
    console, out = piped_console(monkeypatch)
    shown = console.show("console", None, "Done\n\nline one", done=True)
    console.show("console", shown, "Done\n\nline one\nline two", done=True)
    console.show("console", shown, "Done\n\nline one\nline two", done=True)
    assert out.getvalue().splitlines() == ["Done", "", "line one", "line two"]


@pytest.mark.spec("CNV-25")
def test_console_prints_each_new_line_of_a_growing_message_without_a_doing_line(monkeypatch):
    console, out = piped_console(monkeypatch)
    shown = console.show_growing("console", None, "⚙️ #3 calc\nline one")
    console.show_growing("console", shown, "⚙️ #3 calc\nline one\nline two\nline three")
    console.show_growing("console", shown, "⚙️ #3 calc\nline one\nline two\nline three")
    assert out.getvalue().splitlines() == ["⚙️ #3 calc", "line one", "line two", "line three"]


@pytest.mark.spec("CNV-25")
def test_console_grows_a_sent_message_by_its_new_lines_only(monkeypatch):
    console, out = piped_console(monkeypatch)
    sent = console.send("console", "⚙️ #3 calc\nline one")
    console.show_growing("console", [sent], "⚙️ #3 calc\nline one\nline two")
    console.show_growing("console", [sent], "⚙️ #3 calc\nline one\nline two\nline three")
    assert out.getvalue().splitlines() == ["⚙️ #3 calc", "line one", "line two", "line three"]


def colour_terminal(monkeypatch, columns=10):
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    resized_to(monkeypatch, columns)
    return terminal


def resized_to(monkeypatch, columns, lines=24):
    monkeypatch.setattr(
        "tinkerkit.chat.console.shutil.get_terminal_size", lambda: os.terminal_size((columns, lines))
    )


def typing(monkeypatch, *lines):
    prompts, typed = [], list(lines)

    def typed_line(prompt):
        prompts.append(prompt)
        if not typed:
            raise EOFError
        return typed.pop(0)

    monkeypatch.setattr("builtins.input", typed_line)
    pressing(monkeypatch, *[key for line in lines for key in typed_keys(line)], "eof")
    return prompts


def typed_keys(line):
    return (Text(line), "esc", "enter") if line.lstrip().startswith("/") else (Text(line), "enter")


def pressing(monkeypatch, *keys):
    pressed = iter(keys)

    def keys_left():
        yield from pressed

    monkeypatch.setattr(say, "keys", keys_left)


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


def shown_again(line, columns=10, prompt="› "):
    typed = f"{prompt}{line}"
    return f"{GREY}{typed}{' ' * (columns - visible_width(typed))}{RESET}"


def rule_drawn(columns):
    return f"{DIM}{'─' * columns}{RESET}"


def cleared_to_end(rows):
    return [f"{row}\033[K" for row in rows]


def block_and_rule(block, columns):
    return unwrapped("\033[E".join([*cleared_to_end(block), rule_drawn(columns)]))


def sent_back_shown(line, rows=1, block=(), columns=10, prompt="› "):
    shown = f"{shown_again(line, columns, prompt)}\033[E{block_and_rule(block, columns)}"
    return above_the_rule(rows + len(block) + 1, shown)


def prompt_typed(line, columns=10):
    drawer = PromptDrawer("› ")
    first = drawer.draw("", 0, [], columns)
    typed = drawer.draw(line, len(line), [], columns) if line is not None else ""
    return first + typed + drawer.done(line or "", columns)


def stacked(line, count):
    return "\n".join([line] * count)


@pytest.mark.spec("CNV-6")
def test_console_marks_each_message_and_indents_its_lines_on_a_colour_terminal(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    console = ConsoleChannel()
    console.send("console", "Done\nline one")
    shown = console.show("console", None, "Report\nline one", done=True)
    console.show("console", shown, "Report\nline one\nline two", done=True)
    assert terminal.getvalue() == (
        f"\r\033[K{MARK}Done\n  line one\n\r\033[K{MARK}Report\n  line one\n\r\033[K  line two\n"
    )


@pytest.mark.spec("CNV-6")
def test_console_shows_your_line_again_highlighted_without_ruling_each_prompt(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    Config.save(model="n")
    typing(monkeypatch, "hello there", "")
    console.read()
    assert sys.stdout.getvalue() == (
        prompt_typed("hello there")
        + sent_back_shown("hello there", rows=2)
        + prompt_typed("")
        + pulled_up(1, kept=0)
        + prompt_typed(None)
    )
    assert [console.lines.get() for _ in range(3)] == ["hello there", "", None]


def lines_keyed(monkeypatch, *keys, columns=80):
    console = ruled_console(monkeypatch, columns)
    pressing(monkeypatch, *keys)
    console.read()
    return [console.lines.get() for _ in range(console.lines.qsize())]


@pytest.mark.spec("MSG-9")
def test_console_edits_the_prompt_line_with_cursor_keys(monkeypatch):
    keys = [Text("helo"), "left", Text("l"), "end", Text(" world"), "home", "delete", Text("H"), "enter"]
    assert lines_keyed(monkeypatch, *keys) == ["Hello world", None]


@pytest.mark.spec("MSG-9")
def test_console_brings_back_earlier_lines_with_up_and_down(monkeypatch):
    readline.clear_history()
    readline.add_history("earlier")
    readline.add_history("latest")
    try:
        assert lines_keyed(monkeypatch, "up", "up", "down", "enter") == ["latest", None]
    finally:
        readline.clear_history()


@pytest.mark.spec("MSG-9")
def test_console_ctrl_c_clears_a_typed_line_and_quits_on_an_empty_one(monkeypatch):
    assert lines_keyed(monkeypatch, Text("abc"), "ctrl-c", Text("d"), "enter") == ["d", None]
    assert lines_keyed(monkeypatch, "ctrl-c", Text("never")) == [None]


@pytest.mark.spec("MSG-10")
def test_console_fills_the_command_picked_from_the_menu_and_reads_on(monkeypatch):
    picked = slash_matches("/")[1].name
    assert lines_keyed(monkeypatch, Text("/"), "down", "tab", Text("x"), "enter") == [f"/{picked} x", None]


@pytest.mark.spec("MSG-10")
def test_console_sends_a_fully_typed_command_on_enter_and_fills_a_partial_one(monkeypatch):
    assert lines_keyed(monkeypatch, Text("/runs"), "enter") == ["/runs", None]
    assert lines_keyed(monkeypatch, Text("/task"), "enter") == ["/task", None]
    assert lines_keyed(monkeypatch, Text("/tas"), "enter", Text("x"), "enter") == ["/task x", None]


@pytest.mark.spec("MSG-10")
def test_console_esc_closes_the_menu_and_keeps_the_typed_slash(monkeypatch):
    assert lines_keyed(monkeypatch, Text("/"), "esc", "enter") == ["/", None]


@pytest.mark.spec("MSG-10", "CNV-6")
def test_console_draws_the_command_menu_under_the_prompt_line(monkeypatch):
    first = slash_matches("/")[0]
    lines_keyed(monkeypatch, Text("/"))
    assert f"› /{first.name}{DIM} {synopsis_words(first)}" in sys.stdout.getvalue()


@pytest.mark.spec("MSG-10")
def test_console_draws_a_command_s_words_under_the_prompt_line(monkeypatch):
    lines_keyed(monkeypatch, Text("/model r"))
    assert "› /model recipe" in sys.stdout.getvalue()


@pytest.mark.spec("MSG-10")
def test_console_fills_or_sends_the_word_picked_from_the_menu(monkeypatch):
    assert lines_keyed(monkeypatch, Text("/model r"), "tab", Text("x"), "enter") == ["/model recipe x", None]
    assert lines_keyed(monkeypatch, Text("/model r"), "enter") == ["/model recipe", None]


@pytest.mark.spec("CNV-6")
def test_console_keeps_the_lines_typed_for_up_and_down_across_restarts(monkeypatch, tinker_home):
    colour_terminal(monkeypatch)
    monkeypatch.setattr(ConsoleScreen, "TICK_SECONDS", 60)
    typing(monkeypatch, "first", "", "second", "/exit")
    console = ConsoleChannel()
    console.receive(1)
    while console.lines.get(timeout=1) is not None:
        pass
    readline.clear_history()
    reading_console(monkeypatch)
    assert typed_before() == ["first", "second"]
    assert (tinker_home / "prompt_history").exists()


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize(("line", "columns", "rows"), [("hi", 20, 1), ("x" * 25, 10, 3)])
def test_console_shows_a_line_sent_again_right_above_the_rule_by_all_its_rows(
    monkeypatch, line, columns, rows
):
    console = ruled_console(monkeypatch, columns)
    assert drawn_after(lambda: console.show_again(line)) == sent_back_shown(line, rows, columns=columns)


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("line", ["", "  ", "/exit", " /EXIT "])
def test_console_drops_a_quiet_line_by_pulling_the_prompt_up(monkeypatch, line):
    console = ruled_console(monkeypatch)
    assert drawn_after(lambda: console.show_again(line)) == pulled_up(1, kept=0)


@pytest.mark.spec("CNV-6")
def test_console_sends_a_line_back_above_the_live_block(monkeypatch, clock):
    console = ruled_console(monkeypatch, columns=20)
    console.screen.show_live("Learning", 100.0, under=["one"])
    shown = drawn_after(lambda: console.show_again("hi"))
    assert shown == sent_back_shown("hi", block=console.screen.drawn, columns=20)


def model_picker(monkeypatch, console, chosen, models=({"name": "m"}, {"name": "n"}), embedding=()):
    shown = []

    def pick(choices, start):
        shown.append((choices, start, console.screen.printing.locked()))
        return chosen

    monkeypatch.setattr(say, "pick", pick)
    monkeypatch.setattr(
        Ollama,
        "capabilities",
        staticmethod(lambda name: ["embedding" if name in embedding else "completion"]),
    )
    monkeypatch.setattr(
        Ollama, "installed_models", staticmethod(lambda: None if models is None else list(models))
    )
    return shown


def picked_names(shown):
    return [
        ([styled(choice)[0].split()[0] for choice in choices], start, locked)
        for choices, start, locked in shown
    ]


@pytest.mark.spec("CNV-6")
def test_console_picks_the_model_for_model_typed_alone(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    Models.keep("n", MEASURED)
    Config.save(model="n")
    shown = model_picker(monkeypatch, console, 1)
    typing(monkeypatch, " /model ", "/model n", "/models", "model")
    console.read()
    assert picked_names(shown) == [(["General", "m", "n"], 2, True)]
    assert styled(shown[0][0][1])[1] is DIMMED
    assert [console.lines.get() for _ in range(5)] == ["/model m", "/model n", "/models", "model", None]


CHANNEL_ADD = "/channel add --kind telegram --name home --token abc --chat '42, 43'"


@pytest.mark.spec("CNV-6")
def test_console_adds_a_channel_in_place_and_reads_on(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    Config.save(model="n")
    every_telegram_talks_to(monkeypatch, BotAPI(getMe={"username": "my_bot"}))
    added_while_locked, warned = [], []
    keep = Channels.keep
    monkeypatch.setattr(
        Channels,
        "keep",
        staticmethod(
            lambda *args: added_while_locked.append(console.screen.printing.locked()) or keep(*args)
        ),
    )
    monkeypatch.setattr(say, "warn", warned.append)
    typing(monkeypatch, CHANNEL_ADD, "/channel add --kind pigeon", "hi")
    console.read()
    assert Channels.settings()["home"]["allowed"] == ["42", "43"] and added_while_locked == [True]
    assert warned == ["tinker: no channel kind 'pigeon' (supported: telegram)"]
    assert [console.lines.get() for _ in range(4)] == ["", "", "hi", None]


@pytest.mark.spec("CNV-6")
def test_console_gives_back_the_name_of_the_channel_it_added(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    every_telegram_talks_to(monkeypatch, BotAPI(getMe={"username": "my_bot"}))
    monkeypatch.setattr(say, "warn", lambda text: None)
    assert (
        console.channel_added(["--kind", "telegram", "--name", "home", "--token", "abc", "--chat", "42"])
        == "home"
    )
    assert console.channel_added(["--kind", "pigeon"]) is None


@pytest.mark.spec("CNV-6")
def test_piped_console_sends_channel_add_on(monkeypatch):
    console, _ = piped_console(monkeypatch)
    typing(monkeypatch, CHANNEL_ADD)
    console.read()
    assert [console.lines.get() for _ in range(2)] == [CHANNEL_ADD, None]
    assert Channels.settings() == {}


@pytest.mark.spec("CNV-6")
def test_console_picks_a_model_first_when_none_is_in_use(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    shown = model_picker(monkeypatch, console, 2)
    typing(monkeypatch, "hi")
    console.read()
    assert picked_names(shown) == [(["General", "m", "n"], 1, True)]
    assert [console.lines.get() for _ in range(3)] == ["/model n", "hi", None]


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize(
    ("saved", "pick", "picks"), [({}, None, 1), ({"model": "n"}, 1, 0)], ids=["cancelled", "model in use"]
)
def test_console_sends_only_the_typed_line_without_a_first_pick(monkeypatch, home_store, saved, pick, picks):
    console = ruled_console(monkeypatch)
    Config.save(**saved)
    shown = model_picker(monkeypatch, console, pick)
    typing(monkeypatch, "hi")
    console.read()
    assert len(shown) == picks
    assert [console.lines.get() for _ in range(2)] == ["hi", None]


@pytest.mark.spec("CNV-6")
def test_console_says_how_to_get_a_model_when_ollama_has_none(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    printed = []
    shown = model_picker(monkeypatch, console, 1, models=())
    monkeypatch.setattr(Models, "shown_recipe", staticmethod(lambda: ["recipe"]))
    monkeypatch.setattr(say, "text", printed.append)
    typing(monkeypatch, "hi")
    console.read()
    assert shown == [] and printed == ["recipe", MODEL_PULL_HINT]
    assert [console.lines.get() for _ in range(2)] == ["hi", None]


@pytest.mark.spec("CNV-6")
def test_console_warns_and_reads_on_when_ollama_is_down_at_first_run(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    warned = []
    model_picker(monkeypatch, console, 1)
    monkeypatch.setattr(
        Models, "command_choices", staticmethod(lambda command: (_ for _ in ()).throw(SystemExit("down")))
    )
    monkeypatch.setattr(say, "warn", warned.append)
    typing(monkeypatch, "hi")
    console.read()
    assert warned == ["down"]
    assert [console.lines.get() for _ in range(2)] == ["hi", None]


@pytest.mark.spec("CNV-6")
def test_console_picks_the_model_for_model_calibrate_typed_alone(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    Config.save(model="n")
    shown = model_picker(
        monkeypatch, console, 1, embedding=("e",), models=({"name": "m"}, {"name": "n"}, {"name": "e"})
    )
    typing(monkeypatch, "/model calibrate", "/model calibrate n")
    console.read()
    assert picked_names(shown) == [(["General", "m", "n"], 2, True)]
    assert not any(styled(choice)[1] is DIMMED for choice in shown[0][0])
    assert [console.lines.get() for _ in range(3)] == ["/model calibrate m", "/model calibrate n", None]


@pytest.mark.spec("CNV-6")
def test_console_prints_the_recipe_before_the_pick(monkeypatch, home_store):
    console = ruled_console(monkeypatch)
    Config.save(model="n")
    printed = []
    model_picker(monkeypatch, console, 1)
    monkeypatch.setattr(
        Models, "shown_recipe", staticmethod(lambda: ["🧠⚡ m is the smartest and the fastest"])
    )
    monkeypatch.setattr(say, "text", printed.append)
    typing(monkeypatch, "/model")
    console.read()
    assert printed == ["🧠⚡ m is the smartest and the fastest"] and console.lines.get() == "/model m"


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("command", ["model", "model calibrate"])
def test_console_sends_nothing_when_the_pick_is_cancelled(monkeypatch, home_store, command):
    console = ruled_console(monkeypatch)
    model_picker(monkeypatch, console, None)
    typing(monkeypatch, f"/{command}")
    console.read()
    assert console.lines.get() == "" and console.receive(0) == []


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("command", ["model", "model calibrate"])
@pytest.mark.parametrize("models", [(), None])
def test_console_passes_a_pick_command_on_when_there_is_nothing_to_pick(
    monkeypatch, home_store, models, command
):
    console = ruled_console(monkeypatch)
    shown = model_picker(monkeypatch, console, 0, models)
    typing(monkeypatch, f"/{command}")
    console.read()
    assert shown == [] and console.lines.get() == f"/{command}"


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("command", ["model", "model calibrate"])
def test_console_passes_a_piped_pick_command_on(monkeypatch, home_store, command):
    colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    console = ConsoleChannel()
    shown = model_picker(monkeypatch, console, 0)
    typing(monkeypatch, f"/{command}")
    console.read()
    assert shown == [] and console.lines.get() == f"/{command}"


@pytest.mark.spec("CNV-6")
def test_console_leaves_piped_input_unruled_and_unrepeated(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    prompts = typing(monkeypatch, "hello there")
    ConsoleChannel().read()
    assert prompts == [ConsoleScreen.PROMPT] * 2
    assert terminal.getvalue() == ""


@pytest.mark.spec("CNV-6")
def test_console_prints_above_the_rule_without_touching_the_prompt(monkeypatch):
    ruled_console(monkeypatch).send("console", "hi")
    assert sys.stdout.getvalue() == printed_above(f"{MARK}hi")


@pytest.mark.spec("CNV-6")
def test_what_tinker_says_while_you_type_prints_above_the_prompt(monkeypatch):
    reading_console(monkeypatch)
    said = drawn_after(lambda: say.text("[12:00:00] tinker_bot: hello"))
    assert said == printed_above("[12:00:00] tinker_bot: hello", rows=3)


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("under", [[], UNDER], ids=["live-line", "with-lines-under"])
def test_console_shows_its_live_block_dim_and_indented_above_the_rule_and_prompt(monkeypatch, clock, under):
    screen = live_screen(monkeypatch)
    shown = drawn_after(lambda: screen.show_live("Learning", 100.0, under=under))
    assert shown == block_grown(1 + len(under)) + block_changed(block_drawn("Learning", under))


@pytest.mark.spec("CNV-6")
def test_console_changes_its_live_line_in_place_without_touching_what_you_type(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Writing tests", 100.0)
    clock[0] += 64
    changed = drawn_after(lambda: screen.show_live("Running tests", 100.0))
    assert changed == live_changed("Running tests", "1m 4s")


@pytest.mark.spec("CNV-6")
def test_console_changes_its_live_block_in_place_and_turns_its_star_and_glow_on_each_tick(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=UNDER)
    clock[0] += 1
    changed = drawn_after(lambda: screen.show_live("Learning", 100.0, under=["📊 4 page(s) read", "…"]))
    assert changed == block_changed(block_drawn("Learning", ["📊 4 page(s) read", "…"], "1s"))
    assert drawn_after(screen.tick) == block_changed(
        block_drawn("Learning", ["📊 4 page(s) read", "…"], "1s", star="✶", word=GLOWING_T)
    )


@pytest.mark.spec("CNV-6")
def test_the_live_block_ticks_about_eight_times_a_second():
    assert round(1 / ConsoleScreen.TICK_SECONDS) == 8


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize(
    ("before", "after", "moved"),
    [
        (UNDER, ("Writing", []), block_shrunk(2, rows_left=1) + live_changed("Writing")),
        ([], ("Learning", UNDER), block_grown(2) + block_changed(block_drawn("Learning", UNDER))),
    ],
    ids=["shrinking pulls up", "growing pushes down"],
)
def test_a_live_block_changing_size_moves_the_rule_and_prompt(monkeypatch, clock, before, after, moved):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=before)
    text, under = after
    assert drawn_after(lambda: screen.show_live(text, 100.0, under=under)) == moved


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("under", [[], UNDER], ids=["live-line", "with-lines-under"])
def test_console_prints_above_its_live_block_and_takes_it_out_for_good(monkeypatch, clock, under):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=under)
    rows = 1 + len(under)
    assert drawn_after(lambda: screen.print("hello")) == printed_above("hello", block_rows=rows)
    assert drawn_after(screen.end_live) == block_taken_out(rows)
    assert screen.under == []
    assert drawn_after(screen.tick) == "" and drawn_after(screen.end_live) == ""


@pytest.mark.spec("CNV-6")
def test_the_lines_under_the_live_line_are_cut_to_the_terminal_width(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    screen.show_live("Learning", 100.0, under=["x" * 60])
    assert under_drawn("x" * (LIVE_COLUMNS - 3)) + "\033[K" in sys.stdout.getvalue()


def menu_rows():
    return [MenuRow(True, "/ask", "QUESTION", "answers a question"), MenuRow(False, "/clear", "", "clears")]


@pytest.mark.spec("CNV-6")
def test_prompt_drawer_draws_the_line_and_puts_the_cursor_at_the_column():
    drawer = PromptDrawer("› ")
    assert drawer.draw("hello", 2, [], 80) == "\r\033[J› hello\r\033[4C"
    assert drawer.cursor_row == 0


@pytest.mark.spec("MSG-10")
def test_prompt_drawer_draws_the_menu_rows_under_the_line_and_goes_back_up():
    drawer = PromptDrawer("› ")
    drawn = drawer.draw("/", 1, menu_rows(), 80)
    rows = "\r\n› /ask QUESTION  answers a question\r\n  /clear  clears"
    assert drawn == "\r\033[J› /" + rows + "\033[2A\r\033[3C"


@pytest.mark.spec("MSG-10")
def test_prompt_drawer_cuts_menu_rows_to_one_less_than_the_width():
    drawn = PromptDrawer("› ").draw("/", 1, menu_rows(), 12)
    assert "\r\n› /ask QUES\r\n" in drawn and "\r\n  /clear  c\033" in drawn


@pytest.mark.spec("MSG-10")
def test_prompt_drawer_dims_the_words_on_a_colour_terminal(monkeypatch):
    colour_terminal(monkeypatch)
    drawn = PromptDrawer("› ").draw("/", 1, menu_rows()[:1], 80)
    assert f"› /ask{DIM} QUESTION{RESET}  answers a question" in drawn


@pytest.mark.spec("CNV-6")
def test_prompt_drawer_follows_a_wrapped_line_and_starts_the_next_draw_from_its_top():
    drawer = PromptDrawer("› ")
    assert drawer.draw("x" * 12, 12, [], 10) == "\r\033[J› " + "x" * 12 + "\r\033[4C"
    assert drawer.cursor_row == 1
    assert drawer.draw("x", 1, [], 10).startswith("\033[1A\r\033[J› x")


@pytest.mark.spec("CNV-6")
def test_prompt_drawer_done_drops_the_menu_and_moves_below_the_line():
    drawer = PromptDrawer("› ")
    drawer.draw("x" * 12, 12, menu_rows(), 10)
    assert drawer.done("x" * 12, 10) == "\033[1A\r\033[J› " + "x" * 12 + "\r\n"
    assert drawer.cursor_row == 0


@pytest.mark.spec("CNV-6")
def test_prompt_drawer_taken_away_clears_from_the_line_top_and_draws_next_from_there():
    drawer = PromptDrawer("› ")
    drawer.draw("x" * 12, 12, menu_rows(), 10)
    assert drawer.taken_away() == "\033[1A\r\033[J"
    assert drawer.cursor_row == 0
    assert drawer.draw("x", 1, [], 10).startswith("\r\033[J› x")


def typing_on(screen, text="hi", rows=()):
    drawer = PromptDrawer("› ")
    drawer.draw(text, len(text), list(rows), 10)
    screen.typing = (drawer, lambda: drawer.draw(text, len(text), list(rows), 10))


def prompt_again(text="hi", rows=()):
    return PromptDrawer("› ").draw(text, len(text), list(rows), 10)


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("rows", [[], menu_rows()], ids=["line", "with-menu"])
def test_a_print_while_typing_takes_the_prompt_away_and_draws_it_again(monkeypatch, rows):
    screen = ruled_screen(monkeypatch)
    typing_on(screen, rows=rows)
    printed = drawn_after(lambda: screen.print("hello"))
    assert printed == "\r\033[J" + printed_above("hello") + prompt_again(rows=rows)


@pytest.mark.spec("CNV-6")
def test_a_resize_while_typing_takes_the_prompt_away_before_drawing_the_rule_again(monkeypatch):
    screen = ruled_screen(monkeypatch)
    typing_on(screen)
    resized_to(monkeypatch, 5)
    printed = drawn_after(lambda: screen.print("hello"))
    assert printed.startswith("\r\033[J" + RULE_REDRAWN_NARROWER)


@pytest.mark.spec("CNV-6")
def test_the_live_block_while_typing_draws_the_prompt_again_and_a_quiet_tick_draws_nothing(
    monkeypatch, clock
):
    screen = ruled_screen(monkeypatch)
    typing_on(screen)
    assert drawn_after(screen.tick) == ""
    shown = drawn_after(lambda: screen.show_live("Learning", 100.0))
    assert shown.startswith("\r\033[J") and shown.endswith(prompt_again())


@pytest.mark.spec("CNV-6")
def test_a_print_from_elsewhere_while_typing_keeps_the_typed_text_and_ends_typing(monkeypatch):
    console = ruled_console(monkeypatch)

    def keys_left():
        yield Text("hi")
        printed.append(drawn_after(lambda: console.screen.print("hello")))
        yield "enter"

    printed = []
    monkeypatch.setattr(say, "keys", keys_left)
    assert console.typed_line() == "hi"
    assert printed == ["\r\033[J" + printed_above("hello") + prompt_again()]
    assert console.screen.typing is None


@pytest.fixture
def started_screen(monkeypatch):
    resize_handler = signal.getsignal(signal.SIGWINCH)

    def started():
        colour_terminal(monkeypatch)
        monkeypatch.setattr(ConsoleScreen, "TICK_SECONDS", 60)
        ConsoleScreen().start()

    yield started
    signal.signal(signal.SIGWINCH, resize_handler)


@pytest.mark.spec("CNV-6")
def test_console_starts_by_drawing_the_rule_where_the_cursor_is(started_screen):
    started_screen()
    assert sys.stdout.getvalue() == unwrapped(rule_drawn(10)) + "\n"


def stale_rows_cleared(rows, typed_rows=0):
    return above_the_rule(rows + typed_rows, "\033[E".join(["\033[K"] * rows))


def redrawn_with_rule(block, columns, typed_rows=0):
    return above_the_rule(len(block) + 1 + typed_rows, block_and_rule(block, columns))


RULE_REDRAWN_NARROWER = stale_rows_cleared(2) + pulled_up(1, kept=1) + redrawn_with_rule([], 5)


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
def test_a_resize_signal_draws_the_rule_again_without_waiting_for_the_tick(monkeypatch, started_screen):
    started_screen()
    resized_to(monkeypatch, 5)
    signal.raise_signal(signal.SIGWINCH)
    assert redrawn_with_rule([], 5) in drawn_soon(redrawn_with_rule([], 5))


@pytest.mark.spec("CNV-6")
def test_a_print_right_after_a_resize_draws_the_rule_again_first(monkeypatch):
    screen = ruled_screen(monkeypatch)
    resized_to(monkeypatch, 5)
    printed = drawn_after(lambda: screen.print("hello"))
    assert printed == RULE_REDRAWN_NARROWER + printed_above("hello")


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
def test_an_unruled_screen_does_nothing_when_resized(monkeypatch):
    screen = ruled_screen(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    resized_to(monkeypatch, 5)
    assert drawn_after(screen.tick) == ""


@pytest.mark.spec("CNV-6")
def test_console_without_colour_shows_no_live_line(monkeypatch, clock):
    screen = live_screen(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    screen.show_live("Writing tests", 100.0)
    screen.tick()
    screen.end_live()
    assert sys.stdout.getvalue() == ""


@pytest.mark.spec("CNV-6")
def test_once_the_console_reads_live_draws_in_its_live_line(monkeypatch, clock):
    reading_console(monkeypatch, LIVE_COLUMNS)
    assert drawn_after(lambda: say.live("Writing tests")) == live_shown("Writing tests")
    said = drawn_after(lambda: say.text("Answered"))
    assert said == block_taken_out(1) + printed_above("Answered")


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
def test_console_shows_a_message_without_a_doing_line_in_its_live_line_until_done(monkeypatch, clock):
    console = live_console(monkeypatch)
    shown = console.show("console", None, "⏳ **Getting ready**\nI answer soon.")
    assert sys.stdout.getvalue() == live_shown("⏳ Getting ready")
    done = drawn_after(lambda: console.show("console", shown, "Answer", done=True))
    assert done == block_taken_out(1) + printed_above(f"{MARK}Answer")


@pytest.mark.spec("CNV-25")
def test_console_prints_a_growing_messages_new_lines_above_the_rule_without_a_live_line(monkeypatch, clock):
    console = live_console(monkeypatch)
    shown = console.show_growing("console", None, "⚙️ #3 calc\nline one")
    assert sys.stdout.getvalue() == printed_above(f"{MARK}⚙️ #3 calc\n  line one", rows=2)
    grew = drawn_after(lambda: console.show_growing("console", shown, "⚙️ #3 calc\nline one\nline two"))
    assert grew == printed_above("  line two")
    assert console.live_owner is None


@pytest.mark.spec("CNV-6")
def test_a_done_message_leaves_a_live_line_it_does_not_own(monkeypatch, clock):
    console = live_console(monkeypatch)
    console.show("console", None, CALC_WRITING_NOW, doing="Writing")
    done = drawn_after(lambda: console.show("console", None, "Answer", done=True))
    assert done == printed_above(f"{MARK}Answer", block_rows=1)
    assert console.screen.live == ("Writing", 100.0, {})


@pytest.mark.spec("CNV-6")
def test_a_done_message_forgets_how_it_went(monkeypatch, clock):
    console = live_console(monkeypatch)
    shown = console.show("console", None, CALC_WRITING_NOW, doing="Writing")
    console.show("console", shown, "🏁 calc", done=True)
    assert console.began == {} and console.doings == {} and console.live_owner is None


@pytest.mark.spec("CNV-6")
def test_a_jobs_live_line_counts_from_when_the_job_began(monkeypatch, clock):
    monkeypatch.setattr(time, "time", lambda: 5000.0)
    console = live_console(monkeypatch)
    console.show("console", None, CALC_WRITING_NOW, began=4936.0, doing="Writing")
    assert console.screen.live == ("Writing", 36.0, {})


@pytest.mark.spec("CNV-6")
def test_a_jobs_live_line_shows_its_tokens_and_thinking(monkeypatch, clock):
    console = live_console(monkeypatch)
    line = "🔨 calc\n✻ Tinkering… (↓ 1.2k tokens · thinking) · **Writing**"
    console.show("console", None, line, doing="Writing", tokens=1200, thinking=True, thought_seconds=None)
    facts = {"tokens": 1200, "thinking": True, "thought_seconds": None}
    assert console.screen.live == ("Writing", 100.0, facts)
    assert f"Tinkering…{RESET} (0s · ↓ 1.2k tokens" in sys.stdout.getvalue()


@pytest.mark.spec("CNV-6")
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


@pytest.mark.spec("CNV-6")
def test_console_frames_the_banner_as_wide_as_the_terminal_up_to_64_columns(monkeypatch):
    terminal = colour_terminal(monkeypatch, 80)
    ConsoleChannel().banner(["🔧 Tinker", "model qwen3", "help lists commands"])
    assert terminal.getvalue() == framed(
        64, ("🔧 Tinker", BOLD, 9), ("model qwen3", DIM, 11), ("help lists commands", DIM, 19)
    )


@pytest.mark.spec("CNV-6")
def test_console_cuts_banner_text_that_is_too_long(monkeypatch):
    terminal = colour_terminal(monkeypatch, 14)
    ConsoleChannel().banner(["🔧 Tinker tool", "help lists commands"])
    assert terminal.getvalue() == framed(14, ("🔧 Tinker", BOLD, 9), ("help lists", DIM, 10))


@pytest.mark.spec("CNV-6")
def test_console_draws_no_banner_without_colour_or_a_terminal(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ConsoleChannel().banner(["🔧 Tinker"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    ConsoleChannel().banner(["🔧 Tinker"])
    assert terminal.getvalue() == ""


@pytest.mark.spec("CNV-6")
def test_console_receives_lines_until_exit():
    console = ConsoleChannel()
    console.screen.reading = True
    for line in ("hello", "  ", "exit", " /Exit "):
        console.lines.put(line)
    assert [message.text for message in console.receive(0)] == ["hello"]
    assert console.receive(0) == [] and not console.closed
    assert [message.text for message in console.receive(0)] == ["exit"] and not console.closed
    assert console.receive(0) == [] and console.closed
    eof = ConsoleChannel()
    eof.screen.reading = True
    eof.lines.put(None)
    assert eof.receive(0) == [] and eof.closed


def test_a_channel_without_photos_sends_them_as_files(tmp_path):
    channel = Channel("plain", {}, {})
    seen = []
    channel.send = lambda chat, text: seen.append((chat, text))
    channel.send_photo("1", tmp_path / "a.png", "a.png")
    assert seen == [("1", f"a.png\n📄 `{tmp_path / 'a.png'}`")]


@pytest.mark.spec("CNV-9")
def test_a_chat_shows_its_typing_sign_while_it_waits():
    channel = Recorder(chats=("1",))
    with channel.waiting("1", "Running /knowledge"):
        wait_until(lambda: ("typing", "1", None, "") in channel.log)


@pytest.mark.spec("CNV-9")
def test_console_waits_in_its_live_line_and_takes_it_out_after(monkeypatch, clock):
    console = live_console(monkeypatch)
    with console.waiting("console", "Running /knowledge"):
        assert sys.stdout.getvalue() == live_shown("Running /knowledge")
        sys.stdout.truncate(0)
        sys.stdout.seek(0)
    assert sys.stdout.getvalue() == block_taken_out(1) and console.live_owner is None


@pytest.mark.spec("CNV-9")
def test_console_waits_without_taking_a_jobs_live_line(monkeypatch, clock):
    console = live_console(monkeypatch)
    console.show("console", None, CALC_WRITING_NOW, doing="Writing")
    with console.waiting("console", "Running /knowledge"):
        assert console.screen.live == ("Writing", 100.0, {})
    assert console.screen.live == ("Writing", 100.0, {})


@pytest.mark.spec("CNV-6")
def test_console_wipes_the_screen_and_scrollback_only_in_a_colour_terminal(monkeypatch):
    terminal = colour_terminal(monkeypatch)
    ConsoleChannel().wipe()
    assert terminal.getvalue() == "\033[H\033[2J\033[3J"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ConsoleChannel().wipe()
    assert terminal.getvalue() == "\033[H\033[2J\033[3J"


@pytest.mark.spec("CNV-23")
def test_the_terminal_opens_the_file_and_other_channels_send_where_it_is(monkeypatch, tmp_path):
    opened, printed = [], []
    monkeypatch.setattr("tinkerkit.chat.console.webbrowser.open", opened.append)
    console = ConsoleChannel()
    monkeypatch.setattr(console.screen, "print", printed.append)
    page = tmp_path / "knowledge.html"
    console.send_file("console", page, "🧠 **Tinker's memory**")
    assert opened == [page.as_uri()] and f"Opened in your browser: {page}" in Markup.plain(printed[0])
    plain = Recorder()
    plain.send_file("1", page, "🧠 **Tinker's memory**")
    assert plain.texts() == [f"🧠 **Tinker's memory**\n📄 `{page}`"]


@pytest.mark.spec("SET-11")
def test_console_sends_the_next_page_on_an_empty_line_right_after_offering_it(monkeypatch):
    colour_terminal(monkeypatch)
    console = ConsoleChannel()
    printed = []
    monkeypatch.setattr(console.screen, "print", printed.append)
    console.offer_next("console", 1, "read list 2")
    assert DIM in printed[-1] and "↵ next page" in printed[-1]
    assert console.answered("") == "/read list 2"
    assert console.answered("") == ""
    console.offer_next("console", 2, "read list 3")
    assert console.answered("hello") == "hello"
    assert console.answered("") == ""


@pytest.mark.spec("CNV-26")
def test_console_reads_a_dropped_file_path(tmp_path):
    dropped = tmp_path / "my notes.txt"
    dropped.write_text("notes")
    console = ConsoleChannel()
    assert console.answered(f"'{dropped}'") == f"/read '{dropped}'"
    assert console.answered(str(dropped).replace(" ", "\\ ") + " ") == f"/read '{dropped}'"
    assert console.lines.empty()


@pytest.mark.spec("CNV-26")
def test_console_answers_words_after_a_dropped_path_inside_its_reading(tmp_path):
    dropped = tmp_path / "plain.txt"
    dropped.write_text("notes")
    console = ConsoleChannel()
    assert console.answered(f"{dropped} what does it say?") == "what does it say?"
    assert console.lines.get_nowait() == f"/read {dropped}"


@pytest.mark.spec("CNV-26")
def test_console_passes_on_a_line_that_names_no_file(tmp_path):
    console = ConsoleChannel()
    assert console.answered(f"{tmp_path / 'gone.txt'} hi") == f"{tmp_path / 'gone.txt'} hi"
    assert console.answered("it's 'open") == "it's 'open"
    assert console.lines.empty()
