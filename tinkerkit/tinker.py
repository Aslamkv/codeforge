from .memory.dream import Mind
from .practice import Practice
from .shelf import Shelf
from .skills import Skills


class Tinker:
    def __init__(self, yes=False, started_at=None):
        self.subsystems = {
            word: subsystem
            for subsystem in (kind(yes, started_at) for kind in (Shelf, Mind, Skills, Practice))
            for word in subsystem.COMMANDS
        }

    def handle(self, words, voice):
        self.subsystems[words[0]].handle(words, voice)
