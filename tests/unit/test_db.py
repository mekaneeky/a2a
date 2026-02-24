import pytest
from sqlmodel import SQLModel

from app.db import init_db, make_engine, session_scope
from app.models import Agent


def test_make_engine_for_sqlite_and_init_db() -> None:
    engine = make_engine("sqlite://")
    init_db(engine)
    assert Agent.__table__.name in SQLModel.metadata.tables


def test_make_engine_non_sqlite_uses_empty_connect_args(monkeypatch) -> None:
    captured = {}

    def fake_create_engine(db_url: str, echo: bool, connect_args: dict):
        captured["db_url"] = db_url
        captured["echo"] = echo
        captured["connect_args"] = connect_args
        return "engine"

    monkeypatch.setattr("app.db.create_engine", fake_create_engine)

    engine = make_engine("postgresql://example")

    assert engine == "engine"
    assert captured["db_url"] == "postgresql://example"
    assert captured["echo"] is False
    assert captured["connect_args"] == {}


def test_session_scope_yields_session() -> None:
    engine = make_engine("sqlite://")
    init_db(engine)

    scope = session_scope(engine)
    session = next(scope)
    assert session is not None
    with pytest.raises(StopIteration):
        next(scope)
