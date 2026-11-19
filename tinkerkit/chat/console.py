import contextlib
import os
import re
import shutil
import signal
import sys
lazy import fcntl
lazy import json
lazy import queue
lazy import readline
lazy import shlex
lazy import threading
lazy import time
lazy import webbrowser
lazy from pathlib import Path

from ..commands import COMMANDS, CommandMenu, channel_add_words, model_pick
from .markup import Markup, Message, indented, marked, md, pieces, split_at_tinkering, styled
lazy from ..messages import (
    CLEAR_LINE,
    CLEAR_TO_LINE_END,
    CLEAR_TO_SCREEN_END,
    LineEditor,
    char_width,
    colourful,
    cut_to_width,
    live_line,
    say,
    visible_width,
)
lazy from ..model.models import Models
lazy from ..prompts import Prompts
lazy from ..settings import Config, Home

MODEL_PULL_HINT = "Get one with `ollama pull NAME`, then pick it with `/model NAME`."
BANNER_MAX_COLUMNS = 64
WIPE_SCREEN_AND_SCROLLBACK = "\033[H\033[2J\033[3J"
NEXT_PAGE_HINT = "↵ next page"
TYPING_SIGN_SECONDS = 4
COMMAND_NAMES = tuple(command.name for command in COMMANDS if command.name)
BARE_RE = re.compile(
    r"(?<![/\w.-])tinker (?=(?:" + "|".join(sorted(COMMAND_NAMES, key=len, reverse=True)) + r")\b)"
)
EXIT_WORDS = ("exit",)
PROMPT_HISTORY_LINES = 1000


def typed_exit(line):
    return line.strip().lower() in [f"/{word}" for word in EXIT_WORDS]


def dropped_path(text):
    try:
        words = shlex.split(text)
    except ValueError:
        return None, ""
    for end in range(len(words), 0, -1):
        path = os.path.expanduser(" ".join(words[:end]))
        if os.path.isfile(path):
            return path, " ".join(words[end:])
    return None, ""


@contextlib.contextmanager
def said_plainly():
    write, screen = say.write, say.screen
    say.write_through(print)
    try:
        yield
    finally:
        say.write_through(write, screen)


class Channel:
    KIND = ""
    STYLE = Prompts.PHONE_STYLE
    SECRETS = ()

    def __init__(self, name, settings, secrets):
        self.name, self.settings, self.secrets = name, settings, secrets
        self.allowed = dict.fromkeys(str(chat) for chat in settings.get("allowed", [])).keys()
        self.closed = False
        self.state = None

    def handle(self):
        raise NotImplementedError

    def receive(self, timeout):
        raise NotImplementedError

    def send(self, chat, text):
        raise NotImplementedError

    def edit(self, chat, sent, text):
        return self.send(chat, text)

    def send_file(self, chat, path, caption):
        return self.send(chat, f"{caption}\n📄 `{path}`")

    def send_photo(self, chat, path, caption):
        return self.send_file(chat, path, caption)

    def typing(self, chat):
        pass

    @contextlib.contextmanager
    def waiting(self, chat, text):
        done = threading.Event()
        threading.Thread(target=keep_typing, args=(self, chat, done), daemon=True).start()
        try:
            yield
        finally:
            done.set()

    def send_banner(self, chat, lines):
        first, *middle, last = lines
        return self.send(
            chat, "\n".join([md(t"**{first}**"), *(md(t"{line}") for line in middle), md(t"_{last}_")])
        )

    def lasting(self, chat, sent):
        pass

    def glance(self, chat, asked, sent, keep=None):
        pass

    def collect(self):
        pass

    def clear(self):
        return 0

    def delete(self, chat, sent):
        pass

    def latest(self, chat, sent):
        return True

    def show(self, chat, shown, text, done=False, bottom=None, status=False, began=None, **going):
        shown = list(shown or [])
        if (done if bottom is None else bottom) and shown and not self.latest(chat, shown[-1]):
            for old in shown:
                self.delete(chat, old)
            shown = []
        parts = pieces(f"{self.said(text)}\n\n🕒 _updated {time.strftime('%H:%M:%S')}_")
        now = [
            self.edit(chat, shown[index], part) if index < len(shown) else self.send(chat, part)
            for index, part in enumerate(parts)
        ]
        for extra in shown[len(parts) :]:
            self.delete(chat, extra)
        return now

    def show_growing(self, chat, shown, text):
        return self.show(chat, shown, text)

    def count_live(self, chat, shown, **facts):
        pass

    def said(self, text):
        return BARE_RE.sub("/", text)

    def skip_waiting(self):
        pass

    def offer(self, commands):
        pass

    def offer_fresh(self, chat, shown):
        pass

    def offer_next(self, chat, shown, words):
        pass

    def now_inside(self, chat, mode):
        pass

    def offer_leave(self, chat, shown, label):
        pass

    def offer_answers(self, chat, shown):
        pass

    def offer_run(self, chat, shown, label):
        pass

    def ask_for(self, chat, question, hint):
        return self.send(chat, question)

    def choose(self, chat, question, names):
        return None

    def listened(self, timeout):
        try:
            messages = self.receive(timeout)
        except ChannelError as exc:
            if str(exc) != getattr(self, "refused", None):
                say.warn(f"{self.name}: {exc}; trying again")
            self.refused = str(exc)
            time.sleep(timeout)
            return []
        self.refused = None
        return [message for message in messages if message.chat in self.allowed]

    def claim(self, holder):
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
        try:
            holder = json.loads(self.lock_file().read_text() or "{}")
        except OSError, ValueError:
            return {}
        return holder if isinstance(holder, dict) else {}

    def lock_file(self):
        return Home.lock_file(self.name)


class ChannelError(Exception):
    pass


class PreviewFailed(Exception):
    pass


class ConsoleChannel(Channel):
    KIND = "console"
    STYLE = Prompts.TERMINAL_STYLE
    CHAT = "console"

    def __init__(self):
        super().__init__("console", {"allowed": [self.CHAT]}, {})
        self.screen, self.lines = ConsoleScreen(), queue.Queue()
        self.last_id, self.numbering = 0, threading.Lock()
        self.printed, self.began, self.doings, self.live_owner = {}, {}, {}, None
        self.history, self.next_page, self.mode = None, None, None

    def handle(self):
        return "this terminal"

    def offer_next(self, chat, shown, words):
        self.next_page = words
        self.note(NEXT_PAGE_HINT)

    def now_inside(self, chat, mode):
        self.mode = mode
        typing = self.screen.typing
        if typing is not None:
            typing[0].prompt = self.prompt()
            self.screen.draw_prompt()

    def prompt(self):
        if self.mode is None or not typed_here():
            return self.screen.prompt()
        return self.mode.prompt()

    def receive(self, timeout):
        if not self.screen.reading:
            self.start_reading()
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return []
        if line is None or typed_exit(line):
            self.closed = True
            return []
        return [Message(self.CHAT, line, "you")] if line.strip() else []

    def start_reading(self):
        say.write_through(self.screen.print, self.screen if typed_here() else None)
        if typed_here():
            self.history = remembered_prompts()
        self.screen.start()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        if typed_here() and not Config.get("model"):
            self.first_pick()
        while True:
            try:
                line = self.typed_line() if typed_here() else input(self.prompt())
            except EOFError, KeyboardInterrupt:
                line = None
            if line is None:
                self.lines.put(None)
                return
            self.show_again(line)
            self.remember(line)
            self.lines.put(self.answered(line))

    def typed_line(self):
        editor, menu, drawer = LineEditor(typed_before()), CommandMenu(), PromptDrawer(self.prompt())
        columns = shutil.get_terminal_size().columns
        self.screen.typing_with(
            (
                drawer,
                lambda: drawer.draw(editor.text, editor.column, menu.rows() if menu.open else [], columns),
            )
        )
        self.screen.draw_prompt()
        keys = say.keys()
        try:
            for key in keys:
                if self.pressed(editor, menu, key):
                    break
                menu.follow(editor.text)
                self.screen.draw_prompt()
        finally:
            keys.close()
            self.screen.typing_with(None)
        self.screen.draw(drawer.done(editor.text, columns))
        return editor.answer

    @staticmethod
    def pressed(editor, menu, key):
        answer = menu.press(key) if menu.open else None
        if answer is None:
            return editor.press(key)
        if answer.line is not None:
            editor.replace(answer.line)
        return answer.send and editor.press("enter")

    def first_pick(self):
        try:
            line = self.model_picked("model")
        except SystemExit as exc:
            say.warn(str(exc))
            return
        if line is None:
            Models.say_recipe()
            say.text(MODEL_PULL_HINT)
        elif line:
            self.lines.put(line)

    def answered(self, line):
        next_page, self.next_page = self.next_page, None
        if next_page and not line.strip():
            return f"/{next_page}"
        words = channel_add_words(line)
        if words is None or not typed_here():
            return self.dropped(line)
        self.channel_added(words)
        return ""

    def dropped(self, line):
        path, words = dropped_path(line)
        if path is None:
            return self.picked(line)
        reading = f"/read {shlex.quote(path)}"
        if not words:
            return reading
        self.lines.put(reading)
        return words

    def channel_added(self, words):
        from .channels import ChannelCommand

        try:
            with self.screen.printing, said_plainly():
                return ChannelCommand.run(["add", *words])
        except SystemExit as exc:
            say.warn(str(exc))
            return None

    def picked(self, line):
        pick = model_pick(line)
        if not pick or not typed_here():
            return line
        try:
            chosen = self.model_picked(pick)
        except SystemExit:
            return line
        return line if chosen is None else chosen

    def model_picked(self, pick):
        choices = Models.command_choices(pick.split()[-1])
        if not choices:
            return None
        Models.say_recipe()
        with self.screen.printing:
            name = Models.picked(choices)
        return "" if name is None else f"/{pick} {name}"

    def remember(self, line):
        if self.history and line.strip() and not typed_exit(line):
            readline.add_history(line)
            readline.write_history_file(self.history)

    def show_again(self, line):
        if self.screen.ruled():
            quiet = not line.strip() or typed_exit(line)
            self.screen.sent(f"{self.prompt()}{line}", shown=not quiet)

    def banner(self, lines):
        if typed_here():
            self.screen.draw(self.framed(lines))

    def wipe(self, lines):
        if typed_here():
            self.screen.wiped(WIPE_SCREEN_AND_SCROLLBACK + self.framed(lines))

    def framed(self, lines):
        width = min(terminal_columns(), BANNER_MAX_COLUMNS)
        edge = "─" * (width - 2)
        rows = [self.banner_row(line, "dim" if row else "bold", width - 4) for row, line in enumerate(lines)]
        framed = [styled(f"╭{edge}╮", "accent"), *rows, styled(f"╰{edge}╯", "accent")]
        return "\n".join(framed) + "\n"

    def note(self, text):
        self.screen.print(styled(text, "dim"))

    @staticmethod
    def banner_row(text, style, room):
        shown = cut_to_width(text, room)
        padding = " " * (room - visible_width(shown))
        return styled("│ ", "accent") + styled(shown, style) + padding + styled(" │", "accent")

    def send(self, chat, text):
        sent, text = self.new_id(), self.said(text)
        self.screen.print(marked(Markup.ansi(text)))
        self.printed[sent] = {line for line in text.split("\n") if line.strip()}
        return sent

    def edit(self, chat, sent, text):
        self.screen.print(marked(Markup.ansi(self.said(text))))
        return sent

    def send_file(self, chat, path, caption):
        webbrowser.open(Path(path).as_uri())
        return self.send(chat, f"{caption}\n📄 Opened in your browser: `{path}`")

    def show(self, chat, shown, text, done=False, bottom=None, status=False, began=None, doing=None, **facts):
        sent, text = (shown or [None])[0] or self.new_id(), self.said(text)
        if began is not None:
            self.began[sent] = monotonic_at(began)
        if done:
            self.show_done(sent, text)
        else:
            self.show_going(sent, text, doing, facts)
        return [sent]

    def show_growing(self, chat, shown, text):
        sent = (shown or [None])[0] or self.new_id()
        self.print_settled(sent, self.said(text).split("\n"))
        return [sent]

    @contextlib.contextmanager
    def waiting(self, chat, text):
        if not self.screen.ruled() or self.live_owner is not None:
            yield
            return
        waiter = self.live_owner = object()
        self.screen.show_live(text, time.monotonic())
        try:
            yield
        finally:
            if self.live_owner is waiter:
                self.live_owner = None
                self.screen.end_live()

    def count_live(self, chat, shown, **facts):
        if shown and shown[0] == self.live_owner:
            self.screen.count_live(**facts)

    def show_done(self, sent, text):
        if self.live_owner == sent:
            self.live_owner = None
            self.screen.end_live()
        self.print_settled(sent, text.split("\n"))
        self.forget_going(sent)

    def forget_going(self, sent):
        self.began.pop(sent, None)
        self.doings.pop(sent, None)

    def show_going(self, sent, text, doing, facts):
        settled, going_on, under = split_at_tinkering(text.split("\n"))
        self.print_settled(sent, settled)
        shown_under = [Markup.plain(line).strip() for line in under if line.strip()]
        self.show_doing(sent, doing or Markup.plain(going_on).strip(), facts, shown_under)

    def print_settled(self, sent, lines):
        printed = self.printed.setdefault(sent, set())
        new = [line for line in lines if line.strip() and line not in printed]
        if new and not printed:
            self.screen.print(marked(Markup.ansi("\n".join(lines))))
        elif new:
            self.screen.print(indented(Markup.ansi("\n".join(new))))
        printed.update(new)

    def show_doing(self, sent, doing, facts, under):
        if self.screen.ruled():
            self.live_owner = sent
            self.screen.show_live(doing, self.began.setdefault(sent, time.monotonic()), under, **facts)
        elif self.doings.get(sent) != doing:
            self.doings[sent] = doing
            self.screen.print(doing)

    def new_id(self):
        with self.numbering:
            self.last_id += 1
            return self.last_id


class PromptDrawer:
    def __init__(self, prompt):
        self.prompt, self.cursor_row = prompt, 0

    def draw(self, text, column, rows, columns):
        line = self.prompt + text
        menu = "".join("\r\n" + menu_row(row, columns - 1) for row in rows)
        drawn_row = (max(visible_width(line), 1) - 1) // columns + len(rows)
        return self.cleared() + line + menu + self.cursor_placed(text[:column], drawn_row, columns)

    def done(self, text, columns):
        cleared, self.cursor_row = self.cleared(), 0
        return cleared + self.prompt + text + "\r\n"

    def taken_away(self):
        cleared, self.cursor_row = self.cleared(), 0
        return cleared

    def cleared(self):
        return rows_moved(-self.cursor_row) + "\r" + CLEAR_TO_SCREEN_END

    def cursor_placed(self, before, drawn_row, columns):
        row, column = divmod(visible_width(self.prompt + before), columns)
        self.cursor_row = row
        return rows_moved(row - drawn_row) + "\r" + (f"\033[{column}C" if column else "")


def typed_before():
    return [readline.get_history_item(item) for item in range(1, readline.get_current_history_length() + 1)]


def rows_moved(rows):
    if not rows:
        return ""
    return f"\033[{abs(rows)}{'B' if rows > 0 else 'A'}"


def menu_row(row, room):
    named = ("› " if row.marked else "  ") + row.name
    words = f" {row.words}" if row.words else ""
    shown = cut_to_width(named + words + "  " + row.what, room)
    end = len(named) + len(words)
    return shown[: len(named)] + styled(shown[len(named) : end], "dim") + shown[end:]


class ConsoleScreen:
    PROMPT = "› " if sys.stdin.isatty() else ""
    TICK_SECONDS = 0.12

    def __init__(self):
        self.reading, self.live, self.under, self.frame, self.printing = False, None, [], 0, threading.Lock()
        self.size, self.drawn = shutil.get_terminal_size(), []
        self.size_changed, self.typing = threading.Event(), None

    def start(self):
        self.reading, self.size = True, shutil.get_terminal_size()
        if typed_here():
            self.draw(unwrapped(self.rule()) + "\n")
            self.wake_on_resize()
            threading.Thread(target=self.keep_ticking, daemon=True).start()

    def wake_on_resize(self):
        if hasattr(signal, "SIGWINCH") and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGWINCH, lambda *_: self.size_changed.set())

    def print(self, text):
        with self.printing:
            self.out(lambda: self.printed_in_pieces(text) if self.ruled() else self.printed_over_prompt(text))

    def printed_in_pieces(self, text):
        room = terminal_lines() - self.live_rows() - 2
        return "".join(
            self.printed_above("\n".join(lines), rows) for lines, rows in screen_pieces(text, room)
        )

    def printed_above(self, text, rows):
        return f"{self.room_made(rows, above=self.live_rows())}\r{text}{RESTORE_CURSOR}"

    def printed_over_prompt(self, text):
        if not sys.stdout.isatty():
            return text + "\n"
        return f"{CLEAR_LINE}{text}\n{self.PROMPT if self.reading else ''}"

    def sent(self, typed, shown):
        with self.printing:
            rows = rows_shown(typed)
            self.out(
                lambda: self.shown_above_the_block(typed, rows) if shown else self.pulled_up(rows, kept=0),
                typed_rows=rows,
            )

    def shown_above_the_block(self, typed, rows):
        shown = NEXT_LINE.join([self.shown_again(typed), self.block_and_rule()])
        return self.above_the_rule(rows + self.live_rows(), shown)

    @staticmethod
    def shown_again(typed):
        return styled(typed + " " * (terminal_columns() - visible_width(typed)), "grey background")

    def draw(self, text):
        with self.printing:
            write_out(text)

    def wiped(self, top):
        with self.printing:
            if self.ruled():
                self.out(lambda: top + self.block_and_rule() + "\n")
            else:
                write_out(top)

    def typing_with(self, typing):
        self.typing = typing

    def draw_prompt(self):
        with self.printing:
            typing = self.typing
            if typing is not None:
                write_out(typing[1]())

    def out(self, made, typed_rows=0):
        text = self.repaired(typed_rows) + made()
        if text and self.typing is not None:
            drawer, drawn = self.typing
            text = drawer.taken_away() + text + drawn()
        write_out(text)

    def show_live(self, text, began, under=(), **facts):
        if not self.ruled():
            return
        with self.printing:
            self.out(lambda: self.live_replaced(text, began, under, facts))

    def count_live(self, **facts):
        with self.printing:
            self.out(lambda: self.live_counted(facts))

    def end_live(self):
        with self.printing:
            self.out(self.live_ended)

    def live_replaced(self, text, began, under, facts):
        rows_before = self.live_rows()
        self.live, self.under = (text, began, facts), list(under)
        return self.resized(rows_before, self.live_rows()) + self.redrawn_live()

    def live_counted(self, facts):
        if self.live is None:
            return ""
        text, began, _ = self.live
        self.live = (text, began, facts)
        return self.redrawn_live()

    def live_ended(self):
        if self.live is None:
            return ""
        rows = self.live_rows()
        self.live, self.under, self.drawn = None, [], []
        return self.shrunk(rows)

    def live_ticked(self):
        if self.live is None:
            return ""
        self.frame += 1
        return self.redrawn_live()

    def keep_ticking(self):
        while True:
            self.size_changed.wait(self.TICK_SECONDS)
            self.size_changed.clear()
            self.tick()

    def tick(self):
        with self.printing:
            self.out(self.live_ticked)

    def repaired(self, typed_rows=0):
        size = shutil.get_terminal_size()
        redrawn = self.redrawn_at(size, typed_rows) if size != self.size and self.ruled() else ""
        self.size = size
        return redrawn

    def redrawn_at(self, size, typed_rows):
        stale, kept = self.stale_rows(size.columns), self.live_rows() + 1 + typed_rows
        rewrapped = stale + typed_rows - kept
        return (
            self.stale_rows_cleared(stale, typed_rows)
            + self.pulled_up(rewrapped, kept)
            + self.above_the_prompt(kept, self.block_and_rule())
        )

    def stale_rows_cleared(self, stale, typed_rows):
        return self.above_the_prompt(stale + typed_rows, NEXT_LINE.join([CLEAR_TO_LINE_END] * stale))

    def stale_rows(self, columns):
        rule_rows = rows_at(self.size.columns, columns)
        return rule_rows + sum(rows_shown_at(row, columns) for row in self.drawn)

    def block_and_rule(self):
        block = self.live_block() if self.live is not None else []
        return unwrapped(NEXT_LINE.join([*block, self.rule()]))

    def live_rows(self):
        return 0 if self.live is None else 1 + len(self.under)

    def resized(self, rows_before, rows_after):
        if rows_after > rows_before:
            return self.room_made(rows_after - rows_before, above=0) + RESTORE_CURSOR
        return self.shrunk(rows_before - rows_after)

    @staticmethod
    def room_made(rows, above):
        return f"{ROW_DOWN_OR_SCROLL * rows}{SAVE_CURSOR}{rows_up(rows + above + 1)}{inserted_rows(rows)}"

    def shrunk(self, by):
        return self.pulled_up(by, kept=self.live_rows() + 1)

    @staticmethod
    def pulled_up(rows, kept):
        if not rows:
            return ""
        return f"{SAVE_CURSOR}{lines_up(kept + rows)}{deleted_rows(rows)}{RESTORE_CURSOR}{rows_up(rows)}"

    def redrawn_live(self):
        return self.above_the_rule(self.live_rows(), unwrapped(NEXT_LINE.join(self.live_block())))

    def live_block(self):
        return [row + CLEAR_TO_LINE_END for row in self.live_drawn()]

    @classmethod
    def above_the_rule(cls, rows, change):
        return cls.above_the_prompt(rows + 1, change)

    @staticmethod
    def above_the_prompt(rows, change):
        return f"{SAVE_CURSOR}{lines_up(rows)}{change}{RESTORE_CURSOR}"

    def live_drawn(self):
        text, began, facts = self.live
        width = terminal_columns() - 1
        tinkering = live_line(text, began, self.frame, width, sys.stdout, **facts)
        under = (f"  {styled(cut_to_width(line, width - 2), 'dim')}" for line in self.under)
        self.drawn = [tinkering, *under]
        return self.drawn

    def prompt(self):
        return "› " if typed_here() else self.PROMPT

    @staticmethod
    def rule():
        return styled("─" * terminal_columns(), "dim")

    def ruled(self):
        return self.reading and typed_here()


SAVE_CURSOR, RESTORE_CURSOR, NEXT_LINE = "\0337", "\0338", "\033[E"
AUTOWRAP_OFF, AUTOWRAP_ON = "\033[?7l", "\033[?7h"
COLOUR_CODE_RE = re.compile(r"\033\[[0-9;]*m")
ROW_DOWN_OR_SCROLL = "\033D"


def unwrapped(rows):
    return f"{AUTOWRAP_OFF}{rows}{AUTOWRAP_ON}"


def screen_pieces(text, room):
    lines, rows = [], 0
    for line in fitting_lines(text, room):
        line_rows = rows_shown(line)
        if lines and rows + line_rows > room:
            yield lines, rows
            lines, rows = [], 0
        lines.append(line)
        rows += line_rows
    yield lines, rows


def fitting_lines(text, room):
    for line in text.split("\n"):
        if rows_shown(line) > room:
            yield from screen_wide_rows(COLOUR_CODE_RE.sub("", line))
        else:
            yield line


def screen_wide_rows(line):
    columns = terminal_columns()
    row, width = "", 0
    for char in line:
        char_columns = char_width(char)
        if width + char_columns > columns:
            yield row
            row, width = "", 0
        row += char
        width += char_columns
    yield row


def rows_shown(text):
    return rows_shown_at(text, terminal_columns())


def rows_shown_at(text, columns):
    return sum(rows_at(visible_width(COLOUR_CODE_RE.sub("", line)), columns) for line in text.split("\n"))


def rows_at(width, columns):
    return max(1, -(-width // columns))


def terminal_columns():
    return shutil.get_terminal_size().columns


def terminal_lines():
    return shutil.get_terminal_size().lines


def monotonic_at(wall_time):
    return time.monotonic() - (time.time() - wall_time)


def lines_up(rows):
    return f"\033[{rows}F"


def rows_up(rows):
    return f"\033[{rows}A"


def inserted_rows(rows):
    return f"\033[{rows}L" if rows else ""


def deleted_rows(rows):
    return f"\033[{rows}M"


def write_out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def remembered_prompts():
    path = Home.prompt_history()
    readline.set_auto_history(False)
    readline.set_history_length(PROMPT_HISTORY_LINES)
    readline.clear_history()
    with contextlib.suppress(OSError):
        readline.read_history_file(path)
    return path


def typed_here():
    return sys.stdin.isatty() and colourful(sys.stdout)


def keep_typing(channel, chat, done):
    while not done.is_set():
        channel.typing(chat)
        done.wait(TYPING_SIGN_SECONDS)
