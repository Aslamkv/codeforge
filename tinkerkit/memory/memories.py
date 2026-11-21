import datetime
import json
import re

from ..messages import next_page_line, say
from ..settings import (
    ANSWER_COMMON_WORDS,
    ANSWER_LASTS_DAYS,
    ANSWER_SAME_MEANING,
    ANSWER_SAME_WORDS,
    EPISODE_FOUND_CHARS,
    EPISODE_GIST_CHARS,
    EPISODES_KEPT_BYTES,
    NOTE_SAME_WORDS,
    NOTES_KEPT_BYTES,
    PRESENT_WHENS,
    QUESTION_SAME_MEANING,
    RECALL_MEANING,
    RECALL_SHOWN,
    SAME_FACT_SIMILARITY,
    SAME_MEANING,
    TRAIT_OBVIOUS_SOURCES,
    TRAITS_CHARS,
    Home,
    Numbered,
    confirm_deletion,
)
from ..text import similarity, words

EPISODE_BOOKKEEPING = frozenset({"id", "chat", "time", "dreamt", "dreamt_time"})
KEPT_WHOLE = EPISODE_BOOKKEEPING | {"status"}
DREAM = "dream"
STARTER_TOOLS = (
    ("how much free disk space is left", "df -h ~"),
    ("how big is this folder", "du -sh ~"),
    ("list the files by size", "ls -lS ~"),
    ("what are the biggest files here", "du -ah ~ | sort -rh | head -n 20"),
    ("how big is each folder in here", "du -sh ~/*"),
    ("how many files are in this folder", "find ~ -type f | wc -l"),
    ("which files are larger than 100 MB", "find ~ -type f -size +100M"),
)
GIVEN_ID = r"\b[emtq]\d+\b"
ID_SEPARATOR = r"\s*(?:,\s*(?:(?:and|or)\b)?|\b(?:and|or)\b)\s*"
GIVEN_IDS = re.compile(rf"{GIVEN_ID}(?:{ID_SEPARATOR}{GIVEN_ID})*")
REMOVED_ID = "\0"
REMOVED_IDS = rf"(?: *{REMOVED_ID})+ *"
CLAUSE_END = r"(?=[,.;:!?)\]]|$)"
LEAD_WORD = r"\b(?:in|from|of|by|see|at|per|on|to)"
SENTENCE_START = r"(^\s*|[.!?:]\s+)"
OPENING_PHRASE = rf"\w+\s+(?:on|to)\s*{REMOVED_IDS},\s*"
LEFT_AFTER_IDS = (
    (re.compile(rf"{SENTENCE_START}{OPENING_PHRASE}", re.IGNORECASE), r"\1"),
    (re.compile(rf"{LEAD_WORD}\s+(?={REMOVED_IDS}{CLAUSE_END})", re.IGNORECASE), ""),
    (re.compile(rf"[(\[]\s*[,;]?{REMOVED_IDS}[,;]?\s*[)\]]"), REMOVED_ID),
    (re.compile(rf",\s*{REMOVED_IDS},"), ","),
    (re.compile(rf"^[\s,;]*{REMOVED_IDS}[\s,;]*"), ""),
    (re.compile(rf"([(\[]){REMOVED_IDS}"), r"\1"),
    (re.compile(rf"{REMOVED_IDS}{CLAUSE_END}"), ""),
    (re.compile(REMOVED_IDS), " "),
)


def without_ids(text):
    text = GIVEN_IDS.sub(REMOVED_ID, text)
    for leftover, replacement in LEFT_AFTER_IDS:
        text = leftover.sub(replacement, text)
    return text


class Scratchpad:
    EPISODES = "episodes"
    COUNTER = "episode_ids"

    @classmethod
    def write(cls, episode, now=None):
        kept = dict(
            outputs_cut(episode),
            id=f"e{cls.next_number()}",
            time=stamp(now),
            dreamt=False,
        )
        Home.store().put(cls.EPISODES, kept["id"], kept)
        return kept

    @classmethod
    def next_number(cls):
        return Home.store().update(cls.COUNTER, "last", lambda last: (last or 0) + 1)

    @classmethod
    def fresh(cls):
        return [episode for episode in Home.store().items(cls.EPISODES).values() if not episode["dreamt"]]

    @classmethod
    def dreamt(cls, ids, now=None, gists=None):
        gists = gists or {}
        for episode_id in ids:
            written = {"gist": gists[episode_id][:EPISODE_GIST_CHARS]} if episode_id in gists else {}
            Home.store().update(
                cls.EPISODES,
                episode_id,
                lambda episode, written=written: dict(
                    gist(episode), dreamt=True, dreamt_time=stamp(now), **written
                ),
            )

    @classmethod
    def fail(cls, episode_id):
        episode = Home.store().get(cls.EPISODES, episode_id)
        if episode and not episode["dreamt"]:
            Home.store().update(cls.EPISODES, episode_id, failed)

    @classmethod
    def prune(cls):
        dreamt = sorted(
            (episode for episode in Home.store().items(cls.EPISODES).values() if episode["dreamt"]),
            key=lambda episode: episode["dreamt_time"],
        )
        size = sum(len(json.dumps(episode)) for episode in dreamt)
        for oldest in dreamt:
            if size <= EPISODES_KEPT_BYTES:
                return
            Home.store().delete(cls.EPISODES, oldest["id"])
            size -= len(json.dumps(oldest))


def failed(episode):
    return episode if episode["dreamt"] else dict(episode, status="failed")


class Notes:
    KEY = "notes"

    @classmethod
    def keep(cls, text, now=None):
        text = text.strip()
        if not text:
            return None
        note = {"at": stamp(now), "text": text}
        kept = cls.listed()
        unalike = [older for older in kept if similarity(older["text"], text) < NOTE_SAME_WORDS]
        Home.store().put(DREAM, cls.KEY, within_budget([note, *unalike]))
        return note if len(unalike) == len(kept) else None

    @classmethod
    def listed(cls):
        return Home.store().get(DREAM, cls.KEY) or []


def within_budget(notes):
    while len(notes) > 1 and len(json.dumps(notes)) > NOTES_KEPT_BYTES:
        notes = notes[:-1]
    return notes


class Memories:
    KINDS = ("person", "fact", "done", "tool", "answer")
    MEMORIES = "memories"
    COUNTER = "memory_ids"

    def __init__(self, retriever):
        self.retriever = retriever

    def add(self, kind, gist, text, sources, now=None, lasts=None):
        if not sources:
            raise ValueError("a memory needs the episodes it came from")
        if not lasts_found(kind, lasts):
            raise ValueError("an answer memory needs how long it lasts")
        memory = {
            "id": f"m{self.next_number()}",
            "kind": kind,
            "gist": gist,
            "text": text,
            "sources": list(sources),
            "first_seen": stamp(now),
            "last_seen": stamp(now),
            "recalled": 0,
            "last_recalled": "",
            "strength": 1,
            "retired": "",
        }
        if kind == "answer":
            memory["lasts"] = lasts
        Home.store().put(self.MEMORIES, memory["id"], memory)
        return memory

    def add_starter_tools(self):
        stored = Home.store().items(self.MEMORIES).values()
        kept = {memory["text"] for memory in stored if memory["kind"] == "tool"}
        for gist, line in STARTER_TOOLS:
            if line not in kept:
                self.add("tool", gist=gist, text=line, sources=["starter"])

    def next_number(self):
        return Home.store().update(self.COUNTER, "last", lambda last: (last or 0) + 1)

    def update(self, memory_id, changes):
        return Home.store().update(self.MEMORIES, memory_id, lambda memory: dict(memory, **changes))

    def retire(self, memory_id, why):
        return self.update(memory_id, {"retired": why})

    def candidates(self, kinds):
        if kinds is None:
            return self.living() + Scratchpad.fresh()
        return [memory for memory in self.living() if memory["kind"] in kinds]

    def recall(self, request, now=None, kinds=None):
        candidates = self.candidates(kinds)
        found = [
            candidates[index] for index in self.hits(request, [recall_text(item) for item in candidates])
        ]
        return [self.recalled(item, now) if item["id"].startswith("m") else item for item in found]

    def living(self):
        return [memory for memory in Home.store().items(self.MEMORIES).values() if not memory["retired"]]

    def most_alike(self, kind, gist, text):
        same_kind = [memory for memory in self.living() if memory["kind"] == kind]
        likeness = self.retriever.likeness() if self.retriever else WordsLikeness()
        closes = likeness.to(
            likeness_text(gist, text),
            [likeness_text(memory["gist"], memory["text"]) for memory in same_kind],
            SAME_FACT_SIMILARITY,
            SAME_MEANING,
        )
        alike = [(close, memory) for close, memory in zip(closes, same_kind) if close]
        return max(alike, key=lambda pair: pair[0], default=(0.0, None))[1]

    def hits(self, request, texts):
        by_words = self.retriever.by_words(request, texts)
        similarity_of = self.retriever.similarities(request, texts) or []
        by_meaning = sorted(
            (index for index, similarity in enumerate(similarity_of) if similarity >= RECALL_MEANING),
            key=lambda index: -similarity_of[index],
        )
        return (by_words + [index for index in by_meaning if index not in by_words])[:RECALL_SHOWN]

    def answer_to(self, request, now=None):
        memory = self.same_answer(request)
        return self.recalled(memory, now) if memory else None

    def same_answer(self, request):
        answers = [memory for memory in self.living() if memory["kind"] == "answer"]
        matches = self.matching_questions(request, [memory["gist"] for memory in answers])
        return answers[max(matches, key=matches.get)] if matches else None

    def matching_questions(self, request, questions):
        needed = telling_words(request)
        telling = [index for index, question in enumerate(questions) if needed <= words_of(question)]
        candidates = [plain_words(questions[index]) for index in telling]
        closes = self.retriever.likeness().to(
            plain_words(request), candidates, ANSWER_SAME_WORDS, ANSWER_SAME_MEANING
        )
        return {index: close for index, close in zip(telling, closes) if close}

    def recalled(self, memory, now):
        return self.update(memory["id"], {"recalled": memory["recalled"] + 1, "last_recalled": stamp(now)})


class Traits:
    TRAITS = "traits"
    COUNTER = "trait_ids"

    def __init__(self, retriever=None):
        self.retriever = retriever

    def apply(self, deltas, now=None):
        changed = [self.applied(delta, stamp(now)) for delta in deltas]
        return [trait for trait in changed if trait]

    def applied(self, delta, at):
        ops = {
            "add": self.add,
            "support": self.support,
            "confirm": self.confirm,
            "reject": self.reject,
            "amend": self.amend,
            "merge": self.merge,
        }
        op = ops.get(delta.get("op"))
        if op is None or (op not in (self.add, self.merge) and not self.open(delta.get("id"))):
            return None
        return op(delta, at)

    def open(self, trait_id):
        trait = Home.store().get(self.TRAITS, trait_id) if trait_id else None
        return trait is not None and trait["status"] != "retired"

    def add(self, delta, at):
        if not delta.get("sources") or names_the_present(delta.get("when", "")):
            return None
        alike = self.most_alike(delta.get("when", ""), delta["do"])
        if alike:
            return (
                None
                if alike["status"] == "retired"
                else self.alike_added(dict(delta, id=alike["id"]), alike, at)
            )
        return self.created(
            delta["about"],
            delta,
            delta.get("confirmed"),
            delta["sources"],
            history_entry("add", at, delta["do"]),
        )

    def merge(self, delta, at):
        merged = [
            Home.store().get(self.TRAITS, trait_id)
            for trait_id in dict.fromkeys(delta.get("ids") or [])
            if self.open(trait_id)
        ]
        if len(merged) < 2:
            return None
        merged_ids = [old["id"] for old in merged]
        trait = self.created(
            max(merged, key=trait_number)["about"],
            delta,
            any(old["status"] == "confirmed" for old in merged),
            [source for old in merged for source in old["sources"]],
            history_entry("merge", at, " ".join(merged_ids)),
        )
        why = f"merged into {trait['id']}"
        for trait_id in merged_ids:
            self.changed(trait_id, at, "merge", why, lambda old: {"status": "retired", "why": why})
        Questions().merged(merged_ids, trait["id"])
        return trait

    def created(self, about, delta, confirmed, sources, history):
        trait = {
            "id": f"t{self.next_number()}",
            "about": about,
            "when": delta.get("when", ""),
            "do": delta["do"],
            "gist": "",
            "status": "confirmed" if confirmed or rests_on_episodes(sources) else "candidate",
            "sources": list(dict.fromkeys(sources)),
            "history": [history],
        }
        Home.store().put(self.TRAITS, trait["id"], trait)
        return trait

    def most_alike(self, when, do):
        traits = self.all()
        likeness = self.retriever.likeness() if self.retriever else WordsLikeness()
        closes = likeness.to(
            likeness_text(when, do),
            [likeness_text(trait["when"], trait["do"]) for trait in traits],
            SAME_FACT_SIMILARITY,
            SAME_MEANING,
        )
        alike = [(close, trait) for close, trait in zip(closes, traits) if close]
        return max(alike, key=lambda pair: pair[0], default=(0.0, None))[1]

    def alike_added(self, delta, alike, at):
        supported = self.support(delta, at)
        confirming = delta.get("confirmed") and alike["status"] == "candidate"
        return self.confirm(delta, at) if confirming else supported

    def support(self, delta, at):
        return self.sourced(delta, at, "support", {})

    def confirm(self, delta, at):
        return self.sourced(delta, at, "confirm", {"status": "confirmed"})

    def sourced(self, delta, at, event, changes):
        sources = [source for source in delta.get("sources") or [] if source != delta["id"]]
        if not sources:
            return None
        return self.changed(
            delta["id"],
            at,
            event,
            " ".join(sources),
            lambda trait: sourced_changes(list(dict.fromkeys(trait["sources"] + sources)), changes),
        )

    def reject(self, delta, at):
        why = delta.get("why", "")
        return self.changed(delta["id"], at, "reject", why, lambda trait: {"status": "retired", "why": why})

    def amend(self, delta, at):
        trait = Home.store().get(self.TRAITS, delta["id"])
        when, do = delta.get("when", trait["when"]), delta.get("do", trait["do"])
        return self.changed(
            delta["id"],
            at,
            "amend",
            likeness_text(when, do),
            lambda trait: {"when": when, "do": do, "gist": ""},
        )

    def changed(self, trait_id, at, event, text, changes):
        return Home.store().update(
            self.TRAITS,
            trait_id,
            lambda trait: dict(
                trait, **changes(trait), history=trait["history"] + [history_entry(event, at, text)]
            ),
        )

    def gisted(self, trait_id, gist):
        return Home.store().update(self.TRAITS, trait_id, lambda trait: dict(trait, gist=gist))

    def next_number(self):
        return Home.store().update(self.COUNTER, "last", lambda last: (last or 0) + 1)

    def all(self):
        return list(Home.store().items(self.TRAITS).values())

    def living(self):
        return [trait for trait in self.all() if trait["status"] != "retired"]

    def confirmed(self):
        return [trait for trait in self.all() if trait["status"] == "confirmed"]

    def relevant(self, text, room=TRAITS_CHARS):
        confirmed = sorted(self.confirmed(), key=trait_number)
        always = sorted(
            (trait for trait in confirmed if not trait["when"]), key=lambda trait: -len(trait["sources"])
        )
        conditional = [trait for trait in confirmed if trait["when"]]
        hits = self.retriever.by_words(
            text, [likeness_text(trait["when"], trait["do"]) for trait in conditional]
        )
        return within_room(
            [trait_line(trait) for trait in always + [conditional[index] for index in hits]], room
        )

    def candidates(self):
        return [trait for trait in self.all() if trait["status"] == "candidate"]


def word_likeness(text, other):
    these, those = set(words(text)), set(words(other))
    return len(these & those) / len(these | those) if these | those else 1.0


def without_repeats(items):
    return list(dict.fromkeys(items))


class Questions:
    QUESTIONS = "questions"
    COUNTER = "question_ids"

    def __init__(self, retriever=None):
        self.retriever = retriever

    def add(self, kind, about, text, thought, expected, traits, sources, now=None):
        text, thought, expected = without_ids(text), without_ids(thought), without_ids(expected)
        alike = self.like(text, self.with_status("pending"))
        if alike:
            self.absorb(alike, traits, sources)
            return None
        if kind == "open" and self.like(text, self.with_status("answered")):
            return None
        question = {
            "id": f"q{self.next_number('last')}",
            "kind": kind,
            "about": about,
            "text": text,
            "thought": thought,
            "expected": expected,
            "traits": list(traits),
            "sources": list(sources),
            "status": "pending",
            "answer": "",
            "at": stamp(now),
            "turn": self.next_number("turn"),
            "skips": 0,
        }
        Home.store().put(self.QUESTIONS, question["id"], question)
        return question

    def with_status(self, status):
        return [question for question in self.all() if question["status"] == status]

    def like(self, text, kept):
        meanings = self.meanings(text, [question["text"] for question in kept]) or [0.0] * len(kept)
        return next(
            (
                question
                for question, meaning in zip(kept, meanings)
                if word_likeness(text, question["text"]) >= SAME_FACT_SIMILARITY
                or meaning >= QUESTION_SAME_MEANING
            ),
            None,
        )

    def meanings(self, text, texts):
        return self.retriever.similarities(text, texts) if self.retriever else None

    def absorb(self, kept, traits, sources):
        absorbed = dict(
            kept,
            traits=without_repeats(kept["traits"] + traits),
            sources=without_repeats(kept["sources"] + sources),
        )
        Home.store().put(self.QUESTIONS, kept["id"], absorbed)

    def pending(self):
        traits = Traits().all()
        no_longer_candidates = {trait["id"] for trait in traits if trait["status"] != "candidate"}
        waiting = [
            question
            for question in self.with_status("pending")
            if not self.settled(question, no_longer_candidates)
        ]
        never_skipped = [question for question in waiting if question["skips"] == 0]
        skipped = sorted(
            (question for question in waiting if question["skips"] > 0), key=lambda question: question["turn"]
        )
        return self.in_kept_order(never_skipped, traits) + skipped

    def settled(self, question, no_longer_candidates):
        about_traits = question["kind"] == "confirm" and question["traits"]
        return bool(about_traits) and set(question["traits"]) <= no_longer_candidates

    def in_kept_order(self, questions, traits):
        support = {trait["id"]: len(trait["sources"]) for trait in traits}
        confirms = [question for question in questions if question["kind"] == "confirm"]
        opens = [question for question in questions if question["kind"] != "confirm"]
        best_supported_first = sorted(
            confirms, key=lambda question: (-self.support(question, support), question["turn"])
        )
        return best_supported_first + sorted(opens, key=lambda question: question["turn"])

    def support(self, question, support):
        return max((support.get(trait_id, 0) for trait_id in question["traits"]), default=0)

    def next(self):
        return next(iter(self.pending()), None)

    def answer(self, question_id, text, now=None):
        return self.changed(
            question_id, lambda question: {"status": "answered", "answer": text, "at": stamp(now)}
        )

    def drop(self, question_id, why, now=None):
        return self.changed(
            question_id, lambda question: {"status": "dropped", "answer": why, "at": stamp(now)}
        )

    def skip(self, question_id, now=None):
        return self.changed(
            question_id,
            lambda question: {
                "at": stamp(now),
                "turn": self.next_number("turn"),
                "skips": question["skips"] + 1,
            },
        )

    def merged(self, trait_ids, merged_id):
        for question in self.all():
            if question["kind"] == "confirm" and set(question["traits"]) & set(trait_ids):
                self.changed(
                    question["id"],
                    lambda question: {"traits": with_merged(question["traits"], trait_ids, merged_id)},
                )

    def changed(self, question_id, changes):
        with Home.store().transaction(write=True):
            question = Home.store().get(self.QUESTIONS, question_id)
            if question is None or question["status"] != "pending":
                return None
            return Home.store().update(
                self.QUESTIONS, question_id, lambda question: dict(question, **changes(question))
            )

    def next_number(self, key):
        return Home.store().update(self.COUNTER, key, lambda last: (last or 0) + 1)

    def all(self):
        return list(Home.store().items(self.QUESTIONS).values())


def likeness_text(when, do):
    return f"{when} {do}".strip()


def sourced_changes(sources, changes):
    obvious = {"status": "confirmed"} if rests_on_episodes(sources) else {}
    return {"sources": sources, **obvious, **changes}


def rests_on_episodes(sources):
    episodes = {source for source in sources if re.fullmatch(r"e\d+", source)}
    return len(episodes) >= TRAIT_OBVIOUS_SOURCES


def names_the_present(when):
    phrases = "|".join(re.escape(phrase) for phrase in PRESENT_WHENS)
    return re.search(rf"\b({phrases})\b", when, re.IGNORECASE) is not None


class WordsLikeness:
    def to(self, text, others, least_words, least_meaning):
        closes = [similarity(text, other) for other in others]
        return [close if close >= least_words else 0.0 for close in closes]


def history_entry(event, at, text):
    return {"event": event, "at": at, "text": text}


def trait_number(trait):
    return int(trait["id"].removeprefix("t"))


def with_merged(traits, merged_ids, merged_id):
    return list(dict.fromkeys(merged_id if trait_id in merged_ids else trait_id for trait_id in traits))


def trait_line(trait):
    return f"- {trait_said(trait)}"


def trait_said(trait):
    said = trait["gist"] or trait["do"]
    return f"{trait['when']}: {said}" if trait["when"] else said


def within_room(lines, room):
    kept = []
    for line in lines:
        if len("\n".join(kept + [line])) > room:
            break
        kept.append(line)
    return "\n".join(kept)


def lasts_found(kind, lasts):
    return kind != "answer" or (isinstance(lasts, str) and lasts in ANSWER_LASTS_DAYS)


def telling_words(text):
    return words_of(text) - ANSWER_COMMON_WORDS


def words_of(text):
    return set(plain_words(text).split())


def plain_words(text):
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def recall_text(item):
    if "gist" in item and "text" in item:
        return f"{item['gist']} {item['text']}"
    return " ".join(
        text for key, value in item.items() if key not in KEPT_WHOLE for text in strings_in(value)
    )


def strings_in(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return strings_in(list(value.values()))
    if isinstance(value, list):
        return [text for inner in value for text in strings_in(inner)]
    return []


def stamp(now):
    return (now or datetime.datetime.now()).isoformat(timespec="seconds")


def outputs_cut(value):
    if isinstance(value, dict):
        return {
            key: item[:EPISODE_FOUND_CHARS]
            if key == "output" and isinstance(item, str)
            else outputs_cut(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [outputs_cut(item) for item in value]
    return value


def gist(value):
    if isinstance(value, dict):
        return {key: item if key in KEPT_WHOLE else gist(item) for key, item in value.items()}
    if isinstance(value, list):
        return [gist(item) for item in value]
    if isinstance(value, str):
        return value[:EPISODE_GIST_CHARS]
    return value


def show_memories(page=1):
    living = sorted(Memories(None).living(), key=lambda memory: memory["last_seen"], reverse=True)
    shown, pages = Numbered.page(living, page)
    if shown:
        with say.part("Memories", "🧠"):
            for memory in shown:
                say.item(memory_line(memory))
        if page < pages:
            say.info(next_page_line(page, pages, f"memory {page + 1}"), "👉")
    elif Home.store().items(Memories.MEMORIES):
        say.info("No memory is kept now: all of them are retired.", "📭")
    else:
        say.info("Nothing remembered yet: a dream makes them from what you do.", "📭")
    say.info(memory_state(), "📊")
    say.info(
        "tinker memory show ID shows one; tinker memory forget ID deletes one; "
        "tinker memory traits shows the traits; tinker dream run makes memories now.",
        "👉",
    )


def show_remembered(item_id):
    item, collection = remembered(item_id)
    SHOWN_BY_COLLECTION[collection](item)


def forget(item_id, yes=False):
    if item_id.startswith("t"):
        forget_trait(item_id)
        return
    item, collection = remembered(item_id)
    confirm_deletion(
        "This forgets for good:",
        [f"{NOUN_OF[collection]} {item_id}: {heading_of(item)}"],
        f"tinker memory forget {item_id}",
        yes,
    )
    Home.store().delete(collection, item_id)
    say.done(f"Forgot {item_id}.")


def forget_trait(trait_id):
    if not Traits().open(trait_id):
        raise SystemExit(
            f"tinker: no trait {trait_id} to forget: tinker memory traits lists the traits by id."
        )
    Traits().apply([{"op": "reject", "id": trait_id, "why": "forgotten"}])
    say.done(f"Forgot {trait_id}.")


def show_traits():
    confirmed, candidates = Traits().confirmed(), Traits().candidates()
    if not confirmed and not candidates:
        say.info(
            "No traits yet: a dream learns them from what you do, and tinker interact asks about them.", "📭"
        )
        return
    for about in TRAIT_ABOUTS:
        shown = [trait for trait in by_number(confirmed) if trait["about"] == about]
        if shown:
            with say.part(f"Traits: {about}", "🪞"):
                for trait in shown:
                    say.item(trait_view_line(trait))
    if candidates:
        with say.part("Candidates", "🌱"):
            for trait in by_number(candidates):
                say.item(f"{trait_view_line(trait)} · {counted(len(trait['sources']), 'source')}")
    pending = Questions().pending()
    if pending:
        with say.part("Questions", "💭"):
            for question in pending:
                say.item(question_view_line(question))
    waiting = len(pending)
    say.info(
        f"{counted(waiting, 'question waits', 'questions wait')}: tinker interact asks {'it' if waiting == 1 else 'them'}.",
        "👉",
    )


def question_view_line(question):
    line = f"{question['id']} {question['text']}"
    return f"{line} · {question['thought']}" if question["thought"] else line


def by_number(traits):
    return sorted(traits, key=lambda trait: int(trait["id"][1:]))


def trait_view_line(trait):
    return (
        f"{trait['id']} {trait['when']}: {trait['do']}" if trait["when"] else f"{trait['id']} {trait['do']}"
    )


def remembered(item_id):
    collection = COLLECTION_OF_PREFIX.get(item_id[:1])
    item = collection and Home.store().get(collection, item_id)
    if not item:
        raise SystemExit(f"tinker: no memory or episode {item_id}: tinker memory lists the memories by id.")
    return item, collection


def show_memory(memory):
    with say.part(memory_line(memory), "🧠"):
        say.text(memory["text"])
        say.info(
            f"first seen {memory['first_seen']} · last seen {memory['last_seen']} · "
            f"recalled {memory['recalled']} · strength {memory['strength']}"
        )
        if memory["retired"]:
            say.info(f"retired: {memory['retired']}")
    with say.part("Sources", "📎"):
        for episode_id in memory["sources"]:
            say.item(source_line(episode_id))


def show_episode(episode):
    with say.part(f"Episode {episode['id']} {episode['time']}", "📓"):
        say.text(json.dumps(episode, indent=2, ensure_ascii=False))


def source_line(episode_id):
    episode = Home.store().get(Scratchpad.EPISODES, episode_id)
    if not episode:
        return f"{episode_id}: gone"
    return f"{episode_id} {episode['time']}: {heading_of(episode)}"


def heading_of(item):
    return next((item[key] for key in HEADING_KEYS if item.get(key)), item.get("status", ""))


def memory_line(memory):
    return f"{memory['id']} {memory['kind']}: {memory['gist']}"


def memory_state():
    fresh = len(Scratchpad.fresh())
    retired = sum(1 for memory in Home.store().items(Memories.MEMORIES).values() if memory["retired"])
    last = Home.store().get(DREAM, "state", {}).get("last")
    dreamt = f"last dream {last}" if last else "no dream yet"
    return f"{counted(fresh, 'fresh episode')} · {counted(retired, 'retired memory', 'retired memories')} · {dreamt}"


def counted(number, one, many=None):
    return f"{number} {one if number == 1 else many or one + 's'}"


HEADING_KEYS = ("gist", "message", "task", "line")
TRAIT_ABOUTS = ("person", "tinker")
COLLECTION_OF_PREFIX = {"m": Memories.MEMORIES, "e": Scratchpad.EPISODES}
NOUN_OF = {Memories.MEMORIES: "memory", Scratchpad.EPISODES: "episode"}
SHOWN_BY_COLLECTION = {Memories.MEMORIES: show_memory, Scratchpad.EPISODES: show_episode}
