import re
from collections import namedtuple
lazy import hashlib
lazy import os
lazy import shlex
lazy import shutil
lazy import signal
lazy import socket
lazy import subprocess
lazy import time
lazy from pathlib import Path

from .settings import (
    COMMAND_TIMEOUT,
    ENV_EXAMPLE,
    ENV_FILE,
    FAILURE_OUTPUT_CHARS,
    ONLINE_PROBES,
    SETUP_TIMEOUT,
    STATE_DIR,
    Config,
    Home,
    NotAPytestClass,
)
from .text import field, first_line, now_text
lazy from .messages import human_duration, say
lazy from .prompts import Part, Prompts, Reply

TestOutcome = namedtuple("TestOutcome", "state output")
CommandOutcome = namedtuple("CommandOutcome", "ok output")
Finished = namedtuple("Finished", "code output ending")
Span = namedtuple("Span", "start end")


class Toolchain:
    COMMANDS = ("check", "test_all", "test_one", "run", "format", "lint")
    REQUIRED = ("test_all", "test_one")
    NO_TESTS_RE = re.compile(
        r"\bno tests? (ran|run|to run|found|collected|matched)\b|\b0 (tests|passed)\b|"
        r"\bran 0 tests\b|\btests?\s*0\b|\b\d+ deselected\b",
        re.I,
    )
    TESTS_RAN_RE = re.compile(
        r"\b[1-9]\d* (tests?|passed|passing|failed|failing)\b|\bran [1-9]\d* tests?\b", re.I
    )
    ENV_FILE_RE = re.compile(r"(?<![\w.-])\.env(?![\w.-])")
    MACHINE_TOOLCHAIN_CHANGE_RE = re.compile(
        r"\brustup\s+(default|override|update|toolchain|component|target)\b|\bcargo\s+install\b|"
        r"\b(npm|pnpm|yarn)\s+(i|install|add)\b[^&|;]*\s(-g|--global)\b|\byarn\s+global\b|"
        r"\bpip3?\s+install\b[^&|;]*\s--user\b|\b(brew|apt|apt-get|dnf|yum|pacman|port)\s+install\b|"
        r"\b(go|gem)\s+install\b|\b(pyenv|nvm|asdf)\s+(install|global|use)\b"
    )
    NETWORK_FAILURE_RE = re.compile(
        r"could not resolve|resolve host|name or service not known|nodename nor servname|"
        r"temporary failure in name resolution|network is unreachable|no route to host|"
        r"connection (refused|reset|aborted|timed out)|failed to establish a new connection|"
        r"newconnectionerror|max retries exceeded|read timed out|\b(enotfound|eai_again|"
        r"econnrefused|econnreset|etimedout)\b|spurious network error|failed to download|"
        r"could not fetch url|unable to (connect|reach)|you are offline|network error",
        re.I,
    )

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        setup = self.command_list(data.get("setup"))
        self.setup = [
            command
            for command in setup
            if not self.ENV_FILE_RE.search(command) and not self.MACHINE_TOOLCHAIN_CHANGE_RE.search(command)
        ]
        self.env_commands_dropped = [command for command in setup if self.ENV_FILE_RE.search(command)]
        self.machine_changing_commands = [
            command for command in setup if self.MACHINE_TOOLCHAIN_CHANGE_RE.search(command)
        ]
        self.use = self.command_list(data.get("use"))
        self.activate = field(data, "activate")
        for key in self.COMMANDS:
            setattr(self, key, field(data, key))
        self.lint_declared = bool(self.lint)
        self.lint = "" if self.lint.lower() == "none" else self.lint

    @staticmethod
    def command_list(value):
        commands = value or []
        return [
            str(command)
            for command in (commands if isinstance(commands, list) else [commands])
            if str(command).strip()
        ]

    def __bool__(self):
        return bool(self.test_all and self.test_one)

    def summary(self):
        return (
            f"setup {' && '.join(self.setup) or '-'} | activate {self.activate or '-'} | check "
            f"{self.check or '-'} | tests {self.test_all} | use {' && '.join(self.use) or '-'} | run "
            f"{self.run or '-'} | format {self.format or '-'} | lint {self.lint or '-'}"
        )

    def to_dict(self):
        data = {
            "setup": self.setup,
            "activate": self.activate,
            "use": self.use,
            **{key: getattr(self, key) for key in self.COMMANDS},
        }
        if self.lint_declared and not self.lint:
            data["lint"] = "none"
        return data

    def merged(self, changes):
        data = self.to_dict()
        for key, value in (changes or {}).items():
            if key in data and (isinstance(value, list) or value not in (None, "")):
                data[key] = value
        return Toolchain(data)

    def problems(self):
        problems = [f"toolchain.{key} is missing" for key in self.REQUIRED if not getattr(self, key)]
        if not self.lint_declared:
            problems.append(
                'toolchain.lint is missing: the language\'s usual linter, or "none" when it has none'
            )
        if self.test_one and "{name}" not in self.test_one and "{file}" not in self.test_one:
            problems.append("toolchain.test_one must contain {name} (and {file} if the runner needs it)")
        return problems

    def shell(self, command):
        return f"{self.activate} && {command}" if self.activate else command

    def run_setup(self, workspace, commands=None):
        waiting, again = [], commands is not None
        for command in commands if again else self.setup:
            shell_command = self.in_environment_once_made(workspace, command)
            before = {entry.name for entry in Path(workspace.root).iterdir()}
            code, output = workspace.run(shell_command, timeout=SETUP_TIMEOUT, quiet=again)
            nested = self.new_project_made(Path(workspace.root), before)
            if nested:
                return False, self.nested_project_problem(command, nested), waiting
            if code != 0 and self.failed_for_the_network(output):
                waiting.append(command)
                if not again:
                    say.warn(f"`{command}` waits for the network")
            elif code != 0:
                return False, self.setup_failure(command, code, output), waiting
        return True, "", waiting

    def in_environment_once_made(self, workspace, command):
        active = self.activate and workspace.run(self.activate, timeout=SETUP_TIMEOUT, quiet=True)[0] == 0
        return self.shell(command) if active else command

    @staticmethod
    def new_project_made(root, before):
        own = {entry.name for entry in root.iterdir() if entry.is_file()}
        made = [entry for entry in root.iterdir() if entry.is_dir() and entry.name not in before]
        return next((entry.name for entry in made if any((entry / name).is_file() for name in own)), "")

    @staticmethod
    def nested_project_problem(command, nested):
        return (
            f"$ {command}  made a new project in {nested}/: setup only prepares this project "
            f"(its files are written for it), so this command is not one of its setup"
        )

    def failed_for_the_network(self, output):
        return bool(self.NETWORK_FAILURE_RE.search(output)) or not online()

    @staticmethod
    def setup_failure(command, code, output):
        ended = f"timed out after {SETUP_TIMEOUT}s" if code is None else f"exit code {code}"
        return f"$ {command}  failed ({ended})\n{output.strip() or '(it printed nothing)'}"

    def run_check(self, workspace):
        if not self.check:
            return CommandOutcome(True, "")
        code, output = workspace.run(self.shell(self.check), quiet=True)
        return CommandOutcome(code == 0, f"$ {self.check}\n{output}")

    def run_lint(self, workspace):
        if not self.lint:
            return CommandOutcome(True, "")
        code, output = workspace.run(self.shell(self.lint), timeout=SETUP_TIMEOUT, quiet=True)
        return CommandOutcome(code == 0, output)

    def run_all(self, workspace):
        code, output = workspace.run(self.shell(self.test_all), timeout=self.suite_timeout(), quiet=True)
        if timed_out(code, output):
            output += f"\n(the tests did not finish within {self.suite_timeout()}s: some test hangs)"
        return code, output

    def run_test(self, workspace, ref):
        command = ref.command(self.test_one)
        code, output = workspace.run(self.shell(command), timeout=self.test_timeout(), quiet=True)
        if timed_out(code, output):
            return TestOutcome("fails", self.hang_report(command, output))
        if code == 0 and not self.ran_no_test(output):
            return TestOutcome("passes", output)
        if code == 0:
            return TestOutcome(
                "fails", f"$ {command}\nthe command ran no test: is the test named {ref.name}?\n{output}"
            )
        return TestOutcome("fails", f"$ {command}\n{output}")

    def hang_report(self, command, output):
        return (
            f"$ {command}\nthe test did not finish within {self.test_timeout()}s: it hangs (an "
            f"endless loop, a wait for input, a program or server left running, or a real network call). "
            f"Output so far:\n{output}"
        )

    @staticmethod
    def ran_no_test(output):
        return bool(Toolchain.NO_TESTS_RE.search(output)) and not Toolchain.TESTS_RAN_RE.search(output)

    @staticmethod
    def reached_no_test(code, output, refs):
        if code == 0 or timed_out(code, output):
            return False
        if code in (126, 127) or code is None:
            return True
        return not any(ref.file in output or (ref.name and ref.name in output) for ref in refs)

    @staticmethod
    def suite_timeout():
        return 2 * Toolchain.test_timeout()

    @staticmethod
    def test_timeout():
        return int(Config.get("test_timeout"))


def timed_out(code, output):
    return code is None and output != Workspace.REFUSED_OUTPUT


def online():
    for address in ONLINE_PROBES:
        try:
            socket.create_connection(address, timeout=3).close()
            return True
        except OSError:
            continue
    return False


class Workspace:
    REFUSED_OUTPUT = "refused: the command is unsafe"
    UNSAFE_COMMAND = re.compile(
        r"(^|[;&|]\s*)sudo\b|\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\.\.)|\bmkfs\b|\bdd\s+if=|"
        r"(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b|>\s*/(etc|usr|bin|dev/sd)"
    )
    PLAIN_PLACEHOLDER_RE = re.compile(
        r"^(<.*>|your[_-][\w-]*|[\w-]*_here|change[-_ ]?me|replace[-_ ]?me|x{3,}|\*{3,}|"
        r"\.\.\.|placeholder)$",
        re.I,
    )
    UNUSABLE_PATH_CHARS = "\n\r\t`<>|*?\"'"

    def __init__(self, root):
        self.root = Path(root).resolve()

    def run(self, command, timeout=COMMAND_TIMEOUT, quiet=False):
        if self.unsafe(command):
            return None, Workspace.REFUSED_OUTPUT
        started = time.perf_counter()
        proc = self.spawn(command, stdout=subprocess.PIPE, text=True, errors="replace")
        code, output, status = self.finished(proc, timeout)
        if not quiet:
            say.info(f"Ran `{command}`: {status} ({human_duration(time.perf_counter() - started)})", "▶️")
        return code, (output or "").replace(str(self.root) + os.sep, "")

    def spawn(self, command, stdout, **text):
        return subprocess.Popen(
            TaughtCommands.prelude(command) + command,
            shell=True,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
            **text,
        )

    def finished(self, proc, timeout):
        try:
            output, _ = proc.communicate(timeout=timeout)
            return Finished(proc.returncode, output, f"exit code {proc.returncode}")
        except subprocess.TimeoutExpired:
            self.kill(proc)
            output, _ = proc.communicate()
            return Finished(None, output, f"timed out after {timeout}s")

    @staticmethod
    def unsafe(command):
        if Workspace.UNSAFE_COMMAND.search(command):
            say.stop(
                f"Refused `{command}`: sudo, deleting outside the project, piping a download into a "
                f"shell or writing to the system are never run"
            )
            return True
        return False

    @staticmethod
    def kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL) if os.name != "nt" else proc.kill()
        except OSError:
            pass

    def start(self, command):
        if self.unsafe(command):
            return None
        output_file = self.root / STATE_DIR / "app-output.txt"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as out:
            proc = self.spawn(command, stdout=out)
        return proc, output_file

    def stop(self, proc):
        if proc.poll() is None and os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
            except OSError, subprocess.TimeoutExpired:
                pass
        self.kill(proc)
        proc.wait()

    def exists(self, rel_path):
        full = self.inside(rel_path)
        return full is not None and full.is_file()

    def read(self, rel_path):
        full = self.inside(rel_path)
        return full.read_text(errors="replace") if full and full.is_file() else ""

    def inside(self, rel_path):
        try:
            return self.resolve(rel_path)
        except ToolError:
            return None

    def lines(self, rel_path):
        return len(self.read(rel_path).splitlines())

    def write(self, rel_path, content):
        full = self.resolve(rel_path, for_write=True)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content if not content or content.endswith("\n") else content + "\n")

    def resolve(self, rel_path, for_write=False):
        self.check_usable(rel_path)
        full = (self.root / rel_path.strip()).resolve()
        if full == self.root or self.root not in full.parents:
            raise ToolError(f"path '{rel_path}' is outside the project")
        if for_write and full.relative_to(self.root).parts[0] in (STATE_DIR, ".git"):
            raise ToolError(f"path '{rel_path}' is protected")
        return full

    def check_usable(self, rel_path):
        if not rel_path or not isinstance(rel_path, str):
            raise ToolError("path is required")
        candidate = Path(rel_path.strip())
        if (
            len(rel_path) > 300
            or any(len(piece) > 120 for piece in candidate.parts)
            or any(ch in rel_path for ch in self.UNUSABLE_PATH_CHARS)
        ):
            raise ToolError(f"'{first_line(rel_path, 60)}' is not a usable file path")
        if candidate.is_absolute() or rel_path.strip().startswith("~"):
            raise ToolError(f"path '{rel_path}' must be relative to the project")

    def apply(self, reply, may_change, windows=None, empty_ok=True):
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

    def refusal(self, path, body, may_change, windows, empty_ok=True):
        if path.lower() in Reply.PLACEHOLDER_PATHS:
            return "not a real file path"
        try:
            self.resolve(path, for_write=True)
        except ToolError as exc:
            return str(exc)
        if path in windows:
            return "it was shown in part: change it with a LINES block"
        wrongly_empty = not body.strip() and (not empty_ok or self.read(path).strip())
        if wrongly_empty:
            return "the reply gave it empty"
        return may_change(path, self.exists(path))

    def apply_line_edits(self, reply, windows, change):
        for path, edits in self.edits_inside_windows(reply, windows, change.notes).items():
            old = self.read(path)
            change.backups[path] = old
            self.write(
                path,
                "\n".join(self.edited(old.splitlines(), self.without_overlaps(path, edits, change.notes)))
                + "\n",
            )

    @staticmethod
    def edits_inside_windows(reply, windows, notes):
        edits = {}
        for path, first, last, new in reply.line_edits():
            shown = windows.get(path)
            if not shown:
                notes.append(f"{path}: LINES blocks only change files shown as numbered lines")
            elif not shown[0] <= first <= last <= shown[1]:
                notes.append(
                    f"{path}: lines {first}-{last} are outside the lines shown ({shown[0]}-{shown[1]})"
                )
            else:
                edits.setdefault(path, []).append((first, last, new))
        return edits

    @staticmethod
    def without_overlaps(path, edits, notes):
        taken = []
        for first, last, new in edits:
            if any(
                Workspace.overlap(first, last, taken_first, taken_last)
                for taken_first, taken_last, _ in taken
            ):
                notes.append(
                    f"{path}: lines {first}-{last} overlap an earlier LINES block; kept the earlier one"
                )
            else:
                taken.append((first, last, new))
        return taken

    @staticmethod
    def overlap(first, last, other_first, other_last):
        return not (last < other_first or first > other_last)

    @staticmethod
    def edited(lines, edits):
        bottom_up = sorted(edits, key=lambda edit: edit[0], reverse=True)
        for first, last, new in bottom_up:
            lines[first - 1 : last] = new
        return lines

    def snapshot(self, paths):
        return {path: (self.read(path) if self.exists(path) else None) for path in paths}

    def revert(self, change):
        self.restore(change.backups)

    def restore(self, snapshot):
        for path, text in snapshot.items():
            if text is None:
                (self.root / path).unlink(missing_ok=True)
            else:
                (self.root / path).write_text(text)

    def prepare_env(self):
        example, env = self.root / ENV_EXAMPLE, self.root / ENV_FILE
        if not example.is_file() or env.is_file():
            return
        env.write_text(self.env_without_placeholders(example.read_text(errors="replace")))
        unset = [line.split("=")[0] for line in env.read_text().splitlines() if line.endswith("=")]
        say.done(
            f"Created {ENV_FILE} from {ENV_EXAMPLE}" + (f"; set {', '.join(unset)} in it" if unset else "")
        )

    @staticmethod
    def env_without_placeholders(text):
        return "\n".join(Workspace.blanked_if_placeholder(line) for line in text.splitlines()) + "\n"

    @staticmethod
    def blanked_if_placeholder(line):
        body = line.strip()
        if not body or body.startswith("#") or "=" not in body:
            return line
        key, value = body.split("=", 1)
        return f"{key.rstrip()}=" if Workspace.is_placeholder(value.split(" #", 1)[0]) else line

    @staticmethod
    def is_placeholder(value):
        return not value or bool(Workspace.PLAIN_PLACEHOLDER_RE.prefixmatch(value.strip().strip("'\"")))


class Change:
    def __init__(self):
        self.backups, self.notes = {}, []

    def __bool__(self):
        return bool(self.backups)

    @property
    def files(self):
        return list(self.backups)

    @property
    def created(self):
        return [path for path, old in self.backups.items() if old is None]


class TaughtCommands:
    SHELL_FUNCTION_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    SOURCE_COMMAND_RE = re.compile(r"(^|[;&|(]\s*)source\s+")
    ALIAS_LOOKUP_TIMEOUT = 10

    @staticmethod
    def teach(name, definition=None):
        name = name.strip()
        TaughtCommands.stop_if_broken()
        if definition is not None and definition.strip().lower() == "none":
            TaughtCommands.forget(name)
            return
        entry = TaughtCommands.entry_for(name, definition)
        if entry["kind"] == "alias":
            TaughtCommands.check_alias(name, entry)
        TaughtCommands.remember(name, entry)

    @staticmethod
    def forget(name):
        if name not in TaughtCommands.all():
            raise SystemExit(f"{name} was not taught (tinker memory lists the commands taught)")
        Home.store().delete("commands", name)
        say.done(f"Forgot the command {name}")

    @staticmethod
    def entry_for(name, definition):
        if definition is None:
            return TaughtCommands.look_up(name)
        if not definition.strip():
            raise SystemExit(
                f"tinker learn {name}: the definition is empty: give what {name} stands for, or leave "
                f"it out to read it from your shell"
            )
        return {"kind": "alias", "definition": definition.strip(), "from": "given"}

    @staticmethod
    def check_alias(name, entry):
        if not TaughtCommands.SHELL_FUNCTION_NAME_RE.fullmatch(name):
            raise SystemExit(
                f"{name!r} cannot be an alias Tinker defines: letters, digits and _ only, not starting "
                f"with a digit"
            )
        entry["definition"] = TaughtCommands.SOURCE_COMMAND_RE.sub(r"\1. ", entry["definition"])
        problem = TaughtCommands.syntax_problem(name, entry["definition"])
        if problem:
            raise SystemExit(
                f"{name} stands for `{entry['definition']}`, which is not valid POSIX shell "
                f"({problem}); give a definition that is"
            )

    @staticmethod
    def remember(name, entry):
        entry = dict(entry, taught=now_text())
        Home.store().put("commands", name, entry)
        say.done(f"Learned the command {name}: {TaughtCommands.describe(entry)}")
        say.tip(
            "The toolchain uses it where it fits (each command runs in a new shell: e.g. "
            f"`{name} && ...`); SPEC.md and README show it spelled out."
        )

    @staticmethod
    def look_up(name):
        found = TaughtCommands.alias(name)
        if found:
            return found
        path = shutil.which(name)
        if path:
            return {"kind": "program", "path": path, "from": "PATH"}
        shell = os.environ.get("SHELL", "")
        raise SystemExit(
            f"{name} is neither an alias in your shell ({shell or 'SHELL is not set'}) nor a program on "
            f'PATH: give what it stands for, e.g. tinker learn {name} ". .venv/bin/activate"'
        )

    @staticmethod
    def alias(name):
        shell = os.environ.get("SHELL", "")
        if not shell or not TaughtCommands.SHELL_FUNCTION_NAME_RE.fullmatch(name):
            return None
        try:
            proc = subprocess.run(
                [shell, "-i", "-c", f"alias {name}"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=TaughtCommands.ALIAS_LOOKUP_TIMEOUT,
            )
        except OSError, subprocess.TimeoutExpired:
            return None
        definition = TaughtCommands.alias_definition(name, proc.stdout)
        return (
            None
            if definition is None
            else {"kind": "alias", "definition": definition, "from": f"your {Path(shell).name} alias"}
        )

    @staticmethod
    def alias_definition(name, printed):
        for line in printed.splitlines():
            match = re.fullmatch(rf"(?:alias\s+)?{re.escape(name)}=(.+)", line.strip())
            if match:
                return TaughtCommands.unquote(match.group(1))
        return None

    @staticmethod
    def unquote(value):
        try:
            words = shlex.split(value)
        except ValueError:
            return value
        return words[0] if len(words) == 1 and value[:1] in "'\"" else value

    @staticmethod
    def syntax_problem(name, definition):
        try:
            proc = subprocess.run(
                ["sh", "-n", "-c", TaughtCommands.function(name, definition)],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=TaughtCommands.ALIAS_LOOKUP_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"sh could not check it: {exc}"
        return (
            ""
            if proc.returncode == 0
            else first_line(proc.stderr, 160) or f"sh -n exited with {proc.returncode}"
        )

    @staticmethod
    def describe(entry):
        if entry.get("kind") == "program":
            return f"the program {entry.get('path', '?')} ({entry.get('from', 'PATH')})"
        return f"stands for `{entry.get('definition', '')}` ({entry.get('from', 'given')})"

    @staticmethod
    def all():
        commands = Home.store().items("commands")
        broken = [
            name
            for name, entry in commands.items()
            if not (isinstance(entry, dict) and entry.get("kind") in ("alias", "program"))
        ]
        if broken:
            raise SystemExit(
                f"Tinker's commands {', '.join(broken)} are neither an alias nor a program: forget them "
                f"with tinker learn NAME none, and teach them again"
            )
        return commands

    @staticmethod
    def stop_if_broken():
        TaughtCommands.all()

    @staticmethod
    def aliases():
        return {
            name: entry["definition"]
            for name, entry in TaughtCommands.all().items()
            if entry["kind"] == "alias"
        }

    @staticmethod
    def spelled_out(command):
        for name, definition in TaughtCommands.aliases().items():
            command = TaughtCommands.name_re(name).sub(lambda match, meant=definition: meant, command)
        return command

    @staticmethod
    def prelude(command):
        return "".join(
            TaughtCommands.function(name, definition)
            for name, definition in TaughtCommands.aliases().items()
            if TaughtCommands.uses(command, name)
        )

    @staticmethod
    def uses(command, name):
        return TaughtCommands.name_re(name).search(command) is not None

    @staticmethod
    def name_re(name):
        return re.compile(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])")

    @staticmethod
    def function(name, definition):
        return f"{name}() {{ {definition}\n}}\n"

    @staticmethod
    def prompt_part():
        lines = [
            f"- {name}: "
            + (
                "a program on this machine"
                if entry["kind"] == "program"
                else f"stands for `{entry['definition']}`"
            )
            for name, entry in TaughtCommands.all().items()
        ]
        return Part(
            "The person's own commands (use them where they fit; each command runs in a new shell)",
            "\n".join(lines),
            atomic=True,
        )


class TestRef(NotAPytestClass):
    def __init__(self, test):
        file, _, name = str(test).rpartition("::")
        self.file, self.name = (file, name) if file else (str(test), "")

    def __str__(self):
        return f"{self.file}::{self.name}"

    def command(self, template):
        return template.replace("{file}", self.file).replace("{name}", self.name).replace("{id}", str(self))

    def present(self, workspace):
        return workspace.exists(self.file) and (
            not self.name or self.name_at(workspace.read(self.file), self.name) >= 0
        )

    def excerpt(self, workspace, names):
        text = workspace.read(self.file)
        start = self.name_at(text, self.name)
        if start < 0:
            return text
        others = [
            position
            for position in (self.name_at(text, name) for name in names if name != self.name)
            if position >= 0
        ]
        line_start, end = self.span(text, names)
        first = text.rfind("\n", 0, min([start] + others)) + 1
        is_the_first_test = first >= line_start
        if is_the_first_test:
            return text[:end].rstrip()
        head = self.opening_lines(text[:first])
        return ("\n".join(head) + "\n...\n" if head else "") + text[line_start:end].rstrip()

    @staticmethod
    def opening_lines(before_first_test):
        head = before_first_test.splitlines()[:15]
        while head and TestRef.blank_or_decorator(head[-1]):
            head.pop()
        return head

    @staticmethod
    def blank_or_decorator(line):
        return not line.strip() or line.strip().startswith(("@", "#[", "["))

    def remove(self, workspace, names):
        text = workspace.read(self.file)
        if not self.name or self.name_at(text, self.name) < 0:
            return False
        start, end = self.span(text, names)
        workspace.write(
            self.file,
            text[:start].rstrip() + "\n\n\n" + text[end:].lstrip()
            if text[end:].strip()
            else text[:start].rstrip() + "\n",
        )
        return True

    def span(self, text, names):
        start = self.name_at(text, self.name)
        later = sorted(
            position
            for position in (self.name_at(text, name) for name in names if name != self.name)
            if position > start
        )
        end = self.definition_start(text, later[0]) if later else len(text)
        return Span(self.definition_start(text, start), end)

    @staticmethod
    def name_at(text, name):
        match = re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) if name else None
        return match.start() if match else -1

    @staticmethod
    def definition_start(text, at):
        line_start = text.rfind("\n", 0, at) + 1
        for _ in range(2):
            prev = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
            if line_start and text[prev:line_start].strip().startswith(("@", "#[", "[")):
                line_start = prev
        return line_start


class Output:
    ERROR_LINE_RE = re.compile(
        r"error|fail|assert|exception|expected|panic|undefined|cannot|not found|"
        r"no module|missing",
        re.I,
    )
    EXACT_ERROR_RE = re.compile(r"\b\w*(Error|Exception|Panic)\b|\bassert", re.I)
    NOISE_RE = re.compile(r"0x[0-9a-f]+|\d+(\.\d+)?\s*(ms|s|sec|seconds)\b|\b\d+\b", re.I)
    OWN_CODE_UNSEEN = "the test tool does not find the project's code"
    FILE_LOCATION_RE = re.compile(r"([\w./\\-]+\.\w+)[\"']?(?:, line |:|\()\d+")
    NOT_FOUND_RE = re.compile(
        r"No module named ['\"]?([\w.]+)|Cannot find module ['\"]([^'\"]+)|"
        r"cannot find package ['\"]([^'\"]+)|unresolved import `?([\w:]+)|"
        r"Could not resolve ['\"]([^'\"]+)|cannot load such file -- (\S+)",
        re.I,
    )
    UNLOADABLE_RE = re.compile(
        r"syntax\s*error|parse\s*error|indentation\s*error|unexpected (token|end|eof|indent)|"
        r"unterminated|invalid syntax|compil(e|ation) (error|failed)|(can|could)\s*not compile|"
        r"unexpected character",
        re.I,
    )

    @staticmethod
    def key_line(output, limit=200):
        lines = [
            line.strip(" #")
            for line in output.splitlines()
            if line.strip(" #") and not Output.is_banner(line.strip(" #"))
        ]
        exact = [line for line in lines if Output.EXACT_ERROR_RE.search(line)]
        if exact:
            return first_line(exact[-1], limit)
        loose = [line for line in lines if Output.ERROR_LINE_RE.search(line)]
        return first_line((loose[:1] or lines[-1:] or ["no output"])[0], limit)

    @staticmethod
    def is_banner(line):
        return sum(char in "=-_*~#+ " for char in line) >= len(line) / 2

    @staticmethod
    def failure_text(output, limit=FAILURE_OUTPUT_CHARS):
        text = "\n".join(line for line in Output.compact_lines(output.strip()).splitlines() if line.strip())
        return text if len(text) <= limit else "[...]\n" + text[-(limit - 6) :]

    @staticmethod
    def compact_lines(text, limit=360):
        return "\n".join(
            line
            if len(line) <= limit
            else f"{line[: limit * 2 // 3]} ...[{len(line) - limit} chars]... {line[-limit // 3 :]}"
            for line in text.splitlines()
        )

    @staticmethod
    def signature(output):
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        errors = [line for line in lines if Output.ERROR_LINE_RE.search(line)]
        key = "\n".join((errors or lines)[-4:])
        return hashlib.sha1(Output.NOISE_RE.sub("#", key).encode()).hexdigest()[:12]

    @staticmethod
    def tool_failure(output, workdir):
        if Output.OWN_CODE_UNSEEN in output:
            return Output.OWN_CODE_UNSEEN
        return Output.NOISE_RE.sub("#", Output.key_line(output.replace(str(workdir), ".")))

    @staticmethod
    def misses_own_code(output, paths):
        stems = {Path(path).stem.lower() for path in paths} | {
            Path(path).parent.name.lower() for path in paths
        }
        stems.discard("")
        for match in Output.NOT_FOUND_RE.finditer(output):
            name = next(group for group in match.groups() if group).strip("./\\")
            if Output.names_a_module_of(name, stems):
                return name
        return ""

    @staticmethod
    def names_a_module_of(name, stems):
        pieces = [piece.lower() for piece in re.split(r"[./\\:]+", name) if piece]
        return bool(pieces) and (pieces[0] in stems or Path(name).stem.lower() in stems)

    @staticmethod
    def unloadable(output, path):
        lines = output.splitlines()
        return bool(path) and any(
            Output.UNLOADABLE_RE.search(line)
            and (Output.names(line, path) or Output.names(Output.nearest_location(lines, index), path))
            for index, line in enumerate(lines)
        )

    @staticmethod
    def nearest_location(lines, index, reach=6):
        around = range(max(0, index - reach), min(len(lines), index + reach + 1))
        for candidate in sorted(around, key=lambda candidate: abs(candidate - index)):
            match = Output.FILE_LOCATION_RE.search(lines[candidate])
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def names(text, path):
        return re.search(rf"(?<![\w.-]){re.escape(path)}(?![\w-])", text.replace("\\", "/")) is not None

    @staticmethod
    def mentioned_in(output, paths):
        counts = [(-count, path) for path in paths if (count := Output.mentions(output, path))]
        return [path for _, path in sorted(counts)]

    @staticmethod
    def mentions(output, path):
        return len(re.findall(rf"(?<![\w.-]){re.escape(path)}(?![\w-])", output)) or len(
            re.findall(rf"(?<![\w./-]){re.escape(Path(path).name)}(?![\w-])", output)
        )


class FileView:
    def __init__(self, workspace, fixed, candidates, tail=(), output="", hints="", windows=2):
        self.workspace, self.fixed, self.tail = workspace, list(fixed), list(tail)
        self.candidates = list(dict.fromkeys(candidates))
        self.output, self.hints, self.max_windows = output, hints, windows
        self.whole, self.windows = set(), {}

    def __call__(self, limit):
        self.whole, self.windows = set(), {}
        files = self.file_parts(
            room=limit - sum(part.floor_size() for part in self.fixed if part.required) - 300
        )
        how = [Part("Changing a file shown in part", Prompts.LINES_HOWTO, True)] if self.windows else []
        return self.fixed + files + how + self.tail

    def file_parts(self, room):
        files = []
        for path in self.candidates:
            if room < 250 or not self.workspace.exists(path):
                continue
            part = self.whole_file(path, room) or self.window(path, room)
            if part:
                files.append(part)
                room -= part.size()
        return files

    @property
    def shown(self):
        return self.whole | set(self.windows)

    def whole_file(self, path, room):
        part = Part(f"File {path}", self.workspace.read(path), True, exact=True)
        if part.size() <= room:
            self.whole.add(path)
            return part
        return None

    def window(self, path, room):
        if len(self.windows) >= self.max_windows or room < 700:
            return None
        lines = self.workspace.read(path).splitlines()
        first, last, body = self.window_view(
            lines, self.focus_line(path, lines, self.output, self.hints), room - 140
        )
        self.windows[path] = (first, last)
        return Part(
            f"File {path}, lines {first}-{last} of {len(lines)} (numbered; the rest is not shown)",
            body,
            True,
            exact=True,
        )

    @staticmethod
    def focus_line(path, lines, output, hints):
        return (
            FileView.line_pointed_at(path, len(lines), output) or FileView.first_line_using(lines, hints) or 1
        )

    @staticmethod
    def line_pointed_at(path, count, output):
        for name in (re.escape(path), re.escape(Path(path).name)):
            for match in re.finditer(rf"{name}[\"']?(?:, line |:|\()(\d+)", output):
                if 1 <= int(match.group(1)) <= count:
                    return int(match.group(1))
        return None

    @staticmethod
    def first_line_using(lines, hints):
        for word in sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", hints)), key=len, reverse=True)[:40]:
            for index, line in enumerate(lines, 1):
                if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", line):
                    return index
        return None

    @staticmethod
    def window_view(lines, center, room):
        start = end = max(1, min(center, len(lines)))
        size = lambda first, last: sum(
            len(lines[line_number - 1]) + 7 for line_number in range(first, last + 1)
        )
        grown = True
        while grown:
            grown = False
            if start > 1 and size(start - 1, end) <= room:
                start, grown = start - 1, True
            if end < len(lines) and size(start, end + 1) <= room:
                end, grown = end + 1, True
        return (
            start,
            end,
            "\n".join(f"{line_number:>4}| {lines[line_number - 1]}" for line_number in range(start, end + 1)),
        )


class ToolError(Exception):
    pass
