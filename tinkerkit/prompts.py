import re
from collections import namedtuple
lazy import json
lazy from pathlib import Path

from .settings import (
    ENV_EXAMPLE,
    ENV_FILE,
    FACT_PHASES,
    FACTS_PER_CHUNK,
    FACTS_PER_SLOT,
    LESSON_PHASES,
    MAX_FILE_LINES,
    MAX_RULE_CHARS,
    MAX_VERSION_COMMANDS,
    MIN_TRIMMED_PROSE_CHARS,
    PLAYBOOK_RULES,
    READ_FACTS_PER_CHAPTER,
    READ_KEY_TERMS,
    RESEARCH_FACTS_CHECKED,
    RESEARCH_GAPS,
    RESEARCH_QUESTIONS,
    SEARCHES_PER_RESEARCH_QUESTION,
    TEACH_CHECK_COMMANDS,
    TEACH_CHECK_SEARCHES,
    TEACH_CLAIMS,
    TOOL_ROLES,
    first_line,
)

StringStates = namedtuple("StringStates", "states in_string")
FileBlock = namedtuple("FileBlock", "path content")
LineEdit = namedtuple("LineEdit", "path first last lines")


class Reply:
    THINK_RE = re.compile(r"<think>.*?</think>", re.S)
    HEADER_PATH = r"[ \t:]+(?:(?:path|file)[ \t]*[=:][ \t]*)?[\"'`]?([^\s<>\"'`]+?)[\"'`]?"
    FILE_BLOCK_RE = re.compile(
        r"<<<FILE" + HEADER_PATH + r"[ \t]*(?:>>>[ \t]*([^\s<>]*))?[ \t]*\n(.*?)\n?<<<END>>>", re.S
    )
    LINES_BLOCK_RE = re.compile(
        r"<<<LINES" + HEADER_PATH + r"[ \t]+(?:lines[ \t]*=[ \t]*)?(\d+)\s*-\s*(\d+)[ \t]*>>>[ \t]*\n"
        r"(.*?)\n?<<<END>>>",
        re.S,
    )
    NUMBERED_RE = re.compile(r"^\s*\d+\| ?")
    PLACEHOLDER_PATHS = {"path", "path/to/file", "file", "filename", "key"}
    PLACEHOLDER_BODY_RE = re.compile(
        r"\s*[<\[(]?\s*(the\s+)?(complete\s+|whole\s+|full\s+)?(file\s+)?(content|contents|"
        r"code|text|file|\.\.\.|…)\s*(here|goes here)?\s*[>\])]?\s*",
        re.I,
    )
    RELATIVE_PATH_LINE_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.[\w]+|[\w.-]+(?:/[\w.-]+)+")
    OBJECT_KEY_RE = re.compile(r'"(?:[^"\\]|\\.)*"\s*:')
    STRING_CONCATENATION_RE = re.compile(r'(?<!\\)"\s*\+\s*"')
    BLOCK_MARKS_ONLY_RE = re.compile(r"\s*(<{2,}|>{2,}|<<<\w*>>>)\s*")
    MARKDOWN_FENCE_RE = re.compile(r"\s*(`{3,}|~{3,})[\w+#.-]*\s*")
    ESCAPED_SINGLE_QUOTE_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\'")

    def __init__(self, raw, thought=""):
        self.text = self.THINK_RE.sub("", raw or "").strip()
        self.thought = thought

    def json(self):
        start, end = self.text.find("{"), self.text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in reply")
        return self.first_parsed(self.json_candidates(self.text[start : end + 1], self.text[start:]))

    def json_candidates(self, whole, to_the_end):
        to_the_end = to_the_end.rstrip().rstrip("`").rstrip()
        return [
            whole,
            self.mended(whole),
            self.closed(self.mended(to_the_end)),
            self.balanced(self.mended(to_the_end)),
        ]

    @staticmethod
    def first_parsed(candidates):
        problem = None
        for candidate in filter(None, candidates):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as exc:
                problem = problem or exc
        raise problem

    @classmethod
    def mended(cls, text):
        return cls.strip_trailing_commas(
            cls.ESCAPED_SINGLE_QUOTE_RE.sub(r"\1'", cls.STRING_CONCATENATION_RE.sub("", text))
        )

    @staticmethod
    def closed(text):
        inside, ends_inside = Reply.string_states(text)
        stack = []
        for char, in_string in zip(text, inside):
            if in_string or char not in "{[]}":
                continue
            if char in "{[":
                stack.append(Reply.closer(char))
            elif not stack or stack.pop() != char:
                return None
        if ends_inside or not stack:
            return None
        return text.rstrip().rstrip(",") + "".join(reversed(stack))

    @staticmethod
    def balanced(text):
        inside, ends_inside = Reply.string_states(text)
        out, stack = [], []
        for index, (char, in_string) in enumerate(zip(text, inside)):
            if in_string:
                pass
            elif char == '"' and stack and stack[-1] == "]" and Reply.OBJECT_KEY_RE.prefixmatch(text, index):
                Reply.close_list_before_last_comma(out, stack)
            elif char in "{[":
                stack.append(Reply.closer(char))
            elif char in "}]":
                if char not in stack:
                    continue
                Reply.close_until(char, out, stack)
            out.append(char)
        return None if ends_inside else "".join(out).rstrip().rstrip(",") + "".join(reversed(stack))

    @staticmethod
    def closer(opener):
        return "}" if opener == "{" else "]"

    @staticmethod
    def close_list_before_last_comma(out, stack):
        comma = "".join(out).rstrip().rfind(",")
        if comma >= 0:
            out.insert(comma, stack.pop())

    @staticmethod
    def close_until(char, out, stack):
        while stack[-1] != char:
            out.append(stack.pop())
        stack.pop()

    @staticmethod
    def strip_trailing_commas(text):
        inside, _ = Reply.string_states(text)
        return "".join(
            char
            for index, (char, in_string) in enumerate(zip(text, inside))
            if in_string or not (char == "," and text[index + 1 :].lstrip()[:1] in ("]", "}"))
        )

    @staticmethod
    def string_states(text):
        states, in_string, escaped = [], False, False
        for char in text:
            states.append(in_string)
            if in_string:
                escaped, in_string = (False, True) if escaped else (char == "\\", char != '"')
            elif char == '"':
                in_string = True
        return StringStates(states, in_string)

    def spec_json(self):
        try:
            return self.json()
        except ValueError as problem:
            data = self.spec_with_data_as_text()
            if data is None:
                raise problem
            return data

    def spec_with_data_as_text(self):
        start, at = self.text.find("{"), self.text.rfind('"data"')
        if start < 0 or at <= start:
            return None
        try:
            data = json.loads(self.strip_trailing_commas(self.text[start:at].rstrip().rstrip(",") + "}"))
        except ValueError:
            return None
        data["data"] = self.text[at:].split(":", 1)[-1].strip().rstrip("}").strip()
        return data

    def files(self):
        found = []
        for path, after, body in self.FILE_BLOCK_RE.findall(self.text):
            path, body = self.real_path_and_body(path.strip(), after, body)
            if self.PLACEHOLDER_BODY_RE.fullmatch(body):
                continue
            markdown = path.lower().endswith((".md", ".markdown"))
            found.append(FileBlock(str(Path(path)), self.without_debris(body, fences=not markdown)))
        return found

    def real_path_and_body(self, path, after, body):
        first, _, rest = body.partition("\n")
        if path.lower() in self.PLACEHOLDER_PATHS and self.RELATIVE_PATH_LINE_RE.fullmatch(after):
            return after, body
        if after:
            return path, after + "\n" + body
        if path.lower() in self.PLACEHOLDER_PATHS and self.RELATIVE_PATH_LINE_RE.fullmatch(first.strip()):
            return first.strip(), rest
        return path, body

    @classmethod
    def without_debris(cls, body, fences=True):
        lines = cls.without_mark_lines(body.split("\n"))
        return "\n".join(cls.unfenced(lines) if fences else lines)

    @classmethod
    def without_mark_lines(cls, lines):
        while lines and cls.BLOCK_MARKS_ONLY_RE.fullmatch(lines[-1]):
            lines.pop()
        while lines and cls.BLOCK_MARKS_ONLY_RE.fullmatch(lines[0]):
            lines.pop(0)
        return lines

    @classmethod
    def unfenced(cls, lines):
        filled = [index for index, line in enumerate(lines) if line.strip()]
        if cls.one_fence_around(lines, filled):
            return lines[filled[0] + 1 : filled[-1]]
        return lines

    @classmethod
    def one_fence_around(cls, lines, filled):
        fence_at = lambda index: cls.MARKDOWN_FENCE_RE.fullmatch(lines[index])
        return (
            len(filled) >= 2
            and fence_at(filled[0])
            and fence_at(filled[-1])
            and not any(fence_at(index) for index in filled[1:-1])
        )

    def as_file(self, path):
        if self.files():
            return self
        blocks = re.findall(r"^(`{3,}|~{3,})[\w+#.-]*[ \t]*\n(.*?)^\1[ \t]*$", self.text, re.S | re.M)
        return Reply(f"<<<FILE {path}>>>\n{blocks[0][1].rstrip()}\n<<<END>>>") if len(blocks) == 1 else self

    def line_edits(self):
        return [
            LineEdit(
                str(Path(path.strip())),
                int(first),
                int(last),
                self.unnumbered(self.without_debris(body).splitlines()),
            )
            for path, first, last, body in self.LINES_BLOCK_RE.findall(self.text)
        ]

    @classmethod
    def unnumbered(cls, lines):
        if lines and all(cls.NUMBERED_RE.prefixmatch(line) or not line.strip() for line in lines):
            return [cls.NUMBERED_RE.sub("", line, count=1) for line in lines]
        return lines

    def named_paths(self):
        return list(
            dict.fromkeys([path for path, _ in self.files()] + [edit[0] for edit in self.line_edits()])
        )

    def cause(self):
        return first_line(self.marked("CAUSE"), 160)

    def test_wrong(self):
        return first_line(self.marked("TEST WRONG"), 200)

    def criterion(self):
        return self.marked("CRITERION").strip()

    def marked(self, mark):
        match = re.search(rf"^\s*{mark}:\s*(.+)$", self.text, re.M)
        return match.group(1) if match else ""

    def corrected_criteria(self):
        return {
            name: text.strip()
            for name, text in re.findall(r"^\s*CRITERION\s+([^\s:]+):\s*(.+)$", self.text, re.M)
        }

    def toolchain_line(self):
        match = re.search(r"^\s*TOOLCHAIN:\s*(\{.*\})\s*$", self.text, re.M)
        return json.loads(match.group(1)) if match else None

    @staticmethod
    def files_ended(content):
        end = content.rfind("<<<END>>>")
        rest = content[end + 9 :] if end >= 0 else ""
        return end >= 0 and "<<<FILE" not in rest and "<<<LINES" not in rest

    @staticmethod
    def files_complete(content):
        return Reply.files_ended(content) and len(content[content.rfind("<<<END>>>") + 9 :].strip()) >= 300

    @staticmethod
    def wrote(paths):
        wanted = {str(Path(path)) for path in paths}

        def done(content):
            return wanted <= {path for path, _ in Reply(content).files()} or Reply.files_complete(content)

        return done

    @staticmethod
    def json_complete(content):
        start = content.find("{")
        if start < 0 or not content.rstrip().endswith("}"):
            return False
        try:
            json.loads(Reply.strip_trailing_commas(content[start:].strip()))
            return True
        except ValueError:
            return False


class Part:
    SHORTEST_KEPT_CHARS = 150
    PLACES = {"first": 0, None: 1, "last": 2}

    def __init__(self, title, text, required=False, keep="head", exact=False, atomic=False, place=None):
        self.title, self.text, self.required, self.keep = title, (text or "").strip(), required, keep
        self.exact = bool(exact and required)
        self.atomic = bool(atomic and not required)
        self.place = place

    @staticmethod
    def bullets(lines):
        return "\n".join(f"- {line}" for line in lines)

    @staticmethod
    def total_size(parts):
        return sum(part.size() for part in parts if part.text or part.required)

    @staticmethod
    def assemble(parts, limit, notes=None):
        parts = Part.shorten_required(sorted(parts, key=lambda part: Part.PLACES[part.place]), limit, notes)
        room = limit - Part.required_size(parts)
        blocks = []
        for part in parts:
            if part.required:
                blocks.append(part.render())
            elif part.text and (fitted := part.fitted(room)):
                blocks.append(fitted.render())
                room = room - part.size() if fitted is part else 0
        return "\n\n".join(blocks)

    def fitted(self, room):
        if self.size() <= room:
            return self
        blank_line_before = 2
        text_room = room - len(self.render("")) - blank_line_before
        if not self.atomic and text_room >= Part.SHORTEST_KEPT_CHARS:
            return self.shortened(text_room)
        return None

    @staticmethod
    def shorten_required(parts, limit, notes):
        trimmed = set()
        while Part.required_size(parts) > limit:
            index = Part.longest_prose(parts, limit)
            parts[index] = parts[index].shortened(
                max(MIN_TRIMMED_PROSE_CHARS, len(parts[index].text) - (Part.required_size(parts) - limit))
            )
            trimmed.add(parts[index].title)
        if trimmed and notes is not None:
            notes.extend(sorted(trimmed))
        return parts

    @staticmethod
    def required_size(parts):
        return sum(part.size() for part in parts if part.required)

    @staticmethod
    def longest_prose(parts, limit):
        prose = [
            index
            for index, part in enumerate(parts)
            if part.required and not part.exact and len(part.text) > MIN_TRIMMED_PROSE_CHARS
        ]
        if not prose:
            exact = ", ".join(part.title for part in parts if part.exact) or "none"
            raise PromptTooLarge(
                f"required prompt parts need {Part.required_size(parts)} chars; budget allows "
                f"{limit} (parts that cannot be shortened: {exact})"
            )
        return max(prose, key=lambda chosen: len(parts[chosen].text))

    def render(self, text=None):
        text = self.text if text is None else text
        return f"## {self.title}\n{text}" if self.title else text

    def size(self):
        return len(self.render()) + 2

    def floor_size(self):
        return self.size() if self.exact else min(self.size(), MIN_TRIMMED_PROSE_CHARS + len(self.title) + 12)

    def shortened(self, room):
        marker = "[...]"
        room -= len(marker) + 1
        text = (
            ""
            if room <= 0
            else (
                self.text[:room] + "\n" + marker if self.keep == "head" else marker + "\n" + self.text[-room:]
            )
        )
        return Part(self.title, text, self.required, self.keep, self.exact, self.atomic, self.place)

    def relaxed(self):
        return Part(
            self.title, self.text, self.required, self.keep, exact=False, atomic=self.atomic, place=self.place
        )


class PromptTooLarge(Exception):
    pass


class Prompts:
    NO_TOOL_CALLS = "\nReply in plain text; never call a tool or function."
    FOLDER_NAME_SYSTEM = (
        "Name the folder of the project the person asks for: one to three short lowercase words "
        "joined by hyphens, at most 20 characters, the shorter the better; shorten well-known "
        "words (calc-py for a calculator in Python, todo for a todo app). Reply with the name "
        "only."
    )
    SAME_CASES = (
        "Several similar cases (every line, every unit, each row of a table) are built from the values "
        "the criterion names, with a loop or a small helper, never written out by hand one by one: a long "
        "hand-made literal (a grid, a board, a list of positions) is where counting slips."
    )
    FILE_BLOCK_FORMAT = "<<<FILE path>>>\nthe complete file\n<<<END>>>"

    FILES_REPLY = (
        f'Reply with a line "CAUSE: <one sentence>", then each file you change, complete:\n'
        f"{FILE_BLOCK_FORMAT}\n"
        f"Only the files shown may change; the project's other files exist. Files stay under "
        f"{MAX_FILE_LINES} lines."
    )

    LINES_HOWTO = (
        "A file shown as numbered lines is changed with: <<<LINES path A-B>>>, then the new text for its "
        "lines A to B (within the range shown, without the line numbers), then <<<END>>>. Give a whole "
        "file only for files shown in full."
    )

    DEPENDENCIES_TITLE = (
        "Third-party dependencies (the only ones it may import; everything else is the standard "
        "library or the project's own files)"
    )

    SPEC_JSON = (
        '{"language":"Python",\n'
        ' "criteria":[{"text":"tip.split(\\"60.00\\", 15, 3) is 23.00 (60.00 + 15% = 69.00, / 3)",'
        '"source":"words copied from the request","test":"tests/test_tip.py::test_split","effort":1},\n'
        ' {"text":"tip.main([\\"60.50\\", \\"--tip\\", \\"15\\", \\"--people\\", \\"3\\"]) prints \\"23.19 each\\" '
        '(60.50 + 15% = 69.575, / 3 = 23.19)","source":"...","test":"tests/test_tip.py::test_main_split","effort":1},\n'
        ' {"text":"tip.main([]) prints a clear error (the amount is missing) and exits with 1","source":"...",'
        '"test":"tests/test_tip.py::test_main_missing_amount","effort":1},\n'
        ' {"text":"tip.main([\\"abc\\"]) prints a clear error and exits with 1","source":"...",'
        '"test":"tests/test_tip.py::test_main_bad_amount","effort":1}],\n'
        ' "files":[{"path":"tip.py","kind":"code","role":"the tip calculator and its command line","uses":[]},\n'
        '  {"path":"tests/test_tip.py","kind":"test","role":"its tests","uses":["tip.py"]},\n'
        '  {"path":"pyproject.toml","kind":"config","role":"tells the test tool where the code is","uses":[]}],\n'
        ' "entry":"tip.py",\n'
        ' "constraints":["how the request says to build it"],"non_goals":["what a typical version adds unasked"],\n'
        ' "data":""'
    )

    SPEC_RULES = f"""Rules:
- build only what the request clearly asks for; everything else waits until the user asks for it, so
  the smallest project that does what is asked is the right one;
- language: the one the request names, else Python;
- criteria: each one behaviour the request asks for, or obviously needs to be correct (a calculator
  that divides needs division by zero handled), of one module, checked by ONE fast unit test through
  its public interface; "source" copies the request's words it comes from (else it is dropped); none
  for what the request does not ask: no command line, menu, server, settings, persistence or logging
  it does not ask for (a program a person runs still gets the checks below); never about how it is
  built; no repeats; usually 3 to 10; each thing the constraints say it handles (each kind, level or
  form named) is checked by a criterion, so nothing the spec promises goes untested;
- a program a person runs is tested the way it is used, through its entry point; what it shows a
  person names the things it is about (a title, a name), not only their ids. Its criteria, one each:
  1. its normal use as a person runs it, and each command, option or route the request names,
     written exactly as the request writes it, with what it prints, in words a person reads (e.g.
     main(["book", "add", "Dune", "Herbert"]) prints "Added Dune by Herbert (id 1)");
  2. a value written as a person writes it (a number with decimals), which it takes, and a computed
     number it shows written the same way: a result that does not come out even, shown rounded
     (10 / 3 shows 3.33; 80.33, never 80.33000000000004);
  3. each kind of wrong input it can meet, with a clear message and a non-zero exit, never a crash
     or silence, checked on the program as a person runs it (its message and its exit code, not
     only a function's error): an unknown command, a missing argument, a value of the wrong kind
     (a word where a number goes), and what its own rules forbid (dividing by zero) (source: the
     request's words about what that input is for);
- each setting the request names (an environment variable, a .env key) gets a criterion that it
  takes effect (source: the request's words naming it);
- a criterion that states a computed value shows its working from the request's rules and its input,
  part by part, adding up every part, e.g. "2 days late x 0.05 = 0.10", "3 x 4.50 + 2 x 1.25 = 16.00",
  so the value is right; one that plays out steps (moves, commands, events in turn) shows the state each
  step leaves, e.g. "X 1, O 2, X 4, O 5, X 7: X holds 1-4-7", so the outcome it states is the one they reach;
- a program that reads input as it goes (a prompt, a game) ends cleanly when its input runs out, never
  waiting for more or crashing: one criterion gives it input that stops early;
- "test" is "<test file path>::<test name>": a test file of its own (never inside a code file), named
  and placed as the language's test tool finds it; the code the tests call is where a test file can
  reach it (in a language whose program file cannot be used from a test file, the logic is in a
  library file the program uses);
- files: the fewest that hold the criteria: code files (one responsibility each, under {MAX_FILE_LINES}
  lines) and their test files; a dependency file only for third-party dependencies, the fewest (none
  where the standard library does it; the test tools are installed by the toolchain's setup); the
  language's own project config file when the test tool needs it to find the code (e.g. pyproject.toml
  for tests in a folder of their own), never a helper file;
  {ENV_EXAMPLE} only when the request names a setting a user provides (a key, a path, a port): it lists
  each with its default (empty if required); each setting gets a criterion that it takes effect, and a
  required one a criterion (source "setting") that without it the app says which setting to set; kind: code, test, template, config or docs;
  "uses": the project files it imports, renders or calls; no file named like a standard-library
  module or a dependency (it would be loaded instead of it);
- entry: the file a person starts to use it. What a person uses (an app, a tool, a calculator, a game,
  a service) always has one, with the simplest interface that fits: command-line arguments for a tool
  (e.g. calculator.py add 5 2 prints 7), a prompt or menu only when the request asks for one, a server
  only for a web service or API; "" only when the request asks for code that other code uses (a
  library, a module, functions). A small app is one file named for what it is (calculator.py), its
  logic and its main() together; it is split into modules only when it grows past one responsibility;
  each command or option it offers gets a criterion through the entry point;
- an external service (API, SDK) sits behind one thin adapter file (role "adapter: ...") with no
  criterion; code using it takes it as a parameter, so tests pass sample data in exactly the shape the
  service returns; "data" names each adapter function and that sample data ("" when there is none);
- effort: 1 trivial, 2 ordinary, 3 tricky."""

    TOOLCHAIN_JSON = (
        '"toolchain":{"setup":["python3 -m venv .venv","pip install pytest ruff black"],'
        '"activate":". .venv/bin/activate","use":[],"run":"python3 tip.py",'
        '"check":"python -c \\"import tip\\"","test_all":"pytest -q",'
        '"test_one":"pytest -q {file}::{name}","format":"ruff check --fix --exit-zero . && black .",'
        '"lint":"ruff check ."}'
    )

    TOOLCHAIN_RULES = (
        "toolchain: the language's usual tools, every command written the way a person types it in "
        "the project folder; setup: what prepares it for development, in order (the project's own "
        "environment when the language keeps dependencies in one, then the test framework, the "
        "formatter, the linter and the dependency file's packages), creating no project file (the files "
        "listed are written for it: never a command that starts a new project) and changing nothing "
        "outside the project folder (never the machine's default toolchain, a global install or "
        "a global setting: what is installed already is used as it is); "
        'activate: what makes a shell use that environment ("" when the language needs none), '
        "run before every command but setup's first; use: only what a person must run once to use "
        "it ([] when the language as installed is enough, e.g. a Python app on the standard library "
        "alone; else e.g. make and activate the environment and install the dependency file); run: "
        "how a person starts it after that, with no arguments (they add them), by the command name that "
        "works wherever the language is installed (e.g. python3 calculator.py), printing only what the "
        'program prints (a tool that reports its own building runs quietly), "" when there is '
        "no entry point; check quickly builds or loads every code file, so a missing import or "
        "dependency fails it; test_all and test_one: the test tool's own command as a person types "
        "it (e.g. pytest -q), never a wrapper; it must find the project's code (the project config "
        "file says where, when the tool alone does not); test_one runs one test given {file} and "
        "{name}; format formats the code with the language's formatter after the linter's own safe "
        'fixes ("" when it has none); lint: the language\'s usual linter over the code, exiting '
        'non-zero when it finds a problem, each reported with its file and line ("none" when it '
        "has none)"
    )

    SPEC_SYSTEM = f"""Plan a project before any code exists, as a small, testable spec: the code will be written to
pass exactly these tests and nothing more.
Reply with ONLY JSON (this example is for "a tip calculator in Python that splits a bill"):
{SPEC_JSON},
 {TOOLCHAIN_JSON}}}
{SPEC_RULES}
- {TOOLCHAIN_RULES}."""

    FEATURE_SPEC_SYSTEM = f"""Plan a change to an existing project (a feature or a bug fix) before any code exists, as
a small, testable spec of the change only, in the project's language. For a bug, one criterion's test
reproduces it. Keep existing behaviour. Reply with ONLY JSON:
{SPEC_JSON}}}
{SPEC_RULES}
- files: only the existing files that must change and any new ones."""

    CONTRACT_SYSTEM = f"""Write the contract of one file of a planned project before any code exists, in the project's
language: every name, signature, data shape, route, command and exact string that its criteria's tests
check or that the files using it rely on; nothing more (no extra functions, options or fallbacks). Use
the contracts shown exactly. A file that renders templates names each one, the values it gives it and
what the page must show. State several requests or calls share is kept where they all reach it, and
state that belongs to one user's flow is kept per user or session. Code using an external service
takes the adapter as a parameter (default: the adapter), so tests pass sample data; an adapter only
makes its call and returns the data. When the project has settings ({ENV_EXAMPLE}), the entry point
loads them from {ENV_FILE} at start (environment variables override; unset or empty counts as missing).
Reply with ONLY JSON: {{"contract":["sync_all(push=sync_client.push) -> int: calls push(note) for each
unsynced note, marks it synced, returns the count"],"uses":["the project files it imports, renders or calls"]}}"""

    INTERFACE_SYSTEM = """Check one file of a plan against the files it uses, before any code exists. Tests and code
follow the plan exactly, so a mismatch found now saves hours later. Check that the file uses each of them
exactly as its contract says (names, arguments, return shapes, exact strings) and that each offers what
the file needs. Correct each mismatch in the entry that is wrong.
Reply with ONLY JSON: {"problems":[{"file":"path","problem":"what is wrong, one line"}],"files":[the
corrected entries of only the files that change: path, kind, role, contract, uses]}
or {"problems":[],"files":[]} when they match."""

    TOOLCHAIN_SYSTEM = f"""Give the commands to set up, check, test and run a project with its language's usual tools.
Reply with ONLY JSON: {{{TOOLCHAIN_JSON}}} (the example is for a Python project with no entry point).
Rules: {TOOLCHAIN_RULES}."""

    TOOLCHAIN_FIX_SYSTEM = (
        f"The project's tools do not work: the output shows a tooling problem (a missing test "
        f"framework, dependency or command), not a failing test. Fix the setup only: "
        f"give each dependency or config file you change complete, and/or a line TOOLCHAIN: {{json}} "
        f"with the corrected commands. Never change code or tests. {TOOLCHAIN_RULES}.\n"
        f'Reply with a line "CAUSE: <one sentence>", then the changes:\n{FILE_BLOCK_FORMAT}'
    )

    TESTS_REPLY = f"""Reply with the test file, complete:
{FILE_BLOCK_FORMAT}
Rules: one test per criterion with exactly the given name; assert only what the criterion and the
contracts state, and exactly: a value or line it states equals what is printed or returned (the whole
line, not a part found in it: "80.33" in "80.33000000000004" passes wrongly), a clear error by what it
names (the bad value, the missing thing) and its exit code; what a criterion says is shown or printed
(rounded, worded) is checked on what the program prints, whatever function it names, so every part
of it is enforced; a test of something random checks what always holds (its length, which
characters it may use), never what holds only by chance (that each kind appears), unless the app
guarantees it; the file is self-contained: sample data
is defined in it and passed where the app takes an adapter, so nothing outside the app runs (no
network, no real settings, no fake servers, no helper files); never replace the app's own modules,
classes or functions with fakes or copies of their logic (only an adapter to an external service is replaced, by sample data): a test of a fake tests nothing;
a test that runs the app as its own process runs it as the run command does (from the project folder,
with the project's code found there); every test finishes within seconds and leaves nothing running or
behind: a file it makes (a
cache, a database, a settings file) goes in a temporary folder of its own, so no test sees another's:
the app runs in that folder (its working directory) or is pointed there by a setting the criterion
names, and is called exactly as the criterion writes it (never with an argument it does not show);
keep each test short. A criterion whose own value is wrong by the request's rules (a miscount, a
miscalculation) is tested with the right value and corrected, so the spec and its test say the same:
before the file, a line "CRITERION <test name>: <the criterion as it should read>"."""

    SPEC_TESTS_SYSTEM = (
        """Write the spec tests for the given criteria before the code exists, in the project's language
and test framework (as the test command runs them). Each test checks its criterion through the public
interface of the module the contracts name: the code will be written to pass these tests and nothing more.
"""
        + SAME_CASES
        + "\n"
        + TESTS_REPLY
    )

    REPRODUCE_SYSTEM = f"""A user reports a bug in a project that is built and whose tests pass. Write ONE test that
shows the bug: it checks the correct behaviour the report implies, so it fails on the code as it is now
and passes once the bug is fixed. It tests through the app's public interface, as the project's other
tests do, with sample data in place of each adapter: nothing outside the app runs. It goes in a NEW
test file next to the project's tests; the file and the test are named as the project's test tool
expects. Reply with a line "TEST: <test file>::<test name>", a line "EXPECTED: <the correct behaviour,
one sentence>", then the test file, complete:
{FILE_BLOCK_FORMAT}
Keep it short."""

    CODE_SYSTEM = f"""Write one file of a project, in its language. It must fulfil its contract exactly, make the
given tests pass, meet its criteria and do nothing more: no extra functions, options, fallbacks, logging
or abstraction: only what the tests need. Make the code right, not the tests pass: compute what the request
means, never a result adjusted or special-cased to match a test's number (a wrong number in a test fails
and is then reviewed). Use the contracts of the files it uses exactly; an adapter to
an external service only makes its call and returns the data. Keep it under {MAX_FILE_LINES} lines; if it
needs more, move a part into a new small module and give that file too; never repeat other files.
Reply with only:
{FILE_BLOCK_FORMAT}"""

    CONFIG_SYSTEM = f"""Write a project's small configuration files (dependencies, settings, tool settings), each
complete, as its role and contract say, in the language's usual formats, before any test or code exists:
only what the project needs (the test tools are installed by the toolchain's setup); a project config
file the test tool reads says where the project's code is, so the test tool's own command finds it
(e.g. pyproject.toml with only [tool.pytest.ini_options] pythonpath = ["."]): only the sections the
project needs, no packaging metadata or version limits it was not asked for. {ENV_EXAMPLE} lists
each setting with a comment and its default (empty for a required one). Write only the files asked for
(never the code or tests). Reply with one block per file and nothing else: the reply starts with the
first block (no plan, notes or reasoning before it):
{FILE_BLOCK_FORMAT}"""

    CHANGE_SYSTEM = (
        "Change one file of an existing project so that it meets the given criteria or fixes the given "
        "problems, with the smallest change; keep everything else as it is.\n" + FILES_REPLY
    )

    BUILD_SYSTEM = (
        "The project does not build or load: the output shows why (a missing dependency, an import of a "
        "module or name that does not exist, a syntax error). Fix the cause in the files shown: a missing "
        "dependency goes into the dependency file; an import or the code is corrected, keeping every "
        "contract. Do not change tests.\n" + FILES_REPLY
    )

    FIX_SYSTEM = (
        "A test fails. Find the cause in the failure output and change the code so that the test passes, "
        "keeping the contracts and adding nothing the test does not need: change only the files the cause "
        "is in (usually one), and leave the rest of the design as it is. Do not change tests. Make the "
        "code right, not the test pass: never a special case that only serves the test's example. When the "
        "test itself is wrong (it expects what the request does not say, such as a miscalculated value), "
        'change nothing and reply only with a line "TEST WRONG: <why>": the test is then reviewed.\n'
        + FILES_REPLY
    )

    REVIEW_SYSTEM = (
        "A test still fails after several fixes, so the test or the code is wrong against the plan. "
        "Compare both with the criterion and the contracts, decide which one is wrong and correct it "
        "(a test keeps its name and tests the criterion through the app's interface). The request "
        "decides: a criterion or test that contradicts its rules (a wrong computed value, a rule "
        "misread) is what is wrong, whatever the criterion says. A test that replaces the app's own "
        "code with a fake of it (anything but an adapter to an external service) is wrong: it must "
        'use the real code. When the criterion itself is wrong, also reply with a line "CRITERION: '
        '<the criterion as it should read>": the spec and its test must say the same. '
        + SAME_CASES
        + "\n"
        + FILES_REPLY
    )

    LESSONS_SYSTEM = f"""A code generator just built a project. Below are the problems it hit and how each was
solved. Write the lessons that would have avoided such problems in OTHER projects, so the generator gets
them right the first time: at most 3, each one imperative sentence naming the concrete technique (how to
fake a kind of service in tests, a setting a tool needs, a pattern a runner expects). A lesson is general:
never name this project, its files, its names, its features or its data. Skip anything the known lessons
already say.
"scope" is "any" only when the lesson holds in every language as written; a lesson that names this
language's own tools, libraries or syntax is "language".
Reply with ONLY JSON: {{"lessons":[{{"phase":"one of {", ".join(LESSON_PHASES)}","scope":"language (only
this language) or any","text":"the lesson"}}],"confirmed":[numbers of known lessons these problems show
again]}} or {{"lessons":[],"confirmed":[]}} when nothing generalizes."""

    TIDY_SYSTEM = (
        "The project's linter reports problems in one file of a working project whose tests pass. Fix "
        "each problem reported, keeping its behaviour, its contract and every test passing: the smallest "
        "change, no new features, no disabling the linter with comments or settings. Change only the "
        "lines the problems are in (and lines they need, such as an import), each with a block "
        "<<<LINES path A-B>>>, then the new text for lines A to B (without the line numbers), then "
        '<<<END>>>. Reply with a line "CAUSE: <one sentence>", then the blocks.'
    )

    RECALL_SYSTEM = """Before a project is planned, choose what a code generator should know for it. It knows only
what it was trained on; what it has learned from the web since is listed below, one topic per line.
"use": the learned topics this request needs, by the name before the colon. "learn": topics this
request needs that are not listed and that it may not know well or as they are today (the language,
frameworks, libraries and techniques the request names or clearly implies), most important first;
none when learning now is not possible. Reply with ONLY JSON: {"use":["python"],"learn":["Flask"]}"""

    SLOT_PLAN_SYSTEM = f"""Plan how to learn a topic for a code generator that knows only what it was trained on.
Below are the topic, the titles and snippets of web pages already found about it, and the slots learning
fills: what it must find out, for a language and for a tool. Say what the topic is: "kind": "language" for a
programming language; "tool" for a library, framework or tool used in code; "domain" for a field, practice,
standard or subject a program must get right (food bank inventory, accessibility rules), which is researched.
Give its name, its newest stable version the pages name (else ""), and one sentence on what it is;
"language": the language's name when it is one, else the language a tool is used with ("" when none);
"docs": its official documentation site: "root", the address of its newest documentation, and "versioned",
the address of one version's documentation with {{version}} where the version goes ("" when the site has no
such addresses);
"commands": up to {MAX_VERSION_COMMANDS} commands that show its installed version and exit (one command each: no shell
operators, nothing installed or changed);
"tools": for a language only, one per role it has, of {", ".join(TOOL_ROLES)}: the one the topic names, else
the one most used today, each with the command that shows its version and its "docs" as above;
"hints": for some slots of its kind, a few words on what to look for about this topic;
"followups": for each slot of its kind its documentation may not answer, one web search that answers it.
Reply with ONLY JSON (this example is for Go):
{{"kind":"language","name":"Go","version":"1.25","summary":"A compiled, statically typed language with built-in
concurrency.","language":"Go","docs":{{"root":"https://go.dev/doc/","versioned":""}},"commands":["go version"],
 "tools":[{{"role":"lint","name":"golangci-lint","command":"golangci-lint --version",
   "docs":{{"root":"https://golangci-lint.run/","versioned":""}}}}],
 "hints":{{"toolchain":"go test, gofmt, go vet, go mod"}},"followups":{{"limitations":"Go garbage collector limits"}}}}"""

    DIGEST_SYSTEM = f"""Answer one question about a topic for a code generator that knows only what it was trained on,
from the numbered passages of web pages below. List the facts the passages state that answer it: each one
plain, concrete sentence the passages state (never from your own knowledge), with the exact commands, names
and versions they give; the ones that change how code is written first; nothing about an unreleased version
(alpha, beta, release candidate, dev, draft); at most {FACTS_PER_SLOT}. "cites": the ids of the passages that
state it; a fact no passage states is left out. "phase" is where it matters: spec (what to build and test),
design (files, structure), config (dependencies, settings), tests, code, toolchain (commands and tools).
Reply with ONLY JSON: {{"facts":[{{"phase":"one of {", ".join(FACT_PHASES)}","text":"the fact","cites":["P3"]}}]}}
or {{"facts":[]}} when the passages do not answer it."""

    FACTS_SYSTEM = f"""Read part of a web page for a code generator that knows only what it was trained on. List what
the page states that matches "Look for" and that such a generator needs to build good software today:
each one plain, concrete sentence the page states (never from your own knowledge), with the exact
commands, names and versions it gives; the ones that change how code is written first (what to use,
what it replaces, what to avoid); no reference lists a generator already knows (keywords, grammar),
nothing about one particular project, nothing about an unreleased version (alpha, beta, release
candidate, dev, draft); at most {FACTS_PER_CHUNK}. "phase" is
where it matters: spec (what to build and test), design (files, structure), config (dependencies,
settings), tests, code, toolchain (commands and tools).
Reply with ONLY JSON: {{"facts":[{{"phase":"one of {", ".join(FACT_PHASES)}","text":"the fact"}}]}}
or {{"facts":[]}} when the excerpt states nothing like that."""

    PLAYBOOK_SYSTEM = f"""Condense what was learned about a programming language or a tool into the rules a code
generator follows to write small programs with it that are correct, idiomatic, modern and efficient, quickly: at
most {PLAYBOOK_RULES}, the most useful first. Each rule is one short imperative sentence (under
{MAX_RULE_CHARS} chars) that changes what is written: the construct, library, command or setting to use
(the newest stable form the facts show) and what it replaces or what to avoid; a short example in
backticks when it helps. Rest every rule on the numbered facts and lessons given (cite their numbers;
never your own knowledge); merge facts that say one thing; leave out what a small program never needs.
Where the facts give alternatives, the rule names only the one that adds no file, dependency or tool
(a setting in an existing file over a new file, the standard library over a package), never both.
A rule that needs a package or tool beyond the language's own install and its tools listed is never for
every program: its "when" names that package or tool ("projects that use uv").
A rule never tells to create or add a file (the plan decides the files): it says what to write.
"phase" is where it matters: {", ".join(FACT_PHASES)} (toolchain: commands and tools).
"when": "" for a rule that holds for every program in the language; else a few words naming the code it
applies to ("async code", "a package published to an index", "code that reads files"), so a program that
has no such code never sees it.
"declare": what a new project writes to get the newest stable version or edition the facts name, and
where (one sentence, citing its facts), or null when the facts name none.
Reply with ONLY JSON: {{"rules":[{{"phase":"code","text":"Use `match` for dispatch on a value's shape, not
if/elif chains","when":"","cites":[3,7]}}],"declare":{{"text":"Declare `go 1.25` in go.mod","cites":[2]}}}}"""

    READ_SURVEY_SYSTEM = f"""You, Tinker, are about to read what the person gave you (a book, an article, a document),
every chapter of it, as a careful reader does: first its contents, what it says about itself (its foreword, preface,
introduction or summary) and the terms its index refers to most, to know what it is about and what to read first.
Decide from what is given below, never from what you were trained on.
"summary": two sentences: what it is about, its main argument, and what a reader gets from it.
"software": true only when it teaches how to plan, design, write, test or run software (then what it teaches may
guide the projects you build); false for anything else.
"terms": its key terms, at most {READ_KEY_TERMS}, the most central first: they are followed through every chapter.
"chapters": every chapter by its number, with "read": "core" (where its main ideas are taught: what its foreword and
its index point to), "supporting" (examples, applications or detail that add to the core) or "other" (the rest);
the core chapters are read first, then the supporting ones, then the others, and "look_for": in a few words, what to
find in it.
Reply with ONLY JSON: {{"summary":"...","software":true,"terms":["..."],
"chapters":[{{"chapter":1,"read":"other","look_for":"..."}},{{"chapter":2,"read":"core","look_for":"..."}}]}}"""

    READ_CHAPTER_SYSTEM = f"""You, Tinker, are reading what the person gave you, one part of a chapter at a time, to
learn what it teaches and keep it. Below are the source (its summary and key terms), what you learned from it
before, what the person asks you to look for (when they ask: find that above all), and the chapter: what to look
for in it, and its text. List what this text teaches: its ideas, definitions, practices, rules of thumb and
warnings, each one plain, self-contained sentence the text states or shows (never from your own knowledge), concrete
(the names, numbers and examples it gives), the most important first; nothing you learned before, unless this text adds to it or changes
it (then say how); at most {READ_FACTS_PER_CHAPTER}. "section": the heading it is under, or "".
"phase": where it matters when building software: spec (what to build and test), design (files, structure), config
(dependencies, settings), tests, code, toolchain (commands and tools); or book when it does not change how software
is built.
Reply with ONLY JSON: {{"points":[{{"text":"...","section":"...","phase":"one of {", ".join(FACT_PHASES)}, book"}}]}}
or {{"points":[]}} when the text teaches nothing new."""

    RESEARCH_PLAN_SYSTEM = f"""Plan research on a topic for a code generator that must understand it well to build
software for it. Split the topic into at most {RESEARCH_QUESTIONS} research questions that together cover it:
what it is and its key terms; how it is done in practice (the steps, roles, records and rules); the
numbers, limits and standards that matter; what is changing or disputed. Each question has
{SEARCHES_PER_RESEARCH_QUESTION} web searches that would answer it from different, independent sources (name the year
when it helps). Give the topic's name and one sentence on what it is.
Reply with ONLY JSON (this example is for "food bank inventory"):
{{"name":"Food bank inventory","summary":"How food banks track donated food from intake to distribution.",
 "questions":[{{"question":"How do food banks record donations at intake?",
   "queries":["food bank donation intake weighing records","food bank receiving donated food procedure"]}}]}}"""

    RESEARCH_ANALYSIS_SYSTEM = f"""You are a careful researcher. Below are research questions about a topic and numbered
facts found on web pages: each with the question it was searched for and how many independent sites
state it. Work only from these facts, never from your own knowledge.
- findings: answer each question the facts answer, citing the facts it rests on; "confidence": high
  when it rests on facts two or more sites state and nothing contradicts them, medium when it rests on
  facts one site states, low when the facts are few, partial or disagree;
- contradictions: only facts that cannot both be true; "doubtful": those of them more likely wrong
  (by the number and standing of the sources on each side; all of them when it cannot be told), with
  a note on why. The pairs listed as possibly disagreeing are only candidates: facts about different things,
  versions, places or dates, and a fact that merely gives more detail than another or states a
  stricter rule within the other's limits, are compatible: leave them out;
- gaps: what a question still needs that no fact answers, each with one web search that would find
  it; at most {RESEARCH_GAPS}, the most important first;
- checks: the key facts the answers rest on that only one site states (above all figures, limits and
  rules), each with one web search that would find another, independent source for it; at most
  {RESEARCH_FACTS_CHECKED}, those the answers depend on most first.
Reply with ONLY JSON:
{{"findings":[{{"question":1,"answer":"...","confidence":"high","facts":[1,4]}}],
 "contradictions":[{{"facts":[3,9,12],"doubtful":[9],"note":"..."}}],
 "gaps":[{{"question":2,"missing":"...","search":"..."}}],
 "checks":[{{"fact":7,"search":"..."}}]}}"""

    README_SYSTEM = """Write the heart of a README for a beginner who has never seen this project: from the request,
its tested criteria and its code, say what it is and how to use it. Only what the code really does and
accepts (the commands, options and functions in the files shown); nothing it does not do. Every
example is run as written to check it, so it must work exactly as given.
- overview: two or three plain sentences: what it is, who it is for, what it does;
- features: every feature it has, one plain line each (not test wording);
- requires: what a person needs installed to use it, in a few words (e.g. "Python 3.10 or newer");
- usage: tldr-style, one entry per feature or common task: "what" says the task in a few words;
  "command" is exactly what a person types in the project folder once it is ready to use: the run
  command shown with its arguments (e.g. python3 calculator.py add 5 2), quiet so that only the
  program's own output shows (a tool that reports its own building gets its quiet option, among the
  tool's options before the program's own arguments, e.g. cargo run -q -- 5 + 3), never a path into
  the project's environment; with no entry point, a one-line call through the language's
  interpreter that prints the result; for a server (started first, so never an example itself), a
  request to it, e.g. curl -s http://127.0.0.1:<its default port>/...; a command that needs a file
  creates it first in the same command, with printf (it works alike in every shell; echo -e does
  not); a program that asks for input gets it piped in (printf '1\n5\n' | ...), and its output is
  shown as printed then: what it was given is not echoed, so each prompt runs on into what follows it
  on the same line; "output" is exactly what it prints, errors included (a long output: its first lines), never
  a note about it such as its exit code; a value that differs each run (an id,
  a code, a time) is written as <what it is>;
- serves: true when the run command keeps running until stopped (a server), else false.
Reply with ONLY JSON: {"overview":"...","features":["..."],"requires":"Python 3.10 or newer",
"usage":[{"what":"Add two numbers","command":"python3 calculator.py add 5 2","output":"7"}],"serves":false}"""

    ASK_SYSTEM = (
        "You are Tinker, and the person asks you about what you have learned. Below is what you keep: the "
        "toolchain that worked for each language (what its projects use), toolchain fixes, lessons, facts and "
        "rules, the person's own commands, and passages of what you read. Answer from these alone, as a "
        "person answers a colleague: only what is asked, in a few short plain lines (no Markdown, no item "
        "numbers, no notes on what is proven, nothing about what you do not keep). The toolchain that worked "
        "comes before facts and rules. What something you read says names its chapter. When nothing below "
        "answers it, say in one line that you have not learned that yet."
    )

    EXERCISE_LANGUAGE_SYSTEM = """The person asks Tinker, a code generator, to practise a programming language, named by the
words below. Say what the words name. "language": exactly one programming language that programs are written and
run in (its usual name, without a version: "Rust", "TypeScript", "C++"). "ambiguous": they could be several
languages and nothing says which ("lisp": Common Lisp, Scheme, Clojure...); list the likely ones. "other": not a
programming language at all (a framework, a tool, a markup or data format, a person, a word). Reply with ONLY JSON:
{"verdict":"language|ambiguous|other","name":"the language's name, when it is one","choices":["for ambiguous: the
languages it could be"],"why":"one short sentence"}"""

    EXERCISE_PROJECT_SYSTEM = """Tinker, a code generator, practises a programming language by building small projects
one after another, to write better code faster. Choose the next one. It is small: a few files, built and tested in
minutes, run on this machine with no network, no accounts and no paid services. It practises what the language is
used for and what the earlier projects did not (its standard library, its types and errors, files, text, data
structures, a command-line interface), a little harder than the last one, and the focus below first when one is
given. Never repeat an earlier project. "task" is the request as a person writes it, one or two sentences that name
the language; "feature" is one feature to add to it once it is built, as a person asks for it; "folder" is a short
lowercase name for its folder, words joined by - ("csv-stats"); "practises" names what of the language it exercises.
Reply with ONLY JSON: {"task":"...","feature":"...","folder":"...","practises":["..."]}"""

    EXERCISE_REFLECT_SYSTEM = f"""Tinker, a code generator, just built a practice project, to get better at a
language and faster at it. Below are its measures, the earlier projects' measures, the problems it hit (failures,
retried or unusable replies, criteria nothing made pass, README examples that failed, slow steps) and the lessons
it keeps. Write the lessons that would make the next projects better code or need fewer model calls and fixes:
at most 3, each one imperative sentence naming the concrete technique, tool usage or habit of the language. A
lesson is general: never name this project, its files, its names, its features or its data. Skip anything the
known lessons already say. "scope" is "any" only when the lesson holds in every language as written. "focus" is
one sentence on what the next practice project should exercise, from what went worst here.
Reply with ONLY JSON: {{"lessons":[{{"phase":"one of {", ".join(LESSON_PHASES)}","scope":"language or any",
"text":"the lesson"}}],"confirmed":[numbers of known lessons these problems show again],"focus":"..."}}"""

    TEACH_SYSTEM = f"""The person teaches you, Tinker, something, to correct what you keep. Below is what you keep that
is most related to it, numbered. Several things may be taught at once: split it into the claims it rests on and
the changes it asks for, each change on its own.
"claims": each claim, at most {TEACH_CLAIMS}, as {{"text": ..., "preference": true or false}}. A preference is only
what the person marks with the word prefer ("I prefer no type checker", "prefer X over Y"): it is theirs, honored
and never checked. Every other claim is a fact, checked first, however sure the person sounds.
"commands": commands that show on this machine whether a fact holds (a tool's --version or --help), one command
each, no shell operators, nothing installed or changed, at most {TEACH_CHECK_COMMANDS};
"searches": web searches that find independent sources for the facts, at most {TEACH_CHECK_SEARCHES} ([] when every
claim is a preference). A fact about what something you read says is checked against its own text (its passages are
found for you): it needs no command and no search.
"language": the language it is about, or "" when it holds for every language.
"changes": each change, with "claims": the numbers of the claims it rests on, and one or more of:
"retire": the numbers of the items it shows wrong;
"toolchain": the language's toolchain commands to replace, by key (check, test_all, test_one, format, lint; setup
is the whole list of its commands), each written as a person types it; a key goes in one change only (one that
several claims affect rests on all of them);
"lesson": what was taught, as one general imperative sentence, with the phase where it matters (one of
{", ".join(LESSON_PHASES)}); never a project's names, files or data;
"fact": what something you read really says, in place of an item of it that it corrects: {{"about": that item's number,
"text": one plain sentence}}.
Reply with ONLY JSON: {{"claims":[{{"text":"Tool A formats code as tool B does","preference":false}},
{{"text":"I prefer no type checker in my projects","preference":true}}],"commands":["tool-a format --help"],
"searches":["tool A replaces tool B formatter"],"language":"Python","changes":[{{"claims":[1],"retire":[2],
"toolchain":{{"format":"tool-a format ."}},"lesson":{{"phase":"toolchain","text":"Format with tool A alone; tool B
is not needed beside it"}}}},{{"claims":[2],"toolchain":{{"lint":"tool-a check ."}},"lesson":{{"phase":"toolchain",
"text":"Run no type checker: the tests check what the spec asks"}}}}]}}"""

    JUDGE_SYSTEM = """The person taught you, Tinker, something. Before you change what you keep, check it. Below are the
facts it rests on, the person's preferences (theirs to make: honored, never checked), the changes the facts are
for, and the evidence, numbered: what commands showed on this machine, what web pages say and what the things you
read say. Decide from the evidence alone, never from what you were trained on (it may be out of date). A tool a
command does not find here
may still be installed in a project's own environment: that says nothing about the tool itself.
For each fact: "holds" only when the evidence states it (cite those numbers), "wrong" when the evidence
contradicts it, "unsure" when it says too little.
For each change: "follows" true only when every item it retires is shown wrong, and its toolchain commands and
its lesson and its fact agree with the evidence and the preferences.
Reply with ONLY JSON: {"claims":[{"claim":1,"verdict":"holds","why":"one short sentence","evidence":[1,3]}],
"changes":[{"change":1,"follows":true,"why":"one short sentence"}]}"""

    README_FIX_SYSTEM = (
        "Some usage examples in a project's README do not print what the README says. For each, "
        "decide what is wrong: the README (its command or the output it shows) or the code (it does "
        "not do what the request and the README say), and correct that with the smallest change. "
        "The README and the code files shown may change, never the tests; every test must keep "
        'passing. "It printed" is everything the command wrote, errors included; a README '
        "line that is a note about the output (such as an exit code) is a README mistake. The "
        "commands run in a POSIX sh (echo -e prints its -e there: printf makes a file alike "
        "everywhere). A tool's own messages in the output (building, running) are the "
        "README's mistake: its command gets the tool's quiet option, before the program's own "
        "arguments. Input piped in is not echoed: a "
        "prompt runs on into the next output on the same line.\n" + FILES_REPLY
    )

    BRIEF_SYSTEM = (
        "You condense part of a software spec. Output terse bullets of concrete requirements only: "
        "features, interfaces, data, storage, libraries, constraints, edge cases. No prose, no code. "
        "At most {words} words."
    )

    CHAT_SYSTEM = (
        "You are Tinker, a friendly helper that builds small programs on this computer with a local model. "
        "You are chatting over {kind}, read on a phone: answer briefly, in short lines. Format for "
        "meaning, in Markdown: **bold** for what matters most, *italic* for an aside, `code` for "
        'commands, names and paths, "- " for items; an emoji only where it says something with less '
        "(✅ done or fine, ⚠️ a problem, 💡 a tip, 👉 what to do next); never a heading or a table. Talk "
        "about anything. You cannot run anything yourself from the chat; these messages do:\n{commands}"
    )

    COMMAND_SYSTEM = (
        "The person describes a command they want run on their computer ({system}, shell {shell}, in the "
        'folder {folder}). Reply with ONLY JSON: {{"command": "the one command line that does it", '
        '"does": "what it does, in one short sentence"}}, or {{"command": "", "does": "why no '
        'single command fits"}} when the description is unclear or asks for no command.'
    )

    COMMAND_TELL_SYSTEM = (
        "Tell the person what this command's output says, for a chat read on a phone: the answer to "
        "what they asked first, then only what matters, in a few short lines. Format for meaning, in "
        'Markdown: **bold** for the key values, `code` for names and paths, "- " for items; an emoji '
        "only where it says something with less (⚠️ a problem or something nearly full, ✅ all fine, "
        "💾 disk, 📁 folders, 🌐 network, ⏱ time). Never a heading or a table; never anything the "
        "output does not show; never the command again."
    )
