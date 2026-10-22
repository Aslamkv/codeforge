"""What every phase of a cycle works through: the Session (the model, the project and its workspace,
what Tinker knows, the run's options and its tracker), and the phase contract (Worker, Phase)."""

lazy import json
lazy import time

lazy from .knowledge import Knowledge, Library
lazy from .prompts import Part, Prompts, PromptTooLarge, Reply
from .settings import (
    DEFAULT_MAX_STALL, ENV_EXAMPLE, ENV_FILE, NETWORK_NOTE, NETWORK_RETRY, SETUP_TIMEOUT, fingerprint, first_line,
    human_duration,
)
lazy from .workspace import TaughtCommands, Workspace


class Session:
    """What the phases of a run share: the model, the project and its workspace, what earlier
    projects taught, the Internet plugin (with --web), the run's options and its tracker; and what they
    all do alike: ask the model (within its budgets), run setup and the tests, note the problems
    solved for the lessons, and record the knowledge a cycle used (to prove or discard it)."""

    def __init__(self, model, project, max_stall=DEFAULT_MAX_STALL, internet=None, confirm=True, tracker=None):
        self.model, self.project, self.max_stall = model, project, max_stall
        self.internet = internet  # the Internet plugin: a run with --web only
        self.confirm = confirm    # a plan waits for the person (--confirm)
        self.tracker = tracker    # the run's one report (Tracker), when it has one
        self.cycle = None         # the cycle being worked on (where the knowledge it uses is recorded)
        self.ws = Workspace(project.workdir)
        self.tool_problem = ""
        self.build_problem = ""
        self.unreproduced = ""  # tinker fix: why the bug could not be shown by a test
        self.review_problem = ""  # why the final review found the project not done
        self.network_waiting = []  # setup commands that failed because the network did: done when it is back
        self.all_tests_run = None  # (files key, (code, output)) of the last run of all tests
        # criteria parked when this session began: one retry each (a resume after a change)
        self.parked_at_start = {s["id"] for s in project.criteria if s.get("status") == "blocked"}
        self._knowledge = None
        if tracker is not None:
            model.watch = tracker.live

    @property
    def knowledge(self):
        """What was learned for the project's language (and any language): before the spec names it, the language
        the request names, else Python (the spec's own default)."""
        request = (self.project.cycles[:1] or [{}])[0].get("request", "")
        language = self.project.language or Knowledge.named_in(request) or "Python"
        if self._knowledge is None or self._knowledge.language != language:
            self._knowledge = Knowledge(language, (self.project.run or {}).get("id"))
        return self._knowledge

    @property
    def toolchain(self):
        return self.project.toolchain

    def save(self):
        """The project kept, and the tracker told how many spec points pass."""
        self.project.save()
        if self.tracker is not None:
            green, _, total = self.project.counts()
            self.tracker.points(green, total)

    def ask(self, system, parts, label, **kw):
        """A model call whose reply is files (FILE or LINES blocks), ended as soon as it is complete."""
        kw.setdefault("done_when", Reply.files_complete)
        return Reply(self.model.ask(system, parts, label, **kw))

    def ask_until_usable(self, system, parts, label, read, attempts=3, think=None, max_reply=None):
        """read() of the first usable JSON reply (Model.ask_until_usable); when none is, or the
        prompt cannot fit even shortened, the run stops here with why."""
        try:
            result = self.model.ask_until_usable(system, parts, label, read, attempts, think, max_reply)
        except PromptTooLarge as exc:
            raise SystemExit(f"{label}: even shortened, the prompt does not fit {self.model.name}'s input budget "
                             f"of {self.model.max_tokens} tokens ({exc}). Measure the model again (tinker calibrate) "
                             f"or use one that reads longer prompts (tinker models).")
        if result is None:
            raise SystemExit(f"Could not get a usable {label.lower()}. See the model calls: tinker log --calls")
        return result

    def room(self, system):
        """Chars the user message may take next to this system prompt, with a little slack (the
        chars-per-token estimate moves with every real count)."""
        return int(self.model.limit_chars(system + Prompts.NO_TOOL_CALLS) * 0.95)

    def knowledge_parts(self, *phases):
        """What is known for these phases, as optional parts (dropped first when the prompt is
        full): lessons from earlier projects, then the facts proven for the language, then the facts
        learned from the web about the topics the recall phase chose for this project (the most
        related to the request first), and for the toolchain the person's own commands (tinker learn
        NAME). What applies only to code the request does not need is left out, proven or not. Each lesson and fact shown is recorded in the cycle's "used", with these phases."""
        lessons = self.knowledge.lessons(phases)
        proven = [fact for fact in self.knowledge.facts(phases) if fact["id"] not in self.inapplicable()]
        learned = self.learned_for(phases, {fact["id"] for fact in proven})
        self.used([f"lesson:{lesson['id']}" for lesson in lessons] + [f"known:{fact['id']}" for fact in proven]
                  + [f"fact:{item['topic']}:{item['id']}" for item in learned if item.get("id")], phases)
        facts = [f"- [{self.project.language or 'the language'}] {fact['text']}" for fact in proven] + \
                [f"- [{item['name']}] {item['text']}" for item in learned]
        parts = [Part("Lessons from earlier projects (follow them)",
                      "\n".join(f"- {lesson['text']}" for lesson in lessons), atomic=True, place="first"),
                 Part("Learned from the web (current; newer than what you were trained on)", "\n".join(facts),
                      place="first")]
        return parts + ([TaughtCommands.prompt_part()] if "toolchain" in phases else []) + self.checks_part(phases)

    def checks_part(self, phases):
        """For a prompt that writes code or tests: the checks they must pass (the toolchain's lint), so they are
        written to pass them rather than tidied afterwards."""
        lint = self.project.toolchain.lint if self.project.toolchain else ""
        if not lint or lint == "none" or not {"code", "tests"} & set(phases):
            return []
        return [Part("Checks the code and tests must pass (write them so these find nothing)", f"$ {lint}")]

    def learned_for(self, phases, proven):
        """What the recall phase chose from the web for these phases, but the facts proven already."""
        return [item for item in self.project.spec.get("learned", [])
                if Library.shown_in(item, phases) and item.get("id") not in proven | self.inapplicable()]

    def inapplicable(self):
        """What the recall phase judged this project's request has no need of (Recaller.keep_out_inapplicable)."""
        return set(self.project.spec.get("inapplicable", []))

    def used(self, keys, phases):
        """Knowledge shown in prompts of these phases, recorded in the cycle being worked on."""
        if self.cycle is None or not keys:
            return
        used = self.cycle.setdefault("used", {})
        for key in keys:
            used[key] = sorted(set(used.get(key, [])) | set(phases))

    def failed_phases(self, phase):
        """The prompt phases whose knowledge a failure in this cycle phase disproves: the tools' for the
        tools, the code's for a project that does not build, the code's and the plan's for a criterion
        no fix made pass; none for a bug no test could show."""
        if phase in ("setup", "final_review") or self.tool_problem:
            return ("toolchain", "config")
        if self.build_problem:
            return ("code", "config")
        if phase == "verify":
            return ("code", "tests", "design", "spec")
        return ()

    def note(self, kind, text):
        """A problem this run solved, kept for the lessons learned when the cycle ends."""
        events = self.project.spec.setdefault("events", [])
        events.append({"kind": kind, "text": first_line(text, 300)})
        del events[:-30]

    def setup(self, force=False):
        """Run the setup commands when they or the config files changed; prepare .env."""
        self.ws.prepare_env()
        if not force and self.setup_key() == self.project.spec.get("setup_key"):
            return True, ""
        ok, output, self.network_waiting = self.toolchain.run_setup(self.ws)
        if ok and not self.network_waiting:
            self.setup_done()
        return ok, output

    def setup_key(self):
        """What setup depends on: its commands and the config files."""
        configs = sorted(p for p in self.project.paths("config") if p not in (ENV_FILE, ENV_EXAMPLE))
        return fingerprint(json.dumps(self.toolchain.setup) + "".join(self.ws.read(p) for p in configs))

    def setup_done(self):
        self.project.spec["setup_key"] = self.setup_key()
        self.save()

    def wait_for_network(self):
        """When all that needs no network is done and some setup still waits for it: that batch is
        tried again every NETWORK_RETRY seconds until the network is back, then the run goes on by
        itself (a person who will not wait stops it with Ctrl+C and runs tinker resume later)."""
        if not self.network_waiting:
            return
        began = time.monotonic()
        print(f"  waiting for the network: {len(self.network_waiting)} setup command(s), tried every "
              f"{NETWORK_RETRY}s (Ctrl+C stops; tinker resume goes on later)")
        if not self.retried_until_online(began):
            return
        print(f"  the network is back after {human_duration(time.monotonic() - began)}: setup done")
        self.setup_done()

    def retried_until_online(self, began):
        """The setup that waits tried again until it ran: True, or False once one reached the network and failed
        for a reason of its own (the checks that follow report it). A line says so every NETWORK_NOTE seconds."""
        noted = began
        while self.network_waiting:
            time.sleep(NETWORK_RETRY)
            ok, output, self.network_waiting = self.toolchain.run_setup(self.ws, self.network_waiting)
            if not ok:
                return False
            if self.network_waiting and time.monotonic() - noted >= NETWORK_NOTE:
                noted = time.monotonic()
                print(f"  still waiting for the network ({human_duration(noted - began)})")
        return True

    def format_code(self):
        """The toolchain's formatter run over the project, when it has one (after setup: it may be
        installed there): the model never formats code itself. Formatting only tidies: when it
        fails, the project goes on without it, and it is not remembered as a command that works."""
        command = self.toolchain.format
        if not command:
            return
        ok, failed = self.setup()
        if not ok:
            return
        code, output = self.ws.run(self.toolchain.shell(command), timeout=SETUP_TIMEOUT, quiet=True)
        if code == 0:
            print(f"  formatted the code: $ {command}")
        else:
            self.drop_formatter(command, code, output)

    def drop_formatter(self, command, code, output):
        print(f"  note: the formatter does not work ({first_line(output.strip(), 160) or f'exit code {code}'}); "
              f"the code stays as written and the project goes on without `{command}`")
        self.project.spec["toolchain"] = dict(self.toolchain.to_dict(), format="")
        self.save()

    def run_all_tests(self):
        """All tests, once per state of the project's files: a slow or hanging suite is not run twice."""
        key = fingerprint("".join(p + self.ws.read(p) for p in sorted(self.project.files)))
        if self.all_tests_run and self.all_tests_run[0] == key:
            return self.all_tests_run[1]
        result = self.toolchain.run_all(self.ws)
        self.all_tests_run = (key, result)
        return result

    def files_on_disk(self, *kinds):
        return [p for p in self.project.paths(*kinds) if self.ws.exists(p)]


class Worker:
    """A part of the work on a project, with what every part needs: the session (the model and
    what earlier projects taught), the project (its plan and state) and its workspace (its files
    and the commands run in it)."""

    def __init__(self, session):
        self.s, self.p, self.ws = session, session.project, session.ws


class Phase(Worker):
    """One phase of a cycle (CycleRunner): run(cycle) does it, and returns False when the cycle
    must stop there. TITLE is printed as it starts. A phase that BUILDS from the plan thinks at
    build_think (the setting); planning thinks at the model's own setting."""
    TITLE = ""
    BUILDS = False

    def run(self, cycle):
        raise NotImplementedError
