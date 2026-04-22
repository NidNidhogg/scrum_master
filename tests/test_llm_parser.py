
import pytest
from unittest.mock import AsyncMock, patch
from datetime import date
from bot.handlers.admin import _process_transcript
from db.models import User, DailyContract, Task, ContractStatus, UserRole
from db import repository as repo
from agent.tools import BatchCreateContractsInput, TaskInput
from sqlalchemy import select
from sqlalchemy.orm import selectinload

@pytest.mark.asyncio
async def test_successful_parsing_and_db_injection(db_session):
    # 1. Setup: Register 2 dummy users
    dev1, _ = await repo.get_or_create_user(db_session, tg_id=101, username="dev1", full_name="Developer One")
    dev2, _ = await repo.get_or_create_user(db_session, tg_id=102, username="dev2", full_name="Developer Two")
    await db_session.commit()

    # 2. Mock Gemini response
    # Structure based on BatchCreateContractsInput
    mock_parsed_output = BatchCreateContractsInput(
        meeting_context="Testing LLM Injection logic.",
        tasks=[
            TaskInput(developer_alias="dev1", description="Write tests"),
            TaskInput(developer_alias="dev1", description="Fix bug"),
            TaskInput(developer_alias="dev2", description="Deploy to staging")
        ]
    )

    # 3. Action
    # Mocking AgentCore.parse_transcript (called inside _process_transcript)
    with patch("bot.handlers.admin.agent.parse_transcript", new_callable=AsyncMock) as mock_parse:
        mock_parse.return_value = mock_parsed_output
        
        # We need a mock status_message because _process_transcript calls .edit_text on it
        status_msg = AsyncMock()
        
        raw_text = "Simulated raw transcript text"
        await _process_transcript(raw_text, db_session, status_msg)

    # 4. Assertions
    # Verify contracts created for today (default date in _process_transcript is today)
    stmt = select(DailyContract).where(DailyContract.target_date == date.today()).options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    res = await db_session.execute(stmt)
    contracts = res.scalars().all()

    assert len(contracts) == 2
    
    # Check Dev1's contract
    dev1_contract = next(c for c in contracts if c.user_id == dev1.id)
    assert len(dev1_contract.tasks) == 2
    assert any(t.description == "Write tests" for t in dev1_contract.tasks)
    assert any(t.description == "Fix bug" for t in dev1_contract.tasks)

    # Check Dev2's contract
    dev2_contract = next(c for c in contracts if c.user_id == dev2.id)
    assert len(dev2_contract.tasks) == 1
    assert dev2_contract.tasks[0].description == "Deploy to staging"

    # Verify status message was updated
    status_msg.edit_text.assert_called()
    # Check that it called the final review message formatter
    args, kwargs = status_msg.edit_text.call_args
    header_text = args[0].lower()
    assert any(x in header_text for x in ["draft daily plans", "планы на сегодня", "today’s drafts"])
