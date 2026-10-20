"""Channels: talking to Tinker from a chat app, a plugin like the web. With no channel enabled nothing of
this runs. A channel is enabled with tinker channel enable NAME; then every task reports its progress there,
and the Tinker that listens (tinker channel start, or tinker in a terminal) reads the chat: one at a time
(Telegram lets one program read a bot). What a chat can ask and how it is answered is the conversation's
(conversation.py); this is the chat apps themselves (TelegramChannel), their settings and tinker channel.

Only the chats a channel was paired with are listened to: a message from anyone else is ignored. Bot tokens
are kept apart from the settings, in a file only its owner can read."""
import http.client
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from .conversation import OFF, Channel, ChannelError, Desk, Markup, Message, first_words
from .settings import HOME


CHANNELS_FILE = HOME / "channels.json"         # name -> its kind, whether it is enabled, the chats it serves
SECRETS_FILE = HOME / "channel-secrets.json"   # name -> its secrets (a bot token): mode 600, never printed
PAIRING_WAIT = 120                             # seconds to wait for the first message when pairing a chat
PIN_NOTE_WAIT = 2                              # seconds before Telegram's note about a pin is taken out
CHECK_TRIES = 3                                # tries of a channel's first call when it starts listening


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

    def call(self, method, wait=30, **fields):
        """The Bot API's result (waiting up to wait seconds); a refused call is a ChannelError, a dropped
        connection an OSError. Calls go over connections kept open (a new one per call costs a name lookup
        and a handshake: seconds, where a call on an open one takes a fraction of one); one that dropped
        meanwhile is opened again, once."""
        path = f"/bot{self.secrets.get('token', '')}/{method}"
        data = urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None})
        for attempt in (1, 2):
            connection = self.connection(wait)
            try:
                connection.request("POST", path, data, {"Content-Type": "application/x-www-form-urlencoded"})
                response = connection.getresponse()
                status, body = response.status, response.read().decode(errors="replace")
            except (http.client.HTTPException, OSError):
                connection.close()
                if attempt == 2:
                    raise OSError(f"Telegram did not answer {method}") from None
                continue
            self.open_connections.put(connection)
            break
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
            if message.get("text"):
                person = message.get("from") or {}
                sender = person.get("username") or person.get("first_name", "")
                messages.append(Message(message["chat"]["id"], message["text"], sender, message.get("message_id")))
            if message.get("message_id"):
                self.seen(str(message["chat"]["id"]), message["message_id"], heard=True)
        return messages

    def skip_waiting(self):
        try:
            waiting = self.call("getUpdates", offset=-1)
            self.offset = waiting[-1]["update_id"] + 1 if waiting else None
        except (OSError, ChannelError):
            pass

    def typing(self, chat):
        try:
            self.call("sendChatAction", wait=10, chat_id=chat, action="typing")
        except (OSError, ChannelError):
            pass

    def send(self, chat, text):
        try:
            sent = self.post("sendMessage", chat, text)["message_id"]
        except (OSError, ChannelError):
            return None
        self.seen(str(chat), sent)
        return sent

    def seen(self, chat, message, heard=False):
        """A message of the chat's: the newest id so far, and whether the person wrote it."""
        self.newest[chat] = max(self.newest.get(chat, 0), message)
        self.alive[chat] = (self.alive.get(chat, []) + [message])[-200:]
        if heard:
            self.heard[chat] = (self.heard.get(chat, []) + [message])[-50:]

    def pin(self, chat, sent):
        """sent pinned as the message updated last (the one before unpinned). Telegram adds a note to the
        chat about each pin, as its next message: that note is taken out once it is surely not the
        person's (they did not write it) nor Tinker's (a note cannot be edited)."""
        chat = str(chat)
        if self.pinned.get(chat) == sent:
            return
        if self.pinned.get(chat):
            try:  # one taken out (sent anew at the bottom) is unpinned already
                self.call("unpinChatMessage", chat_id=chat, message_id=self.pinned[chat])
            except (OSError, ChannelError):
                pass
        after = self.newest.get(chat, 0)
        try:
            self.call("pinChatMessage", chat_id=chat, message_id=sent, disable_notification=True)
        except (OSError, ChannelError):
            return
        self.pinned[chat] = sent
        threading.Thread(target=self.drop_pin_note, args=(chat, after), daemon=True).start()

    def drop_pin_note(self, chat, after):
        time.sleep(PIN_NOTE_WAIT)  # so that a message the person sent meanwhile is heard first
        for note in range(after + 1, after + 4):
            if note in self.heard.get(chat, []):
                continue
            try:  # Tinker's own messages can be edited: one that cannot, and is not the person's, is the note
                self.call("editMessageReplyMarkup", chat_id=chat, message_id=note, reply_markup="{}")
                continue
            except ChannelError as exc:
                if "can't be edited" not in str(exc):
                    continue
            except OSError:
                return
            self.delete(chat, note)
            return

    def delete(self, chat, sent):
        """A message taken out: Tinker's own, or the person's (a bot may, in a private chat)."""
        try:
            self.call("deleteMessage", chat_id=chat, message_id=sent)
        except (OSError, ChannelError):
            pass
        alive = self.alive.get(str(chat), [])
        if sent in alive:
            alive.remove(sent)

    def latest(self, chat, sent):
        """Whether no message still in the chat came after sent (of those seen here)."""
        return sent is not None and sent >= max(self.alive.get(str(chat), [0]), default=0)

    def edit(self, chat, sent, text):
        if sent is None:
            return self.send(chat, text)
        try:
            self.post("editMessageText", chat, text, message_id=sent)
        except ChannelError as exc:  # unchanged text is refused, harmlessly
            if "not modified" not in str(exc):
                return self.send(chat, text)
        except OSError:
            pass
        return sent

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


KINDS = {kind.KIND: kind for kind in (TelegramChannel,)}


class Channels:
    """The channels Tinker has: their settings (kind, enabled, the chats they serve) in channels.json,
    their secrets apart in channel-secrets.json (mode 600)."""

    @staticmethod
    def settings():
        return Channels.read(CHANNELS_FILE)

    @staticmethod
    def read(path):
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise SystemExit(f"tinker: {path} cannot be read ({exc}): fix or delete it") from None
        return data if isinstance(data, dict) else {}

    @staticmethod
    def write(settings=None, secrets=None):
        HOME.mkdir(parents=True, exist_ok=True)
        if settings is not None:
            CHANNELS_FILE.write_text(json.dumps(settings, indent=2) + "\n")
        if secrets is not None:
            SECRETS_FILE.touch(mode=0o600, exist_ok=True)
            os.chmod(SECRETS_FILE, 0o600)
            SECRETS_FILE.write_text(json.dumps(secrets, indent=2) + "\n")

    @staticmethod
    def make(name):
        settings = Channels.settings().get(name)
        if settings is None:
            raise SystemExit(f"tinker: no channel {name!r} (tinker channel list shows them)")
        kind = KINDS.get(settings.get("kind"))
        if kind is None:
            raise SystemExit(f"tinker: channel {name!r} is of an unknown kind {settings.get('kind')!r}")
        return kind(name, settings, Channels.read(SECRETS_FILE).get(name, {}))

    @staticmethod
    def enabled():
        """The enabled channels, made; none when a run is told not to listen (tinker channel start's own)."""
        if os.environ.get(OFF) == "off":
            return []
        return [Channels.make(name) for name, s in Channels.settings().items() if s.get("enabled")]


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
            print(ChannelCommand.USAGE)
            return
        if action not in actions:
            raise SystemExit(f"tinker channel: no action {action!r}\n{ChannelCommand.USAGE}")
        actions[action](rest)

    @staticmethod
    def named(rest, action):
        if len(rest) != 1:
            raise SystemExit(f"tinker channel {action} NAME")
        if rest[0] not in Channels.settings():
            raise SystemExit(f"tinker: no channel {rest[0]!r} (tinker channel list shows them)")
        return rest[0]

    @staticmethod
    def list(rest):
        settings = Channels.settings()
        if not settings:
            print(f"No channels yet. Add one: tinker channel add  (kinds: {', '.join(KINDS)})")
            return
        for name, s in settings.items():
            state = "enabled" if s.get("enabled") else "disabled"
            chats = ", ".join(map(str, s.get("allowed", []))) or "no chat paired"
            print(f"  {name:<16} {s.get('kind', '?'):<10} {state:<9} chats: {chats}")

    @staticmethod
    def add(rest):
        """Asks the kind, a name and the kind's secrets; checks them; pairs the chat(s) it will serve.
        Each answer can come as an option instead: --kind, --name, --token (any secret), --chat."""
        given = ChannelCommand.options(rest)
        kinds = list(KINDS)
        kind = given.get("kind") or ask("Channel kind", kinds[0] if len(kinds) == 1 else "", choices=kinds)
        if kind not in KINDS:
            raise SystemExit(f"tinker: no channel kind {kind!r} (supported: {', '.join(KINDS)})")
        settings = Channels.settings()
        name = given.get("name") or ask("Channel name", kind)
        if not re.fullmatch(r"[A-Za-z0-9][\w-]{0,31}", name):
            raise SystemExit("tinker: a channel name is letters, digits, - and _ (up to 32)")
        if name in settings:
            raise SystemExit(f"tinker: a channel {name!r} exists (tinker channel remove {name} first)")
        secrets = {key: given.get(key) or ask(what, secret=True) for key, what in KINDS[kind].SECRETS}
        channel = KINDS[kind](name, {}, secrets)
        try:
            who = channel.who()
        except (ChannelError, OSError) as exc:
            raise SystemExit(f"tinker: the {kind} channel does not work with that: {exc}") from None
        chats = ([c.strip() for c in given["chat"].split(",")] if given.get("chat")
                 else ChannelCommand.pair(channel, who))
        settings[name] = {"kind": kind, "enabled": False, "allowed": chats, "who": who}
        all_secrets = Channels.read(SECRETS_FILE)
        all_secrets[name] = secrets
        Channels.write(settings, all_secrets)
        print(f"Added {name} ({kind}, {who}), serving chat(s) {', '.join(chats)}. "
              f"It starts disabled: tinker channel enable {name}")

    @staticmethod
    def pair(channel, who):
        """The chat that sends the channel a message now, once its sender confirms it here."""
        channel.skip_waiting()
        print(f"Send any message to {who} now (waiting up to {PAIRING_WAIT}s)…")
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
    def options(rest):
        found, items = {}, list(rest)
        while items:
            item = items.pop(0)
            if not item.startswith("--") or not items:
                raise SystemExit(f"tinker channel add: unexpected {item!r} (options: --kind --name --token --chat)")
            found[item[2:]] = items.pop(0)
        return found

    @staticmethod
    def remove(rest):
        name = ChannelCommand.named(rest, "remove")
        settings, secrets = Channels.settings(), Channels.read(SECRETS_FILE)
        settings.pop(name, None)
        secrets.pop(name, None)
        Channels.write(settings, secrets)
        print(f"Removed {name}.")

    @staticmethod
    def enable(rest, on=True):
        name = ChannelCommand.named(rest, "enable" if on else "disable")
        settings = Channels.settings()
        settings[name]["enabled"] = on
        Channels.write(settings)
        print(f"{name} is {'enabled: tasks report to it; tinker channel start listens on it' if on else 'disabled'}.")

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
                print(f"  {channel.name} did not answer ({exc}); trying again ({tries + 1}/{CHECK_TRIES})")
                time.sleep(5)


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
