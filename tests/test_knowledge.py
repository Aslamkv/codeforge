"""KNW: what Tinker knows beyond the model: lessons, toolchain fixes, and what it learns from the web."""

import json

import pytest

from tinkerkit.knowledge import Embeddings, Knowledge, Learner, Library, Researcher, Retriever
from tinkerkit.model import Ollama
from tinkerkit.settings import Config, JsonFile, MAX_LESSONS
from tinkerkit.workspace import Toolchain
from conftest import read_json


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
    JsonFile.write(Library.path("broken"), {"facts": "not a list"})
    (Library.path("junk")).write_text("{")
    assert Library.read("missing") is None and Library.read("broken") is None and Library.read("") is None
    assert sorted(Library.topics()) == ["flask", "python"]
    assert Library.language("Python") == "python" and Library.language("Flask") is None
    assert Library.language("") is None


@pytest.mark.spec("KNW-2")
@pytest.mark.xfail(strict=True, reason="ISSUE-4: Library.slug keeps the version, Knowledge.language_slug drops it")
def test_a_learned_language_is_found_for_a_versioned_name(tinker_home):
    learned("python", kind="language", language="Python")
    assert Library.language("Python 3.12") == "python"


@pytest.mark.spec("KNW-2")
def test_the_two_language_slugs_agree_without_a_version():
    assert Library.slug("Python") == Knowledge.language_slug("Python") == "python"


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
    assert [(i["phase"], i["text"]) for i in items] == [
        ("summary", "A language."), ("tools", "its tools: tests pytest, format black"),
        ("machine", "on this machine: Python 3.14.0; `black --version` fails (not installed)"),
        ("findings", "What is new? Free threading. (high confidence)"),
        ("code", "match is a soft keyword"), ("spec", "a claim (corroborated)")]
    assert all(i["topic"] == "py" and i["name"] == "Python 3.14" for i in items)
    assert "bad.json is not readable" in capsys.readouterr().out
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
def test_language_slug(language, slug):
    assert Knowledge.language_slug(language) == slug


@pytest.mark.spec("KNW-4")
def test_a_new_language_starts_empty(tinker_home):
    known = Knowledge(None)
    assert known.language == "Python" and known.path == tinker_home / "languages" / "python.json"
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
    assert [l["text"] for l in read_json(known.path)["lessons"]] == ["Use tmp_path for files a test makes"]
    assert [l["text"] for l in read_json(tinker_home / "memory.json")["lessons"]] == ["Keep each module under sixty lines"]
    again = Knowledge("Python")
    assert again.add_lessons([lesson("Use tmp_path for the files a test makes", "tests")], [2]) == 0
    seen = {l["text"]: l["seen"] for l in again.known_lessons()}
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
    for text in distinct[:MAX_LESSONS]:
        known.add_lessons([lesson(text)], [])
    assert len(known.known_lessons()) == MAX_LESSONS
    known.confirm(known.known_lessons()[3])
    assert known.add_lessons([lesson("a brand new rule that is unlike every other one")], []) == 1
    texts = [l["text"] for l in known.any_language_doc["lessons"]]
    assert len(texts) == MAX_LESSONS and "a brand new rule that is unlike every other one" in texts
    assert known.lessons(("code",), limit=1) == [known.known_lessons()[0]["text"]]
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
    known.fix_result(stored, worked=False)
    known.fix_result(stored, worked=False)
    assert known.known_fix("no module named pytest") is None  # failed more often than it helped
    assert known.similar_fix("no module named pytests")["cause"] == "pytest missing"
    assert known.similar_fix("completely different failure") is None
    for i in range(25):
        known.remember_fix({"failure": f"failure {i}", "cause": "c", "replay": None})
    assert len(known.language_doc["fixes"]) == 20
    known.remember_fix({"failure": "failure 24", "cause": "new", "replay": None})
    assert [f["cause"] for f in known.language_doc["fixes"] if f["failure"] == "failure 24"] == ["new"]


@pytest.mark.spec("KNW-6")
def test_remember_toolchain_without_its_project_commands(tinker_home):
    known = Knowledge("Python")
    toolchain = Toolchain({"setup": ["python3 -m venv .venv"], "run": "python3 app.py", "check": "python3 -c 'import app'",
                           "test_all": "pytest -q", "test_one": "pytest -q {file}::{name}", "lint": "ruff check ."})
    known.remember_toolchain(toolchain)
    kept = Knowledge("Python").toolchain()
    assert kept.run == "" and kept.check == "" and kept.test_all == "pytest -q" and kept.setup == toolchain.setup
    known.finished_project()
    assert read_json(known.path)["projects"] == 1
    assert "the toolchain that worked (1 finished project(s))" in Knowledge("Python").summary()


class EmbedServer:
    """Ollama's /api/embed: a vector per text (its length and its count of 'a'), counting the requests."""

    def __init__(self, broken=False):
        self.requests, self.broken = [], broken

    def __call__(self, path, payload=None, timeout=10, errors=False):
        self.requests.append(payload["input"])
        if self.broken:
            return {"error": "not an embedding model"}
        return {"embeddings": [[float(len(t)), float(t.count("a")) + 0.5] for t in payload["input"]]}


@pytest.mark.spec("KNW-7")
def test_embeddings_are_cached_per_model(tinker_home, monkeypatch):
    server = EmbedServer()
    monkeypatch.setattr(Ollama, "get_json", staticmethod(server))
    monkeypatch.setattr(Embeddings, "BATCH", 2)
    embeddings = Embeddings("embed-a")
    assert embeddings.vectors(["aa", "b", "ccc"]) == [[2.0, 2.5], [1.0, 0.5], [3.0, 0.5]]
    assert server.requests == [["aa", "b"], ["ccc"]]
    assert Embeddings("embed-a").vectors(["b", "dddd"])[0] == [1.0, 0.5]
    assert server.requests[-1] == ["dddd"]
    Embeddings("embed-b").vectors(["b"])
    assert server.requests[-1] == ["b"]  # another model: embedded again
    assert list(read_json(tinker_home / "embeddings.json")["vectors"]) == [Embeddings.key("b")]


@pytest.mark.spec("KNW-7")
def test_embeddings_keep_prune_and_failure(tinker_home, monkeypatch):
    monkeypatch.setattr(Ollama, "get_json", staticmethod(EmbedServer()))
    embeddings = Embeddings("e")
    embeddings.vectors(["one", "two"])
    embeddings.vectors(["two"], prune=True)
    assert list(read_json(tinker_home / "embeddings.json")["vectors"]) == [Embeddings.key("two")]
    embeddings.vectors(["three"], keep=False)
    assert Embeddings.key("three") not in read_json(tinker_home / "embeddings.json")["vectors"]
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
    Config.load()["embed_model"] = 5
    with pytest.raises(SystemExit, match="not a model name"):
        Embeddings.check()
    Config.load()["embed_model"] = "nomic"
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
            return [table[t] for t in texts]
    retriever = Retriever(Meaning())
    assert retriever.rank("web framework", ["Django ORM", "Flask is a small tool", "pandas"]) == [1, 2, 0]
    assert retriever.used == "words and meaning (e)"

    class Broken(Meaning):
        failed = "it broke"

        def vectors(self, texts, keep=True, prune=False):
            return None
    retriever = Retriever(Broken())
    assert retriever.rank("x", ["a", "b"]) == [0, 1]
    assert retriever.embeddings is None and "meaning search not used: it broke" in capsys.readouterr().out


@pytest.mark.spec("KNW-9")
def test_read_plan():
    plan = Learner.read_plan({"name": " Flask ", "summary": "A  web\nframework.", "kind": "tool",
                              "queries": ["a", " ", "b", "c", "d", "e"],
                              "commands": ["flask --version", "pip install flask", "ls | wc", "rm -rf /"]})
    assert plan["name"] == "Flask" and plan["summary"] == "A web framework." and plan["queries"] == ["a", "b", "c", "d"]
    assert plan["commands"] == ["flask --version"] and len(plan["dropped"]) == 3
    assert Learner.read_plan({"name": "Bookkeeping", "kind": "domain", "queries": ["x"]})["domain"]
    assert Learner.read_plan({"name": "Go", "language": "Go", "queries": []})["language"] == "Go"
    for bad in ({"name": "", "queries": ["x"]}, {"name": "X", "queries": []}, ["not", "an", "object"]):
        with pytest.raises(ValueError):
            Learner.read_plan(bad)


@pytest.mark.spec("KNW-9")
def test_safe_commands():
    kept, dropped = Learner.safe_commands(["python3 --version", "  go   version ", "", "npm install x",
                                           "cargo --version; rm x", "echo $HOME", "sudo ls"])
    assert kept == ["python3 --version", "go version"]
    assert dropped == ["npm install x", "cargo --version; rm x", "echo $HOME", "sudo ls"]


@pytest.mark.spec("KNW-9")
def test_read_language_plan():
    plan = Learner.read_language_plan({
        "name": "Go", "version": "1.25", "summary": "Compiled.",
        "tools": [{"role": "tests", "name": "go test", "search": "go test flags", "command": "go version"},
                  {"role": "tests", "name": "ginkgo"}, {"role": "magic", "name": "x"}, "junk",
                  {"role": "lint", "name": "golangci-lint", "command": "golangci-lint --version"}],
        "searches": [{"section": "fundamentals", "query": "Go spec"}, {"section": "gossip", "query": "x"},
                     {"section": "new", "query": " "}],
        "commands": ["go version", "go env"]})
    assert plan["name"] == "Go 1.25" and plan["language"] == "Go" and plan["kind"] == "language"
    assert [t["name"] for t in plan["tools"]] == ["go test", "golangci-lint"]
    assert plan["searches"] == [{"section": "fundamentals", "query": "Go spec"}]
    assert plan["commands"] == ["go version", "go env", "golangci-lint --version"]
    assert Learner.read_language_plan({"name": "", "summary": "a band"}) == {"not_language": "a band"}
    with pytest.raises(ValueError, match="searches"):
        Learner.read_language_plan({"name": "Go", "searches": []})


@pytest.mark.spec("KNW-10")
def test_read_facts_and_add_facts():
    from tinkerkit.prompts import Reply
    facts = Learner.read_facts(Reply(json.dumps({"facts": [
        {"phase": "code", "text": "x" * 500}, {"phase": "nowhere", "text": "dropped"}, "junk",
        {"phase": "TESTS", "text": "pytest -q"}, {"phase": "code", "text": "c"}, {"phase": "code", "text": "d"},
        {"phase": "code", "text": "e"}]})))
    assert [f["phase"] for f in facts] == ["code", "tests", "code", "code"] and len(facts[0]["text"]) == 400
    kept = Learner.add_facts([], [{"text": "Flask 3.1 needs Python 3.9", "source": "https://a"}])
    Learner.add_facts(kept, [{"text": "Flask 3.1 needs Python 3.9.", "source": "https://b"},
                             {"text": "Flask 3.1 needs Python 3.9", "source": "https://a"},
                             {"text": "Something else entirely", "source": "https://c"}])
    assert len(kept) == 2 and kept[0]["also"] == ["https://b"]
    assert Learner.pages_of(kept[0]) == ["https://a", "https://b"]


@pytest.mark.spec("KNW-10")
def test_relevant_chunks():
    paragraphs = ["intro about the page " * 10] + [f"filler {i} " * 25 for i in range(6)] + ["install with pip " * 10]
    chunks = Learner.relevant_chunks("\n\n".join(paragraphs), 250, "pip install")
    assert len(chunks) == 2 and chunks[0].startswith("intro") and chunks[1].startswith("install with pip")
    assert Learner.relevant_chunks("short page", 250, "x") == ["short page"]
    assert Learner.UNRELEASED.search("Flask 4.0 is in development") and not Learner.UNRELEASED.search("Flask 3.1 adds")


class FakeInternet:
    """The Internet plugin, answering from a script of pages."""

    def __init__(self, pages=None, up=True):
        self.pages, self.up = pages or {}, up

    def available(self):
        return self.up

    def why(self, key=None):
        return "offline" if not self.up else "no answer"

    def search(self, query, count):
        if not self.up:
            return None
        return [{"title": url, "url": url, "content": ""} for url in self.pages][:count]

    def fetch(self, url):
        return {"title": url, "url": url, "content": self.pages[url]} if self.up else None


PLAN = {"name": "Flask", "summary": "A small web framework.", "language": "", "kind": "tool",
        "queries": ["flask quickstart"], "commands": ["echo Flask 3.1.0"]}


@pytest.mark.spec("KNW-11")
def test_learn_a_tool(make_model, fake_ollama, tinker_home):
    fake_ollama.reply(json.dumps(PLAN), when="Plan learning")
    fake_ollama.reply(json.dumps({"facts": [{"phase": "code", "text": "Flask 3.1 routes use @app.route"},
                                            {"phase": "code", "text": "Flask 4.0 is unreleased"}]}), when="Facts")
    internet = FakeInternet({"https://flask.dev/quick": "Flask quickstart. Routes are decorators."})
    entry = Learner(make_model(), internet, None).learn("Flask")
    assert entry["name"] == "Flask" and entry["sources"] == ["https://flask.dev/quick"]
    assert [f["text"] for f in entry["facts"]] == ["Flask 3.1 routes use @app.route"]
    assert entry["facts"][0]["source"] == "https://flask.dev/quick"
    assert entry["machine"] == [{"command": "echo Flask 3.1.0", "found": True, "version": "3.1.0", "output": "Flask 3.1.0"}]
    assert Library.read("flask")["facts"] == entry["facts"]


@pytest.mark.spec("KNW-11")
def test_learning_cut_short_keeps_what_was_learned_before(make_model, fake_ollama, tinker_home):
    learned("flask", **{k: v for k, v in PLAN.items() if k != "kind"}, learned="2026-01-01", kind="tool",
            facts=[{"phase": "code", "text": "old fact"}])
    learner = Learner(make_model(), FakeInternet(up=False), None)
    entry = learner.learn("Flask")
    assert entry["facts"] == [{"phase": "code", "text": "old fact"}] and learner.kept_earlier
    assert fake_ollama.calls == []  # the saved plan is reused: no call


@pytest.mark.spec("KNW-11")
def test_learning_nothing_gives_none(make_model, fake_ollama, tinker_home):
    fake_ollama.reply(json.dumps(dict(PLAN, commands=["false"])), when="Plan learning")
    assert Learner(make_model(), FakeInternet(up=False), None).learn("Flask") is None
    assert Library.read("flask") is None


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
        {"question": "No searches?", "queries": []}, "junk"] + [{"question": f"Q{i}", "queries": ["q"]} for i in range(9)]})
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
    assert [g["search"] for g in analysis["gaps"]] == ["s1", "s2"] and [c["fact"] for c in analysis["checks"]] == [1, 2]
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
    vectors = [[1, 0], [0.85, 0.53], [0.82, 0.57], [0, 1]]  # the same claim, but no repeat (below REPEAT_CLAIM)
    differ = Researcher(type("L", (), {"model": None, **FakeLearner(vectors).__dict__})()).cross_check(facts)
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
    corroborated, single, disputed = ({"confidence": c} for c in ("corroborated", "single", "disputed"))
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
    assert [(f["confidence"], f["question"]) for f in entry["facts"]] == \
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


@pytest.mark.spec("KNW-15")
def test_a_domain_is_researched_when_tinker_learn_finds_one(make_model, fake_ollama, tinker_home):
    plan = {"name": "Food bank inventory", "summary": "s", "language": "", "kind": "domain", "queries": ["q"]}
    fake_ollama.reply(json.dumps(plan), when="Plan learning")
    fake_ollama.reply(json.dumps(FRAME), when="Frame the research")
    fake_ollama.reply(json.dumps({"facts": [{"phase": "spec", "text": "Food is weighed"}]}), when="Facts", times=2)
    fake_ollama.reply(analysis(), when="Analyse")
    entry = Learner(make_model(), FakeInternet({"https://a.org/x": "Food is weighed."}), None).learn("food banks", deep=True)
    assert entry["kind"] == "research"


LANGUAGE_PLAN = {"name": "Go", "version": "1.25", "summary": "A compiled language.",
                 "tools": [{"role": "tests", "name": "go test", "search": "go test flags", "command": "echo go version go1.25.1"}],
                 "searches": [{"section": "fundamentals", "query": "Go spec"}, {"section": "new", "query": "Go 1.25 notes"}],
                 "commands": ["echo go version go1.25.1"]}


@pytest.mark.spec("KNW-16")
def test_a_language_is_learned_as_one(make_model, fake_ollama, tinker_home):
    fake_ollama.reply(json.dumps({"name": "Go", "summary": "s", "language": "Go", "kind": "tool", "queries": []}),
                      when="Plan learning (")
    fake_ollama.reply(json.dumps(LANGUAGE_PLAN), when="Plan learning the language")
    fake_ollama.reply(json.dumps({"facts": [{"phase": "code", "text": "Go 1.25 has generic type aliases"}]}),
                      when="Facts", times=3)
    pages = {"https://go.dev/doc": "Go docs.", "https://go.dev/notes": "Release notes.", "https://go.dev/test": "go test."}
    entry = Learner(make_model(), FakeInternet(pages), None).learn("Go")
    assert entry["kind"] == "language" and entry["language"] == "Go" and entry["name"] == "Go 1.25"
    assert entry["tools"] == [{"role": "tests", "name": "go test", "search": "go test flags"}]
    assert entry["machine"][0]["version"] == "1.25.1" and {f["section"] for f in entry["facts"]} <= {"fundamentals", "new",
                                                                                                    "tools"}
    assert Library.language("Go") == "go"
    fake_ollama.reply(json.dumps({"name": "Go", "summary": "s", "language": "Go", "queries": []}), when="Plan learning (")
    again = Learner(make_model(), FakeInternet(pages), None).learn("golang")
    assert again["kind"] == "language" and fake_ollama.labels()[-1].startswith("Plan learning (")  # learned already
