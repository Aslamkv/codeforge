"""Tinker's settings: where it keeps what it knows (its home and its database), the limits it works
within, and the small helpers every part uses."""

import contextlib
lazy import difflib
lazy import hashlib
lazy import json
lazy import os
import re
lazy import shutil
lazy import sqlite3
lazy import subprocess
lazy import sys
lazy import time
from pathlib import Path

lazy from .messages import say


DEFAULT_CONFIG = frozendict({
    "model": None,
    "ollama_url": "http://localhost:11434",
    "keep_alive": 0,
    "max_growth": 1.15,
    "build_think": "medium",
    "test_timeout": 60,
    "web": "ollama",
    "web_fallback": "",
    "web_timeout": 30,
    "learn_missing": 2,
    "learn_rate": 8,
    "learn_backoff": 20,
    "learn_cutoff": 0.0,
    "learn_pages": 8,
    "learn_min_pages": 2,
    "learn_searches": 3,
    "learn_think": "low",
    "docs_pages": 30,
    "tool_docs_pages": 5,
    "docs_latest_days": 30,
    "fetch_workers": 6,
    "fetch_timeout": 15,
    "fetch_max_bytes": 2000000,
    "fetch_domain_delay": 0.1,
    "fetch_fallbacks": 2,
    "embed_model": "",
    "projects": "~/Projects",
})

STATE_DIR = ".tinker"
PROJECT_DB = "project.db"
ENV_FILE, ENV_EXAMPLE = ".env", ".env.example"

PHASES = (
    "recall",
    "spec",
    "design",
    "review",
    "setup",
    "tests",
    "code",
    "verify",
    "tidy",
    "docs",
    "final_review",
    "lessons",
    "done")
FIX_PHASES = ("recall", "reproduce", "verify", "final_review", "lessons", "done")
PLANNING_PHASES = ("recall", "spec", "design", "review")
CONFIRM_AFTER = frozendict({"feature": "review", "fix": "reproduce"})
FORMATTER_RUNS_AFTER = ("verify",)
STATUS_TEXT = frozendict({"green": "passes", "todo": "to do", "active": "in progress", "blocked": "parked"})
KINDS = ("code", "test", "template", "config", "docs")
MAX_FILE_LINES = 60
TESTS_PER_CALL = 10

DEFAULT_MAX_STALL = 3
MAX_CALLS_PER_CRITERION_PASS = 8
STALLED_REVIEW_TRIES = 2
BROKEN_BUILD_FIXES = 3
FAILURE_OUTPUT_CHARS = 1600
TESTS_LOCKED_REASON = "tests do not change now"
FILES_SHOWN_PER_FIX = 7

NETWORK_RETRY_SECONDS = 30
ONLINE_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443))
NETWORK_WAIT_NOTE_SECONDS = 600
SETUP_TIMEOUT = 900
COMMAND_TIMEOUT = 300

STARTING_CHARS_PER_TOKEN = 2.8
MIN_CHARS_PER_TOKEN = 2.0
MAX_CHARS_PER_TOKEN = 4.0
PROMPT_BUDGET_SHARE = 0.95
MIN_TRIMMED_PROSE_CHARS = 300
MIN_PROMPT_TOKENS = 512

MIN_REPLY_TOKENS = 1024
LAST_CHUNK_WAIT_SECONDS = 3.0
PROGRESS_UPDATE_SECONDS = 2.0
LOOPING_REPEATED_LINES = 20

FIX_MAX_TOKENS = 4096
TEST_FILE_MAX_TOKENS = 8192
CHECK_MAX_TOKENS = 12288
UNFINISHED_THOUGHT_KEPT_CHARS = 4000

OLLAMA_SILENCE_TIMEOUT = 600
STALL_RETRIES = 2
OOM_RETRIES = 2
OOM_RETRY_PROMPT_SHARE = 0.75
REPLY_ERROR_RETRIES = 1
CRASH_RECOVERY_SECONDS = 10
CRASH_RETRIES = 3
CRASHED_LIMIT_SHARE = 0.9
CRASH_NEAR_LIMIT_SHARE = 0.5

DEFAULT_THINK = "high"
PROMPT_TARGET_TOKENS = 8192
REPLY_TARGET_TOKENS = 16384
CAPACITY_SEARCH_PRECISION = 0.1
RUNNER_OVERHEAD_BYTES = 1.5e9
PROBE_NUM_BATCH = 256

LESSON_PHASES = ("design", "config", "tests", "code", "toolchain")
MAX_LESSONS_PER_FILE = 15
LESSONS_PER_PROMPT = 5
SAME_LESSON_SIMILARITY = 0.75
SAME_FACT_SIMILARITY = 0.75
TOOLCHAIN_HINT_SIMILARITY = 0.7
EVENTS_KEPT_PER_ITEM = 20
RETIRED_KEPT_PER_LIST = 50
RUNS_KEPT = 200

SIMILAR_LESSON_MEANING = 0.75
SIMILAR_LESSON_WORDS = 0.5

MEMORY_ITEMS_SHOWN = 25
TEACH_CLAIMS = 4
TEACH_CHECK_COMMANDS = 3
TEACH_CHECK_SEARCHES = 3

READ_MAX_BYTES = 50_000_000
READ_MIN_CHAPTERS = 5
READ_WINDOW_CHARS = 20_000
READ_INDEX_TERMS = 40
READ_KEY_TERMS = 12
READ_FOCUS_CHAPTERS = 3
READ_CHAPTER_TEXT_SHARE = 0.6
READ_FOCUS_CHUNKS = 3
READ_FACTS_PER_CHAPTER = 8
BOOK_PASSAGES_SHOWN = 4

EXERCISE_FOLDER = "tinker-exercise"
EXERCISE_ENV = "TINKER_EXERCISE"
EXERCISE_PERSON_LOOK_SECONDS = 2
EARLIER_MEASURES_SHOWN = 8
REFLECTION_PROBLEMS = 12

FACT_PHASES = ("spec", "design", "config", "tests", "code", "toolchain")
MAX_VERSION_COMMANDS = 6
VERSION_TIMEOUT = 20
RESULTS_PER_RESEARCH_SEARCH = 3
CHUNKS_PER_PAGE = 2
FACTS_PER_CHUNK = 4
MAX_FACT_CHARS = 400
TOOL_ROLES = ("environment", "packages", "build", "tests", "format", "lint", "types")
PLAYBOOK_RULES = 16
MAX_RULE_CHARS = 200
MAX_RULE_WHEN_CHARS = 80

DOCS_SITE_SEARCH = "{topic} official documentation"
SITE_MAP_FILES = ("llms.txt", "sitemap.xml")
MAX_MAP_LINKS = 5000
RESULTS_PER_LEARN_SEARCH = 10
MIN_USABLE_PAGE_CHARS = 400
MAX_REDIRECTS = 5
FETCH_USER_AGENT = "Tinker/1.0 (a local coding agent learning from documentation)"
MAX_PASSAGE_CHARS = 1200
MENU_LINK_SHARE = 0.8
NEAR_DUPLICATE_OVERLAP = 0.8
SLOT_RELEVANT_SIMILARITY = 0.6
SLOT_CANDIDATE_PASSAGES = 60
MMR_BALANCE = 0.7
FACTS_PER_SLOT = 6
FIRST_BACKOFF_SECONDS = 1.0

RESEARCH_QUESTIONS = 4
SEARCHES_PER_RESEARCH_QUESTION = 2
RESEARCH_PAGES_PER_ROUND = (8, 6)
RESEARCH_GAPS = 2
RESEARCH_FACTS_CHECKED = 2
SAME_CLAIM_SIMILARITY = 0.8
REPEAT_CLAIM_SIMILARITY = 0.9
MAX_RECALL_TOPICS = 6
TOPIC_RELEVANT_SIMILARITY = 0.6


class Home:
    """Tinker's own folder next to the script (.tinker): its database, tinker.db (settings, measured
    models, what it knows, the projects it points to, the channels), and the channels' lock files.
    Only Home knows where it is; tinker reset deletes it."""
    root = Path(__file__).resolve().parent.parent / STATE_DIR
    stores = {}

    @classmethod
    def store(cls):
        return cls.stores.setdefault(cls.database(), Store(cls.database()))

    @classmethod
    def database(cls):
        return cls.root / "tinker.db"

    @classmethod
    def web_log(cls):
        """Where learning logs every web request it makes (one JSON line each: kind, result, seconds, address)."""
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / "web-requests.log"

    @classmethod
    def graph_page(cls):
        """The page tinker memory --graph writes: what Tinker knows, as a graph to open in a browser."""
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / "memory.html"

    @classmethod
    def reading_file(cls, name):
        """Where a file sent in a chat is kept for tinker read: its name, made safe, in the home's reading folder."""
        folder = cls.root / "reading"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (re.sub(r"[^\w.-]+", "-", Path(name).name).strip("-.") or "book")

    @classmethod
    def lock_file(cls, name):
        """The lock a Tinker holds while it listens on a channel (released when its process ends)."""
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / f"channel-{name}.lock"

    @classmethod
    def reset(cls, yes=False):
        """tinker reset: delete Tinker's folder next to the script, after asking (yes: without); the next
        command starts fresh. Projects keep their own .tinker folders, so tinker resume works inside them."""
        home = cls.root
        if not home.is_dir():
            say.info(f"Nothing to reset: {home} does not exist.")
            return
        if home.name != STATE_DIR:
            raise SystemExit(f"Not deleting {home}: it is not a .tinker folder.")
        say.warn(f"This deletes {home}: Tinker's settings, measured models, what it knows and its list of projects.")
        say.info("Projects are not touched (each keeps its own .tinker folder).", "📁")
        if not confirmed("Delete it?", "tinker reset", yes):
            say.info("Nothing deleted.")
            return
        shutil.rmtree(home)
        cls.stores.pop(cls.database(), None)
        say.done(f"Deleted {home}. The next command starts fresh.")

    @staticmethod
    def parts():
        """What tinker clear can delete: {name: (its collections, its logs, what it is)}."""
        return {"languages": (["languages"], [], "per language: the toolchain that worked, its fixes, its lessons"),
                "memory": (["memory"], [], "lessons that hold for any language"),
                "learned": (["learned", "embeddings"], ["learn"], "what tinker learn learned, and its index"),
                "docs": (["sources"], [], "the documentation pages tinker learn fetched, cleaned, per topic and version"),
                "models": (["models"], [], "what calibration measured for each model"),
                "runs": (["projects", "runs"], [], "the list of projects and a line about each run (projects keep "
                                                   "their own .tinker)"),
                "commands": (["commands"], [], "your commands Tinker may use"),
                "history": (["chat history", "chat history numbers"], [], "what the chats showed, and what you asked "
                                                                         "(their messages go from the chats in a day)"),
                "exercise": (["exercise"], ["exercise"], "the exercises: each language's practice projects, what they "
                                                         "measured, and where each stopped")}

    @classmethod
    def clear(cls, names, yes=False):
        """tinker clear PART...: delete those parts of what Tinker keeps, after asking (yes: without
        asking); everything else stays."""
        names = cls.known_parts(names)
        held = {name: cls.entries_in(name) for name in names}
        if not any(held.values()):
            say.info("Nothing to clear: " + ", ".join(names) + " " + ("is" if len(names) == 1 else "are") + " empty.")
            return
        cls.show_what_clearing_deletes(held)
        if not confirmed("Delete them?", "it", yes):
            say.info("Nothing deleted.")
            return
        for name in names:
            cls.delete_part(name)
        say.done(f"Cleared {', '.join(names)}.")

    @classmethod
    def known_parts(cls, names):
        unknown = [name for name in names if name not in cls.parts()]
        if unknown:
            raise SystemExit(f"tinker clear: no part {', '.join(unknown)}; the parts are: {', '.join(cls.parts())}")
        return list(dict.fromkeys(names))

    @classmethod
    def entries_in(cls, name):
        collections, logs, _ = cls.parts()[name]
        store = cls.store()
        return sum(store.count(collection) for collection in collections) + sum(store.logged(log) for log in logs)

    @classmethod
    def show_what_clearing_deletes(cls, held):
        say.warn("This deletes:")
        with say.section():
            for name, entries in held.items():
                say.item(f"{name}: {cls.parts()[name][2]} ({entries} entries)")

    @classmethod
    def delete_part(cls, name):
        collections, logs, _ = cls.parts()[name]
        for collection in collections:
            cls.store().delete(collection)
        for log in logs:
            cls.store().drop_log(log)

    @classmethod
    def keep_awake(cls):
        """Keep the computer from sleeping while Tinker works (it stops with Tinker). Idle sleep would
        pause a run for as long as nobody touches the computer."""
        command = cls.stay_awake_command(str(os.getpid()))
        if not command:
            return
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError:
            pass  # staying awake helps, it is not needed

    @staticmethod
    def stay_awake_command(pid):
        """The command that keeps this computer awake while the process pid lives: caffeinate on macOS,
        systemd-inhibit on Linux; None elsewhere."""
        if shutil.which("caffeinate"):
            return ["caffeinate", "-i", "-s", "-w", pid]  # -s: no system sleep either, on mains power
        if shutil.which("systemd-inhibit"):
            return ["systemd-inhibit", "--what=idle:sleep", "--why=Tinker is working", "tail", f"--pid={pid}",
                    "-f", "/dev/null"]
        return None

    @classmethod
    def ensure(cls):
        """Tinker's folder next to the script, with its database, made on first use."""
        try:
            new = not cls.database().exists()
            cls.store().connect().close()
            if new:
                say.done(f"Created {cls.database()} (Tinker's settings and memory)")
        except (OSError, SystemExit, sqlite3.Error) as exc:
            raise SystemExit(f"Tinker keeps its settings in {cls.root}, which cannot be written: {exc}")


class Config:
    """The settings: DEFAULT_CONFIG, overridden by the home's "config" (read each time, so what another
    Tinker changed is seen at once). They change through Tinker's commands only: use-model, embed,
    use-web, and tinker config KEY VALUE for the rest."""
    KEEP_ALIVE_RE = re.compile(r"-?\d+|-?(\d+(\.\d+)?(ns|us|ms|s|m|h))+")  # seconds, or a duration: 10m, 1h30m
    SETTABLE = ("ollama_url", "keep_alive", "max_growth", "build_think", "test_timeout", "web_fallback",
                "web_timeout", "learn_missing", "learn_rate", "learn_backoff", "learn_cutoff", "learn_pages",
                "learn_min_pages", "learn_searches", "learn_think", "docs_pages", "tool_docs_pages",
                "docs_latest_days", "fetch_workers", "fetch_timeout", "fetch_max_bytes",
                "fetch_domain_delay", "fetch_fallbacks", "projects")
    OWN_COMMAND = {"model": "use-model", "embed_model": "embed", "web": "use-web"}
    checks = []

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
        cls.refuse_unsettable(key)
        value = cls.parse(key, text.strip())
        before = Home.store().get("config", key)
        cls.save(**{key: value})
        try:
            cls.check_all()
        except SystemExit:
            cls.restore(key, before)
            raise
        return value

    @classmethod
    def refuse_unsettable(cls, key):
        if key in cls.OWN_COMMAND:
            raise SystemExit(f"tinker config: {key} is set with tinker {cls.OWN_COMMAND[key]} NAME")
        if key not in cls.SETTABLE:
            raise SystemExit(f"tinker config: no setting {key!r}; the settings are: {', '.join(cls.SETTABLE)}")

    @staticmethod
    def parse(key, text):
        """A setting's value from its text: numbers as numbers, true/false/null as such, else the text."""
        known = {"true": True, "false": False, "null": None}
        if text.lower() in known and key in ("build_think", "learn_think"):
            return known[text.lower()]
        if is_number(DEFAULT_CONFIG[key]):
            for kind in (int, float):
                try:
                    return kind(text)
                except ValueError:
                    continue
        return text

    @classmethod
    def check_all(cls):
        cls.check_model(run=True)
        for check in cls.checks:
            check()

    @classmethod
    def restore(cls, key, before):
        if before is None:
            Home.store().delete("config", key)
        else:
            cls.save(**{key: before})

    @classmethod
    def check_model(cls, run):
        """The settings a model takes (Tinker decides them; a person may tune them with tinker config),
        and for a run also its tests' timeout: checked before anything starts. A command that loads no
        model never reads them."""
        cls.check_keep_alive()
        cls.check_max_growth()
        cls.check_build_think()
        if run:
            cls.check_test_timeout()

    @classmethod
    def check_keep_alive(cls):
        if not cls.KEEP_ALIVE_RE.fullmatch(str(cls.get("keep_alive"))):
            raise SystemExit(f"keep_alive must be seconds (0, 300; -1 keeps the model loaded) or a duration (10m, "
                             f"1h); it is {cls.get('keep_alive')!r} (tinker config keep_alive VALUE)")

    @classmethod
    def check_max_growth(cls):
        growth = cls.get("max_growth")
        if not is_number(growth) or growth <= 1:
            raise SystemExit(f"max_growth must be a number above 1 (1.15 unloads the model once it is 15% larger); "
                             f"it is {growth!r}")

    @classmethod
    def check_build_think(cls):
        for key in ("build_think", "learn_think"):
            think = cls.get(key)
            if not cls.is_think_setting(think):
                raise SystemExit(f'{key} must be "high", "medium", "low", true, false or null; it is {think!r}')

    @staticmethod
    def is_think_setting(think):
        """Whether think is one Ollama takes: a level, true, false or null (0 and 1 equal false and true, but are
        not them)."""
        return think in ("high", "medium", "low", True, False, None) and not (think in (0, 1)
                                                                             and not isinstance(think, bool))

    @classmethod
    def check_test_timeout(cls):
        timeout = cls.get("test_timeout")
        if not number_from(timeout, 1):
            raise SystemExit(f"test_timeout must be a whole number of seconds, 1 or more; it is {timeout!r}")

    @classmethod
    def ollama_url(cls):
        return str(cls.get("ollama_url")).rstrip("/")

    @staticmethod
    def keep_alive(value):
        """keep_alive as Ollama takes it: seconds as a number, a duration (10m) as it is."""
        return int(value) if re.fullmatch(r"-?\d+", str(value)) else value


def confirmed(question, command, yes=False):
    """Whether the person agrees to what was just shown (yes: agreed beforehand). Only a terminal can
    ask: elsewhere nothing is done, and it says how to agree."""
    if yes:
        return True
    if not sys.stdin.isatty():
        raise SystemExit(f"Nothing deleted: run {command} in a terminal to confirm, or add --yes.")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number_from(value, least, whole=True):
    """Whether a setting is a number (a whole one, when whole) of least or more."""
    return is_number(value) and (not whole or isinstance(value, int)) and not value < least


class Store:
    """One SQLite database: documents (JSON values) by collection and key, and logs of entries. What
    is written is written whole or not at all, and several Tinkers may use one database at once."""
    SCHEMA = ("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
              "PRIMARY KEY (collection, key))",
              "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
              "time TEXT NOT NULL, entry TEXT NOT NULL)")
    LOCK_WAIT_SECONDS = 30

    def __init__(self, path):
        self.path, self.made = Path(path), False

    def get(self, collection, key, default=None):
        with self.transaction() as db:
            return self.read(db, collection, key, default)

    def put(self, collection, key, value):
        with self.transaction(write=True) as db:
            self.write(db, collection, key, value)

    def update(self, collection, key, change, default=None):
        """The document changed by change(value) -> value, read and written in one transaction (no other
        Tinker writes in between); the new value."""
        with self.transaction(write=True) as db:
            value = change(self.read(db, collection, key, default))
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

    @contextlib.contextmanager
    def transaction(self, write=False):
        """A connection, in one transaction (a write's: taken at once, so a read-modify-write is whole)."""
        db = self.opened()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.execute("COMMIT")
        except sqlite3.DatabaseError as exc:  # not a database (any more), or locked past LOCK_WAIT_SECONDS
            raise self.unreadable(exc) from None
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def opened(self):
        try:
            return self.connect()
        except sqlite3.Error as exc:
            raise self.unreadable(exc) from None

    def unreadable(self, exc):
        return SystemExit(f"{self.path} cannot be read ({exc})")

    def connect(self):
        """A connection; the database is made on first use, readable by its owner only."""
        if not self.path.exists():  # new, or deleted since (tinker reset)
            self.create_private_file()
        elif not self.made:  # one made before it was kept private
            self.path.chmod(0o600)
        db = sqlite3.connect(self.path, timeout=self.LOCK_WAIT_SECONDS, isolation_level=None)
        if not self.made:
            self.create_tables(db)
        return db

    def create_private_file(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        self.made = False

    def create_tables(self, db):
        for statement in self.SCHEMA:
            db.execute(statement)
        self.made = True

    @staticmethod
    def read(db, collection, key, default):
        row = db.execute("SELECT value FROM docs WHERE collection = ? AND key = ?", (collection, key)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def write(db, collection, key, value):
        db.execute("INSERT INTO docs (collection, key, value) VALUES (?, ?, ?) ON CONFLICT (collection, key) "
                   "DO UPDATE SET value = excluded.value", (collection, key, json.dumps(value, ensure_ascii=False)))


class Log:
    """One log of a database (a run's model calls, tinker learn's): add(event) appends to it."""

    def __init__(self, store, name):
        self.store, self.name = store, name

    def add(self, event):
        self.store.append(self.name, event)


def projects_folder():
    """Where new projects get their own folders (tinker config projects)."""
    return Path(str(Config.get("projects") or "~/Projects")).expanduser().resolve()


def first_line(text, limit=140):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[:limit - 3] + "..."


def now_text():
    return time.strftime("%Y-%m-%d %H:%M")


def clock(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


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
