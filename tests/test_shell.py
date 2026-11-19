import contextlib
import json
import os
from pathlib import Path

import pytest
from test_console import Recorder

from tinkerkit.chat.markup import Message
from tinkerkit.chat.shell import (
    AtOnceLine,
    Checked,
    PlainLine,
    RootLine,
    Shell,
    UndoneLine,
    recalled_line,
    system_name,
)
from tinkerkit.desk import Questions, Talk
from tinkerkit.memory.memories import Scratchpad
from tinkerkit.model.models import Models, QuickModel
from tinkerkit.prompts import Prompts
from tinkerkit.settings import EPISODE_FOUND_CHARS, Home


def asked(text):
    return [{"said": "request", "text": text}]


@pytest.mark.spec("INS-2")
def test_a_proposal_sends_the_whole_thread_each_turn_cut(measured_model, fake_ollama):
    shell = Shell(Talk("cmds"), Path.cwd(), Questions())
    thread = [
        {"said": "request", "text": "list the logs"},
        {"said": "proposal", "text": "ls *.log"},
        {"said": "output", "text": "x" * (EPISODE_FOUND_CHARS + 50)},
        {"said": "amend", "text": "only the big ones"},
    ]
    fake_ollama.reply("low: a plain request").reply('{"line": "ls -S *.log", "does": "sorts by size"}')
    shell.propose(Recorder(), Message("1", "only the big ones"), "", 300, thread)
    sent = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "request: list the logs" in sent and "proposal: ls *.log" in sent
    assert "output: x" in sent and "amend: only the big ones" in sent
    assert "x" * EPISODE_FOUND_CHARS in sent and "x" * (EPISODE_FOUND_CHARS + 1) not in sent


@pytest.mark.spec("INS-2")
def test_a_proposal_shows_its_line_does_touches_and_undo(measured_model, fake_ollama, tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    fake_ollama.reply(
        '{"line": "mv -n a.txt b.txt", "does": "renames a to b", "touches": ["a.txt", "b.txt"], '
        '"undo": "mv -n b.txt a.txt"}'
    )
    shown = shell.propose(channel, Message("1", "rename a"), "", 300, asked("rename a"))
    assert "```\nmv -n a.txt b.txt\n```" in shown and "renames a to b" in shown
    assert "a.txt" in shown.split("```")[-1] and "b.txt" in shown.split("```")[-1]
    assert "mv -n b.txt a.txt" in shown and "can't be undone" not in shown
    held = shell.take(channel, "1")
    assert held["touches"] == ["a.txt", "b.txt"] and held["undo"] == "mv -n b.txt a.txt"


@pytest.mark.spec("INS-2")
def test_a_missing_undo_puts_back_the_backups_of_what_it_touches(measured_model, fake_ollama, tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    fake_ollama.reply("low: a plain request").reply(
        '{"line": "echo hi >> notes.txt", "does": "appends hi", "touches": ["notes.txt"]}'
    )
    shown = shell.propose(channel, Message("1", "append hi"), "", 300, asked("append hi"))
    assert "puts back" in shown and "notes.txt" in shown.split("**Undo:**")[-1]
    assert "can't be undone" not in shown
    assert shell.take(channel, "1")["undo"] == ""


@pytest.mark.spec("INS-2")
def test_a_missing_undo_touching_nothing_has_nothing_to_undo(measured_model, fake_ollama, tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    fake_ollama.reply("low: a plain request").reply('{"line": "date", "does": "shows the date"}')
    shown = shell.propose(channel, Message("1", "the date"), "", 300, asked("the date"))
    assert "nothing to undo" in shown and "can't be undone" not in shown
    assert shell.take(channel, "1")["undo"] == ""


@pytest.mark.spec("INS-2")
def test_an_irreversible_line_cant_be_undone_whatever_the_model_says(measured_model, fake_ollama, tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    fake_ollama.reply("low: a plain request").reply(
        '{"line": "rm notes.txt", "does": "deletes notes", "touches": "notes.txt", "undo": "git checkout notes.txt"}'
    )
    shown = shell.propose(channel, Message("1", "delete notes"), "", 300, asked("delete notes"))
    assert "can't be undone" in shown and "git checkout" not in shown
    held = shell.take(channel, "1")
    assert held["undo"] == "can't be undone" and held["touches"] == []
    assert held["irreversible"] is True


@pytest.mark.spec("CNV-18")
def test_an_unreadable_json_answer_is_taken_as_empty(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    for unreadable in ("{bad}", "[1]", "none"):
        fake_ollama.reply("low: a plain request").reply(unreadable)
        assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300, asked("x"))
    fake_ollama.reply("low: a plain request").reply('Sure: {"line": "ls", "does": "lists", "flags": [}')
    assert "```\nls\n```" in shell.propose(channel, Message("1", "x"), "", 300, asked("x"))


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
def test_a_proposed_command_sees_tool_memories_only(measured_model, fake_ollama, tmp_path):

    remembering("tool", "disk usage goes through dust", "dust shows disk usage")
    remembering("fact", "the disk is named Bees", "the disk name")
    Scratchpad.write({"chat": "1", "request": "show disk usage", "line": "du -sh .", "output": "", "code": 0})
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"line": "dust", "does": "shows disk usage"}')
    shell.propose(Recorder(), Message("1", "disk usage"), "", 300, asked("disk usage"))
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert "disk usage goes through dust" in system
    assert "named Bees" not in system and "du -sh ." not in system


@pytest.mark.spec("INS-2")
def test_a_proposal_is_shown_as_a_guess_that_waits(measured_model, fake_ollama, tmp_path):
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    fake_ollama.reply("low: a plain request").reply('{"line": "ls -S", "does": "sorts by size"}')
    shown = shell.propose(channel, Message("1", "by size"), "", 300, asked("by size"))
    assert shown.startswith("🖥 **I think you meant to do it like this:**\n```\nls -S\n```\n**Am I right?**\n")
    assert shell.take(channel, "1")["line"] == "ls -S"


@pytest.mark.spec("INS-9")
def test_a_read_only_line_shaped_like_a_recalled_tool_line_runs_at_once(
    measured_model, fake_ollama, tmp_path
):
    (tmp_path / "logs").mkdir()
    remembering("tool", "the logs by size", "ls -S /var/log")
    shell, channel = Shell(Talk("cmds"), tmp_path, Questions()), Recorder()
    fake_ollama.reply("low: a plain request").reply('{"line": "ls -S logs", "does": "lists by size"}')
    shown = shell.propose(channel, Message("1", "the logs by size"), "", 300, asked("the logs by size"))
    assert shown == "▶️ **Running:**\n```\nls -S logs\n```"
    assert not shell.take(channel, "1")
    shell.proposal_shown(channel, "1", None)
    [episode] = Scratchpad.fresh()
    assert episode["line"] == "ls -S logs" and episode["code"] == 0


@pytest.mark.spec("INS-9")
@pytest.mark.parametrize(
    ("line", "known"),
    [
        ("ls -S", ["ls -l"]),
        ("ls -S", []),
        ("echo hi > out.txt", ["echo hi > out.txt"]),
        ("ls -S $(pwd)", ["ls -S $(pwd)"]),
    ],
)
def test_any_other_line_is_a_guess_that_waits(line, known, tmp_path):
    assert type(Checked.of(line, tmp_path, known)) is PlainLine


@pytest.mark.spec("INS-9")
def test_path_arguments_are_left_out_of_a_lines_shape(tmp_path):
    (tmp_path / "notes.txt").write_text("")
    assert isinstance(Checked.of("wc -l notes.txt", tmp_path, ["wc -l ~/a.txt"]), AtOnceLine)
    assert isinstance(Checked.of("du -sh .", tmp_path, ["du -sh /var"]), AtOnceLine)
    assert type(Checked.of("wc -l missing", tmp_path, ["wc -l"])) is PlainLine


@pytest.mark.spec("REM-6")
def test_a_proposed_command_counts_only_the_tool_memories_it_shows(measured_model, fake_ollama, tmp_path):
    from tinkerkit.memory.memories import Memories

    remembering("tool", "disk usage goes through dust", "dust shows disk usage")
    remembering("fact", "the disk usage is high", "disk usage grows")
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"line": "dust", "does": "shows disk usage"}')
    shell.propose(Recorder(), Message("1", "disk usage"), "", 300, asked("disk usage"))
    recalled = {
        memory["gist"]: memory["recalled"] for memory in Home.store().items(Memories.MEMORIES).values()
    }
    assert recalled == {"disk usage goes through dust": 1, "the disk usage is high": 0}


@pytest.mark.spec("REM-6")
def test_a_proposed_command_with_nothing_recalled_keeps_its_prompt(measured_model, fake_ollama, tmp_path):
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"line": "ls", "does": "lists"}')
    shell.propose(Recorder(), Message("1", "list files"), "", 300, asked("list files"))
    assert "What you remember" not in fake_ollama.calls[-1]["messages"][0]["content"]


@pytest.mark.spec("INS-9")
def test_a_recalled_tool_line_is_asked_to_be_kept_but_paths(measured_model, fake_ollama, tmp_path):
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    remembering("tool", "disk usage goes through dust", "dust shows disk usage")
    fake_ollama.reply("low: a plain request").reply('{"line": "dust", "does": "shows disk usage"}')
    shell.propose(Recorder(), Message("1", "disk usage"), "", 300, asked("disk usage"))
    assert Prompts.COMMAND_RECALLED in fake_ollama.calls[-1]["messages"][0]["content"]


@pytest.mark.spec("INS-9")
def test_a_proposed_command_names_the_system_and_its_tools(measured_model, fake_ollama, tmp_path):
    shell = Shell(Talk("cmds"), tmp_path, Questions())
    fake_ollama.reply("low: a plain request").reply('{"line": "ls", "does": "lists"}')
    shell.propose(Recorder(), Message("1", "list files"), "", 300, asked("list files"))
    expected = system_name(os.uname().sysname)
    assert f"({expected}, shell" in fake_ollama.calls[-1]["messages"][0]["content"]
    assert system_name("Darwin") == Prompts.MACOS and system_name("Linux") == "Linux"


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


@pytest.mark.spec("INS-9")
def test_a_recalled_tool_memory_shows_the_request_and_its_line():
    item = {"id": "m1", "kind": "tool", "gist": "show disk space", "text": "df -h"}
    assert recalled_line(item) == "show disk space → df -h"


@pytest.mark.spec("CNV-20")
def test_shell_proposes_one_command(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"line": "rm -rf build", "does": "deletes the build folder"}')
    shown = shell.propose(channel, Message("1", "clean up"), "", 300, asked("clean up"))
    assert "```\nrm -rf build\n```" in shown and "can't be undone" in shown
    proposed = shell.take(channel, "1")
    assert (
        proposed
        == {
            "line": "rm -rf build",
            "asked": "clean up",
            "level": "low",
            "shown": None,
            "does": "deletes the build folder",
            "touches": [],
            "undo": "can't be undone",
            "irreversible": True,
        }
        and shell.take(channel, "1") is None
    )
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"line": "ls\\nrm x", "does": "two"}')
    assert shell.propose(channel, Message("1", "x"), "", 300, asked("x")).startswith(
        "🤷 **No command found:**"
    )
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply("{}")
    assert "describe it another way" in shell.propose(channel, Message("1", "x"), "", 300, asked("x"))
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"line": "df -h", "does": "shows the free space", "undo": "true"}')
    assert "can't be undone" not in shell.propose(channel, Message("1", "disk"), "", 300, asked("disk"))


@pytest.mark.spec("INS-2")
def test_shell_refuses_a_line_that_is_no_command(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()

    def proposed(line):
        fake_ollama.reply("low: a plain request")
        fake_ollama.reply(json.dumps({"line": line, "does": "something"}))
        shown = shell.propose(channel, Message("1", "x"), "", 300, asked("x"))
        shell.take(channel, "1")
        return shown

    fake_ollama.reply("low: a plain request").reply('{"line": "ls" "does": }')
    unreadable = shell.propose(channel, Message("1", "x"), "", 300, asked("x"))
    assert unreadable.startswith("🤷 **Couldn't read the model's answer:**")
    assert "No command found" not in unreadable and shell.take(channel, "1") is None
    for prose in ("No single line can satisfy this request.", "I can't do that", "./no-such-script.sh"):
        assert proposed(prose).startswith("🤷 **No command found:**")
        assert shell.take(channel, "1") is None
    for command in ("cd /tmp && ls", "for f in *; do echo $f; done", "LANG=C ls -l", "/bin/ls"):
        assert f"```\n{command}\n```" in proposed(command)


@pytest.mark.spec("INS-3")
def test_shell_refuses_a_root_line_without_waiting(measured_model, fake_ollama):
    shell, channel = Shell(Talk("cmds"), Path.cwd(), Questions()), Recorder()
    fake_ollama.reply("low: a plain request")
    fake_ollama.reply('{"line": "sudo rm -rf /tmp/x", "does": "deletes x"}')
    shown = shell.propose(channel, Message("1", "delete x"), "", 300, asked("delete x"))
    assert "sudo isn't supported yet" in shown and "/confirm" not in shown
    assert shell.take(channel, "1") is None


@pytest.mark.spec("INS-3")
@pytest.mark.parametrize(
    "line",
    [
        "sudo ls",
        "su -c ls",
        "doas ls",
        "/usr/bin/sudo ls",
        "ls && sudo reboot",
        "echo hi | sudo tee /etc/x",
        """osascript -e 'do shell script "ls" with administrator privileges'""",
    ],
)
def test_checked_refuses_root_lines(tmp_path, line):
    assert isinstance(Checked.of(line, tmp_path), RootLine)


@pytest.mark.spec("INS-3")
@pytest.mark.parametrize(
    "line",
    [
        "rm -rf build",
        "dd if=/dev/zero of=disk.img",
        "git reset --hard HEAD~1",
        "git push --force",
        "git push -f origin main",
        "kill 123",
        "chmod -R 755 .",
        "ls; rm x",
        "ls || rm x",
        "echo 'unclosed",
        "find . -delete",
        "find . -name '*.log' -exec rm {} \\;",
        "find . -exec rm -f {} +",
        "find . -execdir rm {} ';' -print",
        "find . -ok rm {} \\;",
        "find . -okdir git reset --hard \\;",
    ],
)
def test_checked_finds_irreversible_lines(tmp_path, line):
    assert isinstance(Checked.of(line, tmp_path), UndoneLine)


@pytest.mark.spec("INS-3")
@pytest.mark.parametrize(
    "line",
    [
        "ls -la",
        "git reset HEAD x",
        "git push",
        "chmod 644 x",
        "echo hi > new.txt",
        "echo hi >> kept.txt",
        "trash x",
    ],
)
def test_checked_passes_reversible_lines(tmp_path, line):
    (tmp_path / "kept.txt").write_text("")
    assert isinstance(Checked.of(line, tmp_path), PlainLine)


@pytest.mark.spec("INS-3")
@pytest.mark.parametrize(
    "line",
    [
        "ls -la",
        "du -sh .",
        "df -h",
        "stat x",
        "wc -l x",
        "cat x | sort | uniq -c",
        "head -n 5 x && tail -n 5 x",
        "file x; pwd",
        "grep -rn 'a > b' .",
        "find . -name '*.py'",
        "/bin/ls",
        "echo hi",
        "which python || whoami",
        "date; uname -a",
        "tree -L 2",
        "uniq x",
        "sort -r x",
        "ls 2>/dev/null",
        "ls >/dev/null",
        "ls 2> /dev/null",
        "ls &>/dev/null",
        "ls 2>&1 | sort",
        "ls >&2",
    ],
)
def test_checked_reads_read_only_lines(line):
    assert Checked.read_only(line)


@pytest.mark.spec("INS-3")
@pytest.mark.parametrize(
    "line",
    [
        "",
        "rm x",
        "ls | xargs rm",
        "env ls",
        "FOO=1 ls",
        "ls && touch x",
        "vim x",
        "less x",
        "echo hi > x",
        "echo hi >> x",
        "cat < x",
        "ls 2> err",
        "ls 2>&1 > out",
        "ls &> out",
        "echo $(rm x)",
        "echo `rm x`",
        "find . -delete",
        "find . -exec rm {} ;",
        "find . -execdir rm {} +",
        "find . -ok rm {} ;",
        "find . -fprint out",
        "sort -o out x",
        "sort --output=out x",
        "uniq x out",
        "tree -o out",
        "echo 'unclosed",
    ],
)
def test_checked_refuses_lines_that_do_more_than_read(line):
    assert not Checked.read_only(line)


@pytest.mark.spec("INS-3")
def test_checked_counts_overwriting_an_existing_file_as_irreversible(tmp_path):
    (tmp_path / "kept.txt").write_text("")
    assert isinstance(Checked.of("echo hi > kept.txt", tmp_path), UndoneLine)
    assert isinstance(Checked.of("echo hi>kept.txt", tmp_path), UndoneLine)
    assert isinstance(Checked.of("echo hi > fresh.txt", tmp_path), PlainLine)


@pytest.mark.spec("INS-3")
@pytest.mark.parametrize(
    "line", ["ls 2>/dev/null", "ls >/dev/null", "ls 2>&1 | sort", "find . -exec cat {} \\;"]
)
def test_checked_keeps_lines_that_write_nothing_reversible(tmp_path, line):
    assert not isinstance(Checked.of(line, tmp_path), UndoneLine)


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
