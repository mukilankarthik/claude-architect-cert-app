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

import csv
import io
import json
import os
import random
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
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

TIMED_EXAM_QUESTION_COUNT = 60  # mirrors the real CCA-F exam: 60 items in 120 minutes
TIMED_EXAM_TIME_LIMIT_MIN = 120

# Official CCA-F domain weights (see the Exam Blueprint tab) — reused to compute
# a weighted score estimate and flag focus areas on the Results page.
DOMAIN_WEIGHTS = {
    "Agentic Architecture & Orchestration": 0.27,
    "Tool Design & MCP Integration": 0.18,
    "Claude Code Configuration & Workflows": 0.20,
    "Prompt Engineering & Structured Output": 0.20,
    "Context Management & Reliability": 0.15,
}
FOCUS_AREA_THRESHOLD_PCT = 70  # domain score below this is called out as a focus area

# The 6 named scenarios from the CCA-F blueprint (4 of 6 appear on any given real exam).
# Individual questions aren't tagged by scenario — instead each scenario carries an
# approximate domain-weight profile (sums to 1.0) reflecting which domains that scenario's
# narrative leans on most, used to draw a stratified practice set (see sample_scenario_pool).
SCENARIOS = [
    {
        "name": "Customer Support Resolution Agent",
        "summary": "Agent SDK, MCP tools, escalation & first-contact resolution",
        "domain_weights": {
            "Agentic Architecture & Orchestration": 0.35,
            "Tool Design & MCP Integration": 0.30,
            "Context Management & Reliability": 0.25,
            "Claude Code Configuration & Workflows": 0.05,
            "Prompt Engineering & Structured Output": 0.05,
        },
    },
    {
        "name": "Code Generation with Claude Code",
        "summary": "Slash commands, CLAUDE.md, plan mode vs direct execution",
        "domain_weights": {
            "Claude Code Configuration & Workflows": 0.55,
            "Agentic Architecture & Orchestration": 0.15,
            "Prompt Engineering & Structured Output": 0.15,
            "Tool Design & MCP Integration": 0.10,
            "Context Management & Reliability": 0.05,
        },
    },
    {
        "name": "Multi-Agent Research System",
        "summary": "Coordinator/subagent orchestration, source provenance",
        "domain_weights": {
            "Agentic Architecture & Orchestration": 0.45,
            "Context Management & Reliability": 0.25,
            "Tool Design & MCP Integration": 0.15,
            "Prompt Engineering & Structured Output": 0.10,
            "Claude Code Configuration & Workflows": 0.05,
        },
    },
    {
        "name": "Developer Productivity with Claude",
        "summary": "Built-in tools, MCP servers, codebase exploration",
        "domain_weights": {
            "Claude Code Configuration & Workflows": 0.40,
            "Tool Design & MCP Integration": 0.30,
            "Agentic Architecture & Orchestration": 0.15,
            "Context Management & Reliability": 0.10,
            "Prompt Engineering & Structured Output": 0.05,
        },
    },
    {
        "name": "Claude Code for Continuous Integration",
        "summary": "CI/CD review, test generation, PR feedback",
        "domain_weights": {
            "Claude Code Configuration & Workflows": 0.50,
            "Prompt Engineering & Structured Output": 0.20,
            "Tool Design & MCP Integration": 0.15,
            "Agentic Architecture & Orchestration": 0.10,
            "Context Management & Reliability": 0.05,
        },
    },
    {
        "name": "Structured Data Extraction",
        "summary": "JSON schemas, validation-retry loops, edge-case handling",
        "domain_weights": {
            "Prompt Engineering & Structured Output": 0.55,
            "Context Management & Reliability": 0.20,
            "Tool Design & MCP Integration": 0.15,
            "Agentic Architecture & Orchestration": 0.05,
            "Claude Code Configuration & Workflows": 0.05,
        },
    },
]
SCENARIO_PRACTICE_QUESTION_COUNT = 20  # a focused set, not a full 60-item mock

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
#
# Light/dark mode is driven entirely from Python (st.session_state.theme),
# not Streamlit's own hidden theme menu or client-side JS — get_theme_css()
# picks one variable set and re-injects the whole stylesheet on every rerun,
# which also lets us reskin native widgets (inputs, buttons, sidebar) instead
# of only our own .cca-* components.

THEME_VARS = {
    "light": {
        "bg": "#f7f9fc",
        "surface": "#ffffff",
        "sidebar-bg": "#ffffff",
        "text": "#0f172a",
        "text-muted": "rgba(15, 23, 42, 0.6)",
        "border": "rgba(15, 23, 42, 0.1)",
        "card-bg": "rgba(15, 23, 42, 0.03)",
        "input-bg": "#ffffff",
        "accent": "#0d9488",
        "accent-2": "#0891b2",
        "accent-soft": "rgba(13, 148, 136, 0.1)",
        "shadow": "rgba(15, 23, 42, 0.08)",
        "correct-bg": "rgba(22, 163, 74, 0.12)",
        "correct-border": "rgba(22, 163, 74, 0.45)",
        "wrong-bg": "rgba(220, 38, 38, 0.1)",
        "wrong-border": "rgba(220, 38, 38, 0.4)",
        "hero-from": "#0f172a",
        "hero-to": "#0d9488",
    },
    "dark": {
        "bg": "#0b1220",
        "surface": "#111827",
        "sidebar-bg": "#0b1220",
        "text": "#e5eaf1",
        "text-muted": "rgba(226, 232, 240, 0.65)",
        "border": "rgba(255, 255, 255, 0.1)",
        "card-bg": "rgba(255, 255, 255, 0.045)",
        "input-bg": "#1e293b",
        "accent": "#2dd4bf",
        "accent-2": "#38bdf8",
        "accent-soft": "rgba(45, 212, 191, 0.16)",
        "shadow": "rgba(0, 0, 0, 0.5)",
        "correct-bg": "rgba(34, 197, 94, 0.16)",
        "correct-border": "rgba(34, 197, 94, 0.5)",
        "wrong-bg": "rgba(248, 113, 113, 0.14)",
        "wrong-border": "rgba(248, 113, 113, 0.45)",
        "hero-from": "#0b1220",
        "hero-to": "#0f766e",
    },
}


CSS_TEMPLATE = """
<style>
:root {
__ROOT_VARS__
    --cca-accent-rgb: __ACCENT_RGB__;
    --cca-card-border: var(--cca-border);
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

/* App-wide reskin: driven entirely by our own --cca-* variables rather than
   Streamlit's own (fixed-light) theme, so the sidebar toggle genuinely
   changes the whole app, not just the .cca-* component classes below. */
.stApp {
    background: var(--cca-bg) !important;
}
.stApp, .stApp p, .stApp li, .stApp span, .stApp label, .stMarkdown, h1, h2, h3, h4, h5, h6 {
    color: var(--cca-text);
}
section[data-testid="stSidebar"] {
    background: var(--cca-sidebar-bg) !important;
    border-right: 1px solid var(--cca-border);
}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {
    background: transparent !important;
}
[data-testid="stHeader"] * { color: var(--cca-text) !important; }
hr { border-color: var(--cca-border) !important; }

div[data-testid="stTextInput"] input,
div[data-testid="stTextArea"] textarea,
div[data-testid="stNumberInput"] input,
div[data-baseweb="select"] > div,
div[data-baseweb="input"] {
    background: var(--cca-input-bg) !important;
    color: var(--cca-text) !important;
    border-color: var(--cca-border) !important;
}
div[data-testid="stTextInput"] input:focus,
div[data-testid="stTextArea"] textarea:focus,
div[data-testid="stNumberInput"] input:focus {
    border-color: var(--cca-accent) !important;
    box-shadow: 0 0 0 1px var(--cca-accent) !important;
}
div[data-testid="stToggle"] label div[data-checked="true"] {
    background-color: var(--cca-accent) !important;
}
div[data-testid="stMetricValue"] { color: var(--cca-text) !important; }
div[data-testid="stMetricLabel"] { color: var(--cca-text-muted) !important; }
div[data-testid="stTabs"] button[aria-selected="true"] {
    color: var(--cca-accent) !important;
    border-color: var(--cca-accent) !important;
}
a { color: var(--cca-accent) !important; }

.cca-hero {
    background: linear-gradient(120deg, var(--cca-hero-from) 0%, var(--cca-hero-to) 55%, var(--cca-hero-from) 100%);
    background-size: 200% 200%;
    animation: ccaGradientShift 12s ease infinite, ccaFadeInUp 0.5s ease both;
    border-radius: 18px;
    padding: 2rem 2.4rem;
    color: #fff !important;
    margin-bottom: 1.2rem;
    box-shadow: 0 10px 30px rgba(var(--cca-accent-rgb), 0.25);
}
.cca-hero h1, .cca-hero p { color: #fff !important; margin: 0; }
.cca-hero p { opacity: 0.92; margin-top: 0.4rem; }
.cca-hero .cca-hero-meta {
    display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.9rem;
}
.cca-hero .cca-hero-pill {
    background: rgba(255, 255, 255, 0.16);
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 0.82rem;
    font-weight: 600;
    backdrop-filter: blur(6px);
}

.cca-card {
    background: var(--cca-card-bg);
    border: 1px solid var(--cca-card-border);
    border-radius: 16px;
    padding: 1.4rem 1.6rem;
    margin: 0.6rem 0 1.3rem 0;
    font-size: 1.08rem;
    line-height: 1.65;
    box-shadow: 0 2px 10px var(--cca-shadow);
    animation: ccaFadeInUp 0.35s ease both;
    transition: border-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
}
.cca-card:hover {
    border-color: var(--cca-accent);
    transform: translateY(-2px);
    box-shadow: 0 8px 20px var(--cca-shadow);
}

/* Equal-height cards across a row of st.columns(): Streamlit already stretches each
   stColumn to the tallest column's height, but a card's own box only sizes to its
   text, leaving the button below it at a different vertical position per column.
   Force the flex chain from the card's element-container down to the card itself
   to grow and fill that available height, so every card box (and the button under
   it) lines up regardless of how much text it holds. */
div[data-testid="stElementContainer"]:has(> div[data-testid="stMarkdown"] .cca-card) {
    flex: 1;
}
div[data-testid="stElementContainer"]:has(> div[data-testid="stMarkdown"] .cca-card) > div[data-testid="stMarkdown"],
div[data-testid="stElementContainer"]:has(> div[data-testid="stMarkdown"] .cca-card) > div[data-testid="stMarkdown"] > div,
div[data-testid="stElementContainer"]:has(> div[data-testid="stMarkdown"] .cca-card) [data-testid="stMarkdownContainer"] {
    height: 100%;
}
.cca-card { height: 100%; width: 100%; box-sizing: border-box; }

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
.cca-choice-picked {
    background: var(--cca-accent-soft) !important;
    border: 1px solid var(--cca-accent) !important;
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
.cca-badge-flag {
    background: var(--cca-wrong-bg);
    border-color: var(--cca-wrong-border);
    color: inherit;
}

.cca-scenario-pane {
    background: var(--cca-card-bg);
    border: 1px solid var(--cca-card-border);
    border-radius: 16px;
    padding: 1.4rem 1.6rem;
    box-shadow: 0 2px 10px var(--cca-shadow);
    max-height: 640px;
    overflow-y: auto;
    line-height: 1.7;
    font-size: 1.02rem;
}
.cca-scenario-pane h4 {
    margin-top: 0;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
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
    background: var(--cca-surface) !important;
    color: var(--cca-text) !important;
    border: 1px solid var(--cca-border) !important;
    transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease !important;
}
.stButton > button[kind="primary"] {
    background: var(--cca-accent) !important;
    color: #fff !important;
    border: 1px solid var(--cca-accent) !important;
}
.stButton > button:disabled {
    opacity: 0.45;
}
.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 14px rgba(var(--cca-accent-rgb), 0.18);
    border-color: var(--cca-accent) !important;
    color: var(--cca-accent) !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 6px 16px rgba(var(--cca-accent-rgb), 0.35);
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
    background: var(--cca-card-bg);
    overflow: hidden;
}

section[data-testid="stSidebar"] button {
    white-space: normal;
    padding-left: 0.25rem;
    padding-right: 0.25rem;
    min-width: 0;
    height: 3.2rem !important;
    font-size: 0.82rem;
    line-height: 1.2;
    word-break: break-word;
    display: flex !important;
    align-items: center;
    justify-content: center;
}

/* Theme toggle: a compact icon button pinned at the top of the sidebar */
.cca-theme-toggle button {
    height: 2.4rem !important;
    font-size: 1.1rem !important;
}

/* Streak pill on Home — a small nudge to keep the daily habit going */
.cca-streak-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.85rem;
    font-weight: 600;
    padding: 5px 12px;
    border-radius: 999px;
    background: var(--cca-accent-soft);
    color: var(--cca-accent);
    border: 1px solid var(--cca-accent);
}
</style>
"""


def render_theme_css() -> None:
    v = THEME_VARS[st.session_state.theme]
    root_vars = "\n".join(f"    --cca-{k}: {val};" for k, val in v.items())
    accent_rgb = "13, 148, 136" if st.session_state.theme == "light" else "45, 212, 191"
    css = CSS_TEMPLATE.replace("__ROOT_VARS__", root_vars).replace("__ACCENT_RGB__", accent_rgb)
    st.markdown(css, unsafe_allow_html=True)

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


@st.cache_data
def load_cheat_sheets() -> dict:
    with open(MATERIALS_DIR / "cheat_sheets.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_claude_code_reference() -> dict:
    with open(MATERIALS_DIR / "claude_code_reference.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_mental_model() -> dict:
    with open(MATERIALS_DIR / "mental_model.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_scenario_walkthroughs() -> dict:
    with open(MATERIALS_DIR / "scenario_walkthroughs.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_glossary() -> dict:
    with open(MATERIALS_DIR / "glossary.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_cram_sheet() -> dict:
    with open(MATERIALS_DIR / "cram_sheet.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_capstone() -> dict:
    with open(MATERIALS_DIR / "capstone.json", encoding="utf-8") as f:
        return json.load(f)


def get_materials_groups() -> list[dict]:
    """Materials sections grouped for the browsable gallery landing page (see the
    "materials" mode below). Grouping puts the highest-value, quickest-to-scan
    content (Quick Reference) first — full PDFs are longer reads and go second."""
    pdfs = sorted(MATERIALS_DIR.glob("*.pdf"))
    pdf_items = [
        {
            "label": f"📄 {p.stem.replace('-', ' ').replace('_', ' ').title()}",
            "icon": "📄",
            "title": p.stem.replace("-", " ").replace("_", " ").title(),
            "description": "Full source PDF — viewable inline or downloadable.",
        }
        for p in pdfs
    ]
    return [
        {
            "title": "🧭 Quick Reference",
            "description": "Condensed, exam-ready references — start here if you only have a few minutes.",
            "items": [
                {
                    "label": "🗺️ Cram Sheet", "icon": "🗺️", "title": "Cram Sheet",
                    "description": "The one-pager for right before the exam — core model, per-domain must-knows, and the traps.",
                },
                {
                    "label": "📇 Cheat Sheets", "icon": "📇", "title": "Cheat Sheets",
                    "description": "Condensed key facts per domain — a fast pass before the exam.",
                },
                {
                    "label": "🧭 Mental Model", "icon": "🧭", "title": "Mental Model",
                    "description": "The Five Actors and the 8-step agentic loop underlying almost every scenario question.",
                },
                {
                    "label": "📖 Glossary", "icon": "📖", "title": "Glossary",
                    "description": "40+ exam terms defined precisely, grouped by domain.",
                },
                {
                    "label": "⌨️ Claude Code Reference", "icon": "⌨️", "title": "Claude Code Reference",
                    "description": "Slash commands, keyboard shortcuts, config files, hooks, and CLAUDE.md conventions.",
                },
                {
                    "label": "📋 Exam Blueprint", "icon": "📋", "title": "Exam Blueprint",
                    "description": "Domain weights, scoring details, and the 6 named exam scenarios.",
                },
                {
                    "label": "🔗 Reference Links", "icon": "🔗", "title": "Reference Links",
                    "description": "Curated external docs and guides worth reading alongside this app.",
                },
            ],
        },
        {
            "title": "🧩 Worked Walkthroughs",
            "description": "Each of the 6 named exam scenarios traced step by step through the agentic loop.",
            "items": [
                {
                    "label": "🧩 Scenario Walkthroughs", "icon": "🧩", "title": "Scenario Walkthroughs",
                    "description": "A worked trace, key lesson, and common trap for each of the 6 exam scenarios.",
                },
                {
                    "label": "🎓 Capstone Project", "icon": "🎓", "title": "Capstone Project",
                    "description": "Build a real helpdesk agent touching all 5 domains — the highest-leverage prep beyond the question bank.",
                },
            ],
        },
        {
            "title": "📄 Exam Guides",
            "description": "Full source PDFs the question bank and cheat sheets are drawn from.",
            "items": pdf_items,
        },
        {
            "title": "🤖 Tools",
            "description": "Expand the question bank from your own material.",
            "items": [
                {
                    "label": "🤖 Generate Questions", "icon": "🤖", "title": "Generate Questions",
                    "description": "Upload a PDF or paste text to generate new practice questions with your own AI provider key.",
                },
            ],
        },
    ]


ALL_QUESTIONS = load_questions()

# ─── Scenario practice ──────────────────────────────────────────────────────


def combine_scenario_domain_weights(scenario_names: list[str]) -> dict[str, float]:
    """Average the domain-weight profiles of the given scenarios into one normalized
    profile (renormalized defensively against float drift; degenerate/empty input
    yields all-zero weights)."""
    chosen = [s for s in SCENARIOS if s["name"] in scenario_names]
    combined = {d: 0.0 for d in DOMAIN_WEIGHTS}
    for s in chosen:
        for d, w in s["domain_weights"].items():
            combined[d] += w
    total = sum(combined.values())
    return {d: w / total for d, w in combined.items()} if total else combined


def sample_scenario_pool(scenario_names: list[str], count: int) -> list[dict]:
    """Stratified sample of `count` questions drawn from each domain in proportion to
    the combined domain-weight profile of the given scenarios. Target counts per domain
    are rounded via largest-remainder so they sum to exactly `count`; any shortfall (a
    domain running out of unique questions) is topped up from the rest of the bank."""
    weights = combine_scenario_domain_weights(scenario_names)
    by_domain: dict[str | None, list[dict]] = {}
    for q in ALL_QUESTIONS:
        by_domain.setdefault(q.get("domain"), []).append(q)
    for qs in by_domain.values():
        random.shuffle(qs)

    raw = {d: weights.get(d, 0) * count for d in DOMAIN_WEIGHTS}
    target = {d: int(raw[d]) for d in raw}
    remainder = count - sum(target.values())
    for d in sorted(raw, key=lambda d: raw[d] - target[d], reverse=True)[:remainder]:
        target[d] += 1

    pool, used_ids = [], set()
    for d, n in target.items():
        chosen = by_domain.get(d, [])[:n]
        pool.extend(chosen)
        used_ids.update(q["id"] for q in chosen)

    if len(pool) < count:
        leftover = [q for q in ALL_QUESTIONS if q["id"] not in used_ids]
        random.shuffle(leftover)
        pool.extend(leftover[: count - len(pool)])

    random.shuffle(pool)
    return pool[:count]


# ─── Checkpoint & session log helpers ───────────────────────────────────────


def get_unused_questions() -> tuple[list[dict], int]:
    """Questions not yet answered in a prior session, plus how many have been used."""
    checkpoint = STORAGE.load_checkpoint()
    used = set(checkpoint["used_ids"])
    unused = [q for q in ALL_QUESTIONS if q["id"] not in used]
    return unused, len(used)


def build_and_store_session_log(
    session_id: str, answers: dict, questions: list, cohort: str, learner_id: str = "", exam_type: str = "learning"
) -> None:
    log = {
        "session_id": session_id,
        "cohort": cohort,
        "learner_id": learner_id or None,  # self-declared, unverified — see the Learner ID field's help text
        "exam_type": exam_type,
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


# Leitner-style review intervals in days, indexed by a question's current correct
# streak (capped at the last box): a fresh miss is box 0 (due immediately), and each
# additional correct-in-a-row answer pushes the next review further out.
DRILL_BOX_INTERVALS_DAYS = [0, 1, 3, 7, 14, 30]


def compute_learner_srs_state(learner_id: str, logs: list[dict]) -> dict[int, dict]:
    """Per-question review state for a learner, derived entirely from their session
    logs (no separate persisted state): the correct streak ending at their most recent
    attempt, and when that attempt happened. Skipped answers don't count as an attempt."""
    if not learner_id:
        return {}
    attempts_by_id: dict[int, list[tuple[str, str]]] = {}
    for log in sorted((l for l in logs if l.get("learner_id") == learner_id), key=lambda l: l.get("date", "")):
        for entry in log.get("questions", []):
            if entry["result"] == "skipped":
                continue
            attempts_by_id.setdefault(entry["id"], []).append((log.get("date", ""), entry["result"]))

    state = {}
    for qid, attempts in attempts_by_id.items():
        streak = 0
        for _, result in reversed(attempts):
            if result != "correct":
                break
            streak += 1
        state[qid] = {"streak": streak, "last_seen": attempts[-1][0], "attempts": len(attempts)}
    return state


def compute_drill_queue(learner_id: str, logs: list[dict], now: datetime | None = None) -> list[dict]:
    """Spaced-repetition drill queue: every question this learner has attempted that's
    currently due for review, ranked most-overdue first. A miss always resets a question
    to box 0 (due immediately); each correct-in-a-row answer advances it to a longer
    interval (see DRILL_BOX_INTERVALS_DAYS), so mastered questions stop resurfacing every
    session but still get a decay check once their interval elapses."""
    if not learner_id:
        return []
    now = now or datetime.now()
    state = compute_learner_srs_state(learner_id, logs)

    overdue_by_id = {}
    for qid, s in state.items():
        box = min(s["streak"], len(DRILL_BOX_INTERVALS_DAYS) - 1)
        try:
            last_seen = datetime.strptime(s["last_seen"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_seen = now
        due_date = last_seen + timedelta(days=DRILL_BOX_INTERVALS_DAYS[box])
        overdue_days = (now - due_date).total_seconds() / 86400
        if overdue_days >= 0:
            overdue_by_id[qid] = overdue_days

    id_to_question = {q["id"]: q for q in ALL_QUESTIONS}
    due_ids = sorted(overdue_by_id, key=lambda qid: -overdue_by_id[qid])
    return [id_to_question[qid] for qid in due_ids if qid in id_to_question]


def aggregate_domain_stats_from_logs(logs: list[dict]) -> dict:
    """Correct/total per domain across the given session logs. Pass every log for a
    cohort-wide view, or pre-filter to one learner_id for a personal view."""
    id_to_domain = {q["id"]: q.get("domain") for q in ALL_QUESTIONS}
    stats = {}
    for log in logs:
        for entry in log.get("questions", []):
            if entry["result"] == "skipped":
                continue
            dom = id_to_domain.get(entry["id"])
            if not dom:
                continue
            s = stats.setdefault(dom, {"correct": 0, "total": 0})
            s["total"] += 1
            if entry["result"] == "correct":
                s["correct"] += 1
    return stats


def summarize_logs(logs: list[dict]) -> dict:
    """Session count, total answered, total correct, and overall accuracy for a set
    of session logs — the same headline numbers shown as stat cards on the Progress
    tab, factored out so both the cohort-wide and personal report sections can share it."""
    answered = sum(log.get("total_answered", 0) for log in logs)
    correct = sum(log.get("correct", 0) for log in logs)
    return {
        "sessions": len(logs),
        "answered": answered,
        "correct": correct,
        "accuracy_pct": int(correct / answered * 100) if answered else 0,
    }


def compute_study_streak(logs: list[dict], learner_id: str = "", now: datetime | None = None) -> int:
    """Consecutive-day study streak ending today or yesterday (a session done
    yesterday but not yet today doesn't break the streak until a full day is
    missed) — a lightweight nudge to keep learners coming back daily. Filters
    to a learner's own logs when a learner_id is given, otherwise cohort-wide."""
    now = now or datetime.now()
    relevant = [l for l in logs if not learner_id or l.get("learner_id") == learner_id]
    days = set()
    for log in relevant:
        try:
            days.add(datetime.strptime(log["date"][:10], "%Y-%m-%d").date())
        except (KeyError, ValueError):
            continue
    if not days:
        return 0

    today = now.date()
    cursor = today if today in days else today - timedelta(days=1)
    if cursor not in days:
        return 0
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def focus_areas_from_domain_stats(domain_stats: dict) -> list[str]:
    return [
        dom for dom, s in domain_stats.items()
        if s["total"] and (s["correct"] / s["total"]) * 100 < FOCUS_AREA_THRESHOLD_PCT
    ]


def build_progress_report(all_logs: list[dict], learner_id: str = "") -> dict:
    """Everything the study report needs, computed once and shared by both the
    Markdown and printable-HTML renderers: cohort-wide stats always, plus a personal
    section (domain stats, focus areas, spaced-repetition drill backlog) when a
    learner_id is given and has at least one recorded session."""
    cohort_stats = summarize_logs(all_logs)
    cohort_domain_stats = aggregate_domain_stats_from_logs(all_logs)
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cohort": {
            **cohort_stats,
            "domain_stats": cohort_domain_stats,
            "focus_areas": focus_areas_from_domain_stats(cohort_domain_stats),
        },
        "personal": None,
    }
    if not learner_id:
        return report

    personal_logs = [l for l in all_logs if l.get("learner_id") == learner_id]
    if not personal_logs:
        return report

    personal_domain_stats = aggregate_domain_stats_from_logs(personal_logs)
    srs_state = compute_learner_srs_state(learner_id, all_logs)
    drill_due = compute_drill_queue(learner_id, all_logs)
    report["personal"] = {
        **summarize_logs(personal_logs),
        "learner_id": learner_id,
        "domain_stats": personal_domain_stats,
        "focus_areas": focus_areas_from_domain_stats(personal_domain_stats),
        "drill_due_count": len(drill_due),
        "drill_tracked_count": len(srs_state),
    }
    return report


def render_progress_report_markdown(report: dict) -> str:
    def domain_table(domain_stats: dict) -> str:
        if not domain_stats:
            return "_No domain-tagged questions answered yet._\n"
        lines = ["| Domain | Exam Weight | Correct | Score |", "| --- | --- | --- | --- |"]
        for dom, s in domain_stats.items():
            weight = f"{int(DOMAIN_WEIGHTS[dom] * 100)}%" if dom in DOMAIN_WEIGHTS else "—"
            score = f"{int(s['correct'] / s['total'] * 100)}%" if s["total"] else "—"
            lines.append(f"| {dom} | {weight} | {s['correct']}/{s['total']} | {score} |")
        return "\n".join(lines)

    parts = [
        "# CCA-F Study Report",
        f"_Generated {report['generated_at']}_",
        "",
        "## Cohort-wide (all sessions on this deployment)",
        f"- Sessions recorded: {report['cohort']['sessions']}",
        f"- Questions answered: {report['cohort']['answered']}",
        f"- Overall accuracy: {report['cohort']['accuracy_pct']}%",
        "",
        domain_table(report["cohort"]["domain_stats"]),
    ]
    if report["cohort"]["focus_areas"]:
        parts.append("\n🎯 **Focus areas (below " + f"{FOCUS_AREA_THRESHOLD_PCT}%" + "):** " + ", ".join(report["cohort"]["focus_areas"]))

    personal = report["personal"]
    if personal:
        parts += [
            "",
            f"## Personal (Learner ID: {personal['learner_id']})",
            f"- Sessions recorded: {personal['sessions']}",
            f"- Questions answered: {personal['answered']}",
            f"- Personal accuracy: {personal['accuracy_pct']}%",
            f"- Drill backlog: {personal['drill_due_count']} due now, "
            f"{personal['drill_tracked_count']} tracked overall",
            "",
            domain_table(personal["domain_stats"]),
        ]
        if personal["focus_areas"]:
            parts.append("\n🎯 **Personal focus areas (below " + f"{FOCUS_AREA_THRESHOLD_PCT}%" + "):** " + ", ".join(personal["focus_areas"]))

    return "\n".join(parts) + "\n"


def render_progress_report_html(report: dict) -> str:
    def domain_rows(domain_stats: dict) -> str:
        if not domain_stats:
            return "<tr><td colspan='4'><em>No domain-tagged questions answered yet.</em></td></tr>"
        rows = []
        for dom, s in domain_stats.items():
            weight = f"{int(DOMAIN_WEIGHTS[dom] * 100)}%" if dom in DOMAIN_WEIGHTS else "—"
            score = f"{int(s['correct'] / s['total'] * 100)}%" if s["total"] else "—"
            rows.append(f"<tr><td>{dom}</td><td>{weight}</td><td>{s['correct']}/{s['total']}</td><td>{score}</td></tr>")
        return "\n".join(rows)

    def section(title: str, stats: dict, domain_stats: dict, focus_areas: list[str], extra: str = "") -> str:
        focus_html = (
            f"<p class='focus'>🎯 Focus areas (below {FOCUS_AREA_THRESHOLD_PCT}%): {', '.join(focus_areas)}</p>"
            if focus_areas else ""
        )
        return f"""
        <h2>{title}</h2>
        <div class="stats">
          <div><strong>{stats['sessions']}</strong><span>Sessions</span></div>
          <div><strong>{stats['answered']}</strong><span>Questions Answered</span></div>
          <div><strong>{stats['accuracy_pct']}%</strong><span>Accuracy</span></div>
        </div>
        {extra}
        <table>
          <thead><tr><th>Domain</th><th>Exam Weight</th><th>Correct</th><th>Score</th></tr></thead>
          <tbody>{domain_rows(domain_stats)}</tbody>
        </table>
        {focus_html}
        """

    personal = report["personal"]
    personal_html = ""
    if personal:
        extra = (
            f"<p class='drill'>Drill backlog: <strong>{personal['drill_due_count']}</strong> due now, "
            f"{personal['drill_tracked_count']} tracked overall.</p>"
        )
        personal_html = section(
            f"Personal — Learner ID: {personal['learner_id']}",
            personal, personal["domain_stats"], personal["focus_areas"], extra,
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>CCA-F Study Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #1e293b; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
  h1 {{ margin-bottom: 0; }}
  .subtitle {{ color: #64748b; margin-top: 0.2rem; }}
  h2 {{ border-bottom: 2px solid #0d9488; padding-bottom: 0.3rem; margin-top: 2.2rem; }}
  .stats {{ display: flex; gap: 1.5rem; margin: 1rem 0; }}
  .stats div {{ flex: 1; text-align: center; border: 1px solid #cbd5e1; border-radius: 8px; padding: 0.8rem; }}
  .stats strong {{ display: block; font-size: 1.5rem; }}
  .stats span {{ font-size: 0.8rem; color: #64748b; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 0.45rem 0.6rem; text-align: left; font-size: 0.92rem; }}
  th {{ background: #f1f5f9; }}
  .focus {{ background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; padding: 0.6rem 0.8rem; margin-top: 0.8rem; }}
  .drill {{ color: #334155; }}
  @media print {{ body {{ margin: 0.5rem auto; }} }}
</style>
</head><body>
  <h1>🏛️ CCA-F Study Report</h1>
  <p class="subtitle">Generated {report['generated_at']} · use your browser's Print (Ctrl/Cmd+P) to save as PDF</p>
  {section("Cohort-wide (all sessions on this deployment)", report["cohort"], report["cohort"]["domain_stats"], report["cohort"]["focus_areas"])}
  {personal_html}
</body></html>
"""


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
        "learner_id": "",          # self-declared, unverified — keys personal Drill Mode history
        "session_id": None,
        "session_finished": False,
        "git_push_status": None,
        "exam_type": "learning",   # "learning", "timed", "drill", or "scenario"
        "exam_deadline": None,     # epoch seconds when a timed exam auto-submits
        "time_expired": False,
        "scenario_random_draw": random.sample([s["name"] for s in SCENARIOS], k=4),
        "flagged": set(),          # idx -> flagged for review, timed mock exam only
        "scratchpad": {},          # idx -> scratch-pad text, timed mock exam only
        "materials_view": "gallery",   # "gallery" (browsable landing page) or "detail" (one section)
        "theme": "dark",   # "light" or "dark" — toggled from the sidebar, see render_theme_css()
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()
render_theme_css()

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
        prev_clicked = st.button("◀ Prev", disabled=idx == 0, width='stretch', key=f"{key_prefix}_prev")
    with col_next:
        next_clicked = st.button("Next ▶", disabled=idx == total - 1, width='stretch', key=f"{key_prefix}_next")
    return prev_clicked, next_clicked


def render_choice_rows(
    q: dict, chosen: str | None, tag_chosen_answer: bool = False, reveal_correct: bool = True
) -> None:
    """Render every answer choice as a pill. When reveal_correct is True, highlights
    the correct one and (if wrong) the user's own pick — used in Learning Mode and
    Review, where feedback is immediate. Timed mock exams pass reveal_correct=False
    so choices only show which one you picked, not whether it was right, matching a
    real proctored exam where you don't get graded mid-test."""
    for letter, text in q["choices"].items():
        if reveal_correct and letter == q["correct"]:
            st.markdown(
                f"<div class='cca-choice cca-choice-correct'>✅ <strong>{letter}.</strong> {text}</div>",
                unsafe_allow_html=True,
            )
        elif letter == chosen:
            if reveal_correct:
                suffix = " <em>(your answer)</em>" if tag_chosen_answer else ""
                st.markdown(
                    f"<div class='cca-choice cca-choice-wrong'>❌ <strong>{letter}.</strong> {text}{suffix}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div class='cca-choice cca-choice-picked'>☑️ <strong>{letter}.</strong> {text} "
                    "<em>(your answer)</em></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f"<div class='cca-choice'>&nbsp;&nbsp;&nbsp;<strong>{letter}.</strong> {text}</div>",
                unsafe_allow_html=True,
            )


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+(?=[A-Z(])")


def split_scenario_and_question(question_text: str) -> tuple[str, str]:
    """Question bank entries are one flat string: a scenario narrative followed by
    the actual ask, usually ending in the final '?'. Splits off that last sentence
    as "the question" so a mock-exam layout can show the narrative in a separate
    scenario pane, matching the real proctored exam's two-pane screen. Falls back to
    (empty scenario, whole text) when there's only one sentence to split — e.g. a
    short, self-contained question with no narrative setup."""
    sentences = SENTENCE_SPLIT_RE.split(question_text.strip())
    if len(sentences) < 2:
        return "", question_text.strip()
    return " ".join(sentences[:-1]).strip(), sentences[-1].strip()


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


def render_countdown_clock(deadline_epoch: float, total_seconds: int) -> None:
    """A centered, self-ticking circular countdown clock for timed exams.
    Ticks client-side every second via JS (no Streamlit rerun needed to stay
    visually live); the actual time-limit enforcement happens server-side in
    the exam view, which checks the same deadline on every rerun."""
    deadline_ms = int(deadline_epoch * 1000)
    total_ms = int(total_seconds * 1000)
    st.iframe(
        f"""
        <div style="display:flex; justify-content:center; align-items:center; font-family:inherit;">
          <div id="cca-clock-ring" style="width:130px; height:130px; border-radius:50%;
               display:flex; align-items:center; justify-content:center;
               background:conic-gradient(#0d9488 0%, rgba(127,127,127,0.18) 0);
               transition: background 0.3s ease;">
            <div style="width:104px; height:104px; border-radius:50%;
                 background:var(--cca-clock-bg, #f8fafc);
                 display:flex; flex-direction:column; align-items:center; justify-content:center;">
              <div id="cca-clock-time" style="font-size:1.55rem; font-weight:700; color:var(--cca-clock-fg, #1e293b);
                   font-variant-numeric: tabular-nums;">--:--</div>
              <div style="font-size:0.65rem; letter-spacing:0.04em; text-transform:uppercase;
                   opacity:0.6; color:var(--cca-clock-fg, #1e293b);">remaining</div>
            </div>
          </div>
        </div>
        <style>
          @media (prefers-color-scheme: dark) {{
            :root {{ --cca-clock-bg: #1e293b; --cca-clock-fg: #f1f5f9; }}
          }}
        </style>
        <script>
          const deadline = {deadline_ms};
          const totalMs = {total_ms};
          function tick() {{
            const remaining = Math.max(0, deadline - Date.now());
            const mins = Math.floor(remaining / 60000);
            const secs = Math.floor((remaining % 60000) / 1000);
            const pct = Math.max(0, Math.min(100, (remaining / totalMs) * 100));
            const urgent = remaining < totalMs * 0.1;
            const timeEl = document.getElementById('cca-clock-time');
            const ringEl = document.getElementById('cca-clock-ring');
            if (timeEl) {{
              timeEl.textContent = String(mins).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
              timeEl.style.color = urgent ? '#dc2626' : '';
            }}
            if (ringEl) {{
              const color = urgent ? '#dc2626' : '#0d9488';
              ringEl.style.background = `conic-gradient(${{color}} ${{pct}}%, rgba(127,127,127,0.18) 0)`;
            }}
            if (remaining <= 0) clearInterval(timer);
          }}
          tick();
          const timer = setInterval(tick, 1000);
        </script>
        """,
        height=150,
    )


def render_stat_cards(items: list[tuple]) -> None:
    """items: list of (value, label) pairs, rendered as equal-width stat cards."""
    for col, (value, label) in zip(st.columns(len(items)), items):
        col.markdown(
            f"<div class='cca-stat-card'><div class='cca-stat-value'>{value}</div>"
            f"<div class='cca-stat-label'>{label}</div></div>",
            unsafe_allow_html=True,
        )


def render_score_trend_chart(trend_rows: list[dict]) -> None:
    """Score-by-session line chart, built with Altair (not st.line_chart) so its
    background/axes/gridlines follow our own theme toggle — st.line_chart bakes
    in Streamlit's fixed config.toml theme (always light) regardless of our
    runtime dark/light state, which left a jarring white box in dark mode."""
    v = THEME_VARS[st.session_state.theme]
    df = pd.DataFrame(trend_rows).reset_index().rename(columns={"index": "Session"})
    df["Session"] = df["Session"] + 1
    chart = (
        alt.Chart(df)
        .mark_line(point=True, color=v["accent"])
        .encode(
            x=alt.X("Session:O", title="Session #"),
            y=alt.Y("Score %:Q", title="Score %", scale=alt.Scale(domain=[0, 100])),
            tooltip=["Session", "Date", "Cohort", "Score %"],
        )
        .properties(height=280, background="transparent")
        .configure_axis(
            labelColor=v["text-muted"], titleColor=v["text-muted"], gridColor=v["border"], domainColor=v["border"],
        )
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)

# ─── Exam flow helpers ──────────────────────────────────────────────────────


def start_exam(exam_type: str = "learning", external_pool: list[dict] | None = None) -> None:
    """exam_type "learning": untimed, draws from not-yet-covered questions, tracked
    in the checkpoint. "timed": a fixed-size, fixed-duration mock exam that mirrors
    the real CCA-F exam — always a fresh random draw from the full bank, and never
    marks questions as covered so it doesn't interfere with Learning Mode's pool.
    "drill": untimed, replays a specific learner's questions currently due for
    spaced-repetition review (see compute_drill_queue). "scenario": untimed, a
    domain-weighted practice set for one or more named exam scenarios (see
    sample_scenario_pool). Both "drill" and "scenario" take their question set via
    `external_pool` and never touch the shared checkpoint."""
    if exam_type == "timed":
        count = min(TIMED_EXAM_QUESTION_COUNT, len(ALL_QUESTIONS))
        pool = random.sample(ALL_QUESTIONS, count)
        st.session_state.exam_deadline = time.time() + TIMED_EXAM_TIME_LIMIT_MIN * 60
    elif exam_type in ("drill", "scenario"):
        pool = list(external_pool or [])
        if st.session_state.shuffle:
            random.shuffle(pool)
        st.session_state.exam_deadline = None
    else:
        unused, _ = get_unused_questions()
        pool = unused if unused else ALL_QUESTIONS.copy()
        if st.session_state.shuffle:
            random.shuffle(pool)
        st.session_state.exam_deadline = None

    st.session_state.exam_type = exam_type
    st.session_state.questions = pool
    st.session_state.current_idx = 0
    st.session_state.answers = {}
    st.session_state.flagged = set()
    st.session_state.scratchpad = {}
    st.session_state.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.session_state.session_finished = False
    st.session_state.time_expired = False
    st.session_state.mode = "exam"


def finish_session() -> None:
    """Persist checkpoint + session log, then move to the results screen. Timed
    mock exams and Drill Mode sessions still get a session-log entry (useful history),
    but never call save_checkpoint_entry, so they don't affect Learning Mode's
    covered-questions pool."""
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
        "exam_type": st.session_state.exam_type,
    })
    build_and_store_session_log(
        st.session_state.session_id, answers, questions, st.session_state.cohort_name,
        learner_id=st.session_state.learner_id, exam_type=st.session_state.exam_type,
    )
    st.session_state.git_push_status = None
    st.session_state.session_finished = True
    st.session_state.mode = "results"

# ─── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    col_title, col_theme = st.columns([5, 1])
    with col_title:
        st.title("🏛️ CCA-F Study Guide")
        st.caption("Claude Certified Architect – Foundations")
    with col_theme:
        st.markdown("<div class='cca-theme-toggle'>", unsafe_allow_html=True)
        theme_icon = "☀️" if st.session_state.theme == "dark" else "🌙"
        if st.button(theme_icon, key="theme_toggle", help="Switch light / dark mode", width='stretch'):
            st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    st.divider()

    if st.session_state.mode != "exam":
        col_home, col_mat, col_progress = st.columns(3)
        if col_home.button("🏠 Home", width='stretch', disabled=st.session_state.mode == "home"):
            st.session_state.mode = "home"
            st.rerun()
        if col_mat.button("📚 Materials", width='stretch', disabled=st.session_state.mode == "materials"):
            st.session_state.mode = "materials"
            st.session_state.materials_view = "gallery"
            st.rerun()
        if col_progress.button("📈 Progress", width='stretch', disabled=st.session_state.mode == "progress"):
            st.session_state.mode = "progress"
            st.rerun()
        st.divider()

    if st.session_state.mode == "home":
        st.subheader("⚙️ Session Settings")
        st.session_state.cohort_name = st.text_input("Cohort / Team Name", value=st.session_state.cohort_name)
        st.caption("Studying solo? Just put your own name here — it's only used to label your session in the shared log.")
        st.session_state.learner_id = st.text_input(
            "Learner ID (for Drill Mode)", value=st.session_state.learner_id,
            help="Not a login — just a personal label. Type the same ID every visit to build up your own "
            "missed-question history for Drill Mode. Leave blank if you don't want personal tracking.",
        )
        st.session_state.shuffle = st.toggle(
            "Shuffle questions", value=st.session_state.shuffle, key="toggle_shuffle"
        )
        st.session_state.show_explanation = st.toggle(
            "Show explanation after each answer", value=st.session_state.show_explanation,
            key="toggle_show_explanation",
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
            if st.button("🔄 Reset Checkpoint", width='stretch'):
                STORAGE.reset_checkpoint()
                st.success("Checkpoint cleared.")
                st.rerun()
        else:
            st.caption("No sessions recorded yet.")
        st.divider()
        st.caption(f"Storage backend: **{STORAGE.backend_name}**")
        st.info(f"📚 {len(ALL_QUESTIONS)} total · {len(unused)} remaining")

    elif st.session_state.mode == "materials":
        st.subheader("📚 Jump to a section")
        materials_section_options = [
            item["label"] for group in get_materials_groups() for item in group["items"]
        ]
        # Keep the widget's own state in sync with materials_section changes made
        # elsewhere (e.g. a gallery card click) — only legal to assign this *before*
        # the keyed widget below is instantiated for this run, not after.
        current_section = st.session_state.get("materials_section")
        if current_section in materials_section_options and (
            st.session_state.get("materials_section_widget") != current_section
        ):
            st.session_state.materials_section_widget = current_section

        def _jump_to_materials_section():
            st.session_state.materials_section = st.session_state.materials_section_widget
            st.session_state.materials_view = "detail"

        st.selectbox(
            "Choose what to view", options=materials_section_options,
            key="materials_section_widget", label_visibility="collapsed",
            on_change=_jump_to_materials_section,
        )
        if st.session_state.materials_view == "detail":
            if st.button("⬅ Back to overview", width='stretch'):
                st.session_state.materials_view = "gallery"
                st.rerun()

    elif st.session_state.mode == "exam":
        questions = st.session_state.questions
        is_timed = st.session_state.exam_type == "timed"
        answered = len(st.session_state.answers)
        total = len(questions)
        st.metric("Answered", f"{answered}/{total}")
        st.progress(answered / total if total else 0)
        st.divider()

        if is_timed and st.session_state.flagged:
            st.caption(f"🚩 {len(st.session_state.flagged)} flagged for review")

        st.subheader("Navigator")
        cols = st.columns(4)
        for i in range(total):
            col = cols[i % 4]
            flagged = is_timed and i in st.session_state.flagged
            if i in st.session_state.answers:
                if is_timed:
                    label = "●🚩" if flagged else "●"  # answered, but no correctness reveal mid-exam
                else:
                    label = "✅" if st.session_state.answers[i] == questions[i]["correct"] else "❌"
            elif flagged:
                label = f"{i + 1}🚩"
            elif i == st.session_state.current_idx:
                label = f"**{i + 1}**"
            else:
                label = str(i + 1)
            if col.button(label, key=f"nav_{i}", width='stretch'):
                st.session_state.current_idx = i
                st.rerun()

        if is_timed and st.session_state.flagged:
            with st.expander(f"🚩 Review flagged ({len(st.session_state.flagged)})"):
                for i in sorted(st.session_state.flagged):
                    if st.button(f"Q{i + 1}", key=f"jump_flag_{i}", width='stretch'):
                        st.session_state.current_idx = i
                        st.rerun()

        st.divider()
        finish_label = "🏁 Submit Exam" if is_timed else "🏁 Finish Session"
        if st.button(finish_label, width='stretch', type="primary"):
            finish_session()
            st.rerun()

    elif st.session_state.mode == "results":
        if st.button("🔄 New Session", width='stretch', type="primary"):
            st.session_state.mode = "home"
            st.rerun()
        if st.button("📖 Review Answers", width='stretch'):
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
            if col.button(label, key=f"rnav_{i}", width='stretch'):
                st.session_state.current_idx = i
                st.rerun()
        st.divider()
        if st.button("📊 Back to Results", width='stretch'):
            st.session_state.mode = "results"
            st.rerun()

# ─── HOME ───────────────────────────────────────────────────────────────────

if st.session_state.mode == "home":
    all_logs_home = STORAGE.read_all_session_logs()
    streak = compute_study_streak(all_logs_home, st.session_state.learner_id)
    streak_pill = (
        f"<span class='cca-hero-pill'>🔥 {streak}-day streak</span>" if streak > 0
        else "<span class='cca-hero-pill'>✨ Start today's streak</span>"
    )
    st.markdown(
        f"<div class='cca-hero'><h1>🏛️ Claude Certified Architect – Foundations</h1>"
        f"<p>Welcome back, {st.session_state.cohort_name}!</p>"
        f"<div class='cca-hero-meta'>{streak_pill}"
        f"<span class='cca-hero-pill'>📚 {len(ALL_QUESTIONS)} questions</span>"
        f"<span class='cca-hero-pill'>🎯 {PASS_THRESHOLD_PCT}% to pass</span></div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "This interactive study guide helps you prepare for the **CCA-F** exam — solo or as a cohort."
    )
    with st.expander("ℹ️ New here? How this app works"):
        st.markdown(
            "- **Studying alone?** Ignore the \"cohort\" language — just enter your name above and use "
            "Learning Mode or the Timed Mock Exam like any other solo study tool.\n"
            "- **Shared checkpoint:** progress (which questions have been covered) is tracked for everyone "
            "using this deployment, not per-person — that's intentional for cohorts working through the "
            "bank together, but it means a solo learner sharing a deployment with others will see questions "
            "already covered by someone else.\n"
            "- **Materials tab** has source PDFs, a domain-by-domain cheat sheet, the exam blueprint, and "
            "reference links — worth a look before diving into questions.\n"
            "- **Drill Mode** runs a spaced-repetition review of your personal history, keyed by the "
            "Learner ID field in the sidebar (not a login — just retype the same ID each visit to keep "
            "your history). Misses come back next session; questions you get right repeatedly are spaced "
            "further apart instead of resurfacing every time."
        )
    st.divider()

    unused, used_count = get_unused_questions()
    render_stat_cards([
        (len(ALL_QUESTIONS), "Total Questions"),
        (used_count, "Already Covered"),
        (len(unused), "Available Today"),
        (f"🔥 {streak}" if streak else "—", "Day Streak"),
    ])

    if not unused:
        st.warning("🔁 All questions have been covered! The next session will cycle back to the beginning. Reset the checkpoint to start fresh.")

    st.divider()
    col_learn, col_timed, col_drill = st.columns(3)

    with col_learn:
        st.markdown(
            "<div class='cca-card'><strong>📖 Learning Mode</strong><br>"
            "Untimed. Works through questions you haven't covered yet, in sequence. "
            "Select an answer, hit Submit, and (if enabled) see the explanation right away. "
            "Progress is saved after every answer, and covered questions are skipped next time."
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("🚀 Start Learning Mode", type="primary", width='stretch'):
            start_exam("learning")
            st.rerun()

    with col_timed:
        st.markdown(
            "<div class='cca-card'><strong>⏱️ Timed Mock Exam</strong><br>"
            f"{TIMED_EXAM_QUESTION_COUNT} questions in {TIMED_EXAM_TIME_LIMIT_MIN} minutes — same "
            "question count and time limit as the real exam, every time. Answers aren't graded "
            "until you submit, just like the real thing. Doesn't affect Learning Mode's progress."
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("⏱️ Start Timed Mock Exam", width='stretch'):
            start_exam("timed")
            st.rerun()

    with col_drill:
        learner_id = st.session_state.learner_id
        all_logs = STORAGE.read_all_session_logs()
        srs_state = compute_learner_srs_state(learner_id, all_logs) if learner_id else {}
        drill_pool = compute_drill_queue(learner_id, all_logs) if learner_id else []
        on_cooldown = len(srs_state) - len(drill_pool)
        st.markdown(
            "<div class='cca-card'><strong>🎯 Drill Mode</strong><br>"
            "Untimed spaced-repetition review of <em>your</em> questions, keyed by the Learner ID in "
            "the sidebar. A miss brings a question back next session; each correct-in-a-row answer "
            "pushes it further out, so mastered questions stop resurfacing but still get a decay check "
            "later. Ordered most-overdue first. Doesn't affect the shared checkpoint."
            "</div>",
            unsafe_allow_html=True,
        )
        if not learner_id:
            st.caption("Set a Learner ID in the sidebar to enable Drill Mode.")
        elif not srs_state:
            st.caption("No questions on record yet for this Learner ID — answer some in Learning Mode first.")
        elif not drill_pool:
            st.caption(f"Nothing due right now — {on_cooldown} question(s) are on cooldown until their next review.")
        elif on_cooldown:
            st.caption(f"{on_cooldown} additional question(s) tracked but not due yet.")
        if st.button(
            f"🎯 Start Drill Mode ({len(drill_pool)})", width='stretch', disabled=not drill_pool
        ):
            start_exam("drill", external_pool=drill_pool)
            st.rerun()

    st.divider()
    st.markdown(
        "<div class='cca-card'><strong>🧩 Scenario Practice</strong><br>"
        "Untimed practice weighted toward one of the CCA-F's 6 named exam scenarios. Questions "
        "aren't individually tagged by scenario — this draws a stratified sample from the domains "
        "each scenario leans on most (see the Exam Blueprint tab), so it's an approximation, not a "
        "literal scenario-specific question set. Doesn't affect the shared checkpoint."
        "</div>",
        unsafe_allow_html=True,
    )
    scenario_names_all = [s["name"] for s in SCENARIOS]
    random_draw_label = "🎲 Random draw (4 of 6, mirrors the real exam)"
    col_scenario_pick, col_scenario_count, col_scenario_reroll = st.columns([3, 1, 1])
    scenario_choice = col_scenario_pick.selectbox(
        "Scenario", options=[random_draw_label] + scenario_names_all, key="scenario_picker",
    )
    scenario_count = col_scenario_count.number_input(
        "Questions", min_value=5, max_value=len(ALL_QUESTIONS),
        value=min(SCENARIO_PRACTICE_QUESTION_COUNT, len(ALL_QUESTIONS)), step=5,
    )
    if scenario_choice == random_draw_label:
        if col_scenario_reroll.button("🎲 Re-roll", width='stretch'):
            st.session_state.scenario_random_draw = random.sample(scenario_names_all, k=4)
            st.rerun()
        active_scenarios = st.session_state.scenario_random_draw
        st.caption("This session's draw: " + ", ".join(active_scenarios))
    else:
        active_scenarios = [scenario_choice]
        summary = next(s["summary"] for s in SCENARIOS if s["name"] == scenario_choice)
        st.caption(summary)

    weight_preview = combine_scenario_domain_weights(active_scenarios)
    top_domains = sorted(weight_preview.items(), key=lambda kv: -kv[1])[:3]
    st.caption(
        "Emphasizes: " + ", ".join(f"{d} ({w:.0%})" for d, w in top_domains if w > 0)
    )
    if st.button("🧩 Start Scenario Practice", width='stretch'):
        start_exam("scenario", external_pool=sample_scenario_pool(active_scenarios, int(scenario_count)))
        st.rerun()

# ─── EXAM ───────────────────────────────────────────────────────────────────

elif st.session_state.mode == "exam":
    is_timed = st.session_state.exam_type == "timed"

    # Server-side enforcement: whatever the on-screen clock shows, this is what actually
    # ends the exam — checked on every rerun (question nav, submit, etc.).
    if is_timed and st.session_state.exam_deadline and time.time() >= st.session_state.exam_deadline:
        st.session_state.time_expired = True
        finish_session()
        st.rerun()

    questions = st.session_state.questions
    idx = st.session_state.current_idx
    q = questions[idx]
    total = len(questions)
    already_answered = idx in st.session_state.answers
    chosen = st.session_state.answers.get(idx)

    if is_timed:
        render_countdown_clock(st.session_state.exam_deadline, TIMED_EXAM_TIME_LIMIT_MIN * 60)

        # Real proctored CCA-F screens split a scenario narrative (left pane) from the
        # actual question + answer choices (right pane) — mirror that here instead of
        # the single stacked card used by Learning/Drill/Review modes.
        scenario_text, question_text = split_scenario_and_question(q["question"])

        col_title, col_flag, col_prev, col_next = st.columns([5, 2, 1, 1])
        with col_title:
            st.markdown(f"### Question {idx + 1} of {total}")
            domain = q.get("domain")
            badge_html = f"<span class='cca-badge'>{domain}</span> " if domain else ""
            badge_html += f"<span class='cca-badge cca-badge-muted'>Q#{q['id']}</span>"
            st.markdown(badge_html, unsafe_allow_html=True)
        with col_flag:
            is_flagged = idx in st.session_state.flagged
            flag_label = "🚩 Flagged" if is_flagged else "🏳️ Flag for review"
            if st.button(flag_label, key=f"flag_{idx}", width='stretch', type="primary" if is_flagged else "secondary"):
                if is_flagged:
                    st.session_state.flagged.discard(idx)
                else:
                    st.session_state.flagged.add(idx)
                st.rerun()
        with col_prev:
            prev_clicked = st.button("◀ Prev", disabled=idx == 0, width='stretch', key="exam_prev")
        with col_next:
            next_clicked = st.button("Next ▶", disabled=idx == total - 1, width='stretch', key="exam_next")
        if prev_clicked:
            st.session_state.current_idx -= 1
            st.rerun()
        if next_clicked:
            st.session_state.current_idx += 1
            st.rerun()

        if scenario_text:
            col_scenario, col_question = st.columns([2, 3])
        else:
            col_scenario, col_question = None, st.container()

        if col_scenario is not None:
            with col_scenario:
                st.markdown(
                    f"<div class='cca-scenario-pane'><h4>Scenario</h4>{scenario_text}</div>",
                    unsafe_allow_html=True,
                )
                with st.expander("📝 Scratch pad"):
                    st.session_state.scratchpad[idx] = st.text_area(
                        "Scratch pad", value=st.session_state.scratchpad.get(idx, ""),
                        key=f"scratch_{idx}", label_visibility="collapsed", height=160,
                        placeholder="Jot down notes, elimination reasoning, etc. — not graded, not saved after the exam.",
                    )

        with col_question:
            st.markdown(f"<div class='cca-card'><strong>{question_text}</strong></div>", unsafe_allow_html=True)

            if already_answered:
                render_choice_rows(q, chosen, tag_chosen_answer=False, reveal_correct=False)
                st.caption("☑️ Answer recorded — results and explanations are shown once you submit the exam.")

                st.write("")
                if idx < total - 1:
                    if st.button("Next Question ▶", type="primary"):
                        st.session_state.current_idx += 1
                        st.rerun()
                else:
                    st.info("You've reached the last question. Click **Submit Exam** in the sidebar to save results.")
            else:
                choice_labels = [f"{letter}. {text}" for letter, text in q["choices"].items()]
                choice_keys = list(q["choices"].keys())

                selected_label = st.radio(
                    "Select your answer:", options=choice_labels, index=None, key=f"radio_{idx}"
                )

                st.write("")
                if st.button("Submit Answer", type="primary", disabled=selected_label is None):
                    selected_letter = choice_keys[choice_labels.index(selected_label)]
                    st.session_state.answers[idx] = selected_letter
                    st.rerun()

    else:
        prev_clicked, next_clicked = render_nav_header("exam", idx, total, f"### Question {idx + 1} of {total}", q)
        if prev_clicked:
            st.session_state.current_idx -= 1
            st.rerun()
        if next_clicked:
            st.session_state.current_idx += 1
            st.rerun()

        st.markdown(f"<div class='cca-card'><strong>{q['question']}</strong></div>", unsafe_allow_html=True)

        if already_answered:
            render_choice_rows(q, chosen, tag_chosen_answer=False, reveal_correct=True)

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
                if st.session_state.exam_type == "learning":
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

    is_timed_result = st.session_state.exam_type == "timed"
    hero_title = (
        "🕒 Mock Exam Results" if is_timed_result
        else "🎯 Drill Session Results" if st.session_state.exam_type == "drill"
        else "🧩 Scenario Practice Results" if st.session_state.exam_type == "scenario"
        else "📊 Session Results"
    )
    st.markdown(
        f"<div class='cca-hero'><h1>{hero_title}</h1>"
        f"<p>Team: {st.session_state.cohort_name}</p></div>",
        unsafe_allow_html=True,
    )
    if st.session_state.time_expired:
        st.warning("⏰ Time's up! Your exam was auto-submitted with whatever was answered when the clock hit zero.")

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
                "Exam Weight": f"{int(DOMAIN_WEIGHTS[dom] * 100)}%" if dom in DOMAIN_WEIGHTS else "—",
                "Correct": f"{s['correct']}/{s['total']}",
                "Score": f"{int(s['correct'] / s['total'] * 100)}%",
            }
            for dom, s in domain_stats.items()
        ])

        # Weighted estimate: only meaningful once every official domain has at least
        # one answered question, otherwise missing domains would silently drop out
        # of the weighted average instead of being counted against it.
        covered_weight = sum(DOMAIN_WEIGHTS.get(dom, 0) for dom in domain_stats)
        if covered_weight > 0 and set(DOMAIN_WEIGHTS).issubset(domain_stats):
            weighted_pct = sum(
                DOMAIN_WEIGHTS[dom] * (s["correct"] / s["total"]) for dom, s in domain_stats.items()
            ) * 100
            st.caption(f"📐 Weighted score estimate (by official exam domain weights): **{int(weighted_pct)}%**")

        focus_areas = [
            dom for dom, s in domain_stats.items()
            if (s["correct"] / s["total"]) * 100 < FOCUS_AREA_THRESHOLD_PCT
        ]
        if focus_areas:
            st.warning("🎯 Focus areas (below " + f"{FOCUS_AREA_THRESHOLD_PCT}%" + "): " + ", ".join(focus_areas))

    st.subheader("Question Breakdown")
    breakdown_rows = [
        {
            "Q#": i + 1,
            "ID": q["id"],
            "Domain": q.get("domain", ""),
            "Status": "✅" if answers.get(i) == q["correct"] else ("❌" if answers.get(i) else "⬜ Skipped"),
            "Your Answer": answers.get(i, "—"),
            "Correct": q["correct"],
            "Question": q["question"][:80] + "...",
        }
        for i, q in enumerate(questions)
    ]
    st.dataframe(breakdown_rows, width='stretch', hide_index=True)

    csv_buffer = io.StringIO()
    csv_writer = csv.DictWriter(csv_buffer, fieldnames=list(breakdown_rows[0].keys()))
    csv_writer.writeheader()
    csv_writer.writerows(breakdown_rows)
    st.download_button(
        label="⬇️ Download results (CSV)",
        data=csv_buffer.getvalue(),
        file_name=f"cca-f-results-{st.session_state.session_id}.csv",
        mime="text/csv",
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
            if st.button("☁️ Push to Git", type="primary", width='stretch'):
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
                if st.button("🔄 Retry Push", width='stretch'):
                    with st.spinner("Retrying..."):
                        st.session_state.git_push_status = git_push_checkpoint(st.session_state.session_id)
                    st.rerun()
            st.caption("Team members should run `git pull` before the next session.")

    if st.button("📖 Review All Answers with Explanations", type="primary", width='stretch'):
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

    materials_groups = get_materials_groups()
    pdf_group = next(g for g in materials_groups if g["title"] == "📄 Exam Guides")
    pdf_by_label = dict(zip((item["label"] for item in pdf_group["items"]), sorted(MATERIALS_DIR.glob("*.pdf"))))
    all_labels = [item["label"] for group in materials_groups for item in group["items"]]
    section = st.session_state.get("materials_section")
    if section not in all_labels:
        section = all_labels[0]

    if st.session_state.materials_view == "gallery":
        for group in materials_groups:
            if not group["items"]:
                continue
            st.subheader(group["title"])
            st.caption(group["description"])
            cols = st.columns(min(len(group["items"]), 4))
            for i, item in enumerate(group["items"]):
                with cols[i % len(cols)]:
                    st.markdown(
                        f"<div class='cca-card'><strong>{item['icon']} {item['title']}</strong><br>"
                        f"{item['description']}</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button("Open →", key=f"materials_open_{item['label']}", width='stretch'):
                        st.session_state.materials_section = item["label"]
                        st.session_state.materials_view = "detail"
                        st.rerun()
            st.write("")

    else:
        if st.button("⬅ Back to overview"):
            st.session_state.materials_view = "gallery"
            st.rerun()
        st.write("")

        if section in pdf_by_label:
            pdf_path = pdf_by_label[section]
            pdf_viewer(str(pdf_path), width=700, height=800)
            st.download_button(
                label="⬇️ Download PDF",
                data=pdf_path.read_bytes(),
                file_name=pdf_path.name,
                mime="application/pdf",
                key=f"dl_{pdf_path.stem}",
            )

        elif section == "🗺️ Cram Sheet":
            cram = load_cram_sheet()
            st.info(cram["headline"])
            st.subheader("Core Model")
            st.markdown("\n".join(f"- {point}" for point in cram["core_model"]))
            st.write("")
            for domain in cram["by_domain"]:
                st.subheader(f"{domain['domain']} ({domain['weight']})")
                st.markdown("\n".join(f"- {point}" for point in domain["must_know"]))
                st.write("")
            st.subheader("⚠️ Traps to Recognize")
            st.markdown("\n".join(f"- {trap}" for trap in cram["traps"]))
            cram_md = (
                "# Cram Sheet\n\n" + cram["headline"] + "\n\n## Core Model\n"
                + "\n".join(f"- {p}" for p in cram["core_model"]) + "\n\n"
                + "\n\n".join(
                    f"## {d['domain']} ({d['weight']})\n" + "\n".join(f"- {p}" for p in d["must_know"])
                    for d in cram["by_domain"]
                )
                + "\n\n## Traps to Recognize\n" + "\n".join(f"- {t}" for t in cram["traps"])
            )
            st.download_button(
                label="⬇️ Download Cram Sheet (Markdown)",
                data=cram_md,
                file_name="cca-f-cram-sheet.md",
                mime="text/markdown",
            )

        elif section == "🧭 Mental Model":
            mm = load_mental_model()
            st.info(mm["intro"])
            st.subheader("The Five Actors")
            cols = st.columns(len(mm["actors"]))
            for col, actor in zip(cols, mm["actors"]):
                with col:
                    st.markdown(
                        f"<div class='cca-card'><strong>{actor['icon']} {actor['name']}</strong><br>"
                        f"<small>{actor['role']}</small></div>",
                        unsafe_allow_html=True,
                    )
            st.write("")
            st.subheader("The 8-Step Loop")
            for step in mm["loop_steps"]:
                st.markdown(f"**{step['step']}. {step['title']}** — {step['detail']}")
            st.write("")
            st.success(f"**Core principle:** {mm['core_principle']}")
            st.warning(f"**Exam tip:** {mm['exam_tip']}")

        elif section == "📖 Glossary":
            st.caption("40+ terms grouped by domain. Search below or scan by section.")
            glossary = load_glossary()
            terms = glossary["terms"]
            query = st.text_input("🔍 Search terms", value="")
            if query:
                q = query.lower()
                terms = [t for t in terms if q in t["term"].lower() or q in t["definition"].lower()]
            domains_order = [
                "Agentic Architecture & Orchestration",
                "Claude Code Configuration & Workflows",
                "Prompt Engineering & Structured Output",
                "Tool Design & MCP Integration",
                "Context Management & Reliability",
            ]
            for domain in domains_order:
                domain_terms = sorted((t for t in terms if t["domain"] == domain), key=lambda t: t["term"])
                if not domain_terms:
                    continue
                st.subheader(domain)
                for t in domain_terms:
                    st.markdown(f"**{t['term']}** — {t['definition']}")
                st.write("")
            glossary_md = "# Glossary\n\n" + "\n\n".join(
                f"## {d}\n" + "\n\n".join(f"**{t['term']}** — {t['definition']}" for t in glossary["terms"] if t["domain"] == d)
                for d in domains_order
            )
            st.download_button(
                label="⬇️ Download Glossary (Markdown)",
                data=glossary_md,
                file_name="cca-f-glossary.md",
                mime="text/markdown",
            )

        elif section == "🧩 Scenario Walkthroughs":
            walkthroughs = load_scenario_walkthroughs()
            st.caption(walkthroughs["intro"])
            names = [w["name"] for w in walkthroughs["walkthroughs"]]
            pick = st.selectbox("Choose a scenario", options=names)
            w = next(w for w in walkthroughs["walkthroughs"] if w["name"] == pick)
            st.subheader(w["name"])
            st.markdown(f"**Setup:** {w['setup']}")
            st.write("")
            st.markdown("**Trace through the loop:**")
            for i, step in enumerate(w["trace"], start=1):
                st.markdown(f"{i}. {step}")
            st.write("")
            st.success(f"**Key lesson:** {w['key_lesson']}")
            st.warning(f"**Watch for:** {w['watch_for']}")

        elif section == "🎓 Capstone Project":
            capstone = load_capstone()
            st.subheader(capstone["title"])
            st.info(capstone["why"])
            st.markdown(capstone["overview"])
            st.write("")
            st.markdown("**Minimum scope:**")
            st.markdown("\n".join(f"- {item}" for item in capstone["minimum_scope"]))
            st.write("")
            for phase in capstone["phases"]:
                with st.expander(f"{phase['title']}  ·  {phase['domain_focus']}"):
                    st.markdown("\n".join(f"- {task}" for task in phase["tasks"]))
            st.write("")
            st.subheader("Self-Assessment Rubric")
            st.table([{"Domain": r["domain"], "Check yourself": r["check"]} for r in capstone["self_assessment_rubric"]])
            st.write("")
            st.markdown(f"*{capstone['closing_note']}*")

        elif section == "📇 Cheat Sheets":
            st.caption("Condensed key facts per domain — a quick pass before the exam, not a substitute for the lessons.")
            cheat_sheets = load_cheat_sheets()
            cheat_sheet_md_parts = []
            for domain in cheat_sheets["domains"]:
                st.subheader(f"{domain['title']} ({domain['weight']})")
                bullet_lines = [f"- {point}" for point in domain["points"]]
                st.markdown("\n".join(bullet_lines))
                st.write("")
                cheat_sheet_md_parts.append(
                    f"## {domain['title']} ({domain['weight']})\n" + "\n".join(bullet_lines)
                )
            st.download_button(
                label="⬇️ Download Cheat Sheet (Markdown)",
                data="# CCA-F Cheat Sheet\n\n" + "\n\n".join(cheat_sheet_md_parts),
                file_name="cca-f-cheat-sheet.md",
                mime="text/markdown",
            )

        elif section == "⌨️ Claude Code Reference":
            st.caption(
                "Operational reference for the Claude Code CLI — slash commands, keyboard shortcuts, config files, "
                "hooks, and CLAUDE.md conventions. Useful for the exam's Claude Code Configuration & Workflows "
                "domain, and as a working reference on the job."
            )
            cc_ref = load_claude_code_reference()
            cc_md_parts = []
            for cc_section in cc_ref["sections"]:
                st.subheader(f"{cc_section['icon']} {cc_section['title']}")
                if cc_section["kind"] == "table":
                    st.table([dict(zip(cc_section["columns"], row)) for row in cc_section["rows"]])
                    md_table = (
                        "| " + " | ".join(cc_section["columns"]) + " |\n"
                        + "| " + " | ".join(["---"] * len(cc_section["columns"])) + " |\n"
                        + "\n".join("| " + " | ".join(row) + " |" for row in cc_section["rows"])
                    )
                    cc_md_parts.append(f"## {cc_section['title']}\n{md_table}")
                else:
                    bullet_lines = [f"- {point}" for point in cc_section["points"]]
                    st.markdown("\n".join(bullet_lines))
                    cc_md_parts.append(f"## {cc_section['title']}\n" + "\n".join(bullet_lines))
                st.write("")
            st.download_button(
                label="⬇️ Download Claude Code Reference (Markdown)",
                data="# Claude Code Reference\n\n" + "\n\n".join(cc_md_parts),
                file_name="claude-code-reference.md",
                mime="text/markdown",
            )

        elif section == "📋 Exam Blueprint":
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
            st.markdown(
                "**Exam scenarios** — 4 of the following 6 scenarios appear on any given exam. "
                "Practice any of them (or a random draw of 4) from Home under Scenario Practice:"
            )
            st.markdown(
                "\n".join(f"{i}. **{s['name']}** — {s['summary']}" for i, s in enumerate(SCENARIOS, start=1))
            )

        elif section == "🔗 Reference Links":
            ref = load_reference_links()
            for ref_section in ref["sections"]:
                st.subheader(ref_section["title"])
                for item in ref_section["items"]:
                    st.markdown(f"**[{item['label']}]({item['url']})**  \n{item['description']}")
                st.write("")

        elif section == "🤖 Generate Questions":
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

# ─── PROGRESS ───────────────────────────────────────────────────────────────

elif st.session_state.mode == "progress":
    st.title("📈 Progress")
    st.caption(
        "Aggregated across every recorded session on this deployment — this is cohort-wide history, "
        "not a personal record tied to you individually (see the checkpoint model explained on Home)."
    )
    st.divider()

    logs = STORAGE.read_all_session_logs()
    if not logs:
        st.info("No sessions recorded yet. Finish a Learning Mode or Timed Mock Exam session to see progress here.")
    else:
        render_stat_cards([
            (len(logs), "Sessions Recorded"),
            (sum(log.get("total_answered", 0) for log in logs), "Questions Answered"),
            (f"{int(sum(log.get('correct', 0) for log in logs) / max(sum(log.get('total_answered', 0) for log in logs), 1) * 100)}%",
             "Overall Accuracy"),
        ])

        st.write("")
        domain_stats = aggregate_domain_stats_from_logs(logs)
        if domain_stats:
            st.subheader("Score by Domain (all-time)")
            st.table([
                {
                    "Domain": dom,
                    "Exam Weight": f"{int(DOMAIN_WEIGHTS[dom] * 100)}%" if dom in DOMAIN_WEIGHTS else "—",
                    "Correct": f"{s['correct']}/{s['total']}",
                    "Score": f"{int(s['correct'] / s['total'] * 100)}%",
                }
                for dom, s in domain_stats.items()
            ])
            focus_areas = [
                dom for dom, s in domain_stats.items()
                if (s["correct"] / s["total"]) * 100 < FOCUS_AREA_THRESHOLD_PCT
            ]
            if focus_areas:
                st.warning("🎯 Focus areas (below " + f"{FOCUS_AREA_THRESHOLD_PCT}%" + "): " + ", ".join(focus_areas))
        else:
            st.caption("No domain-tagged questions have been answered yet.")

        st.write("")
        st.subheader("Score Trend by Session")
        trend_rows = [
            {
                "Date": log.get("date", "?"),
                "Cohort": log.get("cohort", "?"),
                "Score %": int(log["correct"] / log["total_answered"] * 100) if log.get("total_answered") else 0,
            }
            for log in logs
        ]
        st.dataframe(trend_rows, width='stretch', hide_index=True)
        render_score_trend_chart(trend_rows)

        st.divider()
        st.subheader("📄 Study Report")
        report_caption = "Cohort-wide stats above, as a file to save or share."
        if st.session_state.learner_id:
            report_caption += (
                f" Since a Learner ID (\"{st.session_state.learner_id}\") is set in the sidebar, it also "
                "includes your personal domain breakdown and drill backlog."
            )
        else:
            report_caption += " Set a Learner ID in the sidebar to also include a personal breakdown."
        st.caption(report_caption)

        report = build_progress_report(logs, st.session_state.learner_id)
        col_md, col_html = st.columns(2)
        col_md.download_button(
            label="⬇️ Download Report (Markdown)",
            data=render_progress_report_markdown(report),
            file_name="cca-f-study-report.md",
            mime="text/markdown",
            width='stretch',
        )
        col_html.download_button(
            label="🖨️ Download Printable Report (HTML)",
            data=render_progress_report_html(report),
            file_name="cca-f-study-report.html",
            mime="text/html",
            width='stretch',
            help="Open the downloaded file in a browser and use Print (Ctrl/Cmd+P) → Save as PDF.",
        )
