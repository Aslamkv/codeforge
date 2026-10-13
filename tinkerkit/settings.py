"""Tinker's settings: where it keeps what it learns, the limits it works within, and the
small helpers every part uses."""

import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


# --- Tinker's own folder: settings, measured models, what it learned, the runs ------------------
HOME = Path(__file__).resolve().parent.parent / ".tinker"  # next to the script (its real file, not a link)
CONFIG_FILE = HOME / "config.json"      # the model in use, Ollama's address, keep_alive, the web, the embeddings
MODELS_DIR = HOME / "models"            # <model>.json: what calibration measured for each model
LANGUAGES_DIR = HOME / "languages"      # <language>.json: a working toolchain, toolchain fixes, lessons
MEMORY_FILE = HOME / "memory.json"      # lessons that hold for any language
RUNS_DIR = HOME / "runs"                # id -> project, so tinker resume ID works anywhere
COMMANDS_FILE = HOME / "commands.json"  # the person's own commands Tinker may use (tinker learn NAME)

DEFAULT_CONFIG = {                      # config.json starts as this; what the file holds overrides it
    "model": None,                      # the model every run uses: none until tinker use-model NAME selects one
    "ollama_url": "http://localhost:11434",  # where Ollama listens (Ollama's own default)
    "keep_alive": 0,                    # unload after every call: a model kept loaded can keep growing
    "max_growth": 1.15,                 # with keep_alive, unload once the model grows 15% past its first size
    "build_think": "medium",            # think level for building (tests, code, fixes); planning uses the model's
    "test_timeout": 60,                 # seconds one test may take: longer means it hangs
    "web": "ollama",                    # the Internet plugin's backend, used by tinker learn and --web only:
                                        # "ollama" (its key in OLLAMA_API_KEY), "duckduckgo" (no key), or ""
    "web_fallback": "",                 # opt-in: a backend asked whatever the first cannot answer (and alone
                                        # when the first is not usable), e.g. "duckduckgo"; "" for none
    "web_timeout": 30,                  # seconds one web search or fetch may take
    "learn_missing": 2,                 # topics the recall phase of a run with --web may learn
    "embed_model": "",                  # the embedding model that indexes what is learned (tinker embed NAME);
                                        # tinker learn and --web need one
}

# --- each project's own state ------------------------------------------------------------------
STATE_DIR = ".tinker"             # in each project: its spec, run and log
ENV_FILE, ENV_EXAMPLE = ".env", ".env.example"
LOG_NAME = "current.log"

# --- the work: cycles, phases, criteria, files -------------------------------------------------
# The idea, as the phases of one cycle: plan only what the request asks as a spec; turn every spec point into
# a unit test, with the test tool installed first; write just enough code to pass them; review it once at
# the end, the way a person will run it; then it is done.
PHASES = (
    "recall",        # what was learned that the request needs
    "spec",          # plan: the spec points with their tests, the files, the toolchain (one call)
    "design",        # plan: the contracts where files meet
    "review",        # plan: a file checked against the contracts it uses (only where several meet)
    "setup",         # the test tool and the dependencies installed, and shown to work, before any test
    "tests",         # a test for every spec point, before any code
    "code",          # just enough code to pass them
    "verify",        # ... fixed until every spec test passes
    "tidy",          # the language's linter over the code: what it finds fixed, every test still passing
    "docs",          # the README, and its examples run as a user runs them
    "final_review",  # once at the end: set up afresh, every test run the way a person runs them
    "lessons",       # what the next projects should know
    "done")
FIX_PHASES = ("recall", "reproduce", "verify", "final_review", "lessons", "done")  # tinker fix: a test that shows the bug, then the fix
PLANNING_PHASES = ("recall", "spec", "design", "review")  # the phases --plan-only runs
FORMAT_AFTER = ("verify",)  # phases after which the toolchain's formatter runs (the model never formats)
STATUS_TEXT = {"green": "passes", "todo": "to do", "active": "in progress", "blocked": "parked"}
KINDS = ("code", "test", "template", "config", "docs")
MAX_FILE_LINES = 60               # design guidance: small modules (a bigger file still works, in windows)
TESTS_PER_CALL = 10

# --- fixing: until each criterion's test passes ------------------------------------------------
DEFAULT_MAX_STALL = 3             # fixes without progress before the review against the plan
MAX_TRIES_PER_PASS = 8            # model calls on one criterion per pass, whatever "progress" says
REVIEW_TRIES = 2                  # review calls when fixing stops making progress
BUILD_TRIES = 3                   # fixes for a project that does not build before the run stops
OUTPUT_CHARS = 1600               # the end of a failure output shown to the model
TESTS_LOCKED = "tests do not change now"  # why a fix may not change a test (a fix that tries points at the test)
FIX_FILES = 7                     # files a fix may be shown (the most related first)

# --- the project's commands (one test's limit is test_timeout in config.json) ------------------
NETWORK_RETRY = 30                # seconds between tries of what waits for the network (a package to install)
ONLINE_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443))  # opened and closed to see the internet is there
NETWORK_NOTE = 600                # while a build waits for the network, a line says so this often
SETUP_TIMEOUT = 900
COMMAND_TIMEOUT = 300             # setup aside, the longest a command may run (all tests)

# --- prompts: within the model's input budget --------------------------------------------------
CHARS_PER_TOKEN = 2.8             # the start (code ~2.9 chars/token); then learned from real prompts
MIN_CHARS_PER_TOKEN = 2.0         # HTML and dense code have fewer chars per token; never assume fewer
MAX_CHARS_PER_TOKEN = 4.0         # prose measured 4.1; never assume more
PROMPT_HEADROOM = 0.95            # aim 5% under the budget: code has fewer chars per token than prose
TRIM_FLOOR = 300                  # a shortened prose part keeps at least this many chars
MIN_PROMPT_TOKENS = 512           # a run never lowers its prompt budget below this

# --- replies -----------------------------------------------------------------------------------
MIN_REPLY_TOKENS = 1024
EARLY_STOP_GRACE = 3.0            # seconds to await Ollama's last chunk (its token counts) once a reply is complete
PROGRESS_EVERY = 2.0              # seconds between live progress updates

# --- thinking budgets: a reply cut off while still thinking is asked again unthinking, given the
# thought so far to conclude (THOUGHT_KEPT chars of it), so a lower budget costs no answer ----------
DEFAULT_FIX_MAX = 4096            # a fix (measured: most that answer thinking need under 4K; past it, few do)
TESTS_MAX_TOKENS = 8192           # a test file: room to think, and to write the whole file unthinking
CHECK_MAX_TOKENS = 12288          # a check of a plan or an app (not its making)
THOUGHT_KEPT = 4000               # chars of an unfinished thought passed on to conclude

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
DEFAULT_THINK = "high"            # think hard once instead of reworking cheap guesses (if the model takes it)
PROMPT_TARGET = 8192              # the largest prompt a run needs (whole files are small modules; a fix shows at
                                  # most FIX_FILES of them)
REPLY_TARGET = 16384              # the longest reply a run needs (thinking included: the longest calls think to it)
CAPACITY_STEP = 0.1               # a context that does not fit is searched for to within this much (10%)
MEMORY_OVERHEAD = 1.5e9           # bytes a runner needs besides the weights and the context
PROBE_BATCH = 256                 # piece size tried when a whole prompt crashes a GGUF runner (num_batch)

# --- learning: what earlier projects taught (languages/<language>.json, memory.json) -----------
LESSON_PHASES = ("design", "config", "tests", "code", "toolchain")  # where a lesson is shown
MAX_LESSONS = 15                  # per file; the least confirmed go first
LESSONS_SHOWN = 5                 # per prompt (an optional part: dropped first when the prompt is full)
SAME_LESSON = 0.75                # text similarity at which a new lesson is an old one seen again
SAME_FACT = 0.75                  # text similarity at which a fact learned from the web is a repeat
SIMILAR_FAILURE = 0.7             # similarity at which a past toolchain fix is shown as a hint

# --- learning from the web: tinker learn TOPIC, and the recall phase of a run --------------------------
LEARNED_DIR = HOME / "learned"    # <topic>.json: what was learned about a topic, with its sources
EMBEDDINGS_FILE = HOME / "embeddings.json"  # the embedding of each learned text, made once (tinker embed model)
LEARN_LOG = HOME / "learn.jsonl"  # every model call tinker learn makes
FACT_PHASES = ("spec", "design", "config", "tests", "code", "toolchain")  # where a learned fact is shown
MAX_QUERIES = 4                   # web searches per topic (at least one for each thing it names)
MAX_COMMANDS = 6                  # commands that show what of a topic is installed here
VERSION_TIMEOUT = 20              # seconds such a command may take
SEARCH_RESULTS = 3                # results taken from each search
MAX_PAGES = 5                     # pages read per topic
PAGE_CHUNKS = 2                   # prompt-sized chunks of a page condensed (its top, then the most related)
FACTS_PER_CHUNK = 4               # facts one chunk may give
FACT_CHARS = 400                  # the longest fact kept (room for its exact numbers, names and units)
MAX_FACTS = 16                    # facts kept per topic
TOOL_ROLES = ("environment", "packages", "build", "tests", "format", "lint")  # a language's tools, one per role
LANGUAGE_SECTIONS = ("fundamentals", "new", "conventions")  # what a language's learning searches besides tools
SECTION_PAGES = {"fundamentals": 2, "new": 2, "conventions": 1, "tools": 1}  # pages read per search of a section
SECTION_FACTS = {"fundamentals": 10, "new": 10, "tools": 16, "conventions": 6}  # facts a language keeps per section

# --- research: tinker learn of a domain (Researcher) ----------------------------------------------------
RESEARCH_QUESTIONS = 4            # research questions a topic is split into
RESEARCH_QUERIES = 2              # web searches per question, for independent sources
RESEARCH_PAGES = (8, 6)           # pages read per round: the first round, then one for the gaps and checks
RESEARCH_GAPS = 2                 # gaps the next round searches (the most important first)
RESEARCH_CHECKS = 2               # key facts one site states that the next round looks for on other sites
SAME_CLAIM = 0.8                  # embedding similarity at which two facts make the same claim
                                  # (qwen3-embedding: paraphrases 0.86 and up, unrelated facts 0.67 and down)
REPEAT_CLAIM = 0.9                # ... at which one fact repeats another (kept once, with the pages of both)
LANGUAGE_SEARCHES = 6             # searches of a language besides its tools
LANGUAGE_PAGES = 14               # pages read per language
LANGUAGE_COMMANDS = 8             # commands that show what of a language and its tools is installed here
RECALL_TOPICS = 6                 # learned topics one cycle uses at most
TOPIC_RELEVANT = 0.6              # embedding similarity of a request and a topic it needs (qwen3-embedding:


class Home:
    """Tinker's own folder next to the script (.tinker): config.json, models/, languages/,
    memory.json, learned/, embeddings.json, learn.jsonl, commands.json and runs/. Made on first use; tinker reset
    deletes it."""

    @staticmethod
    def reset():
        """tinker reset: delete Tinker's folder next to the script (settings, measured models, lessons,
        what was learned from the web, the run index), after asking; the next command starts fresh.
        Projects keep their own .tinker folders, so tinker resume works inside them."""
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
            raise SystemExit("Nothing deleted: run tinker reset in a terminal to confirm.")
        if input("Delete it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return
        shutil.rmtree(HOME)
        print(f"Deleted {HOME}. The next command starts fresh.")

    @staticmethod
    def parts():
        """What tinker clear can delete: {name: (its paths, what it is)}."""
        return {"languages": ([LANGUAGES_DIR], "per language: the toolchain that worked, its fixes, its lessons"),
                "memory": ([MEMORY_FILE], "lessons that hold for any language"),
                "learned": ([LEARNED_DIR, EMBEDDINGS_FILE, LEARN_LOG], "what tinker learn learned, and its index"),
                "models": ([MODELS_DIR], "what calibration measured for each model"),
                "runs": ([RUNS_DIR], "the index of runs (projects keep their own .tinker)"),
                "commands": ([COMMANDS_FILE], "your commands Tinker may use")}

    @staticmethod
    def clear(names, yes=False):
        """tinker clear PART...: delete those parts of what Tinker keeps, after asking (yes: without
        asking); everything else stays."""
        parts = Home.parts()
        unknown = [n for n in names if n not in parts]
        if unknown:
            raise SystemExit(f"tinker clear: no part {', '.join(unknown)}; the parts are: {', '.join(parts)}")
        paths = [path for name in dict.fromkeys(names) for path in parts[name][0] if path.exists()]
        if not paths:
            print("Nothing to clear: " + ", ".join(names) + " " + ("is" if len(names) == 1 else "are") + " empty.")
            return
        print("This deletes:")
        for path in paths:
            print(f"  {path.relative_to(HOME.parent)}{'/' if path.is_dir() else ''}")
        if not yes:
            if not sys.stdin.isatty():
                raise SystemExit("Nothing deleted: run it in a terminal to confirm, or add --yes.")
            if input("Delete them? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing deleted.")
                return
        for path in paths:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        print(f"Cleared {', '.join(dict.fromkeys(names))}.")

    @staticmethod
    def keep_awake():
        """Keep the computer from sleeping while Tinker works (it stops with Tinker): on macOS with
        caffeinate, on Linux with systemd-inhibit; elsewhere nothing is done. Idle sleep would pause
        a run for as long as nobody touches the computer."""
        pid = str(os.getpid())
        if shutil.which("caffeinate"):
            command = ["caffeinate", "-i", "-s", "-w", pid]  # -s: no system sleep either, on mains power
        elif shutil.which("systemd-inhibit"):
            command = ["systemd-inhibit", "--what=idle:sleep", "--why=Tinker is working", "tail", f"--pid={pid}",
                       "-f", "/dev/null"]
        else:
            return
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError:
            pass  # staying awake helps, it is not needed

    @staticmethod
    def ensure():
        """Tinker's folder next to the script, with config.json written with the defaults on first use."""
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            if not CONFIG_FILE.is_file():
                Config.save()
                print(f"  created {CONFIG_FILE} (default model, Ollama's address, keep_alive)")
        except OSError as exc:
            raise SystemExit(f"Tinker keeps its settings in {HOME}, which cannot be written: {exc}")


class Config:
    """config.json in Tinker's folder: DEFAULT_CONFIG, overridden by what the file holds
    (read once, when first needed)."""
    KEEP_ALIVE_RE = re.compile(r"-?\d+|-?(\d+(\.\d+)?(ns|us|ms|s|m|h))+")  # seconds, or a duration: 10m, 1h30m
    values = None

    @classmethod
    def save(cls, **changes):
        """The settings, with these changes, written to config.json."""
        cls.load().update(changes)
        JsonFile.write(CONFIG_FILE, cls.values)

    @staticmethod
    def check_model(run):
        """The settings a model takes from config.json (Tinker decides them; a person may tune them
        there), and for a run also its tests' timeout: checked before anything starts. A command
        that loads no model never reads them."""
        if not Config.KEEP_ALIVE_RE.fullmatch(str(Config.get("keep_alive"))):
            raise SystemExit(f"keep_alive in {CONFIG_FILE} must be seconds (0, 300; -1 keeps the model loaded) or a "
                             f"duration (10m, 1h); it is {Config.get('keep_alive')!r}")
        growth = Config.get("max_growth")
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
        terminal width (a wrapped line cannot be redrawn with \\r); written to a file, it is whole."""
        width = shutil.get_terminal_size((80, 24)).columns if sys.__stdout__.isatty() else 1000
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
