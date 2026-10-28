"""Test setup: a throwaway SQLite file, set before anything imports app.db."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

_DB = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
# Never call a real provider from the default test run
os.environ["GROQ_API_KEY"] = os.environ.get("LIVE_GROQ_API_KEY", "")

from sqlmodel import SQLModel  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import engine, init_db, new_session  # noqa: E402
from app.tools import ToolContext  # noqa: E402

# A Wednesday morning
NOW = datetime(2026, 10, 28, 9, 10)


@pytest.fixture(autouse=True)
def clean_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def session():
    with new_session() as s:
        yield s


@pytest.fixture
def ctx(session):
    return ToolContext(session, NOW)
