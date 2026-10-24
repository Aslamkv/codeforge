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


@pytest.mark.spec("PRM-2")
def test_json_in_prose():
    assert Reply('Sure! Here it is:\n```json\n{"a": [1, 2]}\n```\nDone.').json() == {"a": [1, 2]}


@pytest.mark.spec("PRM-2")
@pytest.mark.parametrize("text,expected", [
    pytest.param('{"a": [1, 2,], "b": 3,}', {"a": [1, 2], "b": 3}, id="trailing commas"),
    pytest.param('{"a": "one " + "two"}', {"a": "one two"}, id="joined strings"),
    pytest.param('{"a": "it\\\'s"}', {"a": "it's"}, id="escaped single quote"),
    pytest.param('{"a": {"b": [1, 2]}', {"a": {"b": [1, 2]}}, id="left open at the end"),
    pytest.param('{"a": ["x", "y", "b": 1}', {"a": ["x", "y"], "b": 1}, id="list open before the next key"),
])
def test_json_mends_the_usual_slips(text, expected):
    assert Reply(text).json() == expected


@pytest.mark.spec("PRM-2")
def test_json_without_an_object_or_beyond_mending():
    with pytest.raises(ValueError, match="no JSON object"):
        Reply("no json here").json()
    with pytest.raises(json.JSONDecodeError):
        Reply('{"a": tru}').json()


@pytest.mark.spec("PRM-2")
def test_a_string_that_is_an_objects_key():
    assert Reply.OBJECT_KEY_RE.prefixmatch('"name": 1') and not Reply.OBJECT_KEY_RE.prefixmatch('"name", 1')


@pytest.mark.spec("PRM-2")
def test_strings_joined_as_in_code_become_one():
    assert Reply.STRING_CONCATENATION_RE.sub("", '"a" + "b"') == '"ab"'


@pytest.mark.spec("PRM-2")
def test_an_escaped_single_quote_after_an_even_run_of_backslashes():
    assert Reply.ESCAPED_SINGLE_QUOTE_RE.sub(r"\1'", r"it\'s") == "it's"
    assert Reply.ESCAPED_SINGLE_QUOTE_RE.sub(r"\1'", r"it\\'s") == r"it\\'s"


@pytest.mark.spec("PRM-3")
@pytest.mark.parametrize("header", ["<<<FILE app.py>>>", "<<<FILE path=app.py>>>", "<<<FILE: app.py>>>",
                                    '<<<FILE "app.py">>>', "<<<FILE app.py", "<<<FILE file: app.py>>>"])
def test_files_read_every_header_form(header):
    assert Reply(f"{header}\nprint(1)\n<<<END>>>").files() == [("app.py", "print(1)")]


@pytest.mark.spec("PRM-3")
def test_files_several_blocks_with_normalized_paths():
    reply = Reply("<<<FILE ./src/a.py>>>\na = 1\n<<<END>>>\ntext\n<<<FILE tests/test_a.py>>>\nimport a\n<<<END>>>")
    assert reply.files() == [("src/a.py", "a = 1"), ("tests/test_a.py", "import a")]


@pytest.mark.spec("PRM-3")
def test_files_placeholder_path_with_the_real_one_after_it():
    assert Reply("<<<FILE path>>>real/app.py\nx = 1\n<<<END>>>").files() == [("real/app.py", "x = 1")]
    assert Reply("<<<FILE path>>>\nreal/app.py\nx = 1\n<<<END>>>").files() == [("real/app.py", "x = 1")]


@pytest.mark.spec("PRM-3")
def test_files_skip_placeholder_bodies_and_keep_a_file_begun_on_the_header():
    assert Reply("<<<FILE app.py>>>\nthe complete file\n<<<END>>>").files() == []
    assert Reply("<<<FILE app.py>>>x=1\ny = 2\n<<<END>>>").files() == [("app.py", "x=1\ny = 2")]


@pytest.mark.spec("PRM-3")
def test_files_drop_debris_and_a_fence_around_the_body():
    reply = Reply("<<<FILE app.py>>>\n```python\nx = 1\n```\n>>>\n<<<END>>>")
    assert reply.files() == [("app.py", "x = 1")]


@pytest.mark.spec("PRM-3")
def test_a_markdown_file_keeps_its_fences():
    body = "```sh\nmake\n```"
    assert Reply(f"<<<FILE README.md>>>\n{body}\n<<<END>>>").files() == [("README.md", body)]


@pytest.mark.spec("PRM-3")
@pytest.mark.parametrize("header", ["<<<FILE x>>>", "<<<FILE path=x>>>", '<<<FILE "x">>>', "<<<FILE: x>>>", "<<<FILE x"])
def test_a_file_header_gives_its_path_in_every_form(header):
    assert Reply(f"{header}\nbody\n<<<END>>>").files() == [("x", "body")]


@pytest.mark.spec("PRM-3")
def test_a_relative_path_alone_on_a_line():
    assert all(Reply.RELATIVE_PATH_LINE_RE.fullmatch(path) for path in ("src/app.py", "app.py", "src/app"))
    assert not any(Reply.RELATIVE_PATH_LINE_RE.fullmatch(text) for text in ("the file", "app"))


@pytest.mark.spec("PRM-3")
def test_a_line_of_the_block_formats_own_marks():
    assert all(Reply.BLOCK_MARKS_ONLY_RE.fullmatch(line) for line in (">>>", "<<<END>>>", " << "))
    assert not Reply.BLOCK_MARKS_ONLY_RE.fullmatch("x >>> y")


@pytest.mark.spec("PRM-3")
def test_a_markdown_fence_line():
    assert all(Reply.MARKDOWN_FENCE_RE.fullmatch(line) for line in ("```", "```python", "~~~"))
    assert not Reply.MARKDOWN_FENCE_RE.fullmatch("```python x = 1")


@pytest.mark.spec("PRM-4")
def test_line_edits():
    reply = Reply("<<<LINES app.py 3-4>>>\n  3| a = 1\n  4| b = 2\n<<<END>>>\n<<<LINES lib.py lines=7-7>>>\nx\n<<<END>>>")
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
    assert not Reply.files_ended("<<<FILE a.py>>>\nx = 1") and not Reply.files_ended(answer + "\n<<<LINES b.py 1-2>>>")


@pytest.mark.spec("PRM-6")
def test_wrote_once_every_wanted_file_has_its_block():
    done = Reply.wrote(["a.py", "./b.py"])
    assert not done("<<<FILE a.py>>>\n1\n<<<END>>>")
    assert done("<<<FILE a.py>>>\n1\n<<<END>>>\n<<<FILE b.py>>>\n2\n<<<END>>>")


@pytest.mark.spec("PRM-7")
def test_json_complete():
    assert Reply.json_complete('ok {"a": [1,],}')
    assert not Reply.json_complete('{"a": 1')
    assert not Reply.json_complete('{"a": 1} trailing')
    assert not Reply.json_complete("no braces")
    assert not Reply.json_complete('{"a": }')


@pytest.mark.spec("PRM-8")
def test_marker_lines():
    reply = Reply("CAUSE: the sum was wrong\nTEST WRONG: expects 6 for 2+3\nCRITERION: add(2, 3) is 5\n"
                  "CRITERION test_add: add(2, 3) is 5\nTOOLCHAIN: {\"lint\": \"ruff check .\"}\n"
                  "<<<FILE a.py>>>\n1\n<<<END>>>\n<<<LINES a.py 1-1>>>\n2\n<<<END>>>\n<<<LINES b.py 1-1>>>\n3\n<<<END>>>")
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
def test_strip_trailing_commas_outside_strings_only():
    assert Reply.strip_trailing_commas('{"a": [1, 2, ], "s": "x, ]",}') == '{"a": [1, 2 ], "s": "x, ]"}'


def parts_text(parts, limit):
    return Part.assemble(parts, limit)


@pytest.mark.spec("PRM-11")
def test_assemble_orders_by_place_and_leaves_out_empty_optional_parts():
    parts = [Part("Middle", "m", True), Part("Last", "l", True, place="last"), Part("Empty", ""),
             Part("First", "f", place="first"), Part("Required empty", "", True)]
    text = Part.assemble(parts, 1000)
    assert [line for line in text.splitlines() if line.startswith("##")] == \
        ["## First", "## Middle", "## Required empty", "## Last"]


@pytest.mark.spec("PRM-11")
def test_assemble_fits_optional_parts_while_they_fit():
    parts = [Part("R", "r" * 100, True), Part("Atomic", "a" * 500, atomic=True), Part("Prose", "p" * 1000)]
    text = Part.assemble(parts, 600)
    assert len(text) <= 600
    assert "## Atomic" not in text
    assert "## Prose\n" + "p" * 100 in text and "[...]" in text


@pytest.mark.spec("PRM-11")
def test_assemble_leaves_out_a_part_too_small_to_shorten():
    text = Part.assemble([Part("R", "r" * 100, True), Part("Prose", "p" * 1000)], 200)
    assert "## Prose" not in text


@pytest.mark.spec("PRM-12")
def test_required_prose_is_shortened_head_or_tail():
    notes = []
    head, tail = Part("Head", "H" + "x" * 2000, True), Part("Tail", "y" * 2000 + "T", True, keep="tail")
    text = Part.assemble([head, tail], 2000, notes)
    assert len(text) <= 2000 and notes == ["Head", "Tail"]
    assert "## Head\nH" in text and "T\n\n## Tail" not in text and text.endswith("T")
    assert text.count("[...]") == 2


@pytest.mark.spec("PRM-12")
def test_exact_parts_are_never_shortened():
    exact = Part("File app.py", "c" * 3000, True, exact=True)
    with pytest.raises(PromptTooLarge, match="File app.py"):
        Part.assemble([exact, Part("Prose", "p" * 3000, True)], 2000)
    text = Part.assemble([exact, Part("Prose", "p" * 3000, True)], 3700)
    assert "c" * 3000 in text


@pytest.mark.spec("PRM-12")
def test_prose_is_never_cut_below_the_floor():
    with pytest.raises(PromptTooLarge):
        Part.assemble([Part("Prose", "p" * 3000, True)], 100)
    shortened = Part("P", "p" * 3000, True).shortened(MIN_TRIMMED_PROSE_CHARS)
    assert len(shortened.text) <= MIN_TRIMMED_PROSE_CHARS and shortened.text.endswith("[...]")


@pytest.mark.spec("PRM-13")
def test_part_flags_render_and_size():
    assert not Part("t", "x", exact=True).exact and Part("t", "x", True, exact=True).exact
    assert Part("t", "x", atomic=True).atomic and not Part("t", "x", True, atomic=True).atomic
    relaxed = Part("t", "x", True, exact=True, place="first").relaxed()
    assert not relaxed.exact and relaxed.required and relaxed.place == "first"
    part = Part("Title", " text ")
    assert part.render() == "## Title\ntext" and part.size() == len("## Title\ntext") + 2
    assert Part("", "bare").render() == "bare"
    assert Part.total_size([Part("a", "x"), Part("b", ""), Part("c", "", True)]) == \
        Part("a", "x").size() + Part("c", "", True).size()


def example(text):
    return json.loads(text)


@pytest.mark.spec("PRM-14")
def test_the_prompts_example_json_parses_and_its_toolchain_works():
    spec = example(Prompts.SPEC_JSON + ",\n " + Prompts.TOOLCHAIN_JSON + "}")
    assert spec["criteria"] and spec["files"] and spec["entry"] == "tip.py"
    assert not Toolchain(spec["toolchain"]).problems()
    assert example(Prompts.SPEC_JSON + "}")["language"] == "Python"
    assert example("{" + Prompts.TOOLCHAIN_JSON + "}")["toolchain"]["test_all"] == "pytest -q"
    readme = re.search(r"Reply with ONLY JSON: (\{.*\})", Prompts.README_SYSTEM, re.S).group(1)
    assert example(readme)["usage"][0]["command"] == "python3 calculator.py add 5 2"
    assert Prompts.SPEC_JSON[:-1] in Prompts.SPEC_SYSTEM and Prompts.TOOLCHAIN_JSON in Prompts.TOOLCHAIN_SYSTEM


@pytest.mark.spec("PRM-14")
def test_the_templates_format():
    assert "60 words" in Prompts.BRIEF_SYSTEM.format(words=60)
    systems = [value for key, value in vars(Prompts).items() if key.endswith("_SYSTEM")]
    assert len(systems) >= 25 and all(isinstance(system, str) and system.strip() for system in systems)


@pytest.mark.spec("PRM-15")
def test_the_example_toolchain_commands_are_real_commands():
    toolchain = example("{" + Prompts.TOOLCHAIN_JSON + "}")["toolchain"]
    commands = toolchain["setup"] + [value for key, value in toolchain.items() if isinstance(value, str)]
    assert not [command for command in commands if re.search(r"\btinker\b", command)]


@pytest.mark.spec("PRM-16")
def test_the_lessons_prompt_asks_for_general_lessons():
    assert "never name this project, its files, its names, its features or its data" in \
        " ".join(Prompts.LESSONS_SYSTEM.split())
