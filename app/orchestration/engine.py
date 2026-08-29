import json
import logging
import re
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional
from sqlalchemy import select
from app.agents.base import Agent
from app.agents.llm import LLMCaller, LLMUnavailableError, estimate_tokens
from app.agents.personas import prepare_agents_for_turn
from app.agents.pool import AgentPool, get_agent_pool
from app.config import resolve_workspace_dir
from app.mcp.manager import get_mcp_manager
from app.database.models import ArtifactModel, MessageModel, SessionModel, ToolCallRecordModel
from app.database.session import get_session_factory
from app.orchestration.control import TurnControl
from app.orchestration.state import ArtifactItem, DebateMessage, DebateState
from app.orchestration.strategies import get_strategy

logger = logging.getLogger(__name__)

EventCallback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]

# 닫는 펜스가 없어도 (max_tokens 로 답변이 잘렸을 때) 마지막 블록을 건집니다.
# 예전 정규식은 ``` 짝이 맞을 때만 매칭돼서, 다이어그램 도중에 잘린 답변은
# 아티팩트가 통째로 사라졌습니다.
CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_\-\+]*)[ \t]*\r?\n(.*?)(?:```|\Z)", re.DOTALL)

# 언어 태그 없이 열린 블록이라도 첫 줄이 이 키워드면 Mermaid 로 취급합니다.
MERMAID_HEADERS = (
    "graph", "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "journey", "gantt", "pie", "gitgraph", "mindmap", "timeline",
    "quadrantchart", "requirementdiagram", "c4context", "sankey-beta",
    "block-beta", "architecture-beta", "xychart-beta",
)

CODE_LANGUAGES = ("python", "py", "typescript", "javascript", "bash", "shell", "json", "toml", "sql")

# `A[결제 서비스 (Payment)]` 처럼 대괄호 라벨 안에 괄호가 들어간 형태. LLM 이 가장 자주
# 만드는 Mermaid 파싱 오류이고, 따옴표로 감싸면 그대로 통과합니다.
_PAREN_LABEL_RE = re.compile(r"\[([^\[\]{}\"|]*[()][^\[\]{}\"|]*)\]")
# 여는 문자 -> 닫는 문자. 이 쌍으로 감싸인 것은 라벨이 아니라 노드 모양입니다.
_SHAPE_PAIRS = {"(": ")", "[": "]", "/": "/", "\\": "\\", "{": "}"}


def normalize_mermaid(content: str) -> str:
    """LLM 이 흔히 내는 Mermaid 문법 오류를 최소한만 손봅니다.

    다이어그램을 다시 써 주는 것이 아니라, 렌더러가 통째로 거부해서 화면이 비는
    두 가지 경우만 막습니다: 잘못된 줄바꿈과 따옴표 없는 괄호 라벨.
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return text

    # ```mermaid 를 잘라낸 뒤 남은 "mermaid" 머리글
    lines = text.split("\n")
    if lines[0].strip().lower() == "mermaid":
        lines = lines[1:]

    out: List[str] = []
    for line in lines:
        # `[(...)]`(원통), `[[...]]`(서브루틴), `[/.../]`·`[\...\]`(평행사변형) 같은
        # 모양 문법은 라벨이 아니라 노드 종류입니다. 따옴표를 씌우면 안 됩니다.
        def _quote(m: re.Match) -> str:
            inner = m.group(1)
            if not inner:
                return m.group(0)
            if _SHAPE_PAIRS.get(inner[0]) == inner[-1]:
                return m.group(0)
            return f'["{inner.strip()}"]'

        out.append(_PAREN_LABEL_RE.sub(_quote, line))
    return "\n".join(out).strip()


def extract_code_blocks(text: str) -> List[Dict[str, str]]:
    """Extracts markdown code blocks from text."""
    matches = []
    for match in CODE_FENCE_RE.finditer(text or ""):
        lang = match.group(1).strip().lower() or "text"
        code = match.group(2).strip()
        if not code:
            continue
        if lang == "text":
            first = code.split("\n", 1)[0].strip().lower()
            if any(first.startswith(h) for h in MERMAID_HEADERS):
                lang = "mermaid"
        matches.append({"language": lang, "code": code})
    return matches


class OrchestratorEngine:
    """Multi-Agent Orchestration & Debate Execution Engine."""

    def __init__(self, agent_pool: Optional[AgentPool] = None, llm_caller: Optional[LLMCaller] = None):
        self.agent_pool = agent_pool or get_agent_pool()
        self.llm_caller = llm_caller or LLMCaller()
        self.session_factory = get_session_factory()

    # ------------------------------------------------------------------ 작업 공간

    @staticmethod
    async def _apply_session_workspace(workspace_dir: str) -> None:
        """세션이 지정한 작업 공간으로 MCP 서버를 맞춥니다 (같으면 아무것도 안 함)."""
        manager = get_mcp_manager()
        desired = resolve_workspace_dir(workspace_dir or None)
        if manager.workspace == desired:
            return
        logger.info(f"Switching MCP workspace for this turn: {manager.workspace} -> {desired}")
        await manager.set_workspace(desired)

    # ------------------------------------------------------------------ 발언

    @staticmethod
    def _unavailable_notice(agent: Agent, exc: LLMUnavailableError, streamed: str) -> str:
        """응답을 받지 못했을 때 화면과 기록에 남는 문구.

        여기서 그럴듯한 대체 발언을 만들어 내면 다음 에이전트의 입력과 최종 합성
        보고서까지 그 거짓말 위에 쌓입니다. 못 받았다고 적는 편이 낫습니다.
        """
        notice = (
            f"> ⚠️ **연결 끊김 — {agent.name} 의 발언을 받지 못했습니다.**\n"
            f">\n"
            f"> - 모델: `{agent.model}`\n"
            f"> - 엔드포인트: `{exc.endpoint}`\n"
            f"> - 원인: `{exc.reason}`\n"
            f">\n"
            f"> 이 자리에 들어갈 내용을 대신 지어내지 않았습니다. "
            f"엔드포인트를 복구한 뒤 같은 요청을 다시 보내주세요."
        )
        if streamed.strip():
            return (
                f"{streamed.rstrip()}\n\n---\n\n{notice}\n>\n"
                f"> (위 본문은 연결이 끊기기 전까지 도착한 부분입니다.)"
            )
        return notice

    async def _speak(
        self,
        *,
        db,
        state: DebateState,
        agent: Agent,
        prompt_messages: List[Dict[str, Any]],
        custom_instructions: str,
        round_number: int,
        msg_type: str,
        on_event: Optional[EventCallback],
    ) -> DebateMessage:
        """한 에이전트의 발언을 스트리밍하고, DB 에 기록하고, 상태에 반영합니다.

        LLM 이 응답하지 못하면 실패 사실을 그대로 적은 `msg_type="error"` 발언을
        남기고 토론을 계속합니다 (다른 에이전트는 아직 살아 있을 수 있습니다).

        이 발언 중에 실행된 MCP 도구도 여기서 함께 기록합니다. 어느 발언이 부른
        도구인지는 이 자리에서만 알 수 있습니다 — 밖에서 기록하던 예전 방식은
        `message_id` 를 비워 둘 수밖에 없었고, 그래서 새로고침한 화면과 저장
        파일에서 도구 기록이 발언과 따로 놀았습니다.
        """
        msg_id = str(uuid.uuid4())
        if on_event:
            await on_event({
                "type": "message_stream_start",
                "message": {
                    "id": msg_id,
                    "sender_key": agent.key,
                    "sender_name": agent.name,
                    "sender_role": agent.role,
                    "content": "",
                    "round_number": round_number,
                    "msg_type": msg_type,
                },
            })

        # 이 발언이 실행한 도구. 모아 두었다가 발언 행과 **같은 커밋**에 넣습니다.
        # 도구가 끝나는 즉시 넣으면, 발언 행이 아직 없는 동안 존재하지 않는 발언을
        # 가리키는 행이 남습니다. SQLite 가 외래키를 검사하지 않아 지금은 통과할
        # 뿐이고, 누군가 PRAGMA foreign_keys 를 켜는 날 삽입이 실패합니다.
        executed_tools: List[Dict[str, Any]] = []

        async def _on_tool_call(call_log: Dict[str, Any]) -> None:
            executed_tools.append(call_log)
            if on_event:
                await on_event({
                    "type": "tool_executed",
                    "agent_key": agent.key,
                    "agent_name": agent.name,
                    "tool_call": call_log,
                })

        streamed: List[str] = []

        async def _on_chunk(delta: str) -> None:
            streamed.append(delta)
            if on_event:
                await on_event({
                    "type": "message_stream_chunk",
                    "message_id": msg_id,
                    "delta": delta,
                })

        try:
            content, tool_logs = await self.llm_caller.call_agent(
                agent, prompt_messages, custom_instructions,
                on_tool_call=_on_tool_call, on_chunk=_on_chunk,
                session_id=state.session_id,
            )
            final_type = msg_type
        except LLMUnavailableError as exc:
            logger.warning(f"Agent '{agent.key}' produced no response: {exc}")
            content = self._unavailable_notice(agent, exc, "".join(streamed))
            tool_logs = []
            final_type = "error"
            if agent.key not in state.failed_agent_keys:
                state.failed_agent_keys.append(agent.key)

        db.add(MessageModel(
            id=msg_id,
            session_id=state.session_id,
            sender_key=agent.key,
            sender_name=agent.name,
            sender_role=agent.role,
            content=content,
            round_number=round_number,
            msg_type=final_type,
        ))
        for call_log in executed_tools:
            db.add(ToolCallRecordModel(
                id=str(uuid.uuid4()),
                session_id=state.session_id,
                message_id=msg_id,
                agent_key=agent.key,
                tool_name=call_log.get("tool_name", ""),
                arguments=call_log.get("arguments", {}),
                output=call_log.get("output", ""),
                status=call_log.get("status", "success"),
            ))
        await db.commit()

        message = DebateMessage(
            id=msg_id,
            sender_key=agent.key,
            sender_name=agent.name,
            sender_role=agent.role,
            content=content,
            round_number=round_number,
            msg_type=final_type,
            # 발언이 실패로 끝나면 `call_agent` 는 도구 기록을 돌려주지 못합니다.
            # 그전에 실제로 실행된 것은 남아 있어야 합니다.
            tool_calls=tool_logs or executed_tools,
        )
        state.messages.append(message)

        if on_event:
            await on_event({"type": "message_added", "message": message.model_dump()})
        return message

    # ------------------------------------------------------------------ 유저 발언

    async def _record_user_message(
        self,
        *,
        db,
        state: DebateState,
        content: str,
        round_number: int,
        on_event: Optional[EventCallback],
    ) -> DebateMessage:
        """유저 발언을 기록에 남기고 화면에 흘립니다.

        턴을 여는 최초 요청과 토론 도중의 개입이 같은 자리에 같은 모양으로
        들어가야, 다음 발언자의 맥락(`_build_context_for_agent`)과 합성 전사가
        둘을 구분 없이 읽습니다.
        """
        msg_id = str(uuid.uuid4())
        db.add(MessageModel(
            id=msg_id,
            session_id=state.session_id,
            sender_key="user",
            sender_name="User",
            sender_role="Client / Requestor",
            content=content,
            round_number=round_number,
            msg_type="user",
        ))
        await db.commit()

        message = DebateMessage(
            id=msg_id,
            sender_key="user",
            sender_name="User",
            sender_role="Client / Requestor",
            content=content,
            round_number=round_number,
            msg_type="user",
        )
        state.messages.append(message)

        if on_event:
            await on_event({"type": "message_added", "message": message.model_dump()})
        return message

    async def _apply_interjections(
        self,
        *,
        db,
        state: DebateState,
        control: Optional[TurnControl],
        round_number: int,
        on_event: Optional[EventCallback],
    ) -> int:
        """대기 중인 사용자 개입을 지금 시점의 토론 기록에 밀어 넣습니다.

        발언이 진행되는 중간에 끼워 넣으면, 그 발언의 프롬프트는 이미 만들어진
        뒤라 반영되지도 않으면서 기록 순서만 어긋납니다. 그래서 호출 지점은 항상
        발언과 발언 사이입니다. 여기서 들어간 메모는 다음 발언자의 맥락에 그대로
        실립니다.
        """
        if control is None:
            return 0
        notes = control.drain_notes()
        for note in notes:
            await self._record_user_message(
                db=db,
                state=state,
                content=f"[토론 중 사용자 개입]\n{note}",
                round_number=round_number,
                on_event=on_event,
            )
        state.interjection_count += len(notes)
        if notes:
            logger.info(
                f"Applied {len(notes)} user interjection(s) to session {state.session_id} "
                f"at round {round_number}"
            )
        return len(notes)

    # ------------------------------------------------------------------ 턴

    async def run_turn(
        self,
        session_id: str,
        user_prompt: str,
        on_event: Optional[EventCallback] = None,
        control: Optional[TurnControl] = None,
    ) -> DebateState:
        """Executes a full multi-agent collaborative debate and synthesis turn.

        `control` 이 주어지면 발언과 발언 사이마다 사용자의 정지 요청과 개입
        메모를 확인합니다. 정지는 태스크를 죽이는 것이 아니라 남은 라운드를
        건너뛰고 최종 합성으로 넘어가는 것이라, 지금까지의 토론으로도 산출물이
        나옵니다.
        """
        async with self.session_factory() as db:
            # 1. Load session config from DB
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            res = await db.execute(stmt)
            session_model = res.scalar_one_or_none()
            if not session_model:
                raise ValueError(f"Session with ID '{session_id}' not found.")

            # 이 대화의 작업 공간을 적용합니다. filesystem 은 허용 경로를 argv 로,
            # sandbox 는 SANDBOX_WORKSPACE 를 env 로 기동 시점에 받으므로, 경로가
            # 달라졌으면 서버를 다시 띄우는 것 외에 방법이 없습니다.
            await self._apply_session_workspace(session_model.workspace_dir)

            strategy_name = session_model.strategy
            max_rounds = session_model.max_rounds
            active_keys = session_model.active_agents or ["orchestrator", "architect", "coder", "critic"]
            custom_instructions = session_model.custom_instructions or ""

            # Ensure orchestrator is in active keys
            if "orchestrator" not in active_keys:
                active_keys = ["orchestrator"] + active_keys

            # 세션 페르소나를 적용합니다. 첫 턴이면 이 시점에 기록되고 잠깁니다.
            active_agents = await prepare_agents_for_turn(
                db, session_model, self.agent_pool, active_keys
            )
            orchestrator_agent = next(
                (a for a in active_agents if a.key == "orchestrator"),
                self.agent_pool.get_orchestrator(),
            )

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

            # 이전 턴의 대화 기록을 DB에서 로드하여 대화 맥락을 보존합니다.
            stmt_prev = (
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at)
            )
            res_prev = await db.execute(stmt_prev)
            prev_db_msgs = res_prev.scalars().all()
            for pm in prev_db_msgs:
                state.messages.append(
                    DebateMessage(
                        id=pm.id,
                        sender_key=pm.sender_key,
                        sender_name=pm.sender_name,
                        sender_role=pm.sender_role,
                        content=pm.content,
                        round_number=pm.round_number,
                        msg_type=pm.msg_type,
                    )
                )

            # 3. Record User Message in DB
            await self._record_user_message(
                db=db,
                state=state,
                content=user_prompt,
                round_number=0,
                on_event=on_event,
            )

            # 4. Phase 1: Master Orchestrator Goal Analysis & Planning
            state.status = "planning"
            state.current_speaker = orchestrator_agent.name
            if on_event:
                await on_event({"type": "status_changed", "status": "planning", "speaker": orchestrator_agent.name})

            if len(state.messages) > 1:
                history_snippets = []
                for m in state.messages[:-1]:
                    if m.msg_type == "error":
                        continue
                    history_snippets.append(f"{m.sender_name}({m.sender_role}): {m.content[:250]}")
                history_text = "\n".join(history_snippets[-6:])
                orch_plan_prompt = [
                    {"role": "user", "content": (
                        f"[이전 대화 맥락]:\n{history_text}\n\n"
                        f"[신규 User Request]:\n{user_prompt}\n\n"
                        "위의 이전 세션 논의 맥락과 새로운 사용자 요청을 종합 분석하여 이번 토론의 핵심 목표, "
                        "접근 방향, 각 에이전트에게 부여할 발언 지침을 작성하세요."
                    )}
                ]
            else:
                orch_plan_prompt = [
                    {"role": "user", "content": f"[User Request]: {user_prompt}\n\n위 요청을 분석하고 이번 토론의 핵심 목표, 접근 방향, 각 에이전트(Architect, Coder, Critic)에게 부여할 발언 지침을 작성하세요."}
                ]

            await self._speak(
                db=db,
                state=state,
                agent=orchestrator_agent,
                prompt_messages=orch_plan_prompt,
                custom_instructions=custom_instructions,
                round_number=0,
                msg_type="orchestrator",
                on_event=on_event,
            )

            # 5. Phase 2: Multi-Round Specialist Debate Loop
            strategy = get_strategy(strategy_name)
            state.status = "debating"

            # 계획 발언과 첫 라운드 사이도 개입이 반영되는 지점입니다.
            await self._apply_interjections(
                db=db, state=state, control=control, round_number=0, on_event=on_event
            )

            stopped_early = False
            for round_num in range(1, max_rounds + 1):
                if control is not None and control.stop_requested:
                    stopped_early = True
                    break

                state.current_round = round_num
                if on_event:
                    await on_event({
                        "type": "round_started",
                        "round": round_num,
                        "max_rounds": max_rounds,
                    })

                speakers = strategy.get_speakers_for_round(active_agents, round_num, state)

                for agent in speakers:
                    # 발언과 발언 사이. 사용자의 개입과 정지는 여기서만 반영됩니다.
                    # 진행 중이던 발언을 끊지 않으므로 잘린 기록이 남지 않습니다.
                    await self._apply_interjections(
                        db=db, state=state, control=control,
                        round_number=round_num, on_event=on_event,
                    )
                    if control is not None and control.stop_requested:
                        stopped_early = True
                        break

                    state.current_speaker = agent.name
                    if on_event:
                        await on_event({
                            "type": "status_changed",
                            "status": "debating",
                            "speaker": agent.name,
                            "round": round_num,
                        })

                    await self._speak(
                        db=db,
                        state=state,
                        agent=agent,
                        prompt_messages=self._build_context_for_agent(state, agent),
                        custom_instructions=custom_instructions,
                        round_number=round_num,
                        msg_type="agent",
                        on_event=on_event,
                    )

                if stopped_early:
                    break

            # 정지 요청이 마지막 발언 도중에 들어왔더라도, 그때까지 쌓인 개입은
            # 합성 전사에 실어 보냅니다.
            await self._apply_interjections(
                db=db, state=state, control=control,
                round_number=state.current_round, on_event=on_event,
            )
            state.stopped_early = stopped_early
            if stopped_early:
                logger.info(
                    f"Debate for session {session_id} stopped early by the user at "
                    f"round {state.current_round}/{max_rounds}; synthesizing what we have."
                )

            # 6. Phase 3: Final Consensus & Artifact Synthesis
            state.status = "synthesizing"
            state.current_speaker = orchestrator_agent.name
            if on_event:
                await on_event({
                    "type": "status_changed",
                    "status": "synthesizing",
                    "speaker": orchestrator_agent.name,
                })

            synth_message = await self._speak(
                db=db,
                state=state,
                agent=orchestrator_agent,
                prompt_messages=self._build_synthesis_prompt(state, orchestrator_agent),
                custom_instructions=custom_instructions,
                round_number=state.current_round + 1,
                msg_type="orchestrator",
                on_event=on_event,
            )
            synthesis_failed = synth_message.msg_type == "error"

            # 7. Extract and Persist Artifacts
            artifacts = self._extract_artifacts_from_synthesis(
                session_id, synth_message.content, state, synthesis_failed=synthesis_failed
            )
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

            # 합성이 시작된 뒤에 도착한 개입은 이번 턴에 실을 자리가 없습니다.
            # 그대로 버리면 화면은 "다음 발언 차례에 반영됩니다" 라고 알린 채 턴이
            # 끝나 버립니다. 기록에 남겨 두면 다음 턴이 맥락으로 읽어 갑니다.
            deferred = await self._apply_interjections(
                db=db, state=state, control=control,
                round_number=state.current_round + 1, on_event=on_event,
            )
            if deferred and on_event:
                await on_event({"type": "interjections_deferred", "count": deferred})

            state.status = "completed"
            # 사용자가 도중에 끊었다면 합의에 이른 것이 아닙니다.
            state.is_consensus_reached = not state.failed_agent_keys and not state.stopped_early
            if state.failed_agent_keys:
                state.error_message = (
                    "다음 에이전트가 LLM 엔드포인트에 닿지 못했습니다: "
                    + ", ".join(state.failed_agent_keys)
                )

            if on_event:
                await on_event({
                    "type": "artifacts_synthesized",
                    "artifacts": [a.model_dump() for a in state.artifacts],
                })
                await on_event({
                    "type": "turn_completed",
                    "status": "completed",
                    "failed_agents": list(state.failed_agent_keys),
                    "error_message": state.error_message,
                    "stopped_early": state.stopped_early,
                    "rounds_completed": state.current_round,
                    "max_rounds": state.max_rounds,
                })

            return state

    def _build_context_for_agent(self, state: DebateState, agent: Agent) -> List[Dict[str, Any]]:
        """Prepares discussion transcript for agent turn."""
        context: List[Dict[str, Any]] = []
        context.append({
            "role": "user",
            "content": f"[User Goal / Current Request]:\n{state.user_prompt}\n\n[Debate Progress]: Round {state.current_round} of {state.max_rounds}."
        })

        for msg in state.messages:
            # 응답을 못 받은 자리는 맥락에 넣지 않습니다. 실패 안내문을 발언인 양
            # 읽히게 하면 다음 에이전트가 그것을 논평하기 시작합니다.
            if msg.msg_type == "error":
                continue
            if msg.sender_key == "user":
                context.append({
                    "role": "user",
                    "content": f"[User]:\n{msg.content}"
                })
            else:
                role_label = f"[{msg.sender_name} ({msg.sender_role})]"
                context.append({
                    "role": "assistant" if msg.sender_key == agent.key else "user",
                    "content": f"{role_label}:\n{msg.content}"
                })

        context.append({
            "role": "user",
            "content": f"이제 {agent.name}({agent.role})님의 차례입니다. 앞선 전체 논의 맥락과 직전 발언들을 충실히 반영하여 전문적인 의견을 발언하고 필요시 도구를 활용해 주세요."
        })
        return context

    def _build_synthesis_prompt(
        self, state: DebateState, agent: Optional[Agent] = None
    ) -> List[Dict[str, Any]]:
        """Constructs prompt for orchestrator final consensus & artifact generation.

        전사(transcript)를 오케스트레이터의 컨텍스트 한도에 맞춰 자릅니다.
        여기는 한 개의 user 메시지 안에 토론 전체가 통째로 들어가는 자리라,
        `fit_context_window()` 가 손댈 수 있는 것이 없습니다 (메시지 단위로
        덜어내는데 덜어낼 메시지가 없습니다). 라운드가 몇 번만 돌아도 한도를
        넘어 400 이 나므로, 만드는 쪽에서 크기를 정해야 합니다.

        최근 발언부터 채웁니다. 뒤로 갈수록 앞선 논의가 반영된 결론이라,
        잘라야 한다면 앞쪽을 버리는 편이 낫습니다.
        """
        usable = [m for m in state.messages if m.msg_type != "error"]

        def render(msg: DebateMessage) -> str:
            prefix = "### [User]" if msg.sender_key == "user" else f"### {msg.sender_name} ({msg.sender_role})"
            return f"{prefix}:\n{msg.content}\n"

        if agent is None:
            kept, dropped = [render(m) for m in usable], 0
        else:
            # 응답 분량과 지시문 몫을 빼고 남는 것이 전사의 예산입니다.
            budget = agent.max_context_window - agent.max_tokens - 1024
            kept_rev: List[str] = []
            dropped = 0
            for msg in reversed(usable):
                block = render(msg)
                probe = [{"role": "user", "content": "\n".join([block] + kept_rev)}]
                if kept_rev and budget > 0 and estimate_tokens(agent.model, probe) > budget:
                    dropped = len(usable) - len(kept_rev)
                    break
                kept_rev.insert(0, block)
            kept = kept_rev

        if dropped:
            logger.warning(
                f"Synthesis transcript trimmed: dropped {dropped} of {len(usable)} message(s) "
                f"(max_context_window={agent.max_context_window})"
            )
            kept.insert(0, f"[앞선 발언 {dropped}건은 컨텍스트 한도로 생략되었습니다. "
                           f"생략된 내용을 지어내지 말고, 남은 기록만으로 종합하세요.]\n")

        full_transcript = "\n".join(kept)

        early_stop = ""
        if state.stopped_early:
            # 남은 라운드에서 나왔을 반론을 지어내면, 검증되지 않은 결론이 검증된
            # 것처럼 보고서에 올라갑니다.
            early_stop = (
                f"\n[주의] 사용자가 예정된 라운드보다 일찍 토론을 정지시켰습니다 "
                f"(진행: {state.current_round}/{state.max_rounds} 라운드). 남은 라운드에서 "
                f"나왔을 의견을 추측해 채우지 말고, 지금까지 오간 논의만으로 정리하되 "
                f"아직 검토되지 못한 쟁점을 보고서에 명시하세요.\n"
            )

        missing = ""
        if state.failed_agent_keys:
            # 누가 빠졌는지 알려야, 오케스트레이터가 없는 의견을 있는 것처럼 요약하지 않습니다.
            missing = (
                f"\n[주의] 다음 에이전트는 LLM 연결 실패로 이번 토론에서 발언하지 못했습니다: "
                f"{', '.join(state.failed_agent_keys)}. 이들의 의견을 추측해서 채우지 말고, "
                f"보고서에 누락 사실을 명시하세요.\n"
            )

        prompt = (
            f"[User Goal]: {state.user_prompt}\n\n"
            f"[Full Multi-Agent Debate Transcript]:\n{full_transcript}\n"
            f"{early_stop}"
            f"{missing}\n"
            f"수석 오케스트레이터로서 모든 토론과 피드백을 통합하여 최종 합의 보고서를 작성하세요.\n"
            f"반드시 다음 항목들을 포함해야 합니다:\n"
            f"1. **최종 합의 요약 및 아키텍처 결정 사항 (Summary & Architecture)**\n"
            f"2. **Mermaid 다이어그램** (```mermaid 블록. 노드 라벨에 괄호를 쓸 때는 "
            f'A["결제 서비스 (Payment)"] 처럼 반드시 큰따옴표로 감쌀 것)\n'
            f"3. **완전한 실행 가능 소스 코드** (```python 블록)\n"
            f"4. **품질/보안 점검표 및 엣지 케이스 대응 전략**"
        )
        return [{"role": "user", "content": prompt}]

    def _extract_artifacts_from_synthesis(
        self,
        session_id: str,
        synth_text: str,
        state: DebateState,
        synthesis_failed: bool = False,
    ) -> List[ArtifactItem]:
        """Extracts markdown, code, and mermaid artifacts from the synthesis text."""
        artifacts: List[ArtifactItem] = []

        # 1. Full Synthesized Markdown Document
        artifacts.append(
            ArtifactItem(
                artifact_type="markdown",
                title=(
                    "합성 실패 (LLM 연결 끊김)" if synthesis_failed
                    else "종합 아키텍처 & 산출물 보고서 (Final Synthesis Report)"
                ),
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
                        content=normalize_mermaid(code),
                        language="mermaid",
                    )
                )
                mermaid_idx += 1
            elif lang in CODE_LANGUAGES:
                artifacts.append(
                    ArtifactItem(
                        artifact_type="code",
                        title=f"핵심 구현 소스코드 ({lang}) #{code_idx}",
                        content=code,
                        language=lang if lang not in ["py"] else "python",
                    )
                )
                code_idx += 1

        # 2-b. 합성 보고서에 다이어그램이 없으면 토론 본문에서 찾습니다.
        #      아키텍트가 그린 다이어그램이 최종 보고서에 다시 실리지 않는 경우가
        #      잦고 (답변 길이 제한), 그때마다 다이어그램 탭이 통째로 비었습니다.
        if mermaid_idx == 1:
            for msg in reversed(state.messages):
                if msg.msg_type == "error" or msg.sender_key == "user":
                    continue
                found = [b for b in extract_code_blocks(msg.content) if b["language"] == "mermaid"]
                if not found:
                    continue
                for block in found:
                    artifacts.append(
                        ArtifactItem(
                            artifact_type="mermaid",
                            title=f"시스템 아키텍처 다이어그램 #{mermaid_idx} ({msg.sender_name} 제안)",
                            content=normalize_mermaid(block["code"]),
                            language="mermaid",
                        )
                    )
                    mermaid_idx += 1
                break

        # 3. JSON Summary Artifact
        json_summary = {
            "session_id": session_id,
            "goal": state.user_prompt,
            "strategy": state.strategy,
            "total_rounds": state.current_round,
            "participating_agents": state.active_agent_keys,
            "failed_agents": list(state.failed_agent_keys),
            "total_messages": len(state.messages),
            "consensus_reached": not state.failed_agent_keys,
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
