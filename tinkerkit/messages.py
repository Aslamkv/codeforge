import contextlib
from dataclasses import dataclass
lazy import os
lazy import shutil
lazy import sys
lazy import time
lazy import unicodedata

VARIATION_SELECTOR = "\ufe0f"
TRUECOLOR_ACCENT = "\033[38;2;70;130;180m"
PALETTE_ACCENT = "\033[38;5;67m"
TRUECOLOR_GLOW = "\033[38;2;176;196;222m"
PALETTE_GLOW = "\033[38;5;153m"
GLOW_WIDTH = 3
TINKERING = "Tinkering…"
STYLES = frozendict(
    {
        "dim": "\033[2m",
        "bold": "\033[1m",
        "grey background": "\033[48;5;236m",
        "cyan": "\033[36m",
        "yellow": "\033[33m",
        "green": "\033[32m",
    }
)
RESET = "\033[0m"
CLEAR_TO_LINE_END = "\033[K"
LINE_START = "\r"
CLEAR_LINE = LINE_START + CLEAR_TO_LINE_END


@dataclass(frozen=True)
class Message:
    kind: str
    text: str
    depth: int = 0
    icon: str = ""

    @property
    def line(self):
        return "   " * self.depth + (
            f"{self.icon}{space_after(self.icon)}{self.text}" if self.icon else self.text
        )


def space_after(icon):
    drawn_one_column_wide = icon.endswith(VARIATION_SELECTOR)
    return "  " if drawn_one_column_wide else " "


class Messages:
    ICONS = frozendict(
        {
            "done": "✅",
            "warn": "⚠️",
            "fail": "❌",
            "retry": "🔁",
            "skip": "➖",
            "stop": "🛑",
            "tip": "💡",
            "item": "•",
            "found": "💡",
        }
    )
    STAR = "✻✶✳✢✳✶"

    def __init__(self):
        self.depth, self.followers, self.log, self.drawn, self.frame = 0, [], None, False, 0
        self.doing, self.last_step, self.began = "", "", None
        self.heard_tokens, self.thinking_since, self.thought_seconds = 0, None, None
        self.write, self.screen = print, None

    def title(self, text, icon=""):
        if self.depth == 0:
            self.write("")
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

    def tokens(self, count):
        self.heard_tokens += count
        self.tell(Message("tokens", str(count), self.depth))

    def thinking(self, now):
        if now:
            self.thinking_since = time.monotonic()
        elif self.thinking_since is not None:
            self.thought_seconds = time.monotonic() - self.thinking_since
            self.thinking_since = None
        self.tell(Message("thinking", "on" if now else "", self.depth))

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
        self.write(message.line)
        self.tell(message)

    def live(self, text):
        if self.began is None:
            self.began = time.monotonic()
        if self.screen:
            self.screen.show_live(text, self.began, **self.live_facts)
            self.drawn = True
        elif sys.__stdout__.isatty():
            self.draw_live(text)
        self.tell(Message("live", text, self.depth + 1, "⏳"))

    def draw_live(self, text):
        width = max(shutil.get_terminal_size((80, 24)).columns - 1, 10)
        indent = "   " * (self.depth + 1)
        drawn = live_line(text, self.began, self.frame, width, indent=indent, **self.live_facts)
        terminal_write(LINE_START + drawn + CLEAR_TO_LINE_END)
        self.frame += 1
        self.drawn = True

    @property
    def live_facts(self):
        return {
            "tokens": self.heard_tokens,
            "thinking": self.thinking_since is not None,
            "thought_seconds": self.thought_seconds,
        }

    @contextlib.contextmanager
    def working_on(self, doing):
        before = self.doing
        self.now_on(doing)
        try:
            yield
        finally:
            self.now_on(before)

    def now_on(self, doing):
        if not (doing and self.doing):
            self.began, self.heard_tokens = None, 0
        if doing:
            self.thought_seconds = None
        self.doing = doing
        self.tell(Message("working", doing, self.depth))
        if doing:
            self.live(doing)

    def going_on(self):
        return self.doing or self.last_step or "Working on it"

    def end_live(self):
        if not self.drawn:
            return
        if self.screen:
            self.screen.end_live()
        else:
            terminal_write(CLEAR_LINE)
        self.drawn = False

    def detail(self, text, **facts):
        if self.log is not None:
            self.log.add({"note": text, **facts})

    def write_through(self, write, screen=None):
        self.write, self.screen = write, screen

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


def terminal_write(text):
    sys.__stdout__.write(text)
    sys.__stdout__.flush()


def live_line(text, began, frame, width, stream=None, indent="", **facts):
    (head, _), doing = tinkering_pieces(time.monotonic() - began, text, **facts)
    word, rest = head[: len(TINKERING)], head[len(TINKERING) :]
    pieces = [
        (indent, None),
        (star_at(frame), "accent"),
        (" ", None),
        *glowing(word, frame),
        (rest, None),
        doing,
    ]
    return fitted(pieces, width, stream)


def glowing(word, frame):
    start = frame % (len(word) + GLOW_WIDTH) - GLOW_WIDTH
    end = max(start + GLOW_WIDTH, 0)
    start = max(start, 0)
    return [(word[:start], "accent"), (word[start:end], "glow"), (word[end:], "accent")]


def star_at(frame):
    return Messages.STAR[frame % len(Messages.STAR)]


def tinkering(elapsed, doing, **facts):
    return "".join(text for text, _ in tinkering_pieces(elapsed, doing, **facts))


def tinkering_pieces(elapsed, doing, tokens=0, thinking=False, thought_seconds=None):
    facts = (
        human_duration(elapsed) if elapsed is not None else "",
        f"↓ {short_count(tokens)} tokens" if tokens else "",
        thought(thinking, thought_seconds),
    )
    shown = " · ".join(fact for fact in facts if fact)
    return [(f"{TINKERING} ({shown})" if shown else TINKERING, None), (f" · {doing}", "dim")]


def thought(thinking, thought_seconds):
    if thinking:
        return "thinking"
    return f"thought for {human_duration(thought_seconds)}" if thought_seconds is not None else ""


def short_count(count):
    for size, unit in ((1_000_000, "M"), (1_000, "k")):
        if count >= size:
            scaled = count / size
            return (f"{scaled:.1f}".removesuffix(".0") if scaled < 10 else f"{scaled:.0f}") + unit
    return str(count)


def fitted(pieces, width, stream=None):
    shown = []
    for text, style in pieces:
        text = text[:width]
        width -= len(text)
        shown.append(paint(text, style, stream) if style else text)
    return "".join(shown)


def paint(text, style, stream=None):
    if not text or not colourful(stream or sys.__stdout__):
        return text
    return f"{style_code(style)}{text}{RESET}"


def colourful(stream):
    return stream.isatty() and not os.environ.get("NO_COLOR")


def style_code(style):
    truecolor = os.environ.get("COLORTERM") in ("truecolor", "24bit")
    if style == "accent":
        return TRUECOLOR_ACCENT if truecolor else PALETTE_ACCENT
    if style == "glow":
        return TRUECOLOR_GLOW if truecolor else PALETTE_GLOW
    return STYLES[style]


def cut_to_width(text, room):
    kept, used = "", 0
    for char in text:
        used += char_width(char)
        if used > room:
            break
        kept += char
    return kept.rstrip()


def visible_width(text):
    return sum(char_width(char) for char in text)


def char_width(char):
    if unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def human_duration(seconds):
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


say = Messages()
