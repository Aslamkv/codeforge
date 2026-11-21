import json

from ..jobs import Runner
from ..logger import note
from ..model.models import Models, OllamaError
from ..prompts import Prompts, Reply
from ..ranking import Embeddings, Retriever
from ..settings import (
    DREAM_REPLY_TOKENS,
    REFLECT_ANSWERED,
    REFLECT_EPISODES,
    REFLECT_GAPS,
    REFLECT_ROUNDS,
    REFLECT_THOUGHTS,
    Home,
)
from .memories import TRAIT_ABOUTS, Memories, Notes, Questions, Scratchpad, Traits
from .routes import Routes

REFLECTED_WHY = "reflected: "
NOT_TURNED_DOWN_WHYS = ("forgotten", "replaced by ", REFLECTED_WHY)
SHOWN_MEMORY_KEYS = ("id", "kind", "gist", "text", "strength", "recalled")
SIDE_TRAIT_KEYS = ("id", "when", "do")
PENDING_KEYS = ("id", "text", "thought", "expected")


class Reflection:
    def __init__(self, thoughts=(), logged=lambda name, record: None):
        self.earlier = list(thoughts)[-REFLECT_THOUGHTS:]
        self.thoughts = []
        self.logged = logged
        self.learned = False

    def run(self, now=None):
        for _ in range(REFLECT_ROUNDS):
            kept = self.round(now)
            if kept is None:
                return
            yield kept
            if kept["done"] or not kept_anything(kept):
                return

    def round(self, now=None):
        seen, shown_gaps = evidence(), holding({"gaps": gaps()}).get("gaps", {})
        system = Prompts.REFLECT_SYSTEM + (Prompts.REFLECT_GAPS_GUIDE if shown_gaps else "")
        reply = asked(system, reflection_text(seen, shown_gaps, self.earlier + self.thoughts))
        if not isinstance(reply, dict):
            return None
        thought = said(reply, "thought")
        if thought:
            self.thoughts.append(thought)
        given = {item["id"] for item in seen["episodes"] + seen["memories"]}
        confirms = [trait_added(item, given, now, self.logged) for item in listed(reply.get("add"))]
        opens = [question_kept(item, now, self.logged) for item in listed(reply.get("questions"))]
        kept = [question for question in confirms + opens if question]
        return {
            "thought": thought,
            "kept": kept,
            "notes": notes_kept(reply.get("notes"), now),
            "retired": routes_retired(listed(reply.get("retire")), shown_gaps, now),
            "dropped": questions_dropped(listed(reply.get("drop")), now),
            "learn": self.learn_queued(said(reply, "learn").strip()),
            "done": reply.get("done") is True,
        }

    def learn_queued(self, topic):
        if not topic or self.learned:
            return ""
        Runner.queue_idle_job(["learn", topic])
        self.learned = True
        return topic


def kept_anything(kept):
    return any(kept[key] for key in ("kept", "notes", "retired", "learn"))


def notes_kept(notes, now):
    texts = [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []
    return [kept["text"] for kept in (Notes.keep(text, now) for text in texts) if kept]


def routes_retired(retires, shown_gaps, now):
    named = {route["id"] for kind in ("turned down", "failing") for route in shown_gaps.get(kind, [])}
    wanted = [{"id": item["id"], "why": said(item, "why")} for item in retires if item.get("id") in named]
    retired = {
        route["id"]
        for route in Routes().apply(
            [{"op": "retire", "id": item["id"], "why": REFLECTED_WHY + item["why"]} for item in wanted], now
        )
    }
    return [item for item in wanted if item["id"] in retired]


def questions_dropped(drops, now):
    named = [item for item in drops if isinstance(item.get("id"), str)]
    return [item["id"] for item in named if Questions().drop(item["id"], said(item, "why"), now)]


def holding(parts):
    return {name: part for name, part in parts.items() if holds_something(part)}


def holds_something(value):
    if isinstance(value, dict):
        return any(holds_something(part) for part in value.values())
    return bool(value)


def evidence():
    episodes = [episode for episode in Home.store().items(Scratchpad.EPISODES).values() if episode["dreamt"]]
    newest = sorted(episodes, key=lambda episode: episode["dreamt_time"], reverse=True)
    people = [memory for memory in Memories(None).living() if memory["kind"] == "person"]
    return {"episodes": own_words(newest)[:REFLECT_EPISODES], "memories": shown(people)}


def own_words(episodes):
    kinds = ((episode, kind_of(episode)) for episode in episodes)
    return [
        {"id": episode["id"], "words": episode[kind.text]}
        for episode, kind in kinds
        if kind and not kind.failed(episode)
    ]


class ChatEpisode:
    name, shape, text = "chat", "message", "message"

    @staticmethod
    def failed(episode):
        return episode.get("status") == "failed"


class InstructionEpisode:
    name, shape, text = "instruction", "code", "request"

    @staticmethod
    def failed(episode):
        return episode["code"] != 0


class BuildEpisode:
    name, shape, text = "build", "task", "task"

    @staticmethod
    def failed(episode):
        return episode.get("status") == "blocked"


EPISODE_KINDS = (ChatEpisode, InstructionEpisode, BuildEpisode)


def kind_of(episode):
    return next((kind for kind in EPISODE_KINDS if kind.shape in episode), None)


def went_wrong(episode):
    kind = kind_of(episode)
    return kind is not None and kind.failed(episode)


def gaps():
    episodes = newest_episodes()
    routes = newest_routes()
    failed = [episode for episode in episodes if went_wrong(episode)]
    return {
        "turned down": [turned_down_shown(route) for route in routes if turned_down(route)][:REFLECT_GAPS],
        "failing": failing_routes(routes, failed)[:REFLECT_GAPS],
        "failed": [failed_shown(episode) for episode in failed][:REFLECT_GAPS],
    }


def newest_episodes():
    episodes = Home.store().items(Scratchpad.EPISODES).values()
    return sorted(episodes, key=lambda episode: episode["time"], reverse=True)


def newest_routes():
    return sorted(Routes().all(), key=lambda route: route["history"][-1]["at"], reverse=True)


def turned_down(route):
    return route["status"] == "retired" and not route["retired"].startswith(NOT_TURNED_DOWN_WHYS)


def turned_down_shown(route):
    return {"id": route["id"], "pattern": route["pattern"], "line": route["line"], "why": route["retired"]}


def failing_routes(routes, failed):
    shown = [failing_shown(route, failed) for route in routes if route["status"] != "retired"]
    return [route for route in shown if route["failed"]]


def failing_shown(route, failed):
    count = sum(
        1 for episode in failed if kind_of(episode) is ChatEpisode and episode.get("route") == route["id"]
    )
    return {
        "id": route["id"],
        "pattern": route["pattern"],
        "line": route["line"],
        "uses": route["uses"],
        "failed": count,
    }


def failed_shown(episode):
    return {"id": episode["id"], "gist": episode.get("gist") or episode.get(kind_of(episode).text, "")}


def self_facts():
    routes = Routes().all()
    living = [route for route in routes if route["status"] != "retired"]
    episodes = list(Home.store().items(Scratchpad.EPISODES).values())
    builds = of_kind(episodes, BuildEpisode)
    return {
        "routes living": len(living),
        "routes confirmed": sum(1 for route in living if route["status"] == "confirmed"),
        "route uses": sum(route["uses"] for route in living),
        "routes turned down": sum(1 for route in routes if turned_down(route)),
        "failed": {kind.name: failed_count(of_kind(episodes, kind)) for kind in EPISODE_KINDS},
        "builds done": {
            "done": sum(1 for build in builds if build.get("status") == "done"),
            "all": len(builds),
        },
    }


def of_kind(episodes, kind):
    return [episode for episode in episodes if kind_of(episode) is kind]


def failed_count(episodes):
    return {"failed": sum(1 for episode in episodes if went_wrong(episode)), "all": len(episodes)}


def reflection_text(seen, shown_gaps, thoughts):
    traits, pending = Traits().all(), Questions().pending()
    answered = Questions().with_status("answered")
    sides = {about: side(about, traits, pending, answered) for about in TRAIT_ABOUTS}
    measured = holding(
        {
            "gaps": shown_gaps,
            "self": self_facts() | {"notes": Notes.listed()},
            "thoughts": thoughts,
            "waiting": len(pending),
        }
    )
    return json.dumps(sides | {"evidence": seen} | measured, ensure_ascii=False)


def side(about, traits, pending, answered):
    own = own_traits(traits, about)
    return {
        "confirmed": traits_shown(own, "confirmed"),
        "candidates": traits_shown(own, "candidate"),
        "pending": [shown_question(question) for question in pending if question["about"] == about],
        "answered": [
            {"text": question["text"], "answer": question["answer"]}
            for question in answered
            if question["about"] == about
        ][-REFLECT_ANSWERED:],
        "retired": [
            {"when": trait["when"], "do": trait["do"], "why": trait.get("why", "")}
            for trait in own
            if trait["status"] == "retired"
        ],
    }


def own_traits(traits, about):
    return [trait for trait in traits if trait["about"] == about]


def shown_question(question):
    return {key: question[key] for key in PENDING_KEYS}


def traits_shown(traits, status):
    return [{key: trait[key] for key in SIDE_TRAIT_KEYS} for trait in traits if trait["status"] == status]


def add_delta(item, sources):
    if item.get("about") not in TRAIT_ABOUTS or not said(item, "do"):
        return None
    return {
        "op": "add",
        "about": item["about"],
        "when": said(item, "when"),
        "do": item["do"],
        "sources": sources,
        **({"confirmed": True} if item.get("stated") is True else {}),
    }


def trait_added(item, given, now, logged):
    sources = cited_only(item.get("sources"), given)
    delta = add_delta(item, sources)
    if delta is None:
        return None
    confirms = []
    for trait in Traits().apply([delta], now):
        if newly_added(trait):
            logged("add", trait)
            if trait["status"] == "candidate":
                confirms.append(confirm_asked(item, trait, sources, now, logged))
        else:
            logged("support", trait)
    return next((question for question in confirms if question), None)


def confirm_asked(item, trait, sources, now, logged):
    question = said(item, "question")
    if not question:
        return None
    kept = meaning_questions().add(
        "confirm",
        trait["about"],
        question,
        said(item, "thought"),
        said(item, "expected"),
        [trait["id"]],
        sources,
        now,
    )
    if kept:
        logged("question", kept)
    return kept


def question_kept(item, now, logged):
    text = said(item, "text")
    about = item.get("about") if item.get("about") in TRAIT_ABOUTS else "person"
    if not text:
        return None
    question = meaning_questions().add(
        "open", about, text, said(item, "thought"), said(item, "expected"), [], [], now
    )
    if question:
        logged("question", question)
    return question


def meaning_questions():
    return Questions(Retriever(Embeddings.configured()))


def newly_added(trait):
    return len(trait["history"]) == 1 and trait["history"][0]["event"] == "add"


def cited_only(sources, given):
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, str) and source in given]


def said(item, key):
    text = item.get(key)
    return text if isinstance(text, str) else ""


def shown(memories):
    return [shown_memory(memory) for memory in memories]


def shown_memory(memory):
    return {key: memory[key] for key in SHOWN_MEMORY_KEYS}


def asked(system, content):
    if Models.quick() is None:
        return None
    try:
        text = Models.quick_reply(
            [{"role": "system", "content": system}, {"role": "user", "content": content}],
            DREAM_REPLY_TOKENS,
            "off",
            temperature=0,
        )
    except OllamaError:
        return None
    try:
        return reply_read(text, system)
    except ValueError as problem:
        note(f"Quick reply unread: {type(problem).__name__}: {problem}: {text!r}")
        return None


def reply_read(text, system):
    return Reply(text).json(system)


def listed(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
