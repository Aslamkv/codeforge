import json
import sys

import pytest
from conftest import MODEL, TOOLCHAIN, FakeSearch, web_page

from tinkerkit import cli
from tinkerkit.cli import Cli, Commands
from tinkerkit.commands import kind_of
from tinkerkit.knowledge import Embeddings, Knowledge, Library
from tinkerkit.learning import DocCache
from tinkerkit.model import Ollama
from tinkerkit.passages import Block, Page
from tinkerkit.principles import Principle, Principles
from tinkerkit.project import Tracker
from tinkerkit.prompts import Prompts
from tinkerkit.reading import Readings
from tinkerkit.sandbox import Ran, Sandbox
from tinkerkit.settings import (
    ASK_ITEM_CHARS,
    ASK_ITEMS_SHOWN,
    ASK_PASSAGES_SHOWN,
    BOOK_PASSAGES_SHOWN,
    MEMORY_ITEMS_SHOWN,
    TEACH_CLAIMS,
    Home,
)
from tinkerkit.teaching import (
    Asker,
    DocumentationPassages,
    Memory,
    PassageSource,
    ReadingPassages,
    Teacher,
    Verdict,
    marked,
    read_plan,
)
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
    return [principle.do for principle in Principles(None).live()]


@pytest.mark.spec("TCH-1")
def test_memory_is_what_tinker_keeps_but_the_retired_most_related_first(kept):
    kinds = {item["kind"] for item in kept.items}
    assert kinds == {"toolchain", "principle", "fact"}
    Knowledge("Python", "r2").disprove(
        {f"lesson:{kept.items[1]['id'].split(':')[1]}": ["toolchain"]}, ("toolchain",)
    )
    assert all(item["status"] != "retired" for item in Memory().items)
    first = kept.about("what is your current toolchain for python")[0]
    assert first["kind"] == "toolchain" and "black" in first["text"]
    assert kept.about("which principles do you have")[0]["kind"] == "principle"
    assert kept.about("what is the current toolset you use for a python project")[0]["kind"] == "toolchain"
    assert len(kept.about("anything", limit=2)) == 2 and MEMORY_ITEMS_SHOWN >= 20


@pytest.mark.spec("TCH-1")
def test_a_rule_given_brings_the_facts_it_cites_right_after_it_within_the_limit(kept):
    entry = Library.read("flask")
    entry["rules"] = [{"phase": "code", "text": "Route each page", "cites": [entry["facts"][0]["id"]]}]
    Library.save("flask", entry)
    memory = Memory()
    given = [item["kind"] for item in memory.about("which rules do you have")]
    assert given[:2] == ["rule", "fact"] and given.count("fact") == 1
    assert [item["kind"] for item in memory.about("which rules do you have", limit=2)] == ["rule", "fact"]
    assert len(memory.about("which rules do you have", limit=1)) == 1


@pytest.mark.spec("TCH-1", "PRI-9")
def test_an_item_of_a_topic_the_text_does_not_name_is_not_shown(kept):
    asked = "how do flask routes work with black"
    assert {item["group"] for item in kept.about(asked)} == {"flask"}
    assert {item["group"] for item in kept.about("which lessons do you have")} >= {"python"}


@pytest.mark.spec("TCH-1", "PRI-9")
def test_about_makes_no_embedding_call(kept, monkeypatch):
    embedded = []

    def embed(path, payload=None, timeout=10, errors=False):
        embedded.extend(payload["input"])
        return {"embeddings": [[float(len(text)), 1.0] for text in payload["input"]]}

    monkeypatch.setattr(Ollama, "get_json", staticmethod(embed))
    memory = Memory(Embeddings("e"))
    memory.about("what is your current toolchain for python")
    memory.about("how do flask routes work with black")
    assert embedded == []


@pytest.mark.spec("TCH-1")
def test_memory_holds_the_live_principles_in_english(kept):
    principles = Principles(None)
    formatting = Principle.new("formatting python", "run ruff format", scope="python", because="it is fast")
    principles.put(formatting)
    gone = Principle.new("linting python", "run pylint", scope="python")
    gone.retire("contradicted", "r3")
    principles.put(gone)
    memory = Memory()
    put_ids = {f"principle:{formatting.id}", f"principle:{gone.id}"}
    kept_principles = [item for item in memory.items if item["id"] in put_ids]
    assert [(item["group"], item["text"]) for item in kept_principles] == [
        ("python", "When formatting python, run ruff format, because it is fast.")
    ]
    assert memory.about("which principles do you keep for python")[0]["kind"] == "principle"


@pytest.mark.spec("TCH-2")
def test_ask_says_the_answer_alone(kept, make_model, fake_ollama, capsys):
    answer = "Tests run with pytest.\nCode is formatted with black and linted with ruff."
    fake_ollama.reply(first_reply(answer, uses=[1]), when="ask")
    ending = Asker(make_model(), kept).answer("what is your current toolchain for python")
    asked = fake_ollama.calls[0]["messages"]
    assert (
        asked[0]["content"].startswith(Prompts.ASK_SYSTEM)
        and "[1] toolchain (python, proven)" in asked[1]["content"]
    )
    assert '"uses"' in Prompts.ASK_SYSTEM and "Not from what I learned" not in Prompts.ASK_SYSTEM
    assert capsys.readouterr().out == answer + "\n"
    assert ending == {"status": "done", "headline": "Answered", "details": [("Answer", answer)], "next": []}
    unchanged = "black ."
    assert Home.store().get("languages", "python")["toolchain"]["format"] == unchanged


@pytest.mark.spec("TCH-2")
def test_ask_with_nothing_kept_still_asks_and_marks_own_knowledge(
    home_store, make_model, fake_ollama, capsys
):
    fake_ollama.reply(first_reply("Python is a programming language."), when="ask")
    Asker(make_model(), Memory()).answer("what is python")
    asked = fake_ollama.calls[0]["messages"]
    assert "What Tinker keeps" in asked[1]["content"] and "(nothing)" in asked[1]["content"]
    assert "first" in Prompts.ASK_SYSTEM
    assert capsys.readouterr().out == "Not from what I learned: Python is a programming language.\n"


@pytest.mark.spec("TCH-2")
def test_ask_marks_an_answer_that_uses_no_number_it_was_given(kept, make_model, fake_ollama, capsys):
    fake_ollama.reply(first_reply("Use pytest.", uses=[99, "1"]), when="ask")
    Asker(make_model(), kept).answer("what is your current toolchain for python")
    assert capsys.readouterr().out == "Not from what I learned: Use pytest.\n"


@pytest.mark.spec("TCH-2")
def test_ask_shows_the_version_installed_here_of_each_topic_shown(kept, make_model, fake_ollama):
    entry = Library.read("flask")
    entry["machine"] = [
        {"command": "flask --version", "found": True, "output": "Flask 3.0.2"},
        {"command": "gunicorn --version", "found": False, "output": ""},
    ]
    Library.save("flask", entry)
    Library.save(
        "django",
        {
            "topic": "django",
            "facts": [{"phase": "code", "text": "Django routes live in urls.py"}],
            "machine": [{"command": "django-admin --version", "found": True, "output": "5.0"}],
        },
    )
    fake_ollama.reply("With @app.route in Flask 3.0.2.", when="ask")
    Asker(make_model(), Memory()).answer("how do flask routes work")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "Installed here" in prompt
    assert "flask: `flask --version`: Flask 3.0.2\n" in prompt
    assert "gunicorn" not in prompt and "django" not in prompt
    assert "version" in Prompts.ASK_SYSTEM and "installed here" in Prompts.ASK_SYSTEM


@pytest.mark.spec("TCH-2")
def test_ask_says_a_topic_with_no_command_found_here_is_not_installed(kept, make_model, fake_ollama):
    entry = Library.read("flask")
    entry["machine"] = [{"command": "flask --version", "found": False, "output": ""}]
    Library.save("flask", entry)
    fake_ollama.reply("With @app.route.", when="ask")
    Asker(make_model(), Memory()).answer("how do flask routes work")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "## Installed here\nflask: not installed\n" in prompt and "flask --version" not in prompt


@pytest.mark.spec("TCH-2")
def test_ask_shows_at_most_the_ask_items(home_store, make_model, fake_ollama):
    facts = [{"phase": "code", "text": f"Flask fact {number}"} for number in range(ASK_ITEMS_SHOWN + 5)]
    Library.save("flask", {"topic": "flask", "facts": facts})
    fake_ollama.reply("With @app.route.", when="ask")
    Asker(make_model(), Memory()).answer("flask fact")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert f"[{ASK_ITEMS_SHOWN}] fact" in prompt and f"[{ASK_ITEMS_SHOWN + 1}]" not in prompt
    assert ASK_ITEMS_SHOWN == 6 < MEMORY_ITEMS_SHOWN


@pytest.mark.spec("TCH-2", "PRI-10")
def test_ask_shows_a_principle_as_its_prompt_text_and_cuts_each_item(home_store, make_model, fake_ollama):
    long_fact = "Flask routes use @app.route " + "and more " * 60
    Library.save("flask", {"topic": "flask", "facts": [{"phase": "code", "text": long_fact}]})
    Principles(None).put(
        Principle.new("routing in flask", "use @app.route", scope="flask", because="it is plain")
    )
    fake_ollama.reply("With @app.route.", when="ask")
    Asker(make_model(), Memory()).answer("how do flask routes work")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "routing in flask → use @app.route" in prompt and "because it is plain" not in prompt
    assert long_fact[:ASK_ITEM_CHARS] in prompt and long_fact[: ASK_ITEM_CHARS + 1] not in prompt
    assert ASK_ITEM_CHARS == 240


class FakeSandbox:
    def __init__(self, ran=None):
        self.runs, self.ran = [], ran or Ran(True, code=0, output="Flask 3.0.2")

    def run(self, command, folder, timeout=None):
        self.runs.append(command)
        return self.ran


def first_reply(answer, check=None, uses=()):
    return json.dumps({"answer": answer, "uses": list(uses), "check": check})


def flask_found_here():
    entry = Library.read("flask")
    entry["machine"] = [{"command": "python3 --version", "found": True, "output": "Python 3.12.1"}]
    Library.save("flask", entry)


@pytest.mark.spec("TCH-10")
def test_an_ask_without_a_check_makes_one_call(kept, make_model, fake_ollama, capsys):
    fake_ollama.reply(first_reply("Tests run with pytest.", uses=[1]), when="ask")
    ending = Asker(make_model(), kept).answer("what is your current toolchain for python")
    assert len(fake_ollama.calls) == 1 and "check" in Prompts.ASK_SYSTEM
    assert capsys.readouterr().out == "Tests run with pytest.\n"
    assert ending["details"] == [("Answer", "Tests run with pytest.")]


@pytest.mark.spec("TCH-10")
def test_an_ask_runs_its_sql_check_and_answers_with_what_it_printed(
    kept, make_model, fake_ollama, capsys, monkeypatch
):
    monkeypatch.setattr(Sandbox, "here", classmethod(lambda cls: pytest.fail("a sql check needs no sandbox")))
    check = {"kind": "sql", "code": "select 40 + 2", "shows": "what 40 + 2 gives"}
    answered = "I ran select 40 + 2, it printed 42."
    reply = first_reply("It gives 42.", check)
    fake_ollama.reply(reply, when="ask")
    fake_ollama.reply(json.dumps({"answer": answered, "uses": [1]}), when="ask")
    ending = Asker(make_model(), kept).answer("what does sqlite give for 40 + 2")
    first, second = (call["messages"] for call in fake_ollama.calls)
    assert second[:2] == first and first[0]["content"].startswith(Prompts.ASK_SYSTEM)
    assert second[2] == {"role": "assistant", "content": reply} and len(second) == 4
    assert "The check Tinker ran" in second[3]["content"] and "42" in second[3]["content"]
    assert "select 40 + 2" in second[3]["content"] and "The question" not in second[3]["content"]
    out = capsys.readouterr().out
    assert "Ran a sql check" in out and answered in out
    assert ending["details"] == [("Answer", answered)]


@pytest.mark.spec("TCH-2")
def test_a_checked_answer_that_uses_no_number_given_is_marked(kept, make_model, fake_ollama):
    ending = checked_ask(kept, make_model, fake_ollama, checked_reply(confirms=False, uses=[]))
    assert ending["details"] == [("Answer", f"Not from what I learned: {CHECKED_ANSWER}")]


@pytest.mark.spec("TCH-10")
def test_an_ask_runs_a_command_check_with_a_tool_found_here(kept, make_model, fake_ollama):
    flask_found_here()
    check = {"kind": "command", "code": "import flask", "shows": "flask imports", "tool": "python3"}
    fake_ollama.reply(first_reply("It imports.", check), when="ask")
    fake_ollama.reply(
        json.dumps({"answer": "I ran import flask, it printed Flask 3.0.2.", "uses": []}), when="ask"
    )
    sandbox = FakeSandbox()
    Asker(make_model(), Memory(), sandbox=sandbox).answer("how do flask routes work")
    assert len(sandbox.runs) == 1 and "Flask 3.0.2" in fake_ollama.calls[1]["messages"][3]["content"]


@pytest.mark.spec("TCH-10")
def test_a_refused_check_is_told_once_and_the_fixed_check_runs_instead(kept, make_model, fake_ollama, capsys):
    flask_found_here()
    refused = {"kind": "command", "code": "flask", "shows": "it installs", "tool": "pip"}
    fixed = {"kind": "command", "code": "import flask", "shows": "flask imports", "tool": "python3"}
    answered = "I ran import flask, it printed Flask 3.0.2."
    fake_ollama.reply(first_reply("It imports.", refused, [1]), when="ask")
    fake_ollama.reply(json.dumps({"check": fixed}), when="ask")
    fake_ollama.reply(json.dumps({"answer": answered, "uses": [1]}), when="ask")
    sandbox = FakeSandbox()
    ending = Asker(make_model(), Memory(), sandbox=sandbox).answer("how do flask routes work")
    first, retry, checked = (call["messages"] for call in fake_ollama.calls)
    assert retry[:2] == first and len(retry) == 4 and Prompts.ASK_CHECK_AGAIN in retry[3]["content"]
    assert "refused" in retry[3]["content"] and "The question" not in retry[3]["content"]
    assert checked[:4] == retry and len(checked) == 6 and "Flask 3.0.2" in checked[5]["content"]
    assert len(sandbox.runs) == 1 and "Check refused" in capsys.readouterr().out
    assert ending["details"] == [("Answer", answered)]


@pytest.mark.spec("TCH-10")
def test_a_check_refused_again_is_said_and_the_first_answer_goes_on(kept, make_model, fake_ollama, capsys):
    flask_found_here()
    check = {"kind": "command", "code": "flask", "shows": "it installs", "tool": "pip"}
    fake_ollama.reply(first_reply("Install it with pip.", check, [1]), when="ask")
    fake_ollama.reply(json.dumps({"check": check}), when="ask")
    sandbox = FakeSandbox()
    ending = Asker(make_model(), Memory(), sandbox=sandbox).answer("how do flask routes work")
    out = capsys.readouterr().out
    assert len(fake_ollama.calls) == 2 and sandbox.runs == []
    assert out.count("Check refused") == 2 and "Install it with pip." in out
    assert ending["details"] == [("Answer", "Install it with pip.")]


@pytest.mark.spec("TCH-10")
def test_a_failed_check_with_no_fixed_one_is_said_and_the_first_answer_goes_on(
    kept, make_model, fake_ollama, capsys
):
    flask_found_here()
    check = {"kind": "command", "code": "import flask", "shows": "flask imports", "tool": "python3"}
    fake_ollama.reply(first_reply("It imports.", check), when="ask")
    fake_ollama.reply(json.dumps({"check": None}), when="ask")
    sandbox = FakeSandbox(Ran(False, why="no sandbox here"))
    Asker(make_model(), Memory(), sandbox=sandbox).answer("how do flask routes work")
    out = capsys.readouterr().out
    assert len(fake_ollama.calls) == 2 and "no sandbox here" in fake_ollama.calls[1]["messages"][3]["content"]
    assert "Check failed: no sandbox here" in out and "No fixed check" in out and "It imports." in out


@pytest.mark.spec("TCH-10")
def test_a_sql_check_whose_statement_errors_is_told_the_error_and_fixed(
    kept, make_model, fake_ollama, capsys
):
    flask_found_here()
    broken = {"kind": "sql", "code": "select * from missing", "shows": "the table"}
    fixed = {"kind": "sql", "code": "select 40 + 2", "shows": "what 40 + 2 gives"}
    fake_ollama.reply(first_reply("It gives 42.", broken), when="ask")
    fake_ollama.reply(json.dumps({"check": fixed}), when="ask")
    fake_ollama.reply(json.dumps({"answer": "It printed 42.", "uses": []}), when="ask")
    Asker(make_model(), Memory()).answer("how do flask routes work")
    retry = fake_ollama.calls[1]["messages"]
    assert "no such table: missing" in retry[3]["content"] and Prompts.ASK_CHECK_AGAIN in retry[3]["content"]
    assert "42" in fake_ollama.calls[2]["messages"][5]["content"]
    assert "Check failed" in capsys.readouterr().out


SQL_CHECK = {"kind": "sql", "code": "select 40 + 2", "shows": "what 40 + 2 gives"}
CHECKED_ANSWER = "I ran select 40 + 2, it printed 42."


def checked_reply(
    confirms=True, topic="flask", fact="SQLite adds integers exactly: 40 + 2 gives 42", uses=(1,)
):
    return json.dumps(
        {"answer": CHECKED_ANSWER, "uses": list(uses), "confirms": confirms, "fact": fact, "topic": topic}
    )


def checked_ask(kept, make_model, fake_ollama, reply):
    fake_ollama.reply(first_reply("It gives 42.", SQL_CHECK), when="ask")
    fake_ollama.reply(reply, when="ask")
    return Asker(make_model(), kept).answer("how do flask routes work")


def facts_of(slug):
    return Library.read(slug)["facts"]


@pytest.mark.spec("TCH-11")
def test_an_answer_the_check_confirmed_is_kept_as_a_candidate_fact(kept, make_model, fake_ollama, capsys):
    ending = checked_ask(kept, make_model, fake_ollama, checked_reply())
    fact = facts_of("flask")[-1]
    assert fact["text"] == "SQLite adds integers exactly: 40 + 2 gives 42" and fact["status"] == "candidate"
    assert [event["event"] for event in fact["history"]] == ["checked"]
    assert "select 40 + 2" in fact["sources"][0] and "42" in fact["sources"][1]
    kept_line = f"Fact kept for Flask: {fact['text']}"
    assert kept_line in capsys.readouterr().out
    assert ending["details"] == [("Answer", CHECKED_ANSWER), ("Kept", kept_line)]
    assert "confirms" in fake_ollama.calls[1]["messages"][-1]["content"]


@pytest.mark.spec("TCH-11")
def test_a_confirmed_answer_like_a_kept_fact_adds_to_it(kept, make_model, fake_ollama):
    checked_ask(kept, make_model, fake_ollama, checked_reply())
    entry = Library.read("flask")
    entry["facts"][-1]["status"] = "proven"
    Library.save("flask", entry)
    checked_ask(
        kept, make_model, fake_ollama, checked_reply(fact="SQLite adds integers exactly: 40 + 2 is 42")
    )
    [_, fact] = facts_of("flask")
    assert fact["status"] == "proven" and fact["text"].endswith("gives 42")
    assert [event["event"] for event in fact["history"]] == ["checked", "checked"]
    assert len(fact["sources"]) == 2


@pytest.mark.spec("TCH-11")
def test_an_answer_the_check_did_not_confirm_keeps_nothing(kept, make_model, fake_ollama):
    ending = checked_ask(kept, make_model, fake_ollama, checked_reply(confirms=False))
    assert len(facts_of("flask")) == 1 and ending["details"] == [("Answer", CHECKED_ANSWER)]


@pytest.mark.spec("TCH-11")
def test_a_plain_checked_reply_is_the_answer_marked_and_keeps_nothing(kept, make_model, fake_ollama):
    ending = checked_ask(kept, make_model, fake_ollama, CHECKED_ANSWER)
    assert len(facts_of("flask")) == 1
    assert ending["details"] == [("Answer", f"Not from what I learned: {CHECKED_ANSWER}")]


@pytest.mark.spec("TCH-11")
def test_a_confirmed_fact_with_no_topic_shown_is_said_and_not_kept(kept, make_model, fake_ollama, capsys):
    ending = checked_ask(kept, make_model, fake_ollama, checked_reply(topic="django"))
    assert "No book or topic to keep it in" in capsys.readouterr().out
    assert len(facts_of("flask")) == 1 and ending["details"] == [("Answer", CHECKED_ANSWER)]


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
    lesson = numbered_item(kept, teaching, "principle")
    fake_ollama.reply(
        plan_reply(changes=[dict(RUFF_CHANGE, retire=[lesson]), NO_MYPY_CHANGE]), when="what to check"
    )
    fake_ollama.reply(verdict_reply(), when="weigh the evidence")
    ending = Teacher(make_model(), FakeWeb(), kept).teach(teaching)
    python = Home.store().get("languages", "python")
    assert python["toolchain"]["format"] == "ruff format ." and python["toolchain"]["lint"] == "ruff check ."
    taught = {principle.do: principle for principle in Principles(None).live()}
    ruff, no_mypy = (
        taught["Format and lint Python with ruff alone; black is not needed"],
        taught["Run no type checker in Python projects"],
    )
    assert ruff.status == no_mypy.status == "proven" and ruff.history[-1]["event"] == "taught"
    sources = lambda principle: [item["source"] for item in principle.evidence if item["kind"] == "taught"]
    assert sources(ruff) == [PAGE["url"]] and sources(no_mypy) == ["your preference"]
    assert ending["status"] == "done" and ending["headline"] == "Corrected: 5 change(s)"
    kept_principles = {principle.do: principle for principle in Principles(None).all()}
    retired = kept_principles["Format Python with black before linting with ruff"]
    assert retired.status == "retired"
    assert retired.history[-1]["reason"] == f"corrected by what you taught: {teaching}"


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
    fake_ollama.reply(first_reply("Black formats it.", uses=[1]), when="ask")
    Commands(Cli.parse(["ask", "what", "formats", "python"])).run()
    fake_ollama.reply(plan_reply(), when="what to check")
    fake_ollama.reply(verdict_reply(), when="weigh the evidence")
    Commands(Cli.parse(["teach", "ruff", "alone", "is", "enough,", "I", "prefer", "no", "mypy"])).run()
    assert ended[0][:2] == ("Asking: what formats python", "done")
    out = capsys.readouterr().out
    assert out.startswith("Black formats it.") and out.count("Time taken") == 2
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
    fake_ollama.reply(first_reply("Chapter 1 says each test comes before the code.", uses=[2]), when="ask")
    Asker(make_model(), Memory()).answer("when does The Craft write each test?")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "## From what you read\n[2] The Craft, chapter 1: Testing\nWrite one test" in prompt
    assert WRONG in prompt and capsys.readouterr().out == "Chapter 1 says each test comes before the code.\n"


def learned_flask(texts):
    Library.save(
        "flask",
        {
            "topic": "flask",
            "name": "Flask",
            "summary": "A web framework.",
            "facts": [{"phase": "code", "text": "Flask routes use @app.route"}],
            "docs": {"flask": "latest"},
        },
    )
    blocks = [Block("heading", "Routing")] + [Block("text", text) for text in texts]
    DocCache.put("flask", "latest", [Page("https://flask.example/routing", "2020-01-01", blocks)])
    Home.store().update("sources", "flask@latest", lambda kept: dict(kept, kept="2020-01-01 10:00"))


@pytest.mark.spec("TCH-3")
def test_an_ask_about_a_learned_topic_sees_its_kept_documentation(home_store, make_model, fake_ollama):
    learned_flask(["The route decorator binds a view function to a URL rule."])
    fake_ollama.reply("With the route decorator.", when="ask")
    Asker(make_model(), Memory()).answer("how do flask routes bind a view function?")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert "[2] flask@latest, https://flask.example/routing > Routing\nThe route decorator binds" in prompt


@pytest.mark.spec("TCH-3")
def test_an_ask_gives_a_passage_kept_at_two_addresses_once(home_store, make_model, fake_ollama):
    learned_flask(["The route decorator binds a view function to a URL rule."])
    blocks = [
        Block("heading", "Routing"),
        Block("text", "The route decorator binds a view function to a URL rule. "),
    ]
    DocCache.put(
        "flask",
        "latest",
        DocCache.pages("flask", "latest")
        + [Page("https://mirror.flask.example/routing", "2020-01-01", blocks)],
    )
    fake_ollama.reply("With the route decorator.", when="ask")
    Asker(make_model(), Memory()).answer("how do flask routes bind a view function?")
    prompt = fake_ollama.calls[0]["messages"][1]["content"]
    assert prompt.count("The route decorator binds") == 1
    assert "[2] flask@latest, https://flask.example/routing > Routing" in prompt


@pytest.mark.spec("TCH-3")
def test_an_ask_embeds_only_the_passages_its_words_shortlist(
    home_store, make_model, fake_ollama, monkeypatch
):
    learned_flask([f"Passage {number}: a flask route binds a view. " + "x" * 1100 for number in range(40)])
    embedded = []

    def embed(path, payload=None, timeout=10, errors=False):
        embedded.extend(payload["input"])
        return {"embeddings": [[float(len(text)), 1.0] for text in payload["input"]]}

    monkeypatch.setattr(Ollama, "get_json", staticmethod(embed))
    fake_ollama.reply("With the route decorator.", when="ask")
    Asker(make_model(), Memory(Embeddings("e"))).answer("how does a flask route bind a view?")
    passages_embedded = [text for text in embedded if "Passage " in text]
    assert len(passages_embedded) == PassageSource.SHORTLIST_TIMES * BOOK_PASSAGES_SHOWN


@pytest.mark.spec("TCH-3")
def test_an_ask_takes_its_passages_in_turn_from_each_sources_best(home_store, monkeypatch):
    given = lambda name, texts: [{"source": name, "text": text} for text in texts]
    readings = given("book", ["same", "book 2", "book 3", "book 4"])
    docs = given("docs", ["same", "docs 2", "docs 3", "docs 4"])
    monkeypatch.setattr(ReadingPassages, "passages", lambda self, question, items: readings)
    monkeypatch.setattr(DocumentationPassages, "passages", lambda self, question, items: docs)
    _, passages = Asker(None, Memory()).recalled("anything")
    assert passages == [readings[0], readings[1], docs[1]] and len(passages) == ASK_PASSAGES_SHOWN


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
