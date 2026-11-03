"""Run the actual migration chain without a new interpreter for every fixture.

Each integration still owns a fresh schema. Upgrade/rollback CLI tests continue
to launch subprocesses, and no production migration code is patched or skipped.
"""
import os
import re
from pathlib import Path
from threading import RLock
from unittest.mock import patch
from urllib.parse import urlparse

_migration_lock = RLock()


def migrate_test_schema(dsn, schema):
    target = urlparse(dsn)
    if (target.scheme not in {"postgres", "postgresql"} or target.hostname not in {"localhost", "127.0.0.1"}
            or target.path not in {"/ofx69_test", "/ofx24_test"} or target.query or target.fragment
            or not re.fullmatch(r"ofx69_test_[a-f0-9]{32}", schema)):
        raise ValueError("Migrations require a disposable local test database and an isolated test schema")

    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    # env.py reads the explicit environment, just as in the subprocess version.
    # Config without an ini file preserves logging configured by pytest.
    with _migration_lock, patch.dict(os.environ, {"DATABASE_URL": dsn, "DATABASE_SCHEMA": schema}):
        command.upgrade(config, "head")
