import multiprocessing
import stat

import pytest

from tinkerkit.store import Store


@pytest.mark.spec("STO-1")
def test_store_documents(tmp_path):
    store = Store(tmp_path / "a" / "x.db")
    assert store.get("c", "k") is None and store.get("c", "k", {"d": 1}) == {"d": 1}
    store.put("c", "b", {"name": "café", "n": [1]})
    store.put("c", "a", 2)
    store.put("c", "b", {"name": "again"})
    store.put("other", "z", 3)
    assert store.items("c") == {"b": {"name": "again"}, "a": 2} and store.count("c") == 2
    store.delete("c", "b")
    assert store.items("c") == {"a": 2}
    store.delete("c")
    assert store.items("c") == {} and store.get("other", "z") == 3


def add_many(path, times):
    store = Store(path)
    for _ in range(times):
        store.update("c", "n", lambda count: count + 1, 0)


@pytest.mark.spec("STO-1")
def test_store_updates_from_two_processes_lose_nothing(tmp_path):
    path = tmp_path / "x.db"
    Store(path).put("c", "n", 0)
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=add_many, args=(path, 50)) for _ in range(2)]
    for worker in workers:
        worker.start()
    add_many(path, 50)
    for worker in workers:
        worker.join(30)
    assert Store(path).get("c", "n") == 150


@pytest.mark.spec("STO-1")
def test_a_failed_write_leaves_the_old_value(tmp_path):
    store = Store(tmp_path / "x.db")
    store.put("c", "k", "old")

    def broken(value):
        raise RuntimeError("crash in the middle")

    with pytest.raises(RuntimeError):
        store.update("c", "k", broken)
    assert store.get("c", "k") == "old"
    with pytest.raises(TypeError):
        not_json = object()
        store.put("c", "k", not_json)
    assert store.get("c", "k") == "old"


@pytest.mark.spec("STO-2")
def test_store_logs_and_mode(tmp_path):
    store = Store(tmp_path / "x.db")
    first = store.append("log", {"label": "one"})
    store.append("log", {"label": "two", "x": "é"})
    store.append("other", {"label": "no"})
    entries = store.entries("log")
    assert [entry["label"] for _, entry in entries] == ["one", "two"] and all(
        "time" in entry for _, entry in entries
    )
    assert [entry["label"] for _, entry in store.entries("log", after=first)] == ["two"]
    store.drop_log("log")
    assert store.entries("log") == [] and len(store.entries("other")) == 1
    assert stat.S_IMODE((tmp_path / "x.db").stat().st_mode) == 0o600


@pytest.mark.spec("STO-2")
def test_a_deleted_database_is_made_again(tmp_path):
    store = Store(tmp_path / "x.db")
    store.put("c", "k", 1)
    (tmp_path / "x.db").unlink()
    assert store.get("c", "k") is None
    store.put("c", "k", 2)
    assert store.get("c", "k") == 2
