"""
tests/test_sla_repeat.py — SLA repeat-ping window logic.

The prod failure: one ping per contract per day was silently all a dev
ever got; unresolved-name contracts never pinged at all. The repeat
window must bring a contract back into overdue results.
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta, timezone

from db import repository as repo
from db.models import (
    ContractStatus,
    DailyContract,
    Task,
    TaskStatus,
    User,
    UserRole,
)


async def _active_contract_with_task(db_session) -> DailyContract:
    user, _ = await repo.get_or_create_user(
        db_session, tg_id=111, username="dev", full_name="Dev One",
        role=UserRole.developer,
    )
    contract = DailyContract(
        user_id=user.id,
        target_date=date.today(),
        status=ContractStatus.active,
        started_at=datetime.now(tz=timezone.utc) - timedelta(hours=5),
    )
    db_session.add(contract)
    await db_session.flush()
    db_session.add(Task(
        contract_id=contract.id, description="Idle task",
        status=TaskStatus.in_progress,
    ))
    await db_session.commit()
    return contract


@pytest.mark.asyncio
async def test_first_ping_when_overdue(db_session):
    contract = await _active_contract_with_task(db_session)
    overdue = await repo.get_overdue_active_contracts(db_session)
    assert contract in overdue


@pytest.mark.asyncio
async def test_repeat_window_suppresses_then_restores(db_session):
    contract = await _active_contract_with_task(db_session)

    # Ping #1 fires → contract leaves the overdue list immediately
    await repo.record_sla_ping(db_session, contract.id)
    await db_session.commit()
    assert contract not in await repo.get_overdue_active_contracts(db_session)

    # Simulate the ping aging past the repeat window → contract is due again
    contract.sla_ping_at = datetime.now(tz=timezone.utc) - timedelta(hours=5)
    await db_session.commit()
    assert contract in await repo.get_overdue_active_contracts(db_session)


@pytest.mark.asyncio
async def test_terminal_flag_stops_monitoring(db_session):
    contract = await _active_contract_with_task(db_session)
    await repo.mark_sla_ping_sent(db_session, contract.id)
    await db_session.commit()
    assert contract not in await repo.get_overdue_active_contracts(db_session)


@pytest.mark.asyncio
async def test_recent_activity_keeps_contract_out(db_session):
    user, _ = await repo.get_or_create_user(
        db_session, tg_id=222, username="dev2", full_name="Dev Two",
        role=UserRole.developer,
    )
    contract = DailyContract(
        user_id=user.id,
        target_date=date.today(),
        status=ContractStatus.active,
        started_at=datetime.now(tz=timezone.utc) - timedelta(hours=5),
    )
    db_session.add(contract)
    await db_session.flush()
    # Task finished 1h ago → last_activity inside the threshold
    db_session.add(Task(
        contract_id=contract.id, description="Done task",
        status=TaskStatus.done,
        updated_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    ))
    await db_session.commit()
    assert contract not in await repo.get_overdue_active_contracts(db_session)
