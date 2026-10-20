"""Channels: talking to Tinker from a chat app, a plugin like the web. With no channel enabled nothing of
this runs. A channel is enabled with tinker channel enable NAME; then every task reports its progress there,
and tinker channel start reads the chat: it is the only one that does (Telegram lets one program read a
bot). From the chat: a task (its spec comes to the chat first, to confirm or amend before anything is built),
a resume, the runs, their status, stop; anything else is a normal chat with the
model in use (Talk), told at once when a task holds the model; `command WHAT` has the model name the command
line that does it, run once the person confirms it (Shell); and `emergency MESSAGE` stops the task
(saved), answers at once and then goes on with the task. A task run from a terminal is served the same way:
its progress is kept in RUNNING_DIR, and it is stopped or paused with a signal.

Channel is what every kind of channel does (receive messages, send, edit what it sent); TelegramChannel is
one kind. Every message is written in Markup (bold, italic, code ...), each mark for what it means, and each
kind shows it its own way. Only the chats a channel was paired with are listened to: a message from anyone else is ignored.
Bot tokens are kept apart from the settings, in a file only its owner can read."""
import fcntl
import html
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
RUNNING_DIR = HOME / "running"                 # <process>.json: what each run reporting to the channels does now
OFF = "TINKER_CHANNELS"                        # =off: a run started by `tinker channel start` does not report too
PAIRING_WAIT = 120                             # seconds to wait for the first message when pairing a chat
CHECK_TRIES = 3                                # tries of a channel's first call when it starts listening
EDIT_EVERY = 5                                 # seconds between edits of a progress message
CHAT_KEPT = 12                                 # messages of one chat kept as the context of its next reply
CHAT_REPLY_TOKENS = 1024                       # the longest chat reply
CHAT_BUSY_TOKENS = 300                         # ... while a task runs, so the task waits as little as it can
STOP_WAIT = 120                                # seconds a task is given to stop (and save) for an emergency
PAUSE = signal.SIGUSR1                         # to a task run from a terminal: stop for an emergency, then go on
STOP = signal.SIGUSR2                          # ... stop, as Ctrl-C (which a task started in the background ignores)
RESUME_WAIT = 600                              # seconds such a task waits for the answer before it goes on anyway
CHAT_TIMEOUT = 600                             # seconds a chat reply may take (a task may hold the model first)
TYPING_EVERY = 4                               # seconds between "typing…" signs while a reply is written
BUSY_AFTER = 5                                 # seconds a chat reply may take before a note says the model is busy
BUSY_EDIT_EVERY = 30                           # seconds between updates of that note (the progress of what runs)
COMMAND_TOKENS = 300                           # the longest reply naming a command
COMMAND_TIMEOUT = 120                          # seconds a command run from the chat may take
COMMAND_SHOWN = 3000                           # chars of its output sent back, folded (its end, when longer)
COMMAND_READ = 6000                            # ... the model reads to tell what it says


class Markup:
    """The marks a chat message is written with, as in Markdown, each for what it means: **bold** for what
    matters most, *italic* (or _italic_) for an aside, ~~struck~~ for what is gone, `code` for what is typed
    or named exactly (a command, a path), ```a block``` of code, and ```output for a command's output (folded
    where the chat can fold it); "- " starts an item. A mark meant as itself is escaped (literal)."""
    FENCE_RE = re.compile(r"```(\w*)\n?(.*?)(?:\n?```|\Z)", re.S)
    CODE_RE = re.compile(r"`([^`\n]+)`")
    ESCAPED_RE = re.compile(r"\\([*_`~\\])")
    MARKS = ((re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "b"), (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), "s"),
             (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"), "i"),
             (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "i"))
    ITEM_RE = re.compile(r"(?m)^\s*[-*] (?=\S)")
    HEADING_RE = re.compile(r"(?m)^#{1,6} +(.+)$")

    @staticmethod
    def literal(text):
        """text shown as it is: its marks escaped."""
        return re.sub(r"([*_`~\\])", r"\\\1", str(text))

    @staticmethod
    def columns(text):
        """Text in columns, as a terminal lines them up with spaces and tabs, as a chat reads it best: each
        line's columns joined by " | ". A column ends where the lines are blank at the same place (a short
        line counts as blank past its end; of 4 lines or more, one value may run into the gap, and its cut
        moves to the space after it). The line of fewest words (of three or more) shapes them: its last word
        starts the last column, which runs to the end of each line (spaces and all), and a gap with none of
        its words on one side is inside a column (a date and its time). Text not in columns stays as it is."""
        lines = str(text).expandtabs().splitlines()
        rows = [line for line in lines if len(line.split()) >= 3]
        if len(rows) < 2:
            return str(text)
        shape = min(rows, key=lambda line: len(line.split()))
        last = list(re.finditer(r"\S+", shape))[-1].start()
        tolerated = 1 if len(rows) >= 4 else 0
        blank = [sum(at < len(line) and line[at] != " " for line in rows) <= tolerated for at in range(last)]
        gaps = [at for at in range(1, last) if blank[at] and not blank[at - 1]] + [last]
        cuts, begin = [], 0
        for index, gap in enumerate(gaps):
            after = shape[gap:gaps[index + 1]] if index + 1 < len(gaps) else shape[gap:]
            if shape[begin:gap].strip() and after.strip():
                cuts.append(gap)
                begin = gap
        if len(cuts) < 2:
            return str(text)
        shown = []
        for line in lines:
            if len(line.split()) < 3:
                shown.append(line)
                continue
            parts, begin = [], 0
            for cut in cuts:
                if cut >= len(line):
                    break
                if line[cut] != " " and line[cut - 1] != " ":  # a value running into the gap: cut after it
                    cut = line.find(" ", cut)
                    if cut < 0:
                        break
                if cut > begin:
                    parts.append(line[begin:cut].strip())
                    begin = cut
            parts.append(line[begin:].strip())
            shown.append(" | ".join(part for part in parts if part))
        return "\n".join(shown)

    @staticmethod
    def fenced(text):
        """text safe inside a block: a fence in it cannot end the block."""
        return str(text).replace("```", "ˋˋˋ")

    @classmethod
    def html(cls, text):
        """The text as Telegram's HTML shows it."""
        keep = cls.keeper()

        def block(found):
            body = html.escape(found.group(2).rstrip("\n"), quote=False)
            return keep(f"<blockquote expandable>{body}</blockquote>" if found.group(1) == "output"
                        else f"<pre>{body}</pre>")
        return cls.render(text, block, lambda code: f"<code>{html.escape(code, quote=False)}</code>",
                          lambda plain: html.escape(plain, quote=False),
                          lambda tag, inner: f"<{tag}>{inner}</{tag}>", keep)

    @classmethod
    def plain(cls, text):
        """The text with its marks taken off, for a chat that shows none."""
        keep = cls.keeper()
        return cls.render(text, lambda found: keep(found.group(2).rstrip("\n")), lambda code: code,
                          lambda plain: plain, lambda tag, inner: inner, keep)

    @staticmethod
    def keeper():
        kept = []

        def keep(shown):
            kept.append(shown)
            return f"\x00{len(kept) - 1}\x00"
        keep.kept = kept
        return keep

    @classmethod
    def render(cls, text, block, code, plain, mark, keep):
        """text shown with block(fence match), code(text), plain(text) and mark(tag, inner); what is shown
        as it is (blocks, code, escaped marks) is kept apart until the rest is marked."""
        text = cls.FENCE_RE.sub(block, str(text))
        text = cls.ESCAPED_RE.sub(lambda found: keep(plain(found.group(1))), text)
        text = cls.CODE_RE.sub(lambda found: keep(code(found.group(1))), text)
        text = cls.ITEM_RE.sub("• ", plain(text))
        text = cls.HEADING_RE.sub(lambda found: mark("b", found.group(1)), text)
        for pattern, tag in cls.MARKS:
            text = pattern.sub(lambda found: mark(tag, found.group(1)), text)
        return re.sub("\x00(\\d+)\x00", lambda found: keep.kept[int(found.group(1))], text)


lit = Markup.literal


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
        """Send text (Markup) to chat; what edit needs to change it later (or None)."""
        raise NotImplementedError

    def edit(self, chat, sent, text):
        """Change a message sent before (a channel that cannot edit sends a new one)."""
        return self.send(chat, text)

    def typing(self, chat):
        """Show chat that a reply is being written (a channel that cannot says nothing)."""

    def skip_waiting(self):
        """Forget the messages sent while nothing listened: a command sent hours ago is not run now."""

    def listened(self, timeout):
        """The new messages from the chats this channel serves; the rest are ignored. A refusal (another
        program reading this bot's messages, a revoked token) is said once and waited out."""
        try:
            messages = self.receive(timeout)
        except ChannelError as exc:
            if str(exc) != getattr(self, "refused", None):
                print(f"  note: {self.name}: {exc}; trying again")
            self.refused = str(exc)
            time.sleep(timeout)
            return []
        self.refused = None
        return [m for m in messages if m.chat in self.allowed]

    def claim(self, holder):
        """Become the one tinker channel start listening on this channel: True once it is, False when another
        one listens. Held until this process ends, even when it crashes. holder: what this one is (its
        process), kept in the lock for the next one to read."""
        HOME.mkdir(parents=True, exist_ok=True)
        self.claimed = open(self.lock_file(), "a")
        try:
            fcntl.flock(self.claimed, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.claimed.close()
            return False
        self.claimed.truncate(0)
        self.claimed.write(json.dumps(holder))
        self.claimed.flush()
        return True

    def holder(self):
        """What the Tinker listening on this channel said it is ({} when that cannot be read)."""
        try:
            holder = json.loads(self.lock_file().read_text() or "{}")
        except (OSError, ValueError):
            return {}
        return holder if isinstance(holder, dict) else {}

    def lock_file(self):
        return HOME / f"channel-{self.name}.lock"


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
            return self.post("sendMessage", chat, text)["message_id"]
        except (OSError, ChannelError):
            return None

    def edit(self, chat, sent, text):
        if sent is None:
            return self.send(chat, text)
        try:
            self.post("editMessageText", chat, text, message_id=sent)
        except ChannelError as exc:  # unchanged text is refused, harmlessly
            if "not modified" not in str(exc):
                return self.send(chat, text)
        except OSError:
            pass
        return sent

    def post(self, method, chat, text, **fields):
        """text sent (or edited) as Telegram shows Markup (HTML); what it cannot read as HTML goes plain."""
        try:
            return self.call(method, chat_id=chat, text=Markup.html(text)[:4096], parse_mode="HTML", **fields)
        except ChannelError as exc:
            if "parse" not in str(exc) and "entit" not in str(exc):
                raise
            return self.call(method, chat_id=chat, text=Markup.plain(text)[:4096], **fields)


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
    END_RE = re.compile(r"^(Done|Stopped|Not done|Interrupted|Run \w+ is not finished|📋 Spec for)")
    TIMES_RE = re.compile(r"^(Log|Started|Total execution time): ")  # the terminal's, not the chat's
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
        lines = [f"🔨 **{lit(self.title)}**", f"▸ _{lit(self.phase)}_"]
        lines += [f"✅ {lit(self.passing)}"] if self.passing else []
        lines += [f"… {lit(self.latest)}"] if self.latest else []
        return "\n".join(lines + [f"🕒 _updated {time.strftime('%H:%M:%S')}_"])

    def brief(self):
        """Markup: the title, with the phase and the passing points."""
        return f"**{lit(self.title)}** ({lit(self.phase)}" + (f", {lit(self.passing)})" if self.passing else ")")

    def ending(self):
        """The summary for the chat (Markup); a spec waiting for confirmation is the summary itself."""
        lines = [line for line in self.summary if line.strip() and not self.TIMES_RE.match(line)]
        if self.waiting():
            return "\n".join(shown for shown in map(self.spec_line, lines) if shown is not None)
        if not lines:
            return f"🏁 **{lit(self.title)}**"
        first = lines[0]
        mark = "✅" if first.startswith("Done") and "but" not in first else "⏸" if re.match(
            r"(Interrupted|Run \w+ is not finished|Planned)", first) else "⚠️"
        return "\n".join([f"🏁 **{lit(self.title)}**", f"{mark} **{lit(first)}**"] + [self.done_line(line)
                                                                                    for line in lines[1:]])

    SPEC_RE = re.compile(r"📋 Spec for (.+?) \(run (\w+)\): (.+?)(?:; a person runs it with: (.+))?$")
    CHANGE_RE = re.compile(r"\s+([+~-]) (?:(C\d+) )?(.*)")
    POINT_RE = re.compile(r"\s+(C\d+)\s+(.*)")

    def spec_line(self, line):
        """A line of a waiting spec as the chat shows it (None: a line the chat leaves out)."""
        head, change, point = self.SPEC_RE.match(line), self.CHANGE_RE.match(line), self.POINT_RE.match(line)
        if head:
            name, run, language, start = head.groups()
            return (f"📋 **Spec for {lit(name)}** · run `{run}`\n🧰 {lit(language)}"
                    + (f" · ▶️ `{start}`" if start else ""))
        if line == "What changed:":
            return "\n✏️ **What changed**"
        if change:
            sign, number, text = change.groups()
            number = f"**{number}** " if number else ""
            return {"+": f"➕ {number}{lit(text)}", "~": f"🔄 {number}{lit(text)}",
                    "-": f"➖ ~~{lit(text)}~~"}[sign]
        if line.startswith("Tested before any code is written"):
            return "\n🧪 **Tested before any code is written**"
        if point:
            return f"**{point.group(1)}** {lit(point.group(2))}"
        if line.startswith("Not included: "):
            return f"\n🚫 _Not included:_ {lit(line.split(': ', 1)[1])}"
        if line.startswith("Its folder: "):
            return f"📁 `{line.split(': ', 1)[1].split('   (', 1)[0]}`"
        if line.startswith("Build it: "):
            return "\n👉 `confirm` to build it · `amend WHAT` to change it"
        if line.startswith("Change it: "):
            return None
        return lit(line)

    @staticmethod
    def done_line(line):
        """A line of a run's summary as the chat shows it: what to type, as code."""
        label, _, value = line.partition(": ")
        more = re.fullmatch(r"Add a feature with: (tinker add .+?); fix a bug with: (tinker fix .+)", line)
        if more:
            return f"➕ Add a feature: `{more.group(1)}` · 🐞 fix a bug: `{more.group(2)}`"
        if label in ("Run it", "Settings") and value:
            return f"▶️ {label}: `{value}`" if label == "Run it" else f"⚙️ {label}: {lit(value)}"
        return lit(line)

    def waiting(self):
        """The run ended with its spec waiting for the person to confirm or amend it."""
        return any(line.startswith("📋 Spec for") for line in self.summary)


class Reporter:
    """Progress shown on the channels: one message per chat, edited at most every EDIT_EVERY seconds (a
    change that came sooner is shown once that much has passed), and the summary sent once when the run ends."""

    def __init__(self, channels, title, shared=None):
        self.channels, self.progress = channels, Progress(title)
        self.sent, self.last_edit, self.lock = {}, 0.0, threading.Lock()
        self.shared = shared  # a file the progress is kept in too, for the Tinker listening (running_elsewhere)
        self.quiet = False  # the run stops for an emergency and goes on: no summary
        self.pending, self.ended = False, False  # a change not shown yet; the run ended
        threading.Thread(target=self.keep_up, daemon=True).start()

    def line(self, text):
        with self.lock:
            if self.progress.read(text):
                self.pending = True
                if time.monotonic() - self.last_edit >= EDIT_EVERY:
                    self.update()

    def keep_up(self):
        while not self.ended:
            time.sleep(EDIT_EVERY)
            with self.lock:
                if self.pending and not self.ended:
                    self.update()

    def update(self):
        self.last_edit, self.pending = time.monotonic(), False
        for channel in self.channels:
            for chat in channel.allowed:
                key = (channel.name, chat)
                self.sent[key] = channel.edit(chat, self.sent.get(key), self.progress.text())
        if self.shared:
            try:
                self.shared.parent.mkdir(parents=True, exist_ok=True)
                self.shared.write_text(json.dumps({"title": self.progress.title, "brief": self.progress.brief(),
                                                   "text": self.progress.text()}))
            except OSError:
                pass

    def finish(self):
        with self.lock:
            self.update()
            self.ended = True
            if self.progress.summary and not self.quiet:
                for channel in self.channels:
                    for chat in channel.allowed:
                        for part in pieces(self.progress.ending()):
                            channel.send(chat, part)


def pieces(text, size=3500):
    """text in parts a chat message holds, split between lines."""
    parts, part = [], ""
    for line in text.splitlines():
        if part and len(part) + len(line) + 1 > size:
            parts.append(part)
            part = ""
        part = f"{part}\n{line}" if part else line[:size]
    return parts + [part] if part else parts


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


HELP_ALONE = ("🛠 **What you can send**\n"
              "🔨 `task WHAT TO BUILD` plan it in its own folder; its spec comes here first\n"
              "✏️ `amend WHAT` change the spec that waits\n"
              "✅ `confirm [ID]` build the spec that waits (its folder name is asked once)\n"
              "📁 `rename NAME` name its folder otherwise\n"
              "▶️ `resume ID` continue a run\n"
              "📚 `runs` every run · 📊 `status [ID]` a run's spec points\n"
              "⏹ `stop` stop the task that is running\n"
              "🚨 `emergency MESSAGE` stop what runs, answer now, then go on\n"
              "🖥 `command WHAT` the command line that does it, run once you confirm\n"
              "❓ `help` this list · 💬 anything else: a normal chat")


class Talk:
    """A normal chat with the model in use: a message that is not a command is answered by it, with that
    chat's last CHAT_KEPT messages (and what is running now) as context. It does not think, so a reply comes
    quickly, and its settings are a run's own (num_ctx), so a task and a chat share the loaded model: warm
    loads it as listening starts, and neither the first reply nor the next task waits for it to load.
    While a task runs (here or elsewhere), the model is the task's: a chat is told so at once, and that note
    becomes the (shorter) answer after the task's current step. Chats take turns, so an emergency waits for one
    at most."""
    SYSTEM = ("You are Tinker, a friendly helper that builds small programs on this computer with a local model. "
              "You are chatting over {kind}, read on a phone: answer briefly, in short lines. Format for "
              "meaning, in Markdown: **bold** for what matters most, *italic* for an aside, `code` for "
              "commands, names and paths, \"- \" for items; an emoji only where it says something with less "
              "(✅ done or fine, ⚠️ a problem, 💡 a tip, 👉 what to do next); never a heading or a table. Talk "
              "about anything. You cannot run anything yourself from the chat; these messages do:\n{commands}")

    def __init__(self, commands):
        self.commands, self.said, self.lock, self.turn = commands, {}, threading.Lock(), threading.Lock()

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

    def answer(self, channel, message, now, waiting=lambda: "", write=None):
        """The reply to message, sent when written (in a thread, so `stop` is heard meanwhile). now(): what
        runs now, [{title, brief, text}]; waiting(): a spec that waits for the person; write: what writes the
        reply (channel, message, context, tokens -> text), the chat's own by default. When something runs, or the reply is not
        written in BUSY_AFTER seconds, a note says so at once, updated every BUSY_EDIT_EVERY seconds with the
        progress; the reply then takes its place."""
        threading.Thread(target=self.answering, args=(channel, message, now, waiting, write or self.reply),
                         daemon=True).start()

    def answering(self, channel, message, now, waiting, write_reply):
        running, written, reply = now(), threading.Event(), []
        context = ["Running now:\n" + "\n\n".join(r["text"] for r in running)] if running else []
        context += [f"A spec waits for the person (confirm builds it, amend WHAT changes it):\n{waiting()}"
                    if waiting() else ""]

        def write():
            try:
                with self.turn:
                    reply.append(write_reply(channel, message, "\n\n".join(c for c in context if c),
                                             CHAT_BUSY_TOKENS if running else CHAT_REPLY_TOKENS))
            finally:
                written.set()
        threading.Thread(target=write, daemon=True).start()
        note = None
        if running or not written.wait(BUSY_AFTER):
            note = channel.send(message.chat, self.busy_note(running))
            while not written.wait(BUSY_EDIT_EVERY):
                note = channel.edit(message.chat, note, self.busy_note(now()))
        channel.edit(message.chat, note, reply[0] if reply else "⚠️ **The model did not answer.**")

    @staticmethod
    def busy_note(running):
        if not running:
            lines = ["⏳ **The model is busy or loading.** I'll answer here as soon as it is free."]
        else:
            lines = ["⏳ **Busy** with " + "; ".join(r["brief"] for r in running) + ".",
                     "I'll answer here after the current step.", "🚨 An answer now: `emergency MESSAGE`"]
        return "\n".join(lines + [f"🕒 _updated {time.strftime('%H:%M:%S')}_"])

    def reply(self, channel, message, now="", tokens=CHAT_REPLY_TOKENS):
        """The model's reply to message; now: what is going on (what runs, a spec that waits)."""
        found = self.model()
        if not found:
            return ("⚠️ **No model is in use yet**, so I cannot chat: `tinker calibrate NAME`, then "
                    f"`tinker use-model NAME`. These still work:\n{self.commands}")
        with self.lock:
            said = self.said.setdefault((channel.name, message.chat), [])
            said.append({"role": "user", "content": message.text})
            asked = said[-CHAT_KEPT:]
        system = self.SYSTEM.format(kind=channel.KIND.title(), commands=self.commands)
        system += f"\n\n{now}" if now else "\n\nNo task is running now."
        try:
            text = self.ask(channel, message.chat, [{"role": "system", "content": system}] + asked, tokens)
        except OllamaError as exc:
            return f"⚠️ **The model did not answer:** {lit(first_words(exc))}"
        text = text or "_(no answer)_"
        with self.lock:
            said.append({"role": "assistant", "content": text})
            del said[:-CHAT_KEPT]
        return text


    def ask(self, channel, chat, messages, tokens):
        """The model's reply to messages (it does not think), "typing…" shown meanwhile; OllamaError when it
        fails. Only called once model() found the model."""
        name, options, think = self.model()
        done = threading.Event()
        threading.Thread(target=keep_typing, args=(channel, chat, done), daemon=True).start()
        try:
            data = Ollama.chat(name, messages, Config.keep_alive(Config.get("keep_alive")),
                               dict(options, num_predict=tokens), think=think, timeout=CHAT_TIMEOUT)
        finally:
            done.set()
        return data["message"]["content"].strip()


class Shell:
    """command WHAT: the model names the one command line that does what the person describes, shown with
    what it does (and a warning when it changes or deletes things). `confirm` as the person's very next
    message runs it; anything else drops it. It runs from the folder tinker channel start listens in, in the
    person's login shell, with no input, for up to COMMAND_TIMEOUT seconds; its exit code and output (the
    end of it, when long) come back folded, and the model then tells what it says, in a few lines."""
    SYSTEM = ("The person describes a command they want run on their computer ({system}, shell {shell}, in the "
              "folder {folder}). Reply with ONLY JSON: {{\"command\": \"the one command line that does it\", "
              "\"does\": \"what it does, in one short sentence\"}}, or {{\"command\": \"\", \"does\": \"why no "
              "single command fits\"}} when the description is unclear or asks for no command.")
    CHANGES_RE = re.compile(r"\b(rm|rmdir|mv|dd|mkfs\w*|chmod|chown|kill|killall|pkill|sudo|shutdown|reboot|"
                            r"truncate)\b|(?<![\d&>])>(?!&)|\bgit\s+(push|reset|clean|checkout|rebase)\b|"
                            r"\b(brew|pip3?|npm|cargo)\s+(install|uninstall|remove)\b|\s-delete\b|-exec\s+(rm|mv)\b|"
                            r"\bxargs\s+(rm|mv)\b|\bsed\s+-i\b")
    WRITING = ""  # a command still being named
    TELL_SYSTEM = ("Tell the person what this command's output says, for a chat read on a phone: the answer to "
                   "what they asked first, then only what matters, in a few short lines. Format for meaning, in "
                   "Markdown: **bold** for the key values, `code` for names and paths, \"- \" for items; an emoji "
                   "only where it says something with less (⚠️ a problem or something nearly full, ✅ all fine, "
                   "💾 disk, 📁 folders, 🌐 network, ⏱ time). Never a heading or a table; never anything the "
                   "output does not show; never the command again.")

    def __init__(self, talk, folder):
        self.talk, self.folder = talk, folder
        self.proposed = {}  # (channel, chat) -> (command line, what was asked) waiting for confirm (WRITING meanwhile)
        self.shell = os.environ.get("SHELL") or "/bin/sh"

    def name(self, channel, message, now):
        """Have the model name the command (the reply comes as a chat's, busy note and all)."""
        self.proposed[(channel.name, message.chat)] = self.WRITING
        self.talk.answer(channel, message, now, write=self.propose)

    def propose(self, channel, message, context, tokens):
        key = (channel.name, message.chat)
        if not self.talk.model():
            self.proposed.pop(key, None)
            return "⚠️ **No model is in use yet:** `tinker calibrate NAME`, then `tinker use-model NAME`."
        system = self.SYSTEM.format(system=os.uname().sysname, shell=Path(self.shell).name, folder=self.folder)
        try:
            data = json_in(self.talk.ask(channel, message.chat, [{"role": "system", "content": system},
                                                                 {"role": "user", "content": message.text}],
                                         COMMAND_TOKENS))
        except OllamaError as exc:
            self.proposed.pop(key, None)
            return f"⚠️ **The model did not answer:** {lit(first_words(exc))}"
        line, does = str(data.get("command") or "").strip(), str(data.get("does") or "").strip()
        if not line or "\n" in line:
            self.proposed.pop(key, None)
            return f"🤷 **No command found:** {lit(does or 'describe it another way')}"
        self.proposed[key] = (line, message.text)
        warning = ("\n⚠️ **It changes or deletes things:** read it before you confirm."
                   if self.CHANGES_RE.search(line) else "")
        return (f"🖥 **Run this?** _in_ `{self.folder}`\n```\n{Markup.fenced(line)}\n```\n{lit(does)}{warning}\n"
                f"👉 `confirm` to run it · anything else cancels it")

    def take(self, channel, chat):
        """The command waiting for this chat's next message (WRITING while it is named; None when none)."""
        key = (channel.name, chat)
        line = self.proposed.get(key)
        if line != self.WRITING:
            self.proposed.pop(key, None)
        return line

    def run(self, channel, chat, proposed):
        """Run the command; its output comes at once, folded, and what it says once the model has read it."""
        line, asked = proposed
        sent = channel.send(chat, f"▶️ **Running** `{line}` …")
        try:
            done = subprocess.run([self.shell, "-lc", line], cwd=self.folder, text=True, capture_output=True,
                                  stdin=subprocess.DEVNULL, timeout=COMMAND_TIMEOUT)
            code, output = done.returncode, done.stdout + done.stderr
        except subprocess.TimeoutExpired as exc:
            code, output = f"stopped after {COMMAND_TIMEOUT}s", text_of(exc.stdout) + text_of(exc.stderr)
        except OSError as exc:
            code, output = "not started", str(exc)
        output = output.strip()
        shown = output if len(output) <= COMMAND_SHOWN else "… (the start is left out)\n" + output[-COMMAND_SHOWN:]
        head = f"{'✅' if code == 0 else '❌'} `{line}` · _exit {lit(code)}_"
        folded = f"```output\n{Markup.fenced(Markup.columns(shown))}\n```" if output else "_(no output)_"
        sent = channel.edit(chat, sent, f"{head}\n{folded}")
        if not output or not self.talk.model():
            return
        try:
            told = self.talk.ask(channel, chat, [{"role": "system", "content": self.TELL_SYSTEM},
                                                 {"role": "user", "content": f"They asked: {asked}\nCommand: {line}\n"
                                                  f"Exit code: {code}\nOutput:\n{output[-COMMAND_READ:]}"}],
                                 CHAT_REPLY_TOKENS)
        except OllamaError:
            return
        if told:
            channel.edit(chat, sent, f"{head}\n{told}\n{folded}")


def json_in(text):
    """The JSON object a reply holds ({} when none)."""
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(found.group(0)) if found else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def text_of(output):
    return output.decode(errors="replace") if isinstance(output, bytes) else (output or "")


def running_elsewhere():
    """[{pid, title, brief, text, paused}] of the tasks run from a terminal, reporting to the channels (the
    file of one that ended is removed)."""
    found = []
    for path in RUNNING_DIR.glob("*.json"):
        pid = int(path.stem) if path.stem.isdigit() else 0
        if pid <= 0 or pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            path.unlink(missing_ok=True)
            continue
        except PermissionError:
            pass
        try:
            found.append(dict(json.loads(path.read_text()), pid=pid))
        except (OSError, ValueError, TypeError):
            pass
    return found


def keep_typing(channel, chat, done):
    """The chat's "typing…" sign, shown until done is set."""
    while not done.is_set():
        channel.typing(chat)
        done.wait(TYPING_EVERY)


def command_of(text, commands):
    """The command a message gives (one of commands, sent as its first word, with or without a /), or ""
    when it is chat. A word alone is a command; with more after it only task, emergency, amend, command, resume ID,
    status ID, confirm ID and rename NAME are."""
    words = text.split()
    first = words[0].lower().lstrip("/") if words else ""
    if first not in commands:
        return ""
    if len(words) == 1 or first in ("task", "emergency", "amend", "command") or (first in ("resume", "status", "confirm",
                                                                                  "rename") and len(words) == 2):
        return first
    return ""


def after_word(message):
    """What a command word is followed by (the emergency's own message, the command described)."""
    return Message(message.chat, message.text.split(None, 1)[1], message.sender)


class Attached:
    """A task run from a terminal, reporting to the channels: its progress message, kept in RUNNING_DIR too,
    for tinker channel start (which reads the chat) to answer status from. That stops it with STOP, as Ctrl-C
    does (saved), and pauses it for an emergency with PAUSE: the run stops the same way, waits for the answer
    (its file is removed then), and goes on here (resumed)."""

    def __init__(self, channels, title):
        self.reporter = Reporter(channels, title, RUNNING_DIR / f"{os.getpid()}.json")
        self.paused = False

    def start(self):
        sys.stdout = LineWatcher(sys.stdout, self.reporter.line)
        signal.signal(PAUSE, self.pause)
        signal.signal(STOP, self.stop)
        self.reporter.update()
        return self

    def pause(self, signum, frame):
        self.paused = self.reporter.quiet = True
        raise KeyboardInterrupt

    def stop(self, signum, frame):
        raise KeyboardInterrupt

    def end(self):
        """Stop reporting; True when the run stopped for an emergency, now answered: it goes on."""
        if isinstance(sys.stdout, LineWatcher):
            sys.stdout.flush()
            sys.stdout = sys.stdout.stream
        self.reporter.finish()
        shared = self.reporter.shared
        if not self.paused:
            shared.unlink(missing_ok=True)
            return False
        print("\nStopped for an emergency from the chat: going on once it is answered.")
        try:
            shared.write_text(json.dumps(dict(json.loads(shared.read_text()), paused=True)))
        except (OSError, ValueError):
            pass
        ends = time.monotonic() + RESUME_WAIT
        while shared.exists() and time.monotonic() < ends:
            time.sleep(1)
        shared.unlink(missing_ok=True)
        return True


def attach(title):
    """The enabled channels the run about to start reports to (None when there are none). A channel that
    does not work is said once and left out: a run never fails for its channel."""
    try:
        channels = Channels.enabled()
    except SystemExit as exc:
        print(f"  note: channels left out: {exc}")
        return None
    return Attached(channels, title).start() if channels else None


class Alone:
    """tinker channel start: the one reading the chat, from this folder. One task runs here at a time, as
    its own tinker (which does not report too); its printed progress goes to the chat. Tasks run from a
    terminal are served too (running_elsewhere). Anything that is not a command is a normal chat."""
    COMMANDS = ("task", "resume", "runs", "status", "stop", "help", "emergency", "confirm", "amend", "rename",
                "command")
    RUN_RE = re.compile(r"^Run ([0-9a-f]{3,}) \(")  # a run's first line: its ID
    FOLDER_LINE_RE = re.compile(r"^Project: (/.+)$")  # its header's: its folder

    def __init__(self, channels, root):
        self.channels, self.root = channels, Path(root).resolve()
        self.process = self.reporter = self.task = None  # task: {arguments, folder, title, run} of the last one
        self.lock, self.talk = threading.Lock(), Talk(HELP_ALONE)
        self.shell = Shell(self.talk, self.root)
        self.urgent = False  # an emergency is being answered: no task starts meanwhile
        self.waiting = None  # {run, folder, title, spec} of the spec that waits for confirm or amend

    def run(self):
        for channel in self.channels:
            if not channel.claim({"pid": os.getpid()}):
                raise SystemExit(f"tinker: {channel.name} is listened to by another tinker channel start (process "
                                 f"{channel.holder().get('pid', '?')}): stop it first")
        self.talk.warm()
        for channel in self.channels:
            channel.skip_waiting()
            for chat in channel.allowed:
                channel.send(chat, f"👋 **Tinker is listening** _from_ `{self.root}`\n\n{HELP_ALONE}")
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
                    channel.send(chat, "👋 **Tinker stopped listening.**")
            print("\nStopped listening.")

    def listen(self, channel):
        while True:
            for message in channel.listened(timeout=20):
                print(f"[{time.strftime('%H:%M:%S')}] {channel.name}: {message.text}")
                command = command_of(message.text, self.COMMANDS)
                proposed = self.shell.take(channel, message.chat)  # a command waits for this very message
                if proposed == Shell.WRITING and command == "confirm":
                    channel.send(message.chat, "⏳ The command is still being worked out: `confirm` once it is shown.")
                    continue
                if proposed and command == "confirm" and len(message.text.split()) == 1:
                    threading.Thread(target=self.shell.run, args=(channel, message.chat, proposed), daemon=True).start()
                    continue
                if proposed:
                    channel.send(message.chat, f"🚫 _Not run:_ `{proposed[0]}`")
                if command == "command":
                    if len(message.text.split()) == 1:
                        channel.send(message.chat, "✍️ Write `command WHAT`, e.g. `command show the free disk space`")
                    else:
                        self.shell.name(channel, after_word(message), self.now)
                    continue
                if not command:
                    self.talk.answer(channel, message, self.now, lambda: (self.waiting or {}).get("spec", ""))
                    continue
                if command_of(message.text, self.COMMANDS) == "emergency" and len(message.text.split()) > 1:
                    threading.Thread(target=self.emergency, args=(channel, after_word(message)), daemon=True).start()
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
        if command == "emergency":
            return "✍️ Write `emergency MESSAGE`"
        if command == "task":  # Tinker makes its folder in the projects folder, named after it
            if len(words) < 2:
                return "✍️ Write `task WHAT TO BUILD`, e.g. `task a calculator in Python`"
            request = " ".join(words[1:])
            return self.start_task(["task", request, "--confirm"], self.root, request)
        if command in ("confirm", "amend", "rename"):
            return self.decide(command, words)
        if command == "resume":
            if len(words) != 2:
                return "✍️ Write `resume ID` (`runs` lists the IDs)"
            return self.start_task(["resume", words[1]], self.root, f"resume {words[1]}")
        if command == "stop":
            if self.stop_task():
                return "⏹ **Stopping the task:** it is saved; `resume` goes on later."
            elsewhere = running_elsewhere()
            for run in elsewhere:  # as Ctrl-C in its terminal: the run saves itself
                signal_task(run["pid"], STOP)
            if not elsewhere:
                return "💤 No task is running."
            return f"⏹ **Stopping** {'; '.join(lit(r['title']) for r in elsewhere)}: saved; `resume` goes on later."
        if command in ("runs", "status"):
            if command == "status" and len(words) == 1 and self.now():
                return "\n\n".join(r["text"] for r in self.now())
            title = "📚 **Runs**" if command == "runs" else f"📊 **Status** {lit(' '.join(words[1:2]))}".rstrip()
            return f"{title}\n```\n{Markup.fenced(Markup.columns(self.tinker([command] + words[1:2])))}\n```"
        return HELP_ALONE

    def decide(self, command, words):
        """confirm [ID]: build the spec that waits (its folder name is asked once first: confirm again, or
        rename NAME, builds it); amend WHAT: change it (it waits again); rename NAME: rename its folder."""
        waiting = self.waiting or {}
        if command == "amend" and len(words) == 1:
            return "✍️ Write `amend WHAT`, e.g. `amend also handle negative numbers`"
        if command == "rename":
            return self.rename(waiting, words)
        if command == "confirm" and len(words) == 1 and waiting.get("stage") == "spec":
            waiting["stage"] = "name"
            return (f"📁 **Its folder:** `{waiting['folder']}`\n👉 `confirm` to build it there · `rename NAME` "
                    f"to name it otherwise (it builds then)")
        run = words[1] if command == "confirm" and len(words) == 2 else waiting.get("run")
        if not run:
            return "🤷 No spec waits for confirmation: start one with `task WHAT TO BUILD`"
        mine = waiting.get("run") == run
        folder, title = (waiting["folder"], waiting["title"]) if mine else (self.root, f"run {run}")
        if command == "confirm":
            refused = self.start_task(["resume", run], folder, title)
            return refused or f"✅ **Confirmed:** building {lit(title)}"
        refused = self.start_task(["amend", run, " ".join(words[1:])], folder, title)
        return refused or f"✏️ **Changing the spec** of {lit(title)} …"

    def rename(self, waiting, words):
        """rename NAME: the waiting project's folder renamed (tinker rename); asked for its name, it builds."""
        if len(words) != 2:
            return "✍️ Write `rename NAME`, e.g. `rename calculator`"
        if not waiting:
            return "🤷 No spec waits for confirmation, so no folder can be renamed (once built, it stays)."
        if self.busy():
            return "⏳ A task is running: wait for it, or `stop` it first."
        done = subprocess.run(tinker_command() + ["rename", waiting["run"], words[1]], cwd=self.root, text=True,
                              capture_output=True, env=dict(os.environ, NO_COLOR="1"), timeout=60)
        if done.returncode:
            return f"⚠️ **Not renamed:** {lit(first_lines((done.stdout + done.stderr).strip(), 10).splitlines()[-1])}"
        waiting["folder"] = Path(waiting["folder"]).parent / words[1]
        waiting["title"] = f"{words[1]}: {waiting['title'].split(': ', 1)[-1]}"
        if waiting.get("stage") != "name":
            return f"📁 **Renamed:** its folder is `{waiting['folder']}`"
        refused = self.start_task(["resume", waiting["run"]], waiting["folder"], waiting["title"])
        return refused or f"✅ **Renamed** to `{words[1]}`: building {lit(waiting['title'])}"

    def busy(self):
        return self.process is not None and self.process.poll() is None

    def now(self):
        """What runs now: the task started here, and any run from a terminal."""
        progress = self.reporter.progress if self.busy() else None
        here = [{"title": progress.title, "brief": progress.brief(), "text": progress.text()}] if progress else []
        return here + running_elsewhere()

    def emergency(self, channel, message):
        """Stop what runs (a task saves itself), answer message, then go on with the task: one started here
        is resumed here, one run from a terminal resumes itself there."""
        if self.urgent:
            channel.send(message.chat, "🚨 An emergency is being answered: one at a time.")
            return
        with self.lock:
            self.urgent, stopped = True, self.task if self.busy() else None
            if stopped:
                channel.send(message.chat, f"🚨 **Stopping** {lit(stopped['title'])} to answer: it is saved and "
                                           f"goes on after.")
                self.reporter.quiet = True
                self.stop_task()
                try:
                    self.process.wait(STOP_WAIT)
                except subprocess.TimeoutExpired:  # it goes on by itself: answered after its step, as a chat
                    stopped = None
                    channel.send(message.chat, f"⚠️ The task did not stop in {STOP_WAIT}s; answering after its step.")
            paused = [run for run in running_elsewhere() if self.pause(channel, message, run)]
        try:
            channel.send(message.chat, self.talk.reply(channel, message))
        finally:
            self.urgent = False
        if stopped:
            again = ["resume", stopped["run"]] if stopped["run"] else stopped["arguments"]
            self.start_task(again, stopped["folder"], stopped["title"])
            channel.send(message.chat, f"▶️ **Going on** with {lit(stopped['title'])}")
        for run in paused:  # its file removed: the task goes on in its terminal
            (RUNNING_DIR / f"{run['pid']}.json").unlink(missing_ok=True)
            channel.send(message.chat, f"▶️ **Going on** with {lit(run['title'])} _(in its terminal)_")

    def pause(self, channel, message, run):
        """A task run from a terminal, stopped for an emergency: True once it says it is paused."""
        channel.send(message.chat, f"🚨 **Stopping** {lit(run['title'])} to answer: it is saved and goes on after.")
        signal_task(run["pid"], PAUSE)
        ends = time.monotonic() + STOP_WAIT
        while time.monotonic() < ends:
            now = next((r for r in running_elsewhere() if r["pid"] == run["pid"]), None)
            if now is None or now.get("paused"):
                return now is not None
            time.sleep(1)
        channel.send(message.chat, f"⚠️ {lit(run['title'])} did not stop in {STOP_WAIT}s; answering after its step.")
        return False

    def start_task(self, arguments, folder, title):
        with self.lock:
            if self.busy():
                return "⏳ A task is running: wait for it, or `stop` it first."
            if self.urgent:
                return "🚨 An emergency is being answered: send it again in a moment."
            self.task = {"arguments": arguments, "folder": folder, "title": title, "run": None}
            self.reporter = Reporter(self.channels, title)
            env = dict(os.environ, **{OFF: "off", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
            try:  # from here: a new task makes its own folder, a run given by ID finds its own
                self.process = subprocess.Popen(tinker_command() + arguments, cwd=self.root, env=env, text=True,
                                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as exc:
                self.process = None
                return f"⚠️ **Could not start it:** {lit(exc)}"
        if self.waiting and self.waiting["folder"] == folder:
            self.waiting = None  # confirmed or being changed: it waits again only once its spec is sent
        threading.Thread(target=self.follow, args=(self.process, self.reporter, self.task), daemon=True).start()
        self.reporter.update()
        return None

    def follow(self, process, reporter, task):
        """The task's printed lines to the terminal and the chat; its run and folder, once its header names
        them (the folder of a new task is named after it: the title says it from then on)."""
        for line in process.stdout:
            sys.stdout.write(line)
            run, folder = self.RUN_RE.match(line), self.FOLDER_LINE_RE.match(line.rstrip())
            if run and not task["run"]:
                task["run"] = run.group(1)
            if folder and Path(folder.group(1)) != Path(task["folder"]):
                task["folder"] = Path(folder.group(1))
                task["title"] = reporter.progress.title = f"{task['folder'].name}: {reporter.progress.title}"
            reporter.line(line)
        process.wait()
        reporter.finish()
        if task["run"] and reporter.progress.waiting():
            self.waiting = {"run": task["run"], "folder": task["folder"], "title": task["title"],
                            "spec": reporter.progress.ending(), "stage": "spec"}

    def stop_task(self):
        if not self.busy():
            return False
        os.killpg(self.process.pid, signal.SIGINT)  # as Ctrl-C: the run saves itself
        return True

    def tinker(self, arguments):
        done = subprocess.run(tinker_command() + arguments, cwd=self.root, text=True, capture_output=True,
                              env=dict(os.environ, NO_COLOR="1"), timeout=120)
        return first_lines((done.stdout + done.stderr).strip() or "(nothing)", 40)


def signal_task(pid, signum):
    """Send a task run from a terminal a signal (PAUSE or STOP)."""
    try:
        os.kill(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


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
             "tinker channel enable NAME          every task reports to it; tinker channel start reads it\n"
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
                    channel.send(message.chat, "✅ **Paired with Tinker.** Only this chat is listened to.")
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
