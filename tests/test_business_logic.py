
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date
from bot.handlers.dev_actions import handle_approve
from db.models import User, DailyContract, Task, TaskStatus, ContractStatus, UserRole
from db import repository as repo
from sqlalchemy import select
from sqlalchemy.orm import selectinload

@pytest.mark.asyncio
async def test_idempotent_approval_mixed_statuses(db_session):
    # 1. Setup Data
    tg_id = 12345
    user, _ = await repo.get_or_create_user(db_session, tg_id=tg_id, username="yaroslav")
    
    contract = DailyContract(
        user_id=user.id,
        target_date=date.today(),
        status=ContractStatus.draft,
        meeting_context="Mixed status test"
    )
    db_session.add(contract)
    await db_session.flush()

    # 4 tasks already in_progress (carried over)
    for i in range(4):
        db_session.add(Task(
            contract_id=contract.id,
            description=f"Carried Task {i}",
            status=TaskStatus.in_progress
        ))
    
    # 3 tasks in todo (newly drafted)
    for i in range(3):
        db_session.add(Task(
            contract_id=contract.id,
            description=f"New Task {i}",
            status=TaskStatus.todo
        ))
    
    await db_session.commit()

    # 2. Mock callback
    callback = AsyncMock()
    callback.data = f"approve:c:{contract.id}"
    callback.from_user.id = tg_id
    callback.bot = AsyncMock()
    callback.message = AsyncMock()

    # 3. Action
    # Patch send_team_notification to avoid network calls during test
    import bot.handlers.dev_actions as dev_actions
    dev_actions.send_team_notification = AsyncMock()

    await handle_approve(callback, db_session)

    # 4. Assertions
    # Refresh contract and tasks
    stmt = select(DailyContract).where(DailyContract.id == contract.id).options(selectinload(DailyContract.tasks))
    res = await db_session.execute(stmt)
    updated_contract = res.scalar_one()

    assert updated_contract.status == ContractStatus.active
    assert len(updated_contract.tasks) == 7
    for t in updated_contract.tasks:
        assert t.status == TaskStatus.in_progress

    # Verify Telegram UI feedback
    callback.answer.assert_called_once()
    callback.message.edit_reply_markup.assert_called_once()

@pytest.mark.asyncio
async def test_double_click_safety(db_session):
    # 1. Setup Data: Already active contract
    tg_id = 54321
    user, _ = await repo.get_or_create_user(db_session, tg_id=tg_id, username="double_click")
    
    contract = DailyContract(
        user_id=user.id,
        target_date=date.today(),
        status=ContractStatus.active,
        meeting_context="Double click test"
    )
    db_session.add(contract)
    await db_session.flush()

    task = Task(
        contract_id=contract.id,
        description="Already active task",
        status=TaskStatus.in_progress
    )
    db_session.add(task)
    await db_session.commit()

    # 2. Mock callback
    callback = AsyncMock()
    callback.data = f"approve:c:{contract.id}"
    callback.from_user.id = tg_id
    callback.bot = AsyncMock()
    callback.message = AsyncMock()

    # Patch notification
    import bot.handlers.dev_actions as dev_actions
    dev_actions.send_team_notification = AsyncMock()

    # 3. Action: Call approve on active contract
    await handle_approve(callback, db_session)

    # 4. Assertions
    # Should still be active/in_progress
    stmt = select(DailyContract).where(DailyContract.id == contract.id).options(selectinload(DailyContract.tasks))
    res = await db_session.execute(stmt)
    updated_contract = res.scalar_one()

    assert updated_contract.status == ContractStatus.active
    assert updated_contract.tasks[0].status == TaskStatus.in_progress
    
    # UI should still respond to clear spinner
    callback.answer.assert_called_once()
