import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.adapters.conversion.postgres_batches import PostgresConversionBatchRepository


def test_file_listing_paginates_owner_metadata_without_loading_financial_payloads():
    now = datetime.now(timezone.utc)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE conversion_batches (batch_id TEXT, identity_type TEXT, identity_id TEXT)")
    db.execute("CREATE TABLE conversion_jobs (job_id TEXT, batch_id TEXT, status TEXT, created_at TEXT, "
               "expires_at TEXT, document TEXT, result_payload TEXT)")
    for number, owner in [(1, "owner"), (2, "owner"), (3, "other")]:
        db.execute("INSERT INTO conversion_batches VALUES (?, 'user', ?)", (f"batch_{number}", owner))
        db.execute("INSERT INTO conversion_jobs VALUES (?, ?, 'completed', ?, ?, ?, ?)",
            (f"job_{number}", f"batch_{number}", now.isoformat(), (now + timedelta(hours=24)).isoformat(),
             json.dumps({"filename": f"extrato-{number}.pdf", "storage_key": "private-storage-reference"}),
             "PRIVATE FINANCIAL PAYLOAD MUST NOT BE FETCHED"))
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
            return [{key: datetime.fromisoformat(value) if key.endswith("_at") else value
                     for key, value in dict(row).items()} for row in self.cursor.fetchall()]

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    repository = PostgresConversionBatchRepository(database_url="", connection_factory=connect)
    try:
        first = repository.list_files_for_owner(identity_type="user", identity_id="owner", limit=1)
        assert len(first) == 1 and first[0].job_id == "job_2"
        second = repository.list_files_for_owner(identity_type="user", identity_id="owner", limit=1,
                                                 before=(first[0].created_at, first[0].job_id))
        assert len(second) == 1 and second[0].filename == "extrato-1.pdf"
        assert all("result_payload" not in query and "storage_key" not in query and "SELECT *" not in query for query in queries)
        assert all("b.identity_type = %s AND b.identity_id = %s" in query and "LIMIT %s" in query for query in queries)
    finally:
        db.close()
