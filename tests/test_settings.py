"""SET: Tinker's settings, its home folder and the small helpers every part uses."""

import io
import json
import re
import sys
from pathlib import Path

import pytest

import tinkerkit.settings as settings
from tinkerkit.settings import (
    Config, Console, EventLog, Home, JsonFile, Tee, after_what_they_use, first_line, fingerprint, groups_fitting,
    human_duration, similarity, split_text, words,
)
from conftest import REAL_HOME


@pytest.mark.spec("SET-1")
def test_home_is_dot_tinker_next_to_the_real_script():
    assert REAL_HOME == Path(settings.__file__).resolve().parent.parent / ".tinker"


@pytest.mark.spec("SET-1")
def test_every_home_file_is_inside_home(tinker_home):
    for name in ("CONFIG_FILE", "MODELS_DIR", "LANGUAGES_DIR", "MEMORY_FILE", "RUNS_DIR", "COMMANDS_FILE",
                 "LEARNED_DIR", "EMBEDDINGS_FILE", "LEARN_LOG"):
        assert getattr(settings, name).parent == settings.HOME, name
    assert settings.HOME == tinker_home


@pytest.mark.spec("SET-2")
def test_default_config():
    assert settings.DEFAULT_CONFIG == {
        "model": None, "ollama_url": "http://localhost:11434", "keep_alive": 0, "max_growth": 1.15,
        "build_think": "medium", "test_timeout": 60, "web": "ollama", "web_fallback": "", "web_timeout": 30,
        "learn_missing": 2, "embed_model": "", "projects": "~/Projects"}


@pytest.mark.spec("SET-3")
def test_phase_lists():
    assert settings.PHASES == ("recall", "spec", "design", "review", "setup", "tests", "code", "verify", "tidy",
                               "docs", "final_review", "lessons", "done")
    assert settings.FIX_PHASES == ("recall", "reproduce", "verify", "final_review", "lessons", "done")
    assert settings.PLANNING_PHASES == ("recall", "spec", "design", "review")


@pytest.mark.spec("SET-4")
def test_config_is_defaults_overridden_by_the_file_and_read_once(tinker_home):
    JsonFile.write(tinker_home / "config.json", {"model": "m1", "test_timeout": 5})
    assert Config.get("model") == "m1" and Config.get("test_timeout") == 5
    assert Config.get("web") == "ollama"  # a default the file does not set
    JsonFile.write(tinker_home / "config.json", {"model": "changed"})
    assert Config.get("model") == "m1"  # cached: read once


@pytest.mark.spec("SET-4")
def test_config_save_writes_the_merged_settings(tinker_home):
    JsonFile.write(tinker_home / "config.json", {"model": "m1"})
    Config.save(web="duckduckgo")
    saved = json.loads((tinker_home / "config.json").read_text())
    assert saved["model"] == "m1" and saved["web"] == "duckduckgo" and saved["keep_alive"] == 0


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("key,value", [("keep_alive", "soon"), ("keep_alive", "10 m"), ("max_growth", 1),
                                       ("max_growth", True), ("max_growth", "2"), ("build_think", "max"),
                                       ("build_think", "true")])
def test_check_model_rejects_unusable_settings(key, value):
    Config.load()[key] = value
    with pytest.raises(SystemExit, match=key):
        Config.check_model(run=False)


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("value", [0, 300, -1, "10m", "1h30m", "500ms"])
def test_check_model_accepts_keep_alive_seconds_or_durations(value):
    Config.load()["keep_alive"] = value
    Config.check_model(run=True)


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("value", [0, True, 1.5, "60"])
def test_test_timeout_is_checked_for_a_run_only(value):
    Config.load()["test_timeout"] = value
    Config.check_model(run=False)
    with pytest.raises(SystemExit, match="test_timeout"):
        Config.check_model(run=True)


@pytest.mark.spec("SET-6")
def test_keep_alive_and_ollama_url_as_ollama_takes_them():
    assert Config.keep_alive("300") == 300 and Config.keep_alive(-1) == -1
    assert Config.keep_alive("10m") == "10m"
    Config.load()["ollama_url"] = "http://host:1/"
    assert Config.ollama_url() == "http://host:1"


@pytest.mark.spec("SET-7")
def test_json_write_is_whole_utf8_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "a" / "b" / "data.json"
    JsonFile.write(path, {"name": "café", "n": [1]})
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n") and "café" in text and '\n  "n"' in text
    assert json.loads(text) == {"name": "café", "n": [1]}
    assert [p.name for p in path.parent.iterdir()] == ["data.json"]


@pytest.mark.spec("SET-8")
def test_json_read_missing_gives_the_default(tmp_path):
    assert JsonFile.read(tmp_path / "none.json", {"d": 1}) == {"d": 1}


@pytest.mark.spec("SET-8")
@pytest.mark.parametrize("content", ["{broken", "[1, 2]"])
def test_json_read_broken_file_that_keeps_work_stops(tmp_path, content):
    path = tmp_path / "x.json"
    path.write_text(content)
    with pytest.raises(SystemExit, match="fix it, or delete it"):
        JsonFile.read(path, {}, keeps_work=True)


@pytest.mark.spec("SET-8")
def test_json_read_broken_cache_is_noted_and_the_default_used(tmp_path, capsys):
    path = tmp_path / "x.json"
    path.write_text("{broken")
    assert JsonFile.read(path, {"d": 1}) == {"d": 1}
    assert "could not be read" in capsys.readouterr().out


@pytest.mark.spec("SET-9")
def test_ensure_makes_home_and_default_config(tinker_home):
    assert not tinker_home.exists()
    Home.ensure()
    assert json.loads((tinker_home / "config.json").read_text()) == settings.DEFAULT_CONFIG
    Home.ensure()  # a second time changes nothing


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


@pytest.mark.spec("SET-10")
def test_reset_without_home_does_nothing(tinker_home, capsys):
    Home.reset(yes=True)
    assert "Nothing to reset" in capsys.readouterr().out


@pytest.mark.spec("SET-10")
def test_reset_refuses_a_folder_not_named_dot_tinker(tmp_path, monkeypatch):
    other = tmp_path / "precious"
    other.mkdir()
    monkeypatch.setattr(settings, "HOME", other)
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
    assert list(Home.parts()) == ["languages", "memory", "learned", "models", "runs", "commands"]


@pytest.mark.spec("SET-11")
def test_clear_deletes_only_the_named_parts(tinker_home):
    for path in (settings.LANGUAGES_DIR / "python.json", settings.MEMORY_FILE, settings.MODELS_DIR / "m.json"):
        JsonFile.write(path, {})
    Home.clear(["languages", "memory"], yes=True)
    assert not settings.LANGUAGES_DIR.exists() and not settings.MEMORY_FILE.exists()
    assert (settings.MODELS_DIR / "m.json").exists()


@pytest.mark.spec("SET-11")
def test_clear_rejects_an_unknown_part():
    with pytest.raises(SystemExit, match="the parts are: languages"):
        Home.clear(["everything"], yes=True)


@pytest.mark.spec("SET-11")
def test_clear_with_nothing_there(capsys):
    Home.clear(["runs"], yes=True)
    assert "Nothing to clear: runs is empty" in capsys.readouterr().out


@pytest.mark.spec("SET-11")
def test_clear_outside_a_terminal_without_yes_deletes_nothing(monkeypatch):
    JsonFile.write(settings.MEMORY_FILE, {})
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="Nothing deleted"):
        Home.clear(["memory"])
    assert settings.MEMORY_FILE.exists()


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
def test_tee_writes_both_and_ignores_the_file_failing():
    stream, file = io.StringIO(), io.StringIO()
    tee = Tee(stream, file)
    assert tee.write("hi") == 2
    file.close()
    tee.write(" there")
    assert stream.getvalue() == "hi there"


@pytest.mark.spec("SET-16")
def test_event_log_appends_json_lines(tmp_path):
    log = EventLog(tmp_path / "log.jsonl")
    log.add({"label": "one"})
    log.add({"label": "two", "x": "é"})
    lines = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [l["label"] for l in lines] == ["one", "two"] and all("time" in l for l in lines)


@pytest.mark.spec("SET-17")
def test_console_step_format(capsys):
    import time
    Console.step("did it", time.perf_counter() - 61)
    assert re.fullmatch(r"\[\d\d:\d\d:\d\d +1m 1s \(61\.\d\ds\)\] did it\n", capsys.readouterr().out)


@pytest.mark.spec("SET-17")
def test_console_mirror_copies_output_to_current_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(sys, "__stdout__", io.StringIO())
    monkeypatch.setattr(sys, "__stderr__", io.StringIO())
    Console.mirror(tmp_path)
    print("logged line")
    assert "logged line" in (tmp_path / "current.log").read_text()


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
