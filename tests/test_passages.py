import pytest

from tinkerkit.passages import Block, Cleaner, MainBlocks, Page, Passage, is_html, text_blocks
from tinkerkit.settings import MAX_PASSAGE_CHARS

INSTALL = (
    "Install the tool with the package manager, then run it from the project folder to check every file. "
)
CONFIGURE = "Settings live in one file at the project root; each key has a default that suits most projects. "

PAGE = f"""<!doctype html><html><head><title>Tool docs</title><style>p {{ color: red }}</style></head><body>
<nav><a href="/">Home</a> <a href="/docs">Docs</a></nav>
<header>Site header</header>
<main>
<h1><a href="#start">Getting started</a></h1>
<p>{INSTALL * 3}</p>
<ul><li><a href="/a">Link one</a></li><li><a href="/b">Link two</a></li></ul>
<pre><code>tool check .
    --strict</code></pre>
<h2>Configure</h2>
<p>{CONFIGURE * 3}</p>
<form><input name="q"> Search the docs</form>
<aside>Sponsored</aside>
</main>
<footer>Copyright</footer><script>alert(1)</script>
</body></html>"""


@pytest.mark.spec("PAS-1")
@pytest.mark.parametrize(
    "text,kind,html",
    [
        ("anything", "text/html", True),
        ("anything", "application/xhtml+xml", True),
        ("\n  <!DOCTYPE html><p>x</p>", "", True),
        ("<html><body>x</body></html>", "text/plain", True),
        ("# Title\n<html> later", "text/markdown", False),
        ('{"a": 1}', "application/json", False),
    ],
)
def test_html_is_told_by_its_content_type_or_its_first_tag(text, kind, html):
    assert is_html(text, kind) == html


@pytest.mark.spec("PAS-1")
def test_a_page_keeps_its_main_content_as_blocks():
    page = Cleaner.page("https://tool.dev/start", PAGE, "text/html", "2026-10-21 10:00")
    kinds = [(block.kind, block.text[:15]) for block in page.blocks]
    assert kinds == [
        ("heading", "Getting started"),
        ("text", INSTALL[:15]),
        ("code", "tool check .\n  "),
        ("heading", "Configure"),
        ("text", CONFIGURE[:15]),
    ]
    code_keeps_its_layout = "tool check .\n    --strict"
    assert page.blocks[2].text == code_keeps_its_layout
    shown = " ".join(block.text for block in page.blocks)
    for left_out in (
        "Home",
        "Site header",
        "Link one",
        "Search the docs",
        "Sponsored",
        "Copyright",
        "alert",
        "color",
    ):
        assert left_out not in shown
    assert page.usable()


@pytest.mark.spec("PAS-1")
def test_a_page_keeps_its_title_its_heading_levels_and_its_list_items():
    parsed = MainBlocks.parsed(
        "<html><head><title>Tool docs</title></head><body><h3>Use</h3><ul><li>one</li>"
        '<li><a href="/x">a menu link</a></li></ul><pre>a\n  b</pre></body></html>'
    )
    assert parsed.title == "Tool docs"
    assert [(block.kind, block.text, block.level) for block in parsed.content()] == [
        ("heading", "Use", 3),
        ("item", "one", 0),
        ("code", "a\n  b", 0),
    ]


@pytest.mark.spec("PAS-1")
def test_plain_text_and_markdown_are_read_by_their_lines():
    blocks = text_blocks(
        "# Title\n\nSome text\nthat goes on.\n\n```\ncode line\n  indented\n```\n\n"
        "[One](https://a) [Two](https://b)\n\n## Next\nMore."
    )
    assert blocks == [
        Block("heading", "Title"),
        Block("text", "Some text that goes on."),
        Block("code", "code line\n  indented"),
        Block("heading", "Next"),
        Block("text", "More."),
    ]
    assert Cleaner.page("https://x/readme.md", "# Doc\n\nWords.", "text/plain", "t").blocks[0] == Block(
        "heading", "Doc"
    )


def page(url, *blocks):
    return Page(url, "2026-10-21 10:00", list(blocks))


@pytest.mark.spec("PAS-1")
def test_the_pool_gathers_passages_under_headings_and_numbers_them():
    long_code = "\n".join(f"line {number} of a long example" for number in range(80))
    pool = Cleaner().pool(
        [
            page(
                "https://a.dev/1",
                Block("heading", "Install"),
                Block("text", INSTALL),
                Block("code", "tool install"),
                Block("heading", "Use"),
                Block("text", "x " * 900),
                Block("code", long_code),
            )
        ]
    )
    assert [(passage.id, passage.heading) for passage in pool][:2] == [("P1", "Install"), ("P2", "Use")]
    assert pool[0].text == f"{INSTALL.strip()}\n\ntool install" and pool[0].url == "https://a.dev/1"
    assert pool[0].fetched == "2026-10-21 10:00"
    assert all(len(passage.text) <= MAX_PASSAGE_CHARS for passage in pool if passage.text != long_code)
    assert any(passage.text == long_code for passage in pool)


@pytest.mark.spec("PAS-1")
def test_the_pool_drops_a_sites_repeats_and_near_duplicates():
    chrome = Block("text", "Edit this page on the project's own site, or report a problem with it.")
    pages = [
        page("https://a.dev/1", chrome, Block("text", INSTALL)),
        page("https://a.dev/2", chrome, Block("text", CONFIGURE)),
        page("https://b.org/copy", Block("text", INSTALL + "Really.")),
        page("https://b.org/tiny", Block("text", "Next")),
    ]
    pool = Cleaner().pool(pages)
    assert [passage.url for passage in pool] == ["https://a.dev/1", "https://a.dev/2"]
    assert all("Edit this page" not in passage.text for passage in pool)
    known = [Passage("https://a.dev/1", "t", "", INSTALL, "P1")]
    more = Cleaner().pool(
        [page("https://c.net/x", Block("text", INSTALL)), page("https://c.net/y", Block("text", CONFIGURE))],
        known=known,
    )
    numbered_on_from_the_known_ones = [("P2", "https://c.net/y")]
    assert [(passage.id, passage.url) for passage in more] == numbered_on_from_the_known_ones
