"""Asking Tinker what it keeps (Asker), and teaching it (Teacher). Both read what Tinker keeps as one list of
items (Memory: each toolchain, fix, lesson, fact, finding, rule and command of the memory graph, the retired
left out), the most related first. An answer comes from those items alone, each claim citing its item. A
teaching changes nothing until it is checked: the model turns it into claims and the change it asks for, the
claims are checked on this machine (commands that only show what is installed) and on the web (pages of
independent searches), and the model weighs that evidence alone. Only when every claim holds on it, and the
change follows from them, are the items shown wrong retired, the toolchain corrected and the lesson kept,
proven, with its sources. Anything less changes nothing, and says why."""

import re

from .messages import say
lazy from .conversation import Research
lazy from .graph import Graph
lazy from .knowledge import Knowledge, Learner, Library, Retriever
lazy from .prompts import Part, Prompts
lazy from .settings import (
    LESSON_PHASES, MEMORY_SHOWN, TEACH_CLAIMS, TEACH_COMMANDS, TEACH_SEARCHES, first_line, words,
)

ITEM_KINDS = ("toolchain", "fix", "lesson", "fact", "finding", "rule", "command")
TOOLCHAIN_KEYS = ("setup", "check", "test_all", "test_one", "format", "lint")  # what a teaching may replace
VERDICTS = ("holds", "wrong", "unsure")
CITED_RE = re.compile(r"\[(\d+)\]")


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
        """How many of an item's kind and language (or topic) the words name ("toolchains" names toolchain)."""
        return (item["group"] in named) + any(word.startswith(item["kind"]) for word in named)


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
    """tinker ask QUESTION: answered from what Tinker keeps alone (no web), each claim citing the item it rests
    on; the items it cites are listed under it."""

    def __init__(self, model, memory, tracker=None):
        self.model, self.memory, self.tracker = model, memory, tracker

    def answer(self, question):
        """The answer said, and the items it cites; the ending."""
        moved_on(self.tracker, "recall", "Recalling what Tinker keeps about it…", "🧠")
        items = self.memory.about(question)
        if not items:
            say.warn("Tinker keeps nothing yet: it learns from builds (tinker task) and from tinker learn")
            return ending("blocked", "Nothing to answer from", [("Why", "Tinker keeps nothing yet")])
        moved_on(self.tracker, "answer", "Answering from what Tinker keeps…", "💬")
        answer = self.asked(question, items)
        cited = self.cited(answer, items)
        self.show(question, answer, cited)
        return ending("done", "Answered", [("Answer", answer)] + [("From", line) for line in cited])

    def asked(self, question, items):
        parts = [Part("What Tinker keeps", numbered(items), True), Part("The question", question, True)]
        return self.model.ask(Prompts.ASK_SYSTEM, parts, "ask").strip()

    @staticmethod
    def cited(answer, items):
        """The lines of the items the answer cites, by the numbers it gives them, in order."""
        numbers = dict.fromkeys(int(n) for n in CITED_RE.findall(answer))
        return [item_line(n, items[n - 1]) for n in numbers if 1 <= n <= len(items)]

    @staticmethod
    def show(question, answer, cited):
        with say.part(question, "❓"):
            for line in answer.splitlines():
                say.text(line)
        if cited:
            with say.part("From what Tinker keeps", "📒"):
                for line in cited:
                    say.item(line)


class Teacher:
    """tinker teach WHAT: what Tinker keeps corrected by the person, once checked (the module says how)."""

    def __init__(self, model, internet, memory, tracker=None):
        self.model, self.internet, self.memory, self.tracker = model, internet, memory, tracker
        self.learner = Learner(model, internet, None)
        self.learner.tracker = tracker

    def teach(self, teaching):
        """The teaching checked, then what Tinker keeps corrected only when it holds; the ending."""
        items = self.recalled(teaching)
        plan = self.understood(teaching, items)
        evidence = self.on_machine(plan["commands"]) + self.on_web(plan["searches"])
        if not evidence:
            return self.unchanged("Nothing changed: nothing could check it",
                                  [("Why", "no command ran and no web page was read")])
        verdict = self.judged(teaching, plan, items, evidence)
        if not verdict.holds():
            return self.unchanged("Nothing changed: the teaching did not hold", verdict.details())
        return self.corrected(teaching, plan, items, verdict.sources(evidence), verdict)

    def recalled(self, teaching):
        moved_on(self.tracker, "recall", "Recalling what Tinker keeps about it…", "🧠")
        return self.memory.about(teaching)

    def understood(self, teaching, items):
        """What to check (claims, commands, searches) and the change asked for, read by read_plan."""
        moved_on(self.tracker, "understand", "Working out what to check…", "🤔")
        parts = [Part("What Tinker keeps", numbered(items) or "(nothing yet)", True),
                 Part("What the person teaches", teaching, True)]
        plan = self.model.ask_until_usable(Prompts.TEACH_SYSTEM, parts, "teach: what to check",
                                           lambda reply: read_plan(reply.json(), len(items)))
        if plan is None:
            raise SystemExit(f"{self.model.name} could not work out what to check; nothing changed")
        with say.part("To check", "📝"):
            for claim in plan["claims"]:
                say.item(claim)
        return plan

    def on_machine(self, commands):
        """[{source, text}]: what each safe command shows here (the others are skipped, and said)."""
        kept, dropped = Learner.safe_commands(commands)
        for command in dropped:
            say.skip(f"Skipped `{command}`: only commands that show what is installed are run")
        return [{"source": "this machine", "text": f"`{shown['command']}` → "
                 + (shown["output"] if shown["found"] else "fails: not installed here")}
                for shown in self.learner.look_at_machine(kept)]

    def on_web(self, searches):
        """[{source, text}]: of each page the searches find, the parts most about them."""
        if not searches:
            return []
        moved_on(self.tracker, "search", "Searching the web for sources…", "🔎")
        pages = Research(None).read(self.internet, searches)
        if not pages:
            say.warn(f"No web page found: {self.internet.why()}")
        for page in pages:
            say.info(f"Read {page['title'] or page['url']}", "📖")
        return [{"source": page["url"], "text": f"{page['title']}\n{page['content']}"} for page in pages]

    def judged(self, teaching, plan, items, evidence):
        """The model's verdict on each claim and on the change, from the evidence alone (Verdict)."""
        moved_on(self.tracker, "judge", "Weighing the evidence…", "⚖️")
        parts = [Part("What the person teaches", teaching, True),
                 Part("Claims", "\n".join(f"{n}. {claim}" for n, claim in enumerate(plan["claims"], 1)), True),
                 Part("The change it asks for", change_text(plan, items), True),
                 Part("Evidence", "\n\n".join(f"[{n}] {e['source']}\n{e['text']}" for n, e in enumerate(evidence, 1)),
                      True)]
        verdict = self.model.ask_until_usable(Prompts.JUDGE_SYSTEM, parts, "teach: weigh the evidence",
                                              lambda reply: Verdict.read(reply.json(), plan["claims"], len(evidence)))
        if verdict is None:
            raise SystemExit(f"{self.model.name} could not weigh the evidence; nothing changed")
        verdict.show()
        return verdict

    def corrected(self, teaching, plan, items, sources, verdict):
        """The change made: the items shown wrong retired, the toolchain's commands replaced, the lesson kept."""
        moved_on(self.tracker, "correct", "Correcting what Tinker keeps…", "✏️")
        reason = f"corrected by what you taught: {first_line(teaching, 120)}"
        changes = [line for line in [*self.retired([items[n - 1] for n in plan["retire"]], reason),
                                     self.toolchain_corrected(plan["language"], plan["toolchain"]),
                                     self.lesson_kept(plan["language"], plan["lesson"], sources)] if line]
        if not changes:
            return self.unchanged("Nothing changed: there was nothing to correct", verdict.details())
        for line in changes:
            say.done(line)
        return ending("done", f"Corrected: {len(changes)} change(s)", verdict.details() + [("Changed", line)
                                                                                         for line in changes])

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
        return f"Lesson kept (proven, taught by you) for {language or 'any language'}: {kept['text']}" if kept else ""

    @staticmethod
    def unchanged(headline, details):
        say.stop(headline)
        return ending("blocked", headline, details)


def read_plan(data, count):
    """What to check and the change asked for, from the model's reply: claims, commands, searches (each within its
    limit), language, retire (numbers of the items shown, 1 to count), toolchain ({key: command} of
    TOOLCHAIN_KEYS) and lesson ({phase, text} or None). ValueError when there is no claim, nothing checks
    them, or there is no change."""
    texts = lambda key, limit: [str(t).strip() for t in data.get(key) or [] if str(t).strip()][:limit]
    plan = {"claims": texts("claims", TEACH_CLAIMS), "commands": texts("commands", TEACH_COMMANDS),
            "searches": texts("searches", TEACH_SEARCHES), "language": str(data.get("language") or "").strip(),
            "retire": sorted({n for n in data.get("retire") or [] if isinstance(n, int) and 1 <= n <= count}),
            "toolchain": read_toolchain(data.get("toolchain")),
            "lesson": read_lesson(data.get("lesson"))}
    if not plan["claims"]:
        raise ValueError("no claims to check")
    if not plan["commands"] and not plan["searches"]:
        raise ValueError("no command or web search checks the claims")
    if not (plan["retire"] or plan["toolchain"] or plan["lesson"]):
        raise ValueError("no change: nothing to retire, no toolchain command and no lesson")
    return plan


def read_toolchain(given):
    """{key: command} of TOOLCHAIN_KEYS (setup: a list of commands), the empty ones left out."""
    given, found = given if isinstance(given, dict) else {}, {}
    for key, value in given.items():
        if key == "setup" and isinstance(value, list):
            value = [str(command).strip() for command in value if str(command).strip()]
        elif key in TOOLCHAIN_KEYS and key != "setup" and isinstance(value, str):
            value = value.strip()
        else:
            continue
        if value:
            found[key] = value
    return found


def read_lesson(given):
    """{phase, text} of a usable lesson, else None."""
    given = given if isinstance(given, dict) else {}
    text, phase = str(given.get("text") or "").strip(), str(given.get("phase") or "").strip().lower()
    return {"phase": phase, "text": text} if text and phase in LESSON_PHASES else None


def change_text(plan, items):
    """The change a teaching asks for, as the judge reads it."""
    lines = [f"Retire: {item_line(n, items[n - 1])}" for n in plan["retire"]]
    lines += [f"Toolchain of {plan['language']}: {key} becomes `{shown(command)}`"
              for key, command in plan["toolchain"].items()]
    if plan["lesson"]:
        lines.append(f"Keep the lesson ({plan['lesson']['phase']}): {plan['lesson']['text']}")
    return "\n".join(lines)


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
    """The model's verdict on a teaching: each claim holds, is wrong or is unsure (with why, and the evidence
    it rests on), and whether the change follows."""

    def __init__(self, claims, change, why):
        self.claims, self.change, self.why = claims, change, why

    @classmethod
    def read(cls, data, claims, evidence):
        """The verdict in the model's reply; ValueError when a claim has none, or a verdict is unknown."""
        given = {item.get("claim"): item for item in data.get("claims") or [] if isinstance(item, dict)}
        judged = []
        for n, claim in enumerate(claims, 1):
            item = given.get(n)
            if item is None or item.get("verdict") not in VERDICTS:
                raise ValueError(f"claim {n} has no verdict (one of {', '.join(VERDICTS)})")
            cites = sorted({c for c in item.get("evidence") or [] if isinstance(c, int) and 1 <= c <= evidence})
            judged.append({"claim": claim, "verdict": item["verdict"], "why": str(item.get("why") or "").strip(),
                           "evidence": cites})
        return cls(judged, data.get("change") is True, str(data.get("why") or "").strip())

    def holds(self):
        """Every claim holds on evidence it cites, and the change follows from them."""
        return self.change and all(c["verdict"] == "holds" and c["evidence"] for c in self.claims)

    def sources(self, evidence):
        """Where the evidence the claims rest on comes from (pages, this machine), each once."""
        return list(dict.fromkeys(evidence[n - 1]["source"] for c in self.claims for n in c["evidence"]))

    def show(self):
        for claim in self.claims:
            line = f"{claim['claim']}: {claim['why'] or 'no reason given'}" + self.cited(claim)
            {"holds": say.done, "wrong": say.fail}.get(claim["verdict"], say.warn)(
                f"{claim['verdict'].capitalize()}: {line}")
        if not self.change:
            say.warn(f"The change does not follow: {self.why or 'no reason given'}")

    def details(self):
        """(label, value) of each claim's verdict, and of the change when it does not follow."""
        rows = [(claim["verdict"].capitalize(), f"{claim['claim']} ({claim['why'] or 'no reason given'})")
                for claim in self.claims]
        return rows + ([("Change", f"does not follow: {self.why or 'no reason given'}")] if not self.change else [])

    @staticmethod
    def cited(claim):
        return f" [{', '.join(map(str, claim['evidence']))}]" if claim["evidence"] else ""
