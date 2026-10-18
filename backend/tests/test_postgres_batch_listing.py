import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.adapters.conversion.postgres_batches import PostgresConversionBatchRepository


def test_postgres_listing_query_limits_owner_headers_before_aggregating_metadata():
    now = datetime.now(timezone.utc)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE conversion_batches (batch_id TEXT, status TEXT, files_count INTEGER, "
               "created_at TEXT, expires_at TEXT, identity_type TEXT, identity_id TEXT)")
    db.execute("CREATE TABLE conversion_jobs (batch_id TEXT, job_id TEXT, status TEXT, expires_at TEXT)")
    for number, owner, timestamp in [(1, "owner", now), (2, "owner", now), (3, "other", now), (4, "owner", now - timedelta(days=2))]:
        expires = timestamp + timedelta(hours=24)
        db.execute("INSERT INTO conversion_batches VALUES (?, 'completed', 1, ?, ?, 'user', ?)",
                   (f"batch_{number}", timestamp.isoformat(), expires.isoformat(), owner))
        db.execute("INSERT INTO conversion_jobs VALUES (?, ?, 'completed', ?)",
                   (f"batch_{number}", f"job_{number}", expires.isoformat()))
    queries = []

    class Cursor:
        def __enter__(self):
            self.cursor = db.cursor()
            return self

        def __exit__(self, *args):
            self.cursor.close()

        def execute(self, query, params):
            queries.append(query)
            self.cursor.execute(query.replace('"public".', '').replace("%s", "?"),
                                tuple(value.isoformat() if isinstance(value, datetime) else value for value in params))

        def fetchall(self):
            return [{key: datetime.fromisoformat(value) if key.endswith("_at") and value else value
                     for key, value in dict(row).items()} for row in self.cursor.fetchall()]

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    repository = PostgresConversionBatchRepository(database_url="", connection_factory=connect, clock=lambda: now)
    try:
        first = repository.list_for_owner(identity_type="user", identity_id="owner", limit=2)
        assert [item.batch_id for item in first] == ["batch_2", "batch_1"]
        last = repository.list_for_owner(identity_type="user", identity_id="owner", limit=2,
                                         before=(first[-1].created_at, first[-1].batch_id))
        assert [item.batch_id for item in last] == ["batch_4"]
        assert last[0].first_job_id is None
        assert last[0].expires_at < now
        assert all(item.completed_count == item.files_count == 1 for item in first + last)
        assert all("result_payload" not in query and "document" not in query for query in queries)
        assert all("LIMIT" in query and "identity_type = %s AND identity_id = %s" in query for query in queries)
    finally:
        db.close()
