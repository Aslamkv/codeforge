import json

from tinkerkit.conversation import ALL_FOR_NOW, Turn, md
from tinkerkit.prompts import Prompts
from tinkerkit.reflecting import (
    TRAIT_ABOUTS,
    Reflection,
    asked,
    cited_only,
    listed,
    question_kept,
    said,
    traits_shown,
)
from tinkerkit.remembering import Questions, Scratchpad, Traits
from tinkerkit.settings import INTERACT_TURNS, Home

UNREADABLE = "Couldn't read that answer; here it is again."


class Interview:
    def __init__(self, questions=None):
        self.questions = questions or Questions()
        self.shown_to = {}

    def turn(self, chat, now=None, say=""):
        question = self.questions.next() or first_kept(Reflection().round(now))
        if question is None:
            return Turn(after(say, ALL_FOR_NOW), True)
        self.shown_to[chat] = question["id"]
        return Turn(after(say, self.shown(question)), False)

    def current(self, chat):
        pending = self.questions.pending()
        shown = [question for question in pending if question["id"] == self.shown_to.get(chat)]
        return next(iter(shown + pending), None)

    def shown(self, question):
        return md(
            t"💭 I've been thinking: {question['thought']} {question['text']} My guess: {question['expected']}"
        )

    def skipped(self, chat):
        question = self.current(chat)
        if question:
            self.questions.skip(question["id"])
        return self.turn(chat)

    def answered(self, chat, text, now=None):
        question = self.current(chat)
        if question is None:
            return self.turn(chat, now)
        reply = asked(
            Prompts.INTERACT_TALK_SYSTEM, talk_text(chat, question, self.waiting_after(question), text)
        )
        if not isinstance(reply, dict):
            return Turn(f"{UNREADABLE}\n{self.shown(question)}", False)
        say = said(reply, "say")
        episode = Scratchpad.write(
            {"chat": chat, "kind": "interact", "question": question["text"], "answer": text, "say": say}, now
        )
        Scratchpad.dreamt([episode["id"]], now)
        Traits().apply(deltas_of(reply, question["traits"], episode["id"]), now)
        if isinstance(reply.get("question"), dict):
            question_kept(reply["question"], now, lambda name, record: None)
        if reply.get("settled") is not True:
            return Turn(say or self.shown(question), False)
        self.questions.answer(question["id"], text, now)
        return self.turn(chat, now, say)

    def waiting_after(self, question):
        return next(
            (waiting for waiting in self.questions.pending() if waiting["id"] != question["id"]), None
        )


def first_kept(round_kept):
    return round_kept["kept"][0] if round_kept and round_kept["kept"] else None


def after(say, text):
    return f"{say}\n\n{text}" if say else text


def talk_text(chat, question, waiting, message):
    traits = Traits().all()
    return json.dumps(
        {
            "traits": {
                about: traits_shown([trait for trait in traits if trait["about"] == about], "confirmed")
                for about in TRAIT_ABOUTS
            },
            "question": {
                "text": question["text"],
                "thought": question["thought"],
                "expected": question["expected"],
                "traits": asked_traits(question),
            },
            "next": waiting["text"] if waiting else None,
            "dialogue": dialogue(chat),
            "message": message,
        },
        ensure_ascii=False,
    )


def dialogue(chat):
    turns = sorted(
        (
            episode
            for episode in Home.store().items(Scratchpad.EPISODES).values()
            if episode["chat"] == chat and episode.get("kind") == "interact"
        ),
        key=lambda episode: (episode["time"], int(episode["id"].removeprefix("e"))),
    )
    return [
        {"question": turn["question"], "answer": turn["answer"], "say": turn.get("say", "")}
        for turn in turns[-INTERACT_TURNS:]
    ]


def asked_traits(question):
    traits = {trait["id"]: trait for trait in Traits().all()}
    return [
        {
            "id": trait["id"],
            "about": trait["about"],
            "when": trait["when"],
            "do": trait["gist"] or trait["do"],
        }
        for trait in (traits[trait_id] for trait_id in question["traits"] if trait_id in traits)
    ]


def deltas_of(reply, asked_about, source):
    return [
        *(
            {"op": "confirm", "id": trait_id, "sources": [source]}
            for trait_id in cited_only(reply.get("confirm"), asked_about)
        ),
        *(
            {"op": "reject", "id": item["id"], "why": said(item, "why")}
            for item in listed(reply.get("reject"))
            if item.get("id") in asked_about
        ),
        *(amend_of(item) for item in listed(reply.get("amend")) if item.get("id") in asked_about),
        *(
            {
                "op": "add",
                "about": item["about"],
                "when": said(item, "when"),
                "do": item["do"],
                "sources": [source],
                "confirmed": True,
            }
            for item in listed(reply.get("add"))
            if item.get("about") in TRAIT_ABOUTS and said(item, "do")
        ),
    ]


def amend_of(item):
    changes = {key: item[key] for key in ("when", "do") if isinstance(item.get(key), str)}
    return {"op": "amend", "id": item["id"], **changes}
