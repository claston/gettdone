"""The image boundary checks real SQLite availability, not scanner exceptions."""
from types import SimpleNamespace

import pytest

from app.workers import worker_image_contract as contract


@pytest.mark.parametrize("module", ["sqlite3", "_sqlite3"])
def test_image_contract_rejects_either_sqlite_python_module(module, monkeypatch):
    monkeypatch.setattr(contract.util, "find_spec", lambda name: SimpleNamespace() if name == module else None)
    with pytest.raises(RuntimeError, match="SQLite"):
        contract.verify_no_sqlite_runtime()


def test_image_contract_rejects_library_even_without_python_bindings(monkeypatch):
    monkeypatch.setattr(contract.util, "find_spec", lambda name: None)
    monkeypatch.setattr(contract.ctypes, "CDLL", lambda name: SimpleNamespace())
    with pytest.raises(RuntimeError, match="SQLite shared library"):
        contract.verify_no_sqlite_runtime()


def test_image_contract_accepts_runtime_without_sqlite(monkeypatch):
    monkeypatch.setattr(contract.util, "find_spec", lambda name: None)

    def absent(name):
        assert name == "libsqlite3.so.0"
        raise OSError("not present in the worker runtime")

    monkeypatch.setattr(contract.ctypes, "CDLL", absent)
    contract.verify_no_sqlite_runtime()
