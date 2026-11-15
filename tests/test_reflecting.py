import datetime
import json

import pytest
from conftest import ask, confirmed_trait, replying

from tinkerkit.prompts import Prompts
from tinkerkit.reflecting import Reflection
from tinkerkit.remembering import Memories, Scratchpad, Traits
from tinkerkit.retrieval import Retriever
from tinkerkit.settings import REFLECT_EPISODES, REFLECT_ROUNDS, REFLECT_THOUGHTS, Home

NOW = datetime.datetime(2026, 11, 11, 12, 0, 0)
EARLIER = datetime.datetime(2026, 11, 1, 9, 0, 0)


def dreamt_episode(message="list the files", at=EARLIER):
    kept = Scratchpad.write(
        {"chat": "telegram:1", "message": message, "reply": "Done.", "status": "answered"}, at
    )
    Scratchpad.dreamt([kept["id"]], at, {kept["id"]: message})
    return Home.store().get(Scratchpad.EPISODES, kept["id"])


def given(asked):
    return json.loads(asked[-1][1]["content"])


def added(do, sources, about="person", question="", thought="", expected=""):
    return {
        "about": about,
        "when": "",
        "do": do,
        "sources": sources,
        "question": question,
        "thought": thought,
        "expected": expected,
    }


def opened(text, about="person", thought="", expected=""):
    return {"about": about, "text": text, "thought": thought, "expected": expected}


@pytest.mark.spec("REF-1")
def test_a_round_is_given_both_sides_the_evidence_the_thoughts_and_how_many_wait(home_store, monkeypatch):
    first = dreamt_episode("I write Rust at work")
    confirmed = confirmed_trait("writes Rust at work")
    retired = confirmed_trait("likes long answers", about="tinker")
    Traits(Retriever(None)).apply([{"op": "reject", "id": retired["id"], "why": "never said so"}], NOW)
    person = Memories(Retriever(None)).add(
        "person", "works in Rust", "works in Rust", [first["id"]], NOW, None
    )
    Memories(Retriever(None)).add("fact", "the sky is blue", "the sky is blue", [first["id"]], NOW, None)
    ask("What do you build?", sources=[], thought="work is unknown", expected="tools", now=NOW)
    asked = replying(monkeypatch, {"thought": "now", "add": [], "questions": [], "done": True})

    Reflection(thoughts=["before"]).round(NOW)

    content = given(asked)
    assert asked[-1][0]["content"] == Prompts.REFLECT_SYSTEM
    assert [trait["id"] for trait in content["person"]["confirmed"]] == [confirmed["id"]]
    assert content["person"]["pending"] == [
        {"text": "What do you build?", "thought": "work is unknown", "expected": "tools"}
    ]
    assert content["tinker"]["retired"][0]["why"] == "never said so"
    assert content["tinker"]["confirmed"] == []
    assert content["evidence"]["episodes"] == [{"id": first["id"], "gist": "I write Rust at work"}]
    assert [memory["id"] for memory in content["evidence"]["memories"]] == [person["id"]]
    assert content["thoughts"] == ["before"]
    assert content["waiting"] == 1


@pytest.mark.spec("REF-1")
def test_a_round_shows_only_the_newest_dreamt_episodes(home_store, monkeypatch):
    for day in range(REFLECT_EPISODES + 2):
        dreamt_episode(f"episode {day}", EARLIER + datetime.timedelta(days=day))
    Scratchpad.write({"chat": "telegram:1", "message": "fresh", "reply": "Done.", "status": "answered"}, NOW)
    asked = replying(monkeypatch, {"thought": "", "add": [], "questions": [], "done": True})

    Reflection().round(NOW)

    gists = [episode["gist"] for episode in given(asked)["evidence"]["episodes"]]
    assert len(gists) == REFLECT_EPISODES
    assert gists[0] == f"episode {REFLECT_EPISODES + 1}"
    assert "fresh" not in gists


@pytest.mark.spec("REF-1")
def test_a_round_keeps_a_candidate_with_its_confirm_question_and_an_open_question(home_store, monkeypatch):
    first = dreamt_episode("I write Rust at work")
    logs = []
    replying(
        monkeypatch,
        {
            "thought": "work shows, goals do not",
            "add": [
                added(
                    "writes Rust at work",
                    [first["id"]],
                    "person",
                    "Do you write Rust at work?",
                    "said so",
                    "yes",
                ),
                added("answers in one line", [first["id"]], "robot", "Short?"),
                added("answers briefly", ["e99"], "tinker", "Short?"),
            ],
            "questions": [
                opened("What are you building toward?", "person", "goals unknown", "a product"),
                {},
            ],
            "done": False,
        },
    )

    reflection = Reflection(logged=lambda name, record: logs.append((name, record["id"])))
    kept = reflection.round(NOW)

    trait = Traits().candidates()[0]
    assert [candidate["do"] for candidate in Traits().candidates()] == ["writes Rust at work"]
    assert kept["thought"] == "work shows, goals do not"
    assert [(question["kind"], question["text"]) for question in kept["kept"]] == [
        ("confirm", "Do you write Rust at work?"),
        ("open", "What are you building toward?"),
    ]
    confirm, open_question = kept["kept"]
    assert (confirm["traits"], confirm["thought"], confirm["expected"]) == ([trait["id"]], "said so", "yes")
    assert (open_question["about"], open_question["expected"]) == ("person", "a product")
    assert [name for name, _ in logs] == ["add", "question", "question"]
    assert reflection.thoughts == ["work shows, goals do not"]


@pytest.mark.spec("REF-1")
def test_a_round_the_model_fails_gives_none(home_store, monkeypatch):
    replying(monkeypatch, ["not a dict"])

    assert Reflection().round(NOW) is None


@pytest.mark.spec("REF-2")
def test_run_stops_on_done_and_gives_back_every_round_and_thought(home_store, monkeypatch):
    first = dreamt_episode()
    asked = replying(
        monkeypatch,
        {"thought": "one", "add": [], "questions": [opened("What do you build?")], "done": False},
        {
            "thought": "two",
            "add": [],
            "questions": [opened("Which language do you like best?")],
            "done": True,
        },
    )
    earlier = [f"old {number}" for number in range(REFLECT_THOUGHTS + 3)]
    reflection = Reflection(thoughts=earlier)

    rounds = reflection.run(NOW)

    assert [round_["thought"] for round_ in rounds] == ["one", "two"]
    assert [len(round_["kept"]) for round_ in rounds] == [1, 1]
    assert reflection.thoughts == ["one", "two"]
    assert given(asked)["thoughts"] == earlier[-REFLECT_THOUGHTS:] + ["one"]
    assert first["dreamt"]


@pytest.mark.spec("REF-2")
def test_run_stops_after_a_round_that_keeps_nothing_new(home_store, monkeypatch):
    asked = replying(
        monkeypatch,
        {"thought": "one", "add": [], "questions": [opened("What do you build?")], "done": False},
        {"thought": "two", "add": [], "questions": [opened("What do you build?")], "done": False},
        {"thought": "three", "add": [], "questions": [opened("Never asked")], "done": False},
    )

    rounds = Reflection().run(NOW)

    assert len(asked) == 2
    assert [len(round_["kept"]) for round_ in rounds] == [1, 0]


@pytest.mark.spec("REF-2")
def test_run_stops_after_the_rounds_allowed_or_a_failed_round(home_store, monkeypatch):
    topics = (
        "apple bridge cobalt dolphin ember falcon glacier harbor iris juniper kettle lantern meadow nectar "
    )
    topics += "orchid pepper quartz raven saffron tundra umber violet walnut xenon yarrow zephyr"
    replies = [
        {"thought": topic, "add": [], "questions": [opened(f"{topic}?")]}
        for topic in topics.split()[: REFLECT_ROUNDS + 5]
    ]
    asked = replying(monkeypatch, *replies)

    assert len(Reflection().run(NOW)) == REFLECT_ROUNDS
    assert len(asked) == REFLECT_ROUNDS

    asked = replying(
        monkeypatch, {"thought": "one", "questions": [opened("Something new entirely?")]}, ["no"]
    )

    assert [round_["thought"] for round_ in Reflection().run(NOW)] == ["one"]
    assert len(asked) == 2
