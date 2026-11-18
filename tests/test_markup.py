import io
import sys

import pytest

from tinkerkit.chat.markup import Markup, pieces


@pytest.mark.spec("CNV-1")
def test_markup_plain():
    text = "**bold**, *it*, _also_, ~~gone~~, `x_y*`, [docs](https://x.org/a_b), my_var_name, \\*lit\\*"
    assert Markup.plain(text) == "bold, it, also, gone, x_y*, docs (https://x.org/a_b), my_var_name, *lit*"
    assert Markup.plain("# Title\n- one\n* two") == "Title\n• one\n• two"
    assert Markup.plain("```python\nprint(1)\n```") == "print(1)"


@pytest.mark.spec("CNV-1")
def test_markup_html():
    assert (
        Markup.html("**b** & <x> `a<b` [l](https://x.org/?a=1&b=2)")
        == '<b>b</b> &amp; &lt;x&gt; <code>a&lt;b</code> <a href="https://x.org/?a=1&amp;b=2">l</a>'
    )
    assert Markup.html("# Title\n- a") == "<b>Title</b>\n• a"
    assert Markup.html("```sh\nls -l\n```") == "<pre>ls -l</pre>"
    assert Markup.html("```output\n1 & 2\n```") == "<blockquote expandable>1 &amp; 2</blockquote>"
    assert Markup.html("~~old~~ *new*") == "<s>old</s> <i>new</i>"


@pytest.mark.spec("CNV-1")
def test_markup_ansi_and_literal(monkeypatch):
    not_a_terminal = io.StringIO()
    monkeypatch.setattr(sys, "stdout", not_a_terminal)
    assert Markup.ansi("**b**") == "b"
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.setattr(sys, "stdout", tty)
    assert Markup.ansi("**b** `c`") == "\033[1mb\033[22m \033[36mc\033[39m"
    output = Markup.ansi("```output\n" + "\n".join(str(number) for number in range(40)) + "\n```")
    assert "\033[2m  │ 29\033[22m" in output and "… 10 more lines" in output and "│ 30" not in output
    monkeypatch.setenv("NO_COLOR", "1")
    assert Markup.ansi("**b**") == "b"
    assert Markup.literal("a*b_c`d~e\\f") == "a\\*b\\_c\\`d\\~e\\\\f"
    assert Markup.plain(Markup.literal("**not bold**")) == "**not bold**"
    assert Markup.fenced("a ``` b") == "a ˋˋˋ b"


@pytest.mark.spec("CNV-1")
def test_markup_of_a_t_string():
    said, bold, line, out = "*hi*", "**ok**", "ls -a", "a ``` b"
    assert Markup.of(t"🔎 {said} {bold:md} {line:code}") == "🔎 \\*hi\\* **ok** `ls -a`"
    assert Markup.of(t"{out:block}") == "```\na ˋˋˋ b\n```"
    with pytest.raises(ValueError):
        Markup.of(t"{said:bold}")


@pytest.mark.spec("CNV-1")
def test_columns():
    table = (
        "NAME                 ID              SIZE      MODIFIED\n"
        "qwen3:8b             500a1f067a9f    5.2 GB    2 days ago\n"
        "llama3.2:latest      a80c4f17acd5    2.0 GB    3 weeks ago"
    )
    assert Markup.columns(table) == (
        "NAME | ID | SIZE | MODIFIED\nqwen3:8b | 500a1f067a9f | 5.2 GB | 2 days ago\n"
        "llama3.2:latest | a80c4f17acd5 | 2.0 GB | 3 weeks ago"
    )
    for text in ("just some words here\nand more words there", "one line only with words", "a\nb"):
        assert Markup.columns(text) == text
    assert (
        Markup.html(f"```output\n{table}\n```")
        == f"<blockquote expandable>{Markup.columns(table)}</blockquote>"
    )
    assert Markup.html(f"```\n{table}\n```") == f"<pre>{table}</pre>"
    assert Markup.ansi(f"```\n{table}\n```").count(" | ") == 0


@pytest.mark.spec("CNV-1")
def test_columns_leave_lines_outside_the_aligned_rows_whole():
    table = (
        "Reading list\n"
        "NUMBER  TITLE                   ADDED\n"
        "3       Lighthouse Keeping      2026-11-10\n"
        "2       The Craft               2026-11-09\n"
        "1       Tides and Moons         2026-11-01\n"
        "Page 1 of 2: tinker read list 2 for more."
    )
    assert Markup.columns(table) == (
        "Reading list\nNUMBER | TITLE | ADDED\n3 | Lighthouse Keeping | 2026-11-10\n2 | The Craft | 2026-11-09\n"
        "1 | Tides and Moons | 2026-11-01\nPage 1 of 2: tinker read list 2 for more."
    )
    read_list = (
        "📚 Reading list\n"
        "  • #3 📕 Lighthouse Keeping: 2 of 9 chapters · 1.2 MB · added 2026-11-10\n"
        "  • #2 📄 The Craft: not started · 40 KB · added 2026-11-09\n"
        "  • #1 🌐 Tides and Moons of Earth: learned · added 2026-11-01 ✓\n"
        "👉 Page 1 of 2: tinker read list 2 for more."
    )
    assert Markup.columns(read_list) == read_list


@pytest.mark.spec("CNV-2")
def test_pieces_split_between_lines_and_lose_no_text():
    text = "\n".join(f"line {number:03}" for number in range(100))
    parts = pieces(text, size=100)
    assert all(len(part) <= 100 for part in parts) and "\n".join(parts) == text
    assert pieces("") == [] and pieces("one") == ["one"]
    a_long_line = "short\n" + "x" * 250 + "\nend"
    assert "".join("".join(pieces(a_long_line, size=100)).split()) == "".join(a_long_line.split())
