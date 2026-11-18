import os
import signal
import time

import pytest
from test_console import Recorder, live_console

import tinkerkit.chat.reports as reports
from tinkerkit.build.project import Tracker
from tinkerkit.chat.reports import CLOCK_ONLY_EDIT_SECONDS, PROGRESS_EDIT_SECONDS, Progress, Reporter, attach
from tinkerkit.settings import JOB_STOP_SIGNAL
from tinkerkit.skills import SkillResult


def everyone(channels):
    return [(channel, chat) for channel in channels for chat in channel.allowed]


@pytest.mark.spec("CNV-6")
def test_progress_says_when_the_work_began_counting_earlier_runs():
    progress = Progress("calc", before=60.0)
    assert progress.work_began is None
    progress.take({"started": 1000.0})
    assert progress.work_began == 940.0


def snapshot(**state):
    base = {
        "title": "calc: a calculator",
        "run": "abc123",
        "folder": "/tmp/projects/calc",
        "step": None,
        "doing": Tracker.STARTING,
        "began": None,
        "steps": [],
        "passing": None,
        "working": "",
        "tally": "",
        "found": "",
        "latest": "",
        "waiting": None,
        "ended": None,
        "before": 0.0,
        "seconds": 0.0,
    }
    return dict(base, **state)


@pytest.mark.spec("CNV-15")
def test_progress_shows_the_tracker():
    progress = Progress("a calculator", phase="✅ **Confirmed:** building it")
    assert progress.text().splitlines() == [
        "🔨 **a calculator**",
        "✻ Tinkering… · ✅ **Confirmed:** building it",
    ]
    before_any_step = snapshot()
    assert progress.take(before_any_step) and "Tinkering… · ✅ **Confirmed:** building it" in progress.text()
    progress.take(
        snapshot(
            job=7,
            step="verify",
            doing=Tracker.DOING_AND_DONE["verify"][0],
            started=time.time() - 65,
            steps=["🧠 Recalled what Tinker has learned", "📝 Spec written"],
            passing=[2, 3],
            latest="wrote calc.py",
        )
    )
    assert progress.text().splitlines() == [
        "🔨 **calc: a calculator** (Job #7)",
        "✅ 🧠 Recalled what Tinker has learned",
        "✅ 📝 Spec written",
        "✻ Tinkering… (1m 5s) · **🔁 Fixing until every test passes**",
        "📊 2/3 spec points pass",
        "… wrote calc.py",
    ]
    assert (
        progress.brief() == "**calc: a calculator** (🔁 Fixing until every test passes, 2/3 spec points pass)"
    )
    assert not progress.take(progress.snapshot)


@pytest.mark.spec("CNV-15")
def test_progress_says_what_the_run_works_on_how_far_it_got_and_what_it_found():
    progress = Progress("Reading Atomic Habits")
    progress.take(
        snapshot(
            title="Reading Atomic Habits",
            step="chapters",
            doing=Tracker.DOING_AND_DONE["chapters"][0],
            started=time.time() - 12,
            tokens=1234,
            thought_seconds=4.0,
            steps=["🧭 Chose the chapters to read: 3 core chapter(s) of 20"],
            working="Reading chapter 3, The 1% Rule (2 of 7)",
            tally="1/7 chapters · 4 things learned",
            found="Small habits compound like interest",
            latest="📖 Chapter 1: 4 things learned",
        )
    )
    assert progress.text().splitlines() == [
        "🔨 **Reading Atomic Habits**",
        "✅ 🧭 Chose the chapters to read: 3 core chapter(s) of 20",
        "✻ Tinkering… (12s · ↓ 1.2k tokens · thought for 4s) · **📚 Reading chapter 3, The 1% Rule (2 of 7)**",
        "📊 1/7 chapters · 4 things learned",
        "💡 Small habits compound like interest",
    ]
    assert progress.brief() == (
        "**Reading Atomic Habits** (📚 Reading chapter 3, The 1% Rule (2 of 7), "
        "1/7 chapters · 4 things learned)"
    )
    progress.turn()
    assert progress.text().splitlines()[2].startswith("✶ Tinkering…") and not progress.take(progress.snapshot)


@pytest.mark.spec("CNV-15")
def test_progress_says_while_the_model_thinks_and_edits_for_a_new_token_count_only_with_the_clock():
    progress = Progress("calc")
    progress.take(snapshot(tokens=950, thinking=True, thought_seconds=4.0))
    assert "✻ Tinkering… (↓ 950 tokens · thinking) · " in progress.text()
    assert not progress.take(dict(progress.snapshot, tokens=2000))
    assert progress.take(dict(progress.snapshot, thinking=False))


@pytest.mark.spec("CNV-15")
def test_progress_ending_of_a_done_run():
    progress = Progress("calc", before=60)
    progress.take(
        snapshot(
            seconds=30.0,
            ended={
                "status": "done",
                "headline": "Done. All 2 spec tests pass (SPEC.md).",
                "details": [["Run it", "python3 calc.py"]],
                "next": [
                    ["➕ Add a feature", 'tinker add @abc123 "..."'],
                    ["🐞 fix a bug", 'tinker fix @abc123 "..."'],
                ],
            },
        )
    )
    assert progress.time_taken() == "1m 30s (90 seconds)" and not progress.waiting()
    assert progress.ending().splitlines() == [
        "🏁 **calc: a calculator**",
        "✅ **Done. All 2 spec tests pass (SPEC.md).**",
        "⏱ **Done in 1m 30s (90 seconds)**",
        "▶️ Run it: `python3 calc.py`",
        '➕ Add a feature: `tinker add @abc123 "..."` · 🐞 fix a bug: `tinker fix @abc123 "..."`',
    ]
    progress.take(
        snapshot(
            ended={"status": "blocked", "headline": "Stopped: 1/2 tests pass", "details": [], "next": []}
        )
    )
    assert progress.ending().splitlines()[1:3] == [
        "⚠️ **Stopped: 1/2 tests pass**",
        "⏱ _so far: 1m 0s (60 seconds)_",
    ]


PLAN = {
    "name": "calc",
    "run": "abc123",
    "language": "Python",
    "start": "python3 calc.py",
    "kind": "project",
    "changes": ["+ C3 calc.sub(5, 3) is 2", "~ C1 calc.add(2, 3) is 5", "- adds"],
    "criteria": [["C1", "calc.add(2, 3) is 5"]],
    "non_goals": ["a GUI"],
    "folder": "/tmp/x/calc",
}


@pytest.mark.spec("CNV-15")
def test_progress_ending_of_a_plan_that_waits():
    progress = Progress("calc")
    progress.take(snapshot(waiting=PLAN))
    assert progress.waiting()
    lines = progress.ending().splitlines()
    assert lines[:2] == ["📋 **Plan for calc** · run `abc123`", "🧰 Python · ▶️ `python3 calc.py`"]
    for line in (
        "✏️ **What changed**",
        "➕ C3 calc.sub(5, 3) is 2",
        "🔄 C1 calc.add(2, 3) is 5",
        "➖ ~~adds~~",
        "🧪 **Tested before any code is written**",
        "**C1** calc.add(2, 3) is 5",
        "🚫 _Not included:_ a GUI",
        "📁 `calc`",
        "👉 `/confirm` to build it · `/amend WHAT` to change it",
    ):
        assert line in lines, line
    assert lines[-1].startswith("⏱ _so far:")
    assert Progress.plan(dict(PLAN, kind="fix")).startswith("🐞 **Bug shown for calc**")


@pytest.mark.spec("CNV-16")
def test_reporter_shows_the_tracker_and_ends_with_its_ending(home_store, monkeypatch):
    chats = ("1", "2")
    channel = Recorder(chats=chats)
    no_background_edits = 1000
    monkeypatch.setattr(reports, "PROGRESS_EDIT_SECONDS", no_background_edits)
    reporter = Reporter(everyone([channel]), "a calculator", pid=4242)
    reporter.update()
    one_per_chat = len(chats)
    assert len(channel.texts("send")) == one_per_chat
    keep_job(home_store, 1, 4242, snapshot(step="spec", doing=Tracker.DOING_AND_DONE["spec"][0]))
    reporter.refresh()
    assert "📝 Writing the spec" in channel.texts()[-1] and channel.log[-1][0] == "edit"
    edits = len(channel.log)
    reporter.refresh()
    assert len(channel.log) == edits
    reporter.finish(snapshot(ended={"status": "done", "headline": "Done.", "details": [], "next": []}))
    assert reporter.ended and "✅ **Done.**" in channel.texts()[-1]
    assert set(reporter.sent) == {("rec", "1"), ("rec", "2")}


@pytest.mark.spec("SKL-7")
def test_reporter_sends_the_ending_files_to_its_audience_after_the_ending(home_store, tmp_path):
    picture, notes = tmp_path / "thumb.out", tmp_path / "notes.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nrest")
    notes.write_text("plain words")
    channel = Recorder(chats=("1", "2"))
    reporter = Reporter(everyone([channel]), "thumbs", pid=4242)
    ended = SkillResult(True, [picture, notes], "", 10, tmp_path).use_ending("thumbs")
    reporter.finish(snapshot(ended=ended))
    delivered = [entry for entry in channel.log if entry[0] in ("file", "photo")]
    assert delivered == [
        ("photo", "1", str(picture), "thumb.out"),
        ("photo", "2", str(picture), "thumb.out"),
        ("file", "1", str(notes), "notes.png"),
        ("file", "2", str(notes), "notes.png"),
    ]
    assert channel.log.index(delivered[0]) > max(
        at for at, entry in enumerate(channel.log) if "Used thumbs" in entry[3]
    )


@pytest.mark.spec("CNV-16")
def test_a_reporter_shows_only_to_its_audience(monkeypatch):
    monkeypatch.setattr(reports, "PROGRESS_EDIT_SECONDS", 1000)
    channel = Recorder(chats=("1", "2"))
    reporter = Reporter([(channel, "2")], "a calculator", pid=999999)
    reporter.update()
    reporter.finish()
    assert {chat for _, chat, *_ in channel.log} == {"2"}
    assert set(reporter.sent) == {("rec", "2")}


@pytest.mark.spec("CNV-16")
def test_reporter_edits_for_new_tokens_only_with_the_clock_but_the_console_counts_them_live(
    home_store, monkeypatch, clock
):
    monkeypatch.setattr(reports, "PROGRESS_EDIT_SECONDS", 1000)
    chat, console = Recorder(), live_console(monkeypatch)
    reporter = Reporter(everyone([chat, console]), "a calculator", pid=4242)
    keep_job(home_store, 1, 4242, snapshot(step="spec", doing=Tracker.DOING_AND_DONE["spec"][0], tokens=10))
    reporter.refresh()
    edits = len(chat.log)
    keep_job(home_store, 1, 4242, snapshot(step="spec", doing=Tracker.DOING_AND_DONE["spec"][0], tokens=900))
    reporter.refresh()
    assert len(chat.log) == edits
    assert console.screen.live[2]["tokens"] == 900


@pytest.mark.spec("CNV-16")
def test_progress_edits_come_within_about_two_seconds():
    assert PROGRESS_EDIT_SECONDS <= 2 < CLOCK_ONLY_EDIT_SECONDS


@pytest.mark.spec("CNV-16")
def test_a_reporter_without_a_tracker_shows_its_lines(monkeypatch):
    monkeypatch.setattr(reports, "PROGRESS_EDIT_SECONDS", 1000)
    channel = Recorder()
    learning = Reporter(everyone([channel]), "learn rust", pid=999999)
    learning.printed("  reading 4 pages")
    assert learning.progress.snapshot["latest"] == "reading 4 pages"


@pytest.mark.spec("CNV-16")
def test_a_reporter_without_a_tracker_ends_with_its_title_and_last_line(monkeypatch):
    monkeypatch.setattr(reports, "PROGRESS_EDIT_SECONDS", 1000)
    channel = Recorder()
    calibrating = Reporter(everyone([channel]), "calibrate qwen", pid=999999)
    calibrating.update()
    for line in (
        "📏 Testing qwen",
        "   📥 Checking it can read",
        "✅ qwen is ready\n",
        "   📖 Reads\n",
        "   🧠 Solved\n",
    ):
        calibrating.printed(line)
    calibrating.finish()
    ending = channel.texts()[-1]
    assert "🏁 **calibrate qwen**" in ending and "✅ qwen is ready\n   📖 Reads\n   🧠 Solved" in ending
    assert "Tinkering" not in ending and "Testing" not in ending and "Checking" not in ending


def keep_job(home_store, number, pid, progress, **job):
    kept = {"number": number, "command": "task", "title": "task", "state": "running", "pid": pid, **job}
    home_store.put("jobs", str(number), dict(kept, progress=progress) if progress else kept)


@pytest.fixture
def attached(monkeypatch, home_store):

    handler = signal.getsignal(JOB_STOP_SIGNAL)

    def start(title="calc: a calculator"):
        tracker = Tracker(title)
        tracker.publish()
        return attach(tracker)

    yield start
    signal.signal(JOB_STOP_SIGNAL, handler)


@pytest.mark.spec("CNV-17")
def test_a_terminal_task_reports_itself_and_stops_on_a_signal(attached, home_store):
    task = attached()
    assert task.tracker.state["reports"] is True
    task.tracker.phase("recall")
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), JOB_STOP_SIGNAL)
        time.sleep(1)
