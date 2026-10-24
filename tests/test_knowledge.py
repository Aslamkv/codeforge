import json

import pytest

from tinkerkit.knowledge import Embeddings, Knowledge, Learner, Library, Researcher, Retriever, language_key, record
from tinkerkit.model import Ollama
from tinkerkit.settings import MAX_LESSONS_PER_FILE, MAX_RULE_CHARS, PLAYBOOK_RULES, Config, Home
from tinkerkit.workspace import Toolchain


def learned(slug, **entry):
    Library.save(slug, {"topic": slug, "name": slug.title(), "summary": "s", "facts": [], **entry})


@pytest.mark.spec("KNW-1")
@pytest.mark.parametrize("topic,slug", [("Test-driven development", "test-driven-development"), ("C++", "cpp"),
                                        ("C#", "csharp"), ("Python 3.14", "python-3-14"), ("  Flask!  ", "flask")])
def test_slug(topic, slug):
    assert Library.slug(topic) == slug


@pytest.mark.spec("KNW-1")
def test_library_read_topics_and_language(tinker_home):
    assert len(Library.slug("x" * 100)) == 60
    learned("flask")
    learned("python", kind="language")
    Home.store().put("learned", "broken", {"facts": "not a list"})
    Home.store().put("learned", "junk", "not an entry")
    assert Library.read("missing") is None and Library.read("broken") is None and Library.read("") is None
    assert sorted(Library.topics()) == ["flask", "python"]
    assert Library.language("Python") == "python" and Library.language("Flask") is None
    assert Library.language("") is None


@pytest.mark.spec("KNW-2")
def test_a_learned_language_is_found_for_a_versioned_name(tinker_home):
    learned("python", kind="language", language="Python")
    assert Library.language("Python 3.12") == Library.language("python") == "python"
    assert Knowledge("Python 3.12").key == Knowledge("Python").key == "python"


@pytest.mark.spec("KNW-3")
def test_items(capsys):
    entry = {"topic": "py", "name": "Python 3.14", "summary": "A language.", "kind": "language",
             "tools": [{"role": "tests", "name": "pytest"}, {"role": "format", "name": "black"}],
             "machine": [{"command": "python3 --version", "found": True, "output": "Python 3.14.0"},
                         {"command": "black --version", "found": False, "output": ""}],
             "questions": [{"question": "What is new?"}],
             "findings": [{"question": 1, "answer": "Free threading.", "confidence": "high"}],
             "facts": [{"phase": "code", "text": "match is a soft keyword"},
                       {"phase": "spec", "text": "a claim", "confidence": "corroborated"}]}
    items = Library.items({"py": entry, "bad": {"facts": [{"phase": "code"}], "name": "Bad"}})
    assert [(item["phase"], item["text"]) for item in items] == [
        ("summary", "A language."), ("tools", "its tools: tests pytest, format black"),
        ("machine", "on this machine: Python 3.14.0; `black --version` fails (not installed)"),
        ("findings", "What is new? Free threading. (high confidence)"),
        ("code", "match is a soft keyword"), ("spec", "a claim (corroborated)")]
    assert all(item["topic"] == "py" and item["name"] == "Python 3.14" for item in items)
    assert "What was learned about bad is not readable" in capsys.readouterr().out
    assert Library.searched(items[0]) == "Python 3.14: A language."


@pytest.mark.spec("KNW-3")
def test_shown_in():
    shown = lambda phase, phases: Library.shown_in({"phase": phase}, phases)
    assert shown("machine", ("toolchain",)) and shown("machine", ("config",)) and not shown("machine", ("tests",))
    assert shown("tools", ("tests",)) and not shown("tools", ("code",))
    assert shown("findings", ("spec",)) and shown("findings", ("design",)) and not shown("findings", ("code",))
    assert shown("code", ("code", "tests")) and not shown("code", ("spec",))


@pytest.mark.spec("KNW-4")
@pytest.mark.parametrize("language,slug", [("Python 3.12", "python"), ("JavaScript (Node.js)", "javascript"),
                                           ("C++", "cpp"), ("C#", "csharp"), ("", "unknown"), ("Rust", "rust")])
def test_language_key(language, slug):
    assert language_key(language) == slug


@pytest.mark.spec("KNW-4")
def test_a_new_language_starts_empty(tinker_home):
    known = Knowledge(None)
    assert known.language == "Python" and known.key == "python"
    assert known.known_lessons() == [] and known.toolchain() is None
    assert known.summary() == "Python: nothing learned yet"


def lesson(text, phase="code", scope="any"):
    return {"text": text, "phase": phase, "scope": scope}


@pytest.mark.spec("KNW-5")
def test_add_lessons(tinker_home):
    known = Knowledge("Python")
    added = known.add_lessons([lesson("Use tmp_path for files a test makes", "tests", "language"),
                               lesson("Keep each module under sixty lines", "design"),
                               lesson("", "code"), lesson("text", "nowhere")], [])
    assert added == 2
    assert [lesson["text"]
            for lesson in Home.store().get("languages", "python")["lessons"]] == ["Use tmp_path for files a test makes"]
    assert [lesson["text"]
            for lesson in Home.store().get("memory", "any")["lessons"]] == ["Keep each module under sixty lines"]
    assert all(lesson["status"] == "candidate" and len(lesson["id"]) == 12 for lesson in known.known_lessons())
    again = Knowledge("Python")
    assert again.add_lessons([lesson("Use tmp_path for the files a test makes", "tests")], [2]) == 0
    seen = {lesson["text"]: lesson["seen"] for lesson in again.known_lessons()}
    assert seen == {"Use tmp_path for files a test makes": 2, "Keep each module under sixty lines": 2}
    assert "2 lesson(s)" in again.summary()


@pytest.mark.spec("KNW-5")
def test_lessons_are_capped_and_ranked(tinker_home):
    known = Knowledge("Python")
    distinct = ["Give pytest a pythonpath setting in pyproject.toml", "Run cargo test with -q for short output",
                "Pass sample JSON to adapters instead of calling the API", "Name test files test_*.py",
                "Keep secrets out of the repository", "Close sqlite connections in a finally block",
                "Round money to two decimals only when printing it", "Exit with code 1 after an error message",
                "Read settings from the environment first", "Prefer pathlib over os.path joins",
                "Quote shell variables that hold paths", "Sort dictionary keys before comparing output",
                "Seed random generators inside tests", "Use tmp_path for files the tests create",
                "Flush stdout before the process exits"]
    for text in distinct[:MAX_LESSONS_PER_FILE]:
        known.add_lessons([lesson(text)], [])
    assert len(known.known_lessons()) == MAX_LESSONS_PER_FILE
    known.confirm(known.known_lessons()[3])
    assert known.add_lessons([lesson("a brand new rule that is unlike every other one")], []) == 1
    texts = [lesson["text"] for lesson in known.any_language_doc["lessons"]]
    assert len(texts) == MAX_LESSONS_PER_FILE and "a brand new rule that is unlike every other one" in texts
    assert [lesson["text"] for lesson in known.lessons(("code",), limit=1)] == [known.known_lessons()[0]["text"]]
    assert known.known_lessons()[0]["seen"] == 2
    assert known.lessons(("design",)) == []


@pytest.mark.spec("KNW-6")
def test_toolchain_fixes(tinker_home):
    known = Knowledge("Python")
    fix = {"failure": "no module named pytest", "cause": "pytest missing", "replay": {"toolchain": {"setup": ["x"]}}}
    known.remember_fix(fix)
    assert known.known_fix("no module named pytest")["cause"] == "pytest missing"
    assert known.known_fix("no module named pytest", tried={"no module named pytest"}) is None
    assert known.known_fix("other") is None
    stored = known.known_fix("no module named pytest")
    known.fix_result(stored, worked=True)
    assert known.known_fix("no module named pytest")["used"] == 2
    known.fix_result(stored, worked=False)
    gone_at_once = known.known_fix("no module named pytest") is None
    assert gone_at_once
    assert Knowledge("Python").language_doc["fixes"] == []
    known.remember_fix(fix)
    assert known.similar_fix("no module named pytests")["cause"] == "pytest missing"
    assert known.similar_fix("completely different failure") is None
    for number in range(25):
        known.remember_fix({"failure": f"failure {number}", "cause": "c", "replay": None})
    assert len(known.language_doc["fixes"]) == 20
    known.remember_fix({"failure": "failure 24", "cause": "new", "replay": None})
    assert [fix["cause"] for fix in known.language_doc["fixes"] if fix["failure"] == "failure 24"] == ["new"]


@pytest.mark.spec("KNW-6")
def test_remember_toolchain_without_its_project_commands(tinker_home):
    known = Knowledge("Python")
    toolchain = Toolchain({"setup": ["python3 -m venv .venv"], "run": "python3 app.py", "check": "python3 -c 'import app'",
                           "test_all": "pytest -q", "test_one": "pytest -q {file}::{name}", "lint": "ruff check ."})
    known.remember_toolchain(toolchain)
    kept = Knowledge("Python").toolchain()
    assert kept.run == "" and kept.check == "" and kept.test_all == "pytest -q" and kept.setup == toolchain.setup
    known.finished_project()
    assert Home.store().get("languages", "python")["projects"] == 1
    assert "the toolchain that worked (1 finished project(s))" in Knowledge("Python").summary()


class EmbedServer:
    def __init__(self, broken=False):
        self.requests, self.broken = [], broken

    def __call__(self, path, payload=None, timeout=10, errors=False):
        self.requests.append(payload["input"])
        if self.broken:
            return {"error": "not an embedding model"}
        return {"embeddings": [[float(len(text)), float(text.count("a")) + 0.5] for text in payload["input"]]}


@pytest.mark.spec("KNW-7")
def test_embeddings_are_cached_per_model(tinker_home, monkeypatch):
    server = EmbedServer()
    monkeypatch.setattr(Ollama, "get_json", staticmethod(server))
    monkeypatch.setattr(Embeddings, "TEXTS_PER_REQUEST", 2)
    embeddings = Embeddings("embed-a")
    assert embeddings.vectors(["aa", "b", "ccc"]) == [[2.0, 2.5], [1.0, 0.5], [3.0, 0.5]]
    assert server.requests == [["aa", "b"], ["ccc"]]
    assert Embeddings("embed-a").vectors(["b", "dddd"])[0] == [1.0, 0.5]
    assert server.requests[-1] == ["dddd"]
    Embeddings("embed-b").vectors(["b"])
    embedded_again = ["b"]
    assert server.requests[-1] == embedded_again
    assert list(Home.store().get("embeddings", "embed-b")) == [Embeddings.key("b")]
    each_model_keeps_its_own = 4
    assert len(Home.store().get("embeddings", "embed-a")) == each_model_keeps_its_own


@pytest.mark.spec("KNW-7")
def test_embeddings_keep_prune_and_failure(tinker_home, monkeypatch):
    monkeypatch.setattr(Ollama, "get_json", staticmethod(EmbedServer()))
    embeddings = Embeddings("e")
    embeddings.vectors(["one", "two"])
    embeddings.vectors(["two"], prune=True)
    assert list(Home.store().get("embeddings", "e")) == [Embeddings.key("two")]
    embeddings.vectors(["three"], keep=False)
    assert Embeddings.key("three") not in Home.store().get("embeddings", "e")
    monkeypatch.setattr(Ollama, "get_json", staticmethod(EmbedServer(broken=True)))
    broken = Embeddings("other")
    assert broken.vectors(["x"]) is None and broken.failed == "other gave no embeddings (not an embedding model)"
    assert Embeddings.cosine([1, 0], [1, 0]) == 1.0 and Embeddings.cosine([1, 0], [0, 1]) == 0.0
    assert Embeddings.cosine([0, 0], [1, 1]) == 0.0


@pytest.mark.spec("KNW-7")
def test_embeddings_required_check_and_configured(monkeypatch):
    assert Embeddings.configured() is None
    with pytest.raises(SystemExit, match="needs an embedding model"):
        Embeddings.required("--web")
    Config.save(embed_model=5)
    with pytest.raises(SystemExit, match="not a model name"):
        Embeddings.check()
    Config.save(**{"embed_model": "nomic"})
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": "nomic"}]))
    assert Embeddings.required("--web").name == "nomic" and Embeddings.configured().name == "nomic"


@pytest.mark.spec("KNW-8")
def test_by_words_and_rank():
    texts = ["Flask routes and templates", "Django models", "Flask Flask Flask quickstart", "nothing here"]
    retriever = Retriever(None)
    assert retriever.by_words("flask quickstart", texts) == [2, 0]
    assert retriever.rank("flask quickstart", texts) == [2, 0, 1, 3]
    assert retriever.rank("q", []) == [] and retriever.used == "words"


@pytest.mark.spec("KNW-8")
def test_rank_fuses_meaning(monkeypatch, capsys):
    class Meaning:
        name, failed = "e", ""

        def vectors(self, texts, keep=True, prune=False):
            table = {"web framework": [1, 0], "Flask is a small tool": [0.9, 0.1], "Django ORM": [0, 1], "pandas": [0.1, 1]}
            return [table[text] for text in texts]
    retriever = Retriever(Meaning())
    assert retriever.rank("web framework", ["Django ORM", "Flask is a small tool", "pandas"]) == [1, 2, 0]
    assert retriever.used == "words and meaning (e)"

    class Broken(Meaning):
        failed = "it broke"

        def vectors(self, texts, keep=True, prune=False):
            return None
    retriever = Retriever(Broken())
    assert retriever.rank("x", ["a", "b"]) == [0, 1]
    assert retriever.embeddings is None and "Meaning search not used: it broke" in capsys.readouterr().out


@pytest.mark.spec("KNW-9")
def test_safe_commands():
    kept, dropped = Learner.safe_commands(["python3 --version", "  go   version ", "", "npm install x",
                                           "cargo --version; rm x", "echo $HOME", "sudo ls"])
    assert kept == ["python3 --version", "go version"]
    assert dropped == ["npm install x", "cargo --version; rm x", "echo $HOME", "sudo ls"]


@pytest.mark.spec("KNW-10")
def test_read_facts_and_add_facts():
    from tinkerkit.prompts import Reply
    facts = Learner.read_facts(Reply(json.dumps({"facts": [
        {"phase": "code", "text": "x" * 500}, {"phase": "nowhere", "text": "dropped"}, "junk",
        {"phase": "TESTS", "text": "pytest -q"}, {"phase": "code", "text": "c"}, {"phase": "code", "text": "d"},
        {"phase": "code", "text": "e"}]})))
    assert [fact["phase"] for fact in facts] == ["code", "tests", "code", "code"] and len(facts[0]["text"]) == 400
    kept = Learner.add_facts([], [{"text": "Flask 3.1 needs Python 3.9", "source": "https://a"}])
    Learner.add_facts(kept, [{"text": "Flask 3.1 needs Python 3.9.", "source": "https://b"},
                             {"text": "Flask 3.1 needs Python 3.9", "source": "https://a"},
                             {"text": "Something else entirely", "source": "https://c"}])
    assert len(kept) == 2 and kept[0]["also"] == ["https://b"]
    assert Learner.pages_of(kept[0]) == ["https://a", "https://b"]


@pytest.mark.spec("KNW-10")
def test_relevant_chunks():
    paragraphs = ["intro about the page " * 10] + [f"filler {number} " * 25
                                                   for number in range(6)] + ["install with pip " * 10]
    chunks = Learner.relevant_chunks("\n\n".join(paragraphs), 250, "pip install")
    assert len(chunks) == 2 and chunks[0].startswith("intro") and chunks[1].startswith("install with pip")
    assert Learner.relevant_chunks("short page", 250, "x") == ["short page"]
    assert Learner.UNRELEASED_RE.search("Flask 4.0 is in development") and not Learner.UNRELEASED_RE.search("Flask 3.1 adds")


class FakeInternet:
    def __init__(self, pages=None, up=True):
        self.pages, self.up = pages or {}, up

    def available(self):
        return self.up

    def problem(self, key=None):
        return "offline" if not self.up else "no answer"

    def search(self, query, count):
        if not self.up:
            return None
        return [{"title": url, "url": url, "content": ""} for url in self.pages][:count]

    def fetch(self, url):
        return {"title": url, "url": url, "content": self.pages[url]} if self.up else None


@pytest.mark.spec("KNW-12")
def test_look_at_machine(make_model):
    found = Learner(make_model(), FakeInternet(), None).look_at_machine(["pwd", "echo tool version 2.4.1", "exit 3"])
    assert found[0]["found"] and "tinker-learn-" in found[0]["output"]
    assert found[1] == {"command": "echo tool version 2.4.1", "found": True, "version": "2.4.1",
                        "output": "tool version 2.4.1"}
    assert found[2]["found"] is False and found[2]["version"] == ""


@pytest.mark.spec("KNW-13")
def test_read_frame():
    frame = Researcher.read_frame({"name": "Food banks", "summary": "s", "questions": [
        {"question": "How is food recorded?", "queries": ["a", "b", "c"]}, {"question": "", "queries": ["x"]},
        {"question": "No searches?", "queries": []}, "junk"] + [{"question": f"Q{number}", "queries": ["q"]}
                                                                for number in range(9)]})
    assert frame["questions"][0] == {"question": "How is food recorded?", "queries": ["a", "b"]}
    assert len(frame["questions"]) == 4
    with pytest.raises(ValueError):
        Researcher.read_frame({"name": "X", "questions": []})


@pytest.mark.spec("KNW-13")
def test_read_analysis():
    analysis = Researcher.read_analysis({
        "findings": [{"question": 1, "answer": "Weighed at intake.", "confidence": "certain", "facts": [1, 9, "x"]},
                     {"question": 7, "answer": "unknown question"}, {"question": 1, "answer": ""}],
        "contradictions": [{"facts": [1, 2], "doubtful": [2, 5], "note": "n"}, {"facts": [1]}, {"facts": [1, 3]}],
        "gaps": [{"question": 1, "missing": "m", "search": "s1"}, {"question": 1, "search": "s2"},
                 {"question": 1, "search": "s3"}, {"question": 2, "search": ""}],
        "checks": [{"fact": 1, "search": "c"}, {"fact": 99, "search": "c"}, {"fact": 2, "search": "d"},
                   {"fact": 3, "search": "e"}]}, fact_count=3, question_count=2)
    assert analysis["findings"] == [{"question": 1, "answer": "Weighed at intake.", "confidence": "low", "facts": [1]}]
    assert analysis["contradictions"] == [{"facts": [1, 2], "doubtful": [2], "note": "n"},
                                          {"facts": [1, 3], "doubtful": [1, 3], "note": ""}]
    assert [gap["search"] for gap in analysis["gaps"]] == ["s1", "s2"] and [check["fact"]
                                                                            for check in analysis["checks"]] == [1, 2]
    with pytest.raises(ValueError, match="finding"):
        Researcher.read_analysis({"findings": []}, 3, 2)


class FakeLearner:
    def __init__(self, vectors):
        self.embeddings = type("E", (), {"vectors": lambda self, texts, keep=True: vectors})()


def fact(text, source, section="1"):
    return {"text": text, "source": source, "section": section}


@pytest.mark.spec("KNW-14")
def test_cross_check_supports_agreeing_facts_and_pairs_disagreeing_ones():
    facts = [fact("Food is weighed in 10 kg crates", "https://a.org/1"),
             fact("Crates hold 10 kg of food", "https://www.b.org/x"),
             fact("Crates hold 12 kg of food", "https://c.org/y"),
             fact("Volunteers sign in", "https://a.org/2")]
    same_claim_but_no_repeat = [[1, 0], [0.85, 0.53], [0.82, 0.57], [0, 1]]
    differ = Researcher(type("L", (), {"model": None, **FakeLearner(same_claim_but_no_repeat).__dict__})()).cross_check(facts)
    assert facts[0]["sites"] == ["a.org", "b.org"] and facts[1]["sites"] == ["a.org", "b.org"]
    assert facts[3]["sites"] == ["a.org"] and (1, 3) in differ and (2, 3) in differ


@pytest.mark.spec("KNW-14")
def test_fold_repeats_keeps_the_more_detailed_text():
    facts = [fact("Open on Mondays", "https://a.org"), fact("Open on Mondays from 9 to 5", "https://b.org")]
    vectors = [[1, 0], [1, 0.01]]
    researcher = Researcher(type("L", (), {"model": None, "embeddings": None})())
    researcher.fold_repeats(facts, vectors)
    assert len(facts) == 1 and facts[0]["text"] == "Open on Mondays from 9 to 5" and facts[0]["also"] == ["https://b.org"]
    assert Researcher.agree({"text": "5 kg"}, {"text": "5 kg at 9am"}) and not Researcher.agree({"text": "5"}, {"text": "6"})
    assert Researcher.site("https://www.Example.org/x") == "example.org"


@pytest.mark.spec("KNW-14")
def test_supported_and_entry():
    corroborated, single, disputed = ({"confidence": confidence}
                                      for confidence in ("corroborated", "single", "disputed"))
    assert Researcher.supported("high", [corroborated, single]) == "high"
    assert Researcher.supported("high", [corroborated, single, single]) == "medium"
    assert Researcher.supported("high", [corroborated, disputed]) == "medium"
    assert Researcher.supported("high", []) == "low" and Researcher.supported("low", [corroborated]) == "low"
    learner = type("L", (), {"model": type("M", (), {"name": "m"})(), "notes": []})()
    facts = [dict(fact("a", "https://a"), sites=["a", "b"]), dict(fact("b", "https://b", "2"), sites=["b"]),
             dict(fact("c", "https://c"), sites=["c"])]
    analysis = {"findings": [{"question": 1, "answer": "x", "confidence": "high", "facts": [1, 3]}],
                "contradictions": [{"facts": [2, 3], "doubtful": [3], "note": ""}], "gaps": [], "checks": []}
    frame = {"name": "T", "summary": "s", "questions": [{"question": "q1"}, {"question": "q2"}]}
    entry = Researcher(learner).entry("t", frame, facts, ["https://a"], analysis)
    assert [(fact["confidence"], fact["question"]) for fact in entry["facts"]] == \
        [("corroborated", 1), ("single", 2), ("disputed", 1)]
    assert entry["findings"][0]["confidence"] == "medium" and entry["kind"] == "research"
    assert "section" not in entry["facts"][0]


@pytest.mark.spec("KNW-14")
def test_next_searches_and_fact_listing():
    frame = {"questions": [{"question": "q1"}, {"question": "q2"}]}
    facts = [fact("a", "u", "2"), fact("b", "u", "1")]
    analysis = {"gaps": [{"question": 2, "search": "find gap"}], "checks": [{"fact": 1, "search": "check a"}]}
    searches = Researcher.next_searches(frame, facts, analysis)
    assert searches == [{"query": "find gap", "section": "2", "pages": 1, "focus": "q2"},
                        {"query": "check a", "section": "2", "pages": 2,
                         "focus": "whether this is so, and the exact figures: a"}]
    listing = Researcher.fact_listing([dict(facts[0], sites=["x"]), dict(facts[1], sites=["x", "y"])], 1000)
    assert listing == "F1 [Q2, 1 site(s)] a\nF2 [Q1, 2 site(s)] b"
    assert Researcher.fact_listing([dict(facts[0], sites=["x"]), dict(facts[1], sites=["x", "y"])], 25) == \
        "F2 [Q1, 2 site(s)] b"


FRAME = {"name": "Food bank inventory", "summary": "How food banks track donations.",
         "questions": [{"question": "How is donated food recorded?", "queries": ["food bank intake records"]}]}


def analysis(gaps=()):
    return json.dumps({"findings": [{"question": 1, "answer": "It is weighed at intake.", "confidence": "high", "facts": [1]}],
                       "contradictions": [], "gaps": list(gaps), "checks": []})


@pytest.mark.spec("KNW-15")
def test_research_a_domain(make_model, fake_ollama, tinker_home):
    pages = {"https://a.org/intake": "Donations are weighed at intake.", "https://www.b.org/guide": "We weigh all donations."}
    fake_ollama.reply(json.dumps(FRAME), when="Frame the research")
    fake_ollama.reply(json.dumps({"facts": [{"phase": "spec", "text": "Donated food is weighed at intake"}]}),
                      when="Facts", times=4)
    fake_ollama.reply(analysis(gaps=[{"question": 1, "missing": "units", "search": "food bank weight units"}]),
                      when="Analyse the findings")
    fake_ollama.reply(analysis(), when="Analyse the findings")
    entry = Researcher(Learner(make_model(), FakeInternet(pages), None)).research("food bank inventory")
    assert entry["kind"] == "research" and entry["name"] == "Food bank inventory"
    assert entry["questions"] == FRAME["questions"] and entry["open"] == []
    assert entry["facts"][0]["confidence"] == "corroborated" and entry["facts"][0]["sites"] == ["a.org", "b.org"]
    assert entry["findings"][0]["confidence"] == "high" and sorted(entry["sources"]) == sorted(pages)
    assert [label.split(" (")[0].split(",")[0] for label in fake_ollama.labels()].count("Analyse the findings") == 2
    assert Library.read("food-bank-inventory")["kind"] == "research"


SOURCES = [{"id": "fact1", "text": "a"}, {"id": "fact2", "text": "b"}, {"id": "lesson1", "text": "c"}]


@pytest.mark.spec("KNW-22")
def test_read_rules_keeps_only_rules_that_rest_on_what_was_learned():
    rules = Learner.read_rules({"rules": [
        {"phase": "code", "text": "Use  match\n for dispatch", "cites": [1, 3, 1, 9, "2"]},
        {"phase": "code", "text": "Trust me", "cites": []},
        {"phase": "dreams", "text": "Unknown phase", "cites": [1]},
        {"phase": "code", "text": "Use match for dispatch", "cites": [2]},
        {"phase": "tests", "text": "x" * 500, "cites": [2]}, "junk"]}, SOURCES)
    assert [(rule["phase"], rule["text"][:22], rule["cites"]) for rule in rules] == [
        ("code", "Use match for dispatch", ["fact1", "lesson1"]), ("tests", "x" * 22, ["fact2"])]
    assert len(rules[1]["text"]) == MAX_RULE_CHARS and rules[0]["id"]
    many = Learner.read_rules({"rules": [{"phase": "code", "text": f"rule {number}", "cites": [1]}
                                         for number in range(40)]},
                              SOURCES)
    assert len(many) == PLAYBOOK_RULES
    with pytest.raises(ValueError, match="cites"):
        Learner.read_rules({"rules": [{"phase": "code", "text": "Trust me"}]}, SOURCES)


@pytest.mark.spec("KNW-22")
def test_a_rule_says_when_it_applies_and_what_a_new_project_declares_comes_first():
    rules = Learner.read_rules({"rules": [{"phase": "code", "text": "Await in async code", "when": "async code " * 20,
                                           "cites": [1]}],
                                "declare": {"text": "Set edition 2024 in Cargo.toml", "cites": [2]}}, SOURCES)
    assert [(rule["phase"], rule["text"], rule["when"])
            for rule in rules][0] == ("config", "Set edition 2024 in Cargo.toml", "")
    assert rules[1]["when"].startswith("async code") and len(rules[1]["when"]) <= 80
    assert Learner.read_rules({"rules": [{"phase": "code", "text": "x", "cites": [1]}], "declare": None},
                              SOURCES)[0]["when"] == ""


@pytest.mark.spec("KNW-22")
def test_condensing_folds_in_the_lessons_the_language_proved(make_model, fake_ollama, tinker_home):
    known = Knowledge("Go")
    known.add_lessons([dict(lesson("Name the module after its folder", "config"), scope="language"),
                       dict(lesson("Read the spec twice", "design"), scope="language")], [])
    known.prove({f"lesson:{known.language_doc['lessons'][0]['id']}": ["config"]})
    fake_ollama.reply(json.dumps({"rules": [{"phase": "config", "text": "Name the module after its folder",
                                             "cites": [2]}]}), when="Condense")
    plan = {"kind": "language", "language": "Go", "name": "Go 1.25", "tools": [{"role": "tests", "name": "go test"}]}
    facts = [{"phase": "code", "text": "Go 1.25 has generic type aliases", "slot": "this-version"}]
    rules = Learner(make_model(), FakeInternet(), None).condense(plan, facts)
    prompt = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "1. [this-version, code] Go 1.25 has generic type aliases" in prompt and "2. Name the module after its folder" in prompt
    a_candidate_lesson = "Read the spec twice"
    assert a_candidate_lesson not in prompt
    assert rules[0]["cites"] == [known.language_doc["lessons"][0]["id"]]


@pytest.mark.spec("KNW-22")
def test_a_playbook_the_model_cannot_give_leaves_the_facts_as_they_are(make_model, fake_ollama, tinker_home):
    fake_ollama.reply(json.dumps({"rules": []}), when="Condense", times=2)
    learner = Learner(make_model(), FakeInternet(), None)
    assert learner.condense({"language": "Go", "name": "Go", "tools": []}, [{"phase": "code", "text": "a"}]) == []


def language_with_rules():
    Library.save("go", {"topic": "go", "kind": "language", "language": "Go", "name": "Go 1.25", "summary": "s",
                        "facts": [{"phase": "code", "text": "Aliases may be generic"},
                                  {"phase": "tests", "text": "go test -run picks tests"},
                                  {"phase": "code", "text": "Go 1.25 has a new GC"}]})
    entry = Library.read("go")
    aliases, run, _ = (fact["id"] for fact in entry["facts"])
    entry["rules"] = [{"phase": "code", "text": "Use generic type aliases", "cites": [aliases]},
                      {"phase": "tests", "text": "Run one test with go test -run", "cites": [run, aliases]}]
    Library.save("go", entry)
    return [rule["id"] for rule in Library.read("go")["rules"]]


@pytest.mark.spec("KNW-22")
def test_rules_are_shown_in_place_of_the_facts_they_cite(tinker_home):
    aliases, _ = language_with_rules()
    items = Library.items({"go": Library.read("go")})
    assert [(item["phase"], item["text"], item.get("kind")) for item in items[1:]] == [
        ("code", "Use generic type aliases", "rule"), ("tests", "Run one test with go test -run", "rule")]
    assert items[1]["id"] == aliases and items[1]["status"] == "candidate"
    plain = {key: value for key, value in Library.read("go").items() if key != "rules"}
    every_fact_without_a_playbook = 4
    assert len(Library.items({"go": plain})) == every_fact_without_a_playbook


@pytest.mark.spec("KNW-22")
def test_a_rule_is_proven_and_retired_like_a_fact(tinker_home):
    aliases, run = language_with_rules()
    Knowledge("Go").prove({f"fact:go:{aliases}": ["code"]})
    assert Library.read("go")["rules"][0]["status"] == "proven"
    assert [fact["text"] for fact in Knowledge("Go").facts(("code",))] == ["Use generic type aliases"]
    assert Knowledge("Go").disprove({f"fact:go:{run}": ["tests"]}, ("tests",)) == 1
    entry = Library.read("go")
    assert [rule["text"]
            for rule in entry["rules"]] == ["Use generic type aliases"] and entry["retired"][0]["was"] == "rules"
    shown = [item["text"] for item in Library.items({"go": entry})]
    cited_only_by_the_retired_rule = "go test -run picks tests"
    assert cited_only_by_the_retired_rule in shown
    assert "Aliases may be generic" not in shown and "Go 1.25 has a new GC" not in shown


@pytest.mark.spec("KNW-5")
def test_a_lesson_that_names_the_project_is_not_kept(tinker_home):
    known = Knowledge("Python")
    added = known.add_lessons([lesson("Keep calc.py's parser apart from its arithmetic"),
                               lesson("In calc-py, store results in history.json"),
                               lesson("Keep a parser apart from the arithmetic it calls")], [],
                              names=["calc.py", "history.json", "calc-py"])
    assert added == 1 and [lesson["text"]
                           for lesson in known.known_lessons()] == ["Keep a parser apart from the arithmetic it calls"]


def topic_with_facts():
    Library.save("flask", {"topic": "flask", "name": "Flask", "summary": "s", "facts": [
        {"phase": "code", "text": "Flask routes use @app.route"}, {"phase": "tests", "text": "Use app.test_client()"}]})
    return [fact["id"] for fact in Library.read("flask")["facts"]]


@pytest.mark.spec("KNW-17")
def test_knowledge_starts_as_a_candidate_and_a_success_proves_it(tinker_home):
    route, client = topic_with_facts()
    assert all(fact["status"] == "candidate" for fact in Library.read("flask")["facts"])
    known = Knowledge("Python")
    known.add_lessons([lesson("Seed random generators inside tests", "tests")], [])
    lesson_id = known.known_lessons()[0]["id"]
    used = {f"lesson:{lesson_id}": ["tests"], f"fact:flask:{route}": ["code"]}
    assert known.prove(used) == 2
    again = Knowledge("Python")
    assert again.known_lessons()[0]["status"] == "proven"
    statuses = {fact["id"]: fact["status"] for fact in Library.read("flask")["facts"]}
    assert statuses == {route: "proven", client: "candidate"}
    facts = [{key: value for key, value in fact.items() if key != "history"} for fact in again.language_doc["facts"]]
    assert facts == [{"id": route, "phase": "code", "text": "Flask routes use @app.route", "topic": "flask",
                      "status": "proven"}]
    proven_again = again.prove(used)
    assert proven_again == 0


@pytest.mark.spec("KNW-17")
def test_a_failure_retires_what_was_used_in_that_step(tinker_home):
    route, client = topic_with_facts()
    known = Knowledge("Python")
    known.add_lessons([lesson("Seed random generators inside tests", "tests"),
                       lesson("Prefer pathlib over os.path joins", "code")], [])
    seed, paths = (lesson["id"] for lesson in known.known_lessons())
    known.prove({f"fact:flask:{route}": ["code"], f"lesson:{paths}": ["code"]})
    used = {f"lesson:{seed}": ["tests"], f"lesson:{paths}": ["code"], f"known:{route}": ["code"],
            f"fact:flask:{client}": ["tests"]}
    none_shown_there = ("toolchain", "config")
    assert known.disprove(used, none_shown_there) == 0
    gone = Knowledge("Python").disprove(used, ("code",))
    after = Knowledge("Python")
    assert gone == 2 and [lesson["text"] for lesson in after.known_lessons()] == ["Seed random generators inside tests"]
    assert after.language_doc["facts"] == []
    assert after.disprove(used, ("tests",)) == 2
    assert Knowledge("Python").known_lessons() == [] and [fact["id"]
                                                          for fact in Library.read("flask")["facts"]] == [route]


@pytest.mark.spec("KNW-18")
def test_proven_facts_are_shown_for_their_phase(tinker_home):
    route, client = topic_with_facts()
    known = Knowledge("Python 3.13")
    known.prove({f"fact:flask:{route}": ["code"]})
    assert [fact["text"] for fact in Knowledge("Python").facts(("code",))] == ["Flask routes use @app.route"]
    assert Knowledge("Python").facts(("tests",)) == []


def events(item):
    return [(event["event"], event["run"]) for event in item.get("history", [])]


@pytest.mark.spec("KNW-19")
def test_each_item_keeps_its_history_by_run(tinker_home):
    route, _ = topic_with_facts()
    Knowledge("Python", "r1").add_lessons([lesson("Seed random generators inside tests", "tests")], [])
    Knowledge("Python", "r2").add_lessons([lesson("Seed random generators inside the tests", "tests")], [])
    known = Knowledge("Python", "r3")
    seed = known.known_lessons()[0]
    known.prove({f"lesson:{seed['id']}": ["tests"], f"fact:flask:{route}": ["code"]})
    Knowledge("Python", "r4").prove({f"lesson:{seed['id']}": ["tests"], f"known:{route}": ["code"]})
    after = Knowledge("Python")
    seed = after.known_lessons()[0]
    assert events(seed) == [("born", "r1"), ("confirmed", "r2"), ("helped", "r3"), ("helped", "r4")]
    assert seed["helped"] == 2 and seed["status"] == "proven"
    assert events(Library.read("flask")["facts"][0]) == [("helped", "r3")]
    assert events(after.language_doc["facts"][0]) == [("born", "r3"), ("helped", "r4")]


@pytest.mark.spec("KNW-19")
def test_a_fix_keeps_its_history_and_history_is_capped(tinker_home, monkeypatch):
    Knowledge("Python", "r1").remember_fix({"failure": "no module named calc", "cause": "pythonpath",
                                            "replay": {"toolchain": {}}})
    again = Knowledge("Python", "r2")
    again.fix_result(again.known_fix("no module named calc"), worked=True)
    assert events(Knowledge("Python").known_fix("no module named calc")) == [("born", "r1"), ("helped", "r2")]
    monkeypatch.setattr("tinkerkit.knowledge.EVENTS_KEPT_PER_ITEM", 3)
    item = {}
    for run in ("r1", "r2", "r3", "r4"):
        record(item, "helped", run)
    assert [run for _, run in events(item)] == ["r2", "r3", "r4"] and item["helped"] == 4


@pytest.mark.spec("KNW-5", "KNW-19")
def test_lessons_rank_by_times_seen_and_helped(tinker_home):
    known = Knowledge("Python")
    known.add_lessons([lesson("Seed random generators inside tests", "tests"),
                       lesson("Name each test after the behaviour it checks", "tests")], [2])
    seed, name = known.lessons(("tests",))
    known.prove({f"lesson:{seed['id']}": ["tests"]})
    known.prove({f"lesson:{seed['id']}": ["tests"]})
    assert [lesson["text"]
            for lesson in Knowledge("Python").lessons(("tests",))][0] == "Seed random generators inside tests"


@pytest.mark.spec("KNW-20")
def test_what_is_retired_is_kept_apart_with_why_and_never_shown(tinker_home, monkeypatch):
    route, client = topic_with_facts()
    known = Knowledge("Python", "r1")
    known.add_lessons([lesson("Seed random generators inside tests", "tests")], [])
    seed = known.known_lessons()[0]["id"]
    known.remember_fix({"failure": "no module named calc", "cause": "pythonpath", "replay": {"toolchain": {}}})
    Knowledge("Python", "r2").disprove({f"lesson:{seed}": ["tests"], f"fact:flask:{client}": ["tests"]}, ("tests",))
    after = Knowledge("Python", "r3")
    after.fix_result(after.known_fix("no module named calc"), worked=False)
    retired = Knowledge("Python").any_language_doc["retired"][0]
    assert retired["text"] == "Seed random generators inside tests" and retired["status"] == "retired"
    assert retired["reason"] == "shown in the tests prompts of a step that failed" and retired["was"] == "lessons"
    assert events(retired)[-1] == ("failed", "r2")
    fix = Knowledge("Python").language_doc["retired"][0]
    assert fix["reason"] == "its replay failed" and events(fix)[-1] == ("failed", "r3")
    entry = Library.read("flask")
    assert [fact["id"] for fact in entry["facts"]] == [route] and entry["retired"][0]["id"] == client
    assert Knowledge("Python").lessons(("tests",)) == [] and Knowledge("Python").known_fix("no module named calc") is None
    assert all(item["id"] != client for item in Library.items({"flask": entry}))
    monkeypatch.setattr("tinkerkit.knowledge.RETIRED_KEPT_PER_LIST", 1)
    Knowledge("Python").disprove({f"fact:flask:{route}": ["code"]}, ("code",))
    assert [retired["id"] for retired in Library.read("flask")["retired"]] == [route]


@pytest.mark.spec("KNW-21")
def test_a_finding_cites_its_facts_by_id(tinker_home):
    Library.save("food", {"topic": "food", "name": "Food", "summary": "s", "questions": [{"question": "q?"}],
                          "facts": [{"phase": "spec", "text": "one"}, {"phase": "spec", "text": "two"}],
                          "findings": [{"question": 1, "answer": "a", "facts": [2, 9], "confidence": "low"}]})
    entry = Library.read("food")
    assert entry["findings"][0]["cites"] == [entry["facts"][1]["id"]]


@pytest.mark.spec("KNW-25")
def test_the_checks_a_reused_toolchain_lacks_and_the_tools_it_cannot_count_on(tinker_home):
    learned("python", kind="language", language="Python",
            tools=[{"role": "environment", "name": "uv"}, {"role": "lint", "name": "ruff"},
                   {"role": "types", "name": "mypy"}, {"role": "format", "name": "black"}],
            machine=[{"command": "uv --version", "found": False}, {"command": "ruff --version", "found": False},
                     {"command": "mypy --version", "found": False}])
    known = Knowledge("Python")
    every_tool_with_no_toolchain_yet = ["uv", "ruff", "mypy"]
    assert known.missing_checks() == [] and known.unavailable_tools() == every_tool_with_no_toolchain_yet
    known.remember_toolchain(Toolchain({"setup": ["python3 -m venv .venv", "pip install pytest ruff"],
                                        "test_all": "pytest", "test_one": "pytest {file}", "lint": "ruff check ."}))
    assert known.missing_checks() == ["types mypy", "format black"]
    neither_installed_nor_to_be_added = ["uv"]
    assert known.unavailable_tools() == neither_installed_nor_to_be_added
    assert "add the learned types mypy, format black it lacks" in known.toolchain_title("reuse it")
