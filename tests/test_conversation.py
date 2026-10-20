"""CNV: the conversation: Markup, channels, a task's progress, a chat, a command line, research and the Desk."""

import io
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import tinkerkit.conversation as conversation
from tinkerkit.conversation import (
    Channel, ChannelError, ConsoleChannel, Desk, LineWatcher, Markup, Message, Progress, Reporter, Research, Shell, Talk,
    after_word, built_project, command_of, first_lines, first_words, json_in, pieces, running_elsewhere, text_of,
)
from tinkerkit.project import Runs
from conftest import MODEL


def wait_until(condition, timeout=15):
    ends = time.monotonic() + timeout
    while time.monotonic() < ends:
        if condition():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


class Recorder(Channel):
    """A chat channel that keeps what it was asked to do: [(action, chat, id, text)]."""
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

    def pin(self, chat, sent):
        self.log.append(("pin", chat, sent, ""))

    def typing(self, chat):
        self.log.append(("typing", chat, None, ""))

    def latest(self, chat, sent):
        return self.newest.get(chat) == sent

    def receive(self, timeout):
        messages, self.inbox = self.inbox, []
        return messages

    def texts(self, action=None):
        return [text for kind, _, _, text in list(self.log) if action in (None, kind) and kind in ("send", "edit")]


@pytest.mark.spec("CNV-1")
def test_markup_plain():
    text = "**bold**, *it*, _also_, ~~gone~~, `x_y*`, [docs](https://x.org/a_b), my_var_name, \\*lit\\*"
    assert Markup.plain(text) == "bold, it, also, gone, x_y*, docs (https://x.org/a_b), my_var_name, *lit*"
    assert Markup.plain("# Title\n- one\n* two") == "Title\n• one\n• two"
    assert Markup.plain("```python\nprint(1)\n```") == "print(1)"


@pytest.mark.spec("CNV-1")
def test_markup_html():
    assert Markup.html("**b** & <x> `a<b` [l](https://x.org/?a=1&b=2)") == \
        '<b>b</b> &amp; &lt;x&gt; <code>a&lt;b</code> <a href="https://x.org/?a=1&amp;b=2">l</a>'
    assert Markup.html("# Title\n- a") == "<b>Title</b>\n• a"
    assert Markup.html("```sh\nls -l\n```") == "<pre>ls -l</pre>"
    assert Markup.html("```output\n1 & 2\n```") == "<blockquote expandable>1 &amp; 2</blockquote>"
    assert Markup.html("~~old~~ *new*") == "<s>old</s> <i>new</i>"


@pytest.mark.spec("CNV-1")
def test_markup_ansi_and_literal(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert Markup.ansi("**b**") == "b"  # not a terminal: plain
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.setattr(sys, "stdout", tty)
    assert Markup.ansi("**b** `c`") == "\033[1mb\033[22m \033[36mc\033[39m"
    output = Markup.ansi("```output\n" + "\n".join(str(i) for i in range(40)) + "\n```")
    assert "\033[2m  │ 29\033[22m" in output and "… 10 more lines" in output and "│ 30" not in output
    monkeypatch.setenv("NO_COLOR", "1")
    assert Markup.ansi("**b**") == "b"
    assert Markup.literal("a*b_c`d~e\\f") == "a\\*b\\_c\\`d\\~e\\\\f"
    assert Markup.plain(Markup.literal("**not bold**")) == "**not bold**"
    assert Markup.fenced("a ``` b") == "a ˋˋˋ b"


@pytest.mark.spec("CNV-2")
def test_columns():
    table = ("NAME                 ID              SIZE      MODIFIED\n"
             "qwen3:8b             500a1f067a9f    5.2 GB    2 days ago\n"
             "llama3.2:latest      a80c4f17acd5    2.0 GB    3 weeks ago")
    assert Markup.columns(table) == ("NAME | ID | SIZE | MODIFIED\nqwen3:8b | 500a1f067a9f | 5.2 GB | 2 days ago\n"
                                     "llama3.2:latest | a80c4f17acd5 | 2.0 GB | 3 weeks ago")
    for text in ("just some words here\nand more words there", "one line only with words", "a\nb"):
        assert Markup.columns(text) == text


@pytest.mark.spec("CNV-3")
def test_pieces_split_between_lines():
    text = "\n".join(f"line {i:03}" for i in range(100))
    parts = pieces(text, size=100)
    assert all(len(p) <= 100 for p in parts) and "\n".join(parts) == text
    assert pieces("") == [] and pieces("one") == ["one"]


@pytest.mark.spec("CNV-3")
@pytest.mark.xfail(strict=True, reason="ISSUE-2: a line longer than the size is cut and the rest lost")
def test_pieces_lose_no_text_of_a_long_line():
    text = "short\n" + "x" * 250 + "\nend"
    assert "".join("".join(pieces(text, size=100)).split()) == "".join(text.split())


@pytest.mark.spec("CNV-4")
@pytest.mark.parametrize("text,command", [("status", "status"), ("/Status", "status"), ("status abc123", "status"),
                                          ("status of my build please", ""), ("task a calculator", "task"),
                                          ("models are getting better", ""), ("models", "models"), ("hello", ""),
                                          ("", ""), ("confirm", "confirm"), ("confirm abc123", "confirm"),
                                          ("research the new python", "research")])
def test_command_of(text, command):
    assert command_of(text, Desk.COMMANDS) == command


@pytest.mark.spec("CNV-5")
def test_said_removes_tinker_before_a_command():
    channel = Recorder()
    assert channel.said("Build it: tinker resume abc (or tinker   amend)") == "Build it: resume abc (or tinker   amend)"
    assert channel.said("the tinker tool and /x/tinker task") == "the tinker tool and /x/tinker task"
    channel.bare = False
    assert channel.said("tinker resume abc") == "tinker resume abc"


@pytest.mark.spec("CNV-5")
def test_show_edits_sends_deletes_and_pins(monkeypatch):
    channel = Recorder()
    channel.pins = True
    first = channel.show("1", None, "hello", pin=True)
    assert channel.log[0][:3] == ("send", "1", first[0]) and "🕒 _updated" in channel.log[0][3]
    assert ("pin", "1", first[0], "") in channel.log
    monkeypatch.setattr(conversation, "pieces", lambda text, size=3500: [text[:10], text[10:]])
    two = channel.show("1", first, "a longer message now")
    assert two[0] == first[0] and channel.log[-2][0] == "edit" and channel.log[-1][0] == "send"
    monkeypatch.setattr(conversation, "pieces", lambda text, size=3500: [text])
    assert channel.show("1", two, "short") == [first[0]] and channel.log[-1] == ("delete", "1", two[1], "")


@pytest.mark.spec("CNV-5")
def test_show_done_comes_to_the_bottom():
    channel = Recorder()
    old = channel.show("1", None, "working")
    channel.send("1", "the person said something")
    new = channel.show("1", old, "done", done=True)
    assert new != old and ("delete", "1", old[0], "") in channel.log
    kept = channel.show("1", new, "done again", done=True)
    assert kept == new


@pytest.mark.spec("CNV-6")
def test_listened_filters_chats_and_says_a_refusal_once(monkeypatch, capsys):
    channel = Recorder(chats=("1",), inbox=[Message("1", "hi"), Message("2", "stranger")])
    assert [m.text for m in channel.listened(0)] == ["hi"]
    monkeypatch.setattr(conversation.time, "sleep", lambda s: None)

    def refused(timeout):
        raise ChannelError("409 Conflict")
    channel.receive = refused
    assert channel.listened(5) == [] and channel.listened(5) == []
    assert capsys.readouterr().out.count("409 Conflict") == 1


@pytest.mark.spec("CNV-6")
def test_only_one_process_claims_a_channel():
    one, two = Recorder("bot"), Recorder("bot")
    assert one.claim({"pid": 123}) is True
    try:
        assert two.claim({"pid": 456}) is False and two.holder() == {"pid": 123}
    finally:
        one.claimed.close()
    assert Recorder("other").holder() == {}


@pytest.mark.spec("CNV-7")
def test_console_shows_headlines_then_the_whole_message(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    console = ConsoleChannel()
    shown = console.show("console", None, "🔨 **calc**\n✅ step one\n▸ **Writing the code** · ⏱ 3s\n… detail")
    console.show("console", shown, "🔨 **calc**\n✅ step one\n▸ **Writing the code** · ⏱ 9s\n… other detail")
    console.show("console", shown, "🔨 **calc**\n▸ **Verifying** · ⏱ 1s")
    assert out.getvalue().splitlines() == ["⋯ 🔨 calc · ▸ Writing the code", "⋯ 🔨 calc · ▸ Verifying"]
    console.show("console", shown, "Done\nline one", done=True)
    console.show("console", shown, "Done\nline one\nline two", done=True)
    assert out.getvalue().splitlines()[2:4] == ["Done", "line one"]
    assert out.getvalue().splitlines()[4].startswith("line two")


@pytest.mark.spec("CNV-7")
def test_console_receives_lines_until_exit():
    console = ConsoleChannel()
    console.reader = object()  # no input thread: lines are queued by hand
    for line in ("hello", "  ", "exit"):
        console.lines.put(line)
    assert [m.text for m in console.receive(0)] == ["hello"]
    assert console.receive(0) == [] and not console.closed
    assert console.receive(0) == [] and console.closed
    eof = ConsoleChannel()
    eof.reader = object()
    eof.lines.put(None)
    assert eof.receive(0) == [] and eof.closed


RUN_LINES = ["Run abc123 (new): a calculator", "Project: /tmp/calc", "model fake (calibrated x)",
             "Recall (what Tinker has learned)", "  nothing learned yet", "Plan: the spec (project)",
             "  ... Spec (attempt 1) [think high]: 120 tok written, 0 tok thinking, 14s",
             "  language: Python; 2 criteria", "Verify (until every test passes)",
             "[10:00:01      1s (1.00s)] tests: 1/2 criteria pass", "  C2 passes (2/2 pass)"]


@pytest.mark.spec("CNV-8")
def test_progress_follows_the_steps():
    progress = Progress("calc: a calculator")
    changed = [progress.read(line) for line in RUN_LINES]
    assert changed[:4] == [False, False, False, True] and changed[6] is True
    assert progress.steps == ["🧠 Recalled what Tinker has learned", "📝 Spec written"]
    assert progress.phase == "🔁 Fixing until every test passes" and progress.passing == "2/2 spec points pass"
    assert progress.latest == "" and progress.live == ""  # a step starts afresh; pass counts are kept apart
    assert progress.read("[10:00:02      1s (1.00s)]   Fix the code C1: changed calc.py")
    assert progress.latest == "Fix the code C1: changed calc.py"
    text = progress.text()
    assert text.startswith("🔨 **calc: a calculator**\n✅ 🧠 Recalled") and "▸ **🔁 Fixing until every test passes** · ⏱" in text
    assert "📊 2/2 spec points pass" in text
    assert progress.brief() == "**calc: a calculator** (🔁 Fixing until every test passes, 2/2 spec points pass)"


@pytest.mark.spec("CNV-8")
def test_progress_ending_of_a_done_run():
    progress = Progress("calc", before=60)
    for line in ["=" * 60, "Done. All 2 spec tests pass (SPEC.md).", "Run it: cd /tmp/calc && python3 calc.py",
                 'Add a feature with: tinker add abc "..."; fix a bug with: tinker fix abc "..."',
                 "Log: /tmp/calc/.tinker/log.jsonl", "Total execution time: 30.00s (30s)"]:
        progress.read(line)
    assert progress.seconds == 30.0 and progress.took() == "1m 30s (90 seconds)" and not progress.waiting()
    assert progress.ending().splitlines() == [
        "🏁 **calc**", "✅ **Done. All 2 spec tests pass (SPEC.md).**", "⏱ **Done in 1m 30s (90 seconds)**",
        "▶️ Run it: `python3 calc.py`", '➕ Add a feature: `tinker add abc "..."` · 🐞 fix a bug: `tinker fix abc "..."`']


@pytest.mark.spec("CNV-8")
def test_progress_ending_of_a_spec_that_waits():
    progress = Progress("calc")
    for line in ["=" * 60, "📋 Spec for calc (run abc123): Python; a person runs it with: python3 calc.py",
                 "What changed:", "  + C3 calc.sub(5, 3) is 2", "  - adds", "Tested before any code is written:",
                 "  C1  calc.add(2, 3) is 5", "Not included: a GUI", "Its folder: /tmp/x/calc   (rename it: ...)",
                 "Build it: tinker resume abc123", "Change it: tinker amend abc123 CHANGE"]:
        progress.read(line)
    assert progress.waiting()
    lines = progress.ending().splitlines()
    assert lines[:2] == ["📋 **Spec for calc** · run `abc123`", "🧰 Python · ▶️ `python3 calc.py`"]
    for line in ("✏️ **What changed**", "➕ **C3** calc.sub(5, 3) is 2", "➖ ~~adds~~", "**C1** calc.add(2, 3) is 5",
                 "🚫 _Not included:_ a GUI", "📁 `calc`", "👉 `confirm` to build it · `amend WHAT` to change it"):
        assert line in lines, line
    assert not any("Change it" in line for line in lines) and lines[-1].startswith("⏱ _so far:")


@pytest.mark.spec("CNV-9")
def test_reporter_edits_one_message_and_ends_with_the_summary(tmp_path):
    channel = Recorder(chats=("1", "2"))
    shared = tmp_path / "running" / "1.json"
    reporter = Reporter([channel], "calc", shared=shared)
    reporter.line("Recall (what Tinker has learned)")
    assert len(channel.texts("send")) == 2  # one message per chat, at once
    reporter.line("Plan: the spec")  # within EDIT_EVERY: shown later
    assert len(channel.log) == 2 and reporter.pending
    assert json.loads(shared.read_text())["title"] == "calc"
    reporter.progress.read("=" * 60)
    reporter.progress.read("Done. All 1 spec tests pass (SPEC.md).")
    reporter.finish()
    assert reporter.ended and "✅ **Done. All 1 spec tests pass (SPEC.md).**" in channel.texts()[-1]
    assert set(reporter.sent) == {("rec", "1"), ("rec", "2")}


@pytest.mark.spec("CNV-9")
def test_a_quiet_reporter_ends_without_a_summary():
    channel = Recorder()
    reporter = Reporter([channel], "calc", keep="📋 the confirmed spec")
    reporter.quiet = True
    reporter.progress.read("=" * 60)
    reporter.progress.read("Interrupted.")
    reporter.finish()
    assert "Interrupted" not in channel.texts()[-1] and channel.texts()[-1].startswith("📋 the confirmed spec\n\n🔨")


@pytest.mark.spec("CNV-10")
def test_line_watcher():
    seen, stream = [], io.StringIO()

    def see(line):
        seen.append(line)
        raise RuntimeError("a channel failing")
    watcher = LineWatcher(stream, see)
    assert watcher.write("one\ntw") == 6
    watcher.write("o\rthree\n")
    assert stream.getvalue() == "one\ntwo\rthree\n" and seen == ["one", "two", "three"]
    assert watcher.getvalue() == stream.getvalue()


@pytest.mark.spec("CNV-11")
def test_talk_model_needs_a_measured_model(measured_model, tinker_home):
    from tinkerkit.settings import Config
    assert Talk.model() == (MODEL, {"num_ctx": 32768, "num_predict": 1024}, False)
    Config.load()["model"] = None
    assert Talk.model() is None


@pytest.mark.spec("CNV-11")
def test_talk_reply(measured_model, fake_ollama, monkeypatch):
    monkeypatch.setattr(conversation, "CHAT_KEPT", 4)
    talk, channel = Talk("task; status"), Recorder()
    for i in range(3):
        fake_ollama.reply(f"answer {i}")
        assert talk.reply(channel, Message("1", f"question {i}")) == f"answer {i}"
    messages = fake_ollama.calls[-1]["messages"]
    assert messages[0]["role"] == "system" and "task; status" in messages[0]["content"]
    assert [m["content"] for m in messages[1:]] == ["answer 0", "question 1", "answer 1", "question 2"]
    assert fake_ollama.calls[-1]["think"] is False and fake_ollama.calls[-1]["options"]["num_predict"] == 1024
    from tinkerkit.model import OllamaError
    fake_ollama.reply(OllamaError("runner failed"))
    assert talk.reply(channel, Message("1", "x")).startswith("⚠️ **The model did not answer:**")


@pytest.mark.spec("CNV-11")
def test_talk_without_a_model_says_so():
    assert Talk("cmds").reply(Recorder(), Message("1", "hi")).startswith("⚠️ **No model is in use yet**")
    assert "Busy** with **calc**" in Talk.busy_note([{"brief": "**calc**"}])
    assert "busy or loading" in Talk.busy_note([])


@pytest.mark.spec("CNV-12")
def test_shell_proposes_one_command(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd()), Recorder()
    fake_ollama.reply('{"command": "rm -rf build", "does": "deletes the build folder"}')
    shown = shell.propose(channel, Message("1", "clean up"), "", 300)
    assert "```\nrm -rf build\n```" in shown and "It changes or deletes things" in shown
    proposed = shell.take(channel, "1")
    assert proposed == {"line": "rm -rf build", "asked": "clean up", "shown": None} and shell.take(channel, "1") is None
    fake_ollama.reply('{"command": "ls\\nrm x", "does": "two"}')
    assert shell.propose(channel, Message("1", "x"), "", 300).startswith("🤷 **No command found:**")
    fake_ollama.reply("{}")
    assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300)
    fake_ollama.reply('{"command": "df -h", "does": "shows the free space"}')
    assert "It changes" not in shell.propose(channel, Message("1", "disk"), "", 300)


@pytest.mark.spec("CNV-12")
def test_shell_runs_the_confirmed_command(tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path), Recorder()
    shell.shell = "/bin/sh"
    shell.run(channel, "1", {"line": "pwd; echo oops >&2; exit 3", "asked": "where", "shown": None})
    final = channel.texts()[-1]
    assert final.startswith("❌ `pwd; echo oops >&2; exit 3` · _exit 3_") and str(tmp_path) in final and "oops" in final
    assert "```output" in final
    shell.drop(channel, "1", {"line": "ls", "shown": None})
    assert "🚫 _Not run:_ `ls`" in channel.texts()[-1]


@pytest.mark.spec("CNV-13")
def test_research_reads_what_is_most_about_the_searches(monkeypatch):
    paragraphs = ["Python 3.14 released with free threading support and more " * 2, "Cookies and privacy notice " * 4,
                  "The release notes for python list every change in detail " * 2]
    text = "\n\n".join(paragraphs)
    wanted = {"python", "release", "notes", "free", "threading"}
    assert Research.most_about(text, wanted) == "\n\n".join(p.strip() for p in paragraphs)
    monkeypatch.setattr(conversation, "RESEARCH_PAGE_CHARS", len(paragraphs[0]) + len(paragraphs[2]))
    assert Research.most_about(text, wanted) == paragraphs[0].strip() + "\n\n" + paragraphs[2].strip()
    assert Research.most_about("tiny", wanted) == "tiny"


@pytest.mark.spec("CNV-13")
def test_research_searches_fall_back_to_the_topic(measured_model, fake_ollama):
    research = Research(Talk("cmds"))
    fake_ollama.reply("I would search the web.")
    assert research.searches(Recorder(), "1", "python news") == ["python news"]
    fake_ollama.reply('{"searches": ["a", "a", "b", "c", "d"]}')
    assert research.searches(Recorder(), "1", "python news") == ["a", "b", "c"]


@pytest.mark.spec("CNV-14")
def test_running_elsewhere(tinker_home):
    running = tinker_home / "running"
    running.mkdir(parents=True)
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    (running / f"{ended.pid}.json").write_text('{"title": "gone"}')
    (running / f"{os.getpid()}.json").write_text('{"title": "mine"}')
    (running / f"{os.getppid()}.json").write_text('{"title": "calc", "brief": "b", "text": "t"}')
    (running / "notapid.json").write_text("{}")
    assert running_elsewhere() == [{"title": "calc", "brief": "b", "text": "t", "pid": os.getppid()}]
    assert not (running / f"{ended.pid}.json").exists()


FAKE_TINKER = '''
import sys, time, signal
args = sys.argv[1:]
folder = sys.argv[0].rsplit("/", 1)[0] + "/calc"
with open(sys.argv[0] + ".calls", "a") as calls:
    calls.write(" ".join(args) + "\\n")
if args[:1] == ["help"]:
    print("- `task WHAT`\\n- `status`")
    sys.exit(0)
print("Run abc123 (new): a calculator", flush=True)
print("Project: " + folder, flush=True)
if args[:1] == ["slow"]:
    signal.signal(signal.SIGINT, lambda *a: (print("Interrupted.", flush=True), sys.exit(1)))
    time.sleep(30)
print("Plan: the spec (project)", flush=True)
print("=" * 60)
if "--confirm" in args:
    print("📋 Spec for calc (run abc123): Python; a person runs it with: python3 calc.py")
    print("Tested before any code is written:")
    print("  C1  calc.add(2, 3) is 5")
    print("Its folder: " + folder + "   (rename it: tinker rename abc123 NAME)")
    print("Build it: tinker resume abc123   (in the chat: confirm)")
else:
    print("Done. All 1 spec tests pass (SPEC.md).")
print("Total execution time: 1.00s (1s)")
'''


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """A Desk on a recorder channel, whose tinker is a small script that prints like a run."""
    script = tmp_path / "fake_tinker.py"
    script.write_text(textwrap.dedent(FAKE_TINKER))
    monkeypatch.setattr(conversation, "tinker_command", lambda: [sys.executable, str(script)])
    channel = Recorder(chats=("1",))
    desk = Desk([channel], tmp_path)
    desk.ran = []
    monkeypatch.setattr(desk, "tinker", lambda words: desk.ran.append(words) or f"output of {' '.join(words)}")
    yield desk, channel, script
    desk.stop_task()
    if desk.process:
        desk.process.wait(10)
    if desk.reporter:
        desk.reporter.ended = True


def say(desk, channel, text):
    desk.hear(channel, Message("1", text, "you", id=len(channel.log) + 1000))


@pytest.mark.spec("CNV-15")
def test_desk_answers_commands(desk):
    desk, channel, _ = desk
    assert desk.talk.commands == "task WHAT; status"  # the chat's prompt names tinker help's commands
    replies = {text: desk.answer(Message("1", text), channel) for text in
               ("task", "status", "stop", "resume", "channel add", "models", "/Runs")}
    assert replies["task"].startswith("✍️ Write `task WHAT TO BUILD`")
    assert replies["status"] == "🟢 **Tinker** · idle: send a task" and replies["stop"] == "💤 No task is running."
    assert replies["resume"].startswith("✍️ Write `resume ID`")
    assert "asks in a terminal" in replies["channel add"]
    assert replies["models"] == "⚙️ **models**\n```\noutput of models\n```"
    assert replies["/Runs"] == "📚 **Runs**\n```\noutput of runs\n```"


@pytest.mark.spec("CNV-15")
def test_a_command_that_deletes_and_exit_ask_first(desk):
    desk, channel, _ = desk
    assert desk.answer(Message("1", "clear memory"), channel) is None
    assert desk.ran == [["clear", "memory"]] and "👉 `confirm` to delete it" in channel.texts()[-1]
    assert desk.answer(Message("1", "exit"), channel) is None and "**Stop Tinker?**" in channel.texts()[-1]
    say(desk, channel, "confirm")
    wait_until(lambda: channel.closed)


@pytest.mark.spec("CNV-15")
def test_a_task_runs_as_its_own_tinker_and_its_spec_waits(desk):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting is not None)
    assert Path(str(script) + ".calls").read_text().splitlines() == ["help", "task a calculator --confirm"]
    assert desk.waiting["run"] == "abc123" and desk.waiting["stage"] == "spec"
    assert "📋 **Spec for calc** · run `abc123`" in channel.texts()[-1] or "📋" in desk.waiting["spec"]
    assert desk.status().startswith("🙋 **Tinker** · calc: a calculator waits for you")


@pytest.mark.spec("CNV-16")
def test_confirm_asks_for_the_folder_once_then_builds(desk):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting is not None)
    say(desk, channel, "confirm")
    assert desk.waiting["stage"] == "name" and any("📁 **Its folder:** `calc`" in t for t in channel.texts()[-3:])
    say(desk, channel, "confirm")
    wait_until(lambda: "resume abc123" in Path(str(script) + ".calls").read_text())
    wait_until(lambda: not desk.busy())
    assert desk.waiting is None


@pytest.mark.spec("CNV-16")
def test_what_waits_for_confirm_is_cancelled_by_anything_else(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kw: None)
    desk.answer(Message("1", "clear memory"), channel)
    say(desk, channel, "no thanks")
    assert "🚫 _Not done:_ `clear memory`" in channel.texts()[-1] and desk.ran == [["clear", "memory"]]
    desk.answer(Message("1", "clear memory"), channel)
    say(desk, channel, "confirm")
    wait_until(lambda: ["clear", "memory", "--yes"] in desk.ran)
    assert desk.decide("confirm", ["confirm"]).startswith("🤷 No spec waits")
    assert desk.rename({}, ["rename", "x"]).startswith("🤷 No spec waits")
    assert desk.decide("amend", ["amend"]).startswith("✍️ Write `amend WHAT`")


@pytest.mark.spec("CNV-17")
def test_follow_takes_the_run_and_folder_from_the_header(tmp_path):
    desk = Desk.__new__(Desk)
    desk.echo, desk.boards, desk.spent, desk.waiting = False, {}, {}, None
    desk.say_status = lambda *a, **k: None
    reporter = Reporter([Recorder()], "a calculator")
    process = type("P", (), {"stdout": io.StringIO("Run abc123 (new): x\nProject: /tmp/projects/calc\n"
                                                   + "=" * 60 + "\n📋 Spec for calc (run abc123): Python\n"),
                             "wait": lambda self: 0})()
    task = {"run": None, "folder": tmp_path, "title": "a calculator", "arguments": ["task"]}
    desk.follow(process, reporter, task)
    assert task["run"] == "abc123" and task["folder"] == Path("/tmp/projects/calc")
    assert task["title"] == "calc: a calculator" == reporter.progress.title
    assert desk.waiting["run"] == "abc123" and desk.boards["abc123"] is reporter.sent


@pytest.mark.spec("CNV-18")
def test_helpers(tmp_path):
    assert json_in('Sure: {"a": 1} ok') == {"a": 1} and json_in("none") == {} and json_in("{bad}") == {}
    assert json_in("[1]") == {}
    assert first_lines("a\nb\nc", 2) == "a\nb\n… 1 more lines" and first_lines("a", 2) == "a"
    assert first_words("  a \n b  c ", 3) == "a b"
    assert text_of(b"caf\xc3\xa9") == "café" and text_of(None) == "" and text_of("x") == "x"
    assert after_word(Message("1", "research the python news", "me")).text == "the python news"


@pytest.mark.spec("CNV-18")
def test_built_project(tmp_path):
    import tinkerkit.project as project_module

    def run(run_id, name, updated, status):
        folder = tmp_path / name
        folder.mkdir()
        record = dict(Runs.new_record("t", folder, None), id=run_id, status=status, updated=updated)
        project_module.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (project_module.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(record))
    run("aaa111", "calc", "2026-10-01 10:00", "done")
    run("bbb222", "notes", "2026-10-02 10:00", "done")
    run("ccc333", "todo", "2026-10-03 10:00", "blocked")
    assert built_project("")["id"] == "bbb222"  # the one built last
    assert built_project("calc")["id"] == "aaa111" and built_project("ccc")["id"] == "ccc333"
    assert built_project("cc") is None and built_project("nothing") is None


@pytest.mark.spec("CNV-19")
def test_an_emergency_stops_the_task_answers_and_goes_on(desk):
    desk, channel, script = desk
    assert desk.start_task(["slow"], desk.root, "calc") is None
    wait_until(lambda: desk.task["run"] == "abc123")
    desk.emergency(channel, Message("1", "is my disk full?"))
    wait_until(lambda: "resume abc123" in Path(str(script) + ".calls").read_text())
    calls = Path(str(script) + ".calls").read_text().splitlines()
    assert calls[-2:] == ["slow", "resume abc123"]
    texts = channel.texts()
    emergency = next(t for t in reversed(texts) if t.startswith("🚨 **Emergency:**"))
    assert "⏸ Stopping calc" in emergency and "No model is in use yet" in emergency and "▶️ **Going on** with" in emergency


@pytest.mark.spec("CNV-19")
def test_one_emergency_at_a_time(desk):
    desk, channel, _ = desk
    desk.urgent = True
    desk.emergency(channel, Message("1", "help"))
    assert channel.texts()[-1] == "🚨 An emergency is being answered: one at a time."
    assert desk.start_task(["task", "x"], desk.root, "x").startswith("🚨 An emergency is being answered")


def built(tmp_path, name, run_id, run, status="done", updated="2026-10-01 10:00"):
    """A project the run index knows, with its toolchain's run command."""
    import tinkerkit.project as project_module
    folder = tmp_path / name
    (folder / ".tinker").mkdir(parents=True)
    (folder / ".tinker" / "spec.json").write_text(json.dumps({"toolchain": {"run": run, "activate": ""}}))
    project_module.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    record = dict(Runs.new_record("t", folder, None), id=run_id, status=status, updated=updated)
    (project_module.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(record))
    return folder


ECHO_APP = "import sys\nprint('args:', sys.argv[1:])\nprint('input:', sys.stdin.read().strip())\n"


@pytest.mark.spec("CNV-20")
def test_run_a_built_project(desk, tmp_path):
    desk, channel, _ = desk
    folder = built(tmp_path, "calc", "aaa111", f'"{sys.executable}" app.py')
    (folder / "app.py").write_text(ECHO_APP)
    desk.run_app(channel, "1", ['calc add "two words" <<< hello there'])
    final = channel.texts()[-1]
    assert final.startswith("✅ **calc** `") and "add 'two words'` · _exit 0_" in final
    assert "_input:_ `hello there`" in final and "args: ['add', 'two words']" in final and "input: hello there" in final
    desk.run_app(channel, "1", ["5 + 3"])  # no project named: the one built last, every word an argument
    assert "args: ['5', '+', '3']" in channel.texts()[-1]


@pytest.mark.spec("CNV-20")
def test_run_with_nothing_to_run(desk, tmp_path):
    desk, channel, _ = desk
    desk.run_app(channel, "1", ["x"])
    assert channel.texts()[-1].startswith("🤷 **No project is built yet:**")
    built(tmp_path, "lib", "bbb222", "")
    desk.run_app(channel, "1", ["lib"])
    assert channel.texts()[-1].startswith("🤷 **lib has no run command:**")


class Listening(Recorder):
    """A chat whose messages come one at a time, as a person sends them."""

    def receive(self, timeout):
        time.sleep(0.05)
        return [self.inbox.pop(0)] if self.inbox else []


@pytest.mark.spec("CNV-21")
def test_desk_listens_until_the_chat_ends_it(desk):
    _, _, script = desk
    channel = Listening("bot", inbox=[Message("1", "exit", id=1), Message("1", "confirm", id=2)])
    listening = Desk([channel], Path.cwd())
    listening.run()
    statuses = [text for text in channel.texts() if "**Tinker** ·" in text]
    assert statuses[0].startswith("🟢 **Tinker** · idle: send a task") and statuses[-1].startswith("💤 **Tinker** · not listening")
    assert ("pin", "1", 101, "") in channel.log and channel.closed
    channel.claimed.close()


@pytest.mark.spec("CNV-21")
def test_desk_refuses_a_chat_another_tinker_listens_to(desk):
    _, _, script = desk
    holder = Recorder("bot")
    assert holder.claim({"pid": 999})
    try:
        with pytest.raises(SystemExit, match="bot is listened to by another Tinker \\(process 999\\)"):
            Desk([Recorder("bot")], Path.cwd()).run()
    finally:
        holder.claimed.close()


@pytest.mark.spec("CNV-22")
def test_one_shot_commands(monkeypatch, capsys):
    monkeypatch.setattr(conversation, "commands_brief", lambda: "cmds")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert conversation.one_shot("stop", "") is None
    assert "💤 No task is running." in capsys.readouterr().out
    assert conversation.one_shot("chat", "") == "tinker chat needs words after it, e.g. tinker chat hello"
    assert conversation.one_shot("research", "") == "tinker research needs words after it, e.g. tinker research ..."
    conversation.one_shot("chat", "hello")
    assert "No model is in use yet" in capsys.readouterr().out


@pytest.mark.spec("CNV-22")
def test_one_shot_command_runs_nothing_without_a_yes(monkeypatch, measured_model, fake_ollama, capsys, tmp_path):
    monkeypatch.setattr(conversation, "commands_brief", lambda: "cmds")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.chdir(tmp_path)
    fake_ollama.reply('{"command": "touch made.txt", "does": "makes a file"}')
    conversation.one_shot("command", "make a file")
    out = capsys.readouterr().out
    assert "touch made.txt" in out and "Not run:" in out and not (tmp_path / "made.txt").exists()


class Pages:
    """The Internet plugin, with a few pages."""

    def __init__(self, up=True):
        self.up = up

    def available(self):
        return self.up

    def why(self, key=None):
        return "no key" if not self.up else "no answer"

    def search(self, query, count):
        return [{"title": f"Page {n}", "url": f"https://site{n}.org/{query.replace(' ', '-')}", "content": "excerpt"}
                for n in (1, 2)]

    def fetch(self, url):
        return {"title": "", "url": url, "content": "Python 3.14 was released in October with free threading."}


@pytest.mark.spec("CNV-23")
def test_research_in_a_chat(measured_model, fake_ollama, monkeypatch):
    channel = Recorder()
    monkeypatch.setattr(conversation, "Internet", lambda: Pages(up=False))
    Research(Talk("cmds")).researching(channel, "1", "the newest Python", lambda: [])
    assert "⚠️ **The web is not usable:** no key" in channel.texts()[-1]
    monkeypatch.setattr(conversation, "Internet", lambda: Pages())
    fake_ollama.reply('{"searches": ["python release"]}').reply("**Python 3.14** came out in October [1].")
    Research(Talk("cmds")).researching(channel, "1", "the newest Python", lambda: [])
    final = channel.texts()[-1]
    assert final.startswith("🔎 **the newest Python**\n**Python 3.14** came out in October [1].")
    assert "📚 **Sources**\n[1] [Page 1](https://site1.org/python-release)\n[2] [Page 2](https://site2.org/python-release)" in final
    assert "[1] Page 1 (https://site1.org/python-release)\nPython 3.14 was released" in \
        fake_ollama.calls[-1]["messages"][1]["content"]


@pytest.fixture
def attached(monkeypatch, tinker_home):
    import signal
    handlers = {sig: signal.getsignal(sig) for sig in (conversation.PAUSE, conversation.STOP)}
    started = []

    def start(title="calc: a calculator"):  # in the test itself: pytest sets its own stdout for each phase
        monkeypatch.setattr(sys, "stdout", sys.stdout)
        started.append(conversation.Attached([channel], title).start())
        return started[-1]
    channel = Recorder()
    yield start, channel
    for task in started:
        task.reporter.ended = True
    for sig, handler in handlers.items():
        signal.signal(sig, handler)


@pytest.mark.spec("CNV-24")
def test_a_terminal_task_reports_and_stops_on_a_signal(attached, tinker_home):
    start, channel = attached
    task = start()
    shared = tinker_home / "running" / f"{os.getpid()}.json"
    assert isinstance(sys.stdout, LineWatcher) and json.loads(shared.read_text())["title"] == "calc: a calculator"
    print("Recall (what Tinker has learned)")
    assert task.reporter.progress.step[0] == "Recall"
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), conversation.STOP)
        time.sleep(1)
    assert task.end() is False and not shared.exists() and not isinstance(sys.stdout, LineWatcher)
    assert channel.texts()


@pytest.mark.spec("CNV-24")
def test_a_paused_task_waits_for_its_answer(attached, tinker_home, monkeypatch):
    start, channel = attached
    task = start()
    shared = tinker_home / "running" / f"{os.getpid()}.json"
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), conversation.PAUSE)
        time.sleep(1)
    seen = []

    def answer():  # the conversation answers the emergency, then removes the file
        wait_until(lambda: shared.exists() and json.loads(shared.read_text()).get("paused"))
        seen.append(json.loads(shared.read_text()))
        shared.unlink()
    threading.Thread(target=answer, daemon=True).start()
    assert task.end() is True and seen[0]["paused"] and task.reporter.quiet
    monkeypatch.setattr(conversation, "RESUME_WAIT", 0.2)
    later = start("calc")
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), conversation.PAUSE)
        time.sleep(1)
    assert later.end() is True and not shared.exists()  # nobody answered: it goes on after RESUME_WAIT
