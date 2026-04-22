
import pytest
from db import repository as repo
from db.models import User, UserRole

@pytest.mark.asyncio
async def test_get_user_by_tg_id(db_session):
    # Setup: Insert a dummy user
    tg_id = 123456789
    user = User(
        tg_id=tg_id,
        username="test_user",
        full_name="Test User",
        role=UserRole.developer,
        notification_chat_id=tg_id
    )
    db_session.add(user)
    await db_session.flush()

    # Call repository function
    fetched_user = await repo.get_user_by_tg_id(db_session, tg_id)

    # Assert
    assert fetched_user is not None
    assert fetched_user.tg_id == tg_id
    assert fetched_user.username == "test_user"

@pytest.mark.asyncio
async def test_get_or_create_user(db_session):
    tg_id = 987654321
    
    # 1. Test creation
    user, created = await repo.get_or_create_user(
        db_session, 
        tg_id=tg_id, 
        username="new_dev",
        full_name="New Developer"
    )
    assert created is True
    assert user.tg_id == tg_id
    
    # 2. Test fetching (not creating second time)
    user2, created2 = await repo.get_or_create_user(
        db_session,
        tg_id=tg_id
    )
    assert created2 is False
    assert user2.id == user.id
