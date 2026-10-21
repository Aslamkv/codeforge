"""The project's plan and record: the spec (Project, SpecDocument), its runs, the checks before a run,
and the run's one report (Tracker). A project's state is kept in its own database, in its .tinker
folder; Tinker's home keeps only a pointer to each project."""

import os
import re
import secrets
import shutil
import time
from pathlib import Path

from .prompts import Part, Prompts, Reply
from .settings import (
    FIX_PHASES, Home, PHASES, PROJECT_DB, RUNS_KEPT, STATE_DIR, STATUS_TEXT, Store, confirmed, first_line, now_text, split_text,
)
from .workspace import TaughtCommands, TestRef, Toolchain


class RunChecks:
    """What a run needs that must be there already, checked before anything starts (no folder
    emptied, no model loaded): its folder, its saved state, and that the options fit it."""

    @staticmethod
    def before_start(args):
        """What a run needs that must be there already, checked before anything starts (no folder
        emptied, no model loaded or measured): a new run's folder (with no run in it) and spec file;
        for tinker restart, the folder's run (its task and spec are built again); an existing run, its
        folder and its saved state; for tinker add and tinker fix a project whose cycles are all done;
        for tinker amend a plan that waits. Returns the project's folder."""
        if args.restart:
            return RunChecks.restarting(args)
        if args.resume is None:
            return RunChecks.new_run(args)
        return RunChecks.existing_run(args)

    @staticmethod
    def restarting(args):
        """tinker restart: the folder's run read before anything is deleted (its task, its spec's
        text, its spec file when it is in the folder, which stays); its folder. No run: an error."""
        workdir = Runs.workdir("", args.workdir)
        run = saved(workdir, "run", "record")
        if not isinstance(run, dict) or not str(run.get("task") or "").strip():
            raise SystemExit(f"{workdir} has no task to restart: start one with tinker task \"...\"")
        args.task = run["task"]
        args.spec_text = saved(workdir, "texts", "spec_text", "")
        args.spec_name = run.get("spec")
        named = Path(run["spec"]) if run.get("spec") else None
        args.spec = str(named) if named and named.is_file() else None
        return workdir

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
        """A new run needs a folder without one (tinker resume, tinker add or tinker fix work on the one there)."""
        existing = saved(workdir, "run", "record")
        if existing is not None:
            raise SystemExit(f"{workdir} already holds run {existing.get('id', '?')}. Continue it with tinker resume, add "
                             f'to it with tinker add "...", or start the new project in another folder (--workdir).')

    @staticmethod
    def check_phases(project):
        """Each saved cycle is at one of its phases (PHASES, or FIX_PHASES for a bug fix)."""
        for cycle in project.cycles:
            phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
            if cycle.get("phase") not in phases:
                raise SystemExit(f"The saved phase '{cycle.get('phase')}' of cycle {cycle.get('id')} is not one of its "
                                 f"phases ({', '.join(phases)}): restart the project, or tell it as a bug")

    @staticmethod
    def check_open_to_add(project):
        """tinker add and tinker fix start a new cycle: the project's own must exist, and none may be unfinished."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is not None:
            what = "project" if cycle["id"] == "base" else cycle.get("kind", "feature")
            raise SystemExit(f"Run {run_id} has an unfinished {what} ({first_line(cycle['request'], 80)}). "
                             f"Finish it first with: tinker resume @{run_id}")
        if not project.cycles:
            raise SystemExit(f"Run {run_id} has no project spec yet; continue it with tinker resume first.")

    @staticmethod
    def check_awaiting(project, what="tinker amend changes"):
        """tinker amend, tinker rename: the open cycle's plan waits for the person to confirm it."""
        cycle = project.open_cycle()
        if cycle is None or not cycle.get("awaiting"):
            raise SystemExit(f"{what} a plan that waits for confirmation, and run {project.run.get('id', '?')} has "
                             f"none waiting: change a built project with tinker add or tinker fix")


class Runs:
    """The runs: each kept in its project's database, and pointed to from Tinker's home ("projects":
    its run ID, folder and task), so @ID and @folder work from any folder; one line about each run kept
    in the home ("runs"), so what it taught is traced after its folder is gone; a run opened (a new one,
    or a saved one resumed or changed); a folder emptied for tinker restart."""

    @staticmethod
    def open(args):
        """Returns (run, workdir, spec_text, mode): mode new, resume, or change (tinker add, tinker fix)."""
        if args.resume is None:
            return Runs.open_new(args)
        return Runs.open_existing(args)

    @staticmethod
    def open_new(args):
        workdir = Path(args.workdir).expanduser().resolve()
        RunChecks.check_no_run(workdir)
        spec_text, spec_name = Runs.spec_of_new_run(args)
        project_store(workdir).put("texts", "spec_text", spec_text)  # resume needs no spec file
        run = Runs.new_record(args.task, workdir, spec_name)
        Runs.save(run, workdir)
        return run, workdir, spec_text, "new"

    @staticmethod
    def spec_of_new_run(args):
        """(its text, its file's name): a restart's is the spec the run had, read before the folder was emptied."""
        if getattr(args, "restart", False):
            return args.spec_text, args.spec_name
        return Runs.read_spec_file(args.spec) if args.spec else ("", None)

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
        return {"id": secrets.token_hex(3), "task": task, "workdir": str(workdir), "spec": spec_name,
                "created": now_text(), "status": "running"}

    @staticmethod
    def save(run, workdir):
        """The run kept in its project, and pointed to from Tinker's home (its ID, folder and task only)."""
        run["updated"] = now_text()
        project_store(workdir).put("run", "record", run)
        Home.store().put("projects", run["id"], {"id": run["id"], "workdir": str(workdir), "task": run.get("task", "")})

    @staticmethod
    def remember(run, language):
        """One line about the run in Tinker's home, the last RUNS_KEPT (the least recently updated go first)."""
        line = {key: run.get(key, "") for key in ("id", "task", "status", "created", "updated")}
        Home.store().put("runs", run["id"], dict(line, language=language or ""))
        lines = Home.store().items("runs")
        for run_id in sorted(lines, key=lambda key: lines[key].get("updated", ""))[:max(0, len(lines) - RUNS_KEPT)]:
            Home.store().delete("runs", run_id)

    @staticmethod
    def workdir(name, workdir_arg):
        """The project folder: the named project's (@ID or @folder), else the one given (--workdir, else this one)."""
        if name:
            workdir = Path(Runs.find(name)["workdir"])
            if not workdir.is_dir():
                raise SystemExit(f"The folder of project @{name} no longer exists: {workdir}")
            return workdir
        workdir = Path(workdir_arg).expanduser().resolve()
        if not workdir.is_dir():
            raise SystemExit(f"The project folder {workdir} does not exist: create it first, or give another --workdir.")
        return workdir

    @staticmethod
    def find(name):
        """The project named exactly: by its full run ID, or by its folder's name (never by part of either)."""
        pointers = list(Home.store().items("projects").values())
        found = [p for p in pointers if p.get("id") == name] or \
                [p for p in pointers if Path(str(p.get("workdir", ""))).name == name]
        if not found:
            raise SystemExit(f"No project @{name}: name one by its full run ID or its folder's name (tinker runs "
                             f"lists them)")
        if len(found) > 1:
            raise SystemExit(f"Several projects are in folders named {name}: name one by its ID: "
                             f"{', '.join('@' + p['id'] for p in found)}")
        return found[0]

    @staticmethod
    def read_record(workdir):
        """The run kept in a project folder."""
        run = saved(workdir, "run", "record")
        if run is None:
            raise SystemExit(f'No saved run in {workdir}. Name one with @PROJECT (tinker runs lists them), '
                             f'or start one with: tinker task "..." --workdir {workdir}')
        return run

    @staticmethod
    def read_spec_file(path):
        spec_path = Path(path).expanduser()
        if not spec_path.is_file():
            raise SystemExit(f"Spec file not found: {path}")
        return spec_path.read_text(encoding="utf-8"), spec_path.name

    @staticmethod
    def entries():
        """Each project pointed to, with its own record when its folder is still there (else "missing")."""
        return [{**pointer, **(Runs.record_pointed_to(pointer) or {"status": "missing"})}
                for pointer in Home.store().items("projects").values()]

    @staticmethod
    def record_pointed_to(pointer):
        folder = pointer.get("workdir", "")
        return saved(folder, "run", "record") if Path(folder).is_dir() else None

    @staticmethod
    def clear_folder(workdir, spec, yes=False):
        """Delete what is in workdir, after showing it and asking (in a terminal only; yes: without asking).
        The entry that holds the spec file stays. Refuses the home folder, the filesystem root and the folder
        Tinker itself lives in."""
        Runs.refuse_to_empty_a_shared_folder(workdir)
        keep = next((item for item in workdir.iterdir() if spec and spec.is_relative_to(item.resolve())), None)
        items = sorted(item for item in workdir.iterdir() if item != keep)
        if not items:
            return
        Runs.show_what_emptying_deletes(workdir, items, keep)
        if not confirmed("Delete them and build the task again from scratch?", "tinker restart", yes):
            raise SystemExit("Nothing deleted.")
        for item in items:
            shutil.rmtree(item) if is_folder(item) else item.unlink()
        Runs.forget_runs_in(workdir)
        print(f"Emptied {workdir}.")

    @staticmethod
    def refuse_to_empty_a_shared_folder(workdir):
        """The home folder, the filesystem root and the folder Tinker lives in are never emptied."""
        if workdir in (Path.home().resolve(), Path(workdir.anchor)) or Home.root.resolve().is_relative_to(workdir):
            raise SystemExit(f"tinker restart will not empty {workdir}: run it in the project's own folder.")

    @staticmethod
    def show_what_emptying_deletes(workdir, items, keep):
        print(f"tinker restart deletes everything in {workdir} ({len(items)} items):")
        for item in items[:20]:
            print(f"  {item.name}{'/' if is_folder(item) else ''}")
        if len(items) > 20:
            print(f"  ... and {len(items) - 20} more")
        if keep:
            print(f"Kept: {keep.name} (it holds the spec file)")

    @staticmethod
    def forget_runs_in(workdir):
        """Drop the pointers to a folder that was emptied: they lead nowhere now."""
        for run_id, pointer in Home.store().items("projects").items():
            if pointer.get("workdir") == str(workdir):
                Home.store().delete("projects", run_id)


class Project:
    """Everything planned and done, kept in the project's database (and shown in SPEC.md).
    Criteria are dicts: id, title, behaviour, test, effort, source, status, cycle, history."""
    DEPENDENCY_FILES = {"requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile", "package.json", "go.mod",
                        "Cargo.toml", "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "mix.exs"}

    def __init__(self, workdir, run, spec):
        self.workdir, self.run, self.spec = workdir, run, spec
        self.state = workdir / STATE_DIR
        self.store = project_store(workdir)

    @classmethod
    def load(cls, workdir, run=None):
        """The project kept in a folder (its run read from it, unless given)."""
        return cls(workdir, run or Runs.read_record(workdir), saved(workdir, "spec", "plan", {"steps": [], "counter": 0}))

    def save(self):
        self.store.put("spec", "plan", self.spec)
        (self.workdir / "SPEC.md").write_text(SpecDocument(self).text(), encoding="utf-8")

    @property
    def language(self):
        return self.spec.get("language", "")

    @property
    def cycles(self):
        return self.spec.setdefault("cycles", [])

    def open_cycle(self):
        return next((c for c in self.cycles if c.get("phase") != "done"), None)

    def new_cycle(self, cycle_id, request, brief="", kind="feature"):
        cycle = {"id": cycle_id, "request": request, "brief": brief, "kind": kind,
                 "phase": FIX_PHASES[0] if kind == "fix" else PHASES[0], "created": now_text()}
        self.cycles.append(cycle)
        self.save()
        return cycle

    def cycle(self, cycle_id):
        return next((c for c in self.cycles if c["id"] == cycle_id), None)

    def cycle_criteria(self, cycle):
        """The criteria of one cycle."""
        return [s for s in self.criteria if s.get("cycle") == cycle["id"]]

    @property
    def criteria(self):
        """Every criterion, in plan order (saved as "steps")."""
        return self.spec.setdefault("steps", [])

    def criterion(self, criterion_id):
        return next((s for s in self.criteria if s["id"] == criterion_id), None)

    def new_criterion(self, prefix, title, behaviour, test, cycle_id, source, status, effort=2):
        """A criterion with the next id: prefix C (spec), B (a bug)."""
        self.spec["counter"] = self.spec.get("counter", 0) + 1
        return {"id": f"{prefix}{self.spec['counter']}", "title": title, "behaviour": behaviour, "test": test,
                "effort": effort, "source": source, "status": status, "cycle": cycle_id, "history": []}

    def criterion_of_test(self, test):
        """The behaviour of the criterion a test checks ("" for a test no criterion owns)."""
        return next((s["behaviour"] for s in self.criteria if s["test"] == test), "")

    def counts(self):
        green = sum(1 for s in self.criteria if s["status"] == "green")
        parked = sum(1 for s in self.criteria if s["status"] == "blocked")
        return green, parked, len(self.criteria)

    def parked(self):
        """The criteria that nothing made pass (stored as "blocked")."""
        return [s for s in self.criteria if s["status"] == "blocked"]

    def refs(self):
        return [TestRef(s["test"]) for s in self.criteria]

    def test_names(self, file):
        return [r.name for r in self.refs() if r.file == file]

    @property
    def toolchain(self):
        return Toolchain(self.spec.get("toolchain"))

    @toolchain.setter
    def toolchain(self, toolchain):
        self.spec["toolchain"] = toolchain.to_dict()

    @property
    def files(self):
        """Every project file: path -> {kind, role, contract, uses}."""
        return self.spec.setdefault("files", {})

    def paths(self, *kinds):
        return [p for p, f in self.files.items() if not kinds or f.get("kind") in kinds]

    def outline(self):
        """The project's files by role (what the design says, not parsed code)."""
        return "\n".join(f"- {p} ({f.get('kind', 'code')}): {f.get('role', '')}" for p, f in self.files.items())

    def register(self, created, kind, role, uses=()):
        for path in created:
            self.files.setdefault(path, {"kind": kind, "role": role, "contract": [], "uses": list(uses)})

    def remove_file(self, ws, path):
        """A file of the project gone: from the disk and from the design."""
        ws.restore({path: None})
        self.files.pop(path, None)

    def uses(self, path, depth=2):
        """The files a file uses, directly and (depth 2) one level down."""
        direct = self.files.get(path, {}).get("uses", [])
        deeper = [u2 for u in direct for u2 in self.files.get(u, {}).get("uses", [])] if depth > 1 else []
        return list(dict.fromkeys(direct + deeper))

    def criteria_of(self, path, steps):
        """The criteria (of steps) a file must meet: those whose tests call it directly, or whose text
        names it ("playlist.songs", "app.py"); a template's are those of the files that render it.
        A file no test reaches (an adapter) has none: its contract says what it does."""
        template = self.files.get(path, {}).get("kind") == "template"
        direct = [s for s in steps if self.reached_by(path, s, template)]
        if not template:
            return direct
        renderers = [p for p, f in self.files.items() if path in f.get("uses", []) and p != path]
        found = {s["id"]: s for s in direct + [s for p in renderers for s in self.criteria_of(p, steps)]}
        return list(found.values())

    def reached_by(self, path, criterion, template):
        """Whether the criterion's test uses the file, or its text names it (a template by its file name only)."""
        behaviour = criterion["behaviour"].lower()
        return path in self.files.get(TestRef(criterion["test"]).file, {}).get("uses", []) \
            or Path(path).name.lower() in behaviour \
            or (not template and re.search(rf"\b{re.escape(Path(path).stem.lower())}\.\w", behaviour) is not None)

    def entry_files(self, paths):
        """Of these paths, the app's entry point: the file the design says a user starts, then any
        the run command names."""
        entry, run = self.spec.get("entry", ""), self.toolchain.run
        return [p for p in paths if p == entry] + [p for p in paths if p != entry and Path(p).name in run]

    def contracts(self, paths):
        out = []
        for path in paths:
            lines = self.files.get(path, {}).get("contract") or []
            if lines:
                out.append(f"{path}:\n" + "\n".join(f"  - {line}" for line in lines))
        return "\n".join(out)

    def dependencies(self, read):
        """The project's third-party dependencies: its dependency files (read(path) once written,
        else what the design says they hold). Empty when it has none."""
        found = [p for p, f in self.files.items() if f.get("kind") == "config"
                 and ("depend" in f.get("role", "").lower() or Path(p).name in self.DEPENDENCY_FILES)]
        return "\n".join(f"{p}:\n{read(p) or self.files[p].get('role', '')}".strip() for p in found)


class SpecDocument:
    """SPEC.md: the plan as a person reads it."""

    def __init__(self, project):
        self.p = project

    def text(self):
        lines = self.heading() + self.table() + self.commands() + self.cycles() + self.design() + self.criteria()
        return "\n".join(lines) + "\n"

    def heading(self):
        p = self.p
        topics = list(dict.fromkeys(item["name"] for item in p.spec.get("learned", [])))
        return [f"# Spec: {first_line(p.run['task'], 120)}", "",
                f"Run {p.run['id']}. Language: {p.language or '?'}."
                + (f" Learned from the web and used: {', '.join(topics)}." if topics else ""), ""]

    def table(self):
        return ["| ID | Criterion | Test | Status |", "|---|---|---|---|"] + \
            [f"| {s['id']} | {s['title']} | `{s['test']}` | {status_text(s)} |" for s in self.p.criteria]

    def commands(self):
        tc = self.p.toolchain
        if not tc:
            return []
        out = TaughtCommands.spelled_out  # these commands work for anyone
        lines = ["", "## Commands (run in the project folder)", ""] + [f"- setup: `{out(c)}`" for c in tc.setup]
        lines += [f"- activate: `{out(tc.activate)}`"] if tc.activate else []
        lines += [f"- {key}: `{out(getattr(tc, key))}`" for key in Toolchain.COMMANDS if getattr(tc, key)]
        return lines

    def cycles(self):
        return [line for cycle in self.p.cycles for line in self.cycle(cycle)]

    @staticmethod
    def cycle(cycle):
        lines = ["", f"## {'Project' if cycle['id'] == 'base' else 'Feature ' + cycle['id']}: "
                     f"{first_line(cycle['request'], 120)}", "", f"Phase: {cycle.get('phase')}", ""]
        for title, key in (("Constraints", "constraints"), ("Not wanted (non-goals)", "non_goals"),
                           ("Interface check", "review")):
            if cycle.get(key):
                lines += [f"{title}:"] + [f"- {item}" for item in cycle[key]] + [""]
        if cycle.get("data"):
            lines += ["External services: adapters and the sample data tests pass instead:", "",
                      "```", cycle["data"], "```", ""]
        return lines

    def design(self):
        if not self.p.files:
            return []
        lines = ["", "## Design (each file's contract; tests and code are written to it)", ""]
        for path, f in self.p.files.items():
            uses = f" (uses {', '.join(f['uses'])})" if f.get("uses") else ""
            lines.append(f"- `{path}` [{f.get('kind', 'code')}]: {f.get('role', '')}{uses}")
            lines += [f"    - {c}" for c in f.get("contract", [])]
        return lines

    def criteria(self):
        steps = self.p.criteria
        return ["", "## Spec criteria (tests written first; the code passes them)", ""] + self.criteria_lines(steps) \
            if steps else []

    def criteria_lines(self, steps):
        lines = []
        for s in steps:
            lines += [f"### {s['id']} {s['title']}", "", s["behaviour"], ""]
            if s.get("source"):
                lines.append(f"- From the request: \"{s['source']}\"")
            lines += [f"- Test: `{s['test']}`", f"- Status: {status_text(s)}"]
            if s.get("last_error") and s["status"] != "green":
                lines.append(f"- Last error: {first_line(s['last_error'], 300)}")
            lines.append("")
        return lines


class Brief:
    """A long spec file condensed into a brief that fits the spec prompt (kept in the project's
    database, so a resumed run reads it again without asking)."""

    @staticmethod
    def load(model, store, mode, task, spec_text):
        """The brief of the project's spec file (none for a change: a feature's spec is condensed
        with its request)."""
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
        """A long spec read in prompt-sized chunks, each condensed, so it fits the spec prompt."""
        limit = max(400, model.limit_chars(Prompts.SPEC_SYSTEM) - len(task) - 300)
        if len(spec) <= limit:
            return spec.strip()
        chunks = split_text(spec, model.limit_chars(Prompts.BRIEF_SYSTEM) - len(task) - 120)
        words_each = max(60, limit // 7 // len(chunks))
        notes = [Reply(model.ask(Prompts.BRIEF_SYSTEM.format(words=words_each),
                                 [Part("Task", task, True), Part("Spec excerpt", chunk, True)],
                                 f"Brief {i}/{len(chunks)}")).text
                 for i, chunk in enumerate(chunks, 1)]
        text = "\n".join(notes)
        cut = text.rfind("\n", 0, limit)
        return text if len(text) <= limit else text[:cut if cut > 0 else limit]


class Tracker:
    """A run's one report: what it does, kept as data (never read back out of printed text). Each
    change is published at once in Tinker's home ("running", under the run's process), where the
    terminal and the chats read it, and appended to the project's "progress" log. The steps are named
    by phase (STEPS: what it is doing, and what it did)."""
    STEPS = {"recall": ("🧠 Recalling what Tinker has learned", "🧠 Recalled what Tinker has learned"),
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
             "reproduce": ("🐞 Showing the bug with a test", "🐞 Bug shown by a test")}
    STARTING = "🚀 Starting: the model loads"
    ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    QUIET_EVERY = 1.0  # seconds between publishing changes that only move the latest line on

    def __init__(self, title, before=0.0):
        self.pid, self.store, self.published = os.getpid(), None, 0.0
        self.state = {"title": title, "run": None, "folder": None, "step": None, "doing": self.STARTING,
                      "began": None, "steps": [], "passing": None, "live": "", "latest": "", "waiting": None,
                      "ended": None, "before": before, "seconds": 0.0, "paused": False, "pid": self.pid}

    def project(self, store, run_id, folder, title=None):
        """The run's project: its database (the progress log), its run ID and its folder."""
        self.store = store
        self.change("project", run=run_id, folder=str(folder), title=title or self.state["title"])

    def phase(self, phase):
        """The run is on to a phase: the one before is done."""
        steps = list(self.state["steps"])
        if self.state["step"] in self.STEPS:
            steps.append(self.STEPS[self.state["step"]][1])
        doing = self.STEPS.get(phase, (phase, phase))[0]
        self.change("phase", phase=phase, step=phase, doing=doing, began=time.time(), steps=steps, live="", latest="")

    def points(self, passing, total):
        if total and self.state["passing"] != [passing, total]:
            self.change("points", passing=[passing, total])

    def live(self, label, written, thinking, seconds):
        """What the model writes now (a reply as it goes)."""
        self.change("live", quiet=True,
                    live=f"{label.split(' [')[0]}: {written} tok written, {thinking} tok thinking, {seconds:.0f}s")

    def printed(self, line):
        """A line the run printed: kept in the project's output log, and shown as the latest."""
        line = self.ANSI_RE.sub("", line).rstrip()
        if self.store is not None:
            self.store.append("output", {"line": line})
        shown = self.without_clock(line)
        if shown and not shown.startswith("..."):
            self.change("printed", quiet=True, latest=shown[:300])

    @staticmethod
    def without_clock(line):
        """A step's line without its [time duration] prefix."""
        return line.split("] ", 1)[-1].strip() if line.startswith("[") else line.strip()

    def wait(self, plan):
        """The plan waits for the person (plan: what to show them, as data)."""
        self.change("waiting", waiting=plan, live="")

    def end(self, ending, seconds):
        """The run ended (ending: its status, headline, details and the commands to go on)."""
        self.change("ended", ended=ending, seconds=seconds, live="")

    def pause(self):
        self.change("paused", paused=True)

    def change(self, event, quiet=False, **values):
        self.state.update(values)
        if self.store is not None and not quiet:
            self.store.append("progress", {"event": event, **{k: v for k, v in values.items() if k != "steps"}})
        now = time.monotonic()
        if not quiet or now - self.published >= self.QUIET_EVERY:
            self.publish()

    def publish(self):
        self.published = time.monotonic()
        Home.store().put("running", str(self.pid), self.state)

    def close(self):
        """The run's report taken out of the home (it ended, and nobody waits to read it)."""
        Home.store().delete("running", str(self.pid))


class Outcome:
    """How a session ended, for the summary."""

    def __init__(self):
        self.status, self.exit_code = "stopped", 1

    def set(self, status):
        """"done" or "awaiting" (exit code 0), or "blocked" (1)."""
        self.status, self.exit_code = status, (1 if status == "blocked" else 0)


def project_store(workdir):
    """The database of the project in a folder (made when first written)."""
    return Store(Path(workdir) / STATE_DIR / PROJECT_DB)


def saved(workdir, collection, key, default=None):
    """What a project keeps, read without making a database in a folder that has none."""
    path = Path(workdir) / STATE_DIR / PROJECT_DB
    return project_store(workdir).get(collection, key, default) if path.is_file() else default


def status_text(criterion):
    return STATUS_TEXT.get(criterion["status"], criterion["status"])


def is_folder(item):
    return item.is_dir() and not item.is_symlink()
