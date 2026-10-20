"""The project's plan and record: the spec (Project, SpecDocument), its runs, and the checks before
a run."""

import json
import re
import secrets
import shutil
import sys
from pathlib import Path

from .prompts import Part, Prompts, Reply
from .settings import (
    FIX_PHASES, HOME, JsonFile, PHASES, PLANNING_PHASES, RUNS_DIR, STATE_DIR, STATUS_TEXT,
    first_line, now_text, split_text,
)
from .workspace import TaughtCommands, TestRef, Toolchain


class Outcome:
    """How a session ended, for the summary."""

    def __init__(self):
        self.status, self.exit_code = "stopped", 1

    def set(self, status):
        """"done", "planned" or "awaiting" (exit code 0), or "blocked" (1)."""
        self.status, self.exit_code = status, (1 if status == "blocked" else 0)


class RunChecks:
    """What a run needs that must be there already, checked before anything starts (no folder
    emptied, no model loaded): its folder, its saved state, and that the options fit it."""

    @staticmethod
    def restarting(args):
        """tinker restart: the folder's run read before anything is deleted (its task, its spec's
        text, its spec file when it is in the folder, which stays); its folder. No run: an error."""
        workdir = Runs.workdir("", args.workdir)
        run = JsonFile.read(workdir / STATE_DIR / "run.json", None, keeps_work=True)
        if not isinstance(run, dict) or not str(run.get("task") or "").strip():
            raise SystemExit(f"{workdir} has no task to restart: start one with tinker task \"...\"")
        args.task = run["task"]
        spec = workdir / STATE_DIR / "spec.md"
        args.spec_text = spec.read_text(encoding="utf-8") if spec.is_file() else ""
        args.spec_name = run.get("spec")
        named = Path(run["spec"]) if run.get("spec") else None
        args.spec = str(named) if named and named.is_file() else None
        return workdir

    @staticmethod
    def before_start(args):
        """What a run needs that must be there already, checked before anything starts (no folder
        emptied, no model loaded or measured): a new run's folder (with no run in it) and spec file;
        for tinker restart, the folder's run (its task and spec are built again); an existing run, its folder and its saved state; for tinker add and
        tinker fix a project whose cycles are all done; for --plan-only a cycle that is still planning.
        Returns the project's folder."""
        if args.restart:
            return RunChecks.restarting(args)
        if args.resume is None:
            workdir = Runs.workdir("", args.workdir)
            if args.spec:
                Runs.read_spec_file(args.spec)
            RunChecks.check_no_run(workdir)
            return workdir
        workdir = Runs.workdir(args.resume, args.workdir)
        state, run = workdir / STATE_DIR, Runs.read_record(workdir)
        if args.add is not None and args.spec_file:
            Runs.read_spec_file(args.spec_file)
        spec = JsonFile.read(state / "spec.json", {"steps": [], "counter": 0}, keeps_work=True)
        project = Project(workdir, run, spec)
        RunChecks.check_phases(project)
        if args.add is not None:
            RunChecks.check_open_to_add(project)
        elif args.amend:
            RunChecks.check_awaiting(project)
        elif args.plan_only:
            RunChecks.check_planning(project)
        if args.web and args.add is None:
            RunChecks.check_recall_ahead(project)
        return workdir

    @staticmethod
    def check_no_run(workdir):
        """A new run needs a folder without one (tinker resume, tinker add or tinker fix work on the one there)."""
        state = workdir / STATE_DIR
        if (state / "spec.json").is_file() or (state / "run.json").is_file():
            existing = JsonFile.read(state / "run.json", {}).get("id", "?")
            raise SystemExit(f"{workdir} already holds run {existing}. Continue it with tinker resume, add to it "
                             f'with tinker add "...", or start the new project in another folder (--workdir).')

    @staticmethod
    def check_phases(project):
        """Each saved cycle is at one of its phases (PHASES, or FIX_PHASES for a bug fix)."""
        for cycle in project.cycles:
            phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
            if cycle.get("phase") not in phases:
                raise SystemExit(f"The saved phase '{cycle.get('phase')}' of cycle {cycle.get('id')} is not one of its "
                                 f"phases ({', '.join(phases)}): set its \"phase\" in "
                                 f"{project.state / 'spec.json'} to one of them")

    @staticmethod
    def check_open_to_add(project):
        """tinker add and tinker fix start a new cycle: the project's own must exist, and none may be unfinished."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is not None:
            what = "project" if cycle["id"] == "base" else cycle.get("kind", "feature")
            raise SystemExit(f"Run {run_id} has an unfinished {what} ({first_line(cycle['request'], 80)}). "
                             f"Finish it first with: tinker resume {run_id}")
        if not project.cycles:
            raise SystemExit(f"Run {run_id} has no project spec yet; continue it with tinker resume first.")

    @staticmethod
    def check_awaiting(project, what="tinker amend changes"):
        """tinker amend, tinker rename: the open cycle's spec waits for the person to confirm it (task --confirm)."""
        cycle = project.open_cycle()
        if cycle is None or not cycle.get("awaiting"):
            raise SystemExit(f"{what} a spec that waits for confirmation (tinker task --confirm), and "
                             f"run {project.run.get('id', '?')} has none waiting: change a built project with "
                             f"tinker add or tinker fix")

    @staticmethod
    def check_planning(project):
        """tinker resume --plan-only: the open cycle is still planning (spec, design, review)."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is None and project.cycles:
            raise SystemExit(f"--plan-only: every cycle of run {run_id} is done, so there is nothing to plan. "
                             f'Add a feature with tinker add {run_id} "..."')
        if cycle is not None and cycle.get("kind") == "fix":
            raise SystemExit(f"--plan-only stops after planning, and a bug fix plans nothing (run {run_id}): continue "
                             f"it without --plan-only: tinker resume {run_id}")
        if cycle is not None and cycle["phase"] not in PLANNING_PHASES:
            raise SystemExit(f"--plan-only stops after planning, and run {run_id} is past it (next phase: "
                             f"{cycle['phase']}). Continue it without --plan-only: tinker resume {run_id}")

    @staticmethod
    def check_recall_ahead(project):
        """tinker resume --web: the web is used only by a cycle's recall phase, so the open cycle must
        still be at it."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is None and not project.cycles:
            return  # no cycle yet: the project's own starts at its recall phase
        if cycle is None or cycle["phase"] != "recall":
            where = f"next phase: {cycle['phase']}" if cycle else "every cycle is done"
            raise SystemExit(f"--web is used only by a cycle's recall phase, and run {run_id} is past it ({where}). "
                             f"Continue it without --web: tinker resume {run_id}")


class Runs:
    """The runs: each is registered in runs/ by its ID (and kept in its project's .tinker/run.json),
    so tinker resume ID, tinker add ID, tinker fix ID and tinker status ID work from any folder; a run opened (a new
    one, or a saved one resumed or changed); a folder emptied for tinker restart."""

    @staticmethod
    def clear_folder(workdir, spec, yes=False):
        """Delete what is in workdir, after showing it and asking (in a terminal only; yes: without asking).
        The entry that holds the spec file stays. Refuses the home folder, the filesystem root and the folder
        Tinker itself lives in."""
        if workdir in (Path.home().resolve(), Path(workdir.anchor)) or HOME.resolve().is_relative_to(workdir):
            raise SystemExit(f"tinker restart will not empty {workdir}: run it in the project's own folder.")
        keep = next((item for item in workdir.iterdir() if spec and spec.is_relative_to(item.resolve())), None)
        items = sorted(item for item in workdir.iterdir() if item != keep)
        if not items:
            return
        print(f"tinker restart deletes everything in {workdir} ({len(items)} items):")
        for item in items[:20]:
            print(f"  {item.name}{'/' if item.is_dir() and not item.is_symlink() else ''}")
        if len(items) > 20:
            print(f"  ... and {len(items) - 20} more")
        if keep:
            print(f"Kept: {keep.name} (it holds the spec file)")
        if not yes and not sys.stdin.isatty():
            raise SystemExit("Nothing deleted: run tinker restart in a terminal to confirm, or add --yes.")
        if not yes and input("Delete them and build the task again from scratch? [y/N] ").strip().lower() not in (
                "y", "yes"):
            raise SystemExit("Nothing deleted.")
        for item in items:
            shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink()
        Runs.forget_runs_in(workdir)
        print(f"Emptied {workdir}.")

    @staticmethod
    def entries():
        entries = []
        for path in sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.is_dir() else []:
            try:
                entries.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return entries

    @staticmethod
    def workdir(run_id, workdir_arg):
        """The project folder: the run's (by ID), else the one given (--workdir, else this one)."""
        if run_id:
            workdir = Path(Runs.find(run_id)["workdir"])
            if not workdir.is_dir():
                raise SystemExit(f"Project folder for run {run_id} no longer exists: {workdir}")
            return workdir
        workdir = Path(workdir_arg).expanduser().resolve()
        if not workdir.is_dir():
            raise SystemExit(f"The project folder {workdir} does not exist: create it first, or give another --workdir.")
        return workdir

    @staticmethod
    def read_spec_file(path):
        spec_path = Path(path).expanduser()
        if not spec_path.is_file():
            raise SystemExit(f"Spec file not found: {path}")
        return spec_path.read_text(encoding="utf-8"), spec_path.name

    @staticmethod
    def read_record(workdir):
        """The run saved in a project folder (its .tinker/run.json)."""
        run = JsonFile.read(workdir / STATE_DIR / "run.json", None, keeps_work=True)
        if run is None:
            raise SystemExit(f'No saved run in {workdir}. Start one with: tinker task "..." --workdir {workdir}')
        return run

    @staticmethod
    def forget_runs_in(workdir):
        """Drop the registered runs (runs/) of a folder that was emptied: their IDs lead nowhere now."""
        for path in sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.is_dir() else []:
            if JsonFile.read(path, {}).get("workdir") == str(workdir):
                path.unlink()

    @staticmethod
    def open(args):
        """Returns (run, workdir, spec_text, mode): mode new, resume, or change (tinker add, tinker fix)."""
        if args.resume is None:
            return Runs.open_new(args)
        return Runs.open_existing(args)

    @staticmethod
    def save(run, workdir):
        run["updated"] = now_text()
        (workdir / STATE_DIR / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        try:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            (RUNS_DIR / f"{run['id']}.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"  note: could not register run in {RUNS_DIR}: {exc}")

    @staticmethod
    def find(run_id):
        matches = [e for e in Runs.entries() if e.get("id", "").startswith(run_id.lower())]
        if not matches:
            raise SystemExit(f"No run with ID '{run_id}'. List runs with: tinker runs")
        if len(matches) > 1:
            raise SystemExit(f"ID '{run_id}' matches several runs: {', '.join(m['id'] for m in matches)}")
        return matches[0]

    @staticmethod
    def open_new(args):
        workdir = Path(args.workdir).expanduser().resolve()
        RunChecks.check_no_run(workdir)
        state = workdir / STATE_DIR
        state.mkdir(exist_ok=True)
        if getattr(args, "restart", False):  # the spec the run had, read before the folder was emptied
            spec_text, spec_name = args.spec_text, args.spec_name
        else:
            spec_text, spec_name = Runs.read_spec_file(args.spec) if args.spec else ("", None)
        (state / "spec.md").write_text(spec_text, encoding="utf-8")  # resume needs no spec file
        run = Runs.new_record(args.task, workdir, spec_name)
        Runs.save(run, workdir)
        return run, workdir, spec_text, "new"

    @staticmethod
    def open_existing(args):
        workdir = Runs.workdir(args.resume, args.workdir)
        state, run = workdir / STATE_DIR, Runs.read_record(workdir)
        adding = args.add is not None
        if adding:
            spec_text = Runs.read_spec_file(args.spec_file)[0] if args.spec_file else ""
        else:
            spec_text = (state / "spec.md").read_text(encoding="utf-8") if (state / "spec.md").is_file() else ""
        run["status"] = "running"
        Runs.save(run, workdir)
        return run, workdir, spec_text, ("change" if adding else "resume")

    @staticmethod
    def new_record(task, workdir, spec_name):
        return {"id": secrets.token_hex(3), "task": task, "workdir": str(workdir), "spec": spec_name,
                "created": now_text(), "status": "running"}


class Project:
    """Everything planned and done, saved in .tinker/spec.json (and shown in SPEC.md).
    Criteria are dicts: id, title, behaviour, test, effort, source, status, cycle, history."""
    DEPENDENCY_FILES = {"requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile", "package.json", "go.mod",
                        "Cargo.toml", "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "mix.exs"}

    def __init__(self, workdir, run, spec):
        self.workdir, self.run, self.spec = workdir, run, spec
        self.state = workdir / STATE_DIR

    @property
    def language(self):
        return self.spec.get("language", "")

    def save(self):
        (self.state / "spec.json").write_text(json.dumps(self.spec, indent=2), encoding="utf-8")
        (self.workdir / "SPEC.md").write_text(SpecDocument(self).text(), encoding="utf-8")

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

    def criterion_of_test(self, test):
        """The behaviour of the criterion a test checks ("" for a test no criterion owns)."""
        return next((s["behaviour"] for s in self.criteria if s["test"] == test), "")

    @property
    def criteria(self):
        """Every criterion, in plan order (saved as "steps")."""
        return self.spec.setdefault("steps", [])

    def counts(self):
        green = sum(1 for s in self.current if s["status"] == "green")
        parked = sum(1 for s in self.current if s["status"] == "blocked")
        return green, parked, len(self.current)

    def parked(self):
        """The criteria in play that nothing made pass (stored as "blocked")."""
        return [s for s in self.current if s["status"] == "blocked"]

    @property
    def toolchain(self):
        return Toolchain(self.spec.get("toolchain"))

    @toolchain.setter
    def toolchain(self, toolchain):
        self.spec["toolchain"] = toolchain.to_dict()

    @property
    def current(self):
        """The criteria in play (all of them)."""
        return self.criteria

    @property
    def files(self):
        """Every project file: path -> {kind, role, contract, uses}."""
        return self.spec.setdefault("files", {})

    def cycle_criteria(self, cycle):
        """The criteria of one cycle."""
        return [s for s in self.criteria if s.get("cycle") == cycle["id"]]

    def outline(self):
        """The project's files by role (what the design says, not parsed code)."""
        return "\n".join(f"- {p} ({f.get('kind', 'code')}): {f.get('role', '')}" for p, f in self.files.items())

    def paths(self, *kinds):
        return [p for p, f in self.files.items() if not kinds or f.get("kind") in kinds]

    def test_names(self, file):
        return [r.name for r in self.refs() if r.file == file]

    def remove_file(self, ws, path):
        """A file of the project gone: from the disk and from the design."""
        ws.restore({path: None})
        self.files.pop(path, None)

    def new_criterion(self, prefix, title, behaviour, test, cycle_id, source, status, effort=2):
        """A criterion with the next id: prefix C (spec), B (a bug)."""
        self.spec["counter"] = self.spec.get("counter", 0) + 1
        return {"id": f"{prefix}{self.spec['counter']}", "title": title, "behaviour": behaviour, "test": test,
                "effort": effort, "source": source, "status": status, "cycle": cycle_id, "history": []}

    def criteria_of(self, path, steps):
        """The criteria (of steps) a file must meet: those whose tests call it directly, or whose text
        names it ("playlist.songs", "app.py"); a template's are those of the files that render it.
        A file no test reaches (an adapter) has none: its contract says what it does."""
        name, stem = Path(path).name.lower(), Path(path).stem.lower()
        template = self.files.get(path, {}).get("kind") == "template"  # named by its file name, not its stem
        direct = [s for s in steps if path in self.files.get(TestRef(s["test"]).file, {}).get("uses", [])
                  or name in s["behaviour"].lower()
                  or (not template and re.search(rf"\b{re.escape(stem)}\.\w", s["behaviour"].lower()))]
        if not template:
            return direct
        renderers = [p for p, f in self.files.items() if path in f.get("uses", []) and p != path]
        found = {s["id"]: s for s in direct + [s for p in renderers for s in self.criteria_of(p, steps)]}
        return list(found.values())

    def refs(self):
        return [TestRef(s["test"]) for s in self.current]

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

    def register(self, created, kind, role, uses=()):
        for path in created:
            self.files.setdefault(path, {"kind": kind, "role": role, "contract": [], "uses": list(uses)})

    def dependencies(self, read):
        """The project's third-party dependencies: its dependency files (read(path) once written,
        else what the design says they hold). Empty when it has none."""
        found = [p for p, f in self.files.items() if f.get("kind") == "config"
                 and ("depend" in f.get("role", "").lower() or Path(p).name in self.DEPENDENCY_FILES)]
        return "\n".join(f"{p}:\n{read(p) or self.files[p].get('role', '')}".strip() for p in found)

    def cycle(self, cycle_id):
        return next((c for c in self.cycles if c["id"] == cycle_id), None)

    def criterion(self, criterion_id):
        return next(s for s in self.criteria if s["id"] == criterion_id)

    def uses(self, path, depth=2):
        """The files a file uses, directly and (depth 2) one level down."""
        direct = self.files.get(path, {}).get("uses", [])
        deeper = [u2 for u in direct for u2 in self.files.get(u, {}).get("uses", [])] if depth > 1 else []
        return list(dict.fromkeys(direct + deeper))


class Brief:
    """A long spec file condensed into a brief that fits the spec prompt (brief.md in the
    project's .tinker, so a resumed run reads it again without asking)."""

    @staticmethod
    def load(model, state, mode, task, spec_text):
        """The brief of the project's spec file (none for a change: a feature's spec is condensed
        with its request)."""
        if mode == "change":
            return ""
        brief_file = state / "brief.md"
        if brief_file.is_file():
            return brief_file.read_text(encoding="utf-8")
        brief = Brief.make(model, task, spec_text) if spec_text else ""
        brief_file.write_text(brief, encoding="utf-8")
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


class SpecDocument:
    """SPEC.md: the plan as a person reads it."""

    def __init__(self, project):
        self.p = project

    def text(self):
        p = self.p
        topics = list(dict.fromkeys(item["name"] for item in p.spec.get("learned", [])))
        lines = [f"# Spec: {first_line(p.run['task'], 120)}", "",
                 f"Run {p.run['id']}. Language: {p.language or '?'}."
                 + (f" Learned from the web and used: {', '.join(topics)}." if topics else ""), "",
                 "| ID | Criterion | Test | Status |", "|---|---|---|---|"]
        lines += [f"| {s['id']} | {s['title']} | `{s['test']}` | {STATUS_TEXT.get(s['status'], s['status'])} |"
                  for s in p.criteria]
        lines += self.commands() + self.cycles() + self.design() + self.criteria()
        return "\n".join(lines) + "\n"

    def commands(self):
        tc = self.p.toolchain
        if not tc:
            return []
        out = TaughtCommands.spelled_out  # your aliases spelled out: these commands work for anyone
        lines = ["", "## Commands (run in the project folder)", ""] + [f"- setup: `{out(c)}`" for c in tc.setup]
        lines += [f"- {key}: `{out(getattr(tc, key))}`" for key in Toolchain.COMMANDS if getattr(tc, key)]
        return lines

    def cycles(self):
        lines = []
        for cycle in self.p.cycles:
            lines += ["", f"## {'Project' if cycle['id'] == 'base' else 'Feature ' + cycle['id']}: "
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
            lines += [f"- Test: `{s['test']}`", f"- Status: {STATUS_TEXT.get(s['status'], s['status'])}"]
            if s.get("last_error") and s["status"] != "green":
                lines.append(f"- Last error: {first_line(s['last_error'], 300)}")
            lines.append("")
        return lines
