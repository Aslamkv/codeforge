import os
import resource
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_SECONDS = 30
CPU_SECONDS = 20
FILE_SIZE_BYTES = 10 * 1024 * 1024
PROCESS_LIMIT = 64
PROCESS_MARGIN = 32
OUTPUT_LENGTH = 4000
PROBE_SECONDS = 10


@dataclass
class Ran:
    ran: bool
    code: int | None = None
    output: str = ""
    timed_out: bool = False
    why: str = ""


class Sandbox:
    @classmethod
    def here(cls):
        backend = {"darwin": MacSandbox}.get(sys.platform)
        if backend is None:
            return NoSandbox(f"no sandbox on {sys.platform}")
        why = backend.probe()
        return NoSandbox(why) if why else backend()

    @classmethod
    def probe(cls):
        return ""

    @staticmethod
    @contextmanager
    def folder():
        folder = Path(tempfile.mkdtemp(prefix="tinker-sandbox-")).resolve()
        try:
            yield folder
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def run(self, command, folder, timeout=TIMEOUT_SECONDS):
        try:
            done = subprocess.run(
                self.wrapped(command, folder),
                cwd=folder,
                env=bare_environment(folder),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
                preexec_fn=limits(self.process_limit()),
            )
        except subprocess.TimeoutExpired as expired:
            return Ran(True, output=cut(expired.output), timed_out=True)
        return Ran(True, done.returncode, cut(done.stdout))

    def wrapped(self, command, folder):
        return list(command)

    def process_limit(self):
        return PROCESS_LIMIT


class MacSandbox(Sandbox):
    @classmethod
    def probe(cls):
        try:
            done = subprocess.run(
                ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=PROBE_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"sandbox-exec does not run: {error}"
        return f"sandbox-exec fails: {done.stderr.strip() or done.returncode}" if done.returncode else ""

    def wrapped(self, command, folder):
        return ["sandbox-exec", "-p", mac_profile(folder), *command]

    def process_limit(self):
        return processes_of_this_user() + PROCESS_MARGIN


class NoSandbox(Sandbox):
    def __init__(self, why):
        self.why = why

    def run(self, command, folder, timeout=TIMEOUT_SECONDS):
        return Ran(False, why=self.why)


def mac_profile(folder):
    return (
        "(version 1) (allow default) (deny network*) (deny file-write*) "
        f'(allow file-write* (subpath "{folder}") (literal "/dev/null"))'
    )


def processes_of_this_user():
    listed = subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid="], capture_output=True, text=True)
    return len(listed.stdout.split())


def bare_environment(folder):
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(folder),
        "TMPDIR": str(folder),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def limits(processes):
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_BYTES, FILE_SIZE_BYTES))
        resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))

    return apply


def cut(output):
    text = output.decode(errors="replace") if isinstance(output, bytes) else output or ""
    return text[:OUTPUT_LENGTH]
