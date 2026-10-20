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

Usage:
    tinker task "a calculator in Python" [spec.md] [--plan-only] [--web]   build it
    tinker restart                        build this folder's task again from scratch (asks first)
    tinker resume [ID]
    tinker add [ID] "Add a feature" [--spec feature.md] [--web]
    tinker fix [ID] "Describe a bug: what you do, what happens, what should happen" [--web]
    tinker status [ID] | tinker runs
    tinker learn WHAT                     learn a language, a tool or a domain (Tinker tells which)
    tinker learn NAME ["DEFINITION"]      a command of yours Tinker may use (an alias in your shell)
    tinker doctor                         what is missing or wrong, and the command that fixes it
    tinker models | tinker calibrate NAME | tinker use-model NAME | tinker embed NAME
    tinker list-config | tinker memory | tinker reset
    tinker help [COMMAND]                 every command, or one command's arguments and options
How a run thinks, how long the model stays loaded and when it is unloaded are Tinker's to decide,
from the measured model and config.json (where they may be tuned by hand).

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
--plan-only stops after the review so the plan (SPEC.md) can be read first.

Learning: a model knows what it was trained on, as it was then. tinker learn WHAT tells what WHAT is
(one planning call; a topic learned before keeps its kind) and learns it the way that fits it:
  a programming language as it is today, e.g. "Python 3.14 with pytest, black and venv". Every
      language gets the same sections, each with its own searches and share of the facts:
      fundamentals (syntax, keywords, types, errors, modules, the concurrency and runtime model),
      what is new (the newest stable releases), its tools (one per role: environment, packages,
      build, tests, format, lint: the ones named, else the usual ones; how to install and run each)
      and conventions. Saved in learned/<language>.json; every project in that language uses it.
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
The web is the Internet plugin: web search and web fetch through a backend (config.json's "web":
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

Each run is faster than the last. Tinker keeps a folder next to this script, .tinker/:
  config.json            the model in use, Ollama's address, keep_alive,
                         the Internet plugin (tinker learn, --web): web (its backend), web_fallback,
                         web_timeout, learn_missing; the embedding model (tinker embed NAME); and
                         projects, where tinker task makes each new project's own folder (~/Projects).
  models/<model>.json    measured once per model, in minutes (tinker calibrate NAME): whether its
                         weights fit the GPU memory at all (no call), the think setting it accepts,
                         its chars per token and speeds (one short chat), and whether a run's whole
                         context (its longest prompt and reply) fits: one read of that many tokens,
                         since a reply fills memory as a prompt of its length does and reading is
                         far faster than writing; only when that fails, the largest that fits is
                         found to within 10%. A call that crashes the runner is tried again; a limit
                         that keeps crashing drops 10% for that run only (the run then says to
                         measure again).
                         tinker list-config shows them; tinker models lists them.
  languages/<lang>.json  per language: the toolchain that last worked (the next design starts from
                         it), toolchain fixes (the same failure is fixed again without the model),
                         and lessons.
  learned/<topic>.json   what was learned from the web about each language and topic
                         (tinker learn, or a run's recall phase): facts,
                         each with its source, and what is installed here; researched topics also
                         their questions, findings, contradictions and open gaps.
  commands.json          your own commands Tinker may use (tinker learn NAME).
  embeddings.json        the embedding of each learned text, made once (by the tinker embed model).
  learn.jsonl            every model call tinker learn made.
  memory.json            lessons for any language. Lessons are shown in the prompts of the phase
                         they concern, as optional parts (they never push out what is required).
  runs/                  run IDs, so tinker resume ID, tinker add ID and tinker fix ID work from anywhere.
All plain JSON: edit them by hand; tinker memory shows what was learned; tinker reset starts fresh.

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
keeps its spec, run and log in its own .tinker folder. Everything printed also goes to
current.log.

How the code reads: this file is the main system, top-down: main() first, then the command line
(Cli, Commands), a run (ProjectRun, Session) and the cycle of phases (CycleRunner). The parts it uses
are the modules of tinkerkit/, one responsibility each, each using only those listed above it:
  settings   every tunable number and fixed name, grouped by the part that uses them; small helpers
  prompts    prompt parts, replies and every system prompt (Part, Reply, Prompts)
  workspace  the project's files and the commands run in it (Workspace, Toolchain, Output, TestRef)
  model      the local model (Ollama, Model and the Tries of one request, Models, Calibrator)
  project    the plan and its record (Project, SpecDocument, Runs, RunChecks)
  web        the Internet plugin, the only part that goes online (Internet, OllamaWeb)
  channels   the chat plugin: a run's progress, status and stop over Telegram, and chat (Channel, Talk)
  knowledge  lessons (Knowledge) and learning (Learner, Researcher, Library, Retriever, Embeddings)
  planning   recall, the spec, the contracts where files meet, and their review
  building   setup, the tests, the code, and fixing until every test passes
  finishing  tidy, the final review, the README and the lessons
Patterns and texts that only one class uses are defined in that class.
"""

import argparse
import datetime
import json
import os
import re
import secrets
import shutil
import sys
import time
import traceback
from pathlib import Path

from tinkerkit.building import BugReproducer, CodeWriter, TestToolSetup, TestWriter, Verifier
from tinkerkit.finishing import FinalReview, LessonWriter, ReadmeWriter, Tidy
from tinkerkit.knowledge import Embeddings, Knowledge, Learner, Library
from tinkerkit.model import Calibrator, Model, Models, Ollama, OllamaError
from tinkerkit.planning import Designer, InterfaceReviewer, Recaller, SpecWriter
from tinkerkit.project import Brief, Outcome, Project, RunChecks, Runs
from tinkerkit.prompts import Part, PromptTooLarge, Prompts, Reply
from tinkerkit.settings import (
    COMMANDS_FILE, CONFIG_FILE, Config, Console, DEFAULT_MAX_STALL, ENV_EXAMPLE, ENV_FILE, EventLog, FIX_PHASES,
    FORMAT_AFTER, HOME, Home, JsonFile, LANGUAGES_DIR, LEARNED_DIR, LEARN_LOG, MEMORY_FILE, NETWORK_NOTE,
    NETWORK_RETRY, PHASES, PLANNING_PHASES, SETUP_TIMEOUT, STATE_DIR, STATUS_TEXT, clock, fingerprint, first_line,
    human_duration, now_text,
)
from tinkerkit.web import Internet

try:  # channels are a plugin: without tinkerkit/channels.py, Tinker works as it did
    from tinkerkit import channels
except ImportError:
    channels = None
from tinkerkit.workspace import TaughtCommands, Toolchain, Workspace


                                  # the topic a request is about 0.65 and up, unrelated topics 0.52 and down)


def main():
    """Tinker's command line: tinker reset, a command that shows or changes its settings (then it
    exits), or a run: a new project, or a resumed one, or a feature or a bug fix for one. What fails
    for a reason that is not a bug (Ollama, a file, Ctrl-C) ends with what happened, not a traceback."""
    script_started, started_at = time.perf_counter(), datetime.datetime.now()
    try:
        args = Cli.parse()
        if args.reset:
            return Home.reset()
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
    """The command line: `tinker COMMAND ...`, e.g. `tinker task "..."` builds something (see
    GROUPS, and tinker help). Each command takes only its own arguments and options, every value is checked before
    anything starts, and what is wrong is an error that says what to change. Everything a person
    should not have to decide (thinking, how long the model stays loaded) Tinker decides itself,
    from the measured model and config.json. The result has one shape for the rest of Tinker."""
    # every command, in the order help shows them: (synopsis, what it does); options are listed apart
    GROUPS = {
        "Build": [
            ('task "TASK" [SPEC]', "build something new, in its own folder in ~/Projects (SPEC: a spec file)"),
            ("restart", "build this folder's task again from scratch (asks first)"),
            ("resume [ID]", "continue a run: this folder's, or the one with ID (a spec that waits: build it)"),
            ('amend [ID] "CHANGE"', "change a spec that waits for confirmation (task --confirm)"),
            ("rename [ID] NAME", "rename the folder of a project whose spec waits for confirmation"),
            ('add [ID] "FEATURE"', "add a feature to a built project"),
            ('fix [ID] "BUG"', "fix a bug: what you do, what happens, what should"),
            ("status [ID]", "a run's spec points and whether they pass"),
            ("runs", "every run"),
        ],
        "Learn": [
            ("learn WHAT", "learn a language, a tool or a domain"),
            ('learn NAME "DEFINITION"', "a command of yours Tinker may use"),
            ("memory", "what Tinker has learned"),
        ],
        "Set up": [
            ("doctor", "what is missing or wrong, and how to fix it"),
            ("models", "the models Ollama has, and which are measured"),
            ("calibrate NAME", "measure a model once (a minute or a few)"),
            ("use-model NAME", "the measured model every build uses"),
            ("embed NAME", "the embedding model that indexes what is learned"),
            ("list-config", "the settings a build would use"),
            ("clear PART... [--yes]", "delete parts of what Tinker keeps: languages, memory, learned, models,"
                                      " runs, commands"),
            ("reset", "delete everything Tinker keeps (projects stay)"),
            ("help [COMMAND]", "this list, or one command's arguments and options"),
        ],
        "Channels": [
            ("channel list", "the chat channels (Telegram), and which are enabled"),
            ("channel add", "add one: its kind, a name, its token; pairs your chat"),
            ("channel remove NAME", "delete a channel and its token"),
            ("channel enable NAME", "every task reports to it; tinker channel start reads it"),
            ("channel disable NAME", "stop using it (channels start disabled)"),
            ("channel start [NAME]", "read the chat: tasks, status, stop, emergency, commands, or just chat"),
        ],
    }
    EXAMPLES = ("tinker doctor", 'tinker task "a calculator in Python"', 'tinker add "keep a history of results"',
                'tinker learn "food bank inventory"', "tinker help task")
    RUNS = ("task", "restart", "resume", "amend", "add", "fix")
    COMMANDS = RUNS + ("rename", "status", "runs", "learn", "memory", "doctor", "models", "use-model", "calibrate", "embed",
                       "list-config", "clear", "reset")
    LEARNING = ("learn",)  # the commands that load the model and read the web (but for teaching a command)
    ABOUT = {
        "task": "Build something new: a spec, a unit test for each point, just enough code, and a README a "
                "person can follow (its examples are run to check them). It gets its own folder in the projects "
                "folder (config.json: projects, ~/Projects), with a short name after the task (calc-py); "
                "--workdir DIR builds in DIR instead (--workdir . here).",
        "restart": "Build this folder's task again from scratch: everything in it is deleted (after asking), "
                   "but for its spec file.",
        "resume": "Continue a run: this folder's, or the one with this ID (from anywhere). A spec that waits "
                  "for confirmation (task --confirm) is confirmed: building starts.",
        "amend": "Change a spec that waits for confirmation (task --confirm), before anything is built: the "
                 "spec is given again with the change, and waits again (confirm it with tinker resume).",
        "add": "Add a feature to a built project (this folder's, or the run with this ID).",
        "fix": "Fix a bug: describe what you do, what happens and what should happen. A test shows the bug, "
               "then the code is fixed so it passes and every other test still passes.",
        "rename": "Rename a project's folder while its spec waits for confirmation (task --confirm), before "
                  "anything is built in it (what is built there may name its folder).",
        "status": "Show a run's spec points and whether their tests pass.",
        "runs": "List every run, newest first.",
        "learn": "Learn something and keep it for the builds that need it. Tinker tells what it is: a language "
                 "(\"Python 3.14\"), a tool (\"Flask\"), a domain (\"food bank inventory\", researched with its "
                 "sources cross-checked), or NAME \"DEFINITION\" (or an alias in your shell): a command of yours.",
        "memory": "Show what Tinker has learned: toolchains, fixes, lessons, topics, your commands.",
        "doctor": "Check what Tinker needs (Ollama, a measured model, an embedding model, the web) and say how "
                  "to fix what is missing.",
        "models": "List the models Ollama has, and which are measured.",
        "use-model": "Use this measured model for every build from now on.",
        "calibrate": "Measure a model once: the largest prompt it reads and the longest reply it writes.",
        "embed": "Use this embedding model to index what is learned, so builds find it by meaning.",
        "list-config": "Show the settings a build would use.",
        "clear": "Delete parts of what Tinker keeps, e.g. tinker clear languages memory: languages (toolchains, "
                 "fixes, lessons per language), memory (lessons for any language), learned (what tinker learn "
                 "learned), models (measurements), runs (the run index), commands (your commands).",
        "reset": "Delete everything Tinker keeps (settings, measured models, what it learned, the run index); "
                 "projects are not touched.",
    }
    FIELDS = {"reset": False, "list_config": False, "models": False, "use_model": None, "embed": None,
              "calibrate": None, "learn": None, "memory": False, "runs": False,
              "status": None, "doctor": False, "task": None, "spec": None, "spec_file": None, "restart": False,
              "resume": None, "amend": None, "add": None, "fix": None, "workdir": None, "plan_only": False,
              "web": False, "confirm": False, "rename": None}
    RUN_ID_RE = re.compile(r"[0-9a-fA-F]{3,}")
    FOLDER_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")

    @staticmethod
    def parse(argv=None):
        """The command line read and checked (a mistake exits with what to change)."""
        argv = sys.argv[1:] if argv is None else argv
        if not argv or argv[0] in ("help", "-h", "--help"):
            Cli.help(argv[1] if len(argv) > 1 else None)
        if argv[0] == "channel":  # the chat channels: a plugin of its own, nothing else runs
            if channels is None:
                raise SystemExit("tinker: channels are not installed (tinkerkit/channels.py is missing)")
            channels.ChannelCommand.run(argv[1:])
            raise SystemExit(0)
        if argv[0] == "clear":  # nothing else runs: no model, no project
            given = Cli.parser("clear").parse_args(argv[1:])
            Home.clear(given.parts, given.yes)
            raise SystemExit(0)
        old = next((a for a in argv if a.startswith("--") and a[2:] in Cli.COMMANDS), None)
        if old:  # the commands were options once
            raise SystemExit(f"tinker: commands are written without --: tinker {old[2:]} (tinker help lists them)")
        command = argv[0]
        if command not in Cli.COMMANDS:
            raise SystemExit(f"tinker: no command {command!r} (tinker help lists them)")
        parser = Cli.parser(command)
        given = parser.parse_args(argv[1:])
        args = argparse.Namespace(**Cli.FIELDS)
        for name, value in vars(given).items():
            setattr(args, name, value)
        args.command = command
        if command:
            Cli.read_command(command, given, args, parser)
        if command in Cli.RUNS:
            Cli.check_run(args, parser)
        if command != "task":  # a new task without --workdir gets a folder of its own (ProjectFolder)
            args.workdir = args.workdir or "."
        return args

    @staticmethod
    def help(command=None):
        """tinker help: every command; tinker help COMMAND: that command's arguments and options."""
        if command is None:
            print(Cli.overview())
            raise SystemExit(0)
        if command == "channel" and channels is not None:
            print(channels.ChannelCommand.USAGE)
            raise SystemExit(0)
        if command not in Cli.COMMANDS:
            raise SystemExit(f"tinker: no command {command!r} (tinker help lists them)")
        Cli.parser(command).print_help()
        raise SystemExit(0)

    @staticmethod
    def overview():
        """tinker help: what it is, every command by what it is for, the options (with the commands
        that take them, read from their parsers) and a few examples; in colour in a terminal
        (unless NO_COLOR is set). Columns are lined up before colouring."""
        paint = Cli.painter()
        title = {"Build": "🔨 Build", "Learn": "🧠 Learn", "Set up": "🔧 Set up", "Channels": "💬 Channels"}
        width = max(len(synopsis) for lines in Cli.GROUPS.values() for synopsis, _ in lines) + 2
        out = [f"🛠  {paint('tinker', 'bold')} builds what you ask for with a local Ollama model: a small spec, a unit",
               "   test for each point, just enough code, and a README a person can follow.", "",
               f"{paint('Usage:', 'bold')} tinker {paint('COMMAND', 'cyan')} {paint('[ARGUMENTS]', 'yellow')} "
               f"{paint('[OPTIONS]', 'green')}"]
        for group, lines in Cli.GROUPS.items():
            if group == "Channels" and channels is None:  # the plugin is not installed
                continue
            out += ["", paint(title[group], "bold")]
            for synopsis, what in lines:
                name, _, arguments = synopsis.partition(" ")
                padding = " " * (width - len(synopsis))
                out.append(f"  {paint(name, 'cyan')}{' ' + paint(arguments, 'yellow') if arguments else ''}"
                           f"{padding}{what}")
        options = Cli.options()
        o_width = max(len(name) for name in options) + 2
        out += ["", paint("⚙️  Options", "bold")]
        out += [f"  {paint(name, 'green')}{' ' * (o_width - len(name))}{what} "
                f"{paint('(' + ', '.join(commands) + ')', 'dim')}" for name, (what, commands) in options.items()]
        out += ["", paint("💡 Examples", "bold")] + [f"  {paint('$', 'dim')} {example}" for example in Cli.EXAMPLES]
        return "\n".join(out + ["", f"{paint('tinker help COMMAND', 'cyan')} shows one command in full."])

    @staticmethod
    def painter():
        """paint(text, style): ANSI colour in a terminal; plain text when piped or NO_COLOR is set."""
        codes = {"bold": "1", "dim": "2", "cyan": "36", "yellow": "33", "green": "32"}
        if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
            return lambda text, style: text
        return lambda text, style: f"\033[{codes[style]}m{text}\033[0m"

    @staticmethod
    def options():
        """{--option [VALUE]: (what it does, the commands that take it)}, from the commands' parsers."""
        found = {}
        for command in Cli.COMMANDS:
            for action in Cli.parser(command)._actions:
                if action.option_strings and action.option_strings[0].startswith("--") and action.dest != "help":
                    name = action.option_strings[0] + (f" {action.metavar or action.dest.upper()}" if action.nargs != 0
                                                       else "")
                    found.setdefault(name, (action.help, []))[1].append(command)
        return found

    @staticmethod
    def parser(command):
        """The parser of one command, with only what that command takes."""
        usage = {"add": 'tinker add [ID] "feature" [--spec FILE] [--web] [--plan-only] [--workdir DIR]',
                 "amend": 'tinker amend [ID] "change" [--workdir DIR]',
                 "rename": "tinker rename [ID] NAME [--workdir DIR]",
                 "fix": 'tinker fix [ID] "bug" [--web] [--workdir DIR]',
                 "learn": 'tinker learn WHAT  |  tinker learn NAME "DEFINITION"'}.get(command)
        parser = argparse.ArgumentParser(prog=f"tinker {command}", usage=usage, allow_abbrev=False,
                                         description=Cli.ABOUT[command])
        if command == "task":
            parser.add_argument("task", help="what to build")
            parser.add_argument("spec", nargs="?", help="a spec file, e.g. plan.md")
        elif command in ("resume", "status"):
            parser.add_argument("id", nargs="?", default="", help="the run's ID (default: this folder's run)")
        elif command == "rename":
            parser.add_argument("words", nargs="+", metavar="[ID] NAME", help="the folder's new name")
        elif command in ("add", "fix", "amend"):
            parser.add_argument("words", nargs="+", metavar="[ID] TEXT",
                                help={"fix": "the bug", "add": "the feature", "amend": "the change"}[command])
        elif command == "learn":
            parser.add_argument("words", nargs="+", metavar="WHAT [DEFINITION]")
        elif command == "clear":
            parser.add_argument("parts", nargs="+", metavar="PART", help="languages, memory, learned, models, runs "
                                                                          "or commands")
            parser.add_argument("--yes", action="store_true", help="delete without asking")
        elif command in ("use-model", "calibrate", "embed"):
            parser.add_argument("name", help="the model's name, as `tinker models` lists it")
        if command in ("task", "add", "fix"):
            parser.add_argument("--web", action="store_true", help="may learn from the web what it needs")
        if command in ("task", "add"):
            parser.add_argument("--plan-only", action="store_true", help="plan, write SPEC.md and stop for review")
        if command == "task":
            parser.add_argument("--confirm", action="store_true", help="stop after a quick spec: build it with "
                                                                       "tinker resume, change it with tinker amend")
        if command == "add":
            parser.add_argument("--spec", dest="spec_file", metavar="FILE", help="a spec file for the feature")
        if command in Cli.RUNS + ("status", "rename"):
            parser.add_argument("--workdir", metavar="DIR",
                                help="the project folder (default: a new one in ~/Projects, named after the task)"
                                if command == "task" else "the project folder, if not this one")
        return parser

    @staticmethod
    def read_command(command, given, args, parser):
        """The command's arguments in the fields the rest of Tinker reads, checked."""
        field = command.replace("-", "_")
        if command in ("reset", "list-config", "models", "memory", "runs", "doctor"):
            setattr(args, field, True)
        elif command in ("use-model", "calibrate", "embed"):
            setattr(args, field, Cli.non_empty(given.name, command, parser))
        elif command in ("resume", "status"):
            setattr(args, field, Cli.run_id(given.id, parser))
        elif command == "restart":
            args.restart = True
        elif command in ("add", "fix", "amend"):
            setattr(args, field, given.words)
        elif command == "rename":
            if len(given.words) > 2 or not Cli.FOLDER_RE.fullmatch(given.words[-1]):
                parser.error("use tinker rename NAME or tinker rename ID NAME; a name is letters, digits, - _ and ., "
                             "starting with a letter or digit")
            run_id = Cli.run_id(given.words[0], parser) if len(given.words) == 2 else ""
            if run_id and given.workdir is not None:
                parser.error(Cli.finds_its_folder("tinker rename"))
            args.rename = (run_id, given.words[-1])
        elif command == "learn":
            if len(given.words) > 2 or not given.words[0].strip():
                parser.error('use tinker learn WHAT (e.g. tinker learn Flask) or tinker learn NAME "DEFINITION" '
                             '(quote the definition)')
            args.learn = given.words
        if command == "status" and args.status and args.workdir is not None:
            parser.error(Cli.finds_its_folder("tinker status"))

    @staticmethod
    def non_empty(value, command, parser):
        if not value.strip():
            parser.error(f"tinker {command}: the value is empty")
        return value.strip()

    @staticmethod
    def run_id(value, parser):
        if value and not Cli.RUN_ID_RE.fullmatch(value):
            parser.error(f"'{value}' is not a run ID (list them with tinker runs)")
        return value

    @staticmethod
    def works_for_hours(args):
        """A run or learning: work the computer must not sleep through (the other commands are quick)."""
        return args.command in Cli.RUNS + Cli.LEARNING

    @staticmethod
    def check_run(args, parser):
        """A new build, or one of new, resume, add, fix: read into one shape. A feature or a bug
        becomes a resume of its run with a request (args.change_request; args.bug for a bug);
        args.spec is the spec file of a new build."""
        kind = args.command
        args.bug, args.change_request = kind == "fix", None
        if kind == "task" and not args.task:
            parser.error('give a task, e.g. tinker task "a calculator in Python"')
        if kind == "resume" and args.resume and args.workdir is not None:
            parser.error(Cli.finds_its_folder("tinker resume"))
        if kind in ("add", "fix"):
            Cli.check_request(kind, args, parser)
        if kind == "amend":
            Cli.check_amend(args, parser)

    @staticmethod
    def finds_its_folder(command):
        return f"{command} ID finds the run's folder itself: remove --workdir (or give --workdir and no ID)"

    @staticmethod
    def check_amend(args, parser):
        """amend [ID] "change": a resume of its run, with the change (args.amend)."""
        if len(args.amend) > 2 or not args.amend[-1].strip():
            parser.error('use tinker amend "change" or tinker amend ID "change" (quote the change)')
        run_id = args.amend[0] if len(args.amend) == 2 else ""
        if run_id and not Cli.RUN_ID_RE.fullmatch(run_id):
            parser.error(f"'{run_id}' is not a run ID. Use tinker amend \"change\" or tinker amend ID \"change\"")
        if run_id and args.workdir is not None:
            parser.error(Cli.finds_its_folder("tinker amend"))
        args.amend, args.resume = args.amend[-1].strip(), run_id

    @staticmethod
    def check_request(kind, args, parser):
        """add [ID] "feature" or fix [ID] "bug"."""
        what = "bug" if kind == "fix" else "feature"
        request = args.fix if kind == "fix" else args.add
        if len(request) > 2:
            parser.error(f'use tinker {kind} "{what}" or tinker {kind} ID "{what}" (quote the {what})')
        run_id, text = (request[0], request[1]) if len(request) == 2 else ("", request[0])
        if not text.strip():
            parser.error(f"tinker {kind} needs a {what} description")
        if run_id and not Cli.RUN_ID_RE.fullmatch(run_id):
            parser.error(f"'{run_id}' is not a run ID. Use tinker {kind} \"{what}\" or tinker {kind} ID \"{what}\"")
        if run_id and args.workdir is not None:
            parser.error(Cli.finds_its_folder(f"tinker {kind}"))
        args.add, args.fix, args.change_request, args.resume = [text], None, text.strip(), run_id


class Commands:
    """The commands that show or change Tinker's settings, models, what it knows and runs, then
    exit: tinker list-config, tinker models, tinker use-model, tinker embed, tinker calibrate,
    tinker learn, tinker memory, tinker runs, tinker status."""

    def __init__(self, args):
        self.args = args

    def run(self):
        """Runs the command the command line gives; False when it gives none (a run then)."""
        args = self.args
        if args.list_config:
            self.list_config()
        elif args.models:
            self.list_models()
        elif args.use_model:
            self.use_model(args.use_model)
        elif args.embed:
            self.embed(args.embed)
        elif args.calibrate is not None:
            self.calibrate()
        elif args.learn:
            self.learn()
        elif args.memory:
            self.show_memory()
        elif args.runs:
            self.list_runs()
        elif args.status is not None:
            self.show_status(args.status, args.workdir)
        elif args.rename:
            self.rename(*args.rename, args.workdir)
        elif args.doctor:
            Doctor().run()
        else:
            return False
        return True

    @staticmethod
    def rename(run_id, name, workdir_arg):
        """A project's folder renamed (in the folder it is in) while its spec waits for confirmation, with
        the run's records; nothing is built there yet."""
        workdir = Runs.workdir(run_id, workdir_arg or ".")
        run = Runs.read_record(workdir)
        project = Project(workdir, run, JsonFile.read(workdir / STATE_DIR / "spec.json", {}, keeps_work=True))
        RunChecks.check_awaiting(project, "tinker rename renames the folder of")
        target = workdir.parent / name
        if target.exists():
            raise SystemExit(f"{target} already exists: pick another name")
        workdir.rename(target)
        run["workdir"] = str(target)
        Runs.save(run, target)
        print(f"Renamed: {workdir} -> {target}")

    @staticmethod
    def list_config():
        """tinker list-config: the settings a run would use, to check them (and measure again) first."""
        name = Config.get("model")
        settings = Models.settings(name) if name else None
        print(f"config:          {CONFIG_FILE}")
        print(f"ollama:          {Config.ollama_url()}")
        if not name:
            state = "none (measure one with tinker calibrate NAME, then select it with tinker use-model NAME)"
        elif settings:
            state = f"{name} (calibrated {settings.get('calibrated', '?')})"
        else:
            state = f"{name} (not measured: tinker calibrate {name})"
        print(f"model in use:    {state}")
        if settings:
            print(f"input budget:    {settings['max_prompt_tokens']} tokens"
                  + (f" (largest read {settings['largest_prompt_read']}, crashed at {settings['crashed_reading']})"
                     if settings.get("crashed_reading") else ""))
            print(f"reply limit:     {settings['max_reply_tokens']} tokens (fixes at most {settings['fix_max_tokens']})")
            print(f"context:         {settings['num_ctx']} tokens")
            think = settings.get("think")
            build = Config.get("build_think") if think not in (None, False) else None
            print(f"think:           {think} for planning" + (f", {build} for building" if build else ""))
            print(f"chars per token: {settings.get('chars_per_token')}")
            print(f"model file:      {Models.file(settings['model'])}")
        print(f"keep_alive:      {Config.get('keep_alive')}")
        print(f"web:             {Config.get('web') or 'none'} ({Internet.state()}; used by tinker learn and --web only)")
        print(f"web_fallback:    {Config.get('web_fallback') or 'none'} (asked what web cannot answer)")
        print(f"learn_missing:   {Config.get('learn_missing')} (topics a run with --web may learn)")
        embed = Config.get("embed_model") or "none: select one with tinker embed NAME (tinker learn and --web need one)"
        print(f"embed_model:     {embed}")

    @staticmethod
    def list_models():
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(f"Ollama does not answer at {Config.ollama_url()} ({CONFIG_FILE.name}: ollama_url).")
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
        print(f"Model in use: {name} (in {CONFIG_FILE})")

    @staticmethod
    def embed(name):
        """tinker embed NAME: the embedding model that indexes what is learned from now on: one Ollama
        has that gives embeddings (tried once here)."""
        Models.installed(name)
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(f"{embeddings.failed}: choose an embedding model (tinker models lists the models Ollama has)")
        Config.save(embed_model=name)
        print(f"Embedding model: {name} (in {CONFIG_FILE}); what was learned is indexed with it from the next "
              f"tinker learn or run on")

    def calibrate(self):
        """tinker calibrate NAME: measure a model (again)."""
        Calibrator(Models.installed(self.args.calibrate)).run()

    def learn(self):
        """tinker learn WHAT [DEFINITION]: a command of yours (a definition given, or an alias in your
        shell) is kept at once, with no model; anything else is learned from the web with the model
        in use (Learner.learn tells a language, a tool and a domain apart), saved and indexed (an
        embedding model and the Internet plugin must be there; when one is not, nothing is loaded
        and it says why)."""
        args, option = self.args, "tinker learn"
        topic, definition = args.learn[0].strip(), (args.learn[1] if len(args.learn) > 1 else None)
        if definition is not None or TaughtCommands.alias(topic):
            TaughtCommands.teach(topic, definition)
            return
        name, settings = Models.selected()
        Models.installed(name)
        Config.check_model(run=False)
        embeddings = Embeddings.required(option)
        internet = Internet.required(option)
        model = Model(name, settings, Config.keep_alive(Config.get("keep_alive")), LEARN_LOG,
                      max_growth=float(Config.get("max_growth")))
        learner = Learner(model, internet, embeddings)
        try:
            entry = learner.learn(topic, deep=True)
        finally:
            model.unload()
            Models.remember(model)
        if entry is not None:
            self.print_learned(topic, entry)
        if entry is None:
            raise SystemExit(f"Nothing learned about {topic}; nothing saved.")

    @staticmethod
    def show_memory():
        print(f"Tinker's folder: {HOME}\nModel in use: {Config.get('model') or 'none'}")
        for path in sorted(LANGUAGES_DIR.glob("*.json")) if LANGUAGES_DIR.is_dir() else []:
            doc = JsonFile.read(path, {}) or {}
            print(f"\n{doc.get('language', path.stem)} ({path.name}), {doc.get('projects', 0)} project(s) finished")
            if Toolchain(doc.get("toolchain")):
                print(f"  toolchain: {Toolchain(doc.get('toolchain')).summary()}")
            for f in doc.get("fixes", []):
                print(f"  fix: {first_line(f['failure'], 70)} -> {first_line(f['cause'], 70)} (helped {f.get('used', 0)}, "
                      f"failed {f.get('failed', 0)}{'; replayed without the model' if f.get('replay') else ''})")
            Commands.print_lessons(doc.get("lessons", []))
        print(f"\nAny language ({MEMORY_FILE.name}):")
        Commands.print_lessons((JsonFile.read(MEMORY_FILE, {}) or {}).get("lessons", []))
        topics = Library.topics()
        print(f"\nLearned from the web ({LEARNED_DIR.name}/, tinker learn WHAT):"
              + ("" if topics else " nothing yet"))
        for slug, entry in topics.items():
            kind = "language" if entry.get("kind") == "language" else "topic"
            print(f"  {slug} ({kind}): {entry.get('name', slug)}, learned {entry.get('learned', '?')}, "
                  f"{len(entry.get('facts', []))} fact(s): {first_line(str(entry.get('summary', '')), 80)}")
        taught = TaughtCommands.all()
        print(f"\nYour commands ({COMMANDS_FILE.name}, tinker learn NAME):" + ("" if taught else " none yet"))
        for name, entry in taught.items():
            print(f"  {name}: {TaughtCommands.describe(entry)}")
        print("\nThese are plain JSON files: edit or delete entries at any time.")

    @staticmethod
    def list_runs():
        entries = sorted(Runs.entries(), key=lambda e: e.get("updated", ""), reverse=True)
        if not entries:
            print("No runs yet.")
            return
        home = str(Path.home())
        print(f"{'ID':8} {'STATUS':14} {'UPDATED':17} {'PROJECT':32} TASK")
        for e in entries:
            workdir = e.get("workdir", "")
            shown = ("~" + workdir[len(home):]) if workdir.startswith(home) else workdir
            if len(shown) > 32:
                shown = "..." + shown[-29:]  # keep the folder name, the part that identifies it
            if not Path(workdir).is_dir():
                shown = (shown[:22] + " (missing)") if len(shown) > 22 else shown + " (missing)"
            task = e.get("task", "").replace("\n", " ")
            if e.get("features"):
                task = f"[+{len(e['features'])} feature{'s' if len(e['features']) > 1 else ''}] " + task
            print(f"{e.get('id', '?'):8} {e.get('status', '?'):14} {e.get('updated', ''):17} "
                  f"{shown[:32]:32} {task[:60] + ('...' if len(task) > 60 else '')}")

    @staticmethod
    def show_status(run_id, workdir_arg):
        workdir = Runs.workdir(run_id, workdir_arg)
        state = workdir / STATE_DIR
        run = JsonFile.read(state / "run.json", None, keeps_work=True)
        spec = JsonFile.read(state / "spec.json", None, keeps_work=True)
        if run is None:
            raise SystemExit(f"No run in {workdir}.")
        print(f"Run {run['id']} ({run.get('status', '?')}, updated {run.get('updated', '?')}): {first_line(run['task'])}")
        print(f"Project: {workdir}   Language: {(spec or {}).get('language', '?')}")
        if not spec or not spec.get("steps"):
            print("No spec yet (planned on the next tinker resume).")
            return
        steps = spec["steps"]
        print(f"{sum(1 for s in steps if s['status'] == 'green')}/{len(steps)} criteria pass")
        for s in steps:
            print(f"  {s['id']:5} {STATUS_TEXT.get(s['status'], s['status']):11} {s['title'][:50]:50} {s['test']}")
            if s["status"] == "blocked" and s.get("last_error"):
                print(f"        last error: {first_line(s['last_error'], 110)}")
        for c in spec.get("cycles", []):
            if c.get("phase") != "done":
                print(f"  {'project' if c['id'] == 'base' else 'feature ' + c['id']}: next phase {c.get('phase')}")

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
    def print_lessons(lessons):
        for lesson in lessons:
            print(f"  lesson [{lesson.get('phase')}, seen {lesson.get('seen', 1)}x]: {lesson.get('text')}")
        if not lessons:
            print("  no lessons yet")


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
                             f"app), or set ollama_url in {CONFIG_FILE}")
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
                             f"ln -s {script} /usr/local/bin/tinker")
        return found

class ProjectRun:
    """One run on a project: a new project, or a resumed one, or a feature (tinker add) or a bug fix
    (tinker fix) for one. It opens the run and a session on it, works through the cycle that is open,
    saves whatever happens, and ends with a summary."""

    def __init__(self, args, script_started, started_at):
        self.args = args
        self.script_started, self.started_at = script_started, started_at
        self.session = self.record = self.mode = self.log = None  # record: the run's run.json
        self.internet = None  # the Internet plugin, with --web only
        self.spec_text = ""
        self.outcome = Outcome()

    def execute(self):
        """Check what the run needs, prepare its folder, open it, work, and finish (exiting with
        its outcome)."""
        workdir = self.check_requirements()
        if self.args.restart:  # this folder's task again, in the folder emptied first
            spec = self.args.spec
            Runs.clear_folder(workdir, Path(spec).expanduser().resolve() if spec else None)
        Console.mirror(workdir)
        self.open()
        listening = channels.attach(f"{Path(workdir).name}: {self.record['task']}") if channels else None
        urgent = False
        try:
            self.work_safely()
            self.finish()
        finally:  # the summary is printed first, so the channels send it too
            if listening:
                urgent = listening.end()
        if urgent:  # stopped to answer an emergency from a chat, now answered: the run goes on
            print("\nAn emergency from a chat is answered: resuming.")
            sys.stdout.flush()
            os.execv(sys.executable, [sys.executable, str(Path(sys.argv[0]).resolve()), "resume", self.record["id"]])
        sys.exit(self.outcome.exit_code)

    def check_requirements(self):
        """The run's folder, once everything the run needs is checked: before a folder is emptied
        or the model loaded, so what is missing stops it with nothing changed."""
        Models.installed(Models.selected()[0])
        Config.check_model(run=True)
        TaughtCommands.all()  # a broken commands.json stops here, not part way
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

    def open(self):
        """The model (measured first if new: before the run exists, so a model that fails leaves
        nothing behind), the run, its project and the session."""
        args = self.args
        model_name, settings = Models.selected()
        self.record, workdir, self.spec_text, self.mode = Runs.open(args)
        state = workdir / STATE_DIR
        self.log = EventLog(state / "log.jsonl")
        model = self.make_model(model_name, settings, self.log.path)
        self.record["model"] = model.name
        print(f"Started: {clock(self.started_at)}")
        self.print_header(model, workdir)
        self.log.add({"label": "session start", "mode": self.mode, "model": model.name,
                      "started": clock(self.started_at)})
        spec = JsonFile.read(state / "spec.json", {"steps": [], "counter": 0}, keeps_work=True)
        project = Project(workdir, self.record, spec)
        self.session = Session(model, project, DEFAULT_MAX_STALL, self.internet)
        if project.language:
            print(f"learned so far: {self.session.knowledge.summary()}")

    def work(self):
        """The cycle to work on, run from its saved phase."""
        session, run = self.session, self.record
        brief = Brief.load(session.model, session.project.state, self.mode, run["task"], self.spec_text)
        cycle = self.choose_cycle(brief)
        if cycle is not None and cycle.get("awaiting"):  # its spec waits for the person: changed, or confirmed
            if self.args.amend:
                print("\nPlan: the spec, changed as you asked")
                SpecWriter(session).amend(cycle, self.args.amend)
            else:
                cycle["awaiting"], cycle["confirmed"] = False, True
                print("Spec confirmed: building it")
                session.save()
        if cycle is None:
            CycleRunner(session).show_plan()
            print("  every cycle of this run is done; add a feature with tinker add, or fix a bug with tinker fix")
            self.outcome.set("done")
            return
        self.outcome.set(CycleRunner(session).run(cycle, plan_only=self.args.plan_only))

    def save(self):
        """Whatever happened: the model unloaded, what the run learned about it kept, the run and
        the project saved."""
        model, project = self.session.model, self.session.project
        if self.outcome.status != "awaiting":  # a spec that waits keeps the model loaded for the change asked next
            model.unload()
        Models.remember(model)
        self.record["status"] = self.outcome.status
        Runs.save(self.record, project.workdir)
        try:
            project.save()
        except OSError:
            pass

    def finish(self):
        """The summary, the times and the log."""
        workdir = self.session.project.workdir
        self.print_summary()
        ended_at = datetime.datetime.now()
        wall = (ended_at - self.started_at).total_seconds()
        print(f"Started: {clock(self.started_at)}   Ended: {clock(ended_at)}   Wall-clock time: {human_duration(wall)}")
        times = {"started": clock(self.started_at), "ended": clock(ended_at), "wall_seconds": round(wall, 1)}
        self.log.add({"label": "session end", "status": self.outcome.status, **times})
        self.record["last_session"] = times
        Runs.save(self.record, workdir)
        total = time.perf_counter() - self.script_started
        print(f"Total execution time: {total:.2f}s ({human_duration(total)})")

    def make_model(self, name, settings, log_path):
        return Model(name, settings, Config.keep_alive(Config.get("keep_alive")), log_path,
                     max_growth=float(Config.get("max_growth")))

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

    def choose_cycle(self, brief):
        """The cycle to work on: a new feature or bug (tinker add, tinker fix), the unfinished one, or the
        project itself."""
        session, run, mode, args, spec_text = self.session, self.record, self.mode, self.args, self.spec_text
        project = session.project
        cycle = project.open_cycle()
        if mode == "change":  # its project is open to a new cycle (RunChecks.before_start)
            feature_id = secrets.token_hex(3)
            kind = "fix" if args.bug else "feature"
            feature_brief = Brief.make(session.model, args.change_request, spec_text) if spec_text and not args.bug else ""
            cycle = project.new_cycle(feature_id, args.change_request, feature_brief, kind)
            run.setdefault("features", []).append({"id": feature_id, "request": args.change_request, "kind": kind,
                                                   "added": now_text()})
            Runs.save(run, project.workdir)
        elif cycle is None and not project.cycles:
            cycle = project.new_cycle("base", run["task"], brief)
            if args.confirm:  # its spec waits for the person once it is planned
                cycle["confirm"] = True
                project.save()
        return cycle

    def print_summary(self):
        status, session = self.outcome.status, self.session
        print("\n" + "=" * 60)
        if status == "done":
            self.summary_done()
        elif status == "awaiting":
            self.summary_awaiting()
        elif status == "planned":
            print(f"Planned: {session.project.workdir / 'SPEC.md'}")
            print(f"Read the plan (and its commands), then build with: tinker resume {self.record['id']}")
        elif status == "blocked":
            self.summary_blocked()
        else:
            run_id = self.record["id"]
            print(f"Run {run_id} is not finished. Progress is saved; continue with:\n  tinker resume {run_id}")
        print(f"Log: {session.project.state / 'log.jsonl'}")

    def summary_done(self):
        project, run_id = self.session.project, self.record["id"]
        failing = [item for c in project.cycles for item in c.get("readme_failures", [])]
        if failing:  # the README's examples are how a person uses it: one that fails is said first
            print(f"Done, but {len(failing)} README example(s) do not print what they say (all "
                  f"{len(project.criteria)} spec tests pass):" + "".join(f"\n  - {f}" for f in failing))
        else:
            print(f"Done. All {len(project.criteria)} spec tests pass (SPEC.md).")
        if project.toolchain.run:
            print(f"Run it: cd {project.workdir} && {project.toolchain.run}")
        if (project.workdir / ENV_EXAMPLE).is_file():
            print(f"Settings: {ENV_FILE} (defaults in {ENV_EXAMPLE}).")
        if any(f.get("role", "").lower().startswith("adapter") for f in project.files.values()):
            print("The tests use sample data instead of external services: try those by hand with real settings.")
        print(f'Add a feature with: tinker add {run_id} "..."; fix a bug with: tinker fix {run_id} "..."')

    def summary_awaiting(self):
        """The spec as a person reads it, to confirm or change before anything is built."""
        project, run_id = self.session.project, self.record["id"]
        cycle = project.open_cycle()
        runs = f"; a person runs it with: {project.toolchain.run}" if project.toolchain.run else ""
        print(f"📋 Spec for {project.workdir.name} (run {run_id}): {project.language}{runs}")
        if cycle.get("changes"):
            print("What changed:" + "".join(f"\n  {line}" for line in cycle["changes"]))
        print("Tested before any code is written:")
        for s in project.cycle_criteria(cycle):
            print(f"  {s['id']}  {s['behaviour']}")
        if cycle.get("non_goals"):
            print(f"Not included: {'; '.join(cycle['non_goals'])}")
        print(f"Its folder: {project.workdir}   (rename it: tinker rename {run_id} NAME)")
        print(f"Build it: tinker resume {run_id}   (in the chat: confirm)")
        print(f'Change it: tinker amend {run_id} "..."   (in the chat: amend WHAT)')

    def summary_blocked(self):
        """Why it stopped: a bug no test showed, a project that does not build, tools that do not
        work, or criteria parked."""
        session, run_id = self.session, self.record["id"]
        if session.unreproduced:
            print(f"Not fixed: {session.unreproduced}")
        elif session.review_problem:
            print(f"Not done: the final review found {session.review_problem}")
            print(f"Correct it (or the request), then: tinker resume {run_id}")
        elif session.build_problem:
            print(f"Stopped before working on criteria: {session.build_problem}")
            print(f"See current.log for each fix; correct it by hand or continue with: tinker resume {run_id}")
        elif session.tool_problem:
            print(f"Stopped before working on criteria: {session.tool_problem}")
            print("No code change can fix that. Check the commands in SPEC.md (setup, test_all) and the dependency")
            print(f"file, then continue with: tinker resume {run_id}")
        else:
            green, parked, total = session.project.counts()
            print(f"Stopped: {green}/{total} tests pass; {parked} parked after fixing and a review against the plan:")
            for s in session.project.parked():
                print(f"  {s['id']} {s['title']}: {first_line(s.get('last_error', ''), 160)}")
            print(f"Change what it cannot (the request, the plan, a dependency), then: tinker resume {run_id}")


class ProjectFolder:
    """A new task's own folder in the projects folder (config.json: projects): named after the task by one
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
        options = {"num_ctx": settings["num_ctx"], "num_predict": 24}  # a run's num_ctx: the model stays loaded
        if settings.get("num_batch"):
            options["num_batch"] = settings["num_batch"]
        think = False if settings.get("think") not in (None, False) else None
        try:
            data = Ollama.chat(model, [{"role": "system", "content": Prompts.FOLDER_NAME_SYSTEM},
                                       {"role": "user", "content": task}],
                               Config.keep_alive(Config.get("keep_alive")), options, think=think, timeout=180)
        except OllamaError:
            return ""
        name = data["message"]["content"].strip().strip("`'\".").lower()
        return name if ProjectFolder.NAME_RE.fullmatch(name) and len(name) <= ProjectFolder.NAME_MAX else ""

    @staticmethod
    def from_words(task):
        kept = [w for w in re.findall(r"[a-z0-9]+", task.lower()) if w not in ProjectFolder.COMMON]
        name = "-".join(kept[:2])[:ProjectFolder.NAME_MAX].strip("-")
        return name if ProjectFolder.NAME_RE.fullmatch(name) else "project"


class Session:
    """What the phases of a run share: the model, the project and its workspace, what earlier
    projects taught, the Internet plugin (with --web); and what they all do alike: ask the model
    (within its budgets), run setup and the tests, note the problems solved for the lessons."""

    def __init__(self, model, project, max_stall=DEFAULT_MAX_STALL, internet=None):
        self.model, self.project, self.max_stall = model, project, max_stall
        self.internet = internet  # the Internet plugin: a run with --web only
        self.ws = Workspace(project.workdir)
        self.tool_problem = ""
        self.build_problem = ""
        self.unreproduced = ""  # tinker fix: why the bug could not be shown by a test
        self.review_problem = ""  # why the final review found the project not done
        self.network_waiting = []  # setup commands that failed because the network did: done when it is back
        self.all_tests_run = None  # (files key, (code, output)) of the last run of all tests
        # criteria parked when this session began: one retry each (a resume after a change)
        self.parked_at_start = {s["id"] for s in project.criteria if s.get("status") == "blocked"}
        self._knowledge = None

    @property
    def knowledge(self):
        """What was learned for the project's language (and any language)."""
        language = self.project.language or "Python"
        if self._knowledge is None or self._knowledge.language != language:
            self._knowledge = Knowledge(language)
        return self._knowledge

    def format_code(self):
        """The toolchain's formatter run over the project, when it has one (after setup: it may be
        installed there): the model never formats code itself. Formatting only tidies: when it
        fails, the project goes on without it, and it is not remembered as a command that works."""
        command = self.toolchain.format
        if not command:
            return
        ok, failed = self.setup()
        if not ok:
            return
        code, output = self.ws.run(self.toolchain.shell(command), timeout=SETUP_TIMEOUT, quiet=True)
        if code == 0:
            print(f"  formatted the code: $ {command}")
            return
        print(f"  note: the formatter does not work ({first_line(output.strip(), 160) or f'exit code {code}'}); "
              f"the code stays as written and the project goes on without `{command}`")
        self.project.spec["toolchain"] = dict(self.toolchain.to_dict(), format="")
        self.save()

    def save(self):
        self.project.save()

    @property
    def toolchain(self):
        return self.project.toolchain

    def setup(self, force=False):
        """Run the setup commands when they or the config files changed; prepare .env."""
        self.ws.prepare_env()
        if not force and self.setup_key() == self.project.spec.get("setup_key"):
            return True, ""
        ok, output, self.network_waiting = self.toolchain.run_setup(self.ws)
        if ok and not self.network_waiting:
            self.setup_done()
        return ok, output

    def setup_key(self):
        """What setup depends on: its commands and the config files."""
        configs = sorted(p for p in self.project.paths("config") if p not in (ENV_FILE, ENV_EXAMPLE))
        return fingerprint(json.dumps(self.toolchain.setup) + "".join(self.ws.read(p) for p in configs))

    def setup_done(self):
        self.project.spec["setup_key"] = self.setup_key()
        self.save()

    def wait_for_network(self):
        """When all that needs no network is done and some setup still waits for it: that batch is
        tried again every NETWORK_RETRY seconds until the network is back, then the run goes on by
        itself (a person who will not wait stops it with Ctrl+C and runs tinker resume later)."""
        if not self.network_waiting:
            return
        began = noted = time.monotonic()
        print(f"  waiting for the network: {len(self.network_waiting)} setup command(s), tried every "
              f"{NETWORK_RETRY}s (Ctrl+C stops; tinker resume goes on later)")
        while self.network_waiting:
            time.sleep(NETWORK_RETRY)
            ok, output, self.network_waiting = self.toolchain.run_setup(self.ws, self.network_waiting)
            if not ok:  # it reached the network and failed for a reason of its own: the checks that follow report it
                return
            if self.network_waiting and time.monotonic() - noted >= NETWORK_NOTE:
                noted = time.monotonic()
                print(f"  still waiting for the network ({human_duration(noted - began)})")
        print(f"  the network is back after {human_duration(time.monotonic() - began)}: setup done")
        self.setup_done()

    def ask(self, system, parts, label, **kw):
        """A model call whose reply is files (FILE or LINES blocks), ended as soon as it is complete."""
        kw.setdefault("done_when", Reply.files_complete)
        return Reply(self.model.ask(system, parts, label, **kw))

    def ask_until_usable(self, system, parts, label, read, attempts=3, think=None, max_reply=None):
        """read() of the first usable JSON reply (Model.ask_until_usable); when none is, or the
        prompt cannot fit even shortened, the run stops here with why."""
        try:
            result = self.model.ask_until_usable(system, parts, label, read, attempts, think, max_reply)
        except PromptTooLarge as exc:
            raise SystemExit(f"{label}: even shortened, the prompt does not fit {self.model.name}'s input budget "
                             f"of {self.model.max_tokens} tokens ({exc}). Measure the model again (tinker calibrate) "
                             f"or use one that reads longer prompts (tinker models).")
        if result is None:
            raise SystemExit(f"Could not get a usable {label.lower()}. See .tinker/log.jsonl for the replies.")
        return result

    def room(self, system):
        """Chars the user message may take next to this system prompt, with a little slack (the
        chars-per-token estimate moves with every real count)."""
        return int(self.model.limit_chars(system + Prompts.NO_TOOL_CALLS) * 0.95)

    def run_all_tests(self):
        """All tests, once per state of the project's files: a slow or hanging suite is not run twice."""
        key = fingerprint("".join(p + self.ws.read(p) for p in sorted(self.project.files)))
        if self.all_tests_run and self.all_tests_run[0] == key:
            return self.all_tests_run[1]
        result = self.toolchain.run_all(self.ws)
        self.all_tests_run = (key, result)
        return result

    def knowledge_parts(self, *phases):
        """What is known for these phases, as optional parts (dropped first when the prompt is
        full): lessons from earlier projects, then the facts learned from the web about the topics
        the recall phase chose for this project, the most related to the request first, and for the
        toolchain the person's own commands (tinker learn NAME)."""
        lessons = self.knowledge.lessons(phases)
        facts = [f"- [{item['name']}] {item['text']}" for item in self.project.spec.get("learned", [])
                 if Library.shown_in(item, phases)]
        parts = [Part("Lessons from earlier projects (follow them)", "\n".join(f"- {lesson}" for lesson in lessons),
                      atomic=True, place="first"),
                 Part("Learned from the web (current; newer than what you were trained on)", "\n".join(facts),
                      place="first")]
        return parts + ([TaughtCommands.prompt_part()] if "toolchain" in phases else [])

    def note(self, kind, text):
        """A problem this run solved, kept for the lessons learned when the cycle ends."""
        events = self.project.spec.setdefault("events", [])
        events.append({"kind": kind, "text": first_line(text, 300)})
        del events[:-30]

    def files_on_disk(self, *kinds):
        return [p for p in self.project.paths(*kinds) if self.ws.exists(p)]


class CycleRunner:
    """A cycle of the project itself, of a feature (tinker add) or of a bug fix (tinker fix): Tinker's idea in
    order (PHASES, FIX_PHASES): plan the task as a spec, install the test tool, a test for every spec
    point, just enough code to pass them, tidied, one final review, done. Resumable at
    each phase: a phase is saved as done once it ends."""

    def __init__(self, session):
        self.s, self.p = session, session.project

    def show_plan(self):
        print(f"Plan: {len(self.p.criteria)} spec criteria (see SPEC.md)")
        for s in self.p.criteria:
            print(f"  {s['id']:5} [{STATUS_TEXT.get(s['status'], s['status'])}] effort {s.get('effort', 2)} "
                  f"{s['title']}  -> {s['test']}")

    def run(self, cycle, plan_only=False):
        """Continue a cycle from its saved phase: "done", "blocked" (a phase stopped it), "planned"
        (--plan-only, once planning is done) or "awaiting" (its spec waits for the person to confirm it)."""
        phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
        while cycle["phase"] != "done":
            if cycle.get("awaiting"):
                return "awaiting"
            phase = cycle["phase"]
            if plan_only and phase not in PLANNING_PHASES:
                self.show_plan()
                return "planned"
            if self.run_phase(phase, cycle) is False:
                return "blocked"
            if phase in FORMAT_AFTER:
                self.s.format_code()
            cycle["phase"] = phases[phases.index(phase) + 1]
            if phase == "spec" and cycle.get("confirm") and not cycle.get("confirmed"):
                cycle["awaiting"] = True  # the person confirms it (tinker resume) or changes it (tinker amend)
            self.s.save()
            if phase == "review":
                self.show_plan()
        return "done"

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


if __name__ == "__main__":
    main()
