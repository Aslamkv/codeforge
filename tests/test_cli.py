"""CLI: tinker.py: the command line, the commands that show or change settings, a run and its cycle, and a whole
task built end to end (with a scripted model and real test runs). SES: the session every phase works through."""

import argparse
import datetime
import json
import os
import sys

import pytest

from tinkerkit import cli
from tinkerkit.cli import Cli, Commands, CycleRunner, Doctor, ProjectFolder, ProjectRun
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.model import Models, Ollama, OllamaError
from tinkerkit.project import Runs, Tracker, project_store
from tinkerkit.prompts import Part, PromptTooLarge, Prompts
from tinkerkit.session import Phase
from tinkerkit.settings import Config, FIX_PHASES, Home, PHASES
from conftest import MEASURED, MODEL, PY, TOOLCHAIN, criterion, file_block, plan_of, record_of


def parse(*argv):
    return Cli.parse(list(argv))


def registered(folder, run_id="abc123", **record):
    folder.mkdir(parents=True, exist_ok=True)
    Runs.save(dict(Runs.new_record("a calculator", folder, None), id=run_id, **record), folder)


@pytest.mark.spec("CLI-1")
def test_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = parse("task", "a", "calculator", "in", "Python", "--web", "--confirm")
    assert (args.command, args.task, args.workdir, args.web, args.confirm) == \
        ("task", "a calculator in Python", None, True, True)
    assert parse("task", "x").confirm is False
    (tmp_path / "plan.md").write_text("spec")
    args = parse("task", "a calculator", "plan.md")
    assert (args.task, args.spec) == ("a calculator", "plan.md")
    assert parse("task", "notes about plan.txt").task == "notes about plan.txt"  # not a file: a word


@pytest.mark.spec("CLI-1")
@pytest.mark.parametrize("argv,why", [(["--task", "x"], "commands are written without --"), (["fly"], "no command 'fly'"),
                                      (["task", "x", "--yes"], None), (["task", "x", "--plan-only"], None),
                                      (["task", "x", "--no-confirm"], None), (["resume", "abc123"], None),
                                      (["status", "@abc", "--workdir", "x"], None), (["rename", "bad name!"], None),
                                      (["learn", "a", "b", "c"], None), (["use-web", "bing"], None),
                                      (["resume", "@abc", "--workdir", "x"], None), (["add"], None),
                                      (["config", "only-a-key"], None), (["list-config"], "no command 'list-config'")])
def test_mistakes_are_errors(argv, why, capsys):
    with pytest.raises(SystemExit) as error:
        parse(*argv)
    if why:
        assert why in str(error.value.code)
    else:
        assert error.value.code == 2 and "error:" in capsys.readouterr().err


@pytest.mark.spec("CLI-1")
def test_projects_are_named_with_an_at(tmp_path):
    args = parse("confirm", "@abc123")
    assert (args.command, args.resume, args.workdir) == ("resume", "abc123", ".")
    args = parse("add", "@calc", "keep", "a", "history")
    assert (args.resume, args.change_request, args.add, args.bug) == ("calc", "keep a history", ["keep a history"], False)
    registered(tmp_path / "add", "add123")
    args = parse("add", "add", "a", "history")  # a word, even one naming a project, is part of the request
    assert (args.resume, args.change_request) == ("", "add a history")
    args = parse("fix", "100", "divided", "by", "0", "crashes")
    assert (args.bug, args.change_request, args.fix, args.resume) == (True, "100 divided by 0 crashes", None, "")
    args = parse("amend", "@abc123", "also", "divide")
    assert (args.amend, args.resume) == ("also divide", "abc123")
    with pytest.raises(SystemExit):
        parse("amend", "@abc123", "also", "divide", "--confirm")
    assert parse("rename", "@abc123", "calc").rename == ("abc123", "calc")
    assert parse("rename", "calc").rename == ("", "calc")
    assert parse("learn", "act", ". .venv/bin/activate").learn == ["act", ". .venv/bin/activate"]
    assert parse("status").status == "" and parse("status", "@calc").status == "calc" and parse("runs").runs is True
    assert parse("log", "@calc", "--calls").log == "calc" and parse("log").calls is False
    assert parse("use-web", "duckduckgo").use_web == "duckduckgo"
    assert parse("config").config == [] and parse("config", "test_timeout", "90").config == ["test_timeout", "90"]


@pytest.mark.spec("CLI-1")
def test_clear_runs_on_its_own(home_store):
    home_store.put("memory", "any", {})
    with pytest.raises(SystemExit) as done:
        parse("clear", "memory", "--yes")
    assert done.value.code == 0 and home_store.items("memory") == {}


@pytest.mark.spec("CLI-2")
def test_help_overview(capsys):
    with pytest.raises(SystemExit):
        parse("help")
    out = capsys.readouterr().out
    from tinkerkit.commands import COMMANDS
    for command in COMMANDS:
        for synopsis, what in command.rows:
            assert synopsis.split()[0] in out and what in out
    for option in ("--spec FILE", "--web", "--confirm", "--workdir DIR", "--yes", "--calls"):
        assert option in out
    assert "@ID" in out and "💡 Examples" in out and "tinker help COMMAND" in out


@pytest.mark.spec("CLI-2")
def test_help_markup_and_one_command(capsys, monkeypatch):
    monkeypatch.setenv("TINKER_FORMAT", "markup")
    with pytest.raises(SystemExit):
        parse("help")
    out = capsys.readouterr().out
    assert out.startswith("🛠 **What you can send**") and "- `task WHAT TO BUILD`" in out
    assert "💬 anything else: a normal chat" in out
    with pytest.raises(SystemExit):
        parse("help", "task")
    assert capsys.readouterr().out.startswith("usage: tinker task")
    with pytest.raises(SystemExit):
        parse("help", "research")
    assert capsys.readouterr().out.startswith("usage: tinker research TOPIC")
    with pytest.raises(SystemExit, match="no command 'fly'"):
        parse("help", "fly")
    assert "resume" in Cli.options()["--workdir DIR"][1] and "fix" in Cli.options()["--confirm"][1]


@pytest.mark.spec("CLI-3")
def test_use_model_and_use_web(home_store, capsys):
    with pytest.raises(SystemExit, match="has not been measured"):
        Commands.use_model("ghost")
    Models.keep("m", MEASURED)
    Commands.use_model("m")
    Commands.use_web("duckduckgo")
    assert Config.get("model") == "m" and Config.get("web") == "duckduckgo"
    assert "Web search: duckduckgo" in capsys.readouterr().out


@pytest.mark.spec("CLI-3")
def test_runs_and_status(tmp_path, capsys):
    registered(tmp_path / "older", "aaa111")
    registered(tmp_path / "newer", "bbb222")
    for folder, updated in (("older", "2026-10-01 10:00"), ("newer", "2026-10-02 10:00")):
        store = project_store(tmp_path / folder)
        store.put("run", "record", dict(store.get("run", "record"), updated=updated))
    Commands.list_runs()
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("PROJECT") and lines[1].startswith("@bbb222") and lines[2].startswith("@aaa111")
    steps = [criterion("C1", "t.py::test_a", "adds", status="green"),
             criterion("C2", "t.py::test_b", "subtracts", status="blocked", last_error="AssertionError: 8 != 2")]
    project_store(tmp_path / "older").put("spec", "plan", {"steps": steps, "language": "Python", "cycles": [
        {"id": "base", "phase": "setup", "awaiting": True}]})
    Commands.show_status("older", None)
    out = capsys.readouterr().out
    assert "1/2 criteria pass" in out and "last error: AssertionError: 8 != 2" in out
    assert "project: next phase setup (its plan waits for your confirm)" in out


@pytest.mark.spec("CLI-3")
def test_rename_a_folder_whose_plan_waits(tmp_path):
    folder = tmp_path / "calc-py"
    registered(folder)
    project_store(folder).put("spec", "plan", {"cycles": [{"id": "base", "phase": "setup", "awaiting": True}]})
    Commands.rename("abc123", "calculator", None)
    assert not folder.exists() and (tmp_path / "calculator" / ".tinker" / "project.db").is_file()
    assert Runs.find("abc123")["workdir"] == str(tmp_path / "calculator") and Runs.find("calculator")["id"] == "abc123"
    (tmp_path / "taken").mkdir()
    with pytest.raises(SystemExit, match="already exists"):
        Commands.rename("abc123", "taken", None)
    project_store(tmp_path / "calculator").put("spec", "plan", {"cycles": [{"id": "base", "phase": "done"}]})
    with pytest.raises(SystemExit, match="renames the folder of a plan that waits"):
        Commands.rename("abc123", "other", None)


@pytest.mark.spec("CLI-3")
def test_config(measured_model, capsys):
    Commands(parse("config")).run()
    out = capsys.readouterr().out
    for line in (f"model in use:    {MODEL} (calibrated", "input budget:    8192 tokens", "context:         32768 tokens",
                 "think:           high for planning, medium for building", "keep_alive:      0",
                 "test_timeout:    60", "web:             ollama"):
        assert line in out, line
    Commands(parse("config", "test_timeout", "90")).run()
    assert Config.get("test_timeout") == 90 and "test_timeout is 90 now" in capsys.readouterr().out


@pytest.mark.spec("CLI-4")
def test_doctor(monkeypatch, capsys):
    assert [name for _, name, _ in Doctor().checks()] == ["Ollama", "model", "embedding model (for tinker learn and --web)",
                                                          "web (for tinker learn and --web)", "tinker command"]
    with pytest.raises(SystemExit, match="does not answer"):
        Doctor.ollama()  # the network is off in tests
    ok = lambda: "fine"

    def missing():
        raise SystemExit("not there")
    monkeypatch.setattr(Doctor, "checks", lambda self: [(True, "Ollama", ok), (False, "web", missing)])
    Doctor().run()
    assert "Everything Tinker needs is there." in capsys.readouterr().out
    monkeypatch.setattr(Doctor, "checks", lambda self: [(True, "Ollama", missing), (False, "web", missing)])
    with pytest.raises(SystemExit) as failed:
        Doctor().run()
    assert failed.value.code == 1 and "1 thing(s) to fix" in capsys.readouterr().out


@pytest.mark.spec("CLI-5")
def test_project_folders(measured_model, fake_ollama, tmp_path):
    Config.save(projects=str(tmp_path / "Projects"))
    assert ProjectFolder.from_words("Build me a simple calculator app in Python") == "calculator-python"
    assert ProjectFolder.from_words("a the of") == "project"
    fake_ollama.reply("`calc-py`")
    assert ProjectFolder.make("a calculator in Python") == tmp_path / "Projects" / "calc-py"
    fake_ollama.reply("calc-py")
    assert ProjectFolder.make("a calculator in Python").name == "calc-py-2"
    fake_ollama.reply("This Is Not A Folder Name")
    assert ProjectFolder.make("a todo list").name == "todo-list"
    fake_ollama.reply(OllamaError("down"))
    assert ProjectFolder.named("x") == ""
    assert fake_ollama.calls[0]["think"] is False and fake_ollama.calls[0]["options"]["num_predict"] == 24


@pytest.mark.spec("CLI-6")
def test_log_prints_the_output_or_the_model_calls(tmp_path, capsys):
    folder = tmp_path / "calc"
    registered(folder)
    store = project_store(folder)
    for line in ("Run abc123 (new): a calculator", "Done."):
        store.append("output", {"line": line})
    store.append("events", {"label": "Spec (attempt 1)", "reply": '{"language": "Python"}'})
    store.append("events", {"label": "Write calc.py", "error": "runner failed"})
    Commands(parse("log", "@calc")).run()
    assert capsys.readouterr().out == "Run abc123 (new): a calculator\nDone.\n"
    Commands(parse("log", "@abc123", "--calls")).run()
    out = capsys.readouterr().out
    assert "] Spec (attempt 1)\n    {\"language\": \"Python\"}" in out and "Write calc.py  error: runner failed" in out


@pytest.fixture
def session(make_project, make_session, project_dir):
    return make_session(make_project(toolchain=dict(TOOLCHAIN, setup=["echo ran >> setups.txt"],
                                                    test_all="echo run >> runs.txt")))


@pytest.mark.spec("SES-1")
def test_the_session_holds_what_phases_need(make_project, make_session, make_model):
    tracker = Tracker("calc")
    model = make_model()
    session = make_session(make_project(), model=model, confirm=False, tracker=tracker)
    assert (session.model, session.confirm, session.tracker, session.cycle) == (model, False, tracker, None)
    assert session.toolchain.test_all == TOOLCHAIN["test_all"]
    worker = Phase(session)
    assert (worker.session, worker.project, worker.workspace) == (session, session.project, session.workspace)


@pytest.mark.spec("SES-2")
def test_setup_runs_only_when_it_changed(session, project_dir):
    runs = lambda: (project_dir / "setups.txt").read_text().count("ran")
    assert session.setup() == (True, "")
    session.setup()
    assert runs() == 1
    session.setup(force=True)
    assert runs() == 2
    session.project.files["pyproject.toml"] = {"kind": "config", "role": "settings"}
    (project_dir / "pyproject.toml").write_text("[tool]")
    session.setup()
    assert runs() == 3
    (project_dir / ".env.example").write_text("KEY=<your key>\n")
    session.setup()
    assert (project_dir / ".env").read_text() == "KEY=\n"


@pytest.mark.spec("SES-2")
def test_the_suite_runs_once_per_state_of_the_files(session, project_dir):
    session.project.files["calc.py"] = {"kind": "code"}
    (project_dir / "calc.py").write_text("one")
    session.run_all_tests()
    session.run_all_tests()
    assert (project_dir / "runs.txt").read_text().count("run") == 1
    (project_dir / "calc.py").write_text("two")
    session.run_all_tests()
    assert (project_dir / "runs.txt").read_text().count("run") == 2


@pytest.mark.spec("SES-2")
def test_note_ask_until_usable_and_room(session, fake_ollama, monkeypatch):
    for number in range(35):
        session.note("fix", f"problem {number}")
    assert len(session.project.spec["events"]) == 30 and session.project.spec["events"][0]["text"] == "problem 5"
    fake_ollama.reply("nope", times=3)
    with pytest.raises(SystemExit, match="Could not get a usable plan. See the model calls: tinker log --calls"):
        session.ask_until_usable("s", [Part("Q", "q", True)], "Plan", lambda reply: reply.json())

    def too_large(*args, **kwargs):
        raise PromptTooLarge("too big")
    monkeypatch.setattr(session.model, "ask_until_usable", too_large)
    with pytest.raises(SystemExit, match="even shortened, the prompt does not fit"):
        session.ask_until_usable("s", [], "Plan", lambda reply: reply)
    assert session.room("system") == int(session.model.limit_chars("system" + Prompts.NO_TOOL_CALLS) * 0.95)


@pytest.mark.spec("SES-2")
def test_format_code_drops_a_formatter_that_fails(session, project_dir, capsys):
    session.project.spec["toolchain"]["format"] = "echo formatted > formatted.txt"
    session.format_code()
    assert (project_dir / "formatted.txt").exists() and session.toolchain.format
    session.project.spec["toolchain"]["format"] = "no-such-formatter-xyz ."
    session.format_code()
    assert session.toolchain.format == "" and "The formatter does not work" in capsys.readouterr().out


@pytest.mark.spec("SES-3")
def test_knowledge_parts_record_what_they_show(session, home_store):
    knowledge = Knowledge("Python")
    knowledge.add_lessons([{"text": "Use tmp_path in tests", "phase": "tests", "scope": "any"}], [])
    Library.save("flask", {"topic": "flask", "name": "Flask", "summary": "s", "facts": [
        {"phase": "tests", "text": "use the test client"}, {"phase": "code", "text": "routes use decorators"}]})
    proven, client = (fact["id"] for fact in reversed(Library.read("flask")["facts"]))
    knowledge.prove({f"fact:flask:{proven}": ["code"]})
    session.project.spec["learned"] = [item for item in Library.items({"flask": Library.read("flask")}) if item["id"]]
    session.cycle = cycle = session.project.cycles[0]
    parts = session.knowledge_parts("tests", "code")
    assert [part.title for part in parts] == ["Lessons from earlier projects (follow them)",
                                              "Learned from the web (current; newer than what you were trained on)"]
    assert parts[0].text == "- Use tmp_path in tests"
    assert parts[1].text == "- [Python] routes use decorators\n- [Flask] use the test client"  # proven once, not twice
    assert all(not part.required and part.place == "first" for part in parts)
    lesson_id = knowledge.known_lessons()[0]["id"]
    assert cycle["used"] == {f"lesson:{lesson_id}": ["code", "tests"], f"known:{proven}": ["code", "tests"],
                             f"fact:flask:{client}": ["code", "tests"]}
    assert len(session.knowledge_parts("toolchain")) == 3


@pytest.mark.spec("SES-3")
def test_code_and_tests_are_told_the_checks_they_must_pass(session):
    session.project.spec["toolchain"]["lint"] = "ruff check . && mypy --strict ."
    checks = [part for part in session.knowledge_parts("code") if part.title.startswith("Checks")]
    assert [part.text for part in checks] == ["$ ruff check . && mypy --strict ."]
    assert not [part for part in session.knowledge_parts("design", "toolchain") if part.title.startswith("Checks")]
    session.project.spec["toolchain"]["lint"] = "none"
    assert not [part for part in session.knowledge_parts("tests") if part.title.startswith("Checks")]


@pytest.mark.spec("SES-5")
def test_before_the_spec_names_the_language_knowledge_is_for_the_one_the_request_names(session, home_store):
    Library.save("rust", {"topic": "rust", "kind": "language", "language": "Rust", "name": "Rust 1.95", "facts": []})
    Knowledge("Go").finished_project()
    session.project.spec["language"] = ""
    for request, language in [("a calculator in Rust", "Rust"), ("a Go tool, later ported to Rust", "Go"),
                              ("a calculator", "Python"), ("a trusty calculator", "Python")]:
        session.project.cycles[0]["request"] = request
        assert session.knowledge.language == language
    session.project.spec["language"] = "Rust"
    session.project.cycles[0]["request"] = "a calculator in Go"
    assert session.knowledge.language == "Rust"  # the spec named it


@pytest.mark.spec("SES-3")
def test_the_phases_a_failure_disproves(session):
    assert session.failed_phases("setup") == ("toolchain", "config")
    assert session.failed_phases("verify") == ("code", "tests", "design", "spec")
    assert session.failed_phases("reproduce") == ()
    session.build_problem = "no colon"
    assert session.failed_phases("verify") == ("code", "config")


@pytest.mark.spec("SES-4")
def test_save_keeps_the_project_and_tells_the_tracker(make_project, make_session, project_dir):
    tracker = Tracker("calc")
    session = make_session(make_project([criterion("C1", "t.py::a", status="green")]), tracker=tracker)
    session.save()
    assert plan_of(project_dir)["steps"][0]["id"] == "C1" and tracker.state["passing"] == [1, 1]


@pytest.mark.spec("SES-4")
def test_each_build_step_says_what_came_of_it(make_project, make_session):
    project = make_project([criterion("C1", "t.py::a", status="green"), criterion("C2", "t.py::b")])
    runner, cycle = CycleRunner(make_session(project)), {"id": "base", "files": ["calc.py", "t.py"],
                                                          "code_done": ["calc.py"]}
    assert [runner.outcome(phase, cycle) for phase in ("spec", "design", "tests", "code", "verify", "tidy")] == [
        "2 point(s) to test", "2 file(s)", "0/2 test(s) written", "1 file(s) written", "1/2 spec points pass", ""]


def recording_phases(monkeypatch, stop_at=None):
    ran = []

    class Recorded(Phase):
        TITLE, BUILDS = "", False

        def run(self, cycle):
            ran.append(cycle["phase"])
            return False if cycle["phase"] == stop_at else None
    monkeypatch.setattr(CycleRunner, "phase_class", staticmethod(lambda phase: Recorded))
    return ran


@pytest.mark.spec("CLI-7")
def test_cycle_runs_its_phases_in_order(session, monkeypatch, project_dir):
    ran = recording_phases(monkeypatch)
    formatted = []
    monkeypatch.setattr(session, "format_code", lambda: formatted.append(session.project.cycles[0]["phase"]))
    cycle = session.project.cycles[0]
    assert CycleRunner(session).run(cycle) == "done"
    assert ran == list(PHASES[:-1]) and formatted == ["verify"] and cycle["phase"] == "done"
    assert plan_of(project_dir)["cycles"][0]["phase"] == "done" and session.cycle is cycle
    fix = session.project.new_cycle("f1", "a bug", kind="fix")
    ran.clear()
    assert CycleRunner(session).run(fix) == "done" and ran == list(FIX_PHASES[:-1])


@pytest.mark.spec("CLI-7")
def test_cycle_waits_after_its_plan_unless_told_not_to(session, monkeypatch):
    ran = recording_phases(monkeypatch)
    cycle = dict(session.project.cycles[0], confirm=True)
    session.project.cycles[0] = cycle
    assert CycleRunner(session).run(cycle) == "awaiting" and ran == ["recall", "spec", "design", "review"]
    assert cycle["phase"] == "setup" and cycle["awaiting"]
    cycle.update(awaiting=False, confirmed=True)
    assert CycleRunner(session).run(cycle) == "done"
    fix = dict(session.project.new_cycle("f1", "a bug", kind="fix"), confirm=True)
    session.project.cycles[-1] = fix
    ran.clear()
    assert CycleRunner(session).run(fix) == "awaiting" and ran == ["recall", "reproduce"]
    feature = dict(session.project.new_cycle("f2", "more", kind="feature"), confirm=False)
    session.project.cycles[-1] = feature
    assert CycleRunner(session).run(feature) == "done"


@pytest.mark.spec("CLI-7")
def test_a_blocked_cycle_disproves_what_it_used_there(session, monkeypatch):
    recording_phases(monkeypatch, stop_at="verify")
    knowledge = session.knowledge
    knowledge.add_lessons([{"text": "Seed random generators inside tests", "phase": "tests"},
                           {"text": "Pin the toolchain versions", "phase": "toolchain"}], [])
    seed, pin = (lesson["id"] for lesson in knowledge.known_lessons())
    cycle = session.project.cycles[0]
    cycle["used"] = {f"lesson:{seed}": ["tests"], f"lesson:{pin}": ["toolchain"]}
    assert CycleRunner(session).run(cycle) == "blocked" and cycle["phase"] == "verify"
    assert [lesson["text"] for lesson in Knowledge("Python").known_lessons()] == ["Pin the toolchain versions"]


@pytest.mark.spec("CLI-7")
def test_a_web_run_past_recall_recalls_first(session, monkeypatch):
    ran = recording_phases(monkeypatch)
    recalled = []
    monkeypatch.setattr(cli.Recaller, "run", lambda self, cycle: recalled.append(cycle["phase"]))
    cycle = dict(session.project.cycles[0], phase="code")
    session.project.cycles[0] = cycle
    assert CycleRunner(session).run(cycle, web=True) == "done"
    assert recalled == ["code"] and ran[0] == "code"
    recalled.clear()
    fresh = dict(session.project.new_cycle("f1", "more"), phase="recall")
    CycleRunner(session).run(fresh, web=True)
    assert recalled == []  # at recall already: no extra recall


@pytest.mark.spec("CLI-7")
def test_every_phase_has_a_phase_class():
    for phase in set(PHASES + FIX_PHASES) - {"done"}:
        worker = CycleRunner.phase_class(phase)
        assert issubclass(worker, Phase) and worker.TITLE


@pytest.mark.spec("CLI-8")
@pytest.mark.parametrize("error,message", [(KeyboardInterrupt(), "\nInterrupted."),
                                           (OllamaError("Ollama returned HTTP 500"), "\nOllama returned HTTP 500"),
                                           (PermissionError(13, "Permission denied", "/x/y"), "\nPermission denied: /x/y")])
def test_main_ends_cleanly(monkeypatch, error, message):
    def fail(argv=None):
        raise error
    monkeypatch.setattr(Cli, "parse", staticmethod(fail))
    with pytest.raises(SystemExit) as ended:
        cli.main()
    assert ended.value.code == message


SPEC_REPLY = {
    "language": "Python",
    "criteria": [{"text": "calc.add(2, 3) is 5", "source": "a calculator that adds", "test": "test_calc.py::test_add",
                  "effort": 1},
                 {"text": "calc.sub(5, 3) is 2", "source": "and subtracts", "test": "test_calc.py::test_sub", "effort": 1},
                 {"text": 'calc.main(["add", "2", "3"]) prints 5', "source": "a calculator that adds",
                  "test": "test_calc.py::test_main", "effort": 1}],
    "files": [{"path": "calc.py", "kind": "code", "role": "the calculator and its command line", "uses": []},
              {"path": "test_calc.py", "kind": "test", "role": "its tests", "uses": ["calc.py"]}],
    "entry": "calc.py", "constraints": ["the standard library only"], "non_goals": ["a GUI"], "data": "",
    "toolchain": TOOLCHAIN}
TESTS = '''import calc


def test_add():
    assert calc.add(2, 3) == 5


def test_sub():
    assert calc.sub(5, 3) == 2


def test_main(capsys):
    calc.main(["add", "2", "3"])
    assert capsys.readouterr().out == "5\\n"
'''
CODE = '''import sys


def add(a, b):
    return a + b


def sub(a, b):
    return a + b


def main(argv):
    op, a, b = argv[0], int(argv[1]), int(argv[2])
    print(add(a, b) if op == "add" else sub(a, b))


if __name__ == "__main__":
    main(sys.argv[1:])
'''
README = {"overview": "A calculator for the command line.", "features": ["Adds two numbers", "Subtracts two numbers"],
          "requires": "Python 3",
          "usage": [{"what": "Add", "command": f'"{PY}" calc.py add 2 3', "output": "5"},
                    {"what": "Subtract", "command": f'"{PY}" calc.py sub 5 3', "output": "2"}], "serves": False}


def build(*argv):
    with pytest.raises(SystemExit) as ended:
        ProjectRun(Cli.parse(list(argv)), 0.0, datetime.datetime.now()).execute()
    return ended.value.code


@pytest.fixture
def run_ready(measured_model, fake_ollama, tmp_path, monkeypatch):
    """A machine ready for a run: the model installed, the output restored after the run, a project folder."""
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    folder = tmp_path / "calc"
    folder.mkdir()
    return folder


def printed(folder):
    return "\n".join(entry["line"] for _, entry in project_store(folder).entries("output"))


@pytest.mark.spec("CLI-9")
def test_a_task_is_built_end_to_end(run_ready, fake_ollama, home_store):
    folder = run_ready
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    fake_ollama.reply(file_block("test_calc.py", TESTS), when="Tests test_calc.py")
    fake_ollama.reply(file_block("calc.py", CODE), when="Write calc.py")
    fake_ollama.reply("CAUSE: sub added instead of subtracting\n" + file_block("calc.py", CODE.replace("a + b\n\n\ndef main",
                      "a - b\n\n\ndef main")), when="Fix the code C2")
    fake_ollama.reply(json.dumps(README), when="README")
    fake_ollama.reply(json.dumps({"lessons": [{"phase": "code", "scope": "any", "text": "Subtract with the minus operator"}],
                                  "confirmed": []}), when="Lessons")
    assert build("task", "a calculator that adds and subtracts", "--workdir", str(folder)) == 0
    labels = [label.split(" [")[0] for label in fake_ollama.labels()]
    assert labels == ["Spec (attempt 1)", "Tests test_calc.py (3)", "Write calc.py", "Fix the code C2 (no thinking first)",
                      "README (attempt 1)", "Lessons (attempt 1)"]
    assert fake_ollama.queue == [] and fake_ollama.calls[0]["think"] == "high"  # no confirm: no quick draft
    assert "return a - b" in (folder / "calc.py").read_text()
    spec = plan_of(folder)
    assert [(criterion["id"], criterion["status"]) for criterion in spec["steps"]] == [("C1", "green"), ("C2", "green"),
                                                                                       ("C3", "green")]
    assert spec["cycles"][0]["phase"] == "done" and "readme_failures" not in spec["cycles"][0]
    assert "| C2 | calc.sub(5, 3) is 2 | `test_calc.py::test_sub` | passes |" in (folder / "SPEC.md").read_text()
    assert "calc.py sub 5 3\n2\n```" in (folder / "README.md").read_text()
    run = record_of(folder)
    assert run["status"] == "done" and home_store.get("projects", run["id"]) == \
        {"id": run["id"], "workdir": str(folder), "task": "a calculator that adds and subtracts"}
    assert home_store.get("languages", "python")["projects"] == 1
    assert home_store.get("memory", "any")["lessons"][0]["text"] == "Subtract with the minus operator"
    assert "Done. All 3 spec tests pass (SPEC.md)." in printed(folder)
    assert sorted(path.name for path in folder.iterdir()) == [
        ".tinker", "README.md", "SPEC.md", "calc.py", "test_calc.py"]
    assert home_store.items("running") == {}  # its tracker is gone once it ended
    progress = [event["event"] for _, event in project_store(folder).entries("progress")]
    assert progress[0] == "project" and "points" in progress and progress[-1] == "ended"


@pytest.mark.spec("CLI-10")
def test_learn_a_command_of_yours_needs_no_model(home_store):
    Commands(parse("learn", "act", ". .venv/bin/activate")).run()
    assert home_store.get("commands", "act")["definition"] == ". .venv/bin/activate"


@pytest.mark.spec("CLI-10")
def test_learning_checks_everything_before_loading(measured_model, fake_ollama, monkeypatch, capsys):
    from tinkerkit.workspace import TaughtCommands
    monkeypatch.setattr(TaughtCommands, "alias", staticmethod(lambda name: None))
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}, {"name": "embedder"}]))
    with pytest.raises(SystemExit, match="tinker learn needs an embedding model"):
        Commands(parse("learn", "Flask")).run()
    Config.save(embed_model="embedder")
    with pytest.raises(SystemExit, match="tinker learn needs the Internet plugin"):
        Commands(parse("learn", "Flask")).run()
    Config.save(web="duckduckgo")
    monkeypatch.setattr(cli.SlotLearner, "learn", lambda self, topic, deep=False: None)
    with pytest.raises(SystemExit, match="Nothing learned about Flask; nothing saved."):
        Commands(parse("learn", "Flask")).run()
    assert fake_ollama.calls == []
    assert "Time taken: 0s" in capsys.readouterr().out  # however it ends
    assert Home.store().get("running", str(os.getpid())) is None  # its tracker left the home
    Config.save(model=None)
    with pytest.raises(SystemExit, match="No model in use"):
        Commands(parse("learn", "Flask")).run()


@pytest.mark.spec("CLI-11")
def test_memory(home_store, capsys):
    known = Knowledge("Python")
    known.remember_toolchain(cli.Toolchain(TOOLCHAIN))
    known.remember_fix({"failure": "no module named calc", "cause": "pythonpath", "replay": {"toolchain": {}}})
    known.add_lessons([{"text": "Use tmp_path in tests", "phase": "tests", "scope": "language"},
                       {"text": "Exit with 1 after an error", "phase": "code", "scope": "any"}], [])
    Library.save("flask", {"name": "Flask", "summary": "A web framework.", "learned": "2026-10-01",
                           "facts": [{"phase": "code", "text": "x"}]})
    Commands(parse("learn", "act", "echo hi")).run()
    capsys.readouterr()
    Commands.show_memory()
    out = capsys.readouterr().out
    for line in ("📘 Python · nothing built yet", "   🧰 Toolchain\n", f"      test      {TOOLCHAIN['test_all']}",
                 "   🔧 Fixes\n      • no module named calc → pythonpath (helped 1×)",
                 "   📒 Lessons (✅ proven by a build, 🌱 not yet)\n      🌱 Use tmp_path in tests",
                 "🌍 Any language\n   📒 Lessons", "      🌱 Exit with 1 after an error",
                 "🔬 Topics learned\n   • Flask: A web framework. (read 1 Oct, 1 facts)",
                 "⌨️  Your commands\n   • act: stands for `echo hi` (given)", "   Ask about it   tinker ask"):
        assert line in out, line
    assert "setup" not in out.split("🧰 Toolchain")[1].split("🔧")[0]  # a row only for a command it has


@pytest.mark.spec("CLI-11")
def test_memory_of_a_language_read_from_the_web_only(home_store, capsys):
    Library.save("go", {"name": "Go", "kind": "language", "language": "Go", "summary": "s",
                        "learned": "2026-10-22 08:53", "facts": [{"phase": "code", "text": "x"}]})
    Commands.show_memory()
    out = capsys.readouterr().out
    assert "📘 Go · nothing built yet · docs read 22 Oct (1 facts)" in out and "Topics learned" not in out


@pytest.mark.spec("CLI-11")
def test_memory_with_nothing_learned_says_how_to_start(home_store, capsys):
    Commands.show_memory()
    out = capsys.readouterr().out
    assert "🌱 Nothing yet: it learns from every build" in out and "Any language" not in out


@pytest.mark.spec("CLI-11")
def test_models(measured_model, monkeypatch, capsys):
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL, "size": 5.2e9},
                                                                          {"name": "other", "size": 1e9}]))
    Commands.list_models()
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith(f"* {MODEL}") and "5.2G" in lines[1] and "8192" in lines[1]
    assert lines[2].startswith("  other") and lines[2].endswith("not measured: tinker calibrate NAME")
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: None))
    with pytest.raises(SystemExit, match="Ollama does not answer"):
        Commands.list_models()


@pytest.mark.spec("CLI-12")
def test_a_task_waits_for_its_plan_then_confirm_builds_it(run_ready, fake_ollama, home_store):
    folder = run_ready
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    assert build("task", "a calculator that adds and subtracts", "--confirm", "--workdir", str(folder)) == 0
    out = printed(folder)
    assert "📋 Plan for calc (run " in out and "  C2  calc.sub(5, 3) is 2" in out and "Not included: a GUI" in out
    assert fake_ollama.calls[0]["think"] == "low" and not (folder / "calc.py").exists()
    run = record_of(folder)
    assert run["status"] == "awaiting" and plan_of(folder)["cycles"][0]["phase"] == "setup"
    assert f"Build it: tinker confirm @{run['id']}" in out
    fixed = CODE.replace("a + b\n\n\ndef main", "a - b\n\n\ndef main")
    fake_ollama.reply(file_block("test_calc.py", TESTS), when="Tests").reply(file_block("calc.py", fixed), when="Write")
    fake_ollama.reply(json.dumps(README), when="README")
    assert build("confirm", f"@{run['id']}") == 0
    assert "Plan confirmed: building it" in printed(folder)
    assert record_of(folder)["status"] == "done" and fake_ollama.queue == []


@pytest.mark.spec("CLI-12")
def test_amend_plans_again_and_waits_again(run_ready, fake_ollama):
    folder = run_ready
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    build("task", "a calculator that adds and subtracts", "--confirm", "--workdir", str(folder))
    more = dict(SPEC_REPLY, criteria=SPEC_REPLY["criteria"] + [{"text": "calc.mul(2, 3) is 6", "source": "adds",
                                                                "test": "test_calc.py::test_mul"}])
    fake_ollama.reply(json.dumps(more), when="Amend the spec")
    assert build("amend", "also multiply", "--workdir", str(folder)) == 0
    cycle = plan_of(folder)["cycles"][0]
    assert cycle["awaiting"] and cycle["phase"] == "setup" and cycle["amendments"] == ["also multiply"]
    assert "+ C4 calc.mul(2, 3) is 6" in printed(folder) and not (folder / "calc.py").exists()


@pytest.mark.spec("CLI-12")
@pytest.mark.parametrize("problem,said", [
    ({"unreproduced": 'the bug could not be reproduced ... then: tinker fix @abc123 "..."'},
     "Not fixed: the bug could not be reproduced"),
    ({"review_problem": "`pytest` fails"}, "Not done: the final review found `pytest` fails"),
    ({"build_problem": "it does not build"}, "Stopped before working on criteria: it does not build"),
    ({"tool_problem": "the tools do not work"}, "No code change can fix that."),
    ({}, "Stopped: 1/2 tests pass; 1 parked after fixing and a review against the plan:")])
def test_a_blocked_run_says_why_and_how_to_go_on(make_project, make_session, capsys, problem, said):
    steps = [criterion("C1", "t.py::a", "adds", status="green"),
             criterion("C2", "t.py::b", "subtracts", status="blocked", last_error="AssertionError")]
    session = make_session(make_project(steps))
    for key, value in problem.items():
        setattr(session, key, value)
    run = ProjectRun.__new__(ProjectRun)
    run.session, run.record, run.tracker = session, {"id": "abc123"}, Tracker("calc")
    run.outcome = cli.Outcome()
    run.outcome.set("blocked")
    run.print_summary()
    out = capsys.readouterr().out
    assert said in out and ("tinker resume @abc123" in out or "tinker fix @abc123" in out)
    assert run.ending["status"] == "blocked" and run.ending["headline"] in out


@pytest.mark.spec("CLI-13")
def test_the_cycle_a_run_works_on(make_project, make_session):
    def run_on(project, mode, confirm=True, **given):
        run = ProjectRun.__new__(ProjectRun)
        defaults = {"bug": False, "change_request": "keep a history"}
        run.args = argparse.Namespace(**dict(defaults, **given))
        run.session, run.record, run.mode, run.spec_text = make_session(project, confirm=confirm), project.run, mode, ""
        return run.choose_cycle("")
    project = make_project(cycles=[])
    base = run_on(project, "new")
    assert (base["id"], base["phase"], base["confirm"]) == ("base", "recall", True)
    assert run_on(project, "resume") is base
    base["phase"] = "done"
    assert run_on(project, "resume") is None
    feature = run_on(project, "change", confirm=False)
    feature["phase"] = "done"
    fix = run_on(project, "change", bug=True, change_request="2/0 crashes")
    assert (feature["kind"], feature["confirm"], fix["kind"], fix["phase"], fix["confirm"]) == \
        ("feature", False, "fix", "recall", True)
    assert [(feature["kind"], feature["request"])
            for feature in project.run["features"]] == [("feature", "keep a history"),
                                                                                              ("fix", "2/0 crashes")]
