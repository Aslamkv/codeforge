"""Tinker's settings: where it keeps what it knows (its home and its database), the limits it works
within, and the small helpers every part uses."""

import contextlib
import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_CONFIG = {                      # the settings: what the home's "config" holds overrides these
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
    "projects": "~/Projects",           # where tinker task makes a new project's own folder (named after the
                                        # task), unless --workdir names one
}

# --- each project's own state ------------------------------------------------------------------
STATE_DIR = ".tinker"             # in each project: its database (project.db) and a started server's output
PROJECT_DB = "project.db"
ENV_FILE, ENV_EXAMPLE = ".env", ".env.example"

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
PLANNING_PHASES = ("recall", "spec", "design", "review")  # the phases that make the plan
CONFIRM_AFTER = {"feature": "review", "fix": "reproduce"}  # by cycle kind: where its plan waits for the person
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

# --- learning: what earlier projects taught (Tinker's "languages" and "memory") ---------------
LESSON_PHASES = ("design", "config", "tests", "code", "toolchain")  # where a lesson is shown
MAX_LESSONS = 15                  # per file; the least confirmed go first
LESSONS_SHOWN = 5                 # per prompt (an optional part: dropped first when the prompt is full)
SAME_LESSON = 0.75                # text similarity at which a new lesson is an old one seen again
SAME_FACT = 0.75                  # text similarity at which a fact learned from the web is a repeat
SIMILAR_FAILURE = 0.7             # similarity at which a past toolchain fix is shown as a hint

# --- learning from the web: tinker learn TOPIC, and the recall phase of a run --------------------------
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
                                  # the topic a request is about 0.65 and up, unrelated topics 0.52 and down)


class Store:
    """One SQLite database: documents (JSON values) by collection and key, and logs of entries. What
    is written is written whole or not at all, and several Tinkers may use one database at once."""
    SCHEMA = ("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
              "PRIMARY KEY (collection, key))",
              "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
              "time TEXT NOT NULL, entry TEXT NOT NULL)")
    WAIT = 30  # seconds to wait for another Tinker's write

    def __init__(self, path):
        self.path, self.made = Path(path), False

    @contextlib.contextmanager
    def transaction(self, write=False):
        """A connection, in one transaction (a write's: taken at once, so a read-modify-write is whole)."""
        try:
            db = self.connect()
        except sqlite3.Error as exc:
            raise SystemExit(f"{self.path} cannot be read ({exc})") from None
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.execute("COMMIT")
        except sqlite3.DatabaseError as exc:  # not a database (any more), or locked past WAIT
            raise SystemExit(f"{self.path} cannot be read ({exc})") from None
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def connect(self):
        """A connection; the database is made on first use, readable by its owner only."""
        if not self.path.exists():  # new, or deleted since (tinker reset)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
            self.made = False
        db = sqlite3.connect(self.path, timeout=self.WAIT, isolation_level=None)
        if not self.made:
            for statement in self.SCHEMA:
                db.execute(statement)
            self.made = True
        return db

    def get(self, collection, key, default=None):
        with self.transaction() as db:
            row = db.execute("SELECT value FROM docs WHERE collection = ? AND key = ?", (collection, key)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, collection, key, value):
        with self.transaction(write=True) as db:
            self.write(db, collection, key, value)

    @staticmethod
    def write(db, collection, key, value):
        db.execute("INSERT INTO docs (collection, key, value) VALUES (?, ?, ?) ON CONFLICT (collection, key) "
                   "DO UPDATE SET value = excluded.value", (collection, key, json.dumps(value, ensure_ascii=False)))

    def update(self, collection, key, change, default=None):
        """The document changed by change(value) -> value, read and written in one transaction (no other
        Tinker writes in between); the new value."""
        with self.transaction(write=True) as db:
            row = db.execute("SELECT value FROM docs WHERE collection = ? AND key = ?", (collection, key)).fetchone()
            value = change(json.loads(row[0]) if row else default)
            self.write(db, collection, key, value)
        return value

    def delete(self, collection, key=None):
        """One document, or (key None) the whole collection."""
        with self.transaction(write=True) as db:
            if key is None:
                db.execute("DELETE FROM docs WHERE collection = ?", (collection,))
            else:
                db.execute("DELETE FROM docs WHERE collection = ? AND key = ?", (collection, key))

    def items(self, collection):
        """{key: value} of a collection, in the order they were first written."""
        with self.transaction() as db:
            rows = db.execute("SELECT key, value FROM docs WHERE collection = ? ORDER BY rowid", (collection,)).fetchall()
        return {key: json.loads(value) for key, value in rows}

    def count(self, collection):
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM docs WHERE collection = ?", (collection,)).fetchone()[0]

    def append(self, log, entry):
        """An entry added to a log, with its time; its id."""
        with self.transaction(write=True) as db:
            return db.execute("INSERT INTO logs (name, time, entry) VALUES (?, ?, ?)",
                              (log, time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(entry, ensure_ascii=False))).lastrowid

    def entries(self, log, after=0):
        """[(id, entry with its "time")] of a log after an id, in order."""
        with self.transaction() as db:
            rows = db.execute("SELECT id, time, entry FROM logs WHERE name = ? AND id > ? ORDER BY id",
                              (log, after)).fetchall()
        return [(row_id, {"time": stamp, **json.loads(entry)}) for row_id, stamp, entry in rows]

    def drop_log(self, log):
        with self.transaction(write=True) as db:
            db.execute("DELETE FROM logs WHERE name = ?", (log,))

    def logged(self, log):
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM logs WHERE name = ?", (log,)).fetchone()[0]


class Log:
    """One log of a database (a run's model calls, tinker learn's): add(event) appends to it."""

    def __init__(self, store, name):
        self.store, self.name = store, name

    def add(self, event):
        self.store.append(self.name, event)


class Home:
    """Tinker's own folder next to the script (.tinker): its database, tinker.db (settings, measured
    models, what it knows, the projects it points to, the channels), and the channels' lock files.
    Only Home knows where it is; tinker reset deletes it."""
    root = Path(__file__).resolve().parent.parent / STATE_DIR  # next to the script (its real file, not a link)
    stores = {}

    @classmethod
    def store(cls):
        path = cls.root / "tinker.db"
        return cls.stores.setdefault(path, Store(path))

    @classmethod
    def lock_file(cls, name):
        """The lock a Tinker holds while it listens on a channel (released when its process ends)."""
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / f"channel-{name}.lock"

    @classmethod
    def reset(cls, yes=False):
        """tinker reset: delete Tinker's folder next to the script (settings, measured models, what it
        learned, the projects it points to), after asking (yes: without); the next command starts fresh.
        Projects keep their own .tinker folders, so tinker resume works inside them."""
        home = cls.root
        if not home.is_dir():
            print(f"Nothing to reset: {home} does not exist.")
            return
        if home.name != STATE_DIR:
            raise SystemExit(f"Not deleting {home}: it is not a .tinker folder.")
        print(f"This deletes {home}: Tinker's settings, measured models, what it knows and its list of projects.")
        print("Projects are not touched (each keeps its own .tinker folder).")
        if not yes and not sys.stdin.isatty():
            raise SystemExit("Nothing deleted: run tinker reset in a terminal to confirm, or add --yes.")
        if not yes and input("Delete it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return
        shutil.rmtree(home)
        cls.stores.pop(home / "tinker.db", None)
        print(f"Deleted {home}. The next command starts fresh.")

    @staticmethod
    def parts():
        """What tinker clear can delete: {name: (its collections, its logs, what it is)}."""
        return {"languages": (["languages"], [], "per language: the toolchain that worked, its fixes, its lessons"),
                "memory": (["memory"], [], "lessons that hold for any language"),
                "learned": (["learned", "embeddings"], ["learn"], "what tinker learn learned, and its index"),
                "models": (["models"], [], "what calibration measured for each model"),
                "runs": (["projects"], [], "the list of projects (projects keep their own .tinker)"),
                "commands": (["commands"], [], "your commands Tinker may use")}

    @classmethod
    def clear(cls, names, yes=False):
        """tinker clear PART...: delete those parts of what Tinker keeps, after asking (yes: without
        asking); everything else stays."""
        parts = cls.parts()
        unknown = [n for n in names if n not in parts]
        if unknown:
            raise SystemExit(f"tinker clear: no part {', '.join(unknown)}; the parts are: {', '.join(parts)}")
        names, store = list(dict.fromkeys(names)), cls.store()
        held = {name: sum(store.count(c) for c in parts[name][0]) + sum(store.logged(log) for log in parts[name][1])
                for name in names}
        if not any(held.values()):
            print("Nothing to clear: " + ", ".join(names) + " " + ("is" if len(names) == 1 else "are") + " empty.")
            return
        print("This deletes:")
        for name in names:
            print(f"  {name}: {parts[name][2]} ({held[name]} entries)")
        if not yes:
            if not sys.stdin.isatty():
                raise SystemExit("Nothing deleted: run it in a terminal to confirm, or add --yes.")
            if input("Delete them? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing deleted.")
                return
        for name in names:
            for collection in parts[name][0]:
                store.delete(collection)
            for log in parts[name][1]:
                store.drop_log(log)
        print(f"Cleared {', '.join(names)}.")

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

    @classmethod
    def ensure(cls):
        """Tinker's folder next to the script, with its database, made on first use."""
        try:
            new = not (cls.root / "tinker.db").exists()
            cls.store().connect().close()
            if new:
                print(f"  created {cls.root / 'tinker.db'} (Tinker's settings and memory)")
        except (OSError, SystemExit, sqlite3.Error) as exc:
            raise SystemExit(f"Tinker keeps its settings in {cls.root}, which cannot be written: {exc}")


class Config:
    """The settings: DEFAULT_CONFIG, overridden by the home's "config" (read each time, so what another
    Tinker changed is seen at once). They change through Tinker's commands only: use-model, embed,
    use-web, and tinker config KEY VALUE for the rest."""
    KEEP_ALIVE_RE = re.compile(r"-?\d+|-?(\d+(\.\d+)?(ns|us|ms|s|m|h))+")  # seconds, or a duration: 10m, 1h30m
    SETTABLE = ("ollama_url", "keep_alive", "max_growth", "build_think", "test_timeout", "web_fallback",
                "web_timeout", "learn_missing", "projects")  # the keys with no command of their own
    checks = []  # more checks of the settings, from the parts that use them (the Internet plugin's)

    @staticmethod
    def load():
        return {**DEFAULT_CONFIG, **Home.store().items("config")}

    @staticmethod
    def save(**changes):
        for key, value in changes.items():
            Home.store().put("config", key, value)

    @classmethod
    def get(cls, key):
        return Home.store().get("config", key, DEFAULT_CONFIG[key])

    @classmethod
    def set(cls, key, text):
        """tinker config KEY VALUE: one setting, read by its type and checked; kept only when it passes."""
        if key not in cls.SETTABLE:
            commands = {"model": "use-model", "embed_model": "embed", "web": "use-web"}
            raise SystemExit(f"tinker config: {key} is set with tinker {commands[key]} NAME" if key in commands else
                             f"tinker config: no setting {key!r}; the settings are: {', '.join(cls.SETTABLE)}")
        value = cls.parse(key, text.strip())
        before = Home.store().get("config", key)
        cls.save(**{key: value})
        try:
            cls.check_model(run=True)
            for check in cls.checks:
                check()
        except SystemExit:
            Home.store().delete("config", key) if before is None else cls.save(**{key: before})
            raise
        return value

    @staticmethod
    def parse(key, text):
        """A setting's value from its text: numbers as numbers, true/false/null as such, else the text."""
        known = {"true": True, "false": False, "null": None}
        if text.lower() in known and key == "build_think":
            return known[text.lower()]
        if isinstance(DEFAULT_CONFIG[key], (int, float)) and not isinstance(DEFAULT_CONFIG[key], bool):
            for kind in (int, float):
                try:
                    return kind(text)
                except ValueError:
                    continue
        return text

    @staticmethod
    def check_model(run):
        """The settings a model takes (Tinker decides them; a person may tune them with tinker config),
        and for a run also its tests' timeout: checked before anything starts. A command that loads no
        model never reads them."""
        if not Config.KEEP_ALIVE_RE.fullmatch(str(Config.get("keep_alive"))):
            raise SystemExit(f"keep_alive must be seconds (0, 300; -1 keeps the model loaded) or a duration (10m, "
                             f"1h); it is {Config.get('keep_alive')!r} (tinker config keep_alive VALUE)")
        growth = Config.get("max_growth")
        if isinstance(growth, bool) or not isinstance(growth, (int, float)) or growth <= 1:
            raise SystemExit(f"max_growth must be a number above 1 (1.15 unloads the model once it is 15% larger); "
                             f"it is {growth!r}")
        if Config.get("build_think") not in ("high", "medium", "low", True, False, None) \
                or Config.get("build_think") in (0, 1) and not isinstance(Config.get("build_think"), bool):
            raise SystemExit(f'build_think must be "high", "medium", "low", true, false or null; it is '
                             f"{Config.get('build_think')!r}")
        timeout = Config.get("test_timeout")
        if run and (isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1):
            raise SystemExit(f"test_timeout must be a whole number of seconds, 1 or more; it is {timeout!r}")

    @classmethod
    def ollama_url(cls):
        return str(cls.get("ollama_url")).rstrip("/")

    @staticmethod
    def keep_alive(value):
        """keep_alive as Ollama takes it: seconds as a number, a duration (10m) as it is."""
        return int(value) if re.fullmatch(r"-?\d+", str(value)) else value


class Console:
    """What Tinker prints: each step with when it started and how long it took, a live progress
    line (on the terminal only), and every whole line given to whoever keeps the run's output."""

    @staticmethod
    def mirror(see):
        """Every whole line printed from now on also given to see(line) (a run keeps its output so)."""
        sys.stdout = Tee(sys.stdout, see)
        sys.stderr = Tee(sys.stderr, see)

    @staticmethod
    def step(message, started_at):
        """[start clock time, duration readable and in seconds] message"""
        elapsed = time.perf_counter() - started_at
        began = datetime.datetime.now() - datetime.timedelta(seconds=elapsed)
        print(f"[{began:%H:%M:%S} {human_duration(elapsed):>7} ({elapsed:.2f}s)] {message}")

    @staticmethod
    def progress(text):
        """Redraw one status line in place, on the terminal only (never kept in the run's output). Cut to
        the terminal width (a wrapped line cannot be redrawn with \\r); written to a file, it is whole."""
        width = shutil.get_terminal_size((80, 24)).columns if sys.__stdout__.isatty() else 1000
        sys.__stdout__.write("\r" + text[:max(width - 1, 10)] + "\033[K")
        sys.__stdout__.flush()

    @staticmethod
    def clear_progress():
        sys.__stdout__.write("\r\033[K")
        sys.__stdout__.flush()


class Tee:
    """Writes to a stream, like `tee`, and gives each whole line to see(line) (which may never break the
    writing)."""

    def __init__(self, stream, see):
        self.stream, self.see, self.partial = stream, see, ""

    def write(self, text):
        self.stream.write(text)
        self.partial += text
        *lines, self.partial = self.partial.split("\n")
        for line in lines:
            try:
                self.see(line)
            except Exception:  # keeping the output must never break the run
                pass
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    def __getattr__(self, name):
        return getattr(self.stream, name)


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
