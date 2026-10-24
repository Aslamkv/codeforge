"""
🔧 Tinker — your friendly project builder

Tell Tinker what you want. Tinker plans it, builds it, tests it,
and learns something new every time.

  🧠 Plans first      Every detail is thought through before any work starts
  🧱 Builds small     Simple pieces that work well together
  ✅ Tests the goal   Checks the result the way a person would use it
  📒 Remembers        Each run makes the next one faster
  🔁 Any model        Switch the AI whenever you like

Part of CodeForge 🛠️ — built by the community, for the community.
Free to copy, change, and share. 🌻

"""

import re
import sys

lazy import argparse
lazy import datetime
lazy import os
lazy import secrets
lazy import shutil
lazy import time
lazy import traceback
lazy import webbrowser
lazy from pathlib import Path

from . import conversation, exercising, reading
from .commands import BY_NAME, COMMANDS, GROUPS, refs
from .jobs import STATES, Jobs, archived, job_number
from .messages import Messages, human_duration, say
from .reading import Reader
lazy from .building import BugReproducer, CodeWriter, TestToolSetup, TestWriter, Verifier
lazy from .finishing import FinalReview, LessonWriter, ReadmeWriter, Tidy
lazy from .graph import write_page
lazy from .knowledge import Embeddings, Library
lazy from .learning import LearnStopped, SlotLearner
lazy from .model import Calibrator, Model, Models, Ollama, OllamaError
lazy from .planning import Designer, InterfaceReviewer, Recaller, SpecWriter
lazy from .project import Brief, Outcome, Project, RunChecks, Runs, Tracker, project_store, saved
lazy from .prompts import Prompts
lazy from .session import Session
lazy from .teaching import Asker, Memory, Teacher
lazy from .settings import (
    CONFIRM_AFTER, Config, DEFAULT_MAX_STALL, ENV_EXAMPLE, ENV_FILE, FIX_PHASES, FORMAT_AFTER, Home, Log,
    PHASES, STATUS_TEXT, clock, first_line, now_text, projects_folder,
)
lazy from .web import Internet
lazy from .workspace import TaughtCommands, TestRef, Toolchain

try:  # chat channels are a plugin: without tinkerkit/channels.py, Tinker works as it did, in the terminal
    from . import channels
except ImportError:
    channels = None


def main():
    """Tinker's command line: tinker reset, a command that shows or changes its settings (then it
    exits), or a run: a new project, or a resumed one, or a feature or a bug fix for one. What fails
    for a reason that is not a bug (Ollama, a file, Ctrl-C) ends with what happened, not a traceback."""
    script_started, started_at = time.perf_counter(), datetime.datetime.now()
    try:
        argv = Jobs.arguments(sys.argv[1:])
        with Jobs.running(argv):
            run(Cli.parse(argv), script_started, started_at)
    except KeyboardInterrupt:  # Ctrl-C: what each part saves is saved as it goes
        raise SystemExit("\nInterrupted.")
    except OllamaError as exc:  # Ollama failed past every retry (a run handles it itself, and saves)
        raise SystemExit(f"\n{exc}")
    except OSError as exc:  # a file or folder that cannot be read or written (full disk, permissions)
        raise SystemExit(f"\n{exc.strerror or exc}: {exc.filename or ''}".rstrip(": "))


def run(args, script_started, started_at):
    """tinker reset, a command that shows or changes something (then it exits), or a run on a project."""
    if args.reset:
        return Home.reset(args.yes)
    Home.ensure()
    if Cli.works_for_hours(args):
        Home.keep_awake()
    if Commands(args).run():
        return
    ProjectRun(args, script_started, started_at).execute()


class Cli:
    """The command line: `tinker COMMAND ...`, e.g. `tinker task ...` builds something. Every command
    comes from the one table of commands (tinkerkit/commands.py): its arguments, its options and its
    help. Each command takes only its own arguments and options, every value is checked before
    anything starts, and what is wrong is an error that says what to change. A project is named only
    as @ID or @folder. The result has one shape for the rest of Tinker."""
    EXAMPLES = ("tinker", "tinker doctor", "tinker task a calculator in Python", "tinker add keep a history of results",
                "tinker research the newest Python release", "tinker help task")
    # the conversation's own (one_shot)
    TALK = tuple(command.name for command in COMMANDS if command.group == "Talk" and command.cli)
    TITLES = {"Build": "🔨 Build", "Learn": "🧠 Learn", "Jobs": "⏯ Jobs", "Set up": "🔧 Set up", "Talk": "💬 Talk",
              "Channels": "📡 Channels"}
    RUNS = tuple(command.name for command in COMMANDS if command.run and command.name != "confirm")
    COMMANDS = tuple(command.name for command in COMMANDS if command.cli and command.group != "Talk"
                     and command.name not in ("help", "channel", "confirm"))
    LEARNING = ("learn", "exercise", "read")  # the commands that load the model for long (but for teaching a command)
    FIELDS = {"reset": False, "config": None, "models": False, "use_model": None, "embed": None, "use_web": None,
              "calibrate": None, "learn": None, "ask": None, "teach": None, "read": None, "exercise": None, "memory": False, "runs": False, "status": None, "log": None, "jobs": None, "stop": None,
              "doctor": False, "task": None, "spec": None, "spec_file": None, "restart": False, "resume": None,
              "amend": None, "add": None, "fix": None, "workdir": None, "web": False, "confirm": False,
              "rename": None, "yes": False, "calls": False, "graph": False}
    FOLDER_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")

    @staticmethod
    def parse(argv=None):
        """The command line read and checked (a mistake exits with what to change)."""
        argv = sys.argv[1:] if argv is None else argv
        Cli.run_at_once(argv)
        if argv[0] == "confirm":  # a plan that waits, confirmed: its run resumed
            argv = ["resume"] + argv[1:]
        command = Cli.known_command(argv)
        parser = Cli.parser(command)
        given = parser.parse_args(argv[1:])
        args = argparse.Namespace(**Cli.FIELDS)
        for name, value in vars(given).items():
            setattr(args, name, value)
        args.command = command
        Cli.read_command(command, given, args, parser)
        if command in Cli.RUNS:
            Cli.check_run(args, parser)
        if command != "task":  # a new task without --workdir gets a folder of its own (ProjectFolder)
            args.workdir = args.workdir or "."
        return args

    @staticmethod
    def run_at_once(argv):
        """What runs as soon as it is read, and exits: the conversation (no command), help, one of the
        conversation's own commands said once, the chat channels (a plugin of its own) and tinker clear
        (no model, no project)."""
        if not argv:
            Home.ensure()
            conversation.session(Path.cwd())
            raise SystemExit(0)
        if argv[0] in ("help", "-h", "--help"):
            Cli.help(argv[1] if len(argv) > 1 else None)
        if argv[0] in Cli.TALK:
            if argv[1:2] in (["-h"], ["--help"]):
                Cli.help(argv[0])
            Home.ensure()
            raise SystemExit(conversation.one_shot(argv[0], " ".join(argv[1:])))
        if argv[0] == "channel":
            if channels is None:
                raise SystemExit("tinker: channels are not installed (tinkerkit/channels.py is missing)")
            channels.ChannelCommand.run(argv[1:])
            raise SystemExit(0)
        if argv[0] == "clear":
            given = Cli.parser("clear").parse_args(argv[1:])
            Cli.cleared(given.parts, given.yes)
            raise SystemExit(0)

    @staticmethod
    def cleared(parts, yes):
        """tinker clear PART... (Home's parts), or, alone, tinker clear read [#N...] (readings deleted) or tinker
        clear jobs [#N...] (jobs archived)."""
        if parts[0] not in ("read", "jobs"):
            return Home.clear(parts, yes)
        numbers = [job_number(word) for word in parts[1:]]
        if None in numbers and parts[0] == "jobs":
            raise SystemExit("tinker clear jobs takes job numbers alone: tinker clear jobs #N (tinker jobs)")
        if None in numbers:
            raise SystemExit("tinker clear read takes reading numbers alone: tinker clear read #N (tinker read list)")
        if parts[0] == "jobs":
            return archived(numbers, yes)
        reading.forgotten(numbers, yes)

    @staticmethod
    def known_command(argv):
        """The command, one of tinker's (the commands were options once: --task is said to be task)."""
        old = next((word for word in argv if word.startswith("--") and word[2:] in Cli.COMMANDS), None)
        if old:
            raise SystemExit(f"tinker: commands are written without --: tinker {old[2:]} (tinker help lists them)")
        if argv[0] not in Cli.COMMANDS:
            raise SystemExit(f"tinker: no command {argv[0]!r} (tinker help lists them)")
        return argv[0]

    @staticmethod
    def help(command=None):
        """tinker help: every command; tinker help COMMAND: that command's arguments and options."""
        if command is None:
            say.text(Cli.overview_markup() if os.environ.get("TINKER_FORMAT") == "markup" else Cli.overview())
            raise SystemExit(0)
        if command == "channel" and channels is not None:
            say.text(channels.ChannelCommand.USAGE)
            raise SystemExit(0)
        if command in Cli.TALK:  # the conversation's own: as said in a session, a chat, or once (tinker COMMAND ...)
            found = BY_NAME[command]
            say.text(f"usage: tinker {found.synopsis}\n\n{found.what[0].upper()}{found.what[1:]}.")
            raise SystemExit(0)
        if command == "confirm":
            command = "resume"
        if command not in Cli.COMMANDS:
            raise SystemExit(f"tinker: no command {command!r} (tinker help lists them)")
        Cli.parser(command).print_help()
        raise SystemExit(0)

    @staticmethod
    def overview():
        """tinker help: what it is, every command by what it is for, the options (with the commands
        that take them) and a few examples; in colour in a terminal (unless NO_COLOR is set). Columns
        are lined up before colouring."""
        paint = Cli.painter()
        out = [f"🛠  {paint('tinker', 'bold')} builds what you ask for with a local Ollama model: a small spec, a unit",
               "   test for each point, just enough code, and a README a person can follow.", "",
               f"{paint('Usage:', 'bold')} tinker {paint('COMMAND', 'cyan')} {paint('[ARGUMENTS]', 'yellow')} "
               f"{paint('[OPTIONS]', 'green')}   (a project: {paint('@ID', 'yellow')} or {paint('@folder', 'yellow')})"]
        out += Cli.command_lines(paint) + Cli.option_lines(paint)
        out += ["", paint("💡 Examples", "bold")] + [f"  {paint('$', 'dim')} {example}" for example in Cli.EXAMPLES]
        return "\n".join(out + ["", f"{paint('tinker help COMMAND', 'cyan')} shows one command in full."])

    @staticmethod
    def command_lines(paint):
        """Every command by what it is for, its synopsis lined up (the channels only with their plugin)."""
        rows = [(group, row) for group in GROUPS for command in COMMANDS if command.group == group
                for row in command.rows]
        width = max(len(synopsis) for _, (synopsis, _) in rows) + 2
        out = []
        for group in GROUPS:
            if group == "Channels" and channels is None:
                continue
            out += ["", paint(Cli.TITLES[group], "bold")]
            for synopsis, what in [row for of_group, row in rows if of_group == group]:
                name, _, arguments = synopsis.partition(" ")
                padding = " " * (width - len(synopsis))
                out.append(f"  {paint(name, 'cyan')}{' ' + paint(arguments, 'yellow') if arguments else ''}"
                           f"{padding}{what}")
        return out

    @staticmethod
    def option_lines(paint):
        """Every option, with the commands that take it."""
        options = Cli.options()
        width = max(len(name) for name in options) + 2
        return ["", paint("⚙️  Options", "bold")] + [
            f"  {paint(name, 'green')}{' ' * (width - len(name))}{what} "
            f"{paint('(' + ', '.join(commands) + ')', 'dim')}" for name, (what, commands) in options.items()]

    @staticmethod
    def options():
        """{--option [VALUE]: (what it does, the commands that take it)}, from the command table."""
        found = {}
        for command in COMMANDS:
            if command.name not in Cli.COMMANDS:
                continue
            for option in command.options:
                name = option.flag + (f" {option.metavar}" if option.metavar else "")
                found.setdefault(name, (option.help, []))[1].append(command.name)
        return found

    @staticmethod
    def painter():
        """paint(text, style): ANSI colour in a terminal; plain text when piped or NO_COLOR is set."""
        codes = {"bold": "1", "dim": "2", "cyan": "36", "yellow": "33", "green": "32"}
        if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
            return lambda text, style: text
        return lambda text, style: f"\033[{codes[style]}m{text}\033[0m"

    @staticmethod
    def overview_markup():
        """tinker help as a conversation shows it (Markup): what to send, by what it is for. A command is sent
        there as its words alone (no tinker before it)."""
        out = ["🛠 **What you can send**"]
        for group in GROUPS:
            if group == "Channels" and channels is None:
                continue
            out += ["", f"**{Cli.TITLES[group]}**"]
            for command in [command for command in COMMANDS if command.group == group]:
                for synopsis, what in command.rows:
                    out.append("💬 anything else: a normal chat" if not command.name else f"- `{synopsis}` {what}")
        return "\n".join(out + ["", "❓ `help COMMAND` shows one command in full"])

    @staticmethod
    def parser(command):
        """The parser of one command, with only what that command takes (from the command table)."""
        found = BY_NAME[command]
        parser = argparse.ArgumentParser(prog=f"tinker {command}", allow_abbrev=False, description=found.about)
        for argument in found.arguments:
            choices = list(Internet.backends()) if argument.choices == "backends" else argument.choices
            parser.add_argument(argument.dest, nargs=argument.nargs, metavar=argument.metavar, help=argument.help,
                                choices=choices)
        for option in found.options:
            if option.metavar:
                parser.add_argument(option.flag, dest=option.dest, metavar=option.metavar, help=option.help)
            else:
                parser.add_argument(option.flag, dest=option.dest, action="store_true", help=option.help)
        return parser

    @staticmethod
    def read_command(command, given, args, parser):
        """The command's arguments in the fields the rest of Tinker reads, checked."""
        field = command.replace("-", "_")
        if command in ("reset", "models", "memory", "runs", "doctor"):
            setattr(args, field, True)
        elif command in ("use-model", "calibrate", "embed", "use-web"):
            setattr(args, field, Cli.non_empty(given.name, command, parser))
        elif command in ("resume", "status", "log"):
            setattr(args, field, Cli.project_named(given.words, parser))
        elif command == "restart":
            args.restart = True
        elif command in ("add", "fix", "amend"):
            setattr(args, field, given.words)
        elif command == "rename":
            args.rename = Cli.renamed(given.words, parser)
        elif command == "learn":
            args.learn = Cli.learning(given.words, parser)
        elif command in ("jobs", "stop"):
            setattr(args, field, Cli.job_words(command, given.words, parser))
        elif command == "read":
            args.read = Cli.reading(given.words, parser)
        elif command in ("ask", "teach", "exercise"):
            setattr(args, field, Cli.non_empty(" ".join(given.words), command, parser))
        elif command == "config":
            if len(given.words) not in (0, 2):
                parser.error("use tinker config (the settings) or tinker config KEY VALUE")
            args.config = given.words
        if command in ("restart", "reset"):
            args.yes = given.yes
        named = {"status": args.status, "log": args.log, "rename": (args.rename or ("",))[0]}.get(command)
        if named and args.workdir is not None:
            parser.error(Cli.finds_its_folder(f"tinker {command}"))

    @staticmethod
    def project_named(words, parser):
        """The project the words name (@ID or @folder), or "" for this folder's; nothing else may follow."""
        name, rest = refs(words)
        if rest:
            parser.error(f"name a project as @ID or @folder (tinker runs lists them), not {' '.join(rest)!r}")
        return name

    @staticmethod
    def renamed(words, parser):
        """(the project named, or "", the folder's new name)."""
        name, rest = refs(words)
        if len(rest) != 1 or not Cli.FOLDER_RE.fullmatch(rest[0]):
            parser.error("use tinker rename NAME or tinker rename @PROJECT NAME; a name is letters, digits, - _ "
                         "and ., starting with a letter or digit")
        return name, rest[0]

    @staticmethod
    def job_words(command, words, parser):
        """jobs [STATE] [PAGE]: (the state or None, the page); stop [#JOB]: the job's number or None."""
        if command == "stop":
            number = job_number(words[0]) if len(words) == 1 else None
            if words and number is None:
                parser.error("use tinker stop (every job of yours that runs) or tinker stop #N (tinker jobs lists them)")
            return number
        state = words[0] if words and words[0] in STATES else None
        rest = words[1:] if state else words
        if len(rest) > 1 or (rest and not Cli.is_page(rest[0])):
            parser.error("use tinker jobs [running|paused|stopped|finished|archived] [PAGE], e.g. tinker jobs finished 2")
        return state, int(rest[0]) if rest else 1

    @staticmethod
    def is_page(word):
        """Whether a word is a page number (1 or more)."""
        return word.isdigit() and int(word) >= 1

    @staticmethod
    def learning(words, parser):
        if len(words) > 2 or not words[0].strip():
            parser.error('use tinker learn WHAT (e.g. tinker learn Flask) or tinker learn NAME "DEFINITION" '
                         '(quote the definition)')
        return words

    @staticmethod
    def reading(words, parser):
        """read's words, checked: list; show #N; #N [WHAT] (its quotes left out); else a source (its words, joined)."""
        if words[0] in ("list", "show"):
            if words == ["list"] or (words[0] == "show" and len(words) == 2 and job_number(words[1])):
                return words
            parser.error("use tinker read list, or tinker read show #N")
        if job_number(words[0]):
            return words[:1] + ([" ".join(words[1:]).strip(" \"'“”")] if words[1:] else [])
        return [Cli.non_empty(" ".join(words), "read", parser)]

    @staticmethod
    def non_empty(value, command, parser):
        if not value.strip():
            parser.error(f"tinker {command}: the value is empty")
        return value.strip()

    @staticmethod
    def check_run(args, parser):
        """A new build, or one of new, resume, add, fix: read into one shape. A feature or a bug
        becomes a resume of its run with a request (args.change_request; args.bug for a bug);
        args.spec is the spec file of a new build."""
        kind = args.command
        args.bug, args.change_request = kind == "fix", None
        if kind == "task":
            Cli.read_task(args, parser)
        if kind == "resume" and args.resume and args.workdir is not None:
            parser.error(Cli.finds_its_folder("tinker resume"))
        if kind in ("add", "fix"):
            Cli.check_request(kind, args, parser)
        if kind == "amend":
            Cli.check_amend(args, parser)

    @staticmethod
    def read_task(args, parser):
        """The task's words, quoted or not (an older spec file given last as a word is still one)."""
        words = list(args.task)
        last = Path(words[-1])
        if len(words) > 1 and not args.spec and Cli.is_spec_file(last):
            args.spec = words.pop()
        args.task = " ".join(words).strip()
        if not args.task:
            parser.error("give a task, e.g. tinker task a calculator in Python")

    @staticmethod
    def is_spec_file(path):
        """Whether a word names a spec file here (Markdown or text)."""
        return path.suffix in (".md", ".txt") and path.is_file()

    @staticmethod
    def check_request(kind, args, parser):
        """add [@PROJECT] FEATURE or fix [@PROJECT] BUG: a resume of its run, with the request (args.change_request)."""
        what = "bug" if kind == "fix" else "feature"
        name, words = refs(args.fix if kind == "fix" else args.add)
        text = " ".join(words).strip()
        if not text:
            parser.error(f"tinker {kind} needs a {what} description")
        if name and args.workdir is not None:
            parser.error(Cli.finds_its_folder(f"tinker {kind}"))
        args.add, args.fix, args.change_request, args.resume = [text], None, text, name

    @staticmethod
    def check_amend(args, parser):
        """amend [@PROJECT] CHANGE: a resume of its run, with the change (args.amend)."""
        name, words = refs(args.amend)
        text = " ".join(words).strip()
        if not text:
            parser.error("use tinker amend CHANGE or tinker amend @PROJECT CHANGE")
        if name and args.workdir is not None:
            parser.error(Cli.finds_its_folder("tinker amend"))
        args.amend, args.resume = text, name

    @staticmethod
    def finds_its_folder(command):
        return f"{command} @PROJECT finds the project's folder itself: remove --workdir (or give --workdir and no @PROJECT)"

    @staticmethod
    def works_for_hours(args):
        """A run or learning: work the computer must not sleep through (the other commands are quick)."""
        return args.command in Cli.RUNS + Cli.LEARNING


class Commands:
    """The commands that show or change Tinker's settings, models, what it knows and the projects, then
    exit: tinker config, tinker models, tinker use-model, tinker embed, tinker use-web, tinker calibrate,
    tinker learn, tinker ask, tinker teach, tinker read, tinker memory, tinker runs, tinker status, tinker log,
    tinker rename."""

    def __init__(self, args):
        self.args = args

    def run(self):
        """Runs the command the command line gives; False when it gives none (a run then)."""
        args = self.args
        if args.config is not None:
            self.config(args.config)
        elif args.models:
            self.list_models()
        elif args.use_model:
            self.use_model(args.use_model)
        elif args.embed:
            self.embed(args.embed)
        elif args.use_web:
            self.use_web(args.use_web)
        elif args.calibrate is not None:
            self.calibrate()
        elif args.learn:
            self.learn()
        elif args.ask:
            self.ask()
        elif args.teach:
            self.teach()
        elif args.read:
            self.read()
        elif args.exercise:
            self.exercise()
        elif args.memory:
            self.show_memory(args.graph)
        elif args.runs:
            self.list_runs()
        elif args.jobs is not None:
            self.list_jobs(*args.jobs)
        elif args.command == "stop":
            self.stop(args.stop)
        elif args.status is not None:
            self.show_status(args.status, args.workdir)
        elif args.log is not None:
            self.show_log(args.log, args.workdir, args.calls)
        elif args.rename:
            self.rename(*args.rename, args.workdir)
        elif args.doctor:
            Doctor().run()
        else:
            return False
        return True

    @staticmethod
    def config(words):
        """tinker config: the settings a run would use; tinker config KEY VALUE sets one (checked)."""
        if words:
            value = Config.set(words[0], words[1])
            say.done(f"{words[0]} is {value!r} now")
            return
        name = Config.get("model")
        settings = Models.settings(name) if name else None
        say.text(f"home:            {Home.root}")
        say.text(f"ollama_url:      {Config.ollama_url()}")
        say.text(f"model in use:    {Commands.model_state(name, settings)}")
        if settings:
            Commands.print_measured(settings)
        for key in Config.SETTABLE:
            if key not in ("ollama_url",):
                say.text(f"{key + ':':16} {Config.get(key)!r}")
        say.text(f"web:             {Config.get('web') or 'none'} ({Internet.state()}; used by tinker learn and --web only)")
        embed = Config.get("embed_model") or "none: select one with tinker embed NAME (tinker learn and --web need one)"
        say.text(f"embed_model:     {embed}")
        say.text("Set one with tinker config KEY VALUE (the model, the embedding model and the web have their own commands).")

    @staticmethod
    def model_state(name, settings):
        if not name:
            return "none (measure one with tinker calibrate NAME, then select it with tinker use-model NAME)"
        if settings:
            return f"{name} (calibrated {settings.get('calibrated', '?')})"
        return f"{name} (not measured: tinker calibrate {name})"

    @staticmethod
    def print_measured(settings):
        """What calibration measured for the model in use, and how it thinks."""
        say.text(f"input budget:    {settings['max_prompt_tokens']} tokens"
                 + (f" (largest read {settings['largest_prompt_read']}, crashed at {settings['crashed_reading']})"
                    if settings.get("crashed_reading") else ""))
        say.text(f"reply limit:     {settings['max_reply_tokens']} tokens (fixes at most {settings['fix_max_tokens']})")
        say.text(f"context:         {settings['num_ctx']} tokens")
        think = settings.get("think")
        build = Config.get("build_think") if think not in (None, False) else None
        say.text(f"think:           {think} for planning" + (f", {build} for building" if build else ""))
        say.text(f"chars per token: {settings.get('chars_per_token')}")

    @staticmethod
    def list_models():
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(f"Ollama does not answer at {Config.ollama_url()} (tinker config ollama_url URL).")
        say.text(f"  {'MODEL':36} {'SIZE':>7}  {'PROMPT':>6} {'READ/S':>7} {'WRITE/S':>7}  CALIBRATED")
        for model in models:
            name, s = model.get("name", ""), Models.settings(model.get("name", "")) or {}
            mark = "*" if name == Config.get("model") else " "
            say.text(f"{mark} {name[:36]:36} {model.get('size', 0) / 1e9:6.1f}G  {s.get('max_prompt_tokens') or '-':>6} "
                     f"{s.get('read_tokens_per_s') or '-':>7} {s.get('write_tokens_per_s') or '-':>7}  "
                     f"{s.get('calibrated', 'not measured: tinker calibrate NAME')}")
        say.text("\n* the model in use. Measure one with tinker calibrate NAME, select it with tinker use-model NAME.")

    @staticmethod
    def use_model(name):
        """tinker use-model NAME: the model every run uses from now on, one already measured (nothing
        is measured unasked)."""
        if Models.settings(name) is None:
            raise SystemExit(f"{name} has not been measured: measure it first with tinker calibrate {name} "
                             f"(tinker models lists the models Ollama has)")
        Config.save(model=name)
        say.done(f"Model in use: {name}")

    @staticmethod
    def embed(name):
        """tinker embed NAME: the embedding model that indexes what is learned from now on: one Ollama
        has that gives embeddings (tried once here)."""
        Models.installed(name)
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(f"{embeddings.failed}: choose an embedding model (tinker models lists the models Ollama has)")
        Config.save(embed_model=name)
        say.done(f"Embedding model: {name}; what was learned is indexed with it from the next tinker learn or run on")

    @staticmethod
    def use_web(name):
        """tinker use-web NAME: the web search tinker learn and --web use from now on (one of
        Internet.backends()); it says when that one is not usable yet (a missing key)."""
        Config.save(web=name)
        say.done(f"Web search: {name}; {Internet.state()}")

    def calibrate(self):
        """tinker calibrate NAME: measure a model (again)."""
        Calibrator(Models.installed(self.args.calibrate)).run()

    def learn(self):
        """tinker learn WHAT [DEFINITION]: a command of yours (a definition given, or an alias in your
        shell) is kept at once, with no model; anything else is learned from the web with the model
        in use (SlotLearner tells a language, a tool and a domain apart), kept and indexed (an
        embedding model and the Internet plugin must be there; when one is not, nothing is loaded
        and it says why). A learn the web cannot feed stops before any model call, saving nothing
        (learning.LearnStopped), and ends non-zero. However it ends, it says how long it took. Its tracker
        reports its steps and its end, as a run's does, so a chat that started it shows them."""
        args, option = self.args, "tinker learn"
        topic, definition = args.learn[0].strip(), (args.learn[1] if len(args.learn) > 1 else None)
        if definition is not None or TaughtCommands.alias(topic):
            TaughtCommands.teach(topic, definition)
            return
        model, embeddings, internet = self.learning_tools(option)
        tracker = Commands.followed(f"Learning {topic}")
        started, ending = time.monotonic(), {"status": "stopped", "headline": f"Learning {topic} did not finish"}
        try:
            ending = self.learned(topic, SlotLearner(model, internet, embeddings, tracker))
        except SystemExit as stopped:
            ending = Commands.learn_stopped(topic, stopped)
            raise
        finally:
            model.unload()
            Models.remember(model)
            took = time.monotonic() - started
            say.title(f"Time taken: {human_duration(took)}", "⏱️")
            Commands.tracker_ended(tracker, ending, took)

    @staticmethod
    def followed(title):
        """The one report of a learn, an ask or a teach: what it says and what the model is doing, as a run's
        tracker keeps them."""
        tracker = Tracker(title)
        say.follow(tracker.heard)
        return tracker

    def learned(self, topic, learner):
        """The topic learned and shown; its ending, for the tracker."""
        entry = learner.learn(topic, deep=True)
        if entry is None:
            raise SystemExit(f"Nothing learned about {topic}; nothing saved.")
        self.print_learned(topic, entry)
        return {"status": "done", "headline": f"Learned {entry['name']}",
                "details": learner.recap or [("Findings", str(len(entry.get("findings", []))))],
                "next": [("See it", "tinker memory")]}

    @staticmethod
    def learn_stopped(topic, stopped):
        """The ending of a learn that stopped: why, and what to do next when it says."""
        details = stopped.details if isinstance(stopped, LearnStopped) else [("Why", first_line(str(stopped), 200))]
        return {"status": "blocked", "headline": f"Nothing learned about {topic}", "details": details}

    @staticmethod
    def tracker_ended(tracker, ending, seconds):
        """The end told to the tracker, which leaves the home unless the conversation that started it reads it last."""
        tracker.end(ending, seconds)
        if os.environ.get(conversation.OFF) != "off":
            tracker.close()

    @staticmethod
    def learning_tools(option):
        """(the model in use, the embedding model, the Internet plugin), each checked first: when one is
        not there, nothing is loaded and it says why."""
        name, settings = Commands.checked_model()
        embeddings = Embeddings.required(option)
        internet = Internet.required(option)
        return Commands.model_in_use(name, settings, "learn"), embeddings, internet

    @staticmethod
    def checked_model():
        """(name, settings) of the model in use: selected, installed and measured (else it says what to do)."""
        name, settings = Models.selected()
        Models.installed(name)
        Config.check_model(run=False)
        return name, settings

    @staticmethod
    def model_in_use(name, settings, log_name):
        """The model, its calls kept in the home's log of that name (nothing is loaded until it is asked)."""
        log = Log(Home.store(), log_name)
        say.keep_log(log)
        return Model(name, settings, Config.keep_alive(Config.get("keep_alive")), log,
                     max_growth=float(Config.get("max_growth")))

    def ask(self):
        """tinker ask QUESTION: answered from what Tinker keeps alone (no web), each claim citing the item it
        rests on."""
        question = self.args.ask
        model = Commands.model_in_use(*Commands.checked_model(), "teach")
        memory = Memory(Embeddings.configured())
        Commands.reported(f"Asking: {first_line(question, 60)}", model,
                          lambda tracker: Asker(model, memory, tracker).answer(question))

    def teach(self):
        """tinker teach WHAT: what Tinker keeps corrected by the person, once checked on this machine and on the
        web (the Internet plugin must be there: nothing is corrected unchecked)."""
        teaching = self.args.teach
        name, settings = Commands.checked_model()
        internet = Internet.required("tinker teach")
        model, memory = Commands.model_in_use(name, settings, "teach"), Memory(Embeddings.configured())
        Commands.reported(f"Teaching: {first_line(teaching, 60)}", model,
                          lambda tracker: Teacher(model, internet, memory, tracker).teach(teaching))

    def read(self):
        """tinker read SOURCE: the source stored on the reading list (no model), then learned from as read #N, which
        its job is from then on: stopped and resumed, it goes on with the chapters not read yet. tinker read #N
        ["WHAT"]: go on, or look again for WHAT. tinker read list, tinker read show #N: show the reading list."""
        words = self.args.read
        if words == ["list"]:
            return reading.show_list()
        if words[0] == "show":
            return reading.show(job_number(words[1]))
        number, focus = job_number(words[0]), " ".join(words[1:])
        if number is None:
            number, new = reading.stored(words[0])
            if not new:
                return
            Jobs.retarget(["read", f"#{number}"])
        title = reading.known_reading(number)["title"]
        model = Commands.model_in_use(*Commands.checked_model(), "read")
        Commands.reported(f"Reading #{number} {first_line(title, 50)}", model,
                          lambda tracker: Reader(model, tracker).read(number, focus))

    def exercise(self):
        """tinker exercise LANGUAGE: practice projects in that language, one after another, until it is stopped
        (exercising.Exercise; a job: tinker stop #N stops it, everything kept, tinker resume #N goes on); tinker
        exercise status shows how each is going. Its tracker reports to the chats too, as a run's does."""
        what = self.args.exercise
        if what.lower() == "status":
            return exercising.show()
        if what.split()[0].lower() in ("stop", "resume"):
            raise SystemExit(f"An exercise is a job: tinker {what.split()[0].lower()} #N (tinker jobs lists them).")
        active = exercising.Exercises.active()
        if active:
            raise SystemExit(exercising.already_running(active))
        model = Commands.model_in_use(*Commands.checked_model(), "exercise")
        Commands.reported(f"Exercising {what}", model, lambda tracker: exercising.Exercise(model, tracker).run(what),
                          chats=True)

    @staticmethod
    def reported(title, model, work, chats=False):
        """work(tracker) done with the model, its tracker told how it ended (work gives the ending) and how long it
        took, however it ends; the model unloaded after. chats: the tracker reports to the enabled chats too."""
        tracker = Commands.followed(title)
        listening = conversation.attach(title, tracker) if chats else None
        started, ending = time.monotonic(), {"status": "stopped", "headline": f"{title} did not finish"}
        try:
            ending = work(tracker)
        except SystemExit as stopped:
            ending = {"status": "blocked", "headline": first_line(str(stopped), 200)}
            raise
        finally:
            model.unload()
            Models.remember(model)
            took = time.monotonic() - started
            say.title(f"Time taken: {human_duration(took)}", "⏱️")
            if listening:  # the chats it reports to are told the end, and the tracker leaves the home
                tracker.end(ending, took)
                listening.end()
            else:
                Commands.tracker_ended(tracker, ending, took)

    @staticmethod
    def print_learned(topic, entry):
        with say.part(f"{entry['name']}: {entry['summary']}", "📘"):
            for item in Library.items({Library.slug(entry.get("language") or topic): entry})[1:]:
                say.item(f"[{item['phase']}] {item['text']}")
            for contradiction in entry.get("contradictions", []):
                say.info(f"Facts {', '.join(map(str, contradiction['facts']))} disagree: {contradiction['note']}", "⚖️")
            for gap in entry.get("open", []):
                say.info(f"Still open (Q{gap['question']}): {gap['missing'] or gap['search']}", "❓")
            for note in entry["notes"]:
                say.warn(f"Not learned: {note}")
        say.tip("A run uses this whenever its request needs it. See everything Tinker learned with: tinker memory")

    @staticmethod
    def show_memory(graph=False):
        """What Tinker knows, as lists; or (graph) as a graph in one page, opened in the browser when this
        prints to a terminal (a chat sends the page as a file)."""
        if graph:
            Commands.show_graph()
            return
        languages, any_language, topics = Home.store().items("languages"), Home.store().get("memory", "any") or {}, \
            Library.topics()
        taught = TaughtCommands.all()
        say.title("What Tinker has learned", "🧠")
        if not (languages or any_language.get("lessons") or topics or taught):
            say.info("Nothing yet: it learns from every build (tinker task) and from tinker learn WHAT", "🌱")
        for key, (doc, topic) in Commands.languages_known(languages, topics).items():
            Commands.print_language(key, doc, topic)
        Commands.print_any_language(any_language.get("lessons", []))
        Commands.print_topics({slug: entry for slug, entry in topics.items() if entry.get("kind") != "language"})
        Commands.print_taught(taught)
        Commands.print_retired(sum(len(doc.get("retired", [])) for doc in [*languages.values(), any_language,
                                                                           *topics.values()]))
        Commands.print_memory_tips()

    @staticmethod
    def show_graph():
        graph = write_page(Embeddings.configured())
        say.text(f"Tinker's memory: {graph.summary()}")
        say.text(f"Similar lessons found by {graph.similar_by}")
        say.text(f"Page: {Home.graph_page()}")
        if sys.stdout.isatty():
            webbrowser.open(Home.graph_page().as_uri())
            say.done("Opened in your browser.")

    @staticmethod
    def languages_known(languages, topics):
        """{key: (what its builds taught, what was learned of it from the web)} of every language Tinker knows."""
        learned = {slug: entry for slug, entry in topics.items() if entry.get("kind") == "language"}
        return {key: (languages.get(key) or {}, learned.get(key)) for key in dict.fromkeys([*languages, *learned])}

    @staticmethod
    def print_language(key, doc, topic):
        """A language: how many projects built it and what was read of it, then the toolchain that worked, its
        proven facts, its fixes and its lessons (each only when there is one)."""
        name = doc.get("language") or (topic or {}).get("name") or key
        with say.part(" · ".join([name, Commands.projects_built(doc.get("projects", 0)), *Commands.read_of(topic)]),
                      "📘"):
            Commands.print_toolchain(Toolchain(doc.get("toolchain")))
            Commands.print_items("Proven facts", "✅", [fact.get("text", "") for fact in doc.get("facts", [])])
            Commands.print_items("Fixes", "🔧", [f"{first_line(fix['failure'], 60)} → {first_line(fix['cause'], 60)}"
                                                 f" (helped {fix.get('used', 0)}×)" for fix in doc.get("fixes", [])])
            Commands.print_lessons(doc.get("lessons", []))

    @staticmethod
    def projects_built(count):
        return "nothing built yet" if not count else f"{count} project{'s' if count > 1 else ''} built"

    @staticmethod
    def read_of(topic):
        """What was read of it from the web, as a part of its heading (nothing when it was not)."""
        if not topic:
            return []
        return [f"docs read {Commands.short_date(topic.get('learned'))} ({len(topic.get('facts', []))} facts)"]

    @staticmethod
    def short_date(text):
        """"2026-10-22 00:22" as "22 Oct"; anything else as it is."""
        try:
            return datetime.datetime.strptime(str(text)[:10], "%Y-%m-%d").strftime("%-d %b")
        except ValueError:
            return str(text or "?")

    @staticmethod
    def print_toolchain(toolchain):
        """The toolchain that worked, one command a row, under what it does."""
        if not toolchain:
            return
        rows = [("setup", command) for command in toolchain.setup] + [
            (label, getattr(toolchain, key)) for label, key in (("activate", "activate"), ("test", "test_all"),
                                                                ("check", "check"), ("format", "format"),
                                                                ("lint", "lint"))]
        with say.part("Toolchain", "🧰"):
            shown = ""
            for label, command in rows:
                if command:
                    say.text(f"{'' if label == shown else label:9} {command}")
                    shown = label

    @staticmethod
    def print_items(title, icon, texts):
        if texts:
            with say.part(title, icon):
                for text in texts:
                    say.item(first_line(text, 110))

    @staticmethod
    def print_lessons(lessons):
        """Lessons, each marked proven (✅) or not yet (🌱)."""
        if lessons:
            with say.part("Lessons (✅ proven by a build, 🌱 not yet)", "📒"):
                for lesson in lessons:
                    say.info(first_line(lesson.get("text", ""), 110), "✅" if lesson.get("status") == "proven" else "🌱")

    @staticmethod
    def print_any_language(lessons):
        if lessons:
            with say.part("Any language", "🌍"):
                Commands.print_lessons(lessons)

    @staticmethod
    def print_topics(topics):
        """The tools and domains learned from the web (languages are shown with their own)."""
        if topics:
            with say.part("Topics learned", "🔬"):
                for slug, entry in topics.items():
                    say.item(f"{entry.get('name', slug)}: {first_line(str(entry.get('summary', '')), 80)} "
                             f"(read {Commands.short_date(entry.get('learned'))}, {len(entry.get('facts', []))} facts)")

    @staticmethod
    def print_taught(taught):
        if taught:
            with say.part("Your commands", "⌨️"):
                for name, entry in taught.items():
                    say.item(f"{name}: {TaughtCommands.describe(entry)}")

    @staticmethod
    def print_retired(count):
        if count:
            say.title(f"{count} item{'s' if count > 1 else ''} did not hold and {'were' if count > 1 else 'was'} "
                      f"retired: never shown to the model again", "♻️")

    @staticmethod
    def print_memory_tips():
        with say.part("What next", "💡"):
            for what, command in (("Ask about it", "tinker ask what is your toolchain for python"),
                                  ("Correct it", "tinker teach WHAT"), ("Explore it", "tinker memory --graph"),
                                  ("Forget a part", "tinker clear PART")):
                say.text(f"{what:14} {command}")

    @staticmethod
    def list_runs():
        entries = sorted(Runs.entries(), key=lambda entry: entry.get("updated", ""), reverse=True)
        if not entries:
            say.text("No runs yet.")
            return
        say.text(f"{'PROJECT':9} {'STATUS':14} {'UPDATED':17} {'FOLDER':32} TASK")
        for entry in entries:
            task = Commands.task_of(entry)
            say.text(f"{'@' + entry.get('id', '?'):9} {entry.get('status', '?'):14} {entry.get('updated', ''):17} "
                     f"{Commands.short_folder(entry.get('workdir', '')):32} "
                     f"{task[:60] + ('...' if len(task) > 60 else '')}")

    @staticmethod
    def list_jobs(state, page):
        """tinker jobs: the jobs in a state (none: the running, paused and stopped ones), newest first, a page
        of them at a time."""
        jobs = Jobs.listed(state)
        if not jobs:
            say.text(f"No {state or 'running, paused or stopped'} jobs.")
            return
        shown, pages = Jobs.page(jobs, page)
        say.text(f"{'JOB':6} {'STATE':9} {'STARTED':17} COMMAND")
        for job in shown:
            title = job["title"][:70] + ("..." if len(job["title"]) > 70 else "")
            say.text(f"{'#' + str(job['number']):6} {job['state']:9} {job['started']:17} {title}")
        if page < pages:
            say.text(f"\nPage {page} of {pages}: tinker jobs {(state + ' ') if state else ''}{page + 1} for more.")
        elif pages > 1 or page > 1:
            say.text(f"\nPage {page} of {pages}.")

    @staticmethod
    def stop(number):
        """tinker stop #N: that job told to stop, as Ctrl-C stops it (it saves what it did); tinker stop: the job that
        runs, whatever it is."""
        if number is not None:
            job = Jobs.get(number)
            if job is None or job["state"] not in ("running", "paused"):
                raise SystemExit(f"Job #{number} is not running or paused (tinker jobs lists them).")
            jobs = [job]
        else:
            jobs = Jobs.listed("running")
        if not jobs:
            say.info("No job is running.", "💤")
        for job in jobs:
            Jobs.stop(job)
            say.done(f"Stopping #{job['number']} {job['title']}: it saves itself; tinker resume #{job['number']} goes on.")

    @staticmethod
    def short_folder(workdir):
        """The folder in at most 32 chars: under ~, and cut at its start (its name is what identifies it)."""
        home = str(Path.home())
        shown = ("~" + workdir[len(home):]) if workdir.startswith(home) else workdir
        return ("..." + shown[-29:] if len(shown) > 32 else shown)[:32]

    @staticmethod
    def task_of(entry):
        """The run's task on one line, with how many features were added to it."""
        task = entry.get("task", "").replace("\n", " ")
        features = entry.get("features")
        return f"[+{len(features)} feature{'s' if len(features) > 1 else ''}] " + task if features else task

    @staticmethod
    def show_status(name, workdir_arg):
        workdir = Runs.workdir(name, workdir_arg)
        run = Runs.read_record(workdir)
        spec = saved(workdir, "spec", "plan")
        say.text(f"Run {run['id']} ({run.get('status', '?')}, updated {run.get('updated', '?')}): {first_line(run['task'])}")
        say.text(f"Project: {workdir}   Language: {(spec or {}).get('language', '?')}")
        if not spec or not spec.get("steps"):
            say.text("No spec yet (planned on the next tinker resume).")
            return
        Commands.print_criteria(spec["steps"])
        for cycle in spec.get("cycles", []):
            if cycle.get("phase") != "done":
                waits = " (its plan waits for your confirm)" if cycle.get("awaiting") else ""
                say.text(f"  {'project' if cycle['id'] == 'base' else cycle.get('kind', 'feature') + ' ' + cycle['id']}"
                         f": next phase {cycle.get('phase')}{waits}")

    @staticmethod
    def print_criteria(steps):
        """How many criteria pass; each one's status, and a parked one's last error."""
        say.text(f"{sum(1 for step in steps if step['status'] == 'green')}/{len(steps)} criteria pass")
        for step in steps:
            say.text(f"  {step['id']:5} {STATUS_TEXT.get(step['status'], step['status']):11} "
                     f"{step['title'][:50]:50} {step['test']}")
            if step["status"] == "blocked" and step.get("last_error"):
                say.text(f"        last error: {first_line(step['last_error'], 110)}")

    @staticmethod
    def show_log(name, workdir_arg, calls=False):
        """tinker log: what the project's runs printed; --calls: its model calls."""
        workdir = Runs.workdir(name, workdir_arg)
        Runs.read_record(workdir)
        store = project_store(workdir)
        if calls:
            Commands.print_calls(store)
            return
        for _, entry in store.entries("output"):
            say.text(entry.get("line", ""))

    @staticmethod
    def print_calls(store):
        """Each model call and detail: when, what, an error, and the start of its reply."""
        for _, entry in store.entries("events"):
            say.text(f"[{entry['time']}] {entry.get('label') or entry.get('note', '')}"
                     + (f"  error: {entry['error']}" if entry.get("error") else ""))
            if entry.get("reply"):
                say.text("\n".join(f"    {line}" for line in str(entry["reply"]).splitlines()[:40]))

    @staticmethod
    def rename(name, new, workdir_arg):
        """A project's folder renamed (in the folder it is in) while its plan waits for confirmation, with
        the run's record and its pointer; nothing is built there yet."""
        workdir = Runs.workdir(name, workdir_arg or ".")
        project = Project.load(workdir)
        RunChecks.check_awaiting(project, "tinker rename renames the folder of")
        target = workdir.parent / new
        if target.exists():
            raise SystemExit(f"{target} already exists: pick another name")
        workdir.rename(target)
        run = dict(project.run, workdir=str(target))
        Runs.save(run, target)
        say.done(f"Renamed: {workdir} -> {target}")


class Doctor:
    """tinker doctor: what Tinker needs, checked in order, each with what it found and, when
    something is missing or wrong, the command that fixes it (like brew doctor). Required: Ollama
    answering, a model selected and measured. For learning and --web: an embedding model and the
    web. Nice to have: the tinker command on PATH."""

    def run(self):
        problems = 0
        for required, name, check in self.checks():
            try:
                found = check()
                say.done(f"{name}: {found}")
            except SystemExit as exc:
                problems += required
                (say.fail if required else say.warn)(f"{name}: {exc}")
        if problems:
            say.title(f"{problems} thing(s) to fix before a build can run.", Messages.ICONS["stop"])
        else:
            say.title("Everything Tinker needs is there.", "🎉")
        if problems:
            raise SystemExit(1)

    def checks(self):
        """(required, what, check): a check returns what it found or exits with what to do."""
        return [(True, "Ollama", self.ollama), (True, "model", self.model),
                (False, "embedding model (for tinker learn and --web)", self.embedding),
                (False, "web (for tinker learn and --web)", self.web),
                (False, "tinker command", self.command)]

    @staticmethod
    def ollama():
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(f"it does not answer at {Config.ollama_url()}: start it (ollama serve, or the Ollama "
                             f"app), or set where it listens: tinker config ollama_url URL")
        return f"answers at {Config.ollama_url()}, {len(models)} model(s) installed"

    @staticmethod
    def model():
        name, settings = Models.selected()
        Models.installed(name)
        return (f"{name}, measured {settings.get('calibrated', '?')}: reads {settings['max_prompt_tokens']} tokens, "
                f"writes up to {settings['max_reply_tokens']}")

    @staticmethod
    def embedding():
        name = Config.get("embed_model")
        if not name:
            raise SystemExit("none selected: pick one Ollama has with tinker embed NAME (tinker models lists them)")
        Embeddings.check()
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(f"{embeddings.failed}: pick another with tinker embed NAME")
        return f"{name}, working"

    @staticmethod
    def web():
        state = Internet.state()
        if not state.startswith("ready"):
            raise SystemExit(state)
        return state

    @staticmethod
    def command():
        found = shutil.which("tinker")
        if not found:
            script = Path(__file__).resolve().parent.parent / "tinker.py"
            raise SystemExit(f"not on PATH, so it is typed as python3 {script}: to type tinker anywhere, run "
                             f"{script.parent / 'install.sh'}")
        return found


class ProjectRun:
    """One run on a project: a new project, or a resumed one, or a feature (tinker add) or a bug fix
    (tinker fix) for one. It opens the run and a session on it, works through the cycle that is open,
    saves whatever happens, and ends with a summary. Its tracker reports it all, as data."""

    def __init__(self, args, script_started, started_at):
        self.args = args
        self.script_started, self.started_at = script_started, started_at
        self.session = self.record = self.mode = self.log = None  # record: the run's record
        self.internet = None  # the Internet plugin, with --web only
        self.spec_text = ""
        self.outcome = Outcome()
        self.tracker = Tracker(args.task or "a task")

    def execute(self):
        """Check what the run needs, prepare its folder, open it, work, and finish (exiting with
        its outcome)."""
        workdir = self.check_requirements()
        if self.args.restart:  # this folder's task again, in the folder emptied first
            spec = self.args.spec
            Runs.clear_folder(workdir, Path(spec).expanduser().resolve() if spec else None, self.args.yes)
        say.follow(self.tracker.heard)
        self.open()
        listening = conversation.attach(self.tracker.state["title"], self.tracker)
        try:
            self.work_safely()
            self.finish()
        finally:  # the summary is printed first, so the channels show it too
            if listening:
                listening.end()
            elif os.environ.get(conversation.OFF) != "off":  # the conversation that started it reads it last
                self.tracker.close()
        sys.exit(self.outcome.exit_code)

    def check_requirements(self):
        """The run's folder, once everything the run needs is checked: before a folder is emptied
        or the model loaded, so what is missing stops it with nothing changed."""
        Models.installed(Models.selected()[0])
        Config.check_model(run=True)
        TaughtCommands.all()  # a broken command stops here, not part way
        if self.args.web:  # a run with --web learns what it needs: like tinker learn, it needs both
            Embeddings.required("--web")
            self.internet = Internet.required("--web")
        else:
            Embeddings.check()
        if self.args.workdir is None:  # a new task: its own folder in the projects folder, named after it
            if self.args.spec:
                Runs.read_spec_file(self.args.spec)  # checked before a folder is made
            self.args.workdir = str(ProjectFolder.make(self.args.task))
        return RunChecks.before_start(self.args)

    def open(self):
        """The model, the run, its project and the session; the tracker told the run and its folder."""
        args = self.args
        model_name, settings = Models.selected()
        self.record, workdir, self.spec_text, self.mode = Runs.open(args)
        project = Project.load(workdir, self.record)
        self.tracker.project(project.store, self.record["id"], workdir, f"{workdir.name}: {self.record['task']}")
        Jobs.retarget(["resume", f"@{self.record['id']}"])
        self.log = Log(project.store, "events")
        say.keep_log(self.log)
        model = Model(model_name, settings, Config.keep_alive(Config.get("keep_alive")), self.log,
                      max_growth=float(Config.get("max_growth")))
        self.record["model"] = model.name
        self.print_header(model, workdir)
        self.log.add({"label": "session start", "mode": self.mode, "model": model.name,
                      "started": clock(self.started_at)})
        self.session = Session(model, project, DEFAULT_MAX_STALL, self.internet, confirm=args.confirm,
                               tracker=self.tracker)
        if project.language:
            say.info(f"Learned so far: {self.session.knowledge.summary()}", "🧠")

    def print_header(self, model, workdir):
        run, mode, args = self.record, self.mode, self.args
        change = "fixing a bug" if args.bug else "adding a feature"
        doing = {"new": "restarting" if self.args.restart else "new", "resume": "resuming", "change": change}[mode]
        with say.part(f"Run {run['id']} ({doing}): {run['task']}", "🚀"):
            if mode == "change":
                say.info(f"Request: {first_line(args.change_request, 200)}", "✏️")
            say.info(f"Project: {workdir}", "📁")
            say.info(f"Model: {model.name}; started {clock(self.started_at)}", "🤖")
        say.detail(f"model {model.name} (calibrated {model.settings.get('calibrated', '?')})   context {model.num_ctx}   "
                   f"input budget {model.max_tokens} tok   think {model.think}"
                   + (f" (building: {model.build_think})" if model.build_think and model.build_think != model.think
                      else "")
                   + f"   keep_alive {model.keep_alive}   reply limit per call (thinking included): {model.max_reply} "
                     f"tokens; fixes at most {model.fix_max}")

    def work_safely(self):
        """work, saved whatever happens: Ollama failing, Ctrl-C, a stop, or a bug (reported, so
        the run can be continued)."""
        try:
            self.work()
        except OllamaError as exc:
            say.title(str(exc), Messages.ICONS["fail"])
        except KeyboardInterrupt:
            say.title("Interrupted", Messages.ICONS["stop"])
        except SystemExit as exc:
            if exc.code not in (None, 0) and isinstance(exc.code, str):
                say.title(exc.code, Messages.ICONS["stop"])
        except Exception as exc:  # a bug must not lose the run: report it, save, and say how to continue
            details = traceback.format_exc()
            say.title(f"Unexpected error: {type(exc).__name__}: {first_line(str(exc), 300)}", Messages.ICONS["fail"])
            with say.section():
                say.text(details.rstrip())
            self.log.add({"label": "unexpected error", "error": details})
        finally:
            self.save()

    def work(self):
        """The cycle to work on, run from its saved phase: a plan that waits is confirmed (or amended)."""
        session, run = self.session, self.record
        brief = Brief.load(session.model, session.project.store, self.mode, run["task"], self.spec_text)
        cycle = self.choose_cycle(brief)
        if cycle is not None and cycle.get("awaiting"):
            self.confirm_or_amend(cycle)
        if cycle is None:
            CycleRunner(session).show_plan()
            say.done("Every cycle of this run is done; add a feature with tinker add, or fix a bug with tinker fix")
            self.outcome.set("done")
            return
        self.outcome.set(CycleRunner(session).run(cycle, web=self.internet is not None))

    def choose_cycle(self, brief):
        """The cycle to work on: a new feature or bug (tinker add, tinker fix), the unfinished one, or the
        project itself. A new cycle's plan waits for confirmation only when the run was given --confirm."""
        project = self.session.project
        cycle = project.open_cycle()
        if self.mode == "change":  # its project is open to a new cycle (RunChecks.before_start)
            cycle = self.new_change()
        elif cycle is None and not project.cycles:
            cycle = project.new_cycle("base", self.record["task"], brief)
            cycle["confirm"] = self.session.confirm
            project.save()
        return cycle

    def new_change(self):
        """A feature's or a bug's cycle (its spec file condensed with its request), kept in the run too."""
        session, run, args, project = self.session, self.record, self.args, self.session.project
        feature_id = secrets.token_hex(3)
        kind = "fix" if args.bug else "feature"
        feature_brief = Brief.make(session.model, args.change_request, self.spec_text) \
            if self.spec_text and not args.bug else ""
        cycle = project.new_cycle(feature_id, args.change_request, feature_brief, kind)
        cycle["confirm"] = session.confirm
        run.setdefault("features", []).append({"id": feature_id, "request": args.change_request, "kind": kind,
                                               "added": now_text()})
        Runs.save(run, project.workdir)
        project.save()
        return cycle

    def confirm_or_amend(self, cycle):
        """The plan that waits for the person: changed as they ask, or confirmed."""
        if self.args.amend:
            say.title("Plan: changing it as you asked", "✏️")
            self.tracker.phase("amend")
            self.amend(cycle, self.args.amend)
        else:
            cycle["awaiting"], cycle["confirmed"] = False, True
            say.done("Plan confirmed: building it")
            self.session.save()

    def amend(self, cycle, change):
        """The plan that waits, changed as the person asks: a spec planned again (SpecWriter.amend), or a
        bug shown again with the change (its test written again)."""
        if cycle.get("kind") != "fix":
            SpecWriter(self.session).amend(cycle, change)
            return
        cycle.setdefault("amendments", []).append(change)
        cycle.update(phase="reproduce", awaiting=False, confirmed=False)
        self.session.save()

    def save(self):
        """Whatever happened: the model unloaded, what the run learned about it kept, the run and
        the project saved."""
        model, project = self.session.model, self.session.project
        if self.outcome.status != "awaiting":  # a plan that waits keeps the model loaded for the change asked next
            model.unload()
        Models.remember(model)
        self.record["status"] = self.outcome.status
        Runs.save(self.record, project.workdir)
        Runs.remember(self.record, project.language)
        try:
            project.save()
        except OSError:
            pass

    def finish(self):
        """The summary, the times, and the run's end told to its tracker."""
        self.print_summary()
        ended_at = datetime.datetime.now()
        wall = (ended_at - self.started_at).total_seconds()
        times = {"started": clock(self.started_at), "ended": clock(ended_at), "wall_seconds": round(wall, 1)}
        self.log.add({"label": "session end", "status": self.outcome.status, **times})
        self.record["last_session"] = times
        Runs.save(self.record, self.session.project.workdir)
        total = time.perf_counter() - self.script_started
        say.title(f"Time taken: {human_duration(wall)} (started {clock(self.started_at)}, ended {clock(ended_at)})",
                  "⏱️")
        say.detail(f"total execution time: {total:.2f}s")
        self.tracker.end(self.ending, total)

    def print_summary(self):
        """What happened, for the terminal; the same as data (self.ending) for the tracker."""
        status = self.outcome.status
        self.ending = {"status": status, "headline": "", "details": [], "next": []}
        if status == "done":
            self.summary_done()
        elif status == "awaiting":
            self.summary_awaiting()
        elif status == "blocked":
            self.summary_blocked()
        else:
            self.summary_unfinished()
        say.tip(f"Everything it said: tinker log @{self.record['id']}")

    def summary_unfinished(self):
        run_id = self.record["id"]
        self.ending["headline"] = f"Run {run_id} is not finished. Progress is saved"
        self.ending["next"] = [("Continue it", f"tinker resume @{run_id}")]
        say.title(self.ending["headline"], "⏸️")
        say.tip(f"Continue with: tinker resume @{run_id}")

    def summary_done(self):
        project, run_id = self.session.project, self.record["id"]
        failing = [*cycle.get("readme_failures", []) for cycle in project.cycles]
        if failing:  # the README's examples are how a person uses it: one that fails is said first
            headline = (f"Done, but {len(failing)} README example(s) do not print what they say (all "
                        f"{len(project.criteria)} spec tests pass)")
            with say.part(f"{headline}:", Messages.ICONS["warn"]):
                for failure in failing:
                    say.item(failure)
        else:
            headline = f"Done. All {len(project.criteria)} spec tests pass (SPEC.md)."
            say.title(headline, "🎉")
        self.ending.update(headline=headline, details=[("README example", failure) for failure in failing])
        if project.toolchain.run:
            say.info(f"Run it: cd {project.workdir} && {project.toolchain.run}", "▶️")
            self.ending["details"].append(("Run it", project.toolchain.run))
        if (project.workdir / ENV_EXAMPLE).is_file():
            say.info(f"Settings: {ENV_FILE} (defaults in {ENV_EXAMPLE}).", "⚙️")
            self.ending["details"].append(("Settings", f"{ENV_FILE} (defaults in {ENV_EXAMPLE})"))
        if project.has_adapters():
            say.tip("The tests use sample data instead of external services: try those by hand with real settings.")
        say.tip(f'Add a feature with: tinker add @{run_id} "..."; fix a bug with: tinker fix @{run_id} "..."')
        self.ending["next"] = [("➕ Add a feature", f'tinker add @{run_id} "..."'),
                               ("🐞 fix a bug", f'tinker fix @{run_id} "..."')]

    def summary_awaiting(self):
        """The plan as a person reads it, to confirm or change before anything is built; the tracker is
        given it as data."""
        run_id = self.record["id"]
        plan = self.waiting_plan()
        self.print_plan(plan)
        say.info(f"Its folder: {plan['folder']}   (rename it: tinker rename @{run_id} NAME)", "📁")
        say.tip(f"Build it: tinker confirm @{run_id}   (in the chat: confirm)")
        say.tip(f"Change it: tinker amend @{run_id} CHANGE   (in the chat: amend WHAT)")
        self.ending.update(headline=f"Its plan waits for your confirm: tinker confirm @{run_id}")
        self.tracker.wait(plan)

    def waiting_plan(self):
        """The plan that waits, as data: what it is, what changed, its criteria, what it leaves out."""
        project = self.session.project
        cycle = project.open_cycle()
        kind = "fix" if cycle.get("kind") == "fix" else "project" if cycle["id"] == "base" else "feature"
        return {"name": project.workdir.name, "run": self.record["id"], "language": project.language,
                "start": project.toolchain.run, "kind": kind, "changes": cycle.get("changes", []),
                "criteria": [(criterion["id"], criterion["behaviour"]) for criterion in project.cycle_criteria(cycle)],
                "non_goals": cycle.get("non_goals", []), "folder": str(project.workdir)}

    @staticmethod
    def print_plan(plan):
        runs = f"; a person runs it with: {plan['start']}" if plan["start"] else ""
        heading, icon = ("Bug shown for", "🐞") if plan["kind"] == "fix" else ("Plan for", "📋")
        say.title(f"{heading} {plan['name']} (run {plan['run']}): {plan['language']}{runs}", icon)
        with say.section():
            if plan["changes"]:
                with say.part("What changed:", "✏️"):
                    for line in plan["changes"]:
                        say.item(line)
            with say.part("Tested before any code is written:", "🧪"):
                for criterion_id, behaviour in plan["criteria"]:
                    say.text(f"{criterion_id}  {behaviour}")
            if plan["non_goals"]:
                say.info(f"Not included: {'; '.join(plan['non_goals'])}", "🚫")

    def summary_blocked(self):
        """Why it stopped: a bug no test showed, a project that does not build, tools that do not
        work, or criteria parked."""
        run_id = self.record["id"]
        lines = self.why_blocked(self.session, run_id)
        say.title(lines[0], Messages.ICONS["stop"])
        with say.section():
            for line in lines[1:]:
                say.text(line)
        self.ending.update(headline=lines[0], details=[("Then", line.strip()) for line in lines[1:]],
                           next=[("Continue it", f"tinker resume @{run_id}")])

    @staticmethod
    def why_blocked(session, run_id):
        """The lines that say why it stopped (the first is the headline) and how to go on."""
        if session.unreproduced:
            return [f"Not fixed: {session.unreproduced}"]
        if session.review_problem:
            return [f"Not done: the final review found {session.review_problem}",
                    f"Correct it (or the request), then: tinker resume @{run_id}"]
        if session.build_problem:
            return [f"Stopped before working on criteria: {session.build_problem}",
                    f"See what each fix did (tinker log @{run_id}); correct it by hand or continue with: "
                    f"tinker resume @{run_id}"]
        if session.tool_problem:
            return [f"Stopped before working on criteria: {session.tool_problem}",
                    "No code change can fix that. Check the commands in SPEC.md (setup, test_all) and the dependency",
                    f"file, then continue with: tinker resume @{run_id}"]
        green, parked, total = session.project.counts()
        return [f"Stopped: {green}/{total} tests pass; {parked} parked after fixing and a review against the plan:"] + \
            [f"  {criterion['id']} {criterion['title']}: {first_line(criterion.get('last_error', ''), 160)}"
             for criterion in session.project.parked()] + \
            [f"Change what it cannot (the request, the plan, a dependency), then: tinker resume @{run_id}"]


class ProjectFolder:
    """A new task's own folder in the projects folder (tinker config projects): named after the task by one
    quick call to the model (calc-py for a calculator in Python), checked, else from the task's own words;
    a name that is taken gets -2, -3 ..."""
    NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+){0,2}")
    NAME_MAX = 20
    COMMON = {"a", "an", "the", "build", "make", "create", "write", "simple", "small", "basic", "using", "use",
              "with", "in", "for", "of", "and", "to", "that", "which", "me", "my", "please", "app", "program"}

    @staticmethod
    def make(task):
        root = projects_folder()
        root.mkdir(parents=True, exist_ok=True)
        name = ProjectFolder.named(task) or ProjectFolder.from_words(task)
        folder, count = root / name, 1
        while folder.exists():
            count += 1
            folder = root / f"{name}-{count}"
        folder.mkdir()
        say.info(f"Project folder: {folder}", "📁")
        return folder

    @staticmethod
    def named(task):
        """The model's name for the task's folder ("" when it gives none that fits NAME_RE)."""
        model, settings = Models.selected()
        options, think = ProjectFolder.quick_call(settings)
        try:
            data = Ollama.chat(model, [{"role": "system", "content": Prompts.FOLDER_NAME_SYSTEM},
                                       {"role": "user", "content": task}],
                               Config.keep_alive(Config.get("keep_alive")), options, think=think, timeout=180)
        except OllamaError:
            return ""
        name = data["message"]["content"].strip().strip("`'\".").lower()
        return name if ProjectFolder.NAME_RE.fullmatch(name) and len(name) <= ProjectFolder.NAME_MAX else ""

    @staticmethod
    def quick_call(settings):
        """(options, think) of a call that names something: a run's num_ctx (the model stays loaded for the run),
        a few tokens, no thinking."""
        options = {"num_ctx": settings["num_ctx"], "num_predict": 24}
        if settings.get("num_batch"):
            options["num_batch"] = settings["num_batch"]
        return options, (False if settings.get("think") not in (None, False) else None)

    @staticmethod
    def from_words(task):
        kept = [word for word in re.findall(r"[a-z0-9]+", task.lower()) if word not in ProjectFolder.COMMON]
        name = "-".join(kept[:2])[:ProjectFolder.NAME_MAX].strip("-")
        return name if ProjectFolder.NAME_RE.fullmatch(name) else "project"


class CycleRunner:
    """A cycle of the project itself, of a feature (tinker add) or of a bug fix (tinker fix): Tinker's idea in
    order (PHASES, FIX_PHASES): plan the task as a spec, which waits for the person's confirm (CONFIRM_AFTER),
    install the test tool, a test for every spec point, just enough code to pass them, tidied, one final
    review, done. Resumable at each phase: a phase is saved as done once it ends. A phase that stops the
    cycle disproves the knowledge the cycle used in it."""

    def __init__(self, session):
        self.session, self.project = session, session.project

    def run(self, cycle, web=False):
        """Continue a cycle from its saved phase: "done", "blocked" (a phase stopped it) or "awaiting" (its
        plan waits for the person to confirm it). With web, a cycle past its recall phase recalls first, so
        the rest of it may learn what it needs."""
        phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
        self.session.cycle = cycle
        if web and cycle["phase"] not in ("recall", "done") and not cycle.get("awaiting"):
            self.recall_first(cycle)
        while cycle["phase"] != "done":
            if cycle.get("awaiting"):
                return "awaiting"
            phase = cycle["phase"]
            if self.session.tracker is not None:
                self.session.tracker.phase(phase)
            if self.run_phase(phase, cycle) is False:
                self.disprove(cycle, phase)
                return "blocked"
            if phase in FORMAT_AFTER:
                self.session.format_code()
            self.done_with(phase, cycle, phases)
            if phase == "review":
                self.show_plan()
        return "done"

    def recall_first(self, cycle):
        """A run with --web past its recall phase: what the rest of the cycle needs is learned first."""
        if self.session.tracker is not None:
            self.session.tracker.phase("recall")
        with say.part(f"{Recaller.TITLE} (with --web, before {cycle['phase']})", Tracker.icon("recall")):
            Recaller(self.session).run(cycle)

    def run_phase(self, phase, cycle):
        """Runs one phase; only setup, verify, the final review and reproduce can stop
        the cycle (they return False). The plan is made at the model's full think setting; building
        from it at build_think."""
        worker = self.phase_class(phase)(self.session)
        self.session.model.building = worker.BUILDS
        if not worker.TITLE:
            return worker.run(cycle)
        started = time.perf_counter()
        with say.part(worker.TITLE + (f" ({'project' if cycle['id'] == 'base' else 'feature ' + cycle['id']})"
                                      if phase == "spec" else ""), Tracker.icon(phase)):
            went = worker.run(cycle)
            if went is not False:
                say.took(Tracker.done_words(phase), time.perf_counter() - started)
        if self.session.tracker is not None:
            self.session.tracker.outcome(self.outcome(phase, cycle))
        return went

    def outcome(self, phase, cycle):
        """What came of a phase, as the person counts it: "9 point(s) to test", "4 file(s)", "6/9 spec points pass"."""
        criteria = self.project.cycle_criteria(cycle)
        green, _, total = self.project.counts()
        return {"spec": f"{len(criteria)} point(s) to test",
                "design": f"{len(cycle.get('files', []))} file(s)",
                "tests": f"{sum(TestRef(criterion['test']).present(self.session.workspace) for criterion in criteria)}"
                         f"/{len(criteria)} test(s) written",
                "code": f"{len(cycle.get('code_done', []))} file(s) written",
                "verify": f"{green}/{total} spec points pass"}.get(phase, "")

    @staticmethod
    def phase_class(phase):
        """The Phase that does a phase of a cycle (in the order of PHASES, then a bug fix's own)."""
        return {"recall": Recaller,
                "spec": SpecWriter,
                "design": Designer,
                "review": InterfaceReviewer,
                "setup": TestToolSetup,
                "tests": TestWriter,
                "code": CodeWriter,
                "verify": Verifier,
                "tidy": Tidy,
                "final_review": FinalReview,
                "docs": ReadmeWriter,
                "lessons": LessonWriter,
                "reproduce": BugReproducer}[phase]

    def done_with(self, phase, cycle, phases):
        """The cycle on to its next phase, saved; past the phase its plan waits after, it waits for the person
        to confirm it (tinker confirm) or change it (tinker amend), when its run was given --confirm."""
        cycle["phase"] = phases[phases.index(phase) + 1]
        if self.waits_for_confirm(phase, cycle):
            cycle["awaiting"] = True
        self.session.save()

    @staticmethod
    def waits_for_confirm(phase, cycle):
        """Whether the cycle's plan, once past this phase, waits for the person: its run was given --confirm,
        and it is not confirmed yet."""
        return (phase == CONFIRM_AFTER[cycle.get("kind", "feature")] and cycle.get("confirm")
                and not cycle.get("confirmed"))

    def disprove(self, cycle, phase):
        """The knowledge used in a step that failed is discarded (it did not hold)."""
        gone = self.session.knowledge.disprove(cycle.get("used", {}), self.session.failed_phases(phase))
        if gone:
            say.info(f"{gone} lesson(s) and fact(s) used in this step are discarded: they did not hold", "🗑️")
        self.session.save()

    def show_plan(self):
        with say.part(f"Plan: {len(self.project.criteria)} spec criteria (see SPEC.md)", "📋"):
            for criterion in self.project.criteria:
                say.text(f"{criterion['id']:5} [{STATUS_TEXT.get(criterion['status'], criterion['status'])}] "
                         f"effort {criterion.get('effort', 2)} {criterion['title']}  -> {criterion['test']}")

