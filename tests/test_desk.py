import contextlib
import datetime
import io
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest
from conftest import ACCENT, DIM, add_back, ask, confirmed_trait, quick_model_replying, tinker_runs
from test_console import (
    Recorder,
    colour_terminal,
    drawn_after,
    piped_console,
    ruled_console,
    sent_back_shown,
    typing,
    wait_until,
)
from test_jobs import put_job
from test_reports import PLAN, everyone, keep_job, snapshot
from test_shell import REMEMBERED_REPLY, chatted_episode, pausing_into, remembering
from test_skills import made_skill

import tinkerkit.chat.channels as channels
import tinkerkit.chat.reports as reports
import tinkerkit.desk as desk_module
import tinkerkit.jobs as jobs
from tinkerkit.build.project import Runs
from tinkerkit.chat.console import ConsoleChannel, ConsoleScreen
from tinkerkit.chat.ledger import DONE_NOTE_KEPT_SECONDS, Ledger
from tinkerkit.chat.markup import Message
from tinkerkit.chat.reports import Progress, Reporter
from tinkerkit.chat.shell import Question, first_words, text_of
from tinkerkit.commands import BY_NAME, COMMANDS, GROUPS, command_of
from tinkerkit.desk import (
    ALL_FOR_NOW,
    ChatState,
    Desk,
    InsideInterview,
    InsideJob,
    InsideReading,
    JobFollower,
    NoInterview,
    NoWaitingPlan,
    Talk,
    WaitingName,
    WaitingPlan,
    after_word,
    built_project,
    first_lines,
    thread_episodes,
    thread_line,
    welcome,
)
from tinkerkit.jobs import Queue, Runner, began_at
from tinkerkit.memory.interview import UNREADABLE, Interview
from tinkerkit.memory.memories import Questions as KeptQuestions
from tinkerkit.memory.memories import Scratchpad
from tinkerkit.messages import DIMMED, HEADING, Messages, Text, next_page_words
from tinkerkit.messages import say as shared_say
from tinkerkit.model.models import Cut, Models, QuickModel, ThinkLevels
from tinkerkit.settings import EPISODE_GIST_CHARS, Config, Home
from tinkerkit.shelf import Shelf
from tinkerkit.skills import Skills
from tinkerkit.store import Log


@pytest.fixture(autouse=True)
def runner_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(Runner, "host", classmethod(lambda cls: calls.append("host") or True))
    monkeypatch.setattr(Runner, "pause", classmethod(lambda cls, hosting: calls.append(("pause", hosting))))
    return calls


@pytest.mark.spec("CNV-3")
@pytest.mark.parametrize(
    "text,command",
    [
        ("/status", "status"),
        ("/Status", "status"),
        ("/status of my build please", "status"),
        ("status", ""),
        ("/task a calculator", "task"),
        ("models are getting better", ""),
        ("hello", ""),
        ("/hello", ""),
        ("", ""),
        ("/", ""),
        ("/use_model", ""),
        ("/model calibrate", "model"),
        ("/status@tinker_bot", "status"),
    ],
)
def test_command_of(text, command):
    assert command_of(text, Desk.COMMANDS) == command


@pytest.mark.spec("CNV-3")
def test_said_command_gives_a_fixed_phrase_of_a_chat_command_as_slash():
    for said in ("clear chat", "status", "jobs running", "model recipe all", "exercise status"):
        assert desk_module.said_command(said) == f"/{said}"
    for said in ("task", "fix", "ask"):
        assert desk_module.said_command(said) == ""
    assert desk_module.said_command("Clear the chat, please!") == "/clear chat"
    assert desk_module.said_command("clear the chat and the history") == ""
    assert desk_module.said_command("status of my build") == ""


@pytest.mark.spec("CNV-3")
def test_said_command_takes_show_words_and_a_left_out_command_name():
    assert desk_module.said_command("show model recipe") == "/model recipe"
    assert desk_module.said_command("show recipe") == "/model recipe"
    assert desk_module.said_command("see the recipe all") == "/model recipe all"
    assert desk_module.said_command("chat") == ""
    assert desk_module.said_command("show me the chat") == ""


@pytest.mark.spec("CNV-3", "SYS-3")
def test_said_command_drops_question_words():
    assert desk_module.said_command("What's the time now?") == "/system time"
    assert desk_module.said_command("what time is it now?") == "/system time"
    assert desk_module.said_command("what is my battery") == "/system battery"
    assert desk_module.said_command("ram usage") == "/system ram"
    assert desk_module.said_command("disk usage") == "/system disk"


def test_said_filler_holds_no_word_of_a_phrase():
    words = {word for phrase in desk_module.said_phrases() for word in phrase}
    assert not words & set(desk_module.SAID_FILLER)


@pytest.mark.spec("CNV-6")
def test_console_shows_a_line_sent_again_after_the_prompt_it_was_typed_at(monkeypatch):
    monkeypatch.setattr("tinkerkit.chat.console.typed_here", lambda: True)
    console = ruled_console(monkeypatch, 30)
    console.now_inside("console", InsideInterview(NoInterview()))
    assert drawn_after(lambda: console.show_again("hi")) == sent_back_shown(
        "hi", columns=30, prompt="💬 interact › "
    )


@pytest.mark.spec("CNV-6")
@pytest.mark.parametrize("mode", [InsideReading(3, "Small Craft"), InsideInterview(NoInterview())])
def test_console_keeps_the_plain_prompt_for_piped_input_inside_a_mode(monkeypatch, mode):
    colour_terminal(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    prompts = typing(monkeypatch, "hello there")
    console = ConsoleChannel()
    console.now_inside("console", mode)
    console.read()
    assert prompts == [ConsoleScreen.PROMPT] * 2


@pytest.mark.spec("CNV-6")
def test_the_banner_names_tinker_the_model_the_projects_folder_and_the_chats(monkeypatch):
    settings = {"model": "qwen3", "embed_model": "nomic-embed"}
    monkeypatch.setattr(Config, "get", lambda key: settings.get(key))
    root = Path.home() / "Projects"
    with_chats = Desk([ConsoleChannel(), Recorder("telegram")], root)
    assert with_chats.banner_lines() == [
        "🔧 Tinker",
        "model qwen3",
        "embedding nomic-embed",
        "projects ~/Projects",
        "chats telegram",
        "help lists commands · exit quits",
    ]
    settings.clear()
    alone = Desk([ConsoleChannel()], "/tmp")
    assert alone.banner_lines()[1:4] == [
        "model no model yet",
        "embedding none",
        f"projects {Path('/tmp').resolve()}",
    ]
    assert len(alone.banner_lines()) == 5


@pytest.mark.spec("CNV-6", "CHN-11")
def test_the_terminal_banner_and_a_chat_banner_name_the_chats_only(desk, monkeypatch):
    monkeypatch.setattr(Config, "get", lambda key: None)
    console, telegram, stateless, drawn = ConsoleChannel(), Recorder("Telegram"), Recorder("rec"), []
    telegram.state = "connecting…"
    monkeypatch.setattr(console, "banner", lambda lines: drawn.append(list(lines)))
    listening = Desk([console, telegram, stateless], Path.cwd())
    listening.said_status = "🟢 **Tinker** · idle"
    listening.open_banner()
    assert "chats Telegram, rec" in drawn[0] and not any("connecting" in line for line in drawn[0])
    listening.start_chat(telegram)
    assert any("chats Telegram, rec" in text and "connecting" not in text for text in telegram.texts("send"))


@pytest.mark.spec("CNV-6", "CHN-11")
def test_a_chat_state_that_changes_after_the_banner_prints_a_dimmed_line_and_draws_no_banner(
    desk, monkeypatch
):
    terminal = colour_terminal(monkeypatch, 80)
    console, telegram, drawn = ConsoleChannel(), Recorder("Telegram"), []
    telegram.state = "connecting…"
    monkeypatch.setattr(console, "banner", lambda lines: drawn.append(list(lines)))
    listening = Desk([console, telegram], Path.cwd())
    listening.open_banner()
    listening.say_state_changes()
    telegram.state = "connected (IPv4)"
    listening.say_state_changes()
    listening.say_state_changes()
    shown = terminal.getvalue()
    assert "connecting…" not in shown and shown.count("Telegram · connected (IPv4)") == 1
    assert f"{DIM}Telegram · connected (IPv4)" in shown and len(drawn) == 1


@pytest.mark.spec("CNV-6")
def test_the_conversation_opens_with_the_banner_before_the_status(monkeypatch):
    terminal = colour_terminal(monkeypatch, 80)
    typing(monkeypatch)
    desk = Desk([ConsoleChannel()], Path.cwd())
    monkeypatch.setattr(desk.talk, "warm", lambda: None)
    desk.run()
    shown = terminal.getvalue()
    assert shown.startswith(f"{ACCENT}╭") and shown.index("🔧 Tinker") < shown.index("╰") < shown.index(
        "idle"
    )


@pytest.mark.spec("REM-6")
def test_a_chat_reply_adds_what_tinker_remembers(measured_model, fake_ollama):
    remembering("fact", "the person keeps bees", "they keep bees in the garden")
    fake_ollama.reply("none").reply("low: a plain request").reply("hi")
    assert Talk("task").reply(Recorder(), Message("1", "tell me about the bees")) == "hi"
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert "What you remember" in system and "the person keeps bees" in system


def remembered_answer(question, answer):
    from tinkerkit.memory.memories import Memories
    from tinkerkit.ranking import Retriever

    now = datetime.datetime(2026, 11, 2, 9, 0)
    return Memories(Retriever(None)).add("answer", question, answer, ["e1"], now, "always")


@pytest.mark.spec("REM-12", "CNV-24")
def test_a_chat_reply_comes_from_a_remembered_answer_without_the_model(measured_model, fake_ollama):
    from tinkerkit.memory.memories import Memories
    from tinkerkit.ranking import Retriever

    memory = remembered_answer("how tall is everest in km", "About 8.85 km.")
    reply = Talk("task").reply(Recorder(), Message("1", "How tall is Everest in km?"))
    assert reply == (
        '💭 From memory (you asked "how tall is everest in km" on 2026-11-02):\n'
        "About 8.85 km.\n_`/fresh` asks the model again._"
    )
    assert fake_ollama.calls == []
    [kept] = [item for item in Memories(Retriever(None)).living() if item["id"] == memory["id"]]
    assert kept["recalled"] == 1


@pytest.mark.spec("REM-12")
def test_a_remembered_answer_needs_every_word_of_the_message(measured_model, fake_ollama):
    remembered_answer("how tall is everest in km", "About 8.85 km.")
    fake_ollama.reply("none").reply("29,032 feet")
    assert Talk("task").reply(Recorder(), Message("1", "how tall is everest in feet")) == "29,032 feet"


@pytest.mark.spec("REM-12")
def test_a_retired_answer_or_another_kind_is_not_a_reply(measured_model, fake_ollama):
    from tinkerkit.memory.memories import Memories
    from tinkerkit.ranking import Retriever

    memory = remembered_answer("how tall is everest in km", "About 8.85 km.")
    Memories(Retriever(None)).retire(memory["id"], "outdated")
    remembering("fact", "how tall is everest in km", "About 8.85 km.")
    fake_ollama.reply("none").reply("8.8 km")
    assert Talk("task").reply(Recorder(), Message("1", "how tall is everest in km")) == "8.8 km"


@pytest.mark.spec("REM-1")
def test_a_reply_from_memory_makes_a_remembered_episode():
    reply = desk_module.Remembered("💭 From memory (you asked ...):\nAbout 8.85 km.")
    episode = desk_module.answered_episode(Message("1", "how tall is everest in km"), [reply], [])
    assert episode["status"] == "remembered"
    assert desk_module.answered_episode(Message("1", "hi"), ["hello"], [])["status"] == "answered"


@pytest.mark.spec("REM-6")
def test_a_chat_reply_with_nothing_recalled_keeps_its_prompt(measured_model, fake_ollama):
    remembering("fact", "the person keeps bees", "they keep bees in the garden")
    fake_ollama.reply("none").reply("low: a plain request").reply("hi")
    assert Talk("task").reply(Recorder(), Message("1", "weather today")) == "hi"
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert system == desk_module.Prompts.CHAT_SYSTEM.format(style=Recorder.STYLE, state="")


THREAD_NOW = datetime.datetime(2026, 11, 11, 12, 0, 0)


def written_minutes_ago(episode, minutes):
    return Scratchpad.write(episode, THREAD_NOW - datetime.timedelta(minutes=minutes))


def thread_lines(chat):
    return [thread_line(episode) for episode in thread_episodes(chat, THREAD_NOW)]


@pytest.mark.spec("REM-11")
def test_the_thread_is_the_last_turns_of_the_chat_oldest_first(home_store):
    for number in range(6):
        written_minutes_ago(chatted_episode(number), 10 - number)
    assert thread_lines("1") == [f"question {number} → Answer {number}. More." for number in range(2, 6)]


@pytest.mark.spec("REM-11")
def test_the_thread_keeps_only_this_chats_recent_messages(home_store):
    written_minutes_ago(chatted_episode(0), 31)
    written_minutes_ago(chatted_episode(1), 29)
    written_minutes_ago(chatted_episode(2, chat="2"), 5)
    written_minutes_ago({"chat": "1", "task": "make a game", "status": "done"}, 5)
    assert thread_lines("1") == ["question 1 → Answer 1. More."]
    assert thread_lines("3") == []


@pytest.mark.spec("REM-11")
def test_a_thread_line_is_the_gist_once_dreamt_and_is_cut(home_store):
    for number, changes in enumerate(({"gist": "asked about zero"}, {}, {})):
        episode = written_minutes_ago(chatted_episode(number), 3 - number)
        home_store.put(Scratchpad.EPISODES, episode["id"], dict(episode, dreamt=number < 2, **changes))
    written_minutes_ago(chatted_episode(3, message="x" * 300), 0)
    assert thread_lines("1") == [
        "asked about zero",
        "question 1 → Answer 1. More.",
        "question 2 → Answer 2. More.",
        "x" * EPISODE_GIST_CHARS,
    ]


@pytest.mark.spec("REM-11")
def test_a_thread_line_keeps_the_whole_reply_on_one_line(home_store):
    reply = "First part. Second part!\n\n  Third   part?\nFourth " + "y" * 300
    written_minutes_ago(chatted_episode(0, reply=reply), 0)
    line = "question 0 → First part. Second part! Third part? Fourth " + "y" * 300
    assert thread_lines("1") == [line[:EPISODE_GIST_CHARS]]


@pytest.mark.spec("REM-11")
def test_a_remembered_thread_line_gives_only_the_answer(home_store):
    written_minutes_ago(chatted_episode(0, reply=REMEMBERED_REPLY, status="remembered"), 0)
    assert thread_lines("1") == ["question 0 → The answer. More of it."]


@pytest.mark.spec("CNV-18")
def test_talk_reply(measured_model, fake_ollama):
    talk, channel = Talk("task; status"), Recorder()
    Scratchpad.write(chatted_episode(0))
    fake_ollama.reply("none").reply("low: a plain request").reply("answer 1")
    assert talk.reply(channel, Message("1", "question 1")) == "answer 1"
    route, messages = fake_ollama.calls[-3]["messages"], fake_ollama.calls[-1]["messages"]
    assert route[0]["content"].startswith(
        desk_module.Prompts.CHAT_ROUTE_SYSTEM.format(index=desk_module.chat_index())
    )
    assert "question 0 → Answer 0." in route[0]["content"] and route[1:] == messages[1:]
    assert messages[0]["role"] == "system" and desk_module.chat_index() not in messages[0]["content"]
    assert "Earlier in this chat" in messages[0]["content"]
    assert "question 0 → Answer 0." in messages[0]["content"]
    assert [message["content"] for message in messages[1:]] == ["question 1"]
    fake_ollama.reply("none").reply("low: a plain request").reply("answer 2")
    talk.reply(channel, Message("2", "question 2"))
    assert "Earlier in this chat" not in system_of(fake_ollama.calls[-1])
    assert fake_ollama.calls[-1]["think"] is None
    assert fake_ollama.calls[-1]["options"]["num_predict"] == (
        desk_module.CHAT_MAX_REPLY_TOKENS + desk_module.CHAT_THINK_TOKENS
    )
    from tinkerkit.model.models import OllamaError

    fake_ollama.reply("none").reply("low: a plain request").reply(OllamaError("runner failed"))
    assert talk.reply(channel, Message("1", "x")).startswith("⚠️ **The model did not answer:**")


class FakeChatState:
    def __init__(self):
        self.named = []

    def everyday(self):
        return "Running now:\nbuild x"

    def for_commands(self, names):
        self.named.append(names)
        return "A spec waits: calc"


def system_of(call):
    return call["messages"][0]["content"]


@pytest.mark.spec("CNV-18")
def test_the_state_reaches_the_prompt(measured_model, fake_ollama):
    fake_ollama.reply("low: a plain request").reply("hi")
    Talk("cmds").reply(Recorder(), Message("1", "hello"), FakeChatState())
    assert system_of(fake_ollama.calls[-1]).endswith("\n\nRunning now:\nbuild x")
    fake_ollama.reply("low: a plain request").reply("hi")
    Talk("cmds").reply(Recorder(), Message("1", "hello"))
    assert "Running now" not in system_of(fake_ollama.calls[-1])


@pytest.mark.spec("CNV-18")
def test_a_named_command_taking_words_gets_a_second_call(measured_model, fake_ollama):
    talk, state = Talk("cmds"), FakeChatState()
    fake_ollama.reply("`/amend`").reply("/amend handle negatives")
    assert talk.reply(Recorder(), Message("1", "also handle negatives"), state) == "/amend handle negatives"
    fill = system_of(fake_ollama.calls[-1])
    assert desk_module.command_details(["amend"]) in fill and fill.endswith("\n\nA spec waits: calc")
    assert "Running now" not in fill and state.named == [["amend"]]
    assert [message["content"] for message in fake_ollama.calls[-1]["messages"][1:]] == [
        "also handle negatives"
    ]
    Scratchpad.write(chatted_episode(0))
    fake_ollama.reply("`/amend`").reply("/amend handle zero")
    talk.reply(Recorder(), Message("1", "and zero"), state)
    assert all("question 0 → Answer 0." in system_of(call) for call in fake_ollama.calls[-2:])


TRAITS_PART = "Who the person is and how they want you to be"


@pytest.mark.spec("REM-16")
def test_both_calls_of_a_chat_reply_get_the_persons_traits(measured_model, fake_ollama):
    confirmed_trait("Answers in short sentences")
    confirmed_trait("Uses metric units", when="on numbers")
    fake_ollama.reply("`/amend`").reply("/amend handle negatives")
    Talk("cmds").reply(Recorder(), Message("1", "also handle negatives"), FakeChatState())
    fill = fake_ollama.calls[-1]
    fake_ollama.reply("none").reply("low: a plain request").reply("hi")
    Talk("cmds").reply(Recorder(), Message("1", "tell me about the bees"), FakeChatState())
    for call in (fill, fake_ollama.calls[-1]):
        assert f"## {TRAITS_PART}\n- Answers in short sentences" in system_of(call)


@pytest.mark.spec("REM-16")
def test_a_chat_reply_without_confirmed_traits_has_no_traits_part(measured_model, fake_ollama):
    from tinkerkit.memory.memories import Traits

    Traits().apply([{"op": "add", "about": "person", "when": "", "do": "Likes tea", "sources": ["e1"]}])
    fake_ollama.reply("`/amend`").reply("/amend handle negatives")
    Talk("cmds").reply(Recorder(), Message("1", "also handle negatives"), FakeChatState())
    assert all(TRAITS_PART not in system_of(call) for call in fake_ollama.calls)


@pytest.mark.spec("REM-16")
def test_the_command_tell_gets_the_persons_traits(measured_model, fake_ollama):
    fake_ollama.reply("told")
    Talk("cmds").tell(Recorder(), "1", "how much ram", "/system ram", "", "8 GB free", "low")
    assert TRAITS_PART not in system_of(fake_ollama.calls[-1])
    confirmed_trait("Answers in short sentences")
    fake_ollama.reply("told")
    Talk("cmds").tell(Recorder(), "1", "how much ram", "/system ram", "", "8 GB free", "low")
    assert system_of(fake_ollama.calls[-1]).endswith(f"## {TRAITS_PART}\n- Answers in short sentences")


@pytest.mark.spec("CNV-18")
def test_talk_without_a_model_says_so():
    assert Talk("cmds").reply(Recorder(), Message("1", "hi")).startswith("⚠️ **No model is in use yet**")
    assert (
        Talk.busy_note([{"brief": "**calc**"}])
        == "⏸ **Pausing** **calc** to answer you.\nIt goes on once you have your answer."
    )
    assert "Getting ready to answer" in Talk.busy_note([])


def chatted(desk, channel, monkeypatch, asked):
    replies = []
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: QuickModel("m", {}, None)))

    def quick_reply(messages, *rest, **options):
        asked.append(messages)
        return replies.pop(0)

    monkeypatch.setattr(Models, "quick_reply", staticmethod(quick_reply))

    def chat(reply, shown, *more, ahead=("none", "low: a plain request")):
        replies.extend([*ahead, reply, *more])
        say(desk, channel, "do it for me")
        wait_until(lambda: shown("".join(channel.texts()[-1:])))

    return chat


@pytest.mark.spec("CNV-18")
def test_a_chat_reply_that_is_a_command_line_is_run_at_once(desk, monkeypatch):
    desk, channel, _ = desk
    chat = chatted(desk, channel, monkeypatch, [])
    chat("/clear chat", lambda last: last.startswith("🧹 **Chat cleared:**"))
    chat("`/status`", lambda last: last == "Tinker is up.", "/status", "Tinker is up.", ahead=())
    handled = []
    monkeypatch.setattr(desk, "answer", lambda message, channel: handled.append(message.text))
    chat("/fix the login bug", lambda last: handled == ["/fix the login bug"])
    chat("/clear chat\n/fix the login bug", lambda last: handled[1:] == ["/clear chat", "/fix the login bug"])
    chat("status is fine", lambda last: "status is fine" in last)
    assert all("`/status`" not in text and "/confirm" not in text for text in channel.texts())
    handled.clear()
    chat("`/system time`\n`/system date`", lambda last: len(handled) == 2)
    chat("/system time and /system date", lambda last: len(handled) == 3)
    chat("The time is shown by `/system time` for you.", lambda last: len(handled) == 4)
    assert handled == ["/system time", "/system date", "/system time and /system date", "/system time"]


@pytest.mark.spec("REM-1")
def test_each_chat_request_writes_one_episode(desk, monkeypatch):
    from tinkerkit.memory.memories import Scratchpad

    desk, channel, _ = desk
    chat = chatted(desk, channel, monkeypatch, [])
    monkeypatch.setattr(desk, "answer", lambda message, channel: None)
    chat("status is fine", lambda last: "status is fine" in last)
    wait_until(lambda: len(Scratchpad.fresh()) == 1)
    chat("/clear chat\n/fix the login bug", lambda last: len(Scratchpad.fresh()) == 2)
    answered, commanded = sorted(Scratchpad.fresh(), key=lambda episode: int(episode["id"][1:]))
    assert answered["message"] == "do it for me" and answered["reply"] == "status is fine"
    assert answered["status"] == "answered" and answered["chat"] == "1" and "commands" not in answered
    assert (
        commanded["commands"] == ["/clear chat", "/fix the login bug"] and commanded["status"] == "answered"
    )


def budgeted(monkeypatch, replies):
    calls = []
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: QuickModel("m", {}, None)))

    def quick_reply(messages, tokens, think=None, temperature=None):
        calls.append((tokens, think))
        return replies.pop(0)

    monkeypatch.setattr(Models, "quick_reply", staticmethod(quick_reply))
    return calls


@pytest.mark.spec("CNV-18")
def test_a_thinking_chat_answer_gets_a_thinking_budget_and_an_empty_cut_is_asked_again_without(
    desk, monkeypatch
):
    desk, channel, _ = desk
    calls = budgeted(monkeypatch, ["none", "high: a hard request", Cut(""), "the answer"])
    say(desk, channel, "do it for me")
    wait_until(lambda: "the answer" in "".join(channel.texts()[-1:]))
    assert calls[-2:] == [
        (desk_module.CHAT_MAX_REPLY_TOKENS + desk_module.CHAT_THINK_TOKENS, "high"),
        (desk_module.CHAT_MAX_REPLY_TOKENS, "off"),
    ]


@pytest.mark.spec("CNV-18")
def test_a_chat_answer_without_thinking_gets_only_the_reply_budget(desk, monkeypatch):
    desk, channel, _ = desk
    calls = budgeted(monkeypatch, ["none", "off: a plain request", Cut("")])
    say(desk, channel, "do it for me")
    wait_until(lambda: "_(no answer)_" in "".join(channel.texts()[-1:]))
    assert calls[-1] == (desk_module.CHAT_MAX_REPLY_TOKENS, "off")


@pytest.mark.spec("CNV-18", "REM-1")
def test_a_chat_answer_cut_off_at_length_is_shown_but_kept_as_no_episode(desk, monkeypatch):
    desk, channel, _ = desk
    budgeted(monkeypatch, ["none", "low: a plain request", Cut("half an ans")])
    say(desk, channel, "do it for me")
    wait_until(lambda: "half an ans" in "".join(channel.texts()[-1:]))
    assert Scratchpad.fresh() == []


@pytest.mark.spec("CNV-24", "REM-12")
def test_fresh_asks_the_model_again_what_the_chat_last_asked(desk, monkeypatch):
    desk, channel, _ = desk
    asked = []
    chat = chatted(desk, channel, monkeypatch, asked)
    chat("first answer", lambda last: "first answer" in last)
    wait_until(lambda: len(Scratchpad.fresh()) == 1)
    remembered_answer("do it for me", "Done from memory.")
    replies = ["none", "low: a plain request", "fresh answer"]
    monkeypatch.setattr(
        Models, "quick_reply", staticmethod(lambda messages, *rest, **options: replies.pop(0))
    )
    say(desk, channel, "/fresh")
    wait_until(lambda: "fresh answer" in "".join(channel.texts()[-1:]))
    assert all("Done from memory." not in text for text in channel.texts())
    wait_until(lambda: len(Scratchpad.fresh()) == 2)
    episodes = sorted(Scratchpad.fresh(), key=lambda episode: int(episode["id"][1:]))
    assert [episode["message"] for episode in episodes] == ["do it for me", "do it for me"]
    assert episodes[-1]["reply"] == "fresh answer"


@pytest.mark.spec("CNV-24")
def test_only_a_reply_from_memory_offers_a_fresh_answer(desk, monkeypatch):
    desk, channel, _ = desk
    chat = chatted(desk, channel, monkeypatch, [])
    chat("first answer", lambda last: "first answer" in last)
    assert [entry for entry in channel.log if entry[0] == "fresh"] == []
    remembered_answer("do it for me", "Done from memory.")
    say(desk, channel, "do it for me")
    wait_until(lambda: any(entry[0] == "fresh" for entry in channel.log))
    [(_, chat_id, shown, _)] = [entry for entry in channel.log if entry[0] == "fresh"]
    assert chat_id == "1" and shown == channel.newest["1"]
    assert "Done from memory." in channel.texts()[-1]


@pytest.mark.spec("CNV-24")
def test_fresh_with_no_question_yet_says_so(desk, monkeypatch):
    desk, channel, _ = desk
    chatted(desk, channel, monkeypatch, [])
    say(desk, channel, "/fresh")
    assert channel.texts()[-1] == "❓ **No question yet** to ask again."


@pytest.mark.spec("CNV-12", "CNV-18")
def test_a_command_asked_in_words_is_answered_in_words(desk, monkeypatch):
    desk, channel, _ = desk
    asked = []
    chat = chatted(desk, channel, monkeypatch, asked)
    monkeypatch.setattr(desk, "answer", lambda message, channel: f"raw {message.text}")
    chat("/system time", lambda last: last == "It is **10:00**.", "It is **10:00**.")
    assert "raw /system time" in asked[-1][-1]["content"] and "do it for me" in asked[-1][-1]["content"]
    assert all("raw /system time" not in text for text in channel.texts())
    chat("/rename jarvis", lambda last: last == "✅ raw /rename jarvis")
    chat("/run tests", lambda last: last == "✅ raw /run tests")
    monkeypatch.setattr(desk, "answer", lambda message, channel: "fix started")
    chat("/fix the login bug", lambda last: last == "fix started")


@pytest.mark.spec("RVW-3")
def test_a_chat_reply_asks_at_its_messages_level(measured_model, fake_ollama, monkeypatch):
    monkeypatch.setattr(ThinkLevels, "values", staticmethod(lambda name: ["low", "medium", "high"]))
    fake_ollama.reply("none").reply("Look at the loop.")
    text = Talk("cmds").reply(Recorder(), Message("1", "why does this function fail with a bug"))
    assert text == "Look at the loop." and [call["think"] for call in fake_ollama.calls] == [False, "high"]


@pytest.mark.spec("RVW-1")
def test_a_chat_reply_review_gets_the_thread_before_the_message(measured_model, fake_ollama):
    Scratchpad.write(chatted_episode(0))
    fake_ollama.reply("none").reply("low: a plain request").reply("answer 1")
    Talk("cmds").reply(Recorder(), Message("1", "question 1"))
    reviewed = fake_ollama.calls[1]["messages"][1]["content"]
    assert reviewed.index("question 0 → Answer 0. More.") < reviewed.index("question 1")
    assert reviewed.endswith("\n\nquestion 1")


@pytest.mark.spec("REM-13")
def test_a_chat_reply_is_not_told_the_threads_earlier_answer(measured_model, fake_ollama):
    Scratchpad.write(chatted_episode(0))
    Scratchpad.write(chatted_episode(1))
    fake_ollama.reply("none").reply("low: a plain request").reply("answer 1")
    Talk("cmds").reply(Recorder(), Message("1", "question 1"))
    reviewed = fake_ollama.calls[1]["messages"][1]["content"]
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert "question 0 → Answer 0. More." in reviewed and "Answer 0. More." in system
    assert "Answer 1" not in reviewed and "Answer 1" not in system


@pytest.mark.spec("REM-13", "CNV-24")
def test_a_fresh_reply_is_not_told_a_remembered_answer(measured_model, fake_ollama):
    remembered_answer("how tall is everest in km", "About 8.85 km.")
    remembered_answer("how tall is everest in feet", "About 29,032 feet.")
    fake_ollama.reply("none").reply("low: a plain request").reply("8.8 km")
    Talk("cmds").asked_reply(Recorder(), Message("1", "How tall is Everest in km?"))
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert "how tall is everest in feet" in system
    assert "how tall is everest in km" not in system


@pytest.mark.spec("REM-13")
def test_a_chat_reply_is_not_told_a_recalled_episodes_answer(measured_model, fake_ollama):
    Scratchpad.write(chatted_episode("about bees", chat="2"))
    Scratchpad.write(chatted_episode("about bees and honey", chat="2"))
    fake_ollama.reply("none").reply("low: a plain request").reply("bees")
    Talk("cmds").asked_reply(Recorder(), Message("1", "question about bees"))
    system = fake_ollama.calls[-1]["messages"][0]["content"]
    assert "Answer about bees and honey. More." in system
    assert "Answer about bees. More." not in system


FAKE_TINKER = """
import json, os, signal, sys, time
sys.path.insert(0, os.environ["TINKER_SOURCE"])
from tinkerkit.store import Store
store = Store(os.environ["FAKE_TINKER_DB"])
args = sys.argv[1:]
folder = sys.argv[0].rsplit("/", 1)[0] + "/calc"
with open(sys.argv[0] + ".calls", "a") as calls:
    calls.write(" ".join(args) + "\\n")
if args[:1] == ["help"]:
    print("- `task WHAT`\\n- `status`")
    sys.exit(0)
state = {"title": "calc: a calculator", "run": "abc123", "folder": folder, "step": "spec", "doing": "📝 Writing the spec",
         "began": time.time(), "steps": [], "passing": None, "live": "", "latest": "", "waiting": None, "ended": None,
         "before": 0.0, "seconds": 1.0}
job = {"number": os.getpid(), "command": "task", "title": "task", "state": "running", "pid": os.getpid()}
if os.environ.get("FAKE_TINKER_FOLLOWS"):
    job.update(pid=os.getpid() + 100000, follower=os.getpid())
store.put("jobs", str(os.getpid()), dict(job, progress=state))
print("Plan: the spec (project)", flush=True)
if args[:1] == ["slow"]:
    signal.signal(signal.SIGINT, lambda *a: (print("Interrupted.", flush=True), sys.exit(1)))
    time.sleep(30)
if args[:1] == ["task"]:
    state["waiting"] = {"name": "calc", "run": "abc123", "language": "Python", "start": "python3 calc.py",
                        "kind": "project", "criteria": [["C1", "calc.add(2, 3) is 5"]], "folder": folder}
elif args[:1] == ["research"]:
    state["ended"] = {"status": "done", "headline": "Researched it.", "details": [], "next": [],
                      "text": "**Python 3.14** came out in October [1]."}
    if os.environ.get("FAKE_TINKER_FOLLOWS"):
        picture = sys.argv[0].rsplit("/", 1)[0] + "/chart.png"
        open(picture, "wb").write(b"\\x89PNG\\r\\n\\x1a\\nrest")
        state["ended"]["files"] = [picture]
else:
    state["ended"] = {"status": "done", "headline": "Done. All 1 spec tests pass (SPEC.md).", "details": [], "next": []}
store.put("jobs", str(os.getpid()), dict(job, progress=state))
"""


@pytest.fixture
def desk(tmp_path, monkeypatch, home_store):
    script = tmp_path / "fake_tinker.py"
    script.write_text(textwrap.dedent(FAKE_TINKER))
    monkeypatch.setenv("TINKER_SOURCE", str(Path(__file__).resolve().parent.parent))
    monkeypatch.setenv("FAKE_TINKER_DB", str(home_store.path))
    tinker_runs(monkeypatch, script)
    monkeypatch.setattr(reports, "PROGRESS_EDIT_SECONDS", 0.2)
    channel = Recorder(chats=("1",))
    desk = Desk([channel], tmp_path, shelf=Shelf())
    desk.ran = []
    ran = lambda words, **options: desk.ran.append(words) or f"output of {' '.join(words)}"
    monkeypatch.setattr(desk, "tinker", ran)
    monkeypatch.setattr(desk, "printed", ran)
    yield desk, channel, script
    desk.stop_task()
    if desk.last_task:
        desk.last_task.wait(10)
    if desk.reporter:
        desk.reporter.ended = True


class EndingTask:
    pid = 4242

    def poll(self):
        return None


@pytest.mark.spec("JOB-7")
@pytest.mark.parametrize("ended", [PermissionError, ProcessLookupError])
def test_stopping_a_task_that_just_ended_stops_nothing(tmp_path, monkeypatch, ended):
    desk = Desk([ConsoleChannel()], tmp_path)
    desk.last_task = EndingTask()

    def group_gone(pid, sig):
        raise ended

    monkeypatch.setattr(os, "killpg", group_gone)
    assert desk.stop_task() is False


def say(desk, channel, text):
    desk.hear(channel, Message("1", text, "you", id=len(channel.log) + 1000))


def calls_of(script):
    path = Path(str(script) + ".calls")
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.spec("CNV-9")
def test_desk_answers_commands(desk):
    desk, channel, _ = desk
    assert desk.talk.commands == "- `task WHAT`\n- `status`"
    replies = {
        text: desk.answer(Message("1", text), channel)
        for text in (
            "task",
            "status",
            "stop",
            "stop #9",
            "resume",
            "resume #9",
            "resume abc123",
            "channel add",
            "channel remove bot",
            "channel start",
            "model",
            "/Runs",
        )
    }
    assert replies["task"].startswith("✍️ Write `/task WHAT TO BUILD`")
    assert (
        replies["status"] == "🟢 **Tinker** · idle: send a task"
        and replies["stop"] == "💤 No job is running."
    )
    assert replies["stop #9"] == "🤷 Job #9 is not in the queue (`/queue list` shows it)."
    assert replies["resume"] == "🤷 No job is stopped (jobs lists them)."
    assert replies["resume #9"] == "🤷 No job #9 (jobs lists them)."
    assert replies["resume abc123"].startswith("✍️ Write `/resume [#JOB | @PROJECT]`")
    assert replies["channel add"].startswith(
        "🖥 Channels are set up from the terminal: `tinker channel add` there"
    )
    assert [replies[text] for text in ("channel remove bot", "channel start")] == [
        f"🖥 `channel {action}` asks in a terminal: `tinker channel {action}` there"
        for action in ("remove", "start")
    ]
    assert replies["model"] == "⚙️ **model**\n```\noutput of model\n```"
    assert replies["/Runs"] == "📚 **Runs**\n```\noutput of runs\n```"


@pytest.mark.spec("CNV-9")
def test_desk_answers_a_command_while_its_channel_says_it_waits(desk):
    desk, channel, _ = desk
    waited = []

    @contextlib.contextmanager
    def waiting(chat, text):
        waited.append((chat, text))
        yield
        waited.append("done")

    channel.waiting = waiting
    desk.answer(Message("1", "/knowledge graph"), channel)
    assert waited == [("1", "Running /knowledge graph"), "done"]


@pytest.mark.spec("CNV-9")
def test_a_command_that_deletes_and_exit_ask_first(desk):
    desk, channel, _ = desk
    assert desk.answer(Message("1", "/clear memory"), channel) is None
    assert desk.ran == [["clear", "memory"]] and "👉 `/confirm` to delete it" in channel.texts()[-1]
    assert desk.answer(Message("1", "/exit"), channel) is None and "**Stop Tinker?**" in channel.texts()[-1]
    say(desk, channel, "/confirm")
    wait_until(lambda: channel.closed)


@pytest.mark.spec("CNV-9")
def test_a_deletion_whose_preview_fails_answers_with_the_error_and_waits_for_nothing(desk, monkeypatch):
    desk, channel, _ = desk
    failed = subprocess.CompletedProcess([], 1, "", "No reading #15\n")
    monkeypatch.setattr(desk, "run_tinker", lambda arguments, timeout, env=None: failed)
    monkeypatch.setattr(desk, "printed", Desk.printed.__get__(desk))
    answer = desk.answer(Message("1", "/read forget #15"), channel)
    assert "No reading #15" in answer and "/confirm" not in answer
    assert not any("/confirm" in text for text in channel.texts())
    assert desk.questions.waiting(channel, "1") is None


@pytest.mark.spec("CNV-9", "REM-9", "REM-10")
def test_memory_forget_asks_first_and_the_other_memory_views_are_glances(desk, monkeypatch):
    desk, channel, _ = desk
    assert desk.answer(Message("1", "/memory forget a1b2c3"), channel) is None
    assert (
        desk.ran == [["memory", "forget", "a1b2c3"]] and "👉 `/confirm` to delete it" in channel.texts()[-1]
    )
    monkeypatch.setattr(desk, "tinker", lambda words: desk.ran.append(words) or "🧠 Memories")
    assert (
        desk.answer(Message("1", "/memory traits"), channel) == "⚙️ **memory traits**\n```\n🧠 Memories\n```"
    )
    assert desk.ran[-1] == ["memory", "traits"]
    assert (
        desk.answer(Message("1", "/memory forget t3"), channel)
        == "⚙️ **memory forget t3**\n```\n🧠 Memories\n```"
    )
    assert desk.ran[-1] == ["memory", "forget", "t3"]


@pytest.mark.spec("CNV-9", "DRM-8")
def test_dream_status_is_a_glance_in_a_chat(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk, "tinker", lambda words: desk.ran.append(words) or "💤 Dream")
    assert desk.answer(Message("1", "/dream"), channel) == "⚙️ **dream**\n```\n💤 Dream\n```"
    assert desk.ran[-1] == ["dream"]


@pytest.fixture
def terminal_desk(desk, monkeypatch):
    desk, console, _ = desk
    desk.consoles.append(console)
    desk.listened, desk.checked, desk.renewed = [], [], []
    monkeypatch.setattr(desk, "listen", desk.listened.append)
    monkeypatch.setattr(desk, "renew_banner", lambda: desk.renewed.append(True))
    monkeypatch.setattr(channels.ChannelCommand, "check", desk.checked.append)
    return desk, console


def kept_telegram(monkeypatch, settings):
    telegram = Recorder("Telegram")
    monkeypatch.setattr(channels.Channels, "settings", lambda: {"Telegram": settings})
    monkeypatch.setattr(channels.Channels, "make", lambda name: telegram)
    return telegram


ROUTED_ADD = "/channel add --kind telegram --name jarvis"


def added_jarvis(monkeypatch, console, name):
    kept, jarvis = {"jarvis": {"kind": "telegram", "enabled": False}}, Recorder("jarvis")
    console.added = []
    console.channel_added = lambda words: console.added.append(words) or name
    monkeypatch.setattr(channels.Channels, "settings", lambda: kept)
    monkeypatch.setattr(channels.Channels, "keep", lambda name, settings: kept.update({name: settings}))
    monkeypatch.setattr(channels.Channels, "make", lambda name: jarvis)
    return kept, jarvis


@pytest.mark.spec("CNV-6", "CNV-9")
def test_the_terminal_adds_a_channel_asked_for_in_words_then_enables_and_listens_on_it(
    terminal_desk, monkeypatch
):
    desk, console = terminal_desk
    monkeypatch.setattr(desk_module, "typed_here", lambda: True)
    kept, jarvis = added_jarvis(monkeypatch, console, "jarvis")
    assert desk.answer(Message("1", ROUTED_ADD), console) == "📡 Listening on jarvis."
    wait_until(lambda: desk.listened == [jarvis])
    assert console.added == [["--kind", "telegram", "--name", "jarvis"]] and kept["jarvis"]["enabled"]
    assert desk.ran == [] and desk.channels == [console, jarvis]


@pytest.mark.spec("CNV-6")
def test_the_terminal_starts_nothing_when_a_channel_add_asked_for_in_words_fails(terminal_desk, monkeypatch):
    desk, console = terminal_desk
    monkeypatch.setattr(desk_module, "typed_here", lambda: True)
    kept, _ = added_jarvis(monkeypatch, console, None)
    assert not desk.answer(Message("1", ROUTED_ADD), console)
    assert not kept["jarvis"]["enabled"] and desk.channels == [console] and desk.listened == []


@pytest.mark.spec("CNV-9")
def test_a_chat_or_piped_console_is_told_to_add_a_channel_from_the_terminal(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk_module, "typed_here", lambda: False)
    answer = (
        "🖥 Channels are set up from the terminal: `tinker channel add` there "
        "(its token would land in the chat's history, and pairing needs the terminal)"
    )
    assert desk.answer(Message("1", ROUTED_ADD), channel) == answer
    desk.consoles.append(channel)
    assert desk.answer(Message("1", ROUTED_ADD), channel) == answer
    assert desk.ran == []


@pytest.mark.spec("CNV-9")
def test_the_terminal_removes_a_channel_once_confirmed(terminal_desk):
    desk, console = terminal_desk
    assert desk.answer(Message("1", "/channel remove bot"), console) is None
    assert desk.ran == [["channel", "remove", "bot"]] and "👉 `/confirm` to delete it" in console.texts()[-1]


@pytest.mark.spec("CNV-9")
def test_the_terminal_starts_listening_on_enabled_channels_not_yet_listened_to(terminal_desk, monkeypatch):
    desk, console = terminal_desk
    telegram, again = Recorder("Telegram"), Recorder("rec")
    monkeypatch.setattr(channels.Channels, "enabled", lambda: [telegram, again])
    assert desk.answer(Message("1", "/channel start"), console) == "📡 Listening on Telegram."
    wait_until(lambda: desk.listened == [telegram])
    assert desk.checked == [telegram] and desk.channels == [console, telegram] and desk.renewed == [True]


@pytest.mark.spec("CNV-18")
def test_the_chat_state_names_the_model_and_each_channel_with_whether_it_is_listened_to(desk, monkeypatch):
    desk, _, _ = desk
    desk.channels.append(Recorder("jarvis"))
    settings = {
        "jarvis": {"kind": "telegram", "enabled": True},
        "bot": {"kind": "telegram", "enabled": False},
    }
    monkeypatch.setattr(channels.Channels, "settings", lambda: settings)
    Config.save(model="n")
    state = ChatState(desk)
    assert state.model() == "Model in use: n"
    assert (
        "jarvis (telegram, enabled): listening" in state.channels()
        and "bot (telegram, disabled): not listening" in state.channels()
    )
    monkeypatch.setattr(channels.Channels, "settings", lambda: {})
    Config.save(model="")
    assert state.model() == "Model in use: none picked yet" and state.channels() == "Channels: none set up"


def quiet_chat_state(desk, monkeypatch, running=(), plan="", entries=()):
    monkeypatch.setattr(desk, "running_now", lambda: list(running))
    monkeypatch.setattr(desk, "waiting_plan", types.SimpleNamespace(shown=lambda: plan))
    monkeypatch.setattr(Runs, "entries", staticmethod(lambda: list(entries)))
    monkeypatch.setattr(channels.Channels, "settings", lambda: {})
    Config.save(model="n")
    return ChatState(desk)


@pytest.mark.spec("CNV-18")
def test_the_everyday_chat_state_holds_what_runs_and_the_plan_that_waits(desk, monkeypatch):
    desk, _, _ = desk
    state = quiet_chat_state(desk, monkeypatch)
    assert state.running() == state.waiting() == state.projects() == state.everyday() == ""
    state = quiet_chat_state(
        desk, monkeypatch, running=[{"text": "job 1"}, {"text": "job 2"}], plan="the plan"
    )
    assert state.everyday() == (
        "Running now:\njob 1\n\njob 2\n\n"
        "A spec waits for the person (confirm builds it, amend WHAT changes it):\nthe plan"
    )


@pytest.mark.spec("CNV-18")
def test_the_chat_state_for_commands_holds_only_the_pieces_they_use(desk, monkeypatch):
    desk, _, _ = desk
    entry = {"id": "3", "workdir": "/tmp/app", "task": "an app"}
    state = quiet_chat_state(desk, monkeypatch, running=[{"text": "job 1"}], plan="the plan", entries=[entry])
    assert state.for_commands(["model"]) == "Model in use: n"
    assert state.for_commands(["confirm"]) == state.waiting() + "\n\n" + state.projects()
    assert state.for_commands(["queue"]) == state.running() + "\n\n" + state.waiting()
    assert state.for_commands(["jobs", "model", "stop"]) == "\n\n".join(
        ["Model in use: n", state.running(), state.waiting()]
    )
    assert state.projects() == "Known projects:\n@3 /tmp/app: an app"
    assert state.for_commands(["status"]) == state.projects()
    assert state.for_commands(["help"]) == ""


@pytest.mark.spec("CNV-9")
def test_the_terminal_starts_listening_on_the_channel_named(terminal_desk, monkeypatch):
    desk, console = terminal_desk
    kept_telegram(monkeypatch, {})
    assert desk.answer(Message("1", "/channel start Telegram"), console) == "📡 Listening on Telegram."
    assert desk.answer(Message("1", "/channel start Slack"), console) == (
        "⚠️ tinker: no channel 'Slack' (tinker channel list shows them)"
    )


@pytest.mark.spec("CNV-9")
def test_the_terminal_says_when_no_channel_is_new_to_listen_on(terminal_desk, monkeypatch):
    desk, console = terminal_desk
    monkeypatch.setattr(channels.Channels, "enabled", lambda: [Recorder("rec")])
    assert desk.answer(Message("1", "/channel start"), console) == (
        "🤷 No enabled channel is new to listen on (tinker channel enable NAME, or start NAME)."
    )
    assert desk.renewed == [] and desk.checked == []


@pytest.mark.spec("CNV-9")
def test_the_terminal_skips_a_channel_that_fails_its_check_or_is_held(terminal_desk, monkeypatch):
    desk, console = terminal_desk
    broken, held = Recorder("broken"), Recorder("held")
    held.claim, held.holder = lambda holder: False, lambda: {"pid": 42}
    monkeypatch.setattr(channels.Channels, "enabled", lambda: [broken])

    def failing(channel):
        raise SystemExit(f"tinker: channel {channel.name} does not work: no")

    monkeypatch.setattr(channels.ChannelCommand, "check", failing)
    assert (
        desk.answer(Message("1", "/channel start"), console) == "⚠️ tinker: channel broken does not work: no"
    )
    monkeypatch.setattr(channels.ChannelCommand, "check", lambda channel: None)
    monkeypatch.setattr(channels.Channels, "enabled", lambda: [held])
    assert desk.answer(Message("1", "/channel start"), console) == (
        "⚠️ held is listened to by another Tinker (process 42): stop it first"
    )
    assert desk.channels == [console] and desk.renewed == []


@pytest.mark.spec("CNV-9")
def test_the_terminal_listens_on_a_channel_once_it_is_enabled(terminal_desk, monkeypatch):
    desk, console = terminal_desk
    telegram = kept_telegram(monkeypatch, {"enabled": True})
    assert desk.answer(Message("1", "/channel enable Telegram"), console) == (
        "⚙️ **channel enable Telegram**\n```\noutput of channel enable Telegram\n```\n📡 Listening on Telegram."
    )
    wait_until(lambda: desk.listened == [telegram])
    assert desk.ran == [["channel", "enable", "Telegram"]] and desk.channels == [console, telegram]


@pytest.mark.spec("CNV-9")
def test_the_terminal_adds_nothing_when_an_enable_fails_or_the_channel_is_listened_to(
    terminal_desk, monkeypatch
):
    desk, console = terminal_desk
    shown = "⚙️ **channel enable {0}**\n```\noutput of channel enable {0}\n```"
    monkeypatch.setattr(
        channels.Channels, "settings", lambda: {"Telegram": {"enabled": False}, "rec": {"enabled": True}}
    )
    assert desk.answer(Message("1", "/channel enable Telegram"), console) == shown.format("Telegram")
    assert desk.answer(Message("1", "/channel enable rec"), console) == shown.format("rec")
    assert desk.channels == [console] and desk.renewed == [] and desk.checked == []


@pytest.mark.spec("CNV-9")
def test_a_chat_does_not_listen_on_a_channel_it_enables(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(channels.Channels, "settings", lambda: {"Telegram": {"enabled": True}})
    assert desk.answer(Message("1", "/channel enable Telegram"), channel).endswith("```")
    assert desk.channels == [channel]


@pytest.mark.spec("CNV-9")
def test_a_deletion_preview_is_what_tinker_printed_without_its_errors(tmp_path, monkeypatch, home_store):
    script = tmp_path / "deletes.py"
    script.write_text(
        "import sys\nprint('Delete these runs:')\nprint('  • abc123')\n"
        "sys.exit('Nothing deleted: run tinker clear runs in a terminal to confirm, or add --yes.')\n"
    )
    tinker_runs(monkeypatch, script)
    channel = Recorder(chats=("1",))
    desk = Desk([channel], tmp_path)
    desk.answer(Message("1", "/clear runs"), channel)
    shown = channel.texts()[-1]
    assert "Delete these runs:" in shown and "abc123" in shown
    assert "Nothing deleted" not in shown and "👉 `/confirm` to delete it" in shown


@pytest.mark.spec("CNV-9")
def test_a_task_runs_as_its_own_tinker_and_its_plan_waits(desk, home_store):
    desk, channel, script = desk
    say(desk, channel, "/task a calculator")
    wait_until(lambda: bool(desk.waiting_plan))
    assert calls_of(script) == ["help", "task a calculator --confirm"]
    assert (
        desk.waiting_plan.run == "abc123"
        and not isinstance(desk.waiting_plan, WaitingName)
        and desk.waiting_plan.new
    )
    assert desk.waiting_plan.plan.startswith("📋 **Plan for calc** · run `abc123`")
    assert desk.status().startswith("🙋 **Tinker** · calc: a calculator waits for you")


@pytest.mark.spec("CNV-16")
def test_research_in_a_chat_is_the_research_job_ending_with_its_answer(desk):
    desk, channel, script = desk
    say(desk, channel, "/research the newest Python")
    wait_until(lambda: not desk.busy())
    assert calls_of(script) == ["help", "research the newest Python"]
    wait_until(lambda: "came out in October" in channel.texts()[-1])
    shown = unstamped(channel.texts())[-1]
    assert "✅ **Researched it.**" in shown and shown.endswith("**Python 3.14** came out in October [1].")


@pytest.mark.spec("JOB-10", "SKL-7")
def test_a_chat_job_another_tinker_runs_ends_in_the_chat_with_its_files(desk, monkeypatch):
    monkeypatch.setenv("FAKE_TINKER_FOLLOWS", "1")
    desk, channel, script = desk
    say(desk, channel, "/research the newest Python")
    picture = str(Path(script).parent / "chart.png")
    wait_until(lambda: ("photo", "1", picture, "chart.png") in channel.log)
    assert any("✅ **Researched it.**" in text for text in channel.texts())


@pytest.mark.spec("CNV-16")
def test_a_job_shows_its_progress_only_in_the_chat_that_asked(desk):
    desk, channel, script = desk
    channel.allowed = ["1", "2"]
    say(desk, channel, "/research the newest Python")
    wait_until(lambda: not desk.busy())
    wait_until(lambda: any("came out in October" in text for text in channel.texts(chat="1")))
    assert not any("research" in text or "October" in text for text in channel.texts(chat="2"))


@pytest.mark.spec("CNV-10")
def test_confirm_asks_a_new_projects_folder_once_then_builds(desk):
    desk, channel, script = desk
    say(desk, channel, "/task a calculator")
    wait_until(lambda: bool(desk.waiting_plan))
    say(desk, channel, "/confirm")
    assert isinstance(desk.waiting_plan, WaitingName) and any(
        "📁 **Its folder:** `calc`" in text for text in channel.texts()[-3:]
    )
    say(desk, channel, "/confirm")
    wait_until(lambda: "resume @abc123" in calls_of(script))
    wait_until(lambda: not desk.busy())
    assert not desk.waiting_plan


@pytest.mark.spec("CNV-10")
def test_the_plan_that_waits_and_its_build_show_only_in_the_chat_that_confirmed(desk):
    desk, channel, script = desk
    channel.allowed = ["1", "2"]
    say(desk, channel, "/task a calculator")
    wait_until(lambda: bool(desk.waiting_plan))
    say(desk, channel, "/confirm")
    say(desk, channel, "/confirm")
    wait_until(lambda: "resume @abc123" in calls_of(script))
    wait_until(lambda: not desk.busy())
    assert any("📁 **Its folder:**" in text for text in channel.texts(chat="1"))
    assert not any("📁 **Its folder:**" in text or "Confirmed" in text for text in channel.texts(chat="2"))


@pytest.mark.spec("CNV-9")
def test_a_confirmed_plan_is_kept_without_its_next_step_lines(desk):
    desk, channel, script = desk
    say(desk, channel, "/task a calculator")
    wait_until(lambda: bool(desk.waiting_plan))
    say(desk, channel, "/confirm")
    say(desk, channel, "/rename sums")
    wait_until(lambda: "resume @abc123" in calls_of(script))
    kept = desk.reporter.plan_on_top.splitlines()
    assert kept[0].startswith("📋 **Plan for calc** · run `abc123`") and kept[-1] == "📁 `sums`"
    assert not [line for line in kept if line.startswith(("👉", "⏱"))]


def a_waiting_plan(tmp_path, new=True):
    return WaitingPlan("abc123", tmp_path / "calc", "calc: history", "📋 plan\n📁 `calc`", "1 second", new)


@pytest.mark.spec("CNV-10")
def test_a_waiting_plan_owns_its_run_and_its_folder_name(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.owns("abc123") and plan.owns("calc") and not plan.owns("other")
    assert not NoWaitingPlan().owns("abc123") and not NoWaitingPlan()


@pytest.mark.spec("CNV-10")
def test_only_a_new_projects_plain_confirm_asks_its_folder_name(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.asks_name("confirm", "")
    assert not plan.asks_name("confirm", "calc") and not plan.asks_name("amend", "")
    assert not a_waiting_plan(tmp_path, new=False).asks_name("confirm", "")
    assert not plan.named().asks_name("confirm", "") and not NoWaitingPlan().asks_name("confirm", "")


@pytest.mark.spec("CNV-10")
def test_a_named_plan_keeps_the_plans_fields(tmp_path):
    plan = a_waiting_plan(tmp_path)
    named = plan.named()
    assert isinstance(named, WaitingName) and vars(named) == vars(plan)


@pytest.mark.spec("CNV-10")
def test_a_plan_is_shown_with_the_time_so_far(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.shown() == Progress.waits(plan.plan, plan.so_far)
    assert NoWaitingPlan().shown() == ""


@pytest.mark.spec("CNV-10")
def test_a_named_plan_also_asks_for_its_folder(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.asked() == plan.shown()
    assert plan.named().asked() == (
        f"{plan.shown()}\n\n📁 **Its folder:** `calc`\n👉 `/confirm` to build it "
        f"there · `/rename NAME` to name it otherwise (it builds then)"
    )


@pytest.mark.spec("CNV-10")
def test_the_next_steps_follow_what_the_plan_asks(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.next_steps() == "`/confirm` · `/amend WHAT`"
    assert plan.named().next_steps() == "`/confirm` · `/rename NAME`"


@pytest.mark.spec("CNV-10")
def test_a_renamed_plan_moves_its_folder_title_and_plan(tmp_path):
    plan = a_waiting_plan(tmp_path)
    plan.renamed("sums")
    assert plan.folder == tmp_path / "sums" and plan.title == "sums: history"
    assert plan.plan == "📋 plan\n📁 `sums`"


@pytest.mark.spec("CNV-10")
def test_a_folder_name_runs_the_plans_run(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.run_for("calc") == "abc123" and plan.run_for("other") == "other"
    assert plan.run_for("") is None
    assert NoWaitingPlan().run_for("calc") == "calc" and NoWaitingPlan().run_for("") is None


@pytest.mark.spec("CNV-10")
def test_a_plan_moves_on_when_its_folder_starts_a_task(tmp_path):
    plan = a_waiting_plan(tmp_path)
    assert plan.moves_on(tmp_path / "calc") and not plan.moves_on(tmp_path)
    assert not NoWaitingPlan().moves_on(tmp_path)


@pytest.mark.spec("CNV-10")
def test_a_plan_resumes_its_run(tmp_path):
    assert a_waiting_plan(tmp_path).resumed() == ["resume", "@abc123"]


class RenameDesk:
    def __init__(self):
        self.calls = []

    def renamed_in_place(self, audience, waiting, name):
        self.calls.append(("in place", waiting, name))

    def build_renamed(self, audience, waiting, name):
        self.calls.append(("build", waiting, name))
        return "building"


@pytest.mark.spec("CNV-10")
def test_a_renamed_spec_plan_stays_waiting(tmp_path):
    plan, desk = a_waiting_plan(tmp_path), RenameDesk()
    assert plan.after_rename(desk, [], "sums") is None
    assert desk.calls == [("in place", plan, "sums")]


@pytest.mark.spec("CNV-10")
def test_a_renamed_named_plan_builds(tmp_path):
    plan, desk = a_waiting_plan(tmp_path).named(), RenameDesk()
    assert plan.after_rename(desk, [], "sums") == "building"
    assert desk.calls == [("build", plan, "sums")]


@pytest.mark.spec("CNV-10")
def test_confirm_of_a_feature_builds_at_once_and_amend_changes_it(desk):
    desk, channel, script = desk
    desk.waiting_plan = a_waiting_plan(desk.root, new=False)
    say(desk, channel, "/amend also keep the date")
    wait_until(lambda: "amend @abc123 also keep the date" in calls_of(script))
    wait_until(lambda: not desk.busy())
    desk.waiting_plan = a_waiting_plan(desk.root, new=False)
    say(desk, channel, "/confirm @calc")
    wait_until(lambda: "resume @abc123" in calls_of(script))


@pytest.mark.spec("CNV-10")
def test_what_waits_for_confirm_is_cancelled_by_anything_else(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kwargs: None)
    desk.answer(Message("1", "/clear memory"), channel)
    say(desk, channel, "no thanks")
    assert "🚫 _Not done:_ `clear memory`" in channel.texts()[-1] and desk.ran == [["clear", "memory"]]
    desk.answer(Message("1", "/clear memory"), channel)
    say(desk, channel, "/confirm")
    wait_until(lambda: ["clear", "memory", "--yes"] in desk.ran)
    assert desk.decide([(channel, "1")], "confirm", ["confirm"]).startswith("🤷 No plan waits")
    assert desk.rename([(channel, "1")], NoWaitingPlan(), ["rename", "x"]).startswith("🤷 No plan waits")


@pytest.mark.spec("CNV-10")
def test_a_question_with_nothing_to_say_on_a_cancel_is_dropped_quietly(desk):
    desk, channel, _ = desk
    desk.questions.ask(channel, "1", Question(wanted="model pick"))
    assert not desk.settle(channel, Message("1", "/status"), "status")
    assert not desk.questions.waiting(channel, "1") and channel.texts() == []
    assert desk.decide([(channel, "1")], "amend", ["amend"]).startswith("✍️ Write `/amend [@PROJECT] CHANGE`")


@pytest.mark.spec("CNV-10")
@pytest.mark.parametrize("then", ["hello", "/nope", "/clear runs", "/status", "/leave", "/fresh", "/start"])
def test_a_cancel_shows_at_once_before_the_message_that_cancels_is_answered(desk, monkeypatch, then):
    desk, _, _ = desk
    console, out = piped_console(monkeypatch)
    answer = lambda channel, message, *args, **kwargs: channel.send(message.chat, "THE ANSWER")
    monkeypatch.setattr(desk.talk, "answer", answer)
    desk.hear(console, Message("console", "/clear memory", "you"))
    asked = len(out.getvalue())
    desk.hear(console, Message("console", then, "you"))
    assert out.getvalue()[asked:].startswith("🚫 Not done: clear memory\n")


@pytest.mark.spec("CNV-10")
def test_a_chat_has_one_question_and_a_new_one_cancels_the_one_before(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kwargs: None)
    desk.answer(Message("1", "/clear memory"), channel)
    say(desk, channel, "/clear runs")
    assert any(text.startswith("🚫 _Not done:_ `clear memory`") for text in channel.texts())
    assert len(desk.questions.by_chat) == 1
    say(desk, channel, "/confirm")
    wait_until(lambda: ["clear", "runs", "--yes"] in desk.ran)
    assert ["clear", "memory", "--yes"] not in desk.ran and desk.questions.by_chat == {}
    desk.shell.name(channel, Message("1", "list files"), desk.running_now)
    say(desk, channel, "/confirm")
    assert channel.texts()[-1].startswith("⏳ The command is still being worked out")
    say(desk, channel, "hello")
    assert desk.questions.waiting(channel, "1").naming


@pytest.mark.spec("CNV-11")
def test_a_command_sent_alone_asks_for_its_words(desk, monkeypatch):
    desk, channel, _ = desk
    answered = []
    monkeypatch.setattr(desk, "answer", lambda message, channel: answered.append(message.text))
    say(desk, channel, "/fix")
    assert answered == [] and channel.texts()[-1].startswith("✍️ **/fix** · fix a bug")
    say(desk, channel, "typing 2/0 crashes")
    say(desk, channel, "/ask")
    starts_with_a_command_word = "help me"
    say(desk, channel, starts_with_a_command_word)
    say(desk, channel, "/read")
    a_file_to_read = "/read /tmp/book.epub"
    say(desk, channel, a_file_to_read)
    say(desk, channel, "/teach")
    a_command_of_its_own = "/status"
    say(desk, channel, a_command_of_its_own)
    say(desk, channel, "/teach")
    say(desk, channel, "status codes in HTTP")
    assert answered == [
        "/fix typing 2/0 crashes",
        "/ask help me",
        "/read /tmp/book.epub",
        "/status",
        "/teach status codes in HTTP",
    ]
    assert desk.questions.by_chat == {}


@pytest.mark.spec("CNV-3")
def test_a_said_phrase_runs_its_command_and_a_deleting_one_asks_first(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kwargs: None)
    say(desk, channel, "Clear the memory, please!")
    assert desk.ran == [["clear", "memory"]] and "👉 `/confirm` to delete it" in channel.texts()[-1]
    say(desk, channel, "confirm")
    wait_until(lambda: ["clear", "memory", "--yes"] in desk.ran)


def test_a_said_phrase_answering_a_waiting_question_is_not_a_command(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk.talk, "answer", lambda *args, **kwargs: None)
    desk.answer(Message("1", "/clear memory"), channel)
    say(desk, channel, "status")
    assert "🚫 _Not done:_ `clear memory`" in channel.texts()[-1] and desk.ran == [["clear", "memory"]]


def test_a_slash_word_that_names_no_command_is_only_answered(desk, monkeypatch):
    desk, channel, _ = desk
    heard = []
    monkeypatch.setattr(desk, "answer", lambda message, channel: heard.append(message.text))
    monkeypatch.setattr(
        desk.talk, "answer", lambda channel, message, *rest, **kwargs: heard.append(message.text)
    )
    say(desk, channel, "/frobnicate the build")
    assert heard == [] and channel.texts()[-1] == "❓ No command /frobnicate: /help lists them"
    say(desk, channel, "status of my build")
    say(desk, channel, "/status")
    assert heard == ["status of my build", "/status"]


@pytest.mark.spec("CNV-11")
def test_a_model_pick_sent_alone_asks_with_buttons(desk, monkeypatch):
    desk, channel, _ = desk
    answered, chosen = [], []
    rows = [("General models", HEADING), "qwen3  in use · solved 2 of 3", ("big  not measured yet", DIMMED)]
    monkeypatch.setattr(desk, "answer", lambda message, channel: answered.append(message.text))
    monkeypatch.setattr(Models, "command_choices", lambda command: rows)
    monkeypatch.setattr(Models, "shown_recipe", lambda: ["🧠⚡ qwen3 is the smartest and the fastest"])
    Config.save(model="qwen3")
    say(desk, channel, "/model calibrate")
    assert answered == ["/model calibrate"]
    monkeypatch.setattr(
        channel, "choose", lambda chat, question, names: chosen.append((question, names)) or 7
    )
    say(desk, channel, "/model")
    assert answered == ["/model calibrate"] and chosen == [
        (
            "🧠 **/model** · choose a model (in use: qwen3)\n🧠⚡ qwen3 is the smartest and the fastest\n"
            "```\nGeneral models\nqwen3  in use · solved 2 of 3\nbig  not measured yet\n```",
            ["qwen3", "big"],
        )
    ]
    say(desk, channel, "big")
    assert answered[-1] == "/model big"
    say(desk, channel, "/model calibrate")
    assert chosen[-1][0].startswith("🧠 **/model calibrate** · choose a model (in use: qwen3)\n")
    say(desk, channel, "qwen3")
    assert answered[-1] == "/model calibrate qwen3"
    monkeypatch.setattr(Models, "command_choices", lambda command: [("General models", HEADING)])
    say(desk, channel, "/model")
    monkeypatch.setattr(
        Models, "command_choices", lambda command: (_ for _ in ()).throw(SystemExit("no Ollama"))
    )
    say(desk, channel, "/model")
    assert answered[-2:] == ["/model", "/model"] and len(chosen) == 2


@pytest.mark.spec("CNV-12")
def test_a_glance_goes_soon_and_history_shows_things_again(desk, monkeypatch):
    desk, channel, _ = desk
    glances = []
    monkeypatch.setattr(
        channel, "glance", lambda chat, asked, sent, keep=None: glances.append((chat, asked, sent, keep))
    )
    monkeypatch.setattr(channel, "clear", lambda: 3)
    desk.hear(channel, Message("1", "/jobs", "you", id=7))
    assert glances == [("1", 7, [101], None)]
    assert desk.answer(Message("1", "/history"), channel) == "📜 **History:** nothing shown in the chats yet"
    Ledger.shown("rec", "1", None, [5], "🔨 **calc**\n▸ build", "task a calculator")
    listed = desk.answer(Message("1", "/history"), channel)
    assert "`#1`" in listed and "🔨 calc" in listed and "_you: task a calculator_" in listed
    again = desk.answer(Message("1", "/history #1"), channel)
    assert again.startswith("📜 **#1**") and "💬 _you:_ task a calculator" in again
    assert again.endswith("🔨 **calc**\n▸ build") and "No #9" in desk.answer(
        Message("1", "/history #9"), channel
    )
    assert desk.answer(Message("1", "/clear chat"), channel) is None
    assert (
        channel.texts()[-1].startswith("🧹 **Chat cleared:** 3 messages taken out")
        and glances[-1][3] == DONE_NOTE_KEPT_SECONDS
    )
    desk.hear(channel, Message("1", "/clear"))
    assert channel.texts()[-1].startswith("🧹 **Chat cleared:** 3 messages taken out")
    assert desk.questions.by_chat == {}
    monkeypatch.setattr(desk, "tinker", lambda words: Home.clear(["history"], yes=True) or "Cleared history.")
    desk.answer(Message("1", "/clear history"), channel)
    shown = len(channel.texts())
    say(desk, channel, "/confirm")
    wait_until(lambda: len(channel.texts()) > shown)
    assert channel.texts()[-1].startswith("🧹 **clear history: done, and the chat:** 3 messages taken out")
    nothing_left = ([], 1)
    assert Ledger.page(1) == nothing_left


@pytest.mark.spec("CNV-13")
def test_follow_reads_the_tasks_tracker(tmp_path, home_store):
    desk = Desk.__new__(Desk)
    desk.echo_to_terminal, desk.messages_by_run, desk.seconds_by_run, desk.waiting_plan = (
        False,
        {},
        {},
        NoWaitingPlan(),
    )
    desk.say_status = lambda *args, **kwargs: None
    reporter = Reporter(everyone([Recorder()]), "a calculator", pid=4343)
    keep_job(home_store, 1, 4343, snapshot(folder="/tmp/projects/calc", waiting=PLAN))
    process = type(
        "P",
        (),
        {
            "stdout": io.StringIO("Plan: the spec\n📋 printed text is never read\n"),
            "pid": 4343,
            "wait": lambda self: 0,
        },
    )()
    task = {"run": None, "folder": tmp_path, "title": "a calculator", "arguments": ["task"]}
    desk.follow(process, reporter, task)
    assert task["run"] == "abc123" and task["folder"] == Path("/tmp/projects/calc")
    assert task["title"] == "calc: a calculator" == reporter.progress.title
    assert desk.waiting_plan.run == "abc123" and desk.messages_by_run["abc123"] is reporter.sent
    assert reporter.tracked()["waiting"] == PLAN


@pytest.mark.spec("CNV-2")
def test_helpers(tmp_path):
    assert first_lines("a\nb\nc", 2) == "a\nb\n… 1 more lines" and first_lines("a", 2) == "a"
    assert first_words("  a \n b  c ", 3) == "a b"
    assert text_of(b"caf\xc3\xa9") == "café" and text_of(None) == "" and text_of("x") == "x"
    assert after_word(Message("1", "research the python news", "me")).text == "the python news"


def built(tmp_path, name, run_id, run, status="done", updated="2026-10-01 10:00"):
    from tinkerkit.build.project import project_store

    folder = tmp_path / name
    folder.mkdir(parents=True)
    Runs.save(dict(Runs.new_record("t", folder, None), id=run_id, status=status), folder)
    store = project_store(folder)
    store.put("run", "record", dict(store.get("run", "record"), updated=updated))
    store.put("spec", "plan", {"toolchain": {"run": run, "activate": ""}})
    return folder


@pytest.mark.spec("CNV-2")
def test_built_project(tmp_path):
    built(tmp_path, "calc", "aaa111", "x", updated="2026-10-01 10:00")
    built(tmp_path, "notes", "bbb222", "x", updated="2026-10-02 10:00")
    built(tmp_path, "todo", "ccc333", "x", status="blocked", updated="2026-10-03 10:00")
    last_built = "bbb222"
    assert built_project("")["id"] == last_built
    assert built_project("calc")["id"] == "aaa111" and built_project("ccc333")["id"] == "ccc333"
    assert built_project("ccc") is None and built_project("nothing") is None


@pytest.mark.spec("CNV-19", "JOB-10")
def test_a_chat_pauses_the_job_that_runs_before_it_is_answered(desk, monkeypatch):
    desk, channel, _ = desk
    order = []
    monkeypatch.setattr(desk.talk, "pausing_jobs", pausing_into(order))
    monkeypatch.setattr(desk.talk, "reply", lambda *asked: order.append("answered") or "the answer")
    say(desk, channel, "hello there")
    wait_until(lambda: any("the answer" in text for text in channel.texts()))
    assert order == ["paused", "answered", "goes on"]
    assert Desk([channel], desk.root).talk.pausing_jobs is jobs.answering


@pytest.mark.spec("JOB-7", "CNV-19")
def test_a_stop_ends_a_running_chat_answer_and_the_conversation_lives(desk, monkeypatch):
    desk, channel, _ = desk
    asked, never = threading.Event(), threading.Event()
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: QuickModel("m", {}, None)))
    monkeypatch.setattr(
        Models, "quick_reply", staticmethod(lambda *_, **__: asked.set() or never.wait(30) or "late")
    )
    say(desk, channel, "hello there")
    assert asked.wait(10)
    say(desk, channel, "/stop")
    wait_until(lambda: any("⏹ Stopped." in text for text in channel.texts()))
    assert Queue.ordered() == [] and signal.getsignal(desk_module.JOB_STOP_SIGNAL) is jobs.stop_answers


ECHO_APP = "import sys\nprint('args:', sys.argv[1:])\nprint('input:', sys.stdin.read().strip())\n"


@pytest.mark.spec("CNV-21")
def test_run_a_built_project(desk, tmp_path):
    desk, channel, _ = desk
    folder = built(tmp_path, "calc", "aaa111", f'"{sys.executable}" app.py')
    (folder / "app.py").write_text(ECHO_APP)
    desk.run_app(channel, "1", ['@calc add "two words" <<< hello there'])
    final = channel.texts()[-1]
    assert final.startswith("✅ **calc** `") and "add 'two words'` · _exit 0_" in final
    assert (
        "_input:_ `hello there`" in final
        and "args: ['add', 'two words']" in final
        and "input: hello there" in final
    )
    no_project_named = ["5 + 3"]
    desk.run_app(channel, "1", no_project_named)
    assert "args: ['5', '+', '3']" in channel.texts()[-1]


@pytest.mark.spec("CNV-21")
def test_run_with_nothing_to_run(desk, tmp_path):
    desk, channel, _ = desk
    desk.run_app(channel, "1", ["x"])
    assert channel.texts()[-1].startswith("🤷 **No project is built yet:**")
    built(tmp_path, "lib", "bbb222", "")
    desk.run_app(channel, "1", ["@lib"])
    assert channel.texts()[-1].startswith("🤷 **lib has no run command:**")
    desk.run_app(channel, "1", ["@nothing 1 2"])
    assert channel.texts()[-1].startswith("🤷 **No project @nothing:**")
    a_folders_name_as_an_argument = ["lib 1 2"]
    desk.run_app(channel, "1", a_folders_name_as_an_argument)
    assert "lib" in channel.texts()[-1] and "has no run command" in channel.texts()[-1]


class Listening(Recorder):
    def receive(self, timeout):
        time.sleep(0.05)
        return [self.inbox.pop(0)] if self.inbox else []


def goodbyes_handed(monkeypatch):
    handed = []
    monkeypatch.setattr(
        desk_module,
        "tinker_process",
        lambda arguments, output=None: handed.append((arguments[:2], json.loads(arguments[2]), output)),
    )
    return handed


@pytest.mark.spec("CNV-7", "CNV-9")
def test_the_status_is_sent_anew_as_tinker_starts_and_the_goodbye_is_handed_on(desk, home_store, monkeypatch):
    monkeypatch.setattr(desk_module, "ENDED_LOOK_SECONDS", 0.05)
    handed = goodbyes_handed(monkeypatch)
    status_before = 55
    home_store.put("listening", "bot", {"1": status_before})
    exit_said, confirm_said = Message("1", "/exit", id=1), Message("1", "/confirm", id=2)
    channel = Listening("bot", inbox=[exit_said, confirm_said])
    collected = []
    channel.collect = lambda: collected.append(True)
    Desk([channel], Path.cwd()).run()
    banner, first_status, the_question_to_stop = 101, 102, 103
    status = [(kind, sent, text) for kind, _, sent, text in channel.log if "**Tinker** ·" in text]
    assert [(kind, sent) for kind, sent, _ in status] == [("send", first_status)]
    assert status[0][2].startswith("🟢 **Tinker** · idle: send a task") and channel.closed
    deleted = {sent for kind, _, sent, _ in channel.log if kind == "delete"}
    assert deleted == {status_before, exit_said.id, confirm_said.id}
    assert not any("Stopping" in text for text in channel.texts())
    assert home_store.get("listening", "bot") == {"1": first_status} and collected
    assert home_store.get("banner", "bot") == {"1": banner}
    goodbye = {
        "status": "💤 **Tinker** · not listening",
        "channels": ["bot"],
        "question": {"channel": "bot", "chat": "1", "messages": [the_question_to_stop]},
    }
    assert handed == [(["channel", "goodbye"], goodbye, subprocess.DEVNULL)]
    channel.claimed.close()


@pytest.mark.spec("CNV-7")
def test_ctrl_c_closes_the_chats_and_posts_no_status_after_the_first(desk, home_store, monkeypatch):
    handed = goodbyes_handed(monkeypatch)
    channel = Listening("bot")

    def interrupted(self):
        self.start_chat(channel)
        raise KeyboardInterrupt

    monkeypatch.setattr(Desk, "listen_until_ended", interrupted)
    Desk([channel], Path.cwd()).run()
    status = [sent for kind, _, sent, text in channel.log if "**Tinker** ·" in text and kind != "delete"]
    assert channel.closed and len(status) == 1
    assert handed[0][1]["status"] == "💤 **Tinker** · not listening"
    channel.claimed.close()


@pytest.mark.spec("JOB-9", "JOB-10")
def test_the_conversation_hosts_the_runner_and_pauses_the_job_when_it_quits(
    desk, home_store, monkeypatch, runner_calls
):
    goodbyes_handed(monkeypatch)
    channel = Listening("bot")

    def interrupted(self):
        assert runner_calls == ["host"]
        raise KeyboardInterrupt

    monkeypatch.setattr(Desk, "listen_until_ended", interrupted)
    Desk([channel], Path.cwd()).run()
    assert runner_calls == ["host", ("pause", True)]
    channel.claimed.close()


@pytest.mark.spec("CNV-7")
def test_no_status_is_posted_to_a_chat_once_a_channel_is_closed(monkeypatch):
    chat = Recorder()
    console, out = piped_console(monkeypatch)
    desk = desk_with_a_started_chat(chat, console)
    chat.closed = True
    desk.say_status("💤 **Tinker** · not listening")
    assert chat.log == [] and "not listening" in out.getvalue()


@pytest.mark.spec("CNV-7")
def test_a_terminal_alone_hands_no_goodbye_on(monkeypatch):
    handed = goodbyes_handed(monkeypatch)
    console, out = piped_console(monkeypatch)
    Desk([console], Path.cwd()).say_goodbye()
    assert handed == [] and "not listening" in out.getvalue()


@pytest.mark.spec("CLI-10")
def test_a_chat_unloads_the_model_in_use_and_the_embedding_model_when_it_ends(
    desk, home_store, monkeypatch, fake_ollama
):
    monkeypatch.setattr(desk_module, "ENDED_LOOK_SECONDS", 0.05)
    goodbyes_handed(monkeypatch)
    Config.save(model="m", embed_model="e")
    channel = Listening("bot", inbox=[Message("1", "/exit", id=1), Message("1", "/confirm", id=2)])
    chat = Desk([channel], Path.cwd())
    monkeypatch.setattr(chat.talk, "warm", lambda: None)
    chat.run()
    assert sorted(fake_ollama.unloaded) == ["e", "m"]
    channel.claimed.close()


@pytest.mark.spec("CNV-7")
@pytest.mark.parametrize("slow", ["collect", "status", "adopt_exercise"])
def test_the_terminal_reads_and_exits_at_once_while_periodic_work_waits(monkeypatch, slow):
    monkeypatch.setattr(desk_module, "STATUS_LOOK_SECONDS", 0)
    monkeypatch.setattr(desk_module, "EXPIRED_MESSAGES_SWEEP_SECONDS", 0)
    colour_terminal(monkeypatch, 80)
    console = ConsoleChannel()
    desk = Desk([console], Path.cwd())
    monkeypatch.setattr(desk.talk, "warm", lambda: None)
    status = desk.status
    waits, released = threading.Event(), threading.Event()

    def waiting(then=lambda: None, passing=0):
        calls = []

        def wait():
            calls.append(True)
            if len(calls) > passing:
                waits.set()
                released.wait()
            return then()

        return wait

    typed = iter([Text("/exit"), "enter", "eof"])

    def exit_once_it_waits():
        assert waits.wait(timeout=5)
        yield from typed

    monkeypatch.setattr(shared_say, "keys", exit_once_it_waits)
    patched = {"collect": (console, waiting()), "status": (desk, waiting(status, passing=1))}
    owner, stand_in = patched.get(slow, (desk, waiting()))
    monkeypatch.setattr(owner, slow, stand_in)
    running = threading.Thread(target=desk.run, daemon=True)
    running.start()
    assert waits.wait(timeout=5)
    started = time.monotonic()
    running.join(timeout=5)
    ended_in = time.monotonic() - started
    released.set()
    assert not running.is_alive() and ended_in < 1 and console.closed


def desk_with_a_started_chat(channel, *others):
    listening = Desk([channel, *others], Path.cwd())
    listening.chats_started.add(channel)
    return listening


@pytest.mark.spec("CNV-7")
def test_every_status_is_sent_anew_the_one_before_taken_out(desk):
    channel = Recorder("bot")
    listening = desk_with_a_started_chat(channel)
    listening.say_status("🔄 **Tinker** · working on calc")
    listening.say_status("🔄 **Tinker** · working on learn go")
    listening.say_status("🟢 **Tinker** · idle: send a task")
    log = [(kind, sent) for kind, _, sent, _ in channel.log]
    assert log[0] == ("send", 101) and "edit" not in {kind for kind, _ in log}
    assert sorted(log[1:3]) == [("delete", 101), ("send", 102)]
    assert sorted(log[3:]) == [("delete", 102), ("send", 103)]


def ended_idle_calibration(lines, state="finished", idle=True, code=0):
    job = jobs.Jobs.save(dict(jobs.Jobs.new(["model", "calibrate", "fresh"]), state=state, idle=idle))
    output = Log(Home.store(), f"output #{job['number']}")
    for line in lines:
        output.add({"line": line})
    output.add({"exit": code})
    return job


def unstamped(texts):
    return [text.split("\n\n🕒")[0] for text in texts]


@pytest.mark.spec("MOD-31")
def test_an_idle_calibration_ending_is_said_once_in_started_chats_and_consoles(home_store):
    ready = [f"{Messages.ICONS['done']} fresh is ready", "🧠 fresh thinks at 40 tokens a second"]
    channel, other, console = Recorder("bot"), Recorder("quiet"), Recorder("console")
    console.show = lambda chat, reply_to, text, **options: console.log.append(("send", chat, None, text))
    listening = desk_with_a_started_chat(channel, other)
    listening.consoles = [console]
    ended_idle_calibration(ready)
    listening.say_idle_endings()
    listening.say_idle_endings()
    assert unstamped(channel.texts()) == ["\n".join(ready)]
    assert console.texts() == ["\n".join(ready)] and other.texts() == []


@pytest.mark.spec("MOD-31")
def test_a_failed_idle_calibration_says_its_lines_as_they_are(home_store):
    channel = Recorder("bot")
    listening = desk_with_a_started_chat(channel)
    ended_idle_calibration(["❌ fresh could not be measured"], code=1)
    listening.say_idle_endings()
    assert unstamped(channel.texts()) == ["❌ fresh could not be measured"]


@pytest.mark.spec("MOD-31")
def test_only_idle_calibrations_ended_after_the_desk_started_are_said(home_store):
    ended_idle_calibration(["✅ old is ready"])
    channel = Recorder("bot")
    listening = desk_with_a_started_chat(channel)
    ended_idle_calibration(["✅ asked for"], idle=False)
    ended_idle_calibration(["half way"], state="queued")
    listening.say_idle_endings()
    assert channel.texts() == []


@pytest.mark.spec("CNV-7")
def test_each_chat_starts_cleared_with_the_banner_before_the_status(desk, monkeypatch):
    channel, cleared, lasting = Recorder("bot"), [], []
    monkeypatch.setattr(channel, "clear", lambda: cleared.append(channel.name) or 0)
    monkeypatch.setattr(channel, "lasting", lambda chat, sent: lasting.append(list(sent)))
    listening = Desk([channel], Path.cwd())
    listening.said_status = "🟢 **Tinker** · idle"
    monkeypatch.setattr(
        listening, "banner_lines", lambda: ["🔧 Tinker", "model qwen3", "help lists commands"]
    )
    listening.start_chat(channel)
    listening.start_chat(channel)
    log = [(kind, sent, text) for kind, _, sent, text in channel.log]
    banner = "**🔧 Tinker**\nmodel qwen3\n_help lists commands_"
    assert cleared == ["bot", "bot"] and log[0] == ("send", 101, banner) and log[1][:2] == ("send", 102)
    assert {entry[1] for entry in log[2:] if entry[0] == "delete"} == {101, 102}
    assert [entry for entry in log[2:] if entry[0] == "send"][0] == ("send", 103, banner)
    assert [101] in lasting and [103] in lasting


def renewing_desk(monkeypatch, lines):
    channel, console, done = Recorder("bot"), ConsoleChannel(), []
    monkeypatch.setattr(channel, "clear", lambda: done.append("clear bot") or 0)
    monkeypatch.setattr(console, "clear", lambda: done.append("clear console") or 0)
    monkeypatch.setattr(console, "wipe", lambda shown: done.extend(["wipe", list(shown)]))
    monkeypatch.setattr(console, "banner", lambda shown: done.append(list(shown)))
    listening = Desk([console, channel], Path.cwd())
    monkeypatch.setattr(listening, "banner_lines", lambda: list(lines))
    listening.open_banner()
    listening.chats_started.add(channel)
    return listening, channel, done


@pytest.mark.spec("CNV-6")
def test_a_message_that_changes_the_banner_clears_the_chats_and_shows_the_banner_anew(desk, monkeypatch):
    lines = ["🔧 Tinker", "model qwen3", "help lists commands"]
    listening, channel, done = renewing_desk(monkeypatch, lines)
    lines[1] = "model gemma3"
    listening.hear(channel, Message("1", "/jobs", "you", id=7))
    banner = "**🔧 Tinker**\nmodel gemma3\n_help lists commands_"
    sent = channel.texts("send")
    assert done[0] == ["🔧 Tinker", "model qwen3", "help lists commands"]
    assert set(done[1:3]) == {"clear bot", "clear console"} and done[3:] == ["wipe", list(lines)]
    assert banner in sent and sent.index(banner) == len(sent) - 2


@pytest.mark.spec("CNV-12")
def test_clear_in_a_chat_clears_that_chat_alone(desk, monkeypatch):
    lines = ["🔧 Tinker", "model qwen3", "help lists commands"]
    listening, channel, done = renewing_desk(monkeypatch, lines)
    listening.hear(channel, Message("1", "/clear"))
    assert done[1:] == ["clear bot"]


@pytest.mark.spec("CNV-12")
def test_clear_in_the_terminal_wipes_it_down_to_the_banner_and_the_status_with_no_note(desk, monkeypatch):
    lines = ["🔧 Tinker", "model qwen3", "help lists commands"]
    listening, _, done = renewing_desk(monkeypatch, lines)
    console, shown = listening.consoles[0], []
    monkeypatch.setattr(console, "send", lambda chat, text: done.append(text) or 1)
    monkeypatch.setattr(console, "show", lambda chat, asked, text, done=False: shown.append(text))
    listening.answer(Message(ConsoleChannel.CHAT, "/clear"), console)
    assert done[1:] == ["clear console", "wipe", lines]
    assert shown == ["🟢 **Tinker** · idle: send a task"]


@pytest.mark.spec("CNV-12")
def test_clear_in_the_terminal_leaves_a_followed_job_to_its_live_line(desk, monkeypatch):
    listening, _, _ = renewing_desk(monkeypatch, ["🔧 Tinker"])
    console, shown = listening.consoles[0], []
    monkeypatch.setattr(console, "show", lambda chat, asked, text, done=False: shown.append(text))
    monkeypatch.setattr(listening, "terminal_follows_job", lambda: True)
    monkeypatch.setattr(listening, "keep_job_at_bottom", lambda: shown.append("job at bottom"))
    listening.answer(Message(ConsoleChannel.CHAT, "/clear"), console)
    assert shown == ["job at bottom"]


@pytest.mark.spec("CNV-6")
def test_a_message_that_leaves_the_banner_alone_clears_nothing(desk, monkeypatch):
    lines = ["🔧 Tinker", "model qwen3", "help lists commands"]
    listening, channel, done = renewing_desk(monkeypatch, lines)
    listening.hear(channel, Message("1", "/jobs", "you", id=7))
    assert done == [lines] and not any("🔧 Tinker" in text for text in channel.texts())


def status_said_while_busy(monkeypatch, asker):
    chat, console, shown = Recorder("bot"), ConsoleChannel(), []
    monkeypatch.setattr(console, "show", lambda chat, sent, text, done=False: shown.append(text) or [1])
    listening = desk_with_a_started_chat(chat)
    listening.channels.append(console)
    listening.consoles.append(console)
    listening.reporter = Reporter(everyone([asker(chat, console)]), "calc")
    monkeypatch.setattr(listening, "keep_job_at_bottom", lambda: None)
    monkeypatch.setattr(listening, "busy", lambda: True)
    listening.say_status("🔄 **Tinker** · working on calc")
    monkeypatch.setattr(listening, "busy", lambda: False)
    listening.say_status("🟢 **Tinker** · idle: send a task")
    assert any("working on calc" in text for text in chat.texts("send"))
    return shown


@pytest.mark.spec("CNV-6")
def test_the_terminal_leaves_out_what_tinker_works_on_while_a_job_shows_its_progress(desk, monkeypatch):
    shown = status_said_while_busy(monkeypatch, lambda chat, console: console)
    assert shown == ["🟢 **Tinker** · idle: send a task"]


@pytest.mark.spec("CNV-6")
def test_the_terminal_prints_what_tinker_works_on_when_a_chat_asked(desk, monkeypatch):
    shown = status_said_while_busy(monkeypatch, lambda chat, console: chat)
    assert shown == ["🔄 **Tinker** · working on calc", "🟢 **Tinker** · idle: send a task"]


@pytest.mark.spec("CNV-7")
def test_a_job_says_its_status_as_it_starts_and_ends(desk, home_store, monkeypatch):
    channel = Recorder("bot")
    listening = desk_with_a_started_chat(channel)
    listening.say_status()

    class Running:
        pid, stdout, code = os.getpid(), [], None

        def poll(self):
            return self.code

        def wait(self, timeout=None):
            return 0

    job = Running()

    def spawn(arguments):
        keep_job(home_store, 1, os.getpid(), None, title="learn go")
        Queue.add_front(1)
        return job

    monkeypatch.setattr(listening, "spawn", spawn)
    monkeypatch.setattr(listening, "follow", lambda process, reporter, task: None)
    listening.start_task(everyone([channel]), ["learn", "go"], Path.cwd(), "learn go")
    sent = channel.texts("send")
    assert sent[0].startswith("🟢 **Tinker** · idle")
    assert sent[1].startswith("🔄 **Tinker** · working on learn go\n\n🕒") and "learn go" in sent[2]
    listening.reporter.progress.snapshot["doing"] = "building"
    listening.reporter.update()
    assert listening.status() == "🔄 **Tinker** · working on learn go"
    listening.say_status("🔄 **Tinker** · working on learn go; calc in a terminal")
    sent = channel.texts("send")
    assert "calc in a terminal" in sent[-2] and "building" in sent[-1]
    job.code = 0
    Queue.remove(1)
    listening.ended(job, listening.reporter, listening.task)
    assert listening.said_status.startswith("🟢 **Tinker** · idle")


@pytest.mark.spec("CNV-7")
def test_a_run_in_a_terminal_shows_its_title_and_never_how_far_it_got(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    state = snapshot(title="calc", doing="🔨 Building", passing=[1, 2], pid_began=began_at(os.getppid()))
    keep_job(home_store, 1, os.getppid(), state, title="calc")
    add_back(1)
    assert desk.status() == "🔄 **Tinker** · working on calc"


@pytest.mark.spec("CNV-9")
def test_status_counts_the_jobs_queued_behind_the_one_that_runs(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    for number, title in ((1, "calc"), (2, "learn go"), (3, "notes")):
        keep_job(home_store, number, os.getpid(), None, title=title, state="queued")
        add_back(number)
    assert desk.status() == "🔄 **Tinker** · working on calc · 2 queued"


@pytest.mark.spec("CNV-14")
def test_running_now_lists_the_queue_in_order_from_saved_progress(home_store, tmp_path):
    desk = Desk([Recorder()], tmp_path)
    for number, title in ((2, "learn go"), (1, "calc")):
        keep_job(home_store, number, os.getpid(), snapshot(title=title), title=title, state="queued")
        add_back(number)
    assert [job["title"] for job in desk.running_now()] == ["learn go", "calc"]


@pytest.mark.spec("CNV-7")
def test_desk_refuses_a_chat_another_tinker_listens_to(desk):
    _, _, script = desk
    holder = Recorder("bot")
    assert holder.claim({"pid": 999})
    try:
        with pytest.raises(SystemExit, match="bot is listened to by another Tinker \\(process 999\\)"):
            Desk([Recorder("bot")], Path.cwd()).run()
    finally:
        holder.claimed.close()


@pytest.mark.spec("CNV-18")
def test_the_commands_brief_keeps_each_commands_arguments_description_and_options(monkeypatch):
    shown = "**Build**\n- `/task WHAT` build something new (--spec FILE)\n- `/channel add [--kind KIND]` add one\n"
    monkeypatch.setattr(desk_module, "help_text", lambda: shown)
    assert desk_module.commands_brief() == (
        "- `/task WHAT` build something new (--spec FILE)\n- `/channel add [--kind KIND]` add one"
    )


@pytest.mark.spec("CNV-18")
def test_the_chat_index_names_each_chat_command_once_grouped_as_in_help_with_what_it_is_for():
    index = desk_module.chat_index()
    named = re.findall(r"^/(\S+) — ", index, re.M)
    chat_names = [command.name for command in COMMANDS if command.name and command.chat]
    assert named == [
        command.name
        for group in GROUPS
        for command in COMMANDS
        if command.group == group and command.name in chat_names
    ]
    assert all(re.fullmatch(r"[A-Z][\w ]*:|/\S+ — .+", line) for line in index.splitlines())
    assert "/clear" in index and not set(named) - set(chat_names)
    assert (
        "/skill — learn a shell command, alias, script or program as a skill, and list or forget the skills"
        in index.splitlines()
    )


@pytest.mark.spec("CNV-18", "RVW-3")
def test_a_message_routed_to_none_is_reviewed_and_answered_without_the_commands(
    measured_model, fake_ollama, monkeypatch
):
    monkeypatch.setattr(ThinkLevels, "values", staticmethod(lambda name: ["low", "medium", "high"]))
    fake_ollama.reply("none").reply("low: a plain request").reply("It is a bird.")
    assert Talk("task").reply(Recorder(), Message("1", "tell me about the robin")) == "It is a bird."
    route, review, answer = fake_ollama.calls[-3:]
    assert system_of(route).startswith(
        desk_module.Prompts.CHAT_ROUTE_SYSTEM.format(index=desk_module.chat_index())
    )
    assert route["think"] is False and route["options"]["num_predict"] == desk_module.CHAT_ROUTE_TOKENS
    assert route["options"]["temperature"] == 0 and "temperature" not in answer["options"]
    assert "low: a plain request" not in system_of(answer) and "/skill" not in system_of(answer)
    assert system_of(answer).startswith(
        desk_module.Prompts.CHAT_SYSTEM.format(style=Recorder.STYLE, state="")
    )


@pytest.mark.spec("CNV-18", "RVW-3")
def test_a_message_routed_to_a_command_that_takes_words_goes_to_the_fill_call_unreviewed(
    measured_model, fake_ollama, monkeypatch
):
    monkeypatch.setattr(ThinkLevels, "values", staticmethod(lambda name: ["low", "medium", "high"]))
    fake_ollama.reply("/skill").reply("/skill forget mgen")
    assert Talk("task").reply(Recorder(), Message("1", "forget the mgen skill")) == "/skill forget mgen"
    route, fill = fake_ollama.calls[-2:]
    assert system_of(fill).startswith(
        desk_module.Prompts.CHAT_FILL_SYSTEM.format(commands=desk_module.command_details(["skill"]), state="")
    )
    assert fill["think"] is False and len(fake_ollama.calls) == 2
    assert route["options"]["temperature"] == 0 and fill["options"]["temperature"] == 0


@pytest.mark.spec("CNV-18")
def test_a_fill_line_naming_a_command_the_route_did_not_name_is_dropped(measured_model, fake_ollama):
    Skills.put(made_skill("mgen"))
    fake_ollama.reply("/skill").reply('/skill learn mgen "generate an image from text"\n/skill mgen a shark')
    reply = Talk("task").reply(Recorder(), Message("1", "learn mgen and draw a shark"))
    assert reply == '/skill learn mgen "generate an image from text"'
    assert len(fake_ollama.calls) == 2


@pytest.mark.spec("CNV-18")
@pytest.mark.parametrize(("routed", "run"), [("help skill", "/help skill"), ("/help", "/help")])
def test_a_fill_reply_left_with_no_line_runs_the_routed_lines(measured_model, fake_ollama, routed, run):
    fake_ollama.reply(routed).reply('/skill learn mgen "generate an image"')
    assert Talk("task").reply(Recorder(), Message("1", "how do I learn a skill")) == run
    assert len(fake_ollama.calls) == 2


@pytest.mark.spec("CNV-18")
def test_a_fill_line_naming_the_routed_command_is_kept(measured_model, fake_ollama):
    fake_ollama.reply("/system").reply("/system ram")
    assert Talk("task").reply(Recorder(), Message("1", "how much ram is free")) == "/system ram"
    assert len(fake_ollama.calls) == 2


@pytest.mark.spec("CNV-18")
@pytest.mark.parametrize("routed", ["/runs", "`/runs`", "runs"])
def test_a_message_routed_to_a_command_without_words_runs_it_at_once(measured_model, fake_ollama, routed):
    fake_ollama.reply(routed)
    assert Talk("task").reply(Recorder(), Message("1", "which projects are there")) == "/runs"
    assert len(fake_ollama.calls) == 1


@pytest.mark.spec("CNV-18")
def test_a_route_line_names_the_command_its_leading_words_name():
    Skills.put(made_skill("mgen"))
    assert desk_module.named_commands("runs") == ["runs"]
    assert desk_module.named_commands("skill learn mgen") == ["skill"]
    assert desk_module.named_commands("/system ram") == ["system"]
    assert desk_module.named_commands("`skill mgen a cat`") == ["skill mgen"]
    assert desk_module.named_commands("runs\nsystem ram") == ["runs", "system"]
    assert desk_module.named_commands("runs\nno such thing") == []
    assert desk_module.named_commands("none") == []


@pytest.mark.spec("CNV-18")
def test_the_command_details_give_only_the_named_commands_rows():
    details = desk_module.command_details(["clear", "system"]).splitlines()
    rows = [row for name in ("clear", "system") for row in BY_NAME[name].rows]
    assert details == [f"- `/{synopsis}` {what}" for synopsis, what in rows]
    assert desk_module.command_details(["no-such-command"]) == ""


@pytest.mark.spec("CNV-18")
def test_the_chat_index_lists_each_learned_skill_only_when_there_are_some():
    assert not any(line.startswith("/skill thumbs") for line in desk_module.chat_index().splitlines())
    Skills.put(made_skill("thumbs"))
    Skills.put(made_skill("count", purpose="count lines"))
    index = desk_module.chat_index().splitlines()
    assert index[-2:] == ["/skill thumbs — make a thumbnail", "/skill count — count lines"]


@pytest.mark.spec("CNV-18")
def test_a_reply_naming_a_known_skill_goes_to_the_fill_call_with_its_template_and_slots():
    Skills.put(made_skill("thumbs"))
    assert desk_module.named_commands("/skill thumbs") == ["skill thumbs"]
    assert desk_module.named_commands("/skill ghost") == ["skill"]
    assert desk_module.takes_words("skill thumbs")
    assert desk_module.command_details(["skill thumbs"]).splitlines() == [
        "- `/skill thumbs WORDS` make a thumbnail",
        "  template: convert {source} -resize {size} {target}",
        "  slots: source (e.g. a.png), size (e.g. 64x64)",
    ]
    assert desk_module.ChatState(None).for_commands(["skill thumbs"]) == ""


@pytest.mark.spec("CNV-22")
def test_one_shot_commands(monkeypatch, capsys):
    monkeypatch.setattr(desk_module, "commands_brief", lambda: "cmds")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert desk_module.one_shot("chat", "") == "tinker chat needs words after it, e.g. tinker chat hello"
    assert (
        desk_module.one_shot("command", "") == "tinker command needs words after it, e.g. tinker command ..."
    )
    assert desk_module.one_shot("history", "") is None
    assert "nothing shown in the chats yet" in capsys.readouterr().out
    desk_module.one_shot("chat", "hello")
    assert "No model is in use yet" in capsys.readouterr().out


@pytest.mark.spec("CNV-22")
def test_one_shot_command_runs_nothing_without_a_yes(
    monkeypatch, measured_model, fake_ollama, capsys, tmp_path
):
    monkeypatch.setattr(desk_module, "commands_brief", lambda: "cmds")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.chdir(tmp_path)
    fake_ollama.reply("low: a plain request").reply('{"command": "touch made.txt", "does": "makes a file"}')
    desk_module.one_shot("command", "make a file")
    out = capsys.readouterr().out
    assert "touch made.txt" in out and "Not run:" in out and not (tmp_path / "made.txt").exists()


@pytest.mark.spec("CNV-8")
def test_start_is_answered_with_a_welcome(desk):
    desk, channel, script = desk
    say(desk, channel, "/start")
    text = channel.texts()[-1]
    assert text.startswith("👋 **Hi, I'm Tinker!**")
    for section in (
        "🔨 **Build something**",
        "📂 **Your projects**",
        "💬 **Ask me anything**",
        "⏳ **While I work**",
    ):
        assert section in text
    assert "`/task a calculator in Python`" in text and "normal chat" in text and "`/help`" in text
    assert "`confirm`" not in text and "`/confirm`" in text
    assert calls_of(script) == ["help"] and desk.last_task is None
    a_start_link_with_a_payload = "/start abcdef"
    say(desk, channel, a_start_link_with_a_payload)
    assert channel.texts()[-1] == welcome()


@pytest.mark.spec("CNV-23")
def test_knowledge_graph_is_sent_as_a_file(desk, monkeypatch):
    desk, channel, _ = desk
    sent = []
    channel.send_file = lambda chat, path, caption: sent.append((chat, path, caption))
    page = desk_module.Home.graph_page()

    def make_page(words):
        desk.ran.append(words)
        page.write_text("<!doctype html>")
        return "Tinker's memory: 2 lessons; 3 links"

    monkeypatch.setattr(desk, "tinker", make_page)
    assert command_of("/knowledge graph") == "knowledge"
    assert desk.answer(Message("1", "knowledge graph"), channel) is None
    assert desk.ran == [["knowledge", "graph"]]
    assert sent == [("1", page, "🧠 **Tinker's memory**\nTinker's memory: 2 lessons; 3 links")]
    monkeypatch.setattr(desk, "tinker", lambda words: "Error: the home cannot be read")
    assert (
        desk.answer(Message("1", "knowledge graph"), channel)
        == "🧠 **knowledge graph**\n```\nError: the home cannot be read\n```"
        and len(sent) == 1
    )


@pytest.mark.spec("CNV-23", "DRM-9")
def test_memory_graph_and_dream_graph_send_the_memory_page(desk, monkeypatch):
    desk, channel, _ = desk
    sent = []
    channel.send_file = lambda chat, path, caption: sent.append((chat, path, caption))
    page = desk_module.Home.memory_page()

    def make_page(words):
        desk.ran.append(words)
        page.write_text(f"<!doctype html>{len(desk.ran)}")
        return "Tinker's episodes and memories: 2 episodes, 1 memory; 2 links"

    monkeypatch.setattr(desk, "tinker", make_page)
    assert desk.answer(Message("1", "memory graph"), channel) is None
    assert desk.answer(Message("1", "dream graph"), channel) is None
    assert desk.ran == [["memory", "graph"], ["dream", "graph"]]
    caption = (
        "💭 **Tinker's episodes and memories**\nTinker's episodes and memories: 2 episodes, 1 memory; 2 links"
    )
    assert sent == [("1", page, caption), ("1", page, caption)]


@pytest.mark.spec("GRF-6", "GRF-10", "CMD-1")
def test_knowledge_with_words_is_browsed_in_a_chat(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk, "tinker", lambda words: desk.ran.append(words) or "[2d35d6] ✅ lesson    Seed")
    assert command_of("/knowledge python lessons 2") == "knowledge"
    answer = desk.answer(Message("1", "knowledge python lessons 2"), channel)
    assert desk.ran == [["knowledge", "python", "lessons", "2"]]
    assert answer == "🧠 **knowledge python lessons 2**\n```\n[2d35d6] ✅ lesson    Seed\n```"
    desk.answer(Message("1", "knowledge show [2d35d6]"), channel)
    assert desk.ran[-1] == ["knowledge", "show", "[2d35d6]"]


@pytest.fixture
def inside(desk, monkeypatch):
    desk, channel, _ = desk
    asked = []
    monkeypatch.setattr(Shelf, "entered", lambda self, words: (3, "Small Craft"))
    monkeypatch.setattr(Shelf, "exists", staticmethod(lambda number: True))
    monkeypatch.setattr(
        Shelf, "answered", staticmethod(lambda number, text: asked.append((number, text)) or "It is a boat.")
    )
    monkeypatch.setattr(desk.talk, "answer", lambda channel, message, *rest, **options: asked.append("talk"))
    desk.handle(channel, Message("1", "/read book.pdf"))
    return desk, channel, asked


@pytest.mark.spec("CNV-25")
def test_keeping_a_reading_with_chapters_enters_it_in_one_line_saying_how_to_leave(inside):
    desk, channel, _ = inside
    assert desk.inside == {"1": (3, "Small Craft")}
    assert channel.texts()[-1].endswith(
        "\n📕 **Inside #3 Small Craft**: ask me about it · `/leave` leaves it"
    )


@pytest.mark.spec("CNV-25")
def test_inside_a_reading_a_plain_message_is_answered_by_the_shelf_not_the_chat(inside):
    desk, channel, asked = inside
    desk.handle(channel, Message("1", "what is it"))
    assert asked == [(3, "what is it")] and channel.texts()[-1] == "It is a boat."


@pytest.mark.spec("CNV-25")
def test_inside_a_reading_a_command_still_runs(inside):
    desk, channel, asked = inside
    desk.handle(channel, Message("1", "/read list"))
    assert desk.ran[-1] == ["read", "list"] and asked == [] and desk.inside


def tinker_envs(desk, monkeypatch):
    envs = []
    monkeypatch.delenv("TINKER_INSIDE", raising=False)
    monkeypatch.delattr(desk, "tinker")
    monkeypatch.setattr(
        desk_module.subprocess,
        "run",
        lambda command, **options: (
            envs.append(options["env"]) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    return envs


@pytest.mark.spec("RDG-15")
def test_inside_a_reading_the_reading_list_is_told_which_one(inside, monkeypatch):
    desk, channel, _ = inside
    envs = tinker_envs(desk, monkeypatch)
    desk.handle(channel, Message("1", "/read list"))
    assert envs[-1]["TINKER_INSIDE"] == "3" and envs[-1]["NO_COLOR"] == "1"


@pytest.mark.spec("RDG-15")
def test_outside_a_reading_the_reading_list_is_told_none(desk, monkeypatch):
    desk, channel, _ = desk
    envs = tinker_envs(desk, monkeypatch)
    desk.handle(channel, Message("1", "/read list"))
    assert envs and "TINKER_INSIDE" not in envs[-1]


@pytest.mark.spec("CNV-25")
def test_leave_leaves_the_reading_and_plain_messages_go_back_to_the_chat(inside):
    desk, channel, asked = inside
    desk.handle(channel, Message("1", "/leave"))
    assert channel.texts()[-1] == "📕 **Left #3 Small Craft**" and desk.inside == {}
    desk.handle(channel, Message("1", "hello"))
    assert asked == ["talk"]
    desk.handle(channel, Message("1", "/leave"))
    assert channel.texts()[-1] == "📕 **Not inside a reading, the interview or a job**"


@pytest.mark.spec("CNV-25")
def test_the_channel_is_told_on_entering_and_leaving_a_reading(inside):
    desk, channel, _ = inside
    desk.handle(channel, Message("1", "/leave"))
    told = [(chat, reading) for kind, chat, _, reading in channel.log if kind == "inside"]
    assert told == [("1", (3, "Small Craft")), ("1", None)]


@pytest.mark.spec("CNV-25")
def test_each_answer_inside_offers_the_leave_button(inside):
    desk, channel, _ = inside
    desk.handle(channel, Message("1", "what is it"))
    assert channel.log[-1] == ("leave", "1", channel.newest["1"], "📕 Leave #3")


@pytest.mark.spec("CNV-25")
def test_a_reading_gone_is_left_and_the_channel_told(inside, monkeypatch):
    desk, channel, asked = inside
    monkeypatch.setattr(Shelf, "exists", staticmethod(lambda number: False))
    desk.handle(channel, Message("1", "hello"))
    assert asked == ["talk"] and desk.inside == {}
    assert ("inside", "1", None, None) in channel.log
    assert not [entry for entry in channel.log if entry[0] == "leave"]


@pytest.mark.spec("RDG-15")
def test_a_command_deleting_the_entered_reading_leaves_it_in_that_call(inside, monkeypatch):
    desk, channel, _ = inside
    gone = []
    monkeypatch.setattr(Shelf, "exists", staticmethod(lambda number: not gone))
    monkeypatch.setattr(desk, "answer", lambda message, channel: gone.append(3) or "Forgot #3")
    desk.handle(channel, Message("1", "/read forget #3"))
    assert channel.log[-1] == ("inside", "1", None, None) and desk.inside == {}


@pytest.mark.spec("RDG-15")
def test_confirming_the_forgetting_of_the_entered_reading_leaves_it(inside, monkeypatch):
    desk, channel, _ = inside
    confirmed = threading.Event()
    monkeypatch.setattr(
        desk, "tinker", lambda words: confirmed.wait(5) and desk.ran.append(words) or "✅ Forgot #3"
    )
    monkeypatch.setattr(
        Shelf, "exists", staticmethod(lambda number: ["read", "forget", "#3", "--yes"] not in desk.ran)
    )
    say(desk, channel, "/read forget #3")
    say(desk, channel, "/confirm")
    confirmed.set()
    wait_until(lambda: ("inside", "1", None, None) in channel.log and desk.inside == {})


@pytest.mark.spec("CNV-25")
def test_the_terminal_prompt_names_the_reading_while_inside(monkeypatch):
    monkeypatch.setattr("tinkerkit.chat.console.typed_here", lambda: True)
    channel = ConsoleChannel()
    plain = channel.prompt()
    channel.now_inside("console", InsideReading(3, "Small Craft"))
    assert channel.prompt() == "📕 #3 Small Craft › "
    channel.now_inside("console", None)
    assert channel.prompt() == plain


ENTERED = "💬 **Interview**: answer, /skip · /leave leaves it"


RUST_OPENING = "💭 I've been thinking: You mention Rust a lot. When do you write Rust? My guess: On weekdays."


EDITOR_OPENING = "💭 I've been thinking: You never named one. Which editor do you use? My guess: Helix."


def rust_question():
    return ask("When do you write Rust?", thought="You mention Rust a lot.", expected="On weekdays.")


def editor_question():
    return ask("Which editor do you use?", thought="You never named one.", expected="Helix.")


@pytest.fixture
def interviewing(desk):
    desk, channel, _ = desk
    rust_question()
    editor_question()
    desk.interview = Interview()
    asked = []
    desk.talk.answer = lambda channel, message, *rest, **options: asked.append("talk")
    return desk, channel, asked, desk.interviewed(channel, "1")


@pytest.mark.spec("CNV-25", "INT-1")
def test_entering_the_interview_says_how_to_leave_then_shows_the_next_question(interviewing):
    desk, channel, _, entered = interviewing
    assert entered == f"{ENTERED}\n\n{RUST_OPENING}"
    assert channel.log[-1] == ("inside", "1", None, desk.inside["1"])
    assert desk.inside["1"].prompt() == "💬 interact › "


@pytest.mark.spec("INT-1")
def test_a_desk_opened_interviewing_shows_the_first_question_after_the_status(monkeypatch):
    terminal = colour_terminal(monkeypatch, 80)
    typing(monkeypatch)
    rust_question()
    desk = Desk([ConsoleChannel()], Path.cwd(), interview=Interview(), interviewing=True)
    monkeypatch.setattr(desk.talk, "warm", lambda: None)
    desk.run()
    shown = terminal.getvalue()
    assert shown.index("idle") < shown.index("When do you write Rust?")
    assert desk.inside[ConsoleChannel.CHAT].prompt() == "💬 interact › "


@pytest.mark.spec("INT-1")
def test_a_desk_opened_interviewing_with_nothing_pending_says_so(monkeypatch):
    terminal = colour_terminal(monkeypatch, 80)
    typing(monkeypatch)
    desk = Desk([ConsoleChannel()], Path.cwd(), interview=Interview(), interviewing=True)
    monkeypatch.setattr(desk.talk, "warm", lambda: None)
    desk.run()
    assert "all I had on my mind" in terminal.getvalue() and desk.inside == {}


@pytest.mark.spec("INT-1", "CNV-3")
@pytest.mark.parametrize("text", ["/interact", "interact"])
def test_interact_enters_the_interview_and_shows_the_first_question(desk, text):
    desk, channel, _ = desk
    rust_question()
    desk.interview = Interview()
    desk.handle(channel, Message("1", text))
    assert channel.texts()[-1] == f"{ENTERED}\n\n{RUST_OPENING}"
    assert desk.inside["1"].prompt() == "💬 interact › "


@pytest.mark.spec("INT-1")
def test_the_first_interview_turn_offers_its_answer_buttons(desk):
    desk, channel, _ = desk
    rust_question()
    desk.interview = Interview()
    desk.handle(channel, Message("1", "/interact"))
    assert channel.log[-1] == ("answers", "1", channel.newest["1"], "")


@pytest.mark.spec("INT-1")
def test_each_later_interview_turn_offers_its_answer_buttons(interviewing):
    desk, channel, _, _ = interviewing
    desk.handle(channel, Message("1", "weekdays mostly"))
    assert channel.log[-1] == ("answers", "1", channel.newest["1"], "")
    desk.handle(channel, Message("1", "/skip"))
    assert channel.log[-1] == ("answers", "1", channel.newest["1"], "")


@pytest.mark.spec("INT-1")
def test_nothing_left_to_ask_offers_no_answer_buttons(interviewing):
    desk, channel, _, _ = interviewing
    for question in KeptQuestions().pending():
        KeptQuestions().answer(question["id"], "done")
    desk.handle(channel, Message("1", "/skip"))
    assert not [entry for entry in channel.log if entry[0] == "answers"]


@pytest.mark.spec("CNV-25")
def test_entering_a_reading_offers_no_answer_buttons(inside):
    _, channel, _ = inside
    assert not [entry for entry in channel.log if entry[0] in ("answers", "leave")]


@pytest.mark.spec("CNV-25", "INT-1", "INT-2")
def test_interact_with_no_question_pending_and_no_quick_model_says_nothing_is_left(desk):
    desk, channel, _ = desk
    desk.interview = Interview()
    desk.handle(channel, Message("1", "/interact"))
    assert channel.texts()[-1] == ALL_FOR_NOW
    assert desk.inside == {}


@pytest.mark.spec("INT-3")
def test_inside_the_interview_a_plain_message_shows_the_same_question_again(interviewing):
    desk, channel, asked, _ = interviewing
    desk.handle(channel, Message("1", "weekdays mostly"))
    assert channel.texts()[-1] == f"{UNREADABLE}\n{RUST_OPENING}" and asked == []


@pytest.mark.spec("INT-3")
def test_inside_the_interview_an_answer_moves_to_the_next_question(interviewing, monkeypatch):
    desk, channel, asked, _ = interviewing
    reply = {
        "say": "Got it.",
        "confirm": [],
        "reject": [],
        "amend": [],
        "add": [],
        "question": None,
        "settled": True,
    }
    quick_model_replying(monkeypatch, lambda messages: json.dumps(reply))
    desk.handle(channel, Message("1", "weekdays mostly"))
    assert channel.texts()[-1] == f"Got it.\n\n{EDITOR_OPENING}" and asked == []
    assert KeptQuestions().all()[0]["answer"] == "weekdays mostly"


@pytest.mark.spec("INT-4")
def test_skip_inside_the_interview_shows_the_next_question(interviewing):
    desk, channel, _, _ = interviewing
    desk.handle(channel, Message("1", "/skip"))
    assert channel.texts()[-1] == EDITOR_OPENING
    assert KeptQuestions().next()["text"] == "Which editor do you use?"


@pytest.mark.spec("INT-1", "INT-4")
def test_skip_with_nothing_left_says_so_and_leaves_the_interview(interviewing):
    desk, channel, _, _ = interviewing
    for question in KeptQuestions().pending():
        KeptQuestions().answer(question["id"], "done")
    desk.handle(channel, Message("1", "/skip"))
    assert channel.texts()[-1] == ALL_FOR_NOW
    assert desk.inside == {} and channel.log[-1] == ("inside", "1", None, None)


@pytest.mark.spec("INT-4")
def test_inside_the_interview_another_command_still_runs(interviewing):
    desk, channel, _, _ = interviewing
    desk.handle(channel, Message("1", "/read list"))
    assert desk.ran[-1] == ["read", "list"] and desk.inside


@pytest.mark.spec("CNV-25")
def test_leave_leaves_the_interview(interviewing):
    desk, channel, asked, _ = interviewing
    desk.handle(channel, Message("1", "/leave"))
    assert channel.texts()[-1] == "💬 **Left the interview**" and desk.inside == {}
    desk.handle(channel, Message("1", "hello"))
    assert asked == ["talk"]


@pytest.mark.spec("CNV-25")
def test_entering_the_interview_leaves_a_reading(inside):
    desk, channel, _ = inside
    editor_question()
    desk.interview = Interview()
    desk.interviewed(channel, "1")
    assert desk.inside["1"] == InsideInterview(desk.interview)


@pytest.mark.spec("CNV-25")
def test_entering_the_interview_takes_its_turn_once(inside, monkeypatch):
    desk, channel, _ = inside
    editor_question()
    desk.interview = Interview()
    turns = []
    taken = Interview.turn
    monkeypatch.setattr(Interview, "turn", lambda self, chat: turns.append(chat) or taken(self, chat))
    entered = desk.interviewed(channel, "1")
    assert turns == ["1"] and "Which editor do you use?" in entered


@pytest.mark.spec("CNV-25")
def test_entering_a_reading_leaves_the_interview(interviewing, monkeypatch):
    desk, channel, _, _ = interviewing
    monkeypatch.setattr(Shelf, "entered", lambda self, words: (3, "Small Craft"))
    monkeypatch.setattr(Shelf, "exists", staticmethod(lambda number: True))
    desk.handle(channel, Message("1", "/read book.pdf"))
    assert desk.inside == {"1": (3, "Small Craft")}


@pytest.mark.spec("INT-1")
def test_the_terminal_prompt_says_interact_inside_the_interview(monkeypatch):
    monkeypatch.setattr("tinkerkit.chat.console.typed_here", lambda: True)
    channel = ConsoleChannel()
    channel.now_inside("console", InsideInterview(NoInterview()))
    assert channel.prompt() == "💬 interact › "


def queued_job(home_store, number, title):
    put_job(home_store, number, title=title, state="running", pid=os.getpid(), arguments=title.split())
    Queue.place(number, lambda others: others + [number])


def dequeued(number):
    Queue.place(number, lambda others: [other for other in others if other != number])


@pytest.fixture
def in_job(desk, home_store, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr("tinkerkit.desk.PROGRESS_EDIT_SECONDS", 1000)
    queued_job(home_store, 4, "learn rust")
    asked = []
    desk.talk.answer = lambda channel, message, *rest, **options: asked.append("talk")
    desk.handle(channel, Message("1", "/jobs #4"))
    return desk, channel, asked


@pytest.mark.spec("CNV-25", "JOB-6")
def test_jobs_of_a_queued_job_enters_it_showing_its_view(in_job):
    desk, channel, _ = in_job
    assert desk.inside == {"1": InsideJob(4, "learn rust")}
    assert ("inside", "1", None, desk.inside["1"]) in channel.log
    shown = channel.texts()[-1]
    assert "#4 running learn rust" in shown and "`/leave` leaves it" in shown


@pytest.mark.spec("CNV-25")
def test_inside_a_job_plain_messages_and_commands_are_answered_as_outside(in_job):
    desk, channel, asked = in_job
    desk.handle(channel, Message("1", "hello"))
    desk.handle(channel, Message("1", "/read list"))
    assert asked == ["talk"] and desk.ran[-1] == ["read", "list"] and "1" in desk.inside


@pytest.mark.spec("CNV-25")
def test_leave_leaves_the_job(in_job):
    desk, channel, _ = in_job
    desk.handle(channel, Message("1", "/leave"))
    assert channel.texts()[-1] == "⚙️ **Left #4 learn rust**" and desk.inside == {}


@pytest.mark.spec("CNV-25", "JOB-6")
def test_jobs_of_a_job_not_in_the_queue_shows_its_view_once(desk, home_store):
    desk, channel, _ = desk
    put_job(home_store, 2, title="learn go", state="finished")
    desk.handle(channel, Message("1", "/jobs #2"))
    assert desk.inside == {} and "#2 finished learn go" in channel.texts()[-1]


@pytest.mark.spec("CNV-25")
def test_jobs_of_an_unknown_job_says_so(desk):
    desk, channel, _ = desk
    desk.handle(channel, Message("1", "/jobs #9"))
    assert desk.inside == {} and channel.texts()[-1].startswith("⚙️ **No job #9**")


@pytest.mark.spec("CNV-25")
def test_other_jobs_commands_run_as_before(desk):
    desk, channel, _ = desk
    desk.handle(channel, Message("1", "/jobs stopped"))
    assert desk.ran[-1] == ["jobs", "stopped"] and desk.inside == {}


@pytest.mark.spec("CNV-25")
def test_a_job_left_the_queue_is_left_on_the_next_message(in_job):
    desk, channel, asked = in_job
    dequeued(4)
    desk.handle(channel, Message("1", "hello"))
    assert desk.inside == {} and asked == ["talk"]


def following(desk, channel):
    return JobFollower(desk, channel, "1", desk.inside["1"], channel.newest["1"])


@pytest.mark.spec("CNV-25")
def test_entering_a_job_offers_its_leave_button(in_job):
    desk, channel, _ = in_job
    assert ("leave", "1", channel.newest["1"], "⚙️ Leave #4") in channel.log


@pytest.mark.spec("CNV-25", "JOB-6")
def test_a_followed_job_message_is_edited_when_its_view_changes(in_job, home_store):
    desk, channel, _ = in_job
    follower, entered = following(desk, channel), channel.newest["1"]
    before = len(channel.log)
    assert follower.step() and len(channel.log) == before
    Log(home_store, "output #4").add({"line": "reading the rust book"})
    assert follower.step()
    edited = [entry for entry in channel.log[before:] if entry[0] == "edit"]
    assert [entry[2] for entry in edited] == [entered]
    assert "reading the rust book" in edited[0][3] and "`/leave` leaves it" in edited[0][3]
    assert channel.log[-1] == ("leave", "1", entered, "⚙️ Leave #4")


@pytest.mark.spec("CNV-25", "JOB-6")
def test_a_followed_job_leaving_the_queue_shows_how_it_ended_and_leaves(in_job, home_store):
    desk, channel, _ = in_job
    follower, sent = following(desk, channel), len([entry for entry in channel.log if entry[0] == "send"])
    put_job(home_store, 4, title="learn rust", state="finished")
    Log(home_store, "output #4").add({"line": "reading the rust book"})
    Log(home_store, "output #4").add({"exit": 0})
    dequeued(4)
    assert not follower.step()
    assert desk.inside == {} and ("inside", "1", None, None) in channel.log
    ended = channel.texts()[-1]
    assert "#4 finished learn rust" in ended and "reading the rust book" in ended
    assert "```\nIt ended with exit code 0." in ended
    assert len([entry for entry in channel.log if entry[0] == "send"]) == sent


@pytest.mark.spec("CNV-25", "JOB-6")
def test_a_followed_job_leaving_the_queue_without_an_exit_says_it_stopped(in_job, home_store):
    desk, channel, _ = in_job
    follower = following(desk, channel)
    put_job(home_store, 4, title="learn rust", state="stopped")
    dequeued(4)
    assert not follower.step()
    assert "```\nIt stopped before it ended." in channel.texts()[-1]


@pytest.mark.spec("CNV-25", "JOB-6")
def test_a_followed_job_leaving_the_queue_still_running_reads_ended(in_job, home_store):
    desk, channel, _ = in_job
    follower = following(desk, channel)
    Log(home_store, "output #4").add({"exit": 1})
    dequeued(4)
    assert not follower.step()
    ended = channel.texts()[-1]
    assert "#4 ended learn rust" in ended and "running" not in ended
    assert "```\nIt ended with exit code 1." in ended


@pytest.mark.spec("CNV-25")
def test_a_followed_job_whose_record_is_gone_says_so(in_job, home_store):
    desk, channel, _ = in_job
    follower = following(desk, channel)
    jobs.JOBS.delete(4)
    assert follower.step()
    assert "#4 gone learn rust" in channel.texts()[-1]
    dequeued(4)
    assert not follower.step()
    assert "#4 gone learn rust" in channel.texts()[-1] and desk.inside == {}


@pytest.mark.spec("CNV-25")
def test_a_followed_job_stops_once_the_chat_left_it(in_job):
    desk, channel, _ = in_job
    follower = following(desk, channel)
    desk.handle(channel, Message("1", "/leave"))
    before = len(channel.log)
    assert not follower.step() and len(channel.log) == before


@pytest.mark.spec("CNV-25")
def test_a_followed_job_ending_after_the_chat_left_it_leaves_nothing_else(in_job, home_store):
    desk, channel, _ = in_job
    follower = following(desk, channel)
    dequeued(4)
    desk.handle(channel, Message("1", "/leave"))
    follower.end()
    assert desk.inside == {}
    assert [entry for entry in channel.log if entry[0] == "inside" and entry[3] is None] == [
        ("inside", "1", None, None)
    ]


@pytest.mark.spec("CNV-25")
def test_entering_a_job_leaves_a_reading(inside, home_store, monkeypatch):
    monkeypatch.setattr("tinkerkit.desk.PROGRESS_EDIT_SECONDS", 1000)
    desk, channel, _ = inside
    queued_job(home_store, 4, "learn rust")
    desk.handle(channel, Message("1", "/jobs #4"))
    assert desk.inside == {"1": InsideJob(4, "learn rust")}


@pytest.mark.spec("CNV-25")
def test_entering_the_interview_leaves_a_job(in_job):
    desk, channel, _ = in_job
    editor_question()
    desk.interview = Interview()
    desk.interviewed(channel, "1")
    assert desk.inside["1"] == InsideInterview(desk.interview)


@pytest.mark.spec("CNV-25")
def test_the_terminal_prompt_names_the_job_while_inside(monkeypatch):
    monkeypatch.setattr("tinkerkit.chat.console.typed_here", lambda: True)
    channel = ConsoleChannel()
    channel.now_inside("console", InsideJob(4, "learn rust"))
    assert channel.prompt() == "⚙️ #4 learn rust › "


@pytest.mark.spec("CNV-25")
def test_a_reading_kept_with_no_chapters_is_not_entered(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(Shelf, "entered", lambda self, words: None)
    desk.handle(channel, Message("1", "/read note.txt"))
    assert desk.inside == {} and "📕" not in channel.texts()[-1]


@pytest.mark.spec("RDG-15")
def test_the_reading_list_is_answered_at_once_in_a_chat(desk):
    desk, channel, _ = desk
    assert (
        desk.answer(Message("1", "read list"), channel) == "📚 **read list**\n```\noutput of read list\n```"
    )
    assert desk.answer(Message("1", "read show #2"), channel).startswith("📚 **read show #2**")
    assert desk.ran == [["read", "list"], ["read", "show", "#2"]]


@pytest.mark.spec("RDG-15")
def test_a_reading_is_renamed_at_once_and_forgotten_after_asking_in_a_chat(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk, "tinker", lambda words: desk.ran.append(words) or "✅ #1 Small Craft")
    assert desk.answer(Message("1", "/read rename #1 Small Craft"), channel).startswith(
        "⚙️ **read rename #1 Small Craft**"
    )
    assert desk.answer(Message("1", "/read forget #1"), channel) is None
    assert "👉 `/confirm` to delete it" in channel.texts()[-1]
    say(desk, channel, "/confirm")
    wait_until(lambda: ["read", "forget", "#1", "--yes"] in desk.ran)


@pytest.mark.spec("SET-11")
def test_a_reply_naming_the_next_page_offers_it(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk, "answer", lambda message, channel: "#3 a\nPage 1 of 2: tinker jobs 2 for more.")
    desk.handle(channel, Message("1", "/jobs"))
    assert channel.log[-1] == ("next", "1", channel.newest["1"], "jobs 2")
    monkeypatch.setattr(desk, "answer", lambda message, channel: "#1 a\nPage 2 of 2.")
    desk.handle(channel, Message("1", "/jobs 2"))
    assert all(kind != "next" or shown != channel.newest["1"] for kind, _, shown, _ in channel.log)


@pytest.mark.spec("SET-11")
def test_a_listed_page_of_tinker_output_offers_the_next_page_and_keeps_its_lines(desk, monkeypatch):
    desk, channel, _ = desk
    rows = "".join(f"📘 #{number:<3} Lighthouse Keeping    3 days ago\n" for number in range(12, 2, -1))
    output = rows + "👉 Page 1 of 2: tinker read list 2 for more.\n"
    monkeypatch.delattr(desk, "tinker")
    monkeypatch.setattr(
        desk, "run_tinker", lambda arguments, timeout, env: subprocess.CompletedProcess([], 0, output, "")
    )
    desk.handle(channel, Message("1", "/read list"))
    assert channel.log[-1] == ("next", "1", channel.newest["1"], "read list 2")
    assert " | " not in channel.log[-2][3]


@pytest.mark.spec("SET-11")
def test_a_reply_told_after_a_question_also_offers_the_next_page(desk, monkeypatch):
    desk, channel, _ = desk
    monkeypatch.setattr(desk, "answer", lambda message, channel: "#3 a\nPage 1 of 2: tinker jobs 2 for more.")
    monkeypatch.setattr(desk, "told", lambda channel, message, reply, asked: reply)
    desk.handle(channel, Message("1", "/jobs"), asked=[])
    assert channel.log[-1] == ("next", "1", channel.newest["1"], "jobs 2")


@pytest.mark.spec("SET-11")
def test_history_pages_end_with_the_shared_next_page_line(monkeypatch):
    monkeypatch.setattr(
        desk_module.Ledger,
        "page",
        lambda page: ([], 3) if page > 3 else ([{"number": 1, "time": "2026-11-12 10:00", "text": "hi"}], 3),
    )
    assert next_page_words(desk_module.history_text(["2"])) == "history 3"
    assert next_page_words(desk_module.history_text(["3"])) is None


@pytest.mark.spec("ARCH-2")
def test_the_enabled_chats_come_from_the_chat_channels_module(monkeypatch):
    enabled = object()
    monkeypatch.setattr(channels.Channels, "enabled", staticmethod(lambda: enabled))
    assert desk_module.chat_channels() is enabled


@pytest.mark.spec("SET-1")
def test_a_direct_tinker_run_shares_the_home(tinker_home, monkeypatch):
    ran = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **options: (
            ran.append(options["env"]) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    monkeypatch.delenv("TINKER_HOME")
    Desk.run_tinker(types.SimpleNamespace(root=tinker_home), ["status"], 5)
    desk_module.help_text()
    assert [env["TINKER_HOME"] for env in ran] == [str(tinker_home)] * 2
    assert all(env["NO_COLOR"] == "1" for env in ran)
