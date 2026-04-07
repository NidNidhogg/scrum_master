"""
db/repository.py – Data Access Layer.
All database I/O is isolated here; handlers/workers never touch raw SQL.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    ContractStatus,
    DailyContract,
    IntradayUpdate,
    Task,
    TaskStatus,
    User,
    UserAlias,
    UserRole,
)


# ──────────────────────────────────────────────
# User CRUD
# ──────────────────────────────────────────────

async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    tg_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    role: UserRole = UserRole.developer,
) -> tuple[User, bool]:
    """Returns (user, created)."""
    user = await get_user_by_tg_id(session, tg_id)
    if user:
        return user, False
    user = User(
        tg_id=tg_id,
        username=username,
        full_name=full_name,
        role=role,
        notification_chat_id=tg_id,
    )
    session.add(user)
    await session.flush()
    return user, True


async def get_lead_users(session: AsyncSession) -> Sequence[User]:
    result = await session.execute(
        select(User).where(User.role == UserRole.lead, User.is_active == True)
    )
    return result.scalars().all()


async def get_active_developers(session: AsyncSession) -> Sequence[User]:
    result = await session.execute(
        select(User).where(User.role == UserRole.developer, User.is_active == True)
    )
    return result.scalars().all()


# ──────────────────────────────────────────────
# Alias resolution (Entity Resolution)
# ──────────────────────────────────────────────

async def resolve_alias(session: AsyncSession, alias: str) -> Optional[User]:
    """Map a transcript name to a User. Case-insensitive contains match."""
    result = await session.execute(
        select(UserAlias)
        .where(UserAlias.alias.ilike(alias.strip()))
        .options(selectinload(UserAlias.user))
    )
    row = result.scalar_one_or_none()
    return row.user if row else None


async def add_alias(session: AsyncSession, user_id: int, alias: str) -> UserAlias:
    obj = UserAlias(user_id=user_id, alias=alias.strip().lower())
    session.add(obj)
    await session.flush()
    return obj


async def get_all_aliases(session: AsyncSession) -> Sequence[UserAlias]:
    result = await session.execute(
        select(UserAlias).options(selectinload(UserAlias.user))
    )
    return result.scalars().all()


# ──────────────────────────────────────────────
# DailyContract CRUD
# ──────────────────────────────────────────────

async def get_contract_today(
    session: AsyncSession, user_id: int, target_date: Optional[date] = None
) -> Optional[DailyContract]:
    target_date = target_date or date.today()
    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.user_id == user_id, DailyContract.target_date == target_date)
        .options(selectinload(DailyContract.tasks))
    )
    return result.scalar_one_or_none()


async def get_or_create_contract(
    session: AsyncSession,
    user_id: int,
    target_date: Optional[date] = None,
    meeting_context: Optional[str] = None,
) -> tuple[DailyContract, bool]:
    """Append Mode: returns existing contract if present."""
    target_date = target_date or date.today()
    contract = await get_contract_today(session, user_id, target_date)
    if contract:
        if meeting_context and not contract.meeting_context:
            contract.meeting_context = meeting_context
        return contract, False
    contract = DailyContract(
        user_id=user_id,
        target_date=target_date,
        meeting_context=meeting_context,
        status=ContractStatus.draft,
    )
    session.add(contract)
    await session.flush()
    return contract, True


async def activate_contract(session: AsyncSession, contract_id: int) -> None:
    await session.execute(
        update(DailyContract)
        .where(DailyContract.id == contract_id)
        .values(status=ContractStatus.active, started_at=datetime.now(tz=timezone.utc))
    )


async def complete_contract(session: AsyncSession, contract_id: int) -> None:
    await session.execute(
        update(DailyContract)
        .where(DailyContract.id == contract_id)
        .values(status=ContractStatus.completed)
    )


async def activate_daily_plans(
    session: AsyncSession, target_date: Optional[date] = None
) -> Sequence[DailyContract]:
    """
    Transitions all 'draft' contracts for the given date to 'active'.
    Sets 'started_at' to current time.
    """
    target_date = target_date or date.today()
    result = await session.execute(
        select(DailyContract)
        .where(
            DailyContract.target_date == target_date,
            DailyContract.status == ContractStatus.draft,
        )
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    contracts = result.scalars().all()

    now = datetime.now(tz=timezone.utc)
    for contract in contracts:
        contract.status = ContractStatus.active
        contract.started_at = now
        # When Lead bulk-activates, we treat them as ready-to-work
        for task in contract.tasks:
            task.status = TaskStatus.in_progress

    await session.flush()
    return contracts


async def discard_daily_plans(
    session: AsyncSession, target_date: Optional[date] = None
) -> int:
    """
    Deletes all 'draft' contracts for a given date.
    Returns count of deleted contracts.
    """
    from sqlalchemy import delete
    target_date = target_date or date.today()
    
    # We need to fetch count first or rely on rowcount
    result = await session.execute(
        select(DailyContract).where(
            DailyContract.target_date == target_date,
            DailyContract.status == ContractStatus.draft,
        )
    )
    count = len(result.scalars().all())
    
    await session.execute(
        delete(DailyContract)
        .where(
            DailyContract.target_date == target_date,
            DailyContract.status == ContractStatus.draft,
        )
    )
    return count


async def get_contracts_for_date(
    session: AsyncSession, target_date: Optional[date] = None
) -> Sequence[DailyContract]:
    target_date = target_date or date.today()
    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.target_date == target_date)
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    return result.scalars().all()


# ──────────────────────────────────────────────
# SLA
# ──────────────────────────────────────────────

async def get_overdue_active_contracts(
    session: AsyncSession, threshold_hours: int = 4
) -> Sequence[DailyContract]:
    """Returns active contracts where elapsed time >= threshold and SLA ping not yet sent."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=threshold_hours)
    result = await session.execute(
        select(DailyContract)
        .where(
            DailyContract.status == ContractStatus.active,
            DailyContract.sla_ping_sent == False,
            DailyContract.started_at <= cutoff,
        )
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    return result.scalars().all()


async def mark_sla_ping_sent(session: AsyncSession, contract_id: int) -> None:
    await session.execute(
        update(DailyContract)
        .where(DailyContract.id == contract_id)
        .values(sla_ping_sent=True)
    )


# ──────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────

async def add_task(
    session: AsyncSession,
    contract_id: int,
    description: str,
    time_estimate: Optional[str] = None,
) -> Task:
    task = Task(
        contract_id=contract_id,
        description=description,
        time_estimate=time_estimate,
        status=TaskStatus.todo,
    )
    session.add(task)
    await session.flush()
    return task


async def update_task_status(
    session: AsyncSession,
    task_id: int,
    status: TaskStatus,
    blocker_note: Optional[str] = None,
) -> None:
    values: dict = {"status": status}
    if blocker_note is not None:
        values["blocker_note"] = blocker_note
    await session.execute(update(Task).where(Task.id == task_id).values(**values))


async def get_tasks_by_contract(
    session: AsyncSession, contract_id: int
) -> Sequence[Task]:
    result = await session.execute(
        select(Task).where(Task.contract_id == contract_id)
    )
    return result.scalars().all()


# ──────────────────────────────────────────────
# IntradayUpdates
# ──────────────────────────────────────────────

async def log_intraday_update(
    session: AsyncSession,
    user_id: int,
    raw_text: str,
    contract_id: Optional[int] = None,
) -> IntradayUpdate:
    update_obj = IntradayUpdate(
        user_id=user_id, raw_text=raw_text, contract_id=contract_id
    )
    session.add(update_obj)
    await session.flush()
    return update_obj


async def get_intraday_updates_today(
    session: AsyncSession, target_date: Optional[date] = None
) -> Sequence[IntradayUpdate]:
    from sqlalchemy import cast, Date
    target_date = target_date or date.today()
    result = await session.execute(
        select(IntradayUpdate)
        .where(cast(IntradayUpdate.created_at, Date) == target_date)
        .options(selectinload(IntradayUpdate.user))
    )
    return result.scalars().all()
