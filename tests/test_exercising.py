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
def test_the_exercise_shows_what_its_build_does_and_that_it_goes_on(make_model, home_store, quiet_person,
                                                                    monkeypatch, capsys):
    build = FakeBuild(polls=1)
    build.pid = ALIVE  # a build that runs: its tracker is kept
    exercise = Exercise(make_model(), Tracker("Exercising rust"), start=lambda arguments: build)
    exercise.on_to("build")
    home_store.put("running", str(ALIVE), {"doing": "💻 Writing the code", "live": "writing… 5s", "latest": "✅ Wrote a.py",
                                         "passing": [2, 5], "exercise": True})
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    exercise.built(lambda: ["task", "t"])
    state = exercise.tracker.state
    assert state["doing"] == "🔨 Building the practice project · 💻 Writing the code"
    assert (state["live"], state["latest"], state["passing"]) == ("writing… 5s", "✅ Wrote a.py", [2, 5])
    home_store.put("activity", "person", time.time())  # the person asks for something; Tinker is idle after
    monkeypatch.setattr(time, "sleep", lambda seconds: home_store.put("activity", "person", 0.0))
    exercise.wait_until_quiet()
    assert exercise.tracker.state["doing"].startswith("🔨 Building")
    assert "Tinker is idle: the exercise goes on." in capsys.readouterr().out
    assert exercise.tracker.state["resumed"]


@pytest.mark.spec("EXR-6")
def test_an_exercise_that_goes_on_by_itself_comes_to_the_bottom(home_store):
    channel = Recorder()
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"})
    reporter = conversation.Reporter([channel], "Exercising Rust", pid=ALIVE)
    reporter.refresh()
    reporter.ended = True  # no updates of its own meanwhile
    channel.send("1", "hello")  # the person's message: the exercise's is no longer the newest
    reporter.ended = False
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building",
                                           "resumed": time.time()})
    reporter.refresh()
    assert channel.log[-1][0] == "send" and "Exercising Rust" in channel.log[-1][3]
    assert any(entry[0] == "delete" for entry in channel.log)


@pytest.mark.spec("EXR-6")
def test_each_practice_project_shows_only_its_own_steps(make_model, fake_ollama, home_store, projects, quiet_person):
    Library.save("rust", RUST)
    exercise = Exercises.of("Rust")
    exercise["projects"].append(dict(CHOICE, folder=str(projects / "word-count"), step="done"))
    exercise["projects"].append(dict(CHOICE, task="A line counter in Rust", folder=str(projects / "line-count"),
                                     step="build"))
    runner = exercise_of(make_model, Builds())
    runner.tracker.change("old", steps=["🎯 Practice project chosen"] * 20)
    fake_ollama.reply(json.dumps(REFLECTION))
    runner.practise(exercise)
    assert runner.tracker.state["title"] == "Exercising Rust: line-count"
    assert runner.tracker.state["steps"] == ["🔨 Practice project built", "➕ Feature added", "📏 Measured"]


@pytest.mark.spec("EXR-6")
def test_the_conversation_adopts_an_exercise_a_conversation_before_it_started(home_store, tmp_path, monkeypatch):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    followed = []
    monkeypatch.setattr(desk, "follow_exercise", lambda pid, title, ended, anew: followed.append((pid, title, anew)))
    home_store.put("activity", "exercise", {"pid": ALIVE, "language": "Rust"})
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "reports": True})
    desk.adopt_exercise()
    assert followed == []  # one run from a terminal with chats reports by itself
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"})
    desk.adopt_exercise()
    assert followed == [(ALIVE, "Exercising Rust", True)]
    assert "runs already" in desk.start_exercise(["exercise", "resume", "rust"])  # one at a time: nothing started


@pytest.mark.spec("EXR-6")
def test_an_exercise_that_ended_unstopped_goes_on_as_the_conversation_starts(home_store, tmp_path, monkeypatch):
    desk = Desk([Recorder()], tmp_path)
    started = []
    monkeypatch.setattr(desk, "start_exercise", started.append)
    home_store.put("activity", "exercise", {"pid": 999999999, "language": "Rust"})  # killed: never released
    desk.adopt_exercise()
    assert started == [["exercise", "resume", "Rust"]]


@pytest.mark.spec("EXR-6")
def test_an_adopted_exercise_takes_its_message_out_and_sends_it_at_the_bottom(home_store, tmp_path):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    conversation.keep_exercise_board({("rec", "1"): [7]})
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"})
    desk.follow_exercise(ALIVE, "Exercising Rust", lambda: time.sleep(0.2), anew=True)
    after = channel.log[channel.log.index(("delete", "1", 7, "")):]
    assert any(kind == "send" and "Exercising Rust" in text for kind, _, _, text in after)


@pytest.mark.spec("EXR-7")
def test_an_exercise_that_ends_before_it_keeps_a_tracker_shows_what_it_printed(home_store, tmp_path):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    log = tmp_path / "exercise.log"
    log.write_text("before\n")
    start = log.stat().st_size
    log.write_text("before\n'cobol' is not a programming language. Nothing done.\n")
    desk.follow_exercise(ALIVE + 999999, "exercise cobol", lambda: None, (log, start))
    ends = time.monotonic() + 5
    while not channel.log or "Nothing done" not in channel.log[-1][3]:
        assert time.monotonic() < ends, channel.log
        time.sleep(0.05)
    assert "before" not in channel.log[-1][3]


@pytest.mark.spec("EXR-6")
def test_the_exercises_own_model_calls_give_way_at_once_and_are_asked_again(make_model, home_store, quiet_person,
                                                                           monkeypatch):
    exercise = Exercise(make_model(), Tracker("Exercising rust"), start=Builds())
    monkeypatch.setattr(signal, "default_pause", signal.signal(conversation.PAUSE, exercise.give_way), raising=False)
    calls = []

    def ask(*call, **options):
        calls.append(call[2])
        if len(calls) == 1:
            Home.person_seen()  # the person asks for something while the call runs
            time.sleep(5)  # the call is interrupted before it ends
            raise AssertionError("not interrupted")
        return {"ok": True}
    monkeypatch.setattr(exercise.model, "ask_until_usable", ask)
    monkeypatch.setattr(exercising, "EXERCISE_LOOK", 0.05)
    monkeypatch.setattr(exercise, "wait_until_quiet", lambda: home_store.put("activity", "person", 0.0))
    assert exercise.asked("system", [], "exercise: next project") == {"ok": True}
    assert calls == ["exercise: next project"] * 2 and not exercise.calling
    signal.signal(conversation.PAUSE, signal.default_pause)


@pytest.mark.spec("EXR-6")
def test_the_conversation_marks_the_person_and_leaves_exercise_runs_alone(home_store, tmp_path, monkeypatch):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    monkeypatch.setattr(desk, "tinker", lambda words, timeout=120: "ok")
    for looking in ("exercise status", "help exercise", "help", "runs"):
        desk.hear(channel, Message("1", looking))
    assert Home.person_last_seen() == 0.0
    desk.hear(channel, Message("1", "hello there"))
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
    assert ending["status"] == "stopped" and ending["next"] == [("Go on", "tinker exercise resume Rust")]
    assert stopped_builds == [True] and home_store.get("exercise", "rust")["language"] == "Rust"
    assert home_store.get("activity", "exercise") is None and exercise.tracker.state["exercise"]
    assert home_store.get("exercise", "rust")["left"]


@pytest.mark.spec("EXR-7")
def test_only_one_exercise_runs_at_a_time(home_store):
    home_store.put("activity", "exercise", {"pid": ALIVE, "language": "Go"})
    with pytest.raises(SystemExit, match="An exercise of Go runs already"):
        Exercises.claim(Exercises.of("Rust"))


@pytest.mark.spec("EXR-7")
def test_resume_goes_on_with_the_language_named_else_the_one_left_last(home_store):
    with pytest.raises(SystemExit, match="No exercise to resume yet"):
        Exercises.resumed([])
    for language, left in (("Rust", "2026-10-22 09:00"), ("Python", "2026-10-22 10:00"), ("Go", None)):
        exercise = Exercises.of(language)
        exercise.update(started="2026-10-21 08:00", left=left)
        Exercises.save(exercise)
    assert Exercises.resumed([]) == "Python"
    assert Exercises.resumed(["rust"]) == "rust"


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
    assert Cli.parse(["exercise", "resume", "rust"]).exercise == "resume rust"
    with pytest.raises(SystemExit):
        Cli.parse(["exercise"])


@pytest.mark.spec("EXR-6")
def test_a_chat_never_waits_for_an_exercise(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"})
    assert desk.now() == []


@pytest.mark.spec("EXR-7")
def test_every_exercise_keeps_one_message_started_stopped_or_resumed(home_store, tmp_path, monkeypatch):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    told = []
    monkeypatch.setattr(conversation, "signal_task", lambda pid, signum: told.append((pid, signum)))
    assert desk.stop_exercise().startswith("🤷 No exercise runs")
    home_store.put("activity", "exercise", {"pid": ALIVE, "language": "Rust"})
    home_store.put("running", str(ALIVE), {"title": "Exercising Rust", "exercise": True, "doing": "🔨 Building"})
    first_ended = []
    desk.follow_exercise(ALIVE, "Exercising Rust", lambda: first_ended.append(True) or time.sleep(0.2))
    assert desk.stop_exercise() is None and told == [(ALIVE, conversation.STOP)]
    for _ in range(100):
        if desk.exercising is None:
            break
        time.sleep(0.05)
    desk.follow_exercise(ALIVE, "exercise resume", lambda: None)  # the next exercise: the same message
    for _ in range(100):
        if desk.exercising is None:
            break
        time.sleep(0.05)
    sent = {entry[2] for entry in channel.log if entry[0] == "send" and "🔨 **" in entry[3]}
    deleted = {entry[2] for entry in channel.log if entry[0] == "delete"}
    assert first_ended and len(sent - deleted) == 1
    assert list(conversation.exercise_board().values()) == [[next(iter(sent - deleted))]]


def test_exercise_commands_that_switch_it_show_in_its_message_alone():
    assert conversation.exercise_switch("exercise stop") and conversation.exercise_switch("/exercise resume rust")
    assert not conversation.exercise_switch("exercise status") and not conversation.exercise_switch("exercise")
