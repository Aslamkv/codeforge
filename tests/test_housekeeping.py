import datetime
import json

import pytest

from tinkerkit.memory.housekeeping import Compacted, Housekeeping
from tinkerkit.memory.memories import Memories, Traits
from tinkerkit.memory.routes import Routes
from tinkerkit.settings import RETIRED_KEPT_BYTES

NOW = datetime.datetime(2026, 11, 20, 12, 0, 0)
BUDGET = "tinkerkit.memory.housekeeping.RETIRED_KEPT_BYTES"


def stamp(day):
    return f"2026-11-{day:02d}T12:00:00"


def memory(memory_id, day, retired="wrong"):
    return {
        "id": memory_id,
        "kind": "fact",
        "gist": "a gist",
        "text": "x" * 500,
        "sources": ["e1", "e2"],
        "first_seen": stamp(1),
        "last_seen": stamp(day),
        "recalled": 0,
        "last_recalled": "",
        "strength": 1,
        "retired": retired,
    }


def trait(trait_id, day, status="retired"):
    return {
        "id": trait_id,
        "text": "likes short answers",
        "status": status,
        "sources": ["e1", "e2", "e3"],
        "history": [
            {"event": "add", "at": stamp(1), "text": "y" * 300},
            {"event": "reject", "at": stamp(day), "text": "no"},
        ],
    }


def route(route_id, day, status="retired"):
    return {
        "id": route_id,
        "pattern": "show jobs",
        "line": "/jobs",
        "status": status,
        "retired": "no" if status == "retired" else "",
        "history": [
            {"event": "add", "at": stamp(1), "text": "z" * 300},
            {"event": "retire", "at": stamp(day), "text": "no"},
        ],
    }


def put_all(store, memories=(), traits=(), routes=()):
    for name, items in ((Memories.MEMORIES, memories), (Traits.TRAITS, traits), (Routes.ROUTES, routes)):
        for item in items:
            store.put(name, item["id"], item)


def memory_stub(item, at):
    return dict(item, text="", sources=[], stub=True, at=at)


def trait_stub(item, at):
    return dict(item, sources=[], history=[], stub=True, at=at)


def route_stub(item, at):
    return dict(item, history=[], stub=True, at=at)


def size(*items):
    return sum(len(json.dumps(item)) for item in items)


def test_the_retired_kept_space_is_a_million_bytes():
    assert RETIRED_KEPT_BYTES == 1_000_000


@pytest.mark.spec("DRM-14")
def test_retired_ones_within_the_kept_space_are_not_changed(home_store):
    put_all(home_store, [memory("m1", 3)], [trait("t1", 4)], [route("r1", 5)])

    assert Housekeeping().compact() == []
    assert home_store.get("memories", "m1") == memory("m1", 3)


@pytest.mark.spec("DRM-14")
def test_the_oldest_retired_one_of_any_kind_is_shrunk_first_until_they_fit(home_store, monkeypatch):
    items = memory("m1", 5), trait("t1", 3), route("r1", 4)
    put_all(home_store, [items[0]], [items[1]], [items[2]])
    monkeypatch.setattr(BUDGET, size(*items) - 1)

    assert Housekeeping().compact() == [Compacted("trait", "t1", "stub")]
    assert home_store.get("traits", "t1")["stub"] is True
    assert "stub" not in home_store.get("memories", "m1")
    assert "stub" not in home_store.get("routes", "r1")


@pytest.mark.spec("DRM-14")
def test_each_kind_shrinks_to_its_stub(home_store, monkeypatch):
    items = memory("m1", 5), trait("t1", 3), route("r1", 4)
    put_all(home_store, [items[0]], [items[1]], [items[2]])
    stubs = (
        memory_stub(items[0], stamp(5)),
        trait_stub(items[1], stamp(3)),
        route_stub(items[2], stamp(4)),
    )
    monkeypatch.setattr(BUDGET, size(*stubs))

    changes = Housekeeping().compact()

    assert changes == [
        Compacted("trait", "t1", "stub"),
        Compacted("route", "r1", "stub"),
        Compacted("memory", "m1", "stub"),
    ]
    assert home_store.get("memories", "m1") == stubs[0]
    assert home_store.get("traits", "t1") == stubs[1]
    assert home_store.get("routes", "r1") == stubs[2]


@pytest.mark.spec("DRM-14")
def test_stubs_are_deleted_oldest_first_only_when_all_are_stubs_and_still_over(home_store, monkeypatch):
    put_all(home_store, [memory("m1", 5), memory("m2", 2)], [trait("t1", 3)])
    stub_m1 = memory_stub(memory("m1", 5), stamp(5))
    stub_t1 = trait_stub(trait("t1", 3), stamp(3))
    monkeypatch.setattr(BUDGET, size(stub_m1, stub_t1))

    changes = Housekeeping().compact()

    assert changes == [
        Compacted("memory", "m2", "stub"),
        Compacted("trait", "t1", "stub"),
        Compacted("memory", "m1", "stub"),
        Compacted("memory", "m2", "delete"),
    ]
    assert home_store.get("memories", "m2") is None
    assert home_store.get("memories", "m1") == stub_m1
    assert home_store.get("traits", "t1") == stub_t1


@pytest.mark.spec("DRM-14")
def test_stubs_keep_their_at_which_orders_their_deletion(home_store, monkeypatch):
    late = trait_stub(trait("t1", 3), stamp(6))
    early = trait_stub(trait("t2", 3), stamp(2))
    put_all(home_store, traits=[late, early])
    monkeypatch.setattr(BUDGET, size(late))

    assert Housekeeping().compact() == [Compacted("trait", "t2", "delete")]
    assert home_store.get("traits", "t1") == late


@pytest.mark.spec("DRM-14")
def test_living_ones_are_never_touched(home_store, monkeypatch):
    living = (
        memory("m1", 1, retired=""),
        trait("t1", 1, status="confirmed"),
        route("r1", 1, status="candidate"),
    )
    put_all(home_store, [living[0]], [living[1]], [living[2]])
    monkeypatch.setattr(BUDGET, 0)

    assert Housekeeping().compact() == []
    assert home_store.get("memories", "m1") == living[0]
    assert home_store.get("traits", "t1") == living[1]
    assert home_store.get("routes", "r1") == living[2]


@pytest.mark.spec("DRM-14", "RTE-5")
def test_a_retired_route_stub_still_drops_an_add_of_the_same_pattern_and_line(home_store, monkeypatch):
    add = {"op": "add", "pattern": "show jobs", "line": "/jobs", "source": "model"}
    Routes().apply([add, {"op": "retire", "id": "r1", "why": "no"}], NOW)
    retired = home_store.get("routes", "r1")
    monkeypatch.setattr(BUDGET, size(route_stub(retired, retired["history"][-1]["at"])))

    assert Housekeeping().compact() == [Compacted("route", "r1", "stub")]
    assert home_store.get("routes", "r1")["history"] == []
    assert Routes().apply([add], NOW) == []
