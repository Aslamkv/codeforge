import re
import sys
from collections import namedtuple
lazy import argparse
lazy import datetime
lazy import functools
lazy import os
lazy import secrets
lazy import shutil
lazy import signal
lazy import time
lazy import traceback
lazy import webbrowser
lazy from pathlib import Path

from . import conversation, exercising, reading
from .commands import BY_NAME, COMMANDS, GROUPS, refs, taught_command
from .jobs import STATES, Jobs, archived, calls_log, job_number
from .messages import Messages, human_duration, paint, say
from .reading import Reader
lazy from .building import BugReproducer, CodeWriter, TestToolSetup, TestWriter, Verifier
lazy from .finishing import FinalReview, LessonWriter, ReadmeWriter, Tidy
lazy from .graph import Graph, MemoryBrowser, write_page
lazy from .knowledge import Embeddings, Learner, Library, Researcher, search_entry
lazy from .learning import LearnStopped, SlotLearner
lazy from .model import Calibrator, Model, Models, Ollama, OllamaError, SelectedModel
lazy from .planning import Designer, InterfaceReviewer, Recaller, SpecWriter
lazy from .project import (
    Brief,
    Outcome,
    Project,
    RunChecks,
    Runs,
    Steps,
    Tracker,
    ending,
    project_store,
    saved,
)
lazy from .prompts import Prompts
lazy from .session import Session, Stopped
lazy from .settings import (
    CONFIRM_AFTER,
    DEFAULT_MAX_STALL,
    ENV_EXAMPLE,
    ENV_FILE,
    FIX_PHASES,
    FORMATTER_RUNS_AFTER,
    NUMBERED_PARTS,
    PHASES,
    STATUS_TEXT,
    Config,
    Home,
    Numbered,
    projects_folder,
)
lazy from .store import Log
lazy from .teaching import Asker, Memory, Teacher
lazy from .text import clock, first_line, now_text
lazy from .web import Internet
lazy from .workspace import TaughtCommands, TestRef

try:
    from . import channels
except ImportError:
    channels = None


Renamed = namedtuple("Renamed", "project folder")
LearningTools = namedtuple("LearningTools", "model embeddings internet")
DoctorCheck = namedtuple("DoctorCheck", "required what check")


def main():
    script_started, started_at = time.perf_counter(), datetime.datetime.now()
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, ended_like_ctrl_c)
    try:
        argv = Cli.named(Jobs.arguments(sys.argv[1:]))
        with Jobs.running(argv):
            run(Cli.parse(argv), script_started, started_at)
    except KeyboardInterrupt:
        raise SystemExit("\nInterrupted.")
    except OllamaError as exc:
        raise SystemExit(f"\n{exc}")
    except OSError as exc:
        raise SystemExit(cannot_read_or_write(exc))


def ended_like_ctrl_c(*_):
    raise KeyboardInterrupt


def cannot_read_or_write(exc):
    return f"\n{exc.strerror or exc}: {exc.filename or ''}".rstrip(": ")


def run(args, script_started, started_at):
    if args.reset:
        return Home.reset(args.yes)
    Home.ensure()
    if Cli.works_for_hours(args):
        Home.keep_awake()
    if Commands(args).run():
        return
    ProjectRun(args, script_started, started_at).execute()


class Cli:
    EXAMPLES = (
        "tinker",
        "tinker doctor",
        "tinker task a calculator in Python",
        "tinker add keep a history of results",
        "tinker research the newest Python release",
        "tinker help task",
    )
    TALK_COMMANDS = tuple(command.name for command in COMMANDS if command.group == "Talk" and command.cli)
    TITLES = {
        "Build": "🔨 Build",
        "Learn": "🧠 Learn",
        "Jobs": "⏯ Jobs",
        "Set up": "🔧 Set up",
        "Talk": "💬 Talk",
        "Channels": "📡 Channels",
    }
    RUNS = tuple(command.name for command in COMMANDS if command.run and command.name != "confirm")
    COMMANDS = tuple(
        command.name
        for command in COMMANDS
        if command.cli and command.group != "Talk" and command.name not in ("help", "channel", "confirm")
    )
    FIELDS = {
        "reset": False,
        "config": None,
        "models": False,
        "use_model": None,
        "embed": None,
        "calibrate": None,
        "learn": None,
        "research": None,
        "ask": None,
        "teach": None,
        "read": None,
        "exercise": None,
        "memory": None,
        "runs": False,
        "status": None,
        "log": None,
        "jobs": None,
        "stop": None,
        "doctor": False,
        "task": None,
        "spec": None,
        "spec_file": None,
        "restart": False,
        "resume": None,
        "amend": None,
        "add": None,
        "fix": None,
        "workdir": None,
        "web": False,
        "confirm": False,
        "rename": None,
        "yes": False,
        "calls": False,
        "graph": False,
    }
    FOLDER_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")

    @staticmethod
    def parse(argv=None):
        argv = sys.argv[1:] if argv is None else argv
        Cli.run_at_once(argv)
        if argv[0] == "confirm":
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
        makes_its_own_folder = command == "task"
        if not makes_its_own_folder:
            args.workdir = args.workdir or "."
        return args

    @staticmethod
    def run_at_once(argv):
        if not argv:
            Home.ensure()
            conversation.session(Path.cwd())
            raise SystemExit(0)
        if argv[0] in ("help", "-h", "--help"):
            Cli.help(argv[1] if len(argv) > 1 else None)
        if argv[0] in Cli.TALK_COMMANDS:
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
        if parts[0] not in NUMBERED_PARTS:
            return Home.clear(parts, yes)
        numbers = [job_number(word) for word in parts[1:]]
        if None in numbers and parts[0] == "jobs":
            raise SystemExit("tinker clear jobs takes job numbers alone: tinker clear jobs #N (tinker jobs)")
        if None in numbers:
            raise SystemExit(
                "tinker clear read takes reading numbers alone: tinker clear read #N (tinker read list)"
            )
        if parts[0] == "jobs":
            return archived(numbers, yes)
        reading.forgotten(numbers, yes)

    @staticmethod
    def known_command(argv):
        if argv[0] not in Cli.COMMANDS:
            raise SystemExit(f"tinker: no command {argv[0]!r} (tinker help lists them)")
        return argv[0]

    @staticmethod
    def help(command=None):
        if command is None:
            say.text(Cli.overview_markup() if os.environ.get("TINKER_FORMAT") == "markup" else Cli.overview())
            raise SystemExit(0)
        if command == "channel" and channels is not None:
            say.text(channels.ChannelCommand.USAGE)
            raise SystemExit(0)
        if command in Cli.TALK_COMMANDS:
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
        paint = Cli.painter()
        out = [
            f"🛠  {paint('tinker', 'bold')} builds what you ask for with a local Ollama model: a small spec, a unit",
            "   test for each point, just enough code, and a README a person can follow.",
            "",
            f"{paint('Usage:', 'bold')} tinker {paint('COMMAND', 'cyan')} {paint('[ARGUMENTS]', 'yellow')} "
            f"{paint('[OPTIONS]', 'green')}   (a project: {paint('@ID', 'yellow')} or {paint('@folder', 'yellow')})",
        ]
        out += Cli.command_lines(paint) + Cli.option_lines(paint)
        out += ["", paint("💡 Examples", "bold")] + [
            f"  {paint('$', 'dim')} {example}" for example in Cli.EXAMPLES
        ]
        return "\n".join(out + ["", f"{paint('tinker help COMMAND', 'cyan')} shows one command in full."])

    @staticmethod
    def command_lines(paint):
        rows = [
            (group, row)
            for group in GROUPS
            for command in COMMANDS
            if command.group == group
            for row in command.rows
        ]
        width = max(len(synopsis) for _, (synopsis, _) in rows) + 2
        out = []
        for group in GROUPS:
            if group == "Channels" and channels is None:
                continue
            out += ["", paint(Cli.TITLES[group], "bold")]
            for synopsis, what in [row for of_group, row in rows if of_group == group]:
                name, _, arguments = synopsis.partition(" ")
                padding = " " * (width - len(synopsis))
                out.append(
                    f"  {paint(name, 'cyan')}{' ' + paint(arguments, 'yellow') if arguments else ''}"
                    f"{padding}{what}"
                )
        return out

    @staticmethod
    def option_lines(paint):
        options = Cli.options()
        width = max(len(name) for name in options) + 2
        return ["", paint("⚙️  Options", "bold")] + [
            f"  {paint(name, 'green')}{' ' * (width - len(name))}{what} "
            f"{paint('(' + ', '.join(commands) + ')', 'dim')}"
            for name, (what, commands) in options.items()
        ]

    @staticmethod
    def options():
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
        return functools.partial(paint, stream=sys.stdout)

    @staticmethod
    def overview_markup():
        out = ["🛠 **What you can send**"]
        for group in GROUPS:
            if group == "Channels" and channels is None:
                continue
            out += ["", f"**{Cli.TITLES[group]}**"]
            for command in [command for command in COMMANDS if command.group == group]:
                for synopsis, what in command.rows:
                    out.append(
                        "💬 anything else: a normal chat" if not command.name else f"- `/{synopsis}` {what}"
                    )
        return "\n".join(out + ["", "❓ `/help COMMAND` shows one command in full"])

    @staticmethod
    def parser(command):
        found = BY_NAME[command]
        parser = argparse.ArgumentParser(
            prog=f"tinker {command}", allow_abbrev=False, description=found.about
        )
        for argument in found.arguments:
            parser.add_argument(
                argument.dest,
                nargs=argument.nargs,
                metavar=argument.metavar,
                help=argument.help,
            )
        for option in found.options:
            if option.metavar:
                parser.add_argument(option.flag, dest=option.dest, metavar=option.metavar, help=option.help)
            else:
                parser.add_argument(option.flag, dest=option.dest, action="store_true", help=option.help)
        return parser

    @staticmethod
    def read_command(command, given, args, parser):
        value = Cli.command_value(command, given, parser)
        if value is not None:
            setattr(args, command.replace("-", "_"), value)
        if command in ("restart", "reset"):
            args.yes = given.yes
        named = {"status": args.status, "log": args.log, "rename": (args.rename or ("",))[0]}.get(command)
        if named and args.workdir is not None:
            parser.error(Cli.finds_its_folder(f"tinker {command}"))

    @staticmethod
    def command_value(command, given, parser):
        match command:
            case "reset" | "restart" | "models" | "runs" | "doctor":
                return True
            case "use-model" | "calibrate" | "embed" if given.name is None:
                return None
            case "use-model" | "calibrate" | "embed":
                return Cli.non_empty(given.name, command, parser)
            case "resume" | "status" | "log":
                return Cli.project_named(given.words, parser)
            case "add" | "fix" | "amend":
                return given.words
            case "rename":
                return Cli.renamed(given.words, parser)
            case "learn":
                return Cli.learning(given.words, parser)
            case "jobs" | "stop":
                return Cli.job_words(command, given.words, parser)
            case "read":
                return Cli.reading(given.words, parser)
            case "memory":
                return Cli.memory_words(given, parser)
            case "ask" | "teach" | "research" | "exercise":
                return Cli.non_empty(" ".join(given.words), command, parser)
            case "config":
                return Cli.config_words(given.words, parser)
        return None

    @staticmethod
    def config_words(words, parser):
        if len(words) not in (0, 2):
            parser.error("use tinker config (the settings) or tinker config KEY VALUE")
        return words

    @staticmethod
    def project_named(words, parser):
        name, rest = refs(words)
        if rest:
            parser.error(f"name a project as @ID or @folder (tinker runs lists them), not {' '.join(rest)!r}")
        return name

    @staticmethod
    def renamed(words, parser):
        name, rest = refs(words)
        if len(rest) != 1 or not Cli.FOLDER_RE.fullmatch(rest[0]):
            parser.error(
                "use tinker rename NAME or tinker rename @PROJECT NAME; a name is letters, digits, - _ "
                "and ., starting with a letter or digit"
            )
        return Renamed(name, rest[0])

    @staticmethod
    def job_words(command, words, parser):
        if command == "stop":
            number = job_number(words[0]) if len(words) == 1 else None
            if words and number is None:
                parser.error(
                    "use tinker stop (every job of yours that runs) or tinker stop #N (tinker jobs lists them)"
                )
            return number
        state = words[0] if words and words[0] in STATES else None
        rest = words[1:] if state else words
        if len(rest) > 1 or (rest and not Cli.is_page(rest[0])):
            parser.error(
                "use tinker jobs [running|paused|stopped|finished|archived] [PAGE], e.g. tinker jobs finished 2"
            )
        return state, int(rest[0]) if rest else 1

    @staticmethod
    def is_page(word):
        return word.isdigit() and int(word) >= 1

    @staticmethod
    def learning(words, parser):
        taught = taught_command(words)
        if taught:
            return list(taught)
        topic = " ".join(words).strip()
        if not topic:
            parser.error("use tinker learn WHAT (e.g. tinker learn Flask) or tinker learn NAME = DEFINITION")
        return [topic]

    @staticmethod
    def reading(words, parser):
        if words[0] in ("list", "show"):
            if Cli.lists_readings(words) or (words[0] == "show" and len(words) == 2 and job_number(words[1])):
                return words
            parser.error("use tinker read list [PAGE], or tinker read show #N")
        if job_number(words[0]):
            return words[:1] + ([" ".join(words[1:]).strip(" \"'“”")] if words[1:] else [])
        return [Cli.non_empty(" ".join(words), "read", parser)]

    @staticmethod
    def memory_words(given, parser):
        if given.graph and given.words:
            parser.error(f"--graph takes no other words; {MemoryBrowser.USAGE}")
        try:
            return MemoryBrowser.request(given.words)
        except ValueError as refused:
            parser.error(str(refused))

    @staticmethod
    def lists_readings(words):
        return words[0] == "list" and (len(words) == 1 or (len(words) == 2 and Cli.is_page(words[1])))

    @staticmethod
    def non_empty(value, command, parser):
        if not value.strip():
            parser.error(f"tinker {command}: the value is empty")
        return value.strip()

    @staticmethod
    def check_run(args, parser):
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
        words = list(args.task)
        last = Path(words[-1])
        if len(words) > 1 and not args.spec and Cli.is_spec_file(last):
            args.spec = words.pop()
        args.task = " ".join(words).strip()
        if not args.task:
            parser.error("give a task, e.g. tinker task a calculator in Python")

    @staticmethod
    def is_spec_file(path):
        return path.suffix in (".md", ".txt") and path.is_file()

    @staticmethod
    def check_request(kind, args, parser):
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
        return BY_NAME[args.command].kind == "job"

    @staticmethod
    def named(argv):
        if argv != ["calibrate"]:
            return argv
        name = Commands.picked_model("calibrate")
        if name is None:
            raise SystemExit(0)
        return [*argv, name]


class Commands:
    def __init__(self, args):
        self.args = args

    def run(self):
        run = self.handlers().get(self.args.command)
        if run is None:
            return False
        run()
        return True

    def handlers(self):
        args = self.args
        return {
            "config": lambda: self.config(args.config),
            "models": self.list_models,
            "use-model": lambda: self.use_model(args.use_model),
            "embed": lambda: self.embed(args.embed),
            "calibrate": self.calibrate,
            "learn": self.learn,
            "research": self.research,
            "ask": self.ask,
            "teach": self.teach,
            "read": self.read,
            "exercise": self.exercise,
            "memory": lambda: self.show_memory(args.graph, args.memory),
            "runs": self.list_runs,
            "jobs": lambda: self.list_jobs(*args.jobs),
            "stop": lambda: self.stop(args.stop),
            "status": lambda: self.show_status(args.status, args.workdir),
            "log": lambda: self.show_log(args.log, args.workdir, args.calls),
            "rename": lambda: self.rename(*args.rename, args.workdir),
            "doctor": lambda: Doctor().run(),
        }

    @staticmethod
    def config(words):
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
        for key in Config.settable():
            if key not in ("ollama_url",):
                say.text(f"{key + ':':16} {Config.get(key)!r}")
        say.text(f"web:             {Internet.state()}")
        embed = (
            Config.get("embed_model")
            or "none: select one with tinker embed NAME (tinker learn and --web need one)"
        )
        say.text(f"embed_model:     {embed}")
        say.text(
            "Set one with tinker config KEY VALUE (the model and the embedding model have their own commands)."
        )

    @staticmethod
    def model_state(name, settings):
        if not name:
            return "none (measure one with tinker calibrate NAME, then select it with tinker use-model NAME)"
        if settings:
            return f"{name} (calibrated {settings.get('calibrated', '?')})"
        return f"{name} (not measured: tinker calibrate {name})"

    @staticmethod
    def print_measured(settings):
        say.text(
            f"input budget:    {settings['max_prompt_tokens']} tokens"
            + (
                f" (largest read {settings['largest_prompt_read']}, crashed at {settings['crashed_reading']})"
                if settings.get("crashed_reading")
                else ""
            )
        )
        say.text(
            f"reply limit:     {settings['max_reply_tokens']} tokens (fixes at most {settings['fix_max_tokens']})"
        )
        say.text(f"context:         {settings['num_ctx']} tokens")
        think = settings.get("think")
        build = Config.get("build_think") if think not in (None, False) else None
        say.text(f"think:           {think} for planning" + (f", {build} for building" if build else ""))
        say.text(f"chars per token: {settings.get('chars_per_token')}")

    @staticmethod
    def list_models():
        models = Models.answering()
        say.text(
            f"  {'MODEL':36} {'SIZE':>7}  {'PROMPT':>6} {'READ/S':>7} {'WRITE/S':>7} {'SOLVED':>6}  CALIBRATED"
        )
        for model in models:
            name, s = model.get("name", ""), Models.settings(model.get("name", "")) or {}
            mark = "*" if name == Config.get("model") else " "
            say.text(
                f"{mark} {name[:36]:36} {model.get('size', 0) / 1e9:6.1f}G  {s.get('max_prompt_tokens') or '-':>6} "
                f"{s.get('read_tokens_per_s') or '-':>7} {s.get('write_tokens_per_s') or '-':>7} {Commands.solved(s):>6}  "
                f"{s.get('calibrated', 'not measured: tinker calibrate NAME')}"
            )
        say.text(
            "\n* the model in use. Measure one with tinker calibrate NAME, select it with tinker use-model NAME."
        )

    @staticmethod
    def solved(settings):
        if settings.get("solved") is None or not settings.get("quality_tasks"):
            return "-"
        return f"{settings['solved']}/{len(settings['quality_tasks'])}"

    @staticmethod
    def use_model(name):
        if name is None:
            name = Commands.picked_model()
            if name is None:
                return
        if Models.settings(name) is None:
            raise SystemExit(
                f"{name} has not been measured: measure it first with tinker calibrate {name} "
                f"(tinker models lists the models Ollama has)"
            )
        Config.save(model=name)
        say.done(f"Model in use: {name}")

    @staticmethod
    def picked_model(command="use-model"):
        choices = Models.command_choices(command) if sys.stdin.isatty() else []
        if not choices:
            Commands.list_models()
            return None
        return Models.picked(command, choices)

    @staticmethod
    def embed(name):
        if name is None:
            name = Commands.picked_model("embed")
            if name is None:
                return
        Models.installed(name)
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(
                f"{embeddings.failed}: choose an embedding model (tinker models lists the models Ollama has)"
            )
        Config.save(embed_model=name)
        say.done(
            f"Embedding model: {name}; what was learned is indexed with it from the next tinker learn or run on"
        )

    def calibrate(self):
        if self.args.calibrate is None:
            Commands.list_models()
            return
        Calibrator(Models.installed(self.args.calibrate)).run()

    def learn(self):
        learn = self.args.learn
        topic, definition = learn[0].strip(), (learn[1] if len(learn) > 1 else None)
        if definition is not None or TaughtCommands.alias(topic):
            TaughtCommands.teach(topic, definition)
            return
        model, embeddings, internet = self.learning_tools("tinker learn")
        Commands.reported(
            f"Learning {topic}",
            model,
            lambda tracker: self.learned(topic, SlotLearner(model, internet, embeddings, tracker)),
            blocked=lambda stopped: Commands.blocked(f"Nothing learned about {topic}", stopped),
        )

    @staticmethod
    def followed(title):
        tracker = Tracker(title)
        say.follow(tracker.heard)
        return tracker

    def learned(self, topic, learner):
        entry = learner.learn(topic, deep=True)
        if entry is None:
            raise SystemExit(f"Nothing learned about {topic}; nothing saved.")
        self.print_learned(topic, entry)
        return ending(
            "done",
            f"Learned {entry['name']}",
            [(name, text) for _, name, text in learner.recap]
            or [("Findings", str(len(entry.get("findings", []))))],
            [("See it", "tinker memory")],
        )

    @staticmethod
    def blocked(headline, stopped):
        details = (
            stopped.details if isinstance(stopped, LearnStopped) else [("Why", first_line(str(stopped), 200))]
        )
        return ending("blocked", headline, details)

    def research(self):
        topic = self.args.research
        model, embeddings, internet = self.learning_tools("tinker research")
        Commands.reported(
            f"Researching {topic}",
            model,
            lambda tracker: Commands.researched(
                topic, Commands.researcher(model, internet, embeddings, tracker).research(topic)
            ),
            blocked=lambda stopped: Commands.blocked(f"Nothing found about {topic}", stopped),
        )

    @staticmethod
    def researcher(model, internet, embeddings, tracker):
        learner = Learner(model, internet, embeddings)
        learner.tracker = tracker
        return Researcher(learner)

    @staticmethod
    def researched(topic, entry):
        if entry is None:
            raise SystemExit(f"Nothing found about {topic}; nothing saved.")
        details = [
            ("Findings", str(len(entry.get("findings", [])))),
            ("Facts", str(len(entry.get("facts", [])))),
            ("Pages", str(len(entry.get("sources", [])))),
        ]
        researched = ending("done", f"Researched {entry['name']}", details, [("See it", "tinker memory")])
        return {**researched, "text": Researcher.answer(entry)}

    @staticmethod
    def learning_tools(option):
        name, settings = Commands.checked_model()
        embeddings = Embeddings.required(option)
        return LearningTools(Commands.model_in_use(name, settings), embeddings, Internet())

    @staticmethod
    def checked_model():
        name, settings = Models.selected()
        Models.installed(name)
        Config.check()
        return SelectedModel(name, settings)

    @staticmethod
    def model_in_use(name, settings):
        log = calls_log()
        say.keep_log(log)
        return Commands.configured_model(name, settings, log)

    @staticmethod
    def configured_model(name, settings, log):
        return Model(
            name,
            settings,
            Config.keep_alive(Config.get("keep_alive")),
            log,
            max_growth=float(Config.get("max_growth")),
        )

    def ask(self):
        question = self.args.ask
        model = Commands.model_in_use(*Commands.checked_model())
        memory = Memory(Embeddings.configured())
        Commands.reported(
            f"Asking: {first_line(question, 60)}",
            model,
            lambda tracker: Asker(model, memory, tracker).answer(question),
        )

    def teach(self):
        teaching = self.args.teach
        name, settings = Commands.checked_model()
        internet = Internet()
        model, memory = Commands.model_in_use(name, settings), Memory(Embeddings.configured())
        Commands.reported(
            f"Teaching: {first_line(teaching, 60)}",
            model,
            lambda tracker: Teacher(model, internet, memory, tracker).teach(teaching),
        )

    def read(self):
        words = self.args.read
        if words[0] == "list":
            return reading.show_list(int(words[1]) if words[1:] else 1)
        if words[0] == "show":
            return reading.show(job_number(words[1]))
        number, focus = job_number(words[0]), " ".join(words[1:])
        if number is None:
            number, new = reading.stored(words[0])
            if not new:
                return
            Jobs.retarget(["read", f"#{number}"])
        title = reading.known_reading(number)["title"]
        model = Commands.model_in_use(*Commands.checked_model())
        Commands.reported(
            f"Reading #{number} {first_line(title, 50)}",
            model,
            lambda tracker: Reader(model, tracker).read(number, focus),
        )

    def exercise(self):
        what = self.args.exercise
        if what.lower() == "status":
            return exercising.show()
        model = Commands.model_in_use(*Commands.checked_model())
        Commands.reported(
            f"Exercising {what}",
            model,
            lambda tracker: exercising.Exercise(model, tracker).run(what),
            chats=True,
        )

    @staticmethod
    def reported(title, model, work, chats=False, blocked=None):
        tracker = Commands.followed(title)
        listening = conversation.attach(title, tracker) if chats else None
        started, ended = time.monotonic(), ending("stopped", f"{title} did not finish")
        try:
            ended = work(tracker)
        except SystemExit as stopped:
            ended = blocked(stopped) if blocked else ending("blocked", first_line(str(stopped), 200))
            raise
        finally:
            Models.remember(model)
            took = time.monotonic() - started
            say.title(f"Time taken: {human_duration(took)}", "⏱️")
            tracker.end(ended, took)
            if listening:
                listening.end()

    @staticmethod
    def print_learned(topic, entry):
        with say.part(f"{entry['name']}: {entry['summary']}", "📘"):
            for item in Library.items({Library.slug(entry.get("language") or topic): entry})[1:]:
                say.item(f"[{item['phase']}] {item['text']}")
            for contradiction in entry.get("contradictions", []):
                say.info(
                    f"Facts {', '.join(map(str, contradiction['facts']))} disagree: {contradiction['note']}",
                    "⚖️",
                )
            for gap in entry.get("open", []):
                missing = gap["missing"] or search_entry(gap["search"])["query"]
                say.info(f"Still open (Q{gap['question']}): {missing}", "❓")
            for note in entry["notes"]:
                say.warn(f"Not learned: {note}")
        say.tip(
            "A run uses this whenever its request needs it. See everything Tinker learned with: tinker memory"
        )

    @staticmethod
    def show_memory(graph=False, request=None):
        if graph:
            Commands.show_graph()
            return
        MemoryBrowser(Graph.build()).show(request or MemoryBrowser.request([]))

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
    def list_runs():
        entries = sorted(Runs.entries(), key=lambda entry: entry.get("updated", ""), reverse=True)
        if not entries:
            say.text("No runs yet.")
            return
        say.text(f"{'PROJECT':9} {'STATUS':14} {'UPDATED':17} {'FOLDER':32} TASK")
        for entry in entries:
            task = Commands.task_of(entry)
            say.text(
                f"{'@' + entry.get('id', '?'):9} {entry.get('status', '?'):14} {entry.get('updated', ''):17} "
                f"{Commands.short_folder(entry.get('workdir', '')):32} "
                f"{task[:60] + ('...' if len(task) > 60 else '')}"
            )

    @staticmethod
    def list_jobs(state, page):
        jobs = Jobs.listed(state)
        if not jobs:
            say.text(f"No {state or 'running, paused or stopped'} jobs.")
            return
        shown, pages = Numbered.page(jobs, page)
        say.text(f"{'JOB':6} {'STATE':9} {'STARTED':17} COMMAND")
        for job in shown:
            title = job["title"][:70] + ("..." if len(job["title"]) > 70 else "")
            say.text(f"{'#' + str(job['number']):6} {job['state']:9} {job['started']:17} {title}")
        if page < pages:
            say.text(
                f"\nPage {page} of {pages}: tinker jobs {(state + ' ') if state else ''}{page + 1} for more."
            )
        elif pages > 1 or page > 1:
            say.text(f"\nPage {page} of {pages}.")

    @staticmethod
    def stop(number):
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
            say.done(
                f"Stopping #{job['number']} {job['title']}: it saves itself; tinker resume #{job['number']} goes on."
            )

    @staticmethod
    def short_folder(workdir):
        home = str(Path.home())
        shown = ("~" + workdir[len(home) :]) if workdir.startswith(home) else workdir
        return ("..." + shown[-29:] if len(shown) > 32 else shown)[:32]

    @staticmethod
    def task_of(entry):
        task = entry.get("task", "").replace("\n", " ")
        features = entry.get("features")
        return f"[+{len(features)} feature{'s' if len(features) > 1 else ''}] " + task if features else task

    @staticmethod
    def show_status(name, workdir_arg):
        workdir = Runs.workdir(name, workdir_arg)
        run = Runs.read_record(workdir)
        spec = saved(workdir, "spec", "plan")
        say.text(
            f"Run {run['id']} ({run.get('status', '?')}, updated {run.get('updated', '?')}): {first_line(run['task'])}"
        )
        say.text(f"Project: {workdir}   Language: {(spec or {}).get('language', '?')}")
        if not spec or not spec.get("steps"):
            say.text("No spec yet (planned on the next tinker resume).")
            return
        Commands.print_criteria(spec["steps"])
        for cycle in spec.get("cycles", []):
            if cycle.get("phase") != "done":
                waits = " (its plan waits for your confirm)" if cycle.get("awaiting") else ""
                say.text(
                    f"  {'project' if cycle['id'] == 'base' else cycle.get('kind', 'feature') + ' ' + cycle['id']}"
                    f": next phase {cycle.get('phase')}{waits}"
                )

    @staticmethod
    def print_criteria(steps):
        say.text(f"{sum(1 for step in steps if step['status'] == 'green')}/{len(steps)} criteria pass")
        for step in steps:
            say.text(
                f"  {step['id']:5} {STATUS_TEXT.get(step['status'], step['status']):11} "
                f"{step['title'][:50]:50} {step['test']}"
            )
            if step["status"] == "blocked" and step.get("last_error"):
                say.text(f"        last error: {first_line(step['last_error'], 110)}")

    @staticmethod
    def show_log(name, workdir_arg, calls=False):
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
        for _, entry in store.entries("events"):
            say.text(
                f"[{entry['time']}] {entry.get('label') or entry.get('note', '')}"
                + (f"  error: {entry['error']}" if entry.get("error") else "")
            )
            if entry.get("reply"):
                say.text("\n".join(f"    {line}" for line in str(entry["reply"]).splitlines()[:40]))

    @staticmethod
    def rename(name, new, workdir_arg):
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
    def run(self):
        problems = 0
        for required, name, check in self.checks():
            try:
                found = check()
                say.done(f"{name}: {found}")
            except SystemExit as exc:
                problems += required
                (say.fail if required else say.warn)(f"{name}: {exc}")
        if not problems:
            say.title("Everything Tinker needs is there.", "🎉")
            return
        say.title(f"{problems} thing(s) to fix before a build can run.", Messages.ICONS["stop"])
        raise SystemExit(1)

    def checks(self):
        return [
            DoctorCheck(True, "Ollama", self.ollama),
            DoctorCheck(True, "model", self.model),
            DoctorCheck(False, "embedding model (for tinker learn and --web)", self.embedding),
            DoctorCheck(False, "web search", self.web),
            DoctorCheck(False, "tinker command", self.command),
        ]

    @staticmethod
    def ollama():
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(
                f"it does not answer at {Config.ollama_url()}: start it (ollama serve, or the Ollama "
                f"app), or set where it listens: tinker config ollama_url URL"
            )
        return f"answers at {Config.ollama_url()}, {len(models)} model(s) installed"

    @staticmethod
    def model():
        name, settings = Models.selected()
        Models.installed(name)
        return (
            f"{name}, measured {settings.get('calibrated', '?')}: reads {settings['max_prompt_tokens']} tokens, "
            f"writes up to {settings['max_reply_tokens']}"
        )

    @staticmethod
    def embedding():
        name = Config.get("embed_model")
        if not name:
            raise SystemExit(
                "none selected: pick one Ollama has with tinker embed NAME (tinker models lists them)"
            )
        Embeddings.check()
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(f"{embeddings.failed}: pick another with tinker embed NAME")
        return f"{name}, working"

    @staticmethod
    def web():
        return Internet.state()

    @staticmethod
    def command():
        found = shutil.which("tinker")
        if not found:
            script = Path(__file__).resolve().parent.parent / "tinker.py"
            raise SystemExit(
                f"not on PATH, so it is typed as python3 {script}: to type tinker anywhere, run "
                f"{script.parent / 'install.sh'}"
            )
        return found


class ProjectRun:
    def __init__(self, args, script_started, started_at):
        self.args = args
        self.script_started, self.started_at = script_started, started_at
        self.session = self.record = self.mode = self.log = None
        self.internet = None
        self.spec_text = ""
        self.outcome = Outcome()
        self.stopped = None
        self.tracker = Tracker(args.task or "a task")

    def execute(self):
        workdir = self.check_requirements()
        if self.args.restart:
            spec = self.args.spec
            Runs.clear_folder(workdir, Path(spec).expanduser().resolve() if spec else None, self.args.yes)
        say.follow(self.tracker.heard)
        self.open()
        listening = conversation.attach(self.tracker.state["title"], self.tracker)
        try:
            self.work_safely()
            self.finish()
        finally:
            if listening:
                listening.end()
        sys.exit(self.outcome.exit_code)

    def check_requirements(self):
        Models.installed(Models.selected()[0])
        Config.check()
        TaughtCommands.stop_if_broken()
        if self.args.web:
            Embeddings.required("--web")
            self.internet = Internet()
        else:
            Embeddings.check()
        makes_its_own_folder = self.args.workdir is None
        if makes_its_own_folder:
            if self.args.spec:
                Runs.read_spec_file(self.args.spec)
            self.args.workdir = str(ProjectFolder.make(self.args.task))
        return RunChecks.before_start(self.args)

    def open(self):
        args = self.args
        model_name, settings = Models.selected()
        self.record, workdir, self.spec_text, self.mode = Runs.open(args)
        project = Project.load(workdir, self.record)
        self.tracker.project(
            project.store, self.record["id"], workdir, f"{workdir.name}: {self.record['task']}"
        )
        Jobs.retarget(["resume", f"@{self.record['id']}"])
        self.log = Log(project.store, "events")
        say.keep_log(self.log)
        model = Commands.configured_model(model_name, settings, self.log)
        self.record["model"] = model.name
        self.print_header(model, workdir)
        self.log_start(model)
        self.session = Session(
            model, project, DEFAULT_MAX_STALL, self.internet, confirm=args.confirm, tracker=self.tracker
        )
        if project.language:
            say.info(f"Learned so far: {self.session.knowledge.summary()}", "🧠")

    def log_start(self, model):
        self.log.add(
            {
                "label": "session start",
                "mode": self.mode,
                "model": model.name,
                "started": clock(self.started_at),
            }
        )

    def print_header(self, model, workdir):
        run, args = self.record, self.args
        with say.part(f"Run {run['id']} ({self.doing()}): {run['task']}", "🚀"):
            if self.mode == "change":
                say.info(f"Request: {first_line(args.change_request, 200)}", "✏️")
            say.info(f"Project: {workdir}", "📁")
            say.info(f"Model: {model.name}; started {clock(self.started_at)}", "🤖")
        say.detail(
            f"model {model.name} (calibrated {model.settings.get('calibrated', '?')})   context {model.num_ctx}   "
            f"input budget {model.max_tokens} tok   think {self.thinking(model)}   keep_alive {model.keep_alive}   "
            f"reply limit per call (thinking included): {model.max_reply} tokens; fixes at most {model.fix_max}"
        )

    def doing(self):
        if self.mode == "change":
            return "fixing a bug" if self.args.bug else "adding a feature"
        if self.mode == "resume":
            return "resuming"
        return "restarting" if self.args.restart else "new"

    @staticmethod
    def thinking(model):
        if model.build_think and model.build_think != model.think:
            return f"{model.think} (building: {model.build_think})"
        return model.think

    def work_safely(self):
        try:
            self.work()
        except OllamaError as exc:
            say.title(str(exc), Messages.ICONS["fail"])
        except KeyboardInterrupt:
            say.title("Interrupted", Messages.ICONS["stop"])
        except SystemExit as exc:
            if exc.code not in (None, 0) and isinstance(exc.code, str):
                say.title(exc.code, Messages.ICONS["stop"])
        except Exception as exc:
            details = traceback.format_exc()
            say.title(
                f"Unexpected error: {type(exc).__name__}: {first_line(str(exc), 300)}", Messages.ICONS["fail"]
            )
            with say.section():
                say.text(details.rstrip())
            self.log.add({"label": "unexpected error", "error": details})
        finally:
            self.save()

    def work(self):
        session, run = self.session, self.record
        brief = Brief.load(session.model, session.project.store, self.mode, run["task"], self.spec_text)
        cycle = self.choose_cycle(brief)
        if cycle is not None and cycle.get("awaiting"):
            self.confirm_or_amend(cycle)
        runner = CycleRunner(session)
        if cycle is None:
            runner.show_plan()
            say.done(
                "Every cycle of this run is done; add a feature with tinker add, or fix a bug with tinker fix"
            )
            self.outcome.set("done")
            return
        self.outcome.set(runner.run(cycle, web=self.internet is not None))
        self.stopped = runner.stopped

    def choose_cycle(self, brief):
        project = self.session.project
        cycle = project.open_cycle()
        if self.mode == "change":
            cycle = self.new_change()
        elif cycle is None and not project.cycles:
            cycle = project.new_cycle("base", self.record["task"], brief)
            cycle["confirm"] = self.session.confirm
            project.save()
        return cycle

    def new_change(self):
        session, run, args, project = self.session, self.record, self.args, self.session.project
        feature_id = secrets.token_hex(3)
        kind = "fix" if args.bug else "feature"
        feature_brief = (
            Brief.make(session.model, args.change_request, self.spec_text)
            if self.spec_text and not args.bug
            else ""
        )
        cycle = project.new_cycle(feature_id, args.change_request, feature_brief, kind)
        cycle["confirm"] = session.confirm
        run.setdefault("features", []).append(
            {"id": feature_id, "request": args.change_request, "kind": kind, "added": now_text()}
        )
        Runs.save(run, project.workdir)
        project.save()
        return cycle

    def confirm_or_amend(self, cycle):
        if self.args.amend:
            say.title("Plan: changing it as you asked", "✏️")
            self.tracker.phase("amend")
            self.amend(cycle, self.args.amend)
        else:
            cycle["awaiting"], cycle["confirmed"] = False, True
            say.done("Plan confirmed: building it")
            self.session.save()

    def amend(self, cycle, change):
        if cycle.get("kind") != "fix":
            SpecWriter(self.session).amend(cycle, change)
            return
        cycle.setdefault("amendments", []).append(change)
        cycle.update(phase="reproduce", awaiting=False, confirmed=False)
        self.session.save()

    def save(self):
        model, project = self.session.model, self.session.project
        Models.remember(model)
        self.record["status"] = self.outcome.status
        Runs.save(self.record, project.workdir)
        Runs.remember(self.record, project.language)
        try:
            project.save()
        except OSError:
            pass

    def finish(self):
        self.print_summary()
        ended_at = datetime.datetime.now()
        wall = (ended_at - self.started_at).total_seconds()
        times = {"started": clock(self.started_at), "ended": clock(ended_at), "wall_seconds": round(wall, 1)}
        self.log.add({"label": "session end", "status": self.outcome.status, **times})
        self.record["last_session"] = times
        Runs.save(self.record, self.session.project.workdir)
        total = time.perf_counter() - self.script_started
        say.title(
            f"Time taken: {human_duration(wall)} (started {clock(self.started_at)}, ended {clock(ended_at)})",
            "⏱️",
        )
        say.detail(f"total execution time: {total:.2f}s")
        self.tracker.end(self.ending, total)

    def print_summary(self):
        status = self.outcome.status
        self.ending = ending(status, "")
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
        if failing:
            headline = (
                f"Done, but {len(failing)} README example(s) do not print what they say (all "
                f"{len(project.criteria)} spec tests pass)"
            )
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
            say.tip(
                "The tests use sample data instead of external services: try those by hand with real settings."
            )
        say.tip(f'Add a feature with: tinker add @{run_id} "..."; fix a bug with: tinker fix @{run_id} "..."')
        self.ending["next"] = [
            ("➕ Add a feature", f'tinker add @{run_id} "..."'),
            ("🐞 fix a bug", f'tinker fix @{run_id} "..."'),
        ]

    def summary_awaiting(self):
        run_id = self.record["id"]
        plan = self.waiting_plan()
        self.print_plan(plan)
        say.info(f"Its folder: {plan['folder']}   (rename it: tinker rename @{run_id} NAME)", "📁")
        say.tip(f"Build it: tinker confirm @{run_id}   (in the chat: /confirm)")
        say.tip(f"Change it: tinker amend @{run_id} CHANGE   (in the chat: /amend WHAT)")
        self.ending.update(headline=f"Its plan waits for your confirm: tinker confirm @{run_id}")
        self.tracker.wait(plan)

    def waiting_plan(self):
        project = self.session.project
        cycle = project.open_cycle()
        kind = "fix" if cycle.get("kind") == "fix" else "project" if cycle["id"] == "base" else "feature"
        return {
            "name": project.workdir.name,
            "run": self.record["id"],
            "language": project.language,
            "start": project.toolchain.run,
            "kind": kind,
            "changes": cycle.get("changes", []),
            "criteria": [
                (criterion["id"], criterion["behaviour"]) for criterion in project.cycle_criteria(cycle)
            ],
            "non_goals": cycle.get("non_goals", []),
            "folder": str(project.workdir),
        }

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
        run_id = self.record["id"]
        lines = self.why_blocked(self.stopped, self.session.project, run_id)
        say.title(lines[0], Messages.ICONS["stop"])
        with say.section():
            for line in lines[1:]:
                say.text(line)
        self.ending.update(
            headline=lines[0],
            details=[("Then", line.strip()) for line in lines[1:]],
            next=[("Continue it", f"tinker resume @{run_id}")],
        )

    @staticmethod
    def why_blocked(stopped, project, run_id):
        kind, reason = stopped or (None, "")
        if kind == "unreproduced":
            return [f"Not fixed: {reason}"]
        if kind == "review":
            return [
                f"Not done: the final review found {reason}",
                f"Correct it (or the request), then: tinker resume @{run_id}",
            ]
        if kind == "build":
            return [
                f"Stopped before working on criteria: {reason}",
                f"See what each fix did (tinker log @{run_id}); correct it by hand or continue with: "
                f"tinker resume @{run_id}",
            ]
        if kind == "tools":
            return [
                f"Stopped before working on criteria: {reason}",
                "No code change can fix that. Check the commands in SPEC.md (setup, test_all) and the dependency",
                f"file, then continue with: tinker resume @{run_id}",
            ]
        return ProjectRun.parked_report(project, run_id)

    @staticmethod
    def parked_report(project, run_id):
        green, parked, total = project.counts()
        return [
            f"Stopped: {green}/{total} tests pass; {parked} parked after fixing and a review against the plan:",
            *(
                f"  {criterion['id']} {criterion['title']}: {first_line(criterion.get('last_error', ''), 160)}"
                for criterion in project.parked()
            ),
            f"Change what it cannot (the request, the plan, a dependency), then: tinker resume @{run_id}",
        ]


class ProjectFolder:
    NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+){0,2}")
    NAME_MAX = 20
    NAME_TOKENS = 24
    COMMON = {
        "a",
        "an",
        "the",
        "build",
        "make",
        "create",
        "write",
        "simple",
        "small",
        "basic",
        "using",
        "use",
        "with",
        "in",
        "for",
        "of",
        "and",
        "to",
        "that",
        "which",
        "me",
        "my",
        "please",
        "app",
        "program",
    }

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
        if not Models.quick():
            return ""
        try:
            reply = Models.quick_reply(
                [
                    {"role": "system", "content": Prompts.FOLDER_NAME_SYSTEM},
                    {"role": "user", "content": task},
                ],
                ProjectFolder.NAME_TOKENS,
            )
        except OllamaError:
            return ""
        name = reply.strip("`'\".").lower()
        return name if ProjectFolder.NAME_RE.fullmatch(name) and len(name) <= ProjectFolder.NAME_MAX else ""

    @staticmethod
    def from_words(task):
        kept = [word for word in re.findall(r"[a-z0-9]+", task.lower()) if word not in ProjectFolder.COMMON]
        name = "-".join(kept[:2])[: ProjectFolder.NAME_MAX].strip("-")
        return name if ProjectFolder.NAME_RE.fullmatch(name) else "project"


class CycleRunner:
    def __init__(self, session):
        self.session, self.project = session, session.project
        self.stopped = None

    def run(self, cycle, web=False):
        steps = Steps(FIX_PHASES if cycle.get("kind") == "fix" else PHASES, self.session.tracker)
        self.session.cycle = cycle
        if web and cycle["phase"] not in ("recall", "done") and not cycle.get("awaiting"):
            self.recall_first(cycle, steps)
        went = steps.run(
            cycle["phase"],
            lambda phase: self.phase_done(phase, cycle),
            lambda phase: self.reached(phase, cycle),
            waits=lambda: cycle.get("awaiting"),
        )
        return "awaiting" if went == "waiting" else went

    def phase_done(self, phase, cycle):
        if self.run_phase(phase, cycle) is False:
            self.disprove(cycle, phase)
            return False
        if phase in FORMATTER_RUNS_AFTER:
            self.session.format_code()
        if self.waits_for_confirm(phase, cycle):
            cycle["awaiting"] = True
        if phase == "review":
            self.show_plan()
        return True

    def reached(self, phase, cycle):
        cycle["phase"] = phase
        self.session.save()

    def recall_first(self, cycle, steps):
        steps.enter("recall")
        with say.part(f"{Recaller.TITLE} (with --web, before {cycle['phase']})", Tracker.icon("recall")):
            Recaller(self.session).run(cycle)

    def run_phase(self, phase, cycle):
        worker = self.phase_class(phase)(self.session)
        self.session.model.is_building = worker.BUILDS
        if not worker.TITLE:
            return self.result(worker.run(cycle))
        started = time.perf_counter()
        with say.part(self.phase_title(worker, phase, cycle), Tracker.icon(phase)):
            went = self.result(worker.run(cycle))
            if went is not False:
                say.took(Tracker.done_words(phase), time.perf_counter() - started)
        if self.session.tracker is not None:
            self.session.tracker.outcome(self.outcome(phase, cycle))
        return went

    @staticmethod
    def phase_title(worker, phase, cycle):
        if phase != "spec":
            return worker.TITLE
        return f"{worker.TITLE} ({'project' if cycle['id'] == 'base' else 'feature ' + cycle['id']})"

    def result(self, went):
        if isinstance(went, Stopped):
            self.stopped = went
            return False
        return went

    def outcome(self, phase, cycle):
        criteria = self.project.cycle_criteria(cycle)
        green, _, total = self.project.counts()
        return {
            "spec": f"{len(criteria)} point(s) to test",
            "design": f"{len(cycle.get('files', []))} file(s)",
            "tests": f"{sum(TestRef(criterion['test']).present(self.session.workspace) for criterion in criteria)}"
            f"/{len(criteria)} test(s) written",
            "code": f"{len(cycle.get('code_done', []))} file(s) written",
            "verify": f"{green}/{total} spec points pass",
        }.get(phase, "")

    @staticmethod
    def phase_class(phase):
        return {
            "recall": Recaller,
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
            "reproduce": BugReproducer,
        }[phase]

    @staticmethod
    def waits_for_confirm(phase, cycle):
        return (
            phase == CONFIRM_AFTER[cycle.get("kind", "feature")]
            and cycle.get("confirm")
            and not cycle.get("confirmed")
        )

    def disprove(self, cycle, phase):
        gone = self.session.knowledge.disprove(
            cycle.get("used", {}), self.session.failed_phases(phase, self.stopped)
        )
        if gone:
            say.info(f"{gone} lesson(s) and fact(s) used in this step are discarded: they did not hold", "🗑️")
        self.session.save()

    def show_plan(self):
        with say.part(f"Plan: {len(self.project.criteria)} spec criteria (see SPEC.md)", "📋"):
            for criterion in self.project.criteria:
                say.text(
                    f"{criterion['id']:5} [{STATUS_TEXT.get(criterion['status'], criterion['status'])}] "
                    f"effort {criterion.get('effort', 2)} {criterion['title']}  -> {criterion['test']}"
                )
