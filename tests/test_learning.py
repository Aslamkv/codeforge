import hashlib
import json
import re

import pytest
from conftest import FakeSearch, quick_model_replying, web_page

from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.learning import (
    DocCache,
    LearnStopped,
    SlotLearner,
    read_digest,
    read_plan,
    wanted_docs,
)
from tinkerkit.model import Answer
from tinkerkit.passages import Block, Page, Passage
from tinkerkit.project import Tracker
from tinkerkit.prompts import Reply
from tinkerkit.settings import (
    DOCS_SITE_SEARCH,
    DOCS_VERSION_SEARCH,
    FACTS_PER_SLOT,
    MAX_PLANNED_SEARCHES,
    RESULTS_PER_LEARN_SEARCH,
    SLOT_CANDIDATE_PASSAGES,
    Config,
    Home,
)
from tinkerkit.slots import PROFILES, Selector, missing, short_slots, web_slots
from tinkerkit.web import RequestLog, WebPage


class FakeWeb(FakeSearch):
    def __init__(self, found=None, pages=None):
        super().__init__()
        self.found, self.pages = found or {}, pages or {}
        self.queries, self.fetched, self.requests = [], [], RequestLog()

    def search(self, query, count):
        self.queries.append(query)
        self.requests("search", "mwmbl", "", 0.01, query)
        return list(self.found.get(query, []))[:count]

    def fetch(self, urls, max_bytes=None):
        self.fetched += urls
        return [self.page(url) for url in urls]

    def page(self, url):
        why = "" if url in self.pages else "HTTP 404"
        self.requests("fetch", "fetcher", why, 0.01, url)
        if why:
            return WebPage.failed(url, why)
        return web_page(url, self.pages[url], "text/html")


class CountingModel:
    name = "fake-model"

    def __init__(self, replies=None):
        self.replies, self.calls, self.thinking = replies or {}, [], []

    def limit_chars(self, system, tokens=None):
        return 8000

    def ask_until_usable(self, system, parts, label, read, attempts=3, think=None, **settings):
        self.calls.append(label)
        self.thinking.append((label, think))
        answer = next(reply for start, reply in self.replies.items() if label.startswith(start))
        try:
            return Answer(read(Reply(json.dumps(answer(parts) if callable(answer) else answer))))
        except ValueError as exc:
            return Answer(None, why=f"no usable reply after 1 attempt(s): {exc}")


class CountingEmbeddings:
    name, failed = "fake-embed", ""

    def __init__(self):
        self.calls = 0

    def vectors(self, texts, keep=True, prune=False):
        self.calls += 1
        return [
            [
                1.0 if bit == "1" else -1.0
                for bit in bin(int(hashlib.sha1(text.encode()).hexdigest(), 16))[2:66]
            ]
            for text in texts
        ]


def result(url, title, snippet=""):
    return {"title": title, "url": url, "content": snippet}


def html(*sections):
    return (
        "<html><body><main>"
        + "".join(f"<h2>{heading}</h2><p>{(paragraph + ' ') * 10}</p>" for heading, paragraph in sections)
        + "</main></body></html>"
    )


START = "https://widgetlib.dev/start"
CHANGES = "https://widgetlib.dev/changes"
APP = "https://widgetlib.dev/app"
OFF_TOPIC = result("https://cooking.example/soup", "Soup recipes", "How to cook a soup")
RESULTS = [
    result(START, "Widgetlib getting started"),
    result(START + "#install", "Widgetlib install"),
    OFF_TOPIC,
    result(CHANGES, "Widgetlib changelog"),
    result(APP, "Widgetlib app"),
]
DRAWN_BY_ITS_SCRIPTS = "<html><body><div id='app'></div><script>render()</script></body></html>"
PAGES = {
    START: html(
        ("Overview", "Widgetlib overview: its purpose is drawing widgets in a window from code."),
        ("Getting started", "This tutorial shows the API: create a Widget, then call show on it."),
        ("Tutorial API reference", "The API has Widget, Layout and Theme; the tutorial covers each."),
        ("Project structure", "Keep widgets in a widgets folder; configuration lives in widgetlib.toml."),
        ("Testing", "Install widgetlib in the test environment, then run testing with the headless mode."),
    ),
    CHANGES: html(
        ("Changelog", "Version 2.1 deprecations: Window.draw is replaced by Window.render."),
        (
            "Common mistakes",
            "A common mistake is drawing outside the event loop; limitations include one window.",
        ),
    ),
    APP: DRAWN_BY_ITS_SCRIPTS,
}
FIRST_SEARCH = "Widgetlib official documentation"
PITFALLS_SEARCH = {"query": " widgetlib  common mistakes", "engines": ["wikibooks"], "count": 4}
VERSION_DOCS_SEARCH = "Widgetlib 2.1.0 documentation"
PLAN = {
    "kind": "tool",
    "name": "Widgetlib",
    "version": "2.0",
    "summary": "A widget library.",
    "language": "Python",
    "commands": ["echo widgetlib 2.1.0", "pip install widgetlib"],
    "hints": {"core-usage": "the Widget class"},
    "followups": {"pitfalls-and-limits": PITFALLS_SEARCH, "unknown-slot": "x"},
}
DOC_ROOT = "https://widgetlib.dev/docs/2.1/"
ELSEWHERE = "https://elsewhere.org/x"
DOC_PAGES = {
    DOC_ROOT + "quickstart.html": html(
        ("Getting started", "This tutorial shows the API: create a Widget, then show it.")
    ),
    DOC_ROOT + "changelog.html": html(
        ("Changelog 2.1", "Widgetlib 2.1 deprecations: Window.draw is now Window.render.")
    ),
    DOC_ROOT + "configuration.html": html(
        (
            "Configuration",
            "Project structure: widgets in a widgets folder; configuration "
            "lives in widgetlib.toml, as best practices advise.",
        )
    ),
    DOC_ROOT + "install.html": html(
        ("Install", "Install widgetlib with pip, then run testing in the headless mode.")
    ),
    ELSEWHERE: html(("Gardening", "Gardening is fun in the spring when the soil is warm.")),
}
DOC_RESULTS = [
    result(DOC_ROOT + "quickstart.html", "Widgetlib 2.1 quickstart"),
    result(DOC_ROOT + "changelog.html", "Widgetlib 2.1 changelog"),
    result(DOC_ROOT + "configuration.html", "Widgetlib 2.1 configuration"),
    result(DOC_ROOT + "install.html", "Widgetlib 2.1 install"),
    result(ELSEWHERE, "Gardening in spring"),
]
FOUND = {FIRST_SEARCH: RESULTS, VERSION_DOCS_SEARCH: DOC_RESULTS}
ALL_PAGES = {**PAGES, **DOC_PAGES}


def digest(parts):
    passages = next(part.text for part in parts if part.title == "Passages")
    question = next(part.text for part in parts if part.title == "Question")
    first = re.search(r"\[(P\d+)\]", passages).group(1)
    return {
        "facts": [
            {"phase": "code", "text": f"Fact for: {question[:40]}", "cites": [first]},
            {"phase": "code", "text": "Uncited claim", "cites": ["P999"]},
        ]
    }


def tool_model():
    return CountingModel(
        {
            "Plan learning": PLAN,
            "Digest": digest,
            "Condense": {"rules": [{"phase": "code", "text": "Create a Widget, then show it", "cites": [1]}]},
        }
    )


def learner(web, model=None, embeddings="default"):
    return SlotLearner(
        model or tool_model(), web, CountingEmbeddings() if embeddings == "default" else embeddings
    )


@pytest.mark.spec("SLT-1")
def test_profiles_are_data_and_a_slot_takes_the_plans_hint_and_follow_up():
    assert [slot.id for slot in PROFILES["language"]] == [
        "installed",
        "toolchain",
        "fundamentals",
        "this-version",
        "limitations",
        "best-use-cases",
        "readiness",
    ]
    assert [slot.id for slot in PROFILES["tool"]] == [
        "installed",
        "purpose",
        "core-usage",
        "this-version",
        "pitfalls-and-limits",
        "conventions",
        "fit",
        "readiness",
    ]
    assert [slot.source for slot in PROFILES["tool"]][::7] == ["machine", "check"]
    plan = read_plan(PLAN)
    core, this_version = (
        next(slot for slot in web_slots("tool") if slot.id == key) for key in ("core-usage", "this-version")
    )
    assert core.asked(plan, "2.1").endswith("(look for: the Widget class)") and "Widgetlib" in core.asked(
        plan, "2.1"
    )
    its_own_search = "Widgetlib 2.1 changelog deprecations"
    assert this_version.search(plan, "2.1") == {"query": its_own_search}
    pitfalls = next(slot for slot in web_slots("tool") if slot.id == "pitfalls-and-limits")
    assert pitfalls.search(plan, "2.1") == dict(PITFALLS_SEARCH, query="widgetlib common mistakes")
    assert (
        core.search(plan, "2.1") == {"query": "Widgetlib getting started tutorial API"}
        and core.keywords() == "getting started tutorial API"
    )


@pytest.mark.spec("LRN-1")
def test_a_copy_of_the_latest_documentation_is_read_again_after_docs_latest_days(tinker_home):
    page = Page("https://x.dev/", "t", [Block("text", "Overview")])
    DocCache.put("x", "latest", [page])
    assert DocCache.get("x", "latest")[0].blocks == page.blocks
    Home.store().update("sources", "x@latest", lambda kept: dict(kept, kept="2020-01-01 10:00"))
    assert DocCache.get("x", "latest") is None
    DocCache.put("x", "1.0", [page])
    Home.store().update("sources", "x@1.0", lambda kept: dict(kept, kept="2020-01-01 10:00"))
    a_versions_own_documentation = DocCache.get("x", "1.0")
    assert a_versions_own_documentation is not None
    Config.save(docs_latest_days=100000)
    assert DocCache.get("x", "latest") is not None


def passage(number, text, url="https://x.dev/doc"):
    return Passage(url, "t", "", text, f"P{number}")


TOOLCHAIN = next(slot for slot in web_slots("language") if slot.id == "toolchain")
FUNDAMENTALS = next(slot for slot in web_slots("language") if slot.id == "fundamentals")
LANGUAGE = {"name": "Lang", "language": "Lang"}


@pytest.mark.spec("SLT-2")
def test_each_slot_draws_from_one_pool_and_mmr_passes_over_a_near_repeat():
    test_runner = "The test framework runs every test file; the formatter and linter run before each commit."
    pool = [
        passage(1, test_runner),
        passage(2, test_runner + " Always."),
        passage(3, "The package manager installs commands and the linter."),
        passage(4, "Keywords and syntax: basic types are int and str; the linter checks them."),
        passage(5, "Gardening is fun."),
    ]
    picked = Selector(None, 10_000).select([TOOLCHAIN, FUNDAMENTALS], pool, LANGUAGE, "1.0")
    toolchain = [passage.id for passage in picked["toolchain"]]
    the_near_repeat_waits = toolchain.index("P3") < 2
    assert "P5" not in toolchain and set(toolchain[:2]) & {"P1", "P2"} and the_near_repeat_waits
    first_fundamental = [passage.id for passage in picked["fundamentals"]][0]
    assert first_fundamental == "P4" and first_fundamental in toolchain
    digest_room = len(pool[0].listing()) + 2
    tight = Selector(None, digest_room).select([TOOLCHAIN], pool, LANGUAGE, "1.0")
    assert len(tight["toolchain"]) == 1


@pytest.mark.spec("SLT-2")
def test_meaning_finds_a_passage_with_no_shared_keyword():
    class Close(CountingEmbeddings):
        def vectors(self, texts, keep=True, prune=False):
            self.calls += 1
            close, far = [1.0, 0.0], [0.0, 1.0]
            return [close if "check" in text or "test" in text else far for text in texts]

    pool = [passage(1, "Its unit checks run with one command."), passage(2, "Gardening is fun.")]
    picked = Selector(Close(), 10_000).select([TOOLCHAIN], pool, LANGUAGE, "1.0")
    assert [passage.id for passage in picked["toolchain"]] == ["P1"]


@pytest.mark.spec("SLT-2")
def test_only_each_slots_shortlist_is_embedded():
    class Seen(CountingEmbeddings):
        def vectors(self, texts, keep=True, prune=False):
            self.seen = getattr(self, "seen", 0) + len(texts)
            return super().vectors(texts, keep, prune)

    pool = [passage(number, f"The linter number {number} checks the code.") for number in range(1, 400)]
    embeddings = Seen()
    Selector(embeddings, 100_000).select([TOOLCHAIN], pool, LANGUAGE, "1.0")
    the_question = 1
    assert embeddings.seen == SLOT_CANDIDATE_PASSAGES + the_question


@pytest.mark.spec("SLT-3")
def test_short_slots_and_readiness():
    selected = {
        "toolchain": [passage(1, "a")],
        "fundamentals": [],
        "this-version": [passage(2, "b")] * 2,
        "limitations": [passage(3, "c")],
        "best-use-cases": [passage(4, "d")],
    }
    emptiest_first = ["fundamentals", "toolchain"]
    assert [slot.id for slot in short_slots("language", selected)] == emptiest_first
    facts = [{"slot": "toolchain"}, {"slot": "fundamentals"}]
    assert missing("language", facts, machine=[]) == [
        "installed",
        "this-version",
        "limitations",
        "best-use-cases",
    ]
    assert "installed" not in missing("language", facts, machine=[{"command": "x", "found": False}])


@pytest.mark.spec("LRN-2")
def test_read_plan_checks_the_plan():
    plan = read_plan(PLAN)
    assert (plan["kind"], plan["profile"], plan["language"], plan["used_with"]) == (
        "tool",
        "tool",
        "",
        "Python",
    )
    assert plan["commands"] == ["echo widgetlib 2.1.0"] and plan["dropped"] == ["pip install widgetlib"]
    assert plan["followups"] == {
        "pitfalls-and-limits": dict(PITFALLS_SEARCH, query="widgetlib common mistakes")
    }
    assert plan["tools"] == []
    assert "docs" not in plan
    language = read_plan(
        {
            "kind": "language",
            "name": "Lang",
            "tools": [
                {"role": "tests", "name": "langtest", "command": "langtest --version"},
                {"role": "lint", "name": "langlint", "command": "langlint --version; rm x"},
                {"role": "tests", "name": "other"},
                {"role": "magic", "name": "x"},
                "junk",
            ],
            "hints": {"toolchain": "langtest", "purpose": "a tool's slot"},
        }
    )
    assert language["language"] == "Lang"
    assert language["tools"] == [
        {"role": "tests", "name": "langtest", "command": "langtest --version"},
        {"role": "lint", "name": "langlint", "command": ""},
    ]
    assert language["dropped"] == ["langlint --version; rm x"]
    assert (
        language["hints"] == {"toolchain": "langtest"}
        and read_plan({"kind": "domain", "name": "D"})["profile"] == "tool"
    )
    for bad in ({"kind": "band", "name": "X"}, {"kind": "tool", "name": ""}, ["not", "an", "object"]):
        with pytest.raises(ValueError):
            read_plan(bad)


@pytest.mark.spec("LRN-2")
def test_read_digest_keeps_only_facts_that_cite_the_passages_given():
    slot, given = TOOLCHAIN, [passage(1, "a", "https://a.dev"), passage(2, "b", "https://b.dev")]
    facts = read_digest(
        {
            "facts": [
                {"phase": "tests", "text": "Run  tests\nwith langtest", "cites": ["P1", "[P2]", "P1"]},
                {"phase": "nowhere", "text": "Format with langfmt", "cites": ["P2"]},
                {"phase": "code", "text": "Trust me", "cites": []},
                {"phase": "code", "text": "Made up", "cites": ["P9"]},
                {"phase": "code", "text": "Lang 9 is in development", "cites": ["P1"]},
                "junk",
            ]
            + [{"text": f"fact {number}", "cites": ["P1"]} for number in range(10)]
        },
        slot,
        given,
    )
    assert facts[0] == {
        "phase": "tests",
        "text": "Run tests with langtest",
        "slot": "toolchain",
        "passages": ["P1", "P2"],
        "source": "https://a.dev",
        "also": ["https://b.dev"],
    }
    the_slots_section = "toolchain"
    assert facts[1]["phase"] == the_slots_section and len(facts) == FACTS_PER_SLOT


WEB_AND_WARNING_BY_CAUSE = {
    "no search answered": (dict(), "the search found nothing"),
    "no relevant results": (
        dict(found={FIRST_SEARCH: [OFF_TOPIC]}),
        "no search result is about the topic",
        "Next: learn again later, or name the topic differently if it is ambiguous",
    ),
    "too few pages": (
        dict(found={FIRST_SEARCH: RESULTS}, pages={START: PAGES[START]}),
        "too few pages could be fetched",
    ),
}


@pytest.mark.spec("LRN-3")
@pytest.mark.parametrize("cause", WEB_AND_WARNING_BY_CAUSE)
def test_a_learn_the_web_cannot_feed_stops_before_any_model_call(cause, tinker_home):
    web_settings, *warnings = WEB_AND_WARNING_BY_CAUSE[cause]
    model, embeddings = tool_model(), CountingEmbeddings()
    slot_learner = SlotLearner(model, FakeWeb(**web_settings), embeddings)
    with pytest.raises(LearnStopped) as stopped:
        slot_learner.learn("Widgetlib")
    message = str(stopped.value)
    exit_code = stopped.value.code
    assert isinstance(stopped.value, SystemExit) and exit_code
    assert all(warning in message for warning in warnings)
    assert "Nothing was learned or saved" in message and "Next: " in message
    assert "Web requests: search 1" in message and "Tried: " in message
    assert model.calls == [] and embeddings.calls == 0
    assert all(Home.store().items(part) == {} for part in ("learned", "embeddings", "languages", "memory"))


@pytest.mark.spec("LRN-3")
def test_without_an_embedding_model_nothing_goes_online(tinker_home):
    web = FakeWeb(FOUND, ALL_PAGES)
    with pytest.raises(LearnStopped, match="no embedding model"):
        learner(web, embeddings=None).learn("Widgetlib")
    assert web.queries == [] and web.fetched == []


@pytest.mark.spec("LRN-3")
def test_a_stopped_learn_leaves_what_was_learned_before_untouched(tinker_home):
    Library.save(
        "widgetlib",
        {
            "topic": "Widgetlib",
            "kind": "tool",
            "name": "Widgetlib",
            "summary": "s",
            "learned": "2026-01-01 10:00",
            "facts": [{"phase": "code", "text": "old fact"}],
        },
    )
    before = Library.read("widgetlib")
    with pytest.raises(LearnStopped, match=r"learned about it before \(2026-01-01 10:00\) is untouched"):
        learner(FakeWeb()).learn("Widgetlib")
    assert Library.read("widgetlib") == before


@pytest.mark.spec("LRN-4", "LRN-1")
def test_a_tool_is_learned_from_one_search_and_its_versions_documentation(tinker_home, capsys):
    web, model, embeddings = FakeWeb(FOUND, ALL_PAGES), tool_model(), CountingEmbeddings()
    entry = SlotLearner(model, web, embeddings).learn("Widgetlib")
    assert web.queries[:2] == [FIRST_SEARCH, VERSION_DOCS_SEARCH]
    without_off_topic_and_repeat = [START, CHANGES, APP]
    assert web.fetched[:3] == without_off_topic_and_repeat
    assert all(url in web.fetched for url in DOC_PAGES if url != ELSEWHERE) and ELSEWHERE not in web.fetched
    assert entry["docs"] == {"widgetlib": "2.1.0"} and Home.store().get("sources", "widgetlib@2.1.0")["pages"]
    assert model.calls[0] == "Plan learning" and model.calls[-1] == "Condense the facts" and embeddings.calls
    assert entry["kind"] == "tool" and entry["machine"][0]["version"] == "2.1.0" and entry["language"] == ""
    assert any(fact["source"].startswith(DOC_ROOT) for fact in entry["facts"])
    assert all(
        fact["passages"] and fact["source"].startswith("https://widgetlib.dev") for fact in entry["facts"]
    )
    assert "Uncited claim" not in [fact["text"] for fact in entry["facts"]]
    assert all(passage["fetched"] for passage in entry["passages"])
    assert [rule["text"] for rule in entry["rules"]] == ["Create a Widget, then show it"]
    assert entry["requests"]["search"]["requests"] == len(web.queries)
    assert (
        entry["status"] == ("incomplete" if entry["missing"] else "complete")
        and "readiness" not in entry["missing"]
    )
    saved = Library.read("widgetlib")
    assert "docs" not in saved["plan"] and all(fact["status"] == "candidate" for fact in saved["facts"])
    out = capsys.readouterr().out
    assert "Documentation: widgetlib 2.1.0" in out and f"Web requests: search {len(web.queries)}" in out
    assert "Pages: 2 searched, 1 failed, 4 docs" in out and f"Facts kept: {len(entry['facts'])}" in out


def quick_model_planning(monkeypatch, plans):
    def reply(messages):
        need = next(need for need in plans if need in messages[1]["content"])
        return json.dumps({"searches": plans[need]})

    return quick_model_replying(monkeypatch, reply)


@pytest.mark.spec("WEB-3", "LRN-4")
def test_more_planned_searches_than_one_ask_keeps_each_needs_results_its_own():
    plans = [[{"query": f"need {need} search {number}"} for number in range(4)] for need in range(3)]
    plans[0].insert(1, {"query": ""})
    found = {
        search["query"]: [result(f"https://x.dev/{search['query']}", "t")]
        for plan in plans
        for search in plan
    }
    web = FakeWeb(found)
    results = learner(web).searched_needs(plans)
    assert [len(batch) for batch in web.entries] == [MAX_PLANNED_SEARCHES, 4]
    assert results == [[found[f"need {need} search {number}"][0] for number in range(4)] for need in range(3)]


@pytest.mark.spec("LRN-1", "KNW-25")
def test_kept_documentation_is_not_planned_or_searched(tinker_home, monkeypatch):
    asked = quick_model_planning(monkeypatch, {DOCS_VERSION_SEARCH: [{"query": "{name} {version} docs"}]})
    page = Page("https://widgetlib.dev/docs/", "t", [Block("text", "Overview")])
    DocCache.put("widgetlib", "2.1.0", [page])
    web = FakeWeb()
    kept = wanted_docs("widgetlib", "Widgetlib", "2.1.0", DOCS_VERSION_SEARCH, 4)
    [pages] = learner(web).doc_pages([kept])
    assert [kept_page.blocks for kept_page in pages] == [page.blocks]
    assert asked == [] and web.entries == []


@pytest.mark.spec("LRN-4", "KNW-25")
def test_a_learns_documentation_needs_are_planned_and_each_pools_its_searches(tinker_home, monkeypatch):
    asked = quick_model_planning(
        monkeypatch,
        {
            DOCS_SITE_SEARCH: [{"query": "{topic} guide", "engines": ["wikibooks"], "count": 3}],
            DOCS_VERSION_SEARCH: [
                {"query": "{name}  {version} docs", "engines": ["mwmbl"], "count": 4},
                {"query": "{name} {version} changelog"},
            ],
        },
    )
    found = {
        FIRST_SEARCH: RESULTS,
        "Widgetlib 2.1.0 docs": DOC_RESULTS[:2],
        "Widgetlib 2.1.0 changelog": DOC_RESULTS[2:],
    }
    web = FakeWeb(found, ALL_PAGES)
    learner(web).learn("Widgetlib")
    assert web.entries[:2] == [
        [{"query": FIRST_SEARCH, "engines": [], "count": RESULTS_PER_LEARN_SEARCH}],
        [
            {"query": "Widgetlib 2.1.0 docs", "engines": ["mwmbl"], "count": 4},
            {"query": "Widgetlib 2.1.0 changelog"},
        ],
    ]
    assert not any(DOCS_SITE_SEARCH in messages[1]["content"] for messages in asked)
    assert web.fetched[:3] == [START, CHANGES, APP]
    assert all(url in web.fetched for url in DOC_PAGES if url != ELSEWHERE)


@pytest.mark.spec("LRN-7")
def test_learning_moves_its_tracker_through_its_steps_and_keeps_its_summary(tinker_home):
    tracker = Tracker("Learning Widgetlib")
    slot_learner = SlotLearner(tool_model(), FakeWeb(FOUND, ALL_PAGES), CountingEmbeddings(), tracker)
    entry = slot_learner.learn("Widgetlib")
    done = [
        Tracker.DOING_AND_DONE[phase][1] for phase in ("search", "plan", "machine", "documentation", "digest")
    ]
    assert [step.split(":")[0] for step in tracker.state["steps"][:5]] == done and tracker.state[
        "step"
    ] == "keep"
    each_with_what_came_of_it = [
        "🔎 Web searched: 2 of 3 page(s) read",
        "🤔 Worked out what it is: Widgetlib, a tool",
        "💻 Checked what is installed",
        "📖 Documentation read: 4 page(s) of Widgetlib 2.1.0",
        f"🧠 Passages read: 6 of 6 question(s) answered, {len(entry['facts'])} fact(s)",
    ]
    assert tracker.state["steps"][:5] == each_with_what_came_of_it
    each_step_once = len(tracker.state["steps"]) == len(set(tracker.state["steps"]))
    assert each_step_once
    recap = dict(slot_learner.recap)
    assert list(recap) == ["Answered", "Still open", "Documentation", "Pages", "Facts kept", "Web requests"]
    assert recap["Facts kept"] == str(len(entry["facts"])) and recap["Documentation"] == "widgetlib 2.1.0"


@pytest.mark.spec("LRN-7")
def test_a_stopped_learn_says_why_and_what_next_as_data(tinker_home):
    with pytest.raises(LearnStopped) as stopped:
        learner(FakeWeb(FOUND, ALL_PAGES), embeddings=None).learn("Widgetlib")
    why, next_step = stopped.value.details
    assert (
        why == ("Why", "stopped before starting: no embedding model is selected") and next_step[0] == "Next"
    )


@pytest.mark.spec("LRN-1", "LRN-4")
def test_learning_a_version_again_reuses_the_saved_plan_and_its_kept_documentation(tinker_home):
    learner(FakeWeb(FOUND, ALL_PAGES)).learn("Widgetlib")
    model, web = tool_model(), FakeWeb(FOUND, ALL_PAGES)
    entry = learner(web, model).learn("Widgetlib")
    assert "Plan learning" not in model.calls and model.calls[0].startswith("Digest")
    assert FIRST_SEARCH not in web.queries and VERSION_DOCS_SEARCH not in web.queries
    assert not any(url.startswith(DOC_ROOT) for url in web.fetched)
    assert entry["docs"] == {"widgetlib": "2.1.0"}


@pytest.mark.spec("LRN-3", "LRN-1")
def test_a_saved_plan_whose_documentation_gives_too_little_searches_the_web(tinker_home):
    learner(FakeWeb(FOUND, ALL_PAGES)).learn("Widgetlib")
    Home.store().delete("sources", "widgetlib@2.1.0")
    web_without_the_docs = FakeWeb({FIRST_SEARCH: RESULTS}, PAGES)
    learner(web_without_the_docs).learn("Widgetlib")
    assert web_without_the_docs.queries[:2] == [VERSION_DOCS_SEARCH, FIRST_SEARCH]
    assert START in web_without_the_docs.fetched
    model = tool_model()
    with pytest.raises(LearnStopped, match="the search found nothing"):
        learner(FakeWeb(), model).learn("Widgetlib")
    assert model.calls == []


@pytest.mark.spec("LRN-4")
def test_digest_and_condense_think_at_learn_think_and_the_plan_at_the_models_own(tinker_home):
    Config.save(learn_think="medium")
    model = tool_model()
    learner(FakeWeb(FOUND, ALL_PAGES), model).learn("Widgetlib")
    assert ("Plan learning", None) in model.thinking
    assert all(think == "medium" for label, think in model.thinking if label != "Plan learning")


GUIDE = "https://widgetlib.dev/guide"
PITFALLS = "https://widgetlib.dev/pitfalls"
NO_PITFALLS = {
    START: PAGES[START],
    GUIDE: html(("Guide", "Widgetlib guide to drawing widgets with themes and colours.")),
}
PITFALLS_PAGE = html(
    ("Common mistakes", "Common mistakes: drawing outside the event loop; limitations: one window.")
)


FOUND_NO_PITFALLS = {
    FIRST_SEARCH: [result(START, "Widgetlib start"), result(GUIDE, "Widgetlib guide")],
    VERSION_DOCS_SEARCH: DOC_RESULTS,
}


def follow_ups(web):
    return [query for query in web.queries if query not in (FIRST_SEARCH, VERSION_DOCS_SEARCH)]


@pytest.mark.spec("LRN-5")
def test_a_slot_left_without_facts_gets_the_plans_follow_up_search(tinker_home):
    web = FakeWeb(
        {**FOUND_NO_PITFALLS, "widgetlib common mistakes": [result(PITFALLS, "Widgetlib mistakes")]},
        {**NO_PITFALLS, **DOC_PAGES, PITFALLS: PITFALLS_PAGE},
    )
    entry = learner(web).learn("Widgetlib")
    assert "widgetlib common mistakes" in follow_ups(web)
    assert dict(PITFALLS_SEARCH, query="widgetlib common mistakes") in [
        entry for entries in web.entries for entry in entries
    ]
    assert any(
        fact["source"] == PITFALLS and fact["slot"] == "pitfalls-and-limits" for fact in entry["facts"]
    )
    assert len(follow_ups(web)) <= int(Config.get("learn_searches"))


@pytest.mark.spec("LRN-5")
def test_follow_ups_stop_at_learn_searches(tinker_home):
    Config.save(learn_searches=1)
    web = FakeWeb(FOUND_NO_PITFALLS, {**NO_PITFALLS, **DOC_PAGES})
    entry = learner(web).learn("Widgetlib")
    assert len(follow_ups(web)) == 1 and entry["status"] == "incomplete" and entry["missing"]


@pytest.mark.spec("LRN-5")
def test_a_failed_follow_up_keeps_the_run_alive_and_marks_it_incomplete(tinker_home, capsys):
    web = FakeWeb(FOUND_NO_PITFALLS, {**NO_PITFALLS, **DOC_PAGES})
    model = tool_model()
    entry = learner(web, model).learn("Widgetlib")
    assert follow_ups(web) and entry["status"] == "incomplete" and Library.read("widgetlib")
    assert (
        any("the extra search" in note for note in entry["notes"])
        and "⚠️  the extra search" in capsys.readouterr().out
    )
    assert model.calls[-1] == "Condense the facts"


LANGUAGE_PLAN = {
    "kind": "language",
    "name": "Lang",
    "version": "3.0",
    "summary": "A language.",
    "commands": ["echo lang 3.0.1", "echo langtest 1.2.0"],
    "tools": [{"role": "tests", "name": "langtest", "command": "echo langtest 1.2.0"}],
}
TOOL_DOCS_SEARCH = "langtest 1.2.0 documentation usage configuration"
TOOL_PAGES = {
    "https://langtest.dev/": html(("Langtest", "Langtest runs every test file of a Lang project.")),
    "https://langtest.dev/usage.html": html(
        ("Usage", "Run langtest -q from the project root; configuration lives in langtest.toml.")
    ),
}
LANG_DOC = "https://lang.dev/doc"
LANG_PAGES = {
    LANG_DOC: html(
        (
            "Test framework",
            "Lang test framework: run langtest; the formatter langfmt, the linter "
            "langlint, the package manager langpkg with its commands.",
        ),
        ("Keywords", "Lang keywords and syntax: basic types int and str."),
        ("Release notes", "Lang 3.0 release notes: new features for speed."),
    ),
    "https://lang.dev/limits": html(
        (
            "Limitations",
            "Lang limitations: the runtime performance is bound by one "
            "thread; use cases and strengths are scripting, weaknesses are games.",
        )
    ),
}


def language_model():
    return CountingModel(
        {
            "Plan learning": LANGUAGE_PLAN,
            "Digest": digest,
            "Condense": {"rules": [{"phase": "tests", "text": "Run tests with langtest", "cites": [1]}]},
        }
    )


@pytest.mark.spec("LRN-4", "KNW-21")
def test_a_language_is_learned_under_its_key_and_replaces_what_its_earlier_learning_proved(tinker_home):
    Library.save(
        "lang",
        {
            "topic": "lang",
            "kind": "language",
            "language": "Lang",
            "name": "Lang 2",
            "summary": "s",
            "facts": [{"phase": "code", "text": "Lang 2 keywords are if and else"}],
        },
    )
    old = Library.read("lang")["facts"][0]["id"]
    Knowledge("Lang").prove({f"fact:lang:{old}": ["code"]})
    web = FakeWeb(
        {
            "Lang official documentation": [result(url, "Lang docs") for url in LANG_PAGES],
            TOOL_DOCS_SEARCH: [result(url, "langtest docs") for url in TOOL_PAGES],
        },
        {**LANG_PAGES, **TOOL_PAGES},
    )
    entry = SlotLearner(language_model(), web, CountingEmbeddings()).learn("Lang")
    assert entry["kind"] == "language" and entry["language"] == "Lang" and Library.language("Lang") == "lang"
    assert (
        entry["tools"] == [{"role": "tests", "name": "langtest"}]
        and entry["machine"][1]["version"] == "1.2.0"
    )
    each_command_once = 2
    assert len(entry["machine"]) == each_command_once
    assert TOOL_DOCS_SEARCH in web.queries and all(url in web.fetched for url in TOOL_PAGES)
    language_and_tool_docs_at_once = [batch for batch in web.batches if TOOL_DOCS_SEARCH in batch]
    assert len(language_and_tool_docs_at_once) == 1 and len(language_and_tool_docs_at_once[0]) == 2
    assert entry["docs"] == {"langtest": "1.2.0"} and Home.store().get("sources", "langtest@1.2.0")["pages"]
    assert (
        Knowledge("Lang").language_doc["retired"][0]["reason"]
        == "replaced when the language was learned again"
    )


@pytest.mark.spec("LRN-6")
def test_a_domain_is_researched_when_deep(tinker_home, monkeypatch):
    researched = []
    monkeypatch.setattr(
        "tinkerkit.learning.Researcher.research",
        lambda self, topic: researched.append(topic) or {"kind": "research"},
    )
    model = CountingModel({"Plan learning": {"kind": "domain", "name": "Food bank inventory"}})
    web = FakeWeb(
        {
            "food bank inventory official documentation": [
                result(START, "food bank inventory"),
                result(CHANGES, "food bank inventory"),
            ]
        },
        {
            START: html(("Intake", "Food bank inventory weighs food at intake.")),
            CHANGES: html(("Stock", "Food bank inventory counts stock weekly.")),
        },
    )
    assert learner(web, model).learn("food bank inventory", deep=True) == {"kind": "research"}
    assert researched == ["food bank inventory"]
    Library.save(
        "food-bank-inventory", {"topic": "food bank inventory", "kind": "research", "name": "F", "facts": []}
    )
    nothing_asked = FakeWeb()
    assert learner(nothing_asked).learn("food bank inventory")["kind"] == "research"
    assert nothing_asked.queries == []
