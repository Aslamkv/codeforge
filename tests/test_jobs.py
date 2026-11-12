import datetime
import os
import signal
import threading
import time

import pytest
from conftest import add_back
from test_conversation import Recorder, everyone

from tinkerkit import cli, conversation, exercising, jobs
from tinkerkit.cli import Cli, Commands
from tinkerkit.conversation import Desk, Message
from tinkerkit.jobs import JOBS, Jobs, Queue, Runner, archived, began_at, calls_log, is_job, journaled, today
from tinkerkit.knowledge import Learner, Researcher
from tinkerkit.learning import SlotLearner
from tinkerkit.model import Ollama
from tinkerkit.project import Tracker
from tinkerkit.settings import DREAM_IDLE_SECONDS, JOB_START_TRIES, JOB_STOP_SIGNAL

DEAD_PID = 999999999


def put_job(home_store, number, **fields):
    job = {
        "number": number,
        "command": "learn",
        "title": "learn go",
        "state": "stopped",
        "pid": DEAD_PID,
        "arguments": ["learn", "go"],
        "started": "x",
        "updated": "x",
        **fields,
    }
    home_store.put("jobs", str(number), job)


def research_job_started_on_jan_2(home_store):
    put_job(
        home_store,
        1,
        command="research",
        title="research go",
        arguments=["research", "go"],
        today="2026-01-02",
    )


def stopped_job(home_store, argv, answers=()):
    with pytest.raises(KeyboardInterrupt), Jobs.run_here(argv):
        for request, answer in answers:
            journaled(request, lambda answer=answer: answer)
        raise KeyboardInterrupt
    return Jobs.get(1)


def asked(answer, calls):
    def ask():
        calls.append(answer)
        return answer

    return ask


@pytest.fixture
def told(monkeypatch):
    told = []
    monkeypatch.setattr(jobs, "signal_job", lambda job: told.append(job["number"]))
    return told


@pytest.mark.spec("JOB-1")
def test_long_commands_are_jobs_and_the_rest_are_not():
    for argv in (
        ["task", "a", "calculator"],
        ["resume", "@calc"],
        ["add", "x"],
        ["learn", "rust"],
        ["ask", "q"],
        ["teach", "t"],
        ["research", "python"],
        ["exercise", "rust"],
        ["confirm"],
        ["amend", "x"],
        ["model", "calibrate", "qwen3"],
        ["ask", "status", "of", "python"],
        ["chat", "hello"],
        ["command", "list", "files"],
        ["dream", "run"],
    ):
        assert is_job(argv), argv
    for argv in (
        [],
        ["help"],
        ["model", "calibrate"],
        ["runs"],
        ["jobs"],
        ["stop"],
        ["exercise", "status"],
        ["learn", "ll", "ls -l"],
        ["read", "list"],
        ["dream"],
        ["dream", "status"],
        ["task", "--help"],
    ):
        assert not is_job(argv), argv


@pytest.mark.spec("JOB-1")
def test_an_exercises_build_is_not_a_job_of_its_own(monkeypatch):
    started = []
    monkeypatch.setattr(exercising, "tinker_process", lambda arguments, **env: started.append(env))
    monkeypatch.setattr(Jobs, "current", {"number": 7})
    exercising.start_tinker(["task", "a", "calculator"])
    monkeypatch.setenv("TINKER_EXERCISE", started[0]["TINKER_EXERCISE"])
    assert started[0]["TINKER_EXERCISE"] == "7" and not is_job(["task", "a", "calculator"])


@pytest.mark.spec("JOB-1")
def test_a_job_is_numbered_and_kept_while_it_runs(home_store):
    with Jobs.run_here(["learn", "rust"]):
        job = Jobs.current
        assert (
            job["state"] == "running" and job["pid"] == os.getpid() and job["arguments"] == ["learn", "rust"]
        )
        assert job["today"] == datetime.date.today().isoformat() and job["title"] == "learn rust"
        assert signal.getsignal(JOB_STOP_SIGNAL) is jobs.interrupted
    with Jobs.run_here(["task", "a", "calculator", "--confirm"]):
        assert Jobs.current["number"] == 2 and Jobs.current["title"] == "task a calculator"
    with Jobs.running(["runs"]):
        assert Jobs.current is None


@pytest.mark.spec("JOB-2")
def test_an_interrupted_job_is_stopped_with_its_journal_kept(home_store):
    job = stopped_job(home_store, ["research", "python"], [({"search": "python"}, ["found"])])
    assert job["state"] == "stopped" and len(home_store.entries("journal #1")) == 1
    with Jobs.run_here(["research", "python"]):
        add_back(Jobs.current["number"])
        journaled({"search": "python"}, lambda: ["found"])
    assert Jobs.get(2)["state"] == "finished" and len(home_store.entries("journal #2")) == 0
    assert Queue.ordered() == []


@pytest.mark.spec("JOB-2")
def test_a_tracker_that_ends_stopped_stops_the_job_and_says_how_to_go_on(home_store):
    tracker = Tracker("Exercising rust")
    with Jobs.run_here(["exercise", "rust"]):
        tracker.end({"status": "stopped", "headline": "stopped", "next": [("See", "x")]}, 1.0)
    assert tracker.state["ended"]["next"] == [("See", "x"), ("Go on", "tinker resume #1")]
    assert Jobs.get(1)["state"] == "stopped"
    outside = Tracker("outside a job")
    outside.end({"status": "stopped"}, 1.0)
    assert "next" not in outside.state["ended"]


@pytest.mark.spec("JOB-2")
def test_a_running_job_out_of_the_queue_is_stopped_and_one_in_it_whose_process_is_gone_is_lost(home_store):
    put_job(home_store, 3, state="running")
    assert (
        Jobs.get(3)["state"] == "stopped" and Jobs.listed("stopped")[0]["number"] == 3 and Jobs.lost() == []
    )
    add_back(3)
    assert Jobs.get(3)["state"] == "running" and [job["number"] for job in Jobs.lost()] == [3]
    stopped_job(home_store, ["learn", "rust"])
    assert [job["number"] for job in Jobs.lost()] == [3]


@pytest.mark.spec("JOB-2")
def test_a_job_whose_process_id_went_to_another_process_is_lost(home_store):
    mine = {"number": 4, "command": "learn", "title": "learn go", "state": "running", "pid": os.getpid()}
    home_store.put("jobs", "4", dict(mine, pid_began=began_at(os.getpid())))
    add_back(4)
    assert Jobs.lost() == []
    home_store.put("jobs", "4", dict(mine, pid_began="Sat Oct 24 16:32:00 2026"))
    assert Jobs.get(4)["state"] == "running" and [job["number"] for job in Jobs.lost()] == [4]


@pytest.mark.spec("JOB-3")
def test_a_resumed_job_is_answered_from_its_journal_and_asks_only_what_is_new(home_store):
    kept_twice, never_kept = {"search": "rust"}, {"search": "rust docs"}
    stopped_job(home_store, ["learn", "rust"], [(kept_twice, ["a"]), (kept_twice, ["b"])])
    calls = []
    with Jobs.run_here(Jobs.arguments(["resume", "#1"])):
        assert Jobs.current["number"] == 1
        assert journaled(kept_twice, asked(["new"], calls)) == ["a"]
        assert journaled(kept_twice, asked(["new"], calls)) == ["b"]
        assert journaled(kept_twice, asked(["c"], calls)) == ["c"]
        assert journaled(never_kept, asked(["d"], calls)) == ["d"]
    assert calls == [["c"], ["d"]] and Jobs.get(1)["state"] == "finished"


@pytest.mark.spec("JOB-3")
def test_outside_a_job_and_after_a_failure_it_asks(home_store):
    calls = []
    assert journaled({"search": "x"}, asked(["a"], calls)) == ["a"] and journaled(
        {"search": "x"}, asked(["a"], calls)
    )
    assert len(calls) == 2
    failed_search = ({"search": "x"}, None)
    stopped_job(home_store, ["research", "x"], [failed_search])
    assert len(home_store.entries("journal #1")) == 0


@pytest.mark.spec("JOB-3")
def test_model_calls_go_through_the_journal(home_store, monkeypatch):
    calls = []

    def streamed(name, messages, *rest):
        calls.append(messages)
        return {"message": {"content": "hi"}}

    monkeypatch.setattr(Ollama, "streamed", staticmethod(streamed))
    say_hi = [{"role": "user", "content": "hi"}]
    with pytest.raises(KeyboardInterrupt), Jobs.run_here(["ask", "hi"]):
        Ollama.chat("m", say_hi, 0, {})
        raise KeyboardInterrupt
    calls.clear()
    a_different_keep_alive, an_unload = 300, []
    with Jobs.run_here(Jobs.arguments(["resume", "#1"])):
        assert Ollama.chat("m", say_hi, a_different_keep_alive, {})["message"]["content"] == "hi"
        Ollama.chat("m", an_unload, 0, None)
    assert calls == [an_unload]


@pytest.mark.spec("JOB-4")
def test_a_job_says_the_day_it_started(home_store):
    assert today() == datetime.date.today()
    put_job(home_store, 1, title="learn rust", arguments=["learn", "rust"], today="2026-01-02")
    with Jobs.run_here(Jobs.arguments(["resume", "#1"])):
        assert today() == datetime.date(2026, 1, 2)


@pytest.mark.spec("JOB-5")
def test_resume_runs_the_jobs_own_command_line(home_store):
    this_folders_run = ["resume"]
    assert Jobs.arguments(this_folders_run) == this_folders_run
    assert Jobs.arguments(["resume", "@calc"]) == ["resume", "@calc"]
    assert Jobs.arguments(["task", "x"]) == ["task", "x"]
    with pytest.raises(SystemExit, match="No job #4"):
        Jobs.arguments(["resume", "#4"])
    stopped_job(home_store, ["learn", "rust"])
    assert Jobs.arguments(["resume"]) == ["learn", "rust"] and Jobs.current["number"] == 1
    Jobs.current = None
    home_store.put(
        "jobs", "1", dict(Jobs.get(1), state="running", pid=os.getppid(), pid_began=began_at(os.getppid()))
    )
    Queue.add_front(1)
    with pytest.raises(SystemExit, match="Job #1 is running"):
        Jobs.arguments(["resume", "#1"])
    put_job(home_store, 2, state="queued", arguments=["learn", "go"])
    add_back(2)
    assert Jobs.get(2)["state"] == "queued"
    assert Jobs.arguments(["resume", "#2"]) == ["learn", "go"] and Jobs.current["number"] == 2
    Jobs.current = None
    Queue.remove(2)
    Queue.remove(1)
    home_store.put("jobs", "1", dict(Jobs.get(1), state="finished"))
    with pytest.raises(SystemExit, match="Job #1 is finished"):
        Jobs.arguments(["resume", "#1"])


@pytest.mark.spec("JOB-5")
def test_confirm_builds_the_folders_plan_and_never_a_stopped_job(home_store):
    stopped_job(home_store, ["learn", "rust"])
    assert Jobs.arguments(["confirm"]) == ["confirm"] and Jobs.current is None
    assert Cli.parse(["confirm"]).command == "resume"


@pytest.mark.spec("JOB-5")
def test_a_tasks_job_is_resumed_as_its_run(home_store):
    with Jobs.run_here(["task", "a", "calculator"]):
        Jobs.retarget(["resume", "@a1b2"])
        assert Jobs.get(1)["arguments"] == ["resume", "@a1b2"] and Jobs.get(1)["title"] == "task a calculator"
    Jobs.retarget(["resume", "@other"])
    assert Jobs.get(1)["arguments"] == ["resume", "@a1b2"]


def keep_jobs(home_store, states):
    for number, state in enumerate(states, 1):
        put_job(
            home_store,
            number,
            title=f"learn topic {number}",
            state=state,
            pid=os.getpid() if state == "running" else DEAD_PID,
            arguments=["learn", f"topic {number}"],
            started="2026-10-23 09:00",
            updated="2026-10-23 09:00",
        )
    for number in reversed(range(1, len(states) + 1)):
        if states[number - 1] == "running":
            add_back(number)


@pytest.mark.spec("JOB-6")
def test_jobs_lists_running_then_stopped_newest_first(home_store, capsys):
    Commands.list_jobs(None, 1)
    assert "No running, queued or stopped jobs." in capsys.readouterr().out
    keep_jobs(home_store, ["stopped", "running", "finished", "stopped", "running"])
    assert [job["number"] for job in Jobs.listed()] == [5, 2, 4, 1]
    assert [job["number"] for job in Jobs.listed("finished")] == [3]
    Commands.list_jobs("stopped", 1)
    out = capsys.readouterr().out
    assert out.index("#4") < out.index("#1") and "learn topic 4" in out and "#5" not in out


@pytest.mark.spec("JOB-6")
def test_jobs_come_a_page_at_a_time(home_store, capsys):
    keep_jobs(home_store, ["finished"] * 23)
    Commands.list_jobs("finished", 1)
    out = capsys.readouterr().out
    assert "#23" in out and "#14" in out and "#13" not in out and "tinker jobs finished 2 for more" in out
    Commands.list_jobs("finished", 3)
    out = capsys.readouterr().out
    assert "#3" in out and "#1 " in out and "Page 3 of 3." in out


@pytest.mark.spec("JOB-6")
def test_tinker_jobs_takes_a_state_and_a_page():
    assert Cli.parse(["jobs"]).jobs == (None, 1)
    assert Cli.parse(["jobs", "stopped"]).jobs == ("stopped", 1)
    assert Cli.parse(["jobs", "finished", "2"]).jobs == ("finished", 2)
    assert Cli.parse(["jobs", "3"]).jobs == (None, 3)
    for words in (["jobs", "done"], ["jobs", "0"], ["jobs", "running", "1", "2"]):
        with pytest.raises(SystemExit):
            Cli.parse(words)


@pytest.mark.spec("JOB-7")
def test_stop_tells_a_job_or_the_one_that_runs(home_store, monkeypatch, capsys):
    told = []
    monkeypatch.setattr(Jobs, "stop", staticmethod(lambda job: told.append(job["number"])))
    keep_jobs(home_store, ["running", "stopped", "running"])
    home_store.put("jobs", "3", dict(Jobs.get(3), command="exercise"))
    the_queues_head = [3]
    Commands.stop(None)
    assert told == the_queues_head
    Commands.stop(3)
    assert told == the_queues_head + [3] and "tinker resume #3 goes on" in capsys.readouterr().out
    with pytest.raises(SystemExit, match=r"Job #2 is not in the queue \(tinker queue list shows it\)"):
        Commands.stop(2)
    assert Cli.parse(["stop", "#3"]).stop == 3 and Cli.parse(["stop"]).stop is None
    with pytest.raises(SystemExit):
        Cli.parse(["stop", "3"])


@pytest.mark.spec("JOB-7")
def test_queue_lists_its_jobs_head_first_and_stops_one_or_all(home_store, told, capsys):
    Commands.queue("list")
    assert "The queue is empty." in capsys.readouterr().out
    keep_jobs(home_store, ["running", "stopped", "running"])
    put_job(home_store, 4, title="learn topic 4", state="queued")
    add_back(4)
    Commands.queue("list")
    out = capsys.readouterr().out
    assert (
        out.index("#3 running learn topic 3") < out.index("#1 queued") < out.index("#4 queued learn topic 4")
    )
    assert "#2" not in out
    Commands.queue(1)
    assert Queue.ordered() == [3, 4] and told == [1]
    with pytest.raises(SystemExit, match="Job #2 is not in the queue"):
        Commands.queue(2)
    Commands.queue("all")
    assert Queue.ordered() == [] and told == [1, 3]
    assert "Stopping #3" in capsys.readouterr().out
    assert Cli.parse(["queue", "list"]).queue == "list"
    assert Cli.parse(["queue", "stop", "#4"]).queue == 4
    assert Cli.parse(["queue", "stop", "all"]).queue == "all"
    for words in (
        ["queue"],
        ["queue", "stop"],
        ["queue", "stop", "4"],
        ["queue", "list", "2"],
        ["queue", "go"],
    ):
        with pytest.raises(SystemExit):
            Cli.parse(words)


@pytest.mark.spec("JOB-11")
def test_clear_jobs_archives_them_listed_apart_and_never_resumed(home_store, capsys):
    keep_jobs(home_store, ["stopped", "finished", "running", "stopped"])
    home_store.append("journal #1", {"key": "k", "answer": "a"})
    with pytest.raises(SystemExit, match="Nothing archived: run tinker clear jobs #1"):
        Cli.cleared(["jobs", "#1"], yes=False)
    Cli.cleared(["jobs", "#1"], yes=True)
    assert Jobs.get(1)["state"] == "archived" and Jobs.get(1)["archived_from"] == "stopped"
    assert len(home_store.entries("journal #1")) == 0
    assert [job["number"] for job in Jobs.listed()] == [3, 4] and [
        job["number"] for job in Jobs.listed("archived")
    ] == [1]
    assert Jobs.resumable(1) == (None, "Job #1 is archived: there is nothing left to resume.")
    for words, why in (
        (["jobs", "#3"], "Job #3 is running: stop it first"),
        (["jobs", "#1"], "archived already"),
        (["jobs", "#9"], "No job #9"),
        (["jobs", "memory"], "job numbers alone"),
    ):
        with pytest.raises(SystemExit, match=why):
            Cli.cleared(words, yes=True)
    every_stopped_and_finished = ["jobs"]
    Cli.cleared(every_stopped_and_finished, yes=True)
    assert [job["number"] for job in Jobs.listed("archived")] == [4, 2, 1] and Jobs.get(3)[
        "state"
    ] == "running"
    Cli.cleared(["jobs"], yes=True)
    assert "Nothing to clear: no job is stopped or finished." in capsys.readouterr().out
    assert Cli.parse(["jobs", "archived"]).jobs == ("archived", 1)


@pytest.mark.spec("JOB-7")
def test_the_conversation_stops_and_resumes_jobs(home_store, tmp_path, monkeypatch):
    desk = Desk([Recorder()], tmp_path)
    started, told = [], []
    monkeypatch.setattr(
        desk,
        "start_task",
        lambda audience, arguments, folder, title, *rest, **kwargs: started.append((arguments, title)),
    )
    monkeypatch.setattr(
        desk, "start_exercise", lambda audience, words, title=None: started.append((words, title))
    )
    monkeypatch.setattr(desk, "stop_exercise", lambda: told.append("exercise"))
    monkeypatch.setattr(Jobs, "stop", staticmethod(lambda job: told.append(job["number"])))
    keep_jobs(home_store, ["stopped", "running", "stopped", "running"])
    home_store.put("jobs", "3", dict(Jobs.get(3), command="exercise", title="exercise rust"))
    home_store.put("jobs", "4", dict(Jobs.get(4), command="exercise"))
    answer = lambda text: desk.answer(Message("1", text), desk.channels[0])
    assert (
        answer("stop #2").startswith("⏹ **Stopping #2**")
        and answer("stop #4") is None
        and told == [2, "exercise"]
    )
    assert answer("stop") is None and told == [2, "exercise", "exercise"]
    answer("resume #1")
    answer("resume #3")
    answer("resume @calc")
    assert started == [
        (["resume", "#1"], "learn topic 1"),
        (["resume", "#3"], "exercise rust"),
        (["resume", "@calc"], "resume @calc"),
    ]
    assert answer("stop #1") == "🤷 Job #1 is not in the queue (`/queue list` shows it)."


@pytest.mark.spec("JOB-7")
def test_the_conversation_lists_the_queue_and_stops_one_or_all(home_store, tmp_path, told):
    desk = Desk([Recorder()], tmp_path)
    answer = lambda text: desk.answer(Message("1", text), desk.channels[0])
    assert answer("queue list") == "The queue is empty."
    keep_jobs(home_store, ["running", "stopped", "running"])
    put_job(home_store, 4, title="learn topic 4", state="queued")
    add_back(4)
    listed = answer("queue list")
    assert listed.index("#3 running") < listed.index("#1 queued") < listed.index("#4 queued learn topic 4")
    assert "#2" not in listed
    assert answer("queue stop #1").startswith("⏹ **Stopping #1**")
    assert Queue.ordered() == [3, 4] and told == [1]
    assert answer("queue stop #2") == "🤷 Job #2 is not in the queue (`/queue list` shows it)."
    stopped_all = answer("queue stop all")
    assert Queue.ordered() == [] and told == [1, 3] and "Stopping #3" in stopped_all
    for text in ("queue", "queue stop", "queue stop 4", "queue list 2", "queue go"):
        assert answer(text).startswith("✍️ Write `/queue")


@pytest.mark.spec("JOB-4")
def test_a_research_job_frames_its_topic_on_the_day_the_job_started(home_store, monkeypatch):
    research_job_started_on_jan_2(home_store)
    framed = {"name": "Go", "summary": "", "questions": [{"question": "Which release?", "queries": ["go"]}]}
    given = []
    monkeypatch.setattr(
        Learner,
        "saved_or_asked",
        lambda self, known, read, system, parts, *rest: given.extend(parts) or (framed, True),
    )
    with Jobs.run_here(Jobs.arguments(["resume", "#1"])):
        Researcher(Learner(None, None, None)).frame("go", None)
    assert next(part for part in given if part.title == "Today").text == "2026-01-02"


@pytest.mark.spec("JOB-8")
def test_the_queue_keeps_job_numbers_in_order_and_a_new_front_stops_the_head(home_store, told):
    put_job(home_store, 1)
    put_job(home_store, 2)
    add_back(1)
    add_back(3)
    assert Queue.ordered() == [1, 3] and Queue.head() == 1 and told == []
    Queue.add_front(2)
    assert Queue.ordered() == [2, 1, 3] and told == [1]
    Queue.add_front(3)
    assert Queue.ordered() == [3, 2, 1] and told == [1, 2]
    Queue.remove(3)
    Queue.remove(7)
    assert Queue.ordered() == [2, 1] and Queue.head() == 2
    Queue.remove(2)
    Queue.remove(1)
    assert Queue.ordered() == [] and Queue.head() is None


@pytest.mark.spec("JOB-2", "JOB-8")
def test_a_jobs_state_follows_the_queue_and_a_queued_job_whose_process_is_gone_stays_queued(home_store):
    alive_now = {"pid": os.getpid(), "pid_began": began_at(os.getpid())}
    put_job(home_store, 1, state="running")
    put_job(home_store, 2, state="running", **alive_now)
    put_job(home_store, 3, state="running", **alive_now)
    put_job(home_store, 4, state="finished")
    add_back(3)
    add_back(1)
    add_back(2)
    assert [Jobs.get(number)["state"] for number in (1, 2, 3, 4)] == [
        "queued",
        "queued",
        "running",
        "finished",
    ]
    assert [job["number"] for job in Jobs.lost()] == [1]
    Queue.remove(1)
    assert Jobs.get(1)["state"] == "stopped"
    assert [job["number"] for job in Jobs.listed()] == [3, 2, 1]
    assert [job["number"] for job in Jobs.listed("queued")] == [2]


@pytest.mark.spec("JOB-2", "JOB-8")
def test_a_job_pushed_back_says_so_and_stays_queued(home_store, monkeypatch):
    monkeypatch.setattr(jobs, "signal_job", lambda job: None)
    put_job(home_store, 5, state="queued")
    tracker = Tracker("Learning go")
    with pytest.raises(KeyboardInterrupt), Jobs.run_here(["learn", "go"]):
        add_back(1)
        Queue.add_front(5)
        tracker.end({"status": "stopped", "headline": "Learning go did not finish"}, 1.0)
        raise KeyboardInterrupt
    ended = tracker.state["ended"]
    assert (
        ended["status"] == "paused"
        and ended["headline"] == "Paused for job #5: it goes on when its turn comes"
    )
    assert tracker.state["doing"].startswith("⏸ Paused for job #5") and "next" not in ended
    assert (
        jobs.JOBS.get(1)["state"] == "queued"
        and Jobs.get(1)["state"] == "queued"
        and Queue.ordered() == [5, 1]
    )
    Queue.remove(1)
    assert Jobs.get(1)["state"] == "stopped"


@pytest.mark.spec("JOB-2")
def test_an_interrupted_head_stays_at_the_head_and_a_stopped_job_leaves_the_queue(home_store, told):
    with pytest.raises(KeyboardInterrupt), Jobs.run_here(["learn", "go"]):
        add_back(1)
        raise KeyboardInterrupt
    assert Queue.ordered() == [1] and Jobs.get(1)["state"] == "running" and told == []
    put_job(home_store, 2, pid=os.getpid(), pid_began=began_at(os.getpid()))
    add_back(2)
    Jobs.stop(Jobs.get(1))
    Jobs.stop(Jobs.get(2))
    assert Queue.ordered() == [] and told == [1, 2]


@pytest.mark.spec("JOB-8")
def test_the_conversation_starts_a_task_while_another_runs(home_store, tmp_path, monkeypatch):
    desk = Desk([Recorder()], tmp_path)
    spawned = []

    class Running:
        pid, stdout = os.getpid(), []

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(desk, "spawn", lambda arguments: spawned.append(arguments) or Running())
    monkeypatch.setattr(desk, "follow", lambda process, reporter, task: None)
    assert desk.start_task(everyone(desk.channels), ["learn", "go"], tmp_path, "learn go") is None
    assert (
        desk.start_task(everyone(desk.channels), ["exercise", "python"], tmp_path, "exercise python") is None
    )
    assert spawned == [["learn", "go"], ["exercise", "python"]]
    desk.reporter.ended = True


@pytest.mark.spec("JOB-6")
def test_queued_jobs_are_listed_between_running_and_stopped(home_store):
    keep_jobs(home_store, ["stopped", "running"])
    put_job(home_store, 3, title="learn 3", state="queued", pid=os.getpid(), arguments=["learn", "3"])
    add_back(3)
    assert [job["number"] for job in Jobs.listed()] == [2, 3, 1] and Cli.parse(["jobs", "queued"]).jobs == (
        "queued",
        1,
    )


@pytest.mark.spec("JOB-4")
def test_a_learning_plan_says_the_day_the_job_started(home_store):
    research_job_started_on_jan_2(home_store)
    learner = SlotLearner.__new__(SlotLearner)
    learner.topic = "go"
    with Jobs.run_here(Jobs.arguments(["resume", "#1"])):
        parts = learner.plan_parts([])
    assert next(part for part in parts if part.title == "Today").text == "2026-01-02"


@pytest.mark.spec("JOB-5")
def test_a_job_that_goes_on_starts_its_tracker_resumed(home_store):
    assert Tracker("new").state["resumed"] is None
    stopped_job(home_store, ["learn", "go"])
    Jobs.arguments(["resume", "#1"])
    assert Tracker("Learning go").state["resumed"]


@pytest.mark.spec("JOB-1")
def test_tinker_chat_answers_without_pausing_anything_itself(monkeypatch, capsys):
    paused = []
    monkeypatch.setattr(Queue, "add_front", lambda number: paused.append(number))
    monkeypatch.setattr(conversation, "commands_brief", lambda: "cmds")
    conversation.one_shot("chat", "hello")
    assert paused == []


@pytest.mark.spec("JOB-8", "CNV-19")
def test_an_answer_runs_at_the_front_and_pushes_back_the_head(home_store, told):
    Jobs.save(
        dict(Jobs.new(["learn", "rust"]), state="running", pid=os.getpid(), pid_began=began_at(os.getpid()))
    )
    add_back(1)
    with jobs.answering():
        assert Queue.ordered() == [2, 1] and told == [1]
        assert Jobs.get(2)["state"] == "running" and Jobs.get(2)["pid"] == os.getpid()
        assert Runner.startable() is None
    assert Queue.ordered() == [1] and Jobs.get(2)["state"] == "finished"


@pytest.mark.spec("JOB-7")
def test_a_stopped_answer_here_is_told_without_a_signal_and_a_pushed_back_one_goes_on(
    home_store, monkeypatch
):
    real_kill = os.kill
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: real_kill(pid, sig) if sig == 0 else pytest.fail(f"signalled {pid} {sig}"),
    )
    with jobs.answering():
        stopped = jobs.answer_stopped()
        Queue.add_front(Jobs.save(Jobs.new(["chat"]))["number"])
        assert not stopped.is_set()
        Jobs.stop(Jobs.get(1))
        assert stopped.is_set()


@pytest.mark.spec("JOB-7")
def test_a_stop_from_another_process_ends_only_answers_out_of_the_queue(home_store):
    with jobs.answering():
        stopped = jobs.answer_stopped()
        jobs.stop_answers(JOB_STOP_SIGNAL, None)
        assert not stopped.is_set()
        Queue.remove(1)
        jobs.stop_answers(JOB_STOP_SIGNAL, None)
        assert stopped.is_set()


@pytest.mark.spec("CLI-6")
def test_a_research_job_says_its_steps_and_ends_with_its_answer(home_store, monkeypatch):
    entry = {
        "name": "Go",
        "summary": "A language.",
        "questions": [{"question": "Which release?"}],
        "findings": [{"question": 1, "answer": "Go 1.25.", "confidence": "high", "facts": [1]}],
        "facts": [{"text": "Go 1.25 is out", "source": "https://go.dev"}],
    }

    class Model:
        def unload(self):
            pass

    def research(self, topic):
        for step in ("frame", "investigate", "keep"):
            self.learner.step(step)
        return entry

    monkeypatch.setattr(
        Commands, "learning_tools", staticmethod(lambda option, request: (Model(), None, None))
    )
    monkeypatch.setattr(cli.Models, "remember", staticmethod(lambda model: None))
    monkeypatch.setattr(Researcher, "research", research)
    with Jobs.run_here(["research", "go"]):
        Commands(Cli.parse(["research", "go"])).run()
    state = Jobs.get(1)["progress"]
    assert state["title"] == "Researching go" and len(state["steps"]) == 2 and state["step"] == "keep"
    assert state["ended"]["status"] == "done" and state["ended"]["text"] == Researcher.answer(entry)


@pytest.mark.spec("EXR-7")
def test_an_exercise_paused_for_another_hands_it_its_message(home_store, tmp_path):
    channel = Recorder()
    desk = Desk([channel], tmp_path)
    home_store.put(
        "jobs",
        "1",
        {
            "number": 1,
            "command": "exercise",
            "state": "running",
            "pid": os.getpid(),
            "progress": {"title": "Exercising Rust", "exercise": True, "doing": "🔨"},
        },
    )
    paused_one_ends = threading.Event()
    desk.follow_exercise([(channel, "1")], os.getpid(), "exercise rust", paused_one_ends.wait)
    first = desk.exercise_reporter
    desk.follow_exercise([(channel, "1")], os.getpid(), "exercise python", lambda: time.sleep(5))
    assert first.ended and desk.exercise_reporter is not first
    paused_one_ends.set()
    time.sleep(0.2)
    assert desk.exercise_reporter is not None and desk.exercise_reporter is not first
    desk.exercise_reporter.ended = True


@pytest.mark.spec("JOB-5")
def test_a_new_jobs_message_is_kept_up_and_one_that_goes_on_comes_to_the_bottom(home_store, monkeypatch):
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", 1000)
    channel = Recorder()
    job = {"number": 1, "command": "learn", "title": "learn html5", "state": "running", "pid": os.getpid()}
    monkeypatch.setattr(Jobs, "current", Jobs.save(job))
    a_new_job = Tracker("learn html5")
    a_new_job.publish()
    reporter = conversation.Reporter(everyone([channel]), "learn html5", pid=os.getpid())
    reporter.refresh()
    reporter.refresh()
    the_persons_message = "learn tailwind"
    channel.send("1", the_persons_message)
    sent = len([entry for entry in channel.log if entry[0] == "send"])
    Jobs.resumed_here = True
    gone_on = Tracker("learn html5")
    gone_on.publish()
    reporter.refresh()
    assert len([entry for entry in channel.log if entry[0] == "send"]) == sent + 1
    reporter.ended = True


@pytest.mark.spec("JOB-12")
def test_a_job_keeps_its_model_calls_in_its_own_log_until_it_is_archived(home_store):
    calls_log().add({"note": "outside a job"})
    with Jobs.run_here(["learn", "rust"]):
        calls_log().add({"note": "a model call"})
    assert [entry["note"] for _, entry in home_store.entries("calls #1")] == ["a model call"]
    archived([1], yes=True)
    assert home_store.entries("calls #1") == []


REAL_POPEN = jobs.subprocess.Popen


def finished_head():
    Queue.remove(Queue.head())


class Started:
    def __init__(self, started, lines, during=finished_head):
        self.started, self.lines, self.during, self.folders = started, lines, during, []

    def __call__(self, command, **options):
        if command[:2] != jobs.tinker_command():
            return REAL_POPEN(command, **options)
        self.started.append((command, options["env"]["TINKER_RUNNER_JOB"]))
        self.folders.append(options["cwd"])
        self.stdout = iter(self.lines)
        return self

    def wait(self):
        self.during()
        return 0


@pytest.mark.spec("JOB-9")
def test_one_runner_holds_the_home_and_one_whose_holder_is_gone_is_taken_over(home_store):
    parent = os.getppid()
    home_store.put("state", "job runner", {"pid": parent, "began": began_at(parent)})
    assert not Runner.claim()
    home_store.put("state", "job runner", {"pid": DEAD_PID, "began": "x"})
    assert Runner.claim() and Runner.claim()
    assert home_store.get("state", "job runner") == {"pid": os.getpid(), "began": began_at(os.getpid())}


@pytest.mark.spec("JOB-9")
def test_the_runner_starts_each_head_as_its_own_tinker_and_keeps_its_output(home_store, monkeypatch):
    started = []
    monkeypatch.setattr(jobs.subprocess, "Popen", Started(started, ["one\n", "two\n"]))
    put_job(home_store, 1, pid=None)
    put_job(home_store, 2, arguments=["research", "go"])
    add_back(1)
    add_back(2)
    Runner.go_through()
    assert [(command[2:], job) for command, job in started] == [
        (["learn", "go"], "1"),
        (["resume", "#2"], "2"),
    ]
    assert started[0][0][:2] == jobs.tinker_command()
    assert [entry.get("line") for _, entry in home_store.entries("output #1")] == ["one", "two", None]
    assert Queue.ordered() == []


@pytest.mark.spec("JOB-9")
def test_the_runner_drops_a_head_with_no_job_and_starts_a_job_in_its_folder(
    home_store, monkeypatch, tmp_path
):
    started = []
    monkeypatch.setattr(jobs.subprocess, "Popen", child := Started(started, []))
    put_job(home_store, 2, pid=None, folder=str(tmp_path))
    add_back(1)
    add_back(2)
    Runner.go_through()
    assert [job for _, job in started] == ["2"] and child.folders == [str(tmp_path)] and Queue.ordered() == []


@pytest.mark.spec("JOB-9")
def test_a_head_pushed_back_stays_queued_and_the_new_head_runs_next(home_store, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "signal_job", lambda job: None)

    def pushed_back_once_it_ran():
        if len(started) == 1:
            Jobs.update(1, pid=DEAD_PID)
            Queue.add_front(2)
        else:
            finished_head()

    monkeypatch.setattr(jobs.subprocess, "Popen", Started(started, [], pushed_back_once_it_ran))
    put_job(home_store, 1, pid=None)
    put_job(home_store, 2, pid=None, arguments=["learn", "rust"])
    add_back(1)
    Runner.go_through()
    assert [command[2:] for command, _ in started] == [["learn", "go"], ["learn", "rust"], ["resume", "#1"]]


@pytest.mark.spec("JOB-9")
def test_the_runner_waits_while_the_head_runs_in_a_process_of_its_own(home_store, monkeypatch):
    started = []
    monkeypatch.setattr(jobs.subprocess, "Popen", Started(started, []))
    put_job(home_store, 1, pid=os.getpid(), pid_began=began_at(os.getpid()))
    add_back(1)
    Runner.go_through()
    assert started == [] and Queue.ordered() == [1]


@pytest.mark.spec("JOB-9")
def test_an_add_wakes_the_runner_in_its_own_process(home_store):
    Runner.wake.clear()
    add_back(1)
    assert Runner.wake.is_set()


def fresh_episode():
    from tinkerkit.remembering import Scratchpad

    Scratchpad.write({"chat": "telegram:1", "message": "hi", "reply": "Hello.", "status": "answered"})


def queued_dreams():
    return [number for number in Queue.ordered() if JOBS.get(number)["arguments"] == ["dream", "run"]]


@pytest.mark.spec("DRM-1")
def test_an_idle_queue_with_a_fresh_episode_queues_one_dream(home_store, monkeypatch):
    monkeypatch.setattr(Runner, "idle_since", None)
    fresh_episode()
    Runner.dream_when_idle(100.0)
    Runner.dream_when_idle(100.0 + DREAM_IDLE_SECONDS - 1)
    assert Queue.ordered() == []
    Runner.dream_when_idle(100.0 + DREAM_IDLE_SECONDS)
    dreams = queued_dreams()
    assert len(dreams) == 1 and JOBS.get(dreams[0])["state"] == "queued"
    Runner.dream_when_idle(200.0)
    Runner.dream_when_idle(200.0 + DREAM_IDLE_SECONDS)
    assert queued_dreams() == dreams


@pytest.mark.spec("DRM-1")
def test_no_dream_is_queued_without_a_fresh_episode_or_while_the_queue_is_busy(home_store, monkeypatch):
    monkeypatch.setattr(Runner, "idle_since", None)
    Runner.dream_when_idle(100.0)
    Runner.dream_when_idle(100.0 + DREAM_IDLE_SECONDS)
    assert Queue.ordered() == []
    fresh_episode()
    put_job(home_store, 1, pid=os.getpid(), pid_began=began_at(os.getpid()))
    add_back(1)
    Runner.dream_when_idle(100.0 + 2 * DREAM_IDLE_SECONDS)
    assert Runner.idle_since is None and Queue.ordered() == [1]


@pytest.mark.spec("DRM-1", "JOB-9")
def test_going_through_an_emptied_queue_starts_the_idle_clock(home_store, runner, monkeypatch):
    monkeypatch.setattr(Runner, "idle_since", None)
    fresh_episode()
    Runner.go_through()
    assert Runner.idle_since is not None and Queue.ordered() == []
    monkeypatch.setattr(Runner, "idle_since", Runner.idle_since - DREAM_IDLE_SECONDS)
    Runner.go_through()
    assert len(queued_dreams()) == 1


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(Runner, "paused", False)
    monkeypatch.setattr(jobs, "signal_job", lambda job: None)
    monkeypatch.setattr(jobs.subprocess, "Popen", child := Started([], ["one\n", "two\n"]))
    return child


@pytest.mark.spec("JOB-9")
def test_the_runner_gives_up_on_a_head_that_keeps_ending_while_it_is_the_head(home_store, runner):
    put_job(home_store, 1, state="queued", pid=None)
    put_job(home_store, 2, state="queued", pid=None, arguments=["learn", "rust"])
    add_back(1)
    add_back(2)
    runner.during = lambda: None if len(runner.started) <= JOB_START_TRIES else finished_head()
    Runner.go_through()
    assert [job for _, job in runner.started] == ["1"] * JOB_START_TRIES + ["2"]
    assert JOBS.get(1)["state"] == "stopped" and JOBS.get(1)["start_failures"] == 0
    assert Queue.ordered() == []
    lines = [entry.get("line") for _, entry in home_store.entries("output #1")]
    assert lines[-2] == f"gave up after {JOB_START_TRIES} failed starts in a row; resume #1 tries it again"


@pytest.mark.spec("JOB-9")
def test_the_output_of_a_given_up_head_ends_with_its_exit_so_its_follower_stops(home_store, runner):
    runner.during = lambda: None
    put_job(home_store, 1, state="queued", pid=None, start_failures=JOB_START_TRIES - 1)
    add_back(1)
    follower = jobs.Follower(1)
    Runner.run_job(1)
    entries = [entry for _, entry in home_store.entries("output #1")]
    assert "exit" in entries[-1] and entries[-2]["line"].startswith("gave up after")
    assert follower.show()


@pytest.mark.spec("JOB-9")
def test_a_head_that_leaves_the_queue_clears_its_failed_starts(home_store, runner):
    put_job(home_store, 1, state="queued", pid=None, start_failures=JOB_START_TRIES - 1)
    add_back(1)
    Runner.run_job(1)
    assert JOBS.get(1)["start_failures"] == 0 and JOBS.get(1)["state"] == "queued"


@pytest.mark.spec("JOB-9")
def test_a_head_ending_while_the_runner_is_paused_is_no_failed_start(home_store, runner, monkeypatch):
    def paused_meanwhile():
        monkeypatch.setattr(Runner, "paused", True)

    runner.during = paused_meanwhile
    put_job(home_store, 1, state="queued", pid=None, start_failures=JOB_START_TRIES - 1)
    add_back(1)
    Runner.run_job(1)
    assert JOBS.get(1)["start_failures"] == JOB_START_TRIES - 1 and Queue.ordered() == [1]


@pytest.mark.spec("JOB-10")
def test_a_job_command_hosting_the_runner_follows_its_job_then_the_queue(
    home_store, runner, monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(
        Runner, "host", classmethod(lambda cls: threading.Thread(target=Runner.go_through).start() or True)
    )
    put_job(home_store, 1, state="queued", pid=None)
    home_store.put("job numbers", "last", 1)
    add_back(1)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ended, Jobs.running(["learn", "rust"]):
        raise AssertionError("a job command runs no job itself")
    assert ended.value.code == 0 and capsys.readouterr().out == "one\ntwo\n"
    assert [job for _, job in runner.started] == ["2", "1"] and runner.folders[0] == str(tmp_path)
    assert Queue.ordered() == [] and Jobs.get(2)["title"] == "learn rust"


@pytest.mark.spec("JOB-10")
def test_a_job_command_ends_once_its_job_leaves_the_queue(home_store, runner, monkeypatch, capsys):
    asked_to_host = []

    def another_tinker_hosts(cls):
        if not asked_to_host:
            threading.Thread(target=Runner.run_job, args=(Queue.head(),)).start()
        asked_to_host.append(True)
        return False

    monkeypatch.setattr(Runner, "host", classmethod(another_tinker_hosts))
    put_job(home_store, 1, state="queued", pid=None)
    home_store.put("job numbers", "last", 1)
    add_back(1)
    with pytest.raises(SystemExit), Jobs.running(["learn", "rust"]):
        pass
    assert capsys.readouterr().out == "one\ntwo\n" and Queue.ordered() == [1]


@pytest.mark.spec("JOB-10")
def test_ctrl_c_in_a_job_command_pauses_the_job_that_runs(home_store, runner, told, monkeypatch):
    monkeypatch.setattr(Runner, "host", classmethod(lambda cls: True))

    def ctrl_c(seconds):
        Jobs.update(1, pid=os.getpid(), pid_began=began_at(os.getpid()))
        raise KeyboardInterrupt

    monkeypatch.setattr(jobs.time, "sleep", ctrl_c)
    with pytest.raises(KeyboardInterrupt), Jobs.running(["learn", "rust"]):
        pass
    assert told == [1] and Runner.paused and Queue.ordered() == [1] and Jobs.get(1)["state"] == "running"
    Runner.go_through()
    assert runner.started == []


@pytest.mark.spec("JOB-10")
@pytest.mark.parametrize("hosting", [True, False])
def test_pausing_signals_the_live_head_and_stops_the_runner_only_where_it_is_hosted(
    home_store, runner, told, hosting
):
    put_job(home_store, 1, state="running", pid=os.getpid(), pid_began=began_at(os.getpid()))
    put_job(home_store, 2, state="queued", pid=None)
    add_back(1)
    add_back(2)
    Runner.pause(hosting)
    assert told == [1] and Runner.paused is hosting and Queue.ordered() == [1, 2]
