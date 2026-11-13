import re
from collections import namedtuple
from operator import attrgetter

from .settings import SETTINGS, Config, Home
from .system import SYSTEM_WORDS

Refs = namedtuple("Refs", "project words")
MenuAnswer = namedtuple("MenuAnswer", "line send")
MenuRow = namedtuple("MenuRow", "marked name words what")
MENU_ROWS = 8


class Option:
    def __init__(self, flag, dest, metavar, help):
        self.flag, self.dest, self.metavar, self.help = flag, dest, metavar, help


OPTIONS = frozendict(
    {
        "spec": Option("--spec", "spec", "FILE", "a spec file, e.g. plan.md"),
        "feature_spec": Option("--spec", "spec_file", "FILE", "a spec file for the feature"),
        "web": Option("--web", "web", None, "may learn from the web what the rest of the cycle needs"),
        "confirm": Option(
            "--confirm",
            "confirm",
            None,
            "the plan waits for your confirm before anything is built (as in the conversation)",
        ),
        "new_workdir": Option(
            "--workdir",
            "workdir",
            "DIR",
            "the project folder (default: a new one in ~/Projects, named after the task)",
        ),
        "workdir": Option("--workdir", "workdir", "DIR", "the project folder, if not this one"),
        "yes": Option("--yes", "yes", None, "delete without asking"),
        "calls": Option("--calls", "calls", None, "the model calls, instead of what was printed"),
    }
)


class Argument:
    def __init__(self, dest, nargs=None, metavar=None, help=""):
        self.dest, self.nargs, self.metavar, self.help = dest, nargs, metavar, help


class Action(namedtuple("Action", "kind word when deletes", defaults=(None, None, False))):
    __slots__ = ()

    def fits(self, argv):
        return (self.word is None or argv[1:2] == [self.word]) and (self.when is None or self.when(argv[1:]))


def taught_command(words):
    name, equals, definition = " ".join(words).partition("=")
    if equals and name.strip() and " " not in name.strip():
        return name.strip(), definition.strip()
    if len(words) == 2 and (" " in words[1].strip() or words[1].strip().lower() == "none"):
        return words[0], words[1]
    return None


def never_measured(words):
    return (
        len(words) == 1
        and words[0] != "calibrate"
        and words[0] != Config.get("embed_model")
        and not isinstance(Home.store().get("models", words[0]), dict)
    )


def forgets_no_trait(words):
    return not re.fullmatch(r"t\d+", " ".join(words[1:2]))


class Command:
    def __init__(
        self,
        name,
        group,
        synopsis,
        what,
        about="",
        kind="own",
        chat=False,
        actions=(),
        words=False,
        cli=True,
        run=False,
        deletes=False,
        arguments=(),
        options=(),
        example="",
        rows=(),
    ):
        self.name, self.group, self.synopsis, self.what = name, group, synopsis, what
        self.about = about or what
        self.kind, self.chat, self.actions = kind, chat, tuple(actions)
        self.words, self.cli, self.run, self.deletes = words, cli, run, deletes
        self.arguments, self.example = list(arguments), example
        self.options = [OPTIONS[option] for option in options]
        self.rows = list(rows) or [(synopsis, what)]


PROJECT = Argument("words", "*", "[@PROJECT]", "the project (@ID or @folder; default: this folder's)")
RUN_OPTIONS = ("web", "workdir")

COMMANDS = [
    Command(
        "task",
        "Build",
        "task WHAT TO BUILD",
        "build something new, in its own folder in ~/Projects (--spec FILE)",
        "Build something new: a spec, a unit test for each point, just enough code, and a README a person can "
        "follow (its examples are run to check them). It builds straight through (--confirm: its plan waits for "
        "your confirm first, as it does in the conversation). It gets its own folder in the projects folder (tinker config projects), with a short "
        "name after the task (calc-py); --workdir DIR builds in DIR instead (--workdir . here).",
        kind="job",
        chat=True,
        words=True,
        run=True,
        arguments=[Argument("task", "+", "WHAT", "what to build (no quotes needed)")],
        options=("spec", "web", "confirm", "new_workdir"),
        example="task a calculator in Python",
    ),
    Command(
        "restart",
        "Build",
        "restart [--yes]",
        "build this folder's task again from scratch (asks first)",
        "Build this folder's task again from scratch: everything in it is deleted (after asking, or with --yes), "
        "but for its spec file.",
        kind="job",
        chat=True,
        run=True,
        deletes=True,
        options=("yes", "web", "confirm", "workdir"),
    ),
    Command(
        "confirm",
        "Build",
        "confirm [@PROJECT]",
        "say yes to what waits: a plan, or in a chat a deletion or offer",
        "Say yes to what waits for confirmation: build the plan that waits (as resume); in a chat, also carry out "
        "a deletion or an offer that waits.",
        kind="job",
        chat=True,
        words=True,
        run=True,
        arguments=[PROJECT],
        options=RUN_OPTIONS,
    ),
    Command(
        "amend",
        "Build",
        "amend [@PROJECT] CHANGE",
        "change a plan that waits for confirmation",
        "Change a plan that waits for confirmation, before anything is built: the spec is given again with the "
        "change and planned again, and it waits again (build it with tinker confirm).",
        kind="job",
        chat=True,
        words=True,
        run=True,
        arguments=[Argument("words", "+", "[@PROJECT] CHANGE", "the change")],
        options=RUN_OPTIONS,
        example="amend also handle negative numbers",
    ),
    Command(
        "rename",
        "Build",
        "rename [@PROJECT] NAME",
        "rename the folder of a project whose plan waits",
        "Rename a project's folder while its plan waits for confirmation, before anything is built in it (what is "
        "built there may name its folder).",
        kind="own",
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "[@PROJECT] NAME", "the folder's new name")],
        options=("workdir",),
        example="rename calculator",
    ),
    Command(
        "add",
        "Build",
        "add [@PROJECT] FEATURE",
        "add a feature to a built project",
        "Add a feature to a built project (this folder's, or the project named). Its plan waits for your confirm "
        "first with --confirm (as in the conversation).",
        kind="job",
        chat=True,
        words=True,
        run=True,
        arguments=[Argument("words", "+", "[@PROJECT] FEATURE", "the feature")],
        options=("feature_spec", "confirm") + RUN_OPTIONS,
        example="add keep a history of results",
    ),
    Command(
        "fix",
        "Build",
        "fix [@PROJECT] BUG",
        "fix a bug: what you do, what happens, what should",
        "Fix a bug: describe what you do, what happens and what should happen. A test shows the bug (with --confirm, it "
        "waits for your confirm); then the code is fixed so it passes and every other test still passes.",
        kind="job",
        chat=True,
        words=True,
        run=True,
        arguments=[Argument("words", "+", "[@PROJECT] BUG", "the bug")],
        options=("confirm",) + RUN_OPTIONS,
        example='fix typing 2/0 crashes, it should say "cannot divide by zero"',
    ),
    Command(
        "status",
        "Build",
        "status [@PROJECT]",
        "a project's spec points and whether they pass",
        "Show a project's spec points and whether their tests pass.",
        kind="glance",
        chat=True,
        words=True,
        arguments=[PROJECT],
        options=("workdir",),
    ),
    Command(
        "system",
        "Talk",
        f"system [{'|'.join(SYSTEM_WORDS)}]",
        "what this computer tells at once: one check or all",
        "Show what a system check answers on this computer, or every check's answer.",
        kind="glance",
        chat=True,
        words=True,
        arguments=[Argument("system", "?", "WORD", "the check (default: every one)")],
    ),
    Command(
        "runs",
        "Build",
        "runs",
        "every project, newest first",
        "List every project, newest first.",
        kind="glance",
        chat=True,
    ),
    Command(
        "log",
        "Build",
        "log [@PROJECT] [--calls]",
        "what a project's runs printed (or its model calls)",
        "Show what this folder's project (or the one named) printed in its runs; --calls shows its model calls.",
        kind="glance",
        chat=True,
        words=True,
        arguments=[PROJECT],
        options=("calls", "workdir"),
    ),
    Command(
        "learn",
        "Learn",
        "learn WHAT",
        "learn a language, a tool or a domain",
        "Learn something and keep it for the builds that need it. Tinker tells what it is: a language "
        '("Python 3.14"), a tool ("Flask"), a domain ("food bank inventory", researched with its sources '
        "cross-checked), or NAME = DEFINITION (or an alias in your shell): a command of yours. No quotes needed.",
        kind="job",
        actions=[Action("glance", when=taught_command)],
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "WHAT | NAME = DEFINITION")],
        example="learn rust",
        rows=[
            ("learn WHAT", "learn a language, a tool or a domain"),
            ("learn NAME = DEFINITION", "a command of yours Tinker may use"),
        ],
    ),
    Command(
        "research",
        "Learn",
        "research TOPIC",
        "the web researched in depth, its facts checked across sites, and kept: slow",
        "Research a topic on the web in depth: Tinker frames it as questions, reads pages for each, checks the "
        "facts across sites, keeps what it found (tinker knowledge) and answers with its sources. It is slow. "
        "No quotes needed.",
        kind="job",
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "TOPIC", "the topic (no quotes needed)")],
        example="research the newest Python release",
    ),
    Command(
        "ask",
        "Learn",
        "ask QUESTION",
        "ask what Tinker has learned",
        "Ask what Tinker has learned: it answers just what you ask, from what it keeps alone (toolchains, fixes, "
        "lessons, facts, your commands). Nothing is changed.",
        kind="job",
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "QUESTION", "the question (no quotes needed)")],
        example="ask what is your current toolchain for python",
    ),
    Command(
        "teach",
        "Learn",
        "teach WHAT",
        'correct what Tinker has learned ("prefer" marks your choices)',
        "Correct what Tinker has learned; teach several things at once if you like. Mark your own choices with "
        'the word prefer ("I prefer no mypy"): they are honored as they are. Everything else is a fact, checked '
        "first on this machine (commands that only show what is installed) and on the web, and each change is "
        "made once the facts it rests on hold: what you showed wrong is retired, the toolchain corrected and a "
        "lesson kept, with its sources. A change whose facts do not hold is not made, and it says why.",
        kind="job",
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "WHAT", "what to correct, and why (no quotes needed)")],
        example="teach ruff formats Python as black does, so ruff alone is enough",
    ),
    Command(
        "read",
        "Learn",
        "read FILE | URL",
        "keep a book, an article or a document on the reading list",
        "Keep what you give it: a book, an article or a document (EPUB, PDF, Word, Markdown or plain text). "
        "Its whole text is stored, numbered on the reading list (#1, #2 ...), so you never send it twice; it learns "
        "nothing yet. Ask about it in a chat, or read #N learn to learn from every chapter, the core ones first "
        "(its contents, foreword and index tell which), each saved as soon as it is read. Learning is a job: stop "
        'it, and read #N learn (or resume) goes on with the chapters not read yet. read #N learn "WHAT" reads the '
        "chapters most about WHAT again, with it in mind. Ask about it (tinker ask) and correct it (tinker teach): "
        "its own text is the evidence. What teaches how to build software guides the builds that need it. In a "
        "chat, attach the file.",
        kind="glance",
        actions=[
            Action("glance", "list"),
            Action("glance", "show"),
            Action("glance", "rename"),
            Action("glance", "forget", deletes=True),
            Action("job", when=lambda rest: rest[1:2] == ["learn"]),
        ],
        chat=True,
        words=True,
        options=("yes",),
        arguments=[
            Argument(
                "words",
                "+",
                "WORDS",
                "FILE | URL (what to keep: a file, or a link that downloads it), list, show #N, #N (the same "
                "as show #N), rename #N TITLE, forget #N..., or #N learn [WHAT] (learn from it; with WHAT, look "
                "again)",
            )
        ],
        example='read #2 learn "the chapter on retries: when to give up"',
        rows=[
            ("read FILE | URL", "keep a book, an article or a document on the reading list"),
            (
                "read list [PAGE]",
                "the reading list, a page at a time: how much of each is read, what was learned",
            ),
            ("read show #N", "one reading: its chapters, read or not, and what each taught"),
            ("read rename #N TITLE", "give a reading a new title"),
            ("read forget #N... [--yes]", "delete readings: their file and what was learned from them"),
            ('read #N learn ["WHAT"]', "learn from the chapters not read yet, or look again for WHAT"),
        ],
    ),
    Command(
        "exercise",
        "Learn",
        "exercise LANGUAGE | status",
        "practise a language on its own: get better, faster, and learn from mistakes",
        "Practise a language on its own, to write better code faster: Tinker learns the language first when it "
        "has not, then builds small practice projects one after another in their own folders (in "
        "tinker-exercise in the projects folder), each followed by a feature, measures each one (time, model "
        "calls, fixes, what failed) and keeps lessons from its mistakes and slow steps; the next projects prove "
        "them or retire them. You come first, as with every job: a chat or a job of yours pauses it, and it goes on "
        "once Tinker is idle. "
        "It is a job: stop #N stops it, everything kept; resume #N (or exercise LANGUAGE) goes on from there. One "
        "exercise runs at a time; each language keeps its own. exercise status shows how each is going. A word "
        "that is not one language (or could be several) is said so, and nothing is done.",
        kind="job",
        actions=[Action("glance", "status")],
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "LANGUAGE | status")],
        example="exercise rust",
    ),
    Command(
        "jobs",
        "Jobs",
        "jobs [running|queued|stopped|finished|archived] [PAGE]",
        "the long commands: running, queued, stopped",
        "List the jobs: every long command (a task, add, fix, learn, ask, teach, read, research, exercise, "
        "model calibrate) is one, numbered #1, #2 ... One runs at a time: a job you start goes to the front of the queue and the one that ran waits "
        "behind it (resume #N: at once). Without a state, the running ones, the queued ones and the stopped ones; "
        "running, queued, stopped, finished or archived (by clear jobs) lists those alone. Newest first, 10 to a "
        "page (jobs stopped 2: the second page).",
        kind="glance",
        chat=True,
        words=True,
        arguments=[
            Argument(
                "words",
                "*",
                "[STATE] [PAGE]",
                "running, queued, stopped, finished or archived; the page, from 1",
            )
        ],
        example="jobs stopped",
    ),
    Command(
        "stop",
        "Jobs",
        "stop [#JOB]",
        "stop a job (it is saved): the one that runs, or #N",
        "Stop a job as Ctrl-C does: what it did is kept, and resume #N goes on from there. Without a number, the "
        "job that runs, whatever it is. A queued job that is stopped leaves the queue and never goes on by itself.",
        kind="own",
        chat=True,
        words=True,
        arguments=[Argument("words", "*", "[#JOB]", "the job, as jobs numbers it")],
        example="stop #3",
    ),
    Command(
        "queue",
        "Jobs",
        "queue list | queue stop #JOB | queue stop all",
        "the queue of jobs: list it, or take jobs out",
        "The jobs wait in one queue and its head runs. queue list lists it in order, the head first; queue stop #N "
        "takes job #N out, as stop #N does; queue stop all stops the job that runs and takes every job out.",
        kind="own",
        chat=True,
        words=True,
        arguments=[Argument("words", "+", "list | stop #JOB | stop all", "what to do with the queue")],
        example="queue list",
    ),
    Command(
        "resume",
        "Jobs",
        "resume [#JOB | @PROJECT]",
        "go on with a stopped job, or a project's run",
        "Go on with a stopped job where it stopped (#N; without one, the job stopped last): what it asked the "
        "model and the web before is answered from its journal, not asked again. resume @PROJECT continues a "
        "project's run (@ID or @folder, from anywhere; with no job stopped, this folder's); a plan that waits for "
        "confirmation is confirmed: building starts.",
        kind="job",
        chat=True,
        words=True,
        run=True,
        arguments=[
            Argument("words", "*", "[#JOB | @PROJECT]", "the job (#N) or the project (@ID or @folder)")
        ],
        options=RUN_OPTIONS,
        example="resume #3",
    ),
    Command(
        "knowledge",
        "Learn",
        "knowledge [NAME] [KIND] [PAGE] | show ID | find WORDS | recent [PAGE] | graph",
        "browse what Tinker has learned (graph: as a graph to explore)",
        "Browse what Tinker has learned, a page at a time: knowledge lists the parts (each language, any language, "
        "each topic, your commands) with what each holds; knowledge NAME shows one part, each item with its short "
        "ID, ✅ proven or 🌱 not yet, its kind and its first line; a KIND (toolchain, facts, findings, rules, "
        "fixes, lessons, commands or retired) keeps that kind, and alone lists it across memory. The browser "
        "changes nothing: correct an item with teach, forget a part with clear PART. knowledge graph draws it as a "
        "graph in one page (opened in the browser; in a chat, sent as a file): each lesson, fact and fix linked "
        "to its language or topic and to the runs that taught it, proved it or found it wrong.",
        kind="glance",
        chat=True,
        words=True,
        arguments=[
            Argument(
                "words",
                "*",
                "[NAME] [KIND] [PAGE]",
                "a part (as knowledge lists it), a kind of item, the page, from 1; or show ID, find WORDS, "
                "recent [PAGE], graph",
            )
        ],
        example="knowledge python lessons 2",
        rows=[
            ("knowledge [PAGE]", "the parts of memory: what each holds, how much is proven, what is retired"),
            ("knowledge NAME [KIND] [PAGE]", "one part: its details, then its items with their short IDs"),
            ("knowledge KIND [PAGE]", "one kind of item across memory (retired: the retired ones)"),
            ("knowledge show ID", "one item whole: its text, details, sources, links and runs"),
            ("knowledge find WORDS", "the items that hold every word, retired ones too"),
            ("knowledge recent [PAGE]", "the items newest first, each with its day"),
            ("knowledge graph", "memory as a graph in one page, to explore"),
        ],
    ),
    Command(
        "memory",
        "Learn",
        "memory [PAGE] | show ID | forget ID [--yes] | traits",
        "what Tinker remembers about you, and its traits",
        "See what Tinker remembers from what you do, a page at a time: memory lists the memories newest first, "
        "each with its ID, kind and gist, then how many episodes wait for a dream and when the last dream was; "
        "memory show ID shows one memory or episode whole; memory forget ID deletes one (asking first) or "
        "retires a trait; memory traits shows the confirmed traits, the candidates and the waiting questions.",
        kind="glance",
        actions=[Action("glance", "forget", forgets_no_trait, deletes=True)],
        chat=True,
        words=True,
        arguments=[
            Argument("words", "*", "[PAGE] | show ID | forget ID | traits", "the page, from 1; or an action")
        ],
        options=("yes",),
        example="memory show a1b2c3",
        rows=[
            ("memory [PAGE]", "the memories, newest first, and when the last dream was"),
            ("memory show ID", "one memory (with its sources) or episode whole"),
            ("memory forget ID [--yes]", "delete one memory or episode, or retire a trait"),
            ("memory traits", "the confirmed traits, the candidates and the waiting questions"),
        ],
    ),
    Command(
        "dream",
        "Learn",
        "dream [status] | run",
        "when Tinker last dreamt, and dream now",
        "See how Tinker learns from what you do: dream (or dream status) says when the last dream was, how many "
        "changes each step made, how many episodes wait, and whether a dream is queued or running; dream run "
        "dreams now, turning fresh episodes into memories and learning traits.",
        kind="glance",
        actions=[Action("job", "run")],
        chat=True,
        words=True,
        arguments=[Argument("words", "*", "[status] | run", "status (the default) or run")],
        example="dream run",
        rows=[
            ("dream [status]", "the last dream, its changes, the waiting episodes and any queued dream"),
            ("dream run", "dream now: consolidate fresh episodes, revise memories and learn traits"),
        ],
    ),
    Command(
        "doctor",
        "Set up",
        "doctor",
        "what is missing or wrong, and how to fix it",
        "Check what Tinker needs (Ollama, a measured model, an embedding model, the web) and say how to fix what "
        "is missing.",
        kind="glance",
        chat=True,
    ),
    Command(
        "model",
        "Set up",
        "model [calibrate|recipe [all]] [NAME]",
        "the models Ollama has, and the one every build uses",
        "List the models Ollama has and pick one: a measured model is used for every build, one never measured "
        "is measured first, an embedding model indexes what is learned. calibrate NAME measures a model again. "
        "recipe prints the smartest and the fastest measured model; recipe all measures the rest first.",
        kind="glance",
        actions=[
            Action("job", "calibrate", when=lambda words: len(words) > 1),
            Action("glance", "recipe", when=lambda words: len(words) == 1),
            Action("job", "recipe", when=lambda words: words[1:] == ["all"]),
            Action("job", when=never_measured),
        ],
        chat=True,
        words=True,
        arguments=[
            Argument(
                "words", "*", "[calibrate|recipe [all]] [NAME]", "calibrate or recipe, then the model's name"
            )
        ],
    ),
    Command(
        "config",
        "Set up",
        "config [KEY VALUE]",
        "the settings, or set one",
        "Show the settings a build uses, or set one: tinker config KEY VALUE (e.g. tinker config test_timeout 90). "
        "The settings: "
        + ", ".join(f"{key} ({row.described()})" for key, row in SETTINGS.items() if row.owner == "config")
        + ".",
        kind="glance",
        chat=True,
        words=True,
        arguments=[Argument("words", "*", "KEY VALUE", "the setting and its value")],
    ),
    Command(
        "clear",
        "Set up",
        "clear PART... [--yes]",
        "delete parts of what Tinker keeps (the parts below)",
        "Delete parts of what Tinker keeps, e.g. tinker clear languages memory: languages (toolchains, fixes, "
        "lessons per language), memory (lessons for any language), learned (what tinker learn learned), docs (the "
        "documentation it fetched, per version), models (measurements), runs (the list of projects), commands (your commands), "
        "history (what the chats showed; in a chat its messages go too), exercise (the exercises), read [#N...] "
        "(readings: their text and what was learned from them; every one with no #N), jobs [#N...] (archived, not "
        "deleted: jobs archived lists them, and they cannot be resumed; every stopped and finished one with no "
        "#N). In a conversation, clear chat takes every "
        "message out of the chats but Tinker's status, at once: history keeps what they showed.",
        kind="glance",
        chat=True,
        words=True,
        deletes=True,
        arguments=[
            Argument(
                "parts",
                "+",
                "PART",
                "languages, memory, learned, docs, models, "
                "runs, commands, history, exercise, read "
                "[#N...] or jobs [#N...]",
            )
        ],
        options=("yes",),
        rows=[
            (
                "clear languages | memory | learned",
                "what was learned: per language, for any language, by learn",
            ),
            (
                "clear docs | models | runs | commands",
                "fetched docs, model measures, the project list, your commands",
            ),
            ("clear exercise", "the exercises: their projects, measures and where each stopped"),
            ("clear read [#N...]", "readings: their text and what was learned from them (all, with no #N)"),
            (
                "clear jobs [#N...]",
                "archive jobs: jobs archived lists them (stopped, finished: all, with no #N)",
            ),
            ("clear history", "what the chats showed, and what you asked (the chat's messages go too)"),
            ("clear chat", "in a chat: take its messages out now; history keeps them"),
        ],
    ),
    Command(
        "reset",
        "Set up",
        "reset [--yes]",
        "delete everything Tinker keeps (projects stay)",
        "Delete everything Tinker keeps (settings, measured models, what it learned, the list of projects); "
        "projects are not touched.",
        kind="glance",
        chat=True,
        deletes=True,
        options=("yes",),
    ),
    Command(
        "help",
        "Set up",
        "help [COMMAND]",
        "this list, or one command's arguments and options",
        kind="glance",
        chat=True,
        words=True,
        arguments=[Argument("command", "?", "COMMAND")],
    ),
    Command(
        "",
        "Talk",
        "(no command)",
        "talk with Tinker here: every command, and a normal chat (exit ends it)",
        cli=False,
    ),
    Command(
        "chat",
        "Talk",
        "chat MESSAGE",
        "a normal chat with the model (the job that runs is paused for it)",
        kind="pauses",
        words=True,
        example="chat hello",
    ),
    Command(
        "command",
        "Talk",
        "command WHAT",
        "the command line that does it, run once you confirm",
        kind="pauses",
        chat=True,
        words=True,
        example="command show the free disk space",
    ),
    Command(
        "run",
        "Talk",
        "run [@PROJECT] ARGS",
        "run a built project (the last one built, by default) with these arguments; <<< TEXT gives it input",
        kind="own",
        chat=True,
        words=True,
        example="run 5 + 3",
    ),
    Command(
        "history",
        "Talk",
        "history [PAGE | #N]",
        "what the chats showed, newest first; #N shows one again",
        "List what Tinker showed in the chats (tasks, researches, replies ...), newest first, 10 to a page "
        "(history 2: the second page), with what you asked for each; history #N shows one again. A chat keeps its "
        "messages a day (a glance, like jobs or help, two minutes): the history keeps them all, until tinker clear "
        "history.",
        kind="glance",
        chat=True,
        words=True,
        arguments=[Argument("words", "*", "[PAGE | #N]", "the page, from 1, or #N: the one to show again")],
        example="history #3",
    ),
    Command(
        "interact",
        "Talk",
        "interact",
        "answer Tinker's questions about you, one per turn",
        kind="own",
        chat=True,
    ),
    Command(
        "exit",
        "Talk",
        "exit",
        "end the conversation (in a chat, once confirmed)",
        kind="own",
        chat=True,
        cli=False,
    ),
    Command(
        "channel",
        "Channels",
        "channel ACTION",
        "the chat channels",
        kind="glance",
        actions=[
            Action("terminal", "add"),
            Action("terminal", "remove", deletes=True),
            Action("terminal", "start"),
        ],
        chat=True,
        words=True,
        rows=[
            ("channel list", "the chat channels (Telegram), and which are enabled"),
            (
                "channel add [--kind KIND] [--name NAME]",
                "add one: its kind, a name, its token; pairs your chat",
            ),
            ("channel remove NAME [--yes]", "delete a channel and its token, once you confirm"),
            ("channel enable NAME", "every task reports to it; tinker channel start reads it"),
            ("channel disable NAME", "stop using it (channels start disabled)"),
            ("channel start [NAME]", "read the chat: tasks, status, jobs, stop, commands, or just chat"),
        ],
    ),
]
BY_NAME = frozendict({command.name: command for command in COMMANDS if command.name})
GROUPS = ("Build", "Learn", "Jobs", "Set up", "Talk", "Channels")


def kind_of(argv):
    action = action_of(argv)
    return action.kind if action else None


def deletes(argv):
    action = action_of(argv)
    return bool(action and action.deletes)


def action_of(argv):
    command = BY_NAME.get(argv[0]) if argv else None
    if command is None:
        return None
    asked = next((action for action in command.actions if action.fits(argv)), None)
    return asked or Action(command.kind, deletes=command.deletes)


def ref(word):
    word = str(word or "").strip()
    return word[1:] if word.startswith("@") and len(word) > 1 else None


def refs(words):
    words = [word for word in words if str(word).strip()]
    name = ref(words[0]) if words else None
    return Refs(name, words[1:]) if name else Refs("", words)


def slash_matches(typed):
    if not typed.startswith("/") or " " in typed:
        return []
    offered = [command for command in COMMANDS if command.chat]
    return starts_then_holds(typed[1:], offered, attrgetter("name"))


def word_matches(typed):
    name, _, word = typed.removeprefix("/").partition(" ")
    command = BY_NAME.get(name)
    if not typed.startswith("/") or not word or " " in word or not command or not command.chat:
        return []
    return [(command, known) for known in starts_then_holds(word, offered_words(command), str)]


def starts_then_holds(word, items, name_of):
    starts = [item for item in items if name_of(item).startswith(word)]
    return starts + [item for item in items if word in name_of(item) and item not in starts]


def offered_words(command):
    actions = [action.word for action in command.actions if action.word]
    return list(dict.fromkeys(actions + alternative_words(synopsis_words(command))))


def alternative_words(synopsis):
    words, depth, opens = [], 0, True
    for token in re.findall(r"\[|\]|\||[^\s\[\]|]+", synopsis):
        if token in "[]|":
            depth += {"[": 1, "]": -1}.get(token, 0)
            opens = token != "]"
            continue
        if opens and depth <= 1 and re.fullmatch(r"[a-z]+", token):
            words.append(token)
        opens = False
    return words


def menu_matches(typed):
    commands = [CommandMatch(command) for command in slash_matches(typed)]
    return commands or [WordMatch(command, word) for command, word in word_matches(typed)]


class CommandMatch:
    def __init__(self, command):
        self.command = command

    @property
    def line(self):
        return "/" + self.command.name

    def row(self, marked):
        return MenuRow(marked, self.line, synopsis_words(self.command), self.command.what)

    def fill(self):
        return MenuAnswer(self.line + " ", False)

    def enter(self, typed):
        return self.fill() if self.command.words and typed != self.line else MenuAnswer(self.line, True)


class WordMatch(CommandMatch):
    def __init__(self, command, word):
        super().__init__(command)
        self.word = word

    @property
    def line(self):
        return f"/{self.command.name} {self.word}"

    def row(self, marked):
        return MenuRow(marked, self.line, "", self.command.what)

    def enter(self, typed):
        return MenuAnswer(self.line, True)


class CommandMenu:
    def __init__(self):
        self.typed = None
        self.close()

    @property
    def open(self):
        return bool(self.matches)

    @property
    def chosen(self):
        return self.matches[self.current]

    def follow(self, text):
        if text != self.typed:
            self.typed = text
            self.matches, self.current, self.top = menu_matches(text), 0, 0

    def close(self):
        self.matches, self.current, self.top = [], 0, 0

    def rows(self):
        shown = self.matches[self.top : self.top + MENU_ROWS]
        return [match.row(self.top + index == self.current) for index, match in enumerate(shown)]

    def press(self, key):
        if not self.open or not isinstance(key, str):
            return None
        handler = getattr(self, "on_" + key, None)
        return handler() if handler else None

    def on_up(self):
        return self.move(self.current - 1)

    def on_down(self):
        return self.move(self.current + 1)

    def move(self, current):
        self.current = min(max(current, 0), len(self.matches) - 1)
        self.top = min(max(self.top, self.current - MENU_ROWS + 1), self.current)
        return MenuAnswer(None, False)

    def on_tab(self):
        return self.fill()

    def on_enter(self):
        answer = self.chosen.enter(self.typed)
        self.close()
        return answer

    def fill(self):
        answer = self.chosen.fill()
        self.close()
        return answer

    def on_esc(self):
        self.close()
        return MenuAnswer(None, False)


def synopsis_words(command):
    return command.synopsis.removeprefix(command.name).strip()
