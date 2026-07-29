from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from core.config import get_settings


def get_psycopg_database_url() -> str:
    database_url = get_settings().database_url
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return database_url


@contextmanager
def phase0_postgres_checkpointer(*, setup: bool = False) -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(get_psycopg_database_url()) as checkpointer:
        if setup:
            checkpointer.setup()
        yield checkpointer
