import re
from collections import namedtuple
lazy import time
lazy from pathlib import Path

from .messages import say
from .session import Phase, Stopped
from .settings import ENV_EXAMPLE, ENV_FILE, FILES_SHOWN_PER_FIX
from .text import field, first_line, one_line
lazy from .building import ToolchainRepair, Verifier
lazy from .model import Answer, OllamaError
lazy from .prompts import Part, Prompts, PromptTooLarge
lazy from .workspace import FileView, Output, TaughtCommands, TestRef, Toolchain

Tidied = namedtuple("Tidied", "change reply")
FailingExample = namedtuple("FailingExample", "command expected output")
Example = namedtuple("Example", "command lines")
Comparison = namedtuple("Comparison", "missing values")
Problem = namedtuple("Problem", "what output")
Lessons = namedtuple("Lessons", "new confirmed")


class Tidy(Phase):
    TITLE = "Tidy (the linter over the code)"
    BUILDS = True
    MAX_FILES_PER_PASS = FILES_SHOWN_PER_FIX

    def run(self, cycle):
        if not self.session.toolchain.lint:
            say.skip("No lint command: nothing to tidy")
            return None
        output = self.lint()
        reported = self.reported(output)
        if not reported:
            self.say_nothing_to_tidy(output)
            return None
        say.info(f"$ {self.session.toolchain.lint}: problems in {', '.join(reported)}", "🧹")
        passing = Verifier(self.session).passing()
        for path in reported:
            self.tidy(path, output, passing)
        left = self.reported(self.lint())
        if left:
            say.warn(f"The linter still reports problems in {', '.join(left)}")
        else:
            say.done("The linter finds no problem now")
        return None

    @staticmethod
    def say_nothing_to_tidy(output):
        if not output:
            say.done("The linter finds no problem")
        else:
            say.warn(f"The linter reports problems, in no file Tinker wrote: {Output.key_line(output)}")

    def lint(self):
        self.session.format_code()
        ok, output = self.session.toolchain.run_lint(self.workspace)
        return "" if ok else output

    def reported(self, output):
        counted = (
            [(self.findings(output, path), path) for path in self.session.files_on_disk("code", "test")]
            if output
            else []
        )
        return [path for count, path in sorted(counted, key=lambda pair: -pair[0]) if count][
            : self.MAX_FILES_PER_PASS
        ]

    @staticmethod
    def findings(output, path):
        return sum(
            1 for match in Output.FILE_LOCATION_RE.finditer(output) if Output.names(match.group(1), path)
        )

    def tidy(self, path, output, passing):
        said = self.said_about(output, path)
        lines = self.workspace.read(path).splitlines()
        numbered = "\n".join(f"{number:>4}| {line}" for number, line in enumerate(lines, 1))
        parts = [
            Part(f"Linter output for {path}", Output.failure_text(said), True),
            Part(f"File {path}, lines 1-{len(lines)} (numbered)", numbered, True, exact=True),
            Part("Language", self.project.language),
        ]
        try:
            change, reply = self.tidied(path, parts, len(lines))
        except PromptTooLarge as exc:
            say.skip(f"Tidy {path}: the prompt does not fit ({exc})")
            return
        self.keep_if_better(path, change, reply, output, passing)

    @staticmethod
    def said_about(output, path):
        blocks = [block for block in re.split(r"\n\s*\n", output) if Output.names(block, path)]
        return "\n\n".join(blocks) or output

    def tidied(self, path, parts, lines):
        change, reply = None, None
        for think in (False, None):
            reply = self.session.ask(
                Prompts.TIDY_SYSTEM, parts, f"Tidy {path}", max_reply=self.session.model.fix_max, think=think
            )
            change = self.workspace.apply(
                reply, lambda target, exists: "only the lines shown may change", {path: (1, lines)}
            )
            if change:
                break
        return Tidied(change, reply)

    def keep_if_better(self, path, change, reply, output, passing):
        if not change:
            say.skip(f"Tidy {path}: no usable change")
        elif not self.fewer_findings(path, output):
            self.workspace.revert(change)
            say.warn(f"Tidy {path}: the linter found as much or more in it afterwards; reverted")
        elif self.broke(change, passing):
            say.warn(f"Tidy {path}: it broke a passing test; reverted")
        else:
            say.done(f"Tidy {path}: changed  ({reply.cause() or 'lint problems fixed'})")

    def fewer_findings(self, path, before):
        return self.findings(self.session.toolchain.run_lint(self.workspace)[1], path) < self.findings(
            before, path
        )

    def broke(self, change, passing):
        self.session.last_all_tests_run = None
        tests_kept = all(
            TestRef.name_at(self.workspace.read(path), name) >= 0
            for path in change.files
            for name in self.project.test_names(path)
        )
        if tests_kept and passing <= Verifier(self.session).passing():
            return False
        self.workspace.revert(change)
        self.session.last_all_tests_run = None
        return True


class ReadmeWriter(Phase):
    TITLE = "README (and its examples run, as a user would)"
    BUILDS = True
    MAX_USAGE_EXAMPLES = 12
    EXAMPLE_FIX_ROUNDS = 2
    SERVER_ANSWER_TIMEOUT = 20
    SERVER_ADDRESS_RE = re.compile(r"\b(127\.0\.0\.1|localhost)\b")
    VARYING_VALUE_RE = re.compile(r"<[^<>\n]+>")

    def run(self, cycle):
        readme = next(
            (path for path, info in self.project.files.items() if info.get("kind") == "docs"), "README.md"
        )
        if not self.workspace.exists(readme):
            guide = self.guide()
            cycle["serves"] = guide["serves"]
            self.workspace.write(readme, self.text(guide))
            self.project.register([readme], "docs", "what it does, how to use, set up, run and test it")
            say.done(f"Wrote {readme}")
            self.session.save()
        self.check(cycle, readme)
        return None

    def guide(self):
        try:
            answer = self.session.model.ask_until_usable(
                Prompts.README_SYSTEM, self.readme_parts(), "README", self.read, attempts=2, think=False
            )
        except OllamaError as exc:
            answer = Answer(None, why=str(exc))
        if answer.why:
            say.warn(f"README: {first_line(answer.why, 120)}; its features are the spec's criteria")
        return answer.value or self.criteria_readme()

    def readme_parts(self):
        code = self.code_files()
        entry = self.project.entry_files(code)
        return [
            Part("Request", self.project.run["task"], True, keep="head"),
            Part(
                "What it does (its tested criteria)",
                Part.bullets(criterion["behaviour"] for criterion in self.project.criteria),
                True,
            ),
            Part(
                "What a person runs once to use it",
                "\n".join(self.session.toolchain.use) or "(nothing: the language as installed is enough)",
                True,
            ),
            Part(
                "How a person starts it, after that",
                self.session.toolchain.run or "(no entry point: it is used from code)",
                True,
            ),
            Part("Language", self.project.language, True),
            *[
                Part(f"File {path}", self.workspace.read(path), atomic=True)
                for path in dict.fromkeys(entry + code)
            ],
        ]

    def code_files(self):
        return [path for path in self.session.files_on_disk("code", "template") if path != ENV_FILE]

    def criteria_readme(self):
        return {
            "overview": "",
            "features": [criterion["behaviour"] for criterion in self.project.criteria],
            "usage": [],
            "serves": False,
        }

    def read(self, reply):
        data = reply.json()
        features = [one_line(feature) for feature in data.get("features") or [] if one_line(feature)]
        entries = [use for use in data.get("usage") or [] if isinstance(use, dict)]
        usage = [
            {
                "what": one_line(use.get("what")),
                "command": one_line(use.get("command")),
                "output": str(use.get("output") or "").strip("\n"),
            }
            for use in entries
            if one_line(use.get("what")) and one_line(use.get("command"))
        ]
        if not features or not usage:
            raise ValueError("it needs the features and at least one usage example")
        serves = str(data.get("serves")).lower() in ("true", "yes") or any(
            self.SERVER_ADDRESS_RE.search(use["command"]) for use in usage
        )
        return {
            "overview": field(data, "overview"),
            "features": features,
            "usage": usage[: self.MAX_USAGE_EXAMPLES],
            "serves": serves,
            "requires": one_line(data.get("requires")),
        }

    def text(self, guide):
        sections = [
            f"# {self.project.workdir.name}",
            guide["overview"] or self.project.run["task"].strip(),
            "## Features",
            Part.bullets(guide["features"]),
        ]
        started = self.getting_started(guide)
        sections += ["## Getting started", *[step for step in started if step]] if any(started) else []
        if guide["usage"]:
            sections += [
                "## Usage",
                "\n\n".join(
                    f"- {use['what']}:\n\n```console\n$ {spelled(use['command'])}\n{use['output']}".rstrip()
                    + "\n```"
                    for use in guide["usage"]
                ),
            ]
        if self.workspace.exists(ENV_EXAMPLE):
            sections += [
                "## Settings",
                f"Copy `{ENV_EXAMPLE}` to `{ENV_FILE}` and fill in what it asks for "
                f"(environment variables override it):",
                "```\n" + self.workspace.read(ENV_EXAMPLE).strip() + "\n```",
            ]
        return "\n\n".join(sections + self.development()) + "\n"

    def getting_started(self, guide):
        tc = self.session.toolchain
        started = [f"It needs {guide['requires']}." if guide.get("requires") else ""]
        started += ["Once, in the project folder:", shell_block(*tc.use)] if tc.use else []
        if tc.run and guide["serves"]:
            started.append(
                f"Start it with `{spelled(tc.run)}`; it keeps running, so use it from another terminal."
            )
        elif tc.run:
            started.append(f"Run it with `{spelled(tc.run)}`, followed by what you want it to do.")
        return started

    def development(self):
        tc = self.session.toolchain
        setup = tc.setup[:1] + [tc.activate] + tc.setup[1:] if tc.activate and tc.setup else tc.setup
        sections = [
            "## Development",
            "To change it, set it up once for development:",
            shell_block(*setup),
            (f"In a new terminal, first `{spelled(tc.activate)}`. " if tc.activate else "")
            + "Run the tests:",
            shell_block(tc.test_all),
        ]
        if tc.format or tc.lint:
            sections += ["Format the code and check it with the linter:", shell_block(tc.format, tc.lint)]
        return sections

    def check(self, cycle, readme):
        note = ""
        for round_number in range(self.EXAMPLE_FIX_ROUNDS + 1):
            failures = self.failing(cycle, readme)
            if not failures:
                say.done(f"Every example in {readme} does what it says")
                return
            for command, _expected, _output in failures:
                say.fail(f"{readme}: `{first_line(command, 80)}` does not print what it says")
            if round_number == self.EXAMPLE_FIX_ROUNDS:
                break
            note = self.fix(readme, failures, note)
        say.warn(f"{len(failures)} example(s) in {readme} still do not do what it says (see SPEC.md)")
        cycle["readme_failures"] = [
            f"`{command}`: {first_line(output, 160)}" for command, _, output in failures
        ]

    def failing(self, cycle, readme):
        examples = self.examples(self.workspace.read(readme))
        tc = self.session.toolchain
        as_a_person_runs_it = lambda command: " && ".join(tc.use + [command])
        run = TaughtCommands.spelled_out(tc.run) if tc.run else ""
        serves = run and (
            cycle.get("serves") or any(self.SERVER_ADDRESS_RE.search(command) for command, _ in examples)
        )
        starts_it = lambda example: serves and example[0] == run
        examples = [example for example in examples if not starts_it(example)]
        if not examples:
            return []
        server = self.workspace.start(as_a_person_runs_it(tc.run)) if serves else None
        try:
            if server:
                self.wait_for(examples[0])
            return self.run_examples(examples, as_a_person_runs_it)
        finally:
            if server:
                self.workspace.stop(server[0])

    @staticmethod
    def examples(text):
        usage = text.split("## Usage", 1)[1].split("\n## ", 1)[0] if "## Usage" in text else ""
        found = []
        for block in re.findall(r"```console\n(.*?)```", usage, re.S):
            for line in block.splitlines():
                if line.startswith("$ "):
                    found.append(Example(line[2:].strip(), []))
                elif found and line.strip():
                    found[-1][1].append(line.strip())
        return found

    def wait_for(self, example):
        began = time.monotonic()
        while time.monotonic() - began < self.SERVER_ANSWER_TIMEOUT:
            code, output = self.workspace.run(example[0], timeout=10, quiet=True)
            if code == 0 and not self.missing(example[1], output):
                return
            time.sleep(1)

    def run_examples(self, examples, ready):
        failures, values = [], {}
        for command, expected in examples:
            filled = self.VARYING_VALUE_RE.sub(
                lambda match: values.get(match.group(0), match.group(0)), command
            )
            code, output = self.workspace.run(ready(filled), timeout=Toolchain.test_timeout(), quiet=True)
            missing, shown = self.compare(expected, output)
            values.update(shown)
            if code is None or missing:
                failures.append(
                    FailingExample(
                        command,
                        expected,
                        output
                        if code is not None
                        else f"{output}\n(it did not finish within {Toolchain.test_timeout()}s)",
                    )
                )
        return failures

    @classmethod
    def missing(cls, expected, output):
        return cls.compare(expected, output)[0]

    @classmethod
    def compare(cls, expected, output):
        said = ["".join(line.split()) for line in output.splitlines()]
        values, missing = {}, []
        for line in expected:
            if "..." in line:
                continue
            compact = "".join(line.split())
            found = next((match for match in map(cls.line_pattern(compact).fullmatch, said) if match), None)
            if found:
                values.update(zip(cls.VARYING_VALUE_RE.findall(compact), found.groups()))
            else:
                missing.append(line)
        return Comparison(missing, values)

    @classmethod
    def line_pattern(cls, compact):
        parts = [re.escape(part) for part in cls.VARYING_VALUE_RE.split(compact)]
        return re.compile(parts[0] + "".join(r"(\S+?)" + part for part in parts[1:]))

    def fix(self, readme, failures, note):
        view = self.fix_prompt(readme, failures, note)
        passing = Verifier(self.session).passing()
        try:
            reply = self.session.ask(
                Prompts.README_FIX_SYSTEM,
                view,
                "Fix the README's examples",
                max_reply=self.session.model.fix_max,
            )
        except PromptTooLarge as exc:
            say.skip(f"README fix: the prompt does not fit ({exc})")
            return ""
        change = self.workspace.apply(
            reply,
            lambda path, exists: (
                None
                if exists and path in view.shown and self.project.kind_of(path) != "test"
                else "only the files shown change"
            ),
            view.windows,
        )
        return self.kept(change, reply, passing)

    def fix_prompt(self, readme, failures, note):
        shown = "\n\n".join(
            f"$ {command}\nThe README says it prints:\n"
            + ("\n".join(expected) or "(nothing)")
            + f"\nIt printed:\n{Output.failure_text(output, 1200)}"
            for command, expected, output in failures
        )
        code = self.code_files()
        fixed = [
            Part("Request", self.project.run["task"], True, keep="head"),
            Part("Examples that do not do what the README says", shown, True, keep="head"),
            Part("Note", note, place="last"),
        ]
        return FileView(
            self.workspace,
            fixed,
            [readme] + self.project.entry_files(code) + code,
            [Part("Language", self.project.language)],
            output=shown,
            hints=shown,
        )

    def kept(self, change, reply, passing):
        if not change:
            why = "; ".join(change.notes) or "it changed no file shown"
            say.skip(f"README fix: no usable change ({first_line(why, 160)})")
            return f"Your last reply could not be used: {why}. Give each file you change complete."
        self.session.last_all_tests_run = None
        if not passing <= Verifier(self.session).passing():
            self.workspace.revert(change)
            say.warn("README fix: it broke a passing test; reverted")
            return "Your last change broke a passing test and was undone."
        say.done(f"README fix: changed {', '.join(change.files)}  ({reply.cause() or '?'})")
        return ""


class FinalReview(Phase):
    TITLE = "Final review (the project as a person runs it)"
    BUILDS = True

    def run(self, cycle):
        problem, output = self.problem()
        if problem and not self.by_chance(problem) and ToolchainRepair(self.session).run(output, set()):
            problem, output = self.problem()
        if problem:
            say.fail(f"Not done: {problem}")
            return Stopped("review", problem)
        green, _, total = self.project.counts()
        say.done(
            f"Set up afresh; `{self.session.toolchain.test_all}` passes; "
            f"{green}/{total} spec points tested and passing"
        )
        return True

    @staticmethod
    def by_chance(problem):
        return "passed once and failed" in problem

    def problem(self):
        ok, output = self.session.setup(force=True)
        if not ok:
            return Problem(f"setup fails from the project's own files: {Output.key_line(output)}", output)
        self.session.wait_for_network()
        return self.suite_problem()

    def suite_problem(self):
        self.session.last_all_tests_run = None
        code, output = self.session.run_all_tests()
        if code != 0:
            return Problem(
                f"`{self.session.toolchain.test_all}` fails when the whole suite runs: "
                f"{Output.key_line(output)}",
                output,
            )
        again, output_again = self.session.toolchain.run_all(self.workspace)
        if again != 0:
            return Problem(
                f"`{self.session.toolchain.test_all}` passed once and failed the next time: "
                f"a test depends on chance (random output) or on order: "
                f"{Output.key_line(output_again)}",
                output_again,
            )
        if Toolchain.ran_no_test(output):
            return Problem(f"`{self.session.toolchain.test_all}` runs no test", output)
        untested = [
            criterion["id"]
            for criterion in self.project.criteria
            if (not TestRef(criterion["test"]).present(self.workspace) or criterion["status"] != "green")
        ]
        if untested:
            return Problem(f"spec points without a passing test: {', '.join(untested)}", output)
        return Problem("", "")


class LessonWriter(Phase):
    TITLE = "Lessons (for the next projects)"

    def run(self, cycle):
        events = self.project.spec.get("events", [])
        if not cycle.get("proven"):
            self.prove_what_was_used(cycle)
        if cycle["id"] == "base" and not cycle.get("counted"):
            self.keep_the_toolchain(cycle)
        if not events:
            say.skip("No problems to learn from")
            return
        try:
            new, confirmed = self.ask_lessons(events)
        except (SystemExit, OllamaError) as exc:
            say.warn(f"{exc} Nothing learned this time.")
            return
        names = [Path(path).name for path in self.project.files] + [self.project.workdir.name]
        added = self.session.knowledge.add_lessons(new, confirmed, names)
        say.done(f"{added} new lesson(s), {len(confirmed)} confirmed: {self.session.knowledge.summary()}")
        self.project.spec["events"] = []
        self.session.save()

    def prove_what_was_used(self, cycle):
        proven = self.session.knowledge.prove(cycle.get("used", {}))
        cycle["proven"] = True
        self.session.save()
        if proven:
            say.done(f"{proven} lesson(s) and fact(s) used here are proven now")

    def keep_the_toolchain(self, cycle):
        self.session.knowledge.remember_toolchain(self.session.toolchain)
        self.session.knowledge.finished_project()
        cycle["counted"] = True
        self.session.save()

    def ask_lessons(self, events):
        known = self.session.knowledge.known_lessons()
        return self.session.ask_until_usable(
            Prompts.LESSONS_SYSTEM,
            [
                Part("Language", self.project.language, True),
                Part(
                    "Problems and how they were solved",
                    "\n".join(f"- [{event['kind']}] {event['text']}" for event in events),
                    True,
                    keep="tail",
                ),
                Part(
                    "Known lessons",
                    "\n".join(f"{number}. {lesson['text']}" for number, lesson in enumerate(known, 1)),
                ),
            ],
            "Lessons",
            self.read,
            attempts=2,
        )

    @staticmethod
    def read(reply):
        data = reply.json()
        lessons = [item for item in data.get("lessons", []) if isinstance(item, dict)]
        confirmed = [number for number in data.get("confirmed", []) if isinstance(number, int)]
        return Lessons(lessons[:3], confirmed)


def spelled(command):
    return TaughtCommands.spelled_out(command)


def shell_block(*commands):
    return "```sh\n" + "\n".join(spelled(command) for command in commands if command) + "\n```"
