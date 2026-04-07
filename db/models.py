"""
db/models.py – SQLAlchemy 2.0 Declarative models.
All types use the Mapped[] annotation style (PEP 681 / SA 2.0).
"""
from __future__ import annotations

import enum
from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey,
    String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ──────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────
class UserRole(str, enum.Enum):
    lead = "lead"
    developer = "developer"


class ContractStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    completed = "completed"


class TaskStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"


# ──────────────────────────────────────────────
# Tables
# ──────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False), default=UserRole.developer, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Where to send DM notifications (could be tg_id or group chat id)
    notification_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    aliases: Mapped[List["UserAlias"]] = relationship(
        "UserAlias", back_populates="user", cascade="all, delete-orphan"
    )
    contracts: Mapped[List["DailyContract"]] = relationship(
        "DailyContract", back_populates="user"
    )
    intraday_updates: Mapped[List["IntradayUpdate"]] = relationship(
        "IntradayUpdate", back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User tg_id={self.tg_id} role={self.role}>"


class UserAlias(Base):
    """Raw name variants that appear in meeting transcripts."""

    __tablename__ = "user_aliases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    user: Mapped["User"] = relationship("User", back_populates="aliases")

    def __repr__(self) -> str:
        return f"<UserAlias alias={self.alias!r}>"


class DailyContract(Base):
    """One developer's commitment for one day."""

    __tablename__ = "daily_contracts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_date: Mapped[date] = mapped_column(nullable=False, index=True)
    meeting_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, native_enum=False), default=ContractStatus.draft, nullable=False
    )

    # SLA tracking
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_ping_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="contracts")
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="contract", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<DailyContract user_id={self.user_id} date={self.target_date} status={self.status}>"


class Task(Base):
    """A single atomic work item within a DailyContract."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("daily_contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    time_estimate: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False), default=TaskStatus.todo, nullable=False
    )
    blocker_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    contract: Mapped["DailyContract"] = relationship("DailyContract", back_populates="tasks")

    def __repr__(self) -> str:
        return f"<Task id={self.id} status={self.status}>"


class IntradayUpdate(Base):
    """Raw developer text messages – append-only audit log."""

    __tablename__ = "intraday_updates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("daily_contracts.id", ondelete="SET NULL"), nullable=True
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="intraday_updates")

    def __repr__(self) -> str:
        return f"<IntradayUpdate user_id={self.user_id} at={self.created_at}>"
