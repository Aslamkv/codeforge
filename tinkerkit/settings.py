import contextlib
import re
from collections import namedtuple
from pathlib import Path
lazy import difflib
lazy import hashlib
lazy import json
lazy import os
lazy import shutil
lazy import sqlite3
lazy import subprocess
lazy import sys
lazy import time

lazy from .messages import say

DEFAULT_CONFIG = frozendict(
    {
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
    }
)
WEB_BACKENDS = ("ollama", "duckduckgo")


class Setting(namedtuple("Setting", "kind owner least whole above", defaults=("config", None, False, False))):
    __slots__ = ()
    SECONDS_OR_DURATION_RE = re.compile(r"-?\d+|-?(\d+(\.\d+)?(ns|us|ms|s|m|h))+")
    THINK_WORDS = frozendict({"true": True, "false": False, "null": None})

    def read(self, text):
        if self.kind == "think" and text.lower() in self.THINK_WORDS:
            return self.THINK_WORDS[text.lower()]
        if self.kind == "duration" and re.fullmatch(r"-?\d+", text):
            return int(text)
        if self.kind == "number":
            for kind in (int, float):
                try:
                    return kind(text)
                except ValueError:
                    continue
        return text

    def fits(self, value):
        match self.kind:
            case "number":
                return self.in_range(value)
            case "duration":
                return bool(self.SECONDS_OR_DURATION_RE.fullmatch(str(value)))
            case "think":
                return value in ("high", "medium", "low", True, False, None) and not (
                    value in (0, 1) and not isinstance(value, bool)
                )
            case "backend":
                return value in ("", *WEB_BACKENDS)
        return value is None or isinstance(value, str)

    def in_range(self, value):
        if not is_number(value) or (self.whole and not isinstance(value, int)):
            return False
        return value > self.least if self.above else value >= self.least

    def described(self):
        match self.kind:
            case "number":
                whole = "a whole number" if self.whole else "a number"
                return f"{whole} above {self.least}" if self.above else f"{whole}, {self.least} or more"
            case "duration":
                return "seconds (0, 300; -1 keeps the model loaded) or a duration (10m, 1h)"
            case "think":
                return '"high", "medium", "low", true, false or null'
            case "backend":
                return "one of " + ", ".join(map(repr, WEB_BACKENDS)) + ' or "" (none)'
        return "text"


SETTINGS = frozendict(
    {
        "model": Setting("text", "use-model"),
        "ollama_url": Setting("text"),
        "keep_alive": Setting("duration"),
        "max_growth": Setting("number", least=1, above=True),
        "build_think": Setting("think"),
        "test_timeout": Setting("number", least=1, whole=True),
        "web": Setting("backend", "use-web"),
        "web_fallback": Setting("backend"),
        "web_timeout": Setting("number", least=0, above=True),
        "learn_missing": Setting("number", least=1, whole=True),
        "learn_rate": Setting("number", least=1),
        "learn_backoff": Setting("number", least=0),
        "learn_cutoff": Setting("number", least=0),
        "learn_pages": Setting("number", least=1, whole=True),
        "learn_min_pages": Setting("number", least=1, whole=True),
        "learn_searches": Setting("number", least=1, whole=True),
        "learn_think": Setting("think"),
        "docs_pages": Setting("number", least=1, whole=True),
        "tool_docs_pages": Setting("number", least=0, whole=True),
        "docs_latest_days": Setting("number", least=0),
        "fetch_workers": Setting("number", least=1, whole=True),
        "fetch_timeout": Setting("number", least=1),
        "fetch_max_bytes": Setting("number", least=1000, whole=True),
        "fetch_domain_delay": Setting("number", least=0),
        "fetch_fallbacks": Setting("number", least=0, whole=True),
        "embed_model": Setting("text", "embed"),
        "projects": Setting("text"),
    }
)

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
    "done",
)
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
EXERCISE_BUILD_LOOK_SECONDS = 2
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
PER_PAGE = 10
NUMBERED_PARTS = ("jobs", "read")


Paged = namedtuple("Paged", "items pages")
LogEntry = namedtuple("LogEntry", "id entry")


class Home:
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
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / "web-requests.log"

    @classmethod
    def graph_page(cls):
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / "memory.html"

    @classmethod
    def reading_file(cls, name):
        folder = cls.root / "reading"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (re.sub(r"[^\w.-]+", "-", Path(name).name).strip("-.") or "book")

    @classmethod
    def lock_file(cls, name):
        cls.root.mkdir(parents=True, exist_ok=True)
        return cls.root / f"channel-{name}.lock"

    @classmethod
    def reset(cls, yes=False):
        home = cls.root
        if not home.is_dir():
            say.info(f"Nothing to reset: {home} does not exist.")
            return
        if home.name != STATE_DIR:
            raise SystemExit(f"Not deleting {home}: it is not a .tinker folder.")
        say.info("Projects are not touched (each keeps its own .tinker folder).", "📁")
        confirm_deletion(
            "This deletes:",
            [f"{home}: Tinker's settings, measured models, what it knows and its list of projects"],
            "tinker reset",
            yes,
        )
        shutil.rmtree(home)
        cls.stores.pop(cls.database(), None)
        say.done(f"Deleted {home}. The next command starts fresh.")

    @staticmethod
    def parts():
        return {
            "languages": (
                ["languages"],
                [],
                "per language: the toolchain that worked, its fixes, its lessons",
            ),
            "memory": (["memory"], [], "lessons that hold for any language"),
            "learned": (["learned", "embeddings"], ["learn"], "what tinker learn learned, and its index"),
            "docs": (
                ["sources"],
                [],
                "the documentation pages tinker learn fetched, cleaned, per topic and version",
            ),
            "models": (["models"], [], "what calibration measured for each model"),
            "runs": (
                ["projects", "runs"],
                [],
                "the list of projects and a line about each run (projects keep their own .tinker)",
            ),
            "commands": (["commands"], [], "your commands Tinker may use"),
            "history": (
                ["chat history"],
                [],
                "what the chats showed, and what you asked (their messages go from the chats in a day)",
            ),
            "exercise": (
                ["exercise"],
                ["exercise"],
                "the exercises: each language's practice projects, what they "
                "measured, and where each stopped",
            ),
        }

    @classmethod
    def clear(cls, names, yes=False):
        names = cls.known_parts(names)
        held = {name: cls.entries_in(name) for name in names}
        if not any(held.values()):
            say.info(
                "Nothing to clear: "
                + ", ".join(names)
                + " "
                + ("is" if len(names) == 1 else "are")
                + " empty."
            )
            return
        confirm_deletion(
            "This deletes:",
            [f"{name}: {cls.parts()[name][2]} ({entries} entries)" for name, entries in held.items()],
            f"tinker clear {' '.join(names)}",
            yes,
        )
        for name in names:
            cls.delete_part(name)
        say.done(f"Cleared {', '.join(names)}.")

    @classmethod
    def known_parts(cls, names):
        unknown = [name for name in names if name not in cls.parts()]
        if unknown:
            raise SystemExit(
                f"tinker clear: no part {', '.join(unknown)}; the parts are: "
                + ", ".join([*cls.parts(), *NUMBERED_PARTS])
            )
        return list(dict.fromkeys(names))

    @classmethod
    def entries_in(cls, name):
        collections, logs, _ = cls.parts()[name]
        store = cls.store()
        return sum(store.count(collection) for collection in collections) + sum(
            store.logged(log) for log in logs
        )

    @classmethod
    def delete_part(cls, name):
        collections, logs, _ = cls.parts()[name]
        for collection in collections:
            cls.store().delete(collection)
        for log in logs:
            cls.store().drop_log(log)

    @classmethod
    def keep_awake(cls):
        command = cls.stay_awake_command(str(os.getpid()))
        if not command:
            return
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass

    @staticmethod
    def stay_awake_command(pid):
        if shutil.which("caffeinate"):
            return ["caffeinate", "-i", "-s", "-w", pid]
        if shutil.which("systemd-inhibit"):
            return [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--why=Tinker is working",
                "tail",
                f"--pid={pid}",
                "-f",
                "/dev/null",
            ]
        return None

    @classmethod
    def ensure(cls):
        try:
            new = not cls.database().exists()
            cls.store().connect().close()
            if new:
                say.done(f"Created {cls.database()} (Tinker's settings and memory)")
        except (OSError, SystemExit, sqlite3.Error) as exc:
            raise SystemExit(f"Tinker keeps its settings in {cls.root}, which cannot be written: {exc}")


class Config:
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

    @staticmethod
    def settable():
        return [key for key, row in SETTINGS.items() if row.owner == "config"]

    @classmethod
    def set(cls, key, text):
        cls.refuse_unsettable(key)
        value = SETTINGS[key].read(text.strip())
        cls.check_one(key, value)
        cls.save(**{key: value})
        return value

    @classmethod
    def refuse_unsettable(cls, key):
        if key in SETTINGS and SETTINGS[key].owner != "config":
            raise SystemExit(f"tinker config: {key} is set with tinker {SETTINGS[key].owner} NAME")
        if key not in SETTINGS:
            raise SystemExit(
                f"tinker config: no setting {key!r}; the settings are: {', '.join(cls.settable())}"
            )

    @classmethod
    def check(cls):
        for key in SETTINGS:
            cls.check_one(key, cls.get(key))

    @staticmethod
    def check_one(key, value):
        if not SETTINGS[key].fits(value):
            raise SystemExit(f"{key} must be {SETTINGS[key].described()}; it is {value!r}")

    @classmethod
    def ollama_url(cls):
        return str(cls.get("ollama_url")).rstrip("/")

    @staticmethod
    def keep_alive(value):
        return int(value) if re.fullmatch(r"-?\d+", str(value)) else value


def confirm_deletion(said, items, command, yes=False, verb="delete"):
    say.warn(said)
    with say.section():
        for item in items:
            say.item(item)
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit(f"Nothing {verb}d: run {command} in a terminal to confirm, or add --yes.")
    question = f"{verb.capitalize()} {'it' if len(items) == 1 else 'them'}? [y/N] "
    if input(question).strip().lower() not in ("y", "yes"):
        raise SystemExit(f"Nothing {verb}d.")


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number_from(value, least, whole=True):
    return is_number(value) and (not whole or isinstance(value, int)) and not value < least


class Store:
    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
        "PRIMARY KEY (collection, key))",
        "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
        "time TEXT NOT NULL, entry TEXT NOT NULL)",
    )
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
        with self.transaction(write=True) as db:
            value = change(self.read(db, collection, key, default))
            self.write(db, collection, key, value)
        return value

    def delete(self, collection, key=None):
        with self.transaction(write=True) as db:
            if key is None:
                db.execute("DELETE FROM docs WHERE collection = ?", (collection,))
            else:
                db.execute("DELETE FROM docs WHERE collection = ? AND key = ?", (collection, key))

    def items(self, collection):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT key, value FROM docs WHERE collection = ? ORDER BY rowid", (collection,)
            ).fetchall()
        return {key: json.loads(value) for key, value in rows}

    def count(self, collection):
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM docs WHERE collection = ?", (collection,)).fetchone()[0]

    def append(self, log, entry):
        with self.transaction(write=True) as db:
            return db.execute(
                "INSERT INTO logs (name, time, entry) VALUES (?, ?, ?)",
                (log, time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(entry, ensure_ascii=False)),
            ).lastrowid

    def entries(self, log, after=0):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT id, time, entry FROM logs WHERE name = ? AND id > ? ORDER BY id", (log, after)
            ).fetchall()
        return [LogEntry(row_id, {"time": stamp, **json.loads(entry)}) for row_id, stamp, entry in rows]

    def drop_log(self, log):
        with self.transaction(write=True) as db:
            db.execute("DELETE FROM logs WHERE name = ?", (log,))

    def logged(self, log):
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM logs WHERE name = ?", (log,)).fetchone()[0]

    @contextlib.contextmanager
    def transaction(self, write=False):
        db = self.opened()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
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
        if not self.path.exists():
            self.create_private_file()
        elif not self.made:
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
        row = db.execute(
            "SELECT value FROM docs WHERE collection = ? AND key = ?", (collection, key)
        ).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def write(db, collection, key, value):
        db.execute(
            "INSERT INTO docs (collection, key, value) VALUES (?, ?, ?) ON CONFLICT (collection, key) "
            "DO UPDATE SET value = excluded.value",
            (collection, key, json.dumps(value, ensure_ascii=False)),
        )


class Numbered:
    def __init__(self, collection, counter):
        self.collection, self.counter = collection, counter

    def add(self, entry):
        return self.put(dict(entry, number=self.next_number()))

    def next_number(self):
        return Home.store().update(self.counter, "last", lambda last: (last or 0) + 1)

    def get(self, number):
        return Home.store().get(self.collection, str(number))

    def put(self, entry):
        Home.store().put(self.collection, str(entry["number"]), entry)
        return entry

    def update(self, number, change):
        return Home.store().update(self.collection, str(number), change)

    def delete(self, number):
        Home.store().delete(self.collection, str(number))

    def newest(self):
        entries = Home.store().items(self.collection).values()
        return sorted(entries, key=lambda entry: entry["number"], reverse=True)

    @staticmethod
    def page(entries, number):
        pages = max(1, -(-len(entries) // PER_PAGE))
        return Paged(entries[(number - 1) * PER_PAGE : number * PER_PAGE], pages)


class Log:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def add(self, event):
        self.store.append(self.name, event)


class NotAPytestClass:
    __test__ = False


def projects_folder():
    return Path(str(Config.get("projects") or "~/Projects")).expanduser().resolve()


def first_line(text, limit=140):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def now_text():
    return time.strftime("%Y-%m-%d %H:%M")


def clock(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def split_text(text, size):
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
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def after_what_they_use(starts, uses, known):
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
    groups, used = [], room + 1
    for item in items:
        need = size(item) + 1
        if used + need > room:
            groups.append([])
            used = 0
        groups[-1].append(item)
        used += need
    return groups
