import atexit
import functools
import importlib
import inspect
import os
import socket
import sys
import threading
import time
import zipfile
from datetime import date, datetime
from types import SimpleNamespace

from tinkerkit.messages import say
from tinkerkit.settings import Home

LOG_ROTATE_BYTES = 20_000_000
SECRET_TABLES = {"channel_secrets"}
MASK = "***"


class Watched:
    def __init__(self, module, owner, method, sentence):
        self.module, self.owner, self.method, self.sentence = module, owner, method, sentence

    def classes(self):
        return [self.owner_class()]

    def owner_class(self):
        return getattr(importlib.import_module(self.module), self.owner)


class WatchedFamily(Watched):
    def __init__(self, module, owner, method, sentence, loads):
        super().__init__(module, owner, method, sentence)
        self.loads = loads

    def classes(self):
        for module in self.loads:
            importlib.import_module(module)
        return [cls for cls in descendants(self.owner_class()) if self.method in cls.__dict__]


class WatchedFunction(Watched):
    def __init__(self, module, function, sentence):
        super().__init__(module, module.rpartition(".")[2], function, sentence)

    def classes(self):
        return [importlib.import_module(self.module)]


def descendants(cls):
    found = []
    for child in cls.__subclasses__():
        found += [child] + descendants(child)
    return list(dict.fromkeys(found))


class Step:
    def __init__(self, subject, start, end):
        self.subject, self.start, self.end = subject, start, end

    def starts(self, arguments):
        return [self.start(arguments)]

    def ends(self, arguments, result):
        return [self.end(arguments, result)]

    def forget(self):
        pass


class Fact(Step):
    def __init__(self, subject, end):
        super().__init__(subject, None, end)

    def starts(self, arguments):
        return []


class ChangeFact(Fact):
    def __init__(self, subject, end):
        super().__init__(subject, end)
        self.last = {}

    def ends(self, arguments, result):
        subject, sentence = self.subject(arguments), self.end(arguments, result)
        if self.last.get(subject) == sentence:
            return []
        self.last[subject] = sentence
        return [sentence]

    def forget(self):
        self.last.clear()


def command_line(arguments):
    return f"tinker {arguments.self.args.command}"


def command_options(arguments):
    options = {name: value for name, value in vars(arguments.self.args).items() if name != "command"}
    return ", ".join(f"{name}={value!r}" for name, value in options.items())


def phase(arguments):
    return f"Phase {type(arguments.self).__name__}"


def asked(arguments):
    extra = "".join(f", {name}={value!r}" for name, value in arguments.kw.items())
    return f"label={arguments.label!r}, system={arguments.system!r}, parts={arguments.parts!r}{extra}"


def chatted(arguments):
    return (
        f"label={arguments.label!r}, messages={arguments.messages!r}, options={arguments.options!r}, "
        f"think={arguments.think!r}, keep_alive={arguments.keep_alive!r}"
    )


def stored(arguments):
    return f"{arguments.collection}/{arguments.key!r}"


def looked_up(arguments, result):
    if result is arguments.default:
        return f"Database get {stored(arguments)}: not found"
    return f"Database get {stored(arguments)}: found {shown(arguments, result)!r}"


def shown(arguments, value):
    if arguments.collection not in SECRET_TABLES:
        return value
    if isinstance(value, dict):
        return {key: MASK for key in value}
    return MASK


def host(arguments):
    return arguments.address[0]


def ip_version(sock):
    return "IPv6" if sock.family == socket.AF_INET6 else "IPv4"


def channel_kind(arguments):
    return type(arguments.channel).__name__


COMMAND = Step(
    command_line,
    lambda a: f"Running {command_line(a)} ({command_options(a)})",
    lambda a, result: f"{command_line(a)} finished",
)

WATCHED = (
    Watched("tinkerkit.cli", "Commands", "run", COMMAND),
    Watched("tinkerkit.cli", "ProjectRun", "execute", COMMAND),
    WatchedFamily(
        "tinkerkit.session",
        "Phase",
        "run",
        Step(
            phase,
            lambda a: f"{phase(a)} starts (cycle {a.cycle['id']})",
            lambda a, result: f"{phase(a)} ends",
        ),
        ("tinkerkit.planning", "tinkerkit.building", "tinkerkit.finishing"),
    ),
    Watched(
        "tinkerkit.model",
        "Model",
        "ask",
        Step(
            lambda a: a.self.name,
            lambda a: f"Asking {a.self.name} ({asked(a)})",
            lambda a, result: f"{a.self.name} answered: {result!r}",
        ),
    ),
    Watched(
        "tinkerkit.model",
        "Ollama",
        "chat",
        Step(
            lambda a: f"Ollama {a.name}",
            lambda a: f"Asking Ollama {a.name} ({chatted(a)})",
            lambda a, result: f"Ollama {a.name} answered: {result!r}",
        ),
    ),
    Watched(
        "tinkerkit.workspace",
        "Workspace",
        "run",
        Step(
            lambda a: f"Project command {a.command}",
            lambda a: f"Project command: {a.command}",
            lambda a, result: f"Project command {a.command} ended",
        ),
    ),
    Watched(
        "tinkerkit.workspace",
        "Workspace",
        "start",
        Step(
            lambda a: f"Project command {a.command}",
            lambda a: f"Starting project command: {a.command}",
            lambda a, result: f"Project command {a.command} started",
        ),
    ),
    Watched(
        "tinkerkit.web",
        "Internet",
        "search_all",
        Step(lambda a: "Search", lambda a: f"Searching {a.searches!r}", lambda a, result: "Search done"),
    ),
    Watched(
        "tinkerkit.web",
        "Internet",
        "fetch",
        Step(lambda a: "Fetch", lambda a: f"Fetching {a.urls!r}", lambda a, result: "Fetch done"),
    ),
    Watched("tinkerkit.store", "Store", "get", ChangeFact(lambda a: f"Database get {stored(a)}", looked_up)),
    Watched(
        "tinkerkit.store",
        "Store",
        "put",
        Fact(
            lambda a: f"Database put {stored(a)}",
            lambda a, result: f"Database put {stored(a)}: {shown(a, a.value)!r}",
        ),
    ),
    Watched(
        "tinkerkit.store",
        "Store",
        "append",
        Fact(lambda a: f"Database append {a.log}", lambda a, result: f"Database append {a.log}: {a.entry!r}"),
    ),
    Watched(
        "tinkerkit.model",
        "Ollama",
        "loading",
        Step(
            lambda a: f"Model {a.name}",
            lambda a: f"Loading model {a.name}",
            lambda a, result: f"Model {a.name} loaded",
        ),
    ),
    Watched(
        "tinkerkit.conversation",
        "Talk",
        "warm",
        Step(
            lambda a: "Chat model loading",
            lambda a: "Loading the chat model",
            lambda a, result: "Chat model loading started",
        ),
    ),
    WatchedFunction(
        "tinkerkit.channels",
        "connect_by_address",
        Step(
            lambda a: f"Connection to {host(a)}",
            lambda a: f"Connecting to {host(a)}",
            lambda a, result: f"Connected to {host(a)} at {result.getpeername()[0]} by {ip_version(result)}",
        ),
    ),
    Watched(
        "tinkerkit.conversation",
        "Desk",
        "run",
        Step(
            lambda a: "Conversation", lambda a: "Conversation starts", lambda a, result: "Conversation ends"
        ),
    ),
    Watched(
        "tinkerkit.conversation",
        "Desk",
        "handle",
        Step(
            lambda a: f"{channel_kind(a)} message",
            lambda a: f"Handling a {channel_kind(a)} message: {a.message.text!r}",
            lambda a, result: f"{channel_kind(a)} message handled",
        ),
    ),
    Watched(
        "tinkerkit.channels",
        "TelegramChannel",
        "send",
        Step(
            lambda a: f"Telegram chat {a.chat}",
            lambda a: f"Sending to Telegram chat {a.chat}: {a.text!r}",
            lambda a, result: f"Telegram chat {a.chat} got message {result!r}",
        ),
    ),
    Watched(
        "tinkerkit.conversation",
        "Desk",
        "close_channels",
        Step(lambda a: "Channels", lambda a: "Closing the channels", lambda a, result: "Channels closed"),
    ),
    Watched(
        "tinkerkit.conversation",
        "Desk",
        "unload_models",
        Step(lambda a: "Models", lambda a: "Unloading the models", lambda a, result: "Models unloaded"),
    ),
)

originals = {}


def install():
    if said in say.followers:
        return
    note(f"Tinker is starting: tinker {' '.join(sys.argv[1:])}")
    for watched in WATCHED:
        for cls in watched.classes():
            wrap(cls, watched.method, watched.sentence)
    say.follow(said)
    atexit.register(stopped)


def uninstall():
    for (cls, method), original in originals.items():
        setattr(cls, method, original)
    originals.clear()
    for watched in WATCHED:
        watched.sentence.forget()
    if said in say.followers:
        say.followers.remove(said)
    atexit.unregister(stopped)


def said(message):
    note(f"Said: {message.text}")


def stopped():
    note("Tinker stopped")


def note(sentence):
    TraceLog.write(prefixed(depth.level, sentence))


def wrap(cls, method, sentence):
    if (cls, method) in originals:
        return
    original = cls.__dict__[method]
    originals[cls, method] = original
    setattr(cls, method, wrapped(original, sentence))


def wrapped(original, sentence):
    if isinstance(original, staticmethod):
        return staticmethod(watcher(original.__func__, sentence))
    return watcher(original, sentence)


class Depth(threading.local):
    level = 0


depth = Depth()


def watcher(function, sentence):
    signature = inspect.signature(function)

    @functools.wraps(function)
    def watch(*args, **kwargs):
        started = Call(sentence, lambda: bound(signature, args, kwargs), depth.level)
        depth.level += 1
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            started.fail(error)
            raise
        finally:
            depth.level -= 1
        started.end(result)
        return result

    return watch


def bound(signature, args, kwargs):
    arguments = signature.bind(*args, **kwargs)
    arguments.apply_defaults()
    return SimpleNamespace(**arguments.arguments)


class Call:
    def __init__(self, sentence, binding, level):
        self.sentence, self.binding, self.level = sentence, binding, level
        self.clock = time.perf_counter()
        self.write(lambda: self.sentence.starts(self.arguments))

    @functools.cached_property
    def arguments(self):
        return self.binding()

    def end(self, result):
        seconds = time.perf_counter() - self.clock
        self.write(
            lambda: [
                f"{sentence} ({seconds:.3f}s)" for sentence in self.sentence.ends(self.arguments, result)
            ]
        )

    def fail(self, error):
        seconds = time.perf_counter() - self.clock
        self.write(
            lambda: [
                f"{self.sentence.subject(self.arguments)} failed: {type(error).__name__}: {error} ({seconds:.3f}s)"
            ]
        )

    def write(self, sentences):
        try:
            for sentence in sentences():
                TraceLog.write(prefixed(self.level, sentence))
        except Exception:
            pass


def prefixed(level, sentence):
    clock = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    return f"{clock} [{os.getpid()}] {'  ' * level}{sentence}".replace("\n", "\\n")


class TraceLog:
    lock = threading.Lock()

    @classmethod
    def write(cls, text):
        try:
            with cls.lock:
                log = Home.tinker_log()
                cls.rotate_when_big(log)
                cls.append(log, cls.day_line(log) + text + "\n")
        except OSError:
            pass

    @classmethod
    def day_line(cls, log):
        today = date.today()
        if log.exists() and date.fromtimestamp(log.stat().st_mtime) == today:
            return ""
        return f"── {today.isoformat()} ──\n"

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
