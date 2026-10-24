import json

import pytest

from tinkerkit.building import BugReproducer, BuildFix, CodeWriter, FixAttempt, TestTool, TestWriter, ToolchainRepair, Verifier
from tinkerkit.prompts import Reply
from tinkerkit.settings import TESTS_LOCKED_REASON
from tinkerkit.workspace import Output, TestRef
from conftest import criterion, file_block

CALC_WITH_WRONG_SUB = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("import calc\n\n\ndef test_add():\n    assert calc.add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert calc.sub(5, 3) == 2\n")
FILES = {"calc.py": {"kind": "code", "role": "the calculator", "contract": [], "uses": []},
         "test_calc.py": {"kind": "test", "role": "tests", "contract": [], "uses": ["calc.py"]}}


@pytest.fixture
def calc(make_project, make_session, project_dir):
    (project_dir / "calc.py").write_text(CALC_WITH_WRONG_SUB)
    (project_dir / "test_calc.py").write_text(TESTS)
    steps = [criterion("C1", "test_calc.py::test_add", "calc.add(2, 3) is 5"),
             criterion("C2", "test_calc.py::test_sub", "calc.sub(5, 3) is 2")]
    project = make_project(steps, files=json.loads(json.dumps(FILES)),
                           cycles=[{"id": "base", "request": "a calculator", "phase": "verify", "files": list(FILES)}])
    return make_session(project), project


def steps_of(project):
    return {criterion["id"]: criterion for criterion in project.criteria}


@pytest.mark.spec("BLD-1")
def test_tests_are_written_for_the_criteria(make_project, make_session, fake_ollama, project_dir):
    steps = [criterion("C1", "tests/test_calc.py::test_add", "calc.add(2, 3) is 5"),
             criterion("C2", "tests/test_calc.py::test_sub", "calc.sub(5, 3) is 3")]
    project = make_project(steps, files={"calc.py": FILES["calc.py"]})
    body = "import calc\n\ndef test_add():\n    assert calc.add(2, 3) == 5\n\ndef test_sub():\n    assert calc.sub(5, 3) == 2\n"
    fake_ollama.reply("CRITERION test_sub: calc.sub(5, 3) is 2\n" + file_block("tests/test_calc.py", body), when="Tests")
    TestWriter(make_session(project)).run(project.cycles[0])
    assert (project_dir / "tests" / "test_calc.py").read_text() == body
    assert steps_of(project)["C2"]["behaviour"] == "calc.sub(5, 3) is 2"
    assert fake_ollama.labels()[0].startswith("Tests tests/test_calc.py (2)")


@pytest.mark.spec("BLD-1")
def test_a_test_file_with_tests_is_never_rewritten(make_project, make_session, fake_ollama, project_dir):
    (project_dir / "tests").mkdir()
    (project_dir / "tests" / "test_calc.py").write_text("def test_old():\n    pass\n")
    steps = [criterion("C0", "tests/test_calc.py::test_old", "old", status="green"),
             criterion("C1", "tests/test_calc.py::test_add", "calc.add(2, 3) is 5")]
    project = make_project(steps, files={"tests/test_calc.py": {"kind": "test", "role": "tests", "uses": ["calc.py"]}})
    loses_test_old = file_block("tests/test_calc.py", "def test_add():\n    pass\n")
    fake_ollama.reply(loses_test_old, when="Tests")
    fake_ollama.reply(file_block("tests/test_calc_2.py", "def test_add():\n    pass\n"), when="Tests")
    TestWriter(make_session(project)).run(project.cycles[0])
    assert "test_old" in (project_dir / "tests" / "test_calc.py").read_text()
    assert steps_of(project)["C1"]["test"] == "tests/test_calc_2.py::test_add"
    assert project.files["tests/test_calc_2.py"]["kind"] == "test"
    assert "lost its tests test_old" in fake_ollama.calls[1]["messages"][1]["content"]


@pytest.mark.spec("BLD-1")
def test_a_test_that_cannot_be_written_counts_against_its_criterion(make_project, make_session, fake_ollama):
    project = make_project([criterion("C1", "tests/test_calc.py::test_add", "adds")])
    fake_ollama.reply(file_block("elsewhere/test_x.py", "def test_add(): pass"), when="Tests", times=2)
    TestWriter(make_session(project)).run(project.cycles[0])
    step = steps_of(project)["C1"]
    assert step["write_failures"] == 2 and "only test files next to tests/test_calc.py" in step["last_error"]


@pytest.mark.spec("BLD-1")
def test_new_file_next_to_and_may_write(make_project, make_session, project_dir):
    writer = TestWriter(make_session(make_project()))
    (project_dir / "tests").mkdir()
    (project_dir / "tests" / "test_a_2.py").write_text("x")
    assert writer.new_file_next_to("tests/test_a.py") == "tests/test_a_3.py"
    check = TestWriter.may_write("tests/test_a.py")
    assert check("tests/test_b.py", False) is None and "only test files next to" in check("test_b.py", False)


@pytest.mark.spec("BLD-2")
def test_config_files_come_first_in_one_call(make_project, make_session, fake_ollama, project_dir):
    files = {"pyproject.toml": {"kind": "config", "role": "pytest settings", "uses": []},
             "requirements.txt": {"kind": "config", "role": "dependencies", "uses": []}}
    project = make_project(files=files)
    cycle = dict(project.cycles[0], files=list(files))
    fake_ollama.reply(file_block("pyproject.toml", "[tool.pytest.ini_options]") + file_block("requirements.txt", "flask")
                      + file_block("calc.py", "sneaky"), when="Write 2 config files")
    CodeWriter(make_session(project)).write_configs_first(cycle)
    assert (project_dir / "requirements.txt").read_text() == "flask\n" and not (project_dir / "calc.py").exists()
    assert cycle["code_done"] == ["pyproject.toml", "requirements.txt"] and fake_ollama.calls[0]["think"] is False


@pytest.mark.spec("BLD-2")
def test_code_files_one_call_each_with_markers_written_empty(make_project, make_session, fake_ollama, project_dir):
    files = {"pkg/__init__.py": {"kind": "code", "role": "package", "uses": [], "marker": True},
             "pkg/calc.py": {"kind": "code", "role": "the calculator", "uses": []},
             "tests/test_calc.py": {"kind": "test", "role": "tests", "uses": ["pkg/calc.py"]},
             "README.md": {"kind": "docs", "role": "readme", "uses": []}}
    project = make_project(files=files)
    cycle = dict(project.cycles[0], files=list(files))
    named_without_its_folder = file_block("calc.py", FIXED)
    fake_ollama.reply(named_without_its_folder, when="Write pkg/calc.py")
    CodeWriter(make_session(project)).write_all(cycle)
    assert (project_dir / "pkg" / "__init__.py").read_text() == ""
    assert (project_dir / "pkg" / "calc.py").read_text() == FIXED and not (project_dir / "calc.py").exists()
    assert len(fake_ollama.calls) == 1 and cycle["code_done"] == ["pkg/__init__.py", "pkg/calc.py"]


@pytest.mark.spec("BLD-2")
def test_a_file_far_too_long_is_asked_for_again(make_project, make_session, fake_ollama, project_dir):
    project = make_project(files={"calc.py": FILES["calc.py"]})
    fake_ollama.reply(file_block("calc.py", "x = 1\n" * 130), when="Write calc.py")
    fake_ollama.reply(file_block("calc.py", FIXED), when="Write calc.py")
    CodeWriter(make_session(project)).write_file(project.cycles[0], "calc.py")
    assert (project_dir / "calc.py").read_text() == FIXED
    assert "had 130 lines; keep it under 60" in fake_ollama.calls[1]["messages"][1]["content"]


@pytest.mark.spec("BLD-2")
def test_wiring(make_project, make_session, fake_ollama, project_dir):
    files = {"app.py": {"kind": "code", "role": "app", "contract": ["main()"], "uses": ["store.py", "README.md"]},
             "store.py": {"kind": "code", "role": "store", "contract": ["save()"], "uses": []},
             "README.md": {"kind": "docs", "role": "docs", "uses": []}}
    project = make_project(files=files)
    (project_dir / "app.py").write_text("def main():\n    pass\n")
    session = make_session(project)
    writer = CodeWriter(session)
    assert writer.wiring_problems("app.py") == ["it must use store.py (refer to it by name), as its contract says"]
    fake_ollama.reply(file_block("app.py", "import store\n\ndef main():\n    store.save()\n"), when="Wire app.py")
    writer.check_wiring(dict(project.cycles[0], files=["app.py", "store.py"]))
    assert writer.wiring_problems("app.py") == []
    assert CodeWriter.addressed_to(Reply(file_block("x.py", "1")), "app.py", False).files() == [("app.py", "1")]
    assert writer.may_write("app.py")("new_module.py", False) is None
    assert "only app.py" in writer.may_write("app.py")("store.py", True)


@pytest.mark.spec("BLD-3")
def test_verify_fixes_a_failing_criterion(calc, fake_ollama, project_dir):
    session, project = calc
    fake_ollama.reply("CAUSE: sub added\n" + file_block("calc.py", FIXED), when="Fix the code C2")
    assert Verifier(session).run(project.cycles[0]) is True
    assert {criterion["status"] for criterion in project.criteria} == {"green"}
    assert (project_dir / "calc.py").read_text() == FIXED
    assert steps_of(project)["C2"]["history"] == ["fix: sub added -> passes"]
    assert project.spec["events"] == [{"kind": "fix", "text": "calc.sub(5, 3) is 2 | solved: sub added"}]


@pytest.mark.spec("BLD-3")
def test_verify_parks_what_nothing_fixes_and_puts_the_files_back(calc, fake_ollama, project_dir):
    session, project = calc
    fake_ollama.reply("I am not sure what to change.", times=5)
    assert Verifier(session).run(project.cycles[0]) is False
    step = steps_of(project)["C2"]
    assert step["status"] == "blocked" and steps_of(project)["C1"]["status"] == "green"
    assert (project_dir / "calc.py").read_text() == CALC_WITH_WRONG_SUB
    labels = [label.split(" [")[0] for label in fake_ollama.labels()]
    assert labels[0] == "Fix the code C2 (no thinking first)" and labels[-1].startswith("Review the test and the code against the plan C2")
    assert len(labels) == 5 and project.store.get("restore", Verifier.RESTORE_POINT) is None


@pytest.mark.spec("BLD-3")
def test_a_criterion_failing_like_a_parked_one_is_parked_at_once(calc, fake_ollama):
    session, project = calc
    verifier = Verifier(session)
    step = steps_of(project)["C2"]
    _, output = verifier.evaluate(step)
    verifier.parked_by_failure[Verifier.failure_key(step, output)] = "C9"
    assert verifier.work(step, retry=False) is False
    assert step["last_error"] == "same failure as C9" and fake_ollama.calls == []


@pytest.mark.spec("BLD-4")
def test_a_stopped_run_is_put_back_from_its_restore_point(calc, project_dir, capsys):
    session, project = calc
    project.store.put("restore", Verifier.RESTORE_POINT, {"criterion": "C2", "files": {"calc.py": CALC_WITH_WRONG_SUB, "test_calc.py": TESTS,
                                                                                 "new.py": None}})
    (project_dir / "calc.py").write_text("half-done change\n")
    Verifier(session).recover_interrupted()
    assert (project_dir / "calc.py").read_text() == CALC_WITH_WRONG_SUB and project.store.get("restore", Verifier.RESTORE_POINT) is None
    assert "The last run stopped while C2 was being worked on: calc.py restored" in capsys.readouterr().out


@pytest.mark.spec("BLD-4")
def test_failure_key_and_roll_back(calc, project_dir):
    session, project = calc
    first = {"test": "t.py::test_one"}
    second = {"test": "t.py::test_two"}
    assert Verifier.failure_key(first, "t.py::test_one failed at line 3: KeyError") == \
        Verifier.failure_key(second, "t.py::test_two failed at line 9: KeyError")
    snapshot = session.workspace.snapshot(project.files)
    (project_dir / "extra.py").write_text("x")
    project.register(["extra.py"], "code", "added by a fix")
    (project_dir / "calc.py").write_text("changed")
    Verifier(session).roll_back(snapshot)
    assert not (project_dir / "extra.py").exists() and "extra.py" not in project.files
    assert (project_dir / "calc.py").read_text() == CALC_WITH_WRONG_SUB


@pytest.mark.spec("BLD-4")
def test_ladder_and_to_review(calc):
    session, project = calc
    verifier = Verifier(session)
    step = {"test": "tests/test_a.py::test_x", "history": []}
    assert verifier.ladder(step, "", retry=False) == (("fix", 3), ("review", 2))
    assert verifier.ladder(step, "", retry=True) == (("fix", 1), ("review", 1))
    broken = 'File "tests/test_a.py", line 2\nSyntaxError: invalid syntax'
    assert verifier.ladder(step, broken, retry=False) == (("review", 2),)
    step["history"] = ["fix: x -> broke a; reverted", "fix: y -> broke b; reverted"]
    assert verifier.ladder(step, "", retry=False) == (("review", 2),)
    attempt = type("A", (), {"only_wanted_test_change": False, "tests_may_disagree": True})()
    assert "fixes keep breaking" in Verifier.to_review(attempt, "fix") and Verifier.to_review(attempt, "review") == ""


def attempt(session, project, approach="fix", output="it fails", before=()):
    return FixAttempt(session, Verifier(session), steps_of(project)["C2"], approach, output, set(before))


@pytest.mark.spec("BLD-5")
def test_what_a_fix_and_a_review_may_change(calc):
    session, project = calc
    view = type("V", (), {"shown": {"calc.py", "test_calc.py"}})()
    fix = attempt(session, project).may_change(view)
    assert fix("calc.py", True) is None and fix("test_calc.py", True) == TESTS_LOCKED_REASON
    assert fix("other.py", True) == "only the files shown may change" and "no new files" in fix("new.py", False)
    review = attempt(session, project, "review").may_change(view)
    assert review("test_calc.py", True) is None and review("test_helper.py", False) is None
    assert review("sub/new.py", False) == "no new files now; change the files shown"


@pytest.mark.spec("BLD-5")
def test_a_fix_that_blames_the_test_changes_nothing(calc, fake_ollama, project_dir):
    session, project = calc
    fake_ollama.reply("TEST WRONG: 5 - 3 is 2, the test is fine actually\n" + file_block("calc.py", FIXED))
    fix = attempt(session, project)
    assert fix.run() == ("fails", "it fails") and fix.only_wanted_test_change
    assert (project_dir / "calc.py").read_text() == CALC_WITH_WRONG_SUB


@pytest.mark.spec("BLD-5")
def test_a_change_that_breaks_a_passing_test_is_reverted(calc, fake_ollama, project_dir):
    session, project = calc
    broken = "def add(a, b):\n    return 0\n\n\ndef sub(a, b):\n    return a - b\n"
    fake_ollama.reply("CAUSE: rewrote it\n" + file_block("calc.py", broken))
    fix = attempt(session, project, before={"C1"})
    state, output = fix.run()
    assert state == "fails" and "Your last change broke test_calc.py::test_add" in output
    assert (project_dir / "calc.py").read_text() == CALC_WITH_WRONG_SUB
    assert steps_of(project)["C2"]["keep"] == ["test_calc.py::test_add"] and not fix.tests_may_disagree


@pytest.mark.spec("BLD-6")
def test_ran_empty():
    assert TestTool.ran_empty(0, "")
    assert TestTool.ran_empty(5, "no tests ran in 0.01s")
    assert TestTool.ran_empty(1, "collected nothing")
    assert not TestTool.ran_empty(1, "ModuleNotFoundError: No module named pytest")
    assert not TestTool.ran_empty(127, "") and not TestTool.ran_empty(None, "")


@pytest.mark.spec("BLD-6")
def test_the_tools_work(calc):
    session, project = calc
    assert TestTool(session).works() is True and session.tool_problem == ""


@pytest.mark.spec("BLD-6")
def test_a_missing_linter_is_a_tooling_problem_the_model_may_fix(calc, fake_ollama, tinker_home):
    session, project = calc
    project.spec["toolchain"]["lint"] = "no-such-linter-xyz ."
    fake_ollama.reply('CAUSE: that linter is not installed\nTOOLCHAIN: {"lint": "none"}', when="Fix the toolchain")
    assert TestTool(session).works() is True
    assert project.toolchain.lint == "" and project.toolchain.lint_declared
    assert session.knowledge.language_doc["fixes"][0]["cause"] == "that linter is not installed"


@pytest.mark.spec("BLD-6")
def test_tools_that_stay_broken_stop_the_cycle(calc, fake_ollama):
    session, project = calc
    project.spec["toolchain"]["lint"] = "no-such-linter-xyz ."
    fake_ollama.reply("CAUSE: no idea", when="Fix the toolchain", times=2)
    assert TestTool(session).works() is False
    assert session.tool_problem.startswith("the project's tools do not work:")


@pytest.mark.spec("BLD-7")
def test_a_fix_learned_before_is_replayed_without_the_model(calc, fake_ollama, project_dir):
    session, project = calc
    output = "$ ruff check .\nruff: command not found"
    session.knowledge.remember_fix({"failure": Output.tool_failure(output, project_dir), "cause": "no ruff",
                                    "replay": {"toolchain": {"lint": "none"}, "files": {"setup.cfg": "[flake8]\n",
                                                                                        "calc.py": "stolen"}}})
    assert ToolchainRepair(session).run(output, set())[0] == "known"
    assert project.toolchain.lint == "" and (project_dir / "setup.cfg").read_text() == "[flake8]\n"
    never_replaced = CALC_WITH_WRONG_SUB
    assert (project_dir / "calc.py").read_text() == never_replaced
    assert project.files["setup.cfg"]["kind"] == "config" and fake_ollama.calls == []


@pytest.mark.spec("BLD-7")
def test_a_model_fix_changes_only_config_files_and_commands(calc, fake_ollama, project_dir):
    session, project = calc
    fake_ollama.reply("CAUSE: pytest needs a path\n" + file_block("pytest.ini", "[pytest]\npythonpath = .")
                      + file_block("calc.py", "hacked") + '\nTOOLCHAIN: {"test_all": "pytest -x"}')
    kind, fix = ToolchainRepair(session).run("ERROR: no module named calc", set())
    assert kind == "model" and (project_dir / "pytest.ini").exists() and (project_dir / "calc.py").read_text() == CALC_WITH_WRONG_SUB
    assert project.toolchain.test_all == "pytest -x"
    assert fix["replay"] == {"toolchain": {"test_all": "pytest -x"}, "files": {"pytest.ini": "[pytest]\npythonpath = .\n"}}


@pytest.mark.spec("BLD-8")
def test_a_project_that_does_not_build_is_fixed(calc, fake_ollama, project_dir):
    session, project = calc
    (project_dir / "calc.py").write_text("def add(a, b)\n    return a + b\n")
    fake_ollama.reply("CAUSE: a colon was missing\n" + file_block("calc.py", FIXED), when="Make it build")
    assert Verifier(session).make_it_build() is True
    assert (project_dir / "calc.py").read_text() == FIXED and fake_ollama.calls[0]["think"] is False


@pytest.mark.spec("BLD-8")
def test_what_a_build_fix_may_change(calc):
    session, project = calc
    check = BuildFix(session, "output").may_change(type("V", (), {"shown": {"calc.py", "test_calc.py"}})())
    assert check("calc.py", True) is None and check("test_calc.py", True) == TESTS_LOCKED_REASON
    assert check("new.py", False) == "only the files shown may change now"
    assert check("hidden.py", True) == "only the files shown may change now"


@pytest.fixture
def bug(calc, project_dir):
    session, project = calc
    (project_dir / "calc.py").write_text(FIXED)
    for step in project.criteria:
        step["status"] = "green"
    project.cycles[0]["phase"] = "done"
    cycle = project.new_cycle("f1", "add(-1, -1) gives 0, it should be -2", kind="fix")
    return session, project, cycle


@pytest.mark.spec("BLD-9")
def test_a_bug_is_reproduced_with_a_new_failing_test(bug, fake_ollama, project_dir):
    session, project, cycle = bug
    (project_dir / "calc.py").write_text(FIXED.replace("return a + b", "return abs(a) + abs(b) if a < 0 else a + b"))
    test = "import calc\n\n\ndef test_negative():\n    assert calc.add(-1, -1) == -2\n"
    fake_ollama.reply("TEST: test_bug.py::test_negative\nEXPECTED: add(-1, -1) is -2\n" + file_block("test_bug.py", test))
    assert BugReproducer(session).run(cycle) is True
    step = project.cycle_criteria(cycle)[0]
    assert (step["id"], step["test"], step["behaviour"]) == ("B3", "test_bug.py::test_negative", "add(-1, -1) is -2")
    assert project.files["test_bug.py"]["kind"] == "test"


@pytest.mark.spec("BLD-9")
def test_a_bug_no_test_shows_is_dropped(bug, fake_ollama, project_dir):
    session, project, cycle = bug
    passing = "import calc\n\n\ndef test_negative():\n    assert calc.add(-1, -1) == -2\n"
    fake_ollama.reply("TEST: test_bug.py::test_negative\n" + file_block("test_bug.py", passing), times=2)
    assert BugReproducer(session).run(cycle) is False
    assert project.cycle_criteria(cycle) == [] and not (project_dir / "test_bug.py").exists()
    assert (cycle["phase"], cycle["outcome"]) == ("done", "not reproduced") and "could not be reproduced" in session.unreproduced_reason
    assert "passed on the code as it is" in fake_ollama.calls[1]["messages"][1]["content"]


@pytest.mark.spec("BLD-9")
def test_what_a_bug_test_must_be(bug):
    session, project, cycle = bug
    reproducer = BugReproducer(session)
    assert "names no file and test" in reproducer.unusable(cycle, TestRef("test_bug.py"))
    assert "outside the project" in reproducer.unusable(cycle, TestRef("../t.py::test_x"))
    assert "a file the project has" in reproducer.unusable(cycle, TestRef("test_calc.py::test_x"))
    assert reproducer.unusable(cycle, TestRef("test_new.py::test_x")) == ""
    assert "breaks the build" in reproducer.why_not({"test": "t::x"}, "build", "SyntaxError")
    assert "ran no test" in reproducer.why_not({"test": "t::x"}, "fails", "no tests ran")
    assert reproducer.why_not(None, "missing", "why") == "why"


@pytest.mark.spec("BLD-9")
def test_the_bug_test_follows_the_amendments(bug, fake_ollama):
    session, project, cycle = bug
    cycle["amendments"] = ["it happens only with two negative numbers"]
    fake_ollama.reply("TEST: test_bug.py::test_x\n" + file_block("test_bug.py", "def test_x():\n    assert True\n"),
                      times=2)
    BugReproducer(session).run(cycle)
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "Changed by the person: it happens only with two negative numbers" in prompt
