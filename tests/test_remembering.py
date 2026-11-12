import datetime

import pytest

from tinkerkit.remembering import (
    Memories,
    Portrait,
    Scratchpad,
    forget,
    show_memories,
    show_portrait,
    show_remembered,
)
from tinkerkit.retrieval import Retriever
from tinkerkit.settings import (
    EPISODE_FOUND_CHARS,
    EPISODE_GIST_CHARS,
    EPISODE_KEPT_DAYS,
    PORTRAIT_WORDS,
    RECALL_MEANING,
    RECALL_SHOWN,
    Home,
)

NOW = datetime.datetime(2026, 11, 11, 12, 0, 0)


def chat_episode(**changes):
    return {
        "chat": "telegram:1",
        "message": "list the files",
        "commands": [{"line": "ls", "output": "a.py\nb.py"}],
        "reply": "Two files.",
        "status": "answered",
        **changes,
    }


@pytest.mark.spec("REM-1")
def test_a_written_episode_gets_the_next_id_its_time_and_is_not_dreamt(home_store):
    first = Scratchpad.write(chat_episode(), NOW)
    second = Scratchpad.write({"chat": "cli", "task": "make a game", "status": "done"}, NOW)

    assert first["id"] == "e1" and second["id"] == "e2"
    assert first["time"] == "2026-11-11T12:00:00"
    assert first["dreamt"] is False
    assert home_store.get("episodes", "e1")["message"] == "list the files"


@pytest.mark.spec("REM-1")
def test_ids_keep_counting_after_episodes_are_pruned(home_store):
    Scratchpad.write(chat_episode(), NOW)
    Scratchpad.dreamt(["e1"], NOW)
    Scratchpad.prune(NOW + datetime.timedelta(days=EPISODE_KEPT_DAYS + 1))

    assert Scratchpad.write(chat_episode(), NOW)["id"] == "e2"


@pytest.mark.spec("REM-1")
def test_a_written_episode_cuts_command_output_to_the_found_size(home_store):
    long = "x" * (EPISODE_FOUND_CHARS * 2)
    episode = Scratchpad.write(chat_episode(commands=[{"line": "cat big", "output": long}], output=long), NOW)

    assert len(episode["commands"][0]["output"]) == EPISODE_FOUND_CHARS
    assert len(episode["output"]) == EPISODE_FOUND_CHARS


@pytest.mark.spec("REM-1")
def test_fresh_episodes_are_the_ones_not_dreamt(home_store):
    Scratchpad.write(chat_episode(), NOW)
    Scratchpad.write(chat_episode(message="second"), NOW)
    Scratchpad.dreamt(["e1"], NOW)

    assert [episode["id"] for episode in Scratchpad.fresh()] == ["e2"]


@pytest.mark.spec("REM-2")
def test_a_dreamt_episode_keeps_only_its_gist(home_store):
    long = "y" * (EPISODE_GIST_CHARS * 3)
    Scratchpad.write(chat_episode(message=long, reply=long, commands=[{"line": long, "output": long}]), NOW)

    Scratchpad.dreamt(["e1"], NOW)

    episode = Home.store().get("episodes", "e1")
    assert episode["dreamt"] is True and episode["dreamt_time"] == "2026-11-11T12:00:00"
    assert len(episode["message"]) == len(episode["reply"]) == EPISODE_GIST_CHARS
    assert len(episode["commands"][0]["line"]) == len(episode["commands"][0]["output"]) == EPISODE_GIST_CHARS
    assert episode["id"] == "e1" and episode["chat"] == "telegram:1" and episode["status"] == "answered"
    assert "gist" not in episode


@pytest.mark.spec("REM-2")
def test_a_dreamt_episode_keeps_the_gist_the_dream_wrote_cut(home_store):
    Scratchpad.write(chat_episode(), NOW)
    Scratchpad.write(chat_episode(message="second"), NOW)

    Scratchpad.dreamt(["e1", "e2"], NOW, gists={"e1": "z" * (EPISODE_GIST_CHARS * 2)})

    assert Home.store().get("episodes", "e1")["gist"] == "z" * EPISODE_GIST_CHARS
    assert "gist" not in Home.store().get("episodes", "e2")


@pytest.mark.spec("REM-2")
def test_prune_deletes_episodes_dreamt_longer_ago_than_kept(home_store):
    Scratchpad.write(chat_episode(), NOW)
    Scratchpad.write(chat_episode(), NOW)
    Scratchpad.write(chat_episode(), NOW)
    Scratchpad.dreamt(["e1"], NOW - datetime.timedelta(days=EPISODE_KEPT_DAYS + 1))
    Scratchpad.dreamt(["e2"], NOW - datetime.timedelta(days=EPISODE_KEPT_DAYS - 1))

    Scratchpad.prune(NOW)

    assert sorted(home_store.items("episodes")) == ["e2", "e3"]


@pytest.mark.spec("DRM-9")
def test_the_memory_page_lives_in_the_tinker_home(tinker_home):
    assert Home.memory_page() == tinker_home / "memory.html"
    assert tinker_home.is_dir()


class FakeEmbeddings:
    name = "fake"
    failed = "no reason"

    def __init__(self, meanings):
        self.meanings = meanings

    def vectors(self, texts, keep=True, prune=False):
        return [self.meanings.get(text, [0.0, 1.0]) for text in texts]


def memories(embeddings=None):
    return Memories(Retriever(embeddings))


@pytest.mark.spec("REM-3")
def test_an_added_memory_gets_the_next_id_and_starts_fresh(home_store):
    added = memories().add("person", "Likes Rust", "The person writes Rust daily.", ["e1"], NOW)
    second = memories().add("fact", "Repo", "The repo is codeforge.", ["e2"], NOW)

    assert added == {
        "id": "m1",
        "kind": "person",
        "gist": "Likes Rust",
        "text": "The person writes Rust daily.",
        "sources": ["e1"],
        "first_seen": "2026-11-11T12:00:00",
        "last_seen": "2026-11-11T12:00:00",
        "recalled": 0,
        "last_recalled": "",
        "strength": 1,
        "retired": "",
    }
    assert second["id"] == "m2"
    assert home_store.get("memories", "m1") == added


@pytest.mark.spec("REM-3")
def test_a_memory_without_sources_is_refused(home_store):
    with pytest.raises(ValueError):
        memories().add("fact", "Repo", "The repo is codeforge.", [], NOW)

    assert home_store.items("memories") == {}


@pytest.mark.spec("REM-3")
def test_update_changes_a_memory_and_retire_says_why(home_store):
    memories().add("fact", "Repo", "The repo is codeforge.", ["e1"], NOW)

    memories().update("m1", {"strength": 3, "last_seen": "2026-11-12T00:00:00"})
    memories().retire("m1", "the repo moved")

    kept = home_store.get("memories", "m1")
    assert kept["strength"] == 3 and kept["last_seen"] == "2026-11-12T00:00:00"
    assert kept["retired"] == "the repo moved"


@pytest.mark.spec("REM-4")
def test_recall_finds_memories_and_fresh_episodes_holding_a_word(home_store):
    memories().add("person", "Likes Rust", "The person writes Rust daily.", ["e1"], NOW)
    memories().add("fact", "Repo", "The repo is codeforge.", ["e1"], NOW)
    Scratchpad.write(chat_episode(message="build a rust game"), NOW)
    Scratchpad.write(chat_episode(message="rust again"), NOW)
    Scratchpad.dreamt(["e2"], NOW)

    found = memories().recall("rust please", NOW)

    assert sorted(item["id"] for item in found) == ["e1", "m1"]


@pytest.mark.spec("REM-4")
def test_recall_matches_what_an_episode_says_not_its_bookkeeping(home_store):
    Scratchpad.write({"chat": "telegram:1", "status": "answered", "message": "hello"}, NOW)
    Scratchpad.write({"chat": "1", "steps": [{"line": "cargo build"}], "notes": ["about rust"]}, NOW)

    assert memories().recall("answered telegram", NOW) == []
    assert [item["id"] for item in memories().recall("cargo rust", NOW)] == ["e2"]


@pytest.mark.spec("REM-4")
def test_recall_of_some_kinds_keeps_episodes_and_counts_only_those_memories(home_store):
    memories().add("tool", "Rust via cargo", "cargo builds rust.", ["e1"], NOW)
    memories().add("fact", "Likes Rust", "The person writes Rust daily.", ["e1"], NOW)
    Scratchpad.write(chat_episode(message="build a rust game"), NOW)

    found = memories().recall("rust", NOW, ("tool",))

    assert sorted(item["id"] for item in found) == ["e1", "m1"]
    assert home_store.get("memories", "m2")["recalled"] == 0


@pytest.mark.spec("REM-4")
def test_recall_skips_retired_memories(home_store):
    memories().add("person", "Likes Rust", "The person writes Rust daily.", ["e1"], NOW)
    memories().retire("m1", "wrong")

    assert memories().recall("rust", NOW) == []


@pytest.mark.spec("REM-4")
def test_a_recalled_memory_counts_the_recall(home_store):
    memories().add("person", "Likes Rust", "The person writes Rust daily.", ["e1"], NOW)
    memories().add("fact", "Repo", "The repo is codeforge.", ["e1"], NOW)

    memories().recall("rust", NOW + datetime.timedelta(hours=1))

    assert home_store.get("memories", "m1")["recalled"] == 1
    assert home_store.get("memories", "m1")["last_recalled"] == "2026-11-11T13:00:00"
    assert home_store.get("memories", "m2")["recalled"] == 0


@pytest.mark.spec("REM-4")
def test_recall_shows_at_most_the_shown_count_best_first(home_store):
    for number in range(RECALL_SHOWN + 2):
        memories().add("fact", f"Note {number}", "rust and many filler words here", ["e1"], NOW)
    memories().add("fact", "Best", "rust rust", ["e1"], NOW)

    found = memories().recall("rust", NOW)

    assert len(found) == RECALL_SHOWN
    assert found[0]["gist"] == "Best"


@pytest.mark.spec("REM-4")
def test_recall_finds_by_meaning_close_enough_after_word_hits(home_store):
    memories().add("fact", "Pets", "A cat lives here.", ["e1"], NOW)
    memories().add("fact", "Cars", "A red car.", ["e1"], NOW)
    memories().add("fact", "Kitten", "Has a kitten.", ["e1"], NOW)
    embeddings = FakeEmbeddings(
        {
            "feline": [1.0, 0.0],
            "Pets A cat lives here.": [0.9, 0.1],
            "Cars A red car.": [0.0, 1.0],
            "Kitten Has a kitten.": [1.0, 0.0],
        }
    )

    found = memories(embeddings).recall("feline", NOW)

    assert [item["id"] for item in found] == ["m3", "m1"]
    assert RECALL_MEANING > 0


@pytest.mark.spec("REM-5")
def test_the_portrait_is_cut_to_its_word_limit(home_store):
    assert Portrait.read() == ""

    Portrait.write(" ".join(["word"] * (PORTRAIT_WORDS + 20)))

    assert len(Portrait.read().split()) == PORTRAIT_WORDS
    assert home_store.get("dream", "portrait") == Portrait.read()


@pytest.mark.spec("REM-7")
def test_memory_lists_living_memories_newest_first_with_the_state(home_store, capsys):
    older = memories().add("fact", "Uses uv", "The person uses uv for Python.", ["e1"], NOW)
    newer = memories().add("person", "Likes short answers", "Keep replies short.", ["e1"], NOW)
    memories().update(newer["id"], {"last_seen": "2026-11-12T08:00:00"})
    gone = memories().add("tool", "Uses pip", "Old habit.", ["e1"], NOW)
    memories().retire(gone["id"], "uv took its place")
    Scratchpad.write(chat_episode(), NOW)
    home_store.put("dream", "state", {"last": "2026-11-10T03:00:00"})

    show_memories()

    out = capsys.readouterr().out
    assert out.index("m2 person: Likes short answers") < out.index(f"{older['id']} fact: Uses uv")
    assert "Uses pip" not in out
    assert "1 fresh episode · 1 retired memory · last dream 2026-11-10T03:00:00" in out
    assert (
        "memory show ID" in out and "memory forget ID" in out and "memory portrait" in out and "dream" in out
    )


@pytest.mark.spec("REM-7")
def test_memory_comes_a_page_at_a_time(home_store, capsys, monkeypatch):
    monkeypatch.setattr("tinkerkit.settings.PER_PAGE", 2)
    for number in range(3):
        memories().add("fact", f"Fact {number}", "text", ["e1"], NOW + datetime.timedelta(hours=number))

    show_memories(2)

    out = capsys.readouterr().out
    assert "Fact 0" in out and "Fact 2" not in out


@pytest.mark.spec("REM-7")
def test_memory_with_nothing_remembered_says_a_dream_makes_them(home_store, capsys):
    show_memories()

    out = capsys.readouterr().out
    assert "a dream makes them from what you do" in out and "no dream yet" in out


@pytest.mark.spec("REM-8")
def test_show_gives_a_memory_whole_with_its_sources_and_gone_ones(home_store, capsys):
    Scratchpad.write(chat_episode(), NOW)
    memory = memories().add("fact", "Lists files", "The person lists files often.", ["e1", "e9"], NOW)

    show_remembered(memory["id"])

    out = capsys.readouterr().out
    assert "Lists files" in out and "The person lists files often." in out
    assert "e1 2026-11-11T12:00:00: list the files" in out and "e9: gone" in out


@pytest.mark.spec("REM-8")
def test_show_gives_an_episode_whole(home_store, capsys):
    Scratchpad.write(chat_episode(), NOW)

    show_remembered("e1")

    out = capsys.readouterr().out
    assert "list the files" in out and "Two files." in out and "a.py" in out


@pytest.mark.spec("REM-8")
def test_show_and_forget_refuse_an_unknown_id(home_store):
    for view in (show_remembered, lambda item_id: forget(item_id, yes=True)):
        with pytest.raises(SystemExit, match="no memory or episode m7"):
            view("m7")


@pytest.mark.spec("REM-9")
def test_forget_deletes_a_memory_or_an_episode_after_saying_what(home_store, capsys):
    Scratchpad.write(chat_episode(), NOW)
    memory = memories().add("fact", "Lists files", "text", ["e1"], NOW)

    forget(memory["id"], yes=True)
    forget("e1", yes=True)

    out = capsys.readouterr().out
    assert "memory m1: Lists files" in out and "episode e1" in out
    assert home_store.get("memories", "m1") is None and home_store.get("episodes", "e1") is None


@pytest.mark.spec("REM-9")
def test_forget_without_yes_off_a_terminal_deletes_nothing(home_store):
    memories().add("fact", "Lists files", "text", ["e1"], NOW)

    with pytest.raises(SystemExit, match="Nothing deleted"):
        forget("m1")

    assert home_store.get("memories", "m1") is not None


@pytest.mark.spec("REM-10")
def test_you_shows_the_portrait_and_its_person_memories(home_store, capsys):
    Portrait.write("A Python developer who likes short answers.")
    memories().add("person", "Likes short answers", "text", ["e1"], NOW)
    memories().add("fact", "Uses uv", "text", ["e1"], NOW)

    show_portrait()

    out = capsys.readouterr().out
    assert "A Python developer who likes short answers." in out
    assert "m1 person: Likes short answers" in out and "Uses uv" not in out


@pytest.mark.spec("REM-10")
def test_you_without_a_portrait_says_a_dream_writes_it(home_store, capsys):
    show_portrait()

    assert "No portrait yet" in capsys.readouterr().out


@pytest.mark.spec("REM-12")
def test_same_answer_finds_the_answer_memory_without_counting_a_recall(home_store):
    kept = memories().add("answer", "what is the capital of France?", "Paris.", ["e1"], NOW, "always")

    found = memories().same_answer("what is the capital of France?")

    assert found["id"] == kept["id"] and found["recalled"] == 0
    assert memories().same_answer("what is the capital of Peru?") is None


@pytest.mark.spec("REM-12")
def test_same_answer_finds_a_shouted_punctuated_request(home_store):
    kept = memories().add("answer", "how tall is it?", "330 metres.", ["e1"], NOW, "always")

    assert memories().same_answer("HOW TALL is it!!")["id"] == kept["id"]


@pytest.mark.spec("REM-12")
def test_same_answer_embeds_plain_words(home_store):
    memories().add("answer", "How tall is it?", "330 metres.", ["e1"], NOW, "always")
    lookup = memories()
    seen = []
    lookup.retriever.similarities = lambda request, questions: seen.append((request, questions)) or [1.0]

    lookup.same_answer("HOW TALL is it!!")

    assert seen == [("how tall is it", ["how tall is it"])]


@pytest.mark.spec("REM-12")
def test_matching_questions_returns_every_match_by_index(home_store):
    lookup = memories()
    lookup.retriever.similarities = lambda request, questions: [0.95, 0.2, 0.92]

    matches = lookup.matching_questions(
        "How tall is it?", ["how tall is it", "how tall is the tower", "HOW TALL is it!!"]
    )

    assert matches == {0: 0.95, 2: 0.92}


@pytest.mark.spec("REM-12")
def test_matching_questions_leaves_out_a_question_missing_a_telling_word(home_store):
    lookup = memories()
    seen = []
    lookup.retriever.similarities = lambda request, questions: (
        seen.append(questions) or [1.0] * len(questions)
    )

    matches = lookup.matching_questions("how many feet is it", ["how many km is it", "how many feet is it"])

    assert matches == {1: 1.0}
    assert seen == [["how many feet is it"]]


@pytest.mark.spec("REM-12")
def test_matching_questions_leaves_out_a_close_question_below_the_threshold(home_store):
    lookup = memories()
    lookup.retriever.similarities = lambda request, questions: [0.89]

    assert lookup.matching_questions("how tall is it", ["how tall is it really"]) == {}


@pytest.mark.spec("REM-12")
def test_matching_questions_falls_back_to_word_similarity(home_store):
    matches = memories().matching_questions(
        "HOW TALL is it!!", ["how tall is it?", "how tall is the tower in paris"]
    )

    assert list(matches) == [0] and matches[0] >= 0.9


@pytest.mark.spec("REM-12")
def test_matching_questions_of_no_questions_is_empty_without_embedding(home_store):
    lookup = memories()
    seen = []
    lookup.retriever.similarities = lambda request, questions: seen.append(questions) or []

    assert lookup.matching_questions("how tall is it", []) == {}
    assert lookup.matching_questions("how many feet", ["how many km"]) == {}
    assert seen == []
