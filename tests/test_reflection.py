import datetime
import json

import pytest
from conftest import ask, confirmed_trait, replying
from test_memories import CHESS, HELIX, HELIX_AGAIN, meanings_of_helix_and_chess

from tinkerkit.jobs import Jobs
from tinkerkit.memory.memories import Memories, Notes, Questions, Scratchpad, Traits
from tinkerkit.memory.reflection import Reflection, gaps, self_facts
from tinkerkit.memory.routes import Routes, forget_route
from tinkerkit.prompts import Prompts
from tinkerkit.ranking import Embeddings, Retriever
from tinkerkit.settings import (
    REFLECT_ANSWERED,
    REFLECT_EPISODES,
    REFLECT_GAPS,
    REFLECT_ROUNDS,
    REFLECT_THOUGHTS,
    Home,
)

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
    answered = ask("Which editor do you use?", sources=[], thought="tools are unknown", now=NOW)
    Questions().answer(answered["id"], "Helix", NOW)
    asked = replying(monkeypatch, {"thought": "now", "add": [], "questions": [], "done": True})

    Reflection(thoughts=["before"]).round(NOW)

    content = given(asked)
    assert asked[-1][0]["content"] == Prompts.REFLECT_SYSTEM
    assert [trait["id"] for trait in content["person"]["confirmed"]] == [confirmed["id"]]
    assert content["person"]["pending"] == [
        {"text": "What do you build?", "thought": "work is unknown", "expected": "tools"}
    ]
    assert content["person"]["answered"] == [{"text": "Which editor do you use?", "answer": "Helix"}]
    assert content["tinker"]["answered"] == []
    assert list(content["person"]) == ["confirmed", "candidates", "pending", "answered", "retired"]
    assert content["tinker"]["retired"][0]["why"] == "never said so"
    assert content["tinker"]["confirmed"] == []
    assert content["evidence"]["episodes"] == [{"id": first["id"], "gist": "I write Rust at work"}]
    assert [memory["id"] for memory in content["evidence"]["memories"]] == [person["id"]]
    assert content["thoughts"] == ["before"]
    assert content["waiting"] == 1


@pytest.mark.spec("REF-1")
def test_a_round_shows_only_the_newest_answered_questions_of_each_side(home_store, monkeypatch):
    for number in range(REFLECT_ANSWERED + 2):
        question = ask(f"Question number {number}?", sources=[], now=NOW)
        Questions().answer(question["id"], f"answer {number}", NOW)
    tinkers = ask("How short should replies be?", sources=[], about="tinker", now=NOW)
    Questions().answer(tinkers["id"], "short", NOW)
    asked = replying(monkeypatch, {"thought": "now", "add": [], "questions": [], "done": True})

    Reflection().round(NOW)

    content = given(asked)
    assert [question["answer"] for question in content["person"]["answered"]] == [
        f"answer {number}" for number in range(2, REFLECT_ANSWERED + 2)
    ]
    assert content["tinker"]["answered"] == [{"text": "How short should replies be?", "answer": "short"}]


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


@pytest.mark.spec("REM-15")
def test_a_round_drops_an_open_question_meaning_an_answered_one(home_store, monkeypatch):
    settled = ask(HELIX, now=NOW)
    Questions().answer(settled["id"], "Helix", NOW)
    monkeypatch.setattr(Embeddings, "configured", staticmethod(meanings_of_helix_and_chess))
    replying(
        monkeypatch,
        {"thought": "tools", "add": [], "questions": [opened(HELIX_AGAIN), opened(CHESS)], "done": False},
    )

    kept = Reflection().round(NOW)

    assert [question["text"] for question in kept["kept"]] == [CHESS]


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

    rounds = list(reflection.run(NOW))

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

    rounds = list(Reflection().run(NOW))

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

    assert len(list(Reflection().run(NOW))) == REFLECT_ROUNDS
    assert len(asked) == REFLECT_ROUNDS

    asked = replying(
        monkeypatch, {"thought": "one", "questions": [opened("Something new entirely?")]}, ["no"]
    )

    assert [round_["thought"] for round_ in Reflection().run(NOW)] == ["one"]
    assert len(asked) == 2


def at_hour(hour):
    return datetime.datetime(2026, 11, 2, hour, 0, 0)


def route_added(pattern, line, hour):
    return Routes().apply([{"op": "add", "pattern": pattern, "line": line, "source": "chat"}], at_hour(hour))[
        0
    ]


def route_retired(route, why, hour):
    Routes().apply([{"op": "retire", "id": route["id"], "why": why}], at_hour(hour))


def chat_episode(message, status, hour, route=""):
    episode = {"chat": "telegram:1", "message": message, "reply": "", "status": status, "route": route}
    return Scratchpad.write(episode, at_hour(hour))


def instruction_episode(request, code, hour):
    episode = {"chat": "telegram:1", "request": request, "line": "ls", "output": "", "code": code}
    return Scratchpad.write(episode, at_hour(hour))


def build_episode(task, status, hour):
    return Scratchpad.write({"chat": "telegram:1", "task": task, "status": status}, at_hour(hour))


@pytest.mark.spec("REF-4")
def test_gaps_list_the_routes_the_person_turned_down_newest_first(home_store):
    older = route_added("show the files", "ls", 1)
    newer = route_added("show the disk", "df -h", 2)
    forgotten = route_added("show the time", "date", 3)
    replaced = route_added("show the date", "date", 4)
    route_retired(older, "no", 5)
    route_retired(newer, "corrected", 6)
    forget_route(forgotten["id"])
    route_added("show the date", "date +%F", 7)

    turned_down = gaps()["turned down"]

    assert turned_down == [
        {"id": newer["id"], "pattern": "show the disk", "line": "df -h", "why": "corrected"},
        {"id": older["id"], "pattern": "show the files", "line": "ls", "why": "no"},
    ]
    assert replaced["id"] not in [route["id"] for route in turned_down]


@pytest.mark.spec("REF-4")
def test_gaps_list_each_living_route_with_failed_chats_and_how_often(home_store):
    failing = route_added("show the files", "ls", 1)
    working = route_added("show the disk", "df -h", 2)
    Routes().apply([{"op": "use", "id": failing["id"]}], at_hour(3))
    chat_episode("show the files", "failed", 4, failing["id"])
    dreamt = chat_episode("show the files here", "failed", 5, failing["id"])
    Scratchpad.dreamt([dreamt["id"]], at_hour(6), {dreamt["id"]: "listing failed"})
    chat_episode("show the disk", "answered", 7, working["id"])

    assert gaps()["failing"] == [
        {"id": failing["id"], "pattern": "show the files", "line": "ls", "uses": 1, "failed": 2}
    ]


@pytest.mark.spec("REF-4")
def test_gaps_list_failed_chats_instructions_and_builds_newest_first(home_store):
    chat = chat_episode("what is up", "failed", 1)
    chat_episode("hello", "answered", 2)
    instruction = instruction_episode("list files", 2, 3)
    instruction_episode("list files", 0, 4)
    build = build_episode("make a game", "blocked", 5)
    build_episode("make a site", "done", 6)
    Scratchpad.write({"kind": "interact", "status": "failed"}, at_hour(7))
    Scratchpad.dreamt([chat["id"]], at_hour(8), {chat["id"]: "a greeting failed"})

    assert gaps()["failed"] == [
        {"id": build["id"], "gist": "make a game"},
        {"id": instruction["id"], "gist": "list files"},
        {"id": chat["id"], "gist": "a greeting failed"},
    ]


@pytest.mark.spec("REF-4")
def test_gaps_keep_at_most_the_newest_few_of_each_kind(home_store):
    newest = [chat_episode(f"question {hour}", "failed", hour).get("id") for hour in range(REFLECT_GAPS + 2)]

    assert [episode["id"] for episode in gaps()["failed"]] == newest[::-1][:REFLECT_GAPS]


@pytest.mark.spec("REF-5")
def test_self_facts_count_routes_failures_and_builds_each_time(home_store):
    used = route_added("show the files", "ls", 1)
    Routes().apply([{"op": "confirm", "id": used["id"]}, {"op": "use", "id": used["id"]}], at_hour(2))
    Routes().apply([{"op": "use", "id": used["id"]}], at_hour(2))
    route_added("show the disk", "df -h", 3)
    route_retired(route_added("show the time", "date", 4), "no", 5)
    chat_episode("show the files", "failed", 6, used["id"])
    chat_episode("show the files", "answered", 7, used["id"])
    instruction_episode("list files", 0, 8)
    build_episode("make a game", "blocked", 9)
    build_episode("make a site", "done", 10)

    assert self_facts() == {
        "routes living": 2,
        "routes confirmed": 1,
        "route uses": 2,
        "routes turned down": 1,
        "failed": {
            "chat": {"failed": 1, "all": 2},
            "instruction": {"failed": 0, "all": 1},
            "build": {"failed": 1, "all": 2},
        },
        "builds done": {"done": 1, "all": 2},
    }

    build_episode("make a tool", "done", 11)

    assert self_facts()["builds done"] == {"done": 2, "all": 3}


def failing_route(pattern="show the files", line="ls"):
    route = route_added(pattern, line, 1)
    chat_episode(pattern, "failed", 2, route["id"])
    return route


def reflected(**reply):
    return {"thought": "", "add": [], "questions": [], "done": False} | reply


@pytest.mark.spec("REF-1", "REF-3")
def test_a_round_leaves_out_every_part_that_holds_nothing_and_the_gaps_guide(home_store, monkeypatch):
    asked = replying(monkeypatch, reflected(done=True))

    Reflection().round(NOW)

    assert asked[-1][0]["content"] == Prompts.REFLECT_SYSTEM
    assert given(asked).keys().isdisjoint({"gaps", "self", "thoughts", "waiting"})


@pytest.mark.spec("REF-1", "REF-3", "REF-4", "REF-5")
def test_a_round_shows_gaps_and_self_with_the_kept_notes_and_explains_them(home_store, monkeypatch):
    failing_route()
    Notes.keep("I often miss file listings", NOW)
    asked = replying(monkeypatch, reflected(done=True))

    Reflection().round(NOW)

    content = given(asked)
    assert asked[-1][0]["content"] == Prompts.REFLECT_SYSTEM + Prompts.REFLECT_GAPS_GUIDE
    assert content["gaps"] == gaps()
    assert content["self"] == self_facts() | {"notes": Notes.listed()}


@pytest.mark.spec("REF-1", "REF-6")
def test_a_round_keeps_its_notes_and_retires_only_living_routes_named_in_gaps(home_store, monkeypatch):
    failing = failing_route()
    other = route_added("show the disk", "df -h", 3)
    replying(
        monkeypatch,
        reflected(
            notes=["I miss file listings", "  "],
            retire=[
                {"id": failing["id"], "why": "keeps failing"},
                {"id": other["id"], "why": "not named"},
                {"id": "r99", "why": "unknown"},
            ],
        ),
    )

    kept = Reflection().round(NOW)

    assert kept["notes"] == ["I miss file listings"]
    assert [note["text"] for note in Notes.listed()] == ["I miss file listings"]
    assert kept["retired"] == [{"id": failing["id"], "why": "keeps failing"}]
    assert Home.store().get(Routes.ROUTES, failing["id"])["status"] == "retired"
    assert Home.store().get(Routes.ROUTES, other["id"])["status"] != "retired"
    assert gaps()["turned down"] == []


@pytest.mark.spec("REF-1")
def test_a_loop_queues_at_most_one_learn(home_store, monkeypatch):
    replying(
        monkeypatch,
        reflected(learn="git rebasing"),
        reflected(learn="docker networks", notes=["I lack container know-how"]),
        reflected(done=True),
    )

    rounds = list(Reflection().run(NOW))

    assert [round_["learn"] for round_ in rounds] == ["git rebasing", "", ""]
    assert [job["arguments"] for job in Jobs.every()] == [["learn", "git rebasing"]]


@pytest.mark.spec("REF-2")
def test_run_goes_on_after_a_round_that_keeps_only_a_note(home_store, monkeypatch):
    asked = replying(
        monkeypatch,
        reflected(notes=["I answer chats well"]),
        reflected(),
        reflected(notes=["Something else entirely about builds"]),
    )

    rounds = list(Reflection().run(NOW))

    assert len(asked) == 2
    assert [round_["notes"] for round_ in rounds] == [["I answer chats well"], []]


@pytest.mark.spec("REF-2", "REF-6")
def test_run_stops_after_a_round_whose_only_note_is_alike_to_a_kept_one(home_store, monkeypatch):
    Notes.keep("I answer chats well", NOW)
    asked = replying(
        monkeypatch,
        reflected(notes=["I answer chats well"]),
        reflected(notes=["Something else entirely about builds"]),
    )

    rounds = list(Reflection().run(NOW))

    assert len(asked) == 1
    assert [round_["notes"] for round_ in rounds] == [[]]
    assert [note["text"] for note in Notes.listed()] == ["I answer chats well"]
