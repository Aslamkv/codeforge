import contextlib
import os
import signal
lazy import datetime
lazy import json
lazy import subprocess
lazy import sys
lazy import threading
lazy import time
lazy from pathlib import Path

lazy from .commands import kind_of
lazy from .messages import say
lazy from .settings import EXERCISE_ENV, Home, Numbered, confirm_deletion, fingerprint, now_text
lazy from .store import Log

JOBS = Numbered("jobs", "job numbers")
STATES = ("running", "paused", "stopped", "finished", "archived")
JOB_PAUSE_GRACE_SECONDS = 150
PAUSED_JOB_IDLE_LOOK_SECONDS = 1.0
JOB_STOP_SIGNAL = signal.SIGUSR2


class Journal:
    def __init__(self, number):
        self.log, self.lock, self.kept = f"journal #{number}", threading.Lock(), {}
        for _, entry in Home.store().entries(self.log):
            self.kept.setdefault(entry["key"], []).append(entry["answer"])

    def answer(self, request, ask, keep, dump, load):
        key = fingerprint(json.dumps(request, sort_keys=True, ensure_ascii=False, default=str))
        with self.lock:
            kept = self.kept.get(key)
            if kept:
                return load(kept.pop(0))
        answer = ask()
        if keep(answer):
            Home.store().append(self.log, {"key": key, "answer": dump(answer)})
        return answer

    def drop(self):
        Home.store().drop_log(self.log)


def journaled(
    request, ask, keep=lambda answer: answer is not None, dump=lambda answer: answer, load=lambda kept: kept
):
    if Jobs.journal is None:
        return ask()
    return Jobs.journal.answer(request, ask, keep, dump, load)


def calls_log():
    return Log(Home.store(), f"calls #{Jobs.current['number']}" if Jobs.current else None)


def today():
    started = (Jobs.current or {}).get("today")
    return datetime.date.fromisoformat(started) if started else datetime.date.today()


class Jobs:
    current = None
    journal = None
    ended_status = None
    resumed_here = False

    @classmethod
    @contextlib.contextmanager
    def running(cls, argv):
        if not is_job(argv):
            yield
            return
        cls.begin(argv)
        signal.signal(JOB_STOP_SIGNAL, interrupted)
        try:
            yield
        except KeyboardInterrupt:
            cls.ended_status = cls.ended_status or "stopped"
            raise
        finally:
            cls.close()

    @classmethod
    def begin(cls, argv):
        job = cls.current or cls.new(argv)
        cls.current = cls.save(
            dict(
                job,
                state="running",
                pid=os.getpid(),
                pid_began=began_at(os.getpid()),
                paused_by=None,
                go_now=False,
            )
        )
        cls.journal, cls.ended_status = Journal(job["number"]), None
        cls.pause_running(job["number"])

    @staticmethod
    def pause_running(by):
        others = [job for job in Jobs.listed("running") if job["pid"] != os.getpid()]
        for job in others:
            say.info(f"Pausing #{job['number']} {job['title']}: it goes on once Tinker is idle", "⏸")
            Jobs.update(job["number"], paused_by=by)
            signal_job(job)
        ends = time.monotonic() + JOB_PAUSE_GRACE_SECONDS
        while Jobs.still_running(others) and time.monotonic() < ends:
            time.sleep(0.5)
        return others

    @staticmethod
    def still_running(jobs):
        return any(Jobs.get(job["number"])["state"] == "running" for job in jobs)

    @classmethod
    def new(cls, argv):
        number = JOBS.next_number()
        title = " ".join(word for word in argv if not word.startswith("--"))
        return {
            "number": number,
            "command": argv[0],
            "title": title,
            "arguments": list(argv),
            "started": now_text(),
            "today": datetime.date.today().isoformat(),
        }

    @classmethod
    def close(cls):
        number, paused = cls.current["number"], cls.ended_status in ("stopped", "paused")
        paused_by = (Jobs.get(number) or {}).get("paused_by") if paused else None
        state = "paused" if paused_by else "stopped" if paused else "finished"
        cls.update(number, state=state)
        if state == "finished":
            cls.journal.drop()
        cls.current = cls.journal = None
        if paused_by:
            go_on_when_idle(number)

    @classmethod
    def ended(cls, ending):
        if cls.current is None:
            return ending
        cls.ended_status, number = ending.get("status"), cls.current["number"]
        if cls.ended_status != "stopped":
            return ending
        paused_by = (Jobs.get(number) or {}).get("paused_by")
        if paused_by:
            cls.ended_status = "paused"
            asker = "you" if paused_by == "you" else f"job #{paused_by}"
            return dict(
                ending,
                status="paused",
                headline=f"Paused for {asker}: it goes on once Tinker is idle",
                next=[("Go on now", f"tinker resume #{number}")],
            )
        return dict(ending, next=list(ending.get("next") or []) + [("Go on", f"tinker resume #{number}")])

    @classmethod
    def retarget(cls, arguments):
        if cls.current is not None:
            cls.current = cls.update(cls.current["number"], arguments=list(arguments))

    @staticmethod
    def update(number, **changes):
        return JOBS.update(number, lambda job: dict(job, **changes, updated=now_text()))

    @staticmethod
    def save(job):
        return JOBS.put(dict(job, updated=now_text()))

    @staticmethod
    def get(number):
        job = JOBS.get(number)
        return settled(job) if job else None

    @staticmethod
    def of_process(pid):
        return next((job for job in JOBS.newest() if job.get("pid") == pid), None)

    @staticmethod
    def every():
        return [settled(job) for job in JOBS.newest()]

    @staticmethod
    def lost():
        return [
            job
            for job in JOBS.newest()
            if job.get("state") in ("running", "paused") and not alive(job.get("pid"), job.get("pid_began"))
        ]

    @staticmethod
    def listed(state=None):
        jobs = Jobs.every()
        states = [state] if state else ["running", "paused", "stopped"]
        return [job for wanted in states for job in jobs if job["state"] == wanted]

    @staticmethod
    def resumable(number=None):
        if number is None:
            stopped = sorted(Jobs.listed("stopped"), key=lambda job: job["updated"], reverse=True)
            return (stopped[0], "") if stopped else (None, "No job is stopped (tinker jobs lists them).")
        job = Jobs.get(number)
        if job is None:
            return None, f"No job #{number} (tinker jobs lists them)."
        if job["state"] == "running" and job["pid"] != os.getpid():
            return None, f"Job #{number} is running (stop it with tinker stop #{number})."
        if job["state"] == "paused" and job["pid"] != os.getpid():
            return None, f"Job #{number} is paused: it goes on by itself once Tinker is idle."
        if job["state"] in ("finished", "archived"):
            return None, f"Job #{number} is {job['state']}: there is nothing left to resume."
        return job, ""

    @classmethod
    def arguments(cls, argv):
        if argv[:1] != ["resume"] or len(argv) > 2:
            return argv
        number = job_number(argv[1]) if len(argv) == 2 else None
        resumes_a_project = len(argv) == 2 and number is None
        if resumes_a_project:
            return argv
        if cls.wake(number):
            say.done(f"Job #{number} goes on now, in the process where it was paused.")
            raise SystemExit(0)
        job, why = cls.resumable(number)
        resumes_this_folders_run = job is None and number is None
        if resumes_this_folders_run:
            return argv
        if job is None:
            raise SystemExit(why)
        cls.current, cls.resumed_here = job, True
        return job["arguments"]

    @staticmethod
    def wake(number):
        job = Jobs.get(number) if number is not None else None
        if not Jobs.paused_elsewhere(job):
            return False
        Jobs.update(number, go_now=True)
        return True

    @staticmethod
    def paused_elsewhere(job):
        return job and job["state"] == "paused" and job["pid"] != os.getpid()

    @staticmethod
    def stop(job):
        if job["state"] == "paused":
            Jobs.update(job["number"], state="stopped", paused_by=None)
        else:
            signal_job(job)


def archived(numbers, yes=False):
    jobs = [archivable(number) for number in numbers] or Jobs.listed("stopped") + Jobs.listed("finished")
    if not jobs:
        say.info("Nothing to clear: no job is stopped or finished.")
        return
    numbered = " ".join(f"#{job['number']}" for job in jobs)
    confirm_deletion(
        "This archives (tinker jobs archived lists them; they cannot be resumed):",
        [f"#{job['number']} {job['state']}: {job['title']}" for job in jobs],
        f"tinker clear jobs {numbered}",
        yes,
        verb="archive",
    )
    for job in jobs:
        Jobs.update(job["number"], state="archived", archived_from=job["state"])
        Home.store().drop_log(f"journal #{job['number']}")
        Home.store().drop_log(f"calls #{job['number']}")
    say.done(f"Archived {numbered}.")


def archivable(number):
    job = Jobs.get(number)
    if job is None:
        raise SystemExit(f"No job #{number} (tinker jobs lists them).")
    if job["state"] in ("running", "paused"):
        raise SystemExit(f"Job #{number} is {job['state']}: stop it first (tinker stop #{number}).")
    if job["state"] == "archived":
        raise SystemExit(f"Job #{number} is archived already (tinker jobs archived lists them).")
    return job


def go_on_when_idle(number):
    while True:
        job = Jobs.get(number)
        if job["state"] != "paused":
            return
        if job.get("go_now") or its_turn(number):
            break
        time.sleep(PAUSED_JOB_IDLE_LOOK_SECONDS)
    say.info(f"Going on with #{number} {job['title']}", "▶️")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(Path(sys.argv[0]).resolve()), "resume", f"#{number}"])


def its_turn(number):
    return idle() and next_to_go_on() == number


@contextlib.contextmanager
def paused_for_you():
    key = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
    Home.store().update("activity", "answering", lambda asked: dict(asked or {}, **{key: os.getpid()}))
    try:
        Jobs.pause_running("you")
        yield
    finally:
        Home.store().update(
            "activity",
            "answering",
            lambda asked: {name: value for name, value in (asked or {}).items() if name != key},
        )


def idle():
    asked = Home.store().get("activity", "answering") or {}
    return not Jobs.listed("running") and not any(alive(pid) for pid in asked.values())


def next_to_go_on():
    paused = Jobs.listed("paused")
    return paused[0]["number"] if paused else None


def signal_job(job):
    try:
        os.kill(job["pid"], JOB_STOP_SIGNAL)
    except ProcessLookupError, PermissionError:
        pass


def is_job(argv):
    if {"-h", "--help"} & set(argv) or os.environ.get(EXERCISE_ENV):
        return False
    return kind_of(argv) == "job"


def job_number(word):
    word = str(word or "").strip()
    return int(word[1:]) if word.startswith("#") and word[1:].isdigit() else None


def settled(job):
    if job.get("state") in ("running", "paused") and not alive(job.get("pid"), job.get("pid_began")):
        return dict(job, state="stopped")
    return job


def alive(pid, began=None):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return began is None or began == began_at(pid)


def began_at(pid):
    done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return done.stdout.strip()


def interrupted(signum, frame):
    raise KeyboardInterrupt
