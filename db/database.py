"""
db/database.py – Async SQLAlchemy engine & session factory.
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base

load_dotenv()

DB_URL: str = os.getenv("DB_URL", "sqlite+aiosqlite:///./scrum_master.db")

engine: AsyncEngine = create_async_engine(
    DB_URL,
    echo=False,            # Flip to True for SQL query logging during dev
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# Columns added after the initial schema; ALTER TABLE for existing SQLite DBs.
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "daily_contracts": {
        "sla_ping_count": "INTEGER NOT NULL DEFAULT 0",
        "sla_ping_at": "DATETIME",
    },
}


async def init_db() -> None:
    """Create all tables (idempotent – safe to call on every startup)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_column_migrations)


def _apply_column_migrations(sync_conn) -> None:
    """Add newly-introduced columns to pre-existing databases."""
    from sqlalchemy import text

    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {
            row[1]
            for row in sync_conn.execute(text(f"PRAGMA table_info({table})"))
        }
        for col, ddl in columns.items():
            if col not in existing:
                sync_conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI / aiogram DI-friendly session generator."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
