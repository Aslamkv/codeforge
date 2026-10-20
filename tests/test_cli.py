"""CLI: tinker.py: the command line, the commands that show or change settings, a run's session and its cycle,
and a whole task built end to end (with a scripted model and real test runs)."""

import json
import sys
from pathlib import Path

import pytest

import tinker
from tinker import Cli, Commands, CycleRunner, Doctor, ProjectFolder, ProjectRun
from tinkerkit.knowledge import Knowledge
from tinkerkit.model import Models, Ollama, OllamaError
from tinkerkit.planning import Phase
from tinkerkit.project import Runs
from tinkerkit.prompts import Part, PromptTooLarge
from tinkerkit.settings import Config, FIX_PHASES, JsonFile, PHASES
from conftest import MEASURED, MODEL, PY, TOOLCHAIN, criterion, file_block, read_json


def parse(*argv):
    return Cli.parse(list(argv))


def registered(folder, run_id="abc123"):
    (folder / ".tinker").mkdir(parents=True, exist_ok=True)
    Runs.save(dict(Runs.new_record("a calculator", folder, None), id=run_id), folder)


@pytest.mark.spec("CLI-1")
def test_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = parse("task", "a", "calculator", "in", "Python", "--web", "--confirm")
    assert (args.command, args.task, args.workdir, args.web, args.confirm) == \
        ("task", "a calculator in Python", None, True, True)
    (tmp_path / "plan.md").write_text("spec")
    args = parse("task", "a calculator", "plan.md")
    assert (args.task, args.spec) == ("a calculator", "plan.md")
    assert parse("task", "notes about plan.txt").task == "notes about plan.txt"  # not a file: a word


@pytest.mark.spec("CLI-1")
@pytest.mark.parametrize("argv,why", [(["--task", "x"], "commands are written without --"), (["fly"], "no command 'fly'"),
                                      (["task", "x", "--yes"], None), (["resume", "xyz!"], None),
                                      (["status", "abc", "--workdir", "x"], None), (["rename", "bad name!"], None),
                                      (["learn", "a", "b", "c"], None), (["use-web", "bing"], None),
                                      (["resume", "abc", "--workdir", "x"], None), (["add"], None)])
def test_mistakes_are_errors(argv, why, capsys):
    with pytest.raises(SystemExit) as error:
        parse(*argv)
    if why:
        assert why in str(error.value.code)
    else:
        assert error.value.code == 2 and "error:" in capsys.readouterr().err


@pytest.mark.spec("CLI-1")
def test_runs_by_id_and_the_word_forms(tmp_path):
    registered(tmp_path / "calc")
    args = parse("confirm", "abc123")
    assert (args.command, args.resume, args.workdir) == ("resume", "abc123", ".")
    args = parse("add", "abc123", "keep", "a", "history")
    assert (args.resume, args.change_request, args.add, args.bug) == ("abc123", "keep a history", ["keep a history"], False)
    args = parse("add", "fed", "is", "not", "a", "run")  # hex, but no run starts so: words
    assert (args.resume, args.change_request) == ("", "fed is not a run")
    args = parse("fix", "2/0", "crashes")
    assert (args.bug, args.change_request, args.fix) == (True, "2/0 crashes", None)
    args = parse("amend", "abc123", "also", "divide")
    assert (args.amend, args.resume) == ("also divide", "abc123")
    assert parse("rename", "abc123", "calc").rename == ("abc123", "calc")
    assert parse("rename", "calc").rename == ("", "calc")
    assert parse("learn", "act", ". .venv/bin/activate").learn == ["act", ". .venv/bin/activate"]
    assert parse("status").status == "" and parse("runs").runs is True
    assert parse("use-web", "duckduckgo").use_web == "duckduckgo"


@pytest.mark.spec("CLI-1")
def test_clear_runs_on_its_own(tinker_home):
    JsonFile.write(tinker_home / "memory.json", {})
    with pytest.raises(SystemExit) as done:
        parse("clear", "memory", "--yes")
    assert done.value.code == 0 and not (tinker_home / "memory.json").exists()


@pytest.mark.spec("CLI-2")
def test_help_overview(capsys):
    with pytest.raises(SystemExit):
        parse("help")
    out = capsys.readouterr().out
    for lines in Cli.GROUPS.values():
        for synopsis, what in lines:
            assert synopsis.split()[0] in out and what in out
    for option in ("--spec FILE", "--web", "--plan-only", "--workdir DIR", "--yes", "--confirm"):
        assert option in out
    assert "💡 Examples" in out and "tinker help COMMAND" in out


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
    assert "resume" in Cli.options()["--workdir DIR"][1] and Cli.options()["--plan-only"][1] == ["task", "add"]


@pytest.mark.spec("CLI-3")
def test_use_model_and_use_web(tinker_home, capsys):
    with pytest.raises(SystemExit, match="has not been measured"):
        Commands.use_model("ghost")
    JsonFile.write(Models.file("m"), MEASURED)
    Commands.use_model("m")
    Commands.use_web("duckduckgo")
    saved = read_json(tinker_home / "config.json")
    assert saved["model"] == "m" and saved["web"] == "duckduckgo"
    assert "Web search: duckduckgo" in capsys.readouterr().out


@pytest.mark.spec("CLI-3")
def test_runs_and_status(tmp_path, capsys):
    for run_id, updated in (("aaa111", "2026-10-01 10:00"), ("bbb222", "2026-10-02 10:00")):
        folder = tmp_path / run_id
        registered(folder, run_id)
        record = read_json(folder / ".tinker" / "run.json")
        JsonFile.write(Path(tinker.Runs.entries.__globals__["RUNS_DIR"]) / f"{run_id}.json", dict(record, updated=updated))
    Commands.list_runs()
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("ID") and lines[1].startswith("bbb222") and lines[2].startswith("aaa111")
    folder = tmp_path / "aaa111"
    steps = [criterion("C1", "t.py::test_a", "adds", status="green"),
             criterion("C2", "t.py::test_b", "subtracts", status="blocked", last_error="AssertionError: 8 != 2")]
    JsonFile.write(folder / ".tinker" / "spec.json", {"steps": steps, "language": "Python",
                                                      "cycles": [{"id": "base", "phase": "verify"}]})
    Commands.show_status("", str(folder))
    out = capsys.readouterr().out
    assert "1/2 criteria pass" in out and "last error: AssertionError: 8 != 2" in out and "next phase verify" in out


@pytest.mark.spec("CLI-3")
def test_rename_a_folder_whose_spec_waits(tmp_path):
    folder = tmp_path / "calc-py"
    registered(folder)
    JsonFile.write(folder / ".tinker" / "spec.json", {"cycles": [{"id": "base", "phase": "design", "awaiting": True}]})
    Commands.rename("abc123", "calculator", None)
    assert not folder.exists() and (tmp_path / "calculator" / ".tinker").is_dir()
    assert Runs.find("abc123")["workdir"] == str(tmp_path / "calculator")
    (tmp_path / "taken").mkdir()
    with pytest.raises(SystemExit, match="already exists"):
        Commands.rename("abc123", "taken", None)
    JsonFile.write(tmp_path / "calculator" / ".tinker" / "spec.json", {"cycles": [{"id": "base", "phase": "done"}]})
    with pytest.raises(SystemExit, match="renames the folder of a spec that waits"):
        Commands.rename("abc123", "other", None)


@pytest.mark.spec("CLI-3")
def test_list_config(measured_model, capsys):
    Commands.list_config()
    out = capsys.readouterr().out
    for line in (f"model in use:    {MODEL} (calibrated", "input budget:    8192 tokens", "context:         32768 tokens",
                 "think:           high for planning, medium for building", "keep_alive:      0", "web:             ollama"):
        assert line in out, line


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
def test_project_folders(measured_model, fake_ollama, tmp_path, capsys):
    Config.load()["projects"] = str(tmp_path / "Projects")
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


@pytest.fixture
def session(make_project, make_session, project_dir):
    return make_session(make_project(toolchain=dict(TOOLCHAIN, setup=["echo ran >> setups.txt"],
                                                    test_all="echo run >> runs.txt")))


@pytest.mark.spec("CLI-6")
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


@pytest.mark.spec("CLI-6")
def test_the_suite_runs_once_per_state_of_the_files(session, project_dir):
    session.project.files["calc.py"] = {"kind": "code"}
    (project_dir / "calc.py").write_text("one")
    session.run_all_tests()
    session.run_all_tests()
    assert (project_dir / "runs.txt").read_text().count("run") == 1
    (project_dir / "calc.py").write_text("two")
    session.run_all_tests()
    assert (project_dir / "runs.txt").read_text().count("run") == 2


@pytest.mark.spec("CLI-6")
def test_note_ask_until_usable_and_room(session, fake_ollama, monkeypatch):
    for i in range(35):
        session.note("fix", f"problem {i}")
    assert len(session.project.spec["events"]) == 30 and session.project.spec["events"][0]["text"] == "problem 5"
    fake_ollama.reply("nope", times=3)
    with pytest.raises(SystemExit, match="Could not get a usable plan"):
        session.ask_until_usable("s", [Part("Q", "q", True)], "Plan", lambda reply: reply.json())

    def too_large(*args, **kwargs):
        raise PromptTooLarge("too big")
    monkeypatch.setattr(session.model, "ask_until_usable", too_large)
    with pytest.raises(SystemExit, match="even shortened, the prompt does not fit"):
        session.ask_until_usable("s", [], "Plan", lambda reply: reply)
    assert session.room("system") == int(session.model.limit_chars("system" + tinker.Prompts.NO_TOOL_CALLS) * 0.95)


@pytest.mark.spec("CLI-6")
def test_format_code_drops_a_formatter_that_fails(session, project_dir, capsys):
    session.project.spec["toolchain"]["format"] = "echo formatted > formatted.txt"
    session.format_code()
    assert (project_dir / "formatted.txt").exists() and session.toolchain.format
    session.project.spec["toolchain"]["format"] = "no-such-formatter-xyz ."
    session.format_code()
    assert session.toolchain.format == "" and "the formatter does not work" in capsys.readouterr().out


@pytest.mark.spec("CLI-6")
def test_knowledge_parts(session, tinker_home):
    Knowledge("Python").add_lessons([{"text": "Use tmp_path in tests", "phase": "tests", "scope": "any"}], [])
    session.project.spec["learned"] = [{"name": "Flask", "phase": "tests", "text": "use the test client", "topic": "f"},
                                       {"name": "Flask", "phase": "spec", "text": "not for tests", "topic": "f"}]
    parts = session.knowledge_parts("tests")
    assert [p.title for p in parts] == ["Lessons from earlier projects (follow them)",
                                        "Learned from the web (current; newer than what you were trained on)"]
    assert parts[0].text == "- Use tmp_path in tests" and parts[1].text == "- [Flask] use the test client"
    assert all(not p.required and p.place == "first" for p in parts)
    assert len(session.knowledge_parts("toolchain")) == 3


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
def test_cycle_runs_its_phases_in_order(session, monkeypatch):
    ran = recording_phases(monkeypatch)
    formatted = []
    monkeypatch.setattr(session, "format_code", lambda: formatted.append(session.project.cycles[0]["phase"]))
    cycle = session.project.cycles[0]
    assert CycleRunner(session).run(cycle) == "done"
    assert ran == list(PHASES[:-1]) and formatted == ["verify"] and cycle["phase"] == "done"
    assert read_json(session.project.state / "spec.json")["cycles"][0]["phase"] == "done"
    fix = session.project.new_cycle("f1", "a bug", kind="fix")
    ran.clear()
    assert CycleRunner(session).run(fix) == "done" and ran == list(FIX_PHASES[:-1])


@pytest.mark.spec("CLI-7")
def test_cycle_stops_plans_or_awaits(session, monkeypatch):
    cycle = session.project.cycles[0]
    ran = recording_phases(monkeypatch, stop_at="setup")
    assert CycleRunner(session).run(cycle) == "blocked" and cycle["phase"] == "setup"
    cycle["phase"] = "recall"
    ran.clear()
    assert CycleRunner(session).run(cycle, plan_only=True) == "planned" and ran == ["recall", "spec", "design", "review"]
    cycle.update(phase="recall", confirm=True)
    ran.clear()
    assert CycleRunner(session).run(cycle) == "awaiting" and ran == ["recall", "spec"] and cycle["awaiting"]
    cycle.update(awaiting=False, confirmed=True)
    assert CycleRunner(session).run(cycle) == "blocked" and ran[-1] == "setup"


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
        tinker.main()
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


@pytest.mark.spec("CLI-9")
def test_a_task_is_built_end_to_end(measured_model, fake_ollama, tmp_path, tinker_home, monkeypatch):
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    folder = tmp_path / "calc"
    folder.mkdir()
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    fake_ollama.reply(file_block("test_calc.py", TESTS), when="Tests test_calc.py")
    fake_ollama.reply(file_block("calc.py", CODE), when="Write calc.py")
    fake_ollama.reply("CAUSE: sub added instead of subtracting\n" + file_block("calc.py", CODE.replace("a + b\n\n\ndef main",
                      "a - b\n\n\ndef main")), when="Fix the code C2")
    fake_ollama.reply(json.dumps(README), when="README")
    fake_ollama.reply(json.dumps({"lessons": [{"phase": "code", "scope": "any", "text": "Subtract with the minus operator"}],
                                  "confirmed": []}), when="Lessons")
    args = Cli.parse(["task", "a calculator that adds and subtracts", "--workdir", str(folder)])
    with pytest.raises(SystemExit) as ended:
        ProjectRun(args, 0.0, __import__("datetime").datetime.now()).execute()
    assert ended.value.code == 0
    labels = [label.split(" [")[0] for label in fake_ollama.labels()]
    assert labels == ["Spec (attempt 1)", "Tests test_calc.py (3)", "Write calc.py", "Fix the code C2 (no thinking first)",
                      "README (attempt 1)", "Lessons (attempt 1)"]
    assert fake_ollama.queue == []
    assert "return a - b" in (folder / "calc.py").read_text()
    spec = read_json(folder / ".tinker" / "spec.json")
    assert [(s["id"], s["status"]) for s in spec["steps"]] == [("C1", "green"), ("C2", "green"), ("C3", "green")]
    assert spec["cycles"][0]["phase"] == "done" and "readme_failures" not in spec["cycles"][0]
    spec_md = (folder / "SPEC.md").read_text()
    assert "| C2 | calc.sub(5, 3) is 2 | `test_calc.py::test_sub` | passes |" in spec_md
    readme = (folder / "README.md").read_text()
    assert "## Usage" in readme and "calc.py sub 5 3\n2\n```" in readme
    run = read_json(folder / ".tinker" / "run.json")
    assert run["status"] == "done" and read_json(tinker_home / "runs" / f"{run['id']}.json")["status"] == "done"
    assert read_json(tinker_home / "languages" / "python.json")["projects"] == 1
    assert read_json(tinker_home / "memory.json")["lessons"][0]["text"] == "Subtract with the minus operator"
    log = (folder / "current.log").read_text()
    assert "Done. All 3 spec tests pass (SPEC.md)." in log


@pytest.mark.spec("CLI-10")
def test_learn_a_command_of_yours_needs_no_model(tinker_home):
    Commands(parse("learn", "act", ". .venv/bin/activate")).run()
    assert read_json(tinker_home / "commands.json")["commands"]["act"]["definition"] == ". .venv/bin/activate"


@pytest.mark.spec("CLI-10")
def test_learning_checks_everything_before_loading(measured_model, fake_ollama, monkeypatch):
    from tinkerkit.workspace import TaughtCommands
    monkeypatch.setattr(TaughtCommands, "alias", staticmethod(lambda name: None))
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}, {"name": "embedder"}]))
    with pytest.raises(SystemExit, match="tinker learn needs an embedding model"):
        Commands(parse("learn", "Flask")).run()
    Config.load()["embed_model"] = "embedder"
    with pytest.raises(SystemExit, match="tinker learn needs the Internet plugin"):
        Commands(parse("learn", "Flask")).run()
    Config.load()["web"] = "duckduckgo"
    monkeypatch.setattr(tinker.Learner, "learn", lambda self, topic, deep=False: None)
    with pytest.raises(SystemExit, match="Nothing learned about Flask; nothing saved."):
        Commands(parse("learn", "Flask")).run()
    assert fake_ollama.calls == []
    Config.load()["model"] = None
    with pytest.raises(SystemExit, match="No model in use"):
        Commands(parse("learn", "Flask")).run()


@pytest.mark.spec("CLI-11")
def test_memory(tinker_home, capsys):
    from tinkerkit.knowledge import Library
    known = Knowledge("Python")
    known.remember_toolchain(tinker.Toolchain(TOOLCHAIN))
    known.remember_fix({"failure": "no module named calc", "cause": "pythonpath", "replay": {"toolchain": {}}})
    known.add_lessons([{"text": "Use tmp_path in tests", "phase": "tests", "scope": "language"},
                       {"text": "Exit with 1 after an error", "phase": "code", "scope": "any"}], [])
    Library.save("flask", {"name": "Flask", "summary": "A web framework.", "learned": "2026-10-01", "facts": [{}]})
    Commands(parse("learn", "act", "echo hi")).run()
    capsys.readouterr()
    Commands.show_memory()
    out = capsys.readouterr().out
    for line in ("Python (python.json), 0 project(s) finished", "  toolchain: setup -", "  fix: no module named calc -> "
                 "pythonpath (helped 1, failed 0; replayed without the model)", "  lesson [tests, seen 1x]: Use tmp_path",
                 "Any language (memory.json):\n  lesson [code, seen 1x]: Exit with 1", "  flask (topic): Flask, learned "
                 "2026-10-01, 1 fact(s): A web framework.", "  act: stands for `echo hi` (given)"):
        assert line in out, line


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


def build(args, folder):
    with pytest.raises(SystemExit) as ended:
        ProjectRun(Cli.parse(args), 0.0, __import__("datetime").datetime.now()).execute()
    return ended.value.code


@pytest.mark.spec("CLI-12")
def test_a_confirmed_task_waits_for_its_spec_then_resume_builds_it(measured_model, fake_ollama, tmp_path, monkeypatch):
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    folder = tmp_path / "calc"
    folder.mkdir()
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    assert build(["task", "a calculator that adds and subtracts", "--confirm", "--workdir", str(folder)], folder) == 0
    out = (folder / "current.log").read_text()  # everything the run printed
    assert "📋 Spec for calc (run " in out and "  C2  calc.sub(5, 3) is 2" in out and "Not included: a GUI" in out
    assert fake_ollama.calls[0]["think"] == "low" and not (folder / "calc.py").exists()
    assert read_json(folder / ".tinker" / "run.json")["status"] == "awaiting"
    fixed = CODE.replace("a + b\n\n\ndef main", "a - b\n\n\ndef main")
    fake_ollama.reply(file_block("test_calc.py", TESTS), when="Tests").reply(file_block("calc.py", fixed), when="Write")
    fake_ollama.reply(json.dumps(README), when="README")
    assert build(["resume", "--workdir", str(folder)], folder) == 0
    assert "Spec confirmed: building it" in (folder / "current.log").read_text()
    assert read_json(folder / ".tinker" / "run.json")["status"] == "done" and fake_ollama.queue == []


@pytest.mark.spec("CLI-12")
@pytest.mark.parametrize("problem,said", [
    ({"unreproduced": 'the bug could not be reproduced ... then: tinker fix abc123 "..."'},
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
    run.session, run.record = session, {"id": "abc123"}
    run.outcome = tinker.Outcome()
    run.outcome.set("blocked")
    run.print_summary()
    out = capsys.readouterr().out
    assert said in out and ("tinker resume abc123" in out or "tinker fix abc123" in out)


@pytest.mark.spec("CLI-13")
def test_the_cycle_a_run_works_on(make_project, make_session):
    def run_on(project, mode, **given):
        run = ProjectRun.__new__(ProjectRun)
        defaults = {"bug": False, "change_request": "keep a history", "confirm": False}
        run.args = __import__("argparse").Namespace(**dict(defaults, **given))
        run.session, run.record, run.mode, run.spec_text = make_session(project), project.run, mode, ""
        return run.choose_cycle("")
    project = make_project(cycles=[])
    base = run_on(project, "new", confirm=True)
    assert (base["id"], base["phase"], base["confirm"]) == ("base", "recall", True)
    assert run_on(project, "resume") is base
    base["phase"] = "done"
    assert run_on(project, "resume") is None
    feature = run_on(project, "change")
    fix = (feature.update(phase="done"), run_on(project, "change", bug=True, change_request="2/0 crashes"))[1]
    assert (feature["kind"], feature["request"], fix["kind"], fix["phase"]) == ("feature", "keep a history", "fix", "recall")
    assert [(f["kind"], f["request"]) for f in project.run["features"]] == [("feature", "keep a history"),
                                                                            ("fix", "2/0 crashes")]
