import subprocess
import sys
from pathlib import Path
from typing import ClassVar

SYSTEM_CHECK_SECONDS = 5


class SystemCheck:
    registered: ClassVar[list[SystemCheck]] = []
    word: ClassVar[str]
    emoji: ClassVar[str]
    label: ClassVar[str]
    mac: ClassVar[tuple[str, ...]]
    linux: ClassVar[tuple[str, ...]]

    def __init_subclass__(cls, **options):
        super().__init_subclass__(**options)
        SystemCheck.registered.append(cls())

    def answer(self) -> str:
        argv = self.command()
        output = run_quietly(argv) if argv else ""
        summary = self.summary(output) if output else None
        return f"{self.emoji} {self.label}: {summary}" if summary else self.fallback()

    def command(self) -> tuple[str, ...]:
        return {"darwin": self.mac, "linux": self.linux}.get(sys.platform, ())

    def summary(self, output: str) -> str | None:
        return output

    def fallback(self) -> str:
        return f"⚠️ {self.label}: couldn't check on this computer"


def run_quietly(argv: tuple[str, ...]) -> str:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=SYSTEM_CHECK_SECONDS)
    except OSError, subprocess.SubprocessError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


class TimeCheck(SystemCheck):
    word, emoji, label = "time", "🕒", "Time"
    mac = linux = ("date", "+%H:%M %Z")


class DateCheck(SystemCheck):
    word, emoji, label = "date", "📅", "Date"
    mac = linux = ("date", "+%A %d %B %Y")


class DiskCheck(SystemCheck):
    word, emoji, label = "disk", "💾", "Disk"
    mac = linux = ("df", "-h", str(Path.home()))


class RamCheck(SystemCheck):
    word, emoji, label = "ram", "🧠", "RAM"
    mac = ("memory_pressure",)
    linux = ("free", "-h")


class BatteryCheck(SystemCheck):
    word, emoji, label = "battery", "🔋", "Battery"
    mac = ("pmset", "-g", "batt")
    linux = ("upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0")


class UptimeCheck(SystemCheck):
    word, emoji, label = "uptime", "⏱️", "Uptime"
    mac = linux = ("uptime",)


class NetworkCheck(SystemCheck):
    word, emoji, label = "network", "🌐", "Network"
    mac = ("ipconfig", "getifaddr", "en0")
    linux = ("hostname", "-I")

    def summary(self, output: str) -> str | None:
        return output.split()[0]


SYSTEM_CHECKS = tuple(SystemCheck.registered)

SYSTEM_WORDS = tuple(check.word for check in SYSTEM_CHECKS)
