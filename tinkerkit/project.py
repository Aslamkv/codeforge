import re
from collections import namedtuple
lazy import os
lazy import secrets
lazy import shutil
lazy import time
lazy from pathlib import Path

lazy from .jobs import Jobs, began_at
lazy from .messages import say
lazy from .prompts import Part, Prompts, Reply
lazy from .settings import (
    EXERCISE_ENV,
    FIX_PHASES,
    PHASES,
    PROJECT_DB,
    RUNS_KEPT,
    STATE_DIR,
    STATUS_TEXT,
    Home,
    Store,
    confirm_deletion,
    first_line,
    now_text,
    split_text,
)
lazy from .workspace import TaughtCommands, TestRef, Toolchain

SpecSource = namedtuple("SpecSource", "text name")


class RunChecks:
    @staticmethod
    def before_start(args):
        if args.restart:
            return RunChecks.restarting(args)
        if args.resume is None:
            return RunChecks.new_run(args)
        return RunChecks.existing_run(args)

    @staticmethod
    def restarting(args):
        workdir = Runs.workdir("", args.workdir)
        run = saved(workdir, "run", "record")
        if not RunChecks.has_task(run):
            raise SystemExit(f'{workdir} has no task to restart: start one with tinker task "..."')
        args.task = run["task"]
        args.spec_text = saved(workdir, "texts", "spec_text", "")
        args.spec_name = run.get("spec")
        named = Path(run["spec"]) if run.get("spec") else None
        args.spec = str(named) if named and named.is_file() else None
        return workdir

    @staticmethod
    def has_task(run):
        return isinstance(run, dict) and str(run.get("task") or "").strip()

    @staticmethod
    def new_run(args):
        workdir = Runs.workdir("", args.workdir)
        if args.spec:
            Runs.read_spec_file(args.spec)
        RunChecks.check_no_run(workdir)
        return workdir

    @staticmethod
    def existing_run(args):
        workdir = Runs.workdir(args.resume, args.workdir)
        project = Project.load(workdir, Runs.read_record(workdir))
        if args.add is not None and args.spec_file:
            Runs.read_spec_file(args.spec_file)
        RunChecks.check_phases(project)
        if args.add is not None:
            RunChecks.check_open_to_add(project)
        elif args.amend:
            RunChecks.check_awaiting(project)
        return workdir

    @staticmethod
    def check_no_run(workdir):
        existing = saved(workdir, "run", "record")
        if existing is not None:
            raise SystemExit(
                f"{workdir} already holds run {existing.get('id', '?')}. Continue it with tinker resume, add "
                f'to it with tinker add "...", or start the new project in another folder (--workdir).'
            )

    @staticmethod
    def check_phases(project):
        for cycle in project.cycles:
            phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
            if cycle.get("phase") not in phases:
                raise SystemExit(
                    f"The saved phase '{cycle.get('phase')}' of cycle {cycle.get('id')} is not one of its "
                    f"phases ({', '.join(phases)}): restart the project, or tell it as a bug"
                )

    @staticmethod
    def check_open_to_add(project):
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is not None:
            what = "project" if cycle["id"] == "base" else cycle.get("kind", "feature")
            raise SystemExit(
                f"Run {run_id} has an unfinished {what} ({first_line(cycle['request'], 80)}). "
                f"Finish it first with: tinker resume @{run_id}"
            )
        if not project.cycles:
            raise SystemExit(f"Run {run_id} has no project spec yet; continue it with tinker resume first.")

    @staticmethod
    def check_awaiting(project, what="tinker amend changes"):
        cycle = project.open_cycle()
        if cycle is None or not cycle.get("awaiting"):
            raise SystemExit(
                f"{what} a plan that waits for confirmation, and run {project.run.get('id', '?')} has "
                f"none waiting: change a built project with tinker add or tinker fix"
            )


class Runs:
    @staticmethod
    def open(args):
        if args.resume is None:
            return Runs.open_new(args)
        return Runs.open_existing(args)

    @staticmethod
    def open_new(args):
        workdir = Path(args.workdir).expanduser().resolve()
        RunChecks.check_no_run(workdir)
        spec_text, spec_name = Runs.spec_of_new_run(args)
        project_store(workdir).put("texts", "spec_text", spec_text)
        run = Runs.new_record(args.task, workdir, spec_name)
        Runs.save(run, workdir)
        return run, workdir, spec_text, "new"

    @staticmethod
    def spec_of_new_run(args):
        if getattr(args, "restart", False):
            return SpecSource(args.spec_text, args.spec_name)
        return Runs.read_spec_file(args.spec) if args.spec else SpecSource("", None)

    @staticmethod
    def open_existing(args):
        workdir = Runs.workdir(args.resume, args.workdir)
        run = Runs.read_record(workdir)
        adding = args.add is not None
        if adding:
            spec_text = Runs.read_spec_file(args.spec_file)[0] if args.spec_file else ""
        else:
            spec_text = saved(workdir, "texts", "spec_text", "")
        run["status"] = "running"
        Runs.save(run, workdir)
        return run, workdir, spec_text, ("change" if adding else "resume")

    @staticmethod
    def new_record(task, workdir, spec_name):
        return {
            "id": secrets.token_hex(3),
            "task": task,
            "workdir": str(workdir),
            "spec": spec_name,
            "created": now_text(),
            "status": "running",
        }

    @staticmethod
    def save(run, workdir):
        run["updated"] = now_text()
        project_store(workdir).put("run", "record", run)
        Home.store().put(
            "projects", run["id"], {"id": run["id"], "workdir": str(workdir), "task": run.get("task", "")}
        )

    @staticmethod
    def remember(run, language):
        line = {key: run.get(key, "") for key in ("id", "task", "status", "created", "updated")}
        Home.store().put("runs", run["id"], dict(line, language=language or ""))
        lines = Home.store().items("runs")
        for run_id in sorted(lines, key=lambda key: lines[key].get("updated", ""))[
            : max(0, len(lines) - RUNS_KEPT)
        ]:
            Home.store().delete("runs", run_id)

    @staticmethod
    def workdir(name, workdir_arg):
        if name:
            workdir = Path(Runs.find(name)["workdir"])
            if not workdir.is_dir():
                raise SystemExit(f"The folder of project @{name} no longer exists: {workdir}")
            return workdir
        workdir = Path(workdir_arg).expanduser().resolve()
        if not workdir.is_dir():
            raise SystemExit(
                f"The project folder {workdir} does not exist: create it first, or give another --workdir."
            )
        return workdir

    @staticmethod
    def find(name):
        pointers = list(Home.store().items("projects").values())
        found = [pointer for pointer in pointers if pointer.get("id") == name] or [
            pointer for pointer in pointers if Path(str(pointer.get("workdir", ""))).name == name
        ]
        if not found:
            raise SystemExit(
                f"No project @{name}: name one by its full run ID or its folder's name (tinker runs "
                f"lists them)"
            )
        if len(found) > 1:
            raise SystemExit(
                f"Several projects are in folders named {name}: name one by its ID: "
                f"{', '.join('@' + pointer['id'] for pointer in found)}"
            )
        return found[0]

    @staticmethod
    def read_record(workdir):
        run = saved(workdir, "run", "record")
        if run is None:
            raise SystemExit(
                f"No saved run in {workdir}. Name one with @PROJECT (tinker runs lists them), "
                f'or start one with: tinker task "..." --workdir {workdir}'
            )
        return run

    @staticmethod
    def read_spec_file(path):
        spec_path = Path(path).expanduser()
        if not spec_path.is_file():
            raise SystemExit(f"Spec file not found: {path}")
        return SpecSource(spec_path.read_text(), spec_path.name)

    @staticmethod
    def entries():
        return [
            {**pointer, **(Runs.record_pointed_to(pointer) or {"status": "missing"})}
            for pointer in Home.store().items("projects").values()
        ]

    @staticmethod
    def record_pointed_to(pointer):
        folder = pointer.get("workdir", "")
        return saved(folder, "run", "record") if Path(folder).is_dir() else None

    @staticmethod
    def clear_folder(workdir, spec, yes=False):
        Runs.refuse_to_empty_a_shared_folder(workdir)
        keep = next(
            (item for item in workdir.iterdir() if spec and spec.is_relative_to(item.resolve())), None
        )
        items = sorted(item for item in workdir.iterdir() if item != keep)
        if not items:
            return
        confirm_deletion(
            f"tinker restart deletes everything in {workdir}, to build the task again from scratch:",
            Runs.listed_for_deletion(items, keep),
            "tinker restart",
            yes,
        )
        for item in items:
            shutil.rmtree(item) if is_folder(item) else item.unlink()
        Runs.forget_runs_in(workdir)
        say.done(f"Emptied {workdir}")

    @staticmethod
    def refuse_to_empty_a_shared_folder(workdir):
        if workdir in (Path.home().resolve(), Path(workdir.anchor)) or Home.root.resolve().is_relative_to(
            workdir
        ):
            raise SystemExit(f"tinker restart will not empty {workdir}: run it in the project's own folder.")

    @staticmethod
    def listed_for_deletion(items, keep):
        listed = [f"{item.name}{'/' if is_folder(item) else ''}" for item in items[:20]]
        more = [f"... and {len(items) - 20} more"] if len(items) > 20 else []
        kept = [f"(kept: {keep.name}, which holds the spec file)"] if keep else []
        return listed + more + kept

    @staticmethod
    def forget_runs_in(workdir):
        for run_id, pointer in Home.store().items("projects").items():
            if pointer.get("workdir") == str(workdir):
                Home.store().delete("projects", run_id)


class Project:
    DEPENDENCY_FILES = {
        "requirements.txt",
        "pyproject.toml",
        "setup.cfg",
        "Pipfile",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "mix.exs",
    }

    def __init__(self, workdir, run, spec):
        self.workdir, self.run, self.spec = workdir, run, spec
        self.state = workdir / STATE_DIR
        self.store = project_store(workdir)

    @classmethod
    def load(cls, workdir, run=None):
        return cls(
            workdir,
            run or Runs.read_record(workdir),
            saved(workdir, "spec", "plan", {"steps": [], "counter": 0}),
        )

    def save(self):
        self.store.put("spec", "plan", self.spec)
        (self.workdir / "SPEC.md").write_text(SpecDocument(self).text())

    @property
    def language(self):
        return self.spec.get("language", "")

    @property
    def cycles(self):
        return self.spec.setdefault("cycles", [])

    def open_cycle(self):
        return next((cycle for cycle in self.cycles if cycle.get("phase") != "done"), None)

    def new_cycle(self, cycle_id, request, brief="", kind="feature"):
        cycle = {
            "id": cycle_id,
            "request": request,
            "brief": brief,
            "kind": kind,
            "phase": FIX_PHASES[0] if kind == "fix" else PHASES[0],
            "created": now_text(),
        }
        self.cycles.append(cycle)
        self.save()
        return cycle

    def cycle(self, cycle_id):
        return next((cycle for cycle in self.cycles if cycle["id"] == cycle_id), None)

    def cycle_criteria(self, cycle):
        return [criterion for criterion in self.criteria if criterion.get("cycle") == cycle["id"]]

    @property
    def criteria(self):
        return self.spec.setdefault("steps", [])

    def criterion(self, criterion_id):
        return next((criterion for criterion in self.criteria if criterion["id"] == criterion_id), None)

    def new_criterion(self, prefix, title, behaviour, test, cycle_id, source, status, effort=2):
        self.spec["counter"] = self.spec.get("counter", 0) + 1
        return {
            "id": f"{prefix}{self.spec['counter']}",
            "title": title,
            "behaviour": behaviour,
            "test": test,
            "effort": effort,
            "source": source,
            "status": status,
            "cycle": cycle_id,
            "history": [],
        }

    def criterion_of_test(self, test):
        return next((criterion["behaviour"] for criterion in self.criteria if criterion["test"] == test), "")

    def counts(self):
        green = sum(1 for criterion in self.criteria if criterion["status"] == "green")
        parked = sum(1 for criterion in self.criteria if criterion["status"] == "blocked")
        return green, parked, len(self.criteria)

    def parked(self):
        return [criterion for criterion in self.criteria if criterion["status"] == "blocked"]

    def refs(self):
        return [TestRef(criterion["test"]) for criterion in self.criteria]

    def test_names(self, file):
        return [ref.name for ref in self.refs() if ref.file == file]

    @property
    def toolchain(self):
        return Toolchain(self.spec.get("toolchain"))

    @toolchain.setter
    def toolchain(self, toolchain):
        self.spec["toolchain"] = toolchain.to_dict()

    @property
    def files(self):
        return self.spec.setdefault("files", {})

    def has_adapters(self):
        return any(info.get("role", "").lower().startswith("adapter") for info in self.files.values())

    def kind_of(self, path):
        return self.files.get(path, {}).get("kind")

    def paths(self, *kinds):
        return [path for path, info in self.files.items() if not kinds or info.get("kind") in kinds]

    def outline(self):
        return "\n".join(
            f"- {path} ({info.get('kind', 'code')}): {info.get('role', '')}"
            for path, info in self.files.items()
        )

    def register(self, created, kind, role, uses=()):
        for path in created:
            self.files.setdefault(path, {"kind": kind, "role": role, "contract": [], "uses": list(uses)})

    def remove_file(self, workspace, path):
        workspace.restore({path: None})
        self.files.pop(path, None)

    def uses(self, path, depth=2):
        direct = self.files.get(path, {}).get("uses", [])
        deeper = [*self.files.get(used, {}).get("uses", []) for used in direct] if depth > 1 else []
        return list(dict.fromkeys(direct + deeper))

    def criteria_of(self, path, steps):
        template = self.kind_of(path) == "template"
        direct = [step for step in steps if self.reached_by(path, step, template)]
        if not template:
            return direct
        renderers = [
            renderer
            for renderer, info in self.files.items()
            if path in info.get("uses", []) and renderer != path
        ]
        found = {
            step["id"]: step
            for step in direct + [*self.criteria_of(renderer, steps) for renderer in renderers]
        }
        return list(found.values())

    def reached_by(self, path, criterion, template):
        behaviour = criterion["behaviour"].lower()
        return (
            path in self.files.get(TestRef(criterion["test"]).file, {}).get("uses", [])
            or Path(path).name.lower() in behaviour
            or (
                not template
                and re.search(rf"\b{re.escape(Path(path).stem.lower())}\.\w", behaviour) is not None
            )
        )

    def entry_files(self, paths):
        entry, run = self.spec.get("entry", ""), self.toolchain.run
        return [path for path in paths if path == entry] + [
            path for path in paths if path != entry and Path(path).name in run
        ]

    def contracts(self, paths):
        out = []
        for path in paths:
            lines = self.files.get(path, {}).get("contract") or []
            if lines:
                out.append(f"{path}:\n" + "\n".join(f"  - {line}" for line in lines))
        return "\n".join(out)

    def dependencies(self, read):
        found = [
            path
            for path, info in self.files.items()
            if info.get("kind") == "config"
            and ("depend" in info.get("role", "").lower() or Path(path).name in self.DEPENDENCY_FILES)
        ]
        return "\n".join(
            f"{path}:\n{read(path) or self.files[path].get('role', '')}".strip() for path in found
        )


class SpecDocument:
    def __init__(self, project):
        self.project = project

    def text(self):
        lines = (
            self.heading() + self.table() + self.commands() + self.cycles() + self.design() + self.criteria()
        )
        return "\n".join(lines) + "\n"

    def heading(self):
        project = self.project
        topics = list(dict.fromkeys(item["name"] for item in project.spec.get("learned", [])))
        return [
            f"# Spec: {first_line(project.run['task'], 120)}",
            "",
            f"Run {project.run['id']}. Language: {project.language or '?'}."
            + (f" Learned from the web and used: {', '.join(topics)}." if topics else ""),
            "",
        ]

    def table(self):
        return ["| ID | Criterion | Test | Status |", "|---|---|---|---|"] + [
            f"| {criterion['id']} | {criterion['title']} | `{criterion['test']}` | {status_text(criterion)} |"
            for criterion in self.project.criteria
        ]

    def commands(self):
        toolchain = self.project.toolchain
        if not toolchain:
            return []
        for_anyone = TaughtCommands.spelled_out
        lines = ["", "## Commands (run in the project folder)", ""] + [
            f"- setup: `{for_anyone(command)}`" for command in toolchain.setup
        ]
        lines += [f"- activate: `{for_anyone(toolchain.activate)}`"] if toolchain.activate else []
        lines += [
            f"- {key}: `{for_anyone(getattr(toolchain, key))}`"
            for key in Toolchain.COMMANDS
            if getattr(toolchain, key)
        ]
        return lines

    def cycles(self):
        return [*self.cycle(cycle) for cycle in self.project.cycles]

    @staticmethod
    def cycle(cycle):
        lines = [
            "",
            f"## {'Project' if cycle['id'] == 'base' else 'Feature ' + cycle['id']}: "
            f"{first_line(cycle['request'], 120)}",
            "",
            f"Phase: {cycle.get('phase')}",
            "",
        ]
        for title, key in (
            ("Constraints", "constraints"),
            ("Not wanted (non-goals)", "non_goals"),
            ("Interface check", "review"),
        ):
            if cycle.get(key):
                lines += [f"{title}:"] + [f"- {item}" for item in cycle[key]] + [""]
        if cycle.get("data"):
            lines += [
                "External services: adapters and the sample data tests pass instead:",
                "",
                "```",
                cycle["data"],
                "```",
                "",
            ]
        return lines

    def design(self):
        if not self.project.files:
            return []
        lines = ["", "## Design (each file's contract; tests and code are written to it)", ""]
        for path, info in self.project.files.items():
            uses = f" (uses {', '.join(info['uses'])})" if info.get("uses") else ""
            lines.append(f"- `{path}` [{info.get('kind', 'code')}]: {info.get('role', '')}{uses}")
            lines += [f"    - {line}" for line in info.get("contract", [])]
        return lines

    def criteria(self):
        steps = self.project.criteria
        return (
            ["", "## Spec criteria (tests written first; the code passes them)", ""]
            + self.criteria_lines(steps)
            if steps
            else []
        )

    def criteria_lines(self, steps):
        lines = []
        for step in steps:
            lines += [f"### {step['id']} {step['title']}", "", step["behaviour"], ""]
            if step.get("source"):
                lines.append(f'- From the request: "{step["source"]}"')
            lines += [f"- Test: `{step['test']}`", f"- Status: {status_text(step)}"]
            if step.get("last_error") and step["status"] != "green":
                lines.append(f"- Last error: {first_line(step['last_error'], 300)}")
            lines.append("")
        return lines


class Brief:
    @staticmethod
    def load(model, store, mode, task, spec_text):
        if mode == "change":
            return ""
        kept = store.get("texts", "brief")
        if kept is not None:
            return kept
        brief = Brief.make(model, task, spec_text) if spec_text else ""
        store.put("texts", "brief", brief)
        return brief

    @staticmethod
    def make(model, task, spec):
        limit = max(400, model.limit_chars(Prompts.SPEC_SYSTEM) - len(task) - 300)
        if len(spec) <= limit:
            return spec.strip()
        chunks = split_text(spec, model.limit_chars(Prompts.BRIEF_SYSTEM) - len(task) - 120)
        words_each = max(60, limit // 7 // len(chunks))
        notes = [
            Reply(
                model.ask(
                    Prompts.BRIEF_SYSTEM.format(words=words_each),
                    [Part("Task", task, True), Part("Spec excerpt", chunk, True)],
                    f"Brief {number}/{len(chunks)}",
                )
            ).text
            for number, chunk in enumerate(chunks, 1)
        ]
        text = "\n".join(notes)
        cut = text.rfind("\n", 0, limit)
        return text if len(text) <= limit else text[: cut if cut > 0 else limit]


class Tracker:
    DOING_AND_DONE = {
        "recall": ("🧠 Recalling what Tinker has learned", "🧠 Recalled what Tinker has learned"),
        "amend": ("✏️ Changing the plan", "✏️ Plan changed"),
        "spec": ("📝 Writing the spec", "📝 Spec written"),
        "design": ("📐 Designing where the files meet", "📐 Design done"),
        "review": ("🔍 Reviewing the design", "🔍 Design reviewed"),
        "setup": ("🔧 Setting up the test tool", "🔧 Test tool set up"),
        "tests": ("🧪 Writing a test for every spec point", "🧪 Tests written"),
        "code": ("💻 Writing the code", "💻 Code written"),
        "verify": ("🔁 Fixing until every test passes", "🔁 Verified"),
        "tidy": ("🧹 Tidying the code", "🧹 Code tidied"),
        "docs": ("📖 Writing the README", "📖 README written"),
        "final_review": ("🏁 Final review, as a person runs it", "🏁 Final review done"),
        "lessons": ("📒 Keeping lessons for the next projects", "📒 Lessons kept"),
        "reproduce": ("🐞 Showing the bug with a test", "🐞 Bug shown by a test"),
        "search": ("🔎 Searching the web", "🔎 Web searched"),
        "plan": ("🤔 Working out what it is", "🤔 Worked out what it is"),
        "machine": ("💻 Checking what is installed", "💻 Checked what is installed"),
        "documentation": ("📖 Reading its documentation", "📖 Documentation read"),
        "digest": ("🧠 Reading the passages for answers", "🧠 Passages read"),
        "follow-up": ("🔁 Searching for what is still missing", "🔁 Searched for what was missing"),
        "condense": ("✍️ Turning the facts into rules", "✍️ Rules written"),
        "frame": ("📝 Framing the research questions", "📝 Questions framed"),
        "investigate": ("🔬 Reading and cross-checking sources", "🔬 Sources cross-checked"),
        "keep": ("💾 Saving what was learned", "💾 Saved"),
        "open": ("📖 Opening the book", "📖 Book opened"),
        "survey": ("🧭 Reading its contents, foreword and index", "🧭 Chose the chapters to read"),
        "chapters": ("📚 Reading the chapters that matter", "📚 Chapters read"),
        "answer": ("💬 Answering from what Tinker keeps", "💬 Answered"),
        "understand": ("🤔 Working out what to check", "🤔 Worked out what to check"),
        "judge": ("⚖️ Weighing the evidence", "⚖️ Evidence weighed"),
        "correct": ("✏️ Correcting what Tinker keeps", "✏️ Corrected"),
        "identify": ("🔤 Making sure it is a language", "🔤 It is a language"),
        "learn": ("📚 Learning the language first", "📚 Language learned"),
        "pick": ("🎯 Choosing the next practice project", "🎯 Practice project chosen"),
        "build": ("🔨 Building the practice project", "🔨 Practice project built"),
        "feature": ("➕ Adding a feature to it", "➕ Feature added"),
        "measure": ("📏 Measuring how it went", "📏 Measured"),
        "reflect": ("📒 Learning from what went wrong and what was slow", "📒 Lessons kept"),
        "searches": ("🤔 Working out what to search", "🤔 Searches chosen"),
        "read": ("🌐 Searching the web and reading the pages", "🌐 Pages read"),
        "write": ("✍️ Writing the answer from the pages", "✍️ Answer written"),
    }
    STARTING = "🚀 Starting: the model loads"
    QUIET_PUBLISH_SECONDS = 1.0

    def __init__(self, title, before=0.0):
        self.pid, self.store, self.published = os.getpid(), None, 0.0
        self.state = {
            "title": title,
            "run": None,
            "folder": None,
            "step": None,
            "doing": self.STARTING,
            "began": None,
            "steps": [],
            "passing": None,
            "working": "",
            "tally": "",
            "found": "",
            "outcome": "",
            "latest": "",
            "waiting": None,
            "ended": None,
            "before": before,
            "seconds": 0.0,
            "pid": self.pid,
            "pid_began": began_at(self.pid),
            "resumed": time.time() if Jobs.resumed_here else None,
            "exercise": os.environ.get(EXERCISE_ENV) == "1",
        }

    @classmethod
    def icon(cls, phase):
        return cls.DOING_AND_DONE[phase][0].split(" ", 1)[0] if phase in cls.DOING_AND_DONE else ""

    @classmethod
    def done_words(cls, phase):
        return cls.DOING_AND_DONE[phase][1].split(" ", 1)[1] if phase in cls.DOING_AND_DONE else phase

    def project(self, store, run_id, folder, title=None):
        self.store = store
        self.change("project", run=run_id, folder=str(folder), title=title or self.state["title"])

    def phase(self, phase):
        steps = list(self.state["steps"])
        if self.state["step"] in self.DOING_AND_DONE:
            outcome = self.state.get("outcome")
            steps.append(self.DOING_AND_DONE[self.state["step"]][1] + (f": {outcome}" if outcome else ""))
        doing = self.DOING_AND_DONE.get(phase, (phase, phase))[0]
        self.change(
            "phase",
            phase=phase,
            step=phase,
            doing=doing,
            began=time.time(),
            steps=steps,
            working="",
            found="",
            outcome="",
            latest="",
        )

    def points(self, passing, total):
        if total and self.state["passing"] != [passing, total]:
            self.change("points", passing=[passing, total])

    def outcome(self, text):
        self.change("outcome", outcome=text)

    def heard(self, message):
        if message.kind in ("working", "tally"):
            self.change(message.kind, quiet=message.kind == "tally", **{message.kind: message.text})
            return
        if message.kind == "live":
            return
        if self.store is not None:
            self.store.append("output", {"line": message.line, "kind": message.kind})
        found = {"found": message.text[:300]} if message.kind == "found" else {}
        self.change("said", quiet=True, latest=message.line.strip()[:300], **found)

    def wait(self, plan):
        self.change("waiting", waiting=plan, working="")

    def end(self, ending, seconds):
        ended = Jobs.ended(ending)
        paused = {"doing": f"⏸ {ended['headline']}"} if ended.get("status") == "paused" else {}
        self.change("ended", ended=ended, seconds=seconds, working="", **paused)

    def change(self, event, quiet=False, **values):
        self.state.update(values)
        if self.store is not None and not quiet:
            self.store.append(
                "progress",
                {"event": event, **{key: value for key, value in values.items() if key != "steps"}},
            )
        now = time.monotonic()
        if not quiet or now - self.published >= self.QUIET_PUBLISH_SECONDS:
            self.publish()

    def publish(self):
        self.published = time.monotonic()
        Home.store().put("running", str(self.pid), self.state)

    def close(self):
        Home.store().delete("running", str(self.pid))


class Steps:
    def __init__(self, names, tracker=None, only_if=None):
        self.names, self.tracker, self.only_if = tuple(names), tracker, only_if or {}

    def run(self, at, step, save, waits=lambda: False):
        while at != self.names[-1]:
            if waits():
                return "waiting"
            if self.wanted(at):
                self.enter(at)
                if step(at) is False:
                    return "blocked"
            at = self.names[self.names.index(at) + 1]
            save(at)
        return "done"

    def wanted(self, step):
        return self.only_if.get(step, lambda: True)()

    def enter(self, step):
        if step not in self.names:
            raise ValueError(f"no step {step!r}: the steps are {', '.join(self.names)}")
        if self.tracker is not None and self.tracker.state["step"] != step:
            self.tracker.phase(step)


class Outcome:
    def __init__(self):
        self.status, self.exit_code = "stopped", 1

    def set(self, status):
        self.status, self.exit_code = status, (1 if status == "blocked" else 0)


def project_store(workdir):
    return Store(Path(workdir) / STATE_DIR / PROJECT_DB)


def saved(workdir, collection, key, default=None):
    path = Path(workdir) / STATE_DIR / PROJECT_DB
    return project_store(workdir).get(collection, key, default) if path.is_file() else default


def status_text(criterion):
    return STATUS_TEXT.get(criterion["status"], criterion["status"])


def is_folder(item):
    return item.is_dir() and not item.is_symlink()
