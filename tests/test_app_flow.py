"""End-to-end coverage of the Streamlit UI flow using streamlit.testing.v1.AppTest —
the officially supported way to drive a Streamlit script headlessly in CI without
a browser. Mirrors the manual Playwright pass described in CLAUDE.md but runs fast
enough to gate every PR.

Every test gets an isolated checkpoint/session-log directory via
isolated_storage_paths (tests/conftest.py) so nothing here ever touches the
repo's real checkpoint.json or session_logs/.

Note on entering exam mode: real user flow starts an exam by clicking a home-page
button (which calls start_exam() then st.rerun() mid-script). AppTest's element
tree doesn't always retract widgets rendered during that pre-rerun half-pass — the
sidebar's home-only toggles (no value yet in session_state) survive into the
resulting tree and blow up on the *next* rerun's widget-state serialization. Since
that's a testing-harness artifact rather than something a real browser session hits,
exam-start tests that need multiple further reruns seed session_state directly
(mirroring what start_exam() would have set) instead of clicking through home mode.
The one-shot "click Start ... Mode" tests below don't hit this because they don't
need any additional rerun afterward.
"""

import json

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def at(isolated_storage_paths):
    """A fresh AppTest instance with an isolated storage backend, run once."""
    test = AppTest.from_file("app.py")
    test.run(timeout=30)
    assert test.exception == [], f"app.py raised on initial load: {test.exception}"
    return test


@pytest.fixture
def all_questions():
    with open("questions.json", encoding="utf-8") as f:
        return json.load(f)


def _click(test, label, *, exact=True):
    for b in test.button:
        if (b.label == label) if exact else (label in b.label):
            b.click()
            return
    raise AssertionError(f"no button labeled {label!r} found; available: {[b.label for b in test.button]}")


def _seed_learning_exam(test, questions):
    """Put the app straight into an in-progress learning-mode exam, equivalent to
    what start_exam('learning') would produce, without going through the home-page
    button click (see module docstring for why)."""
    test.session_state["mode"] = "exam"
    test.session_state["exam_type"] = "learning"
    test.session_state["questions"] = questions
    test.session_state["current_idx"] = 0
    test.session_state["answers"] = {}
    test.session_state["session_id"] = "test-session"
    test.session_state["session_finished"] = False
    test.run(timeout=30)
    assert test.exception == []
    return test


def test_home_page_loads_without_error(at):
    assert at.session_state["mode"] == "home"
    assert any("Start Learning Mode" in b.label for b in at.button)


def test_learning_mode_start_draws_from_full_bank_when_checkpoint_empty(at):
    _click(at, "🚀 Start Learning Mode", exact=False)
    at.run(timeout=30)

    assert at.exception == []
    assert at.session_state["mode"] == "exam"
    assert at.session_state["exam_type"] == "learning"
    assert len(at.session_state["questions"]) > 0


def test_timed_exam_draws_fixed_size_pool_and_sets_a_deadline(at):
    _click(at, "Start Timed Mock Exam", exact=False)
    at.run(timeout=30)

    assert at.exception == []
    assert at.session_state["exam_type"] == "timed"
    assert at.session_state["exam_deadline"] is not None
    # mirrors TIMED_EXAM_QUESTION_COUNT unless the bank itself is smaller
    assert len(at.session_state["questions"]) <= 60
    assert len(at.session_state["questions"]) > 0


def test_drill_mode_button_disabled_without_learner_history(at):
    drill_buttons = [b for b in at.button if "Drill Mode" in b.label]
    assert len(drill_buttons) == 1
    assert drill_buttons[0].disabled is True
    assert "(0)" in drill_buttons[0].label


def test_learning_mode_falls_back_to_full_bank_once_checkpoint_is_exhausted(isolated_storage_paths, all_questions):
    all_ids = [q["id"] for q in all_questions]
    isolated_storage_paths.SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    isolated_storage_paths.CHECKPOINT_PATH.write_text(
        json.dumps({"used_ids": all_ids, "sessions": []}), encoding="utf-8"
    )

    test = AppTest.from_file("app.py")
    test.run(timeout=30)
    _click(test, "🚀 Start Learning Mode", exact=False)
    test.run(timeout=30)

    assert test.exception == []
    assert len(test.session_state["questions"]) == len(all_ids)


def test_answering_a_question_persists_to_checkpoint_immediately(isolated_storage_paths, all_questions):
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:3])
    first_question_id = test.session_state["questions"][0]["id"]

    test.radio[0].set_value(test.radio[0].options[0])
    test.run(timeout=30)
    _click(test, "Submit Answer")
    test.run(timeout=30)

    assert test.exception == []
    assert test.session_state["answers"][0] is not None

    checkpoint_path = isolated_storage_paths.CHECKPOINT_PATH
    assert checkpoint_path.exists()
    saved = json.loads(checkpoint_path.read_text())
    assert first_question_id in saved["used_ids"]


def test_submit_answer_button_disabled_until_an_option_is_selected(all_questions):
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:1])
    submit = next(b for b in test.button if b.label == "Submit Answer")
    assert submit.disabled is True


def test_next_question_advances_current_idx(all_questions):
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:2])

    test.radio[0].set_value(test.radio[0].options[0])
    test.run(timeout=30)
    _click(test, "Submit Answer")
    test.run(timeout=30)
    _click(test, "Next Question", exact=False)
    test.run(timeout=30)

    assert test.exception == []
    assert test.session_state["current_idx"] == 1


def test_full_learning_session_finish_produces_results(all_questions):
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:2])

    test.radio[0].set_value(test.radio[0].options[0])
    test.run(timeout=30)
    _click(test, "Submit Answer")
    test.run(timeout=30)
    _click(test, "🏁 Finish Session")
    test.run(timeout=30)

    assert test.exception == []
    assert test.session_state["mode"] == "results"
    assert test.session_state["session_finished"] is True


def test_finishing_without_answering_every_question_reports_partial_total(all_questions):
    """Corner case: a learner can finish with unanswered questions still in the pool."""
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:5])

    test.radio[0].set_value(test.radio[0].options[0])
    test.run(timeout=30)
    _click(test, "Submit Answer")
    test.run(timeout=30)
    _click(test, "🏁 Finish Session")
    test.run(timeout=30)

    assert test.exception == []
    assert test.session_state["mode"] == "results"
    assert len(test.session_state["answers"]) == 1
    assert len(test.session_state["questions"]) == 5


def test_review_mode_reachable_after_results(all_questions):
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:2])

    test.radio[0].set_value(test.radio[0].options[0])
    test.run(timeout=30)
    _click(test, "Submit Answer")
    test.run(timeout=30)
    _click(test, "🏁 Finish Session")
    test.run(timeout=30)
    _click(test, "Review Answers", exact=False)
    test.run(timeout=30)

    assert test.exception == []
    assert test.session_state["mode"] == "review"


def test_reset_checkpoint_clears_used_ids(isolated_storage_paths, all_questions):
    test = _seed_learning_exam(AppTest.from_file("app.py"), all_questions[:1])

    test.radio[0].set_value(test.radio[0].options[0])
    test.run(timeout=30)
    _click(test, "Submit Answer")
    test.run(timeout=30)
    assert isolated_storage_paths.CHECKPOINT_PATH.exists()

    _click(test, "🏁 Finish Session")
    test.run(timeout=30)
    _click(test, "🔄 New Session", exact=False)
    test.run(timeout=30)
    assert test.session_state["mode"] == "home"

    _click(test, "🔄 Reset Checkpoint")
    test.run(timeout=30)

    assert test.exception == []
    assert not isolated_storage_paths.CHECKPOINT_PATH.exists()
