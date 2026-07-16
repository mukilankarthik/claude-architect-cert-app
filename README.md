# Claude Certified Architect – Foundations Study Guide

An interactive Streamlit app for cohort study sessions preparing for the **Claude Certified Architect – Foundations (CCA-F)** exam. Includes a large bank of practice questions with full answer explanations, a session checkpoint system, curated reference materials, and a tool to generate new questions from your own documents using the AI provider of your choice.

Maintained by **Mukilan Karthikeyan** ([mukilankarthikeyan@gmail.com](mailto:mukilankarthikeyan@gmail.com)).

---

## Features

- **Large practice question bank** — multiple-choice questions covering the full CCAR-F blueprint (Agentic Architecture, Tool Design & MCP, Claude Code Configuration, Prompt Engineering, Context Management), each with a detailed explanation for every answer choice
- **Session checkpoint** — questions already covered in past sessions are automatically skipped so your team never repeats the same questions across launches
- **Cohort-friendly** — set a team name, track progress on a shared screen with a live question navigator
- **Configurable sessions** — choose whether to shuffle, and whether to show explanations immediately or on demand
- **Materials tab** — inline PDF viewer of the exam guide, a domain/scenario blueprint summary, and curated reference links
- **AI-agnostic question generation** — upload a PDF or paste text and generate new questions using **Anthropic (Claude)**, **OpenAI (GPT)**, or **Google (Gemini)** — bring your own API key for whichever provider you prefer
- **Pluggable persistence** — local JSON files by default, or a shared Postgres database when hosting in the cloud (see [Cloud Deployment](#cloud-deployment--session-persistence) below)

---

## Project Structure

```
claude-architect-cert-app/
├── app.py                        # Streamlit application (UI + session flow)
├── ai_providers.py                # Provider-agnostic AI text generation (Anthropic / OpenAI / Gemini)
├── storage.py                     # Pluggable persistence (local JSON files or Postgres)
├── questions.json                 # Parsed CCA-F practice questions
├── pyproject.toml                 # Poetry dependency config
├── poetry.lock                    # Pinned dependency versions
├── .env.example                   # Environment variables this app reads
├── .gitignore
├── materials/
│   ├── *.pdf                      # Exam guides / question banks shown in the Materials tab
│   └── reference_links.json       # Curated reference links
└── checkpoint.json                # Auto-generated locally; committed via the git-push flow below (local-file backend only)
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Git (optional — only needed for the local-file checkpoint sync flow described below)

### 1. Install Poetry

Poetry manages all Python dependencies for this project.

**macOS / Linux / WSL:**
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

**Windows (PowerShell):**
```powershell
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
```

After installation, restart your terminal and verify:
```bash
poetry --version
```

> Full instructions at [python-poetry.org/docs](https://python-poetry.org/docs/#installation)

### 2. Clone the repo

```bash
git clone <your-repo-url>
cd claude-architect-cert-app
```

### 3. Install dependencies

```bash
poetry install
```

Hosting with a shared Postgres database instead of local files? Also install the optional `postgres` extra:

```bash
poetry install -E postgres
```

### 4. (Optional) Configure environment variables

Copy `.env.example` to `.env` and fill in whichever you need — every value is optional and can also be typed directly into the app's UI at runtime:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Prefills the API key field when Anthropic is selected in "Generate Questions" |
| `OPENAI_API_KEY` | Prefills the API key field when OpenAI is selected |
| `GOOGLE_API_KEY` | Prefills the API key field when Gemini is selected |
| `DATABASE_URL` | If set, switches persistence to Postgres instead of local files — see below |

### 5. Run the app

```bash
poetry run streamlit run app.py
```

Then open **http://localhost:8501** in your browser.

---

## AI Providers

The **Materials → 🤖 Generate Questions** tab can turn any PDF or pasted text into new multiple-choice questions in the same format as the exam bank, using whichever provider you choose:

| Provider | Model field default | Get a key |
|---|---|---|
| Anthropic (Claude) | `claude-sonnet-4-6` | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI (GPT) | `gpt-4.1` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Google (Gemini) | `gemini-2.5-flash` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

API keys are only held in memory for the current session — they're never written to disk or logged. The model field is editable, so you can point at any model your key has access to. Adding a fourth provider is a small, contained change in `ai_providers.py` (one entry in `PROVIDERS`, one dispatch branch in `generate_text`) — `app.py` doesn't need to change.

---

## Checkpoint System

The app tracks which questions have been covered across sessions.

| Behaviour | Detail |
|---|---|
| Questions covered | Saved as soon as an answer is submitted |
| Next session | Automatically draws only from questions not yet covered |
| All exhausted | App warns you and cycles back from the beginning |
| Reset | Use the **Reset Checkpoint** button on the Home screen to start fresh |

*How* that checkpoint is stored depends on the active storage backend — see the next section.

---

## Cloud Deployment & Session Persistence

By default this app stores `checkpoint.json` and `session_logs/*.json` as local files (`storage.py`'s `LocalFileStorage`). That's the simplest option and works well when the app runs on **one long-lived machine or VM** that your whole cohort shares — a facilitator's laptop, a single always-on server, etc. In that setup, the **Results** screen offers a "Push to Git" button that commits and pushes `checkpoint.json` + the session log so teammates can `git pull` before their next session.

**That local-file + git-push pattern breaks down on most cloud PaaS hosts** (Streamlit Community Cloud, a serverless container, an auto-scaled instance group, etc.), for two reasons:
1. **Disk is often ephemeral** — a redeploy or restart can wipe local files, silently resetting everyone's checkpoint.
2. **There's usually no git identity or push access** from inside the running container, so the auto-push step has nothing to push to even if the disk did persist.

### Using a shared Postgres database instead

Set the `DATABASE_URL` environment variable (standard `postgresql://user:pass@host:port/dbname` form) wherever you deploy, and the app automatically switches to `storage.py`'s `PostgresStorage` backend — no code changes needed. Checkpoint state and session logs are then read from and written to that database on every request, so:
- State survives restarts and redeploys (it lives outside the container).
- Every instance of the app (if you're running more than one) sees the same checkpoint, since they all read from the same database.
- The git-push flow is no longer shown on the Results screen — the Sync section instead confirms syncing happened automatically.

The Postgres backend creates its two tables (`checkpoint_state`, `session_logs`) automatically on first use — no manual migration step. Install the extra dependency it needs with `poetry install -E postgres` (or `pip install psycopg2-binary`).

**Known limitation:** the checkpoint is stored as a single row that's read, modified, and written back on every answer. That's simple and mirrors the local-file behavior exactly, but it isn't safe under many *simultaneous* writers hammering the same row at once. For a study cohort answering questions at normal human pace this is a non-issue; it wasn't designed for high-concurrency workloads. If you outgrow that, the fix is moving to one row per answered question instead of one JSON blob — `storage.py` is the only file that would need to change.

### Where this fits

| Host | Notes |
|---|---|
| A shared VM / on-prem server | Local-file backend + git-push works fine; disk and git access both persist |
| Streamlit Community Cloud | Set `DATABASE_URL` — disk resets on redeploy |
| Render / Railway / Fly.io | Set `DATABASE_URL` unless you've attached a persistent volume you fully control |
| A container on ECS / Cloud Run | Always set `DATABASE_URL` — these are stateless by design |

Any managed Postgres works (Supabase, Neon, RDS, Cloud SQL, etc.) — this app only needs the standard connection string.

### Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (already the case if you're reading this from the repo).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick this repo/branch and set the main file to `app.py`.
3. Streamlit Cloud installs from `requirements.txt` automatically (not `pyproject.toml`/Poetry) — this repo ships both, so no extra step is needed. If you're using the Postgres backend, uncomment the `psycopg2-binary` line in `requirements.txt` first.
4. In the app's **Settings → Secrets**, paste the contents of `.streamlit/secrets.toml.example` with real values filled in (only the keys you need — every one is optional). Streamlit exposes these as both `st.secrets` and `os.environ`, so `app.py`'s existing `os.environ.get(...)` calls work unchanged.
5. Deploy. Since Streamlit Cloud's disk resets on every redeploy, set `DATABASE_URL` in secrets if you want checkpoints to survive across redeploys and be shared across viewers — otherwise each redeploy starts everyone's checkpoint fresh (still fine for a single live workshop session).
6. `.streamlit/config.toml` in this repo sets the app's theme (teal accent) automatically — no extra configuration needed.

---

## Study Materials

The `materials/` folder contains all reference content surfaced in the **Materials** tab of the app.

### Adding new PDFs

Drop any `.pdf` file into the `materials/` folder — the app automatically picks it up and creates a new tab for it. No code changes needed. The tab name is derived from the filename: `prompt-engineering-guide.pdf` → **Prompt Engineering Guide**.

### Adding new reference links

Edit `materials/reference_links.json` to add links to an existing section or create a new one:

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

See the **Materials → 📋 Exam Blueprint** tab in the app for the full domain-weight breakdown.
