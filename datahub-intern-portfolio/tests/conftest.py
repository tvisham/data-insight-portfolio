"""Shared pytest fixtures.

Every test in this suite works against a fresh temporary SQLite file
so tests never contaminate each other. We patch `settings.db_path` for
the duration of the test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    # Override before any module reads `settings`.
    monkeypatch.setenv("DATAHUB_DB_PATH", str(db_path))
    # Force reload of settings so the new path is picked up.
    from importlib import reload

    from src import config as cfg
    reload(cfg)

    # Also re-patch other modules that captured the old settings.
    from src.db import database as db_module
    reload(db_module)

    db_module.init_schema()
    yield db_path


@pytest.fixture(scope="session", autouse=True)
def _ensure_repo_root_on_syspath():
    # `python -m pytest` from the repo root already handles this,
    # but if someone runs pytest from a nested dir it's a common trap.
    import sys
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    yield
