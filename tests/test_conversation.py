"""CNV: the conversation: Markup, channels, a task's progress, a chat, a command line, research and the Desk."""

import contextlib
import io
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
    Channel, ChannelError, ConsoleChannel, Desk, Markup, Message, Progress, Reporter, Research, Shell, Talk, after_word,
    built_project, command_of, described, first_lines, first_words, json_in, pieces, running, text_of, welcome,
)
from tinkerkit.ledger import NOTE_KEEP, Ledger
from tinkerkit.settings import Home
from tinkerkit.project import Tracker
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


@pytest.mark.spec("CNV-1")
def test_markup_of_a_t_string():
    said, bold, line, out = "*hi*", "**ok**", "ls -a", "a ``` b"
    assert Markup.of(t"🔎 {said} {bold:md} {line:code}") == "🔎 \\*hi\\* **ok** `ls -a`"
    assert Markup.of(t"{out:block}") == "```\na ˋˋˋ b\n```"
    with pytest.raises(ValueError):
        Markup.of(t"{said:bold}")


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
def test_pieces_lose_no_text_of_a_long_line():
    text = "short\n" + "x" * 250 + "\nend"
    assert "".join("".join(pieces(text, size=100)).split()) == "".join(text.split())


@pytest.mark.spec("CNV-4")
@pytest.mark.parametrize("text,command", [("status", "status"), ("/Status", "status"), ("status abc123", "status"),
                                          ("status of my build please", ""), ("task a calculator", "task"),
                                          ("models are getting better", ""), ("models", "models"), ("hello", ""),
                                          ("", ""), ("confirm", "confirm"), ("confirm abc123", "confirm"),
                                          ("research the new python", "research"), ("/use_model", "use-model"),
                                          ("/status@tinker_bot", "status")])
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


def snapshot(**state):
    """A run's tracker, as a run publishes it."""
    base = {"title": "calc: a calculator", "run": "abc123", "folder": "/tmp/projects/calc", "step": None,
            "doing": Tracker.STARTING, "began": None, "steps": [], "passing": None, "live": "", "latest": "",
            "waiting": None, "ended": None, "before": 0.0, "seconds": 0.0}
    return dict(base, **state)


@pytest.mark.spec("CNV-8")
def test_progress_shows_the_tracker():
    progress = Progress("a calculator", phase="✅ **Confirmed:** building it")
    assert progress.text().splitlines() == ["🔨 **a calculator**", "▸ ✅ **Confirmed:** building it"]
    assert progress.take(snapshot()) and "▸ ✅ **Confirmed:** building it" in progress.text()  # said until a step
    progress.take(snapshot(step="verify", doing=Tracker.STEPS["verify"][0], began=time.time() - 65,
                           steps=["🧠 Recalled what Tinker has learned", "📝 Spec written"], passing=[2, 3],
                           live="Fix the code C2: 120 tok written, 0 tok thinking, 14s", latest="wrote calc.py"))
    assert progress.text().splitlines() == [
        "🔨 **calc: a calculator**", "✅ 🧠 Recalled what Tinker has learned", "✅ 📝 Spec written",
        "▸ **🔁 Fixing until every test passes** · ⏱ 1m 5s", "⏳ _Fix the code C2: 120 tok written, 0 tok thinking, 14s_",
        "📊 2/3 spec points pass", "… wrote calc.py"]
    assert progress.brief() == "**calc: a calculator** (🔁 Fixing until every test passes, 2/3 spec points pass)"
    assert not progress.take(progress.snapshot)  # nothing changed


@pytest.mark.spec("CNV-8")
def test_progress_ending_of_a_done_run():
    progress = Progress("calc", before=60)
    progress.take(snapshot(seconds=30.0, ended={"status": "done", "headline": "Done. All 2 spec tests pass (SPEC.md).",
                                                "details": [["Run it", "python3 calc.py"]],
                                                "next": [["➕ Add a feature", 'tinker add @abc123 "..."'],
                                                         ["🐞 fix a bug", 'tinker fix @abc123 "..."']]}))
    assert progress.took() == "1m 30s (90 seconds)" and not progress.waiting()
    assert progress.ending().splitlines() == [
        "🏁 **calc: a calculator**", "✅ **Done. All 2 spec tests pass (SPEC.md).**", "⏱ **Done in 1m 30s (90 seconds)**",
        "▶️ Run it: `python3 calc.py`",
        '➕ Add a feature: `tinker add @abc123 "..."` · 🐞 fix a bug: `tinker fix @abc123 "..."`']
    progress.take(snapshot(ended={"status": "blocked", "headline": "Stopped: 1/2 tests pass", "details": [],
                                  "next": []}))
    assert progress.ending().splitlines()[1:3] == ["⚠️ **Stopped: 1/2 tests pass**", "⏱ _so far: 1m 0s (60 seconds)_"]


PLAN = {"name": "calc", "run": "abc123", "language": "Python", "start": "python3 calc.py", "kind": "project",
        "changes": ["+ C3 calc.sub(5, 3) is 2", "~ C1 calc.add(2, 3) is 5", "- adds"],
        "criteria": [["C1", "calc.add(2, 3) is 5"]], "non_goals": ["a GUI"], "folder": "/tmp/x/calc"}


@pytest.mark.spec("CNV-8")
def test_progress_ending_of_a_plan_that_waits():
    progress = Progress("calc")
    progress.take(snapshot(waiting=PLAN))
    assert progress.waiting()
    lines = progress.ending().splitlines()
    assert lines[:2] == ["📋 **Plan for calc** · run `abc123`", "🧰 Python · ▶️ `python3 calc.py`"]
    for line in ("✏️ **What changed**", "➕ C3 calc.sub(5, 3) is 2", "🔄 C1 calc.add(2, 3) is 5", "➖ ~~adds~~",
                 "🧪 **Tested before any code is written**", "**C1** calc.add(2, 3) is 5", "🚫 _Not included:_ a GUI",
                 "📁 `calc`", "👉 `confirm` to build it · `amend WHAT` to change it"):
        assert line in lines, line
    assert lines[-1].startswith("⏱ _so far:")
    assert Progress.plan(dict(PLAN, kind="fix")).startswith("🐞 **Bug shown for calc**")


@pytest.mark.spec("CNV-9")
def test_reporter_shows_the_tracker_and_ends_with_its_ending(home_store, monkeypatch):
    channel = Recorder(chats=("1", "2"))
    monkeypatch.setattr(conversation, "EDIT_EVERY", 1000)  # no background edits: the test refreshes it
    reporter = Reporter([channel], "a calculator", pid=4242)
    reporter.update()
    assert len(channel.texts("send")) == 2  # one message per chat
    home_store.put("running", "4242", snapshot(step="spec", doing=Tracker.STEPS["spec"][0]))
    reporter.refresh()
    assert "📝 Writing the spec" in channel.texts()[-1] and channel.log[-1][0] == "edit"
    edits = len(channel.log)
    reporter.refresh()  # unchanged, and its clock not due: no edit
    assert len(channel.log) == edits
    reporter.finish(snapshot(ended={"status": "done", "headline": "Done.", "details": [], "next": []}))
    assert reporter.ended and "✅ **Done.**" in channel.texts()[-1]
    assert set(reporter.sent) == {("rec", "1"), ("rec", "2")}


@pytest.mark.spec("CNV-9")
def test_a_reporter_without_a_tracker_shows_its_lines(monkeypatch):
    monkeypatch.setattr(conversation, "EDIT_EVERY", 1000)
    channel = Recorder()
    learning = Reporter([channel], "learn rust", pid=999999)
    learning.printed("  reading 4 pages")
    assert learning.progress.snapshot["latest"] == "reading 4 pages"


@pytest.mark.spec("CNV-11")
def test_talk_model_needs_a_measured_model(measured_model, tinker_home):
    from tinkerkit.settings import Config
    assert Talk.model() == (MODEL, {"num_ctx": 32768, "num_predict": 1024}, False)
    Config.save(**{"model": None})
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
    assert Talk.busy_note([{"brief": "**calc**"}]) == "⏸ **Pausing** **calc** to answer you.\nIt goes on once you have your answer."
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
def test_running(home_store):
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    home_store.put("running", str(ended.pid), snapshot(title="gone"))
    home_store.put("running", str(os.getpid()), snapshot(title="mine"))
    home_store.put("running", str(os.getppid()), snapshot(title="calc"))
    home_store.put("running", "notapid", {})
    assert [r["title"] for r in running()] == ["calc"] and running()[0]["pid"] == os.getppid()
    assert running(skip={os.getppid()}) == []
    assert home_store.get("running", str(ended.pid)) is None
    shown = described(running()[0])
    assert shown["title"] == "calc" and shown["brief"].startswith("**calc**") and shown["pid"] == os.getppid()


FAKE_TINKER = '''
import json, os, signal, sys, time
sys.path.insert(0, os.environ["TINKER_SOURCE"])
from tinkerkit.settings import Store
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
store.put("running", str(os.getpid()), state)
print("Plan: the spec (project)", flush=True)
if args[:1] == ["slow"]:
    signal.signal(signal.SIGINT, lambda *a: (print("Interrupted.", flush=True), sys.exit(1)))
    time.sleep(30)
if args[:1] == ["task"]:
    state["waiting"] = {"name": "calc", "run": "abc123", "language": "Python", "start": "python3 calc.py",
                        "kind": "project", "criteria": [["C1", "calc.add(2, 3) is 5"]], "folder": folder}
else:
    state["ended"] = {"status": "done", "headline": "Done. All 1 spec tests pass (SPEC.md).", "details": [], "next": []}
store.put("running", str(os.getpid()), state)
'''


@pytest.fixture
def desk(tmp_path, monkeypatch, home_store):
    """A Desk on a recorder channel, whose tinker is a small script that publishes a tracker as a run does."""
    script = tmp_path / "fake_tinker.py"
    script.write_text(textwrap.dedent(FAKE_TINKER))
    monkeypatch.setenv("TINKER_SOURCE", str(Path(__file__).resolve().parent.parent))
    monkeypatch.setenv("FAKE_TINKER_DB", str(home_store.path))
    monkeypatch.setattr(conversation, "tinker_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(conversation, "EDIT_EVERY", 0.2)
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


def calls_of(script):
    path = Path(str(script) + ".calls")
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.spec("CNV-15")
def test_desk_answers_commands(desk):
    desk, channel, _ = desk
    assert desk.talk.commands == "task WHAT; status"  # the chat's prompt names tinker help's commands
    replies = {text: desk.answer(Message("1", text), channel) for text in
               ("task", "status", "stop", "stop #9", "resume", "resume #9", "resume abc123", "channel add", "models",
                "/Runs")}
    assert replies["task"].startswith("✍️ Write `task WHAT TO BUILD`")
    assert replies["status"] == "🟢 **Tinker** · idle: send a task" and replies["stop"] == "💤 No job is running."
    assert replies["stop #9"] == "🤷 Job #9 is not running or paused: `jobs` lists them."
    assert replies["resume"] == "🤷 No job is stopped (jobs lists them)."
    assert replies["resume #9"] == "🤷 No job #9 (jobs lists them)."
    assert replies["resume abc123"].startswith("✍️ Write `resume [#JOB | @PROJECT]`")
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
def test_a_task_runs_as_its_own_tinker_and_its_plan_waits(desk, home_store):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting is not None)
    assert calls_of(script) == ["help", "task a calculator --confirm"]
    assert desk.waiting["run"] == "abc123" and desk.waiting["stage"] == "spec" and desk.waiting["new"]
    assert desk.waiting["spec"].startswith("📋 **Plan for calc** · run `abc123`")
    assert desk.status().startswith("🙋 **Tinker** · calc: a calculator waits for you")
    assert home_store.items("running") == {}  # the task's tracker is read last, then removed


@pytest.mark.spec("CNV-16")
def test_confirm_asks_a_new_projects_folder_once_then_builds(desk):
    desk, channel, script = desk
    say(desk, channel, "task a calculator")
    wait_until(lambda: desk.waiting is not None)
    say(desk, channel, "confirm")
    assert desk.waiting["stage"] == "name" and any("📁 **Its folder:** `calc`" in t for t in channel.texts()[-3:])
    say(desk, channel, "confirm")
    wait_until(lambda: "resume @abc123" in calls_of(script))
    wait_until(lambda: not desk.busy())
    assert desk.waiting is None


@pytest.mark.spec("CNV-16")
def test_confirm_of_a_feature_builds_at_once_and_amend_changes_it(desk):
    desk, channel, script = desk
    desk.waiting = {"run": "abc123", "folder": desk.root / "calc", "title": "calc: history", "spec": "📋 plan",
                    "stage": "spec", "new": False}
    say(desk, channel, "amend also keep the date")
    wait_until(lambda: "amend @abc123 also keep the date" in calls_of(script))
    wait_until(lambda: not desk.busy())
    desk.waiting = {"run": "abc123", "folder": desk.root / "calc", "title": "calc: history", "spec": "📋 plan",
                    "stage": "spec", "new": False}
    say(desk, channel, "confirm @calc")
    wait_until(lambda: "resume @abc123" in calls_of(script))


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
    assert desk.decide("confirm", ["confirm"]).startswith("🤷 No plan waits")
    assert desk.rename({}, ["rename", "x"]).startswith("🤷 No plan waits")
    assert desk.decide("amend", ["amend"]).startswith("✍️ Write `amend [@PROJECT] CHANGE`")


@pytest.mark.spec("CNV-27")
def test_a_glance_goes_soon_and_history_shows_things_again(desk, monkeypatch):
    desk, channel, _ = desk
    glances = []
    monkeypatch.setattr(channel, "glance", lambda chat, asked, sent, keep=None: glances.append((chat, asked, sent, keep)))
    monkeypatch.setattr(channel, "clear", lambda: 3)
    desk.hear(channel, Message("1", "jobs", "you", id=7))
    assert glances == [("1", 7, [101], None)]
    assert desk.answer(Message("1", "history"), channel) == "📜 **History:** nothing shown in the chats yet"
    Ledger.shown("rec", "1", None, [5], "🔨 **calc**\n▸ build", "task a calculator")
    listed = desk.answer(Message("1", "history"), channel)
    assert "`#1`" in listed and "🔨 calc" in listed and "_you: task a calculator_" in listed
    again = desk.answer(Message("1", "history #1"), channel)
    assert again.startswith("📜 **#1**") and "💬 _you:_ task a calculator" in again
    assert again.endswith("🔨 **calc**\n▸ build") and "No #9" in desk.answer(Message("1", "history #9"), channel)
    assert desk.answer(Message("1", "clear chat"), channel) is None
    assert channel.texts()[-1].startswith("🧹 **Chat cleared:** 3 messages taken out") and glances[-1][3] == NOTE_KEEP
    monkeypatch.setattr(desk, "tinker", lambda words: Home.clear(["history"], yes=True) or "Cleared history.")
    desk.answer(Message("1", "clear history"), channel)
    shown = len(channel.texts())
    say(desk, channel, "confirm")
    wait_until(lambda: len(channel.texts()) > shown)
    assert channel.texts()[-1].startswith("🧹 **clear history: done, and the chat:** 3 messages taken out")
    assert Ledger.page(1) == ([], 1)  # nothing is left, not even what clearing it said

@pytest.mark.spec("CNV-17")
def test_follow_reads_the_tasks_tracker(tmp_path, home_store):
    desk = Desk.__new__(Desk)
    desk.echo, desk.boards, desk.spent, desk.waiting = False, {}, {}, None
    desk.say_status = lambda *a, **k: None
    reporter = Reporter([Recorder()], "a calculator", pid=4343)
    home_store.put("running", "4343", snapshot(folder="/tmp/projects/calc", waiting=PLAN))
    process = type("P", (), {"stdout": io.StringIO("Plan: the spec\n📋 printed text is never read\n"), "pid": 4343,
                             "wait": lambda self: 0})()
    task = {"run": None, "folder": tmp_path, "title": "a calculator", "arguments": ["task"]}
    desk.follow(process, reporter, task)
    assert task["run"] == "abc123" and task["folder"] == Path("/tmp/projects/calc")
    assert task["title"] == "calc: a calculator" == reporter.progress.title
    assert desk.waiting["run"] == "abc123" and desk.boards["abc123"] is reporter.sent
    assert home_store.get("running", "4343") is None


@pytest.mark.spec("CNV-18")
def test_helpers(tmp_path):
    assert json_in('Sure: {"a": 1} ok') == {"a": 1} and json_in("none") == {} and json_in("{bad}") == {}
    assert json_in("[1]") == {}
    assert first_lines("a\nb\nc", 2) == "a\nb\n… 1 more lines" and first_lines("a", 2) == "a"
    assert first_words("  a \n b  c ", 3) == "a b"
    assert text_of(b"caf\xc3\xa9") == "café" and text_of(None) == "" and text_of("x") == "x"
    assert after_word(Message("1", "research the python news", "me")).text == "the python news"


def built(tmp_path, name, run_id, run, status="done", updated="2026-10-01 10:00"):
    """A built project Tinker points to, with its toolchain's run command."""
    from tinkerkit.project import project_store
    folder = tmp_path / name
    folder.mkdir(parents=True)
    Runs.save(dict(Runs.new_record("t", folder, None), id=run_id, status=status), folder)
    store = project_store(folder)
    store.put("run", "record", dict(store.get("run", "record"), updated=updated))
    store.put("spec", "plan", {"toolchain": {"run": run, "activate": ""}})
    return folder


@pytest.mark.spec("CNV-18")
def test_built_project(tmp_path):
    built(tmp_path, "calc", "aaa111", "x", updated="2026-10-01 10:00")
    built(tmp_path, "notes", "bbb222", "x", updated="2026-10-02 10:00")
    built(tmp_path, "todo", "ccc333", "x", status="blocked", updated="2026-10-03 10:00")
    assert built_project("")["id"] == "bbb222"  # the one built last
    assert built_project("calc")["id"] == "aaa111" and built_project("ccc333")["id"] == "ccc333"
    assert built_project("ccc") is None and built_project("nothing") is None


@pytest.mark.spec("CNV-19", "JOB-10")
def test_a_chat_pauses_the_job_that_runs_before_it_is_answered(desk, monkeypatch):
    desk, channel, _ = desk
    order = []

    @contextlib.contextmanager
    def pausing():
        order.append("paused")
        yield
        order.append("goes on")
    monkeypatch.setattr(desk.talk, "pausing", pausing)
    monkeypatch.setattr(desk.talk, "reply", lambda *asked: order.append("answered") or "the answer")
    say(desk, channel, "hello there")
    wait_until(lambda: any("the answer" in text for text in channel.texts()))
    assert order == ["paused", "answered", "goes on"]
    assert Desk([channel], desk.root).talk.pausing is conversation.paused_for_you


@pytest.mark.spec("JOB-10")
def test_there_is_no_emergency_command():
    assert "emergency" not in [c.name for c in conversation.COMMANDS] and conversation.command_of("emergency help") == ""
    assert not hasattr(Desk, "emergency")


ECHO_APP = "import sys\nprint('args:', sys.argv[1:])\nprint('input:', sys.stdin.read().strip())\n"


@pytest.mark.spec("CNV-20")
def test_run_a_built_project(desk, tmp_path):
    desk, channel, _ = desk
    folder = built(tmp_path, "calc", "aaa111", f'"{sys.executable}" app.py')
    (folder / "app.py").write_text(ECHO_APP)
    desk.run_app(channel, "1", ['@calc add "two words" <<< hello there'])
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
    desk.run_app(channel, "1", ["@lib"])
    assert channel.texts()[-1].startswith("🤷 **lib has no run command:**")
    desk.run_app(channel, "1", ["@nothing 1 2"])
    assert channel.texts()[-1].startswith("🤷 **No project @nothing:**")
    desk.run_app(channel, "1", ["lib 1 2"])  # a word that is a folder's name is an argument, not a project
    assert "lib" in channel.texts()[-1] and "has no run command" in channel.texts()[-1]


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
def test_the_status_is_sent_anew_and_pinned_as_tinker_starts_and_ends(desk, home_store):
    home_store.put("listening", "bot", {"1": 55})
    channel = Listening("bot", inbox=[Message("1", "exit", id=1), Message("1", "confirm", id=2)])
    collected = []
    channel.collect = lambda: collected.append(True)
    Desk([channel], Path.cwd()).run()
    status = [(kind, sent) for kind, _, sent, text in channel.log if "**Tinker** ·" in text]
    assert status == [("send", 101), ("send", 103)]  # 102: the question to stop
    assert {("pin", 101), ("pin", 103)} <= {(kind, sent) for kind, _, sent, _ in channel.log}
    deleted = {sent for kind, _, sent, _ in channel.log if kind == "delete"}
    assert deleted == {55, 101, 1, 2, 102}  # the statuses before, the person's exit and confirm, the question
    assert not any("Stopping" in text for text in channel.texts())
    assert home_store.get("listening", "bot") == {"1": 103} and collected
    channel.claimed.close()


@pytest.mark.spec("CNV-21")
def test_a_status_about_something_else_is_sent_anew_the_same_one_edited(desk):
    channel = Recorder("bot")
    listening = Desk([channel], Path.cwd())
    listening.ready.add(channel)
    channel.pins = True
    listening.say_status("🔄 **Tinker** · working on calc\n▸ planning")
    listening.say_status("🔄 **Tinker** · working on calc\n▸ building")
    listening.say_status("🟢 **Tinker** · idle: send a task")
    log = [(kind, sent) for kind, _, sent, _ in channel.log]
    assert log[:4] == [("send", 101), ("pin", 101), ("edit", 101), ("pin", 101)]
    assert sorted(log[4:]) == [("delete", 101), ("pin", 102), ("send", 102)]  # the one before goes meanwhile


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
    assert conversation.one_shot("chat", "") == "tinker chat needs words after it, e.g. tinker chat hello"
    assert conversation.one_shot("research", "") == "tinker research needs words after it, e.g. tinker research ..."
    assert conversation.one_shot("history", "") is None
    assert "nothing shown in the chats yet" in capsys.readouterr().out
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
def attached(monkeypatch, home_store):
    import signal
    handlers = {sig: signal.getsignal(sig) for sig in (conversation.STOP,)}
    monkeypatch.setattr(conversation, "EDIT_EVERY", 1000)
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
    for sig, handler in handlers.items():
        signal.signal(sig, handler)


@pytest.mark.spec("CNV-24")
def test_a_terminal_task_reports_and_stops_on_a_signal(attached, home_store):
    start, channel = attached
    task = start()
    assert channel.texts()[0].startswith("🔨 **calc: a calculator**")
    task.tracker.phase("recall")
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), conversation.STOP)
        time.sleep(1)
    task.end()
    assert home_store.get("running", str(os.getpid())) is None
    assert "🧠 Recalling what Tinker has learned" in channel.texts()[-1]


@pytest.mark.spec("CNV-25")
def test_start_is_answered_with_a_welcome(desk):
    desk, channel, script = desk
    say(desk, channel, "/start")
    text = channel.texts()[-1]
    assert text.startswith("👋 **Hi, I'm Tinker!**")
    for section in ("🔨 **Build something**", "📂 **Your projects**", "💬 **Ask me anything**", "⏳ **While I work**"):
        assert section in text
    assert "`task a calculator in Python`" in text and "normal chat" in text and "`help`" in text
    assert calls_of(script) == ["help"] and desk.process is None  # no task, no chat
    say(desk, channel, "/start abcdef")  # a start link with a payload is a start too
    assert channel.texts()[-1] == welcome()


@pytest.mark.spec("CNV-26")
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
    assert desk.answer(Message("1", "memory --graph"), channel) == \
        "🧠 **memory --graph**\n```\nError: the home cannot be read\n```" and len(sent) == 1


@pytest.mark.spec("CNV-26")
def test_the_terminal_opens_the_file_and_other_channels_send_where_it_is(monkeypatch, tmp_path):
    opened, printed = [], []
    monkeypatch.setattr(conversation.webbrowser, "open", opened.append)
    console = ConsoleChannel()
    monkeypatch.setattr(console, "print", printed.append)
    page = tmp_path / "memory.html"
    console.send_file("console", page, "🧠 **Tinker's memory**")
    assert opened == [page.as_uri()] and f"Opened in your browser: {page}" in Markup.plain(printed[0])
    plain = Recorder()
    plain.send_file("1", page, "🧠 **Tinker's memory**")
    assert plain.texts() == [f"🧠 **Tinker's memory**\n📄 `{page}`"]


@pytest.mark.spec("JOB-10")
def test_a_command_line_run_once_confirmed_pauses_the_job_while_its_output_is_read(tmp_path, monkeypatch):
    order = []

    @contextlib.contextmanager
    def pausing():
        order.append("paused")
        yield
        order.append("goes on")
    talk = Talk("cmds", pausing=pausing)
    monkeypatch.setattr(talk, "model", lambda: ("m", {}, None))
    monkeypatch.setattr(talk, "ask", lambda *asked: order.append("read") or "it says hi")
    channel = Recorder()
    Shell(talk, tmp_path).run(channel, "1", {"line": "echo hi", "asked": "say hi", "shown": None})
    assert order == ["paused", "read", "goes on"] and "it says hi" in channel.texts()[-1]

@pytest.mark.spec("RDG-13")
def test_the_reading_list_is_answered_at_once_in_a_chat(desk):
    desk, channel, _ = desk
    assert desk.answer(Message("1", "read list"), channel) == "📚 **read list**\n```\noutput of read list\n```"
    assert desk.answer(Message("1", "read show #2"), channel).startswith("📚 **read show #2**")
    assert desk.ran == [["read", "list"], ["read", "show", "#2"]]
