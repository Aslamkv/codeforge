import hashlib
import json
import re

import pytest

from tinkerkit.fetcher import Fetched
from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.learning import (
    LearnStopped,
    RateLimited,
    RequestLog,
    SlotLearner,
    Throttle,
    read_digest,
    read_plan,
)
from tinkerkit.passages import Passage
from tinkerkit.project import Tracker
from tinkerkit.prompts import Reply
from tinkerkit.settings import FACTS_PER_SLOT, SLOT_CANDIDATE_PASSAGES, Config, Home
from tinkerkit.slots import PROFILES, Selector, missing, short_slots, web_slots


class FakeWeb:
    def __init__(self, answers=((),), pages=None, fallback=None, up=True):
        self.answers, self.pages, self.fallback, self.up = list(answers), pages or {}, fallback or {}, up
        self.queries, self.fetched, self.fallback_asked, self.limited = [], [], [], False

    def available(self):
        return self.up

    def problem(self, key=None):
        return "offline" if not self.up else "HTTP 429" if self.limited else "the backend broke"

    def search(self, query, count):
        self.queries.append(query)
        answer = self.answers[min(len(self.queries), len(self.answers)) - 1]
        self.limited = answer == "429"
        return None if answer is None or self.limited else list(answer)[:count]

    def rate_limited(self, key):
        return self.limited

    def fetch(self, url):
        self.fallback_asked.append(url)
        self.limited = False
        return {"title": "", "url": url, "content": self.fallback[url]} if url in self.fallback else None

    def page_fetcher(self, log=None):
        return FakeFetcher(self, log)


class FakeFetcher:
    def __init__(self, web, log):
        self.web, self.log = web, log

    def fetch_all(self, urls, kind="fetch"):
        self.web.fetched += urls
        got = [self.fetched(url) for url in urls]
        for each in got:
            self.log(kind, "ok" if each.ok else "failed", 0.01, each.url)
        return got

    def fetched(self, url):
        if url not in self.web.pages:
            return Fetched(url, 404, url, error="HTTP 404")
        if self.web.pages[url] is None:
            return Fetched(url, final_url=url, error=Fetched.ROBOTS)
        return Fetched(url, 200, url, self.web.pages[url], "text/html")


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
            return read(Reply(json.dumps(answer(parts) if callable(answer) else answer)))
        except ValueError:
            return None


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


class FakeClock:
    def __init__(self):
        self.at, self.slept = 0.0, []

    def now(self):
        return self.at

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.at += seconds


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
FALLBACK = {
    APP: "# App guide\n\n" + "Widgetlib apps install with pip and run testing with the headless mode. " * 8
}
DOCS = "https://widgetlib.dev/docs/"
PLAN = {
    "kind": "tool",
    "name": "Widgetlib",
    "version": "2.0",
    "summary": "A widget library.",
    "language": "Python",
    "commands": ["echo widgetlib 2.1.0", "pip install widgetlib"],
    "hints": {"core-usage": "the Widget class"},
    "docs": {"root": DOCS + "latest/", "versioned": DOCS + "{version}/"},
    "followups": {"pitfalls-and-limits": "widgetlib common mistakes", "unknown-slot": "x"},
}
DOC_ROOT = DOCS + "2.1/"
MINOR_VERSION_DOC_PAGES = {
    DOC_ROOT: "<html><body><main><h1>Widgetlib 2.1</h1><p>"
    + "Widgetlib overview: its purpose is drawing widgets. " * 10
    + '</p><a href="quickstart.html">Quickstart</a> <a href="changelog/index.html">Changelog</a> '
    '<a href="configuration.html">Configuration</a> <a href="install.html">Installation</a> '
    '<a href="https://elsewhere.org/x">Elsewhere</a></main></body></html>',
    DOC_ROOT + "quickstart.html": html(
        ("Getting started", "This tutorial shows the API: create a Widget, then show it.")
    ),
    DOC_ROOT + "changelog/index.html": '<html><body><ul><li><a href="2.1.html">2.1</a></li>'
    '<li><a href="2.0.html">2.0</a></li></ul></body></html>',
    DOC_ROOT + "changelog/2.1.html": html(
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
}


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


@pytest.mark.spec("LRN-1")
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
    assert this_version.search(plan, "2.1") == its_own_search
    pitfalls = next(slot for slot in web_slots("tool") if slot.id == "pitfalls-and-limits")
    assert pitfalls.search(plan, "2.1") == "widgetlib common mistakes"
    assert (
        core.search(plan, "2.1") == "Widgetlib getting started tutorial API"
        and core.keywords() == "getting started tutorial API"
    )


@pytest.mark.spec("LRN-2")
def test_the_throttle_lets_rate_requests_a_minute_through():
    clock = FakeClock()
    throttle = Throttle(2, 20, clock=clock.now, sleep=clock.sleep)
    for _ in range(3):
        throttle.call(lambda: "ok", lambda: False)
    half_a_minute_for_the_third = [30.0]
    assert clock.slept == half_a_minute_for_the_third


@pytest.mark.spec("LRN-2")
def test_the_throttle_waits_out_429_within_its_budget():
    clock = FakeClock()
    with pytest.raises(RateLimited):
        Throttle(60, 20, clock=clock.now, sleep=clock.sleep).call(lambda: None, lambda: True)
    assert clock.slept == [1.0, 2.0, 4.0, 8.0, 5.0] and sum(clock.slept) <= 20
    answers = iter([True, False])
    assert (
        Throttle(60, 20, clock=clock.now, sleep=clock.sleep).call(lambda: "ok", lambda: next(answers)) == "ok"
    )
    clock.slept.clear()
    with pytest.raises(RateLimited):
        Throttle(60, 0, clock=clock.now, sleep=clock.sleep).call(lambda: None, lambda: True)
    assert clock.slept == []


@pytest.mark.spec("LRN-3")
def test_every_request_is_logged_and_counted(tinker_home):
    log = RequestLog()
    log("search", "429", 0.5, "q")
    log("search", "ok", 0.4, "q")
    log("fetch", "failed", 1.0, "https://a")
    lines = [json.loads(line) for line in Home.web_log().read_text().splitlines()]
    assert [(line["kind"], line["result"], line["target"]) for line in lines] == [
        ("search", "429", "q"),
        ("search", "ok", "q"),
        ("fetch", "failed", "https://a"),
    ]
    assert log.counts()["search"] == {"requests": 2, "429": 1, "ok": 1}
    assert log.text() == "search 2, fetch 1 (1 failed); HTTP 429: 1" and RequestLog().text() == "none"


def passage(number, text, url="https://x.dev/doc"):
    return Passage(url, "t", "", text, f"P{number}")


TOOLCHAIN = next(slot for slot in web_slots("language") if slot.id == "toolchain")
FUNDAMENTALS = next(slot for slot in web_slots("language") if slot.id == "fundamentals")
LANGUAGE = {"name": "Lang", "language": "Lang"}


@pytest.mark.spec("LRN-7")
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


@pytest.mark.spec("LRN-7")
def test_meaning_finds_a_passage_with_no_shared_keyword():
    class Close(CountingEmbeddings):
        def vectors(self, texts, keep=True, prune=False):
            self.calls += 1
            close, far = [1.0, 0.0], [0.0, 1.0]
            return [close if "check" in text or "test" in text else far for text in texts]

    pool = [passage(1, "Its unit checks run with one command."), passage(2, "Gardening is fun.")]
    picked = Selector(Close(), 10_000).select([TOOLCHAIN], pool, LANGUAGE, "1.0")
    assert [passage.id for passage in picked["toolchain"]] == ["P1"]


@pytest.mark.spec("LRN-7")
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


@pytest.mark.spec("LRN-8")
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


@pytest.mark.spec("LRN-9")
def test_read_plan_checks_the_plan():
    plan = read_plan(PLAN)
    assert (plan["kind"], plan["profile"], plan["language"], plan["used_with"]) == (
        "tool",
        "tool",
        "",
        "Python",
    )
    assert plan["commands"] == ["echo widgetlib 2.1.0"] and plan["dropped"] == ["pip install widgetlib"]
    assert plan["followups"] == {"pitfalls-and-limits": "widgetlib common mistakes"} and plan["tools"] == []
    assert plan["docs"] == {"root": DOCS + "latest/", "versioned": DOCS + "{version}/"}
    language = read_plan(
        {
            "kind": "language",
            "name": "Lang",
            "docs": {"root": "not an address", "versioned": "https://x/"},
            "tools": [
                {
                    "role": "tests",
                    "name": "langtest",
                    "command": "langtest --version",
                    "docs": {"root": "https://langtest.dev/"},
                },
                {"role": "lint", "name": "langlint", "command": "langlint --version; rm x"},
                {"role": "tests", "name": "other"},
                {"role": "magic", "name": "x"},
                "junk",
            ],
            "hints": {"toolchain": "langtest", "purpose": "a tool's slot"},
        }
    )
    assert language["language"] == "Lang" and language["docs"] == {"root": "", "versioned": ""}
    assert language["tools"] == [
        {
            "role": "tests",
            "name": "langtest",
            "command": "langtest --version",
            "docs": {"root": "https://langtest.dev/", "versioned": ""},
        },
        {"role": "lint", "name": "langlint", "command": "", "docs": {"root": "", "versioned": ""}},
    ]
    assert language["dropped"] == ["langlint --version; rm x"]
    assert (
        language["hints"] == {"toolchain": "langtest"}
        and read_plan({"kind": "domain", "name": "D"})["profile"] == "tool"
    )
    for bad in ({"kind": "band", "name": "X"}, {"kind": "tool", "name": ""}, ["not", "an", "object"]):
        with pytest.raises(ValueError):
            read_plan(bad)


@pytest.mark.spec("LRN-9")
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
    "plugin off": (dict(up=False), "the Internet plugin is off"),
    "search error": (dict(answers=[None]), "the search failed"),
    "429 past the backoff": (dict(answers=["429"]), "rate limited (HTTP 429)"),
    "zero results": (dict(answers=[[]]), "the search found nothing"),
    "no relevant results": (dict(answers=[[OFF_TOPIC]]), "no search result is about the topic"),
    "too few pages": (dict(answers=[RESULTS], pages={START: PAGES[START]}), "too few pages could be fetched"),
}


@pytest.mark.spec("LRN-10")
@pytest.mark.parametrize("cause", WEB_AND_WARNING_BY_CAUSE)
def test_a_learn_the_web_cannot_feed_stops_before_any_model_call(cause, tinker_home):
    web_settings, warning = WEB_AND_WARNING_BY_CAUSE[cause]
    model, embeddings, clock = tool_model(), CountingEmbeddings(), FakeClock()
    slot_learner = SlotLearner(model, FakeWeb(**web_settings), embeddings)
    slot_learner.throttle = Throttle(8, 20, clock=clock.now, sleep=clock.sleep)
    with pytest.raises(LearnStopped) as stopped:
        slot_learner.learn("Widgetlib")
    message = str(stopped.value)
    exit_code = stopped.value.code
    assert isinstance(stopped.value, SystemExit) and exit_code
    assert warning in message and "Nothing was learned or saved" in message and "Next: " in message
    assert "Web requests: " in message and "Tried: " in message
    assert model.calls == [] and embeddings.calls == 0
    assert all(Home.store().items(part) == {} for part in ("learned", "embeddings", "languages", "memory"))
    assert sum(clock.slept) <= 20


@pytest.mark.spec("LRN-10")
def test_without_an_embedding_model_nothing_goes_online(tinker_home):
    web = FakeWeb([RESULTS], PAGES)
    with pytest.raises(LearnStopped, match="no embedding model"):
        learner(web, embeddings=None).learn("Widgetlib")
    assert web.queries == []


@pytest.mark.spec("LRN-10")
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
        learner(FakeWeb(up=False)).learn("Widgetlib")
    assert Library.read("widgetlib") == before


@pytest.mark.spec("LRN-11")
def test_a_tool_is_learned_from_its_versions_documentation_and_one_search(tinker_home, capsys):
    web, model, embeddings = (
        FakeWeb([RESULTS], {**PAGES, **MINOR_VERSION_DOC_PAGES}, FALLBACK),
        tool_model(),
        CountingEmbeddings(),
    )
    entry = SlotLearner(model, web, embeddings).learn("Widgetlib")
    assert web.queries == ["Widgetlib official documentation"]
    without_off_topic_and_repeat = [START, CHANGES, APP]
    assert web.fetched[:3] == without_off_topic_and_repeat and web.fallback_asked == [APP]
    one_hop_behind_the_index = [DOCS + "2.1.0/", DOC_ROOT + "changelog/2.1.html"]
    assert all(url in web.fetched for url in one_hop_behind_the_index)
    assert "https://elsewhere.org/x" not in web.fetched and entry["docs"] == {"widgetlib": "2.1"}
    assert model.calls[0] == "Plan learning" and model.calls[-1] == "Condense the facts" and embeddings.calls
    assert entry["kind"] == "tool" and entry["machine"][0]["version"] == "2.1.0" and entry["language"] == ""
    assert any(fact["source"].startswith(DOC_ROOT) for fact in entry["facts"])
    assert all(
        fact["passages"] and fact["source"].startswith("https://widgetlib.dev") for fact in entry["facts"]
    )
    assert "Uncited claim" not in [fact["text"] for fact in entry["facts"]]
    assert all(passage["fetched"] for passage in entry["passages"])
    assert [rule["text"] for rule in entry["rules"]] == ["Create a Widget, then show it"]
    assert entry["requests"]["search"]["requests"] == 1 and entry["requests"]["fallback"]["requests"] == 1
    assert (
        entry["status"] == ("incomplete" if entry["missing"] else "complete")
        and "readiness" not in entry["missing"]
    )
    saved = Library.read("widgetlib")
    assert saved["plan"]["docs"]["versioned"] and all(
        fact["status"] == "candidate" for fact in saved["facts"]
    )
    out = capsys.readouterr().out
    assert "Documentation: widgetlib 2.1" in out and "Web requests: search 1" in out
    assert "Pages: 3 searched" in out and f"Facts kept: {len(entry['facts'])}" in out


@pytest.mark.spec("LRN-18")
def test_learning_moves_its_tracker_through_its_steps_and_keeps_its_summary(tinker_home):
    tracker = Tracker("Learning Widgetlib")
    slot_learner = SlotLearner(
        tool_model(),
        FakeWeb([RESULTS], {**PAGES, **MINOR_VERSION_DOC_PAGES}, FALLBACK),
        CountingEmbeddings(),
        tracker,
    )
    entry = slot_learner.learn("Widgetlib")
    done = [
        Tracker.DOING_AND_DONE[phase][1] for phase in ("search", "plan", "machine", "documentation", "digest")
    ]
    assert [step.split(":")[0] for step in tracker.state["steps"][:5]] == done and tracker.state[
        "step"
    ] == "keep"
    each_with_what_came_of_it = [
        "🔎 Web searched: 3 of 3 page(s) read",
        "🤔 Worked out what it is: Widgetlib, a tool",
        "💻 Checked what is installed",
        "📖 Documentation read: 5 page(s) of Widgetlib 2.1.0",
        f"🧠 Passages read: 6 of 6 question(s) answered, {len(entry['facts'])} fact(s)",
    ]
    assert tracker.state["steps"][:5] == each_with_what_came_of_it
    each_step_once = len(tracker.state["steps"]) == len(set(tracker.state["steps"]))
    assert each_step_once
    recap = dict(slot_learner.recap)
    assert list(recap) == ["Answered", "Still open", "Documentation", "Pages", "Facts kept", "Web requests"]
    assert recap["Facts kept"] == str(len(entry["facts"])) and recap["Documentation"] == "widgetlib 2.1"


@pytest.mark.spec("LRN-18")
def test_a_stopped_learn_says_why_and_what_next_as_data(tinker_home):
    with pytest.raises(LearnStopped) as stopped:
        learner(FakeWeb([RESULTS], PAGES, FALLBACK), embeddings=None).learn("Widgetlib")
    why, next_step = stopped.value.details
    assert (
        why == ("Why", "stopped before starting: no embedding model is selected") and next_step[0] == "Next"
    )


@pytest.mark.spec("LRN-11", "LRN-17")
def test_learning_again_reuses_the_saved_plan_and_the_kept_documentation_with_no_web(tinker_home):
    learner(FakeWeb([RESULTS], {**PAGES, **MINOR_VERSION_DOC_PAGES}, FALLBACK)).learn("Widgetlib")
    model, web = tool_model(), FakeWeb([RESULTS], {**PAGES, **MINOR_VERSION_DOC_PAGES}, FALLBACK)
    entry = learner(web, model).learn("Widgetlib")
    assert "Plan learning" not in model.calls and model.calls[0].startswith("Digest")
    assert not any(url.startswith(DOCS) for url in web.fetched) and entry["docs"] == {"widgetlib": "2.1"}
    only_the_empty_slots_follow_up = ["widgetlib common mistakes"]
    assert web.queries == only_the_empty_slots_follow_up


@pytest.mark.spec("LRN-10", "LRN-17")
def test_a_saved_plan_whose_documentation_gives_too_little_searches_the_web(tinker_home):
    learner(FakeWeb([RESULTS], {**PAGES, **MINOR_VERSION_DOC_PAGES}, FALLBACK)).learn("Widgetlib")
    Home.store().delete("sources", "widgetlib@2.1")
    web_without_the_docs = FakeWeb([RESULTS], PAGES, FALLBACK)
    learner(web_without_the_docs).learn("Widgetlib")
    assert (
        web_without_the_docs.queries[0] == "Widgetlib official documentation"
        and START in web_without_the_docs.fetched
    )
    model, offline = tool_model(), FakeWeb([None], {})
    Home.store().delete("sources", "widgetlib@2.1")
    with pytest.raises(LearnStopped, match="the search failed"):
        learner(offline, model).learn("Widgetlib")
    assert model.calls == []


@pytest.mark.spec("LRN-11")
def test_the_fallback_fetch_is_capped_and_never_for_a_page_robots_txt_refused(tinker_home):
    Config.save(fetch_fallbacks=1)
    thin = {url: PAGES[APP] for url in ("https://widgetlib.dev/a", "https://widgetlib.dev/b")}
    results = [
        result(START, "Widgetlib"),
        result(CHANGES, "Widgetlib"),
        result("https://widgetlib.dev/private", "Widgetlib"),
    ]
    results += [result(url, "Widgetlib") for url in thin]
    web = FakeWeb([results], {**PAGES, "https://widgetlib.dev/private": None, **thin})
    learner(web).learn("Widgetlib")
    assert web.fallback_asked == ["https://widgetlib.dev/a"]


@pytest.mark.spec("LRN-11")
def test_digest_and_condense_think_at_learn_think_and_the_plan_at_the_models_own(tinker_home):
    Config.save(learn_think="medium")
    model = tool_model()
    learner(FakeWeb([RESULTS], {**PAGES, **MINOR_VERSION_DOC_PAGES}, FALLBACK), model).learn("Widgetlib")
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


@pytest.mark.spec("LRN-12")
def test_a_slot_left_without_facts_gets_the_plans_follow_up_search(tinker_home):
    web = FakeWeb(
        [
            [result(START, "Widgetlib start"), result(GUIDE, "Widgetlib guide")],
            [result(PITFALLS, "Widgetlib mistakes")],
        ],
        {**NO_PITFALLS, **MINOR_VERSION_DOC_PAGES, PITFALLS: PITFALLS_PAGE},
    )
    entry = learner(web).learn("Widgetlib")
    assert web.queries == ["Widgetlib official documentation", "widgetlib common mistakes"]
    assert any(
        fact["source"] == PITFALLS and fact["slot"] == "pitfalls-and-limits" for fact in entry["facts"]
    )
    assert len(web.queries) <= int(Config.get("learn_searches"))


@pytest.mark.spec("LRN-12")
def test_follow_ups_stop_at_learn_searches(tinker_home):
    Config.save(learn_searches=1)
    web = FakeWeb(
        [[result(START, "Widgetlib start"), result(GUIDE, "Widgetlib guide")]],
        {**NO_PITFALLS, **MINOR_VERSION_DOC_PAGES},
    )
    entry = learner(web).learn("Widgetlib")
    assert (
        len(web.queries) == 1
        and entry["status"] == "incomplete"
        and "pitfalls-and-limits" in entry["missing"]
    )


@pytest.mark.spec("LRN-12")
def test_a_failed_follow_up_keeps_the_run_alive_and_marks_it_incomplete(tinker_home, capsys):
    web = FakeWeb(
        [[result(START, "Widgetlib start"), result(GUIDE, "Widgetlib guide")], None],
        {**NO_PITFALLS, **MINOR_VERSION_DOC_PAGES},
    )
    model = tool_model()
    entry = learner(web, model).learn("Widgetlib")
    assert len(web.queries) >= 2 and entry["status"] == "incomplete" and Library.read("widgetlib")
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
    "tools": [
        {
            "role": "tests",
            "name": "langtest",
            "command": "echo langtest 1.2.0",
            "docs": {"root": "https://langtest.dev/"},
        }
    ],
}
TOOL_PAGES = {
    "https://langtest.dev/": html(("Langtest", "Langtest runs every test file of a Lang project."))
    + '<a href="usage.html">Usage</a> <a href="blog.html">Blog</a>',
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


@pytest.mark.spec("LRN-11", "KNW-24")
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
    web = FakeWeb([[result(url, "Lang docs") for url in LANG_PAGES]], {**LANG_PAGES, **TOOL_PAGES})
    entry = SlotLearner(language_model(), web, CountingEmbeddings()).learn("Lang")
    assert entry["kind"] == "language" and entry["language"] == "Lang" and Library.language("Lang") == "lang"
    assert (
        entry["tools"] == [{"role": "tests", "name": "langtest"}]
        and entry["machine"][1]["version"] == "1.2.0"
    )
    each_command_once = 2
    assert len(entry["machine"]) == each_command_once
    assert (
        "https://langtest.dev/usage.html" in web.fetched
        and "https://langtest.dev/blog.html" not in web.fetched
    )
    assert entry["docs"] == {"langtest": "latest"} and Home.store().get("sources", "langtest@latest")["pages"]
    assert (
        Knowledge("Lang").language_doc["retired"][0]["reason"]
        == "replaced when the language was learned again"
    )


@pytest.mark.spec("LRN-13")
def test_a_domain_is_researched_when_deep(tinker_home, monkeypatch):
    researched = []
    monkeypatch.setattr(
        "tinkerkit.learning.Researcher.research",
        lambda self, topic: researched.append(topic) or {"kind": "research"},
    )
    model = CountingModel({"Plan learning": {"kind": "domain", "name": "Food bank inventory"}})
    web = FakeWeb(
        [[result(START, "food bank inventory"), result(CHANGES, "food bank inventory")]],
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
    nothing_asked = FakeWeb(up=False)
    assert learner(nothing_asked).learn("food bank inventory")["kind"] == "research"
