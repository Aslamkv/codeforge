import contextlib
import os
import re
import signal
from typing import NamedTuple
lazy import json
lazy import shutil
lazy import subprocess
lazy import threading
lazy from datetime import datetime
lazy from pathlib import Path

from .settings import Numbered
lazy from .jobs import alive, launch
lazy from .settings import Home
lazy from .text import first_line

INSTRUCTIONS = Numbered("instructions", "instruction numbers")
RUN_STOP_GRACE_SECONDS = 10
BACKUP_MAX_BYTES = 1 << 30
TERMINAL_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[ -/]*[0-~]")
TERMINAL_MODE = re.compile(r"\x1b\[[?<=>]")


class Instructions:
    @staticmethod
    def add(request):
        return INSTRUCTIONS.add(
            {
                "title": first_line(request),
                "request": request,
                "thread": [{"said": "request", "text": request}],
                "folder": "~",
                "runs": [],
            }
        )

    @staticmethod
    def get(number):
        return INSTRUCTIONS.get(number)

    @staticmethod
    def listed(page):
        return Numbered.page(INSTRUCTIONS.newest(), page)

    @staticmethod
    def said(number, kind, text):
        return INSTRUCTIONS.update(
            number, lambda entry: dict(entry, thread=[*entry["thread"], {"said": kind, "text": text}])
        )

    @staticmethod
    def moved(number, folder):
        return INSTRUCTIONS.update(number, lambda entry: dict(entry, folder=folder))

    @staticmethod
    def ran(number, run_folder, chat, proposed):
        run = {
            "folder": str(run_folder),
            "chat": chat,
            "line": proposed["line"],
            "asked": proposed.get("asked", ""),
            "does": proposed.get("does", ""),
            "touches": proposed.get("touches", []),
            "undo": proposed.get("undo"),
            "irreversible": proposed.get("irreversible", False),
            "posted": False,
        }
        undoes = {"undoes": proposed["undoes"]} if "undoes" in proposed else {}
        at_once = {"at_once": True} if proposed.get("at_once") else {}
        episodes = {"episodes": proposed["episodes"]} if proposed.get("episodes") else {}
        return INSTRUCTIONS.update(
            number,
            lambda entry: dict(
                entry,
                runs=[
                    *entry["runs"],
                    run | {"corrected": corrected_request(entry["thread"])} | undoes | at_once | episodes,
                ],
            ),
        )

    @staticmethod
    def posted(number, run_folder):
        def marked(run):
            return dict(run, posted=True) if run["folder"] == str(run_folder) else run

        return INSTRUCTIONS.update(
            number, lambda entry: dict(entry, runs=[marked(run) for run in entry["runs"]])
        )

    @staticmethod
    def undone(number, run_folder):
        def marked(run):
            return dict(run, undone=True) if run["folder"] == str(run_folder) else run

        return INSTRUCTIONS.update(
            number, lambda entry: dict(entry, runs=[marked(run) for run in entry["runs"]])
        )

    @staticmethod
    def to_undo(number):
        runs = INSTRUCTIONS.get(number)["runs"]
        undoable = (run for run in reversed(runs) if not run.get("undone") and "undoes" not in run)
        return next(undoable, None)

    @staticmethod
    def unposted_runs():
        return [
            (entry["number"], run)
            for entry in reversed(INSTRUCTIONS.newest())
            for run in entry["runs"]
            if not run.get("posted", True)
        ]

    @staticmethod
    def live():
        return [
            entry
            for entry in INSTRUCTIONS.newest()
            if entry["runs"] and Run.ended(entry["runs"][-1]["folder"]) is None
        ]

    @staticmethod
    def working_folder(number):
        entry = INSTRUCTIONS.get(number)
        ends = (Run.ended(run["folder"]) for run in reversed(entry["runs"]))
        return next((end.folder for end in ends if end and end.folder), entry["folder"])


def corrected_request(thread):
    stretch = thread[last_output_index(thread) + 1 :]
    if sum(said["said"] == "proposal" for said in stretch) <= 1:
        return ""
    return "\n".join(said["text"] for said in stretch if said["said"] in ("request", "amend"))


def last_output_index(thread):
    outputs = [index for index, said in enumerate(thread) if said["said"] == "output"]
    return outputs[-1] if outputs else -1


class RunEnd(NamedTuple):
    code: object
    output: str
    folder: object
    needs_terminal: bool = False


class Backups(NamedTuple):
    restores: dict
    irreversible: bool


class Run:
    CLONE = ("cp", "-c", "-R")

    @staticmethod
    def start(number, line, folder, touches=()):
        run_folder = Home.run_folder(number)
        place = os.path.expanduser(folder)
        Run.back_up(run_folder, Run.existing(place, touches))
        pid = launch(run_folder, place, ["set -o pipefail", line], "w")
        Home.keep_awake(pid)
        return run_folder

    @staticmethod
    def existing(place, touches):
        paths = (Path(place, os.path.expanduser(touch)) for touch in touches)
        return list(dict.fromkeys(str(path) for path in paths if path.exists() or path.is_symlink()))

    @staticmethod
    def back_up(run_folder, originals):
        backups = Backups({}, True)
        if Run.fits_backup(originals):
            backups = Run.cloned(run_folder / "backup", originals)
        (run_folder / "backups").write_text(json.dumps(backups._asdict()))

    @staticmethod
    def fits_backup(originals):
        try:
            return Run.size(originals) <= BACKUP_MAX_BYTES
        except OSError:
            return False

    @staticmethod
    def size(originals):
        return sum(Run.path_size(Path(original)) for original in originals)

    @staticmethod
    def path_size(path):
        if path.is_dir() and not path.is_symlink():
            return sum(Run.path_size(inner) for inner in path.iterdir())
        return path.lstat().st_size

    @staticmethod
    def cloned(backup_folder, originals):
        if not originals:
            return Backups({}, False)
        backup_folder.mkdir()
        restores = {original: str(backup_folder / str(index)) for index, original in enumerate(originals)}
        if all(Run.clone(original, backup) for original, backup in restores.items()):
            return Backups(restores, False)
        shutil.rmtree(backup_folder, ignore_errors=True)
        return Backups({}, True)

    @staticmethod
    def clone(original, backup):
        return subprocess.run([*Run.CLONE, original, backup], capture_output=True).returncode == 0

    @staticmethod
    def backups(run_folder):
        return Backups(**json.loads((Path(run_folder) / "backups").read_text()))

    @staticmethod
    def overwritten(run_folder):
        since = Run.finished_at(Path(run_folder))
        originals = (Path(original) for original in Run.backups(run_folder).restores)
        return [str(path) for path in originals if path.exists() and path.stat().st_mtime > since]

    @staticmethod
    def finished_at(run_folder):
        marks = (run_folder / name for name in ("ended", "state"))
        return next(mark.stat().st_mtime for mark in marks if mark.exists())

    @staticmethod
    def restore(run_folder):
        for original, backup in Run.backups(run_folder).restores.items():
            Run.clear(Path(original))
            Run.clone(backup, original)

    @staticmethod
    def clear(path):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    @staticmethod
    def ended(run_folder):
        run_folder = Path(run_folder)
        state = json.loads((run_folder / "state").read_text())
        if (run_folder / "ended").exists():
            code = int((run_folder / "ended").read_text())
            needs_terminal = code != 0 and bool(TERMINAL_MODE.search(Run.raw_output(run_folder)))
            return RunEnd(code, Run.output(run_folder), Run.ended_folder(run_folder), needs_terminal)
        if alive(state["pid"], state["began"]):
            return None
        code = "stopped" if (run_folder / "stopped").exists() else "unknown"
        return RunEnd(code, Run.output(run_folder), None)

    @staticmethod
    def stop(run_folder):
        mark = Path(run_folder) / "stopped"
        mark.write_text("")
        if not Run.signalled(run_folder, signal.SIGCONT, signal.SIGTERM):
            mark.unlink()
            return False
        killer = threading.Timer(RUN_STOP_GRACE_SECONDS, Run.kill_left, (run_folder,))
        killer.daemon = True
        killer.start()
        return True

    @staticmethod
    def kill_left(run_folder):
        state = json.loads((Path(run_folder) / "state").read_text())
        leader_ours = alive(state["pid"], state["began"])
        if leader_ours or (not alive(state["pid"]) and Run.group_left(state["pid"])):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(state["pid"], signal.SIGKILL)

    @staticmethod
    def group_left(group):
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True

    @staticmethod
    def pause(run_folder):
        paused = Run.signalled(run_folder, signal.SIGSTOP)
        if paused:
            (Path(run_folder) / "paused").write_text("")
        return paused

    @staticmethod
    def go_on(run_folder):
        going = Run.signalled(run_folder, signal.SIGCONT)
        if going:
            (Path(run_folder) / "paused").unlink(missing_ok=True)
        return going

    @staticmethod
    def is_paused(run_folder):
        return (Path(run_folder) / "paused").exists()

    @staticmethod
    def seconds_since_began(run_folder):
        began = json.loads((Path(run_folder) / "state").read_text())["began"]
        return (datetime.now() - datetime.strptime(began, "%a %b %d %H:%M:%S %Y")).total_seconds()

    @staticmethod
    def signalled(run_folder, *signals):
        run_folder = Path(run_folder)
        state = json.loads((run_folder / "state").read_text())
        if (run_folder / "ended").exists() or not alive(state["pid"], state["began"]):
            return False
        try:
            for sent in signals:
                os.killpg(state["pid"], sent)
        except ProcessLookupError, PermissionError:
            return False
        return True

    @staticmethod
    def output(run_folder):
        return TERMINAL_ESCAPE.sub("", Run.raw_output(run_folder)).strip()

    @staticmethod
    def raw_output(run_folder):
        log = Path(run_folder) / "output.log"
        return log.read_text(errors="replace") if log.exists() else ""

    @staticmethod
    def ended_folder(run_folder):
        kept = run_folder / "folder"
        return kept.read_text().strip() if kept.exists() else None
