import subprocess
from pathlib import Path

import pytest

import tinkerkit.system as system
from tinkerkit.system import SYSTEM_CHECK_SECONDS, SYSTEM_CHECKS, SYSTEM_WORDS, SystemCheck


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


HOME = str(Path.home())

TABLE = [
    ("time", "🕒", "Time", ("date", "+%H:%M %Z"), ("date", "+%H:%M %Z")),
    ("date", "📅", "Date", ("date", "+%A %d %B %Y"), ("date", "+%A %d %B %Y")),
    ("disk", "💾", "Disk", ("df", "-h", HOME), ("df", "-h", HOME)),
    ("ram", "🧠", "RAM", ("memory_pressure",), ("free", "-h")),
    (
        "battery",
        "🔋",
        "Battery",
        ("pmset", "-g", "batt"),
        ("upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"),
    ),
    ("uptime", "⏱️", "Uptime", ("uptime",), ("uptime",)),
    ("network", "🌐", "Network", ("ipconfig", "getifaddr", "en0"), ("hostname", "-I")),
]


@pytest.mark.spec("SYS-1")
def test_system_checks_are_exactly_the_table_rows_in_order():
    rows = [(check.word, check.emoji, check.label, check.mac, check.linux) for check in SYSTEM_CHECKS]
    assert rows == TABLE
    assert SYSTEM_WORDS == tuple(row[0] for row in TABLE)
    assert "memory" not in SYSTEM_WORDS
    assert SYSTEM_CHECK_SECONDS == 5


@pytest.mark.spec("SYS-2")
def test_every_check_is_one_registered_subclass_of_system_check():
    assert all(isinstance(check, SystemCheck) and type(check) is not SystemCheck for check in SYSTEM_CHECKS)
    assert len({type(check) for check in SYSTEM_CHECKS}) == len(SYSTEM_CHECKS)


@pytest.mark.spec("SYS-2")
def test_a_new_subclass_registers_itself_after_the_table(monkeypatch):
    monkeypatch.setattr(SystemCheck, "registered", list(SystemCheck.registered))

    class Weather(SystemCheck):
        word, emoji, label, mac, linux = "weather", "☁️", "Weather", ("true",), ("true",)

    assert type(SystemCheck.registered[-1]) is Weather


@pytest.mark.spec("SYS-1")
@pytest.mark.parametrize(
    ("word", "output", "line"),
    [
        ("time", "11:16 IST", "🕒 Time: 11:16 IST"),
        ("date", "Saturday 31 October 2026", "📅 Date: Saturday 31 October 2026"),
        ("network", "192.168.220.41 10.0.0.2", "🌐 Network: 192.168.220.41"),
    ],
)
def test_answer_gives_emoji_label_and_summary(monkeypatch, word, output, line):
    monkeypatch.setattr(system.sys, "platform", "linux")
    monkeypatch.setattr(system.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, output))
    assert check_named(word).answer() == line


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
    assert check_named("network").answer() == "🌐 Network: 14:05"
    called, options = runs[0]
    assert called == argv
    assert options.get("shell", False) is False
    assert options["timeout"] == SYSTEM_CHECK_SECONDS


@pytest.mark.spec("SYS-2")
def test_answer_on_a_system_without_a_command_runs_nothing(runs, monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "plan9")
    assert check_named("battery").answer() == "⚠️ Battery: couldn't check on this computer"
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
def test_answer_falls_back_instead_of_raising(monkeypatch, fake_run):
    assert answer_with(monkeypatch, fake_run) == "⚠️ Time: couldn't check on this computer"


@pytest.mark.spec("SYS-2")
def test_answer_falls_back_when_summary_cannot_read_the_output(runs, monkeypatch):
    monkeypatch.setattr(type(check_named("time")), "summary", lambda check, output: None)
    assert check_named("time").answer() == "⚠️ Time: couldn't check on this computer"
