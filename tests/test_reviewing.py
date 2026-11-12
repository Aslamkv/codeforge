import pytest
from conftest import LOADING_PHASE, MODEL

from tinkerkit.model import LoadedModel, Ollama, OllamaError
from tinkerkit.prompts import Prompts
from tinkerkit.reviewing import RequestReview
from tinkerkit.settings import Config

STORY = "Write a story about a dragon who learns to bake bread"
REVIEW_PHASE = "🧭 Working out how much thought your request needs"


def logged(model_log):
    return [
        {key: value for key, value in entry.entry.items() if key != "time"}
        for entry in model_log.store.entries(model_log.name)
    ]


@pytest.mark.spec("RVW-1")
@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("There is a bug in tinkerkit/model.py when the reply is empty", "high"),
        ("Why does the function parse drop the last line?", "high"),
        ("The test of the parser fails on Windows", "high"),
        ("Help me refactor the class Store into smaller parts", "high"),
        ("What design should the `Cache` have for many readers?", "high"),
        (" ".join(["word"] * 61), "high"),
        ("Add a flag.\n- read it\n- keep it\n- print it", "high"),
        ("Why? How? When?", "high"),
        ("Look at a.py, b.py and tinkerkit/c.py together", "high"),
        ("hi", "off"),
        ("Hello there, thanks!", "off"),
        ("What is a monad", "off"),
        ("rename the variable x to count", "off"),
        ("format this paragraph please", "off"),
        ("list the software engineering principles", "off"),
        ("Name the SOLID principles", "off"),
        ("define coupling", "off"),
    ],
)
def test_the_word_rule_finds_high_and_short_simple_requests_without_a_model_call(
    request_text, expected, fake_ollama, model_log
):
    level, why = RequestReview(model_log).level(request_text)
    assert level == expected and why and fake_ollama.calls == []


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
def test_other_requests_ask_the_reviewer_without_thinking(
    measured_model, fake_ollama, tinker_home, model_log
):
    fake_ollama.show(MODEL, {"thinking": {"values": [False, "low", "medium", "high"]}})
    fake_ollama.reply("Level: low. A small, plain change.\nmore")
    request_text = "Write a short poem about the sea in the evening for my sister's birthday card"
    assert RequestReview(model_log).level(request_text) == ("low", "Level: low. A small, plain change.")
    call = fake_ollama.calls[-1]
    assert call["think"] is False
    assert call["options"]["temperature"] == 0
    assert call["messages"] == [
        {"role": "system", "content": Prompts.REVIEW_PROMPT},
        {"role": "user", "content": request_text},
    ]
    assert logged(model_log)[-1] == {"review": "low", "why": "Level: low. A small, plain change."}


@pytest.mark.spec("RVW-1")
def test_a_thread_goes_before_the_request_in_the_review_call(
    measured_model, fake_ollama, tinker_home, model_log
):
    fake_ollama.reply("low: a plain follow-up")
    RequestReview(model_log).level(STORY, thread="\n\nEarlier:\n- a question → an answer\n")
    assert fake_ollama.calls[-1]["messages"][1] == {
        "role": "user",
        "content": f"Earlier:\n- a question → an answer\n\n{STORY}",
    }


@pytest.mark.spec("RVW-1")
def test_a_thread_does_not_change_the_word_rule(fake_ollama, model_log):
    thread = "\n\nEarlier:\n- " + "fix the parse function bug " * 40
    assert RequestReview(model_log).level("hi there", thread=thread)[0] == "off"
    assert fake_ollama.calls == []


@pytest.mark.spec("RVW-1")
def test_a_reply_naming_no_level_is_medium(measured_model, fake_ollama, tinker_home, model_log):
    fake_ollama.reply("I am not sure.")
    level, why = RequestReview(model_log).level(STORY)
    assert level == "medium" and why
    assert logged(model_log) == [{"review": "medium", "why": why}]


@pytest.mark.spec("RVW-1")
def test_a_failed_reviewer_call_is_medium(measured_model, fake_ollama, tinker_home, model_log):
    fake_ollama.reply(OllamaError("down"))
    assert RequestReview(model_log).level(STORY)[0] == "medium"
    Config.save(**{"model": None})
    assert RequestReview(model_log).level(STORY)[0] == "medium"
    assert len(fake_ollama.calls) == 1


@pytest.mark.spec("RVW-1")
def test_the_rule_level_goes_to_the_call_log(fake_ollama, model_log):
    level, why = RequestReview(model_log).level("hi")
    assert logged(model_log) == [{"review": level, "why": why}]


@pytest.mark.spec("RVW-3")
def test_a_level_below_the_floor_is_raised_and_the_log_says_so(fake_ollama, model_log):
    level, why = RequestReview(model_log).level("hi", least="medium")
    assert level == "medium" and "raised" in why
    assert logged(model_log) == [{"review": level, "why": why}]


@pytest.mark.spec("RVW-3")
def test_a_level_at_or_above_the_floor_stays(fake_ollama, model_log):
    request_text = "There is a bug in tinkerkit/model.py when the reply is empty"
    assert RequestReview(model_log).level(request_text, least="medium") == RequestReview(model_log).level(
        request_text
    )


@pytest.mark.spec("PRJ-18")
@pytest.mark.parametrize(
    "loaded, shown",
    [([], [LOADING_PHASE, REVIEW_PHASE]), ([LoadedModel(MODEL, 1)], [REVIEW_PHASE])],
)
def test_the_review_phase_shows_while_the_model_reviews_after_loading_it_when_not_in_memory(
    loaded, shown, measured_model, fake_ollama, tinker_home, model_log, monkeypatch
):
    phases = []
    monkeypatch.setattr(Ollama, "loaded_models", staticmethod(lambda: loaded))
    fake_ollama.reply("low: a short story")
    RequestReview(model_log, phases.append).level(STORY)
    assert phases == shown


@pytest.mark.spec("PRJ-18")
def test_the_review_phase_never_shows_when_a_rule_decides(fake_ollama, model_log):
    phases = []
    RequestReview(model_log, phases.append).level("hi")
    assert phases == []


@pytest.mark.spec("PRJ-18")
def test_the_review_phase_never_shows_without_a_model(measured_model, fake_ollama, tinker_home, model_log):
    phases = []
    Config.save(**{"model": None})
    RequestReview(model_log, phases.append).level(STORY)
    assert phases == [] and fake_ollama.calls == []
