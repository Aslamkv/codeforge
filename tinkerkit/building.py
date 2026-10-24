"""Building a cycle: the test tool set up, the tests, the code, and fixing until every test passes."""

lazy import itertools
lazy import json
import re
lazy import time
lazy from pathlib import Path

from .messages import human_duration, say
lazy from .planning import Designer, SpecWriter, asked
from .session import Phase, Worker
lazy from .prompts import Part, PromptTooLarge, Prompts, Reply
lazy from .settings import (
    BUILD_TRIES, ENV_EXAMPLE, ENV_FILE, FIX_FILES, MAX_FILE_LINES, MAX_TRIES_PER_PASS,
    OUTPUT_CHARS, REVIEW_TRIES, TESTS_LOCKED, TESTS_MAX_TOKENS, TESTS_PER_CALL, first_line,
)
lazy from .workspace import FileView, Output, TestRef, ToolError, Toolchain


class TestToolSetup(Phase):
    """4. Setup, before any test is written: the project's config files (its dependencies and its
    test tool's settings) are written, set up, and the test tool is shown to run (TestTool), so
    the tests are written for a test tool that is there and works, in any language."""
    __test__ = False  # not a pytest test class
    TITLE = "Setup (the test tool, before any test is written)"
    BUILDS = True

    def run(self, cycle):
        Designer(self.s).ensure_toolchain()
        CodeWriter(self.s).write_configs_first(cycle)
        self.s.setup()
        if self.s.network_waiting:  # the tests and the code need no network: they are written meanwhile
            say.info("The test tool is checked once the network is back; writing the tests and the code meanwhile",
                     "📡")
            return True
        return TestTool(self.s).works()


class TestWriter(Phase):
    """5. Spec tests, before the code: a few criteria per call."""
    __test__ = False  # not a pytest test class
    TITLE = "Spec tests (one for every spec point, before any code)"
    BUILDS = True

    def run(self, cycle, only=None):
        """A few criteria per call, before the code. A test file that already holds tests is never sent back to be rewritten (it may
        be too big, and rewriting risks its tests): the next tests go into a new file next to it, and
        their criteria follow them there."""
        steps = [s for s in (only or self.p.cycle_criteria(cycle)) if not TestRef(s["test"]).present(self.ws)]
        by_file = {}
        for step in steps:
            by_file.setdefault(TestRef(step["test"]).file, []).append(step)
        for path, group in by_file.items():
            for batch in itertools.batched(group, TESTS_PER_CALL):
                self.write_batch(cycle, path, list(batch))
        self.s.save()

    def write_batch(self, cycle, path, batch):
        note = ""
        for _ in (1, 2):
            todo = [s for s in batch if not TestRef(s["test"]).present(self.ws)]
            if not todo:
                return
            current = self.ws.read(path)
            try:
                with say.working_on(f"Writing {len(todo)} test(s) in {path}: {', '.join(s['id'] for s in todo)}"):
                    reply = self.s.ask(Prompts.SPEC_TESTS_SYSTEM, self.prompt(cycle, path, todo, current, note),
                                       f"Tests {'next to ' if current.strip() else ''}"
                                       f"{path} ({len(todo)})",
                                       max_reply=TESTS_MAX_TOKENS)  # past it, the thought is concluded
            except PromptTooLarge as exc:
                say.skip(f"Tests for {path} skipped: {exc}; verify writes them")
                return
            note = self.take(reply, path, todo, current)

    def prompt(self, cycle, path, todo, current, note):
        where = (f"a NEW test file next to {path} (do not change {path}; name it by the language's test "
                 f"conventions)" if current.strip() else path)
        uses = self.p.files.get(path, {}).get("uses") or self.p.paths("code", "template")
        parts = [Part(f"Tests to write in {where} (name: criterion)",
                      "\n".join(f"- {TestRef(s['test']).name}: {s['behaviour']}" for s in todo), True),
                 Part("Test command", self.s.toolchain.test_one, True, place="first"),
                 Part("Note", note, place="last"),
                 Part("External services: adapters and sample data (tests pass the sample data)", cycle.get("data", ""),
                      place="first")]
        configs = [c for c in self.s.files_on_disk("config") if c not in (ENV_FILE, ENV_EXAMPLE)]
        return parts + [
                Part("The project's config files (the tests use the names they give: the package, the library, "
                     "the program)", "\n\n".join(f"{c}:\n{self.ws.read(c)}" for c in configs), atomic=True,
                     place="first"),
                Part("Contracts of the files these tests use", self.p.contracts(uses)),
                Part(f"Start of {path} (its imports and shared setup; it stays as it is; a fake of the app's own "
                     f"code in it is not to be used)",
                     "\n".join(current.splitlines()[:30])),
                Part("Project files", self.p.outline(), place="first"),
                *self.s.knowledge_parts("tests")]

    def take(self, reply, path, todo, current):
        """The tests the reply wrote, kept where they belong; what to tell the next call ("" when all were)."""
        change = self.ws.apply(reply.as_file(self.new_file_next_to(path) if current.strip() else path),
                               self.may_write(path), empty_ok=False)
        self.keep_existing_tests(change)
        self.correct_criteria(todo, reply.corrected_criteria())
        self.follow_new_files(path, todo, change)
        return self.report_and_note(path, todo, change)

    def new_file_next_to(self, path):
        """A name for a new test file next to path, as the test tool will find it (path's own name
        with a number: test_use.py -> test_use_2.py)."""
        stem, suffix = Path(path).stem, Path(path).suffix
        number = 2
        while self.ws.exists(str(Path(path).with_name(f"{stem}_{number}{suffix}"))):
            number += 1
        return str(Path(path).with_name(f"{stem}_{number}{suffix}"))

    @staticmethod
    def may_write(path):
        folder = str(Path(path).parent)

        def check(p, exists):
            if str(Path(p).parent) == folder:
                return None  # a test file next to it (one that had tests keeps them: keep_existing_tests)
            return f"only test files next to {path} can be written now"
        return check

    def keep_existing_tests(self, change):
        """A rewrite of a test file stays only if every test it had is still there."""
        for path, old in list(change.backups.items()):
            had = [n for n in self.p.test_names(path) if old and TestRef.name_at(old, n) >= 0]
            lost = [n for n in had if TestRef.name_at(self.ws.read(path), n) < 0]
            if lost:
                self.ws.restore({path: change.backups.pop(path)})
                change.notes.append(f"{path} lost its tests {', '.join(lost)}; write the new tests in a new file")

    def correct_criteria(self, todo, corrected):
        """A criterion the test writer found wrong by the request's rules reads as its test checks,
        so SPEC.md, the source of truth, and the tests say the same."""
        for step in todo:
            text = corrected.get(TestRef(step["test"]).name)
            if text and text != step["behaviour"]:
                say.info(f"{step['id']} now reads: {first_line(text, 120)} (was: {first_line(step['behaviour'], 80)})", "✏️")
                step["behaviour"], step["title"] = text, text[:80]

    def follow_new_files(self, path, todo, change):
        """Tests written into a new file: their criteria point there; the file joins the project."""
        for step in todo:
            ref = TestRef(step["test"])
            home = next((p for p in change.created if TestRef.name_at(self.ws.read(p), ref.name) >= 0), None)
            if home and not ref.present(self.ws):
                step["test"] = f"{home}::{ref.name}"
        self.p.register(change.created, "test", f"tests (with {path})", self.p.files.get(path, {}).get("uses", []))

    def report_and_note(self, path, todo, change):
        for note in change.notes:
            say.skip(f"Skipped {note}")
        missing = [TestRef(s["test"]).name for s in todo if not TestRef(s["test"]).present(self.ws)]
        homes = sorted({TestRef(s["test"]).file for s in todo if TestRef(s["test"]).present(self.ws)})
        for step in todo:
            if TestRef(step["test"]).name in missing:
                step["write_failures"] = step.get("write_failures", 0) + 1
                step["last_error"] = ("its test could not be written: " + "; ".join(change.notes))[:400]
        say.done(f"{', '.join(homes) or path}: wrote {len(todo) - len(missing)}/{len(todo)} tests"
                 + (f"; missing {', '.join(missing)}" if missing else ""))
        return (f"These tests were not written: {', '.join(missing)}. " + "; ".join(change.notes))[:600] if missing else ""


class CodeWriter(Phase):
    """6. Code: just enough code to pass the tests: one call per file in design order (the
    config files were written by the setup phase; any still missing first, in one call); then
    setup, and a check that each file uses the files its contract says it uses."""
    TITLE = "Code (just enough to pass the tests)"
    BUILDS = True

    def run(self, cycle):
        self.write_all(cycle)
        ok, output = self.s.setup()
        if not ok:
            say.fail(f"Setup fails: {Output.key_line(output)} (verify fixes the toolchain first)")
        self.check_wiring(cycle)

    def write_all(self, cycle):
        """The config files still missing (in one call), then one call per other file (not tests,
        not docs), in design order. .env is made by Tinker from .env.example, never by the model."""
        self.write_configs_first(cycle)
        pending = self.pending(cycle, lambda kind: kind not in ("test", "docs"))
        for done, path in enumerate(pending, 1):
            role = first_line(self.p.files.get(path, {}).get("role", ""), 60)
            with say.working_on(f"Writing {path} ({done} of {len(pending)})" + (f": {role}" if role else "")):
                self.write_file(cycle, path)
            cycle["code_done"].append(path)
            self.s.save()
            say.tally(f"{done}/{len(pending)} files written")

    def write_configs_first(self, cycle):
        """The setup phase: the cycle's new config files (dependencies, the test tool's settings)
        in one call, before any test or code is written."""
        configs = [p for p in self.pending(cycle, lambda kind: kind == "config") if not self.ws.exists(p)]
        if configs:
            self.write_configs(cycle, configs)
        cycle["code_done"].extend(configs)
        self.s.save()

    def pending(self, cycle, wanted):
        """The cycle's files of a wanted kind not written yet, in design order (never .env)."""
        done = cycle.setdefault("code_done", [])
        return [p for p in cycle["files"]
                if p not in done and p != ENV_FILE and wanted(self.p.files.get(p, {}).get("kind"))]

    def write_configs(self, cycle, paths):
        """Small files (dependencies, settings, tool settings): one call for all of them."""
        listing = "\n".join(f"- {p}: {self.p.files[p].get('role', '')}" + "".join(
            f"\n    - {c}" for c in self.p.files[p].get("contract", [])) for p in paths)
        reply = self.s.ask(Prompts.CONFIG_SYSTEM, [
            Part("Files to write", listing, True),
            Part("Language and toolchain", f"{self.p.language}\n{json.dumps(self.s.toolchain.to_dict())}", True),
            Part("Constraints", "\n".join(f"- {c}" for c in cycle.get("constraints", []))),
            Part("Project files", self.p.outline()),
            *self.s.knowledge_parts("config", "toolchain"),
        ], f"Write {len(paths)} config files", think=False,  # short, conventional files: no thinking needed
           done_when=Reply.wrote(paths))
        change = self.ws.apply(reply, lambda p, exists: None if p in paths else "only the config files listed now")
        missing = [p for p in paths if p not in change.backups]
        say.done(f"Wrote {', '.join(change.files) or 'none'}" + (f"; missing {', '.join(missing)}" if missing else ""))
        for path in paths:
            if path not in change.backups:  # one more try, on its own
                self.write_file(cycle, path)

    def write_file(self, cycle, path):
        info, exists = self.p.files.get(path, {}), self.ws.exists(path)
        criteria = self.criteria_for(path, cycle)
        if not exists and info.get("marker") and not criteria:
            return self.write_marker(path)
        note = ""
        for attempt in (1, 2):
            view = FileView(self.ws, self.fixed_parts(path, info, criteria, note), [path] if exists else [],
                            self.context_parts(cycle, info, criteria), hints="\n".join(s["behaviour"] for s in criteria),
                            windows=1)
            try:
                reply = self.s.ask(Prompts.CHANGE_SYSTEM if exists else Prompts.CODE_SYSTEM, view,
                                   f"{'Change' if exists else 'Write'} {path}",
                                   done_when=Reply.files_complete if exists else Reply.wrote([path]))
            except PromptTooLarge as exc:
                say.skip(f"{path}: skipped, the prompt does not fit ({exc}); verify works on it")
                return
            change = self.ws.apply(self.addressed_to(reply.as_file(path), path, exists), self.may_write(path),
                                   view.windows)
            for text in change.notes:
                say.skip(f"Skipped {text}")
            note = self.unusable(path, change, attempt == 1 and info.get("kind") == "code")
            if not note:
                self.say_written(path, change, exists)
                return

    def write_marker(self, path):
        """A file nothing asks anything of (a package marker): written empty, with no call."""
        self.ws.write(path, "")
        say.done(f"Wrote {path} (empty: no contract or criterion asks anything of it)")

    def criteria_for(self, path, cycle):
        """The criteria this file must meet (the same rule its contract was planned with); the entry
        point's are also those that run the program as a person does ("running the binary with ...")."""
        steps = self.p.cycle_criteria(cycle)
        own = self.p.criteria_of(path, steps)
        if self.p.entry_files([path]):
            own += [s for s in steps if s not in own and SpecWriter.runs_program(s["behaviour"], path, self.s.toolchain.run)]
        return own

    def fixed_parts(self, path, info, criteria, note):
        renderers = [p for p, f in self.p.files.items() if path in f.get("uses", []) and f.get("kind") == "code"]
        template = info.get("kind") == "template"
        parts = [Part("Language", self.p.language, True, place="first"),
                 Part("File", f"{path} ({info.get('kind', 'code')})\nRole: {info.get('role', '')}", True),
                 Part("Its contract", self.p.contracts([path]), bool(info.get("contract"))),
                 Part("Contracts of the files that render it (what it is given and must show)",
                      self.p.contracts(renderers) if template else "", template and bool(renderers)),
                 Part("Criteria it serves", "\n".join(f"- {s['behaviour']}" for s in criteria)
                      or "none directly: its contract says what it does", True),
                 Part("How a user starts it (it is the entry point)", self.s.toolchain.run, True)
                 if path == self.p.spec.get("entry") and self.s.toolchain.run else Part("How a user starts it", "")]
        return parts + ([Part("Note", note, True)] if note else [])

    def context_parts(self, cycle, info, criteria):
        deps = self.p.dependencies(lambda p: self.ws.read(p) if self.ws.exists(p) else "")
        parts = [Part("Contracts of the files it uses", self.p.contracts(info.get("uses", []))),
                 Part(Prompts.DEPENDENCIES_TITLE, deps if info.get("kind") == "code" else "", atomic=True,
                      place="first"),
                 Part("External services: adapters and sample data", cycle.get("data", ""), place="first")]
        if info.get("kind") in ("code", "template"):
            names = [TestRef(s["test"]).name for s in self.p.criteria]
            parts += [Part(f"Test {i} ({s['test']})", TestRef(s["test"]).excerpt(self.ws, names), atomic=True)
                      for i, s in enumerate(criteria[:5], 1)]
        settings = (f"read from {ENV_FILE} (defaults in {ENV_EXAMPLE}); environment variables override it. Read it "
                    f"with a listed dependency, or with a few lines of standard-library code when none reads it")
        has_settings = info.get("kind") == "code" and ENV_EXAMPLE in self.p.files
        return parts + [Part("Settings", settings if has_settings else "", place="first"),
                        Part("Other files", self.p.outline(), place="first"),
                        Part("Not wanted (non-goals)", "\n".join(f"- {g}" for g in cycle.get("non_goals", [])),
                             place="first"),
                        *self.s.knowledge_parts("config" if info.get("kind") == "config" else "code")]

    @staticmethod
    def addressed_to(reply, path, exists):
        """A new file goes where the design says, whatever path the reply names."""
        blocks = reply.files()
        if not exists and len(blocks) == 1 and blocks[0][0] != path:
            return Reply(f"<<<FILE {path}>>>\n{blocks[0][1]}\n<<<END>>>")
        return reply

    def may_write(self, path):
        """The file itself, or a new module it splits off; a planned file is written in its own
        call, from its own contract."""
        def check(p, exists):
            if p == path or (not exists and p not in self.p.files):
                return None
            return f"only {path} (and new modules split off it) can be written now"
        return check

    def unusable(self, path, change, first_code):
        """What to tell the next call when the reply could not be used: it wrote no file, or (the first time
        for code) one far too long, then undone; "" when it is kept."""
        if path not in change.backups:
            return ("Your reply could not be used: " + "; ".join(change.notes))[:600] if change.notes else \
                f"Your reply had no usable <<<FILE {path}>>> block."
        if first_code and self.too_long(path):
            note = (f"Your {path} had {self.ws.lines(path)} lines; keep it under {MAX_FILE_LINES} by moving a "
                    f"part into a new small module (give that file too).")
            self.ws.revert(change)
            return note
        return ""

    def too_long(self, path):
        """A code file far past MAX_FILE_LINES (twice it): it is asked for again, in smaller modules. Only
        then: a rewrite into modules is a long call (minutes of thinking), and a file a little past the
        guidance still works (it is read in windows); a wordier language reaches it sooner."""
        lines = self.ws.lines(path)
        if lines > MAX_FILE_LINES * 2:
            say.warn(f"{path}: {lines} lines; asking for smaller modules")
            return True
        return False

    def say_written(self, path, change, exists):
        self.p.register([p for p in change.created if p != path], "code", f"module split from {path}")
        others = [p for p in change.files if p != path]
        say.done(f"{'Changed' if exists else 'Wrote'} {path} ({self.ws.lines(path)} lines)"
                 + (f"; also {', '.join(others)}" if others else ""))

    def check_wiring(self, cycle):
        """A code file must refer to each file its contract says it uses (by name or stem). One
        change call per file that does not."""
        for path in cycle["files"]:
            if self.p.files.get(path, {}).get("kind") != "code" or not self.ws.exists(path):
                continue
            problems = self.wiring_problems(path)
            if problems:
                self.rewire(path, problems)

    def wiring_problems(self, path):
        text = self.ws.read(path)
        return [f"it must use {u} (refer to it by name), as its contract says" for u in self.p.files[path].get("uses", [])
                if self.p.files.get(u, {}).get("kind") in ("code", "template")
                and Path(u).name not in text and Path(u).stem not in text]

    def rewire(self, path, problems):
        say.warn(f"{path} is not wired as designed: {'; '.join(problems)}")
        info = self.p.files[path]
        view = FileView(self.ws, [Part("Problems", "\n".join(problems), True),
                                  Part(f"Contract of {path}", self.p.contracts([path]), True)], [path],
                        [Part("Contracts of the files it uses", self.p.contracts(info.get("uses", [])))],
                        hints=" ".join(info.get("uses", [])), windows=1)
        reply = self.s.ask(Prompts.CHANGE_SYSTEM, view, f"Wire {path}")
        change = self.ws.apply(reply, lambda p, exists: None if p == path else f"only {path} changes now", view.windows)
        if change:
            say.done(f"Rewired {path}")
        else:
            say.skip(f"{path}: no usable change")


class Verifier(Phase):
    """7. Verify: fix the code until each criterion's test passes. When fixes stop making
    progress, one review against the plan decides whether the test or the code is wrong; then the
    criterion is parked. A change that breaks a passing test is reverted. Criteria failing the same
    way as one already parked are parked at once; parked ones get one more try if others passed
    since, or once when a later run resumes. A test that fails and passes on reruns is said to be
    flaky; a run stopped in the middle of a criterion leaves a restore point, used on resume."""
    TITLE = "Verify (until every test passes)"
    BUILDS = True
    RANK = {"missing": -1, "build": 0, "fails": 1, "passes": 2}

    PENDING = "verify"                      # the restore point: the files as they were before the criterion worked on
    FLAKY_RERUNS = 3                        # reruns of a failed test that tell a flaky one
    FLAKY_NOTE = ("This test's result changes from one run to the next with no change to the code: the code "
                  "is not deterministic. Look for what can differ between runs: the order of an unordered "
                  "collection (a set, a hash map), randomness, the clock, or state shared between tests.")

    def __init__(self, session):
        super().__init__(session)
        self.parked = {}  # failure signature -> the criterion parked with it

    def run(self, cycle):
        """True when every criterion passes, False when some are parked."""
        self.recover_interrupted()
        Designer(self.s).ensure_toolchain()
        if not TestTool(self.s).works() or not self.make_it_build():
            return False
        self.mark_passing()
        green_at_start = self.p.counts()[0]
        self.work_through([s for s in self.p.criteria if s["status"] in ("todo", "active")], retry=False)
        self.retry_parked(green_at_start)
        green, _, total = self.p.counts()
        return green == total

    def recover_interrupted(self):
        """A run stopped while a criterion was being worked on (Ctrl-C, a kill, a crash) may have
        left a change that was never checked: the files go back to how they were before it."""
        pending = self.p.store.get("restore", self.PENDING)
        if not isinstance(pending, dict) or not isinstance(pending.get("files"), dict):
            return
        changed = [p for p, text in pending["files"].items() if self.ws.read(p) != (text or "")]
        if changed:
            self.roll_back(pending["files"])
            say.info(f"The last run stopped while {pending.get('criterion', 'a criterion')} was being worked on: "
                     f"{', '.join(changed)} restored to how they were before it", "♻️")
        self.p.store.delete("restore", self.PENDING)

    def make_it_build(self):
        """The project builds and loads (its check command passes) before any criterion is worked
        on: a missing dependency, an import of something that does not exist or a syntax error is
        the project's problem, not one criterion's. Each failure gets a fix that sees the error,
        the files it names and the dependency files; then setup and the check run again. A fix that
        thought to its cap gets its conclusion, even past the fixes allowed."""
        tried, thought = [], ""
        for attempt in itertools.count():
            ok, output = self.s.setup()
            if ok:
                ok, output = self.s.toolchain.run_check(self.ws)
            if ok:
                say.done("The project builds now" if attempt else "The project builds")
                return True
            say.fail(f"The project does not build: {Output.key_line(output)}")
            if attempt >= BUILD_TRIES and not thought:
                break
            fix = BuildFix(self.s, output, tried, thought)
            tried.append(fix.run())
            thought = fix.thought
            self.s.all_tests_run = None
        self.s.build_problem = f"the project does not build after {len(tried)} fixes: {Output.key_line(output)}"
        return False

    def mark_passing(self):
        green = self.passing()
        for s in self.p.criteria:
            s["status"] = "green" if s["id"] in green else ("blocked" if s["status"] == "blocked" else "todo")
        self.s.save()

    def passing(self):
        """The criteria whose tests pass now: one run of all tests; one per test only when some fail."""
        started = time.perf_counter()
        code, _ = self.s.run_all_tests()
        tests = [s for s in self.p.criteria if TestRef(s["test"]).present(self.ws)]
        if code == 0:
            result = {s["id"] for s in tests}
        elif not self.s.toolchain.run_check(self.ws)[0]:
            return set()  # it does not build: no test can pass
        else:
            result = {s["id"] for s in tests if self.evaluate(s, checked=True)[0] == "passes"}
        took = human_duration(time.perf_counter() - started)
        say.info(f"Tests: {len(result)}/{len(self.p.criteria)} criteria pass ({took})", "🧪")
        return result

    def work_through(self, steps, retry):
        for step in steps:
            if step["status"] == "green":
                continue
            passed = self.work(step, retry)
            step["status"] = "green" if passed else "blocked"
            if passed:
                self.mark_others_passing()
            self.s.save()
            green, _, total = self.p.counts()
            if passed:
                say.done(f"{step['id']} passes ({green}/{total} pass)")
                say.found(f"Works now: {step['title']}")
            else:
                say.info(f"{step['id']} is parked ({green}/{total} pass)", "⏸️")

    def retry_parked(self, green_at_start):
        """The parked criteria get one more try when others passed since, else those parked by an earlier
        run once (the project, its plan or Tinker may have changed since)."""
        parked = [s for s in self.p.criteria if s["status"] == "blocked"]
        earlier = [s for s in parked if s["id"] in self.s.parked_at_start]
        if parked and self.p.counts()[0] > green_at_start:
            say.step(f"Retrying {len(parked)} parked criteria, now that others pass", "🔁")
            self.parked = {}
            self.work_through(parked, retry=True)
        elif earlier:
            say.step(f"Retrying {len(earlier)} criteria parked by an earlier run, once (the project, its plan or "
                     f"Tinker may have changed since)", "🔁")
            self.s.parked_at_start -= {s["id"] for s in earlier}
            self.parked = {}
            self.work_through(earlier, retry=True)
        elif parked:
            say.skip(f"{len(parked)} criteria parked and nothing else passes yet: a retry would see the same project")

    def work(self, step, retry):
        """Whether the criterion passes after fixes; parked (False) when nothing helps, the files
        then back to how they were."""
        green, _, total = self.p.counts()
        with say.part(f"{step['id']} ({green}/{total} pass): {step['title']}", "🎯"), \
                say.working_on(f"Making {step['id']} pass: {first_line(step['title'], 80)}"):
            say.info(f"Test: {step['test']}", "🧪")
            return self.fixed(step, retry)

    def fixed(self, step, retry):
        step["status"] = "active"
        state, output = self.first_look(step)
        if state in ("missing", "passes"):
            return state == "passes"
        same = self.failure_key(step, output)
        if same in self.parked:
            step["last_error"] = f"same failure as {self.parked[same]}"
            say.skip(f"Fails the same way as {self.parked[same]}, which nothing fixed; parked without new attempts")
            return False
        if self.climb_safely(step, state, output, retry):
            return True
        self.parked.setdefault(same, step["id"])
        say.fail(f"Nothing helped; {step['id']} is back to how it was")
        return False

    def first_look(self, step):
        """(state, output) of the criterion's test: written now if it is missing, and a failure
        that passes on reruns said to be flaky."""
        state, output = self.evaluate(step)
        if state == "missing":
            state, output = self.write_missing_test(step)
        if state == "fails" and self.flaky(step):
            say.warn("Its test passes and fails on reruns: the code is not deterministic")
            output += f"\n\n{self.FLAKY_NOTE}"
        return state, output

    def evaluate(self, step, checked=False):
        """(state, output): missing / build (the check fails) / fails / passes."""
        ref = TestRef(step["test"])
        if not ref.present(self.ws):
            return "missing", f"the test {ref} does not exist"
        if not checked:
            ok, output = self.s.toolchain.run_check(self.ws)
            if not ok:
                return "build", output
        return self.s.toolchain.run_test(self.ws, ref)

    def write_missing_test(self, step):
        """Its test is written now, unless writing it already failed twice (asking the same again
        would fail the same way)."""
        if step.get("write_failures", 0) >= 2:
            say.fail(f"Its test could not be written in {step['write_failures']} tries; parked")
            return "missing", step.get("last_error") or f"the test {step['test']} could not be written"
        say.step("Its test is missing; writing it", "✍️")
        TestWriter(self.s).run(self.p.cycle(step.get("cycle") or "base") or {}, only=[step])
        state, output = self.evaluate(step)
        if state == "missing":
            step["last_error"] = output
        return state, output

    def flaky(self, step):
        """Whether the criterion's test, which just failed, passes on one of a few reruns."""
        ref = TestRef(step["test"])
        return any(self.s.toolchain.run_test(self.ws, ref)[0] == "passes" for _ in range(self.FLAKY_RERUNS))

    @staticmethod
    def failure_key(step, output):
        """The failure without the test's own name: criteria failing alike share it."""
        ref = TestRef(step["test"])
        unnamed = "\0"  # a test without a name: nothing in the output is replaced for it
        return Output.signature(output.replace(str(ref), "#").replace(ref.name or unnamed, "#").replace(ref.file, "#"))

    def climb_safely(self, step, state, output, retry):
        """climb, with a restore point kept on disk while it goes (a run stopped part way is put
        back on resume: recover_interrupted); the files go back if nothing helped."""
        snapshot = self.ws.snapshot(self.p.files)  # with its test: rolling back keeps the test just written
        self.p.store.put("restore", self.PENDING, {"criterion": step["id"], "files": snapshot})
        try:
            if self.climb(step, state, output, retry):
                return True
            self.roll_back(snapshot)
            return False
        finally:
            self.p.store.delete("restore", self.PENDING)

    def climb(self, step, state, output, retry):
        """Fixes until they stop making progress, then reviews against the plan (see ladder); a
        reason to review sooner (see to_review) cuts the fixing short. An attempt that thought to its
        cap always gets its conclusion, even past the attempts allowed. True when the test passes."""
        before = {s["id"] for s in self.p.criteria if s["status"] == "green"}
        climb, total = Climb(state, output), 0
        for approach, patience in self.ladder(step, output, retry):
            stall, concluding = 0, False  # concluding: the last attempt thought to its cap; its conclusion is due
            while (stall < patience or concluding) and total < MAX_TRIES_PER_PASS:
                if approach == "fix" and self.test_unloadable(step, output):
                    say.step("The test file no longer loads: reviewing the test now", "🔍")
                    break
                total += 1
                attempt = FixAttempt(self.s, self, step, approach, output, before)
                state, output = attempt.run()
                if state == "passes":
                    return True
                stall = 0 if climb.progressed(state, output) else stall + 1
                concluding = attempt.overthought
                step["last_error"] = Output.key_line(output)
                why = self.to_review(attempt, approach)
                if why:
                    say.step(f"{why}: reviewing against the plan now", "🔍")
                    break
        return False

    def ladder(self, step, output, retry):
        """The approaches in turn, each with the attempts without progress it is given: fix the
        code (DEFAULT_MAX_STALL times without progress), then review the test and the code against the plan (a retry: one each).
        The review comes at once when the fault is likely the test's: it does not load, or fixes
        kept breaking other tests in an earlier pass."""
        if self.test_unloadable(step, output):
            say.step(f"The test file {TestRef(step['test']).file} itself does not load: reviewing the test now", "🔍")
            return (("review", REVIEW_TRIES),)
        if sum(" broke " in line for line in step.get("history", [])) >= 2:
            say.step("Earlier fixes kept breaking other tests (the tests may disagree): reviewing against the plan now",
                     "🔍")
            return (("review", REVIEW_TRIES),)
        return (("fix", 1), ("review", 1)) if retry else (("fix", self.s.max_stall), ("review", REVIEW_TRIES))

    @staticmethod
    def to_review(attempt, approach):
        """Why fixing should stop for a review, or "": the fix wanted to change the test, or fixes
        keep breaking the same other test (the two tests may disagree)."""
        if attempt.blamed_test:
            return "the fix points at the test, not the code"
        if attempt.conflict and approach == "fix":
            return "fixes keep breaking the same other test (the tests may disagree)"
        return ""

    @staticmethod
    def test_unloadable(step, output):
        ref = TestRef(step["test"])
        return Output.unloadable(output, ref.file)

    def roll_back(self, snapshot):
        """The project as it was: its files, and its design (files created since are dropped)."""
        for path in list(self.p.files):
            if path not in snapshot:  # created since: it goes again
                (self.ws.root / path).unlink(missing_ok=True)
                del self.p.files[path]
        self.ws.restore(snapshot)

    def mark_others_passing(self):
        """A fix may have made other criteria pass too."""
        for other in self.p.criteria:
            if other["status"] != "green" and self.evaluate(other)[0] == "passes":
                other["status"] = "green"


class Climb:
    """How a criterion's fixes go: the furthest stage its test reached (Verifier.RANK) and the failures seen.
    A new failure at the same stage is progress once (a fix may uncover the next problem); failures that only
    change shape (compile errors worded anew) are not, until a stage is won."""

    def __init__(self, state, output):
        self.seen, self.best, self.sideways = {Output.signature(output)}, Verifier.RANK[state], 0

    def progressed(self, state, output):
        rank, signature = Verifier.RANK[state], Output.signature(output)
        self.sideways = 0 if rank > self.best else self.sideways + (signature not in self.seen)
        progressed = rank > self.best or (rank == self.best and signature not in self.seen and self.sideways <= 1)
        self.best = max(self.best, rank)
        self.seen.add(signature)
        return progressed


class FixAttempt(Worker):
    """One model call for a failing test: the prompt, the change, and its result."""
    APPROACH_TEXT = {"fix": "fix the code", "review": "review the test and the code against the plan"}

    def __init__(self, session, verifier, step, approach, output, before):
        super().__init__(session)
        self.v = verifier
        self.step, self.approach, self.output, self.before = step, approach, output, before
        self.ref = TestRef(step["test"])
        self.label = f"{self.APPROACH_TEXT[approach].capitalize()} {step['id']}"
        self.blamed_test = False  # a fix that changed nothing because it only wanted to change a test
        self.conflict = False     # it broke a test an earlier fix broke too: the two tests may disagree
        self.overthought = False  # it thought to its cap without an answer
        self.broken_test, self.broken_output = "", ""  # a test the change broke, and how it failed

    def run(self):
        """(state, output) after the attempt; the output carries what went wrong."""
        view = self.prompt()
        try:
            reply = self.ask(view)
        except PromptTooLarge as exc:
            return "fails", self.output + f"\n(prompt too large: {exc})"
        if self.approach == "fix" and reply.test_wrong():
            return self.test_blamed(reply.test_wrong())
        change = self.ws.apply(reply, self.may_change(view), view.windows)
        self.remember_wanted_files(reply, change, view)
        cause = reply.cause() or "?"
        if not change:
            return self.no_change(cause, change)
        return self.tested(change, reply, cause)

    def prompt(self):
        """The criterion, the files that exist, the failure and the test first (and, after a call
        that thought to its cap, that thought); then the files most likely involved, whole while
        they fit (see FileView)."""
        excerpt = self.ref.excerpt(self.ws, self.p.test_names(self.ref.file))
        fixed = [Part("Criterion", self.step["behaviour"], True),
                 Part("Project files (all exist; only those shown below may change)",
                      ", ".join(p for p in self.p.files if self.ws.exists(p)), True),
                 Part("Failure output", Output.failure_text(self.output), True, keep="tail", place="last"),
                 Part(f"Test {self.ref}", excerpt, True)] + self.kept_tests()
        if self.approach == "review":
            fixed += self.plan_parts()
        thought = self.step.pop("unfinished_thought", "")
        fixed += [Part("Your analysis so far (it ran out of room before an answer: conclude it and answer now)",
                       thought, True, keep="tail", place="last")] if thought else []
        candidates = self.candidates()
        tail = [Part("Contracts", self.p.contracts(candidates[:4])),
                Part("Already tried", "\n".join(self.step.get("history", [])[-4:]), keep="tail", place="last"),
                Part("Language", self.p.language, place="first"),
                *self.s.knowledge_parts(*(("code", "tests") if self.approach == "review" else ("code",)))]
        return FileView(self.ws, fixed, candidates, tail, output=self.output, hints=excerpt)

    def kept_tests(self):
        """The tests an earlier change broke (up to two): they pass now and must keep passing."""
        return [Part(f"Test {ref} (it passes now and must keep passing)", ref.excerpt(self.ws, self.p.test_names(ref.file)),
                     True, atomic=True) for ref in map(TestRef, self.step.get("keep", [])[:2]) if ref.present(self.ws)]

    def plan_parts(self):
        """For a review: the request, which decides when the plan contradicts itself, and the criteria of the
        tests that must keep passing."""
        return [Part("Request", self.p.run["task"], True, keep="head", place="first")] + \
            [Part(f"Criterion of {ref}", self.p.criterion_of_test(str(ref)), True)
             for ref in map(TestRef, self.step.get("keep", [])[:2]) if self.p.criterion_of_test(str(ref))]

    def candidates(self):
        """Files the model asked for last time first, then those the failure names, then the
        files the test uses, then the rest (the test file
        itself only in a review)."""
        code = [p for p in self.s.files_on_disk("code", "template", "config") if p != ENV_FILE]
        named = [p for p in self.mentioned(code) if p in code]
        wanted = [p for p in self.step.get("want", []) if self.ws.exists(p)]
        own_test = [self.ref.file] if self.approach == "review" else []
        uses = [p for p in self.p.uses(self.ref.file) if p in code]
        return list(dict.fromkeys(wanted + own_test + named[:2] + uses + named + code))[:FIX_FILES]

    def mentioned(self, paths):
        return Output.mentioned_in(self.output, paths)

    def ask(self, view):
        """The reply. The first call of an approach for a criterion does not think: most failures
        show their cause, and a call that thinks often thinks for minutes to its cap first. The
        next ones think (a hard problem needs it); when one thought to its cap without an answer,
        the call after it does not think (the same prompt would think the same way again): it is
        given that thought to conclude."""
        key, tried = f"overthought_{self.approach}", f"tried_{self.approach}"
        first, concluding = not self.step.get(tried), bool(self.step.pop(key, None))
        self.step[tried] = self.step.get(tried, 0) + 1
        think = False if first or concluding else None
        label = self.label + (" (no thinking: concluding the last one's thought)" if concluding
                              else " (no thinking first)" if first else "")
        reply = self.s.ask(self.system(), view, label, max_reply=self.s.model.fix_max, unthinking_retry=False,
                           think=think)
        self.overthought = self.s.model.overthought
        if self.overthought:
            self.step[key], self.step["unfinished_thought"] = True, self.s.model.last_thought
        return reply

    def system(self):
        return Prompts.FIX_SYSTEM if self.approach == "fix" else Prompts.REVIEW_SYSTEM

    def may_change(self, view):
        def check(path, exists):
            if not exists:  # the design is settled: a new file now would shadow or duplicate a planned one
                return None if self.approach == "review" and Path(path).parent == Path(self.ref.file).parent \
                    else "no new files now; change the files shown"
            if self.p.files.get(path, {}).get("kind") == "test" or path == self.ref.file:
                return None if self.approach == "review" and path in view.shown else TESTS_LOCKED
            return None if path in view.shown else "only the files shown may change"
        return check

    def remember_wanted_files(self, reply, change, view):
        """A file it tried to change without seeing it whole is shown first next time."""
        self.step["want"] = [p for p in reply.named_paths() if p not in change.backups and self.ws.exists(p)
                             and p not in view.whole][:2]

    def test_blamed(self, why):
        """The fix found the test wrong, not the code: nothing changes, and the review comes next."""
        self.blamed_test = True
        self.record(f"the test is wrong: {why}")
        say.warn(f"{self.label}: the test is wrong ({first_line(why, 120)})")
        return "fails", self.output

    def no_change(self, cause, change):
        note = "no usable change" + (f" ({'; '.join(change.notes)})" if change.notes else "")
        folder = str(Path(self.ref.file).parent)
        refused_near_tests = any(TESTS_LOCKED in n or str(Path(n.split(":", 1)[0]).parent) == folder
                                 for n in change.notes)  # a test, or a new file beside them
        self.blamed_test = self.approach == "fix" and refused_near_tests
        self.record(f"{cause} -> {note}")
        say.skip(f"{self.label}: {note}")
        return "fails", self.output

    def tested(self, change, reply, cause):
        """(state, output) with the change; passing or not, a change that breaks a passing test goes."""
        self.p.register(change.created, "test" if self.approach == "review" else "code", f"added for {self.step['id']}")
        self.s.setup()
        state, output = self.v.evaluate(self.step)
        if self.broke_others(change, cause):
            return "fails", self.output + self.broken_note()
        self.record(f"{cause} -> {state}")
        if state == "passes":
            self.passed(reply, cause)
        say.info(f"{self.label}: changed {', '.join(change.files)} -> {state}"
                 + (f"  ({cause})" if cause != "?" else ""), "🔧")
        return state, output

    def passed(self, reply, cause):
        """A review's corrected criterion taken; what solved it noted for the lessons."""
        if self.approach == "review" and reply.criterion():
            self.correct_criterion(reply.criterion())
        if cause != "?":
            self.s.note(self.approach, f"{self.step['behaviour'][:120]} | solved: {cause}")

    def correct_criterion(self, text):
        """The spec says what the corrected test checks: a criterion found wrong (a miscomputed value)
        is reworded with it, so SPEC.md, the source of truth, and the tests stay the same."""
        say.info(f"{self.step['id']} now reads: {first_line(text, 120)} (was: {first_line(self.step['behaviour'], 80)})", "✏️")
        self.step["behaviour"], self.step["title"] = text, text[:80]
        self.s.save()

    def broke_others(self, change, cause):
        if not self.before:
            return False
        broken = sorted(self.before - self.v.passing())
        if not broken:
            return False
        first = self.p.criterion(broken[0])  # how it failed, while the change is still there: the next try sees why
        self.broken_test = first["test"]
        self.broken_output = Output.failure_text(self.v.evaluate(first)[1], OUTPUT_CHARS // 2)
        self.ws.revert(change)
        tests = [self.p.criterion(b)["test"] for b in broken]
        self.keep_passing(tests)
        names = ", ".join(tests[:2])
        self.record(f"{cause} -> broke {names}; reverted")
        say.warn(f"{self.label}: broke {names}; reverted")
        return True

    def keep_passing(self, tests):
        """Tests the next tries must keep passing; one broken before too means the two tests may disagree."""
        kept = self.step.setdefault("keep", [])
        self.conflict = any(t in kept for t in tests)
        kept.extend(t for t in tests if t not in kept)

    def broken_note(self):
        return (f"\n\nYour last change broke {', '.join(self.step['keep'])} (shown below); "
                f"it was reverted. With it, {self.broken_test} failed like this:\n{self.broken_output}")

    def record(self, line):
        self.step.setdefault("history", []).append(f"{self.approach}: {line}")


class BuildFix(Worker):
    """One model call for a project that does not build or load (its check command or its setup
    fails): the error, the files it names and the dependency files. Code, templates and config
    may change; tests may not."""

    def __init__(self, session, output, tried=(), concluding=""):
        super().__init__(session)
        self.output, self.tried, self.concluding = output, tried, concluding
        self.thought = ""  # its thought when it thought to its cap without an answer: the next fix concludes it

    def run(self):
        """What it did, one line (for the next try). The first fix does not think; the next ones do,
        and the one after a fix that thought to its cap does not: it is given that thought to conclude."""
        view = self.prompt()
        try:
            label = "Make it build" + (" (no thinking: concluding the last one's thought)" if self.concluding else "")
            reply = self.s.ask(Prompts.BUILD_SYSTEM, view, label, max_reply=self.s.model.fix_max, unthinking_retry=False,
                               think=False if self.concluding or not self.tried else None)
        except PromptTooLarge as exc:
            say.skip(f"Make it build: the prompt does not fit ({exc})")
            return "the prompt did not fit"
        if self.s.model.overthought:
            self.thought = self.s.model.last_thought
        change = self.ws.apply(reply, self.may_change(view), view.windows)
        return self.said(change, reply.cause() or "?")

    def prompt(self):
        """What failed, the files there are (and a thought to conclude); then the files most likely involved."""
        fixed = [Part("What failed", Output.failure_text(self.output), True, keep="tail"),
                 Part("Project files (all exist; only those shown below may change)",
                      ", ".join(p for p in self.p.files if self.ws.exists(p)), True)]
        fixed += [Part("Your analysis so far (it ran out of room before an answer: conclude it and answer now)",
                       self.concluding, True, keep="tail", place="last")] if self.concluding else []
        tail = [Part("Already tried (it still failed)", "\n".join(f"- {t}" for t in self.tried)),
                Part("Language", self.p.language), *self.s.knowledge_parts("code", "config")]
        return FileView(self.ws, fixed, self.candidates(), tail, output=self.output, hints=self.output)

    def candidates(self):
        """The files the error names, then the dependency and other config files, then the code."""
        files = [p for p in self.s.files_on_disk("code", "template", "config") if p != ENV_FILE]
        configs = [p for p in files if self.p.files[p].get("kind") == "config"]
        return list(dict.fromkeys(Output.mentioned_in(self.output, files) + configs + files))[:FIX_FILES]

    def may_change(self, view):
        def check(path, exists):
            if not exists or path not in view.shown:
                return "only the files shown may change now"
            return TESTS_LOCKED if self.p.files.get(path, {}).get("kind") == "test" else None
        return check

    def said(self, change, cause):
        """What the fix did, said and noted for the lessons; one line, for the next try."""
        for note in change.notes:
            say.skip(f"Skipped {note}")
        did = f"changed {', '.join(change.files)}" if change else "no usable change"
        say.info(f"Make it build: {did}" + (f"  ({cause})" if cause != "?" else ""), "🔧")
        if change and cause != "?":
            self.s.note("build", cause)
        return f"{cause} -> {did}"


class BugReproducer(Phase):
    """tinker fix "bug": a test that fails now because of the bug (the criterion the fix must meet), in
    a new test file the model names as the project's test tool expects. The fix itself is the usual
    verify loop: the code changes until that test passes and every other test still does. A test
    that passes on the current code, breaks the build or is not run by the test command does not
    show the bug: it is asked once more with why, then the cycle stops and asks for a clearer
    report."""
    TITLE = "Reproduce the bug (a test that fails now)"

    def run(self, cycle):
        Designer(self.s).ensure_toolchain()
        if not TestTool(self.s).works() or not Verifier(self.s).make_it_build():
            return False
        note = ""
        for _ in range(2):
            step, state, output = self.write_test(cycle, note)
            if step and state == "fails" and not Toolchain.ran_no_test(output):
                say.done(f"Reproduced: {first_line(step['behaviour'], 140)}")
                with say.section():
                    say.info(f"It fails now: {Output.key_line(output)}", "🐞")
                self.s.save()
                return True
            note = self.why_not(step, state, output)
            say.fail(f"Not reproduced: {note}")
        self.drop(cycle)
        self.s.unreproduced = (f"the bug could not be reproduced with a test, so nothing was changed. Describe what "
                               f"you do, what happens and what should happen instead, then: tinker fix "
                               f"{self.p.run['id']} \"...\"")
        return False

    def write_test(self, cycle, note):
        """(step, state, output): the test the model wrote, evaluated; (None, "missing", why) when
        its reply names no usable test."""
        reply = self.s.ask(Prompts.REPRODUCE_SYSTEM, self.prompt(cycle, note), f"Reproduce bug {cycle['id']}")
        named = re.search(r"^\s*TEST:\s*(.+?)\s*$", reply.text, re.M)
        if not named:
            return None, "missing", 'the reply has no line "TEST: <test file>::<test name>"'
        ref = TestRef(named.group(1).strip("`'\" "))
        problem = self.unusable(cycle, ref)
        if problem:
            return None, "missing", problem
        step = self.step_for(cycle, ref)
        change = self.ws.apply(reply, lambda path, exists: None if path == ref.file else "only the bug's test file")
        for line in change.notes:
            say.skip(f"Skipped {line}")
        expected = re.search(r"^\s*EXPECTED:\s*(.+)$", reply.text, re.M)
        if expected:
            step["behaviour"] = first_line(expected.group(1), 300)
        return (step, *Verifier(self.s).evaluate(step))

    def prompt(self, cycle, note):
        code = self.s.files_on_disk("code", "template")
        named = Output.mentioned_in(asked(cycle), code)
        entry = self.p.entry_files(code)
        tests = self.s.files_on_disk("test")
        return [Part("Bug report", asked(cycle), True),
                Part("The project's test files (the new one goes next to them)", "\n".join(tests) or "none yet", True),
                Part("Note", note, bool(note)),
                Part("Test command", self.s.toolchain.test_one, True),
                Part("Contracts of the code", self.p.contracts([p for p in code if self.p.files[p].get("contract")])),
                *[Part(f"File {p}", self.ws.read(p), atomic=True) for p in dict.fromkeys(named + entry)][:2],
                Part(f"Start of {tests[0]} (how the project's tests are written)",
                     "\n".join(self.ws.read(tests[0]).splitlines()[:30]) if tests else ""),
                Part("External services: adapters and sample data",
                     next((c.get("data", "") for c in self.p.cycles if c["id"] == "base"), "")),
                Part("Project files", self.p.outline()),
                *self.s.knowledge_parts("tests")]

    def unusable(self, cycle, ref):
        """Why a named test cannot be the bug's ("" when it can): it needs a file and a name, in the
        project, in a new file (one the project has would be replaced whole)."""
        if not ref.file or not ref.name:
            return f"TEST: {ref} names no file and test (<test file>::<test name>)"
        try:
            self.ws.resolve(ref.file, for_write=True)
        except ToolError as exc:
            return f"TEST: {ref.file}: {exc}"
        mine = {TestRef(s["test"]).file for s in self.p.cycle_criteria(cycle)}
        if (ref.file in self.p.files or self.ws.exists(ref.file)) and ref.file not in mine:
            return f"TEST: {ref.file} is a file the project has; the bug's test goes in a new file next to its tests"
        return ""

    def step_for(self, cycle, ref):
        """The bug's criterion, with the test the model named (one made earlier for this bug keeps
        its id; its earlier test file goes)."""
        existing = self.p.cycle_criteria(cycle)
        if existing:
            step = existing[0]
            old = TestRef(step["test"]).file
            if old != ref.file:
                self.p.remove_file(self.ws, old)
            step["test"] = str(ref)
        else:
            step = self.p.new_criterion("B", first_line(cycle["request"], 80), cycle["request"], str(ref),
                                        cycle["id"], "bug report", "todo")
            self.p.criteria.append(step)
        self.p.register([ref.file], "test", f"reproduces bug {cycle['id']}", self.p.paths("code"))
        return step

    def why_not(self, step, state, output):
        """What to tell the model when its test does not show the bug."""
        if step is None:
            return output
        test = step["test"]
        match state:
            case "passes":
                return (f"Your test {test} passed on the code as it is, so it does not show the bug. Check what the "
                        f"report says happens, and test exactly that.")
            case "build":
                return f"Your test {test} breaks the build (the test is wrong, not the code): {Output.key_line(output)}"
            case "fails":
                return (f"The test command ran no test for {test}: name the file and the test as the project's test "
                        f"tool expects. {Output.key_line(output)}")
            case _:
                return f"Your reply had no test {test}: {Output.key_line(output)}"

    def drop(self, cycle):
        """A report no test could show: its test and criterion go, and the cycle closes (nothing
        was changed in the code), so the next tinker fix or tinker add can start."""
        for step in self.p.cycle_criteria(cycle):
            self.p.remove_file(self.ws, TestRef(step["test"]).file)
            self.p.criteria.remove(step)
        cycle["phase"], cycle["outcome"] = "done", "not reproduced"
        self.s.save()


class TestTool(Worker):
    """Whether the project's tools work, the way the toolchain runs them: setup runs, the test
    command reaches the tests (before any test exists: it runs and finds none), and the linter
    runs (what it finds is for the tidy phase). A tooling problem gets a toolchain fix (config files
    and commands only; a fix learned earlier first), at most twice: no code change can fix it. A
    toolchain that works is remembered for the language."""
    __test__ = False  # not a pytest test class
    STAGE_TEXT = {"setup": "setup fails", "tests": "the tests cannot run", "lint": "the linter cannot run"}
    MISSING_TOOL_RE = re.compile(r"command not found|No module named|not recognized as|cannot find|not installed|"
                                 r"no such file or directory|unknown command", re.I)

    def works(self):
        problem, repair, tried = "", None, set()
        for attempt in range(3):
            stage, output = self.check(attempt)
            if stage == "ok":
                self.remember_what_worked(repair)
                return True
            if repair and repair[0] == "known":
                self.s.knowledge.fix_result(repair[1], worked=False)
            problem = Output.key_line(output)
            say.fail(f"{self.STAGE_TEXT[stage]}: {problem}")
            repair = ToolchainRepair(self.s).run(output, tried) if attempt < 2 else None
            if repair is None:
                break
        self.s.tool_problem = f"the project's tools do not work: {problem}"
        return False

    def check(self, attempt):
        """("ok" | "setup" | "tests" | "lint", output): setup runs (what needs the network once it
        is there); the test command reaches the tests written so far (with none yet, it runs and
        finds none) and they can import the app's own code; the linter runs."""
        ok, output = self.s.setup(force=attempt > 0)
        if not ok:
            return "setup", output
        self.s.wait_for_network()
        self.s.all_tests_run = None if attempt else self.s.all_tests_run
        code, output = self.s.run_all_tests()
        present = [r for r in self.p.refs() if r.present(self.ws)]
        problem = self.test_tool_problem(code, output, present)
        if problem:
            return "tests", problem
        if not present:
            say.done(f"The test tool runs (no tests yet): $ {self.s.toolchain.test_all}")
        return self.linter_runs()

    def test_tool_problem(self, code, output, present):
        """The output, with what it means, when the test command does not reach the tests, the tests cannot
        see the app's code, or the one-test command does not pick a test by its name; else ""."""
        reached = self.reaches_code(output) or (not Toolchain.reached_no_test(code, output, present) if present
                                                else self.ran_empty(code, output))
        if not reached:
            say.fail(f"$ {self.s.toolchain.test_all}  [{'ran no test' if present else 'did not run'}: a tooling problem]")
            return output
        unseen = self.unseen_code(output) if present and code else ""
        if unseen:
            return unseen
        return self.one_test_unpicked(present[0]) if present and present[0].name in output else ""

    def reaches_code(self, output):
        """Whether the test command got as far as the app's own code: it names a code file of the
        plan, one not written yet or one that does not compile (a compiled language builds all of
        it before any test runs). The tool works; what fails is the code, which the build fixes."""
        return any(path in output for path in self.p.paths("code"))

    @staticmethod
    def ran_empty(code, output):
        """Whether a test command run before any test exists ran and found none: it says so, or it
        says nothing that looks like an error (quiet settings may make it say nothing at all, and
        many test tools exit non-zero when they find no test). One that is not there or does not
        finish did not run."""
        if code == 0:
            return True
        return code not in (None, 126, 127) and (Toolchain.ran_no_test(output)
                                                 or not Output.ERROR_LINE_RE.search(output))

    def unseen_code(self, output):
        """The output, with what it means, when the tests cannot import the project's own code
        that the check command loads (a test tool not seeing the project, which no code change
        fixes); else "". Once a test has passed, the tool sees the code: a module not found then
        is a test's own doing (one that runs the app from elsewhere)."""
        if any(s.get("status") == "green" for s in self.p.criteria):
            return ""
        missed = Output.misses_own_code(output, self.s.files_on_disk("code"))
        if not missed or not self.s.toolchain.run_check(self.ws)[0]:
            return ""
        say.fail(f"$ {self.s.toolchain.test_all}  [the tests cannot import the project's own {missed}, which loads "
                 f"with the check command: a tooling problem]")
        return output + (f"\n(The tests cannot import the project's own module {missed}, which loads fine with the "
                         f"check command: {Output.OWN_CODE_UNSEEN} when it runs from the project folder.)")

    def one_test_unpicked(self, real):
        """"" when the one-test command runs a test by its name, else why not: a command that answers
        a made-up test name as it answers a real one does not pick tests, and every test it runs
        would look failing. Checked once per command, when the whole suite has run that test."""
        toolchain = self.s.toolchain
        if getattr(self.s, "one_test_checked", None) == toolchain.test_one:
            return ""
        made_up = TestRef(f"{real.file}::tinker_made_up_test")
        _, real_output = toolchain.run_test(self.ws, real)
        _, made_up_output = toolchain.run_test(self.ws, made_up)
        if self.unnamed(real_output, real.name) != self.unnamed(made_up_output, made_up.name):
            self.s.one_test_checked = toolchain.test_one
            return ""
        say.fail(f"$ {real.command(toolchain.test_one)}  [answers a made-up test name the same way: a tooling problem]")
        return (f"{real_output}\n(The one-test command `{toolchain.test_one}` answers a made-up test name the same "
                f"way as the real test {real.name}: it does not run a test by its name, as the test tool needs it.)")

    @staticmethod
    def unnamed(output, name):
        """An output without the test's name and its numbers (timings, counts)."""
        return re.sub(r"[\d.]+", "", output.replace(name, "<name>"))

    def linter_runs(self):
        """("ok", "") when the linter runs (what it finds does not matter here), else ("lint", why):
        a linter that is not installed or not found is a tooling problem, like a missing test tool;
        one that reaches the app's code and finds it does not compile runs (the build fixes that)."""
        if not self.s.toolchain.lint:
            return "ok", ""
        code, output = self.ws.run(self.s.toolchain.shell(self.s.toolchain.lint), quiet=True)
        if code in (126, 127) or (code != 0 and self.MISSING_TOOL_RE.search(output) and not self.reaches_code(output)):
            say.fail(f"$ {self.s.toolchain.lint}  [the linter does not run: a tooling problem]")
            return "lint", f"$ {self.s.toolchain.lint}\nthe linter does not run (exit code {code}):\n{output}"
        return "ok", ""

    def remember_what_worked(self, repair):
        """The repair that made the tools work, for the next projects (the toolchain itself is kept
        once the project is done: only then has every command of it worked)."""
        knowledge = self.s.knowledge
        if repair and repair[0] == "known":
            knowledge.fix_result(repair[1], worked=True)
        elif repair:
            knowledge.remember_fix(repair[1])
            self.s.note("toolchain", repair[1]["cause"])


class ToolchainRepair(Worker):
    """Makes the project's tools work (setup, the test command): a fix learned on an earlier
    project for exactly this failure first (no model call), else one model call that may change
    only config files and the commands."""

    def run(self, output, tried):
        """Returns ("known" or "model", the fix), or None when nothing changed."""
        failure = Output.tool_failure(output, self.ws.root)
        known = self.s.knowledge.known_fix(failure, tried)
        if known:
            tried.add(failure)
            say.info(f"Applying a toolchain fix learned on an earlier project ({known['cause']})", "♻️")
            self.replay(known["replay"])
            return "known", known
        fix = self.ask_model(output, failure)
        return ("model", fix) if fix else None

    def replay(self, replay):
        """A learned fix: its commands, and the config files it created (an existing file is kept;
        a file this project plans as code or tests is its own to write, never another project's)."""
        if replay.get("toolchain"):
            self.p.toolchain = self.s.toolchain.merged(replay["toolchain"])
        for path, content in (replay.get("files") or {}).items():
            if not self.ws.exists(path) and self.p.files.get(path, {}).get("kind") in (None, "config"):
                self.ws.write(path, content)
                self.p.register([path], "config", "added by a learned toolchain fix")
        self.s.save()

    def ask_model(self, output, failure):
        """One call that may change only config files and the commands. Returns the fix (for
        knowledge: replayable when it only changed commands or created files), or None."""
        configs = [p for p in self.s.files_on_disk("config") if p != ENV_FILE]
        before = self.s.toolchain.to_dict()
        reply = self.ask_fix(output, failure, configs, before)
        change = self.apply_fix(reply, configs)
        commands = {k: v for k, v in self.s.toolchain.to_dict().items() if v != before.get(k)}
        changed = (["toolchain"] if commands else []) + change.files
        say.info(f"Toolchain fix: {', '.join(changed) or 'no usable change'}  ({reply.cause() or '?'})", "🔧")
        self.s.save()
        if not changed:
            return None
        return self.learned(failure, reply, commands, change)

    def ask_fix(self, output, failure, configs, before):
        similar = self.s.knowledge.similar_fix(failure)
        return self.s.ask(Prompts.TOOLCHAIN_FIX_SYSTEM, [
            Part("Output", Output.failure_text(output), True, keep="tail"),
            Part("Toolchain", json.dumps(before), True),
            Part("Language", self.p.language),
            Part("A similar failure on an earlier project was fixed by", similar["cause"] if similar else ""),
            *[Part(f"File {p}", self.ws.read(p), atomic=True) for p in configs],
            *self.s.knowledge_parts("toolchain", "config"),
        ], "Fix the toolchain", max_reply=self.s.model.fix_max, unthinking_retry=False)

    def apply_fix(self, reply, configs):
        """The commands the reply gives, and its config files (new ones, or those shown): the change."""
        try:
            given = reply.toolchain_line()
        except ValueError:
            given = None
        if given:
            self.p.toolchain = self.s.toolchain.merged(given)
        change = self.ws.apply(reply, lambda p, exists: None if p in configs or not exists
                               else "only config files change now")
        self.p.register(change.created, "config", "added by the toolchain fix")
        for note in change.notes:
            say.skip(f"Skipped {note}")
        return change

    def learned(self, failure, reply, commands, change):
        """The fix as knowledge keeps it: replayable when it only changed commands or created files (code it
        made to get going belongs to this project: only its config files are kept)."""
        replayable = set(change.files) == set(change.created)
        files = {p: self.ws.read(p) for p in change.created if self.p.files.get(p, {}).get("kind") == "config"}
        return {"failure": failure, "cause": reply.cause() or "?",
                "replay": {"toolchain": commands, "files": files} if replayable else None}
