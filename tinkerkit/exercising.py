"""Tinker practising a language on its own (tinker exercise LANGUAGE), to write better code faster. It makes sure the
words name one language: words that are not one, or could be several, are said so, and nothing is done. It learns
the language first when Tinker has not, and needs it installed here. Then it builds practice projects one after
another, each in its own folder in the exercise folder, each chosen for what the earlier ones did not practise:
the project, then one feature added to it. Each is measured (its time, model calls, retried replies, criteria
nothing made pass, README examples that failed, its slowest steps), and one call turns its problems into lessons
for the language, kept as candidates: the next projects prove or retire them, as every build does.

Each build is a tinker of its own, which saves itself as it goes. The exercise is a job, so the person comes first
as with every job: a chat, a command line or a job of theirs pauses it (its build stops, saving itself), and it
goes on once Tinker is idle. Its tracker shows what the build does now, as the build's own tracker says it. tinker
stop #N stops it with everything kept; tinker resume #N (or tinker exercise LANGUAGE) goes on from where it was."""

import os
import re
import signal
import subprocess
import time
from pathlib import Path

from .messages import say
lazy from .conversation import OFF, STOP, STOP_WAIT, alive, tinker_process
lazy from .jobs import Jobs
lazy from .knowledge import Knowledge, Library, language_key
lazy from .project import Project, saved
lazy from .prompts import Part, Prompts
lazy from .settings import (
    EXERCISE_ENV, EXERCISE_FOLDER, EXERCISE_LOOK, EXERCISE_PROBLEMS, EXERCISE_SHOWN, Home, first_line,
    now_text, projects_folder, similarity,
)

VERDICTS = ("language", "ambiguous", "other")
STEPS = ("build", "feature", "measure", "reflect", "done")  # a practice project's, in order
SAME_PROJECT = 0.8  # a task this alike to an earlier one repeats it
FOLDER_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+){0,3}")
RETRIED_RE = re.compile(r"\(attempt [2-9]")


class Exercise:
    """One language's exercise, run until it is stopped (the module says how)."""

    def __init__(self, model, tracker, start=None):
        self.model, self.tracker = model, tracker
        self.start = start or start_tinker  # how a build starts: start(arguments) -> its process
        self.process = None  # the build that runs now
        self.doing = ""  # what the exercise's own step does (a build's progress is shown after it)

    def run(self, asked):
        """The exercise of the language the words name, until it is stopped; its ending."""
        signal.signal(STOP, interrupted)
        self.tracker.change("exercise", exercise=True)  # its own runs, not the person's: shown as the exercise
        self.on_to("identify")
        exercise = Exercises.of(identified(self.model, asked))
        Exercises.claim(exercise)
        try:
            self.knows(exercise["language"])
            while True:
                self.practise(exercise)
        except KeyboardInterrupt:
            self.stop_build()
            return stopped(exercise)
        finally:
            Exercises.release(exercise)

    def on_to(self, step):
        self.tracker.phase(step)
        self.doing = self.tracker.state["doing"]

    def knows(self, name):
        """The language learned first when Tinker has not (tinker learn, as a build of its own), and installed here."""
        if not Library.language(name):
            self.on_to("learn")
            say.info(f"Tinker has not learned {name} yet: learning it first.", "📚")
            self.built(lambda: ["learn", name])
            if not Library.language(name):
                raise SystemExit(f"Could not learn {name} (tinker learn {name} says why); nothing practised.")
        if not installed(Library.read(Library.language(name))):
            raise SystemExit(f"{name} is not installed here: install it, then tinker exercise {name}. Nothing practised.")

    def practise(self, exercise):
        """One practice project, from the step it reached to its end, each step saved as it ends."""
        project = Exercises.current(exercise) or self.picked(exercise)
        self.tracker.change("project", title=f"Exercising {exercise['language']}: {Path(project['folder']).name}",
                            steps=[], step=None, passing=None)
        while project["step"] != "done":
            getattr(self, project["step"])(exercise, project)
            project["step"] = STEPS[STEPS.index(project["step"]) + 1]
            Exercises.save(exercise)
        say.done(told(exercise, project))

    def picked(self, exercise):
        """The next practice project, chosen by one call: new, a little harder, practising the focus first."""
        self.on_to("pick")
        name, earlier = exercise["language"], [p["task"] for p in exercise["projects"]]
        parts = [Part("Language", name, True), Part("Focus", exercise.get("focus") or "(none yet)", True),
                 Part("Earlier projects", "\n".join(f"- {task}" for task in earlier[-30:]) or "(none yet)", True)]
        focus = f", practising {exercise['focus']}" if exercise.get("focus") else ""
        with say.working_on(f"Choosing practice project {len(earlier) + 1} in {name}{focus}"):
            choice = self.model.ask_until_usable(Prompts.EXERCISE_PROJECT_SYSTEM, parts, "exercise: next project",
                                lambda reply: read_choice(reply.json(), name, earlier))
        if choice is None:
            raise SystemExit(f"{self.model.name} could not choose a practice project; the exercise stopped, "
                             f"everything kept.")
        project = dict(choice, folder=str(free_folder(choice["folder"])), step=STEPS[0], started=now_text())
        exercise["projects"].append(project)
        Exercises.save(exercise)
        say.found(f"Next practice: {project['task']}")
        self.tracker.outcome(first_line(project["task"], 80))
        return project

    def build(self, exercise, project):
        """The project built from its task, in its own folder (once its run is kept, a build goes on with it)."""
        self.on_to("build")
        folder = Path(project["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        task = ["task", project["task"], "--workdir", str(folder)]
        self.built(lambda: going_on(folder) if run_of(folder) else task)

    def feature(self, exercise, project):
        """Its feature added, once the project is built (one that did not build gets none)."""
        folder = Path(project["folder"])
        if (run_of(folder) or {}).get("status") != "done":
            say.skip("The project did not build, so no feature is added to it")
            return
        self.on_to("feature")
        before = project.setdefault("cycles", len(Project.load(folder).cycles))
        Exercises.save(exercise)
        add = ["add", project["feature"], "--workdir", str(folder)]
        self.built(lambda: going_on(folder) if len(Project.load(folder).cycles) > before else add)

    def measure(self, exercise, project):
        self.on_to("measure")
        folder = Path(project["folder"])
        project["measures"], project["problems"] = measured(folder), problems(folder)
        project["ended"] = now_text()

    def reflect(self, exercise, project):
        """One call turns what went wrong and what was slow into lessons for the language (candidates, proven or
        retired by the next builds), and says what the next project should practise."""
        self.on_to("reflect")
        folder = Path(project["folder"])
        knowledge = Knowledge(exercise["language"], (run_of(folder) or {}).get("id"))
        with say.working_on(f"Learning from what went wrong and what was slow in {folder.name}"):
            reflection = self.model.ask_until_usable(Prompts.EXERCISE_REFLECT_SYSTEM,
                                    reflection_parts(exercise, project, knowledge.known_lessons()),
                                    "exercise: lessons", lambda reply: read_reflection(reply.json()), attempts=2)
        if reflection is None:
            say.warn("No lessons from this project: nothing usable came of looking back at it.")
            return
        names = [folder.name] + [Path(path).name for path in Project.load(folder).files]
        project["lessons"] = knowledge.add_lessons(reflection["lessons"], reflection["confirmed"], names)
        exercise["focus"] = reflection["focus"] or exercise.get("focus", "")
        say.done(f"{project['lessons']} new lesson(s), {len(reflection['confirmed'])} confirmed; next focus: "
                 f"{exercise['focus'] or '(none)'}")
        lessons = [str(item.get("text", "")).strip() for item in reflection["lessons"]]
        if any(lessons):
            say.found(first_line(next(text for text in lessons if text), 200))

    def built(self, arguments):
        """A tinker of its own (arguments() says how to start it, from where it is), run to its end: its exit code.
        Meanwhile the exercise's tracker shows what the build does."""
        self.process = self.start(arguments())
        while self.process.poll() is None:
            self.show_build(Home.store().get("running", str(self.process.pid)))
            time.sleep(EXERCISE_LOOK)
        return self.process.returncode

    def show_build(self, build):
        """The build's tracker (None before it keeps one) shown in the exercise's: its step after the exercise's,
        what it works on now, its spec points passing, what it found out and its latest line."""
        if not build:
            return
        self.tracker.change("build", quiet=True, doing=f"{self.doing} · {build.get('doing', '')}",
                            **{key: build.get(key, "") for key in ("working", "tally", "found", "latest")},
                            passing=build.get("passing"))

    def stop_build(self):
        """The build that runs stopped as Ctrl-C stops it (it saves itself); killed when it does not stop in time."""
        process = self.process
        if process is None or process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(STOP_WAIT)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

def identified(model, asked):
    """The language the words name: as Tinker learned it (no call), else as one call says. Words that are not one
    language end the exercise with why (SystemExit), and nothing is done."""
    key = Library.language(asked)
    if key:
        return Library.read(key)["name"]
    verdict = model.ask_until_usable(Prompts.EXERCISE_LANGUAGE_SYSTEM, [Part("The words", asked, True)],
                                     "exercise: which language", lambda reply: read_verdict(reply.json()), attempts=2)
    if verdict is None:
        raise SystemExit(f"Could not tell whether {asked!r} is a programming language; nothing done.")
    if verdict["verdict"] == "ambiguous":
        choices = verdict["choices"] or ["one language"]
        raise SystemExit(f"{asked!r} could be {', '.join(choices)}: say which, e.g. tinker exercise {choices[0]}. "
                         f"Nothing done.")
    if verdict["verdict"] == "other":
        raise SystemExit(f"{asked!r} is not a programming language ({verdict['why'] or 'no language by that name'}). "
                         f"Nothing done.")
    return verdict["name"]


def read_verdict(data):
    """{verdict, name, choices, why} of what the words name (a language needs its name)."""
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f'"verdict" must be one of {", ".join(VERDICTS)}')
    name = str(data.get("name") or "").strip()[:40]
    if verdict == "language" and not name:
        raise ValueError('a language needs its "name"')
    choices = [str(c).strip()[:40] for c in data.get("choices") or [] if str(c).strip()][:6]
    return {"verdict": verdict, "name": name, "choices": choices, "why": first_line(str(data.get("why") or ""), 160)}


def installed(entry):
    """True when what learning found on this machine has the language (nothing checked counts as there)."""
    machine = (entry or {}).get("machine") or []
    return not machine or any(found.get("found") for found in machine)


def read_choice(data, language, earlier):
    """{task, feature, folder, practises} of the next practice project: a task that names the language, never one
    that repeats an earlier project, and a folder name that fits (else one from the task's words)."""
    task, feature = str(data.get("task") or "").strip(), str(data.get("feature") or "").strip()
    if not task or not feature:
        raise ValueError('a practice project needs a "task" and a "feature"')
    repeated = next((before for before in earlier if similarity(before, task) >= SAME_PROJECT), None)
    if repeated:
        raise ValueError(f"it repeats an earlier project ({first_line(repeated, 80)}): choose another")
    if language.lower() not in task.lower():
        task = f"{task.rstrip('.')} in {language}"
    folder = str(data.get("folder") or "").strip().lower()
    if not FOLDER_RE.fullmatch(folder) or len(folder) > 30:
        folder = "-".join(re.findall(r"[a-z][a-z0-9]*", task.lower())[:3]) or "practice"
    practises = [first_line(str(p), 60) for p in data.get("practises") or [] if str(p).strip()][:5]
    return {"task": task[:400], "feature": feature[:300], "folder": folder, "practises": practises}


def free_folder(name):
    """A folder of the exercise folder no project has yet: name, else name-2, name-3 ..."""
    root = exercise_folder()
    folder, count = root / name, 1
    while folder.exists():
        count += 1
        folder = root / f"{name}-{count}"
    return folder


def exercise_folder():
    return projects_folder() / EXERCISE_FOLDER


def run_of(folder):
    """The run kept in a practice project's folder (None before its build kept one)."""
    return saved(folder, "run", "record")


def going_on(folder):
    return ["resume", "--workdir", str(folder)]


def start_tinker(arguments):
    """A tinker of its own, as an exercise's (its tracker says so, and it reports to no chat), printing here."""
    return tinker_process(arguments, **{EXERCISE_ENV: "1", OFF: "off"})


def interrupted(signum, frame):
    raise KeyboardInterrupt


def stopped(exercise):
    """The ending of an exercise stopped by the person: everything kept, and how to go on."""
    done = len(Exercises.finished(exercise))
    say.title(f"Exercise of {exercise['language']} stopped: everything is kept.", "🛑")
    return {"status": "stopped", "headline": f"Exercise of {exercise['language']} stopped, everything kept",
            "details": [("Practice projects done", str(done))]}


# === what a practice project measured, and what it teaches ===========================================================

def measured(folder):
    """What the project's runs measured, from its own database: its status, minutes, model calls (and the retried
    ones), reply tokens, spec points passing and parked, README examples that failed, and its slowest steps."""
    project = Project.load(folder)
    events = [entry for _, entry in project.store.entries("events")]
    calls = [entry for entry in events if "reply_tokens" in entry]
    passing, parked, points = project.counts()
    return {"status": (run_of(folder) or {}).get("status", "unknown"),
            "minutes": round(sum(e.get("wall_seconds", 0) for e in events if e.get("label") == "session end") / 60, 1),
            "calls": len(calls), "retried": sum(1 for e in calls if RETRIED_RE.search(e.get("label", ""))),
            "tokens": sum(e.get("reply_tokens") or 0 for e in calls), "passing": passing, "parked": parked,
            "points": points, "readme_failures": len(readme_failures(project)), "slowest": slowest(calls)}


def readme_failures(project):
    return [failure for cycle in project.cycles for failure in cycle.get("readme_failures", [])]


def slowest(calls, count=3):
    """The steps that took the most model time, as "Write: 177s in 2 calls" (a step is its call's first word, so no
    file is named)."""
    steps = {}
    for call in calls:
        step = (call.get("label") or "call").split()[0]
        seconds, number = steps.get(step, (0.0, 0))
        steps[step] = (seconds + (call.get("seconds") or 0), number + 1)
    ranked = sorted(steps.items(), key=lambda item: -item[1][0])[:count]
    return [f"{step}: {round(seconds)}s in {number} call{'s' * (number != 1)}" for step, (seconds, number) in ranked]


def problems(folder):
    """What went wrong in the project's runs, at most EXERCISE_PROBLEMS: criteria nothing made pass, README examples
    that failed, failed and unusable or cut model replies, and the warnings it said (tools that failed among them)."""
    project = Project.load(folder)
    events = [entry for _, entry in project.store.entries("events")]
    found = [f"nothing made this pass: {step['behaviour']}" for step in project.parked()]
    found += [f"README example failed: {failure}" for failure in readme_failures(project)]
    found += [f"model call failed: {e.get('label', '')}: {e['error']}" for e in events if e.get("error")]
    found += [f"reply not usable: {e['note']}" for e in events if "not usable" in str(e.get("note", ""))]
    found += [f"reply cut short: {e.get('label', '')}" for e in events if e.get("cut")]
    found += [f"warning: {e['line'].strip()}" for _, e in project.store.entries("output") if e.get("kind") == "warn"]
    return list(dict.fromkeys(first_line(problem, 240) for problem in found))[:EXERCISE_PROBLEMS]


def reflection_parts(exercise, project, known):
    earlier = Exercises.finished(exercise)[-EXERCISE_SHOWN:]
    measures = project["measures"]
    return [Part("Language", exercise["language"], True),
            Part("This project's measures", f"{measures_line(measures)}\nslowest steps: "
                                            f"{'; '.join(measures['slowest']) or '(none)'}", True),
            Part("Earlier projects' measures", "\n".join(f"- {measures_line(p['measures'])}" for p in earlier)
                 or "(none yet)"),
            Part("Problems", "\n".join(f"- {problem}" for problem in project["problems"]) or "(none)", True, keep="tail"),
            Part("Known lessons", "\n".join(f"{n}. {lesson['text']}" for n, lesson in enumerate(known, 1)))]


def read_reflection(data):
    """{lessons (at most 3), confirmed (numbers), focus} of a reflection."""
    lessons = [item for item in data.get("lessons") or [] if isinstance(item, dict)][:3]
    confirmed = [n for n in data.get("confirmed") or [] if isinstance(n, int)]
    return {"lessons": lessons, "confirmed": confirmed, "focus": first_line(str(data.get("focus") or ""), 200)}


def measures_line(measures):
    """A project's measures in one line: "done, 9.5 min, 31 model calls (2 retried), 6/6 spec points pass ..."."""
    return (f"{measures['status']}, {measures['minutes']} min, {measures['calls']} model calls "
            f"({measures['retried']} retried), {measures['passing']}/{measures['points']} spec points pass, "
            f"{measures['parked']} parked, {measures['readme_failures']} README examples failed")


def told(exercise, project):
    """What one practice project came to, against the one before: "csv-stats: done, 9.5 min (12.0 before) ..."."""
    line = f"{Path(project['folder']).name}: {measures_line(project['measures'])}"
    earlier = Exercises.finished(exercise)[:-1]
    if earlier:
        before = earlier[-1]["measures"]
        line += f" (before: {before['minutes']} min, {before['calls']} model calls)"
    return line


# === where exercises are kept ========================================================================================

class Exercises:
    """Every language's exercise, kept in the home ("exercise", by the language's key): {language, started,
    projects, focus, left}, each project {task, feature, folder, practises, step, started, ended, measures, problems,
    lessons}. The exercise that runs now is kept in the home's "activity" (its process and language)."""

    @staticmethod
    def of(name):
        """The language's exercise as it was left, or a new one."""
        return Home.store().get("exercise", language_key(name)) or {"language": name, "started": now_text(),
                                                                     "projects": [], "focus": ""}

    @staticmethod
    def save(exercise):
        Home.store().put("exercise", language_key(exercise["language"]), exercise)

    @staticmethod
    def every():
        return list(Home.store().items("exercise").values())

    @staticmethod
    def current(exercise):
        return next((p for p in exercise["projects"] if p["step"] != "done"), None)

    @staticmethod
    def finished(exercise):
        return [p for p in exercise["projects"] if p["step"] == "done" and p.get("measures")]

    @staticmethod
    def active():
        """{pid, language} of the exercise that runs now (None when none does)."""
        state = Home.store().get("activity", "exercise")
        pid = (state or {}).get("pid")
        return state if pid and pid != os.getpid() and alive(pid) else None

    @staticmethod
    def claim(exercise):
        """This process runs the exercise now: one at a time."""
        running_one = Exercises.active()
        if running_one:
            raise SystemExit(already_running(running_one))
        Exercises.save(exercise)
        Home.store().put("activity", "exercise", {"pid": os.getpid(), "language": exercise["language"]})

    @staticmethod
    def release(exercise):
        exercise["left"] = now_text()
        Exercises.save(exercise)
        if (Home.store().get("activity", "exercise") or {}).get("pid") == os.getpid():
            Home.store().delete("activity", "exercise")


def already_running(active):
    """Why a second exercise does not start: one runs at a time, and how to switch."""
    return (f"An exercise of {active['language']} runs already: it did not pause in time (one runs at a time). "
            f"Ask again in a moment.")


# === tinker exercise status ====================================================================================

def job_state(exercise):
    """How an exercise that does not run was left: paused (it goes on once Tinker is idle) or stopped."""
    paused = [job for job in Jobs.listed("paused") if job["command"] == "exercise"
              and language_key(" ".join(job["arguments"][1:])) == language_key(exercise["language"])]
    return "paused" if paused else "stopped"


def show():
    """Every exercise: whether it runs, its focus, and each recent practice project's measures."""
    exercises, active = Exercises.every(), Exercises.active()
    if not exercises:
        say.info("No exercise yet: start one with tinker exercise LANGUAGE (e.g. tinker exercise python).")
        return
    for exercise in exercises:
        runs = active and language_key(active["language"]) == language_key(exercise["language"])
        state = "running" if runs else job_state(exercise)
        with say.part(f"{exercise['language']}: {state}, {len(Exercises.finished(exercise))} practice project(s) "
                      f"done since {exercise['started']}", "🏋️"):
            if exercise.get("focus"):
                say.item(f"Next focus: {exercise['focus']}")
            for project in Exercises.finished(exercise)[-EXERCISE_SHOWN:]:
                say.item(f"{Path(project['folder']).name}: {measures_line(project['measures'])}")
            current = Exercises.current(exercise)
            if current:
                say.item(f"Now: {current['task']} ({current['step']})")
    say.tip(f"Practice projects are in {exercise_folder()}. Stop with: tinker stop #N; go on with: tinker resume #N "
            f"(tinker jobs lists them)")
