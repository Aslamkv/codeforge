import contextlib
from pathlib import Path

import pytest
from test_console import Recorder

from tinkerkit.chat.markup import Message
from tinkerkit.chat.shell import Shell, recalled_line
from tinkerkit.desk import Questions, Talk
from tinkerkit.memory.memories import Scratchpad
from tinkerkit.model.models import Models, QuickModel
from tinkerkit.settings import Home


@pytest.mark.spec("CNV-18")
def test_an_unreadable_json_answer_is_taken_as_empty(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    for unreadable in ("{bad}", "[1]", "none"):
        fake_ollama.reply("low: a plain request").reply(unreadable)
        assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300)
    fake_ollama.reply("low: a plain request").reply('Sure: {"command": "ls", "does": "lists", "flags": [}')
    assert "```\nls\n```" in shell.propose(channel, Message("1", "x"), "", 300)


@pytest.mark.spec("REM-1")
def test_each_command_line_run_writes_one_episode(tmp_path):

    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    shell.run(channel, "1", {"line": "echo hi; exit 3", "asked": "say hi", "shown": None})
    [episode] = Scratchpad.fresh()
    assert episode["chat"] == "1" and episode["request"] == "say hi" and episode["line"] == "echo hi; exit 3"
    assert episode["output"].strip() == "hi" and episode["code"] == 3


def remembering(kind, gist, text):
    from tinkerkit.memory.memories import Memories
    from tinkerkit.ranking import Retriever

    return Memories(Retriever(None)).add(kind, gist, text, ["e1"])


@pytest.mark.spec("REM-6")
def test_a_proposed_command_sees_tool_memories_and_episodes_only(measured_model, fake_ollama, tmp_path):

    remembering("tool", "disk usage goes through dust", "dust shows disk usage")
    remembering("fact", "the disk is named Bees", "the disk name")
    Scratchpad.write({"chat": "1", "request": "show disk usage", "line": "du -sh .", "output": "", "code": 0})
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"command": "dust", "does": "shows disk usage"}')
    shell.propose(Recorder(), Message("1", "disk usage"), "", 300)
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert "disk usage goes through dust" in system and "du -sh ." in system
    assert "named Bees" not in system


@pytest.mark.spec("REM-6")
def test_a_proposed_command_counts_only_the_tool_memories_it_shows(measured_model, fake_ollama, tmp_path):
    from tinkerkit.memory.memories import Memories

    remembering("tool", "disk usage goes through dust", "dust shows disk usage")
    remembering("fact", "the disk usage is high", "disk usage grows")
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"command": "dust", "does": "shows disk usage"}')
    shell.propose(Recorder(), Message("1", "disk usage"), "", 300)
    recalled = {
        memory["gist"]: memory["recalled"] for memory in Home.store().items(Memories.MEMORIES).values()
    }
    assert recalled == {"disk usage goes through dust": 1, "the disk usage is high": 0}


@pytest.mark.spec("REM-6")
def test_a_proposed_command_with_nothing_recalled_keeps_its_prompt(measured_model, fake_ollama, tmp_path):
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"command": "ls", "does": "lists"}')
    shell.propose(Recorder(), Message("1", "list files"), "", 300)
    assert "What you remember" not in fake_ollama.calls[-1]["messages"][0]["content"]


REMEMBERED_REPLY = (
    '💭 From memory (you asked "x" on 2026-01-01):\nThe answer.\nMore of it.\n_`/fresh` asks again._'
)


def chatted_episode(number, chat="1", **changes):
    return {"chat": chat, "message": f"question {number}", "reply": f"Answer {number}. More.", **changes}


@pytest.mark.spec("REM-11")
def test_a_recalled_remembered_episode_gives_only_the_answer():
    line = recalled_line({"id": "e1", **chatted_episode(0, reply=REMEMBERED_REPLY, status="remembered")})
    assert "reply: The answer.\nMore of it." in line
    assert "From memory" not in line and "/fresh" not in line


@pytest.mark.spec("CNV-20")
def test_shell_proposes_one_command(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"command": "rm -rf build", "does": "deletes the build folder"}')
    shown = shell.propose(channel, Message("1", "clean up"), "", 300)
    assert "```\nrm -rf build\n```" in shown and "It changes or deletes things" in shown
    proposed = shell.take(channel, "1")
    assert (
        proposed == {"line": "rm -rf build", "asked": "clean up", "level": "low", "shown": None}
        and shell.take(channel, "1") is None
    )
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"command": "ls\\nrm x", "does": "two"}')
    assert shell.propose(channel, Message("1", "x"), "", 300).startswith("🤷 **No command found:**")
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply("{}")
    assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300)
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"command": "df -h", "does": "shows the free space"}')
    assert "It changes" not in shell.propose(channel, Message("1", "disk"), "", 300)


@pytest.mark.spec("CNV-20")
def test_shell_runs_the_confirmed_command(tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    shell.shell = "/bin/sh"
    shell.run(
        channel, "1", {"line": "pwd; echo oops >&2; exit 3", "asked": "where", "level": "low", "shown": None}
    )
    final = channel.texts()[-1]
    assert (
        final.startswith("❌ `pwd; echo oops >&2; exit 3` · _exit 3_")
        and str(tmp_path) in final
        and "oops" in final
    )
    assert "```output" in final
    shell.drop(channel, "1", {"line": "ls", "shown": None})
    assert "🚫 _Not run:_ `ls`" in channel.texts()[-1]


def pausing_into(order):
    @contextlib.contextmanager
    def pausing():
        order.append("paused")
        yield
        order.append("goes on")

    return pausing


@pytest.mark.spec("JOB-10")
def test_a_command_line_run_once_confirmed_pauses_the_job_while_its_output_is_read(tmp_path, monkeypatch):
    order = []
    talk = Talk("cmds", pausing_jobs=pausing_into(order))
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: QuickModel("m", {}, None)))
    monkeypatch.setattr(
        Models, "quick_reply", staticmethod(lambda *asked, **options: order.append("read") or "it says hi")
    )
    channel = Recorder()
    Shell(talk, tmp_path, Questions()).run(
        channel, "1", {"line": "echo hi", "asked": "say hi", "level": "low", "shown": None}
    )
    assert order == ["paused", "read", "goes on"] and "it says hi" in channel.texts()[-1]
