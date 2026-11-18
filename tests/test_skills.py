import itertools
import json
import os
import sys

import pytest
from conftest import quick_model_replying

from tinkerkit import skills
from tinkerkit.model.models import LoadedModel, Models
from tinkerkit.prompts import Prompts
from tinkerkit.settings import Home
from tinkerkit.skills import Probe, Skill, SkillLearner, SkillRun, Skills
from tinkerkit.workspace import TaughtCommands


def made_skill(name="thumbs", **changes):
    fields = dict(
        name=name,
        command="convert",
        template="convert {source} -resize {size} {target}",
        slots=[{"name": "source", "sample": "a.png"}, {"name": "size", "sample": "64x64"}],
        purpose="make a thumbnail",
        output="files",
        peak_memory=120_000_000,
    )
    return Skill(**(fields | changes))


@pytest.mark.spec("SKL-1")
def test_new_skill_is_a_candidate_with_no_uses():
    skill = made_skill()
    assert (skill.state, skill.uses) == ("candidate", [])


@pytest.mark.spec("SKL-1")
def test_skill_round_trips_through_a_dict():
    skill = made_skill(state="proven")
    skill.used({"source": "b.png", "size": "32x32"}, ok=True)
    assert Skill.from_dict(skill.to_dict()) == skill


@pytest.mark.spec("SKL-1")
def test_used_records_time_values_and_outcome():
    skill = made_skill(output="text")
    skill.used({"source": "c.png"}, ok=False)
    [use] = skill.uses
    assert (use["values"], use["ok"]) == ({"source": "c.png"}, False)
    assert use["when"]


@pytest.mark.spec("SKL-1")
def test_skills_put_get_all_forget(home_store):
    Skills.put(made_skill("thumbs"))
    Skills.put(made_skill("count", output="text"))
    assert Skills.get("thumbs") == made_skill("thumbs")
    assert Skills.get("missing") is None
    assert [skill.name for skill in Skills.all()] == ["thumbs", "count"]
    assert set(home_store.items("skills")) == {"thumbs", "count"}
    Skills.forget("thumbs")
    assert [skill.name for skill in Skills.all()] == ["count"]


@pytest.mark.spec("SKL-1")
def test_put_replaces_a_skill_of_the_same_name():
    Skills.put(made_skill())
    proven = made_skill(state="proven")
    Skills.put(proven)
    assert Skills.all() == [proven]


@pytest.mark.spec("SKL-1")
def test_forget_unknown_skill_stops():
    with pytest.raises(SystemExit, match="no skill"):
        Skills.forget("ghost")


@pytest.mark.spec("SKL-4")
def test_skill_folder_is_new_and_empty_each_run(tinker_home):
    first = Home.skill_folder("make thumbs/")
    second = Home.skill_folder("make thumbs/")
    assert first != second
    assert first.is_dir() and second.is_dir()
    assert not any(first.iterdir())
    assert first.parent == second.parent == tinker_home / "skills"
    assert "/" not in first.name and " " not in first.name


@pytest.fixture
def shell_world(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("ZDOTDIR", str(tmp_path))
    (tmp_path / ".zshrc").write_text("alias thumbs='shrink -q'\n")
    return bin_dir


def made_program(bin_dir, name, script):
    program = bin_dir / name
    program.write_text(f"#!/bin/sh\n{script}\n")
    program.chmod(0o755)
    return program


@pytest.mark.spec("SKL-2")
def test_probe_finds_a_program_and_its_help(shell_world):
    program = made_program(shell_world, "shrink", 'echo "usage: shrink [-q] FILE"')
    probe = SkillLearner.probe("shrink")
    assert probe.taught == {"kind": "program", "path": str(program), "from": "PATH"}
    assert str(program) in probe.shell
    assert probe.program == str(program)
    assert probe.help == "usage: shrink [-q] FILE"


@pytest.mark.spec("SKL-2")
def test_probe_follows_an_alias_to_its_program(shell_world):
    program = made_program(shell_world, "shrink", 'echo "shrink help"')
    probe = SkillLearner.probe("thumbs")
    assert probe.taught["kind"] == "alias"
    assert probe.taught["definition"] == "shrink -q"
    assert "shrink -q" in probe.shell
    assert probe.program == str(program)
    assert probe.help == "shrink help"


@pytest.mark.spec("SKL-2")
def test_probe_prefers_the_command_taught_to_tinker(shell_world):
    made_program(shell_world, "shrink", "echo help")
    TaughtCommands.teach("tiny", "shrink -q")
    probe = SkillLearner.probe("tiny")
    assert probe.taught["definition"] == "shrink -q"
    assert probe.program == str(shell_world / "shrink")


@pytest.mark.spec("SKL-2")
def test_probe_falls_back_to_short_help(shell_world):
    made_program(shell_world, "shrink", '[ "$1" = "-h" ] && echo "short help" || exit 2')
    assert SkillLearner.probe("shrink").help == "short help"


@pytest.mark.spec("SKL-2")
def test_probe_gives_empty_help_when_the_program_hangs(shell_world, monkeypatch):
    monkeypatch.setattr(skills, "SKILL_PROBE_SECONDS", 0.5)
    made_program(shell_world, "shrink", "sleep 5")
    assert SkillLearner.probe("shrink").help == ""


SHELL_FUNCTION = """
mkthumb() {
  local size="${2:-64}"
  [ -z "$1" ] && return 1
  if [ -n "$size" ]; then
    echo making
  fi
  shrink -q "$1"
}
"""


@pytest.mark.spec("SKL-2")
def test_probe_of_a_shell_function_finds_the_program_it_calls_past_shell_words(shell_world, tmp_path):
    (tmp_path / ".zshrc").write_text(SHELL_FUNCTION)
    program = made_program(shell_world, "shrink", 'echo "shrink help"')
    probe = SkillLearner.probe("mkthumb")
    assert "mkthumb" in probe.shell
    assert probe.program == str(program)


@pytest.mark.spec("SKL-2")
def test_probe_of_an_unknown_name_stops(shell_world):
    with pytest.raises(SystemExit, match="nowhere"):
        SkillLearner.probe("nowhere")


def made_probe(help_text="usage: convert [options] in out"):
    return Probe("thumbs", None, "thumbs: aliased to convert", "/usr/bin/convert", help_text)


def draft_reply(**changes):
    reply = {
        "template": "thumbs {source} -resize {size} {target}",
        "slots": [
            {"name": "source", "sample": "a.png"},
            {"name": "size", "sample": "64x64"},
            {"name": "target", "sample": "b.png"},
        ],
        "output": "files",
    }
    return reply | changes


def drafting(monkeypatch, reply):
    return quick_model_replying(monkeypatch, lambda messages: json.dumps(reply))


@pytest.mark.spec("SKL-2")
def test_drafted_turns_the_reply_into_a_candidate_skill(monkeypatch):
    drafting(monkeypatch, draft_reply())
    skill = SkillLearner.drafted(made_probe(), "make a thumbnail")
    assert skill.name == "thumbs"
    assert skill.template == "thumbs {source} -resize {size} {target}"
    assert [slot["name"] for slot in skill.slots] == ["source", "size", "target"]
    assert skill.slots[1]["sample"] == "64x64"
    assert skill.purpose == "make a thumbnail"
    assert skill.output == "files"
    assert skill.peak_memory == 0
    assert skill.state == "candidate"
    assert skill.uses == []


def drafted_template(monkeypatch, template, slot_names):
    slots = [{"name": name, "sample": "x"} for name in slot_names]
    drafting(monkeypatch, draft_reply(template=template, slots=slots))
    return SkillLearner.drafted(made_probe(), "make a thumbnail").template


@pytest.mark.spec("SKL-2")
@pytest.mark.parametrize("form", ['"$prompt"', "${prompt}", "$prompt"])
def test_drafted_reads_a_dollar_slot_as_a_braced_slot(monkeypatch, form):
    template = drafted_template(monkeypatch, f"say {form} now", ["prompt"])
    assert template == "say {prompt} now"


@pytest.mark.spec("SKL-2")
def test_drafted_keeps_dollar_words_that_are_not_slots(monkeypatch):
    template = drafted_template(monkeypatch, "say $prompt to $HOME and $word $1", ["prompt"])
    assert template == "say {prompt} to $HOME and $word $1"


@pytest.mark.spec("SKL-2")
def test_drafted_does_not_read_a_longer_dollar_word_as_a_slot(monkeypatch):
    template = drafted_template(monkeypatch, "say $prompt $prompts", ["prompt"])
    assert template == "say {prompt} $prompts"


@pytest.mark.spec("SKL-2")
def test_drafted_asks_once_with_the_skill_prompt_the_probe_and_purpose(monkeypatch):
    asked = drafting(monkeypatch, draft_reply())
    SkillLearner.drafted(made_probe(), "make a thumbnail")
    assert len(asked) == 1
    system, user = asked[0]
    assert system == {"role": "system", "content": Prompts.SKILL_SYSTEM}
    for part in ("thumbs", "aliased to convert", "/usr/bin/convert", "usage: convert", "make a thumbnail"):
        assert part in user["content"]


@pytest.mark.spec("SKL-2", "SKL-4")
def test_the_skill_prompt_gives_a_written_file_a_fixed_name_not_a_slot():
    assert "a file the line writes gets a fixed name" in Prompts.SKILL_SYSTEM
    assert "each run has a folder of its own" in Prompts.SKILL_SYSTEM
    assert "an output file" not in Prompts.SKILL_SYSTEM


@pytest.mark.spec("SKL-2")
def test_drafted_cuts_long_help(monkeypatch):
    asked = drafting(monkeypatch, draft_reply())
    SkillLearner.drafted(made_probe("h" * (skills.SKILL_HELP_CHARS * 2)), "make a thumbnail")
    content = asked[0][1]["content"]
    assert "h" * skills.SKILL_HELP_CHARS in content
    assert "h" * (skills.SKILL_HELP_CHARS + 1) not in content


@pytest.mark.spec("SKL-2")
@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"template": ""}, "template"),
        ({"template": None}, "template"),
        ({"output": "image"}, "output"),
        ({"slots": [{"name": "source", "sample": "a.png"}]}, "size"),
        ({"slots": draft_reply()["slots"] + [{"name": "width", "sample": "64"}]}, "width"),
        ({"slots": [{"sample": "a.png"}]}, "slot"),
    ],
)
def test_drafted_fails_on_a_bad_reply(monkeypatch, changes, says):
    drafting(monkeypatch, draft_reply(**changes))
    with pytest.raises(SystemExit, match=says):
        SkillLearner.drafted(made_probe(), "make a thumbnail")


@pytest.mark.spec("SKL-2")
def test_drafted_fails_on_a_reply_that_is_not_json(monkeypatch):
    quick_model_replying(monkeypatch, lambda messages: "no idea")
    with pytest.raises(SystemExit):
        SkillLearner.drafted(made_probe(), "make a thumbnail")


@pytest.mark.spec("SKL-2")
def test_drafted_fails_with_no_model(monkeypatch):
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: None))
    with pytest.raises(SystemExit, match="model"):
        SkillLearner.drafted(made_probe(), "make a thumbnail")


@pytest.mark.spec("SKL-3")
def test_command_quotes_each_slot_value():
    skill = made_skill(template="convert {source} -resize {size} {target}")
    line = SkillRun(skill).command({"source": "my photo.png", "size": "64x64", "target": "a'b; rm -rf ~"})
    assert line == "convert 'my photo.png' -resize 64x64 'a'\"'\"'b; rm -rf ~'"


@pytest.mark.spec("SKL-3")
def test_command_with_a_missing_slot_value_names_it():
    skill = made_skill(template="convert {source} -resize {size} {target}")
    with pytest.raises(SystemExit, match="target"):
        SkillRun(skill).command({"source": "a.png", "size": "64x64"})


@pytest.fixture
def plain_shell(tinker_home, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    return tinker_home


@pytest.mark.spec("SKL-4")
def test_run_gives_the_files_new_in_its_own_folder(plain_shell):
    result = SkillRun(made_skill(template="printf x > {target}", slots=[])).run({"target": "out.txt"})
    assert result.ok and result.folder.parent == plain_shell / "skills"
    assert result.files == [result.folder / "out.txt"]
    assert (result.folder / "out.txt").read_text() == "x"
    assert result.peak_memory > 0


@pytest.mark.spec("SKL-4")
def test_run_peak_memory_is_the_rise_in_memory_in_use_when_higher(plain_shell, monkeypatch):
    readings = itertools.chain([1_000_000_000], itertools.repeat(21_000_000_000))
    monkeypatch.setattr(SkillRun, "memory_in_use", staticmethod(lambda: next(readings)))
    monkeypatch.setattr(skills, "SKILL_MEMORY_SAMPLE_SECONDS", 0.01)
    result = SkillRun(made_skill(template="sleep 0.1", output="text", slots=[])).run({})
    assert result.peak_memory == 20_000_000_000


@pytest.mark.spec("SKL-4")
def test_run_peak_memory_is_its_own_peak_when_memory_in_use_is_unreadable(plain_shell, monkeypatch):
    monkeypatch.setattr(SkillRun, "memory_in_use", staticmethod(lambda: None))
    monkeypatch.setattr(skills, "SKILL_MEMORY_SAMPLE_SECONDS", 0.01)
    result = SkillRun(made_skill(template="sleep 0.1", output="text", slots=[])).run({})
    assert 0 < result.peak_memory < 1_000_000_000


@pytest.mark.spec("SKL-4")
def test_vm_stat_memory_in_use_is_wired_active_and_compressor_pages():
    text = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                                  1179615.\n"
        "Pages active:                                 100126.\n"
        "Pages inactive:                                90848.\n"
        "Pages wired down:                             126638.\n"
        "Pages occupied by compressor:                  10823.\n"
    )
    assert SkillRun.vm_stat_bytes(text) == (100126 + 126638 + 10823) * 16384


@pytest.mark.spec("SKL-4")
@pytest.mark.skipif(sys.platform != "darwin", reason="vm_stat is on a Mac")
def test_memory_in_use_reads_this_macs_vm_stat():
    assert 0 < SkillRun.memory_in_use() < os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


@pytest.mark.spec("SKL-4")
def test_meminfo_memory_in_use_is_total_less_available():
    text = "MemTotal:       16000000 kB\nMemFree:  1000 kB\nMemAvailable:   6000000 kB\n"
    assert SkillRun.meminfo_bytes(text) == 10_000_000 * 1024


@pytest.mark.spec("SKL-4")
def test_run_gives_existing_files_whose_paths_it_printed(plain_shell, tmp_path):
    made = tmp_path / "made.txt"
    made.write_text("y")
    result = SkillRun(made_skill(template="echo done {path} missing.txt", slots=[])).run({"path": str(made)})
    assert result.ok and result.files == [made]


@pytest.mark.spec("SKL-4")
def test_run_gives_what_it_printed_as_text_when_no_file(plain_shell):
    result = SkillRun(made_skill(template="echo hello {who}", output="text", slots=[])).run({"who": "you"})
    assert (result.ok, result.files, result.text) == (True, [], "hello you\n")


@pytest.mark.spec("SKL-4")
def test_run_that_exits_non_zero_fails_with_what_it_printed(plain_shell):
    result = SkillRun(made_skill(template="echo broke >&2; exit 3", output="text", slots=[])).run({})
    assert (result.ok, result.text) == (False, "broke\n")


@pytest.mark.spec("SKL-4")
def test_run_of_a_files_skill_that_gives_no_file_fails(plain_shell):
    result = SkillRun(made_skill(template="echo nothing", slots=[])).run({})
    assert (result.ok, result.files, result.text) == (False, [], "nothing\n")


@pytest.mark.spec("SKL-4")
def test_run_defines_taught_aliases_it_uses(plain_shell):
    TaughtCommands.teach("greet", "echo hello from alias")
    result = SkillRun(made_skill(template="greet", output="text", slots=[])).run({})
    assert (result.ok, result.text) == (True, "hello from alias\n")


@pytest.fixture
def zsh_with_function(tinker_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("ZDOTDIR", str(tmp_path))
    (tmp_path / ".zshrc").write_text('greet() {\n  echo "hello $1"\n}\nalias hi="echo startup ran"\n')
    return tinker_home


@pytest.mark.spec("SKL-4")
def test_run_defines_the_shell_function_its_command_is(zsh_with_function):
    skill = made_skill(command="greet", template="greet {who}", output="text", slots=[])
    result = SkillRun(skill).run({"who": "you"})
    assert (result.ok, result.text) == (True, "hello you\n")


@pytest.mark.spec("SKL-4")
def test_definition_is_only_the_function_not_the_rest_of_the_startup_files(zsh_with_function):
    definition = SkillRun(made_skill(command="greet")).definition()
    assert definition.startswith("greet () {") and definition.endswith("}\n")
    assert "startup ran" not in definition


@pytest.mark.spec("SKL-4")
@pytest.mark.parametrize("command", ["hi", "ls", "nowhere"])
def test_definition_of_a_command_that_is_not_a_function_is_empty(zsh_with_function, command):
    assert SkillRun(made_skill(command=command)).definition() == ""


def learning_world(monkeypatch, skill, loaded=("qwen3", "nomic")):
    seen = []
    monkeypatch.setattr(
        SkillLearner, "probe", staticmethod(lambda name: seen.append(("probe", name)) or name)
    )
    monkeypatch.setattr(
        SkillLearner,
        "drafted",
        staticmethod(lambda probe, purpose: seen.append(("drafted", purpose)) or skill),
    )
    monkeypatch.setattr(
        skills.Ollama, "loaded_models", staticmethod(lambda: [LoadedModel(name, 1) for name in loaded])
    )
    monkeypatch.setattr(skills.Ollama, "unload", staticmethod(lambda name: seen.append(("unload", name))))
    return seen


@pytest.mark.spec("SKL-5")
def test_learned_unloads_the_models_then_trials_with_the_sample_values_and_saves_it_proven(
    plain_shell, monkeypatch
):
    skill = made_skill(
        "greet", template="echo hello {who}", output="text", slots=[{"name": "who", "sample": "you"}]
    )
    seen = learning_world(monkeypatch, skill)
    result = SkillLearner.learned("greet", "say hello")
    assert seen == [("probe", "greet"), ("drafted", "say hello"), ("unload", "qwen3"), ("unload", "nomic")]
    assert (result.ok, result.text) == (True, "hello you\n")
    saved = Skills.get("greet")
    assert saved.state == "proven" and saved.peak_memory == result.peak_memory > 0


@pytest.mark.spec("SKL-2", "SKL-5")
def test_fixed_folds_a_slot_whose_sample_names_a_written_file_into_the_template():
    skill = made_skill(
        template="mgen {prompt} {name} ${name}",
        slots=[{"name": "prompt", "sample": "a cat"}, {"name": "name", "sample": "my cat.png"}],
    )
    skill.fixed(["my cat.png", "other.txt"])
    assert skill.template == "mgen {prompt} 'my cat.png' 'my cat.png'"
    assert skill.slots == [{"name": "prompt", "sample": "a cat"}]


@pytest.mark.spec("SKL-2", "SKL-5")
def test_fixed_keeps_a_slot_whose_sample_matches_no_written_file():
    slots = [{"name": "prompt", "sample": "a cat"}]
    skill = made_skill(template="mgen {prompt}", slots=slots)
    skill.fixed(["cat.png"])
    assert (skill.template, skill.slots) == ("mgen {prompt}", slots)


@pytest.mark.spec("SKL-2", "SKL-5")
def test_learned_saves_a_slot_naming_a_file_the_trial_wrote_as_that_fixed_name(plain_shell, monkeypatch):
    skill = made_skill(
        "draw",
        template="echo {prompt} > {name}",
        output="files",
        slots=[{"name": "prompt", "sample": "a cat"}, {"name": "name", "sample": "cat.png"}],
    )
    learning_world(monkeypatch, skill)
    assert SkillLearner.learned("draw", "draw").ok
    saved = Skills.get("draw")
    assert saved.template == "echo {prompt} > cat.png"
    assert saved.slots == [{"name": "prompt", "sample": "a cat"}]


@pytest.mark.spec("SKL-5")
def test_learned_trial_that_fails_saves_nothing_and_gives_what_it_printed(plain_shell, monkeypatch):
    learning_world(monkeypatch, made_skill("broken", template="echo broke; exit 2", output="text", slots=[]))
    result = SkillLearner.learned("broken", "")
    assert (result.ok, result.text) == (False, "broke\n")
    assert Skills.get("broken") is None


@pytest.mark.spec("SKL-5")
def test_learning_a_name_again_replaces_its_skill(plain_shell, monkeypatch):
    Skills.put(made_skill("greet", purpose="old"))
    learning_world(
        monkeypatch, made_skill("greet", template="echo hi", output="text", slots=[], purpose="new")
    )
    SkillLearner.learned("greet", "new")
    assert [(skill.name, skill.purpose) for skill in Skills.all()] == [("greet", "new")]


@pytest.mark.spec("SKL-5")
def test_learned_works_when_ollama_is_not_running(plain_shell, monkeypatch):
    learning_world(monkeypatch, made_skill("greet", template="echo hi", output="text", slots=[]))
    monkeypatch.setattr(skills.Ollama, "loaded_models", staticmethod(lambda: None))
    assert SkillLearner.learned("greet", "").ok


@pytest.mark.spec("SKL-8")
def test_list_shows_each_skill_with_purpose_state_slots_and_uses(home_store, capsys):
    used = made_skill("thumbs", state="proven")
    used.used({"source": "b.png"}, ok=True)
    Skills.put(used)
    Skills.put(made_skill("count", purpose="count lines", output="text", slots=[]))
    skills.show_list()
    out = capsys.readouterr().out
    assert "thumbs — make a thumbnail (proven, slots source, size; 1 uses)" in out
    assert "count — count lines (candidate, no slots; 0 uses)" in out


@pytest.mark.spec("SKL-8")
def test_list_with_no_skills_says_so(home_store, capsys):
    skills.show_list()
    assert "No skills yet" in capsys.readouterr().out


@pytest.mark.spec("SKL-8", "SET-8")
def test_forget_asks_first_then_deletes(home_store, monkeypatch):
    Skills.put(made_skill("thumbs"))
    asked = []
    monkeypatch.setattr(skills, "confirm_deletion", lambda *args: asked.append(args))
    skills.forgotten("thumbs", yes=False)
    assert asked and asked[0][-1] is False and "thumbs" in asked[0][1][0]
    assert Skills.get("thumbs") is None


@pytest.mark.spec("SKL-8", "SET-8")
def test_forget_refused_keeps_the_skill(home_store, monkeypatch):
    Skills.put(made_skill("thumbs"))
    monkeypatch.setattr(skills.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit, match="Nothing deleted"):
        skills.forgotten("thumbs")
    assert Skills.get("thumbs") is not None


@pytest.mark.spec("SKL-8")
def test_forget_of_an_unknown_skill_stops_without_asking(home_store, monkeypatch):
    monkeypatch.setattr(skills, "confirm_deletion", lambda *args: pytest.fail("asked"))
    with pytest.raises(SystemExit, match="no skill"):
        skills.forgotten("ghost")


@pytest.mark.spec("SKL-6")
def test_values_fill_the_slots_in_order_the_last_taking_the_words_left():
    skill = made_skill(slots=[{"name": "source", "sample": ""}, {"name": "caption", "sample": ""}])
    assert skill.values(["a.png", "a", "fine", "day"]) == {"source": "a.png", "caption": "a fine day"}
    assert skill.values(["a.png"]) == {"source": "a.png"}
    assert made_skill(slots=[]).values([]) == {}


def using_world(monkeypatch, total, loaded=(("qwen3", 600),)):
    unloaded = []
    monkeypatch.setattr(
        skills.Ollama, "loaded_models", staticmethod(lambda: [LoadedModel(*model) for model in loaded])
    )
    monkeypatch.setattr(skills.Ollama, "unload", staticmethod(unloaded.append))
    monkeypatch.setattr(skills.Calibrator, "total_memory", staticmethod(lambda: total))
    return unloaded


def greeting_skill(peak_memory=500):
    return made_skill(
        "greet",
        template="echo hello {who}",
        output="text",
        slots=[{"name": "who", "sample": "you"}],
        peak_memory=peak_memory,
    )


@pytest.mark.spec("SKL-6")
def test_use_runs_with_the_words_and_adds_it_to_the_history(plain_shell, monkeypatch):
    using_world(monkeypatch, total=10_000)
    Skills.put(greeting_skill())
    result = skills.skill_used(Skills.get("greet"), ["dear", "friend"])
    assert (result.ok, result.text) == (True, "hello dear friend\n")
    uses = Skills.get("greet").uses
    assert [(use["values"], use["ok"]) for use in uses] == [({"who": "dear friend"}, True)]


@pytest.mark.spec("SKL-6")
def test_use_that_fails_is_in_the_history_as_failed(plain_shell, monkeypatch):
    using_world(monkeypatch, total=10_000)
    Skills.put(made_skill("broken", template="exit 3", output="text", slots=[]))
    assert not skills.skill_used(Skills.get("broken"), []).ok
    assert [use["ok"] for use in Skills.get("broken").uses] == [False]


@pytest.mark.spec("SKL-6")
def test_use_keeps_the_model_when_both_fit(plain_shell, monkeypatch):
    unloaded = using_world(monkeypatch, total=1_100)
    skills.skill_used(greeting_skill(peak_memory=500), ["you"])
    assert unloaded == []


@pytest.mark.spec("SKL-6")
def test_use_unloads_the_models_when_they_and_the_skill_do_not_fit(plain_shell, monkeypatch):
    unloaded = using_world(monkeypatch, total=1_099, loaded=(("qwen3", 300), ("nomic", 300)))
    skills.skill_used(greeting_skill(peak_memory=500), ["you"])
    assert unloaded == ["qwen3", "nomic"]


@pytest.mark.spec("SKL-6")
def test_use_works_when_ollama_is_not_running(plain_shell, monkeypatch):
    using_world(monkeypatch, total=1_000)
    monkeypatch.setattr(skills.Ollama, "loaded_models", staticmethod(lambda: None))
    assert skills.skill_used(greeting_skill(), ["you"]).ok


@pytest.mark.spec("SKL-6", "SKL-3")
def test_use_with_a_missing_slot_names_it_and_runs_nothing(plain_shell, monkeypatch):
    unloaded = using_world(monkeypatch, total=1)
    skill = made_skill("pair", template="echo {a} {b}", output="text", slots=[{"name": "a"}, {"name": "b"}])
    with pytest.raises(SystemExit, match="needs a value for b"):
        skills.skill_used(skill, ["one"])
    assert unloaded == [] and skill.uses == []


@pytest.mark.spec("SKL-6")
def test_unknown_skill_says_so_and_lists_the_skills(home_store):
    Skills.put(made_skill("thumbs"))
    with pytest.raises(SystemExit) as stopped:
        skills.known_skill("ghost")
    assert str(stopped.value).splitlines() == ["There is no skill ghost", made_skill("thumbs").glance()]


@pytest.mark.spec("SKL-6")
def test_unknown_skill_with_no_skills_says_there_are_none(home_store):
    with pytest.raises(SystemExit, match="There is no skill ghost\nNo skills yet"):
        skills.known_skill("ghost")
