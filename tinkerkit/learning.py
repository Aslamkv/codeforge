"""Learning a language or a tool by its slots: tinker learn TOPIC, and the recall phase of a run with --web.
A fixed pipeline whose main source is the topic's own documentation, for the version installed, and no model
call (plan, digest, condense or embedding) until the web (or the documentation kept from before) has delivered
usable pages:
  PREFLIGHT  the Internet plugin and an embedding model are there
  PLAN       the plan saved with the topic; else SEARCH 1 (DOCS_SITE_SEARCH, throttled), GATE 1, PICK (the results
             about the topic, BM25), FETCH (the plugin's fetcher), GATE 2, then the first model call: language,
             tool or domain, the version commands, the documentation sites, slot hints, follow-up searches
  MACHINE    the version commands run here: the version installed
  DOCS       the documentation of that version (and of a language's tools): kept from before (DocCache), else
             read from its site (DocSite); then GATE 2 for a saved plan (search 1 when the docs give too little)
  CLEAN      the pages as passages, repeats dropped
  SELECT     each slot's passages from one shared pool
  DIGEST     each slot's passages into facts that cite them (thinking at learn_think)
  FOLLOW-UP  each slot left without facts: the plan's follow-up search, while learn_searches allow; its
             failure is not fatal
  CONDENSE   the facts into rules (thinking at learn_think)
  KEEP       saved (only now), the old proven copies retired, and indexed
A gate that stops the run raises LearnStopped, and a model that fails ends it too: nothing is learned then."""

lazy import json
lazy import threading
lazy import time
lazy import urllib.parse
lazy from collections import Counter

lazy from .docsite import DocSite
lazy from .jobs import today
lazy from .knowledge import Learner, Library, Researcher, Retriever, language_key
from .messages import Message, Messages, say
lazy from .model import OllamaError
lazy from .passages import Cleaner, Page, text_blocks
lazy from .prompts import Part, PromptTooLarge, Prompts
lazy from .settings import (
    DOCS_SITE_SEARCH, FACTS_PER_SLOT, FACT_PHASES, FIRST_BACKOFF_SECONDS, MAX_FACT_CHARS,
    MAX_VERSION_COMMANDS, RESULTS_PER_LEARN_SEARCH, TOOL_ROLES, Config, Home, first_line, now_text,
)
lazy from .slots import PROFILES, TOOL_DOCS_QUERY, Selector, fill, missing, short_slots, web_slots
lazy from .web import is_web_address

LEARN_STOP_CAUSES = frozendict({
    "plugin": ("the Internet plugin is off or cannot be reached",
               "check the network and tinker use-web (tinker config shows the web's state), then learn again"),
    "embeddings": ("no embedding model is selected", "select one with tinker embed NAME, then learn again"),
    "error": ("the search failed", "learn again; if it keeps failing, try another backend with tinker use-web NAME"),
    "rate-limit": ("the search is rate limited (HTTP 429)", "wait a minute, then learn again"),
    "no-results": ("the search found nothing", "check the topic's spelling, or name it more fully"),
    "irrelevant": ("no search result is about the topic", "name the topic more fully (with its language, say)"),
    "pages": ("too few pages could be fetched", "learn again later: a site may refuse fetches or draw its pages with "
              "scripts (fetch_fallbacks lets the web plugin fetch more of those)"),
})

LEARN_STOP_PLACES = frozendict({
    "preflight": "before starting",
    "gate 1": "at the first web search",
    "gate 2": "while reading pages",
})
RECAP_LINE_ICONS = ("✅", "❓", "📖", "📄", "🧩", "🌐")


class LearnStopped(SystemExit):
    """A learn stopped before any model call because the web gave nothing to learn from. Its message names the
    cause, what was tried and the requests made, says nothing was saved, and what to do next. A SystemExit:
    tinker learn ends non-zero; a run's recall phase goes on without it. details: why and what to do next, as
    (label, text), for the chats."""

    def __init__(self, message, details=()):
        super().__init__(message)
        self.details = list(details)


class SearchFailed(Exception):
    def __init__(self, cause, detail=""):
        super().__init__(cause)
        self.cause, self.detail = cause, detail


class RateLimited(Exception):
    """HTTP 429 lasted past the backoff budget."""


class SlotLearner:
    """Learns a topic by its profile's slots (see the module). Learning it again reuses the plan saved with it
    (edit it there) and the documentation kept for its version; a domain is researched instead when deep
    (Researcher), else learned like a tool."""

    def __init__(self, model, internet, embeddings, tracker=None):
        self.learner = Learner(model, internet, embeddings)
        self.learner.tracker = tracker
        self.model, self.internet, self.embeddings = model, internet, embeddings
        self.log = RequestLog()
        self.throttle = Throttle(Config.get("learn_rate"), Config.get("learn_backoff"))
        self.cleaner = Cleaner()
        self.fetcher = None
        self.topic, self.known, self.tried, self.seen, self.docs = "", None, [], set(), {}
        self.searches, self.fallbacks, self.saw_429, self.incomplete = 0, 0, False, False
        self.pages = Counter()
        self.recap = []

    def learn(self, topic, deep=False):
        """The topic learned and saved (its entry); a topic researched before is researched again when deep,
        else kept as it is."""
        known = Library.read(Library.slug(topic))
        if (known or {}).get("kind") == "research":
            return Researcher(self.learner).research(topic) if deep else known
        self.learner.start()
        self.topic, self.known = topic, known or Library.read(Library.language(topic) or "")
        with say.part(f"Learning {topic}", "📚"):
            entry = self.learned_by_slots(deep)
        if self.recap:
            say.summary(f"Learned {entry['name']}", [(icon, *row) for icon, row in zip(RECAP_LINE_ICONS, self.recap)])
        return entry

    def learned_by_slots(self, deep):
        """PREFLIGHT, the plan and its pages, the machine, the documentation, then what is learned from them; a
        domain, when deep, is researched instead."""
        self.preflight()
        plan, pages = self.plan_and_pages()
        if plan["kind"] == "domain" and deep:
            say.info(f"{self.topic} is a broad subject, not one tool: researching it and cross-checking the sources",
                     "🧭")
            return Researcher(self.learner).research(self.topic)
        each_command_once = list(dict.fromkeys(
            plan["commands"] + [tool["command"] for tool in plan["tools"] if tool["command"]]))
        machine = self.learner.look_at_machine(each_command_once)
        version = version_of(machine, plan["commands"]) or plan["version"]
        pages = self.enough_pages(pages + self.documentation(plan, machine, version))
        return self.learned(plan, version, machine, pages)

    def preflight(self):
        """The Internet plugin and an embedding model are there, before anything goes online."""
        if self.internet is None or not self.internet.available():
            self.stop("preflight", "plugin", self.internet.problem() if self.internet else "")
        if self.embeddings is None:
            self.stop("preflight", "embeddings")
        self.fetcher = self.internet.page_fetcher(self.log)

    def plan_and_pages(self):
        """(the plan, the pages read for it): the plan saved with the topic, with no search; else search 1 and
        its gates, then the model's plan."""
        saved = self.saved_plan()
        if saved:
            return saved, []
        pages, results = self.usable_pages(self.first_search())
        return self.asked_plan(results), pages

    def saved_plan(self):
        saved = (self.known or {}).get("plan")
        if not saved:
            return None
        try:
            plan = read_plan(saved)
        except ValueError as exc:
            say.warn(f"The plan saved last time cannot be used ({exc}), so making a new one")
            return None
        self.learner.step("plan", "Using the plan saved last time: no web search needed to start", "📋")
        return plan

    def first_search(self):
        """SEARCH 1, then GATE 1: the results about the topic, or the run stops."""
        self.learner.step("search", f"Searching the web for pages about {self.topic}…", "🔎")
        try:
            results = self.search(DOCS_SITE_SEARCH.format(topic=self.topic))
        except SearchFailed as failed:
            self.stop("gate 1", failed.cause, failed.detail)
        picked = self.picked(self.topic, results)
        if not picked:
            self.stop("gate 1", "irrelevant", f"{len(results)} result(s), none above learn_cutoff")
        return picked

    def search(self, query):
        """The results of one throttled search, or SearchFailed with the cause."""
        self.searches += 1
        self.tried.append(f'search "{query}"')
        try:
            found = self.throttle.call(lambda: self.timed("search", query, lambda: self.internet.search(query, RESULTS_PER_LEARN_SEARCH)),
                                       lambda: self.internet.rate_limited(query))
        except RateLimited:
            raise SearchFailed("rate-limit", f"still after {Config.get('learn_backoff')}s of backoff")
        if found is None:
            cause = "rate-limit" if self.saw_429 else "error" if self.internet.available() else "plugin"
            raise SearchFailed(cause, first_line(self.internet.problem(query), 160))
        if not found:
            raise SearchFailed("no-results")
        return found

    def timed(self, kind, target, request):
        """request()'s result, logged with how long it took and whether it was rate limited."""
        started = time.monotonic()
        result = request()
        limited = self.internet.rate_limited(target)
        self.saw_429 |= limited
        self.log(kind, "ok" if result is not None else "429" if limited else "failed", time.monotonic() - started, target)
        return result

    def picked(self, topic, results):
        """PICK, with no model: the results about the topic by the words of their title and snippet (BM25 above
        learn_cutoff), in the order the search ranked them, each address once (none fetched before), at most
        learn_pages."""
        scores = Retriever(None).word_scores(topic, [f"{result['title']} {result['content']}" for result in results])
        picked, cutoff, most = [], float(Config.get("learn_cutoff")), int(Config.get("learn_pages"))
        for result, score in zip(results, scores):
            key = address_key(result["url"])
            if score > cutoff and key not in self.seen and len(picked) < most:
                self.seen.add(key)
                picked.append(result)
        return picked

    def usable_pages(self, picked, have=0):
        """FETCH, then GATE 2: (the pages with usable text, their search results); the run stops when they and the
        pages it has already (have) are fewer than learn_min_pages."""
        pages, results = self.fetched(picked)
        needed = int(Config.get("learn_min_pages"))
        if len(pages) + have < needed:
            self.stop("gate 2", "pages", f"{len(pages) + have} page(s) gave usable text; {needed} needed")
        return pages, results

    def fetched(self, picked):
        """(the pages with usable text, their results): fetched by the plugin's fetcher; a page that gives too
        little text (one its scripts draw) is fetched by the plugin instead, at most fetch_fallbacks per learn.
        Snippets never count as pages."""
        got = self.fetcher.fetch_all([result["url"] for result in picked])
        with say.section():
            pages = [self.fallen_back(page) if not page.usable() and self.may_fall_back(fetched) else page
                     for page, fetched in ((self.page_of(fetched), fetched) for fetched in got)]
            usable = [(page, result) for page, result in zip(pages, picked) if page.usable()]
            self.pages.update(searched=len(usable), failed=len(pages) - len(usable))
            self.tried.append(f"fetched {len(picked)} page(s), {len(usable)} with usable text")
            say.info(f"Read {len(usable)} of {len(picked)} page(s) found", "📄")
        self.outcome(f"{len(usable)} of {len(picked)} page(s) read")
        return [page for page, _ in usable], [result for _, result in usable]

    def outcome(self, text):
        self.learner.outcome(text)

    @staticmethod
    def page_of(fetched):
        if not fetched.ok:
            say.fail(f"Could not open {fetched.url} ({fetched.error})")
            return Page(fetched.url, now_text(), [])
        return Cleaner.page(fetched.final_url or fetched.url, fetched.text, fetched.kind, now_text())

    def may_fall_back(self, fetched):
        return not fetched.refused and self.fallbacks < int(Config.get("fetch_fallbacks"))

    def fallen_back(self, page):
        """The page as the web plugin fetches it (logged as a fallback), or as it was when that fails too."""
        self.fallbacks += 1
        say.retry(f"{page.url} showed too little text; asking the web plugin to read it "
                  f"({self.fallbacks} of {Config.get('fetch_fallbacks')})")
        try:
            got = self.throttle.call(lambda: self.timed("fallback", page.url, lambda: self.internet.fetch(page.url)),
                                     lambda: self.internet.rate_limited(page.url))
        except RateLimited:
            got = None
        return Page(page.url, now_text(), text_blocks(got["content"])) if got else page

    def asked_plan(self, results):
        """The model's plan (the first model call), from the topic and the titles and snippets of the pages fetched."""
        self.learner.step("plan", f"Working out what {self.topic} is, how to check the version you have, and where "
                                  f"its documentation is…", "🤔")
        with say.working_on(f"Working out what {self.topic} is, from {len(results)} page(s) found"):
            plan = self.model.ask_until_usable(Prompts.SLOT_PLAN_SYSTEM, self.plan_parts(results), "Plan learning",
                                               lambda reply: read_plan(reply.json()), attempts=2)
        if plan is None:
            raise SystemExit(f"{self.model.name} gave no usable plan for learning {self.topic}. Nothing was saved.")
        self.say_plan(plan)
        return plan

    def say_plan(self, plan):
        """What the plan says the topic is, where its documentation is, and what it left out."""
        say.found(f"{plan['name']} is a {plan['kind']}: {plan['summary']}")
        self.outcome(f"{plan['name']}, a {plan['kind']}")
        if plan["docs"]["root"]:
            with say.section():
                say.info(f"Documentation: {plan['docs']['root']}", "📍")
        self.learner.show_dropped(plan)

    def plan_parts(self, results):
        found = "\n".join(f"- {result['title']} ({result['url']}): {first_line(result['content'], 200)}"
                          for result in results)
        slots = lambda profile: "\n".join(f"- {slot.id}: {fill(slot.question, {'name': self.topic}, '')}"
                                          for slot in web_slots(profile))
        return [Part("Topic", self.topic, True), Part("Today", today().isoformat(), True),
                Part("Pages found", found, True), Part("Slots of a language", slots("language"), True),
                Part("Slots of a tool", slots("tool"), True)]

    def documentation(self, plan, machine, version):
        """DOCS: the pages of the topic's documentation its slots want and, for a language, of each tool's (how to
        run and set it up), each for the version installed: kept from before, else read from the site."""
        self.learner.step("documentation")
        site = DocSite(self.fetcher)
        pages = self.doc_pages(site, self.slug(plan), plan["docs"], version,
                               [slot.docs for slot in web_slots(plan["profile"])], int(Config.get("docs_pages")))
        for tool in {tool["docs"].get("root") or tool["name"]: tool for tool in plan["tools"]}.values():
            pages += self.doc_pages(site, Library.slug(tool["name"]), tool["docs"],
                                    version_of(machine, [tool["command"]]), [TOOL_DOCS_QUERY],
                                    int(Config.get("tool_docs_pages")))
        self.outcome(f"{len(pages)} page(s)" + (f" of {plan['name']} {version}" if version else ""))
        return pages

    def doc_pages(self, site, slug, docs, version, wanted, cap):
        if not (docs.get("root") or docs.get("versioned")) or cap <= 0:
            return []
        with say.working_on(f"Reading the {slug} documentation" + (f" for {version}" if version else "")):
            pages, key, kept = site.pages(slug, docs, version, wanted, cap)
        self.docs[slug] = key or "not reachable"
        self.pages.update(**{"kept docs" if kept else "docs": len(pages)})
        where = "the copy saved last time" if kept else docs.get("versioned") or docs.get("root")
        say.info(f"Read {len(pages)} page(s) of the {slug} {key} documentation ({where})" if key
                 else f"Could not reach the {slug} documentation ({where})", "📖")
        return pages

    def enough_pages(self, pages):
        """GATE 2 for a learn whose plan was saved: the documentation's usable pages, and when they are fewer than
        learn_min_pages, search 1's too; the run stops when there are still too few (before any model call)."""
        usable = [page for page in pages if page.usable()]
        if self.searches or len(usable) >= int(Config.get("learn_min_pages")):
            return usable
        say.info("The documentation gave too little to learn from", "📖")
        return usable + self.usable_pages(self.first_search(), have=len(usable))[0]

    def learned(self, plan, version, machine, pages):
        """CLEAN, SELECT, DIGEST, FOLLOW-UP, CONDENSE and KEEP: the entry kept."""
        selector = Selector(self.embeddings, self.digest_room(plan, version))
        pool = self.cleaner.pool(pages)
        say.info(f"Split the pages into {len(pool)} passage(s), repeats dropped", "🧹")
        try:
            facts = self.digested(plan, version, self.selected(selector, plan, version, pool))
            facts += self.followed_up(plan, version, pool, selector, facts)
            rules = self.learner.condense(plan, facts, think=self.think()) if facts else []
        except (OllamaError, PromptTooLarge) as exc:
            raise SystemExit(f"Learning {self.topic} stopped: the model failed ({first_line(str(exc), 160)}). "
                             f"Nothing was saved.")
        if not facts:
            raise SystemExit(f"Nothing learned about {self.topic}: no passage answered a slot. Nothing was saved.")
        entry = self.kept(plan, machine, facts, rules, pool)
        self.recap = self.recap_of(entry, plan["profile"])
        return entry

    def digest_room(self, plan, version):
        """The chars a slot's passages may take in its digest prompt: what the topic and the longest question leave."""
        longest = max(Part("Question", slot.asked(plan, version), True).size() for slot in web_slots(plan["profile"]))
        topic = Part("Topic", f"{plan['name']} {version}", True).size()
        return self.model.limit_chars(Prompts.DIGEST_SYSTEM) - longest - topic - Part("Passages", "", True).size()

    def selected(self, selector, plan, version, pool, slots=None):
        """SELECT: each slot's passages from the one pool; a slot below its minimum is reported."""
        selected = selector.select(slots or web_slots(plan["profile"]), pool, plan, version)
        with say.section():
            for slot in short_slots(plan["profile"], selected):
                if slot.id in selected:
                    say.warn(f"Only {len(selected[slot.id])} passage(s) about {label(slot.id)}; {slot.minimum} wanted")
        return selected

    def digested(self, plan, version, selected):
        """DIGEST: each slot's passages read for the facts that answer its question (one call per slot that has
        passages), each fact citing its passages."""
        self.learner.step("digest", "Reading the passages for answers, one question at a time (the slow part)…",
                          "🧠")
        asked = [slot for slot in web_slots(plan["profile"]) if selected.get(slot.id)]
        facts, answered = [], 0
        for done, slot in enumerate(asked, 1):
            with say.working_on(f"Learning about {label(slot.id)} ({done} of {len(asked)})"):
                found = self.slot_facts(slot, plan, version, selected[slot.id])
            facts, answered = facts + found, answered + bool(found)
            say.tally(f"{done}/{len(asked)} questions · {len(facts)} fact(s)")
        self.outcome(f"{answered} of {len(asked)} question(s) answered, {len(facts)} fact(s)")
        return facts

    def slot_facts(self, slot, plan, version, passages):
        """One slot's facts; one stated twice is kept once."""
        parts = [Part("Topic", f"{plan['name']} {version}".strip(), True), Part("Question", slot.asked(plan, version), True),
                 Part("Passages", "\n\n".join(passage.listing() for passage in passages), True, exact=True)]
        found = self.model.ask_until_usable(Prompts.DIGEST_SYSTEM, parts, f"Digest {slot.id}",
                                            lambda reply: read_digest(reply.json(), slot, passages), attempts=2,
                                            think=self.think()) or []
        with say.section():
            if found:
                say.done(f"{label(slot.id)}: {len(found)} fact(s) from {len(passages)} passage(s)")
                say.found(first_line(found[0]["text"], 200))
            else:
                say.skip(f"{label(slot.id)}: nothing found in {len(passages)} passage(s)")
        return Learner.add_facts([], found)

    @staticmethod
    def think():
        return Config.get("learn_think")

    def followed_up(self, plan, version, pool, selector, facts):
        """FOLLOW-UP: each slot left without facts, while searches are left (learn_searches), gets the plan's
        follow-up search; its pages join the pool, and that slot alone is selected and digested again. A search
        that fails is no reason to stop: the library is then incomplete. The new facts."""
        found = []
        answered = {fact["slot"] for fact in facts}
        for slot in [slot for slot in web_slots(plan["profile"]) if slot.id not in answered]:
            if self.searches >= int(Config.get("learn_searches")):
                break
            query = slot.search(plan, version)
            self.learner.step("follow-up", f'Nothing found on {label(slot.id)} yet: searching the web for "{query}"…',
                              "🔁")
            pages = self.extra_pages(query, plan)
            if pages:
                pool += self.cleaner.pool(pages, known=pool)
                passages = self.selected(selector, plan, version, pool, [slot])[slot.id]
                with say.working_on(f"Learning about {label(slot.id)} from {len(pages)} more page(s)"):
                    found += self.slot_facts(slot, plan, version, passages) if passages else []
        return found

    def extra_pages(self, query, plan):
        """The usable pages one more search gives; [] when it fails, which marks the library incomplete."""
        try:
            picked = self.picked(plan["name"], self.search(query))
        except SearchFailed as failed:
            return self.extra_failed(query, LEARN_STOP_CAUSES[failed.cause][0])
        pages = self.fetched(picked)[0] if picked else []
        return pages or self.extra_failed(query, "no result about the topic gave usable text")

    def extra_failed(self, query, why):
        self.incomplete = True
        self.learner.notes.append(f'the extra search "{query}" failed: {why}; learned from the pages found before')
        with say.section():
            say.warn(self.learner.notes[-1])
        return []

    def slug(self, plan):
        return language_key(plan["language"]) if plan["kind"] == "language" else Library.slug(self.topic)

    def kept(self, plan, machine, facts, rules, pool):
        """KEEP: the entry saved under the language's key or the topic's slug, with what readiness found missing;
        a language's earlier proven copies retired; indexed for recall."""
        slug, language = self.slug(plan), plan["kind"] == "language"
        entry = self.entry(plan, machine, facts, rules, pool)
        self.learner.step("keep")
        Library.save(slug, entry)
        say.info(f"Saved {len(facts)} fact(s) about {plan['name']} from {len(entry['sources'])} page(s) as {slug}"
                 + (" (some questions are still open)" if entry["status"] == "incomplete" else ""), "💾")
        self.outcome(f"{len(facts)} fact(s) and {len(rules)} rule(s)")
        if language:
            self.learner.replace_proven(slug, entry)
        self.learner.index(slug, entry)
        return entry

    def entry(self, plan, machine, facts, rules, pool):
        """What is kept of a topic learned: the plan, the facts and rules with the passages they cite, what is
        missing, and how the learning went."""
        language = plan["kind"] == "language"
        gaps = missing(plan["profile"], facts, machine)
        cited = {*fact["passages"] for fact in facts}
        used = [passage for passage in pool if passage.id in cited]
        return {"topic": self.topic, "kind": plan["profile"], "name": plan["name"], "summary": plan["summary"],
                "language": plan["language"] if language else "", "version": plan["version"],
                "tools": [{"role": tool["role"], "name": tool["name"]} for tool in plan["tools"]],
                "commands": plan["commands"], "plan": {key: value for key, value in plan.items() if key != "dropped"},
                "learned": now_text(), "model": self.model.name, "machine": machine, "facts": facts, "rules": rules,
                "sources": list(dict.fromkeys(passage.url for passage in used)), "docs": self.docs,
                "passages": [{"id": passage.id, "url": passage.url, "fetched": passage.fetched,
                              "heading": passage.heading} for passage in used],
                "status": "incomplete" if gaps or self.incomplete else "complete", "missing": gaps,
                "requests": self.log.counts(), "notes": self.learner.notes}

    def recap_of(self, entry, profile):
        """READINESS and the run in a few lines, as data (the summary, which the chats show too): the slots
        filled and missing, the documentation, the pages, the facts, the requests."""
        filled = [label(slot.id) for slot in PROFILES[profile]
                  if slot.source != "check" and slot.id not in entry["missing"]]
        return [("Answered", ", ".join(filled) or "nothing"),
                ("Still open", ", ".join(map(label, entry["missing"])) or "nothing"),
                ("Documentation", ", ".join(f"{slug} {key}" for slug, key in self.docs.items()) or "none"),
                ("Pages", ", ".join(f"{count} {kind}" for kind, count in self.pages.items() if count) or "none"),
                ("Facts kept", str(len(entry["facts"]))),
                ("Web requests", self.log.text())]

    def stop(self, gate, cause, detail=""):
        """The run stops here, before any model call: LearnStopped, with why and what to do next."""
        what, next_step = LEARN_STOP_CAUSES[cause]
        before = (f"what was learned about it before ({self.known.get('learned', '?')}) is untouched" if self.known
                  else "there was no library for it before")
        raise LearnStopped("\n".join(message.line for message in [
            Message("stop", f"Learning {self.topic} stopped {LEARN_STOP_PLACES[gate]}: {what}" + (f" ({detail})" if detail else "")
                    + ".", 0, Messages.ICONS["stop"]),
            Message("info", f"Tried: {'; '.join(self.tried) or 'nothing yet'}", 1, "🔎"),
            Message("info", f"Web requests: {self.log.text()}", 1, "🌐"),
            Message("info", f"Nothing was learned or saved; {before}.", 1, "💾"),
            Message("tip", f"Next: {next_step}", 1, "👉")]),
            [("Why", f"stopped {LEARN_STOP_PLACES[gate]}: {what}"), ("Next", next_step)])


def label(slot_id):
    """A slot as a person reads it: "core-usage" -> "core usage"."""
    return slot_id.replace("-", " ")


def version_of(machine, commands):
    """The version the first of these commands that worked showed ("" when none did)."""
    return next((tool["version"] for tool in machine if tool["command"] in commands and tool["found"]
                 and tool["version"]), "")


def read_plan(data):
    """A slot plan, checked: its kind (language, tool or domain) and profile, a name, its version and summary,
    the language (a language's own; a tool's goes in used_with), its documentation site, up to MAX_VERSION_COMMANDS
    commands that only show what is installed (any other is "dropped"), a language's tools (one per role of
    TOOL_ROLES, each with its version command and documentation), and the hints and follow-up searches of its
    profile's slots."""
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    kind, name = clean(data.get("kind"), 20).lower(), clean(data.get("name"), 80)
    if kind not in ("language", "tool", "domain"):
        raise ValueError('"kind" must be "language", "tool" or "domain"')
    if not name:
        raise ValueError("it needs the topic's name")
    profile = "language" if kind == "language" else "tool"
    ids = {slot.id for slot in web_slots(profile)}
    language = clean(data.get("language"), 40)
    commands, dropped = Learner.safe_commands(data.get("commands") or [])
    tools, tool_dropped = read_tools(data.get("tools")) if kind == "language" else ([], [])
    return {"kind": kind, "profile": profile, "name": name, "version": clean(data.get("version"), 20),
            "summary": clean(data.get("summary"), 200) or name,
            "language": (language or name) if kind == "language" else "",
            "used_with": "" if kind == "language" else language or clean(data.get("used_with"), 40),
            "docs": read_docs(data.get("docs")), "commands": commands[:MAX_VERSION_COMMANDS], "dropped": dropped + tool_dropped,
            "tools": tools, "hints": by_slot(data.get("hints"), ids), "followups": by_slot(data.get("followups"), ids)}


def read_tools(given):
    """([one tool per role of TOOL_ROLES, as {role, name, command, docs}], [its commands that were not safe])."""
    tools, dropped = {}, []
    for tool in given if isinstance(given, list) else []:
        if isinstance(tool, dict):
            role, name = clean(tool.get("role"), 20).lower(), clean(tool.get("name"), 40)
            if role in TOOL_ROLES and name and role not in tools:
                kept, unsafe = Learner.safe_commands([tool.get("command") or ""])
                dropped += unsafe
                tools[role] = {"role": role, "name": name, "command": (kept or [""])[0], "docs": read_docs(tool.get("docs"))}
    return list(tools.values()), dropped


def read_docs(given):
    """A documentation site as {root, versioned}: web addresses only; versioned only with {version} in it."""
    given = given if isinstance(given, dict) else {}
    root, versioned = clean(given.get("root"), 300), clean(given.get("versioned"), 300)
    return {"root": root if is_web_address(root) else "",
            "versioned": versioned if is_web_address(versioned) and "{version}" in versioned else ""}


def by_slot(given, ids):
    """{slot id: text} for the profile's slots only."""
    return {key: clean(text, 160) for key, text in (given.items() if isinstance(given, dict) else [])
            if key in ids and clean(text, 160)}


def read_digest(data, slot, passages):
    """A slot's facts, checked: each with its text (cut to MAX_FACT_CHARS), a known phase (else the slot's section)
    and the passages it cites among those given; a fact that cites none, or speaks of an unreleased version, is
    dropped. At most FACTS_PER_SLOT. Each keeps its slot, the passages it cites and their pages (source, also)."""
    by_id = {passage.id: passage for passage in passages}
    facts = []
    for item in (data.get("facts") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        text, phase = clean(item.get("text"), MAX_FACT_CHARS), clean(item.get("phase"), 20).lower()
        cited = list(dict.fromkeys(str(citation).strip("[] ") for citation in item.get("cites") or []))
        cited = [passage_id for passage_id in cited if passage_id in by_id]
        if not text or not cited or Learner.UNRELEASED.search(text):
            continue
        pages = list(dict.fromkeys(by_id[passage_id].url for passage_id in cited))
        facts.append({"phase": phase if phase in FACT_PHASES else slot.section, "text": text, "slot": slot.id,
                      "passages": cited, "source": pages[0], **({"also": pages[1:]} if pages[1:] else {})})
    return facts[:FACTS_PER_SLOT]


def clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def address_key(url):
    """An address as one page: without its #fragment and a trailing slash."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


class Throttle:
    """At most rate requests a minute: a token bucket, full at the start, so a learn's few requests go at once and
    a burst waits its turn. HTTP 429 is waited out with growing pauses (FIRST_BACKOFF_SECONDS, doubled) for at most backoff
    seconds from the first one; past that, RateLimited, so a learn that cannot go on stops quickly."""

    def __init__(self, rate, backoff, clock=None, sleep=None):
        self.rate, self.backoff = float(rate), float(backoff)
        self.clock, self.sleep = clock or time.monotonic, sleep or time.sleep
        self.tokens, self.filled = self.rate, self.clock()

    def call(self, request, limited):
        """request()'s result, asked again while limited() says it was rate limited, within the budget."""
        deadline, pause = None, FIRST_BACKOFF_SECONDS
        while True:
            self.take()
            result = request()
            if not limited():
                return result
            now = self.clock()
            deadline = now + self.backoff if deadline is None else deadline
            if now >= deadline:
                raise RateLimited()
            self.sleep(min(pause, deadline - now))
            pause *= 2

    def take(self):
        """One token, waiting for it when the bucket is empty."""
        self.refill()
        if self.tokens < 1:
            self.sleep((1 - self.tokens) * 60 / self.rate)
            self.refill()
        self.tokens -= 1

    def refill(self):
        now = self.clock()
        self.tokens = min(self.rate, self.tokens + (now - self.filled) * self.rate / 60)
        self.filled = now


class RequestLog:
    """Every web request a learn makes: one JSON line in the home's web log (when, kind, result, seconds, address),
    so the endpoint that hits a rate limit shows; and counted by kind (search, fetch, robots, fallback) for the
    summary."""

    def __init__(self):
        self.path, self.tally, self.lock = Home.web_log(), {}, threading.Lock()

    def __call__(self, kind, result, seconds, target):
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "result": result,
                           "seconds": round(seconds, 3), "target": target})
        with self.lock:
            counts = self.tally.setdefault(kind, Counter())
            counts.update(requests=1, **{result: 1})
            with self.path.open("a") as log:
                log.write(line + "\n")

    def counts(self):
        """{kind: {requests, ok, failed, 429}}."""
        return {kind: dict(counts) for kind, counts in self.tally.items()}

    def text(self):
        """"search 1, fetch 8 (2 failed), fallback 1; HTTP 429: 0", or "none"."""
        if not self.tally:
            return "none"
        shown = [f"{kind} {counts['requests']}" + (f" ({counts['failed']} failed)" if counts["failed"] else "")
                 for kind, counts in self.tally.items()]
        return ", ".join(shown) + f"; HTTP 429: {sum(counts['429'] for counts in self.tally.values())}"
