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


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(
        select(User).where(User.username.ilike(username.strip().lstrip("@")))
    )
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


async def get_all_users(session: AsyncSession) -> Sequence[User]:
    """Fetches all registered users, including Leads."""
    result = await session.execute(select(User))
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


async def upsert_alias(session: AsyncSession, user_id: int, alias: str) -> tuple[UserAlias, bool]:
    """Returns (alias_obj, created). Updates user_id if alias exists."""
    clean_alias = alias.strip().lower()
    
    # Check if exists
    result = await session.execute(
        select(UserAlias).where(UserAlias.alias == clean_alias)
    )
    obj = result.scalar_one_or_none()
    
    if obj:
        obj.user_id = user_id
        await session.flush()
        return obj, False
    
    obj = UserAlias(user_id=user_id, alias=clean_alias)
    session.add(obj)
    await session.flush()
    return obj, True


async def get_all_aliases(session: AsyncSession) -> Sequence[UserAlias]:
    result = await session.execute(
        select(UserAlias).options(selectinload(UserAlias.user))
    )
    return result.scalars().all()


# ──────────────────────────────────────────────
# DailyContract CRUD
# ──────────────────────────────────────────────

async def get_contract_today(
    session: AsyncSession, 
    user_id: Optional[int] = None, 
    developer_alias: Optional[str] = None,
    target_date: Optional[date] = None
) -> Optional[DailyContract]:
    target_date = target_date or date.today()
    query = select(DailyContract).where(DailyContract.target_date == target_date)
    
    if user_id:
        query = query.where(DailyContract.user_id == user_id)
    elif developer_alias:
        query = query.where(DailyContract.developer_alias == developer_alias)
    else:
        return None
        
    result = await session.execute(query.options(selectinload(DailyContract.tasks)))
    return result.scalar_one_or_none()


async def get_or_create_contract(
    session: AsyncSession,
    user_id: Optional[int] = None,
    developer_alias: Optional[str] = None,
    target_date: Optional[date] = None,
    meeting_context: Optional[str] = None,
    lead_tg_id: Optional[int] = None,
) -> tuple[DailyContract, bool]:
    """Returns (contract, created)."""
    target_date = target_date or date.today()
    contract = await get_contract_today(
        session, user_id=user_id, developer_alias=developer_alias, target_date=target_date
    )
    
    lead_user = None
    if lead_tg_id:
        lead_user = await get_user_by_tg_id(session, lead_tg_id)

    if contract:
        if meeting_context and not contract.meeting_context:
            contract.meeting_context = meeting_context
        # Update user_id if it was NULL but now we have one
        if user_id and not contract.user_id:
            contract.user_id = user_id
        # Link Lead if not already linked
        if lead_user and not contract.lead_id:
            contract.lead_id = lead_user.id
        return contract, False
        
    contract = DailyContract(
        user_id=user_id,
        lead_id=lead_user.id if lead_user else None,
        developer_alias=developer_alias,
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


async def get_unfinished_tasks_before(
    session: AsyncSession, target_date: date
) -> Sequence[Task]:
    """Finds all tasks from contracts before target_date that are NOT done/blocked."""
    result = await session.execute(
        select(Task)
        .join(DailyContract)
        .where(
            DailyContract.target_date < target_date,
            Task.status != TaskStatus.done,
        )
        .options(selectinload(Task.contract))
    )
    return result.scalars().all()


async def discard_daily_plans(
    session: AsyncSession, target_date: Optional[date] = None
) -> int:
    """
    PERSISTENT BACKLOG CLEANUP: 
    1. Carries over unfinished tasks from previous days to target_date.
    2. Deletes only 'done' tasks for the target_date (to allow re-runs).
    3. Returns count of deleted tasks.
    """
    from sqlalchemy import delete
    target_date = target_date or date.today()
    
    # 1. Carry over unfinished tasks from the past
    unfinished_past = await get_unfinished_tasks_before(session, target_date)
    for task in unfinished_past:
        # Move to a contract on target_date
        new_contract, _ = await get_or_create_contract(
            session, 
            user_id=task.contract.user_id,
            developer_alias=task.contract.developer_alias,
            target_date=target_date
        )
        task.contract_id = new_contract.id
        session.add(task)

    # 2. DELETE 'done' tasks for this date (allows lead to re-run transcript without losing carry-overs)
    result = await session.execute(
        select(DailyContract.id).where(DailyContract.target_date == target_date)
    )
    contract_ids = [row[0] for row in result.all()]
    
    deleted_count = 0
    if contract_ids:
        # Delete only DONE tasks
        res = await session.execute(
            delete(Task).where(
                Task.contract_id.in_(contract_ids),
                Task.status == TaskStatus.done
            )
        )
        deleted_count = res.rowcount
        
        # We DON'T delete the contracts themselves here because they might hold carry-over tasks
        # the lead wants to keep. Empty contracts will be cleaned up by get_contracts_for_date filters if needed,
        # or we just keep them.
    
    await session.commit()
    return deleted_count


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
    """
    Returns active contracts where user hasn't approved or finished a task for > thresh.
    Priority: Max(started_at, Max(task.updated_at for done tasks))
    """
    # 1. Fetch all active contracts for today where ping hasn't been sent yet
    result = await session.execute(
        select(DailyContract)
        .where(
            DailyContract.status == ContractStatus.active,
            DailyContract.sla_ping_sent == False,
        )
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    all_active = result.scalars().all()
    
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=threshold_hours)
    overdue = []
    
    for c in all_active:
        # 2. Base activity – ensure UTC awareness (SQLite sometimes returns naive)
        last_activity = c.started_at
        if last_activity and last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        
        # 3. Check if any task was finished AFTER approval
        for t in c.tasks:
            if t.status == TaskStatus.done and t.updated_at:
                t_upd = t.updated_at
                if t_upd.tzinfo is None:
                    t_upd = t_upd.replace(tzinfo=timezone.utc)
                    
                if last_activity is None or t_upd > last_activity:
                    last_activity = t_upd
        
        if last_activity and last_activity <= cutoff:
            overdue.append(c)
            
    return overdue


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
) -> Optional[Task]:
    import re

    def _normalize(text: str) -> str:
        """Strip all bracketed/parenthetical content, collapse whitespace, lowercase."""
        stripped = re.sub(r'\[.*?\]|\(.*?\)', '', text)
        return re.sub(r'\s+', ' ', stripped).strip().lower()

    norm_desc = _normalize(description)

    # Load existing tasks for this contract and fuzzy-compare
    result = await session.execute(
        select(Task).where(Task.contract_id == contract_id)
    )
    for t in result.scalars().all():
        if _normalize(t.description) == norm_desc:
            print(f"DEBUG: Dedup skip — '{description}' matches existing task.")
            return None  # Skip fuzzy duplicate

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


async def get_all_open_tasks(session: AsyncSession, target_date: Optional[date] = None) -> Sequence[Task]:
    """Fetches all open tasks (todo/in_progress) across the whole team for a specific date (default today)."""
    target_date = target_date or date.today()
    result = await session.execute(
        select(Task)
        .join(DailyContract)
        .where(
            DailyContract.target_date == target_date,
            Task.status.in_([TaskStatus.todo, TaskStatus.in_progress])
        )
        .options(selectinload(Task.contract).selectinload(DailyContract.user))
        .order_by(DailyContract.user_id)
    )
    return result.scalars().all()


async def get_task(session: AsyncSession, task_id: int) -> Optional[Task]:
    result = await session.execute(
        select(Task)
        .where(Task.id == task_id)
        .options(
            selectinload(Task.contract).selectinload(DailyContract.user),
            selectinload(Task.contract).selectinload(DailyContract.tasks)
        )
    )
    return result.scalar_one_or_none()


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
