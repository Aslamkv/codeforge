import pytest
from conftest import TOOLCHAIN, criterion, plan_of

from tinkerkit.knowledge import Knowledge, Library
from tinkerkit.project import Tracker
from tinkerkit.prompts import Part, Prompts, PromptTooLarge
from tinkerkit.session import Phase, Session, Stopped


@pytest.mark.spec("SES-1")
def test_the_session_holds_what_phases_need(make_project, make_session, make_model):
    tracker = Tracker("calc")
    model = make_model()
    session = make_session(make_project(), model=model, confirm=False, tracker=tracker)
    assert (session.model, session.confirm, session.tracker, session.cycle) == (model, False, tracker, None)
    assert session.toolchain.test_all == TOOLCHAIN["test_all"]
    worker = Phase(session)
    assert (worker.session, worker.project, worker.workspace) == (session, session.project, session.workspace)


@pytest.mark.spec("SES-2")
def test_setup_runs_only_when_it_changed(session, project_dir):
    runs = lambda: (project_dir / "setups.txt").read_text().count("ran")
    assert session.setup() == (True, "")
    session.setup()
    assert runs() == 1
    session.setup(force=True)
    assert runs() == 2
    session.project.files["pyproject.toml"] = {"kind": "config", "role": "settings"}
    (project_dir / "pyproject.toml").write_text("[tool]")
    session.setup()
    assert runs() == 3
    (project_dir / ".env.example").write_text("KEY=<your key>\n")
    session.setup()
    assert (project_dir / ".env").read_text() == "KEY=\n"


@pytest.mark.spec("SES-2")
def test_the_suite_runs_once_per_state_of_the_files(session, project_dir):
    session.project.files["calc.py"] = {"kind": "code"}
    (project_dir / "calc.py").write_text("one")
    session.run_all_tests()
    session.run_all_tests()
    assert (project_dir / "runs.txt").read_text().count("run") == 1
    (project_dir / "calc.py").write_text("two")
    session.run_all_tests()
    assert (project_dir / "runs.txt").read_text().count("run") == 2


@pytest.mark.spec("SES-2")
def test_note_ask_until_usable_and_room(session, fake_ollama, monkeypatch):
    for number in range(35):
        session.note("fix", f"problem {number}")
    assert (
        len(session.project.spec["events"]) == 30 and session.project.spec["events"][0]["text"] == "problem 5"
    )
    fake_ollama.reply("nope", times=3)
    with pytest.raises(
        SystemExit, match="Could not get a usable plan. See the model calls: tinker log --calls"
    ):
        session.ask_until_usable("s", [Part("Q", "q", True)], "Plan", lambda reply: reply.json())

    def too_large(*args, **kwargs):
        raise PromptTooLarge("too big")

    monkeypatch.setattr(session.model, "ask", too_large)
    with pytest.raises(SystemExit, match="even shortened, the prompt does not fit"):
        session.ask_until_usable("s", [], "Plan", lambda reply: reply)
    assert session.room("system") == int(session.model.limit_chars("system" + Prompts.NO_TOOL_CALLS) * 0.95)


@pytest.mark.spec("SES-2")
def test_format_code_drops_a_formatter_that_fails(session, project_dir, capsys):
    session.project.spec["toolchain"]["format"] = "echo formatted > formatted.txt"
    session.format_code()
    assert (project_dir / "formatted.txt").exists() and session.toolchain.format
    session.project.spec["toolchain"]["format"] = "no-such-formatter-xyz ."
    session.format_code()
    assert session.toolchain.format == "" and "The formatter does not work" in capsys.readouterr().out


@pytest.mark.spec("SES-3")
def test_knowledge_parts_record_what_they_show(session, home_store):
    knowledge = Knowledge("Python")
    knowledge.add_lessons([{"text": "Use tmp_path in tests", "phase": "tests", "scope": "any"}], [])
    Library.save(
        "flask",
        {
            "topic": "flask",
            "name": "Flask",
            "summary": "s",
            "facts": [
                {"phase": "tests", "text": "use the test client"},
                {"phase": "code", "text": "routes use decorators"},
            ],
        },
    )
    proven, client = (fact["id"] for fact in reversed(Library.read("flask")["facts"]))
    knowledge.prove({f"fact:flask:{proven}": ["code"]})
    session.project.spec["learned"] = [
        item for item in Library.items({"flask": Library.read("flask")}) if item["id"]
    ]
    session.cycle = cycle = session.project.cycles[0]
    parts = session.knowledge_parts("tests", "code")
    assert [part.title for part in parts] == [
        "Lessons from earlier projects (follow them)",
        "Learned from the web (current; newer than what you were trained on)",
    ]
    assert parts[0].text == "- Use tmp_path in tests"
    each_proven_fact_once = "- [Python] routes use decorators\n- [Flask] use the test client"
    assert parts[1].text == each_proven_fact_once
    assert all(not part.required and part.place == "first" for part in parts)
    lesson_id = knowledge.known_lessons()[0]["id"]
    assert cycle["used"] == {
        f"lesson:{lesson_id}": ["code", "tests"],
        f"known:{proven}": ["code", "tests"],
        f"fact:flask:{client}": ["code", "tests"],
    }
    assert len(session.knowledge_parts("toolchain")) == 3


@pytest.mark.spec("SES-3")
def test_code_and_tests_are_told_the_checks_they_must_pass(session):
    session.project.spec["toolchain"]["lint"] = "ruff check . && mypy --strict ."
    checks = [part for part in session.knowledge_parts("code") if part.title.startswith("Checks")]
    assert [part.text for part in checks] == ["$ ruff check . && mypy --strict ."]
    assert not [
        part for part in session.knowledge_parts("design", "toolchain") if part.title.startswith("Checks")
    ]
    session.project.spec["toolchain"]["lint"] = "none"
    assert not [part for part in session.knowledge_parts("tests") if part.title.startswith("Checks")]


@pytest.mark.spec("SES-5")
def test_before_the_spec_names_the_language_knowledge_is_for_the_one_the_request_names(session, home_store):
    Library.save(
        "rust", {"topic": "rust", "kind": "language", "language": "Rust", "name": "Rust 1.95", "facts": []}
    )
    Knowledge("Go").finished_project()
    session.project.spec["language"] = ""
    for request, language in [
        ("a calculator in Rust", "Rust"),
        ("a Go tool, later ported to Rust", "Go"),
        ("a calculator", "Python"),
        ("a trusty calculator", "Python"),
    ]:
        session.project.cycles[0]["request"] = request
        assert session.knowledge.language == language
    session.project.spec["language"] = "Rust"
    session.project.cycles[0]["request"] = "a calculator in Go"
    assert session.knowledge.language == "Rust"


@pytest.mark.spec("SES-3")
def test_the_phases_a_failure_disproves():
    assert Session.failed_phases("setup") == ("toolchain", "config")
    assert Session.failed_phases("verify") == ("code", "tests", "design", "spec")
    assert Session.failed_phases("reproduce") == ()
    assert Session.failed_phases("verify", Stopped("build", "no colon")) == ("code", "config")
    assert Session.failed_phases("reproduce", Stopped("tools", "no pytest")) == ("toolchain", "config")
    assert Session.failed_phases("reproduce", Stopped("unreproduced", "no test")) == ()


@pytest.mark.spec("SES-4")
def test_save_keeps_the_project_and_tells_the_tracker(make_project, make_session, project_dir):
    tracker = Tracker("calc")
    session = make_session(make_project([criterion("C1", "t.py::a", status="green")]), tracker=tracker)
    session.save()
    assert plan_of(project_dir)["steps"][0]["id"] == "C1" and tracker.state["passing"] == [1, 1]
