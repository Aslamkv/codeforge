"""Channels: talking to Tinker from a chat app, a plugin like the web. With no channel enabled nothing of
this runs. A channel is enabled with tinker channel enable NAME; then a task it is running reports its
progress there and takes `status` and `stop` until it ends, and tinker channel start listens on its own:
a task, a resume, the runs, their status, all asked for from the chat. Anything else is a normal chat with
the model in use (Talk).

Channel is what every kind of channel does (receive messages, send, edit what it sent); TelegramChannel is
one kind. Only the chats a channel was paired with are listened to: a message from anyone else is ignored.
Bot tokens are kept apart from the settings, in a file only its owner can read."""
import _thread
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .model import Models, Ollama, OllamaError
from .settings import HOME, Config

CHANNELS_FILE = HOME / "channels.json"         # name -> its kind, whether it is enabled, the chats it serves
SECRETS_FILE = HOME / "channel-secrets.json"   # name -> its secrets (a bot token): mode 600, never printed
OFF = "TINKER_CHANNELS"                        # =off: a run started by `tinker channel start` does not listen too
PAIRING_WAIT = 120                             # seconds to wait for the first message when pairing a chat
CHECK_TRIES = 3                                # tries of a channel's first call when it starts listening
EDIT_EVERY = 5                                 # seconds between edits of a progress message
CHAT_KEPT = 12                                 # messages of one chat kept as the context of its next reply
CHAT_REPLY_TOKENS = 1024                       # the longest chat reply
CHAT_TIMEOUT = 600                             # seconds a chat reply may take (a task may hold the model first)
TYPING_EVERY = 4                               # seconds between "typing…" signs while a reply is written


class Message:
    """One message to Tinker: who sent it (the chat to answer) and what it says."""

    def __init__(self, chat, text, sender=""):
        self.chat, self.text, self.sender = str(chat), (text or "").strip(), sender


class Channel:
    """What every kind of channel does. KIND names it; SECRETS are the settings asked for (and kept)
    apart, never shown. A dropped connection is not an error: receive returns nothing and tries again."""
    KIND = ""
    SECRETS = ()     # [(key, what to ask)]

    def __init__(self, name, settings, secrets):
        self.name, self.settings, self.secrets = name, settings, secrets
        self.allowed = {str(chat) for chat in settings.get("allowed", [])}

    def who(self):
        """What the channel is called where it lives (a bot's @name); raises ChannelError when its
        secrets do not work."""
        raise NotImplementedError

    def receive(self, timeout):
        """The new messages, waiting up to timeout seconds for one: [Message]."""
        raise NotImplementedError

    def send(self, chat, text):
        """Send text to chat; what edit needs to change it later (or None)."""
        raise NotImplementedError

    def edit(self, chat, sent, text):
        """Change a message sent before (a channel that cannot edit sends a new one)."""
        return self.send(chat, text)

    def typing(self, chat):
        """Show chat that a reply is being written (a channel that cannot says nothing)."""

    def skip_waiting(self):
        """Forget the messages sent while nothing listened: a command sent hours ago is not run now."""

    def listened(self, timeout):
        """The new messages from the chats this channel serves; the rest are ignored."""
        return [m for m in self.receive(timeout) if m.chat in self.allowed]


class ChannelError(Exception):
    """A channel that cannot work as set up (a refused token, a refused chat)."""


class TelegramChannel(Channel):
    """A Telegram bot (made with @BotFather): its token is the one secret."""
    KIND = "telegram"
    SECRETS = (("token", "Bot token (from @BotFather)"),)
    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, name, settings, secrets):
        super().__init__(name, settings, secrets)
        self.offset = None

    def call(self, method, wait=30, **fields):
        """The Bot API's result (waiting up to wait seconds); a refused call is a ChannelError, a dropped
        connection an OSError."""
        url = self.API.format(token=self.secrets.get("token", ""), method=method)
        data = urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=wait) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise ChannelError(f"Telegram refused {method}: {exc.code} {first_words(detail)}") from None
        if not reply.get("ok"):
            raise ChannelError(f"Telegram refused {method}: {first_words(json.dumps(reply))}")
        return reply.get("result")

    def who(self):
        return "@" + self.call("getMe")["username"]

    def receive(self, timeout):
        try:
            updates = self.call("getUpdates", wait=timeout + 10, offset=self.offset, timeout=int(timeout))
        except OSError:  # the network is gone for now: the messages wait at Telegram
            time.sleep(min(timeout, 10))
            return []
        messages = []
        for update in updates:
            self.offset = update["update_id"] + 1
            message = update.get("message") or {}
            if message.get("text"):
                person = message.get("from") or {}
                sender = person.get("username") or person.get("first_name", "")
                messages.append(Message(message["chat"]["id"], message["text"], sender))
        return messages

    def skip_waiting(self):
        try:
            waiting = self.call("getUpdates", offset=-1)
            self.offset = waiting[-1]["update_id"] + 1 if waiting else None
        except (OSError, ChannelError):
            pass

    def typing(self, chat):
        try:
            self.call("sendChatAction", wait=10, chat_id=chat, action="typing")
        except (OSError, ChannelError):
            pass

    def send(self, chat, text):
        try:
            return self.call("sendMessage", chat_id=chat, text=text[:4000])["message_id"]
        except OSError:
            return None

    def edit(self, chat, sent, text):
        if sent is None:
            return self.send(chat, text)
        try:
            self.call("editMessageText", chat_id=chat, message_id=sent, text=text[:4000])
        except ChannelError as exc:  # unchanged text is refused, harmlessly
            if "not modified" not in str(exc):
                return self.send(chat, text)
        except OSError:
            pass
        return sent


KINDS = {kind.KIND: kind for kind in (TelegramChannel,)}


def first_words(text, size=160):
    return " ".join(str(text).split())[:size]


class Channels:
    """The channels Tinker has: their settings (kind, enabled, the chats they serve) in channels.json,
    their secrets apart in channel-secrets.json (mode 600)."""

    @staticmethod
    def settings():
        return Channels.read(CHANNELS_FILE)

    @staticmethod
    def read(path):
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise SystemExit(f"tinker: {path} cannot be read ({exc}): fix or delete it") from None
        return data if isinstance(data, dict) else {}

    @staticmethod
    def write(settings=None, secrets=None):
        HOME.mkdir(parents=True, exist_ok=True)
        if settings is not None:
            CHANNELS_FILE.write_text(json.dumps(settings, indent=2) + "\n")
        if secrets is not None:
            SECRETS_FILE.touch(mode=0o600, exist_ok=True)
            os.chmod(SECRETS_FILE, 0o600)
            SECRETS_FILE.write_text(json.dumps(secrets, indent=2) + "\n")

    @staticmethod
    def make(name):
        settings = Channels.settings().get(name)
        if settings is None:
            raise SystemExit(f"tinker: no channel {name!r} (tinker channel list shows them)")
        kind = KINDS.get(settings.get("kind"))
        if kind is None:
            raise SystemExit(f"tinker: channel {name!r} is of an unknown kind {settings.get('kind')!r}")
        return kind(name, settings, Channels.read(SECRETS_FILE).get(name, {}))

    @staticmethod
    def enabled():
        """The enabled channels, made; none when a run is told not to listen (tinker channel start's own)."""
        if os.environ.get(OFF) == "off":
            return []
        return [Channels.make(name) for name, s in Channels.settings().items() if s.get("enabled")]


class Progress:
    """What a run prints, kept short for a chat: one message kept up to date (the phase, how many spec
    points pass, the latest step) and, when the run ends, its summary."""
    PASS_RE = re.compile(r"tests: (\d+)/(\d+) criteria pass|^\s*C\d+ passes \((\d+)/(\d+) pass\)")
    END_RE = re.compile(r"^(Done|Stopped|Not done|Interrupted|Run \w+ is not finished)")
    RULE = "=" * 20

    def __init__(self, title):
        self.title, self.phase, self.passing, self.latest = title, "starting", "", ""
        self.summary, self.in_summary, self.ended = [], False, False

    def read(self, line):
        """Take one printed line; True when it changed what the progress message says."""
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("..."):
            return False
        if line.startswith(self.RULE):
            self.in_summary, self.summary = True, []
            return False
        if self.in_summary:
            self.summary.append(line)
            self.ended = self.ended or bool(self.END_RE.match(line))
            return False
        counts = self.PASS_RE.search(line)
        if counts:
            numbers = [n for n in counts.groups() if n]
            self.passing = f"{numbers[0]}/{numbers[1]} spec points pass"
        elif not line.startswith((" ", "[")) and len(line) < 80:
            self.phase = line  # a phase prints its title alone on its line
        else:
            self.latest = first_words(line.split("] ", 1)[-1], 120)
        return True

    def text(self):
        lines = [f"🔨 {self.title}", f"▸ {self.phase}"]
        lines += [f"✅ {self.passing}"] if self.passing else []
        lines += [f"… {self.latest}"] if self.latest else []
        return "\n".join(lines + [f"updated {time.strftime('%H:%M:%S')}"])

    def ending(self):
        return "\n".join([f"🏁 {self.title}"] + [line for line in self.summary if line.strip()][:20])


class Reporter:
    """Progress shown on the channels: one message per chat, edited at most every EDIT_EVERY seconds,
    and the summary sent once when the run ends."""

    def __init__(self, channels, title):
        self.channels, self.progress = channels, Progress(title)
        self.sent, self.last_edit, self.lock = {}, 0.0, threading.Lock()

    def line(self, text):
        with self.lock:
            if self.progress.read(text) and time.monotonic() - self.last_edit >= EDIT_EVERY:
                self.update()

    def update(self):
        self.last_edit = time.monotonic()
        for channel in self.channels:
            for chat in channel.allowed:
                key = (channel.name, chat)
                self.sent[key] = channel.edit(chat, self.sent.get(key), self.progress.text())

    def finish(self):
        with self.lock:
            self.update()
            if self.progress.summary:
                for channel in self.channels:
                    for chat in channel.allowed:
                        channel.send(chat, self.progress.ending())


class LineWatcher:
    """sys.stdout passed on as it is, each whole line also given to see(line)."""

    def __init__(self, stream, see):
        self.stream, self.see, self.partial = stream, see, ""

    def write(self, text):
        self.stream.write(text)
        self.partial += text.replace("\r", "\n")
        *lines, self.partial = self.partial.split("\n")
        for line in lines:
            try:
                self.see(line)
            except Exception:  # a channel must never break the run it reports on
                pass
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


HELP_ATTACHED = ("This task is running. Ask:\n• status: how it is going\n• stop: stop it (tinker resume goes on later)\n"
                 "• anything else: a normal chat")
HELP_ALONE = ("Ask Tinker:\n"
              "• task FOLDER \"what to build\": build it in a new folder\n"
              "• resume ID: continue a run\n"
              "• runs: every run\n"
              "• status [ID]: a run's spec points\n"
              "• stop: stop the task that is running\n"
              "• help: this list\n"
              "• anything else: a normal chat")


class Talk:
    """A normal chat with the model in use: a message that is not a command is answered by it, with that
    chat's last CHAT_KEPT messages (and what is running now) as context. It does not think, so a reply comes
    quickly, and its settings are a run's own (num_ctx), so a task and a chat share the loaded model: warm
    loads it as listening starts, and neither the first reply nor the next task waits for it to load."""
    SYSTEM = ("You are Tinker, a friendly helper that builds small programs on this computer with a local model. "
              "You are chatting over {kind}: answer briefly, in plain text, without markdown. Talk about "
              "anything. You cannot run anything yourself from the chat; these messages do:\n{commands}")

    def __init__(self, commands):
        self.commands, self.said, self.lock = commands, {}, threading.Lock()

    @staticmethod
    def model():
        """(name, options, think) for a chat with the model in use; None when no measured model is in use."""
        name = Config.get("model")
        settings = (Models.settings(name) or {}) if name else {}
        if not settings.get("num_ctx"):
            return None
        options = {"num_ctx": settings["num_ctx"], "num_predict": CHAT_REPLY_TOKENS}
        if settings.get("num_batch"):
            options["num_batch"] = settings["num_batch"]
        return name, options, (False if settings.get("think") not in (None, False) else None)

    def warm(self):
        """Load the model now, in the background."""
        threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        """The model loaded, and one token written (a loaded model's first reply is much slower than the next)."""
        found = self.model()
        if found:
            name, options, think = found
            try:
                Ollama.chat(name, [{"role": "user", "content": "hi"}], Config.keep_alive(Config.get("keep_alive")),
                            dict(options, num_predict=1), think=think, timeout=CHAT_TIMEOUT)
            except OllamaError as exc:
                print(f"  note: the model did not load for chat: {first_words(exc)}")

    def answer(self, channel, message, now=""):
        """The reply to message, sent when written (in a thread, so `stop` is heard meanwhile)."""
        threading.Thread(target=lambda: channel.send(message.chat, self.reply(channel, message, now)),
                         daemon=True).start()

    def reply(self, channel, message, now=""):
        found = self.model()
        if not found:
            return ("No model is in use yet (tinker calibrate NAME, then tinker use-model NAME), so I cannot "
                    f"chat. These still work:\n{self.commands}")
        name, options, think = found
        with self.lock:
            said = self.said.setdefault((channel.name, message.chat), [])
            said.append({"role": "user", "content": message.text})
            asked = said[-CHAT_KEPT:]
        system = self.SYSTEM.format(kind=channel.KIND.title(), commands=self.commands)
        system += f"\n\nRunning now:\n{now}" if now else "\n\nNo task is running now."
        done = threading.Event()
        threading.Thread(target=keep_typing, args=(channel, message.chat, done), daemon=True).start()
        try:
            data = Ollama.chat(name, [{"role": "system", "content": system}] + asked,
                               Config.keep_alive(Config.get("keep_alive")), options, think=think,
                               timeout=CHAT_TIMEOUT)
        except OllamaError as exc:
            return f"The model did not answer: {first_words(exc)}"
        finally:
            done.set()
        text = data["message"]["content"].strip() or "(no answer)"
        with self.lock:
            said.append({"role": "assistant", "content": text})
            del said[:-CHAT_KEPT]
        return text


def keep_typing(channel, chat, done):
    """The chat's "typing…" sign, shown until done is set."""
    while not done.is_set():
        channel.typing(chat)
        done.wait(TYPING_EVERY)


def command_of(text, commands):
    """The command a message gives (one of commands, sent as its first word, with or without a /), or ""
    when it is chat. A word alone is a command; with more after it only task, resume ID and status ID are."""
    words = text.split()
    first = words[0].lower().lstrip("/") if words else ""
    if first not in commands:
        return ""
    if len(words) == 1 or first == "task" or (first in ("resume", "status") and len(words) == 2):
        return first
    return ""


class Attached:
    """Channels listening while a task runs (in a thread): `status` answers with its progress, `stop`
    stops it as Ctrl-C would (saved, so tinker resume goes on). Ends with the run."""

    def __init__(self, channels, title):
        self.channels, self.reporter = channels, Reporter(channels, title)
        self.talk, self.done = Talk(HELP_ATTACHED), threading.Event()
        self.threads = [threading.Thread(target=self.listen, args=(c,), daemon=True) for c in channels]

    def start(self):
        sys.stdout = LineWatcher(sys.stdout, self.reporter.line)
        for channel in self.channels:
            channel.skip_waiting()
        for thread in self.threads:
            thread.start()
        self.reporter.update()
        return self

    def listen(self, channel):
        while not self.done.is_set():
            for message in channel.listened(timeout=20):
                command = command_of(message.text, ("stop", "status", "help"))
                if command == "stop":
                    channel.send(message.chat, "Stopping: the run is saved; tinker resume goes on later.")
                    _thread.interrupt_main()
                elif command == "status":
                    channel.send(message.chat, self.reporter.progress.text())
                elif command == "help":
                    channel.send(message.chat, HELP_ATTACHED)
                else:
                    self.talk.answer(channel, message, self.reporter.progress.text())

    def end(self):
        self.done.set()
        if isinstance(sys.stdout, LineWatcher):
            sys.stdout.flush()
            sys.stdout = sys.stdout.stream
        self.reporter.finish()


def attach(title):
    """The enabled channels listening to the run about to start (None when there are none). A channel
    that does not work is said once and left out: a run never fails for its channel."""
    try:
        channels = Channels.enabled()
    except SystemExit as exc:
        print(f"  note: channels left out: {exc}")
        return None
    return Attached(channels, title).start() if channels else None


class Alone:
    """tinker channel start: the channels listen on their own, from this folder. One task runs at a
    time, as its own tinker (which does not listen too); its printed progress goes to the chat. Anything
    that is not a command is a normal chat."""
    FOLDER_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")
    COMMANDS = ("task", "resume", "runs", "status", "stop", "help")

    def __init__(self, channels, root):
        self.channels, self.root = channels, Path(root).resolve()
        self.process = self.reporter = None
        self.lock, self.talk = threading.Lock(), Talk(HELP_ALONE)

    def run(self):
        self.talk.warm()
        for channel in self.channels:
            channel.skip_waiting()
            for chat in channel.allowed:
                channel.send(chat, f"👋 Tinker is listening (projects in {self.root}).\n{HELP_ALONE}")
        names = ", ".join(c.name for c in self.channels)
        print(f"Listening on {names}; projects go in {self.root}. Ctrl-C stops.")
        threads = [threading.Thread(target=self.listen, args=(c,), daemon=True) for c in self.channels]
        for thread in threads:
            thread.start()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop_task()
            for channel in self.channels:
                for chat in channel.allowed:
                    channel.send(chat, "Tinker stopped listening.")
            print("\nStopped listening.")

    def listen(self, channel):
        while True:
            for message in channel.listened(timeout=20):
                print(f"[{time.strftime('%H:%M:%S')}] {channel.name}: {message.text}")
                if not command_of(message.text, self.COMMANDS):
                    self.talk.answer(channel, message, self.reporter.progress.text() if self.busy() else "")
                    continue
                reply = self.answer(message.text, channel)
                if reply:
                    channel.send(message.chat, reply)

    def answer(self, text, channel):
        """What a command asks for, done; the reply (None when the reply comes as progress)."""
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        command = command_of(text, self.COMMANDS)
        if command == "task":
            if len(words) < 3:
                return 'Write: task FOLDER "what to build", e.g. task calc "a calculator in Python"'
            folder, request = words[1], " ".join(words[2:])
            if not self.FOLDER_RE.fullmatch(folder):
                return f"{folder!r} is not a folder name: letters, digits, - _ and ., starting with a letter or digit"
            if (self.root / folder).exists():
                return f"{folder} already exists in {self.root}: pick a new folder, or resume its run"
            (self.root / folder).mkdir(parents=True)
            return self.start_task(["task", request], self.root / folder, f"{folder}: {request}")
        if command == "resume":
            if len(words) != 2:
                return "Write: resume ID (runs lists the IDs)"
            return self.start_task(["resume", words[1]], self.root, f"resume {words[1]}")
        if command == "stop":
            if not self.stop_task():
                return "No task is running."
            return "Stopping the task: it is saved, resume goes on later."
        if command in ("runs", "status"):
            if command == "status" and len(words) == 1 and self.busy():
                return self.reporter.progress.text()
            return self.tinker([command] + words[1:2])
        return HELP_ALONE

    def busy(self):
        return self.process is not None and self.process.poll() is None

    def start_task(self, arguments, folder, title):
        with self.lock:
            if self.busy():
                return "A task is running: wait for it, or stop it first."
            self.reporter = Reporter(self.channels, title)
            env = dict(os.environ, **{OFF: "off", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
            self.process = subprocess.Popen(tinker_command() + arguments, cwd=folder, env=env, text=True,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=self.follow, args=(self.process, self.reporter), daemon=True).start()
        self.reporter.update()
        return None

    def follow(self, process, reporter):
        for line in process.stdout:
            sys.stdout.write(line)
            reporter.line(line)
        process.wait()
        reporter.finish()

    def stop_task(self):
        if not self.busy():
            return False
        os.killpg(self.process.pid, signal.SIGINT)  # as Ctrl-C: the run saves itself
        return True

    def tinker(self, arguments):
        done = subprocess.run(tinker_command() + arguments, cwd=self.root, text=True, capture_output=True,
                              env=dict(os.environ, NO_COLOR="1"), timeout=120)
        return first_lines((done.stdout + done.stderr).strip() or "(nothing)", 40)


def first_lines(text, count):
    lines = text.splitlines()
    return "\n".join(lines[:count] + ([f"… {len(lines) - count} more lines"] if len(lines) > count else []))


def tinker_command():
    """How to start Tinker again: the script this one is."""
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


class ChannelCommand:
    """tinker channel list | add | remove NAME | enable NAME | disable NAME | start [NAME]."""
    USAGE = ("tinker channel list                 the channels, and whether each is enabled\n"
             "tinker channel add                  add one (asks its kind, a name, its secrets, and pairs your chat)\n"
             "tinker channel remove NAME          delete it, secrets included\n"
             "tinker channel enable NAME          a running task reports to it and takes status and stop\n"
             "tinker channel disable NAME         stop using it (new channels start disabled)\n"
             "tinker channel start [NAME]         listen on enabled channels (or NAME): ask for tasks, or just chat")

    @staticmethod
    def run(argv):
        action, rest = (argv[0], argv[1:]) if argv else ("list", [])
        actions = {"list": ChannelCommand.list, "add": ChannelCommand.add, "remove": ChannelCommand.remove,
                   "enable": ChannelCommand.enable, "disable": ChannelCommand.disable, "start": ChannelCommand.start}
        if action in ("help", "-h", "--help"):
            print(ChannelCommand.USAGE)
            return
        if action not in actions:
            raise SystemExit(f"tinker channel: no action {action!r}\n{ChannelCommand.USAGE}")
        actions[action](rest)

    @staticmethod
    def named(rest, action):
        if len(rest) != 1:
            raise SystemExit(f"tinker channel {action} NAME")
        if rest[0] not in Channels.settings():
            raise SystemExit(f"tinker: no channel {rest[0]!r} (tinker channel list shows them)")
        return rest[0]

    @staticmethod
    def list(rest):
        settings = Channels.settings()
        if not settings:
            print(f"No channels yet. Add one: tinker channel add  (kinds: {', '.join(KINDS)})")
            return
        for name, s in settings.items():
            state = "enabled" if s.get("enabled") else "disabled"
            chats = ", ".join(map(str, s.get("allowed", []))) or "no chat paired"
            print(f"  {name:<16} {s.get('kind', '?'):<10} {state:<9} chats: {chats}")

    @staticmethod
    def add(rest):
        """Asks the kind, a name and the kind's secrets; checks them; pairs the chat(s) it will serve.
        Each answer can come as an option instead: --kind, --name, --token (any secret), --chat."""
        given = ChannelCommand.options(rest)
        kinds = list(KINDS)
        kind = given.get("kind") or ask("Channel kind", kinds[0] if len(kinds) == 1 else "", choices=kinds)
        if kind not in KINDS:
            raise SystemExit(f"tinker: no channel kind {kind!r} (supported: {', '.join(KINDS)})")
        settings = Channels.settings()
        name = given.get("name") or ask("Channel name", kind)
        if not re.fullmatch(r"[A-Za-z0-9][\w-]{0,31}", name):
            raise SystemExit("tinker: a channel name is letters, digits, - and _ (up to 32)")
        if name in settings:
            raise SystemExit(f"tinker: a channel {name!r} exists (tinker channel remove {name} first)")
        secrets = {key: given.get(key) or ask(what, secret=True) for key, what in KINDS[kind].SECRETS}
        channel = KINDS[kind](name, {}, secrets)
        try:
            who = channel.who()
        except (ChannelError, OSError) as exc:
            raise SystemExit(f"tinker: the {kind} channel does not work with that: {exc}") from None
        chats = ([c.strip() for c in given["chat"].split(",")] if given.get("chat")
                 else ChannelCommand.pair(channel, who))
        settings[name] = {"kind": kind, "enabled": False, "allowed": chats, "who": who}
        all_secrets = Channels.read(SECRETS_FILE)
        all_secrets[name] = secrets
        Channels.write(settings, all_secrets)
        print(f"Added {name} ({kind}, {who}), serving chat(s) {', '.join(chats)}. "
              f"It starts disabled: tinker channel enable {name}")

    @staticmethod
    def pair(channel, who):
        """The chat that sends the channel a message now, once its sender confirms it here."""
        channel.skip_waiting()
        print(f"Send any message to {who} now (waiting up to {PAIRING_WAIT}s)…")
        ends = time.monotonic() + PAIRING_WAIT
        while time.monotonic() < ends:
            for message in channel.receive(timeout=10):
                answer = ask(f"Message from {message.sender or 'someone'} (chat {message.chat}): "
                             f"{first_words(message.text, 40)!r}. Is that you? [y/N]", "n")
                if answer.lower().startswith("y"):
                    channel.send(message.chat, "✅ Paired with Tinker. Only this chat is listened to.")
                    return [message.chat]
        raise SystemExit("tinker: no message came; run tinker channel add again (or give --chat ID)")

    @staticmethod
    def options(rest):
        found, items = {}, list(rest)
        while items:
            item = items.pop(0)
            if not item.startswith("--") or not items:
                raise SystemExit(f"tinker channel add: unexpected {item!r} (options: --kind --name --token --chat)")
            found[item[2:]] = items.pop(0)
        return found

    @staticmethod
    def remove(rest):
        name = ChannelCommand.named(rest, "remove")
        settings, secrets = Channels.settings(), Channels.read(SECRETS_FILE)
        settings.pop(name, None)
        secrets.pop(name, None)
        Channels.write(settings, secrets)
        print(f"Removed {name}.")

    @staticmethod
    def enable(rest, on=True):
        name = ChannelCommand.named(rest, "enable" if on else "disable")
        settings = Channels.settings()
        settings[name]["enabled"] = on
        Channels.write(settings)
        print(f"{name} is {'enabled: tasks report to it; tinker channel start listens on it' if on else 'disabled'}.")

    @staticmethod
    def disable(rest):
        ChannelCommand.enable(rest, on=False)

    @staticmethod
    def start(rest):
        if rest:
            channels = [Channels.make(ChannelCommand.named(rest, "start"))]
        else:
            channels = Channels.enabled()
        if not channels:
            raise SystemExit("tinker: no channel is enabled (tinker channel enable NAME, or start NAME)")
        for channel in channels:
            ChannelCommand.check(channel)
        Alone(channels, Path.cwd()).run()

    @staticmethod
    def check(channel):
        """The channel answers: a refusal (a bad token) ends here, a slow or dropped network is asked
        again, up to CHECK_TRIES times."""
        for tries in range(1, CHECK_TRIES + 1):
            try:
                return channel.who()
            except ChannelError as exc:
                raise SystemExit(f"tinker: channel {channel.name} does not work: {exc}") from None
            except OSError as exc:
                if tries == CHECK_TRIES:
                    raise SystemExit(f"tinker: channel {channel.name} cannot be reached ({exc}), tried {tries} "
                                     f"times: check the network and start it again") from None
                print(f"  {channel.name} did not answer ({exc}); trying again ({tries + 1}/{CHECK_TRIES})")
                time.sleep(5)


def ask(question, default="", choices=None, secret=False):
    """One answer from the person at the terminal (default when they just press Enter)."""
    shown = f"{question}" + (f" ({'/'.join(choices)})" if choices else "") + (f" [{default}]" if default else "")
    if not sys.stdin.isatty():
        if default:
            return default
        raise SystemExit(f"tinker channel: {question} is needed (run it in a terminal, or give it as an option)")
    if secret:
        import getpass
        answer = getpass.getpass(f"{shown}: ")
    else:
        answer = input(f"{shown}: ")
    answer = answer.strip() or default
    if not answer:
        raise SystemExit(f"tinker channel: {question} is needed")
    return answer
