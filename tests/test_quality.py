import pytest

from tinkerkit import quality

pytestmark = pytest.mark.spec("MOD-27")


def test_eight_tasks_with_unique_ids_a_prompt_and_an_answer_that_solves_it():
    tasks = quality.tasks()
    assert len({task["id"] for task in tasks}) == len(tasks) == 8
    for task in tasks:
        assert task["prompt"].strip() and quality.solves(task["answer"], task["answer"])


def test_four_tiers_of_two_tasks_from_easy_to_hard():
    tiers = quality.tiers()
    assert [[task["tier"] for task in tier] for tier in tiers] == [[1, 1], [2, 2], [3, 3], [4, 4]]
    assert [task for tier in tiers for task in tier] == sorted(quality.tasks(), key=lambda task: task["tier"])


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
