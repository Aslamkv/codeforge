import contextlib
import os
import re
import shutil
import signal
import sys
from collections import namedtuple
lazy import fcntl
lazy import html
lazy import json
lazy import queue
lazy import shlex
lazy import subprocess
lazy import threading
lazy import time
lazy import webbrowser
lazy from pathlib import Path

from .commands import BY_NAME, COMMANDS, deletes, kind_of, refs
from .jobs import Jobs, alive, job_number, paused_for_you
from .project import Runs, Tracker, saved
lazy from .ledger import DONE_NOTE_KEPT_SECONDS, Ledger
lazy from .messages import (
    CLEAR_LINE,
    CLEAR_TO_LINE_END,
    Messages,
    char_width,
    colourful,
    cut_to_width,
    human_duration,
    live_line,
    paint,
    say,
    star_at,
    tinkering,
    visible_width,
)
lazy from .model import Models, OllamaError
lazy from .prompts import Prompts, Reply
lazy from .settings import EXERCISE_FOLDER, JOB_STOP_SIGNAL, Config, Home, projects_folder
lazy from .text import field
lazy from .workspace import Toolchain

CHANNEL_REPORTS_ENV = "TINKER_CHANNELS"
PROGRESS_EDIT_SECONDS = 2
CLOCK_ONLY_EDIT_SECONDS = 15
BUILD_STOP_GRACE_SECONDS = 120
STATUS_LOOK_SECONDS = 10
EXPIRED_MESSAGES_SWEEP_SECONDS = 2
ENDED_LOOK_SECONDS = 0.5
BANNER_MAX_COLUMNS = 64

CHAT_CONTEXT_MESSAGES = 12
CHAT_MAX_REPLY_TOKENS = 1024
TYPING_SIGN_SECONDS = 4
MODEL_BUSY_NOTE_AFTER_SECONDS = 5
MODEL_BUSY_NOTE_EDIT_SECONDS = 30

COMMAND_NAMING_MAX_TOKENS = 300
CHAT_COMMAND_TIMEOUT = 120
CHAT_COMMAND_OUTPUT_SHOWN_CHARS = 3000
CHAT_COMMAND_OUTPUT_READ_CHARS = 6000

PLAN_CONFIRMING_COMMANDS = tuple(
    command.name for command in COMMANDS if "--confirm" in (option.flag for option in command.options)
)
ONE_WORD_COMMANDS = tuple(command.name for command in COMMANDS if command.chat and command.words == "one")
ANY_WORDS_COMMANDS = tuple(command.name for command in COMMANDS if command.chat and command.words == "any")
COMMAND_NAMES = tuple(command.name for command in COMMANDS if command.name)
BARE_RE = re.compile(
    r"(?<![/\w.-])tinker (?=(?:" + "|".join(sorted(COMMAND_NAMES, key=len, reverse=True)) + r")\b)"
)
USAGE = frozendict(
    {
        command.name: f"✍️ Write `{command.synopsis}`"
        + (f", e.g. `{command.example}`" if command.example else "")
        for command in COMMANDS
        if command.name
    }
)
COMMANDS_ASKING_FOR_WORDS = tuple(
    command.name
    for command in COMMANDS
    if command.chat and any(argument.nargs in ("+", None) for argument in command.arguments)
) + ("command",)
EXIT_WORDS = ("exit",)
CHAT_APP_START = "/start"
START_WELCOME = """👋 **Hi, I'm Tinker!**
I build small programs for you, on this computer, with an AI model that runs right here.

🔨 **Build something**
- Tell me what you want: `task a calculator in Python`
- I reply with a plan: what I will build, and how I will test it
- Like it? Send `confirm`. Want a change? `amend also handle negative numbers`
- I build it, and you see my progress here, in one message

📂 **Your projects**
- `runs` lists them all, newest first
- `run 5 + 3` uses the one built last
- `add @calc-py a history of results` adds a feature
- `fix @calc-py 2/0 crashes` fixes a bug

💬 **Ask me anything**
- Just write to me: what is not a command is a normal chat
- `research the newest Python release`: I study it on the web, thoroughly, and keep what I find (it takes a while)
- `command how full is my disk`: I find the command line, and run it once you `confirm`
- `learn rust`: I study it, and use it in my builds

⏳ **While I work**
- `status` tells what I am doing
- `stop` stops it (it is saved): `jobs` lists what runs and what stopped, `resume #3` goes on
- Just write to me: I pause what runs, answer you, and go on with it

❓ `help` lists everything I can do, and `help task` explains one command."""


MenuEntry = namedtuple("MenuEntry", "name what")
WordsAsked = namedtuple("WordsAsked", "question example")
AppRequest = namedtuple("AppRequest", "project args stdin")
ShellRun = namedtuple("ShellRun", "code output")
Secret = namedtuple("Secret", "key question")
Question = namedtuple("Question", "runs cancels held wanted naming", defaults=(None, None, None, None, False))


def session(root):
    try:
        chats = chat_channels()
    except SystemExit as exc:
        say.warn(f"Chats left out: {exc}")
        chats = []
    Desk([ConsoleChannel()] + chats, root).run()


def one_shot(command, text):
    console, talk = ConsoleChannel(commands_without_tinker=False), Talk(commands_brief())
    message, chat = Message(ConsoleChannel.CHAT, text, "you"), ConsoleChannel.CHAT
    if command == "history":
        console.send(chat, history_text(text.split()[:1]))
        return None
    if not text and command != "run":
        return f"tinker {command} needs words after it, e.g. tinker {command} {'hello' if command == 'chat' else '...'}"
    if command == "chat":
        with paused_for_you():
            console.show(chat, None, talk.reply(console, message, "", CHAT_MAX_REPLY_TOKENS), done=True)
    elif command == "run":
        Desk([console], Path.cwd()).run_app(console, chat, [text])
    else:
        with paused_for_you():
            command_once(console, talk, message)
    return None


def command_once(console, talk, message):
    chat, shell = ConsoleChannel.CHAT, Shell(talk, Path.cwd(), Questions())
    console.show(chat, None, shell.propose(console, message, "", COMMAND_NAMING_MAX_TOKENS), done=True)
    proposed = shell.take(console, chat)
    if not proposed:
        return
    if sys.stdin.isatty() and input("Run it? [y/N] ").strip().lower() in ("y", "yes"):
        shell.run(console, chat, proposed)
    else:
        console.show(chat, None, f"🚫 _Not run:_ `{proposed['line']}`", done=True)


class Desk:
    COMMANDS = tuple(command.name for command in COMMANDS if command.chat)
    TRACKER_LOOK_SECONDS = 1.0

    def __init__(self, channels, root):
        self.channels, self.root = channels, Path(root).resolve()
        self.last_task = self.reporter = self.task = None
        self.live_tasks = []
        self.lock, self.talk = threading.Lock(), Talk(commands_brief(), pausing_jobs=paused_for_you)
        self.questions = Questions()
        self.shell = Shell(self.talk, self.root, self.questions)
        self.waiting_plan = None
        self.messages_by_run = {}
        self.seconds_by_run = {}
        self.consoles = [channel for channel in channels if isinstance(channel, ConsoleChannel)]
        self.said_status = None
        self.chats_started, self.post_lock = set(), threading.Lock()
        self.stop_question = None
        self.exercise_reporter = None
        self.echo_to_terminal = not self.consoles

    def run(self):
        self.claim_chats()
        self.open_banner()
        self.talk.warm()
        self.say_status(anew=True)
        self.adopt_exercise()
        if not self.consoles:
            names = ", ".join(channel.name for channel in self.channels)
            say.info(f"Listening on {names}; projects go in {self.root}. Ctrl-C stops.", "👂")
        try:
            self.listen_until_ended()
        except KeyboardInterrupt:
            self.stop_task()
            self.say_goodbye()
            if not self.consoles:
                say.title("Stopped listening.", "🛑")

    def claim_chats(self):
        for channel in [channel for channel in self.channels if channel not in self.consoles]:
            if not channel.claim({"pid": os.getpid()}):
                held = f"{channel.name} is listened to by another Tinker (process {channel.holder().get('pid', '?')})"
                if not self.consoles:
                    raise SystemExit(f"tinker: {held}: stop it first")
                say.warn(f"{held}: this one talks here only")
                self.channels.remove(channel)

    def open_banner(self):
        for console in self.consoles:
            console.banner(self.banner_lines())

    def banner_lines(self):
        chats = [channel.name for channel in self.channels if channel not in self.consoles]
        details = [f"model {Config.get('model') or 'no model yet'}", f"projects {self.shown_root()}"]
        details += [f"chats {', '.join(chats)}"] if chats else []
        return ["🔧 Tinker", " · ".join(details), "help lists commands · exit quits"]

    def shown_root(self):
        home = Path.home()
        return str("~" / self.root.relative_to(home)) if self.root.is_relative_to(home) else str(self.root)

    def say_goodbye(self):
        leaving = threading.Thread(target=self.take_out_question, daemon=True)
        leaving.start()
        self.say_status("💤 **Tinker** · not listening")
        leaving.join()

    def take_out_question(self):
        if self.stop_question:
            channel, chat, shown = self.stop_question
            for message in shown:
                channel.delete(chat, message)

    def say_status(self, text=None, anew=False):
        text = text or self.status()
        if text == self.said_status and not anew:
            return
        anew = anew or headline(text) != headline(self.said_status)
        self.said_status = text
        for channel in self.channels:
            if channel not in self.consoles:
                with self.post_lock:
                    if channel in self.chats_started:
                        self.post_status(channel, text)
            elif (anew and not self.busy()) or text.startswith("💤"):
                channel.show(ConsoleChannel.CHAT, None, text, done=True)
        self.keep_job_at_bottom()

    def keep_job_at_bottom(self):
        reporter = self.reporter
        if self.busy() and reporter.sent and not reporter.ended:
            with reporter.lock:
                reporter.send_at_bottom = True
                reporter.update()

    def status(self):
        if self.busy():
            return md(t"🔄 **Tinker** · working on {self.reporter.progress.title}")
        if self.waiting_plan:
            then = (
                "`confirm` · `rename NAME`"
                if self.waiting_plan.get("stage") == "name"
                else "`confirm` · `amend WHAT`"
            )
            return md(t"🙋 **Tinker** · {self.waiting_plan['title']} waits for you: {then:md}")
        elsewhere = [described(state) for state in persons_runs(self.children())]
        if elsewhere:
            return md(
                t"🔄 **Tinker** · working in a terminal on {'; '.join(run['title'] for run in elsewhere)}"
            )
        exercise = exercising_now()
        if exercise:
            return md(t"🏋️ **Tinker** · {exercise['title']}")
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
        looked = collected = 0.0
        while any(thread.is_alive() for thread in threads) and not any(
            channel.closed for channel in self.channels
        ):
            time.sleep(ENDED_LOOK_SECONDS)
            if time.monotonic() - collected >= EXPIRED_MESSAGES_SWEEP_SECONDS:
                collected = time.monotonic()
                for channel in self.channels:
                    channel.collect()
            if time.monotonic() - looked >= STATUS_LOOK_SECONDS:
                looked = time.monotonic()
                self.say_status()
                if self.exercise_reporter is None:
                    self.adopt_exercise()
        raise KeyboardInterrupt

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
            self.post_status(channel, self.said_status)

    def hear(self, channel, message):
        if message.text.split()[:1] == [CHAT_APP_START]:
            channel.send(message.chat, welcome())
            return
        message = self.with_words_wanted(channel, message)
        command = command_of(message.text)
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
        if not command:
            self.talk.answer(channel, message, self.running_now, lambda: self.plan_shown(self.waiting_plan))
            return
        reply = self.answer(message, channel)
        if reply:
            sent = channel.send(message.chat, reply)
            if kind_of(command_words(message.text)) == "glance":
                channel.glance(message.chat, message.id, [sent])

    def with_words_wanted(self, channel, message):
        question = self.questions.waiting(channel, message.chat)
        if not question or not question.wanted:
            return message
        wanted = self.questions.answered(channel, message.chat).wanted
        if self.command_of_its_own(message, command_of(message.text), wanted):
            return message
        return Message(message.chat, f"{wanted} {message.text}", message.sender, message.id)

    @staticmethod
    def command_of_its_own(message, command, wanted):
        return command and (
            message.text.startswith("/") or len(message.text.split()) == 1 or command == wanted
        )

    def asked_with_words(self, channel, message, command):
        if len(message.text.split()) == 1:
            channel.send(message.chat, USAGE[command])
        else:
            self.shell.name(channel, after_word(message), self.running_now)

    def settle(self, channel, message, command):
        confirm = command == "confirm" and len(message.text.split()) == 1
        question = self.questions.waiting(channel, message.chat)
        if question and question.naming:
            if confirm:
                channel.send(
                    message.chat, "⏳ The command is still being worked out: `confirm` once it is shown."
                )
            return confirm
        question = self.questions.answered(channel, message.chat)
        if question and confirm:
            threading.Thread(target=question.runs, daemon=True).start()
            return True
        if question:
            question.cancels()
        return False

    def answer(self, message, channel):
        words = command_words(message.text)
        handler = self.handlers(channel, message, words).get(words[0])
        return handler() if handler is not None else self.by_table(channel, message.chat, words)

    def by_table(self, channel, chat, words):
        kind = kind_of(words)
        if kind == "terminal":
            return f"🖥 `{' '.join(words[:2])}` asks in a terminal: `tinker {' '.join(words[:2])}` there"
        if deletes(words) and "--yes" not in words:
            return self.ask_first(channel, chat, words)
        if kind == "job":
            return self.start_task(words, self.root, " ".join(words))
        shown = self.tinker(words)
        return md(t"⚙️ **{' '.join(words)}**\n{shown:block}")

    def handlers(self, channel, message, words):
        command = words[0]
        return {
            "task": lambda: self.new_task(words),
            "confirm": lambda: self.decide(command, words),
            "amend": lambda: self.decide(command, words),
            "rename": lambda: self.decide(command, words),
            "resume": lambda: self.resume(words),
            "stop": lambda: self.stop(words),
            "research": lambda: self.research(words),
            "exercise": lambda: self.exercise(channel, message, words),
            "runs": lambda: self.report(command, words),
            "status": lambda: self.report(command, words),
            "help": lambda: help_text(words[1:2]),
            "history": lambda: history_text(words[1:2]),
            "run": lambda: self.run_app_apart(channel, message),
            "command": lambda: self.asked_with_words(channel, message, command),
            "clear": lambda: self.clear(channel, message.chat, words),
            "read": lambda: self.read(channel, message.chat, words),
            "memory": lambda: self.memory(channel, message.chat, words),
            **{word: lambda: self.ask_to_exit(channel, message.chat, command) for word in EXIT_WORDS},
        }

    def run_app_apart(self, channel, message):
        threading.Thread(
            target=self.run_app, args=(channel, message.chat, message.text.split(None, 1)[1:]), daemon=True
        ).start()

    def clear(self, channel, chat, words):
        if words[1:] == ["chat"]:
            return self.clear_chat(channel, chat, "Chat cleared", " · `history` shows any of them again")
        return self.by_table(channel, chat, words)

    def read(self, channel, chat, words):
        if words[1:2] in (["list"], ["show"]):
            return md(t"📚 **{' '.join(words)}**\n{self.tinker(words):block}")
        return self.by_table(channel, chat, words)

    def memory(self, channel, chat, words):
        if "--graph" in words:
            return self.memory_graph(channel, chat, words)
        return self.by_table(channel, chat, words)

    def research(self, words):
        return self.start_task(words, self.root, " ".join(words)) if len(words) > 1 else USAGE["research"]

    def exercise(self, channel, message, words):
        if len(words) < 2:
            return USAGE["exercise"]
        if words[1].lower() != "status":
            return self.start_exercise(words)

        def answer():
            sent = channel.send(message.chat, md(t"🏋️ **{' '.join(words)}**\n{self.tinker(words):block}"))
            channel.glance(message.chat, message.id, [sent])

        threading.Thread(target=answer, daemon=True).start()
        return None

    def stop_exercise(self):
        running_one = exercising_now()
        if not running_one:
            return "🤷 No exercise runs: `resume #N` goes on with one (`jobs stopped` lists them)"
        signal_task(running_one["pid"], JOB_STOP_SIGNAL)
        self.exercise_says("🛑 Stopping: its build saves itself first")
        return None

    def start_exercise(self, words, title=None):
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
        self.follow_exercise(process.pid, title or " ".join(words), process.wait, (log, start), anew=True)
        return None

    def adopt_exercise(self):
        exercise = exercising_now()
        lost = next((job for job in Jobs.lost() if job["command"] == "exercise"), None)
        if exercise and not exercise["reports"]:
            self.follow_exercise(
                exercise["pid"], exercise["title"], lambda: until_ended(exercise["pid"]), anew=True
            )
        elif not exercise and lost:
            self.start_exercise(["resume", f"#{lost['number']}"], lost["title"])

    def follow_exercise(self, pid, title, ended, log=None, anew=False):
        sent = exercise_board()
        if anew:
            self.take_out(sent)
        if self.exercise_reporter is not None:
            self.exercise_reporter.ended = True
        reporter = self.exercise_reporter = Reporter(
            self.channels,
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

    def memory_graph(self, channel, chat, words):
        page = Home.graph_page()
        before = page.stat().st_mtime_ns if page.exists() else None
        shown = self.tinker(words)
        written_now = page.exists() and page.stat().st_mtime_ns != before
        if not written_now:
            return md(t"🧠 **{' '.join(words)}**\n{shown:block}")
        channel.send_file(chat, page, md(t"🧠 **Tinker's memory**\n{shown}"))
        return None

    def new_task(self, words):
        if len(words) < 2:
            return USAGE["task"]
        request = " ".join(words[1:])
        return self.start_task(["task", request], self.root, request)

    def decide(self, command, words):
        waiting = self.waiting_plan or {}
        if command == "rename":
            return self.rename(waiting, words)
        name, rest = refs(words[1:])
        if command == "amend" and not rest:
            return USAGE["amend"]
        if self.confirms_a_new_project(command, name, waiting):
            return self.ask_folder_name(waiting)
        target = name or waiting.get("run")
        if not target:
            return "🤷 No plan waits for confirmation: start one with `task WHAT TO BUILD`"
        mine = bool(waiting) and target in (waiting.get("run"), Path(str(waiting.get("folder", ""))).name)
        folder, title = (waiting["folder"], waiting["title"]) if mine else (self.root, f"@{target}")
        target = waiting["run"] if mine else target
        if command == "confirm":
            started = self.start_task(
                ["resume", f"@{target}"],
                folder,
                title,
                "✅ **Confirmed:** building it",
                keep=waiting["plan"] if mine else "",
            )
        else:
            change = " ".join(rest)
            started = self.start_task(
                ["amend", f"@{target}", change], folder, title, md(t"✏️ **Changing the plan:** {change}")
            )
        return self.on_task(waiting if mine else {}, started)

    @staticmethod
    def confirms_a_new_project(command, name, waiting):
        return command == "confirm" and not name and waiting.get("stage") == "spec" and waiting.get("new")

    def ask_folder_name(self, waiting):
        waiting["stage"] = "name"
        self.on_board(waiting, self.asked(waiting))
        return None

    def on_task(self, waiting, said):
        if said and waiting:
            self.on_board(waiting, f"{self.asked(waiting)}\n\n{said}")
            return None
        return said

    @staticmethod
    def asked(waiting):
        if waiting.get("stage") != "name":
            return Desk.plan_shown(waiting)
        return (
            f"{Desk.plan_shown(waiting)}\n\n📁 **Its folder:** `{Path(waiting['folder']).name}`\n👉 `confirm` to build it "
            f"there · `rename NAME` to name it otherwise (it builds then)"
        )

    @staticmethod
    def plan_shown(waiting):
        return Progress.waits(waiting["plan"], waiting["so_far"]) if waiting else ""

    def on_board(self, waiting, text):
        board = self.messages_by_run.setdefault(waiting["run"], {})
        for channel in self.channels:
            for chat in channel.allowed:
                board[(channel.name, chat)] = channel.show(
                    chat, board.get((channel.name, chat)), text, done=True
                )
        self.say_status()

    def rename(self, waiting, words):
        if len(words) != 2:
            return USAGE["rename"]
        if not waiting:
            return "🤷 No plan waits for confirmation, so no folder can be renamed (once built, it stays)."
        if self.busy():
            return self.on_task(waiting, "⏳ A task is running: wait for it, or `stop` it first.")
        why = self.rename_folder(waiting, words[1])
        if why is not None:
            return self.on_task(waiting, md(t"⚠️ **Not renamed:** {why}"))
        self.renamed(waiting, words[1])
        if waiting.get("stage") != "name":
            self.on_board(
                waiting, f"{self.plan_shown(waiting)}\n\n📁 **Renamed:** its folder is `{words[1]}`"
            )
            return None
        return self.on_task(
            waiting,
            self.start_task(
                ["resume", f"@{waiting['run']}"],
                waiting["folder"],
                waiting["title"],
                f"✅ **Renamed** to `{words[1]}`: building it",
                keep=waiting["plan"],
            ),
        )

    def rename_folder(self, waiting, name):
        done = subprocess.run(
            tinker_command() + ["rename", f"@{waiting['run']}", name],
            cwd=self.root,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, NO_COLOR="1"),
            timeout=60,
        )
        if done.returncode:
            return first_lines((done.stdout + done.stderr).strip(), 10).splitlines()[-1]
        return None

    @staticmethod
    def renamed(waiting, name):
        old, waiting["folder"] = waiting["folder"], Path(waiting["folder"]).parent / name
        waiting["title"] = f"{name}: {waiting['title'].split(': ', 1)[-1]}"
        waiting["plan"] = waiting["plan"].replace(f"📁 `{Path(old).name}`", f"📁 `{name}`")

    def resume(self, words):
        name, rest = refs(words[1:])
        if name and not rest:
            return self.start_task(["resume", f"@{name}"], self.root, f"resume @{name}")
        number = job_number(words[1]) if len(words) == 2 else None
        if len(words) > 2 or (len(words) == 2 and number is None):
            return USAGE["resume"]
        if Jobs.wake(number):
            return md(t"▶️ **Job #{number}** goes on now.")
        job, why = Jobs.resumable(number)
        if job is None:
            return md(t"🤷 {why.replace('tinker ', '')}")
        again = ["resume", f"#{job['number']}"]
        if job["command"] == "exercise":
            return self.start_exercise(again, job["title"])
        return self.start_task(again, self.root, job["title"])

    def stop(self, words):
        if len(words) > 1:
            number = job_number(words[1])
            return self.stop_job(number) if number is not None else USAGE["stop"]
        said = [self.stop_job(job["number"]) for job in Jobs.listed("running")]
        return "\n".join(line for line in said if line) or (None if said else "💤 No job is running.")

    def stop_job(self, number):
        job = Jobs.get(number)
        if job is None or job["state"] not in ("running", "paused"):
            return f"🤷 Job #{number} is not running or paused: `jobs` lists them."
        if job["state"] == "paused":
            Jobs.stop(job)
        elif self.busy() and job["pid"] == self.last_task.pid:
            self.stop_task()
        elif job["command"] == "exercise":
            return self.stop_exercise()
        else:
            Jobs.stop(job)
        return md(t"⏹ **Stopping #{number}** {job['title']}: it is saved; `resume #{number}` goes on later.")

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
            "👉 `confirm` to stop it · anything else keeps it",
            done=True,
        )
        self.wait_for_confirm(channel, chat, {"words": [command], "shown": shown})
        return None

    def ask_first(self, channel, chat, words):
        preview = self.printed(words)
        shown = channel.show(
            chat,
            None,
            md(
                t"🗑 **{' '.join(words)}**\n{preview:block}\n"
                t"👉 `confirm` to delete it · anything else cancels it"
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
            for each in self.channels:
                each.closed = True
            return
        words = asking["words"] + ["--yes"]
        if kind_of(words) == "job":
            refused = self.start_task(words, self.root, " ".join(asking["words"]))
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

    def clear_chat(self, channel, chat, cleared, after=""):
        taken = sum(each.clear() for each in self.channels)
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
                md(t"🤷 **No project @{name}:** `runs` lists them")
                if name
                else "🤷 **No project is built yet:** `task WHAT TO BUILD` builds one",
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

    def start_task(self, arguments, folder, title, phase=Tracker.STARTING, sent=None, keep=""):
        run = self.run_named(arguments)
        sent = sent if sent is not None else self.messages_by_run.get(run) if run else None
        with self.lock:
            self.task = {"arguments": arguments, "folder": folder, "title": title, "run": run}
            self.reporter = Reporter(
                self.channels,
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
            self.live_tasks = [process for process in self.live_tasks if process.poll() is None] + [
                self.last_task
            ]
            self.say_status()
            with self.reporter.lock:
                self.reporter.update()
        its_plan_moves_on = self.waiting_plan and self.waiting_plan["folder"] == folder
        if its_plan_moves_on:
            self.waiting_plan = None
        threading.Thread(
            target=self.follow, args=(self.last_task, self.reporter, self.task), daemon=True
        ).start()
        return None

    def run_named(self, arguments):
        name = refs(arguments[1:2])[0] if arguments[0] in ("resume", "amend") else ""
        waiting = self.waiting_plan or {}
        return (
            waiting.get("run") if name and name == Path(str(waiting.get("folder", ""))).name else name or None
        )

    def spawn(self, arguments):
        arguments = arguments + ["--confirm"] if arguments[0] in PLAN_CONFIRMING_COMMANDS else arguments
        env = dict(os.environ, **{CHANNEL_REPORTS_ENV: "off", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
        return subprocess.Popen(
            tinker_command() + arguments,
            cwd=self.root,
            env=env,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
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
            self.waiting_plan = {
                "run": task["run"],
                "folder": task["folder"],
                "title": task["title"],
                "plan": reporter.progress.planned(plan),
                "so_far": reporter.progress.time_taken(),
                "stage": "spec",
                "new": plan.get("kind") == "project",
            }
        self.say_status()

    def stop_task(self):
        if not self.busy():
            return False
        os.killpg(self.last_task.pid, signal.SIGINT)
        return True

    def busy(self):
        return self.last_task is not None and self.last_task.poll() is None

    def running_now(self):
        progress = self.reporter.progress if self.busy() else None
        here = (
            [{"title": progress.title, "brief": progress.brief(), "text": progress.text()}]
            if progress
            else []
        )
        return here + [described(state) for state in persons_runs(self.children())]

    def children(self):
        return {process.pid for process in self.live_tasks}

    def tinker(self, arguments, timeout=120):
        done = self.run_tinker(arguments, timeout)
        return shown_output(done.stdout + done.stderr)

    def printed(self, arguments):
        return shown_output(self.run_tinker(arguments, 120).stdout)

    def run_tinker(self, arguments, timeout):
        return subprocess.run(
            tinker_command() + arguments,
            cwd=self.root,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, NO_COLOR="1"),
            timeout=timeout,
        )


def shown_output(output):
    return first_lines(Markup.columns(output.strip()) or "(nothing)", 40)


def command_of(text, commands=None):
    words = text.split()
    first = command_word(words[0]) if words else ""
    if first not in (commands or Desk.COMMANDS):
        return ""
    if takes_these_words(first, words):
        return first
    return ""


def takes_these_words(first, words):
    return len(words) == 1 or first in ANY_WORDS_COMMANDS or (first in ONE_WORD_COMMANDS and len(words) == 2)


def command_words(text):
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    words[0] = command_word(words[0])
    return words


def command_word(word):
    return word.lower().lstrip("/").partition("@")[0].replace("_", "-")


def offered():
    return [MenuEntry(command.name, command.what) for command in COMMANDS if command.chat]


def words_asked(command):
    found = BY_NAME[command]
    needed = found.synopsis.split(None, 1)[1]
    example = found.example.split(None, 1)[1] if " " in found.example else ""
    question = md(t"✍️ **{command}** · {found.what}\nSend {needed:code} now")
    return WordsAsked(question + (md(t", e.g. {example:code}") if example else ""), example or needed)


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


class Talk:
    def __init__(self, commands, pausing_jobs=contextlib.nullcontext):
        self.commands, self.said, self.lock, self.turn = commands, {}, threading.Lock(), threading.Lock()
        self.pausing_jobs = pausing_jobs

    def answer(self, channel, message, now, waiting=lambda: "", write=None, on_shown=None):
        threading.Thread(
            target=self.answering,
            args=(channel, message, now, waiting, write or self.reply, on_shown),
            daemon=True,
        ).start()

    def answering(self, channel, message, now, waiting, write_reply, on_shown):
        running, written, reply = now(), threading.Event(), []
        asked = (channel, message, self.context(running, waiting), CHAT_MAX_REPLY_TOKENS)
        threading.Thread(target=self.write, args=(write_reply, asked, reply, written), daemon=True).start()
        note = self.busy_until(written, running, now, channel, message.chat)
        note = channel.show(
            message.chat, note, reply[0] if reply else "⚠️ **The model did not answer.**", done=True
        )
        if on_shown:
            on_shown(note)

    @staticmethod
    def context(running, waiting):
        context = ["Running now:\n" + "\n\n".join(run["text"] for run in running)] if running else []
        context += [
            f"A spec waits for the person (confirm builds it, amend WHAT changes it):\n{waiting()}"
            if waiting()
            else ""
        ]
        return "\n\n".join(piece for piece in context if piece)

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

    def reply(self, channel, message, now="", tokens=CHAT_MAX_REPLY_TOKENS):
        if not Models.quick():
            return (
                "⚠️ **No model is in use yet**, so I cannot chat: `tinker calibrate NAME`, then "
                f"`tinker use-model NAME`. These still work:\n{self.commands}"
            )
        said = self.chat_of(channel, message.chat)
        asked = self.remembered(said, "user", message.text)
        system = Prompts.CHAT_SYSTEM.format(kind=channel.KIND.title(), commands=self.commands)
        system += f"\n\n{now}" if now else "\n\nNo task is running now."
        try:
            text = self.ask(channel, message.chat, [{"role": "system", "content": system}] + asked, tokens)
        except OllamaError as exc:
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")
        text = text or "_(no answer)_"
        self.remembered(said, "assistant", text)
        return text

    def chat_of(self, channel, chat):
        with self.lock:
            return self.said.setdefault((channel.name, chat), [])

    def remembered(self, said, role, text):
        with self.lock:
            said.append({"role": role, "content": text})
            del said[:-CHAT_CONTEXT_MESSAGES]
            return list(said)

    def ask(self, channel, chat, messages, tokens):
        done = threading.Event()
        threading.Thread(target=keep_typing, args=(channel, chat, done), daemon=True).start()
        try:
            return Models.quick_reply(messages, tokens)
        finally:
            done.set()

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


class Shell:
    CHANGES_RE = re.compile(
        r"\b(rm|rmdir|mv|dd|mkfs\w*|chmod|chown|kill|killall|pkill|sudo|shutdown|reboot|"
        r"truncate)\b|(?<![\d&>])>(?!&)|\bgit\s+(push|reset|clean|checkout|rebase)\b|"
        r"\b(brew|pip3?|npm|cargo)\s+(install|uninstall|remove)\b|\s-delete\b|-exec\s+(rm|mv)\b|"
        r"\bxargs\s+(rm|mv)\b|\bsed\s+-i\b"
    )
    LOGIN_SHELL = os.environ.get("SHELL") or "/bin/sh"

    def __init__(self, talk, folder, questions):
        self.talk, self.folder, self.questions = talk, folder, questions
        self.shell = self.LOGIN_SHELL

    def name(self, channel, message, now):
        self.questions.ask(channel, message.chat, Question(naming=True))

        def shown(ids):
            question = self.questions.waiting(channel, message.chat)
            if question and question.held:
                question.held["shown"] = ids

        self.talk.answer(channel, message, now, write=self.propose, on_shown=shown)

    def propose(self, channel, message, context, tokens):
        if not Models.quick():
            self.questions.answered(channel, message.chat)
            return "⚠️ **No model is in use yet:** `tinker calibrate NAME`, then `tinker use-model NAME`."
        system = Prompts.COMMAND_SYSTEM.format(
            system=os.uname().sysname, shell=Path(self.shell).name, folder=self.folder
        )
        try:
            data = json_answer(
                self.talk.ask(
                    channel,
                    message.chat,
                    [{"role": "system", "content": system}, {"role": "user", "content": message.text}],
                    COMMAND_NAMING_MAX_TOKENS,
                )
            )
        except OllamaError as exc:
            self.questions.answered(channel, message.chat)
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")
        line, does = field(data, "command"), field(data, "does")
        if not line or "\n" in line:
            self.questions.answered(channel, message.chat)
            return md(t"🤷 **No command found:** {does or 'describe it another way'}")
        self.wait_for_confirm(channel, message.chat, {"line": line, "asked": message.text, "shown": None})
        return self.proposal(line, does)

    def wait_for_confirm(self, channel, chat, proposed):
        self.questions.ask(
            channel,
            chat,
            Question(
                runs=lambda: self.run(channel, chat, proposed),
                cancels=lambda: self.drop(channel, chat, proposed),
                held=proposed,
            ),
        )

    def proposal(self, line, does):
        warning = (
            "\n⚠️ **It changes or deletes things:** read it before you confirm."
            if self.CHANGES_RE.search(line)
            else ""
        )
        return md(
            t"🖥 **Run this?**\n{line:block}\n{does}{warning:md}\n"
            t"👉 `confirm` to run it · anything else cancels it"
        )

    def take(self, channel, chat):
        question = self.questions.waiting(channel, chat)
        if not question or not question.held:
            return None
        return self.questions.answered(channel, chat).held

    def drop(self, channel, chat, proposed):
        channel.show(chat, proposed["shown"], f"🚫 _Not run:_ `{proposed['line']}`", done=True)

    def run(self, channel, chat, proposed):
        line = proposed["line"]
        sent = channel.show(chat, proposed["shown"], f"▶️ **Running** `{line}` …")
        code, output = in_shell(self.shell, line, self.folder)
        head = md(t"{'✅' if code == 0 else '❌'} {line:code} · _exit {code}_")
        folded = self.folded(output)
        tells = bool(output) and bool(Models.quick())
        sent = channel.show(chat, sent, f"{head}\n{folded}", done=not tells)
        if not tells:
            return
        with self.talk.pausing_jobs():
            told = self.told(channel, chat, proposed["asked"], line, code, output)
        channel.show(chat, sent, f"{head}\n{told}\n{folded}" if told else f"{head}\n{folded}", done=True)

    @staticmethod
    def folded(output):
        if not output:
            return "_(no output)_"
        shown = (
            output
            if len(output) <= CHAT_COMMAND_OUTPUT_SHOWN_CHARS
            else "… (the start is left out)\n" + output[-CHAT_COMMAND_OUTPUT_SHOWN_CHARS:]
        )
        return f"```output\n{Markup.fenced(Markup.columns(shown))}\n```"

    def told(self, channel, chat, asked, line, code, output):
        try:
            return self.talk.ask(
                channel,
                chat,
                [
                    {"role": "system", "content": Prompts.COMMAND_TELL_SYSTEM},
                    {
                        "role": "user",
                        "content": f"They asked: {asked}\nCommand: {line}\n"
                        f"Exit code: {code}\nOutput:\n{output[-CHAT_COMMAND_OUTPUT_READ_CHARS:]}",
                    },
                ],
                CHAT_MAX_REPLY_TOKENS,
            )
        except OllamaError:
            return ""


def in_shell(shell, line, folder, given=None):
    try:
        done = subprocess.run(
            [shell, "-lc", line],
            cwd=folder,
            text=True,
            capture_output=True,
            input=given,
            stdin=None if given else subprocess.DEVNULL,
            timeout=CHAT_COMMAND_TIMEOUT,
        )
        return ShellRun(done.returncode, (done.stdout + done.stderr).strip())
    except subprocess.TimeoutExpired as exc:
        return ShellRun(
            f"stopped after {CHAT_COMMAND_TIMEOUT}s", (text_of(exc.stdout) + text_of(exc.stderr)).strip()
        )
    except OSError as exc:
        return ShellRun("not started", str(exc))


class Reporter:
    def __init__(
        self,
        channels,
        title,
        pid=None,
        sent=None,
        phase=Tracker.STARTING,
        plan_on_top="",
        before=0.0,
        on_step=None,
    ):
        self.channels, self.progress, self.pid = channels, Progress(title, phase, before), pid
        self.on_step = on_step
        self.plan_on_top = plan_on_top
        self.sent, self.last_edit, self.lock = ({} if sent is None else sent), 0.0, threading.Lock()
        self.ended = False
        self.send_at_bottom = True
        threading.Thread(target=self.keep_up, daemon=True).start()

    def keep_up(self):
        while not self.ended:
            time.sleep(PROGRESS_EDIT_SECONDS)
            self.refresh()

    def refresh(self):
        with self.lock:
            if self.ended:
                return
            state = self.tracked()
            went_on = ((state or {}).get("resumed") or 0) > (self.progress.snapshot.get("resumed") or 0)
            self.send_at_bottom = self.send_at_bottom or went_on
            changed = self.progress.take(state)
            if changed or went_on or time.monotonic() - self.last_edit >= CLOCK_ONLY_EDIT_SECONDS:
                self.update()
            else:
                self.count_live()

    def tracked(self):
        job = Jobs.of_process(self.pid) if self.pid else None
        return (job or {}).get("progress")

    def update(self):
        self.last_edit = time.monotonic()
        self.progress.turn()
        self.say(self.progress.text())
        if self.on_step:
            self.on_step()

    def count_live(self):
        for channel in self.channels:
            for chat in channel.allowed:
                channel.count_live(chat, self.sent.get((channel.name, chat)), **self.progress.facts)

    def say(self, text, done=False):
        text = f"{self.plan_on_top}\n\n{text}" if self.plan_on_top else text
        bottom, self.send_at_bottom = done or self.send_at_bottom, False
        for channel in self.channels:
            for chat in channel.allowed:
                key = (channel.name, chat)
                self.sent[key] = channel.show(
                    chat,
                    self.sent.get(key),
                    text,
                    done,
                    bottom,
                    began=self.progress.work_began,
                    doing=self.progress.working_on,
                    **self.progress.facts,
                )

    def printed(self, line):
        if self.tracked() is None and self.says_something(line):
            with self.lock:
                self.progress.snapshot["latest"] = line.strip()[:300]

    @staticmethod
    def says_something(line):
        return line.strip() and not line.lstrip().startswith("...")

    def finish(self, snapshot=None):
        with self.lock:
            self.ended = True
            self.progress.take(snapshot or self.tracked())
            if self.progress.waiting() or self.progress.snapshot.get("ended"):
                self.say(self.progress.ending(), done=True)
            else:
                self.update()


class Progress:
    OWN_PHASE_ICONS = ("✅", "✏️", "📁")
    CHANGE_SIGNS = {"+": "➕", "~": "🔄"}

    def __init__(self, title, phase=Tracker.STARTING, before=0.0):
        self.snapshot = {
            "title": title,
            "doing": phase,
            "step": None,
            "began": None,
            "steps": [],
            "passing": None,
            "working": "",
            "tally": "",
            "found": "",
            "latest": "",
            "waiting": None,
            "ended": None,
            "before": before,
            "seconds": 0.0,
        }
        self.turns = 0

    def turn(self):
        self.turns += 1

    def take(self, snapshot):
        before = self.text(counted=False)
        said = self.snapshot["doing"] if self.snapshot.get("step") is None else None
        self.snapshot = dict(
            self.snapshot, **{key: value for key, value in (snapshot or {}).items() if key != "before"}
        )
        if said and self.snapshot.get("step") is None:
            self.snapshot["doing"] = said
        return self.text(counted=False) != before

    def text(self, counted=True):
        state = self.snapshot
        doing = (
            self.working_on
            if self.working_on.startswith(self.OWN_PHASE_ICONS)
            else md(t"**{self.working_on}**")
        )
        job = f" (Job #{state['job']})" if state.get("job") else ""
        lines = [md(t"🔨 **{state['title']}**{job}")] + [f"✅ {step}" for step in state.get("steps", [])]
        lines += [f"{star_at(self.turns)} {self.tinkering_line(doing, counted)}"]
        lines += [md(t"📊 {self.how_far}")] if self.how_far else []
        if state.get("found"):
            lines += [md(t"💡 {state['found']}")]
        elif state.get("latest"):
            lines += [md(t"… {state['latest']}")]
        return "\n".join(lines)

    def tinkering_line(self, doing, counted):
        if not counted:
            return tinkering(None, doing, **dict(self.facts, tokens=0))
        began = self.work_began
        return tinkering(time.time() - began if began else None, doing, **self.facts)

    @property
    def facts(self):
        state = self.snapshot
        return {
            "tokens": state.get("tokens", 0),
            "thinking": state.get("thinking", False),
            "thought_seconds": state.get("thought_seconds"),
        }

    @property
    def title(self):
        return self.snapshot["title"]

    @property
    def work_began(self):
        started = self.snapshot.get("started")
        return started - self.snapshot.get("before", 0.0) if started else None

    @property
    def working_on(self):
        state = self.snapshot
        if not state.get("working"):
            return state["doing"]
        return f"{Tracker.icon(state.get('step'))} {state['working']}".strip()

    @property
    def passing(self):
        counts = self.snapshot.get("passing")
        return f"{counts[0]}/{counts[1]} spec points pass" if counts else ""

    @property
    def how_far(self):
        return " · ".join(part for part in (self.passing, self.snapshot.get("tally")) if part)

    def brief(self):
        got = f", {self.how_far}" if self.how_far else ""
        return md(t"**{self.title}** ({Markup.plain(self.working_on):md}{got})")

    def waiting(self):
        return bool(self.snapshot.get("waiting"))

    def ending(self):
        if self.waiting():
            return self.waits(self.planned(self.snapshot["waiting"]), self.time_taken())
        ended = self.snapshot.get("ended")
        if not ended:
            return md(t"🏁 **{self.title}**")
        return "\n".join([md(t"🏁 **{self.title}**")] + self.ended_lines(ended))

    def ended_lines(self, ended):
        mark = {"done": "✅", "blocked": "⚠️"}.get(ended.get("status"), "⏸")
        took = (
            f"⏱ **Done in {self.time_taken()}**"
            if ended.get("status") == "done"
            else f"⏱ _so far: {self.time_taken()}_"
        )
        lines = ([md(t"{mark} **{ended['headline']}**")] if ended.get("headline") else []) + [took]
        lines += [self.detail(label, value) for label, value in ended.get("details", [])]
        lines += (
            [" · ".join(f"{label}: `{command}`" for label, command in ended.get("next", []))]
            if ended.get("next")
            else []
        )
        return lines + ([ended["text"]] if ended.get("text") else [])

    @staticmethod
    def detail(label, value):
        if label == "Run it":
            return md(t"▶️ {label}: {value:code}")
        return md(t"• {label}: {value}")

    def time_taken(self):
        seconds = self.snapshot.get("before", 0.0) + self.snapshot.get("seconds", 0.0)
        return f"{human_duration(seconds)} ({round(seconds)} seconds)"

    @staticmethod
    def waits(planned, so_far):
        return f"{Progress.with_next_step(planned)}\n⏱ _so far: {so_far}_"

    @staticmethod
    def plan(plan):
        return Progress.with_next_step(Progress.planned(plan))

    @staticmethod
    def with_next_step(planned):
        return f"{planned}\n\n👉 `confirm` to build it · `amend WHAT` to change it"

    @staticmethod
    def planned(plan):
        heading = "🐞 **Bug shown for" if plan.get("kind") == "fix" else "📋 **Plan for"
        start = f" · ▶️ `{plan['start']}`" if plan.get("start") else ""
        lines = [
            md(t"{heading:md} {plan.get('name', '')}** · run {plan.get('run', ''):code}"),
            md(t"🧰 {plan.get('language') or '?'}{start:md}"),
        ]
        if plan.get("changes"):
            lines += ["", "✏️ **What changed**"] + [Progress.change_line(change) for change in plan["changes"]]
        lines += ["", "🧪 **Tested before any code is written**"]
        lines += [md(t"**{criterion_id}** {text}") for criterion_id, text in plan.get("criteria", [])]
        if plan.get("non_goals"):
            lines += ["", md(t"🚫 _Not included:_ {'; '.join(plan['non_goals'])}")]
        if plan.get("folder"):
            lines += [f"📁 `{Path(plan['folder']).name}`"]
        return "\n".join(lines)

    @staticmethod
    def change_line(change):
        if change.startswith("- "):
            return md(t"➖ ~~{change[2:]}~~")
        return md(t"{Progress.CHANGE_SIGNS.get(change[:1], '•')} {change[2:]}")


class Attached:
    def __init__(self, channels, title, tracker):
        self.tracker = tracker
        self.reporter = Reporter(channels, title, pid=os.getpid())

    def start(self):
        signal.signal(JOB_STOP_SIGNAL, self.stop)
        self.tracker.change("attached", reports=True)
        with self.reporter.lock:
            self.reporter.progress.take(self.tracker.state)
            self.reporter.update()
        return self

    def stop(self, signum, frame):
        raise KeyboardInterrupt

    def end(self):
        self.reporter.finish(self.tracker.state)


def attach(title, tracker):
    try:
        channels = chat_channels()
    except SystemExit as exc:
        say.warn(f"Channels left out: {exc}")
        return None
    return Attached(channels, title, tracker).start() if channels else None


def chat_channels():
    try:
        from .channels import Channels
    except ImportError:
        return []
    return Channels.enabled()


def persons_runs(skip=()):
    return [state for state in running(skip) if not state.get("exercise")]


def exercising_now():
    jobs = [job for job in Jobs.listed("running") if job["command"] == "exercise" and job.get("progress")]
    return described(tracker_of(jobs[0])) if jobs else None


def running(skip=()):
    return [
        tracker_of(job)
        for state in ("running", "paused")
        for job in Jobs.listed(state)
        if job.get("progress") and job["pid"] != os.getpid() and job["pid"] not in skip
    ]


def tracker_of(job):
    return dict(job["progress"], pid=job["pid"])


def exercise_switch(text):
    words = text.split()
    return len(words) > 1 and words[0].lstrip("/").lower() == "exercise" and words[1].lower() != "status"


def headline(status):
    return re.split(r":| \(", (status or "").split("\n")[0])[0]


def exercise_board():
    kept = Home.store().get("activity", "exercise message") or {}
    return {tuple(key.split("\t", 1)): ids for key, ids in kept.items()}


def keep_exercise_board(sent):
    Home.store().put(
        "activity", "exercise message", {f"{name}\t{chat}": ids for (name, chat), ids in sent.items()}
    )


def printed_since(path, start):
    with open(path, encoding="utf-8", errors="replace") as file:
        file.seek(start)
        return "\n".join(file.read().strip().splitlines()[-10:])


def until_ended(pid):
    while alive(pid):
        time.sleep(PROGRESS_EDIT_SECONDS)


def described(state):
    progress = Progress(state.get("title") or "a task")
    progress.take(state)
    return {
        "title": progress.title,
        "brief": progress.brief(),
        "text": progress.text(),
        "pid": state.get("pid"),
        "reports": bool(state.get("reports")),
    }


def signal_task(pid, signum):
    try:
        os.kill(pid, signum)
    except ProcessLookupError, PermissionError:
        pass


class Channel:
    KIND = ""
    SECRETS = ()

    def __init__(self, name, settings, secrets):
        self.name, self.settings, self.secrets = name, settings, secrets
        self.allowed = {str(chat) for chat in settings.get("allowed", [])}
        self.closed = False
        self.commands_without_tinker = True

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

    def typing(self, chat):
        pass

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

    def count_live(self, chat, shown, **facts):
        pass

    def said(self, text):
        return BARE_RE.sub("", text) if self.commands_without_tinker else text

    def skip_waiting(self):
        pass

    def offer(self, commands):
        pass

    def ask_for(self, chat, question, hint):
        return self.send(chat, question)

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


class ConsoleChannel(Channel):
    KIND = "console"
    CHAT = "console"

    def __init__(self, commands_without_tinker=True):
        super().__init__("console", {"allowed": [self.CHAT]}, {})
        self.commands_without_tinker = commands_without_tinker
        self.screen, self.lines = ConsoleScreen(), queue.Queue()
        self.last_id, self.numbering = 0, threading.Lock()
        self.printed, self.began, self.doings, self.live_owner = {}, {}, {}, None

    def handle(self):
        return "this terminal"

    def receive(self, timeout):
        if not self.screen.reading:
            self.start_reading()
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return []
        if line is None or line.strip().lower() in EXIT_WORDS:
            self.closed = True
            return []
        return [Message(self.CHAT, line, "you")] if line.strip() else []

    def start_reading(self):
        say.write_through(self.screen.print, self.screen if typed_here() else None)
        self.screen.start()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        while True:
            self.draw_rule()
            try:
                line = input(self.screen.prompt())
            except EOFError, KeyboardInterrupt:
                self.lines.put(None)
                return
            self.show_again(line)
            self.lines.put(line)

    def draw_rule(self):
        if typed_here():
            self.screen.draw(self.screen.rule() + "\n")

    def show_again(self, line):
        if not typed_here():
            return
        typed, width = f"› {line}", shutil.get_terminal_size().columns
        rows = max(1, -(-len(typed) // width))
        quiet = not line.strip() or line.strip().lower() in EXIT_WORDS
        shown = "" if quiet else styled(typed.ljust(width), "grey background") + "\n"
        self.screen.draw(f"{lines_up(rows + 1)}{CLEAR_DOWN}{shown}")

    def banner(self, lines):
        if not typed_here():
            return
        width = min(shutil.get_terminal_size().columns, BANNER_MAX_COLUMNS)
        edge = "─" * (width - 2)
        rows = [self.banner_row(line, "dim" if row else "bold", width - 4) for row, line in enumerate(lines)]
        framed = [styled(f"╭{edge}╮", "accent"), *rows, styled(f"╰{edge}╯", "accent")]
        self.screen.draw("\n".join(framed) + "\n")

    @staticmethod
    def banner_row(text, style, room):
        shown = cut_to_width(text, room)
        padding = " " * (room - visible_width(shown))
        return styled("│ ", "accent") + styled(shown, style) + padding + styled(" │", "accent")

    def send(self, chat, text):
        self.screen.print(marked(Markup.ansi(self.said(text))))
        return self.new_id()

    def edit(self, chat, sent, text):
        self.screen.print(marked(Markup.ansi(self.said(text))))
        return sent

    def send_file(self, chat, path, caption):
        webbrowser.open(Path(path).as_uri())
        return self.send(chat, f"{caption}\n📄 Opened in your browser: `{path}`")

    def show(self, chat, shown, text, done=False, bottom=None, status=False, began=None, doing=None, **facts):
        sent, text = (shown or [None])[0] or self.new_id(), self.said(text)
        if began is not None:
            self.began[sent] = time.monotonic() - (time.time() - began)
        if done:
            self.show_done(sent, text)
        else:
            self.show_going(sent, text, doing, facts)
        return [sent]

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


class ConsoleScreen:
    PROMPT = "› " if sys.stdin.isatty() else ""
    TICK_SECONDS = 0.25

    def __init__(self):
        self.reading, self.live, self.under, self.frame, self.printing = False, None, [], 0, threading.Lock()

    def start(self):
        self.reading = True
        if typed_here():
            threading.Thread(target=self.keep_ticking, daemon=True).start()

    def print(self, text):
        with self.printing:
            write_out(self.printed_in_pieces(text) if self.ruled() else self.printed_over_prompt(text))

    def printed_in_pieces(self, text):
        room = shutil.get_terminal_size().lines - self.live_rows() - 2
        return "".join(
            self.printed_above("\n".join(lines), rows) for lines, rows in screen_pieces(text, room)
        )

    def printed_above(self, text, rows):
        return f"{self.room_made(rows, above=self.live_rows())}\r{text}{RESTORE_CURSOR}"

    def printed_over_prompt(self, text):
        if not sys.stdout.isatty():
            return text + "\n"
        return f"{CLEAR_LINE}{text}\n{self.PROMPT if self.reading else ''}"

    def draw(self, text):
        with self.printing:
            write_out(text)

    def show_live(self, text, began, under=(), **facts):
        if not self.ruled():
            return
        with self.printing:
            rows_before = self.live_rows()
            self.live, self.under = (text, began, facts), list(under)
            write_out(self.resized(rows_before, self.live_rows()) + self.redrawn_live())

    def count_live(self, **facts):
        with self.printing:
            if self.live is not None:
                text, began, _ = self.live
                self.live = (text, began, facts)
                write_out(self.redrawn_live())

    def end_live(self):
        with self.printing:
            if self.live is not None:
                taken_out = self.shrunk(self.live_rows(), self.live_rows())
                self.live, self.under = None, []
                write_out(taken_out)

    def keep_ticking(self):
        while True:
            time.sleep(self.TICK_SECONDS)
            self.tick()

    def tick(self):
        with self.printing:
            if self.live is not None:
                self.frame += 1
                write_out(self.redrawn_live())

    def live_rows(self):
        return 0 if self.live is None else 1 + len(self.under)

    def resized(self, rows_before, rows_after):
        if rows_after > rows_before:
            return self.grown(rows_after - rows_before)
        return self.shrunk(rows_before, rows_before - rows_after)

    def grown(self, by):
        return self.room_made(by, above=0) + RESTORE_CURSOR

    @staticmethod
    def room_made(rows, above):
        return f"{ROW_DOWN_OR_SCROLL * rows}{SAVE_CURSOR}{rows_up(rows + above + 1)}{inserted_rows(rows)}"

    def shrunk(self, rows, by):
        return self.at_the_live_block(rows, DELETE_LINE * by) + CURSOR_UP * by if by else ""

    def redrawn_live(self):
        changed = NEXT_LINE.join(row + CLEAR_TO_LINE_END for row in self.live_drawn())
        return self.at_the_live_block(self.live_rows(), changed)

    @staticmethod
    def at_the_live_block(rows, change):
        return f"{SAVE_CURSOR}{lines_up(rows + 1)}{change}{RESTORE_CURSOR}"

    def live_drawn(self):
        text, began, facts = self.live
        width = shutil.get_terminal_size().columns - 1
        tinkering = live_line(text, began, self.frame, width, sys.stdout, **facts)
        return [tinkering, *(f"  {styled(cut_to_width(line, width - 2), 'dim')}" for line in self.under)]

    def prompt(self):
        return styled("› ", "accent") if typed_here() else self.PROMPT

    @staticmethod
    def rule():
        return styled("─" * shutil.get_terminal_size().columns, "dim")

    def ruled(self):
        return self.reading and typed_here()


SAVE_CURSOR, RESTORE_CURSOR, CURSOR_UP, NEXT_LINE = "\0337", "\0338", "\033[A", "\033[E"
COLOUR_CODE_RE = re.compile(r"\033\[[0-9;]*m")
DELETE_LINE, CLEAR_DOWN, ROW_DOWN_OR_SCROLL = "\033[M", "\033[J", "\033D"


def lines_up(rows):
    return f"\033[{rows}F"


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
    columns = shutil.get_terminal_size().columns
    row, width = "", 0
    for char in line:
        if width + char_width(char) > columns:
            yield row
            row, width = "", 0
        row += char
        width += char_width(char)
    yield row


def rows_shown(text):
    columns = shutil.get_terminal_size().columns
    return sum(
        max(1, -(-visible_width(COLOUR_CODE_RE.sub("", line)) // columns)) for line in text.split("\n")
    )


def rows_up(rows):
    return f"\033[{rows}A"


def inserted_rows(rows):
    return f"\033[{rows}L"


def write_out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def typed_here():
    return sys.stdin.isatty() and colourful(sys.stdout)


def styled(text, style):
    return paint(text, style, sys.stdout)


def marked(text):
    return indented(text, styled("● ", "accent"))


def indented(text, first="  "):
    return first + text.replace("\n", "\n  ") if colourful(sys.stdout) else text


def split_at_tinkering(lines):
    at = next((row for row, line in enumerate(lines) if is_tinkering(line)), None)
    return (lines[:at], lines[at], lines[at + 1 :]) if at is not None else ([], lines[0], [])


def is_tinkering(line):
    star, _, rest = Markup.plain(line).lstrip().partition(" ")
    return star in Messages.STAR and rest.startswith("Tinkering…")


class Message:
    def __init__(self, chat, text, sender="", id=None):
        self.chat, self.text, self.sender, self.id = str(chat), (text or "").strip(), sender, id


def pieces(text, size=3500):
    parts, part = [], ""
    for whole in text.splitlines():
        for line in [whole[start : start + size] for start in range(0, len(whole), size)] or [""]:
            if part and len(part) + len(line) + 1 > size:
                parts.append(part)
                part = ""
            part = f"{part}\n{line}" if part else line
    return parts + [part] if part else parts


class Markup:
    FENCE_RE = re.compile(r"```(\w*)\n?(.*?)(?:\n?```|\Z)", re.S)
    CODE_RE = re.compile(r"`([^`\n]+)`")
    LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
    ESCAPED_RE = re.compile(r"\\([*_`~\\])")
    MARKS = (
        (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "b"),
        (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), "s"),
        (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"), "i"),
        (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "i"),
    )
    ITEM_RE = re.compile(r"(?m)^\s*[-*] (?=\S)")
    HEADING_RE = re.compile(r"(?m)^#{1,6} +(.+)$")
    ANSI_ON_OFF = {"b": ("1", "22"), "i": ("3", "23"), "s": ("9", "29")}
    TERMINAL_OUTPUT_LINES = 30

    @classmethod
    def of(cls, template):
        return "".join(part if isinstance(part, str) else cls.formatted(part) for part in template)

    @classmethod
    def formatted(cls, part):
        match part.format_spec:
            case "":
                return cls.literal(part.value)
            case "md":
                return str(part.value)
            case "code":
                return f"`{part.value}`"
            case "block":
                return f"```\n{cls.fenced(part.value)}\n```"
            case spec:
                raise ValueError(f"no Markup format {spec!r}")

    @staticmethod
    def literal(text):
        return re.sub(r"([*_`~\\])", r"\\\1", str(text))

    @staticmethod
    def fenced(text):
        return str(text).replace("```", "ˋˋˋ")

    @staticmethod
    def columns(text):
        lines = str(text).expandtabs().splitlines()
        rows = [line for line in lines if len(line.split()) >= 3]
        cuts = Markup.column_cuts(rows) if len(rows) >= 2 else []
        if len(cuts) < 2:
            return str(text)
        return "\n".join(Markup.in_columns(line, cuts) if len(line.split()) >= 3 else line for line in lines)

    @staticmethod
    def column_cuts(rows):
        shape = min(rows, key=lambda line: len(line.split()))
        last = list(re.finditer(r"\S+", shape))[-1].start()
        tolerated = 1 if len(rows) >= 4 else 0
        blank = [sum(at < len(line) and line[at] != " " for line in rows) <= tolerated for at in range(last)]
        gaps = [at for at in range(1, last) if blank[at] and not blank[at - 1]] + [last]
        cuts, begin = [], 0
        for index, gap in enumerate(gaps):
            after = shape[gap : gaps[index + 1]] if index + 1 < len(gaps) else shape[gap:]
            if shape[begin:gap].strip() and after.strip():
                cuts.append(gap)
                begin = gap
        return cuts

    @staticmethod
    def in_columns(line, cuts):
        parts, begin = [], 0
        for cut in cuts:
            if cut >= len(line):
                break
            if line[cut] != " " and line[cut - 1] != " ":
                cut = line.find(" ", cut)
                if cut < 0:
                    break
            if cut > begin:
                parts.append(line[begin:cut].strip())
                begin = cut
        parts.append(line[begin:].strip())
        return " | ".join(part for part in parts if part)

    @classmethod
    def html(cls, text):
        keep = cls.keeper()

        def block(found):
            body = html.escape(found.group(2).rstrip("\n"), quote=False)
            return keep(
                f"<blockquote expandable>{body}</blockquote>"
                if found.group(1) == "output"
                else f"<pre>{body}</pre>"
            )

        return cls.render(
            text,
            block,
            lambda code: f"<code>{html.escape(code, quote=False)}</code>",
            lambda plain: html.escape(plain, quote=False),
            lambda tag, inner: f"<{tag}>{inner}</{tag}>",
            keep,
            lambda words, url: f'<a href="{html.escape(url)}">{html.escape(words, quote=False)}</a>',
        )

    @classmethod
    def ansi(cls, text):
        if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
            return cls.plain(text)
        keep = cls.keeper()

        def block(found):
            body = found.group(2).rstrip("\n").splitlines()
            if found.group(1) == "output":
                more = len(body) - cls.TERMINAL_OUTPUT_LINES
                body = body[: cls.TERMINAL_OUTPUT_LINES] + ([f"… {more} more lines"] if more > 0 else [])
                return keep("\n".join(f"\033[2m  │ {line}\033[22m" for line in body))
            return keep("\n".join(f"    \033[36m{line}\033[39m" for line in body))

        return cls.render(
            text,
            block,
            lambda code: f"\033[36m{code}\033[39m",
            lambda plain: plain,
            lambda tag, inner: f"\033[{cls.ANSI_ON_OFF[tag][0]}m{inner}\033[{cls.ANSI_ON_OFF[tag][1]}m",
            keep,
            lambda words, url: f"\033[4m{words}\033[24m \033[2m({url})\033[22m",
        )

    @classmethod
    def plain(cls, text):
        keep = cls.keeper()
        return cls.render(
            text,
            lambda found: keep(found.group(2).rstrip("\n")),
            lambda code: code,
            lambda plain: plain,
            lambda tag, inner: inner,
            keep,
            lambda words, url: f"{words} ({url})",
        )

    @classmethod
    def render(cls, text, block, code, plain, mark, keep, link):
        text = cls.FENCE_RE.sub(block, str(text))
        text = cls.LINK_RE.sub(lambda found: keep(link(found.group(1), found.group(2))), text)
        text = cls.ESCAPED_RE.sub(lambda found: keep(plain(found.group(1))), text)
        text = cls.CODE_RE.sub(lambda found: keep(code(found.group(1))), text)
        text = cls.ITEM_RE.sub("• ", plain(text))
        text = cls.HEADING_RE.sub(lambda found: mark("b", found.group(1)), text)
        for pattern, tag in cls.MARKS:
            text = pattern.sub(lambda found, tag=tag: mark(tag, found.group(1)), text)
        return re.sub("\x00(\\d+)\x00", lambda found: keep.kept[int(found.group(1))], text)

    @staticmethod
    def keeper():
        kept = []

        def keep(shown):
            kept.append(shown)
            return f"\x00{len(kept) - 1}\x00"

        keep.kept = kept
        return keep


md = Markup.of


def help_text(words=()):
    try:
        done = subprocess.run(
            tinker_command() + ["help", *words],
            text=True,
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, NO_COLOR="1", TINKER_FORMAT="markup"),
        )
    except OSError, subprocess.SubprocessError:
        return "❓ `help` lists what you can send"
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
    older = f" · `history {page + 1}` older" if page < pages else ""
    return "\n".join(lines + [f"👉 `history #N` shows one again{older}"])


def history_thing(word):
    thing = Ledger.thing(word[1:]) if word[1:].isdigit() else None
    if thing is None:
        return md(t"🤷 **No {word} in the history:** `history` lists what it keeps")
    asked = md(t"💬 _you:_ {thing['asked']}\n") if thing.get("asked") else ""
    return md(t"📜 **#{thing['number']}** · {thing['time']}\n") + asked + "\n" + thing["text"]


def entry_headline(text):
    lines = [line for line in Markup.plain(text).splitlines() if line.strip()]
    return first_words(lines[0] if lines else "", 60)


def commands_brief():
    return "; ".join(re.findall(r"- `([^`]+)`", help_text())) or "help"


def tinker_command():
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


def tinker_process(arguments, folder=None, output=None, **env):
    return subprocess.Popen(
        tinker_command() + arguments,
        cwd=folder,
        env=dict(os.environ, PYTHONUNBUFFERED="1", **env),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
        start_new_session=True,
    )


def started_by_a_conversation():
    return os.environ.get(CHANNEL_REPORTS_ENV) == "off"


def json_answer(text):
    try:
        data = Reply(text).json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def text_of(output):
    return output.decode(errors="replace") if isinstance(output, bytes) else (output or "")


def keep_typing(channel, chat, done):
    while not done.is_set():
        channel.typing(chat)
        done.wait(TYPING_SIGN_SECONDS)


def first_words(text, size=160):
    return " ".join(str(text).split())[:size]


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
