import datetime
import json
import re

from .messages import say
from .settings import (
    ANSWER_COMMON_WORDS,
    ANSWER_LASTS_DAYS,
    ANSWER_SAME_MEANING,
    ANSWER_SAME_WORDS,
    EPISODE_FOUND_CHARS,
    EPISODE_GIST_CHARS,
    EPISODE_KEPT_DAYS,
    PORTRAIT_WORDS,
    RECALL_MEANING,
    RECALL_SHOWN,
    Home,
    Numbered,
    confirm_deletion,
)
from .text import similarity

EPISODE_BOOKKEEPING = frozenset({"id", "chat", "time", "dreamt", "dreamt_time"})
KEPT_WHOLE = EPISODE_BOOKKEEPING | {"status"}


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
    def prune(cls, now):
        oldest = now - datetime.timedelta(days=EPISODE_KEPT_DAYS)
        for episode_id, episode in Home.store().items(cls.EPISODES).items():
            if episode["dreamt"] and datetime.datetime.fromisoformat(episode["dreamt_time"]) < oldest:
                Home.store().delete(cls.EPISODES, episode_id)


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

    def next_number(self):
        return Home.store().update(self.COUNTER, "last", lambda last: (last or 0) + 1)

    def update(self, memory_id, changes):
        return Home.store().update(self.MEMORIES, memory_id, lambda memory: dict(memory, **changes))

    def retire(self, memory_id, why):
        return self.update(memory_id, {"retired": why})

    def recall(self, request, now=None, kinds=None):
        living = [memory for memory in self.living() if kinds is None or memory["kind"] in kinds]
        candidates = living + Scratchpad.fresh()
        found = [
            candidates[index] for index in self.hits(request, [recall_text(item) for item in candidates])
        ]
        return [self.recalled(item, now) if item["id"].startswith("m") else item for item in found]

    def living(self):
        return [memory for memory in Home.store().items(self.MEMORIES).values() if not memory["retired"]]

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
        if not telling:
            return {}
        plain = plain_words(request)
        candidates = [plain_words(questions[index]) for index in telling]
        meanings = self.retriever.similarities(plain, candidates)
        closeness, least = (
            (meanings, ANSWER_SAME_MEANING)
            if meanings
            else ([similarity(plain, candidate) for candidate in candidates], ANSWER_SAME_WORDS)
        )
        return {index: close for index, close in zip(telling, closeness) if close >= least}

    def recalled(self, memory, now):
        return self.update(memory["id"], {"recalled": memory["recalled"] + 1, "last_recalled": stamp(now)})


class Portrait:
    DREAM = "dream"
    KEY = "portrait"

    @classmethod
    def read(cls):
        return Home.store().get(cls.DREAM, cls.KEY, "")

    @classmethod
    def write(cls, text):
        Home.store().put(cls.DREAM, cls.KEY, " ".join(text.split()[:PORTRAIT_WORDS]))


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
            say.info(f"Page {page} of {pages}: tinker memory {page + 1} for more.", "👉")
    else:
        say.info("Nothing remembered yet: a dream makes them from what you do.", "📭")
    say.info(memory_state(), "📊")
    say.info(
        "tinker memory show ID shows one; tinker memory forget ID deletes one; "
        "tinker memory portrait shows the portrait; tinker dream run makes memories now.",
        "👉",
    )


def show_remembered(item_id):
    item, collection = remembered(item_id)
    SHOWN_BY_COLLECTION[collection](item)


def forget(item_id, yes=False):
    item, collection = remembered(item_id)
    confirm_deletion(
        "This forgets for good:",
        [f"{NOUN_OF[collection]} {item_id}: {heading_of(item)}"],
        f"tinker memory forget {item_id}",
        yes,
    )
    Home.store().delete(collection, item_id)
    say.done(f"Forgot {item_id}.")


def show_portrait():
    portrait = Portrait.read()
    if not portrait:
        say.info("No portrait yet: a dream writes it from what you do.", "📭")
        return
    with say.part("Who you are", "🪞"):
        say.text(portrait)
    people = [memory for memory in Memories(None).living() if memory["kind"] == "person"]
    with say.part("From these memories", "🧠"):
        for memory in people:
            say.item(memory_line(memory))


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
    last = Home.store().get(Portrait.DREAM, "state", {}).get("last")
    dreamt = f"last dream {last}" if last else "no dream yet"
    return f"{counted(fresh, 'fresh episode')} · {counted(retired, 'retired memory', 'retired memories')} · {dreamt}"


def counted(number, one, many=None):
    return f"{number} {one if number == 1 else many or one + 's'}"


HEADING_KEYS = ("gist", "message", "task", "line")
COLLECTION_OF_PREFIX = {"m": Memories.MEMORIES, "e": Scratchpad.EPISODES}
NOUN_OF = {Memories.MEMORIES: "memory", Scratchpad.EPISODES: "episode"}
SHOWN_BY_COLLECTION = {Memories.MEMORIES: show_memory, Scratchpad.EPISODES: show_episode}
