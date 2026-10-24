"""The conversation: talking to Tinker the way a person does, in the terminal (tinker, with no command) or
in a chat app (the channels plugin: tinker channel start), one Tinker serving them all at once (Desk).
Every command works the same way everywhere: a task (its spec comes first, to confirm or amend before
anything is built), a resume, the runs, their status, stop, and every other command of tinker's (one that
deletes waits for confirm); anything else is a normal chat with the model in use (Talk), told at once when
a task holds the model; `command WHAT` has the model name the command line that does it, run once the
person confirms it (Shell); `research TOPIC` answers from the web, with the pages as sources (Research).
A chat or a command line asked while a job runs pauses it (jobs.paused_for_you): the person is answered at
once, and the job goes on as soon as they are. A task run from a terminal is served the same way: its
tracker is kept in Tinker's home ("running"), and it is stopped with a signal.

Channel is what every kind of channel does (receive messages, send, edit what it sent); ConsoleChannel is
the terminal, and the channels plugin adds the chat apps (TelegramChannel). Every message is written in
Markup (bold, italic, code ...), each mark for what it means, and each kind shows it its own way. Each
thing (a task, a research, a command, a chat's reply) has one message, edited as it goes on
(show), with when it was last updated; a new one is sent only for a new thing, or for what no longer fits.
Tinker's status is the one message pinned, edited in place for ever. A chat is only a view of the ledger
(ledger.py): its messages go after a while (a glance, like jobs or help, and the question it answered, at
once), and history shows any thing again."""
import contextlib
lazy import fcntl
lazy import html
lazy import json
import os
lazy import queue
import re
lazy import shlex
import signal
lazy import subprocess
import sys
lazy import threading
lazy import time
lazy import webbrowser
lazy from pathlib import Path

from .commands import COMMANDS, TERMINAL_ONLY, named, refs
from .jobs import STOP, Jobs, job_number, paused_for_you, today
lazy from .ledger import NOTE_KEEP, Ledger
lazy from .messages import human_duration, say
lazy from .model import Models, Ollama, OllamaError
from .project import Runs, Tracker, saved
lazy from .settings import EXERCISE_FOLDER, Config, Home, projects_folder
lazy from .web import Internet
lazy from .workspace import Toolchain


# --- tasks, and the Tinkers that serve them --------------------------------------------------------------
OFF = "TINKER_CHANNELS"                        # =off: a run the conversation started does not report too
EDIT_EVERY = 5                                 # seconds between edits of a progress message
CLOCK_EVERY = 15                               # ... between edits that only move its clock on
STOP_WAIT = 120                                # seconds a build is given to stop (and save) before it is killed
STATUS_EVERY = 10                              # seconds between looks at what Tinker does, for its pinned status
COLLECT_EVERY = 2                              # seconds between takings out of the messages whose time is up

# --- a chat with the model ---------------------------------------------------------------------------------
CHAT_KEPT = 12                                 # messages of one chat kept as the context of its next reply
CHAT_REPLY_TOKENS = 1024                       # the longest chat reply
CHAT_TIMEOUT = 600                             # seconds a chat reply may take (a task may hold the model first)
TYPING_EVERY = 4                               # seconds between "typing…" signs while a reply is written
BUSY_AFTER = 5                                 # seconds a chat reply may take before a note says the model is busy
BUSY_EDIT_EVERY = 30                           # seconds between updates of that note

# --- command WHAT, research TOPIC --------------------------------------------------------------------------
COMMAND_TOKENS = 300                           # the longest reply naming a command
COMMAND_TIMEOUT = 120                          # seconds a command run from the chat may take
COMMAND_SHOWN = 3000                           # chars of its output sent back, folded (its end, when longer)
COMMAND_READ = 6000                            # ... the model reads to tell what it says
RESEARCH_SEARCHES = 3                          # web searches a research makes
RESEARCH_PAGES = 4                             # pages it reads (the first each search finds, then the second ...)
RESEARCH_PAGE_CHARS = 3500                     # of each page, the parts most about the topic

# --- tinker's commands, as the conversation takes them: from the one table (commands.py) -------------------
LONG = named("long")                           # a task: its progress shown (one that runs is paused for it)
QUICK = named("quick")                         # what it prints is shown
GLANCES = QUICK + ("help", "history")          # what only shows something: its message goes soon, and its question at once
DELETES = tuple(c.name for c in COMMANDS if c.deletes)  # what it deletes is shown first; done once confirmed
PLANNED = tuple(c.name for c in COMMANDS if "--confirm" in (o.flag for o in c.options))  # its plan waits here
OWN = named("own")                             # the conversation's own
ONE_WORD = tuple(c.name for c in COMMANDS if c.chat and c.words == "one")  # a command with one word after it
ANY_WORDS = tuple(c.name for c in COMMANDS if c.chat and c.words == "any")  # ... with any words after it
SAID_BARE = tuple(c.name for c in COMMANDS if c.name)  # commands a conversation takes as their words
BARE_RE = re.compile(r"(?<![/\w.-])tinker (?=(?:" + "|".join(sorted(SAID_BARE, key=len, reverse=True)) + r")\b)")
USAGE = frozendict({c.name: f"✍️ Write `{c.synopsis}`" + (f", e.g. `{c.example}`" if c.example else "") for c in COMMANDS if c.name})
EXIT = ("exit",)  # end the conversation (in a chat, once confirmed: it cannot be started again from there)
START = "/start"  # what a chat app sends when a person first opens the bot
WELCOME = """👋 **Hi, I'm Tinker!**
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
- `research the newest Python release`: I search the web, and name my sources
- `command how full is my disk`: I find the command line, and run it once you `confirm`
- `learn rust`: I study it, and use it in my builds

⏳ **While I work**
- `status` tells what I am doing
- `stop` stops it (it is saved): `jobs` lists what runs and what stopped, `resume #3` goes on
- Just write to me: I pause what runs, answer you, and go on with it

❓ `help` lists everything I can do, and `help task` explains one command."""  # what a chat app's /start gets


# === the conversation ============================================================================================

def session(root):
    """tinker with no command: the conversation in this terminal, and in the enabled chats too: one Tinker
    serving them all, so a task started in one is seen, stopped and answered from the others."""
    try:
        chats = chat_channels()
    except SystemExit as exc:
        say.warn(f"Chats left out: {exc}")
        chats = []
    Desk([ConsoleChannel()] + chats, root).run()


def one_shot(command, text):
    """tinker chat|command|research|run|history ...: one of the conversation's own commands, said once in this terminal
    (command asks there before it runs anything). A chat or a command line pauses the job that runs. None, or why
    it could not."""
    console, talk = ConsoleChannel(bare=False), Talk(commands_brief())
    message, chat = Message(ConsoleChannel.CHAT, text, "you"), ConsoleChannel.CHAT
    if command == "history":
        console.send(chat, history_text(text.split()[:1]))
        return None
    if not text and command != "run":
        return f"tinker {command} needs words after it, e.g. tinker {command} {'hello' if command == 'chat' else '...'}"
    if command == "chat":
        with paused_for_you():
            console.show(chat, None, talk.reply(console, message, "", CHAT_REPLY_TOKENS), done=True)
    elif command == "research":
        research_once(console, talk, text)
    elif command == "run":
        Desk([console], Path.cwd()).run_app(console, chat, [text])
    else:
        with paused_for_you():
            command_once(console, talk, message)
    return None


def research_once(console, talk, topic):
    """tinker research TOPIC: researched in this terminal. It is a job: its tracker shows its steps where it was
    started from (a chat), and ends with its answer, or how to go on once stopped."""
    tracker, started = Tracker(f"Researching {topic}"), time.monotonic()
    ending = {"status": "stopped", "headline": f"Research of {topic} stopped"}
    try:
        status, text = Research(talk, tracker).researching(console, ConsoleChannel.CHAT, topic,
                                                          lambda: [described(state) for state in running()])
        ending = {"status": status, "headline": "", "text": text}
    finally:
        tracker.end(ending, time.monotonic() - started)
        if os.environ.get(OFF) != "off":  # the conversation that started it reads it last
            tracker.close()


def command_once(console, talk, message):
    """tinker command WHAT: the command line named, and run once the person agrees in this terminal."""
    chat, shell = ConsoleChannel.CHAT, Shell(talk, Path.cwd())
    console.show(chat, None, shell.propose(console, message, "", COMMAND_TOKENS), done=True)
    proposed = shell.take(console, chat)
    if not proposed:
        return
    if sys.stdin.isatty() and input("Run it? [y/N] ").strip().lower() in ("y", "yes"):
        shell.run(console, chat, proposed)
    else:
        console.show(chat, None, f"🚫 _Not run:_ `{proposed['line']}`", done=True)


class Desk:
    """The conversation, one Tinker serving its channels at once: the terminal (tinker, with no command) and
    the enabled chats (tinker channel start serves the chats alone). Every command of tinker's works here: a
    task runs as its own tinker (which does not report too; as every job, it pauses the one that runs), followed
    by its tracker, its progress in its one message; one that deletes shows what it deletes first, and waits for confirm. Tasks run from a
    terminal are served too (running). Anything that is not a command is a normal chat."""
    COMMANDS = tuple(c.name for c in COMMANDS if c.chat)
    LOOK_EVERY = 1.0  # seconds between looks at a followed task's tracker (for its run and folder)

    def __init__(self, channels, root):
        self.channels, self.root = channels, Path(root).resolve()
        self.process = self.reporter = self.task = None  # task: {arguments, folder, title, run} of the last one
        self.followed = []  # every task started here whose process lives: the last one, and those it paused
        self.lock, self.talk = threading.Lock(), Talk(commands_brief(), pausing=paused_for_you)
        self.shell = Shell(self.talk, self.root)
        self.waiting = None  # {run, folder, title, spec, stage, new} of the plan that waits for confirm or amend
        self.boards = {}  # run -> its task's messages ({(channel, chat): ids}), kept from one of its runs to the next
        self.asking = {}  # (channel, chat) -> {words, shown}: a command that deletes, waiting for confirm
        self.spent = {}  # run -> the seconds its runs took so far (a task's time: done in ...)
        self.consoles = [c for c in channels if isinstance(c, ConsoleChannel)]
        self.said_status = None  # the status said last (a status said again only when it changed)
        self.exercising = None  # the Reporter of the exercise this conversation follows
        self.echo = not self.consoles  # a task's printed lines go to the terminal too (not to a console's)

    def run(self):
        self.claim_chats()
        self.talk.warm()
        for channel in self.channels:
            channel.skip_waiting()
            channel.offer(offered())
        self.say_status(anew=True)
        self.adopt_exercise()
        if not self.consoles:
            names = ", ".join(c.name for c in self.channels)
            say.info(f"Listening on {names}; projects go in {self.root}. Ctrl-C stops.", "👂")
        try:
            self.listen_until_ended()
        except KeyboardInterrupt:
            self.stop_task()
            self.say_status("💤 **Tinker** · not listening")
            if not self.consoles:
                say.title("Stopped listening.", "🛑")

    def claim_chats(self):
        """Each chat listened to by this Tinker alone: one another Tinker listens to is left out (served by
        that one; without a terminal here, there is nothing to serve)."""
        for channel in [c for c in self.channels if c not in self.consoles]:
            channel.pins = True  # it hears the chat: it can tell the notes of its pins from the person's messages
            if not channel.claim({"pid": os.getpid()}):
                held = f"{channel.name} is listened to by another Tinker (process {channel.holder().get('pid', '?')})"
                if not self.consoles:
                    raise SystemExit(f"tinker: {held}: stop it first")
                say.warn(f"{held}: this one talks here only")
                self.channels.remove(channel)

    def say_status(self, text=None, anew=False):
        """Tinker's status, one message in each chat, pinned (the console: said at its start and end): text,
        else what it does now, said only when it changed (anew: it starts listening, said whatever it is)."""
        text = text or self.status()
        if text == self.said_status and not anew:
            return
        self.said_status = text
        for channel in self.channels:
            if channel not in self.consoles:
                self.pin_status(channel, text)
            elif anew or text.startswith("💤"):
                channel.show(ConsoleChannel.CHAT, None, text, done=True)

    def status(self):
        """What Tinker is doing now, as its status says it (Markup): working, waiting for the person, or idle."""
        if self.busy():
            progress = self.reporter.progress
            passing = f" · {progress.passing}" if progress.passing else ""
            return md(t"🔄 **Tinker** · working on {progress.title}\n▸ {Markup.plain(progress.snapshot['doing']):md}"
                      t"{passing}")
        if self.waiting:
            then = "`confirm` · `rename NAME`" if self.waiting.get("stage") == "name" else "`confirm` · `amend WHAT`"
            return md(t"🙋 **Tinker** · {self.waiting['title']} waits for you: {then:md}")
        elsewhere = [described(state) for state in persons_runs(self.children())]
        if elsewhere:
            return "🔄 **Tinker** · working in a terminal: " + "; ".join(r["brief"] for r in elsewhere)
        exercise = exercising_now()
        if exercise:
            return f"🏋️ **Tinker** · {exercise['brief']}\n_It pauses whenever you ask for something._"
        return "🟢 **Tinker** · idle: send a task"

    @staticmethod
    def pin_status(channel, text):
        """The status pinned in each of the channel's chats: the one message it has, kept from start to start
        (Tinker's "listening"), edited in place; it never goes."""
        mine = Home.store().get("listening", channel.name) or {}
        for chat in channel.allowed:
            before = mine.get(chat)
            shown = channel.show(chat, [before] if before else None, text, bottom=False, pin=True)
            channel.lasting(chat, shown)
            mine[chat] = shown[0] if shown else None
        Home.store().put("listening", channel.name, mine)

    def listen_until_ended(self):
        """Every channel listened to, each in its thread, until the person ends it (exit) or Ctrl-C."""
        threads = [threading.Thread(target=self.listen, args=(c,), daemon=True) for c in self.channels]
        for thread in threads:
            thread.start()
        looked = collected = 0.0
        while any(t.is_alive() for t in threads) and not any(c.closed for c in self.channels):
            time.sleep(0.5)
            if time.monotonic() - collected >= COLLECT_EVERY:
                collected = time.monotonic()
                for channel in self.channels:
                    channel.collect()
            if time.monotonic() - looked >= STATUS_EVERY:
                looked = time.monotonic()
                self.say_status()  # what runs elsewhere (an exercise, a terminal's run) moves on by itself
                if self.exercising is None:
                    self.adopt_exercise()  # a paused exercise that went on by itself has its message kept up again
        raise KeyboardInterrupt

    def listen(self, channel):
        while not channel.closed:
            for message in channel.listened(timeout=1 if channel in self.consoles else 20):
                if channel not in self.consoles:
                    say.text(f"[{time.strftime('%H:%M:%S')}] {channel.name}: {message.text}")
                self.hear(channel, message)

    def hear(self, channel, message):
        """One message: a welcome for a person who just opened the bot; the answer to what waits for it; else
        the command it gives (the slow ones in a thread, so the next message is heard meanwhile); else a chat."""
        if message.text.split()[:1] == [START]:
            channel.send(message.chat, welcome())
            return
        command = command_of(message.text)
        if message.id and (command == "confirm" or exercise_switch(message.text)):  # its answer is in a message
            # already (the plan's, the exercise's): it would only make the chat grow
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
        if command == "command":
            self.asked_with_words(channel, message, command)
            return
        reply = self.answer(message, channel)
        if reply:
            sent = channel.send(message.chat, reply)
            if command in GLANCES:
                channel.glance(message.chat, message.id, [sent])

    def asked_with_words(self, channel, message, command):
        """command WHAT: answered in a thread (alone, how to say it)."""
        if len(message.text.split()) == 1:
            channel.send(message.chat, USAGE[command])
        else:
            self.shell.name(channel, after_word(message), self.now)

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
        words = command_words(message.text)
        command = words[0]
        if command == "task":
            return self.new_task(words)
        if command in ("confirm", "amend", "rename"):
            return self.decide(command, words)
        if command == "resume":
            return self.resume(words)
        if command == "stop":
            return self.stop(words)
        if command == "research":
            return self.start_task(words, self.root, " ".join(words)) if len(words) > 1 else USAGE["research"]
        if command == "exercise":
            return self.exercise(channel, message, words)
        if command in ("runs", "status"):
            return self.report(command, words)
        if command == "help":
            return help_text(words[1:2])
        if command == "history":
            return history_text(words[1:2])
        if command == "clear" and words[1:] == ["chat"]:
            return self.clear_chat(channel, message.chat, "Chat cleared", " · `history` shows any of them again")
        if command in EXIT:  # the console ends on its own word (ConsoleChannel); a chat asks first
            return self.ask_to_exit(channel, message.chat, command)
        if command == "channel" and words[1:2] and words[1] in TERMINAL_ONLY:
            return f"🖥 `channel {words[1]}` asks in a terminal: `tinker channel {words[1]}` there"
        if command == "read" and words[1:2] in (["list"], ["show"]):
            return md(t"📚 **{' '.join(words)}**\n{self.tinker(words):block}")
        if command in DELETES and "--yes" not in words:
            return self.ask_first(channel, message.chat, words)
        if command in LONG:
            return self.start_task(words, self.root, " ".join(words))
        if command == "memory" and "--graph" in words:
            return self.memory_graph(channel, message.chat, words)
        shown = self.tinker(words)
        return md(t"⚙️ **{' '.join(words)}**\n{shown:block}")

    def exercise(self, channel, message, words):
        """exercise LANGUAGE: started as a tinker of its own that outlives this conversation (what it prints goes
        to the exercise folder's log), shown only in the exercise's one message. exercise status: what tinker says,
        answered in a thread, a glance."""
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
        """The exercise told to stop (its build saves itself first); its message says how it ended."""
        running_one = exercising_now()
        if not running_one:
            return "🤷 No exercise runs: `resume #N` goes on with one (`jobs stopped` lists them)"
        signal_task(running_one["pid"], STOP)
        self.exercise_says("🛑 Stopping: its build saves itself first")
        return None

    def start_exercise(self, words, title=None):
        """The exercise started (words: exercise LANGUAGE, or resume #N): as every job, it pauses the one that runs.
        Its message comes to the bottom."""
        log = projects_folder() / EXERCISE_FOLDER / "exercise.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        start = log.stat().st_size if log.exists() else 0
        try:
            with open(log, "a", encoding="utf-8") as output:
                process = tinker_process(words, self.root, output, **{OFF: "off", "NO_COLOR": "1"})
        except OSError as exc:
            return md(t"⚠️ **Could not start it:** {exc}")
        self.follow_exercise(process.pid, title or " ".join(words), process.wait, (log, start), anew=True)
        return None

    def adopt_exercise(self):
        """As this conversation starts: an exercise that runs and does not report by itself (one a conversation
        before this one started) has its message kept up here; one that ended without being stopped (its process
        killed, the computer restarted) goes on, as its job resumed. Either way its message comes anew at the bottom
        of the chat."""
        exercise, left = exercising_now(), Home.store().get("activity", "exercise")
        if exercise and not exercise["reports"]:
            self.follow_exercise(exercise["pid"], exercise["title"], lambda: until_ended(exercise["pid"]), anew=True)
        elif not exercise and left and not alive(left["pid"]):
            job = next((job for job in Jobs.listed("stopped") if job["command"] == "exercise"), None)
            self.start_exercise(["resume", f"#{job['number']}"] if job else ["exercise", left["language"]],
                                job["title"] if job else None)

    def follow_exercise(self, pid, title, ended, log=None, anew=False):
        """The exercise's message kept up (its tracker) until ended() returns: its ending shown, its tracker
        removed. One that ended before it kept a tracker shows what it printed to log ((path, from offset)).
        Every exercise has the one message (its ids kept in the home, "activity"), started, stopped or resumed;
        anew: the one before is taken out, and it is sent at the bottom."""
        sent = exercise_board()
        if anew:
            self.take_out(sent)
        if self.exercising is not None:  # paused for this one: its message is this one's until it goes on
            self.exercising.ended = True
        reporter = self.exercising = Reporter(self.channels, title, pid=pid, sent=sent,
                                              on_step=lambda: (keep_exercise_board(sent), self.say_status()))
        reporter.refresh()

        def follow():
            ended()
            if reporter is not self.exercising:  # its message went to another exercise (adopted again if it went on)
                return
            printed = printed_since(*log) if log and reporter.tracked() is None else ""
            reporter.finish()
            if printed:
                reporter.say(md(t"⚠️ **{title}**\n{printed:block}"), done=True)
            keep_exercise_board(sent)
            if self.exercising is reporter:
                self.exercising = None
            Home.store().delete("running", str(pid))
            self.say_status()
        threading.Thread(target=follow, daemon=True).start()

    def take_out(self, sent):
        """The messages of sent ({(channel, chat): ids}) taken out of their chats, and forgotten."""
        for channel in self.channels:
            for (name, chat), ids in list(sent.items()):
                if name == channel.name:
                    for old in ids or []:
                        channel.delete(chat, old)
                    del sent[(name, chat)]

    def exercise_says(self, line):
        """A line shown as the latest of the exercise's message, which comes to the bottom of the chat."""
        reporter = self.exercising
        if reporter is None:
            return
        with reporter.lock:
            reporter.progress.snapshot["latest"] = line
            reporter.fresh = True
            reporter.update()

    def memory_graph(self, channel, chat, words):
        """memory --graph: the page made (tinker memory --graph) and sent as a file, what tinker printed under it."""
        page = Home.graph_page()
        before = page.stat().st_mtime_ns if page.exists() else None
        shown = self.tinker(words)
        if not page.exists() or page.stat().st_mtime_ns == before:  # not written now: what went wrong is shown
            return md(t"🧠 **{' '.join(words)}**\n{shown:block}")
        channel.send_file(chat, page, md(t"🧠 **Tinker's memory**\n{shown}"))
        return None

    def new_task(self, words):
        """task WHAT: Tinker makes its folder in the projects folder, named after it."""
        if len(words) < 2:
            return USAGE["task"]
        request = " ".join(words[1:])
        return self.start_task(["task", request], self.root, request)

    def decide(self, command, words):
        """confirm [@PROJECT]: build the plan that waits (a new project's folder name is asked once first:
        confirm again, or rename NAME, builds it); amend [@PROJECT] WHAT: change it (it waits again); rename
        NAME: rename its folder."""
        waiting = self.waiting or {}
        if command == "rename":
            return self.rename(waiting, words)
        name, rest = refs(words[1:])
        if command == "amend" and not rest:
            return USAGE["amend"]
        if command == "confirm" and not name and waiting.get("stage") == "spec" and waiting.get("new"):
            return self.ask_folder_name(waiting)
        target = name or waiting.get("run")
        if not target:
            return "🤷 No plan waits for confirmation: start one with `task WHAT TO BUILD`"
        mine = bool(waiting) and target in (waiting.get("run"), Path(str(waiting.get("folder", ""))).name)
        folder, title = (waiting["folder"], waiting["title"]) if mine else (self.root, f"@{target}")
        target = waiting["run"] if mine else target
        if command == "confirm":
            return self.on_task(waiting if mine else {}, self.start_task(
                ["resume", f"@{target}"], folder, title, "✅ **Confirmed:** building it",
                keep=self.kept(waiting) if mine else ""))
        change = " ".join(rest)
        return self.on_task(waiting if mine else {}, self.start_task(
            ["amend", f"@{target}", change], folder, title, md(t"✏️ **Changing the plan:** {change}")))

    def ask_folder_name(self, waiting):
        """A new project's plan confirmed: its folder's name is asked once, before it is built."""
        waiting["stage"] = "name"
        self.on_board(waiting, self.asked(waiting))
        return None

    def on_task(self, waiting, said):
        """What was said about the task whose plan waits (a refusal), in its message rather than apart, under
        what it waits for."""
        if said and waiting:
            self.on_board(waiting, f"{self.asked(waiting)}\n\n{said}")
            return None
        return said

    @staticmethod
    def asked(waiting):
        """The waiting plan, with what it waits for: its folder's name, once a new project's plan is confirmed."""
        if waiting.get("stage") != "name":
            return waiting["spec"]
        return (f"{waiting['spec']}\n\n📁 **Its folder:** `{Path(waiting['folder']).name}`\n👉 `confirm` to build it "
                f"there · `rename NAME` to name it otherwise (it builds then)")

    @staticmethod
    def kept(waiting):
        """Of a waiting plan, what stays in the task's message once it is built: all but what to send next."""
        return "\n".join(line for line in waiting["spec"].splitlines() if not line.startswith(("👉", "⏱"))).strip()

    def on_board(self, waiting, text):
        """text shown in the message of the task whose plan waits (Tinker's status follows it)."""
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
            return "🤷 No plan waits for confirmation, so no folder can be renamed (once built, it stays)."
        if self.busy():
            return self.on_task(waiting, "⏳ A task is running: wait for it, or `stop` it first.")
        done = subprocess.run(tinker_command() + ["rename", f"@{waiting['run']}", words[1]], cwd=self.root, text=True,
                              capture_output=True, stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1"),
                              timeout=60)
        if done.returncode:
            why = first_lines((done.stdout + done.stderr).strip(), 10).splitlines()[-1]
            return self.on_task(waiting, md(t"⚠️ **Not renamed:** {why}"))
        self.renamed(waiting, words[1])
        if waiting.get("stage") != "name":
            self.on_board(waiting, f"{waiting['spec']}\n\n📁 **Renamed:** its folder is `{words[1]}`")
            return None
        return self.on_task(waiting, self.start_task(["resume", f"@{waiting['run']}"], waiting["folder"],
                                                     waiting["title"], f"✅ **Renamed** to `{words[1]}`: building it",
                                                     keep=self.kept(waiting)))

    @staticmethod
    def renamed(waiting, name):
        """The waiting plan as its folder's new name says it."""
        old, waiting["folder"] = waiting["folder"], Path(waiting["folder"]).parent / name
        waiting["title"] = f"{name}: {waiting['title'].split(': ', 1)[-1]}"
        waiting["spec"] = waiting["spec"].replace(f"📁 `{Path(old).name}`", f"📁 `{name}`")

    def resume(self, words):
        """resume @PROJECT: its run goes on; resume [#N]: the job goes on where it stopped (with no number, the one
        stopped last), an exercise as the exercise."""
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
        """stop #N: that job; stop: the job that runs, whatever it is (as Ctrl-C: it saves itself)."""
        if len(words) > 1:
            number = job_number(words[1])
            return self.stop_job(number) if number is not None else USAGE["stop"]
        said = [self.stop_job(job["number"]) for job in Jobs.listed("running")]
        return "\n".join(line for line in said if line) or (None if said else "💤 No job is running.")

    def stop_job(self, number):
        """Job #number stopped: the task started here, the exercise (said in its message), a job elsewhere, or a
        paused one (where it waits: it no longer goes on by itself)."""
        job = Jobs.get(number)
        if job is None or job["state"] not in ("running", "paused"):
            return f"🤷 Job #{number} is not running or paused: `jobs` lists them."
        if job["state"] == "paused":
            Jobs.stop(job)
        elif self.busy() and job["pid"] == self.process.pid:
            self.stop_task()
        elif job["command"] == "exercise":
            return self.stop_exercise()
        else:
            Jobs.stop(job)
        return md(t"⏹ **Stopping #{number}** {job['title']}: it is saved; `resume #{number}` goes on later.")

    def report(self, command, words):
        """runs, status [@PROJECT]: tinker's own; status alone at once, from what the conversation knows."""
        if command == "status" and len(words) == 1:
            return "\n\n".join([self.status()] + [r["text"] for r in self.now()])
        title = "📚 **Runs**" if command == "runs" else md(t"📊 **Status** {' '.join(words[1:2])}").rstrip()
        return f"{title}\n```\n{Markup.fenced(self.tinker([command] + words[1:2]))}\n```"

    def ask_to_exit(self, channel, chat, command):
        shown = channel.show(chat, None, "🛑 **Stop Tinker?** It cannot be started again from here.\n"
                                         "👉 `confirm` to stop it · anything else keeps it", done=True)
        self.asking[(channel.name, chat)] = {"words": [command], "shown": shown}
        return None

    def ask_first(self, channel, chat, words):
        """A command that deletes: what it would delete (tinker's own list of it), waiting for confirm."""
        preview = "\n".join(line for line in self.tinker(words).splitlines() if not line.startswith("Nothing deleted"))
        shown = channel.show(chat, None, md(t"🗑 **{' '.join(words)}**\n{preview:block}\n"
                                            t"👉 `confirm` to delete it · anything else cancels it"), done=True)
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
        if words[0] == "clear" and "history" in words:  # nothing of it is left: not even what clearing it said
            self.tinker(words)
            self.clear_chat(channel, chat, md(t"{' '.join(asking['words'])}: done, and the chat"))
            return
        channel.show(chat, asking["shown"], md(t"🗑 **{' '.join(asking['words'])}**\n{self.tinker(words):block}"),
                     done=True)

    def clear_chat(self, channel, chat, cleared, after=""):
        """clear chat: every message taken out of the chats but Tinker's status (history keeps what they showed),
        said in a note of its own that goes in seconds. None: nothing more to say."""
        taken = sum(each.clear() for each in self.channels)
        sent = channel.send(chat, f"🧹 **{cleared}:** {taken} messages taken out{after}")
        channel.glance(chat, None, [sent], NOTE_KEEP)
        return None

    def run_app(self, channel, chat, said):
        """run [@PROJECT] ARGS... [<<< INPUT]: a built project run the way a person runs it (its run command,
        in its folder and environment), with the person's arguments (and input), and what it printed. The
        project is the one named (@ID or @folder), else the one built last."""
        name, args, stdin = app_request(said[0] if said else "")
        project = built_project(name)
        if project is None:
            channel.show(chat, None, md(t"🤷 **No project @{name}:** `runs` lists them") if name else
                         "🤷 **No project is built yet:** `task WHAT TO BUILD` builds one", done=True)
            return
        workdir, title = Path(project["workdir"]), Path(project["workdir"]).name
        toolchain = Toolchain((saved(workdir, "spec", "plan", {}) or {}).get("toolchain"))
        if not toolchain.run:
            channel.show(chat, None, md(t"🤷 **{title} has no run command:** it is not a program a person runs"),
                         done=True)
            return
        line = " ".join([toolchain.run] + [shlex.quote(arg) for arg in args])
        shown = channel.show(chat, None, md(t"▶️ **{title}** {line:code} …"))
        code, output = in_shell(Shell.SHELL, toolchain.shell(line), workdir, stdin)
        fed = md(t"\n_input:_ `{stdin.strip()}`") if stdin else ""
        printed = f"```\n{Markup.fenced(output[-COMMAND_SHOWN:])}\n```" if output else "_(it printed nothing)_"
        channel.show(chat, shown, md(t"{'✅' if code == 0 else '❌'} **{title}** {line:code} · _exit {code}_{fed:md}\n"
                                     t"{printed:md}"), done=True)

    def start_task(self, arguments, folder, title, phase=Tracker.STARTING, sent=None, keep=""):
        """Start a task as its own tinker. Its message is the one its run had (by its ID: sent, else the
        boards), keeps keep at its top, and says phase first (Markup when Tinker says it: ✅ confirmed ...);
        it is shown before the run's tracker is, so it is one message."""
        run = self.run_named(arguments)
        sent = sent if sent is not None else self.boards.get(run) if run else None
        with self.lock:
            self.task = {"arguments": arguments, "folder": folder, "title": title, "run": run}
            self.reporter = Reporter(self.channels, title, sent=sent, phase=phase, keep=keep,
                                     before=self.spent.get(run, 0.0), on_step=self.say_status)
            with self.reporter.lock:
                self.reporter.update()
            try:
                self.process = self.spawn(arguments)
            except OSError as exc:
                self.process = None
                return md(t"⚠️ **Could not start it:** {exc}")
            self.reporter.pid = self.process.pid
            self.followed = [p for p in self.followed if p.poll() is None] + [self.process]
        if self.waiting and self.waiting["folder"] == folder:
            self.waiting = None  # confirmed or being changed: it waits again only once its plan is shown
        threading.Thread(target=self.follow, args=(self.process, self.reporter, self.task), daemon=True).start()
        return None

    def run_named(self, arguments):
        """The run a resume or amend names (the waiting one, named by its folder), else None."""
        name = refs(arguments[1:2])[0] if arguments[0] in ("resume", "amend") else ""
        waiting = self.waiting or {}
        return waiting.get("run") if name and name == Path(str(waiting.get("folder", ""))).name else name or None

    def spawn(self, arguments):
        """The task as its own tinker, from here (a new task makes its own folder, a run given by ID finds its
        own), reporting through this conversation only. A run that plans waits for its plan to be confirmed."""
        arguments = arguments + ["--confirm"] if arguments[0] in PLANNED else arguments
        env = dict(os.environ, **{OFF: "off", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
        return subprocess.Popen(tinker_command() + arguments, cwd=self.root, env=env, text=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)

    def follow(self, process, reporter, task):
        """The task's printed lines to the terminal (and as its latest line, when it keeps no tracker); its
        run and folder from its tracker (the title is named after the folder). Once it ends: its last state
        shown, a plan that waits kept, and its tracker removed."""
        looked = 0.0
        for line in process.stdout:
            if self.echo:
                sys.stdout.write(line)
            reporter.printed(line)
            if time.monotonic() - looked >= self.LOOK_EVERY:
                looked = time.monotonic()
                self.learn(task, reporter, reporter.tracked())
        process.wait()
        self.ended(process, reporter, task)

    def learn(self, task, reporter, state):
        """A followed task's run, folder and title, as its tracker says them."""
        if not state:
            return
        if state.get("run"):
            task["run"] = state["run"]
            self.boards[task["run"]] = reporter.sent
        if state.get("folder"):
            task["folder"] = Path(state["folder"])
        if state.get("title"):
            task["title"] = state["title"]

    def ended(self, process, reporter, task):
        """A followed task ended: its last state shown, its time kept, a plan that waits kept, and its tracker
        removed."""
        final = reporter.tracked()
        self.learn(task, reporter, final)
        if task["run"]:
            self.spent[task["run"]] = reporter.progress.snapshot.get("before", 0.0) + (final or {}).get("seconds", 0.0)
        reporter.finish(final)
        if final is not None:
            Home.store().delete("running", str(process.pid))
        if task["run"] and reporter.progress.waiting():
            plan = reporter.progress.snapshot["waiting"]
            self.waiting = {"run": task["run"], "folder": task["folder"], "title": task["title"],
                            "spec": reporter.progress.ending(), "stage": "spec", "new": plan.get("kind") == "project"}
        self.say_status()

    def stop_task(self):
        if not self.busy():
            return False
        os.killpg(self.process.pid, signal.SIGINT)  # as Ctrl-C: the run saves itself
        return True

    def busy(self):
        return self.process is not None and self.process.poll() is None

    def now(self):
        """What runs now: the task started here, and any run from a terminal ({title, brief, text}). An exercise's
        builds are left out: they are not jobs (the exercise is, and is paused as one)."""
        progress = self.reporter.progress if self.busy() else None
        here = [{"title": progress.title, "brief": progress.brief(), "text": progress.text()}] if progress else []
        return here + [described(state) for state in persons_runs(self.children())]

    def children(self):
        """The processes of the tasks started here (served as here, not as elsewhere): the last, and those it paused."""
        return {process.pid for process in self.followed}

    def tinker(self, arguments, timeout=120):
        """What one of tinker's quick commands prints: its columns as a chat reads them (" | "), its first 40
        lines."""
        done = subprocess.run(tinker_command() + arguments, cwd=self.root, text=True, capture_output=True,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1"), timeout=timeout)
        return first_lines(Markup.columns((done.stdout + done.stderr).strip()) or "(nothing)", 40)


def command_of(text, commands=None):
    """The command a message gives (one of the conversation's commands, sent as its first word, with or
    without a /), or "" when it is chat. A word alone is a command; one of ONE_WORD with one word after it
    is, and one of ANY_WORDS with any words after it ("models are getting better" is a chat)."""
    words = text.split()
    first = command_word(words[0]) if words else ""
    if first not in (commands or Desk.COMMANDS):
        return ""
    if len(words) == 1 or first in ANY_WORDS or (first in ONE_WORD and len(words) == 2):
        return first
    return ""


def command_words(text):
    """A command's words, as a shell splits them (as they come, when it cannot), the command itself lowercase
    and without its /."""
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    words[0] = command_word(words[0])
    return words


def command_word(word):
    """A command's name as it is sent: lowercase, and as a chat app's menu sends it too (/use_model, and
    /use_model@bot in a group) the name it stands for (use-model)."""
    return word.lower().lstrip("/").partition("@")[0].replace("_", "-")


def offered():
    """(name, what it does) of each command a chat takes, as its menu lists them."""
    return [(c.name, c.what) for c in COMMANDS if c.chat]


def after_word(message):
    """What a command word is followed by (the command described)."""
    return Message(message.chat, message.text.split(None, 1)[1], message.sender)


def app_request(given):
    """(the project named, the arguments, the input after <<< or None) of run [@PROJECT] ARGS... [<<< INPUT]."""
    words, _, fed = given.partition("<<<")
    stdin = fed.strip() + "\n" if "<<<" in given else None
    try:
        words = shlex.split(words)
    except ValueError:
        words = words.split()
    name, args = refs(words)
    return name, args, stdin


def welcome():
    """The welcome for a person who just opened the bot (/start): what Tinker is, and what to send, grouped by
    what a beginner does, in plain words."""
    return WELCOME


# === what a chat asks for: a chat, a command line, a research ====================================================

class Talk:
    """A normal chat with the model in use: a message that is not a command is answered by it, with that
    chat's last CHAT_KEPT messages (and what is running now) as context. It does not think, so a reply comes
    quickly, and its settings are a run's own (num_ctx), so a task and a chat share the loaded model: warm
    loads it as listening starts, and neither the first reply nor the next task waits for it to load.
    A job that runs (here or elsewhere) is paused while the reply is written (pausing: jobs.paused_for_you), and a
    note says so meanwhile; the job goes on once the reply is written. Chats take turns."""
    SYSTEM = ("You are Tinker, a friendly helper that builds small programs on this computer with a local model. "
              "You are chatting over {kind}, read on a phone: answer briefly, in short lines. Format for "
              "meaning, in Markdown: **bold** for what matters most, *italic* for an aside, `code` for "
              "commands, names and paths, \"- \" for items; an emoji only where it says something with less "
              "(✅ done or fine, ⚠️ a problem, 💡 a tip, 👉 what to do next); never a heading or a table. Talk "
              "about anything. You cannot run anything yourself from the chat; these messages do:\n{commands}")

    def __init__(self, commands, pausing=contextlib.nullcontext):
        self.commands, self.said, self.lock, self.turn = commands, {}, threading.Lock(), threading.Lock()
        self.pausing = pausing  # while a reply is written: what runs is paused for the person

    def answer(self, channel, message, now, waiting=lambda: "", write=None, on_shown=None):
        """The reply to message, sent when written (in a thread, so `stop` is heard meanwhile). now(): what
        runs now, [{title, brief, text}]; waiting(): a spec that waits for the person; write: what writes the
        reply (channel, message, context, tokens -> text), the chat's own by default; on_shown(ids): the
        ids of the reply's message, once it is written. When something runs, or the reply is not
        written in BUSY_AFTER seconds, a note says so at once (what is paused for it), updated every
        BUSY_EDIT_EVERY seconds; the reply then takes its place."""
        threading.Thread(target=self.answering, args=(channel, message, now, waiting, write or self.reply,
                                                      on_shown), daemon=True).start()

    def answering(self, channel, message, now, waiting, write_reply, on_shown):
        running, written, reply = now(), threading.Event(), []
        asked = (channel, message, self.context(running, waiting), CHAT_REPLY_TOKENS)
        threading.Thread(target=self.write, args=(write_reply, asked, reply, written), daemon=True).start()
        note = self.busy_until(written, running, now, channel, message.chat)
        note = channel.show(message.chat, note, reply[0] if reply else "⚠️ **The model did not answer.**", done=True)
        if on_shown:
            on_shown(note)

    @staticmethod
    def context(running, waiting):
        """What goes on, for the model to know: what runs, and a spec that waits for the person."""
        context = ["Running now:\n" + "\n\n".join(r["text"] for r in running)] if running else []
        context += [f"A spec waits for the person (confirm builds it, amend WHAT changes it):\n{waiting()}"
                    if waiting() else ""]
        return "\n\n".join(c for c in context if c)

    def write(self, write_reply, asked, reply, written):
        """The reply written (chats take turns, what runs paused first) into reply; written is set once it is done,
        or failed."""
        try:
            with self.turn, self.pausing():
                reply.append(write_reply(*asked))
        finally:
            written.set()

    def busy_until(self, written, running, now, channel, chat):
        """While something runs, or the reply is slow, a note that says so, kept up to date until the reply
        is written: the note's ids (None when none was needed)."""
        note = None
        if running or not written.wait(BUSY_AFTER):
            note = channel.show(chat, None, self.busy_note(running))
            while not written.wait(BUSY_EDIT_EVERY):
                note = channel.show(chat, note, self.busy_note(now()))
        return note

    @staticmethod
    def busy_note(running):
        if not running:
            lines = ["⏳ **The model is busy or loading.** I'll answer here as soon as it is free."]
        else:
            lines = ["⏸ **Pausing** " + "; ".join(r["brief"] for r in running) + " to answer you.",
                     "It goes on once you have your answer."]
        return "\n".join(lines)

    def reply(self, channel, message, now="", tokens=CHAT_REPLY_TOKENS):
        """The model's reply to message; now: what is going on (what runs, a spec that waits)."""
        found = self.model()
        if not found:
            return ("⚠️ **No model is in use yet**, so I cannot chat: `tinker calibrate NAME`, then "
                    f"`tinker use-model NAME`. These still work:\n{self.commands}")
        said = self.chat_of(channel, message.chat)
        asked = self.remembered(said, "user", message.text)
        system = self.SYSTEM.format(kind=channel.KIND.title(), commands=self.commands)
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
        """The chat's last CHAT_KEPT messages, with this one."""
        with self.lock:
            said.append({"role": role, "content": text})
            del said[:-CHAT_KEPT]
            return list(said)

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
                say.warn(f"The model did not load for chat: {first_words(exc)}")


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
        """Have the model name the command (the reply comes as a chat's: what runs paused, busy note and all)."""
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
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")
        line, does = str(data.get("command") or "").strip(), str(data.get("does") or "").strip()
        if not line or "\n" in line:
            self.proposed.pop(key, None)
            return md(t"🤷 **No command found:** {does or 'describe it another way'}")
        self.proposed[key] = {"line": line, "asked": message.text, "shown": None}
        return self.proposal(line, does)

    def proposal(self, line, does):
        warning = ("\n⚠️ **It changes or deletes things:** read it before you confirm."
                   if self.CHANGES_RE.search(line) else "")
        return md(t"🖥 **Run this?**\n{line:block}\n{does}{warning:md}\n"
                  t"👉 `confirm` to run it · anything else cancels it")

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
        line = proposed["line"]
        sent = channel.show(chat, proposed["shown"], f"▶️ **Running** `{line}` …")
        code, output = in_shell(self.shell, line, self.folder)
        head = md(t"{'✅' if code == 0 else '❌'} {line:code} · _exit {code}_")
        folded = self.folded(output)
        tells = bool(output) and bool(self.talk.model())
        sent = channel.show(chat, sent, f"{head}\n{folded}", done=not tells)
        if not tells:
            return
        with self.talk.pausing():  # the model reads the output: what runs is paused for the person again
            told = self.told(channel, chat, proposed["asked"], line, code, output)
        channel.show(chat, sent, f"{head}\n{told}\n{folded}" if told else f"{head}\n{folded}", done=True)

    @staticmethod
    def folded(output):
        """The output, folded (its end, when long), its columns as a chat reads them."""
        if not output:
            return "_(no output)_"
        shown = output if len(output) <= COMMAND_SHOWN else "… (the start is left out)\n" + output[-COMMAND_SHOWN:]
        return f"```output\n{Markup.fenced(Markup.columns(shown))}\n```"

    def told(self, channel, chat, asked, line, code, output):
        """What the output says, as the model tells it ("" when it does not answer)."""
        try:
            return self.talk.ask(channel, chat, [{"role": "system", "content": self.TELL_SYSTEM},
                                                 {"role": "user", "content": f"They asked: {asked}\nCommand: {line}\n"
                                                  f"Exit code: {code}\nOutput:\n{output[-COMMAND_READ:]}"}],
                                 CHAT_REPLY_TOKENS)
        except OllamaError:
            return ""


def in_shell(shell, line, folder, given=None):
    """(exit code, or why it has none; output) of a command line run in the person's login shell, in folder,
    with given as its input (none: no input), for up to COMMAND_TIMEOUT seconds."""
    try:
        done = subprocess.run([shell, "-lc", line], cwd=folder, text=True, capture_output=True, input=given,
                              stdin=None if given else subprocess.DEVNULL, timeout=COMMAND_TIMEOUT)
        return done.returncode, (done.stdout + done.stderr).strip()
    except subprocess.TimeoutExpired as exc:
        return f"stopped after {COMMAND_TIMEOUT}s", (text_of(exc.stdout) + text_of(exc.stderr)).strip()
    except OSError as exc:
        return "not started", str(exc)


class Research:
    """research TOPIC: an answer from the web, with its sources. One quick call names the searches (up to
    RESEARCH_SEARCHES), the pages they find are read in turn (the first of each search, then the second
    ..., up to RESEARCH_PAGES; of each, the parts most about the topic), and one call writes the answer
    from them alone, each claim citing its page. One message says how far it got, and becomes the answer; its
    tracker says the same, for a chat it runs for. (tinker learn researches a topic in depth, and keeps it for the
    builds.)"""
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

    def __init__(self, talk, tracker=None):
        self.talk, self.tracker = talk, tracker

    def researching(self, channel, chat, topic, now):
        """The research, its message kept up to date: (how it ended, "done" or "blocked", and what it ends with)."""
        head = md(t"🔎 **{topic}**")
        self.on("searches")
        sent = channel.show(chat, None, f"{head}\n⏳ _working out what to search_")
        internet = Internet()
        unusable = self.unusable(internet)
        if unusable:
            return self.ended(channel, chat, sent, "blocked", f"{head}\n{unusable}")
        busy = "; ".join(r["brief"] for r in now())
        waits = f"\n⏳ _the model is busy with_ {busy}_: it answers after the current step_" if busy else ""
        try:
            searches = self.searches(channel, chat, topic)
            searching = " · ".join(f"`{q}`" for q in searches)
            self.on("read")
            sent = channel.show(chat, sent, f"{head}\n🌐 _searching:_ {searching}{waits}")
            pages = self.read(internet, searches)
            if not pages:
                return self.ended(channel, chat, sent, "blocked",
                                  md(t"{head:md}\n🤷 **Nothing found on the web** for it: {internet.why()}"))
            self.on("write")
            sent = channel.show(chat, sent, f"{head}\n📖 _read {len(pages)} pages; writing the answer_" + waits)
            told = self.answer_from(channel, chat, topic, pages)
        except OllamaError as exc:
            return self.ended(channel, chat, sent, "blocked",
                              md(t"{head:md}\n⚠️ **The model did not answer:** {first_words(exc)}"))
        return self.ended(channel, chat, sent, "done", f"{head}\n{told}\n\n📚 **Sources**\n{self.sources(pages)}")

    def on(self, phase):
        if self.tracker is not None:
            self.tracker.phase(phase)

    @staticmethod
    def ended(channel, chat, sent, status, text):
        channel.show(chat, sent, text, done=True)
        return status, text

    def unusable(self, internet):
        """Why a research cannot be made ("" when it can): the web, or the model, not there."""
        if not internet.available():
            return md(t"⚠️ **The web is not usable:** {internet.why()}")
        if not self.talk.model():
            return "⚠️ **No model is in use yet:** `tinker use-model NAME`"
        return ""

    def searches(self, channel, chat, topic):
        """The web searches the model names for the topic (the topic itself when it names none)."""
        data = json_in(self.talk.ask(channel, chat, [
            {"role": "system", "content": self.SEARCHES_SYSTEM.format(today=today().strftime("%d %B %Y"),
                                                                    year=today().strftime("%Y"))},
            {"role": "user", "content": topic}], COMMAND_TOKENS))
        found = [str(q).strip() for q in data.get("searches") or [] if str(q).strip()]
        return list(dict.fromkeys(found))[:RESEARCH_SEARCHES] or [topic]

    def read(self, internet, searches):
        """[{title, url, content}] of the pages the searches find, in turn (a page that cannot be read
        gives the search's own excerpt), each cut to the parts most about what was searched."""
        found = [internet.search(query, RESEARCH_PAGES) or [] for query in searches]
        pages, wanted = [], set(re.findall(r"[a-z0-9]{3,}", " ".join(searches).lower()))
        for result in self.in_turn(found)[:RESEARCH_PAGES]:
            page = internet.fetch(result["url"]) or result
            content = self.most_about(page.get("content") or result.get("content", ""), wanted)
            if content.strip():
                pages.append({"title": page.get("title") or result["title"], "url": result["url"], "content": content})
        return pages

    @staticmethod
    def in_turn(found):
        """The results of the searches taken in turn: the first of each, then the second ..., each page once."""
        order, seen = [], set()
        for rank in range(RESEARCH_PAGES):
            for results in found:
                if rank < len(results) and results[rank]["url"] not in seen:
                    seen.add(results[rank]["url"])
                    order.append(results[rank])
        return order

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

    def answer_from(self, channel, chat, topic, pages):
        """The model's answer from the pages alone, each claim citing its page."""
        return self.talk.ask(channel, chat, [
            {"role": "system", "content": self.ANSWER_SYSTEM.format(today=today().strftime("%d %B %Y"))},
            {"role": "user", "content": f"The person asks: {topic}\n\n" + "\n\n".join(
                f"[{n}] {page['title']} ({page['url']})\n{page['content']}" for n, page in enumerate(pages, 1))}],
            CHAT_REPLY_TOKENS)

    @staticmethod
    def sources(pages):
        return "\n".join(f"[{n}] [{page['title'][:70] or page['url']}]({page['url']})" for n, page in enumerate(pages, 1))


# === a task's one message ========================================================================================

class Reporter:
    """A run's tracker shown on the channels: the task's one message per chat (sent: kept from the task's
    run before). It reads the tracker of the run's process (pid) every EDIT_EVERY seconds and edits the
    message when it changed (its clock moves on every CLOCK_EVERY), and its ending is edited into it when
    the run ends. A command that keeps no tracker (learn, calibrate) shows its latest printed line."""

    def __init__(self, channels, title, pid=None, sent=None, phase=Tracker.STARTING, keep="", before=0.0,
                 on_step=None):
        self.channels, self.progress, self.pid = channels, Progress(title, phase, before), pid
        self.on_step = on_step  # told when the message changes (Tinker's status follows it)
        self.keep = keep  # what stays at the top of the task's message (its confirmed plan), above its progress
        self.sent, self.last_edit, self.lock = ({} if sent is None else sent), 0.0, threading.Lock()
        self.ended = False
        self.fresh = True  # its first update: the task (re)started from the person's message, below which it shows
        threading.Thread(target=self.keep_up, daemon=True).start()

    def keep_up(self):
        while not self.ended:
            time.sleep(EDIT_EVERY)
            self.refresh()

    def refresh(self):
        """The message edited when the tracker changed, or its clock is due to move on."""
        with self.lock:
            if self.ended:
                return
            state = self.tracked()
            went_on = ((state or {}).get("resumed") or 0) > (self.progress.snapshot.get("resumed") or 0)
            self.fresh = self.fresh or went_on  # it went on by itself: shown at the bottom, where the person looks
            changed = self.progress.take(state)
            if changed or went_on or time.monotonic() - self.last_edit >= CLOCK_EVERY:
                self.update()

    def tracked(self):
        """The run's tracker as it is now (None when it keeps none, or not yet)."""
        return Home.store().get("running", str(self.pid)) if self.pid else None

    def update(self):
        self.last_edit = time.monotonic()
        self.say(self.progress.text())
        if self.on_step:
            self.on_step()

    def say(self, text, done=False):
        """text shown in the task's message (Markup), under what it keeps; done: its last update. Its first
        update and its last come to the bottom of the chat (the task (re)started, or done: look here)."""
        text = f"{self.keep}\n\n{text}" if self.keep else text
        bottom, self.fresh = done or self.fresh, False
        for channel in self.channels:
            for chat in channel.allowed:
                key = (channel.name, chat)
                self.sent[key] = channel.show(chat, self.sent.get(key), text, done, bottom)

    def printed(self, line):
        """A line a command printed: shown as its latest, when the command keeps no tracker."""
        if self.tracked() is None and line.strip() and not line.lstrip().startswith("..."):
            with self.lock:
                self.progress.snapshot["latest"] = line.strip()[:300]

    def finish(self, snapshot=None):
        """The run ended: its last tracker (given, or read now) shown, with its ending unless quiet."""
        with self.lock:
            self.ended = True
            self.progress.take(snapshot or self.tracked())
            if self.progress.waiting() or self.progress.snapshot.get("ended"):
                self.say(self.progress.ending(), done=True)
            else:
                self.update()


class Progress:
    """A run's tracker (its snapshot), shown short for a chat: one message kept up to date (the steps
    done, the one it is on with its clock, what the model is writing right now, how many spec points
    pass, the latest line) and, when the run ends, its ending: how it ended and how to go on, or the
    plan that waits for the person. It only shows the tracker's data: it never reads a run's printed
    text for meaning."""
    SAID = ("✅", "✏️", "📁")  # a phase Tinker says itself (Markup), not a step of the run's
    CHANGE_SIGNS = {"+": "➕", "~": "🔄"}

    def __init__(self, title, phase=Tracker.STARTING, before=0.0):
        self.snapshot = {"title": title, "doing": phase, "step": None, "began": None, "steps": [], "passing": None,
                         "live": "", "latest": "", "waiting": None, "ended": None, "before": before, "seconds": 0.0}

    def take(self, snapshot):
        """The tracker as it is now (the phase Tinker said stays until the run is on a step); True when that
        changed what the message says."""
        before = self.text()
        said = self.snapshot["doing"] if self.snapshot.get("step") is None else None
        self.snapshot = dict(self.snapshot, **{k: v for k, v in (snapshot or {}).items() if k != "before"})
        if said and self.snapshot.get("step") is None:
            self.snapshot["doing"] = said
        return self.text() != before

    def text(self):
        state = self.snapshot
        began = state.get("began")
        clock = f" · ⏱ {human_duration(time.time() - began)}" if began else ""
        doing = state["doing"] if state["doing"].startswith(self.SAID) else md(t"**{state['doing']}**")
        lines = [md(t"🔨 **{state['title']}**")] + [f"✅ {step}" for step in state.get("steps", [])]
        lines += [f"▸ {doing}{clock}"]
        lines += [md(t"⏳ _{state['live']}_")] if state.get("live") else []
        lines += [md(t"📊 {self.passing}")] if self.passing else []
        lines += [md(t"… {state['latest']}")] if state.get("latest") else []
        return "\n".join(lines)

    @property
    def title(self):
        return self.snapshot["title"]

    @property
    def passing(self):
        counts = self.snapshot.get("passing")
        return f"{counts[0]}/{counts[1]} spec points pass" if counts else ""

    def brief(self):
        """Markup: the title, with the phase and the passing points."""
        passing = f", {self.passing}" if self.passing else ""
        return md(t"**{self.title}** ({Markup.plain(self.snapshot['doing']):md}{passing})")

    def waiting(self):
        """The run ended with its plan waiting for the person to confirm or amend it."""
        return bool(self.snapshot.get("waiting"))

    def ending(self):
        """The ending for the chat (Markup): the plan that waits, or how the run ended and how to go on."""
        if self.waiting():
            return self.plan(self.snapshot["waiting"]) + f"\n⏱ _so far: {self.took()}_"
        ended = self.snapshot.get("ended")
        if not ended:
            return md(t"🏁 **{self.title}**")
        return "\n".join([md(t"🏁 **{self.title}**")] + self.ended_lines(ended))

    def ended_lines(self, ended):
        """How the run ended, how long it took, what it made and how to go on."""
        mark = {"done": "✅", "blocked": "⚠️"}.get(ended.get("status"), "⏸")
        took = f"⏱ **Done in {self.took()}**" if ended.get("status") == "done" else f"⏱ _so far: {self.took()}_"
        lines = ([md(t"{mark} **{ended['headline']}**")] if ended.get("headline") else []) + [took]
        lines += [self.detail(label, value) for label, value in ended.get("details", [])]
        lines += [" · ".join(f"{label}: `{command}`" for label, command in ended.get("next", []))] \
            if ended.get("next") else []
        return lines + ([ended["text"]] if ended.get("text") else [])

    @staticmethod
    def detail(label, value):
        """One detail of how the run ended: the command that runs it as code."""
        if label == "Run it":
            return md(t"▶️ {label}: {value:code}")
        return md(t"• {label}: {value}")

    def took(self):
        """How long the task has taken, in its runs (the time a plan waits for the person left out)."""
        seconds = self.snapshot.get("before", 0.0) + self.snapshot.get("seconds", 0.0)
        return f"{human_duration(seconds)} ({round(seconds)} seconds)"

    @staticmethod
    def plan(plan):
        """A plan that waits for the person (Markup), from the tracker's data."""
        heading = "🐞 **Bug shown for" if plan.get("kind") == "fix" else "📋 **Plan for"
        start = f" · ▶️ `{plan['start']}`" if plan.get("start") else ""
        lines = [md(t"{heading:md} {plan.get('name', '')}** · run {plan.get('run', ''):code}"),
                 md(t"🧰 {plan.get('language') or '?'}{start:md}")]
        if plan.get("changes"):
            lines += ["", "✏️ **What changed**"] + [Progress.change_line(change) for change in plan["changes"]]
        lines += ["", "🧪 **Tested before any code is written**"]
        lines += [md(t"**{criterion_id}** {text}") for criterion_id, text in plan.get("criteria", [])]
        if plan.get("non_goals"):
            lines += ["", md(t"🚫 _Not included:_ {'; '.join(plan['non_goals'])}")]
        if plan.get("folder"):
            lines += [f"📁 `{Path(plan['folder']).name}`"]
        return "\n".join(lines + ["", "👉 `confirm` to build it · `amend WHAT` to change it"])

    @staticmethod
    def change_line(change):
        """A change to the plan ("+ added", "- dropped", "~ changed"), with its sign."""
        if change.startswith("- "):
            return md(t"➖ ~~{change[2:]}~~")
        return md(t"{Progress.CHANGE_SIGNS.get(change[:1], '•')} {change[2:]}")


# === tasks run from a terminal ===================================================================================

class Attached:
    """A task run from a terminal, reporting to the channels: its tracker (which the run keeps in Tinker's
    home) is shown in its progress message, and the conversation (which reads the chat) answers status from
    it. That stops it with STOP, as Ctrl-C does (saved); a job that is paused is stopped the same way."""

    def __init__(self, channels, title, tracker):
        self.tracker = tracker
        self.reporter = Reporter(channels, title, pid=os.getpid())

    def start(self):
        signal.signal(STOP, self.stop)
        self.tracker.change("attached", reports=True)  # a conversation leaves its message to it
        with self.reporter.lock:
            self.reporter.progress.take(self.tracker.state)
            self.reporter.update()
        return self

    def stop(self, signum, frame):
        raise KeyboardInterrupt

    def end(self):
        """Stop reporting: its last state shown, and its tracker taken out of the home."""
        self.reporter.finish(self.tracker.state)
        self.tracker.close()


def attach(title, tracker):
    """The enabled channels the run about to start reports to (None when there are none). A channel that
    does not work is said once and left out: a run never fails for its channel."""
    try:
        channels = chat_channels()
    except SystemExit as exc:
        say.warn(f"Channels left out: {exc}")
        return None
    return Attached(channels, title, tracker).start() if channels else None


def chat_channels():
    """The enabled chat channels (the channels plugin's), made: none without the plugin, or when a run is told
    not to report (one the conversation runs reports through it)."""
    try:
        from .channels import Channels
    except ImportError:
        return []
    return Channels.enabled()


def persons_runs(skip=()):
    """The runs the person started (running), but an exercise's (its builds; the exercise itself is a job)."""
    return [state for state in running(skip) if not state.get("exercise")]


def exercising_now():
    """The exercise that runs, as the conversation shows it (described), or None."""
    pid = (Home.store().get("activity", "exercise") or {}).get("pid")
    state = next((state for state in running() if state["pid"] == pid), None) if pid else None
    return described(state) if state else None


def running(skip=()):
    """[a run's tracker, with its pid] of the runs in Tinker's home ("running"), but for this process and the
    processes skipped. The tracker of a process that ended is removed."""
    found = []
    for key, state in Home.store().items("running").items():
        pid = int(key) if key.isdigit() else 0
        if pid <= 0 or pid == os.getpid() or pid in skip:
            continue
        if not alive(pid):
            Home.store().delete("running", key)
        elif isinstance(state, dict):
            found.append(dict(state, pid=pid))
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def exercise_switch(text):
    """True for exercise LANGUAGE: shown in the exercise's message alone."""
    words = text.split()
    return len(words) > 1 and words[0].lstrip("/").lower() == "exercise" and words[1].lower() != "status"


def exercise_board():
    """The exercise's message ids, as a Reporter keeps them ({(channel, chat): ids})."""
    kept = Home.store().get("activity", "exercise message") or {}
    return {tuple(key.split("\t", 1)): ids for key, ids in kept.items()}


def keep_exercise_board(sent):
    Home.store().put("activity", "exercise message", {f"{name}\t{chat}": ids for (name, chat), ids in sent.items()})


def printed_since(path, start):
    """The last lines written to a file after offset start."""
    with open(path, encoding="utf-8", errors="replace") as file:
        file.seek(start)
        return "\n".join(file.read().strip().splitlines()[-10:])


def until_ended(pid):
    """Back once the process (not this one's child) has ended."""
    while alive(pid):
        time.sleep(EDIT_EVERY)


def described(state):
    """{title, brief, text, pid, reports} of a run's tracker, as the conversation shows it (reports: the run
    reports to the chats by itself)."""
    progress = Progress(state.get("title") or "a task")
    progress.take(state)
    return {"title": progress.title, "brief": progress.brief(), "text": progress.text(), "pid": state.get("pid"),
            "reports": bool(state.get("reports"))}


def signal_task(pid, signum):
    """Send a task run from a terminal a signal (STOP)."""
    try:
        os.kill(pid, signum)
    except ProcessLookupError, PermissionError:
        pass


# === how a message is written, and the channels it goes through ==================================================

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

    def send_file(self, chat, path, caption):
        """Send a file with caption (Markup) under it; a channel that cannot sends the caption and where the file is."""
        return self.send(chat, f"{caption}\n📄 `{path}`")

    def typing(self, chat):
        """Show chat that a reply is being written (a channel that cannot says nothing)."""

    def pin(self, chat, sent):
        """Pin Tinker's status, the chat's one pin (a channel that cannot pins nothing)."""

    def lasting(self, chat, sent):
        """The messages sent (Tinker's status) stay in the chat (a channel that keeps no ledger keeps all)."""

    def glance(self, chat, asked, sent, keep=None):
        """The messages sent only show something the person's message asked (its id) for: both go soon (the
        messages in keep seconds, when given)."""

    def collect(self):
        """Take out of the chats the messages whose time is up."""

    def clear(self):
        """Take every message out of the chats but Tinker's status; how many."""
        return 0

    def delete(self, chat, sent):
        """Take a message of Tinker's out of the chat (a channel that cannot leaves it)."""

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

    def said(self, text):
        """text as this interface takes commands: in a conversation without "tinker" before them."""
        return BARE_RE.sub("", text) if self.bare else text

    def skip_waiting(self):
        """Forget the messages sent while nothing listened: a command sent hours ago is not run now."""

    def offer(self, commands):
        """The commands, (name, what it does) each, listed where the person types them (a chat app's menu)."""

    def listened(self, timeout):
        """The new messages from the chats this channel serves; the rest are ignored. A refusal (another
        program reading this bot's messages, a revoked token) is said once and waited out."""
        try:
            messages = self.receive(timeout)
        except ChannelError as exc:
            if str(exc) != getattr(self, "refused", None):
                say.warn(f"{self.name}: {exc}; trying again")
            self.refused = str(exc)
            time.sleep(timeout)
            return []
        self.refused = None
        return [m for m in messages if m.chat in self.allowed]

    def claim(self, holder):
        """Become the one Tinker listening on this channel: True once it is, False when another one
        listens. Held until this process ends, even when it crashes. holder: what this one is (its
        process), kept in the lock for the next one to read."""
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
        except OSError, ValueError:
            return {}
        return holder if isinstance(holder, dict) else {}

    def lock_file(self):
        return Home.lock_file(self.name)


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
            except EOFError, KeyboardInterrupt:
                self.lines.put(None)
                return

    def send(self, chat, text):
        self.print(Markup.ansi(self.said(text)))
        return self.new_id()

    def edit(self, chat, sent, text):
        self.print(Markup.ansi(self.said(text)))
        return sent

    def send_file(self, chat, path, caption):
        """The file opened in the browser (a terminal is on this computer), and where it is."""
        webbrowser.open(Path(path).as_uri())
        return self.send(chat, f"{caption}\n📄 Opened in your browser: `{path}`")

    def show(self, chat, shown, text, done=False, bottom=None, pin=False):  # a terminal prints at its bottom
        sent, text = (shown or [None])[0] or self.new_id(), self.said(text)
        if done:
            self.show_whole(sent, text)
        else:
            self.show_headline(sent, text)
        return [sent]

    def show_whole(self, sent, text):
        """The thing whole, or only its new lines when it grew by lines at its end since it was shown."""
        self.headlines.pop(sent, None)
        before, self.whole[sent] = self.whole.get(sent), text
        grew = before is not None and text.startswith(before + "\n")
        self.print(Markup.ansi(text[len(before) + 1:].lstrip("\n") if grew else text))

    def show_headline(self, sent, text):
        """A dim line, when the thing's headline (its title, and what it does now) changed."""
        headline = self.headline(text)
        if self.headlines.get(sent) != headline:
            self.headlines[sent] = headline
            self.print(f"\033[2m⋯ {headline}\033[22m" if sys.stdout.isatty() and not os.environ.get("NO_COLOR")
                       else f"⋯ {headline}")

    @staticmethod
    def headline(text):
        lines = [line.strip() for line in Markup.plain(text).splitlines() if line.strip()]
        doing = next((line for line in lines if line.startswith("▸")), lines[1] if len(lines) > 1 else "")
        return " · ".join(line for line in (lines[:1] + [doing.split(" · ⏱")[0]]) if line)

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

    def new_id(self):
        with self.printing:
            self.next_id += 1
            return self.next_id


class Message:
    """One message to Tinker: who sent it (the chat to answer), what it says, and its id in the chat."""

    def __init__(self, chat, text, sender="", id=None):
        self.chat, self.text, self.sender, self.id = str(chat), (text or "").strip(), sender, id


def pieces(text, size=3500):
    """text in parts a chat message holds, split between lines (a line longer than a part, across parts)."""
    parts, part = [], ""
    for whole in text.splitlines():
        for line in [whole[i:i + size] for i in range(0, len(whole), size)] or [""]:
            if part and len(part) + len(line) + 1 > size:
                parts.append(part)
                part = ""
            part = f"{part}\n{line}" if part else line
    return parts + [part] if part else parts


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

    @classmethod
    def of(cls, template):
        """A message from a t-string, each value as its format says: none, shown as it is (its marks escaped);
        :md, Markup already; :code, a code span; :block, a fenced block."""
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
        """text shown as it is: its marks escaped."""
        return re.sub(r"([*_`~\\])", r"\\\1", str(text))

    @staticmethod
    def fenced(text):
        """text safe inside a block: a fence in it cannot end the block."""
        return str(text).replace("```", "ˋˋˋ")

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
        cuts = Markup.column_cuts(rows) if len(rows) >= 2 else []
        if len(cuts) < 2:
            return str(text)
        return "\n".join(Markup.in_columns(line, cuts) if len(line.split()) >= 3 else line for line in lines)

    @staticmethod
    def column_cuts(rows):
        """Where the columns of these rows begin, as the row of fewest words shapes them."""
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
        return cuts

    @staticmethod
    def in_columns(line, cuts):
        """The line's columns joined by " | " (a value running into a gap is cut after it)."""
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

    @staticmethod
    def keeper():
        kept = []

        def keep(shown):
            kept.append(shown)
            return f"\x00{len(kept) - 1}\x00"
        keep.kept = kept
        return keep


md = Markup.of


# === small helpers ===============================================================================================

def help_text(words=()):
    """tinker help (of one command: words), as a conversation shows it: tinker's own (Markup)."""
    try:
        done = subprocess.run(tinker_command() + ["help", *words], text=True, capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1", TINKER_FORMAT="markup"))
    except OSError, subprocess.SubprocessError:
        return "❓ `help` lists what you can send"
    shown = (done.stdout + done.stderr).strip()
    return shown if not words else md(t"❓ **help {' '.join(words)}**\n{shown:block}")


def history_text(words=()):
    """history [PAGE | #N] (Markup): the things shown in the chats, newest first, or the one numbered N as it was
    shown last, with what the person asked for it."""
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
        lines.append(md(t"`#{thing['number']}` {thing['time'][5:]} · {headline(thing['text'])}") + asked)
    older = f" · `history {page + 1}` older" if page < pages else ""
    return "\n".join(lines + [f"👉 `history #N` shows one again{older}"])


def history_thing(word):
    """history #N (Markup): the thing as it was shown last, with what the person asked for it."""
    thing = Ledger.thing(word[1:]) if word[1:].isdigit() else None
    if thing is None:
        return md(t"🤷 **No {word} in the history:** `history` lists what it keeps")
    asked = md(t"💬 _you:_ {thing['asked']}\n") if thing.get("asked") else ""
    return md(t"📜 **#{thing['number']}** · {thing['time']}\n") + asked + "\n" + thing["text"]


def headline(text):
    """A message's first line that says something, plain and short."""
    lines = [line for line in Markup.plain(text).splitlines() if line.strip()]
    return first_words(lines[0] if lines else "", 60)


def commands_brief():
    """The commands, as the chat's prompt names them: their synopses alone (the whole help would slow every
    reply)."""
    return "; ".join(re.findall(r"- `([^`]+)`", help_text())) or "help"


def tinker_command():
    """How to start Tinker again: the script this one is."""
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


def tinker_process(arguments, folder=None, output=None, **env):
    """A tinker of its own, started in the background (its own process group: a signal to it reaches what it runs),
    printing to output (else where this one prints), with env added to this one's."""
    return subprocess.Popen(tinker_command() + arguments, cwd=folder, env=dict(os.environ, PYTHONUNBUFFERED="1", **env),
                            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT if output else None,
                            start_new_session=True)


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


def built_project(name=""):
    """The project named (@ID or @folder, as Runs.find takes it), or without a name the one built last, from
    the projects Tinker points to ({id, workdir, task, status ...}). None when there is none."""
    projects = [r for r in Runs.entries() if Path(str(r.get("workdir", ""))).is_dir()]
    if name:
        try:
            found = Runs.find(name)
        except SystemExit:
            return None
        return next((r for r in projects if r.get("id") == found.get("id")), None)
    done = [r for r in projects if r.get("status") == "done"]
    return max(done, key=lambda r: str(r.get("updated", "")), default=None)


def first_lines(text, count):
    lines = text.splitlines()
    return "\n".join(lines[:count] + ([f"… {len(lines) - count} more lines"] if len(lines) > count else []))
