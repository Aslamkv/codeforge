import re
import shlex
import sqlite3
import time
from pathlib import Path

from .sandbox import TIMEOUT_SECONDS, Ran, Sandbox, cut

DEFAULT_FILE = "check"
PROGRESS_STEPS = 1000
SHORT_OUTPUT_CHARS = 500
REFUSED_ACTIONS = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
INSTALLERS = {
    "pip",
    "pip3",
    "npm",
    "npx",
    "yarn",
    "pnpm",
    "brew",
    "apt",
    "apt-get",
    "dnf",
    "yum",
    "pacman",
    "gem",
    "conda",
    "poetry",
    "uv",
}
INSTALL_WORDS = {"install", "add", "get"}


def installs(words):
    return bool(INSTALLERS & set(words)) or bool(INSTALL_WORDS & set(words[1:2]))


def found_programs(machines):
    return {first_word(tool["command"]) for machine in machines for tool in machine if tool.get("found")}


def first_word(command):
    words = command.split()
    return words[0] if words else ""


class Check:
    def __init__(self, proposal, tools, sandbox_here):
        self.code = proposal.get("code", "")
        self.shows = proposal.get("shows", "")
        self.proposal = proposal
        self.tools = tools
        self.sandbox_here = sandbox_here

    @classmethod
    def of(cls, proposal, tools, sandbox_here):
        kind = {"sql": SqlCheck, "command": CommandCheck}.get(proposal.get("kind"))
        if kind is None:
            return RefusedCheck(proposal, f"a check of kind {proposal.get('kind')!r} cannot run")
        if not str(proposal.get("code", "")).strip():
            return RefusedCheck(proposal, "a check without code cannot run")
        return kind(proposal, tools, sandbox_here)

    def refusal(self) -> str:
        return ""


class RefusedCheck(Check):
    def __init__(self, proposal, why):
        super().__init__(proposal, set(), None)
        self.why = why

    def run(self):
        return Ran(False, why=self.why)

    def refusal(self):
        return self.why


class SqlCheck(Check):
    def run(self):
        connection = sqlite3.connect(":memory:")
        deadline = time.monotonic() + TIMEOUT_SECONDS
        try:
            confine(connection, deadline)
            return self.statements_run(connection, deadline)
        finally:
            connection.close()

    def statements_run(self, connection, deadline):
        lines = []
        for statement in statements(self.code):
            try:
                lines.extend(rows_of(connection.execute(statement)))
            except sqlite3.Error as error:
                timed_out = time.monotonic() >= deadline
                why = "it ran out of time" if timed_out else statement_failure(error, lines)
                lines.append(f"Error: {error}")
                return Ran(True, code=1, output=cut("\n".join(lines)), timed_out=timed_out, why=why)
        return Ran(True, code=0, output=cut("\n".join(lines)))


def statement_failure(error, lines):
    printed = "\n".join(lines)
    before = f"\nIt printed before that:\n{printed}" if printed and len(printed) <= SHORT_OUTPUT_CHARS else ""
    return f"a statement errored: {error}{before}"


def confine(connection, deadline):
    if hasattr(connection, "enable_load_extension"):
        connection.enable_load_extension(False)
    connection.set_authorizer(authorized)
    connection.set_progress_handler(lambda: time.monotonic() >= deadline, PROGRESS_STEPS)


def authorized(action, *_):
    return sqlite3.SQLITE_DENY if action in REFUSED_ACTIONS else sqlite3.SQLITE_OK


def statements(code):
    pending = ""
    for piece in re.split(r"(?<=;)", code):
        pending += piece
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        yield pending


def rows_of(cursor):
    return ["|".join("" if value is None else str(value) for value in row) for row in cursor.fetchall()]


class CommandCheck(Check):
    def run(self):
        why = self.refusal()
        if why:
            return Ran(False, why=why)
        name = Path(self.proposal.get("file") or DEFAULT_FILE).name or DEFAULT_FILE
        with Sandbox.folder() as folder:
            (folder / name).write_text(self.code)
            return self.sandbox_here().run([*shlex.split(self.tool), name], folder, TIMEOUT_SECONDS)

    @property
    def tool(self):
        return str(self.proposal.get("tool", ""))

    def refusal(self):
        try:
            words = shlex.split(self.tool)
        except ValueError:
            return f"the tool {self.tool!r} cannot be read"
        program = words[0] if words else ""
        if installs(words):
            return f"{self.tool!r} installs software, which a check may not do"
        if program not in self.tools:
            return f"{program or 'no tool'} is not found on this machine"
        return ""
