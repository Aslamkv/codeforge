import ctypes
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
PROCESS_MARGIN = 32
OUTPUT_LENGTH = 4000
PROBE_SECONDS = 10

CLONE_NEWUSER = 0x10000000
CLONE_NEWNET = 0x40000000
O_PATH = 0o10000000
O_CLOEXEC = 0o2000000
PR_SET_NO_NEW_PRIVS = 38
LANDLOCK_CREATE_RULESET = 444
LANDLOCK_ADD_RULE = 445
LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
WRITE_FILE = 1 << 1
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13
TRUNCATE = 1 << 14
WRITE_RIGHTS_BY_ABI = {
    1: WRITE_FILE
    | REMOVE_DIR
    | REMOVE_FILE
    | MAKE_CHAR
    | MAKE_DIR
    | MAKE_REG
    | MAKE_SOCK
    | MAKE_FIFO
    | MAKE_BLOCK
    | MAKE_SYM,
    2: REFER,
    3: TRUNCATE,
}


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
        backend = {"darwin": MacSandbox, "linux": LinuxSandbox}.get(sys.platform)
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
                preexec_fn=self.prepared(folder),
            )
        except subprocess.TimeoutExpired as expired:
            return Ran(True, output=cut(expired.output), timed_out=True)
        return Ran(True, done.returncode, cut(done.stdout))

    def prepared(self, folder):
        apply_limits = limits(self.process_limit())

        def prepare():
            apply_limits()
            self.confine(folder)

        return prepare

    def wrapped(self, command, folder):
        return list(command)

    def confine(self, folder):
        pass

    def process_limit(self):
        return processes_of_this_user() + PROCESS_MARGIN


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


class LinuxSandbox(Sandbox):
    @classmethod
    def probe(cls):
        sandbox = cls()
        try:
            with cls.folder() as folder:
                done = subprocess.run(
                    ["true"],
                    cwd=folder,
                    capture_output=True,
                    timeout=PROBE_SECONDS,
                    preexec_fn=lambda: sandbox.confine(folder),
                )
        except (OSError, subprocess.SubprocessError) as error:
            return f"no user namespaces or Landlock here: {error}"
        return f"a confined command fails: {done.returncode}" if done.returncode else ""

    def confine(self, folder):
        os.unshare(CLONE_NEWUSER | CLONE_NEWNET)
        landlock = libc()
        rights = handled_rights(landlock_version(landlock))
        ruleset = new_ruleset(landlock, rights)
        allow_writes_beneath(landlock, ruleset, str(folder), rights)
        allow_writes_beneath(landlock, ruleset, "/dev/null", WRITE_FILE)
        checked(landlock.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0))
        checked(landlock_call(landlock, LANDLOCK_RESTRICT_SELF, ruleset, 0))


class NoSandbox(Sandbox):
    def __init__(self, why):
        self.why = why

    def run(self, command, folder, timeout=TIMEOUT_SECONDS):
        return Ran(False, why=self.why)


class RulesetAttribute(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def libc():
    loaded = ctypes.CDLL(None, use_errno=True)
    loaded.syscall.restype = ctypes.c_long
    return loaded


def landlock_version(landlock):
    return checked(landlock_call(landlock, LANDLOCK_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION))


def new_ruleset(landlock, rights):
    attribute = RulesetAttribute(rights)
    return checked(
        landlock_call(landlock, LANDLOCK_CREATE_RULESET, ctypes.byref(attribute), ctypes.sizeof(attribute), 0)
    )


def handled_rights(abi):
    return sum(rights for version, rights in WRITE_RIGHTS_BY_ABI.items() if version <= abi)


def allow_writes_beneath(landlock, ruleset, path, rights):
    descriptor = os.open(path, O_PATH | O_CLOEXEC)
    try:
        rule = PathBeneath(rights, descriptor)
        checked(
            landlock_call(
                landlock, LANDLOCK_ADD_RULE, ruleset, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(rule), 0
            )
        )
    finally:
        os.close(descriptor)


def landlock_call(landlock, number, *arguments):
    return landlock.syscall(
        number, *(ctypes.c_long(value) if isinstance(value, int) else value for value in arguments)
    )


def checked(result):
    if result < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return result


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
