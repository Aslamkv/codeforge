import io
import json
import urllib.error
import urllib.request

import pytest
from conftest import MEASURED, MODEL

import tinkerkit.model as model_module
from tinkerkit.messages import say
from tinkerkit.model import Answer, Calibrator, Model, Models, Ollama, OllamaError, Stream
from tinkerkit.prompts import Part, Prompts, PromptTooLarge, Reply
from tinkerkit.settings import PROMPT_BUDGET_SHARE, STARTING_CHARS_PER_TOKEN, Config, Home


class Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def stream_of(*chunks):
    return Body(b"".join(json.dumps(chunk).encode() + b"\n" for chunk in chunks))


def http_error(code, body=b""):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


@pytest.mark.spec("MOD-1")
def test_measured_settings_are_kept_by_model_name(home_store):
    assert Models.settings("qwen3:8b") is None
    Models.keep("qwen3:8b", {"num_ctx": 1})
    assert Models.settings("qwen3:8b") == {"num_ctx": 1} and home_store.get("models", "qwen3:8b") == {
        "num_ctx": 1
    }
    home_store.put("models", "broken", "not a dict")
    assert Models.settings("broken") is None


@pytest.mark.spec("MOD-2")
def test_selected_needs_a_measured_model(tinker_home):
    with pytest.raises(SystemExit, match="No model in use"):
        Models.selected()
    Config.save(**{"model": "m"})
    with pytest.raises(SystemExit, match="has not been measured"):
        Models.selected()
    Models.keep("m", dict(MEASURED, num_ctx=0, fix_max_tokens=True))
    with pytest.raises(SystemExit, match="no usable num_ctx, fix_max_tokens"):
        Models.selected()
    Models.keep("m", MEASURED)
    assert Models.selected() == ("m", MEASURED)


@pytest.mark.spec("MOD-25")
def test_quick_calls_use_the_measured_model(measured_model, fake_ollama, tinker_home):
    assert Models.quick() == (MODEL, {"num_ctx": 32768}, False)
    Models.keep(MODEL, dict(MEASURED, think=None, num_batch=512))
    assert Models.quick() == (MODEL, {"num_ctx": 32768, "num_batch": 512}, None)
    fake_ollama.reply("  hi there \n")
    assert Models.quick_reply([{"role": "user", "content": "hi"}], 7) == "hi there"
    call = fake_ollama.calls[-1]
    assert call["options"] == {"num_ctx": 32768, "num_batch": 512, "num_predict": 7}
    assert call["timeout"] == model_module.QUICK_REPLY_TIMEOUT and call["keep_alive"] is not None
    fake_ollama.reply(OllamaError("down"))
    with pytest.raises(OllamaError, match="down"):
        Models.quick_reply([{"role": "user", "content": "hi"}], 7)
    Config.save(**{"model": None})
    assert Models.quick() is None


@pytest.mark.spec("MOD-3")
def test_installed(monkeypatch):
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: None))
    with pytest.raises(SystemExit, match="Ollama does not answer"):
        Models.installed("qwen3")
    monkeypatch.setattr(
        Ollama, "installed_models", staticmethod(lambda: [{"name": "qwen3:8b"}, {"name": "llama3"}])
    )
    assert Models.installed("llama3") == "llama3"
    with pytest.raises(SystemExit, match="Did you mean qwen3:8b"):
        Models.installed("qwen3")


@pytest.mark.spec("MOD-4")
def test_context_for_and_reply_limits():
    assert Models.context_for(8192, 16384, None) == 25600
    assert Models.context_for(1000, 1000, None) == 3072
    assert Models.context_for(8192, 16384, 16000) == 16000
    assert Models.reply_limits(16384) == {"max_reply_tokens": 16384, "fix_max_tokens": 4096}
    assert Models.reply_limits(2000)["fix_max_tokens"] == 2000


@pytest.mark.spec("MOD-5")
def test_remember_keeps_the_learned_chars_per_token(measured_model, make_model, capsys):
    model = make_model()
    model.recent_chars_per_token = [3.5, 2.9, 3.1, 3.3, 3.0]
    model.crashes = 2
    model.max_tokens = 5000
    Models.remember(model)
    assert Models.settings(MODEL)["chars_per_token"] == 2.9
    assert "crashed 2 time(s)" in capsys.readouterr().out
    too_few_to_learn_from = [3.0] * 4
    model.recent_chars_per_token = too_few_to_learn_from
    Models.remember(model)
    assert Models.settings(MODEL)["chars_per_token"] == 2.9


@pytest.mark.spec("MOD-6")
def test_error_kinds():
    assert Ollama.is_oom("CUDA error: out of memory") and Ollama.is_oom(
        "model requires more system memory: insufficient memory"
    )
    assert not Ollama.is_oom(None)
    for crash in (
        "llama runner process has terminated: signal: killed",
        "runner failed",
        "panic: x",
        "Segmentation fault",
    ):
        assert Ollama.is_crash(crash)
    assert not Ollama.is_crash("model not found")


@pytest.mark.spec("MOD-7")
def test_get_json(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Body(b'{"models": []}'))
    assert Ollama.get_json("/api/tags") == {"models": []}

    def fail(request, timeout):
        raise http_error(404, b'{"error": "no such model"}')

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert Ollama.get_json("/api/show", {"model": "x"}) is None
    assert Ollama.get_json("/api/show", {"model": "x"}, errors=True) == {"error": "no such model"}

    def fail_plain(request, timeout):
        raise http_error(500, b"<html>")

    monkeypatch.setattr(urllib.request, "urlopen", fail_plain)
    assert Ollama.get_json("/api/embed", {}, errors=True) == {"error": "HTTP 500"}


@pytest.mark.spec("MOD-7")
def test_get_json_without_ollama_gives_none():
    assert Ollama.get_json("/api/tags") is None
    assert Ollama.installed_models() is None and Ollama.loaded_models() is None


@pytest.mark.spec("MOD-8")
def test_chat_streams_and_gives_the_final_chunk(monkeypatch):
    sent = []

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return stream_of(
            {"message": {"content": "", "thinking": "let me "}},
            {"message": {"content": "Hel"}},
            {"message": {"content": "lo"}},
            {"done": True, "done_reason": "stop", "eval_count": 3},
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    final = Ollama.chat("m", [{"role": "user", "content": "hi"}], 0, {"num_ctx": 2048}, think="high")
    assert final["message"] == {"role": "assistant", "content": "Hello", "thinking": "let me "}
    assert final["done_reason"] == "stop"
    assert sent[0] == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "keep_alive": 0,
        "think": "high",
        "options": {"num_ctx": 2048},
    }
    Ollama.chat("m", [], "5m", None)
    assert "think" not in sent[1] and "options" not in sent[1] and sent[1]["keep_alive"] == "5m"


@pytest.mark.spec("MOD-8")
def test_chat_errors(monkeypatch):
    def oom(request, timeout):
        raise http_error(500, b'{"error":"model requires more system memory: out of memory"}')

    monkeypatch.setattr(urllib.request, "urlopen", oom)
    with pytest.raises(OllamaError) as error:
        Ollama.chat("m", [], 0, None)
    assert error.value.oom and not error.value.crashed and "HTTP 500" in str(error.value)


@pytest.mark.spec("MOD-8")
def test_chat_without_ollama():
    with pytest.raises(OllamaError, match="Could not reach Ollama"):
        Ollama.chat("m", [{"role": "user", "content": "x"}], 0, None)


@pytest.mark.spec("MOD-9")
def test_stream_keeps_the_end_of_the_thought(monkeypatch):
    monkeypatch.setattr(model_module, "UNFINISHED_THOUGHT_KEPT_CHARS", 10)
    stream = Stream(None, None)
    for piece in ("a" * 8, "b" * 8):
        stream.take({"message": {"thinking": piece}})
    assert stream.thought_tail == "aa" + "b" * 8 and stream.thinking == 2


@pytest.mark.spec("MOD-9")
def test_stream_says_live_the_step_never_what_the_model_is_doing(monkeypatch):
    clock = iter([0, 3, 6])
    monkeypatch.setattr(model_module.time, "perf_counter", lambda: next(clock))
    seen = []
    say.follow(lambda message: seen.append(message.text) if message.kind == "live" else None)
    say.step("Writing the spec…", "📝")
    stream = Stream("Write calc.py", None)
    stream.take({"message": {"thinking": "hm"}})
    stream.take({"message": {"content": "x"}})
    assert seen == ["Writing the spec… 3s", "Writing the spec… 6s"]


@pytest.mark.spec("MOD-9")
def test_what_the_command_works_on_is_said_live_and_heard(monkeypatch):
    clock = iter([0, 3, 6])
    monkeypatch.setattr(model_module.time, "perf_counter", lambda: next(clock))
    seen = []
    say.follow(
        lambda message: (
            seen.append((message.kind, message.text)) if message.kind in ("live", "working") else None
        )
    )
    stream = Stream("Read: chapter 2", None)
    with say.working_on("Reading chapter 2, Habits (1 of 3)"):
        stream.take({"message": {"thinking": "hm"}})
        stream.take({"message": {"content": "x"}})
    assert seen == [
        ("working", "Reading chapter 2, Habits (1 of 3)"),
        ("live", "Reading chapter 2, Habits (1 of 3)"),
        ("live", "Reading chapter 2, Habits (1 of 3)… 3s"),
        ("live", "Reading chapter 2, Habits (1 of 3)… 6s"),
        ("working", ""),
    ]
    assert say.doing == ""


@pytest.mark.spec("MOD-9")
def test_a_stream_that_writes_one_line_again_and_again_is_cut(monkeypatch):
    monkeypatch.setattr(model_module, "LOOPING_REPEATED_LINES", 4)
    stream = Stream(None, None)
    ends = [
        stream.take({"message": {"content": line}}) for line in ["[tool]\n", "x = 1\n", "x = 1\n", "x = 1\n"]
    ]
    assert ends == [False] * 4
    assert stream.take({"message": {"content": "x = 1\n"}}) and stream.final["done_reason"] == "length"
    calm = Stream(None, None)
    assert not any(calm.take({"message": {"content": piece}}) for piece in ["\n"] * 6 + ["a\n", "b\n"] * 4)


@pytest.mark.spec("MOD-9")
def test_stream_error_chunks():
    stream = Stream(None, None)
    stream.take({"message": {"content": "partial "}})
    with pytest.raises(OllamaError) as error:
        stream.take({"error": "failed to parse tool call"})
    assert error.value.in_reply and error.value.partial == "partial " and error.value.streamed == 1
    with pytest.raises(OllamaError) as error:
        Stream(None, None).take({"error": "runner process has terminated"})
    assert error.value.crashed and not error.value.in_reply


@pytest.mark.spec("MOD-9")
def test_stream_stops_soon_after_the_reply_is_complete(monkeypatch):
    clock = iter(range(0, 1000))
    monkeypatch.setattr(model_module.time, "perf_counter", lambda: next(clock))
    stream = Stream(None, lambda content: content.endswith("}"))
    response = [
        json.dumps({"message": {"content": piece}}).encode()
        for piece in ['{"a": 1', "}", " junk", " more junk", " and more", " and more"]
    ]
    stream.read(response)
    assert (
        stream.reply == '{"a": 1}'
        and stream.final["early_stop"]
        and stream.final["done_reason"] == "complete"
    )
    assert stream.content.startswith('{"a": 1} junk')


@pytest.mark.spec("MOD-10")
@pytest.mark.parametrize(
    "context,shared",
    [(24576, (8192, 16384)), (4096, (1365, 2731)), (1200, (512, 1024)), (100000, (8192, 16384))],
)
def test_shared(context, shared):
    assert Calibrator.shared(context) == shared


@pytest.mark.spec("MOD-10")
def test_safe_ratio():
    calibrator = Calibrator("m")
    assert calibrator.safe_ratio() == STARTING_CHARS_PER_TOKEN
    calibrator.measured_chars_per_token = 3.0
    assert calibrator.safe_ratio() == 2.91
    calibrator.measured_chars_per_token = 9.0
    assert calibrator.safe_ratio() == 4.0
    calibrator.measured_chars_per_token = 1.0
    assert calibrator.safe_ratio() == 2.0


class Probed(Calibrator):
    def __init__(self, limit):
        super().__init__("m")
        self.limit, self.tried = limit, []

    def reads(self, context, gguf):
        self.tried.append(context)
        return context <= self.limit


@pytest.mark.spec("MOD-11")
def test_capacity():
    assert Probed(30000).capacity(24576, {}) == (24576, None)
    probed = Probed(10000)
    good, bad = probed.capacity(24576, {})
    assert good <= 10000 < bad and bad - good <= 0.1 * bad
    with pytest.raises(SystemExit, match="cannot hold even 1536"):
        Probed(1000).capacity(24576, {})


@pytest.mark.spec("MOD-12")
def test_check_fit(monkeypatch):
    calibrator = Calibrator("big")
    monkeypatch.setattr(Calibrator, "gpu_memory", classmethod(lambda cls: 16e9))
    monkeypatch.setattr(Calibrator, "total_memory", staticmethod(lambda: 24e9))
    with pytest.raises(SystemExit, match="needs 15.0 GB for its weights alone"):
        calibrator.check_fit({"size": 15e9})
    calibrator.check_fit({"size": 10e9})
    calibrator.check_fit({"size": None})
    monkeypatch.setattr(Calibrator, "gpu_memory", classmethod(lambda cls: None))
    calibrator.check_fit({"size": 99e9})


@pytest.mark.spec("MOD-13")
def test_calibrate_writes_the_measured_settings(fake_ollama, monkeypatch, no_sleep, tinker_home):
    monkeypatch.setattr(
        Ollama,
        "model_facts",
        staticmethod(
            lambda name: {
                "context_length": 40960,
                "capabilities": ["completion", "thinking"],
                "format": "gguf",
                "size": 5e9,
            }
        ),
    )
    monkeypatch.setattr(Calibrator, "gpu_memory", classmethod(lambda cls: 20e9))
    monkeypatch.setattr(
        Ollama,
        "read_raw",
        staticmethod(
            lambda name, prompt, options, timeout=0: {
                "prompt_eval_count": len(prompt),
                "prompt_eval_duration": 2e9,
            }
        ),
    )
    fake_ollama.reply({"content": "OK", "prompt_eval_count": 400, "eval_count": 2, "eval_duration": 1e8})
    settings = Calibrator("m").run()
    saved = Models.settings("m")
    assert saved == settings and Home.store().get("models", "m") == settings
    assert saved["max_prompt_tokens"] == 8192 and saved["max_reply_tokens"] == 16384
    assert saved["fix_max_tokens"] == 4096 and saved["num_ctx"] == 25600 and saved["think"] == "high"
    assert saved["probes"] == [[8192, "read"]] and 2.0 <= saved["chars_per_token"] <= 4.0


@pytest.mark.spec("MOD-16")
def test_model_settings(make_model):
    model = make_model(num_batch=256, chars_per_token=9)
    assert (model.max_tokens, model.num_ctx, model.max_reply, model.fix_max) == (8192, 32768, 16384, 4096)
    assert model.options == {"num_batch": 256} and model.chars_per_token == 4.0
    assert model.build_think == "medium" and model.think_now() == "high"
    model.is_building = True
    assert model.think_now() == "medium"
    assert make_model(think=None).build_think is None
    assert model.limit_chars("s" * 100) == int(8192 * PROMPT_BUDGET_SHARE * 4.0) - 140
    assert model.limit_chars("", tokens=1000) == int(1000 * PROMPT_BUDGET_SHARE * 4.0) - 40


@pytest.mark.spec("MOD-17")
def test_ask_once(make_model, fake_ollama, model_log):
    fake_ollama.reply("the answer")
    model = make_model()
    assert model.ask("You help.", [Part("Question", "what?", True), Part("Empty", "")], "Ask") == Answer(
        "the answer"
    )
    messages = fake_ollama.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": "You help." + Prompts.NO_TOOL_CALLS}
    assert messages[1] == {"role": "user", "content": "## Question\nwhat?"}
    assert fake_ollama.calls[0]["think"] == "high" and fake_ollama.calls[0]["options"]["num_ctx"] == 32768
    log = [entry for _, entry in model_log.store.entries("calls")]
    assert log[0]["reply"] == "the answer" and log[0]["label"].startswith("Ask [think high")


@pytest.mark.spec("MOD-24")
def test_a_model_logs_its_details_and_says_only_when_it_asks_again(model_log, fake_ollama, capsys):
    fake_ollama.reply("not json").reply('{"ok": 1}')
    model = Model(MODEL, MEASURED, 0, model_log)
    answer = model.ask_until_usable("s", [Part("Q", "q", True)], "Plan", lambda reply: reply.json()["ok"])
    assert answer.value == 1
    assert capsys.readouterr().out == "🔁 Working on it: no usable result yet, trying again\n"
    notes = [entry["note"] for _, entry in model_log.store.entries("calls") if "note" in entry]
    assert any(note.startswith("Plan (attempt 1)") for note in notes) and any(
        "not usable" in note for note in notes
    )
    stream = Stream("Plan", None)
    stream.thinking = 3
    assert stream.status(stream.started + 12) == "Working on it… 12s"
    assert stream.status(stream.started + 101) == "Working on it… 1m 41s (101s)"


@pytest.mark.spec("MOD-17")
def test_an_answer_gives_its_value_when_it_is_usable_and_ends_when_it_is_not():
    assert Answer("ok").needed("No plan") == "ok"
    with pytest.raises(SystemExit, match=r"^No plan \(it ran out\)$"):
        Answer("", why="it ran out").needed("No plan")


def cut(thinking="I was thinking about it"):
    return {"content": "", "thinking": thinking, "done_reason": "length", "eval_count": 4096}


@pytest.mark.spec("MOD-18")
def test_a_reply_cut_while_thinking_gets_more_room(make_model, fake_ollama):
    fake_ollama.reply(cut()).reply("done")
    assert make_model().ask("s", [Part("Q", "q", True)], "Ask", budget=2048) == Answer("done")
    assert [call["options"]["num_predict"] for call in fake_ollama.calls] == [2048, 4096]


@pytest.mark.spec("MOD-18")
def test_then_it_is_asked_without_thinking_to_conclude(make_model, fake_ollama):
    fake_ollama.reply(cut("the plan is to add")).reply("concluded")
    assert make_model().ask("s", [Part("Q", "q", True)], "Ask") == Answer("concluded")
    second = fake_ollama.calls[1]
    assert second["think"] is False and "the plan is to add" in second["messages"][1]["content"]


@pytest.mark.spec("MOD-18")
def test_without_unthinking_retry_it_gives_up(make_model, fake_ollama):
    fake_ollama.reply(cut("half a thought"))
    model = make_model()
    assert model.ask("s", [Part("Q", "q", True)], "Ask", unthinking_retry=False) == Answer(
        "", why="the reply ran out of room while still thinking", thought="half a thought"
    )


@pytest.mark.spec("MOD-18")
def test_a_cut_reply_whose_blocks_have_ended_is_kept(make_model, fake_ollama):
    answer = "<<<FILE a.py>>>\nx = 1\n<<<END>>>\nI"
    fake_ollama.reply({"content": answer, "thinking": "t", "done_reason": "length", "eval_count": 4096})
    assert make_model().ask("s", [Part("Q", "q", True)], "Ask", done_when=Reply.files_complete) == Answer(
        answer
    )
    assert len(fake_ollama.calls) == 1


@pytest.mark.spec("MOD-19")
def test_out_of_memory_shrinks_the_prompt_then_gives_up(make_model, fake_ollama, capsys):
    fake_ollama.reply(OllamaError("oom", oom=True)).reply("ok")
    assert make_model().ask("s", [Part("Q", "q", True)], "Ask") == Answer("ok")
    assert "Out of GPU memory; trying again with a shorter prompt" in capsys.readouterr().out
    fake_ollama.reply(OllamaError("oom", oom=True), times=3)
    with pytest.raises(OllamaError, match="oom"):
        make_model().ask("s", [Part("Q", "q", True)], "Ask")


@pytest.mark.spec("MOD-19")
def test_a_stall_reloads_the_model(make_model, fake_ollama):
    fake_ollama.reply(OllamaError("stuck", stalled=True), times=2).reply("ok")
    assert make_model().ask("s", [Part("Q", "q", True)], "Ask") == Answer("ok")
    assert fake_ollama.unloaded == [MODEL, MODEL]
    fake_ollama.reply(OllamaError("stuck", stalled=True), times=3)
    with pytest.raises(OllamaError):
        make_model().ask("s", [Part("Q", "q", True)], "Ask")


@pytest.mark.spec("MOD-19")
def test_a_rejected_think_level_falls_back(make_model, fake_ollama):
    fake_ollama.reply(OllamaError('invalid think value "high"')).reply(
        OllamaError("model does not support think")
    )
    fake_ollama.reply("ok")
    model = make_model()
    assert model.ask("s", [Part("Q", "q", True)], "Ask") == Answer("ok")
    assert [call["think"] for call in fake_ollama.calls] == ["high", True, None]
    assert model.think is None and model.think_rejected


@pytest.mark.spec("MOD-19")
def test_a_reply_error_is_asked_once_more_then_the_partial_is_used(make_model, fake_ollama, capsys):
    error = OllamaError("parse error", in_reply=True, partial="<<<FILE a.py>>>\nx")
    fake_ollama.reply(error, times=2)
    assert make_model().ask("s", [Part("Q", "q", True)], "Ask") == Answer("<<<FILE a.py>>>\nx")
    assert len(fake_ollama.calls) == 2


@pytest.mark.spec("MOD-19")
def test_crashes_lower_a_limit_for_the_run_then_stop(make_model, fake_ollama, no_sleep, capsys):
    model = make_model(max_prompt_tokens=700)
    fake_ollama.reply(OllamaError("runner process has terminated", crashed=True), times=2).reply("ok")
    assert model.ask("s", [Part("Q", "q" * 1000, True)], "Ask") == Answer("ok")
    assert model.max_tokens == 630 and model.crashes == 2 and model.calibrated[0] == 700
    assert no_sleep.count(model_module.CRASH_RECOVERY_SECONDS) == 2
    fake_ollama.reply(OllamaError("runner failed", crashed=True), times=4)
    with pytest.raises(OllamaError, match="crashed 4 times on this call"):
        make_model().ask("s", [Part("Q", "q", True)], "Ask")


@pytest.mark.spec("MOD-19")
def test_a_crash_while_writing_lowers_the_longest_reply(make_model, fake_ollama, no_sleep):
    model = make_model()
    crash = OllamaError("runner failed", crashed=True, streamed=12000)
    fake_ollama.reply(crash, times=2).reply("ok")
    assert model.ask("s", [Part("Q", "q", True)], "Ask") == Answer("ok")
    assert model.max_reply == int(16384 * 0.9) and model.max_tokens == 8192


@pytest.mark.spec("MOD-19")
def test_any_other_error_is_raised(make_model, fake_ollama):
    fake_ollama.reply(OllamaError("model not found"))
    with pytest.raises(OllamaError, match="model not found"):
        make_model().ask("s", [Part("Q", "q", True)], "Ask")


@pytest.mark.spec("MOD-20")
def test_a_prompt_too_large_is_relaxed_then_refused(make_model, fake_ollama, model_log):
    fake_ollama.reply("ok")
    model = make_model(max_prompt_tokens=512)
    exact = Part("File", "c" * 3000, True, exact=True)
    assert model.ask("s", [exact], "Ask") == Answer("ok")
    assert any(
        "shortening every part" in entry.get("note", "") for _, entry in model_log.store.entries("calls")
    )
    assert "[...]" in fake_ollama.calls[0]["messages"][1]["content"]
    with pytest.raises(PromptTooLarge):
        model.ask("s", [Part(f"P{number}", "p" * 1000, True) for number in range(10)], "Ask")


@pytest.mark.spec("MOD-21")
def test_ask_until_usable(make_model, fake_ollama, monkeypatch):
    fake_ollama.reply("not json").reply('{"ok": true}')
    answer = make_model().ask_until_usable(
        "s", [Part("Q", "q", True)], "Plan", lambda reply: reply.json()["ok"]
    )
    assert answer == Answer(True)
    assert (
        "Your previous reply was not usable (no JSON object in reply)"
        in fake_ollama.calls[1]["messages"][1]["content"]
    )
    assert [call["label"].split(" [")[0] for call in fake_ollama.calls] == [
        "Plan (attempt 1)",
        "Plan (attempt 2)",
    ]
    fake_ollama.reply("no", times=2)
    answer = make_model().ask_until_usable(
        "s", [Part("Q", "q", True)], "Plan", lambda reply: reply.json(), attempts=2
    )
    assert (
        answer.value is None and answer.why == "no usable reply after 2 attempt(s): no JSON object in reply"
    )
    model = make_model()

    def too_large(*args, **kwargs):
        raise PromptTooLarge("too big")

    monkeypatch.setattr(model, "ask", too_large)
    answer = model.ask_until_usable("s", [], "Plan", lambda reply: reply)
    assert answer.value is None and "even shortened, the prompt does not fit" in answer.why
    assert "(too big)" in answer.why and "tinker calibrate" in answer.why


@pytest.mark.spec("MOD-22")
def test_learn_chars_per_token(make_model):
    model = make_model()
    too_few_tokens = 40
    model.learn_chars_per_token(3000, too_few_tokens)
    assert model.recent_chars_per_token == [] and model.learned_chars_per_token() is None
    for chars in (3100, 2900, 3300, 3500, 3200):
        model.learn_chars_per_token(chars, 1000)
    assert model.chars_per_token == 2.9 and model.learned_chars_per_token() == 2.9
    model.learn_chars_per_token(1000, 1000)
    assert model.chars_per_token == 2.0
    for _ in range(25):
        model.learn_chars_per_token(3000, 1000)
    assert len(model.recent_chars_per_token) == 20 and model.chars_per_token == 3.0


@pytest.mark.spec("MOD-23")
def test_other_models_are_unloaded_and_a_grown_model_too(make_model, fake_ollama, monkeypatch):
    monkeypatch.setattr(Ollama, "loaded_models", staticmethod(lambda: [("other", 1), (MODEL, 1)]))
    fake_ollama.reply("ok")
    make_model().ask("s", [Part("Q", "q", True)], "Ask")
    assert fake_ollama.unloaded == ["other"]
    sizes = iter([10e9, 11e9, 12e9])
    monkeypatch.setattr(Ollama, "loaded_models", staticmethod(lambda: [(MODEL, next(sizes))]))
    model = Model(MODEL, MEASURED, 300, make_model().log, exclusive=False, max_growth=1.15)
    for _ in range(3):
        model.check_memory()
    assert fake_ollama.unloaded == ["other", MODEL]


@pytest.mark.spec("MOD-14")
def test_a_failed_probe_is_recorded_and_waited_out(monkeypatch, fake_ollama, no_sleep, capsys):
    def crash(name, prompt, options, timeout=0):
        raise OllamaError("llama runner process has terminated", crashed=True)

    monkeypatch.setattr(Ollama, "read_raw", staticmethod(crash))
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": "m"}]))
    calibrator = Calibrator("m")
    assert calibrator.probe(4096, 8192) is None
    assert calibrator.probe_history == [[4096, "crashed"]] and model_module.CRASH_RECOVERY_SECONDS in no_sleep
    assert (
        fake_ollama.unloaded == ["m"]
        and "Could not read it (llama runner process has terminated)" in capsys.readouterr().out
    )
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: None))
    with pytest.raises(SystemExit, match="Ollama stopped answering during calibration"):
        calibrator.probe(4096, 8192)


@pytest.mark.spec("MOD-14")
def test_a_gguf_model_that_crashes_is_read_in_pieces(monkeypatch, no_sleep, fake_ollama):
    reads = []

    def read(name, prompt, options, timeout=0):
        reads.append(dict(options))
        if "num_batch" not in options:
            raise OllamaError("runner failed", crashed=True)
        return {"prompt_eval_count": len(prompt), "prompt_eval_duration": 1e9}

    monkeypatch.setattr(Ollama, "read_raw", staticmethod(read))
    monkeypatch.setattr(Ollama, "installed_models", staticmethod(lambda: [{"name": "m"}]))
    calibrator = Calibrator("m")
    assert calibrator.reads(16384, gguf=True) is True and calibrator.options == {"num_batch": 256}
    assert reads[0]["num_ctx"] == 16384 and reads[1]["num_batch"] == 256


@pytest.mark.spec("MOD-15")
def test_the_first_chat_finds_the_think_setting(fake_ollama):
    fake_ollama.reply(OllamaError('invalid think value "high"')).reply(
        {"content": "OK", "prompt_eval_count": 300}
    )
    assert Calibrator("m").first_chat({"capabilities": ["thinking"]}) is True
    assert [call["think"] for call in fake_ollama.calls] == ["high", True]
    fake_ollama.reply("OK")
    assert Calibrator("m").first_chat({"capabilities": ["completion"]}) is None
    assert fake_ollama.calls[-1]["think"] is None
    fake_ollama.reply(OllamaError("model requires more system memory"))
    with pytest.raises(SystemExit, match="m does not run"):
        Calibrator("m").first_chat({"capabilities": []})
