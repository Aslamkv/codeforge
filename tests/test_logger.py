import os
import re
import socket
import threading
import zipfile
from datetime import date
from types import SimpleNamespace

import pytest

from tinkerkit import channels, logger
from tinkerkit.building import CodeWriter, Verifier
from tinkerkit.channels import TelegramChannel
from tinkerkit.conversation import CHAT_APP_START, Desk, Talk
from tinkerkit.finishing import Tidy
from tinkerkit.logger import TraceLog
from tinkerkit.messages import say
from tinkerkit.model import Ollama
from tinkerkit.planning import Recaller
from tinkerkit.session import Phase
from tinkerkit.settings import Config, Home
from tinkerkit.store import Store

PREFIX = re.compile(r"\d\d:\d\d:\d\d\.\d{3} \[\d+\] ")


def lines():
    log = Home.tinker_log()
    return log.read_text().splitlines() if log.exists() else []


def sentences():
    return [
        PREFIX.sub("", line, count=1)
        for line in lines()
        if not line.startswith("── ") and " Tinker is starting: " not in line
    ]


def day_line():
    return f"── {date.today().isoformat()} ──"


@pytest.fixture(autouse=True)
def fresh_day(monkeypatch):
    monkeypatch.setattr(TraceLog, "day", None)


def unwritable_home(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(Home, "root", blocker / "home")


def archives():
    return sorted(Home.root.glob("tinker-*.log.zip"))


@pytest.mark.spec("LOG-1")
def test_write_appends_one_line_per_text_after_the_day_line():
    TraceLog.write("first")
    TraceLog.write("second")
    assert lines() == [day_line(), "first", "second"]


@pytest.mark.spec("LOG-1")
def test_the_day_line_comes_again_on_a_new_day(monkeypatch):
    TraceLog.write("first")
    monkeypatch.setattr(TraceLog, "day", "2000-01-01")
    TraceLog.write("second")
    assert lines() == [day_line(), "first", day_line(), "second"]


@pytest.mark.spec("LOG-1")
def test_write_asks_home_for_the_path_each_time(tmp_path, monkeypatch):
    TraceLog.write("first")
    other = tmp_path / "other-home"
    monkeypatch.setattr(Home, "root", other)
    TraceLog.write("second")
    assert lines() == ["second"]


@pytest.mark.spec("LOG-1")
def test_threads_never_mix_lines():
    text = "x" * 5000

    def work():
        for _ in range(20):
            TraceLog.write(text)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert lines() == [day_line()] + [text] * 160


@pytest.mark.spec("LOG-2")
def test_a_big_log_is_zipped_and_a_new_log_started(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 100)
    TraceLog.write("x" * 200)
    TraceLog.write("after")
    assert lines() == ["after"]
    [archive] = archives()
    assert archive.name.startswith("tinker-") and archive.name.endswith(".log.zip")
    with zipfile.ZipFile(archive) as packed:
        [name] = packed.namelist()
        assert packed.read(name).decode().splitlines() == [day_line(), "x" * 200]
    assert sorted(path.name for path in Home.root.iterdir()) == [archive.name, "tinker.log"]


@pytest.mark.spec("LOG-2")
def test_a_small_log_is_not_rotated(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 10_000)
    TraceLog.write("a")
    TraceLog.write("b")
    assert archives() == []
    assert lines() == [day_line(), "a", "b"]


@pytest.mark.spec("LOG-2")
def test_a_lost_rename_skips_the_rotation(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 10)
    TraceLog.write("x" * 50)

    def lost(source, target):
        raise FileNotFoundError(source)

    monkeypatch.setattr(logger.os, "rename", lost)
    TraceLog.write("after")
    assert archives() == []
    assert lines() == [day_line(), "x" * 50, "after"]


@pytest.mark.spec("LOG-2")
def test_the_stamp_names_time_and_process(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 10)
    monkeypatch.setattr(logger.os, "getpid", lambda: 4242)
    monkeypatch.setattr(logger.time, "strftime", lambda pattern: "20261111-120000")
    TraceLog.write("x" * 50)
    TraceLog.write("after")
    assert [path.name for path in archives()] == ["tinker-20261111-120000-4242.log.zip"]


@pytest.mark.spec("LOG-6")
def test_a_failing_write_never_raises(tmp_path, monkeypatch):
    unwritable_home(tmp_path, monkeypatch)
    TraceLog.write("dropped")


class Inner:
    def run(self, value, scale=1):
        return value * scale

    def same(self, value):
        return value

    def fail(self, message):
        raise ValueError(message)


class Outer:
    def run(self, inner):
        return inner.run(2, scale=3)

    @staticmethod
    def shout(text):
        return text.upper()


class Unprintable:
    def __repr__(self):
        raise RuntimeError("no repr")


class Planner(Phase):
    def run(self, cycle):
        return "planned"


class Broken(Phase):
    def run(self, cycle):
        raise ValueError("no plan")


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(
        logger,
        "WATCHED",
        (
            logger.Watched(
                __name__,
                "Inner",
                "run",
                logger.Step(
                    lambda a: "Inner",
                    lambda a: f"Inner runs {a.value!r} x{a.scale}",
                    lambda a, result: f"Inner ran {result!r}",
                ),
            ),
            logger.Watched(
                __name__,
                "Inner",
                "fail",
                logger.Step(
                    lambda a: f"Inner fail {a.message!r}",
                    lambda a: f"Inner fails {a.message!r}",
                    lambda a, result: "Inner failed not",
                ),
            ),
            logger.Watched(
                __name__, "Inner", "same", logger.Fact(lambda a: "Same", lambda a, result: f"Same {result!r}")
            ),
            logger.Watched(
                __name__,
                "Outer",
                "run",
                logger.Step(
                    lambda a: "Outer",
                    lambda a: f"Outer runs {a.inner!r}",
                    lambda a, result: f"Outer ran {result!r}",
                ),
            ),
            logger.Watched(
                __name__,
                "Outer",
                "shout",
                logger.Fact(lambda a: "Shout", lambda a, result: f"Shout {a.text!r}: {result!r}"),
            ),
        ),
    )
    logger.install()
    Home.tinker_log().unlink()
    TraceLog.day = None


@pytest.mark.spec("LOG-3")
def test_watched_names_every_watched_method():
    named = {f"{watched.owner}.{watched.method}" for watched in logger.WATCHED}
    assert named == {
        "Commands.run",
        "ProjectRun.execute",
        "Phase.run",
        "Model.ask",
        "Ollama.chat",
        "Workspace.run",
        "Workspace.start",
        "Internet.search_all",
        "Internet.fetch",
        "Store.get",
        "Store.put",
        "Store.append",
        "Ollama.loading",
        "Talk.warm",
        "channels.connect_by_address",
        "Desk.run",
        "Desk.handle",
        "TelegramChannel.send",
        "Desk.close_channels",
        "Desk.unload_models",
    }


@pytest.mark.spec("LOG-3")
def test_install_wraps_the_run_of_every_phase():
    plain = {cls: cls.__dict__["run"] for cls in (CodeWriter, Verifier, Tidy, Recaller, Phase)}
    logger.install()
    assert all(cls.__dict__["run"] is not plain[cls] for cls in (CodeWriter, Verifier, Tidy, Recaller))
    assert Phase.__dict__["run"] is plain[Phase]


@pytest.mark.spec("LOG-4")
def test_install_twice_wraps_once_and_uninstall_restores():
    get, chat = Store.__dict__["get"], Ollama.__dict__["chat"]
    logger.install()
    wrapped = Store.__dict__["get"]
    logger.install()
    assert Store.__dict__["get"] is wrapped is not get
    assert isinstance(Ollama.__dict__["chat"], staticmethod)
    logger.uninstall()
    assert Store.__dict__["get"] is get and Ollama.__dict__["chat"] is chat


@pytest.mark.spec("LOG-5")
def test_install_twice_starts_follows_and_registers_the_exit_once(monkeypatch):
    registered = exit_hooks(monkeypatch)
    followers = list(say.followers)
    logger.install()
    logger.install()
    assert len([line for line in lines() if "Tinker is starting: " in line]) == 1
    assert len(say.followers) == len(followers) + 1
    assert len(registered) == 1
    logger.uninstall()
    assert say.followers == followers and registered == []


def exit_hooks(monkeypatch):
    registered = []
    monkeypatch.setattr(logger.atexit, "register", registered.append)
    monkeypatch.setattr(
        logger.atexit, "unregister", lambda hook: registered.remove(hook) if hook in registered else None
    )
    return registered


@pytest.mark.spec("LOG-5")
def test_install_writes_the_start_line_with_the_arguments(monkeypatch):
    exit_hooks(monkeypatch)
    monkeypatch.setattr(logger.sys, "argv", ["tinker", "make", "a game"])
    logger.install()
    assert [PREFIX.sub("", line, count=1) for line in lines()] == [
        day_line(),
        "Tinker is starting: tinker make a game",
    ]


@pytest.mark.spec("LOG-5")
def test_each_said_message_is_written(monkeypatch, capsys):
    exit_hooks(monkeypatch)
    logger.install()
    say.warn("No network")
    assert sentences() == ["Said: No network"]
    logger.uninstall()
    say.warn("Still no network")
    assert sentences() == ["Said: No network"]


@pytest.mark.spec("LOG-5")
def test_the_exit_writes_the_stop_line_until_uninstalled(monkeypatch):
    registered = exit_hooks(monkeypatch)
    logger.install()
    (stop,) = registered
    stop()
    assert sentences() == ["Tinker stopped"]
    logger.uninstall()
    assert registered == []


@pytest.mark.spec("LOG-3")
@pytest.mark.spec("LOG-4")
@pytest.mark.spec("LOG-5")
def test_a_real_store_call_returns_as_before_and_is_logged(tmp_path):
    logger.install()
    store = Store(tmp_path / "s.db")
    store.put("c", "k", {"a": 1})
    assert store.get("c", "k") == {"a": 1}
    assert store.get("c", "missing") is None
    put, found, missing = sentences()
    assert re.fullmatch(r"Database put c/'k': \{'a': 1\} \(\d+\.\d{3}s\)", put)
    assert re.fullmatch(r"Database get c/'k': found \{'a': 1\} \(\d+\.\d{3}s\)", found)
    assert re.fullmatch(r"Database get c/'missing': not found \(\d+\.\d{3}s\)", missing)


def started_and_ended(start, end):
    first, last = sentences()
    assert first == start
    assert re.fullmatch(rf"{re.escape(end)} \(\d+\.\d{{3}}s\)", last)


@pytest.mark.spec("LOG-4")
def test_a_step_writes_a_start_and_an_end_line():
    logger.install()
    assert Planner.__new__(Planner).run({"id": "base"}) == "planned"
    started_and_ended("Phase Planner starts (cycle base)", "Phase Planner ends")


@pytest.mark.spec("LOG-4")
def test_a_step_that_raises_ends_with_its_failure():
    logger.install()
    with pytest.raises(ValueError):
        Broken.__new__(Broken).run({"id": 2})
    started_and_ended("Phase Broken starts (cycle 2)", "Phase Broken failed: ValueError: no plan")


@pytest.mark.spec("LOG-4")
def test_loading_a_model_is_a_step(monkeypatch):
    monkeypatch.setattr(Ollama, "loaded_models", staticmethod(lambda: []))
    logger.install()
    Ollama.loading("qwen", lambda text: None)
    started_and_ended("Loading model qwen", "Model qwen loaded")


@pytest.mark.spec("LOG-4")
def test_warming_the_chat_model_is_a_step():
    logger.install()
    talk = Talk.__new__(Talk)
    talk.load = lambda: None
    talk.warm()
    started_and_ended("Loading the chat model", "Chat model loading started")


@pytest.mark.spec("LOG-4")
def test_connecting_tells_the_host_and_the_address_reached_by_ip_version(monkeypatch):
    sock = SimpleNamespace(
        family=socket.AF_INET6, getpeername=lambda: ("::1", 443, 0, 0), settimeout=lambda wait: None
    )
    monkeypatch.setattr(
        channels, "addresses_by_family", lambda host, port, family: [(None, ("::1", 443, 0, 0))]
    )
    monkeypatch.setattr(channels, "shake_hands", lambda context, host, address: sock)
    logger.install()
    assert channels.connect_by_address(("api.example.org", 443), None, 5) is sock
    started_and_ended("Connecting to api.example.org", "Connected to api.example.org at ::1 by IPv6")


@pytest.mark.spec("LOG-4")
def test_a_conversation_that_raises_ends_with_its_failure(monkeypatch):
    def broken(self):
        raise OSError("no chats")

    monkeypatch.setattr(Desk, "claim_chats", broken)
    logger.install()
    with pytest.raises(OSError):
        Desk.__new__(Desk).run()
    started_and_ended("Conversation starts", "Conversation failed: OSError: no chats")


@pytest.mark.spec("LOG-4")
def test_handling_a_message_tells_the_channel_and_what_was_asked():
    channel = SimpleNamespace(send=lambda chat, text: None)
    logger.install()
    Desk.__new__(Desk).handle(channel, SimpleNamespace(text=CHAT_APP_START, chat=1))
    started_and_ended(
        f"Handling a SimpleNamespace message: {CHAT_APP_START!r}", "SimpleNamespace message handled"
    )


@pytest.mark.spec("LOG-4")
def test_sending_to_telegram_tells_the_chat_and_the_text():
    channel = TelegramChannel.__new__(TelegramChannel)
    channel.post = lambda method, chat, text, **fields: {"message_id": 7}
    channel.seen = lambda chat, sent: None
    logger.install()
    assert channel.send(42, "hello") == 7
    started_and_ended("Sending to Telegram chat 42: 'hello'", "Telegram chat 42 got message 7")


@pytest.mark.spec("LOG-4")
def test_closing_the_channels_is_a_step():
    desk = Desk.__new__(Desk)
    desk.channels = [SimpleNamespace()]
    logger.install()
    desk.close_channels()
    started_and_ended("Closing the channels", "Channels closed")


@pytest.mark.spec("LOG-4")
def test_unloading_the_models_is_a_step(monkeypatch):
    monkeypatch.setattr(Config, "get", lambda key: None)
    logger.install()
    Desk.unload_models()
    started_and_ended("Unloading the models", "Models unloaded")


@pytest.mark.spec("LOG-4")
def test_a_newline_in_a_sentence_stays_on_its_line():
    logger.install()
    Planner.__new__(Planner).run({"id": "a\nb"})
    assert sentences()[0] == "Phase Planner starts (cycle a\\nb)"


@pytest.mark.spec("LOG-3")
@pytest.mark.spec("LOG-5")
def test_a_line_has_time_process_and_indent_per_depth(fakes):
    assert Outer().run(Inner()) == 6
    day, outer_start, inner_start, inner_end, outer_end = lines()
    prefix = rf"\d\d:\d\d:\d\d\.\d{{3}} \[{os.getpid()}\] "
    assert day == day_line()
    assert re.fullmatch(prefix + r"Outer runs <.*>", outer_start)
    assert re.fullmatch(prefix + r"  Inner runs 2 x3", inner_start)
    assert re.fullmatch(prefix + r"  Inner ran 6 \(\d+\.\d{3}s\)", inner_end)
    assert re.fullmatch(prefix + r"Outer ran 6 \(\d+\.\d{3}s\)", outer_end)


@pytest.mark.spec("LOG-5")
def test_a_static_method_logs_all_its_arguments(fakes):
    assert Outer.shout("hi") == "HI"
    (end,) = sentences()
    assert re.fullmatch(r"Shout 'hi': 'HI' \(\d+\.\d{3}s\)", end)


@pytest.mark.spec("LOG-3")
@pytest.mark.spec("LOG-4")
@pytest.mark.spec("LOG-5")
def test_an_error_is_raised_as_before_and_logged(fakes):
    with pytest.raises(ValueError, match="broken"):
        Inner().fail("broken")
    start, end = sentences()
    assert start == "Inner fails 'broken'"
    assert re.fullmatch(r"Inner fail 'broken' failed: ValueError: broken \(\d+\.\d{3}s\)", end)
    assert logger.depth.level == 0


@pytest.mark.spec("LOG-3")
def test_values_are_given_in_full(fakes):
    Inner().run("x" * 500)
    start, end = sentences()
    assert start == f"Inner runs {'x' * 500!r} x1"
    assert end.startswith(f"Inner ran {'x' * 500!r} (")


@pytest.mark.spec("LOG-6")
def test_a_failing_line_keeps_the_result(fakes):
    value = Unprintable()
    assert Inner().same(value) is value
    assert lines() == []


@pytest.mark.spec("LOG-6")
def test_a_failing_write_keeps_the_result(fakes, tmp_path, monkeypatch):
    unwritable_home(tmp_path, monkeypatch)
    assert Inner().run(4) == 4
    with pytest.raises(ValueError):
        Inner().fail("still raised")
