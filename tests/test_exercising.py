"""EXR: tinker exercise LANGUAGE: practice projects that teach Tinker a language, giving way to the person."""

import json
import os
import signal
import time
from pathlib import Path

import pytest

from tinker import Cli
from tinkerkit import conversation, exercising
from tinkerkit.conversation import Desk, Message, persons_runs
from tinkerkit.exercising import (
    Exercise, Exercises, free_folder, identified, measured, person_busy, problems, read_choice, read_verdict, slowest,
)
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.project import Tracker, project_store
from tinkerkit.settings import EXERCISE_QUIET, Config, Home
from test_conversation import Recorder

ALIVE = os.getppid()  # a process that runs while the tests do, and is not this one
RUST = {"topic": "rust", "kind": "language", "name": "Rust", "summary": "A systems language.",
        "machine": [{"command": "cargo --version", "found": True, "version": "1.90"}]}
CHOICE = {"task": "A word counter for text files in Rust", "feature": "also count lines", "folder": "word-count",
          "practises": ["files", "iterators"]}
REFLECTION = {"lessons": [{"phase": "code", "scope": "language", "text": "Read whole files with fs::read_to_string"}],
              "confirmed": [], "focus": "error handling with Result"}


class FakeBuild:
    """A build's process: it runs for polls looks, then ends with code."""

    def __init__(self, polls=0, code=0):
        self.polls, self.code, self.returncode, self.pid = polls, code, None, 424242

    def poll(self):
        if self.polls > 0:
            self.polls -= 1
            return None
        self.returncode = self.code
        return self.code

    def wait(self, timeout=None):
        self.polls = 0
        return self.poll()


class Builds:
    """How an exercise starts its builds, scripted: each start is kept, and does to the project's folder what the
    real command would (a task keeps a run that is done, an add a new cycle, a learn the language)."""

    def __init__(self, status="done"):
        self.started, self.status = [], status

    def __call__(self, arguments):
        self.started.append(list(arguments))
        command = arguments[0]
        if command == "learn":
            Library.save("rust", RUST)
        elif command in ("task", "add"):
            folder = Path(arguments[arguments.index("--workdir") + 1])
            store = project_store(folder)
            plan = store.get("spec", "plan", {"steps": [], "counter": 0, "cycles": [{"id": "base", "phase": "done"}]})
            if command == "add":
                plan["cycles"].append({"id": "f1", "phase": "done"})
            store.put("spec", "plan", plan)
            store.put("run", "record", {"id": "run1", "task": "t", "status": self.status})
            store.append("events", {"label": "session end", "wall_seconds": 120.0})
        return FakeBuild()


@pytest.fixture
def projects(tmp_path, home_store):
    Config.save(projects=str(tmp_path / "Projects"))
    return tmp_path / "Projects" / "tinker-exercise"


@pytest.fixture
def quiet_person(monkeypatch, home_store):
    """The person last asked for something long ago, and runs nothing."""
    home_store.put("activity", "person", time.time() - EXERCISE_QUIET - 1)


def exercise_of(make_model, builds, title="Exercising rust"):
    return Exercise(make_model(), Tracker(title), start=builds)


# --- EXR-1: the words must name one language ------------------------------------------------------------------------

@pytest.mark.spec("EXR-1")
def test_a_learned_language_needs_no_call(make_model, fake_ollama, home_store):
    Library.save("rust", RUST)
    assert identified(make_model(), "rust") == "Rust"
    assert fake_ollama.calls == []


@pytest.mark.spec("EXR-1")
@pytest.mark.parametrize("verdict, said", [
    ({"verdict": "ambiguous", "choices": ["Common Lisp", "Scheme"], "why": "many lisps"}, "could be Common Lisp, Scheme"),
    ({"verdict": "other", "why": "it is a web framework"}, "is not a programming language (it is a web framework)"),
])
def test_words_that_are_not_one_language_do_nothing(make_model, fake_ollama, home_store, projects, verdict, said):
    fake_ollama.reply(json.dumps(verdict), when="which language")
    builds = Builds()
    with pytest.raises(SystemExit, match=r"Nothing done") as stopped:
        exercise_of(make_model, builds).run("lisp")
    assert said in str(stopped.value)
    assert builds.started == [] and home_store.items("exercise") == {} and not projects.exists()


@pytest.mark.spec("EXR-1")
def test_read_verdict_needs_a_known_verdict_and_a_language_its_name():
    assert read_verdict({"verdict": "Language", "name": "Go", "why": "a language"})["verdict"] == "language"
    with pytest.raises(ValueError):
        read_verdict({"verdict": "maybe"})
    with pytest.raises(ValueError):
        read_verdict({"verdict": "language"})


# --- EXR-2: learned first, installed here ---------------------------------------------------------------------------

@pytest.mark.spec("EXR-2")
def test_a_language_not_learned_is_learned_first(make_model, fake_ollama, quiet_person):
    fake_ollama.reply(json.dumps({"verdict": "language", "name": "Rust"}), when="which language")
    builds = Builds()
    exercise = exercise_of(make_model, builds)
    exercise.knows(identified(exercise.model, "rust"))
    assert builds.started == [["learn", "Rust"]]


@pytest.mark.spec("EXR-2")
def test_a_language_not_installed_is_said_and_nothing_is_practised(make_model, home_store, quiet_person):
    Library.save("rust", dict(RUST, machine=[{"command": "cargo --version", "found": False}]))
    builds = Builds()
    with pytest.raises(SystemExit, match="Rust is not installed here"):
        exercise_of(make_model, builds).knows("Rust")
    assert builds.started == []


# --- EXR-3: the next practice project -------------------------------------------------------------------------------

@pytest.mark.spec("EXR-3")
def test_read_choice_names_the_language_and_never_repeats_a_project():
    choice = read_choice({"task": "A todo list", "feature": "due dates", "folder": "Bad Name!"}, "Rust", [])
    assert choice["task"] == "A todo list in Rust" and choice["folder"] == "a-todo-list"
    with pytest.raises(ValueError, match="repeats"):
        read_choice({"task": "A todo list in Rust", "feature": "x"}, "Rust", ["A todo list in Rust"])
    with pytest.raises(ValueError):
        read_choice({"task": "A todo list"}, "Rust", [])


@pytest.mark.spec("EXR-3")
def test_each_practice_project_gets_a_folder_of_its_own(projects):
    (projects / "word-count").mkdir(parents=True)
    assert free_folder("word-count") == projects / "word-count-2"
    assert free_folder("csv-stats") == projects / "csv-stats"


# --- EXR-4: build, feature, measure, reflect, each step kept --------------------------------------------------------

@pytest.mark.spec("EXR-4")
def test_a_practice_project_is_built_then_given_its_feature(make_model, fake_ollama, home_store, projects, quiet_person):
    Library.save("rust", RUST)
    fake_ollama.reply(json.dumps(CHOICE), when="next project")
    fake_ollama.reply(json.dumps(REFLECTION), when="lessons")
    builds = Builds()
    exercise = Exercises.of("Rust")
    exercise_of(make_model, builds).practise(exercise)
    folder = str(projects / "word-count")
    assert builds.started == [["task", CHOICE["task"], "--workdir", folder],
                              ["add", CHOICE["feature"], "--workdir", folder]]
    kept = home_store.get("exercise", "rust")
    project = kept["projects"][0]
    assert project["step"] == "done" and project["measures"]["minutes"] == 4.0 and project["lessons"] == 1
    assert kept["focus"] == "error handling with Result"
    assert any("fs::read_to_string" in lesson["text"] for lesson in Knowledge("Rust").known_lessons())


@pytest.mark.spec("EXR-4")
def test_a_project_that_did_not_build_gets_no_feature(make_model, fake_ollama, projects, quiet_person):
    fake_ollama.reply(json.dumps(CHOICE), when="next project")
    fake_ollama.reply(json.dumps(REFLECTION), when="lessons")
    builds = Builds(status="blocked")
    exercise_of(make_model, builds).practise(Exercises.of("Rust"))
    assert [started[0] for started in builds.started] == ["task"]


@pytest.mark.spec("EXR-4")
def test_an_exercise_goes_on_from_the_step_it_reached(make_model, fake_ollama, home_store, projects, quiet_person):
    folder = projects / "word-count"
    Builds()(["task", "t", "--workdir", str(folder)])
    exercise = Exercises.of("Rust")
    exercise["projects"].append(dict(CHOICE, folder=str(folder), step="build", started="then"))
    fake_ollama.reply(json.dumps(REFLECTION), when="lessons")
    builds = Builds()
    exercise_of(make_model, builds).practise(exercise)
    assert builds.started[0] == ["resume", "--workdir", str(folder)]
    assert "next project" not in " ".join(fake_ollama.labels())


# --- EXR-5: what a project measured ---------------------------------------------------------------------------------

@pytest.mark.spec("EXR-5")
def test_measured_and_problems_come_from_the_projects_own_database(make_project, project_dir):
    from conftest import criterion
    project = make_project([criterion("C1", "t::a", status="green"), criterion("C2", "t::b", "sorts", status="blocked")],
                           cycles=[{"id": "base", "request": "r", "phase": "done", "readme_failures": ["`x`: boom"]}])
    project.save()
    project.store.put("run", "record", {"id": "abc123", "status": "done"})
    for event in ({"label": "Spec (attempt 1) [think]", "reply_tokens": 100, "seconds": 50.0},
                  {"label": "Spec (attempt 2) [think]", "reply_tokens": 80, "seconds": 40.0, "cut": True},
                  {"label": "Write calc.py [think]", "reply_tokens": 300, "seconds": 120.0},
                  {"note": "spec not usable: no criteria"}, {"label": "session end", "wall_seconds": 300.0}):
        project.store.append("events", event)
    project.store.append("output", {"line": "⚠️ ruff is not installed", "kind": "warn"})
    measures = measured(project_dir)
    assert {k: measures[k] for k in ("status", "minutes", "calls", "retried", "tokens", "passing", "parked", "points",
                                      "readme_failures")} == {"status": "done", "minutes": 5.0, "calls": 3, "retried": 1,
                                                              "tokens": 480, "passing": 1, "parked": 1, "points": 2,
                                                              "readme_failures": 1}
    assert measures["slowest"] == ["Write: 120s in 1 call", "Spec: 90s in 2 calls"]
    found = problems(project_dir)
    assert "nothing made this pass: sorts" in found and "README example failed: `x`: boom" in found
    assert "reply not usable: spec not usable: no criteria" in found and "warning: ⚠️ ruff is not installed" in found
    assert any(problem.startswith("reply cut short: Spec (attempt 2)") for problem in found)


@pytest.mark.spec("EXR-5")
def test_slowest_never_names_a_file():
    assert slowest([{"label": "Write notes.py", "seconds": 5}]) == ["Write: 5s in 1 call"]


# --- EXR-6: the person comes first ----------------------------------------------------------------------------------

@pytest.mark.spec("EXR-6")
def test_the_person_is_busy_while_they_ask_or_run_something_of_theirs(home_store, quiet_person):
    assert not person_busy()
    home_store.put("running", str(ALIVE), {"title": "Exercise build", "exercise": True})
    assert not person_busy()
    home_store.put("running", str(ALIVE), {"title": "a calculator", "exercise": False})
    assert person_busy()
    home_store.delete("running")
    Home.person_seen()
    assert person_busy()


@pytest.mark.spec("EXR-6")
def test_a_build_gives_way_and_starts_again_from_where_it_was(make_model, home_store, quiet_person, monkeypatch,
                                                               no_sleep):
    signals, starts = [], iter([["task", "t"], ["resume", "--workdir", "f"]])
    monkeypatch.setattr(os, "killpg", lambda pid, signum: signals.append(signum))
    builds = []

    def start(arguments):
        build = FakeBuild(polls=5, code=0)
        builds.append((arguments, build))
        if len(builds) == 1:
            home_store.put("activity", "person", time.time())  # the person asks for something while it builds
        return build

    def quiet_again(seconds):
        home_store.put("activity", "person", time.time() - EXERCISE_QUIET - 1)
    monkeypatch.setattr(time, "sleep", quiet_again)
    code = Exercise(make_model(), Tracker("Exercising rust"), start=start).built(lambda: next(starts))
    assert code == 0 and signals == [signal.SIGINT]
    assert [arguments for arguments, _ in builds] == [["task", "t"], ["resume", "--workdir", "f"]]


@pytest.mark.spec("EXR-6")
def test_the_conversation_marks_the_person_and_leaves_exercise_runs_alone(home_store, tmp_path, monkeypatch):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    monkeypatch.setattr(desk, "tinker", lambda words, timeout=120: "ok")
    desk.hear(channel, Message("1", "exercise status"))
    assert Home.person_last_seen() == 0.0
    desk.hear(channel, Message("1", "help"))
    assert Home.person_last_seen() > 0
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"})
    assert persons_runs() == []
    home_store.put("activity", "exercise", {"pid": ALIVE, "language": "Rust"})
    assert desk.status().startswith("🏋️ **Tinker** · **Exercising Rust**")


# --- EXR-7: one at a time, stopped with everything kept -------------------------------------------------------------

@pytest.mark.spec("EXR-7")
def test_a_stopped_exercise_stops_its_build_and_keeps_everything(make_model, home_store, projects, quiet_person,
                                                                 monkeypatch):
    Library.save("rust", RUST)
    exercise = exercise_of(make_model, Builds())
    stopped_builds = []
    monkeypatch.setattr(exercise, "practise", lambda kept: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(exercise, "stop_build", lambda: stopped_builds.append(True))
    ending = exercise.run("rust")
    assert ending["status"] == "stopped" and ending["next"] == [("Go on", "tinker exercise Rust")]
    assert stopped_builds == [True] and home_store.get("exercise", "rust")["language"] == "Rust"
    assert home_store.get("activity", "exercise") is None and exercise.tracker.state["exercise"]


@pytest.mark.spec("EXR-7")
def test_only_one_exercise_runs_at_a_time(home_store):
    home_store.put("activity", "exercise", {"pid": ALIVE, "language": "Go"})
    with pytest.raises(SystemExit, match="An exercise of Go runs already"):
        Exercises.claim(Exercises.of("Rust"))


@pytest.mark.spec("EXR-7")
def test_exercise_stop_tells_the_one_that_runs(home_store, monkeypatch, capsys):
    exercising.stop()
    assert "No exercise runs." in capsys.readouterr().out
    told = []
    home_store.put("activity", "exercise", {"pid": ALIVE, "language": "Go"})
    monkeypatch.setattr(exercising, "signal_task", lambda pid, signum: told.append((pid, signum)))
    lives = iter([True, True, False, False])
    monkeypatch.setattr(exercising, "alive", lambda pid: next(lives))
    exercising.stop()
    assert told == [(ALIVE, conversation.STOP)] and "stopped, everything kept" in capsys.readouterr().out


@pytest.mark.spec("EXR-7")
def test_exercise_status_shows_each_project_measured(home_store, projects, quiet_person, capsys):
    exercise = Exercises.of("Rust")
    measures = {"status": "done", "minutes": 4.0, "calls": 9, "retried": 0, "tokens": 900, "passing": 3, "parked": 0,
                "points": 3, "readme_failures": 0, "slowest": []}
    exercise["projects"].append(dict(CHOICE, folder=str(projects / "word-count"), step="done", measures=measures))
    Exercises.save(exercise)
    exercising.show()
    out = capsys.readouterr().out
    assert "Rust: stopped, 1 practice project(s) done" in out and "word-count: done, 4.0 min, 9 model calls" in out


@pytest.mark.spec("EXR-7")
def test_tinker_exercise_takes_a_language_stop_or_status():
    assert Cli.parse(["exercise", "rust"]).exercise == "rust"
    assert Cli.parse(["exercise", "stop"]).exercise == "stop"
    with pytest.raises(SystemExit):
        Cli.parse(["exercise"])
