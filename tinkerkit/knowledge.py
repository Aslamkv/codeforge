"""What Tinker knows beyond the model: lessons, toolchain fixes and proven facts from earlier projects
(Knowledge), and what it learns from the web (Learner, Researcher), kept and searched (Library,
Retriever, Embeddings). What it learns starts as a candidate: a cycle that succeeds using it proves it,
and a failure in the step it was meant to help retires it (kept apart, never shown again). Each item
keeps its history: the run that taught it, and the runs it helped or failed."""

lazy import collections
lazy import hashlib
lazy import math
import re
lazy import tempfile
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request
from collections import namedtuple
lazy from pathlib import Path

lazy from .jobs import today
from .messages import say
lazy from .model import Models, Ollama, OllamaError
lazy from .prompts import Part, PromptTooLarge, Prompts
from .settings import (
    CHUNKS_PER_PAGE, EVENTS_KEPT_PER_ITEM, FACTS_PER_CHUNK, FACT_PHASES, LESSONS_PER_PROMPT, LESSON_PHASES,
    MAX_FACT_CHARS, MAX_LESSONS_PER_FILE, MAX_RULE_CHARS, MAX_RULE_WHEN_CHARS, PLAYBOOK_RULES,
    REPEAT_CLAIM_SIMILARITY, RESEARCH_FACTS_CHECKED, RESEARCH_GAPS, RESEARCH_PAGES_PER_ROUND,
    RESEARCH_QUESTIONS, RESULTS_PER_RESEARCH_SEARCH, RETIRED_KEPT_PER_LIST, SAME_CLAIM_SIMILARITY,
    SAME_FACT_SIMILARITY, SAME_LESSON_SIMILARITY, SEARCHES_PER_RESEARCH_QUESTION, TOOLCHAIN_HINT_SIMILARITY,
    VERSION_TIMEOUT, Config, Home, fingerprint, first_line, now_text, similarity, split_text, words,
)
lazy from .workspace import Toolchain, Workspace


SavedPlan = namedtuple("SavedPlan", "plan saved")
SafeCommands = namedtuple("SafeCommands", "kept dropped")
Findings = namedtuple("Findings", "facts sources")
Investigation = namedtuple("Investigation", "facts sources analysis")
CrossCheck = namedtuple("CrossCheck", "support differ")


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
        named = [name for name in dict.fromkeys(known_name for known_name in known if known_name)
                 if mentions(text or "", name)]
        return min(named, key=lambda name: (text or "").lower().index(name.lower()), default=None)

    def summary(self):
        lessons = len(self.language_doc.get("lessons", [])) + len(self.any_language_doc.get("lessons", []))
        projects, facts = self.language_doc.get("projects", 0), len(self.language_doc.get("facts", []))
        known = [("the toolchain that worked" + (f" ({projects} finished project(s))" if projects else ""))
                 if self.language_doc.get("toolchain") else "",
                 f"{len(self.language_doc.get('fixes', []))} toolchain fix(es)" if self.language_doc.get("fixes") else "",
                 f"{lessons} lesson(s)" if lessons else "", f"{facts} proven fact(s)" if facts else ""]
        return f"{self.language}: " + (", ".join(topic for topic in known if topic) or "nothing learned yet")

    def lessons(self, phases, limit=LESSONS_PER_PROMPT):
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
        same = next((lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON_SIMILARITY), None)
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
        """At most MAX_LESSONS_PER_FILE, the most confirmed first; the new ones always stay (the least
        confirmed older ones go to make room)."""
        rank = Knowledge.rank
        fresh = [lesson for lesson in lessons if any(lesson is new_lesson for new_lesson in new)]
        older = sorted((lesson for lesson in lessons if not any(lesson is new_lesson for new_lesson in new)), key=rank,
                       reverse=True)
        return sorted(fresh + older[:max(0, MAX_LESSONS_PER_FILE - len(fresh))], key=rank, reverse=True)

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
            claims = [claim for key in Library.CLAIM_KINDS for claim in entry.get(key, []) if claim.get("id") in ids]
            proven += sum(claim.get("status") != "proven" for claim in claims)
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
        failed = " ".join(tool["command"] for tool in entry.get("machine", []) if not tool.get("found"))
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
        return next((fix for fix in self.language_doc.get("fixes", []) if fix["failure"] == failure and fix.get("replay")
                     and fix["failure"] not in tried), None)

    def similar_fix(self, failure):
        scored = [(similarity(fix["failure"], failure), fix) for fix in self.language_doc.get("fixes", [])]
        best = max(scored, key=lambda pair: pair[0], default=(0, None))
        return best[1] if best[0] >= TOOLCHAIN_HINT_SIMILARITY else None

    def remember_fix(self, fix):
        fixes = [kept for kept in self.language_doc.setdefault("fixes", []) if kept["failure"] != fix["failure"]]
        self.language_doc["fixes"] = (fixes + [record(dict(fix, used=1, added=now_text()), "born", self.run)])[-20:]
        self.save()

    def fix_result(self, fix, worked):
        """A replayed fix helped (counted) or failed (retired at once: it is disproved)."""
        if worked:
            fix["used"] = fix.get("used", 0) + 1
            record(fix, "helped", self.run)
        else:
            retire(self.language_doc, "fixes", lambda known: known["failure"] == fix["failure"], "its replay failed",
                   self.run)
        self.save()

    def finished_project(self):
        self.language_doc["projects"] = self.language_doc.get("projects", 0) + 1
        self.save()

    def retire_item(self, key, reason):
        """One of the language's lessons, proven facts or fixes, or a lesson for any language, retired by its
        key (lesson:ID, known:ID, fix:FAILURE); whether it was. Only the list it left is saved."""
        kind, _, rest = key.partition(":")
        picks = {"lesson": ("lessons", lambda item: item.get("id") == rest),
                 "known": ("facts", lambda item: item.get("id") == rest),
                 "fix": ("fixes", lambda item: item.get("failure") == rest)}
        if kind not in picks:
            return False
        name, gone = picks[kind]
        if retire(self.language_doc, name, gone, reason, self.run):
            self.save_language()
            return True
        if kind == "lesson" and retire(self.any_language_doc, name, gone, reason, self.run):
            self.save_any_language()
            return True
        return False

    def correct_toolchain(self, changes):
        """The toolchain that worked, with the commands the person taught (and Tinker checked) in place of its
        own; None when there is none to correct, or the corrected one is not usable."""
        known = self.toolchain()
        corrected = known.merged(changes) if known else None
        if not corrected:
            return None
        self.language_doc["toolchain"], self.language_doc["toolchain_saved"] = corrected.to_dict(), now_text()
        self.save_language()
        return corrected

    def taught_lesson(self, item, sources):
        """A lesson the person taught and Tinker checked, kept as proven with the sources that showed it (one like
        a known lesson proves that one instead); None when it is not a usable lesson. scope: language or any."""
        known = self.known_lessons()
        text = str(item.get("text", "")).strip()
        lesson = next((lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON_SIMILARITY), None)
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
    UNRELEASED_RE = re.compile(r"\b(unreleased|not (yet )?released|(pre|future|upcoming)[- ]release|release candidate|"
                            r"draft(?![\w-])|in development|nightly)\b", re.I)
    CHANGES = re.compile(r"\b(install|uninstall|remove|delete|update|upgrade|add|rm|init|new|create|run|exec|"
                         r"download|fetch|get|set|config)\b", re.I)

    def __init__(self, model, internet, embeddings):
        self.model, self.internet = model, internet
        self.embeddings = embeddings
        self.model_failure = ""
        self.notes = []
        self.kept_earlier_topic = False
        self.tracker = None

    def step(self, phase, said="", icon=""):
        """Learning is on to a phase (Tracker.DOING_AND_DONE): said, when there is something to say, and its
        tracker (when it has one) moved on, so the chats show it."""
        if said:
            say.step(said, icon)
        if self.tracker is not None and self.tracker.state["step"] != phase:
            self.tracker.phase(phase)

    def outcome(self, text):
        """What came of the phase learning is on, for its tracker (when it has one)."""
        if self.tracker is not None:
            self.tracker.outcome(text)

    def start(self):
        self.notes, self.model_failure, self.kept_earlier_topic = [], "", False

    def saved_or_asked(self, known, read, system, parts, label, what, saved_as):
        """(plan, whether it was saved): the plan saved before (known, as read reads it), else the
        model's; the command stops when the model gives no usable one (what: what it was for)."""
        if known:
            try:
                plan = read(known)
                say.info(f"Using the plan saved last time ({saved_as})", "📋")
                return SavedPlan(plan, True)
            except ValueError as exc:
                say.warn(f"The plan saved last time cannot be used ({exc}), so making a new one")
        plan = self.model.ask_until_usable(system, parts, label, lambda reply: read(reply.json()), attempts=2)
        if plan is None:
            raise SystemExit(f"{self.model.name} gave no usable plan for {what}; see the log of its replies "
                             f"(tinker memory; its calls are in tinker learn's log)")
        return SavedPlan(plan, False)

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
        return SafeCommands(kept, dropped)

    def show_dropped(self, plan):
        with say.section():
            for command in plan["dropped"]:
                say.info(f"Skipped `{command}`: Tinker only runs commands that show what is installed (one command, "
                         f"no shell operators, nothing installed or changed)", "🚫")

    @staticmethod
    def replace_proven(slug, entry):
        """What the language's earlier learning proved, replaced by what it learned now (Knowledge.supersede)."""
        shown = {item["id"] for item in Library.items({slug: entry}) if item["id"]}
        gone = Knowledge(entry["language"]).supersede(slug, shown)
        if gone:
            say.info(f"Replaced {gone} fact(s) from earlier learning with what was learned now", "♻️")

    def look_at_machine(self, commands):
        """What each command shows (run in an empty folder): its first line and version number; a
        command that fails means that tool is not installed."""
        if commands:
            self.step("machine", "Checking what is installed on this machine…", "💻")
        with tempfile.TemporaryDirectory(prefix="tinker-learn-") as folder, say.section():
            workspace = Workspace(Path(folder))
            return [self.what_shows(workspace, command) for command in commands]

    def what_shows(self, workspace, command):
        code, output = workspace.run(command, timeout=VERSION_TIMEOUT, quiet=True)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        version = self.VERSION_RE.search(" ".join(lines[:5])) if code == 0 else None
        shown = {"command": command, "found": code == 0, "version": version.group(0) if version else "",
                 "output": (lines[0] if lines else "")[:160]}
        failed = "did not finish" if code is None else f"exit code {code}"
        if code == 0:
            say.done(f"{command} → {shown['output'][:100]}")
        else:
            say.fail(f"{command} → not installed ({failed})")
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
        found = self.internet.search(search["query"], RESULTS_PER_RESEARCH_SEARCH)
        if found is None:
            self.notes.append(f"search '{search['query']}': {self.internet.problem(search['query'])}")
            with say.section():
                say.warn(self.notes[-1])
        return (found or [])[:search["pages"]]

    @staticmethod
    def in_turn(searches, results, max_pages):
        """The pages taken in turn (each search's first, then each one's second, ...), each once, with its
        search's section and what it looks for."""
        pages, seen = [], set()
        for rank in range(RESULTS_PER_RESEARCH_SEARCH):
            for search, found in zip(searches, results):
                if rank < len(found) and found[rank]["url"] not in seen and len(pages) < max_pages:
                    seen.add(found[rank]["url"])
                    pages.append(dict(found[rank], section=search["section"], focus=search["focus"]))
        return pages

    def read_pages(self, name, pages):
        """(facts, sources) of the pages read until the model is lost."""
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
        """The facts one page states about what its search looks for, from the parts of its text
        that hold the most of what it looks for (a page's top is often its menus), in page order;
        the search's excerpt when the page itself cannot be read."""
        got = self.internet.fetch(page["url"])
        text = (got or {}).get("content") or page.get("content") or ""
        if not text.strip():
            return self.unreadable(page, number)
        source = Part("Page", f"{(got or {}).get('title') or page.get('title', '')}\n{page['url']}", True)
        focus = Part("Look for", page["focus"], True)
        room = self.room_for_excerpts(topic, source, focus)
        found = []
        for chunk_number, chunk in enumerate(self.relevant_chunks(text, room, f"{topic} {page['focus']}"), 1):
            parts = [Part("Topic", topic, True), focus, source, Part("Excerpt", chunk, True)]
            facts = self.facts_in(parts, page, number, chunk_number)
            if facts is None:
                break
            found += [dict(fact, source=page["url"]) for fact in facts if not self.UNRELEASED_RE.search(fact["text"])]
        return found

    def unreadable(self, page, number):
        """No facts from a page that cannot be read: why is noted and said."""
        why = f"{page['url']}: {self.internet.problem(page['url'])}"
        self.notes.append(f"page {why}")
        with say.section():
            say.fail(f"Could not read page {number}: {why}")
        return []

    def room_for_excerpts(self, topic, source, focus):
        """The characters a page's excerpt may take, next to the prompt and what it always shows."""
        return self.model.limit_chars(Prompts.FACTS_SYSTEM) - source.size() - focus.size() - len(topic) - 200

    @staticmethod
    def relevant_chunks(text, room, wanted):
        """Up to CHUNKS_PER_PAGE prompt-sized chunks of a page: its first (where a page says what it is
        about), then those the word search ranks the most related to what is looked for (the next
        ones when none matches), in page order: a long page's useful part is rarely all at its top."""
        chunks = split_text(text, room)
        if len(chunks) <= CHUNKS_PER_PAGE:
            return chunks
        matched = [index for index in Retriever(None).by_words(wanted, chunks) if index]
        chosen = [0] + (matched + [index for index in range(1, len(chunks)) if index not in matched])[:CHUNKS_PER_PAGE - 1]
        return [chunks[index] for index in sorted(chosen)]

    def facts_in(self, parts, page, number, part):
        """The facts the model reads in one chunk of a page; None when the page cannot be read on (a chunk too
        large), or the model stopped answering (it crashed or failed again and again: no page is read on)."""
        try:
            return self.model.ask_until_usable(Prompts.FACTS_SYSTEM, parts, f"Facts, page {number} part {part}",
                                               self.read_facts, attempts=2) or []
        except PromptTooLarge as exc:
            self.notes.append(f"page {page['url']}: {first_line(str(exc), 160)}")
        except OllamaError as exc:
            self.model_failure = first_line(str(exc), 200)
            self.notes.append(f"the model stopped answering at page {number}; the pages after it were not read")
            say.warn(f"{self.notes[-1]} ({self.model_failure})")
        return None

    @staticmethod
    def read_facts(reply):
        facts = []
        for item in reply.json().get("facts", []):
            if not isinstance(item, dict):
                continue
            text, phase = str(item.get("text", "")).strip()[:MAX_FACT_CHARS], str(item.get("phase", "")).strip().lower()
            if text and phase in FACT_PHASES:
                facts.append({"phase": phase, "text": text})
        return facts[:FACTS_PER_CHUNK]

    @staticmethod
    def add_facts(facts, found):
        """The found facts added to facts: a fact stated again is kept once, and when another page
        states it, that page joins its sources ("also")."""
        for fact in found:
            same = next((known for known in facts if similarity(fact["text"], known["text"]) >= SAME_FACT_SIMILARITY), None)
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
            return f"the web was lost ({self.internet.problem()})"
        if self.model_failure:
            return f"the model stopped answering ({self.model_failure})"
        return ""

    def condense(self, plan, facts, think=None):
        """A topic's facts (a language's with the lessons its projects proved) condensed into its playbook (one
        call): at most PLAYBOOK_RULES rules a code generator follows, each citing the facts and lessons it rests
        on, so its prompts show a few rules in place of many facts. [] when the model gives no usable reply (the
        facts are then shown as they are); a model that fails raises, so nothing is kept. think: the call's
        think setting (None: the model's own)."""
        lessons = Knowledge(plan["language"]).proven_lessons() if plan.get("kind") == "language" else []
        sources = self.rule_sources(facts, lessons)
        self.step("condense", f"Turning {len(facts)} fact(s) into rules a code writer can follow…", "✍️")
        with say.working_on(f"Turning {len(facts)} fact(s) about {plan['name']} into rules"):
            rules = self.model.ask_until_usable(Prompts.PLAYBOOK_SYSTEM, self.playbook_parts(plan, facts, lessons),
                                                "Condense the facts",
                                                lambda reply: self.read_rules(reply.json(), sources), attempts=2,
                                                think=think)
        self.say_rules(rules, facts)
        return rules or []

    def say_rules(self, rules, facts):
        """What condensing gave: the rules and the first of them, or that the facts stay as they are."""
        if not rules:
            self.notes.append("the facts were not condensed into rules (no usable reply)")
        with say.section():
            if rules:
                say.done(f"{len(rules)} rule(s)")
                say.found(first_line(rules[0]["text"], 200))
            else:
                say.warn("Could not make rules: the facts are kept as they are")
        self.outcome(f"{len(rules or [])} rule(s) from {len(facts)} fact(s)")

    @staticmethod
    def rule_sources(facts, lessons):
        """What a rule may cite, numbered as the prompt numbers it: the facts, then the lessons."""
        return [{"id": Library.claim_id(fact, fact["text"]), "text": fact["text"]} for fact in facts] + \
               [{"id": lesson.get("id") or fingerprint(lesson["text"])[:12], "text": lesson["text"]} for lesson in lessons]

    @staticmethod
    def playbook_parts(plan, facts, lessons):
        """The topic and its tools, its facts (with their slot and phase), then its lessons, numbered on."""
        listing = "\n".join(f"{number}. [{fact.get('slot', '')}, {fact['phase']}] {fact['text']}"
                            for number, fact in enumerate(facts, 1))
        proved = "\n".join(f"{number}. {lesson['text']}" for number, lesson in enumerate(lessons, len(facts) + 1))
        tools = Library.tools_text(plan.get("tools", [])) or "none named"
        return [Part("Topic", f"{plan['name']} ({plan.get('kind', 'tool')}); its tools: {tools}", True),
                Part("Facts learned from the web", listing, True, exact=True),
                Part("Lessons its projects proved", proved or "(none yet)", True, exact=True)]

    @staticmethod
    def read_rules(data, sources):
        """The rules, checked: each with a known phase, its text (cut to MAX_RULE_CHARS), when it applies ("" for
        every program) and the ids of the sources it cites by number; a rule that rests on none is dropped
        (never the model's own knowledge). What a new project declares (to get the newest version) is the first
        rule, for every program. At most PLAYBOOK_RULES, one per text."""
        rules = {}
        given = (data.get("rules") or []) if isinstance(data, dict) else []
        declare = data.get("declare") if isinstance(data, dict) else None
        if isinstance(declare, dict):
            given = [dict(declare, phase="config", when="")] + list(given)
        for item in given:
            if not isinstance(item, dict):
                continue
            text, phase = " ".join(str(item.get("text") or "").split())[:MAX_RULE_CHARS], str(item.get("phase") or "").lower()
            cites = list(dict.fromkeys(sources[number - 1]["id"] for number in item.get("cites") or []
                                       if isinstance(number, int) and 1 <= number <= len(sources)))
            when = " ".join(str(item.get("when") or "").split())[:MAX_RULE_WHEN_CHARS]
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
            say.warn(f"Not indexed now ({self.embeddings.failed}); a run indexes it when it searches")
        else:
            say.info(f"Indexed with {self.embeddings.name}, so a run can find it by meaning", "🔍")


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
    CONFIDENCE_LEVELS = ("low", "medium", "high")

    def __init__(self, learner):
        self.learner, self.model = learner, learner.model

    def research(self, topic):
        """The topic researched and saved (its entry), or None when nothing was found. A topic
        researched before is kept as it was when the web or the model is lost on the way."""
        self.learner.start()
        with say.part(f"Researching {topic}", "🔬"):
            return self.researched(topic, Library.slug(topic))

    def researched(self, topic, slug):
        learner = self.learner
        known = Library.read(slug)
        frame = self.frame(topic, known if (known or {}).get("kind") == "research" else None)
        facts, sources, analysis = self.investigate(frame)
        if known and (learner.cut_short() or not facts):
            say.warn(f"{learner.cut_short() or 'No page gave facts'}; kept what was found before "
                     f"({known.get('learned', '?')})")
            learner.kept_earlier_topic = True
            return known
        if not facts:
            say.fail(f"Nothing found about {topic}: " + ("; ".join(learner.notes) or "the pages stated nothing useful"))
            return None
        entry = self.entry(topic, frame, facts, sources, analysis)
        learner.step("keep")
        Library.save(slug, entry)
        say.info(f"Saved {len(entry['findings'])} finding(s) from {len(facts)} fact(s) on {len(sources)} page(s) "
                 f"as {slug}", "💾")
        learner.index(slug, entry)
        return entry

    def frame(self, topic, known):
        """The topic's name, summary and research questions (each with its searches): as saved for
        it, else from the model."""
        self.learner.step("frame")
        parts = [Part("Topic", topic, True), Part("Today", today().isoformat(), True)]
        frame, saved = self.learner.saved_or_asked(known, self.read_frame, Prompts.RESEARCH_PLAN_SYSTEM, parts,
                                                   "Frame the research", f"researching {topic}",
                                                   Library.slug(topic))
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
        """A research frame, checked: a name, a summary and up to RESEARCH_QUESTIONS questions, each
        with up to SEARCHES_PER_RESEARCH_QUESTION searches."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        name, summary = str(data.get("name") or "").strip()[:80], " ".join(str(data.get("summary") or "").split())[:200]
        questions = []
        for item in data.get("questions") or []:
            if not isinstance(item, dict):
                continue
            question = " ".join(str(item.get("question") or "").split())[:200]
            queries = [" ".join(str(query).split()) for query in item.get("queries") or []
                       if str(query).strip()][:SEARCHES_PER_RESEARCH_QUESTION]
            if question and queries:
                questions.append({"question": question, "queries": queries})
        if not name or not questions:
            raise ValueError("it needs a name and at least one question with its searches")
        return {"name": name, "summary": summary, "questions": questions[:RESEARCH_QUESTIONS]}

    def investigate(self, frame):
        """(facts, sources, analysis) after each round of RESEARCH_PAGES_PER_ROUND: the first round searches
        every question, each next one the gaps and checks the last analysis named. It ends early
        when none is left, or the web or the model is lost."""
        searches = self.first_searches(frame)
        facts, sources, analysis = [], [], self.EMPTY_ANALYSIS
        for round_number, pages in enumerate(RESEARCH_PAGES_PER_ROUND, 1):
            if not searches:
                break
            self.learner.step("investigate", f"Round {round_number}: {len(searches)} search(es), reading up to "
                                             f"{pages} page(s)…", "🔎")
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
        """The first round's searches: each query of each question, the question as what to look for."""
        return [{"query": query, "section": str(number), "pages": RESULTS_PER_RESEARCH_SEARCH, "focus": question["question"]}
                for number, question in enumerate(frame["questions"], 1) for query in question["queries"]]

    def say_round(self, facts, sources, found):
        """How far the research got after a round: pages read, facts found, the first of them."""
        say.tally(f"{len(sources)} page(s) read · {len(facts)} fact(s)")
        if found:
            say.found(first_line(found[0]["text"], 200))
        self.learner.outcome(f"{len(facts)} fact(s) from {len(sources)} page(s)")

    def cross_check(self, facts):
        """Each fact's sites, with no model call. A fact that repeats another (REPEAT_CLAIM_SIMILARITY, numbers
        agreeing) is folded into it. A fact making the same claim as one on another site
        (SAME_CLAIM_SIMILARITY) shares its sites when their numbers agree; when they do not, the pair is
        returned, for the analysis to judge (facts about other things, versions or dates differ
        without contradicting). Without embeddings only the pages that repeat a fact count."""
        vectors = self.learner.embeddings.vectors([fact["text"] for fact in facts], keep=False) \
            if self.learner.embeddings else None
        if vectors is None:
            say.warn("Cross-checking by repeats only: the embedding model gave no embeddings")
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
        support, differ = [set(site) for site in sites], []
        for first, second in ((first, second) for first in range(len(facts)) for second in range(first + 1, len(facts))):
            if not self.same_claim(vectors[first], vectors[second]) or sites[first] & sites[second]:
                continue
            if self.agree(facts[first], facts[second]):
                support[first] |= sites[second]
                support[second] |= sites[first]
            else:
                differ.append((first + 1, second + 1))
        return CrossCheck(support, differ)

    def fold_repeats(self, facts, vectors):
        """Each fact that repeats an earlier one, folded into it (in place, with its vector): its
        pages join the earlier one's, and the more detailed text is kept."""
        index = 0
        while index < len(facts):
            kept = next((earlier for earlier in range(index)
                         if self.same_claim(vectors[earlier], vectors[index], REPEAT_CLAIM_SIMILARITY)
                         and self.agree(facts[earlier], facts[index])), None)
            if kept is None:
                index += 1
                continue
            vectors.pop(index)
            self.fold(facts[kept], facts.pop(index))

    def fold(self, original, repeat):
        """A repeat's pages join the original's, and the more detailed text is kept."""
        if self.more_detailed(repeat, original):
            original["text"] = repeat["text"]
        for url in Learner.pages_of(repeat):
            if url not in Learner.pages_of(original):
                original["also"] = original.get("also", []) + [url]

    def more_detailed(self, fact, other):
        """Whether a fact says more than another: more numbers, else a longer text."""
        return (len(self.numbers(fact)), len(fact["text"])) > (len(self.numbers(other)), len(other["text"]))

    @staticmethod
    def same_claim(a, b, threshold=SAME_CLAIM_SIMILARITY):
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
        parts = self.analysis_parts(frame, facts, differ)
        try:
            return self.model.ask_until_usable(Prompts.RESEARCH_ANALYSIS_SYSTEM, parts, "Analyse the findings",
                                               lambda reply: self.read_analysis(reply.json(), len(facts),
                                                                                len(frame["questions"])), attempts=2)
        except (PromptTooLarge, OllamaError) as exc:
            self.learner.notes.append(f"the findings could not be analysed ({first_line(str(exc), 160)})")
            say.warn(self.learner.notes[-1])
            return None

    def analysis_parts(self, frame, facts, differ):
        """The analysis prompt: the topic, its questions, the facts that may disagree, then as many facts as fit."""
        questions = "\n".join(f"Q{number}. {question['question']}"
                              for number, question in enumerate(frame["questions"], 1))
        pairs = "\n".join(f"F{first} / F{second}" for first, second in differ) or "(none)"
        fixed = [Part("Topic", frame["name"], True), Part("Questions", questions, True),
                 Part("Facts that may disagree", pairs, True)]
        room = self.model.limit_chars(Prompts.RESEARCH_ANALYSIS_SYSTEM) - Part.total_size(fixed) - 200
        return fixed + [Part("Facts", self.fact_listing(facts, room), True, exact=True)]

    @staticmethod
    def fact_listing(facts, room):
        """One line per fact, "F<n> [Q<question>, <k> site(s)] text", in their own order: as many
        as fit in room chars, the facts most sites state kept first."""
        lines = {number: f"F{number} [Q{fact.get('section', '?')}, {len(fact.get('sites', []))} site(s)] {fact['text']}"
                 for number, fact in enumerate(facts, 1)}
        kept, used = set(), 0
        for number in sorted(lines, key=lambda number: -len(facts[number - 1].get("sites", []))):
            if used + len(lines[number]) + 1 <= room:
                kept.add(number)
                used += len(lines[number]) + 1
        return "\n".join(lines[number] for number in sorted(kept))

    @staticmethod
    def read_analysis(data, fact_count, question_count):
        """The analysis, checked: findings for known questions citing known facts, contradictions
        between known facts (with the doubtful ones among them), up to RESEARCH_GAPS gaps and
        RESEARCH_FACTS_CHECKED checks, with a search each."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        cited = lambda numbers: sorted({number for number in numbers or [] if isinstance(number, int)
                                        and 1 <= number <= fact_count})
        asked = lambda item: isinstance(item, dict) and isinstance(item.get("question"), int) \
            and 1 <= item["question"] <= question_count
        findings = Researcher.read_findings(data.get("findings") or [], cited, asked)
        if not findings:
            raise ValueError("it needs a finding for at least one question")
        return {"findings": findings, "contradictions": Researcher.read_contradictions(data.get("contradictions") or [],
                                                                                       cited),
                "gaps": Researcher.read_gaps(data.get("gaps") or [], asked)[:RESEARCH_GAPS],
                "checks": Researcher.read_checks(data.get("checks") or [], fact_count)[:RESEARCH_FACTS_CHECKED]}

    @staticmethod
    def read_findings(given, cited, asked):
        return [{"question": finding["question"], "answer": " ".join(str(finding.get("answer") or "").split())[:600],
                 "confidence": finding.get("confidence") if finding.get("confidence") in ("high", "medium", "low")
                 else "low",
                 "facts": cited(finding.get("facts"))}
                for finding in given if asked(finding) and str(finding.get("answer") or "").strip()]

    @staticmethod
    def read_contradictions(given, cited):
        """Contradictions between two or more known facts, with the doubtful ones among them (unsure: all are)."""
        contradictions = []
        for item in given:
            if isinstance(item, dict) and len(cited(item.get("facts"))) >= 2:
                facts = cited(item["facts"])
                doubtful = [number for number in cited(item.get("doubtful")) if number in facts] or facts
                contradictions.append({"facts": facts, "doubtful": doubtful,
                                       "note": " ".join(str(item.get("note") or "").split())[:300]})
        return contradictions

    @staticmethod
    def read_gaps(given, asked):
        return [{"question": gap["question"], "missing": " ".join(str(gap.get("missing") or "").split())[:200],
                 "search": " ".join(str(gap.get("search") or "").split())[:160]}
                for gap in given if asked(gap) and str(gap.get("search") or "").strip()]

    @staticmethod
    def read_checks(given, fact_count):
        return [{"fact": check["fact"], "search": " ".join(str(check.get("search") or "").split())[:160]}
                for check in given if isinstance(check, dict) and isinstance(check.get("fact"), int)
                and 1 <= check["fact"] <= fact_count and str(check.get("search") or "").strip()]

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
        kept = [self.kept_fact(fact, number in disputed) for number, fact in enumerate(facts, 1)]
        cited = lambda finding: [kept[number - 1] for number in finding["facts"]]
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
        return {**{key: value for key, value in fact.items() if key != "section"}, "question": int(fact["section"]),
                "sites": sites, "confidence": confidence}

    @classmethod
    def supported(cls, stated, cited):
        """How sure a finding may be, whatever the model said: high only when at least half the facts
        it cites are corroborated and none is disputed; medium when it cites any; else low."""
        corroborated = sum(fact["confidence"] == "corroborated" for fact in cited)
        disputed = any(fact["confidence"] == "disputed" for fact in cited)
        ceiling = "high" if cited and not disputed and 2 * corroborated >= len(cited) else "medium" if cited else "low"
        return min(stated, ceiling, key=cls.CONFIDENCE_LEVELS.index)


class Library:
    """What Tinker learned about topics and languages, one entry each in Tinker's "learned", from tinker
    learn WHAT or a run's recall phase (a language under its language_key). Each fact and finding has an
    id and a status: a candidate until a cycle that used it succeeds. A run shows the facts of the
    topics its recall phase chose (ranked in the project's "learned") in the prompts of the phases they
    concern, as an optional part. A language's rules (its playbook) are claims too, shown in place of the
    facts they cite."""
    CLAIM_KINDS = ("facts", "findings", "rules")

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
        for claim, text in [(item, item.get("text", "")) for item in entry.get("facts", []) + entry.get("rules", [])] + \
                           [(item, item.get("answer", "")) for item in entry.get("findings", [])]:
            claim.setdefault("id", fingerprint(str(text))[:12])
            claim.setdefault("status", "candidate")
        facts = entry.get("facts", [])
        for finding in entry.get("findings", []):
            finding.setdefault("cites", [facts[number - 1]["id"] for number in finding.get("facts", [])
                                         if isinstance(number, int) and 1 <= number <= len(facts)])
        Home.store().put("learned", slug, entry)

    @staticmethod
    def claims(slug, ids, change):
        """The claims of a topic with these ids, changed by change(claim) -> claim or None (gone)."""
        def changed(entry):
            if not entry:
                return entry
            for key in Library.CLAIM_KINDS:
                entry[key] = [kept for kept in (change(claim) if claim.get("id") in ids else claim
                                                for claim in entry.get(key, []))
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
                                for key in Library.CLAIM_KINDS))
            return entry
        if Library.read(slug):
            Home.store().update("learned", slug, retired_in)
        return sum(gone)

    @staticmethod
    def taught_fact(slug, about, text, sources):
        """A fact the person taught in place of a claim of a topic (about: its id, retired or not), kept as proven with
        its sources, where that claim was (its phase, chapter and section); None when the topic has no such claim."""
        entry = Library.read(slug) or {}
        old = next((claim for key in Library.CLAIM_KINDS + ("retired",) for claim in entry.get(key, [])
                    if claim.get("id") == about), None)
        if old is None:
            return None
        fact = {"text": text[:MAX_FACT_CHARS], "phase": old.get("phase", "book"), "status": "proven",
                "sources": list(dict.fromkeys(sources)), "id": fingerprint(text)[:12],
                **{key: old[key] for key in ("chapter", "slot", "section") if key in old}}
        record(fact, "taught", None)
        Home.store().update("learned", slug, lambda kept: dict(kept, facts=kept.get("facts", []) + [fact]))
        return fact

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
                say.warn(f"What was learned about {slug} is not readable ({type(exc).__name__}: {exc}); left out")
        return found

    @staticmethod
    def topic_items(slug, entry):
        name = str(entry.get("name") or entry["topic"])
        plain = lambda phase, text: {"topic": slug, "name": name, "phase": phase, "text": text, "id": None,
                                     "status": None}
        items = [plain("summary", str(entry.get("summary") or name))]
        if entry.get("tools"):
            items.append(plain("tools", f"its tools: {Library.tools_text(entry['tools'])}"))
        installed = [(tool["output"] if tool["found"] else f"`{tool['command']}` fails (not installed)")
                     for tool in entry.get("machine", [])]
        if installed:
            items.append(plain("machine", f"on this machine: {'; '.join(installed)}"))
        items += [{"topic": slug, "name": name, "phase": "findings",
                   "text": f"{entry['questions'][finding['question'] - 1]['question']} {finding['answer']} "
                           f"({finding['confidence']} confidence)", "id": Library.claim_id(finding, finding["answer"]),
                   "status": finding.get("status", "candidate")} for finding in entry.get("findings", [])]
        items += [{"topic": slug, "name": name, "phase": str(rule["phase"]), "text": str(rule["text"]), "kind": "rule",
                   "when": str(rule.get("when") or ""), "id": Library.claim_id(rule, rule["text"]),
                   "status": rule.get("status", "candidate")} for rule in entry.get("rules", [])]
        return items + [{"topic": slug, "name": name, "phase": str(fact["phase"]),
                         "text": str(fact["text"]) + (f" ({fact['confidence']})" if fact.get("confidence") else ""),
                         "id": Library.claim_id(fact, fact["text"]), "status": fact.get("status", "candidate")}
                        for fact in Library.facts_shown(entry)]

    @staticmethod
    def facts_shown(entry):
        """A topic's facts as its prompts may show them: all of them, unless its rules (a playbook) stand in for
        them; then only those a retired rule cited and no rule in use cites (what the retired rule said is
        open again)."""
        rules = entry.get("rules", [])
        if not rules:
            return entry["facts"]
        cited = lambda given: {*rule.get("cites", []) for rule in given}
        reopened = cited(retired for retired in entry.get("retired", []) if retired.get("was") == "rules") - cited(rules)
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
    RRF_K = 60
    BM25_K1, BM25_B = 1.5, 0.75

    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.used = "words"
        self.query_embeddings = {}

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
        return [index for _, index in sorted((scored for scored in scores if scored[0] > 0),
                                             key=lambda pair: (-pair[0], pair[1]))]

    def word_scores(self, query, texts):
        """Each text's BM25 score for the query's words, in order (0 for a text that holds none)."""
        if not texts:
            return []
        terms, docs = set(words(query)), [words(text) for text in texts]
        average = sum(len(doc) for doc in docs) / len(docs) or 1
        holding = collections.Counter(*(set(doc) & terms) for doc in docs)
        return [self.relevance(terms, doc, len(docs), holding, average) for doc in docs]

    def relevance(self, terms, doc, total, holding, average):
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
        if vectors and query not in self.query_embeddings:
            asked = self.embeddings.vectors([query], keep=False)
            self.query_embeddings[query] = asked[0] if asked else None
        if not vectors or not self.query_embeddings.get(query):
            say.warn(f"Meaning search not used: {self.embeddings.failed}; the word search stands alone")
            self.embeddings = None
            return []
        self.used = f"words and meaning ({self.embeddings.name})"
        similarity_of = [Embeddings.cosine(self.query_embeddings[query], vector) for vector in vectors]
        return sorted(range(len(texts)), key=lambda index: (-similarity_of[index], index))


class Embeddings:
    """Embeddings from the embedding model selected with tinker embed NAME (none: the word search
    works alone), kept in Tinker's "embeddings" by text, per model, so each text is embedded once.
    Local: nothing goes online."""
    TEXTS_PER_REQUEST = 32

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
        for start in range(0, len(missing), self.TEXTS_PER_REQUEST):
            batch = missing[start:start + self.TEXTS_PER_REQUEST]
            found = self.embedded(batch)
            if found is None:
                return None
            fresh.update(self.keyed(batch, found))
        result = [stored.get(key) or fresh[key] for key in keys]
        if keep and (fresh or prune):
            self.kept(fresh, keys if prune else None)
        return result

    def keyed(self, texts, vectors):
        """Each text's vector under its cache key, rounded to 5 places (it keeps the cache small)."""
        return {self.key(text): [round(number, 5) for number in vector] for text, vector in zip(texts, vectors)}

    def embedded(self, batch):
        """The batch's embeddings, as the model gives them; None when it gives none (failed says why)."""
        data = Ollama.get_json("/api/embed", {"model": self.name, "input": batch, "keep_alive": 0}, timeout=300,
                               errors=True)
        found = data.get("embeddings") if isinstance(data, dict) else None
        if self.one_vector_each(found, batch):
            return found
        self.failed = f"{self.name} gave no embeddings" + (f" ({data['error']})" if isinstance(data, dict)
                                                           and data.get("error") else "")
        return None

    def one_vector_each(self, found, batch):
        """Whether the model gave one vector for each text of the batch."""
        return isinstance(found, list) and len(found) == len(batch) and all(self.is_vector(vector) for vector in found)

    @staticmethod
    def is_vector(value):
        return isinstance(value, list) and value and all(isinstance(number, (int, float)) for number in value)

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
    """An event in an item's history (born, confirmed, helped, failed), the last EVENTS_KEPT_PER_ITEM; helped is
    also counted, beyond them. The item, changed."""
    item["history"] = (item.get("history", []) + [{"event": event, "run": run, "at": now_text()}])[-EVENTS_KEPT_PER_ITEM:]
    if event == "helped":
        item["helped"] = item.get("helped", 0) + 1
    return item


def retire(doc, key, gone, reason, run):
    """The items of doc[key] that gone(item) picks, moved to doc["retired"] (the last RETIRED_KEPT_PER_LIST) with why
    and the run: never shown again, kept for people to see. How many."""
    items = doc.get(key, [])
    leaving = [item for item in items if gone(item)]
    doc[key] = [item for item in items if not gone(item)]
    retired = [record(dict(item, status="retired", reason=reason, retired=now_text(), was=key), "failed", run)
               for item in leaving]
    if retired:
        doc["retired"] = (doc.get("retired", []) + retired)[-RETIRED_KEPT_PER_LIST:]
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
