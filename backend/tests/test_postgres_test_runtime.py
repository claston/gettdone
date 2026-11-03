import os
from pathlib import Path

import pytest

from tests.postgres_runtime import migrate_test_schema


@pytest.mark.parametrize("dsn,schema", [
    ("postgresql://user@database.example/ofx69_test", "ofx69_test_" + "a" * 32),
    ("postgresql://user@127.0.0.1/production", "ofx69_test_" + "a" * 32),
    ("postgresql://user@127.0.0.1/ofx69_test?host=remote", "ofx69_test_" + "a" * 32),
    ("postgresql://user@127.0.0.1/ofx69_test", "public"),
])
def test_in_process_migrations_refuse_non_disposable_targets(dsn, schema):
    with pytest.raises(ValueError, match="disposable"):
        migrate_test_schema(dsn, schema)


@pytest.mark.parametrize("fail", [False, True])
def test_migrations_restore_environment_and_keep_real_upgrade_semantics(monkeypatch, fail):
    from alembic import command

    monkeypatch.setenv("DATABASE_URL", "original-url")
    monkeypatch.setenv("DATABASE_SCHEMA", "original-schema")
    target = "postgresql://postgres@127.0.0.1/ofx69_test"
    schema = "ofx69_test_" + "b" * 32
    seen = []

    def upgrade(config, revision):
        seen.append(revision)
        assert config.config_file_name is None  # Do not reconfigure the test logger.
        assert Path(config.get_main_option("script_location")).name == "alembic"
        assert os.environ["DATABASE_URL"] == target
        assert os.environ["DATABASE_SCHEMA"] == schema
        if fail:
            raise RuntimeError("migration failed")

    monkeypatch.setattr(command, "upgrade", upgrade)
    if fail:
        with pytest.raises(RuntimeError, match="migration failed"):
            migrate_test_schema(target, schema)
    else:
        migrate_test_schema(target, schema)
    assert seen == ["head"]
    assert os.environ["DATABASE_URL"] == "original-url"
    assert os.environ["DATABASE_SCHEMA"] == "original-schema"
