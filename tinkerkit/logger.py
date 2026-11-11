import functools
import importlib
import json
import os
import threading
import time
import zipfile
from datetime import datetime

from tinkerkit.settings import Home

LOG_ROTATE_BYTES = 20_000_000
LOG_VALUE_CHARS = 120


class Watched:
    def __init__(self, module, owner, method):
        self.module, self.owner, self.method = module, owner, method

    def classes(self):
        return [self.owner_class()]

    def owner_class(self):
        return getattr(importlib.import_module(self.module), self.owner)


class WatchedFamily(Watched):
    def __init__(self, module, owner, method, loads):
        super().__init__(module, owner, method)
        self.loads = loads

    def classes(self):
        for module in self.loads:
            importlib.import_module(module)
        return [cls for cls in descendants(self.owner_class()) if self.method in cls.__dict__]


def descendants(cls):
    found = []
    for child in cls.__subclasses__():
        found += [child] + descendants(child)
    return list(dict.fromkeys(found))


WATCHED = (
    Watched("tinkerkit.cli", "Commands", "run"),
    Watched("tinkerkit.cli", "ProjectRun", "execute"),
    WatchedFamily(
        "tinkerkit.session",
        "Phase",
        "run",
        ("tinkerkit.planning", "tinkerkit.building", "tinkerkit.finishing"),
    ),
    Watched("tinkerkit.model", "Model", "ask"),
    Watched("tinkerkit.model", "Ollama", "chat"),
    Watched("tinkerkit.workspace", "Workspace", "run"),
    Watched("tinkerkit.workspace", "Workspace", "start"),
    Watched("tinkerkit.web", "Internet", "search_all"),
    Watched("tinkerkit.web", "Internet", "fetch"),
    Watched("tinkerkit.store", "Store", "get"),
    Watched("tinkerkit.store", "Store", "put"),
    Watched("tinkerkit.store", "Store", "append"),
)

originals = {}


def install():
    for watched in WATCHED:
        for cls in watched.classes():
            wrap(cls, watched.method)


def uninstall():
    for (cls, method), original in originals.items():
        setattr(cls, method, original)
    originals.clear()


def wrap(cls, method):
    if (cls, method) in originals:
        return
    original = cls.__dict__[method]
    originals[cls, method] = original
    setattr(cls, method, wrapped(original, f"{cls.__name__}.{method}"))


def wrapped(original, call):
    if isinstance(original, staticmethod):
        return staticmethod(watcher(original.__func__, call, skipped=0))
    return watcher(original, call, skipped=1)


class Depth(threading.local):
    level = 0


depth = Depth()


def watcher(function, call, skipped):
    @functools.wraps(function)
    def watch(*args, **kwargs):
        started = Call(call, args[skipped:], kwargs, depth.level)
        depth.level += 1
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            started.end(failed, error)
            raise
        finally:
            depth.level -= 1
        started.end(returned, result)
        return result

    return watch


class Call:
    def __init__(self, call, args, kwargs, level):
        self.call, self.args, self.kwargs, self.level = call, args, kwargs, level
        self.at, self.clock = datetime.now(), time.perf_counter()

    def end(self, outcome, value):
        seconds = time.perf_counter() - self.clock
        try:
            TraceLog.write(self.entry(seconds) | outcome(value))
        except Exception:
            pass

    def entry(self, seconds):
        return {
            "at": self.at.isoformat(timespec="milliseconds"),
            "pid": os.getpid(),
            "call": self.call,
            "seconds": round(seconds, 4),
            "depth": self.level,
            "args": [cut(value) for value in self.args]
            + [f"{name}={cut(value)}" for name, value in self.kwargs.items()],
        }


def returned(result):
    return {"result": cut(result)}


def failed(error):
    return {"error": f"{type(error).__name__}: {error}"}


def cut(value):
    return repr(value)[:LOG_VALUE_CHARS]


class TraceLog:
    lock = threading.Lock()

    @classmethod
    def write(cls, entry):
        line = json.dumps(entry) + "\n"
        try:
            with cls.lock:
                log = Home.tinker_log()
                cls.rotate_when_big(log)
                cls.append(log, line)
        except OSError:
            pass

    @classmethod
    def append(cls, log, line):
        descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)

    @classmethod
    def rotate_when_big(cls, log):
        if log.exists() and log.stat().st_size >= LOG_ROTATE_BYTES:
            cls.rotate(log)

    @classmethod
    def rotate(cls, log):
        archive = Home.tinker_log_archive(cls.stamp())
        renamed = archive.with_suffix("")
        try:
            os.rename(log, renamed)
        except FileNotFoundError:
            return
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as packed:
            packed.write(renamed, renamed.name)
        renamed.unlink()

    @staticmethod
    def stamp():
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
