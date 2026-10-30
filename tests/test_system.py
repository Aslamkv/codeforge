import subprocess

import pytest

import tinkerkit.system as system
from tinkerkit.system import SYSTEM_CHECK_SECONDS, SYSTEM_CHECKS, SYSTEM_WORDS


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def fake_run(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, stdout="  14:05 CET  \n")

    monkeypatch.setattr(system.subprocess, "run", fake_run)
    return calls


def check_named(word):
    return next(check for check in SYSTEM_CHECKS if check.word == word)


def answer_with(monkeypatch, fake_run, platform="darwin"):
    monkeypatch.setattr(system.subprocess, "run", fake_run)
    monkeypatch.setattr(system.sys, "platform", platform)
    return check_named("time").answer()


@pytest.mark.spec("SYS-1")
def test_system_checks_are_the_known_words_each_with_a_fixed_command_per_system():
    assert SYSTEM_WORDS == ("time", "date", "disk", "ram", "battery", "uptime", "network")
    for check in SYSTEM_CHECKS:
        for argv in (check.mac, check.linux):
            assert isinstance(argv, tuple) and argv and all(isinstance(part, str) for part in argv)
    assert check_named("ram").mac == ("top", "-l", "1", "-n", "0")
    assert check_named("ram").linux == ("free", "-h")
    assert SYSTEM_CHECK_SECONDS == 5


@pytest.mark.spec("SYS-2")
@pytest.mark.parametrize(
    ("platform", "argv"),
    [
        ("darwin", ["ipconfig", "getifaddr", "en0"]),
        ("linux", ["hostname", "-I"]),
    ],
)
def test_answer_runs_the_command_for_this_system_without_a_shell(runs, monkeypatch, platform, argv):
    monkeypatch.setattr(system.sys, "platform", platform)
    assert check_named("network").answer() == "14:05 CET"
    called, options = runs[0]
    assert called == argv
    assert options.get("shell", False) is False
    assert options["timeout"] == SYSTEM_CHECK_SECONDS


@pytest.mark.spec("SYS-2")
def test_answer_on_a_system_without_a_command_runs_nothing(runs, monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "plan9")
    assert check_named("battery").answer() == "⚠️ No battery on this computer"
    assert runs == []


def missing(argv, **_):
    raise FileNotFoundError(argv[0])


def too_slow(argv, **options):
    raise subprocess.TimeoutExpired(argv, options["timeout"])


def failed(argv, **_):
    return subprocess.CompletedProcess(argv, 1, stdout="partial\n")


def empty(argv, **_):
    return subprocess.CompletedProcess(argv, 0, stdout=" \n")


@pytest.mark.spec("SYS-2")
@pytest.mark.parametrize("fake_run", [missing, too_slow, failed, empty])
def test_answer_says_none_instead_of_raising(monkeypatch, fake_run):
    assert answer_with(monkeypatch, fake_run) == "⚠️ No time on this computer"
