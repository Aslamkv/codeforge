import re
lazy import difflib
lazy import hashlib
lazy import time
lazy import urllib.parse


def first_line(text, limit=140):
    lines = (text or "").strip().splitlines()
    line = lines[0] if lines else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def one_line(value):
    return " ".join(str(value or "").split())


def field(item, key, limit=None):
    return str(item.get(key) or "").strip()[:limit]


def host(url):
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def now_text():
    return time.strftime("%Y-%m-%d %H:%M")


def clock(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def split_text(text, size):
    size, chunks, current = max(size, 200), [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            chunks += [current] if current else []
            chunks.append(para[:size])
            current, para = "", para[size:]
        if para and current and len(current) + 2 + len(para) > size:
            chunks.append(current)
            current = para
        elif para:
            current = f"{current}\n\n{para}" if current else para
    return chunks + ([current] if current else [])


def fingerprint(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def similarity(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
