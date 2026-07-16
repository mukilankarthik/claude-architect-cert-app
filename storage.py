"""Pluggable persistence for checkpoint state and session logs.

Two backends share an identical method surface so the rest of the app
never needs to know which one is active:

- ``LocalFileStorage`` — the original behavior: JSON files on local disk.
  Works well for a single shared machine/VM, paired with the git-push
  sync flow in app.py.
- ``PostgresStorage`` — used automatically when the ``DATABASE_URL`` env
  var is set. Needed on ephemeral or multi-instance cloud hosts, where
  local disk isn't guaranteed to persist or be shared across instances.

Call ``get_storage()`` to obtain the right backend for the current
environment.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
CHECKPOINT_PATH = BASE_DIR / "checkpoint.json"
SESSION_LOGS_DIR = BASE_DIR / "session_logs"

EMPTY_CHECKPOINT = {"used_ids": [], "sessions": []}


class LocalFileStorage:
    """Default backend: checkpoint.json + session_logs/*.json on local disk."""

    backend_name = "local"

    def __init__(self):
        SESSION_LOGS_DIR.mkdir(exist_ok=True)

    def load_checkpoint(self) -> dict:
        if CHECKPOINT_PATH.exists():
            with open(CHECKPOINT_PATH, encoding="utf-8") as f:
                return json.load(f)
        return dict(EMPTY_CHECKPOINT)

    def _write_checkpoint(self, data: dict) -> None:
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def save_checkpoint_entry(self, question_id: int) -> None:
        data = self.load_checkpoint()
        if question_id not in data["used_ids"]:
            data["used_ids"].append(question_id)
        self._write_checkpoint(data)

    def finalize_checkpoint(self, session_meta: dict) -> None:
        data = self.load_checkpoint()
        data["sessions"].append(session_meta)
        self._write_checkpoint(data)

    def reset_checkpoint(self) -> None:
        if CHECKPOINT_PATH.exists():
            CHECKPOINT_PATH.unlink()

    def write_session_log(self, session_id: str, log: dict) -> None:
        log_path = SESSION_LOGS_DIR / f"{session_id}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)


class PostgresStorage:
    """Shared backend for cloud hosting: checkpoint + session logs in Postgres.

    Stores the checkpoint as a single JSONB row (same shape as
    checkpoint.json) and each session log as one JSONB row keyed by
    session_id. This keeps the schema trivial and mirrors
    LocalFileStorage's semantics exactly, but it means the checkpoint
    update is a read-modify-write — safe for a study cohort's normal
    usage pattern, not designed for high-concurrency simultaneous writes.
    psycopg2 is imported lazily so it's only required when DATABASE_URL
    is actually set (see the "postgres" extra in pyproject.toml).
    """

    backend_name = "postgres"

    def __init__(self, database_url: str):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL is set but psycopg2 isn't installed. "
                "Install it with: pip install psycopg2-binary "
                "(or poetry install -E postgres)."
            ) from exc
        self._psycopg2 = psycopg2
        self._database_url = database_url
        self._ensure_schema()

    def _connect(self):
        return self._psycopg2.connect(self._database_url)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint_state (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    data JSONB NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS session_logs (
                    session_id TEXT PRIMARY KEY,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.commit()

    def load_checkpoint(self) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT data FROM checkpoint_state WHERE id = 1")
            row = cur.fetchone()
            return row[0] if row else dict(EMPTY_CHECKPOINT)

    def _write_checkpoint(self, data: dict) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO checkpoint_state (id, data) VALUES (1, %s)
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
                """,
                [self._psycopg2.extras.Json(data)],
            )
            conn.commit()

    def save_checkpoint_entry(self, question_id: int) -> None:
        data = self.load_checkpoint()
        if question_id not in data["used_ids"]:
            data["used_ids"].append(question_id)
        self._write_checkpoint(data)

    def finalize_checkpoint(self, session_meta: dict) -> None:
        data = self.load_checkpoint()
        data["sessions"].append(session_meta)
        self._write_checkpoint(data)

    def reset_checkpoint(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM checkpoint_state WHERE id = 1")
            conn.commit()

    def write_session_log(self, session_id: str, log: dict) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_logs (session_id, data) VALUES (%s, %s)
                ON CONFLICT (session_id) DO UPDATE SET data = EXCLUDED.data
                """,
                [session_id, self._psycopg2.extras.Json(log)],
            )
            conn.commit()


def get_storage(database_url: str | None):
    """Pick the storage backend for this run: Postgres if DATABASE_URL is set, else local file."""
    if database_url:
        return PostgresStorage(database_url)
    return LocalFileStorage()
