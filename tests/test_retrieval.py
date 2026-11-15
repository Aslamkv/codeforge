import pytest

from tinkerkit.retrieval import Likeness

SEED = "Seed random generators inside tests"
SEED_AGAIN = "Always seed random generators before tests run"
CLOCK = "Mock the clock in tests that read the time"
EXIT = "Exit with code 1 after an error"


class Meanings:
    name, failed = "embed-model", ""

    def __init__(self, table):
        self.table, self.asked = table, []

    def vectors(self, texts, keep=True, prune=False):
        self.asked.append(keep)
        if self.table is None:
            self.failed = "embed-model gave no embeddings"
            return None
        return [self.table.get(text, [0.0, 1.0]) for text in texts]


def pairs_alike(likeness, texts):
    closeness = likeness.among(texts, 0.6, 0.9)
    return {
        (first, second)
        for first in range(len(texts))
        for second in range(first + 1, len(texts))
        if closeness(first, second)
    }


@pytest.mark.spec("KNW-11")
def test_texts_are_alike_by_words_or_by_meaning():
    meanings = Meanings({SEED: [1.0, 0.0], CLOCK: [0.95, 0.05], EXIT: [0.0, -1.0]})
    likeness = Likeness(meanings)

    assert pairs_alike(likeness, [SEED, SEED_AGAIN, CLOCK, EXIT]) == {(0, 1), (0, 2)}
    assert likeness.used == "words and meaning (embed-model)"
    assert meanings.asked == [False]


@pytest.mark.spec("KNW-11")
def test_closeness_is_the_highest_measure_reached():
    closeness = Likeness(Meanings({"a b c": [1.0, 0.0], "x y z": [1.0, 0.0]})).among(
        ["a b c", "x y z"], 0.6, 0.9
    )

    assert closeness(0, 1) == pytest.approx(1.0)


@pytest.mark.spec("KNW-11")
def test_without_an_embedding_model_words_alone_decide():
    likeness = Likeness(None)

    assert pairs_alike(likeness, [SEED, SEED_AGAIN, CLOCK]) == {(0, 1)}
    assert likeness.used == "words (no embedding model selected (tinker model NAME))"


@pytest.mark.spec("KNW-11")
def test_failing_embeddings_leave_words_alone_with_one_warning(capsys):
    likeness = Likeness(Meanings(None))

    assert pairs_alike(likeness, [SEED, SEED_AGAIN, CLOCK]) == {(0, 1)}
    assert pairs_alike(likeness, [SEED, CLOCK]) == set()
    assert likeness.used == "words (embed-model gave no embeddings)"
    assert capsys.readouterr().out.count("embed-model gave no embeddings") == 1


@pytest.mark.spec("KNW-11")
def test_a_failed_model_is_not_asked_again_in_the_run(capsys):
    first, second = Meanings(None), Meanings(None)

    pairs_alike(Likeness(first), [SEED, CLOCK])
    likeness = Likeness(second)

    assert pairs_alike(likeness, [SEED, SEED_AGAIN, CLOCK]) == {(0, 1)}
    assert first.asked == [False] and second.asked == []
    assert likeness.used == "words (embed-model gave no embeddings)"
    assert capsys.readouterr().out.count("embed-model gave no embeddings") == 1


@pytest.mark.spec("KNW-11")
def test_no_texts_ask_for_no_vectors():
    meanings = Meanings({})
    likeness = Likeness(meanings)

    likeness.among([], 0.6, 0.9)

    assert meanings.asked == [] and likeness.used == "words"


@pytest.mark.spec("KNW-11")
def test_a_text_is_measured_against_each_other_text():
    meanings = Meanings({SEED: [1.0, 0.0], CLOCK: [0.95, 0.05], EXIT: [0.0, -1.0]})

    closes = Likeness(meanings).to(SEED, [SEED_AGAIN, CLOCK, EXIT], 0.6, 0.9)

    assert [bool(close) for close in closes] == [True, True, False]
    assert Likeness(meanings).to(SEED, [], 0.6, 0.9) == [] and meanings.asked == [False]
