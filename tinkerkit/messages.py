import contextlib
lazy import shutil
lazy import sys
from dataclasses import dataclass

VARIATION_SELECTOR = "\ufe0f"


@dataclass(frozen=True)
class Message:
    kind: str
    text: str
    depth: int = 0
    icon: str = ""

    @property
    def line(self):
        return "   " * self.depth + (f"{self.icon}{space_after(self.icon)}{self.text}" if self.icon else self.text)


def space_after(icon):
    drawn_one_column_wide = icon.endswith(VARIATION_SELECTOR)
    return "  " if drawn_one_column_wide else " "


class Messages:
    ICONS = frozendict({"done": "✅", "warn": "⚠️", "fail": "❌", "retry": "🔁", "skip": "➖", "stop": "🛑",
                        "tip": "💡", "item": "•", "found": "💡"})
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        self.depth, self.followers, self.log, self.drawn, self.frame = 0, [], None, False, 0
        self.doing = ""
        self.last_step = ""

    def title(self, text, icon=""):
        if self.depth == 0:
            print()
        self.say("title", text, icon)

    @contextlib.contextmanager
    def part(self, text, icon=""):
        self.title(text, icon)
        with self.section():
            yield

    def text(self, text):
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
        self.say("found", text)

    def tally(self, text):
        self.tell(Message("tally", text, self.depth))

    def took(self, text, seconds):
        self.done(f"{text} ({human_duration(seconds)})")

    def summary(self, title, rows, icon="✨"):
        with self.part(title, icon):
            for row_icon, label, value in rows:
                self.info(f"{label}: {value}", row_icon)

    @contextlib.contextmanager
    def section(self):
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
        return self.doing or self.last_step or "Working on it"

    def end_live(self):
        if self.drawn:
            sys.__stdout__.write("\r\033[K")
            sys.__stdout__.flush()
            self.drawn = False

    def detail(self, text, **facts):
        if self.log is not None:
            self.log.add({"note": text, **facts})

    def follow(self, follower):
        self.followers.append(follower)

    def keep_log(self, log):
        self.log = log

    def tell(self, message):
        for follower in self.followers:
            try:
                follower(message)
            except Exception:
                pass

    def reset(self):
        self.__init__()


def human_duration(seconds):
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else (f"{minutes}m {secs}s" if minutes else f"{secs}s")


say = Messages()
