"""The local model: Ollama's API, one request with its budgets and retries (Model), and measuring a
model once, quickly (Calibrator)."""

lazy import json
lazy import secrets
lazy import subprocess
lazy import sys
lazy import time
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request

from .messages import human_duration, say
lazy from .prompts import Part, Prompts, PromptTooLarge, Reply
from .settings import (
    CAPACITY_STEP,
    CHARS_PER_TOKEN,
    CRASH_NEAR,
    CRASH_RECOVERY,
    CRASH_RETRIES,
    CRASH_SHRINK,
    DEFAULT_CONFIG,
    DEFAULT_FIX_MAX,
    DEFAULT_THINK,
    EARLY_STOP_GRACE,
    LOOP_LINES,
    MAX_CHARS_PER_TOKEN,
    MEMORY_OVERHEAD,
    MIN_CHARS_PER_TOKEN,
    MIN_PROMPT_TOKENS,
    MIN_REPLY_TOKENS,
    OOM_RETRIES,
    OOM_SHRINK,
    PROBE_BATCH,
    PROGRESS_EVERY,
    PROMPT_HEADROOM,
    PROMPT_TARGET,
    REPLY_ERROR_RETRIES,
    REPLY_TARGET,
    REQUEST_TIMEOUT,
    STALL_RETRIES,
    THOUGHT_KEPT,
    Config,
    Home,
    first_line,
    now_text,
)
lazy from .workspace import ToolError


class Model:
    """The one model, with the settings its calibration measured: its context, its think setting,
    every prompt within its input budget, every reply up to the longest it writes (a fix: up to
    fix_max). Every exchange, and every detail of a call, goes to its log (log.add(event)); the person
    sees a live line while it writes (say.live) and a short one when it is asked again."""

    def __init__(self, name, settings, keep_alive, log, exclusive=True,
                 max_growth=DEFAULT_CONFIG["max_growth"]):
        self.name, self.settings = name, settings
        self.max_tokens, self.num_ctx = int(settings["max_prompt_tokens"]), int(settings["num_ctx"])
        self.think = settings.get("think")
        self.build_think = Config.get("build_think") if self.think not in (None, False) else None
        self.building = False  # set per phase: building calls think at build_think, planning at think
        self.max_reply, self.fix_max = int(settings["max_reply_tokens"]), int(settings["fix_max_tokens"])
        self.keep_alive = keep_alive
        self.options = {"num_batch": int(settings["num_batch"])} if settings.get("num_batch") else {}
        self.log, self.exclusive, self.max_growth = log, exclusive, max_growth
        self.think_rejected = False
        self.overthought = False    # the last call thought to its cap without an answer
        self.last_thought = ""      # ... and the end of what it thought
        self.crashes = 0            # runner crashes in this run (limits lowered after them hold for this run only)
        self.calibrated = (self.max_tokens, self.max_reply)
        self.base_gb = None
        self.chars_per_token = max(MIN_CHARS_PER_TOKEN, min(float(settings.get("chars_per_token") or CHARS_PER_TOKEN),
                                                            MAX_CHARS_PER_TOKEN))
        self.ratios = []  # chars per real token of recent prompts

    def ask(self, system, parts, label, **kw):
        """One request (ask_once); a prompt too large as it is gets every part shortened (exact
        ones too) rather than failing: PromptTooLarge only when even that does not fit."""
        try:
            return self.ask_once(system, parts, label, **kw)
        except PromptTooLarge:
            self.note(f"{label}: the prompt is too large as it is; shortening every part")
        relaxed = lambda limit: [p.relaxed() for p in (parts(limit) if callable(parts) else parts)]
        return self.ask_once(system, relaxed, label, **kw)

    def ask_once(self, system, parts, label, budget=None, done_when=None, max_reply=None, unthinking_retry=True,
                 think=None):
        """One request. parts: a list of Part, or a function limit -> list (rebuilt on a retry).
        The reply may be as long as the longest the model writes (budget: shorter), and at most
        max_reply: a smaller budget saves nothing on a reply that ends on its own, and one that
        is cut is asked again from the start.
        A reply cut off while still thinking is asked again with twice the budget up to max_reply,
        then (if unthinking_retry) without thinking; otherwise it counts as an empty reply (and
        self.overthought says so). A cut reply is finished when done_when says so or its blocks have
        all ended (done_when may wait for the model to run on). think: this call's think setting
        instead of the phase's."""
        if self.exclusive:
            self.evict_others()
        self.overthought = False
        chosen = self.think_now() if think is None else think
        tries = Tries(None if self.think_rejected else chosen, budget or self.max_reply)
        request = (system + Prompts.NO_TOOL_CALLS, parts, label, done_when, max_reply or self.max_reply,
                   unthinking_retry)
        reply = None
        while reply is None:
            reply = self.one_try(tries, *request)
        return reply

    def one_try(self, tries, system, parts, label, done_when, max_reply, unthinking_retry):
        """One try of a request: its reply, or None to try again (after what went wrong calls for)."""
        user = self.user_message(system, self.with_thought(parts, tries.thought), label, tries.shrink)
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
            return None if self.think_again(tries, data, shown, started, cap, unthinking_retry) else ""
        self.report(shown, data, started, cut)
        self.check_memory()
        return content

    def evict_others(self):
        """Unload every other model, so two never share memory (other apps reload theirs)."""
        for loaded, _ in Ollama.loaded_models() or []:
            if loaded and loaded != self.name:
                self.note(f"unloading {loaded} (exclusive mode: only one model in memory)")
                Ollama.unload(loaded)

    def think_now(self):
        """The think setting for the current phase: planning at the model's, building at build_think."""
        return self.build_think if self.building and self.build_think else self.think

    def user_message(self, system, parts, label, shrink):
        limit = self.limit_chars(system, int(self.max_tokens * OOM_SHRINK ** shrink))
        notes = []
        user = Part.assemble(parts(limit) if callable(parts) else parts, limit, notes)
        if notes:
            self.note(f"{label}: shortened {', '.join(notes)} to fit the prompt budget")
        return user

    @staticmethod
    def with_thought(parts, thought):
        """The prompt's parts with the end of an unfinished thought after them, for the retry that
        does not think (parts: a list, or a function limit -> list)."""
        if not thought.strip():
            return parts
        return Model.with_part(parts, Part("Your analysis so far (it ran out of room before an answer: conclude it "
                                           "and answer now, in exactly the reply format the instructions give)",
                                           thought, keep="tail", place="last"))

    @staticmethod
    def with_part(parts, part):
        """The prompt's parts with one more after them (parts: a list, or a function limit -> list)."""
        return (lambda limit: parts(limit) + [part]) if callable(parts) else list(parts) + [part]

    def reply_room(self, system, user):
        return self.num_ctx - int((len(system) + len(user)) / self.chars_per_token) - 64

    @staticmethod
    def call_label(label, think, budget):
        mode = "no thinking" if think is False else f"think {think if think is not None else 'default'}"
        return f"{label} [{mode}, reply up to {budget // 1024}K tok]"

    def send(self, system, user, tries, shown, done_when):
        return Ollama.chat(self.name, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                           self.keep_alive, self.request_options(tries.budget), tries.think, shown, done_when)

    def request_options(self, budget):
        options = dict(self.options)
        options.setdefault("num_ctx", self.num_ctx)
        options["num_predict"] = budget
        return options

    def after_error(self, exc, label, tries, prompt_tokens, shown, started):
        """What a failed request leads to: None to try it again (after what the failure calls for),
        or the reply to return (what arrived before a reply error, once tries run out)."""
        if exc.crashed:
            tries.crashes += 1
            if exc.streamed:
                self.after_reply_crash(exc, label, tries.crashes)
            else:
                self.after_crash(exc, label, prompt_tokens, tries.crashes)
            return None
        if exc.in_reply:
            tries.reply_errors += 1
            return exc.partial if self.reply_error_is_final(exc, shown, started, tries.reply_errors) else None
        self.recover(exc, label, tries)
        return None

    def after_crash(self, exc, label, prompt_tokens, crashes):
        """The runner died reading the prompt (some runners do above some size, which varies a
        little). The same prompt is tried again once the crashed runner has freed its memory; when
        it crashes again, the budget drops a little (CRASH_SHRINK) for the rest of this run only:
        the measured value stays in the model's settings, and the run ends with a note to measure
        again if crashes keep happening."""
        self.stop_after(exc, crashes, f"the last time reading a prompt of ~{prompt_tokens} tokens")
        what = f"crashed reading a prompt of ~{prompt_tokens} tokens"
        if crashes > 1 and prompt_tokens >= self.max_tokens * CRASH_NEAR:
            lowered = max(MIN_PROMPT_TOKENS, int(self.max_tokens * CRASH_SHRINK))
            what += f"; prompt budget {self.max_tokens} -> {lowered} tokens for this run"
            self.max_tokens = lowered
        self.crashed(exc, label, what, prompt_tokens=prompt_tokens)

    def after_reply_crash(self, exc, label, crashes):
        """The runner died while writing the reply (out of GPU memory, which fills token by token,
        or memory other apps took). The same call is made again once the runner has freed its
        memory; when it crashes again near the longest reply, that limit drops a little
        (CRASH_SHRINK) for the rest of this run only. A crash far below the limit is not about the
        reply's length: the limit stays."""
        self.stop_after(exc, crashes, f"the last time after writing {exc.streamed} tokens")
        what = f"crashed after writing {exc.streamed} tokens"
        if crashes > 1 and exc.streamed >= self.max_reply * CRASH_NEAR:
            lowered = max(MIN_REPLY_TOKENS, int(self.max_reply * CRASH_SHRINK))
            what += f"; longest reply {self.max_reply} -> {lowered} tokens for this run"
            self.max_reply, self.fix_max = lowered, min(self.fix_max, lowered)
        self.crashed(exc, label, what, written=exc.streamed)

    def stop_after(self, exc, crashes, last):
        if crashes > CRASH_RETRIES:
            raise OllamaError(f"{exc}\n{self.name} crashed {crashes} times on this call, {last}. If this keeps "
                              f"happening, measure the model again: tinker calibrate {self.name}", crashed=True)

    def crashed(self, exc, label, what, **facts):
        """Say what happened, log it, and give the crashed runner time to free its memory."""
        self.crashes += 1
        self.note(f"{label}: {self.name} {what} ({first_line(str(exc), 90)}); trying again in {CRASH_RECOVERY}s",
                  f"{self.name} crashed; trying again in {CRASH_RECOVERY}s")
        self.log.add({"label": label, "model": self.name, "error": str(exc), **facts,
                      "budget_now": self.max_tokens, "max_reply_now": self.max_reply})
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY)

    def reply_error_is_final(self, exc, shown, started, tries):
        """Ollama failed while streaming the reply (it could not parse what the model wrote). True when
        that was the last try: what arrived before the error counts as the reply (often nothing usable,
        which the caller treats as an attempt with no change; the run goes on)."""
        self.log.add({"label": shown, "model": self.name, "error": str(exc), "reply": exc.partial})
        if tries <= REPLY_ERROR_RETRIES:
            self.step(f"{shown}  ({first_line(str(exc), 120)}; asking again)", started)
            return False
        self.step(f"{shown}  ({first_line(str(exc), 120)} again; using the {len(exc.partial)} chars that came "
                  f"before it)", started)
        return True

    def recover(self, exc, label, tries):
        """What to change before trying a failed request again; re-raises when nothing helps."""
        think = tries.think
        if self.building and think == self.build_think != self.think and "think" in str(exc).lower():
            self.note(f"{label}: {self.name} rejected think={think!r} for building; using {self.think!r} as for planning")
            self.build_think = None
            tries.think = self.think
        elif exc.oom and tries.shrink < OOM_RETRIES:
            self.note(f"{label}: out of GPU memory; retrying this request with a prompt budget of "
                      f"{int(self.max_tokens * OOM_SHRINK ** (tries.shrink + 1))} tokens",
                      "Out of GPU memory; trying again with a shorter prompt")
            tries.shrink += 1
        elif exc.stalled and tries.stalls < STALL_RETRIES:
            self.note(f"{label}: {exc}; reloading the model and retrying",
                      f"{self.name} stopped responding; reloading it and trying again")
            Ollama.unload(self.name)
            tries.stalls += 1
        elif think not in (None, True, False) and "think" in str(exc).lower():
            self.note(f"{label}: {self.name} rejected think={think!r}; using think=true")
            self.think = tries.think = True
        elif think is not None and "think" in str(exc).lower():
            self.note(f"{label}: {self.name} rejected the think setting; retrying without it")
            self.think, self.think_rejected, tries.think = None, True, None
        else:
            raise exc

    def prompt_tokens(self, system, user):
        """The most tokens system + user can be (the lowest chars per token seen)."""
        return int((len(system) + len(user)) / self.chars_per_token)

    def log_exchange(self, system, user, shown, budget, data, started):
        """Learn from the real token count and log the exchange. Returns (content, cut)."""
        content = data.get("message", {}).get("content") or ""
        cut = data.get("done_reason") == "length"
        self.learn_chars_per_token(len(system) + len(user), data.get("prompt_eval_count", 0))
        self.log.add({"label": shown, "model": self.name, "system": system, "user": user,
                      "prompt_tokens": data.get("prompt_eval_count", 0),
                      "reply_tokens": data.get("eval_count", 0), "budget": budget, "cut": cut,
                      "seconds": round(time.perf_counter() - started, 1),
                      "load_seconds": round(data.get("load_duration", 0) / 1e9, 1), "reply": content})
        return content, cut

    def learn_chars_per_token(self, chars, tokens):
        """Learn chars per token from real prompts: the lowest recent ratio, capped."""
        if tokens and chars and tokens > 50:
            self.ratios = (self.ratios + [chars / tokens])[-20:]
            self.chars_per_token = max(MIN_CHARS_PER_TOKEN, min(min(self.ratios), MAX_CHARS_PER_TOKEN))

    @staticmethod
    def unfinished(content, done_when):
        return not content.strip() or (done_when is not None and not done_when(content)
                                       and not Reply.files_ended(content))

    def think_again(self, tries, data, shown, started, cap, unthinking_retry):
        """After a reply cut off while still thinking: True to ask again, with more room or without
        thinking (then given the thought so far to conclude: what was thought is not lost), or False
        to give up (an empty reply; self.overthought and self.last_thought say so)."""
        action, said = self.after_cut(data, tries.budget, cap, unthinking_retry)
        self.step(f"{shown}  ({said})", started)
        thought = data["message"].get("thinking", "")
        if action == "give up":
            self.overthought, self.last_thought = True, thought
            return False
        if action == "more":
            tries.budget = min(tries.budget * 2, cap)
        else:
            tries.think, tries.thought = False, thought
        return True

    @staticmethod
    def after_cut(data, budget, cap, unthinking_retry):
        """A reply cut off while still thinking: ("more" (twice the budget), "unthinking" (ask again
        without thinking) or "give up" (it counts as an empty reply), what to say about it)."""
        tokens = data.get("eval_count", 0)
        if budget < cap:
            return "more", (f"cut at {tokens} tokens while thinking; asking again with up to "
                            f"{min(budget * 2, cap) // 1024}K tokens")
        if unthinking_retry:
            return "unthinking", "cut at the most a reply may think; asking again without thinking"
        return "give up", f"thought to the {tokens}-token cap without an answer; counted as an attempt with no change"

    def report(self, shown, data, started, cut):
        tokens_in, tokens_out = data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        if tokens_in > self.max_tokens:
            self.note(f"note: prompt was {tokens_in} tokens, above the {self.max_tokens} budget estimate")
        if data.get("early_stop"):
            self.step(f"{shown}  (stopped as soon as the reply was complete, ~{tokens_out} tok)", started)
        else:
            self.step(f"{shown}  (prompt {tokens_in} tok, gen {data.get('eval_duration', 0) / 1e9:.1f}s"
                      f"/{tokens_out} tok, load {data.get('load_duration', 0) / 1e9:.1f}s)", started)
        if cut:
            self.note(f"reply cut at {tokens_out} tokens")

    def step(self, message, started):
        """A call's step, kept in the log with how long it took."""
        self.log.add({"note": message, "model": self.name, "seconds": round(time.perf_counter() - started, 1)})

    def note(self, detail, retry=""):
        """A detail of a call, kept in the log; when it is asked again, the person is told in plain words."""
        self.log.add({"note": detail, "model": self.name})
        if retry:
            say.retry(retry)

    def check_memory(self):
        """With the model kept loaded, unload it once it grows past max_growth x its first size."""
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
        """Its size in memory, in GB (None when it is not loaded, or Ollama cannot tell)."""
        return next((s / 1e9 for n, s in Ollama.loaded_models() or [] if n == self.name), None)

    def unload(self):
        if self.keep_alive not in (0, "0"):
            Ollama.unload(self.name)

    def ask_until_usable(self, system, parts, label, read, attempts=3, think=None, max_reply=None):
        """Ask for JSON until read(reply) gives a result; read raises ValueError and the like when
        a reply is not usable, and the reason goes into the next prompt. None when no reply was.
        think: this request's think setting instead of the phase's; max_reply: its longest reply."""
        for attempt in range(1, attempts + 1):
            reply = Reply(self.ask(system, parts, f"{label} (attempt {attempt})", done_when=Reply.json_complete,
                                   think=think, max_reply=max_reply))
            try:
                return read(reply)
            except (ValueError, ToolError, AttributeError, TypeError, KeyError) as exc:
                self.note(f"{label.lower()} not usable: {exc}",
                          "That answer could not be used; asking again" if attempt < attempts else "")
                parts = self.with_part(parts, Part("Note", f"Your previous reply was not usable ({exc}). Reply "
                                                           f"with ONLY the JSON.", True))
        return None

    def limit_chars(self, system, tokens=None):
        """Room for the user message, in chars, so system + user stay within the token budget."""
        return int((tokens or self.max_tokens) * PROMPT_HEADROOM * self.chars_per_token) - len(system) - 40

    def learned_chars_per_token(self):
        """What this run's prompts showed (None until enough were seen): the next run starts there."""
        return round(min(self.ratios), 2) if len(self.ratios) >= 5 else None


class Tries:
    """What changes between the tries of one request: the think setting and the reply budget, the
    prompt shrunk after running out of memory, a thought cut off to conclude, and the stalls,
    crashes and reply errors so far."""

    def __init__(self, think, budget):
        self.think, self.budget = think, budget
        self.shrink = self.stalls = self.crashes = self.reply_errors = 0
        self.thought = ""  # what a try cut off while thinking had thought (the next one concludes it)


class Models:
    """The models Ollama has, and what calibration measured for each (Tinker's "models"): the settings
    a run uses, and what a run learned about its model."""
    MEASURED = ("max_prompt_tokens", "num_ctx", "max_reply_tokens", "fix_max_tokens")  # what a run needs of them

    @staticmethod
    def installed(name):
        """The name, when Ollama has a model of exactly that name (as tinker models lists it)."""
        models = Ollama.installed_models()
        if models is None:
            raise SystemExit(f"Ollama does not answer at {Config.ollama_url()} (tinker config ollama_url URL).")
        names = [m.get("name", "") for m in models]
        if name not in names:
            close = [n for n in names if name.lower() in n.lower()]
            hint = f" Did you mean {' or '.join(close)}?" if close else ""
            raise SystemExit(f"Ollama has no model named '{name}'.{hint} Installed: {', '.join(names) or 'none'}")
        return name

    @staticmethod
    def selected():
        """(name, settings) of the model in use: the one tinker use-model selected, measured."""
        name = Config.get("model")
        if not name:
            raise SystemExit("No model in use. Measure one Ollama has with tinker calibrate NAME, then select it with "
                             "tinker use-model NAME (tinker models lists them).")
        settings = Models.settings(name)
        if settings is None:
            raise SystemExit(f"{name}, the model in use, has not been measured: tinker calibrate {name}")
        broken = Models.unusable_measurements(settings)
        if broken:
            raise SystemExit(f"What was measured for {name} has no usable {', '.join(broken)}: measure the model "
                             f"again with tinker calibrate {name}")
        return name, settings

    @staticmethod
    def unusable_measurements(settings):
        return [key for key in Models.MEASURED if isinstance(settings.get(key), bool)
                or not isinstance(settings.get(key), int) or settings[key] < 1]

    @staticmethod
    def settings(name):
        """A model's measured settings, or None when it was never calibrated."""
        settings = Home.store().get("models", name)
        return settings if isinstance(settings, dict) else None

    @staticmethod
    def keep(name, settings):
        Home.store().put("models", name, settings)

    @staticmethod
    def remember(model):
        """What this run learned about its model, for the next run: the chars per token its prompts
        showed (prompts are packed from there). Limits lowered after crashes are not kept: the
        measured ones stay, and a note says to measure again if crashes keep happening."""
        if model.crashes:
            Models.say_crashes(model)
        learned, settings = model.learned_chars_per_token(), Models.settings(model.name)
        if learned and settings and settings.get("chars_per_token") != learned:
            Home.store().update("models", model.name, lambda kept: dict(kept or settings, chars_per_token=learned))

    @staticmethod
    def say_crashes(model):
        lowered = [f"{name} {now} (measured {was})" for name, now, was in
                   (("prompt budget", model.max_tokens, model.calibrated[0]),
                    ("longest reply", model.max_reply, model.calibrated[1])) if now < was]
        say.warn(f"{model.name} crashed {model.crashes} time(s) this run" + (f"; lowered for this run only: "
                 f"{', '.join(lowered)}" if lowered else "") + f". If crashes keep happening, measure it again: "
                 f"tinker calibrate {model.name}")

    @staticmethod
    def context_for(budget, max_reply, context_length):
        """num_ctx for a prompt budget and a longest reply: room for both (a runner that keeps to it
        then never cuts a reply short), within what the model supports."""
        needed = -(-(budget + max_reply + 512) // 1024) * 1024
        return min(needed, context_length) if context_length else needed

    @staticmethod
    def reply_limits(max_reply):
        """The reply settings for a longest reply: it, and the budget of a fix under it."""
        return {"max_reply_tokens": max_reply,
                "fix_max_tokens": min(DEFAULT_FIX_MAX, max_reply)}


class Calibrator:
    """Measures a model once, quickly, so every run uses it at its limits without trial and error.
    A run needs at most PROMPT_TARGET tokens of prompt and REPLY_TARGET of reply, so the question
    is whether a context of that size fits in GPU memory. It is answered by reading, not writing:
    a reply fills memory exactly as a prompt of the same length does, and a model reads several
    times faster than it writes. In order:
    1. fit, with no model call: the model's weights against the machine's GPU memory; a model
       that cannot fit stops here, with what to do;
    2. one short chat on a fresh load: the think setting it accepts, the chars per token of text
       like a real prompt, how fast it writes;
    3. one read of the whole context, freshly loaded as a run loads it (a GGUF runner reserves the
       context when it loads, so it reads only a full prompt, and in pieces when one piece
       crashes it);
    4. only when that fails: the largest context that reads, found to within CAPACITY_STEP, and
       shared between the prompt (up to PROMPT_TARGET) and the reply.
    A probe is a prompt of an exact number of tokens: random digits sent as is (no chat template),
    one token each in most tokenizers (one that counts them otherwise is measured by the read).
    Kept in Tinker's "models" (tinker models and tinker config show it)."""

    # text like a real prompt (code, a docstring, a comment): a run sizes its prompts from its chars per token
    FILLER = """def due_items(items, today):
    \"\"\"The items due by today, oldest first: each a dict with "id", "title" and "due" (an ISO date).\"\"\"
    found = [item for item in items if item.get("due") and item["due"] <= today.isoformat()]
    return sorted(found, key=lambda item: (item["due"], item["id"]))  # ties keep a stable order


# A request asks for the due list; the reply is JSON: {"items": [...], "count": 3}.
"""

    def __init__(self, name):
        self.name = name
        self.tokens_per_digit = 1.0  # a digit is one token in most tokenizers; measured by each read
        self.best = {}               # Ollama's stats for the largest prompt read
        self.load_seconds = None
        self.options = {}            # num_batch, when reading a prompt in pieces avoids a crash
        self.measured_ratio = None   # chars per token of prompt-like text
        self.history = []            # [tokens, "read" | "crashed"] of every probe, kept in the settings
        self.write_tps = None        # tokens written per second

    def run(self):
        say.title(f"Calibrating {self.name} (once per model; a few minutes)", "📏")
        started = time.perf_counter()
        facts = Ollama.model_facts(self.name)
        self.check_fit(facts)
        think = self.first_chat(facts)
        target = min(PROMPT_TARGET + REPLY_TARGET, facts["context_length"] or PROMPT_TARGET + REPLY_TARGET)
        context, crash = self.capacity(target, facts)
        budget, max_reply = self.shared(context)
        settings = self.settings(facts, think, budget, context, crash, max_reply)
        Ollama.unload(self.name)
        Models.keep(self.name, settings)
        say.took(f"Calibrated {self.name}: prompts up to {budget} tokens, replies up to {max_reply}, think {think}",
                 time.perf_counter() - started)
        return settings

    def check_fit(self, facts):
        """The weights against the GPU memory, with no model call: a model that cannot fit stops
        here, with what to do (a guess of the context is not worth more: memory per token depends on
        an architecture Ollama does not always describe, so the context is measured instead)."""
        memory, size = self.gpu_memory(), facts.get("size") or 0
        if not memory or not size:
            return
        with say.section():
            say.info(f"{size / 1e9:.1f} GB of weights; {memory / 1e9:.1f} GB of GPU memory", "🧮")
        if size + MEMORY_OVERHEAD > memory:
            raise SystemExit(self.does_not_fit(size, memory))

    def does_not_fit(self, size, memory):
        return (f"{self.name} needs {size / 1e9:.1f} GB for its weights alone, and the GPU may use "
                f"{memory / 1e9:.1f} GB: choose a smaller model or quantization"
                + (f", or let the GPU use more memory (e.g. sudo sysctl iogpu.wired_limit_mb="
                   f"{int(self.total_memory() * 0.9 / 2**20)})" if sys.platform == "darwin" else "")
                + ".")

    @classmethod
    def gpu_memory(cls):
        """The memory a model may use: on a Mac, the GPU's wired limit (iogpu.wired_limit_mb when it
        is set, else macOS's default share of the memory); elsewhere the machine's memory."""
        total = cls.total_memory()
        if sys.platform != "darwin" or not total:
            return total
        try:
            limit = int(subprocess.run(["sysctl", "-n", "iogpu.wired_limit_mb"], capture_output=True,
                                       text=True).stdout or 0)
        except OSError, ValueError:
            limit = 0
        return limit * 2**20 if limit > 0 else int(total * (0.75 if total > 36e9 else 0.67))

    @staticmethod
    def total_memory():
        """The machine's memory in bytes (None when it cannot be told)."""
        try:
            if sys.platform == "darwin":
                return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
            with open("/proc/meminfo") as info:
                return int(next(line for line in info if line.startswith("MemTotal")).split()[1]) * 1024
        except OSError, ValueError, StopIteration:
            return None

    def first_chat(self, facts):
        """One short chat on a fresh load: the think setting the model accepts ("high", else true;
        none when it does not think), the chars per token of prompt-like text, and how fast it
        writes. Returns the think setting."""
        thinks = not facts["capabilities"] or "thinking" in facts["capabilities"]
        content = self.FILLER * 4
        for think in ((DEFAULT_THINK, True) if thinks else (None,)):
            try:
                data = Ollama.chat(self.name, [{"role": "user", "content": content + "\nSay OK."}], 0,
                                   {"num_ctx": 4096, "num_predict": 64}, think)
            except OllamaError as exc:
                if think is not None and "think" in str(exc).lower():
                    continue
                raise SystemExit(f"{self.name} does not run: {exc}")
            self.measure_chat(content, data)
            with say.section():
                say.info(f"Thinks with {think!r}; {self.safe_ratio()} chars per token; writes {self.write_tps} "
                         f"tokens a second", "🧠")
            return think
        return None

    def measure_chat(self, content, data):
        """From a chat's stats: how long the model took to load, its chars per token, how fast it writes."""
        self.load_seconds = round(data.get("load_duration", 0) / 1e9, 1)
        read, written = data.get("prompt_eval_count") or 0, data.get("eval_count") or 0
        self.measured_ratio = len(content) / read if read > 50 else None
        seconds = (data.get("eval_duration") or 0) / 1e9
        self.write_tps = round(written / seconds, 1) if written and seconds else None

    def capacity(self, target, facts):
        """(the largest context that reads, the smallest that failed or None): the target first;
        when it fails, halving the gap until it is within CAPACITY_STEP."""
        gguf = (facts.get("format") or "").lower() == "gguf"
        if self.reads(target, gguf):
            return target, None
        good, bad = 0, target
        while bad - good > CAPACITY_STEP * bad and bad > MIN_PROMPT_TOKENS + MIN_REPLY_TOKENS:
            middle = (good + bad) // 2
            good, bad = (middle, bad) if self.reads(middle, gguf) else (good, middle)
        if good < MIN_PROMPT_TOKENS + MIN_REPLY_TOKENS:
            raise SystemExit(f"{self.name} cannot hold even {MIN_PROMPT_TOKENS + MIN_REPLY_TOKENS} tokens of "
                             f"context on this machine: choose a smaller model or quantization.")
        return good, bad

    def reads(self, context, gguf):
        """Whether the model, freshly loaded with this context, reads it: all of it (memory then
        holds as much as a full prompt and reply), or for a GGUF model a full prompt (its runner
        reserved the whole context when it loaded); a GGUF runner that crashes on one piece is
        tried once more in pieces (num_batch)."""
        tokens = min(context, PROMPT_TARGET) if gguf else context
        with say.section():
            say.step(f"Reading {tokens} tokens with a context of {context} (about "
                     f"{round(tokens / max(self.read_speed(), 1))}s)…", "📏")
        if self.probe(tokens, context):
            return True
        if gguf and not self.options:
            self.options = {"num_batch": PROBE_BATCH}
            if self.probe(tokens, context):
                with say.section(), say.section():
                    say.done(f"Read in pieces of {PROBE_BATCH} tokens (num_batch)")
                return True
            self.options = {}
        return False

    def probe(self, tokens, num_ctx):
        """Have the model read a prompt of exactly `tokens` tokens on a fresh load, as a run loads
        it: the count Ollama reports, or None when it fails (then after the crashed runner had time
        to free its memory)."""
        digits = max(1, round(tokens / self.tokens_per_digit))
        prompt = "".join(secrets.choice("0123456789") for _ in range(digits))
        timeout = max(REQUEST_TIMEOUT, int(tokens / max(self.read_speed() / 3, 1)))
        try:
            data = Ollama.read_raw(self.name, prompt, {"num_ctx": num_ctx, "num_predict": 1, **self.options}, timeout)
        except OllamaError as exc:
            self.probe_failed(exc, tokens)
            return None
        return self.probe_read(data, tokens, digits)

    def probe_failed(self, exc, tokens):
        """Noted, after the crashed runner had time to free its memory; Ollama gone ends calibration."""
        if Ollama.installed_models() is None:
            raise SystemExit(f"Ollama stopped answering during calibration: {exc}")
        with say.section(), say.section():
            say.fail(f"Could not read it ({first_line(str(exc), 100)})")
        self.history.append([tokens, "crashed"])
        Ollama.unload(self.name)
        time.sleep(CRASH_RECOVERY)

    def probe_read(self, data, tokens, digits):
        """The tokens read, noted with what they teach: tokens per digit, and the largest read so far."""
        read = data.get("prompt_eval_count") or tokens
        self.tokens_per_digit = read / digits
        self.history.append([read, "read"])
        if read >= self.best.get("prompt_eval_count", 0):
            self.best = data
        with say.section(), say.section():
            say.done(f"Read {read} tokens in {data.get('prompt_eval_duration', 0) / 1e9:.0f}s")
        return read

    def read_speed(self):
        """Tokens a second it reads (from the last read; at first a guess from how fast it writes)."""
        if self.best.get("prompt_eval_duration"):
            return self.best["prompt_eval_count"] / self.best["prompt_eval_duration"] * 1e9
        return (self.write_tps or 25) * 4

    @staticmethod
    def shared(context):
        """(prompt budget, longest reply) of a context: the prompt up to PROMPT_TARGET and no more
        than a third of it, the reply the rest, up to REPLY_TARGET."""
        budget = min(PROMPT_TARGET, max(MIN_PROMPT_TOKENS, context // 3))
        return budget, max(MIN_REPLY_TOKENS, min(REPLY_TARGET, context - budget))

    def settings(self, facts, think, budget, context, crash, max_reply):
        return {"model": self.name, "calibrated": now_text(),
                "max_prompt_tokens": budget, "largest_prompt_read": context, "crashed_reading": crash,
                "num_ctx": Models.context_for(budget, max_reply, facts["context_length"]),
                "context_length": facts["context_length"], "think": think, **Models.reply_limits(max_reply),
                "chars_per_token": self.safe_ratio(), "num_batch": self.options.get("num_batch"),
                "read_tokens_per_s": round(self.read_speed()) if self.best else None,
                "write_tokens_per_s": self.write_tps, "load_seconds": self.load_seconds, "probes": self.history,
                "tokens_per_digit": round(self.tokens_per_digit, 4)}

    def safe_ratio(self):
        """The chars per token prompts are sized with: what was measured, 3% lower (prompts with
        more markup have fewer chars per token; each run then learns the real ones)."""
        if not self.measured_ratio:
            return CHARS_PER_TOKEN
        return round(max(MIN_CHARS_PER_TOKEN, min(self.measured_ratio * 0.97, MAX_CHARS_PER_TOKEN)), 2)


class Ollama:
    """Ollama's HTTP API (the setting ollama_url): chat calls streamed with live progress, raw
    prompts for calibration, and what it says about its models."""
    RUNNER_CRASHES = ("runner failed", "runner process has terminated", "runner process no longer running",
                      "command buffer execution failed", "panic:", "signal: abort", "signal: killed",
                      "segmentation fault")

    @staticmethod
    def chat(name, messages, keep_alive, options, think=None, label=None, done_when=None,
             timeout=REQUEST_TIMEOUT):
        """Stream one chat request (a live line in plain words when labelled, say.live). done_when(content)
        says when the reply is complete: the stream is
        closed there, which stops Ollama generating. Returns the final stats chunk with message.content
        set to the reply."""
        payload = Ollama.chat_payload(name, messages, keep_alive, options, think)
        stream = Stream(label, done_when)
        try:
            with urllib.request.urlopen(Ollama.request("/api/chat", payload), timeout=timeout) as response:
                stream.read(response)
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc)
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}",
                              stalled=isinstance(exc.reason, TimeoutError))
        except (TimeoutError, ConnectionError) as exc:
            raise OllamaError(f"Ollama stopped responding ({exc or 'no data for ' + str(timeout) + 's'})",
                              stalled=True)
        finally:
            stream.close()
        stream.final["message"] = {"role": "assistant", "content": stream.reply, "thinking": stream.thought}
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
        """A request to one of Ollama's endpoints: a GET, or a POST of payload as JSON."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        return urllib.request.Request(Config.ollama_url() + path, data=data, headers={"Content-Type": "application/json"},
                                      method="GET" if payload is None else "POST")

    @staticmethod
    def http_error(exc, streamed=0):
        """The OllamaError for an HTTP error, with what Ollama said (out of memory? a crash?)."""
        body = exc.read().decode("utf-8", errors="replace").strip()
        return OllamaError(f"Ollama returned HTTP {exc.code}: {body or exc.reason}", oom=Ollama.is_oom(body),
                           crashed=Ollama.is_crash(body), streamed=streamed)

    @staticmethod
    def is_oom(text):
        lowered = (text or "").lower()
        return "insufficient memory" in lowered or "out of memory" in lowered

    @staticmethod
    def is_crash(text):
        """The model's runner died (not a refusal of the request): retrying after a pause can work."""
        lowered = (text or "").lower()
        return any(sign in lowered for sign in Ollama.RUNNER_CRASHES)

    @staticmethod
    def read_raw(name, prompt, options, timeout=REQUEST_TIMEOUT):
        """Have the model read a prompt sent as is (no chat template: calibration sends an exact
        number of tokens), on a fresh load and without streaming. Ollama's stats."""
        payload = {"model": name, "prompt": prompt, "raw": True, "stream": False, "keep_alive": 0, "options": options}
        try:
            with urllib.request.urlopen(Ollama.request("/api/generate", payload), timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise Ollama.http_error(exc)
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {Config.ollama_url()}: {exc.reason}")
        except (TimeoutError, ConnectionError) as exc:
            raise OllamaError(f"Ollama stopped responding ({exc})", stalled=True)
        if data.get("error"):
            raise OllamaError(f"Ollama error: {data['error']}", oom=Ollama.is_oom(data["error"]),
                              crashed=Ollama.is_crash(data["error"]))
        return data

    @staticmethod
    def unload(name):
        try:
            Ollama.chat(name, [], 0, None)
        except OllamaError:
            pass

    @staticmethod
    def installed_models():
        """[{name, size, details}] of the models Ollama has (None when Ollama does not answer)."""
        data = Ollama.get_json("/api/tags")
        return None if data is None else sorted(data.get("models", []), key=lambda m: m.get("name", ""))

    @staticmethod
    def loaded_models():
        """[(name, size_bytes)] of the models Ollama has loaded (None if unknown)."""
        data = Ollama.get_json("/api/ps")
        if data is None:
            return None
        return [(m.get("name") or m.get("model") or "", m.get("size", 0)) for m in data.get("models", [])]

    @staticmethod
    def model_facts(name):
        """What Ollama says about a model: its longest context and what it can do (thinking...)."""
        data = Ollama.get_json("/api/show", {"model": name}, timeout=30) or {}
        info = data.get("model_info") or {}
        context = next((v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)), None)
        size = next((m.get("size") for m in Ollama.installed_models() or [] if m.get("name") == name), None)
        return {"context_length": context, "capabilities": data.get("capabilities") or [],
                "format": (data.get("details") or {}).get("format") or "", "size": size}

    @staticmethod
    def get_json(path, payload=None, timeout=10, errors=False):
        """GET (or POST payload to) an Ollama endpoint; its JSON, or None when Ollama does not answer
        (errors: an error's own JSON, {"error": ...}, instead of None)."""
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
    """Reads a streamed reply: collects its content, shows what the model is doing (say.live), and
    stops early once done_when(content) says the reply is complete, or once the model loops (it writes
    one line again and again): that reply is cut, as one that ran out of room is."""

    def __init__(self, label, done_when):
        self.label, self.done_when = label, done_when
        self.content, self.written, self.thinking, self.final = "", 0, 0, {}
        self.thought = ""  # the end of what the model thought (kept for a reply cut while thinking)
        self.started = self.last_print = time.perf_counter()
        self.progressed = False
        self.answer, self.complete_at = None, None  # the reply as it was when complete, and when

    def read(self, response):
        for raw in response:
            if raw.strip() and self.take(json.loads(raw.decode("utf-8"))):
                return

    def take(self, chunk):
        """One chunk; True when the reply is over. Once the reply is complete, Ollama's last chunk
        (with the real prompt token count, which keeps the prompt budget right) is awaited a few
        seconds more; anything the model writes meanwhile is not part of the reply."""
        if "error" in chunk:
            if self.answer is not None:
                return self.stop_early()  # the reply was complete before Ollama failed on what followed
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
        if self.complete_at is not None and time.perf_counter() - self.complete_at > EARLY_STOP_GRACE:
            return self.stop_early()
        return False

    def error(self, said):
        oom, crashed = Ollama.is_oom(str(said)), Ollama.is_crash(str(said))
        return OllamaError(f"Ollama error: {said}", oom=oom, crashed=crashed, in_reply=not (oom or crashed),
                           partial=self.content, streamed=self.written + self.thinking)

    def add(self, message):
        """A message's piece of the reply and of the thought, counted; the reply's piece."""
        piece = message.get("content") or ""
        self.content += piece
        self.written += bool(piece)
        self.thinking += bool(message.get("thinking"))
        self.thought = (self.thought + (message.get("thinking") or ""))[-THOUGHT_KEPT:]
        return piece

    def show(self):
        now = time.perf_counter()
        if self.label and now - self.last_print >= PROGRESS_EVERY:
            self.last_print, self.progressed = now, True
            say.live(self.status(now))

    def status(self, now):
        return plainly(self.written, self.thinking, now - self.started)

    def complete(self, piece):
        """Checked when a piece may end a block or an object (or every 40 pieces)."""
        if not self.done_when or not (">" in piece or "}" in piece or self.written % 40 == 0):
            return False
        return self.done_when(self.content)

    def looping(self):
        """Whether the last LOOP_LINES finished lines of the reply are one line, written again and again."""
        lines = self.content.rsplit("\n", LOOP_LINES + 1)[-LOOP_LINES - 1:-1]
        return len(lines) == LOOP_LINES and bool(lines[0].strip()) and len(set(lines)) == 1

    def stop_looping(self):
        self.final = {"done_reason": "length", "eval_count": self.written + self.thinking, "looped": True}
        return True

    def stop_early(self):
        self.final = {"done_reason": "complete", "eval_count": self.written + self.thinking, "early_stop": True}
        return True

    @property
    def reply(self):
        return self.content if self.answer is None else self.answer

    def close(self):
        if self.progressed:
            say.end_live()


def plainly(written, thinking, seconds):
    """What the model is doing and for how long, as a person reads it: "thinking… 1m 41s (101s)"."""
    seconds = round(seconds)
    doing = "writing its answer" if written else "thinking" if thinking else "reading"
    return f"{doing}… {human_duration(seconds)}" + (f" ({seconds}s)" if seconds >= 60 else "")


class OllamaError(Exception):
    """A call to Ollama that failed. oom: out of memory; crashed: the model's runner died (some
    runners do, reading a prompt above some size); stalled: no answer; in_reply: Ollama failed
    while streaming the reply (it could not parse what the model wrote, e.g. text that looks like a
    tool call): partial is what arrived before."""

    def __init__(self, message, oom=False, stalled=False, in_reply=False, partial="", crashed=False, streamed=0):
        super().__init__(message)
        self.oom, self.stalled, self.crashed = oom, stalled, crashed
        self.in_reply, self.partial = in_reply, partial
        self.streamed = streamed  # tokens the model had written (thinking included) when it failed
