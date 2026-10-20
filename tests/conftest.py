"""Shared fixtures. Every test runs with its own Tinker home (never the real .tinker next to tinker.py, which
holds live settings, secrets and locks), with no network, and with a scripted Ollama when it needs a model."""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

import tinker  # noqa: F401  (imported so its path constants are redirected too)
import tinkerkit.settings as settings
from tinkerkit.model import Model, Ollama
from tinkerkit.project import Project

REAL_HOME = settings.HOME
MODEL = "fake-model"
MEASURED = {"model": MODEL, "calibrated": "2026-10-20 10:00", "max_prompt_tokens": 8192, "num_ctx": 32768,
            "max_reply_tokens": 16384, "fix_max_tokens": 4096, "think": "high", "chars_per_token": 3.0}


def tinker_modules():
    return [module for name, module in list(sys.modules.items())
            if module is not None and (name == "tinker" or name.startswith("tinkerkit"))]


@pytest.fixture(autouse=True)
def tinker_home(tmp_path, monkeypatch):
    """Tinker's home moved into the test's own folder: every path constant under the real home, in every
    module that imported one, points into it; the cached config is dropped."""
    home = tmp_path / "tinker-home" / ".tinker"
    for module in tinker_modules():
        for attr, value in list(vars(module).items()):
            if isinstance(value, Path) and (value == REAL_HOME or REAL_HOME in value.parents):
                monkeypatch.setattr(module, attr, home / value.relative_to(REAL_HOME))
    monkeypatch.setattr(settings.Config, "values", None)
    for module in tinker_modules():
        for attr, value in vars(module).items():
            assert not (isinstance(value, Path) and (value == REAL_HOME or REAL_HOME in value.parents)), \
                f"{module.__name__}.{attr} still points at the real Tinker home"
    return home


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test reaches the network or the person's Ollama: a connection in this process fails as if there
    were no network (a test that needs an answer fakes the call it makes)."""
    def refuse(self, address):
        raise OSError(f"network is off in tests ({address})")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    for name in ("OLLAMA_API_KEY", "TINKER_CHANNELS", "NO_COLOR", "TINKER_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    # a project file rewritten within the second its last test run compiled it, at the same size, would load its
    # stale bytecode (a test-speed artifact: a real model call takes longer than that)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture
def no_sleep(monkeypatch):
    """time.sleep returns at once (retries, crash recovery and waits take no time); the seconds asked are
    kept in the list it returns."""
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    return slept


class FakeOllama:
    """Ollama's chat, scripted: each call takes the first queued answer whose `when` is in the call's
    label (or that has none). An answer is the reply's text, a dict (the final chunk, merged), an
    exception (raised), or a function of the messages that gives one of those."""

    def __init__(self):
        self.queue, self.calls, self.unloaded = [], [], []

    def reply(self, answer, when=None, times=1):
        for _ in range(times):
            self.queue.append((when, answer))
        return self

    def chat(self, name, messages, keep_alive, options, think=None, label=None, done_when=None, timeout=None):
        if not messages:  # an unload
            self.unloaded.append(name)
            return {"message": {"content": "", "thinking": ""}}
        self.calls.append({"name": name, "messages": messages, "options": options, "think": think,
                           "label": label or "", "keep_alive": keep_alive})
        index = next((i for i, (when, _) in enumerate(self.queue) if when is None or when in (label or "")), None)
        if index is None:
            raise AssertionError(f"no scripted reply for the call {label!r}; queued: {[w for w, _ in self.queue]}")
        answer = self.queue.pop(index)[1]
        if callable(answer) and not isinstance(answer, type):
            answer = answer(messages)
        if isinstance(answer, BaseException):
            raise answer
        prompt = sum(len(m["content"]) for m in messages)
        final = {"done": True, "done_reason": "stop", "prompt_eval_count": max(60, prompt // 3), "eval_count": 20,
                 "eval_duration": 10 ** 8, "load_duration": 0}
        if isinstance(answer, dict):
            final.update({k: v for k, v in answer.items() if k != "content"})
            content, thinking = answer.get("content", ""), answer.get("thinking", "")
        else:
            content, thinking = answer, ""
        final["message"] = {"role": "assistant", "content": content, "thinking": thinking}
        return final

    def labels(self):
        return [call["label"] for call in self.calls]


@pytest.fixture
def fake_ollama(monkeypatch):
    """Ollama's chat, scripted (FakeOllama); no other model is loaded."""
    fake = FakeOllama()
    monkeypatch.setattr(Ollama, "chat", staticmethod(fake.chat))
    monkeypatch.setattr(Ollama, "loaded_models", staticmethod(lambda: []))
    return fake


@pytest.fixture
def make_model(tmp_path, fake_ollama):
    """A real Model on the scripted Ollama, with measured settings (overridable)."""
    def make(**overrides):
        return Model(MODEL, dict(MEASURED, **overrides), 0, tmp_path / "model-log.jsonl")
    return make


@pytest.fixture
def measured_model(tinker_home):
    """The fake model selected in config.json and measured in models/ (as tinker use-model leaves it)."""
    settings.JsonFile.write(tinker_home / "config.json", {"model": MODEL})
    settings.JsonFile.write(tinker_home / "models" / f"{MODEL}.json", MEASURED)
    settings.Config.values = None
    return MODEL


@pytest.fixture
def project_dir(tmp_path):
    folder = tmp_path / "project"
    (folder / ".tinker").mkdir(parents=True)
    return folder


PY = sys.executable
PYTEST = f'"{PY}" -m pytest -q -p no:cacheprovider'
TOOLCHAIN = {"setup": [], "activate": "", "use": [], "run": f'"{PY}" calc.py', "check": f'"{PY}" -c "import calc"',
             "test_all": PYTEST, "test_one": PYTEST + " {file}::{name}", "format": "", "lint": "none"}


@pytest.fixture
def make_project(project_dir):
    """A Project with a run record and a spec (criteria, files, cycles and toolchain may be given)."""
    def make(criteria=(), files=None, cycles=None, toolchain=None, **spec):
        run = {"id": "abc123", "task": "a calculator", "workdir": str(project_dir), "status": "running"}
        data = {"steps": [dict(c) for c in criteria], "counter": len(criteria), "files": files or {},
                "cycles": cycles if cycles is not None else [{"id": "base", "request": "a calculator", "phase": "recall",
                                                               "kind": "feature"}],
                "language": "Python", "toolchain": toolchain if toolchain is not None else dict(TOOLCHAIN), **spec}
        return Project(project_dir, run, data)
    return make


@pytest.fixture
def make_session(make_model):
    """A real Session (tinker.py) on a Project and the scripted model."""
    def make(project, model=None, internet=None):
        return tinker.Session(model or make_model(), project, internet=internet)
    return make


def criterion(id, test, behaviour="does it", status="todo", cycle="base", **more):
    return {"id": id, "title": behaviour[:80], "behaviour": behaviour, "test": test, "effort": 1, "source": "request",
            "status": status, "cycle": cycle, "history": [], **more}


def file_block(path, body):
    return f"<<<FILE {path}>>>\n{body}\n<<<END>>>"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
