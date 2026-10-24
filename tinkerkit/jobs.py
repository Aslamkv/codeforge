"""Jobs: every long command (a task and its add, fix, amend and resume; learn, ask, teach, research and exercise) is
a job, numbered #1, #2 ... Each is listed by `jobs`, stopped by `stop #N` (as Ctrl-C stops it: it saves what it
saves) and resumed by `resume #N` without doing again what it did: its journal keeps every model call, web search
and page fetch with its answer, and a request made again word for word is answered from it. The first request
that differs is asked anew, and the job goes on live from there. A finished job's journal is deleted: its fruit is
handed over. The job keeps the date it started, so prompts that say today's date ask the same on any later day.

One job runs at a time, whatever kind it is: a job that starts pauses the one that runs, and so does the person's
chat (paused_for_you: what runs is paused while they are answered). A paused job saves itself (as on stop) and
waits in its own process; once Tinker is idle (no job runs, and no request is being answered), the job paused last
goes on by itself at once, as resume #N. resume #N makes a paused job go on at once too.

clear jobs #N archives a stopped or finished job: it is kept, and listed by jobs archived, but never resumed."""

import contextlib
lazy import datetime
lazy import json
import os
import signal
lazy import sys
lazy import threading
lazy import time
lazy from pathlib import Path

lazy from .messages import say
lazy from .settings import EXERCISE_ENV, Home, confirmed, fingerprint, now_text

COMMANDS = ("task", "restart", "resume", "confirm", "amend", "add", "fix", "learn", "ask", "teach", "read",
            "research", "exercise")
STATES = ("running", "paused", "stopped", "finished", "archived")
PAGE = 10              # jobs listed on one page
PAUSE_WAIT = 150       # seconds a job is given to pause (save, and let go of the model) for one that starts
LOOK_EVERY = 1.0       # seconds between looks of a paused job at whether Tinker is idle
STOP = signal.SIGUSR2  # to a job: stop as Ctrl-C does (which a job started in the background ignores)


class Journal:
    """A job's requests and their answers, in the home's log "journal #N". A request made again is answered from
    it, in the order the answers were kept; one never made before (or made more often than kept) is asked."""

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


def journaled(request, ask, keep=lambda answer: answer is not None, dump=lambda answer: answer,
              load=lambda kept: kept):
    """ask()'s answer: in a job, given again from its journal when this very request was made before, else asked
    and kept (when keep(answer): a failure is asked again on resume). dump/load: the answer as JSON and back."""
    if Jobs.journal is None:
        return ask()
    return Jobs.journal.answer(request, ask, keep, dump, load)


def today():
    """Today's date, as the job that runs says it: the day it started."""
    started = (Jobs.current or {}).get("today")
    return datetime.date.fromisoformat(started) if started else datetime.date.today()


class Jobs:
    """The jobs, kept in the home ("jobs", by number): {number, command, title, arguments, state, pid, started,
    updated, today, paused_by}. current: the job this process runs; journal: its journal."""
    current = None
    journal = None
    ending = None  # the status the job's tracker ended with ("stopped", "paused": it can go on)
    resuming = False  # this process goes on with a job (resume #N): its message comes to the bottom

    @classmethod
    @contextlib.contextmanager
    def running(cls, argv):
        """The command, run as a job when it is one (a new job, or the one resumed); its state kept however it
        ends: stopped (Ctrl-C, stop #N, or its tracker says so) or finished (its journal deleted)."""
        if not is_job(argv):
            yield
            return
        cls.begin(argv)
        signal.signal(STOP, interrupted)
        try:
            yield
        except KeyboardInterrupt:
            cls.ending = cls.ending or "stopped"
            raise
        finally:
            cls.close()

    @classmethod
    def begin(cls, argv):
        """This process runs the job: the one resumed (arguments), else a new one; the job that runs is paused."""
        job = cls.current or cls.new(argv)
        cls.current = cls.save(dict(job, state="running", pid=os.getpid(), paused_by=None, go_now=False))
        cls.journal, cls.ending = Journal(job["number"]), None
        cls.pause_running(job["number"])

    @staticmethod
    def pause_running(by):
        """One job runs at a time: the ones that run are told to stop, paused by `by` (a job's number, or "you"),
        and waited for while they save themselves (at most PAUSE_WAIT seconds). The jobs paused."""
        others = [job for job in Jobs.listed("running") if job["pid"] != os.getpid()]
        for job in others:
            say.info(f"Pausing #{job['number']} {job['title']}: it goes on once Tinker is idle", "⏸")
            Jobs.update(job["number"], paused_by=by)
            signal_job(job)
        ends = time.monotonic() + PAUSE_WAIT
        while any(Jobs.get(job["number"])["state"] == "running" for job in others) and time.monotonic() < ends:
            time.sleep(0.5)
        return others

    @classmethod
    def new(cls, argv):
        number = Home.store().update("job numbers", "last", lambda last: (last or 0) + 1)
        title = " ".join(word for word in argv if not word.startswith("--"))  # its words, not its options
        return {"number": number, "command": argv[0], "title": title, "arguments": list(argv),
                "started": now_text(), "today": datetime.date.today().isoformat()}

    @classmethod
    def close(cls):
        """The job's state as it ended: paused (it then waits, and goes on), stopped, or finished (its journal
        dropped)."""
        number, paused = cls.current["number"], cls.ending in ("stopped", "paused")
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
        """The job's tracker ended (ending: its status, headline, details, next): kept for how the job ends, and
        how to go on added to a stopped one's."""
        if cls.current is None:
            return ending
        cls.ending, number = ending.get("status"), cls.current["number"]
        if cls.ending != "stopped":
            return ending
        paused_by = (Jobs.get(number) or {}).get("paused_by")
        if paused_by:
            cls.ending = "paused"
            asker = "you" if paused_by == "you" else f"job #{paused_by}"
            return dict(ending, status="paused", headline=f"Paused for {asker}: it goes on once Tinker is idle",
                        next=[("Go on now", f"tinker resume #{number}")])
        return dict(ending, next=list(ending.get("next") or []) + [("Go on", f"tinker resume #{number}")])

    @classmethod
    def retarget(cls, arguments):
        """What resumes the job from now on (a task, once its run is known: resume @RUN)."""
        if cls.current is not None:
            cls.current = cls.update(cls.current["number"], arguments=list(arguments))

    @staticmethod
    def update(number, **changes):
        """The job changed in one transaction (another process may change it meanwhile: a job that pauses it)."""
        return Home.store().update("jobs", str(number), lambda job: dict(job, **changes, updated=now_text()))

    @staticmethod
    def save(job):
        job = dict(job, updated=now_text())
        Home.store().put("jobs", str(job["number"]), job)
        return job

    @staticmethod
    def get(number):
        job = Home.store().get("jobs", str(number))
        return settled(job) if job else None

    @staticmethod
    def every():
        """Every job, newest first, a job whose process ended without saying how taken as stopped."""
        jobs = [settled(job) for job in Home.store().items("jobs").values()]
        return sorted(jobs, key=lambda job: job["number"], reverse=True)

    @staticmethod
    def listed(state=None):
        """The jobs in a state, newest first; with none, the running ones, the paused ones and the stopped ones."""
        jobs = Jobs.every()
        states = [state] if state else ["running", "paused", "stopped"]
        return [job for wanted in states for job in jobs if job["state"] == wanted]

    @staticmethod
    def page(jobs, number):
        """(the jobs on page number, from 1, and how many pages there are)."""
        pages = max(1, -(-len(jobs) // PAGE))
        return jobs[(number - 1) * PAGE:number * PAGE], pages

    @staticmethod
    def resumable(number=None):
        """The job resume #N goes on with (the stopped one stopped last, when no number is given): it, or why not."""
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
        """The command line to run: resume #N (or resume alone, while a job is stopped) as the job's own command
        line, that job resumed by this process; any other as it is. SystemExit when it cannot be resumed."""
        if argv[:1] != ["resume"] or len(argv) > 2:
            return argv
        number = job_number(argv[1]) if len(argv) == 2 else None
        if len(argv) == 2 and number is None:
            return argv  # resume @PROJECT
        if cls.wake(number):
            say.done(f"Job #{number} goes on now, in the process where it was paused.")
            raise SystemExit(0)
        job, why = cls.resumable(number)
        if job is None and number is None:
            return argv  # resume alone, with no job stopped: this folder's run
        if job is None:
            raise SystemExit(why)
        cls.current, cls.resuming = job, True
        return job["arguments"]

    @staticmethod
    def wake(number):
        """A paused job told to go on now (where it waits, in its own process): True when one was."""
        job = Jobs.get(number) if number is not None else None
        if not job or job["state"] != "paused" or job["pid"] == os.getpid():
            return False
        Jobs.update(number, go_now=True)
        return True

    @staticmethod
    def stop(job):
        """The job told to stop, as Ctrl-C stops it: it saves itself (and its tracker says it stopped). A paused one
        is stopped where it waits: it no longer goes on by itself."""
        if job["state"] == "paused":
            Jobs.update(job["number"], state="stopped", paused_by=None)
        else:
            signal_job(job)

def archived(numbers, yes=False):
    """clear jobs [#N...]: those jobs (every stopped and finished one, with none named) archived once the person
    agrees: kept, and listed by jobs archived, but never resumed (a stopped one's journal goes)."""
    jobs = [archivable(number) for number in numbers] or Jobs.listed("stopped") + Jobs.listed("finished")
    if not jobs:
        say.info("Nothing to clear: no job is stopped or finished.")
        return
    say.warn("This archives (tinker jobs archived lists them; they cannot be resumed):")
    with say.section():
        for job in jobs:
            say.item(f"#{job['number']} {job['state']}: {job['title']}")
    numbered = " ".join(f"#{job['number']}" for job in jobs)
    if not confirmed("Archive them?", f"tinker clear jobs {numbered}", yes):
        say.info("Nothing archived.")
        return
    for job in jobs:
        Jobs.update(job["number"], state="archived", archived_from=job["state"])
        Home.store().drop_log(f"journal #{job['number']}")
    say.done(f"Archived {numbered}.")

def archivable(number):
    """Job #number, one that can be archived: neither running nor paused, nor archived already."""
    job = Jobs.get(number)
    if job is None:
        raise SystemExit(f"No job #{number} (tinker jobs lists them).")
    if job["state"] in ("running", "paused"):
        raise SystemExit(f"Job #{number} is {job['state']}: stop it first (tinker stop #{number}).")
    if job["state"] == "archived":
        raise SystemExit(f"Job #{number} is archived already (tinker jobs archived lists them).")
    return job


def go_on_when_idle(number):
    """A paused job waits until Tinker is idle and it is the job paused last (or resume #N says go on now), then
    goes on as resume #N, in this process. Stopped meanwhile (stop #N), it does not."""
    while True:
        job = Jobs.get(number)
        if job["state"] != "paused":
            return
        if job.get("go_now") or (idle() and next_to_go_on() == number):
            break
        time.sleep(LOOK_EVERY)
    say.info(f"Going on with #{number} {job['title']}", "▶️")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(Path(sys.argv[0]).resolve()), "resume", f"#{number}"])


@contextlib.contextmanager
def paused_for_you():
    """While the person is answered (a chat, a command line): the job that runs is paused for them, and Tinker is
    not idle, so it goes on once they are answered. The request is marked first, so the paused job does not go on
    the moment it has saved itself."""
    key = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
    Home.store().update("activity", "answering", lambda asked: dict(asked or {}, **{key: os.getpid()}))
    try:
        Jobs.pause_running("you")
        yield
    finally:
        Home.store().update("activity", "answering", lambda asked: {name: value for name, value in (asked or {}).items()
                                                                    if name != key})


def idle():
    """No job runs, and no request of the person's is being answered (one of a process that is gone is not)."""
    asked = Home.store().get("activity", "answering") or {}
    return not Jobs.listed("running") and not any(alive(pid) for pid in asked.values())


def next_to_go_on():
    """The paused job that goes on first: the newest."""
    paused = Jobs.listed("paused")
    return paused[0]["number"] if paused else None


def signal_job(job):
    try:
        os.kill(job["pid"], STOP)
    except ProcessLookupError, PermissionError:
        pass


def is_job(argv):
    """Whether a command line is a long command, run as a job: not one only shown (help, exercise status, read list,
    read show), not learn NAME "DEFINITION" (kept at once), not a build an exercise runs (the exercise is the job)."""
    if not argv or argv[0] not in COMMANDS or {"-h", "--help"} & set(argv):
        return False
    if os.environ.get(EXERCISE_ENV) == "1":
        return False
    return not (argv[0] == "learn" and len(argv) == 3) and not (argv[0] == "exercise" and argv[1:2] == ["status"]) \
        and not (argv[0] == "read" and argv[1:2] in (["list"], ["show"]))


def job_number(word):
    """The job a word names as #N, or None."""
    word = str(word or "").strip()
    return int(word[1:]) if word.startswith("#") and word[1:].isdigit() else None


def settled(job):
    """A running or paused job whose process is gone (killed, the computer restarted) is stopped: its journal is
    there."""
    if job.get("state") in ("running", "paused") and not alive(job.get("pid")):
        return dict(job, state="stopped")
    return job


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def interrupted(signum, frame):
    raise KeyboardInterrupt
