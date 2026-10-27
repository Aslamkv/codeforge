import argparse
import datetime
import json
import signal
import sys

import pytest
from conftest import MEASURED, MODEL, PY, TOOLCHAIN, criterion, file_block, plan_of, record_of

from tinkerkit import cli
from tinkerkit.cli import Cli, Commands, CycleRunner, Doctor, ProjectFolder, ProjectRun
from tinkerkit.commands import kind_of
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.model import Models, Ollama, OllamaError
from tinkerkit.project import Runs, Tracker, project_store
from tinkerkit.session import Phase, Stopped
from tinkerkit.settings import FIX_PHASES, KEEP_LOADED_ENV, PHASES, Config
from tinkerkit.workspace import Toolchain


def parse(*argv):
    return Cli.parse(list(argv))


def registered(folder, run_id="abc123", **record):
    folder.mkdir(parents=True, exist_ok=True)
    Runs.save(dict(Runs.new_record("a calculator", folder, None), id=run_id, **record), folder)


def bare_run(**attributes):
    run = ProjectRun.__new__(ProjectRun)
    vars(run).update(attributes)
    return run


@pytest.mark.spec("CLI-1")
def test_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = parse("task", "a", "calculator", "in", "Python", "--web", "--confirm")
    assert (args.command, args.task, args.workdir, args.web, args.confirm) == (
        "task",
        "a calculator in Python",
        None,
        True,
        True,
    )
    assert parse("task", "x").confirm is False
    (tmp_path / "plan.md").write_text("spec")
    args = parse("task", "a calculator", "plan.md")
    assert (args.task, args.spec) == ("a calculator", "plan.md")
    not_a_file = "notes about plan.txt"
    assert parse("task", not_a_file).task == not_a_file


@pytest.mark.spec("CLI-1")
def test_a_learn_is_one_topic_or_a_command_of_yours():
    assert parse("learn", "food", "bank", "inventory").learn == ["food bank inventory"]
    assert parse("learn", "Python", "3.14").learn == ["Python 3.14"]
    assert parse("learn", "gs", "=", "git", "status").learn == ["gs", "git status"]
    assert parse("learn", "serve", "python3 -m http.server").learn == ["serve", "python3 -m http.server"]


@pytest.mark.spec("CLI-1")
@pytest.mark.parametrize(
    "argv,why",
    [
        (["fly"], "no command 'fly'"),
        (["task", "x", "--yes"], None),
        (["resume", "abc123"], None),
        (["status", "@abc", "--workdir", "x"], None),
        (["rename", "bad name!"], None),
        (["learn", " "], None),
        (["resume", "@abc", "--workdir", "x"], None),
        (["add"], None),
        (["config", "only-a-key"], None),
    ],
)
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
    assert (args.resume, args.change_request, args.add, args.bug) == (
        "calc",
        "keep a history",
        ["keep a history"],
        False,
    )
    registered(tmp_path / "add", "add123")
    names_a_project_too = "add"
    args = parse("add", names_a_project_too, "a", "history")
    assert (args.resume, args.change_request) == ("", "add a history")
    args = parse("fix", "100", "divided", "by", "0", "crashes")
    assert (args.bug, args.change_request, args.fix, args.resume) == (
        True,
        "100 divided by 0 crashes",
        None,
        "",
    )
    args = parse("amend", "@abc123", "also", "divide")
    assert (args.amend, args.resume) == ("also divide", "abc123")
    with pytest.raises(SystemExit):
        parse("amend", "@abc123", "also", "divide", "--confirm")
    assert parse("rename", "@abc123", "calc").rename == ("abc123", "calc")
    assert parse("rename", "calc").rename == ("", "calc")
    assert (
        parse("status").status == ""
        and parse("status", "@calc").status == "calc"
        and parse("runs").runs is True
    )
    assert parse("log", "@calc", "--calls").log == "calc" and parse("log").calls is False
    assert parse("config").config == [] and parse("config", "test_timeout", "90").config == [
        "test_timeout",
        "90",
    ]


@pytest.mark.spec("SET-10")
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
        parse("help", "chat")
    assert capsys.readouterr().out.startswith("usage: tinker chat MESSAGE")
    with pytest.raises(SystemExit, match="no command 'fly'"):
        parse("help", "fly")
    assert "resume" in Cli.options()["--workdir DIR"][1] and "fix" in Cli.options()["--confirm"][1]


@pytest.mark.spec("CLI-3")
def test_use_model(home_store, capsys):
    with pytest.raises(SystemExit, match="has not been measured"):
        Commands.use_model("ghost")
    Models.keep("m", MEASURED)
    Commands.use_model("m")
    assert Config.get("model") == "m" and "Model in use: m" in capsys.readouterr().out


def picker(monkeypatch, chosen, terminal=True, embedding=()):
    shown = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: terminal)
    monkeypatch.setattr(cli.say, "pick", lambda choices, start: shown.append((choices, start)) or chosen)
    monkeypatch.setattr(
        Ollama,
        "capabilities",
        staticmethod(lambda name: ["embedding" if name in embedding else "completion"]),
    )
    monkeypatch.setattr(
        Ollama, "installed_models", staticmethod(lambda: [{"name": "other"}, {"name": "m"}, {"name": "n"}])
    )
    return shown


@pytest.mark.spec("CLI-3")
def test_use_model_without_a_name_picks_one_on_a_terminal(home_store, monkeypatch, capsys):
    assert parse("use-model").use_model is None
    Models.keep("m", MEASURED)
    Models.keep("n", MEASURED)
    Config.save(model="n")
    shown = picker(monkeypatch, 0, embedding=("other",))
    Commands.use_model(None)
    assert shown == [([("m", False), ("n", False)], 1)]
    assert Config.get("model") == "m" and "Model in use: m" in capsys.readouterr().out
    picker(monkeypatch, None)
    Commands.use_model(None)
    assert Config.get("model") == "m" and capsys.readouterr().out == ""
    picker(monkeypatch, 0)
    with pytest.raises(SystemExit, match="other has not been measured"):
        Commands.use_model(None)


@pytest.mark.spec("CLI-3")
def test_use_model_without_a_name_lists_the_models_off_a_terminal(home_store, monkeypatch, capsys):
    shown = picker(monkeypatch, 1, terminal=False)
    Commands.use_model(None)
    assert shown == [] and capsys.readouterr().out.splitlines()[1].startswith("  other")
    shown = picker(monkeypatch, 1)
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: []))
    Commands.use_model(None)
    assert shown == [] and "MODEL" in capsys.readouterr().out


@pytest.mark.spec("CLI-3")
def test_calibrate_without_a_name_picks_one_before_its_job_starts(home_store, monkeypatch, capsys):
    assert Cli.named(["calibrate", "m"]) == ["calibrate", "m"] and Cli.named(["runs"]) == ["runs"]
    assert kind_of(["calibrate"]) == "glance" and kind_of(["calibrate", "m"]) == "job"
    Models.keep("m", MEASURED)
    Config.save(model="m")
    shown = picker(monkeypatch, 0)
    assert Cli.named(["calibrate"]) == ["calibrate", "other"]
    assert shown == [([("other", False), ("m", False), ("n", False)], 1)]
    picker(monkeypatch, None)
    with pytest.raises(SystemExit) as cancelled:
        Cli.named(["calibrate"])
    assert not cancelled.value.code and capsys.readouterr().out == ""
    shown = picker(monkeypatch, 0, terminal=False)
    with pytest.raises(SystemExit) as listed:
        Cli.named(["calibrate"])
    assert not listed.value.code and shown == [] and "MODEL" in capsys.readouterr().out
    Commands(parse("calibrate")).run()
    assert "MODEL" in capsys.readouterr().out


@pytest.mark.spec("CLI-3")
def test_embed_without_a_name_picks_one_that_embeds(home_store, monkeypatch, capsys):
    assert parse("embed").embed is None
    Config.save(embed_model="n")
    monkeypatch.setattr(cli.Embeddings, "vectors", lambda self, texts, keep=True: [[1.0]])
    shown = picker(monkeypatch, 0, embedding=("m", "n"))
    Commands.embed(None)
    assert shown == [([("m", False), ("n", False)], 1)]
    assert Config.get("embed_model") == "m" and "Embedding model: m" in capsys.readouterr().out
    picker(monkeypatch, None, embedding=("m", "n"))
    Commands.embed(None)
    assert Config.get("embed_model") == "m" and capsys.readouterr().out == ""
    shown = picker(monkeypatch, 0, terminal=False, embedding=("m", "n"))
    Commands.embed(None)
    assert shown == [] and "MODEL" in capsys.readouterr().out


@pytest.mark.spec("CLI-3")
def test_runs_and_status(tmp_path, capsys):
    registered(tmp_path / "older", "aaa111")
    registered(tmp_path / "newer", "bbb222")
    for folder, updated in (("older", "2026-10-01 10:00"), ("newer", "2026-10-02 10:00")):
        store = project_store(tmp_path / folder)
        store.put("run", "record", dict(store.get("run", "record"), updated=updated))
    Commands.list_runs()
    lines = capsys.readouterr().out.splitlines()
    assert (
        lines[0].startswith("PROJECT") and lines[1].startswith("@bbb222") and lines[2].startswith("@aaa111")
    )
    steps = [
        criterion("C1", "t.py::test_a", "adds", status="green"),
        criterion("C2", "t.py::test_b", "subtracts", status="blocked", last_error="AssertionError: 8 != 2"),
    ]
    project_store(tmp_path / "older").put(
        "spec",
        "plan",
        {
            "steps": steps,
            "language": "Python",
            "cycles": [{"id": "base", "phase": "setup", "awaiting": True}],
        },
    )
    Commands.show_status("older", None)
    out = capsys.readouterr().out
    assert "1/2 criteria pass" in out and "last error: AssertionError: 8 != 2" in out
    assert "project: next phase setup (its plan waits for your confirm)" in out


@pytest.mark.spec("CLI-3")
def test_rename_a_folder_whose_plan_waits(tmp_path):
    folder = tmp_path / "calc-py"
    registered(folder)
    project_store(folder).put(
        "spec", "plan", {"cycles": [{"id": "base", "phase": "setup", "awaiting": True}]}
    )
    Commands.rename("abc123", "calculator", None)
    assert not folder.exists() and (tmp_path / "calculator" / ".tinker" / "project.db").is_file()
    assert (
        Runs.find("abc123")["workdir"] == str(tmp_path / "calculator")
        and Runs.find("calculator")["id"] == "abc123"
    )
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
    for line in (
        f"model in use:    {MODEL} (calibrated",
        "input budget:    8192 tokens",
        "context:         32768 tokens",
        "think:           high for planning, medium for building",
        "keep_alive:      0",
        "test_timeout:    60",
        "web:             searches mwmbl, wikipedia, wikibooks",
    ):
        assert line in out, line
    Commands(parse("config", "test_timeout", "90")).run()
    assert Config.get("test_timeout") == 90 and "test_timeout is 90 now" in capsys.readouterr().out


@pytest.mark.spec("CLI-4")
def test_doctor(monkeypatch, capsys):
    assert [name for _, name, _ in Doctor().checks()] == [
        "Ollama",
        "model",
        "embedding model (for tinker learn and --web)",
        "web search",
        "tinker command",
    ]
    assert Doctor.web() == "searches mwmbl, wikipedia, wikibooks"
    with pytest.raises(SystemExit, match="does not answer"):
        Doctor.ollama()
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


@pytest.mark.spec("CLI-8")
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
    Config.save(**{"model": None})
    assert ProjectFolder.named("x") == ""


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
    assert (
        '] Spec (attempt 1)\n    {"language": "Python"}' in out
        and "Write calc.py  error: runner failed" in out
    )


@pytest.mark.spec("CLI-11")
def test_each_build_step_says_what_came_of_it(make_project, make_session):
    project = make_project([criterion("C1", "t.py::a", status="green"), criterion("C2", "t.py::b")])
    runner, cycle = (
        CycleRunner(make_session(project)),
        {"id": "base", "files": ["calc.py", "t.py"], "code_done": ["calc.py"]},
    )
    assert [
        runner.outcome(phase, cycle) for phase in ("spec", "design", "tests", "code", "verify", "tidy")
    ] == [
        "2 point(s) to test",
        "2 file(s)",
        "0/2 test(s) written",
        "1 file(s) written",
        "1/2 spec points pass",
        "",
    ]


def recording_phases(monkeypatch, stop_at=None):
    ran = []

    class Recorded(Phase):
        TITLE, BUILDS = "", False

        def run(self, cycle):
            ran.append(cycle["phase"])
            return False if cycle["phase"] == stop_at else None

    monkeypatch.setattr(CycleRunner, "phase_class", staticmethod(lambda phase: Recorded))
    return ran


@pytest.mark.spec("CLI-11")
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


@pytest.mark.spec("CLI-11")
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


@pytest.mark.spec("CLI-11")
def test_a_blocked_cycle_disproves_what_it_used_there(session, monkeypatch):
    recording_phases(monkeypatch, stop_at="verify")
    knowledge = session.knowledge
    knowledge.add_lessons(
        [
            {"text": "Seed random generators inside tests", "phase": "tests"},
            {"text": "Pin the toolchain versions", "phase": "toolchain"},
        ],
        [],
    )
    seed, pin = (lesson["id"] for lesson in knowledge.known_lessons())
    cycle = session.project.cycles[0]
    cycle["used"] = {f"lesson:{seed}": ["tests"], f"lesson:{pin}": ["toolchain"]}
    assert CycleRunner(session).run(cycle) == "blocked" and cycle["phase"] == "verify"
    assert [lesson["text"] for lesson in Knowledge("Python").known_lessons()] == [
        "Pin the toolchain versions"
    ]


@pytest.mark.spec("CLI-11")
def test_a_web_run_past_recall_recalls_first(session, monkeypatch):
    ran = recording_phases(monkeypatch)
    recalled = []
    monkeypatch.setattr(cli.Recaller, "run", lambda self, cycle: recalled.append(cycle["phase"]))
    cycle = dict(session.project.cycles[0], phase="code")
    session.project.cycles[0] = cycle
    assert CycleRunner(session).run(cycle, web=True) == "done"
    assert recalled == ["code"] and ran[0] == "code"
    recalled.clear()
    at_recall_already = dict(session.project.new_cycle("f1", "more"), phase="recall")
    CycleRunner(session).run(at_recall_already, web=True)
    assert recalled == []


@pytest.mark.spec("CLI-11")
def test_every_phase_has_a_phase_class():
    for phase in set(PHASES + FIX_PHASES) - {"done"}:
        worker = CycleRunner.phase_class(phase)
        assert issubclass(worker, Phase) and worker.TITLE


@pytest.mark.spec("CLI-12")
@pytest.mark.parametrize(
    "error,message",
    [
        (KeyboardInterrupt(), "\nInterrupted."),
        (OllamaError("Ollama returned HTTP 500"), "\nOllama returned HTTP 500"),
        (PermissionError(13, "Permission denied", "/x/y"), "\nPermission denied: /x/y"),
    ],
)
def test_main_ends_cleanly(monkeypatch, error, message):
    def fail(argv=None):
        raise error

    monkeypatch.setattr(Cli, "parse", staticmethod(fail))
    with pytest.raises(SystemExit) as ended:
        cli.main()
    assert ended.value.code == message


@pytest.mark.spec("CLI-12")
@pytest.mark.parametrize("ending", [KeyboardInterrupt, signal.SIGTERM, signal.SIGHUP])
def test_main_unloads_every_kept_model_however_it_ends(monkeypatch, fake_ollama, ending):
    def end(argv=None):
        Ollama.kept.update({"a", "b"})
        if ending is KeyboardInterrupt:
            raise ending
        signal.raise_signal(ending)

    monkeypatch.setattr(Cli, "parse", staticmethod(end))
    with pytest.raises(SystemExit) as ended:
        cli.main()
    assert ended.value.code == "\nInterrupted."
    assert sorted(fake_ollama.unloaded) == ["a", "b"]


@pytest.mark.spec("CLI-12")
def test_main_unloads_nothing_it_did_not_keep(monkeypatch, fake_ollama):
    monkeypatch.setattr(Cli, "parse", staticmethod(lambda argv=None: argparse.Namespace()))
    monkeypatch.setattr(cli, "run", lambda *args: None)
    cli.main()
    assert fake_ollama.unloaded == []


@pytest.mark.spec("CLI-12")
def test_a_job_a_chat_started_leaves_its_models_loaded(monkeypatch, fake_ollama):
    def end(argv=None):
        Ollama.kept.update({"a", "b"})
        raise KeyboardInterrupt

    monkeypatch.setenv(KEEP_LOADED_ENV, "1")
    monkeypatch.setattr(Cli, "parse", staticmethod(end))
    with pytest.raises(SystemExit):
        cli.main()
    assert fake_ollama.unloaded == []
    Ollama.kept.clear()


@pytest.mark.spec("CLI-12")
def test_a_job_never_unloads_its_model_when_it_ends(monkeypatch, fake_ollama):
    class Ended:
        def end(self, ended, took):
            pass

    class Kept:
        name = "m"

        def unload(self):
            raise AssertionError("the job unloaded its model")

    monkeypatch.setattr(Commands, "followed", staticmethod(lambda title: Ended()))
    monkeypatch.setattr(Models, "remember", staticmethod(lambda model: None))
    Commands.reported("Asking", Kept(), lambda tracker: cli.ending("done", "asked"))
    assert fake_ollama.unloaded == []


SPEC_REPLY = {
    "language": "Python",
    "criteria": [
        {
            "text": "calc.add(2, 3) is 5",
            "source": "a calculator that adds",
            "test": "test_calc.py::test_add",
            "effort": 1,
        },
        {
            "text": "calc.sub(5, 3) is 2",
            "source": "and subtracts",
            "test": "test_calc.py::test_sub",
            "effort": 1,
        },
        {
            "text": 'calc.main(["add", "2", "3"]) prints 5',
            "source": "a calculator that adds",
            "test": "test_calc.py::test_main",
            "effort": 1,
        },
    ],
    "files": [
        {"path": "calc.py", "kind": "code", "role": "the calculator and its command line", "uses": []},
        {"path": "test_calc.py", "kind": "test", "role": "its tests", "uses": ["calc.py"]},
    ],
    "entry": "calc.py",
    "constraints": ["the standard library only"],
    "non_goals": ["a GUI"],
    "data": "",
    "toolchain": TOOLCHAIN,
}
TESTS = """import calc


def test_add():
    assert calc.add(2, 3) == 5


def test_sub():
    assert calc.sub(5, 3) == 2


def test_main(capsys):
    calc.main(["add", "2", "3"])
    assert capsys.readouterr().out == "5\\n"
"""
CODE = """import sys


def add(a, b):
    return a + b


def sub(a, b):
    return a + b


def main(argv):
    op, a, b = argv[0], int(argv[1]), int(argv[2])
    print(add(a, b) if op == "add" else sub(a, b))


if __name__ == "__main__":
    main(sys.argv[1:])
"""
README = {
    "overview": "A calculator for the command line.",
    "features": ["Adds two numbers", "Subtracts two numbers"],
    "requires": "Python 3",
    "usage": [
        {"what": "Add", "command": f'"{PY}" calc.py add 2 3', "output": "5"},
        {"what": "Subtract", "command": f'"{PY}" calc.py sub 5 3', "output": "2"},
    ],
    "serves": False,
}


def build(*argv):
    with pytest.raises(SystemExit) as ended:
        ProjectRun(Cli.parse(list(argv)), 0.0, datetime.datetime.now()).execute()
    return ended.value.code


@pytest.fixture
def run_ready(measured_model, fake_ollama, tmp_path, monkeypatch):
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    folder = tmp_path / "calc"
    folder.mkdir()
    return folder


def printed(folder):
    return "\n".join(entry["line"] for _, entry in project_store(folder).entries("output"))


@pytest.mark.spec("CLI-13")
def test_a_task_is_built_end_to_end(run_ready, fake_ollama, home_store):
    folder = run_ready
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    fake_ollama.reply(file_block("test_calc.py", TESTS), when="Tests test_calc.py")
    fake_ollama.reply(file_block("calc.py", CODE), when="Write calc.py")
    fake_ollama.reply(
        "CAUSE: sub added instead of subtracting\n"
        + file_block("calc.py", CODE.replace("a + b\n\n\ndef main", "a - b\n\n\ndef main")),
        when="Fix the code C2",
    )
    fake_ollama.reply(json.dumps(README), when="README")
    fake_ollama.reply(
        json.dumps(
            {
                "lessons": [{"phase": "code", "scope": "any", "text": "Subtract with the minus operator"}],
                "confirmed": [],
            }
        ),
        when="Lessons",
    )
    assert build("task", "a calculator that adds and subtracts", "--workdir", str(folder)) == 0
    labels = [label.split(" [")[0] for label in fake_ollama.labels()]
    assert labels == [
        "Spec (attempt 1)",
        "Tests test_calc.py (3)",
        "Write calc.py",
        "Fix the code C2 (no thinking first)",
        "README (attempt 1)",
        "Lessons (attempt 1)",
    ]
    no_quick_draft = fake_ollama.calls[0]["think"] == "high"
    assert fake_ollama.queue == [] and no_quick_draft
    assert "return a - b" in (folder / "calc.py").read_text()
    spec = plan_of(folder)
    assert [(criterion["id"], criterion["status"]) for criterion in spec["steps"]] == [
        ("C1", "green"),
        ("C2", "green"),
        ("C3", "green"),
    ]
    assert spec["cycles"][0]["phase"] == "done" and "readme_failures" not in spec["cycles"][0]
    assert (
        "| C2 | calc.sub(5, 3) is 2 | `test_calc.py::test_sub` | passes |" in (folder / "SPEC.md").read_text()
    )
    assert "calc.py sub 5 3\n2\n```" in (folder / "README.md").read_text()
    run = record_of(folder)
    assert run["status"] == "done" and home_store.get("projects", run["id"]) == {
        "id": run["id"],
        "workdir": str(folder),
        "task": "a calculator that adds and subtracts",
    }
    assert home_store.get("languages", "python")["projects"] == 1
    assert home_store.get("memory", "any")["lessons"][0]["text"] == "Subtract with the minus operator"
    assert "Done. All 3 spec tests pass (SPEC.md)." in printed(folder)
    assert sorted(path.name for path in folder.iterdir()) == [
        ".tinker",
        "README.md",
        "SPEC.md",
        "calc.py",
        "test_calc.py",
    ]
    progress = [event["event"] for _, event in project_store(folder).entries("progress")]
    assert progress[0] == "project" and "points" in progress and progress[-1] == "ended"


@pytest.mark.spec("CLI-7")
def test_learn_a_command_of_yours_needs_no_model(home_store):
    Commands(parse("learn", "act", ". .venv/bin/activate")).run()
    assert home_store.get("commands", "act")["definition"] == ". .venv/bin/activate"


@pytest.mark.spec("CLI-7")
@pytest.mark.parametrize(
    "words,finder,nothing",
    [
        (["learn", "Flask"], ("SlotLearner", "learn"), "Nothing learned about Flask; nothing saved."),
        (
            ["research", "food", "banks"],
            ("Researcher", "research"),
            "Nothing found about food banks; nothing saved.",
        ),
    ],
)
def test_learning_checks_everything_before_loading_and_finding_nothing_is_an_error(
    measured_model, fake_ollama, monkeypatch, capsys, words, finder, nothing
):
    monkeypatch.setattr(cli.TaughtCommands, "alias", staticmethod(lambda name: None))
    monkeypatch.setattr(
        Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}, {"name": "embedder"}])
    )
    with pytest.raises(SystemExit, match=f"tinker {words[0]} needs an embedding model"):
        Commands(parse(*words)).run()
    Config.save(embed_model="embedder")
    finder_class, method = finder
    monkeypatch.setattr(getattr(cli, finder_class), method, lambda self, *args, **kwargs: None)
    with pytest.raises(SystemExit, match=nothing):
        Commands(parse(*words)).run()
    assert fake_ollama.calls == []
    assert "Time taken: 0s" in capsys.readouterr().out
    Config.save(model=None)
    with pytest.raises(SystemExit, match="No model in use"):
        Commands(parse(*words)).run()


@pytest.mark.spec("CLI-5", "GRF-8", "GRF-9")
def test_memory_browses_what_tinker_learned(home_store, capsys):
    known = Knowledge("Python")
    known.remember_toolchain(Toolchain(TOOLCHAIN))
    known.add_lessons([{"text": "Use tmp_path in tests", "phase": "tests", "scope": "language"}], [])
    Library.save(
        "go",
        {
            "name": "Go",
            "kind": "language",
            "language": "Go",
            "summary": "A compiled language.",
            "learned": "2026-10-22 08:53",
            "facts": [{"phase": "code", "text": "Run go test ./..."}],
        },
    )
    Commands(parse("memory")).run()
    out = capsys.readouterr().out
    assert "📘 Python (memory python): 1 toolchain, 1 lesson; 1 proven · nothing built yet" in out
    assert "📘 Go (memory go): 1 fact; 0 proven · nothing built yet" in out
    Commands(parse("memory", "go", "facts")).run()
    out = capsys.readouterr().out
    assert "A compiled language.\nlearned: 2026-10-22 08:53\nnothing built yet" in out
    assert "] 🌱 fact      Run go test ./..." in out


@pytest.mark.spec("CLI-5", "GRF-8")
def test_memory_with_nothing_learned_says_how_to_start(home_store, capsys):
    Commands(parse("memory")).run()
    assert "🌱 Nothing yet: it learns from every build" in capsys.readouterr().out


@pytest.mark.spec("CLI-5", "GRF-9")
def test_memory_refuses_words_it_cannot_read(home_store, capsys):
    for wrong in (["show"], ["lessons", "facts"], ["python", "0"]):
        with pytest.raises(SystemExit):
            parse("memory", *wrong)
        assert "tinker memory NAME [KIND] [PAGE]" in capsys.readouterr().err


@pytest.mark.spec("CLI-5")
def test_models(measured_model, monkeypatch, capsys):
    monkeypatch.setattr(
        Ollama,
        "installed_models",
        staticmethod(lambda: [{"name": MODEL, "size": 5.2e9}, {"name": "other", "size": 1e9}]),
    )
    Commands.list_models()
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith(f"* {MODEL}") and "5.2G" in lines[1] and "8192" in lines[1]
    assert lines[2].startswith("  other") and lines[2].endswith("not measured: tinker calibrate NAME")
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: None))
    with pytest.raises(SystemExit, match="Ollama does not answer"):
        Commands.list_models()


@pytest.mark.spec("CLI-10")
def test_a_task_waits_for_its_plan_then_confirm_builds_it(run_ready, fake_ollama, home_store):
    folder = run_ready
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    assert build("task", "a calculator that adds and subtracts", "--confirm", "--workdir", str(folder)) == 0
    out = printed(folder)
    assert (
        "📋 Plan for calc (run " in out
        and "  C2  calc.sub(5, 3) is 2" in out
        and "Not included: a GUI" in out
    )
    assert fake_ollama.calls[0]["think"] == "low" and not (folder / "calc.py").exists()
    run = record_of(folder)
    assert run["status"] == "awaiting" and plan_of(folder)["cycles"][0]["phase"] == "setup"
    assert f"Build it: tinker confirm @{run['id']}" in out
    fixed = CODE.replace("a + b\n\n\ndef main", "a - b\n\n\ndef main")
    fake_ollama.reply(file_block("test_calc.py", TESTS), when="Tests").reply(
        file_block("calc.py", fixed), when="Write"
    )
    fake_ollama.reply(json.dumps(README), when="README")
    assert build("confirm", f"@{run['id']}") == 0
    assert "Plan confirmed: building it" in printed(folder)
    assert record_of(folder)["status"] == "done" and fake_ollama.queue == []


@pytest.mark.spec("CLI-10")
def test_amend_plans_again_and_waits_again(run_ready, fake_ollama):
    folder = run_ready
    fake_ollama.reply(json.dumps(SPEC_REPLY), when="Spec")
    build("task", "a calculator that adds and subtracts", "--confirm", "--workdir", str(folder))
    more = dict(
        SPEC_REPLY,
        criteria=SPEC_REPLY["criteria"]
        + [{"text": "calc.mul(2, 3) is 6", "source": "adds", "test": "test_calc.py::test_mul"}],
    )
    fake_ollama.reply(json.dumps(more), when="Amend the spec")
    assert build("amend", "also multiply", "--workdir", str(folder)) == 0
    cycle = plan_of(folder)["cycles"][0]
    assert cycle["awaiting"] and cycle["phase"] == "setup" and cycle["amendments"] == ["also multiply"]
    assert "+ C4 calc.mul(2, 3) is 6" in printed(folder) and not (folder / "calc.py").exists()


@pytest.mark.spec("CLI-10")
@pytest.mark.parametrize(
    "stopped,said",
    [
        (
            Stopped("unreproduced", 'the bug could not be reproduced ... then: tinker fix @abc123 "..."'),
            "Not fixed: the bug could not be reproduced",
        ),
        (Stopped("review", "`pytest` fails"), "Not done: the final review found `pytest` fails"),
        (Stopped("build", "it does not build"), "Stopped before working on criteria: it does not build"),
        (Stopped("tools", "the tools do not work"), "No code change can fix that."),
        (None, "Stopped: 1/2 tests pass; 1 parked after fixing and a review against the plan:"),
    ],
)
def test_a_blocked_run_says_why_and_how_to_go_on(make_project, make_session, capsys, stopped, said):
    steps = [
        criterion("C1", "t.py::a", "adds", status="green"),
        criterion("C2", "t.py::b", "subtracts", status="blocked", last_error="AssertionError"),
    ]
    run = bare_run(
        session=make_session(make_project(steps)),
        record={"id": "abc123"},
        tracker=Tracker("calc"),
        stopped=stopped,
        outcome=cli.Outcome(),
    )
    run.outcome.set("blocked")
    run.print_summary()
    out = capsys.readouterr().out
    assert said in out and ("tinker resume @abc123" in out or "tinker fix @abc123" in out)
    assert run.ending["status"] == "blocked" and run.ending["headline"] in out


@pytest.mark.spec("CLI-9")
def test_the_cycle_a_run_works_on(make_project, make_session):
    def run_on(project, mode, confirm=True, **given):
        defaults = {"bug": False, "change_request": "keep a history"}
        run = bare_run(
            args=argparse.Namespace(**dict(defaults, **given)),
            session=make_session(project, confirm=confirm),
            record=project.run,
            mode=mode,
            spec_text="",
        )
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
    assert (feature["kind"], feature["confirm"], fix["kind"], fix["phase"], fix["confirm"]) == (
        "feature",
        False,
        "fix",
        "recall",
        True,
    )
    assert [(feature["kind"], feature["request"]) for feature in project.run["features"]] == [
        ("feature", "keep a history"),
        ("fix", "2/0 crashes"),
    ]


@pytest.mark.spec("JOB-1")
def test_a_job_keeps_the_computer_awake_and_a_glance_does_not():
    assert Cli.works_for_hours(Cli.parse(["ask", "what", "formats", "python"]))
    assert Cli.works_for_hours(Cli.parse(["calibrate", "qwen3"]))
    assert not Cli.works_for_hours(Cli.parse(["runs"]))
