"""The project on disk: its files, the commands that set it up, test, run, format and lint it, and what their output says."""

import hashlib
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from .prompts import Part, Prompts, Reply
from .settings import (
    COMMANDS_FILE, COMMAND_TIMEOUT, Config, Console, ENV_EXAMPLE, ENV_FILE, JsonFile, ONLINE_PROBES, OUTPUT_CHARS,
    SETUP_TIMEOUT, STATE_DIR, first_line, now_text,
)


class TaughtCommands:
    """The person's own commands Tinker may use (tinker learn NAME [DEFINITION]), kept in
    commands.json next to the script: an alias (the shell code its name stands for: given, or read
    from the person's shell) or a program on PATH. The toolchain prompts list them, so the model
    uses them where they fit. Tinker runs each command in a new non-interactive POSIX shell, where
    the person's aliases do not exist, so a command that uses an alias gets its definition first, as
    a shell function. SPEC.md and the README show commands with aliases spelled out, so they work
    for anyone. Plain JSON: edit or delete at any time."""
    NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # a name a POSIX shell function can have
    SOURCE_RE = re.compile(r"(^|[;&|(]\s*)source\s+")  # "source" is not POSIX: "." is
    LOOKUP_TIMEOUT = 10  # seconds the person's shell may take to say what an alias stands for

    @staticmethod
    def teach(name, definition=None):
        """tinker learn NAME [DEFINITION]: NAME from now on, an alias of DEFINITION (checked as
        POSIX shell) or, without one, as the person's shell or PATH has it; "none" forgets it."""
        name, known = name.strip(), TaughtCommands.all()
        if definition is not None and definition.strip().lower() == "none":
            if known.pop(name, None) is None:
                raise SystemExit(f"{name} was not taught (tinker memory lists the commands taught)")
            TaughtCommands.save(known)
            print(f"Forgot the command {name} (in {COMMANDS_FILE})")
            return
        if definition is None:
            entry = TaughtCommands.look_up(name)
        elif not definition.strip():
            raise SystemExit(f"tinker learn {name}: the definition is empty: give what {name} stands for, or leave "
                             f"it out to read it from your shell")
        else:
            entry = {"kind": "alias", "definition": definition.strip(), "from": "given"}
        if entry["kind"] == "alias":
            if not TaughtCommands.NAME_RE.fullmatch(name):
                raise SystemExit(f"{name!r} cannot be an alias Tinker defines: letters, digits and _ only, not starting "
                                 f"with a digit")
            entry["definition"] = TaughtCommands.SOURCE_RE.sub(r"\1. ", entry["definition"])
            problem = TaughtCommands.syntax_problem(name, entry["definition"])
            if problem:
                raise SystemExit(f"{name} stands for `{entry['definition']}`, which is not valid POSIX shell "
                                 f"({problem}); give a definition that is")
        known[name] = dict(entry, taught=now_text())
        TaughtCommands.save(known)
        print(f"Learned the command {name}: {TaughtCommands.describe(known[name])} (in {COMMANDS_FILE})")
        print("The toolchain uses it where it fits (each command runs in a new shell: e.g. "
              f"`{name} && ...`); SPEC.md and README show it spelled out.")

    @staticmethod
    def all():
        """{name: entry} of the commands taught (a broken commands.json is an error, never overwritten)."""
        data = JsonFile.read(COMMANDS_FILE, {}, keeps_work=True) or {}
        commands = data.get("commands", {})
        if not isinstance(commands, dict) or not all(isinstance(e, dict) and e.get("kind") in ("alias", "program")
                                                     for e in commands.values()):
            raise SystemExit(f"{COMMANDS_FILE}: \"commands\" must map each name to {{\"kind\": \"alias\", \"definition\": "
                             f"...}} or {{\"kind\": \"program\", ...}}: fix it, or delete it to teach them again")
        return commands

    @staticmethod
    def save(known):
        JsonFile.write(COMMANDS_FILE, {"commands": known})

    @staticmethod
    def look_up(name):
        """What NAME is for the person: an alias in their shell (asked once, now), else a program
        on PATH; neither is an error that says to give the definition."""
        found = TaughtCommands.alias(name)
        if found:
            return found
        shell = os.environ.get("SHELL", "")
        path = shutil.which(name)
        if path:
            return {"kind": "program", "path": path, "from": "PATH"}
        raise SystemExit(f"{name} is neither an alias in your shell ({shell or 'SHELL is not set'}) nor a program on "
                         f"PATH: give what it stands for, e.g. tinker learn {name} \". .venv/bin/activate\"")

    @staticmethod
    def alias(name):
        """NAME as an alias in the person's shell ({kind, definition, from}), or None: a name of
        one word only, asked of their interactive shell once, now."""
        shell = os.environ.get("SHELL", "")
        if not shell or not TaughtCommands.NAME_RE.fullmatch(name):
            return None
        try:
            proc = subprocess.run([shell, "-i", "-c", f"alias {name}"], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=TaughtCommands.LOOKUP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in proc.stdout.splitlines():
            match = re.fullmatch(rf"(?:alias\s+)?{re.escape(name)}=(.+)", line.strip())
            if match:
                return {"kind": "alias", "definition": TaughtCommands.unquote(match.group(1)),
                        "from": f"your {Path(shell).name} alias"}
        return None

    @staticmethod
    def syntax_problem(name, definition):
        """Why NAME() { DEFINITION } is not valid POSIX shell ("" when it is), checked by sh -n."""
        try:
            proc = subprocess.run(["sh", "-n", "-c", TaughtCommands.function(name, definition)], capture_output=True,
                                  text=True, stdin=subprocess.DEVNULL, timeout=TaughtCommands.LOOKUP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"sh could not check it: {exc}"
        return "" if proc.returncode == 0 else first_line(proc.stderr, 160) or f"sh -n exited with {proc.returncode}"

    @staticmethod
    def describe(entry):
        if entry.get("kind") == "program":
            return f"the program {entry.get('path', '?')} ({entry.get('from', 'PATH')})"
        return f"stands for `{entry.get('definition', '')}` ({entry.get('from', 'given')})"

    @staticmethod
    def unquote(value):
        """An alias's value as the shell prints it ('source .venv/bin/activate'), without its quotes."""
        try:
            words = shlex.split(value)
        except ValueError:
            return value
        return words[0] if len(words) == 1 and value[:1] in "'\"" else value

    @staticmethod
    def function(name, definition):
        return f"{name}() {{ {definition}\n}}\n"

    @staticmethod
    def spelled_out(command):
        """The command with each alias it uses replaced by what it stands for (for SPEC.md and the
        README: they work for anyone)."""
        for name, entry in TaughtCommands.all().items():
            if entry["kind"] == "alias":
                command = re.sub(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])",
                                 lambda m, meant=entry["definition"]: meant, command)
        return command

    @staticmethod
    def prelude(command):
        """The definitions, as shell functions, of the aliases this command uses ("" for none)."""
        return "".join(TaughtCommands.function(name, entry["definition"])
                       for name, entry in TaughtCommands.all().items()
                       if entry["kind"] == "alias" and TaughtCommands.uses(command, name))

    @staticmethod
    def uses(command, name):
        return re.search(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])", command) is not None

    @staticmethod
    def prompt_part():
        """The commands taught, for the toolchain prompts (an optional part)."""
        lines = [f"- {name}: " + ("a program on this machine" if entry["kind"] == "program"
                                  else f"stands for `{entry['definition']}`")
                 for name, entry in TaughtCommands.all().items()]
        return Part("The person's own commands (use them where they fit; each command runs in a new shell)",
                    "\n".join(lines), atomic=True)


def online():
    """Whether this machine reaches the internet at all: a connection opened and closed (nothing
    sent) to well-known public addresses. It tells a command that failed because the network is
    gone, when its output does not say so, from one that failed for a reason of its own."""
    for address in ONLINE_PROBES:
        try:
            socket.create_connection(address, timeout=3).close()
            return True
        except OSError:
            continue
    return False


class Toolchain:
    """Every command written the way a person types it in the project folder. setup (list): what
    prepares it for development (the project's own environment, the test tools); activate: what
    makes a shell use that environment ("" when the language needs none), run before every other
    command; use (list): what a person runs once to use it (empty when the language as installed
    is enough); run: how a person starts it after that, when it is an app; check, test_all,
    test_one ({file} {name}), format (the model never formats code) and lint. Judged only by
    exit code and output."""
    COMMANDS = ("check", "test_all", "test_one", "run", "format", "lint")
    REQUIRED = ("test_all", "test_one")
    NO_TESTS_RE = re.compile(r"\bno tests? (ran|run|to run|found|collected|matched)\b|\b0 (tests|passed)\b|"
                             r"\bran 0 tests\b|\btests?\s*0\b", re.I)
    ENV_TARGET = re.compile(r"(?<![\w.-])\.env(?![\w.-])")  # the settings file itself, not .env.example
    # a command that failed because the network did (any package manager): it waits for the network, nothing more
    NETWORK_RE = re.compile(r"could not resolve|resolve host|name or service not known|nodename nor servname|"
                            r"temporary failure in name resolution|network is unreachable|no route to host|"
                            r"connection (refused|reset|aborted|timed out)|failed to establish a new connection|"
                            r"newconnectionerror|max retries exceeded|read timed out|\b(enotfound|eai_again|"
                            r"econnrefused|econnreset|etimedout)\b|spurious network error|failed to download|"
                            r"could not fetch url|unable to (connect|reach)|you are offline|network error", re.I)

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        setup = data.get("setup") or []
        setup = [str(c) for c in (setup if isinstance(setup, list) else [setup]) if str(c).strip()]
        self.setup = [c for c in setup if not self.ENV_TARGET.search(c)]
        self.dropped = [c for c in setup if self.ENV_TARGET.search(c)]  # Tinker makes .env itself
        use = data.get("use") or []
        self.use = [str(c) for c in (use if isinstance(use, list) else [use]) if str(c).strip()]
        self.activate = str(data.get("activate") or "").strip()
        for key in self.COMMANDS:
            setattr(self, key, str(data.get(key) or "").strip())
        self.lint_named = bool(self.lint)  # a linter, or "none" for a language that has none
        self.lint = "" if self.lint.lower() == "none" else self.lint

    def __bool__(self):
        return bool(self.test_all and self.test_one)

    def summary(self):
        return (f"setup {' && '.join(self.setup) or '-'} | activate {self.activate or '-'} | check "
                f"{self.check or '-'} | tests {self.test_all} | use {' && '.join(self.use) or '-'} | run "
                f"{self.run or '-'} | format {self.format or '-'} | lint {self.lint or '-'}")

    def shell(self, command):
        """The command as a person runs it: in the activated environment, when there is one."""
        return f"{self.activate} && {command}" if self.activate else command

    def run_setup(self, ws, commands=None):
        """Run the setup commands (or only these of them): (ok, what failed, the commands that wait
        for the network). A command that failed because
        the network did is set aside (the network is a plugin: it may come back) and the rest run;
        any other failure stops setup with the failing command, its exit code and its output, so
        whoever fixes it knows which command failed even when it printed nothing."""
        waiting = []
        for command in self.setup if commands is None else commands:  # in the environment as soon as the setup has made it
            active = self.activate and ws.run(self.activate, timeout=SETUP_TIMEOUT, quiet=True)[0] == 0
            code, output = ws.run(self.shell(command) if active else command, timeout=SETUP_TIMEOUT)
            if code != 0 and (self.NETWORK_RE.search(output) or not online()):
                waiting.append(command)
                if commands is None:  # said once, not at every try again
                    print(f"  waits for the network: $ {command}")
            elif code != 0:
                ended = f"timed out after {SETUP_TIMEOUT}s" if code is None else f"exit code {code}"
                return False, f"$ {command}  failed ({ended})\n{output.strip() or '(it printed nothing)'}", waiting
        return True, "", waiting

    def to_dict(self):
        data = {"setup": self.setup, "activate": self.activate, "use": self.use,
                **{key: getattr(self, key) for key in self.COMMANDS}}
        if self.lint_named and not self.lint:
            data["lint"] = "none"
        return data

    def run_lint(self, ws):
        """(ok, output) of the linter over the project, when the toolchain has one."""
        if not self.lint:
            return True, ""
        code, output = ws.run(self.shell(self.lint), timeout=SETUP_TIMEOUT, quiet=True)
        return code == 0, output

    def run_check(self, ws):
        """(ok, output): the fast compile or lint, when the toolchain has one."""
        if not self.check:
            return True, ""
        code, output = ws.run(self.shell(self.check), quiet=True)
        return code == 0, f"$ {self.check}\n{output}"

    @staticmethod
    def reached_no_test(code, output, refs):
        """The test command failed without reaching any test (a missing framework, tool or
        command): no code change can fix that. Language-neutral: no test file or name in its output.
        A run that timed out is not one: some test hangs, which the fix loop deals with."""
        if code == 0 or (code is None and output != Workspace.REFUSED):
            return False
        if code in (126, 127) or code is None:
            return True
        return not any(r.file in output or (r.name and r.name in output) for r in refs)

    def run_all(self, ws):
        code, output = ws.run(self.shell(self.test_all), timeout=self.suite_timeout(), quiet=True)
        if code is None and output != Workspace.REFUSED:
            output += f"\n(the tests did not finish within {self.suite_timeout()}s: some test hangs)"
        return code, output

    def merged(self, changes):
        """This toolchain with the values a dict of changes gives replacing its own."""
        data = self.to_dict()
        for key, value in (changes or {}).items():
            if key in data and value not in (None, "", []):
                data[key] = value
        return Toolchain(data)

    def run_test(self, ws, ref):
        """(state, output) for one test: passes / fails; "no test ran" and "did not finish" are failures."""
        command = ref.command(self.test_one)
        code, output = ws.run(self.shell(command), timeout=self.test_timeout(), quiet=True)
        if code is None and output != Workspace.REFUSED:
            return "fails", (f"$ {command}\nthe test did not finish within {self.test_timeout()}s: it hangs (an "
                             f"endless loop, a wait for input, a program or server left running, or a real network call). "
                             f"Output so far:\n{output}")
        if code == 0 and not self.NO_TESTS_RE.search(output):
            return "passes", output
        if code == 0:
            return "fails", f"$ {command}\nthe command ran no test: is the test named {ref.name}?\n{output}"
        return "fails", f"$ {command}\n{output}"

    def problems(self):
        problems = [f"toolchain.{key} is missing" for key in self.REQUIRED if not getattr(self, key)]
        if not self.lint_named:
            problems.append('toolchain.lint is missing: the language\'s usual linter, or "none" when it has none')
        if self.test_one and "{name}" not in self.test_one and "{file}" not in self.test_one:
            problems.append("toolchain.test_one must contain {name} (and {file} if the runner needs it)")
        return problems

    @staticmethod
    def suite_timeout():
        """All the (fast) tests together; past this, one run per test finds the one that hangs."""
        return 2 * Toolchain.test_timeout()

    @staticmethod
    def test_timeout():
        """Seconds one test may take: longer means it hangs (test_timeout in config.json)."""
        return int(Config.get("test_timeout"))


class Workspace:
    """The project folder: its files (paths kept inside it) and the commands run in it."""
    REFUSED = "refused: the command is unsafe"  # the output of a command that is never run
    UNSAFE_COMMAND = re.compile(r"(^|[;&|]\s*)sudo\b|\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\.\.)|\bmkfs\b|\bdd\s+if=|"
                                r"(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b|>\s*/(etc|usr|bin|dev/sd)")
    PLACEHOLDER_RE = re.compile(r"^(<.*>|your[\w-]*|.*_here|change[-_ ]?me|replace[-_ ]?me|x{3,}|\*{3,}|todo|tbd|"
                                r"none|null|example[\w-]*|placeholder|insert[\w-]*|put[\w-]*|\.\.\.|"
                                r"tinker-start-check)$", re.I)

    def __init__(self, root):
        self.root = root

    def run(self, command, timeout=COMMAND_TIMEOUT, quiet=False):
        """Run one planned command in a POSIX shell in the project folder,
        with the person's aliases it uses defined first (TaughtCommands). Returns (exit code, or
        None if refused or timed out; output)."""
        if self.unsafe(command):
            return None, Workspace.REFUSED
        started = time.perf_counter()
        proc = subprocess.Popen(TaughtCommands.prelude(command) + command, shell=True, cwd=self.root,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace",
                                start_new_session=(os.name != "nt"))
        try:
            output, _ = proc.communicate(timeout=timeout)
            code, status = proc.returncode, f"exit code {proc.returncode}"
        except subprocess.TimeoutExpired:
            self.kill(proc)
            output, _ = proc.communicate()
            code, status = None, f"timed out after {timeout}s"
        if not quiet:
            Console.step(f"$ {command}  [{status}]", started)
        return code, (output or "").replace(str(self.root) + os.sep, "")

    def exists(self, rel_path):
        return (self.root / rel_path).is_file()

    def read(self, rel_path):
        full = self.root / rel_path
        return full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""

    def apply(self, reply, may_change, windows=None, empty_ok=True):
        """Write a reply's FILE blocks (whole files, each checked by may_change(path, exists) ->
        None or why not) and its LINES blocks (only inside the windows shown). empty_ok: a new
        file may be empty (a package marker; never a test file)."""
        change, windows = Change(), windows or {}
        for path, body in reply.files():
            reason = self.refusal(path, body, may_change, windows, empty_ok)
            if reason:
                change.notes.append(f"{path}: {reason}")
            elif path not in change.backups:
                change.backups[path] = self.read(path) if self.exists(path) else None
                self.write(path, body)
        self.apply_line_edits(reply, windows, change)
        return change

    def prepare_env(self):
        """.env from .env.example when missing; placeholder values blanked either way."""
        example, env = self.root / ENV_EXAMPLE, self.root / ENV_FILE
        if example.is_file() and not env.is_file():
            env.write_text(self.env_without_placeholders(example.read_text(encoding="utf-8", errors="replace")),
                           encoding="utf-8")
            unset = [l.split("=")[0] for l in env.read_text(encoding="utf-8").splitlines() if l.endswith("=")]
            print(f"  created {ENV_FILE} from {ENV_EXAMPLE}" + (f"; set {', '.join(unset)} in it" if unset else ""))
        elif env.is_file():
            text = env.read_text(encoding="utf-8", errors="replace")
            if self.env_without_placeholders(text).strip() != text.strip():
                env.write_text(self.env_without_placeholders(text), encoding="utf-8")
                print(f"  {ENV_FILE}: cleared placeholder values (a placeholder is not a real setting)")

    @staticmethod
    def unsafe(command):
        if Workspace.UNSAFE_COMMAND.search(command):
            print(f"  $ {command}\n    REFUSED: sudo, deleting outside the project, piping a download into a "
                  f"shell or writing to the system are never run")
            return True
        return False

    @staticmethod
    def kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL) if os.name != "nt" else proc.kill()
        except OSError:
            pass

    def refusal(self, path, body, may_change, windows, empty_ok=True):
        if path.lower() in Reply.PLACEHOLDER_PATHS:
            return "not a real file path"
        try:
            self.resolve(path, for_write=True)
        except ToolError as exc:
            return str(exc)
        if path in windows:
            return "it was shown in part: change it with a LINES block"
        if not body.strip() and (not empty_ok or self.read(path).strip()):  # a new one may be (a package marker)
            return "the reply gave it empty"
        return may_change(path, self.exists(path))

    def write(self, rel_path, content):
        full = self.resolve(rel_path, for_write=True)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content if not content or content.endswith("\n") else content + "\n", encoding="utf-8")

    def apply_line_edits(self, reply, windows, change):
        edits = {}
        for path, a, b, new in reply.line_edits():
            shown = windows.get(path)
            if not shown:
                change.notes.append(f"{path}: LINES blocks only change files shown as numbered lines")
            elif not shown[0] <= a <= b <= shown[1]:
                change.notes.append(f"{path}: lines {a}-{b} are outside the lines shown ({shown[0]}-{shown[1]})")
            elif path not in change.backups:
                edits.setdefault(path, []).append((a, b, new))
        for path, ranges in edits.items():
            old = self.read(path)
            lines, taken = old.splitlines(), []
            for a, b, new in sorted(ranges, reverse=True):
                if any(not (b < x or a > y) for x, y in taken):
                    change.notes.append(f"{path}: overlapping LINES blocks; kept the first")
                    continue
                lines[a - 1:b] = new
                taken.append((a, b))
            change.backups[path] = old
            self.write(path, "\n".join(lines) + "\n")

    @staticmethod
    def env_without_placeholders(text):
        """A .env text with placeholder values blanked: the app then sees the setting as unset."""
        out = []
        for line in text.splitlines():
            body = line.strip()
            if body and not body.startswith("#") and "=" in body:
                key, value = body.split("=", 1)
                if Workspace.is_placeholder(value.split(" #", 1)[0]):
                    line = f"{key.rstrip()}="
            out.append(line)
        return "\n".join(out) + "\n"

    def resolve(self, rel_path, for_write=False):
        if not rel_path or not isinstance(rel_path, str):
            raise ToolError("path is required")
        candidate = Path(rel_path.strip())
        if len(rel_path) > 300 or any(len(piece) > 120 for piece in candidate.parts) or \
                any(ch in rel_path for ch in "\n\r\t`<>|*?\"'"):
            raise ToolError(f"'{first_line(rel_path, 60)}' is not a usable file path")
        if candidate.is_absolute() or rel_path.strip().startswith("~"):
            raise ToolError(f"path '{rel_path}' must be relative to the project")
        full = (self.root / candidate).resolve()
        if full == self.root or self.root not in full.parents:
            raise ToolError(f"path '{rel_path}' is outside the project")
        if for_write and full.relative_to(self.root).parts[0] in (STATE_DIR, ".git"):
            raise ToolError(f"path '{rel_path}' is protected")
        return full

    def restore(self, snapshot):
        for path, text in snapshot.items():
            if text is None:
                (self.root / path).unlink(missing_ok=True)
            else:
                (self.root / path).write_text(text, encoding="utf-8")

    def lines(self, rel_path):
        return len(self.read(rel_path).splitlines())

    def revert(self, change):
        self.restore(change.backups)

    def snapshot(self, paths):
        return {p: (self.read(p) if self.exists(p) else None) for p in paths}

    def start(self, command):
        """Start a command that keeps running (a server) in the background: (process, output
        file), or None when refused. Stop it with stop()."""
        if self.unsafe(command):
            return None
        output_file = self.root / STATE_DIR / "app-output.txt"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as out:
            proc = subprocess.Popen(TaughtCommands.prelude(command) + command, shell=True, cwd=self.root,
                                    stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
        return proc, output_file

    def stop(self, proc):
        """Stop a started command and everything it started: politely, then for good."""
        if proc.poll() is None and os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.kill(proc)
        proc.wait()

    @staticmethod
    def is_placeholder(value):
        """A value that only shows where a real one goes (your_api_key_here, <key>, ...)."""
        return not value or bool(Workspace.PLACEHOLDER_RE.match(value.strip().strip("'\"")))


class TestRef:
    """A criterion's test: "tests/test_app.py::test_x" -> file tests/test_app.py, name test_x."""

    def __init__(self, test):
        file, _, name = str(test).rpartition("::")
        self.file, self.name = (file, name) if file else (str(test), "")

    def __str__(self):
        return f"{self.file}::{self.name}"

    def present(self, ws):
        return ws.exists(self.file) and (not self.name or self.name_at(ws.read(self.file), self.name) >= 0)

    @staticmethod
    def name_at(text, name):
        match = re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) if name else None
        return match.start() if match else -1

    def excerpt(self, ws, names):
        """The test's own lines (from its name to the next known test of the file), after the
        file's opening lines (imports, shared setup)."""
        text = ws.read(self.file)
        start = self.name_at(text, self.name)
        if start < 0:
            return text
        others = sorted(p for p in (self.name_at(text, n) for n in names if n != self.name) if p >= 0)
        line_start, end = self.span(text, names)
        first = text.rfind("\n", 0, min([start] + others)) + 1
        if first >= line_start:
            return text[:end].rstrip()  # the first test: the file's opening lines come with it
        head = text[:first].splitlines()[:15]
        while head and (not head[-1].strip() or head[-1].strip().startswith(("@", "#[", "["))):
            head.pop()  # blank lines and the first test's decorators
        return ("\n".join(head) + "\n...\n" if head else "") + text[line_start:end].rstrip()

    def span(self, text, names):
        """(start, end) of the test's own lines in its file's text: from its definition (with the
        decorators above it) to the definition of the next known test, or the end of the file."""
        start = self.name_at(text, self.name)
        later = sorted(p for p in (self.name_at(text, n) for n in names if n != self.name) if p > start)
        end = self.definition_start(text, later[0]) if later else len(text)
        return self.definition_start(text, start), end

    def remove(self, ws, names):
        """The test's own lines cut from its file (a test of a withdrawn criterion); True if it was there."""
        text = ws.read(self.file)
        if not self.name or self.name_at(text, self.name) < 0:
            return False
        start, end = self.span(text, names)
        ws.write(self.file, text[:start].rstrip() + "\n\n\n" + text[end:].lstrip() if text[end:].strip()
                 else text[:start].rstrip() + "\n")
        return True

    def command(self, template):
        return template.replace("{file}", self.file).replace("{name}", self.name).replace("{id}", str(self))

    @staticmethod
    def definition_start(text, at):
        """Where a test's definition begins: its line, or a decorator line or two above it."""
        line_start = text.rfind("\n", 0, at) + 1
        for _ in range(2):
            prev = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
            if line_start and text[prev:line_start].strip().startswith(("@", "#[", "[")):
                line_start = prev
        return line_start


class Output:
    """What a command printed, read for what went wrong (language-neutral: lines that look like
    errors, not parsed)."""
    ERROR_LINE_RE = re.compile(r"error|fail|assert|exception|expected|panic|undefined|cannot|not found|"
                               r"no module|missing", re.I)
    EXACT_ERROR_RE = re.compile(r"\b\w*(Error|Exception|Panic)\b|\bassert", re.I)
    NOISE_RE = re.compile(r"0x[0-9a-f]+|\d+(\.\d+)?\s*(ms|s|sec|seconds)\b|\b\d+\b", re.I)
    OWN_CODE_UNSEEN = "the test tool does not find the project's code"
    LOCATION_RE = re.compile(r"([\w./\\-]+\.\w+)[\"']?(?:, line |:|\()\d+")  # path, line N / path:N / path(N
    NOT_FOUND_RE = re.compile(r"No module named ['\"]?([\w.]+)|Cannot find module ['\"]([^'\"]+)|"
                              r"cannot find package ['\"]([^'\"]+)|unresolved import `?([\w:]+)|"
                              r"Could not resolve ['\"]([^'\"]+)|cannot load such file -- (\S+)", re.I)
    UNLOADABLE_RE = re.compile(r"syntax\s*error|parse\s*error|indentation\s*error|unexpected (token|end|eof|indent)|"
                               r"unterminated|invalid syntax|compil(e|ation) (error|failed)|(can|could)\s*not compile|"
                               r"unexpected character", re.I)

    @staticmethod
    def key_line(output, limit=200):
        """The line that says what went wrong: the last error or exception line, else the first
        failure-looking line, else the last line (banners such as === FAILURES === skipped)."""
        lines = [l.strip(" #") for l in output.splitlines() if l.strip(" #") and not Output.is_banner(l.strip(" #"))]
        exact = [l for l in lines if Output.EXACT_ERROR_RE.search(l)]
        if exact:
            return first_line(exact[-1], limit)
        loose = [l for l in lines if Output.ERROR_LINE_RE.search(l)]
        return first_line((loose[:1] or lines[-1:] or ["no output"])[0], limit)

    @staticmethod
    def misses_own_code(output, paths):
        """The project's own module a test could not import ("No module named 'moods'", "Cannot
        find module './moods'", ...) when it is one of `paths`: the test tool does not see the app's
        code (a search path, a package setting), which no change to the code fixes. Language-
        neutral: the usual not-found wordings, the name matched against the project's file names."""
        stems = {Path(p).stem.lower() for p in paths} | {Path(p).parent.name.lower() for p in paths}
        stems.discard("")
        for match in Output.NOT_FOUND_RE.finditer(output):
            name = next(g for g in match.groups() if g).strip("./\\")
            pieces = [piece.lower() for piece in re.split(r"[./\\:]+", name) if piece]
            if pieces and (pieces[0] in stems or Path(name).stem.lower() in stems):
                return name
        return ""

    @staticmethod
    def unloadable(output, path):
        """True when the output says the file at `path` itself does not parse or compile, so nothing
        in it can run. Language-neutral: a line with a syntax or parse error's words that names the
        file, or whose nearest location (path, line N / path:N) is in it. A test that fails to load
        because the code it imports is broken is not this: the nearest location is in that code."""
        lines = output.splitlines()
        return bool(path) and any(
            Output.UNLOADABLE_RE.search(line) and
            (Output.names(line, path) or Output.names(Output.nearest_location(lines, index), path))
            for index, line in enumerate(lines))

    @staticmethod
    def nearest_location(lines, index, reach=6):
        """The file named by the location nearest to lines[index] (within reach lines), or ""."""
        around = range(max(0, index - reach), min(len(lines), index + reach + 1))
        for i in sorted(around, key=lambda i: abs(i - index)):
            match = Output.LOCATION_RE.search(lines[i])
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def names(text, path):
        """Whether text names this project file (as given, or by a longer path ending with it)."""
        return re.search(rf"(?<![\w.-]){re.escape(path)}(?![\w-])", text.replace("\\", "/")) is not None

    @staticmethod
    def is_banner(line):
        return sum(c in "=-_*~#+ " for c in line) >= len(line) / 2

    @staticmethod
    def tool_failure(output, workdir):
        """A tooling failure as text that recurs across projects: its key line without the project's
        path, timings, addresses and counts (and tests that cannot see the project's code are that,
        whatever its modules are called), so the fix learned on one project is replayed on the next."""
        if Output.OWN_CODE_UNSEEN in output:
            return Output.OWN_CODE_UNSEEN
        return Output.NOISE_RE.sub("#", Output.key_line(output.replace(str(workdir), ".")))

    @staticmethod
    def failure_text(output, limit=OUTPUT_CHARS):
        """The end of a command's output, where runners print failures and their summary."""
        text = "\n".join(line for line in Output.compact_lines(output.strip()).splitlines() if line.strip())
        return text if len(text) <= limit else "[...]\n" + text[-(limit - 6):]

    @staticmethod
    def mentioned_in(output, paths):
        """Project files an output refers to, most mentioned first: by their path (also within a
        longer one, such as an absolute path), else by their name alone; never inside another file's
        name (count.py is not mentioned by tests/test_count.py)."""
        counts = []
        for path in paths:
            n = len(re.findall(rf"(?<![\w.-]){re.escape(path)}(?![\w-])", output)) or \
                len(re.findall(rf"(?<![\w./-]){re.escape(Path(path).name)}(?![\w-])", output))
            if n:
                counts.append((-n, path))
        return [p for _, p in sorted(counts)]

    @staticmethod
    def compact_lines(text, limit=360):
        """Very long lines (whole pages in assertion messages) shortened to their two ends."""
        return "\n".join(line if len(line) <= limit else
                         f"{line[:limit * 2 // 3]} ...[{len(line) - limit} chars]... {line[-limit // 3:]}"
                         for line in text.splitlines())

    @staticmethod
    def signature(output):
        """What identifies a failure, without timings, addresses and counts."""
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        errors = [l for l in lines if Output.ERROR_LINE_RE.search(l)]
        key = "\n".join((errors or lines)[-4:])
        return hashlib.sha1(Output.NOISE_RE.sub("#", key).encode()).hexdigest()[:12]


class Change:
    """Files a reply changed: their previous text (None for a file it created), and what was refused."""

    def __init__(self):
        self.backups, self.notes = {}, []

    def __bool__(self):
        return bool(self.backups)

    @property
    def files(self):
        return list(self.backups)

    @property
    def created(self):
        return [p for p, old in self.backups.items() if old is None]


class FileView:
    """Builds a prompt (for Model.ask) of fixed parts, then each candidate file whole while it
    fits, else, for the first few that do not, a numbered window around the lines that matter,
    then optional tail parts. Files are placed before long prose, which may be shortened, since
    a file shown in part is much harder to change. Records what was shown."""

    def __init__(self, workspace, fixed, candidates, tail=(), output="", hints="", windows=2):
        self.ws, self.fixed, self.tail = workspace, list(fixed), list(tail)
        self.candidates = list(dict.fromkeys(candidates))
        self.output, self.hints, self.max_windows = output, hints, windows
        self.whole, self.windows = set(), {}

    def __call__(self, limit):
        self.whole, self.windows = set(), {}
        room = limit - sum(p.floor_size() for p in self.fixed if p.required) - 300
        files = []
        for path in self.candidates:
            if room < 250 or not self.ws.exists(path):
                continue
            part = self.whole_file(path, room) or self.window(path, room)
            if part:
                files.append(part)
                room -= part.size()
        how = [Part("Changing a file shown in part", Prompts.LINES_HOWTO, True)] if self.windows else []
        return self.fixed + files + how + self.tail

    @property
    def shown(self):
        return self.whole | set(self.windows)

    def whole_file(self, path, room):
        part = Part(f"File {path}", self.ws.read(path), True, exact=True)
        if part.size() <= room:
            self.whole.add(path)
            return part
        return None

    def window(self, path, room):
        if len(self.windows) >= self.max_windows or room < 700:
            return None
        lines = self.ws.read(path).splitlines()
        first, last, body = self.window_view(lines, self.focus_line(path, lines, self.output, self.hints), room - 140)
        self.windows[path] = (first, last)
        return Part(f"File {path}, lines {first}-{last} of {len(lines)} (numbered; the rest is not shown)",
                    body, True, exact=True)

    @staticmethod
    def focus_line(path, lines, output, hints):
        """The line of a file that matters most: one the output points at (path:line, line N), else
        the first line using a name from the hints, else the top."""
        for name in (re.escape(path), re.escape(Path(path).name)):
            for match in re.finditer(rf"{name}[\"']?(?:, line |:|\()(\d+)", output):
                if 1 <= int(match.group(1)) <= len(lines):
                    return int(match.group(1))
        for word in sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", hints)), key=len, reverse=True)[:40]:
            for index, line in enumerate(lines, 1):
                if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", line):
                    return index
        return 1

    @staticmethod
    def window_view(lines, center, room):
        """Numbered lines around `center` that fit in `room` chars: (first, last, text)."""
        start = end = max(1, min(center, len(lines)))
        size = lambda a, b: sum(len(lines[i - 1]) + 7 for i in range(a, b + 1))
        grown = True
        while grown:
            grown = False
            for a, b in ((start - 1, end), (start, end + 1)):
                if a >= 1 and b <= len(lines) and size(a, b) <= room:
                    start, end, grown = a, b, True
        return start, end, "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))


class ToolError(Exception):
    """A rejected path or command."""
