"""Corner-case coverage for the pure logic functions in app.py: explanation
parsing, checkpoint-driven question selection, spaced-repetition drill-queue
computation, domain stat aggregation, and the git checkpoint push helper.

app.py is a Streamlit script, but importing it directly works in "bare mode"
(Streamlit just logs a "missing ScriptRunContext" warning) — that's enough to
reach the plain functions below without needing a running Streamlit server.
All storage access is redirected to a scratch directory so these tests never
touch the repo's real checkpoint.json / session_logs.
"""

import subprocess
from datetime import datetime

import pytest

import app


@pytest.fixture
def isolated_storage(isolated_storage_paths, monkeypatch):
    """Swap app.STORAGE for a fresh LocalFileStorage backed by tmp_path."""
    from storage import LocalFileStorage

    fresh = LocalFileStorage()
    monkeypatch.setattr(app, "STORAGE", fresh)
    return fresh


FIXTURE_QUESTIONS = [
    {"id": 1, "question": "Q1", "choices": {"A": "a", "B": "b"}, "correct": "A", "domain": "Domain A"},
    {"id": 2, "question": "Q2", "choices": {"A": "a", "B": "b"}, "correct": "B", "domain": "Domain B"},
    {"id": 3, "question": "Q3", "choices": {"A": "a", "B": "b"}, "correct": "A", "domain": "Domain A"},
    {"id": 4, "question": "Q4", "choices": {"A": "a", "B": "b"}, "correct": "A", "domain": None},
]


@pytest.fixture
def fixture_questions(monkeypatch):
    monkeypatch.setattr(app, "ALL_QUESTIONS", FIXTURE_QUESTIONS)
    return FIXTURE_QUESTIONS


# ─── parse_explanation ───────────────────────────────────────────────────────


def test_parse_explanation_standard_order():
    choices = {"A": "Wrong answer text", "B": "Right answer text"}
    explanation = (
        "A. Wrong answer text ❌ Incorrect. This is wrong because X. "
        "B. Right answer text ✅ Correct. This is right because Y."
    )
    segments = app.parse_explanation(explanation, choices, "B")
    assert segments is not None
    assert [s["letter"] for s in segments] == ["A", "B"]
    a, b = segments
    assert a["is_correct"] is False
    assert "wrong because X" in a["reasoning"]
    assert b["is_correct"] is True
    assert "right because Y" in b["reasoning"]


def test_parse_explanation_is_order_independent_correct_answer_first():
    choices = {"A": "Wrong answer text", "B": "Right answer text"}
    explanation = (
        "B. Right answer text ✅ Correct. This is right because Y. "
        "A. Wrong answer text ❌ Incorrect. This is wrong because X."
    )
    segments = app.parse_explanation(explanation, choices, "B")
    assert segments is not None
    # segments are always returned sorted by letter regardless of source order
    assert [s["letter"] for s in segments] == ["A", "B"]


def test_parse_explanation_handles_no_blank_lines_between_choices():
    choices = {"A": "First choice here", "B": "Second choice here"}
    explanation = "A. First choice here ❌ Incorrect. reasoning wraps mid B. Second choice here ✅ Correct. more text"
    segments = app.parse_explanation(explanation, choices, "B")
    assert segments is not None
    assert segments[0]["reasoning"] != segments[1]["reasoning"]


def test_parse_explanation_returns_none_when_a_choice_echo_is_missing():
    choices = {"A": "First choice here", "B": "Second choice here"}
    explanation = "A. First choice here ❌ Incorrect. reasoning. B. Completely paraphrased differently. ✅ Correct."
    assert app.parse_explanation(explanation, choices, "B") is None


def test_parse_explanation_handles_plain_text_verdicts_without_emoji():
    choices = {"A": "Choice one text", "B": "Choice two text"}
    explanation = "A. Choice one text Incorrect. because reasons. B. Choice two text Correct. because other reasons."
    segments = app.parse_explanation(explanation, choices, "B")
    assert segments is not None
    assert segments[0]["reasoning"] == "because reasons."
    assert segments[1]["reasoning"] == "because other reasons."


def test_parse_explanation_handles_three_or_more_choices():
    choices = {"A": "Choice one text", "B": "Choice two text", "C": "Choice three text"}
    explanation = (
        "A. Choice one text ❌ Incorrect. reason A. "
        "B. Choice two text ❌ Incorrect. reason B. "
        "C. Choice three text ✅ Correct. reason C."
    )
    segments = app.parse_explanation(explanation, choices, "C")
    assert [s["letter"] for s in segments] == ["A", "B", "C"]
    assert segments[2]["is_correct"] is True


def test_parse_explanation_collapses_internal_whitespace_in_choice_prefix():
    choices = {"A": "Choice  with\nnewline inside", "B": "Second choice text"}
    explanation = (
        "A. Choice with newline inside ❌ Incorrect. reason. "
        "B. Second choice text ✅ Correct. reason."
    )
    segments = app.parse_explanation(explanation, choices, "B")
    assert segments is not None


def test_parse_explanation_falls_back_with_dash_when_no_reasoning_remains():
    choices = {"A": "Choice one text", "B": "Choice two text"}
    explanation = "A. Choice one text ❌ Incorrect. B. Choice two text ✅ Correct."
    segments = app.parse_explanation(explanation, choices, "B")
    assert segments is not None
    assert segments[0]["reasoning"] == "—"
    assert segments[1]["reasoning"] == "—"


# ─── get_unused_questions ───────────────────────────────────────────────────


def test_get_unused_questions_all_unused_when_checkpoint_empty(fixture_questions, isolated_storage):
    unused, used_count = app.get_unused_questions()
    assert [q["id"] for q in unused] == [1, 2, 3, 4]
    assert used_count == 0


def test_get_unused_questions_excludes_used_ids(fixture_questions, isolated_storage):
    isolated_storage.save_checkpoint_entry(1)
    isolated_storage.save_checkpoint_entry(3)

    unused, used_count = app.get_unused_questions()
    assert [q["id"] for q in unused] == [2, 4]
    assert used_count == 2


def test_get_unused_questions_empty_when_all_used(fixture_questions, isolated_storage):
    for q in fixture_questions:
        isolated_storage.save_checkpoint_entry(q["id"])

    unused, used_count = app.get_unused_questions()
    assert unused == []
    assert used_count == len(fixture_questions)


# ─── build_and_store_session_log ────────────────────────────────────────────


def test_build_and_store_session_log_computes_correct_incorrect_skipped(fixture_questions, isolated_storage):
    questions = FIXTURE_QUESTIONS[:3]
    answers = {0: "A", 1: "A", 2: None}  # idx0 correct, idx1 wrong, idx2 not in dict at all -> skipped
    del answers[2]

    app.build_and_store_session_log("sess1", answers, questions, "Team X", learner_id="alice")

    logs = app.STORAGE.read_all_session_logs()
    assert len(logs) == 1
    log = logs[0]
    assert log["cohort"] == "Team X"
    assert log["learner_id"] == "alice"
    assert log["total_answered"] == 2
    assert log["correct"] == 1
    results = {entry["id"]: entry["result"] for entry in log["questions"]}
    assert results == {1: "correct", 2: "incorrect", 3: "skipped"}


def test_build_and_store_session_log_stores_none_for_blank_learner_id(fixture_questions, isolated_storage):
    app.build_and_store_session_log("sess2", {}, FIXTURE_QUESTIONS[:1], "Team X", learner_id="")
    log = app.STORAGE.read_all_session_logs()[0]
    assert log["learner_id"] is None


# ─── compute_learner_srs_state ──────────────────────────────────────────────


def test_compute_learner_srs_state_empty_without_learner_id(fixture_questions):
    assert app.compute_learner_srs_state("", [{"learner_id": "alice", "questions": []}]) == {}


def test_compute_learner_srs_state_streak_counts_trailing_corrects(fixture_questions):
    logs = [
        {
            "learner_id": "alice",
            "date": "2026-01-01 00:00:00",
            "questions": [{"id": 1, "result": "incorrect"}],
        },
        {
            "learner_id": "alice",
            "date": "2026-01-02 00:00:00",
            "questions": [{"id": 1, "result": "correct"}],
        },
        {
            "learner_id": "alice",
            "date": "2026-01-03 00:00:00",
            "questions": [{"id": 1, "result": "correct"}],
        },
    ]
    state = app.compute_learner_srs_state("alice", logs)
    assert state[1]["streak"] == 2
    assert state[1]["last_seen"] == "2026-01-03 00:00:00"
    assert state[1]["attempts"] == 3


def test_compute_learner_srs_state_miss_resets_streak_to_zero(fixture_questions):
    logs = [
        {"learner_id": "alice", "date": "2026-01-01 00:00:00", "questions": [{"id": 1, "result": "correct"}]},
        {"learner_id": "alice", "date": "2026-01-02 00:00:00", "questions": [{"id": 1, "result": "incorrect"}]},
    ]
    state = app.compute_learner_srs_state("alice", logs)
    assert state[1]["streak"] == 0


def test_compute_learner_srs_state_skipped_answers_do_not_count_as_an_attempt(fixture_questions):
    logs = [
        {"learner_id": "alice", "date": "2026-01-01 00:00:00", "questions": [{"id": 1, "result": "incorrect"}]},
        {"learner_id": "alice", "date": "2026-01-02 00:00:00", "questions": [{"id": 1, "result": "skipped"}]},
    ]
    state = app.compute_learner_srs_state("alice", logs)
    assert state[1]["last_seen"] == "2026-01-01 00:00:00"
    assert state[1]["streak"] == 0


# ─── compute_drill_queue ─────────────────────────────────────────────────────


def test_compute_drill_queue_empty_without_learner_id(fixture_questions):
    assert app.compute_drill_queue("", [{"learner_id": "alice", "questions": []}]) == []


def test_compute_drill_queue_missed_question_is_always_due(fixture_questions):
    logs = [
        {
            "learner_id": "alice",
            "date": "2026-01-01 00:00:00",
            "questions": [{"id": 1, "result": "incorrect"}, {"id": 2, "result": "correct"}],
        }
    ]
    # id 2's single correct answer (box 1, due after 1 day) isn't due one minute later;
    # id 1's miss (box 0, due immediately) always is.
    now = datetime(2026, 1, 1, 0, 1, 0)
    queue = app.compute_drill_queue("alice", logs, now=now)
    assert [q["id"] for q in queue] == [1]


def test_compute_drill_queue_ignores_other_learners(fixture_questions):
    logs = [{"learner_id": "bob", "date": "2026-01-01 00:00:00", "questions": [{"id": 1, "result": "incorrect"}]}]
    assert app.compute_drill_queue("alice", logs) == []


def test_compute_drill_queue_correct_streak_goes_on_cooldown_then_becomes_due(fixture_questions):
    logs = [
        {"learner_id": "alice", "date": "2026-01-01 00:00:00", "questions": [{"id": 1, "result": "correct"}]},
    ]
    # box 1 -> due after 1 day. Not due after half a day, due once the day has passed.
    soon = datetime(2026, 1, 1, 12, 0, 0)
    later = datetime(2026, 1, 2, 1, 0, 0)
    assert app.compute_drill_queue("alice", logs, now=soon) == []
    assert [q["id"] for q in app.compute_drill_queue("alice", logs, now=later)] == [1]


def test_compute_drill_queue_orders_most_overdue_first(fixture_questions):
    logs = [
        {"learner_id": "alice", "date": "2026-01-01 00:00:00", "questions": [{"id": 1, "result": "incorrect"}]},
        {"learner_id": "alice", "date": "2026-01-05 00:00:00", "questions": [{"id": 2, "result": "incorrect"}]},
    ]
    # both are box 0 (due immediately) but id 1 has been overdue far longer
    now = datetime(2026, 1, 10, 0, 0, 0)
    queue = app.compute_drill_queue("alice", logs, now=now)
    assert [q["id"] for q in queue] == [1, 2]


def test_compute_drill_queue_handles_logs_missing_date_key(fixture_questions):
    logs = [{"learner_id": "alice", "questions": [{"id": 1, "result": "incorrect"}]}]
    queue = app.compute_drill_queue("alice", logs)
    assert [q["id"] for q in queue] == [1]


# ─── scenario practice ───────────────────────────────────────────────────────

SCENARIO_A_HEAVY = {"name": "A-Heavy", "summary": "", "domain_weights": {"Domain A": 1.0, "Domain B": 0.0}}
SCENARIO_B_HEAVY = {"name": "B-Heavy", "summary": "", "domain_weights": {"Domain A": 0.0, "Domain B": 1.0}}
SCENARIO_MIXED = {"name": "Mixed", "summary": "", "domain_weights": {"Domain A": 0.5, "Domain B": 0.5}}


@pytest.fixture
def fixture_scenarios(monkeypatch):
    monkeypatch.setattr(app, "DOMAIN_WEIGHTS", {"Domain A": 0.5, "Domain B": 0.5})
    monkeypatch.setattr(app, "SCENARIOS", [SCENARIO_A_HEAVY, SCENARIO_B_HEAVY, SCENARIO_MIXED])
    return app.SCENARIOS


def test_combine_scenario_domain_weights_matches_single_scenario_profile(fixture_scenarios):
    assert app.combine_scenario_domain_weights(["A-Heavy"]) == {"Domain A": 1.0, "Domain B": 0.0}


def test_combine_scenario_domain_weights_averages_multiple_scenarios(fixture_scenarios):
    weights = app.combine_scenario_domain_weights(["A-Heavy", "B-Heavy"])
    assert weights == {"Domain A": 0.5, "Domain B": 0.5}


def test_combine_scenario_domain_weights_empty_input_is_all_zero(fixture_scenarios):
    assert app.combine_scenario_domain_weights([]) == {"Domain A": 0.0, "Domain B": 0.0}


def test_sample_scenario_pool_returns_exact_requested_count(fixture_scenarios):
    questions = (
        [{"id": i, "domain": "Domain A"} for i in range(1, 11)]
        + [{"id": i, "domain": "Domain B"} for i in range(11, 21)]
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app, "ALL_QUESTIONS", questions)
        pool = app.sample_scenario_pool(["Mixed"], 6)
    assert len(pool) == 6
    assert len({q["id"] for q in pool}) == 6  # no duplicates


def test_sample_scenario_pool_stratifies_toward_the_heavier_domain(fixture_scenarios):
    questions = (
        [{"id": i, "domain": "Domain A"} for i in range(1, 21)]
        + [{"id": i, "domain": "Domain B"} for i in range(21, 41)]
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app, "ALL_QUESTIONS", questions)
        pool = app.sample_scenario_pool(["A-Heavy"], 10)
    assert all(q["domain"] == "Domain A" for q in pool)


def test_sample_scenario_pool_tops_up_when_a_domain_runs_short(fixture_scenarios):
    # only 2 Domain A questions exist, but the profile wants all 10 from Domain A
    questions = (
        [{"id": i, "domain": "Domain A"} for i in range(1, 3)]
        + [{"id": i, "domain": "Domain B"} for i in range(3, 23)]
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app, "ALL_QUESTIONS", questions)
        pool = app.sample_scenario_pool(["A-Heavy"], 10)
    assert len(pool) == 10
    assert len({q["id"] for q in pool}) == 10


# ─── aggregate_domain_stats_from_logs ───────────────────────────────────────


def test_aggregate_domain_stats_counts_correct_and_total_per_domain(fixture_questions):
    logs = [
        {
            "questions": [
                {"id": 1, "result": "correct"},
                {"id": 2, "result": "incorrect"},
                {"id": 3, "result": "correct"},
            ]
        }
    ]
    stats = app.aggregate_domain_stats_from_logs(logs)
    assert stats == {
        "Domain A": {"correct": 2, "total": 2},
        "Domain B": {"correct": 0, "total": 1},
    }


def test_aggregate_domain_stats_excludes_skipped_entries(fixture_questions):
    logs = [{"questions": [{"id": 1, "result": "skipped"}]}]
    assert app.aggregate_domain_stats_from_logs(logs) == {}


def test_aggregate_domain_stats_ignores_questions_with_no_domain(fixture_questions):
    logs = [{"questions": [{"id": 4, "result": "correct"}]}]  # fixture id 4 has domain=None
    assert app.aggregate_domain_stats_from_logs(logs) == {}


def test_aggregate_domain_stats_across_multiple_logs(fixture_questions):
    logs = [
        {"questions": [{"id": 1, "result": "correct"}]},
        {"questions": [{"id": 1, "result": "incorrect"}]},
    ]
    stats = app.aggregate_domain_stats_from_logs(logs)
    assert stats == {"Domain A": {"correct": 1, "total": 2}}


def test_aggregate_domain_stats_empty_logs_returns_empty_dict(fixture_questions):
    assert app.aggregate_domain_stats_from_logs([]) == {}


# ─── study report ────────────────────────────────────────────────────────────


def test_summarize_logs_computes_accuracy():
    logs = [{"total_answered": 4, "correct": 3}, {"total_answered": 2, "correct": 0}]
    summary = app.summarize_logs(logs)
    assert summary == {"sessions": 2, "answered": 6, "correct": 3, "accuracy_pct": 50}


def test_summarize_logs_empty_logs_has_zero_accuracy_not_a_div_by_zero():
    assert app.summarize_logs([]) == {"sessions": 0, "answered": 0, "correct": 0, "accuracy_pct": 0}


def test_focus_areas_from_domain_stats_flags_below_threshold():
    stats = {
        "Domain A": {"correct": 9, "total": 10},   # 90%, above threshold
        "Domain B": {"correct": 3, "total": 10},   # 30%, below threshold
        "Domain C": {"correct": 0, "total": 0},    # no attempts, shouldn't crash or flag
    }
    assert app.focus_areas_from_domain_stats(stats) == ["Domain B"]


def test_build_progress_report_without_learner_id_has_no_personal_section(fixture_questions):
    logs = [{"total_answered": 1, "correct": 1, "questions": [{"id": 1, "result": "correct"}]}]
    report = app.build_progress_report(logs, learner_id="")
    assert report["personal"] is None
    assert report["cohort"]["sessions"] == 1


def test_build_progress_report_unknown_learner_id_has_no_personal_section(fixture_questions):
    logs = [{"learner_id": "bob", "total_answered": 1, "correct": 1, "questions": [{"id": 1, "result": "correct"}]}]
    report = app.build_progress_report(logs, learner_id="alice")
    assert report["personal"] is None


def test_build_progress_report_personal_section_filters_by_learner_id(fixture_questions):
    logs = [
        {
            "learner_id": "alice", "date": "2026-01-01 00:00:00", "total_answered": 1, "correct": 1,
            "questions": [{"id": 1, "result": "correct"}],
        },
        {
            "learner_id": "bob", "date": "2026-01-01 00:00:00", "total_answered": 1, "correct": 0,
            "questions": [{"id": 2, "result": "incorrect"}],
        },
    ]
    report = app.build_progress_report(logs, learner_id="alice")
    assert report["cohort"]["sessions"] == 2  # cohort section always covers everyone
    assert report["personal"]["learner_id"] == "alice"
    assert report["personal"]["sessions"] == 1
    assert report["personal"]["domain_stats"] == {"Domain A": {"correct": 1, "total": 1}}


def test_render_progress_report_markdown_includes_personal_section_when_present(fixture_questions):
    logs = [
        {
            "learner_id": "alice", "date": "2026-01-01 00:00:00", "total_answered": 1, "correct": 1,
            "questions": [{"id": 1, "result": "correct"}],
        },
    ]
    report = app.build_progress_report(logs, learner_id="alice")
    md = app.render_progress_report_markdown(report)
    assert "# CCA-F Study Report" in md
    assert "Personal (Learner ID: alice)" in md
    assert "Domain A" in md


def test_render_progress_report_markdown_omits_personal_section_when_absent(fixture_questions):
    report = app.build_progress_report([], learner_id="")
    md = app.render_progress_report_markdown(report)
    assert "Personal" not in md


def test_render_progress_report_html_includes_personal_section_when_present(fixture_questions):
    logs = [
        {
            "learner_id": "alice", "date": "2026-01-01 00:00:00", "total_answered": 1, "correct": 1,
            "questions": [{"id": 1, "result": "correct"}],
        },
    ]
    report = app.build_progress_report(logs, learner_id="alice")
    html = app.render_progress_report_html(report)
    assert "<html>" in html
    assert "Learner ID: alice" in html


# ─── git_push_checkpoint ─────────────────────────────────────────────────────


def test_git_push_checkpoint_commits_and_pushes_when_there_are_changes(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, returncode=1)  # non-zero => changes staged
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(app.subprocess, "run", fake_run)

    ok, message = app.git_push_checkpoint("sess1")

    assert ok is True
    assert "pushed" in message.lower()
    assert ["git", "commit", "-m", "chore: update checkpoint after session (sess1)"] in calls
    assert ["git", "push"] in calls


def test_git_push_checkpoint_no_op_when_nothing_changed(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, returncode=0)  # zero => nothing staged
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(app.subprocess, "run", fake_run)

    ok, message = app.git_push_checkpoint("sess1")

    assert ok is True
    assert "nothing new to commit" in message.lower()
    assert not any(cmd[:2] == ["git", "commit"] for cmd in calls)
    assert not any(cmd[:2] == ["git", "push"] for cmd in calls)


def test_git_push_checkpoint_reports_failure_on_git_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "add"]:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"fatal: not a git repository")
        raise AssertionError(f"should not reach {cmd}")

    monkeypatch.setattr(app.subprocess, "run", fake_run)

    ok, message = app.git_push_checkpoint("sess1")

    assert ok is False
    assert "fatal: not a git repository" in message
