"""Planning a cycle: what was learned that it needs, the spec, and the contracts where files meet."""

import ast
lazy import json
lazy import math
import operator
import re
lazy from pathlib import Path

lazy from .knowledge import Embeddings, Library, Retriever, mentions
lazy from .learning import SlotLearner
from .messages import say
lazy from .model import OllamaError
lazy from .prompts import Part, PromptTooLarge, Prompts
from .session import Phase, Worker
lazy from .settings import (
    CHECK_MAX_TOKENS, Config, ENV_EXAMPLE, ENV_FILE, FACT_PHASES, KINDS, RECALL_TOPICS, TOPIC_RELEVANT,
    after_what_they_use,
    first_line, groups_fitting, words,
)
lazy from .workspace import TestRef, Toolchain


class Recaller(Phase):
    """0. Recall: before planning, everything learned is searched for what the request needs, by
    words and by meaning (Retriever), so the call that follows sees the most related topics first:
    it decides which this cycle uses and, in a run with --web, which it should learn now that it has
    not (up to the setting learn_missing): a framework the request names, a technique, the
    language. Those are learned (SlotLearner); the chosen topics' facts, ranked by the same search, are
    kept in the project ("learned"), so they go into its prompts and a resumed run uses the same.
    Without --web there is nothing to learn, so no call: the topics used are those the request names
    or is close to in meaning (relevant)."""
    TITLE = "Recall (what Tinker has learned)"

    def run(self, cycle):
        known, internet = Library.topics(), self.session.internet
        can_learn = Config.get("learn_missing") if internet and internet.available() else 0
        if not known and not can_learn:
            say.info("Nothing learned yet" + ("" if internet else " (with --web, a run learns what it needs)"), "📭")
            cycle["topics"] = []
            return
        query = cycle["request"] + (f"\n{cycle['brief']}" if cycle.get("brief") else "")
        retriever = Retriever(Embeddings.configured())
        if can_learn:
            use, learn = self.ask(cycle, query, known, self.ranked_topics(known, query, retriever), can_learn)
        else:
            use, learn = self.relevant(known, query), []
        learned = [slug for slug in (self.learn(topic, internet, retriever) for topic in learn[:can_learn]) if slug]
        cycle["topics"] = list(dict.fromkeys(learned + use))[:RECALL_TOPICS]  # what it just learned it needs most
        self.keep_facts(cycle, query, retriever)
        self.say_used(cycle)
        self.use_language(cycle)
        self.session.save()

    @staticmethod
    def say_used(cycle):
        """The topics the cycle uses, by their names."""
        names = [(Library.read(slug) or {}).get("name", slug) for slug in cycle["topics"]]
        say.info(f"Using what was learned about: {', '.join(names) or 'nothing'}", "🧠")

    @staticmethod
    def ranked_topics(known, query, retriever):
        """The learned topics, the most related to the request first, by what they hold."""
        items = Library.items(known)
        order = retriever.rank(query, [Library.searched(item) for item in items], prune=True)
        if known:
            say.info(f"Searched {len(items)} learned texts of {len(known)} topic(s) by {retriever.used}", "🔍")
        return list(dict.fromkeys(items[index]["topic"] for index in order))

    def ask(self, cycle, query, known, ranked, can_learn):
        """(slugs of learned topics to use, topics to learn now). The topics are listed the most
        related to the request first (Retriever), as many as fit."""
        parts = self.recall_parts(cycle, query, known, ranked, can_learn)
        try:
            return self.session.ask_until_usable(Prompts.RECALL_SYSTEM, parts, "Recall",
                                                 lambda reply: self.read_choice(reply, known), attempts=2)
        except (SystemExit, OllamaError) as exc:  # what was learned only helps: the run goes on without it
            say.warn(f"{exc} Going on without what was learned.")
            return [], []

    def recall_parts(self, cycle, query, known, ranked, can_learn):
        """The recall prompt: the request, the language, whether it may learn now, then the topics that fit."""
        fixed = [Part("Request", query, True, keep="head"),
                 Part("Project language", self.project.language if cycle["id"] != "base" else ""),
                 Part("Learning now", f"possible: name up to {can_learn} topics to learn" if can_learn
                      else "not possible: name none", True)]
        # a long request is shortened, the topics keep half the room
        left = self.session.room(Prompts.RECALL_SYSTEM) - 60
        room = max(left - Part.total_size(fixed), (left - sum(part.floor_size() for part in fixed)) // 2)
        return fixed + [Part("Learned topics", self.topic_listing(known, ranked, room) or "(none yet)", True,
                             exact=True)]

    @staticmethod
    def topic_listing(known, ranked, room):
        """One line per topic ("slug: name, summary"), in the order given, as many as fit room."""
        lines, used = [], 0
        for slug in ranked:
            entry = known[slug]
            line = f"- {slug}: {entry.get('name', slug)}, {first_line(str(entry.get('summary', '')), 120)}"
            if used + len(line) + 1 > room:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    @staticmethod
    def read_choice(reply, known):
        """(the known topics to use, the topics to learn: one per topic, as Flask and flask are one)."""
        data = reply.json()
        use = [str(slug).strip() for slug in data.get("use", []) if str(slug).strip() in known]
        learn = [" ".join(str(topic).split())[:80] for topic in data.get("learn", []) if str(topic).strip()]
        learn = {Library.slug(topic): topic for topic in reversed(learn) if Library.slug(topic)
                 and Library.slug(topic) not in known}
        return use, list(reversed(learn.values()))

    @staticmethod
    def relevant(known, query):
        """The learned topics a request needs, with no model call: those whose name it says, and
        those close to it in meaning (TOPIC_RELEVANT, by the embedding model when there is one)."""
        text = query.lower()
        named = [slug for slug, entry in known.items()
                 if any(mentions(text, name) for name in (str(entry.get("name") or slug), entry.get("language") or slug))]
        embeddings = Embeddings.configured()
        about = [f"{entry.get('name') or slug}: {entry.get('summary', '')}" for slug, entry in known.items()]
        vectors = embeddings.vectors([query] + about, keep=False) if embeddings and known else None
        close = [slug for slug, vector in zip(known, (vectors or [])[1:])
                 if Embeddings.cosine(vectors[0], vector) >= TOPIC_RELEVANT]
        return list(dict.fromkeys(named + close))

    def learn(self, topic, internet, retriever):
        """The slug of what was learned now for a topic (a topic that is a language: that language's
        slug, which it may have been learned under already), or None and why not (learning is
        optional: the run goes on); indexed with the embeddings the search uses (None after they
        failed: then not indexed)."""
        if not internet.available():
            say.skip(f"Not learning {topic}: {internet.problem()}")
            return None
        try:
            entry = SlotLearner(self.session.model, internet, retriever.embeddings).learn(topic)
        except (SystemExit, PromptTooLarge, OllamaError) as exc:
            say.fail(f"Could not learn {topic}: {exc}")
            return None
        if not entry:
            return None
        return Library.language(entry["language"]) if entry.get("kind") == "language" else Library.slug(topic)

    def keep_facts(self, cycle, query, retriever):
        """The facts of the chosen topics, the most related to the request first, kept in the
        project ("learned", with earlier cycles' after them) for the prompts of every phase."""
        chosen = {slug: Library.read(slug) for slug in cycle["topics"]}
        items = [item for item in Library.items({slug: entry for slug, entry in chosen.items() if entry})
                 if item["phase"] != "summary"]
        order = retriever.rank(query, [Library.searched(item) for item in items])
        ranked = self.essentials_first([items[index] for index in order])
        earlier = [item for item in self.project.spec.get("learned", []) if item["topic"] not in cycle["topics"]]
        self.project.spec["learned"] = ranked + earlier  # a topic chosen again: its current facts only
        self.keep_out_inapplicable()

    def keep_out_inapplicable(self):
        """What the project does not need is kept out of its prompts ("inapplicable"), proven or not: what
        applies only to code (its "when") the spec did not say the project has (the spec's "situations"), and
        what names a tool a project here cannot count on."""
        known = self.known_rules()
        left_out = self.for_other_code(known) | self.needing_tools_missing_here(known)
        self.project.spec["inapplicable"] = sorted(left_out)
        if left_out:
            say.skip(f"Left out {len(left_out)} learned rule(s) or fact(s) this project does not need")

    def known_rules(self):
        """What was learned for the project: the chosen topics' items, and the language's proven facts once the
        language is known."""
        return self.project.spec.get("learned", []) + (self.session.knowledge.facts(FACT_PHASES)
                                                       if self.project.language else [])

    def situations(self):
        """The distinct kinds of code learned rules apply only to (their "when"), for the spec to choose from."""
        return list(dict.fromkeys(item["when"] for item in self.known_rules() if item.get("when")))

    def for_other_code(self, known):
        """The ids of what applies only to code the spec did not say the project has."""
        has = set(self.project.spec.get("situations", []))
        return {item["id"] for item in known if item.get("when") and item.get("id") and item["when"] not in has}

    def needing_tools_missing_here(self, known):
        """The ids of what names a learned tool a project here cannot count on (Knowledge.unavailable_tools)."""
        missing = self.session.knowledge.unavailable_tools()
        return {item["id"] for item in known if item.get("id") and any(mentions(item["text"], name) for name in missing)}

    @staticmethod
    def essentials_first(ranked):
        """What the toolchain is built from (the tools, what is installed), then a language's rules, then the
        facts: a full prompt drops its last lines first."""
        order = lambda item: 0 if item["phase"] in ("tools", "machine") else 1 if item.get("kind") == "rule" else 2
        return sorted(ranked, key=order)

    def use_language(self, cycle):
        """The project's language as learned (tinker learn): used by every cycle of a project
        in it, not left to the search; once the spec has named the language, its facts join the
        cycle's (the spec phase calls this for a new project)."""
        slug = Library.language(self.project.language)
        if not slug or slug in cycle.get("topics", []):
            return
        cycle["topics"] = [slug] + cycle.get("topics", [])[:RECALL_TOPICS - 1]
        query = cycle["request"] + (f"\n{cycle['brief']}" if cycle.get("brief") else "")
        self.keep_facts(cycle, query, Retriever(Embeddings.configured()))
        say.info(f"Using the language as learned: {Library.read(slug).get('name', slug)}", "🧠")
        self.session.save()


class SpecWriter(Phase):
    """1. Spec: one call plans the project as a small, testable spec: the criteria (each one
    behaviour the request clearly asks for, with one unit test), the files that hold them and
    what uses what, the file a person starts (what a person uses has one, with the simplest
    interface that fits; only code for other code has none) and,
    for a new project, the toolchain (the one that worked before for the language, reused). What
    the request does not ask for waits until the user asks (tinker add, tinker fix).
    A plan the person confirms before anything is built (the conversation's way; --confirm) is a quick
    draft (thinking low): the person reviews it, and changes it with tinker amend."""
    TITLE = "Plan: the spec"

    def run(self, cycle):
        if cycle.get("specified"):
            return
        system, parts = self.spec_prompt(cycle)
        think = self.thinking("low") if cycle.get("confirm") else None
        self.take(cycle, self.session.ask_until_usable(system, parts, "Spec", lambda reply: self.read_spec(reply, cycle),
                                                       think=think))

    def spec_prompt(self, cycle):
        self.listed = Recaller(self.session).situations()
        system, parts = self.plan_prompt(cycle)
        return system, parts + [self.situations_part()]

    def situations_part(self):
        """The kinds of code some learned rules apply to, numbered, for the spec to say which this project has
        (its other rules stay out of the prompts)."""
        listing = "\n".join(f"{number}. {when}" for number, when in enumerate(self.listed, 1))
        return Part('Code some learned rules are for: add "situations": [the numbers of those this project\'s code '
                    'will have] to the reply', listing, atomic=True)

    def plan_prompt(self, cycle):
        if cycle["id"] == "base":
            learned = self.session.knowledge.toolchain()
            return Prompts.SPEC_SYSTEM, [
                Part("Request", asked(cycle), True),
                Part("Requirements from the spec file", cycle.get("brief", "")),
                Part(self.session.knowledge.toolchain_title(
                         f"reuse it when the project is in {self.session.knowledge.language}: "
                         f"change only what names that project's files, and give run for this project's entry point "
                         f"when it has one"),
                     json.dumps(learned.to_dict()) if learned else "", atomic=True, place="first"),
                *self.session.knowledge_parts("spec", "design", "config", "toolchain")]
        return Prompts.FEATURE_SPEC_SYSTEM, [
            Part("Change requested", asked(cycle), True, keep="tail"),
            Part("Project", self.project.run["task"], True),
            Part("Language", self.project.language),
            Part("Feature requirements", cycle.get("brief", "")),
            Part("What it does now (passing criteria)",
                 "\n".join(f"- {criterion['behaviour']}" for criterion in self.project.criteria
                           if criterion["status"] == "green"), keep="tail"),
            Part("Project files", self.project.outline()),
            *self.session.knowledge_parts("spec", "design"),
        ]

    def thinking(self, level):
        """A think setting below the model's own: level when the model takes levels, off for "none"."""
        think = self.session.model.think
        if think in (None, False):
            return think
        return False if level == "none" else level if isinstance(think, str) else think

    def read_spec(self, reply, cycle):
        """The plan, checked: criteria traced to the request, files (each with its kind, role and
        the files it uses), the entry point (one of the files, or none) and, for a new project, a
        usable toolchain."""
        data = reply.spec_json()
        criteria = self.criteria_from(data.get("criteria"), cycle)
        if not criteria:
            raise ValueError("no criteria listed (or none quotes the request)")
        self.send_back_wrong_working(criteria)
        self.place_tests(criteria, data.get("files") or [])
        files = self.without_unneeded_settings(
            self.planned_files(data.get("files") or [], {TestRef(criterion["test"]).file for criterion in criteria}),
                                               criteria)
        entry = self.planned_entry(data.get("entry"), files)
        if cycle["id"] == "base" and entry:
            self.send_back_unrun_program(criteria, entry, data.get("toolchain"))
        toolchain = None
        if cycle["id"] == "base" or not self.project.toolchain:
            given = data.get("toolchain")
            if not isinstance(given, dict):
                raise ValueError("it needs the toolchain")
            toolchain = Designer.read_toolchain_data(given)
        language = str(data.get("language") or "").strip() or "Python"
        return {"language": language, "criteria": criteria, "files": list(files.values()), "entry": entry,
                "toolchain": toolchain, "situations": self.chosen_situations(data.get("situations")), "data": data}

    def chosen_situations(self, numbers):
        """The listed kinds of code the reply says the project has (by number)."""
        listed = getattr(self, "listed", [])
        return [listed[number - 1] for number in numbers or [] if isinstance(number, int) and 1 <= number <= len(listed)]

    def criteria_from(self, items, cycle, taken=()):
        """The criteria the reply gives, each traced to the request (else dropped)."""
        request = asked(cycle) + "\n" + cycle.get("brief", "")
        out = []
        for item in items if isinstance(items, list) else []:
            text = str(item.get("text") or item.get("behaviour") or "").strip() if isinstance(item, dict) else ""
            if not text:
                continue
            source = str(item.get("source", "")).strip()
            if not self.traced(source, request):
                say.skip(f"Dropped a criterion the request does not ask for: {first_line(text, 90)} "
                         f"(source: {first_line(source, 60) or 'none'})")
                continue
            out.append(self.project.new_criterion("C", text[:80], text, self.test_id(item.get("test"), text,
                                                                                     list(taken) + out),
                                                  cycle["id"], source, "todo", self.effort(item.get("effort"))))
        return out

    @staticmethod
    def traced(source, request):
        """Whether a criterion's source is copied from the request: "setting" (a required setting's
        message) or at least 60% of its words of 3+ letters appear in the request. A note in brackets
        after the quote (what the criterion adds to the words it quotes) is not part of the quote."""
        if source.strip().lower().strip('"') == "setting":
            return True
        quoted = re.sub(r"\([^)]*\)", " ", source)
        wanted = [word for word in words(quoted) if len(word) >= 3]
        have = set(words(request))
        return bool(wanted) and sum(word in have for word in wanted) / len(wanted) >= 0.6

    def test_id(self, given, text, siblings):
        ref = TestRef(str(given or "").strip())
        if not ref.name:
            default_file = TestRef(self.project.criteria[0]["test"]).file if self.project.criteria else "tests/test_app"
            ref = TestRef(f"{ref.file if ref.file and ref.file != str(given) else default_file}::"
                          f"test_{'_'.join(words(text)[:5])}")
        taken = {criterion["test"] for criterion in self.project.criteria + siblings}
        return str(ref) if str(ref) not in taken else f"{ref}_{len(taken) + 1}"

    @staticmethod
    def effort(value):
        try:
            return min(max(int(value), 1), 3)
        except TypeError, ValueError:
            return 2

    def send_back_wrong_working(self, criteria):
        """A criterion whose working is wrong is sent back, twice at most (then the review of a failing test
        is left to find it)."""
        wrong = [f"in \"{criterion['behaviour']}\" the working {mistake}" for criterion in criteria
                 for mistake in Working.mistakes(criterion["behaviour"])]
        self.sent_back = getattr(self, "sent_back", 0) + bool(wrong)
        if wrong and self.sent_back <= 2:
            raise ValueError("; ".join(wrong) + ". Give the same spec again with only these values corrected (the "
                             "working's own result, and what the criterion shows from it); every other criterion "
                             "and file stays as it was")

    def place_tests(self, criteria, listed):
        """Each criterion's test in a test file of its own, as the plan lists its test files."""
        listed_tests = [str(Path(str(item.get("path", "")).strip())) for item in listed
                        if isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "test"]
        listed_code = {str(Path(str(item.get("path", "")).strip())) for item in listed
                       if isinstance(item, dict) and str(item.get("kind") or "code").strip().lower() == "code"}
        for criterion in criteria:
            ref = TestRef(criterion["test"])
            ref = TestRef(f"{self.listed_test_file(ref.file, listed_tests)}::{ref.name}")
            if self.outside_a_test_file(ref, listed_code):
                raise ValueError(f"test {criterion['test']} is not in a test file of its own: write it as "
                                 f"<test file path>::<test name> (e.g. tests/test_app.py::test_add), and put the "
                                 f"code it calls where that test file can reach it")
            criterion["test"] = str(ref)

    @staticmethod
    def outside_a_test_file(ref, listed_code):
        """Whether a test is not in a test file of its own: no file named, no file type, or a code file."""
        return "::" in ref.file or not Path(ref.file).suffix or ref.file in listed_code

    def listed_test_file(self, file, listed_tests):
        """The test file a criterion names, as the plan lists it: a test named the language's own
        way ("calculator::test_add" for tests/calculator.rs) names its file by the file's stem."""
        if file in listed_tests or file in self.project.files or not listed_tests:
            return file
        same_stem = [path for path in listed_tests if Path(path).stem == Path(file).stem]
        return same_stem[0] if same_stem else listed_tests[0] if len(listed_tests) == 1 else file

    def planned_files(self, listed, test_files):
        """{path: entry} of the files the plan lists, and of each test file the criteria name."""
        files = {}
        for item in listed:
            if isinstance(item, (dict, str)):
                entry = Designer.layout_file({"path": item} if isinstance(item, str) else item, test_files,
                                             self.workspace)
                files[entry["path"]] = entry
        for path in sorted(test_files - set(files)):
            files[path] = {"path": path, "kind": "test", "role": "tests", "contract": [], "uses": []}
        return files

    def without_unneeded_settings(self, files, criteria):
        """The files planned, without a settings file no spec point needs: one is planned only when a criterion
        is about a setting (nothing is built that the spec does not need)."""
        if ENV_EXAMPLE not in files or ENV_EXAMPLE in self.project.files or any(map(self.about_a_setting, criteria)):
            return files
        say.skip(f"Left out {ENV_EXAMPLE}: no spec point is about a setting")
        return {path: entry for path, entry in files.items() if path != ENV_EXAMPLE}

    @staticmethod
    def about_a_setting(criterion):
        text = f"{criterion['source']} {criterion['behaviour']}".lower()
        return criterion["source"].strip().lower() == "setting" or ENV_EXAMPLE in text or ENV_FILE in text \
            or bool(re.search(r"\bsettings?\b", text))

    def planned_entry(self, given, files):
        """The file a person starts: one of the files (named by its path, or by its name alone when only one
        has it), or none."""
        entry = str(Path(str(given or "").strip())) if str(given or "").strip() else ""
        named = [path for path in list(files) + list(self.project.files) if Path(path).name == entry]
        entry = named[0] if self.unplanned(entry, files) and len(set(named)) == 1 else entry
        if entry and self.unplanned(entry, files):
            raise ValueError(f"entry {entry} is not one of the files")
        return entry

    def unplanned(self, path, files):
        """Whether a file is neither planned now nor in the design."""
        return path not in files and path not in self.project.files

    def send_back_unrun_program(self, criteria, entry, toolchain):
        """A program a person runs is checked as it is run: a plan with no criterion that runs it is sent back,
        twice at most."""
        run = str((toolchain or {}).get("run") or "") if isinstance(toolchain, dict) else ""
        unrun = not any(self.runs_program(criterion["behaviour"], entry, run) for criterion in criteria)
        self.sent_back = getattr(self, "sent_back", 0) + unrun
        if unrun and self.sent_back <= 2:
            raise ValueError(f"{entry} is a program a person runs, yet no criterion runs it: add its normal use "
                             f"as a person runs it, with what it prints (through {entry}'s entry point or its "
                             f"run command), next to the criteria already there")

    @staticmethod
    def runs_program(text, entry, run):
        """Whether a criterion uses the program as a person runs it: it calls its entry point (main(),
        the entry module's own function), names its run command, is written as a command line
        ("roman 42 prints XLII"), says it runs it, or is about its input, output or exit code."""
        lower, stem = text.lower(), Path(entry).stem.lower()
        command = " ".join(run.split()[:2]).lower()
        return ("main(" in lower or f"{stem}(" in lower or f"{stem}." in lower and "main" in lower
                or bool(command) and command in lower
                or bool(re.prefixmatch(r"\s*`?[a-z][\w.-]*`?(?: [^\s(]+)*? (prints|outputs|shows|writes)\b", lower))
                or bool(re.search(r"\b(run|runs|running|ran|launch|launched|invoked?|executed?|stdin|stdout|"
                                  r"standard (input|output)|exit code|exits)\b", lower)))

    def take(self, cycle, plan):
        """A plan read from a spec reply, made the cycle's spec."""
        self.project.spec["situations"] = list(dict.fromkeys(self.project.spec.get("situations", [])
                                                             + plan["situations"]))
        if cycle["id"] == "base" or not self.project.language:
            self.project.spec["language"] = plan["language"]
            Recaller(self.session).use_language(cycle)
        if plan["toolchain"]:
            self.project.toolchain = plan["toolchain"]
        self.project.criteria.extend(plan["criteria"])
        self.keep_files(cycle, plan["files"], plan["entry"])
        self.ensure_run_command(plan["entry"])
        self.keep_scope(cycle, plan["data"])
        Designer(self.session).drop_adapter_criteria(cycle)
        self.say_planned(cycle, plan)
        Recaller(self.session).keep_out_inapplicable()
        cycle["specified"] = True
        self.session.save()

    def ensure_run_command(self, entry):
        """A program a person runs needs its run command: one the toolchain lacks is asked for."""
        if entry and not self.project.toolchain.run:
            say.step(f"The toolchain does not say how to start {entry}: asking for it", "🤔")
            self.project.toolchain = Designer(self.session).ask_toolchain()

    def keep_scope(self, cycle, data):
        """What the cycle must not do, the constraints it keeps, and the sample data its adapters stand for."""
        cycle["non_goals"] = self.strings(data.get("non_goals"), 12)
        cycle["constraints"] = self.strings(data.get("constraints"), 8)
        sample = data.get("data", "")
        cycle["data"] = sample if isinstance(sample, str) else json.dumps(sample)

    def say_planned(self, cycle, plan):
        """The plan in a few lines: its language, criteria and files, its toolchain when new, what is known."""
        starts = f"; a user starts {self.project.spec['entry']}" if self.project.spec.get("entry") else ""
        say.info(f"Language: {self.project.language}; {len(self.project.cycle_criteria(cycle))} criteria; files: "
                 f"{', '.join(cycle['files'])}{starts}", "📋")
        if plan["toolchain"]:
            say.info(f"Toolchain: {self.project.toolchain.summary()}", "🔧")
        say.info(f"Learned so far: {self.session.knowledge.summary()}", "🧠")

    def keep_files(self, cycle, files, entry):
        """The planned files go into the design (a file planned before keeps what it had), in
        build order; a new project gets a README."""
        if cycle["id"] == "base" and not self.has_docs(files):
            files.append({"path": "README.md", "kind": "docs", "role": "how to set up, run and test it",
                          "contract": [], "uses": []})
        paths = {file["path"] for file in files} | set(self.project.files)
        for file in files:
            file["uses"] = [used for used in file["uses"] if used in paths and used != file["path"]]
        Designer.give_templates_a_renderer(files, entry)
        for file in files:
            known = self.project.files.get(file["path"], {})
            self.project.files[file["path"]] = {"kind": file["kind"], "role": file["role"] or known.get("role", ""),
                                                "contract": known.get("contract", []),
                                                "uses": list(dict.fromkeys(known.get("uses", []) + file["uses"]))}
        if entry or cycle["id"] == "base":
            self.project.spec["entry"] = entry
        cycle["files"] = Designer.order([file for file in files if file["path"] != ENV_FILE])

    @staticmethod
    def has_docs(files):
        """Whether one of the files is documentation (a README)."""
        return any(file["kind"] == "docs" for file in files)

    @staticmethod
    def strings(items, limit):
        return [str(item) for item in (items or []) if str(item).strip()][:limit]

    def amend(self, cycle, change):
        """The waiting plan changed as the person asks, before anything is built: one call without thinking
        gives the whole spec again, from the spec as it is and the change, checked as the first one was and
        numbered from where the cycle began; then the cycle plans its design and review again (they wait
        once more). What changed (+ added, ~ changed, - removed) is kept for the summary; a reply that
        never works leaves the plan as it was."""
        before = self.project.cycle_criteria(cycle)
        current = self.current_spec(cycle, before)
        saved = (list(self.project.criteria), self.project.spec.get("counter", 0), dict(self.project.files))
        self.forget(cycle)
        cycle.setdefault("amendments", []).append(change)
        system, parts = self.spec_prompt(cycle)
        parts += [Part("The spec as it is now: give it again whole, in the same JSON, changed only where the "
                       "person's change asks (a criterion the change adds quotes the change as its source)",
                       json.dumps(current), True, atomic=True),
                  Part("The person's change", change, True)]
        try:
            plan = self.session.ask_until_usable(system, parts, "Amend the spec",
                                                 lambda reply: self.read_spec(reply, cycle), think=self.thinking("none"))
        except BaseException:
            self.restore(cycle, saved)
            raise
        self.take(cycle, plan)
        cycle["changes"] = self.changes(before, self.project.cycle_criteria(cycle))
        cycle.update(phase="design", awaiting=False, confirmed=False, contracts_done=[])
        cycle.pop("review", None)
        self.session.save()

    def current_spec(self, cycle, criteria):
        """The cycle's spec in the JSON a spec reply gives."""
        files = [{"path": path, "kind": self.project.files[path]["kind"],
                  "role": self.project.files[path].get("role", ""), "uses": self.project.files[path].get("uses", [])}
                 for path in cycle.get("files", []) if path in self.project.files]
        spec = {"language": self.project.language,
                "criteria": [{"text": criterion["behaviour"], "source": criterion.get("source", ""),
                              "test": criterion["test"],
                              "effort": criterion.get("effort", 2)} for criterion in criteria],
                "files": files, "entry": self.project.spec.get("entry", ""), "non_goals": cycle.get("non_goals", []),
                "constraints": cycle.get("constraints", []), "data": cycle.get("data", "")}
        if cycle["id"] == "base":
            spec["toolchain"] = self.project.toolchain.to_dict()
        return spec

    def forget(self, cycle):
        """The cycle's spec taken back, as if never planned: its criteria (numbered again from where they
        began) and the files it planned that are not written yet."""
        self.project.criteria[:] = [criterion for criterion in self.project.criteria
                                    if criterion.get("cycle") != cycle["id"]]
        self.project.spec["counter"] = max([int(number) for criterion in self.project.criteria
                                            for number in re.findall(r"\d+", criterion["id"])[:1]] or [0])
        for path in cycle.get("files", []):
            if not self.workspace.exists(path):
                self.project.files.pop(path, None)
        cycle["specified"] = False

    def restore(self, cycle, saved):
        """The plan as it was before a change that did not work."""
        self.project.criteria[:], self.project.spec["counter"] = saved[0], saved[1]
        self.project.files.clear()
        self.project.files.update(saved[2])
        cycle["amendments"].pop()
        cycle["specified"] = True

    @staticmethod
    def changes(before, after):
        """What a change did to the criteria, matched by their tests: + added, ~ changed, - removed."""
        was = {criterion["test"]: criterion["behaviour"] for criterion in before}
        now = {criterion["test"] for criterion in after}
        lines = [f"{'+' if criterion['test'] not in was else '~'} {criterion['id']} {criterion['behaviour']}"
                 for criterion in after
                 if was.get(criterion["test"]) != criterion["behaviour"]]
        lines += [f"- {text}" for test, text in was.items() if test not in now]
        return lines or ["no spec point changed"]


class Working:
    """The arithmetic a criterion shows for a value it states ("(300 − 273.15) × 9/5 + 32 = 80.33"),
    worked out here: a model's sums go wrong, and a wrong value in a criterion becomes a test the
    right code cannot pass. Only plain arithmetic is checked (numbers, + − × ÷ and brackets)."""
    ARITHMETIC = r"[\d.\s()+\-*/]"
    CHAIN_RE = re.compile(rf"{ARITHMETIC}+(?:={ARITHMETIC}+)+")
    SIGNS = {"×": "*", "÷": "/", "−": "-", "–": "-"}
    OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

    @classmethod
    def mistakes(cls, text):
        """Each chain of sums in text ("100 × 9/5 + 32 = 180 + 32 = 212") with a part whose value
        is not the chain's last one (within the rounding the last one is written with)."""
        plain = cls.plain(text)
        found = []
        for chain in cls.CHAIN_RE.finditer(plain):
            parts = [cls.balanced(part) for part in chain.group(0).split("=")]
            worked = [(part, value) for part, value in ((part, cls.value(part)) for part in parts) if value is not None]
            if len(worked) < 2 or not any(cls.does_arithmetic(expression) for expression, _ in worked):
                continue
            cut_short = plain[chain.end():].lstrip().startswith(("…", "..."))  # 37.777… goes on: not rounded
            found += cls.wrong_parts(worked, cut_short)
        return found

    @staticmethod
    def does_arithmetic(expression):
        """Whether an expression works something out: a sign between two numbers (or brackets)."""
        return re.search(r"[\d)]\s*[-+*/]\s*[\d(]", expression)

    @classmethod
    def plain(cls, text):
        """The text with its signs as Python writes them (× is *, ÷ is /, 2 x 3 is 2 * 3)."""
        for sign, same in cls.SIGNS.items():
            text = text.replace(sign, same)
        return re.sub(r"(?<=[\d)])\s*x\s*(?=[\d(])", " * ", text)

    @staticmethod
    def balanced(part):
        """A part of a chain without the brackets around it that belong to the sentence."""
        part = part.strip()
        while part.startswith("(") and part.count("(") > part.count(")"):
            part = part[1:].strip()
        while part.endswith(")") and part.count(")") > part.count("("):
            part = part[:-1].strip()
        return part

    @classmethod
    def value(cls, sum_):
        """The value of plain arithmetic, or None when it is not that (or divides by zero)."""
        def worked(node):
            match node:
                case ast.Constant(value=int() | float() as number):
                    return number
                case ast.UnaryOp(op=ast.USub(), operand=inner):
                    return -worked(inner)
                case ast.UnaryOp(op=ast.UAdd(), operand=inner):
                    return worked(inner)
                case ast.BinOp(left, op, right) if type(op) in cls.OPERATIONS:
                    return cls.OPERATIONS[type(op)](worked(left), worked(right))
            raise ValueError(node)
        try:
            return worked(ast.parse(sum_, mode="eval").body)
        except SyntaxError, ValueError, ZeroDivisionError, TypeError:
            return None

    @staticmethod
    def wrong_parts(worked, cut_short):
        """Each part of a chain whose value is not its last one's, within the places that one is written to."""
        last, result = worked[-1]
        places = len(last.partition(".")[2]) if re.fullmatch(r"-?\d+(\.\d+)?", last) else 6
        found = []
        for part, value in worked[:-1]:
            if cut_short and math.trunc(value * 10 ** places + 1e-9) == round(result * 10 ** places):
                continue
            if cut_short or abs(value - result) > 0.5 * 10 ** -places + 1e-9:
                found.append(f"{part} is {round(value, max(places, 2) if places < 6 else 4):g}, not {last}")
        return found


class Designer(Phase):
    """2. Design, only where files meet: the contract of each code file other files use (the
    exact names, signatures and data the two sides must agree on), written callee first. A file
    no other file uses needs none: its tests say what it does."""
    TITLE = "Plan: the design"

    def run(self, cycle):
        self.ensure_toolchain()
        ContractWriter(self.session).write_all(cycle)

    def ensure_toolchain(self):
        """A project planned without a toolchain (or before toolchains existed) gets one."""
        if not self.project.toolchain:
            self.project.toolchain = self.ask_toolchain()
            say.info(f"Toolchain: {self.project.toolchain.summary()}", "🔧")
            self.session.save()

    def ask_toolchain(self):
        learned, entry = self.session.knowledge.toolchain(), self.project.spec.get("entry", "")
        parts = [Part("Language", self.project.language or "Python", True),
                 Part("Entry point (the file a user starts)", entry, bool(entry)),
                 Part("Project files", self.project.outline(), True, exact=True),
                 Part(self.session.knowledge.toolchain_title(
                          "reuse it; adapt only project names, and give run for this project's entry point"),
                      json.dumps(learned.to_dict()) if learned else "", atomic=True),
                 *self.session.knowledge_parts("toolchain")]
        return self.session.ask_until_usable(Prompts.TOOLCHAIN_SYSTEM, parts, "Toolchain",
                                             lambda reply: self.read_toolchain_data(reply.json().get("toolchain", reply.json()),
                                                                              entry))

    @staticmethod
    def read_toolchain_data(data, entry=""):
        """A usable toolchain (one with a run command when a user starts a file), or ValueError."""
        toolchain = Toolchain(data if isinstance(data, dict) else {})
        if entry and not toolchain.run:
            raise ValueError(f"toolchain.run is empty, but a user starts {entry}: give the command that starts it")
        for command in toolchain.dropped:
            say.skip(f"Toolchain: left out `{command}`: Tinker makes {ENV_FILE} itself, from {ENV_EXAMPLE}")
        for command in toolchain.machine:
            say.skip(f"Toolchain: left out `{command}`: it changes the machine's own tools, not this project")
        problems = toolchain.problems()
        if problems:
            raise ValueError("; ".join(problems))
        return toolchain

    def drop_adapter_criteria(self, cycle):
        """An adapter to an external service has no criteria: one about it is dropped (the user
        tries the service by hand; everything else is tested with sample data)."""
        adapters = [path for path, info in self.project.files.items() if info.get("kind") == "code"
                    and info.get("role", "").strip().lower().startswith("adapter")]
        for step in list(self.project.cycle_criteria(cycle)):
            subject = step["behaviour"].lstrip("`'\" ").lower()
            if self.about_an_adapter(subject, adapters):
                self.project.criteria.remove(step)
                say.skip(f"Dropped {step['id']}: it tests an adapter to an external service "
                         f"({first_line(step['behaviour'], 80)})")

    @staticmethod
    def about_an_adapter(subject, adapters):
        """Whether a criterion's subject is one of the adapters (named as a module, or by its file)."""
        return any(subject.startswith((Path(adapter).stem.lower() + ".", Path(adapter).name.lower()))
                   for adapter in adapters)

    @staticmethod
    def layout_file(item, test_files, workspace):
        path = str(Path(str(item.get("path", "")).strip()))
        workspace.resolve(path, for_write=True)
        kind = "test" if path in test_files else (str(item.get("kind") or "").strip().lower() or "code")
        uses = item.get("uses", [])
        return {"path": path, "kind": kind if kind in KINDS else "code",
                "role": str(item.get("role") or item.get("purpose") or ""), "contract": [],
                "uses": [str(Path(used)) for used in uses if isinstance(used, str)] if isinstance(uses, list) else []}

    @staticmethod
    def give_templates_a_renderer(files, entry):
        """A template no file uses goes to the code file whose role names it, else to the entry point."""
        used = {*file["uses"] for file in files}
        code = [file for file in files if file["kind"] == "code"]
        for template in [file for file in files if file["kind"] == "template" and file["path"] not in used]:
            name = Path(template["path"]).name
            owner = next((file for file in code if name in file["role"]), None) or next((file for file in code
                                                                                         if file["path"] == entry),
                                                                                        None)
            if owner:
                owner["uses"].append(template["path"])

    @staticmethod
    def order(files):
        """Config first, then each file after the files it uses; tests and docs last."""
        by_path = {file["path"]: file for file in files}
        starts = [file["path"] for kind in ("config", "template", "code") for file in files if file["kind"] == kind]
        order = after_what_they_use(starts, lambda path: by_path[path]["uses"], lambda path: path in by_path)
        return list(dict.fromkeys(order + [file["path"] for file in files if file["path"] not in order]))


class InterfaceReviewer(Phase):
    """3. Review: each file that uses other code files, against their contracts. Contracts are
    written callee first, so a file should already match the files it uses; what does not is
    corrected now, before tests or code exist. A problem named without a correction gets that
    file's contract written again; files that changed are checked once more. One call per such
    file, more when the contracts it uses do not fit one prompt whole."""
    TITLE = "Plan: review (each file against the contracts it uses)"

    def run(self, cycle):
        planned = [path for path in cycle["files"] if self.meets_two_contracts(path)]
        if not planned:
            return
        problems, changed = self.check_round(cycle, planned, again=False)
        if changed:
            again = [path for path in planned if path in changed or set(self.callees(path)) & changed]
            problems += self.check_round(cycle, again, again=True)[0]
        cycle["files"] = Designer.order([dict(self.project.files[path], path=path)
                                         for path in cycle["files"] if path in self.project.files])
        cycle["review"] = [f"{problem['file']}: {problem['problem']}" if problem["file"] else problem["problem"]
                           for problem in problems]
        self.say_review(cycle["review"], changed)
        self.session.save()

    def meets_two_contracts(self, path):
        """A file with a contract that uses two or more files with contracts: where the plan may disagree."""
        return bool(self.project.files.get(path, {}).get("contract")) \
            and len([callee for callee in self.callees(path) if self.project.files[callee].get("contract")]) >= 2

    def callees(self, path):
        """The code files it uses (not itself)."""
        uses = self.project.files[path].get("uses", [])
        return [used for used in uses if used != path and self.project.kind_of(used) == "code"]

    def check_round(self, cycle, paths, again):
        """(problems, the files whose contracts changed)."""
        calls = [(path, chunk) for path in paths for chunk in self.chunks(path)]
        found, changed = [], set()
        for number, (path, callees) in enumerate(calls, 1):
            label = f"Interface check {'again ' if again else ''}{number}/{len(calls)} ({path})"
            problems, corrections = self.check_file(cycle, path, callees, label)
            found += problems
            changed |= self.apply_corrections(cycle, corrections)
        return found, changed | self.rewrite_unresolved(cycle, found, changed)

    def chunks(self, path):
        """The files it uses, in groups whose contracts fit one prompt next to its own entry."""
        room = self.session.room(Prompts.INTERFACE_SYSTEM) - len(self.design_entry(path)) - 120
        return groups_fitting(self.callees(path), room, size=lambda callee: len(self.design_entry(callee)))

    def check_file(self, cycle, path, callees, label):
        parts = [Part("The file to check", self.design_entry(path), True, exact=True),
                 Part("The files it uses", "\n".join(self.design_entry(callee) for callee in callees), True, exact=True),
                 Part("External services: adapters and sample data", cycle.get("data", ""), atomic=True),
                 Part("Language", self.project.language)]
        try:
            return self.session.ask_until_usable(Prompts.INTERFACE_SYSTEM, parts, label, self.read, attempts=2,
                                                 max_reply=CHECK_MAX_TOKENS)
        except SystemExit as exc:
            say.warn(f"{exc} Going on with this part of the plan as it is.")
            return [], []

    def design_entry(self, path):
        info = self.project.files[path]
        return (f"- {path} [{info['kind']}] {info['role']}" + (f" (uses {', '.join(info['uses'])})" if info["uses"]
                                                               else "")
                + "".join(f"\n    - {line}" for line in info["contract"]))

    def read(self, reply):
        data = reply.json()
        problems = [problem for problem in map(self.problem, data.get("problems") or []) if problem]
        corrections = [self.corrected_file(item) for item in data.get("files") or [] if isinstance(item, dict)]
        return problems[:20], [correction for correction in corrections if correction]

    @staticmethod
    def problem(item):
        """{file, problem} of a problem the check names (a file of its own, or none); None for an empty one."""
        match item:
            case {"problem": problem} if str(problem).strip():
                file = str(item.get("file") or "").strip()
                return {"file": str(Path(file)) if file else "", "problem": str(problem).strip()}
            case str() if item.strip():
                return {"file": "", "problem": item.strip()}
        return None

    def corrected_file(self, item):
        """A file entry from the check, keeping what it does not restate; "uses" of unknown files dropped."""
        path = str(Path(str(item.get("path", "")).strip()))
        self.workspace.resolve(path, for_write=True)
        known = self.project.files.get(path, {})
        contract = item.get("contract")
        contract = [str(line) for line in contract] if isinstance(contract, list) and contract else known.get("contract",
                                                                                                              [])
        uses = [str(Path(used)) for used in item.get("uses", known.get("uses", [])) if isinstance(used, str)]
        kind = str(item.get("kind") or known.get("kind") or "code").strip().lower()
        return {"path": path, "kind": kind if kind in KINDS else known.get("kind", "code"),
                "role": str(item.get("role") or known.get("role", "")), "contract": contract,
                "uses": [used for used in uses if used in self.project.files or used == path]}

    def apply_corrections(self, cycle, corrections):
        """The corrected entries go into the design; returns the files that changed."""
        changed = set()
        for item in corrections:
            entry = {key: item[key] for key in ("kind", "role", "contract", "uses")}
            if self.project.files.get(item["path"]) != entry:
                changed.add(item["path"])
            self.project.files[item["path"]] = entry
            if item["path"] not in cycle["files"] and item["kind"] != "docs":
                cycle["files"].append(item["path"])  # a file the check adds is built with the others
        return changed

    def rewrite_unresolved(self, cycle, problems, corrected):
        """Problems named in a file without a correction: that file's contract is written again."""
        unresolved = {}
        for problem in problems:
            if problem["file"] not in corrected and self.project.kind_of(problem["file"]) == "code":
                unresolved.setdefault(problem["file"], []).append(problem["problem"])
        writer, changed = ContractWriter(self.session), set()
        for path, items in unresolved.items():
            say.step(f"{path}: {len(items)} problem(s) named without a correction; writing its contract again", "✍️")
            if writer.write(cycle, path, problems=items):
                changed.add(path)
        return changed

    @staticmethod
    def say_review(review, changed):
        corrected = f"; {len(changed)} file(s) corrected" if changed else ""
        say.info(f"Interface check: {len(review)} problem(s) found{corrected}", "🔍")
        with say.section():
            for line in review[:10]:
                say.item(first_line(line, 160))


class ContractWriter(Worker):
    """A code file's contract: the exact names, signatures, data shapes and strings its criteria's
    tests check and the files using it rely on, and for each template it renders, what it gives it
    and what the page must show. Written after the code it uses (whose contracts it sees whole).
    Criteria that do not fit one prompt are given over several calls, each extending the contract
    so far."""

    def write_all(self, cycle):
        """The contract of each code file where files meet (needed); the others have none: their
        tests say what they do."""
        done = cycle.setdefault("contracts_done", [])
        for path in self.order(self.project.files, cycle["files"]):
            if path not in done:
                if self.needed(cycle, path):
                    self.write(cycle, path)
                done.append(path)
                self.session.save()

    @staticmethod
    def order(files, paths):
        """The code files' contracts, each after the code it imports or calls (whose contracts it
        then sees exactly). A template has none: the file rendering it says what it is given and
        what the page must show."""
        is_code = lambda path: files.get(path, {}).get("kind") == "code"
        uses = lambda path: [used for used in files[path].get("uses", []) if is_code(used)]
        wanted = set(paths)
        return [path for path in after_what_they_use(paths, uses, is_code) if path in wanted]

    def needed(self, cycle, path):
        """Whether two sides must agree on the file: another file uses it, or it renders a template
        (what the page is given). A file that is neither, with no criterion of its own and not the entry
        point, is a marker."""
        info = self.project.files[path]
        used = any(path in other.get("uses", []) for name, other in self.project.files.items()
                   if name != path and other.get("kind") in ("code", "template"))
        renders = any(self.project.kind_of(used) == "template" for used in info.get("uses", []))
        started = bool(self.project.entry_files([path]))  # the program a person starts is never a marker
        if not (used or renders or started) and not self.project.criteria_of(path, self.project.cycle_criteria(cycle)):
            info["marker"] = True
        return used or renders

    def write(self, cycle, path, problems=()):
        """Writes the file's contract (problems: what the review found in it). Returns whether it changed."""
        info = self.project.files[path]
        before = list(info.get("contract", []))
        criteria = [f"- {criterion['behaviour']} [{criterion['test']}]"
                    for criterion in self.project.criteria_of(path, self.project.cycle_criteria(cycle))]
        so_far = before if (problems or (cycle["id"] != "base" and self.workspace.exists(path))) else []
        written = self.written_in_parts(cycle, path, info, criteria, so_far, problems)
        if written is None:
            return False
        info["contract"], calls = written
        say.done(f"{path}: {len(info['contract'])} contract line(s)" + (f", from {calls} calls" if calls > 1 else ""))
        return info["contract"] != before

    def written_in_parts(self, cycle, path, info, criteria, so_far, problems):
        """(the contract, the calls it took): one call at least, more while criteria are left over, each
        extending the contract so far; None when one gives no usable contract (it stays as it is)."""
        calls = 0
        while calls == 0 or criteria:
            calls += 1
            parts, criteria = self.prompt(cycle, path, info, criteria, so_far, problems)
            label = f"Contract of {path}" + (f" (part {calls})" if calls > 1 or criteria else "")
            try:
                so_far, uses = self.session.ask_until_usable(Prompts.CONTRACT_SYSTEM, parts, label, self.read,
                                                             attempts=2)
            except SystemExit as exc:
                say.warn(f"{exc} Its contract stays as it is.")
                return None
            info["uses"] = list(dict.fromkeys(info.get("uses", []) + [used for used in uses
                                                                      if used in self.project.files and used != path]))
        return so_far, calls

    def prompt(self, cycle, path, info, criteria, so_far, problems):
        """(parts, the criteria left for the next call): the fixed parts whole, then as many
        criteria as fit."""
        base = self.base_parts(path, info, so_far, problems)
        room = self.session.room(Prompts.CONTRACT_SYSTEM) - Part.total_size(base) - 60
        if room < 200:
            say.warn(f"{path}: what its contract must use is larger than one prompt; it is shortened to fit")
            base = [Part(part.title, part.text, part.required, exact=False) for part in base]
        taken = groups_fitting(criteria, max(room, 200))[0] if criteria else []
        given = "\n".join(taken) or "none directly: it serves the files that use it"
        callers = [f"- {caller}: {caller_info.get('role', '')}" for caller, caller_info in self.project.files.items()
                   if path in caller_info.get("uses", [])]
        return base + [Part("Criteria its tests check", given, True, exact=True),
                       Part("Files that use it", "\n".join(callers), atomic=True),
                       Part("External services: adapters and sample data", cycle.get("data", ""), atomic=True,
                            place="first"),
                       Part("Request", asked(cycle), atomic=True, place="first"),
                       *self.session.knowledge_parts("design")], criteria[len(taken):]

    def base_parts(self, path, info, so_far, problems):
        uses = [used for used in info.get("uses", []) if self.project.kind_of(used) == "code"]
        templates = [used for used in info.get("uses", []) if self.project.kind_of(used) == "template"]
        given = Part("Contracts of the files it uses (use them exactly)", self.project.contracts(uses), bool(uses),
                     exact=True)
        deps = self.project.dependencies(lambda dependency: self.workspace.read(dependency)
                                         if self.workspace.exists(dependency) else "")
        return [Part("Language", self.project.language or "Python", True, place="first"),
                Part(Prompts.DEPENDENCIES_TITLE, deps, atomic=True, place="first"),
                Part("File", f"{path} ({info.get('kind', 'code')})\nRole: {info.get('role', '')}"
                     + (f"\nTemplates it renders: {', '.join(templates)}" if templates else ""), True, exact=True),
                given,
                Part("Its contract so far (keep what still holds; add what these criteria need)", "\n".join(so_far),
                     bool(so_far), exact=True),
                Part("Problems the plan review found in it (fix them)", "\n".join(f"- {problem}"
                                                                                  for problem in problems),
                     bool(problems), exact=True)]

    @staticmethod
    def read(reply):
        data = reply.json()
        contract = [str(line).strip() for line in data.get("contract", []) if str(line).strip()]
        if not contract:
            raise ValueError("no contract lines")
        return contract, [str(Path(used)) for used in data.get("uses", []) if isinstance(used, str)]


def asked(cycle):
    """What a cycle asks for: its request, and each change the person asked for while its spec waited
    for confirmation (tinker amend)."""
    return cycle["request"] + "".join(f"\nChanged by the person: {change}" for change in cycle.get("amendments", []))
