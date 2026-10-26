from collections import namedtuple

from .settings import SETTINGS

Refs = namedtuple("Refs", "project words")


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
        "graph": Option(
            "--graph",
            "graph",
            None,
            "as a graph to explore: a page that opens in the browser (in a chat: sent as a file)",
        ),
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
        words="none",
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
        words="any",
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
        "build the plan that waits for your confirmation",
        "Build the plan that waits for confirmation (as resume).",
        kind="job",
        chat=True,
        words="one",
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
        words="any",
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
        words="one",
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
        words="any",
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
        words="any",
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
        words="one",
        arguments=[PROJECT],
        options=("workdir",),
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
        words="one",
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
        words="any",
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
        "facts across sites, keeps what it found (tinker memory) and answers with its sources. It is slow. "
        "No quotes needed.",
        kind="job",
        chat=True,
        words="any",
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
        words="any",
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
        words="any",
        arguments=[Argument("words", "+", "WHAT", "what to correct, and why (no quotes needed)")],
        example="teach ruff formats Python as black does, so ruff alone is enough",
    ),
    Command(
        "read",
        "Learn",
        "read FILE | URL",
        "store a book, an article or a document, then learn from every chapter",
        "Read what you give it: a book, an article or a document (EPUB, Markdown or plain text; a PDF is refused: "
        "convert it to EPUB first, Calibre's ebook-convert keeps its chapters). Its whole text is stored first, "
        "numbered on the reading list (#1, #2 ...), so you never send it twice; then every chapter is read, the "
        "core ones first (its contents, foreword and index tell which), each saved as soon as it is read. It is a "
        'job: stop it, and read #N (or resume) goes on with the chapters not read yet. read #N "WHAT" reads the '
        "chapters most about WHAT again, with it in mind. Ask about it (tinker ask) and correct it (tinker teach): "
        "its own text is the evidence. What teaches how to build software guides the builds that need it. In a "
        "chat, attach the file.",
        kind="job",
        actions=[Action("glance", "list"), Action("glance", "show")],
        chat=True,
        words="any",
        arguments=[
            Argument(
                "words",
                "+",
                "WORDS",
                "FILE | URL (what to read: a file, or a link that downloads "
                "it), list, show #N, or #N [WHAT] (go on; with WHAT, look again)",
            )
        ],
        example='read #2 "the chapter on retries: when to give up"',
        rows=[
            ("read FILE | URL", "store a book, an article or a document, then learn from every chapter"),
            (
                "read list [PAGE]",
                "the reading list, a page at a time: how much of each is read, what was learned",
            ),
            ("read show #N", "one reading: its chapters, read or not, and what each taught"),
            ('read #N ["WHAT"]', "go on with the chapters not read yet, or look again for WHAT"),
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
        words="any",
        arguments=[Argument("words", "+", "LANGUAGE | status")],
        example="exercise rust",
    ),
    Command(
        "jobs",
        "Jobs",
        "jobs [running|paused|stopped|finished|archived] [PAGE]",
        "the long commands: running, paused, stopped",
        "List the jobs: every long command (a task, add, fix, learn, ask, teach, read, research, exercise, "
        "calibrate) is one, numbered #1, #2 ... One runs at a time: a job you start pauses the one that runs, which goes on by itself once Tinker "
        "is idle (resume #N: at once). Without a state, the running ones, the paused ones and the stopped ones; "
        "running, paused, stopped, finished or archived (by clear jobs) lists those alone. Newest first, 10 to a "
        "page (jobs stopped 2: the second page).",
        kind="glance",
        chat=True,
        words="any",
        arguments=[
            Argument(
                "words",
                "*",
                "[STATE] [PAGE]",
                "running, paused, stopped, finished or archived; the page, from 1",
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
        "job that runs, whatever it is. A paused job that is stopped no longer goes on by itself.",
        kind="own",
        chat=True,
        words="one",
        arguments=[Argument("words", "*", "[#JOB]", "the job, as jobs numbers it")],
        example="stop #3",
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
        words="one",
        run=True,
        arguments=[
            Argument("words", "*", "[#JOB | @PROJECT]", "the job (#N) or the project (@ID or @folder)")
        ],
        options=RUN_OPTIONS,
        example="resume #3",
    ),
    Command(
        "memory",
        "Learn",
        "memory [--graph]",
        "what Tinker has learned (--graph: as a graph to explore)",
        "Show what Tinker has learned: toolchains, fixes, lessons, topics, your commands. --graph draws it as a "
        "graph in one page (opened in the browser; in a chat, sent as a file): each lesson, fact and fix linked "
        "to its language or topic and to the runs that taught it, proved it or found it wrong.",
        kind="glance",
        chat=True,
        words="one",
        options=("graph",),
        example="memory --graph",
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
        "models",
        "Set up",
        "models",
        "the models Ollama has, and which are measured",
        "List the models Ollama has, and which are measured.",
        kind="glance",
        chat=True,
    ),
    Command(
        "calibrate",
        "Set up",
        "calibrate NAME",
        "measure a model once (a minute or a few)",
        "Measure a model once: the largest prompt it reads and the longest reply it writes.",
        kind="job",
        chat=True,
        words="one",
        arguments=[Argument("name", None, None, "the model's name, as `tinker models` lists it")],
    ),
    Command(
        "use-model",
        "Set up",
        "use-model NAME",
        "the measured model every build uses",
        "Use this measured model for every build from now on.",
        kind="glance",
        chat=True,
        words="one",
        arguments=[Argument("name", None, None, "the model's name, as `tinker models` lists it")],
    ),
    Command(
        "embed",
        "Set up",
        "embed NAME",
        "the embedding model that indexes what is learned",
        "Use this embedding model to index what is learned, so builds find it by meaning.",
        kind="glance",
        chat=True,
        words="one",
        arguments=[Argument("name", None, None, "the model's name, as `tinker models` lists it")],
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
        words="any",
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
        words="any",
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
        words="one",
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
        words="any",
        example="chat hello",
    ),
    Command(
        "command",
        "Talk",
        "command WHAT",
        "the command line that does it, run once you confirm",
        kind="pauses",
        chat=True,
        words="any",
        example="command show the free disk space",
    ),
    Command(
        "run",
        "Talk",
        "run [@PROJECT] ARGS",
        "run a built project (the last one built, by default) with these arguments; <<< TEXT gives it input",
        kind="own",
        chat=True,
        words="any",
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
        words="one",
        arguments=[Argument("words", "*", "[PAGE | #N]", "the page, from 1, or #N: the one to show again")],
        example="history #3",
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
        words="any",
        rows=[
            ("channel list", "the chat channels (Telegram), and which are enabled"),
            ("channel add", "add one: its kind, a name, its token; pairs your chat"),
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
