import random

import pytest

from tinkerkit import quality

pytestmark = pytest.mark.spec("MOD-27")


def test_a_hundred_tasks_with_unique_ids_a_prompt_and_an_answer_that_solves_it():
    tasks = quality.tasks()
    assert len({task["id"] for task in tasks}) == len(tasks) == 100
    for task in tasks:
        assert task["prompt"].strip() and quality.solves(task["answer"], task["answer"])


def test_a_seeded_pick_repeats_and_draws_distinct_tasks():
    first = quality.pick(3, random.Random(7))
    second = quality.pick(3, random.Random(7))
    assert first == second
    assert len({task["id"] for task in first}) == 3


@pytest.mark.parametrize(
    "reply,answer",
    [
        *[
            (reply, "42")
            for reply in ["42", " 42\n", "42.", "'42'", '"42"', "`42`", "```\n42\n```", "```text\n42\n```"]
        ],
        ("TRUE!", "True"),
    ],
)
def test_a_reply_solves_despite_case_spacing_punctuation_quotes_and_backticks(reply, answer):
    assert quality.solves(reply, answer)


@pytest.mark.parametrize("reply", ["41", "", None, "   "])
def test_a_wrong_or_empty_reply_fails(reply):
    assert not quality.solves(reply, "42")
