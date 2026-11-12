import _socket
import datetime
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest
from conftest import quick_model_replying

from tinkerkit.dreaming import Dream, LiveGraph, MemoryGraph, show_dream, show_memory_graph
from tinkerkit.jobs import Jobs, Queue
from tinkerkit.model import Models, OllamaError
from tinkerkit.prompts import Prompts
from tinkerkit.remembering import Memories, Scratchpad
from tinkerkit.retrieval import Retriever
from tinkerkit.settings import DREAM_BATCH_EPISODES, PORTRAIT_WORDS, Home
from tinkerkit.store import Log

NOW = datetime.datetime(2026, 11, 11, 12, 0, 0)
EARLIER = datetime.datetime(2026, 11, 1, 9, 0, 0)


def episode(chat="telegram:1", message="list the files"):
    return Scratchpad.write(
        {"chat": chat, "message": message, "reply": "Done.", "status": "answered"}, EARLIER
    )


def memories():
    return Memories(Retriever(None))


def dream(phase=lambda step: None):
    return Dream(Retriever(None), phase)


def replying(monkeypatch, *replies):
    queue = list(replies)
    return quick_model_replying(monkeypatch, lambda messages: json.dumps(queue.pop(0)))


def stored(memory_id):
    return Home.store().get(Memories.MEMORIES, memory_id)


def dream_events():
    return [
        {key: value for key, value in logged.entry.items() if key != "time"}
        for logged in Home.store().entries("dream events")
    ]


@pytest.mark.spec("DRM-3")
def test_consolidate_adds_a_memory_citing_the_batch_and_dreams_it(home_store, monkeypatch):
    first = episode()
    asked = replying(
        monkeypatch,
        {
            "add": [
                {
                    "kind": "person",
                    "gist": "likes short replies",
                    "text": "Wants it brief.",
                    "sources": [first["id"]],
                }
            ]
        },
    )
    dream().consolidate(NOW)
    memory = memories().living()[0]
    assert (memory["gist"], memory["strength"], memory["sources"]) == (
        "likes short replies",
        1,
        [first["id"]],
    )
    assert asked[0][0]["content"] == Prompts.DREAM_CONSOLIDATE_SYSTEM
    assert first["id"] in asked[0][1]["content"]
    assert Scratchpad.fresh() == []
    assert "gist" not in Home.store().get(Scratchpad.EPISODES, first["id"])


def question(message="what is the capital of France?", reply="Paris.", **changes):
    return Scratchpad.write(
        dict({"chat": "telegram:1", "message": message, "reply": reply, "status": "answered"}, **changes),
        EARLIER,
    )


@pytest.mark.spec("DRM-3")
def test_consolidate_names_the_questions_and_adds_an_answer_with_its_lasts(home_store, monkeypatch):
    asked = question()
    asked_model = replying(monkeypatch, {"lasts": {asked["id"]: "month"}})
    dream().consolidate(NOW)
    memory = memories().living()[0]
    assert (memory["kind"], memory["gist"], memory["text"], memory["lasts"], memory["sources"]) == (
        "answer",
        "what is the capital of France?",
        "Paris.",
        "month",
        [asked["id"]],
    )
    assert json.loads(asked_model[0][1]["content"])["questions"] == [asked["id"]]


@pytest.mark.spec("DRM-3")
def test_consolidate_makes_no_answer_from_commands_memory_never_or_the_model(home_store, monkeypatch):
    plain = question()
    ran = question("free space?", "9 GB.", commands=[])
    recalled = question("my shell?", "zsh.", status="remembered")
    changing = question("what time is it?", "12:00.")
    asked_model = replying(
        monkeypatch,
        {
            "add": [
                {
                    "kind": "answer",
                    "gist": "capital?",
                    "text": "Paris.",
                    "sources": [plain["id"]],
                    "lasts": "always",
                }
            ],
            "lasts": {
                ran["id"]: "always",
                recalled["id"]: "always",
                changing["id"]: "never",
                plain["id"]: "week",
                "e99": "always",
            },
        },
    )
    dream().consolidate(NOW)
    assert memories().living() == []
    assert json.loads(asked_model[0][1]["content"])["questions"] == [plain["id"], changing["id"]]


@pytest.mark.spec("DRM-3")
def test_consolidate_leaves_an_answer_the_model_updates_or_retires(home_store, monkeypatch):
    updated = memories().add("answer", "what is the capital of France?", "Paris.", ["e0"], EARLIER, "always")
    retired = memories().add("answer", "how high is Everest?", "8,849 m.", ["e0"], EARLIER, "always")
    asked = episode()
    replying(
        monkeypatch,
        {
            "update": [{"id": updated["id"], "text": "Lyon.", "sources": [asked["id"]]}],
            "retire": [{"id": retired["id"], "why": "stale", "sources": [asked["id"]]}],
        },
    )
    dream().consolidate(NOW)
    assert [(memory["id"], memory["text"]) for memory in memories().living()] == [
        (updated["id"], "Paris."),
        (retired["id"], "8,849 m."),
    ]


@pytest.mark.spec("DRM-3")
def test_consolidate_updates_the_living_answer_to_the_same_message(home_store, monkeypatch):
    kept = memories().add("answer", "what is the capital of France?", "Lyon.", ["e0"], EARLIER, "day")
    asked = question()
    replying(monkeypatch, {"lasts": {asked["id"]: "always"}})
    dream().consolidate(NOW)
    [memory] = memories().living()
    assert (memory["id"], memory["text"], memory["lasts"], memory["sources"], memory["last_seen"]) == (
        kept["id"],
        "Paris.",
        "always",
        ["e0", asked["id"]],
        NOW.isoformat(timespec="seconds"),
    )


@pytest.mark.spec("REM-3")
def test_an_answer_memory_needs_how_long_it_lasts():
    with pytest.raises(ValueError):
        memories().add("answer", "what is 2+2?", "4.", ["e1"], NOW)


@pytest.mark.spec("DRM-3")
def test_consolidate_stores_the_gist_of_each_batch_episode(home_store, monkeypatch):
    first = episode()
    second = episode(message="read the log")
    replying(
        monkeypatch,
        {
            "gists": {
                first["id"]: "listed 12 files in ~/notes",
                second["id"]: "",
                "e99": "not in the batch",
            }
        },
    )
    dream().consolidate(NOW)
    assert Home.store().get(Scratchpad.EPISODES, first["id"])["gist"] == "listed 12 files in ~/notes"
    assert "gist" not in Home.store().get(Scratchpad.EPISODES, second["id"])
    assert Home.store().get(Scratchpad.EPISODES, "e99") is None


@pytest.mark.spec("DRM-3")
def test_consolidate_drops_gists_and_lasts_not_given_as_maps_of_strings(home_store, monkeypatch):
    first = episode()
    asked = question()
    replying(
        monkeypatch,
        {
            "gists": [{"id": first["id"], "gist": "listed 12 files"}],
            "lasts": {asked["id"]: ["always"]},
        },
    )
    dream().consolidate(NOW)
    assert "gist" not in Home.store().get(Scratchpad.EPISODES, first["id"])
    assert memories().living() == []


@pytest.mark.spec("DRM-3")
def test_a_change_citing_no_episode_or_one_outside_the_batch_is_dropped(home_store, monkeypatch):
    first = episode()
    replying(
        monkeypatch,
        {
            "add": [
                {"kind": "fact", "gist": "none cited", "text": "x", "sources": []},
                {"kind": "fact", "gist": "outside", "text": "x", "sources": [first["id"], "e99"]},
            ]
        },
    )
    dream().consolidate(NOW)
    assert memories().living() == []
    assert Scratchpad.fresh() == []


@pytest.mark.spec("DRM-3")
def test_confirm_adds_strength_and_update_keeps_its_sources(home_store, monkeypatch):
    first = episode()
    kept = memories().add("fact", "uses zsh", "The shell is zsh.", ["e0"], EARLIER)
    changed = memories().add("fact", "likes tabs", "Indents with tabs.", ["e0"], EARLIER)
    replying(
        monkeypatch,
        {
            "confirm": [{"id": kept["id"], "sources": [first["id"]]}],
            "update": [
                {
                    "id": changed["id"],
                    "gist": "likes spaces",
                    "text": "Indents with spaces.",
                    "sources": [first["id"]],
                }
            ],
        },
    )
    dream().consolidate(NOW)
    assert (stored(kept["id"])["strength"], stored(kept["id"])["last_seen"]) == (2, NOW.isoformat())
    assert stored(kept["id"])["sources"] == ["e0", first["id"]]
    assert stored(changed["id"])["gist"] == "likes spaces"
    assert stored(changed["id"])["sources"] == ["e0", first["id"]]


@pytest.mark.spec("DRM-3")
def test_retire_says_why_and_an_unknown_id_is_dropped(home_store, monkeypatch):
    first = episode()
    old = memories().add("fact", "uses vim", "Edits with vim.", ["e0"], EARLIER)
    replying(
        monkeypatch,
        {
            "retire": [{"id": old["id"], "why": "moved to emacs", "sources": [first["id"]]}],
            "confirm": [{"id": "m99", "sources": [first["id"]]}],
        },
    )
    dream().consolidate(NOW)
    assert stored(old["id"])["retired"] == "moved to emacs"
    assert stored("m99") is None


@pytest.mark.spec("DRM-3")
def test_an_added_memory_of_an_unknown_kind_is_dropped(home_store, monkeypatch):
    first = episode()
    replying(
        monkeypatch,
        {
            "add": [
                {"kind": "mood", "gist": "is happy", "text": "x", "sources": [first["id"]]},
                {"kind": "done", "gist": "listed files", "text": "x", "sources": [first["id"]]},
            ]
        },
    )
    dream().consolidate(NOW)
    assert [memory["gist"] for memory in memories().living()] == ["listed files"]


@pytest.mark.spec("DRM-3")
def test_a_failed_quick_call_replies_none(home_store, monkeypatch):
    def failing(*args, **kwargs):
        raise OllamaError("down")

    monkeypatch.setattr(Models, "quick_reply", failing)
    assert dream().replied("system", "content") is None
    assert dream().asked("system", "content") is None


@pytest.mark.spec("DRM-3")
def test_a_tool_memory_is_kept_only_when_the_tool_is_found(home_store, monkeypatch):
    first = episode()
    monkeypatch.setattr("shutil.which", lambda tool: "/bin/ls" if tool == "ls" else None)
    replying(
        monkeypatch,
        {
            "add": [
                {
                    "kind": "tool",
                    "gist": "lists with ls",
                    "text": "x",
                    "tool": "ls",
                    "sources": [first["id"]],
                },
                {"kind": "tool", "gist": "uses nope", "text": "x", "tool": "nope", "sources": [first["id"]]},
                {"kind": "tool", "gist": "no tool named", "text": "x", "sources": [first["id"]]},
            ]
        },
    )
    dream().consolidate(NOW)
    assert [memory["gist"] for memory in memories().living()] == ["lists with ls"]


@pytest.mark.spec("DRM-3")
def test_batches_go_chat_by_chat_at_most_the_batch_size(home_store, monkeypatch):
    for _ in range(DREAM_BATCH_EPISODES + 1):
        episode("telegram:1")
    episode("console")
    asked = replying(monkeypatch, {}, {}, {})
    dream().consolidate(NOW)
    sizes = [len(json.loads(messages[1]["content"])["episodes"]) for messages in asked]
    assert sizes == [DREAM_BATCH_EPISODES, 1, 1]
    assert Scratchpad.fresh() == []


@pytest.mark.spec("DRM-3")
def test_a_reply_that_cannot_be_read_leaves_its_batch_fresh(home_store, monkeypatch):
    first = episode("telegram:1")
    episode("console")
    replies = iter(["no json here", "{}"])
    quick_model_replying(monkeypatch, lambda messages: next(replies))
    dream().consolidate(NOW)
    assert [item["id"] for item in Scratchpad.fresh()] == [first["id"]]


@pytest.mark.spec("DRM-3")
def test_a_failed_call_leaves_its_batch_fresh(home_store, monkeypatch):
    episode()

    def failed(messages):
        raise OllamaError("down")

    quick_model_replying(monkeypatch, failed)
    dream().consolidate(NOW)
    assert len(Scratchpad.fresh()) == 1


@pytest.mark.spec("DRM-3")
def test_the_batch_is_shown_the_memories_it_recalls(home_store, monkeypatch):
    episode(message="list the files")
    memories().add("fact", "files are listed with ls", "x", ["e0"], EARLIER)
    memories().add("fact", "likes tea", "x", ["e0"], EARLIER)
    asked = replying(monkeypatch, {})
    dream().consolidate(NOW)
    shown = json.loads(asked[0][1]["content"])["memories"]
    assert [memory["gist"] for memory in shown] == ["files are listed with ls"]
    assert all(memory["recalled"] == 0 for memory in memories().living())


@pytest.mark.spec("DRM-7")
def test_each_change_is_logged_as_a_dream_event(home_store, monkeypatch):
    first = episode()
    replying(
        monkeypatch, {"add": [{"kind": "fact", "gist": "uses zsh", "text": "x", "sources": [first["id"]]}]}
    )
    dream().consolidate(NOW)
    assert dream_events() == [{"step": "consolidate", "change": "add", "id": "m1", "gist": "uses zsh"}]


@pytest.mark.spec("DRM-2")
def test_run_walks_its_steps_and_saves_the_state(home_store, monkeypatch):
    first = episode()
    replying(
        monkeypatch, {"add": [{"kind": "fact", "gist": "uses zsh", "text": "x", "sources": [first["id"]]}]}
    )
    doing = []
    dream(doing.append).run(NOW)
    state = Home.store().get("dream", "state")
    assert doing == ["consolidate", "revise", "decay", "portrait"]
    assert state == {
        "done": [],
        "changed": [],
        "counts": {"consolidate": 1, "revise": 0, "decay": 0, "portrait": 0},
        "last": NOW.isoformat(),
    }


@pytest.mark.spec("DRM-2")
def test_run_skips_the_steps_already_done(home_store, monkeypatch):
    episode()
    Home.store().put("dream", "state", {"done": list(Dream.STEPS), "changed": [], "counts": {}, "last": ""})
    asked = replying(monkeypatch)
    doing = []
    dream(doing.append).run(NOW)
    assert (doing, asked) == ([], [])
    assert Home.store().get("dream", "state")["last"] == NOW.isoformat()


@pytest.mark.spec("DRM-2")
def test_run_without_a_quick_model_does_nothing(home_store, monkeypatch):
    episode()
    monkeypatch.setattr(Models, "quick", staticmethod(lambda: None))
    dream().run(NOW)
    assert Home.store().get("dream", "state") is None
    assert len(Scratchpad.fresh()) == 1


LONG_AGO = datetime.datetime(2026, 8, 1, 9, 0, 0)


def remembered(gist, text, kind="fact", sources=("e1",), now=EARLIER, **changes):
    memory = memories().add(kind, gist, text, sources, now)
    return memories().update(memory["id"], changes) if changes else memory


class FakeEmbeddings:
    name = "fake"
    failed = ""

    def __init__(self, vector_of):
        self.vector_of = vector_of

    def vectors(self, texts, keep=True, prune=False):
        return [self.vector_of(text) for text in texts]


@pytest.mark.spec("DRM-4")
def test_revise_merges_a_changed_group_into_one_memory(home_store, monkeypatch):
    first = remembered("likes short replies", "Wants it brief.", "person", ("e1",), LONG_AGO, strength=2)
    second = remembered("likes short replies", "Wants it brief always.", "person", ("e2",), EARLIER)
    remembered("uses zsh", "The shell is zsh.")
    asked = replying(
        monkeypatch,
        {
            "merge": [
                {"ids": [first["id"], second["id"]], "kind": "person", "gist": "brief", "text": "Brief."}
            ],
            "retire": [],
        },
    )
    revising = dream()
    revising.changed = [second["id"]]
    revising.revise(NOW)
    merged = stored("m4")
    assert (merged["sources"], merged["first_seen"], merged["strength"]) == (
        ["e1", "e2"],
        LONG_AGO.isoformat(),
        3,
    )
    assert stored(first["id"])["retired"] == stored(second["id"])["retired"] == "merged into m4"
    assert asked[0][0]["content"] == Prompts.DREAM_REVISE_SYSTEM
    assert "uses zsh" not in asked[0][1]["content"]
    assert revising.changed == [second["id"], "m4", first["id"], second["id"]]
    assert [event["change"] for event in dream_events()] == ["merge", "retire", "retire"]
    assert {event["step"] for event in dream_events()} == {"revise"}


@pytest.mark.spec("DRM-4")
def test_revise_keeps_similar_answers_apart(home_store, monkeypatch):
    feet = memories().add("answer", "how high is Everest?", "29,032 feet.", ["e1"], EARLIER, "always")
    km = memories().add("answer", "how high is Everest?", "8.8 km.", ["e2"], EARLIER, "always")
    asked = replying(monkeypatch)
    revising = dream()
    revising.changed = [km["id"]]
    revising.revise(NOW)
    assert asked == []
    assert [memory["id"] for memory in memories().living()] == [feet["id"], km["id"]]


@pytest.mark.spec("DRM-4")
def test_revise_leaves_a_group_with_nothing_changed(home_store, monkeypatch):
    remembered("likes short replies", "Wants it brief.")
    remembered("likes short replies", "Wants it brief always.")
    asked = replying(monkeypatch)
    dream().revise(NOW)
    assert asked == []


@pytest.mark.spec("DRM-4")
def test_revise_reads_what_changed_earlier_in_the_dream(home_store, monkeypatch):
    first = remembered("likes short replies", "Wants it brief.")
    second = remembered("likes short replies", "Wants it brief always.")
    replying(monkeypatch, {"merge": [], "retire": [{"id": first["id"], "why": "the newer one holds"}]})
    revising = dream()
    revising.state = dict(revising.state, changed=[second["id"]])
    revising.revise(NOW)
    assert stored(first["id"])["retired"] == "the newer one holds"


@pytest.mark.spec("DRM-4")
def test_revise_drops_ids_outside_the_group(home_store, monkeypatch):
    first = remembered("likes short replies", "Wants it brief.")
    second = remembered("likes short replies", "Wants it brief always.")
    other = remembered("uses zsh", "The shell is zsh.")
    replying(
        monkeypatch,
        {
            "merge": [{"ids": [first["id"], other["id"]], "kind": "fact", "gist": "x", "text": "x"}],
            "retire": [{"id": other["id"], "why": "no"}],
        },
    )
    revising = dream()
    revising.changed = [first["id"]]
    revising.revise(NOW)
    assert [memory["id"] for memory in memories().living()] == [first["id"], second["id"], other["id"]]


@pytest.mark.spec("DRM-4")
def test_revise_groups_by_meaning_when_the_embeddings_are_given(home_store, monkeypatch):
    first = remembered("likes short replies", "Wants it brief.")
    second = remembered("hates long answers", "Keep it tight.")
    asked = replying(monkeypatch, {"merge": [], "retire": []})
    revising = Dream(Retriever(FakeEmbeddings(lambda text: [1.0, 0.0])))
    revising.changed = [first["id"]]
    revising.revise(NOW)
    assert second["id"] in asked[0][1]["content"]


@pytest.mark.spec("DRM-5")
def test_decay_retires_a_faded_memory_and_prunes_old_episodes(home_store, monkeypatch):
    faded = remembered("old", "Old.", now=LONG_AGO)
    strong = remembered("strong", "Strong.", now=LONG_AGO, strength=3)
    recalled = remembered("recalled", "Recalled.", now=LONG_AGO, last_recalled=EARLIER.isoformat())
    fresh = remembered("fresh", "Fresh.", now=EARLIER)
    old, young = episode(), episode()
    Scratchpad.dreamt([old["id"]], LONG_AGO)
    Scratchpad.dreamt([young["id"]], EARLIER)
    asked = replying(monkeypatch)
    dream().decay(NOW)
    assert stored(faded["id"])["retired"] == "faded"
    assert [memory["id"] for memory in memories().living()] == [strong["id"], recalled["id"], fresh["id"]]
    assert list(Home.store().items(Scratchpad.EPISODES)) == [young["id"]]
    assert dream_events() == [{"step": "decay", "change": "retire", "id": faded["id"], "gist": "old"}]
    assert asked == []


@pytest.mark.spec("DRM-5")
def test_decay_retires_answers_once_they_expire(home_store):
    def answer(lasts, seen):
        memory = memories().add("answer", f"{lasts} question?", "Answer.", ["e1"], seen, lasts)
        return memories().update(memory["id"], {"strength": 3})

    stale_day = answer("day", NOW - datetime.timedelta(days=2))
    today = answer("day", NOW - datetime.timedelta(hours=2))
    stale_month = answer("month", NOW - datetime.timedelta(days=31))
    this_month = answer("month", NOW - datetime.timedelta(days=20))
    always = answer("always", LONG_AGO)
    dream().decay(NOW)
    assert [stored(memory["id"])["retired"] for memory in (stale_day, stale_month)] == ["expired", "expired"]
    assert [memory["id"] for memory in memories().living()] == [today["id"], this_month["id"], always["id"]]


@pytest.mark.spec("DRM-6")
def test_portrait_is_written_when_a_person_memory_changed(home_store, monkeypatch):
    person = remembered("likes short replies", "Wants it brief.", "person")
    remembered("uses zsh", "The shell is zsh.")
    asked = quick_model_replying(monkeypatch, lambda messages: " ".join(["word"] * 120))
    portraying = dream()
    portraying.changed = [person["id"]]
    portraying.portrait(NOW)
    assert Home.store().get("dream", "portrait") == " ".join(["word"] * 100)
    assert asked[0][0]["content"] == Prompts.DREAM_PORTRAIT_SYSTEM.format(words=PORTRAIT_WORDS)
    assert "likes short replies" in asked[0][1]["content"]
    assert "uses zsh" not in asked[0][1]["content"]
    assert dream_events()[0] | {"gist": ""} == {
        "step": "portrait",
        "change": "portrait",
        "id": "portrait",
        "gist": "",
    }


@pytest.mark.spec("DRM-6")
def test_portrait_is_left_when_no_person_memory_changed(home_store, monkeypatch):
    fact = remembered("uses zsh", "The shell is zsh.")
    remembered("likes short replies", "Wants it brief.", "person")
    asked = quick_model_replying(monkeypatch, lambda messages: "A person.")
    portraying = dream()
    portraying.changed = [fact["id"]]
    portraying.portrait(NOW)
    assert asked == []
    assert Home.store().get("dream", "portrait") is None


@pytest.mark.spec("DRM-6")
def test_an_empty_or_failed_portrait_reply_changes_nothing(home_store, monkeypatch):
    person = remembered("likes short replies", "Wants it brief.", "person")
    for reply in (lambda messages: "  ", failing):
        quick_model_replying(monkeypatch, reply)
        portraying = dream()
        portraying.changed = [person["id"]]
        portraying.portrait(NOW)
    assert Home.store().get("dream", "portrait") is None
    assert dream_events() == []


def failing(messages):
    raise OllamaError("down")


@pytest.mark.spec("DRM-8")
def test_show_dream_says_the_last_dream_its_counts_the_waiting_episodes_and_a_queued_dream(
    home_store, capsys
):
    episode()
    episode(message="show the date")
    counts = {"consolidate": 2, "revise": 1, "decay": 0, "portrait": 1}
    Home.store().put("dream", "state", {"done": [], "changed": [], "counts": counts, "last": NOW.isoformat()})
    Queue.add_front(Jobs.save(dict(Jobs.new(["ask", "why"]), state="queued"))["number"])
    dream = Jobs.save(dict(Jobs.new(["dream", "run"]), state="queued"))["number"]
    Queue.place(dream, lambda others: others + [dream])
    show_dream()
    out = capsys.readouterr().out
    assert f"Last dream {NOW.isoformat()}" in out
    assert "consolidate 2 · revise 1 · decay 0 · portrait 1" in out
    assert "2 fresh episodes wait for a dream" in out and "A dream is queued" in out


@pytest.mark.spec("DRM-8")
def test_show_dream_without_a_dream_says_none_yet_and_how_to_dream(home_store, capsys):
    show_dream()
    out = capsys.readouterr().out
    assert "No dream yet" in out and "0 fresh episodes" in out and "tinker dream run" in out


@pytest.mark.spec("DRM-9")
def test_the_memory_graph_links_memories_to_their_episodes_and_a_merged_memory_to_its_successor(home_store):
    episode()
    episode(message="show the date")
    remembered("Lists files often", "The person lists files.", sources=("e1",), retired="merged into m2")
    remembered(
        "Likes listings", "The person likes file and date listings.", kind="person", sources=("e1", "e2")
    )
    graph = MemoryGraph.build()
    assert {
        node_id: (node["kind"], node["status"], node["group"]) for node_id, node in graph.nodes.items()
    } == {
        "e1": ("episode", "", "episode"),
        "e2": ("episode", "", "episode"),
        "m1": ("memory", "retired", "fact"),
        "m2": ("memory", "", "person"),
    }
    assert graph.nodes["m2"]["label"] == "Likes listings" and graph.nodes["e2"]["label"] == "show the date"
    assert sorted((link["source"], link["target"], link["kind"]) for link in graph.links) == [
        ("m1", "e1", "source"),
        ("m1", "m2", "merged into"),
        ("m2", "e1", "source"),
        ("m2", "e2", "source"),
    ]
    assert graph.summary() == "2 episodes, 1 memory, 1 retired; 4 links"


@pytest.mark.spec("DRM-9", "CNV-23")
def test_show_memory_graph_writes_the_still_page_and_says_where(home_store, capsys):
    episode()
    remembered("Lists files often", "The person lists files.")
    show_memory_graph()
    out = capsys.readouterr().out
    page = Home.memory_page().read_text()
    assert "Likes" not in page and "Lists files often" in page and "1 episode, 1 memory; 1 links" in page
    assert "1 episode, 1 memory; 1 links" in out and f"Page: {Home.memory_page()}" in out


@pytest.fixture
def live_graph(home_store, monkeypatch):
    monkeypatch.setattr(LiveGraph, "POLL_SECONDS", 0.01)
    refused = socket.socket.connect
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda self, address: (_socket.socket.connect if address[0] == "127.0.0.1" else refused)(
            self, address
        ),
    )
    live = LiveGraph()
    thread = threading.Thread(target=live.server.serve_forever, daemon=True)
    thread.start()
    yield live
    live.server.shutdown()
    live.server.server_close()


@pytest.mark.spec("DRM-9")
def test_live_graph_serves_the_graph_page_only_behind_its_token(live_graph):
    episode()
    remembered("Lists files often", "The person lists files.")
    with urllib.request.urlopen(live_graph.url, timeout=5) as response:
        page = response.read().decode()
    assert response.status == 200 and "Lists files often" in page
    assert 'new EventSource("events")' in page and page.index("EventSource") < page.index("</body>")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(live_graph.url.replace(live_graph.token, "wrong"), timeout=5)
    assert refused.value.code == 403


@pytest.mark.spec("DRM-9")
def test_live_graph_streams_each_new_dream_event(live_graph):
    Log(Home.store(), "dream events").add({"step": "old"})
    with urllib.request.urlopen(live_graph.url + "events", timeout=5) as response:
        assert response.headers["Content-Type"] == "text/event-stream"
        Log(Home.store(), "dream events").add({"step": "new"})
        newest = Home.store().entries("dream events")[-1].id
        lines = iter(response.readline, b"")
        assert next(line for line in lines if line.startswith(b"data:")).decode() == f"data: {newest}\n"


@pytest.mark.spec("DRM-9")
def test_the_memory_graph_page_names_its_episodes(home_store):
    episode()
    remembered("Lists files often", "The person lists files.")
    show_memory_graph()
    page = Home.memory_page().read_text()
    assert "Episodes (1)" in page
    assert "Memories (1)" in page


@pytest.mark.spec("DRM-9")
def test_the_memory_graph_page_draws_and_colours_every_kind_it_made(home_store):
    episode()
    remembered("Lists files often", "The person lists files.")
    show_memory_graph()
    page = Home.memory_page().read_text()
    data = page.split('id="data">', 1)[1].split("</script>", 1)[0]
    made = {node["kind"] for node in json.loads(data)["nodes"]}
    drawn = json.loads(page.split("var KINDS=", 1)[1].split(";", 1)[0].replace("'", '"'))
    assert made == {"episode", "memory"}
    assert made <= set(drawn)
    assert all(f"--{kind}:" in page for kind in made)


class ClosedBrowser:
    def write(self, data):
        raise BrokenPipeError

    def flush(self):
        pass


@pytest.mark.spec("DRM-9")
def test_live_graph_writes_a_keepalive_so_a_closed_browser_ends_its_stream(live_graph, monkeypatch):
    def polled_without_writing(seconds):
        raise AssertionError("polled without writing")

    monkeypatch.setattr("tinkerkit.dreaming.time.sleep", polled_without_writing)
    with pytest.raises(BrokenPipeError):
        live_graph.stream(ClosedBrowser(), live_graph.newest_event())
