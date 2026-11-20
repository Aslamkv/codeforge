import os
import re
import shlex
from collections import namedtuple
lazy import shutil
lazy import subprocess
lazy from pathlib import Path

from ..jobs import calls_log
from ..memory.memories import EPISODE_BOOKKEEPING, Memories, Scratchpad
from ..settings import EPISODE_FOUND_CHARS
from .markup import Markup, md
lazy from ..model.models import Models, OllamaError
lazy from ..model.thinking import RequestReview
lazy from ..prompts import Part, Prompts, Reply
lazy from ..ranking import Embeddings, Retriever
lazy from ..text import field

COMMAND_NAMING_MAX_TOKENS = 300
CHAT_COMMAND_TIMEOUT = 120
CHAT_COMMAND_OUTPUT_SHOWN_CHARS = 3000
CANT_BE_UNDONE = "can't be undone"
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
SHELL_BUILTINS_AND_KEYWORDS = frozenset(
    "cd export source . alias unset set eval exec echo printf test [ [[ if for while until case function"
    " { ( ! time true false pwd read type command builtin trap wait kill ulimit umask pushd popd history"
    " jobs fg bg disown which whence where noglob nocorrect setopt".split()
)


ShellRun = namedtuple("ShellRun", "code output")


Question = namedtuple(
    "Question",
    "runs cancels held wanted naming amends",
    defaults=(None, lambda: None, None, None, False, None),
)


def names_a_command(line):
    word = first_word(line)
    return bool(word) and (word in SHELL_BUILTINS_AND_KEYWORDS or shutil.which(word) is not None)


def first_word(line):
    try:
        words = shlex.split(line)
    except ValueError:
        return None
    return next((word for word in words if not ASSIGNMENT.fullmatch(word)), None)


def thread_reply(episode):
    if episode.get("status") == "remembered":
        return "\n".join(episode["reply"].splitlines()[1:-1])
    return episode["reply"]


class Checked:
    ROOT_VERBS = {"sudo", "su", "doas"}
    UNDONE_VERBS = {"rm", "dd", "kill"}
    UNDONE_FLAGS = {"git reset": {"--hard"}, "git push": {"--force", "-f"}, "chmod": {"-R"}}
    OVERWRITES = {">", ">|"}
    FIND_RUNS = {"-exec", "-execdir", "-ok", "-okdir"}
    READ_ONLY_COMMANDS = frozenset(
        "ls du df stat wc cat head tail file sort uniq grep pwd find echo which date whoami uname tree"
        " basename dirname realpath readlink id".split()
    )
    WRITING_OPTIONS = {
        "find": re.compile(r"-(delete|exec|execdir|ok|okdir|fprint|fprint0|fprintf|fls)"),
        "sort": re.compile(r"-[^-]*o.*|--output.*|--compress-program.*"),
        "tree": re.compile(r"-o.*"),
        "file": re.compile(r"-[^-]*C.*|--compile"),
        "date": re.compile(r"-[^-]*s.*|--set.*"),
    }
    UNIQ_OPTIONS_WITH_VALUES = {"-f", "-s", "-w"}

    def __init__(self, line):
        self.line = line

    @classmethod
    def of(cls, line, folder, known=()):
        try:
            commands = cls.commands(line)
        except ValueError:
            return UndoneLine(line)
        if any(cls.is_root(words) for words in commands):
            return RootLine(line)
        if any(cls.is_undone(words, Path(folder)) for words in commands):
            return UndoneLine(line)
        if cls.read_only(line) and cls.shaped_like(line, Path(folder), known):
            return AtOnceLine(line)
        return PlainLine(line)

    @classmethod
    def shaped_like(cls, line, folder, known):
        shape = cls.shape(line, folder)
        return any(cls.shape(other, folder) == shape for other in known)

    @classmethod
    def shape(cls, line, folder):
        try:
            commands = cls.commands(line)
        except ValueError:
            return None
        return [[word for word in words if not cls.is_path(word, folder)] for words in commands]

    @staticmethod
    def is_path(word, folder):
        return "/" in word or "~" in word or (folder / word).exists()

    @classmethod
    def read_only(cls, line):
        if "$(" in line or "`" in line:
            return False
        try:
            commands = cls.commands(line)
        except ValueError:
            return False
        return bool(commands) and all(cls.only_reads(words) for words in commands)

    @classmethod
    def only_reads(cls, words):
        verb = Path(words[0]).name
        writing = cls.WRITING_OPTIONS.get(verb)
        return (
            verb in cls.READ_ONLY_COMMANDS
            and not cls.redirects(words)
            and not (writing and any(writing.fullmatch(word) for word in words[1:]))
            and not (verb == "uniq" and cls.names_uniq_output(words[1:]))
        )

    @classmethod
    def redirects(cls, words):
        return any(
            cls.is_redirection(word) and not cls.writes_nothing(word, target)
            for word, target in zip(words, [*words[1:], ""])
        )

    @staticmethod
    def is_redirection(word):
        return bool(set(word) & set("<>")) and set(word) <= set("<>&|")

    @staticmethod
    def writes_nothing(redirection, target):
        duplicates = redirection.endswith("&") and (target.isdigit() or target == "-")
        return target == os.devnull or duplicates

    @classmethod
    def names_uniq_output(cls, arguments):
        operands, taking_value = [], False
        for word in arguments:
            if taking_value:
                taking_value = False
            elif word in cls.UNIQ_OPTIONS_WITH_VALUES:
                taking_value = True
            elif word == "-" or not word.startswith("-"):
                operands.append(word)
        return len(operands) > 1

    @staticmethod
    def commands(line):
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        commands, words = [], []
        for token in lexer:
            if token and set(token) <= set(";&|()"):
                commands.append(words)
                words = []
            else:
                words.append(token)
        return [words for words in [*commands, words] if words]

    @classmethod
    def is_root(cls, words):
        verb = Path(words[0]).name
        return verb in cls.ROOT_VERBS or (
            verb == "osascript" and "with administrator privileges" in " ".join(words)
        )

    @classmethod
    def is_undone(cls, words, folder):
        verb = Path(words[0]).name
        flagged = cls.UNDONE_FLAGS.get(" ".join([verb, *words[1:2]]), cls.UNDONE_FLAGS.get(verb, set()))
        return (
            verb in cls.UNDONE_VERBS
            or bool(flagged & set(words))
            or any(
                word in cls.OVERWRITES and target != os.devnull and (folder / target).exists()
                for word, target in zip(words, words[1:])
            )
            or (verb == "find" and cls.finds_to_undo(words[1:], folder))
        )

    @classmethod
    def finds_to_undo(cls, arguments, folder):
        return "-delete" in arguments or any(
            word in cls.FIND_RUNS and cls.is_undone(arguments[at + 1 :], folder)
            for at, word in enumerate(arguments[:-1])
        )


class RootLine(Checked):
    def offer(self, shell, channel, chat, proposed):
        shell.questions.answered(channel, chat)
        return md(t"⛔ **sudo isn't supported yet:** {self.line:code} needs root, so it is not run.")


class PlainLine(Checked):
    irreversible = False

    def offer(self, shell, channel, chat, proposed):
        proposed["undo"] = self.undo(proposed["undo"])
        proposed["irreversible"] = self.irreversible
        return self.offered(shell, channel, chat, proposed)

    def offered(self, shell, channel, chat, proposed):
        shell.wait_for_confirm(channel, chat, proposed)
        return shell.proposal(proposed)

    def undo(self, undo):
        return undo


class AtOnceLine(PlainLine):
    def offered(self, shell, channel, chat, proposed):
        proposed["at_once"] = True
        shell.start_at_once(channel, chat, proposed)
        return shell.running(proposed)


class UndoneLine(PlainLine):
    irreversible = True

    def undo(self, undo):
        return CANT_BE_UNDONE


class Shell:
    LOGIN_SHELL = os.environ.get("SHELL") or "/bin/sh"

    def __init__(self, talk, folder, questions):
        self.talk, self.folder, self.questions = talk, folder, questions
        self.shell = self.LOGIN_SHELL
        self.starting = None

    def name(self, channel, message, now, thread):
        self.questions.ask(channel, message.chat, Question(naming=True))
        self.talk.answer(
            channel,
            message,
            now,
            write=lambda *asked: self.propose(*asked, thread),
            on_shown=lambda ids: self.proposal_shown(channel, message.chat, ids),
        )

    def proposal_shown(self, channel, chat, ids):
        starting, self.starting = self.starting, None
        if starting:
            starting["shown"] = ids
            self.run(channel, chat, starting)
            return
        question = self.questions.waiting(channel, chat)
        if question and question.held:
            question.held["shown"] = ids
            self.offer_run(channel, chat, ids)

    def offer_run(self, channel, chat, ids):
        pass

    def propose(self, channel, message, state, tokens, thread):
        if not Models.quick():
            self.questions.answered(channel, message.chat)
            return "⚠️ **No model is in use yet:** `tinker model NAME` picks one."
        latest = thread[-1]["text"]
        recalled = recall(latest, ("tool",))
        system = (
            Prompts.COMMAND_SYSTEM.format(
                system=system_name(os.uname().sysname), shell=Path(self.shell).name, folder=self.folder
            )
            + remembered_part(recalled)
            + recalled_tool_rule(recalled)
        )
        level = reviewed_level(latest)
        try:
            data = json_answer(
                self.talk.ask(
                    channel,
                    message.chat,
                    [{"role": "system", "content": system}, {"role": "user", "content": thread_text(thread)}],
                    COMMAND_NAMING_MAX_TOKENS,
                    level,
                )
            )
        except OllamaError as exc:
            self.questions.answered(channel, message.chat)
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")
        if data is None:
            self.questions.answered(channel, message.chat)
            return "🤷 **Couldn't read the model's answer:** describe it another way"
        line, does = field(data, "line"), field(data, "does")
        if not line or "\n" in line or not names_a_command(line):
            self.questions.answered(channel, message.chat)
            return md(t"🤷 **No command found:** {does or 'describe it another way'}")
        proposed = {
            "line": line,
            "asked": message.text,
            "level": level,
            "shown": None,
            "does": does,
            "touches": touched_paths(data),
            "undo": field(data, "undo"),
        }
        known = [item["text"] for item in recalled if item.get("kind") == "tool"]
        return Checked.of(line, self.folder, known).offer(self, channel, message.chat, proposed)

    def wait_for_confirm(self, channel, chat, proposed):
        self.questions.ask(
            channel,
            chat,
            Question(
                runs=lambda: self.run(channel, chat, proposed),
                cancels=lambda: self.drop(channel, chat, proposed),
                held=proposed,
            ),
        )

    def start_at_once(self, channel, chat, proposed):
        self.questions.answered(channel, chat)
        self.starting = proposed

    @staticmethod
    def running(proposed):
        return md(t"▶️ **Running:**\n{proposed['line']:block}")

    def proposal(self, proposed):
        touches = ", ".join(md(t"{path:code}") for path in proposed["touches"]) or "nothing"
        return md(
            t"🖥 **I think you meant to do it like this:**\n{proposed['line']:block}\n**Am I right?**\n"
            t"{proposed['does']}\n"
            t"**Touches:** {touches:md}\n{undo_shown(proposed):md}\n"
            t"👉 `/confirm` to run it · anything else cancels it"
        )

    def take(self, channel, chat):
        question = self.questions.waiting(channel, chat)
        if not question or not question.held:
            return None
        return self.questions.answered(channel, chat).held

    def drop(self, channel, chat, proposed):
        channel.show(chat, proposed["shown"], f"🚫 _Not run:_ `{proposed['line']}`", done=True)

    @staticmethod
    def kept(chat, proposed, code, output):
        return Scratchpad.write(
            {
                "chat": chat,
                "request": proposed.get("asked", ""),
                "line": proposed["line"],
                "output": output,
                "code": code,
            }
        )

    def run(self, channel, chat, proposed):
        line = proposed["line"]
        sent = channel.show(chat, proposed["shown"], f"▶️ **Running** `{line}` …")
        code, output = in_shell(self.shell, line, self.folder)
        self.kept(chat, proposed, code, output)
        head = md(t"{'✅' if code == 0 else '❌'} {line:code} · _exit {code}_")
        folded = self.folded(output)
        tells = bool(output) and bool(Models.quick())
        sent = channel.show(chat, sent, f"{head}\n{folded}", done=not tells)
        if not tells:
            return
        with self.talk.pausing_jobs():
            told = self.told(channel, chat, proposed, code, output)
        channel.show(chat, sent, f"{head}\n{told}\n{folded}" if told else f"{head}\n{folded}", done=True)

    @staticmethod
    def folded(output):
        if not output:
            return "_(no output)_"
        shown = (
            output
            if len(output) <= CHAT_COMMAND_OUTPUT_SHOWN_CHARS
            else "… (the start is left out)\n" + output[-CHAT_COMMAND_OUTPUT_SHOWN_CHARS:]
        )
        return f"```output\n{Markup.fenced(shown)}\n```"

    def told(self, channel, chat, proposed, code, output):
        try:
            return self.talk.tell(
                channel,
                chat,
                proposed["asked"],
                proposed["line"],
                f"Exit code: {code}\n",
                output,
                proposed["level"],
            )
        except OllamaError:
            return ""


def in_shell(shell, line, folder, given=None):
    try:
        done = subprocess.run(
            [shell, "-lc", line],
            cwd=folder,
            text=True,
            capture_output=True,
            input=given,
            stdin=None if given else subprocess.DEVNULL,
            timeout=CHAT_COMMAND_TIMEOUT,
        )
        return ShellRun(done.returncode, (done.stdout + done.stderr).strip())
    except subprocess.TimeoutExpired as exc:
        return ShellRun(
            f"stopped after {CHAT_COMMAND_TIMEOUT}s", (text_of(exc.stdout) + text_of(exc.stderr)).strip()
        )
    except OSError as exc:
        return ShellRun("not started", str(exc))


def thread_text(thread):
    return "\n\n".join(f"{turn['said']}: {turn['text'][:EPISODE_FOUND_CHARS]}" for turn in thread)


def touched_paths(data):
    touches = data.get("touches")
    return [str(path) for path in touches] if isinstance(touches, list) else []


def undo_shown(proposed):
    undo = proposed["undo"]
    if undo == CANT_BE_UNDONE:
        return "⚠️ **It can't be undone:** read it before you confirm."
    if undo:
        return md(t"**Undo:** {undo:code}")
    return backups_put_back(proposed["touches"])


def backups_put_back(touches):
    if not touches:
        return "**Undo:** nothing to undo"
    paths = ", ".join(md(t"{path:code}") for path in touches)
    return md(t"**Undo:** puts back the backups of {paths:md}")


def system_name(sysname):
    return {"Darwin": Prompts.MACOS}.get(sysname, sysname)


def recalled_tool_rule(recalled):
    if not any(item.get("kind") == "tool" for item in recalled):
        return ""
    return f"\n\n{Prompts.COMMAND_RECALLED}"


def remembered_part(recalled):
    if not recalled:
        return ""
    lines = [recalled_line(item) for item in recalled]
    return f"\n\n{Part(Prompts.REMEMBERED, Part.bullets(lines)).render()}"


def recalled_line(item):
    if item.get("kind") == "tool":
        return f"{item['gist']} → {item['text']}"
    if item["id"].startswith("m"):
        return item["gist"]
    return "; ".join(
        f"{key}: {value}"
        for key, value in shown_episode(item).items()
        if key not in EPISODE_BOOKKEEPING and value not in ("", [])
    )


def shown_episode(episode):
    if "reply" not in episode:
        return episode
    return {**episode, "reply": thread_reply(episode)}


def recall(request, kinds=None):
    return Memories(Retriever(Embeddings.configured())).recall(request, kinds=kinds)


def json_answer(text):
    try:
        data = Reply(text).json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(output):
    return output.decode(errors="replace") if isinstance(output, bytes) else (output or "")


def reviewed_level(text, thread=""):
    level, _ = RequestReview(calls_log()).level(text, thread=thread)
    return level


def first_words(text, size=160):
    return " ".join(str(text).split())[:size]
