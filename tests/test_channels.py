"""CHN: the chat plugin: Telegram, the channels' settings and tinker channel (the Bot API is faked)."""

import io
import json
import stat
import sys
import urllib.parse

import pytest

import tinkerkit.channels as channels
from tinkerkit.channels import ChannelCommand, Channels, TelegramChannel, ask
from tinkerkit.conversation import ChannelError


class BotAPI:
    """Telegram's Bot API, scripted per method: an answer is a result, (status, body) or an exception."""

    def __init__(self, **answers):
        self.answers = {name: list(value) if isinstance(value, list) else [value] for name, value in answers.items()}
        self.calls = []

    def connection(self, wait):
        return Connection(self)


class Connection:
    sock = None

    def __init__(self, api):
        self.api, self.response, self.closed = api, None, False

    def request(self, method, path, data, headers):
        name = path.rsplit("/", 1)[1]
        self.api.calls.append((path, dict(urllib.parse.parse_qsl(data))))
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
def test_channels_settings_and_secrets(tinker_home):
    assert Channels.settings() == {}
    Channels.write({"bot": {"kind": "telegram", "enabled": True, "allowed": ["42"]}}, {"bot": {"token": "secret"}})
    assert stat.S_IMODE((tinker_home / "channel-secrets.json").stat().st_mode) == 0o600
    assert "secret" not in (tinker_home / "channels.json").read_text()
    made = Channels.make("bot")
    assert isinstance(made, TelegramChannel) and made.secrets == {"token": "secret"} and made.allowed == {"42"}
    assert [c.name for c in Channels.enabled()] == ["bot"]
    with pytest.raises(SystemExit, match="no channel 'other'"):
        Channels.make("other")
    Channels.write({"odd": {"kind": "pigeon"}})
    with pytest.raises(SystemExit, match="unknown kind 'pigeon'"):
        Channels.make("odd")
    (tinker_home / "channels.json").write_text("{broken")
    with pytest.raises(SystemExit, match="cannot be read"):
        Channels.settings()


@pytest.mark.spec("CHN-1")
def test_a_run_told_not_to_report_has_no_channels(monkeypatch):
    Channels.write({"bot": {"kind": "telegram", "enabled": True}}, {})
    monkeypatch.setenv("TINKER_CHANNELS", "off")
    assert Channels.enabled() == []


@pytest.mark.spec("CHN-2")
def test_channel_command_list_enable_disable_remove(capsys):
    ChannelCommand.run([])
    assert "No channels yet" in capsys.readouterr().out
    Channels.write({"bot": {"kind": "telegram", "enabled": False, "allowed": ["42"]}}, {"bot": {"token": "t"}})
    ChannelCommand.run(["enable", "bot"])
    assert Channels.settings()["bot"]["enabled"] is True
    capsys.readouterr()
    ChannelCommand.run(["list"])
    listed = capsys.readouterr().out
    assert "bot" in listed and "telegram" in listed and "enabled" in listed and "chats: 42" in listed
    ChannelCommand.run(["disable", "bot"])
    assert Channels.settings()["bot"]["enabled"] is False
    ChannelCommand.run(["remove", "bot"])
    assert Channels.settings() == {} and Channels.read(channels.SECRETS_FILE) == {}
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
    assert Channels.read(channels.SECRETS_FILE) == {"home": {"token": "abc"}}
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
    assert channel.edit("42", None, "x") == 9


@pytest.mark.spec("CHN-5")
def test_latest_delete_and_pin():
    api = BotAPI(sendMessage=[{"message_id": 1}, {"message_id": 2}, {"message_id": 3}])
    channel = telegram(api)
    for _ in range(3):
        channel.send("42", "m")
    assert channel.latest("42", 3) and not channel.latest("42", 2) and not channel.latest("42", None)
    channel.delete("42", 3)
    assert channel.latest("42", 2) and api.calls[-1][0].endswith("/deleteMessage")
    channel.pinned["42"] = 1
    channel.drop_pin_note = lambda chat, after: None
    channel.pin("42", 2)
    methods = [path.rsplit("/", 1)[1] for path, _ in api.calls[-2:]]
    assert methods == ["unpinChatMessage", "pinChatMessage"] and channel.pinned["42"] == 2
    channel.pin("42", 2)
    assert len(api.calls) == 6  # pinned already: no call


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
    Channels.write({"bot": {"kind": "telegram", "enabled": False, "allowed": ["42"]}}, {"bot": {"token": "t"}})
    api = BotAPI(getMe=(401, "Unauthorized"))
    monkeypatch.setattr(TelegramChannel, "connection", lambda self, wait: api.connection(wait))
    with pytest.raises(SystemExit, match="channel bot does not work"):
        ChannelCommand.run(["start", "bot"])
