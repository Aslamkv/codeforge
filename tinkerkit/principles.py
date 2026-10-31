from dataclasses import asdict, dataclass, field

from .knowledge import Retriever, record
from .prompts import Part, Prompts
from .settings import (
    FACT_PHASES,
    INDUCE_KNOWN_SHOWN,
    MAX_FACT_CHARS,
    MAX_RULE_WHEN_CHARS,
    SAME_FACT_SIMILARITY,
    Home,
)
from .text import fingerprint, one_line, similarity


@dataclass
class Principle:
    id: str
    when: str
    do: str
    because: str = ""
    exceptions: list = field(default_factory=list)
    scope: str = "any"
    phase: str = "any"
    evidence: list = field(default_factory=list)
    helped: int = 0
    harmed: int = 0
    status: str = "candidate"
    history: list = field(default_factory=list)

    @classmethod
    def new(cls, when, do, scope="any", **fields):
        principle = cls(
            id=fingerprint(f"{scope}\n{when}\n{do}")[:12], when=when, do=do, scope=scope, **fields
        )
        principle.history = record({}, "born", "")["history"]
        return principle

    @classmethod
    def from_doc(cls, doc):
        return cls(**doc)

    def to_doc(self):
        return asdict(self)

    def prompt_text(self):
        rule = f"{self.when} → {self.do}" if self.when else self.do
        return f"{rule}; unless {self.unless()}" if self.exceptions else rule

    def english(self):
        parts = [f"When {self.when}, {self.do}" if self.when else self.do]
        if self.because:
            parts.append(f"because {self.because}")
        if self.exceptions:
            parts.append(f"unless {self.unless()}")
        sentence = ", ".join(parts)
        return f"{sentence[:1].upper()}{sentence[1:]}."

    def unless(self):
        return " or ".join(self.exceptions)

    def rule_text(self):
        return f"{self.when}\n{self.do}"

    def record(self, event, run, **details):
        self.history = record({"history": self.history}, event, run)["history"]
        self.history[-1].update(details)

    def strengthen(self, evidence, run):
        self.evidence = self.evidence + evidence
        self.record("confirmed", run)

    def contradict(self, run):
        self.harmed += 1
        self.record("contradicted", run)
        if self.harmed > len(self.evidence):
            self.retire("contradicted more often than confirmed", run)

    def except_when(self, exception, evidence, run):
        self.exceptions = self.exceptions + [exception]
        self.evidence = self.evidence + evidence
        self.record("excepted", run)

    def retire(self, reason, run):
        self.status = "retired"
        self.record("retired", run, reason=reason)


class Principles:
    COLLECTION = "principles"

    def __init__(self, embeddings):
        self.embeddings = embeddings

    def all(self):
        return [Principle.from_doc(doc) for doc in Home.store().items(self.COLLECTION).values()]

    def get(self, principle_id):
        doc = Home.store().get(self.COLLECTION, principle_id)
        return Principle.from_doc(doc) if doc else None

    def live(self):
        return [principle for principle in self.all() if principle.status != "retired"]

    def kept(self, scope):
        return [principle for principle in self.live() if principle.scope == scope]

    def forget_scope(self, scope):
        for principle in self.all():
            if principle.scope == scope:
                Home.store().delete(self.COLLECTION, principle.id)

    def about(self, text, limit, scope=None):
        principles = self.kept(scope) if scope else self.live()
        order = Retriever(self.embeddings).rank(text, [principle.do for principle in principles])
        return [principles[index] for index in order[:limit]]

    def put(self, principle):
        Home.store().put(self.COLLECTION, principle.id, principle.to_doc())
        self.embed(principle)

    def embed(self, principle):
        texts = [text for text in (principle.when, principle.do) if text]
        if self.embeddings and texts:
            self.embeddings.vectors(texts)

    def apply(self, deltas, run):
        changed = {}
        for delta in deltas:
            principle = self.apply_one(delta, run)
            if principle:
                self.put(principle)
                changed[principle.id] = principle
        return list(changed.values())

    def apply_one(self, delta, run):
        kinds = {
            "add": self.add,
            "strengthen": self.strengthen,
            "contradict": self.contradict,
            "except": self.add_exception,
        }
        apply_kind = kinds.get(delta.get("kind"))
        return apply_kind(delta, run) if apply_kind else None

    def add(self, delta, run):
        if not delta.get("do") or not delta.get("evidence"):
            return None
        candidate = Principle.new(
            delta.get("when", ""),
            delta["do"],
            because=delta.get("because", ""),
            exceptions=list(delta.get("exceptions", [])),
            scope=delta.get("scope", "any"),
            phase=delta.get("phase", "any"),
            evidence=list(delta["evidence"]),
        )
        like = self.like(candidate)
        if like:
            like.strengthen(candidate.evidence, run)
            return like
        return candidate

    def like(self, candidate):
        return next(
            (
                kept
                for kept in self.kept(candidate.scope)
                if similarity(kept.rule_text(), candidate.rule_text()) >= SAME_FACT_SIMILARITY
            ),
            None,
        )

    def strengthen(self, delta, run):
        principle = self.get(delta.get("id"))
        if principle:
            principle.strengthen(list(delta.get("evidence", [])), run)
        return principle

    def contradict(self, delta, run):
        principle = self.get(delta.get("id"))
        if principle:
            principle.contradict(run)
        return principle

    def add_exception(self, delta, run):
        exception = delta.get("exception")
        principle = self.get(delta.get("id")) if exception else None
        if principle:
            principle.except_when(exception, list(delta.get("evidence", [])), run)
        return principle


class Inducer:
    PHASES = FACT_PHASES + ("book", "any")

    def __init__(self, model, principles):
        self.model = model
        self.principles = principles

    def induce(self, passages, scope, think=None, look_for="", label="Induce principles from the passages"):
        known = self.principles.about(self.passages_text(passages), INDUCE_KNOWN_SHOWN, scope)
        answer = self.model.ask_until_usable(
            Prompts.INDUCE_SYSTEM,
            self.induce_parts(passages, known, look_for),
            label,
            lambda reply: self.read_deltas(reply.json(), passages, known, scope),
            attempts=2,
            think=think,
        )
        return answer.value or []

    @staticmethod
    def passages_text(passages):
        return "\n".join(passage["text"] for passage in passages)

    @staticmethod
    def induce_parts(passages, known, look_for=""):
        listing = "\n".join(f"{number}. {passage['text']}" for number, passage in enumerate(passages, 1))
        principles = "\n".join(
            f"{number}. {principle.prompt_text()}"
            for number, principle in enumerate(known, len(passages) + 1)
        )
        return [
            Part("Passages", listing, True, exact=True),
            Part("Principles kept", principles or "(none yet)", True, exact=True),
        ] + ([Part("Look for", look_for)] if look_for else [])

    def read_deltas(self, data, passages, known, scope):
        given = data.get("deltas") if isinstance(data, dict) else None
        if not isinstance(given, list):
            raise ValueError("it needs a list of deltas")
        deltas = (self.delta_of(item, passages, known, scope) for item in given if isinstance(item, dict))
        return [delta for delta in deltas if delta]

    def delta_of(self, item, passages, known, scope):
        evidence = self.cited_evidence(item, passages)
        kinds = {
            "add": self.add_of,
            "strengthen": self.change_of,
            "contradict": self.change_of,
            "except": self.exception_of,
        }
        delta_of_kind = kinds.get(item.get("kind"))
        if not (evidence and delta_of_kind):
            return None
        delta = delta_of_kind(item, len(passages), known, scope)
        return dict(delta, kind=item["kind"], evidence=evidence) if delta else None

    @staticmethod
    def cited_evidence(item, passages):
        cites = item.get("cites")
        numbers = dict.fromkeys(
            number
            for number in (cites if isinstance(cites, list) else [])
            if isinstance(number, int) and 1 <= number <= len(passages)
        )
        return [
            {
                "kind": "passage",
                "source": passages[number - 1]["source"],
                "text": passages[number - 1]["text"][:MAX_FACT_CHARS],
            }
            for number in numbers
        ]

    def add_of(self, item, shown_passages, known, scope):
        do = one_line(item.get("do"))[:MAX_FACT_CHARS]
        phase = str(item.get("phase") or "").lower()
        return do and {
            "do": do,
            "when": one_line(item.get("when"))[:MAX_RULE_WHEN_CHARS],
            "because": one_line(item.get("because")),
            "scope": scope,
            "phase": phase if phase in self.PHASES else "any",
        }

    @staticmethod
    def change_of(item, shown_passages, known, scope):
        number = item.get("id")
        index = number - shown_passages - 1 if isinstance(number, int) else -1
        return {"id": known[index].id} if 0 <= index < len(known) else None

    def exception_of(self, item, shown_passages, known, scope):
        change = self.change_of(item, shown_passages, known, scope)
        exception = one_line(item.get("exception"))[:MAX_RULE_WHEN_CHARS]
        return change and exception and dict(change, exception=exception)
