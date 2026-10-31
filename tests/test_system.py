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
    monkeypatch.setattr(
        system.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, output)
    )
    assert check_named(word).answer() == line


MAC_DF = """Filesystem       Size   Used  Avail Capacity iused ifree %iused  Mounted on
/dev/disk3s5    926Gi  294Gi  609Gi    33%    1.6M  6.4G    0%   /System/Volumes/Data"""

LINUX_DF = """Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        98G   40G   53G  43% /"""

LINUX_DF_WRAPPED = """Filesystem      Size  Used Avail Use% Mounted on
/dev/mapper/ubuntu--vg-ubuntu--lv
                 98G   40G   53G  43% /"""

MEMORY_PRESSURE = """The system has 25769803776 (1572864 pages with a page size of 16384).

Stats:
Pages free: 1176254

System-wide memory free percentage: 86%"""

FREE = """               total        used        free      shared  buff/cache   available
Mem:            15Gi       4.2Gi       8.1Gi       512Mi       3.0Gi        10Gi
Swap:          2.0Gi          0B       2.0Gi"""

PMSET_AC = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=22806627)\t100%; charged; 0:00 remaining present: true"""

PMSET_BATTERY = """Now drawing from 'Battery Power'
 -InternalBattery-0 (id=22806627)\t76%; discharging; 5:12 remaining present: true"""

UPOWER = """  native-path:          BAT0
  battery
    present:             yes
    state:               discharging
    energy:              40.1 Wh
    percentage:          87%"""


@pytest.mark.spec("SYS-1")
@pytest.mark.parametrize(
    ("word", "platform", "output", "line"),
    [
        ("disk", "darwin", MAC_DF, "💾 Disk: 609 GB free of 926 GB (33% used)"),
        ("disk", "linux", LINUX_DF, "💾 Disk: 53 GB free of 98 GB (43% used)"),
        ("disk", "linux", LINUX_DF_WRAPPED, "💾 Disk: 53 GB free of 98 GB (43% used)"),
        ("ram", "darwin", MEMORY_PRESSURE, "🧠 RAM: 21 GB available of 24 GB (14% used)"),
        ("ram", "linux", FREE, "🧠 RAM: 10 GB available of 15 GB (33% used)"),
        ("battery", "darwin", PMSET_AC, "🔋 Battery: 100%, charged, on AC power"),
        ("battery", "darwin", PMSET_BATTERY, "🔋 Battery: 76%, discharging, on battery power"),
        ("battery", "linux", UPOWER, "🔋 Battery: 87%, discharging"),
        ("uptime", "darwin", "11:25  up  4:03, 2 users, load averages: 2.1 1.9 1.8", "⏱️ Uptime: 4 h 3 m"),
        (
            "uptime",
            "darwin",
            "11:25  up 2 days,  3:53, 2 users, load averages: 2.1",
            "⏱️ Uptime: 2 d 3 h 53 m",
        ),
        ("uptime", "darwin", "11:25  up 45 mins, 2 users, load averages: 2.1", "⏱️ Uptime: 45 m"),
        ("uptime", "darwin", "11:25  up 1 day, 10 mins, 2 users, load averages: 2.1", "⏱️ Uptime: 1 d 10 m"),
        ("uptime", "darwin", "11:25  up 3:00, 2 users, load averages: 2.1", "⏱️ Uptime: 3 h"),
        (
            "uptime",
            "linux",
            " 11:25:01 up 2 days,  3:53,  1 user,  load average: 0.10, 0.20",
            "⏱️ Uptime: 2 d 3 h 53 m",
        ),
    ],
)
def test_summary_reads_the_output_of_each_system(monkeypatch, word, platform, output, line):
    monkeypatch.setattr(system.sys, "platform", platform)
    monkeypatch.setattr(
        system.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, output)
    )
    assert check_named(word).answer() == line


@pytest.mark.spec("SYS-2")
@pytest.mark.parametrize(
    ("word", "label"), [("disk", "Disk"), ("ram", "RAM"), ("battery", "Battery"), ("uptime", "Uptime")]
)
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_summary_that_cannot_read_the_output_falls_back(monkeypatch, word, label, platform):
    monkeypatch.setattr(system.sys, "platform", platform)
    monkeypatch.setattr(
        system.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, "garbled\n")
    )
    assert check_named(word).answer() == f"⚠️ {label}: couldn't check on this computer"


@pytest.mark.spec("SYS-1")
@pytest.mark.parametrize(
    ("text", "size"),
    [("926Gi", "926 GB"), ("23G", "23 GB"), ("8.1Gi", "8.1 GB"), ("512Mi", "512 MB"), ("0B", None)],
)
def test_sizes_read_gb_or_mb(text, size):
    assert system.size_text(text) == size


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
