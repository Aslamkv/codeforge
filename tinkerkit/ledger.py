from collections import namedtuple
lazy import time

lazy from .settings import GLANCE_KEPT_SECONDS, MESSAGE_KEPT_SECONDS, Home, Numbered

DONE_NOTE_KEPT_SECONDS = 5
ANSWER_WINDOW_SECONDS = 60

ChatMessage = namedtuple("ChatMessage", "chat message")
View = namedtuple("View", "chat message view")


class Ledger:
    VIEWS = "chat views"
    HISTORY = Numbered("chat history", "chat history numbers")

    @classmethod
    def kept(cls, channel, chat, message):
        cls.change(channel, chat, message, until=time.time() + MESSAGE_KEPT_SECONDS)

    @classmethod
    def touched(cls, channel, chat, message):
        view = Home.store().get(cls.VIEWS, key(channel, chat, message))
        if view and view.get("until") and not view.get("glance"):
            cls.change(channel, chat, message, until=time.time() + MESSAGE_KEPT_SECONDS)

    @classmethod
    def glance(cls, channel, chat, messages, keep=None):
        for message in messages:
            cls.change(channel, chat, message, until=time.time() + (keep or GLANCE_KEPT_SECONDS), glance=True)

    @classmethod
    def lasting(cls, channel, chat, message):
        cls.change(channel, chat, message, until=None)

    @classmethod
    def is_lasting(cls, channel, chat, message):
        view = Home.store().get(cls.VIEWS, key(channel, chat, message))
        return view is not None and view.get("until") is None

    @classmethod
    def change(cls, channel, chat, message, **changes):
        Home.store().update(cls.VIEWS, key(channel, chat, message), lambda view: dict(view or {}, **changes))

    @classmethod
    def gone(cls, channel, chat, message):
        Home.store().delete(cls.VIEWS, key(channel, chat, message))

    @classmethod
    def due(cls, channel):
        return [
            ChatMessage(chat, message)
            for chat, message, view in cls.views(channel)
            if view.get("until") is not None and view["until"] <= time.time()
        ]

    @classmethod
    def glances(cls, channel, chat):
        return [
            message for each, message, view in cls.views(channel) if each == str(chat) and view.get("glance")
        ]

    @classmethod
    def passing(cls, channel):
        return [
            ChatMessage(chat, message)
            for chat, message, view in cls.views(channel)
            if view.get("until") is not None
        ]

    @classmethod
    def views(cls, channel):
        found = []
        for each, view in Home.store().items(cls.VIEWS).items():
            name, chat, message = each.split("\t")
            if name == channel:
                found.append(View(chat, int(message), view))
        return found

    @classmethod
    def thing_of(cls, channel, chat, messages):
        for message in messages or []:
            view = Home.store().get(cls.VIEWS, key(channel, chat, message)) or {}
            if view.get("thing"):
                return view["thing"]
        return None

    @classmethod
    def shown(cls, channel, chat, thing, messages, text, asked=""):
        if thing is None:
            thing = cls.HISTORY.next_number()
        cls.HISTORY.update(
            thing,
            lambda kept: dict(
                kept or {"number": thing, "asked": asked}, time=time.strftime("%Y-%m-%d %H:%M"), text=text
            ),
        )
        for message in messages:
            if message is not None:
                cls.change(channel, chat, message, thing=thing)
        return thing

    @classmethod
    def thing(cls, number):
        return cls.HISTORY.get(number)

    @classmethod
    def page(cls, number):
        return Numbered.page(cls.HISTORY.newest(), number)


def key(channel, chat, message):
    return f"{channel}\t{chat}\t{message}"
