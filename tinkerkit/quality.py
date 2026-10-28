import json
from pathlib import Path

TASKS = Path(__file__).parent / "quality_tasks.json"
WRAPPING = " \t\n\"'`"
TRAILING = ".,;:!?"


def tasks():
    return json.loads(TASKS.read_text())


def pick(count, rng):
    return rng.sample(tasks(), count)


def solves(reply, answer):
    return bool(reply) and bare(reply) == bare(answer)


def bare(text):
    text = unfenced(str(text).strip()).casefold()
    while (stripped := text.strip(WRAPPING).rstrip(TRAILING)) != text:
        text = stripped
    return text


def unfenced(text):
    if text.startswith("```") and text.endswith("```") and "\n" in text:
        return text[text.index("\n") + 1 : -3]
    return text
