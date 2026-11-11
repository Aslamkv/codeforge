import json
import os
import re
import threading
import zipfile

import pytest

from tinkerkit import logger
from tinkerkit.building import CodeWriter, Verifier
from tinkerkit.finishing import Tidy
from tinkerkit.logger import TraceLog
from tinkerkit.model import Ollama
from tinkerkit.planning import Recaller
from tinkerkit.session import Phase
from tinkerkit.settings import Home
from tinkerkit.store import Store


def lines():
    log = Home.tinker_log()
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def unwritable_home(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(Home, "root", blocker / "home")


def archives():
    return sorted(Home.root.glob("tinker-*.log.zip"))


@pytest.mark.spec("LOG-1")
def test_write_appends_one_json_line_per_entry():
    TraceLog.write({"call": "a", "n": 1})
    TraceLog.write({"call": "b", "text": "two\nlines"})
    assert lines() == [{"call": "a", "n": 1}, {"call": "b", "text": "two\nlines"}]


@pytest.mark.spec("LOG-1")
def test_write_asks_home_for_the_path_each_time(tmp_path, monkeypatch):
    TraceLog.write({"call": "first"})
    other = tmp_path / "other-home"
    monkeypatch.setattr(Home, "root", other)
    TraceLog.write({"call": "second"})
    assert lines() == [{"call": "second"}]


@pytest.mark.spec("LOG-1")
def test_threads_never_mix_lines():
    entry = {"text": "x" * 5000}

    def work():
        for _ in range(20):
            TraceLog.write(entry)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert lines() == [entry] * 160


@pytest.mark.spec("LOG-2")
def test_a_big_log_is_zipped_and_a_new_log_started(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 100)
    TraceLog.write({"text": "x" * 200})
    TraceLog.write({"call": "after"})
    assert lines() == [{"call": "after"}]
    [archive] = archives()
    assert archive.name.startswith("tinker-") and archive.name.endswith(".log.zip")
    with zipfile.ZipFile(archive) as packed:
        [name] = packed.namelist()
        assert json.loads(packed.read(name)) == {"text": "x" * 200}
    assert sorted(path.name for path in Home.root.iterdir()) == [archive.name, "tinker.log"]


@pytest.mark.spec("LOG-2")
def test_a_small_log_is_not_rotated(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 10_000)
    TraceLog.write({"call": "a"})
    TraceLog.write({"call": "b"})
    assert archives() == []
    assert len(lines()) == 2


@pytest.mark.spec("LOG-2")
def test_a_lost_rename_skips_the_rotation(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 10)
    TraceLog.write({"text": "x" * 50})

    def lost(source, target):
        raise FileNotFoundError(source)

    monkeypatch.setattr(logger.os, "rename", lost)
    TraceLog.write({"call": "after"})
    assert archives() == []
    assert lines() == [{"text": "x" * 50}, {"call": "after"}]


@pytest.mark.spec("LOG-2")
def test_the_stamp_names_time_and_process(monkeypatch):
    monkeypatch.setattr(logger, "LOG_ROTATE_BYTES", 10)
    monkeypatch.setattr(logger.os, "getpid", lambda: 4242)
    monkeypatch.setattr(logger.time, "strftime", lambda pattern: "20261111-120000")
    TraceLog.write({"text": "x" * 50})
    TraceLog.write({"call": "after"})
    assert [path.name for path in archives()] == ["tinker-20261111-120000-4242.log.zip"]


@pytest.mark.spec("LOG-6")
def test_a_failing_write_never_raises(tmp_path, monkeypatch):
    unwritable_home(tmp_path, monkeypatch)
    TraceLog.write({"call": "dropped"})


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


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(
        logger,
        "WATCHED",
        (
            logger.Watched(__name__, "Inner", "run"),
            logger.Watched(__name__, "Inner", "fail"),
            logger.Watched(__name__, "Inner", "same"),
            logger.Watched(__name__, "Outer", "run"),
            logger.Watched(__name__, "Outer", "shout"),
        ),
    )
    logger.install()


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


@pytest.mark.spec("LOG-4")
@pytest.mark.spec("LOG-5")
def test_a_real_store_call_returns_as_before_and_is_logged(tmp_path):
    logger.install()
    store = Store(tmp_path / "s.db")
    store.put("c", "k", {"a": 1})
    assert store.get("c", "k") == {"a": 1}
    put, get = lines()
    assert (put["call"], put["args"], put["result"]) == ("Store.put", ["'c'", "'k'", "{'a': 1}"], "None")
    assert (get["call"], get["result"], get["depth"]) == ("Store.get", "{'a': 1}", 0)


@pytest.mark.spec("LOG-5")
def test_an_entry_has_time_process_call_seconds_depth_args_and_result(fakes):
    assert Outer().run(Inner()) == 6
    inner, outer = lines()
    assert inner["call"] == "Inner.run" and inner["depth"] == 1
    assert inner["args"] == ["2", "scale=3"] and inner["result"] == "6"
    assert outer["call"] == "Outer.run" and outer["depth"] == 0
    assert outer["args"][0].startswith("<") and outer["result"] == "6"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}", outer["at"])
    assert outer["at"] <= inner["at"] and outer["pid"] == os.getpid()
    assert outer["seconds"] >= inner["seconds"] >= 0 and outer["seconds"] == round(outer["seconds"], 4)


@pytest.mark.spec("LOG-5")
def test_a_static_method_logs_all_its_arguments(fakes):
    assert Outer.shout("hi") == "HI"
    [entry] = lines()
    assert (entry["call"], entry["args"], entry["result"]) == ("Outer.shout", ["'hi'"], "'HI'")


@pytest.mark.spec("LOG-4")
@pytest.mark.spec("LOG-5")
def test_an_error_is_raised_as_before_and_logged(fakes):
    with pytest.raises(ValueError, match="broken"):
        Inner().fail("broken")
    [entry] = lines()
    assert entry["error"] == "ValueError: broken" and "result" not in entry
    assert logger.depth.level == 0


@pytest.mark.spec("LOG-5")
def test_values_are_cut_to_the_limit(fakes):
    Inner().run("x" * 500)
    [entry] = lines()
    assert entry["args"] == [repr("x" * 500)[: logger.LOG_VALUE_CHARS]]
    assert len(entry["result"]) == logger.LOG_VALUE_CHARS


@pytest.mark.spec("LOG-6")
def test_a_failing_entry_keeps_the_result(fakes):
    value = Unprintable()
    assert Inner().same(value) is value
    assert lines() == []


@pytest.mark.spec("LOG-6")
def test_a_failing_write_keeps_the_result(fakes, tmp_path, monkeypatch):
    unwritable_home(tmp_path, monkeypatch)
    assert Inner().run(4) == 4
    with pytest.raises(ValueError):
        Inner().fail("still raised")
