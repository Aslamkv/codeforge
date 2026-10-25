import os
import re
import signal
import subprocess
import time
from pathlib import Path

from .messages import say
lazy from .conversation import (
    BUILD_STOP_GRACE_SECONDS,
    CHANNEL_REPORTS_ENV,
    JOB_STOP_SIGNAL,
    tinker_process,
)
lazy from .jobs import Jobs
lazy from .knowledge import Knowledge, Library, language_key
lazy from .project import Project, Steps, saved
lazy from .prompts import Part, Prompts
lazy from .settings import (
    EARLIER_MEASURES_SHOWN,
    EXERCISE_BUILD_LOOK_SECONDS,
    EXERCISE_ENV,
    EXERCISE_FOLDER,
    REFLECTION_PROBLEMS,
    Home,
    first_line,
    now_text,
    projects_folder,
    similarity,
)

VERDICTS = ("language", "ambiguous", "other")
PRACTICE_STEPS = ("build", "feature", "measure", "reflect", "done")
SAME_PRACTICE_TASK_SIMILARITY = 0.8
FOLDER_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+){0,3}")
RETRIED_RE = re.compile(r"\(attempt [2-9]")


class Exercise:
    def __init__(self, model, tracker, start_build=None):
        self.model, self.tracker = model, tracker
        self.start_build = start_build or start_tinker
        self.running_build = None
        self.own_step = ""

    def run(self, asked):
        signal.signal(JOB_STOP_SIGNAL, interrupted)
        self.tracker.change("exercise", exercise=True)
        self.on_to("identify")
        exercise = Exercises.of(identified(self.model, asked))
        Exercises.save(exercise)
        try:
            self.knows(exercise["language"])
            while True:
                self.practise(exercise)
        except KeyboardInterrupt:
            self.stop_build()
            return stopped(exercise)
        finally:
            Exercises.leave(exercise)

    def on_to(self, step):
        self.tracker.phase(step)
        self.own_step = self.tracker.state["doing"]

    def knows(self, name):
        if not Library.language(name):
            self.on_to("learn")
            say.info(f"Tinker has not learned {name} yet: learning it first.", "📚")
            self.built(lambda: ["learn", name])
            if not Library.language(name):
                raise SystemExit(f"Could not learn {name} (tinker learn {name} says why); nothing practised.")
        if not installed(Library.read(Library.language(name))):
            raise SystemExit(
                f"{name} is not installed here: install it, then tinker exercise {name}. Nothing practised."
            )

    def practise(self, exercise):
        project = Exercises.current(exercise) or self.picked(exercise)
        self.tracker.change(
            "project",
            title=f"Exercising {exercise['language']}: {Path(project['folder']).name}",
            steps=[],
            step=None,
            passing=None,
        )
        Steps(PRACTICE_STEPS, self.tracker, only_if={"feature": lambda: built(project)}).run(
            project["step"],
            lambda step: self.practise_step(step, exercise, project),
            lambda step: self.reached(step, exercise, project),
        )
        say.done(told(exercise, project))

    def practise_step(self, step, exercise, project):
        self.own_step = self.tracker.state["doing"]
        getattr(self, step)(exercise, project)

    @staticmethod
    def reached(step, exercise, project):
        project["step"] = step
        Exercises.save(exercise)

    def picked(self, exercise):
        self.on_to("pick")
        name, earlier = exercise["language"], [practice["task"] for practice in exercise["projects"]]
        parts = [
            Part("Language", name, True),
            Part("Focus", exercise.get("focus") or "(none yet)", True),
            Part("Earlier projects", Part.bullets(earlier[-30:]) or "(none yet)", True),
        ]
        focus = f", practising {exercise['focus']}" if exercise.get("focus") else ""
        with say.working_on(f"Choosing practice project {len(earlier) + 1} in {name}{focus}"):
            choice = self.model.ask_until_usable(
                Prompts.EXERCISE_PROJECT_SYSTEM,
                parts,
                "exercise: next project",
                lambda reply: read_choice(reply.json(), name, earlier),
            )
        if choice is None:
            raise SystemExit(
                f"{self.model.name} could not choose a practice project; the exercise stopped, "
                f"everything kept."
            )
        project = dict(
            choice, folder=str(free_folder(choice["folder"])), step=PRACTICE_STEPS[0], started=now_text()
        )
        exercise["projects"].append(project)
        Exercises.save(exercise)
        say.found(f"Next practice: {project['task']}")
        self.tracker.outcome(first_line(project["task"], 80))
        return project

    def build(self, exercise, project):
        folder = Path(project["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        task = ["task", project["task"], "--workdir", str(folder)]
        self.built(lambda: going_on(folder) if run_of(folder) else task)

    def feature(self, exercise, project):
        folder = Path(project["folder"])
        before = project.setdefault("cycles", len(Project.load(folder).cycles))
        Exercises.save(exercise)
        add = ["add", project["feature"], "--workdir", str(folder)]
        self.built(lambda: going_on(folder) if len(Project.load(folder).cycles) > before else add)

    def measure(self, exercise, project):
        folder = Path(project["folder"])
        project["measures"], project["problems"] = measured(folder), problems(folder)
        project["ended"] = now_text()

    def reflect(self, exercise, project):
        folder = Path(project["folder"])
        knowledge = Knowledge(exercise["language"], (run_of(folder) or {}).get("id"))
        with say.working_on(f"Learning from what went wrong and what was slow in {folder.name}"):
            reflection = self.model.ask_until_usable(
                Prompts.EXERCISE_REFLECT_SYSTEM,
                reflection_parts(exercise, project, knowledge.known_lessons()),
                "exercise: lessons",
                lambda reply: read_reflection(reply.json()),
                attempts=2,
            )
        if reflection is None:
            say.warn("No lessons from this project: nothing usable came of looking back at it.")
            return
        names = [folder.name] + [Path(path).name for path in Project.load(folder).files]
        project["lessons"] = knowledge.add_lessons(reflection["lessons"], reflection["confirmed"], names)
        exercise["focus"] = reflection["focus"] or exercise.get("focus", "")
        say.done(
            f"{project['lessons']} new lesson(s), {len(reflection['confirmed'])} confirmed; next focus: "
            f"{exercise['focus'] or '(none)'}"
        )
        lessons = [str(item.get("text", "")).strip() for item in reflection["lessons"]]
        if any(lessons):
            say.found(first_line(next(text for text in lessons if text), 200))

    def built(self, arguments):
        self.running_build = self.start_build(arguments())
        while self.running_build.poll() is None:
            self.show_build(Home.store().get("running", str(self.running_build.pid)))
            time.sleep(EXERCISE_BUILD_LOOK_SECONDS)
        return self.running_build.returncode

    def show_build(self, build):
        if not build:
            return
        self.tracker.change(
            "build",
            quiet=True,
            doing=f"{self.own_step} · {build.get('doing', '')}",
            **{key: build.get(key, "") for key in ("working", "tally", "found", "latest")},
            passing=build.get("passing"),
        )

    def stop_build(self):
        process = self.running_build
        if process is None or process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(BUILD_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def identified(model, asked):
    key = Library.language(asked)
    if key:
        return Library.read(key)["name"]
    verdict = model.ask_until_usable(
        Prompts.EXERCISE_LANGUAGE_SYSTEM,
        [Part("The words", asked, True)],
        "exercise: which language",
        lambda reply: read_verdict(reply.json()),
        attempts=2,
    )
    if verdict is None:
        raise SystemExit(f"Could not tell whether {asked!r} is a programming language; nothing done.")
    if verdict["verdict"] == "ambiguous":
        choices = verdict["choices"] or ["one language"]
        raise SystemExit(
            f"{asked!r} could be {', '.join(choices)}: say which, e.g. tinker exercise {choices[0]}. "
            f"Nothing done."
        )
    if verdict["verdict"] == "other":
        raise SystemExit(
            f"{asked!r} is not a programming language ({verdict['why'] or 'no language by that name'}). "
            f"Nothing done."
        )
    return verdict["name"]


def read_verdict(data):
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f'"verdict" must be one of {", ".join(VERDICTS)}')
    name = str(data.get("name") or "").strip()[:40]
    if verdict == "language" and not name:
        raise ValueError('a language needs its "name"')
    choices = [str(choice).strip()[:40] for choice in data.get("choices") or [] if str(choice).strip()][:6]
    return {
        "verdict": verdict,
        "name": name,
        "choices": choices,
        "why": first_line(str(data.get("why") or ""), 160),
    }


def installed(entry):
    machine = (entry or {}).get("machine") or []
    return not machine or any(found.get("found") for found in machine)


def read_choice(data, language, earlier):
    task, feature = str(data.get("task") or "").strip(), str(data.get("feature") or "").strip()
    if not task or not feature:
        raise ValueError('a practice project needs a "task" and a "feature"')
    repeated = next(
        (before for before in earlier if similarity(before, task) >= SAME_PRACTICE_TASK_SIMILARITY), None
    )
    if repeated:
        raise ValueError(f"it repeats an earlier project ({first_line(repeated, 80)}): choose another")
    if language.lower() not in task.lower():
        task = f"{task.rstrip('.')} in {language}"
    folder = str(data.get("folder") or "").strip().lower()
    if not FOLDER_RE.fullmatch(folder) or len(folder) > 30:
        folder = "-".join(re.findall(r"[a-z][a-z0-9]*", task.lower())[:3]) or "practice"
    practises = [
        first_line(str(practice), 60) for practice in data.get("practises") or [] if str(practice).strip()
    ][:5]
    return {"task": task[:400], "feature": feature[:300], "folder": folder, "practises": practises}


def free_folder(name):
    root = exercise_folder()
    folder, count = root / name, 1
    while folder.exists():
        count += 1
        folder = root / f"{name}-{count}"
    return folder


def exercise_folder():
    return projects_folder() / EXERCISE_FOLDER


def run_of(folder):
    return saved(folder, "run", "record")


def built(project):
    if (run_of(Path(project["folder"])) or {}).get("status") == "done":
        return True
    say.skip("The project did not build, so no feature is added to it")
    return False


def going_on(folder):
    return ["resume", "--workdir", str(folder)]


def start_tinker(arguments):
    return tinker_process(arguments, **{EXERCISE_ENV: "1", CHANNEL_REPORTS_ENV: "off"})


def interrupted(signum, frame):
    raise KeyboardInterrupt


def stopped(exercise):
    done = len(Exercises.finished(exercise))
    say.title(f"Exercise of {exercise['language']} stopped: everything is kept.", "🛑")
    return {
        "status": "stopped",
        "headline": f"Exercise of {exercise['language']} stopped, everything kept",
        "details": [("Practice projects done", str(done))],
    }


def measured(folder):
    project = Project.load(folder)
    events = [entry for _, entry in project.store.entries("events")]
    calls = [entry for entry in events if "reply_tokens" in entry]
    passing, parked, points = project.counts()
    return {
        "status": (run_of(folder) or {}).get("status", "unknown"),
        "minutes": round(
            sum(event.get("wall_seconds", 0) for event in events if event.get("label") == "session end") / 60,
            1,
        ),
        "calls": len(calls),
        "retried": sum(1 for event in calls if RETRIED_RE.search(event.get("label", ""))),
        "tokens": sum(event.get("reply_tokens") or 0 for event in calls),
        "passing": passing,
        "parked": parked,
        "points": points,
        "readme_failures": len(readme_failures(project)),
        "slowest": slowest(calls),
    }


def readme_failures(project):
    return [failure for cycle in project.cycles for failure in cycle.get("readme_failures", [])]


def slowest(calls, count=3):
    steps = {}
    for call in calls:
        step = (call.get("label") or "call").split()[0]
        seconds, number = steps.get(step, (0.0, 0))
        steps[step] = (seconds + (call.get("seconds") or 0), number + 1)
    ranked = sorted(steps.items(), key=lambda item: -item[1][0])[:count]
    return [
        f"{step}: {round(seconds)}s in {number} call{'s' * (number != 1)}"
        for step, (seconds, number) in ranked
    ]


def problems(folder):
    project = Project.load(folder)
    events = [entry for _, entry in project.store.entries("events")]
    found = [f"nothing made this pass: {step['behaviour']}" for step in project.parked()]
    found += [f"README example failed: {failure}" for failure in readme_failures(project)]
    found += [
        f"model call failed: {event.get('label', '')}: {event['error']}"
        for event in events
        if event.get("error")
    ]
    found += [
        f"reply not usable: {event['note']}" for event in events if "not usable" in str(event.get("note", ""))
    ]
    found += [f"reply cut short: {event.get('label', '')}" for event in events if event.get("cut")]
    found += [
        f"warning: {event['line'].strip()}"
        for _, event in project.store.entries("output")
        if event.get("kind") == "warn"
    ]
    return list(dict.fromkeys(first_line(problem, 240) for problem in found))[:REFLECTION_PROBLEMS]


def reflection_parts(exercise, project, known):
    earlier = Exercises.finished(exercise)[-EARLIER_MEASURES_SHOWN:]
    measures = project["measures"]
    return [
        Part("Language", exercise["language"], True),
        Part(
            "This project's measures",
            f"{measures_line(measures)}\nslowest steps: {'; '.join(measures['slowest']) or '(none)'}",
            True,
        ),
        Part(
            "Earlier projects' measures",
            Part.bullets(measures_line(practice["measures"]) for practice in earlier) or "(none yet)",
        ),
        Part(
            "Problems",
            Part.bullets(project["problems"]) or "(none)",
            True,
            keep="tail",
        ),
        Part(
            "Known lessons",
            "\n".join(f"{number}. {lesson['text']}" for number, lesson in enumerate(known, 1)),
        ),
    ]


def read_reflection(data):
    lessons = [item for item in data.get("lessons") or [] if isinstance(item, dict)][:3]
    confirmed = [number for number in data.get("confirmed") or [] if isinstance(number, int)]
    return {
        "lessons": lessons,
        "confirmed": confirmed,
        "focus": first_line(str(data.get("focus") or ""), 200),
    }


def measures_line(measures):
    return (
        f"{measures['status']}, {measures['minutes']} min, {measures['calls']} model calls "
        f"({measures['retried']} retried), {measures['passing']}/{measures['points']} spec points pass, "
        f"{measures['parked']} parked, {measures['readme_failures']} README examples failed"
    )


def told(exercise, project):
    line = f"{Path(project['folder']).name}: {measures_line(project['measures'])}"
    earlier = Exercises.finished(exercise)[:-1]
    if earlier:
        before = earlier[-1]["measures"]
        line += f" (before: {before['minutes']} min, {before['calls']} model calls)"
    return line


class Exercises:
    @staticmethod
    def of(name):
        return Home.store().get("exercise", language_key(name)) or {
            "language": name,
            "started": now_text(),
            "projects": [],
            "focus": "",
        }

    @staticmethod
    def save(exercise):
        Home.store().put("exercise", language_key(exercise["language"]), exercise)

    @staticmethod
    def every():
        return list(Home.store().items("exercise").values())

    @staticmethod
    def current(exercise):
        return next((project for project in exercise["projects"] if project["step"] != "done"), None)

    @staticmethod
    def finished(exercise):
        return [
            project
            for project in exercise["projects"]
            if project["step"] == "done" and project.get("measures")
        ]

    @staticmethod
    def leave(exercise):
        exercise["left"] = now_text()
        Exercises.save(exercise)


def job_state(exercise):
    jobs = [
        job
        for state in ("running", "paused")
        for job in Jobs.listed(state)
        if job["command"] == "exercise"
        and language_key(" ".join(job["arguments"][1:])) == language_key(exercise["language"])
    ]
    return jobs[0]["state"] if jobs else "stopped"


def show():
    exercises = Exercises.every()
    if not exercises:
        say.info("No exercise yet: start one with tinker exercise LANGUAGE (e.g. tinker exercise python).")
        return
    for exercise in exercises:
        state = job_state(exercise)
        with say.part(
            f"{exercise['language']}: {state}, {len(Exercises.finished(exercise))} practice project(s) "
            f"done since {exercise['started']}",
            "🏋️",
        ):
            if exercise.get("focus"):
                say.item(f"Next focus: {exercise['focus']}")
            for project in Exercises.finished(exercise)[-EARLIER_MEASURES_SHOWN:]:
                say.item(f"{Path(project['folder']).name}: {measures_line(project['measures'])}")
            current = Exercises.current(exercise)
            if current:
                say.item(f"Now: {current['task']} ({current['step']})")
    say.tip(
        f"Practice projects are in {exercise_folder()}. Stop with: tinker stop #N; go on with: tinker resume #N "
        f"(tinker jobs lists them)"
    )
