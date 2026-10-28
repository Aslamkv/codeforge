import random

import pytest

from tinkerkit import quality

pytestmark = pytest.mark.spec("MOD-27")


def test_there_are_a_hundred_tasks_with_unique_ids():
    ids = [task["id"] for task in quality.tasks()]
    assert len(ids) == 100
    assert len(set(ids)) == 100


def test_every_task_has_a_prompt_and_an_answer():
    for task in quality.tasks():
        assert task["prompt"].strip()
        assert str(task["answer"]).strip()


def test_every_answer_solves_its_own_task():
    for task in quality.tasks():
        assert quality.solves(task["answer"], task["answer"])


def test_a_seeded_pick_repeats_and_draws_distinct_tasks():
    first = quality.pick(3, random.Random(7))
    second = quality.pick(3, random.Random(7))
    assert first == second
    assert len({task["id"] for task in first}) == 3


@pytest.mark.parametrize(
    "reply",
    ["42", " 42\n", "42.", "'42'", '"42"', "`42`", "```\n42\n```", "```text\n42\n```"],
)
def test_a_reply_solves_despite_spacing_punctuation_quotes_and_backticks(reply):
    assert quality.solves(reply, "42")


def test_a_reply_solves_regardless_of_case():
    assert quality.solves("TRUE!", "True")


@pytest.mark.parametrize("reply", ["41", "", None, "   "])
def test_a_wrong_or_empty_reply_fails(reply):
    assert not quality.solves(reply, "42")
