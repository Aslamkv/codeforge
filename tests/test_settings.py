"""SET: Tinker's settings, its home folder and the small helpers every part uses."""

import io
import multiprocessing
import re
import stat
import sys
from pathlib import Path

import pytest

import tinkerkit.settings as settings
from tinkerkit.settings import (
    Config, Console, Home, Store, Tee, after_what_they_use, first_line, fingerprint, groups_fitting,
    human_duration, similarity, split_text, words,
)
from conftest import REAL_HOME


@pytest.mark.spec("SET-1")
def test_home_is_dot_tinker_next_to_the_real_script(tinker_home):
    assert REAL_HOME == Path(settings.__file__).resolve().parent.parent / ".tinker"
    assert Home.root == tinker_home and Home.store().path == tinker_home / "tinker.db"


@pytest.mark.spec("SET-2")
def test_default_config():
    assert settings.DEFAULT_CONFIG == {
        "model": None, "ollama_url": "http://localhost:11434", "keep_alive": 0, "max_growth": 1.15,
        "build_think": "medium", "test_timeout": 60, "web": "ollama", "web_fallback": "", "web_timeout": 30,
        "learn_missing": 2, "learn_rate": 8, "learn_backoff": 20, "learn_cutoff": 0.0, "learn_pages": 8,
        "learn_min_pages": 2, "learn_searches": 3, "learn_think": "low", "docs_pages": 30, "tool_docs_pages": 5,
        "docs_latest_days": 30, "fetch_workers": 6, "fetch_timeout": 15, "fetch_max_bytes": 2000000,
        "fetch_domain_delay": 0.1, "fetch_fallbacks": 2, "embed_model": "", "projects": "~/Projects"}


@pytest.mark.spec("SET-3")
def test_phase_lists():
    assert settings.PHASES == ("recall", "spec", "design", "review", "setup", "tests", "code", "verify", "tidy",
                               "docs", "final_review", "lessons", "done")
    assert settings.FIX_PHASES == ("recall", "reproduce", "verify", "final_review", "lessons", "done")
    assert settings.PLANNING_PHASES == ("recall", "spec", "design", "review")
    assert settings.CONFIRM_AFTER == {"feature": "review", "fix": "reproduce"}


@pytest.mark.spec("SET-4")
def test_config_is_defaults_overridden_by_the_home(home_store):
    home_store.put("config", "model", "m1")
    assert Config.get("model") == "m1" and Config.get("web") == "ollama"
    home_store.put("config", "model", "changed")  # another Tinker changed it: seen at once
    assert Config.get("model") == "changed"
    Config.save(web="duckduckgo", test_timeout=5)
    assert Config.load() == dict(settings.DEFAULT_CONFIG, model="changed", web="duckduckgo", test_timeout=5)


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("key,value", [("keep_alive", "soon"), ("keep_alive", "10 m"), ("max_growth", 1),
                                       ("max_growth", True), ("max_growth", "2"), ("build_think", "max"),
                                       ("build_think", "true"), ("build_think", 1), ("learn_think", "max")])
def test_check_model_rejects_unusable_settings(key, value):
    Config.save(**{key: value})
    with pytest.raises(SystemExit, match=key):
        Config.check_model(run=False)


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("value", [0, 300, -1, "10m", "1h30m", "500ms"])
def test_check_model_accepts_keep_alive_seconds_or_durations(value):
    Config.save(keep_alive=value)
    Config.check_model(run=True)


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("value", [0, True, 1.5, "60"])
def test_test_timeout_is_checked_for_a_run_only(value):
    Config.save(test_timeout=value)
    Config.check_model(run=False)
    with pytest.raises(SystemExit, match="test_timeout"):
        Config.check_model(run=True)


@pytest.mark.spec("SET-6")
def test_keep_alive_and_ollama_url_as_ollama_takes_them():
    assert Config.keep_alive("300") == 300 and Config.keep_alive(-1) == -1
    assert Config.keep_alive("10m") == "10m"
    Config.save(ollama_url="http://host:1/")
    assert Config.ollama_url() == "http://host:1"


@pytest.mark.spec("SET-7")
def test_store_documents(tmp_path):
    store = Store(tmp_path / "a" / "x.db")
    assert store.get("c", "k") is None and store.get("c", "k", {"d": 1}) == {"d": 1}
    store.put("c", "b", {"name": "café", "n": [1]})
    store.put("c", "a", 2)
    store.put("c", "b", {"name": "again"})  # replaced, keeping its place
    store.put("other", "z", 3)
    assert store.items("c") == {"b": {"name": "again"}, "a": 2} and store.count("c") == 2
    store.delete("c", "b")
    assert store.items("c") == {"a": 2}
    store.delete("c")
    assert store.items("c") == {} and store.get("other", "z") == 3


def add_many(path, times):
    store = Store(path)
    for _ in range(times):
        store.update("c", "n", lambda n: n + 1, 0)


@pytest.mark.spec("SET-7")
def test_store_updates_from_two_processes_lose_nothing(tmp_path):
    path = tmp_path / "x.db"
    Store(path).put("c", "n", 0)
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=add_many, args=(path, 50)) for _ in range(2)]
    for worker in workers:
        worker.start()
    add_many(path, 50)
    for worker in workers:
        worker.join(30)
    assert Store(path).get("c", "n") == 150


@pytest.mark.spec("SET-7")
def test_a_failed_write_leaves_the_old_value(tmp_path):
    store = Store(tmp_path / "x.db")
    store.put("c", "k", "old")

    def broken(value):
        raise RuntimeError("crash in the middle")
    with pytest.raises(RuntimeError):
        store.update("c", "k", broken)
    assert store.get("c", "k") == "old"
    with pytest.raises(TypeError):
        store.put("c", "k", object())  # not JSON: nothing written
    assert store.get("c", "k") == "old"


@pytest.mark.spec("SET-8")
def test_store_logs_and_mode(tmp_path):
    store = Store(tmp_path / "x.db")
    first = store.append("log", {"label": "one"})
    store.append("log", {"label": "two", "x": "é"})
    store.append("other", {"label": "no"})
    entries = store.entries("log")
    assert [e["label"] for _, e in entries] == ["one", "two"] and all("time" in e for _, e in entries)
    assert [e["label"] for _, e in store.entries("log", after=first)] == ["two"]
    assert store.logged("log") == 2
    store.drop_log("log")
    assert store.entries("log") == [] and store.logged("other") == 1
    assert stat.S_IMODE((tmp_path / "x.db").stat().st_mode) == 0o600


@pytest.mark.spec("SET-8")
def test_a_deleted_database_is_made_again(tmp_path):
    store = Store(tmp_path / "x.db")
    store.put("c", "k", 1)
    (tmp_path / "x.db").unlink()
    assert store.get("c", "k") is None
    store.put("c", "k", 2)
    assert store.get("c", "k") == 2


@pytest.mark.spec("SET-9")
def test_ensure_makes_home_and_its_database(tinker_home, capsys):
    assert not tinker_home.exists()
    Home.ensure()
    assert (tinker_home / "tinker.db").is_file() and "created" in capsys.readouterr().out
    Home.ensure()
    assert capsys.readouterr().out == ""


@pytest.mark.spec("SET-9")
def test_ensure_stops_when_home_cannot_be_written(tinker_home, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("read-only")
    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(SystemExit, match="cannot be written"):
        Home.ensure()


@pytest.mark.spec("SET-10")
def test_reset_with_yes_deletes_home(tinker_home):
    Home.ensure()
    Home.reset(yes=True)
    assert not tinker_home.exists()
    Config.save(model="m")  # the next command starts fresh
    assert Config.get("model") == "m"


@pytest.mark.spec("SET-10")
def test_reset_without_home_does_nothing(tinker_home, capsys):
    Home.reset(yes=True)
    assert "Nothing to reset" in capsys.readouterr().out


@pytest.mark.spec("SET-10")
def test_reset_refuses_a_folder_not_named_dot_tinker(tmp_path, monkeypatch):
    other = tmp_path / "precious"
    other.mkdir()
    monkeypatch.setattr(Home, "root", other)
    with pytest.raises(SystemExit, match="not a .tinker folder"):
        Home.reset(yes=True)
    assert other.exists()


@pytest.mark.spec("SET-10")
def test_reset_outside_a_terminal_without_yes_deletes_nothing(tinker_home, monkeypatch):
    Home.ensure()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="Nothing deleted"):
        Home.reset()
    assert tinker_home.exists()


@pytest.mark.spec("SET-11")
def test_parts_tinker_clear_knows():
    assert list(Home.parts()) == ["languages", "memory", "learned", "docs", "models", "runs", "commands"]


@pytest.mark.spec("SET-11")
def test_clear_deletes_only_the_named_parts(home_store, capsys):
    home_store.put("languages", "python", {})
    home_store.put("memory", "any", {})
    home_store.put("models", "m", {})
    home_store.append("learn", {"label": "x"})
    home_store.put("learned", "flask", {})
    Home.clear(["languages", "memory", "learned"], yes=True)
    assert home_store.items("languages") == {} and home_store.items("memory") == {}
    assert home_store.items("learned") == {} and home_store.entries("learn") == []
    assert home_store.items("models") == {"m": {}}
    assert "Cleared languages, memory, learned." in capsys.readouterr().out


@pytest.mark.spec("SET-11")
def test_clear_rejects_an_unknown_part():
    with pytest.raises(SystemExit, match="the parts are: languages"):
        Home.clear(["everything"], yes=True)


@pytest.mark.spec("SET-11")
def test_clear_with_nothing_there(capsys):
    Home.clear(["runs"], yes=True)
    assert "Nothing to clear: runs is empty" in capsys.readouterr().out


@pytest.mark.spec("SET-11")
def test_clear_outside_a_terminal_without_yes_deletes_nothing(home_store, monkeypatch):
    home_store.put("memory", "any", {})
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="Nothing deleted"):
        Home.clear(["memory"])
    assert home_store.items("memory") == {"any": {}}


@pytest.mark.spec("SET-12")
@pytest.mark.parametrize("seconds,text", [(42.5, "42s"), (1762.08, "29m 22s"), (4000, "1h 6m 40s"), (0, "0s")])
def test_human_duration(seconds, text):
    assert human_duration(seconds) == text


@pytest.mark.spec("SET-12")
def test_first_line():
    assert first_line("\n  hello world\nsecond") == "hello world"
    assert first_line(None) == "" and first_line("   ") == ""
    assert first_line("x" * 200, 10) == "xxxxxxx..."


@pytest.mark.spec("SET-12")
def test_words_fingerprint_and_similarity():
    assert words("Hello, World 42!") == ["hello", "world", "42"]
    assert words(None) == []
    assert fingerprint("a") == fingerprint("a") != fingerprint("b")
    assert re.fullmatch(r"[0-9a-f]{40}", fingerprint("a"))
    assert similarity("ABC", "abc") == 1.0 and similarity("abc", "xyz") == 0.0


@pytest.mark.spec("SET-13")
def test_split_text_keeps_paragraphs_together_within_the_size():
    text = "\n\n".join(f"paragraph {i} " + "word " * 30 for i in range(10))
    chunks = split_text(text, 400)
    assert all(len(chunk) <= 400 for chunk in chunks)
    assert len(chunks) > 1
    assert "".join(text.split()) == "".join("".join(chunks).split())  # nothing lost
    assert all(chunk.startswith("paragraph") for chunk in chunks)  # split between paragraphs


@pytest.mark.spec("SET-13")
def test_split_text_cuts_a_long_paragraph_and_never_below_200():
    chunks = split_text("x" * 1000, 50)
    assert [len(c) for c in chunks] == [200] * 5


@pytest.mark.spec("SET-14")
def test_after_what_they_use_orders_dependencies_first():
    uses = {"app": ["db", "util"], "db": ["util"], "util": [], "x": ["app"]}
    order = after_what_they_use(["app", "x"], lambda p: uses[p], lambda p: p in uses)
    assert order == ["util", "db", "app", "x"]


@pytest.mark.spec("SET-14")
def test_after_what_they_use_breaks_cycles_and_skips_unknown():
    uses = {"a": ["b", "ghost"], "b": ["a"]}
    assert after_what_they_use(["a"], lambda p: uses[p], lambda p: p in uses) == ["b", "a"]


@pytest.mark.spec("SET-15")
def test_groups_fitting():
    assert groups_fitting(["aa", "bb", "cc"], 6) == [["aa", "bb"], ["cc"]]
    assert groups_fitting(["a" * 10, "b"], 5) == [["a" * 10], ["b"]]
    assert groups_fitting([], 5) == []
    assert groups_fitting([3, 3, 3], 8, size=lambda n: n) == [[3, 3], [3]]


@pytest.mark.spec("SET-16")
def test_tee_writes_and_gives_whole_lines():
    stream, seen = io.StringIO(), []

    def see(line):
        seen.append(line)
        raise RuntimeError("the log failing")
    tee = Tee(stream, see)
    assert tee.write("one\ntw") == 6
    tee.write("o\n")
    assert stream.getvalue() == "one\ntwo\n" and seen == ["one", "two"]
    assert tee.getvalue() == stream.getvalue()  # anything else is the stream's


@pytest.mark.spec("SET-17")
def test_console_step_format(capsys):
    import time
    Console.step("did it", time.perf_counter() - 61)
    assert re.fullmatch(r"\[\d\d:\d\d:\d\d +1m 1s \(61\.\d\ds\)\] did it\n", capsys.readouterr().out)


@pytest.mark.spec("SET-17")
def test_console_mirror_gives_every_printed_line(monkeypatch):
    out, seen = io.StringIO(), []
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    Console.mirror(seen.append)
    print("logged line")
    print("to stderr", file=sys.stderr)
    assert seen == ["logged line", "to stderr"] and out.getvalue() == "logged line\n"


@pytest.mark.spec("SET-17")
def test_the_live_line_is_drawn_on_a_terminal_only(monkeypatch):
    piped = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", piped)
    Console.progress("     ⏳ thinking… 12s")
    Console.clear_progress()
    assert piped.getvalue() == ""


@pytest.mark.spec("SET-18")
@pytest.mark.parametrize("tool,program", [("caffeinate", "caffeinate"), ("systemd-inhibit", "systemd-inhibit")])
def test_keep_awake_uses_the_tool_there_is(monkeypatch, tool, program):
    started = []
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/bin/" + name if name == tool else None)
    monkeypatch.setattr(settings.subprocess, "Popen", lambda command, **kw: started.append(command))
    Home.keep_awake()
    assert started and started[0][0] == program and str(settings.os.getpid()) in " ".join(started[0])


@pytest.mark.spec("SET-18")
def test_keep_awake_without_a_tool_or_failing_does_nothing(monkeypatch):
    monkeypatch.setattr(settings.shutil, "which", lambda name: None)
    Home.keep_awake()
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/bin/caffeinate")

    def broken(*a, **k):
        raise OSError("cannot start")
    monkeypatch.setattr(settings.subprocess, "Popen", broken)
    Home.keep_awake()  # never raises


@pytest.mark.spec("SET-19")
def test_config_set_reads_and_checks_the_value(capsys):
    assert Config.set("test_timeout", "90") == 90 and Config.get("test_timeout") == 90
    assert Config.set("keep_alive", "10m") == "10m" and Config.set("keep_alive", "300") == 300
    assert Config.set("build_think", "false") is False and Config.set("max_growth", "1.5") == 1.5
    assert Config.set("learn_think", "false") is False and Config.set("docs_pages", "12") == 12
    assert Config.set("web_fallback", "duckduckgo") == "duckduckgo"
    with pytest.raises(SystemExit, match="test_timeout must be a whole number"):
        Config.set("test_timeout", "soon")
    assert Config.get("test_timeout") == 90  # what failed is not kept
    with pytest.raises(SystemExit, match="web_fallback must be one of"):
        Config.set("web_fallback", "bing")
    assert Config.get("web_fallback") == "duckduckgo"
    with pytest.raises(SystemExit, match="learn_missing"):
        Config.set("learn_missing", "0")
    assert Config.get("learn_missing") == 2  # never set before: back to the default


@pytest.mark.spec("SET-19")
def test_config_set_only_the_settings_with_no_command_of_their_own():
    with pytest.raises(SystemExit, match="model is set with tinker use-model NAME"):
        Config.set("model", "x")
    with pytest.raises(SystemExit, match="no setting 'colour'"):
        Config.set("colour", "red")
