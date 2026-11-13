import json

import pytest
from conftest import quick_model_replying
from test_dreaming import NOW, replying

from tinkerkit.interviewing import Interview
from tinkerkit.prompts import Prompts
from tinkerkit.remembering import Questions, Scratchpad, Traits
from tinkerkit.settings import Home

HINT = '_a number, a number with changes ("2, but on weekdays"), your own words, or /skip_'


def ask(text, suggestions=()):
    return Questions().add("open", text, suggestions, [], ["e1"])


@pytest.mark.spec("INT-1")
def test_a_turn_shows_the_next_question_as_kept_then_its_suggestions_numbered(home_store):
    ask("When do you write Rust?", ["Weekdays", "Week_ends"])
    ask("Which editor do you use?")
    assert Interview().turn("1") == "\n".join(
        ["💬 When do you write Rust?", "1. Weekdays", "2. Week\\_ends", HINT]
    )


@pytest.mark.spec("INT-1")
def test_a_turn_without_suggestions_shows_no_numbered_lines(home_store):
    ask("Which editor do you use?")
    assert Interview().turn("1") == f"💬 Which editor do you use?\n{HINT}"


@pytest.mark.spec("INT-1")
def test_no_pending_question_has_no_turn(home_store):
    assert Interview().turn("1") is None


@pytest.mark.spec("INT-4")
def test_skip_sends_the_question_to_the_end_and_turns_to_the_next(home_store):
    first = ask("When do you write Rust?")
    ask("Which editor do you use?")
    assert Interview().skipped("1").startswith("💬 Which editor do you use?\n")
    assert [question["id"] for question in Questions().pending()][-1] == first["id"]
    assert Questions().pending()[-1]["status"] == "pending"


@pytest.mark.spec("INT-4")
def test_skip_with_nothing_pending_has_no_turn(home_store):
    assert Interview().skipped("1") is None


def trait(do, sources=("e1",), confirmed=False):
    delta = {"op": "add", "about": "person", "when": "", "do": do, "sources": list(sources)}
    return Traits().apply([dict(delta, confirmed=confirmed)])[0]


def confirm(text, traits, suggestions=("Yes", "No")):
    return Questions().add("confirm", text, suggestions, [t["id"] for t in traits], ["e1"])


@pytest.mark.spec("INT-2")
def test_with_nothing_pending_the_model_gives_the_next_question_kept_before_it_is_shown(
    home_store, monkeypatch
):
    trait("writes Rust", confirmed=True)
    Questions().answer(ask("When do you write Rust?")["id"], "weekdays")
    asked = replying(monkeypatch, {"text": "Which editor do you use?", "suggestions": ["Helix", "Vim"]})
    assert Interview().turn("1") == f"💬 Which editor do you use?\n1. Helix\n2. Vim\n{HINT}"
    assert asked[0][0]["content"] == Prompts.INTERACT_SYSTEM
    assert json.loads(asked[0][1]["content"]) == {
        "traits": [{"id": "t1", "about": "person", "when": "", "do": "writes Rust"}],
        "questions": ["When do you write Rust?"],
    }
    assert Questions().next()["text"] == "Which editor do you use?"


@pytest.mark.spec("INT-2")
def test_the_model_saying_done_ends_the_interview(home_store, monkeypatch):
    replying(monkeypatch, {"done": True})
    assert Interview().turn("1") is None and Questions().all() == []


@pytest.mark.spec("INT-3")
def test_an_answer_applies_its_deltas_answers_the_question_and_shows_the_follow_up(home_store, monkeypatch):
    rust, tabs, vim = trait("writes Rust"), trait("uses tabs"), trait("uses Vim daily")
    question = confirm("Do you write Rust and use tabs?", [rust, tabs, vim])
    asked = replying(
        monkeypatch,
        {
            "confirm": [rust["id"], "t9"],
            "reject": [{"id": tabs["id"], "why": "spaces"}],
            "amend": [{"id": vim["id"], "when": "at work", "do": "uses Vim"}],
            "add": [{"about": "person", "when": "", "do": "indents with four spaces"}],
            "question": {"text": "Which editor at home?", "suggestions": ["Helix", "Emacs"]},
        },
    )
    turn = Interview().answered("1", "yes, but spaces", NOW)
    assert turn == f"💬 Which editor at home?\n1. Helix\n2. Emacs\n{HINT}"
    assert asked[0][0]["content"] == Prompts.INTERACT_ANSWER_SYSTEM
    assert json.loads(asked[0][1]["content"]) == {
        "question": "Do you write Rust and use tabs?",
        "suggestions": ["Yes", "No"],
        "traits": [{"id": t["id"], "about": "person", "when": "", "do": t["do"]} for t in (rust, tabs, vim)],
        "reply": "yes, but spaces",
    }
    [episode] = Home.store().items(Scratchpad.EPISODES).values()
    assert (episode["kind"], episode["chat"], episode["question"], episode["answer"], episode["dreamt"]) == (
        "interact",
        "1",
        "Do you write Rust and use tabs?",
        "yes, but spaces",
        True,
    )
    kept = {t["id"]: t for t in Traits().all()}
    assert kept[rust["id"]]["status"] == "confirmed"
    assert (kept[tabs["id"]]["status"], kept[tabs["id"]]["why"]) == ("retired", "spaces")
    assert (kept[vim["id"]]["when"], kept[vim["id"]]["do"]) == ("at work", "uses Vim")
    [added] = [t for t in kept.values() if t["do"] == "indents with four spaces"]
    assert (added["status"], added["sources"]) == ("confirmed", [episode["id"]])
    assert (
        Questions().all()[0]["status"] == "answered" and Questions().all()[0]["answer"] == "yes, but spaces"
    )
    follow_up = Questions().next()
    assert (follow_up["id"] != question["id"], follow_up["sources"]) == (True, [episode["id"]])


@pytest.mark.spec("INT-3")
def test_an_answer_changes_only_the_traits_its_question_asks_about(home_store, monkeypatch):
    asked_about, other = trait("writes Rust"), trait("uses tabs")
    confirm("Do you write Rust?", [asked_about])
    replying(
        monkeypatch,
        {
            "confirm": [other["id"]],
            "reject": [{"id": other["id"], "why": "no"}],
            "amend": [],
            "add": [],
            "question": None,
        },
        {"done": True},
    )
    Interview().answered("1", "yes")
    assert {t["id"]: t["status"] for t in Traits().all()} == {
        asked_about["id"]: "candidate",
        other["id"]: "candidate",
    }


@pytest.mark.spec("INT-3")
def test_a_reply_that_cannot_be_read_asks_the_question_again_and_changes_nothing(home_store, monkeypatch):
    ask("When do you write Rust?", ["Weekdays", "Weekends"])
    quick_model_replying(monkeypatch, lambda messages: "not a reply")
    assert Interview().answered("1", "weekdays") == (
        f"Couldn't read that answer; here it is again.\n💬 When do you write Rust?\n1. Weekdays\n2. Weekends\n{HINT}"
    )
    assert Questions().next()["status"] == "pending" and Home.store().items(Scratchpad.EPISODES) == {}


def no_model_call(messages):
    raise AssertionError("the model was called")


@pytest.mark.spec("INT-3")
@pytest.mark.parametrize("answer", ["3", " 0", "3, but on weekdays", "12 weekends"])
def test_an_answer_starting_with_a_number_no_suggestion_has_names_the_numbers_and_asks_again(
    home_store, monkeypatch, answer
):
    ask("When do you write Rust?", ["Weekdays", "Weekends"])
    quick_model_replying(monkeypatch, no_model_call)
    assert Interview().answered("1", answer) == (
        f"Pick 1–2, your own words, or /skip.\n💬 When do you write Rust?\n1. Weekdays\n2. Weekends\n{HINT}"
    )
    assert Questions().next()["status"] == "pending" and Home.store().items(Scratchpad.EPISODES) == {}


@pytest.mark.spec("INT-3")
def test_a_number_answer_to_a_question_without_suggestions_goes_to_the_model(home_store, monkeypatch):
    ask("How many years have you written Rust?")
    asked = replying(
        monkeypatch, {"confirm": [], "reject": [], "amend": [], "add": [], "question": None}, {"done": True}
    )
    assert Interview().answered("1", "3") is None
    assert json.loads(asked[0][1]["content"])["reply"] == "3"


@pytest.mark.spec("INT-3")
@pytest.mark.parametrize(
    ("answer", "sent"), [("2", "VS Code"), (" 2, but on weekdays", "VS Code, but on weekdays")]
)
def test_an_answer_starting_with_a_suggestion_number_is_sent_and_kept_as_its_text(
    home_store, monkeypatch, answer, sent
):
    ask("Which editor do you use?", ["Helix", "VS Code"])
    asked = replying(
        monkeypatch, {"confirm": [], "reject": [], "amend": [], "add": [], "question": None}, {"done": True}
    )
    assert Interview().answered("1", answer) is None
    assert json.loads(asked[0][1]["content"])["reply"] == sent
    [episode] = Home.store().items(Scratchpad.EPISODES).values()
    assert (episode["answer"], Questions().all()[0]["answer"]) == (sent, sent)


@pytest.mark.spec("INT-3")
def test_a_question_without_traits_asks_only_for_adds_and_a_follow_up(home_store, monkeypatch):
    ask("Which editor do you use?")
    asked = replying(
        monkeypatch,
        {"add": [{"about": "person", "when": "", "do": "uses Helix for everything"}], "question": None},
        {"done": True},
    )
    assert Interview().answered("1", "Helix for everything") is None
    assert asked[0][0]["content"] == Prompts.INTERACT_ADD_SYSTEM
    assert "traits" not in json.loads(asked[0][1]["content"])
    assert [t["do"] for t in Traits().all()] == ["uses Helix for everything"]


def shown_then_outranked(interview):
    shown = ask("Which editor do you use?", ["Helix", "VS Code"])
    interview.turn("1")
    outranking = confirm("Do you write Rust?", [trait("writes Rust")], ("Yes", "No", "Sometimes"))
    assert Questions().next()["id"] == outranking["id"]
    return shown, outranking


@pytest.mark.spec("INT-3")
def test_an_answer_goes_to_the_question_shown_in_that_chat_though_a_later_one_now_comes_first(
    home_store, monkeypatch
):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    assert interview.suggestion_count("1") == 2
    asked = replying(monkeypatch, {"add": [], "question": None})
    assert interview.answered("1", "2", NOW).startswith("💬 Do you write Rust?\n")
    assert (json.loads(asked[0][1]["content"])["question"], json.loads(asked[0][1]["content"])["reply"]) == (
        "Which editor do you use?",
        "VS Code",
    )
    statuses = {question["id"]: question["status"] for question in Questions().all()}
    assert (statuses[shown["id"]], statuses[outranking["id"]]) == ("answered", "pending")


@pytest.mark.spec("INT-4")
def test_skip_goes_to_the_question_shown_in_that_chat_though_a_later_one_now_comes_first(home_store):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    assert interview.skipped("1").startswith("💬 Do you write Rust?\n")
    skips = {question["id"]: question["skips"] for question in Questions().all()}
    assert (skips[shown["id"]], skips[outranking["id"]]) == (1, 0)


@pytest.mark.spec("INT-3")
def test_an_answer_in_another_chat_goes_to_the_next_question(home_store, monkeypatch):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    asked = replying(monkeypatch, {"confirm": [], "reject": [], "amend": [], "add": [], "question": None})
    interview.answered("2", "yes", NOW)
    assert json.loads(asked[0][1]["content"])["question"] == "Do you write Rust?"
    assert Questions().next()["id"] == shown["id"]


@pytest.mark.spec("INT-3")
def test_an_answer_after_the_shown_question_is_no_longer_pending_goes_to_the_next_question(
    home_store, monkeypatch
):
    interview = Interview()
    shown, outranking = shown_then_outranked(interview)
    Questions().answer(shown["id"], "Helix")
    asked = replying(
        monkeypatch, {"confirm": [], "reject": [], "amend": [], "add": [], "question": None}, {"done": True}
    )
    interview.answered("1", "yes", NOW)
    assert json.loads(asked[0][1]["content"])["question"] == "Do you write Rust?"


@pytest.mark.spec("INT-5")
def test_an_answer_is_kept_as_an_interact_episode_already_dreamt(home_store, monkeypatch):
    ask("Which editor do you use?")
    replying(
        monkeypatch, {"confirm": [], "reject": [], "amend": [], "add": [], "question": None}, {"done": True}
    )
    assert Interview().answered("1", "Helix") is None
    assert Scratchpad.fresh() == []
    assert [e["kind"] for e in Home.store().items(Scratchpad.EPISODES).values()] == ["interact"]
