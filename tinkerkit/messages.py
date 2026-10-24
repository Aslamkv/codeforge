"""What Tinker tells the person: every message of every command, in one voice (say). Each message has a
kind, and its kind gives its icon (Messages.ICONS); a section nests what is said inside it. A message is
printed on the terminal and handed, as data, to whoever follows (a run's or a learn's tracker, which the
chats read). What is not for the person (token counts, settings tried, prompt budgets) is a detail: it goes
to the log only, never printed."""

import contextlib
lazy import shutil
lazy import sys
from dataclasses import dataclass

VARIATION_SELECTOR = "\ufe0f"


@dataclass(frozen=True)
class Message:
    """One thing said: its kind, its words, how deep in its sections, and its icon."""
    kind: str
    text: str
    depth: int = 0
    icon: str = ""

    @property
    def line(self):
        """The message as printed: indented by its depth, its icon before its words."""
        return "   " * self.depth + (f"{self.icon}{space_after(self.icon)}{self.text}" if self.icon else self.text)


def space_after(icon):
    drawn_one_column_wide = icon.endswith(VARIATION_SELECTOR)
    return "  " if drawn_one_column_wide else " "


class Messages:
    """Tinker's voice. title (or part, which nests what follows under it) opens a part of the output;
    text is plain output (a table, a list, help); step says what Tinker is doing; info states a fact;
    done, warn, fail, retry, skip, stop, tip and item say how something went, each with its own icon;
    summary is a titled list of (icon, label, value); live redraws one status line (on a terminal
    only); detail is kept in the log only. What goes on is the command's to say, in the person's words:
    working_on (what it works on now), tally (how far it got) and found (the latest thing it found out);
    the model never names what it does (thinking, writing): that means nothing to the person."""
    ICONS = frozendict({"done": "✅", "warn": "⚠️", "fail": "❌", "retry": "🔁", "skip": "➖", "stop": "🛑",
                        "tip": "💡", "item": "•", "found": "💡"})
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        self.depth, self.followers, self.log, self.drawn, self.frame = 0, [], None, False, 0
        self.doing = ""
        self.last_step = ""

    def title(self, text, icon=""):
        """A part of the output begins: at the top, a blank line sets it apart."""
        if self.depth == 0:
            print()
        self.say("title", text, icon)

    @contextlib.contextmanager
    def part(self, text, icon=""):
        """A titled part: what is said inside is nested under its title."""
        self.title(text, icon)
        with self.section():
            yield

    def text(self, text):
        """Plain output: a line of a table, a list or a help text."""
        self.say("text", text)

    def step(self, text, icon=""):
        self.last_step = text.rstrip(".…")
        self.say("step", text, icon)

    def info(self, text, icon=""):
        self.say("info", text, icon)

    def done(self, text):
        self.say("done", text)

    def warn(self, text):
        self.say("warn", text)

    def fail(self, text):
        self.say("fail", text)

    def retry(self, text):
        self.say("retry", text)

    def skip(self, text):
        self.say("skip", text)

    def stop(self, text):
        self.say("stop", text)

    def tip(self, text):
        self.say("tip", text)

    def item(self, text):
        self.say("item", text)

    def found(self, text):
        """The latest thing the command found out, as it is: "Small habits compound like interest"."""
        self.say("found", text)

    def tally(self, text):
        """How far the command got, in one line: "2/7 chapters · 14 things learned". For whoever follows
        only: the terminal says each part as it is done."""
        self.tell(Message("tally", text, self.depth))

    def took(self, text, seconds):
        """Something done, with how long it took: "✅ Tests written (1m 4s)"."""
        self.done(f"{text} ({human_duration(seconds)})")

    def summary(self, title, rows, icon="✨"):
        """A title, then each (icon, label, value) as its own line beneath it."""
        with self.part(title, icon):
            for row_icon, label, value in rows:
                self.info(f"{label}: {value}", row_icon)

    @contextlib.contextmanager
    def section(self):
        """What is said inside is nested one level deeper."""
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def say(self, kind, text, icon=""):
        message = Message(kind, text, self.depth, icon or self.ICONS.get(kind, ""))
        self.end_live()
        print(message.line)
        self.tell(message)

    def live(self, text):
        """The one status line of what goes on now, with its clock: redrawn in place on a terminal; piped
        (a chat following the command), never written, as its lines would reach the reader as lines of
        their own. Cut to the terminal's width, as a wrapped line cannot be redrawn."""
        message = Message("live", text, self.depth + 1, "⏳")
        if sys.__stdout__.isatty():
            width = shutil.get_terminal_size((80, 24)).columns
            drawn = Message("live", text, self.depth + 1, self.SPINNER[self.frame % len(self.SPINNER)])
            self.frame += 1
            sys.__stdout__.write("\r" + drawn.line[:max(width - 1, 10)] + "\033[K")
            sys.__stdout__.flush()
            self.drawn = True
        self.tell(message)

    @contextlib.contextmanager
    def working_on(self, doing):
        """Inside, the person is told the command works on doing ("Reading chapter 3 of 7: Habits"): the live
        line says it, and whoever follows hears it (a chat shows it as the step it is on)."""
        before = self.doing
        self.now_on(doing)
        try:
            yield
        finally:
            self.now_on(before)

    def now_on(self, doing):
        self.doing = doing
        self.tell(Message("working", doing, self.depth))
        if doing:
            self.live(doing)

    def going_on(self):
        """What the live line says goes on: what the command works on, else the step it said last."""
        return self.doing or self.last_step or "Working on it"

    def end_live(self):
        if self.drawn:
            sys.__stdout__.write("\r\033[K")
            sys.__stdout__.flush()
            self.drawn = False

    def detail(self, text, **facts):
        """What is not for the person: kept in the log (when one is kept), never printed."""
        if self.log is not None:
            self.log.add({"note": text, **facts})

    def follow(self, follower):
        """follower(message) is given every message from now on (live ones too)."""
        self.followers.append(follower)

    def keep_log(self, log):
        """The details go to log (a Log) from now on."""
        self.log = log

    def tell(self, message):
        for follower in self.followers:
            try:
                follower(message)
            except Exception:
                pass

    def reset(self):
        """Nobody follows and no log is kept: as a new process starts."""
        self.__init__()


def human_duration(seconds):
    """1762.08 -> '29m 22s'; 4000 -> '1h 6m 40s'; 42.5 -> '42s'."""
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else (f"{minutes}m {secs}s" if minutes else f"{secs}s")


say = Messages()
