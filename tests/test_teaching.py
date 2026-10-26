import json
import sys

import pytest
from conftest import MODEL, TOOLCHAIN, FakeSearch, web_page

from tinkerkit import cli
from tinkerkit.cli import Cli, Commands
from tinkerkit.commands import kind_of
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.model import Ollama
from tinkerkit.project import Tracker
from tinkerkit.prompts import Prompts
from tinkerkit.reading import Readings
from tinkerkit.settings import MEMORY_ITEMS_SHOWN, TEACH_CLAIMS, Home
from tinkerkit.teaching import Asker, Memory, Teacher, Verdict, marked, read_plan
from tinkerkit.workspace import Toolchain

PY = sys.executable
BLACK_AND_RUFF = dict(TOOLCHAIN, format="black .", lint="ruff check .")
PAGE = {
    "title": "Ruff formatter",
    "url": "https://docs.astral.sh/ruff/formatter/",
    "content": "The Ruff formatter is a drop-in replacement for Black, designed to be used with Ruff's linter.",
}


class FakeWeb(FakeSearch):
    def __init__(self, pages=(PAGE,)):
        super().__init__()
        self.pages, self.queries = list(pages), []

    def search(self, query, count):
        self.queries.append(query)
        return self.pages[:count]

    def fetch(self, urls, max_bytes=None):
        return [self.page(url) for url in urls]

    def page(self, url):
        page = next(page for page in self.pages if page["url"] == url)
        return web_page(url, page["content"])


@pytest.fixture
def kept(home_store):
    python = Knowledge("Python", "r1")
    python.remember_toolchain(Toolchain(BLACK_AND_RUFF))
    python.add_lessons(
        [
            {
                "text": "Format Python with black before linting with ruff",
                "phase": "toolchain",
                "scope": "language",
            },
            {"text": "Name each test after the behaviour it checks", "phase": "tests", "scope": "any"},
        ],
        [],
    )
    Library.save(
        "flask",
        {
            "topic": "flask",
            "name": "Flask",
            "summary": "A web framework.",
            "facts": [{"phase": "code", "text": "Flask routes use @app.route"}],
        },
    )
    return Memory()


def numbered_item(memory, text, kind):
    items = memory.about(text)
    return next(
        number for number, item in enumerate(items, 1) if item["kind"] == kind and "black" in item["text"]
    )


RUFF_FACT = {"text": "Ruff formats Python code as black does", "preference": False}
NO_MYPY = {
    "text": "Our projects need no type checker: the spec's tests catch what matters",
    "preference": True,
}
RUFF_CHANGE = {
    "claims": [1],
    "toolchain": {"format": "ruff format ."},
    "lesson": {"phase": "toolchain", "text": "Format and lint Python with ruff alone; black is not needed"},
}
NO_MYPY_CHANGE = {
    "claims": [2],
    "toolchain": {"lint": "ruff check ."},
    "lesson": {"phase": "toolchain", "text": "Run no type checker in Python projects"},
}


def plan_reply(**changes):
    plan = {
        "claims": [RUFF_FACT, NO_MYPY],
        "commands": [f'"{PY}" --version'],
        "searches": ["ruff formatter replaces black"],
        "language": "Python",
        "changes": [RUFF_CHANGE, NO_MYPY_CHANGE],
    }
    return json.dumps(dict(plan, **changes))


def verdict_reply(verdict="holds", evidence=(2,), follows=True):
    return json.dumps(
        {
            "claims": [
                {"claim": 1, "verdict": verdict, "why": "the Ruff docs say so", "evidence": list(evidence)}
            ],
            "changes": [{"change": 1, "follows": follows, "why": "it agrees with the docs"}],
        }
    )


def lessons():
    return [lesson["text"] for lesson in Home.store().get("languages", "python")["lessons"]]


@pytest.mark.spec("TCH-1")
def test_memory_is_what_tinker_keeps_but_the_retired_most_related_first(kept):
    kinds = {item["kind"] for item in kept.items}
    assert kinds == {"toolchain", "lesson", "fact"}
    Knowledge("Python", "r2").disprove(
        {f"lesson:{kept.items[1]['id'].split(':')[1]}": ["toolchain"]}, ("toolchain",)
    )
    assert all(item["status"] != "retired" for item in Memory().items)
    first = kept.about("what is your current toolchain for python")[0]
    assert first["kind"] == "toolchain" and "black" in first["text"]
    assert kept.about("which lessons do you have")[0]["kind"] == "lesson"
    assert kept.about("what is the current toolset you use for a python project")[0]["kind"] == "toolchain"
    assert len(kept.about("anything", limit=2)) == 2 and MEMORY_ITEMS_SHOWN >= 20


@pytest.mark.spec("TCH-2")
def test_ask_says_the_answer_alone(kept, make_model, fake_ollama, capsys):
    answer = "Tests run with pytest.\nCode is formatted with black and linted with ruff."
    fake_ollama.reply(answer, when="ask")
    ending = Asker(make_model(), kept).answer("what is your current toolchain for python")
    asked = fake_ollama.calls[0]["messages"]
    assert (
        asked[0]["content"].startswith(Prompts.ASK_SYSTEM)
        and "[1] toolchain (python, proven)" in asked[1]["content"]
    )
    assert capsys.readouterr().out == answer + "\n"
    assert ending == {"status": "done", "headline": "Answered", "details": [("Answer", answer)], "next": []}
    unchanged = "black ."
    assert Home.store().get("languages", "python")["toolchain"]["format"] == unchanged


@pytest.mark.spec("TCH-2")
def test_ask_with_nothing_kept_asks_no_model(home_store, make_model, fake_ollama, capsys):
    Asker(make_model(), Memory()).answer("what do you know")
    assert fake_ollama.calls == [] and capsys.readouterr().out.startswith("I have not learned anything yet")


@pytest.mark.spec("TCH-4")
def test_read_plan_keeps_what_is_usable_within_limits():
    plan = read_plan(
        {
            "claims": [f"claim {number}" for number in range(9)]
            + [{"text": "I prefer X", "preference": True}, ""],
            "commands": ["ruff --version"],
            "searches": [" a  plain ", {"query": "b", "engines": ["wikibooks"], "count": 2}, "", "c", "d"],
            "language": "Python",
            "changes": [
                {
                    "claims": [1, 99],
                    "retire": [1, 3, 99, "2"],
                    "toolchain": {"format": "ruff format .", "lint": "", "setup": ["pip install ruff", " "]},
                    "lesson": {"phase": "toolchain", "text": "Use ruff"},
                },
                {"claims": [], "lesson": {"phase": "code", "text": "rests on nothing"}},
                {"claims": [2], "lesson": {"phase": "nowhere", "text": "changes nothing"}},
            ],
        },
        count=3,
    )
    assert len(plan["claims"]) == TEACH_CLAIMS and plan["searches"] == [
        {"query": "a plain"},
        {"query": "b", "engines": ["wikibooks"], "count": 2},
        {"query": "c"},
    ]
    assert plan["claims"][0] == {"text": "claim 0", "preference": False}
    assert plan["changes"] == [
        {
            "claims": [1],
            "retire": [1, 3],
            "lesson": {"phase": "toolchain", "text": "Use ruff"},
            "toolchain": {"format": "ruff format .", "setup": ["pip install ruff"]},
            "fact": None,
        }
    ]
    preferences = read_plan(
        {
            "claims": [{"text": "I prefer X", "preference": True}],
            "changes": [{"claims": [1], "lesson": {"phase": "code", "text": "Use X"}}],
        },
        count=0,
    )
    nothing_to_check = []
    assert preferences["commands"] == preferences["searches"] == nothing_to_check
    for broken, why in [
        ({"claims": [], "searches": ["s"], "changes": [RUFF_CHANGE]}, "no claims"),
        ({"claims": ["c"], "changes": [RUFF_CHANGE]}, "no command or web search"),
        ({"claims": ["c"], "searches": ["s"]}, "no change"),
        (
            {
                "claims": ["c", "d"],
                "searches": ["s"],
                "changes": [RUFF_CHANGE, dict(RUFF_CHANGE, claims=[2])],
            },
            "toolchain format is changed twice",
        ),
        (
            {"claims": ["c"], "searches": ["s"], "changes": [dict(RUFF_CHANGE, toolchain={"run": "x"})]},
            "toolchain run cannot be changed",
        ),
        (
            {"claims": ["c"], "searches": ["s"], "changes": [dict(RUFF_CHANGE, toolchain={"lint": 3})]},
            "toolchain lint must be a command written as text",
        ),
    ]:
        with pytest.raises(ValueError, match=why):
            read_plan(broken, count=1)


@pytest.mark.spec("TCH-5")
def test_teach_runs_only_safe_commands_and_reads_the_pages(kept, make_model, fake_ollama, capsys):
    web = FakeWeb()
    fake_ollama.reply(
        plan_reply(commands=[f'"{PY}" --version', "pip install black", "no-such-tool --help"]),
        when="what to check",
    )
    fake_ollama.reply(verdict_reply("unsure"), when="weigh the evidence")
    Teacher(make_model(), web, kept).teach("ruff alone is enough, and I prefer no mypy")
    out = capsys.readouterr().out
    assert "Skipped `pip install black`" in out and "Python 3" in out
    judged = fake_ollama.calls[1]["messages"][1]["content"]
    assert "[1] this machine" in judged and f"[3] {PAGE['url']}" in judged and "drop-in replacement" in judged
    assert "not found here (a project's own environment may have it)" in judged
    assert "## Facts\n1. Ruff formats" in judged and "## The person's preferences\n2. Our projects" in judged
    a_preferences_change = "Change 2"
    assert "Change 1 (rests on claims 1)" in judged and a_preferences_change not in judged
    assert web.queries == ["ruff formatter replaces black"]


@pytest.mark.spec("TCH-5")
def test_preferences_alone_are_honored_with_no_check(kept, make_model, fake_ollama, capsys):
    fake_ollama.reply(
        plan_reply(claims=[NO_MYPY], commands=[], searches=[], changes=[dict(NO_MYPY_CHANGE, claims=[1])]),
        when="what to check",
    )
    web = FakeWeb()
    ending = Teacher(make_model(), web, kept).teach("I prefer no mypy")
    assert len(fake_ollama.calls) == 1 and web.queries == []
    assert ending["status"] == "done" and "Run no type checker in Python projects" in lessons()
    assert "👤 Your preference, honored: Our projects need no type checker" in capsys.readouterr().out


@pytest.mark.spec("TCH-4")
def test_toolchain_commands_are_read_in_either_form():
    change = {
        "claims": [1],
        "toolchain": {
            "setup": "python3 -m venv .venv && pip install pytest ruff",
            "lint": ["ruff check .", "ruff format --check ."],
        },
    }
    plan = read_plan({"claims": ["c"], "searches": ["s"], "changes": [change]}, count=0)
    assert plan["changes"][0]["toolchain"] == {
        "setup": ["python3 -m venv .venv", "pip install pytest ruff"],
        "lint": "ruff check . && ruff format --check .",
    }


@pytest.mark.spec("TCH-4")
def test_only_a_teaching_marked_with_prefer_has_preferences():
    reply = json.loads(plan_reply())
    assert [claim["preference"] for claim in read_plan(reply, count=0, preferences=False)["claims"]] == [
        False,
        False,
    ]
    assert [claim["preference"] for claim in read_plan(reply, count=0)["claims"]] == [False, True]
    assert marked("I prefer no mypy") and marked("my preference: no mypy") and marked("Preferred: ruff")
    assert not marked("we need no mypy")


@pytest.mark.spec("TCH-5")
def test_facts_nothing_could_check_are_unsure_and_asked_no_more(kept, make_model, fake_ollama):
    fake_ollama.reply(plan_reply(commands=["rm -rf build"]), when="what to check")
    ending = Teacher(make_model(), FakeWeb(pages=()), kept).teach(
        "ruff alone is enough, and I prefer no mypy"
    )
    assert len(fake_ollama.calls) == 1
    assert ("Unsure", "Ruff formats Python code as black does (nothing could check it)") in ending["details"]
    its_change_waits, the_preferences_change_is_made = "black .", "ruff check ."
    assert Home.store().get("languages", "python")["toolchain"]["format"] == its_change_waits
    assert Home.store().get("languages", "python")["toolchain"]["lint"] == the_preferences_change_is_made


@pytest.mark.spec("TCH-6")
def test_a_verdict_needs_every_fact_and_every_change_a_fact_is_for():
    plan = read_plan(json.loads(plan_reply()), count=0)
    read = lambda claims, changes=({"change": 1, "follows": True},): Verdict.read(
        {"claims": claims, "changes": list(changes)}, plan, evidence=2
    )
    holds = {"claim": 1, "verdict": "holds", "evidence": [1, 7]}
    verdict = read([holds])
    assert (
        verdict.claims_by_number[1]["evidence"] == [1]
        and verdict.claims_by_number[2]["verdict"] == "preference"
    )
    assert verdict.why_not(1, plan["changes"][0]) == verdict.why_not(2, plan["changes"][1]) == ""
    cites_nothing_given = dict(holds, evidence=[9])
    assert read([cites_nothing_given]).why_not(1, plan["changes"][0]) == "claim 1 is unsure"
    assert read([dict(holds, verdict="wrong")]).why_not(1, plan["changes"][0]) == "claim 1 is wrong"
    assert (
        read([holds], [{"change": 1, "follows": False, "why": "the docs say otherwise"}]).why_not(
            1, plan["changes"][0]
        )
        == "does not follow: the docs say otherwise"
    )
    with pytest.raises(ValueError, match="claim 1 has no verdict"):
        read([])
    with pytest.raises(ValueError, match="claim 1 has no verdict"):
        read([dict(holds, verdict="maybe")])
    with pytest.raises(ValueError, match="change 1 has no"):
        read([holds], [])


@pytest.mark.spec("TCH-6")
def test_a_change_whose_fact_does_not_hold_waits_and_the_rest_are_made(kept, make_model, fake_ollama, capsys):
    fake_ollama.reply(plan_reply(), when="what to check")
    fake_ollama.reply(verdict_reply("wrong"), when="weigh the evidence")
    ending = Teacher(make_model(), FakeWeb(), kept).teach("ruff alone is enough, and I prefer no mypy")
    assert ending["status"] == "done" and ending["headline"] == "Corrected: 2 change(s)"
    assert ("Wrong", "Ruff formats Python code as black does (the Ruff docs say so)") in ending["details"]
    assert (
        "Not changed",
        "Format and lint Python with ruff alone; black is not needed (claim 1 is wrong)",
    ) in ending["details"]
    assert Home.store().get("languages", "python")["toolchain"]["format"] == "black ."
    assert "Run no type checker in Python projects" in lessons()
    out = capsys.readouterr().out
    assert "❌ Wrong: Ruff formats" in out and "➖ Not changed: Format and lint" in out


@pytest.mark.spec("TCH-6")
def test_nothing_changes_when_no_change_can_be_made(kept, make_model, fake_ollama):
    before = {key: Home.store().get(*key) for key in [("languages", "python"), ("memory", "any")]}
    fake_ollama.reply(plan_reply(claims=[RUFF_FACT], changes=[RUFF_CHANGE]), when="what to check")
    fake_ollama.reply(verdict_reply("unsure"), when="weigh the evidence")
    ending = Teacher(make_model(), FakeWeb(), kept).teach("ruff alone is enough")
    assert ending["status"] == "blocked" and ending["headline"] == "Nothing changed"
    assert {key: Home.store().get(*key) for key in before} == before


@pytest.mark.spec("TCH-7")
def test_a_teaching_that_holds_corrects_what_tinker_keeps(kept, make_model, fake_ollama):
    teaching = "ruff alone is enough, black is not needed, and I prefer no mypy"
    lesson = numbered_item(kept, teaching, "lesson")
    fake_ollama.reply(
        plan_reply(changes=[dict(RUFF_CHANGE, retire=[lesson]), NO_MYPY_CHANGE]), when="what to check"
    )
    fake_ollama.reply(verdict_reply(), when="weigh the evidence")
    ending = Teacher(make_model(), FakeWeb(), kept).teach(teaching)
    python = Home.store().get("languages", "python")
    assert python["toolchain"]["format"] == "ruff format ." and python["toolchain"]["lint"] == "ruff check ."
    retired = python["retired"][-1]
    assert retired["text"] == "Format Python with black before linting with ruff"
    assert retired["reason"] == f"corrected by what you taught: {teaching}"
    taught = {lesson["text"]: lesson for lesson in python["lessons"]}
    ruff, no_mypy = (
        taught["Format and lint Python with ruff alone; black is not needed"],
        taught["Run no type checker in Python projects"],
    )
    assert ruff["status"] == no_mypy["status"] == "proven" and ruff["history"][-1]["event"] == "taught"
    assert ruff["sources"] == [PAGE["url"]] and no_mypy["sources"] == ["your preference"]
    assert ending["status"] == "done" and ending["headline"] == "Corrected: 5 change(s)"


@pytest.mark.spec("TCH-9")
def test_ask_and_teach_report_through_a_tracker_and_run_as_jobs(
    kept, measured_model, fake_ollama, monkeypatch, capsys
):

    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": MODEL}]))
    monkeypatch.setattr(cli, "Internet", FakeWeb)
    ended = []
    monkeypatch.setattr(
        Tracker,
        "end",
        lambda tracker, ending, took: ended.append(
            (tracker.state["title"], ending["status"], tracker.state["steps"])
        ),
    )
    fake_ollama.reply("Black formats it [1].", when="ask")
    Commands(Cli.parse(["ask", "what", "formats", "python"])).run()
    fake_ollama.reply(plan_reply(), when="what to check")
    fake_ollama.reply(verdict_reply(), when="weigh the evidence")
    Commands(Cli.parse(["teach", "ruff", "alone", "is", "enough,", "I", "prefer", "no", "mypy"])).run()
    assert ended[0][:2] == ("Asking: what formats python", "done")
    out = capsys.readouterr().out
    assert out.startswith("Black formats it [1].") and out.count("Time taken") == 2
    assert ended[1][:2] == ("Teaching: ruff alone is enough, I prefer no mypy", "done")
    steps = [step.split(":")[0] for step in ended[1][2]]
    assert "🤔 Worked out what to check" in steps and "⚖️ Evidence weighed" in steps
    assert any(
        step.startswith("🤔 Worked out what to check: ") and "claim(s)" in step for step in ended[1][2]
    )
    assert kind_of(["ask", "q"]) == "job" and kind_of(["teach", "t"]) == "job"


WRONG = "The Craft says to write tests after the code"


def shelved():
    Readings.save(
        {
            "number": 1,
            "title": "The Craft",
            "author": "",
            "source": "craft.md",
            "fingerprint": "f",
            "added": "2026-10-24 10:00",
            "topic": "read-1",
            "survey": None,
            "chapters": [
                {
                    "number": 1,
                    "title": "Testing",
                    "kind": "body",
                    "state": "read",
                    "text": "Write one test for each behaviour before the code, then just enough code.",
                }
            ],
        }
    )
    Library.save(
        "read-1",
        {
            "topic": "The Craft",
            "name": "The Craft",
            "kind": "reading",
            "reading": 1,
            "summary": "On small programs.",
            "facts": [
                {"text": WRONG, "phase": "tests", "chapter": 1, "slot": "chapter 1", "section": "Order"}
            ],
        },
    )


@pytest.mark.spec("TCH-3")
def test_an_ask_about_a_book_sees_what_the_book_says(home_store, make_model, fake_ollama, capsys):
    shelved()
    fake_ollama.reply("Chapter 1 says each test comes before the code.", when="ask")
    Asker(make_model(), Memory()).answer("when does The Craft write each test?")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "## From what you read\n[The Craft, chapter 1: Testing]\nWrite one test" in prompt
    assert WRONG in prompt and capsys.readouterr().out.startswith("Chapter 1 says")


@pytest.mark.spec("TCH-8")
def test_a_teaching_about_a_book_is_checked_against_it_and_keeps_the_fact(
    home_store, make_model, fake_ollama
):
    shelved()
    memory, teaching = Memory(), "The Craft says each test comes before the code, not after"
    about = next(number for number, item in enumerate(memory.about(teaching), 1) if item["text"] == WRONG)
    fake_ollama.reply(
        json.dumps(
            {
                "claims": [
                    {"text": "The Craft says to write each test before the code", "preference": False}
                ],
                "language": "",
                "changes": [
                    {"claims": [1], "fact": {"about": about, "text": "Write each test before the code"}}
                ],
            }
        ),
        when="what to check",
    )
    fake_ollama.reply(
        json.dumps(
            {
                "claims": [{"claim": 1, "verdict": "holds", "why": "chapter 1", "evidence": [1]}],
                "changes": [{"change": 1, "follows": True, "why": "it agrees"}],
            }
        ),
        when="weigh the evidence",
    )
    web = FakeWeb()
    ending = Teacher(make_model(), web, memory).teach(teaching)
    judged = fake_ollama.calls[1]["messages"][1]["content"]
    assert "[1] The Craft, chapter 1: Testing\nWrite one test" in judged and f"Retire: [{about}]" in judged
    assert web.queries == [] and ending["status"] == "done"
    entry = Library.read("read-1")
    assert [fact["text"] for fact in entry["retired"]] == [WRONG]
    fact = entry["facts"][-1]
    assert (fact["text"], fact["status"], fact["phase"], fact["chapter"], fact["section"]) == (
        "Write each test before the code",
        "proven",
        "tests",
        1,
        "Order",
    )
    assert fact["history"][-1]["event"] == "taught" and fact["sources"] == ["The Craft, chapter 1: Testing"]
    no_check = {"claims": ["c"], "changes": [{"claims": [1], "fact": {"about": 1, "text": "t"}}]}
    assert read_plan(no_check, count=1, readings=True)["changes"][0]["retire"] == [1]
    with pytest.raises(ValueError, match="no command or web search"):
        read_plan(no_check, count=1)
