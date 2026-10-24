import pytest

from tinkerkit import cli
from tinkerkit import conversation
from tinkerkit.commands import BY_NAME, COMMANDS, GROUPS, ref, refs


@pytest.mark.spec("CMD-1")
def test_every_command_is_defined_once_with_all_it_needs():
    names = [command.name for command in COMMANDS if command.name]
    assert len(names) == len(set(names))
    for command in COMMANDS:
        assert command.group in GROUPS and command.synopsis and command.what and command.rows
        assert command.chat in ("own", "long", "quick", None) and command.words in ("none", "one", "any")


@pytest.mark.spec("CMD-1")
def test_the_command_line_and_the_conversation_read_the_table():
    on_the_command_line = set(cli.Cli.COMMANDS) | set(cli.Cli.TALK_COMMANDS) | {"help", "channel", "confirm"}
    assert on_the_command_line == {command.name for command in COMMANDS if command.cli}
    assert set(conversation.Desk.COMMANDS) == {command.name for command in COMMANDS if command.chat}
    assert conversation.LONG_COMMANDS == tuple(command.name for command in COMMANDS if command.chat == "long")
    assert set(conversation.ONE_WORD_COMMANDS) == {command.name for command in COMMANDS if command.chat
                                          and command.words == "one"}
    assert conversation.USAGE["task"] == "✍️ Write `task WHAT TO BUILD`, e.g. `task a calculator in Python`"
    for command in cli.Cli.COMMANDS:
        parser = cli.Cli.parser(command)
        assert parser.description == BY_NAME[command].about
        assert {option.flag for option in BY_NAME[command].options} <= {flag for action in parser._actions
                                                                        for flag in action.option_strings}


@pytest.mark.spec("CMD-2")
def test_runs_and_what_deletes():
    assert [command.name for command in COMMANDS if command.run] == [
        "task", "restart", "confirm", "amend", "add", "fix", "resume"]
    for command in [command for command in COMMANDS if command.run and command.name != "restart"]:
        assert "--web" in {option.flag for option in command.options}, command.name
    assert [command.name for command in COMMANDS
            if "--confirm" in {option.flag for option in command.options}] == ["task", "restart", "add", "fix"]
    assert [command.name for command in COMMANDS if command.deletes] == ["restart", "clear", "reset"]
    assert conversation.DELETING_COMMANDS == ("restart", "clear", "reset")


@pytest.mark.spec("CMD-3")
def test_project_references():
    assert ref("@calc-py") == "calc-py" and ref("@a1b2c3") == "a1b2c3"
    assert ref("calc") is None and ref("@") is None and ref("") is None and ref("100") is None
    assert refs(["@calc", "add", "2"]) == ("calc", ["add", "2"])
    a_project_after_the_first_word = ["add", "@calc"]
    assert refs(a_project_after_the_first_word) == ("", a_project_after_the_first_word)
    assert refs([]) == ("", [])
