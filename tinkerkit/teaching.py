import re
from collections import namedtuple

from .messages import say
lazy from .checking import Check, found_programs
lazy from .graph import Graph
lazy from .knowledge import Item, Knowledge, Learner, Library, Retriever, retire, search_entries
lazy from .learning import DocCache
lazy from .passages import Cleaner
lazy from .project import ending
lazy from .prompts import Part, Prompts, Reply
lazy from .reading import Readings
lazy from .sandbox import Sandbox
lazy from .settings import (
    BOOK_PASSAGES_SHOWN,
    CHECK_SOURCE_CHARS,
    LESSON_PHASES,
    MEMORY_ITEMS_SHOWN,
    RECALL_OTHER_TOPIC_SIMILARITY,
    TEACH_CHECK_COMMANDS,
    TEACH_CHECK_SEARCHES,
    TEACH_CLAIMS,
)
lazy from .text import field, first_line, words

ITEM_KINDS = ("toolchain", "fix", "lesson", "fact", "finding", "rule", "command")
TEACHABLE_TOOLCHAIN_KEYS = ("setup", "check", "test_all", "test_one", "format", "lint")
VERDICTS = ("holds", "wrong", "unsure")
PREFERENCE_RE = re.compile(r"\bprefer", re.I)


JudgedChange = namedtuple("JudgedChange", "number change")
Checked = namedtuple("Checked", "evidence verdict")
Detail = namedtuple("Detail", "label value")
Follows = namedtuple("Follows", "follows why")


class Memory:
    def __init__(self, embeddings=None):
        graph = Graph.build()
        self.items = [
            node
            for node in graph.nodes.values()
            if node["kind"] in ITEM_KINDS and node["status"] != "retired"
        ]
        self.cited = self.cited_by_rules(graph)
        self.retriever = Retriever(embeddings)

    def cited_by_rules(self, graph):
        kept = {item["id"]: item for item in self.items}
        cited = {}
        for link in graph.links:
            if (
                link["kind"] == "cites"
                and graph.nodes[link["source"]]["kind"] == "rule"
                and link["target"] in kept
            ):
                cited.setdefault(link["source"], []).append(kept[link["target"]])
        return cited

    def about(self, text, limit=MEMORY_ITEMS_SHOWN):
        ranking = self.retriever.rank(text, [item["text"] for item in self.items])
        named = set(words(text))
        ranking.sort(key=lambda index: -self.named_in(self.items[index], named))
        ranked = [self.items[index] for index in ranking]
        return self.with_cited(self.on_named_topics(text, ranked, named))[:limit]

    def on_named_topics(self, text, ranked, named):
        topics = {item["group"] for item in ranked} & named
        if not topics:
            return ranked
        others = [item for item in ranked if item["group"] not in topics]
        similarity_of = self.retriever.similarities(text, [item["text"] for item in others]) or [0.0] * len(
            others
        )
        alike = [
            item
            for item, similarity in zip(others, similarity_of)
            if similarity >= RECALL_OTHER_TOPIC_SIMILARITY
        ]
        return [item for item in ranked if item["group"] in topics] + alike

    def with_cited(self, ranked):
        given = {}
        for item in ranked:
            for each in [item, *self.cited.get(item["id"], [])]:
                given.setdefault(each["id"], each)
        return list(given.values())

    @staticmethod
    def named_in(item, named):
        group = item["group"] in named
        kind = any(word.startswith(item["kind"]) for word in named) or (group and item["kind"] == "toolchain")
        return group + kind


def numbered(items):
    return "\n".join(item_line(number, item) for number, item in enumerate(items, 1))


def item_line(number, item):
    return f"[{number}] {item['kind']} ({item['group']}, {item['status'] or 'proven'}): {item['text']}"


def moved_on(tracker, phase, said, icon):
    say.step(said, icon)
    if tracker is not None:
        tracker.phase(phase)


class PassageSource:
    SHORTLIST_TIMES = 4

    def __init__(self, retriever):
        self.retriever = retriever

    @staticmethod
    def found(question, items) -> list:
        return []

    def passages(self, question, items, count=BOOK_PASSAGES_SHOWN):
        found = distinct(self.found(question, items))
        by_words = Retriever(None).by_words(question, [passage["text"] for passage in found])
        shortlist = [found[index] for index in by_words[: self.SHORTLIST_TIMES * count]]
        by_meaning = self.retriever.rank(question, [passage["text"] for passage in shortlist])
        return [shortlist[index] for index in by_meaning[:count]]


class ReadingPassages(PassageSource):
    @staticmethod
    def found(question, items):
        return Readings.chapter_passages(Readings.about(question, items))


class DocumentationPassages(PassageSource):
    @staticmethod
    def found(question, items):
        groups = dict.fromkeys(item["group"] for item in items)
        docs = dict.fromkeys(
            (slug, key)
            for group in groups
            for slug, key in ((Library.read(group) or {}).get("docs") or {}).items()
        )
        return [
            {"source": DocumentationPassages.source(slug, key, passage), "text": passage.text}
            for slug, key in docs
            for page in DocCache.pages(slug, key)
            for passage in Cleaner.under_headings(page)
        ]

    @staticmethod
    def source(slug, key, passage):
        return f"{slug}@{key}, {passage.url}" + (f" > {passage.heading}" if passage.heading else "")


class Asker:
    def __init__(self, model, memory, tracker=None, sandbox=None):
        self.model, self.memory, self.tracker, self.sandbox = model, memory, tracker, sandbox

    def answer(self, question):
        self.on_to("recall")
        with say.working_on(f"Looking through what Tinker keeps for: {first_line(question, 60)}"):
            items, passages = self.recalled(question)
        self.on_to("answer")
        read = f" and {len(passages)} passage(s) of what it read" if passages else ""
        parts = ask_parts(question, items, passages)
        with say.working_on(f"Answering from {len(items)} thing(s) Tinker keeps{read}"):
            answer, proposal = first_answer(self.model.ask(Prompts.ASK_SYSTEM, parts, "ask"))
        if proposal is None:
            return self.said(answer)
        return self.said(*(self.checked(parts, items, proposal) or (answer,)))

    def recalled(self, question):
        items = self.memory.about(question)
        passages = distinct(
            passage
            for source in (ReadingPassages, DocumentationPassages)
            for passage in source(self.memory.retriever).passages(question, items)
        )
        return items, passages

    def on_to(self, phase):
        if self.tracker is not None:
            self.tracker.phase(phase)

    def sandbox_here(self):
        return self.sandbox or Sandbox.here()

    def checked(self, parts, items, proposal):
        tools = found_programs(list(machines_here(items).values()))
        check = Check.of(proposal, tools, self.sandbox_here)
        refusal = check.refusal()
        if refusal:
            say.warn(f"Check refused: {refusal}")
            return None
        ran = check.run()
        if not ran.ran or ran.timed_out:
            say.warn(f"Check failed: {ran.why or 'it ran out of time'}")
            return None
        say.step(f"Ran a {proposal.get('kind')} check: {check.shows or first_line(check.code, 60)}")
        ran_part = Part("The check Tinker ran", check_text(check, ran), True)
        with say.working_on("Answering with what the check printed"):
            answer, confirmed = checked_answer(
                self.model.ask(Prompts.ASK_CHECKED_SYSTEM, [*parts, ran_part], "ask: checked")
            )
        return answer, Asker.fact_kept(confirmed, items, check, ran)

    @staticmethod
    def fact_kept(confirmed, items, check, ran):
        fact = str((confirmed or {}).get("fact") or "").strip()
        if not fact:
            return ""
        topic = confirmed.get("topic")
        sources = [cut(f"{check.proposal.get('kind')} check: {check.code}"), cut(f"It printed: {ran.output}")]
        shown = isinstance(topic, str) and topic in {item["group"] for item in items}
        kept = Library.checked_fact(topic, fact, sources) if shown else None
        if kept is None:
            say.skip(f"No book or topic to keep it in: {first_line(fact, 80)}")
            return ""
        return f"Fact kept for {(Library.read(topic) or {}).get('name', topic)}: {kept['text']}"

    @staticmethod
    def said(answer, kept=""):
        for line in answer.splitlines():
            say.text(line)
        if kept:
            say.step(kept)
        return ending("done", "Answered", [("Answer", answer)] + ([("Kept", kept)] if kept else []))


class Teacher:
    def __init__(self, model, internet, memory, tracker=None):
        self.model, self.internet, self.memory, self.tracker = model, internet, memory, tracker
        self.learner = Learner(model, internet, None)
        self.learner.tracker = tracker
        self.readings = []

    def teach(self, teaching):
        items = self.recalled(teaching)
        plan = self.understood(teaching, items)
        evidence, verdict = self.checked(teaching, plan, items)
        return self.corrected(teaching, plan, items, evidence, verdict)

    def recalled(self, teaching):
        moved_on(self.tracker, "recall", "Recalling what Tinker keeps about it…", "🧠")
        items = self.memory.about(teaching)
        self.readings = Readings.about(teaching, items)
        return items

    def understood(self, teaching, items):
        moved_on(self.tracker, "understand", "Working out what to check…", "🤔")
        parts = [
            Part("What Tinker keeps", numbered(items) or "(nothing yet)", True),
            Part("What the person teaches", teaching, True),
        ]
        with say.working_on(
            f"Working out what to check in what you taught, against {len(items)} thing(s) Tinker keeps"
        ):
            plan = self.model.ask_until_usable(
                Prompts.TEACH_SYSTEM,
                parts,
                "teach: what to check",
                lambda reply: read_plan(reply.json(), len(items), marked(teaching), bool(self.readings)),
            ).needed(f"{self.model.name} could not work out what to check; nothing changed")
        with say.part("What you taught", "📝"):
            for claim in plan["claims"]:
                say.info(claim["text"], "👤" if claim["preference"] else "🔍")
        self.learner.outcome(f"{len(plan['claims'])} claim(s), {len(plan['changes'])} change(s) asked for")
        return plan

    def checked(self, teaching, plan, items):
        if not any(not claim["preference"] for claim in plan["claims"]):
            evidence, verdict = [], Verdict.unchecked(plan["claims"], "")
        else:
            evidence = (
                self.on_machine(plan["commands"])
                + self.on_web(plan["searches"])
                + self.in_readings(plan["claims"])
            )
            verdict = (
                self.judged(teaching, plan, items, evidence)
                if evidence
                else Verdict.unchecked(plan["claims"], "nothing could check it")
            )
        verdict.show()
        return Checked(evidence, verdict)

    def on_machine(self, commands):
        kept, dropped = Learner.safe_commands(commands)
        for command in dropped:
            say.skip(f"Skipped `{command}`: only commands that show what is installed are run")
        return [
            {
                "source": "this machine",
                "text": f"`{shown['command']}` → "
                + (
                    shown["output"]
                    if shown["found"]
                    else "not found here (a project's own environment may have it)"
                ),
            }
            for shown in self.learner.look_at_machine(kept)
        ]

    def on_web(self, searches):
        if not searches:
            return []
        moved_on(self.tracker, "search", "Searching the web for sources…", "🔎")
        with say.working_on(f"Reading the web: {len(searches)} search(es)"):
            pages = self.learner.web_pages(searches)
        if not pages:
            say.warn("No web page found")
        for page in pages:
            say.info(f"Read {page['title'] or page['url']}", "📖")
        self.learner.outcome(f"{len(pages)} page(s) read")
        return [{"source": page["url"], "text": f"{page['title']}\n{page['content']}"} for page in pages]

    def in_readings(self, claims):
        if not self.readings:
            return []
        found = [
            passage
            for claim in claims
            if not claim["preference"]
            for passage in Readings.passages(self.readings, claim["text"])
        ]
        found = list({passage["text"]: passage for passage in found}.values())
        say.info(f"Found {len(found)} passage(s) of what was read about it", "📖")
        return found

    def judged(self, teaching, plan, items, evidence):
        moved_on(self.tracker, "judge", "Weighing the evidence…", "⚖️")
        facts = sum(not claim["preference"] for claim in plan["claims"])
        with say.working_on(f"Weighing {len(evidence)} piece(s) of evidence on {facts} fact(s) you taught"):
            return self.model.ask_until_usable(
                Prompts.JUDGE_SYSTEM,
                self.judge_parts(teaching, plan, items, evidence),
                "teach: weigh the evidence",
                lambda reply: Verdict.read(reply.json(), plan, len(evidence)),
            ).needed(f"{self.model.name} could not weigh the evidence; nothing changed")

    @staticmethod
    def judge_parts(teaching, plan, items, evidence):
        return [
            Part("What the person teaches", teaching, True),
            Part("Facts", Teacher.numbered_claims(plan, preference=False), True),
            Part("The person's preferences", Teacher.numbered_claims(plan, preference=True)),
            Part("Changes", Teacher.judged_changes_text(plan, items), True),
            Part("Evidence", Teacher.numbered_evidence(evidence), True),
        ]

    @staticmethod
    def numbered_claims(plan, preference):
        return "\n".join(
            f"{number}. {claim['text']}"
            for number, claim in enumerate(plan["claims"], 1)
            if bool(claim["preference"]) == preference
        )

    @staticmethod
    def judged_changes_text(plan, items):
        return "\n\n".join(
            f"Change {number} (rests on claims {', '.join(map(str, change['claims']))}):\n"
            f"{change_text(change, plan['language'], items)}"
            for number, change in judged_changes(plan)
        )

    @staticmethod
    def numbered_evidence(evidence):
        return "\n\n".join(
            f"[{number}] {piece['source']}\n{piece['text']}" for number, piece in enumerate(evidence, 1)
        )

    def corrected(self, teaching, plan, items, evidence, verdict):
        moved_on(self.tracker, "correct", "Correcting what Tinker keeps…", "✏️")
        reason = f"corrected by what you taught: {first_line(teaching, 120)}"
        made, skipped = [], []
        for number, change in enumerate(plan["changes"], 1):
            why_not = verdict.why_not(number, change)
            if why_not:
                skipped.append(f"{change_summary(change)} ({why_not})")
            else:
                made += self.made(change, plan["language"], items, reason, verdict.sources(change, evidence))
        for line in made:
            say.done(line)
        for line in skipped:
            say.skip(f"Not changed: {line}")
        details = (
            verdict.details()
            + [("Changed", line) for line in made]
            + [("Not changed", line) for line in skipped]
        )
        if not made:
            say.stop("Nothing changed")
            return ending("blocked", "Nothing changed", details)
        return ending("done", f"Corrected: {len(made)} change(s)", details)

    def made(self, change, language, items, reason, sources):
        lines = self.retired([items[number - 1] for number in change["retire"]], reason)
        lines.append(self.toolchain_corrected(language, change["toolchain"]))
        lines.append(self.lesson_kept(language, change["lesson"], sources))
        lines.append(self.fact_kept(change["fact"], items, sources))
        return [line for line in lines if line]

    @staticmethod
    def retired(items, reason):
        return [
            f"Retired {item['kind']}: {first_line(item['text'], 100)}"
            for item in items
            if retire(Item.of(item["id"]), item["group"], reason, None)
        ]

    @staticmethod
    def toolchain_corrected(language, commands):
        if not commands:
            return ""
        if not language or not Knowledge(language).correct_toolchain(commands):
            say.skip(f"No toolchain kept for {language or 'that language'} to correct")
            return ""
        return f"Toolchain for {language}: " + ", ".join(
            f"{key} → {shown(command)}" for key, command in commands.items()
        )

    @staticmethod
    def lesson_kept(language, lesson, sources):
        if not lesson:
            return ""
        scope = "language" if language else "any"
        kept = Knowledge(language or None).taught_lesson(dict(lesson, scope=scope), sources)
        return f"Lesson kept for {language or 'any language'}: {kept['text']}" if kept else ""

    @staticmethod
    def fact_kept(fact, items, sources):
        if not fact:
            return ""
        about = Item.of(items[fact["about"] - 1]["id"])
        kept = (
            Library.taught_fact(about.topic, about.id, fact["text"], sources)
            if about.kind in Item.CLAIM_KINDS
            else None
        )
        if kept is None:
            say.skip(f"No book or topic to keep it in: {first_line(fact['text'], 80)}")
            return ""
        return f"Fact kept for {(Library.read(about.topic) or {}).get('name', about.topic)}: {kept['text']}"


def ask_parts(question, items, passages):
    return [
        Part("What Tinker keeps", numbered(items) or "(nothing)", True),
        Part("From what you read", quoted(passages)),
        Part("Installed here", installed_here(items)),
        Part("The question", question, True),
    ]


def distinct(passages):
    kept = {}
    for passage in passages:
        kept.setdefault(passage["text"].strip(), passage)
    return list(kept.values())


def quoted(passages):
    return "\n\n".join(f"[{passage['source']}]\n{passage['text']}" for passage in passages)


def installed_here(items):
    return "\n".join(
        f"{group}: {Library.machine_text(machine)}" for group, machine in machines_here(items).items()
    )


def machines_here(items):
    machines = {
        group: (Library.read(group) or {}).get("machine")
        for group in dict.fromkeys(item["group"] for item in items)
    }
    return {group: machine for group, machine in machines.items() if machine}


def first_answer(answer):
    data = answer_data(answer)
    if data is None:
        return answer.value.strip(), None
    check = data.get("check")
    return data["answer"].strip(), check if isinstance(check, dict) else None


def checked_answer(answer):
    data = answer_data(answer)
    if data is None:
        return answer.value.strip(), None
    return data["answer"].strip(), data if data.get("confirms") is True else None


def answer_data(answer):
    try:
        data = Reply(answer.value).json()
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("answer"), str) else None


def cut(text):
    return first_line(" ".join(str(text).split()), CHECK_SOURCE_CHARS)


def check_text(check, ran):
    lines = [
        f"Kind: {check.proposal.get('kind')}",
        f"Tool: {check.proposal.get('tool')}" if check.proposal.get("tool") else "",
        f"Code:\n{check.code}",
        f"What it shows: {check.shows}",
        f"Exit code: {ran.code}",
        f"It printed:\n{ran.output or '(nothing)'}",
    ]
    return "\n".join(line for line in lines if line)


def marked(teaching):
    return bool(PREFERENCE_RE.search(teaching))


def read_plan(data, count, preferences=True, readings=False):
    texts = lambda key, limit: [str(text).strip() for text in data.get(key) or [] if str(text).strip()][
        :limit
    ]
    claims = [
        dict(claim, preference=claim["preference"] and preferences)
        for claim in read_claims(data.get("claims"))
    ]
    plan = {
        "claims": claims,
        "commands": texts("commands", TEACH_CHECK_COMMANDS),
        "searches": search_entries(data.get("searches"))[:TEACH_CHECK_SEARCHES],
        "language": field(data, "language"),
        "changes": read_changes(data.get("changes"), len(claims), count),
    }
    if not claims:
        raise ValueError("no claims")
    if states_facts(claims) and not (plan["commands"] or plan["searches"] or readings):
        raise ValueError("no command or web search checks the facts")
    if not plan["changes"]:
        raise ValueError("no change: none rests on a claim and retires, replaces or keeps something")
    return plan


def states_facts(claims):
    return any(not claim["preference"] for claim in claims)


def read_claims(given):
    claims = []
    for claim in given if isinstance(given, list) else []:
        claim = claim if isinstance(claim, dict) else {"text": claim}
        text = field(claim, "text")
        if text:
            claims.append({"text": text, "preference": claim.get("preference") is True})
    return claims[:TEACH_CLAIMS]


def read_changes(given, claims, count):
    changes, keys = [], set()
    for change in given if isinstance(given, list) else []:
        read = read_change(change, claims, count) if isinstance(change, dict) else None
        if not read or not changes_something(read):
            continue
        twice = keys & set(read["toolchain"])
        if twice:
            raise ValueError(
                f"toolchain {', '.join(sorted(twice))} is changed twice: put it in one change that "
                f"rests on all its claims"
            )
        keys |= set(read["toolchain"])
        changes.append(read)
    return changes


def read_change(change, claims, count):
    read = {
        "claims": numbers(change.get("claims"), claims),
        "retire": numbers(change.get("retire"), count),
        "toolchain": read_toolchain(change.get("toolchain")),
        "lesson": read_lesson(change.get("lesson")),
        "fact": read_fact(change.get("fact"), count),
    }
    if read["fact"]:
        read["retire"] = sorted(set(read["retire"]) | {read["fact"]["about"]})
    return read


def changes_something(change):
    return change["claims"] and (change["retire"] or change["toolchain"] or change["lesson"])


def numbers(given, highest):
    return sorted({number for number in given or [] if isinstance(number, int) and 1 <= number <= highest})


def read_toolchain(given):
    if not given:
        return {}
    if not isinstance(given, dict):
        raise ValueError("toolchain must be an object of key: command")
    found = {}
    for key, value in given.items():
        if key not in TEACHABLE_TOOLCHAIN_KEYS:
            raise ValueError(
                f"toolchain {key} cannot be changed (only {', '.join(TEACHABLE_TOOLCHAIN_KEYS)})"
            )
        commands = command_list(key, value)
        if commands:
            found[key] = commands if key == "setup" else " && ".join(commands)
    return found


def command_list(key, value):
    if isinstance(value, list):
        commands = value
    elif isinstance(value, str):
        commands = value.split("&&") if key == "setup" else [value]
    else:
        raise ValueError(f"toolchain {key} must be a command written as text")
    return [str(command).strip() for command in commands if str(command).strip()]


def read_lesson(given):
    given = given if isinstance(given, dict) else {}
    text, phase = field(given, "text"), field(given, "phase").lower()
    return {"phase": phase, "text": text} if text and phase in LESSON_PHASES else None


def read_fact(given, count):
    given = given if isinstance(given, dict) else {}
    text, about = field(given, "text"), given.get("about")
    return {"about": about, "text": text} if text and type(about) is int and 1 <= about <= count else None


def judged_changes(plan):
    return [
        JudgedChange(number, change)
        for number, change in enumerate(plan["changes"], 1)
        if any(not plan["claims"][cited - 1]["preference"] for cited in change["claims"])
    ]


def change_text(change, language, items):
    lines = [f"Retire: {item_line(number, items[number - 1])}" for number in change["retire"]]
    lines += [
        f"Toolchain of {language}: {key} becomes `{shown(command)}`"
        for key, command in change["toolchain"].items()
    ]
    if change["lesson"]:
        lines.append(f"Keep the lesson ({change['lesson']['phase']}): {change['lesson']['text']}")
    if change["fact"]:
        lines.append(f"Keep the fact, in place of [{change['fact']['about']}]: {change['fact']['text']}")
    return "\n".join(lines)


def change_summary(change):
    if change["lesson"]:
        return first_line(change["lesson"]["text"], 100)
    if change["fact"]:
        return first_line(change["fact"]["text"], 100)
    if change["toolchain"]:
        return f"toolchain {', '.join(change['toolchain'])}"
    return f"retire {len(change['retire'])} item(s)"


def shown(command):
    return " && ".join(command) if isinstance(command, list) else command


class Verdict:
    def __init__(self, claims, follows):
        self.claims_by_number, self.follows_by_change = claims, follows

    @classmethod
    def unchecked(cls, claims, why):
        return cls(
            {
                number: {
                    "text": claim["text"],
                    "verdict": "preference" if claim["preference"] else "unsure",
                    "why": "" if claim["preference"] else why,
                    "evidence": [],
                }
                for number, claim in enumerate(claims, 1)
            },
            {},
        )

    @classmethod
    def read(cls, data, plan, evidence):
        verdict = cls.unchecked(plan["claims"], "")
        given = {item.get("claim"): item for item in data.get("claims") or [] if isinstance(item, dict)}
        for number, claim in verdict.claims_by_number.items():
            if claim["verdict"] == "preference":
                continue
            item = given.get(number)
            if item is None or item.get("verdict") not in VERDICTS:
                raise ValueError(f"claim {number} has no verdict (one of {', '.join(VERDICTS)})")
            cites = numbers(item.get("evidence"), evidence)
            holds_bare = item["verdict"] == "holds" and not cites
            claim.update(
                verdict="unsure" if holds_bare else item["verdict"],
                evidence=cites,
                why="no evidence cited" if holds_bare else field(item, "why"),
            )
        said = {item.get("change"): item for item in data.get("changes") or [] if isinstance(item, dict)}
        for number, _ in judged_changes(plan):
            if number not in said or not isinstance(said[number].get("follows"), bool):
                raise ValueError(f'change {number} has no "follows" (true or false)')
            verdict.follows_by_change[number] = Follows(said[number]["follows"], field(said[number], "why"))
        return verdict

    def why_not(self, number, change):
        for claim_number in change["claims"]:
            if self.claims_by_number[claim_number]["verdict"] not in ("holds", "preference"):
                return f"claim {claim_number} is {self.claims_by_number[claim_number]['verdict']}"
        follows, why = self.follows_by_change.get(number, Follows(True, ""))
        return "" if follows else f"does not follow: {why or 'no reason given'}"

    def sources(self, change, evidence):
        found = [
            evidence[cited - 1]["source"]
            for number in change["claims"]
            for cited in self.claims_by_number[number]["evidence"]
        ]
        if any(self.claims_by_number[number]["verdict"] == "preference" for number in change["claims"]):
            found.append("your preference")
        return list(dict.fromkeys(found))

    def show(self):
        for claim in self.claims_by_number.values():
            if claim["verdict"] == "preference":
                say.info(f"Your preference, honored: {claim['text']}", "👤")
                continue
            cited = f" [{', '.join(map(str, claim['evidence']))}]" if claim["evidence"] else ""
            line = f"{claim['verdict'].capitalize()}: {claim['text']} ({claim['why'] or 'no reason given'}){cited}"
            {"holds": say.done, "wrong": say.fail}.get(claim["verdict"], say.warn)(line)

    def details(self):
        return [
            Detail(
                "Your preference" if claim["verdict"] == "preference" else claim["verdict"].capitalize(),
                claim["text"] + (f" ({claim['why']})" if claim["why"] else ""),
            )
            for claim in self.claims_by_number.values()
        ]
