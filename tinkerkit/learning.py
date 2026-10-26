from collections import namedtuple
lazy import datetime
lazy import urllib.parse
lazy from collections import Counter

from .messages import Message, Messages, say
lazy from .jobs import today
lazy from .knowledge import LEARN_STEPS, Learner, Library, Researcher, Retriever, language_key
lazy from .model import OllamaError
lazy from .passages import Cleaner, Page
lazy from .project import Tracked
lazy from .prompts import Part, Prompts, PromptTooLarge
lazy from .settings import (
    DOCS_SITE_SEARCH,
    DOCS_VERSION_SEARCH,
    FACT_PHASES,
    FACTS_PER_SLOT,
    MAX_FACT_CHARS,
    MAX_VERSION_COMMANDS,
    RESULTS_PER_LEARN_SEARCH,
    TOOL_ROLES,
    Config,
    Home,
)
lazy from .slots import PROFILES, TOOL_DOCS_SEARCH, Selector, fill, missing, short_slots, web_slots
lazy from .text import first_line, now_text, one_line

LEARN_STOP_CAUSES = frozendict(
    {
        "embeddings": (
            "no embedding model is selected",
            "select one with tinker embed NAME, then learn again",
        ),
        "no-results": (
            "the search found nothing",
            "check the topic's spelling, or name it more fully; if the web said it could not search, "
            "learn again later",
        ),
        "irrelevant": (
            "no search result is about the topic",
            "name the topic more fully (with its language, say)",
        ),
        "pages": (
            "too few pages could be fetched",
            "learn again later: a site may refuse fetches or draw its pages with scripts",
        ),
    }
)

LEARN_STOP_PLACES = frozendict(
    {
        "preflight": "before starting",
        "gate 1": "at the first web search",
        "gate 2": "while reading pages",
    }
)
RECAP_LINE_ICONS = ("✅", "❓", "📖", "📄", "🧩", "🌐")


Tools = namedtuple("Tools", "tools dropped")
PlanAndPages = namedtuple("PlanAndPages", "plan pages")
UsablePages = namedtuple("UsablePages", "pages results")
WantedDocs = namedtuple("WantedDocs", "slug key name query cap")


class LearnStopped(SystemExit):
    def __init__(self, message, details=()):
        super().__init__(message)
        self.details = list(details)


class DocCache:
    @staticmethod
    def get(slug, key):
        kept = Home.store().get("sources", f"{slug}@{key}")
        if not isinstance(kept, dict) or not kept.get("pages"):
            return None
        if key == "latest" and DocCache.stale(kept):
            return None
        return [Page.of_dict(page) for page in kept["pages"]]

    @staticmethod
    def stale(kept):
        return DocCache.age_days(kept.get("kept", "")) > float(Config.get("docs_latest_days"))

    @staticmethod
    def put(slug, key, pages):
        Home.store().put(
            "sources",
            f"{slug}@{key}",
            {"version": key, "kept": now_text(), "pages": [page.as_dict() for page in pages]},
        )

    @staticmethod
    def age_days(kept):
        try:
            return (datetime.datetime.now() - datetime.datetime.strptime(kept, "%Y-%m-%d %H:%M")).days
        except ValueError:
            return float("inf")


class SlotLearner(Tracked):
    STEPS = LEARN_STEPS

    def __init__(self, model, internet, embeddings, tracker=None):
        self.tracker = tracker
        self.learner = Learner(model, internet, embeddings)
        self.learner.tracker = tracker
        self.model, self.internet, self.embeddings = model, internet, embeddings
        self.cleaner = Cleaner()
        self.topic, self.known, self.tried, self.seen, self.docs = "", None, [], set(), {}
        self.searched_first, self.follow_ups, self.incomplete = False, 0, False
        self.pages = Counter()
        self.recap = []

    def learn(self, topic, deep=False):
        known = Library.read(Library.slug(topic))
        if (known or {}).get("kind") == "research":
            return Researcher(self.learner).research(topic) if deep else known
        self.learner.start()
        self.topic, self.known = topic, known or Library.read(Library.language(topic) or "")
        with say.part(f"Learning {topic}", "📚"):
            entry = self.learned_by_slots(deep)
        if self.recap:
            say.summary(
                f"Learned {entry['name']}", [(icon, *row) for icon, row in zip(RECAP_LINE_ICONS, self.recap)]
            )
        return entry

    def learned_by_slots(self, deep):
        self.preflight()
        plan, pages = self.plan_and_pages()
        if plan["kind"] == "domain" and deep:
            say.info(
                f"{self.topic} is a broad subject, not one tool: researching it and cross-checking the sources",
                "🧭",
            )
            return Researcher(self.learner).research(self.topic)
        each_command_once = list(
            dict.fromkeys(plan["commands"] + [tool["command"] for tool in plan["tools"] if tool["command"]])
        )
        machine = self.learner.look_at_machine(each_command_once)
        version = version_of(machine, plan["commands"]) or plan["version"]
        pages = self.enough_pages(pages + self.documentation(plan, machine, version))
        return self.learned(plan, version, machine, pages)

    def preflight(self):
        if self.embeddings is None:
            self.stop("preflight", "embeddings")

    def plan_and_pages(self):
        saved = self.saved_plan()
        if saved:
            return PlanAndPages(saved, [])
        pages, results = self.usable_pages(self.first_search())
        return PlanAndPages(self.asked_plan(results), pages)

    def saved_plan(self):
        saved = (self.known or {}).get("plan")
        if not saved:
            return None
        try:
            plan = read_plan(saved)
        except ValueError as exc:
            say.warn(f"The plan saved last time cannot be used ({exc}), so making a new one")
            return None
        self.step("plan", "Using the plan saved last time: no web search needed to start", "📋")
        return plan

    def first_search(self):
        self.step("search", f"Searching the web for pages about {self.topic}…", "🔎")
        self.searched_first = True
        results = self.search(DOCS_SITE_SEARCH.format(topic=self.topic))
        if not results:
            self.stop("gate 1", "no-results")
        picked = self.picked(self.topic, results)
        if not picked:
            self.stop("gate 1", "irrelevant", f"{len(results)} result(s), none above learn_cutoff")
        return picked

    def search(self, query):
        self.tried.append(f'search "{query}"')
        return self.internet.search(query, RESULTS_PER_LEARN_SEARCH)

    def picked(self, topic, results, most=None):
        scores = Retriever(None).word_scores(
            topic, [f"{result['title']} {result['content']}" for result in results]
        )
        picked, cutoff, most = [], float(Config.get("learn_cutoff")), most or int(Config.get("learn_pages"))
        for result, score in zip(results, scores):
            key = address_key(result["url"])
            if score > cutoff and key not in self.seen and len(picked) < most:
                self.seen.add(key)
                picked.append(result)
        return picked

    def usable_pages(self, picked, have=0):
        pages, results = self.fetched(picked)
        needed = int(Config.get("learn_min_pages"))
        if len(pages) + have < needed:
            self.stop("gate 2", "pages", f"{len(pages) + have} page(s) gave usable text; {needed} needed")
        return pages, results

    def fetched(self, picked):
        got = self.internet.fetch([result["url"] for result in picked])
        with say.section():
            pages = [self.page_of(web_page) for web_page in got]
            usable = [(page, result) for page, result in zip(pages, picked) if page.usable()]
            self.pages.update(searched=len(usable), failed=len(pages) - len(usable))
            self.tried.append(f"fetched {len(picked)} page(s), {len(usable)} with usable text")
            say.info(f"Read {len(usable)} of {len(picked)} page(s) found", "📄")
        self.outcome(f"{len(usable)} of {len(picked)} page(s) read")
        return UsablePages([page for page, _ in usable], [result for _, result in usable])

    @staticmethod
    def page_of(web_page):
        if web_page.why:
            say.fail(f"Could not open {web_page.url} ({web_page.why})")
        return Page(web_page.url, now_text(), web_page.blocks)

    def asked_plan(self, results):
        self.step(
            "plan",
            f"Working out what {self.topic} is and how to check the version you have…",
            "🤔",
        )
        with say.working_on(f"Working out what {self.topic} is, from {len(results)} page(s) found"):
            plan = self.model.ask_until_usable(
                Prompts.SLOT_PLAN_SYSTEM,
                self.plan_parts(results),
                "Plan learning",
                lambda reply: read_plan(reply.json()),
                attempts=2,
            ).needed(f"{self.model.name} gave no usable plan for learning {self.topic}. Nothing was saved.")
        self.say_plan(plan)
        return plan

    def say_plan(self, plan):
        say.found(f"{plan['name']} is a {plan['kind']}: {plan['summary']}")
        self.outcome(f"{plan['name']}, a {plan['kind']}")
        self.learner.show_dropped(plan)

    def plan_parts(self, results):
        found = "\n".join(
            f"- {result['title']} ({result['url']}): {first_line(result['content'], 200)}"
            for result in results
        )
        slots = lambda profile: "\n".join(
            f"- {slot.id}: {fill(slot.question, {'name': self.topic}, '')}" for slot in web_slots(profile)
        )
        return [
            Part("Topic", self.topic, True),
            Part("Today", today().isoformat(), True),
            Part("Pages found", found, True),
            Part("Slots of a language", slots("language"), True),
            Part("Slots of a tool", slots("tool"), True),
        ]

    def documentation(self, plan, machine, version):
        self.step("documentation")
        tools = {tool["name"]: tool for tool in plan["tools"]}.values()
        wanted = [self.language_docs(plan, version)] + [self.tool_docs(machine, tool) for tool in tools]
        pages = [page for docs_pages in self.doc_pages(wanted) for page in docs_pages]
        self.outcome(f"{len(pages)} page(s)" + (f" of {plan['name']} {version}" if version else ""))
        return pages

    def language_docs(self, plan, version):
        return wanted_docs(
            self.slug(plan), plan["name"], version, DOCS_VERSION_SEARCH, int(Config.get("learn_pages"))
        )

    @staticmethod
    def tool_docs(machine, tool):
        return wanted_docs(
            Library.slug(tool["name"]),
            tool["name"],
            version_of(machine, [tool["command"]]),
            TOOL_DOCS_SEARCH,
            int(Config.get("tool_docs_pages")),
        )

    def doc_pages(self, wanted):
        wanted = [docs for docs in wanted if docs.cap > 0]
        kept = [DocCache.get(docs.slug, docs.key) for docs in wanted]
        missing = [docs.query for docs, pages in zip(wanted, kept) if not pages]
        found = dict(zip(missing, self.searched_all(missing)))
        return [self.docs_of(docs, pages, found.get(docs.query)) for docs, pages in zip(wanted, kept)]

    def searched_all(self, queries):
        if not queries:
            return []
        self.tried += [f'search "{query}"' for query in queries]
        quoted = ", ".join(f'"{query}"' for query in queries)
        with say.working_on(f"Searching the web for {quoted}"):
            return self.internet.search_all(queries, RESULTS_PER_LEARN_SEARCH)

    def docs_of(self, docs, kept, results):
        pages = kept or self.searched_docs(docs, results)
        if pages:
            self.docs[docs.slug] = docs.key
        self.pages.update(**{"kept docs" if kept else "docs": len(pages)})
        say_docs(docs.slug, docs.key, pages, kept)
        return pages

    def searched_docs(self, docs, results):
        picked = self.picked(docs.name, results, docs.cap) if results else []
        with say.working_on(f'Reading the pages found for "{docs.query}"'):
            fetched = self.internet.fetch([result["url"] for result in picked]) if picked else []
        pages = [page for page in map(self.page_of, fetched) if page.usable()]
        if pages:
            DocCache.put(docs.slug, docs.key, pages)
        return pages

    def enough_pages(self, pages):
        usable = [page for page in pages if page.usable()]
        if self.searched_first or len(usable) >= int(Config.get("learn_min_pages")):
            return usable
        say.info("The documentation gave too little to learn from", "📖")
        return usable + self.usable_pages(self.first_search(), have=len(usable))[0]

    def learned(self, plan, version, machine, pages):
        selector = Selector(self.embeddings, self.digest_room(plan, version))
        pool = self.cleaner.pool(pages)
        say.info(f"Split the pages into {len(pool)} passage(s), repeats dropped", "🧹")
        try:
            facts = self.digested(plan, version, self.selected(selector, plan, version, pool))
            facts += self.followed_up(plan, version, pool, selector, facts)
            rules = self.learner.condense(plan, facts, think=self.think()) if facts else []
        except (OllamaError, PromptTooLarge) as exc:
            raise SystemExit(
                f"Learning {self.topic} stopped: the model failed ({first_line(str(exc), 160)}). "
                f"Nothing was saved."
            )
        if not facts:
            raise SystemExit(
                f"Nothing learned about {self.topic}: no passage answered a slot. Nothing was saved."
            )
        entry = self.kept(plan, machine, facts, rules, pool)
        self.recap = self.recap_of(entry, plan["profile"])
        return entry

    def digest_room(self, plan, version):
        longest = max(
            Part("Question", slot.asked(plan, version), True).size() for slot in web_slots(plan["profile"])
        )
        topic = Part("Topic", f"{plan['name']} {version}", True).size()
        return (
            self.model.limit_chars(Prompts.DIGEST_SYSTEM)
            - longest
            - topic
            - Part("Passages", "", True).size()
        )

    def selected(self, selector, plan, version, pool, slots=None):
        selected = selector.select(slots or web_slots(plan["profile"]), pool, plan, version)
        with say.section():
            for slot in short_slots(plan["profile"], selected):
                if slot.id in selected:
                    say.warn(
                        f"Only {len(selected[slot.id])} passage(s) about {label(slot.id)}; {slot.minimum} wanted"
                    )
        return selected

    def digested(self, plan, version, selected):
        self.step("digest", "Reading the passages for answers, one question at a time (the slow part)…", "🧠")
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
        parts = [
            Part("Topic", f"{plan['name']} {version}".strip(), True),
            Part("Question", slot.asked(plan, version), True),
            Part("Passages", "\n\n".join(passage.listing() for passage in passages), True, exact=True),
        ]
        found = (
            self.model.ask_until_usable(
                Prompts.DIGEST_SYSTEM,
                parts,
                f"Digest {slot.id}",
                lambda reply: read_digest(reply.json(), slot, passages),
                attempts=2,
                think=self.think(),
            ).value
            or []
        )
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
        found = []
        answered = {fact["slot"] for fact in facts}
        for slot in [slot for slot in web_slots(plan["profile"]) if slot.id not in answered]:
            if self.follow_ups >= int(Config.get("learn_searches")):
                break
            self.follow_ups += 1
            query = slot.search(plan, version)
            self.step(
                "follow-up", f'Nothing found on {label(slot.id)} yet: searching the web for "{query}"…', "🔁"
            )
            pages = self.extra_pages(query, plan)
            if pages:
                pool += self.cleaner.pool(pages, known=pool)
                passages = self.selected(selector, plan, version, pool, [slot])[slot.id]
                with say.working_on(f"Learning about {label(slot.id)} from {len(pages)} more page(s)"):
                    found += self.slot_facts(slot, plan, version, passages) if passages else []
        return found

    def extra_pages(self, query, plan):
        results = self.search(query)
        if not results:
            return self.extra_failed(query, LEARN_STOP_CAUSES["no-results"][0])
        picked = self.picked(plan["name"], results)
        pages = self.fetched(picked)[0] if picked else []
        return pages or self.extra_failed(query, "no result about the topic gave usable text")

    def extra_failed(self, query, why):
        self.incomplete = True
        self.learner.notes.append(
            f'the extra search "{query}" failed: {why}; learned from the pages found before'
        )
        with say.section():
            say.warn(self.learner.notes[-1])
        return []

    def slug(self, plan):
        return language_key(plan["language"]) if plan["kind"] == "language" else Library.slug(self.topic)

    def kept(self, plan, machine, facts, rules, pool):
        slug, language = self.slug(plan), plan["kind"] == "language"
        entry = self.entry(plan, machine, facts, rules, pool)
        self.step("keep")
        Library.save(slug, entry)
        say.info(
            f"Saved {len(facts)} fact(s) about {plan['name']} from {len(entry['sources'])} page(s) as {slug}"
            + (" (some questions are still open)" if entry["status"] == "incomplete" else ""),
            "💾",
        )
        self.outcome(f"{len(facts)} fact(s) and {len(rules)} rule(s)")
        if language:
            self.learner.replace_proven(slug, entry)
        self.learner.index(slug, entry)
        return entry

    def entry(self, plan, machine, facts, rules, pool):
        language = plan["kind"] == "language"
        gaps = missing(plan["profile"], facts, machine)
        cited = {*fact["passages"] for fact in facts}
        used = [passage for passage in pool if passage.id in cited]
        return {
            "topic": self.topic,
            "kind": plan["profile"],
            "name": plan["name"],
            "summary": plan["summary"],
            "language": plan["language"] if language else "",
            "version": plan["version"],
            "tools": [{"role": tool["role"], "name": tool["name"]} for tool in plan["tools"]],
            "commands": plan["commands"],
            "plan": {key: value for key, value in plan.items() if key != "dropped"},
            "learned": now_text(),
            "model": self.model.name,
            "machine": machine,
            "facts": facts,
            "rules": rules,
            "sources": list(dict.fromkeys(passage.url for passage in used)),
            "docs": self.docs,
            "passages": [
                {"id": passage.id, "url": passage.url, "fetched": passage.fetched, "heading": passage.heading}
                for passage in used
            ],
            "status": "incomplete" if gaps or self.incomplete else "complete",
            "missing": gaps,
            "requests": self.internet.requests.counts(),
            "notes": self.learner.notes,
        }

    def recap_of(self, entry, profile):
        filled = [
            label(slot.id)
            for slot in PROFILES[profile]
            if slot.source != "check" and slot.id not in entry["missing"]
        ]
        return [
            ("Answered", ", ".join(filled) or "nothing"),
            ("Still open", ", ".join(map(label, entry["missing"])) or "nothing"),
            ("Documentation", ", ".join(f"{slug} {key}" for slug, key in self.docs.items()) or "none"),
            ("Pages", ", ".join(f"{count} {kind}" for kind, count in self.pages.items() if count) or "none"),
            ("Facts kept", str(len(entry["facts"]))),
            ("Web requests", self.internet.requests.text()),
        ]

    def stop(self, gate, cause, detail=""):
        what, next_step = LEARN_STOP_CAUSES[cause]
        raise LearnStopped(
            "\n".join(message.line for message in self.stop_messages(gate, what, detail, next_step)),
            [("Why", f"stopped {LEARN_STOP_PLACES[gate]}: {what}"), ("Next", next_step)],
        )

    def stop_messages(self, gate, what, detail, next_step):
        before = (
            f"what was learned about it before ({self.known.get('learned', '?')}) is untouched"
            if self.known
            else "there was no library for it before"
        )
        stopped = f"Learning {self.topic} stopped {LEARN_STOP_PLACES[gate]}: {what}"
        return [
            Message("stop", stopped + (f" ({detail})" if detail else "") + ".", 0, Messages.ICONS["stop"]),
            Message("info", f"Tried: {'; '.join(self.tried) or 'nothing yet'}", 1, "🔎"),
            Message("info", f"Web requests: {self.internet.requests.text()}", 1, "🌐"),
            Message("info", f"Nothing was learned or saved; {before}.", 1, "💾"),
            Message("tip", f"Next: {next_step}", 1, "👉"),
        ]


def label(slot_id):
    return slot_id.replace("-", " ")


def version_of(machine, commands):
    return next(
        (
            tool["version"]
            for tool in machine
            if tool["command"] in commands and tool["found"] and tool["version"]
        ),
        "",
    )


def wanted_docs(slug, name, version, search, cap):
    query = " ".join(search.format(name=name, version=version).split())
    return WantedDocs(slug, version or "latest", name, query, cap)


def say_docs(slug, key, pages, kept):
    if not pages:
        say.info(f"Found no {slug} {key} documentation", "📖")
        return
    saved = " (the copy saved last time)" if kept else ""
    say.info(f"Read {len(pages)} page(s) of the {slug} {key} documentation{saved}", "📖")


def read_plan(data):
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
    return {
        "kind": kind,
        "profile": profile,
        "name": name,
        "version": clean(data.get("version"), 20),
        "summary": clean(data.get("summary"), 200) or name,
        "language": (language or name) if kind == "language" else "",
        "used_with": "" if kind == "language" else language or clean(data.get("used_with"), 40),
        "commands": commands[:MAX_VERSION_COMMANDS],
        "dropped": dropped + tool_dropped,
        "tools": tools,
        "hints": by_slot(data.get("hints"), ids),
        "followups": by_slot(data.get("followups"), ids),
    }


def read_tools(given):
    tools, dropped = {}, []
    for tool in given if isinstance(given, list) else []:
        if isinstance(tool, dict):
            role, name = clean(tool.get("role"), 20).lower(), clean(tool.get("name"), 40)
            if role in TOOL_ROLES and name and role not in tools:
                kept, unsafe = Learner.safe_commands([tool.get("command") or ""])
                dropped += unsafe
                tools[role] = {"role": role, "name": name, "command": (kept or [""])[0]}
    return Tools(list(tools.values()), dropped)


def by_slot(given, ids):
    return {
        key: clean(text, 160)
        for key, text in (given.items() if isinstance(given, dict) else [])
        if key in ids and clean(text, 160)
    }


def read_digest(data, slot, passages):
    by_id = {passage.id: passage for passage in passages}
    facts = []
    for item in (data.get("facts") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        text, phase = clean(item.get("text"), MAX_FACT_CHARS), clean(item.get("phase"), 20).lower()
        cited = list(dict.fromkeys(str(citation).strip("[] ") for citation in item.get("cites") or []))
        cited = [passage_id for passage_id in cited if passage_id in by_id]
        if not text or not cited or Learner.UNRELEASED_RE.search(text):
            continue
        pages = list(dict.fromkeys(by_id[passage_id].url for passage_id in cited))
        facts.append(
            {
                "phase": phase if phase in FACT_PHASES else slot.section,
                "text": text,
                "slot": slot.id,
                "passages": cited,
                "source": pages[0],
                **({"also": pages[1:]} if pages[1:] else {}),
            }
        )
    return facts[:FACTS_PER_SLOT]


def clean(value, limit):
    return one_line(value)[:limit]


def address_key(url):
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, "")
    )
