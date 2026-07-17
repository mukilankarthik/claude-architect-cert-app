"""Claude Certified Architect - Foundations (CCA-F) interactive study guide.

A single-page Streamlit app: cohorts work through a shared question bank,
progress is checkpointed so questions aren't repeated across sessions, and
a "Generate Questions" tool can expand the bank from any source document
using the user's own choice of AI provider (Anthropic, OpenAI, or Gemini).

Persistence is pluggable (see storage.py): local JSON files by default,
or a shared Postgres database when DATABASE_URL is set, which is what
makes checkpoint state survive a cloud deployment across restarts and
multiple instances.
"""

import io
import json
import os
import random
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pdfplumber
import streamlit as st
from streamlit_pdf_viewer import pdf_viewer

from ai_providers import PROVIDERS, AIProviderError, generate_text
from storage import get_storage

# ─── Constants ──────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
MATERIALS_DIR = BASE_DIR / "materials"
QUESTIONS_PATH = BASE_DIR / "questions.json"
IS_GIT_REPO = (BASE_DIR / ".git").exists()

PASS_THRESHOLD_PCT = 70
GENERATION_MAX_SOURCE_CHARS = 12_000  # keeps the prompt within a comfortable context budget across providers

st.set_page_config(
    page_title="Claude Certified Architect - Study Guide",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Theme ──────────────────────────────────────────────────────────────────
# Slate + teal palette: neutral and provider-agnostic (the previous amber/clay
# accent leaned on Anthropic's own brand color, which reads oddly now that the
# generator supports OpenAI and Gemini too). Colors are expressed as CSS
# variables so every component restyles from one place.

THEME_CSS = """
<style>
:root {
    --cca-accent: #0d9488;
    --cca-accent-soft: rgba(13, 148, 136, 0.14);
    --cca-correct-bg: rgba(34, 153, 84, 0.16);
    --cca-correct-border: rgba(34, 153, 84, 0.55);
    --cca-wrong-bg: rgba(220, 38, 38, 0.14);
    --cca-wrong-border: rgba(220, 38, 38, 0.5);
    --cca-card-bg: rgba(127, 127, 127, 0.06);
    --cca-card-border: rgba(127, 127, 127, 0.18);
    --cca-text-muted: rgba(127, 127, 127, 0.9);
}

@keyframes ccaFadeInUp {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}
@keyframes ccaGradientShift {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
@keyframes ccaPop {
    0% { transform: scale(0.97); }
    60% { transform: scale(1.01); }
    100% { transform: scale(1); }
}

.cca-hero {
    background: linear-gradient(120deg, #1e293b 0%, var(--cca-accent) 55%, #1e293b 100%);
    background-size: 200% 200%;
    animation: ccaGradientShift 10s ease infinite, ccaFadeInUp 0.5s ease both;
    border-radius: 16px;
    padding: 1.8rem 2.2rem;
    color: #fff !important;
    margin-bottom: 1.2rem;
    box-shadow: 0 8px 24px rgba(13, 148, 136, 0.18);
}
.cca-hero h1, .cca-hero p { color: #fff !important; margin: 0; }
.cca-hero p { opacity: 0.92; margin-top: 0.4rem; }

.cca-card {
    background: var(--cca-card-bg);
    border: 1px solid var(--cca-card-border);
    border-radius: 14px;
    padding: 1.4rem 1.6rem;
    margin: 0.6rem 0 1.3rem 0;
    font-size: 1.08rem;
    line-height: 1.65;
    animation: ccaFadeInUp 0.35s ease both;
    transition: border-color 0.2s ease;
}
.cca-card:hover { border-color: var(--cca-accent); }

.cca-choice {
    padding: 13px 18px;
    margin: 10px 0;
    border-radius: 12px;
    border: 1px solid var(--cca-card-border);
    background: var(--cca-card-bg);
    line-height: 1.55;
    transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    animation: ccaFadeInUp 0.4s ease both;
}
.cca-choice:hover {
    transform: translateX(3px);
    box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08);
    border-color: var(--cca-accent);
}
.cca-choice-correct {
    background: var(--cca-correct-bg) !important;
    border: 1px solid var(--cca-correct-border) !important;
    animation: ccaFadeInUp 0.4s ease both, ccaPop 0.4s ease both;
}
.cca-choice-wrong {
    background: var(--cca-wrong-bg) !important;
    border: 1px solid var(--cca-wrong-border) !important;
}

.cca-explain-block {
    display: flex;
    gap: 14px;
    align-items: flex-start;
    padding: 12px 16px;
    margin: 10px 0;
    border-radius: 12px;
    border-left: 4px solid var(--cca-card-border);
    background: var(--cca-card-bg);
    animation: ccaFadeInUp 0.4s ease both;
    transition: transform 0.15s ease;
}
.cca-explain-block:hover { transform: translateX(2px); }
.cca-explain-block.cca-explain-correct {
    border-left-color: var(--cca-correct-border);
    background: var(--cca-correct-bg);
}
.cca-explain-block.cca-explain-wrong {
    border-left-color: var(--cca-wrong-border);
    background: rgba(127, 127, 127, 0.045);
}
.cca-explain-head {
    flex: 0 0 auto;
    font-weight: 700;
    font-size: 0.95rem;
    white-space: nowrap;
    padding-top: 1px;
}
.cca-explain-body {
    flex: 1 1 auto;
    line-height: 1.55;
    opacity: 0.92;
}

.cca-badge {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    padding: 3px 10px;
    border-radius: 999px;
    background: var(--cca-accent-soft);
    color: var(--cca-accent);
    border: 1px solid var(--cca-accent);
    margin-bottom: 0.4rem;
}
.cca-badge-muted {
    background: transparent;
    border-color: var(--cca-card-border);
    color: var(--cca-text-muted);
}
.cca-stat-card {
    background: var(--cca-card-bg);
    border: 1px solid var(--cca-card-border);
    border-radius: 12px;
    padding: 1rem 1rem;
    text-align: center;
    transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
    animation: ccaFadeInUp 0.4s ease both;
}
.cca-stat-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);
    border-color: var(--cca-accent);
}
.cca-stat-card .cca-stat-value { font-size: 1.7rem; font-weight: 700; }
.cca-stat-card .cca-stat-label { font-size: 0.78rem; color: var(--cca-text-muted); margin-top: 0.15rem; }
.cca-banner {
    border-radius: 14px;
    padding: 1.05rem 1.4rem;
    font-weight: 600;
    font-size: 1.05rem;
    margin: 0.6rem 0 1.1rem 0;
    animation: ccaFadeInUp 0.35s ease both, ccaPop 0.4s ease both;
}
.cca-banner-pass {
    background: var(--cca-correct-bg);
    border: 1px solid var(--cca-correct-border);
}
.cca-banner-fail {
    background: var(--cca-wrong-bg);
    border: 1px solid var(--cca-wrong-border);
}

/* Buttons: consistent lift-on-hover across the whole app */
.stButton > button {
    transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease !important;
}
.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 14px rgba(13, 148, 136, 0.18);
    border-color: var(--cca-accent) !important;
    color: var(--cca-accent) !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 6px 16px rgba(13, 148, 136, 0.35);
    filter: brightness(1.06);
    color: #fff !important;
}

/* Radio choice rows (unanswered question) rendered as clickable cards */
div[data-testid="stRadio"] > div { gap: 8px; }
div[data-testid="stRadio"] label {
    padding: 12px 16px !important;
    border: 1px solid var(--cca-card-border);
    border-radius: 12px;
    width: 100%;
    transition: transform 0.15s ease, border-color 0.15s ease, background 0.15s ease;
}
div[data-testid="stRadio"] label:hover {
    border-color: var(--cca-accent);
    background: var(--cca-accent-soft);
    transform: translateX(3px);
}

/* Progress bar: gradient fill instead of flat color */
div[data-testid="stProgress"] > div > div > div {
    background: linear-gradient(90deg, var(--cca-accent) 0%, #34d399 100%) !important;
    transition: width 0.4s ease;
}

/* Expander: align with the card language instead of Streamlit's default box */
div[data-testid="stExpander"] {
    border: 1px solid var(--cca-card-border) !important;
    border-radius: 12px !important;
    overflow: hidden;
}

section[data-testid="stSidebar"] button {
    white-space: nowrap;
    padding-left: 0.25rem;
    padding-right: 0.25rem;
    min-width: 0;
}
</style>
"""

st.markdown(THEME_CSS, unsafe_allow_html=True)

# ─── Storage & data loaders ─────────────────────────────────────────────────


@st.cache_resource
def get_storage_backend():
    """One storage instance per server process: Postgres if DATABASE_URL is
    set (required for durable state on ephemeral/multi-instance cloud
    hosts), local JSON files otherwise."""
    return get_storage(os.environ.get("DATABASE_URL"))


STORAGE = get_storage_backend()


@st.cache_data
def load_questions() -> list[dict]:
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_reference_links() -> dict:
    with open(MATERIALS_DIR / "reference_links.json", encoding="utf-8") as f:
        return json.load(f)


ALL_QUESTIONS = load_questions()

# ─── Checkpoint & session log helpers ───────────────────────────────────────


def get_unused_questions() -> tuple[list[dict], int]:
    """Questions not yet answered in a prior session, plus how many have been used."""
    checkpoint = STORAGE.load_checkpoint()
    used = set(checkpoint["used_ids"])
    unused = [q for q in ALL_QUESTIONS if q["id"] not in used]
    return unused, len(used)


def build_and_store_session_log(session_id: str, answers: dict, questions: list, cohort: str) -> None:
    log = {
        "session_id": session_id,
        "cohort": cohort,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_answered": len(answers),
        "correct": sum(1 for idx, q in enumerate(questions) if answers.get(idx) == q["correct"]),
        "questions": [
            {
                "id": q["id"],
                "question": q["question"],
                "chosen": answers.get(idx),
                "correct": q["correct"],
                "result": (
                    "correct" if answers.get(idx) == q["correct"]
                    else "skipped" if answers.get(idx) is None
                    else "incorrect"
                ),
            }
            for idx, q in enumerate(questions)
        ],
    }
    STORAGE.write_session_log(session_id, log)


def git_push_checkpoint(session_id: str) -> tuple[bool, str]:
    """Stage checkpoint + session log, commit and push. Only relevant for the
    local-file backend on a shared machine/VM — see README's cloud section
    for why this doesn't apply once DATABASE_URL is in use."""
    log_file = f"session_logs/{session_id}.json"
    try:
        subprocess.run(
            ["git", "add", "checkpoint.json", log_file],
            cwd=BASE_DIR, check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=BASE_DIR, capture_output=True,
        )
        if result.returncode == 0:
            return True, "Nothing new to commit — checkpoint already up to date."
        subprocess.run(
            ["git", "commit", "-m", f"chore: update checkpoint after session ({session_id})"],
            cwd=BASE_DIR, check=True, capture_output=True,
        )
        subprocess.run(["git", "push"], cwd=BASE_DIR, check=True, capture_output=True)
        return True, "✅ Checkpoint pushed to your git remote successfully."
    except subprocess.CalledProcessError as e:
        return False, f"Git error: {e.stderr.decode().strip() or str(e)}"

# ─── Session state ──────────────────────────────────────────────────────────


def init_state() -> None:
    defaults = {
        "mode": "home",
        "questions": [],
        "current_idx": 0,
        "answers": {},           # idx -> chosen letter (committed on Submit)
        "shuffle": True,
        "show_explanation": True,
        "cohort_name": "My Team",
        "session_id": None,
        "session_finished": False,
        "git_push_status": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()

# ─── Rendering helpers (shared by exam mode and review mode) ───────────────


def render_nav_header(key_prefix: str, idx: int, total: int, title_markdown: str, q: dict) -> tuple[bool, bool]:
    """Title + domain/ID badges + Prev/Next buttons. Returns (prev_clicked, next_clicked)."""
    col_title, col_prev, col_next = st.columns([6, 1, 1])
    with col_title:
        st.markdown(title_markdown)
        domain = q.get("domain")
        badge_html = f"<span class='cca-badge'>{domain}</span> " if domain else ""
        badge_html += f"<span class='cca-badge cca-badge-muted'>Q#{q['id']}</span>"
        st.markdown(badge_html, unsafe_allow_html=True)
    with col_prev:
        prev_clicked = st.button("◀ Prev", disabled=idx == 0, use_container_width=True, key=f"{key_prefix}_prev")
    with col_next:
        next_clicked = st.button("Next ▶", disabled=idx == total - 1, use_container_width=True, key=f"{key_prefix}_next")
    return prev_clicked, next_clicked


def render_choice_rows(q: dict, chosen: str | None, tag_chosen_answer: bool = False) -> None:
    """Render every answer choice as a pill, highlighting the correct one and
    (if wrong) the user's own pick."""
    for letter, text in q["choices"].items():
        if letter == q["correct"]:
            st.markdown(
                f"<div class='cca-choice cca-choice-correct'>✅ <strong>{letter}.</strong> {text}</div>",
                unsafe_allow_html=True,
            )
        elif letter == chosen:
            suffix = " <em>(your answer)</em>" if tag_chosen_answer else ""
            st.markdown(
                f"<div class='cca-choice cca-choice-wrong'>❌ <strong>{letter}.</strong> {text}{suffix}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div class='cca-choice'>&nbsp;&nbsp;&nbsp;<strong>{letter}.</strong> {text}</div>",
                unsafe_allow_html=True,
            )


VERDICT_RE = re.compile(r"(✅\s*Correct\.?|❌\s*Incorrect\.?|\bCorrect\.|\bIncorrect\.)", re.I)


def parse_explanation(explanation: str, choices: dict, correct_letter: str) -> list[dict] | None:
    """Split a flat 'A. ...choice text... verdict. reasoning B. ...' explanation string
    into one block per choice, keyed off where each choice's own text reappears in the
    explanation. Returns None if the source text doesn't follow that convention, so the
    caller can fall back to rendering it as-is."""
    positions = []
    for letter, text in choices.items():
        prefix = re.sub(r"\s+", " ", text.strip())[:18]
        pattern = re.escape(letter) + r"\.\s*" + re.escape(prefix)
        m = re.search(pattern, explanation)
        if m:
            positions.append((m.start(), letter))
    if len(positions) < len(choices):
        return None

    positions.sort()
    segments = []
    for i, (start, letter) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(explanation)
        chunk = explanation[start:end].strip()
        vm = VERDICT_RE.search(chunk)
        reasoning = chunk[vm.end():].strip() if vm else re.sub(rf"^{letter}\.\s*", "", chunk)
        segments.append({
            "letter": letter,
            "is_correct": letter == correct_letter,
            "reasoning": re.sub(r"\s+", " ", reasoning).strip() or "—",
        })
    segments.sort(key=lambda s: s["letter"])
    return segments


def render_explanation_block(q: dict) -> None:
    """Render the explanation as one styled block per answer choice instead of a
    single wall of concatenated text."""
    segments = parse_explanation(q["explanation"], q["choices"], q["correct"])
    if segments is None:
        st.markdown(q["explanation"])
        return
    for seg in segments:
        icon = "✅" if seg["is_correct"] else "❌"
        cls = "cca-explain-correct" if seg["is_correct"] else "cca-explain-wrong"
        st.markdown(
            f"<div class='cca-explain-block {cls}'>"
            f"<div class='cca-explain-head'>{icon} {seg['letter']}</div>"
            f"<div class='cca-explain-body'>{seg['reasoning']}</div></div>",
            unsafe_allow_html=True,
        )


def render_stat_cards(items: list[tuple]) -> None:
    """items: list of (value, label) pairs, rendered as equal-width stat cards."""
    for col, (value, label) in zip(st.columns(len(items)), items):
        col.markdown(
            f"<div class='cca-stat-card'><div class='cca-stat-value'>{value}</div>"
            f"<div class='cca-stat-label'>{label}</div></div>",
            unsafe_allow_html=True,
        )

# ─── Exam flow helpers ──────────────────────────────────────────────────────


def start_exam() -> None:
    unused, _ = get_unused_questions()
    pool = unused if unused else ALL_QUESTIONS.copy()
    if st.session_state.shuffle:
        random.shuffle(pool)
    st.session_state.questions = pool
    st.session_state.current_idx = 0
    st.session_state.answers = {}
    st.session_state.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.session_state.session_finished = False
    st.session_state.mode = "exam"


def finish_session() -> None:
    """Persist checkpoint + session log, then move to the results screen."""
    questions = st.session_state.questions
    answers = st.session_state.answers
    correct = sum(1 for idx, q in enumerate(questions) if answers.get(idx) == q["correct"])
    total_answered = len(answers)
    pct = int(correct / total_answered * 100) if total_answered else 0

    STORAGE.finalize_checkpoint({
        "session_id": st.session_state.session_id,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cohort": st.session_state.cohort_name,
        "correct": correct,
        "total_answered": total_answered,
        "pct": pct,
    })
    build_and_store_session_log(st.session_state.session_id, answers, questions, st.session_state.cohort_name)
    st.session_state.git_push_status = None
    st.session_state.session_finished = True
    st.session_state.mode = "results"

# ─── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🏛️ CCA-F Study Guide")
    st.caption("Claude Certified Architect – Foundations")
    st.divider()

    if st.session_state.mode != "exam":
        col_home, col_mat = st.columns(2)
        if col_home.button("🏠 Home", use_container_width=True, disabled=st.session_state.mode == "home"):
            st.session_state.mode = "home"
            st.rerun()
        if col_mat.button("📚 Materials", use_container_width=True, disabled=st.session_state.mode == "materials"):
            st.session_state.mode = "materials"
            st.rerun()
        st.divider()

    if st.session_state.mode == "home":
        st.subheader("⚙️ Session Settings")
        st.session_state.cohort_name = st.text_input("Cohort / Team Name", value=st.session_state.cohort_name)
        st.session_state.shuffle = st.toggle("Shuffle questions", value=st.session_state.shuffle)
        st.session_state.show_explanation = st.toggle(
            "Show explanation after each answer", value=st.session_state.show_explanation
        )
        st.divider()

        unused, used_count = get_unused_questions()
        st.subheader("📌 Checkpoint")
        st.caption("Answered questions are skipped in future sessions automatically.")
        col_a, col_b = st.columns(2)
        col_a.metric("Covered", f"{used_count}/{len(ALL_QUESTIONS)}")
        col_b.metric("Remaining", len(unused))
        if used_count > 0:
            cp = STORAGE.load_checkpoint()
            if cp["sessions"]:
                last = cp["sessions"][-1]
                st.caption(
                    f"Last session: {last.get('date', '?')} · "
                    f"{last.get('cohort', '?')} · "
                    f"{last.get('correct', '?')}/{last.get('total_answered', '?')} correct"
                )
            if st.button("🔄 Reset Checkpoint", use_container_width=True):
                STORAGE.reset_checkpoint()
                st.success("Checkpoint cleared.")
                st.rerun()
        else:
            st.caption("No sessions recorded yet.")
        st.divider()
        st.caption(f"Storage backend: **{STORAGE.backend_name}**")
        st.info(f"📚 {len(ALL_QUESTIONS)} total · {len(unused)} remaining")

    elif st.session_state.mode == "exam":
        questions = st.session_state.questions
        answered = len(st.session_state.answers)
        total = len(questions)
        st.metric("Answered", f"{answered}/{total}")
        st.progress(answered / total if total else 0)
        st.divider()

        st.subheader("Navigator")
        cols = st.columns(4)
        for i in range(total):
            col = cols[i % 4]
            if i in st.session_state.answers:
                label = "✅" if st.session_state.answers[i] == questions[i]["correct"] else "❌"
            elif i == st.session_state.current_idx:
                label = f"**{i + 1}**"
            else:
                label = str(i + 1)
            if col.button(label, key=f"nav_{i}", use_container_width=True):
                st.session_state.current_idx = i
                st.rerun()

        st.divider()
        if st.button("🏁 Finish Session", use_container_width=True, type="primary"):
            finish_session()
            st.rerun()

    elif st.session_state.mode == "results":
        if st.button("🔄 New Session", use_container_width=True, type="primary"):
            st.session_state.mode = "home"
            st.rerun()
        if st.button("📖 Review Answers", use_container_width=True):
            st.session_state.mode = "review"
            st.session_state.current_idx = 0
            st.rerun()

    elif st.session_state.mode == "review":
        questions = st.session_state.questions
        total = len(questions)
        st.subheader("Navigator")
        cols = st.columns(4)
        for i in range(total):
            col = cols[i % 4]
            if i in st.session_state.answers:
                label = "✅" if st.session_state.answers[i] == questions[i]["correct"] else "❌"
            else:
                label = "⬜"
            if col.button(label, key=f"rnav_{i}", use_container_width=True):
                st.session_state.current_idx = i
                st.rerun()
        st.divider()
        if st.button("📊 Back to Results", use_container_width=True):
            st.session_state.mode = "results"
            st.rerun()

# ─── HOME ───────────────────────────────────────────────────────────────────

if st.session_state.mode == "home":
    st.markdown(
        f"<div class='cca-hero'><h1>🏛️ Claude Certified Architect – Foundations</h1>"
        f"<p>Welcome, {st.session_state.cohort_name}!</p></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        This interactive study guide helps your team prepare for the **CCA-F** exam.

        ### How it works
        - Each session works through all remaining questions in sequence
        - Select your answer and click **Submit** to confirm — you can't change it after
        - Explanations are shown after each submission (toggle in sidebar)
        - Hit **Finish Session** anytime — progress is saved immediately after every answer
        - Questions answered in past sessions are automatically skipped
        """
    )
    st.divider()

    unused, used_count = get_unused_questions()
    render_stat_cards([
        (len(ALL_QUESTIONS), "Total Questions"),
        (used_count, "Already Covered"),
        (len(unused), "Available Today"),
    ])

    if not unused:
        st.warning("🔁 All questions have been covered! The next session will cycle back to the beginning. Reset the checkpoint to start fresh.")

    st.divider()
    if st.button("🚀 Start Study Session", type="primary", use_container_width=True):
        start_exam()
        st.rerun()

# ─── EXAM ───────────────────────────────────────────────────────────────────

elif st.session_state.mode == "exam":
    questions = st.session_state.questions
    idx = st.session_state.current_idx
    q = questions[idx]
    total = len(questions)
    already_answered = idx in st.session_state.answers
    chosen = st.session_state.answers.get(idx)

    prev_clicked, next_clicked = render_nav_header("exam", idx, total, f"### Question {idx + 1} of {total}", q)
    if prev_clicked:
        st.session_state.current_idx -= 1
        st.rerun()
    if next_clicked:
        st.session_state.current_idx += 1
        st.rerun()

    st.markdown(f"<div class='cca-card'><strong>{q['question']}</strong></div>", unsafe_allow_html=True)

    if already_answered:
        render_choice_rows(q, chosen, tag_chosen_answer=False)

        if st.session_state.show_explanation:
            st.write("")
            is_correct = chosen == q["correct"]
            banner_class = "cca-banner-pass" if is_correct else "cca-banner-fail"
            banner_text = (
                "✅ Correct!" if is_correct
                else f"❌ Incorrect — correct answer: <strong>{q['correct']}. {q['choices'][q['correct']]}</strong>"
            )
            st.markdown(f"<div class='cca-banner {banner_class}'>{banner_text}</div>", unsafe_allow_html=True)
            with st.expander("📖 Explanation", expanded=True):
                render_explanation_block(q)

        st.write("")
        if idx < total - 1:
            if st.button("Next Question ▶", type="primary"):
                st.session_state.current_idx += 1
                st.rerun()
        else:
            st.info("You've reached the last question. Click **Finish Session** in the sidebar to save results.")

    else:
        choice_labels = [f"{letter}. {text}" for letter, text in q["choices"].items()]
        choice_keys = list(q["choices"].keys())

        selected_label = st.radio("Select your answer:", options=choice_labels, index=None, key=f"radio_{idx}")

        st.write("")
        if st.button("Submit Answer", type="primary", disabled=selected_label is None):
            selected_letter = choice_keys[choice_labels.index(selected_label)]
            st.session_state.answers[idx] = selected_letter
            STORAGE.save_checkpoint_entry(q["id"])  # persisted immediately so a mid-session exit isn't lost
            st.rerun()

# ─── RESULTS ────────────────────────────────────────────────────────────────

elif st.session_state.mode == "results":
    questions = st.session_state.questions
    answers = st.session_state.answers
    correct = sum(1 for idx, q in enumerate(questions) if answers.get(idx) == q["correct"])
    total_answered = len(answers)
    pct = int(correct / total_answered * 100) if total_answered else 0
    passed = pct >= PASS_THRESHOLD_PCT

    st.markdown(
        f"<div class='cca-hero'><h1>📊 Session Results</h1>"
        f"<p>Team: {st.session_state.cohort_name}</p></div>",
        unsafe_allow_html=True,
    )

    render_stat_cards([
        (total_answered, "Answered"),
        (correct, "Correct"),
        (f"{pct}%", "Score"),
        ("✅ PASS" if passed else "❌ Needs Work", "Status"),
    ])

    st.write("")
    banner_class = "cca-banner-pass" if passed else "cca-banner-fail"
    banner_text = (
        f"🎉 {pct}% — above the estimated {PASS_THRESHOLD_PCT}% pass threshold."
        if passed
        else f"📚 {pct}% — keep studying, target is ~{PASS_THRESHOLD_PCT}%."
    )
    st.markdown(f"<div class='cca-banner {banner_class}'>{banner_text}</div>", unsafe_allow_html=True)

    # Score by domain, when the answered questions carry a "domain" tag
    domain_stats = {}
    for i, q in enumerate(questions):
        dom = q.get("domain")
        if not dom or i not in answers:
            continue
        stat = domain_stats.setdefault(dom, {"correct": 0, "total": 0})
        stat["total"] += 1
        if answers[i] == q["correct"]:
            stat["correct"] += 1
    if domain_stats:
        st.subheader("Score by Domain")
        st.table([
            {
                "Domain": dom,
                "Correct": f"{s['correct']}/{s['total']}",
                "Score": f"{int(s['correct'] / s['total'] * 100)}%",
            }
            for dom, s in domain_stats.items()
        ])

    st.subheader("Question Breakdown")
    st.dataframe(
        [
            {
                "Q#": i + 1,
                "ID": q["id"],
                "Status": "✅" if answers.get(i) == q["correct"] else ("❌" if answers.get(i) else "⬜ Skipped"),
                "Your Answer": answers.get(i, "—"),
                "Correct": q["correct"],
                "Question": q["question"][:80] + "...",
            }
            for i, q in enumerate(questions)
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("📌 Sync Checkpoint")

    if STORAGE.backend_name == "postgres":
        st.success("✅ Synced automatically — every teammate reads the same shared database, no manual step needed.")
    elif not IS_GIT_REPO:
        st.caption(
            f"Checkpoint and session log saved locally to `checkpoint.json` and "
            f"`session_logs/{st.session_state.session_id}.json`. This directory isn't a git repository, "
            "so there's nothing to push — set `DATABASE_URL` if you need shared state across machines."
        )
    else:
        st.caption(
            f"Checkpoint and session log saved locally to `checkpoint.json` and "
            f"`session_logs/{st.session_state.session_id}.json`."
        )
        if st.session_state.git_push_status is None:
            if st.button("☁️ Push to Git", type="primary", use_container_width=True):
                with st.spinner("Pushing to your git remote..."):
                    st.session_state.git_push_status = git_push_checkpoint(st.session_state.session_id)
                st.rerun()
            st.caption("Push so the team can pull the latest checkpoint before the next session.")
        else:
            ok, msg = st.session_state.git_push_status
            if ok:
                st.success(msg)
            else:
                st.warning(f"⚠️ Push failed: {msg}")
                if st.button("🔄 Retry Push", use_container_width=True):
                    with st.spinner("Retrying..."):
                        st.session_state.git_push_status = git_push_checkpoint(st.session_state.session_id)
                    st.rerun()
            st.caption("Team members should run `git pull` before the next session.")

    if st.button("📖 Review All Answers with Explanations", type="primary", use_container_width=True):
        st.session_state.mode = "review"
        st.session_state.current_idx = 0
        st.rerun()

# ─── REVIEW ─────────────────────────────────────────────────────────────────

elif st.session_state.mode == "review":
    questions = st.session_state.questions
    idx = st.session_state.current_idx
    q = questions[idx]
    total = len(questions)
    chosen = st.session_state.answers.get(idx)
    is_correct = chosen == q["correct"]
    status = "✅" if is_correct else ("❌" if chosen else "⬜ Skipped")

    prev_clicked, next_clicked = render_nav_header(
        "review", idx, total, f"### {status} Review: Q{idx + 1} of {total}", q
    )
    if prev_clicked:
        st.session_state.current_idx -= 1
        st.rerun()
    if next_clicked:
        st.session_state.current_idx += 1
        st.rerun()

    st.markdown(f"<div class='cca-card'><strong>{q['question']}</strong></div>", unsafe_allow_html=True)
    render_choice_rows(q, chosen, tag_chosen_answer=True)

    st.divider()
    with st.expander("📖 Explanation", expanded=True):
        render_explanation_block(q)

    if idx < total - 1:
        st.write("")
        if st.button("Next ▶", type="primary"):
            st.session_state.current_idx += 1
            st.rerun()

# ─── MATERIALS ───────────────────────────────────────────────────────────────

elif st.session_state.mode == "materials":
    st.title("📚 Study Materials")
    st.caption("Reference material for the Claude Certified Architect – Foundations exam")
    st.divider()

    pdfs = sorted(MATERIALS_DIR.glob("*.pdf"))
    pdf_tab_names = [f"📄 {p.stem.replace('-', ' ').replace('_', ' ').title()}" for p in pdfs]
    all_tab_names = pdf_tab_names + ["📋 Exam Blueprint", "🔗 Reference Links", "🤖 Generate Questions"]
    tabs = st.tabs(all_tab_names)

    for tab, pdf_path in zip(tabs, pdfs):
        with tab:
            pdf_viewer(str(pdf_path), width=700, height=800)
            st.download_button(
                label="⬇️ Download PDF",
                data=pdf_path.read_bytes(),
                file_name=pdf_path.name,
                mime="application/pdf",
                key=f"dl_{pdf_path.stem}",
            )

    with tabs[-3]:
        st.markdown(
            """
            ### CCAR-F Exam Blueprint
            *Domains and their approximate weight on the 60-item exam:*
            """
        )
        st.table([
            {"Domain": "1. Agentic Architecture & Orchestration", "Weight": "27%"},
            {"Domain": "2. Tool Design & MCP Integration", "Weight": "18%"},
            {"Domain": "3. Claude Code Configuration & Workflows", "Weight": "20%"},
            {"Domain": "4. Prompt Engineering & Structured Output", "Weight": "20%"},
            {"Domain": "5. Context Management & Reliability", "Weight": "15%"},
        ])
        render_stat_cards([
            ("60", "Items"),
            ("120 min", "Time limit"),
            ("720 / 1000", "Passing scaled score"),
        ])
        st.write("")
        st.markdown("**Exam scenarios** — 4 of the following 6 scenarios appear on any given exam:")
        st.markdown(
            """
            1. **Customer Support Resolution Agent** — Agent SDK, MCP tools, escalation & first-contact resolution
            2. **Code Generation with Claude Code** — slash commands, CLAUDE.md, plan mode vs direct execution
            3. **Multi-Agent Research System** — coordinator/subagent orchestration, source provenance
            4. **Developer Productivity with Claude** — built-in tools, MCP servers, codebase exploration
            5. **Claude Code for Continuous Integration** — CI/CD review, test generation, PR feedback
            6. **Structured Data Extraction** — JSON schemas, validation-retry loops, edge-case handling
            """
        )

    with tabs[-2]:
        ref = load_reference_links()
        for section in ref["sections"]:
            st.subheader(section["title"])
            for item in section["items"]:
                st.markdown(f"**[{item['label']}]({item['url']})**  \n{item['description']}")
            st.write("")

    with tabs[-1]:
        st.subheader("🤖 Generate Questions from a Document")
        st.markdown(
            "Upload any PDF or paste text and your chosen AI provider will generate multiple-choice "
            "questions in the same format as the exam bank. Generated questions are added directly to the pool."
        )
        st.divider()

        provider_key = st.selectbox(
            "AI Provider",
            options=list(PROVIDERS.keys()),
            format_func=lambda k: PROVIDERS[k]["label"],
        )
        provider = PROVIDERS[provider_key]

        col_key, col_model = st.columns([2, 1])
        api_key = col_key.text_input(
            f"{provider['label']} API Key",
            type="password",
            value=os.environ.get(provider["env_var"], ""),
            help=f"{provider['key_help']}. Never stored — only used for this session.",
        )
        model = col_model.text_input("Model", value=provider["default_model"])

        st.write("")
        input_tab_upload, input_tab_text = st.tabs(["📎 Upload PDF", "📝 Paste Text"])
        source_text = ""

        with input_tab_upload:
            uploaded = st.file_uploader("Upload a PDF", type="pdf")
            if uploaded:
                with pdfplumber.open(io.BytesIO(uploaded.read())) as pdf:
                    source_text = "\n\n".join(p.extract_text() for p in pdf.pages if p.extract_text())
                st.success(f"Extracted {len(source_text):,} characters from {uploaded.name}")

        with input_tab_text:
            pasted = st.text_area("Paste document text here", height=200)
            if pasted.strip():
                source_text = pasted.strip()

        num_to_generate = st.slider("Number of questions to generate", min_value=3, max_value=20, value=5)

        st.write("")
        generate_clicked = st.button("✨ Generate Questions", type="primary", disabled=not api_key or not source_text)

        if not api_key:
            st.caption(f"Enter your {provider['label']} API key above to enable generation.")
        elif not source_text:
            st.caption("Upload a PDF or paste text above to enable generation.")

        if generate_clicked and api_key and source_text:
            system_prompt = (
                "You are an expert exam question writer specializing in technical certification exams. "
                "Given a document, generate multiple-choice questions that test deep understanding of the content. "
                "Each question must have exactly 4 choices (A, B, C, D), one correct answer, and a detailed explanation "
                "that explains why the correct answer is right AND why each wrong answer is incorrect. "
                "Respond ONLY with a valid JSON array — no markdown, no commentary."
            )
            user_prompt = (
                f"Generate {num_to_generate} multiple-choice questions from the following document.\n\n"
                "Return a JSON array where each element has this exact structure:\n"
                '{"question": "...", "choices": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
                '"correct": "A", "explanation": "..."}\n\n'
                f"Document:\n{source_text[:GENERATION_MAX_SOURCE_CHARS]}"
            )

            with st.spinner(f"Generating {num_to_generate} questions with {provider['label']}..."):
                try:
                    raw = generate_text(provider_key, api_key, model, system_prompt, user_prompt).strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                    generated = json.loads(raw)
                    existing = load_questions()
                    max_id = max((q["id"] for q in existing), default=0)
                    for i, q in enumerate(generated):
                        q["id"] = max_id + i + 1

                    st.success(f"Generated {len(generated)} questions!")
                    st.divider()
                    st.subheader("Preview")
                    for i, q in enumerate(generated):
                        with st.expander(f"Q{q['id']}: {q['question'][:80]}...", expanded=i == 0):
                            for letter, text in q["choices"].items():
                                prefix = "✅" if letter == q["correct"] else "  "
                                st.markdown(f"{prefix} **{letter}.** {text}")
                            st.markdown(f"**Explanation:** {q['explanation']}")

                    st.divider()
                    if st.button("➕ Add all to question bank", type="primary"):
                        all_qs = existing + generated
                        with open(QUESTIONS_PATH, "w", encoding="utf-8") as f:
                            json.dump(all_qs, f, ensure_ascii=False, indent=2)
                        load_questions.clear()
                        st.success(
                            f"✅ {len(generated)} questions added! Bank now has {len(all_qs)} questions. "
                            "Start a new session from Home to use them."
                        )

                except json.JSONDecodeError:
                    st.error(f"{provider['label']} returned an unexpected format. Try again or reduce the number of questions.")
                except AIProviderError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Error: {e}")
