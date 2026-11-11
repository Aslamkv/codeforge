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
lazy from .remembering import Scratchpad
lazy from .settings import DREAM_IDLE_SECONDS, EXERCISE_ENV, JOB_STOP_SIGNAL, Home, Numbered, confirm_deletion
lazy from .store import Log
lazy from .text import fingerprint, now_text

JOBS = Numbered("jobs", "job numbers")
STATES = ("running", "queued", "stopped", "finished", "archived")
QUEUE = ("state", "job queue")
RUNNER = ("state", "job runner")
RUNNER_JOB_ENV = "TINKER_RUNNER_JOB"
QUEUE_LOOK_SECONDS = 1
WAITING = ("running", "queued", "paused")
ANSWERS = {}


class Queue:
    @staticmethod
    def ordered():
        return list(Home.store().get(*QUEUE) or [])

    @staticmethod
    def head():
        return next(iter(Queue.ordered()), None)

    @staticmethod
    def add_front(number):
        pushed_back = Queue.head()
        Queue.place(number, lambda others: [number] + others)
        if pushed_back not in (None, number) and JOBS.get(pushed_back):
            signal_job(JOBS.get(pushed_back))
        Runner.wake.set()

    @staticmethod
    def remove(number):
        Queue.place(number, lambda others: others)

    @staticmethod
    def place(number, placed):
        Home.store().update(
            *QUEUE, lambda numbers: placed([kept for kept in numbers or [] if kept != number])
        )


class Runner:
    wake = threading.Event()
    paused = False
    idle_since = None

    @classmethod
    def host(cls):
        if not cls.claim():
            return False
        threading.Thread(target=cls.run, daemon=True).start()
        return True

    @staticmethod
    def claim():
        me = {"pid": os.getpid(), "began": began_at(os.getpid())}

        def held(holder):
            taken = holder and holder != me and alive(holder.get("pid"), holder.get("began"))
            return holder if taken else me

        return Home.store().update(*RUNNER, held) == me

    @classmethod
    def pause(cls, hosting):
        cls.paused = hosting
        head = JOBS.get(Queue.head()) if Queue.head() is not None else None
        if head and alive(head.get("pid"), head.get("pid_began")):
            signal_job(head)

    @classmethod
    def run(cls):
        while True:
            cls.go_through()
            cls.wake.wait(QUEUE_LOOK_SECONDS)
            cls.wake.clear()

    @classmethod
    def go_through(cls):
        while not cls.paused and (number := cls.startable()) is not None:
            cls.run_job(number)
        if not cls.paused:
            cls.dream_when_idle(time.monotonic())

    @classmethod
    def dream_when_idle(cls, now):
        if Queue.ordered():
            cls.idle_since = None
            return
        if cls.idle_since is None:
            cls.idle_since = now
        if now - cls.idle_since >= DREAM_IDLE_SECONDS and Scratchpad.fresh():
            Queue.add_front(Jobs.save(dict(Jobs.new(["dream", "run"]), state="queued"))["number"])
            cls.idle_since = None

    @staticmethod
    def startable():
        number = Queue.head()
        while number is not None and JOBS.get(number) is None:
            Queue.remove(number)
            number = Queue.head()
        if number is None or alive(JOBS.get(number).get("pid"), JOBS.get(number).get("pid_began")):
            return None
        return number

    @classmethod
    def run_job(cls, number):
        process = cls.start(JOBS.get(number))
        output = Log(Home.store(), f"output #{number}")
        for line in process.stdout:
            output.add({"line": line.rstrip("\n")})
        output.add({"exit": process.wait()})

    @staticmethod
    def start(job):
        return tinker_process(
            Runner.arguments(job),
            job.get("folder"),
            subprocess.PIPE,
            NO_COLOR="1",
            **{RUNNER_JOB_ENV: str(job["number"])},
        )

    @staticmethod
    def arguments(job):
        return ["resume", f"#{job['number']}"] if job.get("pid") else list(job["arguments"])


class Follower:
    def __init__(self, number):
        self.number, self.output = number, f"output #{number}"
        self.seen = len(Home.store().entries(self.output))

    def follow(self, hosting):
        while True:
            queue = Queue.ordered()
            ran_out = self.show()
            hosting = hosting or Runner.host()
            if self.number not in queue and ran_out and not (hosting and queue):
                return
            time.sleep(QUEUE_LOOK_SECONDS)

    def show(self):
        entries = [entry for _, entry in Home.store().entries(self.output)]
        for entry in entries[self.seen :]:
            if "line" in entry:
                say.text(entry["line"])
        self.seen = len(entries)
        return not entries or "exit" in entries[-1]


def runner_job():
    number = os.environ.get(RUNNER_JOB_ENV, "")
    return int(number) if number.isdigit() else None


def tinker_command():
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


def tinker_process(arguments, folder=None, output=None, **env):
    return subprocess.Popen(
        tinker_command() + arguments,
        cwd=folder,
        env=dict(os.environ, PYTHONUNBUFFERED="1", **env),
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
        start_new_session=True,
    )


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
    interrupted = False
    resumed_here = False

    @classmethod
    @contextlib.contextmanager
    def running(cls, argv):
        if not is_job(argv):
            yield
            return
        if runner_job() is None:
            cls.follow(argv)
            raise SystemExit(0)
        cls.current = JOBS.get(runner_job())
        with cls.run_here(argv):
            yield

    @classmethod
    @contextlib.contextmanager
    def run_here(cls, argv):
        cls.begin(argv)
        signal.signal(JOB_STOP_SIGNAL, interrupted)
        try:
            yield
        except KeyboardInterrupt:
            cls.ended_status, cls.interrupted = cls.ended_status or "stopped", True
            raise
        finally:
            cls.close()

    @classmethod
    def follow(cls, argv):
        job = cls.save(dict(cls.current or cls.new(argv), state="queued", folder=str(Path.cwd())))
        follower = Follower(job["number"])
        Queue.add_front(job["number"])
        hosting = Runner.host()
        try:
            follower.follow(hosting)
        except KeyboardInterrupt:
            Runner.pause(hosting)
            raise

    @classmethod
    def begin(cls, argv):
        job = cls.current or cls.new(argv)
        cls.current = cls.save(
            dict(
                job,
                state="running",
                pid=os.getpid(),
                pid_began=began_at(os.getpid()),
            )
        )
        cls.journal, cls.ended_status, cls.interrupted = Journal(job["number"]), None, False

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
        number = cls.current["number"]
        if cls.interrupted and number in Queue.ordered():
            cls.update(number, state="queued")
        elif cls.interrupted or cls.ended_status == "stopped":
            cls.update(number, state="stopped")
            Queue.remove(number)
        else:
            cls.update(number, state="finished")
            Queue.remove(number)
            cls.journal.drop()
        cls.current = cls.journal = None

    @classmethod
    def ended(cls, ending):
        if cls.current is None:
            return ending
        cls.ended_status, number = ending.get("status"), cls.current["number"]
        if cls.ended_status != "stopped":
            return ending
        queue = Queue.ordered()
        if number in queue[1:]:
            headline = f"Paused for job #{queue[0]}: it goes on when its turn comes"
            return dict(ending, status="paused", headline=headline)
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
        return settled(job, Queue.ordered()) if job else None

    @staticmethod
    def of_process(pid):
        return next((job for job in JOBS.newest() if job.get("pid") == pid), None)

    @staticmethod
    def every():
        queue = Queue.ordered()
        return [settled(job, queue) for job in JOBS.newest()]

    @staticmethod
    def lost():
        queue = Queue.ordered()
        return [job for job in JOBS.newest() if is_lost(job, queue)]

    @staticmethod
    def listed(state=None):
        queue = Queue.ordered()
        jobs = sorted(
            Jobs.every(), key=lambda job: queue.index(job["number"]) if job["number"] in queue else len(queue)
        )
        states = [state] if state else ["running", "queued", "stopped"]
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
        if job["state"] in ("finished", "archived"):
            return None, f"Job #{number} is {job['state']}: there is nothing left to resume."
        return job, ""

    @classmethod
    def arguments(cls, argv):
        if runner_job() is not None:
            cls.current, cls.resumed_here = JOBS.get(runner_job()), argv[:1] == ["resume"]
            return cls.current["arguments"]
        if argv[:1] != ["resume"] or len(argv) > 2:
            return argv
        number = job_number(argv[1]) if len(argv) == 2 else None
        resumes_a_project = len(argv) == 2 and number is None
        if resumes_a_project:
            return argv
        job, why = cls.resumable(number)
        resumes_this_folders_run = job is None and number is None
        if resumes_this_folders_run:
            return argv
        if job is None:
            raise SystemExit(why)
        cls.current, cls.resumed_here = job, True
        return job["arguments"]

    @staticmethod
    def stop(job):
        Queue.remove(job["number"])
        if alive(job.get("pid"), job.get("pid_began")):
            signal_job(job)


def queue_lines():
    queued = [Jobs.get(number) for number in Queue.ordered()]
    lines = [f"#{job['number']} {job['state']} {job['title']}" for job in queued if job]
    return lines or ["The queue is empty."]


def emptied_queue():
    head = Queue.head()
    for number in Queue.ordered()[1:]:
        job = Jobs.get(number)
        if job:
            Jobs.stop(job)
        else:
            Queue.remove(number)
    return head


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
    if job["state"] in ("running", "queued"):
        raise SystemExit(f"Job #{number} is {job['state']}: stop it first (tinker stop #{number}).")
    if job["state"] == "archived":
        raise SystemExit(f"Job #{number} is archived already (tinker jobs archived lists them).")
    return job


@contextlib.contextmanager
def answering():
    job = Jobs.save(
        dict(Jobs.new(["chat"]), state="running", pid=os.getpid(), pid_began=began_at(os.getpid()))
    )
    ANSWERS[threading.get_ident()] = (job["number"], threading.Event())
    try:
        Queue.add_front(job["number"])
        yield
    finally:
        ANSWERS.pop(threading.get_ident(), None)
        Queue.remove(job["number"])
        Jobs.update(job["number"], state="finished")
        Runner.wake.set()


def answer_stopped():
    return ANSWERS.get(threading.get_ident(), (None, threading.Event()))[1]


def stop_answers(signum=None, frame=None):
    queued = Queue.ordered()
    for number, stopped in list(ANSWERS.values()):
        if number not in queued:
            stopped.set()


def signal_job(job):
    if job["pid"] == os.getpid():
        return stop_answers()
    try:
        os.kill(job["pid"], JOB_STOP_SIGNAL)
    except ProcessLookupError, PermissionError:
        pass


def is_job(argv):
    if {"-h", "--help"} & set(argv) or os.environ.get(EXERCISE_ENV):
        return False
    return kind_of(argv) in ("job", "pauses")


def queue_action(words):
    if words == ["list"]:
        return "list"
    if words == ["stop", "all"]:
        return "all"
    return job_number(words[1]) if len(words) == 2 and words[0] == "stop" else None


def job_number(word):
    word = str(word or "").strip()
    return int(word[1:]) if word.startswith("#") and word[1:].isdigit() else None


def settled(job, queue):
    if job["number"] in queue:
        return dict(job, state="running" if job["number"] == queue[0] else "queued")
    if job.get("state") in WAITING:
        return dict(job, state="stopped")
    return job


def is_lost(job, queue):
    return job["number"] in queue and not alive(job.get("pid"), job.get("pid_began"))


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
