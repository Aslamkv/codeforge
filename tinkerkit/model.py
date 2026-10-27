from collections import namedtuple
lazy import json
lazy import secrets
lazy import subprocess
lazy import sys
lazy import time
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request

from .messages import say
from .settings import (
    CALIBRATION_KEEP_ALIVE,
    CAPACITY_SEARCH_PRECISION,
    CRASH_NEAR_LIMIT_SHARE,
    CRASH_RECOVERY_SECONDS,
    CRASH_RETRIES,
    CRASHED_LIMIT_SHARE,
    DEFAULT_CONFIG,
    DEFAULT_THINK,
    FIRST_CHAT_CONTEXT,
    FIX_MAX_TOKENS,
    LAST_CHUNK_WAIT_SECONDS,
    LOOPING_REPEATED_LINES,
    MAX_CHARS_PER_TOKEN,
    MIN_CHARS_PER_TOKEN,
    MIN_PROMPT_TOKENS,
    MIN_REPLY_TOKENS,
    OLLAMA_SILENCE_TIMEOUT,
    OOM_RETRIES,
    OOM_RETRY_PROMPT_SHARE,
    PROBE_NUM_BATCH,
    PROBE_SAMPLE_TOKENS,
    PROGRESS_UPDATE_SECONDS,
    PROMPT_BUDGET_SHARE,
    PROMPT_TARGET_TOKENS,
    QUICK_REPLY_TIMEOUT,
    REPLY_ERROR_RETRIES,
    REPLY_TARGET_TOKENS,
    RUNNER_OVERHEAD_BYTES,
    STALL_RETRIES,
    STARTING_CHARS_PER_TOKEN,
    UNFINISHED_THOUGHT_KEPT_CHARS,
    Config,
    Home,
)
from .text import first_line, now_text
lazy from .jobs import journaled
lazy from .prompts import Part, Prompts, PromptTooLarge, Reply
lazy from .workspace import ToolError

SelectedModel = namedtuple("SelectedModel", "name settings")
QuickModel = namedtuple("QuickModel", "name options think")
Capacity = namedtuple("Capacity", "largest_that_reads smallest_that_failed")
ContextSplit = namedtuple("ContextSplit", "prompt_budget longest_reply")
LoadedModel = namedtuple("LoadedModel", "name size_bytes")


class Answer(namedtuple("Answer", "value why thought", defaults=("", ""))):
    def needed(self, message):
        if self.why:
            raise SystemExit(f"{message} ({self.why})")
        return self.value


class Model:
    def __init__(self, name, settings, keep_alive, log, max_growth=DEFAULT_CONFIG["max_growth"]):
        self.name, self.settings = name, settings
        self.max_tokens, self.num_ctx = int(settings["max_prompt_tokens"]), int(settings["num_ctx"])
        self.think = settings.get("think")
        self.build_think = Config.get("build_think") if self.think not in (None, False) else None
        self.is_building = False
        self.max_reply, self.fix_max = int(settings["max_reply_tokens"]), int(settings["fix_max_tokens"])
        self.keep_alive = keep_alive
        self.options = {"num_batch": int(settings["num_batch"])} if settings.get("num_batch") else {}
        self.log, self.max_growth = log, max_growth
        self.think_rejected = False
        self.crashes = 0
        self.calibrated = (self.max_tokens, self.max_reply)
        self.base_gb = None
        self.chars_per_token = max(
            MIN_CHARS_PER_TOKEN,
            min(float(settings.get("chars_per_token") or STARTING_CHARS_PER_TOKEN), MAX_CHARS_PER_TOKEN),
        )
        self.recent_chars_per_token = []

    def ask(self, system, parts, label, **kw):
        try:
            return self.ask_once(system, parts, label, **kw)
        except PromptTooLarge:
            self.note(f"{label}: the prompt is too large as it is; shortening every part")
        relaxed = lambda limit: [part.relaxed() for part in (parts(limit) if callable(parts) else parts)]
        return self.ask_once(system, relaxed, label, **kw)

    def ask_once(
        self,
        system,
        parts,
        label,
        budget=None,
        done_when=None,
        max_reply=None,
        unthinking_retry=True,
        think=None,
    ):
        self.evict_others()
        chosen = self.think_now() if think is None else think
        tries = Tries(None if self.think_rejected else chosen, budget or self.max_reply)
        request = (
            system + Prompts.NO_TOOL_CALLS,
            parts,
            label,
            done_when,
            max_reply or self.max_reply,
            unthinking_retry,
        )
        answer = None
        while answer is None:
            answer = self.one_try(tries, *request)
        return answer

    def one_try(self, tries, system, parts, label, done_when, max_reply, unthinking_retry):
        user = self.user_message(
            system, self.with_thought(parts, tries.unfinished_thought), label, tries.shrink
        )
        cap = min(self.reply_room(system, user), self.max_reply, max_reply)
        tries.budget = max(MIN_REPLY_TOKENS, min(tries.budget, cap))
        shown = self.call_label(label, tries.think, tries.budget)
        started = time.perf_counter()
        try:
            data = self.send(system, user, tries, shown, done_when)
        except OllamaError as exc:
            return self.after_error(exc, label, tries, self.prompt_tokens(system, user), shown, started)
        content, cut = self.log_exchange(system, user, shown, tries.budget, data, started)
        if cut and self.unfinished(content, done_when) and tries.think is not False:
            return self.think_again(tries, data, shown, started, cap, unthinking_retry)
        self.report(shown, data, started, cut)
        self.check_memory()
        return Answer(content)

    def evict_others(self):
        for loaded, _ in Ollama.loaded_models() or []:
            if loaded and loaded != self.name and loaded not in Ollama.embedders:
                self.note(f"unloading {loaded} (only one model in memory)")
                Ollama.unload(loaded)

    def think_now(self):
        return self.build_think if self.is_building and self.build_think else self.think

    def user_message(self, system, parts, label, shrink):
        limit = self.limit_chars(system, int(self.max_tokens * OOM_RETRY_PROMPT_SHARE**shrink))
        notes = []
        user = Part.assemble(parts(limit) if callable(parts) else parts, limit, notes)
        if notes:
            self.note(f"{label}: shortened {', '.join(notes)} to fit the prompt budget")
        return user

    @staticmethod
    def with_thought(parts, thought):
        if not thought.strip():
            return parts
        return Model.with_part(
            parts,
            Part(
                "Your analysis so far (it ran out of room before an answer: conclude it "
                "and answer now, in exactly the reply format the instructions give)",
                thought,
                keep="tail",
                place="last",
            ),
        )

    @staticmethod
    def with_part(parts, part):
        return (lambda limit: parts(limit) + [part]) if callable(parts) else list(parts) + [part]

    def reply_room(self, system, user):
        return self.num_ctx - int((len(system) + len(user)) / self.chars_per_token) - 64

    @staticmethod
    def call_label(label, think, budget):
        mode = "no thinking" if think is False else f"think {think if think is not None else 'default'}"
        return f"{label} [{mode}, reply up to {budget // 1024}K tok]"

    def send(self, system, user, tries, shown, done_when):
        return Ollama.chat(
            self.name,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            self.keep_alive,
            self.request_options(tries.budget),
            tries.think,
            shown,
            done_when,
        )

    def request_options(self, budget):
        options = dict(self.options)
        options.setdefault("num_ctx", self.num_ctx)
        options["num_predict"] = budget
        return options

    def after_error(self, exc, label, tries, prompt_tokens, shown, started):
        if exc.crashed:
            tries.crashes += 1
            if exc.streamed:
                self.after_reply_crash(exc, label, tries.crashes)
            else:
                self.after_crash(exc, label, prompt_tokens, tries.crashes)
            return None
        if exc.in_reply:
            tries.reply_errors += 1
            return (
                Answer(exc.partial)
                if self.reply_error_is_final(exc, shown, started, tries.reply_errors)
                else None
            )
        self.recover(exc, label, tries)
        return None

    def after_crash(self, exc, label, prompt_tokens, crashes):
        self.stop_after(exc, crashes, f"the last time reading a prompt of ~{prompt_tokens} tokens")
        what = f"crashed reading a prompt of ~{prompt_tokens} tokens"
        if crashes > 1 and prompt_tokens >= self.max_tokens * CRASH_NEAR_LIMIT_SHARE:
            lowered = max(MIN_PROMPT_TOKENS, int(self.max_tokens * CRASHED_LIMIT_SHARE))
            what += f"; prompt budget {self.max_tokens} -> {lowered} tokens for this run"
            self.max_tokens = lowered
        self.crashed(exc, label, what, prompt_tokens=prompt_tokens)

    def after_reply_crash(self, exc, label, crashes):
        self.stop_after(exc, crashes, f"the last time after writing {exc.streamed} tokens")
        what = f"crashed after writing {exc.streamed} tokens"
        if crashes > 1 and exc.streamed >= self.max_reply * CRASH_NEAR_LIMIT_SHARE:
            lowered = max(MIN_REPLY_TOKENS, int(self.max_reply * CRASHED_LIMIT_SHARE))
            what += f"; longest reply {self.max_reply} -> {lowered} tokens for this run"
            self.max_reply, self.fix_max = lowered, min(self.fix_max, lowered)
        self.crashed(exc, label, what, written=exc.streamed)

    def stop_after(self, exc, crashes, last):
        if crashes > CRASH_RETRIES:
            raise OllamaError(
                f"{exc}\n{self.name} crashed {crashes} times on this call, {last}. If this keeps "
                f"happening, measure the model again: tinker calibrate {self.name}",
                crashed=True,
            )

    def crashed(self, exc, label, what, **facts):
        self.crashes += 1
        self.note(
            f"{label}: {self.name} {what} ({first_line(str(exc), 90)}); trying again in {CRASH_RECOVERY_SECONDS}s",
            f"{self.name} crashed; trying again in {CRASH_RECOVERY_SECONDS}s",
        )
        self.log.add(
            {
                "label": label,
                "model": self.name,
                "error": str(exc),
                **facts,
                "budget_now": self.max_tokens,
                "max_reply_now": self.max_reply,
            }
        )
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY_SECONDS)

    def reply_error_is_final(self, exc, shown, started, tries):
        self.log.add({"label": shown, "model": self.name, "error": str(exc), "reply": exc.partial})
        if tries <= REPLY_ERROR_RETRIES:
            self.step(f"{shown}  ({first_line(str(exc), 120)}; asking again)", started)
            return False
        self.step(
            f"{shown}  ({first_line(str(exc), 120)} again; using the {len(exc.partial)} chars that came "
            f"before it)",
            started,
        )
        return True

    def recover(self, exc, label, tries):
        if exc.about_thinking and tries.think is not None:
            self.lower_think(label, tries)
        elif exc.oom and tries.shrink < OOM_RETRIES:
            self.shorter_prompt(label, tries)
        elif exc.stalled and tries.stalls < STALL_RETRIES:
            self.reload_model(exc, label, tries)
        else:
            raise exc

    def lower_think(self, label, tries):
        rejected = tries.think
        if self.is_building and rejected == self.build_think != self.think:
            self.build_think, tries.think = None, self.think
        elif rejected in (True, False):
            self.think, self.think_rejected, tries.think = None, True, None
        else:
            self.think = tries.think = True
        self.note(f"{label}: {self.name} rejected think={rejected!r}; using think={tries.think!r}")

    def shorter_prompt(self, label, tries):
        self.note(
            f"{label}: out of GPU memory; retrying this request with a prompt budget of "
            f"{int(self.max_tokens * OOM_RETRY_PROMPT_SHARE ** (tries.shrink + 1))} tokens",
            "Out of GPU memory; trying again with a shorter prompt",
        )
        tries.shrink += 1

    def reload_model(self, exc, label, tries):
        self.note(
            f"{label}: {exc}; reloading the model and retrying",
            f"{self.name} stopped responding; reloading it and trying again",
        )
        Ollama.unload(self.name)
        tries.stalls += 1

    def prompt_tokens(self, system, user):
        return int((len(system) + len(user)) / self.chars_per_token)

    def log_exchange(self, system, user, shown, budget, data, started):
        content = data.get("message", {}).get("content") or ""
        cut = data.get("done_reason") == "length"
        self.learn_chars_per_token(len(system) + len(user), data.get("prompt_eval_count", 0))
        self.log.add(
            {
                "label": shown,
                "model": self.name,
                "system": system,
                "user": user,
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "reply_tokens": data.get("eval_count", 0),
                "budget": budget,
                "cut": cut,
                "seconds": round(time.perf_counter() - started, 1),
                "load_seconds": round(data.get("load_duration", 0) / 1e9, 1),
                "reply": content,
            }
        )
        return content, cut

    def learn_chars_per_token(self, chars, tokens):
        if tokens and chars and tokens > 50:
            self.recent_chars_per_token = (self.recent_chars_per_token + [chars / tokens])[-20:]
            self.chars_per_token = max(
                MIN_CHARS_PER_TOKEN, min(min(self.recent_chars_per_token), MAX_CHARS_PER_TOKEN)
            )

    @staticmethod
    def unfinished(content, done_when):
        return not content.strip() or (
            done_when is not None and not done_when(content) and not Reply.files_ended(content)
        )

    def think_again(self, tries, data, shown, started, cap, unthinking_retry):
        action, said = self.after_cut(data, tries.budget, cap, unthinking_retry)
        self.step(f"{shown}  ({said})", started)
        thought = data["message"].get("thinking", "")
        if action == "give up":
            return Answer("", why="the reply ran out of room while still thinking", thought=thought)
        if action == "more":
            tries.budget = min(tries.budget * 2, cap)
        else:
            tries.think, tries.unfinished_thought = False, thought
        return None

    @staticmethod
    def after_cut(data, budget, cap, unthinking_retry):
        tokens = data.get("eval_count", 0)
        if budget < cap:
            return "more", (
                f"cut at {tokens} tokens while thinking; asking again with up to "
                f"{min(budget * 2, cap) // 1024}K tokens"
            )
        if unthinking_retry:
            return "unthinking", "cut at the most a reply may think; asking again without thinking"
        return (
            "give up",
            f"thought to the {tokens}-token cap without an answer; counted as an attempt with no change",
        )

    def report(self, shown, data, started, cut):
        tokens_in, tokens_out = data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        if tokens_in > self.max_tokens:
            self.note(f"note: prompt was {tokens_in} tokens, above the {self.max_tokens} budget estimate")
        if data.get("early_stop"):
            self.step(f"{shown}  (stopped as soon as the reply was complete, ~{tokens_out} tok)", started)
        else:
            self.step(
                f"{shown}  (prompt {tokens_in} tok, gen {data.get('eval_duration', 0) / 1e9:.1f}s"
                f"/{tokens_out} tok, load {data.get('load_duration', 0) / 1e9:.1f}s)",
                started,
            )
        if cut:
            self.note(f"reply cut at {tokens_out} tokens")

    def step(self, message, started):
        self.log.add(
            {"note": message, "model": self.name, "seconds": round(time.perf_counter() - started, 1)}
        )

    def note(self, detail, retry=""):
        self.log.add({"note": detail, "model": self.name})
        if retry:
            say.retry(retry)

    def check_memory(self):
        if self.keep_alive in (0, "0"):
            return
        size = self.loaded_size()
        if size is None:
            return
        if self.base_gb is None:
            self.base_gb = size
        elif size > self.base_gb * self.max_growth:
            self.note(f"model grew {self.base_gb:.1f} -> {size:.1f} GB; unloading")
            self.unload()

    def loaded_size(self):
        return next(
            (size / 1e9 for model_name, size in Ollama.loaded_models() or [] if model_name == self.name), None
        )

    def unload(self):
        if self.keep_alive not in (0, "0"):
            Ollama.unload(self.name)

    def ask_until_usable(self, system, parts, label, read, attempts=3, think=None, max_reply=None):
        problem = None
        for attempt in range(1, attempts + 1):
            try:
                answer = self.ask(
                    system,
                    parts,
                    f"{label} (attempt {attempt})",
                    done_when=Reply.json_complete,
                    think=think,
                    max_reply=max_reply,
                )
            except PromptTooLarge as exc:
                return Answer(None, why=self.too_large_why(exc))
            try:
                return Answer(read(Reply(answer.value)))
            except (ValueError, ToolError, AttributeError, TypeError, KeyError) as exc:
                problem = exc
                self.note(
                    f"{label.lower()} not usable: {exc}",
                    f"{say.going_on()}: no usable result yet, trying again" if attempt < attempts else "",
                )
                parts = self.with_part(parts, self.not_usable_note(exc))
        return Answer(None, why=f"no usable reply after {attempts} attempt(s): {problem}")

    @staticmethod
    def not_usable_note(problem):
        return Part(
            "Note", f"Your previous reply was not usable ({problem}). Reply with ONLY the JSON.", True
        )

    def too_large_why(self, exc):
        return (
            f"even shortened, the prompt does not fit {self.name}'s input budget of {self.max_tokens} tokens "
            f"({exc}). Measure the model again (tinker calibrate) or use one that reads longer prompts "
            f"(tinker models)"
        )

    def limit_chars(self, system, tokens=None):
        return (
            int((tokens or self.max_tokens) * PROMPT_BUDGET_SHARE * self.chars_per_token) - len(system) - 40
        )

    def learned_chars_per_token(self):
        return round(min(self.recent_chars_per_token), 2) if len(self.recent_chars_per_token) >= 5 else None


class Tries:
    def __init__(self, think, budget):
        self.think, self.budget = think, budget
        self.shrink = self.stalls = self.crashes = self.reply_errors = 0
        self.unfinished_thought = ""


class Models:
    MEASURED_LIMITS = ("max_prompt_tokens", "num_ctx", "max_reply_tokens", "fix_max_tokens")

    @staticmethod
    def answering():
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(
                f"Ollama does not answer at {Config.ollama_url()} (tinker config ollama_url URL)."
            )
        return models

    @staticmethod
    def choices():
        return [(name, Models.settings(name) is not None) for name in Models.named(embedding=False)]

    @staticmethod
    def embedding_choices():
        return [(name, True) for name in Models.named(embedding=True)]

    @staticmethod
    def named(embedding):
        return [name for name in Models.names() if Models.embeds(Ollama.capabilities(name)) == embedding]

    @staticmethod
    def names():
        return [model.get("name", "") for model in Models.answering()]

    @staticmethod
    def command_choices(command):
        if command == "embed":
            return Models.embedding_choices()
        choices = Models.choices()
        return [(name, True) for name, _ in choices] if command == "calibrate" else choices

    @staticmethod
    def embeds(capabilities):
        return not capabilities or "embedding" in capabilities

    @staticmethod
    def picked(command, choices):
        names, current = [name for name, _ in choices], Models.in_use(command)
        start = names.index(current) if current in names else 0
        index = say.pick([(name, not plain) for name, plain in choices], start)
        return None if index is None else names[index]

    @staticmethod
    def in_use(command):
        return Config.get("embed_model" if command == "embed" else "model")

    @staticmethod
    def installed(name):
        names = Models.names()
        if name not in names:
            close = [candidate for candidate in names if name.lower() in candidate.lower()]
            hint = f" Did you mean {' or '.join(close)}?" if close else ""
            raise SystemExit(
                f"Ollama has no model named '{name}'.{hint} Installed: {', '.join(names) or 'none'}"
            )
        return name

    @staticmethod
    def selected():
        name = Config.get("model")
        if not name:
            raise SystemExit(
                "No model in use. Measure one Ollama has with tinker calibrate NAME, then select it with "
                "tinker use-model NAME (tinker models lists them)."
            )
        settings = Models.settings(name)
        if settings is None:
            raise SystemExit(f"{name}, the model in use, has not been measured: tinker calibrate {name}")
        broken = Models.unusable_measurements(settings)
        if broken:
            raise SystemExit(
                f"What was measured for {name} has no usable {', '.join(broken)}: measure the model "
                f"again with tinker calibrate {name}"
            )
        return SelectedModel(name, settings)

    @staticmethod
    def quick():
        name = Config.get("model")
        settings = (Models.settings(name) or {}) if name else {}
        if not settings.get("num_ctx"):
            return None
        options = {"num_ctx": settings["num_ctx"]}
        if settings.get("num_batch"):
            options["num_batch"] = settings["num_batch"]
        return QuickModel(name, options, False if settings.get("think") not in (None, False) else None)

    @staticmethod
    def quick_reply(messages, tokens):
        name, options, think = Models.quick()
        data = Ollama.chat(
            name,
            messages,
            Config.keep_alive(Config.get("keep_alive")),
            dict(options, num_predict=tokens),
            think=think,
            timeout=QUICK_REPLY_TIMEOUT,
        )
        return data["message"]["content"].strip()

    @staticmethod
    def unusable_measurements(settings):
        return [
            key
            for key in Models.MEASURED_LIMITS
            if isinstance(settings.get(key), bool)
            or not isinstance(settings.get(key), int)
            or settings[key] < 1
        ]

    @staticmethod
    def settings(name):
        settings = Home.store().get("models", name)
        return settings if isinstance(settings, dict) else None

    @staticmethod
    def keep(name, settings):
        Home.store().put("models", name, settings)

    @staticmethod
    def remember(model):
        if model.crashes:
            Models.say_crashes(model)
        learned, settings = model.learned_chars_per_token(), Models.settings(model.name)
        if learned and settings and settings.get("chars_per_token") != learned:
            Home.store().update(
                "models", model.name, lambda kept: dict(kept or settings, chars_per_token=learned)
            )

    @staticmethod
    def say_crashes(model):
        lowered = [
            f"{name} {now} (measured {was})"
            for name, now, was in (
                ("prompt budget", model.max_tokens, model.calibrated[0]),
                ("longest reply", model.max_reply, model.calibrated[1]),
            )
            if now < was
        ]
        say.warn(
            f"{model.name} crashed {model.crashes} time(s) this run"
            + (f"; lowered for this run only: {', '.join(lowered)}" if lowered else "")
            + f". If crashes keep happening, measure it again: "
            f"tinker calibrate {model.name}"
        )

    @staticmethod
    def context_for(budget, max_reply, context_length):
        needed = -(-(budget + max_reply + 512) // 1024) * 1024
        return min(needed, context_length) if context_length else needed

    @staticmethod
    def reply_limits(max_reply):
        return {"max_reply_tokens": max_reply, "fix_max_tokens": min(FIX_MAX_TOKENS, max_reply)}


class Calibrator:
    FILLER = """def due_items(items, today):
    \"\"\"The items due by today, oldest first: each a dict with "id", "title" and "due" (an ISO date).\"\"\"
    found = [item for item in items if item.get("due") and item["due"] <= today.isoformat()]
    return sorted(found, key=lambda item: (item["due"], item["id"]))  # ties keep a stable order


# A request asks for the due list; the reply is JSON: {"items": [...], "count": 3}.
"""

    def __init__(self, name):
        self.name = name
        self.tokens_per_digit = 1.0
        self.largest_read_stats = {}
        self.load_seconds = None
        self.options = {}
        self.measured_chars_per_token = None
        self.probe_history = []
        self.write_tokens_per_second = None

    def run(self):
        say.title(f"Calibrating {self.name} (once per model; a few minutes)", "📏")
        started = time.perf_counter()
        facts = Ollama.model_facts(self.name)
        self.check_fit(facts)
        target = min(
            PROMPT_TARGET_TOKENS + REPLY_TARGET_TOKENS,
            facts["context_length"] or PROMPT_TARGET_TOKENS + REPLY_TARGET_TOKENS,
        )
        think = self.first_chat(facts, target)
        context, crash = self.capacity(target, facts)
        budget, max_reply = self.shared(context)
        settings = self.settings(facts, think, budget, context, crash, max_reply)
        Ollama.unload(self.name)
        Models.keep(self.name, settings)
        say.took(
            f"Calibrated {self.name}: prompts up to {budget} tokens, replies up to {max_reply}, think {think}",
            time.perf_counter() - started,
        )
        return settings

    def check_fit(self, facts):
        memory, size = self.gpu_memory(), facts.get("size") or 0
        if not memory or not size:
            return
        with say.section():
            say.info(f"{size / 1e9:.1f} GB of weights; {memory / 1e9:.1f} GB of GPU memory", "🧮")
        if size + RUNNER_OVERHEAD_BYTES > memory:
            raise SystemExit(self.does_not_fit(size, memory))

    def does_not_fit(self, size, memory):
        return (
            f"{self.name} needs {size / 1e9:.1f} GB for its weights alone, and the GPU may use "
            f"{memory / 1e9:.1f} GB: choose a smaller model or quantization"
            + (
                f", or let the GPU use more memory (e.g. sudo sysctl iogpu.wired_limit_mb="
                f"{int(self.total_memory() * 0.9 / 2**20)})"
                if sys.platform == "darwin"
                else ""
            )
            + "."
        )

    @classmethod
    def gpu_memory(cls):
        total = cls.total_memory()
        if sys.platform != "darwin" or not total:
            return total
        try:
            limit = int(
                subprocess.run(
                    ["sysctl", "-n", "iogpu.wired_limit_mb"], capture_output=True, text=True
                ).stdout
                or 0
            )
        except OSError, ValueError:
            limit = 0
        return limit * 2**20 if limit > 0 else int(total * (0.75 if total > 36e9 else 0.67))

    @staticmethod
    def total_memory():
        try:
            if sys.platform == "darwin":
                return int(
                    subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
                )
            with open("/proc/meminfo") as info:
                return int(next(line for line in info if line.startswith("MemTotal")).split()[1]) * 1024
        except OSError, ValueError, StopIteration:
            return None

    def first_chat(self, facts, target):
        thinks = not facts["capabilities"] or "thinking" in facts["capabilities"]
        content = self.FILLER * 4
        for think in (DEFAULT_THINK, True) if thinks else (None,):
            try:
                data = self.chat_at(content, think, target)
            except OllamaError as exc:
                if think is not None and exc.about_thinking:
                    continue
                raise SystemExit(f"{self.name} does not run: {exc}")
            self.measure_chat(content, data)
            self.show_first_chat(think)
            return think
        return None

    def chat_at(self, content, think, target):
        try:
            return self.say_ok(content, think, target)
        except OllamaError as exc:
            if (think is not None and exc.about_thinking) or target <= FIRST_CHAT_CONTEXT:
                raise
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY_SECONDS)
        return self.say_ok(content, think, FIRST_CHAT_CONTEXT)

    def say_ok(self, content, think, context):
        return Ollama.chat(
            self.name,
            [{"role": "user", "content": content + "\nSay OK."}],
            CALIBRATION_KEEP_ALIVE,
            {"num_ctx": context, "num_predict": 64},
            think,
        )

    def show_first_chat(self, think):
        with say.section():
            say.info(
                f"Thinks with {think!r}; {self.safe_ratio()} chars per token; writes {self.write_tokens_per_second} "
                f"tokens a second",
                "🧠",
            )

    def measure_chat(self, content, data):
        self.load_seconds = round(data.get("load_duration", 0) / 1e9, 1)
        read, written = data.get("prompt_eval_count") or 0, data.get("eval_count") or 0
        self.measured_chars_per_token = len(content) / read if read > 50 else None
        seconds = (data.get("eval_duration") or 0) / 1e9
        self.write_tokens_per_second = round(written / seconds, 1) if written and seconds else None

    def capacity(self, target, facts):
        reading = self.reading(facts)
        if reading.target_reads(target):
            return Capacity(target, None)
        good, bad = 0, target
        while bad - good > CAPACITY_SEARCH_PRECISION * bad and bad > MIN_PROMPT_TOKENS + MIN_REPLY_TOKENS:
            middle = (good + bad) // 2
            good, bad = (middle, bad) if reading.reads(middle) else (good, middle)
        if good < MIN_PROMPT_TOKENS + MIN_REPLY_TOKENS:
            raise SystemExit(
                f"{self.name} cannot hold even {MIN_PROMPT_TOKENS + MIN_REPLY_TOKENS} tokens of "
                f"context on this machine: choose a smaller model or quantization."
            )
        return Capacity(good, bad)

    def reading(self, facts):
        return (GgufReading if (facts.get("format") or "").lower() == "gguf" else GrowingReading)(self)

    def read(self, tokens, context):
        with say.section():
            say.step(
                f"Reading {tokens} tokens with a context of {context} (about "
                f"{round(tokens / max(self.read_speed(), 1))}s)…",
                "📏",
            )
        return self.probe(tokens, context)

    def probe(self, tokens, num_ctx):
        digits = max(1, round(tokens / self.tokens_per_digit))
        prompt = "".join(secrets.choice("0123456789") for _ in range(digits))
        timeout = max(OLLAMA_SILENCE_TIMEOUT, int(tokens / max(self.read_speed() / 3, 1)))
        try:
            data = Ollama.read_raw(
                self.name,
                prompt,
                {"num_ctx": num_ctx, "num_predict": 1, **self.options},
                CALIBRATION_KEEP_ALIVE,
                timeout,
            )
        except OllamaError as exc:
            self.probe_failed(exc, tokens)
            return None
        return self.probe_read(data, tokens, digits)

    def probe_failed(self, exc, tokens):
        if Ollama.installed_models() is None:
            raise SystemExit(f"Ollama stopped answering during calibration: {exc}")
        with say.section(), say.section():
            say.fail(f"Could not read it ({first_line(str(exc), 100)})")
        self.probe_history.append([tokens, "crashed"])
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY_SECONDS)

    def probe_read(self, data, tokens, digits):
        read = data.get("prompt_eval_count") or tokens
        self.tokens_per_digit = read / digits
        self.probe_history.append([read, "read"])
        if read >= self.largest_read_stats.get("prompt_eval_count", 0):
            self.largest_read_stats = data
        with say.section(), say.section():
            say.done(f"Read {read} tokens in {data.get('prompt_eval_duration', 0) / 1e9:.0f}s")
        return read

    def read_speed(self):
        if self.largest_read_stats.get("prompt_eval_duration"):
            return (
                self.largest_read_stats["prompt_eval_count"]
                / self.largest_read_stats["prompt_eval_duration"]
                * 1e9
            )
        return (self.write_tokens_per_second or 25) * 4

    @staticmethod
    def shared(context):
        budget = min(PROMPT_TARGET_TOKENS, max(MIN_PROMPT_TOKENS, context // 3))
        return ContextSplit(budget, max(MIN_REPLY_TOKENS, min(REPLY_TARGET_TOKENS, context - budget)))

    def settings(self, facts, think, budget, context, crash, max_reply):
        return {
            "model": self.name,
            "calibrated": now_text(),
            "max_prompt_tokens": budget,
            "largest_prompt_read": context,
            "crashed_reading": crash,
            "num_ctx": Models.context_for(budget, max_reply, facts["context_length"]),
            "context_length": facts["context_length"],
            "think": think,
            **Models.reply_limits(max_reply),
            "chars_per_token": self.safe_ratio(),
            "num_batch": self.options.get("num_batch"),
            "read_tokens_per_s": round(self.read_speed()) if self.largest_read_stats else None,
            "write_tokens_per_s": self.write_tokens_per_second,
            "load_seconds": self.load_seconds,
            "probes": self.probe_history,
            "tokens_per_digit": round(self.tokens_per_digit, 4),
        }

    def safe_ratio(self):
        if not self.measured_chars_per_token:
            return STARTING_CHARS_PER_TOKEN
        return round(
            max(MIN_CHARS_PER_TOKEN, min(self.measured_chars_per_token * 0.97, MAX_CHARS_PER_TOKEN)), 2
        )


class GgufReading:
    def __init__(self, calibrator):
        self.calibrator = calibrator

    def target_reads(self, context):
        return self.reads(context)

    def reads(self, context):
        calibrator, tokens = self.calibrator, min(context, PROBE_SAMPLE_TOKENS)
        if calibrator.read(tokens, context):
            return True
        if not calibrator.options:
            calibrator.options = {"num_batch": PROBE_NUM_BATCH}
            if calibrator.probe(tokens, context):
                with say.section(), say.section():
                    say.done(f"Read in pieces of {PROBE_NUM_BATCH} tokens (num_batch)")
                return True
            calibrator.options = {}
        return False


class GrowingReading:
    def __init__(self, calibrator):
        self.calibrator = calibrator

    def target_reads(self, context):
        return self.sample_shows_fit(context) or self.reads(context)

    def reads(self, context):
        return bool(self.calibrator.read(context, context))

    def sample_shows_fit(self, context):
        tokens, before = min(context, PROBE_SAMPLE_TOKENS), self.loaded_size()
        if before is None or tokens >= context or not self.calibrator.read(tokens, context):
            return False
        after, memory = self.loaded_size(), self.calibrator.gpu_memory()
        if after is None or not memory:
            return False
        grown = (after - before) / tokens * (context - tokens)
        return after + grown + RUNNER_OVERHEAD_BYTES <= memory

    def loaded_size(self):
        name = self.calibrator.name
        return next((size for loaded, size in Ollama.loaded_models() or [] if loaded == name), None)


class Ollama:
    RUNNER_CRASHES = (
        "runner failed",
        "runner process has terminated",
        "runner process no longer running",
        "command buffer execution failed",
        "panic:",
        "signal: abort",
        "signal: killed",
        "segmentation fault",
    )
    kept = set()
    embedders = set()

    @staticmethod
    def chat(
        name,
        messages,
        keep_alive,
        options,
        think=None,
        label=None,
        done_when=None,
        timeout=OLLAMA_SILENCE_TIMEOUT,
    ):
        unloading = not messages
        if unloading:
            return Ollama.streamed(name, messages, keep_alive, options, think, label, done_when, timeout)
        return journaled(
            {"chat": name, "messages": messages, "options": options, "think": think},
            lambda: Ollama.streamed(name, messages, keep_alive, options, think, label, done_when, timeout),
        )

    @staticmethod
    def streamed(name, messages, keep_alive, options, think, label, done_when, timeout):
        if keep_alive not in (0, "0"):
            Ollama.kept.add(name)
        payload = Ollama.chat_payload(name, messages, keep_alive, options, think)
        stream = Stream(label, done_when)
        try:
            with urllib.request.urlopen(Ollama.request("/api/chat", payload), timeout=timeout) as response:
                stream.read(response)
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc)
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}",
                stalled=isinstance(exc.reason, TimeoutError),
            )
        except (TimeoutError, ConnectionError) as exc:
            raise OllamaError(
                f"Ollama stopped responding ({exc or 'no data for ' + str(timeout) + 's'})", stalled=True
            )
        finally:
            stream.close()
        stream.final["message"] = {
            "role": "assistant",
            "content": stream.reply,
            "thinking": stream.thought_tail,
        }
        return stream.final

    @staticmethod
    def chat_payload(name, messages, keep_alive, options, think):
        payload = {"model": name, "messages": messages, "stream": True, "keep_alive": keep_alive}
        if think is not None:
            payload["think"] = think
        if options:
            payload["options"] = options
        return payload

    @staticmethod
    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        return urllib.request.Request(
            Config.ollama_url() + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )

    @staticmethod
    def http_error(exc, streamed=0):
        body = exc.read().decode("utf-8", errors="replace").strip()
        return OllamaError(
            f"Ollama returned HTTP {exc.code}: {body or exc.reason}",
            oom=Ollama.is_oom(body),
            crashed=Ollama.is_crash(body),
            streamed=streamed,
        )

    @staticmethod
    def is_oom(text):
        lowered = (text or "").lower()
        return "insufficient memory" in lowered or "out of memory" in lowered

    @staticmethod
    def is_crash(text):
        lowered = (text or "").lower()
        return any(sign in lowered for sign in Ollama.RUNNER_CRASHES)

    @staticmethod
    def read_raw(name, prompt, options, keep_alive=0, timeout=OLLAMA_SILENCE_TIMEOUT):
        if keep_alive not in (0, "0"):
            Ollama.kept.add(name)
        payload = {
            "model": name,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "keep_alive": keep_alive,
            "options": options,
        }
        try:
            with urllib.request.urlopen(
                Ollama.request("/api/generate", payload), timeout=timeout
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc)
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}")
        except (TimeoutError, ConnectionError) as exc:
            raise OllamaError(f"Ollama stopped responding ({exc})", stalled=True)
        if data.get("error"):
            raise OllamaError(
                f"Ollama error: {data['error']}",
                oom=Ollama.is_oom(data["error"]),
                crashed=Ollama.is_crash(data["error"]),
            )
        return data

    @staticmethod
    def unload(name):
        Ollama.kept.discard(name)
        try:
            Ollama.chat(name, [], 0, None)
        except OllamaError:
            pass

    @staticmethod
    def unload_kept():
        for name in sorted(Ollama.kept):
            Ollama.unload(name)

    @staticmethod
    def installed_models():
        data = Ollama.get_json("/api/tags")
        return (
            None if data is None else sorted(data.get("models", []), key=lambda model: model.get("name", ""))
        )

    @staticmethod
    def loaded_models():
        data = Ollama.get_json("/api/ps")
        if data is None:
            return None
        return [
            LoadedModel(model.get("name") or model.get("model") or "", model.get("size", 0))
            for model in data.get("models", [])
        ]

    @staticmethod
    def capabilities(name):
        return (Ollama.get_json("/api/show", {"model": name}, timeout=30) or {}).get("capabilities")

    @staticmethod
    def model_facts(name):
        data = Ollama.get_json("/api/show", {"model": name}, timeout=30) or {}
        info = data.get("model_info") or {}
        context = next(
            (
                value
                for key, value in info.items()
                if key.endswith(".context_length") and isinstance(value, int)
            ),
            None,
        )
        size = next(
            (
                installed_model.get("size")
                for installed_model in Ollama.installed_models() or []
                if installed_model.get("name") == name
            ),
            None,
        )
        return {
            "context_length": context,
            "capabilities": data.get("capabilities") or [],
            "format": (data.get("details") or {}).get("format") or "",
            "size": size,
        }

    @staticmethod
    def get_json(path, payload=None, timeout=10, errors=False):
        try:
            with urllib.request.urlopen(Ollama.request(path, payload), timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if not errors:
                return None
            try:
                return json.loads(exc.read().decode("utf-8", errors="replace"))
            except ValueError, OSError:
                return {"error": f"HTTP {exc.code}"}
        except urllib.error.URLError, OSError, ValueError:
            return None


class Stream:
    def __init__(self, label, done_when):
        self.label, self.done_when = label, done_when
        self.content, self.written, self.thinking, self.final = "", 0, 0, {}
        self.thought_tail, self.in_thought, self.tokens_told = "", False, 0
        self.started = self.last_print = time.perf_counter()
        self.progressed = False
        self.answer, self.complete_at = None, None

    def read(self, response):
        for raw in response:
            if raw.strip() and self.take(json.loads(raw.decode("utf-8"))):
                return

    def take(self, chunk):
        if "error" in chunk:
            reply_was_complete = self.answer is not None
            if reply_was_complete:
                return self.stop_early()
            raise self.error(chunk["error"])
        piece = self.add(chunk.get("message") or {})
        self.show()
        if chunk.get("done"):
            self.final = chunk if self.answer is None else dict(chunk, done_reason="complete")
            return True
        if self.answer is None and "\n" in piece and self.looping():
            return self.stop_looping()
        if self.answer is None and piece and self.complete(piece):
            self.answer, self.complete_at = self.content, time.perf_counter()
        if self.complete_at is not None and time.perf_counter() - self.complete_at > LAST_CHUNK_WAIT_SECONDS:
            return self.stop_early()
        return False

    def error(self, said):
        oom, crashed = Ollama.is_oom(str(said)), Ollama.is_crash(str(said))
        return OllamaError(
            f"Ollama error: {said}",
            oom=oom,
            crashed=crashed,
            in_reply=not (oom or crashed),
            partial=self.content,
            streamed=self.written + self.thinking,
        )

    def add(self, message):
        piece = message.get("content") or ""
        self.content += piece
        self.written += bool(piece)
        self.thinking += bool(message.get("thinking"))
        self.thought_tail = (self.thought_tail + (message.get("thinking") or ""))[
            -UNFINISHED_THOUGHT_KEPT_CHARS:
        ]
        self.turn_thinking(bool(message.get("thinking")) or (self.in_thought and not piece))
        return piece

    def turn_thinking(self, now):
        if now != self.in_thought:
            self.in_thought = now
            say.thinking(now)

    def show(self):
        now = time.perf_counter()
        if self.label and now - self.last_print >= PROGRESS_UPDATE_SECONDS:
            self.last_print, self.progressed = now, True
            self.tell_tokens()
            say.live(self.status())

    def tell_tokens(self):
        streamed = self.written + self.thinking
        if streamed > self.tokens_told:
            say.tokens(streamed - self.tokens_told)
            self.tokens_told = streamed

    def status(self):
        return say.going_on()

    def complete(self, piece):
        if not self.done_when or not self.may_end_here(piece):
            return False
        return self.done_when(self.content)

    def may_end_here(self, piece):
        return ">" in piece or "}" in piece or self.written % 40 == 0

    def looping(self):
        lines = self.content.rsplit("\n", LOOPING_REPEATED_LINES + 1)[-LOOPING_REPEATED_LINES - 1 : -1]
        return len(lines) == LOOPING_REPEATED_LINES and bool(lines[0].strip()) and len(set(lines)) == 1

    def stop_looping(self):
        self.final = {"done_reason": "length", "eval_count": self.written + self.thinking, "looped": True}
        return True

    def stop_early(self):
        self.final = {
            "done_reason": "complete",
            "eval_count": self.written + self.thinking,
            "early_stop": True,
        }
        return True

    @property
    def reply(self):
        return self.content if self.answer is None else self.answer

    def close(self):
        self.tell_tokens()
        self.turn_thinking(False)
        if self.progressed:
            say.end_live()


class OllamaError(Exception):
    def __init__(
        self, message, oom=False, stalled=False, in_reply=False, partial="", crashed=False, streamed=0
    ):
        super().__init__(message)
        self.oom, self.stalled, self.crashed = oom, stalled, crashed
        self.in_reply, self.partial = in_reply, partial
        self.streamed = streamed

    @property
    def about_thinking(self):
        return "think" in str(self).lower()
