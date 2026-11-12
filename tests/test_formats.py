import io
import zipfile
import zlib

import pytest
from conftest import FOREWORD, INDEX, MARKDOWN, NAMING, TESTING

from tinkerkit import formats
from tinkerkit.formats import kind_of, opened

CONTAINER = (
    '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)
NAV = (
    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
    '<nav epub:type="landmarks"><ol><li><a href="text/cover.xhtml">Cover</a></li></ol></nav>'
    '<nav epub:type="toc"><ol><li><a href="text/foreword.xhtml">Foreword</a></li>'
    '<li><a href="text/part1.xhtml">Part I</a><ol><li><a href="text/part1.xhtml#naming">Naming</a></li>'
    '<li><a href="text/part1.xhtml#testing">Testing</a></li></ol></li>'
    '<li><a href="text/index.xhtml">Index</a></li></ol></nav></body></html>'
)
NCX = (
    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
    '<navPoint id="a"><navLabel><text>Foreword</text></navLabel><content src="text/foreword.xhtml"/></navPoint>'
    '<navPoint id="b"><navLabel><text>Part I</text></navLabel><content src="text/part1.xhtml"/>'
    '<navPoint id="c"><navLabel><text>Naming</text></navLabel><content src="text/part1.xhtml#naming"/></navPoint>'
    '<navPoint id="d"><navLabel><text>Testing</text></navLabel><content src="text/part1.xhtml#testing"/></navPoint>'
    '</navPoint><navPoint id="e"><navLabel><text>Index</text></navLabel><content src="text/index.xhtml"/>'
    "</navPoint></navMap></ncx>"
)
DOCUMENTS = {
    "cover.xhtml": "<h1>The Craft</h1>",
    "foreword.xhtml": f"<h1>Foreword</h1><p>{FOREWORD}</p>",
    "part1.xhtml": "<h1>Part I</h1><p>The basics that every small program needs from its first line.</p>"
    f"<h2 id='naming'>Naming</h2><p>{NAMING}</p><h2 id='testing'>Testing</h2><p>{TESTING}</p>",
    "index.xhtml": "<h1>Index</h1><ul>"
    + "".join(f"<li>{line[2:]}</li>" for line in INDEX.splitlines())
    + "</ul>",
}


def epub(nav=True):
    manifest = "".join(
        f'<item id="d{number}" href="text/{name}" media-type="application/xhtml+xml"/>'
        for number, name in enumerate(DOCUMENTS)
    )
    manifest += (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        if nav
        else '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    )
    spine = "".join(f'<itemref idref="d{number}"/>' for number in range(len(DOCUMENTS)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Craft of Small Programs</dc:title>'
        f"<dc:creator>A. Writer</dc:creator></metadata><manifest>{manifest}</manifest>"
        f"<spine{'' if nav else ' toc="ncx"'}>{spine}</spine></package>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", CONTAINER)
        book.writestr("OEBPS/content.opf", opf)
        book.writestr("OEBPS/nav.xhtml" if nav else "OEBPS/toc.ncx", NAV if nav else NCX)
        for name, body in DOCUMENTS.items():
            book.writestr(
                f"OEBPS/text/{name}",
                f"<html><head><title>x</title><style>p{{}}</style></head><body>{body}</body></html>",
            )
    return buffer.getvalue()


def docx(paragraphs, title=""):
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        if style
        else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for style, text in paragraphs
    )
    document = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    core = (
        '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title><dc:creator>A. Writer</dc:creator></cp:coreProperties>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as document_zip:
        document_zip.writestr("[Content_Types].xml", "<Types/>")
        document_zip.writestr("word/document.xml", document)
        document_zip.writestr("docProps/core.xml", core)
    return buffer.getvalue()


def pdf(pages, title="", images=None):
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    kids = []
    for number, (content, compressed) in enumerate(pages):
        page, stream = 4 + 2 * number, 5 + 2 * number
        kids.append(f"{page} 0 R")
        resources = pdf_image_resources(objects, number, (images or {}).get(number))
        objects[page] = f"<< /Type /Page /Parent 2 0 R{resources} /Contents [{stream} 0 R] >>".encode()
        body = zlib.compress(content) if compressed else content
        flate = b" /Filter /FlateDecode" if compressed else b""
        objects[stream] = b"<< /Length %d%s >>\nstream\n%s\nendstream" % (len(body), flate, body)
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    objects[3] = f"<< /Title ({title}) >>".encode() if title else b"<< >>"
    body = b"".join(b"%d 0 obj\n%s\nendobj\n" % (number, objects[number]) for number in sorted(objects))
    return b"%PDF-1.7\n" + body + b"trailer\n<< /Root 1 0 R /Info 3 0 R >>\n%%EOF\n"


def pdf_image_resources(objects, number, image):
    if image is None:
        return ""
    data, image_filter, shared = image
    picture, listing = 100 + 2 * number, 101 + 2 * number
    objects[picture] = (
        b"<< /Type /XObject /Subtype /Image /Filter /%s /Length %d >>\nstream\n%s\nendstream"
        % (
            image_filter,
            len(data),
            data,
        )
    )
    if shared:
        objects[listing] = f"<< /Im1 {picture} 0 R >>".encode()
        return f" /Resources << /XObject {listing} 0 R >>"
    return f" /Resources << /ProcSet [/PDF /ImageC] /XObject << /Im1 {picture} 0 R >> >>"


def pdf_page(*lines):
    shown = b" T* ".join(b"(" + line.replace(b"(", b"\\(").replace(b")", b"\\)") + b") Tj" for line in lines)
    return b"BT /F1 12 Tf 72 720 Td 14 TL " + shown + b" ET"


CRAFT_PARAGRAPHS = [
    ("Title", "The Craft of Small Programs"),
    ("Heading1", "Foreword"),
    ("", FOREWORD),
    ("Heading1", "Naming"),
    ("", NAMING),
    ("Heading2", "Short names"),
    ("", "Short names suit short scopes."),
    ("Heading1", "Empty"),
    ("Heading1", "Testing"),
    ("", TESTING),
]


def titles(book):
    return [(chapter.title, chapter.kind) for chapter in book.chapters]


@pytest.mark.spec("RDG-2")
def test_a_book_is_read_by_what_its_bytes_hold():
    book = opened(epub(), "anything.txt")
    assert (book.title, book.kind) == ("The Craft of Small Programs", "book")
    text = opened(MARKDOWN.encode(), "craft.md")
    assert (text.title, text.kind) == ("The Craft of Small Programs", "document")
    assert opened(b"Some words\n\nand more words.", "my_little-book.txt").title == "my little book"
    unsupported = opened(b"\x00\x01binary", "x.bin")
    assert isinstance(unsupported, formats.Unsupported) and unsupported.chapters == []
    document = opened(docx(CRAFT_PARAGRAPHS), "anything.epub")
    assert (isinstance(document, formats.Docx), document.kind) == (True, "document")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as plain_zip:
        plain_zip.writestr("notes.txt", "Some notes.")
    assert isinstance(opened(buffer.getvalue(), "book.epub"), formats.Unsupported)
    assert isinstance(opened(pdf([(pdf_page(b"Some words."), False)]), "book.epub"), formats.Pdf)
    with pytest.raises(SystemExit, match="a web page, not something to read.*download link"):
        opened(b"<!DOCTYPE html><html><body>Sign in</body></html>", "book.epub")
    assert isinstance(opened(b"PK\x03\x04 broken", "book.epub"), formats.Unsupported)


@pytest.mark.spec("RDG-7")
def test_a_pdf_gives_its_pages_text_in_order_split_as_plain_text():
    first = pdf_page(b"Chapter 1 Naming", NAMING.encode())
    second = b"BT [(Chapter 2 ) -250 <54657374696e67>] TJ T* (" + TESTING.encode() + b") Tj ET"
    document = opened(pdf([(first, False), (second, True)], title="The Craft (Second Edition)"), "craft.pdf")
    assert document.title == "The Craft (Second Edition)"
    assert (
        document.text.index(NAMING) < document.text.index("Chapter 2 Testing") < document.text.index(TESTING)
    )
    assert titles(document) == [("Chapter 1 Naming", "body"), ("Chapter 2 Testing", "body")]
    assert NAMING in document.chapters[0].text and TESTING in document.chapters[1].text
    assert opened(pdf([(first, True)]), "my_paper.pdf").title == "my paper"


@pytest.mark.spec("RDG-7")
def test_a_pdf_with_no_text_has_no_chapters():
    scan = pdf([(b"q 612 0 0 792 0 0 cm /Im1 Do Q", False), (b"q /Im2 Do Q", True)])
    assert opened(scan, "scan.pdf").chapters == []


@pytest.mark.spec("RDG-7")
def test_a_scan_gives_its_pages_jpeg_images_in_order():
    first, second = b"\xff\xd8\xff\xe0 first page", b"\xff\xd8\xff\xe0 second page"
    images = {
        0: (first, b"DCTDecode", False),
        1: (b"\x00flate", b"FlateDecode", False),
        2: (second, b"DCTDecode", True),
    }
    page = (b"q /Im1 Do Q", False)
    scan = opened(pdf([page, page, page, page], images=images), "scan.pdf")
    assert scan.chapters == [] and scan.page_images() == [first, second]
    assert opened(pdf([(pdf_page(b"Some words."), False)]), "text.pdf").page_images() == []


@pytest.mark.spec("RDG-8")
@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"),
        (b"GIF89a\x01\x00", "image/gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
    ],
)
def test_an_image_is_known_by_its_bytes(data, mime):
    image = opened(data, "photo.bin")
    assert isinstance(image, formats.Image)
    assert (image.kind, image.mime, image.data, image.readable, image.chapters) == (
        "image",
        mime,
        data,
        True,
        [],
    )


@pytest.mark.spec("RDG-8")
@pytest.mark.parametrize(
    ("data", "suffix"),
    [
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", ".wav"),
        (b"ID3\x04\x00\x00\x00", ".mp3"),
        (b"\xff\xfb\x90\x64\x00", ".mp3"),
        (b"OggS\x00\x02", ".ogg"),
        (b"fLaC\x00\x00", ".flac"),
        (b"\x00\x00\x00\x20ftypM4A ", ".m4a"),
        (b"\x1a\x45\xdf\xa3\x9f\x42", ".webm"),
        (b"FORM\x00\x07\x0f\x68AIFFCOMM", ".aiff"),
        (b"FORM\x00\x07\x0f\x68AIFCFVER", ".aiff"),
    ],
)
def test_a_recording_is_known_by_its_bytes(data, suffix):
    audio = opened(data, "talk.txt")
    assert isinstance(audio, formats.Audio)
    assert (audio.kind, audio.suffix, audio.data, audio.readable, audio.chapters) == (
        "audio",
        suffix,
        data,
        True,
        [],
    )


class FakeSenses:
    def seen(self, image):
        return f"A drawing of {image[-5:].decode()}."

    def scanned(self, images):
        return [f"Read from {image[-4:].decode()}." for image in images]

    def heard(self, data, suffix):
        return f"Heard {suffix} speech."


@pytest.mark.spec("RDG-8")
def test_an_image_and_a_recording_are_one_chapter_from_the_senses():
    image = opened(b"\x89PNG\r\n\x1a\n\x00 a cat", "my-cat.png").sensed(FakeSenses())
    audio = opened(b"OggS\x00\x02 talk", "a_talk.ogg").sensed(FakeSenses())
    assert [
        (book.title, [(c.number, c.title, c.text, c.kind) for c in book.chapters]) for book in (image, audio)
    ] == [
        ("my cat", [(1, "my cat", "A drawing of a cat.", "body")]),
        ("a talk", [(1, "a talk", "Heard .ogg speech.", "body")]),
    ]


@pytest.mark.spec("RDG-7")
def test_a_scan_is_read_page_by_page_from_the_senses_and_a_text_pdf_is_not():
    images = {
        0: (b"\xff\xd8\xff\xe0 one", b"DCTDecode", False),
        1: (b"\xff\xd8\xff\xe0 two", b"DCTDecode", False),
    }
    page = (b"q /Im1 Do Q", False)
    scan = opened(pdf([page, page], images=images), "scan.pdf").sensed(FakeSenses())
    assert (
        " ".join(chapter.text for chapter in scan.chapters).split() == "Read from one. Read from two.".split()
    )
    text = opened(pdf([(pdf_page(b"Some words."), False)]), "text.pdf")
    assert text.sensed(None) == text.book()
    assert opened(MARKDOWN.encode(), "craft.md").sensed(None) == opened(MARKDOWN.encode(), "craft.md").book()


@pytest.mark.spec("RDG-3")
@pytest.mark.parametrize("nav", [True, False], ids=["epub3-nav", "epub2-ncx"])
def test_an_epub_is_split_where_its_contents_say(nav):
    book = opened(epub(nav), "book.epub")
    assert book.author == "A. Writer"
    assert titles(book) == [
        ("Foreword", "front"),
        ("Part I", "body"),
        ("Naming", "body"),
        ("Testing", "body"),
        ("Index", "index"),
    ]
    naming = book.chapters[2]
    assert NAMING in naming.text and TESTING not in naming.text and "p{}" not in naming.text


@pytest.mark.spec("RDG-3")
def test_an_epub_with_no_usable_contents_is_split_at_its_headings(monkeypatch):
    monkeypatch.setattr(formats.Epub, "contents", lambda self: [])
    book = opened(epub(), "book.epub")
    assert [title for title, _ in titles(book)] == ["Foreword", "Part I", "Naming", "Testing", "Index"]


@pytest.mark.spec("RDG-4")
def test_markdown_and_plain_text_are_split_into_chapters():
    assert titles(opened(MARKDOWN.encode(), "craft.md")) == [
        ("Foreword", "front"),
        ("Naming", "body"),
        ("Stories", "body"),
        ("Testing", "body"),
        ("Index", "index"),
    ]
    plain = (
        f"Contents\nChapter 1 Naming\nChapter 2 Testing\n\nChapter 1 Naming\n{NAMING}\n\n"
        f"Chapter 2 Testing\n{TESTING}"
    )
    assert [title for title, _ in titles(opened(plain.encode(), "craft.txt"))] == [
        "Chapter 1 Naming",
        "Chapter 2 Testing",
    ]
    parts = opened(("A long plain paragraph of words with no heading at all.\n\n" * 800).encode(), "long.txt")
    assert [chapter.title for chapter in parts.chapters][:2] == ["Part 1", "Part 2"]


@pytest.mark.spec("RDG-4")
def test_a_chapter_with_any_text_past_its_heading_is_kept():
    short = opened(b"## A\n\nOne line.\n\n## B\n\n## C\n\nAnother line.", "short.md")
    assert [chapter.title for chapter in short.chapters] == ["A", "C"]


@pytest.mark.spec("RDG-4")
def test_a_docx_is_split_at_its_heading_paragraphs():
    document = opened(docx(CRAFT_PARAGRAPHS, title="Small Programs"), "craft.docx")
    assert (document.title, document.author) == ("Small Programs", "A. Writer")
    assert [title for title, _ in titles(document)] == ["Foreword", "Naming", "Short names", "Testing"]
    assert NAMING in document.chapters[1].text and "Short names suit" in document.chapters[2].text
    assert opened(docx(CRAFT_PARAGRAPHS), "craft.docx").title == "The Craft of Small Programs"
    plain = opened(docx([("", "Just words.")]), "my_notes.docx")
    assert (plain.title, [chapter.text for chapter in plain.chapters]) == ("my notes", ["Just words."])


@pytest.mark.spec("RDG-5")
def test_chapters_have_kinds_by_their_titles():
    assert [
        kind_of(title)
        for title in (
            "Index",
            "Table of Contents",
            "Acknowledgements",
            "Preface",
            "Introduction",
            "3. Naming things",
            "3. Preface",
            "CONTENTS",
            "Chapter IV: Introduction.",
            "An Index of Patterns",
            "Introduction to Gardening",
            "Copyright Law",
        )
    ] == ["index", "raw", "raw", "front", "front", "body", "front", "raw", "front", "body", "body", "body"]
