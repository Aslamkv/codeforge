import json
import re

from tinkerkit.conversation import md
from tinkerkit.dreaming import TRAIT_ABOUTS, asked, cited_only, listed, questions_text, suggestions_of
from tinkerkit.prompts import Prompts
from tinkerkit.remembering import Questions, Scratchpad, Traits

HINT = '_a number, a number with changes ("2, but on weekdays"), your own words, or /skip_'


class Interview:
    def __init__(self, questions=None):
        self.questions = questions or Questions()
        self.shown_to = {}

    def turn(self, chat, now=None):
        question = self.questions.next() or self.question_asked(now)
        if question is None:
            return None
        self.shown_to[chat] = question["id"]
        return self.shown(question)

    def current(self, chat):
        pending = self.questions.pending()
        shown = [question for question in pending if question["id"] == self.shown_to.get(chat)]
        return next(iter(shown + pending), None)

    def shown(self, question):
        return "\n".join([md(t"💬 {question['text']}"), *self.suggestion_lines(question), HINT])

    def suggestion_count(self, chat):
        question = self.current(chat)
        return len(question["suggestions"]) if question else 0

    def suggestion_lines(self, question):
        return [md(t"{number}. {suggestion}") for number, suggestion in enumerate(question["suggestions"], 1)]

    def question_asked(self, now):
        reply = asked(Prompts.INTERACT_SYSTEM, questions_text(Traits().confirmed(), self.questions.all()))
        if not isinstance(reply, dict) or reply.get("done") is True:
            return None
        return self.question_kept(reply, [], now)

    def question_kept(self, item, sources, now):
        text = item.get("text")
        if not (isinstance(text, str) and text):
            return None
        return self.questions.add("open", text, suggestions_of(item), [], sources, now)

    def skipped(self, chat):
        question = self.current(chat)
        if question is None:
            return None
        self.questions.skip(question["id"])
        return self.turn(chat)

    def answered(self, chat, text, now=None):
        question = self.current(chat)
        if question is None:
            return self.turn(chat, now)
        if out_of_range(question, text):
            return f"Pick 1–{len(question['suggestions'])}, your own words, or /skip.\n{self.shown(question)}"
        text = expanded(question, text)
        reply = asked(answer_system(question), answer_text(question, text))
        if not isinstance(reply, dict):
            return f"Couldn't read that answer; here it is again.\n{self.shown(question)}"
        episode = Scratchpad.write(
            {"chat": chat, "kind": "interact", "question": question["text"], "answer": text}, now
        )
        Scratchpad.dreamt([episode["id"]], now)
        Traits().apply(deltas_of(reply, question["traits"], episode["id"]), now)
        if isinstance(follow_up := follow_up_of(reply), dict):
            self.question_kept(follow_up, [episode["id"]], now)
        self.questions.answer(question["id"], text, now)
        return self.turn(chat, now)


def follow_up_of(reply):
    question = reply.get("question")
    if isinstance(question, str):
        return {"text": question, "suggestions": reply.get("suggestions")}
    return question


def out_of_range(question, text):
    count, number = len(question["suggestions"]), leading_number(text)
    return count > 0 and number is not None and not 1 <= int(number[0]) <= count


def expanded(question, text):
    number = leading_number(text)
    if not question["suggestions"] or number is None:
        return text
    return question["suggestions"][int(number[0]) - 1] + text.strip()[number.end() :]


def leading_number(text):
    return re.match(r"\d+", text.strip())


def answer_system(question):
    return Prompts.INTERACT_ANSWER_SYSTEM if question["traits"] else Prompts.INTERACT_ADD_SYSTEM


def answer_text(question, text):
    asked = {"question": question["text"], "suggestions": question["suggestions"]}
    if question["traits"]:
        asked["traits"] = asked_traits(question)
    return json.dumps(asked | {"reply": text}, ensure_ascii=False)


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
            {"op": "reject", "id": item["id"], "why": text_of(item, "why")}
            for item in listed(reply.get("reject"))
            if item.get("id") in asked_about
        ),
        *(amend_of(item) for item in listed(reply.get("amend")) if item.get("id") in asked_about),
        *(
            {
                "op": "add",
                "about": item["about"],
                "when": text_of(item, "when"),
                "do": item["do"],
                "sources": [source],
                "confirmed": True,
            }
            for item in listed(reply.get("add"))
            if item.get("about") in TRAIT_ABOUTS and text_of(item, "do")
        ),
    ]


def amend_of(item):
    changes = {key: item[key] for key in ("when", "do") if isinstance(item.get(key), str)}
    return {"op": "amend", "id": item["id"], **changes}


def text_of(item, key):
    value = item.get(key)
    return value if isinstance(value, str) else ""
