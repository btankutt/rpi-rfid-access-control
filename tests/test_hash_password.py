"""Tests for scripts/hash_password.py."""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def _ensure_scripts_on_path():
    yield
    # Best-effort cleanup, not strictly necessary across the test session.


def _load_module():
    if "hash_password" in sys.modules:
        del sys.modules["hash_password"]
    return importlib.import_module("hash_password")


class TestHashPasswordScript:
    def test_matching_passwords_emit_bcrypt_hash(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        responses = iter(["correct horse", "correct horse"])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(responses))

        module = _load_module()
        module.main()

        out = capsys.readouterr().out.strip()
        assert out.startswith(("$2a$", "$2b$", "$2y$"))

    def test_mismatch_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch):
        responses = iter(["a", "b"])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(responses))
        # Swallow stderr so it doesn't pollute test output
        monkeypatch.setattr(sys, "stderr", io.StringIO())

        module = _load_module()
        with pytest.raises(SystemExit) as exc:
            module.main()
        assert exc.value.code == 1

    def test_empty_password_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch):
        responses = iter(["", ""])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(responses))
        monkeypatch.setattr(sys, "stderr", io.StringIO())

        module = _load_module()
        with pytest.raises(SystemExit) as exc:
            module.main()
        assert exc.value.code == 1
