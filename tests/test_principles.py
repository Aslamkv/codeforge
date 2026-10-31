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


def kept_principle(principles, **changes):
    principle = a_principle(**changes)
    principles.put(principle)
    return principle


def new_evidence(ref):
    return {"kind": "run", "ref": ref, "source": "lessons"}


def test_apply_add_keeps_a_new_candidate_that_was_born(tinker_home):
    principles = Principles(None)
    delta = {
        "kind": "add",
        "when": "a command needs a path",
        "do": "pass it as an argument",
        "because": "scripts must not assume the cwd",
        "exceptions": [],
        "scope": "python",
        "phase": "code",
        "evidence": [new_evidence("run-8")],
    }
    [added] = principles.apply([delta], "run-8")
    assert principles.get(added.id) == added
    assert added.status == "candidate"
    assert added.do == "pass it as an argument"
    assert added.phase == "code"
    assert added.evidence == [new_evidence("run-8")]
    assert [event["event"] for event in added.history] == ["born"]


def test_apply_drops_an_add_with_no_do_or_no_evidence(tinker_home):
    principles = Principles(None)
    no_do = {"kind": "add", "when": "a test runs", "do": "", "scope": "python", "evidence": [new_evidence("r")]}
    no_evidence = {"kind": "add", "when": "a test runs", "do": "keep it fast", "scope": "python", "evidence": []}
    assert principles.apply([no_do, no_evidence], "run-8") == []
    assert principles.all() == []


def test_apply_add_like_a_kept_principle_strengthens_that_one(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    delta = {
        "kind": "add",
        "when": "a test reads a file",
        "do": "give it a temporary directory.",
        "scope": "python",
        "evidence": [new_evidence("run-9")],
    }
    [changed] = principles.apply([delta], "run-9")
    assert changed.id == kept.id
    assert changed.evidence == kept.evidence + [new_evidence("run-9")]
    assert changed.history[-1]["event"] == "confirmed"
    assert len(principles.all()) == 1


def test_apply_add_like_a_principle_of_another_scope_adds_a_new_one(tinker_home):
    principles = Principles(None)
    kept_principle(principles)
    delta = {
        "kind": "add",
        "when": "a test reads a file",
        "do": "give it a temporary directory.",
        "scope": "rust",
        "evidence": [new_evidence("run-9")],
    }
    principles.apply([delta], "run-9")
    assert sorted(principle.scope for principle in principles.all()) == ["python", "rust"]


def test_apply_strengthen_confirms_and_adds_the_evidence(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    [changed] = principles.apply([{"kind": "strengthen", "id": kept.id, "evidence": [new_evidence("run-9")]}], "run-9")
    assert principles.get(kept.id) == changed
    assert changed.evidence == kept.evidence + [new_evidence("run-9")]
    assert changed.history[-1]["event"] == "confirmed"
    assert changed.history[-1]["run"] == "run-9"


def test_apply_contradict_counts_harm_and_records_it(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    [changed] = principles.apply([{"kind": "contradict", "id": kept.id, "evidence": [new_evidence("run-9")]}], "run-9")
    assert changed.harmed == 1
    assert changed.status == "candidate"
    assert changed.history[-1]["event"] == "contradicted"
    assert changed.evidence == kept.evidence


def test_apply_contradict_retires_one_harmed_more_than_its_evidence(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    contradict = {"kind": "contradict", "id": kept.id, "evidence": [new_evidence("run-9")]}
    [changed] = principles.apply([contradict, contradict], "run-9")
    assert changed.harmed == 2
    assert changed.status == "retired"
    assert changed.history[-1]["event"] == "retired"
    assert "contradicted" in changed.history[-1]["reason"]
    assert principles.kept("python") == []


def test_apply_except_adds_the_exception_and_its_evidence(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    delta = {"kind": "except", "id": kept.id, "exception": "the test is a benchmark", "evidence": [new_evidence("run-9")]}
    [changed] = principles.apply([delta], "run-9")
    assert changed.exceptions == kept.exceptions + ["the test is a benchmark"]
    assert changed.evidence == kept.evidence + [new_evidence("run-9")]
    assert principles.get(kept.id) == changed


def test_apply_drops_unknown_ids_and_kinds(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    deltas = [
        {"kind": "strengthen", "id": "missing", "evidence": [new_evidence("run-9")]},
        {"kind": "contradict", "id": "missing", "evidence": [new_evidence("run-9")]},
        {"kind": "except", "id": "missing", "exception": "never", "evidence": []},
        {"kind": "rewrite", "id": kept.id},
    ]
    assert principles.apply(deltas, "run-9") == []
    assert principles.all() == [kept]
