import json
import signal
import socket
import sys
import time
from pathlib import Path

import pytest

import tinkerkit.cli  # noqa: F401
from tinkerkit.messages import say
from tinkerkit.model import Model, Ollama
from tinkerkit.project import Project
from tinkerkit.settings import Home, Log, Store

REAL_HOME = Home.root
MODEL = "fake-model"
MEASURED = {"model": MODEL, "calibrated": "2026-10-20 10:00", "max_prompt_tokens": 8192, "num_ctx": 32768,
            "max_reply_tokens": 16384, "fix_max_tokens": 4096, "think": "high", "chars_per_token": 3.0}


@pytest.fixture(autouse=True)
def tinker_home(tmp_path, monkeypatch):
    home = tmp_path / "tinker-home" / ".tinker"
    monkeypatch.setattr(Home, "root", home)
    return home


@pytest.fixture(autouse=True)
def quiet_voice():
    say.reset()
    yield
    say.reset()


@pytest.fixture(autouse=True)
def no_job():
    from tinkerkit.jobs import JOB_STOP_SIGNAL, Jobs
    handler = signal.getsignal(JOB_STOP_SIGNAL)
    Jobs.current = Jobs.journal = Jobs.ended_status = None
    Jobs.resumed_here = False
    yield
    Jobs.current = Jobs.journal = Jobs.ended_status = None
    Jobs.resumed_here = False
    signal.signal(JOB_STOP_SIGNAL, handler)

@pytest.fixture
def home_store(tinker_home):
    return Home.store()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(self, address):
        raise OSError(f"network is off in tests ({address})")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    for name in ("OLLAMA_API_KEY", "TINKER_CHANNELS", "NO_COLOR", "TINKER_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    no_stale_bytecode(monkeypatch)


def no_stale_bytecode(monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    return slept


class FakeOllama:
    def __init__(self):
        self.queue, self.calls, self.unloaded = [], [], []

    def reply(self, answer, when=None, times=1):
        for _ in range(times):
            self.queue.append((when, answer))
        return self

    def chat(self, name, messages, keep_alive, options, think=None, label=None, done_when=None, timeout=None):
        unloading = not messages
        if unloading:
            self.unloaded.append(name)
            return {"message": {"content": "", "thinking": ""}}
        self.calls.append({"name": name, "messages": messages, "options": options, "think": think,
                           "label": label or "", "keep_alive": keep_alive})
        index = next((position for position, (when, _) in enumerate(self.queue) if when is None
                      or when in (label or "")), None)
        if index is None:
            raise AssertionError(f"no scripted reply for the call {label!r}; queued: {[when for when, _ in self.queue]}")
        answer = self.queue.pop(index)[1]
        if callable(answer) and not isinstance(answer, type):
            answer = answer(messages)
        if isinstance(answer, BaseException):
            raise answer
        prompt = sum(len(message["content"]) for message in messages)
        final = {"done": True, "done_reason": "stop", "prompt_eval_count": max(60, prompt // 3), "eval_count": 20,
                 "eval_duration": 10 ** 8, "load_duration": 0}
        if isinstance(answer, dict):
            final.update({key: value for key, value in answer.items() if key != "content"})
            content, thinking = answer.get("content", ""), answer.get("thinking", "")
        else:
            content, thinking = answer, ""
        final["message"] = {"role": "assistant", "content": content, "thinking": thinking}
        return final

    def labels(self):
        return [call["label"] for call in self.calls]


@pytest.fixture
def fake_ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(Ollama, "chat", staticmethod(fake.chat))
    monkeypatch.setattr(Ollama, "loaded_models", staticmethod(lambda: []))
    return fake


@pytest.fixture
def model_log(tmp_path):
    return Log(Store(tmp_path / "model-log.db"), "calls")


@pytest.fixture
def make_model(model_log, fake_ollama):
    def make(**overrides):
        return Model(MODEL, dict(MEASURED, **overrides), 0, model_log)
    return make


@pytest.fixture
def measured_model(home_store):
    home_store.put("config", "model", MODEL)
    home_store.put("models", MODEL, MEASURED)
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
    def make(criteria=(), files=None, cycles=None, toolchain=None, **spec):
        run = {"id": "abc123", "task": "a calculator", "workdir": str(project_dir), "status": "running"}
        data = {"steps": [dict(criterion) for criterion in criteria], "counter": len(criteria), "files": files or {},
                "cycles": cycles if cycles is not None else [{"id": "base", "request": "a calculator", "phase": "recall",
                                                               "kind": "feature"}],
                "language": "Python", "toolchain": toolchain if toolchain is not None else dict(TOOLCHAIN), **spec}
        return Project(project_dir, run, data)
    return make


@pytest.fixture
def make_session(make_model):
    from tinkerkit.session import Session

    def make(project, model=None, internet=None, confirm=True, tracker=None):
        return Session(model or make_model(), project, internet=internet, confirm=confirm, tracker=tracker)
    return make


def criterion(id, test, behaviour="does it", status="todo", cycle="base", **more):
    return {"id": id, "title": behaviour[:80], "behaviour": behaviour, "test": test, "effort": 1, "source": "request",
            "status": status, "cycle": cycle, "history": [], **more}


def file_block(path, body):
    return f"<<<FILE {path}>>>\n{body}\n<<<END>>>"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan_of(folder):
    from tinkerkit.project import saved
    return saved(folder, "spec", "plan")


def record_of(folder):
    from tinkerkit.project import saved
    return saved(folder, "run", "record")
