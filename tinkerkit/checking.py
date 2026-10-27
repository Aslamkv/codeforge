import shlex
import sqlite3
import time
from pathlib import Path

from .sandbox import TIMEOUT_SECONDS, Ran, Sandbox, cut

DEFAULT_FILE = "check"
PROGRESS_STEPS = 1000
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


def found_programs(machines):
    return {first_word(tool["command"]) for machine in machines for tool in machine if tool.get("found")}


def first_word(command):
    words = command.split()
    return words[0] if words else ""


class Check:
    def __init__(self, proposal, tools, sandbox):
        self.code = proposal.get("code", "")
        self.shows = proposal.get("shows", "")
        self.proposal = proposal
        self.tools = tools
        self.sandbox = sandbox

    @classmethod
    def of(cls, proposal, tools, sandbox):
        kind = {"sql": SqlCheck, "command": CommandCheck}.get(proposal.get("kind"))
        if kind is None:
            return RefusedCheck(proposal, f"a check of kind {proposal.get('kind')!r} cannot run")
        if not str(proposal.get("code", "")).strip():
            return RefusedCheck(proposal, "a check without code cannot run")
        return kind(proposal, tools, sandbox)

    def refusal(self):
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
                lines.append(f"Error: {error}")
                return Ran(True, code=1, output=cut("\n".join(lines)), timed_out=time.monotonic() >= deadline)
        return Ran(True, code=0, output=cut("\n".join(lines)))


def confine(connection, deadline):
    if hasattr(connection, "enable_load_extension"):
        connection.enable_load_extension(False)
    connection.set_authorizer(authorized)
    connection.set_progress_handler(lambda: time.monotonic() >= deadline, PROGRESS_STEPS)


def authorized(action, *_):
    return sqlite3.SQLITE_DENY if action in REFUSED_ACTIONS else sqlite3.SQLITE_OK


def statements(code):
    pending = ""
    for line in code.splitlines(keepends=True):
        pending += line
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
            return self.sandbox.run([*shlex.split(self.tool), name], folder, TIMEOUT_SECONDS)

    @property
    def tool(self):
        return str(self.proposal.get("tool", ""))

    def refusal(self):
        try:
            words = shlex.split(self.tool)
        except ValueError:
            return f"the tool {self.tool!r} cannot be read"
        program = words[0] if words else ""
        if program in INSTALLERS or INSTALL_WORDS & set(words):
            return f"{self.tool!r} installs software, which a check may not do"
        if program not in self.tools:
            return f"{program or 'no tool'} is not found on this machine"
        return ""
