import hashlib
import json
import re
import sys

import pytest
from conftest import TOOLCHAIN

from tinkerkit import cli, settings
from tinkerkit.cli import Cli, Commands
from tinkerkit.graph import Graph, GraphPage, MemoryBrowser, short_ids
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.principles import Principle, Principles
from tinkerkit.project import Runs
from tinkerkit.settings import Home
from tinkerkit.workspace import TaughtCommands, Toolchain


def lesson(text, phase="tests", scope="any"):
    return {"text": text, "phase": phase, "scope": scope}


@pytest.fixture
def memory(home_store):
    Library.save(
        "flask",
        {
            "topic": "flask",
            "name": "Flask",
            "summary": "A web framework.",
            "kind": "research",
            "questions": [{"question": "How?"}],
            "facts": [
                {"phase": "code", "text": "Flask routes use @app.route", "source": "https://a.org/x"},
                {"phase": "tests", "text": "Use app.test_client()", "sites": ["https://b.org"]},
            ],
            "findings": [
                {"question": 1, "answer": "Routes and a test client", "facts": [1, 2], "confidence": "medium"}
            ],
        },
    )
    route = Library.read("flask")["facts"][0]["id"]
    known = Knowledge("Python", "r1")
    known.remember_toolchain(Toolchain(TOOLCHAIN))
    known.remember_fix(
        {"failure": "no module named calc", "cause": "pythonpath", "replay": {"toolchain": {}}}
    )
    known.add_lessons(
        [
            lesson("Seed random generators inside tests", scope="language"),
            lesson("Name each test after the behaviour it checks"),
            lesson("Exit with code 1 after an error <b>", "code"),
        ],
        [],
    )
    seed, name, _ = (lesson["id"] for lesson in known.known_lessons())
    Knowledge("Python", "r2").prove({f"lesson:{seed}": ["tests"], f"fact:flask:{route}": ["code"]})
    Knowledge("Python", "r2").disprove({f"lesson:{name}": ["tests"]}, ("tests",))
    Runs.remember(
        {"id": "r1", "task": "a calculator", "status": "done", "created": "c", "updated": "u"}, "Python"
    )
    TaughtCommands.teach("act", "echo hi")
    return {"route": route, "seed": seed, "name": name}


@pytest.mark.spec("GRF-1")
def test_one_node_per_thing_tinker_knows(memory):
    nodes = Graph.build().nodes
    kinds = {node_id: node["kind"] for node_id, node in nodes.items()}
    assert kinds["lang:python"] == kinds["any"] == "language" and kinds["topic:flask"] == "topic"
    assert kinds["commands"] == "commands" and kinds["command:act"] == "command"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", nodes["command:act"]["day"])
    assert kinds["toolchain:python"] == "toolchain" and kinds["fix:python:no module named calc"] == "fix"
    assert kinds[f"principle:{memory['seed']}"] == "principle" and kinds[f"known:{memory['route']}"] == "fact"
    assert kinds[f"fact:flask:{memory['route']}"] == "fact"
    assert [node["kind"] for node in nodes.values() if node["id"].startswith("finding:flask:")] == ["finding"]
    retired = nodes[f"principle:{memory['name']}"]
    assert (
        retired["status"] == "retired" and retired["text"] == "Name each test after the behaviour it checks."
    )
    seed = nodes[f"principle:{memory['seed']}"]
    assert set(seed) == {"id", "kind", "label", "text", "status", "group", "details", "sources", "day"}
    assert seed["group"] == "python" and seed["status"] == "proven" and ["helped", "1"] in seed["details"]
    assert nodes[f"fact:flask:{memory['route']}"]["sources"] == ["https://a.org/x"]


@pytest.mark.spec("GRF-1")
def test_a_principle_is_a_node_in_its_scope_with_its_parts(home_store):
    principle = Principle.new(
        "a test reads the clock",
        "freeze the clock",
        scope="timekeeping",
        run="r1",
        because="the time changes between runs",
        exceptions=["the test measures elapsed time"],
        evidence=[{"source": "https://a.org/clock"}, {"source": "a run"}],
    )
    Principles(None).put(principle)
    graph = Graph.build()
    node = graph.nodes[f"principle:{principle.id}"]
    assert node["kind"] == "principle" and node["status"] == "candidate"
    assert node["text"] == (
        "When a test reads the clock, freeze the clock, because the time changes between runs, "
        "unless the test measures elapsed time."
    )
    assert node["group"] == "timekeeping" and graph.nodes["topic:timekeeping"]["kind"] == "topic"
    assert ["when", "a test reads the clock"] in node["details"] and ["harmed", "0"] in node["details"]
    assert node["sources"] == ["https://a.org/clock"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", node["day"])
    assert (f"principle:{principle.id}", "run:r1", "taught_by") in linked(graph)


def linked(graph):
    return {(link["source"], link["target"], link["kind"]) for link in graph.links}


@pytest.mark.spec("GRF-2")
def test_links_join_items_to_what_they_belong_to_and_to_their_runs(memory):
    graph = Graph.build()
    links = linked(graph)
    seed, route = f"principle:{memory['seed']}", memory["route"]
    finding = next(node for node in graph.nodes if node.startswith("finding:"))
    assert {
        (seed, "lang:python", "belongs_to"),
        ("command:act", "commands", "belongs_to"),
        (finding, f"fact:flask:{route}", "cites"),
        (f"fact:flask:{route}", f"known:{route}", "became"),
        (seed, "run:r1", "taught_by"),
        (seed, "run:r2", "helped_in"),
        (f"principle:{memory['name']}", "run:r2", "failed_in"),
    } <= links
    assert (
        graph.nodes["run:r1"]["text"] == "a calculator"
        and ["status", "done"] in graph.nodes["run:r1"]["details"]
    )
    assert graph.nodes["run:r2"]["text"].startswith("no line about this run is kept")


@pytest.mark.spec("GRF-2")
def test_a_rule_links_to_the_facts_and_lessons_it_rests_on(memory):
    entry = Library.read("flask")
    entry["rules"] = [
        {"phase": "code", "text": "Route with @app.route", "cites": [memory["route"], memory["seed"]]}
    ]
    Library.save("flask", entry)
    graph = Graph.build()
    rule = next(node for node in graph.nodes if node.startswith("rule:"))
    links = linked(graph)
    assert {
        (rule, f"fact:flask:{memory['route']}", "cites"),
        (rule, f"principle:{memory['seed']}", "cites"),
    } <= links
    assert graph.nodes[rule]["kind"] == "rule" and "1 rule" in graph.summary()


class FakeEmbeddings:
    name, failed = "embed-model", ""

    def __init__(self, vectors):
        self.given = vectors

    def vectors(self, texts, keep=True, prune=False):
        if self.given is None:
            self.failed = "embed-model gave no embeddings"
            return None
        return [self.given.get(text, [0.0, 1.0]) for text in texts]


@pytest.mark.spec("GRF-3")
def test_similar_lessons_by_meaning_else_by_words(home_store):
    texts = [
        "Seed random generators inside tests",
        "Always seed random generators before tests run",
        "Exit with code 1 after an error",
        "Mock the clock in tests that read the time",
    ]
    Knowledge("Python").add_lessons([lesson(text) for text in texts], [])
    ids = {lesson["text"]: f"principle:{lesson['id']}" for lesson in Knowledge("Python").known_lessons()}
    similar = lambda graph: {
        frozenset((link["source"], link["target"])) for link in graph.links if link["kind"] == "similar"
    }
    first, unlike, also_unlike, like_the_first = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [0.9, 0.1, 0]
    meaning = dict(zip((f"{text}." for text in texts), (first, unlike, also_unlike, like_the_first)))
    by_meaning = Graph.build(FakeEmbeddings(meaning))
    assert by_meaning.similar_by == "meaning (embed-model)"
    assert similar(by_meaning) == {frozenset((ids[texts[0]], ids[texts[3]]))}
    by_words = Graph.build(FakeEmbeddings(None))
    assert by_words.similar_by == "words (embed-model gave no embeddings)"
    assert similar(by_words) == {frozenset((ids[texts[0]], ids[texts[1]]))}
    assert Graph.build().similar_by == "words (no embedding model selected (tinker model NAME))"


@pytest.mark.spec("GRF-4")
def test_the_page_is_self_contained_with_a_plain_list(memory):
    page = GraphPage.html(Graph.build())
    assert not re.search(r"<script[^>]+src=|<link[^>]+href=|@import|url\(|<img", page)
    data = json.loads(
        re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
    )
    assert {node["id"] for node in data["nodes"]} == set(Graph.build().nodes) and data["links"]
    main = re.search(r'<main id="list">(.*?)</main>', page, re.S).group(1)
    assert (
        "<h2>Python</h2>" in main
        and "<h2>Any language</h2>" in main
        and "Seed random generators inside tests" in main
    )
    assert "Exit with code 1 after an error &lt;b&gt;" in main and "<b>" not in main
    assert "Retired (1)" in main and '<a href="https://a.org/x">source</a>' in main


@pytest.mark.spec("GRF-5", "CLI-5")
def test_memory_graph_writes_the_page_and_opens_it_only_in_a_terminal(memory, monkeypatch, capsys):
    assert Cli.parse(["memory", "--graph"]).graph is True and Cli.parse(["memory"]).graph is False
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    Commands.show_memory(graph=True)
    out = capsys.readouterr().out
    assert Home.graph_page().read_text().startswith("<!doctype html>") and opened == []
    assert "Tinker's memory: 1 language, 1 topic, 2 principles" in out and "1 retired" in out
    assert f"Page: {Home.graph_page()}" in out and "Similar lessons found by words" in out
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    Commands.show_memory(graph=True)
    assert opened == [Home.graph_page().as_uri()]


def browse(*words):
    MemoryBrowser(Graph.build()).show(MemoryBrowser.request(list(words)))


def short_id(node_id):
    return digest(node_id)[:6]


def digest(node_id):
    return hashlib.sha1(node_id.encode()).hexdigest()


@pytest.mark.spec("GRF-7")
def test_a_short_id_is_six_hex_digits_made_longer_only_where_two_items_share_them():
    assert short_ids(["lesson:1", "fact:x"]) == {
        "lesson:1": short_id("lesson:1"),
        "fact:x": short_id("fact:x"),
    }
    sharing_six_digits = short_ids(["lesson:438", "lesson:524", "lesson:1"])
    assert sharing_six_digits == {
        "lesson:438": "2d35d66",
        "lesson:524": "2d35d6b",
        "lesson:1": short_id("lesson:1"),
    }


@pytest.mark.spec("GRF-6", "GRF-8")
def test_memory_lists_the_parts_with_what_each_holds(memory, capsys):
    browse()
    out = capsys.readouterr().out
    assert "Python (memory python): 1 toolchain, 1 fix, 1 fact, 1 principle; 4 proven · nothing built yet" in out
    assert "Any language (memory any): 1 principle; 0 proven" in out
    assert "Flask (memory flask): 2 facts, 1 finding; 1 proven" in out
    assert "Your commands (memory commands): 1 command; 1 proven" in out
    assert "1 item did not hold and was retired" in out
    for tip in (
        "tinker memory NAME",
        "tinker memory show ID",
        "tinker memory find WORDS",
        "tinker memory recent",
    ):
        assert tip in out
    assert "tinker memory --graph" in out and "tinker teach" in out and "tinker clear PART" in out


@pytest.mark.spec("GRF-8")
def test_memory_with_nothing_learned_says_how_to_start(home_store, capsys):
    browse()
    out = capsys.readouterr().out
    assert "Nothing yet: it learns from every build" in out and "memory any" not in out


@pytest.mark.spec("GRF-9")
def test_memory_name_shows_one_part_its_items_with_short_ids_and_counts_the_retired(memory, capsys):
    browse("PYTHON")
    out = capsys.readouterr().out
    seed = f"principle:{memory['seed']}"
    assert f"[{short_id(seed)}] ✅ principle Seed random generators inside tests." in out
    assert f"[{short_id('toolchain:python')}] ✅ toolchain" in out and "nothing built yet" in out
    assert "Exit with code 1" not in out and "retired" not in out
    browse("Any", "language")
    out = capsys.readouterr().out
    assert "🌱 principle Exit with code 1 after an error <b>." in out and "Name each test after" not in out
    assert "1 retired, not shown: tinker memory any retired" in out
    browse("any", "retired")
    out = capsys.readouterr().out
    assert f"[{short_id('principle:' + memory['name'])}] ♻️ principle Name each test after" in out
    assert "Exit with code 1" not in out
    browse("flask", "fact")
    out = capsys.readouterr().out
    assert "A web framework." in out and "Flask routes use @app.route" in out and "finding" not in out


@pytest.mark.spec("GRF-9")
def test_memory_kind_alone_lists_that_kind_across_memory_with_each_part(memory, capsys):
    browse("principles")
    out = capsys.readouterr().out
    assert "Seed random generators inside tests.  (python)" in out and "after an error <b>.  (any)" in out
    assert "toolchain" not in out and "Flask" not in out


@pytest.mark.spec("GRF-9")
def test_an_unknown_part_is_refused_saying_memory_lists_the_parts(memory):
    with pytest.raises(SystemExit, match="tinker memory lists the parts"):
        browse("cobol")


@pytest.mark.spec("GRF-6", "GRF-9")
def test_every_memory_list_comes_a_page_at_a_time(memory, monkeypatch, capsys):
    monkeypatch.setattr(settings, "PER_PAGE", 1)
    browse("python", "2")
    out = capsys.readouterr().out
    assert "Page 2 of 4: tinker memory python 3 for more." in out and out.count("] ") == 1
    browse("python", "lessons", "1")
    assert "Page 1 of 1" not in capsys.readouterr().out
    browse("2")
    assert "Page 2 of 4: tinker memory 3 for more." in capsys.readouterr().out


@pytest.mark.spec("GRF-9", "CLI-5")
def test_memory_words_are_a_name_a_kind_and_a_page(home_store, capsys):
    request = MemoryBrowser.request
    assert request([]) == ("parts", "", "", 1, [])
    assert request(["python", "Lessons", "2"]) == ("part", "python", "lesson", 2, ["python", "Lessons"])
    assert request(["any", "language"])[:4] == ("part", "any language", "", 1)
    assert request(["fixes"])[:4] == ("part", "", "fix", 1)
    assert request(["show", "2d35d6"])[:1] == ("show",) and request(["find", "seed", "tests"])[:1] == (
        "find",
    )
    assert request(["recent", "2"])[:4] == ("recent", "", "", 2)
    for wrong in (
        ["show"],
        ["show", "a", "b"],
        ["find"],
        ["recent", "x"],
        ["lessons", "facts"],
        ["python", "0"],
    ):
        with pytest.raises(ValueError, match="tinker memory NAME"):
            request(wrong)
    assert Cli.parse(["memory", "python", "lessons", "2"]).memory == request(["python", "lessons", "2"])
    with pytest.raises(SystemExit):
        Cli.parse(["memory", "--graph", "python"])
    assert "--graph takes no other words; use tinker memory [PAGE]" in capsys.readouterr().err


@pytest.mark.spec("GRF-10")
def test_memory_show_shows_one_item_whole_with_its_links_and_runs(memory, capsys):
    seed = f"principle:{memory['seed']}"
    Knowledge("Python", "r2").prove({f"lesson:{memory['seed']}": ["tests"]})
    browse("show", f"[{short_id(seed).upper()}]")
    out = capsys.readouterr().out
    assert "principle ✅" in out and "Seed random generators inside tests" in out
    assert "part: Python (tinker memory python)" in out and "day: 20" in out
    assert "@r1 a calculator — taught" in out and out.count("@r2 ") == 1 and "— helped ×2" in out
    assert "Correct it: tinker teach WHAT" in out and "Cites" not in out and "Similar" not in out
    for prefix in (digest(seed)[:7], digest(seed)):
        browse("show", prefix)
        assert "Seed random generators inside tests" in capsys.readouterr().out
    route = f"fact:flask:{memory['route']}"
    browse("show", short_id(route))
    out = capsys.readouterr().out
    assert "Cited by" in out and "Routes and a test client" in out
    assert "Became" in out and f"[{short_id('known:' + memory['route'])}]" in out
    assert "source: https://a.org/x" in out
    browse("show", short_id(f"known:{memory['route']}"))
    assert "Came from" in capsys.readouterr().out
    browse("show", short_id(f"principle:{memory['name']}"))
    out = capsys.readouterr().out
    assert "♻️" in out and "— failed" in out


@pytest.mark.spec("GRF-7", "GRF-10")
def test_memory_show_refuses_a_short_unknown_or_shared_id(memory):
    with pytest.raises(SystemExit, match="at least 6"):
        browse("show", short_id("toolchain:python")[:5])
    with pytest.raises(SystemExit, match="No item"):
        browse("show", "zzzzzz")
    graph = Graph.build()
    browser = MemoryBrowser(graph)
    first, second = browser.items[:2]
    browser.ids[first["id"]], browser.ids[second["id"]] = "abcdef1", "abcdef2"
    browser.digests[first["id"]], browser.digests[second["id"]] = "abcdef1", "abcdef2"
    with pytest.raises(SystemExit, match=r"2 items[\s\S]*\[abcdef1\][\s\S]*\[abcdef2\]"):
        browser.show(MemoryBrowser.request(["show", "abcdef"]))


@pytest.mark.spec("GRF-11")
def test_memory_find_lists_items_holding_every_word_retired_too(memory, monkeypatch, capsys):
    browse("find", "TESTS")
    out = capsys.readouterr().out
    assert "Seed random generators inside tests.  (python)" in out
    assert "♻️ principle Name each test after" not in out
    browse("find", "test", "name")
    assert "♻️ principle Name each test after the behaviour it checks.  (any)" in capsys.readouterr().out
    browse("find", "cobol")
    assert "Nothing in memory holds 'cobol'" in capsys.readouterr().out
    monkeypatch.setattr("tinkerkit.graph.MEMORY_FOUND_SHOWN", 1)
    browse("find", "e")
    out = capsys.readouterr().out
    assert out.count("] ") == 1 and "more: add words to narrow it" in out


@pytest.mark.spec("GRF-12")
def test_memory_recent_lists_kept_items_newest_first_with_day_and_part(memory, monkeypatch, capsys):
    graph = Graph.build()
    browser = MemoryBrowser(graph)
    for item in browser.items:
        item["day"] = ""
    seed, route = f"principle:{memory['seed']}", f"fact:flask:{memory['route']}"
    graph.nodes[seed]["day"], graph.nodes[route]["day"] = "2026-01-02", "2026-03-04"
    graph.nodes[f"principle:{memory['name']}"]["day"] = "2026-05-06"
    browser.show(MemoryBrowser.request(["recent"]))
    out = capsys.readouterr().out
    assert out.index("2026-03-04 [") < out.index("2026-01-02 [") and out.count("] ") == 2
    assert "Flask routes use @app.route  (flask)" in out and "Name each test" not in out
    monkeypatch.setattr(settings, "PER_PAGE", 1)
    browser.show(MemoryBrowser.request(["recent"]))
    assert "Page 1 of 2: tinker memory recent 2 for more." in capsys.readouterr().out


@pytest.mark.spec("GRF-1")
def test_a_facts_slot_and_a_topics_missing_slots_are_in_their_details(home_store):
    Library.save(
        "widgetlib",
        {
            "topic": "Widgetlib",
            "kind": "tool",
            "name": "Widgetlib",
            "summary": "s",
            "status": "incomplete",
            "missing": ["this-version", "fit"],
            "passages": [{"id": "P1"}],
            "facts": [
                {
                    "phase": "code",
                    "text": "Create a Widget",
                    "slot": "core-usage",
                    "passages": ["P1"],
                    "source": "https://widgetlib.dev/start",
                }
            ],
        },
    )
    nodes = Graph.build().nodes
    fact = next(node for node in nodes.values() if node["id"].startswith("fact:widgetlib:"))
    assert ["slot", "core-usage"] in fact["details"] and fact["sources"] == ["https://widgetlib.dev/start"]
    assert ["missing", "this-version, fit"] in nodes["topic:widgetlib"]["details"]
    assert "Create a Widget" in GraphPage.html(Graph.build())


def retired_item(was, reason, **fields):
    return dict(fields, status="retired", was=was, reason=reason, retired="2026-03-01 09:00")


@pytest.mark.spec("GRF-1")
def test_an_items_day_is_when_it_was_first_kept_and_a_retired_id_is_its_old_key(home_store):
    born = [{"event": "born", "run": "r1", "at": "2026-01-04 10:00"}, {"event": "helped", "at": "2026-02-09"}]
    Home.store().put(
        "languages",
        "python",
        {
            "language": "Python",
            "toolchain": TOOLCHAIN,
            "toolchain_saved": "2026-01-02 08:00",
            "lessons": [
                {"id": "a", "text": "Seed random", "added": "2026-01-03 10:00", "history": born},
                {"id": "b", "text": "Name tests", "added": "2026-01-05 10:00"},
            ],
            "facts": [{"id": "k", "text": "Use pytest"}],
            "fixes": [{"failure": "boom", "cause": "pythonpath", "added": "2026-01-06 10:00"}],
            "retired": [
                retired_item("lessons", "first", id="c", text="Old", added="2026-01-07 10:00"),
                retired_item("facts", "why", id="k2", text="Old fact"),
                retired_item("fixes", "why", failure="bang", cause="path"),
                retired_item("lessons", "second", id="c", text="Old again"),
            ],
        },
    )
    Library.save(
        "flask",
        {
            "name": "Flask",
            "learned": "2026-02-01 12:00",
            "facts": [{"phase": "code", "text": "Flask routes use @app.route"}],
            "retired": [retired_item("rules", "why", id="r", text="Old rule")],
        },
    )
    nodes = Graph.build().nodes
    route = Library.read("flask")["facts"][0]["id"]
    days = {node_id: node["day"] for node_id, node in nodes.items()}
    assert days["lesson:a"] == "2026-01-04" and days["lesson:b"] == "2026-01-05"
    assert days["toolchain:python"] == "2026-01-02" and days["fix:python:boom"] == "2026-01-06"
    assert days["known:k"] == "" and days[f"fact:flask:{route}"] == "2026-02-01"
    assert days["retired:lesson:c"] == "2026-01-07" and days["retired:rule:flask:r"] == "2026-02-01"
    assert {"retired:known:k2", "retired:fix:python:bang"} <= set(nodes)
    assert ["why", "first"] in nodes["retired:lesson:c"]["details"]
    assert ["why", "second"] in nodes["retired:lesson:c:2"]["details"]
    assert days["lang:python"] == days["topic:flask"] == ""
