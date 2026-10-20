"""Tinker's commands, defined once: the command line builds its parser and its help from them, and the
conversation (the terminal session and the chats) its commands. A project is named in a command only as
@ID (its full run ID) or @folder (its folder's name), never guessed from the command's other words."""


class Option:
    """An option a command takes: its flag, where it goes, the value it takes (none: a switch), what it does."""

    def __init__(self, flag, dest, metavar, help):
        self.flag, self.dest, self.metavar, self.help = flag, dest, metavar, help


OPTIONS = {
    "spec": Option("--spec", "spec", "FILE", "a spec file, e.g. plan.md"),
    "feature_spec": Option("--spec", "spec_file", "FILE", "a spec file for the feature"),
    "web": Option("--web", "web", None, "may learn from the web what the rest of the cycle needs"),
    "no_confirm": Option("--no-confirm", "no_confirm", None, "build straight through: no plan waits for confirmation"),
    "new_workdir": Option("--workdir", "workdir", "DIR", "the project folder (default: a new one in ~/Projects, named "
                                                         "after the task)"),
    "workdir": Option("--workdir", "workdir", "DIR", "the project folder, if not this one"),
    "yes": Option("--yes", "yes", None, "delete without asking"),
    "calls": Option("--calls", "calls", None, "the model calls, instead of what was printed"),
}


class Argument:
    """An argument a command takes: where it goes, how many words (argparse's nargs), how it is shown."""

    def __init__(self, dest, nargs=None, metavar=None, help="", choices=None):
        self.dest, self.nargs, self.metavar, self.help, self.choices = dest, nargs, metavar, help, choices


class Command:
    """One command. group: where help lists it. chat: how the conversation takes it: "own" (its own
    handling), "long" (it runs as a task, its progress shown), "quick" (what it prints is shown), or None
    (not in a conversation). words: what may follow it in a chat for it to be the command ("none", "one",
    "any"). cli: a command of the command line (tinker NAME). run: it runs a cycle of a project. deletes: it
    deletes something (in a chat, shown first and done once confirmed). rows: help's lines, when more than one."""

    def __init__(self, name, group, synopsis, what, about="", chat=None, words="none", cli=True, run=False,
                 deletes=False, arguments=(), options=(), example="", rows=()):
        self.name, self.group, self.synopsis, self.what, self.about = name, group, synopsis, what, about or what
        self.chat, self.words, self.cli, self.run, self.deletes = chat, words, cli, run, deletes
        self.arguments, self.options, self.example = list(arguments), [OPTIONS[o] for o in options], example
        self.rows = list(rows) or [(synopsis, what)]


PROJECT = Argument("words", "*", "[@PROJECT]", "the project (@ID or @folder; default: this folder's)")
RUN_OPTIONS = ("web", "no_confirm", "workdir")

COMMANDS = [
    Command("task", "Build", "task WHAT TO BUILD", "build something new, in its own folder in ~/Projects (--spec FILE)",
            "Build something new: a spec, a unit test for each point, just enough code, and a README a person can "
            "follow (its examples are run to check them). Its plan waits for your confirm first (--no-confirm builds "
            "straight through). It gets its own folder in the projects folder (tinker config projects), with a short "
            "name after the task (calc-py); --workdir DIR builds in DIR instead (--workdir . here).",
            chat="own", words="any", run=True, arguments=[Argument("task", "+", "WHAT", "what to build (no quotes needed)")],
            options=("spec", "web", "no_confirm", "new_workdir"), example="task a calculator in Python"),
    Command("restart", "Build", "restart [--yes]", "build this folder's task again from scratch (asks first)",
            "Build this folder's task again from scratch: everything in it is deleted (after asking, or with --yes), "
            "but for its spec file.", chat="long", run=True, deletes=True, options=("yes", "workdir")),
    Command("resume", "Build", "resume [@PROJECT]", "continue a project's run (a plan that waits: build it)",
            "Continue a run: this folder's, or the project named (@ID or @folder, from anywhere). A plan that waits "
            "for confirmation is confirmed: building starts.", chat="long", words="one", run=True,
            arguments=[PROJECT], options=RUN_OPTIONS, example="resume @calc-py"),
    Command("confirm", "Build", "confirm [@PROJECT]", "build the plan that waits for your confirmation",
            "Build the plan that waits for confirmation (as resume).", chat="own", words="one", run=True,
            arguments=[PROJECT], options=RUN_OPTIONS),
    Command("amend", "Build", "amend [@PROJECT] CHANGE", "change a plan that waits for confirmation",
            "Change a plan that waits for confirmation, before anything is built: the spec is given again with the "
            "change and planned again, and it waits again (build it with tinker confirm).", chat="own", words="any",
            run=True, arguments=[Argument("words", "+", "[@PROJECT] CHANGE", "the change")], options=RUN_OPTIONS,
            example="amend also handle negative numbers"),
    Command("rename", "Build", "rename [@PROJECT] NAME", "rename the folder of a project whose plan waits",
            "Rename a project's folder while its plan waits for confirmation, before anything is built in it (what is "
            "built there may name its folder).", chat="own", words="one",
            arguments=[Argument("words", "+", "[@PROJECT] NAME", "the folder's new name")], options=("workdir",),
            example="rename calculator"),
    Command("add", "Build", "add [@PROJECT] FEATURE", "add a feature to a built project",
            "Add a feature to a built project (this folder's, or the project named). Its plan waits for your confirm "
            "first.", chat="long", words="any", run=True,
            arguments=[Argument("words", "+", "[@PROJECT] FEATURE", "the feature")],
            options=("feature_spec",) + RUN_OPTIONS, example="add keep a history of results"),
    Command("fix", "Build", "fix [@PROJECT] BUG", "fix a bug: what you do, what happens, what should",
            "Fix a bug: describe what you do, what happens and what should happen. A test shows the bug, and it waits "
            "for your confirm; then the code is fixed so it passes and every other test still passes.", chat="long",
            words="any", run=True, arguments=[Argument("words", "+", "[@PROJECT] BUG", "the bug")], options=RUN_OPTIONS,
            example='fix typing 2/0 crashes, it should say "cannot divide by zero"'),
    Command("status", "Build", "status [@PROJECT]", "a project's spec points and whether they pass",
            "Show a project's spec points and whether their tests pass.", chat="quick", words="one",
            arguments=[PROJECT], options=("workdir",)),
    Command("runs", "Build", "runs", "every project, newest first", "List every project, newest first.", chat="quick"),
    Command("log", "Build", "log [@PROJECT] [--calls]", "what a project's runs printed (or its model calls)",
            "Show what this folder's project (or the one named) printed in its runs; --calls shows its model calls.",
            chat="quick", words="one", arguments=[PROJECT], options=("calls", "workdir")),
    Command("learn", "Learn", "learn WHAT", "learn a language, a tool or a domain",
            "Learn something and keep it for the builds that need it. Tinker tells what it is: a language "
            "(\"Python 3.14\"), a tool (\"Flask\"), a domain (\"food bank inventory\", researched with its sources "
            "cross-checked), or NAME \"DEFINITION\" (or an alias in your shell): a command of yours.", chat="long",
            words="any", arguments=[Argument("words", "+", "WHAT [DEFINITION]")], example="learn rust",
            rows=[("learn WHAT", "learn a language, a tool or a domain"),
                  ('learn NAME "DEFINITION"', "a command of yours Tinker may use")]),
    Command("memory", "Learn", "memory", "what Tinker has learned",
            "Show what Tinker has learned: toolchains, fixes, lessons, topics, your commands.", chat="quick"),
    Command("doctor", "Set up", "doctor", "what is missing or wrong, and how to fix it",
            "Check what Tinker needs (Ollama, a measured model, an embedding model, the web) and say how to fix what "
            "is missing.", chat="quick"),
    Command("models", "Set up", "models", "the models Ollama has, and which are measured",
            "List the models Ollama has, and which are measured.", chat="quick"),
    Command("calibrate", "Set up", "calibrate NAME", "measure a model once (a minute or a few)",
            "Measure a model once: the largest prompt it reads and the longest reply it writes.", chat="long",
            words="one", arguments=[Argument("name", None, None, "the model's name, as `tinker models` lists it")]),
    Command("use-model", "Set up", "use-model NAME", "the measured model every build uses",
            "Use this measured model for every build from now on.", chat="quick", words="one",
            arguments=[Argument("name", None, None, "the model's name, as `tinker models` lists it")]),
    Command("embed", "Set up", "embed NAME", "the embedding model that indexes what is learned",
            "Use this embedding model to index what is learned, so builds find it by meaning.", chat="quick",
            words="one", arguments=[Argument("name", None, None, "the model's name, as `tinker models` lists it")]),
    Command("use-web", "Set up", "use-web NAME", "the web search tinker learn and --web use: ollama or duckduckgo",
            "Use this web search for tinker learn and --web from now on: ollama (Ollama's web search and fetch, with a "
            "free key in OLLAMA_API_KEY) or duckduckgo (no key).", chat="quick", words="one",
            arguments=[Argument("name", None, None, "the web search to use", choices="backends")]),
    Command("config", "Set up", "config [KEY VALUE]", "the settings, or set one",
            "Show the settings a build uses, or set one: tinker config KEY VALUE (e.g. tinker config test_timeout 90).",
            chat="quick", words="any", arguments=[Argument("words", "*", "KEY VALUE", "the setting and its value")]),
    Command("clear", "Set up", "clear PART... [--yes]", "delete parts of what Tinker keeps: languages, memory, "
            "learned, models, runs, commands",
            "Delete parts of what Tinker keeps, e.g. tinker clear languages memory: languages (toolchains, fixes, "
            "lessons per language), memory (lessons for any language), learned (what tinker learn learned), models "
            "(measurements), runs (the list of projects), commands (your commands).", chat="quick", words="any",
            deletes=True, arguments=[Argument("parts", "+", "PART", "languages, memory, learned, models, runs or "
                                                                    "commands")], options=("yes",)),
    Command("reset", "Set up", "reset [--yes]", "delete everything Tinker keeps (projects stay)",
            "Delete everything Tinker keeps (settings, measured models, what it learned, the list of projects); "
            "projects are not touched.", chat="quick", deletes=True, options=("yes",)),
    Command("help", "Set up", "help [COMMAND]", "this list, or one command's arguments and options", chat="own",
            words="one", arguments=[Argument("command", "?", "COMMAND")]),
    Command("", "Talk", "(no command)", "talk with Tinker here: every command, and a normal chat (exit ends it)",
            cli=False),
    Command("chat", "Talk", "chat MESSAGE", "a normal chat with the model", words="any", example="chat hello"),
    Command("command", "Talk", "command WHAT", "the command line that does it, run once you confirm", chat="own",
            words="any", example="command show the free disk space"),
    Command("research", "Talk", "research TOPIC", "the web searched, answered with its sources", chat="own",
            words="any", example="research the newest Python release"),
    Command("run", "Talk", "run [@PROJECT] ARGS", "run a built project (the last one built, by default) with these "
            "arguments; <<< TEXT gives it input", chat="own", words="any", example="run 5 + 3"),
    Command("stop", "Talk", "stop", "stop the tasks that run (they are saved)", chat="own"),
    Command("emergency", "Talk", "emergency MESSAGE", "stop what runs, answer now, then go on", chat="own",
            words="any", example="emergency is my disk full?"),
    Command("exit", "Talk", "exit", "end the conversation (in a chat, once confirmed)", chat="own", cli=False),
    Command("channel", "Channels", "channel ACTION", "the chat channels", chat="quick", words="any",
            rows=[("channel list", "the chat channels (Telegram), and which are enabled"),
                  ("channel add", "add one: its kind, a name, its token; pairs your chat"),
                  ("channel remove NAME", "delete a channel and its token"),
                  ("channel enable NAME", "every task reports to it; tinker channel start reads it"),
                  ("channel disable NAME", "stop using it (channels start disabled)"),
                  ("channel start [NAME]", "read the chat: tasks, status, stop, emergency, commands, or just chat")]),
]
BY_NAME = {command.name: command for command in COMMANDS if command.name}
GROUPS = ("Build", "Learn", "Set up", "Talk", "Channels")
TERMINAL_ONLY = ("add", "remove", "start")  # tinker channel's actions that ask in a terminal (a token, a pairing)


def named(*kinds, words=None):
    """The names of the commands a conversation takes as one of these kinds (and these words after it)."""
    return tuple(c.name for c in COMMANDS if c.chat in kinds and (words is None or c.words == words))


def ref(word):
    """The project a word names, as @ID or @folder (without its @); None for any other word."""
    word = str(word or "").strip()
    return word[1:] if word.startswith("@") and len(word) > 1 else None


def refs(words):
    """(the project the first word names, or "", the other words)."""
    words = [w for w in words if str(w).strip()]
    name = ref(words[0]) if words else None
    return (name, words[1:]) if name else ("", words)
