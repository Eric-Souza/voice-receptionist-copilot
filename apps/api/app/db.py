from pathlib import Path

from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registers the tables on SQLModel.metadata)
from app.config import get_settings


def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    # Tools run on the event loop's worker threads, so SQLite must accept other threads
    return create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})


engine = make_engine()


def init_db(bind=None) -> None:
    SQLModel.metadata.create_all(bind or engine)


def new_session(bind=None) -> Session:
    return Session(bind or engine, expire_on_commit=False)
