import datetime
import itertools
import json
import secrets
import shutil
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .graph import Graph, GraphPage
from .jobs import Jobs
from .messages import say
from .model import Models, OllamaError
from .prompts import Prompts, Reply
from .remembering import Memories, Portrait, Scratchpad, counted, recall_text, stamp
from .retrieval import Embeddings
from .settings import (
    DREAM_BATCH_EPISODES,
    DREAM_FORGET_DAYS,
    DREAM_KEEP_STRENGTH,
    DREAM_REPLY_TOKENS,
    PORTRAIT_WORDS,
    SIMILAR_LESSON_MEANING,
    SIMILAR_LESSON_WORDS,
    Home,
)
from .store import Log
from .text import similarity

SHOWN_MEMORY_KEYS = ("id", "kind", "gist", "text", "strength", "recalled")


class Dream:
    STATE = "state"
    EVENTS = "dream events"
    STEPS = ("consolidate", "revise", "decay", "portrait")

    def __init__(self, retriever, phase=lambda step: None):
        self.memories = Memories(retriever)
        self.phase = phase
        self.events = Log(Home.store(), self.EVENTS)
        self.changed = []
        self.state = new_state()

    def run(self, now=None):
        if not Models.quick():
            return
        state = self.state = Home.store().get(Portrait.DREAM, self.STATE) or new_state()
        for step in self.STEPS:
            if step in state["done"]:
                continue
            self.phase(step)
            self.changed = []
            getattr(self, step)(now)
            state["done"].append(step)
            state["changed"] += [memory_id for memory_id in self.changed if memory_id not in state["changed"]]
            state["counts"][step] = len(self.changed)
            Home.store().put(Portrait.DREAM, self.STATE, state)
        Home.store().put(Portrait.DREAM, self.STATE, dict(state, done=[], changed=[], last=stamp(now)))

    def consolidate(self, now=None):
        for batch in self.batches():
            self.consolidated(batch, now)

    def batches(self):
        by_chat = {}
        for episode in sorted(Scratchpad.fresh(), key=lambda episode: int(episode["id"][1:])):
            by_chat.setdefault(episode.get("chat", ""), []).append(episode)
        return [
            episodes[start : start + DREAM_BATCH_EPISODES]
            for episodes in by_chat.values()
            for start in range(0, len(episodes), DREAM_BATCH_EPISODES)
        ]

    def consolidated(self, batch, now):
        reply = self.asked(Prompts.DREAM_CONSOLIDATE_SYSTEM, self.batch_text(batch))
        if not isinstance(reply, dict):
            return
        ids = {episode["id"] for episode in batch}
        for name, change in CHANGE_OF.items():
            for item in listed(reply.get(name)):
                if cited(item.get("sources"), ids):
                    self.logged("consolidate", name, change(self.memories).made(item, item["sources"], now))
        Scratchpad.dreamt(ids, now)

    def batch_text(self, batch):
        living = self.memories.living()
        related = self.memories.hits(
            " ".join(recall_text(episode) for episode in batch), [recall_text(memory) for memory in living]
        )
        return json.dumps(
            {"episodes": batch, "memories": shown([living[index] for index in related])}, ensure_ascii=False
        )

    def revise(self, now=None):
        changed = self.changed_in_dream()
        for group in self.similar_groups(self.memories.living()):
            if len(group) > 1 and any(memory["id"] in changed for memory in group):
                self.revised(group, now)

    def changed_in_dream(self):
        return set(self.state["changed"]) | set(self.changed)

    def similar_groups(self, living):
        alike = self.alike(living)
        group_of = {index: {index} for index in range(len(living))}
        for first, second in itertools.combinations(range(len(living)), 2):
            if group_of[first] is not group_of[second] and alike(first, second):
                joined = group_of[first] | group_of[second]
                for index in joined:
                    group_of[index] = joined
        groups = {id(group): group for group in group_of.values()}.values()
        return [[living[index] for index in sorted(group)] for group in groups]

    def alike(self, living):
        texts = [recall_text(memory) for memory in living]
        embeddings = self.memories.retriever.embeddings
        vectors = embeddings.vectors(texts, keep=False) if embeddings and texts else None
        if vectors:
            return lambda first, second: (
                Embeddings.cosine(vectors[first], vectors[second]) >= SIMILAR_LESSON_MEANING
            )
        return lambda first, second: similarity(texts[first], texts[second]) >= SIMILAR_LESSON_WORDS

    def revised(self, group, now):
        reply = self.asked(Prompts.DREAM_REVISE_SYSTEM, revise_text(group))
        if not isinstance(reply, dict):
            return
        by_id = {memory["id"]: memory for memory in group}
        for item in listed(reply.get("merge")):
            ids = group_ids(item.get("ids"), by_id)
            if len(ids) > 1 and self.merged(item, [by_id[memory_id] for memory_id in ids], now):
                for memory_id in ids:
                    del by_id[memory_id]
        for item in listed(reply.get("retire")):
            if item.get("id") in by_id:
                retired = by_id.pop(item["id"])
                self.logged(
                    "revise", "retire", self.memories.retire(retired["id"], item.get("why") or "revised")
                )

    def merged(self, item, merged, now):
        kind, gist, text = item.get("kind"), item.get("gist"), item.get("text")
        if not all(isinstance(value, str) and value for value in (kind, gist, text)):
            return None
        sources = list(dict.fromkeys(source for memory in merged for source in memory["sources"]))
        memory = self.memories.add(kind, gist, text, sources, now)
        memory = self.memories.update(
            memory["id"],
            {
                "first_seen": min(old["first_seen"] for old in merged),
                "strength": sum(old["strength"] for old in merged),
            },
        )
        self.logged("revise", "merge", memory)
        for old in merged:
            self.logged("revise", "retire", self.memories.retire(old["id"], f"merged into {memory['id']}"))
        return memory

    def decay(self, now=None):
        now = now or datetime.datetime.now()
        oldest = now - datetime.timedelta(days=DREAM_FORGET_DAYS)
        for memory in self.memories.living():
            if faded(memory, oldest):
                self.logged("decay", "retire", self.memories.retire(memory["id"], "faded"))
        Scratchpad.prune(now)

    def portrait(self, now=None):
        changed = [Home.store().get(Memories.MEMORIES, memory_id) for memory_id in self.changed_in_dream()]
        if not any(memory and memory["kind"] == "person" for memory in changed):
            return
        people = [memory for memory in self.memories.living() if memory["kind"] == "person"]
        reply = self.replied(
            Prompts.DREAM_PORTRAIT_SYSTEM.format(words=PORTRAIT_WORDS),
            json.dumps(shown(people), ensure_ascii=False),
        )
        if reply is not None and reply.text.split():
            Portrait.write(reply.text)
            self.logged("portrait", "portrait", {"id": Portrait.KEY, "gist": Portrait.read()})

    def asked(self, system, content):
        reply = self.replied(system, content)
        if reply is None:
            return None
        try:
            return reply.json()
        except ValueError:
            return None

    def replied(self, system, content):
        try:
            return Reply(
                Models.quick_reply(
                    [{"role": "system", "content": system}, {"role": "user", "content": content}],
                    DREAM_REPLY_TOKENS,
                    "off",
                    temperature=0,
                )
            )
        except OllamaError:
            return None

    def logged(self, step, name, memory):
        if memory:
            self.changed.append(memory["id"])
            self.events.add({"step": step, "change": name, "id": memory["id"], "gist": memory["gist"]})


class MemoryGraph(Graph):
    EPISODE_LABELS = ("message", "task", "request", "line")

    def __init__(self):
        super().__init__()
        self.similar_by = None

    @classmethod
    def build(cls, embeddings=None):
        graph = cls()
        graph.add_episodes()
        graph.add_memories()
        return graph

    def add_episodes(self):
        for episode_id, episode in Home.store().items(Scratchpad.EPISODES).items():
            label = next((episode[key] for key in self.EPISODE_LABELS if episode.get(key)), episode_id)
            details = [(key, value) for key, value in episode.items() if key != "id"]
            self.node(
                episode_id,
                "episode",
                str(label),
                json.dumps(episode, ensure_ascii=False),
                "",
                "episode",
                details,
            )

    def add_memories(self):
        memories = Home.store().items(Memories.MEMORIES)
        for memory_id, memory in memories.items():
            status = "retired" if memory["retired"] else ""
            details = [
                (key, memory[key]) for key in ("first_seen", "last_seen", "strength", "recalled", "retired")
            ]
            self.node(memory_id, "memory", memory["gist"], memory["text"], status, memory["kind"], details)
        for memory_id, memory in memories.items():
            for source in memory["sources"]:
                self.link(memory_id, source, "source")
            if memory["retired"].startswith("merged into "):
                self.link(memory_id, memory["retired"].removeprefix("merged into "), "merged into")

    def summary(self):
        counts = self.counts()
        shown = [
            counted(counts[kind], kind, many)
            for kind, many in (("episode", "episodes"), ("memory", "memories"))
            if counts.get(kind)
        ]
        if counts.get("retired"):
            shown.append(f"{counts['retired']} retired")
        return (", ".join(shown) or "nothing remembered yet") + f"; {len(self.links)} links"


class Change:
    def __init__(self, memories):
        self.memories = memories

    def made(self, item, sources, now):
        target = Home.store().get(Memories.MEMORIES, item.get("id")) if "id" in item else None
        if target and target["retired"]:
            target = None
        return self.applied(item, target, sources, now)

    def applied(self, item, target, sources, now):
        raise NotImplementedError


class Add(Change):
    def applied(self, item, target, sources, now):
        kind, gist, text = item.get("kind"), item.get("gist"), item.get("text")
        if not all(isinstance(value, str) and value for value in (kind, gist, text)):
            return None
        if kind not in Memories.KINDS or not tool_found(kind, item):
            return None
        return self.memories.add(kind, gist, text, sources, now)


class Update(Change):
    def applied(self, item, target, sources, now):
        if not target or not tool_found(target["kind"], item):
            return None
        return self.memories.update(
            target["id"],
            {
                "gist": item.get("gist") or target["gist"],
                "text": item.get("text") or target["text"],
                "sources": merged(target["sources"], sources),
            },
        )


class Confirm(Change):
    def applied(self, item, target, sources, now):
        if not target:
            return None
        return self.memories.update(
            target["id"],
            {
                "strength": target["strength"] + 1,
                "last_seen": stamp(now),
                "sources": merged(target["sources"], sources),
            },
        )


def merged(kept, sources):
    return kept + [source for source in sources if source not in kept]


class Retire(Change):
    def applied(self, item, target, sources, now):
        if not target:
            return None
        return self.memories.retire(target["id"], item.get("why") or "a dream found it no longer holds")


def new_state():
    return {"done": [], "changed": [], "counts": {}, "last": ""}


def revise_text(group):
    return json.dumps(
        [dict(shown_memory(memory), last_seen=memory["last_seen"]) for memory in group], ensure_ascii=False
    )


def shown(memories):
    return [shown_memory(memory) for memory in memories]


def shown_memory(memory):
    return {key: memory[key] for key in SHOWN_MEMORY_KEYS}


def group_ids(ids, by_id):
    if not isinstance(ids, list) or not all(
        isinstance(memory_id, str) and memory_id in by_id for memory_id in ids
    ):
        return []
    return list(dict.fromkeys(ids))


def faded(memory, oldest):
    newest = max(memory["last_seen"], memory["last_recalled"])
    return datetime.datetime.fromisoformat(newest) < oldest and memory["strength"] < DREAM_KEEP_STRENGTH


def cited(sources, ids):
    if not isinstance(sources, list) or not sources:
        return False
    return all(isinstance(source, str) and source in ids for source in sources)


def tool_found(kind, item):
    if kind != "tool":
        return True
    tool = item.get("tool")
    return isinstance(tool, str) and bool(tool) and bool(shutil.which(tool))


def listed(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


CHANGE_OF = {"add": Add, "update": Update, "confirm": Confirm, "retire": Retire}


def show_dream():
    state = Home.store().get(Portrait.DREAM, Dream.STATE) or new_state()
    with say.part("Dream", "💤"):
        say.item(last_dream(state))
        say.item(f"{counted(len(Scratchpad.fresh()), 'fresh episode')} wait for a dream")
        say.item(dream_job())


def last_dream(state):
    if not state["last"]:
        return "No dream yet"
    counts = " · ".join(f"{step} {state['counts'].get(step, 0)}" for step in Dream.STEPS)
    return f"Last dream {state['last']}: {counts}"


def dream_job():
    job = next(
        (job for job in Jobs.listed() if job["arguments"] == ["dream", "run"] and job["state"] != "stopped"),
        None,
    )
    if job is None:
        return "No dream queued: tinker dream run dreams now"
    return f"A dream is {job['state']} (job #{job['number']})"


def show_memory_graph():
    graph = MemoryGraph.build()
    Home.memory_page().write_text(GraphPage.html(graph))
    say.text(f"Tinker's episodes and memories: {graph.summary()}")
    say.text(f"Page: {Home.memory_page()}")
    if sys.stdout.isatty():
        webbrowser.open(Home.memory_page().as_uri())
        say.done("Opened in your browser.")


class LiveGraph:
    POLL_SECONDS = 1
    RELOADING = '<script>new EventSource("events").onmessage = () => location.reload()</script>'

    def __init__(self):
        self.token = secrets.token_urlsafe(16)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/{self.token}/"

    def handler(self):
        live = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                match self.path:
                    case path if path == f"/{live.token}/":
                        self.send_page()
                    case path if path == f"/{live.token}/events":
                        self.send_events()
                    case _:
                        self.send_error(403)

            def send_page(self):
                body = live.page().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_events(self):
                after = live.newest_event()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    live.stream(self.wfile, after)
                except BrokenPipeError, ConnectionResetError:
                    return

            def log_message(self, format, *args):
                pass

        return Handler

    def page(self):
        return GraphPage.html(MemoryGraph.build()).replace("</body>", f"{self.RELOADING}</body>", 1)

    @staticmethod
    def newest_event():
        return max((logged.id for logged in Home.store().entries(Dream.EVENTS)), default=0)

    def stream(self, out, after):
        while True:
            events = Home.store().entries(Dream.EVENTS, after)
            for logged in events:
                out.write(f"data: {logged.id}\n\n".encode())
                after = logged.id
            if not events:
                out.write(b": keepalive\n\n")
            out.flush()
            time.sleep(self.POLL_SECONDS)


def serve_dream_graph():
    live = LiveGraph()
    say.text(f"Tinker's episodes and memories, live: {live.url}")
    webbrowser.open(live.url)
    try:
        live.server.serve_forever()
    except KeyboardInterrupt:
        live.server.server_close()
        say.done("Stopped.")
