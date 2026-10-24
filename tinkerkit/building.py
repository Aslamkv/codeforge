lazy import itertools
lazy import json
import re
lazy import time
from collections import namedtuple
lazy from pathlib import Path

from .messages import human_duration, say
lazy from .planning import Designer, SpecWriter, asked
from .session import Phase, Worker
lazy from .prompts import Part, PromptTooLarge, Prompts, Reply
lazy from .settings import (
    BROKEN_BUILD_FIXES, ENV_EXAMPLE, ENV_FILE, FAILURE_OUTPUT_CHARS, FILES_SHOWN_PER_FIX,
    MAX_CALLS_PER_CRITERION_PASS, MAX_FILE_LINES, STALLED_REVIEW_TRIES, TESTS_LOCKED_REASON, TESTS_PER_CALL,
    TEST_FILE_MAX_TOKENS, NotAPytestClass, first_line,
)
lazy from .workspace import FileView, Output, TestOutcome, TestRef, ToolError, Toolchain


WrittenTest = namedtuple("WrittenTest", "step state output")
SetupOutcome = namedtuple("SetupOutcome", "stage output")


class TestToolSetup(Phase, NotAPytestClass):
    TITLE = "Setup (the test tool, before any test is written)"
    BUILDS = True

    def run(self, cycle):
        Designer(self.session).ensure_toolchain()
        CodeWriter(self.session).write_configs_first(cycle)
        self.session.setup()
        if self.session.waiting_for_network:
            say.info("The test tool is checked once the network is back; writing the tests and the code meanwhile",
                     "📡")
            return True
        return TestTool(self.session).works()


class TestWriter(Phase, NotAPytestClass):
    TITLE = "Spec tests (one for every spec point, before any code)"
    BUILDS = True

    def run(self, cycle, only=None):
        steps = [step for step in (only or self.project.cycle_criteria(cycle))
                 if not TestRef(step["test"]).present(self.workspace)]
        by_file = {}
        for step in steps:
            by_file.setdefault(TestRef(step["test"]).file, []).append(step)
        for path, group in by_file.items():
            for batch in itertools.batched(group, TESTS_PER_CALL):
                self.write_batch(cycle, path, list(batch))
        self.session.save()

    def write_batch(self, cycle, path, batch):
        note = ""
        for _ in (1, 2):
            todo = [step for step in batch if not TestRef(step["test"]).present(self.workspace)]
            if not todo:
                return
            current = self.workspace.read(path)
            try:
                with say.working_on(f"Writing {len(todo)} test(s) in {path}: {', '.join(step['id'] for step in todo)}"):
                    reply = self.session.ask(Prompts.SPEC_TESTS_SYSTEM, self.prompt(cycle, path, todo, current, note),
                                             f"Tests {'next to ' if current.strip() else ''}"
                                             f"{path} ({len(todo)})",
                                             max_reply=TEST_FILE_MAX_TOKENS)
            except PromptTooLarge as exc:
                say.skip(f"Tests for {path} skipped: {exc}; verify writes them")
                return
            note = self.take(reply, path, todo, current)

    def prompt(self, cycle, path, todo, current, note):
        where = (f"a NEW test file next to {path} (do not change {path}; name it by the language's test "
                 f"conventions)" if current.strip() else path)
        uses = self.project.files.get(path, {}).get("uses") or self.project.paths("code", "template")
        parts = [Part(f"Tests to write in {where} (name: criterion)",
                      "\n".join(f"- {TestRef(step['test']).name}: {step['behaviour']}" for step in todo), True),
                 Part("Test command", self.session.toolchain.test_one, True, place="first"),
                 Part("Note", note, place="last"),
                 Part("External services: adapters and sample data (tests pass the sample data)", cycle.get("data", ""),
                      place="first")]
        configs = [config for config in self.session.files_on_disk("config") if config not in (ENV_FILE, ENV_EXAMPLE)]
        return parts + [
                Part("The project's config files (the tests use the names they give: the package, the library, "
                     "the program)", "\n\n".join(f"{config}:\n{self.workspace.read(config)}" for config in configs),
                     atomic=True,
                     place="first"),
                Part("Contracts of the files these tests use", self.project.contracts(uses)),
                Part(f"Start of {path} (its imports and shared setup; it stays as it is; a fake of the app's own "
                     f"code in it is not to be used)",
                     "\n".join(current.splitlines()[:30])),
                Part("Project files", self.project.outline(), place="first"),
                *self.session.knowledge_parts("tests")]

    def take(self, reply, path, todo, current):
        change = self.workspace.apply(reply.as_file(self.new_file_next_to(path) if current.strip() else path),
                                      self.may_write(path), empty_ok=False)
        self.keep_existing_tests(change)
        self.correct_criteria(todo, reply.corrected_criteria())
        self.follow_new_files(path, todo, change)
        return self.report_and_note(path, todo, change)

    def new_file_next_to(self, path):
        stem, suffix = Path(path).stem, Path(path).suffix
        number = 2
        while self.workspace.exists(str(Path(path).with_name(f"{stem}_{number}{suffix}"))):
            number += 1
        return str(Path(path).with_name(f"{stem}_{number}{suffix}"))

    @staticmethod
    def may_write(path):
        folder = str(Path(path).parent)

        def check(written, exists):
            next_to_it = str(Path(written).parent) == folder
            if next_to_it:
                return None
            return f"only test files next to {path} can be written now"
        return check

    def keep_existing_tests(self, change):
        for path, old in list(change.backups.items()):
            had = [name for name in self.project.test_names(path) if old and TestRef.name_at(old, name) >= 0]
            lost = [name for name in had if TestRef.name_at(self.workspace.read(path), name) < 0]
            if lost:
                self.workspace.restore({path: change.backups.pop(path)})
                change.notes.append(f"{path} lost its tests {', '.join(lost)}; write the new tests in a new file")

    def correct_criteria(self, todo, corrected):
        for step in todo:
            text = corrected.get(TestRef(step["test"]).name)
            if text and text != step["behaviour"]:
                say.info(f"{step['id']} now reads: {first_line(text, 120)} (was: {first_line(step['behaviour'], 80)})", "✏️")
                step["behaviour"], step["title"] = text, text[:80]

    def follow_new_files(self, path, todo, change):
        for step in todo:
            ref = TestRef(step["test"])
            home = next((new_file for new_file in change.created if TestRef.name_at(self.workspace.read(new_file),
                                                                                    ref.name) >= 0), None)
            if home and not ref.present(self.workspace):
                step["test"] = f"{home}::{ref.name}"
        self.project.register(change.created, "test", f"tests (with {path})",
                              self.project.files.get(path, {}).get("uses", []))

    def report_and_note(self, path, todo, change):
        for note in change.notes:
            say.skip(f"Skipped {note}")
        missing = [TestRef(step["test"]).name for step in todo if not TestRef(step["test"]).present(self.workspace)]
        homes = sorted({TestRef(step["test"]).file for step in todo if TestRef(step["test"]).present(self.workspace)})
        for step in todo:
            if TestRef(step["test"]).name in missing:
                step["write_failures"] = step.get("write_failures", 0) + 1
                step["last_error"] = ("its test could not be written: " + "; ".join(change.notes))[:400]
        say.done(f"{', '.join(homes) or path}: wrote {len(todo) - len(missing)}/{len(todo)} tests"
                 + (f"; missing {', '.join(missing)}" if missing else ""))
        return (f"These tests were not written: {', '.join(missing)}. " + "; ".join(change.notes))[:600] if missing else ""


class CodeWriter(Phase):
    TITLE = "Code (just enough to pass the tests)"
    BUILDS = True

    def run(self, cycle):
        self.write_all(cycle)
        ok, output = self.session.setup()
        if not ok:
            say.fail(f"Setup fails: {Output.key_line(output)} (verify fixes the toolchain first)")
        self.check_wiring(cycle)

    def write_all(self, cycle):
        self.write_configs_first(cycle)
        pending = self.pending(cycle, lambda kind: kind not in ("test", "docs"))
        for done, path in enumerate(pending, 1):
            role = first_line(self.project.files.get(path, {}).get("role", ""), 60)
            with say.working_on(f"Writing {path} ({done} of {len(pending)})" + (f": {role}" if role else "")):
                self.write_file(cycle, path)
            cycle["code_done"].append(path)
            self.session.save()
            say.tally(f"{done}/{len(pending)} files written")

    def write_configs_first(self, cycle):
        configs = [path for path in self.pending(cycle, lambda kind: kind == "config")
                   if not self.workspace.exists(path)]
        if configs:
            self.write_configs(cycle, configs)
        cycle["code_done"].extend(configs)
        self.session.save()

    def pending(self, cycle, wanted):
        done = cycle.setdefault("code_done", [])
        return [path for path in cycle["files"]
                if path not in done and path != ENV_FILE and wanted(self.project.kind_of(path))]

    def write_configs(self, cycle, paths):
        listing = "\n".join(f"- {config}: {self.project.files[config].get('role', '')}" + "".join(
            f"\n    - {line}" for line in self.project.files[config].get("contract", [])) for config in paths)
        reply = self.session.ask(Prompts.CONFIG_SYSTEM, [
            Part("Files to write", listing, True),
            Part("Language and toolchain",
                 f"{self.project.language}\n{json.dumps(self.session.toolchain.to_dict())}", True),
            Part("Constraints", "\n".join(f"- {constraint}" for constraint in cycle.get("constraints", []))),
            Part("Project files", self.project.outline()),
            *self.session.knowledge_parts("config", "toolchain"),
        ], f"Write {len(paths)} config files", think=False,
           done_when=Reply.wrote(paths))
        change = self.workspace.apply(reply,
                                      lambda config, exists: None if config in paths
                                      else "only the config files listed now")
        missing = [config for config in paths if config not in change.backups]
        say.done(f"Wrote {', '.join(change.files) or 'none'}" + (f"; missing {', '.join(missing)}" if missing else ""))
        for path in missing:
            self.write_file(cycle, path)

    def write_file(self, cycle, path):
        info, exists = self.project.files.get(path, {}), self.workspace.exists(path)
        criteria = self.criteria_for(path, cycle)
        if not exists and info.get("marker") and not criteria:
            return self.write_marker(path)
        note = ""
        for attempt in (1, 2):
            view = self.file_view(cycle, path, info, criteria, note, exists)
            try:
                reply = self.ask_for_file(path, view, exists)
            except PromptTooLarge as exc:
                say.skip(f"{path}: skipped, the prompt does not fit ({exc}); verify works on it")
                return
            change = self.applied(reply, path, exists, view)
            note = self.unusable(path, change, attempt == 1 and info.get("kind") == "code")
            if not note:
                self.say_written(path, change, exists)
                return

    def file_view(self, cycle, path, info, criteria, note, exists):
        return FileView(self.workspace, self.fixed_parts(path, info, criteria, note), [path] if exists else [],
                        self.context_parts(cycle, info, criteria),
                        hints="\n".join(criterion["behaviour"] for criterion in criteria), windows=1)

    def ask_for_file(self, path, view, exists):
        return self.session.ask(Prompts.CHANGE_SYSTEM if exists else Prompts.CODE_SYSTEM, view,
                                f"{'Change' if exists else 'Write'} {path}",
                                done_when=Reply.files_complete if exists else Reply.wrote([path]))

    def applied(self, reply, path, exists, view):
        change = self.workspace.apply(self.addressed_to(reply.as_file(path), path, exists), self.may_write(path),
                                      view.windows)
        for text in change.notes:
            say.skip(f"Skipped {text}")
        return change

    def write_marker(self, path):
        self.workspace.write(path, "")
        say.done(f"Wrote {path} (empty: no contract or criterion asks anything of it)")

    def criteria_for(self, path, cycle):
        steps = self.project.cycle_criteria(cycle)
        own = self.project.criteria_of(path, steps)
        if self.project.entry_files([path]):
            own += [step for step in steps if step not in own and SpecWriter.runs_program(step["behaviour"], path,
                                                                                          self.session.toolchain.run)]
        return own

    def fixed_parts(self, path, info, criteria, note):
        renderers = [renderer for renderer, entry in self.project.files.items() if path in entry.get("uses", [])
                     and entry.get("kind") == "code"]
        template = info.get("kind") == "template"
        parts = [Part("Language", self.project.language, True, place="first"),
                 Part("File", f"{path} ({info.get('kind', 'code')})\nRole: {info.get('role', '')}", True),
                 Part("Its contract", self.project.contracts([path]), bool(info.get("contract"))),
                 Part("Contracts of the files that render it (what it is given and must show)",
                      self.project.contracts(renderers) if template else "", template and bool(renderers)),
                 Part("Criteria it serves", "\n".join(f"- {criterion['behaviour']}" for criterion in criteria)
                      or "none directly: its contract says what it does", True),
                 Part("How a user starts it (it is the entry point)", self.session.toolchain.run, True)
                 if path == self.project.spec.get("entry") and self.session.toolchain.run
                 else Part("How a user starts it", "")]
        return parts + ([Part("Note", note, True)] if note else [])

    def context_parts(self, cycle, info, criteria):
        deps = self.project.dependencies(lambda path: self.workspace.read(path) if self.workspace.exists(path) else "")
        parts = [Part("Contracts of the files it uses", self.project.contracts(info.get("uses", []))),
                 Part(Prompts.DEPENDENCIES_TITLE, deps if info.get("kind") == "code" else "", atomic=True,
                      place="first"),
                 Part("External services: adapters and sample data", cycle.get("data", ""), place="first")]
        if info.get("kind") in ("code", "template"):
            names = [TestRef(criterion["test"]).name for criterion in self.project.criteria]
            parts += [Part(f"Test {number} ({criterion['test']})",
                           TestRef(criterion["test"]).excerpt(self.workspace, names), atomic=True)
                      for number, criterion in enumerate(criteria[:5], 1)]
        settings = (f"read from {ENV_FILE} (defaults in {ENV_EXAMPLE}); environment variables override it. Read it "
                    f"with a listed dependency, or with a few lines of standard-library code when none reads it")
        has_settings = info.get("kind") == "code" and ENV_EXAMPLE in self.project.files
        return parts + [Part("Settings", settings if has_settings else "", place="first"),
                        Part("Other files", self.project.outline(), place="first"),
                        Part("Not wanted (non-goals)",
                             "\n".join(f"- {non_goal}" for non_goal in cycle.get("non_goals", [])),
                             place="first"),
                        *self.session.knowledge_parts("config" if info.get("kind") == "config" else "code")]

    @staticmethod
    def addressed_to(reply, path, exists):
        blocks = reply.files()
        if not exists and len(blocks) == 1 and blocks[0][0] != path:
            return Reply(f"<<<FILE {path}>>>\n{blocks[0][1]}\n<<<END>>>")
        return reply

    def may_write(self, path):
        def check(written, exists):
            if written == path or (not exists and written not in self.project.files):
                return None
            return f"only {path} (and new modules split off it) can be written now"
        return check

    def unusable(self, path, change, first_code):
        if path not in change.backups:
            return ("Your reply could not be used: " + "; ".join(change.notes))[:600] if change.notes else \
                f"Your reply had no usable <<<FILE {path}>>> block."
        if first_code and self.too_long(path):
            note = (f"Your {path} had {self.workspace.lines(path)} lines; keep it under {MAX_FILE_LINES} by moving a "
                    f"part into a new small module (give that file too).")
            self.workspace.revert(change)
            return note
        return ""

    def too_long(self, path):
        lines = self.workspace.lines(path)
        if lines > MAX_FILE_LINES * 2:
            say.warn(f"{path}: {lines} lines; asking for smaller modules")
            return True
        return False

    def say_written(self, path, change, exists):
        self.project.register([created for created in change.created if created != path], "code",
                              f"module split from {path}")
        others = [other for other in change.files if other != path]
        say.done(f"{'Changed' if exists else 'Wrote'} {path} ({self.workspace.lines(path)} lines)"
                 + (f"; also {', '.join(others)}" if others else ""))

    def check_wiring(self, cycle):
        for path in cycle["files"]:
            if self.project.kind_of(path) != "code" or not self.workspace.exists(path):
                continue
            problems = self.wiring_problems(path)
            if problems:
                self.rewire(path, problems)

    def wiring_problems(self, path):
        text = self.workspace.read(path)
        return [f"it must use {used} (refer to it by name), as its contract says"
                for used in self.project.files[path].get("uses", [])
                if self.project.kind_of(used) in ("code", "template")
                and Path(used).name not in text and Path(used).stem not in text]

    def rewire(self, path, problems):
        say.warn(f"{path} is not wired as designed: {'; '.join(problems)}")
        info = self.project.files[path]
        view = FileView(self.workspace, [Part("Problems", "\n".join(problems), True),
                                         Part(f"Contract of {path}", self.project.contracts([path]), True)], [path],
                        [Part("Contracts of the files it uses", self.project.contracts(info.get("uses", [])))],
                        hints=" ".join(info.get("uses", [])), windows=1)
        reply = self.session.ask(Prompts.CHANGE_SYSTEM, view, f"Wire {path}")
        change = self.workspace.apply(reply, lambda target, exists: None if target == path
                                      else f"only {path} changes now",
                                      view.windows)
        if change:
            say.done(f"Rewired {path}")
        else:
            say.skip(f"{path}: no usable change")


class Verifier(Phase):
    TITLE = "Verify (until every test passes)"
    BUILDS = True
    RANK = {"missing": -1, "build": 0, "fails": 1, "passes": 2}

    RESTORE_POINT = "verify"
    FLAKY_RERUNS = 3
    FLAKY_NOTE = ("This test's result changes from one run to the next with no change to the code: the code "
                  "is not deterministic. Look for what can differ between runs: the order of an unordered "
                  "collection (a set, a hash map), randomness, the clock, or state shared between tests.")

    def __init__(self, session):
        super().__init__(session)
        self.parked_by_failure = {}

    def run(self, cycle):
        self.recover_interrupted()
        Designer(self.session).ensure_toolchain()
        if not TestTool(self.session).works() or not self.make_it_build():
            return False
        self.mark_passing()
        green_at_start = self.project.counts()[0]
        self.work_through([criterion for criterion in self.project.criteria
                           if criterion["status"] in ("todo", "active")], retry=False)
        self.retry_parked(green_at_start)
        green, _, total = self.project.counts()
        return green == total

    def recover_interrupted(self):
        pending = self.project.store.get("restore", self.RESTORE_POINT)
        if not isinstance(pending, dict) or not isinstance(pending.get("files"), dict):
            return
        changed = [path for path, text in pending["files"].items() if self.workspace.read(path) != (text or "")]
        if changed:
            self.roll_back(pending["files"])
            say.info(f"The last run stopped while {pending.get('criterion', 'a criterion')} was being worked on: "
                     f"{', '.join(changed)} restored to how they were before it", "♻️")
        self.project.store.delete("restore", self.RESTORE_POINT)

    def make_it_build(self):
        tried, thought = [], ""
        for attempt in itertools.count():
            ok, output = self.session.setup()
            if ok:
                ok, output = self.session.toolchain.run_check(self.workspace)
            if ok:
                say.done("The project builds now" if attempt else "The project builds")
                return True
            say.fail(f"The project does not build: {Output.key_line(output)}")
            if attempt >= BROKEN_BUILD_FIXES and not thought:
                break
            fix = BuildFix(self.session, output, tried, thought)
            tried.append(fix.run())
            thought = fix.unfinished_thought
            self.session.last_all_tests_run = None
        self.session.build_problem = f"the project does not build after {len(tried)} fixes: {Output.key_line(output)}"
        return False

    def mark_passing(self):
        green = self.passing()
        for criterion in self.project.criteria:
            criterion["status"] = ("green" if criterion["id"] in green
                                   else "blocked" if criterion["status"] == "blocked" else "todo")
        self.session.save()

    def passing(self):
        started = time.perf_counter()
        code, _ = self.session.run_all_tests()
        tests = [criterion for criterion in self.project.criteria if TestRef(criterion["test"]).present(self.workspace)]
        if code == 0:
            result = {criterion["id"] for criterion in tests}
        elif not self.project_builds():
            return set()
        else:
            result = {criterion["id"] for criterion in tests if self.evaluate(criterion, checked=True)[0] == "passes"}
        took = human_duration(time.perf_counter() - started)
        say.info(f"Tests: {len(result)}/{len(self.project.criteria)} criteria pass ({took})", "🧪")
        return result

    def project_builds(self):
        return self.session.toolchain.run_check(self.workspace)[0]

    def work_through(self, steps, retry):
        for step in steps:
            if step["status"] == "green":
                continue
            passed = self.work(step, retry)
            step["status"] = "green" if passed else "blocked"
            if passed:
                self.mark_others_passing()
            self.session.save()
            self.say_worked(step, passed)

    def say_worked(self, step, passed):
        green, _, total = self.project.counts()
        if passed:
            say.done(f"{step['id']} passes ({green}/{total} pass)")
            say.found(f"Works now: {step['title']}")
        else:
            say.info(f"{step['id']} is parked ({green}/{total} pass)", "⏸️")

    def retry_parked(self, green_at_start):
        parked = [criterion for criterion in self.project.criteria if criterion["status"] == "blocked"]
        earlier = [criterion for criterion in parked if criterion["id"] in self.session.parked_at_start]
        if parked and self.project.counts()[0] > green_at_start:
            say.step(f"Retrying {len(parked)} parked criteria, now that others pass", "🔁")
            self.parked_by_failure = {}
            self.work_through(parked, retry=True)
        elif earlier:
            say.step(f"Retrying {len(earlier)} criteria parked by an earlier run, once (the project, its plan or "
                     f"Tinker may have changed since)", "🔁")
            self.session.parked_at_start -= {criterion["id"] for criterion in earlier}
            self.parked_by_failure = {}
            self.work_through(earlier, retry=True)
        elif parked:
            say.skip(f"{len(parked)} criteria parked and nothing else passes yet: a retry would see the same project")

    def work(self, step, retry):
        green, _, total = self.project.counts()
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
        if same in self.parked_by_failure:
            step["last_error"] = f"same failure as {self.parked_by_failure[same]}"
            say.skip(f"Fails the same way as {self.parked_by_failure[same]}, which nothing fixed; parked without new attempts")
            return False
        if self.climb_safely(step, state, output, retry):
            return True
        self.parked_by_failure.setdefault(same, step["id"])
        say.fail(f"Nothing helped; {step['id']} is back to how it was")
        return False

    def first_look(self, step):
        state, output = self.evaluate(step)
        if state == "missing":
            state, output = self.write_missing_test(step)
        if state == "fails" and self.flaky(step):
            say.warn("Its test passes and fails on reruns: the code is not deterministic")
            output += f"\n\n{self.FLAKY_NOTE}"
        return TestOutcome(state, output)

    def evaluate(self, step, checked=False):
        ref = TestRef(step["test"])
        if not ref.present(self.workspace):
            return TestOutcome("missing", f"the test {ref} does not exist")
        if not checked:
            ok, output = self.session.toolchain.run_check(self.workspace)
            if not ok:
                return TestOutcome("build", output)
        return self.session.toolchain.run_test(self.workspace, ref)

    def write_missing_test(self, step):
        if step.get("write_failures", 0) >= 2:
            say.fail(f"Its test could not be written in {step['write_failures']} tries; parked")
            return "missing", step.get("last_error") or f"the test {step['test']} could not be written"
        say.step("Its test is missing; writing it", "✍️")
        TestWriter(self.session).run(self.project.cycle(step.get("cycle") or "base") or {}, only=[step])
        state, output = self.evaluate(step)
        if state == "missing":
            step["last_error"] = output
        return state, output

    def flaky(self, step):
        ref = TestRef(step["test"])
        return any(self.session.toolchain.run_test(self.workspace, ref)[0] == "passes" for _ in range(self.FLAKY_RERUNS))

    @staticmethod
    def failure_key(step, output):
        ref = TestRef(step["test"])
        matches_nothing = "\0"
        return Output.signature(output.replace(str(ref), "#").replace(ref.name or matches_nothing, "#").replace(ref.file, "#"))

    def climb_safely(self, step, state, output, retry):
        files_with_its_test = self.workspace.snapshot(self.project.files)
        self.project.store.put("restore", self.RESTORE_POINT, {"criterion": step["id"], "files": files_with_its_test})
        try:
            if self.climb(step, state, output, retry):
                return True
            self.roll_back(files_with_its_test)
            return False
        finally:
            self.project.store.delete("restore", self.RESTORE_POINT)

    def climb(self, step, state, output, retry):
        before = {criterion["id"] for criterion in self.project.criteria if criterion["status"] == "green"}
        climb, total = Climb(state, output), 0
        for approach, patience in self.ladder(step, output, retry):
            stall, conclusion_due = 0, False
            while (stall < patience or conclusion_due) and total < MAX_CALLS_PER_CRITERION_PASS:
                if approach == "fix" and self.test_unloadable(step, output):
                    say.step("The test file no longer loads: reviewing the test now", "🔍")
                    break
                total += 1
                attempt = FixAttempt(self.session, self, step, approach, output, before)
                state, output = attempt.run()
                if state == "passes":
                    return True
                stall = 0 if climb.progressed(state, output) else stall + 1
                conclusion_due = attempt.hit_thinking_cap
                step["last_error"] = Output.key_line(output)
                why = self.to_review(attempt, approach)
                if why:
                    say.step(f"{why}: reviewing against the plan now", "🔍")
                    break
        return False

    def ladder(self, step, output, retry):
        if self.test_unloadable(step, output):
            say.step(f"The test file {TestRef(step['test']).file} itself does not load: reviewing the test now", "🔍")
            return (("review", STALLED_REVIEW_TRIES),)
        if sum(" broke " in line for line in step.get("history", [])) >= 2:
            say.step("Earlier fixes kept breaking other tests (the tests may disagree): reviewing against the plan now",
                     "🔍")
            return (("review", STALLED_REVIEW_TRIES),)
        return (("fix", 1), ("review", 1)) if retry else (("fix", self.session.max_stall), ("review", STALLED_REVIEW_TRIES))

    @staticmethod
    def to_review(attempt, approach):
        if attempt.only_wanted_test_change:
            return "the fix points at the test, not the code"
        if attempt.tests_may_disagree and approach == "fix":
            return "fixes keep breaking the same other test (the tests may disagree)"
        return ""

    @staticmethod
    def test_unloadable(step, output):
        ref = TestRef(step["test"])
        return Output.unloadable(output, ref.file)

    def roll_back(self, snapshot):
        created_since = [path for path in self.project.files if path not in snapshot]
        for path in created_since:
            (self.workspace.root / path).unlink(missing_ok=True)
            del self.project.files[path]
        self.workspace.restore(snapshot)

    def mark_others_passing(self):
        for other in self.project.criteria:
            if other["status"] != "green" and self.evaluate(other)[0] == "passes":
                other["status"] = "green"


class Climb:
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
    APPROACH_TEXT = {"fix": "fix the code", "review": "review the test and the code against the plan"}

    def __init__(self, session, verifier, step, approach, output, before):
        super().__init__(session)
        self.v = verifier
        self.step, self.approach, self.output, self.before = step, approach, output, before
        self.ref = TestRef(step["test"])
        self.label = f"{self.APPROACH_TEXT[approach].capitalize()} {step['id']}"
        self.only_wanted_test_change = False
        self.tests_may_disagree = False
        self.hit_thinking_cap = False
        self.broken_test, self.broken_output = "", ""

    def run(self):
        view = self.prompt()
        try:
            reply = self.ask(view)
        except PromptTooLarge as exc:
            return TestOutcome("fails", self.output + f"\n(prompt too large: {exc})")
        if self.approach == "fix" and reply.test_wrong():
            return self.test_blamed(reply.test_wrong())
        change = self.workspace.apply(reply, self.may_change(view), view.windows)
        self.remember_wanted_files(reply, change, view)
        cause = reply.cause() or "?"
        if not change:
            return self.no_change(cause, change)
        return self.tested(change, reply, cause)

    def prompt(self):
        excerpt = self.ref.excerpt(self.workspace, self.project.test_names(self.ref.file))
        fixed = [Part("Criterion", self.step["behaviour"], True),
                 Part("Project files (all exist; only those shown below may change)",
                      ", ".join(path for path in self.project.files if self.workspace.exists(path)), True),
                 Part("Failure output", Output.failure_text(self.output), True, keep="tail", place="last"),
                 Part(f"Test {self.ref}", excerpt, True)] + self.kept_tests()
        if self.approach == "review":
            fixed += self.plan_parts()
        thought = self.step.pop("unfinished_thought", "")
        fixed += [Part("Your analysis so far (it ran out of room before an answer: conclude it and answer now)",
                       thought, True, keep="tail", place="last")] if thought else []
        candidates = self.candidates()
        tail = [Part("Contracts", self.project.contracts(candidates[:4])),
                Part("Already tried", "\n".join(self.step.get("history", [])[-4:]), keep="tail", place="last"),
                Part("Language", self.project.language, place="first"),
                *self.session.knowledge_parts(*(("code", "tests") if self.approach == "review" else ("code",)))]
        return FileView(self.workspace, fixed, candidates, tail, output=self.output, hints=excerpt)

    def kept_tests(self):
        return [Part(f"Test {ref} (it passes now and must keep passing)",
                     ref.excerpt(self.workspace, self.project.test_names(ref.file)), True, atomic=True)
                for ref in map(TestRef, self.step.get("keep", [])[:2]) if ref.present(self.workspace)]

    def plan_parts(self):
        return [Part("Request", self.project.run["task"], True, keep="head", place="first")] + \
            [Part(f"Criterion of {ref}", self.project.criterion_of_test(str(ref)), True)
             for ref in map(TestRef, self.step.get("keep", [])[:2]) if self.project.criterion_of_test(str(ref))]

    def candidates(self):
        code = [path for path in self.session.files_on_disk("code", "template", "config") if path != ENV_FILE]
        named = [path for path in self.mentioned(code) if path in code]
        wanted = [path for path in self.step.get("want", []) if self.workspace.exists(path)]
        own_test = [self.ref.file] if self.approach == "review" else []
        uses = [path for path in self.project.uses(self.ref.file) if path in code]
        return list(dict.fromkeys(wanted + own_test + named[:2] + uses + named + code))[:FILES_SHOWN_PER_FIX]

    def mentioned(self, paths):
        return Output.mentioned_in(self.output, paths)

    def ask(self, view):
        key, tried = f"overthought_{self.approach}", f"tried_{self.approach}"
        first, concluding = not self.step.get(tried), bool(self.step.pop(key, None))
        self.step[tried] = self.step.get(tried, 0) + 1
        think = False if first or concluding else None
        label = self.label + (" (no thinking: concluding the last one's thought)" if concluding
                              else " (no thinking first)" if first else "")
        reply = self.session.ask(self.system(), view, label, max_reply=self.session.model.fix_max,
                                 unthinking_retry=False, think=think)
        self.hit_thinking_cap = self.session.model.hit_thinking_cap
        if self.hit_thinking_cap:
            self.step[key], self.step["unfinished_thought"] = True, self.session.model.last_unfinished_thought
        return reply

    def system(self):
        return Prompts.FIX_SYSTEM if self.approach == "fix" else Prompts.REVIEW_SYSTEM

    def may_change(self, view):
        def check(path, exists):
            if not exists:
                return None if self.approach == "review" and Path(path).parent == Path(self.ref.file).parent \
                    else "no new files now; change the files shown"
            if self.project.kind_of(path) == "test" or path == self.ref.file:
                return None if self.approach == "review" and path in view.shown else TESTS_LOCKED_REASON
            return None if path in view.shown else "only the files shown may change"
        return check

    def remember_wanted_files(self, reply, change, view):
        self.step["want"] = [path for path in reply.named_paths() if path not in change.backups
                             and self.workspace.exists(path)
                             and path not in view.whole][:2]

    def test_blamed(self, why):
        self.only_wanted_test_change = True
        self.record(f"the test is wrong: {why}")
        say.warn(f"{self.label}: the test is wrong ({first_line(why, 120)})")
        return TestOutcome("fails", self.output)

    def no_change(self, cause, change):
        note = "no usable change" + (f" ({'; '.join(change.notes)})" if change.notes else "")
        folder = str(Path(self.ref.file).parent)
        refused_near_tests = any(TESTS_LOCKED_REASON in note or str(Path(note.split(":", 1)[0]).parent) == folder
                                 for note in change.notes)
        self.only_wanted_test_change = self.approach == "fix" and refused_near_tests
        self.record(f"{cause} -> {note}")
        say.skip(f"{self.label}: {note}")
        return TestOutcome("fails", self.output)

    def tested(self, change, reply, cause):
        self.project.register(change.created, "test" if self.approach == "review" else "code",
                              f"added for {self.step['id']}")
        self.session.setup()
        state, output = self.v.evaluate(self.step)
        if self.broke_others(change, cause):
            return TestOutcome("fails", self.output + self.broken_note())
        self.record(f"{cause} -> {state}")
        if state == "passes":
            self.passed(reply, cause)
        say.info(f"{self.label}: changed {', '.join(change.files)} -> {state}"
                 + (f"  ({cause})" if cause != "?" else ""), "🔧")
        return TestOutcome(state, output)

    def passed(self, reply, cause):
        if self.approach == "review" and reply.criterion():
            self.correct_criterion(reply.criterion())
        if cause != "?":
            self.session.note(self.approach, f"{self.step['behaviour'][:120]} | solved: {cause}")

    def correct_criterion(self, text):
        say.info(f"{self.step['id']} now reads: {first_line(text, 120)} (was: {first_line(self.step['behaviour'], 80)})", "✏️")
        self.step["behaviour"], self.step["title"] = text, text[:80]
        self.session.save()

    def broke_others(self, change, cause):
        if not self.before:
            return False
        broken = sorted(self.before - self.v.passing())
        if not broken:
            return False
        self.remember_how_it_broke(broken[0])
        self.workspace.revert(change)
        tests = [self.project.criterion(criterion_id)["test"] for criterion_id in broken]
        self.keep_passing(tests)
        self.say_broken(tests, cause)
        return True

    def remember_how_it_broke(self, criterion_id):
        first = self.project.criterion(criterion_id)
        self.broken_test = first["test"]
        self.broken_output = Output.failure_text(self.v.evaluate(first)[1], FAILURE_OUTPUT_CHARS // 2)

    def say_broken(self, tests, cause):
        names = ", ".join(tests[:2])
        self.record(f"{cause} -> broke {names}; reverted")
        say.warn(f"{self.label}: broke {names}; reverted")

    def keep_passing(self, tests):
        kept = self.step.setdefault("keep", [])
        self.tests_may_disagree = any(test in kept for test in tests)
        kept.extend(test for test in tests if test not in kept)

    def broken_note(self):
        return (f"\n\nYour last change broke {', '.join(self.step['keep'])} (shown below); "
                f"it was reverted. With it, {self.broken_test} failed like this:\n{self.broken_output}")

    def record(self, line):
        self.step.setdefault("history", []).append(f"{self.approach}: {line}")


class BuildFix(Worker):
    def __init__(self, session, output, tried=(), concluding=""):
        super().__init__(session)
        self.output, self.tried, self.concluding = output, tried, concluding
        self.unfinished_thought = ""

    def run(self):
        view = self.prompt()
        try:
            label = "Make it build" + (" (no thinking: concluding the last one's thought)" if self.concluding else "")
            reply = self.session.ask(Prompts.BUILD_SYSTEM, view, label, max_reply=self.session.model.fix_max,
                                     unthinking_retry=False, think=False if self.concluding or not self.tried else None)
        except PromptTooLarge as exc:
            say.skip(f"Make it build: the prompt does not fit ({exc})")
            return "the prompt did not fit"
        if self.session.model.hit_thinking_cap:
            self.unfinished_thought = self.session.model.last_unfinished_thought
        change = self.workspace.apply(reply, self.may_change(view), view.windows)
        return self.said(change, reply.cause() or "?")

    def prompt(self):
        fixed = [Part("What failed", Output.failure_text(self.output), True, keep="tail"),
                 Part("Project files (all exist; only those shown below may change)",
                      ", ".join(path for path in self.project.files if self.workspace.exists(path)), True)]
        fixed += [Part("Your analysis so far (it ran out of room before an answer: conclude it and answer now)",
                       self.concluding, True, keep="tail", place="last")] if self.concluding else []
        tail = [Part("Already tried (it still failed)", "\n".join(f"- {attempt}" for attempt in self.tried)),
                Part("Language", self.project.language), *self.session.knowledge_parts("code", "config")]
        return FileView(self.workspace, fixed, self.candidates(), tail, output=self.output, hints=self.output)

    def candidates(self):
        files = [path for path in self.session.files_on_disk("code", "template", "config") if path != ENV_FILE]
        configs = [path for path in files if self.project.files[path].get("kind") == "config"]
        return list(dict.fromkeys(Output.mentioned_in(self.output, files) + configs + files))[:FILES_SHOWN_PER_FIX]

    def may_change(self, view):
        def check(path, exists):
            if not exists or path not in view.shown:
                return "only the files shown may change now"
            return TESTS_LOCKED_REASON if self.project.kind_of(path) == "test" else None
        return check

    def said(self, change, cause):
        for note in change.notes:
            say.skip(f"Skipped {note}")
        did = f"changed {', '.join(change.files)}" if change else "no usable change"
        say.info(f"Make it build: {did}" + (f"  ({cause})" if cause != "?" else ""), "🔧")
        if change and cause != "?":
            self.session.note("build", cause)
        return f"{cause} -> {did}"


class BugReproducer(Phase):
    TITLE = "Reproduce the bug (a test that fails now)"

    def run(self, cycle):
        Designer(self.session).ensure_toolchain()
        if not TestTool(self.session).works() or not Verifier(self.session).make_it_build():
            return False
        note = ""
        for _ in range(2):
            step, state, output = self.write_test(cycle, note)
            if self.reproduced(step, state, output):
                say.done(f"Reproduced: {first_line(step['behaviour'], 140)}")
                with say.section():
                    say.info(f"It fails now: {Output.key_line(output)}", "🐞")
                self.session.save()
                return True
            note = self.why_not(step, state, output)
            say.fail(f"Not reproduced: {note}")
        self.drop(cycle)
        self.session.unreproduced_reason = (f"the bug could not be reproduced with a test, so nothing was changed. "
                                     f"Describe what you do, what happens and what should happen instead, then: "
                                     f"tinker fix {self.project.run['id']} \"...\"")
        return False

    @staticmethod
    def reproduced(step, state, output):
        return step and state == "fails" and not Toolchain.ran_no_test(output)

    def write_test(self, cycle, note):
        reply = self.session.ask(Prompts.REPRODUCE_SYSTEM, self.prompt(cycle, note), f"Reproduce bug {cycle['id']}")
        named = re.search(r"^\s*TEST:\s*(.+?)\s*$", reply.text, re.M)
        if not named:
            return WrittenTest(None, "missing", 'the reply has no line "TEST: <test file>::<test name>"')
        ref = TestRef(named.group(1).strip("`'\" "))
        problem = self.unusable(cycle, ref)
        if problem:
            return WrittenTest(None, "missing", problem)
        step = self.step_for(cycle, ref)
        change = self.workspace.apply(reply,
                                      lambda path, exists: None if path == ref.file else "only the bug's test file")
        for line in change.notes:
            say.skip(f"Skipped {line}")
        expected = re.search(r"^\s*EXPECTED:\s*(.+)$", reply.text, re.M)
        if expected:
            step["behaviour"] = first_line(expected.group(1), 300)
        return WrittenTest(step, *Verifier(self.session).evaluate(step))

    def prompt(self, cycle, note):
        code = self.session.files_on_disk("code", "template")
        named = Output.mentioned_in(asked(cycle), code)
        entry = self.project.entry_files(code)
        tests = self.session.files_on_disk("test")
        return [Part("Bug report", asked(cycle), True),
                Part("The project's test files (the new one goes next to them)", "\n".join(tests) or "none yet", True),
                Part("Note", note, bool(note)),
                Part("Test command", self.session.toolchain.test_one, True),
                Part("Contracts of the code",
                     self.project.contracts([path for path in code if self.project.files[path].get("contract")])),
                *[Part(f"File {path}", self.workspace.read(path), atomic=True)
                  for path in dict.fromkeys(named + entry)][:2],
                Part(f"Start of {tests[0]} (how the project's tests are written)",
                     "\n".join(self.workspace.read(tests[0]).splitlines()[:30]) if tests else ""),
                Part("External services: adapters and sample data",
                     next((cycle.get("data", "") for cycle in self.project.cycles if cycle["id"] == "base"), "")),
                Part("Project files", self.project.outline()),
                *self.session.knowledge_parts("tests")]

    def unusable(self, cycle, ref):
        if not ref.file or not ref.name:
            return f"TEST: {ref} names no file and test (<test file>::<test name>)"
        try:
            self.workspace.resolve(ref.file, for_write=True)
        except ToolError as exc:
            return f"TEST: {ref.file}: {exc}"
        mine = {TestRef(criterion["test"]).file for criterion in self.project.cycle_criteria(cycle)}
        if (ref.file in self.project.files or self.workspace.exists(ref.file)) and ref.file not in mine:
            return f"TEST: {ref.file} is a file the project has; the bug's test goes in a new file next to its tests"
        return ""

    def step_for(self, cycle, ref):
        existing = self.project.cycle_criteria(cycle)
        if existing:
            step = existing[0]
            old = TestRef(step["test"]).file
            if old != ref.file:
                self.project.remove_file(self.workspace, old)
            step["test"] = str(ref)
        else:
            step = self.project.new_criterion("B", first_line(cycle["request"], 80), cycle["request"], str(ref),
                                              cycle["id"], "bug report", "todo")
            self.project.criteria.append(step)
        self.project.register([ref.file], "test", f"reproduces bug {cycle['id']}", self.project.paths("code"))
        return step

    def why_not(self, step, state, output):
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
        for step in self.project.cycle_criteria(cycle):
            self.project.remove_file(self.workspace, TestRef(step["test"]).file)
            self.project.criteria.remove(step)
        cycle["phase"], cycle["outcome"] = "done", "not reproduced"
        self.session.save()


class TestTool(Worker, NotAPytestClass):
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
                self.session.knowledge.fix_result(repair[1], worked=False)
            problem = Output.key_line(output)
            say.fail(f"{self.STAGE_TEXT[stage]}: {problem}")
            repair = ToolchainRepair(self.session).run(output, tried) if attempt < 2 else None
            if repair is None:
                break
        self.session.tool_problem = f"the project's tools do not work: {problem}"
        return False

    def check(self, attempt):
        ok, output = self.session.setup(force=attempt > 0)
        if not ok:
            return SetupOutcome("setup", output)
        self.session.wait_for_network()
        self.session.last_all_tests_run = None if attempt else self.session.last_all_tests_run
        code, output = self.session.run_all_tests()
        present = [ref for ref in self.project.refs() if ref.present(self.workspace)]
        problem = self.test_tool_problem(code, output, present)
        if problem:
            return SetupOutcome("tests", problem)
        if not present:
            say.done(f"The test tool runs (no tests yet): $ {self.session.toolchain.test_all}")
        return self.linter_runs()

    def test_tool_problem(self, code, output, present):
        reached = self.reaches_code(output) or (not Toolchain.reached_no_test(code, output, present) if present
                                                else self.ran_empty(code, output))
        if not reached:
            say.fail(f"$ {self.session.toolchain.test_all}  "
                     f"[{'ran no test' if present else 'did not run'}: a tooling problem]")
            return output
        unseen = self.unseen_code(output) if present and code else ""
        if unseen:
            return unseen
        return self.one_test_unpicked(present[0]) if present and present[0].name in output else ""

    def reaches_code(self, output):
        return any(path in output for path in self.project.paths("code"))

    @staticmethod
    def ran_empty(code, output):
        if code == 0:
            return True
        return code not in (None, 126, 127) and (Toolchain.ran_no_test(output)
                                                 or not Output.ERROR_LINE_RE.search(output))

    def unseen_code(self, output):
        if any(criterion.get("status") == "green" for criterion in self.project.criteria):
            return ""
        missed = Output.misses_own_code(output, self.session.files_on_disk("code"))
        if not missed or not self.session.toolchain.run_check(self.workspace)[0]:
            return ""
        say.fail(f"$ {self.session.toolchain.test_all}  [the tests cannot import the project's own {missed}, "
                 f"which loads with the check command: a tooling problem]")
        return output + (f"\n(The tests cannot import the project's own module {missed}, which loads fine with the "
                         f"check command: {Output.OWN_CODE_UNSEEN} when it runs from the project folder.)")

    def one_test_unpicked(self, real):
        toolchain = self.session.toolchain
        if getattr(self.session, "one_test_checked", None) == toolchain.test_one:
            return ""
        made_up = TestRef(f"{real.file}::tinker_made_up_test")
        _, real_output = toolchain.run_test(self.workspace, real)
        _, made_up_output = toolchain.run_test(self.workspace, made_up)
        if self.unnamed(real_output, real.name) != self.unnamed(made_up_output, made_up.name):
            self.session.one_test_checked = toolchain.test_one
            return ""
        say.fail(f"$ {real.command(toolchain.test_one)}  [answers a made-up test name the same way: a tooling problem]")
        return (f"{real_output}\n(The one-test command `{toolchain.test_one}` answers a made-up test name the same "
                f"way as the real test {real.name}: it does not run a test by its name, as the test tool needs it.)")

    @staticmethod
    def unnamed(output, name):
        return re.sub(r"[\d.]+", "", output.replace(name, "<name>"))

    def linter_runs(self):
        if not self.session.toolchain.lint:
            return SetupOutcome("ok", "")
        code, output = self.workspace.run(self.session.toolchain.shell(self.session.toolchain.lint), quiet=True)
        if self.linter_missing(code, output):
            say.fail(f"$ {self.session.toolchain.lint}  [the linter does not run: a tooling problem]")
            return SetupOutcome("lint", f"$ {self.session.toolchain.lint}\nthe linter does not run (exit code {code}):\n{output}")
        return SetupOutcome("ok", "")

    def linter_missing(self, code, output):
        return code in (126, 127) or (code != 0 and self.MISSING_TOOL_RE.search(output)
                                      and not self.reaches_code(output))

    def remember_what_worked(self, repair):
        knowledge = self.session.knowledge
        if repair and repair[0] == "known":
            knowledge.fix_result(repair[1], worked=True)
        elif repair:
            knowledge.remember_fix(repair[1])
            self.session.note("toolchain", repair[1]["cause"])


class ToolchainRepair(Worker):
    def run(self, output, tried):
        failure = Output.tool_failure(output, self.workspace.root)
        known = self.session.knowledge.known_fix(failure, tried)
        if known:
            tried.add(failure)
            say.info(f"Applying a toolchain fix learned on an earlier project ({known['cause']})", "♻️")
            self.replay(known["replay"])
            return "known", known
        fix = self.ask_model(output, failure)
        return ("model", fix) if fix else None

    def replay(self, replay):
        if replay.get("toolchain"):
            self.project.toolchain = self.session.toolchain.merged(replay["toolchain"])
        for path, content in (replay.get("files") or {}).items():
            if not self.workspace.exists(path) and self.project.kind_of(path) in (None, "config"):
                self.workspace.write(path, content)
                self.project.register([path], "config", "added by a learned toolchain fix")
        self.session.save()

    def ask_model(self, output, failure):
        configs = [path for path in self.session.files_on_disk("config") if path != ENV_FILE]
        before = self.session.toolchain.to_dict()
        reply = self.ask_fix(output, failure, configs, before)
        change = self.apply_fix(reply, configs)
        commands = {key: value for key, value in self.session.toolchain.to_dict().items() if value != before.get(key)}
        changed = (["toolchain"] if commands else []) + change.files
        say.info(f"Toolchain fix: {', '.join(changed) or 'no usable change'}  ({reply.cause() or '?'})", "🔧")
        self.session.save()
        if not changed:
            return None
        return self.learned(failure, reply, commands, change)

    def ask_fix(self, output, failure, configs, before):
        similar = self.session.knowledge.similar_fix(failure)
        return self.session.ask(Prompts.TOOLCHAIN_FIX_SYSTEM, [
            Part("Output", Output.failure_text(output), True, keep="tail"),
            Part("Toolchain", json.dumps(before), True),
            Part("Language", self.project.language),
            Part("A similar failure on an earlier project was fixed by", similar["cause"] if similar else ""),
            *[Part(f"File {config}", self.workspace.read(config), atomic=True) for config in configs],
            *self.session.knowledge_parts("toolchain", "config"),
        ], "Fix the toolchain", max_reply=self.session.model.fix_max, unthinking_retry=False)

    def apply_fix(self, reply, configs):
        try:
            given = reply.toolchain_line()
        except ValueError:
            given = None
        if given:
            self.project.toolchain = self.session.toolchain.merged(given)
        change = self.workspace.apply(reply, lambda path, exists: None if path in configs or not exists
                                      else "only config files change now")
        self.project.register(change.created, "config", "added by the toolchain fix")
        for note in change.notes:
            say.skip(f"Skipped {note}")
        return change

    def learned(self, failure, reply, commands, change):
        replayable = set(change.files) == set(change.created)
        files = {path: self.workspace.read(path) for path in change.created
                 if self.project.kind_of(path) == "config"}
        return {"failure": failure, "cause": reply.cause() or "?",
                "replay": {"toolchain": commands, "files": files} if replayable else None}
