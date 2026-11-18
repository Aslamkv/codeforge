import contextlib
import os
import re
import signal
lazy import threading
lazy import time
lazy from pathlib import Path

from ..build.project import Tracker
from ..commands import command_of
from ..formats import IMAGE_TYPES, sniffed
from ..jobs import Jobs, alive
from .markup import Markup, md
lazy from ..messages import human_duration, star_at, tinkering
lazy from ..settings import JOB_STOP_SIGNAL, Home

PROGRESS_EDIT_SECONDS = 2
CLOCK_ONLY_EDIT_SECONDS = 15


class Reporter:
    def __init__(
        self,
        audience,
        title,
        pid=None,
        sent=None,
        phase=Tracker.STARTING,
        plan_on_top="",
        before=0.0,
        on_step=None,
    ):
        self.audience, self.progress, self.pid = audience, Progress(title, phase, before), pid
        self.on_step = on_step
        self.plan_on_top = plan_on_top
        self.sent, self.last_edit, self.lock = ({} if sent is None else sent), 0.0, threading.Lock()
        self.ended = False
        self.send_at_bottom = True
        threading.Thread(target=self.keep_up, daemon=True).start()

    def keep_up(self):
        while not self.ended:
            time.sleep(PROGRESS_EDIT_SECONDS)
            self.refresh()

    def refresh(self):
        with self.lock:
            if self.ended:
                return
            state = self.tracked()
            went_on = ((state or {}).get("resumed") or 0) > (self.progress.snapshot.get("resumed") or 0)
            self.send_at_bottom = self.send_at_bottom or went_on
            changed = self.progress.take(state)
            if changed or went_on or time.monotonic() - self.last_edit >= CLOCK_ONLY_EDIT_SECONDS:
                self.update()
            else:
                self.count_live()

    def tracked(self):
        job = Jobs.of_process(self.pid) if self.pid else None
        return (job or {}).get("progress")

    def update(self):
        self.last_edit = time.monotonic()
        self.progress.turn()
        self.say(self.progress.text())
        if self.on_step:
            self.on_step()

    def count_live(self):
        for channel, chat in self.audience:
            channel.count_live(chat, self.sent.get((channel.name, chat)), **self.progress.facts)

    def say(self, text, done=False):
        text = f"{self.plan_on_top}\n\n{text}" if self.plan_on_top else text
        bottom, self.send_at_bottom = done or self.send_at_bottom, False
        for channel, chat in self.audience:
            key = (channel.name, chat)
            self.sent[key] = channel.show(
                chat,
                self.sent.get(key),
                text,
                done,
                bottom,
                began=self.progress.work_began,
                doing=self.progress.working_on,
                **self.progress.facts,
            )

    def printed(self, line):
        if self.tracked() is None and self.says_something(line):
            with self.lock:
                self.progress.snapshot["latest"] = self.joined(self.progress.snapshot.get("latest"), line)

    @staticmethod
    def joined(latest, line):
        if latest and line[:1].isspace():
            return f"{latest}\n{line.rstrip()[:300]}"
        return line.strip()[:300]

    @staticmethod
    def says_something(line):
        return line.strip() and not line.lstrip().startswith("...")

    def finish(self, snapshot=None):
        with self.lock:
            self.ended = True
            self.progress.take(snapshot or self.tracked())
            self.say(self.progress.ending(), done=True)
            self.deliver((self.progress.snapshot.get("ended") or {}).get("files", []))

    def deliver(self, files):
        for path in map(Path, files):
            for channel, chat in self.audience:
                send = channel.send_photo if Reporter.is_image(path) else channel.send_file
                send(chat, str(path), path.name)

    @staticmethod
    def is_image(path):
        with contextlib.suppress(OSError), path.open("rb") as file:
            return bool(sniffed(file.read(64), IMAGE_TYPES))
        return False


class Progress:
    OWN_PHASE_ICONS = ("✅", "✏️", "📁")
    CHANGE_SIGNS = {"+": "➕", "~": "🔄"}

    def __init__(self, title, phase=Tracker.STARTING, before=0.0):
        self.snapshot = {
            "title": title,
            "doing": phase,
            "step": None,
            "began": None,
            "steps": [],
            "passing": None,
            "working": "",
            "tally": "",
            "found": "",
            "latest": "",
            "waiting": None,
            "ended": None,
            "before": before,
            "seconds": 0.0,
        }
        self.turns = 0

    def turn(self):
        self.turns += 1

    def take(self, snapshot):
        before = self.text(counted=False)
        said = self.snapshot["doing"] if self.snapshot.get("step") is None else None
        self.snapshot = dict(
            self.snapshot, **{key: value for key, value in (snapshot or {}).items() if key != "before"}
        )
        if said and self.snapshot.get("step") is None:
            self.snapshot["doing"] = said
        return self.text(counted=False) != before

    def text(self, counted=True):
        state = self.snapshot
        doing = (
            self.working_on
            if self.working_on.startswith(self.OWN_PHASE_ICONS)
            else md(t"**{self.working_on}**")
        )
        job = f" (Job #{state['job']})" if state.get("job") else ""
        lines = [md(t"🔨 **{state['title']}**{job}")] + [f"✅ {step}" for step in state.get("steps", [])]
        lines += [f"{star_at(self.turns)} {self.tinkering_line(doing, counted)}"]
        lines += [md(t"📊 {self.how_far}")] if self.how_far else []
        if state.get("found"):
            lines += [md(t"💡 {state['found']}")]
        elif state.get("latest"):
            lines += [md(t"… {state['latest']}")]
        return "\n".join(lines)

    def tinkering_line(self, doing, counted):
        if not counted:
            return tinkering(None, doing, **dict(self.facts, tokens=0))
        began = self.work_began
        return tinkering(time.time() - began if began else None, doing, **self.facts)

    @property
    def facts(self):
        state = self.snapshot
        return {
            "tokens": state.get("tokens", 0),
            "thinking": state.get("thinking", False),
            "thought_seconds": state.get("thought_seconds"),
        }

    @property
    def title(self):
        return self.snapshot["title"]

    @property
    def work_began(self):
        started = self.snapshot.get("started")
        return started - self.snapshot.get("before", 0.0) if started else None

    @property
    def working_on(self):
        state = self.snapshot
        if not state.get("working"):
            return state["doing"]
        return f"{Tracker.icon(state.get('step'))} {state['working']}".strip()

    @property
    def passing(self):
        counts = self.snapshot.get("passing")
        return f"{counts[0]}/{counts[1]} spec points pass" if counts else ""

    @property
    def how_far(self):
        return " · ".join(part for part in (self.passing, self.snapshot.get("tally")) if part)

    def brief(self):
        got = f", {self.how_far}" if self.how_far else ""
        return md(t"**{self.title}** ({Markup.plain(self.working_on):md}{got})")

    def waiting(self):
        return bool(self.snapshot.get("waiting"))

    def ending(self):
        if self.waiting():
            return self.waits(self.planned(self.snapshot["waiting"]), self.time_taken())
        ended = self.snapshot.get("ended")
        lines = self.ended_lines(ended) if ended else self.last_state_lines()
        return "\n".join([md(t"🏁 **{self.title}**")] + lines)

    def last_state_lines(self):
        state = self.snapshot
        lines = [f"✅ {step}" for step in state.get("steps", [])]
        lines += [md(t"⏸ {self.working_on}")] if state.get("step") else []
        return lines + ([md(t"{state['latest']}")] if state.get("latest") else [])

    def ended_lines(self, ended):
        mark = {"done": "✅", "blocked": "⚠️"}.get(ended.get("status"), "⏸")
        took = (
            f"⏱ **Done in {self.time_taken()}**"
            if ended.get("status") == "done"
            else f"⏱ _so far: {self.time_taken()}_"
        )
        lines = ([md(t"{mark} **{ended['headline']}**")] if ended.get("headline") else []) + [took]
        lines += [self.detail(label, value) for label, value in ended.get("details", [])]
        lines += (
            [" · ".join(f"{label}: `{command}`" for label, command in ended.get("next", []))]
            if ended.get("next")
            else []
        )
        return lines + ([ended["text"]] if ended.get("text") else [])

    @staticmethod
    def detail(label, value):
        if label == "Run it":
            return md(t"▶️ {label}: {value:code}")
        return md(t"• {label}: {value}")

    def time_taken(self):
        seconds = self.snapshot.get("before", 0.0) + self.snapshot.get("seconds", 0.0)
        return f"{human_duration(seconds)} ({round(seconds)} seconds)"

    @staticmethod
    def waits(planned, so_far):
        return f"{Progress.with_next_step(planned)}\n⏱ _so far: {so_far}_"

    @staticmethod
    def plan(plan):
        return Progress.with_next_step(Progress.planned(plan))

    @staticmethod
    def with_next_step(planned):
        return f"{planned}\n\n👉 `/confirm` to build it · `/amend WHAT` to change it"

    @staticmethod
    def planned(plan):
        heading = "🐞 **Bug shown for" if plan.get("kind") == "fix" else "📋 **Plan for"
        start = f" · ▶️ `{plan['start']}`" if plan.get("start") else ""
        lines = [
            md(t"{heading:md} {plan.get('name', '')}** · run {plan.get('run', ''):code}"),
            md(t"🧰 {plan.get('language') or '?'}{start:md}"),
        ]
        if plan.get("changes"):
            lines += ["", "✏️ **What changed**"] + [Progress.change_line(change) for change in plan["changes"]]
        lines += ["", "🧪 **Tested before any code is written**"]
        lines += [md(t"**{criterion_id}** {text}") for criterion_id, text in plan.get("criteria", [])]
        if plan.get("non_goals"):
            lines += ["", md(t"🚫 _Not included:_ {'; '.join(plan['non_goals'])}")]
        if plan.get("folder"):
            lines += [f"📁 `{Path(plan['folder']).name}`"]
        return "\n".join(lines)

    @staticmethod
    def change_line(change):
        if change.startswith("- "):
            return md(t"➖ ~~{change[2:]}~~")
        return md(t"{Progress.CHANGE_SIGNS.get(change[:1], '•')} {change[2:]}")


class Attached:
    def __init__(self, tracker):
        self.tracker = tracker

    def start(self):
        signal.signal(JOB_STOP_SIGNAL, self.stop)
        self.tracker.change("attached", reports=True)
        return self

    def stop(self, signum, frame):
        raise KeyboardInterrupt


def attach(tracker):
    return Attached(tracker).start()


def exercising_now():
    jobs = [job for job in Jobs.listed("running") if job["command"] == "exercise" and job.get("progress")]
    return described(tracker_of(jobs[0])) if jobs else None


def tracker_of(job):
    return dict(job["progress"], pid=job["pid"])


def exercise_switch(text):
    words = text.split()
    return command_of(text) == "exercise" and len(words) > 1 and words[1].lower() != "status"


def headline(status):
    return re.split(r":| \(", (status or "").split("\n")[0])[0]


def exercise_board():
    kept = Home.store().get("activity", "exercise message") or {}
    return {tuple(key.split("\t", 1)): ids for key, ids in kept.items()}


def keep_exercise_board(sent):
    Home.store().put(
        "activity", "exercise message", {f"{name}\t{chat}": ids for (name, chat), ids in sent.items()}
    )


def printed_since(path, start):
    with open(path, encoding="utf-8", errors="replace") as file:
        file.seek(start)
        return "\n".join(file.read().strip().splitlines()[-10:])


def until_ended(pid):
    while alive(pid):
        time.sleep(PROGRESS_EDIT_SECONDS)


def described(state):
    progress = Progress(state.get("title") or "a task")
    progress.take(state)
    return {
        "title": progress.title,
        "brief": progress.brief(),
        "text": progress.text(),
        "pid": state.get("pid"),
        "reports": bool(state.get("reports")),
    }


def signal_task(pid, signum):
    try:
        os.kill(pid, signum)
    except ProcessLookupError, PermissionError:
        pass
