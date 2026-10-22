"""Tinker's settings: where it keeps what it knows (its home and its database), the limits it works
within, and the small helpers every part uses."""

import contextlib
lazy import datetime
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


DEFAULT_CONFIG = frozendict({                      # the settings: what the home's "config" holds overrides these
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
    "learn_rate": 8,                    # web requests per minute while learning (a token bucket)
    "learn_backoff": 20,                # the most seconds one learn waits out HTTP 429 before it stops
    "learn_cutoff": 0.0,                # the word score (BM25) a search result's title and snippet need
    "learn_pages": 8,                   # search results fetched per search, the best first
    "learn_min_pages": 2,               # pages with usable text a learn needs, else it stops
    "learn_searches": 3,                # web searches per learn: the first, then one per slot left without facts
    "learn_think": "low",               # think level for reading pages into facts and condensing them (the plan
                                        # thinks at the model's own)
    "docs_pages": 30,                   # pages taken from a topic's documentation site
    "tool_docs_pages": 5,               # pages taken from the documentation of each of a language's tools
    "docs_latest_days": 30,             # days a copy of unversioned ("latest") documentation is kept before it
                                        # is fetched again (a version's own documentation is kept until cleared)
    "fetch_workers": 6,                 # pages fetched at once
    "fetch_timeout": 15,                # seconds one page may take
    "fetch_max_bytes": 2000000,         # the most of one page read
    "fetch_domain_delay": 0.1,          # seconds between two requests to one site (its pages go one at a time)
    "fetch_fallbacks": 2,               # pages per learn the web plugin fetches when a site gives too little text
    "embed_model": "",                  # the embedding model that indexes what is learned (tinker embed NAME);
                                        # tinker learn and --web need one
    "projects": "~/Projects",           # where tinker task makes a new project's own folder (named after the
                                        # task), unless --workdir names one
})

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
CONFIRM_AFTER = frozendict({"feature": "review", "fix": "reproduce"})  # by cycle kind: where its plan waits for the person
FORMAT_AFTER = ("verify",)  # phases after which the toolchain's formatter runs (the model never formats)
STATUS_TEXT = frozendict({"green": "passes", "todo": "to do", "active": "in progress", "blocked": "parked"})
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
LOOP_LINES = 20                   # the same line written this many times in a row: the model is looping

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
HISTORY_KEPT = 20                 # events an item keeps (born, confirmed, helped, failed), the newest
RETIRED_KEPT = 50                 # retired items kept beside each list, for people to see (never prompts)
RUNS_KEPT = 200                   # lines about runs kept in the home, the most recently updated

# --- the memory graph: tinker memory --graph --------------------------------------------------------------
SIMILAR_MEANING = 0.75            # embedding similarity at which two lessons are linked as similar
SIMILAR_WORDS = 0.5               # ... text similarity, when there are no embeddings

# --- learning from the web: tinker learn TOPIC, and the recall phase of a run --------------------------
FACT_PHASES = ("spec", "design", "config", "tests", "code", "toolchain")  # where a learned fact is shown
MAX_COMMANDS = 6                  # commands that show what of a topic is installed here
VERSION_TIMEOUT = 20              # seconds such a command may take
SEARCH_RESULTS = 3                # results taken from each search of research
PAGE_CHUNKS = 2                   # prompt-sized chunks of a page condensed (its top, then the most related)
FACTS_PER_CHUNK = 4               # facts one chunk may give
FACT_CHARS = 400                  # the longest fact kept (room for its exact numbers, names and units)
TOOL_ROLES = ("environment", "packages", "build", "tests", "format", "lint", "types")  # a language's tools, one per role
PLAYBOOK_RULES = 16               # rules a topic's facts are condensed into (what its prompts show instead)
RULE_CHARS = 200                  # the longest rule kept
RULE_WHEN_CHARS = 80              # the longest "when" of a rule that applies only to some code

# --- learning a language or a tool by its slots: tinker learn TOPIC (the limits a person tunes are in config)
FIRST_SEARCH = "{topic} official documentation"  # the first search, built from the topic alone: its docs site
DOC_MAPS = ("llms.txt", "sitemap.xml")  # where a documentation site lists its pages (then its own links)
MAX_MAP_LINKS = 5000              # links read from one site's map
LEARN_RESULTS = 10                # results asked of one search (the best learn_pages are fetched)
USABLE_PAGE_CHARS = 400           # a page's main text below this is too little (a page its scripts draw)
MAX_REDIRECTS = 5                 # redirects one page fetch follows
FETCH_USER_AGENT = "Tinker/1.0 (a local coding agent learning from documentation)"
PASSAGE_CHARS = 1200              # a passage under one heading is cut here (a code block is kept whole)
LINK_ONLY = 0.8                   # share of a block's text inside links at which it is a menu, not content
NEAR_DUPLICATE = 0.8              # word overlap (3-word shingles) at which a passage repeats another
SLOT_RELEVANT = 0.6               # embedding similarity a passage needs to a slot's question (or a slot keyword;
                                  # qwen3-embedding, as TOPIC_RELEVANT: related 0.65 and up, unrelated 0.52 and down)
SLOT_CANDIDATES = 60              # passages per slot shortlisted by words before the embedding model sees them
MMR_BALANCE = 0.7                 # relevance against novelty when picking a slot's passages (1: relevance only)
FACTS_PER_SLOT = 6                # facts one slot's digest may give
BACKOFF_FIRST = 1.0               # seconds of the first wait after HTTP 429 (then doubled, within learn_backoff)

# --- research: tinker learn of a domain (Researcher) ----------------------------------------------------
RESEARCH_QUESTIONS = 4            # research questions a topic is split into
RESEARCH_QUERIES = 2              # web searches per question, for independent sources
RESEARCH_PAGES = (8, 6)           # pages read per round: the first round, then one for the gaps and checks
RESEARCH_GAPS = 2                 # gaps the next round searches (the most important first)
RESEARCH_CHECKS = 2               # key facts one site states that the next round looks for on other sites
SAME_CLAIM = 0.8                  # embedding similarity at which two facts make the same claim
                                  # (qwen3-embedding: paraphrases 0.86 and up, unrelated facts 0.67 and down)
REPEAT_CLAIM = 0.9                # ... at which one fact repeats another (kept once, with the pages of both)
RECALL_TOPICS = 6                 # learned topics one cycle uses at most
TOPIC_RELEVANT = 0.6              # embedding similarity of a request and a topic it needs (qwen3-embedding:
                                  # the topic a request is about 0.65 and up, unrelated topics 0.52 and down)


class Home:
    """Tinker's own folder next to the script (.tinker): its database, tinker.db (settings, measured
    models, what it knows, the projects it points to, the channels), and the channels' lock files.
    Only Home knows where it is; tinker reset deletes it."""
    root = Path(__file__).resolve().parent.parent / STATE_DIR  # next to the script (its real file, not a link)
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
            print(f"Nothing to reset: {home} does not exist.")
            return
        if home.name != STATE_DIR:
            raise SystemExit(f"Not deleting {home}: it is not a .tinker folder.")
        print(f"This deletes {home}: Tinker's settings, measured models, what it knows and its list of projects.")
        print("Projects are not touched (each keeps its own .tinker folder).")
        if not confirmed("Delete it?", "tinker reset", yes):
            print("Nothing deleted.")
            return
        shutil.rmtree(home)
        cls.stores.pop(cls.database(), None)
        print(f"Deleted {home}. The next command starts fresh.")

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
                "commands": (["commands"], [], "your commands Tinker may use")}

    @classmethod
    def clear(cls, names, yes=False):
        """tinker clear PART...: delete those parts of what Tinker keeps, after asking (yes: without
        asking); everything else stays."""
        names = cls.known_parts(names)
        held = {name: cls.entries_in(name) for name in names}
        if not any(held.values()):
            print("Nothing to clear: " + ", ".join(names) + " " + ("is" if len(names) == 1 else "are") + " empty.")
            return
        cls.show_what_clearing_deletes(held)
        if not confirmed("Delete them?", "it", yes):
            print("Nothing deleted.")
            return
        for name in names:
            cls.delete_part(name)
        print(f"Cleared {', '.join(names)}.")

    @classmethod
    def known_parts(cls, names):
        unknown = [n for n in names if n not in cls.parts()]
        if unknown:
            raise SystemExit(f"tinker clear: no part {', '.join(unknown)}; the parts are: {', '.join(cls.parts())}")
        return list(dict.fromkeys(names))

    @classmethod
    def entries_in(cls, name):
        collections, logs, _ = cls.parts()[name]
        store = cls.store()
        return sum(store.count(c) for c in collections) + sum(store.logged(log) for log in logs)

    @classmethod
    def show_what_clearing_deletes(cls, held):
        print("This deletes:")
        for name, entries in held.items():
            print(f"  {name}: {cls.parts()[name][2]} ({entries} entries)")

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
                print(f"  created {cls.database()} (Tinker's settings and memory)")
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
            if think not in ("high", "medium", "low", True, False, None) or think in (0, 1) and not isinstance(think, bool):
                raise SystemExit(f'{key} must be "high", "medium", "low", true, false or null; it is {think!r}')

    @classmethod
    def check_test_timeout(cls):
        timeout = cls.get("test_timeout")
        if not is_number(timeout) or not isinstance(timeout, int) or timeout < 1:
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
        """Redraw one status line in place, on a terminal only (never kept in the run's output): piped, as
        when a chat follows the command, it would reach the reader as lines of their own. Cut to the
        terminal width (a wrapped line cannot be redrawn with \\r)."""
        if not sys.__stdout__.isatty():
            return
        width = shutil.get_terminal_size((80, 24)).columns
        sys.__stdout__.write("\r" + text[:max(width - 1, 10)] + "\033[K")
        sys.__stdout__.flush()

    @staticmethod
    def clear_progress():
        if sys.__stdout__.isatty():
            sys.__stdout__.write("\r\033[K")
            sys.__stdout__.flush()


class Tee:
    """Writes to a stream, like `tee`, and gives each whole line to see(line) (which may never break the
    writing)."""

    def __init__(self, stream, see):
        self.stream, self.see, self.partial = stream, see, ""

    def write(self, text):
        self.stream.write(text)
        for line in self.whole_lines(text):
            self.show(line)
        return len(text)

    def whole_lines(self, text):
        """The lines this text completes; the rest waits for its end."""
        *lines, self.partial = (self.partial + text).split("\n")
        return lines

    def show(self, line):
        try:
            self.see(line)
        except Exception:  # keeping the output must never break the run
            pass

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    def __getattr__(self, name):
        return getattr(self.stream, name)


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
        except sqlite3.DatabaseError as exc:  # not a database (any more), or locked past WAIT
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
        db = sqlite3.connect(self.path, timeout=self.WAIT, isolation_level=None)
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
