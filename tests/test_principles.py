import json

import pytest

from tests.test_knowledge import EmbedServer
from tinkerkit import principles as principles_module
from tinkerkit.knowledge import Embeddings
from tinkerkit.model import Ollama
from tinkerkit.principles import Inducer, Principle, Principles
from tinkerkit.settings import MAX_FACT_CHARS, MAX_RULE_WHEN_CHARS, Home


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


def test_a_new_principle_is_a_candidate_with_an_id_from_scope_when_and_do():
    principle = a_principle()
    assert principle.status == "candidate"
    assert len(principle.id) == 12
    assert principle.id == a_principle(because="another reason").id
    assert principle.id != a_principle(do="use tmp_path").id
    assert principle.id != a_principle(scope="rust").id
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


def test_forget_scope_deletes_every_principle_of_that_scope_retired_or_not(tinker_home):
    principles = Principles(None)
    python = a_principle()
    retired = a_principle(do="use tmp_path")
    retired.status = "retired"
    anywhere = a_principle(scope="any", do="name each topic")
    for principle in (python, retired, anywhere):
        principles.put(principle)
    principles.forget_scope("python")
    assert principles.all() == [anywhere]


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
    no_do = {
        "kind": "add",
        "when": "a test runs",
        "do": "",
        "scope": "python",
        "evidence": [new_evidence("r")],
    }
    no_evidence = {
        "kind": "add",
        "when": "a test runs",
        "do": "keep it fast",
        "scope": "python",
        "evidence": [],
    }
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
    [changed] = principles.apply(
        [{"kind": "strengthen", "id": kept.id, "evidence": [new_evidence("run-9")]}], "run-9"
    )
    assert principles.get(kept.id) == changed
    assert changed.evidence == kept.evidence + [new_evidence("run-9")]
    assert changed.history[-1]["event"] == "confirmed"
    assert changed.history[-1]["run"] == "run-9"


def test_apply_contradict_counts_harm_and_records_it(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    [changed] = principles.apply(
        [{"kind": "contradict", "id": kept.id, "evidence": [new_evidence("run-9")]}], "run-9"
    )
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
    delta = {
        "kind": "except",
        "id": kept.id,
        "exception": "the test is a benchmark",
        "evidence": [new_evidence("run-9")],
    }
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


def test_apply_drops_an_except_with_no_exception(tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    delta = {"kind": "except", "id": kept.id, "exception": "", "evidence": [new_evidence("run-9")]}
    assert principles.apply([delta], "run-9") == []
    assert principles.get(kept.id) == kept


@pytest.mark.spec("PRI-6")
def test_about_ranks_principles_not_retired_best_first(tinker_home):
    principles = Principles(None)
    files = kept_principle(principles)
    names = kept_principle(principles, when="a function is public", do="name it after what it returns")
    kept_principle(
        principles, when="a file is read", do="give it a temporary directory path", status="retired"
    )
    assert principles.about("what a public function returns", 5) == [names, files]
    assert principles.about("what a public function returns", 1) == [names]


@pytest.mark.spec("PRI-6")
def test_about_embeds_only_the_text(tinker_home, monkeypatch):
    server = EmbedServer()
    monkeypatch.setattr(Ollama, "get_json", staticmethod(server))
    principles = Principles(Embeddings("embed-a"))
    kept_principle(principles)
    server.requests.clear()
    principles.about("tests that read files", 5)
    assert server.requests == [["tests that read files"]]


PASSAGES = [
    {"text": "Tests should never write to the user's home.", "source": "book:testing#2"},
    {"text": "Use a temporary directory for every test that reads a file.", "source": "book:testing#3"},
]


def induced(make_model, fake_ollama, reply, scope="python"):
    fake_ollama.reply(json.dumps(reply), when="Induce", times=2)
    return Inducer(make_model(), Principles(None)).induce(PASSAGES, scope)


@pytest.mark.spec("PRI-4")
def test_induce_shows_numbered_passages_then_the_kept_principles_of_its_scope(
    make_model, fake_ollama, tinker_home
):
    principles = Principles(None)
    kept = kept_principle(principles)
    kept_principle(principles, scope="rust", do="use tempfile")
    fake_ollama.reply(json.dumps({"deltas": []}), when="Induce")
    assert Inducer(make_model(), principles).induce(PASSAGES, "python") == []
    prompt = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "1. Tests should never write to the user's home." in prompt
    assert "2. Use a temporary directory for every test that reads a file." in prompt
    assert f"3. {kept.prompt_text()}" in prompt
    assert "use tempfile" not in prompt


@pytest.mark.spec("PRI-4")
def test_induce_shows_at_most_the_known_principles_allowed(make_model, fake_ollama, tinker_home, monkeypatch):
    monkeypatch.setattr(principles_module, "INDUCE_KNOWN_SHOWN", 1)
    principles = Principles(None)
    kept_principle(principles)
    kept_principle(principles, do="clean the directory after")
    fake_ollama.reply(json.dumps({"deltas": []}), when="Induce")
    Inducer(make_model(), principles).induce(PASSAGES, "python")
    prompt = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "3. " in prompt and "4. " not in prompt


@pytest.mark.spec("PRI-4")
def test_an_induced_add_gets_the_scope_and_its_cited_passages_as_evidence(
    make_model, fake_ollama, tinker_home
):
    reply = {
        "deltas": [
            {
                "kind": "add",
                "when": "a test reads a file " + "w" * 200,
                "do": "Give it a temporary directory " + "d" * 500,
                "because": "tests must not touch the home",
                "phase": "tests",
                "cites": [2, 2, 9],
            }
        ]
    }
    [delta] = induced(make_model, fake_ollama, reply)
    assert delta["kind"] == "add" and delta["scope"] == "python" and delta["phase"] == "tests"
    assert len(delta["do"]) == MAX_FACT_CHARS and len(delta["when"]) == MAX_RULE_WHEN_CHARS
    assert delta["because"] == "tests must not touch the home"
    assert delta["evidence"] == [{"kind": "passage", "source": "book:testing#3", "text": PASSAGES[1]["text"]}]


@pytest.mark.spec("PRI-4")
def test_an_induced_add_of_an_unknown_phase_is_for_any(make_model, fake_ollama, tinker_home):
    reply = {"deltas": [{"kind": "add", "do": "Write the test first", "phase": "lunch", "cites": [1]}]}
    assert induced(make_model, fake_ollama, reply)[0]["phase"] == "any"


@pytest.mark.spec("PRI-4")
def test_an_induced_delta_citing_no_given_passage_is_dropped(make_model, fake_ollama, tinker_home):
    reply = {
        "deltas": [
            {"kind": "add", "do": "Write the test first", "cites": [7]},
            {"kind": "add", "do": "Write the test first", "cites": []},
            {"kind": "add", "do": "Write the test first"},
        ]
    }
    assert induced(make_model, fake_ollama, reply) == []


@pytest.mark.spec("PRI-4")
def test_an_induced_change_names_a_shown_principle_by_its_number(make_model, fake_ollama, tinker_home):
    principles = Principles(None)
    kept = kept_principle(principles)
    reply = {
        "deltas": [
            {"kind": "strengthen", "id": 3, "cites": [2]},
            {"kind": "contradict", "id": 3, "cites": [1]},
            {"kind": "except", "id": 3, "exception": "the file is a fixture", "cites": [1]},
            {"kind": "strengthen", "id": 4, "cites": [2]},
            {"kind": "strengthen", "id": 1, "cites": [2]},
            {"kind": "rewrite", "id": 3, "cites": [2]},
        ]
    }
    fake_ollama.reply(json.dumps(reply), when="Induce")
    deltas = Inducer(make_model(), principles).induce(PASSAGES, "python")
    assert [(delta["kind"], delta["id"]) for delta in deltas] == [
        ("strengthen", kept.id),
        ("contradict", kept.id),
        ("except", kept.id),
    ]
    assert deltas[2]["exception"] == "the file is a fixture"
    assert deltas[0]["evidence"] == [
        {"kind": "passage", "source": "book:testing#3", "text": PASSAGES[1]["text"]}
    ]


@pytest.mark.spec("PRI-4")
def test_a_reply_with_no_list_of_deltas_gives_none(make_model, fake_ollama, tinker_home):
    assert induced(make_model, fake_ollama, {"rules": []}) == []


@pytest.mark.spec("RDG-9")
def test_induce_shows_what_to_look_for_when_given(make_model, fake_ollama, tinker_home):
    fake_ollama.reply(json.dumps({"deltas": []}), when="Read: chapter 3")
    Inducer(make_model(), Principles(None)).induce(
        PASSAGES, "python", look_for="how tests keep the home clean", label="Read: chapter 3"
    )
    prompt = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "Look for" in prompt and "how tests keep the home clean" in prompt


OLD_HISTORY = [{"event": "born", "run": "run-1", "at": "2026-01-01 10:00"}]


def seed_old_knowledge():
    store = Home.store()
    store.put(
        "languages",
        "python",
        {
            "projects": 2,
            "lessons": [
                {
                    "id": "lesson000001",
                    "phase": "tests",
                    "text": "use tmp_path",
                    "status": "proven",
                    "history": OLD_HISTORY,
                },
            ],
            "facts": [
                {
                    "id": "fact00000001",
                    "phase": "code",
                    "text": "uv runs scripts",
                    "status": "proven",
                    "topic": "uv",
                    "history": OLD_HISTORY,
                }
            ],
            "fixes": [{"failure": "boom", "fix": "pin it"}],
        },
    )
    store.put(
        "memory",
        "any",
        {
            "lessons": [
                {
                    "id": "lesson000002",
                    "phase": "spec",
                    "text": "ask first",
                    "status": "candidate",
                    "history": OLD_HISTORY,
                }
            ]
        },
    )
    store.put(
        "learned",
        "uv",
        {
            "topic": "uv",
            "overview": "a fast tool",
            "questions": [{"question": "How fast is it?"}],
            "facts": [
                {
                    "id": "topicfact001",
                    "phase": "toolchain",
                    "text": "uv is fast",
                    "status": "proven",
                    "sources": ["https://docs.astral.sh/uv"],
                    "history": OLD_HISTORY,
                },
                {
                    "phase": "book",
                    "text": "a point from chapter 2",
                    "chapter": 2,
                    "status": "proven",
                    "sources": ["#1 chapter 2"],
                },
            ],
            "rules": [
                {
                    "id": "rule00000001",
                    "phase": "config",
                    "text": "pin the python version",
                    "when": "a project starts",
                    "cites": ["topicfact001"],
                    "status": "candidate",
                }
            ],
            "findings": [
                {"question": 1, "answer": "very fast", "confidence": "high", "cites": ["topicfact001"]}
            ],
            "retired": [
                {
                    "id": "topicfact002",
                    "phase": "toolchain",
                    "text": "uv is slow",
                    "status": "retired",
                    "reason": "wrong",
                    "was": "facts",
                    "history": OLD_HISTORY,
                }
            ],
        },
    )


def migrated_by_do():
    return {principle.do: principle for principle in Principles(None).all()}


@pytest.mark.spec("PRI-8")
def test_migrate_turns_lessons_and_facts_into_principles_with_their_id_status_and_history(tinker_home):
    seed_old_knowledge()
    Principles(None).migrate()
    kept = migrated_by_do()
    lesson = kept["use tmp_path"]
    assert (lesson.id, lesson.status, lesson.history, lesson.scope, lesson.phase, lesson.when) == (
        "lesson000001",
        "proven",
        OLD_HISTORY,
        "python",
        "tests",
        "",
    )
    assert (kept["ask first"].id, kept["ask first"].scope, kept["ask first"].phase) == (
        "lesson000002",
        "any",
        "spec",
    )
    assert (kept["uv runs scripts"].id, kept["uv runs scripts"].scope) == ("fact00000001", "python")


@pytest.mark.spec("PRI-8")
def test_migrate_turns_topic_facts_rules_findings_and_reading_points_into_principles_of_the_topic(
    tinker_home,
):
    seed_old_knowledge()
    Principles(None).migrate()
    kept = migrated_by_do()
    fact = kept["uv is fast"]
    assert (fact.id, fact.scope, fact.phase, fact.status) == ("topicfact001", "uv", "toolchain", "proven")
    assert [evidence["source"] for evidence in fact.evidence] == ["https://docs.astral.sh/uv"]
    rule = kept["pin the python version"]
    assert (rule.id, rule.when, rule.phase, rule.scope) == (
        "rule00000001",
        "a project starts",
        "config",
        "uv",
    )
    assert [(evidence["source"], evidence["text"]) for evidence in rule.evidence] == [
        ("https://docs.astral.sh/uv", "uv is fast")
    ]
    finding = kept["How fast is it? very fast"]
    assert (finding.scope, finding.phase, len(finding.id)) == ("uv", "any", 12)
    assert [evidence["text"] for evidence in finding.evidence] == ["uv is fast"]
    point = kept["a point from chapter 2"]
    assert (point.phase, point.scope, [evidence["source"] for evidence in point.evidence]) == (
        "book",
        "uv",
        ["#1 chapter 2"],
    )


@pytest.mark.spec("PRI-8")
def test_migrate_empties_the_old_lists_keeps_the_rest_and_marks_the_home(tinker_home):
    seed_old_knowledge()
    Principles(None).migrate()
    store = Home.store()
    language = store.get("languages", "python")
    assert (language["lessons"], language["facts"], language["fixes"]) == (
        [],
        [],
        [{"failure": "boom", "fix": "pin it"}],
    )
    assert store.get("memory", "any")["lessons"] == []
    topic = store.get("learned", "uv")
    assert (topic["facts"], topic["rules"], topic["findings"], topic["retired"], topic["overview"]) == (
        [],
        [],
        [],
        [],
        "a fast tool",
    )
    assert store.get("migrated", "principles")["at"]


@pytest.mark.spec("PRI-8")
def test_migrate_turns_retired_topic_items_into_retired_principles(tinker_home):
    seed_old_knowledge()
    Principles(None).migrate()
    retired = migrated_by_do()["uv is slow"]
    assert (retired.id, retired.status, retired.scope, retired.phase, retired.history) == (
        "topicfact002",
        "retired",
        "uv",
        "toolchain",
        OLD_HISTORY,
    )


@pytest.mark.spec("PRI-8")
def test_migrate_runs_once(tinker_home):
    seed_old_knowledge()
    Principles(None).migrate()
    before = Home.store().items("principles")
    Home.store().put("memory", "any", {"lessons": [{"id": "late", "phase": "spec", "text": "late lesson"}]})
    Principles(None).migrate()
    assert Home.store().items("principles") == before
    assert Home.store().get("memory", "any")["lessons"] != []


@pytest.mark.spec("PRI-8")
def test_migrate_marks_a_home_with_no_old_knowledge(tinker_home):
    Principles(None).migrate()
    assert Principles(None).all() == []
    assert Home.store().get("migrated", "principles")


@pytest.mark.spec("PRI-8")
def test_an_item_that_cannot_be_read_keeps_its_text_as_do_with_a_note(tinker_home):
    Home.store().put("memory", "any", {"lessons": ["just a sentence", {"text": "no phase", "status": 7}]})
    Home.store().put(
        "learned", "uv", {"topic": "uv", "findings": [{"question": 9, "answer": "lost question"}]}
    )
    Principles(None).migrate()
    kept = migrated_by_do()
    assert set(kept) == {"just a sentence", "no phase", "lost question"}
    sentence = kept["just a sentence"]
    assert (sentence.scope, sentence.status) == ("any", "candidate")
    assert sentence.history[-1]["note"]
    assert kept["lost question"].history[-1]["note"]
    assert (kept["no phase"].status, kept["no phase"].phase, kept["no phase"].history) == (
        "candidate",
        "any",
        [],
    )
