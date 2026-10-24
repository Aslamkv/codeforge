lazy import json
lazy import time

from .messages import human_duration, say
from .settings import (
    DEFAULT_MAX_STALL,
    ENV_EXAMPLE,
    ENV_FILE,
    NETWORK_RETRY_SECONDS,
    NETWORK_WAIT_NOTE_SECONDS,
    SETUP_TIMEOUT,
    fingerprint,
    first_line,
)
lazy from .knowledge import Knowledge, Library
lazy from .prompts import Part, Prompts, PromptTooLarge, Reply
lazy from .workspace import TaughtCommands, Workspace


class Session:
    def __init__(
        self, model, project, max_stall=DEFAULT_MAX_STALL, internet=None, confirm=True, tracker=None
    ):
        self.model, self.project, self.max_stall = model, project, max_stall
        self.internet = internet
        self.confirm = confirm
        self.tracker = tracker
        self.cycle = None
        self.workspace = Workspace(project.workdir)
        self.tool_problem = ""
        self.build_problem = ""
        self.unreproduced_reason = ""
        self.review_problem = ""
        self.waiting_for_network = []
        self.last_all_tests_run = None
        self.parked_at_start = {
            criterion["id"] for criterion in project.criteria if criterion.get("status") == "blocked"
        }
        self._knowledge = None

    @property
    def knowledge(self):
        request = (self.project.cycles[:1] or [{}])[0].get("request", "")
        language = self.project.language or Knowledge.named_in(request) or "Python"
        if self._knowledge is None or self._knowledge.language != language:
            self._knowledge = Knowledge(language, (self.project.run or {}).get("id"))
        return self._knowledge

    @property
    def toolchain(self):
        return self.project.toolchain

    def save(self):
        self.project.save()
        if self.tracker is not None:
            green, _, total = self.project.counts()
            self.tracker.points(green, total)

    def ask(self, system, parts, label, **kw):
        kw.setdefault("done_when", Reply.files_complete)
        return Reply(self.model.ask(system, parts, label, **kw))

    def ask_until_usable(self, system, parts, label, read, attempts=3, think=None, max_reply=None):
        try:
            result = self.model.ask_until_usable(system, parts, label, read, attempts, think, max_reply)
        except PromptTooLarge as exc:
            raise SystemExit(
                f"{label}: even shortened, the prompt does not fit {self.model.name}'s input budget "
                f"of {self.model.max_tokens} tokens ({exc}). Measure the model again (tinker calibrate) "
                f"or use one that reads longer prompts (tinker models)."
            )
        if result is None:
            raise SystemExit(
                f"Could not get a usable {label.lower()}. See the model calls: tinker log --calls"
            )
        return result

    def room(self, system):
        return int(self.model.limit_chars(system + Prompts.NO_TOOL_CALLS) * 0.95)

    def knowledge_parts(self, *phases):
        lessons = self.knowledge.lessons(phases)
        proven = [fact for fact in self.knowledge.facts(phases) if fact["id"] not in self.inapplicable()]
        learned = self.learned_for(phases, {fact["id"] for fact in proven})
        self.used(
            [f"lesson:{lesson['id']}" for lesson in lessons]
            + [f"known:{fact['id']}" for fact in proven]
            + [f"fact:{item['topic']}:{item['id']}" for item in learned if item.get("id")],
            phases,
        )
        facts = [f"- [{self.project.language or 'the language'}] {fact['text']}" for fact in proven] + [
            f"- [{item['name']}] {item['text']}" for item in learned
        ]
        parts = [
            Part(
                "Lessons from earlier projects (follow them)",
                "\n".join(f"- {lesson['text']}" for lesson in lessons),
                atomic=True,
                place="first",
            ),
            Part(
                "Learned from the web (current; newer than what you were trained on)",
                "\n".join(facts),
                place="first",
            ),
        ]
        return (
            parts
            + ([TaughtCommands.prompt_part()] if "toolchain" in phases else [])
            + self.checks_part(phases)
        )

    def checks_part(self, phases):
        lint = self.project.toolchain.lint if self.project.toolchain else ""
        if not lint or lint == "none" or not {"code", "tests"} & set(phases):
            return []
        return [Part("Checks the code and tests must pass (write them so these find nothing)", f"$ {lint}")]

    def learned_for(self, phases, proven):
        return [
            item
            for item in self.project.spec.get("learned", [])
            if Library.shown_in(item, phases) and item.get("id") not in proven | self.inapplicable()
        ]

    def inapplicable(self):
        return set(self.project.spec.get("inapplicable", []))

    def used(self, keys, phases):
        if self.cycle is None or not keys:
            return
        used = self.cycle.setdefault("used", {})
        for key in keys:
            used[key] = sorted(set(used.get(key, [])) | set(phases))

    def failed_phases(self, phase):
        if phase in ("setup", "final_review") or self.tool_problem:
            return ("toolchain", "config")
        if self.build_problem:
            return ("code", "config")
        if phase == "verify":
            return ("code", "tests", "design", "spec")
        return ()

    def note(self, kind, text):
        events = self.project.spec.setdefault("events", [])
        events.append({"kind": kind, "text": first_line(text, 300)})
        del events[:-30]

    def setup(self, force=False):
        self.workspace.prepare_env()
        if not force and self.setup_key() == self.project.spec.get("setup_key"):
            return True, ""
        ok, output, self.waiting_for_network = self.toolchain.run_setup(self.workspace)
        if ok and not self.waiting_for_network:
            self.setup_done()
        return ok, output

    def setup_key(self):
        configs = sorted(path for path in self.project.paths("config") if path not in (ENV_FILE, ENV_EXAMPLE))
        return fingerprint(
            json.dumps(self.toolchain.setup) + "".join(self.workspace.read(path) for path in configs)
        )

    def setup_done(self):
        self.project.spec["setup_key"] = self.setup_key()
        self.save()

    def wait_for_network(self):
        if not self.waiting_for_network:
            return
        began = time.monotonic()
        say.step(
            f"Waiting for the network: {len(self.waiting_for_network)} setup command(s), tried every "
            f"{NETWORK_RETRY_SECONDS}s (Ctrl+C stops; tinker resume goes on later)…",
            "📡",
        )
        if not self.retried_until_online(began):
            return
        say.done(f"The network is back after {human_duration(time.monotonic() - began)}: setup done")
        self.setup_done()

    def retried_until_online(self, began):
        noted = began
        while self.waiting_for_network:
            time.sleep(NETWORK_RETRY_SECONDS)
            ok, output, self.waiting_for_network = self.toolchain.run_setup(
                self.workspace, self.waiting_for_network
            )
            if not ok:
                return False
            if self.waiting_for_network and time.monotonic() - noted >= NETWORK_WAIT_NOTE_SECONDS:
                noted = time.monotonic()
                say.step(f"Still waiting for the network ({human_duration(noted - began)})…", "📡")
        return True

    def format_code(self):
        command = self.toolchain.format
        if not command:
            return
        ok, failed = self.setup()
        if not ok:
            return
        code, output = self.workspace.run(self.toolchain.shell(command), timeout=SETUP_TIMEOUT, quiet=True)
        if code == 0:
            say.done(f"Formatted the code with `{command}`")
        else:
            self.drop_formatter(command, code, output)

    def drop_formatter(self, command, code, output):
        say.warn(
            f"The formatter does not work ({first_line(output.strip(), 160) or f'exit code {code}'}); "
            f"the code stays as written and the project goes on without `{command}`"
        )
        self.project.spec["toolchain"] = dict(self.toolchain.to_dict(), format="")
        self.save()

    def run_all_tests(self):
        key = fingerprint("".join(path + self.workspace.read(path) for path in sorted(self.project.files)))
        if self.last_all_tests_run and self.last_all_tests_run[0] == key:
            return self.last_all_tests_run[1]
        result = self.toolchain.run_all(self.workspace)
        self.last_all_tests_run = (key, result)
        return result

    def files_on_disk(self, *kinds):
        return [path for path in self.project.paths(*kinds) if self.workspace.exists(path)]


class Worker:
    def __init__(self, session):
        self.session, self.project, self.workspace = session, session.project, session.workspace


class Phase(Worker):
    TITLE = ""
    BUILDS = False

    def run(self, cycle):
        raise NotImplementedError
