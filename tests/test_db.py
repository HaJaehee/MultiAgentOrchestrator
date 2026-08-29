import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.database.models import ArtifactModel, Base, MessageModel, SessionModel, ToolCallRecordModel


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_session_and_message_crud(async_db: AsyncSession):
    # 1. Create Session
    session = SessionModel(
        title="Test Debate Session",
        strategy="sequential_debate",
        max_rounds=2,
        active_agents=["orchestrator", "architect"],
    )
    async_db.add(session)
    await async_db.commit()

    assert session.id is not None

    # 2. Add Message
    msg = MessageModel(
        session_id=session.id,
        sender_key="orchestrator",
        sender_name="Master Orchestrator",
        sender_role="Moderator",
        content="Plan formulation",
        round_number=0,
        msg_type="orchestrator",
    )
    async_db.add(msg)
    await async_db.commit()

    # 3. Add Tool Call
    tc = ToolCallRecordModel(
        session_id=session.id,
        message_id=msg.id,
        agent_key="orchestrator",
        tool_name="filesystem__read_file",
        arguments={"path": "main.py"},
        output="sample file content",
        status="success",
    )
    async_db.add(tc)

    # 4. Add Artifact
    art = ArtifactModel(
        session_id=session.id,
        artifact_type="code",
        title="Sample Code",
        content="print('hello')",
        language="python",
    )
    async_db.add(art)
    await async_db.commit()

    # 5. Query Session with relationships
    stmt = select(SessionModel).where(SessionModel.id == session.id)
    res = await async_db.execute(stmt)
    loaded_session = res.scalar_one()

    assert loaded_session.title == "Test Debate Session"
    assert len(loaded_session.messages) == 1
    assert len(loaded_session.artifacts) == 1
    assert loaded_session.messages[0].content == "Plan formulation"
