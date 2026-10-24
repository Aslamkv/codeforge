"""Channels: talking to Tinker from a chat app, a plugin like the web. With no channel enabled nothing of
this runs. A channel is enabled with tinker channel enable NAME; then every task reports its progress there,
and the Tinker that listens (tinker channel start, or tinker in a terminal) reads the chat: one at a time
(Telegram lets one program read a bot). What a chat can ask and how it is answered is the conversation's
(conversation.py); this is the chat apps themselves (TelegramChannel), their settings and tinker channel.

Only the chats a channel was paired with are listened to: a message from anyone else is ignored. Bot tokens
are kept apart from the settings ("channel_secrets", in Tinker's database, which only its owner can read)."""
lazy import http.client
lazy import json
lazy import os
lazy import queue
lazy import re
lazy import secrets
lazy import sys
lazy import threading
lazy import time
lazy import urllib.parse
lazy from pathlib import Path

from .conversation import OFF, Channel, ChannelError, Desk, Markup, Message, first_words
lazy from .ledger import ASKED_WAIT, Ledger
lazy from .messages import say
lazy from .settings import Home


PAIRING_WAIT = 120                             # seconds to wait for the first message when pairing a chat
PIN_NOTE_WAIT = 2                              # seconds before Telegram's note about a pin is taken out
CHECK_TRIES = 3                                # tries of a channel's first call when it starts listening
RATE_LIMIT_WAITS = 3                           # times a call waits out Telegram's "too many requests"
CAPTION_CHARS = 1024                           # the longest caption Telegram takes on a file
MENU_DESCRIPTION_CHARS = 256                   # the longest description of a command in a bot's menu
BOT_DOWNLOAD_BYTES = 20_000_000                # the largest file Telegram lets a bot download
DOWNLOAD_WAIT = 120                            # seconds a file sent in a chat may take to download
FORM = "application/x-www-form-urlencoded"


class ChannelCommand:
    """tinker channel list | add | remove NAME | enable NAME | disable NAME | start [NAME]."""
    USAGE = ("tinker channel list                 the channels, and whether each is enabled\n"
             "tinker channel add                  add one (asks its kind, a name, its secrets, and pairs your chat)\n"
             "tinker channel remove NAME          delete it, secrets included\n"
             "tinker channel enable NAME          every task reports to it; tinker channel start reads it\n"
             "tinker channel disable NAME         stop using it (new channels start disabled)\n"
             "tinker channel start [NAME]         listen on enabled channels (or NAME): ask for tasks, or just chat")

    @staticmethod
    def run(argv):
        action, rest = (argv[0], argv[1:]) if argv else ("list", [])
        actions = {"list": ChannelCommand.list, "add": ChannelCommand.add, "remove": ChannelCommand.remove,
                   "enable": ChannelCommand.enable, "disable": ChannelCommand.disable, "start": ChannelCommand.start}
        if action in ("help", "-h", "--help"):
            say.text(ChannelCommand.USAGE)
            return
        if action not in actions:
            raise SystemExit(f"tinker channel: no action {action!r}\n{ChannelCommand.USAGE}")
        actions[action](rest)

    @staticmethod
    def list(rest):
        settings = Channels.settings()
        if not settings:
            say.info(f"No channels yet. Add one: tinker channel add  (kinds: {', '.join(KINDS)})", "📭")
            return
        for name, s in settings.items():
            state = "enabled" if s.get("enabled") else "disabled"
            chats = ", ".join(map(str, s.get("allowed", []))) or "no chat paired"
            say.text(f"{name:<16} {s.get('kind', '?'):<10} {state:<9} chats: {chats}")

    @staticmethod
    def add(rest):
        """Asks the kind, a name and the kind's secrets; checks them; pairs the chat(s) it will serve.
        Each answer can come as an option instead: --kind, --name, --token (any secret), --chat."""
        given = ChannelCommand.options(rest)
        kind = ChannelCommand.kind(given)
        name = ChannelCommand.new_name(given, kind)
        secrets = {key: given.get(key) or ask(what, secret=True) for key, what in KINDS[kind].SECRETS}
        channel = KINDS[kind](name, {}, secrets)
        try:
            who = channel.who()
        except (ChannelError, OSError) as exc:
            raise SystemExit(f"tinker: the {kind} channel does not work with that: {exc}") from None
        chats = ([c.strip() for c in given["chat"].split(",")] if given.get("chat")
                 else ChannelCommand.pair(channel, who))
        Channels.keep(name, {"kind": kind, "enabled": False, "allowed": chats, "who": who}, secrets)
        say.done(f"Added {name} ({kind}, {who}), serving chat(s) {', '.join(chats)}. "
                 f"It starts disabled: tinker channel enable {name}")

    @staticmethod
    def options(rest):
        found, items = {}, list(rest)
        while items:
            item = items.pop(0)
            if not item.startswith("--") or not items:
                raise SystemExit(f"tinker channel add: unexpected {item!r} (options: --kind --name --token --chat)")
            found[item[2:]] = items.pop(0)
        return found

    @staticmethod
    def kind(given):
        kinds = list(KINDS)
        kind = given.get("kind") or ask("Channel kind", kinds[0] if len(kinds) == 1 else "", choices=kinds)
        if kind not in KINDS:
            raise SystemExit(f"tinker: no channel kind {kind!r} (supported: {', '.join(KINDS)})")
        return kind

    @staticmethod
    def new_name(given, kind):
        """A name for the new channel: a short word, not a channel's already."""
        name = given.get("name") or ask("Channel name", kind)
        if not re.fullmatch(r"[A-Za-z0-9][\w-]{0,31}", name):
            raise SystemExit("tinker: a channel name is letters, digits, - and _ (up to 32)")
        if name in Channels.settings():
            raise SystemExit(f"tinker: a channel {name!r} exists (tinker channel remove {name} first)")
        return name

    @staticmethod
    def pair(channel, who):
        """The chat that sends the channel a message now, once its sender confirms it here."""
        channel.skip_waiting()
        say.step(f"Send any message to {who} now (waiting up to {PAIRING_WAIT}s)…", "📨")
        ends = time.monotonic() + PAIRING_WAIT
        while time.monotonic() < ends:
            for message in channel.receive(timeout=10):
                answer = ask(f"Message from {message.sender or 'someone'} (chat {message.chat}): "
                             f"{first_words(message.text, 40)!r}. Is that you? [y/N]", "n")
                if answer.lower().startswith("y"):
                    channel.send(message.chat, "✅ **Paired with Tinker.** Only this chat is listened to.")
                    return [message.chat]
        raise SystemExit("tinker: no message came; run tinker channel add again (or give --chat ID)")

    @staticmethod
    def remove(rest):
        name = ChannelCommand.named(rest, "remove")
        Channels.forget(name)
        say.done(f"Removed {name}.")

    @staticmethod
    def enable(rest, on=True):
        name = ChannelCommand.named(rest, "enable" if on else "disable")
        Channels.keep(name, dict(Channels.settings()[name], enabled=on))
        say.done(f"{name} is {'enabled: tasks report to it; tinker channel start listens on it' if on else 'disabled'}.")

    @staticmethod
    def disable(rest):
        ChannelCommand.enable(rest, on=False)

    @staticmethod
    def start(rest):
        if rest:
            channels = [Channels.make(ChannelCommand.named(rest, "start"))]
        else:
            channels = Channels.enabled()
        if not channels:
            raise SystemExit("tinker: no channel is enabled (tinker channel enable NAME, or start NAME)")
        for channel in channels:
            ChannelCommand.check(channel)
        Desk(channels, Path.cwd()).run()

    @staticmethod
    def check(channel):
        """The channel answers: a refusal (a bad token) ends here, a slow or dropped network is asked
        again, up to CHECK_TRIES times."""
        for tries in range(1, CHECK_TRIES + 1):
            try:
                return channel.who()
            except ChannelError as exc:
                raise SystemExit(f"tinker: channel {channel.name} does not work: {exc}") from None
            except OSError as exc:
                if tries == CHECK_TRIES:
                    raise SystemExit(f"tinker: channel {channel.name} cannot be reached ({exc}), tried {tries} "
                                     f"times: check the network and start it again") from None
                say.retry(f"{channel.name} did not answer ({exc}); trying again ({tries + 1}/{CHECK_TRIES})")
                time.sleep(5)

    @staticmethod
    def named(rest, action):
        if len(rest) != 1:
            raise SystemExit(f"tinker channel {action} NAME")
        if rest[0] not in Channels.settings():
            raise SystemExit(f"tinker: no channel {rest[0]!r} (tinker channel list shows them)")
        return rest[0]


class Channels:
    """The channels Tinker has: their settings (kind, enabled, the chats they serve) in Tinker's
    "channels", their secrets apart in its "channel_secrets" (never printed)."""

    @staticmethod
    def enabled():
        """The enabled channels, made; none when a run is told not to listen (tinker channel start's own)."""
        if os.environ.get(OFF) == "off":
            return []
        return [Channels.make(name) for name, s in Channels.settings().items() if s.get("enabled")]

    @staticmethod
    def make(name):
        settings = Channels.settings().get(name)
        if settings is None:
            raise SystemExit(f"tinker: no channel {name!r} (tinker channel list shows them)")
        kind = KINDS.get(settings.get("kind"))
        if kind is None:
            raise SystemExit(f"tinker: channel {name!r} is of an unknown kind {settings.get('kind')!r}")
        return kind(name, settings, Channels.secrets(name))

    @staticmethod
    def settings():
        """{name: settings} of every channel."""
        return Home.store().items("channels")

    @staticmethod
    def secrets(name):
        return Home.store().get("channel_secrets", name, {})

    @staticmethod
    def keep(name, settings, secrets=None):
        Home.store().put("channels", name, settings)
        if secrets is not None:
            Home.store().put("channel_secrets", name, secrets)

    @staticmethod
    def forget(name):
        Home.store().delete("channels", name)
        Home.store().delete("channel_secrets", name)


class TelegramChannel(Channel):
    """A Telegram bot (made with @BotFather): its token is the one secret."""
    KIND = "telegram"
    SECRETS = (("token", "Bot token (from @BotFather)"),)
    HOST = "api.telegram.org"

    def __init__(self, name, settings, secrets):
        super().__init__(name, settings, secrets)
        self.offset = None
        self.open_connections = queue.Queue()  # connections kept open between calls
        self.pinned, self.newest, self.heard = {}, {}, {}  # per chat: the pinned id, the newest id, the person's ids
        self.alive = {}  # per chat: the ids of the messages seen that are still in it (taken out: no more)
        self.asked = {}  # per chat: (the person's last words, when) until a new thing answers them

    def who(self):
        return "@" + self.call("getMe")["username"]

    def receive(self, timeout):
        try:
            updates = self.call("getUpdates", wait=timeout + 10, offset=self.offset, timeout=int(timeout))
        except OSError:  # the network is gone for now: the messages wait at Telegram
            time.sleep(min(timeout, 10))
            return []
        messages = []
        for update in updates:
            self.offset = update["update_id"] + 1
            message = update.get("message") or {}
            if message.get("document") and str(message["chat"]["id"]) in map(str, self.allowed):
                message = self.book_in(message)
            if message.get("text"):
                messages.append(self.message_of(message))
                self.asked[str(message["chat"]["id"])] = (message["text"], time.monotonic())
            if message.get("message_id"):
                self.seen(str(message["chat"]["id"]), message["message_id"], heard=True)
        return messages

    @staticmethod
    def message_of(message):
        person = message.get("from") or {}
        sender = person.get("username") or person.get("first_name", "")
        return Message(message["chat"]["id"], message["text"], sender, message.get("message_id"))

    def book_in(self, message):
        """A file sent in an allowed chat, as the message to read it: read with the file kept in Tinker's home (the
        link Telegram gives a bot carries its token, so it is never passed on). A file too large for a bot, or one
        that cannot be downloaded, is said to the person, and the message has no text."""
        chat, document = str(message["chat"]["id"]), message["document"]
        name = document.get("file_name") or "book"
        if document.get("file_size", 0) > BOT_DOWNLOAD_BYTES:
            self.send(chat, f"{name} is larger than a bot may download from Telegram (20 MB): send a link that "
                            f"downloads it instead (read URL).")
            return dict(message, text="")
        try:
            path = self.downloaded(document["file_id"], name)
        except (OSError, ChannelError, KeyError, TypeError) as exc:
            self.send(chat, f"Could not take {name}: {exc}")
            return dict(message, text="")
        return dict(message, text=f"read {path}")

    def downloaded(self, file_id, name):
        """The file Telegram keeps for file_id, downloaded into Tinker's home as name (Home.reading_file); its path."""
        found = self.call("getFile", file_id=file_id)["file_path"]
        connection = self.connection(DOWNLOAD_WAIT)
        try:
            connection.request("GET", f"/file/bot{self.secrets.get('token', '')}/{found}", None, {})
            response = connection.getresponse()
            status, body = response.status, response.read()
        except http.client.HTTPException, OSError:
            raise OSError("Telegram did not send the file") from None
        finally:
            connection.close()
        if status != 200:
            raise ChannelError(f"Telegram answered {status} for the file")
        path = Home.reading_file(name)
        path.write_bytes(body)
        return path

    def skip_waiting(self):
        try:
            waiting = self.call("getUpdates", offset=-1)
            self.offset = waiting[-1]["update_id"] + 1 if waiting else None
        except OSError, ChannelError:
            pass

    def offer(self, commands):
        """The commands in the bot's menu (shown as the person types /): Telegram takes a-z, 0-9 and _ in a name,
        so use-model is offered as /use_model (and taken back as use-model)."""
        menu = [{"command": name.replace("-", "_"), "description": what[:MENU_DESCRIPTION_CHARS]}
                for name, what in commands]
        try:
            self.call("setMyCommands", commands=json.dumps(menu))
        except OSError, ChannelError:
            pass

    def send(self, chat, text):
        try:
            sent = self.post("sendMessage", chat, text)["message_id"]
        except OSError, ChannelError:
            return None
        self.seen(str(chat), sent)
        return sent

    def edit(self, chat, sent, text):
        """sent edited to text; one that cannot be is taken out, and text sent anew in its place."""
        if sent is None:
            return self.send(chat, text)
        try:
            self.post("editMessageText", chat, text, message_id=sent)
        except ChannelError as exc:  # unchanged text is refused, harmlessly
            if "not modified" not in str(exc):
                self.delete(chat, sent)
                return self.send(chat, text)
        except OSError:
            return sent
        Ledger.touched(self.name, str(chat), sent)
        return sent

    def show(self, chat, shown, text, done=False, bottom=None, pin=False):
        """The thing shown, and kept in the ledger as it is now (Tinker's status is not a thing)."""
        thing = None if pin else Ledger.thing_of(self.name, str(chat), shown)
        now = super().show(chat, shown, text, done, bottom, pin)
        if not pin:
            Ledger.shown(self.name, str(chat), thing, now, text, "" if thing else self.answered(chat))
        return now

    def answered(self, chat):
        """The person's words a new thing answers: their last message, sent within ASKED_WAIT ("" for none)."""
        words, when = self.asked.pop(str(chat), ("", 0.0))
        return words if time.monotonic() - when <= ASKED_WAIT else ""

    def post(self, method, chat, text, **fields):
        """text sent (or edited) as Telegram shows Markup (HTML), commands in it said as the conversation takes
        them; what it cannot read as HTML goes plain."""
        text = self.said(text)
        try:
            return self.call(method, chat_id=chat, text=Markup.html(text)[:4096], parse_mode="HTML", **fields)
        except ChannelError as exc:
            if "parse" not in str(exc) and "entit" not in str(exc):
                raise
            return self.call(method, chat_id=chat, text=Markup.plain(text)[:4096], **fields)

    def send_file(self, chat, path, caption):
        """The file sent as a document, caption (Markup) under it; a failure sends the caption alone."""
        caption = self.said(caption)
        try:
            sent = self.call("sendDocument", wait=120, file=Path(path), chat_id=chat,
                             caption=Markup.html(caption)[:CAPTION_CHARS], parse_mode="HTML")["message_id"]
        except OSError, ChannelError:
            return self.send(chat, caption)
        self.seen(str(chat), sent)
        return sent

    def typing(self, chat):
        try:
            self.call("sendChatAction", wait=10, chat_id=chat, action="typing")
        except OSError, ChannelError:
            pass

    def pin(self, chat, sent):
        """sent (Tinker's status) pinned, the one pin of the chat. The status kept from before is pinned already:
        it is left as it is. A new one is pinned alone (every pin before taken out, so none piles up). Telegram
        adds a note to the chat about each pin, as its next message: that note is taken out once it is surely not
        the person's (they did not write it) nor Tinker's (a note cannot be edited)."""
        chat = str(chat)
        if self.pinned.get(chat) == sent:
            return
        if Ledger.is_lasting(self.name, chat, sent) and self.pinned_now(chat) == sent:
            self.pinned[chat] = sent
            return
        after = self.newest.get(chat, 0)
        try:
            self.call("unpinAllChatMessages", chat_id=chat)
            self.call("pinChatMessage", chat_id=chat, message_id=sent, disable_notification=True)
        except OSError, ChannelError:
            return
        self.pinned[chat] = sent
        threading.Thread(target=self.drop_pin_note, args=(chat, after), daemon=True).start()

    def pinned_now(self, chat):
        """The id of the chat's pinned message (None for none, or when Telegram does not say)."""
        try:
            return (self.call("getChat", chat_id=chat).get("pinned_message") or {}).get("message_id")
        except OSError, ChannelError:
            return None

    def lasting(self, chat, sent):
        for message in sent:
            if message is not None:
                Ledger.lasting(self.name, str(chat), message)

    def glance(self, chat, asked, sent, keep=None):
        """sent only shows something, asked for by the person's message asked: that message taken out now, the
        glance shown before too (one at a time), and sent in keep seconds (GLANCE_KEEP when not given)."""
        self.asked.pop(str(chat), None)  # what it answered asks no thing
        if asked:
            self.delete(chat, asked)
        for message in Ledger.glances(self.name, chat):
            self.delete(chat, message)
        Ledger.glance(self.name, str(chat), [message for message in sent if message is not None], keep)

    def collect(self):
        """The messages whose time is up, taken out of the chats."""
        for chat, message in Ledger.due(self.name):
            self.delete(chat, message)

    def clear(self):
        """Every message in the chats taken out but Tinker's status; how many."""
        passing = Ledger.passing(self.name)
        for chat, message in passing:
            self.delete(chat, message)
        return len(passing)

    def drop_pin_note(self, chat, after):
        time.sleep(PIN_NOTE_WAIT)  # so that a message the person sent meanwhile is heard first
        note = self.pin_note(chat, after)
        if note:
            self.delete(chat, note)

    def pin_note(self, chat, after):
        """Of the next few messages after `after`, the one that is neither the person's nor Tinker's: Tinker's
        own messages can be edited, a note cannot (None when there is none, or the network is gone)."""
        for note in range(after + 1, after + 4):
            if note in self.heard.get(chat, []):
                continue
            try:
                self.call("editMessageReplyMarkup", chat_id=chat, message_id=note, reply_markup="{}")
            except ChannelError as exc:
                if "can't be edited" in str(exc):
                    return note
            except OSError:
                return None
        return None

    def delete(self, chat, sent):
        """A message taken out: Tinker's own, or the person's (a bot may, in a private chat). Without a network it
        stays in the ledger, to be taken out later; a refusal means it is gone, or too old for a bot to take out."""
        alive = self.alive.get(str(chat), [])
        if sent in alive:
            alive.remove(sent)
        try:
            self.call("deleteMessage", chat_id=chat, message_id=sent)
        except OSError:
            return
        except ChannelError:
            pass
        Ledger.gone(self.name, str(chat), sent)

    def latest(self, chat, sent):
        """Whether no message still in the chat came after sent (of those seen here)."""
        return sent is not None and sent >= max(self.alive.get(str(chat), [0]), default=0)

    def seen(self, chat, message, heard=False):
        """A message of the chat's: the newest id so far, and whether the person wrote it; one of a chat served
        is kept in the ledger."""
        self.newest[chat] = max(self.newest.get(chat, 0), message)
        self.alive[chat] = (self.alive.get(chat, []) + [message])[-200:]
        if heard:
            self.heard[chat] = (self.heard.get(chat, []) + [message])[-50:]
        if chat in map(str, self.allowed):
            Ledger.kept(self.name, chat, message)

    def call(self, method, wait=30, file=None, **fields):
        """The Bot API's result (waiting up to wait seconds); a refused call is a ChannelError, a dropped
        connection an OSError. Calls go over connections kept open (a new one per call costs a name lookup
        and a handshake: seconds, where a call on an open one takes a fraction of one); one that dropped
        meanwhile is opened again, once. Too many requests (429) is waited out as long as Telegram asks.
        file: a document (a Path) uploaded with the fields, as a form with parts."""
        path = f"/bot{self.secrets.get('token', '')}/{method}"
        fields = {k: v for k, v in fields.items() if v is not None}
        data, kind = self.multipart(fields, file) if file else (urllib.parse.urlencode(fields), FORM)
        status, body = self.answer(method, path, data, wait, kind)
        for _ in range(RATE_LIMIT_WAITS):
            pause = self.retry_after(status, body)
            if pause is None:
                break
            time.sleep(pause)
            status, body = self.answer(method, path, data, wait, kind)
        return self.result(method, status, body)

    @staticmethod
    def multipart(fields, file):
        """(body, content type) of a multipart/form-data upload: each field, then the file as "document"."""
        boundary = f"tinker-{secrets.token_hex(12)}"
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
                 for name, value in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{file.name}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n'.encode() + file.read_bytes() + b"\r\n")
        return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"

    def answer(self, method, path, data, wait, kind=FORM):
        """(status, body) of the call, on a connection opened again once when it dropped."""
        for attempt in (1, 2):
            try:
                return self.exchange(path, data, wait, kind)
            except http.client.HTTPException, OSError:
                if attempt == 2:
                    raise OSError(f"Telegram did not answer {method}") from None

    @staticmethod
    def retry_after(status, body):
        """The seconds Telegram asks to wait before calling again; None when it did not ask."""
        if status != 429:
            return None
        try:
            return float(json.loads(body)["parameters"]["retry_after"])
        except ValueError, KeyError, TypeError:
            return 1

    def exchange(self, path, data, wait, kind=FORM):
        """(status, body) of one POST over an open connection, kept open for the next call (closed when it failed)."""
        connection = self.connection(wait)
        try:
            connection.request("POST", path, data, {"Content-Type": kind})
            response = connection.getresponse()
            answer = response.status, response.read().decode(errors="replace")
        except http.client.HTTPException, OSError:
            connection.close()
            raise
        self.open_connections.put(connection)
        return answer

    @staticmethod
    def result(method, status, body):
        try:
            reply = json.loads(body)
        except ValueError:
            reply = {}
        if status != 200 or not reply.get("ok"):
            raise ChannelError(f"Telegram refused {method}: {status} {first_words(body)}")
        return reply.get("result")

    def connection(self, wait):
        """An open connection to Telegram (one kept from a call before, else a new one), for a call of up
        to wait seconds."""
        try:
            connection = self.open_connections.get_nowait()
        except queue.Empty:
            connection = http.client.HTTPSConnection(self.HOST, timeout=wait)
        connection.timeout = wait
        if connection.sock is not None:
            connection.sock.settimeout(wait)
        return connection


KINDS = frozendict({kind.KIND: kind for kind in (TelegramChannel,)})


def ask(question, default="", choices=None, secret=False):
    """One answer from the person at the terminal (default when they just press Enter)."""
    shown = f"{question}" + (f" ({'/'.join(choices)})" if choices else "") + (f" [{default}]" if default else "")
    if not sys.stdin.isatty():
        if default:
            return default
        raise SystemExit(f"tinker channel: {question} is needed (run it in a terminal, or give it as an option)")
    if secret:
        import getpass
        answer = getpass.getpass(f"{shown}: ")
    else:
        answer = input(f"{shown}: ")
    answer = answer.strip() or default
    if not answer:
        raise SystemExit(f"tinker channel: {question} is needed")
    return answer
