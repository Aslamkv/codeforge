import pytest

import tinkerkit.checking as checking
from tinkerkit.checking import Check, CommandCheck, RefusedCheck, SqlCheck, found_programs
from tinkerkit.sandbox import NoSandbox, Ran, Sandbox

pytestmark = pytest.mark.spec("TCH-10")


class FakeSandbox:
    def __init__(self):
        self.runs = []

    def run(self, command, folder, timeout=None):
        written = {path.name: path.read_text() for path in folder.iterdir()}
        self.runs.append((command, folder, written))
        return Ran(True, code=0, output="ran")


def no_sandbox():
    raise AssertionError("only a command check asks for a sandbox")


def sql(code):
    return Check.of({"kind": "sql", "code": code, "shows": "it works"}, set(), no_sandbox).run()


def command(tool, code="print(1)", tools=("python3", "cargo", "go", "pip", "npm"), sandbox=None, **more):
    proposal = {"kind": "command", "code": code, "shows": "it works", "tool": tool, **more}
    sandbox = sandbox or FakeSandbox()
    return Check.of(proposal, set(tools), lambda: sandbox).run(), sandbox


def test_a_check_is_chosen_by_its_kind_and_keeps_its_proposal():
    check = Check.of({"kind": "sql", "code": "select 1", "shows": "one"}, set(), no_sandbox)
    assert isinstance(check, SqlCheck)
    assert (check.code, check.shows) == ("select 1", "one")
    proposal = {"kind": "command", "code": "x", "shows": "y", "tool": "python3"}
    assert isinstance(Check.of(proposal, {"python3"}, no_sandbox), CommandCheck)


def test_an_unknown_kind_or_missing_code_is_refused():
    for proposal in ({"kind": "shell", "code": "ls"}, {"kind": "sql"}, {"kind": "sql", "code": " "}, {}):
        check = Check.of(proposal, set(), no_sandbox)
        assert isinstance(check, RefusedCheck)
        result = check.run()
        assert not result.ran and result.why


def test_sql_prints_a_value():
    result = sql("select 1+1")
    assert result.ran and result.code == 0
    assert result.output.strip() == "2"


def test_sql_runs_a_script_statement_by_statement():
    result = sql(
        "create table t (a, b);\ninsert into t values (1, 'x');\ninsert into t values (2, 'y');\nselect * from t;"
    )
    assert result.code == 0
    assert result.output.splitlines() == ["1|x", "2|y"]


def test_sql_stops_at_an_error_keeping_what_printed():
    result = sql("select 1;\nselect * from missing;\nselect 2;")
    assert result.ran and result.code == 1
    assert result.output.startswith("1\n")
    assert "missing" in result.output and "2" not in result.output.splitlines()


def test_sql_refuses_attaching_a_database():
    result = sql("attach database 'other.db' as other;")
    assert result.code == 1
    assert "not authorized" in result.output


def test_sql_cannot_load_an_extension():
    result = sql("select load_extension('x');")
    assert result.code == 1


def test_sql_stops_an_endless_query_at_the_deadline(monkeypatch):
    monkeypatch.setattr(checking, "TIMEOUT_SECONDS", 0.2)
    result = sql("with recursive n(i) as (select 1 union all select i + 1 from n) select count(*) from n;")
    assert result.code == 1
    assert result.timed_out


def test_a_command_runs_its_file_with_the_tool_in_an_empty_folder():
    result, sandbox = command("python3 -B", code="print(42)", file="sub/show.py")
    assert result.output == "ran"
    [(argv, folder, written)] = sandbox.runs
    assert argv == ["python3", "-B", "show.py"]
    assert written == {"show.py": "print(42)"}
    assert not folder.exists()


def test_a_command_file_defaults_to_check():
    _, sandbox = command("python3")
    [(argv, _, written)] = sandbox.runs
    assert argv == ["python3", "check"] and list(written) == ["check"]


@pytest.mark.parametrize(
    "tool",
    ["ruby", "pip install x", "npm", "cargo install ripgrep", "go get x", "brew", "python3 -m pip install x"],
)
def test_an_unfound_tool_or_an_installer_is_refused_without_running(tool):
    result, sandbox = command(tool, tools=("python3", "cargo", "go", "pip", "npm", "brew"))
    assert not result.ran and result.why
    assert sandbox.runs == []


@pytest.mark.parametrize("tool", ["sqlite3 -cmd get", "python3 -c add"])
def test_an_install_word_after_the_first_argument_is_not_installing(tool):
    result, sandbox = command(tool, tools=("python3", "sqlite3"))
    assert "installs" not in result.why
    assert len(sandbox.runs) == 1


def test_without_a_sandbox_a_command_does_not_run():
    result, _ = command("python3", sandbox=NoSandbox("no sandbox on plan9"))
    assert not result.ran and result.why == "no sandbox on plan9"


def test_found_programs_are_the_first_words_of_found_tools():
    machines = [
        [{"command": "python3 --version", "found": True, "output": "Python 3.15.0"}],
        [
            {"command": "cargo --version", "found": False, "output": ""},
            {"command": "sqlite3 -version", "found": True, "output": "3.50"},
        ],
    ]
    assert found_programs(machines) == {"python3", "sqlite3"}


def test_a_real_command_runs_in_the_sandbox():
    here = Sandbox.here()
    if isinstance(here, NoSandbox):
        pytest.skip(here.why)
    result, _ = command("python3", code="print(6 * 7)", sandbox=here, file="six.py")
    assert result.code == 0, result.output
    assert result.output.strip() == "42"
