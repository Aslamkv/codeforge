from dataclasses import asdict, dataclass, field

from .knowledge import record
from .settings import SAME_FACT_SIMILARITY, Home
from .text import fingerprint, similarity


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
    def new(cls, when, do, **fields):
        principle = cls(id=fingerprint(f"{when}\n{do}")[:12], when=when, do=do, **fields)
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

    def kept(self, scope):
        return [
            principle
            for principle in self.all()
            if principle.status != "retired" and principle.scope == scope
        ]

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
        principle = self.get(delta.get("id"))
        if principle:
            principle.except_when(delta.get("exception", ""), list(delta.get("evidence", [])), run)
        return principle
