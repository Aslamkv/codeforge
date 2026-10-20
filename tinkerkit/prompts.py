"""What the model is told and what it answers: prompt parts (Part), replies (Reply) and every system prompt (Prompts)."""

import json
import re
from pathlib import Path

from .settings import (
    ENV_EXAMPLE, ENV_FILE, FACTS_PER_CHUNK, FACT_PHASES, LESSON_PHASES, MAX_COMMANDS, MAX_FILE_LINES, MAX_QUERIES,
    RESEARCH_CHECKS, RESEARCH_GAPS, RESEARCH_QUERIES, RESEARCH_QUESTIONS, TOOL_ROLES, TRIM_FLOOR, first_line,
)


class Reply:
    """What the model answered, and the structured parts in it."""
    THINK_RE = re.compile(r"<think>.*?</think>", re.S)
    HEADER_PATH = r"[ \t:]+(?:(?:path|file)[ \t]*[=:][ \t]*)?[\"'`]?([^\s<>\"'`]+?)[\"'`]?"  # FILE x, FILE path=x, FILE "x"
    FILE_BLOCK_RE = re.compile(r"<<<FILE" + HEADER_PATH + r"[ \t]*(?:>>>[ \t]*([^\s<>]*))?[ \t]*\n(.*?)\n?<<<END>>>",
                               re.S)  # the header's >>> may be missing: the path then ends with its line
    LINES_BLOCK_RE = re.compile(r"<<<LINES" + HEADER_PATH + r"[ \t]+(?:lines[ \t]*=[ \t]*)?(\d+)\s*-\s*(\d+)[ \t]*>>>[ \t]*\n"
                                r"(.*?)\n?<<<END>>>", re.S)
    NUMBERED_RE = re.compile(r"^\s*\d+\| ?")
    PLACEHOLDER_PATHS = {"path", "path/to/file", "file", "filename", "key"}
    PLACEHOLDER_BODY_RE = re.compile(r"\s*[<\[(]?\s*(the\s+)?(complete\s+|whole\s+|full\s+)?(file\s+)?(content|contents|"
                                     r"code|text|file|\.\.\.|…)\s*(here|goes here)?\s*[>\])]?\s*", re.I)
    PATH_LINE_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.[\w]+|[\w.-]+(?:/[\w.-]+)+")  # a relative path, alone on a line
    KEY_AFTER_RE = re.compile(r'"(?:[^"\\]|\\.)*"\s*:')  # a string that is an object's key
    JOINED_STRINGS_RE = re.compile(r'(?<!\\)"\s*\+\s*"')  # "a" + "b", as in code: one string "ab"
    DEBRIS_RE = re.compile(r"\s*(<{2,}|>{2,}|<<<\w*>>>)\s*")  # a line of the block format's own marks, no content
    FENCE_RE = re.compile(r"\s*(`{3,}|~{3,})[\w+#.-]*\s*")      # a markdown fence line: ``` or ```python
    QUOTE_ESCAPE_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\'")      # \' after an even run of backslashes

    def __init__(self, raw):
        self.text = self.THINK_RE.sub("", raw or "").strip()

    def json(self):
        """The JSON object in the reply. A model's usual slips are mended in turn, each only when
        the reply does not parse without it: a comma before a closing bracket, strings joined with
        + as in code, a single quote escaped as in code (\\' is not JSON), the last brackets left
        open, a list left open before the next key."""
        start, end = self.text.find("{"), self.text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in reply")
        whole, to_the_end = self.text[start:end + 1], self.text[start:].rstrip().rstrip("`").rstrip()
        mend = lambda text: self.strip_trailing_commas(
            self.QUOTE_ESCAPE_RE.sub(r"\1'", self.JOINED_STRINGS_RE.sub("", text)))
        candidates = [whole, mend(whole), self.closed(mend(to_the_end)), self.balanced(mend(to_the_end))]
        problem = None
        for candidate in filter(None, candidates):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as exc:
                problem = problem or exc  # the reply's own problem, as it was written
        raise problem

    @staticmethod
    def balanced(text):
        """The JSON text with its brackets put right: a list left open when the next key of its
        object begins ("a":["x","b":1) is closed before that key, a closing bracket that closes
        nothing open is dropped, and what is left open at the end is closed."""
        out, stack, in_string, escaped = [], [], False, False
        for index, char in enumerate(text):
            if in_string:
                escaped, in_string = (False, True) if escaped else (char == "\\", char != '"')
            elif char == '"':
                in_string = True
                if stack and stack[-1] == "]" and Reply.KEY_AFTER_RE.match(text, index):
                    comma = "".join(out).rstrip().rfind(",")
                    if comma >= 0:
                        out.insert(comma, stack.pop())
            elif char in "{[":
                stack.append("}" if char == "{" else "]")
            elif char in "}]":
                if char not in stack:
                    continue  # it closes nothing
                while stack[-1] != char:
                    out.append(stack.pop())
                stack.pop()
            out.append(char)
        return None if in_string else "".join(out).rstrip().rstrip(",") + "".join(reversed(stack))

    @staticmethod
    def closed(text):
        """The JSON text with the brackets it left open closed at its end (a model that wrote the
        whole answer and forgot the last brace or two), or None when it ends inside a string or
        closes a bracket it never opened."""
        stack, in_string, escaped = [], False, False
        for char in text:
            if in_string:
                escaped, in_string = (False, True) if escaped else (char == "\\", char != '"')
            elif char == '"':
                in_string = True
            elif char in "{[":
                stack.append("}" if char == "{" else "]")
            elif char in "}]":
                if not stack or stack.pop() != char:
                    return None
        if in_string or not stack:
            return None
        return text.rstrip().rstrip(",") + "".join(reversed(stack))

    @staticmethod
    def files_complete(content):
        """A reply of FILE/LINES blocks is complete when its last block has ended and what follows is
        not another block (models sometimes run on with junk after their answer)."""
        end = content.rfind("<<<END>>>")
        rest = content[end + 9:] if end >= 0 else ""
        return end >= 0 and "<<<FILE" not in rest and "<<<LINES" not in rest and len(rest.strip()) >= 300

    @staticmethod
    def wrote(paths):
        """When a reply asked for these files is complete: once each has its block, as soon as the
        last one ends (models run on with files nobody asked for), or as files_complete says."""
        wanted = {str(Path(p)) for p in paths}

        def done(content):
            return wanted <= {path for path, _ in Reply(content).files()} or Reply.files_complete(content)
        return done

    def files(self):
        """[(path, content)] of its FILE blocks. A header that copied the format's placeholder
        (<<<FILE path>>>) with the real path right after it, or as the block's first line, is read
        as that path. A block whose body is only the format's placeholder ("content") is the model
        musing about the format, not a file."""
        found = []
        for path, after, body in self.FILE_BLOCK_RE.findall(self.text):
            path = path.strip()
            first, _, rest = body.partition("\n")
            if path.lower() in self.PLACEHOLDER_PATHS and self.PATH_LINE_RE.fullmatch(after):
                path = after  # <<<FILE path>>>real/path.py: the real path after the header
            elif after:
                body = after + "\n" + body  # the file began on the header's line
            elif path.lower() in self.PLACEHOLDER_PATHS and self.PATH_LINE_RE.fullmatch(first.strip()):
                path, body = first.strip(), rest
            if self.PLACEHOLDER_BODY_RE.fullmatch(body):
                continue
            markdown = path.lower().endswith((".md", ".markdown"))  # a fence around a whole doc may be the doc
            found.append((str(Path(path)), self.without_debris(body, fences=not markdown)))
        return found

    def as_file(self, path):
        """The reply as a FILE block for path when it has none but one fenced code block (a model
        that answered with the file and forgot the block format); else the reply as it is."""
        if self.files():
            return self
        blocks = re.findall(r"^(`{3,}|~{3,})[\w+#.-]*[ \t]*\n(.*?)^\1[ \t]*$", self.text, re.S | re.M)
        return Reply(f"<<<FILE {path}>>>\n{blocks[0][1].rstrip()}\n<<<END>>>") if len(blocks) == 1 else self

    @classmethod
    def without_debris(cls, body, fences=True):
        """A block's body without what the model wrapped around it: lines of the block format's
        own marks at its ends (a stray >>> before <<<END>>>), and a markdown fence around the whole
        body. These are never content in any language, and kept they break the file (the stray
        >>> was a syntax error a fix could not see, in a test it could not change)."""
        lines = body.split("\n")
        while lines and cls.DEBRIS_RE.fullmatch(lines[-1]):
            lines.pop()
        while lines and cls.DEBRIS_RE.fullmatch(lines[0]):
            lines.pop(0)
        filled = [i for i, line in enumerate(lines) if line.strip()]
        fence_at = lambda i: cls.FENCE_RE.fullmatch(lines[i])
        if fences and len(filled) >= 2 and fence_at(filled[0]) and fence_at(filled[-1]) \
                and not any(fence_at(i) for i in filled[1:-1]):
            lines = lines[filled[0] + 1:filled[-1]]  # one fence pair around it all (a fence inside is content: docs)
        return "\n".join(lines)

    @staticmethod
    def strip_trailing_commas(text):
        """Remove commas directly before ] or } outside JSON strings."""
        out, in_string, escaped = [], False, False
        for index, char in enumerate(text):
            if in_string:
                out.append(char)
                escaped, in_string = (False, in_string) if escaped else (char == "\\", char != '"')
                continue
            if char == '"':
                in_string = True
            elif char == "," and text[index + 1:].lstrip()[:1] in ("]", "}"):
                continue
            out.append(char)
        return "".join(out)

    def spec_json(self):
        """The spec's JSON; when only its free-form "data" part (written last) is broken, that
        part is kept as text instead of asking again."""
        try:
            return self.json()
        except ValueError as first:
            start, at = self.text.find("{"), self.text.rfind('"data"')
            if start < 0 or at <= start:
                raise first
            try:
                data = json.loads(self.strip_trailing_commas(self.text[start:at].rstrip().rstrip(",") + "}"))
            except ValueError:
                raise first
            data["data"] = self.text[at:].split(":", 1)[-1].strip().rstrip("}").strip()
            return data

    def cause(self):
        match = re.search(r"^\s*CAUSE:\s*(.+)$", self.text, re.M)
        return first_line(match.group(1), 160) if match else ""

    def test_wrong(self):
        """Why the reply says the test itself is wrong ("" when it does not): a line TEST WRONG: why."""
        match = re.search(r"^\s*TEST WRONG:\s*(.+)$", self.text, re.M)
        return first_line(match.group(1), 200) if match else ""

    def criterion(self):
        """The criterion as the reply says it should read ("" when it keeps it): a line CRITERION: text."""
        match = re.search(r"^\s*CRITERION:\s*(.+)$", self.text, re.M)
        return match.group(1).strip() if match else ""

    def corrected_criteria(self):
        """{test name: the criterion as it should read} from lines CRITERION <test name>: text."""
        return {name: text.strip() for name, text in re.findall(r"^\s*CRITERION\s+([^\s:]+):\s*(.+)$", self.text, re.M)}

    def toolchain_line(self):
        match = re.search(r"^\s*TOOLCHAIN:\s*(\{.*\})\s*$", self.text, re.M)
        return json.loads(match.group(1)) if match else None

    def line_edits(self):
        """[(path, first, last, new lines)] of its LINES blocks."""
        edits = []
        for p, a, b, body in self.LINES_BLOCK_RE.findall(self.text):
            new = self.without_debris(body).splitlines()
            if new and all(self.NUMBERED_RE.match(l) or not l.strip() for l in new):
                new = [self.NUMBERED_RE.sub("", l, count=1) for l in new]  # it kept the line numbers
            edits.append((str(Path(p.strip())), int(a), int(b), new))
        return edits

    @staticmethod
    def json_complete(content):
        """A JSON reply is complete once the object that opened it closes and parses."""
        start = content.find("{")
        if start < 0 or not content.rstrip().endswith("}"):
            return False
        try:
            json.loads(Reply.strip_trailing_commas(content[start:].strip()))
            return True
        except ValueError:
            return False

    def named_paths(self):
        return list(dict.fromkeys([p for p, _ in self.files()] + [e[0] for e in self.line_edits()]))


class Part:
    """A section of a prompt. Optional parts are cut or dropped to fit; required ones are always
    sent: prose ones (a request, a failure) are shortened at their less useful end, exact ones
    (a whole file) are sent as they are. An atomic optional part is sent whole or not at all.
    place: "first" for what calls of a run share (lessons, learned facts, the spec's data), "last"
    for what changes from one try to the next (a failure, what was tried): a prompt is laid out
    first, then as given, then last, so Ollama re-reads only what follows the part it has cached."""
    SHORTEST = 150  # chars an optional part keeps at least when it is shortened to fit (else it is left out)
    PLACES = {"first": 0, None: 1, "last": 2}

    def __init__(self, title, text, required=False, keep="head", exact=False, atomic=False, place=None):
        self.title, self.text, self.required, self.keep = title, (text or "").strip(), required, keep
        self.exact = bool(exact and required)
        self.atomic = bool(atomic and not required)
        self.place = place

    @staticmethod
    def total_size(parts):
        """The chars a list of parts takes in a prompt (empty optional parts are left out)."""
        return sum(p.size() for p in parts if p.text or p.required)

    @staticmethod
    def assemble(parts, limit, notes=None):
        """The user message: required parts first fitted (shortening prose ones), then optional
        parts while they fit. Raises PromptTooLarge when the required parts cannot fit."""
        parts = Part.shorten_required(sorted(parts, key=lambda p: Part.PLACES[p.place]), limit, notes)
        room = limit - sum(p.size() for p in parts if p.required)  # what the optional parts may take
        blocks = []
        for p in parts:
            if not p.text and not p.required:
                continue
            if p.required:
                blocks.append(p.render())
            elif p.size() <= room:
                blocks.append(p.render())
                room -= p.size()
            elif not p.atomic and room - len(p.render("")) - 2 >= Part.SHORTEST:  # 2: the blank line before it
                blocks.append(p.shortened(room - len(p.render("")) - 2).render())
                room = 0
        return "\n\n".join(blocks)

    @staticmethod
    def shorten_required(parts, limit, notes):
        over = sum(p.size() for p in parts if p.required) - limit
        trimmed = set()
        while over > 0:
            prose = [i for i, p in enumerate(parts) if p.required and not p.exact and len(p.text) > TRIM_FLOOR]
            if not prose:
                exact = ", ".join(p.title for p in parts if p.exact) or "none"
                raise PromptTooLarge(f"required prompt parts need {limit + over} chars; budget allows {limit} "
                                     f"(parts that cannot be shortened: {exact})")
            i = max(prose, key=lambda k: len(parts[k].text))
            parts[i] = parts[i].shortened(max(TRIM_FLOOR, len(parts[i].text) - over))
            trimmed.add(parts[i].title)
            over = sum(p.size() for p in parts if p.required) - limit
        if trimmed and notes is not None:
            notes.extend(sorted(trimmed))
        return parts

    def render(self, text=None):
        text = self.text if text is None else text
        return f"## {self.title}\n{text}" if self.title else text

    def size(self):
        return len(self.render()) + 2

    def floor_size(self):
        """Its size once shortened as far as it may be."""
        return self.size() if self.exact else min(self.size(), TRIM_FLOOR + len(self.title) + 12)

    def shortened(self, room):
        marker = "[...]"
        room -= len(marker) + 1
        text = "" if room <= 0 else (self.text[:room] + "\n" + marker if self.keep == "head"
                                     else marker + "\n" + self.text[-room:])
        return Part(self.title, text, self.required, self.keep, self.exact, self.atomic, self.place)

    def relaxed(self):
        return Part(self.title, self.text, self.required, self.keep, exact=False, atomic=self.atomic, place=self.place)


class PromptTooLarge(Exception):
    """The required parts of a prompt do not fit the budget."""


class Prompts:
    """Every prompt text: language- and app-neutral (the examples are a small command-line notes
    tool). The *_SYSTEM ones are system prompts; the others are pieces of them and of user messages."""
    NO_TOOL_CALLS = "\nReply in plain text; never call a tool or function."  # Ollama parses tool-call syntax
    FOLDER_NAME_SYSTEM = ("Name the folder of the project the person asks for: one to three short lowercase words "
                          "joined by hyphens, at most 20 characters, the shorter the better; shorten well-known "
                          "words (calc-py for a calculator in Python, todo for a todo app). Reply with the name "
                          "only.")
    SAME_CASES = ("Several similar cases (every line, every unit, each row of a table) are built from the values "
                  "the criterion names, with a loop or a small helper, never written out by hand one by one: a long "
                  "hand-made literal (a grid, a board, a list of positions) is where counting slips.")
    FILE_BLOCK = "<<<FILE path>>>\nthe complete file\n<<<END>>>"  # how a reply gives a whole file

    FILES_REPLY = (f"Reply with a line \"CAUSE: <one sentence>\", then each file you change, complete:\n"
                   f"{FILE_BLOCK}\n"
                   f"Only the files shown may change; the project's other files exist. Files stay under "
                   f"{MAX_FILE_LINES} lines.")

    LINES_HOWTO = ("A file shown as numbered lines is changed with: <<<LINES path A-B>>>, then the new text for its "
                   "lines A to B (within the range shown, without the line numbers), then <<<END>>>. Give a whole "
                   "file only for files shown in full.")

    DEPENDENCIES_TITLE = ("Third-party dependencies (the only ones it may import; everything else is the standard "
                          "library or the project's own files)")

    SPEC_JSON = ('{"language":"Python",\n'
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
                 ' "data":""')

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
  each with its default (empty if required), and a required one gets a criterion (source "setting")
  that without it the app says which setting to set; kind: code, test, template, config or docs;
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

    TOOLCHAIN_JSON = ('"toolchain":{"setup":["python3 -m venv .venv","pip install pytest ruff black"],'
                      '"activate":". .venv/bin/activate","use":[],"run":"python3 tip.py",'
                      '"check":"python -c \\"import tip\\"","test_all":"pytest -q",'
                      '"test_one":"pytest -q {file}::{name}","format":"ruff check tinker fix --exit-zero . && black .",'
                      '"lint":"ruff check ."}')

    TOOLCHAIN_RULES = ("toolchain: the language's usual tools, every command written the way a person types it in "
                       "the project folder; setup: what prepares it for development, in order (the project's own "
                       "environment when the language keeps dependencies in one, then the test framework, the "
                       "formatter, the linter and the dependency file's packages), creating no project file (the files "
                       "listed are written for it: never a command that starts a new project) and changing nothing "
                       "outside the project folder (never the machine's default toolchain, a global install or "
                       "a global setting: what is installed already is used as it is); "
                       "activate: what makes a shell use that environment (\"\" when the language needs none), "
                       "run before every command but setup's first; use: only what a person must run once to use "
                       "it ([] when the language as installed is enough, e.g. a Python app on the standard library "
                       "alone; else e.g. make and activate the environment and install the dependency file); run: "
                       "how a person starts it after that, with no arguments (they add them), by the command name that "
                       "works wherever the language is installed (e.g. python3 calculator.py), printing only what the "
                       "program prints (a tool that reports its own building runs quietly), \"\" when there is "
                       "no entry point; check quickly builds or loads every code file, so a missing import or "
                       "dependency fails it; test_all and test_one: the test tool's own command as a person types "
                       "it (e.g. pytest -q), never a wrapper; it must find the project's code (the project config "
                       "file says where, when the tool alone does not); test_one runs one test given {file} and "
                       "{name}; format formats the code with the language's formatter after the linter's own safe "
                       "fixes (\"\" when it has none); lint: the language's usual linter over the code, exiting "
                       "non-zero when it finds a problem, each reported with its file and line (\"none\" when it "
                       "has none)")

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

    TOOLCHAIN_FIX_SYSTEM = (f"The project's tools do not work: the output shows a tooling problem (a missing test "
                            f"framework, dependency or command), not a failing test. Fix the setup only: "
                            f"give each dependency or config file you change complete, and/or a line TOOLCHAIN: {{json}} "
                            f"with the corrected commands. Never change code or tests. {TOOLCHAIN_RULES}.\n"
                            f"Reply with a line \"CAUSE: <one sentence>\", then the changes:\n{FILE_BLOCK}")

    TESTS_REPLY = f"""Reply with the test file, complete:
{FILE_BLOCK}
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

    SPEC_TESTS_SYSTEM = """Write the spec tests for the given criteria before the code exists, in the project's language
and test framework (as the test command runs them). Each test checks its criterion through the public
interface of the module the contracts name: the code will be written to pass these tests and nothing more.
""" + SAME_CASES + "\n" + TESTS_REPLY

    REPRODUCE_SYSTEM = f"""A user reports a bug in a project that is built and whose tests pass. Write ONE test that
shows the bug: it checks the correct behaviour the report implies, so it fails on the code as it is now
and passes once the bug is fixed. It tests through the app's public interface, as the project's other
tests do, with sample data in place of each adapter: nothing outside the app runs. It goes in a NEW
test file next to the project's tests; the file and the test are named as the project's test tool
expects. Reply with a line "TEST: <test file>::<test name>", a line "EXPECTED: <the correct behaviour,
one sentence>", then the test file, complete:
{FILE_BLOCK}
Keep it short."""

    CODE_SYSTEM = f"""Write one file of a project, in its language. It must fulfil its contract exactly, make the
given tests pass, meet its criteria and do nothing more: no extra functions, options, fallbacks, logging
or abstraction: only what the tests need. Make the code right, not the tests pass: compute what the request
means, never a result adjusted or special-cased to match a test's number (a wrong number in a test fails
and is then reviewed). Use the contracts of the files it uses exactly; an adapter to
an external service only makes its call and returns the data. Keep it under {MAX_FILE_LINES} lines; if it
needs more, move a part into a new small module and give that file too; never repeat other files.
Reply with only:
{FILE_BLOCK}"""

    CONFIG_SYSTEM = f"""Write a project's small configuration files (dependencies, settings, tool settings), each
complete, as its role and contract say, in the language's usual formats, before any test or code exists:
only what the project needs (the test tools are installed by the toolchain's setup); a project config
file the test tool reads says where the project's code is, so the test tool's own command finds it
(e.g. pyproject.toml with only [tool.pytest.ini_options] pythonpath = ["."]): only the sections the
project needs, no packaging metadata or version limits it was not asked for. {ENV_EXAMPLE} lists
each setting with a comment and its default (empty for a required one). Write only the files asked for
(never the code or tests). Reply with one block per file and nothing else: the reply starts with the
first block (no plan, notes or reasoning before it):
{FILE_BLOCK}"""

    CHANGE_SYSTEM = ("Change one file of an existing project so that it meets the given criteria or fixes the given "
                     "problems, with the smallest change; keep everything else as it is.\n" + FILES_REPLY)

    BUILD_SYSTEM = ("The project does not build or load: the output shows why (a missing dependency, an import of a "
                    "module or name that does not exist, a syntax error). Fix the cause in the files shown: a missing "
                    "dependency goes into the dependency file; an import or the code is corrected, keeping every "
                    "contract. Do not change tests.\n" + FILES_REPLY)

    FIX_SYSTEM = ("A test fails. Find the cause in the failure output and change the code so that the test passes, "
                  "keeping the contracts and adding nothing the test does not need: change only the files the cause "
                  "is in (usually one), and leave the rest of the design as it is. Do not change tests. Make the "
                  "code right, not the test pass: never a special case that only serves the test's example. When the "
                  "test itself is wrong (it expects what the request does not say, such as a miscalculated value), "
                  "change nothing and reply only with a line \"TEST WRONG: <why>\": the test is then reviewed.\n"
                  + FILES_REPLY)

    REVIEW_SYSTEM = ("A test still fails after several fixes, so the test or the code is wrong against the plan. "
                     "Compare both with the criterion and the contracts, decide which one is wrong and correct it "
                     "(a test keeps its name and tests the criterion through the app's interface). The request "
                     "decides: a criterion or test that contradicts its rules (a wrong computed value, a rule "
                     "misread) is what is wrong, whatever the criterion says. A test that replaces the app's own "
                     "code with a fake of it (anything but an adapter to an external service) is wrong: it must "
                     "use the real code. When the criterion itself is wrong, also reply with a line \"CRITERION: "
                     "<the criterion as it should read>\": the spec and its test must say the same. "
                     + SAME_CASES + "\n" + FILES_REPLY)

    LESSONS_SYSTEM = f"""A code generator just built a project. Below are the problems it hit and how each was
solved. Write the lessons that would have avoided such problems in OTHER projects, so the generator gets
them right the first time: at most 3, each one imperative sentence naming the concrete technique (how to
fake a kind of service in tests, a setting a tool needs, a pattern a runner expects). A lesson is general:
never name this project, its files, its names, its features or its data. Skip anything the known lessons
already say.
"scope" is "any" only when the lesson holds in every language as written; a lesson that names this
language's own tools, libraries or syntax is "language".
Reply with ONLY JSON: {{"lessons":[{{"phase":"one of {', '.join(LESSON_PHASES)}","scope":"language (only
this language) or any","text":"the lesson"}}],"confirmed":[numbers of known lessons these problems show
again]}} or {{"lessons":[],"confirmed":[]}} when nothing generalizes."""

    TIDY_SYSTEM = ("The project's linter reports problems in one file of a working project whose tests pass. Fix "
                   "each problem reported, keeping its behaviour, its contract and every test passing: the smallest "
                   "change, no new features, no disabling the linter with comments or settings. Change only the "
                   "lines the problems are in (and lines they need, such as an import), each with a block "
                   "<<<LINES path A-B>>>, then the new text for lines A to B (without the line numbers), then "
                   "<<<END>>>. Reply with a line \"CAUSE: <one sentence>\", then the blocks.")

    RECALL_SYSTEM = """Before a project is planned, choose what a code generator should know for it. It knows only
what it was trained on; what it has learned from the web since is listed below, one topic per line.
"use": the learned topics this request needs, by the name before the colon. "learn": topics this
request needs that are not listed and that it may not know well or as they are today (the language,
frameworks, libraries and techniques the request names or clearly implies), most important first;
none when learning now is not possible. Reply with ONLY JSON: {"use":["python"],"learn":["Flask"]}"""

    LEARN_PLAN_SYSTEM = f"""Plan how to learn a topic from the web for a code generator that knows only what it was
trained on. The topic may be a framework, a library, a tool or a technique, or name several. Give its
name, one sentence on what it is, up to {MAX_QUERIES} web searches, at least one for each thing it names,
that find how to use it today (setup, commands, API, configuration) and its current version and what
changed (name the year when it helps), and, only if it is software that may be installed on this
machine, up to {MAX_COMMANDS} commands that show its version and exit (one command each: no shell
operators, nothing installed or changed). "language": the language's name when the topic is mainly a
programming language (with or without its tools), else "". "kind": "tool" for software, a library, a
framework or a technique to use; "domain" for a field, practice, standard or subject a program must get
right (food bank inventory, accessibility rules, double-entry bookkeeping), which is researched.
Reply with ONLY JSON (this example is for Flask):
{{"name":"Flask","summary":"A small web framework for Python.","language":"","kind":"tool",
 "queries":["Flask quickstart routes templates","Flask latest release what changed"],
 "commands":["python3 -m flask --version"]}}"""

    LANGUAGE_PLAN_SYSTEM = f"""Plan how to learn a programming language from the web as it is today, for a code generator
that knows only what it was trained on and must build software in it quickly and well. From the request
take the language, the version it asks for (else the newest stable one) and the tools it names. Give:
- name, version, one sentence on what it is;
- tools: one per role the language has, of {", ".join(TOOL_ROLES)} (build only if it is compiled;
  environment: how a project's dependencies are kept apart): the tool the request names for a role, else
  the one most used today; each with one web search that finds how to use it in a project (install,
  commands, configuration) and one command that shows its version and exits;
- searches: 2 for fundamentals (syntax, reserved keywords, types, errors, modules, the concurrency and
  runtime model), 2 for new (the newest stable releases: features, deprecations, removals; name the
  year), 1 for conventions (project layout, style guide, packaging);
- commands: up to 2 that show the language's version and exit.
Commands: one each, no shell operators, nothing installed or changed. If the request is no programming
language, reply {{"name":"","summary":"what it is instead"}}.
Reply with ONLY JSON (this example is for Go):
{{"name":"Go","version":"1.25","summary":"A compiled, statically typed language with built-in concurrency.",
 "tools":[{{"role":"tests","name":"go test","search":"go test flags table-driven tests","command":"go version"}}],
 "searches":[{{"section":"fundamentals","query":"Go language specification keywords types"}},
  {{"section":"new","query":"Go 1.25 release notes"}}],"commands":["go version"]}}"""

    TOPIC_FOCUS = ("how to set up and use {topic} today (commands, API, configuration), its current version and what "
                   "is new or changed, its conventions and good practice")

    LANGUAGE_FOCUS = {
        "fundamentals": ("the core of {language} a developer must get right: syntax rules, reserved keywords, built-in "
                         "types, error handling, modules and imports, the concurrency and runtime model, idioms"),
        "new": ("what the newest stable releases of {language} added, changed, deprecated or removed (nothing "
                "unreleased: no alpha, beta, release candidate, dev or draft)"),
        "conventions": ("how {language} projects are laid out and written today: project layout, style guide, "
                        "packaging and dependency files"),
    }

    TOOL_FOCUS = ("how to use {tool} ({role}) in a {language} project: installing it in the project, the exact commands "
                  "and options to run it, its configuration file")

    FACTS_SYSTEM = f"""Read part of a web page for a code generator that knows only what it was trained on. List what
the page states that matches "Look for" and that such a generator needs to build good software today:
each one plain, concrete sentence the page states (never from your own knowledge), with the exact
commands, names and versions it gives; nothing about one particular project, nothing about an
unreleased version (alpha, beta, release candidate, dev, draft); at most {FACTS_PER_CHUNK}. "phase" is
where it matters: spec (what to build and test), design (files, structure), config (dependencies,
settings), tests, code, toolchain (commands and tools).
Reply with ONLY JSON: {{"facts":[{{"phase":"one of {', '.join(FACT_PHASES)}","text":"the fact"}}]}}
or {{"facts":[]}} when the excerpt states nothing like that."""

    PICK_FACTS_SYSTEM = """Below are facts found on web pages about a topic, for a code generator. Keep the {count}
that most help it plan and write good software today: what changes how code, tests, configuration and
commands are written (APIs, syntax, defaults, removals, exact commands and options). Drop repeats,
anything vague, history, and trivia a typical project never meets (release signing, proxies, internals).
Reply with ONLY JSON: {{"keep":[the numbers of the facts kept]}}"""

    RESEARCH_PLAN_SYSTEM = f"""Plan research on a topic for a code generator that must understand it well to build
software for it. Split the topic into at most {RESEARCH_QUESTIONS} research questions that together cover it:
what it is and its key terms; how it is done in practice (the steps, roles, records and rules); the
numbers, limits and standards that matter; what is changing or disputed. Each question has
{RESEARCH_QUERIES} web searches that would answer it from different, independent sources (name the year
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
  {RESEARCH_CHECKS}, those the answers depend on most first.
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

    README_FIX_SYSTEM = ("Some usage examples in a project's README do not print what the README says. For each, "
                         "decide what is wrong: the README (its command or the output it shows) or the code (it does "
                         "not do what the request and the README say), and correct that with the smallest change. "
                         "The README and the code files shown may change, never the tests; every test must keep "
                         "passing. \"It printed\" is everything the command wrote, errors included; a README "
                         "line that is a note about the output (such as an exit code) is a README mistake. The "
                         "commands run in a POSIX sh (echo -e prints its -e there: printf makes a file alike "
                         "everywhere). A tool's own messages in the output (building, running) are the "
                         "README's mistake: its command gets the tool's quiet option, before the program's own "
                         "arguments. Input piped in is not echoed: a "
                         "prompt runs on into the next output on the same line.\n"
                         + FILES_REPLY)

    BRIEF_SYSTEM = ("You condense part of a software spec. Output terse bullets of concrete requirements only: "
                    "features, interfaces, data, storage, libraries, constraints, edge cases. No prose, no code. "
                    "At most {words} words.")
