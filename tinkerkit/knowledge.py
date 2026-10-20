"""What Tinker knows beyond the model: lessons, toolchain fixes and proven facts from earlier projects
(Knowledge), and what it learns from the web (Learner, Researcher), kept and searched (Library,
Retriever, Embeddings). What it learns starts as a candidate: a cycle that succeeds using it proves it,
and a failure in the step it was meant to help discards it."""

import collections
import datetime
import hashlib
import math
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .model import Models, Ollama, OllamaError
from .prompts import Part, PromptTooLarge, Prompts
from .settings import (
    Config, FACTS_PER_CHUNK, FACT_CHARS, FACT_PHASES, Home, LANGUAGE_COMMANDS, LANGUAGE_PAGES, LANGUAGE_SEARCHES,
    LANGUAGE_SECTIONS, LESSONS_SHOWN, LESSON_PHASES, MAX_COMMANDS, MAX_FACTS, MAX_LESSONS, MAX_PAGES, MAX_QUERIES,
    PAGE_CHUNKS,
    REPEAT_CLAIM, RESEARCH_CHECKS, RESEARCH_GAPS, RESEARCH_PAGES, RESEARCH_QUERIES, RESEARCH_QUESTIONS, SAME_CLAIM,
    SAME_FACT, SAME_LESSON, SEARCH_RESULTS, SECTION_FACTS, SECTION_PAGES, SIMILAR_FAILURE, TOOL_ROLES,
    VERSION_TIMEOUT, fingerprint, first_line, now_text, similarity, split_text, words,
)
from .workspace import Toolchain, Workspace


def language_key(name):
    """A language's one identity: its first word, without a version. "Python 3.12" -> python,
    "JavaScript (Node.js)" -> javascript, "C++" -> cpp, "C#" -> csharp."""
    base = (name or "").split("(")[0].lower().replace("+", "p").replace("#", "sharp")
    found = re.findall(r"[a-z][a-z0-9]*", base)
    return found[0] if found else "unknown"


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
            data = Ollama.get_json("/api/embed", {"model": self.name, "input": batch, "keep_alive": 0}, timeout=300,
                                   errors=True)
            found = data.get("embeddings") if isinstance(data, dict) else None
            if not (isinstance(found, list) and len(found) == len(batch)
                    and all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in found)):
                self.failed = f"{self.name} gave no embeddings" + (f" ({data['error']})" if isinstance(data, dict)
                                                                   and data.get("error") else "")
                return None
            fresh.update({self.key(text): [round(x, 5) for x in vector] for text, vector in zip(batch, found)})
        result = [stored.get(key) or fresh[key] for key in keys]
        if keep and (fresh or prune):
            stored.update(fresh)
            if prune:
                self.cache["vectors"] = {key: stored[key] for key in keys}
            kept = self.cache["vectors"]
            Home.store().update("embeddings", self.name, lambda saved: {**({} if prune else saved or {}), **kept})
        return result

    @staticmethod
    def key(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / size if size else 0.0


class Learner:
    """Learns from the web what a model may not know, or knows as it was when it was trained, and
    saves it (Library), indexed with the embedding model (Embeddings). The plan tells which kind
    (learn); a domain is researched (Researcher). The other two:
    a tool (a framework, a library, a technique such as TDD): the model plans a few searches, at
    least one for each thing the topic names, aimed at how to use it today;
    a language (or a language a run's recall phase needs): the same
    sections for every language, each with its own searches, what to look for and share of the
    facts: its fundamentals, what is new, its tools (one per role: environment, packages, build,
    tests, format, lint: the ones the request names, else the usual ones) and its conventions.
    Then, for both: the commands that show what is installed here run (only such commands: one
    each, no shell operators, nothing installed or changed); the searches run and their pages are
    taken in turn (each search's first result, then each one's second, ...), so every search is
    read; the model condenses each page into the facts it states, each kept with its source;
    repeats are dropped and, past a section's share, the model keeps the most useful. Learning
    again reuses the saved plan (edit it there). It needs the Internet plugin and an embedding
    model."""
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

    def learn(self, topic, deep=False):
        """What was learned and saved (its entry), or None when nothing could be. The plan tells what
        it is: a programming language is learned as one (learn_language); a domain or practice is
        researched when deep (tinker learn: Researcher), else learned quickly like a tool, the way a run
        learns what it needs; a tool, library or technique is learned from a few pages. A topic
        learned before is learned again the same way."""
        self.start()
        known = Library.read(Library.slug(topic))
        if (known or {}).get("kind") == "research":
            return Researcher(self).research(topic) if deep else known
        if (known or {}).get("kind") == "language":
            return self.learn_language(known.get("topic") or topic)
        print(f"\nLearning about {topic}")
        plan = self.plan(topic, known)
        if plan["domain"] and deep:
            print("  it is a domain: researching it, its sources cross-checked")
            return Researcher(self).research(topic)
        if plan["language"]:
            slug = Library.language(plan["language"])
            if slug:  # learned as a language already (with the tools asked for then): not learned again unasked
                print(f"  it is the programming language {plan['language']}, learned already (as {slug})")
                return Library.read(slug)
            print(f"  it is the programming language {plan['language']}: learning it as a language")
            return self.learn_language(topic)
        focus = Prompts.TOPIC_FOCUS.format(topic=plan["name"])
        searches = [{"query": query, "section": "", "pages": SEARCH_RESULTS, "focus": focus} for query in plan["queries"]]
        return self.finish(Library.slug(topic), topic, plan, searches, MAX_PAGES, {"": MAX_FACTS})

    def start(self):
        self.notes, self.model_failed, self.kept_earlier = [], "", False

    def plan(self, topic, known):
        """What the topic is, the searches and the commands: as saved for it, else from the model."""
        parts = [Part("Topic", topic, True), Part("Today", datetime.date.today().isoformat(), True)]
        plan, saved = self.saved_or_asked(known, self.read_plan, Prompts.LEARN_PLAN_SYSTEM, parts, "Plan learning",
                                          f"learning about {topic}", Library.slug(topic))
        if not saved and not plan["language"]:
            print(f"  {plan['name']}: {plan['summary']}")
        self.show_dropped(plan)
        return plan

    def saved_or_asked(self, known, read, system, parts, label, what, saved_as):
        """(plan, whether it was saved): the plan saved before (known, as read reads it), else the
        model's; the command stops when the model gives no usable one (what: what it was for)."""
        if known:
            try:
                plan = read(known)
                print(f"  the plan kept for it ({saved_as})")
                return plan, True
            except ValueError as exc:
                print(f"  its saved plan is not usable ({exc}); planning it again")
        plan = self.model.ask_until_usable(system, parts, label, lambda reply: read(reply.json()), attempts=2)
        if plan is None:
            raise SystemExit(f"{self.model.name} gave no usable plan for {what}; see the log of its replies "
                             f"(tinker memory; its calls are in tinker learn's log)")
        return plan, False

    def learn_language(self, text):
        """A programming language learned as it is today (see the class), saved as
        Tinker's "learned" under its language key; None when nothing could be learned."""
        self.start()
        print(f"\nLearning the language {text}")
        plan = self.plan_language(text)
        searches = [{"query": search["query"], "section": search["section"], "pages": SECTION_PAGES[search["section"]],
                     "focus": Prompts.LANGUAGE_FOCUS[search["section"]].format(language=plan["language"])}
                    for search in plan["searches"]]
        searches += [{"query": tool["search"], "section": "tools", "pages": SECTION_PAGES["tools"],
                      "focus": Prompts.TOOL_FOCUS.format(tool=tool["name"], role=tool["role"], language=plan["language"])}
                     for tool in plan["tools"]]
        return self.finish(language_key(plan["language"]), text, plan, searches, LANGUAGE_PAGES, SECTION_FACTS)

    def finish(self, slug, topic, plan, searches, max_pages, shares):
        """What is installed here and what the pages state, saved as slug (a topic learned before
        is kept when reading was cut short or found nothing); the entry, or None."""
        machine = self.look_at_machine(plan["commands"])
        facts, sources = self.read_web(plan["name"], searches, max_pages)
        known = Library.read(slug)
        cut_short = self.cut_short()
        if known and (cut_short or not facts):
            print(f"  {cut_short or 'no page gave facts'}; kept what was learned before ({known.get('learned', '?')})")
            self.kept_earlier = True
            return known
        if not facts and not any(m["found"] for m in machine):
            print(f"  nothing learned about {topic}: " + ("; ".join(self.notes) or "the pages stated nothing useful"))
            return None
        facts = self.keep_shares(plan["name"], facts, shares)
        entry = {"topic": topic, **{k: v for k, v in plan.items() if k != "dropped"}, "learned": now_text(),
                 "model": self.model.name, "machine": machine, "facts": facts, "sources": sources, "notes": self.notes}
        Library.save(slug, entry)
        print(f"  learned {len(facts)} fact(s) about {plan['name']} from {len(sources)} page(s); kept as {slug}")
        self.index(slug, entry)
        return entry

    @staticmethod
    def read_plan(data):
        """A topic's plan, checked: a name, a summary, up to MAX_QUERIES searches, up to
        MAX_COMMANDS commands that only show what is installed (any other is "dropped"),
        "language": the language's name when the topic is one (else ""), and "domain": whether it
        is a field or practice to research rather than software to learn to use."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        name, summary = str(data.get("name") or "").strip()[:80], " ".join(str(data.get("summary") or "").split())[:200]
        language = str(data.get("language") or "").strip()[:40]
        domain = str(data.get("kind") or "").strip().lower() == "domain" and not language
        queries = [" ".join(str(q).split()) for q in data.get("queries") or [] if str(q).strip()][:MAX_QUERIES]
        if not name or (not queries and not language):
            raise ValueError("it needs a name and at least one search")
        commands, dropped = Learner.safe_commands(data.get("commands") or [])
        return {"name": name, "summary": summary, "language": language, "domain": domain, "queries": queries,
                "commands": commands[:MAX_COMMANDS], "dropped": dropped}

    def show_dropped(self, plan):
        for command in plan["dropped"]:
            print(f"  left out `{command}`: a command here must only show what is installed (one command, no shell "
                  f"operators, nothing installed or changed)")

    def plan_language(self, text):
        """The language's name and version, its tools, the searches of each section and the
        commands: as saved for this request, else from the model."""
        known = next((entry for entry in Library.topics().values()
                      if entry.get("kind") == "language" and entry.get("topic") == text), None)
        parts = [Part("Request", text, True), Part("Today", datetime.date.today().isoformat(), True)]
        plan, saved = self.saved_or_asked(known, self.read_language_plan, Prompts.LANGUAGE_PLAN_SYSTEM, parts,
                                          "Plan learning the language", f"learning {text}",
                                          language_key((known or {}).get("language") or text))
        if plan.get("not_language"):
            raise SystemExit(f"{text!r} is not a programming language ({plan['not_language']}). Nothing was read.")
        if not saved:
            print(f"  {plan['name']}: {plan['summary']}")
        print(f"  tools: {Library.tools_text(plan['tools']) or 'none named'}")
        self.show_dropped(plan)
        return plan

    def look_at_machine(self, commands):
        """What each command shows (run in an empty folder): its first line and version number; a
        command that fails means that tool is not installed."""
        found = []
        with tempfile.TemporaryDirectory(prefix="tinker-learn-") as folder:
            ws = Workspace(Path(folder))
            for command in commands:
                code, output = ws.run(command, timeout=VERSION_TIMEOUT, quiet=True)
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                version = self.VERSION_RE.search(" ".join(lines[:5])) if code == 0 else None
                found.append({"command": command, "found": code == 0, "version": version.group(0) if version else "",
                              "output": (lines[0] if lines else "")[:160]})
                failed = "did not finish" if code is None else f"exit code {code}"
                print(f"  $ {command}: " + (found[-1]["output"][:100] if code == 0 else f"not installed ({failed})"))
        return found

    def read_web(self, name, searches, max_pages):
        """(facts, sources): the searches run, their pages taken in turn (each search's first
        result, then each one's second, ...: every search is read) up to max_pages, each read for
        what its search looks for; a fact of a section is kept with it, a fact stated again kept
        once (add_facts)."""
        results = []
        for search in searches:
            found = self.internet.search(search["query"], SEARCH_RESULTS)
            if found is None:
                self.notes.append(f"search '{search['query']}': {self.internet.why(search['query'])}")
                print(f"  {self.notes[-1]}")
            results.append((found or [])[:search["pages"]])
        pages, seen = [], set()
        for rank in range(SEARCH_RESULTS):
            for search, found in zip(searches, results):
                if rank < len(found) and found[rank]["url"] not in seen and len(pages) < max_pages:
                    seen.add(found[rank]["url"])
                    pages.append(dict(found[rank], section=search["section"], focus=search["focus"]))
        facts, sources = [], []
        for number, page in enumerate(pages, 1):
            if self.model_failed:
                break
            found = self.read_page(name, page, f"{number}/{len(pages)}")
            sources += [page["url"]] if found else []
            found = [dict(f, section=page["section"]) if page["section"] else f for f in found]
            self.add_facts(facts, found)
        return facts, sources

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

    def keep_shares(self, name, facts, shares):
        """Each section's facts, at most its share (shares: {section: count}; a topic's facts have
        no section): past it, the most useful as the model picks them."""
        kept = []
        for section, share in shares.items():
            facts_of = [f for f in facts if f.get("section", "") == section]
            kept += self.pick(f"{name}: {section}" if section else name, facts_of, share) if len(facts_of) > share \
                else facts_of
        return kept

    def index(self, slug, entry):
        """The texts embedded now, so the recall phase finds them by meaning (if that fails, the
        recall phase embeds them when it searches)."""
        if self.embeddings is None:
            return
        texts = [Library.searched(item) for item in Library.items({slug: entry})]
        if self.embeddings.vectors(texts) is None:
            print(f"  not indexed now ({self.embeddings.failed}); the recall phase indexes it when it searches")
        else:
            print(f"  indexed with {self.embeddings.name}")

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

    @staticmethod
    def read_language_plan(data):
        """A language's plan, checked: its name and version, one tool per role (TOOL_ROLES) with
        the search that finds how to use it, the searches of each section, and the commands that
        only show what is installed (any other is "dropped"). {"not_language": why} when the model
        says the request is no programming language."""
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        language = str(data.get("language") or data.get("name") or "").strip()[:40]
        summary = " ".join(str(data.get("summary") or "").split())[:200]
        if not language:
            if summary:
                return {"not_language": summary}
            raise ValueError("it needs the language's name")
        version = str(data.get("version") or "").strip()[:20]
        tools, commands = {}, list(data.get("commands") or [])
        for tool in data.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            role, tool_name = str(tool.get("role") or "").strip().lower(), str(tool.get("name") or "").strip()[:40]
            if role in TOOL_ROLES and tool_name and role not in tools:
                search = " ".join(str(tool.get("search") or "").split()) or f"how to use {tool_name} in a {language} project"
                tools[role] = {"role": role, "name": tool_name, "search": search}
                commands += [tool["command"]] if tool.get("command") else []
        searches = [{"section": str(s.get("section") or "").strip().lower(), "query": " ".join(str(s.get("query") or "").split())}
                    for s in data.get("searches") or [] if isinstance(s, dict)]
        searches = [s for s in searches if s["section"] in LANGUAGE_SECTIONS and s["query"]][:LANGUAGE_SEARCHES]
        if not searches:
            raise ValueError(f"it needs searches for the language's {', '.join(LANGUAGE_SECTIONS)}")
        commands, dropped = Learner.safe_commands(commands)
        return {"kind": "language", "language": language, "name": f"{language} {version}".strip(), "version": version,
                "summary": summary or language, "tools": list(tools.values()), "searches": searches,
                "commands": list(dict.fromkeys(commands))[:LANGUAGE_COMMANDS], "dropped": dropped}

    def read_page(self, topic, page, number):
        """The facts one page states about what its search looks for, from the parts of its text
        that hold the most of what it looks for (a page's top is often its menus), in page order;
        the search's excerpt when the page itself cannot be read."""
        got = self.internet.fetch(page["url"])
        text = (got or {}).get("content") or page.get("content") or ""
        if not text.strip():
            why = f"{page['url']}: {self.internet.why(page['url'])}"
            self.notes.append(f"page {why}")
            print(f"  page {number}: {why}")
            return []
        source = Part("Page", f"{(got or {}).get('title') or page.get('title', '')}\n{page['url']}", True)
        focus = Part("Look for", page["focus"], True)
        room = self.model.limit_chars(Prompts.FACTS_SYSTEM) - source.size() - focus.size() - len(topic) - 200
        found = []
        for i, chunk in enumerate(self.relevant_chunks(text, room, f"{topic} {page['focus']}"), 1):
            parts = [Part("Topic", topic, True), focus, source, Part("Excerpt", chunk, True)]
            try:
                facts = self.model.ask_until_usable(Prompts.FACTS_SYSTEM, parts, f"Facts, page {number} part {i}",
                                                    self.read_facts, attempts=2) or []
            except PromptTooLarge as exc:
                self.notes.append(f"page {page['url']}: {first_line(str(exc), 160)}")
                break
            except OllamaError as exc:  # it crashed or failed again and again: the pages left are not read
                self.model_failed = first_line(str(exc), 200)
                self.notes.append(f"the model stopped answering at page {number}; the pages after it were not read")
                print(f"  {self.notes[-1]} ({self.model_failed})")
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

    def pick(self, topic, facts, count):
        """The most useful count, as the model picks them (the first ones if it cannot)."""
        listing = "\n".join(f"{i}. [{f['phase']}] {f['text']}" for i, f in enumerate(facts, 1))

        def read(reply):
            numbers = dict.fromkeys(reply.json().get("keep", []))
            kept = [facts[n - 1] for n in numbers if isinstance(n, int) and 1 <= n <= len(facts)]
            return kept[:count]

        try:
            kept = self.model.ask_until_usable(Prompts.PICK_FACTS_SYSTEM.format(count=count),
                                               [Part("Topic", topic, True), Part("Facts", listing, True)], "Pick facts",
                                               read, attempts=2)
        except (PromptTooLarge, OllamaError) as exc:
            print(f"  could not pick ({first_line(str(exc), 120)}): keeping the first {count}")
            kept = None
        return kept or facts[:count]

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
        print(f"\nResearching {topic}")
        known = Library.read(slug)
        frame = self.frame(topic, known if (known or {}).get("kind") == "research" else None)
        facts, sources, analysis = self.investigate(frame)
        if known and (learner.cut_short() or not facts):
            print(f"  {learner.cut_short() or 'no page gave facts'}; kept what was found before "
                  f"({known.get('learned', '?')})")
            learner.kept_earlier = True
            return known
        if not facts:
            print(f"  nothing found about {topic}: " + ("; ".join(learner.notes) or "the pages stated nothing useful"))
            return None
        entry = self.entry(topic, frame, facts, sources, analysis)
        Library.save(slug, entry)
        print(f"  {len(entry['findings'])} finding(s) from {len(facts)} fact(s) on {len(sources)} page(s); kept as "
              f"{slug}")
        learner.index(slug, entry)
        return entry

    def frame(self, topic, known):
        """The topic's name, summary and research questions (each with its searches): as saved for
        it, else from the model."""
        parts = [Part("Topic", topic, True), Part("Today", datetime.date.today().isoformat(), True)]
        frame, saved = self.learner.saved_or_asked(known, self.read_frame, Prompts.RESEARCH_PLAN_SYSTEM, parts,
                                                   "Frame the research", f"researching {topic}",
                                                   Library.slug(topic))
        if not saved:
            print(f"  {frame['name']}: {frame['summary']}")
        for number, question in enumerate(frame["questions"], 1):
            print(f"  Q{number}. {question['question']}")
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
            print(f"  round {round_number}: {len(searches)} search(es), up to {pages} page(s)")
            found, read = self.learner.read_web(frame["name"], searches, pages)
            self.learner.add_facts(facts, found)
            sources += [url for url in read if url not in sources]
            if self.learner.cut_short() or not facts:
                break
            differ = self.cross_check(facts)
            analysis = self.analyse(frame, facts, differ) or analysis
            searches = self.next_searches(frame, facts, analysis)
        return facts, sources, analysis

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

    def cross_check(self, facts):
        """Each fact's sites, with no model call. A fact that repeats another (REPEAT_CLAIM, numbers
        agreeing) is folded into it. A fact making the same claim as one on another site
        (SAME_CLAIM) shares its sites when their numbers agree; when they do not, the pair is
        returned, for the analysis to judge (facts about other things, versions or dates differ
        without contradicting). Without embeddings only the pages that repeat a fact count."""
        vectors = self.learner.embeddings.vectors([fact["text"] for fact in facts], keep=False) \
            if self.learner.embeddings else None
        if vectors is None:
            print("  cross-checking by repeats only: the embedding model gave no embeddings")
            vectors = [None] * len(facts)
        else:
            self.fold_repeats(facts, vectors)
        sites = [{self.site(url) for url in Learner.pages_of(fact)} for fact in facts]
        support, differ = [set(s) for s in sites], []
        for i, j in ((i, j) for i in range(len(facts)) for j in range(i + 1, len(facts))):
            if not self.same_claim(vectors[i], vectors[j]) or sites[i] & sites[j]:
                continue
            if self.agree(facts[i], facts[j]):
                support[i] |= sites[j]
                support[j] |= sites[i]
            else:
                differ.append((i + 1, j + 1))
        for fact, found_on in zip(facts, support):
            fact["sites"] = sorted(found_on)
        return differ

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
            repeat = facts.pop(i)
            vectors.pop(i)
            original = facts[kept]
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

    @staticmethod
    def site(url):
        """The site a page is on (www. left out): pages of one site are one source."""
        return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")

    @classmethod
    def numbers(cls, fact):
        return set(cls.NUMBER_RE.findall(fact["text"]))

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
            print(f"  {self.learner.notes[-1]}")
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
        findings = [{"question": f["question"], "answer": " ".join(str(f.get("answer") or "").split())[:600],
                     "confidence": f.get("confidence") if f.get("confidence") in ("high", "medium", "low") else "low",
                     "facts": cited(f.get("facts"))}
                    for f in data.get("findings") or [] if asked(f) and str(f.get("answer") or "").strip()]
        contradictions = []
        for item in data.get("contradictions") or []:
            if isinstance(item, dict) and len(cited(item.get("facts"))) >= 2:
                facts = cited(item["facts"])
                doubtful = [n for n in cited(item.get("doubtful")) if n in facts] or facts  # unsure: all are
                contradictions.append({"facts": facts, "doubtful": doubtful,
                                       "note": " ".join(str(item.get("note") or "").split())[:300]})
        gaps = [{"question": g["question"], "missing": " ".join(str(g.get("missing") or "").split())[:200],
                 "search": " ".join(str(g.get("search") or "").split())[:160]}
                for g in data.get("gaps") or [] if asked(g) and str(g.get("search") or "").strip()]
        checks = [{"fact": c["fact"], "search": " ".join(str(c.get("search") or "").split())[:160]}
                  for c in data.get("checks") or [] if isinstance(c, dict) and isinstance(c.get("fact"), int)
                  and 1 <= c["fact"] <= fact_count and str(c.get("search") or "").strip()]
        if not findings:
            raise ValueError("it needs a finding for at least one question")
        return {"findings": findings, "contradictions": contradictions,
                "gaps": gaps[:RESEARCH_GAPS], "checks": checks[:RESEARCH_CHECKS]}

    def entry(self, topic, frame, facts, sources, analysis):
        """What is saved: each fact with its question, its sources and its confidence (disputed: a
        contradiction finds it doubtful; corroborated: two or more sites state it; single: one does),
        and each finding no surer than its facts allow (supported)."""
        disputed = {n for contradiction in analysis["contradictions"] for n in contradiction["doubtful"]}
        kept = []
        for n, fact in enumerate(facts, 1):
            sites = fact.pop("sites", [])
            confidence = "disputed" if n in disputed else "corroborated" if len(sites) >= 2 else "single"
            kept.append({**{k: v for k, v in fact.items() if k != "section"}, "question": int(fact["section"]),
                         "sites": sites, "confidence": confidence})
        cited = lambda finding: [kept[n - 1] for n in finding["facts"]]
        findings = [dict(finding, confidence=self.supported(finding["confidence"], cited(finding)))
                    for finding in analysis["findings"]]
        return {"topic": topic, "kind": "research", "name": frame["name"], "summary": frame["summary"],
                "questions": frame["questions"], "learned": now_text(), "model": self.model.name, "machine": [],
                "findings": findings, "contradictions": analysis["contradictions"],
                "open": analysis["gaps"], "facts": kept, "sources": sources, "notes": self.learner.notes}

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
    concern, as an optional part."""

    @staticmethod
    def topics():
        """{slug: entry} of every topic learned."""
        return {slug: entry for slug, entry in Home.store().items("learned").items() if Library.readable(entry)}

    @staticmethod
    def readable(entry):
        return isinstance(entry, dict) and isinstance(entry.get("facts", []), list)

    @staticmethod
    def slug(topic):
        """"Test-driven development" -> test-driven-development, "C++" -> cpp, "Python 3.14" -> python-3-14."""
        text = topic.lower().replace("+", "p").replace("#", "sharp")
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60]

    @staticmethod
    def items(topics):
        """What can be found in these topics ({slug: entry}), one item per text: each topic's
        summary, a language's tools, what is installed here, research findings and each fact (with
        its confidence, when researched), as {topic, name, phase, text, id, status}: a finding's and a
        fact's id and status (None for what is not a claim). A topic that cannot be read is left out,
        with a note."""
        found = []
        for slug, entry in topics.items():
            try:
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
                items += [{"topic": slug, "name": name, "phase": str(f["phase"]),
                           "text": str(f["text"]) + (f" ({f['confidence']})" if f.get("confidence") else ""),
                           "id": Library.claim_id(f, f["text"]), "status": f.get("status", "candidate")}
                          for f in entry["facts"]]
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                print(f"  note: what was learned about {slug} is not readable ({type(exc).__name__}: {exc}); left out")
                continue
            found += items
        return found

    @staticmethod
    def claim_id(claim, text):
        return claim.get("id") or fingerprint(str(text))[:12]

    @staticmethod
    def read(slug):
        """A topic's entry, or None (not learned, or not readable)."""
        entry = Home.store().get("learned", slug) if slug else None
        return entry if Library.readable(entry) else None

    @staticmethod
    def tools_text(tools):
        """A language's tools in one line: "environment venv, tests pytest, format black"."""
        return ", ".join(f"{tool['role']} {tool['name']}" for tool in tools if isinstance(tool, dict) and tool.get("name"))

    @staticmethod
    def language(name):
        """The key of a language learned (with tinker learn, or by a run), under any version of its name; or None."""
        key = language_key(name) if (name or "").strip() else ""
        entry = Library.read(key) if key else None
        return key if entry and entry.get("kind") == "language" else None

    @staticmethod
    def searched(item):
        """The text an item is found by (and embedded as): its topic's name, and its own text."""
        return f"{item['name']}: {item['text']}"

    @staticmethod
    def save(slug, entry):
        """A topic kept: each fact and finding with an id and a status (a candidate until proven)."""
        for claim, text in [(f, f.get("text", "")) for f in entry.get("facts", [])] + \
                           [(f, f.get("answer", "")) for f in entry.get("findings", [])]:
            claim.setdefault("id", fingerprint(str(text))[:12])
            claim.setdefault("status", "candidate")
        Home.store().put("learned", slug, entry)

    @staticmethod
    def shown_in(item, phases):
        """Whether an item belongs in a prompt for these phases: what is installed here goes to the
        toolchain and config files' prompts, a language's tools to those and the tests', research
        findings to the spec's and the design's, a fact to its own phase."""
        shown = {"machine": {"toolchain", "config"}, "tools": {"toolchain", "config", "tests"},
                 "findings": {"spec", "design"}}.get(item["phase"])
        return item["phase"] in phases or bool(shown and shown & set(phases))

    @staticmethod
    def claims(slug, ids, change):
        """The facts and findings of a topic with these ids, changed by change(claim) -> claim or None (gone)."""
        def changed(entry):
            if not entry:
                return entry
            for key in ("facts", "findings"):
                entry[key] = [kept for kept in (change(c) if c.get("id") in ids else c for c in entry.get(key, []))
                              if kept is not None]
            return entry
        if Library.read(slug):
            Home.store().update("learned", slug, changed)


class Knowledge:
    """What earlier projects taught, to do it right the first time. Per language (its language_key):
    the toolchain that last worked and the toolchain fixes found (both replayed without asking the
    model), lessons, and the facts proven by its projects; for any language: lessons. A lesson is
    general: it never names a project, its files, its names or its data. Lessons and facts are shown
    in the prompts of the phase they concern, the most confirmed first; they start as candidates, a
    cycle that succeeds using them proves them, and a failure in the step they were shown to discards
    them. Kept in Tinker's "languages" and "memory"."""

    def __init__(self, language):
        self.language = language or "Python"
        self.key = language_key(self.language)
        self.language_doc = Home.store().get("languages", self.key) or {
            "language": self.language, "projects": 0, "toolchain": None, "fixes": [], "lessons": [], "facts": []}
        self.any_language_doc = Home.store().get("memory", "any") or {"lessons": []}

    def summary(self):
        lessons = len(self.language_doc.get("lessons", [])) + len(self.any_language_doc.get("lessons", []))
        projects, facts = self.language_doc.get("projects", 0), len(self.language_doc.get("facts", []))
        known = [("the toolchain that worked" + (f" ({projects} finished project(s))" if projects else ""))
                 if self.language_doc.get("toolchain") else "",
                 f"{len(self.language_doc.get('fixes', []))} toolchain fix(es)" if self.language_doc.get("fixes") else "",
                 f"{lessons} lesson(s)" if lessons else "", f"{facts} proven fact(s)" if facts else ""]
        return f"{self.language}: " + (", ".join(k for k in known if k) or "nothing learned yet")

    def finished_project(self):
        self.language_doc["projects"] = self.language_doc.get("projects", 0) + 1
        self.save()

    def known_lessons(self):
        return self.language_doc.setdefault("lessons", []) + self.any_language_doc.setdefault("lessons", [])

    def add_lessons(self, new, confirmed, names=()):
        """Adds new lessons as candidates (one like a known lesson confirms it instead; one that names
        the project, by any of names, is not general and is dropped); returns how many were new."""
        known, added = self.known_lessons(), 0
        for number in confirmed:
            if isinstance(number, int) and 1 <= number <= len(known):
                self.confirm(known[number - 1])
        for item in new:
            text, phase = str(item.get("text", "")).strip()[:240], str(item.get("phase", "")).strip().lower()
            if not text or phase not in LESSON_PHASES or self.names_project(text, names):
                continue
            same = next((lesson for lesson in known if similarity(lesson["text"], text) >= SAME_LESSON), None)
            if same:
                self.confirm(same)
                continue
            lesson = {"id": fingerprint(text)[:12], "phase": phase, "text": text, "status": "candidate", "seen": 1,
                      "added": now_text(), "last_seen": now_text()}
            (self.language_doc if item.get("scope") == "language" else self.any_language_doc)["lessons"].append(lesson)
            known.append(lesson)
            added += 1
        for doc in (self.language_doc, self.any_language_doc):
            doc["lessons"] = self.keep(doc["lessons"], known[len(known) - added:])
        self.save()
        return added

    @staticmethod
    def names_project(text, names):
        """Whether a lesson names the project (one of its files, or its folder): then it is not general."""
        return any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text, re.I) for name in names if name)

    def fix_result(self, fix, worked):
        """A replayed fix helped (counted) or failed (deleted at once: it is disproved)."""
        if worked:
            fix["used"] = fix.get("used", 0) + 1
        else:
            self.language_doc["fixes"] = [f for f in self.language_doc.get("fixes", []) if f["failure"] != fix["failure"]]
        self.save()

    def known_fix(self, failure, tried=()):
        """A replayable fix for exactly this failure, not tried yet in this run."""
        return next((f for f in self.language_doc.get("fixes", []) if f["failure"] == failure and f.get("replay")
                     and f["failure"] not in tried), None)

    def save(self):
        Home.store().put("languages", self.key, self.language_doc)
        Home.store().put("memory", "any", self.any_language_doc)

    @staticmethod
    def confirm(lesson):
        lesson["seen"], lesson["last_seen"] = lesson.get("seen", 1) + 1, now_text()

    @staticmethod
    def keep(lessons, new):
        """At most MAX_LESSONS, the most confirmed first; the new ones always stay (the least
        confirmed older ones go to make room)."""
        rank = Knowledge.rank
        fresh = [lesson for lesson in lessons if any(lesson is n for n in new)]
        older = sorted((lesson for lesson in lessons if not any(lesson is n for n in new)), key=rank, reverse=True)
        return sorted(fresh + older[:max(0, MAX_LESSONS - len(fresh))], key=rank, reverse=True)

    def toolchain(self):
        known = Toolchain(self.language_doc.get("toolchain"))
        return known if known else None

    def remember_toolchain(self, toolchain):
        """The toolchain that worked, kept for the language's next projects as what holds for any
        project in it: without its run and check commands, which name this project's own files (the
        next project gives its own)."""
        kept = dict(toolchain.to_dict(), run="", check="") if toolchain else None
        if kept and kept != self.language_doc.get("toolchain"):
            self.language_doc["toolchain"], self.language_doc["toolchain_saved"] = kept, now_text()
            self.save()

    def remember_fix(self, fix):
        fixes = [f for f in self.language_doc.setdefault("fixes", []) if f["failure"] != fix["failure"]]
        self.language_doc["fixes"] = (fixes + [dict(fix, used=1, added=now_text())])[-20:]
        self.save()

    def similar_fix(self, failure):
        scored = [(similarity(f["failure"], failure), f) for f in self.language_doc.get("fixes", [])]
        best = max(scored, key=lambda pair: pair[0], default=(0, None))
        return best[1] if best[0] >= SIMILAR_FAILURE else None

    @staticmethod
    def rank(lesson):
        """The most confirmed, then the most recently seen, first (sorted in reverse)."""
        return lesson.get("seen", 1), lesson.get("last_seen", "")

    def lessons(self, phases, limit=LESSONS_SHOWN):
        """The lessons for these phases, the most confirmed first: [{id, text, ...}]."""
        pool = [lesson for lesson in self.known_lessons() if lesson.get("phase") in phases]
        pool.sort(key=Knowledge.rank, reverse=True)
        return [dict(lesson, id=lesson.get("id") or fingerprint(lesson["text"])[:12]) for lesson in pool[:limit]]

    def facts(self, phases):
        """The language's proven facts for these phases: [{id, phase, text, topic}]."""
        return [fact for fact in self.language_doc.get("facts", []) if fact.get("phase") in phases]

    def prove(self, used):
        """A cycle that used these succeeded: candidate lessons, facts and findings are proven, and the
        web's facts are copied into the language's knowledge. used: {key: [prompt phases]}, the keys as
        Session records them (lesson:ID, fact:TOPIC:ID, known:ID). Returns how many were newly proven."""
        proven = 0
        for lesson in self.known_lessons():
            if f"lesson:{lesson.get('id')}" in used and lesson.get("status") != "proven":
                lesson["status"], proven = "proven", proven + 1
        known = {fact["id"] for fact in self.language_doc.setdefault("facts", [])}
        for slug, ids in self.used_facts(used).items():
            entry = Library.read(slug) or {}
            claims = [c for c in entry.get("facts", []) + entry.get("findings", []) if c.get("id") in ids]
            proven += sum(c.get("status") != "proven" for c in claims)
            Library.claims(slug, ids, lambda claim: dict(claim, status="proven"))
            for claim in claims:
                if claim["id"] not in known and claim.get("phase"):  # a finding stays with its research
                    self.language_doc["facts"].append({"id": claim["id"], "phase": claim["phase"],
                                                       "text": claim["text"], "topic": slug, "status": "proven"})
                    known.add(claim["id"])
        self.save()
        return proven

    def disprove(self, used, phases):
        """A step failed: every lesson, fact or finding used in these prompt phases is discarded, proven
        or not. Returns how many were discarded."""
        shown = {key for key, where in used.items() if set(where) & set(phases)}
        gone = 0
        for doc in (self.language_doc, self.any_language_doc):
            kept = [lesson for lesson in doc.get("lessons", []) if f"lesson:{lesson.get('id')}" not in shown]
            gone, doc["lessons"] = gone + len(doc.get("lessons", [])) - len(kept), kept
        dropped = {key.split(":", 1)[1] for key in shown if key.startswith("known:")}
        for slug, ids in self.used_facts(shown).items():
            Library.claims(slug, ids, lambda claim: None)
            dropped |= ids
        facts = self.language_doc.get("facts", [])
        self.language_doc["facts"] = [fact for fact in facts if fact["id"] not in dropped]
        gone += len(facts) - len(self.language_doc["facts"]) + sum(len(ids) for ids in self.used_facts(shown).values())
        self.save()
        return gone

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
        terms, docs = set(words(query)), [words(text) for text in texts]
        average = sum(len(doc) for doc in docs) / len(docs) or 1
        holding = collections.Counter(term for doc in docs for term in set(doc) & terms)
        scores = []
        for index, doc in enumerate(docs):
            counts, score = collections.Counter(doc), 0.0
            for term in terms & counts.keys():
                idf = math.log(1 + (len(docs) - holding[term] + 0.5) / (holding[term] + 0.5))
                tf, length = counts[term], len(doc) / average
                score += idf * tf * (self.BM25_K1 + 1) / (tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * length))
            if score > 0:
                scores.append((score, index))
        return [index for _, index in sorted(scores, key=lambda pair: (-pair[0], pair[1]))]

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
