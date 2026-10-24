import pytest

from tinkerkit import cli, conversation
from tinkerkit.commands import BY_NAME, COMMANDS, GROUPS, deletes, kind_of, ref, refs


@pytest.mark.spec("CMD-1")
def test_every_command_is_defined_once_with_all_it_needs():
    names = [command.name for command in COMMANDS if command.name]
    assert len(names) == len(set(names))
    for command in COMMANDS:
        assert command.group in GROUPS and command.synopsis and command.what and command.rows
        assert command.words in ("none", "one", "any") and command.kind in ("job", "glance", "pauses", "own")


@pytest.mark.spec("CMD-1")
def test_the_kind_of_a_command_and_of_its_actions_comes_from_the_table():
    assert [command.name for command in COMMANDS if command.kind == "job"] == [
        "task",
        "restart",
        "confirm",
        "amend",
        "add",
        "fix",
        "learn",
        "ask",
        "teach",
        "read",
        "exercise",
        "resume",
        "calibrate",
        "research",
    ]
    assert [command.name for command in COMMANDS if command.kind == "pauses"] == ["chat", "command"]
    assert kind_of(["read", "craft.md"]) == "job" and kind_of(["read", "list", "2"]) == "glance"
    assert kind_of(["read", "show", "#1"]) == "glance" and kind_of(["exercise", "status"]) == "glance"
    assert kind_of(["learn", "rust"]) == "job" and kind_of(["learn", "ll", "ls -l"]) == "glance"
    assert (
        kind_of(["help"]) == "glance" and kind_of(["history", "2"]) == "glance" and kind_of(["stop"]) == "own"
    )
    assert kind_of(["fly"]) is None and kind_of([]) is None


@pytest.mark.spec("CMD-1")
def test_the_command_line_and_the_conversation_read_the_table():
    on_the_command_line = set(cli.Cli.COMMANDS) | set(cli.Cli.TALK_COMMANDS) | {"help", "channel", "confirm"}
    assert on_the_command_line == {command.name for command in COMMANDS if command.cli}
    assert set(conversation.Desk.COMMANDS) == {command.name for command in COMMANDS if command.chat}
    assert set(conversation.ONE_WORD_COMMANDS) == {
        command.name for command in COMMANDS if command.chat and command.words == "one"
    }
    assert conversation.USAGE["task"] == "✍️ Write `task WHAT TO BUILD`, e.g. `task a calculator in Python`"
    for command in cli.Cli.COMMANDS:
        parser = cli.Cli.parser(command)
        assert parser.description == BY_NAME[command].about
        assert {option.flag for option in BY_NAME[command].options} <= {
            flag for action in parser._actions for flag in action.option_strings
        }


@pytest.mark.spec("CMD-2")
def test_runs_and_what_deletes():
    assert [command.name for command in COMMANDS if command.run] == [
        "task",
        "restart",
        "confirm",
        "amend",
        "add",
        "fix",
        "resume",
    ]
    for command in [command for command in COMMANDS if command.run and command.name != "restart"]:
        assert "--web" in {option.flag for option in command.options}, command.name
    assert [
        command.name for command in COMMANDS if "--confirm" in {option.flag for option in command.options}
    ] == ["task", "restart", "add", "fix"]
    assert [command.name for command in COMMANDS if command.deletes] == ["restart", "clear", "reset"]
    assert deletes(["clear", "memory"]) and deletes(["channel", "remove", "bot"])
    assert not deletes(["channel", "list"]) and not deletes(["status"]) and not deletes([])


@pytest.mark.spec("CMD-3")
def test_project_references():
    assert ref("@calc-py") == "calc-py" and ref("@a1b2c3") == "a1b2c3"
    assert ref("calc") is None and ref("@") is None and ref("") is None and ref("100") is None
    assert refs(["@calc", "add", "2"]) == ("calc", ["add", "2"])
    a_project_after_the_first_word = ["add", "@calc"]
    assert refs(a_project_after_the_first_word) == ("", a_project_after_the_first_word)
    assert refs([]) == ("", [])
