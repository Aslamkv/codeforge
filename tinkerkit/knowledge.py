import re
from collections import namedtuple
from itertools import batched, combinations
lazy import collections
lazy import hashlib
lazy import math
lazy import tempfile
lazy from pathlib import Path

from .messages import say
from .settings import (
    CHUNKS_PER_PAGE,
    EMBED_KEEP_ALIVE,
    EVENTS_KEPT_PER_ITEM,
    FACT_PHASES,
    FACTS_PER_CHUNK,
    LESSON_PHASES,
    LESSONS_PER_PROMPT,
    MAX_FACT_CHARS,
    MAX_LESSONS_PER_FILE,
    MAX_PLANNED_SEARCHES,
    MAX_RULE_CHARS,
    MAX_RULE_WHEN_CHARS,
    PLAYBOOK_RULES,
    REPEAT_CLAIM_SIMILARITY,
    RESEARCH_FACTS_CHECKED,
    RESEARCH_GAPS,
    RESEARCH_PAGES_PER_ROUND,
    RESEARCH_QUESTIONS,
    RESULTS_PER_RESEARCH_SEARCH,
    RETIRED_KEPT_PER_LIST,
    SAME_CLAIM_SIMILARITY,
    SAME_FACT_SIMILARITY,
    SAME_LESSON_SIMILARITY,
    SEARCHES_PER_RESEARCH_QUESTION,
    TEACH_PAGE_CHARS,
    TEACH_PAGES,
    TOOLCHAIN_HINT_SIMILARITY,
    VERSION_TIMEOUT,
    Config,
    Home,
)
from .text import field, fingerprint, first_line, host, now_text, one_line, similarity, split_text, words
lazy from .jobs import today
lazy from .model import Answer, Models, Ollama, OllamaError
lazy from .project import Tracked
lazy from .prompts import Part, Prompts, Reply
lazy from .workspace import Toolchain, Workspace

SavedPlan = namedtuple("SavedPlan", "plan saved")
SafeCommands = namedtuple("SafeCommands", "kept dropped")
Findings = namedtuple("Findings", "facts sources")
Investigation = namedtuple("Investigation", "facts sources analysis")
CrossCheck = namedtuple("CrossCheck", "support differ")


class Item(namedtuple("Item", "kind id topic", defaults=("",))):
    TOPIC_KINDS = ("fix", "fact", "finding", "rule")
    CLAIM_KINDS = ("fact", "finding", "rule")

    @property
    def key(self):
        named = (self.kind, self.topic, self.id) if self.kind in Item.TOPIC_KINDS else (self.kind, self.id)
        return ":".join(named)

    @classmethod
    def of(cls, key):
        kind, _, rest = key.partition(":")
        if kind not in Item.TOPIC_KINDS:
            return cls(kind, rest)
        topic, _, item_id = rest.partition(":")
        return cls(kind, item_id, topic)


STORED_AS = frozendict({"lesson": ("lessons", "id"), "known": ("facts", "id"), "fix": ("fixes", "failure")})
LEARN_STEPS = (
    "search",
    "plan",
    "machine",
    "documentation",
    "digest",
    "follow-up",
    "condense",
    "frame",
    "investigate",
    "keep",
    "done",
)


class Knowledge:
    def __init__(self, language, run=None):
        self.language = language or "Python"
        self.key = language_key(self.language)
        self.run = run
        self.language_doc = Home.store().get("languages", self.key) or {
            "language": self.language,
            "projects": 0,
            "toolchain": None,
            "fixes": [],
            "lessons": [],
            "facts": [],
        }
        self.any_language_doc = Home.store().get("memory", "any") or {"lessons": []}

    @staticmethod
    def named_in(text):
        known = [doc.get("language") for doc in Home.store().items("languages").values()] + [
            entry.get("language") for entry in Library.topics().values() if entry.get("kind") == "language"
        ]
        named = [
            name
            for name in dict.fromkeys(known_name for known_name in known if known_name)
            if mentions(text or "", name)
        ]
        return min(named, key=lambda name: (text or "").lower().index(name.lower()), default=None)

    def summary(self):
        lessons = len(self.language_doc.get("lessons", [])) + len(self.any_language_doc.get("lessons", []))
        projects, facts = self.language_doc.get("projects", 0), len(self.language_doc.get("facts", []))
        known = [
            ("the toolchain that worked" + (f" ({projects} finished project(s))" if projects else ""))
            if self.language_doc.get("toolchain")
            else "",
            f"{len(self.language_doc.get('fixes', []))} toolchain fix(es)"
            if self.language_doc.get("fixes")
            else "",
            f"{lessons} lesson(s)" if lessons else "",
            f"{facts} proven fact(s)" if facts else "",
        ]
        return f"{self.language}: " + (", ".join(topic for topic in known if topic) or "nothing learned yet")

    def lessons(self, phases, limit=LESSONS_PER_PROMPT):
        pool = [lesson for lesson in self.known_lessons() if lesson.get("phase") in phases]
        pool.sort(key=Knowledge.rank, reverse=True)
        return [
            dict(lesson, id=lesson.get("id") or fingerprint(lesson["text"])[:12]) for lesson in pool[:limit]
        ]

    @staticmethod
    def rank(lesson):
        return lesson.get("seen", 1) + lesson.get("helped", 0), lesson.get("last_seen", "")

    def facts(self, phases):
        return [fact for fact in self.language_doc.get("facts", []) if fact.get("phase") in phases]

    def known_lessons(self):
        return self.language_doc.setdefault("lessons", []) + self.any_language_doc.setdefault("lessons", [])

    def proven_lessons(self):
        return [lesson for lesson in self.language_doc.get("lessons", []) if lesson.get("status") == "proven"]

    def add_lessons(self, new, confirmed, names=()):
        known, added = self.known_lessons(), 0
        for number in confirmed:
            if isinstance(number, int) and 1 <= number <= len(known):
                self.confirm(known[number - 1])
        for item in new:
            lesson = self.new_lesson(item, known, names)
            if lesson:
                (self.language_doc if item.get("scope") == "language" else self.any_language_doc)[
                    "lessons"
                ].append(lesson)
                known.append(lesson)
                added += 1
        for doc in (self.language_doc, self.any_language_doc):
            doc["lessons"] = self.keep(doc["lessons"], known[len(known) - added :])
        self.save()
        return added

    def new_lesson(self, item, known, names):
        text, phase = field(item, "text", 240), field(item, "phase").lower()
        if not text or phase not in LESSON_PHASES or self.names_project(text, names):
            return None
        same = next(
            (lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON_SIMILARITY), None
        )
        if same:
            self.confirm(same)
            return None
        lesson = {
            "id": fingerprint(text)[:12],
            "phase": phase,
            "text": text,
            "status": "candidate",
            "seen": 1,
            "added": now_text(),
            "last_seen": now_text(),
        }
        return record(lesson, "born", self.run)

    @staticmethod
    def names_project(text, names):
        return any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text, re.I) for name in names if name)

    def confirm(self, lesson):
        lesson["seen"], lesson["last_seen"] = lesson.get("seen", 1) + 1, now_text()
        record(lesson, "confirmed", self.run)

    @staticmethod
    def keep(lessons, new):
        rank = Knowledge.rank
        fresh = [lesson for lesson in lessons if any(lesson is new_lesson for new_lesson in new)]
        older = sorted(
            (lesson for lesson in lessons if not any(lesson is new_lesson for new_lesson in new)),
            key=rank,
            reverse=True,
        )
        return sorted(fresh + older[: max(0, MAX_LESSONS_PER_FILE - len(fresh))], key=rank, reverse=True)

    def prove(self, used):
        used = items_of(used)
        proven = self.prove_lessons(used) + self.prove_web_facts(used)
        for fact in self.language_doc.get("facts", []):
            if Item("known", fact["id"]) in used:
                record(fact, "helped", self.run)
        self.save()
        return proven

    def prove_lessons(self, used):
        proven = 0
        for lesson in self.known_lessons():
            if Item("lesson", lesson.get("id")) in used:
                proven += lesson.get("status") != "proven"
                record(lesson, "helped", self.run)["status"] = "proven"
        return proven

    def prove_web_facts(self, used):
        proven, known = 0, {fact["id"] for fact in self.language_doc.setdefault("facts", [])}
        for slug, ids in self.used_facts(used).items():
            entry = Library.read(slug) or {}
            claims = [
                claim for key in Library.CLAIM_KINDS for claim in entry.get(key, []) if claim.get("id") in ids
            ]
            proven += sum(claim.get("status") != "proven" for claim in claims)
            Library.claims(slug, ids, lambda claim: record(dict(claim, status="proven"), "helped", self.run))
            for claim in claims:
                if claim["id"] not in known and claim.get("phase"):
                    fact = {
                        "id": claim["id"],
                        "phase": claim["phase"],
                        "text": claim["text"],
                        "topic": slug,
                        "status": "proven",
                        **({"when": claim["when"]} if claim.get("when") else {}),
                    }
                    self.language_doc["facts"].append(record(fact, "born", self.run))
                    known.add(claim["id"])
        return proven

    def disprove(self, used, phases):
        shown = items_of(key for key, where in used.items() if set(where) & set(phases))
        reason = f"shown in the {', '.join(phases)} prompts of a step that failed"
        gone = self.retire_lessons(shown, reason)
        dropped = {item.id for item in shown if item.kind == "known"}
        for slug, ids in self.used_facts(shown).items():
            gone += Library.retire(slug, ids, reason, self.run)
            dropped |= ids
        gone += retire_from(self.language_doc, "facts", lambda fact: fact["id"] in dropped, reason, self.run)
        self.save()
        return gone

    def retire_lessons(self, shown, reason):
        return sum(
            retire_from(
                doc, "lessons", lambda lesson: Item("lesson", lesson.get("id")) in shown, reason, self.run
            )
            for doc in (self.language_doc, self.any_language_doc)
        )

    @staticmethod
    def used_facts(used):
        found = collections.defaultdict(set)
        for item in used:
            if item.kind == "fact":
                found[item.topic].add(item.id)
        return dict(found)

    def supersede(self, slug, shown):
        reason = "replaced when the language was learned again"
        gone = retire_from(
            self.language_doc,
            "facts",
            lambda fact: fact.get("topic") == slug and fact["id"] not in shown,
            reason,
            self.run,
        )
        self.save()
        return gone

    def toolchain(self):
        known = Toolchain(self.language_doc.get("toolchain"))
        return known if known else None

    def missing_checks(self):
        known, slug = self.toolchain(), Library.language(self.language)
        if not known or not slug:
            return []
        runs = " ".join([*known.setup, known.format, known.lint]).lower()
        missing = lambda tool: (
            (tool["role"] == "types" and tool["name"].split()[0].lower() not in runs)
            or (tool["role"] in ("lint", "format") and not getattr(known, tool["role"]))
        )
        return [
            f"{tool['role']} {tool['name']}" for tool in Library.read(slug).get("tools", []) if missing(tool)
        ]

    def unavailable_tools(self):
        slug = Library.language(self.language)
        entry, known = (Library.read(slug) if slug else None) or {}, self.toolchain()
        failed = " ".join(tool["command"] for tool in entry.get("machine", []) if not tool.get("found"))
        used = " ".join(
            ([*known.setup, known.format, known.lint, known.test_all] if known else [])
            + self.missing_checks()
        )
        names = dict.fromkeys(str(tool.get("name") or "").split(" ")[0] for tool in entry.get("tools", []))
        return [name for name in names if name and mentions(failed, name) and not mentions(used, name)]

    def toolchain_title(self, reuse):
        missing = self.missing_checks()
        add = (
            f"; add the learned {', '.join(missing)} it lacks (setup installs it, lint runs it with its strictest "
            f"useful settings)"
            if missing
            else ""
        )
        return f"Toolchain that worked for {self.language} before ({reuse}{add})"

    def remember_toolchain(self, toolchain):
        kept = dict(toolchain.to_dict(), run="", check="") if toolchain else None
        if kept and kept != self.language_doc.get("toolchain"):
            self.language_doc["toolchain"], self.language_doc["toolchain_saved"] = kept, now_text()
            self.save()

    def known_fix(self, failure, tried=()):
        return next(
            (
                fix
                for fix in self.language_doc.get("fixes", [])
                if fix["failure"] == failure and fix.get("replay") and fix["failure"] not in tried
            ),
            None,
        )

    def similar_fix(self, failure):
        scored = [(similarity(fix["failure"], failure), fix) for fix in self.language_doc.get("fixes", [])]
        best = max(scored, key=lambda pair: pair[0], default=(0, None))
        return best[1] if best[0] >= TOOLCHAIN_HINT_SIMILARITY else None

    def remember_fix(self, fix):
        fixes = [
            kept for kept in self.language_doc.setdefault("fixes", []) if kept["failure"] != fix["failure"]
        ]
        self.language_doc["fixes"] = (
            fixes + [record(dict(fix, used=1, added=now_text()), "born", self.run)]
        )[-20:]
        self.save()

    def fix_result(self, fix, worked):
        if worked:
            fix["used"] = fix.get("used", 0) + 1
            record(fix, "helped", self.run)
        else:
            retire_from(
                self.language_doc,
                "fixes",
                lambda known: known["failure"] == fix["failure"],
                "its replay failed",
                self.run,
            )
        self.save()

    def finished_project(self):
        self.language_doc["projects"] = self.language_doc.get("projects", 0) + 1
        self.save()

    def correct_toolchain(self, changes):
        known = self.toolchain()
        corrected = known.merged(changes) if known else None
        if not corrected:
            return None
        self.language_doc["toolchain"], self.language_doc["toolchain_saved"] = corrected.to_dict(), now_text()
        self.save_language()
        return corrected

    def taught_lesson(self, item, sources):
        known = self.known_lessons()
        text = field(item, "text")
        lesson = next(
            (lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON_SIMILARITY), None
        )
        if lesson is None:
            lesson = self.new_lesson(item, known, ())
            if lesson is None:
                return None
            self.lessons_of(item.get("scope"))["lessons"].append(lesson)
        lesson.update(status="proven", sources=list(dict.fromkeys(sources)))
        record(lesson, "taught", self.run)
        for doc in (self.language_doc, self.any_language_doc):
            doc["lessons"] = self.keep(doc["lessons"], [lesson])
        if any(kept is lesson for kept in self.language_doc["lessons"]):
            self.save_language()
        else:
            self.save_any_language()
        return lesson

    def lessons_of(self, scope):
        return self.language_doc if scope == "language" else self.any_language_doc

    def save(self):
        self.save_language()
        self.save_any_language()

    def save_language(self):
        Home.store().put("languages", self.key, self.language_doc)

    def save_any_language(self):
        Home.store().put("memory", "any", self.any_language_doc)


class Learner(Tracked):
    STEPS = LEARN_STEPS
    VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
    SHELL_OPERATORS = re.compile(r"[;&|<>`$\n\\]")
    UNRELEASED_RE = re.compile(
        r"\b(unreleased|not (yet )?released|(pre|future|upcoming)[- ]release|release candidate|"
        r"draft(?![\w-])|in development|nightly)\b",
        re.I,
    )
    CHANGES = re.compile(
        r"\b(install|uninstall|remove|delete|update|upgrade|add|rm|init|new|create|run|exec|"
        r"download|fetch|get|set|config)\b",
        re.I,
    )

    def __init__(self, model, internet, embeddings):
        self.model, self.internet = model, internet
        self.embeddings = embeddings
        self.model_failure = ""
        self.notes = []
        self.kept_earlier_topic = False

    def start(self):
        self.notes, self.model_failure, self.kept_earlier_topic = [], "", False

    def saved_or_asked(self, known, read, system, parts, label, what, saved_as):
        if known:
            try:
                plan = read(known)
                say.info(f"Using the plan saved last time ({saved_as})", "📋")
                return SavedPlan(plan, True)
            except ValueError as exc:
                say.warn(f"The plan saved last time cannot be used ({exc}), so making a new one")
        answer = self.model.ask_until_usable(
            system, parts, label, lambda reply: read(reply.json()), attempts=2
        )
        plan = answer.needed(
            f"{self.model.name} gave no usable plan for {what}; see the log of its replies "
            f"(tinker memory; its calls are in tinker learn's log)"
        )
        return SavedPlan(plan, False)

    @staticmethod
    def safe_commands(commands):
        kept, dropped = [], []
        for command in commands:
            command = " ".join(str(command).split())
            if not command:
                continue
            unsafe = (
                Learner.SHELL_OPERATORS.search(command)
                or Learner.CHANGES.search(command)
                or Workspace.UNSAFE_COMMAND.search(command)
            )
            (dropped if unsafe else kept).append(command)
        return SafeCommands(kept, dropped)

    def show_dropped(self, plan):
        with say.section():
            for command in plan["dropped"]:
                say.info(
                    f"Skipped `{command}`: Tinker only runs commands that show what is installed (one command, "
                    f"no shell operators, nothing installed or changed)",
                    "🚫",
                )

    @staticmethod
    def replace_proven(slug, entry):
        shown = {item["id"] for item in Library.items({slug: entry}) if item["id"]}
        gone = Knowledge(entry["language"]).supersede(slug, shown)
        if gone:
            say.info(f"Replaced {gone} fact(s) from earlier learning with what was learned now", "♻️")

    def look_at_machine(self, commands):
        if commands:
            self.step("machine", "Checking what is installed on this machine…", "💻")
        with tempfile.TemporaryDirectory(prefix="tinker-learn-") as folder, say.section():
            workspace = Workspace(Path(folder))
            return [self.what_shows(workspace, command) for command in commands]

    def what_shows(self, workspace, command):
        code, output = workspace.run(command, timeout=VERSION_TIMEOUT, quiet=True)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        version = self.VERSION_RE.search(" ".join(lines[:5])) if code == 0 else None
        shown = {
            "command": command,
            "found": code == 0,
            "version": version.group(0) if version else "",
            "output": (lines[0] if lines else "")[:160],
        }
        failed = "did not finish" if code is None else f"exit code {code}"
        if code == 0:
            say.done(f"{command} → {shown['output'][:100]}")
        else:
            say.fail(f"{command} → not installed ({failed})")
        return shown

    def read_web(self, name, searches, max_pages):
        found = self.in_turn(searches, self.results(searches), max_pages)
        return self.read_pages(name, self.fetched(found))

    def web_pages(self, searches):
        asked = [
            dict(search, pages=RESULTS_PER_RESEARCH_SEARCH, section="", focus=search["query"])
            for search in searches
        ]
        found = self.in_turn(asked, self.results(asked), TEACH_PAGES)
        wanted = " ".join(search["query"] for search in searches)
        pages = [self.web_page(page, wanted) for page in self.fetched(found)]
        return [page for page in pages if page["content"]]

    def web_page(self, page, wanted):
        text = page["content"]
        chunks = (
            relevant_chunks(text, TEACH_PAGE_CHARS // CHUNKS_PER_PAGE, wanted, CHUNKS_PER_PAGE)
            if text.strip()
            else []
        )
        return {"title": page["title"], "url": page["url"], "content": "\n\n".join(chunks)[:TEACH_PAGE_CHARS]}

    def results(self, searches):
        found = searched_in_batches(self.internet, searches, RESULTS_PER_RESEARCH_SEARCH)
        return [results[: search["pages"]] for search, results in zip(searches, found)]

    def fetched(self, found):
        pages = self.internet.fetch([result["url"] for result in found]) if found else []
        return [
            dict(
                result,
                title=page.title or result.get("title", ""),
                content=page.text or result.get("content", ""),
                why=page.why,
            )
            for result, page in zip(found, pages)
        ]

    @staticmethod
    def in_turn(searches, results, max_pages):
        pages, seen = [], set()
        for rank in range(RESULTS_PER_RESEARCH_SEARCH):
            for search, found in zip(searches, results):
                if rank < len(found) and found[rank]["url"] not in seen and len(pages) < max_pages:
                    seen.add(found[rank]["url"])
                    pages.append(dict(found[rank], section=search["section"], focus=search["focus"]))
        return pages

    def read_pages(self, name, pages):
        facts, sources = [], []
        for number, page in enumerate(pages, 1):
            if self.model_failure:
                break
            found = self.read_page(name, page, f"{number}/{len(pages)}")
            sources += [page["url"]] if found else []
            found = [dict(fact, section=page["section"]) if page["section"] else fact for fact in found]
            self.add_facts(facts, found)
        return Findings(facts, sources)

    def read_page(self, topic, page, number):
        text = page["content"]
        if not text.strip():
            return self.unreadable(page, number)
        source = Part("Page", f"{page['title']}\n{page['url']}", True)
        focus = Part("Look for", page["focus"], True)
        room = self.room_for_excerpts(topic, source, focus)
        found = []
        for chunk_number, chunk in enumerate(
            relevant_chunks(text, room, f"{topic} {page['focus']}", CHUNKS_PER_PAGE), 1
        ):
            parts = [Part("Topic", topic, True), focus, source, Part("Excerpt", chunk, True)]
            facts = self.facts_in(parts, page, number, chunk_number)
            if facts is None:
                break
            found += [
                dict(fact, source=page["url"])
                for fact in facts
                if not self.UNRELEASED_RE.search(fact["text"])
            ]
        return found

    def unreadable(self, page, number):
        why = f"{page['url']}: {page['why'] or 'no text'}"
        self.notes.append(f"page {why}")
        with say.section():
            say.fail(f"Could not read page {number}: {why}")
        return []

    def room_for_excerpts(self, topic, source, focus):
        return self.model.limit_chars(Prompts.FACTS_SYSTEM) - source.size() - focus.size() - len(topic) - 200

    def facts_in(self, parts, page, number, part):
        try:
            answer = self.model.ask_until_usable(
                Prompts.FACTS_SYSTEM,
                parts,
                f"Facts, page {number} part {part}",
                self.read_facts,
                attempts=2,
            )
        except OllamaError as exc:
            self.model_failure = first_line(str(exc), 200)
            self.notes.append(
                f"the model stopped answering at page {number}; the pages after it were not read"
            )
            say.warn(f"{self.notes[-1]} ({self.model_failure})")
            return None
        if answer.why:
            self.notes.append(f"page {page['url']}: {first_line(answer.why, 160)}")
        return answer.value or []

    @staticmethod
    def read_facts(reply):
        facts = []
        for item in reply.json().get("facts", []):
            if not isinstance(item, dict):
                continue
            text, phase = (
                field(item, "text", MAX_FACT_CHARS),
                field(item, "phase").lower(),
            )
            if text and phase in FACT_PHASES:
                facts.append({"phase": phase, "text": text})
        return facts[:FACTS_PER_CHUNK]

    @staticmethod
    def add_facts(facts, found):
        for fact in found:
            same = Library.like_fact(facts, fact)
            if same is None:
                facts.append(fact)
            elif fact["source"] not in Learner.pages_of(same):
                same["also"] = same.get("also", []) + [fact["source"]]
        return facts

    @staticmethod
    def pages_of(fact):
        return [fact["source"], *fact.get("also", [])]

    def cut_short(self):
        if self.model_failure:
            return f"the model stopped answering ({self.model_failure})"
        return ""

    def condense(self, plan, facts, think=None):
        lessons = Knowledge(plan["language"]).proven_lessons() if plan.get("kind") == "language" else []
        sources = self.rule_sources(facts, lessons)
        self.step("condense", f"Turning {len(facts)} fact(s) into rules a code writer can follow…", "✍️")
        with say.working_on(f"Turning {len(facts)} fact(s) about {plan['name']} into rules"):
            answer = self.model.ask_until_usable(
                Prompts.PLAYBOOK_SYSTEM,
                self.playbook_parts(plan, facts, lessons),
                "Condense the facts",
                lambda reply: self.read_rules(reply.json(), sources),
                attempts=2,
                think=think,
            )
        rules = answer.value or []
        self.say_rules(rules, facts, answer.why)
        return rules

    def say_rules(self, rules, facts, why):
        if not rules:
            self.notes.append(
                f"the facts were not condensed into rules ({first_line(why, 160) or 'no usable reply'})"
            )
        with say.section():
            if rules:
                say.done(f"{len(rules)} rule(s)")
                say.found(first_line(rules[0]["text"], 200))
            else:
                say.warn("Could not make rules: the facts are kept as they are")
        self.outcome(f"{len(rules or [])} rule(s) from {len(facts)} fact(s)")

    @staticmethod
    def rule_sources(facts, lessons):
        return [{"id": Library.claim_id(fact, fact["text"]), "text": fact["text"]} for fact in facts] + [
            {"id": lesson.get("id") or fingerprint(lesson["text"])[:12], "text": lesson["text"]}
            for lesson in lessons
        ]

    @staticmethod
    def playbook_parts(plan, facts, lessons):
        listing = "\n".join(
            f"{number}. [{fact.get('slot', '')}, {fact['phase']}] {fact['text']}"
            for number, fact in enumerate(facts, 1)
        )
        proved = "\n".join(
            f"{number}. {lesson['text']}" for number, lesson in enumerate(lessons, len(facts) + 1)
        )
        tools = Library.tools_text(plan.get("tools", [])) or "none named"
        return [
            Part("Topic", f"{plan['name']} ({plan.get('kind', 'tool')}); its tools: {tools}", True),
            Part("Facts learned from the web", listing, True, exact=True),
            Part("Lessons its projects proved", proved or "(none yet)", True, exact=True),
        ]

    @staticmethod
    def read_rules(data, sources):
        rules = {}
        for item in Learner.rules_given(data):
            rule = Learner.rule_of(item, sources)
            if rule and rule["text"] not in rules:
                rules[rule["text"]] = rule
        if not rules:
            raise ValueError("it needs at least one rule that cites the facts")
        return list(rules.values())[:PLAYBOOK_RULES]

    @staticmethod
    def rules_given(data):
        if not isinstance(data, dict):
            return []
        given = data.get("rules") or []
        declare = data.get("declare")
        if isinstance(declare, dict):
            given = [dict(declare, phase="config", when="")] + list(given)
        return [item for item in given if isinstance(item, dict)]

    @staticmethod
    def rule_of(item, sources):
        text = one_line(item.get("text"))[:MAX_RULE_CHARS]
        phase = str(item.get("phase") or "").lower()
        cites = Learner.cited_ids(item, sources)
        if not (text and phase in FACT_PHASES and cites):
            return None
        when = one_line(item.get("when"))[:MAX_RULE_WHEN_CHARS]
        return {"id": fingerprint(text)[:12], "phase": phase, "text": text, "when": when, "cites": cites}

    @staticmethod
    def cited_ids(item, sources):
        return list(
            dict.fromkeys(
                sources[number - 1]["id"]
                for number in item.get("cites") or []
                if isinstance(number, int) and 1 <= number <= len(sources)
            )
        )

    def index(self, slug, entry):
        if self.embeddings is None:
            return
        texts = [Library.searched(item) for item in Library.items({slug: entry})]
        if self.embeddings.vectors(texts) is None:
            say.warn(f"Not indexed now ({self.embeddings.failed}); a run indexes it when it searches")
        else:
            say.info(f"Indexed with {self.embeddings.name}, so a run can find it by meaning", "🔍")


class SearchPlanner:
    PLAN_TOKENS = 400

    def plan(self, need, count, values=None):
        planned = self.kept_or_asked(need, count) or [{"query": need, "engines": [], "count": count}]
        return [{**search, "query": self.filled(search["query"], values or {})} for search in planned]

    def kept_or_asked(self, need, count):
        key = " ".join(need.lower().split())
        kept = Home.store().get("search plans", key)
        if kept:
            return kept
        asked = self.asked(need, count)
        if asked:
            Home.store().put("search plans", key, asked)
        return asked

    def asked(self, need, count):
        if not Models.quick():
            return []
        try:
            reply = Models.quick_reply(
                [
                    {"role": "system", "content": Prompts.SEARCH_PLAN_SYSTEM},
                    {"role": "user", "content": f"Need: {need}\nCount: {count}"},
                ],
                self.PLAN_TOKENS,
            )
            searches = Reply(reply).json().get("searches")
        except OllamaError, ValueError, AttributeError:
            return []
        return search_entries(searches)[:MAX_PLANNED_SEARCHES]

    @staticmethod
    def filled(query, values):
        for name, value in values.items():
            query = query.replace("{" + name + "}", str(value))
        return query


def search_entry(raw, limit=160):
    if isinstance(raw, str):
        query, given = raw, {}
    elif isinstance(raw, dict) and isinstance(raw.get("query"), str):
        query, given = raw["query"], {key: raw[key] for key in ("engines", "count") if key in raw}
    else:
        return None
    query = " ".join(query.split())[:limit]
    return {"query": query, **given} if query else None


def search_entries(given):
    entries = map(search_entry, given) if isinstance(given, list) else ()
    return [entry for entry in entries if entry]


def searched_in_batches(internet, searches, count):
    asked = [search for search in searches if search["query"]]
    found = iter(results_in_batches(internet, asked, count))
    return [next(found, []) if search["query"] else [] for search in searches]


def results_in_batches(internet, searches, count):
    batches = batched(searches, MAX_PLANNED_SEARCHES)
    return [results for batch in batches for results in internet.search_all(batch, count)]


class Researcher:
    NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
    EMPTY_ANALYSIS = {"findings": [], "contradictions": [], "gaps": [], "checks": []}
    CONFIDENCE_LEVELS = ("low", "medium", "high")

    def __init__(self, learner):
        self.learner, self.model = learner, learner.model

    def research(self, topic):
        self.learner.start()
        with say.part(f"Researching {topic}", "🔬"):
            return self.researched(topic, Library.slug(topic))

    def researched(self, topic, slug):
        learner = self.learner
        known = Library.read(slug)
        frame = self.frame(topic, known if (known or {}).get("kind") == "research" else None)
        facts, sources, analysis = self.investigate(frame)
        if known and (learner.cut_short() or not facts):
            say.warn(
                f"{learner.cut_short() or 'No page gave facts'}; kept what was found before "
                f"({known.get('learned', '?')})"
            )
            learner.kept_earlier_topic = True
            return known
        if not facts:
            say.fail(
                f"Nothing found about {topic}: "
                + ("; ".join(learner.notes) or "the pages stated nothing useful")
            )
            return None
        entry = self.entry(topic, frame, facts, sources, analysis)
        learner.step("keep")
        Library.save(slug, entry)
        say.info(
            f"Saved {len(entry['findings'])} finding(s) from {len(facts)} fact(s) on {len(sources)} page(s) "
            f"as {slug}",
            "💾",
        )
        learner.index(slug, entry)
        return entry

    def frame(self, topic, known):
        self.learner.step("frame")
        parts = [Part("Topic", topic, True), Part("Today", today().isoformat(), True)]
        frame, saved = self.learner.saved_or_asked(
            known,
            self.read_frame,
            Prompts.RESEARCH_PLAN_SYSTEM,
            parts,
            "Frame the research",
            f"researching {topic}",
            Library.slug(topic),
        )
        if not saved:
            say.found(f"{frame['name']}: {frame['summary']}")
        say.info("Questions to answer:", "📝")
        with say.section():
            for number, question in enumerate(frame["questions"], 1):
                say.info(f"Q{number}. {question['question']}")
        self.learner.outcome(f"{len(frame['questions'])} question(s) to answer")
        return frame

    @staticmethod
    def read_frame(data):
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        name, summary = (
            field(data, "name", 80),
            " ".join(str(data.get("summary") or "").split())[:200],
        )
        questions = []
        for item in data.get("questions") or []:
            if not isinstance(item, dict):
                continue
            question = " ".join(str(item.get("question") or "").split())[:200]
            queries = search_entries(item.get("queries"))[:SEARCHES_PER_RESEARCH_QUESTION]
            if question and queries:
                questions.append({"question": question, "queries": queries})
        if not name or not questions:
            raise ValueError("it needs a name and at least one question with its searches")
        return {"name": name, "summary": summary, "questions": questions[:RESEARCH_QUESTIONS]}

    def investigate(self, frame):
        searches = self.first_searches(frame)
        facts, sources, analysis = [], [], self.EMPTY_ANALYSIS
        for round_number, pages in enumerate(RESEARCH_PAGES_PER_ROUND, 1):
            if not searches:
                break
            self.learner.step(
                "investigate",
                f"Round {round_number}: {len(searches)} search(es), reading up to {pages} page(s)…",
                "🔎",
            )
            with say.working_on(f"Reading up to {pages} page(s) about {frame['name']}, round {round_number}"):
                found, read = self.learner.read_web(frame["name"], searches, pages)
            self.learner.add_facts(facts, found)
            sources += [url for url in read if url not in sources]
            self.say_round(facts, sources, found)
            if self.learner.cut_short() or not facts:
                break
            differ = self.cross_check(facts)
            analysis = self.analyse(frame, facts, differ) or analysis
            searches = self.next_searches(frame, facts, analysis)
        return Investigation(facts, sources, analysis)

    @staticmethod
    def first_searches(frame):
        return [
            dict(search, section=str(number), pages=RESULTS_PER_RESEARCH_SEARCH, focus=question["question"])
            for number, question in enumerate(frame["questions"], 1)
            for search in question["queries"]
        ]

    def say_round(self, facts, sources, found):
        say.tally(f"{len(sources)} page(s) read · {len(facts)} fact(s)")
        if found:
            say.found(first_line(found[0]["text"], 200))
        self.learner.outcome(f"{len(facts)} fact(s) from {len(sources)} page(s)")

    def cross_check(self, facts):
        vectors = (
            self.learner.embeddings.vectors([fact["text"] for fact in facts], keep=False)
            if self.learner.embeddings
            else None
        )
        if vectors is None:
            say.warn("Cross-checking by repeats only: the embedding model gave no embeddings")
            vectors = [None] * len(facts)
        else:
            self.fold_repeats(facts, vectors)
        sites = [{host(url) for url in Learner.pages_of(fact)} for fact in facts]
        support, differ = self.compared(facts, vectors, sites)
        for fact, found_on in zip(facts, support):
            fact["sites"] = sorted(found_on)
        return differ

    def compared(self, facts, vectors, sites):
        support, differ = [set(site) for site in sites], []
        for first, second in combinations(range(len(facts)), 2):
            if not self.same_claim(vectors[first], vectors[second]) or sites[first] & sites[second]:
                continue
            if self.agree(facts[first], facts[second]):
                support[first] |= sites[second]
                support[second] |= sites[first]
            else:
                differ.append((first + 1, second + 1))
        return CrossCheck(support, differ)

    def fold_repeats(self, facts, vectors):
        index = 0
        while index < len(facts):
            kept = next(
                (
                    earlier
                    for earlier in range(index)
                    if self.same_claim(vectors[earlier], vectors[index], REPEAT_CLAIM_SIMILARITY)
                    and self.agree(facts[earlier], facts[index])
                ),
                None,
            )
            if kept is None:
                index += 1
                continue
            vectors.pop(index)
            self.fold(facts[kept], facts.pop(index))

    def fold(self, original, repeat):
        if self.more_detailed(repeat, original):
            original["text"] = repeat["text"]
        for url in Learner.pages_of(repeat):
            if url not in Learner.pages_of(original):
                original["also"] = original.get("also", []) + [url]

    def more_detailed(self, fact, other):
        return (len(self.numbers(fact)), len(fact["text"])) > (len(self.numbers(other)), len(other["text"]))

    @staticmethod
    def same_claim(a, b, threshold=SAME_CLAIM_SIMILARITY):
        return a is not None and b is not None and Embeddings.cosine(a, b) >= threshold

    @classmethod
    def agree(cls, a, b):
        first, second = cls.numbers(a), cls.numbers(b)
        return first <= second or second <= first

    @classmethod
    def numbers(cls, fact):
        return set(cls.NUMBER_RE.findall(fact["text"]))

    def analyse(self, frame, facts, differ):
        parts = self.analysis_parts(frame, facts, differ)
        try:
            answer = self.model.ask_until_usable(
                Prompts.RESEARCH_ANALYSIS_SYSTEM,
                parts,
                "Analyse the findings",
                lambda reply: self.read_analysis(reply.json(), len(facts), len(frame["questions"])),
                attempts=2,
            )
        except OllamaError as exc:
            answer = Answer(None, why=str(exc))
        if answer.why:
            self.learner.notes.append(f"the findings could not be analysed ({first_line(answer.why, 160)})")
            say.warn(self.learner.notes[-1])
        return answer.value

    def analysis_parts(self, frame, facts, differ):
        questions = "\n".join(
            f"Q{number}. {question['question']}" for number, question in enumerate(frame["questions"], 1)
        )
        pairs = "\n".join(f"F{first} / F{second}" for first, second in differ) or "(none)"
        fixed = [
            Part("Topic", frame["name"], True),
            Part("Questions", questions, True),
            Part("Facts that may disagree", pairs, True),
        ]
        room = self.model.limit_chars(Prompts.RESEARCH_ANALYSIS_SYSTEM) - Part.total_size(fixed) - 200
        return fixed + [Part("Facts", self.fact_listing(facts, room), True, exact=True)]

    @staticmethod
    def fact_listing(facts, room):
        lines = {
            number: f"F{number} [Q{fact.get('section', '?')}, {len(fact.get('sites', []))} site(s)] {fact['text']}"
            for number, fact in enumerate(facts, 1)
        }
        kept, used = set(), 0
        for number in sorted(lines, key=lambda number: -len(facts[number - 1].get("sites", []))):
            if used + len(lines[number]) + 1 <= room:
                kept.add(number)
                used += len(lines[number]) + 1
        return "\n".join(lines[number] for number in sorted(kept))

    @staticmethod
    def read_analysis(data, fact_count, question_count):
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        cited = lambda numbers: sorted(
            {number for number in numbers or [] if isinstance(number, int) and 1 <= number <= fact_count}
        )
        asked = lambda item: (
            isinstance(item, dict)
            and isinstance(item.get("question"), int)
            and 1 <= item["question"] <= question_count
        )
        findings = Researcher.read_findings(data.get("findings") or [], cited, asked)
        if not findings:
            raise ValueError("it needs a finding for at least one question")
        return {
            "findings": findings,
            "contradictions": Researcher.read_contradictions(data.get("contradictions") or [], cited),
            "gaps": Researcher.read_gaps(data.get("gaps") or [], asked)[:RESEARCH_GAPS],
            "checks": Researcher.read_checks(data.get("checks") or [], fact_count)[:RESEARCH_FACTS_CHECKED],
        }

    @staticmethod
    def read_findings(given, cited, asked):
        return [
            {
                "question": finding["question"],
                "answer": " ".join(str(finding.get("answer") or "").split())[:600],
                "confidence": finding.get("confidence")
                if finding.get("confidence") in ("high", "medium", "low")
                else "low",
                "facts": cited(finding.get("facts")),
            }
            for finding in given
            if asked(finding) and field(finding, "answer")
        ]

    @staticmethod
    def read_contradictions(given, cited):
        contradictions = []
        for item in given:
            if isinstance(item, dict) and len(cited(item.get("facts"))) >= 2:
                facts = cited(item["facts"])
                doubtful = [number for number in cited(item.get("doubtful")) if number in facts] or facts
                contradictions.append(
                    {
                        "facts": facts,
                        "doubtful": doubtful,
                        "note": " ".join(str(item.get("note") or "").split())[:300],
                    }
                )
        return contradictions

    @staticmethod
    def read_gaps(given, asked):
        return [
            {
                "question": gap["question"],
                "missing": " ".join(str(gap.get("missing") or "").split())[:200],
                "search": search_entry(gap.get("search")),
            }
            for gap in given
            if asked(gap) and search_entry(gap.get("search"))
        ]

    @staticmethod
    def read_checks(given, fact_count):
        return [
            {"fact": check["fact"], "search": search_entry(check.get("search"))}
            for check in given
            if isinstance(check, dict)
            and isinstance(check.get("fact"), int)
            and 1 <= check["fact"] <= fact_count
            and search_entry(check.get("search"))
        ]

    @staticmethod
    def next_searches(frame, facts, analysis):
        gaps = [
            dict(
                gap["search"],
                section=str(gap["question"]),
                pages=1,
                focus=frame["questions"][gap["question"] - 1]["question"],
            )
            for gap in analysis["gaps"]
        ]
        checks = [
            dict(
                check["search"],
                section=facts[check["fact"] - 1]["section"],
                pages=2,
                focus=f"whether this is so, and the exact figures: {facts[check['fact'] - 1]['text']}",
            )
            for check in analysis["checks"]
        ]
        return gaps + checks

    def entry(self, topic, frame, facts, sources, analysis):
        disputed = {*contradiction["doubtful"] for contradiction in analysis["contradictions"]}
        kept = [self.kept_fact(fact, number in disputed) for number, fact in enumerate(facts, 1)]
        cited = lambda finding: [kept[number - 1] for number in finding["facts"]]
        findings = [
            dict(finding, confidence=self.supported(finding["confidence"], cited(finding)))
            for finding in analysis["findings"]
        ]
        return {
            "topic": topic,
            "kind": "research",
            "name": frame["name"],
            "summary": frame["summary"],
            "questions": frame["questions"],
            "learned": now_text(),
            "model": self.model.name,
            "machine": [],
            "findings": findings,
            "contradictions": analysis["contradictions"],
            "open": analysis["gaps"],
            "facts": kept,
            "sources": sources,
            "notes": self.learner.notes,
        }

    @staticmethod
    def kept_fact(fact, disputed):
        sites = fact.pop("sites", [])
        confidence = "disputed" if disputed else "corroborated" if len(sites) >= 2 else "single"
        return {
            **{key: value for key, value in fact.items() if key != "section"},
            "question": int(fact["section"]),
            "sites": sites,
            "confidence": confidence,
        }

    @classmethod
    def supported(cls, stated, cited):
        corroborated = sum(fact["confidence"] == "corroborated" for fact in cited)
        disputed = any(fact["confidence"] == "disputed" for fact in cited)
        ceiling = (
            "high"
            if cited and not disputed and 2 * corroborated >= len(cited)
            else "medium"
            if cited
            else "low"
        )
        return min(stated, ceiling, key=cls.CONFIDENCE_LEVELS.index)

    @staticmethod
    def answer(entry):
        heading = f"{entry['name']}: {entry['summary']}" if entry.get("summary") else entry["name"]
        findings = entry.get("findings", [])
        if not findings:
            return f"{heading}\n\nThe research has no finding: the pages read did not answer its questions."
        sources, lines = [], [heading]
        for number, question in enumerate(entry["questions"], 1):
            answered = [finding for finding in findings if finding["question"] == number]
            if answered:
                lines += ["", f"Q{number}. {question['question']}"]
            for finding in answered:
                cited = Researcher.cited_pages(entry["facts"], finding["facts"], sources)
                lines.append(f"{finding['answer']} ({finding['confidence']} confidence) {cited}".rstrip())
        if sources:
            lines += ["", "Sources:"] + [f"[{number}] {url}" for number, url in enumerate(sources, 1)]
        return "\n".join(lines)

    @staticmethod
    def cited_pages(facts, numbers, sources):
        pages = []
        for number in numbers:
            for url in Learner.pages_of(facts[number - 1]):
                if url not in sources:
                    sources.append(url)
                if url not in pages:
                    pages.append(url)
        return " ".join(f"[{sources.index(url) + 1}]" for url in pages)


class Library:
    CLAIM_KINDS = ("facts", "findings", "rules")

    @staticmethod
    def topics():
        return {
            slug: entry for slug, entry in Home.store().items("learned").items() if Library.readable(entry)
        }

    @staticmethod
    def read(slug):
        entry = Home.store().get("learned", slug) if slug else None
        return entry if Library.readable(entry) else None

    @staticmethod
    def readable(entry):
        return isinstance(entry, dict) and isinstance(entry.get("facts", []), list)

    @staticmethod
    def save(slug, entry):
        for claim, text in [
            (item, item.get("text", "")) for item in entry.get("facts", []) + entry.get("rules", [])
        ] + [(item, item.get("answer", "")) for item in entry.get("findings", [])]:
            claim.setdefault("id", fingerprint(str(text))[:12])
            claim.setdefault("status", "candidate")
        facts = entry.get("facts", [])
        for finding in entry.get("findings", []):
            finding.setdefault(
                "cites",
                [
                    facts[number - 1]["id"]
                    for number in finding.get("facts", [])
                    if isinstance(number, int) and 1 <= number <= len(facts)
                ],
            )
        Home.store().put("learned", slug, entry)

    @staticmethod
    def claims(slug, ids, change):
        def changed(entry):
            if not entry:
                return entry
            for key in Library.CLAIM_KINDS:
                entry[key] = [
                    kept
                    for kept in (
                        change(claim) if claim.get("id") in ids else claim for claim in entry.get(key, [])
                    )
                    if kept is not None
                ]
            return entry

        if Library.read(slug):
            Home.store().update("learned", slug, changed)

    @staticmethod
    def retire(slug, ids, reason, run):
        gone = []

        def retired_in(entry):
            if entry:
                gone.append(
                    sum(
                        retire_from(entry, key, lambda claim: claim.get("id") in ids, reason, run)
                        for key in Library.CLAIM_KINDS
                    )
                )
            return entry

        if Library.read(slug):
            Home.store().update("learned", slug, retired_in)
        return sum(gone)

    @staticmethod
    def taught_fact(slug, about, text, sources):
        entry = Library.read(slug) or {}
        old = next(
            (
                claim
                for key in Library.CLAIM_KINDS + ("retired",)
                for claim in entry.get(key, [])
                if claim.get("id") == about
            ),
            None,
        )
        if old is None:
            return None
        fact = {
            "text": text[:MAX_FACT_CHARS],
            "phase": old.get("phase", "book"),
            "status": "proven",
            "sources": list(dict.fromkeys(sources)),
            "id": fingerprint(text)[:12],
            **{key: old[key] for key in ("chapter", "slot", "section") if key in old},
        }
        record(fact, "taught", None)
        Home.store().update("learned", slug, lambda kept: dict(kept, facts=kept.get("facts", []) + [fact]))
        return fact

    @staticmethod
    def checked_fact(slug, text, sources):
        if not Library.read(slug):
            return None
        fact = {
            "text": text[:MAX_FACT_CHARS],
            "phase": "code",
            "status": "candidate",
            "sources": list(dict.fromkeys(sources)),
            "id": fingerprint(text)[:12],
        }

        def checked_in(entry):
            facts = entry.get("facts", [])
            same = Library.like_fact(facts, fact)
            if same is None:
                return dict(entry, facts=facts + [record(fact, "checked", None)])
            same["sources"] = list(dict.fromkeys(same.get("sources", []) + fact["sources"]))
            record(same, "checked", None)
            return entry

        return Library.like_fact(Home.store().update("learned", slug, checked_in)["facts"], fact)

    @staticmethod
    def like_fact(facts, fact):
        return next(
            (
                known
                for known in facts
                if similarity(known.get("text", ""), fact["text"]) >= SAME_FACT_SIMILARITY
            ),
            None,
        )

    @staticmethod
    def slug(topic):
        text = topic.lower().replace("+", "p").replace("#", "sharp")
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60]

    @staticmethod
    def language(name):
        key = language_key(name) if (name or "").strip() else ""
        entry = Library.read(key) if key else None
        return key if entry and entry.get("kind") == "language" else None

    @staticmethod
    def items(topics):
        found = []
        for slug, entry in topics.items():
            try:
                found += Library.topic_items(slug, entry)
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                say.warn(
                    f"What was learned about {slug} is not readable ({type(exc).__name__}: {exc}); left out"
                )
        return found

    @staticmethod
    def topic_items(slug, entry):
        name = str(entry.get("name") or entry["topic"])
        item = lambda phase, text, claim=None, said=None, **more: {
            "topic": slug,
            "name": name,
            "phase": phase,
            "text": text,
            **more,
            **Library.claim_state(claim, said),
        }
        return (
            [item(phase, text) for phase, text in Library.overview(entry, name)]
            + [
                item("findings", Library.finding_text(entry, finding), finding, finding["answer"])
                for finding in entry.get("findings", [])
            ]
            + [
                item(
                    str(rule["phase"]),
                    str(rule["text"]),
                    rule,
                    rule["text"],
                    kind="rule",
                    when=str(rule.get("when") or ""),
                )
                for rule in entry.get("rules", [])
            ]
            + [
                item(str(fact["phase"]), Library.fact_text(fact), fact, fact["text"])
                for fact in Library.facts_shown(entry)
            ]
        )

    @staticmethod
    def overview(entry, name):
        yield "summary", str(entry.get("summary") or name)
        if entry.get("tools"):
            yield "tools", f"its tools: {Library.tools_text(entry['tools'])}"
        installed = [Library.installed_text(tool) for tool in entry.get("machine", [])]
        if installed:
            yield "machine", f"on this machine: {'; '.join(installed)}"

    @staticmethod
    def installed_text(tool):
        return tool["output"] if tool["found"] else f"`{tool['command']}` fails (not installed)"

    @staticmethod
    def machine_text(machine):
        return "; ".join(
            f"`{tool['command']}`: {tool['output']}" if tool["found"] else Library.installed_text(tool)
            for tool in machine
        )

    @staticmethod
    def finding_text(entry, finding):
        question = entry["questions"][finding["question"] - 1]["question"]
        return f"{question} {finding['answer']} ({finding['confidence']} confidence)"

    @staticmethod
    def fact_text(fact):
        return str(fact["text"]) + (f" ({fact['confidence']})" if fact.get("confidence") else "")

    @staticmethod
    def claim_state(claim, said):
        if claim is None:
            return {"id": None, "status": None}
        return {"id": Library.claim_id(claim, said), "status": claim.get("status", "candidate")}

    @staticmethod
    def facts_shown(entry):
        rules = entry.get("rules", [])
        if not rules:
            return entry["facts"]
        cited = lambda given: {*rule.get("cites", []) for rule in given}
        reopened = cited(
            retired for retired in entry.get("retired", []) if retired.get("was") == "rules"
        ) - cited(rules)
        return [fact for fact in entry["facts"] if Library.claim_id(fact, fact["text"]) in reopened]

    @staticmethod
    def claim_id(claim, text):
        return claim.get("id") or fingerprint(str(text))[:12]

    @staticmethod
    def tools_text(tools):
        return ", ".join(
            f"{tool['role']} {tool['name']}" for tool in tools if isinstance(tool, dict) and tool.get("name")
        )

    @staticmethod
    def searched(item):
        return f"{item['name']}: {item['text']}"

    @staticmethod
    def shown_in(item, phases):
        shown = {
            "machine": {"toolchain", "config"},
            "tools": {"toolchain", "config", "tests"},
            "findings": {"spec", "design"},
        }.get(item["phase"])
        return item["phase"] in phases or bool(shown and shown & set(phases))


def relevant_chunks(text, size, wanted, count):
    chunks = split_text(text, size)
    if len(chunks) <= count:
        return chunks
    matched = [index for index in Retriever(None).by_words(wanted, chunks) if index]
    chosen = [0] + (matched + [index for index in range(1, len(chunks)) if index not in matched])[: count - 1]
    return [chunks[index] for index in sorted(chosen)]


class Retriever:
    RRF_K = 60
    BM25_K1, BM25_B = 1.5, 0.75

    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.used = "words"
        self.query_embeddings = {}

    def rank(self, query, texts, prune=False):
        if not texts:
            return []
        fused = {}
        for ranking in (self.by_words(query, texts), self.by_meaning(query, texts, prune)):
            for position, index in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1.0 / (self.RRF_K + position + 1)
        found = sorted(fused, key=lambda index: (-fused[index], index))
        return found + [index for index in range(len(texts)) if index not in fused]

    def by_words(self, query, texts):
        scores = [(score, index) for index, score in enumerate(self.word_scores(query, texts))]
        return [
            index
            for _, index in sorted(
                (scored for scored in scores if scored[0] > 0), key=lambda pair: (-pair[0], pair[1])
            )
        ]

    def word_scores(self, query, texts):
        if not texts:
            return []
        terms, docs = set(words(query)), [words(text) for text in texts]
        average = sum(len(doc) for doc in docs) / len(docs) or 1
        holding = collections.Counter(*(set(doc) & terms) for doc in docs)
        return [self.relevance(terms, doc, len(docs), holding, average) for doc in docs]

    def relevance(self, terms, doc, total, holding, average):
        counts, score = collections.Counter(doc), 0.0
        for term in terms & counts.keys():
            idf = math.log(1 + (total - holding[term] + 0.5) / (holding[term] + 0.5))
            tf, length = counts[term], len(doc) / average
            score += (
                idf * tf * (self.BM25_K1 + 1) / (tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * length))
            )
        return score

    def by_meaning(self, query, texts, prune):
        similarity_of = self.similarities(query, texts, prune)
        if similarity_of is None:
            return []
        return sorted(range(len(texts)), key=lambda index: (-similarity_of[index], index))

    def similarities(self, query, texts, prune=False):
        if self.embeddings is None:
            return None
        if not texts:
            return []
        vectors = self.embeddings.vectors(texts, prune=prune)
        if vectors and query not in self.query_embeddings:
            asked = self.embeddings.vectors([query], keep=False)
            self.query_embeddings[query] = asked[0] if asked else None
        if not vectors or not self.query_embeddings.get(query):
            say.warn(f"Meaning search not used: {self.embeddings.failed}; the word search stands alone")
            self.embeddings = None
            return None
        self.used = f"words and meaning ({self.embeddings.name})"
        return [Embeddings.cosine(self.query_embeddings[query], vector) for vector in vectors]


class Embeddings:
    TEXTS_PER_REQUEST = 32

    def __init__(self, name):
        self.name, self.failed = name, ""
        self.cache = {"model": name, "vectors": Home.store().get("embeddings", name, {})}

    @staticmethod
    def required(option):
        if not Config.get("embed_model"):
            raise SystemExit(
                f"{option} needs an embedding model, which indexes what it learns: select one Ollama "
                f"has with tinker embed NAME (tinker models lists them)"
            )
        Embeddings.check()
        return Embeddings(Config.get("embed_model"))

    @staticmethod
    def check():
        name = Config.get("embed_model")
        if not isinstance(name, str):
            raise SystemExit(f"embed_model is not a model name ({name!r}): select one with tinker embed NAME")
        if name:
            try:
                Models.installed(name)
            except SystemExit as exc:
                raise SystemExit(f"The embedding model (tinker embed): {exc}")

    @staticmethod
    def configured():
        name = Config.get("embed_model")
        return Embeddings(name) if name else None

    def vectors(self, texts, keep=True, prune=False):
        stored = self.cache["vectors"]
        keys = [self.key(text) for text in texts]
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in stored))
        fresh = {}
        for start in range(0, len(missing), self.TEXTS_PER_REQUEST):
            batch = missing[start : start + self.TEXTS_PER_REQUEST]
            found = self.embedded(batch)
            if found is None:
                return None
            fresh.update(self.keyed(batch, found))
        result = [stored.get(key) or fresh[key] for key in keys]
        if keep and (fresh or prune):
            self.kept(fresh, keys if prune else None)
        return result

    def keyed(self, texts, vectors):
        return {
            self.key(text): [round(number, 5) for number in vector] for text, vector in zip(texts, vectors)
        }

    def embedded(self, batch):
        Ollama.embedders.add(self.name)
        data = Ollama.get_json(
            "/api/embed",
            {"model": self.name, "input": batch, "keep_alive": EMBED_KEEP_ALIVE},
            timeout=300,
            errors=True,
        )
        found = data.get("embeddings") if isinstance(data, dict) else None
        if self.one_vector_each(found, batch):
            return found
        self.failed = f"{self.name} gave no embeddings" + (
            f" ({data['error']})" if isinstance(data, dict) and data.get("error") else ""
        )
        return None

    def one_vector_each(self, found, batch):
        return (
            isinstance(found, list)
            and len(found) == len(batch)
            and all(self.is_vector(vector) for vector in found)
        )

    @staticmethod
    def is_vector(value):
        return isinstance(value, list) and value and all(isinstance(number, (int, float)) for number in value)

    def kept(self, fresh, only=None):
        stored = self.cache["vectors"]
        stored.update(fresh)
        if only is not None:
            self.cache["vectors"] = {key: stored[key] for key in only}
        kept = self.cache["vectors"]
        Home.store().update(
            "embeddings", self.name, lambda saved: {**({} if only is not None else saved or {}), **kept}
        )

    @staticmethod
    def key(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / size if size else 0.0


def record(item, event, run):
    item["history"] = (item.get("history", []) + [{"event": event, "run": run, "at": now_text()}])[
        -EVENTS_KEPT_PER_ITEM:
    ]
    if event == "helped":
        item["helped"] = item.get("helped", 0) + 1
    return item


def retire(item, language, reason, run):
    if item.kind in Item.CLAIM_KINDS:
        return Library.retire(item.topic, {item.id}, reason, run) > 0
    return item.kind in STORED_AS and retire_known(Knowledge(language, run), item, reason)


def retire_known(knowledge, item, reason):
    name, field = STORED_AS[item.kind]
    gone = lambda kept: kept.get(field) == item.id
    if retire_from(knowledge.language_doc, name, gone, reason, knowledge.run):
        knowledge.save_language()
        return True
    if item.kind == "lesson" and retire_from(knowledge.any_language_doc, name, gone, reason, knowledge.run):
        knowledge.save_any_language()
        return True
    return False


def items_of(keys):
    return {Item.of(key) for key in keys}


def retire_from(doc, key, gone, reason, run):
    items = doc.get(key, [])
    leaving = [item for item in items if gone(item)]
    doc[key] = [item for item in items if not gone(item)]
    retired = [
        record(dict(item, status="retired", reason=reason, retired=now_text(), was=key), "failed", run)
        for item in leaving
    ]
    if retired:
        doc["retired"] = (doc.get("retired", []) + retired)[-RETIRED_KEPT_PER_LIST:]
    return len(retired)


def mentions(text, name):
    return bool(re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I))


def language_key(name):
    base = (name or "").split("(")[0].lower().replace("+", "p").replace("#", "sharp")
    found = re.findall(r"[a-z][a-z0-9]*", base)
    return found[0] if found else "unknown"
