import os
import re
from collections import namedtuple
lazy import subprocess
lazy from pathlib import Path

from ..jobs import calls_log
from ..memory.memories import EPISODE_BOOKKEEPING, Memories, Scratchpad
from .markup import Markup, md
lazy from ..model.models import Models, OllamaError
lazy from ..model.thinking import RequestReview
lazy from ..prompts import Part, Prompts, Reply
lazy from ..ranking import Embeddings, Retriever
lazy from ..text import field

COMMAND_NAMING_MAX_TOKENS = 300
CHAT_COMMAND_TIMEOUT = 120
CHAT_COMMAND_OUTPUT_SHOWN_CHARS = 3000


ShellRun = namedtuple("ShellRun", "code output")


Question = namedtuple(
    "Question", "runs cancels held wanted naming", defaults=(None, lambda: None, None, None, False)
)


def thread_reply(episode):
    if episode.get("status") == "remembered":
        return "\n".join(episode["reply"].splitlines()[1:-1])
    return episode["reply"]


class Shell:
    CHANGES_RE = re.compile(
        r"\b(rm|rmdir|mv|dd|mkfs\w*|chmod|chown|kill|killall|pkill|sudo|shutdown|reboot|"
        r"truncate)\b|(?<![\d&>])>(?!&)|\bgit\s+(push|reset|clean|checkout|rebase)\b|"
        r"\b(brew|pip3?|npm|cargo)\s+(install|uninstall|remove)\b|\s-delete\b|-exec\s+(rm|mv)\b|"
        r"\bxargs\s+(rm|mv)\b|\bsed\s+-i\b"
    )
    LOGIN_SHELL = os.environ.get("SHELL") or "/bin/sh"

    def __init__(self, talk, folder, questions):
        self.talk, self.folder, self.questions = talk, folder, questions
        self.shell = self.LOGIN_SHELL

    def name(self, channel, message, now):
        self.questions.ask(channel, message.chat, Question(naming=True))

        def shown(ids):
            question = self.questions.waiting(channel, message.chat)
            if question and question.held:
                question.held["shown"] = ids

        self.talk.answer(channel, message, now, write=self.propose, on_shown=shown)

    def propose(self, channel, message, state, tokens):
        if not Models.quick():
            self.questions.answered(channel, message.chat)
            return "⚠️ **No model is in use yet:** `tinker model NAME` picks one."
        system = Prompts.COMMAND_SYSTEM.format(
            system=os.uname().sysname, shell=Path(self.shell).name, folder=self.folder
        ) + remembered_part(recall(message.text, ("tool",)))
        level = reviewed_level(message.text)
        try:
            data = json_answer(
                self.talk.ask(
                    channel,
                    message.chat,
                    [{"role": "system", "content": system}, {"role": "user", "content": message.text}],
                    COMMAND_NAMING_MAX_TOKENS,
                    level,
                )
            )
        except OllamaError as exc:
            self.questions.answered(channel, message.chat)
            return md(t"⚠️ **The model did not answer:** {first_words(exc)}")
        line, does = field(data, "command"), field(data, "does")
        if not line or "\n" in line:
            self.questions.answered(channel, message.chat)
            return md(t"🤷 **No command found:** {does or 'describe it another way'}")
        proposed = {"line": line, "asked": message.text, "level": level, "shown": None}
        self.wait_for_confirm(channel, message.chat, proposed)
        return self.proposal(line, does)

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

    def proposal(self, line, does):
        warning = (
            "\n⚠️ **It changes or deletes things:** read it before you confirm."
            if self.CHANGES_RE.search(line)
            else ""
        )
        return md(
            t"🖥 **Run this?**\n{line:block}\n{does}{warning:md}\n"
            t"👉 `/confirm` to run it · anything else cancels it"
        )

    def take(self, channel, chat):
        question = self.questions.waiting(channel, chat)
        if not question or not question.held:
            return None
        return self.questions.answered(channel, chat).held

    def drop(self, channel, chat, proposed):
        channel.show(chat, proposed["shown"], f"🚫 _Not run:_ `{proposed['line']}`", done=True)

    def run(self, channel, chat, proposed):
        line = proposed["line"]
        sent = channel.show(chat, proposed["shown"], f"▶️ **Running** `{line}` …")
        code, output = in_shell(self.shell, line, self.folder)
        Scratchpad.write(
            {"chat": chat, "request": proposed.get("asked", ""), "line": line, "output": output, "code": code}
        )
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


def remembered_part(recalled):
    if not recalled:
        return ""
    lines = [recalled_line(item) for item in recalled]
    return f"\n\n{Part(Prompts.REMEMBERED, Part.bullets(lines)).render()}"


def recalled_line(item):
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
        return {}
    return data if isinstance(data, dict) else {}


def text_of(output):
    return output.decode(errors="replace") if isinstance(output, bytes) else (output or "")


def reviewed_level(text, thread=""):
    level, _ = RequestReview(calls_log()).level(text, thread=thread)
    return level


def first_words(text, size=160):
    return " ".join(str(text).split())[:size]
