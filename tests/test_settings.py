import io
import sys
from pathlib import Path

import pytest
from conftest import REAL_HOME

import tinkerkit.settings as settings
from tinkerkit.commands import BY_NAME
from tinkerkit.settings import (
    PER_PAGE,
    SETTINGS,
    Config,
    Home,
    Numbered,
    Paged,
    confirm_deletion,
)


@pytest.mark.spec("SET-1")
def test_home_is_dot_tinker_next_to_the_real_script(tinker_home):
    assert REAL_HOME == Path(settings.__file__).resolve().parent.parent / ".tinker"
    assert Home.root == tinker_home and Home.store().path == tinker_home / "tinker.db"


@pytest.mark.spec("SET-2")
def test_default_config():
    assert settings.DEFAULT_CONFIG == {
        "model": None,
        "ollama_url": "http://localhost:11434",
        "keep_alive": 0,
        "max_growth": 1.15,
        "build_think": "medium",
        "test_timeout": 60,
        "web_timeout": 30,
        "learn_missing": 2,
        "learn_cutoff": 0.0,
        "learn_pages": 8,
        "learn_min_pages": 2,
        "learn_searches": 3,
        "learn_think": "low",
        "tool_docs_pages": 5,
        "docs_latest_days": 30,
        "fetch_workers": 6,
        "fetch_timeout": 15,
        "fetch_max_bytes": 2000000,
        "embed_model": "",
        "projects": "~/Projects",
    }


@pytest.mark.spec("SET-3")
def test_phase_lists():
    assert settings.PHASES == (
        "recall",
        "spec",
        "design",
        "review",
        "setup",
        "tests",
        "code",
        "verify",
        "tidy",
        "docs",
        "final_review",
        "lessons",
        "done",
    )
    assert settings.FIX_PHASES == ("recall", "reproduce", "verify", "final_review", "lessons", "done")
    assert settings.CONFIRM_AFTER == {"feature": "review", "fix": "reproduce"}


@pytest.mark.spec("SET-4")
def test_config_is_defaults_overridden_by_the_home(home_store):
    home_store.put("config", "model", "m1")
    assert Config.get("model") == "m1" and Config.get("web_timeout") == 30
    changed_by_another_tinker = "changed"
    home_store.put("config", "model", changed_by_another_tinker)
    assert Config.get("model") == changed_by_another_tinker
    Config.save(web_timeout=5, test_timeout=5)
    assert Config.load() == dict(settings.DEFAULT_CONFIG, model="changed", web_timeout=5, test_timeout=5)


@pytest.mark.spec("SET-4")
def test_a_stored_key_no_setting_has_is_ignored(home_store):
    home_store.put("config", "web_fallback", "duckduckgo")
    assert Config.load() == settings.DEFAULT_CONFIG
    Config.check()


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize(
    "key,value",
    [
        ("keep_alive", "soon"),
        ("keep_alive", "10 m"),
        ("max_growth", 1),
        ("max_growth", True),
        ("max_growth", "2"),
        ("build_think", "max"),
        ("build_think", "true"),
        ("build_think", 1),
        ("learn_think", "max"),
        ("test_timeout", 0),
        ("test_timeout", 1.5),
        ("test_timeout", "60"),
        ("web_timeout", 0),
        ("web_timeout", True),
        ("learn_missing", 0),
        ("learn_missing", 1.5),
        ("learn_missing", True),
        ("learn_pages", 2.5),
        ("learn_min_pages", 0),
        ("learn_cutoff", -1),
        ("fetch_workers", True),
        ("fetch_max_bytes", "big"),
    ],
)
def test_check_rejects_a_setting_outside_its_row(key, value):
    Config.save(**{key: value})
    with pytest.raises(SystemExit, match=f"^{key} must be"):
        Config.check()


@pytest.mark.spec("SET-5")
@pytest.mark.parametrize("value", [0, 300, -1, "10m", "1h30m", "500ms"])
def test_check_accepts_keep_alive_seconds_or_durations(value):
    Config.save(keep_alive=value)
    Config.check()


@pytest.mark.spec("SET-5")
def test_every_setting_has_one_row_with_its_owner():
    assert list(SETTINGS) == list(settings.DEFAULT_CONFIG)
    owned = {key: row.owner for key, row in SETTINGS.items() if row.owner != "config"}
    assert owned == {"model": "use-model", "embed_model": "embed"}
    Config.check()
    Config.save(web_timeout=2.5, learn_missing=3)
    Config.check()


@pytest.mark.spec("SET-7")
def test_keep_alive_and_ollama_url_as_ollama_takes_them():
    assert Config.keep_alive("300") == 300 and Config.keep_alive(-1) == -1
    assert Config.keep_alive("10m") == "10m"
    Config.save(ollama_url="http://host:1/")
    assert Config.ollama_url() == "http://host:1"


@pytest.mark.spec("SET-8")
def test_ensure_makes_home_and_its_database(tinker_home, capsys):
    assert not tinker_home.exists()
    Home.ensure()
    assert (tinker_home / "tinker.db").is_file() and "Created" in capsys.readouterr().out
    Home.ensure()
    assert capsys.readouterr().out == ""


@pytest.mark.spec("SET-8")
def test_ensure_stops_when_home_cannot_be_written(tinker_home, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(SystemExit, match="cannot be written"):
        Home.ensure()


@pytest.mark.spec("SET-9")
def test_reset_with_yes_deletes_home(tinker_home):
    Home.ensure()
    Home.reset(yes=True)
    assert not tinker_home.exists()
    Config.save(model="m")
    assert Config.get("model") == "m"


@pytest.mark.spec("SET-9")
def test_reset_without_home_does_nothing(tinker_home, capsys):
    Home.reset(yes=True)
    assert "Nothing to reset" in capsys.readouterr().out


@pytest.mark.spec("SET-9")
def test_reset_refuses_a_folder_not_named_dot_tinker(tmp_path, monkeypatch):
    other = tmp_path / "precious"
    other.mkdir()
    monkeypatch.setattr(Home, "root", other)
    with pytest.raises(SystemExit, match="not a .tinker folder"):
        Home.reset(yes=True)
    assert other.exists()


@pytest.mark.spec("SET-9")
def test_reset_outside_a_terminal_without_yes_deletes_nothing(tinker_home, monkeypatch):
    Home.ensure()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="Nothing deleted"):
        Home.reset()
    assert tinker_home.exists()


@pytest.mark.spec("SET-9")
def test_a_deletion_says_what_goes_then_asks(monkeypatch, capsys):
    confirm_deletion("This deletes:", ["the notes", "the logs"], "tinker clear notes", yes=True)
    said = capsys.readouterr().out
    assert "This deletes:" in said and "the notes" in said and "the logs" in said
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(
        SystemExit, match="Nothing archived: run tinker clear jobs #1 in a terminal to confirm"
    ):
        confirm_deletion("This archives:", ["#1"], "tinker clear jobs #1", verb="archive")
    asked = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda question: asked.append(question) or "n")
    with pytest.raises(SystemExit, match=r"^Nothing deleted\.$"):
        confirm_deletion("This deletes:", ["the home"], "tinker reset")
    monkeypatch.setattr("builtins.input", lambda question: asked.append(question) or "y")
    confirm_deletion("This deletes:", ["the notes", "the logs"], "tinker clear notes")
    assert asked == ["Delete it? [y/N] ", "Delete them? [y/N] "]


@pytest.mark.spec("SET-10")
def test_clear_deletes_only_the_named_parts(home_store, capsys):
    home_store.put("languages", "python", {})
    home_store.put("memory", "any", {})
    home_store.put("models", "m", {})
    home_store.put("learned", "flask", {})
    Home.clear(["languages", "memory", "learned"], yes=True)
    assert home_store.items("languages") == {} and home_store.items("memory") == {}
    assert home_store.items("learned") == {}
    assert home_store.items("models") == {"m": {}}
    assert "Cleared languages, memory, learned." in capsys.readouterr().out


@pytest.mark.spec("SET-10")
def test_clear_rejects_an_unknown_part():
    parts = (
        "languages, memory, learned, docs, searches, models, runs, commands, history, exercise, jobs, read"
    )
    with pytest.raises(SystemExit, match=f"the parts are: {parts}$"):
        Home.clear(["everything"], yes=True)


@pytest.mark.spec("SET-10", "KNW-26")
def test_clear_searches_forgets_the_search_plans(home_store):
    home_store.put("search plans", "{topic} docs", [{"query": "{topic} docs"}])
    home_store.put("sources", "flask@latest", {})
    Home.clear(["searches"], yes=True)
    assert home_store.items("search plans") == {} and home_store.items("sources") == {"flask@latest": {}}


@pytest.mark.spec("SET-10")
def test_clear_with_nothing_there(capsys):
    Home.clear(["runs"], yes=True)
    assert "Nothing to clear: runs is empty" in capsys.readouterr().out


@pytest.mark.spec("SET-10")
def test_clear_outside_a_terminal_without_yes_deletes_nothing(home_store, monkeypatch):
    home_store.put("memory", "any", {})
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="Nothing deleted"):
        Home.clear(["memory"])
    assert home_store.items("memory") == {"any": {}}


@pytest.mark.spec("SET-11")
@pytest.mark.parametrize(
    "tool,program", [("caffeinate", "caffeinate"), ("systemd-inhibit", "systemd-inhibit")]
)
def test_keep_awake_uses_the_tool_there_is(monkeypatch, tool, program):
    started = []
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/bin/" + name if name == tool else None)
    monkeypatch.setattr(settings.subprocess, "Popen", lambda command, **kwargs: started.append(command))
    Home.keep_awake()
    assert started and started[0][0] == program and str(settings.os.getpid()) in " ".join(started[0])


@pytest.mark.spec("SET-11")
def test_keep_awake_without_a_tool_or_failing_does_nothing(monkeypatch):
    monkeypatch.setattr(settings.shutil, "which", lambda name: None)
    Home.keep_awake()
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/bin/caffeinate")

    def broken(*args, **kwargs):
        raise OSError("cannot start")

    monkeypatch.setattr(settings.subprocess, "Popen", broken)
    Home.keep_awake()


@pytest.mark.spec("SET-6")
def test_config_set_reads_and_checks_the_value():
    assert Config.set("test_timeout", "90") == 90 and Config.get("test_timeout") == 90
    assert Config.set("keep_alive", "10m") == "10m" and Config.set("keep_alive", "300") == 300
    assert Config.set("build_think", "false") is False and Config.set("max_growth", "1.5") == 1.5
    assert Config.set("learn_think", "false") is False and Config.set("tool_docs_pages", "12") == 12
    assert Config.set("projects", "~/Code") == "~/Code"
    with pytest.raises(SystemExit, match="test_timeout must be a whole number"):
        Config.set("test_timeout", "soon")
    assert Config.get("test_timeout") == 90
    with pytest.raises(SystemExit, match="web_timeout must be a number above 0"):
        Config.set("web_timeout", "0")
    assert Config.get("web_timeout") == settings.DEFAULT_CONFIG["web_timeout"]
    with pytest.raises(SystemExit, match="learn_missing"):
        Config.set("learn_missing", "0")
    assert Config.get("learn_missing") == settings.DEFAULT_CONFIG["learn_missing"]


@pytest.mark.spec("SET-6")
def test_config_set_only_the_settings_with_no_command_of_their_own():
    with pytest.raises(SystemExit, match="model is set with tinker use-model NAME"):
        Config.set("model", "x")
    with pytest.raises(SystemExit, match="no setting 'colour'"):
        Config.set("colour", "red")


@pytest.mark.spec("SET-6")
def test_help_config_lists_the_settings_from_the_table():
    about = BY_NAME["config"].about
    assert "test_timeout (a whole number, 1 or more)" in about and "max_growth (a number above 1)" in about
    assert "web_timeout (" in about and "embed_model" not in about and "model (" not in about


@pytest.mark.spec("SET-12")
def test_a_numbered_list_never_gives_a_number_again(home_store):
    listed = Numbered("things", "thing numbers")
    first, second = listed.add({"text": "a"}), listed.add({"text": "b"})
    assert (first["number"], second["number"]) == (1, 2) and listed.get(2) == second
    listed.delete(2)
    home_store.delete("things")
    assert listed.add({"text": "c"})["number"] == 3 and listed.get(2) is None
    assert listed.update(3, lambda entry: dict(entry, text="d"))["text"] == "d"
    listed.put(dict(first, text="e"))
    assert [entry["text"] for entry in listed.newest()] == ["d", "e"]


@pytest.mark.spec("SET-12")
def test_a_list_comes_a_page_at_a_time():
    entries = list(range(PER_PAGE * 2 + 1))
    assert PER_PAGE == 10 and Numbered.page(entries, 1) == Paged(entries[:PER_PAGE], 3)
    assert Numbered.page(entries, 3) == Paged([entries[-1]], 3) and Numbered.page([], 1) == Paged([], 1)
