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
    missing = sorted(
        spec_points() - points_tested(), key=lambda point: (point.split("-")[0], int(point.split("-")[1]))
    )
    assert not missing, f"spec points with no test: {', '.join(missing)}"


def test_every_test_names_a_spec_point():
    unknown = sorted(points_tested() - spec_points())
    assert not unknown, f"tests name spec points SPEC.md does not have: {', '.join(unknown)}"


def test_each_section_numbers_its_points_from_one_in_order():
    defined = re.findall(
        r"^- \*\*([A-Z]{2,4})-(\d+)\*\*", (ROOT / "SPEC.md").read_text(encoding="utf-8"), re.M
    )
    numbers = {}
    for prefix, number in defined:
        numbers.setdefault(prefix, []).append(int(number))
    unordered = [prefix for prefix, found in numbers.items() if found != list(range(1, len(found) + 1))]
    assert not unordered, f"sections not numbered 1, 2, 3 ... in order: {', '.join(unordered)}"


def test_every_xfail_is_strict_and_names_its_issue():
    tests = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "tests").glob("test_*.py"))
    marks = re.findall(r"mark\.xfail\(([^)]*)\)", tests)
    assert all(re.search(r'strict=True, reason="ISSUE-\d+: ', mark) for mark in marks), marks
