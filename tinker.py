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
    tinker.py --calibrate NAME                 measure a model Ollama has (once per model)
    tinker.py --use-model NAME                 the measured model every run uses
    tinker.py --embed NAME                     the embedding model that indexes what is learned
    tinker.py --notify-command "COMMAND"       where notifications go (--notify sends one)
    tinker.py --learn-language LANGUAGE [--notify]   learn a language as it is today, with its tools
    tinker.py --learn TOPIC [--notify]         learn a topic from the web (Flask, TDD, ...)
    tinker.py --learn-command NAME ["DEFINITION"]    a command of yours Tinker may use (an alias, a program)
    tinker.py "Build a short url service" [spec.md] [--plan-only] [--web] [--notify]
    tinker.py --add [ID] "Add a feature" [--spec feature.md] [--web] [--notify]
    tinker.py --fix [ID] "Describe a bug: what you do, what happens, what should happen" [--web] [--notify]
    tinker.py --new "Build it again from scratch" [spec.md]   (empties this folder first, after asking)
    tinker.py --resume [ID]
    tinker.py --status [ID]
    tinker.py --runs
    tinker.py --models | --calibrate NAME [--tokens N | --reply-tokens N]
    tinker.py --list-config | --reset | --memory

Tinker has no model of its own choosing: measure one (--calibrate NAME), select it (--use-model
NAME), and every run uses that one. Every command is explicit: one command or one run at a time,
names as Ollama lists them, and an option the command would not use is an error, not ignored. All
that can be checked without the model (the values, the selected model, the project folder, the
run, the spec file, the open cycle) is checked before a folder is emptied or the model is loaded;
nothing is measured unasked.

The idea: plan the task in detail and write it as a spec; turn every spec point into a test (with
the test tool installed and working first); write just enough code to pass them, so there is no
code the spec does not ask for; check the built app as a user would; review it once at the end, the
way a person will run it; then it is done:

  0. Recall      the learned topics the request needs (searched by words and meaning); with --web,
                 the missing ones learned now.
  1. Spec        the language and kind of app; the spec criteria, each one behaviour of one module
                 of the app's own code with one fast test through its public interface, quoting the
                 request words it comes from; the acceptance checks of the app's main flow a user would
                 make once it is built; settings in .env (defaults in .env.example); constraints and
                 non-goals. Nothing outside the app is tested: an external service sits behind one thin
                 adapter with no criteria (the user tries it by hand), and the code using it takes it
                 as a parameter, so tests pass sample data: no HTTP fakes, no helper files. Then one
                 coverage call compares the request with the criteria and adds what none covers (the
                 app does only what its criteria test).
  2. Design      the layout (each file's kind, role and the files it uses; the file a user starts),
                 then the toolchain in the language's own tools (setup, check, test_all, test_one, run,
                 whether run serves until stopped, a smoke command), then each code file's contract in
                 its own call, after the code it uses (whose contracts it sees whole): the exact names,
                 signatures, data shapes and strings, and what each template it renders is given and
                 must show (a template has no contract of its own). Planning prompts are never cut:
                 criteria that do not fit one call are given over several, extending the contract.
  3. Review      each file that uses other code files, checked against their contracts (one call
                 each, more if they do not fit whole). A problem named without a correction gets that
                 file's contract written again; files that changed are checked once more.
  4. Setup       before any test is written: the config files (dependencies, the test tool's
                 settings) in one call, set up, and the test tool shown to run (it finds no tests
                 yet), so the tests are written for a test tool that is there and works.
  5. Spec tests  a few per call, then the test tool must reach them.
  6. Code        one call per file in dependency order: just enough to pass the tests.
  7. Verify      the tools still work and the project must
                 build (its check command loads every module: a missing dependency, a bad import or a
                 syntax error gets a fix that sees the error, the files it names and the dependency
                 file), then each failing criterion: fix the code until its test passes; when fixes
                 stop making progress, one review against the plan decides whether the test or the code
                 is wrong. A test that does not finish fails after test_timeout. A change that breaks a
                 passing test is reverted; what nothing helps is parked.
  8. Acceptance  the app is built: first the start check (the run command starts it the way a user
                 does: a server must keep serving on the PORT setting and answer the smoke command, a
                 command must succeed; import and module errors show here), then the acceptance checks
                 get their tests, through the app's entry point, and all are made to pass the same way.
  9. Final review  once, no model call: set up afresh from the project's own files, the whole test
                 suite run with the test command SPEC.md and the README give (as a person runs it),
                 and every spec point's test there and passing. A tooling problem gets one toolchain
                 fix; what still fails is said plainly, and the run is not done.
 10. README      setup, settings, run and test, from the toolchain.
 11. Lessons     one call turns the problems the run solved into lessons for the next projects.
--plan-only stops after the review so the plan (SPEC.md) can be read first.

Learning: a model knows what it was trained on, as it was then. Tinker learns three things:
  --learn-language LANGUAGE  a programming language as it is today, e.g. "Python 3.14 with pytest,
      black and venv". Every language gets the same sections, each with its own searches and share of
      the facts: fundamentals (syntax, keywords, types, errors, modules, the concurrency and runtime
      model), what is new (the newest stable releases), its tools (one per role: environment,
      packages, build, tests, format, lint: the ones named, else the usual ones; how to install
      and run each) and conventions. Saved in learned/<language>.json; every project in that
      language uses it.
  --learn TOPIC  a framework, a library, a technique such as TDD: a few searches, at least one for
      each thing it names, on how to use it today and what changed (a language: --learn-language).
  --learn-command NAME ["DEFINITION"]  a command of yours, e.g. activate: an alias (given, or read
      from your shell) or a program on PATH. The toolchain uses it where it fits (aliases are
      defined in each command's shell, where your shell's own are not); SPEC.md and the README show
      it spelled out.
For a language and a topic, the commands that show what is installed here run; the searches' pages
are read in turn (every search's first result, then the second ...) and condensed into facts, each
kept with its source, unreleased versions skipped.
A run uses them through its first phase, Recall: everything learned is searched for what the
request needs, by words (BM25: exact names and versions) and by meaning (the embedding model
selected with --embed NAME, which learning and --web need), the two rankings fused, so what either
finds is not missed; one call then picks the topics to use from the most related, and the project's
language as learned is always used; their facts, ranked the same way, go into the prompts of the
phases they concern, as an optional part (they never push out what is required). A run started with
--web may also learn then the topics it needs that are not learned yet (up to learn_missing).
The web is the Internet plugin: web search and web fetch through a backend (config.json's "web":
"ollama" is Ollama's web search and fetch, with a free ollama.com key in OLLAMA_API_KEY; a new
backend is a WebBackend). It is used only when asked: by learning, and by a run with --web; nothing
else goes online. Asked for and not usable, it says why before anything starts; lost part way,
the run carries on with what was learned before.

Each run is faster than the last. Tinker keeps a folder next to this script, .tinker/:
  config.json            the model in use, Ollama's address, keep_alive, the notification command
                         (--notify-command),
                         the Internet plugin (--learn, --web): web (its backend), web_timeout,
                         learn_missing; and the embedding model (--embed NAME).
  models/<model>.json    measured once per model (--calibrate NAME): the think setting it accepts,
                         the largest prompt it reads without crashing (some runners crash reading a
                         prompt above some size, whatever the context; measured on a freshly loaded
                         model, as runs use it), the longest reply it writes before GPU memory runs
                         out (writing fills it token by token), its speeds, chars per token. A call
                         that crashes the runner is tried again; a limit that keeps crashing drops 10%
                         for that run only (the run then says to measure again).
                         --calibrate NAME --tokens N / --reply-tokens N test one size by hand (and
                         raise the limit when it works); --list-config shows them; --models lists them.
  languages/<lang>.json  per language: the toolchain that last worked (the next design starts from
                         it), toolchain fixes (the same failure is fixed again without the model),
                         and lessons.
  learned/<topic>.json   what was learned from the web about each language and topic
                         (--learn-language, --learn, or a run's recall phase): facts, each with
                         its source, and what is installed here.
  commands.json          your own commands Tinker may use (--learn-command NAME).
  embeddings.json        the embedding of each learned text, made once (by the --embed model).
  learn.jsonl            every model call --learn made.
  memory.json            lessons for any language. Lessons are shown in the prompts of the phase
                         they concern, as optional parts (they never push out what is required).
  runs/                  run IDs, so --resume ID, --add ID and --fix ID work from anywhere.
All plain JSON: edit them by hand; --memory shows what was learned; --reset starts fresh.

Every request stays within the model's measured input budget, system prompt included (for a GGUF
model, calibration also tries Ollama's num_batch, which makes a runner read a long prompt in
pieces); replies are streamed and stopped once complete (models may run on with junk to the token
cap).

Files are small modules written whole (no language-specific merging). A file too big for a prompt
is shown as numbered lines around what matters and changed with a <<<LINES path A-B>>> block.

Commands come from the plan and run in a POSIX shell in the project folder (listed in SPEC.md;
sudo, deleting outside the project and piping downloads into a shell are refused). Each project
keeps its spec, run and log in its own .tinker folder. Everything printed also goes to
current.log.

Notifications are explicit too: with --notify, a run sends its summary when it ends, and --learn
when it is done, to the command set once with --notify-command, written as in a shell with
"{message}" where the summary goes (tried once with a test message, and kept only when it works):
  tinker.py --notify-command 'zeroclaw channel send "{message}" --channel-id telegram --recipient ID'
  tinker.py --notify-command 'notify-send Tinker "{message}"'
Without --notify nothing is sent; --notify-command none removes the command.

How the code reads: top-down. main() comes first, then the parts it uses in the order it uses
them, then the parts those use, and so on: higher-level code above lower-level code, down to the
small text helpers and, last, the prompts (Prompts).
Each part of Tinker is a class: the command line (Cli, Commands, Home), a run (ProjectRun,
RunChecks, Runs, Session), the phases of a cycle (CycleRunner and one Phase per phase, with the
Workers they share), the model (Model and the Tries of one request, Models, Calibrator, Ollama),
the project (Project, Workspace, Toolchain, Knowledge), learning (Recaller, Retriever, Embeddings, Learner, Library,
TaughtCommands; Internet, the only part
that goes online, with its WebBackend: OllamaWeb) and the common services (Console, EventLog,
Notifier, JsonFile, Config).
The constants below are every tunable number and fixed name, grouped by the part that uses them;
patterns and texts that only one class uses are defined in that class.
"""

import argparse
import collections
import datetime
import difflib
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# --- Tinker's own folder: settings, measured models, what it learned, the runs ------------------
HOME = Path(__file__).resolve().parent / ".tinker"  # next to the script (its real file, not a link)
CONFIG_FILE = HOME / "config.json"      # the model in use, Ollama's address, keep_alive, the notification command
MODELS_DIR = HOME / "models"            # <model>.json: what calibration measured for each model
LANGUAGES_DIR = HOME / "languages"      # <language>.json: a working toolchain, toolchain fixes, lessons
MEMORY_FILE = HOME / "memory.json"      # lessons that hold for any language
RUNS_DIR = HOME / "runs"                # id -> project, so --resume ID works anywhere
COMMANDS_FILE = HOME / "commands.json"  # the person's own commands Tinker may use (--learn-command)

DEFAULT_CONFIG = {                      # config.json starts as this; what the file holds overrides it
    "model": None,                      # the model every run uses: none until --use-model NAME selects one
    "ollama_url": "http://localhost:11434",  # where Ollama listens (Ollama's own default)
    "keep_alive": 0,                    # unload after every call: a model kept loaded can keep growing
    "max_growth": 1.15,                 # with keep_alive, unload once the model grows 15% past its first size
    "build_think": "medium",            # think level for building (tests, code, fixes); planning uses the model's
    "test_timeout": 60,                 # seconds one test may take: longer means it hangs
    "notify": [],                       # where --notify sends a summary (--notify-command sets it): a command's
                                        # arguments, "{message}" standing for it, e.g. ["notify-send", "Tinker",
                                        # "{message}"]; empty: none set
    "web": "ollama",                    # the Internet plugin's backend, used by --learn and --web only:
                                        # "ollama" (its key in OLLAMA_API_KEY), or "" for none
    "web_timeout": 30,                  # seconds one web search or fetch may take
    "learn_missing": 2,                 # topics the recall phase of a run with --web may learn
    "embed_model": "",                  # the embedding model that indexes what is learned (--embed NAME);
                                        # --learn and --web need one
}

# --- each project's own state ------------------------------------------------------------------
STATE_DIR = ".tinker"             # in each project: its spec, run and log
ENV_FILE, ENV_EXAMPLE = ".env", ".env.example"
LOG_NAME = "current.log"

# --- the work: cycles, phases, criteria, files -------------------------------------------------
# The idea, as the phases of one cycle: plan the task and write it as a spec; turn every spec point into a
# test, with the test tool installed first; write just enough code to pass them; check the app as a user
# would; review it once at the end, the way a person will run it; then it is done.
PHASES = (
    "recall",        # what was learned that the request needs
    "spec",          # plan: the spec points, each with its test, traced to the request
    "design",        # plan: the files, the toolchain, each file's contract
    "review",        # plan: each file checked against the contracts it uses
    "setup",         # the test tool and the dependencies installed, and shown to work, before any test
    "tests",         # a test for every spec point, before any code
    "code",          # just enough code to pass them
    "verify",        # ... fixed until every spec test passes
    "acceptance",    # acceptance tests: the built app used the way a user uses it
    "final_review",  # once at the end: set up afresh, every test run the way a person runs them
    "docs",          # the README
    "lessons",       # what the next projects should know
    "done")
FIX_PHASES = ("recall", "reproduce", "verify", "final_review", "lessons", "done")  # --fix: a test that shows the bug, then the fix
PLANNING_PHASES = ("recall", "spec", "design", "review")  # the phases --plan-only runs
FORMAT_AFTER = ("verify", "acceptance")  # phases after which the toolchain's formatter runs (the model never formats)
STATUS_TEXT = {"green": "passes", "todo": "to do", "active": "in progress", "blocked": "parked",
               "later": "after the build"}
RUN_TEST = "@run::starts"         # the start check's "test": the run command itself, not a test file
KINDS = ("code", "test", "template", "config", "docs")
MAX_FILE_LINES = 60               # design guidance: small modules (a bigger file still works, in windows)
TESTS_PER_CALL = 5

# --- fixing: until each criterion's test passes ------------------------------------------------
DEFAULT_MAX_STALL = 3             # fixes without progress before the review against the plan
MAX_TRIES_PER_PASS = 8            # model calls on one criterion per pass, whatever "progress" says
REVIEW_TRIES = 2                  # review calls when fixing stops making progress
BUILD_TRIES = 3                   # fixes for a project that does not build before the run stops
OUTPUT_CHARS = 1600               # the end of a failure output shown to the model
TESTS_LOCKED = "tests do not change now"  # why a fix may not change a test (a fix that tries points at the test)
FIX_FILES = 7                     # files a fix may be shown (the most related first)

# --- the project's commands (one test's limit is test_timeout in config.json) ------------------
SETUP_TIMEOUT = 900
COMMAND_TIMEOUT = 300             # setup aside, the longest a command may run (all tests)
START_TIMEOUT = 30                # the start check: seconds for a server to answer its smoke command
START_GRACE = 3                   # a server with no smoke command counts as started once it runs this long

# --- prompts: within the model's input budget --------------------------------------------------
CHARS_PER_TOKEN = 2.8             # the start (code ~2.9 chars/token); then learned from real prompts
MIN_CHARS_PER_TOKEN = 2.0         # HTML and dense code have fewer chars per token; never assume fewer
MAX_CHARS_PER_TOKEN = 4.0         # prose measured 4.1; never assume more
PROMPT_HEADROOM = 0.95            # aim 5% under the budget: code has fewer chars per token than prose
TRIM_FLOOR = 300                  # a shortened prose part keeps at least this many chars
MIN_PROMPT_TOKENS = 512           # a run never lowers its prompt budget below this

# --- replies -----------------------------------------------------------------------------------
MIN_REPLY_TOKENS = 1024
DEFAULT_FIX_MAX = 8192            # a fix that thinks longer than this has not answered in any run
EARLY_STOP_GRACE = 3.0            # seconds to await Ollama's last chunk (its token counts) once a reply is complete
PROGRESS_EVERY = 2.0              # seconds between live progress updates

# --- Ollama calls that fail --------------------------------------------------------------------
REQUEST_TIMEOUT = 600             # seconds of silence from Ollama before a call counts as stuck
STALL_RETRIES = 2
OOM_RETRIES = 2
OOM_SHRINK = 0.75                 # prompt budget factor for a retry after "out of memory" (that call only)
REPLY_ERROR_RETRIES = 1           # Ollama failing on what the model wrote: ask once more, then use what came
CRASH_RECOVERY = 10               # seconds a crashed runner gets to free its memory before the next call
CRASH_RETRIES = 3                 # a run: calls retried after the runner crashed (the first at the same size)
CRASH_SHRINK = 0.9                # a limit that crashed twice drops to this share of itself (a run: for that run
                                  # only); calibration takes this share of a longest reply that varies
CRASH_NEAR = 0.5                  # a run: a crash below this share of a limit is not about that limit

# --- calibration: a model's settings, measured once per model (models/<model>.json) ------------
DEFAULT_NUM_CTX = 32768           # context for the calibration probes; a model's own is its prompt budget plus
                                  # its longest reply (some runners ignore it and write on regardless)
DEFAULT_THINK = "high"            # think hard once instead of reworking cheap guesses (if the model takes it)
PROBE_START, PROBE_MAX = 1024, 8192  # prompt sizes tried (prompts never need more: whole files are small
                                  # modules, a fix shows at most FIX_FILES of them)
PROBE_BATCH = 256                 # piece size tried when a whole prompt crashes the runner (GGUF models)
PROBE_STEP = 1                    # the gap to the crash point is halved until it is this small
CONFIRM_READS = 2                 # the budget is the largest prompt read this many times in a row
CONFIRM_STEP = 0.99               # a budget that fails a confirming read is tried this much lower
REPLY_PROBE_MAX = 32768           # replies are written up to this many tokens to find where GPU memory runs out
                                  # (it fills token by token)
REPLY_CONFIRMS = 3                # writes that may confirm (or lower) the longest reply

# --- learning: what earlier projects taught (languages/<language>.json, memory.json) -----------
LESSON_PHASES = ("design", "config", "tests", "code", "toolchain")  # where a lesson is shown
MAX_LESSONS = 15                  # per file; the least confirmed go first
LESSONS_SHOWN = 5                 # per prompt (an optional part: dropped first when the prompt is full)
SAME_LESSON = 0.75                # text similarity at which a new lesson is an old one seen again
SAME_FACT = 0.75                  # text similarity at which a fact learned from the web is a repeat
SAME_CRITERION = 0.8              # text similarity at which a criterion the coverage check adds is a repeat
SIMILAR_FAILURE = 0.7             # similarity at which a past toolchain fix is shown as a hint

# --- learning from the web: --learn TOPIC, and the recall phase of a run --------------------------
LEARNED_DIR = HOME / "learned"    # <topic>.json: what was learned about a topic, with its sources
EMBEDDINGS_FILE = HOME / "embeddings.json"  # the embedding of each learned text, made once (--embed model)
LEARN_LOG = HOME / "learn.jsonl"  # every model call --learn makes
FACT_PHASES = ("spec", "design", "config", "tests", "code", "toolchain")  # where a learned fact is shown
MAX_QUERIES = 4                   # web searches per topic (at least one for each thing it names)
MAX_COMMANDS = 6                  # commands that show what of a topic is installed here
VERSION_TIMEOUT = 20              # seconds such a command may take
SEARCH_RESULTS = 3                # results taken from each search
MAX_PAGES = 5                     # pages read per topic
PAGE_CHUNKS = 2                   # prompt-sized chunks of a page condensed (from its top)
FACTS_PER_CHUNK = 4               # facts one chunk may give
MAX_FACTS = 16                    # facts kept per topic
TOOL_ROLES = ("environment", "packages", "build", "tests", "format", "lint")  # a language's tools, one per role
LANGUAGE_SECTIONS = ("fundamentals", "new", "conventions")  # what --learn-language searches besides its tools
SECTION_PAGES = {"fundamentals": 2, "new": 2, "conventions": 1, "tools": 1}  # pages read per search of a section
SECTION_FACTS = {"fundamentals": 10, "new": 10, "tools": 16, "conventions": 6}  # facts a language keeps per section
LANGUAGE_SEARCHES = 6             # searches of a language besides its tools
LANGUAGE_PAGES = 14               # pages read per language
LANGUAGE_COMMANDS = 8             # commands that show what of a language and its tools is installed here
RECALL_TOPICS = 6                 # learned topics one cycle uses at most


def main():
    """Tinker's command line: --reset, a command that shows or changes its settings (then it
    exits), or a run: a new project, or a resumed one, or a feature or a bug fix for one. What fails
    for a reason that is not a bug (Ollama, a file, Ctrl-C) ends with what happened, not a traceback."""
    script_started, started_at = time.perf_counter(), datetime.datetime.now()
    try:
        args = Cli.parse()
        if args.reset:
            return Home.reset()
        Home.ensure()
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
    """The command line, read and checked before anything runs: one command (or one run) at a
    time, no option that it would ignore, every value valid. Nothing is assumed: what is wrong is
    an error that says what to change. A run's options are then read into one shape."""
    # the commands that do one thing and exit, and the only options each one takes
    COMMANDS = {"reset": (), "list_config": (), "models": (), "use_model": (), "embed": (), "notify_command": (),
                "calibrate": ("tokens", "reply_tokens"), "learn": ("notify",), "learn_language": ("notify",),
                "learn_command": (), "memory": (), "runs": (), "status": ("workdir",)}

    # the options of a run: a new one ("task"), or one of RUNS
    RUN_OPTIONS = ("task", "spec", "spec_file", "new", "resume", "add", "fix", "workdir", "plan_only",
                   "max_prompt_tokens", "think", "keep_alive", "max_growth", "no_exclusive", "notify", "max_stall",
                   "web")

    RUNS = ("new", "resume", "add", "fix")

    USAGE = ('%(prog)s --calibrate NAME [--tokens N | --reply-tokens N]\n'
             '       %(prog)s --use-model NAME\n'
             '       %(prog)s --embed NAME\n'
             '       %(prog)s --notify-command "COMMAND"\n'
             '       %(prog)s --learn-language LANGUAGE [--notify]\n'
             '       %(prog)s --learn TOPIC [--notify]\n'
             '       %(prog)s --learn-command NAME ["DEFINITION"]\n'
             '       %(prog)s "task" [spec.md] [--plan-only] [--web] [--notify] [options]\n'
             '       %(prog)s --add [ID] "feature" [--spec FILE] [--web] [--notify]\n'
             '       %(prog)s --fix [ID] "bug" [--web] [--notify]\n'
             '       %(prog)s --new "task" [spec.md]   (empties this folder first, after asking)\n'
             '       %(prog)s --resume [ID] [--notify]\n       %(prog)s --status [ID]\n       %(prog)s --runs\n'
             '       %(prog)s --models | --list-config | --reset | --memory')

    THINK = ("high", "medium", "low", "true", "false", "default")
    KEEP_ALIVE_RE = re.compile(r"-?\d+|-?(\d+(\.\d+)?(ns|us|ms|s|m|h))+")  # seconds, or a duration: 10m, 1h30m
    RUN_ID_RE = re.compile(r"[0-9a-fA-F]{3,}")

    @staticmethod
    def parse():
        """The command line's options, checked (a mistake exits with what to change)."""
        parser = argparse.ArgumentParser(
            allow_abbrev=False,  # an option is written in full: --cal is not taken to mean --calibrate
            description="Build a project with a local Ollama model: plan in detail, spec tests, code, acceptance checks. "
                        "One command at a time; an option the command does not use is an error.",
            usage=Cli.USAGE)
        Cli.add_run_options(parser.add_argument_group("a run (a new one, or one of --new, --resume, --add, --fix)"))
        Cli.add_command_options(parser.add_argument_group("commands (one at a time; each does one thing and exits)"))
        args = parser.parse_args()
        Cli.check(args, parser)
        return args

    @staticmethod
    def add_run_options(group):
        group.add_argument("task", nargs="?", help="What to build (a new run only)")
        group.add_argument("spec", nargs="?", help="Optional spec file of a new run, e.g. plan.md")
        group.add_argument("--workdir", help="the project folder, which must exist (default: this folder); "
                                              "for a run, or --status without an ID")
        group.add_argument("--resume", nargs="?", const="", default=None, metavar="ID",
                            help="continue a run: by ID from anywhere, or without ID the run in this folder")
        group.add_argument("--add", nargs="+", metavar=("[ID]", "FEATURE"),
                            help='add a feature to an existing run: --add "..." or --add ID "..."')
        group.add_argument("--fix", nargs="+", metavar=("[ID]", "BUG"),
                            help='fix a bug in an existing run: a test that shows it, then a fix that makes it pass and '
                                 'keeps every other test passing: --fix "..." or --fix ID "..."')
        group.add_argument("--spec", dest="spec_file", metavar="FILE",
                            help="spec file: for a new run, or for the feature given with --add")
        group.add_argument("--plan-only", action="store_true",
                            help="plan (spec, design, review: SPEC.md) and stop for review; continue with --resume")
        group.add_argument("--web", action="store_true",
                            help="let this run use the Internet plugin (web in config.json): its recall phase may "
                                 "learn from the web the topics the request needs that are not learned yet; without "
                                 "--web a run never goes online")
        group.add_argument("--notify", action="store_true",
                            help="send a summary when this run ends (or when --learn is done) to the command set with "
                                 "--notify-command; without --notify nothing is sent")
        group.add_argument("--max-stall", type=int,
                            help=f"fixes without progress before the review against the plan (default {DEFAULT_MAX_STALL})")
        group.add_argument("--new", metavar="TASK",
                            help="start over in the project folder: delete everything in it (its .tinker too), "
                                 "after asking, and start a fresh run of TASK")
        group.add_argument("--max-prompt-tokens", type=int,
                            help=f"override the model's measured input budget for this run (at least {MIN_PROMPT_TOKENS})")
        group.add_argument("--think", choices=Cli.THINK,
                            help="override the model's think setting for this run (default: send none)")
        group.add_argument("--keep-alive",
                            help=f"Ollama keep_alive: seconds, or a duration such as 10m (default from {CONFIG_FILE.name}: "
                                 f"keep_alive; 0 unloads after each call, memory stays flat; a model kept loaded is "
                                 f"guarded by --max-growth)")
        group.add_argument("--max-growth", type=float,
                            help=f"with --keep-alive, unload the model once it grows by this factor, above 1 (default "
                                 f"from {CONFIG_FILE.name}: max_growth)")
        group.add_argument("--no-exclusive", action="store_true", help="do not unload models loaded by other apps")

    @staticmethod
    def add_command_options(group):
        group.add_argument("--status", nargs="?", const="", default=None, metavar="ID",
                            help="show the criteria of a run and whether their tests pass, then exit")
        group.add_argument("--runs", action="store_true", help="list runs and exit")
        group.add_argument("--models", action="store_true", help="list the installed models (* the default) and exit")
        group.add_argument("--use-model", metavar="NAME",
                            help="the model every run uses from now on: one measured with --calibrate NAME")
        group.add_argument("--embed", metavar="NAME",
                            help="the embedding model (one Ollama has) that indexes what is learned, for the recall "
                                 "phase's search by meaning; --learn and --web need one")
        group.add_argument("--notify-command", metavar="COMMAND",
                            help='where --notify sends a summary from now on: a command written as in a shell, '
                                 '"{message}" where the summary goes, e.g. \'zeroclaw channel send "{message}" '
                                 '--channel-id telegram --recipient ID\'; tried once with a test message and kept only '
                                 'when it works; "none" removes it')
        group.add_argument("--calibrate", metavar="NAME",
                            help="measure a model Ollama has (its name as --models lists it) and exit")
        group.add_argument("--reply-tokens", type=int, metavar="N",
                            help="with --calibrate: only test whether a measured model writes one reply of N tokens; "
                                 "when it does and N is above its longest reply, that becomes N")
        group.add_argument("--list-config", action="store_true",
                            help="show the settings a run would use (model, input budget, reply limit, think) and exit")
        group.add_argument("--reset", action="store_true",
                            help=f"delete {HOME} (settings, measured models, what was learned, the run index) "
                                 "to start fresh; projects are not touched")
        group.add_argument("--tokens", type=int, metavar="N",
                            help="with --calibrate: only test whether a measured model reads one prompt of N tokens; "
                                 "when it does and N is above the prompt budget, the budget becomes N")
        group.add_argument("--learn-language", metavar="LANGUAGE",
                            help='learn a programming language from the web as it is today, e.g. "Python 3.14 with '
                                 'pytest, black and venv": its fundamentals, what is new, its tools (the ones named, '
                                 'else the usual ones) and conventions; every project in it uses what was learned; '
                                 'needs the Internet plugin and an embedding model')
        group.add_argument("--learn", metavar="TOPIC",
                            help="learn a topic from the web with the model in use (a framework, a library, a "
                                 "technique such as TDD; a language: --learn-language) and keep it for the runs that "
                                 "need it, then exit; needs the Internet plugin (web in config.json)")
        group.add_argument("--learn-command", nargs="+", metavar=("NAME", "DEFINITION"),
                            help='a command of yours Tinker may use, e.g. --learn-command activate: an alias (its '
                                 'definition given, e.g. ". .venv/bin/activate", or read from your shell) or a program '
                                 'on PATH; the toolchain uses it where it fits; "none" as DEFINITION forgets it')
        group.add_argument("--memory", action="store_true",
                            help="show what Tinker learned (toolchains, fixes, lessons, topics) and exit")

    @staticmethod
    def check(args, parser):
        """One command or one run, with only the options it takes, each value valid; then the
        run's options in one shape and the defaults filled in (from config.json where it has them)."""
        given = [name for name, value in vars(args).items() if value is not None and value is not False]
        commands = [name for name in Cli.COMMANDS if name in given]
        if len(commands) > 1:
            parser.error(f"one command at a time: {' and '.join(Cli.flag(c, args) for c in commands)}")
        allowed = Cli.COMMANDS[commands[0]] if commands else Cli.RUN_OPTIONS
        extra = [name for name in given if name not in allowed and name not in commands]
        if extra:
            parser.error(Cli.not_taken(commands[0] if commands else None, extra, args))
        Cli.check_values(args, parser)
        if commands:
            Cli.check_command(commands[0], args, parser)
        else:
            Cli.check_run(args, parser)
        Cli.fill_defaults(args, loads_model=not commands or commands[0] in ("learn", "learn_language"))

    @staticmethod
    def flag(name, args):
        """How an option is written on the command line (a positional by its value)."""
        if name in ("task", "spec"):
            return f'"{getattr(args, name)}"'
        return "--spec" if name == "spec_file" else "--" + name.replace("_", "-")

    @staticmethod
    def not_taken(command, extra, args):
        """Why options do not go with this command (or with a run, when command is None)."""
        if command is None:
            owners = {name: [c for c, options in Cli.COMMANDS.items() if name in options] for name in extra}
            reasons = []
            for name in extra:
                commands = " or ".join(Cli.flag(c, args) for c in owners[name])
                reasons.append(f"{Cli.flag(name, args)} works only with {commands}")
            return "; ".join(reasons)
        takes = Cli.COMMANDS[command]
        what = f"takes only {' and '.join(Cli.flag(o, args) for o in takes)}" if takes else "takes no other option"
        return f"{Cli.flag(command, args)} {what}: remove {', '.join(Cli.flag(n, args) for n in extra)}"

    @staticmethod
    def check_values(args, parser):
        for name in ("task", "spec", "spec_file", "new", "workdir", "use_model", "embed", "notify_command", "calibrate"):
            value = getattr(args, name)
            if value is not None and not value.strip():
                parser.error(f"{'--' + name.replace('_', '-') if name not in ('task', 'spec') else 'the ' + name} "
                             f"is empty: give a value or leave it out")
        if args.learn is not None and not Library.slug(args.learn):
            parser.error(f"--learn {args.learn!r}: give a topic in words, e.g. --learn TDD or --learn Flask")
        if args.learn_language is not None and not Library.slug(args.learn_language):
            parser.error(f"--learn-language {args.learn_language!r}: give a language, e.g. --learn-language "
                         f"\"Python 3.14 with pytest, black and venv\"")
        if args.learn_command is not None and (len(args.learn_command) > 2 or not args.learn_command[0].strip()):
            parser.error('use --learn-command NAME or --learn-command NAME "DEFINITION" (quote the definition)')
        for name in ("resume", "status"):
            value = getattr(args, name)
            if value and not Cli.RUN_ID_RE.fullmatch(value):
                parser.error(f"'{value}' is not a run ID (list them with --runs)")
        minimum = {"max_stall": 1, "max_prompt_tokens": MIN_PROMPT_TOKENS, "tokens": 1, "reply_tokens": 1}
        for name, least in minimum.items():
            if getattr(args, name) is not None and getattr(args, name) < least:
                parser.error(f"--{name.replace('_', '-')} must be at least {least}")
        if args.max_growth is not None and args.max_growth <= 1:
            parser.error("--max-growth must be above 1 (1.15 unloads the model once it is 15% larger)")
        if args.keep_alive is not None and not Cli.KEEP_ALIVE_RE.fullmatch(args.keep_alive):
            parser.error(f"--keep-alive {args.keep_alive}: give seconds (0, 300; -1 keeps it loaded) or a duration "
                         f"(10m, 1h)")

    @staticmethod
    def check_command(command, args, parser):
        if command == "calibrate" and args.tokens is not None and args.reply_tokens is not None:
            parser.error("--tokens and --reply-tokens each test one size: give one of them")
        if command == "status" and args.status and args.workdir is not None:
            parser.error(Cli.finds_its_folder("--status"))

    @staticmethod
    def check_run(args, parser):
        """A new run of a task, or one of --new, --resume, --add, --fix: read into one shape. A
        feature or a bug becomes a resume of its run with a request (args.change_request; args.bug
        for a bug); args.spec is the spec file of a new run."""
        runs = [name for name in Cli.RUNS if getattr(args, name) is not None]
        if len(runs) > 1:
            parser.error(f"{' and '.join(Cli.flag(r, args) for r in runs)} are different runs: give one")
        kind = runs[0] if runs else "task"
        args.bug, args.change_request = kind == "fix", None
        if kind in ("task", "new"):
            Cli.check_new_run(kind, args, parser)
        elif kind == "resume":
            Cli.check_resume(args, parser)
        else:
            Cli.check_request(kind, args, parser)

    @staticmethod
    def fill_defaults(args, loads_model):
        """What was left out, from the built-in defaults, and for what loads a model (a run, --learn)
        from config.json (keep_alive, max_growth: checked by Config.check_model)."""
        args.workdir = args.workdir if args.workdir is not None else "."
        args.max_stall = args.max_stall if args.max_stall is not None else DEFAULT_MAX_STALL
        if loads_model:
            args.keep_alive = args.keep_alive if args.keep_alive is not None else Config.get("keep_alive")
            args.max_growth = args.max_growth if args.max_growth is not None else Config.get("max_growth")

    @staticmethod
    def finds_its_folder(flag):
        return f"{flag} ID finds the run's folder itself: remove --workdir (or give --workdir and no ID)"

    @staticmethod
    def check_new_run(kind, args, parser):
        if kind == "task" and not args.task:
            parser.error('give a task, e.g. tinker.py "Build a tool in Python", or --resume ID')
        if kind == "new":
            if args.task and args.spec:
                parser.error('use --new "task" [spec.md]: the task goes with --new, then one spec file at most')
            args.task, args.spec = args.new.strip(), args.task  # --new "task" spec.md: the positional is the spec
        if args.spec and args.spec_file:
            parser.error("give the spec once: positionally or with --spec")
        args.spec = args.spec or args.spec_file

    @staticmethod
    def check_resume(args, parser):
        given = [Cli.flag(name, args) for name in ("task", "spec", "spec_file") if getattr(args, name)]
        if given:
            parser.error(f"--resume continues a run with its saved task and spec: remove {', '.join(given)}")
        if args.resume and args.workdir is not None:
            parser.error(Cli.finds_its_folder("--resume"))

    @staticmethod
    def check_request(kind, args, parser):
        """--add [ID] "feature" or --fix [ID] "bug"."""
        flag, what = f"--{kind}", ("bug" if kind == "fix" else "feature")
        request = args.fix if kind == "fix" else args.add
        if len(request) > 2 or args.task or args.spec or (kind == "fix" and args.spec_file):
            parser.error(f'use {flag} "{what}" or {flag} ID "{what}" (quote the {what}'
                         + ("" if kind == "fix" else "; pass a feature spec with --spec FILE") + ")")
        run_id, text = (request[0], request[1]) if len(request) == 2 else ("", request[0])
        if not text.strip():
            parser.error(f"{flag} needs a {what} description")
        if run_id and not Cli.RUN_ID_RE.fullmatch(run_id):
            parser.error(f"'{run_id}' is not a run ID. Use {flag} \"{what}\" or {flag} ID \"{what}\"")
        if run_id and args.workdir is not None:
            parser.error(Cli.finds_its_folder(flag))
        if kind == "fix" and args.plan_only:
            parser.error("--plan-only stops after planning, and a --fix plans nothing: it writes a test that shows "
                         "the bug, then fixes the code")
        args.add, args.fix, args.change_request, args.resume = [text], None, text.strip(), run_id


class Home:
    """Tinker's own folder next to the script (.tinker): config.json, models/, languages/,
    memory.json, learned/, embeddings.json, learn.jsonl, commands.json and runs/. Made on first use; --reset
    deletes it."""

    @staticmethod
    def reset():
        """--reset: delete Tinker's folder next to the script (settings, measured models, lessons,
        what was learned from the web, the run index), after asking; the next command starts fresh.
        Projects keep their own .tinker folders, so --resume works inside them."""
        if not HOME.is_dir():
            print(f"Nothing to reset: {HOME} does not exist.")
            return
        if HOME.name != ".tinker":
            raise SystemExit(f"Not deleting {HOME}: it is not a .tinker folder.")
        print(f"This deletes {HOME}:")
        for item in sorted(HOME.iterdir()):
            print(f"  {item.name}{'/' if item.is_dir() else ''}")
        print("Projects are not touched (each keeps its own .tinker folder).")
        if not sys.stdin.isatty():
            raise SystemExit("Nothing deleted: run --reset in a terminal to confirm.")
        if input("Delete it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return
        shutil.rmtree(HOME)
        print(f"Deleted {HOME}. The next command starts fresh.")

    @staticmethod
    def ensure():
        """Tinker's folder next to the script, with config.json written with the defaults on first use."""
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            if not CONFIG_FILE.is_file():
                Config.save()
                print(f"  created {CONFIG_FILE} (default model, Ollama's address, keep_alive, notifications)")
        except OSError as exc:
            raise SystemExit(f"Tinker keeps its settings in {HOME}, which cannot be written: {exc}")


class Commands:
    """The commands that show or change Tinker's settings, models, what it knows and runs, then
    exit: --list-config, --models, --use-model, --embed, --notify-command, --calibrate,
    --learn-language, --learn, --learn-command, --memory, --runs, --status."""

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
        elif args.notify_command:
            Notifier.set_command(args.notify_command)
        elif args.calibrate is not None:
            self.calibrate()
        elif args.learn or args.learn_language:
            self.learn()
        elif args.learn_command:
            TaughtCommands.teach(*args.learn_command)
        elif args.memory:
            self.show_memory()
        elif args.runs:
            self.list_runs()
        elif args.status is not None:
            self.show_status(args.status, args.workdir)
        else:
            return False
        return True

    @staticmethod
    def list_config():
        """--list-config: the settings a run would use, to check them (and measure again) first."""
        name = Config.get("model")
        settings = Models.settings(name) if name else None
        print(f"config:          {CONFIG_FILE}")
        print(f"ollama:          {Config.ollama_url()}")
        if not name:
            state = "none (measure one with --calibrate NAME, then select it with --use-model NAME)"
        elif settings:
            state = f"{name} (calibrated {settings.get('calibrated', '?')})"
        else:
            state = f"{name} (not measured: --calibrate {name})"
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
        print(f"notify:          {Notifier.describe()}")
        print(f"web:             {Config.get('web') or 'none'} ({Internet.state()}; used by --learn and --web only)")
        print(f"learn_missing:   {Config.get('learn_missing')} (topics a run with --web may learn)")
        embed = Config.get("embed_model") or "none: select one with --embed NAME (--learn and --web need one)"
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
                  f"{s.get('calibrated', 'not measured: --calibrate NAME')}")
        print("\n* the model in use. Measure one with --calibrate NAME, select it with --use-model NAME.")

    @staticmethod
    def use_model(name):
        """--use-model NAME: the model every run uses from now on, one already measured (nothing
        is measured unasked)."""
        if Models.settings(name) is None:
            raise SystemExit(f"{name} has not been measured: measure it first with --calibrate {name} "
                             f"(--models lists the models Ollama has)")
        Config.save(model=name)
        print(f"Model in use: {name} (in {CONFIG_FILE})")

    @staticmethod
    def embed(name):
        """--embed NAME: the embedding model that indexes what is learned from now on: one Ollama
        has that gives embeddings (tried once here)."""
        Models.installed(name)
        embeddings = Embeddings(name)
        if embeddings.vectors(["a test"], keep=False) is None:
            raise SystemExit(f"{embeddings.failed}: choose an embedding model (--models lists the models Ollama has)")
        Config.save(embed_model=name)
        print(f"Embedding model: {name} (in {CONFIG_FILE}); what was learned is indexed with it from the next "
              f"--learn or run on")

    def calibrate(self):
        """--calibrate NAME: measure a model (again); with --tokens N or --reply-tokens N, only test
        one prompt or one reply of that size."""
        args = self.args
        calibrator = Calibrator(Models.installed(args.calibrate))
        if args.tokens:
            calibrator.test(args.tokens)
        elif args.reply_tokens:
            calibrator.test_reply(args.reply_tokens)
        else:
            calibrator.run()

    def learn(self):
        """--learn TOPIC or --learn-language LANGUAGE: learned from the web with the model in use,
        saved and indexed (an embedding model and the Internet plugin must be there, and with
        --notify the notification command; when one is not, nothing is loaded and it says why).
        With --notify, how it went is sent when it is done, whatever happened (but Ctrl-C: the
        person is there)."""
        args = self.args
        option = "--learn-language" if args.learn_language else "--learn"
        topic = (args.learn_language or args.learn).strip()
        name, settings = Models.selected()
        Models.installed(name)
        Config.check_model(args, run=False)
        if args.notify:
            Notifier.required()
        embeddings = Embeddings.required(option)
        internet = Internet.required(option)
        started = time.perf_counter()
        model = Model(name, settings, Config.keep_alive(args.keep_alive), LEARN_LOG, max_growth=float(args.max_growth))
        learner = Learner(model, internet, embeddings, redirect_languages=True)
        try:
            try:
                entry = learner.learn_language(topic) if args.learn_language else learner.learn(topic)
            finally:
                model.unload()
                Models.remember(model)
        except (SystemExit, Exception) as exc:  # it stopped: the reason is printed as it ends, and sent
            if args.notify:
                Notifier.send(learner.notice(topic, None, time.perf_counter() - started, Commands.failure(exc)))
            raise
        if entry is not None:
            self.print_learned(topic, entry)
        if args.notify:
            Notifier.send(learner.notice(topic, entry, time.perf_counter() - started))
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
        print(f"\nLearned from the web ({LEARNED_DIR.name}/, --learn-language LANGUAGE, --learn TOPIC):"
              + ("" if topics else " nothing yet"))
        for slug, entry in topics.items():
            kind = "language" if entry.get("kind") == "language" else "topic"
            print(f"  {slug} ({kind}): {entry.get('name', slug)}, learned {entry.get('learned', '?')}, "
                  f"{len(entry.get('facts', []))} fact(s): {first_line(str(entry.get('summary', '')), 80)}")
        taught = TaughtCommands.all()
        print(f"\nYour commands ({COMMANDS_FILE.name}, --learn-command NAME):" + ("" if taught else " none yet"))
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
            print("No spec yet (planned on the next --resume).")
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
    def failure(exc):
        """Why a command stopped, in one line, from what stopped it."""
        if isinstance(exc, SystemExit):
            return exc.code if isinstance(exc.code, str) else f"it stopped (exit code {exc.code})"
        if isinstance(exc, OllamaError):
            return str(exc)
        return f"unexpected error: {type(exc).__name__}: {exc}"

    @staticmethod
    def print_learned(topic, entry):
        print(f"\n{entry['name']}: {entry['summary']}")
        for item in Library.items({Library.slug(entry.get("language") or topic): entry})[1:]:
            print(f"  - [{item['phase']}] {item['text']}")
        for note in entry["notes"]:
            print(f"  not learned: {note}")
        print("A run uses this when its recall phase finds the request needs it; --memory lists what was learned.")

    @staticmethod
    def print_lessons(lessons):
        for lesson in lessons:
            print(f"  lesson [{lesson.get('phase')}, seen {lesson.get('seen', 1)}x]: {lesson.get('text')}")
        if not lessons:
            print("  no lessons yet")


class ProjectRun:
    """One run on a project: a new project, or a resumed one, or a feature (--add) or a bug fix
    (--fix) for one. It opens the run and a session on it, works through the cycle that is open,
    saves whatever happens, and ends with a summary (and, with --notify, a notification)."""

    def __init__(self, args, script_started, started_at):
        self.args = args
        self.script_started, self.started_at = script_started, started_at
        self.session = self.record = self.mode = self.log = None  # record: the run's run.json
        self.internet = None  # the Internet plugin, with --web only
        self.spec_text = ""
        self.outcome = Outcome()

    def execute(self):
        # what the run needs is checked before a folder is emptied or the model loaded
        Models.installed(Models.selected()[0])
        Config.check_model(self.args, run=True)
        if self.args.notify:
            Notifier.required()
        TaughtCommands.all()  # a broken commands.json stops here, not part way
        if self.args.web:  # a run with --web learns what it needs: like --learn, it needs both
            Embeddings.required("--web")
            self.internet = Internet.required("--web")
        else:
            Embeddings.check()
        workdir = RunChecks.before_start(self.args)
        if self.args.new is not None:  # --new: a fresh run, in a folder emptied first
            spec = self.args.spec
            Runs.clear_folder(workdir, Path(spec).expanduser().resolve() if spec else None)
        Console.mirror(workdir)
        self.open()
        try:
            self.work()
        except OllamaError as exc:
            print(f"\n{exc}")
        except KeyboardInterrupt:
            print("\nInterrupted.")
            self.outcome.interrupted = True
        except SystemExit as exc:
            if exc.code not in (None, 0):
                print(f"\n{exc.code}" if isinstance(exc.code, str) else "")
        except Exception as exc:  # a bug must not lose the run: report it, save, and say how to continue
            details = traceback.format_exc()
            print(f"\nUnexpected error: {type(exc).__name__}: {first_line(str(exc), 300)}\n{details.rstrip()}")
            self.log.add({"label": "unexpected error", "error": details})
        finally:
            self.save()
        self.finish()
        sys.exit(self.outcome.exit_code)

    def open(self):
        """The model (measured first if new: before the run exists, so a model that fails leaves
        nothing behind), the run, its project and the session."""
        args = self.args
        model_name, settings = Models.for_run(args)
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
        self.session = Session(model, project, args.max_stall, self.internet)
        if project.language:
            print(f"learned so far: {self.session.knowledge.summary()}")

    def work(self):
        """The cycle to work on, run from its saved phase."""
        session, run = self.session, self.record
        brief = Brief.load(session.model, session.project.state, self.mode, run["task"], self.spec_text)
        cycle = self.choose_cycle(brief)
        if cycle is None:
            CycleRunner(session).show_plan()
            print("  every cycle of this run is done; add a feature with --add, or fix a bug with --fix")
            self.outcome.set("done")
            return
        self.outcome.set(CycleRunner(session).run(cycle, plan_only=self.args.plan_only))

    def save(self):
        """Whatever happened: the model unloaded, what the run learned about it kept, the run and
        the project saved."""
        model, project = self.session.model, self.session.project
        model.unload()
        Models.remember(model)
        self.record["status"] = self.outcome.status
        Runs.save(self.record, project.workdir)
        try:
            project.save()
        except OSError:
            pass

    def finish(self):
        """The summary, the times, the log and, with --notify, the notification."""
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
        if self.args.notify and not self.outcome.interrupted:  # after Ctrl-C the person is at the terminal already
            self.notify_summary(wall, ended_at)

    def make_model(self, name, settings, log_path):
        args = self.args
        return Model(name, settings, Config.keep_alive(args.keep_alive), log_path,
                     exclusive=not args.no_exclusive, max_growth=float(args.max_growth))

    def print_header(self, model, workdir):
        run, mode, args = self.record, self.mode, self.args
        change = "fixing a bug" if args.bug else "adding a feature"
        doing = {"new": "new", "resume": "resuming", "change": change}[mode]
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
        """The cycle to work on: a new feature or bug (--add, --fix), the unfinished one, or the
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
        return cycle

    def print_summary(self):
        status, session = self.outcome.status, self.session
        print("\n" + "=" * 60)
        if status == "done":
            self.summary_done()
        elif status == "planned":
            print(f"Planned: {session.project.workdir / 'SPEC.md'}")
            print(f"Read the plan (and its commands), then build with: tinker.py --resume {self.record['id']}")
        elif status == "blocked":
            self.summary_blocked()
        else:
            run_id = self.record["id"]
            print(f"Run {run_id} is not finished. Progress is saved; continue with:\n  tinker.py --resume {run_id}")
        print(f"Log: {session.project.state / 'log.jsonl'}")

    def notify_summary(self, wall, ended_at):
        session, run, outcome, workdir = self.session, self.record, self.outcome, self.session.project.workdir
        icon = {"done": "✅", "planned": "📝", "blocked": "⚠️"}.get(outcome.status, "❌")
        lines = [f"{icon} Tinker run {run['id']}: {outcome.status}", first_line(run["task"], 160)]
        project = session.project
        if project.criteria:
            green, parked, total = project.counts()
            lines.append(f"Criteria: {green}/{total} pass" + (f", {parked} parked" if parked else ""))
            lines += [f"parked {s['id']} {s['title']}: {first_line(s.get('last_error', ''), 120)}"
                      for s in project.parked()[:5]]
        if session.tool_problem or session.build_problem or session.review_problem:
            lines.append(session.tool_problem or session.build_problem or f"final review: {session.review_problem}")
        lines += [f"Time: {human_duration(wall)} ({clock(self.started_at)} - {clock(ended_at)})", f"Project: {workdir}"]
        if outcome.status != "done":
            lines.append(f"Continue: tinker.py --resume {run['id']}")
        Notifier.send("\n".join(lines))

    def summary_done(self):
        project, run_id = self.session.project, self.record["id"]
        spec, accept = project.level_counts()
        starts = any(Project.is_start_check(s) for s in project.criteria)
        print(f"Done. All {spec} spec tests" + (f" and {accept} acceptance checks" if accept else "")
              + (" (the start check among them)" if starts else "") + " pass (SPEC.md).")
        if project.toolchain.run:
            print(f"Run it: cd {project.workdir} && {project.toolchain.run}")
            print(f"Settings: {ENV_FILE} (defaults in {ENV_EXAMPLE}). The tests use sample data instead of external "
                  f"services: try those by hand with real settings.")
        print(f'Add a feature with: tinker.py --add {run_id} "..."; fix a bug with: tinker.py --fix {run_id} "..."')

    def summary_blocked(self):
        """Why it stopped: a bug no test showed, a project that does not build, tools that do not
        work, or criteria parked."""
        session, run_id = self.session, self.record["id"]
        if session.unreproduced:
            print(f"Not fixed: {session.unreproduced}")
        elif session.review_problem:
            print(f"Not done: the final review found {session.review_problem}")
            print(f"Correct it (or the request), then: tinker.py --resume {run_id}")
        elif session.build_problem:
            print(f"Stopped before working on criteria: {session.build_problem}")
            print(f"See current.log for each fix; correct it by hand or continue with: tinker.py --resume {run_id}")
        elif session.tool_problem:
            print(f"Stopped before working on criteria: {session.tool_problem}")
            print("No code change can fix that. Check the commands in SPEC.md (setup, test_all) and the dependency")
            print(f"file, then continue with: tinker.py --resume {run_id}")
        else:
            green, parked, total = session.project.counts()
            print(f"Stopped: {green}/{total} tests pass; {parked} parked after fixing and a review against the plan:")
            for s in session.project.parked():
                print(f"  {s['id']} {s['title']}: {first_line(s.get('last_error', ''), 160)}")
            print(f"Change what it cannot (the request, the plan, a dependency), then: tinker.py --resume {run_id}")


class OllamaError(Exception):
    """A call to Ollama that failed. oom: out of memory; crashed: the model's runner died (some
    runners do, reading a prompt above some size); stalled: no answer; in_reply: Ollama failed
    while streaming the reply (it could not parse what the model wrote, e.g. text that looks like a
    tool call): partial is what arrived before."""

    def __init__(self, message, oom=False, stalled=False, in_reply=False, partial="", crashed=False, streamed=0):
        super().__init__(message)
        self.oom, self.stalled, self.crashed = oom, stalled, crashed
        self.in_reply, self.partial = in_reply, partial
        self.streamed = streamed  # tokens the model had written (thinking included) when it failed


class Config:
    """config.json in Tinker's folder: DEFAULT_CONFIG, overridden by what the file holds
    (read once, when first needed)."""
    values = None

    @classmethod
    def save(cls, **changes):
        """The settings, with these changes, written to config.json."""
        cls.load().update(changes)
        JsonFile.write(CONFIG_FILE, cls.values)

    @staticmethod
    def check_model(args, run):
        """The settings a model takes from config.json, as this command uses them (an option given
        on the command line was checked there), and for a run also its tests' timeout: checked
        before anything starts. A command that loads no model never reads them."""
        if not Cli.KEEP_ALIVE_RE.fullmatch(str(args.keep_alive)):
            raise SystemExit(f"keep_alive in {CONFIG_FILE} must be seconds (0, 300; -1 keeps the model loaded) or a "
                             f"duration (10m, 1h); it is {args.keep_alive!r}")
        growth = args.max_growth
        if isinstance(growth, bool) or not isinstance(growth, (int, float)) or growth <= 1:
            raise SystemExit(f"max_growth in {CONFIG_FILE} must be a number above 1 (1.15 unloads the model once it "
                             f"is 15% larger); it is {growth!r}")
        if Config.get("build_think") not in ("high", "medium", "low", True, False, None):
            raise SystemExit(f'build_think in {CONFIG_FILE} must be "high", "medium", "low", true, false or null; it '
                             f"is {Config.get('build_think')!r}")
        timeout = Config.get("test_timeout")
        if run and (isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1):
            raise SystemExit(f"test_timeout in {CONFIG_FILE} must be a whole number of seconds, 1 or more; it is "
                             f"{timeout!r}")

    @classmethod
    def load(cls):
        if cls.values is None:
            cls.values = {**DEFAULT_CONFIG, **JsonFile.read(CONFIG_FILE, {}, keeps_work=True)}
        return cls.values

    @classmethod
    def get(cls, key):
        return cls.load()[key]

    @classmethod
    def ollama_url(cls):
        return str(cls.get("ollama_url")).rstrip("/")

    @staticmethod
    def keep_alive(value):
        """keep_alive as Ollama takes it: seconds as a number, a duration (10m) as it is."""
        return int(value) if re.fullmatch(r"-?\d+", str(value)) else value


class Notifier:
    """Notifications, sent only when --notify asks: a run's summary when it ends, --learn's when it
    is done. They go to the command set with --notify-command (config.json's notify: its arguments,
    run as they are, no shell, "{message}" standing for the summary), so any notifier works: ZeroClaw
    to Telegram, notify-send, a script of your own."""
    LIMIT = 3500  # characters of a message (Telegram takes 4096 at most)
    TIMEOUT = 60  # seconds the command may take
    EXAMPLE = 'zeroclaw channel send "{message}" --channel-id telegram --recipient ID'

    @staticmethod
    def set_command(text):
        """--notify-command COMMAND: where notifications go from now on, written as in a shell;
        tried once with a test message and kept only when it works. "none" removes it."""
        if text.strip().lower() == "none":
            Config.save(notify=[])
            print(f"No notification command (in {CONFIG_FILE}); --notify needs one")
            return
        try:
            command = shlex.split(text)
        except ValueError as exc:  # an unclosed quote
            raise SystemExit(f"--notify-command {text!r}: {exc}; write it as in a shell, e.g. '{Notifier.EXAMPLE}'")
        command[0] = os.path.expanduser(command[0])
        problem = Notifier.problem(command)
        if problem:
            raise SystemExit(f"--notify-command: {problem}; nothing changed")
        print(f"Sending a test message: {shlex.join(command)}")
        error = Notifier.run(command, "🔧 Tinker: notifications work. --notify sends a summary here when a run "
                                      "ends or --learn is done.")
        if error:
            raise SystemExit(f"The test message was not sent: {error}\nNothing changed: fix the command and set it again")
        Config.save(notify=command)
        print(f"Notification command set (in {CONFIG_FILE}); --notify on a run or --learn sends its summary there")

    @staticmethod
    def required():
        """The notification command, for --notify: set, well formed and found (checked before
        anything starts, not when it ends)."""
        command = Config.get("notify")
        if not command:
            raise SystemExit(f"--notify needs a notification command: set one with --notify-command, e.g.\n"
                             f"  tinker.py --notify-command '{Notifier.EXAMPLE}'")
        problem = Notifier.problem(command)
        if problem:
            raise SystemExit(f"notify in {CONFIG_FILE}: {problem}; set it again with --notify-command")
        return command

    @staticmethod
    def describe():
        """The notification command as it would be typed (for --list-config)."""
        command = Config.get("notify")
        if not command:
            return "none (set one with --notify-command; --notify sends a summary there)"
        if Notifier.problem(command).startswith("it must"):
            return f"{command!r} (not usable: set it again with --notify-command)"
        return f"{shlex.join(command)} (with --notify)"

    @staticmethod
    def problem(command):
        """What makes a command unusable ("" when it is usable)."""
        if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
            return f"it must be a list of a command's arguments; it is {command!r}"
        if not any("{message}" in a for a in command):
            return f'it has no "{{message}}" where the summary goes, e.g. \'{Notifier.EXAMPLE}\''
        if shutil.which(command[0]) is None:
            return f"{command[0]} is not found (not on PATH, nor a program at that path)"
        return ""

    @staticmethod
    def run(command, message):
        """The command run with the message in place of "{message}": "" when it worked, else why not."""
        args = [a.replace("{message}", message[:Notifier.LIMIT]) for a in command]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=Notifier.TIMEOUT)
        except OSError as exc:
            return f"{args[0]}: {exc.strerror or exc}"
        except subprocess.TimeoutExpired:
            return f"{args[0]} did not finish in {Notifier.TIMEOUT}s (the message may have been sent)"
        if proc.returncode != 0:
            return first_line((proc.stderr or proc.stdout).strip(), 200) or f"{args[0]} exited with {proc.returncode}"
        return ""

    @staticmethod
    def send(message):
        error = Notifier.run(Config.get("notify"), message)
        print("  notification sent" if not error else f"  note: notification not sent: {error}")


class TaughtCommands:
    """The person's own commands Tinker may use (--learn-command NAME [DEFINITION]), kept in
    commands.json next to the script: an alias (the shell code its name stands for: given, or read
    from the person's shell) or a program on PATH. The toolchain prompts list them, so the model
    uses them where they fit. Tinker runs each command in a new non-interactive POSIX shell, where
    the person's aliases do not exist, so a command that uses an alias gets its definition first, as
    a shell function. SPEC.md and the README show commands with aliases spelled out, so they work
    for anyone. Plain JSON: edit or delete at any time."""
    NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # a name a POSIX shell function can have
    SOURCE_RE = re.compile(r"(^|[;&|(]\s*)source\s+")  # "source" is not POSIX: "." is
    LOOKUP_TIMEOUT = 10  # seconds the person's shell may take to say what an alias stands for

    @staticmethod
    def teach(name, definition=None):
        """--learn-command NAME [DEFINITION]: NAME from now on, an alias of DEFINITION (checked as
        POSIX shell) or, without one, as the person's shell or PATH has it; "none" forgets it."""
        name, known = name.strip(), TaughtCommands.all()
        if definition is not None and definition.strip().lower() == "none":
            if known.pop(name, None) is None:
                raise SystemExit(f"{name} was not taught (--memory lists the commands taught)")
            TaughtCommands.save(known)
            print(f"Forgot the command {name} (in {COMMANDS_FILE})")
            return
        if definition is None:
            entry = TaughtCommands.look_up(name)
        elif not definition.strip():
            raise SystemExit(f"--learn-command {name}: the definition is empty: give what {name} stands for, or leave "
                             f"it out to read it from your shell")
        else:
            entry = {"kind": "alias", "definition": definition.strip(), "from": "given"}
        if entry["kind"] == "alias":
            if not TaughtCommands.NAME_RE.fullmatch(name):
                raise SystemExit(f"{name!r} cannot be an alias Tinker defines: letters, digits and _ only, not starting "
                                 f"with a digit")
            entry["definition"] = TaughtCommands.SOURCE_RE.sub(r"\1. ", entry["definition"])
            problem = TaughtCommands.syntax_problem(name, entry["definition"])
            if problem:
                raise SystemExit(f"{name} stands for `{entry['definition']}`, which is not valid POSIX shell "
                                 f"({problem}); give a definition that is")
        known[name] = dict(entry, taught=now_text())
        TaughtCommands.save(known)
        print(f"Learned the command {name}: {TaughtCommands.describe(known[name])} (in {COMMANDS_FILE})")
        print("The toolchain uses it where it fits (each command runs in a new shell: e.g. "
              f"`{name} && ...`); SPEC.md and README show it spelled out.")

    @staticmethod
    def all():
        """{name: entry} of the commands taught (a broken commands.json is an error, never overwritten)."""
        data = JsonFile.read(COMMANDS_FILE, {}, keeps_work=True) or {}
        commands = data.get("commands", {})
        if not isinstance(commands, dict) or not all(isinstance(e, dict) and e.get("kind") in ("alias", "program")
                                                     for e in commands.values()):
            raise SystemExit(f"{COMMANDS_FILE}: \"commands\" must map each name to {{\"kind\": \"alias\", \"definition\": "
                             f"...}} or {{\"kind\": \"program\", ...}}: fix it, or delete it to teach them again")
        return commands

    @staticmethod
    def save(known):
        JsonFile.write(COMMANDS_FILE, {"commands": known})

    @staticmethod
    def look_up(name):
        """What NAME is for the person: an alias in their shell (asked once, now), else a program
        on PATH; neither is an error that says to give the definition."""
        shell = os.environ.get("SHELL", "")
        if shell:
            try:
                proc = subprocess.run([shell, "-i", "-c", f"alias {name}"], capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, timeout=TaughtCommands.LOOKUP_TIMEOUT)
                for line in proc.stdout.splitlines():
                    match = re.fullmatch(rf"(?:alias\s+)?{re.escape(name)}=(.+)", line.strip())
                    if match:
                        return {"kind": "alias", "definition": TaughtCommands.unquote(match.group(1)),
                                "from": f"your {Path(shell).name} alias"}
            except (OSError, subprocess.TimeoutExpired):
                pass
        path = shutil.which(name)
        if path:
            return {"kind": "program", "path": path, "from": "PATH"}
        raise SystemExit(f"{name} is neither an alias in your shell ({shell or 'SHELL is not set'}) nor a program on "
                         f"PATH: give what it stands for, e.g. --learn-command {name} \". .venv/bin/activate\"")

    @staticmethod
    def syntax_problem(name, definition):
        """Why NAME() { DEFINITION } is not valid POSIX shell ("" when it is), checked by sh -n."""
        try:
            proc = subprocess.run(["sh", "-n", "-c", TaughtCommands.function(name, definition)], capture_output=True,
                                  text=True, stdin=subprocess.DEVNULL, timeout=TaughtCommands.LOOKUP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"sh could not check it: {exc}"
        return "" if proc.returncode == 0 else first_line(proc.stderr, 160) or f"sh -n exited with {proc.returncode}"

    @staticmethod
    def describe(entry):
        if entry.get("kind") == "program":
            return f"the program {entry.get('path', '?')} ({entry.get('from', 'PATH')})"
        return f"stands for `{entry.get('definition', '')}` ({entry.get('from', 'given')})"

    @staticmethod
    def unquote(value):
        """An alias's value as the shell prints it ('source .venv/bin/activate'), without its quotes."""
        try:
            words = shlex.split(value)
        except ValueError:
            return value
        return words[0] if len(words) == 1 and value[:1] in "'\"" else value

    @staticmethod
    def function(name, definition):
        return f"{name}() {{ {definition}\n}}\n"

    @staticmethod
    def spelled_out(command):
        """The command with each alias it uses replaced by what it stands for (for SPEC.md and the
        README: they work for anyone)."""
        for name, entry in TaughtCommands.all().items():
            if entry["kind"] == "alias":
                command = re.sub(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])", lambda m: entry["definition"], command)
        return command

    @staticmethod
    def prelude(command):
        """The definitions, as shell functions, of the aliases this command uses ("" for none)."""
        return "".join(TaughtCommands.function(name, entry["definition"])
                       for name, entry in TaughtCommands.all().items()
                       if entry["kind"] == "alias" and TaughtCommands.uses(command, name))

    @staticmethod
    def uses(command, name):
        return re.search(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])", command) is not None

    @staticmethod
    def prompt_part():
        """The commands taught, for the toolchain prompts (an optional part)."""
        lines = [f"- {name}: " + ("a program on this machine" if entry["kind"] == "program"
                                  else f"stands for `{entry['definition']}`")
                 for name, entry in TaughtCommands.all().items()]
        return Part("The person's own commands (use them where they fit; each command runs in a new shell)",
                    "\n".join(lines), atomic=True)


class Outcome:
    """How a session ended, for the summary and the notification."""

    def __init__(self):
        self.status, self.exit_code, self.interrupted = "stopped", 1, False

    def set(self, status):
        """"done" or "planned" (exit code 0), or "blocked" (1)."""
        self.status, self.exit_code = status, (1 if status == "blocked" else 0)


class Models:
    """The models Ollama has, and what calibration measured for each (models/<model>.json): the
    settings a run uses, and what a run learned about its model."""
    MEASURED = ("max_prompt_tokens", "num_ctx", "max_reply_tokens", "fix_max_tokens")  # what a run needs of them

    @staticmethod
    def installed(name):
        """The name, when Ollama has a model of exactly that name (as --models lists it)."""
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(f"Ollama does not answer at {Config.ollama_url()} ({CONFIG_FILE.name}: ollama_url).")
        names = [m.get("name", "") for m in models]
        if name not in names:
            close = [n for n in names if name.lower() in n.lower()]
            hint = f" Did you mean {' or '.join(close)}?" if close else ""
            raise SystemExit(f"Ollama has no model named '{name}'.{hint} Installed: {', '.join(names) or 'none'}")
        return name

    @staticmethod
    def selected():
        """(name, settings) of the model in use: the one --use-model selected, measured."""
        name = Config.get("model")
        if not name:
            raise SystemExit("No model in use. Measure one Ollama has with --calibrate NAME, then select it with "
                             "--use-model NAME (--models lists them).")
        settings = Models.settings(name)
        if settings is None:
            raise SystemExit(f"{name}, the model in use ({CONFIG_FILE.name}: model), has not been measured: "
                             f"--calibrate {name}")
        broken = [key for key in Models.MEASURED if isinstance(settings.get(key), bool)
                  or not isinstance(settings.get(key), int) or settings[key] < 1]
        if broken:
            raise SystemExit(f"{Models.file(name)} has no usable {', '.join(broken)}: measure the model again with "
                             f"--calibrate {name}")
        return name, settings

    @staticmethod
    def settings(name):
        """A model's measured settings, or None when it was never calibrated."""
        return JsonFile.read(Models.file(name))

    @staticmethod
    def file(name):
        return MODELS_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", name) + ".json")

    @staticmethod
    def remember(model):
        """What this run learned about its model, for the next run: the chars per token its prompts
        showed (prompts are packed from there). Limits lowered after crashes are not kept: the
        measured ones stay, and a note says to measure again if crashes keep happening."""
        if model.crashes:
            lowered = [f"{name} {now} (measured {was})" for name, now, was in
                       (("prompt budget", model.max_tokens, model.calibrated[0]),
                        ("longest reply", model.max_reply, model.calibrated[1])) if now < was]
            print(f"{model.name} crashed {model.crashes} time(s) this run" + (f"; lowered for this run only: "
                  f"{', '.join(lowered)}" if lowered else "") + f". If crashes keep happening, measure it again: "
                  f"tinker.py --calibrate {model.name}")
        learned, settings = model.learned_chars_per_token(), Models.settings(model.name)
        if learned and settings and settings.get("chars_per_token") != learned:
            try:
                JsonFile.write(Models.file(model.name), dict(settings, chars_per_token=learned))
            except OSError:
                pass

    @staticmethod
    def for_run(args):
        """(name, settings) of this run's model: the one in use; --max-prompt-tokens and --think
        override its settings for this run only."""
        name, settings = Models.selected()
        settings = dict(settings)
        if args.max_prompt_tokens:
            settings["max_prompt_tokens"] = args.max_prompt_tokens
        if args.think is not None:
            settings["think"] = {"false": False, "true": True, "default": None}.get(args.think, args.think)
        return name, settings

    @staticmethod
    def context_for(budget, max_reply, context_length):
        """num_ctx for a prompt budget and a longest reply: room for both (a runner that keeps to it
        then never cuts a reply short), within what the model supports."""
        needed = -(-(budget + max_reply + 512) // 1024) * 1024
        return min(needed, context_length) if context_length else needed

    @staticmethod
    def reply_limits(max_reply):
        """The reply settings for a longest reply: it, and the budget of a fix under it."""
        return {"max_reply_tokens": max_reply,
                "fix_max_tokens": min(DEFAULT_FIX_MAX, max_reply)}


class Embeddings:
    """Embeddings from the embedding model selected with --embed NAME (none: the word search
    works alone), kept in embeddings.json next to the script by text, so each text is embedded once
    (all of them again when the model changes). Local: nothing goes online."""
    BATCH = 32  # texts per request

    def __init__(self, name):
        self.name, self.failed = name, ""
        cache = JsonFile.read(EMBEDDINGS_FILE, None)
        same = isinstance(cache, dict) and cache.get("model") == name and isinstance(cache.get("vectors"), dict)
        self.cache = cache if same else {"model": name, "vectors": {}}

    @staticmethod
    def required(option):
        """The embedding model, for an option that learns: one must be selected (--embed NAME)."""
        if not Config.get("embed_model"):
            raise SystemExit(f"{option} needs an embedding model, which indexes what it learns: select one Ollama "
                             f"has with --embed NAME (--models lists them)")
        Embeddings.check()
        return Embeddings(Config.get("embed_model"))

    @staticmethod
    def check():
        """The embedding model selected (--embed NAME), if any, is one Ollama has (checked before
        anything starts)."""
        name = Config.get("embed_model")
        if not isinstance(name, str):
            raise SystemExit(f"embed_model in {CONFIG_FILE} is not a model name ({name!r}): select one with --embed NAME")
        if name:
            try:
                Models.installed(name)
            except SystemExit as exc:
                raise SystemExit(f"The embedding model (--embed): {exc}")

    @staticmethod
    def configured():
        """The embedding model in use, or None (embed_model is "")."""
        name = Config.get("embed_model")
        return Embeddings(name) if name else None

    def vectors(self, texts, keep=True, prune=False):
        """One embedding per text (from the cache, the missing ones asked for in batches), or None
        when the model does not give them (failed says why). keep: cache them; prune: the cache then
        keeps only these texts' embeddings."""
        stored = self.cache["vectors"]
        keys = [self.key(text) for text in texts]
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in stored))
        fresh = {}
        for start in range(0, len(missing), self.BATCH):
            batch = missing[start:start + self.BATCH]
            data = Ollama.get_json("/api/embed", {"model": self.name, "input": batch, "keep_alive": 0}, timeout=300,
                                   errors=True)
            found = data.get("embeddings") if isinstance(data, dict) else None
            if not (isinstance(found, list) and len(found) == len(batch)
                    and all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in found)):
                self.failed = f"{self.name} gave no embeddings" + (f" ({data['error']})" if isinstance(data, dict)
                                                                   and data.get("error") else "")
                return None
            fresh.update({self.key(text): [round(x, 5) for x in vector] for text, vector in zip(batch, found)})
        result = [stored.get(key) or fresh[key] for key in keys]
        if keep and (fresh or prune):
            stored.update(fresh)
            if prune:
                self.cache["vectors"] = {key: stored[key] for key in keys}
            try:
                JsonFile.write(EMBEDDINGS_FILE, self.cache)
            except OSError as exc:
                print(f"  note: could not keep the embeddings ({exc})")
        return result

    @staticmethod
    def key(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / size if size else 0.0


class Internet:
    """The Internet plugin: web search and web fetch through the backend config.json's "web" names
    (one of backends()). Used only when asked: by --learn, and by the recall phase of a run started
    with --web; nothing else goes online. Asked for and not usable (no backend, no key), it says
    why before anything starts. Its calls never raise: what fails gives None and why() says why;
    once the network is gone it is not tried again by the same command (a lost connection costs
    one timeout), and the run carries on without it."""

    def __init__(self):
        name = Config.get("web")
        self.backend, self.errors = None, {}
        if not name:
            self.gone = f"no web backend ({CONFIG_FILE.name}: web)"
            return
        backend = self.backends()[name](float(Config.get("web_timeout")))
        self.gone = backend.unready()
        self.backend = None if self.gone else backend

    @staticmethod
    def required(option):
        """The plugin, for the option that asks for it: its settings checked and its backend ready
        (else an error that says why, before anything starts)."""
        Internet.check()
        internet = Internet()
        if not internet.available():
            raise SystemExit(f"{option} needs the Internet plugin: {internet.why()}")
        return internet

    @staticmethod
    def state():
        """"ready", or why the plugin would not work (its settings, its backend)."""
        try:
            Internet.check()
        except SystemExit as exc:
            return f"not usable: {exc}"
        internet = Internet()
        return "ready" if internet.available() else internet.why()

    @staticmethod
    def backends():
        """The web backends, by the name config.json's "web" gives one: add a new one here."""
        return {"ollama": OllamaWeb}

    @staticmethod
    def check():
        """web, web_timeout and learn_missing in config.json are usable."""
        name, timeout, missing = Config.get("web"), Config.get("web_timeout"), Config.get("learn_missing")
        if name not in ("", *Internet.backends()):
            raise SystemExit(f"web in {CONFIG_FILE} must be one of {', '.join(map(repr, Internet.backends()))} "
                             f'or "" (no web search); it is {name!r}')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise SystemExit(f"web_timeout in {CONFIG_FILE} must be a number of seconds above 0; it is {timeout!r}")
        if isinstance(missing, bool) or not isinstance(missing, int) or missing < 1:
            raise SystemExit(f"learn_missing in {CONFIG_FILE} must be a whole number, 1 or more (the topics a run "
                             f"with --web may learn); it is {missing!r}")

    def available(self):
        return not self.gone

    def why(self, key=None):
        return self.gone or self.errors.get(key) or "no answer"

    def search(self, query, count):
        """[{title, url, content}] the web search finds (web addresses only), or None."""
        found = self.call(query, lambda: self.backend.search(query, count))
        if found is None:
            return None
        if not isinstance(found, list):
            self.errors[query] = f"the {Config.get('web')} backend's search gave no list"
            return None
        return [{"title": str(r.get("title") or ""), "url": str(r["url"]), "content": str(r.get("content") or "")}
                for r in found if isinstance(r, dict) and str(r.get("url", "")).startswith(("http://", "https://"))]

    def call(self, key, action):
        if self.gone:
            return None
        try:
            return action()
        except WebError as exc:
            self.errors[key] = str(exc)
            if exc.network:
                self.gone = str(exc)
        except Exception as exc:  # a backend's bug never stops Tinker
            self.errors[key] = f"the {Config.get('web')} backend failed: {type(exc).__name__}: {exc}"
        return None

    def fetch(self, url):
        """{title, url, content} of a web page, its main text, or None."""
        page = self.call(url, lambda: self.backend.fetch(url))
        if page is None:
            return None
        if not isinstance(page, dict):
            self.errors[url] = f"the {Config.get('web')} backend's fetch gave no page"
            return None
        return {"title": str(page.get("title") or ""), "url": url, "content": str(page.get("content") or "")}


class RunChecks:
    """What a run needs that must be there already, checked before anything starts (no folder
    emptied, no model loaded): its folder, its saved state, and that the options fit it."""

    @staticmethod
    def before_start(args):
        """What a run needs that must be there already, checked before anything starts (no folder
        emptied, no model loaded or measured): a new run's folder (with no run in it, unless --new
        empties it) and spec file; an existing run, its folder and its saved state; for --add and
        --fix a project whose cycles are all done; for --plan-only a cycle that is still planning.
        Returns the project's folder."""
        if args.resume is None:
            workdir = Runs.workdir("", args.workdir)
            if args.spec:
                Runs.read_spec_file(args.spec)
            if args.new is None:
                RunChecks.check_no_run(workdir)
            return workdir
        workdir = Runs.workdir(args.resume, args.workdir)
        state, run = workdir / STATE_DIR, Runs.read_record(workdir)
        if args.add is not None and args.spec_file:
            Runs.read_spec_file(args.spec_file)
        spec = JsonFile.read(state / "spec.json", {"steps": [], "counter": 0}, keeps_work=True)
        project = Project(workdir, run, spec)
        RunChecks.check_phases(project)
        if args.add is not None:
            RunChecks.check_open_to_add(project)
        elif args.plan_only:
            RunChecks.check_planning(project)
        if args.web and args.add is None:
            RunChecks.check_recall_ahead(project)
        return workdir

    @staticmethod
    def check_no_run(workdir):
        """A new run needs a folder without one (--resume, --add or --fix work on the one there)."""
        state = workdir / STATE_DIR
        if (state / "spec.json").is_file() or (state / "run.json").is_file():
            existing = JsonFile.read(state / "run.json", {}).get("id", "?")
            raise SystemExit(f"{workdir} already holds run {existing}. Continue it with --resume, add to it "
                             f'with --add "...", or start the new project in another folder (--workdir).')

    @staticmethod
    def check_phases(project):
        """Each saved cycle is at one of its phases (PHASES, or FIX_PHASES for a bug fix)."""
        for cycle in project.cycles:
            phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
            if cycle.get("phase") not in phases:
                raise SystemExit(f"The saved phase '{cycle.get('phase')}' of cycle {cycle.get('id')} is not one of its "
                                 f"phases ({', '.join(phases)}): set its \"phase\" in "
                                 f"{project.state / 'spec.json'} to one of them")

    @staticmethod
    def check_open_to_add(project):
        """--add and --fix start a new cycle: the project's own must exist, and none may be unfinished."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is not None:
            what = "project" if cycle["id"] == "base" else cycle.get("kind", "feature")
            raise SystemExit(f"Run {run_id} has an unfinished {what} ({first_line(cycle['request'], 80)}). "
                             f"Finish it first with: tinker.py --resume {run_id}")
        if not project.cycles:
            raise SystemExit(f"Run {run_id} has no project spec yet; continue it with --resume first.")

    @staticmethod
    def check_planning(project):
        """--resume --plan-only: the open cycle is still planning (spec, design, review)."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is None and project.cycles:
            raise SystemExit(f"--plan-only: every cycle of run {run_id} is done, so there is nothing to plan. "
                             f'Add a feature with --add {run_id} "..."')
        if cycle is not None and cycle.get("kind") == "fix":
            raise SystemExit(f"--plan-only stops after planning, and a bug fix plans nothing (run {run_id}): continue "
                             f"it without --plan-only: tinker.py --resume {run_id}")
        if cycle is not None and cycle["phase"] not in PLANNING_PHASES:
            raise SystemExit(f"--plan-only stops after planning, and run {run_id} is past it (next phase: "
                             f"{cycle['phase']}). Continue it without --plan-only: tinker.py --resume {run_id}")

    @staticmethod
    def check_recall_ahead(project):
        """--resume --web: the web is used only by a cycle's recall phase, so the open cycle must
        still be at it."""
        run_id, cycle = project.run.get("id", "?"), project.open_cycle()
        if cycle is None and not project.cycles:
            return  # no cycle yet: the project's own starts at its recall phase
        if cycle is None or cycle["phase"] != "recall":
            where = f"next phase: {cycle['phase']}" if cycle else "every cycle is done"
            raise SystemExit(f"--web is used only by a cycle's recall phase, and run {run_id} is past it ({where}). "
                             f"Continue it without --web: tinker.py --resume {run_id}")


class Runs:
    """The runs: each is registered in runs/ by its ID (and kept in its project's .tinker/run.json),
    so --resume ID, --add ID, --fix ID and --status ID work from any folder; a run opened (a new
    one, or a saved one resumed or changed); a folder emptied for --new."""

    @staticmethod
    def clear_folder(workdir, spec):
        """Delete what is in workdir, after showing it and asking (in a terminal only). The entry that
        holds the spec file stays. Refuses the home folder, the filesystem root and the folder
        Tinker itself lives in."""
        if workdir in (Path.home().resolve(), Path(workdir.anchor)) or HOME.resolve().is_relative_to(workdir):
            raise SystemExit(f"--new will not empty {workdir}: run it in the project's own folder.")
        keep = next((item for item in workdir.iterdir() if spec and spec.is_relative_to(item.resolve())), None)
        items = sorted(item for item in workdir.iterdir() if item != keep)
        if not items:
            return
        print(f"--new deletes everything in {workdir} ({len(items)} items):")
        for item in items[:20]:
            print(f"  {item.name}{'/' if item.is_dir() and not item.is_symlink() else ''}")
        if len(items) > 20:
            print(f"  ... and {len(items) - 20} more")
        if keep:
            print(f"Kept: {keep.name} (it holds the spec file)")
        if not sys.stdin.isatty():
            raise SystemExit("Nothing deleted: run --new in a terminal to confirm.")
        if input("Delete them and start a fresh run? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("Nothing deleted.")
        for item in items:
            shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink()
        Runs.forget_runs_in(workdir)
        print(f"Emptied {workdir}.")

    @staticmethod
    def entries():
        entries = []
        for path in sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.is_dir() else []:
            try:
                entries.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return entries

    @staticmethod
    def workdir(run_id, workdir_arg):
        """The project folder: the run's (by ID), else the one given (--workdir, else this one)."""
        if run_id:
            workdir = Path(Runs.find(run_id)["workdir"])
            if not workdir.is_dir():
                raise SystemExit(f"Project folder for run {run_id} no longer exists: {workdir}")
            return workdir
        workdir = Path(workdir_arg).expanduser().resolve()
        if not workdir.is_dir():
            raise SystemExit(f"The project folder {workdir} does not exist: create it first, or give another --workdir.")
        return workdir

    @staticmethod
    def read_spec_file(path):
        spec_path = Path(path).expanduser()
        if not spec_path.is_file():
            raise SystemExit(f"Spec file not found: {path}")
        return spec_path.read_text(encoding="utf-8"), spec_path.name

    @staticmethod
    def read_record(workdir):
        """The run saved in a project folder (its .tinker/run.json)."""
        run = JsonFile.read(workdir / STATE_DIR / "run.json", None, keeps_work=True)
        if run is None:
            raise SystemExit(f'No saved run in {workdir}. Start one with: tinker.py "task" --workdir {workdir}')
        return run

    @staticmethod
    def forget_runs_in(workdir):
        """Drop the registered runs (runs/) of a folder that was emptied: their IDs lead nowhere now."""
        for path in sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.is_dir() else []:
            if JsonFile.read(path, {}).get("workdir") == str(workdir):
                path.unlink()

    @staticmethod
    def open(args):
        """Returns (run, workdir, spec_text, mode): mode new, resume, or change (--add, --fix)."""
        if args.resume is None:
            return Runs.open_new(args)
        return Runs.open_existing(args)

    @staticmethod
    def save(run, workdir):
        run["updated"] = now_text()
        (workdir / STATE_DIR / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        try:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            (RUNS_DIR / f"{run['id']}.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"  note: could not register run in {RUNS_DIR}: {exc}")

    @staticmethod
    def find(run_id):
        matches = [e for e in Runs.entries() if e.get("id", "").startswith(run_id.lower())]
        if not matches:
            raise SystemExit(f"No run with ID '{run_id}'. List runs with: tinker.py --runs")
        if len(matches) > 1:
            raise SystemExit(f"ID '{run_id}' matches several runs: {', '.join(m['id'] for m in matches)}")
        return matches[0]

    @staticmethod
    def open_new(args):
        workdir = Path(args.workdir).expanduser().resolve()
        RunChecks.check_no_run(workdir)
        state = workdir / STATE_DIR
        state.mkdir(exist_ok=True)
        spec_text, spec_name = Runs.read_spec_file(args.spec) if args.spec else ("", None)
        (state / "spec.md").write_text(spec_text, encoding="utf-8")  # resume needs no spec file
        run = Runs.new_record(args.task, workdir, spec_name)
        Runs.save(run, workdir)
        return run, workdir, spec_text, "new"

    @staticmethod
    def open_existing(args):
        workdir = Runs.workdir(args.resume, args.workdir)
        state, run = workdir / STATE_DIR, Runs.read_record(workdir)
        adding = args.add is not None
        if adding:
            spec_text = Runs.read_spec_file(args.spec_file)[0] if args.spec_file else ""
        else:
            spec_text = (state / "spec.md").read_text(encoding="utf-8") if (state / "spec.md").is_file() else ""
        run["status"] = "running"
        Runs.save(run, workdir)
        return run, workdir, spec_text, ("change" if adding else "resume")

    @staticmethod
    def new_record(task, workdir, spec_name):
        return {"id": secrets.token_hex(3), "task": task, "workdir": str(workdir), "spec": spec_name,
                "created": now_text(), "status": "running"}


class Console:
    """What Tinker prints: each step with when it started and how long it took, a live progress
    line (on the terminal only), and a copy of everything in current.log."""

    @staticmethod
    def mirror(folder):
        """Mirror everything printed into folder/current.log (replaced on every run)."""
        try:
            file = open(Path(folder) / LOG_NAME, "w", encoding="utf-8", buffering=1)
        except OSError as exc:
            print(f"  note: could not write {LOG_NAME}: {exc}")
            return
        sys.stdout = Tee(sys.__stdout__, file)
        sys.stderr = Tee(sys.__stderr__, file)

    @staticmethod
    def step(message, started_at):
        """[start clock time, duration readable and in seconds] message"""
        elapsed = time.perf_counter() - started_at
        began = datetime.datetime.now() - datetime.timedelta(seconds=elapsed)
        print(f"[{began:%H:%M:%S} {human_duration(elapsed):>7} ({elapsed:.2f}s)] {message}")

    @staticmethod
    def progress(text):
        """Redraw one status line in place, on the terminal only (not in current.log). Cut to the
        terminal width: a wrapped line cannot be redrawn with \\r."""
        width = shutil.get_terminal_size((80, 24)).columns
        sys.__stdout__.write("\r" + text[:max(width - 1, 10)] + "\033[K")
        sys.__stdout__.flush()

    @staticmethod
    def clear_progress():
        sys.__stdout__.write("\r\033[K")
        sys.__stdout__.flush()


class EventLog:
    """A project's log.jsonl: every model call and session event, one JSON line each."""

    def __init__(self, path):
        self.path = path

    def add(self, event):
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **event}, ensure_ascii=False) + "\n")


class JsonFile:
    """Reads and writes the plain JSON files Tinker keeps everything in (they may be edited by
    hand at any time)."""

    @staticmethod
    def write(path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)  # never a half-written file

    @staticmethod
    def read(path, default=None, keeps_work=False):
        """A JSON object from a file, or default when the file is missing. A file that is broken
        (or holds something other than an object): with keeps_work (settings, a run, what was
        learned: replacing it would lose them) an error that says to fix or delete it; otherwise
        (a cache, an index) it is reported and default is used."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            problem = str(exc)
        else:
            if isinstance(data, dict):
                return data
            problem = "it is not a JSON object"
        if keeps_work:
            raise SystemExit(f"{path} cannot be read ({problem}): fix it, or delete it to start that part fresh")
        print(f"  note: {path} could not be read ({problem}); ignoring it")
        return default


class Ollama:
    """Ollama's HTTP API (ollama_url in config.json): chat calls streamed with live progress, raw
    prompts for calibration, and what it says about its models."""
    RUNNER_CRASHES = ("runner failed", "runner process has terminated", "runner process no longer running",
                      "command buffer execution failed", "panic:", "signal: abort", "signal: killed",
                      "segmentation fault")

    @staticmethod
    def installed_models():
        """[{name, size, details}] of the models Ollama has (None when Ollama does not answer)."""
        data = Ollama.get_json("/api/tags")
        return None if data is None else sorted(data.get("models", []), key=lambda m: m.get("name", ""))

    @staticmethod
    def get_json(path, payload=None, timeout=10, errors=False):
        """GET (or POST payload to) an Ollama endpoint; its JSON, or None when Ollama does not answer
        (errors: an error's own JSON, {"error": ...}, instead of None)."""
        try:
            with urllib.request.urlopen(Ollama.request(path, payload), timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if not errors:
                return None
            try:
                return json.loads(exc.read().decode("utf-8", errors="replace"))
            except (ValueError, OSError):
                return {"error": f"HTTP {exc.code}"}
        except (urllib.error.URLError, OSError, ValueError):
            return None

    @staticmethod
    def unload(name):
        try:
            Ollama.chat(name, [], 0, None)
        except OllamaError:
            pass

    @staticmethod
    def model_facts(name):
        """What Ollama says about a model: its longest context and what it can do (thinking...)."""
        data = Ollama.get_json("/api/show", {"model": name}, timeout=30) or {}
        info = data.get("model_info") or {}
        context = next((v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)), None)
        return {"context_length": context, "capabilities": data.get("capabilities") or [],
                "format": (data.get("details") or {}).get("format") or ""}

    @staticmethod
    def request(path, payload=None):
        """A request to one of Ollama's endpoints: a GET, or a POST of payload as JSON."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        return urllib.request.Request(Config.ollama_url() + path, data=data, headers={"Content-Type": "application/json"},
                                      method="GET" if payload is None else "POST")

    @staticmethod
    def read_raw(name, prompt, options, timeout=REQUEST_TIMEOUT):
        """Have the model read a prompt sent as is (no chat template: calibration sends an exact
        number of tokens), on a fresh load and without streaming. Ollama's stats."""
        payload = {"model": name, "prompt": prompt, "raw": True, "stream": False, "keep_alive": 0, "options": options}
        try:
            with urllib.request.urlopen(Ollama.request("/api/generate", payload), timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc)
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}")
        except (TimeoutError, ConnectionError) as exc:
            raise OllamaError(f"Ollama stopped responding ({exc})", stalled=True)
        if data.get("error"):
            raise OllamaError(f"Ollama error: {data['error']}", oom=Ollama.is_oom(data["error"]),
                              crashed=Ollama.is_crash(data["error"]))
        return data

    @staticmethod
    def chat(name, messages, keep_alive, options, think=None, label=None, done_when=None,
             timeout=REQUEST_TIMEOUT):
        """Stream one chat request (live progress when labelled). done_when(content) says when the
        reply is complete: the stream is closed there, which stops Ollama generating. Returns the
        final stats chunk with message.content set to the reply."""
        payload = {"model": name, "messages": messages, "stream": True, "keep_alive": keep_alive}
        if think is not None:
            payload["think"] = think
        if options:
            payload["options"] = options
        stream = Stream(label, done_when)
        try:
            with urllib.request.urlopen(Ollama.request("/api/chat", payload), timeout=timeout) as response:
                stream.read(response)
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc)
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}",
                              stalled=isinstance(exc.reason, TimeoutError))
        except (TimeoutError, ConnectionError) as exc:
            raise OllamaError(f"Ollama stopped responding ({exc or 'no data for ' + str(timeout) + 's'})",
                              stalled=True)
        finally:
            stream.close()
        stream.final["message"] = {"role": "assistant", "content": stream.reply}
        return stream.final

    @staticmethod
    def write_raw(name, prompt, options, label, timeout=REQUEST_TIMEOUT):
        """Have the model continue a prompt sent as is, on a fresh load, streamed so that the tokens
        written before a failure are known: (tokens written, Ollama's final stats). A failure raises
        OllamaError with streamed = the tokens written."""
        payload = {"model": name, "prompt": prompt, "raw": True, "stream": True, "keep_alive": 0, "options": options}
        written, started, shown = 0, time.perf_counter(), 0.0
        try:
            with urllib.request.urlopen(Ollama.request("/api/generate", payload), timeout=timeout) as response:
                for raw in response:
                    chunk = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                    if chunk.get("error"):
                        error = str(chunk["error"])
                        raise OllamaError(f"Ollama error: {error}", oom=Ollama.is_oom(error), crashed=Ollama.is_crash(error),
                                          streamed=written)
                    written += bool(chunk.get("response"))
                    if chunk.get("done"):
                        return written, chunk
                    if time.perf_counter() - shown >= PROGRESS_EVERY:
                        shown = time.perf_counter()
                        Console.progress(f"  ... {label}: {written} tokens written, {shown - started:.0f}s")
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc, streamed=written)
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}", streamed=written)
        except (TimeoutError, ConnectionError) as exc:  # the runner died mid-reply
            raise OllamaError(f"Ollama stopped responding ({exc})", crashed=True, streamed=written)
        finally:
            Console.clear_progress()
        return written, {}

    @staticmethod
    def http_error(exc, streamed=0):
        """The OllamaError for an HTTP error, with what Ollama said (out of memory? a crash?)."""
        body = exc.read().decode("utf-8", errors="replace").strip()
        return OllamaError(f"Ollama returned HTTP {exc.code}: {body or exc.reason}", oom=Ollama.is_oom(body),
                           crashed=Ollama.is_crash(body), streamed=streamed)

    @staticmethod
    def is_oom(text):
        lowered = (text or "").lower()
        return "insufficient memory" in lowered or "out of memory" in lowered

    @staticmethod
    def is_crash(text):
        """The model's runner died (not a refusal of the request): retrying after a pause can work."""
        lowered = (text or "").lower()
        return any(sign in lowered for sign in Ollama.RUNNER_CRASHES)

    @staticmethod
    def loaded_models():
        """[(name, size_bytes)] of the models Ollama has loaded (None if unknown)."""
        data = Ollama.get_json("/api/ps")
        if data is None:
            return None
        return [(m.get("name") or m.get("model") or "", m.get("size", 0)) for m in data.get("models", [])]


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
        self.unreproduced = ""  # --fix: why the bug could not be shown by a test
        self.review_problem = ""  # why the final review found the project not done
        self.all_tests_run = None  # (files key, (code, output)) of the last run of all tests
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
        fails, that is a note and the run goes on."""
        command = self.toolchain.format
        if not command:
            return
        ok, failed = self.setup()
        code, output = self.ws.run(command, timeout=SETUP_TIMEOUT, quiet=True) if ok else (None, failed)
        print(f"  formatted the code: $ {command}" if code == 0 else
              f"  note: the formatter did not run ({first_line(output.strip(), 160) or f'exit code {code}'}); "
              f"the code stays as written")

    def save(self):
        self.project.save()

    @property
    def toolchain(self):
        return self.project.toolchain

    def setup(self, force=False):
        """Run the setup commands when they or the config files changed; prepare .env."""
        self.ws.prepare_env()
        configs = sorted(p for p in self.project.paths("config") if p not in (ENV_FILE, ENV_EXAMPLE))
        key = fingerprint(json.dumps(self.toolchain.setup) + "".join(self.ws.read(p) for p in configs))
        if not force and key == self.project.spec.get("setup_key"):
            return True, ""
        ok, output = self.toolchain.run_setup(self.ws)
        if ok:
            self.project.spec["setup_key"] = key
            self.save()
        return ok, output

    def ask(self, system, parts, label, **kw):
        """A model call whose reply is files (FILE or LINES blocks), ended as soon as it is complete."""
        kw.setdefault("done_when", Reply.files_complete)
        return Reply(self.model.ask(system, parts, label, **kw))

    def ask_until_usable(self, system, parts, label, read, attempts=3):
        """read() of the first usable JSON reply (Model.ask_until_usable); when none is, or the
        prompt cannot fit even shortened, the run stops here with why."""
        try:
            result = self.model.ask_until_usable(system, parts, label, read, attempts)
        except PromptTooLarge as exc:
            raise SystemExit(f"{label}: even shortened, the prompt does not fit {self.model.name}'s input budget "
                             f"of {self.model.max_tokens} tokens ({exc}). Measure the model again (--calibrate) "
                             f"or use one that reads longer prompts (--models).")
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
        toolchain the person's own commands (--learn-command)."""
        lessons = self.knowledge.lessons(phases)
        facts = [f"- [{item['name']}] {item['text']}" for item in self.project.spec.get("learned", [])
                 if Library.shown_in(item, phases)]
        parts = [Part("Lessons from earlier projects (follow them)", "\n".join(f"- {lesson}" for lesson in lessons),
                      atomic=True),
                 Part("Learned from the web (current; newer than what you were trained on)", "\n".join(facts))]
        return parts + ([TaughtCommands.prompt_part()] if "toolchain" in phases else [])

    def note(self, kind, text):
        """A problem this run solved, kept for the lessons learned when the cycle ends."""
        events = self.project.spec.setdefault("events", [])
        events.append({"kind": kind, "text": first_line(text, 300)})
        del events[:-30]

    def files_on_disk(self, *kinds):
        return [p for p in self.project.paths(*kinds) if self.ws.exists(p)]


class Calibrator:
    """Measures a model once, so every later run uses it at its limits without trial and error:
    the context it gets, the think setting it accepts, the largest prompt it reads without
    crashing (some runners crash reading a prompt above some size, whatever the context), and
    how fast it reads and writes. Saved in models/<model>.json, which may be edited by hand.
    A probe is a prompt of an exact number of tokens: random digits sent as is (no chat
    template), and a digit is one token in most tokenizers (a model that counts digits otherwise
    is measured by its first read and kept as tokens_per_digit). Every probe is made as a run
    makes its calls: on a freshly loaded model (keep_alive 0). A model kept loaded from the
    previous probe can read prompts a fresh one crashes on, and a run's calls load it fresh."""
    LONG_REPLY_PROMPT = ", ".join(str(n) for n in range(1, 201)) + ","  # a count to continue: no early end
    LONG_REPLY_PROMPT_TOKENS = 900

    # text like a real prompt (code, a docstring, a comment): a run sizes its prompts from its chars per token
    FILLER = '''def due_items(items, today):
    """The items due by today, oldest first: each a dict with "id", "title" and "due" (an ISO date)."""
    found = [item for item in items if item.get("due") and item["due"] <= today.isoformat()]
    return sorted(found, key=lambda item: (item["due"], item["id"]))  # ties keep a stable order


# A request asks for the due list; the reply is JSON: {"items": [...], "count": 3}.
'''

    def __init__(self, name):
        self.name = name
        self.probe_think = None      # think setting for chat calls: off when the model thinks
        self.tokens_per_digit = 1.0  # a digit is one token in most tokenizers; measured by each read
        self.best = {}               # Ollama's stats for the largest prompt read
        self.load_seconds = None
        self.options = {}            # num_batch, when reading a prompt in pieces avoids a crash
        self.measured_ratio = None   # chars per token of prompt-like text (one chat read)
        self.history = []            # [tokens, "read" | "crashed"] of every probe, kept in the settings
        self.last_error = None       # why the last probe failed
        self.write_tps = None        # tokens written per second, from the longest-reply probe

    def test(self, tokens):
        """--calibrate --tokens N: one prompt of N tokens, on a freshly loaded model as in a run;
        it reads or it crashes. When it reads more tokens than the prompt budget, the budget is
        raised to what it read; otherwise the budget stays as it is."""
        settings = self.measured_settings()
        self.tokens_per_digit = float(settings.get("tokens_per_digit") or 1.0)
        self.options = {"num_batch": int(settings["num_batch"])} if settings.get("num_batch") else {}
        print(f"\nTesting {self.name}: one prompt of {tokens} tokens, on a freshly loaded model")
        prompt = self.probe_prompt(tokens)
        print(f"{tokens} tokens is {len(prompt.encode('utf-8'))} bytes: {len(prompt)} random digits, "
              f"{'one token each' if self.tokens_per_digit == 1 else f'{self.tokens_per_digit:g} tokens each'}, "
              f"sent as is (no chat template)")
        print(f"exact prompt being sent:\n{prompt}\n")
        started = time.perf_counter()
        read = self.probe(tokens, int(settings["num_ctx"]), recover=False, prompt=prompt)
        Ollama.unload(self.name)
        before = int(settings["max_prompt_tokens"])
        changed = self.after_test(settings, read, tokens, before)
        if changed:
            JsonFile.write(Models.file(self.name), settings)
        budget = (f"prompt budget raised {before} -> {read} tokens" if read and read > before
                  else f"the prompt budget is unchanged: {before} tokens")
        Console.step(f"{self.name} {self.test_result(read, tokens)}; {budget} (max_prompt_tokens in "
                     f"{Models.file(self.name)})", started)
        return read is not None

    def test_reply(self, tokens):
        """--calibrate --reply-tokens N: one reply of N tokens, freshly loaded as in a run; it is
        written or it crashes (and when). Written in full and longer than the longest reply in the
        model's settings, it becomes the longest reply."""
        settings = self.measured_settings()
        self.options = {"num_batch": int(settings["num_batch"])} if settings.get("num_batch") else {}
        before, budget = int(settings["max_reply_tokens"]), int(settings["max_prompt_tokens"])
        print(f"\nTesting {self.name}: one reply of {tokens} tokens, on a freshly loaded model")
        started = time.perf_counter()
        written, error = self.long_reply(tokens, max(int(settings["num_ctx"]), Models.context_for(budget, tokens, None)))
        Ollama.unload(self.name)
        if error is None and written >= tokens > before:
            room = Models.context_for(budget, tokens, settings.get("context_length"))
            settings.update(Models.reply_limits(tokens), num_ctx=max(int(settings["num_ctx"]), room),
                            reply_raised_by_test=now_text())
            JsonFile.write(Models.file(self.name), settings)
            outcome = f"wrote {written} tokens; longest reply raised {before} -> {tokens} tokens"
        elif error is None:
            outcome = f"wrote {written} tokens; the longest reply is unchanged: {before} tokens"
        else:
            outcome = f"failed after {written} tokens; the longest reply is unchanged: {before} tokens"
        Console.step(f"{self.name} {outcome} (max_reply_tokens in {Models.file(self.name)})", started)
        return error is None

    def run(self):
        print(f"\nCalibrating {self.name} (once per model; a few minutes)")
        started = time.perf_counter()
        facts = Ollama.model_facts(self.name)
        num_ctx = min(DEFAULT_NUM_CTX, facts["context_length"] or DEFAULT_NUM_CTX)
        think = self.think_setting(facts["capabilities"], num_ctx)
        self.measure_text(num_ctx)
        largest, crash = self.prompt_limit(num_ctx)
        if crash and self.is_gguf(facts):
            largest, crash = self.read_in_pieces(num_ctx, largest, crash)
        budget = self.confirmed_budget(largest, crash, num_ctx)
        max_reply = self.reply_limit(facts["context_length"])
        settings = self.settings(facts, think, budget, largest, crash, max_reply)
        Ollama.unload(self.name)
        JsonFile.write(Models.file(self.name), settings)
        Console.step(f"calibrated {self.name}: prompts up to {budget} tokens"
                     + (f" (read {CONFIRM_READS} times in a row; crashed at {crash})" if crash
                        else f" (read {largest} without trouble)")
                     + f", replies up to {max_reply} tokens, think {think}; saved in {Models.file(self.name)}", started)
        return settings

    def measured_settings(self):
        """The model's settings: --tokens and --reply-tokens test one size of a model already
        measured, and never measure one unasked."""
        settings = Models.settings(self.name)
        if settings is None:
            raise SystemExit(f"{self.name} has not been measured yet: measure it first with --calibrate {self.name}, "
                             f"then test one size.")
        return settings

    def probe_prompt(self, tokens):
        """A prompt of exactly `tokens` tokens: random digits (nothing cached is reused), one token
        each in most tokenizers (tokens_per_digit for one that counts them otherwise)."""
        digits = max(1, round(tokens / self.tokens_per_digit))
        return "".join(secrets.choice("0123456789") for _ in range(digits))

    def probe(self, tokens, num_ctx, recover=True, prompt=None):
        """Have the model read a prompt of exactly `tokens` tokens (this `prompt`, if given), freshly
        loaded as in a run: the count Ollama reports, or None when it fails (then, if more probes
        follow, after the crashed runner had time to free its memory)."""
        prompt = prompt or self.probe_prompt(tokens)
        digits = len(prompt)
        try:
            data = Ollama.read_raw(self.name, prompt, {"num_ctx": num_ctx, "num_predict": 1, **self.options})
        except OllamaError as exc:
            if Ollama.installed_models() is None:
                raise SystemExit(f"Ollama stopped answering during calibration: {exc}")
            self.probe_failed(tokens, exc, recover)
            return None
        read = data.get("prompt_eval_count") or tokens
        self.tokens_per_digit = read / digits
        self.history.append([read, "read"])
        self.load_seconds = self.load_seconds or round(data.get("load_duration", 0) / 1e9, 1)
        if read >= self.best.get("prompt_eval_count", 0):
            self.best = data
        sent = "" if read == tokens else f" (sent {digits} digits for {tokens}: this tokenizer counts them otherwise)"
        print(f"  prompt of {read} tokens{sent}: read in {data.get('prompt_eval_duration', 0) / 1e9:.1f}s")
        return read

    def after_test(self, settings, read, tokens, before):
        """What a test changes in the model's settings: a budget below a prompt it read is raised to
        it, and a tokenizer that does not count a digit as one token is right next time. True when
        anything changed."""
        if not read:
            return False
        changed = read != tokens
        if changed:
            settings["tokens_per_digit"] = round(self.tokens_per_digit, 4)
        if read > before:
            room = Models.context_for(read, int(settings["max_reply_tokens"]), settings.get("context_length"))
            settings.update(max_prompt_tokens=read, num_ctx=max(int(settings["num_ctx"]), room), raised_by_test=now_text())
            changed = True
        return changed

    def test_result(self, read, tokens):
        if read:
            return f"read a prompt of {read} tokens"
        if self.last_error is not None and self.last_error.crashed:
            return f"crashed reading a prompt of {tokens} tokens"
        return f"did not read a prompt of {tokens} tokens ({first_line(str(self.last_error), 120)})"

    def long_reply(self, tokens, num_ctx):
        """Have the model write `tokens` tokens, freshly loaded: (tokens written, the error that
        stopped it or None). The prompt is the start of a count sent as is, so it never stops early."""
        prompt = self.LONG_REPLY_PROMPT
        started = time.perf_counter()
        try:
            written, final = Ollama.write_raw(self.name, prompt, {"num_ctx": num_ctx, "num_predict": tokens, **self.options},
                                              f"writing {tokens} tokens")
        except OllamaError as exc:
            seconds = time.perf_counter() - started
            self.write_tps = round(exc.streamed / seconds, 1) if exc.streamed else None
            Console.step(f"  wrote {exc.streamed} tokens, then failed ({first_line(str(exc), 100)})", started)
            return exc.streamed, exc
        written = final.get("eval_count") or written
        seconds = (final.get("eval_duration") or 0) / 1e9 or time.perf_counter() - started
        self.write_tps = round(written / seconds, 1) if seconds else None
        Console.step(f"  wrote {written} tokens ({self.write_tps} tokens a second)", started)
        return written, None

    def think_setting(self, capabilities, num_ctx):
        """The first think setting the model accepts: "high", then true; none if it does not think."""
        if capabilities and "thinking" not in capabilities:
            return None
        for think in (DEFAULT_THINK, True):
            try:
                data = Ollama.chat(self.name, [{"role": "user", "content": "Say OK."}], 0,
                                   {"num_ctx": num_ctx, "num_predict": 64}, think)
            except OllamaError as exc:
                if "think" in str(exc).lower():
                    continue
                raise SystemExit(f"{self.name} does not run: {exc}")
            self.load_seconds = self.load_seconds or round(data.get("load_duration", 0) / 1e9, 1)
            self.probe_think = False
            print(f"  think: {think!r} accepted")
            return think
        return None

    def measure_text(self, num_ctx):
        """Chars per token of text like a real prompt (code, a docstring, a comment), from one
        short chat read: a run starts sizing its prompts from it."""
        content = self.FILLER * 4
        try:
            data = Ollama.chat(self.name, [{"role": "user", "content": content}], 0,
                               {"num_ctx": num_ctx, "num_predict": 1}, self.probe_think)
        except OllamaError as exc:
            raise SystemExit(f"{self.name} failed a short prompt: {exc}")
        tokens = data.get("prompt_eval_count") or 0
        if tokens > 50:
            self.measured_ratio = len(content) / tokens
            print(f"  text like a prompt: {self.measured_ratio:.2f} chars per token")

    def prompt_limit(self, num_ctx, start=PROBE_START, largest=0):
        """(largest prompt read, smallest that crashed or None): doubling from start, then halving
        the gap down to one token, so the largest is the exact limit (largest: a size already read)."""
        top = min(PROBE_MAX, num_ctx // 2)
        size, crash = min(start, top), None
        if largest >= top:
            return largest, None
        while crash is None:
            read = self.probe(size, num_ctx)
            if read is None:
                crash = size
            else:
                largest = read
                if size >= top:
                    return largest, None
                size = min(size * 2, top)
        while crash - largest > PROBE_STEP:
            middle = (largest + crash) // 2
            read = self.probe(middle, num_ctx)
            crash, largest = (middle, largest) if read is None else (crash, min(read, crash - 1))
        if not largest:
            raise SystemExit(f"{self.name} fails even on a {crash}-token prompt; it cannot be used.")
        return largest, crash

    @staticmethod
    def is_gguf(facts):
        """A model in GGUF format, as Ollama reports it (run by its llama.cpp runner)."""
        return (facts.get("format") or "").lower() == "gguf"

    def read_in_pieces(self, num_ctx, largest, crash):
        """A GGUF model's runner can crash reading a long prompt in one piece; Ollama's num_batch
        makes it read the prompt in pieces (it is a setting of that runner, so only GGUF models try it).
        Tried once at the size that crashed: when that reads, the limit is measured again with it
        (and kept in the model's settings)."""
        self.options = {"num_batch": PROBE_BATCH}
        print(f"  trying again in pieces of {PROBE_BATCH} tokens (num_batch)")
        read = self.probe(crash, num_ctx)
        if read is None:
            self.options = {}
            print("  reading in pieces does not help this model")
            return largest, crash
        more, crash_in_pieces = self.prompt_limit(num_ctx, start=crash * 2, largest=read)
        if crash_in_pieces and crash_in_pieces - crash <= PROBE_STEP:
            self.options = {}
            print(f"  in pieces it crashed at {crash_in_pieces}: no better; reading in pieces does not help this model")
            return largest, crash
        print(f"  in pieces it reads {more} tokens" + (f" (crashed at {crash_in_pieces})" if crash_in_pieces else ""))
        return more, crash_in_pieces

    def confirmed_budget(self, largest, crash, num_ctx):
        """The prompt budget: the largest prompt read, when the model reads it CONFIRM_READS more
        times in a row (each on a fresh load); else 1% lower (CONFIRM_STEP) until one is. No margin here: a run
        already aims its prompts 5% under the budget (PROMPT_HEADROOM)."""
        if crash is None:
            return largest
        budget = largest
        print(f"  confirming {budget} tokens: {CONFIRM_READS} more reads in a row")
        while budget >= MIN_PROMPT_TOKENS:
            reads = self.reads_in_a_row(budget, num_ctx)
            if reads:
                return min(budget, *reads)
            budget = int(budget * CONFIRM_STEP)
            print(f"  trying {budget} tokens")
        raise SystemExit(f"{self.name} does not read even {MIN_PROMPT_TOKENS}-token prompts reliably; it cannot be used.")

    def reply_limit(self, context_length):
        """The longest reply, exactly. Writing fills GPU memory token by token, so one reply asked
        to run to REPLY_PROBE_MAX tokens (it cannot end early: the model continues a count) shows
        where memory runs out: the tokens written before the crash. A reply of that length is then
        written once more to confirm it; when that one fails sooner, where it failed is the new
        longest reply, confirmed the same way. Also measures the write speed."""
        top = min(REPLY_PROBE_MAX, context_length or REPLY_PROBE_MAX)
        num_ctx = Models.context_for(self.LONG_REPLY_PROMPT_TOKENS, top, context_length)
        print(f"  longest reply: writing until memory runs out, up to {top} tokens (minutes, at ~25 tokens a second)")
        written, error = self.long_reply(top, num_ctx)
        if error is None:
            return written
        for _ in range(REPLY_CONFIRMS):
            self.reply_failed(written, error)
            print(f"  confirming {written} tokens: writing a reply that long once more")
            again, error = self.long_reply(written, num_ctx)
            if error is None or again >= written:
                return written
            written = again
        self.reply_failed(written, error)
        lowered = max(MIN_REPLY_TOKENS, int(written * CRASH_SHRINK))
        print(f"  the longest reply varies from write to write; using {lowered} tokens ({CRASH_SHRINK:.0%} of the "
              f"shortest)")
        return lowered

    def settings(self, facts, think, budget, largest, crash, max_reply):
        read = self.best.get("prompt_eval_count", 0) / (self.best.get("prompt_eval_duration") or 1) * 1e9
        return {"model": self.name, "calibrated": now_text(),
                "max_prompt_tokens": budget, "largest_prompt_read": largest, "crashed_reading": crash,
                "num_ctx": Models.context_for(budget, max_reply, facts["context_length"]),
                "context_length": facts["context_length"], "think": think, **Models.reply_limits(max_reply),
                "chars_per_token": self.safe_ratio(), "num_batch": self.options.get("num_batch"),
                "read_tokens_per_s": round(read) if self.best.get("prompt_eval_duration") else None,
                "write_tokens_per_s": self.write_tps, "load_seconds": self.load_seconds, "probes": self.history,
                "tokens_per_digit": round(self.tokens_per_digit, 4)}

    def probe_failed(self, tokens, exc, recover=True):
        """A probe failed: say so (and when the model read as much before), then (recover) give the
        crashed runner time to free its memory, so the next probe is not measured against a dying one."""
        self.last_error = exc
        earlier = max((n for n, result in self.history if result == "read"), default=0)
        varies = f"; it read {earlier} before, so the limit varies" if earlier >= tokens else ""
        print(f"  prompt of {tokens} tokens: failed ({first_line(str(exc), 80)}){varies}")
        self.history.append([tokens, "crashed"])
        Ollama.unload(self.name)
        if recover:
            time.sleep(CRASH_RECOVERY)

    def reads_in_a_row(self, tokens, num_ctx):
        """The token counts of CONFIRM_READS reads of `tokens` tokens, or None at the first failure."""
        reads = []
        for _ in range(CONFIRM_READS):
            read = self.probe(tokens, num_ctx)
            if read is None:
                return None
            reads.append(read)
        return reads

    def reply_failed(self, written, error):
        """A long reply failed: a crash (out of memory) after some tokens is a measurement; anything
        else stops the calibration. The crashed runner then gets time to free its memory."""
        if not error.crashed or not written:
            raise SystemExit(f"{self.name} could not write a long reply: {error}")
        self.history.append([written, "reply crashed"])
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY)

    def safe_ratio(self):
        """The chars per token prompts are sized with: what was measured, 3% lower (prompts with
        more markup have fewer chars per token; each run then learns the real ones)."""
        if not self.measured_ratio:
            return CHARS_PER_TOKEN
        return round(max(MIN_CHARS_PER_TOKEN, min(self.measured_ratio * 0.97, MAX_CHARS_PER_TOKEN)), 2)


class Model:
    """The one model, with the settings its calibration measured (models/<model>.json): its
    context, its think setting, every prompt within its input budget, every reply up to the
    longest it writes (a fix: up to fix_max)."""

    def __init__(self, name, settings, keep_alive, log_path, exclusive=True,
                 max_growth=DEFAULT_CONFIG["max_growth"]):
        self.name, self.settings = name, settings
        self.max_tokens, self.num_ctx = int(settings["max_prompt_tokens"]), int(settings["num_ctx"])
        self.think = settings.get("think")
        self.build_think = Config.get("build_think") if self.think not in (None, False) else None
        self.building = False  # set per phase: building calls think at build_think, planning at think
        self.max_reply, self.fix_max = int(settings["max_reply_tokens"]), int(settings["fix_max_tokens"])
        self.keep_alive = keep_alive
        self.options = {"num_batch": int(settings["num_batch"])} if settings.get("num_batch") else {}
        self.log, self.exclusive, self.max_growth = EventLog(log_path), exclusive, max_growth
        self.think_rejected = False
        self.crashes = 0            # runner crashes in this run (limits lowered after them hold for this run only)
        self.calibrated = (self.max_tokens, self.max_reply)
        self.base_gb = None
        self.chars_per_token = max(MIN_CHARS_PER_TOKEN, min(float(settings.get("chars_per_token") or CHARS_PER_TOKEN),
                                                            MAX_CHARS_PER_TOKEN))
        self.ratios = []  # chars per real token of recent prompts

    def unload(self):
        if self.keep_alive not in (0, "0"):
            Ollama.unload(self.name)

    def learned_chars_per_token(self):
        """What this run's prompts showed (None until enough were seen): the next run starts there."""
        return round(min(self.ratios), 2) if len(self.ratios) >= 5 else None

    def limit_chars(self, system, tokens=None):
        """Room for the user message, in chars, so system + user stay within the token budget."""
        return int((tokens or self.max_tokens) * PROMPT_HEADROOM * self.chars_per_token) - len(system) - 40

    def ask(self, system, parts, label, **kw):
        """One request (ask_once); a prompt too large as it is gets every part shortened (exact
        ones too) rather than failing: PromptTooLarge only when even that does not fit."""
        try:
            return self.ask_once(system, parts, label, **kw)
        except PromptTooLarge:
            print(f"  {label}: the prompt is too large as it is; shortening every part")
        relaxed = lambda limit: [p.relaxed() for p in (parts(limit) if callable(parts) else parts)]
        return self.ask_once(system, relaxed, label, **kw)

    def ask_once(self, system, parts, label, budget=None, done_when=None, max_reply=None, unthinking_retry=True):
        """One request. parts: a list of Part, or a function limit -> list (rebuilt on a retry).
        The reply may be as long as the longest the model writes (budget: shorter), and at most
        max_reply: a smaller budget saves nothing on a reply that ends on its own, and one that
        is cut is asked again from the start.
        A reply cut off while still thinking is asked again with twice the budget up to max_reply,
        then (if unthinking_retry) without thinking; otherwise it counts as an empty reply."""
        if self.exclusive:
            self.evict_others()
        tries = Tries(None if self.think_rejected else self.think_now(), budget or self.max_reply)
        max_reply = max_reply or self.max_reply
        system += Prompts.NO_TOOL_CALLS
        while True:
            user = self.user_message(system, parts, label, tries.shrink)
            cap = min(self.reply_room(system, user), self.max_reply, max_reply)
            tries.budget = max(MIN_REPLY_TOKENS, min(tries.budget, cap))
            shown = self.call_label(label, tries.think, tries.budget)
            started = time.perf_counter()
            try:
                data = Ollama.chat(self.name, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                   self.keep_alive, self.request_options(tries.budget), tries.think, shown, done_when)
            except OllamaError as exc:
                final = self.after_error(exc, label, tries, self.prompt_tokens(system, user), shown, started)
                if final is not None:
                    return final
                continue
            content, cut = self.log_exchange(system, user, shown, tries.budget, data, started)
            if cut and not content.strip() and tries.think is not False:
                action = self.after_cut(shown, data, started, tries.budget, cap, unthinking_retry)
                if action == "give up":
                    return ""
                if action == "more":
                    tries.budget = min(tries.budget * 2, cap)
                else:
                    tries.think = False
                continue
            self.report(shown, data, started, cut)
            self.check_memory()
            return content

    def evict_others(self):
        """Unload every other model, so two never share memory (other apps reload theirs)."""
        for loaded, _ in Ollama.loaded_models() or []:
            if loaded and loaded != self.name:
                print(f"  unloading {loaded} (exclusive mode: only one model in memory)")
                Ollama.unload(loaded)

    def think_now(self):
        """The think setting for the current phase: planning at the model's, building at build_think."""
        return self.build_think if self.building and self.build_think else self.think

    def user_message(self, system, parts, label, shrink):
        limit = self.limit_chars(system, int(self.max_tokens * OOM_SHRINK ** shrink))
        notes = []
        user = Part.assemble(parts(limit) if callable(parts) else parts, limit, notes)
        if notes:
            print(f"  {label}: shortened {', '.join(notes)} to fit the prompt budget")
        return user

    def reply_room(self, system, user):
        return self.num_ctx - int((len(system) + len(user)) / self.chars_per_token) - 64

    @staticmethod
    def call_label(label, think, budget):
        mode = "no thinking" if think is False else f"think {think if think is not None else 'default'}"
        return f"{label} [{mode}, reply up to {budget // 1024}K tok]"

    def request_options(self, budget):
        options = dict(self.options)
        options.setdefault("num_ctx", self.num_ctx)
        options["num_predict"] = budget
        return options

    def after_error(self, exc, label, tries, prompt_tokens, shown, started):
        """What a failed request leads to: None to try it again (after what the failure calls for),
        or the reply to return (what arrived before a reply error, once tries run out)."""
        if exc.crashed:
            tries.crashes += 1
            if exc.streamed:
                self.after_reply_crash(exc, label, tries.crashes)
            else:
                self.after_crash(exc, label, prompt_tokens, tries.crashes)
            return None
        if exc.in_reply:
            tries.reply_errors += 1
            return exc.partial if self.reply_error_is_final(exc, shown, started, tries.reply_errors) else None
        self.recover(exc, label, tries)
        return None

    def prompt_tokens(self, system, user):
        """The most tokens system + user can be (the lowest chars per token seen)."""
        return int((len(system) + len(user)) / self.chars_per_token)

    def log_exchange(self, system, user, shown, budget, data, started):
        """Learn from the real token count and log the exchange. Returns (content, cut)."""
        content = data.get("message", {}).get("content") or ""
        cut = data.get("done_reason") == "length"
        self.learn_chars_per_token(len(system) + len(user), data.get("prompt_eval_count", 0))
        self.log.add({"label": shown, "model": self.name, "system": system, "user": user,
                      "prompt_tokens": data.get("prompt_eval_count", 0),
                      "reply_tokens": data.get("eval_count", 0), "budget": budget, "cut": cut,
                      "seconds": round(time.perf_counter() - started, 1),
                      "load_seconds": round(data.get("load_duration", 0) / 1e9, 1), "reply": content})
        return content, cut

    @staticmethod
    def after_cut(shown, data, started, budget, cap, unthinking_retry):
        """A reply cut off while still thinking: "more" (twice the budget), "unthinking" (ask again
        without thinking) or "give up" (it counts as an empty reply)."""
        tokens = data.get("eval_count", 0)
        if budget < cap:
            Console.step(f"{shown}  (cut at {tokens} tokens while thinking; asking again with up to "
                         f"{min(budget * 2, cap) // 1024}K tokens)", started)
            return "more"
        if unthinking_retry:
            Console.step(f"{shown}  (cut at the most a reply may think; asking again without thinking)", started)
            return "unthinking"
        Console.step(f"{shown}  (thought to the {tokens}-token cap without an answer; counted as an attempt "
                     f"with no change)", started)
        return "give up"

    def report(self, shown, data, started, cut):
        tokens_in, tokens_out = data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        if tokens_in > self.max_tokens:
            print(f"  note: prompt was {tokens_in} tokens, above the {self.max_tokens} budget estimate")
        if data.get("early_stop"):
            Console.step(f"{shown}  (stopped as soon as the reply was complete, ~{tokens_out} tok)", started)
        else:
            Console.step(f"{shown}  (prompt {tokens_in} tok, gen {data.get('eval_duration', 0) / 1e9:.1f}s"
                         f"/{tokens_out} tok, load {data.get('load_duration', 0) / 1e9:.1f}s)", started)
        if cut:
            print(f"  reply cut at {tokens_out} tokens")

    def check_memory(self):
        """With the model kept loaded, unload it once it grows past max_growth x its first size."""
        if self.keep_alive in (0, "0"):
            return
        size = next((s / 1e9 for n, s in Ollama.loaded_models() or [] if n == self.name), None)
        if size is None:
            return
        if self.base_gb is None:
            self.base_gb = size
        elif size > self.base_gb * self.max_growth:
            print(f"  model grew {self.base_gb:.1f} -> {size:.1f} GB; unloading")
            self.unload()

    def ask_until_usable(self, system, parts, label, read, attempts=3):
        """Ask for JSON until read(reply) gives a result; read raises ValueError and the like when
        a reply is not usable, and the reason goes into the next prompt. None when no reply was."""
        for attempt in range(1, attempts + 1):
            reply = Reply(self.ask(system, parts, f"{label} (attempt {attempt})", done_when=Reply.json_complete))
            try:
                return read(reply)
            except (ValueError, ToolError, AttributeError, TypeError, KeyError) as exc:
                print(f"  {label.lower()} not usable: {exc}")
                parts = parts + [Part("Note", f"Your previous reply was not usable ({exc}). Reply with ONLY the JSON.",
                                      True)]
        return None

    def after_reply_crash(self, exc, label, crashes):
        """The runner died while writing the reply (out of GPU memory, which fills token by token,
        or memory other apps took). The same call is made again once the runner has freed its
        memory; when it crashes again near the longest reply, that limit drops a little
        (CRASH_SHRINK) for the rest of this run only. A crash far below the limit is not about the
        reply's length: the limit stays."""
        self.stop_after(exc, crashes, f"the last time after writing {exc.streamed} tokens")
        what = f"crashed after writing {exc.streamed} tokens"
        if crashes > 1 and exc.streamed >= self.max_reply * CRASH_NEAR:
            lowered = max(MIN_REPLY_TOKENS, int(self.max_reply * CRASH_SHRINK))
            what += f"; longest reply {self.max_reply} -> {lowered} tokens for this run"
            self.max_reply, self.fix_max = lowered, min(self.fix_max, lowered)
        self.crashed(exc, label, what, written=exc.streamed)

    def after_crash(self, exc, label, prompt_tokens, crashes):
        """The runner died reading the prompt (some runners do above some size, which varies a
        little). The same prompt is tried again once the crashed runner has freed its memory; when
        it crashes again, the budget drops a little (CRASH_SHRINK) for the rest of this run only:
        the measured value stays in the model's settings, and the run ends with a note to measure
        again if crashes keep happening."""
        self.stop_after(exc, crashes, f"the last time reading a prompt of ~{prompt_tokens} tokens")
        what = f"crashed reading a prompt of ~{prompt_tokens} tokens"
        if crashes > 1 and prompt_tokens >= self.max_tokens * CRASH_NEAR:
            lowered = max(MIN_PROMPT_TOKENS, int(self.max_tokens * CRASH_SHRINK))
            what += f"; prompt budget {self.max_tokens} -> {lowered} tokens for this run"
            self.max_tokens = lowered
        self.crashed(exc, label, what, prompt_tokens=prompt_tokens)

    def reply_error_is_final(self, exc, shown, started, tries):
        """Ollama failed while streaming the reply (it could not parse what the model wrote). True when
        that was the last try: what arrived before the error counts as the reply (often nothing usable,
        which the caller treats as an attempt with no change; the run goes on)."""
        self.log.add({"label": shown, "model": self.name, "error": str(exc), "reply": exc.partial})
        if tries <= REPLY_ERROR_RETRIES:
            Console.step(f"{shown}  ({first_line(str(exc), 120)}; asking again)", started)
            return False
        Console.step(f"{shown}  ({first_line(str(exc), 120)} again; using the {len(exc.partial)} chars that came "
                     f"before it)", started)
        return True

    def recover(self, exc, label, tries):
        """What to change before trying a failed request again; re-raises when nothing helps."""
        think = tries.think
        if self.building and think == self.build_think != self.think and "think" in str(exc).lower():
            print(f"  {label}: {self.name} rejected think={think!r} for building; using {self.think!r} as for planning")
            self.build_think = None
            tries.think = self.think
        elif exc.oom and tries.shrink < OOM_RETRIES:
            print(f"  {label}: out of GPU memory; retrying this request with a prompt budget of "
                  f"{int(self.max_tokens * OOM_SHRINK ** (tries.shrink + 1))} tokens")
            tries.shrink += 1
        elif exc.stalled and tries.stalls < STALL_RETRIES:
            print(f"  {label}: {exc}; reloading the model and retrying")
            Ollama.unload(self.name)
            tries.stalls += 1
        elif think not in (None, True, False) and "think" in str(exc).lower():
            print(f"  {label}: {self.name} rejected think={think!r}; using think=true")
            self.think = tries.think = True
        elif think is not None and "think" in str(exc).lower():
            print(f"  {label}: {self.name} rejected the think setting; retrying without it")
            self.think, self.think_rejected, tries.think = None, True, None
        else:
            raise exc

    def learn_chars_per_token(self, chars, tokens):
        """Learn chars per token from real prompts: the lowest recent ratio, capped."""
        if tokens and chars and tokens > 50:
            self.ratios = (self.ratios + [chars / tokens])[-20:]
            self.chars_per_token = max(MIN_CHARS_PER_TOKEN, min(min(self.ratios), MAX_CHARS_PER_TOKEN))

    def stop_after(self, exc, crashes, last):
        if crashes > CRASH_RETRIES:
            raise OllamaError(f"{exc}\n{self.name} crashed {crashes} times on this call, {last}. If this keeps "
                              f"happening, measure the model again: tinker.py --calibrate {self.name}", crashed=True)

    def crashed(self, exc, label, what, **facts):
        """Say what happened, log it, and give the crashed runner time to free its memory."""
        self.crashes += 1
        print(f"  {label}: {self.name} {what} ({first_line(str(exc), 90)}); trying again in {CRASH_RECOVERY}s")
        self.log.add({"label": label, "model": self.name, "error": str(exc), **facts,
                      "budget_now": self.max_tokens, "max_reply_now": self.max_reply})
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY)


class Learner:
    """Learns from the web what a model may not know, or knows as it was when it was trained, and
    saves it (Library), indexed with the embedding model (Embeddings). Two kinds:
    a topic (--learn TOPIC: a framework, a library, a technique such as TDD): the model plans a
    few searches, at least one for each thing the topic names, aimed at how to use it today;
    a language (--learn-language LANGUAGE, or a language a run's recall phase needs): the same
    sections for every language, each with its own searches, what to look for and share of the
    facts: its fundamentals, what is new, its tools (one per role: environment, packages, build,
    tests, format, lint: the ones the request names, else the usual ones) and its conventions.
    Then, for both: the commands that show what is installed here run (only such commands: one
    each, no shell operators, nothing installed or changed); the searches run and their pages are
    taken in turn (each search's first result, then each one's second, ...), so every search is
    read; the model condenses each page into the facts it states, each kept with its source;
    repeats are dropped and, past a section's share, the model keeps the most useful. Learning
    again reuses the saved plan (edit it there). It needs the Internet plugin and an embedding
    model."""
    VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
    SHELL_OPERATORS = re.compile(r"[;&|<>`$\n\\]")
    CHANGES = re.compile(r"\b(install|uninstall|remove|delete|update|upgrade|add|rm|init|new|create|run|exec|"
                         r"download|fetch|get|set|config)\b", re.I)

    def __init__(self, model, internet, embeddings, redirect_languages=False):
        self.model, self.internet = model, internet
        self.embeddings = embeddings  # None in a run whose embedding model failed: then not indexed
        self.redirect_languages = redirect_languages  # --learn: a language is learned with --learn-language
        self.model_failed = ""  # why the model stopped answering while reading pages (then reading ends)
        self.notes = []  # what could not be learned, and why
        self.kept_earlier = False  # reading was cut short, so the topic as learned before was kept

    def learn(self, topic):
        """A topic learned and saved (its entry), or None when nothing could be learned. A topic
        that is a programming language is learned as one in a run; --learn stops and says to use
        --learn-language."""
        self.start()
        print(f"\nLearning about {topic}")
        plan = self.plan(topic, Library.read(Library.slug(topic)))
        if plan["language"]:
            if self.redirect_languages:
                raise SystemExit(f"{topic!r} is about the programming language {plan['language']}: learn it with "
                                 f"--learn-language \"{topic}\" (its fundamentals, what is new, its tools and "
                                 f"conventions). Nothing was read.")
            slug = Library.language(plan["language"])
            if slug:  # learned as a language already (with the tools asked for then): not learned again unasked
                print(f"  it is the programming language {plan['language']}, learned already ({Library.path(slug).name})")
                return Library.read(slug)
            print(f"  it is the programming language {plan['language']}: learning it as a language")
            return self.learn_language(topic)
        focus = Prompts.TOPIC_FOCUS.format(topic=plan["name"])
        searches = [{"query": query, "section": "", "pages": SEARCH_RESULTS, "focus": focus} for query in plan["queries"]]
        return self.finish(Library.slug(topic), topic, plan, searches, MAX_PAGES, {"": MAX_FACTS})

    def start(self):
        self.notes, self.model_failed, self.kept_earlier = [], "", False

    def plan(self, topic, known):
        """What the topic is, the searches and the commands: as saved for it, else from the model."""
        plan = None
        if known:
            try:
                plan = self.read_plan(known)
                print(f"  the searches and commands saved for it ({Library.path(Library.slug(topic)).name})")
            except ValueError as exc:
                print(f"  its saved plan is not usable ({exc}); planning it again")
        if plan is None:
            parts = [Part("Topic", topic, True), Part("Today", datetime.date.today().isoformat(), True)]
            plan = self.model.ask_until_usable(Prompts.LEARN_PLAN_SYSTEM, parts, "Plan learning",
                                               lambda reply: self.read_plan(reply.json()), attempts=2)
            if plan is None:
                raise SystemExit(f"{self.model.name} gave no usable plan for learning about {topic}; see the log of "
                                 f"its replies ({self.model.log.path})")
            if not plan["language"]:
                print(f"  {plan['name']}: {plan['summary']}")
        self.show_dropped(plan)
        return plan

    def learn_language(self, text):
        """A programming language learned as it is today (see the class), saved as
        learned/<language>.json; None when nothing could be learned."""
        self.start()
        print(f"\nLearning the language {text}")
        plan = self.plan_language(text)
        searches = [{"query": search["query"], "section": search["section"], "pages": SECTION_PAGES[search["section"]],
                     "focus": Prompts.LANGUAGE_FOCUS[search["section"]].format(language=plan["language"])}
                    for search in plan["searches"]]
        searches += [{"query": tool["search"], "section": "tools", "pages": SECTION_PAGES["tools"],
                      "focus": Prompts.TOOL_FOCUS.format(tool=tool["name"], role=tool["role"], language=plan["language"])}
                     for tool in plan["tools"]]
        return self.finish(Library.slug(plan["language"]), text, plan, searches, LANGUAGE_PAGES, SECTION_FACTS)

    def finish(self, slug, topic, plan, searches, max_pages, shares):
        """What is installed here and what the pages state, saved as slug (a topic learned before
        is kept when reading was cut short or found nothing); the entry, or None."""
        machine = self.look_at_machine(plan["commands"])
        facts, sources = self.read_web(plan["name"], searches, max_pages)
        known = Library.read(slug)
        cut_short = self.cut_short()
        if known and (cut_short or not facts):
            print(f"  {cut_short or 'no page gave facts'}; kept what was learned before ({known.get('learned', '?')})")
            self.kept_earlier = True
            return known
        if not facts and not any(m["found"] for m in machine):
            print(f"  nothing learned about {topic}: " + ("; ".join(self.notes) or "the pages stated nothing useful"))
            return None
        facts = self.keep_shares(plan["name"], facts, shares)
        entry = {"topic": topic, **{k: v for k, v in plan.items() if k != "dropped"}, "learned": now_text(),
                 "model": self.model.name, "machine": machine, "facts": facts, "sources": sources, "notes": self.notes}
        Library.save(slug, entry)
        print(f"  learned {len(facts)} fact(s) about {plan['name']} from {len(sources)} page(s); saved in "
              f"{Library.path(slug)}")
        self.index(slug, entry)
        return entry

    @staticmethod
    def read_plan(data):
        """A topic's plan, checked: a name, a summary, up to MAX_QUERIES searches, up to
        MAX_COMMANDS commands that only show what is installed (any other is "dropped"), and
        "language": the language's name when the topic is one (else "")."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        name, summary = str(data.get("name") or "").strip()[:80], " ".join(str(data.get("summary") or "").split())[:200]
        language = str(data.get("language") or "").strip()[:40]
        queries = [" ".join(str(q).split()) for q in data.get("queries") or [] if str(q).strip()][:MAX_QUERIES]
        if not name or (not queries and not language):
            raise ValueError("it needs a name and at least one search")
        commands, dropped = Learner.safe_commands(data.get("commands") or [])
        return {"name": name, "summary": summary, "language": language, "queries": queries,
                "commands": commands[:MAX_COMMANDS], "dropped": dropped}

    def show_dropped(self, plan):
        for command in plan["dropped"]:
            print(f"  left out `{command}`: a command here must only show what is installed (one command, no shell "
                  f"operators, nothing installed or changed)")

    def plan_language(self, text):
        """The language's name and version, its tools, the searches of each section and the
        commands: as saved for this request, else from the model."""
        plan = None
        known = next((entry for entry in Library.topics().values()
                      if entry.get("kind") == "language" and entry.get("topic") == text), None)
        if known:
            try:
                plan = self.read_language_plan(known)
                print(f"  the searches and commands saved for it ({Library.path(Library.slug(plan['language'])).name})")
            except ValueError as exc:
                print(f"  its saved plan is not usable ({exc}); planning it again")
        if plan is None:
            parts = [Part("Request", text, True), Part("Today", datetime.date.today().isoformat(), True)]
            plan = self.model.ask_until_usable(Prompts.LANGUAGE_PLAN_SYSTEM, parts, "Plan learning the language",
                                               lambda reply: self.read_language_plan(reply.json()), attempts=2)
            if plan is None:
                raise SystemExit(f"{self.model.name} gave no usable plan for learning {text}; see the log of its "
                                 f"replies ({self.model.log.path})")
            if plan.get("not_language"):
                raise SystemExit(f"{text!r} is not a programming language ({plan['not_language']}): learn it with "
                                 f"--learn \"{text}\". Nothing was read.")
            print(f"  {plan['name']}: {plan['summary']}")
        print(f"  tools: {Library.tools_text(plan['tools']) or 'none named'}")
        self.show_dropped(plan)
        return plan

    def look_at_machine(self, commands):
        """What each command shows (run in an empty folder): its first line and version number; a
        command that fails means that tool is not installed."""
        found = []
        with tempfile.TemporaryDirectory(prefix="tinker-learn-") as folder:
            ws = Workspace(Path(folder))
            for command in commands:
                code, output = ws.run(command, timeout=VERSION_TIMEOUT, quiet=True)
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                version = self.VERSION_RE.search(" ".join(lines[:5])) if code == 0 else None
                found.append({"command": command, "found": code == 0, "version": version.group(0) if version else "",
                              "output": (lines[0] if lines else "")[:160]})
                failed = "did not finish" if code is None else f"exit code {code}"
                print(f"  $ {command}: " + (found[-1]["output"][:100] if code == 0 else f"not installed ({failed})"))
        return found

    def read_web(self, name, searches, max_pages):
        """(facts, sources): the searches run, their pages taken in turn (each search's first
        result, then each one's second, ...: every search is read) up to max_pages, each read for
        what its search looks for; a fact of a section is kept with it."""
        results = []
        for search in searches:
            found = self.internet.search(search["query"], SEARCH_RESULTS)
            if found is None:
                self.notes.append(f"search '{search['query']}': {self.internet.why(search['query'])}")
                print(f"  {self.notes[-1]}")
            results.append((found or [])[:search["pages"]])
        pages, seen = [], set()
        for rank in range(SEARCH_RESULTS):
            for search, found in zip(searches, results):
                if rank < len(found) and found[rank]["url"] not in seen and len(pages) < max_pages:
                    seen.add(found[rank]["url"])
                    pages.append(dict(found[rank], section=search["section"], focus=search["focus"]))
        facts, sources = [], []
        for number, page in enumerate(pages, 1):
            if self.model_failed:
                break
            found = self.read_page(name, page, f"{number}/{len(pages)}")
            sources += [page["url"]] if found else []
            found = [dict(f, section=page["section"]) if page["section"] else f for f in found]
            facts += [f for f in found if not any(similarity(f["text"], k["text"]) >= SAME_FACT for k in facts)]
        return facts, sources

    def cut_short(self):
        """Why the reading ended before every page was read ("" when it did not): the web or the
        model was lost on the way. Learning that was cut short never replaces what was learned before."""
        if not self.internet.available():
            return f"the web was lost ({self.internet.why()})"
        if self.model_failed:
            return f"the model stopped answering ({self.model_failed})"
        return ""

    def keep_shares(self, name, facts, shares):
        """Each section's facts, at most its share (shares: {section: count}; a topic's facts have
        no section): past it, the most useful as the model picks them."""
        kept = []
        for section, share in shares.items():
            facts_of = [f for f in facts if f.get("section", "") == section]
            kept += self.pick(f"{name}: {section}" if section else name, facts_of, share) if len(facts_of) > share \
                else facts_of
        return kept

    def index(self, slug, entry):
        """The texts embedded now, so the recall phase finds them by meaning (if that fails, the
        recall phase embeds them when it searches)."""
        if self.embeddings is None:
            return
        texts = [Library.searched(item) for item in Library.items({slug: entry})]
        if self.embeddings.vectors(texts) is None:
            print(f"  not indexed now ({self.embeddings.failed}); the recall phase indexes it when it searches")
        else:
            print(f"  indexed with {self.embeddings.name}")

    @staticmethod
    def safe_commands(commands):
        """(kept, dropped): only commands that show what is installed are kept: one command each,
        no shell operators, nothing installed or changed."""
        kept, dropped = [], []
        for command in commands:
            command = " ".join(str(command).split())
            if not command:
                continue
            unsafe = (Learner.SHELL_OPERATORS.search(command) or Learner.CHANGES.search(command)
                      or Workspace.UNSAFE_COMMAND.search(command))
            (dropped if unsafe else kept).append(command)
        return kept, dropped

    @staticmethod
    def read_language_plan(data):
        """A language's plan, checked: its name and version, one tool per role (TOOL_ROLES) with
        the search that finds how to use it, the searches of each section, and the commands that
        only show what is installed (any other is "dropped"). {"not_language": why} when the model
        says the request is no programming language."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        language = str(data.get("language") or data.get("name") or "").strip()[:40]
        summary = " ".join(str(data.get("summary") or "").split())[:200]
        if not language:
            if summary:
                return {"not_language": summary}
            raise ValueError("it needs the language's name")
        version = str(data.get("version") or "").strip()[:20]
        tools, commands = {}, list(data.get("commands") or [])
        for tool in data.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            role, tool_name = str(tool.get("role") or "").strip().lower(), str(tool.get("name") or "").strip()[:40]
            if role in TOOL_ROLES and tool_name and role not in tools:
                search = " ".join(str(tool.get("search") or "").split()) or f"how to use {tool_name} in a {language} project"
                tools[role] = {"role": role, "name": tool_name, "search": search}
                commands += [tool["command"]] if tool.get("command") else []
        searches = [{"section": str(s.get("section") or "").strip().lower(), "query": " ".join(str(s.get("query") or "").split())}
                    for s in data.get("searches") or [] if isinstance(s, dict)]
        searches = [s for s in searches if s["section"] in LANGUAGE_SECTIONS and s["query"]][:LANGUAGE_SEARCHES]
        if not searches:
            raise ValueError(f"it needs searches for the language's {', '.join(LANGUAGE_SECTIONS)}")
        commands, dropped = Learner.safe_commands(commands)
        return {"kind": "language", "language": language, "name": f"{language} {version}".strip(), "version": version,
                "summary": summary or language, "tools": list(tools.values()), "searches": searches,
                "commands": list(dict.fromkeys(commands))[:LANGUAGE_COMMANDS], "dropped": dropped}

    def read_page(self, topic, page, number):
        """The facts one page states about what its search looks for, from the top of its text
        (the search's excerpt when the page itself cannot be read)."""
        got = self.internet.fetch(page["url"])
        text = (got or {}).get("content") or page.get("content") or ""
        if not text.strip():
            why = f"{page['url']}: {self.internet.why(page['url'])}"
            self.notes.append(f"page {why}")
            print(f"  page {number}: {why}")
            return []
        source = Part("Page", f"{(got or {}).get('title') or page.get('title', '')}\n{page['url']}", True)
        focus = Part("Look for", page["focus"], True)
        room = self.model.limit_chars(Prompts.FACTS_SYSTEM) - source.size() - focus.size() - len(topic) - 200
        found = []
        for i, chunk in enumerate(split_text(text, room)[:PAGE_CHUNKS], 1):
            parts = [Part("Topic", topic, True), focus, source, Part("Excerpt", chunk, True)]
            try:
                facts = self.model.ask_until_usable(Prompts.FACTS_SYSTEM, parts, f"Facts, page {number} part {i}",
                                                    self.read_facts, attempts=2) or []
            except PromptTooLarge as exc:
                self.notes.append(f"page {page['url']}: {first_line(str(exc), 160)}")
                break
            except OllamaError as exc:  # it crashed or failed again and again: the pages left are not read
                self.model_failed = first_line(str(exc), 200)
                self.notes.append(f"the model stopped answering at page {number}; the pages after it were not read")
                print(f"  {self.notes[-1]} ({self.model_failed})")
                break
            found += [dict(f, source=page["url"]) for f in facts]
        return found

    def pick(self, topic, facts, count):
        """The most useful count, as the model picks them (the first ones if it cannot)."""
        listing = "\n".join(f"{i}. [{f['phase']}] {f['text']}" for i, f in enumerate(facts, 1))

        def read(reply):
            numbers = dict.fromkeys(reply.json().get("keep", []))
            kept = [facts[n - 1] for n in numbers if isinstance(n, int) and 1 <= n <= len(facts)]
            return kept[:count]

        try:
            kept = self.model.ask_until_usable(Prompts.PICK_FACTS_SYSTEM.format(count=count),
                                               [Part("Topic", topic, True), Part("Facts", listing, True)], "Pick facts",
                                               read, attempts=2)
        except (PromptTooLarge, OllamaError) as exc:
            print(f"  could not pick ({first_line(str(exc), 120)}): keeping the first {count}")
            kept = None
        return kept or facts[:count]

    @staticmethod
    def read_facts(reply):
        facts = []
        for item in reply.json().get("facts", []):
            if not isinstance(item, dict):
                continue
            text, phase = str(item.get("text", "")).strip()[:240], str(item.get("phase", "")).strip().lower()
            if text and phase in FACT_PHASES:
                facts.append({"phase": phase, "text": text})
        return facts[:FACTS_PER_CHUNK]

    def notice(self, topic, entry, seconds, failure=""):
        """How learning went, for a notification: learned (cut short or not), what was learned
        before kept, nothing learned, or stopped (failure: why)."""
        if failure:
            lines = [f"❌ Tinker stopped learning {topic}", first_line(failure, 600)]
        elif entry is None:
            lines = [f"❌ Tinker learned nothing about {topic}"] + [first_line(n, 200) for n in self.notes[:3]]
        elif self.kept_earlier:
            lines = [f"⚠️ Tinker could not finish learning {topic}: {self.cut_short() or 'no page gave facts'}",
                     f"Kept what was learned before ({entry.get('learned', '?')})"]
        else:
            lines = [f"{'⚠️' if self.cut_short() else '✅'} Tinker learned {entry['name']}",
                     first_line(entry["summary"], 300),
                     f"{len(entry['facts'])} fact(s) from {len(entry['sources'])} page(s)"]
            if entry.get("tools"):
                lines.append(f"tools: {Library.tools_text(entry['tools'])}")
            lines += [f"not learned: {first_line(n, 200)}" for n in entry["notes"][:3]]
        lines.append(f"Time: {human_duration(seconds)}")
        return "\n".join(lines)


class Toolchain:
    """setup (list), check, test_all, test_one ({file} {name}), run, smoke, format (the formatter,
    run over the code: the model never formats it), and serves (whether run keeps running until
    stopped): run in the project folder, judged only by exit code and output."""
    COMMANDS = ("check", "test_all", "test_one", "run", "smoke", "format")
    REQUIRED = ("test_all", "test_one", "run")
    NO_TESTS_RE = re.compile(r"\bno tests? (ran|run|to run|found|collected|matched)\b|\b0 (tests|passed)\b|"
                             r"\bran 0 tests\b|\btests?\s*0\b", re.I)
    ENV_TARGET = re.compile(r"(?<![\w.-])\.env(?![\w.-])")  # the settings file itself, not .env.example

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        setup = data.get("setup") or []
        setup = [str(c) for c in (setup if isinstance(setup, list) else [setup]) if str(c).strip()]
        self.setup = [c for c in setup if not self.ENV_TARGET.search(c)]
        self.dropped = [c for c in setup if self.ENV_TARGET.search(c)]  # Tinker makes .env itself
        for key in self.COMMANDS:
            setattr(self, key, str(data.get(key) or "").strip())
        self.serves = data.get("serves") in (True, "true", "True", "yes", 1)

    def __bool__(self):
        return bool(self.test_all and self.test_one)

    def summary(self):
        return (f"setup {' && '.join(self.setup) or '-'} | check {self.check or '-'} | tests {self.test_all} | "
                f"run {self.run}{' (serves)' if self.serves else ''} | smoke {self.smoke or '-'} | "
                f"format {self.format or '-'}")

    def run_setup(self, ws):
        """(ok, what failed): the failing command, its exit code and its output, so whoever fixes
        it knows which command failed even when it printed nothing."""
        for command in self.setup:
            code, output = ws.run(command, timeout=SETUP_TIMEOUT)
            if code != 0:
                ended = f"timed out after {SETUP_TIMEOUT}s" if code is None else f"exit code {code}"
                return False, f"$ {command}  failed ({ended})\n{output.strip() or '(it printed nothing)'}"
        return True, ""

    def to_dict(self):
        return {"setup": self.setup, **{key: getattr(self, key) for key in self.COMMANDS}, "serves": self.serves}

    def run_check(self, ws):
        """(ok, output): the fast compile or lint, when the toolchain has one."""
        if not self.check:
            return True, ""
        code, output = ws.run(self.check, quiet=True)
        return code == 0, f"$ {self.check}\n{output}"

    @staticmethod
    def reached_no_test(code, output, refs):
        """The test command failed without reaching any test (a missing framework, tool or
        command): no code change can fix that. Language-neutral: no test file or name in its output.
        A run that timed out is not one: some test hangs, which the fix loop deals with."""
        if code == 0 or (code is None and output != Workspace.REFUSED):
            return False
        if code in (126, 127) or code is None:
            return True
        return not any(r.file in output or (r.name and r.name in output) for r in refs)

    def run_all(self, ws):
        code, output = ws.run(self.test_all, timeout=self.suite_timeout(), quiet=True)
        if code is None and output != Workspace.REFUSED:
            output += f"\n(the tests did not finish within {self.suite_timeout()}s: some test hangs)"
        return code, output

    def merged(self, changes):
        """This toolchain with the values a dict of changes gives replacing its own."""
        data = self.to_dict()
        for key, value in (changes or {}).items():
            if key in data and value not in (None, "", []):
                data[key] = value
        return Toolchain(data)

    def run_app(self, ws):
        """(state, output): a server (serves) is started with a free PORT and must keep running
        until the smoke command gets its answer; a command's smoke command (it with sample arguments)
        must succeed. What failed is said plainly, with the app's own output."""
        if not self.run:
            return "passes", "no run command"
        env = {"PORT": str(self.free_port())}
        if not self.serves and not self.smoke:
            return "passes", ("no smoke command: a command is checked by running it with sample arguments (the "
                              "smoke command); run alone may rightly need arguments")
        if not self.serves:
            command = self.smoke
            code, output = ws.run(command, timeout=self.test_timeout(), quiet=True, env=env)
            if code == 0:
                return "passes", output
            if code is None:
                return "fails", (f"$ {command}\nit did not finish within {self.test_timeout()}s (if the app is a "
                                 f"server or UI that runs until stopped, it serves: set \"serves\": true):\n{output}")
            return "fails", f"$ {command}\nthe app exited with code {code}:\n{output}"
        started = ws.start(self.run, env)
        if started is None:
            return "fails", f"$ {self.run}\nrefused: the run command is unsafe"
        try:
            return self.wait_until_serving(ws, started, env)
        finally:
            ws.stop(started[0])

    def run_test(self, ws, ref):
        """(state, output) for one test: passes / fails; "no test ran" and "did not finish" are failures."""
        command = ref.command(self.test_one)
        code, output = ws.run(command, timeout=self.test_timeout(), quiet=True)
        if code is None and output != Workspace.REFUSED:
            return "fails", (f"$ {command}\nthe test did not finish within {self.test_timeout()}s: it hangs (an "
                             f"endless loop, a wait for input, a program or server left running, or a real network call). "
                             f"Output so far:\n{output}")
        if code == 0 and not self.NO_TESTS_RE.search(output):
            return "passes", output
        if code == 0:
            return "fails", f"$ {command}\nthe command ran no test: is the test named {ref.name}?\n{output}"
        return "fails", f"$ {command}\n{output}"

    def problems(self):
        problems = [f"toolchain.{key} is missing" for key in self.REQUIRED if not getattr(self, key)]
        if self.test_one and "{name}" not in self.test_one and "{file}" not in self.test_one:
            problems.append("toolchain.test_one must contain {name} (and {file} if the runner needs it)")
        return problems

    @staticmethod
    def suite_timeout():
        """All the (fast) tests together; past this, one run per test finds the one that hangs."""
        return 2 * Toolchain.test_timeout()

    @staticmethod
    def free_port():
        """A TCP port nothing listens on now (the start check gives it to the app as PORT)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def test_timeout():
        """Seconds one test may take: longer means it hangs (test_timeout in config.json)."""
        return int(Config.get("test_timeout"))

    def wait_until_serving(self, ws, started, env):
        proc, output_file = started
        began, answer = time.monotonic(), ""
        while time.monotonic() - began < START_TIMEOUT:
            if proc.poll() is not None:
                return "fails", (f"$ {self.run}  (PORT={env['PORT']})\nthe app stopped with exit code {proc.returncode} "
                                 f"instead of serving until stopped. Its output:\n{ws.read_output(output_file)}")
            if not self.smoke and time.monotonic() - began >= START_GRACE:
                return "passes", ws.read_output(output_file)
            if self.smoke:
                code, answer = ws.run(self.smoke, timeout=10, quiet=True, env=env)
                if code == 0:
                    return "passes", answer
            time.sleep(1)
        return "fails", (f"$ {self.run}  (PORT={env['PORT']})\n$ {self.smoke}\nthe app kept running but did not answer "
                         f"within {START_TIMEOUT}s; a server must listen on the port in the PORT setting (the "
                         f"environment overrides {ENV_FILE}). Last answer:\n{answer}\nThe app's output:\n"
                         f"{ws.read_output(output_file)}")


class Library:
    """What Tinker learned about topics and languages, one file each: learned/<topic>.json next to
    the script, from --learn TOPIC, --learn-language LANGUAGE or a run's recall phase. A run shows the facts of the topics its recall
    phase chose (ranked in the project's "learned") in the prompts of the phases they concern, as an
    optional part. Plain JSON: edit or delete a topic at any time."""

    @staticmethod
    def topics():
        """{slug: entry} of every topic learned."""
        found = {}
        for path in sorted(LEARNED_DIR.glob("*.json")) if LEARNED_DIR.is_dir() else []:
            entry = Library.read(path.stem)
            if entry:
                found[path.stem] = entry
        return found

    @staticmethod
    def slug(topic):
        """"Test-driven development" -> test-driven-development, "C++" -> cpp, "Python 3.14" -> python-3-14."""
        text = topic.lower().replace("+", "p").replace("#", "sharp")
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60]

    @staticmethod
    def items(topics):
        """What can be found in these topics ({slug: entry}), one item per text: each topic's
        summary, a language's tools, what is installed here and each fact, as {topic, name, phase,
        text}. A topic a hand edit broke is left out, with a note."""
        found = []
        for slug, entry in topics.items():
            try:
                name = str(entry.get("name") or entry["topic"])
                items = [{"topic": slug, "name": name, "phase": "summary", "text": str(entry.get("summary") or name)}]
                if entry.get("tools"):
                    items.append({"topic": slug, "name": name, "phase": "tools",
                                  "text": f"its tools: {Library.tools_text(entry['tools'])}"})
                installed = [(m["output"] if m["found"] else f"`{m['command']}` fails (not installed)")
                             for m in entry.get("machine", [])]
                if installed:
                    items.append({"topic": slug, "name": name, "phase": "machine",
                                  "text": f"on this machine: {'; '.join(installed)}"})
                items += [{"topic": slug, "name": name, "phase": str(f["phase"]), "text": str(f["text"])}
                          for f in entry["facts"]]
            except (KeyError, TypeError, AttributeError) as exc:
                print(f"  note: {Library.path(slug)} is not readable ({type(exc).__name__}: {exc}); left out")
                continue
            found += items
        return found

    @staticmethod
    def read(slug):
        """A topic's entry, or None (not learned, or not readable)."""
        entry = JsonFile.read(Library.path(slug), None) if slug else None
        return entry if isinstance(entry, dict) and isinstance(entry.get("facts", []), list) else None

    @staticmethod
    def tools_text(tools):
        """A language's tools in one line: "environment venv, tests pytest, format black"."""
        return ", ".join(f"{tool['role']} {tool['name']}" for tool in tools if isinstance(tool, dict) and tool.get("name"))

    @staticmethod
    def path(slug):
        return LEARNED_DIR / f"{slug}.json"

    @staticmethod
    def language(name):
        """The slug of a language learned with --learn-language (or by a run), or None."""
        slug = Library.slug(name or "")
        entry = Library.read(slug) if slug else None
        return slug if entry and entry.get("kind") == "language" else None

    @staticmethod
    def searched(item):
        """The text an item is found by (and embedded as): its topic's name, and its own text."""
        return f"{item['name']}: {item['text']}"

    @staticmethod
    def save(slug, entry):
        try:
            JsonFile.write(Library.path(slug), entry)
        except OSError as exc:
            print(f"  note: could not save what was learned ({exc})")

    @staticmethod
    def shown_in(item, phases):
        """Whether an item belongs in a prompt for these phases: what is installed here goes to the
        toolchain and config files' prompts, a language's tools to those and the tests', a fact to
        its own phase's."""
        shown = {"machine": {"toolchain", "config"}, "tools": {"toolchain", "config", "tests"}}.get(item["phase"])
        return item["phase"] in phases or bool(shown and shown & set(phases))


class Project:
    """Everything planned and done, saved in .tinker/spec.json (and shown in SPEC.md).
    Criteria are dicts: id, title, behaviour, test, effort, source, status, cycle, history."""
    DEPENDENCY_FILES = {"requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile", "package.json", "go.mod",
                        "Cargo.toml", "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "mix.exs"}

    def __init__(self, workdir, run, spec):
        self.workdir, self.run, self.spec = workdir, run, spec
        self.state = workdir / STATE_DIR

    @property
    def language(self):
        return self.spec.get("language", "")

    def save(self):
        (self.state / "spec.json").write_text(json.dumps(self.spec, indent=2), encoding="utf-8")
        (self.workdir / "SPEC.md").write_text(SpecDocument(self).text(), encoding="utf-8")

    @property
    def cycles(self):
        return self.spec.setdefault("cycles", [])

    def open_cycle(self):
        return next((c for c in self.cycles if c.get("phase") != "done"), None)

    def new_cycle(self, cycle_id, request, brief="", kind="feature"):
        cycle = {"id": cycle_id, "request": request, "brief": brief, "kind": kind,
                 "phase": FIX_PHASES[0] if kind == "fix" else PHASES[0], "created": now_text()}
        self.cycles.append(cycle)
        self.save()
        return cycle

    def level_counts(self):
        """(spec criteria, acceptance checks)."""
        accept = sum(1 for s in self.criteria if s.get("level") == "acceptance")
        return len(self.criteria) - accept, accept

    @property
    def criteria(self):
        """Every criterion, in plan order (saved as "steps")."""
        return self.spec.setdefault("steps", [])

    def counts(self):
        green = sum(1 for s in self.current if s["status"] == "green")
        parked = sum(1 for s in self.current if s["status"] == "blocked")
        return green, parked, len(self.current)

    def parked(self):
        """The criteria in play that nothing made pass (stored as "blocked")."""
        return [s for s in self.current if s["status"] == "blocked"]

    @staticmethod
    def is_start_check(criterion):
        """The start check: its "test" is the run command itself (RUN_TEST), not a test file."""
        return criterion["test"] == RUN_TEST

    @property
    def toolchain(self):
        return Toolchain(self.spec.get("toolchain"))

    @toolchain.setter
    def toolchain(self, toolchain):
        self.spec["toolchain"] = toolchain.to_dict()

    @property
    def current(self):
        """The criteria in play: spec criteria, and acceptance checks once the app is built."""
        return [s for s in self.criteria if s["status"] != "later"]

    @property
    def files(self):
        """Every project file: path -> {kind, role, contract, uses}."""
        return self.spec.setdefault("files", {})

    def cycle_criteria(self, cycle, level=None):
        """The criteria of one cycle (of one level: "spec" or "acceptance", when given)."""
        return [s for s in self.criteria
                if s.get("cycle") == cycle["id"] and (level is None or s.get("level", "spec") == level)]

    def outline(self):
        """The project's files by role (what the design says, not parsed code)."""
        return "\n".join(f"- {p} ({f.get('kind', 'code')}): {f.get('role', '')}" for p, f in self.files.items())

    def paths(self, *kinds):
        return [p for p, f in self.files.items() if not kinds or f.get("kind") in kinds]

    def test_names(self, file):
        return [r.name for r in self.refs() if r.file == file]

    def remove_file(self, ws, path):
        """A file of the project gone: from the disk and from the design."""
        ws.restore({path: None})
        self.files.pop(path, None)

    def new_criterion(self, prefix, title, behaviour, test, level, cycle_id, source, status, effort=2):
        """A criterion with the next id: prefix C (spec), A (acceptance), R (the start check), B (a bug)."""
        self.spec["counter"] = self.spec.get("counter", 0) + 1
        return {"id": f"{prefix}{self.spec['counter']}", "title": title, "behaviour": behaviour, "test": test,
                "effort": effort, "source": source, "status": status, "level": level, "cycle": cycle_id, "history": []}

    def criteria_of(self, path, steps):
        """The criteria (of steps) a file must meet: those whose tests call it directly, or whose text
        names it ("playlist.songs", "app.py"); a template's are those of the files that render it.
        A file no test reaches (an adapter) has none: its contract says what it does."""
        name, stem = Path(path).name.lower(), Path(path).stem.lower()
        template = self.files.get(path, {}).get("kind") == "template"  # named by its file name, not its stem
        steps = [s for s in steps if not Project.is_start_check(s)]  # the start check is no file's criterion
        direct = [s for s in steps if path in self.files.get(TestRef(s["test"]).file, {}).get("uses", [])
                  or name in s["behaviour"].lower()
                  or (not template and re.search(rf"\b{re.escape(stem)}\.\w", s["behaviour"].lower()))]
        if not template:
            return direct
        renderers = [p for p, f in self.files.items() if path in f.get("uses", []) and p != path]
        found = {s["id"]: s for s in direct + [s for p in renderers for s in self.criteria_of(p, steps)]}
        return list(found.values())

    def refs(self):
        return [TestRef(s["test"]) for s in self.current]

    def entry_files(self, paths):
        """Of these paths, the app's entry point: the file the design says a user starts, then any
        the run command names."""
        entry, run = self.spec.get("entry", ""), self.toolchain.run
        return [p for p in paths if p == entry] + [p for p in paths if p != entry and Path(p).name in run]

    def contracts(self, paths):
        out = []
        for path in paths:
            lines = self.files.get(path, {}).get("contract") or []
            if lines:
                out.append(f"{path}:\n" + "\n".join(f"  - {line}" for line in lines))
        return "\n".join(out)

    def register(self, created, kind, role, uses=()):
        for path in created:
            self.files.setdefault(path, {"kind": kind, "role": role, "contract": [], "uses": list(uses)})

    def acceptance_lines(self, cycle):
        """The cycle's acceptance checks a user makes (not the start check), one line each."""
        return "\n".join(f"- {s['behaviour']}" for s in self.cycle_criteria(cycle, "acceptance")
                         if not Project.is_start_check(s))

    def dependencies(self, read):
        """The project's third-party dependencies: its dependency files (read(path) once written,
        else what the design says they hold). Empty when it has none."""
        found = [p for p, f in self.files.items() if f.get("kind") == "config"
                 and ("depend" in f.get("role", "").lower() or Path(p).name in self.DEPENDENCY_FILES)]
        return "\n".join(f"{p}:\n{read(p) or self.files[p].get('role', '')}".strip() for p in found)

    def cycle(self, cycle_id):
        return next((c for c in self.cycles if c["id"] == cycle_id), None)

    def criterion(self, criterion_id):
        return next(s for s in self.criteria if s["id"] == criterion_id)

    def uses(self, path, depth=2):
        """The files a file uses, directly and (depth 2) one level down."""
        direct = self.files.get(path, {}).get("uses", [])
        deeper = [u2 for u in direct for u2 in self.files.get(u, {}).get("uses", [])] if depth > 1 else []
        return list(dict.fromkeys(direct + deeper))


class Tee:
    """Writes to the terminal and to a log file, like `tee`."""

    def __init__(self, stream, file):
        self.stream, self.file = stream, file

    def write(self, text):
        self.stream.write(text)
        try:
            self.file.write(text)
            self.file.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


class Knowledge:
    """What earlier projects taught, to do it right the first time. Per language: the toolchain
    that last worked and the toolchain fixes found (both replayed without asking the model), and
    lessons; for any language: lessons. Lessons are shown in the prompts of the phase they concern;
    the most confirmed first. Plain JSON: edit or delete entries by hand at any time."""

    def __init__(self, language):
        self.language = language or "Python"
        self.path = LANGUAGES_DIR / f"{self.language_slug(self.language)}.json"
        self.language_doc = JsonFile.read(self.path, None, keeps_work=True) or {"language": self.language, "projects": 0,
                                                       "toolchain": None, "fixes": [], "lessons": []}
        self.any_language_doc = JsonFile.read(MEMORY_FILE, None, keeps_work=True) or {"lessons": []}

    def summary(self):
        lessons = len(self.language_doc.get("lessons", [])) + len(self.any_language_doc.get("lessons", []))
        projects = self.language_doc.get("projects", 0)
        known = [("the toolchain that worked" + (f" ({projects} finished project(s))" if projects else ""))
                 if self.language_doc.get("toolchain") else "",
                 f"{len(self.language_doc.get('fixes', []))} toolchain fix(es)" if self.language_doc.get("fixes") else "",
                 f"{lessons} lesson(s)" if lessons else ""]
        return f"{self.language}: " + (", ".join(k for k in known if k) or "nothing learned yet")

    @staticmethod
    def language_slug(language):
        """"Python 3.12" -> python, "JavaScript (Node.js)" -> javascript, "C++" -> cpp, "C#" -> csharp."""
        base = language.split("(")[0].lower().replace("+", "p").replace("#", "sharp")
        found = re.findall(r"[a-z][a-z0-9]*", base)
        return found[0] if found else "unknown"

    def finished_project(self):
        self.language_doc["projects"] = self.language_doc.get("projects", 0) + 1
        self.save()

    def known_lessons(self):
        return self.language_doc.setdefault("lessons", []) + self.any_language_doc.setdefault("lessons", [])

    def add_lessons(self, new, confirmed):
        """Adds new lessons (one like a known lesson confirms it instead); returns how many were new."""
        known, added = self.known_lessons(), 0
        for number in confirmed:
            if isinstance(number, int) and 1 <= number <= len(known):
                self.confirm(known[number - 1])
        for item in new:
            text, phase = str(item.get("text", "")).strip()[:240], str(item.get("phase", "")).strip().lower()
            if not text or phase not in LESSON_PHASES:
                continue
            same = next((lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON), None)
            if same:
                self.confirm(same)
                continue
            lesson = {"phase": phase, "text": text, "seen": 1, "added": now_text(), "last_seen": now_text()}
            (self.language_doc if item.get("scope") == "language" else self.any_language_doc)["lessons"].append(lesson)
            known.append(lesson)
            added += 1
        for doc in (self.language_doc, self.any_language_doc):
            doc["lessons"] = self.keep(doc["lessons"], known[len(known) - added:])
        self.save()
        return added

    def fix_result(self, fix, worked):
        key = "used" if worked else "failed"
        fix[key] = fix.get(key, 0) + 1
        self.save()

    def known_fix(self, failure, tried=()):
        """A replayable fix for exactly this failure that has helped more often than not."""
        return next((f for f in self.language_doc.get("fixes", []) if f["failure"] == failure and f.get("replay")
                     and f["failure"] not in tried and f.get("failed", 0) <= f.get("used", 0)), None)

    def save(self):
        try:
            JsonFile.write(self.path, self.language_doc)
            JsonFile.write(MEMORY_FILE, self.any_language_doc)
        except OSError as exc:
            print(f"  note: could not save what was learned ({exc})")

    @staticmethod
    def confirm(lesson):
        lesson["seen"], lesson["last_seen"] = lesson.get("seen", 1) + 1, now_text()

    @staticmethod
    def keep(lessons, new):
        """At most MAX_LESSONS, the most confirmed first; the new ones always stay (the least
        confirmed older ones go to make room)."""
        rank = Knowledge.rank
        fresh = [lesson for lesson in lessons if any(lesson is n for n in new)]
        older = sorted((lesson for lesson in lessons if not any(lesson is n for n in new)), key=rank, reverse=True)
        return sorted(fresh + older[:max(0, MAX_LESSONS - len(fresh))], key=rank, reverse=True)

    def toolchain(self):
        known = Toolchain(self.language_doc.get("toolchain"))
        return known if known else None

    def remember_toolchain(self, toolchain):
        if toolchain and toolchain.to_dict() != self.language_doc.get("toolchain"):
            self.language_doc["toolchain"], self.language_doc["toolchain_saved"] = toolchain.to_dict(), now_text()
            self.save()

    def remember_fix(self, fix):
        fixes = [f for f in self.language_doc.setdefault("fixes", []) if f["failure"] != fix["failure"]]
        self.language_doc["fixes"] = (fixes + [dict(fix, used=1, failed=0, added=now_text())])[-20:]
        self.save()

    def similar_fix(self, failure):
        scored = [(similarity(f["failure"], failure), f) for f in self.language_doc.get("fixes", [])]
        best = max(scored, key=lambda pair: pair[0], default=(0, None))
        return best[1] if best[0] >= SIMILAR_FAILURE else None

    @staticmethod
    def rank(lesson):
        """The most confirmed, then the most recently seen, first (sorted in reverse)."""
        return lesson.get("seen", 1), lesson.get("last_seen", "")

    def lessons(self, phases, limit=LESSONS_SHOWN):
        pool = [lesson for lesson in self.known_lessons() if lesson.get("phase") in phases]
        pool.sort(key=Knowledge.rank, reverse=True)
        return [lesson["text"] for lesson in pool[:limit]]


class Brief:
    """A long spec file condensed into a brief that fits the spec prompt (brief.md in the
    project's .tinker, so a resumed run reads it again without asking)."""

    @staticmethod
    def load(model, state, mode, task, spec_text):
        """The brief of the project's spec file (none for a change: a feature's spec is condensed
        with its request)."""
        if mode == "change":
            return ""
        brief_file = state / "brief.md"
        if brief_file.is_file():
            return brief_file.read_text(encoding="utf-8")
        brief = Brief.make(model, task, spec_text) if spec_text else ""
        brief_file.write_text(brief, encoding="utf-8")
        return brief

    @staticmethod
    def make(model, task, spec):
        """A long spec read in prompt-sized chunks, each condensed, so it fits the spec prompt."""
        limit = max(400, model.limit_chars(Prompts.SPEC_SYSTEM) - len(task) - 300)
        if len(spec) <= limit:
            return spec.strip()
        chunks = split_text(spec, model.limit_chars(Prompts.BRIEF_SYSTEM) - len(task) - 120)
        words_each = max(60, limit // 7 // len(chunks))
        notes = [Reply(model.ask(Prompts.BRIEF_SYSTEM.format(words=words_each),
                                 [Part("Task", task, True), Part("Spec excerpt", chunk, True)],
                                 f"Brief {i}/{len(chunks)}")).text
                 for i, chunk in enumerate(chunks, 1)]
        text = "\n".join(notes)
        cut = text.rfind("\n", 0, limit)
        return text if len(text) <= limit else text[:cut if cut > 0 else limit]


class CycleRunner:
    """A cycle of the project itself, of a feature (--add) or of a bug fix (--fix): Tinker's idea in
    order (PHASES, FIX_PHASES): plan the task as a spec, install the test tool, a test for every spec
    point, just enough code to pass them, acceptance tests, one final review, done. Resumable at
    each phase: a phase is saved as done once it ends."""

    def __init__(self, session):
        self.s, self.p = session, session.project

    def show_plan(self):
        spec, accept = self.p.level_counts()
        print(f"Plan: {spec} spec criteria and {accept} acceptance checks (see SPEC.md)")
        for s in self.p.criteria:
            print(f"  {s['id']:5} [{STATUS_TEXT.get(s['status'], s['status'])}] effort {s.get('effort', 2)} "
                  f"{s['title']}  -> {s['test']}")

    def run(self, cycle, plan_only=False):
        """Continue a cycle from its saved phase: "done", "blocked" (a phase stopped it) or "planned"
        (--plan-only, once planning is done)."""
        phases = FIX_PHASES if cycle.get("kind") == "fix" else PHASES
        while cycle["phase"] != "done":
            phase = cycle["phase"]
            if plan_only and phase not in PLANNING_PHASES:
                self.show_plan()
                return "planned"
            if self.run_phase(phase, cycle) is False:
                return "blocked"
            if phase in FORMAT_AFTER:
                self.s.format_code()
            cycle["phase"] = phases[phases.index(phase) + 1]
            self.s.save()
            if phase == "review":
                self.show_plan()
        return "done"

    def run_phase(self, phase, cycle):
        """Runs one phase; only setup, verify, acceptance, the final review and reproduce can stop
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
                "acceptance": AcceptanceChecker,
                "final_review": FinalReview,
                "docs": ReadmeWriter,
                "lessons": LessonWriter,
                "reproduce": BugReproducer}[phase]


class WebBackend:
    """What a web backend gives the Internet plugin. A new one subclasses this, implements its
    three methods, and is added to Internet.backends() under the name config.json's "web" uses.
    search() and fetch() raise WebError when they cannot answer."""

    def __init__(self, timeout):
        self.timeout = timeout

    def unready(self):
        """Why it cannot be used ("" when it can), found without going online (a missing key)."""
        raise NotImplementedError

    def search(self, query, count):
        """[{"title", "url", "content"}]: up to count results for the query."""
        raise NotImplementedError

    def fetch(self, url):
        """{"title", "url", "content"}: the page's main text."""
        raise NotImplementedError


class OllamaWeb(WebBackend):
    """Ollama's web search and web fetch (ollama.com/api/web_search and /api/web_fetch), with the
    API key of a free ollama.com account (ollama.com/settings/keys) in the OLLAMA_API_KEY
    environment variable, as Ollama's own tools take it."""
    BASE = "https://ollama.com/api"
    KEY_ENV = "OLLAMA_API_KEY"
    MAX_BYTES = 2_000_000  # the most of one answer read

    def __init__(self, timeout):
        super().__init__(timeout)
        self.key = os.environ.get(self.KEY_ENV, "").strip()

    def unready(self):
        return "" if self.key else f"no Ollama API key: set {self.KEY_ENV} (a free key from ollama.com/settings/keys)"

    def search(self, query, count):
        data = self.post("/web_search", {"query": query, "max_results": count})
        if not isinstance(data.get("results"), list):
            raise WebError("ollama.com's web search answered without results")
        return data["results"]  # the plugin keeps what is usable (Internet.search)

    def fetch(self, url):
        return self.post("/web_fetch", {"url": url})

    def post(self, path, payload):
        """The JSON ollama.com answers with; WebError when it does not."""
        request = urllib.request.Request(self.BASE + path, data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {self.key}",
                                                  "Content-Type": "application/json", "User-Agent": "Tinker"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(self.MAX_BYTES)
        except urllib.error.HTTPError as exc:
            hint = {401: f": check {self.KEY_ENV}", 403: f": check {self.KEY_ENV}", 429: ": its rate limit, try later"}
            raise WebError(f"ollama.com answered HTTP {exc.code}{hint.get(exc.code, '')}",
                           network=exc.code in (401, 403, 429) or exc.code >= 500)
        except Exception as exc:  # no answer at all: timeout, refused, no network
            reason = first_line(str(getattr(exc, "reason", "") or exc) or type(exc).__name__, 160)
            raise WebError(f"ollama.com cannot be reached ({reason})", network=True)
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            raise WebError("ollama.com did not answer with JSON")
        if not isinstance(data, dict):
            raise WebError("ollama.com answered with something else than a JSON object")
        return data


class Workspace:
    """The project folder: its files (paths kept inside it) and the commands run in it."""
    REFUSED = "refused: the command is unsafe"  # the output of a command that is never run
    UNSAFE_COMMAND = re.compile(r"(^|[;&|]\s*)sudo\b|\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\.\.)|\bmkfs\b|\bdd\s+if=|"
                                r"(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b|>\s*/(etc|usr|bin|dev/sd)")
    PLACEHOLDER_RE = re.compile(r"^(<.*>|your[\w-]*|.*_here|change[-_ ]?me|replace[-_ ]?me|x{3,}|\*{3,}|todo|tbd|"
                                r"none|null|example[\w-]*|placeholder|insert[\w-]*|put[\w-]*|\.\.\.)$", re.I)

    def __init__(self, root):
        self.root = root

    def run(self, command, timeout=COMMAND_TIMEOUT, quiet=False, env=None):
        """Run one planned command in a POSIX shell in the project folder (env: extra variables),
        with the person's aliases it uses defined first (TaughtCommands). Returns (exit code, or
        None if refused or timed out; output)."""
        if self.unsafe(command):
            return None, Workspace.REFUSED
        started = time.perf_counter()
        proc = subprocess.Popen(TaughtCommands.prelude(command) + command, shell=True, cwd=self.root,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace",
                                start_new_session=(os.name != "nt"), env={**os.environ, **(env or {})})
        try:
            output, _ = proc.communicate(timeout=timeout)
            code, status = proc.returncode, f"exit code {proc.returncode}"
        except subprocess.TimeoutExpired:
            self.kill(proc)
            output, _ = proc.communicate()
            code, status = None, f"timed out after {timeout}s"
        if not quiet:
            Console.step(f"$ {command}  [{status}]", started)
        return code, (output or "").replace(str(self.root) + os.sep, "")

    def exists(self, rel_path):
        return (self.root / rel_path).is_file()

    def read(self, rel_path):
        full = self.root / rel_path
        return full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""

    def apply(self, reply, may_change, windows=None):
        """Write a reply's FILE blocks (whole files, each checked by may_change(path, exists) ->
        None or why not) and its LINES blocks (only inside the windows shown)."""
        change, windows = Change(), windows or {}
        for path, body in reply.files():
            reason = self.refusal(path, body, may_change, windows)
            if reason:
                change.notes.append(f"{path}: {reason}")
            elif path not in change.backups:
                change.backups[path] = self.read(path) if self.exists(path) else None
                self.write(path, body)
        self.apply_line_edits(reply, windows, change)
        return change

    def prepare_env(self):
        """.env from .env.example when missing; placeholder values blanked either way."""
        example, env = self.root / ENV_EXAMPLE, self.root / ENV_FILE
        if example.is_file() and not env.is_file():
            env.write_text(self.env_without_placeholders(example.read_text(encoding="utf-8", errors="replace")),
                           encoding="utf-8")
            unset = [l.split("=")[0] for l in env.read_text(encoding="utf-8").splitlines() if l.endswith("=")]
            print(f"  created {ENV_FILE} from {ENV_EXAMPLE}" + (f"; set {', '.join(unset)} in it" if unset else ""))
        elif env.is_file():
            text = env.read_text(encoding="utf-8", errors="replace")
            if self.env_without_placeholders(text).strip() != text.strip():
                env.write_text(self.env_without_placeholders(text), encoding="utf-8")
                print(f"  {ENV_FILE}: cleared placeholder values (a placeholder is not a real setting)")

    @staticmethod
    def unsafe(command):
        if Workspace.UNSAFE_COMMAND.search(command):
            print(f"  $ {command}\n    REFUSED: sudo, deleting outside the project, piping a download into a "
                  f"shell or writing to the system are never run")
            return True
        return False

    @staticmethod
    def kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL) if os.name != "nt" else proc.kill()
        except OSError:
            pass

    def refusal(self, path, body, may_change, windows):
        if path.lower() in Reply.PLACEHOLDER_PATHS:
            return "not a real file path"
        try:
            self.resolve(path, for_write=True)
        except ToolError as exc:
            return str(exc)
        if path in windows:
            return "it was shown in part: change it with a LINES block"
        if not body.strip():
            return "the reply gave it empty"
        return may_change(path, self.exists(path))

    def write(self, rel_path, content):
        full = self.resolve(rel_path, for_write=True)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content if not content or content.endswith("\n") else content + "\n", encoding="utf-8")

    def apply_line_edits(self, reply, windows, change):
        edits = {}
        for path, a, b, new in reply.line_edits():
            shown = windows.get(path)
            if not shown:
                change.notes.append(f"{path}: LINES blocks only change files shown as numbered lines")
            elif not shown[0] <= a <= b <= shown[1]:
                change.notes.append(f"{path}: lines {a}-{b} are outside the lines shown ({shown[0]}-{shown[1]})")
            elif path not in change.backups:
                edits.setdefault(path, []).append((a, b, new))
        for path, ranges in edits.items():
            old = self.read(path)
            lines, taken = old.splitlines(), []
            for a, b, new in sorted(ranges, reverse=True):
                if any(not (b < x or a > y) for x, y in taken):
                    change.notes.append(f"{path}: overlapping LINES blocks; kept the first")
                    continue
                lines[a - 1:b] = new
                taken.append((a, b))
            change.backups[path] = old
            self.write(path, "\n".join(lines) + "\n")

    @staticmethod
    def env_without_placeholders(text):
        """A .env text with placeholder values blanked: the app then sees the setting as unset."""
        out = []
        for line in text.splitlines():
            body = line.strip()
            if body and not body.startswith("#") and "=" in body:
                key, value = body.split("=", 1)
                if Workspace.is_placeholder(value.split(" #", 1)[0]):
                    line = f"{key.rstrip()}="
            out.append(line)
        return "\n".join(out) + "\n"

    def resolve(self, rel_path, for_write=False):
        if not rel_path or not isinstance(rel_path, str):
            raise ToolError("path is required")
        candidate = Path(rel_path.strip())
        if len(rel_path) > 300 or any(len(piece) > 120 for piece in candidate.parts) or \
                any(ch in rel_path for ch in "\n\r\t`<>|*?\"'"):
            raise ToolError(f"'{first_line(rel_path, 60)}' is not a usable file path")
        if candidate.is_absolute() or rel_path.strip().startswith("~"):
            raise ToolError(f"path '{rel_path}' must be relative to the project")
        full = (self.root / candidate).resolve()
        if full == self.root or self.root not in full.parents:
            raise ToolError(f"path '{rel_path}' is outside the project")
        if for_write and full.relative_to(self.root).parts[0] in (STATE_DIR, ".git"):
            raise ToolError(f"path '{rel_path}' is protected")
        return full

    def restore(self, snapshot):
        for path, text in snapshot.items():
            if text is None:
                (self.root / path).unlink(missing_ok=True)
            else:
                (self.root / path).write_text(text, encoding="utf-8")

    def lines(self, rel_path):
        return len(self.read(rel_path).splitlines())

    def revert(self, change):
        self.restore(change.backups)

    def snapshot(self, paths):
        return {p: (self.read(p) if self.exists(p) else None) for p in paths}

    @staticmethod
    def is_placeholder(value):
        """A value that only shows where a real one goes (your_api_key_here, <key>, ...)."""
        return not value or bool(Workspace.PLACEHOLDER_RE.match(value.strip().strip("'\"")))

    def start(self, command, env):
        """Start a command that keeps running (a server) in the background: (process, output
        file), or None when refused. Stop it with stop()."""
        if self.unsafe(command):
            return None
        output_file = self.root / STATE_DIR / "app-output.txt"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as out:
            proc = subprocess.Popen(TaughtCommands.prelude(command) + command, shell=True, cwd=self.root,
                                    stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"),
                                    env={**os.environ, **env})
        return proc, output_file

    def stop(self, proc):
        """Stop a started command and everything it started: politely, then for good."""
        if proc.poll() is None and os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.kill(proc)
        proc.wait()

    def read_output(self, output_file):
        text = output_file.read_text(encoding="utf-8", errors="replace") if output_file.is_file() else ""
        return text.replace(str(self.root) + os.sep, "") or "(no output)"


class SpecDocument:
    """SPEC.md: the plan as a person reads it."""

    def __init__(self, project):
        self.p = project

    def text(self):
        p = self.p
        topics = list(dict.fromkeys(item["name"] for item in p.spec.get("learned", [])))
        lines = [f"# Spec: {first_line(p.run['task'], 120)}", "",
                 f"Run {p.run['id']}. Language: {p.language or '?'}."
                 + (f" Learned from the web and used: {', '.join(topics)}." if topics else ""), "",
                 "| ID | Criterion | Test | Status |", "|---|---|---|---|"]
        lines += [f"| {s['id']} | {s['title']} | `{s['test']}` | {STATUS_TEXT.get(s['status'], s['status'])} |"
                  for s in p.criteria]
        lines += self.commands() + self.cycles() + self.design() + self.criteria()
        return "\n".join(lines) + "\n"

    def commands(self):
        tc = self.p.toolchain
        if not tc:
            return []
        out = TaughtCommands.spelled_out  # your aliases spelled out: these commands work for anyone
        lines = ["", "## Commands (run in the project folder)", ""] + [f"- setup: `{out(c)}`" for c in tc.setup]
        lines += [f"- {key}: `{out(getattr(tc, key))}`" for key in Toolchain.COMMANDS if getattr(tc, key)]
        return lines + (["- the run command serves until stopped (on the PORT setting)"] if tc.serves else [])

    def cycles(self):
        lines = []
        for cycle in self.p.cycles:
            lines += ["", f"## {'Project' if cycle['id'] == 'base' else 'Feature ' + cycle['id']}: "
                          f"{first_line(cycle['request'], 120)}", "", f"Phase: {cycle.get('phase')}", ""]
            for title, key in (("Constraints", "constraints"), ("Not wanted (non-goals)", "non_goals"),
                               ("Interface check", "review")):
                if cycle.get(key):
                    lines += [f"{title}:"] + [f"- {item}" for item in cycle[key]] + [""]
            if cycle.get("data"):
                lines += ["External services: adapters and the sample data tests pass instead:", "",
                          "```", cycle["data"], "```", ""]
        return lines

    def design(self):
        if not self.p.files:
            return []
        lines = ["", "## Design (each file's contract; tests and code are written to it)", ""]
        for path, f in self.p.files.items():
            uses = f" (uses {', '.join(f['uses'])})" if f.get("uses") else ""
            lines.append(f"- `{path}` [{f.get('kind', 'code')}]: {f.get('role', '')}{uses}")
            lines += [f"    - {c}" for c in f.get("contract", [])]
        return lines

    def criteria(self):
        lines = []
        for level, title in (("spec", "Spec criteria (tests written first; the code passes them)"),
                             ("acceptance", "Acceptance checks (a user's point of view, once the app is built)")):
            steps = [s for s in self.p.criteria if s.get("level", "spec") == level]
            lines += ["", f"## {title}", ""] + self.criteria_lines(steps) if steps else []
        return lines

    def criteria_lines(self, steps):
        lines = []
        for s in steps:
            lines += [f"### {s['id']} {s['title']}", "", s["behaviour"], ""]
            if s.get("source"):
                lines.append(f"- From the request: \"{s['source']}\"")
            lines += [f"- Test: `{s['test']}`", f"- Status: {STATUS_TEXT.get(s['status'], s['status'])}"]
            if s.get("last_error") and s["status"] != "green":
                lines.append(f"- Last error: {first_line(s['last_error'], 300)}")
            lines.append("")
        return lines


class Reply:
    """What the model answered, and the structured parts in it."""
    THINK_RE = re.compile(r"<think>.*?</think>", re.S)
    HEADER_PATH = r"[ \t:]+(?:(?:path|file)[ \t]*[=:][ \t]*)?[\"'`]?([^\s<>\"'`]+?)[\"'`]?"  # FILE x, FILE path=x, FILE "x"
    FILE_BLOCK_RE = re.compile(r"<<<FILE" + HEADER_PATH + r"[ \t]*>>>[ \t]*\n(.*?)\n?<<<END>>>", re.S)
    LINES_BLOCK_RE = re.compile(r"<<<LINES" + HEADER_PATH + r"[ \t]+(?:lines[ \t]*=[ \t]*)?(\d+)\s*-\s*(\d+)[ \t]*>>>[ \t]*\n"
                                r"(.*?)\n?<<<END>>>", re.S)
    NUMBERED_RE = re.compile(r"^\s*\d+\| ?")
    PLACEHOLDER_PATHS = {"path", "path/to/file", "file", "filename", "key"}
    PATH_LINE_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.[\w]+|[\w.-]+(?:/[\w.-]+)+")  # a relative path, alone on a line

    def __init__(self, raw):
        self.text = self.THINK_RE.sub("", raw or "").strip()

    def json(self):
        start, end = self.text.find("{"), self.text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in reply")
        candidate = self.text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return json.loads(self.strip_trailing_commas(candidate))

    @staticmethod
    def files_complete(content):
        """A reply of FILE/LINES blocks is complete when its last block has ended and what follows is
        not another block (models sometimes run on with junk after their answer)."""
        end = content.rfind("<<<END>>>")
        rest = content[end + 9:] if end >= 0 else ""
        return end >= 0 and "<<<FILE" not in rest and "<<<LINES" not in rest and len(rest.strip()) >= 300

    def files(self):
        """[(path, content)] of its FILE blocks. A header that copied the format's placeholder
        (<<<FILE path>>>) with the real path as the block's first line is read as that path."""
        found = []
        for path, body in self.FILE_BLOCK_RE.findall(self.text):
            path = path.strip()
            first, _, rest = body.partition("\n")
            if path.lower() in self.PLACEHOLDER_PATHS and self.PATH_LINE_RE.fullmatch(first.strip()):
                path, body = first.strip(), rest
            found.append((str(Path(path)), body))
        return found

    @staticmethod
    def strip_trailing_commas(text):
        """Remove commas directly before ] or } outside JSON strings."""
        out, in_string, escaped = [], False, False
        for index, char in enumerate(text):
            if in_string:
                out.append(char)
                escaped, in_string = (False, in_string) if escaped else (char == "\\", char != '"')
                continue
            if char == '"':
                in_string = True
            elif char == "," and text[index + 1:].lstrip()[:1] in ("]", "}"):
                continue
            out.append(char)
        return "".join(out)

    def spec_json(self):
        """The spec's JSON; when only its free-form "data" part (written last) is broken, that
        part is kept as text instead of asking again."""
        try:
            return self.json()
        except ValueError as first:
            start, at = self.text.find("{"), self.text.rfind('"data"')
            if start < 0 or at <= start:
                raise first
            try:
                data = json.loads(self.strip_trailing_commas(self.text[start:at].rstrip().rstrip(",") + "}"))
            except ValueError:
                raise first
            data["data"] = self.text[at:].split(":", 1)[-1].strip().rstrip("}").strip()
            return data

    def cause(self):
        match = re.search(r"^\s*CAUSE:\s*(.+)$", self.text, re.M)
        return first_line(match.group(1), 160) if match else ""

    def toolchain_line(self):
        match = re.search(r"^\s*TOOLCHAIN:\s*(\{.*\})\s*$", self.text, re.M)
        return json.loads(match.group(1)) if match else None

    def line_edits(self):
        """[(path, first, last, new lines)] of its LINES blocks."""
        edits = []
        for p, a, b, body in self.LINES_BLOCK_RE.findall(self.text):
            new = body.splitlines()
            if new and all(self.NUMBERED_RE.match(l) or not l.strip() for l in new):
                new = [self.NUMBERED_RE.sub("", l, count=1) for l in new]  # it kept the line numbers
            edits.append((str(Path(p.strip())), int(a), int(b), new))
        return edits

    @staticmethod
    def json_complete(content):
        """A JSON reply is complete once the object that opened it closes and parses."""
        start = content.find("{")
        if start < 0 or not content.rstrip().endswith("}"):
            return False
        try:
            json.loads(Reply.strip_trailing_commas(content[start:].strip()))
            return True
        except ValueError:
            return False

    def named_paths(self):
        return list(dict.fromkeys([p for p, _ in self.files()] + [e[0] for e in self.line_edits()]))


class Part:
    """A section of a prompt. Optional parts are cut or dropped to fit; required ones are always
    sent: prose ones (a request, a failure) are shortened at their less useful end, exact ones
    (a whole file) are sent as they are. An atomic optional part is sent whole or not at all."""
    SHORTEST = 150  # chars an optional part keeps at least when it is shortened to fit (else it is left out)

    def __init__(self, title, text, required=False, keep="head", exact=False, atomic=False):
        self.title, self.text, self.required, self.keep = title, (text or "").strip(), required, keep
        self.exact = bool(exact and required)
        self.atomic = bool(atomic and not required)

    @staticmethod
    def total_size(parts):
        """The chars a list of parts takes in a prompt (empty optional parts are left out)."""
        return sum(p.size() for p in parts if p.text or p.required)

    @staticmethod
    def assemble(parts, limit, notes=None):
        """The user message: required parts first fitted (shortening prose ones), then optional
        parts while they fit. Raises PromptTooLarge when the required parts cannot fit."""
        parts = Part.shorten_required(list(parts), limit, notes)
        room = limit - sum(p.size() for p in parts if p.required)  # what the optional parts may take
        blocks = []
        for p in parts:
            if not p.text and not p.required:
                continue
            if p.required:
                blocks.append(p.render())
            elif p.size() <= room:
                blocks.append(p.render())
                room -= p.size()
            elif not p.atomic and room - len(p.render("")) - 2 >= Part.SHORTEST:  # 2: the blank line before it
                blocks.append(p.shortened(room - len(p.render("")) - 2).render())
                room = 0
        return "\n\n".join(blocks)

    @staticmethod
    def shorten_required(parts, limit, notes):
        over = sum(p.size() for p in parts if p.required) - limit
        trimmed = set()
        while over > 0:
            prose = [i for i, p in enumerate(parts) if p.required and not p.exact and len(p.text) > TRIM_FLOOR]
            if not prose:
                exact = ", ".join(p.title for p in parts if p.exact) or "none"
                raise PromptTooLarge(f"required prompt parts need {limit + over} chars; budget allows {limit} "
                                     f"(parts that cannot be shortened: {exact})")
            i = max(prose, key=lambda k: len(parts[k].text))
            parts[i] = parts[i].shortened(max(TRIM_FLOOR, len(parts[i].text) - over))
            trimmed.add(parts[i].title)
            over = sum(p.size() for p in parts if p.required) - limit
        if trimmed and notes is not None:
            notes.extend(sorted(trimmed))
        return parts

    def render(self, text=None):
        text = self.text if text is None else text
        return f"## {self.title}\n{text}" if self.title else text

    def size(self):
        return len(self.render()) + 2

    def floor_size(self):
        """Its size once shortened as far as it may be."""
        return self.size() if self.exact else min(self.size(), TRIM_FLOOR + len(self.title) + 12)

    def shortened(self, room):
        marker = "[...]"
        room -= len(marker) + 1
        text = "" if room <= 0 else (self.text[:room] + "\n" + marker if self.keep == "head"
                                     else marker + "\n" + self.text[-room:])
        return Part(self.title, text, self.required, self.keep, self.exact, self.atomic)

    def relaxed(self):
        return Part(self.title, self.text, self.required, self.keep, exact=False, atomic=self.atomic)


class Stream:
    """Reads a streamed reply: collects its content, shows live progress, and stops early once
    done_when(content) says the reply is complete."""

    def __init__(self, label, done_when):
        self.label, self.done_when = label, done_when
        self.content, self.written, self.thinking, self.final = "", 0, 0, {}
        self.started = self.last_print = time.perf_counter()
        self.progressed = False
        self.answer, self.complete_at = None, None  # the reply as it was when complete, and when

    def read(self, response):
        for raw in response:
            if raw.strip() and self.take(json.loads(raw.decode("utf-8"))):
                return

    def close(self):
        if self.progressed:
            Console.clear_progress()

    @property
    def reply(self):
        return self.content if self.answer is None else self.answer

    def take(self, chunk):
        """One chunk; True when the reply is over. Once the reply is complete, Ollama's last chunk
        (with the real prompt token count, which keeps the prompt budget right) is awaited a few
        seconds more; anything the model writes meanwhile is not part of the reply."""
        if "error" in chunk:
            if self.answer is not None:
                return self.stop_early()  # the reply was complete before Ollama failed on what followed
            oom, crashed = Ollama.is_oom(str(chunk["error"])), Ollama.is_crash(str(chunk["error"]))
            raise OllamaError(f"Ollama error: {chunk['error']}", oom=oom, crashed=crashed,
                              in_reply=not (oom or crashed), partial=self.content,
                              streamed=self.written + self.thinking)
        message = chunk.get("message") or {}
        piece = message.get("content") or ""
        self.content += piece
        self.written += bool(piece)
        self.thinking += bool(message.get("thinking"))
        self.show()
        if chunk.get("done"):
            self.final = chunk if self.answer is None else dict(chunk, done_reason="complete")
            return True
        if self.answer is None and piece and self.complete(piece):
            self.answer, self.complete_at = self.content, time.perf_counter()
        if self.complete_at is not None and time.perf_counter() - self.complete_at > EARLY_STOP_GRACE:
            return self.stop_early()
        return False

    def stop_early(self):
        self.final = {"done_reason": "complete", "eval_count": self.written + self.thinking, "early_stop": True}
        return True

    def show(self):
        now = time.perf_counter()
        if self.label and now - self.last_print >= PROGRESS_EVERY:
            self.last_print, self.progressed = now, True
            status = (f"{self.written} tok written, {self.thinking} tok thinking" if self.written or self.thinking
                      else "loading model / reading prompt")
            Console.progress(f"  ... {self.label}: {status}, {now - self.started:.0f}s")

    def complete(self, piece):
        """Checked when a piece may end a block or an object (or every 40 pieces)."""
        if not self.done_when or not (">" in piece or "}" in piece or self.written % 40 == 0):
            return False
        return self.done_when(self.content)


class PromptTooLarge(Exception):
    """The required parts of a prompt do not fit the budget."""


class Worker:
    """A part of the work on a project, with what every part needs: the session (the model and
    what earlier projects taught), the project (its plan and state) and its workspace (its files
    and the commands run in it)."""

    def __init__(self, session):
        self.s, self.p, self.ws = session, session.project, session.ws


class Phase(Worker):
    """One phase of a cycle (CycleRunner): run(cycle) does it, and returns False when the cycle
    must stop there. TITLE is printed as it starts. A phase that BUILDS from the plan thinks at
    build_think (config.json); planning thinks at the model's own setting."""
    TITLE = ""
    BUILDS = False

    def run(self, cycle):
        raise NotImplementedError


class Recaller(Phase):
    """0. Recall: before planning, everything learned is searched for what the request needs, by
    words and by meaning (Retriever), so the call that follows sees the most related topics first:
    it decides which this cycle uses and, in a run with --web, which it should learn now that it has
    not (up to learn_missing in config.json): a framework the request names, a technique, the
    language. Those are learned (Learner); the chosen topics' facts, ranked by the same search, are
    kept in the project ("learned"), so they go into its prompts and a resumed run uses the same.
    Without --web it only reads what was learned before; with nothing learned and no --web there is
    nothing to decide: no call."""
    TITLE = "Recall (what Tinker has learned)"

    def run(self, cycle):
        known, internet = Library.topics(), self.s.internet
        can_learn = Config.get("learn_missing") if internet and internet.available() else 0
        if not known and not can_learn:
            print("  nothing learned yet" + ("" if internet else " (with --web, a run learns what it needs)"))
            cycle["topics"] = []
            return
        if known and not can_learn and set(known) == {Library.language(self.p.language)}:
            cycle["topics"] = []  # only the project's language is learned: used without a call
            self.use_language(cycle)
            return
        query = cycle["request"] + (f"\n{cycle['brief']}" if cycle.get("brief") else "")
        retriever = Retriever(Embeddings.configured())
        items = Library.items(known)
        order = retriever.rank(query, [Library.searched(item) for item in items], prune=True)
        ranked = list(dict.fromkeys(items[index]["topic"] for index in order))
        if known:
            print(f"  searched {len(items)} learned texts of {len(known)} topic(s) by {retriever.used}")
        use, learn = self.ask(cycle, query, known, ranked, can_learn)
        learned = [Library.slug(topic) for topic in learn[:can_learn] if self.learn(topic, internet, retriever)]
        cycle["topics"] = list(dict.fromkeys(learned + use))[:RECALL_TOPICS]  # what it just learned it needs most
        self.keep_facts(cycle, query, retriever)
        names = [(Library.read(slug) or {}).get("name", slug) for slug in cycle["topics"]]
        print(f"  using what was learned about: {', '.join(names) or 'nothing'}")
        self.use_language(cycle)
        self.s.save()

    def use_language(self, cycle):
        """The project's language as learned (--learn-language): used by every cycle of a project
        in it, not left to the search; once the spec has named the language, its facts join the
        cycle's (the spec phase calls this for a new project)."""
        slug = Library.language(self.p.language)
        if not slug or slug in cycle.get("topics", []):
            return
        cycle["topics"] = [slug] + cycle.get("topics", [])[:RECALL_TOPICS - 1]
        query = cycle["request"] + (f"\n{cycle['brief']}" if cycle.get("brief") else "")
        self.keep_facts(cycle, query, Retriever(Embeddings.configured()))
        print(f"  using the language as learned: {Library.read(slug).get('name', slug)} ({Library.path(slug).name})")
        self.s.save()

    def ask(self, cycle, query, known, ranked, can_learn):
        """(slugs of learned topics to use, topics to learn now). The topics are listed the most
        related to the request first (Retriever), as many as fit."""
        fixed = [Part("Request", query, True, keep="head"),
                 Part("Project language", self.p.language if cycle["id"] != "base" else ""),
                 Part("Learning now", f"possible: name up to {can_learn} topics to learn" if can_learn
                      else "not possible: name none", True)]
        left = self.s.room(Prompts.RECALL_SYSTEM) - 60  # a long request is shortened, the topics keep half the room
        room = max(left - Part.total_size(fixed), (left - sum(p.floor_size() for p in fixed)) // 2)
        parts = fixed + [Part("Learned topics", self.topic_listing(known, ranked, room) or "(none yet)", True, exact=True)]

        def read(reply):
            data = reply.json()
            use = [str(s).strip() for s in data.get("use", []) if str(s).strip() in known]
            learn = [" ".join(str(t).split())[:80] for t in data.get("learn", []) if str(t).strip()]
            learn = {Library.slug(t): t for t in reversed(learn) if Library.slug(t) and Library.slug(t) not in known}
            return use, list(reversed(learn.values()))  # one per topic (Flask and flask are one)

        try:
            return self.s.ask_until_usable(Prompts.RECALL_SYSTEM, parts, "Recall", read, attempts=2)
        except (SystemExit, OllamaError) as exc:  # what was learned only helps: the run goes on without it
            print(f"  {exc} Going on without what was learned.")
            return [], []

    def learn(self, topic, internet, retriever):
        """A topic learned now (True), or why not (learning is optional: the run goes on); indexed
        with the embeddings the search uses (None after they failed: then not indexed)."""
        if not internet.available():
            print(f"  not learning {topic}: {internet.why()}")
            return False
        try:
            return bool(Learner(self.s.model, internet, retriever.embeddings).learn(topic))
        except (SystemExit, PromptTooLarge, OllamaError) as exc:
            print(f"  could not learn {topic}: {exc}")
            return False

    def keep_facts(self, cycle, query, retriever):
        """The facts of the chosen topics, the most related to the request first, kept in the
        project ("learned", with earlier cycles' after them) for the prompts of every phase."""
        chosen = {slug: Library.read(slug) for slug in cycle["topics"]}
        items = [item for item in Library.items({slug: entry for slug, entry in chosen.items() if entry})
                 if item["phase"] != "summary"]
        order = retriever.rank(query, [Library.searched(item) for item in items])
        ranked = [items[index] for index in order]
        first = ("tools", "machine")  # what the toolchain is built from goes before the facts
        ranked = [item for item in ranked if item["phase"] in first] + [item for item in ranked
                                                                      if item["phase"] not in first]
        earlier = [item for item in self.p.spec.get("learned", []) if item["topic"] not in cycle["topics"]]
        self.p.spec["learned"] = ranked + earlier  # a topic chosen again: its current facts only

    @staticmethod
    def topic_listing(known, ranked, room):
        """One line per topic ("slug: name, summary"), in the order given, as many as fit room."""
        lines, used = [], 0
        for slug in ranked:
            entry = known[slug]
            line = f"- {slug}: {entry.get('name', slug)}, {first_line(str(entry.get('summary', '')), 120)}"
            if used + len(line) + 1 > room:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)


class SpecWriter(Phase):
    """1. Spec: the criteria (each one behaviour of the app's own code, with one test) and the
    acceptance checks, traced to the request, then one call that adds what none covers."""
    TITLE = "Plan: the spec"

    def run(self, cycle):
        """The spec (one call), then its coverage of the request (one call). Resumable: a step done
        is not asked again."""
        if not cycle.get("specified"):
            self.write_spec(cycle)
        if not cycle.get("coverage_done"):
            self.check_coverage(cycle)

    def write_spec(self, cycle):
        """Language, kind of app, spec criteria and acceptance checks traced to the request, the
        adapters and the sample data tests pass instead, constraints, non-goals, files."""
        system, parts = self.spec_prompt(cycle)
        data, criteria, files = self.s.ask_until_usable(system, parts, "Spec", lambda r: self.read_spec(r, cycle))
        if cycle["id"] == "base" or not self.p.language:
            self.p.spec["language"] = str(data.get("language") or "").strip() or "Python"
            Recaller(self.s).use_language(cycle)
        self.p.criteria.extend(criteria)
        cycle["files"] = [f["path"] for f in files]
        cycle["purposes"] = {f["path"]: f["purpose"] for f in files}
        cycle["non_goals"] = self.strings(data.get("non_goals"), 12)
        cycle["constraints"] = self.strings(data.get("constraints"), 8)
        cycle["data"] = data["data"] if isinstance(data.get("data"), str) else json.dumps(data.get("data", ""))
        accept = sum(1 for c in criteria if c["level"] == "acceptance")
        print(f"  language: {self.p.language}; {len(criteria) - accept} spec criteria, {accept} acceptance checks")
        print(f"  learned so far: {self.s.knowledge.summary()}")
        cycle["specified"] = True
        self.s.save()

    def check_coverage(self, cycle):
        """One call compares the request with every criterion and acceptance check: the app does
        only what they test, so what the request asks and none of them covers is added now."""
        steps = self.p.cycle_criteria(cycle)
        lines = "\n".join(f"- [{'acceptance' if s.get('level') == 'acceptance' else 'criterion'}] {s['behaviour']} "
                          f"[{s['test']}]" for s in steps)
        parts = [Part("Request", cycle["request"], True, exact=True),
                 Part("Criteria and acceptance checks", lines, True, exact=True),
                 Part("Requirements from the spec file", cycle.get("brief", ""), atomic=True),
                 Part("Files from the spec", ", ".join(cycle.get("files", [])), atomic=True),
                 Part("Language", self.p.language)]
        try:
            added = self.s.ask_until_usable(Prompts.COVERAGE_SYSTEM, parts, "Coverage",
                                            lambda r: self.read_coverage(r, cycle, steps),
                                            attempts=2)
        except SystemExit as exc:
            print(f"  {exc} Going on with the spec as it is.")
            added = []
        self.p.criteria.extend(added)
        print(f"  coverage: {len(added)} missing criteria added")
        for s in added:
            print(f"    + {s['id']} {first_line(s['behaviour'], 120)}")
        cycle["coverage_done"] = True
        self.s.save()

    def spec_prompt(self, cycle):
        if cycle["id"] == "base":
            return Prompts.SPEC_SYSTEM, [Part("Request", cycle["request"], True),
                                         Part("Requirements from the spec file", cycle.get("brief", "")),
                                         *self.s.knowledge_parts("spec")]
        return Prompts.FEATURE_SPEC_SYSTEM, [
            Part("Change requested", cycle["request"], True, keep="tail"),
            Part("Project", self.p.run["task"], True),
            Part("Language", self.p.language),
            Part("Feature requirements", cycle.get("brief", "")),
            Part("What it does now (passing criteria)",
                 "\n".join(f"- {s['behaviour']}" for s in self.p.criteria if s["status"] == "green"), keep="tail"),
            Part("Project files", self.p.outline()),
            *self.s.knowledge_parts("spec"),
        ]

    def read_spec(self, reply, cycle):
        data = reply.spec_json()
        criteria = self.criteria_from(data.get("criteria"), cycle, "spec")
        if not criteria:
            raise ValueError("no criteria listed (or none quotes the request)")
        criteria += self.criteria_from(data.get("acceptance"), cycle, "acceptance", taken=criteria)
        files = []
        for item in data.get("files", []):
            item = {"path": item} if isinstance(item, str) else item
            path = str(Path(str(item.get("path") or "").strip()))
            self.ws.resolve(path, for_write=True)
            files.append({"path": path, "purpose": str(item.get("purpose", ""))})
        return data, criteria, files

    @staticmethod
    def strings(items, limit):
        return [str(x) for x in (items or []) if str(x).strip()][:limit]

    def read_coverage(self, reply, cycle, steps):
        """The missing criteria, without any that only repeat one the spec has."""
        data = reply.json()
        added = self.criteria_from(data.get("criteria"), cycle, "spec", taken=steps)
        added += self.criteria_from(data.get("acceptance"), cycle, "acceptance", taken=steps + added)
        new = []
        for step in added:
            if any(similarity(step["behaviour"], s["behaviour"]) >= SAME_CRITERION for s in steps + new):
                print(f"  coverage: {first_line(step['behaviour'], 90)} repeats a criterion; left out")
            else:
                new.append(step)
        return new

    def criteria_from(self, items, cycle, level, taken=()):
        """Criteria of one level: "spec" (tests written first, the code is written to them) or
        "acceptance" (a user's checks, written and run once the app is built)."""
        request = cycle["request"] + "\n" + cycle.get("brief", "")
        out = []
        for item in items if isinstance(items, list) else []:
            text = str(item.get("text") or item.get("behaviour") or "").strip() if isinstance(item, dict) else ""
            if not text:
                continue
            source = str(item.get("source", "")).strip()
            if not self.traced(source, request):
                print(f"  dropped a criterion the request does not ask for: {first_line(text, 90)} "
                      f"(source: {first_line(source, 60) or 'none'})")
                continue
            acceptance = level == "acceptance"
            out.append(self.p.new_criterion("A" if acceptance else "C", text[:80], text,
                                            self.test_id(item.get("test"), text, list(taken) + out), level, cycle["id"],
                                            source, "later" if acceptance else "todo", self.effort(item.get("effort"))))
        return out

    @staticmethod
    def traced(source, request):
        """Whether a criterion's source is copied from the request: "setting" (a required setting's
        message) or at least 60% of its words of 3+ letters appear in the request."""
        if source.strip().lower().strip('"') == "setting":
            return True
        wanted = [w for w in words(source) if len(w) >= 3]
        have = set(words(request))
        return bool(wanted) and sum(w in have for w in wanted) / len(wanted) >= 0.6

    def test_id(self, given, text, siblings):
        ref = TestRef(str(given or "").strip())
        if not ref.name:
            default_file = TestRef(self.p.criteria[0]["test"]).file if self.p.criteria else "tests/test_app"
            ref = TestRef(f"{ref.file if ref.file and ref.file != str(given) else default_file}::"
                          f"test_{'_'.join(words(text)[:5])}")
        taken = {s["test"] for s in self.p.criteria + siblings}
        return str(ref) if str(ref) not in taken else f"{ref}_{len(taken) + 1}"

    @staticmethod
    def effort(value):
        try:
            return min(max(int(value), 1), 3)
        except (TypeError, ValueError):
            return 2


class Designer(Phase):
    """2. Design: the files and what uses what (one call), the toolchain (one call), then each
    code file's contract (ContractWriter)."""
    TITLE = "Plan: the design"

    def run(self, cycle):
        if not cycle.get("layout_done"):
            self.lay_out(cycle)
        if not cycle.get("toolchain_done"):
            self.plan_toolchain(cycle)
        ContractWriter(self.s).write_all(cycle)

    def lay_out(self, cycle):
        """Files (kind, role, uses) and the entry point. Mechanical problems get one more call."""
        steps = self.p.cycle_criteria(cycle)
        test_files = sorted({TestRef(s["test"]).file for s in steps})
        problems = []
        for attempt in (1, 2):
            prompt = self.layout_prompt(cycle, steps, test_files, problems)
            files, entry = self.s.ask_until_usable(Prompts.DESIGN_SYSTEM, prompt, "Design",
                                                   lambda r: self.read_layout(r, test_files))
            problems = self.layout_problems(files, entry)
            if not problems:
                break
            print("  design problems: " + "; ".join(problems))
        self.keep_layout(cycle, files, entry, test_files)
        self.drop_adapter_criteria(cycle)
        cycle["layout_done"] = True
        self.s.save()

    def plan_toolchain(self, cycle):
        """The commands to set up, check, test, run and smoke-check it; a feature keeps the
        project's. The start check (the app starts the way a user starts it) joins the
        acceptance checks."""
        if cycle["id"] == "base" or not self.p.toolchain:
            self.p.toolchain = self.ask_toolchain()
        self.add_start_check(cycle)
        cycle["toolchain_done"] = True
        print(f"  toolchain: {self.p.toolchain.summary()}")
        self.s.save()

    @staticmethod
    def order(files):
        """Config first, then each file after the files it uses; tests and docs last."""
        by_path = {f["path"]: f for f in files}
        starts = [f["path"] for kind in ("config", "template", "code") for f in files if f["kind"] == kind]
        order = after_what_they_use(starts, lambda path: by_path[path]["uses"], lambda path: path in by_path)
        return list(dict.fromkeys(order + [f["path"] for f in files if f["path"] not in order]))

    def ensure_toolchain(self):
        """A run planned before toolchains existed gets one."""
        if not self.p.toolchain:
            self.p.toolchain = self.ask_toolchain()
            self.s.save()

    def layout_prompt(self, cycle, steps, test_files, problems):
        purposes = cycle.get("purposes", {})
        spec_files = "\n".join(f"- {p}: {purposes.get(p, '')}" for p in dict.fromkeys(cycle["files"] + test_files))
        fixed = [Part("Language", self.p.language or "Python", True),
                 Part("Files from the spec", spec_files, True, exact=True)]
        if problems:
            fixed.append(Part("Problems in your previous layout (fix them)", "\n".join(problems), True, exact=True))
        room = self.s.room(Prompts.DESIGN_SYSTEM) - Part.total_size(fixed) - 60
        return fixed + [
            Part("Criteria [test file::name]", self.criteria_lines(steps, room), True, exact=True),
            Part("Request", cycle["request"], atomic=True),
            Part("External services: adapters and sample data", cycle.get("data", ""), atomic=True),
            Part("Constraints", "\n".join(f"- {c}" for c in cycle.get("constraints", [])), atomic=True),
            Part("Existing files", self.p.outline() if cycle["id"] != "base" else "", atomic=True),
            *self.s.knowledge_parts("design"),
        ]

    def read_layout(self, reply, test_files):
        data = reply.json()
        files = {}
        for item in data.get("files", []):
            if isinstance(item, dict):
                entry = self.layout_file(item, test_files)
                files[entry["path"]] = entry  # a path given twice: the last entry counts
        if not files:
            raise ValueError("no files in the layout")
        entry = str(Path(str(data.get("entry") or "").strip())) if data.get("entry") else ""
        return list(files.values()), ("" if entry == "." else entry)

    def layout_problems(self, files, entry):
        """What only another layout call can fix. A file "used" that is not in the layout is
        simply dropped from "uses" (not worth a call)."""
        paths = {f["path"] for f in files} | set(self.p.files)
        problems = []
        for f in files:
            unknown = [u for u in f["uses"] if u not in paths]
            if unknown:
                f["uses"] = [u for u in f["uses"] if u in paths]
                print(f"  design: {f['path']} uses {', '.join(unknown)}, not in the design; ignored")
            if f["kind"] not in KINDS:
                problems.append(f"{f['path']}: kind must be one of {', '.join(KINDS)}")
        if entry and entry not in paths:
            problems.append(f"entry {entry} is not one of the files")
        return problems + self.unrendered_templates(files, entry)

    def keep_layout(self, cycle, files, entry, test_files):
        files = [f for f in self.complete_layout(cycle, files, test_files) if f["path"] != ENV_FILE]
        for f in files:
            known = self.p.files.get(f["path"], {})
            self.p.files[f["path"]] = {"kind": f["kind"], "role": f["role"] or known.get("role", ""),
                                       "contract": f["contract"] or known.get("contract", []),
                                       "uses": list(dict.fromkeys(known.get("uses", []) + f["uses"]))}
        if entry and entry not in self.p.files:
            print(f"  design: the entry point {entry} is not one of the files; none is kept")
            entry = ""
        if entry or not self.p.spec.get("entry"):
            self.p.spec["entry"] = entry
        cycle["files"] = self.order(files)
        print(f"  design: {len(files)} files, in this order: {', '.join(cycle['files'])}"
              + (f"; a user starts {entry}" if entry else ""))

    def drop_adapter_criteria(self, cycle):
        """An adapter to an external service has no criteria: one about it is dropped (the user
        tries the service by hand; everything else is tested with sample data)."""
        adapters = [p for p, f in self.p.files.items() if f.get("kind") == "code"
                    and f.get("role", "").strip().lower().startswith("adapter")]
        for step in list(self.p.cycle_criteria(cycle)):
            subject = step["behaviour"].lstrip("`'\" ").lower()
            if any(subject.startswith((Path(a).stem.lower() + ".", Path(a).name.lower())) for a in adapters):
                self.p.criteria.remove(step)
                print(f"  dropped {step['id']}: it tests an adapter to an external service "
                      f"({first_line(step['behaviour'], 80)})")

    def ask_toolchain(self):
        learned, current = self.s.knowledge.toolchain(), self.p.toolchain
        entry = self.p.spec.get("entry", "")
        parts = [Part("Language", self.p.language or "Python", True),
                 Part("Entry point (the file a user starts)", entry, bool(entry)),
                 Part("Project files", self.p.outline(), True, exact=True),
                 Part("Test files", "\n".join(sorted({TestRef(s["test"]).file for s in self.p.criteria
                                                      if not Project.is_start_check(s)})), True),
                 Part("Toolchain in use (keep what works)", json.dumps(current.to_dict()) if current else "", atomic=True),
                 Part(f"Toolchain that worked for {self.p.language} before (reuse it; adapt only project names)",
                      json.dumps(learned.to_dict()) if learned and not current else "", atomic=True),
                 Part("Constraints", "\n".join(f"- {c}" for cycle in self.p.cycles for c in cycle.get("constraints", [])),
                      atomic=True),
                 *self.s.knowledge_parts("toolchain")]
        return self.s.ask_until_usable(Prompts.TOOLCHAIN_SYSTEM, parts, "Toolchain", self.read_toolchain)

    def add_start_check(self, cycle):
        """One acceptance check that no test file can make: the run command starts the app."""
        if not self.p.toolchain.run or any(Project.is_start_check(s) for s in self.p.criteria):
            return
        start_check = self.p.new_criterion(
            "R", "The app starts the way a user starts it",
            "The run command starts the app the way a user does, without errors; a server keeps serving on the PORT "
            "setting and the smoke command gets its answer", RUN_TEST, "acceptance", cycle["id"], "setting", "later")
        first = next((i for i, s in enumerate(self.p.criteria) if s.get("level") == "acceptance"), len(self.p.criteria))
        self.p.criteria.insert(first, start_check)

    @staticmethod
    def criteria_lines(steps, room):
        """Every criterion, whole when they fit. The layout only decides the files and what uses
        what; when the criteria do not fit, each line is cut alike (the contracts and the review
        see every criterion whole)."""
        lines = [f"- {s['behaviour']} [{s['test']}]" for s in steps]
        if sum(len(line) + 1 for line in lines) <= room or not lines:
            return "\n".join(lines)
        each = max(60, room // len(lines) - 1)
        print(f"  design: {len(lines)} criteria do not fit the layout prompt whole; each shown up to {each} chars "
              f"(contracts and the review see them whole)")
        return "\n".join(first_line(line, each) for line in lines)

    def layout_file(self, item, test_files):
        path = str(Path(str(item.get("path", "")).strip()))
        self.ws.resolve(path, for_write=True)
        contract = item.get("contract", [])
        kind = "test" if path in test_files else (str(item.get("kind") or "").strip().lower() or "code")
        return {"path": path, "kind": kind, "role": str(item.get("role", "")),
                "contract": [str(x) for x in contract] if isinstance(contract, list) else [str(contract)],
                "uses": [str(Path(u)) for u in item.get("uses", []) if isinstance(u, str)]}

    @staticmethod
    def unrendered_templates(files, entry):
        """A template no file uses goes to the code file whose role names it, else to the entry
        point; only when there is neither is it a problem."""
        used = {u for f in files for u in f["uses"]}
        code = [f for f in files if f["kind"] == "code"]
        problems = []
        for template in [f for f in files if f["kind"] == "template" and f["path"] not in used]:
            name = Path(template["path"]).name
            named_by = next((f for f in code if name in f["role"]), None)
            owner = named_by or next((f for f in code if f["path"] == entry), None)
            if owner:
                owner["uses"].append(template["path"])
                print(f"  design: {template['path']} is rendered by {owner['path']}")
            else:
                problems.append(f"no file uses {template['path']}: the file that renders it must list it in \"uses\"")
        return problems

    def complete_layout(self, cycle, files, test_files):
        """The spec's files stay, whatever the layout left out; a new project gets a README."""
        paths = {f["path"] for f in files}
        for path in cycle["files"] + test_files:
            if path not in paths and path not in self.p.files:
                kind = "test" if path in test_files else ("config" if Path(path).name == ENV_EXAMPLE else "code")
                files.append({"path": path, "kind": kind, "role": cycle.get("purposes", {}).get(path, ""),
                              "contract": [], "uses": []})
        if cycle["id"] == "base" and not any(f["kind"] == "docs" for f in files):
            files.append({"path": "README.md", "kind": "docs", "role": "how to set up, configure, run and test it",
                          "contract": [], "uses": []})
        return files

    @staticmethod
    def read_toolchain(reply):
        data = reply.json()
        toolchain = Toolchain(data.get("toolchain") if isinstance(data.get("toolchain"), dict) else data)
        for command in toolchain.dropped:
            print(f"  toolchain: left out `{command}`: Tinker makes {ENV_FILE} itself, from {ENV_EXAMPLE}")
        problems = toolchain.problems()
        if problems:
            raise ValueError("; ".join(problems))
        return toolchain


class InterfaceReviewer(Phase):
    """3. Review: each file that uses other code files, against their contracts. Contracts are
    written callee first, so a file should already match the files it uses; what does not is
    corrected now, before tests or code exist. A problem named without a correction gets that
    file's contract written again; files that changed are checked once more. One call per such
    file, more when the contracts it uses do not fit one prompt whole."""
    TITLE = "Plan: review (each file against the contracts it uses)"

    def run(self, cycle):
        planned = [p for p in cycle["files"] if self.p.files.get(p, {}).get("kind") == "code"]
        problems, changed = self.check_round(cycle, planned, again=False)
        if changed:
            again = [p for p in planned if p in changed or set(self.callees(p)) & changed]
            problems += self.check_round(cycle, again, again=True)[0]
        cycle["files"] = Designer.order([dict(self.p.files[p], path=p) for p in cycle["files"] if p in self.p.files])
        cycle["review"] = [f"{p['file']}: {p['problem']}" if p["file"] else p["problem"] for p in problems]
        corrected = f"; {len(changed)} file(s) corrected" if changed else ""
        print(f"  interface check: {len(problems)} problem(s) found{corrected}")
        for line in cycle["review"][:10]:
            print(f"    - {first_line(line, 160)}")
        self.s.save()

    def check_round(self, cycle, paths, again):
        """(problems, the files whose contracts changed)."""
        calls = [(path, chunk) for path in paths for chunk in self.chunks(path)]
        found, changed = [], set()
        for i, (path, callees) in enumerate(calls, 1):
            label = f"Interface check {'again ' if again else ''}{i}/{len(calls)} ({path})"
            problems, corrections = self.check_file(cycle, path, callees, label)
            found += problems
            changed |= self.apply_corrections(cycle, corrections)
        return found, changed | self.rewrite_unresolved(cycle, found, changed)

    def callees(self, path):
        """The code files it uses (not itself)."""
        uses = self.p.files[path].get("uses", [])
        return [u for u in uses if u != path and self.p.files.get(u, {}).get("kind") == "code"]

    def chunks(self, path):
        """The files it uses, in groups whose contracts fit one prompt next to its own entry."""
        room = self.s.room(Prompts.INTERFACE_SYSTEM) - len(self.design_entry(path)) - 120
        return groups_fitting(self.callees(path), room, size=lambda callee: len(self.design_entry(callee)))

    def check_file(self, cycle, path, callees, label):
        parts = [Part("The file to check", self.design_entry(path), True, exact=True),
                 Part("The files it uses", "\n".join(self.design_entry(c) for c in callees), True, exact=True),
                 Part("External services: adapters and sample data", cycle.get("data", ""), atomic=True),
                 Part("Language", self.p.language)]
        try:
            return self.s.ask_until_usable(Prompts.INTERFACE_SYSTEM, parts, label, self.read, attempts=2)
        except SystemExit as exc:
            print(f"  {exc} Going on with this part of the plan as it is.")
            return [], []

    def apply_corrections(self, cycle, corrections):
        """The corrected entries go into the design; returns the files that changed."""
        changed = set()
        for item in corrections:
            entry = {k: item[k] for k in ("kind", "role", "contract", "uses")}
            if self.p.files.get(item["path"]) != entry:
                changed.add(item["path"])
            self.p.files[item["path"]] = entry
            if item["path"] not in cycle["files"] and item["kind"] != "docs":
                cycle["files"].append(item["path"])  # a file the check adds is built with the others
        return changed

    def rewrite_unresolved(self, cycle, problems, corrected):
        """Problems named in a file without a correction: that file's contract is written again."""
        unresolved = {}
        for problem in problems:
            if problem["file"] not in corrected and self.p.files.get(problem["file"], {}).get("kind") == "code":
                unresolved.setdefault(problem["file"], []).append(problem["problem"])
        writer, changed = ContractWriter(self.s), set()
        for path, items in unresolved.items():
            print(f"  {path}: {len(items)} problem(s) named without a correction; writing its contract again")
            if writer.write(cycle, path, problems=items):
                changed.add(path)
        return changed

    def design_entry(self, path):
        f = self.p.files[path]
        return (f"- {path} [{f['kind']}] {f['role']}" + (f" (uses {', '.join(f['uses'])})" if f["uses"] else "")
                + "".join(f"\n    - {c}" for c in f["contract"]))

    def read(self, reply):
        data = reply.json()
        problems = []
        for item in data.get("problems") or []:
            if isinstance(item, dict) and str(item.get("problem", "")).strip():
                file = str(item.get("file") or "").strip()
                problems.append({"file": str(Path(file)) if file else "", "problem": str(item["problem"]).strip()})
            elif isinstance(item, str) and item.strip():
                problems.append({"file": "", "problem": item.strip()})
        corrections = [self.corrected_file(item) for item in data.get("files") or [] if isinstance(item, dict)]
        return problems[:20], [c for c in corrections if c]

    def corrected_file(self, item):
        """A file entry from the check, keeping what it does not restate; "uses" of unknown files dropped."""
        path = str(Path(str(item.get("path", "")).strip()))
        self.ws.resolve(path, for_write=True)
        known = self.p.files.get(path, {})
        contract = item.get("contract")
        contract = [str(x) for x in contract] if isinstance(contract, list) and contract else known.get("contract", [])
        uses = [str(Path(u)) for u in item.get("uses", known.get("uses", [])) if isinstance(u, str)]
        kind = str(item.get("kind") or known.get("kind") or "code").strip().lower()
        return {"path": path, "kind": kind if kind in KINDS else known.get("kind", "code"),
                "role": str(item.get("role") or known.get("role", "")), "contract": contract,
                "uses": [u for u in uses if u in self.p.files or u == path]}


class TestToolSetup(Phase):
    """4. Setup, before any test is written: the project's config files (its dependencies and its
    test tool's settings) are written, set up, and the test tool is shown to run (TestTool), so
    the tests are written for a test tool that is there and works, in any language."""
    TITLE = "Setup (the test tool, before any test is written)"
    BUILDS = True

    def run(self, cycle):
        Designer(self.s).ensure_toolchain()
        CodeWriter(self.s).write_configs_first(cycle)
        return TestTool(self.s).works()


class TestWriter(Phase):
    """5. Spec tests, before the code (and the acceptance tests, once the app is built): a few
    criteria per call."""
    TITLE = "Spec tests (one for every spec point, before any code)"
    BUILDS = True

    def run(self, cycle, only=None, level="spec"):
        """A few criteria per call: the spec tests before the code, the acceptance tests once the
        app is built. A test file that already holds tests is never sent back to be rewritten (it may
        be too big, and rewriting risks its tests): the next tests go into a new file next to it, and
        their criteria follow them there."""
        steps = [s for s in (only or self.p.cycle_criteria(cycle, level)) if not TestRef(s["test"]).present(self.ws)]
        by_file = {}
        for step in steps:
            by_file.setdefault(TestRef(step["test"]).file, []).append(step)
        for path, group in by_file.items():
            for start in range(0, len(group), TESTS_PER_CALL):
                self.write_batch(cycle, path, group[start:start + TESTS_PER_CALL])
        self.s.save()

    def write_batch(self, cycle, path, batch):
        note = ""
        for _ in (1, 2):
            todo = [s for s in batch if not TestRef(s["test"]).present(self.ws)]
            if not todo:
                return
            current = self.ws.read(path)
            kept = [n for n in self.p.test_names(path) if current and TestRef.name_at(current, n) >= 0]
            acceptance = todo[0].get("level") == "acceptance"
            try:
                reply = self.s.ask(Prompts.ACCEPTANCE_TESTS_SYSTEM if acceptance else Prompts.SPEC_TESTS_SYSTEM,
                                   self.prompt(cycle, path, todo, current, note, acceptance),
                                   f"{'Acceptance tests' if acceptance else 'Tests'} {'next to ' if current.strip() else ''}"
                                   f"{path} ({len(todo)})")
            except PromptTooLarge as exc:
                print(f"  tests for {path} skipped: {exc}; verify writes them")
                return
            change = self.ws.apply(reply, self.may_write(path))
            self.keep_existing_tests(path, kept, change)
            self.follow_new_files(path, todo, change)
            note = self.report_and_note(path, todo, change)

    def prompt(self, cycle, path, todo, current, note, acceptance):
        where = (f"a NEW test file next to {path} (do not change {path}; name it by the language's test "
                 f"conventions)" if current.strip() else path)
        uses = self.p.files.get(path, {}).get("uses") or self.p.paths("code", "template")
        parts = [Part(f"Tests to write in {where} (name: criterion)",
                      "\n".join(f"- {TestRef(s['test']).name}: {s['behaviour']}" for s in todo), True),
                 Part("Test command", self.s.toolchain.test_one, True),
                 Part("Note", note),
                 Part("External services: adapters and sample data (tests pass the sample data)", cycle.get("data", ""))]
        if acceptance:  # the app exists: its entry point, as written, is better than its contract
            entry = self.p.entry_files(self.s.files_on_disk("code"))
            parts += [Part(f"File {p} (the entry point)", self.ws.read(p), atomic=True) for p in entry[:1]]
        return parts + [
                Part("Contracts of the files these tests use", self.p.contracts(uses)),
                Part(f"Start of {path} (its imports and shared fakes; it stays as it is)",
                     "\n".join(current.splitlines()[:30])),
                Part("Run command (how a user starts it)", self.s.toolchain.run if acceptance else ""),
                Part("Project files", self.p.outline()),
                *self.s.knowledge_parts("tests")]

    @staticmethod
    def may_write(path):
        folder = str(Path(path).parent)

        def check(p, exists):
            if p == path or (not exists and str(Path(p).parent) == folder):
                return None  # the test file (kept only if its tests survive), or a new one next to it
            return f"only new test files next to {path} can be written now"
        return check

    def keep_existing_tests(self, path, kept, change):
        """A rewrite of an existing test file stays only if every test it had is still there."""
        if path in change.backups and kept:
            lost = [n for n in kept if TestRef.name_at(self.ws.read(path), n) < 0]
            if lost:
                self.ws.restore({path: change.backups.pop(path)})
                change.notes.append(f"{path} lost its tests {', '.join(lost)}; write the new tests in a new file")

    def follow_new_files(self, path, todo, change):
        """Tests written into a new file: their criteria point there; the file joins the project."""
        for step in todo:
            ref = TestRef(step["test"])
            home = next((p for p in change.created if TestRef.name_at(self.ws.read(p), ref.name) >= 0), None)
            if home and not ref.present(self.ws):
                step["test"] = f"{home}::{ref.name}"
        self.p.register(change.created, "test", f"tests (with {path})", self.p.files.get(path, {}).get("uses", []))

    def report_and_note(self, path, todo, change):
        for note in change.notes:
            print(f"  skipped {note}")
        missing = [TestRef(s["test"]).name for s in todo if not TestRef(s["test"]).present(self.ws)]
        homes = sorted({TestRef(s["test"]).file for s in todo if TestRef(s["test"]).present(self.ws)})
        for step in todo:
            if TestRef(step["test"]).name in missing:
                step["write_failures"] = step.get("write_failures", 0) + 1
                step["last_error"] = ("its test could not be written: " + "; ".join(change.notes))[:400]
        print(f"  {', '.join(homes) or path}: wrote {len(todo) - len(missing)}/{len(todo)} tests"
              + (f"; missing {', '.join(missing)}" if missing else ""))
        return (f"These tests were not written: {', '.join(missing)}. " + "; ".join(change.notes))[:600] if missing else ""


class CodeWriter(Phase):
    """6. Code: just enough code to pass the tests: one call per file in design order (the
    config files were written by the setup phase; any still missing first, in one call); then
    setup, and a check that each file uses the files its contract says it uses."""
    TITLE = "Code (just enough to pass the tests)"
    BUILDS = True

    def run(self, cycle):
        self.write_all(cycle)
        ok, output = self.s.setup()
        if not ok:
            print(f"  setup fails: {Output.key_line(output)} (verify fixes the toolchain first)")
        self.check_wiring(cycle)

    def write_configs_first(self, cycle):
        """The setup phase: the cycle's new config files (dependencies, the test tool's settings)
        in one call, before any test or code is written."""
        configs = [p for p in cycle["files"] if p not in cycle.get("code_done", []) and p != ENV_FILE
                   and self.p.files.get(p, {}).get("kind") == "config" and not self.ws.exists(p)]
        if configs:
            self.write_configs(cycle, configs)
        cycle.setdefault("code_done", []).extend(configs)
        self.s.save()

    def write_all(self, cycle):
        """The new config files in one call, then one call per other file (not tests, not docs), in
        design order. .env is made by Tinker from .env.example, never written by the model."""
        todo = [p for p in cycle["files"] if p not in cycle.get("code_done", []) and p != ENV_FILE
                and self.p.files.get(p, {}).get("kind") not in ("test", "docs")]
        configs = [p for p in todo if self.p.files.get(p, {}).get("kind") == "config" and not self.ws.exists(p)]
        if configs:
            self.write_configs(cycle, configs)
        for path in todo:
            if path not in configs:
                self.write_file(cycle, path)
            cycle.setdefault("code_done", []).append(path)
            self.s.save()

    def check_wiring(self, cycle):
        """A code file must refer to each file its contract says it uses (by name or stem). One
        change call per file that does not."""
        for path in cycle["files"]:
            if self.p.files.get(path, {}).get("kind") != "code" or not self.ws.exists(path):
                continue
            problems = self.wiring_problems(path)
            if problems:
                self.rewire(path, problems)

    def write_configs(self, cycle, paths):
        """Small files (dependencies, settings, tool settings): one call for all of them."""
        listing = "\n".join(f"- {p}: {self.p.files[p].get('role', '')}" + "".join(
            f"\n    - {c}" for c in self.p.files[p].get("contract", [])) for p in paths)
        reply = self.s.ask(Prompts.CONFIG_SYSTEM, [
            Part("Files to write", listing, True),
            Part("Language and toolchain", f"{self.p.language}\n{json.dumps(self.s.toolchain.to_dict())}", True),
            Part("Constraints", "\n".join(f"- {c}" for c in cycle.get("constraints", []))),
            Part("Project files", self.p.outline()),
            *self.s.knowledge_parts("config", "toolchain"),
        ], f"Write {len(paths)} config files")
        change = self.ws.apply(reply, lambda p, exists: None if p in paths else "only the config files listed now")
        missing = [p for p in paths if p not in change.backups]
        print(f"  wrote {', '.join(change.files) or 'none'}" + (f"; missing {', '.join(missing)}" if missing else ""))
        for path in paths:
            if path not in change.backups:  # one more try, on its own
                self.write_file(cycle, path)

    def write_file(self, cycle, path):
        info, exists = self.p.files.get(path, {}), self.ws.exists(path)
        criteria = self.criteria_for(path, cycle)
        note = ""
        for attempt in (1, 2):
            view = FileView(self.ws, self.fixed_parts(path, info, criteria, note), [path] if exists else [],
                            self.context_parts(cycle, info, criteria), hints="\n".join(s["behaviour"] for s in criteria),
                            windows=1)
            try:
                reply = self.s.ask(Prompts.CHANGE_SYSTEM if exists else Prompts.CODE_SYSTEM, view,
                                   f"{'Change' if exists else 'Write'} {path}")
            except PromptTooLarge as exc:
                print(f"  {path}: skipped, the prompt does not fit ({exc}); verify works on it")
                return
            change = self.ws.apply(self.addressed_to(reply, path, exists), self.may_write(path), view.windows)
            for text in change.notes:
                print(f"  skipped {text}")
            if path not in change.backups:
                note = ("Your reply could not be used: " + "; ".join(change.notes))[:600] if change.notes else \
                    f"Your reply had no usable <<<FILE {path}>>> block."
                continue
            if attempt == 1 and info.get("kind") == "code" and self.too_long(path):
                note = (f"Your {path} had {self.ws.lines(path)} lines; keep it under {MAX_FILE_LINES} by moving a "
                        f"part into a new small module (give that file too).")
                self.ws.revert(change)
                continue
            self.p.register([p for p in change.created if p != path], "code", f"module split from {path}")
            others = [p for p in change.files if p != path]
            print(f"  {'changed' if exists else 'wrote'} {path} ({self.ws.lines(path)} lines)"
                  + (f"; also {', '.join(others)}" if others else ""))
            return

    def wiring_problems(self, path):
        text = self.ws.read(path)
        return [f"it must use {u} (refer to it by name), as its contract says" for u in self.p.files[path].get("uses", [])
                if self.p.files.get(u, {}).get("kind") in ("code", "template")
                and Path(u).name not in text and Path(u).stem not in text]

    def rewire(self, path, problems):
        print(f"  {path} is not wired as designed: {'; '.join(problems)}")
        info = self.p.files[path]
        view = FileView(self.ws, [Part("Problems", "\n".join(problems), True),
                                  Part(f"Contract of {path}", self.p.contracts([path]), True)], [path],
                        [Part("Contracts of the files it uses", self.p.contracts(info.get("uses", [])))],
                        hints=" ".join(info.get("uses", [])), windows=1)
        reply = self.s.ask(Prompts.CHANGE_SYSTEM, view, f"Wire {path}")
        change = self.ws.apply(reply, lambda p, exists: None if p == path else f"only {path} changes now", view.windows)
        print(f"  rewired {path}" if change else f"  {path}: no usable change")

    def criteria_for(self, path, cycle):
        """The criteria this file must meet (the same rule its contract was planned with)."""
        return self.p.criteria_of(path, self.p.cycle_criteria(cycle))

    def fixed_parts(self, path, info, criteria, note):
        renderers = [p for p, f in self.p.files.items() if path in f.get("uses", []) and f.get("kind") == "code"]
        template = info.get("kind") == "template"
        parts = [Part("Language", self.p.language, True),
                 Part("File", f"{path} ({info.get('kind', 'code')})\nRole: {info.get('role', '')}", True),
                 Part("Its contract", self.p.contracts([path]), bool(info.get("contract"))),
                 Part("Contracts of the files that render it (what it is given and must show)",
                      self.p.contracts(renderers) if template else "", template and bool(renderers)),
                 Part("Criteria it serves", "\n".join(f"- {s['behaviour']}" for s in criteria)
                      or "none directly: its contract says what it does", True),
                 Part("How a user starts it (it is the entry point)", self.start_text(), True)
                 if path == self.p.spec.get("entry") else Part("How a user starts it", "")]
        return parts + ([Part("Note", note, True)] if note else [])

    def context_parts(self, cycle, info, criteria):
        deps = self.p.dependencies(lambda p: self.ws.read(p) if self.ws.exists(p) else "")
        parts = [Part("Contracts of the files it uses", self.p.contracts(info.get("uses", []))),
                 Part(Prompts.DEPENDENCIES_TITLE, deps if info.get("kind") == "code" else "", atomic=True),
                 Part("External services: adapters and sample data", cycle.get("data", "")),
                 Part("What users check once it is built (it must hold for these too)", self.user_checks(cycle)
                      if info.get("kind") in ("code", "template") else "")]
        if info.get("kind") in ("code", "template"):
            names = [TestRef(s["test"]).name for s in self.p.criteria]
            parts += [Part(f"Test {i} ({s['test']})", TestRef(s["test"]).excerpt(self.ws, names), atomic=True)
                      for i, s in enumerate(criteria[:5], 1)]
        settings = (f"read from {ENV_FILE} (defaults in {ENV_EXAMPLE}); environment variables override it. Read it "
                    f"with a listed dependency, or with a few lines of standard-library code when none reads it")
        return parts + [Part("Settings", settings if info.get("kind") == "code" else ""),
                        Part("Other files", self.p.outline()),
                        Part("Not wanted (non-goals)", "\n".join(f"- {g}" for g in cycle.get("non_goals", []))),
                        *self.s.knowledge_parts("config" if info.get("kind") == "config" else "code")]

    @staticmethod
    def addressed_to(reply, path, exists):
        """A new file goes where the design says, whatever path the reply names."""
        blocks = reply.files()
        if not exists and len(blocks) == 1 and blocks[0][0] != path:
            return Reply(f"<<<FILE {path}>>>\n{blocks[0][1]}\n<<<END>>>")
        return reply

    def may_write(self, path):
        """The file itself, or a new module it splits off; a planned file is written in its own
        call, from its own contract."""
        def check(p, exists):
            if p == path or (not exists and p not in self.p.files):
                return None
            return f"only {path} (and new modules split off it) can be written now"
        return check

    def too_long(self, path):
        """A code file well past MAX_FILE_LINES (half again): it is asked for again, in smaller modules."""
        lines = self.ws.lines(path)
        if lines > MAX_FILE_LINES * 1.5:
            print(f"  {path}: {lines} lines; asking for smaller modules")
            return True
        return False

    def start_text(self):
        """For the entry point: the run command, and that a server keeps serving on PORT."""
        tc = self.s.toolchain
        return f"{tc.run}" + (f"\nRun this way it serves until stopped, on the port in the PORT setting "
                              f"(environment, else {ENV_FILE})" if tc.serves else "")

    def user_checks(self, cycle):
        """The acceptance checks, written after the build: values they name (a mood, a message)
        must be in the code from the start, not added by a fix later."""
        return self.p.acceptance_lines(cycle)


class Verifier(Phase):
    """6. Verify: fix the code until each criterion's test passes. When fixes stop making
    progress, one review against the plan decides whether the test or the code is wrong; then the
    criterion is parked. A change that breaks a passing test is reverted. Criteria failing the same
    way as one already parked are parked at once; parked ones get one more try if others passed
    since."""
    TITLE = "Verify (until every test passes)"
    BUILDS = True
    RANK = {"missing": -1, "build": 0, "fails": 1, "passes": 2}

    def __init__(self, session):
        super().__init__(session)
        self.parked = {}  # failure signature -> the criterion parked with it

    def run(self, cycle):
        """True when every criterion in play passes (the project's spec criteria, and once the
        app is built its acceptance checks), False when some are parked."""
        Designer(self.s).ensure_toolchain()
        if not TestTool(self.s).works() or not self.make_it_build():
            return False
        self.mark_passing()
        green_at_start = self.p.counts()[0]
        self.work_through([s for s in self.p.current if s["status"] in ("todo", "active")], retry=False)
        parked = [s for s in self.p.current if s["status"] == "blocked"]
        if parked and self.p.counts()[0] > green_at_start:
            print(f"\nRetrying {len(parked)} parked criteria, now that others pass")
            self.parked = {}
            self.work_through(parked, retry=True)
        elif parked:
            print(f"  {len(parked)} criteria parked and nothing else passes yet: a retry would see the same project")
        green, _, total = self.p.counts()
        return green == total

    def make_it_build(self):
        """The project builds and loads (its check command passes) before any criterion is worked
        on: a missing dependency, an import of something that does not exist or a syntax error is
        the project's problem, not one criterion's. Each failure gets a fix that sees the error,
        the files it names and the dependency files; then setup and the check run again."""
        tried = []
        for attempt in range(BUILD_TRIES + 1):
            ok, output = self.s.setup()
            if ok:
                ok, output = self.s.toolchain.run_check(self.ws)
            if ok:
                print("  the project builds now" if attempt else "  the project builds")
                return True
            print(f"  the project does not build: {Output.key_line(output)}")
            if attempt == BUILD_TRIES:
                break
            tried.append(BuildFix(self.s, output, tried).run())
            self.s.all_tests_run = None
        self.s.build_problem = f"the project does not build after {len(tried)} fixes: {Output.key_line(output)}"
        return False

    def mark_passing(self):
        green = self.passing()
        for s in self.p.current:
            s["status"] = "green" if s["id"] in green else ("blocked" if s["status"] == "blocked" else "todo")
        self.s.save()

    def work_through(self, steps, retry):
        for step in steps:
            if step["status"] == "green":
                continue
            passed = self.work(step, retry)
            step["status"] = "green" if passed else "blocked"
            if passed:
                self.mark_others_passing()
            self.s.save()
            green, _, total = self.p.counts()
            print(f"  {step['id']} {'passes' if passed else 'is parked'} ({green}/{total} pass)")

    def passing(self):
        """The criteria whose tests pass now: one run of all tests; one per test only when some fail."""
        started = time.perf_counter()
        code, _ = self.s.run_all_tests()
        present = [s for s in self.p.current if TestRef(s["test"]).present(self.ws)]
        tests = [s for s in present if not Project.is_start_check(s)]
        if code == 0:
            result = {s["id"] for s in tests}
        elif not self.s.toolchain.run_check(self.ws)[0]:
            return set()  # it does not build: no test can pass, and it cannot start
        else:
            result = {s["id"] for s in tests if self.evaluate(s, checked=True)[0] == "passes"}
        result |= {s["id"] for s in present if Project.is_start_check(s) and self.evaluate(s, checked=True)[0] == "passes"}
        Console.step(f"tests: {len(result)}/{len(self.p.current)} criteria pass", started)
        return result

    def work(self, step, retry):
        green, _, total = self.p.counts()
        print(f"\n{step['id']} ({green}/{total} pass): {step['title']}\n  test: {step['test']}")
        step["status"] = "active"
        state, output = self.evaluate(step)
        if state == "missing":
            state, output = self.write_missing_test(step)
            if state in ("missing", "passes"):
                return state == "passes"
        snapshot = self.ws.snapshot(self.p.files)  # with its test: rolling back keeps the test just written
        same = self.failure_key(step, output)
        if same in self.parked:
            step["last_error"] = f"same failure as {self.parked[same]}"
            print(f"  fails the same way as {self.parked[same]}, which nothing fixed; parked without new attempts")
            return False
        if self.climb(step, state, output, retry):
            return True
        self.roll_back(snapshot)
        self.parked.setdefault(same, step["id"])
        print(f"  nothing helped; {step['id']} is back to how it was")
        return False

    def mark_others_passing(self):
        """A fix may have made other criteria pass too."""
        for other in self.p.current:
            if other["status"] != "green" and self.evaluate(other)[0] == "passes":
                other["status"] = "green"

    def evaluate(self, step, checked=False):
        """(state, output): missing / build (the check fails) / fails / passes."""
        ref = TestRef(step["test"])
        if not ref.present(self.ws):
            return "missing", f"the test {ref} does not exist"
        if not checked:
            ok, output = self.s.toolchain.run_check(self.ws)
            if not ok:
                return "build", output
        if ref.is_start_check:
            return self.s.toolchain.run_app(self.ws)
        return self.s.toolchain.run_test(self.ws, ref)

    def write_missing_test(self, step):
        """Its test is written now, unless writing it already failed twice (asking the same again
        would fail the same way)."""
        if step.get("write_failures", 0) >= 2:
            print(f"  its test could not be written in {step['write_failures']} tries; parked")
            return "missing", step.get("last_error") or f"the test {step['test']} could not be written"
        print("  its test is missing; writing it")
        TestWriter(self.s).run(self.p.cycle(step.get("cycle") or "base") or {}, only=[step])
        state, output = self.evaluate(step)
        if state == "missing":
            step["last_error"] = output
        return state, output

    @staticmethod
    def failure_key(step, output):
        """The failure without the test's own name: criteria failing alike share it."""
        ref = TestRef(step["test"])
        unnamed = "\0"  # a test without a name: nothing in the output is replaced for it
        return Output.signature(output.replace(str(ref), "#").replace(ref.name or unnamed, "#").replace(ref.file, "#"))

    def climb(self, step, state, output, retry):
        """fix (until --max-stall attempts bring no progress), then review against the plan."""
        before = {s["id"] for s in self.p.criteria if s["status"] == "green"}
        ladder = (("fix", 1), ("review", 1)) if retry else (("fix", self.s.max_stall), ("review", REVIEW_TRIES))
        seen, best, total = {Output.signature(output)}, self.RANK[state], 0
        for approach, patience in ladder:
            stall = 0
            while stall < patience and total < MAX_TRIES_PER_PASS:
                total += 1
                attempt = FixAttempt(self.s, self, step, approach, output, before)
                state, output = attempt.run()
                if state == "passes":
                    return True
                rank = self.RANK[state]
                progressed = rank > best or (rank == best and Output.signature(output) not in seen)
                best, stall = max(best, rank), (0 if progressed else stall + 1)
                seen.add(Output.signature(output))
                step["last_error"] = Output.key_line(output)
                if attempt.blamed_test:
                    print("  the fix points at the test, not the code: reviewing both against the plan now")
                    break
                if attempt.conflict and approach == "fix":
                    print("  fixes keep breaking the same other test: the tests may disagree; reviewing them "
                          "against the plan now")
                    break
        return False

    def roll_back(self, snapshot):
        """The project as it was: its files, and its design (files created since are dropped)."""
        for path in list(self.p.files):
            if path not in snapshot:  # created since: it goes again
                (self.ws.root / path).unlink(missing_ok=True)
                del self.p.files[path]
        self.ws.restore(snapshot)


class AcceptanceChecker(Phase):
    """8. Acceptance: the app is built and its spec tests pass. The acceptance checks get their
    tests (a user's point of view, through the entry point), and they are made to pass too."""
    TITLE = "Acceptance (a user's checks, now the app is built)"
    BUILDS = True

    def run(self, cycle):
        checks = self.p.cycle_criteria(cycle, "acceptance")
        if not checks:
            print("  no acceptance checks planned")
            return None
        for check in checks:
            check["status"] = "todo" if check["status"] == "later" else check["status"]
        TestWriter(self.s).run(cycle, level="acceptance")
        return Verifier(self.s).run(cycle)


class FinalReview(Phase):
    """9. Final review, once, when the app is built and every test passes: the project as a
    person gets it, with no model call. It is set up afresh from its own files, the whole test
    suite runs with the test command SPEC.md and the README give (the way a person runs it, not
    one test at a time), and every spec point's test is there and passes. A tooling problem (the
    tests cannot find the app's code when the test tool runs alone, a dependency the project's
    files do not install) gets one toolchain fix; what still fails is said plainly and the cycle is
    not done."""
    TITLE = "Final review (the project as a person runs it)"
    BUILDS = True

    def run(self, cycle):
        problem, output = self.problem()
        if problem and ToolchainRepair(self.s).run(output, set()):
            problem, output = self.problem()
        if problem:
            self.s.review_problem = problem
            print(f"  not done: {problem}")
            return False
        green, _, total = self.p.counts()
        print(f"  set up afresh; `{self.s.toolchain.test_all}` passes; {green}/{total} spec points tested and passing")
        return True

    def problem(self):
        """(what is wrong, its output), or ("", "") when the project passes the review."""
        ok, output = self.s.setup(force=True)
        if not ok:
            return f"setup fails from the project's own files: {Output.key_line(output)}", output
        self.s.all_tests_run = None
        code, output = self.s.run_all_tests()
        if code != 0:
            return f"`{self.s.toolchain.test_all}` fails when the whole suite runs: {Output.key_line(output)}", output
        if Toolchain.NO_TESTS_RE.search(output):
            return f"`{self.s.toolchain.test_all}` runs no test", output
        untested = [s["id"] for s in self.p.criteria if not Project.is_start_check(s)
                    and (not TestRef(s["test"]).present(self.ws) or s["status"] != "green")]
        if untested:
            return f"spec points without a passing test: {', '.join(untested)}", output
        return "", ""


class ReadmeWriter(Phase):
    """10. README: setup, settings, run and test, from the toolchain (no title of its own: the
    file it writes is named as it starts)."""
    BUILDS = True

    def run(self, cycle):
        readme = next((p for p, f in self.p.files.items() if f.get("kind") == "docs"), "README.md")
        if self.ws.exists(readme):
            return
        tc = self.s.toolchain
        out = TaughtCommands.spelled_out  # your aliases spelled out: the README works for anyone
        facts = [f"Setup: {' && '.join(map(out, tc.setup)) or 'none'}", f"Run: {out(tc.run)}",
                 f"Tests: {out(tc.test_all)}"] + ([f"Format: {out(tc.format)}"] if tc.format else [])
        reply = self.s.ask(Prompts.README_SYSTEM, [Part("Request", self.p.run["task"], True),
                                                   Part("Commands", "\n".join(facts), True),
                                                   Part(ENV_EXAMPLE, self.ws.read(ENV_EXAMPLE)),
                                                   Part("Files", self.p.outline())], f"Write {readme}")
        reply = Reply(reply.text.replace("<<<FILE README.md>>>", f"<<<FILE {readme}>>>"))
        if self.ws.apply(reply, lambda p, exists: None if p == readme else "only the README now"):
            print(f"  wrote {readme}")


class LessonWriter(Phase):
    """11. Lessons: what this cycle's problems teach the next projects."""
    TITLE = "Lessons (for the next projects)"

    def run(self, cycle):
        """One call turns the problems this cycle solved into lessons (none: no call). A finished
        project counts for its language's toolchain."""
        events = self.p.spec.get("events", [])
        if cycle["id"] == "base" and not cycle.get("counted"):
            self.s.knowledge.finished_project()
            cycle["counted"] = True  # a resumed lessons phase does not count it again
            self.s.save()
        if not events:
            print("  no problems to learn from")
            return
        known = self.s.knowledge.known_lessons()
        try:
            new, confirmed = self.s.ask_until_usable(Prompts.LESSONS_SYSTEM, [
                Part("Language", self.p.language, True),
                Part("Problems and how they were solved", "\n".join(f"- [{e['kind']}] {e['text']}" for e in events),
                     True, keep="tail"),
                Part("Known lessons", "\n".join(f"{i}. {lesson['text']}" for i, lesson in enumerate(known, 1)))],
                "Lessons", self.read, attempts=2)
        except (SystemExit, OllamaError) as exc:  # the project is built: lessons are a bonus
            print(f"  {exc} Nothing learned this time.")
            return
        added = self.s.knowledge.add_lessons(new, confirmed)
        print(f"  {added} new lesson(s), {len(confirmed)} confirmed: {self.s.knowledge.summary()}")
        self.p.spec["events"] = []
        self.s.save()

    @staticmethod
    def read(reply):
        data = reply.json()
        lessons = [item for item in data.get("lessons", []) if isinstance(item, dict)]
        confirmed = [n for n in data.get("confirmed", []) if isinstance(n, int)]
        return lessons[:3], confirmed


class BugReproducer(Phase):
    """--fix "bug": a test that fails now because of the bug (the criterion the fix must meet), in
    a new test file the model names as the project's test tool expects. The fix itself is the usual
    verify loop: the code changes until that test passes and every other test still does. A test
    that passes on the current code, breaks the build or is not run by the test command does not
    show the bug: it is asked once more with why, then the cycle stops and asks for a clearer
    report."""
    TITLE = "Reproduce the bug (a test that fails now)"

    def run(self, cycle):
        Designer(self.s).ensure_toolchain()
        if not TestTool(self.s).works() or not Verifier(self.s).make_it_build():
            return False
        note = ""
        for _ in range(2):
            step, state, output = self.write_test(cycle, note)
            if step and state == "fails" and not Toolchain.NO_TESTS_RE.search(output):
                print(f"  reproduced: {first_line(step['behaviour'], 140)}\n  it fails now: {Output.key_line(output)}")
                self.s.save()
                return True
            note = self.why_not(step, state, output)
            print(f"  not reproduced: {note}")
        self.drop(cycle)
        self.s.unreproduced = (f"the bug could not be reproduced with a test, so nothing was changed. Describe what "
                               f"you do, what happens and what should happen instead, then: tinker.py --fix "
                               f"{self.p.run['id']} \"...\"")
        return False

    def write_test(self, cycle, note):
        """(step, state, output): the test the model wrote, evaluated; (None, "missing", why) when
        its reply names no usable test."""
        reply = self.s.ask(Prompts.REPRODUCE_SYSTEM, self.prompt(cycle, note), f"Reproduce bug {cycle['id']}")
        named = re.search(r"^\s*TEST:\s*(.+?)\s*$", reply.text, re.M)
        if not named:
            return None, "missing", 'the reply has no line "TEST: <test file>::<test name>"'
        ref = TestRef(named.group(1).strip("`'\" "))
        problem = self.unusable(cycle, ref)
        if problem:
            return None, "missing", problem
        step = self.step_for(cycle, ref)
        change = self.ws.apply(reply, lambda path, exists: None if path == ref.file else "only the bug's test file")
        for line in change.notes:
            print(f"  skipped {line}")
        expected = re.search(r"^\s*EXPECTED:\s*(.+)$", reply.text, re.M)
        if expected:
            step["behaviour"] = first_line(expected.group(1), 300)
        return (step, *Verifier(self.s).evaluate(step))

    def why_not(self, step, state, output):
        """What to tell the model when its test does not show the bug."""
        if step is None:
            return output
        test = step["test"]
        if state == "passes":
            return (f"Your test {test} passed on the code as it is, so it does not show the bug. Check what the "
                    f"report says happens, and test exactly that.")
        if state == "build":
            return f"Your test {test} breaks the build (the test is wrong, not the code): {Output.key_line(output)}"
        if state == "fails":
            return (f"The test command ran no test for {test}: name the file and the test as the project's test "
                    f"tool expects. {Output.key_line(output)}")
        return f"Your reply had no test {test}: {Output.key_line(output)}"

    def drop(self, cycle):
        """A report no test could show: its test and criterion go, and the cycle closes (nothing
        was changed in the code), so the next --fix or --add can start."""
        for step in self.p.cycle_criteria(cycle):
            self.p.remove_file(self.ws, TestRef(step["test"]).file)
            self.p.criteria.remove(step)
        cycle["phase"], cycle["outcome"] = "done", "not reproduced"
        self.s.save()

    def prompt(self, cycle, note):
        code = self.s.files_on_disk("code", "template")
        named = Output.mentioned_in(cycle["request"], code)
        entry = self.p.entry_files(code)
        tests = self.s.files_on_disk("test")
        return [Part("Bug report", cycle["request"], True),
                Part("The project's test files (the new one goes next to them)", "\n".join(tests) or "none yet", True),
                Part("Note", note, bool(note)),
                Part("Test command", self.s.toolchain.test_one, True),
                Part("Contracts of the code", self.p.contracts([p for p in code if self.p.files[p].get("contract")])),
                *[Part(f"File {p}", self.ws.read(p), atomic=True) for p in dict.fromkeys(named + entry)][:2],
                Part(f"Start of {tests[0]} (how the project's tests are written)",
                     "\n".join(self.ws.read(tests[0]).splitlines()[:30]) if tests else ""),
                Part("External services: adapters and sample data",
                     next((c.get("data", "") for c in self.p.cycles if c["id"] == "base"), "")),
                Part("Project files", self.p.outline()),
                *self.s.knowledge_parts("tests")]

    def unusable(self, cycle, ref):
        """Why a named test cannot be the bug's ("" when it can): it needs a file and a name, in the
        project, in a new file (one the project has would be replaced whole)."""
        if not ref.file or not ref.name:
            return f"TEST: {ref} names no file and test (<test file>::<test name>)"
        try:
            self.ws.resolve(ref.file, for_write=True)
        except ToolError as exc:
            return f"TEST: {ref.file}: {exc}"
        mine = {TestRef(s["test"]).file for s in self.p.cycle_criteria(cycle)}
        if (ref.file in self.p.files or self.ws.exists(ref.file)) and ref.file not in mine:
            return f"TEST: {ref.file} is a file the project has; the bug's test goes in a new file next to its tests"
        return ""

    def step_for(self, cycle, ref):
        """The bug's criterion, with the test the model named (one made earlier for this bug keeps
        its id; its earlier test file goes)."""
        existing = self.p.cycle_criteria(cycle)
        if existing:
            step = existing[0]
            old = TestRef(step["test"]).file
            if old != ref.file:
                self.p.remove_file(self.ws, old)
            step["test"] = str(ref)
        else:
            step = self.p.new_criterion("B", first_line(cycle["request"], 80), cycle["request"], str(ref), "spec",
                                        cycle["id"], "bug report", "todo")
            self.p.criteria.append(step)
        self.p.register([ref.file], "test", f"reproduces bug {cycle['id']}", self.p.paths("code"))
        return step


class Retriever:
    """Hybrid search over what was learned, so nothing relevant is missed: a word search (BM25:
    exact terms such as names and versions count) and, with an embedding model (Embeddings), a
    search by meaning (cosine similarity: "web framework" finds Flask). The two rankings are fused
    (reciprocal rank fusion): what either finds ranks high, what both find ranks highest, and what
    neither finds comes last, never dropped."""
    RRF_K = 60     # the usual constant of reciprocal rank fusion: rank 1 counts 1/61, rank 10 1/70
    BM25_K1, BM25_B = 1.5, 0.75

    def __init__(self, embeddings):
        self.embeddings = embeddings  # None: the word search alone
        self.used = "words"
        self.queries = {}  # query -> its embedding

    def rank(self, query, texts, prune=False):
        """The indexes of texts, the most related to query first (all of them)."""
        if not texts:
            return []
        fused = {}
        for ranking in (self.by_words(query, texts), self.by_meaning(query, texts, prune)):
            for position, index in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1.0 / (self.RRF_K + position + 1)
        found = sorted(fused, key=lambda index: (-fused[index], index))
        return found + [index for index in range(len(texts)) if index not in fused]

    def by_words(self, query, texts):
        """BM25: the texts holding the query's words, the best match first (the others not at all)."""
        terms, docs = set(words(query)), [words(text) for text in texts]
        average = sum(len(doc) for doc in docs) / len(docs) or 1
        holding = collections.Counter(term for doc in docs for term in set(doc) & terms)
        scores = []
        for index, doc in enumerate(docs):
            counts, score = collections.Counter(doc), 0.0
            for term in terms & counts.keys():
                idf = math.log(1 + (len(docs) - holding[term] + 0.5) / (holding[term] + 0.5))
                tf, length = counts[term], len(doc) / average
                score += idf * tf * (self.BM25_K1 + 1) / (tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * length))
            if score > 0:
                scores.append((score, index))
        return [index for _, index in sorted(scores, key=lambda pair: (-pair[0], pair[1]))]

    def by_meaning(self, query, texts, prune):
        """Every text by the cosine similarity of its embedding to the query's, the closest first
        (nothing without an embedding model, or when it fails: the word search then stands alone)."""
        if self.embeddings is None:
            return []
        vectors = self.embeddings.vectors(texts, prune=prune)
        if vectors and query not in self.queries:
            asked = self.embeddings.vectors([query], keep=False)
            self.queries[query] = asked[0] if asked else None
        if not vectors or not self.queries.get(query):
            print(f"  meaning search not used: {self.embeddings.failed}; the word search stands alone")
            self.embeddings = None  # not asked again in this run
            return []
        self.used = f"words and meaning ({self.embeddings.name})"
        similarity_of = [Embeddings.cosine(self.queries[query], vector) for vector in vectors]
        return sorted(range(len(texts)), key=lambda index: (-similarity_of[index], index))


class ContractWriter(Worker):
    """A code file's contract: the exact names, signatures, data shapes and strings its criteria's
    tests check and the files using it rely on, and for each template it renders, what it gives it
    and what the page must show. Written after the code it uses (whose contracts it sees whole).
    Criteria that do not fit one prompt are given over several calls, each extending the contract
    so far."""

    def write_all(self, cycle):
        done = cycle.setdefault("contracts_done", [])
        for path in self.order(self.p.files, cycle["files"]):
            if path not in done:
                self.write(cycle, path)
                done.append(path)
                self.s.save()

    @staticmethod
    def order(files, paths):
        """The code files' contracts, each after the code it imports or calls (whose contracts it
        then sees exactly). A template has none: the file rendering it says what it is given and
        what the page must show."""
        is_code = lambda path: files.get(path, {}).get("kind") == "code"
        uses = lambda path: [used for used in files[path].get("uses", []) if is_code(used)]
        wanted = set(paths)
        return [path for path in after_what_they_use(paths, uses, is_code) if path in wanted]

    def write(self, cycle, path, problems=()):
        """Writes the file's contract (problems: what the review found in it). Returns whether it changed."""
        info = self.p.files[path]
        before = list(info.get("contract", []))
        criteria = [f"- {s['behaviour']} [{s['test']}]" for s in self.p.criteria_of(path, self.p.cycle_criteria(cycle))]
        so_far = before if (problems or (cycle["id"] != "base" and self.ws.exists(path))) else []
        calls = 0
        while calls == 0 or criteria:  # at least one call; more while criteria are left over
            calls += 1
            parts, criteria = self.prompt(cycle, path, info, criteria, so_far, problems)
            label = f"Contract of {path}" + (f" (part {calls})" if calls > 1 or criteria else "")
            try:
                so_far, uses = self.s.ask_until_usable(Prompts.CONTRACT_SYSTEM, parts, label, self.read, attempts=2)
            except SystemExit as exc:
                print(f"  {exc} Its contract stays as it is.")
                return False
            info["uses"] = list(dict.fromkeys(info.get("uses", []) + [u for u in uses if u in self.p.files and u != path]))
        info["contract"] = so_far
        print(f"  {path}: {len(so_far)} contract line(s)" + (f", from {calls} calls" if calls > 1 else ""))
        return info["contract"] != before

    def prompt(self, cycle, path, info, criteria, so_far, problems):
        """(parts, the criteria left for the next call): the fixed parts whole, then as many
        criteria as fit."""
        base = self.base_parts(path, info, so_far, problems)
        room = self.s.room(Prompts.CONTRACT_SYSTEM) - Part.total_size(base) - 60
        if room < 200:
            print(f"  {path}: what its contract must use is larger than one prompt; it is shortened to fit")
            base = [Part(p.title, p.text, p.required, exact=False) for p in base]
        taken = groups_fitting(criteria, max(room, 200))[0] if criteria else []
        given = "\n".join(taken) or "none directly: it serves the files that use it"
        callers = [f"- {p}: {f.get('role', '')}" for p, f in self.p.files.items() if path in f.get("uses", [])]
        checks = self.p.acceptance_lines(cycle)
        return base + [Part("Criteria its tests check", given, True, exact=True),
                       Part("Files that use it", "\n".join(callers), atomic=True),
                       Part("What users check once it is built (names and values they expect belong in it)", checks,
                            atomic=True),
                       Part("External services: adapters and sample data", cycle.get("data", ""), atomic=True),
                       Part("Request", cycle["request"], atomic=True),
                       *self.s.knowledge_parts("design")], criteria[len(taken):]

    @staticmethod
    def read(reply):
        data = reply.json()
        contract = [str(x).strip() for x in data.get("contract", []) if str(x).strip()]
        if not contract:
            raise ValueError("no contract lines")
        return contract, [str(Path(u)) for u in data.get("uses", []) if isinstance(u, str)]

    def base_parts(self, path, info, so_far, problems):
        uses = [u for u in info.get("uses", []) if self.p.files.get(u, {}).get("kind") == "code"]
        templates = [u for u in info.get("uses", []) if self.p.files.get(u, {}).get("kind") == "template"]
        given = Part("Contracts of the files it uses (use them exactly)", self.p.contracts(uses), bool(uses), exact=True)
        deps = self.p.dependencies(lambda p: self.ws.read(p) if self.ws.exists(p) else "")
        return [Part("Language", self.p.language or "Python", True),
                Part(Prompts.DEPENDENCIES_TITLE, deps, atomic=True),
                Part("File", f"{path} ({info.get('kind', 'code')})\nRole: {info.get('role', '')}"
                     + (f"\nTemplates it renders: {', '.join(templates)}" if templates else ""), True, exact=True),
                given,
                Part("Its contract so far (keep what still holds; add what these criteria need)", "\n".join(so_far),
                     bool(so_far), exact=True),
                Part("Problems the plan review found in it (fix them)", "\n".join(f"- {p}" for p in problems),
                     bool(problems), exact=True)]


class TestTool(Worker):
    """Whether the project's test tool works, the way the toolchain runs it: setup runs, and the
    test command reaches the tests (before any test exists: it runs and finds none). A tooling
    problem gets a toolchain fix (config files and commands only; a fix learned earlier first), at
    most twice: no code change can fix it. A toolchain that works is remembered for the language."""

    def works(self):
        problem, repair, tried = "", None, set()
        for attempt in range(3):
            stage, output = self.check(attempt)
            if stage == "ok":
                self.remember_what_worked(repair)
                return True
            if repair and repair[0] == "known":
                self.s.knowledge.fix_result(repair[1], worked=False)
            problem = Output.key_line(output)
            print(f"  {'setup fails' if stage == 'setup' else 'the tests cannot run'}: {problem}")
            repair = ToolchainRepair(self.s).run(output, tried) if attempt < 2 else None
            if repair is None:
                break
        self.s.tool_problem = f"the project's tools do not work: {problem}"
        return False

    def check(self, attempt):
        """("ok" | "setup" | "tests", output): setup runs, and the test command reaches the tests
        written so far; with none yet, it runs and finds none (exits 0 or says it ran no test)."""
        ok, output = self.s.setup(force=attempt > 0)
        if not ok:
            return "setup", output
        self.s.all_tests_run = None if attempt else self.s.all_tests_run
        code, output = self.s.run_all_tests()
        present = [r for r in self.p.refs() if r.present(self.ws) and not r.is_start_check]
        reached = (not Toolchain.reached_no_test(code, output, present) if present
                   else code == 0 or (code not in (None, 126, 127) and bool(Toolchain.NO_TESTS_RE.search(output))))
        if not reached:
            print(f"  $ {self.s.toolchain.test_all}  [{'ran no test' if present else 'did not run'}: a tooling problem]")
            return "tests", output
        if not present:
            print(f"  the test tool runs (no tests yet): $ {self.s.toolchain.test_all}")
        return "ok", output

    def remember_what_worked(self, repair):
        knowledge = self.s.knowledge
        knowledge.remember_toolchain(self.s.toolchain)
        if repair and repair[0] == "known":
            knowledge.fix_result(repair[1], worked=True)
        elif repair:
            knowledge.remember_fix(repair[1])
            self.s.note("toolchain", repair[1]["cause"])


class TestRef:
    """A criterion's test: "tests/test_app.py::test_x" -> file tests/test_app.py, name test_x."""

    def __init__(self, test):
        file, _, name = str(test).rpartition("::")
        self.file, self.name = (file, name) if file else (str(test), "")

    def __str__(self):
        return f"{self.file}::{self.name}"

    def present(self, ws):
        if self.is_start_check:
            return True  # the run command itself: always there
        return ws.exists(self.file) and (not self.name or self.name_at(ws.read(self.file), self.name) >= 0)

    @property
    def is_start_check(self):
        return str(self) == RUN_TEST

    @staticmethod
    def name_at(text, name):
        match = re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) if name else None
        return match.start() if match else -1

    def excerpt(self, ws, names):
        """The test's own lines (from its name to the next known test of the file), after the
        file's opening lines (imports, shared setup)."""
        text = ws.read(self.file)
        start = self.name_at(text, self.name)
        if start < 0:
            return text
        others = sorted(p for p in (self.name_at(text, n) for n in names if n != self.name) if p >= 0)
        line_start = self.definition_start(text, start)
        later = [p for p in others if p > start]
        end = text.rfind("\n", 0, later[0]) + 1 if later else len(text)
        first = text.rfind("\n", 0, min([start] + others)) + 1
        if first >= line_start:
            return text[:end].rstrip()  # the first test: the file's opening lines come with it
        head = text[:first].splitlines()[:15]
        while head and (not head[-1].strip() or head[-1].strip().startswith(("@", "#[", "["))):
            head.pop()  # blank lines and the first test's decorators
        return ("\n".join(head) + "\n...\n" if head else "") + text[line_start:end].rstrip()

    def command(self, template):
        return template.replace("{file}", self.file).replace("{name}", self.name).replace("{id}", str(self))

    @staticmethod
    def definition_start(text, at):
        """Where a test's definition begins: its line, or a decorator line or two above it."""
        line_start = text.rfind("\n", 0, at) + 1
        for _ in range(2):
            prev = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
            if line_start and text[prev:line_start].strip().startswith(("@", "#[", "[")):
                line_start = prev
        return line_start


class Output:
    """What a command printed, read for what went wrong (language-neutral: lines that look like
    errors, not parsed)."""
    ERROR_LINE_RE = re.compile(r"error|fail|assert|exception|expected|panic|undefined|cannot|not found|"
                               r"no module|missing", re.I)
    EXACT_ERROR_RE = re.compile(r"\b\w*(Error|Exception|Panic)\b|\bassert", re.I)
    NOISE_RE = re.compile(r"0x[0-9a-f]+|\d+(\.\d+)?\s*(ms|s|sec|seconds)\b|\b\d+\b", re.I)

    @staticmethod
    def key_line(output, limit=200):
        """The line that says what went wrong: the last error or exception line, else the first
        failure-looking line, else the last line (banners such as === FAILURES === skipped)."""
        lines = [l.strip(" #") for l in output.splitlines() if l.strip(" #") and not Output.is_banner(l.strip(" #"))]
        exact = [l for l in lines if Output.EXACT_ERROR_RE.search(l)]
        if exact:
            return first_line(exact[-1], limit)
        loose = [l for l in lines if Output.ERROR_LINE_RE.search(l)]
        return first_line((loose[:1] or lines[-1:] or ["no output"])[0], limit)

    @staticmethod
    def is_banner(line):
        return sum(c in "=-_*~#+ " for c in line) >= len(line) / 2

    @staticmethod
    def tool_failure(output, workdir):
        """A tooling failure as text that recurs across projects: its key line without the project's
        path, timings, addresses and counts."""
        return Output.NOISE_RE.sub("#", Output.key_line(output.replace(str(workdir), ".")))

    @staticmethod
    def failure_text(output, limit=OUTPUT_CHARS):
        """The end of a command's output, where runners print failures and their summary."""
        text = "\n".join(line for line in Output.compact_lines(output.strip()).splitlines() if line.strip())
        return text if len(text) <= limit else "[...]\n" + text[-(limit - 6):]

    @staticmethod
    def mentioned_in(output, paths):
        """Project files an output refers to, most mentioned first."""
        counts = []
        for path in paths:
            n = output.count(path) or len(re.findall(rf"(?<![\w./-]){re.escape(Path(path).name)}(?![\w-])", output))
            if n:
                counts.append((-n, path))
        return [p for _, p in sorted(counts)]

    @staticmethod
    def compact_lines(text, limit=360):
        """Very long lines (whole pages in assertion messages) shortened to their two ends."""
        return "\n".join(line if len(line) <= limit else
                         f"{line[:limit * 2 // 3]} ...[{len(line) - limit} chars]... {line[-limit // 3:]}"
                         for line in text.splitlines())

    @staticmethod
    def signature(output):
        """What identifies a failure, without timings, addresses and counts."""
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        errors = [l for l in lines if Output.ERROR_LINE_RE.search(l)]
        key = "\n".join((errors or lines)[-4:])
        return hashlib.sha1(Output.NOISE_RE.sub("#", key).encode()).hexdigest()[:12]


class ToolchainRepair(Worker):
    """Makes the project's tools work (setup, the test command): a fix learned on an earlier
    project for exactly this failure first (no model call), else one model call that may change
    only config files and the commands."""

    def run(self, output, tried):
        """Returns ("known" or "model", the fix), or None when nothing changed."""
        failure = Output.tool_failure(output, self.ws.root)
        known = self.s.knowledge.known_fix(failure, tried)
        if known:
            tried.add(failure)
            print(f"  applying a toolchain fix learned on an earlier project ({known['cause']})")
            self.replay(known["replay"])
            return "known", known
        fix = self.ask_model(output, failure)
        return ("model", fix) if fix else None

    def replay(self, replay):
        """A learned fix: its commands, and the config files it created (an existing file is kept)."""
        if replay.get("toolchain"):
            self.p.toolchain = self.s.toolchain.merged(replay["toolchain"])
        for path, content in (replay.get("files") or {}).items():
            if not self.ws.exists(path):
                self.ws.write(path, content)
                self.p.register([path], "config", "added by a learned toolchain fix")
        self.s.save()

    def ask_model(self, output, failure):
        """One call that may change only config files and the commands. Returns the fix (for
        knowledge: replayable when it only changed commands or created files), or None."""
        configs = [p for p in self.s.files_on_disk("config") if p != ENV_FILE]
        similar = self.s.knowledge.similar_fix(failure)
        before = self.s.toolchain.to_dict()
        reply = self.s.ask(Prompts.TOOLCHAIN_FIX_SYSTEM, [
            Part("Output", Output.failure_text(output), True, keep="tail"),
            Part("Toolchain", json.dumps(before), True),
            Part("Language", self.p.language),
            Part("A similar failure on an earlier project was fixed by", similar["cause"] if similar else ""),
            *[Part(f"File {p}", self.ws.read(p), atomic=True) for p in configs],
            *self.s.knowledge_parts("toolchain", "config"),
        ], "Fix the toolchain", max_reply=self.s.model.fix_max, unthinking_retry=False)
        try:
            given = reply.toolchain_line()
        except ValueError:
            given = None
        if given:
            self.p.toolchain = self.s.toolchain.merged(given)
        change = self.ws.apply(reply, lambda p, exists: None if p in configs or not exists
                               else "only config files change now")
        self.p.register(change.created, "config", "added by the toolchain fix")
        for note in change.notes:
            print(f"  skipped {note}")
        commands = {k: v for k, v in self.s.toolchain.to_dict().items() if v != before.get(k)}
        changed = (["toolchain"] if commands else []) + change.files
        print(f"  toolchain fix: {', '.join(changed) or 'no usable change'}  ({reply.cause() or '?'})")
        self.s.save()
        if not changed:
            return None
        replayable = set(change.files) == set(change.created)
        files = {p: self.ws.read(p) for p in change.created}
        return {"failure": failure, "cause": reply.cause() or "?",
                "replay": {"toolchain": commands, "files": files} if replayable else None}


class Tries:
    """What changes between the tries of one request: the think setting and the reply budget, the
    prompt shrunk after running out of memory, and the stalls, crashes and reply errors so far."""

    def __init__(self, think, budget):
        self.think, self.budget = think, budget
        self.shrink = self.stalls = self.crashes = self.reply_errors = 0


class BuildFix(Worker):
    """One model call for a project that does not build or load (its check command or its setup
    fails): the error, the files it names and the dependency files. Code, templates and config
    may change; tests may not."""

    def __init__(self, session, output, tried=()):
        super().__init__(session)
        self.output, self.tried = output, tried

    def run(self):
        """What it did, one line (for the next try)."""
        fixed = [Part("What failed", Output.failure_text(self.output), True, keep="tail"),
                 Part("Project files (all exist; only those shown below may change)",
                      ", ".join(p for p in self.p.files if self.ws.exists(p)), True)]
        tail = [Part("Already tried (it still failed)", "\n".join(f"- {t}" for t in self.tried)),
                Part("Language", self.p.language), *self.s.knowledge_parts("code", "config")]
        view = FileView(self.ws, fixed, self.candidates(), tail, output=self.output, hints=self.output)
        try:
            reply = self.s.ask(Prompts.BUILD_SYSTEM, view, "Make it build", max_reply=self.s.model.fix_max,
                               unthinking_retry=False)
        except PromptTooLarge as exc:
            print(f"  make it build: the prompt does not fit ({exc})")
            return "the prompt did not fit"
        change = self.ws.apply(reply, self.may_change(view), view.windows)
        cause = reply.cause() or "?"
        for note in change.notes:
            print(f"  skipped {note}")
        did = f"changed {', '.join(change.files)}" if change else "no usable change"
        print(f"  make it build: {did}" + (f"  ({cause})" if cause != "?" else ""))
        if change and cause != "?":
            self.s.note("build", cause)
        return f"{cause} -> {did}"

    def candidates(self):
        """The files the error names, then the dependency and other config files, then the code."""
        files = [p for p in self.s.files_on_disk("code", "template", "config") if p != ENV_FILE]
        configs = [p for p in files if self.p.files[p].get("kind") == "config"]
        return list(dict.fromkeys(Output.mentioned_in(self.output, files) + configs + files))[:FIX_FILES]

    def may_change(self, view):
        def check(path, exists):
            if not exists or path not in view.shown:
                return "only the files shown may change now"
            return TESTS_LOCKED if self.p.files.get(path, {}).get("kind") == "test" else None
        return check


class Change:
    """Files a reply changed: their previous text (None for a file it created), and what was refused."""

    def __init__(self):
        self.backups, self.notes = {}, []

    def __bool__(self):
        return bool(self.backups)

    @property
    def files(self):
        return list(self.backups)

    @property
    def created(self):
        return [p for p, old in self.backups.items() if old is None]


class FileView:
    """Builds a prompt (for Model.ask) of fixed parts, then each candidate file whole while it
    fits, else, for the first few that do not, a numbered window around the lines that matter,
    then optional tail parts. Files are placed before long prose, which may be shortened, since
    a file shown in part is much harder to change. Records what was shown."""

    def __init__(self, workspace, fixed, candidates, tail=(), output="", hints="", windows=2):
        self.ws, self.fixed, self.tail = workspace, list(fixed), list(tail)
        self.candidates = list(dict.fromkeys(candidates))
        self.output, self.hints, self.max_windows = output, hints, windows
        self.whole, self.windows = set(), {}

    def __call__(self, limit):
        self.whole, self.windows = set(), {}
        room = limit - sum(p.floor_size() for p in self.fixed if p.required) - 300
        files = []
        for path in self.candidates:
            if room < 250 or not self.ws.exists(path):
                continue
            part = self.whole_file(path, room) or self.window(path, room)
            if part:
                files.append(part)
                room -= part.size()
        how = [Part("Changing a file shown in part", Prompts.LINES_HOWTO, True)] if self.windows else []
        return self.fixed + files + how + self.tail

    @property
    def shown(self):
        return self.whole | set(self.windows)

    def whole_file(self, path, room):
        part = Part(f"File {path}", self.ws.read(path), True, exact=True)
        if part.size() <= room:
            self.whole.add(path)
            return part
        return None

    def window(self, path, room):
        if len(self.windows) >= self.max_windows or room < 700:
            return None
        lines = self.ws.read(path).splitlines()
        first, last, body = self.window_view(lines, self.focus_line(path, lines, self.output, self.hints), room - 140)
        self.windows[path] = (first, last)
        return Part(f"File {path}, lines {first}-{last} of {len(lines)} (numbered; the rest is not shown)",
                    body, True, exact=True)

    @staticmethod
    def focus_line(path, lines, output, hints):
        """The line of a file that matters most: one the output points at (path:line, line N), else
        the first line using a name from the hints, else the top."""
        for name in (re.escape(path), re.escape(Path(path).name)):
            for match in re.finditer(rf"{name}[\"']?(?:, line |:|\()(\d+)", output):
                if 1 <= int(match.group(1)) <= len(lines):
                    return int(match.group(1))
        for word in sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", hints)), key=len, reverse=True)[:40]:
            for index, line in enumerate(lines, 1):
                if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", line):
                    return index
        return 1

    @staticmethod
    def window_view(lines, center, room):
        """Numbered lines around `center` that fit in `room` chars: (first, last, text)."""
        start = end = max(1, min(center, len(lines)))
        size = lambda a, b: sum(len(lines[i - 1]) + 7 for i in range(a, b + 1))
        grown = True
        while grown:
            grown = False
            for a, b in ((start - 1, end), (start, end + 1)):
                if 1 <= a and b <= len(lines) and size(a, b) <= room:
                    start, end, grown = a, b, True
        return start, end, "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))


class ToolError(Exception):
    """A rejected path or command."""


class FixAttempt(Worker):
    """One model call for a failing test: the prompt, the change, and its result."""
    APPROACH_TEXT = {"fix": "fix the code", "review": "review the test and the code against the plan"}

    def __init__(self, session, verifier, step, approach, output, before):
        super().__init__(session)
        self.v = verifier
        self.step, self.approach, self.output, self.before = step, approach, output, before
        self.ref = TestRef(step["test"])
        self.label = f"{self.APPROACH_TEXT[approach].capitalize()} {step['id']}"
        self.blamed_test = False  # a fix that changed nothing because it only wanted to change a test
        self.conflict = False     # it broke a test an earlier fix broke too: the two tests may disagree

    def run(self):
        """(state, output) after the attempt; the output carries what went wrong."""
        view = self.prompt()
        try:
            reply = self.s.ask(self.system(), view, self.label, max_reply=self.s.model.fix_max,
                               unthinking_retry=False)
        except PromptTooLarge as exc:
            return "fails", self.output + f"\n(prompt too large: {exc})"
        change = self.ws.apply(reply, self.may_change(view), view.windows)
        self.remember_wanted_files(reply, change, view)
        cause = reply.cause() or "?"
        commands = self.changed_commands(reply)
        if not change and not commands:
            return self.no_change(cause, change)
        self.p.register(change.created, "test" if self.approach == "review" else "code", f"added for {self.step['id']}")
        self.s.setup()
        state, output = self.v.evaluate(self.step)
        if state == "passes" and self.broke_others(change, cause):
            return "fails", self.output + ("\n\nYour last change made this test pass but broke "
                                           f"{', '.join(self.step['keep'])} (shown below); it was reverted.")
        self.record(f"{cause} -> {state}")
        if state == "passes" and cause != "?":
            self.s.note(self.approach, f"{self.step['behaviour'][:120]} | solved: {cause}")
        print(f"  {self.label}: changed {', '.join(change.files + commands)} -> {state}"
              + (f"  ({cause})" if cause != "?" else ""))
        return state, output

    def prompt(self):
        """The criterion, the files that exist, the failure and the test first; then the files
        most likely involved, whole while they fit (see FileView)."""
        if self.ref.is_start_check:
            excerpt = self.check_text()
        else:
            excerpt = self.ref.excerpt(self.ws, self.p.test_names(self.ref.file))
        fixed = [Part("Criterion", self.step["behaviour"], True),
                 Part("Project files (all exist; only those shown below may change)",
                      ", ".join(p for p in self.p.files if self.ws.exists(p)), True),
                 Part("Failure output", Output.failure_text(self.output), True, keep="tail"),
                 Part("How it is checked" if self.ref.is_start_check else f"Test {self.ref}", excerpt, True)]
        fixed += [Part(f"Test {ref} (it passes now and must keep passing)", ref.excerpt(self.ws, self.p.test_names(ref.file)),
                       True, atomic=True) for ref in map(TestRef, self.step.get("keep", [])[:2])
                  if not ref.is_start_check and ref.present(self.ws)]
        candidates = self.candidates()
        tail = [Part("Contracts", self.p.contracts(candidates[:4])),
                Part("Already tried", "\n".join(self.step.get("history", [])[-4:]), keep="tail"),
                Part("Language", self.p.language),
                *self.s.knowledge_parts(*(("code", "tests") if self.approach == "review" else ("code",)))]
        return FileView(self.ws, fixed, candidates, tail, output=self.output, hints=excerpt)

    def system(self):
        return Prompts.FIX_SYSTEM if self.approach == "fix" else Prompts.REVIEW_SYSTEM

    def may_change(self, view):
        def check(path, exists):
            if not exists:  # the design is settled: a new file now would shadow or duplicate a planned one
                return None if self.approach == "review" and Path(path).parent == Path(self.ref.file).parent \
                    else "no new files now; change the files shown"
            if self.p.files.get(path, {}).get("kind") == "test" or path == self.ref.file:
                return None if self.approach == "review" and path in view.shown else TESTS_LOCKED
            return None if path in view.shown else "only the files shown may change"
        return check

    def remember_wanted_files(self, reply, change, view):
        """A file it tried to change without seeing it whole is shown first next time."""
        self.step["want"] = [p for p in reply.named_paths() if p not in change.backups and self.ws.exists(p)
                             and p not in view.whole][:2]

    def changed_commands(self, reply):
        """The start check's commands (run, smoke, serves) a fix of it corrects with a TOOLCHAIN line."""
        if not self.ref.is_start_check:
            return []
        try:
            given = {k: v for k, v in (reply.toolchain_line() or {}).items() if k in ("run", "smoke", "serves")}
        except ValueError:
            return []
        before = self.s.toolchain.to_dict()
        self.p.toolchain = self.s.toolchain.merged(given)
        return [f"toolchain.{k}" for k, v in self.s.toolchain.to_dict().items() if v != before.get(k)]

    def no_change(self, cause, change):
        note = "no usable change" + (f" ({'; '.join(change.notes)})" if change.notes else "")
        self.blamed_test = self.approach == "fix" and any(TESTS_LOCKED in n for n in change.notes)
        self.record(f"{cause} -> {note}")
        print(f"  {self.label}: {note}")
        return "fails", self.output

    def broke_others(self, change, cause):
        if not self.before:
            return False
        broken = sorted(self.before - self.v.passing())
        if not broken:
            return False
        self.ws.revert(change)
        tests = [self.p.criterion(b)["test"] for b in broken]
        kept = self.step.setdefault("keep", [])
        self.conflict = not self.ref.is_start_check and any(t in kept for t in tests)
        kept.extend(t for t in tests if t not in kept)
        names = ", ".join(tests[:2])
        self.record(f"{cause} -> made it pass but broke {names}; reverted")
        print(f"  {self.label}: made it pass but broke {names}; reverted")
        return True

    def record(self, line):
        self.step.setdefault("history", []).append(f"{self.approach}: {line}")

    def check_text(self):
        """What the start check runs, for a fix of it (it may correct these commands too)."""
        tc = self.s.toolchain
        return (f"run: {tc.run}\nserves (keeps running until stopped): {str(tc.serves).lower()}\n"
                f"smoke: {tc.smoke or '(none)'}\nPORT is set in the environment to a free port; the environment "
                f"overrides {ENV_FILE}.\nTo correct a command, add a line: TOOLCHAIN: "
                f"{{\"run\": \"...\", \"smoke\": \"...\", \"serves\": true}}")

    def candidates(self):
        """Files the model asked for last time first, then those the failure names, then the
        files the test uses (for the start check: the entry point), then the rest (the test file
        itself only in a review)."""
        code = [p for p in self.s.files_on_disk("code", "template", "config") if p != ENV_FILE]
        named = [p for p in self.mentioned(code) if p in code]
        wanted = [p for p in self.step.get("want", []) if self.ws.exists(p)]
        own_test = [self.ref.file] if self.approach == "review" and not self.ref.is_start_check else []
        uses = [p for p in self.p.uses(self.ref.file) if p in code]
        if self.ref.is_start_check:
            uses = self.p.entry_files(code) + self.p.uses(self.p.spec.get("entry", ""))
        return list(dict.fromkeys(wanted + own_test + named[:2] + uses + named + code))[:FIX_FILES]

    def mentioned(self, paths):
        return Output.mentioned_in(self.output, paths)


class WebError(Exception):
    """A web backend that could not answer; network: the backend cannot be reached at all (no
    network, or its service is down), so it is not asked again."""

    def __init__(self, message, network=False):
        super().__init__(message)
        self.network = network


def first_line(text, limit=140):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[:limit - 3] + "..."


def now_text():
    return time.strftime("%Y-%m-%d %H:%M")


def clock(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def human_duration(seconds):
    """1762.08 -> '29m 22s'; 4000 -> '1h 6m 40s'; 42.5 -> '42s'."""
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else (f"{minutes}m {secs}s" if minutes else f"{secs}s")


def words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def split_text(text, size):
    """Text in chunks of at most size chars (at least 200), split between paragraphs where it can
    (a longer paragraph is cut)."""
    size, chunks, current = max(size, 200), [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            chunks += [current] if current else []
            chunks.append(para[:size])
            current, para = "", para[size:]
        if para and current and len(current) + 2 + len(para) > size:
            chunks.append(current)
            current = para
        elif para:
            current = f"{current}\n\n{para}" if current else para
    return chunks + ([current] if current else [])


def fingerprint(text):
    """A short fixed key for some text: the same text, the same key."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def after_what_they_use(starts, uses, known):
    """Paths in an order where each comes after the paths it uses (depth first from starts, in
    their order; a cycle is broken where it closes). uses(path): the paths it uses; known(path):
    whether a path takes part at all."""
    order, seen = [], set()

    def visit(path, trail=()):
        if path in seen or path in trail or not known(path):
            return
        for used in uses(path):
            visit(used, trail + (path,))
        seen.add(path)
        order.append(path)

    for path in starts:
        visit(path)
    return order


def similarity(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def groups_fitting(items, room, size=len):
    """Items in order, grouped so that each group fits in room chars (an item larger than room is
    a group of its own): size(item) chars each, and one more for its line break."""
    groups, used = [], room + 1
    for item in items:
        need = size(item) + 1
        if used + need > room:
            groups.append([])
            used = 0
        groups[-1].append(item)
        used += need
    return groups


class Prompts:
    """Every prompt text: language- and app-neutral (the examples are a small command-line notes
    tool). The *_SYSTEM ones are system prompts; the others are pieces of them and of user messages."""
    NO_TOOL_CALLS = "\nReply in plain text; never call a tool or function."  # Ollama parses tool-call syntax
    FILE_BLOCK = "<<<FILE path>>>\nthe complete file\n<<<END>>>"  # how a reply gives a whole file

    FILES_REPLY = (f"Reply with a line \"CAUSE: <one sentence>\", then each file you change, complete:\n"
                   f"{FILE_BLOCK}\n"
                   f"Only the files shown may change; the project's other files exist. Files stay under "
                   f"{MAX_FILE_LINES} lines.")

    LINES_HOWTO = ("A file shown as numbered lines is changed with: <<<LINES path A-B>>>, then the new text for its "
                   "lines A to B (within the range shown, without the line numbers), then <<<END>>>. Give a whole "
                   "file only for files shown in full.")

    DEPENDENCIES_TITLE = ("Third-party dependencies (the only ones it may import; everything else is the standard "
                          "library or the project's own files)")

    SPEC_JSON = ('{"language":"Python",\n'
                 ' "criteria":[{"text":"notes.add(\\"buy milk\\") returns 1 and notes.all() then holds it","source":'
                 '"words copied from the request","test":"tests/test_notes.py::test_add","effort":1},\n'
                 ' {"text":"notes.sync_all(push) calls push(note) for each note not yet synced","source":"...",'
                 '"test":"tests/test_notes.py::test_sync_all","effort":1}],\n'
                 ' "acceptance":[{"text":"`notes add milk` then `notes list` shows \\"1: milk\\"","source":"...",'
                 '"test":"tests/acceptance/test_use.py::test_add_and_list","effort":1}],\n'
                 ' "constraints":["how the request says to build it"],"non_goals":["what a typical version adds unasked"],\n'
                 ' "files":[{"path":"notes.py","purpose":"one line"}],\n'
                 ' "data":"each adapter function and the sample data tests pass instead, e.g. sync_client.push(note) -> '
                 'None, sample [{\\"id\\": 1, \\"text\\": \\"milk\\"}]; data passed between modules"}')

    SPEC_RULES = f"""Rules:
- language: the one the request names, else Python; decide what kind of app it is and how it is used;
- criteria: the spec as tests, only what the request asks for; "source" copies the request's words (else
  the criterion is dropped); each is ONE behaviour of one module of the app's own code, checked by ONE
  fast test through its public interface; together they cover the request; never about how it is built
  (that is "constraints") or about tests; no repeats; usually 4 to 12;
- acceptance: 2 to 5 checks of the app's main flow from a user's point of view once it is built, start
  to end through its entry point (command, main function, the requests it serves), with sample data;
  only what a test of the app's own code can decide (not what only a browser or a person sees);
- nothing outside the app is tested: an external service (API, SDK) sits behind one thin adapter module
  with no logic and no criterion (the user tries it by hand); code using it takes it as a parameter, so
  tests pass sample data (a list, a generator); never a browser, the network, signals or the OS;
- "test" is "<test file>::<test name>" in the language's conventions;
- settings (keys, paths, limits, a server's PORT) come from {ENV_FILE}; {ENV_EXAMPLE} lists them with
  defaults (empty if required); environment variables override; a required setting gets a criterion
  (source "setting") that without it the app says which setting to set;
- files: small modules, one responsibility each, under {MAX_FILE_LINES} lines; list test files, the
  dependency file and {ENV_EXAMPLE} (never {ENV_FILE}); non_goals never exclude what is asked;
- effort: 1 trivial, 2 ordinary, 3 tricky."""

    SPEC_SYSTEM = f"""Plan a project in detail before any code exists: turn the request into a minimal, testable
spec. The code will be written to pass exactly these tests and nothing more.
Reply with ONLY JSON:
{SPEC_JSON}
{SPEC_RULES}"""

    FEATURE_SPEC_SYSTEM = f"""Plan a change to an existing project (a feature or a bug fix) in detail before any code
exists: a minimal, testable spec of the change only, in the project's language. For a bug, one
criterion's test reproduces it. Keep existing behaviour. Reply with ONLY JSON:
{SPEC_JSON}
{SPEC_RULES}
- files: only the existing files that must change and any new ones."""

    TOOLCHAIN_JSON = ('"toolchain":{"setup":["python3 -m venv .venv",".venv/bin/pip install -r requirements.txt '
                      'pytest black"],"check":".venv/bin/python -c \\"import cli, notes, sync_client\\"",'
                      '"test_all":".venv/bin/pytest -q","test_one":".venv/bin/pytest -q {file}::{name}",'
                      '"run":".venv/bin/python cli.py","serves":false,"smoke":".venv/bin/python cli.py list",'
                      '"format":".venv/bin/python -m black ."}')

    TOOLCHAIN_RULES = ("toolchain: the language's usual tools, run from the project folder in a POSIX shell, with "
                       "dependencies installed inside the project; setup installs everything check, tests and run need, "
                       "the test framework included (never .env: Tinker makes it from .env.example); check "
                       "quickly builds or loads every module of the app, so a "
                       "missing import or dependency fails it; test_all and test_one run the test tool the way a person types it "
                       "in the project folder once it is set up (the tool's own command, never through an interpreter "
                       "or wrapper that finds the app's code where the tool alone does not); test_one runs one test "
                       "given {file} and {name}; run "
                       "starts the app the way a user does; serves: true when run keeps running until stopped (a "
                       "server, a UI), and such an app takes its port from the PORT setting; smoke: one command that "
                       "shows the started app works, with no external service: for a server a request to its first "
                       "page (curl -fsS http://127.0.0.1:$PORT/), for a command the command with sample arguments; "
                       "format: formats the code files in place with the language's formatter (the one learned, else "
                       "its usual one, installed by setup), or \"\" when it has none")

    DESIGN_SYSTEM = f"""Lay out the files of a project before any code exists: what each file is for and the
project files it uses (each file's exact contract is written next, one file at a time).
Reply with ONLY JSON (this example is for a Python command-line tool):
{{"entry":"cli.py","files":[{{"path":"notes.py","kind":"code","role":"stores and syncs notes","uses":[]}},
 {{"path":"sync_client.py","kind":"code","role":"adapter: sends a note to the sync service","uses":[]}},
 {{"path":"cli.py","kind":"code","role":"the notes command","uses":["notes.py","sync_client.py"]}},
 {{"path":"requirements.txt","kind":"config","role":"dependencies","contract":["python-dotenv"],"uses":[]}}]}}
Rules: "entry" is the file a user starts; keep the spec's files, add only what is missing; kind: code, test,
template, config or docs; one responsibility per file, under {MAX_FILE_LINES} lines; "uses": the project files
it imports, renders, reads or calls (a test file: those its tests call); an external service sits behind
one thin adapter (role "adapter: ..."); settings come from {ENV_FILE}; a config file's contract lists its entries."""

    CONTRACT_SYSTEM = """Write the contract of one file of a planned project before any code exists, in the project's
language: every name, signature, data shape, route, command and exact string that its criteria's tests
check or that the files using it rely on; nothing more (no extra functions, options or fallbacks). Use
the contracts shown exactly. A file that renders templates names each one, the values it gives it and
what the page must show. State several requests or calls share is kept where they all reach it. Code
using an external service takes the adapter as a parameter (default: the adapter); an entry point takes
it where the app is created, so tests pass sample data; an adapter only makes its call and returns the
data. The entry point loads the settings from .env at start (environment variables override).
Reply with ONLY JSON: {"contract":["sync_all(push=sync_client.push) -> int: calls push(note) for each
unsynced note, marks it synced, returns the count"],"uses":["the project files it imports, renders or calls"]}"""

    INTERFACE_SYSTEM = """Check one file of a plan against the files it uses, before any code exists. Tests and code
follow the plan exactly, so a mismatch found now saves hours later. Check that the file uses each of them
exactly as its contract says (names, arguments, return shapes, exact strings) and that each offers what
the file needs. Correct each mismatch in the entry that is wrong.
Reply with ONLY JSON: {"problems":[{"file":"path","problem":"what is wrong, one line"}],"files":[the
corrected entries of only the files that change: path, kind, role, contract, uses]}
or {"problems":[],"files":[]} when they match."""

    TOOLCHAIN_SYSTEM = f"""Give the commands to set up, check, test and run a project with its language's usual tools.
Reply with ONLY JSON: {{{TOOLCHAIN_JSON}}} (the example is for a Python command-line tool).
Rules: {TOOLCHAIN_RULES}."""

    TOOLCHAIN_FIX_SYSTEM = (f"The project's tools do not work: the output shows a tooling problem (a missing test "
                            f"framework, dependency or command), not a failing test. Fix the setup only: "
                            f"give each dependency or config file you change complete, and/or a line TOOLCHAIN: {{json}} "
                            f"with the corrected commands. Never change code or tests. {TOOLCHAIN_RULES}.\n"
                            f"Reply with a line \"CAUSE: <one sentence>\", then the changes:\n{FILE_BLOCK}")

    TESTS_REPLY = f"""Reply with the test file, complete:
{FILE_BLOCK}
Rules: one test per criterion with exactly the given name; assert only what the criterion and the
contracts state; the file is self-contained: sample data is defined in it and passed where the app takes
an adapter, so nothing outside the app runs (no network, no real settings, no fake servers, no helper
files); every test finishes within seconds and leaves nothing running; keep each test short."""

    SPEC_TESTS_SYSTEM = """Write the spec tests for the given criteria before the code exists, in the project's language
and test framework (as the test command runs them). Each test checks its criterion through the public
interface of the module the contracts name: the code will be written to pass these tests and nothing more.
""" + TESTS_REPLY

    ACCEPTANCE_TESTS_SYSTEM = """The app is built and its spec tests pass. Write its acceptance tests, in its language and
test framework: each checks its criterion from a user's point of view, using the app's main flow start to
end through its entry point the way a user does (a web app through its test client, a command through its
main function), with the app's real code (shown) behind it and sample data in place of each adapter.
""" + TESTS_REPLY

    REPRODUCE_SYSTEM = f"""A user reports a bug in a project that is built and whose tests pass. Write ONE test that
shows the bug: it checks the correct behaviour the report implies, so it fails on the code as it is now
and passes once the bug is fixed. It tests through the app's public interface, as the project's other
tests do, with sample data in place of each adapter: nothing outside the app runs. It goes in a NEW
test file next to the project's tests; the file and the test are named as the project's test tool
expects. Reply with a line "TEST: <test file>::<test name>", a line "EXPECTED: <the correct behaviour,
one sentence>", then the test file, complete:
{FILE_BLOCK}
Keep it short."""

    CODE_SYSTEM = f"""Write one file of a project, in its language. It must fulfil its contract exactly, make the
given tests pass, meet its criteria and do nothing more: no extra functions, options, fallbacks, logging
or abstraction: only what the tests need. Use the contracts of the files it uses exactly; an adapter to
an external service only makes its call and returns the data. Keep it under {MAX_FILE_LINES} lines; if it
needs more, move a part into a new small module and give that file too; never repeat other files.
Reply with only:
{FILE_BLOCK}"""

    CONFIG_SYSTEM = f"""Write a project's small configuration files (dependencies, settings, tool settings), each
complete, as its role and contract say, in the language's usual formats, before any test or code exists.
The dependencies include the test framework; the test tool's settings make the tests find the app's code
when the test tool runs alone from the project folder. {ENV_EXAMPLE} lists each setting with a comment
and its default (empty for a required one). Reply with one block per file:
{FILE_BLOCK}"""

    CHANGE_SYSTEM = ("Change one file of an existing project so that it meets the given criteria or fixes the given "
                     "problems, with the smallest change; keep everything else as it is.\n" + FILES_REPLY)

    BUILD_SYSTEM = ("The project does not build or load: the output shows why (a missing dependency, an import of a "
                    "module or name that does not exist, a syntax error). Fix the cause in the files shown: a missing "
                    "dependency goes into the dependency file; an import or the code is corrected, keeping every "
                    "contract. Do not change tests.\n" + FILES_REPLY)

    FIX_SYSTEM = ("A test fails. Find the cause in the failure output and change the code so that the test passes, "
                  "keeping the contracts and adding nothing the test does not need. Do not change tests.\n" + FILES_REPLY)

    REVIEW_SYSTEM = ("A test still fails after several fixes, so the test or the code is wrong against the plan. "
                     "Compare both with the criterion and the contracts, decide which one is wrong and correct it "
                     "(a test keeps its name and tests the criterion through the app's interface).\n" + FILES_REPLY)

    README_SYSTEM = f"""Write the README.md of a project: one line on what it does, then setup, settings (copy
{ENV_EXAMPLE} to {ENV_FILE} and what each setting means), how to run it and how to run its tests. Use
exactly the given commands; say nothing the given facts do not support. Short. Reply with only:
<<<FILE README.md>>>
the complete file
<<<END>>>"""

    LESSONS_SYSTEM = f"""A code generator just built a project. Below are the problems it hit and how each was
solved. Write the lessons that would have avoided such problems in OTHER projects, so the generator gets
them right the first time: at most 3, each one imperative sentence naming the concrete technique (how to
fake a kind of service in tests, a setting a tool needs, a pattern a runner expects). Skip anything about
this project's own names, features or data, and anything the known lessons already say.
Reply with ONLY JSON: {{"lessons":[{{"phase":"one of {', '.join(LESSON_PHASES)}","scope":"language (only
this language) or any","text":"the lesson"}}],"confirmed":[numbers of known lessons these problems show
again]}} or {{"lessons":[],"confirmed":[]}} when nothing generalizes."""

    COVERAGE_SYSTEM = """Check a spec against its request before anything is designed. The app will do only what
its criteria and acceptance checks test, so each feature, screen and interaction the request asks for, and
the main flow a user goes through from start to end, needs one. List only what is missing, in the spec's
shape: nothing the request does not ask for, nothing about an adapter to an external service, no repeats.
Reply with ONLY JSON: {"criteria":[{"text":"one behaviour of one module","source":"words copied from the
request","test":"<test file>::<test name>","effort":1}],"acceptance":[the same shape]}
or {"criteria":[],"acceptance":[]} when nothing is missing."""

    RECALL_SYSTEM = """Before a project is planned, choose what a code generator should know for it. It knows only
what it was trained on; what it has learned from the web since is listed below, one topic per line.
"use": the learned topics this request needs, by the name before the colon. "learn": topics this
request needs that are not listed and that it may not know well or as they are today (the language,
frameworks, libraries and techniques the request names or clearly implies), most important first;
none when learning now is not possible. Reply with ONLY JSON: {"use":["python"],"learn":["Flask"]}"""

    LEARN_PLAN_SYSTEM = f"""Plan how to learn a topic from the web for a code generator that knows only what it was
trained on. The topic may be a framework, a library, a tool or a technique, or name several. Give its
name, one sentence on what it is, up to {MAX_QUERIES} web searches, at least one for each thing it names,
that find how to use it today (setup, commands, API, configuration) and its current version and what
changed (name the year when it helps), and, only if it is software that may be installed on this
machine, up to {MAX_COMMANDS} commands that show its version and exit (one command each: no shell
operators, nothing installed or changed). "language": the language's name when the topic is mainly a
programming language (with or without its tools), else "".
Reply with ONLY JSON (this example is for Flask):
{{"name":"Flask","summary":"A small web framework for Python.","language":"",
 "queries":["Flask quickstart routes templates","Flask latest release what changed"],
 "commands":["python3 -m flask --version"]}}"""

    LANGUAGE_PLAN_SYSTEM = f"""Plan how to learn a programming language from the web as it is today, for a code generator
that knows only what it was trained on and must build software in it quickly and well. From the request
take the language, the version it asks for (else the newest stable one) and the tools it names. Give:
- name, version, one sentence on what it is;
- tools: one per role the language has, of {", ".join(TOOL_ROLES)} (build only if it is compiled;
  environment: how a project's dependencies are kept apart): the tool the request names for a role, else
  the one most used today; each with one web search that finds how to use it in a project (install,
  commands, configuration) and one command that shows its version and exits;
- searches: 2 for fundamentals (syntax, reserved keywords, types, errors, modules, the concurrency and
  runtime model), 2 for new (the newest stable releases: features, deprecations, removals; name the
  year), 1 for conventions (project layout, style guide, packaging);
- commands: up to 2 that show the language's version and exit.
Commands: one each, no shell operators, nothing installed or changed. If the request is no programming
language, reply {{"name":"","summary":"what it is instead"}}.
Reply with ONLY JSON (this example is for Go):
{{"name":"Go","version":"1.25","summary":"A compiled, statically typed language with built-in concurrency.",
 "tools":[{{"role":"tests","name":"go test","search":"go test flags table-driven tests","command":"go version"}}],
 "searches":[{{"section":"fundamentals","query":"Go language specification keywords types"}},
  {{"section":"new","query":"Go 1.25 release notes"}}],"commands":["go version"]}}"""

    TOPIC_FOCUS = ("how to set up and use {topic} today (commands, API, configuration), its current version and what "
                   "is new or changed, its conventions and good practice")

    LANGUAGE_FOCUS = {
        "fundamentals": ("the core of {language} a developer must get right: syntax rules, reserved keywords, built-in "
                         "types, error handling, modules and imports, the concurrency and runtime model, idioms"),
        "new": ("what the newest stable releases of {language} added, changed, deprecated or removed (nothing "
                "unreleased: no alpha, beta, release candidate, dev or draft)"),
        "conventions": ("how {language} projects are laid out and written today: project layout, style guide, "
                        "packaging and dependency files"),
    }

    TOOL_FOCUS = ("how to use {tool} ({role}) in a {language} project: installing it in the project, the exact commands "
                  "and options to run it, its configuration file")

    FACTS_SYSTEM = f"""Read part of a web page for a code generator that knows only what it was trained on. List what
the page states that matches "Look for" and that such a generator needs to build good software today:
each one plain, concrete sentence the page states (never from your own knowledge), with the exact
commands, names and versions it gives; nothing about one particular project, nothing about an
unreleased version (alpha, beta, release candidate, dev, draft); at most {FACTS_PER_CHUNK}. "phase" is
where it matters: spec (what to build and test), design (files, structure), config (dependencies,
settings), tests, code, toolchain (commands and tools).
Reply with ONLY JSON: {{"facts":[{{"phase":"one of {', '.join(FACT_PHASES)}","text":"the fact"}}]}}
or {{"facts":[]}} when the excerpt states nothing like that."""

    PICK_FACTS_SYSTEM = """Below are facts found on web pages about a topic, for a code generator. Keep the {count}
that most help it plan and write good software today; drop repeats and anything vague.
Reply with ONLY JSON: {{"keep":[the numbers of the facts kept]}}"""

    BRIEF_SYSTEM = ("You condense part of a software spec. Output terse bullets of concrete requirements only: "
                    "features, interfaces, data, storage, libraries, constraints, edge cases. No prose, no code. "
                    "At most {words} words.")


if __name__ == "__main__":
    main()