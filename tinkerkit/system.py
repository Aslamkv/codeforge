import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SYSTEM_CHECK_SECONDS = 5


@dataclass(frozen=True)
class SystemCheck:
    word: str
    mac: tuple[str, ...]
    linux: tuple[str, ...]

    def answer(self) -> str:
        argv = self.command()
        output = run_quietly(argv) if argv else ""
        return output or f"⚠️ No {self.word} on this computer"

    def command(self) -> tuple[str, ...]:
        return {"darwin": self.mac, "linux": self.linux}.get(sys.platform, ())


def run_quietly(argv: tuple[str, ...]) -> str:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=SYSTEM_CHECK_SECONDS)
    except OSError, subprocess.SubprocessError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


TIME = ("date", "+%H:%M %Z, %A %d %B %Y")
DATE = ("date", "+%A %d %B %Y")
DISK = ("df", "-h", str(Path.home()))

SYSTEM_CHECKS = (
    SystemCheck("time", TIME, TIME),
    SystemCheck("date", DATE, DATE),
    SystemCheck("disk", DISK, DISK),
    SystemCheck("ram", ("top", "-l", "1", "-n", "0"), ("free", "-h")),
    SystemCheck(
        "battery", ("pmset", "-g", "batt"), ("upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0")
    ),
    SystemCheck("uptime", ("uptime",), ("uptime",)),
    SystemCheck("network", ("ipconfig", "getifaddr", "en0"), ("hostname", "-I")),
)

SYSTEM_WORDS = tuple(check.word for check in SYSTEM_CHECKS)
