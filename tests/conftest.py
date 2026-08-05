"""Shared test fixtures: repo root + raw-loan fixture loader."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def raw_rows() -> list[dict]:
    import json

    return json.loads((FIXTURES / "loans_raw.json").read_text(encoding="utf-8"))
