"""`.env` is read only when asked, and never beats what is already exported."""

from __future__ import annotations

import os

from teacup_run import load_env


def test_it_loads_keys_from_a_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EXAMPLE_KEY=from-file\n# a comment\n\n")
    monkeypatch.delenv("EXAMPLE_KEY", raising=False)

    used = load_env(tmp_path / ".env")

    assert used == tmp_path / ".env"
    assert os.environ["EXAMPLE_KEY"] == "from-file"


def test_an_exported_variable_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EXAMPLE_KEY=from-file\n")
    monkeypatch.setenv("EXAMPLE_KEY", "from-shell")

    load_env(tmp_path / ".env")

    assert os.environ["EXAMPLE_KEY"] == "from-shell"


def test_placeholders_are_ignored(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EXAMPLE_KEY=sk-...\nOTHER_KEY=\n")
    monkeypatch.delenv("EXAMPLE_KEY", raising=False)

    load_env(tmp_path / ".env")

    assert "EXAMPLE_KEY" not in os.environ


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path / "nope.env") is None
