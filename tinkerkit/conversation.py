"""The conversation: talking to Tinker the way a person does, in the terminal (tinker, with no command) or
in a chat app (the channels plugin: tinker channel start), one Tinker serving them all at once (Desk).
Every command works the same way everywhere: a task (its spec comes first, to confirm or amend before
anything is built), a resume, the runs, their status, stop, and every other command of tinker's (one that
deletes waits for confirm); anything else is a normal chat with the model in use (Talk), told at once when
a task holds the model; `command WHAT` has the model name the command line that does it, run once the
person confirms it (Shell); `research TOPIC` answers from the web, with the pages as sources (Research);
and `emergency MESSAGE` stops the task (saved), answers at once and then goes on with the task. A task run
from a terminal is served the same way: its progress is kept in RUNNING_DIR, and it is stopped or paused
with a signal.

Channel is what every kind of channel does (receive messages, send, edit what it sent); ConsoleChannel is
the terminal, and the channels plugin adds the chat apps (TelegramChannel). Every message is written in
Markup (bold, italic, code ...), each mark for what it means, and each kind shows it its own way. Each
thing (a task, a research, a command, a chat's reply, an emergency) has one message, edited as it goes on
(show), with when it was last updated; a new one is sent only for a new thing, or for what no longer fits.
The one updated last is pinned where a chat pins, so its top says what moved last."""
import fcntl
import html
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .model import Models, Ollama, OllamaError
from .project import Runs
from .settings import HOME, STATE_DIR, Config, JsonFile, human_duration
from .web import Internet
from .workspace import Toolchain


# --- tasks, and the Tinkers that serve them --------------------------------------------------------------
RUNNING_DIR = HOME / "running"                 # <process>.json: what each run reporting to the channels does now
OFF = "TINKER_CHANNELS"                        # =off: a run the conversation started does not report too
STATUS_FILE = HOME / "listening.json"         # channel -> chat -> the id of Tinker's one status message there
EDIT_EVERY = 5                                 # seconds between edits of a progress message
CLOCK_EVERY = 15                               # ... between edits that only move its clock on
STOP_WAIT = 120                                # seconds a task is given to stop (and save) for an emergency
PAUSE = signal.SIGUSR1                         # to a task run from a terminal: stop for an emergency, then go on
STOP = signal.SIGUSR2                          # ... stop, as Ctrl-C (which a task started in the background ignores)
RESUME_WAIT = 600                              # seconds such a task waits for the answer before it goes on anyway

# --- a chat with the model ---------------------------------------------------------------------------------
CHAT_KEPT = 12                                 # messages of one chat kept as the context of its next reply
CHAT_REPLY_TOKENS = 1024                       # the longest chat reply
CHAT_BUSY_TOKENS = 300                         # ... while a task runs, so the task waits as little as it can
CHAT_TIMEOUT = 600                             # seconds a chat reply may take (a task may hold the model first)
TYPING_EVERY = 4                               # seconds between "typing…" signs while a reply is written
BUSY_AFTER = 5                                 # seconds a chat reply may take before a note says the model is busy
BUSY_EDIT_EVERY = 30                           # seconds between updates of that note (the progress of what runs)

# --- command WHAT, research TOPIC --------------------------------------------------------------------------
COMMAND_TOKENS = 300                           # the longest reply naming a command
COMMAND_TIMEOUT = 120                          # seconds a command run from the chat may take
COMMAND_SHOWN = 3000                           # chars of its output sent back, folded (its end, when longer)
COMMAND_READ = 6000                            # ... the model reads to tell what it says
RESEARCH_SEARCHES = 3                          # web searches a research makes
RESEARCH_PAGES = 4                             # pages it reads (the first each search finds, then the second ...)
RESEARCH_PAGE_CHARS = 3500                     # of each page, the parts most about the topic

# --- tinker's commands, as the conversation runs them (their arguments and help are tinker.py's Cli's) -----
LONG = ("resume", "add", "fix", "restart", "learn", "calibrate")  # a task: one at a time, its progress shown
QUICK = ("runs", "status", "memory", "doctor", "models", "list-config", "use-model", "embed", "channel")
DELETES = ("clear", "reset", "restart")  # what it deletes is shown first; done once confirmed (--yes)
OWN = ("task", "confirm", "amend", "rename", "stop", "emergency", "command", "research", "run", "help",
       "exit")  # the conversation's
ONE_WORD = ("resume", "status", "confirm", "rename", "help", "use-model", "embed", "calibrate")  # one word after it
ANY_WORDS = ("task", "emergency", "amend", "command", "research", "run", "add", "fix", "learn", "clear", "channel")
TERMINAL_ONLY = ("add", "remove", "start")  # tinker channel's: they ask in a terminal (a token, a pairing)
SAID_BARE = LONG + QUICK + DELETES + OWN + ("chat", "channel")  # commands a conversation takes as their words
BARE_RE = re.compile(r"(?<![/\w.-])tinker (?=(?:" + "|".join(sorted(SAID_BARE, key=len, reverse=True)) + r")\b)")
USAGE = {"task": "✍️ Write `task WHAT TO BUILD`, e.g. `task a calculator in Python`",
         "amend": "✍️ Write `amend WHAT`, e.g. `amend also handle negative numbers`",
         "rename": "✍️ Write `rename NAME`, e.g. `rename calculator`",
         "resume": "✍️ Write `resume ID` (`runs` lists the IDs)",
         "emergency": "✍️ Write `emergency MESSAGE`",
         "command": "✍️ Write `command WHAT`, e.g. `command show the free disk space`",
         "research": "✍️ Write `research TOPIC`, e.g. `research the newest Python release`"}
EXIT = ("exit",)  # end the conversation (in a chat, once confirmed: it cannot be started again from there)


# === how a message is written, and the channels it goes through ==============================================

class Markup:
    """The marks a chat message is written with, as in Markdown, each for what it means: **bold** for what
    matters most, *italic* (or _italic_) for an aside, ~~struck~~ for what is gone, `code` for what is typed
    or named exactly (a command, a path), [a link](https://...), ```a block``` of code, and ```output for a
    command's output (folded where the chat can fold it); "- " starts an item. A mark meant as itself is
    escaped (literal)."""
    FENCE_RE = re.compile(r"```(\w*)\n?(.*?)(?:\n?```|\Z)", re.S)
    CODE_RE = re.compile(r"`([^`\n]+)`")
    LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
    ESCAPED_RE = re.compile(r"\\([*_`~\\])")
    MARKS = ((re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "b"), (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), "s"),
             (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"), "i"),
             (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "i"))
    ITEM_RE = re.compile(r"(?m)^\s*[-*] (?=\S)")
    HEADING_RE = re.compile(r"(?m)^#{1,6} +(.+)$")
    ANSI = {"b": ("1", "22"), "i": ("3", "23"), "s": ("9", "29")}  # a mark's terminal codes: on, off
    OUTPUT_LINES = 30  # of a command's output, the lines a terminal shows (the start; the rest is counted)

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
                          lambda tag, inner: f"<{tag}>{inner}</{tag}>", keep,
                          lambda words, url: f'<a href="{html.escape(url)}">{html.escape(words, quote=False)}</a>')

    @classmethod
    def ansi(cls, text):
        """The text as a terminal shows it: bold, italic, struck and code in its colours, a block indented,
        a command's output dimmed (its first OUTPUT_LINES lines). Plain where colour is off (NO_COLOR, or
        not a terminal)."""
        if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
            return cls.plain(text)
        keep = cls.keeper()

        def block(found):
            body = found.group(2).rstrip("\n").splitlines()
            if found.group(1) == "output":
                more = len(body) - cls.OUTPUT_LINES
                body = body[:cls.OUTPUT_LINES] + ([f"… {more} more lines"] if more > 0 else [])
                return keep("\n".join(f"\033[2m  │ {line}\033[22m" for line in body))
            return keep("\n".join(f"    \033[36m{line}\033[39m" for line in body))
        return cls.render(text, block, lambda code: f"\033[36m{code}\033[39m", lambda plain: plain,
                          lambda tag, inner: f"\033[{cls.ANSI[tag][0]}m{inner}\033[{cls.ANSI[tag][1]}m", keep,
                          lambda words, url: f"\033[4m{words}\033[24m \033[2m({url})\033[22m")

    @classmethod
    def plain(cls, text):
        """The text with its marks taken off, for a chat that shows none."""
        keep = cls.keeper()
        return cls.render(text, lambda found: keep(found.group(2).rstrip("\n")), lambda code: code,
                          lambda plain: plain, lambda tag, inner: inner, keep, lambda words, url: f"{words} ({url})")

    @staticmethod
    def keeper():
        kept = []

        def keep(shown):
            kept.append(shown)
            return f"\x00{len(kept) - 1}\x00"
        keep.kept = kept
        return keep

    @classmethod
    def render(cls, text, block, code, plain, mark, keep, link):
        """text shown with block(fence match), code(text), plain(text), mark(tag, inner) and link(words, url);
        what is shown as it is (blocks, code, links, escaped marks) is kept apart until the rest is marked."""
        text = cls.FENCE_RE.sub(block, str(text))
        text = cls.LINK_RE.sub(lambda found: keep(link(found.group(1), found.group(2))), text)
        text = cls.ESCAPED_RE.sub(lambda found: keep(plain(found.group(1))), text)
        text = cls.CODE_RE.sub(lambda found: keep(code(found.group(1))), text)
        text = cls.ITEM_RE.sub("• ", plain(text))
        text = cls.HEADING_RE.sub(lambda found: mark("b", found.group(1)), text)
        for pattern, tag in cls.MARKS:
            text = pattern.sub(lambda found: mark(tag, found.group(1)), text)
        return re.sub("\x00(\\d+)\x00", lambda found: keep.kept[int(found.group(1))], text)


lit = Markup.literal


class Message:
    """One message to Tinker: who sent it (the chat to answer), what it says, and its id in the chat."""

    def __init__(self, chat, text, sender="", id=None):
        self.chat, self.text, self.sender, self.id = str(chat), (text or "").strip(), sender, id


class Channel:
    """What every kind of channel does. KIND names it; SECRETS are the settings asked for (and kept)
    apart, never shown. A dropped connection is not an error: receive returns nothing and tries again."""
    KIND = ""
    SECRETS = ()     # [(key, what to ask)]

    def __init__(self, name, settings, secrets):
        self.name, self.settings, self.secrets = name, settings, secrets
        self.allowed = {str(chat) for chat in settings.get("allowed", [])}
        self.pins = False  # may pin Tinker's status (the conversation's: it hears the chat, see pin)
        self.closed = False  # the person ended the conversation here (the console: exit)
        self.bare = True  # a conversation: commands are said as their words alone (said: no "tinker" before)

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

    def pin(self, chat, sent):
        """Pin a message as the one updated last (a channel that cannot pins nothing)."""

    def delete(self, chat, sent):
        """Take a message of Tinker's out of the chat (a channel that cannot leaves it)."""

    def said(self, text):
        """text as this interface takes commands: in a conversation without "tinker" before them."""
        return BARE_RE.sub("", text) if self.bare else text

    def latest(self, chat, sent):
        """Whether sent is the chat's newest message (a channel that cannot tell: it is)."""
        return True

    def show(self, chat, shown, text, done=False, bottom=None, pin=False):
        """One thing's message: text (Markup), with when it was updated as its footer, edited into the
        messages the thing has (shown: their ids, or None for a new thing); a new one only for what no
        longer fits, and one it no longer needs taken out. pin: pinned (Tinker's status, the one message
        pinned: a thing comes to the bottom of the chat instead). Its
        ids now. done: the thing's last update for now (the console shows it whole then). bottom (done, by
        default): where the person looks next, so a message that is no longer the newest is sent anew at
        the bottom (the one before taken out), seen and heard at once (an edit up the chat is not)."""
        shown = list(shown or [])
        if (done if bottom is None else bottom) and shown and not self.latest(chat, shown[-1]):
            for old in shown:
                self.delete(chat, old)
            shown = []
        parts = pieces(f"{self.said(text)}\n\n🕒 _updated {time.strftime('%H:%M:%S')}_")
        now = [self.edit(chat, shown[index], part) if index < len(shown) else self.send(chat, part)
               for index, part in enumerate(parts)]
        for extra in shown[len(parts):]:
            self.delete(chat, extra)
        if pin and self.pins and now and now[0] is not None:
            self.pin(chat, now[0])
        return now

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
        """Become the one Tinker listening on this channel: True once it is, False when another one
        listens. Held until this process ends, even when it crashes. holder: what this one is (its
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


class ConsoleChannel(Channel):
    """The terminal, as a channel: the lines the person types are its messages (exit, or Ctrl-D, ends it),
    and what Tinker says is printed, in the terminal's colours. A terminal cannot edit what it printed: a
    thing that goes on shows a dim line each time its headline (its title, and what it does now) changes, and is shown
    whole when it is done for now (only its new lines, when it grew by lines at its end since it was shown)."""
    KIND = "console"
    CHAT = "console"
    PROMPT = "› " if sys.stdin.isatty() else ""  # typed lines are asked for in a terminal; piped ones are not

    def __init__(self, bare=True):
        super().__init__("console", {"allowed": [self.CHAT]}, {})
        self.bare = bare  # a session: commands said as their words; said once (tinker COMMAND): as tinker ...
        self.lines, self.next_id, self.headlines, self.whole = queue.Queue(), 0, {}, {}
        self.reader, self.printing = None, threading.Lock()

    def who(self):
        return "this terminal"

    def receive(self, timeout):
        if self.reader is None:
            self.reader = threading.Thread(target=self.read, daemon=True)
            self.reader.start()
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return []
        if line is None or line.strip().lower() in EXIT:
            self.closed = True
            return []
        return [Message(self.CHAT, line, "you")] if line.strip() else []

    def read(self):
        while True:
            try:
                self.lines.put(input(self.PROMPT))
            except (EOFError, KeyboardInterrupt):
                self.lines.put(None)
                return

    def send(self, chat, text):
        self.print(Markup.ansi(self.said(text)))
        return self.new_id()

    def edit(self, chat, sent, text):
        self.print(Markup.ansi(self.said(text)))
        return sent

    def new_id(self):
        with self.printing:
            self.next_id += 1
            return self.next_id

    def show(self, chat, shown, text, done=False, bottom=None, pin=False):  # a terminal prints at its bottom
        sent, text = (shown or [None])[0] or self.new_id(), self.said(text)
        if done:
            self.headlines.pop(sent, None)
            before, self.whole[sent] = self.whole.get(sent), text
            grew = before is not None and text.startswith(before + "\n")
            self.print(Markup.ansi(text[len(before) + 1:].lstrip("\n") if grew else text))
            return [sent]
        lines = [line.strip() for line in Markup.plain(text).splitlines() if line.strip()]
        doing = next((line for line in lines if line.startswith("▸")), lines[1] if len(lines) > 1 else "")
        headline = " · ".join(line for line in (lines[:1] + [doing.split(" · ⏱")[0]]) if line)
        if self.headlines.get(sent) != headline:
            self.headlines[sent] = headline
            self.print(f"\033[2m⋯ {headline}\033[22m" if sys.stdout.isatty() and not os.environ.get("NO_COLOR")
                       else f"⋯ {headline}")
        return [sent]

    def print(self, text):
        """text printed above the prompt (the line being typed is shown again under it)."""
        out = sys.stdout
        with self.printing:
            if out.isatty():
                out.write("\r\033[K")
            out.write(text + "\n")
            if out.isatty() and self.reader is not None:
                out.write(self.PROMPT)
            out.flush()


def pieces(text, size=3500):
    """text in parts a chat message holds, split between lines."""
    parts, part = [], ""
    for line in text.splitlines():
        if part and len(part) + len(line) + 1 > size:
            parts.append(part)
            part = ""
        part = f"{part}\n{line}" if part else line[:size]
    return parts + [part] if part else parts


# === a task's one message ====================================================================================

class Progress:
    """What a run prints, kept short for a chat: one message kept up to date (the steps done, the one it is
    on with its clock, what the model is writing right now, how many spec points pass, the latest line) and,
    when the run ends, its summary with how long the task took."""
    PASS_RE = re.compile(r"tests: (\d+)/(\d+) criteria pass|^\s*C\d+ passes \((\d+)/(\d+) pass\)")
    END_RE = re.compile(r"^(Done|Stopped|Not done|Interrupted|Run \w+ is not finished|📋 Spec for)")
    TIMES_RE = re.compile(r"^(Log|Started|Total execution time): ")  # the terminal's, not the chat's
    RULE = "=" * 20
    SAID = ("✅", "✏️", "📁")  # a phase Tinker says itself (Markup), not one a run printed
    SPEC_RE = re.compile(r"📋 Spec for (.+?) \(run (\w+)\): (.+?)(?:; a person runs it with: (.+))?$")
    CHANGE_RE = re.compile(r"\s+([+~-]) (?:(C\d+) )?(.*)")
    POINT_RE = re.compile(r"\s+(C\d+)\s+(.*)")
    LIVE_RE = re.compile(r"^\s*\.\.\. (.+?)(?: \[[^\]]*\])?: (.+?), (\d+)s\s*$")  # a model call, as it goes
    NOISE_RE = re.compile(r"^(Run \w+ \(|model |reply limit|Project: |Project folder: |Started: |learned so far|"
                          r"Log: )")  # its header (where its folder is is Tinker's own)
    TOTAL_RE = re.compile(r"^Total execution time: ([\d.]+)s")
    ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    STEPS = (("Recall", "🧠 Recalling what Tinker has learned", "🧠 Recalled what Tinker has learned"),
             ("Plan: the spec, changed", "✏️ Changing the spec", "✏️ Spec changed"),
             ("Plan: the spec", "📝 Writing the spec", "📝 Spec written"),
             ("Plan: the design", "📐 Designing where the files meet", "📐 Design done"),
             ("Plan: review", "🔍 Reviewing the design", "🔍 Design reviewed"),
             ("Setup", "🔧 Setting up the test tool", "🔧 Test tool set up"),
             ("Spec tests", "🧪 Writing a test for every spec point", "🧪 Tests written"),
             ("Code", "💻 Writing the code", "💻 Code written"),
             ("Verify", "🔁 Fixing until every test passes", "🔁 Verified"),
             ("Tidy", "🧹 Tidying the code", "🧹 Code tidied"),
             ("README", "📖 Writing the README", "📖 README written"),
             ("Final review", "🏁 Final review, as a person runs it", "🏁 Final review done"),
             ("Lessons", "📒 Keeping lessons for the next projects", "📒 Lessons kept"),
             ("Reproduce the bug", "🐞 Showing the bug with a test", "🐞 Bug shown by a test"))  # (title, doing, done)

    def __init__(self, title, phase="🚀 Starting: the model loads", before=0.0):
        self.title, self.phase, self.passing, self.latest = title, phase, "", ""
        self.summary, self.in_summary, self.ended = [], False, False
        self.steps, self.step, self.step_began, self.live = [], None, None, ""  # done ones; the one it is on
        self.before, self.seconds = before, 0.0  # the task's time in its runs before; this run's

    def read(self, line):
        """Take one printed line; True when it changed what the progress message says."""
        line = self.ANSI_RE.sub("", line).rstrip()
        live, total = self.LIVE_RE.match(line), self.TOTAL_RE.match(line)
        if live:  # the model writing: what it writes now
            self.live = f"{live.group(1)}: {live.group(2)}"
            return True
        if total:
            self.seconds = float(total.group(1))
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
        step = next((step for step in self.STEPS if line.startswith(step[0])), None)
        if counts:
            numbers = [n for n in counts.groups() if n]
            self.passing = f"{numbers[0]}/{numbers[1]} spec points pass"
        elif step:  # a step prints its title alone on its line
            if self.step:
                self.steps.append(self.step[2])
            self.step, self.phase, self.step_began, self.live, self.latest = step, step[1], time.monotonic(), "", ""
        elif self.NOISE_RE.match(line):
            return False
        else:
            self.latest = first_words(line.split("] ", 1)[-1], 300)
        return True

    def text(self):
        clock = f" · ⏱ {human_duration(time.monotonic() - self.step_began)}" if self.step_began else ""
        doing = self.phase if self.phase.startswith(self.SAID) else f"**{lit(self.phase)}**"
        lines = [f"🔨 **{lit(self.title)}**"] + [f"✅ {step}" for step in self.steps] + [f"▸ {doing}{clock}"]
        lines += [f"⏳ _{lit(self.live)}_"] if self.live else []
        lines += [f"📊 {lit(self.passing)}"] if self.passing else []
        lines += [f"… {lit(self.latest)}"] if self.latest else []
        return "\n".join(lines)

    def took(self):
        """How long the task has taken, in its runs (the time a spec waits for the person left out)."""
        seconds = self.before + self.seconds
        return f"{human_duration(seconds)} ({round(seconds)} seconds)"

    def brief(self):
        """Markup: the title, with the phase and the passing points."""
        passing = f", {lit(self.passing)}" if self.passing else ""
        return f"**{lit(self.title)}** ({Markup.plain(self.phase)}{passing})"

    def ending(self):
        """The summary for the chat (Markup); a spec waiting for confirmation is the summary itself."""
        lines = [line for line in self.summary if line.strip() and not self.TIMES_RE.match(line)]
        if self.waiting():
            return "\n".join([shown for shown in map(self.spec_line, lines) if shown is not None]
                             + [f"⏱ _so far: {self.took()}_"])
        if not lines:
            return f"🏁 **{lit(self.title)}**"
        first = lines[0]
        done = first.startswith("Done") and "but" not in first
        mark = "✅" if done else "⏸" if re.match(r"(Interrupted|Run \w+ is not finished|Planned)", first) else "⚠️"
        took = f"⏱ **Done in {self.took()}**" if first.startswith("Done") else f"⏱ _so far: {self.took()}_"
        return "\n".join([f"🏁 **{lit(self.title)}**", f"{mark} **{lit(first)}**", took]
                          + [self.done_line(line) for line in lines[1:]])

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
        if line.startswith("Its folder: "):  # its name: where Tinker keeps it is Tinker's own
            return f"📁 `{Path(line.split(': ', 1)[1].split('   (', 1)[0]).name}`"
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
        if label in ("Run it", "Settings") and value:  # in its folder: where that is is Tinker's own
            return (f"▶️ {label}: `{re.sub(r'^cd .+? && ', '', value)}`" if label == "Run it"
                    else f"⚙️ {label}: {lit(value)}")
        return lit(line)

    def waiting(self):
        """The run ended with its spec waiting for the person to confirm or amend it."""
        return any(line.startswith("📋 Spec for") for line in self.summary)


class Reporter:
    """Progress shown on the channels: the task's one message per chat (sent: kept from the task's run
    before), edited at most every EDIT_EVERY seconds (a change that came sooner is shown once that much has
    passed), and its summary edited into it when the run ends."""

    def __init__(self, channels, title, shared=None, sent=None, phase="🚀 Starting: the model loads", keep="",
                 before=0.0, on_step=None):
        self.channels, self.progress = channels, Progress(title, phase, before)
        self.on_step = on_step  # told when the task goes on to its next step (Tinker's status follows it)
        self.keep = keep  # what stays at the top of the task's message (its confirmed spec), above its progress
        self.sent, self.last_edit, self.lock = ({} if sent is None else sent), 0.0, threading.Lock()
        self.shared = shared  # a file the progress is kept in too, for the Tinker listening (running_elsewhere)
        self.quiet = False  # the run stops for an emergency and goes on: no summary
        self.pending, self.ended = False, False  # a change not shown yet; the run ended
        self.fresh = True  # its first update: the task (re)started from the person's message, below which it shows
        threading.Thread(target=self.keep_up, daemon=True).start()

    def line(self, text):
        with self.lock:
            if self.progress.read(text):
                self.pending = True
                if time.monotonic() - self.last_edit >= EDIT_EVERY:
                    self.update()

    def keep_up(self):
        """A change that came sooner than EDIT_EVERY is shown once that much has passed; the clock of the
        step it is on moves on every CLOCK_EVERY."""
        while not self.ended:
            time.sleep(EDIT_EVERY)
            with self.lock:
                if not self.ended and (self.pending or time.monotonic() - self.last_edit >= CLOCK_EVERY):
                    self.update()

    def update(self):
        self.last_edit, self.pending = time.monotonic(), False
        self.say(self.progress.text())
        if self.on_step:
            self.on_step()
        if self.shared:
            try:
                self.shared.parent.mkdir(parents=True, exist_ok=True)
                self.shared.write_text(json.dumps({"title": self.progress.title, "brief": self.progress.brief(),
                                                   "text": self.progress.text()}))
            except OSError:
                pass

    def say(self, text, done=False):
        """text shown in the task's message (Markup), under what it keeps; done: its last update. Its first
        update and its last come to the bottom of the chat (the task (re)started, or done: look here)."""
        text = f"{self.keep}\n\n{text}" if self.keep else text
        bottom, self.fresh = done or self.fresh, False
        for channel in self.channels:
            for chat in channel.allowed:
                key = (channel.name, chat)
                self.sent[key] = channel.show(chat, self.sent.get(key), text, done, bottom)

    def finish(self):
        with self.lock:
            self.ended = True
            if self.progress.summary and not self.quiet:
                self.say(self.progress.ending(), done=True)
            else:
                self.update()


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


# === what a chat asks for: a chat, a command line, a research ================================================

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

    def answer(self, channel, message, now, waiting=lambda: "", write=None, on_shown=None):
        """The reply to message, sent when written (in a thread, so `stop` is heard meanwhile). now(): what
        runs now, [{title, brief, text}]; waiting(): a spec that waits for the person; write: what writes the
        reply (channel, message, context, tokens -> text), the chat's own by default; on_shown(ids): the
        ids of the reply's message, once it is written. When something runs, or the reply is not
        written in BUSY_AFTER seconds, a note says so at once, updated every BUSY_EDIT_EVERY seconds with the
        progress; the reply then takes its place."""
        threading.Thread(target=self.answering, args=(channel, message, now, waiting, write or self.reply,
                                                      on_shown), daemon=True).start()

    def answering(self, channel, message, now, waiting, write_reply, on_shown):
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
            note = channel.show(message.chat, None, self.busy_note(running))
            while not written.wait(BUSY_EDIT_EVERY):
                note = channel.show(message.chat, note, self.busy_note(now()))
        note = channel.show(message.chat, note, reply[0] if reply else "⚠️ **The model did not answer.**", done=True)
        if on_shown:
            on_shown(note)

    @staticmethod
    def busy_note(running):
        if not running:
            lines = ["⏳ **The model is busy or loading.** I'll answer here as soon as it is free."]
        else:
            lines = ["⏳ **Busy** with " + "; ".join(r["brief"] for r in running) + ".",
                     "I'll answer here after the current step.", "🚨 An answer now: `emergency MESSAGE`"]
        return "\n".join(lines)

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
    message runs it; anything else drops it. It runs in the conversation's folder, in the person's login
    shell, with no input, for up to COMMAND_TIMEOUT seconds; its exit code and output (the
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
    SHELL = os.environ.get("SHELL") or "/bin/sh"  # the person's login shell: their PATH, their tools
    TELL_SYSTEM = ("Tell the person what this command's output says, for a chat read on a phone: the answer to "
                   "what they asked first, then only what matters, in a few short lines. Format for meaning, in "
                   "Markdown: **bold** for the key values, `code` for names and paths, \"- \" for items; an emoji "
                   "only where it says something with less (⚠️ a problem or something nearly full, ✅ all fine, "
                   "💾 disk, 📁 folders, 🌐 network, ⏱ time). Never a heading or a table; never anything the "
                   "output does not show; never the command again.")

    def __init__(self, talk, folder):
        self.talk, self.folder = talk, folder
        self.proposed = {}  # (channel, chat) -> {line, asked, shown} waiting for confirm (WRITING meanwhile)
        self.shell = self.SHELL

    def name(self, channel, message, now):
        """Have the model name the command (the reply comes as a chat's, busy note and all)."""
        key = (channel.name, message.chat)
        self.proposed[key] = self.WRITING

        def shown(ids):
            if isinstance(self.proposed.get(key), dict):
                self.proposed[key]["shown"] = ids
        self.talk.answer(channel, message, now, write=self.propose, on_shown=shown)

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
        self.proposed[key] = {"line": line, "asked": message.text, "shown": None}
        warning = ("\n⚠️ **It changes or deletes things:** read it before you confirm."
                   if self.CHANGES_RE.search(line) else "")
        return (f"🖥 **Run this?**\n```\n{Markup.fenced(line)}\n```\n{lit(does)}{warning}\n"
                f"👉 `confirm` to run it · anything else cancels it")

    def take(self, channel, chat):
        """The command waiting for this chat's next message (WRITING while it is named; None when none)."""
        key = (channel.name, chat)
        line = self.proposed.get(key)
        if line != self.WRITING:
            self.proposed.pop(key, None)
        return line

    def drop(self, channel, chat, proposed):
        """A command not confirmed: its message says it was not run."""
        channel.show(chat, proposed["shown"], f"🚫 _Not run:_ `{proposed['line']}`", done=True)

    def run(self, channel, chat, proposed):
        """Run the command, in its message: its output comes at once, folded, and what it says once the
        model has read it."""
        line, asked = proposed["line"], proposed["asked"]
        sent = channel.show(chat, proposed["shown"], f"▶️ **Running** `{line}` …")
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
        tells = bool(output) and bool(self.talk.model())
        sent = channel.show(chat, sent, f"{head}\n{folded}", done=not tells)
        if not tells:
            return
        try:
            told = self.talk.ask(channel, chat, [{"role": "system", "content": self.TELL_SYSTEM},
                                                 {"role": "user", "content": f"They asked: {asked}\nCommand: {line}\n"
                                                  f"Exit code: {code}\nOutput:\n{output[-COMMAND_READ:]}"}],
                                 CHAT_REPLY_TOKENS)
        except OllamaError:
            told = ""
        channel.show(chat, sent, f"{head}\n{told}\n{folded}" if told else f"{head}\n{folded}", done=True)


class Research:
    """research TOPIC: an answer from the web, with its sources. One quick call names the searches (up to
    RESEARCH_SEARCHES), the pages they find are read in turn (the first of each search, then the second
    ..., up to RESEARCH_PAGES; of each, the parts most about the topic), and one call writes the answer
    from them alone, each claim citing its page. One message says how far it got, and becomes the answer.
    (tinker learn researches a topic in depth, and keeps it for the builds.)"""
    SEARCHES_SYSTEM = ("The person wants to know about a topic. Give the web searches that together answer it "
                       "best: two or three, each looking for something different, in the topic's language. Today "
                       "is {today}, and you were trained well before it: never assume what the latest version, "
                       "release or event is, or name one; for something recent, search for it as the latest, "
                       "with the year {year}. Reply with ONLY JSON: {{\"searches\": [\"...\", \"...\"]}}")
    ANSWER_SYSTEM = ("Answer what the person asks from these web pages alone, for a chat read on a phone: the "
                     "answer itself first, in a line or two, then only what matters, in short lines. Today is "
                     "{today}: what you were trained on may be out of date, so trust the pages, the newest first, "
                     "and say so when they may be older than what is asked. Each claim "
                     "cites its page as [n]. Where the pages disagree, say so; where they say nothing, say that "
                     "too; never anything they do not say. Format for meaning, in Markdown: **bold** for the key "
                     "facts and numbers, *italic* for an aside, `code` for exact names, \"- \" for items; an emoji "
                     "only where it says something with less (✅ settled, ⚠️ a caveat or a disagreement, 📅 a "
                     "date, 💡 a tip). Never a heading or a table, and no list of sources (it is added).")

    def __init__(self, talk):
        self.talk = talk

    def answer(self, channel, chat, topic, now):
        """Research topic, in a thread: its message kept up to date, then the answer in it."""
        threading.Thread(target=self.researching, args=(channel, chat, topic, now), daemon=True).start()

    def researching(self, channel, chat, topic, now):
        head = f"🔎 **{lit(topic)}**"
        sent = channel.show(chat, None, f"{head}\n⏳ _working out what to search_")
        internet = Internet()
        if not internet.available():
            channel.show(chat, sent, f"{head}\n⚠️ **The web is not usable:** {lit(internet.why())}", done=True)
            return
        if not self.talk.model():
            channel.show(chat, sent, f"{head}\n⚠️ **No model is in use yet:** `tinker use-model NAME`", done=True)
            return
        busy = "; ".join(r["brief"] for r in now())
        waits = f"\n⏳ _the model is busy with_ {busy}_: it answers after the current step_" if busy else ""
        try:
            searches = self.searches(channel, chat, topic)
            searching = " · ".join(f"`{q}`" for q in searches)
            sent = channel.show(chat, sent, f"{head}\n🌐 _searching:_ {searching}{waits}")
            pages = self.read(internet, searches)
            if not pages:
                channel.show(chat, sent, f"{head}\n🤷 **Nothing found on the web** for it: {lit(internet.why())}",
                             done=True)
                return
            sent = channel.show(chat, sent, f"{head}\n📖 _read {len(pages)} pages; writing the answer_" + waits)
            told = self.talk.ask(channel, chat, [
                {"role": "system", "content": self.ANSWER_SYSTEM.format(today=time.strftime("%d %B %Y"))},
                {"role": "user", "content": f"The person asks: {topic}\n\n" + "\n\n".join(
                    f"[{n}] {page['title']} ({page['url']})\n{page['content']}" for n, page in enumerate(pages, 1))}],
                CHAT_REPLY_TOKENS)
        except OllamaError as exc:
            channel.show(chat, sent, f"{head}\n⚠️ **The model did not answer:** {lit(first_words(exc))}", done=True)
            return
        sources = "\n".join(f"[{n}] [{page['title'][:70] or page['url']}]({page['url']})"
                             for n, page in enumerate(pages, 1))
        channel.show(chat, sent, f"{head}\n{told}\n\n📚 **Sources**\n{sources}", done=True)

    def searches(self, channel, chat, topic):
        """The web searches the model names for the topic (the topic itself when it names none)."""
        data = json_in(self.talk.ask(channel, chat, [
            {"role": "system", "content": self.SEARCHES_SYSTEM.format(today=time.strftime("%d %B %Y"),
                                                                    year=time.strftime("%Y"))},
            {"role": "user", "content": topic}], COMMAND_TOKENS))
        found = [str(q).strip() for q in data.get("searches") or [] if str(q).strip()]
        return list(dict.fromkeys(found))[:RESEARCH_SEARCHES] or [topic]

    def read(self, internet, searches):
        """[{title, url, content}] of the pages the searches find, in turn (a page that cannot be read
        gives the search's own excerpt), each cut to the parts most about what was searched."""
        found = [internet.search(query, RESEARCH_PAGES) or [] for query in searches]
        order, seen = [], set()
        for rank in range(RESEARCH_PAGES):
            for results in found:
                if rank < len(results) and results[rank]["url"] not in seen:
                    seen.add(results[rank]["url"])
                    order.append(results[rank])
        pages, wanted = [], set(re.findall(r"[a-z0-9]{3,}", " ".join(searches).lower()))
        for result in order[:RESEARCH_PAGES]:
            page = internet.fetch(result["url"]) or result
            content = self.most_about(page.get("content") or result.get("content", ""), wanted)
            if content.strip():
                pages.append({"title": page.get("title") or result["title"], "url": result["url"], "content": content})
        return pages

    @staticmethod
    def most_about(text, wanted):
        """Of a page, the paragraphs that share the most words with what was searched, in page order,
        up to RESEARCH_PAGE_CHARS."""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[#*-] )", text) if len(p.strip()) > 40]
        score = lambda p: len(wanted & set(re.findall(r"[a-z0-9]{3,}", p.lower())))
        best, size = set(), 0
        for index in sorted(range(len(paragraphs)), key=lambda i: -score(paragraphs[i])):
            if size + len(paragraphs[index]) > RESEARCH_PAGE_CHARS:
                continue
            best.add(index)
            size += len(paragraphs[index])
        return "\n\n".join(paragraphs[i] for i in sorted(best)) or text[:RESEARCH_PAGE_CHARS]


# === tasks run from a terminal ===============================================================================

class Attached:
    """A task run from a terminal, reporting to the channels: its progress message, kept in RUNNING_DIR too,
    for the conversation (which reads the chat) to answer status from. That stops it with STOP, as Ctrl-C
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
        channels = chat_channels()
    except SystemExit as exc:
        print(f"  note: channels left out: {exc}")
        return None
    return Attached(channels, title).start() if channels else None


def chat_channels():
    """The enabled chat channels (the channels plugin's), made: none without the plugin, or when a run is told
    not to report (one the conversation runs reports through it)."""
    try:
        from .channels import Channels
    except ImportError:
        return []
    return Channels.enabled()


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


def signal_task(pid, signum):
    """Send a task run from a terminal a signal (PAUSE or STOP)."""
    try:
        os.kill(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


# === the conversation ========================================================================================

class Desk:
    """The conversation, one Tinker serving its channels at once: the terminal (tinker, with no command) and
    the enabled chats (tinker channel start serves the chats alone). Every command of tinker's works here: one
    task runs at a time, as its own tinker (which does not report too), its progress in its one message; one
    that deletes shows what it deletes first, and waits for confirm. Tasks run from a terminal are served too
    (running_elsewhere). Anything that is not a command is a normal chat."""
    COMMANDS = tuple(dict.fromkeys(OWN + LONG + QUICK + DELETES))
    RUN_RE = re.compile(r"^Run ([0-9a-f]{3,}) \(")  # a run's first line: its ID
    FOLDER_LINE_RE = re.compile(r"^Project: (/.+)$")  # its header's: its folder

    def __init__(self, channels, root):
        self.channels, self.root = channels, Path(root).resolve()
        self.process = self.reporter = self.task = None  # task: {arguments, folder, title, run} of the last one
        self.lock, self.talk = threading.Lock(), Talk(commands_brief())
        self.shell = Shell(self.talk, self.root)
        self.research = Research(self.talk)
        self.urgent = False  # an emergency is being answered: no task starts meanwhile
        self.waiting = None  # {run, folder, title, spec} of the spec that waits for confirm or amend
        self.boards = {}  # run -> its task's messages ({(channel, chat): ids}), kept from one of its runs to the next
        self.asking = {}  # (channel, chat) -> {words, shown}: a command that deletes, waiting for confirm
        self.spent = {}  # run -> the seconds its runs took so far (a task's time: done in ...)
        self.consoles = [c for c in channels if isinstance(c, ConsoleChannel)]
        self.said_status = None  # the status said last (a status said again only when it changed)
        self.echo = not self.consoles  # a task's printed lines go to the terminal too (not to a console's)

    def run(self):
        for channel in [c for c in self.channels if c not in self.consoles]:
            channel.pins = True  # it hears the chat: it can tell the notes of its pins from the person's messages
            if not channel.claim({"pid": os.getpid()}):
                held = f"{channel.name} is listened to by another Tinker (process {channel.holder().get('pid', '?')})"
                if not self.consoles:
                    raise SystemExit(f"tinker: {held}: stop it first")
                print(f"  note: {held}: this one talks here only")
                self.channels.remove(channel)
        self.talk.warm()
        for channel in self.channels:
            channel.skip_waiting()
        self.say_status(anew=True)
        if not self.consoles:
            names = ", ".join(c.name for c in self.channels)
            print(f"Listening on {names}; projects go in {self.root}. Ctrl-C stops.")
        threads = [threading.Thread(target=self.listen, args=(c,), daemon=True) for c in self.channels]
        for thread in threads:
            thread.start()
        try:
            while any(t.is_alive() for t in threads) and not any(c.closed for c in self.channels):
                time.sleep(0.5)
            raise KeyboardInterrupt  # the person ended it (exit)
        except KeyboardInterrupt:
            self.stop_task()
            self.say_status("💤 **Tinker** · not listening")
            if not self.consoles:
                print("\nStopped listening.")

    def status(self):
        """What Tinker is doing now, as its status says it (Markup): working, waiting for the person, or idle."""
        if self.busy():
            progress = self.reporter.progress
            passing = f" · {lit(progress.passing)}" if progress.passing else ""
            return f"🔄 **Tinker** · working on {lit(progress.title)}\n▸ {Markup.plain(progress.phase)}{passing}"
        if self.waiting:
            then = "`confirm` · `rename NAME`" if self.waiting.get("stage") == "name" else "`confirm` · `amend WHAT`"
            return f"🙋 **Tinker** · {lit(self.waiting['title'])} waits for you: {then}"
        elsewhere = running_elsewhere()
        if elsewhere:
            return "🔄 **Tinker** · working in a terminal: " + "; ".join(r["brief"] for r in elsewhere)
        return "🟢 **Tinker** · idle: send a task"

    def say_status(self, text=None, anew=False):
        """Tinker's status, one message in each chat, pinned (the console: said at its start and end): text,
        else what it does now, said only when it changed. anew (it starts listening): the one from before is
        taken out, and this one sent at the bottom; their ids are kept for the next start (STATUS_FILE)."""
        text = text or self.status()
        if text == self.said_status and not anew:
            return
        self.said_status = text
        try:
            kept = json.loads(STATUS_FILE.read_text())
        except (OSError, ValueError):
            kept = {}
        kept = kept if isinstance(kept, dict) else {}
        for channel in self.channels:
            if channel in self.consoles:
                if anew or text.startswith("💤"):
                    channel.show(ConsoleChannel.CHAT, None, text, done=True)
                continue
            mine = kept.setdefault(channel.name, {})
            for chat in channel.allowed:
                before = mine.get(chat)
                if anew and before:
                    channel.delete(chat, before)
                shown = channel.show(chat, None if anew or not before else [before], text, bottom=False, pin=True)
                mine[chat] = shown[0] if shown else None
        try:
            STATUS_FILE.write_text(json.dumps(kept))
        except OSError:
            pass

    def listen(self, channel):
        while not channel.closed:
            for message in channel.listened(timeout=1 if channel in self.consoles else 20):
                if channel not in self.consoles:
                    print(f"[{time.strftime('%H:%M:%S')}] {channel.name}: {message.text}")
                self.hear(channel, message)

    def hear(self, channel, message):
        """One message: the answer to what waits for it, else the command it gives (the slow ones in a
        thread, so the next message is heard meanwhile), else a chat."""
        command = command_of(message.text, self.COMMANDS)
        if command == "confirm" and message.id:  # a one-time answer: it would only make the chat grow
            channel.delete(message.chat, message.id)
        if self.settle(channel, message, command):
            return
        if not command:
            self.talk.answer(channel, message, self.now, lambda: (self.waiting or {}).get("spec", ""))
            return
        if command == "run":
            threading.Thread(target=self.run_app, args=(channel, message.chat, message.text.split(None, 1)[1:]),
                             daemon=True).start()
            return
        if command in ("research", "command", "emergency"):
            if len(message.text.split()) == 1:
                channel.send(message.chat, USAGE[command])
            elif command == "research":
                self.research.answer(channel, message.chat, after_word(message).text, self.now)
            elif command == "command":
                self.shell.name(channel, after_word(message), self.now)
            else:
                threading.Thread(target=self.emergency, args=(channel, after_word(message)), daemon=True).start()
            return
        reply = self.answer(message, channel)
        if reply:
            channel.send(message.chat, reply)

    def settle(self, channel, message, command):
        """What waits for this very message (a command that deletes, a command line): done when the message
        is confirm, else left undone. True when the message was that confirm."""
        confirm = command == "confirm" and len(message.text.split()) == 1
        asking = self.asking.pop((channel.name, message.chat), None)
        if asking and confirm:
            threading.Thread(target=self.delete, args=(channel, message.chat, asking), daemon=True).start()
            return True
        if asking:
            channel.show(message.chat, asking["shown"], f"🚫 _Not done:_ `{' '.join(asking['words'])}`", done=True,
                         bottom=False)
        proposed = self.shell.take(channel, message.chat)
        if proposed == Shell.WRITING and confirm:
            channel.send(message.chat, "⏳ The command is still being worked out: `confirm` once it is shown.")
            return True
        if proposed and confirm:
            threading.Thread(target=self.shell.run, args=(channel, message.chat, proposed), daemon=True).start()
            return True
        if proposed:
            self.shell.drop(channel, message.chat, proposed)
        return False

    def answer(self, message, channel):
        """What a command asks for, done; the reply (None when the reply comes as progress)."""
        text = message.text
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        command = words[0] = words[0].lower().lstrip("/")
        if command == "task":  # Tinker makes its folder in the projects folder, named after it
            if len(words) < 2:
                return USAGE["task"]
            request = " ".join(words[1:])
            return self.start_task(["task", request, "--confirm"], self.root, request)
        if command in ("confirm", "amend", "rename"):
            return self.decide(command, words)
        if command == "resume":
            if len(words) != 2:
                return USAGE["resume"]
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
            if command == "status" and len(words) == 1:  # at once: what the conversation knows itself
                return "\n\n".join([self.status()] + [r["text"] for r in self.now()])
            title = "📚 **Runs**" if command == "runs" else f"📊 **Status** {lit(' '.join(words[1:2]))}".rstrip()
            return f"{title}\n```\n{Markup.fenced(self.tinker([command] + words[1:2]))}\n```"
        if command == "help":
            return help_text(words[1:2])
        if command in EXIT:  # the console ends on its own word (ConsoleChannel); a chat asks first
            shown = channel.show(message.chat, None, "🛑 **Stop Tinker?** It cannot be started again from here.\n"
                                                     "👉 `confirm` to stop it · anything else keeps it", done=True)
            self.asking[(channel.name, message.chat)] = {"words": [command], "shown": shown}
            return None
        if command == "channel" and words[1:2] and words[1] in TERMINAL_ONLY:
            return f"🖥 `channel {words[1]}` asks in a terminal: `tinker\u00a0channel {words[1]}` there"
        if command in DELETES and "--yes" not in words:
            return self.ask_first(channel, message.chat, words)
        if command in LONG:
            return self.start_task(words, self.root, " ".join(words))
        shown = self.tinker(words)
        return f"⚙️ **{lit(' '.join(words))}**\n```\n{Markup.fenced(shown)}\n```"

    def ask_first(self, channel, chat, words):
        """A command that deletes: what it would delete (tinker's own list of it), waiting for confirm."""
        preview = "\n".join(line for line in self.tinker(words).splitlines() if not line.startswith("Nothing deleted"))
        shown = channel.show(chat, None, f"🗑 **{lit(' '.join(words))}**\n```\n{Markup.fenced(preview)}\n```\n"
                                         f"👉 `confirm` to delete it · anything else cancels it", done=True)
        self.asking[(channel.name, chat)] = {"words": words, "shown": shown}
        return None

    def delete(self, channel, chat, asking):
        """A command that deletes (or exit), confirmed: done (a restart as a task), in its message."""
        if asking["words"][0] in EXIT:
            channel.show(chat, asking["shown"], "🛑 **Stopping Tinker.**", done=True)
            for each in self.channels:
                each.closed = True
            return
        words = asking["words"] + ["--yes"]
        if words[0] in LONG:
            refused = self.start_task(words, self.root, " ".join(asking["words"]))
            if refused:
                channel.show(chat, asking["shown"], refused, done=True)
            return
        channel.show(chat, asking["shown"], f"🗑 **{lit(' '.join(asking['words']))}**\n```\n"
                                            f"{Markup.fenced(self.tinker(words))}\n```", done=True)

    def decide(self, command, words):
        """confirm [ID]: build the spec that waits (its folder name is asked once first: confirm again, or
        rename NAME, builds it); amend WHAT: change it (it waits again); rename NAME: rename its folder."""
        waiting = self.waiting or {}
        if command == "amend" and len(words) == 1:
            return USAGE["amend"]
        if command == "rename":
            return self.rename(waiting, words)
        if command == "confirm" and len(words) == 1 and waiting.get("stage") == "spec":
            waiting["stage"] = "name"
            self.on_board(waiting, self.asked(waiting))
            return None
        run = words[1] if command == "confirm" and len(words) == 2 else waiting.get("run")
        if not run:
            return "🤷 No spec waits for confirmation: start one with `task WHAT TO BUILD`"
        mine = waiting.get("run") == run
        folder, title = (waiting["folder"], waiting["title"]) if mine else (self.root, f"run {run}")
        if command == "confirm":
            return self.on_task(waiting, self.start_task(["resume", run], folder, title, "✅ **Confirmed:** building it",
                                                         keep=self.kept(waiting) if mine else ""))
        change = " ".join(words[1:])
        return self.on_task(waiting, self.start_task(["amend", run, change], folder, title,
                                                     f"✏️ **Changing the spec:** {lit(change)}"))

    def on_task(self, waiting, said):
        """What was said about the task whose spec waits (a refusal), in its message rather than apart, under
        what it waits for."""
        if said and waiting:
            self.on_board(waiting, f"{self.asked(waiting)}\n\n{said}")
            return None
        return said

    @staticmethod
    def asked(waiting):
        """The waiting spec, with what it waits for: its folder's name, once the spec is confirmed."""
        if waiting.get("stage") != "name":
            return waiting["spec"]
        return (f"{waiting['spec']}\n\n📁 **Its folder:** `{Path(waiting['folder']).name}`\n👉 `confirm` to build it "
                f"there · `rename NAME` to name it otherwise (it builds then)")

    @staticmethod
    def kept(waiting):
        """Of a waiting spec, what stays in the task's message once it is built: all but what to send next."""
        return "\n".join(line for line in waiting["spec"].splitlines() if not line.startswith(("👉", "⏱"))).strip()

    def on_board(self, waiting, text):
        """text shown in the message of the task whose spec waits (Tinker's status follows it)."""
        board = self.boards.setdefault(waiting["run"], {})
        for channel in self.channels:
            for chat in channel.allowed:
                board[(channel.name, chat)] = channel.show(chat, board.get((channel.name, chat)), text, done=True)
        self.say_status()

    def rename(self, waiting, words):
        """rename NAME: the waiting project's folder renamed (tinker rename); asked for its name, it builds."""
        if len(words) != 2:
            return USAGE["rename"]
        if not waiting:
            return "🤷 No spec waits for confirmation, so no folder can be renamed (once built, it stays)."
        if self.busy():
            return self.on_task(waiting, "⏳ A task is running: wait for it, or `stop` it first.")
        done = subprocess.run(tinker_command() + ["rename", waiting["run"], words[1]], cwd=self.root, text=True,
                              capture_output=True, stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1"),
                              timeout=60)
        if done.returncode:
            why = first_lines((done.stdout + done.stderr).strip(), 10).splitlines()[-1]
            return self.on_task(waiting, f"⚠️ **Not renamed:** {lit(why)}")
        old, waiting["folder"] = waiting["folder"], Path(waiting["folder"]).parent / words[1]
        waiting["title"] = f"{words[1]}: {waiting['title'].split(': ', 1)[-1]}"
        waiting["spec"] = waiting["spec"].replace(f"📁 `{Path(old).name}`", f"📁 `{words[1]}`")
        if waiting.get("stage") != "name":
            self.on_board(waiting, f"{waiting['spec']}\n\n📁 **Renamed:** its folder is `{words[1]}`")
            return None
        return self.on_task(waiting, self.start_task(["resume", waiting["run"]], waiting["folder"], waiting["title"],
                                                     f"✅ **Renamed** to `{words[1]}`: building it",
                                                     keep=self.kept(waiting)))

    def run_app(self, channel, chat, said):
        """run [PROJECT] ARGS... [<<< INPUT]: a built project run the way a person runs it (its run command,
        in its folder and environment), with the person's arguments (and input), and what it printed. The
        project is the one named (its folder's name or its run's ID), else the one built last."""
        given = said[0] if said else ""
        words, _, fed = given.partition("<<<")
        stdin = fed.strip() + "\n" if "<<<" in given else None
        try:
            words = shlex.split(words)
        except ValueError:
            words = words.split()
        project, args = (built_project(words[0]), words[1:]) if words else (None, [])
        if project is None:  # no project named: the one built last, and every word is an argument
            project, args = built_project(""), words
        if project is None:
            channel.show(chat, None, "🤷 **No project is built yet:** `task WHAT TO BUILD` builds one", done=True)
            return
        workdir, name = Path(project["workdir"]), Path(project["workdir"]).name
        toolchain = Toolchain((JsonFile.read(workdir / STATE_DIR / "spec.json", {}) or {}).get("toolchain"))
        if not toolchain.run:
            channel.show(chat, None, f"🤷 **{lit(name)} has no run command:** it is not a program a person runs",
                         done=True)
            return
        line = " ".join([toolchain.run] + [shlex.quote(arg) for arg in args])
        shown = channel.show(chat, None, f"▶️ **{lit(name)}** `{line}` …")
        try:
            done = subprocess.run([Shell.SHELL, "-lc", toolchain.shell(line)], cwd=workdir, text=True,
                                  capture_output=True, input=stdin, stdin=None if stdin else subprocess.DEVNULL,
                                  timeout=COMMAND_TIMEOUT)
            code, output = done.returncode, (done.stdout + done.stderr).strip()
        except subprocess.TimeoutExpired as exc:
            code, output = f"stopped after {COMMAND_TIMEOUT}s", (text_of(exc.stdout) + text_of(exc.stderr)).strip()
        except OSError as exc:
            code, output = "not started", str(exc)
        fed = f"\n_input:_ `{lit(stdin.strip())}`" if stdin else ""
        printed = f"```\n{Markup.fenced(output[-COMMAND_SHOWN:])}\n```" if output else "_(it printed nothing)_"
        channel.show(chat, shown, f"{'✅' if code == 0 else '❌'} **{lit(name)}** `{line}` · _exit {lit(code)}_{fed}\n"
                                  f"{printed}", done=True)

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
        lines, shown = [f"🚨 **Emergency:** {lit(message.text)}"], [None]

        def say(line, done=False):  # the emergency's one message, a line more each time
            lines.append(line)
            shown[0] = channel.show(message.chat, shown[0], "\n".join(lines), done)
        with self.lock:
            self.urgent, stopped = True, self.task if self.busy() else None
            if stopped:
                say(f"⏸ Stopping {lit(stopped['title'])} to answer: it is saved and goes on after.")
                self.reporter.quiet = True
                self.stop_task()
                try:
                    self.process.wait(STOP_WAIT)
                except subprocess.TimeoutExpired:  # it goes on by itself: answered after its step, as a chat
                    stopped = None
                    say(f"⚠️ The task did not stop in {STOP_WAIT}s; answering after its step.")
            paused = [run for run in running_elsewhere() if self.pause(say, run)]
        try:
            say(f"\n{self.talk.reply(channel, message)}\n", done=not (stopped or paused))
        finally:
            self.urgent = False
        if stopped:
            again = ["resume", stopped["run"]] if stopped["run"] else stopped["arguments"]
            self.start_task(again, stopped["folder"], stopped["title"], sent=self.reporter.sent,
                            keep=self.reporter.keep)
            say(f"▶️ **Going on** with {lit(stopped['title'])}", done=not paused)
        for run in paused:  # its file removed: the task goes on in its terminal
            (RUNNING_DIR / f"{run['pid']}.json").unlink(missing_ok=True)
            say(f"▶️ **Going on** with {lit(run['title'])} _(in its terminal)_", done=run is paused[-1])

    def pause(self, say, run):
        """A task run from a terminal, stopped for an emergency: True once it says it is paused."""
        say(f"⏸ Stopping {lit(run['title'])} to answer: it is saved and goes on after.")
        signal_task(run["pid"], PAUSE)
        ends = time.monotonic() + STOP_WAIT
        while time.monotonic() < ends:
            now = next((r for r in running_elsewhere() if r["pid"] == run["pid"]), None)
            if now is None or now.get("paused"):
                return now is not None
            time.sleep(1)
        say(f"⚠️ {lit(run['title'])} did not stop in {STOP_WAIT}s; answering after its step.")
        return False

    def start_task(self, arguments, folder, title, phase="🚀 Starting: the model loads", sent=None, keep=""):
        """Start a task as its own tinker. Its message is the one its run had (by its ID: sent, else the
        boards), keeps keep at its top, and says phase first (Markup when Tinker says it: ✅ confirmed ...);
        it is shown before the run's first line can show it, so it is one message."""
        run = arguments[1] if arguments[0] in ("resume", "amend") and len(arguments) > 1 else None
        sent = sent if sent is not None else self.boards.get(run) if run else None
        with self.lock:
            if self.busy():
                return "⏳ A task is running: wait for it, or `stop` it first."
            if self.urgent:
                return "🚨 An emergency is being answered: send it again in a moment."
            self.task = {"arguments": arguments, "folder": folder, "title": title, "run": run}
            self.reporter = Reporter(self.channels, title, sent=sent, phase=phase, keep=keep,
                                     before=self.spent.get(run, 0.0), on_step=self.say_status)
            with self.reporter.lock:
                self.reporter.update()
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
        return None

    def follow(self, process, reporter, task):
        """The task's printed lines to the terminal and the chat; its run and folder, once its header names
        them (the folder of a new task is named after it: the title says it from then on)."""
        for line in process.stdout:
            if self.echo:
                sys.stdout.write(line)
            run, folder = self.RUN_RE.match(line), self.FOLDER_LINE_RE.match(line.rstrip())
            if run and not task["run"]:
                task["run"] = run.group(1)
            if task["run"]:
                self.boards[task["run"]] = reporter.sent
            if folder and Path(folder.group(1)) != Path(task["folder"]):
                named, _, request = task["title"].partition(": ")  # a title names its folder at most once
                request = request if request and named == Path(task["folder"]).name else task["title"]
                task["folder"] = Path(folder.group(1))
                task["title"] = reporter.progress.title = f"{task['folder'].name}: {request}"
            reporter.line(line)
        process.wait()
        if task["run"]:
            self.spent[task["run"]] = reporter.progress.before + reporter.progress.seconds
        reporter.finish()
        if task["run"] and reporter.progress.waiting():
            self.waiting = {"run": task["run"], "folder": task["folder"], "title": task["title"],
                            "spec": reporter.progress.ending(), "stage": "spec"}
        self.say_status()

    def stop_task(self):
        if not self.busy():
            return False
        os.killpg(self.process.pid, signal.SIGINT)  # as Ctrl-C: the run saves itself
        return True

    def tinker(self, arguments):
        """What one of tinker's quick commands prints: its columns as a chat reads them (" | "), its first 40
        lines."""
        done = subprocess.run(tinker_command() + arguments, cwd=self.root, text=True, capture_output=True,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1"), timeout=120)
        return first_lines(Markup.columns((done.stdout + done.stderr).strip()) or "(nothing)", 40)


def session(root):
    """tinker with no command: the conversation in this terminal, and in the enabled chats too: one Tinker
    serving them all, so a task started in one is seen, stopped and answered from the others."""
    try:
        chats = chat_channels()
    except SystemExit as exc:
        print(f"  note: chats left out: {exc}")
        chats = []
    Desk([ConsoleChannel()] + chats, root).run()


def one_shot(command, text):
    """tinker chat|command|research|run|stop|emergency ...: one of the conversation's own commands, said once in
    this terminal (command asks there before it runs anything). None, or why it could not."""
    console, talk = ConsoleChannel(bare=False), Talk(commands_brief())
    message, chat = Message(ConsoleChannel.CHAT, text, "you"), ConsoleChannel.CHAT
    if command == "stop":
        elsewhere = running_elsewhere()
        for run in elsewhere:
            signal_task(run["pid"], STOP)
        console.show(chat, None, f"⏹ **Stopping** {'; '.join(lit(r['title']) for r in elsewhere)}: saved; `resume` "
                                 f"goes on later" if elsewhere else "💤 No task is running.", done=True)
        return None
    if not text and command != "run":
        return f"tinker {command} needs words after it, e.g. tinker {command} {'hello' if command == 'chat' else '...'}"
    if command == "chat":
        console.show(chat, None, talk.reply(console, message, "", CHAT_REPLY_TOKENS), done=True)
    elif command == "research":
        Research(talk).researching(console, chat, text, running_elsewhere)
    elif command == "emergency":
        Desk([console], Path.cwd()).emergency(console, message)
    elif command == "run":
        Desk([console], Path.cwd()).run_app(console, chat, [text])
    else:  # command
        shell = Shell(talk, Path.cwd())
        console.show(chat, None, shell.propose(console, message, "", COMMAND_TOKENS), done=True)
        proposed = shell.take(console, chat)
        if not proposed:
            return None
        if sys.stdin.isatty() and input("Run it? [y/N] ").strip().lower() in ("y", "yes"):
            shell.run(console, chat, proposed)
        else:
            console.show(chat, None, f"🚫 _Not run:_ `{proposed['line']}`", done=True)
    return None


def command_of(text, commands):
    """The command a message gives (one of commands, sent as its first word, with or without a /), or ""
    when it is chat. A word alone is a command; one of ONE_WORD with one word after it is, and one of
    ANY_WORDS with any words after it ("models are getting better" is a chat)."""
    words = text.split()
    first = words[0].lower().lstrip("/") if words else ""
    if first not in commands:
        return ""
    if len(words) == 1 or first in ANY_WORDS or (first in ONE_WORD and len(words) == 2):
        return first
    return ""


def after_word(message):
    """What a command word is followed by (the emergency's own message, the command described)."""
    return Message(message.chat, message.text.split(None, 1)[1], message.sender)


# === small helpers ===========================================================================================

def help_text(words=()):
    """tinker help (of one command: words), as a conversation shows it: tinker's own (Markup)."""
    try:
        done = subprocess.run(tinker_command() + ["help", *words], text=True, capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1", TINKER_FORMAT="markup"))
    except (OSError, subprocess.SubprocessError):
        return "❓ `help` lists what you can send"
    shown = (done.stdout + done.stderr).strip()
    return shown if not words else f"❓ **help {lit(' '.join(words))}**\n```\n{Markup.fenced(shown)}\n```"


def commands_brief():
    """The commands, as the chat's prompt names them: their synopses alone (the whole help would slow every
    reply)."""
    return "; ".join(re.findall(r"- `([^`]+)`", help_text())) or "help"


def tinker_command():
    """How to start Tinker again: the script this one is."""
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


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


def keep_typing(channel, chat, done):
    """The chat's "typing…" sign, shown until done is set."""
    while not done.is_set():
        channel.typing(chat)
        done.wait(TYPING_EVERY)


def first_words(text, size=160):
    return " ".join(str(text).split())[:size]


def built_project(word):
    """The run of the project word names (its folder's name, or its run's ID), as the run index keeps it
    ({id, workdir, task, status}); word "": the one built last. None when there is none."""
    projects = sorted((r for r in Runs.entries() if Path(str(r.get("workdir", ""))).is_dir()),
                      key=lambda r: str(r.get("updated", "")), reverse=True)
    if not word:
        return next((r for r in projects if r.get("status") == "done"), None)
    return next((r for r in projects if Path(r["workdir"]).name == word or len(word) >= 3
                 and str(r.get("id", "")).startswith(word)), None)


def first_lines(text, count):
    lines = text.splitlines()
    return "\n".join(lines[:count] + ([f"… {len(lines) - count} more lines"] if len(lines) > count else []))
