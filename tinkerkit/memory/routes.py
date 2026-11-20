import re
from pathlib import Path
from typing import NamedTuple

from ..messages import say
from ..settings import EVENTS_KEPT_PER_ITEM, ROUTE_CORRECTION_SECONDS, ROUTE_SAME_WORDS, Home
from ..text import similarity
from .memories import counted, history_entry, stamp

SAID_FILLER = tuple(
    "the my please can could would you tinker a an me now just show see view display what is it s usage".split()
)
NAMED_SLOTS = ("MODEL", "SKILL", "CHANNEL")
SLOTS = ("JOB", "PROJECT", "PATH", "URL", *NAMED_SLOTS)
TRAILING_PUNCTUATION = ".,!?;:)'\""
INSTRUCT = "/instruct"
CANCEL = "/cancel"
CONFIRM = "/confirm"


class Matched(NamedTuple):
    route: dict
    line: str


class Masked(NamedTuple):
    words: tuple
    values: dict

    @property
    def pattern(self):
        return " ".join(self.words)


def mask(text, known):
    words, values = [], {}
    for token in text.split():
        slot, value = slot_of(token.rstrip(TRAILING_PUNCTUATION), known)
        if slot:
            words.append(slot)
            values.setdefault(slot, []).append(value)
        else:
            words.extend(plain_words(token))
    return Masked(tuple(words), values)


def slot_of(token, known):
    if re.fullmatch(r"#\d+", token):
        return "JOB", token
    if re.fullmatch(r"@[\w.-]+", token):
        return "PROJECT", token
    if re.fullmatch(r"https?://\S+", token):
        return "URL", token
    if looks_like_path(token) and Path(token).expanduser().exists():
        return "PATH", token
    named = [slot for slot in NAMED_SLOTS if token.lower() in lowered(known.get(slot, ()))]
    return (named[0], token.lower()) if named else (None, None)


def looks_like_path(token):
    return "/" in token or token.startswith("~")


def lowered(names):
    return {name.lower() for name in names}


def plain_words(token):
    return [word for word in re.sub(r"[^\w\s]", " ", token.lower()).split() if word not in SAID_FILLER]


def filled(line, values):
    left = {slot: list(found) for slot, found in values.items()}
    words = []
    for word in line.split():
        if word in SLOTS:
            if not left.get(word):
                return None
            word = left[word].pop(0)
        words.append(word)
    return " ".join(words)


def slotted(line, values):
    slots = {value.lower(): slot for slot, found in values.items() for value in found}
    return " ".join(slots.get(word.lower(), word) for word in line.split())


class NoRoutes:
    def match(self, text, known):
        return None


class Routes:
    ROUTES = "routes"
    COUNTER = "route_ids"

    def __init__(self, deletes=lambda line: False):
        self.deletes = deletes

    def apply(self, deltas, now=None):
        changed = [self.applied(delta, stamp(now)) for delta in deltas]
        return [route for route in changed if route]

    def applied(self, delta, at):
        ops = {
            "add": self.add,
            "use": self.use,
            "confirm": self.confirm,
            "miss": self.miss,
            "retire": self.retire,
        }
        op = ops.get(delta.get("op"))
        if op is None or (op != self.add and not self.open(delta.get("id"))):
            return None
        return op(delta, at)

    def open(self, route_id):
        route = Home.store().get(self.ROUTES, route_id) if route_id else None
        return route is not None and route["status"] != "retired"

    def add(self, delta, at):
        if self.deletes(delta["line"]):
            return None
        living = self.find(delta["pattern"])
        if living and living["line"] == delta["line"]:
            return (
                self.confirm(dict(delta, id=living["id"]), at) if living["status"] == "candidate" else living
            )
        if not living and self.retired_alike(delta):
            return None
        route = self.created(delta, at)
        if living:
            self.retire({"id": living["id"], "why": f"replaced by {route['id']}"}, at)
        return route

    def retired_alike(self, delta):
        return any(
            route["pattern"] == delta["pattern"] and route["line"] == delta["line"]
            for route in self.all()
            if route["status"] == "retired"
        )

    def created(self, delta, at):
        route = {
            "id": f"r{self.next_number()}",
            "pattern": delta["pattern"],
            "line": delta["line"],
            "status": "confirmed" if delta.get("confirmed") else "candidate",
            "source": delta["source"],
            "uses": 0,
            "misses": 0,
            "history": [history_entry("add", at, delta["line"])],
            "retired": "",
        }
        Home.store().put(self.ROUTES, route["id"], route)
        return route

    def use(self, delta, at):
        return self.changed(delta, at, "use", lambda route: {"uses": route["uses"] + 1})

    def confirm(self, delta, at):
        return self.changed(delta, at, "confirm", lambda route: {"status": "confirmed"})

    def miss(self, delta, at):
        return self.changed(delta, at, "miss", lambda route: {"misses": route["misses"] + 1})

    def retire(self, delta, at):
        why = delta.get("why", "")
        return self.changed(
            dict(delta, text=why), at, "retire", lambda route: {"status": "retired", "retired": why}
        )

    def changed(self, delta, at, event, changes):
        return Home.store().update(
            self.ROUTES,
            delta["id"],
            lambda route: dict(
                route,
                **changes(route),
                history=(route["history"] + [history_entry(event, at, delta.get("text", ""))])[
                    -EVENTS_KEPT_PER_ITEM:
                ],
            ),
        )

    def next_number(self):
        return Home.store().update(self.COUNTER, "last", lambda last: (last or 0) + 1)

    def all(self):
        return sorted(Home.store().items(self.ROUTES).values(), key=route_number)

    def living(self):
        return [route for route in self.all() if route["status"] != "retired"]

    def find(self, pattern):
        return next((route for route in self.living() if route["pattern"] == pattern), None)

    def seed(self, seeds, now=None):
        taken = {route["pattern"] for route in self.all()}
        for pattern, line in seeds:
            if pattern not in taken:
                self.created(
                    {"pattern": pattern, "line": line, "source": "seed", "confirmed": True}, stamp(now)
                )
                taken.add(pattern)

    def match(self, text, known):
        masked = mask(text, known)
        pattern = masked.pattern
        route = (self.find(pattern) or self.most_alike(pattern)) if pattern else None
        line = filled(route["line"], masked.values) if route else None
        return Matched(route, line) if line else None

    def most_alike(self, pattern):
        scored = [(similarity(pattern, route["pattern"]), route) for route in self.living()]
        best = max((score for score, _ in scored), default=0)
        tied = [route for score, route in scored if score == best]
        sure = best >= ROUTE_SAME_WORDS and len({route["line"] for route in tied}) == 1
        return tied[0] if sure else None


def route_number(route):
    return int(route["id"].removeprefix("r"))


class Routed(NamedTuple):
    pattern: str
    line: str
    values: dict
    route_id: str | None
    at: object
    proposed: bool
    confirmed: bool


class RouteFeedback:
    def __init__(self, routes, known, quiet):
        self.routes = routes
        self.known = known
        self.quiet = quiet
        self.last = {}

    def routed(self, chat, text, line, route, now):
        masked = mask(text, self.known())
        pattern = masked.pattern
        if not pattern:
            return
        confirmed = bool(route) and route["status"] == "confirmed"
        self.last[chat] = Routed(
            pattern,
            slotted(line, masked.values),
            masked.values,
            route and route["id"],
            now,
            not confirmed and self.needs_yes(line),
            confirmed,
        )
        if confirmed:
            self.routes.apply([{"op": "use", "id": route["id"]}], now)

    def needs_yes(self, line):
        return not (self.quiet(line) or instructs(line) or self.routes.deletes(line))

    def proposes(self, chat):
        last = self.last.get(chat)
        return last is not None and last.proposed

    def answered(self, chat, yes, now):
        if not self.proposes(chat):
            return
        last = self.last.pop(chat)
        self.routes.apply(self.agreed(last) if yes else self.retired(last, "no"), now)

    def refused(self, chat, now):
        last = self.last.get(chat)
        if last is None or not instructs(last.line):
            return
        del self.last[chat]
        self.routes.apply(self.retired(last, "no"), now)

    def heard(self, chat, text, now):
        if self.proposes(chat) or text.split() in ([CONFIRM], [CANCEL]):
            return
        last = self.last.get(chat)
        if last is None or (instructs(last.line) and text.split() == [CANCEL]):
            return
        del self.last[chat]
        if is_correction(text) and (now - last.at).total_seconds() <= ROUTE_CORRECTION_SECONDS:
            self.routes.apply(self.corrected(last, text), now)
        elif self.quiet(last.line) or instructs(last.line):
            self.routes.apply(self.agreed(last), now)

    def corrected(self, last, text):
        line = slotted(text, last.values)
        added = [{"op": "add", "pattern": last.pattern, "line": line, "source": "correction"}]
        return self.retired(last, text) + (added if text.startswith("/") else [])

    @staticmethod
    def retired(last, why):
        return [{"op": "retire", "id": last.route_id, "why": why}] if last.route_id else []

    def agreed(self, last):
        if last.confirmed:
            return []
        if last.route_id:
            return [{"op": "confirm", "id": last.route_id}]
        return [{"op": "add", "pattern": last.pattern, "line": last.line, "source": "model"}]


def is_correction(text):
    words = text.lower().split()
    first = words[0].strip(TRAILING_PUNCTUATION) if words else ""
    return text.startswith("/") or first == "no" or words[:2] == ["i", "meant"]


def instructs(line):
    return line.startswith(INSTRUCT)


def show_routes():
    living = sorted(Routes().living(), key=lambda route: route["status"] != "confirmed")
    if not living:
        say.info("No routes yet: Tinker learns them when you agree to what it proposes.", "📭")
        return
    with say.part("Routes", "🧭"):
        for route in living:
            say.item(route_view_line(route))
    say.info("tinker memory forget rN retires one.", "👉")


def route_view_line(route):
    return f"{route['id']} {route['pattern']} → {route['line']} · {route['status']} · {counted(route['uses'], 'use')}"


def forget_route(route_id):
    if not Routes().open(route_id):
        raise SystemExit(
            f"tinker: no route {route_id} to forget: tinker memory routes lists the routes by id."
        )
    Routes().apply([{"op": "retire", "id": route_id, "why": "forgotten"}])
    say.done(f"Forgot {route_id}.")
