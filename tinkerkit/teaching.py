"""Asking Tinker what it keeps (Asker), and teaching it (Teacher). Both read what Tinker keeps as one list of
items (Memory: each toolchain, fix, lesson, fact, finding, rule and command of the memory graph, the retired
left out), the most related first. An answer comes from those items alone, each claim citing its item. A
teaching changes nothing until it is checked: the model turns it into claims and the change it asks for, the
claims are checked on this machine (commands that only show what is installed), on the web (pages of
independent searches) and in what Tinker read (its passages), and the model weighs that evidence alone.
Only when every claim holds on it, and the change follows from them, are the items shown wrong retired, the
toolchain corrected and the lesson (or a book's corrected fact) kept, proven, with its sources. Anything less
changes nothing, and says why. An answer reads the passages of what Tinker read that it is about too."""

import re

from .messages import say
lazy from .conversation import Research
lazy from .graph import Graph
lazy from .knowledge import Knowledge, Learner, Library, Retriever
lazy from .prompts import Part, Prompts
lazy from .reading import Readings
lazy from .settings import (
    LESSON_PHASES, MEMORY_SHOWN, TEACH_CLAIMS, TEACH_COMMANDS, TEACH_SEARCHES, first_line, words,
)

ITEM_KINDS = ("toolchain", "fix", "lesson", "fact", "finding", "rule", "command")
TOOLCHAIN_KEYS = ("setup", "check", "test_all", "test_one", "format", "lint")  # what a teaching may replace
VERDICTS = ("holds", "wrong", "unsure")
PREFER_RE = re.compile(r"\bprefer", re.I)  # how a person marks a preference: prefer, preferred, preference


class Memory:
    """What Tinker keeps, as items a person asks about or corrects: {id, kind, text, status, group}, as the memory
    graph has them, the retired left out."""

    def __init__(self, embeddings=None):
        self.items = [node for node in Graph.build().nodes.values()
                      if node["kind"] in ITEM_KINDS and node["status"] != "retired"]
        self.retriever = Retriever(embeddings)

    def about(self, text, limit=MEMORY_SHOWN):
        """The items most related to text, the most related first: the more of an item's kind and language (or
        topic) the text names, the earlier it comes; alike ones in the order their words and meaning rank them."""
        ranking = self.retriever.rank(text, [item["text"] for item in self.items])
        named = set(words(text))
        ranking.sort(key=lambda index: -self.named_in(self.items[index], named))
        return [self.items[index] for index in ranking[:limit]]

    @staticmethod
    def named_in(item, named):
        """How many of an item's kind and language (or topic) the words name ("toolchains" names toolchain). A
        language's toolchain counts its kind as named with its language: it is what that language's projects use,
        however the words call it (tools, toolset, setup)."""
        group = item["group"] in named
        kind = any(word.startswith(item["kind"]) for word in named) or (group and item["kind"] == "toolchain")
        return group + kind


def numbered(items):
    """Items as a prompt and the person see them: [n] kind (where it belongs, status): text."""
    return "\n".join(item_line(n, item) for n, item in enumerate(items, 1))


def item_line(n, item):
    return f"[{n}] {item['kind']} ({item['group']}, {item['status'] or 'proven'}): {item['text']}"


def moved_on(tracker, phase, said, icon):
    """On to a phase (Tracker.STEPS): said, and the tracker (when there is one) moved on, so the chats show it."""
    say.step(said, icon)
    if tracker is not None:
        tracker.phase(phase)


def ending(status, headline, details=()):
    """How an ask or a teach ended, for its tracker (and so for the chats)."""
    return {"status": status, "headline": headline, "details": list(details)}


class Asker:
    """tinker ask QUESTION: answered from what Tinker keeps alone (no web), as a person answers: only what is
    asked, nothing more. While it works, one status line says what on (on a terminal), and the tracker moves on."""

    def __init__(self, model, memory, tracker=None):
        self.model, self.memory, self.tracker = model, memory, tracker

    def answer(self, question):
        """The answer said, and nothing else; the ending."""
        self.on_to("recall")
        with say.working_on(f"Looking through what Tinker keeps for: {first_line(question, 60)}"):
            items = self.memory.about(question)
            passages = Readings.passages(Readings.about(question, items), question)
        if not items and not passages:
            return self.said("I have not learned anything yet: I learn from builds (tinker task), tinker learn and "
                             "tinker read.")
        self.on_to("answer")
        read = f" and {len(passages)} passage(s) of what you gave it to read" if passages else ""
        with say.working_on(f"Answering from {len(items)} thing(s) Tinker keeps{read}"):
            return self.said(self.asked(question, items, passages))

    def on_to(self, phase):
        if self.tracker is not None:
            self.tracker.phase(phase)

    def asked(self, question, items, passages):
        parts = [Part("What Tinker keeps", numbered(items) or "(nothing)", True),
                 Part("From what you read", quoted(passages)), Part("The question", question, True)]
        return self.model.ask(Prompts.ASK_SYSTEM, parts, "ask").strip()

    @staticmethod
    def said(answer):
        for line in answer.splitlines():
            say.text(line)
        return ending("done", "Answered", [("Answer", answer)])


class Teacher:
    """tinker teach WHAT: what Tinker keeps corrected by the person, once checked (the module says how). Several
    things may be taught at once: each change is made when the claims it rests on hold, the others are said."""

    def __init__(self, model, internet, memory, tracker=None):
        self.model, self.internet, self.memory, self.tracker = model, internet, memory, tracker
        self.learner = Learner(model, internet, None)
        self.learner.tracker = tracker
        self.readings = []  # the readings the teaching is about: their passages are evidence

    def teach(self, teaching):
        """The teaching checked, then each change it asks for made when its claims hold; the ending."""
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
        """The claims (facts and preferences), what checks the facts, and the changes, read by read_plan."""
        moved_on(self.tracker, "understand", "Working out what to check…", "🤔")
        parts = [Part("What Tinker keeps", numbered(items) or "(nothing yet)", True),
                 Part("What the person teaches", teaching, True)]
        with say.working_on(f"Working out what to check in what you taught, against {len(items)} thing(s) Tinker keeps"):
            plan = self.model.ask_until_usable(Prompts.TEACH_SYSTEM, parts, "teach: what to check",
                                               lambda reply: read_plan(reply.json(), len(items), marked(teaching),
                                                                       bool(self.readings)))
        if plan is None:
            raise SystemExit(f"{self.model.name} could not work out what to check; nothing changed")
        with say.part("What you taught", "📝"):
            for claim in plan["claims"]:
                say.info(claim["text"], "👤" if claim["preference"] else "🔍")
        self.learner.outcome(f"{len(plan['claims'])} claim(s), {len(plan['changes'])} change(s) asked for")
        return plan

    def checked(self, teaching, plan, items):
        """(evidence, verdict): the facts weighed on what this machine and the web show (unsure when nothing could
        check them); the preferences honored as they are."""
        if not any(not claim["preference"] for claim in plan["claims"]):
            evidence, verdict = [], Verdict.unchecked(plan["claims"], "")
        else:
            evidence = (self.on_machine(plan["commands"]) + self.on_web(plan["searches"])
                        + self.in_readings(plan["claims"]))
            verdict = (self.judged(teaching, plan, items, evidence) if evidence
                       else Verdict.unchecked(plan["claims"], "nothing could check it"))
        verdict.show()
        return evidence, verdict

    def on_machine(self, commands):
        """[{source, text}]: what each safe command shows here (the others are skipped, and said)."""
        kept, dropped = Learner.safe_commands(commands)
        for command in dropped:
            say.skip(f"Skipped `{command}`: only commands that show what is installed are run")
        return [{"source": "this machine", "text": f"`{shown['command']}` → "
                 + (shown["output"] if shown["found"] else "not found here (a project's own environment may have it)")}
                for shown in self.learner.look_at_machine(kept)]

    def on_web(self, searches):
        """[{source, text}]: of each page the searches find, the parts most about them."""
        if not searches:
            return []
        moved_on(self.tracker, "search", "Searching the web for sources…", "🔎")
        with say.working_on(f"Reading the web: {len(searches)} search(es)"):
            pages = Research(None).read(self.internet, searches)
        if not pages:
            say.warn(f"No web page found: {self.internet.why()}")
        for page in pages:
            say.info(f"Read {page['title'] or page['url']}", "📖")
        self.learner.outcome(f"{len(pages)} page(s) read")
        return [{"source": page["url"], "text": f"{page['title']}\n{page['content']}"} for page in pages]

    def in_readings(self, claims):
        """[{source, text}]: of the readings the teaching is about, the passages most about each fact, each once."""
        if not self.readings:
            return []
        found = [passage for claim in claims if not claim["preference"]
                 for passage in Readings.passages(self.readings, claim["text"])]
        found = list({passage["text"]: passage for passage in found}.values())
        say.info(f"Found {len(found)} passage(s) of what was read about it", "📖")
        return found

    def judged(self, teaching, plan, items, evidence):
        """The model's verdict on each fact and on each change a fact is for, from the evidence alone (Verdict)."""
        moved_on(self.tracker, "judge", "Weighing the evidence…", "⚖️")
        claims = list(enumerate(plan["claims"], 1))
        parts = [Part("What the person teaches", teaching, True),
                 Part("Facts", "\n".join(f"{n}. {c['text']}" for n, c in claims if not c["preference"]), True),
                 Part("The person's preferences", "\n".join(f"{n}. {c['text']}" for n, c in claims if c["preference"])),
                 Part("Changes", "\n\n".join(f"Change {n} (rests on claims {', '.join(map(str, change['claims']))}):\n"
                                             f"{change_text(change, plan['language'], items)}"
                                             for n, change in judged_changes(plan)), True),
                 Part("Evidence", "\n\n".join(f"[{n}] {e['source']}\n{e['text']}" for n, e in enumerate(evidence, 1)),
                      True)]
        facts = sum(not claim["preference"] for claim in plan["claims"])
        with say.working_on(f"Weighing {len(evidence)} piece(s) of evidence on {facts} fact(s) you taught"):
            verdict = self.model.ask_until_usable(Prompts.JUDGE_SYSTEM, parts, "teach: weigh the evidence",
                                                  lambda reply: Verdict.read(reply.json(), plan, len(evidence)))
        if verdict is None:
            raise SystemExit(f"{self.model.name} could not weigh the evidence; nothing changed")
        return verdict

    def corrected(self, teaching, plan, items, evidence, verdict):
        """Each change made whose claims hold (its lines said), the others said with why; the ending."""
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
        details = verdict.details() + [("Changed", line) for line in made] + [("Not changed", line) for line in skipped]
        if not made:
            say.stop("Nothing changed")
            return ending("blocked", "Nothing changed", details)
        return ending("done", f"Corrected: {len(made)} change(s)", details)

    def made(self, change, language, items, reason, sources):
        """One change made: the items it shows wrong retired, the toolchain's commands replaced, the lesson kept;
        a line for each."""
        lines = self.retired([items[n - 1] for n in change["retire"]], reason)
        lines.append(self.toolchain_corrected(language, change["toolchain"]))
        lines.append(self.lesson_kept(language, change["lesson"], sources))
        lines.append(self.fact_kept(change["fact"], items, sources))
        return [line for line in lines if line]

    @staticmethod
    def retired(items, reason):
        """A line for each item retired (a toolchain and the person's commands are corrected, never retired)."""
        return [f"Retired {item['kind']}: {first_line(item['text'], 100)}" for item in items
                if retire_item(item, reason)]

    @staticmethod
    def toolchain_corrected(language, commands):
        if not commands:
            return ""
        if not language or not Knowledge(language).correct_toolchain(commands):
            say.skip(f"No toolchain kept for {language or 'that language'} to correct")
            return ""
        return f"Toolchain for {language}: " + ", ".join(f"{key} → {shown(command)}" for key, command in commands.items())

    @staticmethod
    def lesson_kept(language, lesson, sources):
        if not lesson:
            return ""
        scope = "language" if language else "any"
        kept = Knowledge(language or None).taught_lesson(dict(lesson, scope=scope), sources)
        return f"Lesson kept for {language or 'any language'}: {kept['text']}" if kept else ""


    @staticmethod
    def fact_kept(fact, items, sources):
        """What a book (or another topic) really says, kept in place of the item it corrects; a line, or ""."""
        if not fact:
            return ""
        kind, _, rest = items[fact["about"] - 1]["id"].partition(":")
        slug, _, claim_id = rest.rpartition(":")
        kept = (Library.taught_fact(slug, claim_id, fact["text"], sources) if kind in ("fact", "rule", "finding")
                else None)
        if kept is None:
            say.skip(f"No book or topic to keep it in: {first_line(fact['text'], 80)}")
            return ""
        return f"Fact kept for {(Library.read(slug) or {}).get('name', slug)}: {kept['text']}"

def quoted(passages):
    """Passages of what was read, as a prompt shows them: each under its title and chapter."""
    return "\n\n".join(f"[{passage['source']}]\n{passage['text']}" for passage in passages)

def marked(teaching):
    """Whether the person marked a preference in what they taught (the word prefer): else every claim is a fact."""
    return bool(PREFER_RE.search(teaching))


def read_plan(data, count, preferences=True, readings=False):
    """The claims ({text, preference}; with preferences false, each is a fact), the commands and searches that
    check the facts (each within its limit), the language and the changes (read_changes; count: how many items
    were shown). ValueError when there is no claim, nothing checks the facts (readings: their passages do), or
    there is no change."""
    texts = lambda key, limit: [str(t).strip() for t in data.get(key) or [] if str(t).strip()][:limit]
    claims = [dict(claim, preference=claim["preference"] and preferences) for claim in read_claims(data.get("claims"))]
    plan = {"claims": claims, "commands": texts("commands", TEACH_COMMANDS),
            "searches": texts("searches", TEACH_SEARCHES), "language": str(data.get("language") or "").strip(),
            "changes": read_changes(data.get("changes"), len(claims), count)}
    if not claims:
        raise ValueError("no claims")
    if any(not claim["preference"] for claim in claims) and not (plan["commands"] or plan["searches"] or readings):
        raise ValueError("no command or web search checks the facts")
    if not plan["changes"]:
        raise ValueError("no change: none rests on a claim and retires, replaces or keeps something")
    return plan


def read_claims(given):
    """[{text, preference}], at most TEACH_CLAIMS; a claim given as text alone is a fact."""
    claims = []
    for claim in given if isinstance(given, list) else []:
        claim = claim if isinstance(claim, dict) else {"text": claim}
        text = str(claim.get("text") or "").strip()
        if text:
            claims.append({"text": text, "preference": claim.get("preference") is True})
    return claims[:TEACH_CLAIMS]


def read_changes(given, claims, count):
    """[{claims, retire, toolchain, lesson, fact}]: each change that rests on a claim (1 to claims) and retires an
    item shown (1 to count), replaces toolchain commands, keeps a lesson or keeps a fact in place of an item shown
    (which it retires). ValueError when two change one toolchain key."""
    changes, keys = [], set()
    for change in given if isinstance(given, list) else []:
        if not isinstance(change, dict):
            continue
        read = {"claims": numbers(change.get("claims"), claims), "retire": numbers(change.get("retire"), count),
                "toolchain": read_toolchain(change.get("toolchain")), "lesson": read_lesson(change.get("lesson")),
                "fact": read_fact(change.get("fact"), count)}
        if read["fact"]:
            read["retire"] = sorted(set(read["retire"]) | {read["fact"]["about"]})
        if not read["claims"] or not (read["retire"] or read["toolchain"] or read["lesson"]):
            continue
        twice = keys & set(read["toolchain"])
        if twice:
            raise ValueError(f"toolchain {', '.join(sorted(twice))} is changed twice: put it in one change that "
                             f"rests on all its claims")
        keys |= set(read["toolchain"])
        changes.append(read)
    return changes


def numbers(given, highest):
    return sorted({n for n in given or [] if isinstance(n, int) and 1 <= n <= highest})


def read_toolchain(given):
    """{key: command} of TOOLCHAIN_KEYS (setup: a list of commands), the empty ones left out. ValueError for any other
    key or form, so the model is asked again with why rather than a part of its change dropped unsaid."""
    if not given:
        return {}
    if not isinstance(given, dict):
        raise ValueError("toolchain must be an object of key: command")
    found = {}
    for key, value in given.items():
        if key not in TOOLCHAIN_KEYS:
            raise ValueError(f"toolchain {key} cannot be changed (only {', '.join(TOOLCHAIN_KEYS)})")
        commands = command_list(key, value)
        if commands:
            found[key] = commands if key == "setup" else " && ".join(commands)
    return found


def command_list(key, value):
    """The commands a toolchain value gives: a list as it is; setup's line split at && (it is a list of commands);
    any other line whole. ValueError when it is neither text nor a list."""
    if isinstance(value, list):
        commands = value
    elif isinstance(value, str):
        commands = value.split("&&") if key == "setup" else [value]
    else:
        raise ValueError(f"toolchain {key} must be a command written as text")
    return [str(command).strip() for command in commands if str(command).strip()]


def read_lesson(given):
    """{phase, text} of a usable lesson, else None."""
    given = given if isinstance(given, dict) else {}
    text, phase = str(given.get("text") or "").strip(), str(given.get("phase") or "").strip().lower()
    return {"phase": phase, "text": text} if text and phase in LESSON_PHASES else None


def read_fact(given, count):
    """{about, text} of a fact kept in place of an item shown (about: 1 to count), else None."""
    given = given if isinstance(given, dict) else {}
    text, about = str(given.get("text") or "").strip(), given.get("about")
    return {"about": about, "text": text} if text and type(about) is int and 1 <= about <= count else None

def judged_changes(plan):
    """(number, change) of the changes that rest on a fact: the judge weighs them (the others rest on preferences)."""
    return [(n, change) for n, change in enumerate(plan["changes"], 1)
            if any(not plan["claims"][c - 1]["preference"] for c in change["claims"])]


def change_text(change, language, items):
    """A change, as the judge reads it."""
    lines = [f"Retire: {item_line(n, items[n - 1])}" for n in change["retire"]]
    lines += [f"Toolchain of {language}: {key} becomes `{shown(command)}`" for key, command in change["toolchain"].items()]
    if change["lesson"]:
        lines.append(f"Keep the lesson ({change['lesson']['phase']}): {change['lesson']['text']}")
    if change["fact"]:
        lines.append(f"Keep the fact, in place of [{change['fact']['about']}]: {change['fact']['text']}")
    return "\n".join(lines)


def change_summary(change):
    """A change in a few words, for the person: its lesson, else what it replaces or retires."""
    if change["lesson"]:
        return first_line(change["lesson"]["text"], 100)
    if change["fact"]:
        return first_line(change["fact"]["text"], 100)
    if change["toolchain"]:
        return f"toolchain {', '.join(change['toolchain'])}"
    return f"retire {len(change['retire'])} item(s)"


def shown(command):
    """A toolchain command as a person reads it (setup's list joined as a shell runs it)."""
    return " && ".join(command) if isinstance(command, list) else command


def retire_item(item, reason):
    """An item (as Memory gives it) retired where it is kept; whether it was. A toolchain or a command is not."""
    kind, _, rest = item["id"].partition(":")
    if kind in ("fact", "finding", "rule"):
        slug, _, claim_id = rest.rpartition(":")
        return Library.retire(slug, {claim_id}, reason, None) > 0
    if kind in ("lesson", "known", "fix"):
        key = item["id"] if kind != "fix" else f"fix:{rest.partition(':')[2]}"
        return Knowledge(item["group"]).retire_item(key, reason)
    return False


class Verdict:
    """The verdict on a teaching's claims, by number: a preference is honored; a fact holds (on the evidence it
    cites), is wrong or is unsure, with why. And, for each change a fact is for, whether it follows (with why)."""

    def __init__(self, claims, follows):
        self.claims, self.follows = claims, follows  # {n: {text, verdict, why, evidence}}, {n: (follows, why)}

    @classmethod
    def unchecked(cls, claims, why):
        """Nothing weighed the facts: each is unsure (why), each preference honored."""
        return cls({n: {"text": claim["text"], "verdict": "preference" if claim["preference"] else "unsure",
                        "why": "" if claim["preference"] else why, "evidence": []}
                    for n, claim in enumerate(claims, 1)}, {})

    @classmethod
    def read(cls, data, plan, evidence):
        """The verdict in the judge's reply: one for each fact (a fact said to hold with no evidence it can cite is
        unsure), and whether each change a fact is for follows. ValueError when one is missing or unknown."""
        verdict = cls.unchecked(plan["claims"], "")
        given = {item.get("claim"): item for item in data.get("claims") or [] if isinstance(item, dict)}
        for n, claim in verdict.claims.items():
            if claim["verdict"] == "preference":
                continue
            item = given.get(n)
            if item is None or item.get("verdict") not in VERDICTS:
                raise ValueError(f"claim {n} has no verdict (one of {', '.join(VERDICTS)})")
            cites = numbers(item.get("evidence"), evidence)
            holds_bare = item["verdict"] == "holds" and not cites
            claim.update(verdict="unsure" if holds_bare else item["verdict"], evidence=cites,
                         why="no evidence cited" if holds_bare else str(item.get("why") or "").strip())
        said = {item.get("change"): item for item in data.get("changes") or [] if isinstance(item, dict)}
        for n, _ in judged_changes(plan):
            if n not in said or not isinstance(said[n].get("follows"), bool):
                raise ValueError(f"change {n} has no \"follows\" (true or false)")
            verdict.follows[n] = (said[n]["follows"], str(said[n].get("why") or "").strip())
        return verdict

    def why_not(self, number, change):
        """Why a change is not made ("" when it is): a claim it rests on does not hold, or it does not follow."""
        for n in change["claims"]:
            if self.claims[n]["verdict"] not in ("holds", "preference"):
                return f"claim {n} is {self.claims[n]['verdict']}"
        follows, why = self.follows.get(number, (True, ""))
        return "" if follows else f"does not follow: {why or 'no reason given'}"

    def sources(self, change, evidence):
        """Where what a change rests on comes from: the evidence its facts cite, and the person for a preference."""
        found = [evidence[e - 1]["source"] for n in change["claims"] for e in self.claims[n]["evidence"]]
        if any(self.claims[n]["verdict"] == "preference" for n in change["claims"]):
            found.append("your preference")
        return list(dict.fromkeys(found))

    def show(self):
        for n, claim in self.claims.items():
            if claim["verdict"] == "preference":
                say.info(f"Your preference, honored: {claim['text']}", "👤")
                continue
            cited = f" [{', '.join(map(str, claim['evidence']))}]" if claim["evidence"] else ""
            line = f"{claim['verdict'].capitalize()}: {claim['text']} ({claim['why'] or 'no reason given'}){cited}"
            {"holds": say.done, "wrong": say.fail}.get(claim["verdict"], say.warn)(line)

    def details(self):
        """(label, value) of each claim's verdict."""
        return [("Your preference" if claim["verdict"] == "preference" else claim["verdict"].capitalize(),
                 claim["text"] + (f" ({claim['why']})" if claim["why"] else "")) for claim in self.claims.values()]
