from dataclasses import asdict, dataclass, field

from .knowledge import record
from .settings import Home
from .text import fingerprint


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
