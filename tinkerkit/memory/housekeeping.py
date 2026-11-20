import json
from typing import NamedTuple

from ..settings import RETIRED_KEPT_BYTES, Home
from .memories import Memories, Traits
from .routes import Routes


class Compacted(NamedTuple):
    kind: str
    id: str
    change: str


class RetiredKind:
    name = ""
    store = ""
    emptied = {}

    def retired(self):
        return [item for item in Home.store().items(self.store).values() if self.is_retired(item)]

    def is_retired(self, item):
        return item["status"] == "retired"

    def stub(self, item):
        return dict(item, **self.emptied, stub=True, at=self.at(item))

    def at(self, item):
        return item.get("at") or item["history"][-1]["at"]


class RetiredMemories(RetiredKind):
    name = "memory"
    store = Memories.MEMORIES
    emptied = {"text": "", "sources": []}

    def is_retired(self, item):
        return bool(item["retired"])

    def at(self, item):
        return item.get("at") or item["last_seen"]


class RetiredTraits(RetiredKind):
    name = "trait"
    store = Traits.TRAITS
    emptied = {"sources": [], "history": []}


class RetiredRoutes(RetiredKind):
    name = "route"
    store = Routes.ROUTES
    emptied = {"history": []}


KINDS = (RetiredMemories(), RetiredTraits(), RetiredRoutes())


class Retired(NamedTuple):
    kind: RetiredKind
    item: dict

    @property
    def at(self):
        return self.kind.at(self.item)

    @property
    def size(self):
        return len(json.dumps(self.item))

    def compacted(self, change):
        return Compacted(self.kind.name, self.item["id"], change)


class Housekeeping:
    def compact(self):
        retired = sorted(
            (Retired(kind, item) for kind in KINDS for item in kind.retired()), key=lambda one: one.at
        )
        size = sum(one.size for one in retired)
        stubbed, size = self.stubbed(retired, size)
        return stubbed + self.deleted(retired, size)

    def stubbed(self, retired, size):
        changes = []
        for index, one in enumerate(retired):
            if size <= RETIRED_KEPT_BYTES:
                break
            if one.item.get("stub"):
                continue
            stub = Retired(one.kind, one.kind.stub(one.item))
            Home.store().update(one.kind.store, one.item["id"], lambda item, stub=stub: stub.item)
            retired[index] = stub
            size += stub.size - one.size
            changes.append(one.compacted("stub"))
        return changes, size

    def deleted(self, retired, size):
        changes = []
        for one in retired:
            if size <= RETIRED_KEPT_BYTES:
                break
            Home.store().delete(one.kind.store, one.item["id"])
            size -= one.size
            changes.append(one.compacted("delete"))
        return changes
