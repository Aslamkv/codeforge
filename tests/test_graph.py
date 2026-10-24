"""GRF: the memory graph: what Tinker knows as nodes and links, and the page that shows it."""

import json
import re
import sys

import pytest

from tinkerkit import cli
from tinkerkit.cli import Cli, Commands
from tinkerkit.graph import Graph, GraphPage
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.project import Runs
from tinkerkit.settings import Home
from tinkerkit.workspace import TaughtCommands, Toolchain
from conftest import TOOLCHAIN


def lesson(text, phase="tests", scope="any"):
    return {"text": text, "phase": phase, "scope": scope}


@pytest.fixture
def memory(home_store):
    """A home that knows a little of everything: Python's toolchain, a fix, lessons, a fact proven from a topic,
    a research finding, a retired lesson, a command of the person's, and one run with its line kept."""
    Library.save("flask", {"topic": "flask", "name": "Flask", "summary": "A web framework.", "kind": "research",
                           "questions": [{"question": "How?"}],
                           "facts": [{"phase": "code", "text": "Flask routes use @app.route", "source": "https://a.org/x"},
                                     {"phase": "tests", "text": "Use app.test_client()", "sites": ["https://b.org"]}],
                           "findings": [{"question": 1, "answer": "Routes and a test client", "facts": [1, 2],
                                         "confidence": "medium"}]})
    route = Library.read("flask")["facts"][0]["id"]
    known = Knowledge("Python", "r1")
    known.remember_toolchain(Toolchain(TOOLCHAIN))
    known.remember_fix({"failure": "no module named calc", "cause": "pythonpath", "replay": {"toolchain": {}}})
    known.add_lessons([lesson("Seed random generators inside tests", scope="language"),
                       lesson("Name each test after the behaviour it checks"),
                       lesson("Exit with code 1 after an error <b>", "code")], [])
    seed, name, _ = (lesson["id"] for lesson in known.known_lessons())
    Knowledge("Python", "r2").prove({f"lesson:{seed}": ["tests"], f"fact:flask:{route}": ["code"]})
    Knowledge("Python", "r2").disprove({f"lesson:{name}": ["tests"]}, ("tests",))
    Runs.remember({"id": "r1", "task": "a calculator", "status": "done", "created": "c", "updated": "u"}, "Python")
    TaughtCommands.teach("act", "echo hi")
    return {"route": route, "seed": seed, "name": name}


@pytest.mark.spec("GRF-1")
def test_one_node_per_thing_tinker_knows(memory):
    nodes = Graph.build().nodes
    kinds = {node_id: node["kind"] for node_id, node in nodes.items()}
    assert kinds["lang:python"] == kinds["any"] == "language" and kinds["topic:flask"] == "topic"
    assert kinds["commands"] == "commands" and kinds["command:act"] == "command"
    assert kinds["toolchain:python"] == "toolchain" and kinds["fix:python:no module named calc"] == "fix"
    assert kinds[f"lesson:{memory['seed']}"] == "lesson" and kinds[f"known:{memory['route']}"] == "fact"
    assert kinds[f"fact:flask:{memory['route']}"] == "fact"
    assert [node["kind"] for node in nodes.values() if node["id"].startswith("finding:flask:")] == ["finding"]
    retired = nodes["retired:any:0"]
    assert retired["status"] == "retired" and retired["text"] == "Name each test after the behaviour it checks"
    assert ["why", "shown in the tests prompts of a step that failed"] in retired["details"]
    seed = nodes[f"lesson:{memory['seed']}"]
    assert set(seed) == {"id", "kind", "label", "text", "status", "group", "details", "sources"}
    assert seed["group"] == "python" and seed["status"] == "proven" and ["helped", "1"] in seed["details"]
    assert nodes[f"fact:flask:{memory['route']}"]["sources"] == ["https://a.org/x"]


@pytest.mark.spec("GRF-2")
def test_links_join_items_to_what_they_belong_to_and_to_their_runs(memory):
    graph = Graph.build()
    links = {(link["source"], link["target"], link["kind"]) for link in graph.links}
    seed, route = f"lesson:{memory['seed']}", memory["route"]
    finding = next(node for node in graph.nodes if node.startswith("finding:"))
    assert {(seed, "lang:python", "belongs_to"), ("command:act", "commands", "belongs_to"),
            (finding, f"fact:flask:{route}", "cites"), (f"fact:flask:{route}", f"known:{route}", "became"),
            (seed, "run:r1", "taught_by"), (seed, "run:r2", "helped_in"),
            ("retired:any:0", "run:r2", "failed_in")} <= links
    assert graph.nodes["run:r1"]["text"] == "a calculator" and ["status", "done"] in graph.nodes["run:r1"]["details"]
    assert graph.nodes["run:r2"]["text"].startswith("no line about this run is kept")


@pytest.mark.spec("GRF-2")
def test_a_rule_links_to_the_facts_and_lessons_it_rests_on(memory):
    entry = Library.read("flask")
    entry["rules"] = [{"phase": "code", "text": "Route with @app.route", "cites": [memory["route"], memory["seed"]]}]
    Library.save("flask", entry)
    graph = Graph.build()
    rule = next(node for node in graph.nodes if node.startswith("rule:"))
    links = {(link["source"], link["target"], link["kind"]) for link in graph.links}
    assert {(rule, f"fact:flask:{memory['route']}", "cites"), (rule, f"lesson:{memory['seed']}", "cites")} <= links
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
    texts = ["Seed random generators inside tests", "Always seed random generators before tests run",
             "Exit with code 1 after an error", "Mock the clock in tests that read the time"]
    Knowledge("Python").add_lessons([lesson(text) for text in texts], [])
    ids = {lesson["text"]: f"lesson:{lesson['id']}" for lesson in Knowledge("Python").known_lessons()}
    similar = lambda graph: {frozenset((link["source"], link["target"])) for link in graph.links
                             if link["kind"] == "similar"}
    first, unlike, also_unlike, like_the_first = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [0.9, 0.1, 0]
    meaning = dict(zip(texts, (first, unlike, also_unlike, like_the_first)))
    by_meaning = Graph.build(FakeEmbeddings(meaning))
    assert by_meaning.similar_by == "meaning (embed-model)"
    assert similar(by_meaning) == {frozenset((ids[texts[0]], ids[texts[3]]))}
    by_words = Graph.build(FakeEmbeddings(None))
    assert by_words.similar_by == "words (embed-model gave no embeddings)"
    assert similar(by_words) == {frozenset((ids[texts[0]], ids[texts[1]]))}
    assert Graph.build().similar_by == "words (no embedding model selected (tinker embed NAME))"


@pytest.mark.spec("GRF-4")
def test_the_page_is_self_contained_with_a_plain_list(memory):
    page = GraphPage.html(Graph.build())
    assert not re.search(r"<script[^>]+src=|<link[^>]+href=|@import|url\(|<img", page)
    data = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))
    assert {node["id"] for node in data["nodes"]} == set(Graph.build().nodes) and data["links"]
    main = re.search(r'<main id="list">(.*?)</main>', page, re.S).group(1)
    assert "<h2>Python</h2>" in main and "<h2>Any language</h2>" in main and "Seed random generators inside tests" in main
    assert "Exit with code 1 after an error &lt;b&gt;" in main and "<b>" not in main
    assert "Retired (1)" in main and '<a href="https://a.org/x">source</a>' in main


@pytest.mark.spec("GRF-5", "CLI-11")
def test_memory_graph_writes_the_page_and_opens_it_only_in_a_terminal(memory, monkeypatch, capsys):
    assert Cli.parse(["memory", "--graph"]).graph is True and Cli.parse(["memory"]).graph is False
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    Commands.show_memory(graph=True)
    out = capsys.readouterr().out
    assert Home.graph_page().read_text().startswith("<!doctype html>") and opened == []
    assert "Tinker's memory: 1 language, 1 topic, 2 lessons" in out and "1 retired" in out
    assert f"Page: {Home.graph_page()}" in out and "Similar lessons found by words" in out
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    Commands.show_memory(graph=True)
    assert opened == [Home.graph_page().as_uri()]


@pytest.mark.spec("CLI-11")
def test_memory_counts_what_is_retired(memory, capsys):
    Commands.show_memory()
    out = capsys.readouterr().out
    assert "♻️  1 item did not hold and was retired: never shown to the model again" in out
    assert "Explore it     tinker memory --graph" in out


@pytest.mark.spec("GRF-1")
def test_a_facts_slot_and_a_topics_missing_slots_are_in_their_details(home_store):
    Library.save("widgetlib", {"topic": "Widgetlib", "kind": "tool", "name": "Widgetlib", "summary": "s",
                               "status": "incomplete", "missing": ["this-version", "fit"], "passages": [{"id": "P1"}],
                               "facts": [{"phase": "code", "text": "Create a Widget", "slot": "core-usage",
                                          "passages": ["P1"], "source": "https://widgetlib.dev/start"}]})
    nodes = Graph.build().nodes
    fact = next(node for node in nodes.values() if node["id"].startswith("fact:widgetlib:"))
    assert ["slot", "core-usage"] in fact["details"] and fact["sources"] == ["https://widgetlib.dev/start"]
    assert ["missing", "this-version, fit"] in nodes["topic:widgetlib"]["details"]
    assert "Create a Widget" in GraphPage.html(Graph.build())
