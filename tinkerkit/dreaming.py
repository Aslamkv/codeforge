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
from .remembering import (
    DREAM,
    Memories,
    Questions,
    Scratchpad,
    Traits,
    counted,
    lasts_found,
    likeness_text,
    recall_text,
    stamp,
)
from .retrieval import Embeddings
from .settings import (
    ANSWER_LASTS_DAYS,
    DREAM_BATCH_EPISODES,
    DREAM_FORGET_DAYS,
    DREAM_KEEP_STRENGTH,
    DREAM_REPLY_TOKENS,
    QUESTIONS_WAITING,
    SIMILAR_LESSON_MEANING,
    SIMILAR_LESSON_WORDS,
    Home,
)
from .store import Log
from .text import similarity

SHOWN_MEMORY_KEYS = ("id", "kind", "gist", "text", "strength", "recalled")
TRAIT_KEYS = ("id", "about", "when", "do", "status")
TRAIT_ABOUTS = ("person", "tinker")


class Dream:
    STATE = "state"
    EVENTS = "dream events"
    STEPS = ("consolidate", "revise", "decay", "traits", "merge_traits", "gists", "questions")

    def __init__(self, retriever, phase=lambda step: None):
        self.memories = Memories(retriever)
        self.phase = phase
        self.events = Log(Home.store(), self.EVENTS)
        self.changed = []
        self.count = 0
        self.state = new_state()

    def run(self, now=None):
        if not Models.quick():
            return
        state = self.state = new_state() | (Home.store().get(DREAM, self.STATE) or {})
        for step in self.STEPS:
            if step in state["done"]:
                continue
            self.phase(step)
            self.changed = []
            self.count = 0
            getattr(self, step)(now)
            state["done"].append(step)
            state["changed"] += [memory_id for memory_id in self.changed if memory_id not in state["changed"]]
            state["counts"][step] = self.count
            Home.store().put(DREAM, self.STATE, state)
        Home.store().put(DREAM, self.STATE, dict(state, done=[], changed=[], dreamt=[], last=stamp(now)))

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
        reply = asked(Prompts.DREAM_CONSOLIDATE_SYSTEM, self.batch_text(batch))
        if not isinstance(reply, dict):
            return
        ids = {episode["id"] for episode in batch}
        for name, change in CHANGE_OF.items():
            for item in listed(reply.get(name)):
                if cited(item.get("sources"), ids) and not answer_touched(name, item):
                    self.logged("consolidate", name, change(self.memories).made(item, item["sources"], now))
        self.answers(batch, reply, now)
        Scratchpad.dreamt(ids, now, self.episode_gists(reply, ids))
        self.state["dreamt"] += [episode["id"] for episode in batch]

    def answers(self, batch, reply, now):
        questions = {episode["id"]: episode for episode in answered_in_words(batch)}
        for episode_id, lasts in mapped(reply.get("lasts")).items():
            episode = questions.get(episode_id)
            if episode and lasts_found("answer", lasts):
                self.logged("consolidate", *self.answered(episode, lasts, now))

    def answered(self, episode, lasts, now):
        same = self.memories.same_answer(episode["message"])
        if not same:
            return "add", self.memories.add(
                "answer", episode["message"], episode["reply"], [episode["id"]], now, lasts
            )
        return "update", self.memories.update(
            same["id"],
            {
                "text": episode["reply"],
                "lasts": lasts,
                "sources": merged(same["sources"], [episode["id"]]),
                "last_seen": stamp(now),
            },
        )

    def episode_gists(self, reply, ids):
        return {
            episode_id: gist
            for episode_id, gist in mapped(reply.get("gists")).items()
            if episode_id in ids and isinstance(gist, str) and gist
        }

    def batch_text(self, batch):
        living = self.memories.living()
        related = self.memories.hits(
            " ".join(recall_text(episode) for episode in batch), [recall_text(memory) for memory in living]
        )
        return json.dumps(
            {
                "episodes": batch,
                "questions": [episode["id"] for episode in answered_in_words(batch)],
                "memories": shown([living[index] for index in related]),
            },
            ensure_ascii=False,
        )

    def revise(self, now=None):
        changed = self.changed_in_dream()
        revisable = [memory for memory in self.memories.living() if memory["kind"] != "answer"]
        for group in self.similar_groups(revisable, [recall_text(memory) for memory in revisable]):
            if len(group) > 1 and any(memory["id"] in changed for memory in group):
                self.revised(group, now)

    def changed_in_dream(self):
        return set(self.state["changed"]) | set(self.changed)

    def similar_groups(self, living, texts):
        alike = self.alike(texts)
        group_of = {index: {index} for index in range(len(living))}
        for first, second in itertools.combinations(range(len(living)), 2):
            if group_of[first] is not group_of[second] and alike(first, second):
                joined = group_of[first] | group_of[second]
                for index in joined:
                    group_of[index] = joined
        groups = {id(group): group for group in group_of.values()}.values()
        return [[living[index] for index in sorted(group)] for group in groups]

    def alike(self, texts):
        embeddings = self.memories.retriever.embeddings
        vectors = embeddings.vectors(texts, keep=False) if embeddings and texts else None
        if vectors:
            return lambda first, second: (
                Embeddings.cosine(vectors[first], vectors[second]) >= SIMILAR_LESSON_MEANING
            )
        return lambda first, second: similarity(texts[first], texts[second]) >= SIMILAR_LESSON_WORDS

    def revised(self, group, now):
        reply = asked(Prompts.DREAM_REVISE_SYSTEM, revise_text(group))
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
        if not lasts_found(kind, item.get("lasts")):
            return None
        sources = list(dict.fromkeys(source for memory in merged for source in memory["sources"]))
        memory = self.memories.add(kind, gist, text, sources, now, item.get("lasts"))
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
            elif expired(memory, now):
                self.logged("decay", "retire", self.memories.retire(memory["id"], "expired"))
        Scratchpad.prune(now)

    def traits(self, now=None):
        episodes, people = self.dreamt_episodes(), self.changed_people()
        if not episodes and not people:
            return
        living = Traits().living()
        reply = asked(Prompts.DREAM_TRAITS_SYSTEM, traits_text(episodes, people, living))
        if not isinstance(reply, dict):
            return
        given = {item["id"] for item in episodes + people + living}
        for item in listed(reply.get("add")):
            self.trait_added(item, given, now)
        for item in listed(reply.get("support")):
            self.trait_supported(item, given, now)

    def dreamt_episodes(self):
        kept = (Home.store().get(Scratchpad.EPISODES, episode_id) for episode_id in self.state["dreamt"])
        return [episode for episode in kept if episode]

    def changed_people(self):
        changed = (Home.store().get(Memories.MEMORIES, memory_id) for memory_id in self.changed_in_dream())
        return sorted(
            (memory for memory in changed if memory and memory["kind"] == "person" and not memory["retired"]),
            key=lambda memory: int(memory["id"][1:]),
        )

    def trait_added(self, item, given, now):
        if item.get("about") not in TRAIT_ABOUTS or not (isinstance(item.get("do"), str) and item["do"]):
            return
        when = item.get("when") if isinstance(item.get("when"), str) else ""
        sources = cited_only(item.get("sources"), given)
        delta = {"op": "add", "about": item["about"], "when": when, "do": item["do"], "sources": sources}
        for trait in Traits().apply([delta], now):
            if newly_added(trait):
                self.trait_logged("traits", "add", trait)
                self.confirm_asked(item, trait, sources, now)
            else:
                self.trait_logged("traits", "support", trait)

    def confirm_asked(self, item, trait, sources, now):
        question = item.get("question")
        if isinstance(question, str) and question:
            Questions().add("confirm", question, suggestions_of(item), [trait["id"]], sources, now)

    def trait_supported(self, item, given, now):
        if item.get("id") not in given:
            return
        delta = {"op": "support", "id": item["id"], "sources": cited_only(item.get("sources"), given)}
        for trait in Traits().apply([delta], now):
            self.trait_logged("traits", "support", trait)

    def trait_logged(self, step, name, trait):
        self.recorded(step, name, dict(trait, gist=trait["gist"] or trait["do"]))

    def merge_traits(self, now=None):
        living = Traits().living()
        texts = [likeness_text(trait["when"], trait["do"]) for trait in living]
        for group in self.similar_groups(living, texts):
            if len(group) > 1 and any(changed_since(trait, self.state["last"]) for trait in group):
                self.traits_merged(group, now)

    def traits_merged(self, group, now):
        reply = asked(Prompts.DREAM_MERGE_TRAITS_SYSTEM, merge_traits_text(group))
        if not isinstance(reply, dict):
            return
        by_id = {trait["id"]: trait for trait in group}
        for item in listed(reply.get("merge")):
            ids = group_ids(item.get("ids"), by_id)
            if len(ids) > 1 and isinstance(item.get("do"), str) and item["do"]:
                when = item.get("when") if isinstance(item.get("when"), str) else ""
                delta = {"op": "merge", "ids": ids, "when": when, "do": item["do"]}
                for trait in Traits().apply([delta], now):
                    self.trait_logged("merge_traits", "merge", trait)
                    for trait_id in ids:
                        self.trait_logged("merge_traits", "retire", by_id.pop(trait_id))

    def gists(self, now=None):
        ungisted = {trait["id"]: trait for trait in Traits().confirmed() if not trait["gist"]}
        if not ungisted:
            return
        reply = asked(Prompts.DREAM_GISTS_SYSTEM, gists_text(ungisted.values()))
        if not isinstance(reply, dict):
            return
        for trait_id, gist in mapped(reply.get("gists")).items():
            trait = ungisted.get(trait_id)
            if trait and isinstance(gist, str) and gist and len(gist) < len(trait["do"]):
                self.logged("gists", "gist", Traits().gisted(trait_id, gist))

    def questions(self, now=None):
        while self.questions_wanted() and self.questions_asked(now):
            pass

    def questions_wanted(self):
        return len(Questions().pending()) < QUESTIONS_WAITING

    def questions_asked(self, now):
        content = questions_text(Traits().confirmed(), Questions().all())
        reply = asked(Prompts.DREAM_QUESTIONS_SYSTEM, content)
        if not isinstance(reply, dict):
            return False
        kept = [self.question_kept(item, now) for item in listed(reply.get("questions"))]
        return any(kept)

    def question_kept(self, item, now):
        text = item.get("text")
        if not (isinstance(text, str) and text):
            return None
        question = Questions().add("open", text, suggestions_of(item), [], [], now)
        if question:
            self.logged("questions", "question", dict(question, gist=text))
        return question

    def logged(self, step, name, memory):
        if memory:
            self.changed.append(memory["id"])
            self.recorded(step, name, memory)

    def recorded(self, step, name, item):
        self.count += 1
        self.events.add({"step": step, "change": name, "id": item["id"], "gist": item["gist"]})


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
        if (
            kind not in Memories.KINDS
            or not tool_found(kind, item)
            or not lasts_found(kind, item.get("lasts"))
        ):
            return None
        return self.memories.add(kind, gist, text, sources, now, item.get("lasts"))


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
    return {"done": [], "changed": [], "dreamt": [], "counts": {}, "last": ""}


def traits_text(episodes, people, living):
    return json.dumps(
        {
            "episodes": [{"id": episode["id"], "gist": episode.get("gist", "")} for episode in episodes],
            "memories": shown(people),
            "traits": [{key: trait[key] for key in TRAIT_KEYS} for trait in living],
        },
        ensure_ascii=False,
    )


def merge_traits_text(group):
    return json.dumps(
        [
            {
                "id": trait["id"],
                "about": trait["about"],
                "when": trait["when"],
                "do": trait["do"],
                "changed": last_changed(trait),
            }
            for trait in sorted(group, key=last_changed, reverse=True)
        ],
        ensure_ascii=False,
    )


def changed_since(trait, last):
    return last_changed(trait) > last


def last_changed(trait):
    return max(entry["at"] for entry in trait["history"])


def gists_text(traits):
    return json.dumps(
        [
            {"id": trait["id"], "do": trait["do"]}
            for trait in sorted(traits, key=lambda trait: int(trait["id"][1:]))
        ],
        ensure_ascii=False,
    )


def questions_text(traits, kept):
    return json.dumps(
        {
            "traits": [
                {
                    "id": trait["id"],
                    "about": trait["about"],
                    "when": trait["when"],
                    "do": trait["gist"] or trait["do"],
                }
                for trait in sorted(traits, key=lambda trait: int(trait["id"][1:]))
            ],
            "questions": [
                question["text"] for question in sorted(kept, key=lambda question: int(question["id"][1:]))
            ],
        },
        ensure_ascii=False,
    )


def newly_added(trait):
    return len(trait["history"]) == 1 and trait["history"][0]["event"] == "add"


def cited_only(sources, given):
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, str) and source in given]


def suggestions_of(item):
    suggestions = item.get("suggestions")
    if not isinstance(suggestions, list) or not all(isinstance(text, str) and text for text in suggestions):
        return []
    return suggestions if 2 <= len(suggestions) <= 4 else []


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


def expired(memory, now):
    days = ANSWER_LASTS_DAYS.get(memory.get("lasts")) if memory["kind"] == "answer" else None
    if days is None:
        return False
    return datetime.datetime.fromisoformat(memory["last_seen"]) + datetime.timedelta(days=days) < now


def answer_touched(name, item):
    if name == "add":
        return item.get("kind") == "answer"
    if name in ("update", "retire") and "id" in item:
        target = Home.store().get(Memories.MEMORIES, item["id"])
        return bool(target) and target["kind"] == "answer"
    return False


def answered_in_words(batch):
    return [
        episode
        for episode in batch
        if "message" in episode
        and "reply" in episode
        and "commands" not in episode
        and episode.get("status") != "remembered"
    ]


def cited(sources, ids):
    if not isinstance(sources, list) or not sources:
        return False
    return all(isinstance(source, str) and source in ids for source in sources)


def tool_found(kind, item):
    if kind != "tool":
        return True
    tool = item.get("tool")
    return isinstance(tool, str) and bool(tool) and bool(shutil.which(tool))


def asked(system, content):
    if Models.quick() is None:
        return None
    try:
        text = Models.quick_reply(
            [{"role": "system", "content": system}, {"role": "user", "content": content}],
            DREAM_REPLY_TOKENS,
            "off",
            temperature=0,
        )
        return reply_read(text, system)
    except OllamaError, ValueError:
        return None


def reply_read(text, system):
    return Reply(text).json(system)


def listed(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def mapped(value):
    return value if isinstance(value, dict) else {}


CHANGE_OF = {"add": Add, "update": Update, "confirm": Confirm, "retire": Retire}


def show_dream():
    state = Home.store().get(DREAM, Dream.STATE) or new_state()
    with say.part("Dream", "💤"):
        say.item(last_dream(state))
        say.item(f"{waiting(len(Scratchpad.fresh()), 'fresh episode')} for a dream")
        say.item(f"{waiting(len(Questions().pending()), 'question')} for tinker interact")
        say.item(dream_job())


def waiting(number, one):
    return f"{counted(number, one)} {'waits' if number == 1 else 'wait'}"


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
