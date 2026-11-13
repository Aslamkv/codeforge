import datetime

import pytest

from tinkerkit.remembering import (
    Memories,
    Questions,
    Scratchpad,
    Traits,
    forget,
    show_memories,
    show_remembered,
    show_traits,
)
from tinkerkit.retrieval import Retriever
from tinkerkit.settings import (
    EPISODE_FOUND_CHARS,
    EPISODE_GIST_CHARS,
    EPISODE_KEPT_DAYS,
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


def add_trait(do="Writes Rust daily", when="", sources=("e1",), **more):
    return {"op": "add", "about": "person", "when": when, "do": do, "sources": list(sources), **more}


@pytest.mark.spec("REM-5", "REM-14")
def test_an_added_trait_is_a_candidate_with_the_next_id_and_its_history(home_store):
    added, second = Traits().apply(
        [add_trait(), add_trait("Answer in short replies", "on Telegram", ["m2"], about="tinker")], NOW
    )

    assert added == {
        "id": "t1",
        "about": "person",
        "when": "",
        "do": "Writes Rust daily",
        "gist": "",
        "status": "candidate",
        "sources": ["e1"],
        "history": [{"event": "add", "at": "2026-11-11T12:00:00", "text": "Writes Rust daily"}],
    }
    assert second["id"] == "t2" and second["about"] == "tinker" and second["when"] == "on Telegram"
    assert home_store.get("traits", "t1") == added
    assert [trait["id"] for trait in Traits().candidates()] == ["t1", "t2"]


@pytest.mark.spec("REM-14")
def test_a_trait_the_person_said_is_added_confirmed(home_store):
    Traits().apply([add_trait(confirmed=True)], NOW)

    assert [trait["id"] for trait in Traits().confirmed()] == ["t1"]
    assert Traits().candidates() == []


@pytest.mark.spec("REM-14")
def test_support_adds_only_new_sources_to_the_history(home_store):
    Traits().apply([add_trait()], NOW)

    (supported,) = Traits().apply([{"op": "support", "id": "t1", "sources": ["e1", "e7"]}], NOW)

    assert supported["sources"] == ["e1", "e7"]
    assert supported["history"][-1] == {"event": "support", "at": "2026-11-11T12:00:00", "text": "e1 e7"}


@pytest.mark.spec("REM-5", "REM-14")
def test_confirm_and_reject_set_the_status_and_a_retired_trait_takes_nothing_more(home_store):
    Traits().apply([add_trait(), add_trait("Plays chess")], NOW)

    Traits().apply([{"op": "confirm", "id": "t1"}, {"op": "reject", "id": "t2", "why": "not true"}], NOW)
    unchanged = Traits().apply(
        [{"op": "confirm", "id": "t2"}, {"op": "support", "id": "t2", "sources": ["e9"]}], NOW
    )

    rejected = home_store.get("traits", "t2")
    assert unchanged == []
    assert rejected["status"] == "retired" and rejected["why"] == "not true"
    assert rejected["history"][-1] == {"event": "reject", "at": "2026-11-11T12:00:00", "text": "not true"}
    assert [trait["id"] for trait in Traits().confirmed()] == ["t1"]
    assert [trait["id"] for trait in Traits().living()] == ["t1"]
    assert [trait["id"] for trait in Traits().all()] == ["t1", "t2"]


@pytest.mark.spec("REM-14")
def test_amend_rewrites_when_and_do_and_clears_the_gist(home_store):
    Traits().apply([add_trait()], NOW)
    home_store.update("traits", "t1", lambda trait: dict(trait, gist="Rust"))

    (amended,) = Traits().apply([{"op": "amend", "id": "t1", "when": "at work"}], NOW)

    assert amended["when"] == "at work" and amended["do"] == "Writes Rust daily" and amended["gist"] == ""
    assert amended["history"][-1] == {
        "event": "amend",
        "at": "2026-11-11T12:00:00",
        "text": "at work Writes Rust daily",
    }


@pytest.mark.spec("REM-5", "DRM-10")
def test_a_gist_is_written_without_a_history_entry(home_store):
    (added,) = Traits().apply([add_trait()], NOW)

    gisted = Traits().gisted("t1", "Rust daily")

    assert gisted == dict(added, gist="Rust daily")
    assert home_store.get("traits", "t1") == gisted


@pytest.mark.spec("REM-14")
def test_deltas_with_an_unknown_id_or_op_or_no_source_are_dropped(home_store):
    changed = Traits().apply(
        [
            add_trait(sources=[]),
            {"op": "support", "id": "t1", "sources": ["e1"]},
            {"op": "confirm", "id": "t9"},
            {"op": "forget", "id": "t1"},
        ],
        NOW,
    )
    Traits().apply([add_trait(), {"op": "support", "id": "t1", "sources": []}], NOW)

    assert changed == []
    assert [trait["id"] for trait in Traits().all()] == ["t1"]
    assert home_store.get("traits", "t1")["history"][-1]["event"] == "add"


@pytest.mark.spec("REM-14")
def test_an_add_like_a_living_trait_supports_it_and_one_like_a_retired_trait_is_dropped(home_store):
    Traits().apply([add_trait("Writes Rust code every day"), add_trait("Plays chess on weekends")], NOW)
    Traits().apply([{"op": "reject", "id": "t2", "why": "stopped"}], NOW)

    (supported,) = Traits().apply(
        [add_trait("Writes Rust code every day", sources=["e5"]), add_trait("Plays chess on weekends")], NOW
    )

    assert supported["id"] == "t1" and supported["sources"] == ["e1", "e5"]
    assert [trait["id"] for trait in Traits().all()] == ["t1", "t2"]


@pytest.mark.spec("REM-16")
def test_relevant_traits_put_always_ones_first_by_sources_then_the_conditional_ones_matching_the_text(
    home_store,
):
    Traits().apply(
        [
            add_trait("Writes Rust daily", confirmed=True),
            add_trait("Prefers metric units", sources=["e1", "e2", "e3"], confirmed=True),
            add_trait("Use short replies", "on Telegram", confirmed=True),
            add_trait("Explain each Python error", "when debugging Python", confirmed=True),
            add_trait("Likes jazz records", "music talk", confirmed=True),
            add_trait("Drinks green tea"),
            add_trait("Plays chess on weekends", confirmed=True),
        ],
        NOW,
    )
    home_store.update("traits", "t4", lambda trait: dict(trait, gist="explain errors"))
    Traits().apply([{"op": "reject", "id": "t7", "why": "stopped"}], NOW)

    relevant = Traits(Retriever(None)).relevant("Help me fix this Python error while debugging")

    assert relevant == "- Prefers metric units\n- Writes Rust daily\n- when debugging Python: explain errors"


@pytest.mark.spec("REM-16")
def test_relevant_traits_stop_at_the_first_line_past_the_room(home_store):
    Traits().apply(
        [
            add_trait("Writes Rust daily", sources=["e1", "e2"], confirmed=True),
            add_trait("Prefers metric units", confirmed=True),
            add_trait("Use short replies", confirmed=True),
        ],
        NOW,
    )

    traits = Traits(Retriever(None))
    first, two = "- Writes Rust daily", "- Writes Rust daily\n- Prefers metric units"

    assert traits.relevant("anything", room=len(two)) == two
    assert traits.relevant("anything", room=len(first)) == first
    assert traits.relevant("anything", room=5) == ""


@pytest.mark.spec("REM-16")
def test_relevant_traits_are_empty_without_confirmed_traits(home_store):
    Traits().apply([add_trait()], NOW)

    assert Traits(Retriever(None)).relevant("Writes Rust daily") == ""


@pytest.mark.spec("REM-15")
def test_an_added_question_is_pending_with_the_next_id(home_store):
    question = Questions().add("confirm", "Do you write Rust daily?", ["yes", "no"], ["t1"], ["e1"], NOW)

    assert question == {
        "id": "q1",
        "kind": "confirm",
        "text": "Do you write Rust daily?",
        "suggestions": ["yes", "no"],
        "traits": ["t1"],
        "sources": ["e1"],
        "status": "pending",
        "answer": "",
        "at": "2026-11-11T12:00:00",
        "turn": 1,
        "skips": 0,
    }
    assert home_store.get("questions", "q1") == question


@pytest.mark.spec("REM-15")
def test_a_question_like_one_kept_is_dropped_whatever_its_status(home_store):
    Questions().add("open", "Which editor do you use at work?", [], [], ["e1"], NOW)
    Questions().answer("q1", "Helix", NOW)

    dropped = Questions().add("open", "Which editor do you use at work?", [], [], ["e2"], NOW)

    assert dropped is None
    assert list(home_store.items("questions")) == ["q1"]


@pytest.mark.spec("REM-15")
def test_pending_gives_confirms_first_the_best_supported_first_then_open_ones_in_order(home_store):
    Traits().apply(
        [add_trait("Writes Rust code every day"), add_trait("Plays chess on weekends", sources=["e1", "e2"])],
        NOW,
    )
    Questions().add("open", "Which editor do you use at work?", [], [], ["e1"], NOW)
    Questions().add("confirm", "Do you write Rust code every day?", ["yes", "no"], ["t1"], ["e1"], NOW)
    Questions().add("open", "What music helps you focus?", [], [], ["e2"], NOW)
    Questions().add("confirm", "Do you play chess on weekends?", ["yes", "no"], ["t2"], ["e2"], NOW)

    assert [question["id"] for question in Questions().pending()] == ["q4", "q2", "q1", "q3"]
    assert Questions().next()["id"] == "q4"


@pytest.mark.spec("REM-15")
def test_a_skipped_question_goes_behind_every_pending_one_still_pending(home_store):
    Traits().apply([add_trait("Writes Rust code every day")], NOW)
    Questions().add("confirm", "Do you write Rust code every day?", ["yes", "no"], ["t1"], ["e1"], NOW)
    Questions().add("open", "Which editor do you use at work?", [], [], ["e1"], NOW)

    skipped = Questions().skip("q1", NOW)

    assert skipped["status"] == "pending" and skipped["at"] == "2026-11-11T12:00:00"
    assert [question["id"] for question in Questions().pending()] == ["q2", "q1"]


@pytest.mark.spec("REM-15", "INT-4")
def test_skipped_questions_follow_the_order_they_were_skipped_whatever_their_kind_or_support(home_store):
    Traits().apply(
        [add_trait("Writes Rust code every day"), add_trait("Plays chess on weekends", sources=["e1", "e2"])],
        NOW,
    )
    Questions().add("confirm", "Do you write Rust code every day?", ["yes", "no"], ["t1"], ["e1"], NOW)
    Questions().add("confirm", "Do you play chess on weekends?", ["yes", "no"], ["t2"], ["e2"], NOW)
    Questions().add("open", "Which editor do you use at work?", [], [], ["e1"], NOW)

    Questions().skip("q2", NOW)
    assert [question["id"] for question in Questions().pending()] == ["q1", "q3", "q2"]

    Questions().skip("q3", NOW)
    Questions().skip("q1", NOW)
    assert [question["id"] for question in Questions().pending()] == ["q2", "q3", "q1"]


@pytest.mark.spec("REM-15")
def test_a_confirm_whose_traits_are_all_retired_no_longer_waits(home_store):
    Traits().apply([add_trait("Writes Rust code every day"), add_trait("Plays chess on weekends")], NOW)
    Questions().add("confirm", "Do you write Rust code every day?", ["yes", "no"], ["t1"], ["e1"], NOW)
    Questions().add("confirm", "Do you write Rust and play chess?", ["yes", "no"], ["t1", "t2"], ["e1"], NOW)

    Traits().apply([{"op": "reject", "id": "t1", "why": "not true"}], NOW)

    assert [question["id"] for question in Questions().pending()] == ["q2"]
    assert home_store.get("questions", "q1")["status"] == "pending"


@pytest.mark.spec("REM-15")
def test_an_answered_question_leaves_the_pending_ones(home_store):
    Questions().add("open", "Which editor do you use at work?", [], [], ["e1"], NOW)

    answered = Questions().answer("q1", "Helix", NOW)

    assert answered["status"] == "answered" and answered["answer"] == "Helix"
    assert answered["at"] == "2026-11-11T12:00:00"
    assert Questions().pending() == [] and Questions().next() is None


@pytest.mark.spec("REM-15")
def test_answer_or_skip_of_an_unknown_or_answered_question_changes_nothing(home_store):
    Questions().add("open", "Which editor do you use at work?", [], [], ["e1"], NOW)
    Questions().answer("q1", "Helix", NOW)

    assert Questions().answer("q9", "Vim", NOW) is None
    assert Questions().skip("q9", NOW) is None
    assert Questions().answer("q1", "Vim", NOW) is None
    assert Questions().skip("q1", NOW) is None
    assert home_store.get("questions", "q1")["answer"] == "Helix"
    assert list(home_store.items("questions")) == ["q1"]


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
    assert "memory show ID" in out and "memory forget ID" in out and "memory traits" in out and "dream" in out


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


@pytest.mark.spec("REM-9")
def test_forget_retires_a_trait_as_forgotten_without_asking(home_store, capsys):
    Traits().apply([{"op": "add", "about": "person", "when": "", "do": "keeps bees", "sources": ["e1"]}], NOW)

    forget("t1")

    trait = home_store.get("traits", "t1")
    assert trait["status"] == "retired" and trait["why"] == "forgotten"
    assert "Forgot t1." in capsys.readouterr().out


@pytest.mark.spec("REM-9")
def test_forget_refuses_an_unknown_or_retired_trait(home_store):
    Traits().apply([{"op": "add", "about": "person", "when": "", "do": "keeps bees", "sources": ["e1"]}], NOW)
    forget("t1")

    for trait_id in ("t1", "t7"):
        with pytest.raises(SystemExit, match=f"no trait {trait_id}"):
            forget(trait_id, yes=True)


@pytest.mark.spec("REM-10")
def test_traits_shows_the_confirmed_by_about_then_candidates_then_questions(home_store, capsys):
    Traits().apply(
        [
            {
                "op": "add",
                "about": "tinker",
                "when": "",
                "do": "answer in short lines",
                "sources": ["e1"],
                "confirmed": True,
            },
            {
                "op": "add",
                "about": "person",
                "when": "writing Python",
                "do": "uses uv",
                "sources": ["e1"],
                "confirmed": True,
            },
            {
                "op": "add",
                "about": "person",
                "when": "",
                "do": "keeps bees in the garden",
                "sources": ["e1", "e2"],
            },
            {
                "op": "add",
                "about": "person",
                "when": "",
                "do": "dislikes long meetings",
                "sources": ["e3"],
                "confirmed": True,
            },
        ],
        NOW,
    )
    Traits().apply([{"op": "reject", "id": "t4", "why": "wrong"}], NOW)
    Questions().add("confirm", "Do you keep bees?", [], ["t3"], ["e1"], NOW)

    show_traits()

    out = capsys.readouterr().out
    assert out.index("t2 writing Python: uses uv") < out.index("t1 answer in short lines")
    assert out.index("person") < out.index("tinker") < out.index("t1 answer in short lines")
    assert "t3 keeps bees in the garden · 2 sources" in out
    assert out.index("t1 answer in short lines") < out.index("t3 keeps bees")
    assert "dislikes long meetings" not in out
    assert "1 question waits: tinker interact asks it." in out


@pytest.mark.spec("REM-10")
def test_traits_without_any_says_a_dream_learns_them(home_store, capsys):
    show_traits()

    out = capsys.readouterr().out
    assert "No traits yet" in out and "dream" in out and "interact" in out


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
