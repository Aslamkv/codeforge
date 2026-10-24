"""What learning a language or a tool must find out, as data: a profile is a list of slots, each a question
with the search that answers it, how many passages it draws and the fewest it needs, and the phase its facts
go to. One pool of passages serves every slot (Selector); a slot left without facts is reported (missing)."""

from dataclasses import dataclass

lazy from .knowledge import Embeddings, Retriever
lazy from .passages import repeats, shingles
lazy from .settings import MMR_BALANCE, SLOT_CANDIDATES, SLOT_RELEVANT


@dataclass(frozen=True)
class Slot:
    """One thing a profile finds out. question and query take {topic}, {version} and {language}; the query's
    other words are the slot's keywords, so they name what it is about (no "what", "to"); docs: the words a
    documentation site's addresses and titles use for its pages (whatsnew, changelog); source: "web" (from
    passages), "machine" (the version commands run here) or "check" (readiness: no web)."""
    id: str
    question: str
    query: str
    passages: int
    minimum: int
    section: str
    source: str = "web"
    docs: str = ""

    def asked(self, plan, version):
        """The slot's question for this topic, with the plan's hint for it."""
        hint = plan.get("hints", {}).get(self.id, "")
        return fill(self.question, plan, version) + (f" (look for: {hint})" if hint else "")

    def search(self, plan, version):
        """The follow-up search for this slot: the plan's, else the slot's own."""
        return plan.get("followups", {}).get(self.id) or fill(self.query, plan, version)

    def keywords(self):
        """The words of the slot's own search, without the topic's: what a passage about it says."""
        return " ".join(self.query.format(topic="", version="", language="").split())


def fill(text, plan, version):
    """A slot's text for this topic: {language} is the language itself, or the one a tool is used with."""
    language = plan.get("language") or plan.get("used_with") or ""
    return " ".join(text.format(topic=plan["name"], version=version, language=language).split())


INSTALLED = Slot("installed", "Is {topic} installed here, and which version?", "", 0, 0, "toolchain", "machine")
READINESS = Slot("readiness", "Does every slot above have facts?", "", 0, 0, "", "check")

PROFILES = frozendict({
    "language": (
        INSTALLED,
        Slot("toolchain", "Which test library, formatter, linter and package manager does {topic} use, and what are "
             "the exact commands to run each?", "{topic} test framework formatter linter package manager commands",
             6, 2, "toolchain", docs="installing install packaging venv development tools testing"),
        Slot("fundamentals", "What are {topic}'s reserved keywords, core syntax, basic types and program structure?",
             "{topic} keywords syntax basic types", 5, 2, "code",
             docs="tutorial introduction reference lexical keywords syntax datatypes types controlflow"),
        Slot("this-version", "What is new or special in {topic} {version} that improves code quality or execution "
             "speed?", "{topic} {version} release notes new features", 5, 2, "code",
             docs="whatsnew changelog changes release notes migration porting deprecated removed"),
        Slot("limitations", "What are the real limits of the {topic} runtime and language?",
             "{topic} limitations runtime performance", 4, 1, "design",
             docs="faq design limitations performance threading memory"),
        Slot("best-use-cases", "What is {topic} well suited for, and what does it not suit?",
             "{topic} use cases strengths weaknesses", 3, 1, "spec", docs="faq general about appetite overview"),
        READINESS,
    ),
    "tool": (
        INSTALLED,
        Slot("purpose", "What is {topic} for, and when should it be chosen?", "{topic} overview purpose", 3, 1,
             "spec", docs="overview introduction about index why"),
        Slot("core-usage", "What are {topic}'s main APIs, and the standard way to set it up and use it?",
             "{topic} getting started tutorial API", 6, 2, "code",
             docs="quickstart tutorial getting started usage guide api reference"),
        Slot("this-version", "What changed or was deprecated in {topic} {version}?",
             "{topic} {version} changelog deprecations", 4, 1, "code",
             docs="changelog changes release history whatsnew migration deprecations upgrading"),
        Slot("pitfalls-and-limits", "What mistakes are common with {topic}, and what are its real limitations?",
             "{topic} common mistakes limitations", 4, 1, "code",
             docs="faq troubleshooting limitations caveats gotchas known issues"),
        Slot("conventions", "What project layout, patterns and configuration does {topic} recommend?",
             "{topic} project structure configuration best practices", 4, 1, "design",
             docs="configuration structure layout best practices patterns settings"),
        Slot("fit", "How does {topic} work with the {language} toolchain: testing, formatting, installing?",
             "{topic} {language} testing install", 3, 1, "toolchain",
             docs="install installation integration testing plugins"),
        READINESS,
    ),
})


TOOL_DOCS = "install installation getting started usage configuration command line options"  # a language's
# tools' documentation: the pages that show how to run and set them up (they fill its toolchain slot)


def web_slots(profile):
    return [slot for slot in PROFILES[profile] if slot.source == "web"]


def short_slots(profile, selected):
    """The web slots with fewer passages than their minimum, the emptiest first (by share of the minimum)."""
    short = [slot for slot in web_slots(profile) if len(selected.get(slot.id, [])) < slot.minimum]
    return sorted(short, key=lambda slot: len(selected.get(slot.id, [])) / slot.minimum)


def missing(profile, facts, machine):
    """The slots readiness finds without an answer: installed when no version command ran, a web slot
    with no fact."""
    filled = {fact.get("slot") for fact in facts}
    return [slot.id for slot in PROFILES[profile]
            if (slot.source == "machine" and not machine) or (slot.source == "web" and slot.id not in filled)]


class Selector:
    """Each slot's passages from one shared pool, with no model call: the slot's best SLOT_CANDIDATES by the words
    of its question, the topic and its version (BM25) are shortlisted, and only those are embedded (a pool of a
    thousand passages costs no more); of them, only a passage that holds a keyword of the slot (Slot.keywords) or
    is close in meaning to its question (SLOT_RELEVANT) counts; those are ranked by words and meaning fused, as the
    recall phase fuses them, then picked by maximal marginal relevance (MMR_BALANCE), so a near-repeat of a
    passage taken is passed over; at most the slot's count, within budget chars. A passage may serve several
    slots."""

    def __init__(self, embeddings, budget):
        self.embeddings, self.budget = embeddings, budget  # embeddings: None, the words alone

    def select(self, slots, pool, plan, version):
        """{slot id: [passages]} for each slot (version: the one installed, else the plan's)."""
        texts = [passage.searched() for passage in pool]
        queries = {slot.id: f"{slot.asked(plan, version)} {plan['name']} {version}" for slot in slots}
        shortlist = {slot.id: Retriever(None).by_words(queries[slot.id], texts)[:SLOT_CANDIDATES] for slot in slots}
        vectors = self.vectors(texts, sorted({*found for found in shortlist.values()}))
        return {slot.id: [pool[index] for index in self.diverse(
                    self.relevant(slot.keywords(), queries[slot.id], shortlist[slot.id], texts, vectors),
                    pool, vectors, slot.passages)]
                for slot in slots}

    def vectors(self, texts, indexes):
        """{index: embedding} of the shortlisted passages ({} without embeddings, or when they fail)."""
        if self.embeddings is None or not indexes:
            return {}
        found = self.embeddings.vectors([texts[index] for index in indexes])
        return dict(zip(indexes, found)) if found else {}

    def relevant(self, keywords, query, shortlist, texts, vectors):
        """{index: fused score} of the shortlisted passages that hold a keyword or are close to the query in meaning."""
        closeness = self.closeness(query, vectors)
        holding = set(Retriever(None).by_words(keywords, texts))
        counted = [index for index in shortlist if index in holding or closeness.get(index, 0.0) >= SLOT_RELEVANT]
        fused = {}
        for ranking in (counted, sorted(counted, key=lambda index: -closeness.get(index, 0.0))):
            for position, index in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1.0 / (Retriever.RRF_K + position + 1)
        return fused

    def closeness(self, query, vectors):
        """{index: cosine similarity to the query} ({} without embeddings)."""
        asked = self.embeddings.vectors([query], keep=False) if vectors else None
        return {index: Embeddings.cosine(asked[0], vector) for index, vector in vectors.items()} if asked else {}

    def diverse(self, scores, pool, vectors, count):
        """Up to count of the scored passages, by MMR, within the budget: the indexes in the order taken."""
        if not scores:
            return []
        top, taken, room = max(scores.values()), [], self.budget
        marks = None if vectors else [shingles(passage.text) for passage in pool]
        left = sorted(scores, key=lambda index: (-scores[index], index))
        while left and len(taken) < count:
            best = max(left, key=lambda index: MMR_BALANCE * scores[index] / top
                       - (1 - MMR_BALANCE) * self.most_alike(index, taken, vectors, marks))
            left.remove(best)
            size = len(pool[best].listing()) + 2
            if size <= room:
                taken.append(best)
                room -= size
        return taken

    @staticmethod
    def most_alike(index, taken, vectors, marks):
        """How alike a passage is to the closest one taken: cosine with embeddings, else 1 for a near-repeat."""
        if not taken:
            return 0.0
        if vectors:
            return max(Embeddings.cosine(vectors[index], vectors[other]) for other in taken)
        return max(float(repeats(marks[index], marks[other])) for other in taken)
