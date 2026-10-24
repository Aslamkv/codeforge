"""PLN: planning a cycle: recall, the spec, the contracts where files meet, and their review."""

import json

import pytest

from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.planning import ContractWriter, Designer, InterfaceReviewer, Recaller, SpecWriter, Working, asked
from tinkerkit.prompts import Reply
from conftest import TOOLCHAIN, criterion
from tinkerkit.workspace import Toolchain


@pytest.mark.spec("PLN-1")
def test_asked():
    assert asked({"request": "a calculator"}) == "a calculator"
    assert asked({"request": "a calculator", "amendments": ["also divide", "in French"]}) == \
        "a calculator\nChanged by the person: also divide\nChanged by the person: in French"


@pytest.mark.spec("PLN-2")
@pytest.mark.parametrize("text", ["(300 − 273.15) × 9/5 + 32 = 80.33", "10 / 3 = 3.33", "10 / 3 = 3.3",
                                  "100 × 9/5 + 32 = 180 + 32 = 212", "(5 − 32) × 5/9 = −15",
                                  "3 x 4.50 + 2 x 1.25 = 16.00", "(100 - 32) × 5/9 = 37.77…",
                                  "add(2, 3) prints 5", "x = 5", "2 days late x 0.05 = 0.10"])
def test_working_that_adds_up(text):
    assert Working.mistakes(text) == []


@pytest.mark.spec("PLN-2")
@pytest.mark.parametrize("text,mistake", [("2 x 3 = 7", "2 * 3 is 6, not 7"), ("10 / 3 = 3.4", "10 / 3 is 3.33, not 3.4"),
                                          ("100 × 9/5 + 32 = 190 + 32 = 212", "190 + 32 is 222, not 212"),
                                          ("(100 - 32) × 5/9 = 37.78…", "(100 - 32) * 5/9 is 37.78, not 37.78")])
def test_working_that_does_not(text, mistake):
    assert Working.mistakes(text) == [mistake]


@pytest.mark.spec("PLN-2")
def test_working_values():
    assert Working.value("2 * (3 + 4)") == 14 and Working.value("-2") == -2
    assert Working.value("1 / 0") is None and Working.value("abc") is None and Working.value("2 ** 3") is None
    assert Working.balanced("(3 + 4") == "3 + 4" and Working.balanced("(3 + 4))") == "(3 + 4)"


@pytest.mark.spec("PLN-3")
@pytest.mark.parametrize("source,ok", [("setting", True), ('"Setting"', True), ("a calculator that divides", True),
                                       ("divides numbers (and rounds them)", True), ("a beautiful window", False),
                                       ("", False), ("a b c", False)])
def test_traced(source, ok):
    assert SpecWriter.traced(source, "A calculator that divides two numbers") is ok


@pytest.mark.spec("PLN-4")
@pytest.mark.parametrize("text,ok", [('calc.main(["add", "2"]) prints 2', True), ("calc(5) shows 5", True),
                                     ("python3 calc.py add 2 3 prints 5", True), ("roman 42 prints XLII", True),
                                     ("running it with no input exits with 1", True), ("it reads stdin", True),
                                     ("add(2, 3) is 5", False), ("the history keeps results", False)])
def test_runs_program(text, ok):
    assert SpecWriter.runs_program(text, "calc.py", "python3 calc.py") is ok


@pytest.mark.spec("PLN-5")
def test_effort_strings_and_changes():
    assert [SpecWriter.effort(value) for value in (0, 2, "3", 9, None, "hard")] == [1, 2, 3, 3, 2, 2]
    assert SpecWriter.strings(["a", " ", 3, None, "b"], 2) == ["a", "3"]
    before = [{"id": "C1", "test": "t::a", "behaviour": "adds"}, {"id": "C2", "test": "t::b", "behaviour": "subtracts"}]
    after = [{"id": "C1", "test": "t::a", "behaviour": "adds two"}, {"id": "C3", "test": "t::c", "behaviour": "divides"}]
    assert SpecWriter.changes(before, after) == ["~ C1 adds two", "+ C3 divides", "- subtracts"]
    assert SpecWriter.changes(before, before) == ["no spec point changed"]


REQUEST = "a calculator that adds two numbers"


def spec_reply(**changes):
    data = {"language": "Python",
            "criteria": [{"text": "calc.add(2, 3) is 5", "source": "a calculator that adds",
                          "test": "tests/test_calc.py::test_add", "effort": 1},
                         {"text": 'calc.main(["add", "2", "3"]) prints 5', "source": "adds two numbers",
                          "test": "tests/test_calc.py::test_main", "effort": 9},
                         {"text": "it has a window", "source": "a beautiful window", "test": "tests/test_calc.py::test_gui"}],
            "files": [{"path": "calc.py", "kind": "code", "role": "the calculator", "uses": []},
                      {"path": "tests/test_calc.py", "kind": "test", "role": "its tests", "uses": ["calc.py", "ghost.py"]}],
            "entry": "calc.py", "constraints": ["standard library only"], "non_goals": ["a window"], "data": "",
            "toolchain": dict(TOOLCHAIN)}
    data.update(changes)
    return json.dumps(data)


@pytest.fixture
def planner(make_project, make_session):
    project = make_project(cycles=[{"id": "base", "request": REQUEST, "phase": "spec", "kind": "feature"}],
                           toolchain={}, language="")
    session = make_session(project)
    return session, project, project.cycles[0]


@pytest.mark.spec("PLN-5")
def test_read_spec_traces_criteria_and_checks_the_plan(planner):
    session, project, cycle = planner
    plan = SpecWriter(session).read_spec(Reply(spec_reply()), cycle)
    assert [criterion["test"] for criterion in plan["criteria"]] == ["tests/test_calc.py::test_add",
                                                                     "tests/test_calc.py::test_main"]
    assert [criterion["effort"] for criterion in plan["criteria"]] == [1, 3] and plan["entry"] == "calc.py"
    assert plan["language"] == "Python" and plan["toolchain"].run.endswith("calc.py")
    assert SpecWriter(session).read_spec(Reply(spec_reply(language="")), cycle)["language"] == "Python"


@pytest.mark.spec("PLN-5")
@pytest.mark.parametrize("changes,why", [
    ({"criteria": [{"text": "x", "source": "unrelated words entirely", "test": "t.py::t"}]}, "no criteria listed"),
    ({"criteria": [{"text": "calc.add(2, 3) is 5", "source": "adds", "test": "calc.py::test_add"}],
      "files": [{"path": "calc.py", "kind": "code"}]}, "test file of its own"),
    ({"criteria": [{"text": "calc.add(2, 3) is 5", "source": "adds", "test": "tests/test_calc::test_add"}],
      "files": [{"path": "calc.py", "kind": "code"}]}, "test file of its own"),
    ({"entry": "main.py"}, "entry main.py is not one of the files"),
    ({"toolchain": None}, "it needs the toolchain"),
    ({"toolchain": dict(TOOLCHAIN, test_one="")}, "test_one is missing"),
])
def test_read_spec_rejects(planner, changes, why):
    session, project, cycle = planner
    with pytest.raises(ValueError, match=why):
        SpecWriter(session).read_spec(Reply(spec_reply(**changes)), cycle)


@pytest.mark.spec("PLN-5")
def test_read_spec_sends_wrong_working_back_twice_at_most(planner):
    session, project, cycle = planner
    wrong = spec_reply(criteria=[{"text": "calc.main(['add', '2', '3']) prints 6, since 2 + 3 = 6", "source": "adds",
                                  "test": "tests/test_calc.py::test_main"}])
    writer = SpecWriter(session)
    for _ in range(2):
        with pytest.raises(ValueError, match="the working 2 \\+ 3 is 5, not 6"):
            writer.read_spec(Reply(wrong), cycle)
    assert writer.read_spec(Reply(wrong), cycle)["criteria"]


@pytest.mark.spec("PLN-5")
def test_read_spec_takes_a_test_file_by_its_stem_or_the_only_one(planner):
    session, project, cycle = planner
    criteria = [{"text": 'calc.main(["x"]) prints x', "source": "adds", "test": "calc::test_main"}]
    plan = SpecWriter(session).read_spec(Reply(spec_reply(criteria=criteria)), cycle)
    assert plan["criteria"][0]["test"] == "tests/test_calc.py::test_main"


@pytest.mark.spec("PLN-5")
def test_read_spec_needs_the_program_run_as_a_person_runs_it(planner):
    session, project, cycle = planner
    unrun = spec_reply(criteria=[{"text": "calc.add(2, 3) is 5", "source": "adds", "test": "tests/test_calc.py::test_add"}])
    with pytest.raises(ValueError, match="is a program a person runs, yet no criterion runs it"):
        SpecWriter(session).read_spec(Reply(unrun), cycle)
    library = spec_reply(entry="", criteria=[{"text": "calc.add(2, 3) is 5", "source": "adds",
                                              "test": "tests/test_calc.py::test_add"}])
    assert SpecWriter(session).read_spec(Reply(library), cycle)["entry"] == ""


@pytest.mark.spec("PLN-6")
def test_spec_phase_makes_the_plan_the_cycles_spec(planner, fake_ollama):
    session, project, cycle = planner
    fake_ollama.reply(spec_reply(), when="Spec")
    SpecWriter(session).run(cycle)
    assert [criterion["id"] for criterion in project.criteria] == ["C1", "C2"]
    assert all(criterion["status"] == "todo" and criterion["cycle"] == "base" for criterion in project.criteria)
    assert cycle["files"] == ["calc.py", "tests/test_calc.py", "README.md"]
    assert project.files["README.md"]["kind"] == "docs"
    assert project.files["tests/test_calc.py"]["uses"] == ["calc.py"]
    assert project.spec["entry"] == "calc.py" and project.toolchain.run.endswith("calc.py")
    assert cycle["non_goals"] == ["a window"] and cycle["constraints"] == ["standard library only"]
    assert cycle["specified"] and project.language == "Python"
    assert (project.workdir / "SPEC.md").is_file()
    SpecWriter(session).run(cycle)  # specified: not planned again
    assert len(fake_ollama.calls) == 1


@pytest.mark.spec("PLN-6")
def test_a_program_without_a_run_command_gets_its_toolchain_again(planner, fake_ollama):
    session, project, cycle = planner
    fake_ollama.reply(spec_reply(toolchain=dict(TOOLCHAIN, run="")), when="Spec")
    fake_ollama.reply(json.dumps({"toolchain": TOOLCHAIN}), when="Toolchain")
    SpecWriter(session).run(cycle)
    assert project.toolchain.run == TOOLCHAIN["run"]
    assert [label.split(" (")[0] for label in fake_ollama.labels()] == ["Spec", "Toolchain"]


@pytest.mark.spec("PLN-7")
def test_amend_replaces_the_cycles_criteria(planner, fake_ollama):
    session, project, cycle = planner
    fake_ollama.reply(spec_reply(), when="Spec")
    SpecWriter(session).run(cycle)
    amended = spec_reply(criteria=[
        {"text": "calc.add(2, 3) is 5 and calc.add(-1, 1) is 0", "source": "adds", "test": "tests/test_calc.py::test_add"},
        {"text": 'calc.main(["add", "2", "3"]) prints 5', "source": "adds", "test": "tests/test_calc.py::test_main"},
        {"text": "calc.sub(5, 3) is 2", "source": "also subtract", "test": "tests/test_calc.py::test_sub"}])
    fake_ollama.reply(amended, when="Amend the spec")
    SpecWriter(session).amend(cycle, "also subtract")
    assert [criterion["id"] for criterion in project.criteria] == ["C1", "C2", "C3"]
    assert cycle["amendments"] == ["also subtract"]
    assert cycle["changes"] == ["~ C1 calc.add(2, 3) is 5 and calc.add(-1, 1) is 0", "+ C3 calc.sub(5, 3) is 2"]
    assert fake_ollama.calls[-1]["think"] is False
    assert (cycle["phase"], cycle["awaiting"], cycle["contracts_done"]) == ("design", False, [])  # planned again


@pytest.mark.spec("PLN-7")
def test_an_amendment_that_fails_leaves_the_spec(planner, fake_ollama):
    session, project, cycle = planner
    fake_ollama.reply(spec_reply(), when="Spec")
    SpecWriter(session).run(cycle)
    before = json.dumps(project.criteria)
    fake_ollama.reply("I cannot", when="Amend", times=3)
    with pytest.raises(SystemExit, match="usable amend the spec"):
        SpecWriter(session).amend(cycle, "make it a spaceship")
    assert json.dumps(project.criteria) == before and project.spec["counter"] == 2
    assert cycle["amendments"] == [] and cycle["specified"] and "calc.py" in project.files


@pytest.mark.spec("PLN-8")
def test_order_puts_config_first_and_tests_last():
    files = [{"path": "tests/test_app.py", "kind": "test", "uses": ["app.py"]},
             {"path": "README.md", "kind": "docs", "uses": []},
             {"path": "app.py", "kind": "code", "uses": ["store.py", "templates/index.html"]},
             {"path": "templates/index.html", "kind": "template", "uses": []},
             {"path": "store.py", "kind": "code", "uses": []},
             {"path": "requirements.txt", "kind": "config", "uses": []}]
    assert Designer.order(files) == ["requirements.txt", "templates/index.html", "store.py", "app.py",
                                     "tests/test_app.py", "README.md"]


@pytest.mark.spec("PLN-8")
def test_read_toolchain_data(capsys):
    toolchain = Designer.read_toolchain_data(dict(TOOLCHAIN, setup=["cp .env.example .env", "brew install x"]), "calc.py")
    assert toolchain.setup == [] and "left out `cp .env.example .env`" in capsys.readouterr().out
    with pytest.raises(ValueError, match="toolchain.run is empty"):
        Designer.read_toolchain_data(dict(TOOLCHAIN, run=""), "calc.py")
    with pytest.raises(ValueError, match="lint is missing"):
        Designer.read_toolchain_data(dict(TOOLCHAIN, lint=""))
    with pytest.raises(ValueError, match="test_all is missing"):
        Designer.read_toolchain_data("junk")


@pytest.mark.spec("PLN-8")
def test_templates_get_a_renderer_and_files_their_layout(project_dir):
    files = [{"path": "templates/list.html", "kind": "template", "uses": []},
             {"path": "templates/other.html", "kind": "template", "uses": []},
             {"path": "views.py", "kind": "code", "role": "renders list.html", "uses": []},
             {"path": "app.py", "kind": "code", "role": "the app", "uses": []}]
    Designer.give_templates_a_renderer(files, "app.py")
    assert files[2]["uses"] == ["templates/list.html"] and files[3]["uses"] == ["templates/other.html"]
    from tinkerkit.workspace import Workspace
    entry = Designer.layout_file({"path": "./src/x.py", "kind": "Weird", "purpose": "does x", "uses": ["./a.py", 3]},
                                 set(), Workspace(project_dir))
    assert entry == {"path": "src/x.py", "kind": "code", "role": "does x", "contract": [], "uses": ["a.py"]}
    assert Designer.layout_file({"path": "t.py", "kind": "code"}, {"t.py"}, Workspace(project_dir))["kind"] == "test"


@pytest.mark.spec("PLN-8")
def test_adapter_criteria_are_dropped(make_project, make_session):
    files = {"weather_api.py": {"kind": "code", "role": "adapter: the weather service", "uses": []},
             "app.py": {"kind": "code", "role": "the app", "uses": ["weather_api.py"]}}
    steps = [criterion("C1", "t.py::a", "weather_api.fetch('Oslo') returns the JSON"),
             criterion("C2", "t.py::b", "app.forecast('Oslo') says Sunny")]
    project = make_project(steps, files=files)
    Designer(make_session(project)).drop_adapter_criteria(project.cycles[0])
    assert [criterion["id"] for criterion in project.criteria] == ["C2"]


@pytest.mark.spec("PLN-8")
def test_ensure_toolchain_asks_only_when_missing(make_project, make_session, fake_ollama):
    project = make_project(toolchain={}, entry="calc.py")
    fake_ollama.reply(json.dumps({"toolchain": TOOLCHAIN}), when="Toolchain")
    Designer(make_session(project)).ensure_toolchain()
    assert project.toolchain.test_all == TOOLCHAIN["test_all"]
    Designer(make_session(project)).ensure_toolchain()
    assert len(fake_ollama.calls) == 1


DESIGN = {"app.py": {"kind": "code", "role": "the app", "contract": [], "uses": ["store.py"]},
          "store.py": {"kind": "code", "role": "keeps notes", "contract": [], "uses": []},
          "pkg/__init__.py": {"kind": "code", "role": "package marker", "contract": [], "uses": []},
          "tests/test_app.py": {"kind": "test", "role": "tests", "contract": [], "uses": ["app.py"]}}


@pytest.mark.spec("PLN-9")
def test_contract_order_needed_and_markers(make_project, make_session):
    project = make_project(files=json.loads(json.dumps(DESIGN)), entry="app.py")
    cycle = project.cycles[0]
    writer = ContractWriter(make_session(project))
    assert ContractWriter.order(project.files, ["app.py", "store.py", "tests/test_app.py"]) == ["store.py", "app.py"]
    assert writer.needed(cycle, "store.py") and not writer.needed(cycle, "app.py")
    assert not writer.needed(cycle, "pkg/__init__.py") and project.files["pkg/__init__.py"]["marker"]
    assert "marker" not in project.files["app.py"]


@pytest.mark.spec("PLN-9")
def test_contracts_are_written_where_files_meet(make_project, make_session, fake_ollama):
    files = dict(json.loads(json.dumps(DESIGN)), **{"util.py": {"kind": "code", "role": "helpers", "contract": [],
                                                              "uses": []}})
    project = make_project([criterion("C1", "tests/test_app.py::test_add", "app.add_note('x') prints Added x")],
                           files=files, entry="app.py")
    cycle = dict(project.cycles[0], files=["store.py", "app.py", "tests/test_app.py"])
    fake_ollama.reply(json.dumps({"contract": ["save(text) -> int: the note's id"], "uses": ["ghost.py", "util.py", "store.py"]}),
                      when="Contract of store.py")
    ContractWriter(make_session(project)).write_all(cycle)
    assert project.files["store.py"]["contract"] == ["save(text) -> int: the note's id"]
    assert project.files["store.py"]["uses"] == ["util.py"]
    assert project.files["app.py"]["contract"] == [] and cycle["contracts_done"] == ["store.py", "app.py"]
    assert fake_ollama.labels()[0].startswith("Contract of store.py")


REVIEWED = {"app.py": {"kind": "code", "role": "the app", "contract": ["main() calls a.f() and b.g()"],
                       "uses": ["a.py", "b.py"]},
            "a.py": {"kind": "code", "role": "a", "contract": ["f() -> int"], "uses": []},
            "b.py": {"kind": "code", "role": "b", "contract": ["load(path) -> str"], "uses": []},
            "solo.py": {"kind": "code", "role": "solo", "contract": ["x()"], "uses": ["a.py"]}}


@pytest.mark.spec("PLN-10")
def test_interface_review_corrects_and_checks_again(make_project, make_session, fake_ollama):
    project = make_project(files=json.loads(json.dumps(REVIEWED)))
    cycle = dict(project.cycles[0], files=["a.py", "b.py", "solo.py", "app.py"])
    fake_ollama.reply(json.dumps({"problems": [{"file": "app.py", "problem": "b has load, not g"}],
                                  "files": [{"path": "app.py", "contract": ["main() calls a.f() and b.load(path)"]}]}),
                      when="Interface check 1/1 (app.py)")
    fake_ollama.reply(json.dumps({"problems": [], "files": []}), when="Interface check again 1/1 (app.py)")
    InterfaceReviewer(make_session(project)).run(cycle)
    assert project.files["app.py"]["contract"] == ["main() calls a.f() and b.load(path)"]
    assert project.files["app.py"]["uses"] == ["a.py", "b.py"] and project.files["app.py"]["kind"] == "code"
    assert cycle["review"] == ["app.py: b has load, not g"]
    assert len(fake_ollama.calls) == 2  # solo.py uses one contracted file only: not checked


@pytest.mark.spec("PLN-10")
def test_interface_review_with_nothing_to_check(make_project, make_session, fake_ollama):
    project = make_project(files={"a.py": {"kind": "code", "role": "a", "contract": ["f()"], "uses": []}})
    cycle = dict(project.cycles[0], files=["a.py"])
    InterfaceReviewer(make_session(project)).run(cycle)
    assert fake_ollama.calls == [] and "review" not in cycle


def topic(slug, **entry):
    Library.save(slug, {"topic": slug, "name": entry.pop("name", slug.title()), "summary": "s", "facts": [], **entry})


@pytest.mark.spec("PLN-11")
def test_recall_with_nothing_learned_makes_no_call(make_project, make_session, fake_ollama):
    project = make_project()
    cycle = project.cycles[0]
    Recaller(make_session(project)).run(cycle)
    assert cycle["topics"] == [] and fake_ollama.calls == []


@pytest.mark.spec("PLN-11")
def test_recall_without_the_web_uses_the_topics_named_and_the_language(make_project, make_session, fake_ollama):
    topic("flask", name="Flask", facts=[{"phase": "code", "text": "Flask routes use decorators"}])
    topic("django", name="Django", facts=[{"phase": "code", "text": "Django has an ORM"}])
    topic("python", name="Python 3.14", kind="language", language="Python",
          tools=[{"role": "tests", "name": "pytest"}], facts=[{"phase": "code", "text": "match is soft"}])
    project = make_project(cycles=[{"id": "base", "request": "a Flask app for notes", "phase": "recall"}])
    cycle = project.cycles[0]
    Recaller(make_session(project)).run(cycle)
    assert cycle["topics"] == ["python", "flask"] and fake_ollama.calls == []
    learned = project.spec["learned"]
    assert learned[0]["phase"] == "tools" and {item["topic"] for item in learned} == {"python", "flask"}
    assert all(item["phase"] != "summary" for item in learned)


@pytest.mark.spec("PLN-11")
def test_recall_keeps_a_languages_rules_before_its_other_facts(make_project, make_session, fake_ollama):
    topic("python", name="Python 3.14", kind="language", language="Python",
          tools=[{"role": "tests", "name": "pytest"}],
          facts=[{"phase": "code", "text": "Python has match"}, {"phase": "code", "text": "t-strings are new"}])
    entry = Library.read("python")
    entry["rules"] = [{"phase": "code", "text": "Use match for dispatch", "cites": [entry["facts"][0]["id"]]}]
    Library.save("python", entry)
    project = make_project(cycles=[{"id": "base", "request": "a calculator in Python", "phase": "recall"}])
    Recaller(make_session(project)).run(project.cycles[0])
    assert [item["text"] for item in project.spec["learned"]] == [
        "its tools: tests pytest", "Use match for dispatch"]  # the rules stand in for the facts


@pytest.mark.spec("PLN-11")
def test_topic_listing_fits_the_room():
    known = {"a": {"name": "A", "summary": "first"}, "b": {"name": "B", "summary": "second"}}
    assert Recaller.topic_listing(known, ["b", "a"], 1000) == "- b: B, second\n- a: A, first"
    assert Recaller.topic_listing(known, ["b", "a"], 16) == "- b: B, second"


class Web:
    def available(self):
        return True

    def problem(self, key=None):
        return ""


@pytest.mark.spec("PLN-12")
def test_recall_with_the_web_learns_what_it_needs(make_project, make_session, fake_ollama, monkeypatch):
    topic("flask", name="Flask", facts=[{"phase": "code", "text": "Flask routes use decorators"}])
    project = make_project(cycles=[{"id": "base", "request": "a Flask app with background jobs", "phase": "recall"}])
    learned = []
    monkeypatch.setattr(Recaller, "learn", lambda self, name, internet, retriever: learned.append(name) or
                        Library.slug(name))
    fake_ollama.reply(json.dumps({"use": ["flask", "unknown"], "learn": ["Celery", "celery", "Redis", "RQ"]}),
                      when="Recall")
    cycle = project.cycles[0]
    Recaller(make_session(project, internet=Web())).run(cycle)
    assert learned == ["Celery", "Redis"] and cycle["topics"] == ["celery", "redis", "flask"]
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "possible: name up to 2 topics to learn" in prompt and "- flask: Flask" in prompt


@pytest.mark.spec("PLN-12")
def test_recall_goes_on_when_its_call_fails(make_project, make_session, fake_ollama, capsys):
    topic("flask", name="Flask")
    project = make_project(cycles=[{"id": "base", "request": "a Flask app", "phase": "recall"}])
    fake_ollama.reply("no json", when="Recall", times=2)
    cycle = project.cycles[0]
    Recaller(make_session(project, internet=Web())).run(cycle)
    assert cycle["topics"] == [] and "Going on without what was learned" in capsys.readouterr().out


@pytest.mark.spec("PLN-6")
def test_a_plan_to_confirm_is_a_quick_draft(planner, fake_ollama):
    session, project, cycle = planner
    cycle["confirm"] = True
    fake_ollama.reply(spec_reply(), when="Spec")
    SpecWriter(session).run(cycle)
    assert fake_ollama.calls[0]["think"] == "low"


def python_with_conditional_rules():
    topic("python", name="Python 3.14", kind="language", language="Python",
          tools=[{"role": "environment", "name": "uv"}, {"role": "tests", "name": "pytest"}],
          machine=[{"command": "uv --version", "found": False, "output": ""}],
          facts=[{"phase": "code", "text": "a fact"}])
    entry = Library.read("python")
    cite = [entry["facts"][0]["id"]]
    entry["rules"] = [{"phase": "code", "text": "Never use a mutable default argument", "when": "", "cites": cite},
                      {"phase": "code", "text": "Use inspect.iscoroutinefunction", "when": "async code", "cites": cite},
                      {"phase": "code", "text": "Open text files with an encoding", "when": "code that reads files",
                       "cites": cite},
                      {"phase": "config", "text": "Set build-backend uv_build", "when": "", "cites": cite}]
    Library.save("python", entry)
    return {rule["text"]: rule["id"] for rule in Library.read("python")["rules"]}


def choose(situation):
    """A spec reply that says the project has the listed situation of this name."""
    def reply(messages):
        listing = messages[-1]["content"].split("Code some learned rules are for", 1)[1]
        number = next(int(line.split(".")[0]) for line in listing.splitlines() if line.endswith(situation))
        return spec_reply(situations=[number, 99])
    return reply


@pytest.mark.spec("PLN-13")
def test_rules_for_code_the_project_does_not_have_stay_out_of_its_prompts(make_project, make_session, fake_ollama):
    ids = python_with_conditional_rules()
    Knowledge("Python").remember_toolchain(Toolchain(TOOLCHAIN))
    project = make_project(cycles=[{"id": "base", "request": REQUEST + " in Python", "phase": "recall",
                                    "kind": "feature"}], toolchain={}, language="")
    session, cycle = make_session(project), project.cycles[0]
    Recaller(session).run(cycle)
    assert cycle["topics"] == ["python"]  # named in the request, without its version
    assert set(project.spec["inapplicable"]) == {ids["Use inspect.iscoroutinefunction"],
                                                 ids["Open text files with an encoding"],
                                                 ids["Set build-backend uv_build"]}  # uv is not installed here
    fake_ollama.reply(choose("code that reads files"), when="Spec")
    SpecWriter(session).run(cycle)
    assert project.spec["situations"] == ["code that reads files"]
    shown = "\n".join(part.text for part in session.knowledge_parts("code", "config"))
    assert "mutable default" in shown and "encoding" in shown
    assert "iscoroutinefunction" not in shown and "uv_build" not in shown


@pytest.mark.spec("PLN-13")
def test_a_proven_rule_for_other_code_stays_out_too(make_project, make_session):
    ids = python_with_conditional_rules()
    Knowledge("Python").prove({f"fact:python:{ids['Use inspect.iscoroutinefunction']}": ["code"]})
    assert Knowledge("Python").facts(("code",))[0]["when"] == "async code"
    project = make_project(cycles=[{"id": "base", "request": "a calculator in Python", "phase": "recall",
                                    "kind": "feature"}])
    session = make_session(project)
    Recaller(session).run(project.cycles[0])
    shown = "\n".join(part.text for part in session.knowledge_parts("code"))
    assert "mutable default" in shown and "iscoroutinefunction" not in shown


@pytest.mark.spec("PLN-5")
def test_the_spec_sees_the_config_rules_since_it_plans_the_config_files(planner, fake_ollama):
    session, project, cycle = planner
    topic("python", name="Python 3.14", kind="language", language="Python", facts=[{"phase": "code", "text": "f"}])
    entry = Library.read("python")
    entry["rules"] = [{"phase": "config", "text": "Set pythonpath in pyproject.toml", "when": "", "cites": [entry["facts"][0]["id"]]}]
    Library.save("python", entry)
    project.spec["learned"] = Library.items({"python": Library.read("python")})[1:]
    fake_ollama.reply(spec_reply(), when="Spec")
    SpecWriter(session).run(cycle)
    assert "Set pythonpath in pyproject.toml" in fake_ollama.calls[0]["messages"][-1]["content"]


@pytest.mark.spec("PLN-5")
def test_a_settings_file_is_planned_only_for_a_spec_point_about_a_setting(planner):
    session, project, cycle = planner
    files = [{"path": "calc.py", "kind": "code"}, {"path": "tests/test_calc.py", "kind": "test"},
             {"path": ".env.example", "kind": "template", "role": "(none needed)"}]
    plan = SpecWriter(session).read_spec(Reply(spec_reply(files=files)), cycle)
    assert ".env.example" not in [file["path"] for file in plan["files"]]
    setting = {"text": "with PRECISION=3 in .env, divide prints 3 decimals", "source": "a calculator that adds",
               "test": "tests/test_calc.py::test_precision"}
    criteria = json.loads(spec_reply())["criteria"][:2] + [setting]
    plan = SpecWriter(session).read_spec(Reply(spec_reply(files=files, criteria=criteria)), cycle)
    assert ".env.example" in [file["path"] for file in plan["files"]]
