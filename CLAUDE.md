# CLAUDE.md

Guidance for working in this repo.

## What this is

A single-page Streamlit app (`app.py`) for cohort study sessions preparing for the Claude Certified Architect – Foundations (CCA-F) exam. Cohorts work through a shared multiple-choice question bank; progress is checkpointed so questions aren't repeated across sessions; a "Generate Questions" tool can expand the bank from any source document using the user's own choice of AI provider.

## Architecture

- **`app.py`** — the entire UI + session flow (home, exam, results, review, materials). Streamlit reruns this whole script on every interaction; state that must survive a rerun lives in `st.session_state` (see `init_state()`).
- **`storage.py`** — pluggable persistence behind a single interface (`load_checkpoint`, `save_checkpoint_entry`, `finalize_checkpoint`, `reset_checkpoint`, `write_session_log`). Two backends:
  - `LocalFileStorage` — `checkpoint.json` + `session_logs/*.json` on local disk. Default when `DATABASE_URL` isn't set.
  - `PostgresStorage` — same shape, stored in Postgres. Used automatically when `DATABASE_URL` is set (required for durable state on Streamlit Community Cloud / any ephemeral-disk host — see README's "Cloud Deployment" section for why).
  - `STORAGE` is a single `@st.cache_resource` instance **per server process** — on a shared deployment this means checkpoint state is global across every visitor hitting that process, not per-browser-session. This is by design (shared cohort progress), not a bug — don't "fix" it into per-user scoping without checking with the user first.
- **`ai_providers.py`** — provider-agnostic question generation (Anthropic / OpenAI / Gemini). Adding a fourth provider is one `PROVIDERS` entry + one `_generate_*` function + one dispatch branch in `generate_text()`; `app.py` never needs to change.
- **`questions.json`** — the question bank. Each entry: `id`, `question`, `choices` (dict of letter → text, not always exactly 4), `correct` (a letter), `explanation` (a flat string covering all choices — see below), optional `domain`.

## The explanation string format

`explanation` is one flat string that walks through every choice in sequence, e.g. `"A. <choice text>. ❌ Incorrect.\n<reasoning>\nB. <choice text>. ✅ Correct.\n<reasoning>\n..."`. It does **not** reliably use blank lines between choices (many entries are scraped from PDFs with mid-sentence line wraps), so don't split on `\n`. `app.py`'s `parse_explanation()` locates each choice's block by searching for where that choice's own text (first ~18 chars) reappears in the explanation, then slices between those positions — order-independent, so a correct-answer-first explanation works too. It returns `None` (render the raw string as a fallback) when a choice's text can't be located, which happens for a fraction of entries with heavily paraphrased echoes. Keep new/generated questions' explanations in this same per-choice-echo convention so they parse cleanly.

## Conventions

- Theming lives in one `THEME_CSS` block near the top of `app.py`, expressed as CSS custom properties (`--cca-*`) plus a handful of `.cca-*` component classes (`cca-card`, `cca-choice`, `cca-explain-block`, `cca-banner`, `cca-stat-card`, `cca-badge`). Reuse these classes for new UI rather than inventing one-off inline styles.
- `checkpoint.json` and `session_logs/*.json` are runtime state, not source — don't hand-edit them, and don't commit ones generated from local testing/dev runs.
- Poetry is the source of dependency truth (`pyproject.toml` / `poetry.lock`); `requirements.txt` is a second copy kept in sync by hand for Streamlit Community Cloud, which doesn't read Poetry files. If you change a dependency, update both.
- No test suite exists yet. Verify UI changes by actually running the app (`poetry run streamlit run app.py`) and exercising the flow — this project has been checked with a headless Playwright pass driving the exam flow end to end; that's the bar for "verified," not just a syntax check.

## Git

- Never add Claude/AI attribution (co-author lines, tags, etc.) to commits, files, or anywhere else in this repo.
