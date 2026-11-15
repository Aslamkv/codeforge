import json

import pytest
from conftest import ask, quick_model_replying, replying
from test_dreaming import NOW

from tinkerkit.conversation import ALL_FOR_NOW, Turn
from tinkerkit.interviewing import UNREADABLE, Interview
from tinkerkit.model import OllamaError
from tinkerkit.prompts import Prompts
from tinkerkit.remembering import Questions, Scratchpad, Traits
from tinkerkit.settings import INTERACT_TURNS, Home


def trait(do, sources=("e1",), confirmed=False, about="person"):
    delta = {"op": "add", "about": about, "when": "", "do": do, "sources": list(sources)}
    return Traits().apply([dict(delta, confirmed=confirmed)])[0]


def opening(question):
    return f"💭 I've been thinking: {question['thought']} {question['text']} My guess: {question['expected']}"


def no_model_call(messages):
    raise AssertionError("the model was called")


def talked(say="", settled=True, **deltas):
    return {"say": say, "confirm": [], "reject": [], "amend": [], "add": [], "question": None, **deltas} | {
        "settled": settled
    }


@pytest.mark.spec("INT-1")
def test_a_pending_question_opens_with_its_thought_and_guess_with_no_model_call(home_store, monkeypatch):
    ask("When do you write Rust?", thought="You mention Rust a lot.", expected="On week_ends.")
    quick_model_replying(monkeypatch, no_model_call)
    assert Interview().turn("1") == Turn(
        "💭 I've been thinking: You mention Rust a lot. When do you write Rust? My guess: On week\\_ends.",
        False,
    )


@pytest.mark.spec("INT-2")
def test_with_nothing_pending_one_reflection_round_opens_the_first_question_it_kept(home_store, monkeypatch):
    asked = replying(
        monkeypatch,
        {
            "thought": "I never asked about editors.",
            "add": [],
            "questions": [
                {
                    "about": "person",
                    "text": "Which editor do you use?",
                    "thought": "You never named one.",
                    "expected": "Helix.",
                },
                {
                    "about": "person",
                    "text": "Do you code at night?",
                    "thought": "Late commits.",
                    "expected": "Often.",
                },
            ],
            "done": False,
        },
    )
    turn = Interview().turn("1")
    assert turn == Turn(
        "💭 I've been thinking: You never named one. Which editor do you use? My guess: Helix.", False
    )
    assert [messages[0]["content"] for messages in asked] == [Prompts.REFLECT_SYSTEM]
    assert [question["text"] for question in Questions().pending()] == [
        "Which editor do you use?",
        "Do you code at night?",
    ]


@pytest.mark.spec("INT-2")
def test_a_reflection_round_keeping_no_question_says_all_for_now_and_leaves(home_store, monkeypatch):
    asked = replying(monkeypatch, {"thought": "", "add": [], "questions": [], "done": True})
    assert Interview().turn("1") == Turn(ALL_FOR_NOW, True)
    assert len(asked) == 1 and Questions().all() == []


@pytest.mark.spec("INT-3")
def test_a_message_is_one_talk_call_with_traits_question_next_dialogue_and_message(home_store, monkeypatch):
    rust = trait("writes Rust", confirmed=True)
    brief = trait("answers briefly", confirmed=True, about="tinker")
    tabs = trait("uses tabs")
    question = ask(
        "Do you use tabs?",
        "confirm",
        traits=[tabs["id"]],
        thought="Your diffs show tabs.",
        expected="Tabs everywhere.",
    )
    ask("Which editor do you use?")
    turns = [
        {"chat": "1", "kind": "interact", "question": f"q{n}", "answer": f"a{n}", "say": f"s{n}"}
        for n in range(INTERACT_TURNS + 1)
    ]
    for episode in [*turns, dict(turns[0], chat="2")]:
        Scratchpad.write(episode, NOW)
    asked = replying(monkeypatch, talked("Noted.", settled=False))
    Interview().answered("1", "tabs, always", NOW)
    assert asked[0][0]["content"] == Prompts.INTERACT_TALK_SYSTEM
    assert json.loads(asked[0][1]["content"]) == {
        "traits": {
            "person": [{"id": rust["id"], "when": "", "do": "writes Rust"}],
            "tinker": [{"id": brief["id"], "when": "", "do": "answers briefly"}],
        },
        "question": {
            "text": question["text"],
            "thought": "Your diffs show tabs.",
            "expected": "Tabs everywhere.",
            "traits": [{"id": tabs["id"], "about": "person", "when": "", "do": "uses tabs"}],
        },
        "next": "Which editor do you use?",
        "dialogue": [
            {"question": turn["question"], "answer": turn["answer"], "say": turn["say"]}
            for turn in turns[-INTERACT_TURNS:]
        ],
        "message": "tabs, always",
    }


@pytest.mark.spec("INT-3")
def test_a_settled_answer_applies_its_deltas_answers_and_opens_the_next_after_the_say(
    home_store, monkeypatch
):
    rust, tabs, vim = trait("writes Rust", sources=("s1",)), trait("uses tabs"), trait("uses Vim daily")
    question = ask("Do you write Rust and use tabs?", "confirm", traits=[rust["id"], tabs["id"], vim["id"]])
    waiting = ask("Which editor do you use at home?")
    replying(
        monkeypatch,
        talked(
            "Spaces it is.",
            confirm=[rust["id"], "t9"],
            reject=[{"id": tabs["id"], "why": "spaces"}],
            amend=[{"id": vim["id"], "when": "at work", "do": "uses Vim"}],
            add=[{"about": "person", "when": "", "do": "indents with four spaces"}],
            question={"about": "person", "text": "Do you code on weekends?", "thought": "t", "expected": "e"},
        ),
    )
    turn = Interview().answered("1", "yes, but spaces", NOW)
    assert turn == Turn(f"Spaces it is.\n\n{opening(waiting)}", False)
    [episode] = Home.store().items(Scratchpad.EPISODES).values()
    kept = {t["id"]: t for t in Traits().all()}
    assert (kept[rust["id"]]["status"], kept[rust["id"]]["sources"]) == ("confirmed", ["s1", episode["id"]])
    assert (kept[tabs["id"]]["status"], kept[tabs["id"]]["why"]) == ("retired", "spaces")
    assert (kept[vim["id"]]["when"], kept[vim["id"]]["do"]) == ("at work", "uses Vim")
    [added] = [t for t in kept.values() if t["do"] == "indents with four spaces"]
    assert (added["status"], added["sources"]) == ("confirmed", [episode["id"]])
    answered = {q["id"]: q for q in Questions().all()}[question["id"]]
    assert (answered["status"], answered["answer"]) == ("answered", "yes, but spaces")
    [follow_up] = [q for q in Questions().pending() if q["text"] == "Do you code on weekends?"]
    assert (follow_up["thought"], follow_up["expected"], follow_up["sources"]) == ("t", "e", [])


@pytest.mark.spec("INT-3")
def test_an_unsettled_answer_keeps_the_question_current_and_shows_the_say(home_store, monkeypatch):
    question = ask("When do you write Rust?")
    asked = replying(
        monkeypatch,
        talked(
            "Which days, mostly?", settled=False, add=[{"about": "person", "when": "", "do": "writes Rust"}]
        ),
        talked(settled=False),
    )
    interview = Interview()
    assert interview.answered("1", "some days", NOW) == Turn("Which days, mostly?", False)
    assert interview.answered("1", "hmm", NOW) == Turn(opening(question), False)
    assert [q["status"] for q in Questions().all()] == ["pending"]
    assert [t["do"] for t in Traits().all()] == ["writes Rust"]
    assert json.loads(asked[1][1]["content"])["question"]["text"] == "When do you write Rust?"


def a_failing_call(messages):
    raise OllamaError("the model is down")


@pytest.mark.spec("INT-3")
@pytest.mark.parametrize("reply", [lambda messages: "not a reply", a_failing_call])
def test_a_reply_that_cannot_be_read_shows_the_question_again_and_changes_nothing(
    home_store, monkeypatch, reply
):
    question = ask("When do you write Rust?")
    quick_model_replying(monkeypatch, reply)
    assert Interview().answered("1", "weekdays") == Turn(f"{UNREADABLE}\n{opening(question)}", False)
    assert Questions().next()["status"] == "pending" and Home.store().items(Scratchpad.EPISODES) == {}
    assert Traits().all() == []


@pytest.mark.spec("INT-3")
def test_an_answer_changes_only_the_traits_its_question_asks_about(home_store, monkeypatch):
    asked_about, other = trait("writes Rust"), trait("uses tabs")
    ask("Do you write Rust?", "confirm", traits=[asked_about["id"]])
    replying(
        monkeypatch,
        talked(confirm=[other["id"]], reject=[{"id": other["id"], "why": "no"}], settled=False),
    )
    Interview().answered("1", "yes")
    assert {t["id"]: t["status"] for t in Traits().all()} == {
        asked_about["id"]: "candidate",
        other["id"]: "candidate",
    }


@pytest.mark.spec("INT-3")
def test_an_open_question_only_adds(home_store, monkeypatch):
    other = trait("uses tabs")
    ask("Which editor do you use?")
    replying(
        monkeypatch,
        talked(
            confirm=[other["id"]],
            add=[{"about": "person", "when": "", "do": "uses Helix for everything"}],
            settled=False,
        ),
    )
    Interview().answered("1", "Helix for everything")
    assert {t["do"]: t["status"] for t in Traits().all()} == {
        "uses tabs": "candidate",
        "uses Helix for everything": "confirmed",
    }


@pytest.mark.spec("INT-4")
def test_skip_sends_the_question_to_the_end_and_opens_the_next(home_store, monkeypatch):
    first = ask("When do you write Rust?")
    second = ask("Which editor do you use?")
    quick_model_replying(monkeypatch, no_model_call)
    assert Interview().skipped("1") == Turn(opening(second), False)
    assert [question["id"] for question in Questions().pending()] == [second["id"], first["id"]]
    assert Questions().pending()[-1]["status"] == "pending"


@pytest.mark.spec("INT-4")
def test_a_skipped_confirm_keeps_its_traits_candidates(home_store, monkeypatch):
    tabs = trait("uses tabs")
    ask("Do you use tabs?", "confirm", traits=[tabs["id"]])
    replying(monkeypatch, {"thought": "", "add": [], "questions": [], "done": True})
    assert Interview().skipped("1") == Turn(opening(Questions().next()), False)
    assert [t["status"] for t in Traits().all()] == ["candidate"]


def shown_then_outranked(interview):
    shown = ask("Which editor do you use?")
    interview.turn("1")
    outranking = ask("Do you write Rust?", "confirm", traits=[trait("writes Rust")["id"]])
    assert Questions().next()["id"] == outranking["id"]
    return shown, outranking


@pytest.mark.spec("INT-3")
def test_an_answer_goes_to_the_question_shown_in_that_chat_though_a_later_one_now_comes_first(
    home_store, monkeypatch
):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    asked = replying(monkeypatch, talked())
    assert interview.answered("1", "VS Code", NOW) == Turn(opening(outranking), False)
    sent = json.loads(asked[0][1]["content"])
    assert (sent["question"]["text"], sent["message"]) == ("Which editor do you use?", "VS Code")
    statuses = {question["id"]: question["status"] for question in Questions().all()}
    assert (statuses[shown["id"]], statuses[outranking["id"]]) == ("answered", "pending")


@pytest.mark.spec("INT-4")
def test_skip_goes_to_the_question_shown_in_that_chat_though_a_later_one_now_comes_first(home_store):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    assert interview.skipped("1") == Turn(opening(outranking), False)
    skips = {question["id"]: question["skips"] for question in Questions().all()}
    assert (skips[shown["id"]], skips[outranking["id"]]) == (1, 0)


@pytest.mark.spec("INT-3")
def test_an_answer_in_another_chat_goes_to_the_next_question(home_store, monkeypatch):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    asked = replying(monkeypatch, talked(settled=False))
    interview.answered("2", "yes", NOW)
    assert json.loads(asked[0][1]["content"])["question"]["text"] == "Do you write Rust?"
    assert {q["id"]: q["status"] for q in Questions().all()}[shown["id"]] == "pending"


@pytest.mark.spec("INT-3")
def test_an_answer_after_the_shown_question_is_no_longer_pending_goes_to_the_next_question(
    home_store, monkeypatch
):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    Questions().answer(shown["id"], "Helix")
    asked = replying(monkeypatch, talked(settled=False))
    interview.answered("1", "yes", NOW)
    assert json.loads(asked[0][1]["content"])["question"]["text"] == "Do you write Rust?"


@pytest.mark.spec("INT-5")
def test_an_answer_is_kept_as_an_interact_episode_already_dreamt(home_store, monkeypatch):
    ask("Which editor do you use?")
    replying(monkeypatch, talked("Helix it is.", settled=False))
    Interview().answered("1", "Helix", NOW)
    assert Scratchpad.fresh() == []
    [episode] = Home.store().items(Scratchpad.EPISODES).values()
    assert {key: episode[key] for key in ("chat", "kind", "question", "answer", "say", "dreamt")} == {
        "chat": "1",
        "kind": "interact",
        "question": "Which editor do you use?",
        "answer": "Helix",
        "say": "Helix it is.",
        "dreamt": True,
    }
