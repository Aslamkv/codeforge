#!/usr/bin/env python3
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


Tinker builds exactly what a request asks for with a model run by Ollama. Language- and
app-neutral: the model picks the language (the one the request names, else Python), its usual
tools and the kind of app the request implies; Tinker plans with it, runs the commands the plan
names, and judges them by their exit codes and output.

Usage (a project is named as @ID or @folder; without one, it is this folder's):
    tinker                                talk with Tinker here: every command below, and a normal chat
    tinker task a calculator in Python [--spec plan.md] [--web] [--no-confirm]   plan it, then build it
    tinker restart [--yes]                build this folder's task again from scratch (asks first)
    tinker resume [@PROJECT] | tinker confirm [@PROJECT] | tinker amend [@PROJECT] CHANGE
    tinker rename [@PROJECT] NAME         rename the folder of a project whose plan waits
    tinker add [@PROJECT] Add a feature [--spec feature.md] [--web] [--no-confirm]
    tinker fix [@PROJECT] Describe a bug: what you do, what happens, what should happen [--web] [--no-confirm]
    tinker status [@PROJECT] | tinker runs | tinker log [@PROJECT] [--calls]
    tinker chat MESSAGE | tinker command WHAT | tinker research TOPIC | tinker stop | tinker emergency MESSAGE
    tinker learn WHAT                     learn a language, a tool or a domain (Tinker tells which)
    tinker learn NAME ["DEFINITION"]      a command of yours Tinker may use (an alias in your shell)
    tinker doctor                         what is missing or wrong, and the command that fixes it
    tinker models | tinker calibrate NAME | tinker use-model NAME | tinker embed NAME | tinker use-web NAME
    tinker config [KEY VALUE] | tinker memory | tinker clear PART... | tinker reset [--yes]
    tinker help [COMMAND]                 every command, or one command's arguments and options
How a run thinks, how long the model stays loaded and when it is unloaded are Tinker's to decide,
from the measured model and its settings (tinker config KEY VALUE tunes them).

The terminal and a chat are one interface (the conversation): tinker with no command, or a chat app
(tinker channel start), takes every command as its words (no quotes: task a calculator in Python), and
anything else as a normal chat; tinker COMMAND ... says one of them once. A plan waits for confirm (or
amend, rename) before anything is built, everywhere, unless --no-confirm; a command that deletes shows
what first. A chat that opens the bot (/start) is welcomed with a short guide.

Tinker has no model of its own choosing: measure one (tinker calibrate NAME), select it (tinker use-model
NAME), and every run uses that one. Every command is explicit: one command or one run at a time,
names as Ollama lists them, and an option the command would not use is an error, not ignored. All
that can be checked without the model (the values, the selected model, the project folder, the
run, the spec file, the open cycle) is checked before a folder is emptied or the model is loaded;
nothing is measured unasked.

The idea: build only what the request clearly asks for. Plan it as a small spec; turn every spec
point into one unit test (with the test tool installed and working first); write just enough code to
pass them, so there is no code the spec does not ask for; review it once at the end, the way a person
will run it; then it is done. Anything else waits until the user asks for it (tinker add, tinker fix). The
design follows the task: a one-file task gets one code file, its tests and no design calls.

  0. Recall      the learned topics the request names or is close to in meaning (no model call);
                 with --web, one call also names topics to learn now, and they are learned.
  1. Spec        one call: the language; the criteria, each one behaviour the request asks for (or
                 obviously needs), of one module, with one fast unit test through its public interface,
                 quoting the request words it comes from; the fewest files that hold them and what uses
                 what; the file a user starts, only when the request asks for a program to run;
                 settings (.env.example) only when it names some; for a new project the toolchain (the
                 one that worked before for the language, reused). An external service sits behind one
                 thin adapter with no criteria, and the code using it takes it as a parameter, so tests
                 pass sample data.
  2. Design      only where files meet: the contract of each code file another file uses (its exact
                 names, signatures, data shapes and strings), written after the code it uses.
  3. Review      a file whose contract uses two or more contracted files, checked against them.
  4. Setup       the config files, if any, in one call; set up; the test tool shown to run.
  5. Spec tests  a few per call, then the test tool must reach them.
  6. Code        one call per file in dependency order: just enough to pass the tests.
  7. Verify      the tools still work and the project must build (its check command loads every
                 module: a missing dependency, a bad import or a syntax error gets a fix that sees the
                 error, the files it names and the dependency file), then each failing criterion: fix
                 the code until its test passes; when fixes stop making progress, one review against
                 the plan decides whether the test or the code is wrong. A test that does not finish
                 fails after test_timeout. A change that breaks a passing test is reverted; what nothing
                 helps is parked.
  8. Tidy        the language's linter over the code (after the formatter and the linter's own safe
                 fixes); each file it reports gets one fix, kept only if every test still passes.
  9. README      for a beginner: what it is, every feature, and tldr-style usage (each command with
                 what it prints), from one call; setup, settings, run and tests from the toolchain. It
                 is also the acceptance test: every example is run as a user runs it (a server is
                 started first) and must print what the README says; a failing one gets a fix of the
                 code or of the README, whichever is wrong, kept only while every test passes.
 10. Final review  once, no model call: set up afresh from the project's own files, the whole test
                 suite run with the test command SPEC.md and the README give (as a person runs it),
                 and every spec point's test there and passing. A tooling problem gets one toolchain
                 fix; what still fails is said plainly, and the run is not done.
 11. Lessons     one call turns the problems the run solved into lessons for the next projects.
A plan waits for the person's confirm after the review (a bug fix: once the bug is reproduced), unless
--no-confirm; tinker amend CHANGE plans it again, and it waits again.

Learning: a model knows what it was trained on, as it was then. tinker learn WHAT tells what WHAT is
(one planning call; a topic learned before keeps its kind) and learns it the way that fits it:
  a programming language as it is today, e.g. "Python 3.14 with pytest, black and venv". Every
      language gets the same sections, each with its own searches and share of the facts:
      fundamentals (syntax, keywords, types, errors, modules, the concurrency and runtime model),
      what is new (the newest stable releases), its tools (one per role: environment, packages,
      build, tests, format, lint: the ones named, else the usual ones; how to install and run each)
      and conventions. Kept under the language's one name (python for any Python); every project in it uses it.
  a tool: a framework, a library, a technique such as TDD: a few searches, at least one for each
      thing it names, on how to use it today and what changed.
  a domain: a field, a practice or a standard (e.g. "food bank inventory"), studied the way a
      careful person does: split into research questions, each searched on several
      sites; facts that make the same claim on different sites are found by meaning (the embedding
      model, no model call) and corroborate each other when their numbers agree; one call per round
      answers each question from the facts alone, with its confidence, and names the contradictions
      and the gaps; a second round searches the gaps and looks for the key facts one site states on
      others. A finding is never surer than its facts allow. Kept with what tinker learn learns: the
      findings go to the spec and design prompts, every fact with its sources and confidence.
  a command of yours: tinker learn NAME "DEFINITION", or an alias in your shell (tinker learn activate) with
      no model call. The toolchain uses it where it fits (aliases are
      defined in each command's shell, where your shell's own are not); SPEC.md and the README show
      it spelled out.
A run with --web learns what it needs the quick way (a domain too). For a language and a tool, the
commands that show what is installed here run; the searches' pages
are read in turn (every search's first result, then the second ...) and condensed into facts, each
kept with its source, unreleased versions skipped.
A run uses them through its first phase, Recall: everything learned is searched for what the
request needs, by words (BM25: exact names and versions) and by meaning (the embedding model
selected with tinker embed NAME, which learning and --web need), the two rankings fused, so what either
finds is not missed; one call then picks the topics to use from the most related, and the project's
language as learned is always used; their facts, ranked the same way, go into the prompts of the
phases they concern, as an optional part (they never push out what is required). A run started with
--web may also learn then the topics it needs that are not learned yet (up to learn_missing).
The web is the Internet plugin: web search and web fetch through a backend (the setting "web", set by
tinker use-web NAME:
"ollama" is Ollama's web search and fetch, with a free ollama.com key in OLLAMA_API_KEY;
"duckduckgo" needs no key: DuckDuckGo's results, each page read from its own server as text) and,
only if set, a fallback ("web_fallback", e.g. "duckduckgo"; "" by default), asked whatever the first
cannot answer (and alone when the first is not usable). A new backend
is a WebBackend. A timeout or a rate limit is asked again once; a backend unreachable twice in a
row is left for the rest of the command. It is used only when asked: by learning, and by a run with
--web; nothing else goes online. Asked for and not usable, it says why before anything starts; lost
part way, the run carries on with what was learned before. A page is read from its top and from
the parts the word search finds the most related to what is looked for; facts about an unreleased
version are never kept.

Each run is faster than the last. Tinker keeps a folder next to this script, .tinker/, with one
SQLite database, tinker.db (readable by its owner only): its settings (tinker config), what calibration
measured for each model, per language the toolchain that worked, toolchain fixes, lessons and the facts
its projects proved, lessons for any language, what was learned from the web (each fact with its source),
the embedding of each learned text, your own commands, a pointer to each project (its run ID, folder and
task), the chat channels and their tokens, and each run's live tracker. What it learns starts as a
candidate: a cycle that succeeds using it proves it, and a failure in the step it was meant to help
discards it. Each project keeps its own state in its .tinker/project.db (its run, its plan, its model calls,
what it printed): tinker status, tinker log. tinker memory shows what Tinker knows; nothing is edited by
hand; tinker clear PART forgets a part, tinker reset everything.

Every request stays within the model's measured input budget, system prompt included (for a GGUF
model, calibration also tries Ollama's num_batch, which makes a runner read a long prompt in
pieces); replies are streamed and stopped once complete (models may run on with junk to the token
cap). Thinking has a budget: a check of a plan or an app thinks less than its making, a fix less
still; a reply cut off while still thinking is asked again without thinking, given the thought so
far to conclude, so minutes of thought are never thrown away. While a run or learning goes on, the
computer is kept from sleeping (caffeinate on macOS, systemd-inhibit on Linux).

Files are small modules written whole (no language-specific merging). A file too big for a prompt
is shown as numbered lines around what matters and changed with a <<<LINES path A-B>>> block.

Commands come from the plan and run in a POSIX shell in the project folder (listed in SPEC.md;
sudo, deleting outside the project and piping downloads into a shell are refused). Each project
keeps its spec, run and logs in its own .tinker folder; tinker log shows what a run printed.

How the code reads: this file is the main system, top-down: main() first, then the command line
(Cli, Commands), a run (ProjectRun) and the cycle of phases (CycleRunner). The parts it uses
are the modules of tinkerkit/, one responsibility each, each using only those listed above it:
  settings   every tunable number and fixed name; the home (Home), its database (Store), Config; helpers
  commands   the one table of commands, which the command line and the conversation both read
  prompts    prompt parts, replies and every system prompt (Part, Reply, Prompts)
  workspace  the project's files and the commands run in it (Workspace, Toolchain, Output, TestRef)
  model      the local model (Ollama, Model and the Tries of one request, Models, Calibrator)
  project    the plan and its record (Project, SpecDocument, Runs, RunChecks)
  web        the Internet plugin, the only part that goes online (Internet, OllamaWeb)
  conversation  the terminal and the chats as one: every command, a chat, research (Desk, Channel, Talk)
  channels   the chat plugin: Telegram, and tinker channel (TelegramChannel, Channels)
  knowledge  lessons (Knowledge) and learning (Learner, Researcher, Library, Retriever, Embeddings)
  session    what every phase works through (Session), and the phase contract (Worker, Phase)
  planning   recall, the spec, the contracts where files meet, and their review
  building   setup, the tests, the code, and fixing until every test passes
  finishing  tidy, the final review, the README and the lessons
Patterns and texts that only one class uses are defined in that class.
"""

import argparse
import datetime
import os
import re
import secrets
import shutil
import sys
import time
import traceback
import webbrowser
from pathlib import Path

# Checked before tinkerkit, which an older Python cannot even read; so that this check runs there, this file
# keeps to syntax Python 3.9 reads (no match, no t-strings).
if sys.version_info < (3, 14):
    raise SystemExit(f"Tinker needs Python 3.14 or newer; this is {sys.version.split()[0]}. "
                     f"On a Mac: brew install python")

from tinkerkit.building import BugReproducer, CodeWriter, TestToolSetup, TestWriter, Verifier
from tinkerkit.commands import BY_NAME, COMMANDS, GROUPS, refs
from tinkerkit.finishing import FinalReview, LessonWriter, ReadmeWriter, Tidy
from tinkerkit.graph import write_page
from tinkerkit.knowledge import Embeddings, Learner, Library
from tinkerkit.model import Calibrator, Model, Models, Ollama, OllamaError
from tinkerkit.planning import Designer, InterfaceReviewer, Recaller, SpecWriter
from tinkerkit.project import Brief, Outcome, Project, RunChecks, Runs, Tracker, project_store, saved
from tinkerkit.prompts import Prompts
from tinkerkit.session import Session
from tinkerkit.settings import (
    CONFIRM_AFTER, Config, Console, DEFAULT_MAX_STALL, ENV_EXAMPLE, ENV_FILE, FIX_PHASES, FORMAT_AFTER, Home, Log,
    PHASES, STATUS_TEXT, clock, first_line, human_duration, now_text,
)
from tinkerkit.web import Internet

from tinkerkit import conversation

try:  # chat channels are a plugin: without tinkerkit/channels.py, Tinker works as it did, in the terminal
    from tinkerkit import channels
except ImportError:
    channels = None
from tinkerkit.workspace import TaughtCommands, Toolchain


def main():
    """Tinker's command line: tinker reset, a command that shows or changes its settings (then it
    exits), or a run: a new project, or a resumed one, or a feature or a bug fix for one. What fails
    for a reason that is not a bug (Ollama, a file, Ctrl-C) ends with what happened, not a traceback."""
    script_started, started_at = time.perf_counter(), datetime.datetime.now()
    try:
        args = Cli.parse()
        if args.reset:
            return Home.reset(args.yes)
        Home.ensure()
        if Cli.works_for_hours(args):
            Home.keep_awake()
        if Commands(args).run():
            return
        ProjectRun(args, script_started, started_at).execute()
    except KeyboardInterrupt:  # Ctrl-C: what each part saves is saved as it goes
        raise SystemExit("\nInterrupted.")
    except OllamaError as exc:  # Ollama failed past every retry (a run handles it itself, and saves)
        raise SystemExit(f"\n{exc}")
    except OSError as exc:  # a file or folder that cannot be read or written (full disk, permissions)
        raise SystemExit(f"\n{exc.strerror or exc}: {exc.filename or ''}".rstrip(": "))


class Cli:
    """The command line: `tinker COMMAND ...`, e.g. `tinker task ...` builds something. Every command
    comes from the one table of commands (tinkerkit/commands.py): its arguments, its options and its
    help. Each command takes only its own arguments and options, every value is checked before
    anything starts, and what is wrong is an error that says what to change. A project is named only
    as @ID or @folder. The result has one shape for the rest of Tinker."""
    EXAMPLES = ("tinker", "tinker doctor", "tinker task a calculator in Python", "tinker add keep a history of results",
                "tinker research the newest Python release", "tinker help task")
    TALK = tuple(c.name for c in COMMANDS if c.group == "Talk" and c.cli)  # the conversation's own (one_shot)
    TITLES = {"Build": "🔨 Build", "Learn": "🧠 Learn", "Set up": "🔧 Set up", "Talk": "💬 Talk",
              "Channels": "📡 Channels"}
    RUNS = tuple(c.name for c in COMMANDS if c.run and c.name != "confirm")
    COMMANDS = tuple(c.name for c in COMMANDS if c.cli and c.group != "Talk" and c.name not in ("help", "channel",
                                                                                                "confirm"))
    LEARNING = ("learn",)  # the commands that load the model and read the web (but for teaching a command)
    FIELDS = {"reset": False, "config": None, "models": False, "use_model": None, "embed": None, "use_web": None,
              "calibrate": None, "learn": None, "memory": False, "runs": False, "status": None, "log": None,
              "doctor": False, "task": None, "spec": None, "spec_file": None, "restart": False, "resume": None,
              "amend": None, "add": None, "fix": None, "workdir": None, "web": False, "no_confirm": False,
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
            Home.clear(given.parts, given.yes)
            raise SystemExit(0)

    @staticmethod
    def known_command(argv):
        """The command, one of tinker's (the commands were options once: --task is said to be task)."""
        old = next((a for a in argv if a.startswith("--") and a[2:] in Cli.COMMANDS), None)
        if old:
            raise SystemExit(f"tinker: commands are written without --: tinker {old[2:]} (tinker help lists them)")
        if argv[0] not in Cli.COMMANDS:
            raise SystemExit(f"tinker: no command {argv[0]!r} (tinker help lists them)")
        return argv[0]

    @staticmethod
    def help(command=None):
        """tinker help: every command; tinker help COMMAND: that command's arguments and options."""
        if command is None:
            print(Cli.overview_markup() if os.environ.get("TINKER_FORMAT") == "markup" else Cli.overview())
            raise SystemExit(0)
        if command == "channel" and channels is not None:
            print(channels.ChannelCommand.USAGE)
            raise SystemExit(0)
        if command in Cli.TALK:  # the conversation's own: as said in a session, a chat, or once (tinker COMMAND ...)
            found = BY_NAME[command]
            print(f"usage: tinker {found.synopsis}\n\n{found.what[0].upper()}{found.what[1:]}.")
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
        rows = [(group, row) for group in GROUPS for c in COMMANDS if c.group == group for row in c.rows]
        width = max(len(synopsis) for _, (synopsis, _) in rows) + 2
        out = []
        for group in GROUPS:
            if group == "Channels" and channels is None:
                continue
            out += ["", paint(Cli.TITLES[group], "bold")]
            for synopsis, what in [row for g, row in rows if g == group]:
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
        for c in COMMANDS:
            if c.name not in Cli.COMMANDS:
                continue
            for option in c.options:
                name = option.flag + (f" {option.metavar}" if option.metavar else "")
                found.setdefault(name, (option.help, []))[1].append(c.name)
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
            for c in [c for c in COMMANDS if c.group == group]:
                for synopsis, what in c.rows:
                    out.append("💬 anything else: a normal chat" if not c.name else f"- `{synopsis}` {what}")
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
    def learning(words, parser):
        if len(words) > 2 or not words[0].strip():
            parser.error('use tinker learn WHAT (e.g. tinker learn Flask) or tinker learn NAME "DEFINITION" '
                         '(quote the definition)')
        return words

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
        if len(words) > 1 and not args.spec and last.suffix in (".md", ".txt") and last.is_file():
            args.spec = words.pop()
        args.task = " ".join(words).strip()
        if not args.task:
            parser.error("give a task, e.g. tinker task a calculator in Python")

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
    tinker learn, tinker memory, tinker runs, tinker status, tinker log, tinker rename."""

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
        elif args.memory:
            self.show_memory(args.graph)
        elif args.runs:
            self.list_runs()
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
            print(f"{words[0]} is {value!r} now")
            return
        name = Config.get("model")
        settings = Models.settings(name) if name else None
        print(f"home:            {Home.root}")
        print(f"ollama_url:      {Config.ollama_url()}")
        print(f"model in use:    {Commands.model_state(name, settings)}")
        if settings:
            Commands.print_measured(settings)
        for key in Config.SETTABLE:
            if key not in ("ollama_url",):
                print(f"{key + ':':16} {Config.get(key)!r}")
        print(f"web:             {Config.get('web') or 'none'} ({Internet.state()}; used by tinker learn and --web only)")
        embed = Config.get("embed_model") or "none: select one with tinker embed NAME (tinker learn and --web need one)"
        print(f"embed_model:     {embed}")
        print("Set one with tinker config KEY VALUE (the model, the embedding model and the web have their own commands).")

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
        print(f"input budget:    {settings['max_prompt_tokens']} tokens"
              + (f" (largest read {settings['largest_prompt_read']}, crashed at {settings['crashed_reading']})"
                 if settings.get("crashed_reading") else ""))
        print(f"reply limit:     {settings['max_reply_tokens']} tokens (fixes at most {settings['fix_max_tokens']})")
        print(f"context:         {settings['num_ctx']} tokens")
        think = settings.get("think")
        build = Config.get("build_think") if think not in (None, False) else None
        print(f"think:           {think} for planning" + (f", {build} for building" if build else ""))
        print(f"chars per token: {settings.get('chars_per_token')}")

    @staticmethod
    def list_models():
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(f"Ollama does not answer at {Config.ollama_url()} (tinker config ollama_url URL).")
        print(f"  {'MODEL':36} {'SIZE':>7}  {'PROMPT':>6} {'READ/S':>7} {'WRITE/S':>7}  CALIBRATED")
        for m in models:
            name, s = m.get("name", ""), Models.settings(m.get("name", "")) or {}
            mark = "*" if name == Config.get("model") else " "
            print(f"{mark} {name[:36]:36} {m.get('size', 0) / 1e9:6.1f}G  {s.get('max_prompt_tokens') or '-':>6} "
                  f"{s.get('read_tokens_per_s') or '-':>7} {s.get('write_tokens_per_s') or '-':>7}  "
                  f"{s.get('calibrated', 'not measured: tinker calibrate NAME')}")
        print("\n* the model in use. Measure one with tinker calibrate NAME, select it with tinker use-model NAME.")

    @staticmethod
    def use_model(name):
        """tinker use-model NAME: the model every run uses from now on, one already measured (nothing
        is measured unasked)."""
        if Models.settings(name) is None:
            raise SystemExit(f"{name} has not been measured: measure it first with tinker calibrate {name} "
                             f"(tinker models lists the models Ollama has)")
        Config.save(model=name)
        print(f"Model in use: {name}")

    @staticmethod
    def embed(name):
        """tinker embed NAME: the embedding model that indexes what is learned from now on: one Ollama
        has that gives embeddings (tried once here)."""
        Models.installed(name)
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(f"{embeddings.failed}: choose an embedding model (tinker models lists the models Ollama has)")
        Config.save(embed_model=name)
        print(f"Embedding model: {name}; what was learned is indexed with it from the next tinker learn or run on")

    @staticmethod
    def use_web(name):
        """tinker use-web NAME: the web search tinker learn and --web use from now on (one of
        Internet.backends()); it says when that one is not usable yet (a missing key)."""
        Config.save(web=name)
        print(f"Web search: {name}; {Internet.state()}")

    def calibrate(self):
        """tinker calibrate NAME: measure a model (again)."""
        Calibrator(Models.installed(self.args.calibrate)).run()

    def learn(self):
        """tinker learn WHAT [DEFINITION]: a command of yours (a definition given, or an alias in your
        shell) is kept at once, with no model; anything else is learned from the web with the model
        in use (Learner.learn tells a language, a tool and a domain apart), kept and indexed (an
        embedding model and the Internet plugin must be there; when one is not, nothing is loaded
        and it says why)."""
        args, option = self.args, "tinker learn"
        topic, definition = args.learn[0].strip(), (args.learn[1] if len(args.learn) > 1 else None)
        if definition is not None or TaughtCommands.alias(topic):
            TaughtCommands.teach(topic, definition)
            return
        model, embeddings, internet = self.learning_tools(option)
        learner = Learner(model, internet, embeddings)
        try:
            entry = learner.learn(topic, deep=True)
        finally:
            model.unload()
            Models.remember(model)
        if entry is None:
            raise SystemExit(f"Nothing learned about {topic}; nothing saved.")
        self.print_learned(topic, entry)

    @staticmethod
    def learning_tools(option):
        """(the model in use, the embedding model, the Internet plugin), each checked first: when one is
        not there, nothing is loaded and it says why."""
        name, settings = Models.selected()
        Models.installed(name)
        Config.check_model(run=False)
        embeddings = Embeddings.required(option)
        internet = Internet.required(option)
        model = Model(name, settings, Config.keep_alive(Config.get("keep_alive")), Log(Home.store(), "learn"),
                      max_growth=float(Config.get("max_growth")))
        return model, embeddings, internet

    @staticmethod
    def print_learned(topic, entry):
        print(f"\n{entry['name']}: {entry['summary']}")
        for item in Library.items({Library.slug(entry.get("language") or topic): entry})[1:]:
            print(f"  - [{item['phase']}] {item['text']}")
        for contradiction in entry.get("contradictions", []):
            print(f"  contradiction (facts {', '.join(map(str, contradiction['facts']))}): {contradiction['note']}")
        for gap in entry.get("open", []):
            print(f"  still open (Q{gap['question']}): {gap['missing'] or gap['search']}")
        for note in entry["notes"]:
            print(f"  not learned: {note}")
        print("A run uses this when its recall phase finds the request needs it; tinker memory lists what was learned.")

    @staticmethod
    def show_memory(graph=False):
        """What Tinker knows, as lists; or (graph) as a graph in one page, opened in the browser when this
        prints to a terminal (a chat sends the page as a file)."""
        if graph:
            Commands.show_graph()
            return
        print(f"Tinker's home: {Home.root}\nModel in use: {Config.get('model') or 'none'}")
        languages, any_language = Home.store().items("languages"), Home.store().get("memory", "any") or {}
        for key, doc in languages.items():
            Commands.print_language(key, doc)
        print("\nAny language:")
        Commands.print_lessons(any_language.get("lessons", []))
        Commands.print_topics(Library.topics())
        Commands.print_taught(TaughtCommands.all())
        retired = sum(len(doc.get("retired", [])) for doc in [*languages.values(), any_language,
                                                              *Library.topics().values()])
        if retired:
            print(f"\nRetired: {retired} item(s) that did not hold (never shown to the model; tinker memory --graph "
                  f"shows them, with why)")
        print("\nForget a part with tinker clear PART, a command with tinker learn NAME none.")
        print("See it as a graph: tinker memory --graph")

    @staticmethod
    def show_graph():
        graph = write_page(Embeddings.configured())
        print(f"Tinker's memory: {graph.summary()}")
        print(f"Similar lessons found by {graph.similar_by}")
        print(f"Page: {Home.graph_page()}")
        if sys.stdout.isatty():
            webbrowser.open(Home.graph_page().as_uri())
            print("Opened in your browser.")

    @staticmethod
    def print_language(key, doc):
        """What a language's projects taught: the toolchain that worked, its fixes, proven facts, lessons."""
        print(f"\n{doc.get('language', key)} ({key}), {doc.get('projects', 0)} project(s) finished")
        if Toolchain(doc.get("toolchain")):
            print(f"  toolchain: {Toolchain(doc.get('toolchain')).summary()}")
        for f in doc.get("fixes", []):
            print(f"  fix: {first_line(f['failure'], 70)} -> {first_line(f['cause'], 70)} (helped {f.get('used', 0)}"
                  f"{'; replayed without the model' if f.get('replay') else ''})")
        for fact in doc.get("facts", []):
            print(f"  proven fact [{fact.get('phase')}]: {first_line(fact.get('text', ''), 100)}")
        Commands.print_lessons(doc.get("lessons", []))

    @staticmethod
    def print_lessons(lessons):
        for lesson in lessons:
            print(f"  lesson [{lesson.get('phase')}, {lesson.get('status', 'candidate')}, seen {lesson.get('seen', 1)}x]: "
                  f"{lesson.get('text')}")
        if not lessons:
            print("  no lessons yet")

    @staticmethod
    def print_topics(topics):
        print("\nLearned from the web (tinker learn WHAT):" + ("" if topics else " nothing yet"))
        for slug, entry in topics.items():
            kind = "language" if entry.get("kind") == "language" else "topic"
            print(f"  {slug} ({kind}): {entry.get('name', slug)}, learned {entry.get('learned', '?')}, "
                  f"{len(entry.get('facts', []))} fact(s): {first_line(str(entry.get('summary', '')), 80)}")

    @staticmethod
    def print_taught(taught):
        print("\nYour commands (tinker learn NAME):" + ("" if taught else " none yet"))
        for name, entry in taught.items():
            print(f"  {name}: {TaughtCommands.describe(entry)}")

    @staticmethod
    def list_runs():
        entries = sorted(Runs.entries(), key=lambda e: e.get("updated", ""), reverse=True)
        if not entries:
            print("No runs yet.")
            return
        print(f"{'PROJECT':9} {'STATUS':14} {'UPDATED':17} {'FOLDER':32} TASK")
        for e in entries:
            task = Commands.task_of(e)
            print(f"{'@' + e.get('id', '?'):9} {e.get('status', '?'):14} {e.get('updated', ''):17} "
                  f"{Commands.short_folder(e.get('workdir', '')):32} {task[:60] + ('...' if len(task) > 60 else '')}")

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
        print(f"Run {run['id']} ({run.get('status', '?')}, updated {run.get('updated', '?')}): {first_line(run['task'])}")
        print(f"Project: {workdir}   Language: {(spec or {}).get('language', '?')}")
        if not spec or not spec.get("steps"):
            print("No spec yet (planned on the next tinker resume).")
            return
        Commands.print_criteria(spec["steps"])
        for c in spec.get("cycles", []):
            if c.get("phase") != "done":
                waits = " (its plan waits for your confirm)" if c.get("awaiting") else ""
                print(f"  {'project' if c['id'] == 'base' else c.get('kind', 'feature') + ' ' + c['id']}: next phase "
                      f"{c.get('phase')}{waits}")

    @staticmethod
    def print_criteria(steps):
        """How many criteria pass; each one's status, and a parked one's last error."""
        print(f"{sum(1 for s in steps if s['status'] == 'green')}/{len(steps)} criteria pass")
        for s in steps:
            print(f"  {s['id']:5} {STATUS_TEXT.get(s['status'], s['status']):11} {s['title'][:50]:50} {s['test']}")
            if s["status"] == "blocked" and s.get("last_error"):
                print(f"        last error: {first_line(s['last_error'], 110)}")

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
            print(entry.get("line", ""))

    @staticmethod
    def print_calls(store):
        """Each model call: when, what, an error, and the start of its reply."""
        for _, entry in store.entries("events"):
            print(f"[{entry['time']}] {entry.get('label', '')}" + (f"  error: {entry['error']}" if entry.get("error")
                                                                    else ""))
            if entry.get("reply"):
                print("\n".join(f"    {line}" for line in str(entry["reply"]).splitlines()[:40]))

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
        print(f"Renamed: {workdir} -> {target}")


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
                print(f"  ✓ {name}: {found}")
            except SystemExit as exc:
                problems += required
                print(f"  {'✗' if required else '·'} {name}: {exc}")
        print("\nEverything Tinker needs is there." if not problems else
              f"\n{problems} thing(s) to fix before a build can run.")
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
            script = Path(__file__).resolve()
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
        Console.mirror(self.tracker.printed)
        self.open()
        listening = conversation.attach(self.tracker.state["title"], self.tracker)
        urgent = False
        try:
            self.work_safely()
            self.finish()
        finally:  # the summary is printed first, so the channels show it too
            if listening:
                urgent = listening.end()
            elif os.environ.get(conversation.OFF) != "off":  # the conversation that started it reads it last
                self.tracker.close()
        if urgent:
            self.go_on_after_emergency()
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
        self.log = Log(project.store, "events")
        model = Model(model_name, settings, Config.keep_alive(Config.get("keep_alive")), self.log,
                      max_growth=float(Config.get("max_growth")))
        self.record["model"] = model.name
        print(f"Started: {clock(self.started_at)}")
        self.print_header(model, workdir)
        self.log.add({"label": "session start", "mode": self.mode, "model": model.name,
                      "started": clock(self.started_at)})
        self.session = Session(model, project, DEFAULT_MAX_STALL, self.internet, confirm=not args.no_confirm,
                               tracker=self.tracker)
        if project.language:
            print(f"learned so far: {self.session.knowledge.summary()}")

    def print_header(self, model, workdir):
        run, mode, args = self.record, self.mode, self.args
        change = "fixing a bug" if args.bug else "adding a feature"
        doing = {"new": "restarting" if self.args.restart else "new", "resume": "resuming", "change": change}[mode]
        print(f"Run {run['id']} ({doing}): {run['task']}")
        if mode == "change":
            print(f"  request: {first_line(args.change_request, 200)}")
        print(f"model {model.name} (calibrated {model.settings.get('calibrated', '?')})   context {model.num_ctx}   "
              f"input budget {model.max_tokens} tok   think {model.think}"
              + (f" (building: {model.build_think})" if model.build_think and model.build_think != model.think else "")
              + f"   keep_alive {model.keep_alive}")
        print(f"reply limit per call (thinking included): {model.max_reply} tokens (the longest it writes); "
              f"fixes at most {model.fix_max}")
        print(f"Project: {workdir}")

    def work_safely(self):
        """work, saved whatever happens: Ollama failing, Ctrl-C, a stop, or a bug (reported, so
        the run can be continued)."""
        try:
            self.work()
        except OllamaError as exc:
            print(f"\n{exc}")
        except KeyboardInterrupt:
            print("\nInterrupted.")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                print(f"\n{exc.code}" if isinstance(exc.code, str) else "")
        except Exception as exc:  # a bug must not lose the run: report it, save, and say how to continue
            details = traceback.format_exc()
            print(f"\nUnexpected error: {type(exc).__name__}: {first_line(str(exc), 300)}\n{details.rstrip()}")
            self.log.add({"label": "unexpected error", "error": details})
        finally:
            self.save()

    def work(self):
        """The cycle to work on, run from its saved phase: a plan that waits is confirmed (or amended)."""
        session, run = self.session, self.record
        brief = Brief.load(session.model, session.project.store, self.mode, run["task"], self.spec_text)
        cycle = self.choose_cycle(brief)
        if cycle is not None and self.args.no_confirm:
            cycle["confirm"] = False
        if cycle is not None and cycle.get("awaiting"):
            self.confirm_or_amend(cycle)
        if cycle is None:
            CycleRunner(session).show_plan()
            print("  every cycle of this run is done; add a feature with tinker add, or fix a bug with tinker fix")
            self.outcome.set("done")
            return
        self.outcome.set(CycleRunner(session).run(cycle, web=self.internet is not None))

    def choose_cycle(self, brief):
        """The cycle to work on: a new feature or bug (tinker add, tinker fix), the unfinished one, or the
        project itself. A new cycle's plan waits for confirmation unless the run was given --no-confirm."""
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
            print("\nPlan: changing it as you asked")
            self.tracker.phase("amend")
            self.amend(cycle, self.args.amend)
        else:
            cycle["awaiting"], cycle["confirmed"] = False, True
            print("Plan confirmed: building it")
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
        print(f"Started: {clock(self.started_at)}   Ended: {clock(ended_at)}   Wall-clock time: {human_duration(wall)}")
        times = {"started": clock(self.started_at), "ended": clock(ended_at), "wall_seconds": round(wall, 1)}
        self.log.add({"label": "session end", "status": self.outcome.status, **times})
        self.record["last_session"] = times
        Runs.save(self.record, self.session.project.workdir)
        total = time.perf_counter() - self.script_started
        print(f"Total execution time: {total:.2f}s ({human_duration(total)})")
        self.tracker.end(self.ending, total)

    def print_summary(self):
        """What happened, for the terminal; the same as data (self.ending) for the tracker."""
        status = self.outcome.status
        print("\n" + "=" * 60)
        self.ending = {"status": status, "headline": "", "details": [], "next": []}
        if status == "done":
            self.summary_done()
        elif status == "awaiting":
            self.summary_awaiting()
        elif status == "blocked":
            self.summary_blocked()
        else:
            run_id = self.record["id"]
            self.ending["headline"] = f"Run {run_id} is not finished. Progress is saved"
            self.ending["next"] = [("Continue it", f"tinker resume @{run_id}")]
            print(f"{self.ending['headline']}; continue with:\n  tinker resume @{run_id}")
        print(f"Log: tinker log @{self.record['id']}")

    def summary_done(self):
        project, run_id = self.session.project, self.record["id"]
        failing = [item for c in project.cycles for item in c.get("readme_failures", [])]
        if failing:  # the README's examples are how a person uses it: one that fails is said first
            headline = (f"Done, but {len(failing)} README example(s) do not print what they say (all "
                        f"{len(project.criteria)} spec tests pass)")
            print(f"{headline}:" + "".join(f"\n  - {f}" for f in failing))
        else:
            headline = f"Done. All {len(project.criteria)} spec tests pass (SPEC.md)."
            print(headline)
        self.ending.update(headline=headline, details=[("README example", f) for f in failing])
        if project.toolchain.run:
            print(f"Run it: cd {project.workdir} && {project.toolchain.run}")
            self.ending["details"].append(("Run it", project.toolchain.run))
        if (project.workdir / ENV_EXAMPLE).is_file():
            print(f"Settings: {ENV_FILE} (defaults in {ENV_EXAMPLE}).")
            self.ending["details"].append(("Settings", f"{ENV_FILE} (defaults in {ENV_EXAMPLE})"))
        if any(f.get("role", "").lower().startswith("adapter") for f in project.files.values()):
            print("The tests use sample data instead of external services: try those by hand with real settings.")
        print(f'Add a feature with: tinker add @{run_id} "..."; fix a bug with: tinker fix @{run_id} "..."')
        self.ending["next"] = [("➕ Add a feature", f'tinker add @{run_id} "..."'),
                               ("🐞 fix a bug", f'tinker fix @{run_id} "..."')]

    def summary_awaiting(self):
        """The plan as a person reads it, to confirm or change before anything is built; the tracker is
        given it as data."""
        run_id = self.record["id"]
        plan = self.waiting_plan()
        self.print_plan(plan)
        print(f"Its folder: {plan['folder']}   (rename it: tinker rename @{run_id} NAME)")
        print(f"Build it: tinker confirm @{run_id}   (in the chat: confirm)")
        print(f"Change it: tinker amend @{run_id} CHANGE   (in the chat: amend WHAT)")
        self.ending.update(headline=f"Its plan waits for your confirm: tinker confirm @{run_id}")
        self.tracker.wait(plan)

    def waiting_plan(self):
        """The plan that waits, as data: what it is, what changed, its criteria, what it leaves out."""
        project = self.session.project
        cycle = project.open_cycle()
        kind = "fix" if cycle.get("kind") == "fix" else "project" if cycle["id"] == "base" else "feature"
        return {"name": project.workdir.name, "run": self.record["id"], "language": project.language,
                "start": project.toolchain.run, "kind": kind, "changes": cycle.get("changes", []),
                "criteria": [(s["id"], s["behaviour"]) for s in project.cycle_criteria(cycle)],
                "non_goals": cycle.get("non_goals", []), "folder": str(project.workdir)}

    @staticmethod
    def print_plan(plan):
        runs = f"; a person runs it with: {plan['start']}" if plan["start"] else ""
        heading = "🐞 Bug shown for" if plan["kind"] == "fix" else "📋 Plan for"
        print(f"{heading} {plan['name']} (run {plan['run']}): {plan['language']}{runs}")
        if plan["changes"]:
            print("What changed:" + "".join(f"\n  {line}" for line in plan["changes"]))
        print("Tested before any code is written:")
        for criterion_id, behaviour in plan["criteria"]:
            print(f"  {criterion_id}  {behaviour}")
        if plan["non_goals"]:
            print(f"Not included: {'; '.join(plan['non_goals'])}")

    def summary_blocked(self):
        """Why it stopped: a bug no test showed, a project that does not build, tools that do not
        work, or criteria parked."""
        run_id = self.record["id"]
        lines = self.why_blocked(self.session, run_id)
        print("\n".join(lines))
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
            [f"  {s['id']} {s['title']}: {first_line(s.get('last_error', ''), 160)}" for s in session.project.parked()] + \
            [f"Change what it cannot (the request, the plan, a dependency), then: tinker resume @{run_id}"]

    def go_on_after_emergency(self):
        """Stopped to answer an emergency from a chat, now answered: the run goes on, as tinker resume."""
        print("\nAn emergency from a chat is answered: resuming.")
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, str(Path(sys.argv[0]).resolve()), "resume", f"@{self.record['id']}"])


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
        root = Path(str(Config.get("projects") or "~/Projects")).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        name = ProjectFolder.named(task) or ProjectFolder.from_words(task)
        folder, count = root / name, 1
        while folder.exists():
            count += 1
            folder = root / f"{name}-{count}"
        folder.mkdir()
        print(f"Project folder: {folder}")
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
        kept = [w for w in re.findall(r"[a-z0-9]+", task.lower()) if w not in ProjectFolder.COMMON]
        name = "-".join(kept[:2])[:ProjectFolder.NAME_MAX].strip("-")
        return name if ProjectFolder.NAME_RE.fullmatch(name) else "project"


class CycleRunner:
    """A cycle of the project itself, of a feature (tinker add) or of a bug fix (tinker fix): Tinker's idea in
    order (PHASES, FIX_PHASES): plan the task as a spec, which waits for the person's confirm (CONFIRM_AFTER),
    install the test tool, a test for every spec point, just enough code to pass them, tidied, one final
    review, done. Resumable at each phase: a phase is saved as done once it ends. A phase that stops the
    cycle disproves the knowledge the cycle used in it."""

    def __init__(self, session):
        self.s, self.p = session, session.project

    def run(self, cycle, web=False):
        """Continue a cycle from its saved phase: "done", "blocked" (a phase stopped it) or "awaiting" (its
        plan waits for the person to confirm it). With web, a cycle past its recall phase recalls first, so
        the rest of it may learn what it needs."""
        phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
        self.s.cycle = cycle
        if web and cycle["phase"] not in ("recall", "done") and not cycle.get("awaiting"):
            self.recall_first(cycle)
        while cycle["phase"] != "done":
            if cycle.get("awaiting"):
                return "awaiting"
            phase = cycle["phase"]
            if self.s.tracker is not None:
                self.s.tracker.phase(phase)
            if self.run_phase(phase, cycle) is False:
                self.disprove(cycle, phase)
                return "blocked"
            if phase in FORMAT_AFTER:
                self.s.format_code()
            self.done_with(phase, cycle, phases)
            if phase == "review":
                self.show_plan()
        return "done"

    def recall_first(self, cycle):
        """A run with --web past its recall phase: what the rest of the cycle needs is learned first."""
        print(f"\n{Recaller.TITLE} (with --web, before {cycle['phase']})")
        if self.s.tracker is not None:
            self.s.tracker.phase("recall")
        Recaller(self.s).run(cycle)

    def run_phase(self, phase, cycle):
        """Runs one phase; only setup, verify, the final review and reproduce can stop
        the cycle (they return False). The plan is made at the model's full think setting; building
        from it at build_think."""
        worker = self.phase_class(phase)(self.s)
        if worker.TITLE:
            print(f"\n{worker.TITLE}" + (f" ({'project' if cycle['id'] == 'base' else 'feature ' + cycle['id']})"
                                          if phase == "spec" else ""))
        self.s.model.building = worker.BUILDS
        return worker.run(cycle)

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
        to confirm it (tinker confirm) or change it (tinker amend), unless --no-confirm."""
        cycle["phase"] = phases[phases.index(phase) + 1]
        if phase == CONFIRM_AFTER[cycle.get("kind", "feature")] and cycle.get("confirm") and not cycle.get("confirmed"):
            cycle["awaiting"] = True
        self.s.save()

    def disprove(self, cycle, phase):
        """The knowledge used in a step that failed is discarded (it did not hold)."""
        gone = self.s.knowledge.disprove(cycle.get("used", {}), self.s.failed_phases(phase))
        if gone:
            print(f"  {gone} lesson(s) and fact(s) used in this step are discarded: they did not hold")
        self.s.save()

    def show_plan(self):
        print(f"Plan: {len(self.p.criteria)} spec criteria (see SPEC.md)")
        for s in self.p.criteria:
            print(f"  {s['id']:5} [{STATUS_TEXT.get(s['status'], s['status'])}] effort {s.get('effort', 2)} "
                  f"{s['title']}  -> {s['test']}")


if __name__ == "__main__":
    main()
