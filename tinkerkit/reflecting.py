import json

from .model import Models, OllamaError
from .prompts import Prompts, Reply
from .remembering import Memories, Questions, Scratchpad, Traits
from .settings import DREAM_REPLY_TOKENS, REFLECT_EPISODES, REFLECT_ROUNDS, REFLECT_THOUGHTS, Home

TRAIT_ABOUTS = ("person", "tinker")
SHOWN_MEMORY_KEYS = ("id", "kind", "gist", "text", "strength", "recalled")
SIDE_TRAIT_KEYS = ("id", "when", "do")
PENDING_KEYS = ("text", "thought", "expected")


class Reflection:
    def __init__(self, thoughts=(), logged=lambda name, record: None):
        self.earlier = list(thoughts)[-REFLECT_THOUGHTS:]
        self.thoughts = []
        self.logged = logged

    def run(self, now=None):
        rounds = []
        for _ in range(REFLECT_ROUNDS):
            kept = self.round(now)
            if kept is None:
                break
            rounds.append(kept)
            if kept["done"] or not kept["kept"]:
                break
        return rounds

    def round(self, now=None):
        seen = evidence()
        reply = asked(Prompts.REFLECT_SYSTEM, reflection_text(seen, self.earlier + self.thoughts))
        if not isinstance(reply, dict):
            return None
        thought = said(reply, "thought")
        if thought:
            self.thoughts.append(thought)
        given = {item["id"] for item in seen["episodes"] + seen["memories"]}
        confirms = [trait_added(item, given, now, self.logged) for item in listed(reply.get("add"))]
        opens = [question_kept(item, now, self.logged) for item in listed(reply.get("questions"))]
        kept = [question for question in confirms + opens if question]
        return {"thought": thought, "kept": kept, "done": reply.get("done") is True}


def evidence():
    episodes = [episode for episode in Home.store().items(Scratchpad.EPISODES).values() if episode["dreamt"]]
    newest = sorted(episodes, key=lambda episode: episode["dreamt_time"], reverse=True)[:REFLECT_EPISODES]
    people = [memory for memory in Memories(None).living() if memory["kind"] == "person"]
    return {
        "episodes": [{"id": episode["id"], "gist": episode.get("gist", "")} for episode in newest],
        "memories": shown(people),
    }


def reflection_text(seen, thoughts):
    traits, pending = Traits().all(), Questions().pending()
    sides = {about: side(about, traits, pending) for about in TRAIT_ABOUTS}
    return json.dumps(
        sides | {"evidence": seen, "thoughts": thoughts, "waiting": len(pending)},
        ensure_ascii=False,
    )


def side(about, traits, pending):
    own = [trait for trait in traits if trait["about"] == about]
    return {
        "confirmed": traits_shown(own, "confirmed"),
        "candidates": traits_shown(own, "candidate"),
        "pending": [
            {key: question[key] for key in PENDING_KEYS} for question in pending if question["about"] == about
        ],
        "retired": [
            {"when": trait["when"], "do": trait["do"], "why": trait.get("why", "")}
            for trait in own
            if trait["status"] == "retired"
        ],
    }


def traits_shown(traits, status):
    return [{key: trait[key] for key in SIDE_TRAIT_KEYS} for trait in traits if trait["status"] == status]


def trait_added(item, given, now, logged):
    if item.get("about") not in TRAIT_ABOUTS or not said(item, "do"):
        return None
    sources = cited_only(item.get("sources"), given)
    delta = {
        "op": "add",
        "about": item["about"],
        "when": said(item, "when"),
        "do": item["do"],
        "sources": sources,
    }
    confirms = []
    for trait in Traits().apply([delta], now):
        if newly_added(trait):
            logged("add", trait)
            confirms.append(confirm_asked(item, trait, sources, now, logged))
        else:
            logged("support", trait)
    return next((question for question in confirms if question), None)


def confirm_asked(item, trait, sources, now, logged):
    question = said(item, "question")
    if not question:
        return None
    kept = Questions().add(
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
    question = Questions().add(
        "open", about, text, said(item, "thought"), said(item, "expected"), [], [], now
    )
    if question:
        logged("question", question)
    return question


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
        return reply_read(text, system)
    except OllamaError, ValueError:
        return None


def reply_read(text, system):
    return Reply(text).json(system)


def listed(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
