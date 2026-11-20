import datetime

import pytest

from tinkerkit.memory.routes import (
    SAID_FILLER,
    Masked,
    Matched,
    RouteFeedback,
    Routes,
    filled,
    forget_route,
    mask,
    show_routes,
    slotted,
)
from tinkerkit.settings import EVENTS_KEPT_PER_ITEM

NOW = datetime.datetime(2026, 11, 20, 12, 0, 0)
LATER = NOW + datetime.timedelta(seconds=60)
TOO_LATE = NOW + datetime.timedelta(seconds=121)
AT = "2026-11-20T12:00:00"
KNOWN = {"MODEL": ["qwen3:8b", "gemma"], "SKILL": ["summarize"], "CHANNEL": ["telegram"]}


def add_route(pattern="show jobs", line="/jobs", source="model", **more):
    return {"op": "add", "pattern": pattern, "line": line, "source": source, **more}


@pytest.mark.spec("RTE-2")
def test_mask_lowercases_and_drops_punctuation_and_filler():
    masked = mask("Please, show me the JOBS!", KNOWN)

    assert masked == Masked(("jobs",), {})
    assert "please" in SAID_FILLER


@pytest.mark.spec("RTE-2")
def test_mask_puts_a_slot_for_each_known_value_in_order(tmp_path):
    masked = mask(
        f"Stop #3 and #12 in @codeforge, read {tmp_path} and https://example.com/a?b=1 with Qwen3:8b",
        KNOWN,
    )

    assert masked.words == (
        "stop",
        "JOB",
        "and",
        "JOB",
        "in",
        "PROJECT",
        "read",
        "PATH",
        "and",
        "URL",
        "with",
        "MODEL",
    )
    assert masked.values == {
        "JOB": ["#3", "#12"],
        "PROJECT": ["@codeforge"],
        "PATH": [str(tmp_path)],
        "URL": ["https://example.com/a?b=1"],
        "MODEL": ["qwen3:8b"],
    }


@pytest.mark.spec("RTE-2")
def test_mask_slots_skills_and_channels_and_leaves_a_missing_path_as_words(tmp_path):
    masked = mask(f"run summarize on telegram from {tmp_path}/nothing", KNOWN)

    assert masked.words[:4] == ("run", "SKILL", "on", "CHANNEL")
    assert "PATH" not in masked.words
    assert masked.values == {"SKILL": ["summarize"], "CHANNEL": ["telegram"]}


@pytest.mark.spec("RTE-2")
def test_filled_puts_the_values_back_in_the_slots_in_order_and_misses_a_slot_with_no_value():
    values = {"JOB": ["#3", "#12"], "PROJECT": ["@codeforge"]}

    assert filled("/stop JOB JOB PROJECT", values) == "/stop #3 #12 @codeforge"
    assert filled("none", values) == "none"
    assert filled("/stop JOB JOB", {"JOB": ["#3"]}) is None
    assert filled("/read PATH", {}) is None


@pytest.mark.spec("RTE-1", "RTE-5")
def test_an_added_route_is_a_candidate_with_the_next_id_and_its_history(home_store):
    added, second = Routes().apply(
        [add_route(), add_route("stop JOB", "/stop JOB", "said", confirmed=True)], NOW
    )

    assert added == {
        "id": "r1",
        "pattern": "show jobs",
        "line": "/jobs",
        "status": "candidate",
        "source": "model",
        "uses": 0,
        "misses": 0,
        "history": [{"event": "add", "at": AT, "text": "/jobs"}],
        "retired": "",
    }
    assert second["id"] == "r2" and second["status"] == "confirmed" and second["source"] == "said"
    assert home_store.get("routes", "r1") == added
    assert [route["id"] for route in Routes().living()] == ["r1", "r2"]


@pytest.mark.spec("RTE-5")
def test_use_confirm_miss_and_retire_change_the_route_and_its_history(home_store):
    Routes().apply([add_route()], NOW)

    Routes().apply(
        [
            {"op": "use", "id": "r1", "text": "show the jobs"},
            {"op": "confirm", "id": "r1"},
            {"op": "miss", "id": "r1"},
        ],
        NOW,
    )
    (retired,) = Routes().apply([{"op": "retire", "id": "r1", "why": "wrong command"}], NOW)

    assert retired["uses"] == 1 and retired["misses"] == 1
    assert retired["status"] == "retired" and retired["retired"] == "wrong command"
    assert [entry["event"] for entry in retired["history"]] == ["add", "use", "confirm", "miss", "retire"]
    assert retired["history"][1]["text"] == "show the jobs"
    assert Routes().apply([{"op": "use", "id": "r1"}, {"op": "confirm", "id": "nope"}], NOW) == []
    assert Routes().living() == []


@pytest.mark.spec("RTE-5")
def test_a_living_route_keeps_only_its_newest_history_events(home_store):
    Routes().apply([add_route()], NOW)
    Routes().apply([{"op": "use", "id": "r1", "text": f"use {number}"} for number in range(25)], NOW)
    (route,) = Routes().living()
    assert len(route["history"]) == EVENTS_KEPT_PER_ITEM
    assert [entry["text"] for entry in route["history"]] == [f"use {number}" for number in range(5, 25)]


@pytest.mark.spec("RTE-5")
def test_an_add_with_a_living_pattern_and_the_same_line_confirms_that_route(home_store):
    Routes().apply([add_route()], NOW)

    (confirmed,) = Routes().apply([add_route(source="said")], NOW)

    assert confirmed["id"] == "r1" and confirmed["status"] == "confirmed"
    assert [route["id"] for route in Routes().all()] == ["r1"]


@pytest.mark.spec("RTE-5")
def test_an_add_with_a_living_pattern_and_another_line_retires_it_and_adds_the_new(home_store):
    Routes().apply([add_route()], NOW)

    (added,) = Routes().apply([add_route(line="/runs", source="correction", confirmed=True)], NOW)

    old = home_store.get("routes", "r1")
    assert old["status"] == "retired" and old["retired"] == "replaced by r2"
    assert added["id"] == "r2" and added["line"] == "/runs" and added["status"] == "confirmed"
    assert Routes().find("show jobs") == added


@pytest.mark.spec("RTE-5")
def test_an_add_only_a_retired_route_with_the_same_line_has_is_dropped(home_store):
    Routes().apply([add_route(), {"op": "retire", "id": "r1", "why": "no"}], NOW)

    assert Routes().apply([add_route()], NOW) == []
    assert Routes().find("show jobs") is None
    (other,) = Routes().apply([add_route(line="/runs")], NOW)
    assert other["id"] == "r2"


@pytest.mark.spec("RTE-5", "SET-8")
def test_a_line_that_deletes_is_never_added(home_store):
    routes = Routes(deletes=lambda line: line.startswith("/forget"))

    assert routes.apply([add_route("forget JOB", "/forget JOB")], NOW) == []
    assert routes.all() == []


@pytest.mark.spec("RTE-3")
def test_seeds_are_confirmed_seed_routes(home_store):
    Routes().seed([("model recipe", "/model recipe"), ("jobs", "/jobs")], NOW)

    first, second = Routes().all()
    assert (first["pattern"], first["line"], first["status"], first["source"]) == (
        "model recipe",
        "/model recipe",
        "confirmed",
        "seed",
    )
    assert (second["id"], second["pattern"]) == ("r2", "jobs")


@pytest.mark.spec("RTE-3")
def test_a_seed_is_added_only_when_no_route_has_its_pattern(home_store):
    Routes().apply([add_route("jobs", "/runs"), add_route("help"), {"op": "retire", "id": "r2"}], NOW)

    Routes().seed([("jobs", "/jobs"), ("help", "/help"), ("show runs", "/runs")], NOW)
    Routes().seed([("show runs", "/runs")], NOW)

    assert [(route["pattern"], route["line"], route["status"]) for route in Routes().all()] == [
        ("jobs", "/runs", "candidate"),
        ("help", "/jobs", "retired"),
        ("show runs", "/runs", "confirmed"),
    ]


@pytest.mark.spec("RTE-4")
def test_match_finds_the_masked_pattern_exactly_and_fills_its_line(home_store):
    Routes().seed([("stop JOB", "/stop JOB"), ("model", "/model")], NOW)

    matched = Routes().match("Please stop #4!", KNOWN)

    assert matched == Matched(Routes().find("stop JOB"), "/stop #4")


@pytest.mark.spec("RTE-4", "KNW-11")
def test_match_takes_the_most_alike_living_route_in_words(home_store):
    Routes().seed([("system battery", "/system battery"), ("system disk", "/system disk")], NOW)

    assert Routes().match("system batery", KNOWN).line == "/system battery"
    assert Routes().match("battery", KNOWN) is None


@pytest.mark.spec("RTE-4")
def test_match_skips_retired_routes(home_store):
    Routes().seed([("system battery", "/system battery")], NOW)
    Routes().apply([{"op": "retire", "id": "r1"}], NOW)

    assert Routes().match("system battery", KNOWN) is None


@pytest.mark.spec("RTE-4")
def test_two_best_routes_with_different_lines_are_unsure(home_store):
    Routes().seed([("list all runs today", "/runs"), ("list all rung today", "/jobs")], NOW)

    assert Routes().match("list all runx today", KNOWN) is None
    Routes().apply([{"op": "retire", "id": "r2"}], NOW)
    assert Routes().match("list all runx today", KNOWN).line == "/runs"


@pytest.mark.spec("RTE-4")
def test_a_line_slot_with_no_value_or_no_words_is_a_miss(home_store):
    Routes().seed([("stop", "/stop JOB")], NOW)

    assert Routes().match("stop", KNOWN) is None
    assert Routes().match("please", KNOWN) is None


@pytest.mark.spec("RTE-7")
def test_slotted_turns_each_masked_value_in_a_line_back_into_its_slot():
    values = {"JOB": ["#4"], "MODEL": ["gemma"]}

    assert slotted("/stop #4 with Gemma now", values) == "/stop JOB with MODEL now"
    assert filled(slotted("/stop #4", values), values) == "/stop #4"


def feedback(quiet=lambda line: line.startswith("/jobs")):
    return RouteFeedback(Routes(), lambda: KNOWN, quiet)


def routed(line="/jobs", **quiet):
    Routes().apply([add_route(line=line)], NOW)
    heard = feedback(**quiet)
    heard.routed("chat", "show jobs", line, Routes().find("show jobs"), NOW)
    return heard


def routed_by_a_candidate_and_the_model(message="stop #4", line="/stop #4"):
    heard = routed("/stop")
    heard.routed("other", message, line, None, NOW)
    return heard


def instructed():
    Routes().apply([add_route(pattern="be brief", line="/instruct be brief")], NOW)
    heard = feedback()
    heard.routed("candidate", "be brief", "/instruct be brief", Routes().find("be brief"), NOW)
    heard.routed("model", "list big files", "/instruct list big files", None, NOW)
    return heard


@pytest.mark.spec("RTE-7")
def test_a_quiet_line_no_route_gave_is_added_as_a_model_candidate_on_the_next_message(home_store):
    heard = feedback()
    heard.routed("chat", "Show my jobs for @site", "/jobs @site", None, NOW)

    assert Routes().all() == []
    heard.heard("chat", "thanks", LATER)

    added = Routes().find("jobs for PROJECT")
    assert (added["line"], added["status"], added["source"]) == ("/jobs PROJECT", "candidate", "model")


@pytest.mark.spec("RTE-7")
def test_a_confirmed_route_counts_a_use_at_once(home_store):
    Routes().seed([("show jobs", "/jobs")], NOW)

    feedback().routed("chat", "show jobs", "/jobs", Routes().find("show jobs"), NOW)

    assert Routes().find("show jobs")["uses"] == 1


@pytest.mark.spec("RTE-6", "RTE-7")
def test_a_line_from_the_model_or_a_candidate_that_is_not_quiet_is_proposed_and_keeps_nothing(home_store):
    heard = routed_by_a_candidate_and_the_model("list runs", "/stop")

    assert heard.proposes("chat") and heard.proposes("other")
    heard.heard("chat", "ok", LATER)
    heard.heard("other", "ok", LATER)

    assert [route["status"] for route in Routes().all()] == ["candidate"]


@pytest.mark.spec("RTE-7")
@pytest.mark.parametrize("correction", ["No, the runs", "I meant the runs"])
def test_a_worded_correction_in_time_retires_the_route_used(home_store, correction):
    heard = routed()

    heard.heard("chat", correction, LATER)

    (retired,) = Routes().all()
    assert (retired["status"], retired["retired"]) == ("retired", correction)


@pytest.mark.spec("RTE-7")
def test_a_command_correction_retires_the_route_and_adds_its_line_for_the_pattern(home_store):
    Routes().seed([("stop JOB", "/jobs")], NOW)
    heard = feedback()
    heard.routed("chat", "stop #4", "/jobs", Routes().find("stop JOB"), NOW)

    heard.heard("chat", "/stop #4", LATER)

    old, new = Routes().all()
    assert (old["status"], old["retired"]) == ("retired", "/stop #4")
    assert (new["pattern"], new["line"], new["source"], new["status"]) == (
        "stop JOB",
        "/stop JOB",
        "correction",
        "candidate",
    )


@pytest.mark.spec("RTE-7")
def test_a_correction_after_the_window_is_a_quiet_yes(home_store):
    heard = routed()

    heard.heard("chat", "no thanks", TOO_LATE)

    assert Routes().find("show jobs")["status"] == "confirmed"


@pytest.mark.spec("RTE-7")
@pytest.mark.parametrize("bare", ["/confirm", "/cancel"])
def test_a_bare_confirm_or_cancel_with_nothing_waiting_teaches_nothing(home_store, bare):
    heard = routed()

    heard.heard("chat", bare, LATER)

    (kept,) = Routes().all()
    assert kept["status"] == "candidate"


@pytest.mark.spec("RTE-7")
def test_feedback_is_kept_per_chat_and_used_once(home_store):
    heard = routed()

    heard.heard("other", "no", LATER)
    heard.heard("chat", "thanks", LATER)
    heard.heard("chat", "no", LATER)

    assert Routes().find("show jobs")["status"] == "confirmed"


@pytest.mark.spec("RTE-6")
def test_a_quiet_instruct_deleting_or_confirmed_line_is_not_proposed(home_store):
    Routes().seed([("halt", "/stop")], NOW)
    heard = RouteFeedback(
        Routes(deletes=lambda line: line.startswith("/forget")), lambda: KNOWN, lambda line: line == "/jobs"
    )
    heard.routed("quiet", "show jobs", "/jobs", None, NOW)
    heard.routed("instruct", "always be brief", "/instruct always be brief", None, NOW)
    heard.routed("deletes", "forget it", "/forget it", None, NOW)
    heard.routed("confirmed", "halt", "/stop", Routes().find("halt"), NOW)

    assert not any(heard.proposes(chat) for chat in ["quiet", "instruct", "deletes", "confirmed", "none"])


@pytest.mark.spec("RTE-7")
def test_an_instruct_whose_next_message_is_not_a_cancel_is_a_quiet_yes(home_store):
    heard = instructed()

    heard.heard("candidate", "only the big ones", LATER)
    heard.heard("model", "only the big ones", LATER)

    assert Routes().find("be brief")["status"] == "confirmed"
    added = Routes().find("list big files")
    assert (added["line"], added["status"], added["source"]) == (
        "/instruct list big files",
        "candidate",
        "model",
    )


@pytest.mark.spec("RTE-7")
def test_a_cancel_of_an_instructs_first_proposal_retires_the_candidate_and_adds_nothing(home_store):
    heard = instructed()

    for chat in ["candidate", "model"]:
        heard.heard(chat, "/cancel", LATER)
        heard.refused(chat, LATER)

    (route,) = Routes().all()
    assert (route["status"], route["retired"]) == ("retired", "no")
    assert "candidate" not in heard.last and "model" not in heard.last


@pytest.mark.spec("RTE-6")
def test_a_proposal_waits_through_the_next_message_and_is_never_a_quiet_yes(home_store):
    assert not routed("/stop", quiet=lambda line: True).proposes("chat")
    heard = feedback()
    heard.routed("chat", "show jobs", "/stop", Routes().find("show jobs"), NOW)
    heard.heard("chat", "/confirm", LATER)

    assert heard.proposes("chat")
    assert Routes().find("show jobs")["status"] == "candidate"


@pytest.mark.spec("RTE-7")
def test_a_yes_adds_a_model_line_as_a_candidate_and_confirms_the_candidate_that_gave_it(home_store):
    heard = routed_by_a_candidate_and_the_model()

    heard.answered("chat", True, LATER)
    heard.answered("other", True, LATER)

    assert Routes().find("show jobs")["status"] == "confirmed"
    added = Routes().find("stop JOB")
    assert (added["line"], added["status"], added["source"]) == ("/stop JOB", "candidate", "model")
    assert not heard.proposes("chat") and not heard.proposes("other")


@pytest.mark.spec("RTE-7")
def test_a_no_retires_the_candidate_that_gave_it_and_keeps_nothing_for_a_model_line(home_store):
    heard = routed_by_a_candidate_and_the_model()

    heard.answered("chat", False, LATER)
    heard.answered("other", False, LATER)

    assert [route["status"] for route in Routes().all()] == ["retired"]
    assert not heard.proposes("chat") and not heard.proposes("other")


@pytest.mark.spec("RTE-7")
def test_an_answer_with_no_proposal_waiting_changes_nothing(home_store):
    heard = routed()

    heard.answered("chat", False, LATER)
    heard.answered("none", True, LATER)

    assert Routes().find("show jobs")["status"] == "candidate"


@pytest.mark.spec("RTE-8")
def test_forget_retires_a_route_as_forgotten_without_asking(home_store, capsys):
    Routes().apply([add_route("show the weather", "weather")], NOW)

    forget_route("r1")

    route = home_store.get("routes", "r1")
    assert route["status"] == "retired" and route["retired"] == "forgotten"
    assert "Forgot r1." in capsys.readouterr().out


@pytest.mark.spec("RTE-8")
def test_forget_refuses_an_unknown_or_retired_route(home_store):
    Routes().apply([add_route("show the weather", "weather")], NOW)
    forget_route("r1")

    for route_id in ("r1", "r7"):
        with pytest.raises(SystemExit, match=f"no route {route_id}.*tinker memory routes"):
            forget_route(route_id)


@pytest.mark.spec("RTE-8", "RTE-1")
def test_routes_lists_the_living_routes_confirmed_first(home_store, capsys):
    Routes().apply(
        [
            add_route("show the weather", "weather"),
            add_route("list my files", "files list", confirmed=True),
            add_route("old habit", "status"),
        ],
        NOW,
    )
    Routes().apply([{"op": "use", "id": "r2"}, {"op": "retire", "id": "r3", "why": "forgotten"}], NOW)

    show_routes()

    out = capsys.readouterr().out
    assert out.index("r2 list my files → files list") < out.index("r1 show the weather → weather")
    assert "confirmed · 1 use" in out and "candidate · 0 uses" in out
    assert "r3" not in out


@pytest.mark.spec("RTE-8")
def test_routes_says_so_when_there_are_none(home_store, capsys):
    show_routes()

    assert "No routes yet" in capsys.readouterr().out
