"""What Tinker knows beyond the model: lessons, toolchain fixes and proven facts from earlier projects
(Knowledge), and what it learns from the web (Learner, Researcher), kept and searched (Library,
Retriever, Embeddings). What it learns starts as a candidate: a cycle that succeeds using it proves it,
and a failure in the step it was meant to help retires it (kept apart, never shown again). Each item
keeps its history: the run that taught it, and the runs it helped or failed."""

lazy import collections
lazy import datetime
lazy import hashlib
lazy import math
import re
lazy import tempfile
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request
lazy from pathlib import Path

lazy from .model import Models, Ollama, OllamaError
lazy from .prompts import Part, PromptTooLarge, Prompts
from .settings import (
    Config, FACTS_PER_CHUNK, FACT_CHARS, FACT_PHASES, HISTORY_KEPT, Home, LESSONS_SHOWN, LESSON_PHASES, MAX_LESSONS,
    PAGE_CHUNKS, PLAYBOOK_RULES, RULE_CHARS, RULE_WHEN_CHARS,
    REPEAT_CLAIM, RETIRED_KEPT, RESEARCH_CHECKS, RESEARCH_GAPS, RESEARCH_PAGES, RESEARCH_QUERIES, RESEARCH_QUESTIONS, SAME_CLAIM,
    SAME_FACT, SAME_LESSON, SEARCH_RESULTS, SIMILAR_FAILURE,
    VERSION_TIMEOUT, fingerprint, first_line, now_text, similarity, split_text, words,
)
lazy from .workspace import Toolchain, Workspace


class Knowledge:
    """What earlier projects taught, to do it right the first time. Per language (its language_key):
    the toolchain that last worked and the toolchain fixes found (both replayed without asking the
    model), lessons, and the facts proven by its projects; for any language: lessons. A lesson is
    general: it never names a project, its files, its names or its data. Lessons and facts are shown
    in the prompts of the phase they concern, the most confirmed first; they start as candidates, a
    cycle that succeeds using them proves them, and a failure in the step they were shown to retires
    them. Each keeps its history in this run (run: its ID). Kept in Tinker's "languages" and "memory"."""

    def __init__(self, language, run=None):
        self.language = language or "Python"
        self.key = language_key(self.language)
        self.run = run
        self.language_doc = Home.store().get("languages", self.key) or {
            "language": self.language, "projects": 0, "toolchain": None, "fixes": [], "lessons": [], "facts": []}
        self.any_language_doc = Home.store().get("memory", "any") or {"lessons": []}

    @staticmethod
    def named_in(text):
        """The language a request names, of those Tinker knows (built projects in, or learned), the first it
        names; None when it names none."""
        known = [doc.get("language") for doc in Home.store().items("languages").values()] + \
                [entry.get("language") for entry in Library.topics().values() if entry.get("kind") == "language"]
        named = [name for name in dict.fromkeys(n for n in known if n) if mentions(text or "", name)]
        return min(named, key=lambda name: (text or "").lower().index(name.lower()), default=None)

    def summary(self):
        lessons = len(self.language_doc.get("lessons", [])) + len(self.any_language_doc.get("lessons", []))
        projects, facts = self.language_doc.get("projects", 0), len(self.language_doc.get("facts", []))
        known = [("the toolchain that worked" + (f" ({projects} finished project(s))" if projects else ""))
                 if self.language_doc.get("toolchain") else "",
                 f"{len(self.language_doc.get('fixes', []))} toolchain fix(es)" if self.language_doc.get("fixes") else "",
                 f"{lessons} lesson(s)" if lessons else "", f"{facts} proven fact(s)" if facts else ""]
        return f"{self.language}: " + (", ".join(k for k in known if k) or "nothing learned yet")

    def lessons(self, phases, limit=LESSONS_SHOWN):
        """The lessons for these phases, the most confirmed first: [{id, text, ...}]."""
        pool = [lesson for lesson in self.known_lessons() if lesson.get("phase") in phases]
        pool.sort(key=Knowledge.rank, reverse=True)
        return [dict(lesson, id=lesson.get("id") or fingerprint(lesson["text"])[:12]) for lesson in pool[:limit]]

    @staticmethod
    def rank(lesson):
        """The best evidenced (seen, and helped a cycle that succeeded), then the most recently seen, first
        (sorted in reverse)."""
        return lesson.get("seen", 1) + lesson.get("helped", 0), lesson.get("last_seen", "")

    def facts(self, phases):
        """The language's proven facts for these phases: [{id, phase, text, topic}]."""
        return [fact for fact in self.language_doc.get("facts", []) if fact.get("phase") in phases]

    def known_lessons(self):
        return self.language_doc.setdefault("lessons", []) + self.any_language_doc.setdefault("lessons", [])

    def proven_lessons(self):
        """The lessons this language's own projects proved (not any language's): what its rules fold in."""
        return [lesson for lesson in self.language_doc.get("lessons", []) if lesson.get("status") == "proven"]

    def add_lessons(self, new, confirmed, names=()):
        """Adds new lessons as candidates (one like a known lesson confirms it instead; one that names
        the project, by any of names, is not general and is dropped); returns how many were new."""
        known, added = self.known_lessons(), 0
        for number in confirmed:
            if isinstance(number, int) and 1 <= number <= len(known):
                self.confirm(known[number - 1])
        for item in new:
            lesson = self.new_lesson(item, known, names)
            if lesson:
                (self.language_doc if item.get("scope") == "language" else self.any_language_doc)["lessons"].append(lesson)
                known.append(lesson)
                added += 1
        for doc in (self.language_doc, self.any_language_doc):
            doc["lessons"] = self.keep(doc["lessons"], known[len(known) - added:])
        self.save()
        return added

    def new_lesson(self, item, known, names):
        """The lesson an item teaches, as a candidate; None when it is not a usable general lesson, or one known
        already (then confirmed)."""
        text, phase = str(item.get("text", "")).strip()[:240], str(item.get("phase", "")).strip().lower()
        if not text or phase not in LESSON_PHASES or self.names_project(text, names):
            return None
        same = next((lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON), None)
        if same:
            self.confirm(same)
            return None
        lesson = {"id": fingerprint(text)[:12], "phase": phase, "text": text, "status": "candidate", "seen": 1,
                  "added": now_text(), "last_seen": now_text()}
        return record(lesson, "born", self.run)

    @staticmethod
    def names_project(text, names):
        """Whether a lesson names the project (one of its files, or its folder): then it is not general."""
        return any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text, re.I) for name in names if name)

    def confirm(self, lesson):
        lesson["seen"], lesson["last_seen"] = lesson.get("seen", 1) + 1, now_text()
        record(lesson, "confirmed", self.run)

    @staticmethod
    def keep(lessons, new):
        """At most MAX_LESSONS, the most confirmed first; the new ones always stay (the least
        confirmed older ones go to make room)."""
        rank = Knowledge.rank
        fresh = [lesson for lesson in lessons if any(lesson is n for n in new)]
        older = sorted((lesson for lesson in lessons if not any(lesson is n for n in new)), key=rank, reverse=True)
        return sorted(fresh + older[:max(0, MAX_LESSONS - len(fresh))], key=rank, reverse=True)

    def prove(self, used):
        """A cycle that used these succeeded: candidate lessons, facts and findings are proven, and the
        web's facts are copied into the language's knowledge. used: {key: [prompt phases]}, the keys as
        Session records them (lesson:ID, fact:TOPIC:ID, known:ID). Each of them helped. Returns how many were
        newly proven."""
        proven = self.prove_lessons(used) + self.prove_web_facts(used)
        for fact in self.language_doc.get("facts", []):
            if f"known:{fact['id']}" in used:
                record(fact, "helped", self.run)
        self.save()
        return proven

    def prove_lessons(self, used):
        proven = 0
        for lesson in self.known_lessons():
            if f"lesson:{lesson.get('id')}" in used:
                proven += lesson.get("status") != "proven"
                record(lesson, "helped", self.run)["status"] = "proven"
        return proven

    def prove_web_facts(self, used):
        """The web's facts and findings used, proven where they were learned; the facts (a finding stays with its
        research) copied into the language's knowledge."""
        proven, known = 0, {fact["id"] for fact in self.language_doc.setdefault("facts", [])}
        for slug, ids in self.used_facts(used).items():
            entry = Library.read(slug) or {}
            claims = [c for key in Library.CLAIMS for c in entry.get(key, []) if c.get("id") in ids]
            proven += sum(c.get("status") != "proven" for c in claims)
            Library.claims(slug, ids, lambda claim: record(dict(claim, status="proven"), "helped", self.run))
            for claim in claims:
                if claim["id"] not in known and claim.get("phase"):
                    fact = {"id": claim["id"], "phase": claim["phase"], "text": claim["text"], "topic": slug,
                            "status": "proven", **({"when": claim["when"]} if claim.get("when") else {})}
                    self.language_doc["facts"].append(record(fact, "born", self.run))
                    known.add(claim["id"])
        return proven

    def disprove(self, used, phases):
        """A step failed: every lesson, fact or finding used in these prompt phases is retired, proven
        or not. Returns how many were retired."""
        shown = {key for key, where in used.items() if set(where) & set(phases)}
        reason = f"shown in the {', '.join(phases)} prompts of a step that failed"
        gone = self.retire_lessons(shown, reason)
        dropped = {key.split(":", 1)[1] for key in shown if key.startswith("known:")}
        for slug, ids in self.used_facts(shown).items():
            gone += Library.retire(slug, ids, reason, self.run)
            dropped |= ids
        gone += retire(self.language_doc, "facts", lambda fact: fact["id"] in dropped, reason, self.run)
        self.save()
        return gone

    def retire_lessons(self, shown, reason):
        """The lessons among the keys shown, retired; how many."""
        return sum(retire(doc, "lessons", lambda lesson: f"lesson:{lesson.get('id')}" in shown, reason, self.run)
                   for doc in (self.language_doc, self.any_language_doc))

    @staticmethod
    def used_facts(used):
        """{topic: {ids}} of the web's facts and findings among used keys (fact:TOPIC:ID)."""
        found = collections.defaultdict(set)
        for key in used:
            kind, _, rest = key.partition(":")
            if kind == "fact" and ":" in rest:
                slug, _, claim_id = rest.rpartition(":")
                found[slug].add(claim_id)
        return dict(found)

    def supersede(self, slug, shown):
        """The language was learned again (kept as slug): the facts its earlier learning proved that the new
        learning does not show (shown: the ids its prompts show, its rules when it has them) are retired, as
        replaced by it; how many. Proven facts from other topics stay."""
        reason = "replaced when the language was learned again"
        gone = retire(self.language_doc, "facts", lambda fact: fact.get("topic") == slug and fact["id"] not in shown,
                      reason, self.run)
        self.save()
        return gone

    def toolchain(self):
        known = Toolchain(self.language_doc.get("toolchain"))
        return known if known else None

    def missing_checks(self):
        """The language's learned tools that check code but the toolchain that worked does not run, as "role
        name": a type checker it never names, a linter or formatter where it has no command. A toolchain
        replayed as it is would never take up what was learned after it."""
        known, slug = self.toolchain(), Library.language(self.language)
        if not known or not slug:
            return []
        runs = " ".join([*known.setup, known.format, known.lint]).lower()
        missing = lambda tool: (tool["role"] == "types" and tool["name"].split()[0].lower() not in runs) or \
            (tool["role"] in ("lint", "format") and not getattr(known, tool["role"]))
        return [f"{tool['role']} {tool['name']}" for tool in Library.read(slug).get("tools", []) if missing(tool)]

    def unavailable_tools(self):
        """The language's learned tools a project here cannot count on: not installed on this machine, and
        neither installed nor run by the toolchain that worked, nor a check it lacks (to be added)."""
        slug = Library.language(self.language)
        entry, known = (Library.read(slug) if slug else None) or {}, self.toolchain()
        failed = " ".join(m["command"] for m in entry.get("machine", []) if not m.get("found"))
        used = " ".join(([*known.setup, known.format, known.lint, known.test_all] if known else [])
                        + self.missing_checks())
        names = dict.fromkeys(str(tool.get("name") or "").split(" ")[0] for tool in entry.get("tools", []))
        return [name for name in names if name and mentions(failed, name) and not mentions(used, name)]

    def toolchain_title(self, reuse):
        """The title of the part that shows the toolchain that worked: reuse says how; the learned checks it
        lacks are named, to be added."""
        missing = self.missing_checks()
        add = f"; add the learned {', '.join(missing)} it lacks (setup installs it, lint runs it with its strictest " \
              f"useful settings)" if missing else ""
        return f"Toolchain that worked for {self.language} before ({reuse}{add})"

    def remember_toolchain(self, toolchain):
        """The toolchain that worked, kept for the language's next projects as what holds for any
        project in it: without its run and check commands, which name this project's own files (the
        next project gives its own)."""
        kept = dict(toolchain.to_dict(), run="", check="") if toolchain else None
        if kept and kept != self.language_doc.get("toolchain"):
            self.language_doc["toolchain"], self.language_doc["toolchain_saved"] = kept, now_text()
            self.save()

    def known_fix(self, failure, tried=()):
        """A replayable fix for exactly this failure, not tried yet in this run."""
        return next((f for f in self.language_doc.get("fixes", []) if f["failure"] == failure and f.get("replay")
                     and f["failure"] not in tried), None)

    def similar_fix(self, failure):
        scored = [(similarity(f["failure"], failure), f) for f in self.language_doc.get("fixes", [])]
        best = max(scored, key=lambda pair: pair[0], default=(0, None))
        return best[1] if best[0] >= SIMILAR_FAILURE else None

    def remember_fix(self, fix):
        fixes = [f for f in self.language_doc.setdefault("fixes", []) if f["failure"] != fix["failure"]]
        self.language_doc["fixes"] = (fixes + [record(dict(fix, used=1, added=now_text()), "born", self.run)])[-20:]
        self.save()

    def fix_result(self, fix, worked):
        """A replayed fix helped (counted) or failed (retired at once: it is disproved)."""
        if worked:
            fix["used"] = fix.get("used", 0) + 1
            record(fix, "helped", self.run)
        else:
            retire(self.language_doc, "fixes", lambda f: f["failure"] == fix["failure"], "its replay failed", self.run)
        self.save()

    def finished_project(self):
        self.language_doc["projects"] = self.language_doc.get("projects", 0) + 1
        self.save()

    def save(self):
        Home.store().put("languages", self.key, self.language_doc)
        Home.store().put("memory", "any", self.any_language_doc)


class Learner:
    """Reads the web for what a model may not know, or knows as it was when it was trained: the parts
    learning shares. Research (Researcher) reads pages through it (read_web: the searches run and their
    pages taken in turn, each search's first result, then each one's second, ...; the model condenses each
    page into the facts it states, each kept with its source; repeats are dropped). Learning a language or
    a tool by its slots (learning.SlotLearner) runs the commands that show what is installed here (only such
    commands: one each, no shell operators, nothing installed or changed), condenses its facts into a few
    rules (condense), keeps them in place of the facts they rest on, and indexes what it learned with the
    embedding model."""
    VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
    SHELL_OPERATORS = re.compile(r"[;&|<>`$\n\\]")
    # a fact about what is not out yet: never kept
    UNRELEASED = re.compile(r"\b(unreleased|not (yet )?released|(pre|future|upcoming)[- ]release|release candidate|"
                            r"draft(?![\w-])|in development|nightly)\b", re.I)
    CHANGES = re.compile(r"\b(install|uninstall|remove|delete|update|upgrade|add|rm|init|new|create|run|exec|"
                         r"download|fetch|get|set|config)\b", re.I)

    def __init__(self, model, internet, embeddings):
        self.model, self.internet = model, internet
        self.embeddings = embeddings  # None in a run whose embedding model failed: then not indexed
        self.model_failed = ""  # why the model stopped answering while reading pages (then reading ends)
        self.notes = []  # what could not be learned, and why
        self.kept_earlier = False  # reading was cut short, so the topic as learned before was kept
        self.tracker = None  # tinker learn's report (Tracker); a run's recall phase keeps its own steps

    def step(self, phase, said=""):
        """Learning is on to a phase (Tracker.STEPS): said here, when there is something to say, and its
        tracker (when it has one) moved on, so the chats show it."""
        if said:
            print(f"  {said}")
        if self.tracker is not None and self.tracker.state["step"] != phase:
            self.tracker.phase(phase)

    def start(self):
        self.notes, self.model_failed, self.kept_earlier = [], "", False

    def saved_or_asked(self, known, read, system, parts, label, what, saved_as):
        """(plan, whether it was saved): the plan saved before (known, as read reads it), else the
        model's; the command stops when the model gives no usable one (what: what it was for)."""
        if known:
            try:
                plan = read(known)
                print(f"  📋 Using the plan saved last time ({saved_as})")
                return plan, True
            except ValueError as exc:
                print(f"  ⚠️  The plan saved last time cannot be used ({exc}), so making a new one")
        plan = self.model.ask_until_usable(system, parts, label, lambda reply: read(reply.json()), attempts=2)
        if plan is None:
            raise SystemExit(f"{self.model.name} gave no usable plan for {what}; see the log of its replies "
                             f"(tinker memory; its calls are in tinker learn's log)")
        return plan, False

    @staticmethod
    def safe_commands(commands):
        """(kept, dropped): only commands that show what is installed are kept: one command each,
        no shell operators, nothing installed or changed."""
        kept, dropped = [], []
        for command in commands:
            command = " ".join(str(command).split())
            if not command:
                continue
            unsafe = (Learner.SHELL_OPERATORS.search(command) or Learner.CHANGES.search(command)
                      or Workspace.UNSAFE_COMMAND.search(command))
            (dropped if unsafe else kept).append(command)
        return kept, dropped

    def show_dropped(self, plan):
        for command in plan["dropped"]:
            print(f"     🚫 Skipped `{command}`: Tinker only runs commands that show what is installed (one command, "
                  f"no shell operators, nothing installed or changed)")

    @staticmethod
    def replace_proven(slug, entry):
        """What the language's earlier learning proved, replaced by what it learned now (Knowledge.supersede)."""
        shown = {item["id"] for item in Library.items({slug: entry}) if item["id"]}
        gone = Knowledge(entry["language"]).supersede(slug, shown)
        if gone:
            print(f"  ♻️  Replaced {gone} fact(s) from earlier learning with what was learned now")

    def look_at_machine(self, commands):
        """What each command shows (run in an empty folder): its first line and version number; a
        command that fails means that tool is not installed."""
        if commands:
            self.step("machine", "💻 Checking what is installed on this machine…")
        with tempfile.TemporaryDirectory(prefix="tinker-learn-") as folder:
            ws = Workspace(Path(folder))
            return [self.what_shows(ws, command) for command in commands]

    def what_shows(self, ws, command):
        code, output = ws.run(command, timeout=VERSION_TIMEOUT, quiet=True)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        version = self.VERSION_RE.search(" ".join(lines[:5])) if code == 0 else None
        shown = {"command": command, "found": code == 0, "version": version.group(0) if version else "",
                 "output": (lines[0] if lines else "")[:160]}
        failed = "did not finish" if code is None else f"exit code {code}"
        print(f"     ✅ {command} → {shown['output'][:100]}" if code == 0
              else f"     ❌ {command} → not installed ({failed})")
        return shown

    def read_web(self, name, searches, max_pages):
        """(facts, sources): the searches run, their pages taken in turn (each search's first
        result, then each one's second, ...: every search is read) up to max_pages, each read for
        what its search looks for; a fact of a section is kept with it, a fact stated again kept
        once (add_facts)."""
        results = [self.results(search) for search in searches]
        return self.read_pages(name, self.in_turn(searches, results, max_pages))

    def results(self, search):
        """The pages a search finds, as many as it reads (none, with a note, when the search fails)."""
        found = self.internet.search(search["query"], SEARCH_RESULTS)
        if found is None:
            self.notes.append(f"search '{search['query']}': {self.internet.why(search['query'])}")
            print(f"     ⚠️  {self.notes[-1]}")
        return (found or [])[:search["pages"]]

    @staticmethod
    def in_turn(searches, results, max_pages):
        """The pages taken in turn (each search's first, then each one's second, ...), each once, with its
        search's section and what it looks for."""
        pages, seen = [], set()
        for rank in range(SEARCH_RESULTS):
            for search, found in zip(searches, results):
                if rank < len(found) and found[rank]["url"] not in seen and len(pages) < max_pages:
                    seen.add(found[rank]["url"])
                    pages.append(dict(found[rank], section=search["section"], focus=search["focus"]))
        return pages

    def read_pages(self, name, pages):
        """(facts, sources) of the pages read until the model is lost."""
        facts, sources = [], []
        for number, page in enumerate(pages, 1):
            if self.model_failed:
                break
            found = self.read_page(name, page, f"{number}/{len(pages)}")
            sources += [page["url"]] if found else []
            found = [dict(f, section=page["section"]) if page["section"] else f for f in found]
            self.add_facts(facts, found)
        return facts, sources

    def read_page(self, topic, page, number):
        """The facts one page states about what its search looks for, from the parts of its text
        that hold the most of what it looks for (a page's top is often its menus), in page order;
        the search's excerpt when the page itself cannot be read."""
        got = self.internet.fetch(page["url"])
        text = (got or {}).get("content") or page.get("content") or ""
        if not text.strip():
            why = f"{page['url']}: {self.internet.why(page['url'])}"
            self.notes.append(f"page {why}")
            print(f"     ❌ Could not read page {number}: {why}")
            return []
        source = Part("Page", f"{(got or {}).get('title') or page.get('title', '')}\n{page['url']}", True)
        focus = Part("Look for", page["focus"], True)
        room = self.model.limit_chars(Prompts.FACTS_SYSTEM) - source.size() - focus.size() - len(topic) - 200
        found = []
        for i, chunk in enumerate(self.relevant_chunks(text, room, f"{topic} {page['focus']}"), 1):
            parts = [Part("Topic", topic, True), focus, source, Part("Excerpt", chunk, True)]
            facts = self.facts_in(parts, page, number, i)
            if facts is None:
                break
            found += [dict(f, source=page["url"]) for f in facts if not self.UNRELEASED.search(f["text"])]
        return found

    @staticmethod
    def relevant_chunks(text, room, wanted):
        """Up to PAGE_CHUNKS prompt-sized chunks of a page: its first (where a page says what it is
        about), then those the word search ranks the most related to what is looked for (the next
        ones when none matches), in page order: a long page's useful part is rarely all at its top."""
        chunks = split_text(text, room)
        if len(chunks) <= PAGE_CHUNKS:
            return chunks
        matched = [i for i in Retriever(None).by_words(wanted, chunks) if i]
        chosen = [0] + (matched + [i for i in range(1, len(chunks)) if i not in matched])[:PAGE_CHUNKS - 1]
        return [chunks[i] for i in sorted(chosen)]

    def facts_in(self, parts, page, number, part):
        """The facts the model reads in one chunk of a page; None when the page cannot be read on (a chunk too
        large), or the model stopped answering (it crashed or failed again and again: no page is read on)."""
        try:
            return self.model.ask_until_usable(Prompts.FACTS_SYSTEM, parts, f"Facts, page {number} part {part}",
                                               self.read_facts, attempts=2) or []
        except PromptTooLarge as exc:
            self.notes.append(f"page {page['url']}: {first_line(str(exc), 160)}")
        except OllamaError as exc:
            self.model_failed = first_line(str(exc), 200)
            self.notes.append(f"the model stopped answering at page {number}; the pages after it were not read")
            print(f"  ⚠️  {self.notes[-1]} ({self.model_failed})")
        return None

    @staticmethod
    def read_facts(reply):
        facts = []
        for item in reply.json().get("facts", []):
            if not isinstance(item, dict):
                continue
            text, phase = str(item.get("text", "")).strip()[:FACT_CHARS], str(item.get("phase", "")).strip().lower()
            if text and phase in FACT_PHASES:
                facts.append({"phase": phase, "text": text})
        return facts[:FACTS_PER_CHUNK]

    @staticmethod
    def add_facts(facts, found):
        """The found facts added to facts: a fact stated again is kept once, and when another page
        states it, that page joins its sources ("also")."""
        for fact in found:
            same = next((known for known in facts if similarity(fact["text"], known["text"]) >= SAME_FACT), None)
            if same is None:
                facts.append(fact)
            elif fact["source"] not in Learner.pages_of(same):
                same["also"] = same.get("also", []) + [fact["source"]]
        return facts

    @staticmethod
    def pages_of(fact):
        """The pages that state a fact."""
        return [fact["source"], *fact.get("also", [])]

    def cut_short(self):
        """Why the reading ended before every page was read ("" when it did not): the web or the
        model was lost on the way. Learning that was cut short never replaces what was learned before."""
        if not self.internet.available():
            return f"the web was lost ({self.internet.why()})"
        if self.model_failed:
            return f"the model stopped answering ({self.model_failed})"
        return ""

    def condense(self, plan, facts, think=None):
        """A topic's facts (a language's with the lessons its projects proved) condensed into its playbook (one
        call): at most PLAYBOOK_RULES rules a code generator follows, each citing the facts and lessons it rests
        on, so its prompts show a few rules in place of many facts. [] when the model gives no usable reply (the
        facts are then shown as they are); a model that fails raises, so nothing is kept. think: the call's
        think setting (None: the model's own)."""
        lessons = Knowledge(plan["language"]).proven_lessons() if plan.get("kind") == "language" else []
        sources = self.rule_sources(facts, lessons)
        self.step("condense", f"✍️  Turning {len(facts)} fact(s) into rules a code writer can follow…")
        rules = self.model.ask_until_usable(Prompts.PLAYBOOK_SYSTEM, self.playbook_parts(plan, facts, lessons),
                                            "Condense the facts",
                                            lambda reply: self.read_rules(reply.json(), sources), attempts=2,
                                            think=think)
        if not rules:
            self.notes.append("the facts were not condensed into rules (no usable reply)")
        print(f"     ✅ {len(rules)} rule(s)" if rules else "     ⚠️  Could not make rules: the facts are kept as they are")
        return rules or []

    @staticmethod
    def rule_sources(facts, lessons):
        """What a rule may cite, numbered as the prompt numbers it: the facts, then the lessons."""
        return [{"id": Library.claim_id(fact, fact["text"]), "text": fact["text"]} for fact in facts] + \
               [{"id": lesson.get("id") or fingerprint(lesson["text"])[:12], "text": lesson["text"]} for lesson in lessons]

    @staticmethod
    def playbook_parts(plan, facts, lessons):
        """The topic and its tools, its facts (with their slot and phase), then its lessons, numbered on."""
        listing = "\n".join(f"{n}. [{fact.get('slot', '')}, {fact['phase']}] {fact['text']}"
                            for n, fact in enumerate(facts, 1))
        proved = "\n".join(f"{n}. {lesson['text']}" for n, lesson in enumerate(lessons, len(facts) + 1))
        tools = Library.tools_text(plan.get("tools", [])) or "none named"
        return [Part("Topic", f"{plan['name']} ({plan.get('kind', 'tool')}); its tools: {tools}", True),
                Part("Facts learned from the web", listing, True, exact=True),
                Part("Lessons its projects proved", proved or "(none yet)", True, exact=True)]

    @staticmethod
    def read_rules(data, sources):
        """The rules, checked: each with a known phase, its text (cut to RULE_CHARS), when it applies ("" for
        every program) and the ids of the sources it cites by number; a rule that rests on none is dropped
        (never the model's own knowledge). What a new project declares (to get the newest version) is the first
        rule, for every program. At most PLAYBOOK_RULES, one per text."""
        rules = {}
        given = (data.get("rules") or []) if isinstance(data, dict) else []
        declare = data.get("declare") if isinstance(data, dict) else None
        if isinstance(declare, dict):  # what a new project declares, first: a rule for every program
            given = [dict(declare, phase="config", when="")] + list(given)
        for item in given:
            if not isinstance(item, dict):
                continue
            text, phase = " ".join(str(item.get("text") or "").split())[:RULE_CHARS], str(item.get("phase") or "").lower()
            cites = list(dict.fromkeys(sources[n - 1]["id"] for n in item.get("cites") or []
                                       if isinstance(n, int) and 1 <= n <= len(sources)))
            when = " ".join(str(item.get("when") or "").split())[:RULE_WHEN_CHARS]
            if text and phase in FACT_PHASES and cites and text not in rules:
                rules[text] = {"id": fingerprint(text)[:12], "phase": phase, "text": text, "when": when, "cites": cites}
        if not rules:
            raise ValueError("it needs at least one rule that cites the facts")
        return list(rules.values())[:PLAYBOOK_RULES]

    def index(self, slug, entry):
        """The texts embedded now, so the recall phase finds them by meaning (if that fails, the
        recall phase embeds them when it searches)."""
        if self.embeddings is None:
            return
        texts = [Library.searched(item) for item in Library.items({slug: entry})]
        if self.embeddings.vectors(texts) is None:
            print(f"  ⚠️  Not indexed now ({self.embeddings.failed}); a run indexes it when it searches")
        else:
            print(f"  🔍 Indexed with {self.embeddings.name}, so a run can find it by meaning")


class Researcher:
    """Researches a domain (tinker learn finds it is one) the way a careful person does, and keeps what it
    found where tinker learn keeps what it learns, so a run's recall phase finds it:
    1. frame: the model splits the topic into research questions, each with its own searches;
    2. gather: the pages they find are read for facts, each kept with its source (Learner);
    3. cross-check, with no model call: facts that make the same claim (close in meaning, by the
       embedding model) on other sites support each other when they give the same numbers, and are
       put side by side when they do not; a fact two or more sites state is corroborated;
    4. analyse: one call answers each question from the facts alone, citing them, says how sure
       each answer is, and names the contradictions and the gaps, with a search for each gap;
    5. one more round searches the gaps, and looks for the key facts one site states on others
       (checks); then everything is cross-checked and analysed again; a finding is never surer than
       its facts allow.
    Kept in Tinker's "learned" (kind "research"): the questions, the findings, the contradictions,
    the gaps left open, and every fact with its sources and confidence. Researching it again reuses
    the saved questions (edit them there)."""
    NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
    EMPTY_ANALYSIS = {"findings": [], "contradictions": [], "gaps": [], "checks": []}
    LEVELS = ("low", "medium", "high")  # how sure a finding is

    def __init__(self, learner):
        self.learner, self.model = learner, learner.model

    def research(self, topic):
        """The topic researched and saved (its entry), or None when nothing was found. A topic
        researched before is kept as it was when the web or the model is lost on the way."""
        learner, slug = self.learner, Library.slug(topic)
        learner.start()
        print(f"\n🔬 Researching {topic}")
        known = Library.read(slug)
        frame = self.frame(topic, known if (known or {}).get("kind") == "research" else None)
        facts, sources, analysis = self.investigate(frame)
        if known and (learner.cut_short() or not facts):
            print(f"  ⚠️  {learner.cut_short() or 'No page gave facts'}; kept what was found before "
                  f"({known.get('learned', '?')})")
            learner.kept_earlier = True
            return known
        if not facts:
            print(f"  ❌ Nothing found about {topic}: " + ("; ".join(learner.notes) or "the pages stated nothing useful"))
            return None
        entry = self.entry(topic, frame, facts, sources, analysis)
        learner.step("keep")
        Library.save(slug, entry)
        print(f"  💾 Saved {len(entry['findings'])} finding(s) from {len(facts)} fact(s) on {len(sources)} page(s) "
              f"as {slug}")
        learner.index(slug, entry)
        return entry

    def frame(self, topic, known):
        """The topic's name, summary and research questions (each with its searches): as saved for
        it, else from the model."""
        self.learner.step("frame")
        parts = [Part("Topic", topic, True), Part("Today", datetime.date.today().isoformat(), True)]
        frame, saved = self.learner.saved_or_asked(known, self.read_frame, Prompts.RESEARCH_PLAN_SYSTEM, parts,
                                                   "Frame the research", f"researching {topic}",
                                                   Library.slug(topic))
        if not saved:
            print(f"  🧭 {frame['name']}: {frame['summary']}")
        print("  📝 Questions to answer:")
        for number, question in enumerate(frame["questions"], 1):
            print(f"     Q{number}. {question['question']}")
        return frame

    @staticmethod
    def read_frame(data):
        """A research frame, checked: a name, a summary and up to RESEARCH_QUESTIONS questions, each
        with up to RESEARCH_QUERIES searches."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        name, summary = str(data.get("name") or "").strip()[:80], " ".join(str(data.get("summary") or "").split())[:200]
        questions = []
        for item in data.get("questions") or []:
            if not isinstance(item, dict):
                continue
            question = " ".join(str(item.get("question") or "").split())[:200]
            queries = [" ".join(str(q).split()) for q in item.get("queries") or [] if str(q).strip()][:RESEARCH_QUERIES]
            if question and queries:
                questions.append({"question": question, "queries": queries})
        if not name or not questions:
            raise ValueError("it needs a name and at least one question with its searches")
        return {"name": name, "summary": summary, "questions": questions[:RESEARCH_QUESTIONS]}

    def investigate(self, frame):
        """(facts, sources, analysis) after each round of RESEARCH_PAGES: the first round searches
        every question, each next one the gaps and checks the last analysis named. It ends early
        when none is left, or the web or the model is lost."""
        searches = [{"query": query, "section": str(number), "pages": SEARCH_RESULTS, "focus": question["question"]}
                    for number, question in enumerate(frame["questions"], 1) for query in question["queries"]]
        facts, sources, analysis = [], [], self.EMPTY_ANALYSIS
        for round_number, pages in enumerate(RESEARCH_PAGES, 1):
            if not searches:
                break
            self.learner.step("investigate",
                              f"🔎 Round {round_number}: {len(searches)} search(es), reading up to {pages} page(s)…")
            found, read = self.learner.read_web(frame["name"], searches, pages)
            self.learner.add_facts(facts, found)
            sources += [url for url in read if url not in sources]
            if self.learner.cut_short() or not facts:
                break
            differ = self.cross_check(facts)
            analysis = self.analyse(frame, facts, differ) or analysis
            searches = self.next_searches(frame, facts, analysis)
        return facts, sources, analysis

    def cross_check(self, facts):
        """Each fact's sites, with no model call. A fact that repeats another (REPEAT_CLAIM, numbers
        agreeing) is folded into it. A fact making the same claim as one on another site
        (SAME_CLAIM) shares its sites when their numbers agree; when they do not, the pair is
        returned, for the analysis to judge (facts about other things, versions or dates differ
        without contradicting). Without embeddings only the pages that repeat a fact count."""
        vectors = self.learner.embeddings.vectors([fact["text"] for fact in facts], keep=False) \
            if self.learner.embeddings else None
        if vectors is None:
            print("  ⚠️  Cross-checking by repeats only: the embedding model gave no embeddings")
            vectors = [None] * len(facts)
        else:
            self.fold_repeats(facts, vectors)
        sites = [{self.site(url) for url in Learner.pages_of(fact)} for fact in facts]
        support, differ = self.compared(facts, vectors, sites)
        for fact, found_on in zip(facts, support):
            fact["sites"] = sorted(found_on)
        return differ

    def compared(self, facts, vectors, sites):
        """([each fact's sites, with those of the facts on other sites that make its claim with the same
        numbers], [(n, m) of the facts that make one claim with other numbers])."""
        support, differ = [set(s) for s in sites], []
        for i, j in ((i, j) for i in range(len(facts)) for j in range(i + 1, len(facts))):
            if not self.same_claim(vectors[i], vectors[j]) or sites[i] & sites[j]:
                continue
            if self.agree(facts[i], facts[j]):
                support[i] |= sites[j]
                support[j] |= sites[i]
            else:
                differ.append((i + 1, j + 1))
        return support, differ

    def fold_repeats(self, facts, vectors):
        """Each fact that repeats an earlier one, folded into it (in place, with its vector): its
        pages join the earlier one's, and the more detailed text is kept."""
        i = 0
        while i < len(facts):
            kept = next((k for k in range(i) if self.same_claim(vectors[k], vectors[i], REPEAT_CLAIM)
                         and self.agree(facts[k], facts[i])), None)
            if kept is None:
                i += 1
                continue
            vectors.pop(i)
            self.fold(facts[kept], facts.pop(i))

    def fold(self, original, repeat):
        """A repeat's pages join the original's, and the more detailed text is kept."""
        if (len(self.numbers(repeat)), len(repeat["text"])) > (len(self.numbers(original)), len(original["text"])):
            original["text"] = repeat["text"]
        for url in Learner.pages_of(repeat):
            if url not in Learner.pages_of(original):
                original["also"] = original.get("also", []) + [url]

    @staticmethod
    def same_claim(a, b, threshold=SAME_CLAIM):
        return a is not None and b is not None and Embeddings.cosine(a, b) >= threshold

    @classmethod
    def agree(cls, a, b):
        """Whether two facts' numbers agree: one gives all the other's (it may give more detail)."""
        first, second = cls.numbers(a), cls.numbers(b)
        return first <= second or second <= first

    @classmethod
    def numbers(cls, fact):
        return set(cls.NUMBER_RE.findall(fact["text"]))

    @staticmethod
    def site(url):
        """The site a page is on (www. left out): pages of one site are one source."""
        return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")

    def analyse(self, frame, facts, differ):
        """The findings (each question answered from the facts, citing them, with how sure it is),
        the contradictions and the gaps (each with a search), from one call; None when the model
        gives no usable reply. The facts most sites state are shown first when not all fit."""
        questions = "\n".join(f"Q{n}. {q['question']}" for n, q in enumerate(frame["questions"], 1))
        pairs = "\n".join(f"F{a} / F{b}" for a, b in differ) or "(none)"
        fixed = [Part("Topic", frame["name"], True), Part("Questions", questions, True),
                 Part("Facts that may disagree", pairs, True)]
        room = self.model.limit_chars(Prompts.RESEARCH_ANALYSIS_SYSTEM) - Part.total_size(fixed) - 200
        parts = fixed + [Part("Facts", self.fact_listing(facts, room), True, exact=True)]
        try:
            return self.model.ask_until_usable(Prompts.RESEARCH_ANALYSIS_SYSTEM, parts, "Analyse the findings",
                                               lambda reply: self.read_analysis(reply.json(), len(facts),
                                                                                len(frame["questions"])), attempts=2)
        except (PromptTooLarge, OllamaError) as exc:
            self.learner.notes.append(f"the findings could not be analysed ({first_line(str(exc), 160)})")
            print(f"  ⚠️  {self.learner.notes[-1]}")
            return None

    @staticmethod
    def fact_listing(facts, room):
        """One line per fact, "F<n> [Q<question>, <k> site(s)] text", in their own order: as many
        as fit in room chars, the facts most sites state kept first."""
        lines = {n: f"F{n} [Q{fact.get('section', '?')}, {len(fact.get('sites', []))} site(s)] {fact['text']}"
                 for n, fact in enumerate(facts, 1)}
        kept, used = set(), 0
        for n in sorted(lines, key=lambda n: -len(facts[n - 1].get("sites", []))):
            if used + len(lines[n]) + 1 <= room:
                kept.add(n)
                used += len(lines[n]) + 1
        return "\n".join(lines[n] for n in sorted(kept))

    @staticmethod
    def read_analysis(data, fact_count, question_count):
        """The analysis, checked: findings for known questions citing known facts, contradictions
        between known facts (with the doubtful ones among them), up to RESEARCH_GAPS gaps and
        RESEARCH_CHECKS checks, with a search each."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        cited = lambda numbers: sorted({n for n in numbers or [] if isinstance(n, int) and 1 <= n <= fact_count})
        asked = lambda item: isinstance(item, dict) and isinstance(item.get("question"), int) \
            and 1 <= item["question"] <= question_count
        findings = Researcher.read_findings(data.get("findings") or [], cited, asked)
        if not findings:
            raise ValueError("it needs a finding for at least one question")
        return {"findings": findings, "contradictions": Researcher.read_contradictions(data.get("contradictions") or [],
                                                                                       cited),
                "gaps": Researcher.read_gaps(data.get("gaps") or [], asked)[:RESEARCH_GAPS],
                "checks": Researcher.read_checks(data.get("checks") or [], fact_count)[:RESEARCH_CHECKS]}

    @staticmethod
    def read_findings(given, cited, asked):
        return [{"question": f["question"], "answer": " ".join(str(f.get("answer") or "").split())[:600],
                 "confidence": f.get("confidence") if f.get("confidence") in ("high", "medium", "low") else "low",
                 "facts": cited(f.get("facts"))}
                for f in given if asked(f) and str(f.get("answer") or "").strip()]

    @staticmethod
    def read_contradictions(given, cited):
        """Contradictions between two or more known facts, with the doubtful ones among them (unsure: all are)."""
        contradictions = []
        for item in given:
            if isinstance(item, dict) and len(cited(item.get("facts"))) >= 2:
                facts = cited(item["facts"])
                doubtful = [n for n in cited(item.get("doubtful")) if n in facts] or facts
                contradictions.append({"facts": facts, "doubtful": doubtful,
                                       "note": " ".join(str(item.get("note") or "").split())[:300]})
        return contradictions

    @staticmethod
    def read_gaps(given, asked):
        return [{"question": g["question"], "missing": " ".join(str(g.get("missing") or "").split())[:200],
                 "search": " ".join(str(g.get("search") or "").split())[:160]}
                for g in given if asked(g) and str(g.get("search") or "").strip()]

    @staticmethod
    def read_checks(given, fact_count):
        return [{"fact": c["fact"], "search": " ".join(str(c.get("search") or "").split())[:160]}
                for c in given if isinstance(c, dict) and isinstance(c.get("fact"), int)
                and 1 <= c["fact"] <= fact_count and str(c.get("search") or "").strip()]

    @staticmethod
    def next_searches(frame, facts, analysis):
        """The next round's searches: one page for each gap, and two for each check (a key fact one
        site states, looked for on another, independent one)."""
        gaps = [{"query": gap["search"], "section": str(gap["question"]), "pages": 1,
                 "focus": frame["questions"][gap["question"] - 1]["question"]} for gap in analysis["gaps"]]
        checks = [{"query": check["search"], "section": facts[check["fact"] - 1]["section"], "pages": 2,
                   "focus": f"whether this is so, and the exact figures: {facts[check['fact'] - 1]['text']}"}
                  for check in analysis["checks"]]
        return gaps + checks

    def entry(self, topic, frame, facts, sources, analysis):
        """What is saved: each fact with its question, its sources and its confidence (disputed: a
        contradiction finds it doubtful; corroborated: two or more sites state it; single: one does),
        and each finding no surer than its facts allow (supported)."""
        disputed = {*contradiction["doubtful"] for contradiction in analysis["contradictions"]}
        kept = [self.kept_fact(fact, n in disputed) for n, fact in enumerate(facts, 1)]
        cited = lambda finding: [kept[n - 1] for n in finding["facts"]]
        findings = [dict(finding, confidence=self.supported(finding["confidence"], cited(finding)))
                    for finding in analysis["findings"]]
        return {"topic": topic, "kind": "research", "name": frame["name"], "summary": frame["summary"],
                "questions": frame["questions"], "learned": now_text(), "model": self.model.name, "machine": [],
                "findings": findings, "contradictions": analysis["contradictions"],
                "open": analysis["gaps"], "facts": kept, "sources": sources, "notes": self.learner.notes}

    @staticmethod
    def kept_fact(fact, disputed):
        """A fact as it is kept: with its question, its sites and how sure it is."""
        sites = fact.pop("sites", [])
        confidence = "disputed" if disputed else "corroborated" if len(sites) >= 2 else "single"
        return {**{k: v for k, v in fact.items() if k != "section"}, "question": int(fact["section"]),
                "sites": sites, "confidence": confidence}

    @classmethod
    def supported(cls, stated, cited):
        """How sure a finding may be, whatever the model said: high only when at least half the facts
        it cites are corroborated and none is disputed; medium when it cites any; else low."""
        corroborated = sum(fact["confidence"] == "corroborated" for fact in cited)
        disputed = any(fact["confidence"] == "disputed" for fact in cited)
        ceiling = "high" if cited and not disputed and 2 * corroborated >= len(cited) else "medium" if cited else "low"
        return min(stated, ceiling, key=cls.LEVELS.index)


class Library:
    """What Tinker learned about topics and languages, one entry each in Tinker's "learned", from tinker
    learn WHAT or a run's recall phase (a language under its language_key). Each fact and finding has an
    id and a status: a candidate until a cycle that used it succeeds. A run shows the facts of the
    topics its recall phase chose (ranked in the project's "learned") in the prompts of the phases they
    concern, as an optional part. A language's rules (its playbook) are claims too, shown in place of the
    facts they cite."""
    CLAIMS = ("facts", "findings", "rules")  # what in a topic is proven or retired

    @staticmethod
    def topics():
        """{slug: entry} of every topic learned."""
        return {slug: entry for slug, entry in Home.store().items("learned").items() if Library.readable(entry)}

    @staticmethod
    def read(slug):
        """A topic's entry, or None (not learned, or not readable)."""
        entry = Home.store().get("learned", slug) if slug else None
        return entry if Library.readable(entry) else None

    @staticmethod
    def readable(entry):
        return isinstance(entry, dict) and isinstance(entry.get("facts", []), list)

    @staticmethod
    def save(slug, entry):
        """A topic kept: each fact, finding and rule with an id and a status (a candidate until proven), and each
        finding with the ids of the facts it cites (its numbers no longer count once a fact is retired)."""
        for claim, text in [(f, f.get("text", "")) for f in entry.get("facts", []) + entry.get("rules", [])] + \
                           [(f, f.get("answer", "")) for f in entry.get("findings", [])]:
            claim.setdefault("id", fingerprint(str(text))[:12])
            claim.setdefault("status", "candidate")
        facts = entry.get("facts", [])
        for finding in entry.get("findings", []):
            finding.setdefault("cites", [facts[n - 1]["id"] for n in finding.get("facts", [])
                                         if isinstance(n, int) and 1 <= n <= len(facts)])
        Home.store().put("learned", slug, entry)

    @staticmethod
    def claims(slug, ids, change):
        """The claims of a topic with these ids, changed by change(claim) -> claim or None (gone)."""
        def changed(entry):
            if not entry:
                return entry
            for key in Library.CLAIMS:
                entry[key] = [kept for kept in (change(c) if c.get("id") in ids else c for c in entry.get(key, []))
                              if kept is not None]
            return entry
        if Library.read(slug):
            Home.store().update("learned", slug, changed)

    @staticmethod
    def retire(slug, ids, reason, run):
        """The claims of a topic with these ids, retired; how many."""
        gone = []
        def retired_in(entry):
            if entry:
                gone.append(sum(retire(entry, key, lambda claim: claim.get("id") in ids, reason, run)
                                for key in Library.CLAIMS))
            return entry
        if Library.read(slug):
            Home.store().update("learned", slug, retired_in)
        return sum(gone)

    @staticmethod
    def slug(topic):
        """"Test-driven development" -> test-driven-development, "C++" -> cpp, "Python 3.14" -> python-3-14."""
        text = topic.lower().replace("+", "p").replace("#", "sharp")
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60]

    @staticmethod
    def language(name):
        """The key of a language learned (with tinker learn, or by a run), under any version of its name; or None."""
        key = language_key(name) if (name or "").strip() else ""
        entry = Library.read(key) if key else None
        return key if entry and entry.get("kind") == "language" else None

    @staticmethod
    def items(topics):
        """What can be found in these topics ({slug: entry}), one item per text: each topic's
        summary, a language's tools, what is installed here, research findings, a language's rules
        (kind "rule") and each fact shown (facts_shown, with its confidence when researched), as
        {topic, name, phase, text, id, status}: a claim's id and status (None for what is not a
        claim). A topic that cannot be read is left out, with a note."""
        found = []
        for slug, entry in topics.items():
            try:
                found += Library.topic_items(slug, entry)
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                print(f"  note: what was learned about {slug} is not readable ({type(exc).__name__}: {exc}); left out")
        return found

    @staticmethod
    def topic_items(slug, entry):
        name = str(entry.get("name") or entry["topic"])
        plain = lambda phase, text: {"topic": slug, "name": name, "phase": phase, "text": text, "id": None,
                                     "status": None}
        items = [plain("summary", str(entry.get("summary") or name))]
        if entry.get("tools"):
            items.append(plain("tools", f"its tools: {Library.tools_text(entry['tools'])}"))
        installed = [(m["output"] if m["found"] else f"`{m['command']}` fails (not installed)")
                     for m in entry.get("machine", [])]
        if installed:
            items.append(plain("machine", f"on this machine: {'; '.join(installed)}"))
        items += [{"topic": slug, "name": name, "phase": "findings",
                   "text": f"{entry['questions'][f['question'] - 1]['question']} {f['answer']} "
                           f"({f['confidence']} confidence)", "id": Library.claim_id(f, f["answer"]),
                   "status": f.get("status", "candidate")} for f in entry.get("findings", [])]
        items += [{"topic": slug, "name": name, "phase": str(r["phase"]), "text": str(r["text"]), "kind": "rule",
                   "when": str(r.get("when") or ""), "id": Library.claim_id(r, r["text"]),
                   "status": r.get("status", "candidate")} for r in entry.get("rules", [])]
        return items + [{"topic": slug, "name": name, "phase": str(f["phase"]),
                         "text": str(f["text"]) + (f" ({f['confidence']})" if f.get("confidence") else ""),
                         "id": Library.claim_id(f, f["text"]), "status": f.get("status", "candidate")}
                        for f in Library.facts_shown(entry)]

    @staticmethod
    def facts_shown(entry):
        """A topic's facts as its prompts may show them: all of them, unless its rules (a playbook) stand in for
        them; then only those a retired rule cited and no rule in use cites (what the retired rule said is
        open again)."""
        rules = entry.get("rules", [])
        if not rules:
            return entry["facts"]
        cited = lambda given: {*rule.get("cites", []) for rule in given}
        reopened = cited(r for r in entry.get("retired", []) if r.get("was") == "rules") - cited(rules)
        return [fact for fact in entry["facts"] if Library.claim_id(fact, fact["text"]) in reopened]

    @staticmethod
    def claim_id(claim, text):
        return claim.get("id") or fingerprint(str(text))[:12]

    @staticmethod
    def tools_text(tools):
        """A language's tools in one line: "environment venv, tests pytest, format black"."""
        return ", ".join(f"{tool['role']} {tool['name']}" for tool in tools if isinstance(tool, dict) and tool.get("name"))

    @staticmethod
    def searched(item):
        """The text an item is found by (and embedded as): its topic's name, and its own text."""
        return f"{item['name']}: {item['text']}"

    @staticmethod
    def shown_in(item, phases):
        """Whether an item belongs in a prompt for these phases: what is installed here goes to the
        toolchain and config files' prompts, a language's tools to those and the tests', research
        findings to the spec's and the design's, a fact to its own phase."""
        shown = {"machine": {"toolchain", "config"}, "tools": {"toolchain", "config", "tests"},
                 "findings": {"spec", "design"}}.get(item["phase"])
        return item["phase"] in phases or bool(shown and shown & set(phases))


class Retriever:
    """Hybrid search over what was learned, so nothing relevant is missed: a word search (BM25:
    exact terms such as names and versions count) and, with an embedding model (Embeddings), a
    search by meaning (cosine similarity: "web framework" finds Flask). The two rankings are fused
    (reciprocal rank fusion): what either finds ranks high, what both find ranks highest, and what
    neither finds comes last, never dropped."""
    RRF_K = 60     # the usual constant of reciprocal rank fusion: rank 1 counts 1/61, rank 10 1/70
    BM25_K1, BM25_B = 1.5, 0.75

    def __init__(self, embeddings):
        self.embeddings = embeddings  # None: the word search alone
        self.used = "words"
        self.queries = {}  # query -> its embedding

    def rank(self, query, texts, prune=False):
        """The indexes of texts, the most related to query first (all of them)."""
        if not texts:
            return []
        fused = {}
        for ranking in (self.by_words(query, texts), self.by_meaning(query, texts, prune)):
            for position, index in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1.0 / (self.RRF_K + position + 1)
        found = sorted(fused, key=lambda index: (-fused[index], index))
        return found + [index for index in range(len(texts)) if index not in fused]

    def by_words(self, query, texts):
        """BM25: the texts holding the query's words, the best match first (the others not at all)."""
        scores = [(score, index) for index, score in enumerate(self.word_scores(query, texts))]
        return [index for _, index in sorted((s for s in scores if s[0] > 0), key=lambda pair: (-pair[0], pair[1]))]

    def word_scores(self, query, texts):
        """Each text's BM25 score for the query's words, in order (0 for a text that holds none)."""
        if not texts:
            return []
        terms, docs = set(words(query)), [words(text) for text in texts]
        average = sum(len(doc) for doc in docs) / len(docs) or 1
        holding = collections.Counter(*(set(doc) & terms) for doc in docs)
        return [self.bm25(terms, doc, len(docs), holding, average) for doc in docs]

    def bm25(self, terms, doc, total, holding, average):
        """One text's score: each query term it holds, weighed by how rare the term is and how often the
        text says it, for its length."""
        counts, score = collections.Counter(doc), 0.0
        for term in terms & counts.keys():
            idf = math.log(1 + (total - holding[term] + 0.5) / (holding[term] + 0.5))
            tf, length = counts[term], len(doc) / average
            score += idf * tf * (self.BM25_K1 + 1) / (tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * length))
        return score

    def by_meaning(self, query, texts, prune):
        """Every text by the cosine similarity of its embedding to the query's, the closest first
        (nothing without an embedding model, or when it fails: the word search then stands alone)."""
        if self.embeddings is None:
            return []
        vectors = self.embeddings.vectors(texts, prune=prune)
        if vectors and query not in self.queries:
            asked = self.embeddings.vectors([query], keep=False)
            self.queries[query] = asked[0] if asked else None
        if not vectors or not self.queries.get(query):
            print(f"  meaning search not used: {self.embeddings.failed}; the word search stands alone")
            self.embeddings = None  # not asked again in this run
            return []
        self.used = f"words and meaning ({self.embeddings.name})"
        similarity_of = [Embeddings.cosine(self.queries[query], vector) for vector in vectors]
        return sorted(range(len(texts)), key=lambda index: (-similarity_of[index], index))


class Embeddings:
    """Embeddings from the embedding model selected with tinker embed NAME (none: the word search
    works alone), kept in Tinker's "embeddings" by text, per model, so each text is embedded once.
    Local: nothing goes online."""
    BATCH = 32  # texts per request

    def __init__(self, name):
        self.name, self.failed = name, ""
        self.cache = {"model": name, "vectors": Home.store().get("embeddings", name, {})}

    @staticmethod
    def required(option):
        """The embedding model, for an option that learns: one must be selected (tinker embed NAME)."""
        if not Config.get("embed_model"):
            raise SystemExit(f"{option} needs an embedding model, which indexes what it learns: select one Ollama "
                             f"has with tinker embed NAME (tinker models lists them)")
        Embeddings.check()
        return Embeddings(Config.get("embed_model"))

    @staticmethod
    def check():
        """The embedding model selected (tinker embed NAME), if any, is one Ollama has (checked before
        anything starts)."""
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
        """The embedding model in use, or None (embed_model is "")."""
        name = Config.get("embed_model")
        return Embeddings(name) if name else None

    def vectors(self, texts, keep=True, prune=False):
        """One embedding per text (from the cache, the missing ones asked for in batches), or None
        when the model does not give them (failed says why). keep: cache them; prune: the cache then
        keeps only these texts' embeddings."""
        stored = self.cache["vectors"]
        keys = [self.key(text) for text in texts]
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in stored))
        fresh = {}
        for start in range(0, len(missing), self.BATCH):
            batch = missing[start:start + self.BATCH]
            found = self.embedded(batch)
            if found is None:
                return None
            fresh.update({self.key(text): [round(x, 5) for x in vector] for text, vector in zip(batch, found)})
        result = [stored.get(key) or fresh[key] for key in keys]
        if keep and (fresh or prune):
            self.kept(fresh, keys if prune else None)
        return result

    def embedded(self, batch):
        """The batch's embeddings, as the model gives them; None when it gives none (failed says why)."""
        data = Ollama.get_json("/api/embed", {"model": self.name, "input": batch, "keep_alive": 0}, timeout=300,
                               errors=True)
        found = data.get("embeddings") if isinstance(data, dict) else None
        if isinstance(found, list) and len(found) == len(batch) and all(self.is_vector(v) for v in found):
            return found
        self.failed = f"{self.name} gave no embeddings" + (f" ({data['error']})" if isinstance(data, dict)
                                                           and data.get("error") else "")
        return None

    @staticmethod
    def is_vector(value):
        return isinstance(value, list) and value and all(isinstance(x, (int, float)) for x in value)

    def kept(self, fresh, only=None):
        """The fresh embeddings cached; only: the cache then keeps these keys alone."""
        stored = self.cache["vectors"]
        stored.update(fresh)
        if only is not None:
            self.cache["vectors"] = {key: stored[key] for key in only}
        kept = self.cache["vectors"]
        Home.store().update("embeddings", self.name, lambda saved: {**({} if only is not None else saved or {}), **kept})

    @staticmethod
    def key(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / size if size else 0.0


def record(item, event, run):
    """An event in an item's history (born, confirmed, helped, failed), the last HISTORY_KEPT; helped is
    also counted, beyond them. The item, changed."""
    item["history"] = (item.get("history", []) + [{"event": event, "run": run, "at": now_text()}])[-HISTORY_KEPT:]
    if event == "helped":
        item["helped"] = item.get("helped", 0) + 1
    return item


def retire(doc, key, gone, reason, run):
    """The items of doc[key] that gone(item) picks, moved to doc["retired"] (the last RETIRED_KEPT) with why
    and the run: never shown again, kept for people to see. How many."""
    items = doc.get(key, [])
    leaving = [item for item in items if gone(item)]
    doc[key] = [item for item in items if not gone(item)]
    retired = [record(dict(item, status="retired", reason=reason, retired=now_text(), was=key), "failed", run)
               for item in leaving]
    if retired:
        doc["retired"] = (doc.get("retired", []) + retired)[-RETIRED_KEPT:]
    return len(retired)


def mentions(text, name):
    """Whether text names a tool (uv in `uv_build` and `uv.lock` too, not in `uvicorn`)."""
    return bool(re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I))


def language_key(name):
    """A language's one identity: its first word, without a version. "Python 3.12" -> python,
    "JavaScript (Node.js)" -> javascript, "C++" -> cpp, "C#" -> csharp."""
    base = (name or "").split("(")[0].lower().replace("+", "p").replace("#", "sharp")
    found = re.findall(r"[a-z][a-z0-9]*", base)
    return found[0] if found else "unknown"
