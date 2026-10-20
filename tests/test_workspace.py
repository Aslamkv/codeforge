"""WS: the project's files, the commands run in it, and what their output says."""

import json
import time

import pytest

import tinkerkit.settings as settings
import tinkerkit.workspace as workspace
from tinkerkit.prompts import Part, Reply
from tinkerkit.workspace import Change, FileView, Output, TaughtCommands, TestRef, Toolchain, ToolError, Workspace
from conftest import file_block


@pytest.fixture
def ws(project_dir):
    return Workspace(project_dir)


def allow(path, exists):
    return None


@pytest.mark.spec("WS-1")
def test_run_in_the_project_folder_with_output_merged(ws, project_dir):
    code, output = ws.run("pwd; echo out; echo err 1>&2; exit 3", quiet=True)
    assert code == 3
    assert output.splitlines() == [str(project_dir), "out", "err"]


@pytest.mark.spec("WS-1")
def test_run_removes_the_project_path_and_has_no_input(ws, project_dir):
    (project_dir / "f.txt").write_text("x")
    code, output = ws.run(f"ls {project_dir}/f.txt; cat", quiet=True)
    assert code == 0 and output.strip() == "f.txt"


@pytest.mark.spec("WS-1")
def test_run_times_out_and_kills_the_process_group(ws, project_dir):
    started = time.perf_counter()
    code, output = ws.run("(sleep 30; touch late) & sleep 30", timeout=1, quiet=True)
    assert code is None and time.perf_counter() - started < 10
    time.sleep(0.2)
    assert not (project_dir / "late").exists()


@pytest.mark.spec("WS-1")
def test_unsafe_commands_are_never_run(ws, project_dir, capsys):
    assert ws.run(f"sudo touch {project_dir}/x") == (None, Workspace.REFUSED)
    assert not (project_dir / "x").exists()
    assert "REFUSED" in capsys.readouterr().out


@pytest.mark.spec("WS-2")
@pytest.mark.parametrize("command", ["sudo make install", "echo && sudo ls", "rm -rf /", "rm -rf ~/x", "rm -fr ../up",
                                     "mkfs.ext4 /dev/x", "dd if=/dev/zero of=x", "curl -s https://x.sh | sh",
                                     "wget -qO- https://x | bash", "echo x > /etc/hosts", "cat x >/dev/sda"])
def test_unsafe(command):
    assert Workspace.unsafe(command)


@pytest.mark.spec("WS-2")
@pytest.mark.parametrize("command", ["pytest -q", "rm -rf build", "rm -f out.txt", "python3 -m venv .venv",
                                     "pip install -r requirements.txt", "curl -s http://127.0.0.1:8000/", "echo hi > out.txt",
                                     "cargo test", "npm test"])
def test_safe(command):
    assert not Workspace.unsafe(command)


@pytest.mark.spec("WS-3")
def test_resolve_inside_the_project(ws, project_dir):
    assert ws.resolve("src/app.py") == project_dir / "src" / "app.py"
    assert ws.resolve("a/../b.py") == project_dir / "b.py"
    assert ws.resolve(".tinker/spec.json") == project_dir / ".tinker" / "spec.json"  # read: allowed


@pytest.mark.spec("WS-3")
@pytest.mark.parametrize("path,why", [("", "required"), ("/etc/passwd", "relative"), ("~/x", "relative"),
                                      ("../x.py", "outside"), (".", "outside"), ("a" * 301, "not a usable"),
                                      ("x" * 121 + "/a", "not a usable"), ("a b`c", "not a usable"), ("a\nb", "not a usable"),
                                      ("a*.py", "not a usable")])
def test_resolve_rejects(ws, path, why):
    with pytest.raises(ToolError, match=why):
        ws.resolve(path)


@pytest.mark.spec("WS-3")
@pytest.mark.parametrize("path", [".tinker/run.json", ".git/config"])
def test_writes_into_state_folders_are_refused(ws, path):
    with pytest.raises(ToolError, match="protected"):
        ws.resolve(path, for_write=True)


@pytest.mark.spec("WS-4")
def test_write_and_read(ws, project_dir):
    ws.write("deep/er/a.txt", "no newline")
    assert (project_dir / "deep" / "er" / "a.txt").read_text() == "no newline\n"
    ws.write("empty.py", "")
    assert (project_dir / "empty.py").read_text() == ""
    assert ws.read("missing.txt") == "" and ws.exists("empty.py") and not ws.exists("deep")
    assert ws.lines("deep/er/a.txt") == 1


@pytest.mark.spec("WS-5")
def test_apply_writes_allowed_blocks_and_reverts(ws, project_dir):
    (project_dir / "old.py").write_text("old\n")
    reply = Reply(file_block("old.py", "new") + file_block("made.py", "x = 1"))
    change = ws.apply(reply, allow)
    assert change and change.backups == {"old.py": "old\n", "made.py": None}
    assert change.created == ["made.py"] and sorted(change.files) == ["made.py", "old.py"]
    assert ws.read("old.py") == "new\n"
    ws.revert(change)
    assert ws.read("old.py") == "old\n" and not ws.exists("made.py")


@pytest.mark.spec("WS-5")
def test_apply_refuses_with_notes(ws, project_dir):
    (project_dir / "full.py").write_text("content\n")
    reply = Reply(file_block("path", "x = 1") + file_block("../out.py", "x") + file_block("shown.py", "x")
                  + file_block("full.py", "") + file_block("denied.py", "x") + file_block("new_empty.py", ""))
    change = ws.apply(reply, lambda p, exists: "not now" if p == "denied.py" else None, windows={"shown.py": (1, 5)})
    notes = "\n".join(change.notes)
    assert "path: not a real file path" in notes
    assert "../out.py: path '../out.py' is outside the project" in notes
    assert "shown.py: it was shown in part" in notes
    assert "full.py: the reply gave it empty" in notes
    assert "denied.py: not now" in notes
    assert change.files == ["new_empty.py"]  # a new file may be empty (a package marker)


@pytest.mark.spec("WS-5")
def test_apply_refuses_a_new_empty_file_when_empty_is_not_ok(ws):
    change = ws.apply(Reply(file_block("tests/test_x.py", "")), allow, empty_ok=False)
    assert not change and "the reply gave it empty" in change.notes[0]


@pytest.mark.spec("WS-6")
def test_line_edits_only_inside_the_window_shown(ws, project_dir):
    (project_dir / "big.py").write_text("".join(f"line{i}\n" for i in range(1, 11)))
    reply = Reply("<<<LINES big.py 3-4>>>\nTHREE\nFOUR\n<<<END>>>\n<<<LINES big.py 9-9>>>\nx\n<<<END>>>\n"
                  "<<<LINES other.py 1-1>>>\nx\n<<<END>>>")
    change = ws.apply(reply, allow, windows={"big.py": (2, 6)})
    lines = ws.read("big.py").splitlines()
    assert lines[2:4] == ["THREE", "FOUR"] and lines[8] == "line9"
    assert any("outside the lines shown" in n for n in change.notes)
    assert any("only change files shown" in n for n in change.notes)
    ws.revert(change)
    assert ws.read("big.py").splitlines()[2] == "line3"


@pytest.mark.spec("WS-6")
def test_overlapping_line_edits_keep_one(ws, project_dir):
    (project_dir / "a.py").write_text("1\n2\n3\n4\n")
    reply = Reply("<<<LINES a.py 1-2>>>\nA\n<<<END>>>\n<<<LINES a.py 2-3>>>\nB\n<<<END>>>")
    change = ws.apply(reply, allow, windows={"a.py": (1, 4)})
    assert any("overlapping" in n for n in change.notes)
    assert ws.read("a.py") in ("A\n3\n4\n", "1\nB\n4\n")


@pytest.mark.spec("WS-7")
def test_prepare_env_from_the_example(ws, project_dir, capsys):
    (project_dir / ".env.example").write_text("# settings\nAPI_KEY=your_api_key_here\nPORT=8000\nNAME=<name>\n")
    ws.prepare_env()
    assert ws.read(".env") == "# settings\nAPI_KEY=\nPORT=8000\nNAME=\n"
    assert "set API_KEY, NAME in it" in capsys.readouterr().out


@pytest.mark.spec("WS-7")
def test_prepare_env_clears_placeholders_in_an_existing_env(ws, project_dir):
    (project_dir / ".env").write_text("TOKEN=changeme\nREAL=abc123\n")
    ws.prepare_env()
    assert ws.read(".env") == "TOKEN=\nREAL=abc123\n"


@pytest.mark.spec("WS-7")
@pytest.mark.parametrize("value", ["", "<key>", "your_token", "YOUR-KEY", "key_here", "changeme", "replace-me", "xxx",
                                   "***", "...", "TODO", "'placeholder'"])
def test_placeholders(value):
    assert Workspace.is_placeholder(value)


@pytest.mark.spec("WS-7")
@pytest.mark.parametrize("value", ["8000", "sk-1234", "localhost", "https://api.example.com", "/var/data", "true"])
def test_real_values(value):
    assert not Workspace.is_placeholder(value)


@pytest.mark.spec("WS-8")
def test_start_and_stop_a_background_command(ws, project_dir):
    started = ws.start("echo serving; (sleep 30; touch late) & sleep 30")
    assert started is not None
    process, output = started
    assert output == project_dir / ".tinker" / "app-output.txt"
    time.sleep(0.3)
    ws.stop(process)
    assert process.poll() is not None and "serving" in output.read_text()
    time.sleep(0.2)
    assert not (project_dir / "late").exists()
    assert ws.start("sudo serve") is None


@pytest.fixture
def no_shell_alias(monkeypatch):
    monkeypatch.setattr(TaughtCommands, "alias", staticmethod(lambda name: None))


@pytest.mark.spec("WS-9")
def test_teach_an_alias(tinker_home):
    TaughtCommands.teach("activate", "source .venv/bin/activate")
    entry = json.loads(settings.COMMANDS_FILE.read_text())["commands"]["activate"]
    assert entry["kind"] == "alias" and entry["definition"] == ". .venv/bin/activate" and entry["from"] == "given"


@pytest.mark.spec("WS-9")
def test_teach_rejects_bad_shell_names_and_empty(tinker_home):
    with pytest.raises(SystemExit, match="not valid POSIX shell"):
        TaughtCommands.teach("broken", "if then fi (")
    with pytest.raises(SystemExit, match="cannot be an alias"):
        TaughtCommands.teach("my-cmd", "ls")
    with pytest.raises(SystemExit, match="definition is empty"):
        TaughtCommands.teach("x", "  ")
    assert not settings.COMMANDS_FILE.exists()


@pytest.mark.spec("WS-9")
def test_forget_with_none(tinker_home):
    TaughtCommands.teach("hi", "echo hi")
    TaughtCommands.teach("hi", "none")
    assert TaughtCommands.all() == {}
    with pytest.raises(SystemExit, match="was not taught"):
        TaughtCommands.teach("hi", "none")


@pytest.mark.spec("WS-9")
def test_teach_a_program_on_path(tinker_home, no_shell_alias):
    TaughtCommands.teach("sh")
    entry = TaughtCommands.all()["sh"]
    assert entry["kind"] == "program" and entry["path"].endswith("/sh")
    with pytest.raises(SystemExit, match="neither an alias"):
        TaughtCommands.teach("no-such-program-xyz")


@pytest.mark.spec("WS-10")
def test_all_rejects_a_broken_commands_file(tinker_home):
    settings.JsonFile.write(settings.COMMANDS_FILE, {"commands": {"x": {"kind": "magic"}}})
    with pytest.raises(SystemExit, match="must map each name"):
        TaughtCommands.all()


@pytest.mark.spec("WS-10")
def test_spelled_out_prelude_and_uses(tinker_home):
    TaughtCommands.teach("act", ". .venv/bin/activate")
    assert TaughtCommands.spelled_out("act && pytest") == ". .venv/bin/activate && pytest"
    assert TaughtCommands.spelled_out("react && ./act.sh && x/act") == "react && ./act.sh && x/act"
    assert TaughtCommands.prelude("act && pytest") == "act() { . .venv/bin/activate\n}\n"
    assert TaughtCommands.prelude("pytest") == ""
    assert TaughtCommands.uses("x; act", "act") and not TaughtCommands.uses("action", "act")


@pytest.mark.spec("WS-10")
def test_a_taught_alias_works_in_project_commands(tinker_home, ws):
    TaughtCommands.teach("greet", "echo hello from alias")
    assert ws.run("greet", quiet=True) == (0, "hello from alias\n")


@pytest.mark.spec("WS-10")
def test_describe_unquote_and_the_prompt_part(tinker_home):
    assert TaughtCommands.unquote("'source x'") == "source x"
    assert TaughtCommands.unquote("ls -la") == "ls -la"
    assert "the program /bin/x" in TaughtCommands.describe({"kind": "program", "path": "/bin/x"})
    TaughtCommands.teach("act", "echo a")
    part = TaughtCommands.prompt_part()
    assert part.atomic and "- act: stands for `echo a`" in part.text


TOOLS = {"setup": ["python3 -m venv .venv", "cp .env.example .env", "brew install x", "pip install --user y",
                   "npm install -g z", "pip install -r requirements.txt"],
         "activate": ". .venv/bin/activate", "use": "make", "run": "python3 app.py", "check": "python3 -c 'import app'",
         "test_all": "pytest -q", "test_one": "pytest -q {file}::{name}", "format": "black .", "lint": "ruff check ."}


@pytest.mark.spec("WS-11")
def test_toolchain_reads_and_filters_setup():
    tc = Toolchain(TOOLS)
    assert tc.setup == ["python3 -m venv .venv", "pip install -r requirements.txt"]
    assert tc.dropped == ["cp .env.example .env"]
    assert tc.machine == ["brew install x", "pip install --user y", "npm install -g z"]
    assert tc.use == ["make"] and tc.run == "python3 app.py" and tc


@pytest.mark.spec("WS-11")
def test_toolchain_lint_none_and_truth():
    tc = Toolchain(dict(TOOLS, lint="none"))
    assert tc.lint == "" and tc.lint_named and tc.to_dict()["lint"] == "none"
    assert not Toolchain({"test_all": "pytest"}) and not Toolchain(None) and not Toolchain("junk")


@pytest.mark.spec("WS-12")
def test_shell_merged_and_to_dict():
    tc = Toolchain(TOOLS)
    assert tc.shell("pytest") == ". .venv/bin/activate && pytest"
    assert Toolchain(dict(TOOLS, activate="")).shell("pytest") == "pytest"
    merged = tc.merged({"test_all": "", "lint": "flake8", "setup": [], "unknown": "x"})
    assert merged.test_all == "pytest -q" and merged.lint == "flake8" and merged.setup == []
    assert Toolchain(tc.to_dict()).to_dict() == tc.to_dict()


@pytest.mark.spec("WS-12")
def test_problems():
    assert Toolchain(TOOLS).problems() == []
    problems = Toolchain({"test_one": "pytest -q"}).problems()
    assert "toolchain.test_all is missing" in problems
    assert any("lint is missing" in p for p in problems)
    assert any("must contain {name}" in p for p in problems)


@pytest.mark.spec("WS-13")
def test_run_setup_runs_in_order(ws, project_dir):
    ok, output, waiting = Toolchain(dict(TOOLS, activate="", setup=["echo one > a", "cat a > b"])).run_setup(ws)
    assert (ok, waiting) == (True, []) and ws.read("b") == "one\n"


@pytest.mark.spec("WS-13")
def test_run_setup_stops_at_a_failure(ws, monkeypatch):
    monkeypatch.setattr(workspace, "online", lambda: True)
    ok, output, waiting = Toolchain(dict(TOOLS, activate="", setup=["echo boom; exit 4", "touch never"])).run_setup(ws)
    assert not ok and output == "$ echo boom; exit 4  failed (exit code 4)\nboom" and not ws.exists("never")
    ok, output, _ = Toolchain(dict(TOOLS, activate="", setup=["true; exit 2"])).run_setup(ws)
    assert output.endswith("(it printed nothing)")


@pytest.mark.spec("WS-13")
def test_run_setup_sets_network_failures_aside(ws, monkeypatch):
    monkeypatch.setattr(workspace, "online", lambda: True)
    tc = Toolchain(dict(TOOLS, activate="", setup=["echo 'Could not resolve host: pypi.org'; exit 1", "touch after"]))
    ok, output, waiting = tc.run_setup(ws)
    assert ok and waiting == [tc.setup[0]] and ws.exists("after")
    monkeypatch.setattr(workspace, "online", lambda: False)
    ok, _, waiting = Toolchain(dict(TOOLS, activate="", setup=["exit 1"])).run_setup(ws)
    assert ok and waiting == ["exit 1"]  # offline: whatever failed waits for the network


@pytest.mark.spec("WS-13")
def test_run_setup_refuses_a_command_that_starts_a_new_project(ws, project_dir):
    (project_dir / "Cargo.toml").write_text("[package]\n")
    tc = Toolchain(dict(TOOLS, activate="", setup=["mkdir app && touch app/Cargo.toml"]))
    ok, output, _ = tc.run_setup(ws)
    assert not ok and "made a new project in app/" in output


@pytest.mark.spec("WS-13")
def test_run_setup_uses_the_environment_once_activate_works(ws):
    tc = Toolchain(dict(TOOLS, activate=". ./env.sh", setup=["echo 'export MARK=on' > env.sh", "echo $MARK > seen"]))
    ok, _, _ = tc.run_setup(ws)
    assert ok and ws.read("seen") == "on\n"


@pytest.fixture
def tc_py():
    from conftest import TOOLCHAIN
    return Toolchain(TOOLCHAIN)


@pytest.mark.spec("WS-14")
def test_run_test_passes_and_fails(ws, project_dir, tc_py):
    (project_dir / "test_a.py").write_text("def test_ok():\n    assert True\n\ndef test_bad():\n    assert 1 == 2\n")
    assert tc_py.run_test(ws, TestRef("test_a.py::test_ok"))[0] == "passes"
    state, output = tc_py.run_test(ws, TestRef("test_a.py::test_bad"))
    assert state == "fails" and "assert 1 == 2" in output and output.startswith("$ ")


@pytest.mark.spec("WS-14")
def test_run_test_that_ran_nothing_fails(ws):
    tc = Toolchain(dict(TOOLS, activate="", test_one="echo 'no tests ran'"))
    state, output = tc.run_test(ws, TestRef("t.py::test_x"))
    assert state == "fails" and "ran no test: is the test named test_x?" in output


@pytest.mark.spec("WS-14")
def test_a_test_that_hangs_fails(ws):
    settings.Config.load()["test_timeout"] = 1
    tc = Toolchain(dict(TOOLS, activate="", test_one="sleep 20", test_all="sleep 20"))
    state, output = tc.run_test(ws, TestRef("t.py::test_x"))
    assert state == "fails" and "did not finish within 1s" in output
    code, output = tc.run_all(ws)
    assert code is None and "did not finish within 2s" in output


@pytest.mark.spec("WS-15")
@pytest.mark.parametrize("output,none", [("no tests ran in 0.01s", True), ("collected 0 items", False),
                                         ("Ran 0 tests", True), ("running 0 tests\ntest result: ok. 0 passed", True),
                                         ("0 passed; 3 passed", False), ("5 passed in 0.1s", False),
                                         ("no tests ran\n2 passed", False)])
def test_ran_no_test(output, none):
    assert Toolchain.ran_no_test(output) is none


@pytest.mark.spec("WS-15")
def test_reached_no_test():
    refs = [TestRef("tests/test_a.py::test_x")]
    assert not Toolchain.reached_no_test(0, "", refs)
    assert not Toolchain.reached_no_test(None, "...", refs)  # a timeout: a test hangs
    assert Toolchain.reached_no_test(None, Workspace.REFUSED, refs)
    assert Toolchain.reached_no_test(127, "pytest: command not found tests/test_a.py", refs)
    assert Toolchain.reached_no_test(1, "ModuleNotFoundError: no module named pytest", refs)
    assert not Toolchain.reached_no_test(1, "FAILED tests/test_a.py::test_x", refs)


@pytest.mark.spec("WS-16")
def test_test_ref():
    ref = TestRef("tests/test_a.py::test_add")
    assert (ref.file, ref.name, str(ref)) == ("tests/test_a.py", "test_add", "tests/test_a.py::test_add")
    assert ref.command("pytest {file}::{name} # {id}") == "pytest tests/test_a.py::test_add # tests/test_a.py::test_add"
    bare = TestRef("tests/test_a.py")
    assert (bare.file, bare.name) == ("tests/test_a.py", "")
    assert TestRef("mod::sub::test_x").file == "mod::sub"


@pytest.mark.spec("WS-16")
def test_present_and_name_at(ws, project_dir):
    (project_dir / "t.py").write_text("def test_add_two():\n    pass\n")
    assert not TestRef("t.py::test_add").present(ws)
    assert TestRef("t.py::test_add_two").present(ws)
    assert TestRef("t.py").present(ws) and not TestRef("missing.py::x").present(ws)


TEST_FILE = """import pytest
from app import add


def test_one():
    assert add(1, 1) == 2


@pytest.mark.slow
def test_two():
    assert add(1, 2) == 3


def test_three():
    assert True
"""


@pytest.mark.spec("WS-16")
def test_excerpt_and_remove(ws, project_dir):
    (project_dir / "t.py").write_text(TEST_FILE)
    names = ["test_one", "test_two", "test_three"]
    excerpt = TestRef("t.py::test_two").excerpt(ws, names)
    assert excerpt.startswith("import pytest\nfrom app import add\n...\n@pytest.mark.slow\ndef test_two():")
    assert "test_three" not in excerpt and "test_one" not in excerpt
    first = TestRef("t.py::test_one").excerpt(ws, names)
    assert first.startswith("import pytest") and first.rstrip().endswith("assert add(1, 1) == 2")
    assert TestRef("t.py::test_two").remove(ws, names)
    text = ws.read("t.py")
    assert "test_two" not in text and "mark.slow" not in text and "def test_three" in text and "def test_one" in text
    assert not TestRef("t.py::test_two").remove(ws, names)


@pytest.mark.spec("WS-17")
def test_key_line():
    output = "===== FAILURES =====\nsetup ok\nAssertionError: 3 != 4\nFAILED t.py::x\n----"
    assert Output.key_line(output) == "AssertionError: 3 != 4"
    assert Output.key_line("all fine\nbut it failed somewhere\nend") == "but it failed somewhere"
    assert Output.key_line("one\nlast") == "last"
    assert Output.key_line("") == "no output"


@pytest.mark.spec("WS-17")
def test_signature_ignores_numbers_and_times():
    a = "FAILED test_x - assert 3 == 4 (took 0.52s at 0x7f3a)"
    b = "FAILED test_x - assert 9 == 12 (took 1.1s at 0x1b2c)"
    assert Output.signature(a) == Output.signature(b) != Output.signature("FAILED test_x - KeyError: 'k'")


@pytest.mark.spec("WS-17")
def test_failure_text_and_compact_lines():
    long = "\n".join(f"line {i}" for i in range(500))
    text = Output.failure_text(long, 200)
    assert len(text) <= 200 and text.startswith("[...]") and text.endswith("line 499")
    assert Output.failure_text("a\n\n\nb") == "a\nb"
    line = Output.compact_lines("x" * 1000, 90)
    assert len(line) < 200 and "chars]" in line


@pytest.mark.spec("WS-18")
def test_misses_own_code():
    paths = ["moods.py", "src/app/main.py"]
    assert Output.misses_own_code("ModuleNotFoundError: No module named 'moods'", paths) == "moods"
    assert Output.misses_own_code("Error: Cannot find module './app/main'", paths) == "app/main"
    assert Output.misses_own_code("No module named 'requests'", paths) == ""


@pytest.mark.spec("WS-18")
def test_unloadable():
    out = '  File "tests/test_a.py", line 3\n    def x(\n        ^\nSyntaxError: unexpected EOF while parsing'
    assert Output.unloadable(out, "tests/test_a.py")
    code_broken = '  File "app.py", line 2\n    x = = 1\nSyntaxError: invalid syntax\nERROR tests/test_a.py'
    assert not Output.unloadable(code_broken, "tests/test_a.py")
    assert not Output.unloadable(out, "")


@pytest.mark.spec("WS-18")
def test_mentioned_in_and_tool_failure(project_dir):
    output = "count.py:3 error\n/abs/proj/count.py:9 error\ntests/test_count.py failed"
    assert Output.mentioned_in(output, ["tests/test_count.py", "count.py", "other.py"]) == \
        ["count.py", "tests/test_count.py"]
    failure = Output.tool_failure(f"{project_dir}/x.py:12: error: missing pytest after 3.2s", project_dir)
    assert str(project_dir) not in failure and "12" not in failure and "missing pytest" in failure
    assert Output.tool_failure(f"... {Output.OWN_CODE_UNSEEN} ...", project_dir) == Output.OWN_CODE_UNSEEN


@pytest.mark.spec("WS-19")
def test_file_view_shows_whole_files_then_windows(ws, project_dir):
    (project_dir / "small.py").write_text("x = 1\n")
    (project_dir / "big.py").write_text("".join(f"line_{i} = {i}\n" for i in range(1, 400)))
    view = FileView(ws, [Part("Criterion", "c", True)], ["small.py", "big.py", "missing.py"],
                    [Part("Tail", "t")], output='File "big.py", line 200, in x')
    parts = view(1500)
    titles = [p.title for p in parts]
    assert titles[0] == "Criterion" and titles[1] == "File small.py"
    assert titles[2].startswith("File big.py, lines ") and "of 399" in titles[2]
    assert titles[3] == "Changing a file shown in part" and titles[-1] == "Tail"
    first, last = view.windows["big.py"]
    assert first <= 200 <= last and "200| line_200 = 200" in parts[2].text
    assert view.shown == {"small.py", "big.py"}


@pytest.mark.spec("WS-19")
def test_file_view_without_windows_has_no_howto(ws, project_dir):
    (project_dir / "a.py").write_text("a = 1\n")
    parts = FileView(ws, [], ["a.py"])(5000)
    assert [p.title for p in parts] == ["File a.py"] and parts[0].exact
    assert FileView.focus_line("a.py", ["x", "use_thing()", "y"], "", "use_thing") == 2
    assert FileView.window_view(["a", "b", "c"], 2, 1000) == (1, 3, "   1| a\n   2| b\n   3| c")


@pytest.mark.spec("WS-22")
@pytest.mark.xfail(strict=True, reason="ISSUE-5: the window grows only downwards from the line it is about")
def test_a_window_holds_lines_on_both_sides_of_its_line():
    lines = [f"line{i}" for i in range(1, 21)]
    first, last, _ = FileView.window_view(lines, 10, 80)
    assert first < 10 < last


@pytest.mark.spec("WS-22")
def test_a_window_at_the_end_of_a_file_grows_upwards():
    lines = [f"line{i}" for i in range(1, 21)]
    assert FileView.window_view(lines, 20, 80)[:2] == (15, 20)


@pytest.mark.spec("WS-20")
def test_change():
    change = Change()
    assert not change and change.files == [] and change.created == []
    change.backups.update({"a.py": "old", "b.py": None})
    assert change and change.files == ["a.py", "b.py"] and change.created == ["b.py"]


@pytest.mark.spec("WS-21")
def test_online(monkeypatch):
    assert workspace.online() is False  # the network is off in tests
    opened = []

    class Connection:
        def close(self):
            opened.append("closed")
    monkeypatch.setattr(workspace.socket, "create_connection", lambda address, timeout: Connection())
    assert workspace.online() is True and opened == ["closed"]
