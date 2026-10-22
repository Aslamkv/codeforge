"""TCH: asking what Tinker keeps, and teaching it: checked on this machine and the web before anything changes."""

import json
import sys

import pytest

import tinker
from tinker import Cli, Commands
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.model import Ollama
from tinkerkit.prompts import Prompts
from tinkerkit.settings import Home, MEMORY_SHOWN, TEACH_CLAIMS
from tinkerkit.teaching import Asker, Memory, Teacher, Verdict, read_plan
from tinkerkit.workspace import Toolchain
from conftest import MODEL, TOOLCHAIN

PY = sys.executable
BLACK_AND_RUFF = dict(TOOLCHAIN, format="black .", lint="ruff check .")
PAGE = {"title": "Ruff formatter", "url": "https://docs.astral.sh/ruff/formatter/",
        "content": "The Ruff formatter is a drop-in replacement for Black, designed to be used with Ruff's linter."}


class FakeWeb:
    """The Internet plugin, scripted: every search finds the pages given, and each is fetched as it is."""

    def __init__(self, pages=(PAGE,)):
        self.pages, self.queries = list(pages), []

    def available(self):
        return True

    def why(self, key=None):
        return "nothing found"

    def search(self, query, count):
        self.queries.append(query)
        return self.pages[:count]

    def fetch(self, url):
        return next((page for page in self.pages if page["url"] == url), None)


@pytest.fixture
def kept(home_store):
    """Python's toolchain, which formats with black and lints with ruff, a lesson that says to use both, and a fact
    learned about Flask."""
    python = Knowledge("Python", "r1")
    python.remember_toolchain(Toolchain(BLACK_AND_RUFF))
    python.add_lessons([{"text": "Format Python with black before linting with ruff", "phase": "toolchain",
                         "scope": "language"},
                        {"text": "Name each test after the behaviour it checks", "phase": "tests", "scope": "any"}], [])
    Library.save("flask", {"topic": "flask", "name": "Flask", "summary": "A web framework.",
                           "facts": [{"phase": "code", "text": "Flask routes use @app.route"}]})
    return Memory()


def numbered_item(memory, text, kind):
    """The number an item is shown with to a teaching about text."""
    items = memory.about(text)
    return next(n for n, item in enumerate(items, 1) if item["kind"] == kind and "black" in item["text"])


def plan_reply(**changes):
    plan = {"claims": ["Ruff formats Python code as black does"], "commands": [f'"{PY}" --version'],
            "searches": ["ruff formatter replaces black"], "language": "Python", "retire": [],
            "toolchain": {"format": "ruff format ."},
            "lesson": {"phase": "toolchain", "text": "Format and lint Python with ruff alone; black is not needed"}}
    return json.dumps(dict(plan, **changes))


def verdict_reply(verdict="holds", evidence=(2,), change=True):
    return json.dumps({"claims": [{"claim": 1, "verdict": verdict, "why": "the Ruff docs say so",
                                   "evidence": list(evidence)}], "change": change, "why": "it follows"})


@pytest.mark.spec("TCH-1")
def test_memory_is_what_tinker_keeps_but_the_retired_most_related_first(kept):
    kinds = {item["kind"] for item in kept.items}
    assert kinds == {"toolchain", "lesson", "fact"}
    Knowledge("Python", "r2").disprove({f"lesson:{kept.items[1]['id'].split(':')[1]}": ["toolchain"]}, ("toolchain",))
    assert all(item["status"] != "retired" for item in Memory().items)
    first = kept.about("what is your current toolchain for python")[0]
    assert first["kind"] == "toolchain" and "black" in first["text"]
    assert kept.about("which lessons do you have")[0]["kind"] == "lesson"
    assert len(kept.about("anything", limit=2)) == 2 and MEMORY_SHOWN >= 20


@pytest.mark.spec("TCH-2")
def test_ask_answers_from_what_is_kept_citing_its_items(kept, make_model, fake_ollama, capsys):
    fake_ollama.reply("Python is formatted with black and linted with ruff [1].", when="ask")
    ending = Asker(make_model(), kept).answer("what is your current toolchain for python")
    asked = fake_ollama.calls[0]["messages"]
    assert asked[0]["content"].startswith(Prompts.ASK_SYSTEM) and "[1] toolchain (python, proven)" in asked[1]["content"]
    out = capsys.readouterr().out
    assert "Python is formatted with black and linted with ruff [1]." in out and "• [1] toolchain (python" in out
    assert ending["status"] == "done" and ending["details"][0] == ("Answer", "Python is formatted with black and "
                                                                             "linted with ruff [1].")
    assert [label for label, _ in ending["details"]] == ["Answer", "From"]
    assert Home.store().get("languages", "python")["toolchain"]["format"] == "black ."  # nothing changed


@pytest.mark.spec("TCH-2")
def test_ask_with_nothing_kept_asks_no_model(home_store, make_model, fake_ollama):
    ending = Asker(make_model(), Memory()).answer("what do you know")
    assert ending["status"] == "blocked" and fake_ollama.calls == []


@pytest.mark.spec("TCH-3")
def test_read_plan_keeps_what_is_usable_within_limits():
    plan = read_plan({"claims": [f"claim {n}" for n in range(9)] + [""], "commands": ["ruff --version"],
                      "searches": ["a", "b", "c", "d"], "language": "Python", "retire": [1, 3, 99, "2"],
                      "toolchain": {"format": "ruff format .", "run": "python app.py", "lint": "",
                                    "setup": ["pip install ruff", " "], "test_all": ["not", "a list"]},
                      "lesson": {"phase": "toolchain", "text": "Use ruff"}}, count=3)
    assert len(plan["claims"]) == TEACH_CLAIMS and len(plan["searches"]) == 3
    assert plan["retire"] == [1, 3] and plan["toolchain"] == {"format": "ruff format .", "setup": ["pip install ruff"]}
    assert plan["lesson"] == {"phase": "toolchain", "text": "Use ruff"}
    assert read_plan({"claims": ["c"], "searches": ["s"], "lesson": {"phase": "nowhere", "text": "x"}, "retire": [1]},
                     count=1)["lesson"] is None
    for broken, why in [({"claims": [], "searches": ["s"], "retire": [1]}, "no claims"),
                        ({"claims": ["c"], "retire": [1]}, "no command or web search"),
                        ({"claims": ["c"], "searches": ["s"]}, "no change")]:
        with pytest.raises(ValueError, match=why):
            read_plan(broken, count=1)


@pytest.mark.spec("TCH-4")
def test_teach_runs_only_safe_commands_and_reads_the_pages(kept, make_model, fake_ollama, capsys):
    web = FakeWeb()
    fake_ollama.reply(plan_reply(commands=[f'"{PY}" --version', "pip install black"]), when="what to check")
    fake_ollama.reply(verdict_reply("unsure"), when="weigh the evidence")
    Teacher(make_model(), web, kept).teach("ruff alone is enough, black is not needed")
    out = capsys.readouterr().out
    assert "Skipped `pip install black`" in out and "Python 3" in out
    judged = fake_ollama.calls[1]["messages"][1]["content"]
    assert "[1] this machine" in judged and f"[2] {PAGE['url']}" in judged and "drop-in replacement" in judged
    assert web.queries == ["ruff formatter replaces black"]


@pytest.mark.spec("TCH-4")
def test_teach_with_no_evidence_changes_nothing_and_asks_no_more(kept, make_model, fake_ollama):
    fake_ollama.reply(plan_reply(commands=["rm -rf build"]), when="what to check")
    ending = Teacher(make_model(), FakeWeb(pages=()), kept).teach("ruff alone is enough")
    assert ending["status"] == "blocked" and "nothing could check it" in ending["headline"]
    assert len(fake_ollama.calls) == 1
    assert Home.store().get("languages", "python")["toolchain"]["format"] == "black ."


@pytest.mark.spec("TCH-5")
def test_a_verdict_holds_only_when_every_claim_holds_on_evidence_and_the_change_follows():
    claims = ["a", "b"]
    read = lambda given, change=True: Verdict.read({"claims": given, "change": change}, claims, evidence=2)
    holds = {"claim": 1, "verdict": "holds", "evidence": [1]}
    assert read([holds, dict(holds, claim=2, evidence=[2, 7])]).holds()
    assert read([holds, dict(holds, claim=2, evidence=[2, 7])]).claims[1]["evidence"] == [2]
    assert not read([holds, dict(holds, claim=2, evidence=[9])]).holds()  # no evidence it can cite
    assert not read([holds, dict(holds, claim=2, verdict="unsure")]).holds()
    assert not read([holds, dict(holds, claim=2)], change=False).holds()
    with pytest.raises(ValueError, match="claim 2 has no verdict"):
        read([holds])
    with pytest.raises(ValueError, match="claim 2 has no verdict"):
        read([holds, dict(holds, claim=2, verdict="maybe")])


@pytest.mark.spec("TCH-5")
def test_a_teaching_that_does_not_hold_changes_nothing(kept, make_model, fake_ollama, capsys):
    before = {key: Home.store().get(*key) for key in [("languages", "python"), ("memory", "any")]}
    lesson = numbered_item(kept, "ruff alone is enough, black is not needed", "lesson")
    fake_ollama.reply(plan_reply(retire=[lesson]), when="what to check")
    fake_ollama.reply(verdict_reply("wrong", evidence=(2,)), when="weigh the evidence")
    ending = Teacher(make_model(), FakeWeb(), kept).teach("ruff alone is enough, black is not needed")
    assert ending["status"] == "blocked" and ending["headline"] == "Nothing changed: the teaching did not hold"
    assert ending["details"] == [("Wrong", "Ruff formats Python code as black does (the Ruff docs say so)")]
    assert {key: Home.store().get(*key) for key in before} == before
    assert "❌ Wrong: Ruff formats Python code as black does" in capsys.readouterr().out


@pytest.mark.spec("TCH-6")
def test_a_teaching_that_holds_corrects_what_tinker_keeps(kept, make_model, fake_ollama):
    teaching = "ruff alone is enough, black is not needed"
    lesson = numbered_item(kept, teaching, "lesson")
    fake_ollama.reply(plan_reply(retire=[lesson]), when="what to check")
    fake_ollama.reply(verdict_reply(), when="weigh the evidence")
    ending = Teacher(make_model(), FakeWeb(), kept).teach(teaching)
    python = Home.store().get("languages", "python")
    assert python["toolchain"]["format"] == "ruff format ." and python["toolchain"]["lint"] == "ruff check ."
    retired = python["retired"][-1]
    assert retired["text"] == "Format Python with black before linting with ruff"
    assert retired["reason"] == f"corrected by what you taught: {teaching}"
    taught = next(l for l in python["lessons"] if "ruff alone" in l["text"])
    assert taught["status"] == "proven" and taught["sources"] == [PAGE["url"]]
    assert taught["history"][-1]["event"] == "taught"
    assert ending["status"] == "done" and ending["headline"] == "Corrected: 3 change(s)"


@pytest.mark.spec("TCH-6")
def test_a_taught_lesson_like_a_known_one_proves_it_and_no_toolchain_is_made(home_store):
    known = Knowledge("Go", "r1")
    known.add_lessons([{"text": "Run gofmt on every file before the tests", "phase": "toolchain", "scope": "language"}],
                      [])
    lesson = Knowledge("Go").taught_lesson({"text": "Run gofmt on every file before tests", "phase": "toolchain",
                                            "scope": "language"}, ["https://go.dev"])
    lessons = home_store.get("languages", "go")["lessons"]
    assert len(lessons) == 1 and lesson["status"] == "proven" and lessons[0]["status"] == "proven"
    assert Knowledge("Go").correct_toolchain({"format": "gofmt -w ."}) is None
    assert home_store.get("languages", "go").get("toolchain") is None


@pytest.mark.spec("TCH-7")
def test_teach_needs_the_web_before_any_model_call(measured_model, fake_ollama, monkeypatch):
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    with pytest.raises(SystemExit, match="tinker teach needs the Internet plugin"):
        Commands(Cli.parse(["teach", "ruff", "alone", "is", "enough"])).run()
    assert fake_ollama.calls == []


@pytest.mark.spec("TCH-7")
def test_ask_and_teach_report_through_a_tracker_and_run_as_tasks(kept, measured_model, fake_ollama, monkeypatch,
                                                                 capsys):
    from tinkerkit import conversation
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    monkeypatch.setattr(tinker.Internet, "required", staticmethod(lambda option: FakeWeb()))
    ended = []
    monkeypatch.setattr(Commands, "tracker_ended", staticmethod(lambda tracker, ending, took: ended.append(
        (tracker.state["title"], ending["status"], tracker.state["steps"]))))
    fake_ollama.reply("Black formats it [1].", when="ask")
    Commands(Cli.parse(["ask", "what", "formats", "python"])).run()
    fake_ollama.reply(plan_reply(), when="what to check")
    fake_ollama.reply(verdict_reply(), when="weigh the evidence")
    Commands(Cli.parse(["teach", "ruff", "alone", "is", "enough"])).run()
    assert ended[0][:2] == ("Asking: what formats python", "done")
    assert ended[1][:2] == ("Teaching: ruff alone is enough", "done")
    assert "🤔 Worked out what to check" in ended[1][2] and "⚖️ Evidence weighed" in ended[1][2]
    assert "ask" in conversation.LONG and "teach" in conversation.LONG
    assert "Time taken" in capsys.readouterr().out
