import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .build.project import Subsystem
from .messages import say
from .model.models import Calibrator, Models, Ollama, OllamaError
from .prompts import Prompts, Reply
from .settings import Home, confirm_deletion
from .text import now_text
from .workspace import TaughtCommands
lazy from .build.project import ending

COLLECTION = "skills"
SKILL_PROBE_SECONDS = 10
SKILL_MEMORY_SAMPLE_SECONDS = 0.5
SKILL_HELP_CHARS = 4000
SKILL_DRAFT_TOKENS = 800
SKILL_OUTPUTS = ("files", "text")
SLOT_RE = re.compile(r"\{(\w+)\}")
DOLLAR_SLOT_RE = re.compile(r'(?P<quote>")?\$(?P<brace>\{)?(?P<name>\w+)(?(brace)\})(?(quote)")')
NO_SKILLS = "No skills yet: tinker skill learn NAME [PURPOSE] learns one."


@dataclass
class Skill:
    name: str
    command: str
    template: str
    slots: list
    purpose: str
    output: str
    peak_memory: int
    state: str = "candidate"
    uses: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, record):
        return cls(**record)

    def to_dict(self):
        return asdict(self)

    def used(self, values, ok):
        self.uses.append({"when": now_text(), "values": dict(values), "ok": ok})

    def values(self, words):
        names = [slot["name"] for slot in self.slots]
        if names and len(words) > len(names):
            words = [*words[: len(names) - 1], " ".join(words[len(names) - 1 :])]
        return dict(zip(names, words))

    def samples(self):
        return {slot["name"]: slot["sample"] for slot in self.slots}

    def fixed(self, file_names):
        written = set(file_names)
        for slot in [slot for slot in self.slots if slot["sample"] in written]:
            name = shlex.quote(slot["sample"])
            self.template = re.sub(rf"\$?\{{{slot['name']}\}}", lambda _, name=name: name, self.template)
            self.slots.remove(slot)

    def glance(self):
        slots = f"slots {', '.join(slot['name'] for slot in self.slots)}" if self.slots else "no slots"
        return f"{self.name} — {self.purpose or 'no purpose given'} ({self.state}, {slots}; {len(self.uses)} uses)"


def show_list():
    learned = Skills.all()
    if not learned:
        say.text(NO_SKILLS)
    for skill in learned:
        say.text(skill.glance())


def known_skill(name):
    skill = Skills.get(name)
    if skill is None:
        glances = [skill.glance() for skill in Skills.all()] or [NO_SKILLS]
        raise SystemExit("\n".join([f"There is no skill {name}", *glances]))
    return skill


def use(skill, words):
    return skill_used(skill, words).use_ending(skill.name)


def skill_used(skill, words):
    values = skill.values(words)
    SkillRun(skill).command(values)
    make_room_for(skill)
    result = SkillRun(skill).run(values)
    skill.used(values, result.ok)
    Skills.put(skill)
    return result


def make_room_for(skill):
    loaded = Ollama.loaded_models() or []
    total = Calibrator.total_memory()
    if total is None or skill.peak_memory + sum(model.size_bytes for model in loaded) > total:
        unloaded(loaded)


def unloaded(models):
    for model in models:
        Ollama.unload(model.name)


def forgotten(name, yes=False):
    skill = Skills.get(name)
    if skill is None:
        raise SystemExit(f"There is no skill {name}")
    confirm_deletion("This deletes:", [skill.glance()], f"tinker skill forget {name}", yes)
    Skills.forget(name)
    say.done(f"Forgot the skill {name}.")


class Skills(Subsystem):
    COMMANDS = ("skill",)

    def handle(self, words, voice):
        match words[1:]:
            case ["list"]:
                return show_list()
            case ["forget", name]:
                return forgotten(name, self.yes)
            case ["learn", name, purpose]:
                return self.learn_skill(name, purpose, voice)
        return self.use_skill(known_skill(words[1]), words[2:], voice)

    def use_skill(self, skill, words, voice):
        self.skill_job(f"Using the skill {skill.name}", lambda: use(skill, words), voice)

    def learn_skill(self, name, purpose, voice):
        self.skill_job(f"Learning the skill {name}", lambda: SkillLearner.run(name, purpose), voice)

    def skill_job(self, title, ended, voice):
        self.tracked(voice, title, lambda tracker: (), lambda tracker: ended())

    @staticmethod
    def put(skill):
        Home.store().put(COLLECTION, skill.name, skill.to_dict())

    @staticmethod
    def get(name):
        record = Home.store().get(COLLECTION, name)
        return None if record is None else Skill.from_dict(record)

    @staticmethod
    def all():
        return [Skill.from_dict(record) for record in Home.store().items(COLLECTION).values()]

    @staticmethod
    def forget(name):
        if Skills.get(name) is None:
            raise SystemExit(f"There is no skill {name}")
        Home.store().delete(COLLECTION, name)


@dataclass
class Probe:
    name: str
    taught: dict | None
    shell: str
    program: str
    help: str


class SkillLearner:
    ALIASED_RE = re.compile(r"aliased to (.+)")
    ASSIGNMENT_RE = re.compile(r"^\w+=")
    SHELL_WORDS = frozenset(
        ["[", "[[", "test", "if", "then", "else", "elif", "fi", "local", "echo", "return", "printf", "read"]
        + ["vared", "export", "typeset", "declare", "print", "true", "false", "while", "for", "do", "done"]
    )

    @staticmethod
    def probe(name):
        taught = SkillLearner.taught(name)
        shell = SkillLearner.shell_says(name)
        program = SkillLearner.program(taught, shell)
        if taught is None and not shell and not program:
            raise SystemExit(f"{name} is not taught, not known to your shell and not a program on PATH")
        return Probe(name, taught, shell, program, SkillLearner.help(program))

    @staticmethod
    def run(name, purpose):
        return SkillLearner.learned(name, purpose).trial_ending(name)

    @staticmethod
    def learned(name, purpose):
        skill = SkillLearner.drafted(SkillLearner.probe(name), purpose)
        unloaded(Ollama.loaded_models() or [])
        result = SkillRun(skill).run(skill.samples())
        if result.ok:
            skill.state = "proven"
            skill.peak_memory = result.peak_memory
            skill.fixed(path.name for path in result.files)
            Skills.put(skill)
        return result

    @staticmethod
    def drafted(probe, purpose):
        return SkillDraft(SkillLearner.asked(probe, purpose)).skill(probe.name, purpose)

    @staticmethod
    def asked(probe, purpose):
        if not Models.quick():
            raise SystemExit("No model is set up, so the skill cannot be learned")
        try:
            reply = Models.quick_reply(
                [
                    {"role": "system", "content": Prompts.SKILL_SYSTEM},
                    {"role": "user", "content": SkillLearner.described(probe, purpose)},
                ],
                SKILL_DRAFT_TOKENS,
            )
            return Reply(reply).json()
        except OllamaError, ValueError:
            raise SystemExit("The model's reply for the skill could not be read") from None

    @staticmethod
    def described(probe, purpose):
        return (
            f"Command: {probe.name}\n"
            f"Taught to Tinker: {probe.taught or 'nothing'}\n"
            f"Shell says: {probe.shell or 'nothing'}\n"
            f"Program: {probe.program or 'none found'}\n"
            f"Help:\n{probe.help[:SKILL_HELP_CHARS] or 'none'}\n"
            f"Purpose: {purpose}"
        )

    @staticmethod
    def taught(name):
        entry = TaughtCommands.all().get(name)
        if entry is not None:
            return {key: value for key, value in entry.items() if key != "taught"}
        try:
            return TaughtCommands.look_up(name)
        except SystemExit:
            return None

    @staticmethod
    def shell_says(name):
        shell = os.environ.get("SHELL", "")
        if not shell:
            return ""
        return SkillLearner.output([shell, "-i", "-c", f"which {shlex.quote(name)}"], only_success=True)

    @staticmethod
    def function_definition(name):
        said = SkillLearner.shell_says(name)
        return said + "\n" if said.startswith(f"{name} () {{") else ""

    @staticmethod
    def program(taught, shell):
        for expansion in SkillLearner.expansions(taught, shell):
            found = SkillLearner.first_word_on_path(expansion)
            if found:
                return found
        return ""

    @staticmethod
    def expansions(taught, shell):
        if taught and taught.get("kind") == "alias":
            yield taught["definition"]
        if taught and taught.get("kind") == "program":
            yield shlex.quote(taught["path"])
        for line in shell.splitlines():
            aliased = SkillLearner.ALIASED_RE.search(line)
            yield aliased.group(1) if aliased else line.strip()

    @staticmethod
    def first_word_on_path(expansion):
        try:
            words = shlex.split(expansion)
        except ValueError:
            words = expansion.split()
        if not words or SkillLearner.is_shell_word(words[0]):
            return ""
        return shutil.which(words[0]) or ""

    @staticmethod
    def is_shell_word(word):
        return word in SkillLearner.SHELL_WORDS or bool(SkillLearner.ASSIGNMENT_RE.match(word))

    @staticmethod
    def help(program):
        if not program:
            return ""
        return SkillLearner.output([program, "--help"]) or SkillLearner.output([program, "-h"])

    @staticmethod
    def output(command, only_success=False):
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=SKILL_PROBE_SECONDS,
            )
        except OSError, subprocess.TimeoutExpired:
            return ""
        if proc.returncode != 0:
            return ""
        return (proc.stdout or ("" if only_success else proc.stderr)).strip()


class SkillDraft:
    def __init__(self, reply):
        self.reply = reply if isinstance(reply, dict) else {}

    def skill(self, name, purpose):
        template = self.braced(self.template())
        slots = self.slots(template)
        return Skill(name, name, template, slots, purpose, self.output(), 0)

    def template(self):
        template = self.reply.get("template")
        if not isinstance(template, str) or not template.strip():
            raise SystemExit("The model gave no template for the skill")
        return template.strip()

    def braced(self, template):
        names = {self.slot(entry)["name"] for entry in self.listed_slots()}
        return DOLLAR_SLOT_RE.sub(
            lambda form: f"{{{form['name']}}}" if form["name"] in names else form[0], template
        )

    def slots(self, template):
        slots = [self.slot(entry) for entry in self.listed_slots()]
        named = {slot["name"] for slot in slots}
        held = set(SLOT_RE.findall(template))
        if named - held:
            raise SystemExit(f"The template does not hold the slots {', '.join(sorted(named - held))}")
        if held - named:
            raise SystemExit(f"The template's {', '.join(sorted(held - named))} has no slot")
        return slots

    def listed_slots(self):
        slots = self.reply.get("slots") or []
        if not isinstance(slots, list):
            raise SystemExit("The model's slots for the skill are not a list")
        return slots

    @staticmethod
    def slot(entry):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            raise SystemExit("A slot of the skill has no name")
        return {"name": entry["name"], "sample": str(entry.get("sample", ""))}

    def output(self):
        output = self.reply.get("output")
        if output not in SKILL_OUTPUTS:
            raise SystemExit(f"The skill's output must be files or text, not {output!r}")
        return output


@dataclass
class SkillResult:
    ok: bool
    files: list
    text: str
    peak_memory: int
    folder: Path

    def trial_ending(self, name):
        if self.ok:
            return ending("done", f"Learned {name}", self.result_lines())
        return ending("blocked", f"{name} did not work with its sample values", self.printed())

    def use_ending(self, name):
        if self.ok:
            done = ending("done", f"Used {name}", self.result_lines())
            return done | {"files": [str(path) for path in self.files]}
        return ending("blocked", f"{name} did not work", self.printed())

    def result_lines(self):
        return [("File", str(path)) for path in self.files] or self.printed()

    def printed(self):
        return [("Printed", line) for line in self.text.splitlines()]


class MemoryRise:
    def __init__(self):
        self.baseline = SkillRun.memory_in_use()
        self.highest = self.baseline
        self.stopped = threading.Event()
        self.sampler = threading.Thread(target=self.sample_until_stopped, daemon=True)
        self.sampler.start()

    def sample_until_stopped(self):
        while not self.stopped.wait(SKILL_MEMORY_SAMPLE_SECONDS):
            self.sample()

    def sample(self):
        reading = SkillRun.memory_in_use()
        if reading is not None and self.baseline is not None:
            self.highest = max(self.highest, reading)

    def stopped_rise(self):
        self.stopped.set()
        self.sampler.join()
        self.sample()
        return 0 if self.baseline is None else self.highest - self.baseline


class SkillRun:
    def __init__(self, skill):
        self.skill = skill

    def run(self, values):
        line = self.command(values)
        folder = Home.skill_folder(self.skill.name)
        exit_code, text, peak_memory = self.ran(
            self.definition() + TaughtCommands.prelude(line) + line, folder
        )
        files = self.new_files(folder) or self.printed_files(text, folder)
        ok = exit_code == 0 and (files or self.skill.output != "files")
        return SkillResult(bool(ok), files, text, peak_memory, folder)

    def definition(self):
        return SkillLearner.function_definition(self.skill.command)

    @staticmethod
    def ran(script, folder):
        shell = os.environ.get("SHELL") or "/bin/sh"
        rise = MemoryRise()
        process = subprocess.Popen(
            [shell, "-c", script],
            cwd=folder,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        text = process.stdout.read().decode(errors="replace")
        process.stdout.close()
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        peak = max(rise.stopped_rise(), SkillRun.bytes_of(usage.ru_maxrss))
        return process.returncode, text, peak

    @staticmethod
    def bytes_of(max_rss):
        return max_rss if sys.platform == "darwin" else max_rss * 1024

    @staticmethod
    def memory_in_use():
        try:
            if sys.platform == "darwin":
                return SkillRun.vm_stat_bytes(
                    subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
                )
            return SkillRun.meminfo_bytes(Path("/proc/meminfo").read_text())
        except OSError, ValueError, KeyError, AttributeError:
            return None

    @staticmethod
    def vm_stat_bytes(text):
        page_size = int(re.search(r"page size of (\d+) bytes", text).group(1))
        pages = dict(re.findall(r"^(.+?):\s+(\d+)\.", text, re.MULTILINE))
        in_use = ("Pages wired down", "Pages active", "Pages occupied by compressor")
        return sum(int(pages[name]) for name in in_use) * page_size

    @staticmethod
    def meminfo_bytes(text):
        kilobytes = {name: int(value) for name, value in re.findall(r"^(\w+):\s+(\d+)", text, re.MULTILINE)}
        return (kilobytes["MemTotal"] - kilobytes["MemAvailable"]) * 1024

    @staticmethod
    def new_files(folder):
        return sorted(path for path in folder.rglob("*") if path.is_file())

    @staticmethod
    def printed_files(text, folder):
        return [path for path in (folder / word for word in text.split()) if path.is_file()]

    def command(self, values):
        return SLOT_RE.sub(lambda found: self.filled(found.group(1), values), self.skill.template)

    def filled(self, slot, values):
        if values.get(slot) is None:
            raise SystemExit(f"The skill {self.skill.name} needs a value for {slot}")
        return shlex.quote(str(values[slot]))
