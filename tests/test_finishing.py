"""FIN: finishing a cycle: tidy, the README and its examples, the final review and the lessons."""

import json

import pytest

from tinkerkit.finishing import FinalReview, LessonWriter, ReadmeWriter, Tidy
from tinkerkit.prompts import Reply
from tinkerkit.settings import Home
from tinkerkit.workspace import TaughtCommands
from conftest import TOOLCHAIN, criterion, file_block

FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("import calc\n\n\ndef test_add():\n    assert calc.add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert calc.sub(5, 3) == 2\n")
FILES = {"calc.py": {"kind": "code", "role": "the calculator", "contract": [], "uses": []},
         "test_calc.py": {"kind": "test", "role": "tests", "contract": [], "uses": ["calc.py"]}}


@pytest.fixture
def built(make_project, make_session, project_dir):
    """A built calculator: both criteria pass."""
    (project_dir / "calc.py").write_text(FIXED)
    (project_dir / "test_calc.py").write_text(TESTS)
    steps = [criterion("C1", "test_calc.py::test_add", "calc.add(2, 3) is 5", status="green"),
             criterion("C2", "test_calc.py::test_sub", "calc.sub(5, 3) is 2", status="green")]
    project = make_project(steps, files=json.loads(json.dumps(FILES)),
                           cycles=[{"id": "base", "request": "a calculator", "phase": "docs"}])
    return make_session(project), project


README = """# calc

## Usage

- Add:

```console
$ python3 calc.py add 2 3
5
```

- Many lines, one shortened:

```console
$ python3 calc.py table
1
...
$ python3 calc.py id
<id>
```

## Development

```sh
$ not an example
```
"""


@pytest.mark.spec("FIN-1")
def test_examples():
    assert ReadmeWriter.examples(README) == [("python3 calc.py add 2 3", ["5"]), ("python3 calc.py table", ["1", "..."]),
                                             ("python3 calc.py id", ["<id>"])]
    assert ReadmeWriter.examples("# no usage section\n```console\n$ x\n```") == []


@pytest.mark.spec("FIN-2")
def test_compare():
    assert ReadmeWriter.compare(['{"a": 1}'], '{ "a" : 1 }\n') == ([], {})
    assert ReadmeWriter.compare(["Added <title> (id <id>)"], "Added Dune (id 7)") == ([], {"<title>": "Dune", "<id>": "7"})
    assert ReadmeWriter.compare(["line one", "... more", "line two"], "line one\nline two") == ([], {})
    assert ReadmeWriter.missing(["Hello", "Bye"], "Hello") == ["Bye"]
    assert ReadmeWriter.missing(["code <code>"], "code") == ["code <code>"]


@pytest.mark.spec("FIN-3")
def test_an_expected_line_matches_only_a_whole_line():
    assert ReadmeWriter.missing(["80.33"], "80.33000000000004") == ["80.33"]
    assert ReadmeWriter.missing(["7"], "17") == ["7"]


@pytest.mark.spec("FIN-3")
def test_a_whole_line_matches():
    assert ReadmeWriter.missing(["80.33"], "result:\n80.33\n") == []


@pytest.mark.spec("FIN-4")
def test_read():
    writer = ReadmeWriter.__new__(ReadmeWriter)
    data = {"overview": " A calculator. ", "features": ["Adds", " ", "Subtracts  numbers"], "requires": "Python  3",
            "usage": [{"what": "Add", "command": "python3 calc.py add 2 3", "output": "5\n"}, {"what": "", "command": "x"},
                      "junk"] + [{"what": f"Use {number}", "command": f"python3 calc.py {number}", "output": ""}
                                 for number in range(15)],
            "serves": False}
    guide = writer.read(Reply(json.dumps(data)))
    assert guide["features"] == ["Adds", "Subtracts numbers"] and guide["requires"] == "Python 3"
    assert len(guide["usage"]) == ReadmeWriter.MAX_USAGE_EXAMPLES and guide["usage"][0]["output"] == "5"
    assert guide["serves"] is False and guide["overview"] == "A calculator."
    served = dict(data, usage=[{"what": "Get", "command": "curl -s http://127.0.0.1:8000/"}])
    assert writer.read(Reply(json.dumps(served)))["serves"] is True
    assert writer.read(Reply(json.dumps(dict(data, serves="yes"))))["serves"] is True
    with pytest.raises(ValueError, match="features and at least one usage"):
        writer.read(Reply(json.dumps(dict(data, usage=[]))))


@pytest.mark.spec("FIN-5")
def test_text(built, project_dir, tinker_home):
    session, project = built
    TaughtCommands.teach("act", ". .venv/bin/activate")
    project.spec["toolchain"] = dict(TOOLCHAIN, setup=["python3 -m venv .venv", "pip install -r requirements.txt"],
                                     activate="act", use=["make install"], run="python3 calc.py", format="black .",
                                     lint="ruff check .", test_all="pytest -q")
    (project_dir / ".env.example").write_text("API_KEY=\n")
    guide = {"overview": "Adds numbers.", "features": ["Adds two numbers"], "requires": "Python 3",
             "usage": [{"what": "Add", "command": "python3 calc.py add 2 3", "output": "5"}], "serves": False}
    text = ReadmeWriter(session).text(guide)
    assert text.startswith(f"# {project_dir.name}\n\nAdds numbers.\n\n## Features\n\n- Adds two numbers\n\n## Getting started")
    for part in ("It needs Python 3.", "Once, in the project folder:\n\n```sh\nmake install\n```",
                 "Run it with `python3 calc.py`, followed by what you want it to do.",
                 "## Usage\n\n- Add:\n\n```console\n$ python3 calc.py add 2 3\n5\n```",
                 "## Settings\n\nCopy `.env.example` to `.env`", "```\nAPI_KEY=\n```",
                 "```sh\npython3 -m venv .venv\n. .venv/bin/activate\npip install -r requirements.txt\n```",
                 "In a new terminal, first `. .venv/bin/activate`. Run the tests:\n\n```sh\npytest -q\n```",
                 "```sh\nblack .\nruff check .\n```"):
        assert part in text, part
    assert ReadmeWriter.examples(text) == [("python3 calc.py add 2 3", ["5"])]
    served = ReadmeWriter(session).text(dict(guide, serves=True))
    assert "it keeps running, so use it from another terminal" in served


@pytest.mark.spec("FIN-6")
def test_findings_said_about_and_reported(built):
    session, project = built
    output = ("calc.py:1:1: F401 unused import\ncalc.py:4:5: E225 spacing\n\n"
              "test_calc.py:2:1: E302 blank lines\n\nother/vendor.py:9:1: E501\n")
    assert Tidy.findings(output, "calc.py") == 2 and Tidy.findings(output, "test_calc.py") == 1
    assert Tidy.findings(output, "ghost.py") == 0
    assert Tidy.said_about(output, "test_calc.py") == "test_calc.py:2:1: E302 blank lines"
    assert Tidy.said_about("no file named", "calc.py") == "no file named"
    assert Tidy(session).reported(output) == ["calc.py", "test_calc.py"]
    assert Tidy(session).reported("") == []


@pytest.mark.spec("FIN-7")
def test_final_review_passes_a_built_project(built):
    session, project = built
    assert FinalReview(session).run(project.cycles[0]) is True and session.review_problem == ""


@pytest.mark.spec("FIN-7")
def test_final_review_finds_what_is_wrong(built, project_dir):
    session, project = built
    project.criteria[1]["status"] = "blocked"
    assert FinalReview(session).problem()[0] == "spec points without a passing test: C2"
    project.criteria[1]["status"] = "green"
    project.criteria[1]["test"] = "test_calc.py::test_mul"
    assert FinalReview(session).problem()[0] == "spec points without a passing test: C2"
    (project_dir / "calc.py").write_text(FIXED.replace("a - b", "a * b"))
    problem, output = FinalReview(session).problem()
    assert problem.startswith("`") and "fails when the whole suite runs" in problem and output


@pytest.mark.spec("FIN-7")
def test_final_review_needs_tests_to_run_and_setup_to_work(built, fake_ollama, monkeypatch):
    session, project = built
    project.spec["toolchain"]["test_all"] = "echo no tests ran"
    assert FinalReview(session).problem()[0] == "`echo no tests ran` runs no test"
    project.spec["toolchain"]["setup"] = ["exit 3"]
    monkeypatch.setattr("tinkerkit.workspace.online", lambda: True)
    fake_ollama.reply("CAUSE: no idea", when="Fix the toolchain")
    assert FinalReview(session).run(project.cycles[0]) is False
    assert session.review_problem.startswith("setup fails from the project's own files")


@pytest.mark.spec("FIN-8")
def test_lessons_without_problems_make_no_call(built, fake_ollama, tinker_home):
    session, project = built
    cycle = project.cycles[0]
    LessonWriter(session).run(cycle)
    LessonWriter(session).run(cycle)
    doc = Home.store().get("languages", "python")
    assert doc["projects"] == 1 and doc["toolchain"]["run"] == "" and cycle["counted"]
    assert fake_ollama.calls == []


@pytest.mark.spec("FIN-8")
def test_lessons_from_the_problems_solved(built, fake_ollama):
    session, project = built
    session.note("fix", "sub added instead of subtracting | solved: use -")
    texts = ["Subtract with the minus operator", "Name every test after its criterion",
             "Keep the parser apart from the arithmetic", "Print errors to stderr and exit with 1"]
    fake_ollama.reply(json.dumps({"lessons": [{"phase": "code", "scope": "any", "text": text} for text in texts]
                                  + ["junk"], "confirmed": [1, "x"]}), when="Lessons")
    LessonWriter(session).run(project.cycles[0])
    assert len(session.knowledge.known_lessons()) == 3 and project.spec["events"] == []
    assert LessonWriter.read(Reply('{"lessons": [], "confirmed": [2, "3"]}')) == ([], [2])


@pytest.mark.spec("FIN-9")
def test_readme_examples_are_run_and_a_failing_one_fixed(built, fake_ollama, project_dir):
    session, project = built
    readme = "# calc\n\n## Usage\n\n```console\n$ echo 41\n42\n```\n\n```console\n$ echo hi\nhi\n```\n"
    (project_dir / "README.md").write_text(readme)
    project.register(["README.md"], "docs", "readme")
    fake_ollama.reply("CAUSE: the README was wrong\n" + file_block("README.md", readme.replace("42", "41")),
                      when="Fix the README's examples")
    cycle = project.cycles[0]
    ReadmeWriter(session).check(cycle, "README.md")
    assert "41\n```" in (project_dir / "README.md").read_text() and "readme_failures" not in cycle
    assert "It printed:\n41" in fake_ollama.calls[0]["messages"][1]["content"]


@pytest.mark.spec("FIN-9")
def test_readme_examples_that_stay_wrong_are_kept(built, fake_ollama, project_dir):
    session, project = built
    project.spec["toolchain"]["use"] = ["echo ready > used.txt"]
    (project_dir / "README.md").write_text("## Usage\n\n```console\n$ cat used.txt && echo 1\n2\n```\n")
    project.register(["README.md"], "docs", "readme")
    fake_ollama.reply("CAUSE: ?\n" + file_block("calc.py", "def add(a, b):\n    return 0\n"), times=2)
    cycle = project.cycles[0]
    ReadmeWriter(session).check(cycle, "README.md")
    assert cycle["readme_failures"] == ["`cat used.txt && echo 1`: ready"]
    assert (project_dir / "calc.py").read_text() == FIXED  # a fix that broke a passing test was undone
    assert "broke a passing test" in fake_ollama.calls[1]["messages"][1]["content"]


@pytest.mark.spec("FIN-10")
def test_tidy_fixes_what_the_linter_reports(built, fake_ollama, project_dir):
    session, project = built
    (project_dir / "calc.py").write_text(FIXED + "# TODO tidy me\n")
    project.spec["toolchain"]["lint"] = "! grep -Hn TODO calc.py test_calc.py"
    fake_ollama.reply("CAUSE: a leftover note\n<<<LINES calc.py 7-7>>>\n# done\n<<<END>>>", when="Tidy calc.py")
    Tidy(session).run(project.cycles[0])
    assert (project_dir / "calc.py").read_text() == FIXED + "# done\n"
    numbered = fake_ollama.calls[0]["messages"][1]["content"]
    assert "   7| # TODO tidy me" in numbered and fake_ollama.calls[0]["think"] is False


@pytest.mark.spec("FIN-10")
def test_tidy_reverts_a_change_that_breaks_a_test_or_finds_more(built, fake_ollama, project_dir):
    session, project = built
    (project_dir / "calc.py").write_text(FIXED + "# TODO tidy me\n")
    project.spec["toolchain"]["lint"] = "! grep -Hn TODO calc.py"
    fake_ollama.reply("<<<LINES calc.py 6-7>>>\n    return a * b\n<<<END>>>", when="Tidy calc.py")
    Tidy(session).run(project.cycles[0])
    assert (project_dir / "calc.py").read_text() == FIXED + "# TODO tidy me\n"
    fake_ollama.reply("<<<LINES calc.py 1-1>>>\n# TODO one\n# TODO two\ndef add(a, b):\n<<<END>>>", when="Tidy calc.py")
    Tidy(session).run(project.cycles[0])
    assert (project_dir / "calc.py").read_text() == FIXED + "# TODO tidy me\n"


@pytest.mark.spec("FIN-8")
def test_the_lessons_step_proves_what_the_cycle_used(built, fake_ollama):
    session, project = built
    session.knowledge.add_lessons([{"text": "Seed random generators inside tests", "phase": "tests"}], [])
    lesson_id = session.knowledge.known_lessons()[0]["id"]
    cycle = project.cycles[0]
    cycle["used"] = {f"lesson:{lesson_id}": ["tests"]}
    LessonWriter(session).run(cycle)
    assert session.knowledge.known_lessons()[0]["status"] == "proven" and cycle["proven"]


@pytest.mark.spec("FIN-8")
def test_a_lesson_naming_the_project_is_dropped(built, fake_ollama):
    session, project = built
    session.note("fix", "sub added | solved: use -")
    fake_ollama.reply(json.dumps({"lessons": [{"phase": "code", "scope": "any", "text": "In calc.py, subtract with -"},
                                              {"phase": "code", "scope": "any", "text": "Name tests after criteria"}],
                                  "confirmed": []}), when="Lessons")
    LessonWriter(session).run(project.cycles[0])
    assert [lesson["text"] for lesson in session.knowledge.known_lessons()] == ["Name tests after criteria"]
