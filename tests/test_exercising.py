import json
import os
import time
from pathlib import Path

import pytest
from test_conversation import Recorder

from tinkerkit import conversation, exercising
from tinkerkit.cli import Cli
from tinkerkit.conversation import Desk, persons_runs
from tinkerkit.exercising import (
    PRACTICE_STEPS,
    Exercise,
    Exercises,
    free_folder,
    identified,
    measured,
    problems,
    read_choice,
    read_verdict,
    slowest,
)
from tinkerkit.jobs import Jobs
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.project import Steps, Tracker, project_store
from tinkerkit.settings import Config

OTHER_LIVE_PID = os.getppid()
BUILDING = {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"}


def exercise_job(state, **kept):
    return {
        "number": 1,
        "command": "exercise",
        "title": "exercise rust",
        "state": state,
        "pid": OTHER_LIVE_PID,
        "arguments": ["exercise", "rust"],
        "updated": "x",
        **kept,
    }


def exercise_tracker(title):
    Jobs.current = Jobs.save(exercise_job("running"))
    return Tracker(title)


RUST = {
    "topic": "rust",
    "kind": "language",
    "name": "Rust",
    "summary": "A systems language.",
    "machine": [{"command": "cargo --version", "found": True, "version": "1.90"}],
}
CHOICE = {
    "task": "A word counter for text files in Rust",
    "feature": "also count lines",
    "folder": "word-count",
    "practises": ["files", "iterators"],
}
REFLECTION = {
    "lessons": [{"phase": "code", "scope": "language", "text": "Read whole files with fs::read_to_string"}],
    "confirmed": [],
    "focus": "error handling with Result",
}


class FakeBuild:
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
            plan = store.get(
                "spec", "plan", {"steps": [], "counter": 0, "cycles": [{"id": "base", "phase": "done"}]}
            )
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


def exercise_of(make_model, builds, title="Exercising rust"):
    return Exercise(make_model(), exercise_tracker(title), start_build=builds)


@pytest.mark.spec("EXR-1")
def test_a_learned_language_needs_no_call(make_model, fake_ollama, home_store):
    Library.save("rust", RUST)
    assert identified(make_model(), "rust") == "Rust"
    assert fake_ollama.calls == []


@pytest.mark.spec("EXR-1")
@pytest.mark.parametrize(
    "verdict, said",
    [
        (
            {"verdict": "ambiguous", "choices": ["Common Lisp", "Scheme"], "why": "many lisps"},
            "could be Common Lisp, Scheme",
        ),
        (
            {"verdict": "other", "why": "it is a web framework"},
            "is not a programming language (it is a web framework)",
        ),
    ],
)
def test_words_that_are_not_one_language_do_nothing(
    make_model, fake_ollama, home_store, projects, verdict, said
):
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


@pytest.mark.spec("EXR-2")
def test_a_language_not_learned_is_learned_first(make_model, fake_ollama):
    fake_ollama.reply(json.dumps({"verdict": "language", "name": "Rust"}), when="which language")
    builds = Builds()
    exercise = exercise_of(make_model, builds)
    exercise.knows(identified(exercise.model, "rust"))
    assert builds.started == [["learn", "Rust"]]


@pytest.mark.spec("EXR-2")
def test_a_language_not_installed_is_said_and_nothing_is_practised(make_model, home_store):
    Library.save("rust", dict(RUST, machine=[{"command": "cargo --version", "found": False}]))
    builds = Builds()
    with pytest.raises(SystemExit, match="Rust is not installed here"):
        exercise_of(make_model, builds).knows("Rust")
    assert builds.started == []


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


@pytest.mark.spec("EXR-4")
def test_a_practice_project_is_built_then_given_its_feature(make_model, fake_ollama, home_store, projects):
    Library.save("rust", RUST)
    fake_ollama.reply(json.dumps(CHOICE), when="next project")
    fake_ollama.reply(json.dumps(REFLECTION), when="lessons")
    builds = Builds()
    exercise = Exercises.of("Rust")
    exercise_of(make_model, builds).practise(exercise)
    folder = str(projects / "word-count")
    assert builds.started == [
        ["task", CHOICE["task"], "--workdir", folder],
        ["add", CHOICE["feature"], "--workdir", folder],
    ]
    kept = home_store.get("exercise", "rust")
    project = kept["projects"][0]
    assert project["step"] == "done" and project["measures"]["minutes"] == 4.0 and project["lessons"] == 1
    assert kept["focus"] == "error handling with Result"
    assert any("fs::read_to_string" in lesson["text"] for lesson in Knowledge("Rust").known_lessons())


@pytest.mark.spec("EXR-4")
def test_a_project_that_did_not_build_gets_no_feature(make_model, fake_ollama, projects):
    fake_ollama.reply(json.dumps(CHOICE), when="next project")
    fake_ollama.reply(json.dumps(REFLECTION), when="lessons")
    builds = Builds(status="blocked")
    exercise_of(make_model, builds).practise(Exercises.of("Rust"))
    assert [started[0] for started in builds.started] == ["task"]


@pytest.mark.spec("EXR-4")
def test_an_exercise_goes_on_from_the_step_it_reached(make_model, fake_ollama, home_store, projects):
    folder = projects / "word-count"
    Builds()(["task", "t", "--workdir", str(folder)])
    exercise = Exercises.of("Rust")
    exercise["projects"].append(dict(CHOICE, folder=str(folder), step="build", started="then"))
    fake_ollama.reply(json.dumps(REFLECTION), when="lessons")
    builds = Builds()
    exercise_of(make_model, builds).practise(exercise)
    assert builds.started[0] == ["resume", "--workdir", str(folder)]
    assert "next project" not in " ".join(fake_ollama.labels())


@pytest.mark.spec("EXR-5")
def test_measured_and_problems_come_from_the_projects_own_database(make_project, project_dir):
    from conftest import criterion

    project = make_project(
        [criterion("C1", "t::a", status="green"), criterion("C2", "t::b", "sorts", status="blocked")],
        cycles=[{"id": "base", "request": "r", "phase": "done", "readme_failures": ["`x`: boom"]}],
    )
    project.save()
    project.store.put("run", "record", {"id": "abc123", "status": "done"})
    for event in (
        {"label": "Spec (attempt 1) [think]", "reply_tokens": 100, "seconds": 50.0},
        {"label": "Spec (attempt 2) [think]", "reply_tokens": 80, "seconds": 40.0, "cut": True},
        {"label": "Write calc.py [think]", "reply_tokens": 300, "seconds": 120.0},
        {"note": "spec not usable: no criteria"},
        {"label": "session end", "wall_seconds": 300.0},
    ):
        project.store.append("events", event)
    project.store.append("output", {"line": "⚠️ ruff is not installed", "kind": "warn"})
    measures = measured(project_dir)
    assert {
        key: measures[key]
        for key in (
            "status",
            "minutes",
            "calls",
            "retried",
            "tokens",
            "passing",
            "parked",
            "points",
            "readme_failures",
        )
    } == {
        "status": "done",
        "minutes": 5.0,
        "calls": 3,
        "retried": 1,
        "tokens": 480,
        "passing": 1,
        "parked": 1,
        "points": 2,
        "readme_failures": 1,
    }
    assert measures["slowest"] == ["Write: 120s in 1 call", "Spec: 90s in 2 calls"]
    found = problems(project_dir)
    assert "nothing made this pass: sorts" in found and "README example failed: `x`: boom" in found
    assert (
        "reply not usable: spec not usable: no criteria" in found
        and "warning: ⚠️ ruff is not installed" in found
    )
    assert any(problem.startswith("reply cut short: Spec (attempt 2)") for problem in found)


@pytest.mark.spec("EXR-5")
def test_slowest_never_names_a_file():
    assert slowest([{"label": "Write notes.py", "seconds": 5}]) == ["Write: 5s in 1 call"]


@pytest.mark.spec("EXR-6")
def test_the_exercise_has_no_rule_of_its_own_for_giving_way(make_model, home_store, monkeypatch, no_sleep):
    signals = []
    monkeypatch.setattr(os, "killpg", lambda pid, signum: signals.append(signum))
    the_person_is_answered = {"a chat": os.getpid()}
    home_store.put("activity", "answering", the_person_is_answered)
    code = Exercise(
        make_model(),
        exercise_tracker("Exercising rust"),
        start_build=lambda arguments: FakeBuild(polls=3, code=0),
    ).built(lambda: ["task", "t"])
    assert code == 0 and signals == []


@pytest.mark.spec("EXR-6")
def test_the_exercise_shows_what_its_build_does(make_model, home_store, monkeypatch, tmp_path):
    what_the_build_does = {
        "doing": "💻 Writing the code",
        "working": "Writing a.py",
        "latest": "✅ Wrote a.py",
        "passing": [2, 5],
        "exercise": True,
    }

    def start_build(arguments):
        Jobs.update(1, build=what_the_build_does)
        return FakeBuild(polls=1)

    exercise = Exercise(make_model(), exercise_tracker("Exercising rust"), start_build=start_build)
    Steps(PRACTICE_STEPS, exercise.tracker).enter("build")
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    exercise.practise_step("build", {"language": "rust"}, {"folder": str(tmp_path / "calc"), "task": "t"})
    state = exercise.tracker.state
    assert state["doing"] == "🔨 Building the practice project · 💻 Writing the code"
    assert (state["working"], state["latest"], state["passing"]) == ("Writing a.py", "✅ Wrote a.py", [2, 5])


@pytest.mark.spec("EXR-6")
def test_an_exercise_that_goes_on_by_itself_comes_to_the_bottom(home_store):
    channel = Recorder()
    home_store.put("jobs", "1", exercise_job("running", progress=BUILDING))
    reporter = conversation.Reporter([channel], "Exercising Rust", pid=OTHER_LIVE_PID)
    reporter.refresh()
    reporter.ended = True
    the_persons_message = "hello"
    channel.send("1", the_persons_message)
    reporter.ended = False
    went_on = {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building", "resumed": time.time()}
    home_store.put("jobs", "1", exercise_job("running", progress=went_on))
    reporter.refresh()
    assert channel.log[-1][0] == "send" and "Exercising Rust" in channel.log[-1][3]
    assert any(entry[0] == "delete" for entry in channel.log)


@pytest.mark.spec("EXR-6")
def test_each_practice_project_shows_only_its_own_steps(make_model, fake_ollama, home_store, projects):
    Library.save("rust", RUST)
    exercise = Exercises.of("Rust")
    exercise["projects"].append(dict(CHOICE, folder=str(projects / "word-count"), step="done"))
    exercise["projects"].append(
        dict(CHOICE, task="A line counter in Rust", folder=str(projects / "line-count"), step="build")
    )
    runner = exercise_of(make_model, Builds())
    runner.tracker.change("old", steps=["🎯 Practice project chosen"] * 20)
    fake_ollama.reply(json.dumps(REFLECTION))
    runner.practise(exercise)
    assert runner.tracker.state["title"] == "Exercising Rust: line-count"
    assert runner.tracker.state["steps"] == ["🔨 Practice project built", "➕ Feature added", "📏 Measured"]


@pytest.mark.spec("EXR-6")
def test_the_conversation_adopts_an_exercise_a_conversation_before_it_started(
    home_store, tmp_path, monkeypatch
):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    followed = []
    monkeypatch.setattr(
        desk, "follow_exercise", lambda pid, title, ended, anew: followed.append((pid, title, anew))
    )
    reports_by_itself = {"title": "Exercising Rust", "exercise": True, "reports": True}
    home_store.put("jobs", "1", exercise_job("running", progress=reports_by_itself))
    desk.adopt_exercise()
    assert followed == []
    home_store.put("jobs", "1", exercise_job("running", progress=BUILDING))
    desk.adopt_exercise()
    assert followed == [(OTHER_LIVE_PID, "Exercising Rust", True)]


@pytest.mark.spec("EXR-6")
def test_an_exercise_that_ended_unstopped_goes_on_as_the_conversation_starts(
    home_store, tmp_path, monkeypatch
):
    desk = Desk([Recorder()], tmp_path)
    started = []
    monkeypatch.setattr(desk, "start_exercise", lambda words, title=None: started.append((words, title)))
    desk.adopt_exercise()
    its_job_killed_too = {
        "number": 4,
        "command": "exercise",
        "title": "exercise rust",
        "state": "running",
        "pid": 999999999,
        "arguments": ["exercise", "rust"],
        "updated": "2026-10-23 09:00",
    }
    home_store.put("jobs", "4", its_job_killed_too)
    desk.adopt_exercise()
    assert started == [(["resume", "#4"], "exercise rust")]


@pytest.mark.spec("EXR-6")
def test_an_adopted_exercise_takes_its_message_out_and_sends_it_at_the_bottom(home_store, tmp_path):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    conversation.keep_exercise_board({("rec", "1"): [7]})
    home_store.put("jobs", "1", exercise_job("running", progress=BUILDING))
    desk.follow_exercise(OTHER_LIVE_PID, "Exercising Rust", lambda: time.sleep(0.2), anew=True)
    after = channel.log[channel.log.index(("delete", "1", 7, "")) :]
    assert any(kind == "send" and "Exercising Rust" in text for kind, _, _, text in after)


@pytest.mark.spec("EXR-7")
def test_an_exercise_that_ends_before_it_keeps_a_tracker_shows_what_it_printed(home_store, tmp_path):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    log = tmp_path / "exercise.log"
    log.write_text("before\n")
    start = log.stat().st_size
    log.write_text("before\n'cobol' is not a programming language. Nothing done.\n")
    desk.follow_exercise(OTHER_LIVE_PID + 999999, "exercise cobol", lambda: None, (log, start))
    ends = time.monotonic() + 5
    while not channel.log or "Nothing done" not in channel.log[-1][3]:
        assert time.monotonic() < ends, channel.log
        time.sleep(0.05)
    assert "before" not in channel.log[-1][3]


@pytest.mark.spec("EXR-6")
def test_the_conversation_leaves_exercise_runs_alone(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    home_store.put("jobs", "1", exercise_job("running", progress=BUILDING))
    assert persons_runs() == []
    assert desk.status() == "🏋️ **Tinker** · Exercising Rust"


@pytest.mark.spec("EXR-7")
def test_a_stopped_exercise_stops_its_build_and_keeps_everything(
    make_model, home_store, projects, monkeypatch
):
    Library.save("rust", RUST)
    exercise = exercise_of(make_model, Builds())
    stopped_builds = []
    monkeypatch.setattr(exercise, "practise", lambda kept: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(exercise, "stop_build", lambda: stopped_builds.append(True))
    ending = exercise.run("rust")
    assert ending["status"] == "stopped"
    assert stopped_builds == [True] and home_store.get("exercise", "rust")["language"] == "Rust"
    assert exercise.tracker.state["exercise"]
    assert home_store.get("exercise", "rust")["left"]


@pytest.mark.spec("EXR-7")
def test_exercise_status_shows_each_project_measured(home_store, projects, capsys):
    exercise = Exercises.of("Rust")
    measures = {
        "status": "done",
        "minutes": 4.0,
        "calls": 9,
        "retried": 0,
        "tokens": 900,
        "passing": 3,
        "parked": 0,
        "points": 3,
        "readme_failures": 0,
        "slowest": [],
    }
    exercise["projects"].append(
        dict(CHOICE, folder=str(projects / "word-count"), step="done", measures=measures)
    )
    Exercises.save(exercise)
    exercising.show()
    out = capsys.readouterr().out
    assert (
        "Rust: stopped, 1 practice project(s) done" in out
        and "word-count: done, 4.0 min, 9 model calls" in out
    )
    for state in ("paused", "running"):
        home_store.put("jobs", "1", exercise_job(state))
        exercising.show()
        assert f"Rust: {state}, 1 practice project(s) done" in capsys.readouterr().out


@pytest.mark.spec("EXR-7")
def test_tinker_exercise_takes_a_language_or_status():
    assert Cli.parse(["exercise", "rust"]).exercise == "rust"
    assert Cli.parse(["exercise", "status"]).exercise == "status"
    with pytest.raises(SystemExit):
        Cli.parse(["exercise"])


@pytest.mark.spec("EXR-6")
def test_a_chat_never_waits_for_an_exercise(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    home_store.put("jobs", "1", exercise_job("running", progress=BUILDING))
    assert desk.running_now() == []


@pytest.mark.spec("EXR-7")
def test_every_exercise_keeps_one_message_started_stopped_or_resumed(home_store, tmp_path, monkeypatch):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    told = []
    monkeypatch.setattr(conversation, "signal_task", lambda pid, signum: told.append((pid, signum)))
    assert desk.stop_exercise().startswith("🤷 No exercise runs")
    home_store.put("jobs", "1", exercise_job("running", progress=BUILDING))
    first_ended = []
    desk.follow_exercise(
        OTHER_LIVE_PID, "Exercising Rust", lambda: first_ended.append(True) or time.sleep(0.2)
    )
    assert desk.stop_exercise() is None and told == [(OTHER_LIVE_PID, conversation.JOB_STOP_SIGNAL)]
    for _ in range(100):
        if desk.exercise_reporter is None:
            break
        time.sleep(0.05)
    the_next_exercise = "resume #4"
    desk.follow_exercise(OTHER_LIVE_PID, the_next_exercise, lambda: None)
    for _ in range(100):
        if desk.exercise_reporter is None:
            break
        time.sleep(0.05)
    sent = {entry[2] for entry in channel.log if entry[0] == "send" and "🔨 **" in entry[3]}
    deleted = {entry[2] for entry in channel.log if entry[0] == "delete"}
    assert first_ended and len(sent - deleted) == 1
    assert list(conversation.exercise_board().values()) == [[next(iter(sent - deleted))]]


@pytest.mark.spec("EXR-7")
def test_exercise_commands_that_switch_it_show_in_its_message_alone():
    assert conversation.exercise_switch("exercise rust") and conversation.exercise_switch(
        "/exercise python 3"
    )
    assert not conversation.exercise_switch("exercise status") and not conversation.exercise_switch(
        "exercise"
    )
