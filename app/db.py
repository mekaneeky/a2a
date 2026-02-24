from __future__ import annotations

from collections.abc import Generator

from sqlmodel import SQLModel, Session, create_engine


def make_engine(db_url: str):
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, echo=False, connect_args=connect_args)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_scope(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
