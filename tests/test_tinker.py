import pytest

from tinkerkit import tinker
from tinkerkit.tinker import Tinker


class FakeVoice:
    pass


def heard_subsystem(name, commands, seen):
    class Heard:
        COMMANDS = commands

        def __init__(self, yes=False, started_at=None):
            self.yes = yes
            self.started_at = started_at

        def handle(self, words, voice):
            seen.append((name, self.yes, self.started_at, words, voice))

    return Heard


@pytest.fixture
def seen(monkeypatch):
    heard = []
    monkeypatch.setattr(tinker, "Shelf", heard_subsystem("shelf", ("read",), heard))
    monkeypatch.setattr(tinker, "Mind", heard_subsystem("mind", ("memory", "dream"), heard))
    monkeypatch.setattr(tinker, "Skills", heard_subsystem("skills", ("skill",), heard))
    monkeypatch.setattr(tinker, "Practice", heard_subsystem("practice", ("exercise",), heard))
    return heard


@pytest.mark.spec("TNK-2")
def test_each_command_word_goes_to_the_subsystem_that_serves_it(seen):
    voice = FakeVoice()
    for words in (
        ["read", "a.pdf"],
        ["memory"],
        ["dream", "run"],
        ["skill", "list"],
        ["exercise", "add two"],
    ):
        Tinker().handle(words, voice)
    assert [(name, words) for name, _, _, words, _ in seen] == [
        ("shelf", ["read", "a.pdf"]),
        ("mind", ["memory"]),
        ("mind", ["dream", "run"]),
        ("skills", ["skill", "list"]),
        ("practice", ["exercise", "add two"]),
    ]
    assert all(heard_voice is voice for *_, heard_voice in seen)


@pytest.mark.spec("TNK-2")
def test_subsystems_are_built_with_the_persons_yes_and_start(seen):
    Tinker(True, 12.5).handle(["memory", "forget", "a1b2c3"], FakeVoice())
    assert seen[0][:3] == ("mind", True, 12.5)


@pytest.mark.spec("TNK-2")
def test_the_real_subsystems_serve_the_five_command_words():
    assert set(Tinker().subsystems) == {"read", "memory", "dream", "skill", "exercise"}
