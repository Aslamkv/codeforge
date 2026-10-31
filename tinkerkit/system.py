import re
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

    def summary(self, output: str) -> str | None:
        row = " ".join(output.splitlines()[1:]).split()
        if len(row) < 5 or not row[4].endswith("%"):
            return None
        size, free = size_text(row[1]), size_text(row[3])
        return f"{free} free of {size} ({row[4]} used)" if size and free else None


class RamCheck(SystemCheck):
    word, emoji, label = "ram", "🧠", "RAM"
    mac = ("memory_pressure",)
    linux = ("free", "-h")

    def summary(self, output: str) -> str | None:
        reader = self.read_mac if sys.platform == "darwin" else self.read_linux
        return reader(output)

    def read_mac(self, output: str) -> str | None:
        total = re.search(r"The system has (\d+)", output)
        free = re.search(r"free percentage: (\d+)%", output)
        if not total or not free:
            return None
        total_gb = int(total[1]) / 1024**3
        available_gb = total_gb * int(free[1]) / 100
        return f"{round(available_gb)} GB available of {round(total_gb)} GB ({100 - int(free[1])}% used)"

    def read_linux(self, output: str) -> str | None:
        lines = output.splitlines()
        header = lines[0].split()
        row = next((line.split() for line in lines if line.startswith("Mem:")), [])
        if "available" not in header or len(row) != len(header) + 1:
            return None
        total, available = row[1], row[header.index("available") + 1]
        total_gb, available_gb = size_gb(total), size_gb(available)
        if not total_gb or available_gb is None:
            return None
        used = round((1 - available_gb / total_gb) * 100)
        return f"{size_text(available)} available of {size_text(total)} ({used}% used)"


SIZE = re.compile(r"(\d+(?:\.\d+)?)([GM])i?")


def size_text(text: str) -> str | None:
    found = SIZE.fullmatch(text)
    return f"{found[1]} {found[2]}B" if found else None


def size_gb(text: str) -> float | None:
    found = SIZE.fullmatch(text)
    if not found:
        return None
    return float(found[1]) / (1 if found[2] == "G" else 1024)


class BatteryCheck(SystemCheck):
    word, emoji, label = "battery", "🔋", "Battery"
    mac = ("pmset", "-g", "batt")
    linux = ("upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0")

    def summary(self, output: str) -> str | None:
        reader = self.read_mac if sys.platform == "darwin" else self.read_linux
        return reader(output)

    def read_mac(self, output: str) -> str | None:
        battery = re.search(r"(\d+%); ([^;]+);", output)
        if not battery:
            return None
        source = re.search(r"drawing from '(.+?) Power'", output)
        parts = [battery[1], battery[2]] + ([power_source(source[1])] if source else [])
        return ", ".join(parts)

    def read_linux(self, output: str) -> str | None:
        percentage = re.search(r"percentage:\s+(\d+)(?:\.\d+)?%", output)
        state = re.search(r"state:\s+(\S+)", output)
        return f"{percentage[1]}%, {state[1]}" if percentage and state else None


def power_source(name: str) -> str:
    return f"on {name if name.isupper() else name.lower()} power"


class UptimeCheck(SystemCheck):
    word, emoji, label = "uptime", "⏱️", "Uptime"
    mac = linux = ("uptime",)

    def summary(self, output: str) -> str | None:
        found = re.search(r"up\s+(?:(\d+) days?,\s*)?(?:(\d+):(\d+)|(\d+) mins?|(\d+) hrs?)?,", output)
        if not found or not any(found.groups()):
            return None
        days, hours, minutes, only_minutes, only_hours = (int(group or 0) for group in found.groups())
        amounts = [(days, "d"), (hours or only_hours, "h"), (minutes or only_minutes, "m")]
        return " ".join(f"{amount} {unit}" for amount, unit in amounts if amount) or "0 m"


class NetworkCheck(SystemCheck):
    word, emoji, label = "network", "🌐", "Network"
    mac = ("ipconfig", "getifaddr", "en0")
    linux = ("hostname", "-I")

    def summary(self, output: str) -> str | None:
        return output.split()[0]


SYSTEM_CHECKS = tuple(SystemCheck.registered)

SYSTEM_WORDS = tuple(check.word for check in SYSTEM_CHECKS)
