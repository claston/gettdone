"""Narrow DB interface over the worker's existing pool; no authentication/bootstrap side effects."""


class WorkerOrganizationDb:
    def __init__(self, access):
        self.access = access

    def connect(self):
        return self.access._connect()

    def execute(self, conn, query, params=()):
        return self.access._execute(conn, query, params)

    def fetchone(self, conn, query, params=()):
        return self.access._fetchone(conn, query, params)

    def fetchall(self, conn, query, params=()):
        cursor = self.execute(conn, query, params)
        try:
            return cursor.fetchall()
        finally:
            cursor.close()
