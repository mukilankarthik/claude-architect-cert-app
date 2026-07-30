"""Coverage for scripts/lint_questions.py's pure checking logic (lint() and
find_duplicate_ids()) against small fixture question sets — not the real
questions.json, which is exercised by simply running the script."""

from scripts import lint_questions


def make_question(id, correct="A", choices=None, explanation="A. yes ✅ Correct.\nB. no ❌ Incorrect.", question="Q?"):
    return {
        "id": id,
        "question": question,
        "choices": choices or {"A": "yes", "B": "no"},
        "correct": correct,
        "explanation": explanation,
        "domain": "Domain A",
    }


def test_lint_clean_question_has_no_errors_or_unparsed():
    report = lint_questions.lint([make_question(1)])
    assert report["errors"] == {}
    assert report["duplicate_ids"] == {}
    assert report["unparsed_explanations"] == []


def test_lint_flags_empty_explanation():
    report = lint_questions.lint([make_question(1, explanation="   ")])
    assert report["errors"]["empty_explanation"] == [1]


def test_lint_flags_too_few_choices():
    report = lint_questions.lint([make_question(1, choices={"A": "only one"})])
    assert report["errors"]["too_few_choices"] == [1]


def test_lint_flags_correct_letter_missing_from_choices():
    report = lint_questions.lint([make_question(1, correct="Z")])
    assert report["errors"]["correct_letter_missing_from_choices"] == [1]


def test_lint_flags_empty_question_text():
    report = lint_questions.lint([make_question(1, question="  ")])
    assert report["errors"]["empty_question_text"] == [1]


def test_lint_flags_unparsed_explanation_that_does_not_echo_choice_text():
    report = lint_questions.lint([make_question(1, explanation="This explanation never repeats the choice wording.")])
    assert report["unparsed_explanations"] == [1]


def test_lint_only_reports_categories_that_have_hits():
    report = lint_questions.lint([make_question(1)])
    assert "empty_explanation" not in report["errors"]


def test_lint_total_questions_reflects_input_size():
    report = lint_questions.lint([make_question(1), make_question(2)])
    assert report["total_questions"] == 2


def test_find_duplicate_ids_detects_repeats():
    questions = [make_question(1), make_question(1), make_question(2)]
    assert lint_questions.find_duplicate_ids(questions) == {1: 2}


def test_find_duplicate_ids_empty_when_all_unique():
    questions = [make_question(1), make_question(2)]
    assert lint_questions.find_duplicate_ids(questions) == {}
