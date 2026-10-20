"""Finishing a cycle: the linter's findings fixed, the README (its examples run as the acceptance
test), one final review as a person runs it, and the lessons for the next projects."""

import re
import time
from pathlib import Path

from .building import ToolchainRepair, Verifier
from .model import OllamaError
from .session import Phase
from .prompts import Part, PromptTooLarge, Prompts
from .settings import ENV_EXAMPLE, ENV_FILE, FIX_FILES, first_line
from .workspace import FileView, Output, TaughtCommands, TestRef, Toolchain


class Tidy(Phase):
    """8. Tidy, once the app is built and its tests pass: the language's linter (toolchain.lint) over
    the code and the tests. Each file it reports gets one call that fixes what was found there,
    kept only when every test that passed still passes and every test is still there. Quality, not
    function: tidying never stops the run, and what it cannot fix is said plainly."""
    TITLE = "Tidy (the linter over the code)"
    BUILDS = True
    MAX_FILES = FIX_FILES  # files one tidy pass may change

    def run(self, cycle):
        if not self.s.toolchain.lint:
            print("  no lint command: nothing to tidy")
            return None
        output = self.lint()
        reported = self.reported(output)
        if not reported:
            self.say_nothing_to_tidy(output)
            return None
        print(f"  $ {self.s.toolchain.lint}: problems in {', '.join(reported)}")
        passing = Verifier(self.s).passing()
        for path in reported:
            self.tidy(path, output, passing)
        left = self.reported(self.lint())
        print(f"  the linter still reports problems in {', '.join(left)}" if left
              else "  the linter finds no problem now")
        return None

    @staticmethod
    def say_nothing_to_tidy(output):
        if not output:
            print("  the linter finds no problem")
        else:
            print(f"  the linter reports problems, in no file Tinker wrote: {Output.key_line(output)}")

    def lint(self):
        """What the linter reports ("" when nothing), after the formatter and its safe automatic
        fixes have run."""
        self.s.format_code()
        ok, output = self.s.toolchain.run_lint(self.ws)
        return "" if ok else output

    def reported(self, output):
        """The project's code and test files the linter locates problems in, the most first (a file
        only quoted in another's problem, such as a script a test runs, is not one of them)."""
        counted = [(self.findings(output, p), p) for p in self.s.files_on_disk("code", "test")] if output else []
        return [p for n, p in sorted(counted, key=lambda pair: -pair[0]) if n][:self.MAX_FILES]

    @staticmethod
    def findings(output, path):
        """The problems a linter's output locates in a file (path:line or path, line N)."""
        return sum(1 for match in Output.LOCATION_RE.finditer(output) if Output.names(match.group(1), path))

    def tidy(self, path, output, passing):
        """One call for one file: what the linter says about it, and the file as numbered lines, so
        the reply changes only the lines reported (a few LINES blocks, not the file written again);
        reverted if a test breaks."""
        said = self.said_about(output, path)
        lines = self.ws.read(path).splitlines()
        numbered = "\n".join(f"{n:>4}| {line}" for n, line in enumerate(lines, 1))
        parts = [Part(f"Linter output for {path}", Output.failure_text(said), True),
                 Part(f"File {path}, lines 1-{len(lines)} (numbered)", numbered, True, exact=True),
                 Part("Language", self.p.language)]
        try:
            change, reply = self.tidied(path, parts, len(lines))
        except PromptTooLarge as exc:
            print(f"  tidy {path}: the prompt does not fit ({exc})")
            return
        self.keep_if_better(path, change, reply, output, passing)

    @staticmethod
    def said_about(output, path):
        """What the linter says about one file: its blocks (linters print a problem, or a file's
        problems, as lines between blank ones: the message with its location and code) that name
        the file; the whole output when none is set apart."""
        blocks = [b for b in re.split(r"\n\s*\n", output) if Output.names(b, path)]
        return "\n\n".join(blocks) or output

    def tidied(self, path, parts, lines):
        """(the change, the reply): each problem is named with its line, so thinking only when a call without
        it gave no usable change."""
        change, reply = None, None
        for think in (False, None):
            reply = self.s.ask(Prompts.TIDY_SYSTEM, parts, f"Tidy {path}", max_reply=self.s.model.fix_max,
                               think=think)
            change = self.ws.apply(reply, lambda p, exists: "only the lines shown may change", {path: (1, lines)})
            if change:
                break
        return change, reply

    def keep_if_better(self, path, change, reply, output, passing):
        """The change kept when the linter finds less in the file and no test broke; else reverted."""
        if not change:
            print(f"  tidy {path}: no usable change")
        elif not self.fewer_findings(path, output):
            self.ws.revert(change)
            print(f"  tidy {path}: the linter found as much or more in it afterwards; reverted")
        elif self.broke(change, passing):
            print(f"  tidy {path}: it broke a passing test; reverted")
        else:
            print(f"  tidy {path}: changed  ({reply.cause() or 'lint problems fixed'})")

    def fewer_findings(self, path, before):
        """Whether the linter now locates fewer problems in the file than before the change (a fix
        that leaves them, or makes a new one such as a name it removed but still uses, is no fix)."""
        return self.findings(self.s.toolchain.run_lint(self.ws)[1], path) < self.findings(before, path)

    def broke(self, change, passing):
        """A test that passed fails now, or a test is gone from a test file it tidied."""
        self.s.all_tests_run = None
        tests_kept = all(TestRef.name_at(self.ws.read(path), name) >= 0 for path in change.files
                         for name in self.p.test_names(path))
        if tests_kept and passing <= Verifier(self.s).passing():
            return False
        self.ws.revert(change)
        self.s.all_tests_run = None
        return True


class ReadmeWriter(Phase):
    """9. README, for a beginner, and the acceptance test of the built app: what the app is, every
    feature in plain words, and how to use each one, tldr-style: a shell session, each command
    with the output it prints (from one call that reads the request, the spec and the code); then
    setup, settings, run and tests, from the toolchain exactly as it is. Then every example in it
    is run (a server started first): what the README says must be what the app does. A failing
    example gets a fix of the code or of the README, whichever is wrong, kept only while every
    test passes; what still fails is said plainly. It never stops the run."""
    TITLE = "README (and its examples run, as a user would)"
    BUILDS = True
    MAX_USAGE = 12      # usage examples kept
    FIX_ROUNDS = 2      # fix calls for examples that do not do what the README says
    SERVER_WAIT = 20    # seconds a started server has to answer the first example
    LOCAL_RE = re.compile(r"\b(127\.0\.0\.1|localhost)\b")  # an example that talks to the app as a server
    PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")  # a value that differs each run, e.g. <code>: any one matches

    def run(self, cycle):
        readme = next((p for p, f in self.p.files.items() if f.get("kind") == "docs"), "README.md")
        if not self.ws.exists(readme):
            guide = self.guide()
            cycle["serves"] = guide["serves"]
            self.ws.write(readme, self.text(guide))
            self.p.register([readme], "docs", "what it does, how to use, set up, run and test it")
            print(f"  wrote {readme}")
            self.s.save()
        self.check(cycle, readme)
        return None

    def guide(self):
        """{overview, features, usage: [{what, command, output}], serves} from one call; else the
        criteria as features, with no examples."""
        code = [p for p in self.s.files_on_disk("code", "template") if p != ENV_FILE]
        entry = self.p.entry_files(code)
        parts = [Part("Request", self.p.run["task"], True, keep="head"),
                 Part("What it does (its tested criteria)", "\n".join(f"- {s['behaviour']}" for s in self.p.criteria),
                      True),
                 Part("What a person runs once to use it", "\n".join(self.s.toolchain.use)
                      or "(nothing: the language as installed is enough)", True),
                 Part("How a person starts it, after that", self.s.toolchain.run or "(no entry point: it is used from "
                                                                                    "code)", True),
                 Part("Language", self.p.language, True),
                 *[Part(f"File {p}", self.ws.read(p), atomic=True) for p in dict.fromkeys(entry + code)]]
        try:
            found = self.s.model.ask_until_usable(Prompts.README_SYSTEM, parts, "README", self.read, attempts=2,
                                                  think=False)  # it describes code that exists; its examples are run
        except (PromptTooLarge, OllamaError) as exc:
            print(f"  README: {first_line(str(exc), 120)}; its features are the spec's criteria")
            found = None
        return found or {"overview": "", "features": [s["behaviour"] for s in self.p.criteria], "usage": [],
                         "serves": False}

    def read(self, reply):
        data = reply.json()
        clean = lambda value: " ".join(str(value or "").split())
        features = [clean(f) for f in data.get("features") or [] if clean(f)]
        entries = [u for u in data.get("usage") or [] if isinstance(u, dict)]
        usage = [{"what": clean(u.get("what")), "command": clean(u.get("command")),
                  "output": str(u.get("output") or "").strip("\n")}
                 for u in entries if clean(u.get("what")) and clean(u.get("command"))]
        if not features or not usage:
            raise ValueError("it needs the features and at least one usage example")
        serves = str(data.get("serves")).lower() in ("true", "yes") or any(self.LOCAL_RE.search(u["command"])
                                                                          for u in usage)
        return {"overview": str(data.get("overview") or "").strip(), "features": features,
                "usage": usage[:self.MAX_USAGE], "serves": serves, "requires": clean(data.get("requires"))}

    def text(self, guide):
        """What it is, its features, getting started (only what using it needs), usage as a person
        types it, settings, then development: setting it up for its tests, the tests, format and lint."""
        sections = [f"# {self.p.workdir.name}", guide["overview"] or self.p.run["task"].strip(),
                    "## Features", "\n".join(f"- {feature}" for feature in guide["features"])]
        started = self.getting_started(guide)
        sections += ["## Getting started", *[s for s in started if s]] if any(started) else []
        if guide["usage"]:
            sections += ["## Usage", "\n\n".join(f"- {u['what']}:\n\n```console\n$ {spelled(u['command'])}\n"
                                                   f"{u['output']}".rstrip() + "\n```" for u in guide["usage"])]
        if self.ws.exists(ENV_EXAMPLE):
            sections += ["## Settings", f"Copy `{ENV_EXAMPLE}` to `{ENV_FILE}` and fill in what it asks for "
                                        f"(environment variables override it):",
                         "```\n" + self.ws.read(ENV_EXAMPLE).strip() + "\n```"]
        return "\n\n".join(sections + self.development()) + "\n"

    def getting_started(self, guide):
        """Only what using it needs: what it requires, what to run once, how to start it."""
        tc = self.s.toolchain
        started = [f"It needs {guide['requires']}." if guide.get("requires") else ""]
        started += ["Once, in the project folder:", shell_block(*tc.use)] if tc.use else []
        if tc.run and guide["serves"]:
            started.append(f"Start it with `{spelled(tc.run)}`; it keeps running, so use it from another terminal.")
        elif tc.run:
            started.append(f"Run it with `{spelled(tc.run)}`, followed by what you want it to do.")
        return started

    def development(self):
        """Setting it up for its tests (activated after the environment is made), the tests, format and lint."""
        tc = self.s.toolchain
        setup = tc.setup[:1] + [tc.activate] + tc.setup[1:] if tc.activate and tc.setup else tc.setup
        sections = ["## Development", "To change it, set it up once for development:", shell_block(*setup),
                    (f"In a new terminal, first `{spelled(tc.activate)}`. " if tc.activate else "") + "Run the tests:",
                    shell_block(tc.test_all)]
        if tc.format or tc.lint:
            sections += ["Format the code and check it with the linter:", shell_block(tc.format, tc.lint)]
        return sections

    def check(self, cycle, readme):
        """Every example run as a user runs it; a failing one fixed (fix), up to FIX_ROUNDS times
        (a reply that could not be used is told why, the next time)."""
        note = ""
        for round_number in range(self.FIX_ROUNDS + 1):
            failures = self.failing(cycle, readme)
            if not failures:
                print(f"  every example in {readme} does what it says")
                return
            for command, expected, output in failures:
                print(f"  {readme}: `{first_line(command, 80)}` does not print what it says")
            if round_number == self.FIX_ROUNDS:
                break
            note = self.fix(readme, failures, note)
        print(f"  {len(failures)} example(s) in {readme} still do not do what it says (see SPEC.md)")
        cycle["readme_failures"] = [f"`{command}`: {first_line(output, 160)}" for command, _, output in failures]

    def failing(self, cycle, readme):
        """[(command, expected lines, output)] of the examples that do not print what they say."""
        examples = self.examples(self.ws.read(readme))
        tc = self.s.toolchain
        ready = lambda command: " && ".join(tc.use + [command])  # as a person runs it, after getting started
        run = TaughtCommands.spelled_out(tc.run) if tc.run else ""
        serves = run and (cycle.get("serves") or any(self.LOCAL_RE.search(command) for command, _ in examples))
        examples = [e for e in examples if not (serves and e[0] == run)]  # starting it is no example to check
        if not examples:
            return []
        server = self.ws.start(ready(tc.run)) if serves else None
        try:
            if server:
                self.wait_for(examples[0])
            return self.run_examples(examples, ready)
        finally:
            if server:
                self.ws.stop(server[0])

    @staticmethod
    def examples(text):
        """[(command, the output lines it should print)] of the README's usage sessions: in its Usage
        section, each line "$ command" in a console block, and the lines under it."""
        usage = text.split("## Usage", 1)[1].split("\n## ", 1)[0] if "## Usage" in text else ""
        found = []
        for block in re.findall(r"```console\n(.*?)```", usage, re.S):
            for line in block.splitlines():
                if line.startswith("$ "):
                    found.append((line[2:].strip(), []))
                elif found and line.strip():
                    found[-1][1].append(line.strip())
        return found

    def wait_for(self, example):
        """Until a started server answers the first example (or SERVER_WAIT seconds pass)."""
        began = time.monotonic()
        while time.monotonic() - began < self.SERVER_WAIT:
            code, output = self.ws.run(example[0], timeout=10, quiet=True)
            if code == 0 and not self.missing(example[1], output):
                return
            time.sleep(1)

    def run_examples(self, examples, ready):
        """[(command, expected lines, output)] of the examples that do not print what they say; a <placeholder>
        one example showed fills the commands after it."""
        failures, values = [], {}
        for command, expected in examples:
            filled = self.PLACEHOLDER_RE.sub(lambda m: values.get(m.group(0), m.group(0)), command)
            code, output = self.ws.run(ready(filled), timeout=Toolchain.test_timeout(), quiet=True)
            missing, shown = self.compare(expected, output)
            values.update(shown)
            if code is None or missing:
                failures.append((command, expected, output if code is not None else
                                 f"{output}\n(it did not finish within {Toolchain.test_timeout()}s)"))
        return failures

    @classmethod
    def missing(cls, expected, output):
        """The documented lines its output does not show (whitespace aside; a line with ... is a
        shortened one, not checked; a <placeholder> stands for any value)."""
        return cls.compare(expected, output)[0]

    @classmethod
    def compare(cls, expected, output):
        """(the documented lines its output does not show, {placeholder: the value it showed}).
        Each documented line must be a whole output line, whitespace aside (JSON may be
        spaced either way); a <placeholder> matches any value, which is kept."""
        said = ["".join(line.split()) for line in output.splitlines()]
        values, missing = {}, []
        for line in expected:
            if "..." in line:
                continue
            compact = "".join(line.split())
            found = next((m for m in map(cls.line_pattern(compact).fullmatch, said) if m), None)
            if found:
                values.update(zip(cls.PLACEHOLDER_RE.findall(compact), found.groups()))
            else:
                missing.append(line)
        return missing, values

    @classmethod
    def line_pattern(cls, compact):
        """A documented line as a pattern: its text as it is, each <placeholder> any value."""
        parts = [re.escape(part) for part in cls.PLACEHOLDER_RE.split(compact)]
        return re.compile(parts[0] + "".join(r"(\S+?)" + part for part in parts[1:]))

    def fix(self, readme, failures, note=""):
        """One call: for each failing example, the code or the README is corrected (whichever is
        wrong); kept only while every test that passed still passes. Returns why a reply could not
        be used ("" otherwise), for the next call."""
        view = self.fix_prompt(readme, failures, note)
        passing = Verifier(self.s).passing()
        try:
            reply = self.s.ask(Prompts.README_FIX_SYSTEM, view, "Fix the README's examples",
                               max_reply=self.s.model.fix_max)
        except PromptTooLarge as exc:
            print(f"  README fix: the prompt does not fit ({exc})")
            return ""
        change = self.ws.apply(reply, lambda p, exists: None if exists and p in view.shown
                               and self.p.files.get(p, {}).get("kind") != "test" else "only the files shown change",
                               view.windows)
        return self.kept(change, reply, passing)

    def fix_prompt(self, readme, failures, note):
        """Each failing example (what the README says, what it printed); then the README and the code."""
        shown = "\n\n".join(f"$ {command}\nThe README says it prints:\n" + ("\n".join(expected) or "(nothing)")
                             + f"\nIt printed:\n{Output.failure_text(output, 1200)}"
                             for command, expected, output in failures)
        code = [p for p in self.s.files_on_disk("code", "template") if p != ENV_FILE]
        fixed = [Part("Request", self.p.run["task"], True, keep="head"),
                 Part("Examples that do not do what the README says", shown, True, keep="head"),
                 Part("Note", note, place="last")]
        return FileView(self.ws, fixed, [readme] + self.p.entry_files(code) + code, [Part("Language", self.p.language)],
                        output=shown, hints=shown)

    def kept(self, change, reply, passing):
        """The fix kept while every test that passed still passes; why it was not, for the next call."""
        if not change:
            why = "; ".join(change.notes) or "it changed no file shown"
            print(f"  README fix: no usable change ({first_line(why, 160)})")
            return f"Your last reply could not be used: {why}. Give each file you change complete."
        self.s.all_tests_run = None
        if not passing <= Verifier(self.s).passing():
            self.ws.revert(change)
            print("  README fix: it broke a passing test; reverted")
            return "Your last change broke a passing test and was undone."
        print(f"  README fix: changed {', '.join(change.files)}  ({reply.cause() or '?'})")
        return ""


class FinalReview(Phase):
    """10. Final review, once, when the app is built and every test passes: the project as a
    person gets it, with no model call. It is set up afresh from its own files, the whole test
    suite runs with the test command SPEC.md and the README give (the way a person runs it, not
    one test at a time), and every spec point's test is there and passes. A tooling problem (the
    tests cannot find the app's code when the test tool runs alone, a dependency the project's
    files do not install) gets one toolchain fix; what still fails is said plainly and the cycle is
    not done."""
    TITLE = "Final review (the project as a person runs it)"
    BUILDS = True

    def run(self, cycle):
        problem, output = self.problem()
        if problem and "passed once and failed" not in problem and ToolchainRepair(self.s).run(output, set()):
            problem, output = self.problem()
        if problem:
            self.s.review_problem = problem
            print(f"  not done: {problem}")
            return False
        green, _, total = self.p.counts()
        print(f"  set up afresh; `{self.s.toolchain.test_all}` passes; {green}/{total} spec points tested and passing")
        return True

    def problem(self):
        """(what is wrong, its output), or ("", "") when the project passes the review."""
        ok, output = self.s.setup(force=True)
        if not ok:
            return f"setup fails from the project's own files: {Output.key_line(output)}", output
        self.s.wait_for_network()
        return self.suite_problem()

    def suite_problem(self):
        """(what is wrong, its output) of the whole suite run twice as a person runs it, or ("", "")."""
        self.s.all_tests_run = None
        code, output = self.s.run_all_tests()
        if code != 0:
            return f"`{self.s.toolchain.test_all}` fails when the whole suite runs: {Output.key_line(output)}", output
        again, output_again = self.s.toolchain.run_all(self.ws)  # a test that passes by chance fails some runs
        if again != 0:
            return (f"`{self.s.toolchain.test_all}` passed once and failed the next time: a test depends on chance "
                    f"(random output) or on order: {Output.key_line(output_again)}"), output_again
        if Toolchain.ran_no_test(output):
            return f"`{self.s.toolchain.test_all}` runs no test", output
        untested = [s["id"] for s in self.p.criteria
                    if (not TestRef(s["test"]).present(self.ws) or s["status"] != "green")]
        if untested:
            return f"spec points without a passing test: {', '.join(untested)}", output
        return "", ""


class LessonWriter(Phase):
    """Lessons: what this cycle proved, and what its problems teach the next projects."""
    TITLE = "Lessons (for the next projects)"

    def run(self, cycle):
        """The cycle succeeded: the knowledge it used is proven (once). One call turns the problems
        this cycle solved into general lessons (none: no call); one that names this project is not
        general, and is not kept. A finished project's toolchain is kept for its language: every
        command of it has worked by now (one test at a time, the whole suite, the linter), which is not
        so when the test tool first runs."""
        events = self.p.spec.get("events", [])
        if not cycle.get("proven"):
            self.prove_what_was_used(cycle)
        if cycle["id"] == "base" and not cycle.get("counted"):
            self.keep_the_toolchain(cycle)
        if not events:
            print("  no problems to learn from")
            return
        try:
            new, confirmed = self.ask_lessons(events)
        except (SystemExit, OllamaError) as exc:  # the project is built: lessons are a bonus
            print(f"  {exc} Nothing learned this time.")
            return
        names = [Path(path).name for path in self.p.files] + [self.p.workdir.name]
        added = self.s.knowledge.add_lessons(new, confirmed, names)
        print(f"  {added} new lesson(s), {len(confirmed)} confirmed: {self.s.knowledge.summary()}")
        self.p.spec["events"] = []
        self.s.save()

    def prove_what_was_used(self, cycle):
        proven = self.s.knowledge.prove(cycle.get("used", {}))
        cycle["proven"] = True
        self.s.save()
        if proven:
            print(f"  {proven} lesson(s) and fact(s) used here are proven now")

    def keep_the_toolchain(self, cycle):
        """A finished project's toolchain, kept for its language (counted once: a resumed lessons phase does
        not count it again)."""
        self.s.knowledge.remember_toolchain(self.s.toolchain)
        self.s.knowledge.finished_project()
        cycle["counted"] = True
        self.s.save()

    def ask_lessons(self, events):
        """(new lessons, the numbers of the known ones confirmed) the problems teach."""
        known = self.s.knowledge.known_lessons()
        return self.s.ask_until_usable(Prompts.LESSONS_SYSTEM, [
            Part("Language", self.p.language, True),
            Part("Problems and how they were solved", "\n".join(f"- [{e['kind']}] {e['text']}" for e in events),
                 True, keep="tail"),
            Part("Known lessons", "\n".join(f"{i}. {lesson['text']}" for i, lesson in enumerate(known, 1)))],
            "Lessons", self.read, attempts=2)

    @staticmethod
    def read(reply):
        data = reply.json()
        lessons = [item for item in data.get("lessons", []) if isinstance(item, dict)]
        confirmed = [n for n in data.get("confirmed", []) if isinstance(n, int)]
        return lessons[:3], confirmed


def spelled(command):
    """A command spelled out (the person's aliases replaced by what they stand for): it works for anyone."""
    return TaughtCommands.spelled_out(command)


def shell_block(*commands):
    """A shell block of the commands given (spelled out)."""
    return "```sh\n" + "\n".join(spelled(c) for c in commands if c) + "\n```"
