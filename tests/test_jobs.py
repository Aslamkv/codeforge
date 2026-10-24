import contextlib
import datetime
import os
import signal
import threading
import time

import pytest
from test_conversation import Recorder

from tinkerkit import conversation, jobs
from tinkerkit.cli import Cli, Commands
from tinkerkit.conversation import ConsoleChannel, Desk, Message, Progress, Research, research_once
from tinkerkit.fetcher import Fetched, Fetcher
from tinkerkit.jobs import JOB_STOP_SIGNAL, Jobs, is_job, journaled, today
from tinkerkit.learning import SlotLearner
from tinkerkit.model import Ollama
from tinkerkit.project import Tracker
from tinkerkit.web import Internet

DEAD_PID = 999999999


def stopped_job(home_store, argv, answers=()):
    with pytest.raises(KeyboardInterrupt), Jobs.running(argv):
        for request, answer in answers:
            journaled(request, lambda answer=answer: answer)
        raise KeyboardInterrupt
    return Jobs.get(1)


def asked(answer, calls):
    def ask():
        calls.append(answer)
        return answer

    return ask


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
        ["ask", "status", "of", "python"],
    ):
        assert is_job(argv), argv
    for argv in (
        [],
        ["help"],
        ["runs"],
        ["jobs"],
        ["stop"],
        ["exercise", "status"],
        ["learn", "ll", "ls -l"],
        ["task", "--help"],
    ):
        assert not is_job(argv), argv


@pytest.mark.spec("JOB-1")
def test_an_exercises_build_is_not_a_job_of_its_own(monkeypatch):
    monkeypatch.setenv("TINKER_EXERCISE", "1")
    assert not is_job(["task", "a", "calculator"])


@pytest.mark.spec("JOB-1")
def test_a_job_is_numbered_and_kept_while_it_runs(home_store):
    with Jobs.running(["learn", "rust"]):
        job = Jobs.get(1)
        assert (
            job["state"] == "running" and job["pid"] == os.getpid() and job["arguments"] == ["learn", "rust"]
        )
        assert job["today"] == datetime.date.today().isoformat() and job["title"] == "learn rust"
        assert signal.getsignal(JOB_STOP_SIGNAL) is jobs.interrupted
    with Jobs.running(["task", "a", "calculator", "--confirm"]):
        assert Jobs.current["number"] == 2 and Jobs.current["title"] == "task a calculator"
    with Jobs.running(["runs"]):
        assert Jobs.current is None


@pytest.mark.spec("JOB-2")
def test_an_interrupted_job_is_stopped_with_its_journal_kept(home_store):
    job = stopped_job(home_store, ["research", "python"], [({"search": "python"}, ["found"])])
    assert job["state"] == "stopped" and home_store.logged("journal #1") == 1


@pytest.mark.spec("JOB-2")
def test_a_finished_job_drops_its_journal(home_store):
    with Jobs.running(["research", "python"]):
        journaled({"search": "python"}, lambda: ["found"])
    assert Jobs.get(1)["state"] == "finished" and home_store.logged("journal #1") == 0


@pytest.mark.spec("JOB-2")
def test_a_tracker_that_ends_stopped_stops_the_job_and_says_how_to_go_on(home_store):
    tracker = Tracker("Exercising rust")
    with Jobs.running(["exercise", "rust"]):
        tracker.end({"status": "stopped", "headline": "stopped", "next": [("See", "x")]}, 1.0)
    assert tracker.state["ended"]["next"] == [("See", "x"), ("Go on", "tinker resume #1")]
    assert Jobs.get(1)["state"] == "stopped"
    outside = Tracker("outside a job")
    outside.end({"status": "stopped"}, 1.0)
    assert "next" not in outside.state["ended"]


@pytest.mark.spec("JOB-2")
def test_a_running_job_whose_process_is_gone_is_stopped(home_store):
    home_store.put(
        "jobs",
        "3",
        {
            "number": 3,
            "command": "learn",
            "title": "learn go",
            "state": "running",
            "pid": DEAD_PID,
            "arguments": ["learn", "go"],
            "started": "x",
            "updated": "x",
        },
    )
    assert Jobs.get(3)["state"] == "stopped" and Jobs.listed("stopped")[0]["number"] == 3


@pytest.mark.spec("JOB-3")
def test_a_resumed_job_is_answered_from_its_journal_and_asks_only_what_is_new(home_store):
    kept_twice, never_kept = {"search": "rust"}, {"search": "rust docs"}
    stopped_job(home_store, ["learn", "rust"], [(kept_twice, ["a"]), (kept_twice, ["b"])])
    calls = []
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
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
    assert home_store.logged("journal #1") == 0


@pytest.mark.spec("JOB-3")
def test_model_calls_and_page_fetches_go_through_the_journal(home_store, monkeypatch):
    calls = []

    def streamed(name, messages, *rest):
        calls.append(messages)
        return {"message": {"content": "hi"}}

    monkeypatch.setattr(Ollama, "streamed", staticmethod(streamed))
    fetcher = Fetcher(1, 1.0, 1000, 0.0)
    monkeypatch.setattr(
        fetcher,
        "fetched",
        lambda url, connections, kind: (
            calls.append(url)
            or (Fetched(url, 200, url, "text") if "good" in url else Fetched(url, error="refused"))
        ),
    )
    say_hi = [{"role": "user", "content": "hi"}]
    with pytest.raises(KeyboardInterrupt), Jobs.running(["ask", "hi"]):
        Ollama.chat("m", say_hi, 0, {})
        fetcher.fetch("https://good.example", None, "fetch")
        fetcher.fetch("https://bad.example", None, "fetch")
        raise KeyboardInterrupt
    calls.clear()
    a_different_keep_alive, an_unload = 300, []
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
        assert Ollama.chat("m", say_hi, a_different_keep_alive, {})["message"]["content"] == "hi"
        assert fetcher.fetch("https://good.example", None, "fetch") == Fetched(
            "https://good.example", 200, "https://good.example", "text"
        )
        assert not fetcher.fetch("https://bad.example", None, "fetch").ok
        Ollama.chat("m", an_unload, 0, None)
    assert calls == ["https://bad.example", an_unload]


@pytest.mark.spec("JOB-4")
def test_a_job_says_the_day_it_started(home_store):
    assert today() == datetime.date.today()
    home_store.put(
        "jobs",
        "1",
        {
            "number": 1,
            "command": "learn",
            "title": "learn rust",
            "state": "stopped",
            "pid": DEAD_PID,
            "arguments": ["learn", "rust"],
            "today": "2026-01-02",
            "updated": "x",
        },
    )
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
        assert today() == datetime.date(2026, 1, 2)


@pytest.mark.spec("JOB-5")
def test_resume_runs_the_jobs_own_command_line(home_store):
    this_folders_run = ["resume"]
    assert Jobs.arguments(this_folders_run) == this_folders_run
    assert Jobs.arguments(["resume", "@calc"]) == ["resume", "@calc"] and Jobs.arguments(["task", "x"]) == [
        "task",
        "x",
    ]
    with pytest.raises(SystemExit, match="No job #4"):
        Jobs.arguments(["resume", "#4"])
    stopped_job(home_store, ["learn", "rust"])
    assert Jobs.arguments(["resume"]) == ["learn", "rust"] and Jobs.current["number"] == 1
    Jobs.current = None
    home_store.put("jobs", "1", dict(Jobs.get(1), state="running", pid=os.getppid()))
    with pytest.raises(SystemExit, match="Job #1 is running"):
        Jobs.arguments(["resume", "#1"])
    home_store.put("jobs", "1", dict(Jobs.get(1), state="finished"))
    with pytest.raises(SystemExit, match="Job #1 is finished"):
        Jobs.arguments(["resume", "#1"])


@pytest.mark.spec("JOB-5")
def test_a_tasks_job_is_resumed_as_its_run(home_store):
    with Jobs.running(["task", "a", "calculator"]):
        Jobs.retarget(["resume", "@a1b2"])
        assert Jobs.get(1)["arguments"] == ["resume", "@a1b2"] and Jobs.get(1)["title"] == "task a calculator"
    Jobs.retarget(["resume", "@other"])
    assert Jobs.get(1)["arguments"] == ["resume", "@a1b2"]


def keep_jobs(home_store, states):
    for number, state in enumerate(states, 1):
        home_store.put(
            "jobs",
            str(number),
            {
                "number": number,
                "command": "learn",
                "title": f"learn topic {number}",
                "state": state,
                "pid": os.getpid() if state == "running" else DEAD_PID,
                "arguments": ["learn", f"topic {number}"],
                "started": "2026-10-23 09:00",
                "updated": "2026-10-23 09:00",
            },
        )


@pytest.mark.spec("JOB-6")
def test_jobs_lists_running_then_stopped_newest_first(home_store, capsys):
    Commands.list_jobs(None, 1)
    assert "No running, paused or stopped jobs." in capsys.readouterr().out
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
    assert Cli.parse(["jobs", "finished", "2"]).jobs == ("finished", 2) and Cli.parse(["jobs", "3"]).jobs == (
        None,
        3,
    )
    for words in (["jobs", "done"], ["jobs", "0"], ["jobs", "running", "1", "2"]):
        with pytest.raises(SystemExit):
            Cli.parse(words)


@pytest.mark.spec("JOB-7")
def test_stop_tells_a_job_or_every_one_of_the_persons(home_store, monkeypatch, capsys):
    told = []
    monkeypatch.setattr(Jobs, "stop", staticmethod(lambda job: told.append(job["number"])))
    keep_jobs(home_store, ["running", "stopped", "running"])
    home_store.put("jobs", "3", dict(Jobs.get(3), command="exercise"))
    running_newest_first = [3, 1]
    Commands.stop(None)
    assert told == running_newest_first
    Commands.stop(3)
    assert told == running_newest_first + [3] and "tinker resume #3 goes on" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Job #2 is not running or paused"):
        Commands.stop(2)
    assert Cli.parse(["stop", "#3"]).stop == 3 and Cli.parse(["stop"]).stop is None
    with pytest.raises(SystemExit):
        Cli.parse(["stop", "3"])


@pytest.mark.spec("JOB-11")
def test_clear_jobs_archives_them_listed_apart_and_never_resumed(home_store, capsys):
    keep_jobs(home_store, ["stopped", "finished", "running", "stopped"])
    home_store.append("journal #1", {"key": "k", "answer": "a"})
    with pytest.raises(SystemExit, match="Nothing deleted: run tinker clear jobs #1"):
        Cli.cleared(["jobs", "#1"], yes=False)
    Cli.cleared(["jobs", "#1"], yes=True)
    assert Jobs.get(1)["state"] == "archived" and Jobs.get(1)["archived_from"] == "stopped"
    assert home_store.logged("journal #1") == 0
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
        lambda arguments, folder, title, *rest, **kwargs: started.append((arguments, title)),
    )
    monkeypatch.setattr(desk, "start_exercise", lambda words, title=None: started.append((words, title)))
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
    assert answer("stop").startswith("⏹ **Stopping #2**") and told == [2, "exercise", "exercise", 2]
    answer("resume #1")
    answer("resume #3")
    answer("resume @calc")
    assert started == [
        (["resume", "#1"], "learn topic 1"),
        (["resume", "#3"], "exercise rust"),
        (["resume", "@calc"], "resume @calc"),
    ]


@pytest.mark.spec("CNV-23")
def test_a_research_job_ends_with_its_answer():
    progress = Progress("Researching python")
    progress.take({"ended": {"status": "done", "headline": "", "text": "🔎 **python**\nThe answer [1]"}})
    assert progress.ending().endswith("The answer [1]") and "****" not in progress.ending()


def other_running(home_store, number=1, command="learn", title="learn go"):
    home_store.put(
        "jobs",
        str(number),
        {
            "number": number,
            "command": command,
            "title": title,
            "state": "running",
            "pid": os.getppid(),
            "arguments": title.split(),
            "started": "x",
            "updated": "x",
        },
    )
    home_store.put("job numbers", "last", number)


@pytest.mark.spec("JOB-8")
def test_a_job_that_starts_pauses_the_one_that_runs(home_store, monkeypatch, capsys):
    other_running(home_store)
    told = []
    monkeypatch.setattr(
        jobs, "signal_job", lambda job: told.append(job["number"]) or Jobs.update(1, state="paused")
    )
    with Jobs.running(["exercise", "python"]):
        assert told == [1] and Jobs.get(1)["paused_by"] == 2 and Jobs.current["state"] == "running"
    assert "Pausing #1 learn go: it goes on once Tinker is idle" in capsys.readouterr().out


@pytest.mark.spec("JOB-2", "JOB-8")
def test_a_paused_job_says_so_and_waits_where_it_is(home_store, monkeypatch):
    waited = []
    monkeypatch.setattr(jobs, "go_on_when_idle", waited.append)
    tracker = Tracker("Learning go")
    with pytest.raises(KeyboardInterrupt), Jobs.running(["learn", "go"]):
        another_job = 2
        Jobs.update(1, paused_by=another_job)
        tracker.end({"status": "stopped", "headline": "Learning go did not finish"}, 1.0)
        raise KeyboardInterrupt
    ended = tracker.state["ended"]
    assert (
        ended["status"] == "paused"
        and ended["headline"] == "Paused for job #2: it goes on once Tinker is idle"
    )
    assert tracker.state["doing"].startswith("⏸ Paused for job #2") and ended["next"] == [
        ("Go on now", "tinker resume #1")
    ]
    assert Jobs.get(1)["state"] == "paused" and waited == [1]
    killed_while_waiting = dict(Jobs.get(1), pid=DEAD_PID)
    home_store.put("jobs", "1", killed_while_waiting)
    assert Jobs.get(1)["state"] == "stopped"


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
    assert desk.start_task(["learn", "go"], tmp_path, "learn go") is None
    assert desk.start_task(["exercise", "python"], tmp_path, "exercise python") is None
    assert spawned == [["learn", "go"], ["exercise", "python"]] and len(desk.live_tasks) == 2
    desk.reporter.ended = True


class Execd(Exception):
    pass


def paused_job(home_store, number, pid=None, **more):
    home_store.put(
        "jobs",
        str(number),
        dict(
            {
                "number": number,
                "command": "learn",
                "title": f"learn {number}",
                "state": "paused",
                "pid": pid or os.getpid(),
                "paused_by": 9,
                "arguments": ["learn", str(number)],
                "started": "x",
                "updated": "x",
            },
            **more,
        ),
    )


@pytest.mark.spec("JOB-9")
def test_tinker_is_idle_when_no_job_runs_and_nothing_asked_is_being_answered(home_store):
    assert jobs.idle()
    other_running(home_store, number=5)
    assert not jobs.idle()
    home_store.delete("jobs")
    home_store.put("activity", "answering", {"a chat": os.getpid()})
    assert not jobs.idle()
    home_store.put("activity", "answering", {"a chat of a Tinker that is gone": DEAD_PID})
    assert jobs.idle()


@pytest.mark.spec("JOB-9")
def test_the_newest_paused_job_goes_on_by_itself_once_tinker_is_idle(home_store, monkeypatch, no_sleep):
    def execv(path, argv):
        raise Execd(argv[-2:])

    monkeypatch.setattr(os, "execv", execv)
    paused_job(home_store, 1)
    paused_job(home_store, 3)
    assert jobs.next_to_go_on() == 3
    job_3_first_then_job_1 = iter([True, True, False])
    monkeypatch.setattr(jobs, "next_to_go_on", lambda: 3 if next(job_3_first_then_job_1) else 1)
    with pytest.raises(Execd) as went_on:
        jobs.go_on_when_idle(1)
    assert went_on.value.args[0] == ["resume", "#1"] and len(no_sleep) == 2


@pytest.mark.spec("JOB-9")
def test_resume_makes_a_paused_job_go_on_at_once_and_stop_keeps_it_stopped(home_store, monkeypatch, no_sleep):
    monkeypatch.setattr(os, "execv", lambda path, argv: (_ for _ in ()).throw(Execd(argv[-2:])))
    a_chat_is_being_answered = {"a chat": os.getpid()}
    home_store.put("activity", "answering", a_chat_is_being_answered)
    paused_job(home_store, 1, pid=os.getppid())
    with pytest.raises(SystemExit) as woken:
        Jobs.arguments(["resume", "#1"])
    assert woken.value.code == 0 and Jobs.get(1)["go_now"]
    home_store.put("jobs", "1", dict(Jobs.get(1), pid=os.getpid()))
    with pytest.raises(Execd):
        jobs.go_on_when_idle(1)
    stopped_meanwhile = 2
    paused_job(home_store, stopped_meanwhile, pid=os.getppid())
    assert Jobs.resumable(stopped_meanwhile) == (
        None,
        "Job #2 is paused: it goes on by itself once Tinker is idle.",
    )
    Jobs.stop(Jobs.get(stopped_meanwhile))
    assert (
        Jobs.get(stopped_meanwhile)["state"] == "stopped" and Jobs.get(stopped_meanwhile)["paused_by"] is None
    )
    jobs.go_on_when_idle(stopped_meanwhile)


@pytest.mark.spec("JOB-6")
def test_paused_jobs_are_listed_between_running_and_stopped(home_store):
    keep_jobs(home_store, ["stopped", "running"])
    paused_job(home_store, 3)
    assert [job["number"] for job in Jobs.listed()] == [2, 3, 1] and Cli.parse(["jobs", "paused"]).jobs == (
        "paused",
        1,
    )


@pytest.mark.spec("JOB-3")
def test_web_searches_and_fetches_go_through_the_journal(home_store, monkeypatch):
    calls = []
    internet = Internet.__new__(Internet)
    monkeypatch.setattr(
        internet, "searched", lambda query, count: calls.append(query) or [{"url": "https://a.example"}]
    )
    monkeypatch.setattr(internet, "fetched", lambda url: calls.append(url) or {"url": url, "content": "text"})
    with pytest.raises(KeyboardInterrupt), Jobs.running(["research", "go"]):
        internet.search("go", 5), internet.fetch("https://a.example")
        raise KeyboardInterrupt
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
        assert internet.search("go", 5) == [{"url": "https://a.example"}]
        assert internet.fetch("https://a.example") == {"url": "https://a.example", "content": "text"}
        a_new_request = ("go", 10)
        internet.search(*a_new_request)
    assert calls == ["go", "https://a.example", "go"]


@pytest.mark.spec("JOB-4")
def test_the_prompts_that_say_the_date_say_the_jobs(home_store):
    home_store.put(
        "jobs",
        "1",
        {
            "number": 1,
            "command": "research",
            "title": "research go",
            "state": "stopped",
            "pid": DEAD_PID,
            "arguments": ["research", "go"],
            "today": "2026-01-02",
            "updated": "x",
        },
    )
    asked = []

    class Talk:
        def ask(self, channel, chat, messages, tokens):
            asked.append(messages[0]["content"])
            return '{"searches": ["go"]}'

    learner = SlotLearner.__new__(SlotLearner)
    learner.topic = "go"
    with Jobs.running(Jobs.arguments(["resume", "#1"])):
        Research(Talk()).searches(None, "1", "go")
        parts = learner.plan_parts([])
    assert "02 January 2026" in asked[0] and "2026" in asked[0]
    assert next(part for part in parts if part.title == "Today").text == "2026-01-02"


@pytest.mark.spec("JOB-5")
def test_a_job_that_goes_on_starts_its_tracker_resumed(home_store):
    assert Tracker("new").state["resumed"] is None
    stopped_job(home_store, ["learn", "go"])
    Jobs.arguments(["resume", "#1"])
    assert Tracker("Learning go").state["resumed"]


@pytest.mark.spec("JOB-10")
def test_a_chat_pauses_the_job_that_runs_while_it_is_answered(home_store, monkeypatch):
    other_running(home_store)
    seen = []

    def told_to_pause(job):
        seen.append(jobs.idle())
        Jobs.update(1, state="paused")

    monkeypatch.setattr(jobs, "signal_job", told_to_pause)
    with jobs.paused_for_you():
        assert Jobs.get(1)["paused_by"] == "you" and not jobs.idle()
    assert seen == [False] and jobs.idle()
    tracker = Tracker("Learning go")
    Jobs.current = Jobs.get(1)
    tracker.end({"status": "stopped"}, 1.0)
    assert tracker.state["ended"]["headline"] == "Paused for you: it goes on once Tinker is idle"


@pytest.mark.spec("JOB-10")
def test_tinker_chat_and_command_pause_the_job_that_runs(monkeypatch, capsys):
    paused = []
    monkeypatch.setattr(conversation, "paused_for_you", lambda: contextlib.nullcontext(paused.append(True)))
    monkeypatch.setattr(conversation, "commands_brief", lambda: "cmds")
    conversation.one_shot("chat", "hello")
    conversation.one_shot("research", "")
    assert paused == [True]


@pytest.mark.spec("JOB-8")
def test_a_job_that_does_not_pause_is_waited_for_at_most_pause_wait(home_store, monkeypatch, no_sleep):
    other_running(home_store)
    never_pauses = lambda job: None
    monkeypatch.setattr(jobs, "signal_job", never_pauses)
    clock = iter(range(0, 1000, 50))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(clock))
    with Jobs.running(["ask", "q"]):
        looked_at_50s_and_100s = [0.5, 0.5]
        assert Jobs.get(1)["state"] == "running" and no_sleep == looked_at_50s_and_100s


@pytest.mark.spec("CNV-23")
def test_a_research_job_says_its_steps_and_ends_with_its_answer(home_store, monkeypatch):
    monkeypatch.setenv(conversation.CHANNEL_REPORTS_ENV, "off")
    monkeypatch.setattr(
        Research,
        "researching",
        lambda self, channel, chat, topic, now: (
            self.enter("searches"),
            self.enter("read"),
            self.enter("write"),
            ("done", "🔎 **go**\nGo 1.25 [1]"),
        )[-1],
    )
    research_once(ConsoleChannel(commands_without_tinker=False), None, "go")
    state = home_store.get("running", str(os.getpid()))
    assert state["steps"] == ["🤔 Searches chosen", "🌐 Pages read"] and state["doing"].startswith("✍️")
    assert state["ended"] == {"status": "done", "headline": "", "text": "🔎 **go**\nGo 1.25 [1]"}


@pytest.mark.spec("EXR-7")
def test_an_exercise_paused_for_another_hands_it_its_message(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    home_store.put("running", str(os.getpid()), {"title": "Exercising Rust", "exercise": True, "doing": "🔨"})
    paused_one_ends = threading.Event()
    desk.follow_exercise(os.getpid(), "exercise rust", paused_one_ends.wait)
    first = desk.exercise_reporter
    desk.follow_exercise(os.getpid(), "exercise python", lambda: time.sleep(5))
    assert first.ended and desk.exercise_reporter is not first
    paused_one_ends.set()
    time.sleep(0.2)
    assert desk.exercise_reporter is not None and desk.exercise_reporter is not first
    desk.exercise_reporter.ended = True


@pytest.mark.spec("JOB-5")
def test_a_new_jobs_message_is_kept_up_and_one_that_goes_on_comes_to_the_bottom(home_store, monkeypatch):
    monkeypatch.setattr(conversation, "PROGRESS_EDIT_SECONDS", 1000)
    channel = Recorder()
    a_new_job = Tracker("learn html5")
    a_new_job.publish()
    reporter = conversation.Reporter([channel], "learn html5", pid=os.getpid())
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
