import ast
import builtins
import io
import re
import runpy
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAYERS = (
    "messages",
    "text",
    "store",
    "settings",
    "commands",
    "jobs",
    "ledger",
    "prompts",
    "workspace",
    "model",
    "project",
    "fetcher",
    "passages",
    "web",
    "conversation",
    "channels",
    "knowledge",
    "slots",
    "learning",
    "graph",
    "reading",
    "teaching",
    "exercising",
    "session",
    "planning",
    "building",
    "finishing",
    "cli",
)


def imports_of(module):
    tree = ast.parse((ROOT / "tinkerkit" / f"{module}.py").read_text(encoding="utf-8"))
    top, inner = set(), set()
    nested = {
        id(node)
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if node is not function
    }
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            names = [node.module] if node.module else [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tinkerkit"):
            names = (
                [node.module.split(".", 1)[1]] if "." in node.module else [alias.name for alias in node.names]
            )
        (inner if id(node) in nested else top).update(module_name.split(".")[0] for module_name in names)
    return top, inner


@pytest.mark.spec("ARCH-1")
@pytest.mark.parametrize("module", LAYERS)
def test_each_module_imports_only_the_modules_listed_above_it(module):
    above = set(LAYERS[: LAYERS.index(module)])
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


def program_files():
    return [*(ROOT / "tinkerkit").glob("*.py"), ROOT / "tinker.py"]


@pytest.mark.spec("ARCH-3")
def test_only_the_model_web_and_channel_modules_open_connections():
    network = {"urllib.request", "http.client", "urlopen", "HTTPSConnection"}
    users = set()
    for path in program_files():
        text = path.read_text(encoding="utf-8")
        if any(f"import {name}" in text or f"{name}(" in text for name in network):
            users.add(path.stem)
    assert users <= {"model", "web", "fetcher", "channels", "knowledge"}, users
    knowledge = (ROOT / "tinkerkit" / "knowledge.py").read_text(encoding="utf-8")
    assert "urlopen(" not in knowledge, "knowledge goes online only through Internet and Ollama"
    fetcher_users = {
        path.stem
        for path in (ROOT / "tinkerkit").glob("*.py")
        if ".fetcher import" in path.read_text(encoding="utf-8")
    }
    assert fetcher_users == {"web"}, fetcher_users
    runners = {
        path.stem for path in program_files() if "subprocess.Popen(" in path.read_text(encoding="utf-8")
    }
    assert runners <= {"workspace", "settings", "conversation"}, runners


@pytest.mark.spec("ARCH-4")
def test_only_home_knows_where_the_home_is():
    for path in program_files():
        if path.stem == "settings":
            continue
        text = path.read_text(encoding="utf-8")
        assert "Home.root /" not in text and '"tinker.db"' not in text, (
            f"{path.name} names a path inside the home"
        )
        assert not re.search(r"\b[A-Z_]+_(FILE|DIR)\s*=\s*HOME\b", text), path.name


@pytest.mark.spec("SES-1")
def test_every_phase_works_through_the_session():
    from tinkerkit import session
    from tinkerkit.building import Verifier
    from tinkerkit.planning import SpecWriter

    assert issubclass(SpecWriter, session.Phase) and issubclass(Verifier, session.Worker)
    for module in ("planning", "building", "finishing", "cli"):
        top, inner = imports_of(module)
        assert "session" in top, f"{module} takes its phases from the session"


PRAGMA_RE = re.compile(r"# noqa(: [A-Z]+\d+(, [A-Z]+\d+)*)?")


def every_source_file():
    return sorted([*program_files(), *(ROOT / "tests").glob("*.py")])


def comments_of(path):
    text = path.read_text(encoding="utf-8")
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type == tokenize.COMMENT
    ]


def allowed(path, line, comment):
    shebang = path.name == "tinker.py" and line == 1 and comment.startswith("#!")
    return shebang or bool(PRAGMA_RE.fullmatch(comment))


def docstrings_of(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        getattr(node, "name", "the module")
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and ast.get_docstring(node, clean=False) is not None
    ]


@pytest.mark.spec("ARCH-5")
@pytest.mark.parametrize("path", every_source_file(), ids=lambda path: path.name)
def test_no_comment_but_the_shebang_and_pragmas(path):
    unwanted = [(line, comment) for line, comment in comments_of(path) if not allowed(path, line, comment)]
    assert not unwanted, f"{path.name} has comments: {unwanted}"


@pytest.mark.spec("ARCH-5")
@pytest.mark.parametrize("path", every_source_file(), ids=lambda path: path.name)
def test_no_docstring(path):
    assert not docstrings_of(path), f"{path.name} has docstrings: {docstrings_of(path)}"


@pytest.mark.spec("ARCH-6")
@pytest.mark.parametrize("version", [(3, 14, 2), (3, 9, 6), (2, 7, 18)])
@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_tinker_py_refuses_every_python_below_3_15_before_importing_tinkerkit(monkeypatch, version, platform):
    said = ".".join(map(str, version))
    monkeypatch.setattr(sys, "version_info", version)
    monkeypatch.setattr(sys, "version", f"{said} (main) [Clang]")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(sys.modules, "tinkerkit", None)
    with pytest.raises(SystemExit) as refused:
        runpy.run_path(str(ROOT / "tinker.py"), run_name="tinker")
    assert (
        refused.value.code
        == f"Tinker needs Python 3.15 or newer; this is {said}. On a Mac: brew install python"
    )


@pytest.mark.spec("ARCH-6")
def test_tinker_py_lets_a_3_15_prerelease_through_to_tinkerkit(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 15, 0, "alpha", 1))
    monkeypatch.setitem(sys.modules, "tinkerkit", None)
    with pytest.raises(ImportError):
        runpy.run_path(str(ROOT / "tinker.py"), run_name="tinker")


@pytest.mark.spec("ARCH-6")
def test_tinker_py_starts_tinkerkit_on_python_3_15():
    started = runpy.run_path(str(ROOT / "tinker.py"), run_name="tinker")
    assert started["cli"].main
