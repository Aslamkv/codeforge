"""CMD: Tinker's commands, defined once, and how a project is named in them."""

import pytest

import tinker
from tinkerkit import conversation
from tinkerkit.commands import BY_NAME, COMMANDS, GROUPS, ref, refs


@pytest.mark.spec("CMD-1")
def test_every_command_is_defined_once_with_all_it_needs():
    names = [c.name for c in COMMANDS if c.name]
    assert len(names) == len(set(names))
    for command in COMMANDS:
        assert command.group in GROUPS and command.synopsis and command.what and command.rows
        assert command.chat in ("own", "long", "quick", None) and command.words in ("none", "one", "any")


@pytest.mark.spec("CMD-1")
def test_the_command_line_and_the_conversation_read_the_table():
    cli = set(tinker.Cli.COMMANDS) | set(tinker.Cli.TALK) | {"help", "channel", "confirm"}
    assert cli == {c.name for c in COMMANDS if c.cli}
    assert set(conversation.Desk.COMMANDS) == {c.name for c in COMMANDS if c.chat}
    assert conversation.LONG == tuple(c.name for c in COMMANDS if c.chat == "long")
    assert set(conversation.ONE_WORD) == {c.name for c in COMMANDS if c.chat and c.words == "one"}
    assert conversation.USAGE["task"] == "✍️ Write `task WHAT TO BUILD`, e.g. `task a calculator in Python`"
    for command in tinker.Cli.COMMANDS:
        parser = tinker.Cli.parser(command)
        assert parser.description == BY_NAME[command].about
        assert {o.flag for o in BY_NAME[command].options} <= {s for a in parser._actions for s in a.option_strings}


@pytest.mark.spec("CMD-2")
def test_runs_and_what_deletes():
    assert [c.name for c in COMMANDS if c.run] == ["task", "restart", "confirm", "amend", "add", "fix", "resume"]
    for command in [c for c in COMMANDS if c.run and c.name != "restart"]:
        assert "--web" in {o.flag for o in command.options}, command.name
    assert [c.name for c in COMMANDS if "--confirm" in {o.flag for o in c.options}] == ["task", "restart", "add", "fix"]
    assert [c.name for c in COMMANDS if c.deletes] == ["restart", "clear", "reset"]
    assert conversation.DELETES == ("restart", "clear", "reset")


@pytest.mark.spec("CMD-3")
def test_project_references():
    assert ref("@calc-py") == "calc-py" and ref("@a1b2c3") == "a1b2c3"
    assert ref("calc") is None and ref("@") is None and ref("") is None and ref("100") is None
    assert refs(["@calc", "add", "2"]) == ("calc", ["add", "2"])
    assert refs(["add", "@calc"]) == ("", ["add", "@calc"])  # only the first word names a project
    assert refs([]) == ("", [])
