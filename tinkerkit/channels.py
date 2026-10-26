from collections import namedtuple
lazy import getpass
lazy import http.client
lazy import json
lazy import queue
lazy import re
lazy import secrets
lazy import sys
lazy import time
lazy import urllib.parse
lazy from pathlib import Path

from .conversation import (
    Channel,
    ChannelError,
    Desk,
    Markup,
    Message,
    Secret,
    first_words,
    started_by_a_conversation,
)
lazy from .ledger import ANSWER_WINDOW_SECONDS, Ledger
lazy from .messages import say
lazy from .settings import Home, confirm_deletion

PAIRING_WAIT_SECONDS = 120
FIRST_CALL_TRIES = 3
TELEGRAM_RATE_LIMIT_WAITS = 3
TELEGRAM_MAX_CAPTION_CHARS = 1024
TELEGRAM_MAX_MENU_DESCRIPTION_CHARS = 256
TELEGRAM_MAX_PLACEHOLDER_CHARS = 64
TELEGRAM_MAX_DOWNLOAD_BYTES = 20_000_000
SENT_FILE_DOWNLOAD_TIMEOUT = 120
FORM = "application/x-www-form-urlencoded"


Upload = namedtuple("Upload", "body content_type")
Answer = namedtuple("Answer", "status body")


class ChannelCommand:
    USAGE = (
        "tinker channel list                 the channels, and whether each is enabled\n"
        "tinker channel add                  add one (asks its kind, a name, its secrets, and pairs your chat)\n"
        "tinker channel remove NAME [--yes] delete it, secrets included, once you confirm\n"
        "tinker channel enable NAME          every task reports to it; tinker channel start reads it\n"
        "tinker channel disable NAME         stop using it (new channels start disabled)\n"
        "tinker channel start [NAME]         listen on enabled channels (or NAME): ask for tasks, or just chat"
    )

    @staticmethod
    def run(argv):
        action, rest = (argv[0], argv[1:]) if argv else ("list", [])
        actions = {
            "list": ChannelCommand.list,
            "add": ChannelCommand.add,
            "remove": ChannelCommand.remove,
            "enable": ChannelCommand.enable,
            "disable": ChannelCommand.disable,
            "start": ChannelCommand.start,
        }
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
        for name, channel_settings in settings.items():
            state = "enabled" if channel_settings.get("enabled") else "disabled"
            chats = ", ".join(map(str, channel_settings.get("allowed", []))) or "no chat paired"
            say.text(f"{name:<16} {channel_settings.get('kind', '?'):<10} {state:<9} chats: {chats}")

    @staticmethod
    def add(rest):
        given = ChannelCommand.options(rest)
        kind = ChannelCommand.kind(given)
        name = ChannelCommand.new_name(given, kind)
        secrets = {key: given.get(key) or ask(what, secret=True) for key, what in KINDS[kind].SECRETS}
        channel = KINDS[kind](name, {}, secrets)
        try:
            who = channel.handle()
        except (ChannelError, OSError) as exc:
            raise SystemExit(f"tinker: the {kind} channel does not work with that: {exc}") from None
        chats = (
            [chat.strip() for chat in given["chat"].split(",")]
            if given.get("chat")
            else ChannelCommand.pair(channel, who)
        )
        Channels.keep(name, {"kind": kind, "enabled": False, "allowed": chats, "who": who}, secrets)
        say.done(
            f"Added {name} ({kind}, {who}), serving chat(s) {', '.join(chats)}. "
            f"It starts disabled: tinker channel enable {name}"
        )

    @staticmethod
    def options(rest):
        found, items = {}, list(rest)
        while items:
            item = items.pop(0)
            if not item.startswith("--") or not items:
                raise SystemExit(
                    f"tinker channel add: unexpected {item!r} (options: --kind --name --token --chat)"
                )
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
        name = given.get("name") or ask("Channel name", kind)
        if not re.fullmatch(r"[A-Za-z0-9][\w-]{0,31}", name):
            raise SystemExit("tinker: a channel name is letters, digits, - and _ (up to 32)")
        if name in Channels.settings():
            raise SystemExit(f"tinker: a channel {name!r} exists (tinker channel remove {name} first)")
        return name

    @staticmethod
    def pair(channel, who):
        channel.skip_waiting()
        say.step(f"Send any message to {who} now (waiting up to {PAIRING_WAIT_SECONDS}s)…", "📨")
        ends = time.monotonic() + PAIRING_WAIT_SECONDS
        while time.monotonic() < ends:
            for message in channel.receive(timeout=10):
                answer = ask(
                    f"Message from {message.sender or 'someone'} (chat {message.chat}): "
                    f"{first_words(message.text, 40)!r}. Is that you? [y/N]",
                    "n",
                )
                if answer.lower().startswith("y"):
                    channel.send(message.chat, "✅ **Paired with Tinker.** Only this chat is listened to.")
                    return [message.chat]
        raise SystemExit("tinker: no message came; run tinker channel add again (or give --chat ID)")

    @staticmethod
    def remove(rest):
        name = ChannelCommand.named([word for word in rest if word != "--yes"], "remove")
        confirm_deletion(
            "This deletes:",
            [f"channel {name}: its settings and its secrets"],
            f"tinker channel remove {name}",
            "--yes" in rest,
        )
        Channels.forget(name)
        say.done(f"Removed {name}.")

    @staticmethod
    def enable(rest, on=True):
        name = ChannelCommand.named(rest, "enable" if on else "disable")
        Channels.keep(name, dict(Channels.settings()[name], enabled=on))
        say.done(
            f"{name} is {'enabled: tasks report to it; tinker channel start listens on it' if on else 'disabled'}."
        )

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
        for tries in range(1, FIRST_CALL_TRIES + 1):
            try:
                return channel.handle()
            except ChannelError as exc:
                raise SystemExit(f"tinker: channel {channel.name} does not work: {exc}") from None
            except OSError as exc:
                if tries == FIRST_CALL_TRIES:
                    raise SystemExit(
                        f"tinker: channel {channel.name} cannot be reached ({exc}), tried {tries} "
                        f"times: check the network and start it again"
                    ) from None
                say.retry(
                    f"{channel.name} did not answer ({exc}); trying again ({tries + 1}/{FIRST_CALL_TRIES})"
                )
                time.sleep(5)

    @staticmethod
    def named(rest, action):
        if len(rest) != 1:
            raise SystemExit(f"tinker channel {action} NAME")
        if rest[0] not in Channels.settings():
            raise SystemExit(f"tinker: no channel {rest[0]!r} (tinker channel list shows them)")
        return rest[0]


class Channels:
    @staticmethod
    def enabled():
        if started_by_a_conversation():
            return []
        return [
            Channels.make(name)
            for name, channel_settings in Channels.settings().items()
            if channel_settings.get("enabled")
        ]

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
    KIND = "telegram"
    SECRETS = (Secret("token", "Bot token (from @BotFather)"),)
    HOST = "api.telegram.org"

    def __init__(self, name, settings, secrets):
        super().__init__(name, settings, secrets)
        self.offset = None
        self.open_connections = queue.Queue()
        self.live_message_ids = {}
        self.last_words_by_chat = {}

    def handle(self):
        return "@" + self.call("getMe")["username"]

    def receive(self, timeout):
        try:
            updates = self.call("getUpdates", wait=timeout + 10, offset=self.offset, timeout=int(timeout))
        except OSError:
            return self.nothing_while_the_network_is_gone(timeout)
        messages = []
        for update in updates:
            self.offset = update["update_id"] + 1
            message = update.get("message") or {}
            if self.book_sent(message):
                message = self.book_in(message)
            if message.get("text"):
                messages.append(self.message_of(message))
                self.last_words_by_chat[str(message["chat"]["id"])] = (message["text"], time.monotonic())
            if message.get("message_id"):
                self.seen(str(message["chat"]["id"]), message["message_id"])
        return messages

    @staticmethod
    def nothing_while_the_network_is_gone(timeout):
        time.sleep(min(timeout, 10))
        return []

    def book_sent(self, message):
        return message.get("document") and str(message["chat"]["id"]) in map(str, self.allowed)

    @staticmethod
    def message_of(message):
        person = message.get("from") or {}
        sender = person.get("username") or person.get("first_name", "")
        return Message(message["chat"]["id"], message["text"], sender, message.get("message_id"))

    def book_in(self, message):
        chat, document = str(message["chat"]["id"]), message["document"]
        name = document.get("file_name") or "book"
        if document.get("file_size", 0) > TELEGRAM_MAX_DOWNLOAD_BYTES:
            self.send(
                chat,
                f"{name} is larger than a bot may download from Telegram (20 MB): send a link that "
                f"downloads it instead (read URL).",
            )
            return dict(message, text="")
        try:
            path = self.downloaded(document["file_id"], name)
        except (OSError, ChannelError, KeyError, TypeError) as exc:
            self.send(chat, f"Could not take {name}: {exc}")
            return dict(message, text="")
        return dict(message, text=f"read {path}")

    def downloaded(self, file_id, name):
        found = self.call("getFile", file_id=file_id)["file_path"]
        connection = self.connection(SENT_FILE_DOWNLOAD_TIMEOUT)
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
        menu = [
            {"command": name.replace("-", "_"), "description": what[:TELEGRAM_MAX_MENU_DESCRIPTION_CHARS]}
            for name, what in commands
        ]
        try:
            self.call("setMyCommands", commands=json.dumps(menu))
        except OSError, ChannelError:
            pass

    def ask_for(self, chat, question, hint):
        reply = {"force_reply": True, "input_field_placeholder": hint[:TELEGRAM_MAX_PLACEHOLDER_CHARS]}
        return self.send(chat, question, reply_markup=json.dumps(reply))

    def send(self, chat, text, **fields):
        try:
            sent = self.post("sendMessage", chat, text, **fields)["message_id"]
        except OSError, ChannelError:
            return None
        self.seen(str(chat), sent)
        return sent

    def edit(self, chat, sent, text):
        if sent is None:
            return self.send(chat, text)
        try:
            self.post("editMessageText", chat, text, message_id=sent)
        except ChannelError as exc:
            if not self.refused_as_unchanged(exc):
                self.delete(chat, sent)
                return self.send(chat, text)
        except OSError:
            return sent
        Ledger.touched(self.name, str(chat), sent)
        return sent

    @staticmethod
    def refused_as_unchanged(exc):
        return "not modified" in str(exc)

    def show(self, chat, shown, text, done=False, bottom=None, status=False):
        thing = None if status else Ledger.thing_of(self.name, str(chat), shown)
        now = super().show(chat, shown, text, done, bottom, status)
        if not status:
            Ledger.shown(self.name, str(chat), thing, now, text, "" if thing else self.answered(chat))
        return now

    def answered(self, chat):
        words, when = self.last_words_by_chat.pop(str(chat), ("", 0.0))
        return words if time.monotonic() - when <= ANSWER_WINDOW_SECONDS else ""

    def post(self, method, chat, text, **fields):
        text = self.said(text)
        try:
            return self.call(method, chat_id=chat, text=Markup.html(text)[:4096], parse_mode="HTML", **fields)
        except ChannelError as exc:
            if not self.html_refused(exc):
                raise
            return self.call(method, chat_id=chat, text=Markup.plain(text)[:4096], **fields)

    @staticmethod
    def html_refused(exc):
        return "parse" in str(exc) or "entit" in str(exc)

    def send_file(self, chat, path, caption):
        caption = self.said(caption)
        try:
            sent = self.call(
                "sendDocument",
                wait=120,
                file=Path(path),
                chat_id=chat,
                caption=Markup.html(caption)[:TELEGRAM_MAX_CAPTION_CHARS],
                parse_mode="HTML",
            )["message_id"]
        except OSError, ChannelError:
            return self.send(chat, caption)
        self.seen(str(chat), sent)
        return sent

    def typing(self, chat):
        try:
            self.call("sendChatAction", wait=10, chat_id=chat, action="typing")
        except OSError, ChannelError:
            pass

    def lasting(self, chat, sent):
        for message in sent:
            if message is not None:
                Ledger.lasting(self.name, str(chat), message)

    def glance(self, chat, asked, sent, keep=None):
        self.answered(chat)
        if asked:
            self.delete(chat, asked)
        for message in Ledger.glances(self.name, chat):
            self.delete(chat, message)
        Ledger.glance(self.name, str(chat), [message for message in sent if message is not None], keep)

    def collect(self):
        for chat, message in Ledger.due(self.name):
            self.delete(chat, message)

    def clear(self):
        passing = Ledger.passing(self.name)
        for chat, message in passing:
            self.delete(chat, message)
        return len(passing)

    def delete(self, chat, sent):
        alive = self.live_message_ids.get(str(chat), [])
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
        return sent is not None and sent >= max(self.live_message_ids.get(str(chat), [0]), default=0)

    def seen(self, chat, message):
        self.live_message_ids[chat] = (self.live_message_ids.get(chat, []) + [message])[-200:]
        if chat in map(str, self.allowed):
            Ledger.kept(self.name, chat, message)

    def call(self, method, wait=30, file=None, **fields):
        path = f"/bot{self.secrets.get('token', '')}/{method}"
        fields = {key: value for key, value in fields.items() if value is not None}
        data, kind = self.multipart(fields, file) if file else (urllib.parse.urlencode(fields), FORM)
        status, body = self.answer(method, path, data, wait, kind)
        for _ in range(TELEGRAM_RATE_LIMIT_WAITS):
            pause = self.retry_after(status, body)
            if pause is None:
                break
            time.sleep(pause)
            status, body = self.answer(method, path, data, wait, kind)
        return self.result(method, status, body)

    @staticmethod
    def multipart(fields, file):
        boundary = f"tinker-{secrets.token_hex(12)}"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        ]
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{file.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
            + file.read_bytes()
            + b"\r\n"
        )
        return Upload(
            b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
        )

    def answer(self, method, path, data, wait, kind=FORM):
        for attempt in (1, 2):
            try:
                return self.exchange(path, data, wait, kind)
            except http.client.HTTPException, OSError:
                if attempt == 2:
                    raise OSError(f"Telegram did not answer {method}") from None

    @staticmethod
    def retry_after(status, body):
        if status != 429:
            return None
        try:
            return float(json.loads(body)["parameters"]["retry_after"])
        except ValueError, KeyError, TypeError:
            return 1

    def exchange(self, path, data, wait, kind=FORM):
        connection = self.connection(wait)
        try:
            connection.request("POST", path, data, {"Content-Type": kind})
            response = connection.getresponse()
            answer = Answer(response.status, response.read().decode(errors="replace"))
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
    shown = (
        f"{question}" + (f" ({'/'.join(choices)})" if choices else "") + (f" [{default}]" if default else "")
    )
    if not sys.stdin.isatty():
        if default:
            return default
        raise SystemExit(
            f"tinker channel: {question} is needed (run it in a terminal, or give it as an option)"
        )
    answer = (getpass.getpass if secret else input)(f"{shown}: ").strip() or default
    if not answer:
        raise SystemExit(f"tinker channel: {question} is needed")
    return answer
