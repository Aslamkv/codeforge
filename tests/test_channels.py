"""CHN: the chat plugin: Telegram, the channels' settings and tinker channel (the Bot API is faked)."""

import io
import json
import re
import stat
import sys
import time
import urllib.parse

import pytest

import tinkerkit.channels as channels
import tinkerkit.ledger as ledger
from tinkerkit.channels import ChannelCommand, Channels, TelegramChannel, ask
from tinkerkit.conversation import ChannelError
from tinkerkit.ledger import Ledger
from tinkerkit.settings import Store


class BotAPI:
    """Telegram's Bot API, scripted per method: an answer is a result, (status, body) or an exception."""

    def __init__(self, **answers):
        self.answers = {name: list(value) if isinstance(value, list) else [value] for name, value in answers.items()}
        self.calls, self.raw = [], []  # raw: (headers, body) of each request

    def connection(self, wait):
        return Connection(self)


class Connection:
    sock = None

    def __init__(self, api):
        self.api, self.response, self.closed = api, None, False

    def request(self, method, path, data, headers):
        name = path.rsplit("/", 1)[1]
        self.api.raw.append((headers, data))
        self.api.calls.append((path, dict(urllib.parse.parse_qsl(data)) if isinstance(data, str) else {}))
        queue = self.api.answers.get(name, [{}])
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer if isinstance(answer, tuple) else (200, json.dumps({"ok": True, "result": answer}))
        self.response = io.BytesIO(body.encode())
        self.response.status = status

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def telegram(api, chats=("42",)):
    channel = TelegramChannel("bot", {"allowed": list(chats)}, {"token": "T0K"})
    channel.connection = api.connection
    return channel


@pytest.mark.spec("CHN-1")
def test_channels_settings_and_secrets(tinker_home, home_store):
    assert Channels.settings() == {}
    Channels.keep("bot", {"kind": "telegram", "enabled": True, "allowed": ["42"]}, {"token": "secret"})
    assert stat.S_IMODE((tinker_home / "tinker.db").stat().st_mode) == 0o600
    assert "secret" not in json.dumps(Channels.settings()) and Channels.secrets("bot") == {"token": "secret"}
    made = Channels.make("bot")
    assert isinstance(made, TelegramChannel) and made.secrets == {"token": "secret"} and made.allowed == {"42"}
    assert [c.name for c in Channels.enabled()] == ["bot"]
    with pytest.raises(SystemExit, match="no channel 'other'"):
        Channels.make("other")
    Channels.keep("odd", {"kind": "pigeon"})
    with pytest.raises(SystemExit, match="unknown kind 'pigeon'"):
        Channels.make("odd")
    (tinker_home / "tinker.db").write_bytes(b"not a database at all, just text " * 100)
    with pytest.raises(SystemExit, match="cannot be read"):
        Channels.settings()


@pytest.mark.spec("CHN-1")
def test_a_database_made_readable_to_others_is_made_private(tmp_path):
    path = tmp_path / "old.db"
    path.touch(mode=0o644)
    Store(path).put("channel_secrets", "bot", {"token": "secret"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.spec("CHN-1")
def test_a_run_told_not_to_report_has_no_channels(monkeypatch):
    Channels.keep("bot", {"kind": "telegram", "enabled": True}, {})
    monkeypatch.setenv("TINKER_CHANNELS", "off")
    assert Channels.enabled() == []


@pytest.mark.spec("CHN-2")
def test_channel_command_list_enable_disable_remove(capsys):
    ChannelCommand.run([])
    assert "No channels yet" in capsys.readouterr().out
    Channels.keep("bot", {"kind": "telegram", "enabled": False, "allowed": ["42"]}, {"token": "t"})
    ChannelCommand.run(["enable", "bot"])
    assert Channels.settings()["bot"]["enabled"] is True
    capsys.readouterr()
    ChannelCommand.run(["list"])
    listed = capsys.readouterr().out
    assert "bot" in listed and "telegram" in listed and "enabled" in listed and "chats: 42" in listed
    ChannelCommand.run(["disable", "bot"])
    assert Channels.settings()["bot"]["enabled"] is False
    ChannelCommand.run(["remove", "bot"])
    assert Channels.settings() == {} and Channels.secrets("bot") == {}
    with pytest.raises(SystemExit, match="no action 'fly'"):
        ChannelCommand.run(["fly"])
    with pytest.raises(SystemExit, match="no channel 'bot'"):
        ChannelCommand.run(["enable", "bot"])
    with pytest.raises(SystemExit, match="tinker channel enable NAME"):
        ChannelCommand.run(["enable"])
    ChannelCommand.run(["help"])
    assert "tinker channel start" in capsys.readouterr().out


@pytest.mark.spec("CHN-2")
def test_channel_add_from_options(monkeypatch, capsys):
    api = BotAPI(getMe={"username": "my_bot"})
    monkeypatch.setattr(TelegramChannel, "connection", lambda self, wait: api.connection(wait))
    ChannelCommand.run(["add", "--kind", "telegram", "--name", "home", "--token", "abc", "--chat", "42, 43"])
    assert Channels.settings()["home"] == {"kind": "telegram", "enabled": False, "allowed": ["42", "43"], "who": "@my_bot"}
    assert Channels.secrets("home") == {"token": "abc"}
    assert api.calls[0][0] == "/botabc/getMe" and "It starts disabled" in capsys.readouterr().out
    for argv, why in ((["--name", "home"], "exists"), (["--name", "-bad"], "letters, digits"),
                      (["--kind", "pigeon"], "no channel kind"), (["oops"], "unexpected 'oops'")):
        with pytest.raises(SystemExit, match=why):
            ChannelCommand.run(["add", "--kind", "telegram", "--token", "abc", "--chat", "1"] + argv
                               if argv[0] != "--kind" and argv[0] != "oops" else ["add"] + argv)


@pytest.mark.spec("CHN-2")
def test_channel_add_with_a_token_that_does_not_work(monkeypatch):
    api = BotAPI(getMe=(401, '{"ok": false, "description": "Unauthorized"}'))
    monkeypatch.setattr(TelegramChannel, "connection", lambda self, wait: api.connection(wait))
    with pytest.raises(SystemExit, match="does not work with that: Telegram refused getMe: 401"):
        ChannelCommand.run(["add", "--kind", "telegram", "--name", "home", "--token", "bad", "--chat", "1"])
    assert Channels.settings() == {}


@pytest.mark.spec("CHN-3")
def test_call_posts_form_fields_and_gives_the_result():
    api = BotAPI(sendMessage={"message_id": 7})
    assert telegram(api).call("sendMessage", chat_id="42", text="hi", parse_mode=None) == {"message_id": 7}
    assert api.calls == [("/botT0K/sendMessage", {"chat_id": "42", "text": "hi"})]


@pytest.mark.spec("CHN-3")
def test_call_refused_and_dropped():
    with pytest.raises(ChannelError, match="Telegram refused getMe: 409"):
        telegram(BotAPI(getMe=(409, "Conflict"))).call("getMe")
    once = BotAPI(getMe=[OSError("reset"), {"username": "b"}])
    assert telegram(once).call("getMe") == {"username": "b"} and len(once.calls) == 2
    with pytest.raises(OSError, match="Telegram did not answer getMe"):
        telegram(BotAPI(getMe=[OSError("reset"), OSError("reset")])).call("getMe")


@pytest.mark.spec("CHN-3")
def test_call_waits_out_too_many_requests(monkeypatch):
    slept = []
    monkeypatch.setattr(channels.time, "sleep", slept.append)
    busy = (429, json.dumps({"ok": False, "error_code": 429, "parameters": {"retry_after": 5}}))
    assert telegram(BotAPI(getMe=[busy, {"username": "b"}])).call("getMe") == {"username": "b"}
    assert slept == [5]
    with pytest.raises(ChannelError, match="Telegram refused getMe: 429"):
        telegram(BotAPI(getMe=busy)).call("getMe")


@pytest.mark.spec("CHN-4")
def test_receive(monkeypatch):
    updates = [{"update_id": 10, "message": {"message_id": 5, "text": "hello", "chat": {"id": 42},
                                             "from": {"username": "me"}}},
               {"update_id": 11, "message": {"message_id": 6, "chat": {"id": 42}, "photo": []}}]
    api = BotAPI(getUpdates=[updates])
    channel = telegram(api)
    messages = channel.receive(1)
    assert [(m.chat, m.text, m.sender, m.id) for m in messages] == [("42", "hello", "me", 5)]
    assert channel.offset == 12 and channel.heard["42"] == [5, 6] and channel.newest["42"] == 6
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    assert telegram(BotAPI(getUpdates=[OSError("down"), OSError("down")])).receive(1) == []


@pytest.mark.spec("CHN-4")
def test_skip_waiting_moves_past_old_messages():
    channel = telegram(BotAPI(getUpdates=[[{"update_id": 99}]]))
    channel.skip_waiting()
    assert channel.offset == 100


@pytest.mark.spec("CHN-4")
def test_offer_lists_the_commands_in_the_bots_menu():
    api = BotAPI(setMyCommands=True)
    telegram(api).offer([("use-model", "the model every build uses"), ("help", "x" * 300)])
    path, fields = api.calls[-1]
    menu = json.loads(fields["commands"])
    assert path.endswith("/setMyCommands") and [m["command"] for m in menu] == ["use_model", "help"]
    assert len(menu[1]["description"]) == 256
    telegram(BotAPI(setMyCommands=OSError("down"))).offer([("help", "this list")])


@pytest.mark.spec("CHN-5")
def test_send_and_edit_post_html_with_a_plain_fallback():
    api = BotAPI(sendMessage=[(400, "Bad Request: can't parse entities"), {"message_id": 8}],
                 editMessageText=[(400, "message is not modified"), (400, "message to edit not found")])
    channel = telegram(api)
    assert channel.send("42", "**hi** tinker resume abc") == 8
    html_call, plain_call = api.calls[0][1], api.calls[1][1]
    assert html_call["text"] == "<b>hi</b> resume abc" and html_call["parse_mode"] == "HTML"
    assert plain_call["text"] == "hi resume abc" and "parse_mode" not in plain_call
    api.answers["sendMessage"] = [{"message_id": 9}]
    assert channel.edit("42", 8, "same") == 8  # unchanged text: harmless
    assert channel.edit("42", 8, "new text") == 9  # the message is gone: sent anew
    assert [path.rsplit("/", 1)[1] for path, _ in api.calls[-2:]] == ["deleteMessage", "sendMessage"]
    assert channel.edit("42", None, "x") == 9


@pytest.mark.spec("CHN-5")
def test_latest_and_delete():
    api = BotAPI(sendMessage=[{"message_id": 1}, {"message_id": 2}, {"message_id": 3}])
    channel = telegram(api)
    for _ in range(3):
        channel.send("42", "m")
    assert channel.latest("42", 3) and not channel.latest("42", 2) and not channel.latest("42", None)
    channel.delete("42", 3)
    assert channel.latest("42", 2) and api.calls[-1][0].endswith("/deleteMessage")

def methods(api):
    return [path.rsplit("/", 1)[1] for path, _ in api.calls]

@pytest.mark.spec("CHN-9")
def test_the_status_is_the_one_pin():
    api = BotAPI(getChat=[{"pinned_message": {"message_id": 7}}, {"pinned_message": {"message_id": 5}}, {}])
    channel = telegram(api)
    notes = []
    channel.drop_pin_note = lambda chat, after, wait=2: notes.append(wait)
    channel.pin("42", 2)  # Tinker's first pin: every pin from before taken out, one at a time
    assert methods(api) == ["getChat", "unpinChatMessage", "getChat", "unpinChatMessage", "getChat", "pinChatMessage"]
    assert [fields["message_id"] for path, fields in api.calls if path.endswith("/unpinChatMessage")] == ["7", "5"]
    api.calls.clear()
    channel.closed = True
    channel.pin("42", 3)  # a new status after it: the one before goes with its message
    assert methods(api) == ["pinChatMessage"] and channel.pinned["42"] == 3 and notes == [2, 0]  # ending: the note at once
    api.answers["getChat"] = [{"pinned_message": {"message_id": 3}}]
    started_again = telegram(api)
    api.calls.clear()
    started_again.pin("42", 3)  # pinned already: left as it is
    started_again.pin("42", 3)
    assert methods(api) == ["getChat"] and started_again.pinned["42"] == 3

@pytest.mark.spec("CHN-10")
def test_the_ledger_keeps_every_message_and_collects_them(monkeypatch):
    update = {"update_id": 1, "message": {"message_id": 4, "text": "task calc", "chat": {"id": 42}}}
    api = BotAPI(getUpdates=[[update]], sendMessage=[{"message_id": 1}, {"message_id": 2}, {"message_id": 3}])
    channel = telegram(api)
    channel.receive(1)
    shown = channel.show("42", None, "🔨 calc")
    assert shown == [1] and Ledger.thing(1)["asked"] == "task calc" and Ledger.thing(1)["text"] == "🔨 calc"
    assert channel.show("42", shown, "🔨 calc ✅") == [1] and Ledger.thing(1)["text"] == "🔨 calc ✅"
    assert sorted(message for _, message in Ledger.passing("bot")) == [1, 4]
    channel.glance("42", 4, [channel.send("42", "jobs")])  # the person's message goes at once
    channel.glance("42", None, [channel.send("42", "help")])  # the glance before goes too
    assert Ledger.glances("bot", "42") == [3]
    assert [fields["message_id"] for path, fields in api.calls if path.endswith("/deleteMessage")] == ["4", "2"]
    now = time.time()
    monkeypatch.setattr(ledger.time, "time", lambda: now + 200)
    channel.collect()
    assert sorted(message for _, message in Ledger.passing("bot")) == [1]  # the glance went, the task stays a day
    api.answers["deleteMessage"] = [OSError("down")]
    monkeypatch.setattr(ledger.time, "time", lambda: now + 2 * 24 * 3600)
    channel.collect()
    assert Ledger.due("bot") == [("42", 1)]  # no network: taken out later
    api.answers["deleteMessage"] = [(400, "Bad Request: message can't be deleted")]
    channel.collect()
    assert channel.clear() == 0 and Ledger.thing(1)["text"] == "🔨 calc ✅"  # forgotten; the history stays


@pytest.mark.spec("CHN-6")
def test_ask_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert ask("Channel name", "telegram") == "telegram"
    with pytest.raises(SystemExit, match="Bot token is needed"):
        ask("Bot token", secret=True)


@pytest.mark.spec("CHN-6")
def test_check_tries_a_dropped_network_again(monkeypatch, capsys):
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    flaky = telegram(BotAPI(getMe=[OSError("x"), OSError("x"), {"username": "b"}]))
    assert ChannelCommand.check(flaky) == "@b" and "trying again (2/3)" in capsys.readouterr().out
    down = telegram(BotAPI(getMe=OSError("down")))
    with pytest.raises(SystemExit, match="cannot be reached .* tried 3 times"):
        ChannelCommand.check(down)
    with pytest.raises(SystemExit, match="does not work: Telegram refused getMe"):
        ChannelCommand.check(telegram(BotAPI(getMe=(401, "no"))))


@pytest.mark.spec("CHN-7")
def test_pair_serves_the_chat_its_sender_confirms(monkeypatch, capsys):
    updates = [{"update_id": 1, "message": {"message_id": 1, "text": "hi", "chat": {"id": 7}, "from": {"username": "eve"}}},
               {"update_id": 2, "message": {"message_id": 2, "text": "me!", "chat": {"id": 42},
                                            "from": {"username": "owner"}}}]
    api = BotAPI(getUpdates=[[], updates], sendMessage={"message_id": 3})
    answers = iter(["n", "y"])
    monkeypatch.setattr(channels, "ask", lambda question, default="", **kw: next(answers))
    assert ChannelCommand.pair(telegram(api, chats=()), "@my_bot") == ["42"]
    sent = [fields for path, fields in api.calls if path.endswith("/sendMessage")]
    assert sent and sent[0]["chat_id"] == "42" and "Paired with Tinker" in sent[0]["text"]


@pytest.mark.spec("CHN-7")
def test_start_needs_a_channel_and_checks_it(monkeypatch):
    with pytest.raises(SystemExit, match="no channel is enabled"):
        ChannelCommand.run(["start"])
    Channels.keep("bot", {"kind": "telegram", "enabled": False, "allowed": ["42"]}, {"token": "t"})
    api = BotAPI(getMe=(401, "Unauthorized"))
    monkeypatch.setattr(TelegramChannel, "connection", lambda self, wait: api.connection(wait))
    with pytest.raises(SystemExit, match="channel bot does not work"):
        ChannelCommand.run(["start", "bot"])


@pytest.mark.spec("CHN-8")
def test_send_file_uploads_a_document_with_its_caption(tmp_path):
    page = tmp_path / "memory.html"
    page.write_bytes(b"<!doctype html><p>graph</p>")
    api = BotAPI(sendDocument={"message_id": 12})
    channel = telegram(api)
    assert channel.send_file("42", page, "**Tinker's memory** " + "x" * 2000) == 12
    (headers, body), = api.raw
    boundary = headers["Content-Type"].split("boundary=")[1]
    assert headers["Content-Type"].startswith("multipart/form-data") and api.calls[0][0] == "/botT0K/sendDocument"
    parts = body.split(f"--{boundary}".encode())
    named = {re.search(rb'name="(\w+)"', part).group(1).decode(): part.split(b"\r\n\r\n", 1)[1][:-2]
             for part in parts[1:-1]}
    assert named["chat_id"] == b"42" and named["parse_mode"] == b"HTML"
    assert named["caption"].startswith(b"<b>Tinker's memory</b> xx") and len(named["caption"].decode()) == 1024
    assert named["document"] == b"<!doctype html><p>graph</p>" and b'filename="memory.html"' in body
    assert parts[-1] == b"--\r\n" and channel.alive["42"] == [12]
    failing = telegram(BotAPI(sendDocument=(400, "Bad Request: file too big"), sendMessage={"message_id": 13}))
    assert failing.send_file("42", page, "the caption") == 13

def sent_file(chat=42, size=10, name="My Book.epub"):
    return [[{"update_id": 1, "message": {"message_id": 5, "chat": {"id": chat}, "from": {"username": "me"},
                                          "document": {"file_id": "F1", "file_name": name, "file_size": size}}}]]

@pytest.mark.spec("CHN-11")
def test_a_book_sent_in_a_chat_is_kept_and_read(tinker_home, home_store):
    api = BotAPI(getUpdates=sent_file(), getFile={"file_path": "documents/file_1.epub"},
                 **{"file_1.epub": (200, "EPUB BYTES")})
    messages = telegram(api).receive(1)
    path = tinker_home / "reading" / "My-Book.epub"
    assert [m.text for m in messages] == [f"read {path}"] and path.read_bytes() == b"EPUB BYTES"
    assert api.calls[-1][0] == "/file/botT0K/documents/file_1.epub" and "T0K" not in messages[0].text

@pytest.mark.spec("CHN-11")
def test_a_book_that_cannot_be_taken_is_said_and_one_from_a_stranger_ignored(tinker_home, home_store):
    api = BotAPI(getUpdates=sent_file(size=30_000_000), sendMessage={"message_id": 9})
    assert telegram(api).receive(1) == []
    assert "larger than a bot may download" in api.calls[-1][1]["text"]
    api = BotAPI(getUpdates=sent_file(), getFile={"file_path": "documents/file_1.epub"},
                 sendMessage={"message_id": 9}, **{"file_1.epub": (404, "gone")})
    assert telegram(api).receive(1) == []
    assert "Could not take My Book.epub: Telegram answered 404" in api.calls[-1][1]["text"]
    api = BotAPI(getUpdates=sent_file(chat=7))
    assert telegram(api).receive(1) == [] and [path for path, _ in api.calls] == ["/botT0K/getUpdates"]
    assert not (tinker_home / "reading").exists()
