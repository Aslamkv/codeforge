import ast
import builtins
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAYERS = ("messages", "settings", "jobs", "ledger", "commands", "prompts", "workspace", "model", "project", "fetcher", "web", "passages", "conversation",
          "channels", "knowledge", "slots", "docsite", "learning", "graph", "reading", "teaching", "exercising", "session", "planning", "building",
          "finishing", "cli")


def imports_of(module):
    tree = ast.parse((ROOT / "tinkerkit" / f"{module}.py").read_text(encoding="utf-8"))
    top, inner = set(), set()
    nested = {id(node) for function in ast.walk(tree) if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
              for node in ast.walk(function) if node is not function}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            names = [node.module] if node.module else [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tinkerkit"):
            names = [node.module.split(".", 1)[1]] if "." in node.module else [alias.name for alias in node.names]
        (inner if id(node) in nested else top).update(module_name.split(".")[0] for module_name in names)
    return top, inner


@pytest.mark.spec("ARCH-1")
@pytest.mark.parametrize("module", LAYERS)
def test_each_module_imports_only_the_modules_listed_above_it(module):
    above = set(LAYERS[:LAYERS.index(module)])
    top, inner = imports_of(module)
    assert top <= above, f"{module} imports {sorted(top - above)} at module level"
    allowed_inner = above | ({"channels"} if module == "conversation" else set())
    assert inner <= allowed_inner, f"{module} imports {sorted(inner - allowed_inner)} inside a function"


@pytest.mark.spec("ARCH-1")
def test_every_module_of_the_package_is_in_the_layers():
    modules = {path.stem for path in (ROOT / "tinkerkit").glob("*.py") if path.stem != "__init__"}
    assert modules == set(LAYERS)


@pytest.mark.spec("ARCH-2")
def test_without_the_channels_plugin_the_conversation_has_no_chats(monkeypatch):
    from tinkerkit import conversation
    real_import = builtins.__import__

    def no_channels(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "channels" and level == 1 or name == "tinkerkit.channels":
            raise ImportError("no plugin")
        return real_import(name, globals, locals, fromlist, level)
    monkeypatch.delitem(sys.modules, "tinkerkit.channels", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_channels)
    assert conversation.chat_channels() == []
    assert conversation.attach("a task", None) is None


@pytest.mark.spec("ARCH-3")
def test_only_the_model_web_and_channel_modules_open_connections():
    network = {"urllib.request", "http.client", "urlopen", "HTTPSConnection"}
    users = set()
    for path in [*(ROOT / "tinkerkit").glob("*.py"), ROOT / "tinker.py"]:
        text = path.read_text(encoding="utf-8")
        if any(f"import {name}" in text or f"{name}(" in text for name in network):
            users.add(path.stem)
    assert users <= {"model", "web", "fetcher", "channels", "knowledge"}, users
    knowledge = (ROOT / "tinkerkit" / "knowledge.py").read_text(encoding="utf-8")
    assert "urlopen(" not in knowledge, "knowledge goes online only through Internet and Ollama"
    runners = {path.stem for path in [*(ROOT / "tinkerkit").glob("*.py"), ROOT / "tinker.py"]
               if "subprocess.Popen(" in path.read_text(encoding="utf-8")}
    assert runners <= {"workspace", "settings", "conversation"}, runners


@pytest.mark.spec("ARCH-4")
def test_only_home_knows_where_the_home_is():
    for path in [*(ROOT / "tinkerkit").glob("*.py"), ROOT / "tinker.py"]:
        if path.stem == "settings":
            continue
        text = path.read_text(encoding="utf-8")
        assert "Home.root /" not in text and '"tinker.db"' not in text, f"{path.name} names a path inside the home"
        assert not re.search(r"\b[A-Z_]+_(FILE|DIR)\s*=\s*HOME\b", text), path.name


@pytest.mark.spec("ARCH-4")
def test_every_phase_works_through_the_session():
    from tinkerkit import session
    from tinkerkit.building import Verifier
    from tinkerkit.planning import SpecWriter
    assert issubclass(SpecWriter, session.Phase) and issubclass(Verifier, session.Worker)
    for module in ("planning", "building", "finishing", "cli"):
        top, inner = imports_of(module)
        assert "session" in top, f"{module} takes its phases from the session"
    tinker_py = (ROOT / "tinker.py").read_text(encoding="utf-8")
    assert "class Session" not in tinker_py
