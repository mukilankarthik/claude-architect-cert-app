#!/usr/bin/env python3
"""Lint questions.json for structural issues and explanation-parsing fallbacks.

Reuses app.py's actual parse_explanation() rather than reimplementing its
regex convention, so "flagged here" always means "renders as a raw-text
fallback in the app" — see CLAUDE.md's "explanation string format" section
for the per-choice-echo convention this checks against.

Usage:
    poetry run python scripts/lint_questions.py [--verbose] [--json] [--strict]

Exit code is non-zero if any structural error is found, or (with --strict)
if any explanation fails to parse.
"""

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# app.py is a Streamlit script; importing it outside `streamlit run` is fine (the
# tests do the same — see tests/test_app_logic.py's module docstring), but it emits
# one harmless "missing ScriptRunContext" warning per st.* call made at import time
# (the home page renders once as a side effect of module load). Suppressed here so
# it doesn't drown out the actual lint report.
with contextlib.redirect_stderr(io.StringIO()):
    import app  # noqa: E402  (path must be set up first)

# Structural problems: something is actually broken, not just a rendering fallback.
ERROR_CHECKS = {
    "empty_explanation": lambda q: not q.get("explanation", "").strip(),
    "too_few_choices": lambda q: len(q.get("choices", {})) < 2,
    "correct_letter_missing_from_choices": lambda q: q.get("correct") not in q.get("choices", {}),
    "empty_question_text": lambda q: not q.get("question", "").strip(),
}


def find_duplicate_ids(questions: list[dict]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for q in questions:
        counts[q["id"]] = counts.get(q["id"], 0) + 1
    return {qid: n for qid, n in counts.items() if n > 1}


def lint(questions: list[dict]) -> dict:
    errors: dict[str, list[int]] = {name: [] for name in ERROR_CHECKS}
    unparsed: list[int] = []

    for q in questions:
        for name, check in ERROR_CHECKS.items():
            if check(q):
                errors[name].append(q["id"])
        if app.parse_explanation(q.get("explanation", ""), q.get("choices", {}), q.get("correct")) is None:
            unparsed.append(q["id"])

    duplicate_ids = find_duplicate_ids(questions)

    return {
        "total_questions": len(questions),
        "errors": {name: ids for name, ids in errors.items() if ids},
        "duplicate_ids": duplicate_ids,
        "unparsed_explanations": unparsed,
    }


def id_to_domain(questions: list[dict]) -> dict[int, str | None]:
    return {q["id"]: q.get("domain") for q in questions}


def print_report(report: dict, questions: list[dict], verbose: bool) -> None:
    domains = id_to_domain(questions)

    def describe(ids: list[int]) -> str:
        if not verbose:
            return f"{len(ids)} question(s): {ids[:10]}{' ...' if len(ids) > 10 else ''}"
        lines = [f"  #{qid} (domain: {domains.get(qid) or '—'})" for qid in ids]
        return f"{len(ids)} question(s):\n" + "\n".join(lines)

    print(f"Checked {report['total_questions']} questions in questions.json\n")

    if report["duplicate_ids"]:
        print("❌ Duplicate ids:")
        for qid, count in report["duplicate_ids"].items():
            print(f"  #{qid} appears {count} times")
        print()

    if report["errors"]:
        for name, ids in report["errors"].items():
            print(f"❌ {name}: {describe(ids)}")
        print()
    else:
        print("✅ No structural errors found.\n")

    if report["unparsed_explanations"]:
        print(f"⚠️  Explanations that fall back to raw rendering: {describe(report['unparsed_explanations'])}")
        print(
            "   (parse_explanation() couldn't locate every choice's own text inside the "
            "explanation string — see app.py's parse_explanation docstring and CLAUDE.md's "
            "\"explanation string format\" section)\n"
        )
    else:
        print("✅ Every explanation parses into per-choice blocks.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="List every affected question id (with domain)")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON instead of text")
    parser.add_argument(
        "--strict", action="store_true",
        help="Also fail (non-zero exit) if any explanation falls back to raw rendering",
    )
    args = parser.parse_args()

    questions = app.ALL_QUESTIONS
    report = lint(questions)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, questions, args.verbose)

    has_errors = bool(report["errors"] or report["duplicate_ids"])
    has_warnings = bool(report["unparsed_explanations"])
    if has_errors or (args.strict and has_warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
