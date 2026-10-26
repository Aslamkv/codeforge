import re

import pytest

from tinkerkit.text import field, fingerprint, first_line, host, one_line, similarity, split_text, words


@pytest.mark.spec("TXT-1")
def test_first_line():
    assert first_line("\n  hello world\nsecond") == "hello world"
    assert first_line(None) == "" and first_line("   ") == ""
    assert first_line("x" * 200, 10) == "xxxxxxx..."


@pytest.mark.spec("TXT-1")
def test_one_line():
    assert one_line("  a\n\tb   c ") == "a b c" and one_line(None) == "" and one_line(7) == "7"


@pytest.mark.spec("TXT-1")
def test_field_is_the_stripped_text_of_a_key_cut_to_its_limit():
    item = {"name": "  tinker  ", "none": None, "number": 7}
    assert field(item, "name") == "tinker" and field(item, "name", 3) == "tin"
    assert field(item, "none") == "" and field(item, "missing") == "" and field(item, "number") == "7"


@pytest.mark.spec("TXT-1")
def test_host_is_the_lowercase_site_without_www():
    assert host("https://www.Example.org/x") == "example.org"
    assert host("http://docs.python.org:8080/3/") == "docs.python.org:8080"


@pytest.mark.spec("TXT-1")
def test_words_fingerprint_and_similarity():
    assert words("Hello, World 42!") == ["hello", "world", "42"]
    assert words(None) == []
    assert fingerprint("a") == fingerprint("a") != fingerprint("b")
    assert re.fullmatch(r"[0-9a-f]{40}", fingerprint("a"))
    assert similarity("ABC", "abc") == 1.0 and similarity("abc", "xyz") == 0.0


@pytest.mark.spec("TXT-2")
def test_split_text_keeps_paragraphs_together_within_the_size():
    text = "\n\n".join(f"paragraph {number} " + "word " * 30 for number in range(10))
    chunks = split_text(text, 400)
    assert all(len(chunk) <= 400 for chunk in chunks)
    assert len(chunks) > 1
    nothing_lost = "".join(text.split()) == "".join("".join(chunks).split())
    split_between_paragraphs = all(chunk.startswith("paragraph") for chunk in chunks)
    assert nothing_lost and split_between_paragraphs


@pytest.mark.spec("TXT-2")
def test_split_text_cuts_a_long_paragraph_and_never_below_200():
    chunks = split_text("x" * 1000, 50)
    assert [len(chunk) for chunk in chunks] == [200] * 5
