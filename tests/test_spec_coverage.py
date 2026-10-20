"""SPEC.md is the source of truth and the tests enforce it: every point it states has a test, and every test
names a point it states."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POINT_RE = re.compile(r"\*\*([A-Z]{2,4}-\d+)\*\*")
MARK_RE = re.compile(r"""mark\.spec\(\s*((?:["'][A-Z]{2,4}-\d+["']\s*,?\s*)+)\)""")


def spec_points():
    return set(POINT_RE.findall((ROOT / "SPEC.md").read_text(encoding="utf-8")))


def points_tested():
    found = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for marks in MARK_RE.findall(path.read_text(encoding="utf-8")):
            found |= set(re.findall(r"[A-Z]{2,4}-\d+", marks))
    return found


def test_every_spec_point_has_a_test():
    missing = sorted(spec_points() - points_tested(), key=lambda p: (p.split("-")[0], int(p.split("-")[1])))
    assert not missing, f"spec points with no test: {', '.join(missing)}"


def test_every_test_names_a_spec_point():
    unknown = sorted(points_tested() - spec_points())
    assert not unknown, f"tests name spec points SPEC.md does not have: {', '.join(unknown)}"


def test_every_known_deviation_names_a_point_and_an_xfail_test():
    text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    table = text.split("## Known deviations", 1)[1].split("\n---", 1)[0]
    issues = re.findall(r"\| (ISSUE-\d+) \| ([A-Z]{2,4}-\d+) \|", table)
    assert issues, "the known deviations table lists no issue"
    tests = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py"))
    for issue, point in issues:
        assert point in spec_points(), f"{issue} names {point}, which SPEC.md does not state"
        assert re.search(rf"xfail\(strict=True, reason=\"{issue}\b", tests), f"{issue} has no strict xfail test"
