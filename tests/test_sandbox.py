import subprocess
import sys

import pytest

import tinkerkit.sandbox as sandbox
from tinkerkit.sandbox import LinuxSandbox, MacSandbox, NoSandbox, Sandbox


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def fake_run(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 3, stdout="printed\n")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    return calls


@pytest.mark.spec("SBX-1")
def test_here_picks_the_backend_by_platform_and_none_when_its_probe_fails(monkeypatch):
    monkeypatch.setattr(MacSandbox, "probe", classmethod(lambda cls: ""))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert type(Sandbox.here()) is MacSandbox
    monkeypatch.setattr(sys, "platform", "plan9")
    assert type(Sandbox.here()) is NoSandbox
    assert "plan9" in Sandbox.here().why
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(MacSandbox, "probe", classmethod(lambda cls: "sandbox-exec is missing"))
    assert type(Sandbox.here()) is NoSandbox
    assert Sandbox.here().why == "sandbox-exec is missing"


@pytest.mark.spec("SBX-1")
def test_here_picks_linux_sandbox_on_linux_and_none_when_its_probe_fails(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(LinuxSandbox, "probe", classmethod(lambda cls: ""))
    assert type(Sandbox.here()) is LinuxSandbox
    monkeypatch.setattr(LinuxSandbox, "probe", classmethod(lambda cls: "user namespaces are off"))
    assert type(Sandbox.here()) is NoSandbox
    assert Sandbox.here().why == "user namespaces are off"


@pytest.mark.spec("SBX-4")
def test_no_sandbox_runs_nothing_and_says_why(runs):
    with Sandbox.folder() as folder:
        result = NoSandbox("no sandbox on plan9").run(["python3", "check.py"], folder)
    assert runs == []
    assert not result.ran and result.why == "no sandbox on plan9"


@pytest.mark.spec("SBX-2")
def test_a_run_starts_in_a_new_empty_folder_that_goes_after(runs, monkeypatch):
    monkeypatch.setattr(MacSandbox, "process_limit", lambda self: 500)
    with Sandbox.folder() as folder:
        assert folder.is_dir() and list(folder.iterdir()) == [] and folder == folder.resolve()
        result = MacSandbox().run(["python3", "check.py"], folder, timeout=7)
    assert not folder.exists()
    _, options = runs[0]
    assert options["cwd"] == folder and options["timeout"] == 7
    assert options["env"]["HOME"] == str(folder) and options["env"]["TMPDIR"] == str(folder)
    assert callable(options["preexec_fn"])
    assert result.ran and result.code == 3 and result.output == "printed\n" and not result.timed_out


@pytest.mark.spec("SBX-2")
def test_a_run_past_its_timeout_is_timed_out(monkeypatch):
    def too_slow(argv, **options):
        raise subprocess.TimeoutExpired(argv, options["timeout"], output=b"started")

    monkeypatch.setattr(sandbox.subprocess, "run", too_slow)
    monkeypatch.setattr(MacSandbox, "process_limit", lambda self: 500)
    with Sandbox.folder() as folder:
        result = MacSandbox().run(["python3", "check.py"], folder, timeout=1)
    assert result.ran and result.timed_out and result.output == "started"


@pytest.mark.spec("SBX-2")
def test_a_long_output_is_cut(monkeypatch):
    def loud(argv, **options):
        return subprocess.CompletedProcess(argv, 0, stdout="x" * (sandbox.OUTPUT_LENGTH * 2))

    monkeypatch.setattr(sandbox.subprocess, "run", loud)
    monkeypatch.setattr(MacSandbox, "process_limit", lambda self: 500)
    with Sandbox.folder() as folder:
        result = MacSandbox().run(["true"], folder)
    assert len(result.output) == sandbox.OUTPUT_LENGTH


@pytest.mark.spec("SBX-3")
def test_mac_runs_under_sandbox_exec_denying_the_network_and_writes_outside(runs, monkeypatch):
    monkeypatch.setattr(MacSandbox, "process_limit", lambda self: 500)
    with Sandbox.folder() as folder:
        MacSandbox().run(["python3", "check.py"], folder)
    argv, _ = runs[0]
    assert argv[:2] == ["sandbox-exec", "-p"] and argv[3:] == ["python3", "check.py"]
    profile = argv[2]
    assert "(deny network*)" in profile and "(deny file-write*)" in profile
    assert f'(subpath "{folder}")' in profile


@pytest.mark.spec("SBX-2")
def test_a_run_allows_the_processes_already_running_and_a_few_more(monkeypatch):
    monkeypatch.setattr(sandbox, "processes_of_this_user", lambda: 40)
    assert MacSandbox().process_limit() == 40 + sandbox.PROCESS_MARGIN
    assert LinuxSandbox().process_limit() == 40 + sandbox.PROCESS_MARGIN


class FakeLibc:
    def __init__(self, abi, failing=None):
        self.abi = abi
        self.failing = failing
        self.calls = []

    def syscall(self, number, *arguments):
        self.calls.append((number, *map(plain, arguments)))
        if number == self.failing:
            return -1
        if number == sandbox.LANDLOCK_CREATE_RULESET:
            return self.abi if arguments[0] is None else 7
        return 0

    def prctl(self, *arguments):
        self.calls.append(("prctl", *map(plain, arguments)))
        return 0


def plain(argument):
    if hasattr(argument, "_obj"):
        return {name: getattr(argument._obj, name) for name, _ in argument._obj._fields_}
    return getattr(argument, "value", argument)


@pytest.fixture
def confined(monkeypatch):
    def confine(abi, failing=None):
        libc = FakeLibc(abi, failing)
        unshared = []
        monkeypatch.setattr(sandbox, "libc", lambda: libc)
        monkeypatch.setattr(sandbox.os, "unshare", lambda flags: unshared.append(flags), raising=False)
        monkeypatch.setattr(sandbox.os, "open", lambda path, flags: 11 if path == "/folder" else 12)
        monkeypatch.setattr(sandbox.os, "close", lambda fd: None)
        LinuxSandbox().confine("/folder")
        return unshared, libc.calls

    return confine


@pytest.mark.spec("SBX-3")
def test_linux_cuts_the_network_then_allows_writes_only_in_the_folder(confined):
    unshared, calls = confined(abi=3)
    assert unshared == [sandbox.CLONE_NEWUSER | sandbox.CLONE_NEWNET]
    rights = calls[1][1]["handled_access_fs"]
    assert calls == [
        (444, None, 0, 1),
        (444, {"handled_access_fs": rights}, 8, 0),
        (445, 7, 1, {"allowed_access": rights, "parent_fd": 11}, 0),
        (445, 7, 1, {"allowed_access": sandbox.WRITE_FILE, "parent_fd": 12}, 0),
        ("prctl", 38, 1, 0, 0, 0),
        (446, 7, 0),
    ]


@pytest.mark.spec("SBX-3")
def test_linux_handles_every_write_right_its_landlock_version_knows(confined):
    newest = confined(abi=3)[1][1][1]["handled_access_fs"]
    oldest = confined(abi=1)[1][1][1]["handled_access_fs"]
    assert newest == oldest | sandbox.REFER | sandbox.TRUNCATE
    assert not oldest & (sandbox.REFER | sandbox.TRUNCATE) and oldest & sandbox.WRITE_FILE


@pytest.mark.spec("SBX-3")
@pytest.mark.parametrize("failing", [444, 445, 446])
def test_linux_refuses_to_run_when_a_landlock_step_fails(confined, failing):
    with pytest.raises(OSError):
        confined(abi=3, failing=failing)


@pytest.mark.spec("SBX-2")
def test_a_real_run_writes_only_in_its_folder_and_reaches_no_network(tmp_path):
    here = Sandbox.here()
    if isinstance(here, NoSandbox):
        pytest.skip(here.why)
    outside = tmp_path / "outside"
    script = (
        "import pathlib, socket\n"
        "pathlib.Path('inside').write_text('ok')\n"
        "try:\n"
        f"    pathlib.Path({str(outside)!r}).write_text('no')\n"
        "except OSError:\n"
        "    print('outside refused')\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "except OSError:\n"
        "    print('network refused')\n"
    )
    with Sandbox.folder() as folder:
        (folder / "check.py").write_text(script)
        result = here.run([sys.executable, "check.py"], folder)
        assert (folder / "inside").read_text() == "ok"
    assert result.code == 0, result.output
    assert "outside refused" in result.output and "network refused" in result.output
    assert not outside.exists()


@pytest.mark.spec("SBX-3")
def test_a_real_linux_run_still_writes_to_dev_null():
    here = Sandbox.here()
    if not isinstance(here, LinuxSandbox):
        pytest.skip("not a Linux sandbox here")
    with Sandbox.folder() as folder:
        result = here.run(["sh", "-c", "echo hidden > /dev/null && echo shown"], folder)
    assert result.code == 0 and result.output == "shown\n", result.output
