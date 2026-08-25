import asyncio
import json
import logging
import re
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional
from sqlalchemy import select
from app.agents.base import Agent
from app.agents.llm import LLMCaller
from app.agents.pool import AgentPool, get_agent_pool
from app.database.models import ArtifactModel, MessageModel, SessionModel, ToolCallRecordModel
from app.database.session import get_session_factory
from app.orchestration.state import ArtifactItem, DebateMessage, DebateState
from app.orchestration.strategies import get_strategy

logger = logging.getLogger(__name__)

EventCallback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


def extract_code_blocks(text: str) -> List[Dict[str, str]]:
    """Extracts markdown code blocks from text."""
    pattern = re.compile(r"```([a-zA-Z0-9_\-\+]*)\n(.*?)```", re.DOTALL)
    matches = []
    for match in pattern.finditer(text):
        lang = match.group(1).strip().lower() or "text"
        code = match.group(2).strip()
        matches.append({"language": lang, "code": code})
    return matches


class OrchestratorEngine:
    """Multi-Agent Orchestration & Debate Execution Engine."""

    def __init__(self, agent_pool: Optional[AgentPool] = None, llm_caller: Optional[LLMCaller] = None):
        self.agent_pool = agent_pool or get_agent_pool()
        self.llm_caller = llm_caller or LLMCaller()
        self.session_factory = get_session_factory()

    async def run_turn(
        self,
        session_id: str,
        user_prompt: str,
        on_event: Optional[EventCallback] = None,
    ) -> DebateState:
        """Executes a full multi-agent collaborative debate and synthesis turn."""
        async with self.session_factory() as db:
            # 1. Load session config from DB
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            res = await db.execute(stmt)
            session_model = res.scalar_one_or_none()
            if not session_model:
                raise ValueError(f"Session with ID '{session_id}' not found.")

            strategy_name = session_model.strategy
            max_rounds = session_model.max_rounds
            active_keys = session_model.active_agents or ["orchestrator", "architect", "coder", "critic"]
            custom_instructions = session_model.custom_instructions or ""

            # Ensure orchestrator is in active keys
            if "orchestrator" not in active_keys:
                active_keys = ["orchestrator"] + active_keys

            active_agents = self.agent_pool.get_active(active_keys)
            orchestrator_agent = self.agent_pool.get_orchestrator()

            # 2. Initialize Debate State
            state = DebateState(
                session_id=session_id,
                user_prompt=user_prompt,
                strategy=strategy_name,
                max_rounds=max_rounds,
                current_round=0,
                custom_instructions=custom_instructions,
                active_agent_keys=active_keys,
                status="planning",
            )

            # 3. Record User Message in DB
            user_msg_id = str(uuid.uuid4())
            user_msg_db = MessageModel(
                id=user_msg_id,
                session_id=session_id,
                sender_key="user",
                sender_name="User",
                sender_role="Client / Requestor",
                content=user_prompt,
                round_number=0,
                msg_type="user",
            )
            db.add(user_msg_db)
            await db.commit()

            state.messages.append(
                DebateMessage(
                    id=user_msg_id,
                    sender_key="user",
                    sender_name="User",
                    sender_role="Client / Requestor",
                    content=user_prompt,
                    round_number=0,
                    msg_type="user",
                )
            )

            if on_event:
                await on_event({
                    "type": "message_added",
                    "message": state.messages[-1].model_dump(),
                })

            # 4. Phase 1: Master Orchestrator Goal Analysis & Planning
            state.status = "planning"
            state.current_speaker = orchestrator_agent.name
            if on_event:
                await on_event({"type": "status_changed", "status": "planning", "speaker": orchestrator_agent.name})

            orch_plan_prompt = [
                {"role": "user", "content": f"[User Request]: {user_prompt}\n\n위 요청을 분석하고 이번 토론의 핵심 목표, 접근 방향, 각 에이전트(Architect, Coder, Critic)에게 부여할 발언 지침을 작성하세요."}
            ]

            orch_plan_text, orch_tools = await self.llm_caller.call_agent(
                orchestrator_agent, orch_plan_prompt, custom_instructions
            )

            plan_msg_id = str(uuid.uuid4())
            plan_msg_db = MessageModel(
                id=plan_msg_id,
                session_id=session_id,
                sender_key=orchestrator_agent.key,
                sender_name=orchestrator_agent.name,
                sender_role=orchestrator_agent.role,
                content=orch_plan_text,
                round_number=0,
                msg_type="orchestrator",
            )
            db.add(plan_msg_db)
            await db.commit()

            state.messages.append(
                DebateMessage(
                    id=plan_msg_id,
                    sender_key=orchestrator_agent.key,
                    sender_name=orchestrator_agent.name,
                    sender_role=orchestrator_agent.role,
                    content=orch_plan_text,
                    round_number=0,
                    msg_type="orchestrator",
                    tool_calls=orch_tools,
                )
            )

            if on_event:
                await on_event({
                    "type": "message_added",
                    "message": state.messages[-1].model_dump(),
                })

            # 5. Phase 2: Multi-Round Specialist Debate Loop
            strategy = get_strategy(strategy_name)
            state.status = "debating"

            for round_num in range(1, max_rounds + 1):
                state.current_round = round_num
                if on_event:
                    await on_event({
                        "type": "round_started",
                        "round": round_num,
                        "max_rounds": max_rounds,
                    })

                speakers = strategy.get_speakers_for_round(active_agents, round_num, state)

                for agent in speakers:
                    state.current_speaker = agent.name
                    if on_event:
                        await on_event({
                            "type": "status_changed",
                            "status": "debating",
                            "speaker": agent.name,
                            "round": round_num,
                        })

                    # Construct contextual message list for this agent
                    agent_context = self._build_context_for_agent(state, agent)

                    async def handle_tool_call(tool_data: Dict[str, Any]) -> None:
                        # Record tool call in DB
                        tc_id = str(uuid.uuid4())
                        tc_db = ToolCallRecordModel(
                            id=tc_id,
                            session_id=session_id,
                            agent_key=agent.key,
                            tool_name=tool_data.get("tool_name", ""),
                            arguments=tool_data.get("arguments", {}),
                            output=tool_data.get("output", ""),
                            status=tool_data.get("status", "success"),
                        )
                        db.add(tc_db)
                        await db.commit()
                        if on_event:
                            await on_event({
                                "type": "tool_executed",
                                "agent_key": agent.key,
                                "agent_name": agent.name,
                                "tool_call": tool_data,
                            })

                    response_text, tool_logs = await self.llm_caller.call_agent(
                        agent, agent_context, custom_instructions, on_tool_call=handle_tool_call
                    )

                    msg_id = str(uuid.uuid4())
                    msg_db = MessageModel(
                        id=msg_id,
                        session_id=session_id,
                        sender_key=agent.key,
                        sender_name=agent.name,
                        sender_role=agent.role,
                        content=response_text,
                        round_number=round_num,
                        msg_type="agent",
                    )
                    db.add(msg_db)
                    await db.commit()

                    state.messages.append(
                        DebateMessage(
                            id=msg_id,
                            sender_key=agent.key,
                            sender_name=agent.name,
                            sender_role=agent.role,
                            content=response_text,
                            round_number=round_num,
                            msg_type="agent",
                            tool_calls=tool_logs,
                        )
                    )

                    if on_event:
                        await on_event({
                            "type": "message_added",
                            "message": state.messages[-1].model_dump(),
                        })

            # 6. Phase 3: Final Consensus & Artifact Synthesis
            state.status = "synthesizing"
            state.current_speaker = orchestrator_agent.name
            if on_event:
                await on_event({
                    "type": "status_changed",
                    "status": "synthesizing",
                    "speaker": orchestrator_agent.name,
                })

            synth_prompt = self._build_synthesis_prompt(state)
            synth_text, synth_tools = await self.llm_caller.call_agent(
                orchestrator_agent, synth_prompt, custom_instructions
            )

            synth_msg_id = str(uuid.uuid4())
            synth_msg_db = MessageModel(
                id=synth_msg_id,
                session_id=session_id,
                sender_key=orchestrator_agent.key,
                sender_name=orchestrator_agent.name,
                sender_role=orchestrator_agent.role,
                content=synth_text,
                round_number=state.current_round + 1,
                msg_type="orchestrator",
            )
            db.add(synth_msg_db)

            state.messages.append(
                DebateMessage(
                    id=synth_msg_id,
                    sender_key=orchestrator_agent.key,
                    sender_name=orchestrator_agent.name,
                    sender_role=orchestrator_agent.role,
                    content=synth_text,
                    round_number=state.current_round + 1,
                    msg_type="orchestrator",
                    tool_calls=synth_tools,
                )
            )

            # 7. Extract and Persist Artifacts
            artifacts = self._extract_artifacts_from_synthesis(session_id, synth_text, state)
            for art in artifacts:
                art_db = ArtifactModel(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    artifact_type=art.artifact_type,
                    title=art.title,
                    content=art.content,
                    language=art.language,
                )
                db.add(art_db)
                art.id = art_db.id
                state.artifacts.append(art)

            await db.commit()

            state.status = "completed"
            state.is_consensus_reached = True

            if on_event:
                await on_event({
                    "type": "message_added",
                    "message": state.messages[-1].model_dump(),
                })
                await on_event({
                    "type": "artifacts_synthesized",
                    "artifacts": [a.model_dump() for a in state.artifacts],
                })
                await on_event({
                    "type": "turn_completed",
                    "status": "completed",
                })

            return state

    def _build_context_for_agent(self, state: DebateState, agent: Agent) -> List[Dict[str, Any]]:
        """Prepares discussion transcript for agent turn."""
        context: List[Dict[str, Any]] = []
        context.append({
            "role": "user",
            "content": f"[User Goal]: {state.user_prompt}\n\n[Debate Progress]: Round {state.current_round} of {state.max_rounds}."
        })

        for msg in state.messages:
            if msg.sender_key == "user":
                continue
            role_label = f"[{msg.sender_name} ({msg.sender_role})]"
            context.append({
                "role": "assistant" if msg.sender_key == agent.key else "user",
                "content": f"{role_label}:\n{msg.content}"
            })

        context.append({
            "role": "user",
            "content": f"이제 {agent.name}({agent.role})님의 차례입니다. 앞선 논의와 피드백을 반영하여 발언하고 필요시 도구를 활용해 주세요."
        })
        return context

    def _build_synthesis_prompt(self, state: DebateState) -> List[Dict[str, Any]]:
        """Constructs prompt for orchestrator final consensus & artifact generation."""
        transcript_lines = []
        for msg in state.messages:
            transcript_lines.append(f"### {msg.sender_name} ({msg.sender_role}):\n{msg.content}\n")
        full_transcript = "\n".join(transcript_lines)

        prompt = (
            f"[User Goal]: {state.user_prompt}\n\n"
            f"[Full Multi-Agent Debate Transcript]:\n{full_transcript}\n\n"
            f"수석 오케스트레이터로서 모든 토론과 피드백을 통합하여 최종 합의 보고서를 작성하세요.\n"
            f"반드시 다음 항목들을 포함해야 합니다:\n"
            f"1. **최종 합의 요약 및 아키텍처 결정 사항 (Summary & Architecture)**\n"
            f"2. **Mermaid 다이어그램** (```mermaid 블록)\n"
            f"3. **완전한 실행 가능 소스 코드** (```python 블록)\n"
            f"4. **품질/보안 점검표 및 엣지 케이스 대응 전략**"
        )
        return [{"role": "user", "content": prompt}]

    def _extract_artifacts_from_synthesis(
        self, session_id: str, synth_text: str, state: DebateState
    ) -> List[ArtifactItem]:
        """Extracts markdown, code, and mermaid artifacts from the synthesis text."""
        artifacts: List[ArtifactItem] = []

        # 1. Full Synthesized Markdown Document
        artifacts.append(
            ArtifactItem(
                artifact_type="markdown",
                title="종합 아키텍처 & 산출물 보고서 (Final Synthesis Report)",
                content=synth_text,
                language="markdown",
            )
        )

        # 2. Extract code blocks
        code_blocks = extract_code_blocks(synth_text)
        code_idx = 1
        mermaid_idx = 1
        for block in code_blocks:
            lang = block["language"]
            code = block["code"]
            if lang == "mermaid":
                artifacts.append(
                    ArtifactItem(
                        artifact_type="mermaid",
                        title=f"시스템 아키텍처 다이어그램 #{mermaid_idx}",
                        content=code,
                        language="mermaid",
                    )
                )
                mermaid_idx += 1
            elif lang in ["python", "py", "typescript", "javascript", "bash", "shell", "json", "toml", "sql"]:
                artifacts.append(
                    ArtifactItem(
                        artifact_type="code",
                        title=f"핵심 구현 소스코드 ({lang}) #{code_idx}",
                        content=code,
                        language=lang if lang not in ["py"] else "python",
                    )
                )
                code_idx += 1

        # 3. JSON Summary Artifact
        json_summary = {
            "session_id": session_id,
            "goal": state.user_prompt,
            "strategy": state.strategy,
            "total_rounds": state.current_round,
            "participating_agents": state.active_agent_keys,
            "total_messages": len(state.messages),
            "consensus_reached": True,
        }
        artifacts.append(
            ArtifactItem(
                artifact_type="json",
                title="세션 메타데이터 & 토론 요약 (JSON)",
                content=json.dumps(json_summary, indent=2, ensure_ascii=False),
                language="json",
            )
        )

        return artifacts


_orchestrator_engine: Optional[OrchestratorEngine] = None


def get_orchestrator_engine() -> OrchestratorEngine:
    global _orchestrator_engine
    if _orchestrator_engine is None:
        _orchestrator_engine = OrchestratorEngine()
    return _orchestrator_engine
