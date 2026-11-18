import os
import re
import sys
lazy import html

lazy from ..messages import Messages, colourful, paint


def styled(text, style):
    return paint(text, style, sys.stdout)


def marked(text):
    return indented(text, styled("● ", "accent"))


def indented(text, first="  "):
    return first + text.replace("\n", "\n  ") if colourful(sys.stdout) else text


def split_at_tinkering(lines):
    at = next((row for row, line in enumerate(lines) if is_tinkering(line)), None)
    return (lines[:at], lines[at], lines[at + 1 :]) if at is not None else ([], lines[0], [])


def is_tinkering(line):
    star, _, rest = Markup.plain(line).lstrip().partition(" ")
    return star in Messages.STAR and rest.startswith("Tinkering…")


class Message:
    def __init__(self, chat, text, sender="", id=None):
        self.chat, self.text, self.sender, self.id = str(chat), (text or "").strip(), sender, id


def pieces(text, size=3500):
    parts, part = [], ""
    for whole in text.splitlines():
        for line in [whole[start : start + size] for start in range(0, len(whole), size)] or [""]:
            if part and len(part) + len(line) + 1 > size:
                parts.append(part)
                part = ""
            part = f"{part}\n{line}" if part else line
    return parts + [part] if part else parts


class Markup:
    FENCE_RE = re.compile(r"```(\w*)\n?(.*?)(?:\n?```|\Z)", re.S)
    CODE_RE = re.compile(r"`([^`\n]+)`")
    LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
    ESCAPED_RE = re.compile(r"\\([*_`~\\])")
    MARKS = (
        (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "b"),
        (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), "s"),
        (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"), "i"),
        (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "i"),
    )
    ITEM_RE = re.compile(r"(?m)^\s*[-*] (?=\S)")
    HEADING_RE = re.compile(r"(?m)^#{1,6} +(.+)$")
    ANSI_ON_OFF = {"b": ("1", "22"), "i": ("3", "23"), "s": ("9", "29")}
    TERMINAL_OUTPUT_LINES = 30

    @classmethod
    def of(cls, template):
        return "".join(part if isinstance(part, str) else cls.formatted(part) for part in template)

    @classmethod
    def formatted(cls, part):
        match part.format_spec:
            case "":
                return cls.literal(part.value)
            case "md":
                return str(part.value)
            case "code":
                return f"`{part.value}`"
            case "block":
                return f"```\n{cls.fenced(part.value)}\n```"
            case spec:
                raise ValueError(f"no Markup format {spec!r}")

    @staticmethod
    def literal(text):
        return re.sub(r"([*_`~\\])", r"\\\1", str(text))

    @staticmethod
    def fenced(text):
        return str(text).replace("```", "ˋˋˋ")

    @staticmethod
    def columns(text):
        lines = str(text).expandtabs().splitlines()
        rows = [line for line in lines if Markup.is_row(line)]
        cuts = Markup.column_cuts(rows) if len(rows) >= 2 else []
        if len(cuts) < 2:
            return str(text)
        return "\n".join(Markup.in_columns(line, cuts) if Markup.is_row(line) else line for line in lines)

    @staticmethod
    def is_row(line):
        return len(line.split()) >= 3 and "  " in line.strip()

    @staticmethod
    def column_cuts(rows):
        shape = min(rows, key=lambda line: len(line.split()))
        last = list(re.finditer(r"\S+", shape))[-1].start()
        tolerated = 1 if len(rows) >= 4 else 0
        blank = [sum(at < len(line) and line[at] != " " for line in rows) <= tolerated for at in range(last)]
        gaps = [at for at in range(1, last - 1) if blank[at] and blank[at + 1] and not blank[at - 1]] + [last]
        cuts, begin = [], 0
        for index, gap in enumerate(gaps):
            after = shape[gap : gaps[index + 1]] if index + 1 < len(gaps) else shape[gap:]
            if shape[begin:gap].strip() and after.strip():
                cuts.append(gap)
                begin = gap
        return cuts

    @staticmethod
    def in_columns(line, cuts):
        parts, begin = [], 0
        for cut in cuts:
            if cut >= len(line):
                break
            if line[cut] != " " and line[cut - 1] != " ":
                cut = line.find(" ", cut)
                if cut < 0:
                    break
            if cut > begin:
                parts.append(line[begin:cut].strip())
                begin = cut
        parts.append(line[begin:].strip())
        return " | ".join(part for part in parts if part)

    @classmethod
    def html(cls, text):
        keep = cls.keeper()

        def block(found):
            output = found.group(1) == "output"
            body = found.group(2).rstrip("\n")
            body = html.escape(cls.columns(body) if output else body, quote=False)
            return keep(f"<blockquote expandable>{body}</blockquote>" if output else f"<pre>{body}</pre>")

        return cls.render(
            text,
            block,
            lambda code: f"<code>{html.escape(code, quote=False)}</code>",
            lambda plain: html.escape(plain, quote=False),
            lambda tag, inner: f"<{tag}>{inner}</{tag}>",
            keep,
            lambda words, url: f'<a href="{html.escape(url)}">{html.escape(words, quote=False)}</a>',
        )

    @classmethod
    def ansi(cls, text):
        if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
            return cls.plain(text)
        keep = cls.keeper()

        def block(found):
            body = found.group(2).rstrip("\n").splitlines()
            if found.group(1) == "output":
                more = len(body) - cls.TERMINAL_OUTPUT_LINES
                body = body[: cls.TERMINAL_OUTPUT_LINES] + ([f"… {more} more lines"] if more > 0 else [])
                return keep("\n".join(f"\033[2m  │ {line}\033[22m" for line in body))
            return keep("\n".join(f"    \033[36m{line}\033[39m" for line in body))

        return cls.render(
            text,
            block,
            lambda code: f"\033[36m{code}\033[39m",
            lambda plain: plain,
            lambda tag, inner: f"\033[{cls.ANSI_ON_OFF[tag][0]}m{inner}\033[{cls.ANSI_ON_OFF[tag][1]}m",
            keep,
            lambda words, url: f"\033[4m{words}\033[24m \033[2m({url})\033[22m",
        )

    @classmethod
    def plain(cls, text):
        keep = cls.keeper()
        return cls.render(
            text,
            lambda found: keep(found.group(2).rstrip("\n")),
            lambda code: code,
            lambda plain: plain,
            lambda tag, inner: inner,
            keep,
            lambda words, url: f"{words} ({url})",
        )

    @classmethod
    def render(cls, text, block, code, plain, mark, keep, link):
        text = cls.FENCE_RE.sub(block, str(text))
        text = cls.LINK_RE.sub(lambda found: keep(link(found.group(1), found.group(2))), text)
        text = cls.ESCAPED_RE.sub(lambda found: keep(plain(found.group(1))), text)
        text = cls.CODE_RE.sub(lambda found: keep(code(found.group(1))), text)
        text = cls.ITEM_RE.sub("• ", plain(text))
        text = cls.HEADING_RE.sub(lambda found: mark("b", found.group(1)), text)
        for pattern, tag in cls.MARKS:
            text = pattern.sub(lambda found, tag=tag: mark(tag, found.group(1)), text)
        return re.sub("\x00(\\d+)\x00", lambda found: keep.kept[int(found.group(1))], text)

    @staticmethod
    def keeper():
        kept = []

        def keep(shown):
            kept.append(shown)
            return f"\x00{len(kept) - 1}\x00"

        keep.kept = kept
        return keep


md = Markup.of
