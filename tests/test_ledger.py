import time

import pytest

import tinkerkit.ledger as ledger
from tinkerkit.ledger import GLANCE_KEPT_SECONDS, MESSAGE_KEPT_SECONDS, Ledger
from tinkerkit.settings import PER_PAGE, Home


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(ledger.time, "time", lambda: now[0])
    return now


@pytest.mark.spec("LED-1")
def test_each_message_goes_when_its_time_is_up(clock):
    Ledger.kept("bot", "42", 1)
    Ledger.glance("bot", "42", [2])
    Ledger.lasting("bot", "42", 3)
    assert (
        Ledger.due("bot") == [] and Ledger.glances("bot", "42") == [2] and Ledger.is_lasting("bot", "42", 3)
    )
    assert sorted(message for _, message in Ledger.passing("bot")) == [1, 2]
    clock[0] += GLANCE_KEPT_SECONDS
    assert Ledger.due("bot") == [("42", 2)]
    clock[0] += MESSAGE_KEPT_SECONDS - GLANCE_KEPT_SECONDS - 10
    edited, the_status = 1, 3
    Ledger.touched("bot", "42", edited)
    Ledger.touched("bot", "42", the_status)
    clock[0] += 20
    assert Ledger.due("bot") == [("42", 2)] and Ledger.is_lasting("bot", "42", 3)
    Ledger.gone("bot", "42", 2)
    assert Ledger.due("bot") == [] and Ledger.due("other") == []


@pytest.mark.spec("LED-2")
def test_things_are_kept_as_shown_last_and_listed_newest_first():
    first = Ledger.shown("bot", "42", None, [1], "🔨 calc", "task calc")
    assert Ledger.thing_of("bot", "42", [1]) == first == 1
    assert Ledger.shown("bot", "42", first, [1, 2], "🔨 calc ✅", "ignored") == 1
    assert Ledger.thing(1)["text"] == "🔨 calc ✅" and Ledger.thing(1)["asked"] == "task calc"
    assert Ledger.thing_of("bot", "42", [2]) == 1 and Ledger.thing_of("bot", "42", [9]) is None
    for number in range(PER_PAGE + 1):
        Ledger.shown("bot", "42", None, [10 + number], f"reply {number}")
    things, pages = Ledger.page(1)
    assert pages == 2 and things[0]["text"] == f"reply {PER_PAGE}" and len(things) == PER_PAGE
    assert [thing["number"] for thing in Ledger.page(2)[0]] == [2, 1]


@pytest.mark.spec("SET-18")
def test_the_history_never_numbers_a_thing_again_once_cleared():
    Ledger.shown("bot", "42", None, [1], "first")
    Home.clear(["history"], yes=True)
    assert Ledger.page(1).items == [] and Ledger.shown("bot", "42", None, [2], "second") == 2
