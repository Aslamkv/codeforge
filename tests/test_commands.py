import pytest
from conftest import MEASURED

from tinkerkit import cli, conversation
from tinkerkit.commands import (
    BY_NAME,
    COMMANDS,
    GROUPS,
    CommandMenu,
    MenuAnswer,
    deletes,
    kind_of,
    ref,
    refs,
    slash_matches,
    taught_command,
)
from tinkerkit.messages import Text
from tinkerkit.settings import Config


@pytest.mark.spec("CMD-1")
def test_every_command_is_defined_once_with_all_it_needs():
    names = [command.name for command in COMMANDS if command.name]
    assert len(names) == len(set(names))
    for command in COMMANDS:
        assert command.group in GROUPS and command.synopsis and command.what and command.rows
        assert command.words in (True, False) and command.kind in ("job", "glance", "pauses", "own")


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
        "research",
        "ask",
        "teach",
        "read",
        "exercise",
        "resume",
    ]
    assert [command.name for command in COMMANDS if command.kind == "pauses"] == ["chat", "command"]


@pytest.mark.spec("CMD-1")
@pytest.mark.parametrize(
    "argv,kind",
    [
        (["read", "craft.md"], "job"),
        (["read", "list", "2"], "glance"),
        (["read", "show", "#1"], "glance"),
        (["exercise", "status"], "glance"),
        (["learn", "rust"], "job"),
        (["learn", "ll", "ls -l"], "glance"),
        (["learn", "Python", "3.14"], "job"),
        (["learn", "gs", "=", "git", "status"], "glance"),
        (["help"], "glance"),
        (["history", "2"], "glance"),
        (["stop"], "own"),
        (["channel", "add"], "terminal"),
        (["channel", "remove"], "terminal"),
        (["channel", "start"], "terminal"),
        (["channel", "list"], "glance"),
        (["model"], "glance"),
        (["model", "calibrate"], "glance"),
        (["model", "calibrate", "qwen3"], "job"),
        (["fly"], None),
        ([], None),
    ],
)
def test_the_kind_of_an_action_comes_from_its_command(argv, kind):
    assert kind_of(argv) == kind


@pytest.mark.spec("CMD-1")
def test_model_of_a_never_measured_model_is_a_job(home_store):
    home_store.put("models", "measured", MEASURED)
    Config.save(embed_model="nomic")
    assert kind_of(["model", "measured"]) == "glance"
    assert kind_of(["model", "nomic"]) == "glance"
    assert kind_of(["model", "fresh"]) == "job"


@pytest.mark.spec("JOB-1")
def test_model_of_an_embedding_model_not_yet_in_use_is_a_job(home_store):
    Config.save(embed_model="nomic")
    assert kind_of(["model", "mxbai-embed"]) == "job"


@pytest.mark.spec("CMD-1")
def test_the_command_line_and_the_conversation_read_the_table():
    on_the_command_line = set(cli.Cli.COMMANDS) | set(cli.Cli.TALK_COMMANDS) | {"help", "channel", "confirm"}
    assert on_the_command_line == {command.name for command in COMMANDS if command.cli}
    assert set(conversation.Desk.COMMANDS) == {command.name for command in COMMANDS if command.chat}
    assert conversation.USAGE["task"] == "✍️ Write `/task WHAT TO BUILD`, e.g. `/task a calculator in Python`"
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
    for command in [command for command in COMMANDS if command.run]:
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


@pytest.mark.spec("CLI-1")
@pytest.mark.parametrize(
    "words,taught",
    [
        (["gs", "=", "git", "status"], ("gs", "git status")),
        (["gs=git", "status"], ("gs", "git status")),
        (["serve", "python3 -m http.server"], ("serve", "python3 -m http.server")),
        (["ll", "none"], ("ll", "none")),
        (["Python", "3.14"], None),
        (["food", "bank", "inventory"], None),
        (["rust"], None),
    ],
)
def test_a_learn_is_a_command_only_when_its_words_say_so(words, taught):
    assert taught_command(words) == taught


def names(commands):
    return [command.name for command in commands]


def menu_on(text):
    menu = CommandMenu()
    menu.follow(text)
    return menu


@pytest.mark.spec("MSG-10")
def test_slash_matches_name_starts_first_then_name_holds_in_table_order():
    chat = [command.name for command in COMMANDS if command.chat]
    assert names(slash_matches("/")) == chat
    assert names(slash_matches("/st")) == ["status", "stop", "restart", "history"]
    assert names(slash_matches("/mod")) == ["model"]
    assert names(slash_matches("/xyz")) == []


@pytest.mark.spec("MSG-10")
def test_slash_matches_only_chat_commands_and_only_a_slash_word():
    not_chat = [command.name for command in COMMANDS if not command.chat and command.name]
    assert not set(names(slash_matches("/"))) & set(not_chat)
    assert slash_matches("task") == [] and slash_matches("") == [] and slash_matches("/task a") == []


@pytest.mark.spec("MSG-10")
def test_the_menu_rows_show_the_name_its_synopsis_words_and_what_it_does():
    rows = menu_on("/tas").rows()
    task = BY_NAME["task"]
    assert rows == [(True, "/task", "WHAT TO BUILD", task.what)]
    assert menu_on("/runs").rows()[0][2] == ""


@pytest.mark.spec("MSG-10")
def test_up_and_down_move_the_current_row_scroll_eight_and_stop_at_the_ends():
    menu = menu_on("/")
    count = len(menu.matches)
    assert count > 9
    assert [row[1] for row in menu.rows()] == ["/" + name for name in names(menu.matches[:8])]
    assert menu.press("up") == MenuAnswer(None, False) and menu.current == 0
    for _ in range(8):
        menu.press("down")
    rows = menu.rows()
    assert len(rows) == 8 and rows[-1] == (True, "/" + menu.matches[8].name, *rows[-1][2:])
    assert rows[0][1] == "/" + menu.matches[1].name
    for _ in range(count + 3):
        menu.press("down")
    assert menu.current == count - 1 and menu.rows()[-1][0]
    for _ in range(count + 3):
        menu.press("up")
    assert menu.current == 0 and menu.rows()[0] == (True, "/" + menu.matches[0].name, *menu.rows()[0][2:])


@pytest.mark.spec("MSG-10")
def test_tab_fills_the_line_and_closes_the_menu():
    menu = menu_on("/ru")
    assert menu.press("tab") == MenuAnswer("/runs ", False) and not menu.open
    menu = menu_on("/ta")
    assert menu.press("tab") == MenuAnswer("/task ", False) and not menu.open


@pytest.mark.spec("MSG-10")
def test_enter_sends_a_command_without_words_and_fills_one_that_takes_words():
    menu = menu_on("/ru")
    assert menu.press("enter") == MenuAnswer("/runs", True) and not menu.open
    menu = menu_on("/ta")
    assert menu.press("enter") == MenuAnswer("/task ", False) and not menu.open
    menu.follow("/task ")
    assert not menu.open


@pytest.mark.spec("MSG-10")
def test_esc_closes_the_menu_until_typing_narrows_it_again():
    menu = menu_on("/r")
    assert menu.press("esc") == MenuAnswer(None, False) and not menu.open
    menu.follow("/r")
    assert not menu.open
    menu.follow("/ru")
    assert menu.open and names(menu.matches) == ["runs", "run"]


@pytest.mark.spec("MSG-10")
def test_the_menu_is_closed_without_a_match_and_leaves_other_keys():
    menu = menu_on("/xyz")
    assert not menu.open and menu.press("down") is None and menu.press("enter") is None
    menu = menu_on("/r")
    assert menu.open and menu.press("left") is None and menu.press(Text("u")) is None
    assert menu.press("backspace") is None
