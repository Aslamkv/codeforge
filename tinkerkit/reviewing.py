import re

from .model import Models, OllamaError
from .prompts import Prompts
from .settings import LONG_REQUEST_WORDS, MANY_FILES, MANY_PARTS, REVIEW_REPLY_TOKENS, SHORT_REQUEST_WORDS
from .text import first_line

LEVEL_RE = re.compile(r"\b(off|low|medium|high)\b")
FILE_SUFFIX_RE = re.compile(r"\w\.[A-Za-z]\w{0,4}$")
LIST_LINE_RE = re.compile(r"\s*(?:[-*]|\d+\.)\s")
WORD_EDGES = ".,;:!?()[]{}\"'"


class RequestReview:
    CODE_WORDS = frozenset({"code", "function", "class", "method", "test", "error"})
    HARD_WORDS = frozenset({"bug", "fails", "design", "architecture", "refactor"})
    HARD_PHRASES = ("why does",)
    SIMPLE_WORDS = frozenset(
        {
            "hi",
            "hello",
            "thanks",
            "what",
            "who",
            "when",
            "where",
            "how",
            "is",
            "list",
            "name",
            "define",
            "rename",
            "format",
        }
    )

    def __init__(self, log):
        self.log = log

    def level(self, request):
        level, why = self.ruled(RequestWords(request)) or self.reviewed(request)
        self.log.add({"review": level, "why": why})
        return level, why

    def ruled(self, request):
        if request.about_code(self.CODE_WORDS) and request.says(self.HARD_WORDS, self.HARD_PHRASES):
            return "high", "a request about code that names a hard problem"
        if len(request.words) > LONG_REQUEST_WORDS:
            return "high", f"a long request of more than {LONG_REQUEST_WORDS} words"
        if request.parts() >= MANY_PARTS:
            return "high", f"a request of {MANY_PARTS} or more parts"
        if len(request.paths()) >= MANY_FILES:
            return "high", f"a request naming {MANY_FILES} or more files"
        if len(request.words) <= SHORT_REQUEST_WORDS and request.opens_with(self.SIMPLE_WORDS):
            return "off", "a short greeting, question, list, definition, rename or format"
        return None

    def reviewed(self, request):
        if not Models.quick():
            return "medium", "no model to review the request"
        try:
            reply = Models.quick_reply(
                [
                    {"role": "system", "content": Prompts.REVIEW_PROMPT},
                    {"role": "user", "content": request},
                ],
                REVIEW_REPLY_TOKENS,
                "off",
            )
        except OllamaError as error:
            return "medium", f"the review failed: {error}"
        named = LEVEL_RE.search(reply.lower())
        if not named:
            return "medium", "the review named no level"
        return named.group(1), first_line(reply)


class RequestWords:
    def __init__(self, text):
        self.text = text
        self.words = [word.strip(WORD_EDGES) for word in text.split()]

    def lowered(self):
        return {word.lower() for word in self.words}

    def says(self, words, phrases=()):
        return bool(self.lowered() & words) or any(phrase in self.text.lower() for phrase in phrases)

    def opens_with(self, words):
        return bool(self.words) and self.words[0].lower() in words

    def about_code(self, code_words):
        return bool(self.paths()) or "`" in self.text or bool(self.lowered() & code_words)

    def paths(self):
        return [word for word in self.words if "/" in word or FILE_SUFFIX_RE.search(word)]

    def parts(self):
        list_lines = sum(1 for line in self.text.splitlines() if LIST_LINE_RE.match(line))
        return self.text.count("?") + list_lines
