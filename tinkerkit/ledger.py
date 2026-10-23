"""The ledger: what is said in the chats, kept in Tinker's home, so that a chat is only a view of it. Every message
in a chat (Tinker's, and the person's) is kept as its id and when it goes: a glance (what only shows something,
like jobs or help) in GLANCE_KEEP, a note that the chat was cleared in NOTE_KEEP, the rest in VIEW_KEEP, Tinker's
status never. The chat's collector takes out the ones whose time is up, so a chat holds what is recent alone, and
a bot may still take it out (Telegram lets it for two days).

Each thing shown (a task, a research, a chat's reply ...) is kept as it was shown last, with what the person
asked for it, numbered #1, #2 ...: history lists them and history #N shows one again. Nothing is lost when a chat
is emptied (clear chat, or the chat app's own); clear history forgets it all."""
lazy import time

lazy from .settings import Home

VIEW_KEEP = 24 * 3600                          # seconds a message stays in a chat
GLANCE_KEEP = 120                              # ... a glance
NOTE_KEEP = 5                                  # ... a note that only says something was done (the chat cleared)
ASKED_WAIT = 60                                # seconds after the person's message in which a new thing answers it
PAGE = 10                                      # things history lists on one page

class Ledger:
    """The messages in the chats ("chat views": {until, thing, glance}, by channel, chat and id) and the things
    they show ("chat history": {number, time, asked, text}, by number)."""
    VIEWS, THINGS, NUMBERS = "chat views", "chat history", "chat history numbers"

    @classmethod
    def kept(cls, channel, chat, message):
        """A message now in a chat, going in VIEW_KEEP."""
        cls.change(channel, chat, message, until=time.time() + VIEW_KEEP)

    @classmethod
    def touched(cls, channel, chat, message):
        """A message edited: it goes VIEW_KEEP from now (a status, or a glance, goes as it went)."""
        view = Home.store().get(cls.VIEWS, key(channel, chat, message))
        if view and view.get("until") and not view.get("glance"):
            cls.change(channel, chat, message, until=time.time() + VIEW_KEEP)

    @classmethod
    def glance(cls, channel, chat, messages, keep=None):
        """Messages that only show something, going in keep seconds (GLANCE_KEEP when not given)."""
        for message in messages:
            cls.change(channel, chat, message, until=time.time() + (keep or GLANCE_KEEP), glance=True)

    @classmethod
    def lasting(cls, channel, chat, message):
        """Tinker's status: it never goes."""
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
        """A message no longer in the chat."""
        Home.store().delete(cls.VIEWS, key(channel, chat, message))

    @classmethod
    def due(cls, channel):
        """[(chat, id)] of the channel's messages whose time is up."""
        return [(chat, message) for chat, message, view in cls.views(channel)
                if view.get("until") is not None and view["until"] <= time.time()]

    @classmethod
    def glances(cls, channel, chat):
        """The ids of the chat's glances."""
        return [message for each, message, view in cls.views(channel) if each == str(chat) and view.get("glance")]

    @classmethod
    def passing(cls, channel):
        """[(chat, id)] of the channel's messages but its status: what clear chat takes out."""
        return [(chat, message) for chat, message, view in cls.views(channel) if view.get("until") is not None]

    @classmethod
    def views(cls, channel):
        """[(chat, id, view)] of the channel's messages."""
        found = []
        for each, view in Home.store().items(cls.VIEWS).items():
            name, chat, message = each.split("\t")
            if name == channel:
                found.append((chat, int(message), view))
        return found

    @classmethod
    def thing_of(cls, channel, chat, messages):
        """The number of the thing messages show (None for none)."""
        for message in messages or []:
            view = Home.store().get(cls.VIEWS, key(channel, chat, message)) or {}
            if view.get("thing"):
                return view["thing"]
        return None

    @classmethod
    def shown(cls, channel, chat, thing, messages, text, asked=""):
        """A thing shown in messages as text: kept as it is now (thing None: a new one, answering what was
        asked); its number."""
        if thing is None:
            thing = Home.store().update(cls.NUMBERS, "last", lambda last: (last or 0) + 1)
        Home.store().update(cls.THINGS, str(thing), lambda kept: dict(
            kept or {"number": thing, "asked": asked}, time=time.strftime("%Y-%m-%d %H:%M"), text=text))
        for message in messages:
            if message is not None:
                cls.change(channel, chat, message, thing=thing)
        return thing

    @classmethod
    def thing(cls, number):
        return Home.store().get(cls.THINGS, str(number))

    @classmethod
    def page(cls, number):
        """(the things on page number, from 1, newest first, and how many pages there are)."""
        things = sorted(Home.store().items(cls.THINGS).values(), key=lambda thing: thing["number"], reverse=True)
        pages = max(1, -(-len(things) // PAGE))
        return things[(number - 1) * PAGE:number * PAGE], pages

def key(channel, chat, message):
    return f"{channel}\t{chat}\t{message}"
