import pytest
from conftest import MODEL

from tinkerkit.model import OllamaError
from tinkerkit.prompts import Prompts
from tinkerkit.reviewing import RequestReview
from tinkerkit.settings import Config


def logged(model_log):
    return [
        {key: value for key, value in entry.entry.items() if key != "time"}
        for entry in model_log.store.entries(model_log.name)
    ]


@pytest.mark.spec("RVW-1")
@pytest.mark.parametrize(
    "request_text",
    [
        "There is a bug in tinkerkit/model.py when the reply is empty",
        "Why does the function parse drop the last line?",
        "The test of the parser fails on Windows",
        "Help me refactor the class Store into smaller parts",
        "What design should the `Cache` have for many readers?",
        " ".join(["word"] * 61),
        "Add a flag.\n- read it\n- keep it\n- print it",
        "Why? How? When?",
        "Look at a.py, b.py and tinkerkit/c.py together",
    ],
)
def test_the_word_rule_finds_high_requests_without_a_model_call(request_text, fake_ollama, model_log):
    level, why = RequestReview(model_log).level(request_text)
    assert level == "high" and why and fake_ollama.calls == []


@pytest.mark.spec("RVW-1")
@pytest.mark.parametrize(
    "request_text",
    [
        "hi",
        "Hello there, thanks!",
        "What is a monad",
        "rename the variable x to count",
        "format this paragraph please",
    ],
)
def test_the_word_rule_finds_short_simple_requests_off(request_text, fake_ollama, model_log):
    level, why = RequestReview(model_log).level(request_text)
    assert level == "off" and why and fake_ollama.calls == []


@pytest.mark.spec("RVW-1")
def test_a_simple_word_inside_a_request_does_not_make_it_off(
    measured_model, fake_ollama, tinker_home, model_log
):
    fake_ollama.reply("low: a short story")
    assert RequestReview(model_log).level("Write a story about a dragon who learns how to bake")[0] == "low"


@pytest.mark.spec("RVW-1")
def test_a_high_rule_wins_over_an_off_rule(fake_ollama, model_log):
    assert RequestReview(model_log).level("rename it? and why? and how?")[0] == "high"
    assert RequestReview(model_log).level("hi, the function parse has a bug")[0] == "high"


@pytest.mark.spec("RVW-1")
def test_other_requests_ask_the_reviewer_at_think_high(measured_model, fake_ollama, tinker_home, model_log):
    fake_ollama.show(MODEL, {"thinking": {"values": [False, "low", "medium", "high"]}})
    fake_ollama.reply("Level: low. A small, plain change.\nmore")
    request_text = "Write a short poem about the sea in the evening for my sister's birthday card"
    assert RequestReview(model_log).level(request_text) == ("low", "Level: low. A small, plain change.")
    call = fake_ollama.calls[-1]
    assert call["think"] == "high"
    assert call["messages"] == [
        {"role": "system", "content": Prompts.REVIEW_PROMPT},
        {"role": "user", "content": request_text},
    ]
    assert logged(model_log)[-1] == {"review": "low", "why": "Level: low. A small, plain change."}


@pytest.mark.spec("RVW-1")
def test_a_reply_naming_no_level_is_medium(measured_model, fake_ollama, tinker_home, model_log):
    fake_ollama.reply("I am not sure.")
    level, why = RequestReview(model_log).level("Write a story about a dragon who learns to bake bread")
    assert level == "medium" and why
    assert logged(model_log) == [{"review": "medium", "why": why}]


@pytest.mark.spec("RVW-1")
def test_a_failed_reviewer_call_is_medium(measured_model, fake_ollama, tinker_home, model_log):
    fake_ollama.reply(OllamaError("down"))
    assert (
        RequestReview(model_log).level("Write a story about a dragon who learns to bake bread")[0] == "medium"
    )
    Config.save(**{"model": None})
    assert (
        RequestReview(model_log).level("Write a story about a dragon who learns to bake bread")[0] == "medium"
    )
    assert len(fake_ollama.calls) == 1


@pytest.mark.spec("RVW-1")
def test_the_rule_level_goes_to_the_call_log(fake_ollama, model_log):
    level, why = RequestReview(model_log).level("hi")
    assert logged(model_log) == [{"review": level, "why": why}]
