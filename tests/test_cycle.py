import pytest
from conftest import plan_of

from tinkerkit.build import cycle as cycles
from tinkerkit.build.cycle import PHASE_CLASSES, CycleRunner
from tinkerkit.build.plan import Recaller
from tinkerkit.build.project import Tracker
from tinkerkit.build.session import Phase
from tinkerkit.knowledge.library import Knowledge
from tinkerkit.settings import FIX_PHASES, PHASES


def recording_phases(monkeypatch, stop_at=None, titled=False):
    ran = []

    def recorded(real):
        class Recorded(real):
            TITLE = real.TITLE if titled else ""

            def run(self, cycle):
                ran.append(cycle["phase"])
                return False if cycle["phase"] == stop_at else None

        return Recorded

    monkeypatch.setattr(
        cycles, "PHASE_CLASSES", {name: recorded(real) for name, real in PHASE_CLASSES.items()}
    )
    return ran


def replaced(session, index, **fields):
    session.project.cycles[index] = dict(session.project.cycles[index], **fields)
    return session.project.cycles[index]


@pytest.mark.spec("CYC-1")
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


@pytest.mark.spec("REM-1")
def test_a_cycle_writes_one_episode_when_it_ends_not_while_it_waits(session, monkeypatch):
    from tinkerkit.memory.memories import Scratchpad

    recording_phases(monkeypatch)
    cycle = replaced(session, 0, confirm=True)
    assert CycleRunner(session).run(cycle) == "awaiting" and Scratchpad.fresh() == []
    cycle.update(awaiting=False, confirmed=True)
    assert CycleRunner(session).run(cycle) == "done"
    [episode] = Scratchpad.fresh()
    assert episode["task"] == cycle["request"] and episode["status"] == "done" and episode["chat"] == "cli"


@pytest.mark.spec("CYC-1")
def test_cycle_waits_after_its_plan_unless_told_not_to(session, monkeypatch):
    ran = recording_phases(monkeypatch)
    cycle = replaced(session, 0, confirm=True)
    assert CycleRunner(session).run(cycle) == "awaiting" and ran == ["recall", "spec", "design", "review"]
    assert cycle["phase"] == "setup" and cycle["awaiting"]
    cycle.update(awaiting=False, confirmed=True)
    assert CycleRunner(session).run(cycle) == "done"
    session.project.new_cycle("f1", "a bug", kind="fix")
    fix = replaced(session, -1, confirm=True)
    ran.clear()
    assert CycleRunner(session).run(fix) == "awaiting" and ran == ["recall", "reproduce"]
    session.project.new_cycle("f2", "more", kind="feature")
    feature = replaced(session, -1, confirm=False)
    assert CycleRunner(session).run(feature) == "done"


@pytest.mark.spec("CYC-1")
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


@pytest.mark.spec("CYC-1")
def test_a_web_run_past_recall_recalls_first(session, monkeypatch):
    ran = recording_phases(monkeypatch)
    recalled = []
    monkeypatch.setattr(Recaller, "run", lambda self, cycle: recalled.append(cycle["phase"]))
    cycle = replaced(session, 0, phase="code")
    assert CycleRunner(session).run(cycle, web=True) == "done"
    assert recalled == ["code"] and ran[0] == "code"
    recalled.clear()
    session.project.new_cycle("f1", "more")
    at_recall_already = replaced(session, -1, phase="recall")
    CycleRunner(session).run(at_recall_already, web=True)
    assert recalled == []


@pytest.mark.spec("CYC-1")
def test_every_phase_has_a_phase_class():
    assert set(PHASE_CLASSES) == set(PHASES + FIX_PHASES) - {"done"}
    for name, worker in PHASE_CLASSES.items():
        assert issubclass(worker, Phase) and worker.TITLE and worker.NAME == name


@pytest.mark.spec("CYC-1")
def test_a_phase_is_shown_by_its_title_and_tells_the_tracker_its_outcome(session, monkeypatch, capsys):
    ran = recording_phases(monkeypatch, titled=True)
    told = []
    monkeypatch.setattr(Tracker, "outcome", lambda self, text: told.append(text))
    session.tracker = Tracker("calc")
    cycle = session.project.cycles[0]
    assert CycleRunner(session).run(cycle) == "done" and ran == list(PHASES[:-1])
    assert "Plan: the spec (project)" in capsys.readouterr().out
    assert told == [PHASE_CLASSES[phase](session).outcome(cycle) for phase in ran]
    assert told[ran.index("spec")] == f"{len(session.project.cycle_criteria(cycle))} point(s) to test"
