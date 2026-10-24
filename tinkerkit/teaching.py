import re
from collections import namedtuple

from .messages import say
lazy from .conversation import Research
lazy from .graph import Graph
lazy from .knowledge import Knowledge, Learner, Library, Retriever
lazy from .prompts import Part, Prompts
lazy from .reading import Readings
lazy from .settings import (
    LESSON_PHASES,
    MEMORY_ITEMS_SHOWN,
    TEACH_CHECK_COMMANDS,
    TEACH_CHECK_SEARCHES,
    TEACH_CLAIMS,
    first_line,
    words,
)

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
        self.items = [
            node
            for node in Graph.build().nodes.values()
            if node["kind"] in ITEM_KINDS and node["status"] != "retired"
        ]
        self.retriever = Retriever(embeddings)

    def about(self, text, limit=MEMORY_ITEMS_SHOWN):
        ranking = self.retriever.rank(text, [item["text"] for item in self.items])
        named = set(words(text))
        ranking.sort(key=lambda index: -self.named_in(self.items[index], named))
        return [self.items[index] for index in ranking[:limit]]

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


def ending(status, headline, details=()):
    return {"status": status, "headline": headline, "details": list(details)}


class Asker:
    def __init__(self, model, memory, tracker=None):
        self.model, self.memory, self.tracker = model, memory, tracker

    def answer(self, question):
        self.on_to("recall")
        with say.working_on(f"Looking through what Tinker keeps for: {first_line(question, 60)}"):
            items = self.memory.about(question)
            passages = Readings.passages(Readings.about(question, items), question)
        if not items and not passages:
            return self.said(
                "I have not learned anything yet: I learn from builds (tinker task), tinker learn and "
                "tinker read."
            )
        self.on_to("answer")
        read = f" and {len(passages)} passage(s) of what you gave it to read" if passages else ""
        with say.working_on(f"Answering from {len(items)} thing(s) Tinker keeps{read}"):
            return self.said(self.asked(question, items, passages))

    def on_to(self, phase):
        if self.tracker is not None:
            self.tracker.phase(phase)

    def asked(self, question, items, passages):
        parts = [
            Part("What Tinker keeps", numbered(items) or "(nothing)", True),
            Part("From what you read", quoted(passages)),
            Part("The question", question, True),
        ]
        return self.model.ask(Prompts.ASK_SYSTEM, parts, "ask").strip()

    @staticmethod
    def said(answer):
        for line in answer.splitlines():
            say.text(line)
        return ending("done", "Answered", [("Answer", answer)])


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
            )
        if plan is None:
            raise SystemExit(f"{self.model.name} could not work out what to check; nothing changed")
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
            pages = Research(None).read(self.internet, searches)
        if not pages:
            say.warn(f"No web page found: {self.internet.problem()}")
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
        claims = list(enumerate(plan["claims"], 1))
        parts = [
            Part("What the person teaches", teaching, True),
            Part(
                "Facts",
                "\n".join(
                    f"{number}. {claim['text']}" for number, claim in claims if not claim["preference"]
                ),
                True,
            ),
            Part(
                "The person's preferences",
                "\n".join(f"{number}. {claim['text']}" for number, claim in claims if claim["preference"]),
            ),
            Part(
                "Changes",
                "\n\n".join(
                    f"Change {number} "
                    f"(rests on claims {', '.join(map(str, change['claims']))}):\n"
                    f"{change_text(change, plan['language'], items)}"
                    for number, change in judged_changes(plan)
                ),
                True,
            ),
            Part(
                "Evidence",
                "\n\n".join(
                    f"[{number}] {piece['source']}\n{piece['text']}"
                    for number, piece in enumerate(evidence, 1)
                ),
                True,
            ),
        ]
        facts = sum(not claim["preference"] for claim in plan["claims"])
        with say.working_on(f"Weighing {len(evidence)} piece(s) of evidence on {facts} fact(s) you taught"):
            verdict = self.model.ask_until_usable(
                Prompts.JUDGE_SYSTEM,
                parts,
                "teach: weigh the evidence",
                lambda reply: Verdict.read(reply.json(), plan, len(evidence)),
            )
        if verdict is None:
            raise SystemExit(f"{self.model.name} could not weigh the evidence; nothing changed")
        return verdict

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
            if retire_item(item, reason)
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
        kind, _, rest = items[fact["about"] - 1]["id"].partition(":")
        slug, _, claim_id = rest.rpartition(":")
        kept = (
            Library.taught_fact(slug, claim_id, fact["text"], sources)
            if kind in ("fact", "rule", "finding")
            else None
        )
        if kept is None:
            say.skip(f"No book or topic to keep it in: {first_line(fact['text'], 80)}")
            return ""
        return f"Fact kept for {(Library.read(slug) or {}).get('name', slug)}: {kept['text']}"


def quoted(passages):
    return "\n\n".join(f"[{passage['source']}]\n{passage['text']}" for passage in passages)


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
        "searches": texts("searches", TEACH_CHECK_SEARCHES),
        "language": str(data.get("language") or "").strip(),
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
        text = str(claim.get("text") or "").strip()
        if text:
            claims.append({"text": text, "preference": claim.get("preference") is True})
    return claims[:TEACH_CLAIMS]


def read_changes(given, claims, count):
    changes, keys = [], set()
    for change in given if isinstance(given, list) else []:
        if not isinstance(change, dict):
            continue
        read = {
            "claims": numbers(change.get("claims"), claims),
            "retire": numbers(change.get("retire"), count),
            "toolchain": read_toolchain(change.get("toolchain")),
            "lesson": read_lesson(change.get("lesson")),
            "fact": read_fact(change.get("fact"), count),
        }
        if read["fact"]:
            read["retire"] = sorted(set(read["retire"]) | {read["fact"]["about"]})
        if not changes_something(read):
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
    text, phase = str(given.get("text") or "").strip(), str(given.get("phase") or "").strip().lower()
    return {"phase": phase, "text": text} if text and phase in LESSON_PHASES else None


def read_fact(given, count):
    given = given if isinstance(given, dict) else {}
    text, about = str(given.get("text") or "").strip(), given.get("about")
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


def retire_item(item, reason):
    kind, _, rest = item["id"].partition(":")
    if kind in ("fact", "finding", "rule"):
        slug, _, claim_id = rest.rpartition(":")
        return Library.retire(slug, {claim_id}, reason, None) > 0
    if kind in ("lesson", "known", "fix"):
        key = item["id"] if kind != "fix" else f"fix:{rest.partition(':')[2]}"
        return Knowledge(item["group"]).retire_item(key, reason)
    return False


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
                why="no evidence cited" if holds_bare else str(item.get("why") or "").strip(),
            )
        said = {item.get("change"): item for item in data.get("changes") or [] if isinstance(item, dict)}
        for number, _ in judged_changes(plan):
            if number not in said or not isinstance(said[number].get("follows"), bool):
                raise ValueError(f'change {number} has no "follows" (true or false)')
            verdict.follows_by_change[number] = Follows(
                said[number]["follows"], str(said[number].get("why") or "").strip()
            )
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
