import dataclasses
import re
from collections import namedtuple
from dataclasses import dataclass
from functools import cached_property
lazy import html.parser
lazy import io
lazy import posixpath
lazy import urllib.parse
lazy import xml.etree.ElementTree as ET
lazy import zipfile
lazy import zlib
lazy from pathlib import Path

lazy from .settings import READ_MIN_CHAPTERS, READ_WINDOW_CHARS
lazy from .text import first_line, one_line, split_text, words

ABOUT_CHAPTER_TITLES = (
    "foreword",
    "preface",
    "introduction",
    "prologue",
    "about this book",
    "how to read this book",
    "how to use this book",
    "overview",
    "summary",
    "who this book is for",
)
INDEX_TITLES = ("index", "general index", "subject index")
UNREAD_MATTER_TITLES = (
    "cover",
    "title page",
    "copyright",
    "copyright page",
    "dedication",
    "contents",
    "table of contents",
    "acknowledgements",
    "acknowledgments",
    "about the author",
    "about the authors",
    "also by",
    "praise for",
    "colophon",
    "bibliography",
    "references",
    "endnotes",
    "further reading",
    "list of figures",
    "list of tables",
)
TITLE_NUMBERING_RE = re.compile(r"^(?:chapter\s+)?(?:\d+|[ivxlc]+)\b[.:)\-–—]*\s+")
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
CHAPTER_LINE_RE = re.compile(
    r"^[ \t]*(?:(?:chapter|part|book|section|appendix)[ \t]+[\w.-]+|preface|foreword|"
    r"introduction|prologue|epilogue|conclusion|index|contents)\b[^\n]{0,80}$",
    re.I | re.M,
)
HTML_RE = re.compile(r"\s*(<!doctype html|<html)", re.I)
PDF_OBJECT_RE = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", re.S)
PDF_STREAM_RE = re.compile(rb"stream\r?\n")
PDF_LENGTH_RE = re.compile(rb"/Length\s+(\d+)\b(?!\s+\d+\s+R)")
PDF_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
PDF_CONTENTS_RE = re.compile(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)")
PDF_KIDS_RE = re.compile(rb"/Kids\s*\[([^\]]*)\]")
PDF_INFO_RE = re.compile(rb"/Info\s+(\d+)\s+\d+\s+R")
PDF_WORD_RE = re.compile(rb"/?[^\s()<>\[\]{}/%]*")
PDF_NUMBER_RE = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
PDF_OCTAL_RE = re.compile(rb"[0-7]{1,3}")
PDF_INLINE_IMAGE_END_RE = re.compile(rb"\sEI(?=\s|$)")
PDF_RESOURCES_REF_RE = re.compile(rb"/Resources\s+(\d+\s+\d+\s+R)")
PDF_XOBJECT_RE = re.compile(rb"/XObject\s*(<<.*?>>|\d+\s+\d+\s+R)", re.S)
PDF_JPEG_RE = re.compile(rb"/Filter\s*(?:\[\s*)?/DCTDecode\b")
IMAGE_TYPES = {
    re.compile(rb"\xff\xd8\xff"): "image/jpeg",
    re.compile(rb"\x89PNG\r\n\x1a\n"): "image/png",
    re.compile(rb"GIF8[79]a"): "image/gif",
    re.compile(rb"RIFF.{4}WEBP", re.S): "image/webp",
}
AUDIO_TYPES = {
    re.compile(rb"RIFF.{4}WAVE", re.S): ".wav",
    re.compile(rb"ID3|\xff[\xe0-\xff]"): ".mp3",
    re.compile(rb"OggS"): ".ogg",
    re.compile(rb"fLaC"): ".flac",
    re.compile(rb".{4}ftyp", re.S): ".m4a",
    re.compile(rb"\x1a\x45\xdf\xa3"): ".webm",
    re.compile(rb"FORM.{4}AIF[FC]", re.S): ".aiff",
}
PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
PDF_WORD_GAP = -200
PAGE_BREAK = "\n\f\n"
WEB_PAGE = (
    "That gave a web page, not something to read. A share link (Google Drive, Dropbox, iCloud) needs its "
    "download link: Dropbox's ends in ?dl=1."
)


Located = namedtuple("Located", "path fragment")
NcxEntry = namedtuple("NcxEntry", "depth title src")
SpineText = namedtuple("SpineText", "text starts")
ContentsEntry = namedtuple("ContentsEntry", "depth title where")
ParsedText = namedtuple("ParsedText", "text ids")
PdfObject = namedtuple("PdfObject", "head stream")


@dataclass
class Chapter:
    number: int
    title: str
    text: str
    kind: str
    state: str = "unread"
    principles: list = dataclasses.field(default_factory=list)

    def size(self):
        return len(words(self.text))


@dataclass
class Book:
    title: str
    author: str
    chapters: list

    @classmethod
    def of(cls, entry):
        return cls(entry["title"], entry["author"], [Chapter(**chapter) for chapter in entry["chapters"]])

    def of_kind(self, *kinds):
        return [chapter for chapter in self.chapters if chapter.kind in kinds]

    def in_state(self, *states):
        return [chapter for chapter in self.chapters if chapter.state in states]


def opened(data, name):
    if data.startswith(b"%PDF"):
        return Pdf(name, data)
    if data.startswith(b"PK\x03\x04"):
        return zipped(data, name)
    if mime := sniffed(data, IMAGE_TYPES):
        return Image(name, data, mime)
    if suffix := sniffed(data, AUDIO_TYPES):
        return Audio(name, data, suffix)
    if b"\x00" in data[:4096]:
        return Unsupported(name)
    text = data.decode("utf-8-sig", errors="replace")
    if HTML_RE.match(text):
        raise SystemExit(WEB_PAGE)
    markdown = Markdown(name, text)
    return markdown if markdown.chapters else PlainText(name, text)


def sniffed(data, types):
    return next((found for signature, found in types.items() if signature.match(data)), None)


def zipped(data, name):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return Unsupported(name)
    names = archive.namelist()
    if "word/document.xml" in names:
        return Docx(archive, name)
    if "mimetype" in names and archive.read("mimetype").strip() == b"application/epub+zip":
        return Epub(data, name)
    return Unsupported(name)


class Format:
    kind = "document"
    readable = True
    author = ""

    def __init__(self, name):
        self.name = name

    @property
    def title(self):
        return re.sub(r"[-_]+", " ", Path(self.name).stem).strip() or "Untitled book"

    @property
    def chapters(self):
        return []

    def book(self):
        return Book(self.title, self.author, self.chapters)

    def sensed(self, senses):
        return self.book()

    def one_chapter(self, text):
        return Book(self.title, self.author, [Chapter(1, self.title, text, "body")])


class Unsupported(Format):
    readable = False


class Image(Format):
    kind = "image"

    def __init__(self, name, data, mime):
        super().__init__(name)
        self.data = data
        self.mime = mime

    def sensed(self, senses):
        return self.one_chapter(senses.seen(self.data))


class Audio(Format):
    kind = "audio"

    def __init__(self, name, data, suffix):
        super().__init__(name)
        self.data = data
        self.suffix = suffix

    def sensed(self, senses):
        return self.one_chapter(senses.heard(self.data, self.suffix))


class Text(Format):
    def __init__(self, name, text):
        super().__init__(name)
        self.text = text

    @property
    def title(self):
        return lone_title(self.text) or super().title


class Markdown(Text):
    @cached_property
    def chapters(self):
        return headed_chapters(self.text)


class Docx(Text):
    def __init__(self, archive, name):
        super().__init__(name, docx_markdown(ET.fromstring(archive.read("word/document.xml"))))
        has_core = "docProps/core.xml" in archive.namelist()
        self.core = ET.fromstring(archive.read("docProps/core.xml")) if has_core else None

    @property
    def title(self):
        return self.property("title") or super().title

    @cached_property
    def chapters(self):
        return headed_chapters(self.text) or plain_chapters(self.text)

    @property
    def author(self):
        return self.property("creator")

    def property(self, name):
        found = self.core.find(f"{{*}}{name}") if self.core is not None else None
        return one_line(found.text or "") if found is not None else ""


class Pdf(Text):
    def __init__(self, name, data):
        self.objects = pdf_objects(data)
        infos = PDF_INFO_RE.findall(data)
        self.info = self.objects.get(int(infos[-1]), PdfObject(b"", None)) if infos else PdfObject(b"", None)
        super().__init__(name, PAGE_BREAK.join(self.page_text(page) for page in self.pages()))

    @property
    def title(self):
        return one_line(pdf_string(pdf_dict_string(self.info.head, b"/Title"))) or super(Text, self).title

    @cached_property
    def chapters(self):
        if not self.text.strip():
            return []
        return text_chapters(self.text)

    def sensed(self, senses):
        if self.text.strip():
            return self.book()
        scanned = PAGE_BREAK.join(senses.scanned(self.page_images()))
        return Book(self.title, self.author, text_chapters(scanned))

    def pages(self):
        catalog = next(
            (found for found in self.objects.values() if re.search(rb"/Type\s*/Catalog\b", found.head)), None
        )
        root = re.search(rb"/Pages\s+(\d+)\s+\d+\s+R", catalog.head) if catalog else None
        if root:
            return list(self.page_leaves(int(root[1]), set()))
        return [
            found for _, found in sorted(self.objects.items()) if re.search(rb"/Type\s*/Page\b", found.head)
        ]

    def page_leaves(self, number, seen):
        node = self.objects.get(number)
        if node is None or number in seen:
            return
        seen.add(number)
        kids = PDF_KIDS_RE.search(node.head)
        if kids is None:
            yield node
            return
        for kid in PDF_REF_RE.findall(kids[1]):
            yield from self.page_leaves(int(kid), seen)

    def page_text(self, page):
        contents = PDF_CONTENTS_RE.search(page.head)
        streams = (
            [self.objects.get(int(number)) for number in PDF_REF_RE.findall(contents[1])] if contents else []
        )
        return shown_text(
            b"\n".join(stream.stream for stream in streams if stream and readable_stream(stream))
        )

    def page_images(self):
        return [image for image in map(self.page_image, self.pages()) if image is not None]

    def page_image(self, page):
        reference = PDF_RESOURCES_REF_RE.search(page.head)
        resources = self.dereferenced(reference[1]) if reference else page.head
        listing = PDF_XOBJECT_RE.search(resources)
        if listing is None:
            return None
        pictures = (
            self.objects.get(int(number)) for number in PDF_REF_RE.findall(self.dereferenced(listing[1]))
        )
        return next(
            (
                picture.stream
                for picture in pictures
                if picture and picture.stream is not None and PDF_JPEG_RE.search(picture.head)
            ),
            None,
        )

    def dereferenced(self, value):
        reference = PDF_REF_RE.fullmatch(value)
        return self.objects.get(int(reference[1]), PdfObject(b"", None)).head if reference else value


def pdf_objects(data):
    objects = {int(match[1]): pdf_object(match[2]) for match in PDF_OBJECT_RE.finditer(data)}
    for packed in [found for found in objects.values() if re.search(rb"/Type\s*/ObjStm\b", found.head)]:
        for number, found in unpacked_objects(packed).items():
            objects.setdefault(number, found)
    return objects


def pdf_object(body):
    start = PDF_STREAM_RE.search(body)
    if start is None:
        return PdfObject(body, None)
    head = body[: start.start()]
    length = PDF_LENGTH_RE.search(head)
    end = start.end() + int(length[1]) if length else body.rfind(b"endstream")
    return PdfObject(head, inflated(head, body[start.end() : end]))


def inflated(head, raw):
    if b"/FlateDecode" not in head:
        return raw
    try:
        return zlib.decompressobj().decompress(raw)
    except zlib.error:
        return b""


def readable_stream(found):
    return found.stream is not None and (b"/Filter" not in found.head or b"/FlateDecode" in found.head)


def unpacked_objects(packed):
    count = re.search(rb"/N\s+(\d+)", packed.head)
    first = re.search(rb"/First\s+(\d+)", packed.head)
    if not (count and first and packed.stream):
        return {}
    numbers = [int(number) for number in packed.stream[: int(first[1])].split()[: 2 * int(count[1])]]
    pairs = list(zip(numbers[::2], numbers[1::2]))
    ends = [offset for _, offset in pairs[1:]] + [len(packed.stream) - int(first[1])]
    body = packed.stream[int(first[1]) :]
    return {number: PdfObject(body[start:end], None) for (number, start), end in zip(pairs, ends)}


def pdf_dict_string(head, key):
    found = re.search(re.escape(key) + rb"\s*", head)
    token = next(pdf_tokens(head[found.end() :]), None) if found else None
    return token[1] if token and token[0] == "string" else b""


def pdf_string(raw):
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("latin-1")


def shown_text(content):
    pieces, operands = [], []
    for kind, value in pdf_tokens(content):
        if kind != "operator":
            operands.append((kind, value))
            continue
        pieces.append(operator_text(value, operands))
        operands = []
    lines = (" ".join(line.split()) for line in "".join(pieces).splitlines())
    return "\n".join(line for line in lines if line)


def operator_text(operator, operands):
    numbers = [value for kind, value in operands if kind == "number"]
    if operator in (b"Tj", b"TJ"):
        return shown_strings(operands)
    if operator in (b"'", b'"'):
        return "\n" + shown_strings(operands)
    if operator in (b"T*", b"Tm"):
        return "\n"
    if operator in (b"Td", b"TD"):
        return "\n" if numbers and numbers[-1] else " "
    return ""


def shown_strings(operands):
    return "".join(
        value.decode("latin-1")
        if kind == "string"
        else " "
        if kind == "number" and value <= PDF_WORD_GAP
        else ""
        for kind, value in operands
    )


def pdf_tokens(data):
    position = 0
    while position < len(data):
        byte = data[position : position + 1]
        if byte.isspace() or byte == b"\x00" or byte in b"{}":
            position += 1
        elif byte == b"%":
            line_end = re.compile(rb"[\r\n]").search(data, position)
            position = line_end.end() if line_end else len(data)
        elif byte == b"(":
            value, position = literal_string(data, position + 1)
            yield "string", value
        elif data.startswith((b"<<", b">>"), position):
            position += 2
        elif byte == b"<":
            end = data.find(b">", position)
            end = len(data) if end < 0 else end
            yield "string", hex_string(data[position + 1 : end])
            position = end + 1
        elif byte in (b"[", b"]"):
            yield byte.decode(), None
            position += 1
        else:
            word = PDF_WORD_RE.match(data, position)[0] or byte
            position += len(word)
            yield pdf_word(word)
            if word == b"ID":
                image_end = PDF_INLINE_IMAGE_END_RE.search(data, position)
                position = image_end.end() if image_end else len(data)


def pdf_word(word):
    if word.startswith(b"/"):
        return "name", word
    if PDF_NUMBER_RE.fullmatch(word):
        return "number", float(word)
    return "operator", word


def hex_string(inner):
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", inner).decode()
    return bytes.fromhex(digits + "0" * (len(digits) % 2))


def literal_string(data, position):
    value, depth = bytearray(), 1
    while position < len(data):
        byte = data[position : position + 1]
        position += 1
        if byte == b"\\":
            escaped, position = string_escape(data, position)
            value += escaped
            continue
        depth += {b"(": 1, b")": -1}.get(byte, 0)
        if depth == 0:
            break
        value += byte
    return bytes(value), position


def string_escape(data, position):
    octal = PDF_OCTAL_RE.match(data, position)
    if octal:
        return bytes([int(octal[0], 8) & 0xFF]), octal.end()
    byte = data[position : position + 1]
    if byte == b"\r":
        return b"", position + (2 if data.startswith(b"\r\n", position) else 1)
    if byte == b"\n":
        return b"", position + 1
    return PDF_ESCAPES.get(byte, byte), position + 1


def docx_markdown(document):
    return "\n\n".join(filter(None, (paragraph_line(paragraph) for paragraph in document.iter(f"{WORD}p"))))


def paragraph_line(paragraph):
    text = one_line("".join(run.text or "" for run in paragraph.iter(f"{WORD}t")))
    style = paragraph.find(f"{WORD}pPr/{WORD}pStyle")
    marks = heading_marks(style.get(f"{WORD}val", "") if style is not None else "")
    return f"{marks} {text}" if marks and text else text


def heading_marks(style):
    if style == "Title":
        return "#"
    level = re.fullmatch(r"Heading(\d)", style)
    return "#" * min(int(level.group(1)) + 1, 6) if level else ""


class PlainText(Text):
    @cached_property
    def chapters(self):
        return plain_chapters(self.text)


def lone_title(text):
    headings = [(len(match.group(1)), match.group(2)) for match in HEADING_RE.finditer(text)]
    top = [
        title
        for level, title in headings
        if level == min((heading_level for heading_level, _ in headings), default=0)
    ]
    return top[0].strip() if len(top) == 1 and len(headings) > 1 else ""


def headed_chapters(text):
    headings = [(len(match.group(1)), match.group(2), match.start()) for match in HEADING_RE.finditer(text)]
    if lone_title(text):
        headings = [heading for heading in headings if heading[0] != min(level for level, _, _ in headings)]
    depth = chapter_depth([level for level, _, _ in headings])
    return sliced(text, {start: title for level, title, start in headings if level <= depth})


def text_chapters(text):
    return headed_chapters(text) or plain_chapters(text)


def plain_chapters(text):
    marks = {match.start(): match.group(0).strip() for match in CHAPTER_LINE_RE.finditer(text)}
    if len(marks) >= 2:
        return sliced(text, marks)
    return numbered(
        [(f"Part {number}", piece) for number, piece in enumerate(split_text(text, READ_WINDOW_CHARS), 1)]
    )


def chapter_depth(depths):
    for depth in sorted(set(depths)):
        if sum(level <= depth for level in depths) >= READ_MIN_CHAPTERS:
            return depth
    return max(depths, default=1)


def sliced(text, marks):
    if not marks:
        return []
    starts = sorted(marks)
    pieces = [("Opening pages", text[: starts[0]])] if starts[0] > 0 else []
    pieces += [(marks[start], text[start:end]) for start, end in zip(starts, starts[1:] + [len(text)])]
    return numbered(pieces)


def numbered(pieces):
    pieces = [(one_line(title), tidy(text)) for title, text in pieces]
    pieces = [(title, text) for title, text in pieces if past_heading(title, text)]
    return [Chapter(number, title, text, kind_of(title)) for number, (title, text) in enumerate(pieces, 1)]


def past_heading(title, text):
    return [line for line in text.splitlines() if line and line != title and not HEADING_RE.match(line)]


def tidy(text):
    lines = [one_line(line) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def kind_of(title):
    name = bare_title(title)
    if name in INDEX_TITLES:
        return "index"
    if name in UNREAD_MATTER_TITLES:
        return "raw"
    return "front" if name in ABOUT_CHAPTER_TITLES else "body"


def bare_title(title):
    name = TITLE_NUMBERING_RE.sub("", title.lower().strip())
    return re.sub(r"[\s.:;,!?]+$", "", name)


class Epub(Format):
    kind = "book"

    def __init__(self, data, name):
        super().__init__(name)
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            self.opf_path = self.xml("META-INF/container.xml").find(".//{*}rootfile").get("full-path")
            self.opf = self.xml(self.opf_path)
        except (zipfile.BadZipFile, KeyError, AttributeError, TypeError, ET.ParseError) as exc:
            raise SystemExit(f"This is not an EPUB Tinker can read ({first_line(str(exc), 120)}).") from None
        self.manifest = {item.get("id"): item for item in self.opf.iterfind(".//{*}manifest/{*}item")}

    @property
    def title(self):
        return self.metadata("title") or super().title

    @property
    def author(self):
        return self.metadata("creator")

    @cached_property
    def chapters(self):
        text, starts = self.spine_text()
        return (
            contents_chapters(text, starts, self.contents()) or headed_chapters(text) or plain_chapters(text)
        )

    def xml(self, path):
        return ET.fromstring(self.zip.read(path))

    def text_of(self, path):
        return self.zip.read(path).decode("utf-8", errors="replace")

    def metadata(self, name):
        found = self.opf.find(f".//{{*}}metadata/{{*}}{name}")
        return one_line(found.text) if found is not None else ""

    def spine_text(self):
        parts, starts, size = [], {}, 0
        for ref in self.opf.iterfind(".//{*}spine/{*}itemref"):
            item = self.manifest.get(ref.get("idref"))
            path = located(item.get("href"), self.opf_path)[0] if item is not None else ""
            if path not in self.zip.namelist():
                continue
            text, ids = XhtmlText.of(self.text_of(path))
            starts[(path, "")] = size
            starts.update({(path, found): size + at for found, at in ids.items()})
            parts.append(text + "\n\n")
            size += len(text) + 2
        return SpineText("".join(parts), starts)

    def contents(self):
        nav = next(
            (item for item in self.manifest.values() if "nav" in (item.get("properties") or "").split()), None
        )
        if nav is not None:
            document = located(nav.get("href"), self.opf_path)[0]
            entries = NavEntries.of(self.text_of(document))
        else:
            ncx = next(
                (
                    item
                    for item in self.manifest.values()
                    if item.get("media-type") == "application/x-dtbncx+xml"
                ),
                None,
            )
            if ncx is None:
                return []
            document = located(ncx.get("href"), self.opf_path)[0]
            entries = list(ncx_entries(self.xml(document).find(".//{*}navMap")))
        return [ContentsEntry(depth, title, located(href, document)) for depth, title, href in entries]


def located(href, document):
    path, _, fragment = urllib.parse.unquote(href or "").partition("#")
    return Located(
        posixpath.normpath(posixpath.join(posixpath.dirname(document), path)) if path else document, fragment
    )


def contents_chapters(text, starts, contents):
    depth = chapter_depth([entry_depth for entry_depth, _, _ in contents])
    marks = {}
    for entry_depth, title, (path, fragment) in contents:
        start = starts.get((path, fragment), starts.get((path, "")))
        if entry_depth <= depth and start is not None:
            marks.setdefault(start, title)
    return sliced(text, marks)


def ncx_entries(parent, depth=1):
    if parent is None:
        return
    for point in parent.findall("{*}navPoint"):
        label, content = point.find("{*}navLabel/{*}text"), point.find("{*}content")
        if label is not None and content is not None:
            yield NcxEntry(depth, one_line(label.text), content.get("src"))
        yield from ncx_entries(point, depth + 1)


class XhtmlText(html.parser.HTMLParser):
    BLOCKS = {
        "p",
        "div",
        "section",
        "article",
        "aside",
        "blockquote",
        "pre",
        "table",
        "tr",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
        "hr",
        "header",
        "footer",
        "body",
    }
    HEADINGS = {f"h{level}": level for level in range(1, 7)}
    HIDDEN = {"head", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.size, self.ids, self.hidden = [], 0, {}, 0

    @classmethod
    def of(cls, markup):
        parser = cls()
        parser.feed(markup)
        parser.close()
        return ParsedText("".join(parser.parts), parser.ids)

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
            return
        found = dict(attrs).get("id")
        if found:
            self.ids.setdefault(found, self.size)
        if tag in self.HEADINGS:
            self.add("\n\n" + "#" * self.HEADINGS[tag] + " ")
        elif tag in self.BLOCKS:
            self.add("\n\n")
        elif tag == "li":
            self.add("\n- ")
        elif tag == "br":
            self.add("\n")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.HEADINGS or tag in self.BLOCKS:
            self.add("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.add(re.sub(r"\s+", " ", data))

    def add(self, text):
        self.parts.append(text)
        self.size += len(text)


class NavEntries(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.navs, self.depth, self.link, self.words, self.inside = [], 0, None, [], False

    @classmethod
    def of(cls, markup):
        parser = cls()
        parser.feed(markup)
        parser.close()
        tocs = [entries for toc, entries in parser.navs if toc and entries] or [
            nav_entries for _, nav_entries in parser.navs if nav_entries
        ]
        return tocs[0] if tocs else []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            self.navs.append(("toc" in f"{attrs.get('epub:type', '')} {attrs.get('role', '')}", []))
            self.inside, self.depth = True, 0
        elif self.inside and tag == "ol":
            self.depth += 1
        elif self.inside and tag == "a":
            self.link, self.words = attrs.get("href") or "", []

    def handle_endtag(self, tag):
        if tag == "nav":
            self.inside = False
        elif self.inside and tag == "ol":
            self.depth -= 1
        elif self.inside and tag == "a" and self.link is not None:
            title = one_line("".join(self.words))
            if title and self.link:
                self.navs[-1][1].append((max(self.depth, 1), title, self.link))
            self.link = None

    def handle_data(self, data):
        if self.link is not None:
            self.words.append(data)
