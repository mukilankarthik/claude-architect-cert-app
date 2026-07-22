import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def isolated_storage_paths(tmp_path, monkeypatch):
    """Point storage.py's module-level paths at a scratch dir so tests never
    touch the real checkpoint.json / session_logs in the repo.

    autouse=True is deliberate: a test that forgets to request this fixture
    must never be able to fall through to the repo's real checkpoint.json /
    session_logs/ (that happened once during development of this suite)."""
    import storage

    monkeypatch.setattr(storage, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")
    monkeypatch.setattr(storage, "SESSION_LOGS_DIR", tmp_path / "session_logs")

    # app.py's STORAGE is built by a @st.cache_resource function, which caches
    # process-wide — without clearing it, a later test's AppTest instance would
    # reuse an earlier test's LocalFileStorage (and its already-mkdir'd, now
    # stale, session_logs dir) instead of building a fresh one for this tmp_path.
    import streamlit as st

    st.cache_resource.clear()

    return storage
