import json
import re

import pytest

from tinkerkit.prompts import Part, Prompts, PromptTooLarge, Reply
from tinkerkit.settings import MIN_TRIMMED_PROSE_CHARS
from tinkerkit.workspace import Toolchain


@pytest.mark.spec("PRM-1")
def test_reply_drops_thinking_and_whitespace():
    assert Reply("  <think>hmm\nwell</think>\n answer \n").text == "answer"
    assert Reply(None).text == ""
    assert Reply("half a thought\n</think>\n answer ").text == "answer"
    assert Reply("<think>a</think> first </think> answer").text == "answer"


@pytest.mark.spec("PRM-2")
@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param(
            'Sure! Here it is:\n```json\n{"a": [1, 2]}\n```\nDone.', {"a": [1, 2]}, id="prose around it"
        ),
        pytest.param('{"a": {"b": [1, 2]}', {"a": {"b": [1, 2]}}, id="left open at the end"),
        pytest.param('{"a": [{"b": 1]}', {"a": [{"b": 1}]}, id="closer missing before an outer one"),
        pytest.param('{"a": {"b": [1]}"}', {"a": {"b": [1]}}, id="stray quote between closers"),
        pytest.param(
            '{"question": "Which one?", "suggestions": ["a", "b"]}"}',
            {"question": "Which one?", "suggestions": ["a", "b"]},
            id="stray quote after the whole object",
        ),
        pytest.param('{"k": "[]}"}', {"k": "[]}"}, id="closers and quote inside a string"),
        pytest.param('{"a": [1, 2]', {"a": [1, 2]}, id="object never closed"),
        pytest.param(
            '{"line": "cd /tmp/b && (find . -name "*.log" -delete)", "does": "x", "touches": [], '
            '"undo": "can\'t be undone"}',
            {
                "line": 'cd /tmp/b && (find . -name "*.log" -delete)',
                "does": "x",
                "touches": [],
                "undo": "can't be undone",
            },
            id="unescaped quotes inside a string value",
        ),
        pytest.param(
            '{"a": ["say "hi" ", "b"]}', {"a": ['say "hi" ', "b"]}, id="inner quotes in a list item"
        ),
        pytest.param(
            '{"confirm":["t4"],"question":"q","suggestions":["x","y"]',
            {"confirm": ["t4"], "question": "q", "suggestions": ["x", "y"]},
            id="reply ends before its last closer",
        ),
        pytest.param('} then {"a": 1', {"a": 1}, id="closer only before the object"),
        pytest.param(
            'Fill {name} and {x: 1} in:\n{"a": {"b": 1}}\nThen use {it}.',
            {"a": {"b": 1}},
            id="braces in prose before and after",
        ),
        pytest.param('{"a": 1} or else {"b": 2}', {"a": 1}, id="first whole object wins"),
        pytest.param('Here {"a": 1} {broken', {"a": 1}, id="unclosed brace after the object"),
        pytest.param(
            r'{"line": "find . -name x -exec mv {} notes/ \;", "does": "x", "touches": [], "undo": "y"}',
            {"line": r"find . -name x -exec mv {} notes/ \;", "does": "x", "touches": [], "undo": "y"},
            id="bare backslash escape in a string",
        ),
        pytest.param(
            r'{"line": "grep \"a\" . -exec rm {} \;", "undo": "y"}',
            {"line": r'grep "a" . -exec rm {} \;', "undo": "y"},
            id="bare backslash beside a valid escape",
        ),
    ],
)
def test_json_in_prose_mends_the_usual_slips(text, expected):
    assert Reply(text).json() == expected


@pytest.mark.spec("PRM-2")
def test_json_drops_strings_copied_from_the_prompt_example():
    system = (
        'Answer as JSON: {"why":"why it does not hold","suggestions":["2 to 4 short answers"],'
        '"kind":"done","items":[{"gist":"a short gist"}]}'
    )
    reply = (
        '{"why":"why it does not hold","suggestions":["2 to 4 short answers","yes please"],'
        '"kind":"done","items":[{"gist":"a short gist"},{"gist":"a real gist"}],"empty":{}}'
    )
    assert Reply(reply).json(system) == {
        "suggestions": ["yes please"],
        "kind": "done",
        "items": [{"gist": "a real gist"}],
        "empty": {},
    }
    assert Reply(reply).json()["why"] == "why it does not hold"
    assert Reply('{"why":"why it does not hold"}').json(system) == {}


@pytest.mark.spec("PRM-2")
def test_json_without_an_object_or_beyond_mending():
    with pytest.raises(ValueError, match="no JSON object"):
        Reply("no json here").json()
    with pytest.raises(json.JSONDecodeError):
        Reply('{"a": tru}').json()
    with pytest.raises(json.JSONDecodeError):
        Reply('{"a": 1,}').json()
    with pytest.raises(ValueError, match="no JSON object"):
        Reply('```json\n{\n  "thought": "never closes').json()


@pytest.mark.spec("PRM-3")
@pytest.mark.parametrize(
    "header",
    [
        "<<<FILE app.py>>>",
        "<<<FILE path=app.py>>>",
        "<<<FILE: app.py>>>",
        '<<<FILE "app.py">>>',
        "<<<FILE app.py",
        "<<<FILE file: app.py>>>",
    ],
)
def test_files_read_every_header_form(header):
    assert Reply(f"{header}\nprint(1)\n<<<END>>>").files() == [("app.py", "print(1)")]


@pytest.mark.spec("PRM-3")
def test_files_several_blocks_with_normalized_paths():
    reply = Reply(
        "<<<FILE ./src/a.py>>>\na = 1\n<<<END>>>\ntext\n<<<FILE tests/test_a.py>>>\nimport a\n<<<END>>>"
    )
    assert reply.files() == [("src/a.py", "a = 1"), ("tests/test_a.py", "import a")]


@pytest.mark.spec("PRM-3")
@pytest.mark.parametrize(
    "text,expected",
    [
        ("<<<FILE path>>>real/app.py\nx = 1\n<<<END>>>", [("real/app.py", "x = 1")]),
        ("<<<FILE path>>>\nreal/app.py\nx = 1\n<<<END>>>", [("real/app.py", "x = 1")]),
        ("<<<FILE app.py>>>\nthe complete file\n<<<END>>>", []),
        ("<<<FILE app.py>>>x=1\ny = 2\n<<<END>>>", [("app.py", "x=1\ny = 2")]),
        ("<<<FILE app.py>>>\n```python\nx = 1\n```\n>>>\n<<<END>>>", [("app.py", "x = 1")]),
        ("<<<FILE README.md>>>\n```sh\nmake\n```\n<<<END>>>", [("README.md", "```sh\nmake\n```")]),
    ],
)
def test_files_mend_placeholders_debris_and_fences_but_keep_markdown_fences(text, expected):
    assert Reply(text).files() == expected


@pytest.mark.spec("PRM-4")
def test_line_edits():
    reply = Reply(
        "<<<LINES app.py 3-4>>>\n  3| a = 1\n  4| b = 2\n<<<END>>>\n<<<LINES lib.py lines=7-7>>>\nx\n<<<END>>>"
    )
    assert reply.line_edits() == [("app.py", 3, 4, ["a = 1", "b = 2"]), ("lib.py", 7, 7, ["x"])]


@pytest.mark.spec("PRM-5")
def test_as_file_takes_the_one_fenced_block():
    assert Reply("Here:\n```python\nprint(1)\n```\n").as_file("app.py").files() == [("app.py", "print(1)")]
    two = Reply("```\na\n```\n```\nb\n```")
    assert two.as_file("app.py") is two
    blocks = Reply("<<<FILE x.py>>>\n1\n<<<END>>>")
    assert blocks.as_file("app.py") is blocks


@pytest.mark.spec("PRM-6")
def test_files_complete_only_once_the_model_runs_on():
    answer = "<<<FILE a.py>>>\nx = 1\n<<<END>>>"
    assert not Reply.files_complete(answer)
    assert not Reply.files_complete(answer + "\n" + "junk " * 10)
    assert Reply.files_complete(answer + "\n" + "junk " * 70)
    assert not Reply.files_complete(answer + "\n" + "junk " * 70 + "<<<FILE b.py>>>")


@pytest.mark.spec("PRM-6")
def test_files_ended_once_the_last_block_ends():
    answer = "<<<FILE a.py>>>\nx = 1\n<<<END>>>"
    assert Reply.files_ended(answer) and Reply.files_ended(answer + "\nthat is all")
    assert not Reply.files_ended("<<<FILE a.py>>>\nx = 1") and not Reply.files_ended(
        answer + "\n<<<LINES b.py 1-2>>>"
    )


@pytest.mark.spec("PRM-6")
def test_wrote_once_every_wanted_file_has_its_block():
    done = Reply.wrote(["a.py", "./b.py"])
    assert not done("<<<FILE a.py>>>\n1\n<<<END>>>")
    assert done("<<<FILE a.py>>>\n1\n<<<END>>>\n<<<FILE b.py>>>\n2\n<<<END>>>")


@pytest.mark.spec("PRM-7")
def test_json_complete():
    assert Reply.json_complete('ok {"a": [1]}')
    assert not Reply.json_complete('ok {"a": [1,],}')
    assert not Reply.json_complete('{"a": 1')
    assert not Reply.json_complete('{"a": 1} trailing')
    assert not Reply.json_complete("no braces")
    assert not Reply.json_complete('{"a": }')


@pytest.mark.spec("PRM-8")
def test_marker_lines():
    reply = Reply(
        "CAUSE: the sum was wrong\nTEST WRONG: expects 6 for 2+3\nCRITERION: add(2, 3) is 5\n"
        'CRITERION test_add: add(2, 3) is 5\nTOOLCHAIN: {"lint": "ruff check ."}\n'
        "<<<FILE a.py>>>\n1\n<<<END>>>\n<<<LINES a.py 1-1>>>\n2\n<<<END>>>\n<<<LINES b.py 1-1>>>\n3\n<<<END>>>"
    )
    assert reply.cause() == "the sum was wrong"
    assert reply.test_wrong() == "expects 6 for 2+3"
    assert reply.criterion() == "add(2, 3) is 5"
    assert reply.corrected_criteria() == {"test_add": "add(2, 3) is 5"}
    assert reply.toolchain_line() == {"lint": "ruff check ."}
    assert reply.named_paths() == ["a.py", "b.py"]
    assert Reply("nothing").cause() == "" and Reply("nothing").toolchain_line() is None


@pytest.mark.spec("PRM-9")
def test_spec_json_keeps_the_spec_when_only_data_is_broken():
    data = Reply('{"language": "Python", "criteria": [], "data": "sample = {broken: [}').spec_json()
    assert data["language"] == "Python" and data["data"].startswith('"sample = {broken')


@pytest.mark.spec("PRM-10")
def test_assemble_orders_by_place_and_leaves_out_empty_optional_parts():
    parts = [
        Part("Middle", "m", True),
        Part("Last", "l", True, place="last"),
        Part("Empty", ""),
        Part("First", "f", place="first"),
        Part("Required empty", "", True),
    ]
    text = Part.assemble(parts, 1000)
    assert [line for line in text.splitlines() if line.startswith("##")] == [
        "## First",
        "## Middle",
        "## Required empty",
        "## Last",
    ]


@pytest.mark.spec("PRM-10")
def test_assemble_fits_optional_parts_while_they_fit():
    parts = [Part("R", "r" * 100, True), Part("Atomic", "a" * 500, atomic=True), Part("Prose", "p" * 1000)]
    text = Part.assemble(parts, 600)
    assert len(text) <= 600
    assert "## Atomic" not in text
    assert "## Prose\n" + "p" * 100 in text and "[...]" in text


@pytest.mark.spec("PRM-10")
def test_assemble_leaves_out_a_part_too_small_to_shorten():
    text = Part.assemble([Part("R", "r" * 100, True), Part("Prose", "p" * 1000)], 200)
    assert "## Prose" not in text


@pytest.mark.spec("PRM-11")
def test_required_prose_is_shortened_head_or_tail():
    notes = []
    head, tail = Part("Head", "H" + "x" * 2000, True), Part("Tail", "y" * 2000 + "T", True, keep="tail")
    text = Part.assemble([head, tail], 2000, notes)
    assert len(text) <= 2000 and notes == ["Head", "Tail"]
    assert "## Head\nH" in text and "T\n\n## Tail" not in text and text.endswith("T")
    assert text.count("[...]") == 2


@pytest.mark.spec("PRM-11")
def test_exact_parts_are_never_shortened():
    exact = Part("File app.py", "c" * 3000, True, exact=True)
    with pytest.raises(PromptTooLarge, match="File app.py"):
        Part.assemble([exact, Part("Prose", "p" * 3000, True)], 2000)
    text = Part.assemble([exact, Part("Prose", "p" * 3000, True)], 3700)
    assert "c" * 3000 in text


@pytest.mark.spec("PRM-11")
def test_prose_is_never_cut_below_the_floor():
    with pytest.raises(PromptTooLarge):
        Part.assemble([Part("Prose", "p" * 3000, True)], 100)
    shortened = Part("P", "p" * 3000, True).shortened(MIN_TRIMMED_PROSE_CHARS)
    assert len(shortened.text) <= MIN_TRIMMED_PROSE_CHARS and shortened.text.endswith("[...]")


@pytest.mark.spec("PRM-12")
def test_part_flags_render_and_size():
    assert not Part("t", "x", exact=True).exact and Part("t", "x", True, exact=True).exact
    assert Part("t", "x", atomic=True).atomic and not Part("t", "x", True, atomic=True).atomic
    relaxed = Part("t", "x", True, exact=True, place="first").relaxed()
    assert not relaxed.exact and relaxed.required and relaxed.place == "first"
    part = Part("Title", " text ")
    assert part.render() == "## Title\ntext" and part.size() == len("## Title\ntext") + 2
    assert Part("", "bare").render() == "bare"
    assert (
        Part.total_size([Part("a", "x"), Part("b", ""), Part("c", "", True)])
        == Part("a", "x").size() + Part("c", "", True).size()
    )


@pytest.mark.spec("PRM-13")
def test_the_prompts_example_json_parses_and_its_toolchain_works():
    spec = json.loads(Prompts.SPEC_JSON + ",\n " + Prompts.TOOLCHAIN_JSON + "}")
    assert spec["criteria"] and spec["files"] and spec["entry"] == "tip.py"
    assert not Toolchain(spec["toolchain"]).problems()
    assert json.loads(Prompts.SPEC_JSON + "}")["language"] == "Python"
    assert json.loads("{" + Prompts.TOOLCHAIN_JSON + "}")["toolchain"]["test_all"] == "pytest -q"
    readme = re.search(r"Reply with ONLY JSON: (\{.*\})", Prompts.README_SYSTEM, re.S).group(1)
    assert json.loads(readme)["usage"][0]["command"] == "python3 calculator.py add 5 2"
    for prompt in (Prompts.ASK_SYSTEM, Prompts.ASK_CHECK_AGAIN):
        reply = re.search(r"Reply with ONLY JSON: (\{.*\})", prompt, re.S).group(1)
        assert json.loads(reply)["check"]["kind"] == "sql|command"
    assert (
        Prompts.SPEC_JSON[:-1] in Prompts.SPEC_SYSTEM and Prompts.TOOLCHAIN_JSON in Prompts.TOOLCHAIN_SYSTEM
    )


@pytest.mark.spec("KNW-23")
def test_every_prompt_that_writes_searches_writes_them_with_engines_and_count():
    searches_in = {
        Prompts.RESEARCH_PLAN_SYSTEM: lambda reply: reply["questions"][0]["queries"],
        Prompts.RESEARCH_ANALYSIS_SYSTEM: lambda reply: [
            reply["gaps"][0]["search"],
            reply["checks"][0]["search"],
        ],
        Prompts.RESEARCH_LAST_ANALYSIS_SYSTEM: lambda reply: [reply["gaps"][0]["search"]],
        Prompts.SLOT_PLAN_SYSTEM: lambda reply: list(reply["followups"].values()),
        Prompts.TEACH_SYSTEM: lambda reply: reply["searches"],
    }
    for prompt, searches in searches_in.items():
        example = json.loads(prompt[prompt.index("{", prompt.index("ONLY JSON")) :], strict=False)
        assert Prompts.SEARCH_ENGINES in prompt
        assert "A query is short and never names an engine" in prompt
        assert all(set(search) == {"query", "engines", "count"} for search in searches(example))


@pytest.mark.spec("PRM-13")
def test_the_templates_format():
    assert "60 words" in Prompts.BRIEF_SYSTEM.format(words=60)
    chat = Prompts.CHAT_SYSTEM.format(style=Prompts.PHONE_STYLE, state="Running: calc")
    assert Prompts.PHONE_STYLE in chat and "Running: calc" in chat
    assert "/status — show" in Prompts.CHAT_ROUTE_SYSTEM.format(index="/status — show")
    fill = Prompts.CHAT_FILL_SYSTEM.format(commands="/clear chat", state="Running: calc")
    assert "/clear chat" in fill and "Running: calc" in fill
    tell = Prompts.COMMAND_TELL_SYSTEM.format(style=Prompts.TERMINAL_STYLE)
    assert Prompts.TERMINAL_STYLE in tell
    command = Prompts.COMMAND_SYSTEM.format(system="Darwin", shell="zsh", folder="/tmp")
    assert all(f'"{key}": ' in command for key in ("line", "does", "touches", "undo"))
    systems = [value for key, value in vars(Prompts).items() if key.endswith("_SYSTEM")]
    assert len(systems) >= 25 and all(isinstance(system, str) and system.strip() for system in systems)


@pytest.mark.spec("INS-9")
def test_the_command_prompt_keeps_a_recalled_line_and_the_systems_own_flags():
    command = Prompts.COMMAND_SYSTEM.format(system=Prompts.MACOS, shell="zsh", folder="/tmp")
    assert "this system's own tools" in command and "--block-size" in Prompts.MACOS
    assert "under What you remember" in Prompts.COMMAND_RECALLED
    assert "changing only its paths" in Prompts.COMMAND_RECALLED


@pytest.mark.spec("INS-2")
def test_the_command_prompt_answers_a_stated_problem_with_a_looking_line():
    command = Prompts.COMMAND_SYSTEM.format(system=Prompts.MACOS, shell="zsh", folder="/tmp")
    assert '"line": ""' not in command
    assert "only lists or measures what it names" in command
    assert "never one that changes or deletes" in command


@pytest.mark.spec("CNV-18")
def test_the_phone_style_is_brief_and_the_terminal_style_is_not_a_phone():
    assert Prompts.PHONE_STYLE != Prompts.TERMINAL_STYLE
    assert "phone" in Prompts.PHONE_STYLE and "short lines" in Prompts.PHONE_STYLE
    assert "phone" not in Prompts.TERMINAL_STYLE and "short lines" not in Prompts.TERMINAL_STYLE
    assert all("Markdown" in style for style in (Prompts.PHONE_STYLE, Prompts.TERMINAL_STYLE))


@pytest.mark.spec("CNV-18")
def test_the_route_prompt_asks_for_only_the_command_names_or_none():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/clear — wipe the chat")
    assert "only the command names, one per line" in route and "`none`" in route
    assert "how to use one of Tinker's commands is `/help`" in route
    assert "how to do anything else is `none`" not in route
    assert "(`/help` for `how do I learn a skill?`)" in route
    assert route.index("how to use one of Tinker's commands") < route.index("only the command names")


@pytest.mark.spec("CNV-18")
def test_the_route_prompt_sends_doing_and_how_to_on_this_computer_to_instruct():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/instruct — do it")
    assert (
        "Asked to do something on this computer that no command does, or how to do it there (look at, "
        "list, measure, change or run something in its files, folders or terminal), is `/instruct`" in route
    )
    assert "a question about what is on this computer is `/instruct`" in route
    assert route.index("`/instruct`") < route.index("only the command names")


@pytest.mark.spec("CNV-18")
def test_the_route_prompt_never_asks_and_sends_unsure_to_instruct():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/instruct — do it")
    assert "`ask`" not in route
    assert "(`/instruct` for `my downloads folder is a mess`)" in route
    assert "a message unsure between running something and answering" in route


@pytest.mark.spec("CNV-18")
def test_the_route_prompt_keeps_none_for_words_alone():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/instruct — do it")
    assert "`none` is only for wanting words alone, such as knowledge " in route
    assert "or how to do something away from this computer" in route
    assert "(`none` for `how do I fold a fitted sheet?`)" in route
    assert "or `none` when no command does it" not in route


@pytest.mark.spec("CNV-18")
def test_the_route_prompt_sends_a_knowledge_question_to_none_never_knowledge():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/knowledge — browse what Tinker learned")
    assert (
        "(`none` for `what is a black hole?`, never `/knowledge`, which browses what Tinker learned)" in route
    )


@pytest.mark.spec("CNV-18")
def test_the_route_prompt_puts_the_rules_before_the_command_index():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/clear — wipe the chat")
    assert route.startswith("You pick which of Tinker's commands do what the person asks. ")
    assert route.endswith("\nThe commands, each with what it is for:\n/clear — wipe the chat")
    assert route.index("only the command names") < route.index("/clear — wipe the chat")


@pytest.mark.spec("CNV-18", "RVW-3")
def test_the_answer_prompt_holds_no_commands():
    chat = Prompts.CHAT_SYSTEM.format(style=Prompts.PHONE_STYLE, state="")
    assert "{index}" not in Prompts.CHAT_SYSTEM and "command names" not in chat
    assert "/" not in chat.replace("`/instruct`", "")


@pytest.mark.spec("CNV-18")
def test_the_answer_prompt_only_talks_and_points_actions_to_instruct():
    chat = Prompts.CHAT_SYSTEM.format(style=Prompts.PHONE_STYLE, state="")
    assert "nothing runs, so never say you did, ran, checked, changed or deleted anything" in chat
    assert "send it as `/instruct` followed by what to do" in chat
    assert "never give shell commands to run" in chat


@pytest.mark.spec("CNV-18")
def test_the_answer_prompts_instruct_pointer_is_its_last_rule():
    chat = Prompts.CHAT_SYSTEM.format(style=Prompts.PHONE_STYLE, state="")
    assert chat.endswith("followed by what to do.")
    assert chat.index(Prompts.PHONE_STYLE) < chat.index("nothing runs")


@pytest.mark.spec("CNV-18", "SYS-1")
def test_the_fill_prompt_asks_for_only_the_check_line_when_asked_what_a_system_check_tells():
    fill = Prompts.CHAT_FILL_SYSTEM.format(commands="/system battery", state="")
    assert "only that check's line" in fill and "`/system battery`" in fill and "am I charged?" in fill


@pytest.mark.spec("CNV-18", "SYS-1")
def test_the_route_prompt_keeps_ram_for_system_ram():
    route = Prompts.CHAT_ROUTE_SYSTEM.format(index="/knowledge — ask\n/system — checks")
    assert "`knowledge`" not in route and "where only RAM means `/system ram`" in route


@pytest.mark.spec("CNV-18", "CNV-11")
def test_the_fill_prompt_acts_with_options_filled_and_never_invents_required_words():
    fill = Prompts.CHAT_FILL_SYSTEM.format(commands="/channel add [--kind KIND] [--name NAME]", state="")
    assert "`/channel add --kind telegram --name jarvis`" in fill and "called jarvis" in fill
    assert "options filled from their words" in fill and "never invent" in fill
    assert "one question back" in fill and "act on it at once" in fill
    assert "one command per line" in fill and "`/clear chat`" in fill
    assert "You cannot run anything" not in fill


@pytest.mark.spec("CNV-18", "SKL-6")
def test_the_fill_prompt_quotes_a_several_word_value_for_a_slot_that_is_not_the_last():
    fill = Prompts.CHAT_FILL_SYSTEM.format(commands="/skill mgen {prompt}", state="")
    assert "several words for a skill's slot that is not its last is quoted" in fill


@pytest.mark.spec("CNV-18", "SKL-6")
def test_the_fill_prompt_shows_a_skill_learned_and_a_skill_used_as_separate_examples():
    fill = Prompts.CHAT_FILL_SYSTEM.format(commands="/skill learn NAME DESCRIPTION", state="")
    assert (
        '`/skill learn mgen "generate an image from text"` alone for '
        '"I have an alias mgen, use it to generate images from now on"'
    ) in fill
    assert (
        '`/skill mgen a shark` for "generate an image of a shark" once mgen is a skill '
        "(two separate examples, never one reply of both lines), "
        '`/help skill` for "how do I learn a skill?"'
    ) in fill
    assert "and then" not in fill


@pytest.mark.spec("PRM-14")
def test_the_example_toolchain_commands_are_real_commands():
    toolchain = json.loads("{" + Prompts.TOOLCHAIN_JSON + "}")["toolchain"]
    commands = toolchain["setup"] + [value for key, value in toolchain.items() if isinstance(value, str)]
    assert not [command for command in commands if re.search(r"\btinker\b", command)]


@pytest.mark.spec("PRM-15")
def test_the_lessons_prompt_asks_for_general_lessons():
    assert "never name this project, its files, its names, its features or its data" in " ".join(
        Prompts.LESSONS_SYSTEM.split()
    )


@pytest.mark.spec("INT-3")
def test_the_interact_talk_prompt_holds_tinkers_purpose_and_asks_for_a_lasting_habit_in_tinkers_words():
    prompt = " ".join(Prompts.INTERACT_TALK_SYSTEM.split())

    assert " ".join(Prompts.TINKER_PURPOSE.split()) in prompt
    assert (
        "An add's or amend's do is a lasting habit in Tinker's own words, never the message copied" in prompt
    )
    assert "every detail and condition" in prompt
    assert "a request to be asked something is a question" in prompt
    assert "keeps the whole reply" not in prompt


@pytest.mark.spec("INT-3")
def test_the_interact_talk_prompt_keeps_say_a_short_reply_that_never_asks_or_claims_a_record():
    prompt = " ".join(Prompts.INTERACT_TALK_SYSTEM.split())

    assert "say is one or two short sentences replying to the message only" in prompt
    assert (
        "it never asks the current question or the next one: Tinker shows the question itself after the say"
        in (prompt)
    )
    assert "the next waiting question is only to lead into" in prompt
    assert "it never claims to have recorded, saved or noted anything" in prompt
    assert "add holds only what the person states, never an inference from a rejection" in prompt
    assert (
        "A follow-up question is about the person or Tinker, never a matter the person raised in passing, "
        "such as the weather" in prompt
    )
    assert '"say":"your reply to the person, one or two sentences, never a question"' in prompt
    assert '"text":"a short follow-up question, shown to the person in plain words, naming no id"' in prompt
    assert '"thought":"why you ask, shown to the person in plain words, naming no id"' in prompt
    assert '"expected":"your guess at the answer, shown to the person in plain words, naming no id"' in prompt


@pytest.mark.spec("INT-3")
def test_the_interact_talk_prompt_keeps_question_null_without_a_follow_up_and_settles_nothing_off_topic():
    prompt = " ".join(Prompts.INTERACT_TALK_SYSTEM.split())

    assert '"question":null or {"about":"person|tinker"' in prompt
    assert "question is null unless the message raises a new follow-up" in prompt
    assert (
        "Settled is true when the message answers the question or asks to move on from it (ask less, change "
        "the topic), and false when it does not answer it (off-topic) or the talk about it should go on."
        in prompt
    )
    assert (
        'An off-topic message, such as "By the way, what\'s the weather like?", gives empty confirm, reject, '
        "amend and add, and question null." in prompt
    )
    assert (
        "A request about this talk is not a trait: it gives empty confirm, reject, amend and add." in prompt
    )


@pytest.mark.spec("REF-3")
def test_the_reflect_prompt_holds_tinkers_purpose_lasting_traits_and_what_not_to_ask_again():
    prompt = " ".join(Prompts.REFLECT_SYSTEM.split())

    assert " ".join(Prompts.TINKER_PURPOSE.split()) in prompt
    assert "builds only what the request clearly asks for" in Prompts.TINKER_PURPOSE
    assert "A trait is lasting" in prompt and "never a passing state" in prompt
    assert "12 questions waiting is plenty" in prompt and "never a reason to stop" in prompt
    assert "a question like one waiting" in prompt and "a trait the person retired" in prompt
    assert "nor what an asked question or a known trait already covers, even in other words" in prompt
    assert "the questions the person already answered, with the answers" in prompt
    assert "Never ask an answered question again, in any words" in prompt
    assert "in two or three sentences" in prompt
    assert '"text":"a short question, shown to the person in plain words, naming no id"' in prompt
    assert '"thought":"why you ask, shown to the person in plain words, naming no id"' in prompt
    assert '"expected":"your guess at the answer, shown to the person in plain words, naming no id"' in prompt
    assert (
        '"question":"a short question to confirm it, shown to the person in plain words, naming no id"'
        in prompt
    )
    assert (
        'naming no id","thought":"why you ask, shown to the person in plain words, naming no id",'
        '"expected":"your guess at the answer, shown to the person in plain words, naming no id"}], "questions"'
        in prompt
    )


@pytest.mark.spec("DRM-12")
def test_the_merge_traits_prompt_says_the_traits_come_newest_first():
    prompt = " ".join(Prompts.DREAM_MERGE_TRAITS_SYSTEM.split())

    assert "The traits come newest first" in prompt
    assert "where they disagree, the newer wording wins" in prompt


@pytest.mark.spec("DRM-6")
def test_the_dream_traits_prompt_asks_for_lasting_traits_each_with_a_question_thought_and_guess_for_the_person():
    prompt = " ".join(Prompts.DREAM_TRAITS_SYSTEM.split())

    assert "suggestions" not in prompt
    assert "A trait is lasting (work, goals, likes, ways of working)" in prompt
    assert "never a passing state (the folder, the time, one command's output)" in prompt
    assert (
        '"question":"a short question the person answers to confirm it, shown to the person in plain words, '
        'naming no id"' in prompt
    )
    assert '"thought":"why you ask, shown to the person in plain words, naming no id"' in prompt
    assert '"expected":"your guess at the answer, shown to the person in plain words, naming no id"' in prompt
