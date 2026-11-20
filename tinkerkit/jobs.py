import contextlib
import os
import signal
lazy import datetime
lazy import json
lazy import shlex
lazy import shutil
lazy import subprocess
lazy import sys
lazy import threading
lazy import time
lazy from pathlib import Path

lazy from .commands import kind_of
lazy from .memory.memories import Scratchpad
lazy from .messages import say
lazy from .settings import (
    DREAM_IDLE_SECONDS,
    EXERCISE_ENV,
    HOME_ENV,
    JOB_START_TRIES,
    JOB_STOP_SIGNAL,
    Config,
    Home,
    Numbered,
    confirm_deletion,
)
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
JOB_OUTPUT_LINES = 40
LOGIN_SHELL = os.environ.get("SHELL") or "/bin/sh"


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
        if pushed_back not in (None, number) and (JOBS.get(pushed_back) or {}).get("pid"):
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
    idle_since = None
    last_started = None

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
    def run(cls):
        while True:
            cls.go_through()
            cls.wake.wait(QUEUE_LOOK_SECONDS)
            cls.wake.clear()

    @classmethod
    def go_through(cls):
        cls.judge_last_start()
        if (number := cls.startable()) is not None:
            cls.last_started = number
            cls.detach(JOBS.get(number))
        cls.dream_when_idle(time.monotonic())

    @classmethod
    def judge_last_start(cls):
        number = cls.last_started
        if number is None or JOBS.get(number) is None or cls.running(JOBS.get(number)):
            return
        cls.last_started = None
        cls.count_failed_start(number)

    @classmethod
    def dream_when_idle(cls, now):
        if Queue.ordered():
            cls.idle_since = None
            return
        if cls.idle_since is None:
            cls.idle_since = now
        if now - cls.idle_since < DREAM_IDLE_SECONDS:
            return
        if model := cls.calibration_wanted():
            cls.queue_idle_job(["model", "calibrate", model])
        elif Scratchpad.fresh():
            cls.queue_idle_job(["dream", "run"])

    @classmethod
    def queue_idle_job(cls, argv):
        Queue.add_front(Jobs.save(dict(Jobs.new(argv), state="queued", idle=True))["number"])
        cls.idle_since = None

    @staticmethod
    def calibration_wanted():
        model = Config.get("model")
        if not model or Home.store().get("models", model):
            return None
        calibrations = [job for job in Jobs.every() if job.get("arguments") == ["model", "calibrate", model]]
        return None if calibrations else model

    @staticmethod
    def startable():
        number = Queue.head()
        while number is not None and JOBS.get(number) is None:
            Queue.remove(number)
            number = Queue.head()
        if number is None or Runner.running(JOBS.get(number)):
            return None
        return number

    @staticmethod
    def running(job):
        state = Home.job_path(job["number"]) / "state"
        run = json.loads(state.read_text()) if state.exists() else {}
        return alive(job.get("pid"), job.get("pid_began")) or alive(run.get("pid"), run.get("began"))

    @classmethod
    def count_failed_start(cls, number):
        failures = JOBS.get(number).get("start_failures", 0)
        if Queue.head() != number:
            if failures:
                Jobs.update(number, start_failures=0)
            return
        Jobs.update(number, start_failures=failures + 1)
        if failures + 1 >= JOB_START_TRIES:
            cls.give_up(number)

    @staticmethod
    def give_up(number):
        Jobs.update(number, state="stopped", start_failures=0)
        Queue.remove(number)
        with open(Home.job_folder(number) / "output.log", "a") as output:
            output.write(
                f"gave up after {JOB_START_TRIES} failed starts in a row; resume #{number} tries it again\n"
            )

    @staticmethod
    def detach(job):
        folder = Home.job_folder(job["number"])
        place = os.path.expanduser(job.get("folder") or str(Home.root))
        (folder / "ended").unlink(missing_ok=True)
        launch(folder, place, [Runner.command_line(job)], "a")
        return folder

    @staticmethod
    def command_line(job):
        environment = {
            RUNNER_JOB_ENV: str(job["number"]),
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
            HOME_ENV: str(Home.root),
        }
        settings = [f"{name}={shlex.quote(value)}" for name, value in environment.items()]
        return " ".join([*settings, shlex.join(tinker_command() + Runner.arguments(job))])

    @staticmethod
    def arguments(job):
        return ["resume", f"#{job['number']}"] if job.get("pid") else list(job["arguments"])


class Follower:
    INTERRUPTED = 130

    def __init__(self, number):
        self.number = number
        self.output = Home.job_path(number) / "output.log"
        self.end = Home.job_path(number) / "ended"
        self.seen = self.output.stat().st_size if self.output.exists() else 0

    def follow(self, hosting):
        try:
            while not self.over():
                self.show(whole_lines=True)
                hosting = hosting or Runner.host()
                time.sleep(QUEUE_LOOK_SECONDS)
        except KeyboardInterrupt:
            return self.INTERRUPTED
        self.show(whole_lines=False)
        return max(self.exit_code(), 0)

    def over(self):
        return self.number not in Queue.ordered() and (self.ended() or not self.job_runs())

    def job_runs(self):
        job = JOBS.get(self.number)
        return job is not None and Runner.running(job)

    def ended(self):
        return self.end.exists()

    def exit_code(self):
        return int(self.end.read_text()) if self.ended() else 0

    def show(self, whole_lines):
        if not self.output.exists():
            return
        with open(self.output, "rb") as output:
            output.seek(self.seen)
            grown = output.read()
        shown = grown[: grown.rfind(b"\n") + 1] if whole_lines else grown
        self.seen += len(shown)
        for line in shown.decode(errors="replace").splitlines():
            say.text(line)
        sys.stdout.flush()


def job_view(number):
    job = Jobs.get(number)
    if job is None:
        return None
    return [f"#{number} {job['state']} {job['title']}", *output_lines(job)]


def output_lines(job):
    if job["state"] == "archived":
        return ["Its output is gone."]
    printed = printed_lines(job["number"])
    if not printed:
        return ["It has printed nothing yet."]
    left_out = len(printed) - JOB_OUTPUT_LINES
    return ([f"{left_out} earlier lines left out."] if left_out > 0 else []) + printed[-JOB_OUTPUT_LINES:]


def ending(number):
    end = Home.job_path(number) / "ended"
    if not end.exists():
        return "It stopped before it ended."
    return f"It ended with exit code {end.read_text().strip()}."


def printed_lines(number):
    output = Home.job_path(number) / "output.log"
    return output.read_text(errors="replace").splitlines() if output.exists() else []


def runner_job():
    number = os.environ.get(RUNNER_JOB_ENV, "")
    return int(number) if number.isdigit() else None


def tinker_command():
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


def tinker_environment(**extra):
    inherited = {name: value for name, value in os.environ.items() if name != RUNNER_JOB_ENV}
    return dict(inherited, **{HOME_ENV: str(Home.root)}, **extra)


def tinker_process(arguments, folder=None, output=None, **env):
    return subprocess.Popen(
        tinker_command() + arguments,
        cwd=folder,
        env=tinker_environment(PYTHONUNBUFFERED="1", **env),
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
            raise SystemExit(cls.follow(argv))
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
        job = cls.save(
            dict(cls.current or cls.new(argv), state="queued", folder=str(Path.cwd()), follower=os.getpid())
        )
        follower = Follower(job["number"])
        Queue.add_front(job["number"])
        hosting = Runner.host()
        return follower.follow(hosting)

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
        return next((job for job in JOBS.newest() if pid in (job.get("pid"), job.get("follower"))), None)

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
        shutil.rmtree(Home.job_path(job["number"]), ignore_errors=True)
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


def launch(run_folder, place, lines, output_mode):
    (run_folder / "run.sh").write_text("\n".join([f"cd {shlex.quote(place)}", *lines]) + "\n")
    with open(run_folder / "output.log", output_mode) as output:
        process = subprocess.Popen(
            [LOGIN_SHELL, "-lc", launcher(run_folder)],
            cwd=place,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (run_folder / "state").write_text(json.dumps({"pid": process.pid, "began": began_at(process.pid)}))
    return process.pid


def launcher(run_folder):
    script = shlex.quote(str(run_folder / "run.sh"))
    ended = shlex.quote(str(run_folder / "ended"))
    folder = shlex.quote(str(run_folder / "folder"))
    return f"( trap 'pwd > {folder}' EXIT; . {script} ); echo $? > {ended}.new; mv {ended}.new {ended}"


def interrupted(signum, frame):
    raise KeyboardInterrupt
