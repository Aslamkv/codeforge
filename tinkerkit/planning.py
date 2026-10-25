import ast
import operator
import re
from collections import namedtuple
lazy import json
lazy import math
lazy from pathlib import Path

from .messages import say
from .session import Phase, Worker
lazy from .knowledge import Embeddings, Library, Retriever, mentions
lazy from .learning import SlotLearner
lazy from .model import OllamaError
lazy from .prompts import Part, Prompts, PromptTooLarge
lazy from .settings import (
    CHECK_MAX_TOKENS,
    ENV_EXAMPLE,
    ENV_FILE,
    FACT_PHASES,
    KINDS,
    MAX_RECALL_TOPICS,
    TOPIC_RELEVANT_SIMILARITY,
    Config,
    after_what_they_use,
    field,
    first_line,
    groups_fitting,
    words,
)
lazy from .workspace import TestRef, Toolchain

RecallChoice = namedtuple("RecallChoice", "use learn")
CheckRound = namedtuple("CheckRound", "problems changed")
WrittenContract = namedtuple("WrittenContract", "contract calls")
ContractPrompt = namedtuple("ContractPrompt", "parts left")


class Recaller(Phase):
    TITLE = "Recall (what Tinker has learned)"

    def run(self, cycle):
        known, internet = Library.topics(), self.session.internet
        can_learn = Config.get("learn_missing") if internet and internet.available() else 0
        if not known and not can_learn:
            say.info(
                "Nothing learned yet" + ("" if internet else " (with --web, a run learns what it needs)"),
                "📭",
            )
            cycle["topics"] = []
            return
        query = cycle["request"] + (f"\n{cycle['brief']}" if cycle.get("brief") else "")
        retriever = Retriever(Embeddings.configured())
        if can_learn:
            use, learn = self.ask(cycle, query, known, self.ranked_topics(known, query, retriever), can_learn)
        else:
            use, learn = self.relevant(known, query), []
        learned = [
            slug for slug in (self.learn(topic, internet, retriever) for topic in learn[:can_learn]) if slug
        ]
        just_learned_first = list(dict.fromkeys(learned + use))
        cycle["topics"] = just_learned_first[:MAX_RECALL_TOPICS]
        self.keep_facts(cycle, query, retriever)
        self.say_used(cycle)
        self.use_language(cycle)
        self.session.save()

    @staticmethod
    def say_used(cycle):
        names = [(Library.read(slug) or {}).get("name", slug) for slug in cycle["topics"]]
        say.info(f"Using what was learned about: {', '.join(names) or 'nothing'}", "🧠")

    @staticmethod
    def ranked_topics(known, query, retriever):
        items = Library.items(known)
        order = retriever.rank(query, [Library.searched(item) for item in items], prune=True)
        if known:
            say.info(
                f"Searched {len(items)} learned texts of {len(known)} topic(s) by {retriever.used}", "🔍"
            )
        return list(dict.fromkeys(items[index]["topic"] for index in order))

    def ask(self, cycle, query, known, ranked, can_learn):
        parts = self.recall_parts(cycle, query, known, ranked, can_learn)
        try:
            return self.session.ask_until_usable(
                Prompts.RECALL_SYSTEM,
                parts,
                "Recall",
                lambda reply: self.read_choice(reply, known),
                attempts=2,
            )
        except (SystemExit, OllamaError) as exc:
            say.warn(f"{exc} Going on without what was learned.")
            return RecallChoice([], [])

    def recall_parts(self, cycle, query, known, ranked, can_learn):
        fixed = [
            Part("Request", query, True, keep="head"),
            Part("Project language", self.project.language if cycle["id"] != "base" else ""),
            Part(
                "Learning now",
                f"possible: name up to {can_learn} topics to learn"
                if can_learn
                else "not possible: name none",
                True,
            ),
        ]
        left = self.session.room(Prompts.RECALL_SYSTEM) - 60
        topics_room = max(
            left - Part.total_size(fixed), (left - sum(part.floor_size() for part in fixed)) // 2
        )
        return fixed + [
            Part(
                "Learned topics",
                self.topic_listing(known, ranked, topics_room) or "(none yet)",
                True,
                exact=True,
            )
        ]

    @staticmethod
    def topic_listing(known, ranked, room):
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
        data = reply.json()
        use = [str(slug).strip() for slug in data.get("use", []) if str(slug).strip() in known]
        learn = [" ".join(str(topic).split())[:80] for topic in data.get("learn", []) if str(topic).strip()]
        learn = {
            Library.slug(topic): topic
            for topic in reversed(learn)
            if Library.slug(topic) and Library.slug(topic) not in known
        }
        return RecallChoice(use, list(reversed(learn.values())))

    @staticmethod
    def relevant(known, query):
        text = query.lower()
        named = [
            slug
            for slug, entry in known.items()
            if any(
                mentions(text, name)
                for name in (str(entry.get("name") or slug), entry.get("language") or slug)
            )
        ]
        embeddings = Embeddings.configured()
        about = [f"{entry.get('name') or slug}: {entry.get('summary', '')}" for slug, entry in known.items()]
        vectors = embeddings.vectors([query] + about, keep=False) if embeddings and known else None
        close = [
            slug
            for slug, vector in zip(known, (vectors or [])[1:])
            if Embeddings.cosine(vectors[0], vector) >= TOPIC_RELEVANT_SIMILARITY
        ]
        return list(dict.fromkeys(named + close))

    def learn(self, topic, internet, retriever):
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
        chosen = {slug: Library.read(slug) for slug in cycle["topics"]}
        items = [
            item
            for item in Library.items({slug: entry for slug, entry in chosen.items() if entry})
            if item["phase"] != "summary"
        ]
        order = retriever.rank(query, [Library.searched(item) for item in items])
        ranked = self.essentials_first([items[index] for index in order])
        earlier = [
            item for item in self.project.spec.get("learned", []) if item["topic"] not in cycle["topics"]
        ]
        self.project.spec["learned"] = ranked + earlier
        self.keep_out_inapplicable()

    def keep_out_inapplicable(self):
        known = self.known_rules()
        left_out = self.for_other_code(known) | self.needing_tools_missing_here(known)
        self.project.spec["inapplicable"] = sorted(left_out)
        if left_out:
            say.skip(f"Left out {len(left_out)} learned rule(s) or fact(s) this project does not need")

    def known_rules(self):
        return self.project.spec.get("learned", []) + (
            self.session.knowledge.facts(FACT_PHASES) if self.project.language else []
        )

    def situations(self):
        return list(dict.fromkeys(item["when"] for item in self.known_rules() if item.get("when")))

    def for_other_code(self, known):
        has = set(self.project.spec.get("situations", []))
        return {
            item["id"] for item in known if item.get("when") and item.get("id") and item["when"] not in has
        }

    def needing_tools_missing_here(self, known):
        missing = self.session.knowledge.unavailable_tools()
        return {
            item["id"]
            for item in known
            if item.get("id") and any(mentions(item["text"], name) for name in missing)
        }

    @staticmethod
    def essentials_first(ranked):
        order = lambda item: (
            0 if item["phase"] in ("tools", "machine") else 1 if item.get("kind") == "rule" else 2
        )
        return sorted(ranked, key=order)

    def use_language(self, cycle):
        slug = Library.language(self.project.language)
        if not slug or slug in cycle.get("topics", []):
            return
        cycle["topics"] = [slug] + cycle.get("topics", [])[: MAX_RECALL_TOPICS - 1]
        query = cycle["request"] + (f"\n{cycle['brief']}" if cycle.get("brief") else "")
        self.keep_facts(cycle, query, Retriever(Embeddings.configured()))
        say.info(f"Using the language as learned: {Library.read(slug).get('name', slug)}", "🧠")
        self.session.save()


class SpecWriter(Phase):
    TITLE = "Plan: the spec"

    def run(self, cycle):
        if cycle.get("specified"):
            return
        system, parts = self.spec_prompt(cycle)
        think = self.thinking("low") if cycle.get("confirm") else None
        self.take(
            cycle,
            self.session.ask_until_usable(
                system, parts, "Spec", lambda reply: self.read_spec(reply, cycle), think=think
            ),
        )

    def spec_prompt(self, cycle):
        self.listed = Recaller(self.session).situations()
        system, parts = self.plan_prompt(cycle)
        return system, parts + [self.situations_part()]

    def situations_part(self):
        listing = "\n".join(f"{number}. {when}" for number, when in enumerate(self.listed, 1))
        return Part(
            'Code some learned rules are for: add "situations": [the numbers of those this project\'s code '
            "will have] to the reply",
            listing,
            atomic=True,
        )

    def plan_prompt(self, cycle):
        if cycle["id"] == "base":
            learned = self.session.knowledge.toolchain()
            return Prompts.SPEC_SYSTEM, [
                Part("Request", asked(cycle), True),
                Part("Requirements from the spec file", cycle.get("brief", "")),
                Part(
                    self.session.knowledge.toolchain_title(
                        f"reuse it when the project is in {self.session.knowledge.language}: "
                        f"change only what names that project's files, and give run for this project's entry point "
                        f"when it has one"
                    ),
                    json.dumps(learned.to_dict()) if learned else "",
                    atomic=True,
                    place="first",
                ),
                *self.session.knowledge_parts("spec", "design", "config", "toolchain"),
            ]
        return Prompts.FEATURE_SPEC_SYSTEM, [
            Part("Change requested", asked(cycle), True, keep="tail"),
            Part("Project", self.project.run["task"], True),
            Part("Language", self.project.language),
            Part("Feature requirements", cycle.get("brief", "")),
            Part(
                "What it does now (passing criteria)",
                "\n".join(
                    f"- {criterion['behaviour']}"
                    for criterion in self.project.criteria
                    if criterion["status"] == "green"
                ),
                keep="tail",
            ),
            Part("Project files", self.project.outline()),
            *self.session.knowledge_parts("spec", "design"),
        ]

    def thinking(self, level):
        think = self.session.model.think
        if think in (None, False):
            return think
        return False if level == "none" else level if isinstance(think, str) else think

    def read_spec(self, reply, cycle):
        data = reply.spec_json()
        criteria = self.criteria_from(data.get("criteria"), cycle)
        if not criteria:
            raise ValueError("no criteria listed (or none quotes the request)")
        self.send_back_wrong_working(criteria)
        self.place_tests(criteria, data.get("files") or [])
        files = self.without_unneeded_settings(
            self.planned_files(
                data.get("files") or [], {TestRef(criterion["test"]).file for criterion in criteria}
            ),
            criteria,
        )
        entry = self.planned_entry(data.get("entry"), files)
        if cycle["id"] == "base" and entry:
            self.send_back_unrun_program(criteria, entry, data.get("toolchain"))
        toolchain = None
        if cycle["id"] == "base" or not self.project.toolchain:
            given = data.get("toolchain")
            if not isinstance(given, dict):
                raise ValueError("it needs the toolchain")
            toolchain = Designer.read_toolchain_data(given)
        language = field(data, "language") or "Python"
        return {
            "language": language,
            "criteria": criteria,
            "files": list(files.values()),
            "entry": entry,
            "toolchain": toolchain,
            "situations": self.chosen_situations(data.get("situations")),
            "data": data,
        }

    def chosen_situations(self, numbers):
        listed = getattr(self, "listed", [])
        return [
            listed[number - 1]
            for number in numbers or []
            if isinstance(number, int) and 1 <= number <= len(listed)
        ]

    def criteria_from(self, items, cycle, taken=()):
        request = asked(cycle) + "\n" + cycle.get("brief", "")
        out = []
        for item in items if isinstance(items, list) else []:
            text = (
                str(item.get("text") or item.get("behaviour") or "").strip() if isinstance(item, dict) else ""
            )
            if not text:
                continue
            source = field(item, "source")
            if not self.traced(source, request):
                say.skip(
                    f"Dropped a criterion the request does not ask for: {first_line(text, 90)} "
                    f"(source: {first_line(source, 60) or 'none'})"
                )
                continue
            out.append(
                self.project.new_criterion(
                    "C",
                    text[:80],
                    text,
                    self.test_id(item.get("test"), text, list(taken) + out),
                    cycle["id"],
                    source,
                    "todo",
                    self.effort(item.get("effort")),
                )
            )
        return out

    @staticmethod
    def traced(source, request):
        if source.strip().lower().strip('"') == "setting":
            return True
        quoted = re.sub(r"\([^)]*\)", " ", source)
        wanted = [word for word in words(quoted) if len(word) >= 3]
        have = set(words(request))
        return bool(wanted) and sum(word in have for word in wanted) / len(wanted) >= 0.6

    def test_id(self, given, text, siblings):
        ref = TestRef(str(given or "").strip())
        if not ref.name:
            default_file = (
                TestRef(self.project.criteria[0]["test"]).file if self.project.criteria else "tests/test_app"
            )
            ref = TestRef(
                f"{ref.file if ref.file and ref.file != str(given) else default_file}::"
                f"test_{'_'.join(words(text)[:5])}"
            )
        taken = {criterion["test"] for criterion in self.project.criteria + siblings}
        return str(ref) if str(ref) not in taken else f"{ref}_{len(taken) + 1}"

    @staticmethod
    def effort(value):
        try:
            return min(max(int(value), 1), 3)
        except TypeError, ValueError:
            return 2

    def send_back_wrong_working(self, criteria):
        wrong = [
            f'in "{criterion["behaviour"]}" the working {mistake}'
            for criterion in criteria
            for mistake in Working.mistakes(criterion["behaviour"])
        ]
        self.sent_back = getattr(self, "sent_back", 0) + bool(wrong)
        if wrong and self.sent_back <= 2:
            raise ValueError(
                "; ".join(wrong) + ". Give the same spec again with only these values corrected (the "
                "working's own result, and what the criterion shows from it); every other criterion "
                "and file stays as it was"
            )

    def place_tests(self, criteria, listed):
        listed_tests = [
            str(Path(field(item, "path")))
            for item in listed
            if isinstance(item, dict) and field(item, "kind").lower() == "test"
        ]
        listed_code = {
            str(Path(field(item, "path")))
            for item in listed
            if isinstance(item, dict) and str(item.get("kind") or "code").strip().lower() == "code"
        }
        for criterion in criteria:
            ref = TestRef(criterion["test"])
            ref = TestRef(f"{self.listed_test_file(ref.file, listed_tests)}::{ref.name}")
            if self.outside_a_test_file(ref, listed_code):
                raise ValueError(
                    f"test {criterion['test']} is not in a test file of its own: write it as "
                    f"<test file path>::<test name> (e.g. tests/test_app.py::test_add), and put the "
                    f"code it calls where that test file can reach it"
                )
            criterion["test"] = str(ref)

    @staticmethod
    def outside_a_test_file(ref, listed_code):
        return "::" in ref.file or not Path(ref.file).suffix or ref.file in listed_code

    def listed_test_file(self, file, listed_tests):
        if file in listed_tests or file in self.project.files or not listed_tests:
            return file
        same_stem = [path for path in listed_tests if Path(path).stem == Path(file).stem]
        return same_stem[0] if same_stem else listed_tests[0] if len(listed_tests) == 1 else file

    def planned_files(self, listed, test_files):
        files = {}
        for item in listed:
            if isinstance(item, (dict, str)):
                entry = Designer.layout_file(
                    {"path": item} if isinstance(item, str) else item, test_files, self.workspace
                )
                files[entry["path"]] = entry
        for path in sorted(test_files - set(files)):
            files[path] = {"path": path, "kind": "test", "role": "tests", "contract": [], "uses": []}
        return files

    def without_unneeded_settings(self, files, criteria):
        if (
            ENV_EXAMPLE not in files
            or ENV_EXAMPLE in self.project.files
            or any(map(self.about_a_setting, criteria))
        ):
            return files
        say.skip(f"Left out {ENV_EXAMPLE}: no spec point is about a setting")
        return {path: entry for path, entry in files.items() if path != ENV_EXAMPLE}

    @staticmethod
    def about_a_setting(criterion):
        text = f"{criterion['source']} {criterion['behaviour']}".lower()
        return (
            criterion["source"].strip().lower() == "setting"
            or ENV_EXAMPLE in text
            or ENV_FILE in text
            or bool(re.search(r"\bsettings?\b", text))
        )

    def planned_entry(self, given, files):
        entry = str(Path(str(given or "").strip())) if str(given or "").strip() else ""
        named = [path for path in list(files) + list(self.project.files) if Path(path).name == entry]
        entry = named[0] if self.unplanned(entry, files) and len(set(named)) == 1 else entry
        if entry and self.unplanned(entry, files):
            raise ValueError(f"entry {entry} is not one of the files")
        return entry

    def unplanned(self, path, files):
        return path not in files and path not in self.project.files

    def send_back_unrun_program(self, criteria, entry, toolchain):
        run = str((toolchain or {}).get("run") or "") if isinstance(toolchain, dict) else ""
        unrun = not any(self.runs_program(criterion["behaviour"], entry, run) for criterion in criteria)
        self.sent_back = getattr(self, "sent_back", 0) + unrun
        if unrun and self.sent_back <= 2:
            raise ValueError(
                f"{entry} is a program a person runs, yet no criterion runs it: add its normal use "
                f"as a person runs it, with what it prints (through {entry}'s entry point or its "
                f"run command), next to the criteria already there"
            )

    @staticmethod
    def runs_program(text, entry, run):
        lower, stem = text.lower(), Path(entry).stem.lower()
        command = " ".join(run.split()[:2]).lower()
        return (
            "main(" in lower
            or f"{stem}(" in lower
            or f"{stem}." in lower
            and "main" in lower
            or bool(command)
            and command in lower
            or bool(
                re.prefixmatch(r"\s*`?[a-z][\w.-]*`?(?: [^\s(]+)*? (prints|outputs|shows|writes)\b", lower)
            )
            or bool(
                re.search(
                    r"\b(run|runs|running|ran|launch|launched|invoked?|executed?|stdin|stdout|"
                    r"standard (input|output)|exit code|exits)\b",
                    lower,
                )
            )
        )

    def take(self, cycle, plan):
        self.project.spec["situations"] = list(
            dict.fromkeys(self.project.spec.get("situations", []) + plan["situations"])
        )
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
        if entry and not self.project.toolchain.run:
            say.step(f"The toolchain does not say how to start {entry}: asking for it", "🤔")
            self.project.toolchain = Designer(self.session).ask_toolchain()

    def keep_scope(self, cycle, data):
        cycle["non_goals"] = self.strings(data.get("non_goals"), 12)
        cycle["constraints"] = self.strings(data.get("constraints"), 8)
        sample = data.get("data", "")
        cycle["data"] = sample if isinstance(sample, str) else json.dumps(sample)

    def say_planned(self, cycle, plan):
        starts = f"; a user starts {self.project.spec['entry']}" if self.project.spec.get("entry") else ""
        say.info(
            f"Language: {self.project.language}; {len(self.project.cycle_criteria(cycle))} criteria; files: "
            f"{', '.join(cycle['files'])}{starts}",
            "📋",
        )
        if plan["toolchain"]:
            say.info(f"Toolchain: {self.project.toolchain.summary()}", "🔧")
        say.info(f"Learned so far: {self.session.knowledge.summary()}", "🧠")

    def keep_files(self, cycle, files, entry):
        if cycle["id"] == "base" and not self.has_docs(files):
            files.append(
                {
                    "path": "README.md",
                    "kind": "docs",
                    "role": "how to set up, run and test it",
                    "contract": [],
                    "uses": [],
                }
            )
        paths = {file["path"] for file in files} | set(self.project.files)
        for file in files:
            file["uses"] = [used for used in file["uses"] if used in paths and used != file["path"]]
        Designer.give_templates_a_renderer(files, entry)
        for file in files:
            known = self.project.files.get(file["path"], {})
            self.project.files[file["path"]] = {
                "kind": file["kind"],
                "role": file["role"] or known.get("role", ""),
                "contract": known.get("contract", []),
                "uses": list(dict.fromkeys(known.get("uses", []) + file["uses"])),
            }
        if entry or cycle["id"] == "base":
            self.project.spec["entry"] = entry
        cycle["files"] = Designer.order([file for file in files if file["path"] != ENV_FILE])

    @staticmethod
    def has_docs(files):
        return any(file["kind"] == "docs" for file in files)

    @staticmethod
    def strings(items, limit):
        return [str(item) for item in (items or []) if str(item).strip()][:limit]

    def amend(self, cycle, change):
        before = self.project.cycle_criteria(cycle)
        current = self.current_spec(cycle, before)
        saved = (list(self.project.criteria), self.project.spec.get("counter", 0), dict(self.project.files))
        self.forget(cycle)
        cycle.setdefault("amendments", []).append(change)
        system, parts = self.spec_prompt(cycle)
        parts += [
            Part(
                "The spec as it is now: give it again whole, in the same JSON, changed only where the "
                "person's change asks (a criterion the change adds quotes the change as its source)",
                json.dumps(current),
                True,
                atomic=True,
            ),
            Part("The person's change", change, True),
        ]
        try:
            plan = self.session.ask_until_usable(
                system,
                parts,
                "Amend the spec",
                lambda reply: self.read_spec(reply, cycle),
                think=self.thinking("none"),
            )
        except BaseException:
            self.restore(cycle, saved)
            raise
        self.take(cycle, plan)
        cycle["changes"] = self.changes(before, self.project.cycle_criteria(cycle))
        cycle.update(phase="design", awaiting=False, confirmed=False, contracts_done=[])
        cycle.pop("review", None)
        self.session.save()

    def current_spec(self, cycle, criteria):
        files = [
            {
                "path": path,
                "kind": self.project.files[path]["kind"],
                "role": self.project.files[path].get("role", ""),
                "uses": self.project.files[path].get("uses", []),
            }
            for path in cycle.get("files", [])
            if path in self.project.files
        ]
        spec = {
            "language": self.project.language,
            "criteria": [
                {
                    "text": criterion["behaviour"],
                    "source": criterion.get("source", ""),
                    "test": criterion["test"],
                    "effort": criterion.get("effort", 2),
                }
                for criterion in criteria
            ],
            "files": files,
            "entry": self.project.spec.get("entry", ""),
            "non_goals": cycle.get("non_goals", []),
            "constraints": cycle.get("constraints", []),
            "data": cycle.get("data", ""),
        }
        if cycle["id"] == "base":
            spec["toolchain"] = self.project.toolchain.to_dict()
        return spec

    def forget(self, cycle):
        self.project.criteria[:] = [
            criterion for criterion in self.project.criteria if criterion.get("cycle") != cycle["id"]
        ]
        self.project.spec["counter"] = max(
            [
                int(number)
                for criterion in self.project.criteria
                for number in re.findall(r"\d+", criterion["id"])[:1]
            ]
            or [0]
        )
        for path in cycle.get("files", []):
            if not self.workspace.exists(path):
                self.project.files.pop(path, None)
        cycle["specified"] = False

    def restore(self, cycle, saved):
        self.project.criteria[:], self.project.spec["counter"] = saved[0], saved[1]
        self.project.files.clear()
        self.project.files.update(saved[2])
        cycle["amendments"].pop()
        cycle["specified"] = True

    @staticmethod
    def changes(before, after):
        was = {criterion["test"]: criterion["behaviour"] for criterion in before}
        now = {criterion["test"] for criterion in after}
        lines = [
            f"{'+' if criterion['test'] not in was else '~'} {criterion['id']} {criterion['behaviour']}"
            for criterion in after
            if was.get(criterion["test"]) != criterion["behaviour"]
        ]
        lines += [f"- {text}" for test, text in was.items() if test not in now]
        return lines or ["no spec point changed"]


class Working:
    ARITHMETIC = r"[\d.\s()+\-*/]"
    CHAIN_RE = re.compile(rf"{ARITHMETIC}+(?:={ARITHMETIC}+)+")
    SIGNS = {"×": "*", "÷": "/", "−": "-", "–": "-"}
    OPERATIONS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    @classmethod
    def mistakes(cls, text):
        plain = cls.plain(text)
        found = []
        for chain in cls.CHAIN_RE.finditer(plain):
            parts = [cls.balanced(part) for part in chain.group(0).split("=")]
            worked = [
                (part, value)
                for part, value in ((part, cls.value(part)) for part in parts)
                if value is not None
            ]
            if len(worked) < 2 or not any(cls.does_arithmetic(expression) for expression, _ in worked):
                continue
            cut_short = plain[chain.end() :].lstrip().startswith(("…", "..."))
            found += cls.wrong_parts(worked, cut_short)
        return found

    @staticmethod
    def does_arithmetic(expression):
        return re.search(r"[\d)]\s*[-+*/]\s*[\d(]", expression)

    @classmethod
    def plain(cls, text):
        for sign, same in cls.SIGNS.items():
            text = text.replace(sign, same)
        return re.sub(r"(?<=[\d)])\s*x\s*(?=[\d(])", " * ", text)

    @staticmethod
    def balanced(part):
        part = part.strip()
        while part.startswith("(") and part.count("(") > part.count(")"):
            part = part[1:].strip()
        while part.endswith(")") and part.count(")") > part.count("("):
            part = part[:-1].strip()
        return part

    @classmethod
    def value(cls, sum_):
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
        last, result = worked[-1]
        places = len(last.partition(".")[2]) if re.fullmatch(r"-?\d+(\.\d+)?", last) else 6
        found = []
        for part, value in worked[:-1]:
            if cut_short and math.trunc(value * 10**places + 1e-9) == round(result * 10**places):
                continue
            if cut_short or abs(value - result) > 0.5 * 10**-places + 1e-9:
                found.append(f"{part} is {round(value, max(places, 2) if places < 6 else 4):g}, not {last}")
        return found


class Designer(Phase):
    TITLE = "Plan: the design"

    def run(self, cycle):
        self.ensure_toolchain()
        ContractWriter(self.session).write_all(cycle)

    def ensure_toolchain(self):
        if not self.project.toolchain:
            self.project.toolchain = self.ask_toolchain()
            say.info(f"Toolchain: {self.project.toolchain.summary()}", "🔧")
            self.session.save()

    def ask_toolchain(self):
        learned, entry = self.session.knowledge.toolchain(), self.project.spec.get("entry", "")
        parts = [
            Part("Language", self.project.language or "Python", True),
            Part("Entry point (the file a user starts)", entry, bool(entry)),
            Part("Project files", self.project.outline(), True, exact=True),
            Part(
                self.session.knowledge.toolchain_title(
                    "reuse it; adapt only project names, and give run for this project's entry point"
                ),
                json.dumps(learned.to_dict()) if learned else "",
                atomic=True,
            ),
            *self.session.knowledge_parts("toolchain"),
        ]
        return self.session.ask_until_usable(
            Prompts.TOOLCHAIN_SYSTEM,
            parts,
            "Toolchain",
            lambda reply: self.read_toolchain_data(reply.json().get("toolchain", reply.json()), entry),
        )

    @staticmethod
    def read_toolchain_data(data, entry=""):
        toolchain = Toolchain(data if isinstance(data, dict) else {})
        if entry and not toolchain.run:
            raise ValueError(
                f"toolchain.run is empty, but a user starts {entry}: give the command that starts it"
            )
        for command in toolchain.env_commands_dropped:
            say.skip(f"Toolchain: left out `{command}`: Tinker makes {ENV_FILE} itself, from {ENV_EXAMPLE}")
        for command in toolchain.machine_changing_commands:
            say.skip(f"Toolchain: left out `{command}`: it changes the machine's own tools, not this project")
        problems = toolchain.problems()
        if problems:
            raise ValueError("; ".join(problems))
        return toolchain

    def drop_adapter_criteria(self, cycle):
        adapters = [
            path
            for path, info in self.project.files.items()
            if info.get("kind") == "code" and info.get("role", "").strip().lower().startswith("adapter")
        ]
        for step in list(self.project.cycle_criteria(cycle)):
            subject = step["behaviour"].lstrip("`'\" ").lower()
            if self.about_an_adapter(subject, adapters):
                self.project.criteria.remove(step)
                say.skip(
                    f"Dropped {step['id']}: it tests an adapter to an external service "
                    f"({first_line(step['behaviour'], 80)})"
                )

    @staticmethod
    def about_an_adapter(subject, adapters):
        return any(
            subject.startswith((Path(adapter).stem.lower() + ".", Path(adapter).name.lower()))
            for adapter in adapters
        )

    @staticmethod
    def layout_file(item, test_files, workspace):
        path = str(Path(field(item, "path")))
        workspace.resolve(path, for_write=True)
        kind = "test" if path in test_files else (field(item, "kind").lower() or "code")
        uses = item.get("uses", [])
        return {
            "path": path,
            "kind": kind if kind in KINDS else "code",
            "role": str(item.get("role") or item.get("purpose") or ""),
            "contract": [],
            "uses": [str(Path(used)) for used in uses if isinstance(used, str)]
            if isinstance(uses, list)
            else [],
        }

    @staticmethod
    def give_templates_a_renderer(files, entry):
        used = {*file["uses"] for file in files}
        code = [file for file in files if file["kind"] == "code"]
        for template in [file for file in files if file["kind"] == "template" and file["path"] not in used]:
            name = Path(template["path"]).name
            owner = next((file for file in code if name in file["role"]), None) or next(
                (file for file in code if file["path"] == entry), None
            )
            if owner:
                owner["uses"].append(template["path"])

    @staticmethod
    def order(files):
        by_path = {file["path"]: file for file in files}
        starts = [
            file["path"] for kind in ("config", "template", "code") for file in files if file["kind"] == kind
        ]
        order = after_what_they_use(starts, lambda path: by_path[path]["uses"], lambda path: path in by_path)
        return list(dict.fromkeys(order + [file["path"] for file in files if file["path"] not in order]))


class InterfaceReviewer(Phase):
    TITLE = "Plan: review (each file against the contracts it uses)"

    def run(self, cycle):
        planned = [path for path in cycle["files"] if self.meets_two_contracts(path)]
        if not planned:
            return
        problems, changed = self.check_round(cycle, planned, again=False)
        if changed:
            again = [path for path in planned if path in changed or set(self.callees(path)) & changed]
            problems += self.check_round(cycle, again, again=True)[0]
        cycle["files"] = Designer.order(
            [
                dict(self.project.files[path], path=path)
                for path in cycle["files"]
                if path in self.project.files
            ]
        )
        cycle["review"] = [
            f"{problem['file']}: {problem['problem']}" if problem["file"] else problem["problem"]
            for problem in problems
        ]
        self.say_review(cycle["review"], changed)
        self.session.save()

    def meets_two_contracts(self, path):
        return (
            bool(self.project.files.get(path, {}).get("contract"))
            and len([callee for callee in self.callees(path) if self.project.files[callee].get("contract")])
            >= 2
        )

    def callees(self, path):
        uses = self.project.files[path].get("uses", [])
        return [used for used in uses if used != path and self.project.kind_of(used) == "code"]

    def check_round(self, cycle, paths, again):
        calls = [(path, chunk) for path in paths for chunk in self.chunks(path)]
        found, changed = [], set()
        for number, (path, callees) in enumerate(calls, 1):
            label = f"Interface check {'again ' if again else ''}{number}/{len(calls)} ({path})"
            problems, corrections = self.check_file(cycle, path, callees, label)
            found += problems
            changed |= self.apply_corrections(cycle, corrections)
        return CheckRound(found, changed | self.rewrite_unresolved(cycle, found, changed))

    def chunks(self, path):
        room = self.session.room(Prompts.INTERFACE_SYSTEM) - len(self.design_entry(path)) - 120
        return groups_fitting(self.callees(path), room, size=lambda callee: len(self.design_entry(callee)))

    def check_file(self, cycle, path, callees, label):
        parts = [
            Part("The file to check", self.design_entry(path), True, exact=True),
            Part(
                "The files it uses",
                "\n".join(self.design_entry(callee) for callee in callees),
                True,
                exact=True,
            ),
            Part("External services: adapters and sample data", cycle.get("data", ""), atomic=True),
            Part("Language", self.project.language),
        ]
        try:
            return self.session.ask_until_usable(
                Prompts.INTERFACE_SYSTEM, parts, label, self.read, attempts=2, max_reply=CHECK_MAX_TOKENS
            )
        except SystemExit as exc:
            say.warn(f"{exc} Going on with this part of the plan as it is.")
            return [], []

    def design_entry(self, path):
        info = self.project.files[path]
        return (
            f"- {path} [{info['kind']}] {info['role']}"
            + (f" (uses {', '.join(info['uses'])})" if info["uses"] else "")
            + "".join(f"\n    - {line}" for line in info["contract"])
        )

    def read(self, reply):
        data = reply.json()
        problems = [problem for problem in map(self.problem, data.get("problems") or []) if problem]
        corrections = [
            self.corrected_file(item) for item in data.get("files") or [] if isinstance(item, dict)
        ]
        return problems[:20], [correction for correction in corrections if correction]

    @staticmethod
    def problem(item):
        match item:
            case {"problem": problem} if str(problem).strip():
                file = field(item, "file")
                return {"file": str(Path(file)) if file else "", "problem": str(problem).strip()}
            case str() if item.strip():
                return {"file": "", "problem": item.strip()}
        return None

    def corrected_file(self, item):
        path = str(Path(field(item, "path")))
        self.workspace.resolve(path, for_write=True)
        known = self.project.files.get(path, {})
        contract = item.get("contract")
        contract = (
            [str(line) for line in contract]
            if isinstance(contract, list) and contract
            else known.get("contract", [])
        )
        uses = [str(Path(used)) for used in item.get("uses", known.get("uses", [])) if isinstance(used, str)]
        kind = str(item.get("kind") or known.get("kind") or "code").strip().lower()
        return {
            "path": path,
            "kind": kind if kind in KINDS else known.get("kind", "code"),
            "role": str(item.get("role") or known.get("role", "")),
            "contract": contract,
            "uses": [used for used in uses if used in self.project.files or used == path],
        }

    def apply_corrections(self, cycle, corrections):
        changed = set()
        for item in corrections:
            entry = {key: item[key] for key in ("kind", "role", "contract", "uses")}
            if self.project.files.get(item["path"]) != entry:
                changed.add(item["path"])
            self.project.files[item["path"]] = entry
            if item["path"] not in cycle["files"] and item["kind"] != "docs":
                cycle["files"].append(item["path"])
        return changed

    def rewrite_unresolved(self, cycle, problems, corrected):
        unresolved = {}
        for problem in problems:
            if problem["file"] not in corrected and self.project.kind_of(problem["file"]) == "code":
                unresolved.setdefault(problem["file"], []).append(problem["problem"])
        writer, changed = ContractWriter(self.session), set()
        for path, items in unresolved.items():
            say.step(
                f"{path}: {len(items)} problem(s) named without a correction; writing its contract again", "✍️"
            )
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
    def write_all(self, cycle):
        done = cycle.setdefault("contracts_done", [])
        for path in self.order(self.project.files, cycle["files"]):
            if path not in done:
                if self.needed(cycle, path):
                    self.write(cycle, path)
                done.append(path)
                self.session.save()

    @staticmethod
    def order(files, paths):
        is_code = lambda path: files.get(path, {}).get("kind") == "code"
        uses = lambda path: [used for used in files[path].get("uses", []) if is_code(used)]
        wanted = set(paths)
        return [path for path in after_what_they_use(paths, uses, is_code) if path in wanted]

    def needed(self, cycle, path):
        info = self.project.files[path]
        used = any(
            path in other.get("uses", [])
            for name, other in self.project.files.items()
            if name != path and other.get("kind") in ("code", "template")
        )
        renders = any(self.project.kind_of(used) == "template" for used in info.get("uses", []))
        started_by_a_person = bool(self.project.entry_files([path]))
        if not (used or renders or started_by_a_person) and not self.project.criteria_of(
            path, self.project.cycle_criteria(cycle)
        ):
            info["marker"] = True
        return used or renders

    def write(self, cycle, path, problems=()):
        info = self.project.files[path]
        before = list(info.get("contract", []))
        criteria = [
            f"- {criterion['behaviour']} [{criterion['test']}]"
            for criterion in self.project.criteria_of(path, self.project.cycle_criteria(cycle))
        ]
        so_far = before if (problems or (cycle["id"] != "base" and self.workspace.exists(path))) else []
        written = self.written_in_parts(cycle, path, info, criteria, so_far, problems)
        if written is None:
            return False
        info["contract"], calls = written
        say.done(
            f"{path}: {len(info['contract'])} contract line(s)"
            + (f", from {calls} calls" if calls > 1 else "")
        )
        return info["contract"] != before

    def written_in_parts(self, cycle, path, info, criteria, so_far, problems):
        calls = 0
        while calls == 0 or criteria:
            calls += 1
            parts, criteria = self.prompt(cycle, path, info, criteria, so_far, problems)
            label = f"Contract of {path}" + (f" (part {calls})" if calls > 1 or criteria else "")
            try:
                so_far, uses = self.session.ask_until_usable(
                    Prompts.CONTRACT_SYSTEM, parts, label, self.read, attempts=2
                )
            except SystemExit as exc:
                say.warn(f"{exc} Its contract stays as it is.")
                return None
            info["uses"] = list(
                dict.fromkeys(
                    info.get("uses", [])
                    + [used for used in uses if used in self.project.files and used != path]
                )
            )
        return WrittenContract(so_far, calls)

    def prompt(self, cycle, path, info, criteria, so_far, problems):
        base = self.base_parts(path, info, so_far, problems)
        room = self.session.room(Prompts.CONTRACT_SYSTEM) - Part.total_size(base) - 60
        if room < 200:
            say.warn(f"{path}: what its contract must use is larger than one prompt; it is shortened to fit")
            base = [Part(part.title, part.text, part.required, exact=False) for part in base]
        taken = groups_fitting(criteria, max(room, 200))[0] if criteria else []
        given = "\n".join(taken) or "none directly: it serves the files that use it"
        callers = [
            f"- {caller}: {caller_info.get('role', '')}"
            for caller, caller_info in self.project.files.items()
            if path in caller_info.get("uses", [])
        ]
        return ContractPrompt(
            base
            + [
                Part("Criteria its tests check", given, True, exact=True),
                Part("Files that use it", "\n".join(callers), atomic=True),
                Part(
                    "External services: adapters and sample data",
                    cycle.get("data", ""),
                    atomic=True,
                    place="first",
                ),
                Part("Request", asked(cycle), atomic=True, place="first"),
                *self.session.knowledge_parts("design"),
            ],
            criteria[len(taken) :],
        )

    def base_parts(self, path, info, so_far, problems):
        uses = [used for used in info.get("uses", []) if self.project.kind_of(used) == "code"]
        templates = [used for used in info.get("uses", []) if self.project.kind_of(used) == "template"]
        given = Part(
            "Contracts of the files it uses (use them exactly)",
            self.project.contracts(uses),
            bool(uses),
            exact=True,
        )
        deps = self.project.dependencies(
            lambda dependency: self.workspace.read(dependency) if self.workspace.exists(dependency) else ""
        )
        return [
            Part("Language", self.project.language or "Python", True, place="first"),
            Part(Prompts.DEPENDENCIES_TITLE, deps, atomic=True, place="first"),
            Part(
                "File",
                f"{path} ({info.get('kind', 'code')})\nRole: {info.get('role', '')}"
                + (f"\nTemplates it renders: {', '.join(templates)}" if templates else ""),
                True,
                exact=True,
            ),
            given,
            Part(
                "Its contract so far (keep what still holds; add what these criteria need)",
                "\n".join(so_far),
                bool(so_far),
                exact=True,
            ),
            Part(
                "Problems the plan review found in it (fix them)",
                Part.bullets(problems),
                bool(problems),
                exact=True,
            ),
        ]

    @staticmethod
    def read(reply):
        data = reply.json()
        contract = [str(line).strip() for line in data.get("contract", []) if str(line).strip()]
        if not contract:
            raise ValueError("no contract lines")
        return contract, [str(Path(used)) for used in data.get("uses", []) if isinstance(used, str)]


def asked(cycle):
    return cycle["request"] + "".join(
        f"\nChanged by the person: {change}" for change in cycle.get("amendments", [])
    )
