"""Corner-case coverage for storage.py's LocalFileStorage, PostgresStorage, and get_storage()."""

import sys
import types

import pytest

import storage

# ─── LocalFileStorage ───────────────────────────────────────────────────────


def test_load_checkpoint_returns_empty_shape_when_no_file(isolated_storage_paths):
    s = storage.LocalFileStorage()
    assert s.load_checkpoint() == {"used_ids": [], "sessions": []}


def test_load_checkpoint_calls_are_independent_copies(isolated_storage_paths):
    """Regression test: load_checkpoint() must not hand back references into
    the shared EMPTY_CHECKPOINT constant. Mutating one call's result must not
    leak into the next call's result or into EMPTY_CHECKPOINT itself."""
    s = storage.LocalFileStorage()

    first = s.load_checkpoint()
    first["used_ids"].append(999)
    first["sessions"].append({"fake": True})

    second = s.load_checkpoint()
    assert second["used_ids"] == []
    assert second["sessions"] == []
    assert storage.EMPTY_CHECKPOINT == {"used_ids": [], "sessions": []}


def test_save_checkpoint_entry_persists_id(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.save_checkpoint_entry(1)
    assert s.load_checkpoint()["used_ids"] == [1]


def test_save_checkpoint_entry_is_idempotent_for_duplicate_ids(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.save_checkpoint_entry(1)
    s.save_checkpoint_entry(1)
    s.save_checkpoint_entry(2)
    assert s.load_checkpoint()["used_ids"] == [1, 2]


def test_finalize_checkpoint_appends_session_and_preserves_used_ids(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.save_checkpoint_entry(7)
    s.finalize_checkpoint({"session_id": "abc", "correct": 3})

    data = s.load_checkpoint()
    assert data["used_ids"] == [7]
    assert data["sessions"] == [{"session_id": "abc", "correct": 3}]


def test_reset_checkpoint_removes_file_and_clears_state(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.save_checkpoint_entry(1)
    assert storage.CHECKPOINT_PATH.exists()

    s.reset_checkpoint()

    assert not storage.CHECKPOINT_PATH.exists()
    assert s.load_checkpoint() == {"used_ids": [], "sessions": []}


def test_reset_checkpoint_is_a_noop_when_no_file_exists(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.reset_checkpoint()  # should not raise


def test_write_and_read_session_log_round_trips(isolated_storage_paths):
    s = storage.LocalFileStorage()
    log = {"session_id": "sess1", "correct": 5, "total_answered": 10}
    s.write_session_log("sess1", log)

    logs = s.read_all_session_logs()
    assert logs == [log]


def test_write_session_log_overwrites_same_session_id(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.write_session_log("sess1", {"correct": 1})
    s.write_session_log("sess1", {"correct": 9})

    logs = s.read_all_session_logs()
    assert logs == [{"correct": 9}]


def test_read_all_session_logs_empty_when_no_logs_written(isolated_storage_paths):
    s = storage.LocalFileStorage()
    assert s.read_all_session_logs() == []


def test_read_all_session_logs_sorted_by_filename(isolated_storage_paths):
    s = storage.LocalFileStorage()
    s.write_session_log("20240102_000000", {"session_id": "b"})
    s.write_session_log("20240101_000000", {"session_id": "a"})

    logs = s.read_all_session_logs()
    assert [log["session_id"] for log in logs] == ["a", "b"]


def test_write_session_log_preserves_unicode(isolated_storage_paths):
    s = storage.LocalFileStorage()
    log = {"session_id": "unicode", "question": "What does ✅ mean in the explanation?"}
    s.write_session_log("unicode", log)

    raw = (storage.SESSION_LOGS_DIR / "unicode.json").read_text(encoding="utf-8")
    assert "✅" in raw
    assert s.read_all_session_logs() == [log]


# ─── PostgresStorage ────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.executed = []
        self._result = None

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))
        if "SELECT data FROM checkpoint_state" in query:
            self._result = self.conn.checkpoint_row
        elif "SELECT data FROM session_logs" in query:
            self._result = self.conn.session_log_rows
        elif "INSERT INTO checkpoint_state" in query:
            self.conn.checkpoint_row = (params[0].data,)
        elif "INSERT INTO session_logs" in query:
            self.conn.session_log_rows.append((params[1].data,))
        elif "DELETE FROM checkpoint_state" in query:
            self.conn.checkpoint_row = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.checkpoint_row = None
        self.session_log_rows = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeJson:
    """Mimics psycopg2.extras.Json: wraps a python value for storage."""

    def __init__(self, data):
        self.data = data


@pytest.fixture
def fake_psycopg2(monkeypatch):
    fake_conn = _FakeConn()

    fake_module = types.ModuleType("psycopg2")
    fake_module.connect = lambda *_a, **_kw: fake_conn
    fake_extras = types.ModuleType("psycopg2.extras")
    fake_extras.Json = _FakeJson
    fake_module.extras = fake_extras

    monkeypatch.setitem(sys.modules, "psycopg2", fake_module)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)
    return fake_conn


def test_postgres_storage_raises_helpful_error_without_psycopg2(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg2", None)  # forces ImportError on `import psycopg2`
    with pytest.raises(RuntimeError, match="psycopg2"):
        storage.PostgresStorage("postgres://fake")


def test_postgres_storage_ensures_schema_on_init(fake_psycopg2):
    storage.PostgresStorage("postgres://fake")
    assert fake_psycopg2.committed is True


def test_postgres_storage_load_checkpoint_empty_when_no_row(fake_psycopg2):
    s = storage.PostgresStorage("postgres://fake")
    assert s.load_checkpoint() == {"used_ids": [], "sessions": []}


def test_postgres_storage_save_checkpoint_entry_dedupes(fake_psycopg2):
    s = storage.PostgresStorage("postgres://fake")
    s.save_checkpoint_entry(1)
    s.save_checkpoint_entry(1)
    s.save_checkpoint_entry(2)
    assert s.load_checkpoint()["used_ids"] == [1, 2]


def test_postgres_storage_reset_checkpoint_clears_row(fake_psycopg2):
    s = storage.PostgresStorage("postgres://fake")
    s.save_checkpoint_entry(1)
    s.reset_checkpoint()
    assert s.load_checkpoint() == {"used_ids": [], "sessions": []}


def test_postgres_storage_session_log_round_trip(fake_psycopg2):
    s = storage.PostgresStorage("postgres://fake")
    s.write_session_log("sess1", {"correct": 4})
    assert s.read_all_session_logs() == [{"correct": 4}]


# ─── get_storage() dispatch ─────────────────────────────────────────────────


def test_get_storage_returns_local_when_no_database_url(isolated_storage_paths):
    s = storage.get_storage(None)
    assert isinstance(s, storage.LocalFileStorage)


def test_get_storage_returns_local_when_database_url_is_empty_string(isolated_storage_paths):
    s = storage.get_storage("")
    assert isinstance(s, storage.LocalFileStorage)


def test_get_storage_returns_postgres_when_database_url_set(fake_psycopg2):
    s = storage.get_storage("postgres://fake")
    assert isinstance(s, storage.PostgresStorage)
