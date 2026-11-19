import contextlib
import os
import re
import signal
import sys
from collections import namedtuple
from dataclasses import dataclass
from typing import NamedTuple
lazy import datetime
lazy import json
lazy import shlex
lazy import subprocess
lazy import threading
lazy import time
lazy from pathlib import Path

from .build.project import Runs, Tracker, saved
from .chat.console import EXIT_WORDS, ConsoleChannel, PreviewFailed, keep_typing, typed_here
from .chat.markup import Markup, Message, md
from .chat.reports import (
    PROGRESS_EDIT_SECONDS,
    Progress,
    Reporter,
    described,
    exercise_board,
    exercise_switch,
    exercising_now,
    headline,
    keep_exercise_board,
    printed_since,
    signal_task,
    until_ended,
)
from .chat.shell import (
    CHAT_COMMAND_OUTPUT_SHOWN_CHARS,
    COMMAND_NAMING_MAX_TOKENS,
    Question,
    Shell,
    first_words,
    in_shell,
    remembered_part,
    reviewed_level,
    thread_reply,
)
from .commands import (
    BY_NAME,
    CHAT_COMMANDS,
    COMMANDS,
    GROUPS,
    command_of,
    command_words,
    deletes,
    kind_of,
    model_pick,
    refs,
)
from .jobs import (
    JOBS,
    Jobs,
    Queue,
    Runner,
    answer_stopped,
    answering,
    emptied_queue,
    ending,
    job_number,
    job_view,
    queue_action,
    queue_lines,
    stop_answers,
    tinker_command,
    tinker_environment,
    tinker_process,
)
from .memory.memories import Memories, Scratchpad
from .skills import Skills
lazy from .chat.ledger import DONE_NOTE_KEPT_SECONDS, Ledger
lazy from .messages import next_page_line, next_page_words, say
lazy from .messages import styled as styled_choice
lazy from .model.models import Cut, Models, Ollama, OllamaError, system_turn
lazy from .prompts import Part, Prompts
lazy from .ranking import Embeddings, Retriever, traits_part
lazy from .settings import (
    EPISODE_GIST_CHARS,
    EXERCISE_FOLDER,
    JOB_STOP_SIGNAL,
    THREAD_MINUTES,
    THREAD_TURNS,
    Config,
    Home,
    Page,
    projects_folder,
)
lazy from .workspace import Toolchain

CHANNEL_REPORTS_ENV = "TINKER_CHANNELS"
BUILD_STOP_GRACE_SECONDS = 120
STATUS_LOOK_SECONDS = 10
EXPIRED_MESSAGES_SWEEP_SECONDS = 2
ENDED_LOOK_SECONDS = 0.1
CHAT_MAX_REPLY_TOKENS = 1024
CHAT_THINK_TOKENS = 2048
CHAT_ROUTE_TOKENS = 64
MODEL_BUSY_NOTE_AFTER_SECONDS = 5
MODEL_BUSY_NOTE_EDIT_SECONDS = 30

CHAT_COMMAND_OUTPUT_READ_CHARS = 6000

PLAN_CONFIRMING_COMMANDS = tuple(
    command.name for command in COMMANDS if "--confirm" in (option.flag for option in command.options)
)
USAGE = frozendict(
    {
        command.name: f"✍️ Write `/{command.synopsis}`"
        + (f", e.g. `/{command.example}`" if command.example else "")
        for command in COMMANDS
        if command.name
    }
)
COMMANDS_ASKING_FOR_WORDS = tuple(
    command.name
    for command in COMMANDS
    if command.chat
    and command.name != "clear"
    and any(argument.nargs in ("+", None) for argument in command.arguments)
) + ("command",)
CHAT_APP_START = "/start"
CHAT_FRESH = "/fresh"
CHAT_LEAVE = "/leave"
CHAT_SKIP = "/skip"
NEXT_PAGE_BUTTON = "Next page ›"
FRESH_ANSWER_BUTTON = "🔄 Fresh answer"
LEAVE_READING_BUTTON = "📕 Leave #{number}"
LEAVE_JOB_BUTTON = "⚙️ Leave #{number}"
SKIP_QUESTION_BUTTON = "⏭ Skip"
LEAVE_INTERVIEW_BUTTON = "🚪 Leave"
INTERVIEW_ENTERED = "💬 **Interview**: answer, /skip · /leave leaves it"
INTERVIEW_LEFT = "💬 **Left the interview**"
NO_QUESTION_YET = "❓ **No question yet** to ask again."
START_WELCOME = """👋 **Hi, I'm Tinker!**
I build small programs for you, on this computer, with an AI model that runs right here.

🔨 **Build something**
- Tell me what you want: `/task a calculator in Python`
- I reply with a plan: what I will build, and how I will test it
- Like it? Send `/confirm`. Want a change? `/amend also handle negative numbers`
- I build it, and you see my progress here, in one message

📂 **Your projects**
- `/runs` lists them all, newest first
- `/run 5 + 3` uses the one built last
- `/add @calc-py a history of results` adds a feature
- `/fix @calc-py 2/0 crashes` fixes a bug

💬 **Ask me anything**
- Just write to me: what is not a command is a normal chat
- `/research the newest Python release`: I study it on the web, thoroughly, and keep what I find (it takes a while)
- `/command how full is my disk`: I find the command line, and run it once you `/confirm`
- `/learn rust`: I study it, and use it in my builds

⏳ **While I work**
- `/status` tells what I am doing
- `/stop` stops it (it is saved): `/jobs` lists what runs and what stopped, `/resume #3` goes on
- Just write to me: I pause what runs, answer you, and go on with it

❓ `/help` lists everything I can do, and `/help task` explains one command."""


MenuEntry = namedtuple("MenuEntry", "name what")
WordsAsked = namedtuple("WordsAsked", "question example")
AppRequest = namedtuple("AppRequest", "project args stdin")
Secret = namedtuple("Secret", "key question")


class NoShelf:
    def entered(self, words):
        return None

    def exists(self, number):
        return False

    def answered(self, number, text):
        return None


ALL_FOR_NOW = "That's all I had on my mind for now."


class Turn(NamedTuple):
    text: str
    over: bool


class NoInterview:
    def turn(self, chat, now=None):
        return Turn(ALL_FOR_NOW, True)

    def skipped(self, chat):
        return self.turn(chat)

    def answered(self, chat, text, now=None):
        return self.turn(chat)


class InsideReading(NamedTuple):
    number: int
    title: str

    def answer(self, desk, channel, message):
        sent = channel.send(message.chat, self.answer_text(desk, channel, message))
        channel.offer_leave(message.chat, sent, LEAVE_READING_BUTTON.format(number=self.number))

    def answer_text(self, desk, channel, message):
        try:
            with desk.talk.pausing_jobs(), channel.waiting(message.chat, message.text):
                return desk.shelf.answered(self.number, message.text) or "_(no answer)_"
        except OllamaError as exc:
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")

    def offer(self, desk, channel, chat, shown):
        pass

    def entered_line(self, chat):
        return md(t"\n📕 **Inside #{self.number} {self.title}**: ask me about it · `/leave` leaves it")

    def left_line(self):
        return md(t"📕 **Left #{self.number} {self.title}**")

    def env(self):
        return {"TINKER_INSIDE": str(self.number)}

    def gone(self, shelf):
        return not shelf.exists(self.number)

    def prompt(self):
        return f"📕 #{self.number} {self.title} › "

    def takes(self, text):
        return not text.startswith("/")


class InsideInterview(NamedTuple):
    interview: object

    def answer(self, desk, channel, message):
        if message.text.split() == [CHAT_SKIP]:
            turn = self.interview.skipped(message.chat)
        else:
            turn = self.interview.answered(message.chat, message.text)
        if turn.over:
            channel.send(message.chat, turn.text)
            desk.leave_mode(channel, message.chat)
        else:
            self.offer(desk, channel, message.chat, channel.send(message.chat, turn.text))

    def offer(self, desk, channel, chat, shown):
        channel.offer_answers(chat, shown)

    def opening(self, turn):
        return f"{INTERVIEW_ENTERED}\n\n{turn}"

    def left_line(self):
        return INTERVIEW_LEFT

    def env(self):
        return {}

    def gone(self, shelf):
        return False

    def prompt(self):
        return "💬 interact › "

    def takes(self, text):
        return text.split() == [CHAT_SKIP] or not text.startswith("/")


class InsideJob(NamedTuple):
    number: int
    title: str

    def offer(self, desk, channel, chat, shown):
        JobFollower(desk, channel, chat, self, shown).start()
        self.offer_leave(channel, chat, shown)

    def offer_leave(self, channel, chat, shown):
        channel.offer_leave(chat, shown, LEAVE_JOB_BUTTON.format(number=self.number))

    def entered_line(self, chat):
        return md(t"⚙️ **Inside #{self.number} {self.title}** · `/leave` leaves it\n{self.view():block}")

    def shown_once(self):
        return md(t"⚙️ **jobs #{self.number}**\n{self.view():block}")

    def ended_line(self):
        return md(t"⚙️ **jobs #{self.number}**\n{self.ended_view():block}\n{ending(self.number)}")

    def ended_view(self):
        header, *lines = self.lines()
        if self.still_listed_as_running():
            header = f"#{self.number} ended {self.title}"
        return "\n".join([header, *lines])

    def still_listed_as_running(self):
        job = JOBS.get(self.number)
        return job is not None and job["state"] in ("running", "queued")

    def view(self):
        return "\n".join(self.lines())

    def lines(self):
        return job_view(self.number) or [f"#{self.number} gone {self.title}"]

    def left_line(self):
        return md(t"⚙️ **Left #{self.number} {self.title}**")

    def env(self):
        return {}

    def gone(self, shelf):
        return self.number not in Queue.ordered()

    def prompt(self):
        return f"⚙️ #{self.number} {self.title} › "

    def takes(self, text):
        return False


class JobFollower:
    def __init__(self, desk, channel, chat, mode, sent):
        self.desk, self.channel, self.chat, self.mode = desk, channel, chat, mode
        self.shown, self.text = [sent], mode.entered_line(chat)

    def start(self):
        threading.Thread(target=self.keep_up, daemon=True).start()

    def keep_up(self):
        while True:
            time.sleep(PROGRESS_EDIT_SECONDS)
            if not self.step():
                return

    def step(self):
        if self.desk.inside.get(self.chat) is not self.mode:
            return False
        if self.mode.gone(self.desk.shelf):
            self.end()
            return False
        self.follow()
        return True

    def end(self):
        self.channel.show(self.chat, self.shown, self.mode.ended_line(), done=True)
        if self.desk.inside.get(self.chat) is self.mode:
            self.desk.leave_mode(self.channel, self.chat)

    def follow(self):
        text = self.mode.entered_line(self.chat)
        if text == self.text:
            return
        self.text = text
        self.shown = self.channel.show_growing(self.chat, self.shown, text)
        self.mode.offer_leave(self.channel, self.chat, self.shown[-1])


def session(root, shelf=None, interview=None, interviewing=False):
    try:
        chats = chat_channels()
    except SystemExit as exc:
        say.warn(f"Chats left out: {exc}")
        chats = []
    Desk(
        [ConsoleChannel()] + chats,
        root,
        shelf=shelf or NoShelf(),
        interview=interview,
        interviewing=interviewing,
    ).run()


def chat_channels():
    try:
        from .chat.channels import Channels
    except ImportError:
        return []
    return Channels.enabled()


def one_shot(command, text):
    console, talk = ConsoleChannel(), Talk(commands_brief())
    message, chat = Message(ConsoleChannel.CHAT, text, "you"), ConsoleChannel.CHAT
    if command == "history":
        console.send(chat, history_text(text.split()[:1]))
        return None
    if not text and command != "run":
        return f"tinker {command} needs words after it, e.g. tinker {command} {'hello' if command == 'chat' else '...'}"
    if command == "chat":
        console.show(chat, None, talk.reply(console, message), done=True)
    elif command == "run":
        Desk([console], Path.cwd()).run_app(console, chat, [text])
    else:
        command_once(console, talk, message)
    return None


def command_once(console, talk, message):
    chat, shell = ConsoleChannel.CHAT, Shell(talk, Path.cwd(), Questions())
    console.show(chat, None, shell.propose(console, message, "", COMMAND_NAMING_MAX_TOKENS), done=True)
    proposed = shell.take(console, chat)
    if not proposed:
        return
    if say.agreed("Run it?"):
        shell.run(console, chat, proposed)
    else:
        console.show(chat, None, f"🚫 _Not run:_ `{proposed['line']}`", done=True)


@dataclass
class WaitingPlan:
    run: str
    folder: Path
    title: str
    plan: str
    so_far: str
    new: bool

    def owns(self, target):
        return target in (self.run, self.folder.name)

    def asks_name(self, command, name):
        return command == "confirm" and not name and self.new

    def named(self):
        return WaitingName(**vars(self))

    def shown(self):
        return Progress.waits(self.plan, self.so_far)

    def asked(self):
        return self.shown()

    def next_steps(self):
        return "`/confirm` · `/amend WHAT`"

    def renamed(self, name):
        old, self.folder = self.folder, self.folder.parent / name
        self.title = f"{name}: {self.title.split(': ', 1)[-1]}"
        self.plan = self.plan.replace(f"📁 `{old.name}`", f"📁 `{name}`")

    def run_for(self, name):
        return self.run if name and name == self.folder.name else name or None

    def moves_on(self, folder):
        return self.folder == folder

    def resumed(self):
        return ["resume", f"@{self.run}"]

    def after_rename(self, desk, audience, name):
        desk.renamed_in_place(audience, self, name)


class WaitingName(WaitingPlan):
    def asks_name(self, command, name):
        return False

    def asked(self):
        return (
            f"{self.shown()}\n\n📁 **Its folder:** `{self.folder.name}`\n👉 `/confirm` to build it "
            f"there · `/rename NAME` to name it otherwise (it builds then)"
        )

    def next_steps(self):
        return "`/confirm` · `/rename NAME`"

    def after_rename(self, desk, audience, name):
        return desk.build_renamed(audience, self, name)


class NoWaitingPlan:
    run = None

    def __bool__(self):
        return False

    def owns(self, target):
        return False

    def asks_name(self, command, name):
        return False

    def shown(self):
        return ""

    def run_for(self, name):
        return name or None

    def moves_on(self, folder):
        return False


class Desk:
    COMMANDS = CHAT_COMMANDS
    TRACKER_LOOK_SECONDS = 1.0

    def __init__(self, channels, root, shelf=None, interview=None, interviewing=False):
        self.channels, self.root = channels, Path(root).resolve()
        self.shelf, self.inside = shelf or NoShelf(), {}
        self.interview, self.interviewing = interview or NoInterview(), interviewing
        self.last_task = self.reporter = self.task = None
        self.lock, self.talk = threading.Lock(), Talk(commands_brief(), pausing_jobs=answering)
        self.questions = Questions()
        self.shell = Shell(self.talk, self.root, self.questions)
        self.waiting_plan = NoWaitingPlan()
        self.messages_by_run = {}
        self.seconds_by_run = {}
        self.consoles = [channel for channel in channels if isinstance(channel, ConsoleChannel)]
        self.said_status = None
        self.shown_banner, self.shown_states = self.banner_lines(), {}
        self.chats_started, self.post_lock = set(), threading.Lock()
        self.said_endings = {job["number"] for job in Desk.ended_idle_calibrations()}
        self.stop_question = None
        self.exercise_reporter = None
        self.echo_to_terminal = not self.consoles
        if threading.current_thread() is threading.main_thread():
            signal.signal(JOB_STOP_SIGNAL, stop_answers)

    def run(self):
        self.claim_chats()
        self.hosting = Runner.host()
        self.open_banner()
        self.talk.warm()
        self.say_status(anew=True)
        if self.interviewing:
            self.open_interview()
        if not self.consoles:
            names = ", ".join(channel.name for channel in self.channels)
            say.info(f"Listening on {names}; projects go in {self.root}. Ctrl-C stops.", "👂")
        try:
            self.listen_until_ended()
        except KeyboardInterrupt:
            Runner.pause(self.hosting)
            self.close_channels()
            self.stop_task()
            self.say_goodbye()
            if not self.consoles:
                say.title("Stopped listening.", "🛑")
        finally:
            Desk.unload_models()

    @staticmethod
    def unload_models():
        for name in {Config.get("model"), Config.get("embed_model")} - {None, ""}:
            Ollama.unload(name)

    def claim_chats(self):
        for channel in [channel for channel in self.channels if channel not in self.consoles]:
            if not channel.claim({"pid": os.getpid()}):
                held = held_by_another(channel)
                if not self.consoles:
                    raise SystemExit(f"tinker: {held}: stop it first")
                say.warn(f"{held}: this one talks here only")
                self.channels.remove(channel)

    def listen_on(self, names):
        from .chat.channels import ChannelCommand, Channels

        listened = {channel.name for channel in self.channels}
        try:
            found = [Channels.make(ChannelCommand.named(names, "start"))] if names else Channels.enabled()
            new = [channel for channel in found if channel.name not in listened]
            for channel in new:
                ChannelCommand.check(channel)
        except SystemExit as exc:
            return f"⚠️ {exc}"
        if not new:
            return "🤷 No enabled channel is new to listen on (tinker channel enable NAME, or start NAME)."
        held = [channel for channel in new if not channel.claim({"pid": os.getpid()})]
        claimed = [channel for channel in new if channel not in held]
        self.channels.extend(claimed)
        if claimed:
            self.renew_banner()
        for channel in claimed:
            threading.Thread(target=self.listen, args=(channel,), daemon=True).start()
        lines = [f"⚠️ {held_by_another(channel)}: stop it first" for channel in held]
        if claimed:
            lines.insert(0, f"📡 Listening on {', '.join(channel.name for channel in claimed)}.")
        return "\n".join(lines)

    def open_banner(self):
        self.shown_banner = self.banner_lines()
        self.banner_on_consoles()

    def banner_on_consoles(self):
        self.shown_states = {chat: chat.state for chat in self.chats()}
        for console in self.consoles:
            console.banner(self.shown_banner)

    def say_state_changes(self):
        for chat in self.chats():
            if chat.state != self.shown_states.get(chat):
                self.shown_states[chat] = chat.state
                for console in self.consoles:
                    console.note(Desk.chat_with_state(chat))

    @staticmethod
    def ended_idle_calibrations():
        return [
            job
            for job in Jobs.every()
            if job.get("idle")
            and job["state"] == "finished"
            and job["arguments"][:2] == ["model", "calibrate"]
        ]

    def say_idle_endings(self):
        for job in Desk.ended_idle_calibrations():
            if job["number"] not in self.said_endings:
                self.said_endings.add(job["number"])
                self.say_everywhere(Desk.ending_of(job))

    @staticmethod
    def ending_of(job):
        entries = Home.store().entries(f"output #{job['number']}")
        return "\n".join(entry["line"] for _, entry in entries if "line" in entry)

    def say_everywhere(self, text):
        for channel in self.channels:
            if channel not in self.consoles:
                with self.post_lock:
                    if channel in self.chats_started:
                        for chat in channel.allowed:
                            channel.show(chat, None, text, done=True)
        for console in self.consoles:
            console.show(ConsoleChannel.CHAT, None, text, done=True)

    def renew_banner_when_changed(self):
        if self.banner_lines() != self.shown_banner:
            self.renew_banner()

    def renew_banner(self):
        self.shown_banner = self.banner_lines()
        self.clear_all_chats()
        with self.post_lock:
            for channel in self.chats_started:
                self.post_banner(channel, self.shown_banner)
        self.say_status(anew=True)

    def banner_lines(self):
        chats = self.chats_line(Desk.chat_name)
        details = [f"model {Config.get('model') or 'no model yet'}"]
        details += [f"embedding {Config.get('embed_model') or 'none'}"]
        details += [f"projects {self.shown_root()}"]
        details += [chats] if chats else []
        return ["🔧 Tinker", *details, "help lists commands · exit quits"]

    def chats(self):
        return [channel for channel in self.channels if channel not in self.consoles]

    def chats_line(self, shown):
        chats = [shown(chat) for chat in self.chats()]
        return f"chats {', '.join(chats)}" if chats else None

    @staticmethod
    def chat_name(chat):
        return chat.name

    @staticmethod
    def chat_with_state(chat):
        return f"{chat.name} · {chat.state}" if chat.state else chat.name

    def shown_root(self):
        home = Path.home()
        return str("~" / self.root.relative_to(home)) if self.root.is_relative_to(home) else str(self.root)

    def say_goodbye(self):
        status = "💤 **Tinker** · not listening"
        for console in self.consoles:
            console.show(ConsoleChannel.CHAT, None, status, done=True)
        goodbye = self.goodbye(status)
        if goodbye["channels"] or goodbye["question"]:
            with self.post_lock:
                tinker_process(["channel", "goodbye", json.dumps(goodbye)], output=subprocess.DEVNULL)

    def goodbye(self, status):
        started = [channel.name for channel in self.channels if channel in self.chats_started]
        question = None
        if self.stop_question:
            channel, chat, shown = self.stop_question
            question = {"channel": channel.name, "chat": chat, "messages": list(shown)}
        return {"status": status, "channels": started, "question": question}

    def say_status(self, text=None, anew=False):
        text = text or self.status()
        if text == self.said_status and not anew:
            return
        anew = anew or headline(text) != headline(self.said_status)
        self.said_status = text
        for channel in self.channels:
            if channel not in self.consoles:
                with self.post_lock:
                    if channel in self.chats_started and not self.a_channel_closed():
                        self.post_status(channel, text)
            elif (anew and not self.terminal_follows_job()) or text.startswith("💤"):
                channel.show(ConsoleChannel.CHAT, None, text, done=True)
        self.keep_job_at_bottom()

    def terminal_follows_job(self):
        audience = self.reporter.audience if self.reporter else []
        return self.busy() and any(channel in self.consoles for channel, _ in audience)

    def keep_job_at_bottom(self):
        reporter = self.reporter
        if self.busy() and reporter.sent and not reporter.ended:
            with reporter.lock:
                reporter.send_at_bottom = True
                reporter.update()

    def status(self):
        head, *queued = Queue.ordered() or [None]
        job = Jobs.get(head) if head is not None else None
        if job:
            behind = f" · {len(queued)} queued" if queued else ""
            return md(t"🔄 **Tinker** · working on {job['title']}{behind}")
        if self.waiting_plan:
            then = self.waiting_plan.next_steps()
            return md(t"🙋 **Tinker** · {self.waiting_plan.title} waits for you: {then:md}")
        return "🟢 **Tinker** · idle: send a task"

    @staticmethod
    def post_status(channel, text):
        mine = Home.store().get("listening", channel.name) or {}
        for chat in channel.allowed:
            before = mine.get(chat)
            going = threading.Thread(target=channel.delete, args=(chat, before), daemon=True)
            if before:
                going.start()
            shown = channel.show(chat, None, text, bottom=False, status=True)
            channel.lasting(chat, shown)
            mine[chat] = shown[0] if shown else None
            if going.is_alive():
                going.join()
        Home.store().put("listening", channel.name, mine)

    def listen_until_ended(self):
        threads = [
            threading.Thread(target=self.listen, args=(channel,), daemon=True) for channel in self.channels
        ]
        for thread in threads:
            thread.start()
        threading.Thread(target=self.keep_up, daemon=True).start()
        while any(thread.is_alive() for thread in threads) and not self.a_channel_closed():
            time.sleep(ENDED_LOOK_SECONDS)
        raise KeyboardInterrupt

    def close_channels(self):
        for channel in self.channels:
            channel.closed = True

    def a_channel_closed(self):
        return any(channel.closed for channel in self.channels)

    def keep_up(self):
        self.adopt_exercise()
        looked = collected = 0.0
        while not self.a_channel_closed():
            time.sleep(ENDED_LOOK_SECONDS)
            self.say_state_changes()
            self.say_idle_endings()
            if time.monotonic() - collected >= EXPIRED_MESSAGES_SWEEP_SECONDS:
                collected = time.monotonic()
                for channel in self.channels:
                    channel.collect()
            if time.monotonic() - looked >= STATUS_LOOK_SECONDS:
                looked = time.monotonic()
                self.say_status()
                if self.exercise_reporter is None:
                    self.adopt_exercise()

    def listen(self, channel):
        if channel not in self.consoles:
            self.start_chat(channel)
        while not channel.closed:
            for message in channel.listened(timeout=1 if channel in self.consoles else 20):
                if channel not in self.consoles:
                    say.text(f"[{time.strftime('%H:%M:%S')}] {channel.name}: {message.text}")
                self.hear(channel, message)

    def start_chat(self, channel):
        channel.skip_waiting()
        channel.offer(offered())
        with self.post_lock:
            self.chats_started.add(channel)
            channel.clear()
            self.post_banner(channel, self.banner_lines())
            self.post_status(channel, self.said_status)

    @staticmethod
    def post_banner(channel, lines):
        mine = Home.store().get("banner", channel.name) or {}
        for chat in channel.allowed:
            if mine.get(chat):
                channel.delete(chat, mine[chat])
            sent = channel.send_banner(chat, lines)
            channel.lasting(chat, [sent])
            mine[chat] = sent
        Home.store().put("banner", channel.name, mine)

    def hear(self, channel, message):
        self.handle(channel, message)
        self.renew_banner_when_changed()

    def handle(self, channel, message, asked=None):
        self.handled(channel, message, asked)
        self.left_if_gone(channel, message.chat)

    def handled(self, channel, message, asked):
        said = self.as_said(channel, self.with_words_wanted(channel, message))
        self.cancel_unless_confirmed(channel, said)
        if message.text.split()[:1] == [CHAT_APP_START]:
            channel.send(message.chat, welcome())
            return
        if message.text.split() == [CHAT_FRESH]:
            self.fresh(channel, message)
            return
        message = said
        if message.text.split() == [CHAT_LEAVE]:
            channel.send(message.chat, self.left(channel, message.chat))
            return
        if self.answered_inside(channel, message):
            return
        command = command_of(message.text)
        unknown = not command and unknown_command(message.text)
        if unknown:
            channel.send(message.chat, unknown)
            return
        already_answered_in_a_message = message.id and (
            command in ("confirm",) + EXIT_WORDS or exercise_switch(message.text)
        )
        if already_answered_in_a_message:
            channel.delete(message.chat, message.id)
        if self.settle(channel, message, command):
            return
        if command in COMMANDS_ASKING_FOR_WORDS and len(message.text.split()) == 1:
            self.questions.ask(channel, message.chat, Question(wanted=command))
            channel.ask_for(message.chat, *words_asked(command))
            return
        if self.chose(channel, message, command):
            return
        if not command:
            self.talk.answer(
                channel,
                message,
                self.running_now,
                ChatState(self),
                on_command=lambda line: self.replied(channel, message, line),
            )
            return
        mode_before = self.inside.get(message.chat)
        reply = self.answer(message, channel)
        if not reply:
            return
        if asked is not None:
            sent = channel.send(message.chat, self.told(channel, message, reply, asked))
        else:
            sent = channel.send(message.chat, reply)
            if kind_of(command_words(message.text)) == "glance":
                channel.glance(message.chat, message.id, [sent])
        if words := next_page_words(reply):
            channel.offer_next(message.chat, sent, words)
        self.offer_if_entered(channel, message.chat, sent, mode_before)

    def offer_if_entered(self, channel, chat, sent, mode_before):
        mode = self.inside.get(chat)
        if mode is not None and mode is not mode_before:
            mode.offer(self, channel, chat, sent)

    def left(self, channel, chat):
        if chat not in self.inside:
            return "📕 **Not inside a reading, the interview or a job**"
        return self.leave_mode(channel, chat).left_line()

    def left_if_gone(self, channel, chat):
        mode = self.inside.get(chat)
        if mode and mode.gone(self.shelf):
            self.leave_mode(channel, chat)

    def leave_mode(self, channel, chat):
        mode = self.inside.pop(chat)
        channel.now_inside(chat, None)
        return mode

    def answered_inside(self, channel, message):
        self.left_if_gone(channel, message.chat)
        if message.chat not in self.inside or not self.inside[message.chat].takes(message.text):
            return False
        self.inside[message.chat].answer(self, channel, message)
        return True

    def fresh(self, channel, message):
        question = last_asked(message.chat)
        if not question:
            channel.send(message.chat, NO_QUESTION_YET)
            return
        asked = Message(message.chat, question, message.sender)
        self.talk.answer(
            channel,
            asked,
            self.running_now,
            ChatState(self),
            write=self.talk.asked_reply,
            on_command=lambda line: self.replied(channel, asked, line),
        )

    def told(self, channel, message, reply, asked):
        kind = kind_of(command_words(message.text))
        if kind == "glance":
            return self.talk.told(channel, message.chat, asked, message.text, reply)
        if kind == "own" and not self.questions.waiting(channel, message.chat):
            return f"✅ {reply}"
        return reply

    def chose(self, channel, message, command):
        pick = model_pick(message.text)
        if not pick:
            return False
        try:
            rows = Models.command_choices(pick.split()[-1])
        except SystemExit:
            return False
        names = [Models.row_name(row) for row in rows if styled_choice(row)[1].selectable]
        if not names or channel.choose(message.chat, model_asked(pick, rows), names) is None:
            return False
        self.questions.ask(channel, message.chat, Question(wanted=pick))
        return True

    def as_said(self, channel, message):
        said = said_command(message.text)
        answers_a_question = said != "/confirm" and self.questions.waiting(channel, message.chat)
        if not said or answers_a_question:
            return message
        return Message(message.chat, said, message.sender, message.id)

    def with_words_wanted(self, channel, message):
        question = self.questions.waiting(channel, message.chat)
        if not question or not question.wanted:
            return message
        wanted = self.questions.answered(channel, message.chat).wanted
        if message.text.startswith("/"):
            return message
        return Message(message.chat, f"/{wanted} {message.text}", message.sender, message.id)

    def asked_with_words(self, channel, message, command):
        if len(message.text.split()) == 1:
            channel.send(message.chat, USAGE[command])
        else:
            self.shell.name(channel, after_word(message), self.running_now)

    def replied(self, channel, message, line):
        self.handle(channel, Message(message.chat, line, message.sender), message.text)

    def cancel_unless_confirmed(self, channel, said):
        question = self.questions.waiting(channel, said.chat)
        if not question or question.naming or said.text.split() == ["/confirm"]:
            return
        self.questions.answered(channel, said.chat).cancels()

    def settle(self, channel, message, command):
        confirm = command == "confirm" and len(message.text.split()) == 1
        question = self.questions.waiting(channel, message.chat)
        if question and question.naming:
            if confirm:
                channel.send(
                    message.chat, "⏳ The command is still being worked out: `/confirm` once it is shown."
                )
            return confirm
        question = self.questions.answered(channel, message.chat)
        if question and confirm:
            threading.Thread(target=self.settled, args=(channel, message.chat, question), daemon=True).start()
            return True
        return False

    def settled(self, channel, chat, question):
        question.runs()
        self.left_if_gone(channel, chat)

    def answer(self, message, channel):
        words = command_words(message.text)
        handler = self.handlers(channel, message, words).get(words[0])
        with channel.waiting(message.chat, f"Running /{' '.join(words)}"):
            return handler() if handler is not None else self.by_table(channel, message.chat, words)

    def by_table(self, channel, chat, words):
        kind = kind_of(words)
        if kind == "terminal":
            return self.terminal_kind(channel, chat, words)
        return self.run_listed(channel, chat, words, kind)

    def terminal_kind(self, channel, chat, words):
        here = channel in self.consoles
        if here and words[1] == "add" and typed_here():
            return self.added_and_started(channel, words[2:])
        if here and words[1] == "start":
            return self.listen_on(words[2:])
        if here and words[1] == "remove":
            return self.run_listed(channel, chat, words, "terminal")
        if words[1] == "add":
            return (
                "🖥 Channels are set up from the terminal: `tinker channel add` there "
                "(its token would land in the chat's history, and pairing needs the terminal)"
            )
        return f"🖥 `{' '.join(words[:2])}` asks in a terminal: `tinker {' '.join(words[:2])}` there"

    def added_and_started(self, console, options):
        from .chat.channels import Channels

        name = console.channel_added(options)
        if name is None:
            return ""
        Channels.keep(name, dict(Channels.settings()[name], enabled=True))
        return self.listen_on([name])

    def run_listed(self, channel, chat, words, kind):
        if deletes(words) and "--yes" not in words:
            return self.ask_first(channel, chat, words)
        if kind == "job":
            return self.start_task([(channel, chat)], words, self.root, " ".join(words))
        shown = self.tinker(words)
        return md(t"⚙️ **{' '.join(words)}**\n{shown:block}") + self.listen_once_enabled(channel, words)

    def listen_once_enabled(self, channel, words):
        from .chat.channels import Channels

        if channel not in self.consoles or words[:2] != ["channel", "enable"] or len(words) < 3:
            return ""
        name = words[2]
        listened = {listening.name for listening in self.channels}
        if name in listened or not Channels.settings().get(name, {}).get("enabled"):
            return ""
        return "\n" + self.listen_on([name])

    def handlers(self, channel, message, words):
        command = words[0]
        return {
            "task": lambda: self.new_task([(channel, message.chat)], words),
            "confirm": lambda: self.decide([(channel, message.chat)], command, words),
            "amend": lambda: self.decide([(channel, message.chat)], command, words),
            "rename": lambda: self.decide([(channel, message.chat)], command, words),
            "resume": lambda: self.resume([(channel, message.chat)], words),
            "stop": lambda: self.stop(words),
            "queue": lambda: self.queue(words),
            "research": lambda: self.research([(channel, message.chat)], words),
            "exercise": lambda: self.exercise(channel, message, words),
            "runs": lambda: self.report(command, words),
            "status": lambda: self.report(command, words),
            "help": lambda: help_text(words[1:2]),
            "history": lambda: history_text(words[1:2]),
            "run": lambda: self.run_app_apart(channel, message),
            "command": lambda: self.asked_with_words(channel, message, command),
            "clear": lambda: self.clear(channel, message.chat, words),
            "read": lambda: self.read(channel, message.chat, words),
            "knowledge": lambda: self.knowledge(channel, message.chat, words),
            "memory": lambda: self.memory_or_dream(channel, message.chat, words),
            "dream": lambda: self.memory_or_dream(channel, message.chat, words),
            "interact": lambda: self.interviewed(channel, message.chat),
            "jobs": lambda: self.jobs(channel, message.chat, words),
            **{word: lambda: self.ask_to_exit(channel, message.chat, command) for word in EXIT_WORDS},
        }

    def run_app_apart(self, channel, message):
        threading.Thread(
            target=self.run_app, args=(channel, message.chat, message.text.split(None, 1)[1:]), daemon=True
        ).start()

    def clear(self, channel, chat, words):
        if words[1:] in ([], ["chat"]):
            return self.clear_chat(channel, chat, "Chat cleared", " · `/history` shows any of them again")
        return self.by_table(channel, chat, words)

    def read(self, channel, chat, words):
        if words[1:2] in (["list"], ["show"]):
            return md(t"📚 **{' '.join(words)}**\n{self.tinker(words, env=self.inside_env(chat)):block}")
        reply = self.by_table(channel, chat, words)
        return reply and reply + self.entered(channel, chat, words[1:])

    def inside_env(self, chat):
        return self.inside[chat].env() if chat in self.inside else {}

    def entered(self, channel, chat, words):
        reading = self.shelf.entered(words)
        if not reading:
            return ""
        return self.entered_mode(channel, chat, InsideReading(*reading))

    def open_interview(self):
        console, chat = self.consoles[0], ConsoleChannel.CHAT
        sent = console.send(chat, self.interviewed(console, chat))
        self.offer_if_entered(console, chat, sent, None)

    def interviewed(self, channel, chat):
        turn = self.interview.turn(chat)
        if turn.over:
            return turn.text
        mode = InsideInterview(self.interview)
        self.now_in(channel, chat, mode)
        return mode.opening(turn.text)

    def jobs(self, channel, chat, words):
        number = job_number(words[1]) if len(words) == 2 else None
        if number is None:
            return self.by_table(channel, chat, words)
        job = Jobs.get(number)
        if job is None:
            return md(t"⚙️ **No job #{number}** (`/jobs` lists them)")
        mode = InsideJob(number, job["title"])
        if mode.gone(self.shelf):
            return mode.shown_once()
        return self.entered_mode(channel, chat, mode)

    def entered_mode(self, channel, chat, mode):
        self.now_in(channel, chat, mode)
        return mode.entered_line(chat)

    def now_in(self, channel, chat, mode):
        self.inside[chat] = mode
        channel.now_inside(chat, mode)

    def knowledge(self, channel, chat, words):
        if words[1:] == ["graph"]:
            return self.knowledge_graph(channel, chat, words)
        return md(t"🧠 **{' '.join(words)}**\n{self.tinker(words):block}")

    def memory_or_dream(self, channel, chat, words):
        if words[1:] == ["graph"]:
            return self.memory_graph(channel, chat, words)
        return self.by_table(channel, chat, words)

    def research(self, audience, words):
        return (
            self.start_task(audience, words, self.root, " ".join(words))
            if len(words) > 1
            else USAGE["research"]
        )

    def exercise(self, channel, message, words):
        if len(words) < 2:
            return USAGE["exercise"]
        if words[1].lower() != "status":
            return self.start_exercise([(channel, message.chat)], words)

        def answer():
            sent = channel.send(message.chat, md(t"🏋️ **{' '.join(words)}**\n{self.tinker(words):block}"))
            channel.glance(message.chat, message.id, [sent])

        threading.Thread(target=answer, daemon=True).start()
        return None

    def stop_exercise(self):
        running_one = exercising_now()
        if not running_one:
            return "🤷 No exercise runs: `/resume #N` goes on with one (`/jobs stopped` lists them)"
        signal_task(running_one["pid"], JOB_STOP_SIGNAL)
        self.exercise_says("🛑 Stopping: its build saves itself first")
        return None

    def start_exercise(self, audience, words, title=None):
        log = projects_folder() / EXERCISE_FOLDER / "exercise.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        start = log.stat().st_size if log.exists() else 0
        try:
            with open(log, "a", encoding="utf-8") as output:
                process = tinker_process(
                    words, self.root, output, **{CHANNEL_REPORTS_ENV: "off", "NO_COLOR": "1"}
                )
        except OSError as exc:
            return md(t"⚠️ **Could not start it:** {exc}")
        self.follow_exercise(
            audience, process.pid, title or " ".join(words), process.wait, (log, start), anew=True
        )
        return None

    def adopt_exercise(self):
        exercise = exercising_now()
        lost = next((job for job in Jobs.lost() if job["command"] == "exercise"), None)
        audience = shown_in(self.channels, exercise_board())
        if exercise and not exercise["reports"]:
            self.follow_exercise(
                audience, exercise["pid"], exercise["title"], lambda: until_ended(exercise["pid"]), anew=True
            )
        elif not exercise and lost:
            self.start_exercise(audience, ["resume", f"#{lost['number']}"], lost["title"])

    def follow_exercise(self, audience, pid, title, ended, log=None, anew=False):
        sent = exercise_board()
        if anew:
            self.take_out(sent)
        if self.exercise_reporter is not None:
            self.exercise_reporter.ended = True
        reporter = self.exercise_reporter = Reporter(
            audience,
            title,
            pid=pid,
            sent=sent,
            on_step=lambda: (keep_exercise_board(sent), self.say_status()),
        )
        reporter.refresh()

        def follow():
            ended()
            went_to_another_exercise = reporter is not self.exercise_reporter
            if went_to_another_exercise:
                return
            printed = printed_since(*log) if log and reporter.tracked() is None else ""
            reporter.finish()
            if printed:
                reporter.say(md(t"⚠️ **{title}**\n{printed:block}"), done=True)
            keep_exercise_board(sent)
            if self.exercise_reporter is reporter:
                self.exercise_reporter = None
            self.say_status()

        threading.Thread(target=follow, daemon=True).start()

    def take_out(self, sent):
        for channel in self.channels:
            for (name, chat), ids in list(sent.items()):
                if name == channel.name:
                    for old in ids or []:
                        channel.delete(chat, old)
                    del sent[(name, chat)]

    def exercise_says(self, line):
        reporter = self.exercise_reporter
        if reporter is None:
            return
        with reporter.lock:
            reporter.progress.snapshot["latest"] = line
            reporter.send_at_bottom = True
            reporter.update()

    def knowledge_graph(self, channel, chat, words):
        return self.page_sent(channel, chat, words, Home.graph_page(), "🧠", "Tinker's memory")

    def memory_graph(self, channel, chat, words):
        return self.page_sent(
            channel, chat, words, Home.memory_page(), "💭", "Tinker's episodes and memories"
        )

    def page_sent(self, channel, chat, words, page, icon, title):
        before = Page(page).seen()
        shown = self.tinker(words)
        if not Page(page).changed_since(before):
            return md(t"{icon} **{' '.join(words)}**\n{shown:block}")
        channel.send_file(chat, page, md(t"{icon} **{title}**\n{shown}"))
        return None

    def new_task(self, audience, words):
        if len(words) < 2:
            return USAGE["task"]
        request = " ".join(words[1:])
        return self.start_task(audience, ["task", request], self.root, request)

    def decide(self, audience, command, words):
        waiting = self.waiting_plan
        if command == "rename":
            return self.rename(audience, waiting, words)
        name, rest = refs(words[1:])
        if command == "amend" and not rest:
            return USAGE["amend"]
        if waiting.asks_name(command, name):
            return self.ask_folder_name(audience, waiting)
        target = name or waiting.run
        if not target:
            return "🤷 No plan waits for confirmation: start one with `/task WHAT TO BUILD`"
        mine = waiting.owns(target)
        folder, title = (waiting.folder, waiting.title) if mine else (self.root, f"@{target}")
        target = waiting.run if mine else target
        if command == "confirm":
            started = self.start_task(
                audience,
                ["resume", f"@{target}"],
                folder,
                title,
                "✅ **Confirmed:** building it",
                keep=waiting.plan if mine else "",
            )
        else:
            change = " ".join(rest)
            started = self.start_task(
                audience,
                ["amend", f"@{target}", change],
                folder,
                title,
                md(t"✏️ **Changing the plan:** {change}"),
            )
        return self.on_task(audience, waiting if mine else NoWaitingPlan(), started)

    def ask_folder_name(self, audience, waiting):
        self.waiting_plan = named = waiting.named()
        self.on_board(audience, named, named.asked())
        return None

    def on_task(self, audience, waiting, said):
        if said and waiting:
            self.on_board(audience, waiting, f"{waiting.asked()}\n\n{said}")
            return None
        return said

    def on_board(self, audience, waiting, text):
        board = self.messages_by_run.setdefault(waiting.run, {})
        for channel, chat in audience:
            board[(channel.name, chat)] = channel.show(chat, board.get((channel.name, chat)), text, done=True)
        self.say_status()

    def rename(self, audience, waiting, words):
        if len(words) != 2:
            return USAGE["rename"]
        if not waiting:
            return "🤷 No plan waits for confirmation, so no folder can be renamed (once built, it stays)."
        if self.busy():
            return self.on_task(audience, waiting, "⏳ A task is running: wait for it, or `/stop` it first.")
        why = self.rename_folder(waiting, words[1])
        if why is not None:
            return self.on_task(audience, waiting, md(t"⚠️ **Not renamed:** {why}"))
        waiting.renamed(words[1])
        return waiting.after_rename(self, audience, words[1])

    def renamed_in_place(self, audience, waiting, name):
        self.on_board(audience, waiting, f"{waiting.shown()}\n\n📁 **Renamed:** its folder is `{name}`")

    def build_renamed(self, audience, waiting, name):
        return self.on_task(
            audience,
            waiting,
            self.start_task(
                audience,
                waiting.resumed(),
                waiting.folder,
                waiting.title,
                f"✅ **Renamed** to `{name}`: building it",
                keep=waiting.plan,
            ),
        )

    def rename_folder(self, waiting, name):
        done = self.run_tinker(["rename", f"@{waiting.run}", name], 60)
        if done.returncode:
            return first_lines((done.stdout + done.stderr).strip(), 10).splitlines()[-1]
        return None

    def resume(self, audience, words):
        name, rest = refs(words[1:])
        if name and not rest:
            return self.start_task(audience, ["resume", f"@{name}"], self.root, f"resume @{name}")
        number = job_number(words[1]) if len(words) == 2 else None
        if len(words) > 2 or (len(words) == 2 and number is None):
            return USAGE["resume"]
        job, why = Jobs.resumable(number)
        if job is None:
            return md(t"🤷 {why.replace('tinker ', '')}")
        again = ["resume", f"#{job['number']}"]
        if job["command"] == "exercise":
            return self.start_exercise(audience, again, job["title"])
        return self.start_task(audience, again, self.root, job["title"])

    def stop(self, words):
        if len(words) > 1:
            number = job_number(words[1])
            return self.stop_job(number) if number is not None else USAGE["stop"]
        said = [self.stop_job(job["number"]) for job in Jobs.listed("running")]
        return "\n".join(line for line in said if line) or (None if said else "💤 No job is running.")

    def stop_job(self, number):
        job = Jobs.get(number)
        if job is None or number not in Queue.ordered():
            return f"🤷 Job #{number} is not in the queue (`/queue list` shows it)."
        if job["state"] == "queued":
            Jobs.stop(job)
        elif self.busy() and job["pid"] == self.last_task.pid:
            self.stop_task()
        elif job["command"] == "exercise":
            return self.stop_exercise()
        else:
            Jobs.stop(job)
        if job["command"] == "chat":
            return md(t"⏹ **Stopped #{number}**, the answer.")
        return md(t"⏹ **Stopping #{number}** {job['title']}: it is saved; `/resume #{number}` goes on later.")

    def queue(self, words):
        action = queue_action(words[1:])
        if action is None:
            return USAGE["queue"]
        if action == "list":
            return "\n".join(queue_lines())
        if action != "all":
            return self.stop_job(action)
        head = emptied_queue()
        said = ["🧹 Every waiting job is out of the queue."]
        if head is not None:
            said.append(self.stop_job(head))
        return "\n".join(line for line in said if line)

    def report(self, command, words):
        if command == "status" and len(words) == 1:
            return "\n\n".join([self.status()] + [run["text"] for run in self.running_now()])
        title = "📚 **Runs**" if command == "runs" else md(t"📊 **Status** {' '.join(words[1:2])}").rstrip()
        return f"{title}\n```\n{Markup.fenced(self.tinker([command] + words[1:2]))}\n```"

    def ask_to_exit(self, channel, chat, command):
        shown = channel.show(
            chat,
            None,
            "🛑 **Stop Tinker?** It cannot be started again from here.\n"
            "👉 `/confirm` to stop it · anything else keeps it",
            done=True,
        )
        self.wait_for_confirm(channel, chat, {"words": [command], "shown": shown})
        return None

    def ask_first(self, channel, chat, words):
        try:
            preview = self.printed(words)
        except PreviewFailed as failed:
            return md(t"⚙️ **{' '.join(words)}**\n{str(failed):block}")
        shown = channel.show(
            chat,
            None,
            md(
                t"🗑 **{' '.join(words)}**\n{preview:block}\n"
                t"👉 `/confirm` to delete it · anything else cancels it"
            ),
            done=True,
        )
        self.wait_for_confirm(channel, chat, {"words": words, "shown": shown})
        return None

    def wait_for_confirm(self, channel, chat, asking):
        self.questions.ask(
            channel,
            chat,
            Question(
                runs=lambda: self.delete(channel, chat, asking),
                cancels=lambda: self.not_done(channel, chat, asking),
            ),
        )

    @staticmethod
    def not_done(channel, chat, asking):
        channel.show(
            chat, asking["shown"], f"🚫 _Not done:_ `{' '.join(asking['words'])}`", done=True, bottom=False
        )

    def delete(self, channel, chat, asking):
        if asking["words"][0] in EXIT_WORDS:
            self.stop_question = (channel, chat, asking["shown"])
            self.close_channels()
            return
        words = asking["words"] + ["--yes"]
        if kind_of(words) == "job":
            refused = self.start_task([(channel, chat)], words, self.root, " ".join(asking["words"]))
            if refused:
                channel.show(chat, asking["shown"], refused, done=True)
            return
        clears_history = words[0] == "clear" and "history" in words
        if clears_history:
            self.tinker(words)
            self.clear_chat(channel, chat, md(t"{' '.join(asking['words'])}: done, and the chat"))
            return
        channel.show(
            chat,
            asking["shown"],
            md(t"🗑 **{' '.join(asking['words'])}**\n{self.tinker(words):block}"),
            done=True,
        )

    def clear_all_chats(self):
        taken = sum(each.clear() for each in self.channels)
        for console in self.consoles:
            console.wipe()
        self.banner_on_consoles()
        return taken

    def clear_chat(self, channel, chat, cleared, after=""):
        taken = self.clear_all_chats()
        sent = channel.send(chat, f"🧹 **{cleared}:** {taken} messages taken out{after}")
        channel.glance(chat, None, [sent], DONE_NOTE_KEPT_SECONDS)
        return None

    def run_app(self, channel, chat, said):
        name, args, stdin = app_request(said[0] if said else "")
        workdir, toolchain, refusal = self.runnable(name)
        if refusal:
            channel.show(chat, None, refusal, done=True)
            return
        title = workdir.name
        line = " ".join([toolchain.run] + [shlex.quote(arg) for arg in args])
        shown = channel.show(chat, None, md(t"▶️ **{title}** {line:code} …"))
        code, output = in_shell(Shell.LOGIN_SHELL, toolchain.shell(line), workdir, stdin)
        channel.show(chat, shown, self.app_ran(title, line, code, output, stdin), done=True)

    @staticmethod
    def runnable(name):
        project = built_project(name)
        if project is None:
            return (
                None,
                None,
                md(t"🤷 **No project @{name}:** `/runs` lists them")
                if name
                else "🤷 **No project is built yet:** `/task WHAT TO BUILD` builds one",
            )
        workdir = Path(project["workdir"])
        toolchain = Toolchain((saved(workdir, "spec", "plan", {}) or {}).get("toolchain"))
        if not toolchain.run:
            return (
                workdir,
                toolchain,
                md(t"🤷 **{workdir.name} has no run command:** it is not a program a person runs"),
            )
        return workdir, toolchain, ""

    @staticmethod
    def app_ran(title, line, code, output, stdin):
        fed = md(t"\n_input:_ `{stdin.strip()}`") if stdin else ""
        printed = (
            f"```\n{Markup.fenced(output[-CHAT_COMMAND_OUTPUT_SHOWN_CHARS:])}\n```"
            if output
            else "_(it printed nothing)_"
        )
        return md(
            t"{'✅' if code == 0 else '❌'} **{title}** {line:code} · _exit {code}_{fed:md}\n{printed:md}"
        )

    def start_task(self, audience, arguments, folder, title, phase=Tracker.STARTING, sent=None, keep=""):
        run = self.run_named(arguments)
        sent = sent if sent is not None else self.messages_by_run.get(run) if run else None
        with self.lock:
            self.task = {"arguments": arguments, "folder": folder, "title": title, "run": run}
            self.reporter = Reporter(
                audience,
                title,
                sent=sent,
                phase=phase,
                plan_on_top=keep,
                before=self.seconds_by_run.get(run, 0.0),
            )
            try:
                self.last_task = self.spawn(arguments)
            except OSError as exc:
                self.last_task = None
                return md(t"⚠️ **Could not start it:** {exc}")
            self.reporter.pid = self.last_task.pid
            self.say_status()
            with self.reporter.lock:
                self.reporter.update()
        if self.waiting_plan.moves_on(folder):
            self.waiting_plan = NoWaitingPlan()
        threading.Thread(
            target=self.follow, args=(self.last_task, self.reporter, self.task), daemon=True
        ).start()
        return None

    def run_named(self, arguments):
        name = refs(arguments[1:2])[0] if arguments[0] in ("resume", "amend") else ""
        return self.waiting_plan.run_for(name)

    def spawn(self, arguments):
        arguments = arguments + ["--confirm"] if arguments[0] in PLAN_CONFIRMING_COMMANDS else arguments
        return tinker_process(
            arguments, self.root, subprocess.PIPE, **{CHANNEL_REPORTS_ENV: "off"}, NO_COLOR="1"
        )

    def follow(self, process, reporter, task):
        looked = 0.0
        for line in process.stdout:
            if self.echo_to_terminal:
                sys.stdout.write(line)
            reporter.printed(line)
            if time.monotonic() - looked >= self.TRACKER_LOOK_SECONDS:
                looked = time.monotonic()
                self.learn(task, reporter, reporter.tracked())
        process.wait()
        self.ended(process, reporter, task)

    def learn(self, task, reporter, state):
        if not state:
            return
        if state.get("run"):
            task["run"] = state["run"]
            self.messages_by_run[task["run"]] = reporter.sent
        if state.get("folder"):
            task["folder"] = Path(state["folder"])
        if state.get("title"):
            task["title"] = state["title"]

    def ended(self, process, reporter, task):
        final = reporter.tracked()
        self.learn(task, reporter, final)
        if task["run"]:
            self.seconds_by_run[task["run"]] = reporter.progress.snapshot.get("before", 0.0) + (
                final or {}
            ).get("seconds", 0.0)
        reporter.finish(final)
        if task["run"] and reporter.progress.waiting():
            plan = reporter.progress.snapshot["waiting"]
            self.waiting_plan = WaitingPlan(
                task["run"],
                Path(task["folder"]),
                task["title"],
                reporter.progress.planned(plan),
                reporter.progress.time_taken(),
                plan.get("kind") == "project",
            )
        self.say_status()

    def stop_task(self):
        if not self.busy():
            return False
        try:
            os.killpg(self.last_task.pid, signal.SIGINT)
        except ProcessLookupError, PermissionError:
            return False
        return True

    def busy(self):
        return self.last_task is not None and self.last_task.poll() is None

    def running_now(self):
        ordered = Queue.ordered()
        jobs = [job for job in (Jobs.get(number) for number in ordered) if job]
        return [self.running(job, job["number"] == ordered[0]) for job in jobs]

    def running(self, job, at_head):
        if at_head and self.busy():
            progress = self.reporter.progress
            return {"title": progress.title, "brief": progress.brief(), "text": progress.text()}
        return described(dict({"title": job["title"]}, **(job.get("progress") or {})))

    def tinker(self, arguments, timeout=120, env=None):
        done = self.run_tinker(arguments, timeout, env)
        return shown_output(done.stdout + done.stderr)

    def printed(self, arguments):
        done = self.run_tinker(arguments, 120)
        if done.returncode and not done.stdout.strip():
            raise PreviewFailed(shown_output(done.stderr))
        return shown_output(done.stdout)

    def run_tinker(self, arguments, timeout, env=None):
        return subprocess.run(
            tinker_command() + arguments,
            cwd=self.root,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=tinker_environment(NO_COLOR="1", **(env or {})),
            timeout=timeout,
        )


class ChatState:
    PIECES = ("channels", "model", "running", "waiting", "projects")
    USES = frozendict(
        channel=("channels",),
        model=("model",),
        stop=("running", "waiting"),
        jobs=("running", "waiting"),
        resume=("running", "waiting"),
        queue=("running", "waiting"),
        confirm=("waiting",),
        amend=("waiting",),
        rename=("waiting",),
    )

    def __init__(self, desk):
        self.desk = desk

    def everyday(self):
        return self.joined(("running", "waiting"))

    def for_commands(self, names):
        used = {piece for name in names for piece in self.pieces_of(name)}
        return self.joined(piece for piece in self.PIECES if piece in used)

    def pieces_of(self, name):
        command = BY_NAME.get(name)
        takes_a_project = command and any(
            "@PROJECT" in (argument.metavar or "") for argument in command.arguments
        )
        return self.USES.get(name, ()) + (("projects",) if takes_a_project else ())

    def joined(self, pieces):
        return "\n\n".join(text for text in (getattr(self, piece)() for piece in pieces) if text)

    def running(self):
        running = self.desk.running_now()
        return "Running now:\n" + "\n\n".join(run["text"] for run in running) if running else ""

    def waiting(self):
        plan = self.desk.waiting_plan.shown()
        return (
            f"A spec waits for the person (confirm builds it, amend WHAT changes it):\n{plan}" if plan else ""
        )

    def channels(self):
        from .chat.channels import Channels

        listened = {channel.name for channel in self.desk.channels}
        channels = [
            f"- {name} ({settings.get('kind', '?')}, {'enabled' if settings.get('enabled') else 'disabled'}): "
            + ("listening" if name in listened else "not listening")
            for name, settings in Channels.settings().items()
        ]
        return "Channels:\n" + "\n".join(channels) if channels else "Channels: none set up"

    def model(self):
        return f"Model in use: {Config.get('model') or 'none picked yet'}"

    def projects(self):
        entries = Runs.entries()
        lines = [
            f"@{entry.get('id', '?')} {Runs.short_folder(entry.get('workdir', ''))}: {Runs.task_of(entry)}"
            for entry in entries
        ]
        return "Known projects:\n" + "\n".join(lines) if lines else ""


class NoChatState:
    def everyday(self):
        return ""

    def for_commands(self, names):
        return ""


NO_CHAT_STATE = NoChatState()


def shown_output(output):
    return first_lines(output.strip() or "(nothing)", 40)


def replied_commands(reply):
    lines = [line.strip().strip("`").strip() for line in reply.splitlines()]
    lines = [line for line in lines if line]
    if lines and all(line.startswith("/") and command_of(line) for line in lines):
        return lines
    return glance_in_sentence(reply)


def answered_episode(message, reply, lines):
    said = reply[0] if reply else ""
    failed = not said or said.startswith("⚠️")
    episode = {"chat": message.chat, "message": message.text, "reply": said}
    commands = {"commands": lines} if lines else {}
    return dict(episode, **commands, status=reply_status(said, failed))


def reply_status(said, failed):
    if failed:
        return "failed"
    return "remembered" if isinstance(said, Remembered) else "answered"


def glance_in_sentence(reply):
    spans = [span.strip() for span in re.findall(r"`([^`\n]+)`", reply) if span.strip().startswith("/")]
    if len(spans) == 1 and command_of(spans[0]) and kind_of(command_words(spans[0])) == "glance":
        return spans
    return []


SAID_FILLER = tuple(
    "the my please can could would you tinker a an me now just show see view display what is it s usage".split()
)


def said_command(text):
    if text.startswith("/"):
        return ""
    words = tuple(word for word in re.sub(r"[^\w\s]", " ", text.lower()).split() if word not in SAID_FILLER)
    phrases = said_phrases()
    if words in phrases:
        return "/" + " ".join(words)
    named = [phrase for phrase in phrases if words and phrase[1:] == words and not deletes(list(phrase))]
    return "/" + " ".join(named[0]) if len(named) == 1 else ""


def said_phrases():
    return {
        (command.name, *words)
        for command in COMMANDS
        if command.chat
        for synopsis, _ in command.rows
        for words in synopsis_phrases(synopsis.split()[1:])
    }


def synopsis_phrases(words):
    tokens = re.findall(r"\[|\]|\||[^\s\[\]|]+", " ".join(words))
    phrases, _ = alternatives_phrases(tokens, 0)
    return phrases


def alternatives_phrases(tokens, at):
    phrases, at = sequence_phrases(tokens, at)
    while at < len(tokens) and tokens[at] == "|":
        more, at = sequence_phrases(tokens, at + 1)
        phrases |= more
    return phrases, at


def sequence_phrases(tokens, at):
    phrases = {()}
    while at < len(tokens) and tokens[at] not in ("|", "]"):
        item, at = item_phrases(tokens, at)
        phrases = {phrase + more for phrase in phrases for more in item}
    return phrases, at


def item_phrases(tokens, at):
    token = tokens[at]
    if token == "[":
        inside, at = alternatives_phrases(tokens, at + 1)
        return {()} | inside, at + 1
    if token.startswith("--"):
        return {()}, at + 1
    return ({(token,)} if re.fullmatch(r"[a-z]+", token) else set()), at + 1


def unknown_command(text):
    word = text.split()[0].partition("@")[0] if text.startswith("/") else ""
    return f"❓ No command {word}: /help lists them" if len(word) > 1 else ""


def held_by_another(channel):
    return f"{channel.name} is listened to by another Tinker (process {channel.holder().get('pid', '?')})"


def offered():
    return [MenuEntry(command.name, command.what) for command in COMMANDS if command.chat]


def words_asked(command):
    found = BY_NAME[command]
    needed = found.synopsis.split(None, 1)[1]
    example = found.example.split(None, 1)[1] if " " in found.example else ""
    question = md(t"✍️ **/{command}** · {found.what}\nSend {needed:code} now")
    return WordsAsked(question + (md(t", e.g. {example:code}") if example else ""), example or needed)


def model_asked(pick, rows):
    current = Config.get("model")
    asked = md(t"🧠 **/{pick}** · choose a model") + (md(t" (in use: {current})") if current else "")
    listed = Markup.fenced("\n".join(Models.label(row) for row in rows))
    return "\n".join([asked, *(md(t"{line}") for line in Models.shown_recipe()), f"```\n{listed}\n```"])


def after_word(message):
    return Message(message.chat, message.text.split(None, 1)[1], message.sender)


def app_request(given):
    words, _, fed = given.partition("<<<")
    stdin = fed.strip() + "\n" if "<<<" in given else None
    try:
        words = shlex.split(words)
    except ValueError:
        words = words.split()
    name, args = refs(words)
    return AppRequest(name, args, stdin)


def welcome():
    return START_WELCOME


def thread_episodes(chat, now):
    since = now - datetime.timedelta(minutes=THREAD_MINUTES)
    said = [
        episode for episode in asked_in(chat) if datetime.datetime.fromisoformat(episode["time"]) >= since
    ]
    return said[-THREAD_TURNS:]


def last_asked(chat):
    said = asked_in(chat)
    return said[-1]["message"] if said else None


def asked_in(chat):
    return sorted(
        (
            episode
            for episode in Home.store().items(Scratchpad.EPISODES).values()
            if episode["chat"] == chat and "message" in episode
        ),
        key=written_order,
    )


def written_order(episode):
    return episode["time"], int(episode["id"].removeprefix("e"))


def thread_line(episode):
    if episode["dreamt"] and episode.get("gist"):
        line = episode["gist"]
    else:
        line = f"{episode['message']} → {' '.join(thread_reply(episode).split())}"
    return line[:EPISODE_GIST_CHARS]


def thread_part(lines):
    if not lines:
        return ""
    return f"\n\n{Part(Prompts.EARLIER, Part.bullets(lines)).render()}"


class Talk:
    def __init__(self, commands, pausing_jobs=contextlib.nullcontext):
        self.commands, self.turn = commands, threading.Lock()
        self.pausing_jobs = pausing_jobs

    def answer(self, channel, message, now, state=NO_CHAT_STATE, write=None, on_shown=None, on_command=None):
        threading.Thread(
            target=self.answering,
            args=(channel, message, now, state, write or self.reply, on_shown, on_command),
            daemon=True,
        ).start()

    def answering(self, channel, message, now, state, write_reply, on_shown, on_command):
        running, written, reply = now(), threading.Event(), []
        asked = (channel, message, state, CHAT_MAX_REPLY_TOKENS)
        threading.Thread(target=self.write, args=(write_reply, asked, reply, written), daemon=True).start()
        note = self.busy_until(written, running, now, channel, message.chat)
        lines = replied_commands(reply[0]) if on_command and reply else []
        if not (reply and isinstance(reply[0], Cut)):
            Scratchpad.write(answered_episode(message, reply, lines))
        if lines:
            for shown in note or []:
                channel.delete(message.chat, shown)
            for line in lines:
                on_command(line)
            return
        note = channel.show(
            message.chat, note, reply[0] if reply else "⚠️ **The model did not answer.**", done=True
        )
        if reply and isinstance(reply[0], Remembered) and note:
            channel.offer_fresh(message.chat, note[-1])
        if on_shown:
            on_shown(note)

    def write(self, write_reply, asked, reply, written):
        try:
            with self.turn, self.pausing_jobs():
                reply.append(write_reply(*asked))
        finally:
            written.set()

    def busy_until(self, written, running, now, channel, chat):
        note = None
        if running or not written.wait(MODEL_BUSY_NOTE_AFTER_SECONDS):
            note = channel.show(chat, None, self.busy_note(running))
            while not written.wait(MODEL_BUSY_NOTE_EDIT_SECONDS):
                note = channel.show(chat, note, self.busy_note(now()))
        return note

    @staticmethod
    def busy_note(running):
        if not running:
            lines = [
                "⏳ **Getting ready to answer** (the AI model is loading, or finishing something). "
                "I'll answer here in a moment."
            ]
        else:
            lines = [
                "⏸ **Pausing** " + "; ".join(run["brief"] for run in running) + " to answer you.",
                "It goes on once you have your answer.",
            ]
        return "\n".join(lines)

    def reply(self, channel, message, state=NO_CHAT_STATE, tokens=CHAT_MAX_REPLY_TOKENS):
        remembered = Models.quick() and remembered_answer(message.text)
        return remembered or self.asked_reply(channel, message, state, tokens)

    def asked_reply(self, channel, message, state=NO_CHAT_STATE, tokens=CHAT_MAX_REPLY_TOKENS):
        if not Models.quick():
            return (
                "⚠️ **No model is in use yet**, so I cannot chat: `tinker model NAME` picks "
                f"one. These still work:\n{self.commands}"
            )
        memories = Memories(Retriever(Embeddings.configured()))
        episodes = thread_episodes(message.chat, datetime.datetime.now())
        thread = thread_part(
            [thread_line(episode) for episode in unanswered(memories, message.text, episodes)]
        )
        asked = [{"role": "user", "content": message.text}]
        try:
            text = self.routed(channel, message.chat, asked, thread)
            names = named_commands(text)
            if not names:
                text = self.answered(channel, message, asked, memories, state, thread, tokens)
            elif any(takes_words(name) for name in names):
                filled = self.filled(channel, message, asked, names, state, thread, tokens)
                text = routed_lines(filled, names) or slashed_lines(text)
            else:
                text = "\n".join(f"/{name}" for name in names)
        except OllamaError as exc:
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")
        return text or "_(no answer)_"

    def routed(self, channel, chat, asked, thread):
        system = Prompts.CHAT_ROUTE_SYSTEM.format(index=chat_index()) + thread
        return self.ask(channel, chat, [system_turn(system)] + asked, CHAT_ROUTE_TOKENS, "off", 0) or ""

    def answered(self, channel, message, asked, memories, state, thread, tokens):
        recalled = unanswered(memories, message.text, memories.recall(message.text))
        level = reviewed_level(message.text, thread)
        system = (
            Prompts.CHAT_SYSTEM.format(style=channel.STYLE, state=state_part(state.everyday()))
            + traits_part(message.text)
            + remembered_part(recalled)
            + thread
        )
        return self.ask(channel, message.chat, [system_turn(system)] + asked, tokens, level)

    def filled(self, channel, message, asked, names, state, thread, tokens):
        system = (
            Prompts.CHAT_FILL_SYSTEM.format(
                commands=command_details(names), state=state_part(state.for_commands(names))
            )
            + traits_part(message.text)
            + thread
        )
        return self.ask(channel, message.chat, [system_turn(system)] + asked, tokens, "off", 0)

    def told(self, channel, chat, asked, line, output):
        try:
            told = self.tell(channel, chat, asked, line, "", output, "low")
        except OllamaError:
            return output
        return told or output

    def tell(self, channel, chat, asked, line, exit_line, output, level):
        return self.ask(
            channel,
            chat,
            [
                {
                    "role": "system",
                    "content": Prompts.COMMAND_TELL_SYSTEM.format(style=channel.STYLE) + traits_part(asked),
                },
                {
                    "role": "user",
                    "content": f"They asked: {asked}\nCommand: {line}\n"
                    f"{exit_line}Output:\n{output[-CHAT_COMMAND_OUTPUT_READ_CHARS:]}",
                },
            ],
            CHAT_MAX_REPLY_TOKENS,
            level,
        )

    def ask(self, channel, chat, messages, tokens, level, temperature=None):
        done = threading.Event()
        threading.Thread(target=keep_typing, args=(channel, chat, done), daemon=True).start()
        try:
            return answer_or_stop(lambda: self.thought_reply(messages, tokens, level, temperature))
        finally:
            done.set()

    @staticmethod
    def thought_reply(messages, tokens, level, temperature=None):
        if level == "off":
            return Models.quick_reply(messages, tokens, level, temperature)
        reply = Models.quick_reply(messages, tokens + CHAT_THINK_TOKENS, level, temperature)
        if isinstance(reply, Cut) and not reply:
            return Models.quick_reply(messages, tokens, "off", temperature)
        return reply

    def warm(self):
        threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        if Models.quick():
            try:
                Models.quick_reply([{"role": "user", "content": "hi"}], 1)
            except OllamaError as exc:
                say.warn(f"The model did not load for chat: {first_words(exc)}")


class Questions:
    def __init__(self):
        self.by_chat = {}

    def ask(self, channel, chat, question):
        self.by_chat[(channel.name, chat)] = question

    def waiting(self, channel, chat):
        return self.by_chat.get((channel.name, chat))

    def answered(self, channel, chat):
        return self.by_chat.pop((channel.name, chat), None)


def shown_in(channels, board):
    return [(channel, chat) for channel in channels for name, chat in board if name == channel.name]


def help_text(words=()):
    try:
        done = subprocess.run(
            tinker_command() + ["help", *words],
            text=True,
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=tinker_environment(NO_COLOR="1", TINKER_FORMAT="markup"),
        )
    except OSError, subprocess.SubprocessError:
        return "❓ `/help` lists what you can send"
    shown = (done.stdout + done.stderr).strip()
    return shown if not words else md(t"❓ **help {' '.join(words)}**\n{shown:block}")


def history_text(words=()):
    word = words[0] if words else ""
    if word.startswith("#"):
        return history_thing(word)
    page = int(word) if word.isdigit() and int(word) > 0 else 1
    things, pages = Ledger.page(page)
    if not things:
        return "📜 **History:** nothing shown in the chats yet"
    lines = [f"📜 **History** · page {page} of {pages}"]
    for thing in things:
        asked = md(t" · _you: {first_words(thing['asked'], 40)}_") if thing.get("asked") else ""
        lines.append(
            md(t"`#{thing['number']}` {thing['time'][5:]} · {entry_headline(thing['text'])}") + asked
        )
    older = [next_page_line(page, pages, f"history {page + 1}")] if page < pages else []
    return "\n".join(lines + ["👉 `/history #N` shows one again"] + older)


def history_thing(word):
    thing = Ledger.thing(word[1:]) if word[1:].isdigit() else None
    if thing is None:
        return md(t"🤷 **No {word} in the history:** `/history` lists what it keeps")
    asked = md(t"💬 _you:_ {thing['asked']}\n") if thing.get("asked") else ""
    return md(t"📜 **#{thing['number']}** · {thing['time']}\n") + asked + "\n" + thing["text"]


def entry_headline(text):
    lines = [line for line in Markup.plain(text).splitlines() if line.strip()]
    return first_words(lines[0] if lines else "", 60)


def commands_brief():
    return "\n".join(re.findall(r"^- `[^`]+`.*$", help_text(), re.MULTILINE)) or "help"


def chat_index():
    chat_commands = [command for command in COMMANDS if command.name and command.chat]
    lines = []
    for group in GROUPS:
        purposes = [
            f"/{command.name} — {command.what}" for command in chat_commands if command.group == group
        ]
        lines += [f"{group}:", *purposes] if purposes else []
    return "\n".join(lines + [f"/skill {skill.name} — {skill.purpose}" for skill in Skills.all()])


def command_details(names):
    return "\n".join(line for name in names for line in details_of(name))


def details_of(name):
    if name in BY_NAME:
        return [f"- `/{synopsis}` {what}" for synopsis, what in BY_NAME[name].rows]
    skill = learned_skill(name)
    return skill_details(skill) if skill else []


def skill_details(skill):
    slots = ", ".join(f"{slot['name']} (e.g. {slot['sample']})" for slot in skill.slots)
    return [
        f"- `/skill {skill.name} WORDS` {skill.purpose}",
        f"  template: {skill.template}",
        f"  slots: {slots or 'none'}",
    ]


def learned_skill(name):
    words = name.split()
    return Skills.get(words[1]) if len(words) == 2 and words[0] == "skill" else None


def named_commands(reply):
    lines = [bare_line(line).removeprefix("/") for line in reply.splitlines()]
    names = [line_name(line) for line in lines if line]
    return names if names and all(names) else []


def routed_lines(reply, names):
    return "\n".join(line for line in reply.splitlines() if is_routed(line, names)).strip()


def slashed_lines(reply):
    bare = (bare_line(line) for line in reply.splitlines())
    return "\n".join(f"/{line.removeprefix('/')}" for line in bare if line)


def is_routed(line, names):
    bare = bare_line(line)
    if not (bare.startswith("/") and command_of(bare)):
        return True
    return line_name(bare.removeprefix("/")) in names


def bare_line(line):
    return line.strip().strip("`").strip()


def line_name(line):
    words = line.split()
    leading = (" ".join(words[:count]) for count in range(len(words), 0, -1))
    return next((name for name in leading if names_a_chat_command(name)), "")


def names_a_chat_command(name):
    command = BY_NAME.get(name)
    return bool(command and command.chat) or learned_skill(name) is not None


def takes_words(name):
    if name not in BY_NAME:
        return True
    return any(synopsis != name for synopsis, what in BY_NAME[name].rows)


def state_part(state):
    return f"\n\n{state}" if state else ""


def unanswered(memories, request, items):
    asking = [item for item in items if question_of(item)]
    matches = memories.matching_questions(request, [question_of(item) for item in asking])
    answered = {asking[index]["id"] for index in matches}
    return [item for item in items if item["id"] not in answered]


def question_of(item):
    if item["id"].startswith("m"):
        return item["gist"] if item["kind"] == "answer" else ""
    return item.get("message", "")


class Remembered(str):
    pass


def remembered_answer(request):
    memory = Memories(Retriever(Embeddings.configured())).answer_to(request)
    if not memory:
        return None
    return Remembered(
        f'💭 From memory (you asked "{memory["gist"]}" on {memory["last_seen"][:10]}):\n'
        f"{memory['text']}\n_`/fresh` asks the model again._"
    )


def started_by_a_conversation():
    return os.environ.get(CHANNEL_REPORTS_ENV) == "off"


def answer_or_stop(answer):
    outcome = {}

    def run():
        try:
            outcome["reply"] = answer()
        except Exception as error:
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    stopped = answer_stopped()
    while thread.is_alive() and not stopped.wait(0.1):
        pass
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("reply", "⏹ Stopped.")


def built_project(name=""):
    projects = [run for run in Runs.entries() if Path(str(run.get("workdir", ""))).is_dir()]
    if name:
        try:
            found = Runs.find(name)
        except SystemExit:
            return None
        return next((run for run in projects if run.get("id") == found.get("id")), None)
    done = [run for run in projects if run.get("status") == "done"]
    return max(done, key=lambda run: str(run.get("updated", "")), default=None)


def first_lines(text, count):
    lines = text.splitlines()
    return "\n".join(lines[:count] + ([f"… {len(lines) - count} more lines"] if len(lines) > count else []))
