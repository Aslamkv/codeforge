import argparse
import io
import os
import re
import sys
from pathlib import Path

import pytest
from conftest import criterion, plan_of, record_of

from tinkerkit.jobs import Jobs
from tinkerkit.messages import Message, say
from tinkerkit.project import (
    Brief,
    Outcome,
    Project,
    RunChecks,
    Runs,
    SpecDocument,
    Steps,
    Tracked,
    Tracker,
    ending,
    project_store,
    saved,
)
from tinkerkit.settings import EXERCISE_ENV, Home
from tinkerkit.workspace import TaughtCommands, Workspace


def run_args(**given):
    defaults = {
        "restart": False,
        "resume": None,
        "workdir": ".",
        "spec": None,
        "spec_file": None,
        "add": None,
        "amend": None,
        "web": False,
        "task": "a calculator",
        "yes": False,
    }
    return argparse.Namespace(**dict(defaults, **given))


def saved_run(folder, run_id="abc123", cycles=(), steps=()):
    folder.mkdir(parents=True, exist_ok=True)
    run = dict(Runs.new_record("a calculator", folder, None), id=run_id)
    Runs.save(run, folder)
    project_store(folder).put(
        "spec", "plan", {"steps": list(steps), "counter": len(steps), "cycles": list(cycles)}
    )
    return run


@pytest.mark.spec("PRJ-1")
def test_new_record_and_save(home_store, project_dir):
    run = Runs.new_record("a task", project_dir, "plan.md")
    assert re.fullmatch(r"[0-9a-f]{6}", run["id"])
    assert {key: run[key] for key in ("task", "workdir", "spec", "status")} == {
        "task": "a task",
        "workdir": str(project_dir),
        "spec": "plan.md",
        "status": "running",
    }
    Runs.save(run, project_dir)
    assert record_of(project_dir) == run and "updated" in run
    assert home_store.get("projects", run["id"]) == {
        "id": run["id"],
        "workdir": str(project_dir),
        "task": "a task",
    }


@pytest.mark.spec("PRJ-2")
def test_find_by_full_id_or_folder_name_only(tmp_path):
    saved_run(tmp_path / "calc", "abc123")
    saved_run(tmp_path / "notes", "abd456")
    assert Runs.find("abc123")["workdir"] == str(tmp_path / "calc")
    assert Runs.find("notes")["id"] == "abd456"
    for part in ("abc", "ab", "ABC123", "calc-py", "not"):
        with pytest.raises(SystemExit, match=f"No project @{part}"):
            Runs.find(part)


@pytest.mark.spec("PRJ-2")
def test_two_folders_of_one_name_need_the_id(tmp_path):
    saved_run(tmp_path / "a" / "calc", "aaa111")
    saved_run(tmp_path / "b" / "calc", "bbb222")
    with pytest.raises(
        SystemExit, match="Several projects are in folders named calc: name one by its ID: @aaa111, @bbb222"
    ):
        Runs.find("calc")
    assert Runs.find("bbb222")["workdir"] == str(tmp_path / "b" / "calc")


@pytest.mark.spec("PRJ-2")
def test_entries_have_each_projects_own_record(tmp_path):
    saved_run(tmp_path / "calc", "abc123")
    import shutil

    saved_run(tmp_path / "gone", "fff000")
    shutil.rmtree(tmp_path / "gone")
    entries = {entry["id"]: entry for entry in Runs.entries()}
    assert entries["abc123"]["status"] == "running" and "updated" in entries["abc123"]
    assert entries["fff000"]["status"] == "missing"


@pytest.mark.spec("PRJ-3")
def test_workdir(tmp_path, monkeypatch):
    saved_run(tmp_path / "p", "abc123")
    assert Runs.workdir("abc123", None) == tmp_path / "p" and Runs.workdir("p", None) == tmp_path / "p"
    monkeypatch.chdir(tmp_path)
    assert Runs.workdir("", "p") == (tmp_path / "p").resolve()
    with pytest.raises(SystemExit, match="does not exist"):
        Runs.workdir("", "nowhere")
    import shutil

    shutil.rmtree(tmp_path / "p")
    with pytest.raises(SystemExit, match="no longer exists"):
        Runs.workdir("abc123", None)


@pytest.mark.spec("PRJ-4")
def test_open_new(tmp_path):
    folder = tmp_path / "new"
    folder.mkdir()
    (tmp_path / "plan.md").write_text("Use SQLite.")
    run, workdir, spec_text, mode = Runs.open(run_args(workdir=str(folder), spec=str(tmp_path / "plan.md")))
    assert (mode, spec_text, workdir) == ("new", "Use SQLite.", folder.resolve())
    assert saved(folder, "texts", "spec_text") == "Use SQLite." and run["spec"] == "plan.md"
    with pytest.raises(SystemExit, match="already holds run"):
        Runs.open(run_args(workdir=str(folder)))


@pytest.mark.spec("PRJ-4")
def test_open_existing(tmp_path):
    folder = tmp_path / "p"
    run = saved_run(folder)
    project_store(folder).put("texts", "spec_text", "saved spec")
    Runs.save(dict(run, status="blocked"), folder)
    run, _, text, mode = Runs.open(run_args(resume="", workdir=str(folder)))
    assert (mode, text, run["status"]) == ("resume", "saved spec", "running")
    (tmp_path / "feature.md").write_text("feature spec")
    _, _, text, mode = Runs.open(
        run_args(resume="", workdir=str(folder), add=["x"], spec_file=str(tmp_path / "feature.md"))
    )
    assert (mode, text) == ("change", "feature spec")


@pytest.mark.spec("PRJ-5")
def test_clear_folder_keeps_the_spec_file_and_forgets_its_pointers(tmp_path):
    folder = tmp_path / "p"
    saved_run(folder, "abc123")
    (folder / "app.py").write_text("x")
    (folder / "docs").mkdir()
    (folder / "docs" / "plan.md").write_text("spec")
    Runs.clear_folder(folder, (folder / "docs" / "plan.md").resolve(), yes=True)
    assert [path.name for path in folder.iterdir()] == ["docs"]
    assert Runs.entries() == []


@pytest.mark.spec("PRJ-5")
def test_clear_folder_refuses_the_wrong_folders(tmp_path, monkeypatch, tinker_home):
    for folder in (Path.home().resolve(), Path("/"), tinker_home.parent):
        with pytest.raises(SystemExit, match="will not empty"):
            Runs.clear_folder(folder, None, yes=True)
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "keep.txt").write_text("x")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="Nothing deleted"):
        Runs.clear_folder(folder, None)
    assert (folder / "keep.txt").exists()


@pytest.mark.spec("PRJ-7")
def test_before_start_for_a_new_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert RunChecks.before_start(run_args(workdir=".")) == tmp_path.resolve()
    nothing_made = not (tmp_path / ".tinker").exists()
    assert nothing_made
    with pytest.raises(SystemExit, match="Spec file not found"):
        RunChecks.before_start(run_args(workdir=".", spec="missing.md"))
    with pytest.raises(SystemExit, match="does not exist"):
        RunChecks.before_start(run_args(workdir="nowhere"))
    saved_run(tmp_path)
    with pytest.raises(SystemExit, match="already holds run abc123"):
        RunChecks.before_start(run_args(workdir="."))


@pytest.mark.spec("PRJ-7")
def test_before_start_for_a_saved_run(tmp_path):
    folder = tmp_path / "p"
    saved_run(folder, cycles=[{"id": "base", "request": "x", "phase": "nonsense"}])
    with pytest.raises(SystemExit, match="saved phase 'nonsense'"):
        RunChecks.before_start(run_args(resume="", workdir=str(folder)))
    saved_run(folder, cycles=[{"id": "base", "request": "a calculator", "phase": "verify"}])
    assert RunChecks.before_start(run_args(resume="", workdir=str(folder))) == folder
    with pytest.raises(SystemExit, match="unfinished project"):
        RunChecks.before_start(run_args(resume="", workdir=str(folder), add=["more"]))
    with pytest.raises(SystemExit, match="a plan that waits for confirmation"):
        RunChecks.before_start(run_args(resume="", workdir=str(folder), amend="change"))
    saved_run(folder, cycles=[])
    with pytest.raises(SystemExit, match="no project spec yet"):
        RunChecks.before_start(run_args(resume="", workdir=str(folder), add=["more"]))
    saved_run(folder, cycles=[{"id": "base", "request": "x", "phase": "done"}])
    assert RunChecks.before_start(run_args(resume="", workdir=str(folder), add=["more"])) == folder
    saved_run(folder, cycles=[{"id": "base", "request": "x", "phase": "setup", "awaiting": True}])
    assert RunChecks.before_start(run_args(resume="", workdir=str(folder), amend="change")) == folder


@pytest.mark.spec("PRJ-7")
def test_restart_reads_the_task_and_spec_first(tmp_path, monkeypatch):
    folder = tmp_path / "p"
    saved_run(folder)
    project_store(folder).put("texts", "spec_text", "the spec text")
    args = run_args(restart=True, workdir=str(folder))
    assert RunChecks.before_start(args) == folder
    assert (args.task, args.spec_text, args.spec) == ("a calculator", "the spec text", None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="has no task to restart"):
        RunChecks.before_start(run_args(restart=True, workdir="."))


@pytest.mark.spec("PRJ-8")
def test_project_cycles_and_criteria(make_project, project_dir):
    project = make_project(cycles=[])
    base = project.new_cycle("base", "a calculator")
    fix = project.new_cycle("f1", "a bug", kind="fix")
    assert (base["phase"], fix["phase"]) == ("recall", "recall")
    assert project.open_cycle() is base
    base["phase"] = "done"
    assert project.open_cycle() is fix and project.cycle("f1") is fix and project.cycle("zz") is None
    project.spec["counter"] = 0
    first = project.new_criterion("C", "t", "adds", "tests/test_a.py::test_add", "base", "request", "todo")
    bug = project.new_criterion("B", "t", "fixes", "tests/test_b.py::test_b", "f1", "bug report", "todo")
    assert (first["id"], bug["id"]) == ("C1", "B2") and first["history"] == []
    project.criteria.extend([dict(first, status="green"), dict(bug, status="blocked")])
    assert project.counts() == (1, 1, 2) and [criterion["id"] for criterion in project.parked()] == ["B2"]
    assert [criterion["id"] for criterion in project.cycle_criteria(fix)] == ["B2"]
    assert project.criterion("B2")["status"] == "blocked" and project.criterion("C9") is None
    project.save()
    assert plan_of(project_dir)["steps"][0]["id"] == "C1"
    assert Project.load(project_dir, project.run).criteria[0]["id"] == "C1"
    assert (project_dir / "SPEC.md").read_text().startswith("# Spec: a calculator")
    assert sorted(path.name for path in project_dir.iterdir()) == [".tinker", "SPEC.md"]


FILES = {
    "calc.py": {
        "kind": "code",
        "role": "the calculator",
        "contract": ["add(a, b) -> int"],
        "uses": ["util.py"],
    },
    "util.py": {"kind": "code", "role": "helpers", "contract": [], "uses": ["deep.py"]},
    "deep.py": {"kind": "code", "role": "deeper", "contract": [], "uses": []},
    "templates/page.html": {"kind": "template", "role": "the page", "contract": [], "uses": []},
    "web.py": {"kind": "code", "role": "renders page.html", "contract": [], "uses": ["templates/page.html"]},
    "tests/test_calc.py": {"kind": "test", "role": "tests", "contract": [], "uses": ["calc.py"]},
    "tests/test_web.py": {"kind": "test", "role": "tests", "contract": [], "uses": ["web.py"]},
    "requirements.txt": {"kind": "config", "role": "dependencies", "contract": [], "uses": []},
}


@pytest.mark.spec("PRJ-9")
def test_criteria_of(make_project):
    steps = [
        criterion("C1", "tests/test_calc.py::test_add", "add(2, 3) is 5"),
        criterion("C2", "tests/test_other.py::test_x", "util.clean(' a ') is 'a'"),
        criterion("C3", "tests/test_web.py::test_page", "GET / shows the title"),
        criterion("C4", "tests/test_other.py::test_y", "nothing related"),
    ]
    project = make_project(steps, files=FILES)
    assert [criterion["id"] for criterion in project.criteria_of("calc.py", steps)] == ["C1"]
    assert [criterion["id"] for criterion in project.criteria_of("util.py", steps)] == ["C2"]
    assert [criterion["id"] for criterion in project.criteria_of("templates/page.html", steps)] == ["C3"]
    assert project.criteria_of("deep.py", steps) == []


@pytest.mark.spec("PRJ-10")
def test_design_helpers(make_project, project_dir):
    project = make_project(files=dict(FILES), entry="calc.py")
    project.spec["toolchain"]["run"] = "python3 web.py"
    assert project.entry_files(["web.py", "calc.py", "util.py"]) == ["calc.py", "web.py"]
    assert project.contracts(["calc.py", "util.py"]) == "calc.py:\n  - add(a, b) -> int"
    project.register(["calc.py", "new.py"], "code", "split off", ["calc.py"])
    assert project.files["calc.py"]["role"] == "the calculator" and project.files["new.py"]["uses"] == [
        "calc.py"
    ]
    (project_dir / "requirements.txt").write_text("flask\n")
    assert project.dependencies(lambda path: (project_dir / path).read_text()) == "requirements.txt:\nflask"
    assert project.dependencies(lambda path: "") == "requirements.txt:\ndependencies"
    assert project.uses("calc.py") == ["util.py", "deep.py"] and project.uses("calc.py", depth=1) == [
        "util.py"
    ]
    assert project.paths("test") == ["tests/test_calc.py", "tests/test_web.py"]
    assert project.outline().startswith("- calc.py (code): the calculator")
    workspace = Workspace(project_dir)
    workspace.write("util.py", "x")
    project.remove_file(workspace, "util.py")
    assert not workspace.exists("util.py") and "util.py" not in project.files


@pytest.mark.spec("PRJ-8")
def test_test_names_and_criterion_of_test(make_project):
    steps = [criterion("C1", "t.py::test_a", "adds"), criterion("C2", "t.py::test_b", "subtracts")]
    project = make_project(steps)
    assert project.test_names("t.py") == ["test_a", "test_b"]
    assert project.criterion_of_test("t.py::test_b") == "subtracts" and project.criterion_of_test("x") == ""


@pytest.mark.spec("PRJ-11")
def test_spec_document(make_project, tinker_home):
    TaughtCommands.teach("act", ". .venv/bin/activate")
    steps = [
        criterion("C1", "t.py::test_add", "add(2, 3) is 5", status="green", source="adds numbers"),
        criterion(
            "C2", "t.py::test_div", "div(1, 0) says no", status="blocked", last_error="ZeroDivisionError"
        ),
    ]
    cycles = [
        {
            "id": "base",
            "request": "a calculator",
            "phase": "verify",
            "constraints": ["stdlib only"],
            "non_goals": ["a GUI"],
            "review": ["calc.py: fine"],
            "data": "SAMPLE = {}",
        }
    ]
    toolchain = {
        "setup": ["python3 -m venv .venv"],
        "activate": "act",
        "test_all": "act && pytest -q",
        "test_one": "pytest -q {file}::{name}",
        "lint": "none",
    }
    project = make_project(
        steps,
        files=dict(FILES),
        cycles=cycles,
        toolchain=toolchain,
        learned=[{"name": "Flask", "topic": "flask", "phase": "code", "text": "x"}],
    )
    text = SpecDocument(project).text()
    assert text.startswith(
        "# Spec: a calculator\n\nRun abc123. Language: Python. Learned from the web and used: Flask."
    )
    assert "| C1 | add(2, 3) is 5 | `t.py::test_add` | passes |" in text
    assert "| C2 | div(1, 0) says no | `t.py::test_div` | parked |" in text
    assert (
        "- test_all: `. .venv/bin/activate && pytest -q`" in text
        and "- setup: `python3 -m venv .venv`" in text
    )
    assert "- activate: `. .venv/bin/activate`" in text
    assert "## Project: a calculator" in text and "Phase: verify" in text
    for line in (
        "Constraints:\n- stdlib only",
        "Not wanted (non-goals):\n- a GUI",
        "Interface check:\n- calc.py: fine",
        "```\nSAMPLE = {}\n```",
    ):
        assert line in text
    assert "- `calc.py` [code]: the calculator (uses util.py)\n    - add(a, b) -> int" in text
    assert "### C1 add(2, 3) is 5" in text and '- From the request: "adds numbers"' in text
    assert "- Last error: ZeroDivisionError" in text and text.count("Last error") == 1


@pytest.mark.spec("PRJ-12")
def test_brief(project_dir, make_model, fake_ollama):
    model = make_model(max_prompt_tokens=1024)
    store = project_store(project_dir)
    assert Brief.load(model, store, "change", "task", "long spec") == ""
    assert Brief.load(model, store, "new", "task", "  short spec  ") == "short spec"
    assert store.get("texts", "brief") == "short spec"
    store.put("texts", "brief", "saved brief")
    assert Brief.load(model, store, "resume", "task", "anything") == "saved brief"
    fake_ollama.reply("- condensed", times=20)
    long_spec = "\n\n".join("requirement " + "x" * 300 for _ in range(30))
    brief = Brief.make(model, "task", long_spec)
    assert brief.startswith("- condensed") and len(brief) < len(long_spec)
    assert all(label.startswith("Brief ") for label in fake_ollama.labels())


@pytest.mark.spec("PRJ-13")
def test_outcome():
    outcome = Outcome()
    assert (outcome.status, outcome.exit_code) == ("stopped", 1)
    for status, code in (("done", 0), ("awaiting", 0), ("blocked", 1)):
        outcome.set(status)
        assert (outcome.status, outcome.exit_code) == (status, code)


@pytest.mark.spec("PRJ-14")
def test_the_tracker_publishes_each_change_in_its_job(home_store, project_dir):
    with Jobs.running(["task", "a", "calculator"]):
        tracker = Tracker("a calculator", before=12.0)
        store = project_store(project_dir)
        tracker.project(store, "abc123", project_dir, "calc: a calculator")
        tracker.phase("recall")
        tracker.phase("spec")
        tracker.points(1, 3)
        tracker.wait({"name": "calc", "kind": "project"})
        state = Jobs.get(1)["progress"]
        assert (
            state["title"] == "calc: a calculator"
            and state["run"] == "abc123"
            and state["folder"] == str(project_dir)
        )
        assert state["step"] == "spec" and state["doing"] == "📝 Writing the spec" and state["before"] == 12.0
        assert state["steps"] == ["🧠 Recalled what Tinker has learned"] and state["passing"] == [1, 3]
        assert state["waiting"] == {"name": "calc", "kind": "project"} and state["pid"] == os.getpid()
        assert state["job"] == 1
        events = [event["event"] for _, event in store.entries("progress")]
        assert events == ["project", "phase", "phase", "points", "waiting"]
        the_same_points = (1, 3)
        tracker.points(*the_same_points)
        assert len(store.entries("progress")) == 5
        tracker.end({"status": "done", "headline": "Done."}, 30.0)
    state = Jobs.get(1)["progress"]
    assert state["ended"]["status"] == "done" and state["seconds"] == 30.0 and state["resumed"] is None


@pytest.mark.spec("PRJ-14")
def test_a_tracker_outside_a_job_publishes_nothing(home_store, project_dir, monkeypatch):
    monkeypatch.delenv(EXERCISE_ENV, raising=False)
    tracker = Tracker("a calculator")
    tracker.project(project_store(project_dir), "abc123", project_dir)
    tracker.phase("spec")
    assert home_store.items("jobs") == {} and tracker.state["step"] == "spec"


@pytest.mark.spec("PRJ-14")
def test_an_exercise_build_publishes_in_the_exercise_job_as_build(home_store, monkeypatch):
    Jobs.save({"number": 1, "command": "exercise", "title": "exercise rust", "state": "running"})
    monkeypatch.setenv(EXERCISE_ENV, "1")
    Tracker("calc").phase("spec")
    job = Jobs.get(1)
    assert job["build"]["step"] == "spec" and job["build"]["exercise"] and "progress" not in job


@pytest.mark.spec("PRJ-15")
def test_ending_keeps_status_headline_details_and_next_as_lists():
    assert ending("done", "Done.") == {"status": "done", "headline": "Done.", "details": [], "next": []}
    made = ending("blocked", "Stopped", (("Why", "x"),), (("Go on", "tinker resume"),))
    assert made["details"] == [("Why", "x")] and made["next"] == [("Go on", "tinker resume")]


@pytest.mark.spec("PRJ-15")
def test_steps_live_progress_and_messages(home_store, project_dir, monkeypatch):
    assert set(Tracker.DOING_AND_DONE) >= {
        "recall",
        "spec",
        "design",
        "review",
        "setup",
        "tests",
        "code",
        "verify",
        "tidy",
        "docs",
        "final_review",
        "lessons",
        "reproduce",
        "amend",
    }
    with Jobs.running(["task", "calc"]):
        tracker = Tracker("calc")
        store = project_store(project_dir)
        tracker.project(store, "abc123", project_dir)
        tracker.heard(Message("working", "Writing calc.py", 1))
        tracker.heard(Message("live", "Writing calc.py… 14s", 2, "⏳"))
        tracker.heard(Message("tally", "1/2 files"))
        tracker.heard(Message("done", "Wrote calc.py (12 lines)", 1, "✅"))
        tracker.heard(Message("found", "2/0 gives a clear error", 1, "💡"))
        state = tracker.state
        assert state["working"] == "Writing calc.py" and state["tally"] == "1/2 files"
        assert state["found"] == "2/0 gives a clear error" and state["latest"] == "💡 2/0 gives a clear error"
        assert [(entry["line"], entry["kind"]) for _, entry in store.entries("output")] == [
            ("   ✅ Wrote calc.py (12 lines)", "done"),
            ("   💡 2/0 gives a clear error", "found"),
        ]
        monkeypatch.setattr(Tracker, "QUIET_PUBLISH_SECONDS", 1000)
        a_quiet_change = Message("text", "next line")
        tracker.heard(a_quiet_change)
        assert Jobs.get(1)["progress"]["latest"] != "next line"
        tracker.phase("verify")
        state = Jobs.get(1)["progress"]
        assert state["latest"] == state["found"] == state["working"] == "" and state["tally"] == "1/2 files"


@pytest.mark.spec("PRJ-15")
def test_a_step_done_says_what_came_of_it(home_store):
    tracker = Tracker("reading")
    tracker.phase("survey")
    tracker.outcome("3 core chapter(s) of 20, read first")
    tracker.phase("chapters")
    tracker.phase("keep")
    assert tracker.state["steps"] == [
        "🧭 Chose the chapters to read: 3 core chapter(s) of 20, read first",
        "📚 Chapters read",
    ]


@pytest.mark.spec("PRJ-15")
def test_the_passing_count_follows_every_save(make_project, make_session):
    steps = [
        criterion("C1", "t.py::a", status="green"),
        criterion("B2", "t.py::b", status="todo", cycle="f1"),
    ]
    tracker = Tracker("calc")
    session = make_session(make_project(steps), tracker=tracker)
    session.save()
    assert tracker.state["passing"] == [1, 2]
    session.project.criteria[1]["status"] = "green"
    session.save()
    assert tracker.state["passing"] == [2, 2]


@pytest.mark.spec("MSG-3", "MSG-5")
def test_a_run_keeps_its_calls_and_output_in_its_project(project_dir, make_model, fake_ollama):
    from tinkerkit.prompts import Part
    from tinkerkit.store import Log

    store = project_store(project_dir)
    model = make_model()
    model.log = Log(store, "events")
    fake_ollama.reply("ok")
    model.ask("s", [Part("Q", "q", True)], "Ask")
    assert [event["reply"] for _, event in store.entries("events") if "reply" in event] == ["ok"]
    tracker = Tracker("calc")
    tracker.project(store, "abc123", project_dir)
    say.follow(tracker.heard)
    say.done("Wrote calc.py")
    assert [event["line"] for _, event in store.entries("output")] == ["✅ Wrote calc.py"]


@pytest.mark.spec("PRJ-6")
def test_remember_keeps_a_line_about_each_run(home_store, monkeypatch):
    monkeypatch.setattr("tinkerkit.project.RUNS_KEPT", 2)
    for run_id, updated in (
        ("aaa111", "2026-10-01 10:00"),
        ("bbb222", "2026-10-03 10:00"),
        ("ccc333", "2026-10-02 10:00"),
    ):
        Runs.remember(
            {
                "id": run_id,
                "task": f"task {run_id}",
                "status": "done",
                "created": "2026-10-01 09:00",
                "updated": updated,
                "workdir": "/somewhere",
            },
            "Python",
        )
    assert sorted(home_store.items("runs")) == ["bbb222", "ccc333"]
    assert home_store.get("runs", "bbb222") == {
        "id": "bbb222",
        "task": "task bbb222",
        "status": "done",
        "created": "2026-10-01 09:00",
        "updated": "2026-10-03 10:00",
        "language": "Python",
    }
    home_store.put("projects", "bbb222", {"id": "bbb222", "workdir": "/somewhere", "task": "task bbb222"})
    Home.clear(["runs"], yes=True)
    assert home_store.items("runs") == {} and home_store.items("projects") == {}


@pytest.mark.spec("PRJ-16")
def test_steps_go_on_from_the_one_reached_and_save_each_next_one():
    tracker, ran, saved = Tracker("practice"), [], []
    steps = Steps(("build", "feature", "measure", "done"), tracker)
    assert steps.run("feature", ran.append, saved.append) == "done"
    assert ran == ["feature", "measure"] and saved == ["measure", "done"]
    assert tracker.state["step"] == "measure" and tracker.state["steps"] == ["➕ Feature added"]
    assert steps.run("done", ran.append, saved.append) == "done" and ran == ["feature", "measure"]


@pytest.mark.spec("PRJ-16")
def test_a_step_passed_over_a_blocked_one_and_a_wait_stop_the_steps_as_they_say():
    tracker, ran, saved = Tracker("practice"), [], []
    steps = Steps(("build", "feature", "measure", "done"), tracker, only_if={"feature": lambda: False})
    blocked = steps.run("build", lambda step: ran.append(step) or step != "measure", saved.append)
    assert blocked == "blocked" and ran == ["build", "measure"] and saved == ["feature", "measure"]
    assert tracker.state["steps"] == ["🔨 Practice project built"]
    assert steps.run("build", ran.append, saved.append, waits=lambda: True) == "waiting"


@pytest.mark.spec("PRJ-16")
def test_entering_a_step_moves_the_tracker_once_in_a_row():
    tracker = Tracker("reading")
    steps = Steps(("survey", "chapters", "done"), tracker)
    steps.enter("survey")
    steps.enter("survey")
    steps.enter("chapters")
    assert tracker.state["steps"] == ["🧭 Chose the chapters to read"]
    with pytest.raises(ValueError, match="no step 'fly'"):
        steps.enter("fly")
    Steps(("survey", "done")).enter("survey")


@pytest.mark.spec("PRJ-16")
def test_a_tracked_step_moves_its_tracker_and_says_the_step_when_told(monkeypatch):
    heard = []
    monkeypatch.setattr(say, "followers", [heard.append])
    tracked = Tracked()
    tracked.STEPS = ("survey", "chapters", "done")
    tracked.outcome("ignored without a tracker")
    tracked.step("survey")
    assert heard == []
    tracked.tracker = Tracker("reading")
    tracked.step("survey", "Reading its contents…", "🧭")
    tracked.outcome("2 core chapter(s)")
    assert [(message.kind, message.text) for message in heard] == [("step", "Reading its contents…")]
    assert (
        tracked.tracker.state["step"] == "survey" and tracked.tracker.state["outcome"] == "2 core chapter(s)"
    )
