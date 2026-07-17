# Claude Certified Architect – Foundations Study Guide

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Streamlit](https://img.shields.io/badge/streamlit-1.58%2B-ff4b4b)
![Storage](https://img.shields.io/badge/storage-local%20json%20%7C%20postgres-0d9488)

An interactive Streamlit app for cohort study sessions preparing for the **Claude Certified Architect – Foundations (CCA-F)** exam. Includes a large practice question bank with a detailed explanation for every choice, a checkpoint system so questions aren't repeated across sessions, a timed mock exam that mirrors the real thing, curated reference materials, and a tool to generate new questions from your own documents.

Maintained by **Mukilan Karthikeyan** ([mukilankarthikeyan@gmail.com](mailto:mukilankarthikeyan@gmail.com)).

---

## Contents

- [Features](#features)
- [Study Modes](#study-modes)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [AI Providers](#ai-providers)
- [Checkpoint System](#checkpoint-system)
- [Cloud Deployment & Session Persistence](#cloud-deployment--session-persistence)
- [Study Materials](#study-materials)
- [Estimated Exam Details](#estimated-exam-details)

---

## Features

- **Large practice question bank** — multiple-choice questions covering the full CCA-F blueprint (Agentic Architecture, Tool Design & MCP, Claude Code Configuration, Prompt Engineering, Context Management), each with a detailed, per-choice explanation rendered as its own color-coded block instead of a wall of text
- **Two study modes** — untimed Learning Mode and a timed Mock Exam that mirrors the real exam's question count and time limit (see [Study Modes](#study-modes))
- **Session checkpoint** — questions already covered in past Learning Mode sessions are automatically skipped so your team never repeats the same questions across launches
- **Cohort-friendly** — set a team name, track progress on a shared screen with a live question navigator
- **Materials tab** — inline PDF viewer of exam guides, a domain/scenario blueprint summary, and curated reference links
- **AI-agnostic question generation** — upload a PDF or paste text and generate new questions using **Anthropic (Claude)**, **OpenAI (GPT)**, or **Google (Gemini)** — bring your own API key for whichever provider you prefer
- **Pluggable persistence** — local JSON files by default, or a shared Postgres database when hosting in the cloud (see [Cloud Deployment](#cloud-deployment--session-persistence))

---

## Study Modes

| | 📖 Learning Mode | ⏱️ Timed Mock Exam |
|---|---|---|
| **Timing** | Untimed | 120 minutes, with a live countdown clock |
| **Questions** | All not-yet-covered questions, in sequence | A fresh random draw of 60 questions every time — the same count as the real exam |
| **Feedback** | Immediate (toggle on/off) — see the correct answer and explanation right after each submission | Hidden until you submit — like a real proctored exam, you only find out what you got right at the end |
| **Checkpoint** | Tracked — answered questions are skipped in future Learning Mode sessions | Not tracked — doesn't affect Learning Mode's covered-questions pool, so you can retake it anytime |

Both modes end at the same **Results** screen (score, pass/fail against the estimated 70% threshold, score-by-domain breakdown) and can be followed by **Review Answers** to go back through every question with full explanations.

---

## Project Structure

```
claude-architect-cert-app/
├── app.py                        # Streamlit application (UI + session flow)
├── ai_providers.py                # Provider-agnostic AI text generation (Anthropic / OpenAI / Gemini)
├── storage.py                     # Pluggable persistence (local JSON files or Postgres)
├── questions.json                 # CCA-F practice question bank
├── CLAUDE.md                      # Architecture notes for AI coding agents working in this repo
├── pyproject.toml / poetry.lock   # Poetry dependency management
├── requirements.txt                # Mirror of dependencies for Streamlit Community Cloud (doesn't read Poetry files)
├── .env.example                   # Local environment variables this app reads
├── .streamlit/
│   ├── config.toml                # App theme
│   └── secrets.toml.example       # Reference for Streamlit Cloud's Settings -> Secrets
└── materials/
    ├── *.pdf                      # Exam guides / question source PDFs shown in the Materials tab
    └── reference_links.json       # Curated reference links
```

`checkpoint.json` and `session_logs/*.json` aren't listed above — they're generated at runtime by the local-file storage backend, not part of the source tree.

---

## Getting Started

### Prerequisites

- Python 3.10+
- Git (optional — only needed for the local-file checkpoint sync flow described below)

### 1. Install Poetry

```bash
curl -sSL https://install.python-poetry.org | python3 -   # macOS / Linux / WSL
```
```powershell
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -   # Windows
```

Restart your terminal and verify with `poetry --version`. Full instructions at [python-poetry.org/docs](https://python-poetry.org/docs/#installation).

### 2. Clone and install

```bash
git clone <your-repo-url>
cd claude-architect-cert-app
poetry install
```

Hosting with a shared Postgres database instead of local files? Install the optional extra too:

```bash
poetry install -E postgres
```

### 3. (Optional) Configure environment variables

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Prefills the API key field when Anthropic is selected in "Generate Questions" |
| `OPENAI_API_KEY` | Prefills the API key field when OpenAI is selected |
| `GOOGLE_API_KEY` | Prefills the API key field when Gemini is selected |
| `DATABASE_URL` | If set, switches persistence to Postgres instead of local files — see [Cloud Deployment](#cloud-deployment--session-persistence) |

Every value is optional — API keys can also be typed directly into the app's UI at runtime.

### 4. Run

```bash
poetry run streamlit run app.py
```

Then open **http://localhost:8501**.

---

## AI Providers

The **Materials → 🤖 Generate Questions** tab turns any PDF or pasted text into new multiple-choice questions in the same format as the exam bank, using whichever provider you choose:

| Provider | Model field default | Get a key |
|---|---|---|
| Anthropic (Claude) | `claude-sonnet-4-6` | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI (GPT) | `gpt-4.1` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Google (Gemini) | `gemini-2.5-flash` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

API keys are only held in memory for the current session — never written to disk or logged. The model field is editable, so you can point at any model your key has access to. Adding a fourth provider is a small, contained change in `ai_providers.py` (one entry in `PROVIDERS`, one dispatch branch in `generate_text`) — `app.py` doesn't need to change.

---

## Checkpoint System

Learning Mode tracks which questions have been covered across sessions (Timed Mock Exam intentionally doesn't participate in this — see [Study Modes](#study-modes)).

| Behaviour | Detail |
|---|---|
| Questions covered | Saved as soon as an answer is submitted |
| Next Learning Mode session | Automatically draws only from questions not yet covered |
| All exhausted | App warns you and cycles back from the beginning |
| Reset | Use the **Reset Checkpoint** button on the Home screen to start fresh |

*How* that checkpoint is stored depends on the active storage backend — see the next section.

---

## Cloud Deployment & Session Persistence

By default this app stores `checkpoint.json` and `session_logs/*.json` as local files (`storage.py`'s `LocalFileStorage`). That works well on **one long-lived machine or VM** your whole cohort shares. In that setup, the **Results** screen offers a "Push to Git" button that commits and pushes the checkpoint + session log so teammates can `git pull` before their next session.

**That pattern breaks on most cloud PaaS hosts** (Streamlit Community Cloud, a serverless container, an auto-scaled instance group), for two reasons: disk is usually ephemeral (a redeploy/restart wipes local files), and there's typically no git identity or push access from inside the running container.

### Using Postgres instead

Set the `DATABASE_URL` environment variable and the app automatically switches to `storage.py`'s `PostgresStorage` backend — no code changes needed. State then survives restarts/redeploys and stays consistent across every instance of the app, since they all read the same database. Install the extra dependency with `poetry install -E postgres` (or `pip install psycopg2-binary`); the two tables it needs (`checkpoint_state`, `session_logs`) are created automatically on first use.

**Note:** the checkpoint is a single row read-modified-written on every answer — fine for a study cohort's normal pace, not built for high-concurrency simultaneous writes.

### Deploying to Streamlit Community Cloud with Supabase

A concrete end-to-end path using a free-tier host on each side:

1. **Create a Supabase project** at [supabase.com](https://supabase.com) → New project.
2. **Get the connection string**: Project Settings → Database → Connection string. Use the **pooled** connection (Transaction mode, port `6543`, host like `aws-0-<region>.pooler.supabase.com`) rather than the direct one on port `5432` — the direct connection is IPv6-only and often unreachable from Streamlit Cloud. The pooled username looks like `postgres.<project-ref>`, not just `postgres`.
3. **URL-encode the password** if it contains special characters — `@` → `%40`, `$` → `%24`, `!` → `%21`, etc. An unencoded `@` in particular breaks the URL, since `@` is also the delimiter between credentials and host.
4. **Deploy the app**: push this repo to GitHub, then on [share.streamlit.io](https://share.streamlit.io) → **New app** → pick the repo/branch, main file `app.py`. Streamlit Cloud installs from `requirements.txt` (not Poetry) — `psycopg2-binary` is already uncommented there.
5. **Add secrets**: app's **Settings → Secrets**, paste in whichever keys you need from `.streamlit/secrets.toml.example`, including:
   ```
   DATABASE_URL = "postgresql://postgres.<project-ref>:<url-encoded-password>@aws-0-<region>.pooler.supabase.com:6543/postgres"
   ```
6. **Verify**: once redeployed, check the Home screen's "Storage backend" caption reads `postgres`. Any managed Postgres works the same way (Neon, RDS, Cloud SQL, etc.) — this app only needs the standard connection string.

### Where this fits

| Host | Notes |
|---|---|
| A shared VM / on-prem server | Local-file backend + git-push works fine; disk and git access both persist |
| Streamlit Community Cloud | Set `DATABASE_URL` — disk resets on redeploy |
| Render / Railway / Fly.io | Set `DATABASE_URL` unless you've attached a persistent volume you fully control |
| A container on ECS / Cloud Run | Always set `DATABASE_URL` — these are stateless by design |

---

## Study Materials

The `materials/` folder contains all reference content surfaced in the **Materials** tab.

**Adding a PDF**: drop any `.pdf` file into `materials/` — the app automatically picks it up and creates a new tab, named from the filename (`prompt-engineering-guide.pdf` → **Prompt Engineering Guide**). No code changes needed.

**Adding reference links**: edit `materials/reference_links.json`:

```json
{
  "title": "Your Section Title",
  "items": [
    {
      "label": "Link Label",
      "url": "https://...",
      "description": "One-line description shown under the link"
    }
  ]
}
```

---

## Estimated Exam Details

| | |
|---|---|
| **Certification** | Claude Certified Architect – Foundations (CCA-F) |
| **Offered by** | Anthropic (via Claude Partner Network) |
| **Question format** | Multiple choice / multiple response |
| **Items** | 60 (4 scenarios drawn from a bank of 6) |
| **Time limit** | 120 minutes |
| **Passing score** | Scaled score of 720 on a 100–1000 scale |

See **Materials → 📋 Exam Blueprint** in the app for the full domain-weight breakdown.
