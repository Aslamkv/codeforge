from tests.test_knowledge import EmbedServer
from tinkerkit.knowledge import Embeddings
from tinkerkit.model import Ollama
from tinkerkit.principles import Principle, Principles
from tinkerkit.settings import Home


def a_principle(**changes):
    fields = {
        "when": "a test reads a file",
        "do": "give it a temporary directory",
        "because": "tests must not touch the real home",
        "exceptions": ["the file is a checked-in fixture"],
        "scope": "python",
        "phase": "tests",
        "evidence": [{"kind": "run", "ref": "run-7", "source": "lessons"}],
    }
    return Principle.new(**{**fields, **changes})


def test_a_new_principle_is_a_candidate_with_an_id_from_when_and_do():
    principle = a_principle()
    assert principle.status == "candidate"
    assert len(principle.id) == 12
    assert principle.id == a_principle(because="another reason").id
    assert principle.id != a_principle(do="use tmp_path").id
    assert principle.history[0]["event"] == "born"


def test_a_principle_round_trips_through_its_document():
    principle = a_principle()
    assert Principle.from_doc(principle.to_doc()) == principle


def test_prompt_text_is_compact_and_names_no_evidence():
    assert a_principle().prompt_text() == (
        "a test reads a file → give it a temporary directory; unless the file is a checked-in fixture"
    )
    assert "run-7" not in a_principle().prompt_text()


def test_prompt_text_leaves_out_an_empty_when_and_no_exceptions():
    assert a_principle(when="", exceptions=[]).prompt_text() == "give it a temporary directory"


def test_english_is_a_sentence_that_names_no_evidence():
    assert a_principle().english() == (
        "When a test reads a file, give it a temporary directory, because tests must not touch the real home, "
        "unless the file is a checked-in fixture."
    )
    assert "run-7" not in a_principle().english()


def test_english_leaves_out_empty_parts():
    assert a_principle(when="", because="", exceptions=[]).english() == "Give it a temporary directory."


def test_principles_keeps_each_principle_as_one_document(tinker_home):
    principles = Principles(None)
    principle = a_principle()
    principles.put(principle)
    assert Home.store().get("principles", principle.id) == principle.to_doc()
    assert principles.get(principle.id) == principle
    assert principles.get("missing") is None
    assert principles.all() == [principle]


def test_kept_gives_principles_of_a_scope_that_are_not_retired(tinker_home):
    principles = Principles(None)
    python = a_principle()
    retired = a_principle(do="use tmp_path")
    retired.status = "retired"
    anywhere = a_principle(scope="any", do="name each topic")
    for principle in (python, retired, anywhere):
        principles.put(principle)
    assert principles.kept("python") == [python]
    assert principles.kept("any") == [anywhere]


def test_put_embeds_when_and_do(tinker_home, monkeypatch):
    server = EmbedServer()
    monkeypatch.setattr(Ollama, "get_json", staticmethod(server))
    Principles(Embeddings("embed-a")).put(a_principle())
    assert server.requests == [["a test reads a file", "give it a temporary directory"]]


def test_put_keeps_the_principle_when_the_embedding_server_fails(tinker_home, monkeypatch):
    monkeypatch.setattr(Ollama, "get_json", staticmethod(EmbedServer(broken=True)))
    principles = Principles(Embeddings("embed-a"))
    principle = a_principle()
    principles.put(principle)
    assert principles.get(principle.id) == principle
