import json
import os
import signal
import time
from pathlib import Path

import pytest

from tinkerkit import instructions
from tinkerkit.instructions import Backups, Instructions, Run, RunEnd
from tinkerkit.settings import Home


@pytest.mark.spec("INS-1")
def test_an_instruction_starts_with_its_request_as_the_thread_in_the_home_folder(home_store):
    entry = Instructions.add("list the big files\nin my downloads")
    assert entry == {
        "number": 1,
        "title": "list the big files",
        "request": "list the big files\nin my downloads",
        "thread": [{"said": "request", "text": "list the big files\nin my downloads"}],
        "folder": "~",
        "runs": [],
    }
    assert Instructions.get(1) == entry


@pytest.mark.spec("INS-1")
def test_a_long_first_line_is_cut_for_the_title(home_store):
    entry = Instructions.add("x" * 300)
    assert len(entry["title"]) == 140
    assert entry["title"].endswith("...")


@pytest.mark.spec("INS-1")
def test_a_number_is_never_given_again(home_store):
    Instructions.add("one")
    Instructions.add("two")
    home_store.delete("instructions", "2")
    assert Instructions.add("three")["number"] == 3


@pytest.mark.spec("INS-1")
def test_instructions_list_newest_first_ten_to_a_page(home_store):
    for number in range(1, 13):
        Instructions.add(f"request {number}")
    first = Instructions.listed(1)
    assert first.pages == 2
    assert [entry["number"] for entry in first.items] == list(range(12, 2, -1))
    assert [entry["number"] for entry in Instructions.listed(2).items] == [2, 1]


@pytest.mark.spec("INS-1")
def test_no_instructions_still_give_one_page(home_store):
    assert Instructions.listed(1) == ([], 1)


@pytest.mark.spec("INS-1")
def test_each_turn_is_said_onto_the_thread(home_store):
    Instructions.add("tidy up")
    Instructions.said(1, "proposal", "rm -r tmp")
    Instructions.said(1, "output", "done")
    assert Instructions.get(1)["thread"] == [
        {"said": "request", "text": "tidy up"},
        {"said": "proposal", "text": "rm -r tmp"},
        {"said": "output", "text": "done"},
    ]


@pytest.mark.spec("INS-8")
def test_an_amended_stretch_is_the_corrected_request():
    thread = [
        {"said": "request", "text": "tidy up"},
        {"said": "proposal", "text": "rm -r tmp"},
        {"said": "output", "text": "done"},
        {"said": "amend", "text": "the logs too"},
        {"said": "proposal", "text": "rm logs"},
        {"said": "amend", "text": "only old ones"},
        {"said": "proposal", "text": "rm logs/old"},
    ]
    assert instructions.corrected_request(thread) == "the logs too\nonly old ones"


@pytest.mark.spec("INS-8")
def test_a_stretch_with_one_proposal_has_no_corrected_request():
    thread = [{"said": "request", "text": "tidy up"}, {"said": "proposal", "text": "rm -r tmp"}]
    assert instructions.corrected_request(thread) == ""


@pytest.mark.spec("INS-8")
def test_a_run_keeps_the_corrected_request_of_its_thread(home_store, tmp_path):
    Instructions.add("tidy up")
    Instructions.said(1, "proposal", "rm -r tmp")
    Instructions.said(1, "amend", "only tmp/old")
    Instructions.said(1, "proposal", "rm -r tmp/old")
    Instructions.ran(1, tmp_path, "1", {"line": "rm -r tmp/old"})
    assert Instructions.get(1)["runs"][-1]["corrected"] == "tidy up\nonly tmp/old"


@pytest.mark.spec("INS-1")
def test_a_move_keeps_the_folder_for_the_next_proposal(home_store):
    Instructions.add("go to projects")
    Instructions.moved(1, "~/Projects")
    assert Instructions.get(1)["folder"] == "~/Projects"


@pytest.mark.spec("INS-4")
def test_a_started_run_is_recorded_with_its_chat(home_store, tmp_path):
    Instructions.add("tidy up")
    Instructions.ran(1, tmp_path / "1", "chat-1", {"line": "rm -r tmp", "asked": "tidy up"})
    assert Instructions.get(1)["runs"] == [
        {
            "folder": str(tmp_path / "1"),
            "chat": "chat-1",
            "line": "rm -r tmp",
            "asked": "tidy up",
            "does": "",
            "touches": [],
            "undo": None,
            "irreversible": False,
            "posted": False,
            "corrected": "",
        }
    ]


@pytest.mark.spec("INS-6")
def test_a_started_run_keeps_what_its_line_does_touches_and_how_to_undo_it(home_store, tmp_path):
    Instructions.add("tidy up")
    proposed = {
        "line": "rm -r tmp",
        "asked": "tidy up",
        "does": "removes tmp",
        "touches": ["tmp"],
        "undo": "none",
    }
    Instructions.ran(1, tmp_path / "1", "1", proposed)
    run = Instructions.get(1)["runs"][-1]
    assert (run["does"], run["touches"], run["undo"]) == ("removes tmp", ["tmp"], "none")


@pytest.mark.spec("INS-5")
def test_a_posted_run_is_marked_and_the_others_kept_unposted(home_store, tmp_path):
    Instructions.add("tidy up")
    Instructions.ran(1, tmp_path / "1", "1", {"line": "ls"})
    Instructions.ran(1, tmp_path / "2", "1", {"line": "ls"})
    Instructions.posted(1, tmp_path / "2")
    assert [run["posted"] for run in Instructions.get(1)["runs"]] == [False, True]


@pytest.mark.spec("INS-1")
def test_the_working_folder_is_where_the_last_run_ended(home_store, tmp_path):
    (elsewhere := tmp_path / "elsewhere").mkdir()
    Instructions.add("go elsewhere")
    run_folder = Run.start(1, f"cd {elsewhere}", "~")
    Instructions.ran(1, run_folder, "1", {"line": "ls"})
    ended_run(run_folder)
    assert Instructions.working_folder(1) == str(elsewhere.resolve())


@pytest.mark.spec("INS-1")
def test_the_working_folder_is_the_entrys_with_no_run_and_kept_over_an_unknown_end(home_store, tmp_path):
    Instructions.add("tidy up")
    assert Instructions.working_folder(1) == "~"
    (run_folder := tmp_path / "1").mkdir()
    (run_folder / "state").write_text(json.dumps({"pid": 0, "began": ""}))
    Instructions.ran(1, run_folder, "1", {"line": "ls"})
    assert Instructions.working_folder(1) == "~"


@pytest.fixture
def work(tmp_path):
    (folder := tmp_path / "work").mkdir()
    return folder


@pytest.fixture(autouse=True)
def no_run_left_alive(tinker_home):
    yield
    for state in tinker_home.glob("instruction/*/*/state"):
        Run.kill_left(state.parent)


def ended_run(run_folder):
    deadline = time.monotonic() + 10
    while (end := Run.ended(run_folder)) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    return end


@pytest.mark.spec("INS-4")
def test_each_run_gets_the_next_free_run_folder(tinker_home):
    assert Home.run_folder(3) == tinker_home / "instruction" / "3" / "1"
    assert Home.run_folder(3) == tinker_home / "instruction" / "3" / "2"


@pytest.mark.spec("INS-4")
def test_a_started_line_returns_at_once_and_is_still_going(tinker_home, work):
    began = time.monotonic()
    run_folder = Run.start(1, "sleep 5", str(work))
    assert time.monotonic() - began < 2
    assert Run.ended(run_folder) is None
    os.killpg(json.loads((run_folder / "state").read_text())["pid"], signal.SIGTERM)


@pytest.mark.spec("INS-4")
def test_an_ended_line_gives_its_exit_code_and_output_and_writes_nothing_where_it_ran(tinker_home, work):
    run_folder = Run.start(1, "echo hello", str(work))
    assert ended_run(run_folder) == RunEnd(0, "hello", str(work.resolve()))
    assert list(work.iterdir()) == []
    assert (run_folder / "run.sh").read_text() == f"cd {work}\nset -o pipefail\necho hello\n"


@pytest.fixture
def touched(work):
    (work / "notes.txt").write_text("before")
    (work / "pages").mkdir()
    (work / "pages" / "one.md").write_text("page one")
    return work


@pytest.mark.spec("INS-4")
def test_each_existing_touched_path_is_backed_up_before_the_line_runs(tinker_home, touched):
    line = "echo after > notes.txt; rm -r pages"
    run_folder = Run.start(1, line, str(touched), touches=("notes.txt", str(touched / "pages"), "gone.txt"))
    ended_run(run_folder)
    backups = Run.backups(run_folder)
    assert not backups.irreversible
    assert set(backups.restores) == {str(touched / "notes.txt"), str(touched / "pages")}
    assert Path(backups.restores[str(touched / "notes.txt")]).read_text() == "before"
    assert (Path(backups.restores[str(touched / "pages")]) / "one.md").read_text() == "page one"
    assert all(Path(backup).is_relative_to(run_folder) for backup in backups.restores.values())


@pytest.mark.spec("INS-4")
def test_touches_past_the_backup_limit_make_no_backup_and_the_run_irreversible(
    tinker_home, touched, monkeypatch
):
    monkeypatch.setattr(instructions, "BACKUP_MAX_BYTES", 10)
    run_folder = Run.start(1, "true", str(touched), touches=("notes.txt", "pages"))
    ended_run(run_folder)
    assert Run.backups(run_folder) == Backups({}, True)
    assert not (run_folder / "backup").exists()


@pytest.mark.spec("INS-4")
def test_a_failed_clone_leaves_no_backup_and_the_run_irreversible(tinker_home, touched, monkeypatch):
    monkeypatch.setattr(Run, "CLONE", ("false",))
    run_folder = Run.start(1, "true", str(touched), touches=("notes.txt",))
    ended_run(run_folder)
    assert Run.backups(run_folder) == Backups({}, True)
    assert not (run_folder / "backup").exists()


@pytest.fixture
def unreadable(touched):
    locked = touched / "pages" / "locked"
    locked.mkdir()
    locked.chmod(0)
    yield touched
    locked.chmod(0o755)


@pytest.mark.spec("INS-4")
def test_a_touched_path_that_cannot_be_sized_runs_the_line_with_no_backup_and_irreversible(
    tinker_home, unreadable
):
    run_folder = Run.start(1, "echo ran", str(unreadable), touches=("notes.txt", "pages"))
    assert ended_run(run_folder).code == 0
    assert Run.backups(run_folder) == Backups({}, True)
    assert not (run_folder / "backup").exists()


@pytest.mark.spec("INS-4")
def test_a_run_touching_nothing_has_no_backups_and_stays_reversible(tinker_home, work):
    run_folder = Run.start(1, "true", str(work))
    ended_run(run_folder)
    assert Run.backups(run_folder) == Backups({}, False)


@pytest.mark.spec("INS-7")
def test_only_backed_up_paths_changed_after_the_run_ended_are_overwritten(tinker_home, touched):
    run_folder = Run.start(1, "echo after > notes.txt", str(touched), touches=("notes.txt", "pages"))
    ended_run(run_folder)
    later = (run_folder / "ended").stat().st_mtime + 5
    os.utime(touched / "pages", (later, later))
    assert Run.overwritten(run_folder) == [str(touched / "pages")]


@pytest.mark.spec("INS-7")
def test_a_removed_backed_up_path_is_not_overwritten(tinker_home, touched):
    run_folder = Run.start(1, "rm notes.txt", str(touched), touches=("notes.txt",))
    ended_run(run_folder)
    assert Run.overwritten(run_folder) == []


@pytest.mark.spec("INS-7")
def test_a_restore_puts_each_backup_back_and_a_folder_replaces_the_folder(tinker_home, touched):
    line = "echo after > notes.txt; rm -r pages; mkdir pages; echo new > pages/new.md"
    run_folder = Run.start(1, line, str(touched), touches=("notes.txt", "pages"))
    ended_run(run_folder)
    Run.restore(run_folder)
    assert (touched / "notes.txt").read_text() == "before"
    assert sorted(path.name for path in (touched / "pages").iterdir()) == ["one.md"]
    assert (touched / "pages" / "one.md").read_text() == "page one"


@pytest.mark.spec("INS-7")
def test_a_restore_brings_back_a_removed_path(tinker_home, touched):
    run_folder = Run.start(1, "rm -r notes.txt pages", str(touched), touches=("notes.txt", "pages"))
    ended_run(run_folder)
    Run.restore(run_folder)
    assert (touched / "notes.txt").read_text() == "before"
    assert (touched / "pages" / "one.md").read_text() == "page one"


@pytest.mark.spec("INS-7")
def test_the_run_to_undo_is_the_last_not_undone_like_a_stack(home_store, tmp_path):
    Instructions.add("tidy up")
    assert Instructions.to_undo(1) is None
    Instructions.ran(1, tmp_path / "1", "1", {"line": "ls"})
    Instructions.ran(1, tmp_path / "2", "1", {"line": "pwd"})
    assert Instructions.to_undo(1)["folder"] == str(tmp_path / "2")
    Instructions.undone(1, tmp_path / "2")
    assert [run.get("undone", False) for run in Instructions.get(1)["runs"]] == [False, True]
    assert Instructions.to_undo(1)["folder"] == str(tmp_path / "1")
    Instructions.undone(1, tmp_path / "1")
    assert Instructions.to_undo(1) is None


@pytest.mark.spec("INS-7")
def test_an_undo_run_is_never_on_the_undo_stack(home_store, tmp_path):
    Instructions.add("tidy up")
    Instructions.ran(1, tmp_path / "1", "1", {"line": "touch made"})
    Instructions.undone(1, tmp_path / "1")
    Instructions.ran(1, tmp_path / "2", "1", {"line": "rm made", "undoes": "touch made"})
    assert Instructions.get(1)["runs"][-1]["undoes"] == "touch made"
    assert Instructions.to_undo(1) is None


@pytest.mark.spec("INS-4")
def test_a_failing_line_gives_its_exit_code(tinker_home, work):
    assert ended_run(Run.start(1, "echo no >&2; exit 3", str(work))) == RunEnd(3, "no", str(work.resolve()))


@pytest.mark.spec("INS-4")
def test_a_pipe_whose_part_fails_ends_with_that_failure(tinker_home, work):
    assert ended_run(Run.start(1, "ls --bad-flag 2>/dev/null | sort", str(work))).code not in (0, None)


@pytest.mark.spec("INS-4")
def test_a_run_output_is_kept_without_terminal_escapes(tinker_home, work):
    line = r"printf '\033[31mred\033[0m \033]0;title\007\033(Bdone\033[?1049h'"
    assert ended_run(Run.start(1, line, str(work))) == RunEnd(0, "red done", str(work.resolve()))


@pytest.mark.spec("INS-4")
def test_a_failed_run_that_set_terminal_modes_needs_a_terminal(tinker_home, work):
    end = ended_run(Run.start(1, r"printf '\033[>4;mVim: Error reading input'; exit 1", str(work)))
    assert end.needs_terminal and end.output == "Vim: Error reading input"


@pytest.mark.spec("INS-4")
def test_a_coloured_or_successful_run_needs_no_terminal(tinker_home, work):
    assert not ended_run(Run.start(1, r"printf '\033[31mno\033[0m'; exit 1", str(work))).needs_terminal
    assert not ended_run(Run.start(1, r"printf '\033[?1049h'", str(work))).needs_terminal


@pytest.mark.spec("INS-4")
def test_a_cd_in_the_line_gives_the_folder_it_ended_in(tinker_home, work):
    (work / "inner").mkdir()
    end = ended_run(Run.start(1, "cd inner", str(work)))
    assert end.folder == str((work / "inner").resolve())


@pytest.mark.spec("INS-4")
def test_a_line_reading_input_gets_nothing(tinker_home, work):
    end = ended_run(Run.start(1, "cat; echo read", str(work)))
    assert end == RunEnd(0, "read", str(work.resolve()))


@pytest.mark.spec("INS-5")
def test_a_run_gone_without_an_exit_code_ended_unknown(tinker_home):
    run_folder = Home.run_folder(1)
    (run_folder / "state").write_text(json.dumps({"pid": 999999, "began": "long ago"}))
    (run_folder / "output.log").write_text("half done\n")
    assert Run.ended(run_folder) == RunEnd("unknown", "half done", None)


@pytest.mark.spec("INS-6")
def test_a_stopped_run_ends_without_success(tinker_home, work):
    run_folder = Run.start(1, "sleep 30", str(work))
    assert Run.stop(run_folder) is True
    assert ended_run(run_folder).code not in (0, None)


@pytest.mark.spec("INS-6")
def test_a_stop_leaves_stopped_in_the_run_folder(tinker_home, work):
    run_folder = Run.start(1, "sleep 30", str(work))
    assert Run.stop(run_folder) is True
    ended_run(run_folder)
    assert (run_folder / "stopped").exists()


@pytest.mark.spec("INS-6")
def test_a_stopped_run_gone_without_an_exit_code_ended_stopped(tinker_home):
    run_folder = Home.run_folder(1)
    (run_folder / "state").write_text(json.dumps({"pid": 999999, "began": "long ago"}))
    (run_folder / "output.log").write_text("half done\n")
    (run_folder / "stopped").write_text("")
    assert Run.ended(run_folder) == RunEnd("stopped", "half done", None)


@pytest.mark.spec("INS-6")
def test_a_stopped_run_with_an_exit_code_ended_with_that_code(tinker_home):
    run_folder = Home.run_folder(1)
    (run_folder / "state").write_text(json.dumps({"pid": 999999, "began": "long ago"}))
    (run_folder / "stopped").write_text("")
    (run_folder / "ended").write_text("143\n")
    assert Run.ended(run_folder).code == 143


@pytest.mark.spec("INS-6")
def test_a_paused_run_goes_on_and_finishes(tinker_home, work):
    run_folder = Run.start(1, "sleep 0.5; echo done", str(work))
    assert Run.pause(run_folder) is True
    time.sleep(1)
    assert Run.ended(run_folder) is None
    assert Run.go_on(run_folder) is True
    assert ended_run(run_folder) == RunEnd(0, "done", str(work.resolve()))


@pytest.mark.spec("INS-6")
def test_a_paused_run_is_marked_paused_until_it_goes_on(tinker_home, work):
    run_folder = Run.start(1, "sleep 30", str(work))
    Run.pause(run_folder)
    assert (run_folder / "paused").exists()
    Run.go_on(run_folder)
    assert not (run_folder / "paused").exists()
    Run.stop(run_folder)
    ended_run(run_folder)


@pytest.mark.spec("INS-6")
def test_an_ended_run_is_never_marked_paused(tinker_home, work):
    run_folder = Run.start(1, "true", str(work))
    ended_run(run_folder)
    assert Run.pause(run_folder) is False and not (run_folder / "paused").exists()


@pytest.mark.spec("INS-6")
def test_a_stopped_paused_run_still_ends(tinker_home, work):
    run_folder = Run.start(1, "sleep 30", str(work))
    Run.pause(run_folder)
    Run.stop(run_folder)
    assert ended_run(run_folder) is not None


@pytest.mark.spec("INS-6")
def test_a_line_trapping_term_is_killed_after_the_grace(tinker_home, work, monkeypatch):
    monkeypatch.setattr("tinkerkit.instructions.RUN_STOP_GRACE_SECONDS", 0.5)
    run_folder = Run.start(1, "trap '' TERM; while :; do sleep 0.1; done", str(work))
    time.sleep(0.3)
    began = time.monotonic()
    Run.stop(run_folder)
    assert time.monotonic() - began < 0.4
    assert ended_run(run_folder) is not None
    group = json.loads((run_folder / "state").read_text())["pid"]
    deadline = time.monotonic() + 5
    while Run.group_left(group) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not Run.group_left(group)


@pytest.mark.parametrize("control", [Run.stop, Run.pause, Run.go_on])
def test_control_of_an_ended_run_does_nothing(tinker_home, work, control):
    run_folder = Run.start(1, "echo hello", str(work))
    ended_run(run_folder)
    assert control(run_folder) is False
    gone = Home.run_folder(1)
    (gone / "state").write_text(json.dumps({"pid": 999999, "began": "long ago"}))
    assert control(gone) is False
