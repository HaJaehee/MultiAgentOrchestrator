import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
import litellm
from app.agents.base import Agent
from app.config import LLMConfig, get_config
from app.mcp.manager import MCPManager, get_mcp_manager

logger = logging.getLogger(__name__)

# Suppress litellm verbose logging
litellm.suppress_debug_info = True

# Placeholder key for endpoints that require no authentication (Ollama, vLLM, LM Studio...)
LOCAL_API_KEY_PLACEHOLDER = "sk-no-key-required"

# Marker emitted by the sequential-thinking protocol, used to hide steps when show_steps = false
CONCLUSION_MARKERS = ("## 최종 결론", "## Final Conclusion", "## 최종결론")


class ToolCallLog(dict):
    """Dictionary representing a single tool call and its execution result."""
    pass


class LLMCaller:
    """Executes LLM completions with Tool Calling loop and offline Simulation Fallback."""

    def __init__(self, mcp_manager: Optional[MCPManager] = None, llm_config: Optional[LLMConfig] = None):
        self.mcp_manager = mcp_manager or get_mcp_manager()
        self._llm_config = llm_config

    @property
    def llm_config(self) -> LLMConfig:
        """Global [llm] settings, loaded lazily so the caller works without a config file too."""
        if self._llm_config is None:
            try:
                self._llm_config = get_config().llm
            except Exception:  # pragma: no cover - config missing in isolated tests
                self._llm_config = LLMConfig()
        return self._llm_config

    def build_system_prompt(self, agent: Agent, custom_instructions: str = "") -> str:
        """System prompt = persona + sequential thinking protocol + session instructions."""
        parts = [agent.system_prompt]

        st = agent.sequential_thinking
        if st.enabled and st.mode in ("prompt", "mcp"):
            parts.append(st.render_prompt())
            if st.mode == "mcp":
                parts.append(
                    f"각 사고 단계는 반드시 '{st.mcp_server}' MCP 서버의 sequentialthinking 도구를 호출해 기록한 뒤 진행하세요."
                )

        if custom_instructions:
            parts.append(f"[Session Custom Instructions]:\n{custom_instructions}")

        return "\n\n".join(p for p in parts if p)

    def resolve_tool_servers(self, agent: Agent) -> List[str]:
        """MCP servers this agent may use, including the sequential-thinking server when required."""
        servers = list(agent.allowed_mcp_servers)
        st = agent.sequential_thinking
        if st.enabled and st.mode == "mcp" and st.mcp_server not in servers:
            servers.append(st.mcp_server)
        return servers

    async def call_agent(
        self,
        agent: Agent,
        messages: List[Dict[str, Any]],
        custom_instructions: str = "",
        on_tool_call: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Executes a turn for the given agent.
        Returns (response_text, tool_call_logs).
        """
        formatted_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt(agent, custom_instructions)}
        ]
        formatted_messages.extend(messages)

        # Retrieve available tools for this agent
        tools = self.mcp_manager.get_openai_tools_for_servers(self.resolve_tool_servers(agent))

        # Real endpoint if an API URL, an API key, or a keyless local runtime is configured
        if agent.is_live:
            try:
                content, logs = await self._run_litellm_loop(agent, formatted_messages, tools, on_tool_call)
                return self._apply_show_steps(agent, content), logs
            except Exception as e:
                detail = (
                    f"LLM call failed for {agent.name} "
                    f"(model={agent.model}, api_base={agent.api_base or 'provider default'}): {e}"
                )
                if not self.llm_config.fallback_to_simulation:
                    logger.error(detail)
                    raise RuntimeError(detail) from e
                logger.warning(f"{detail}. Falling back to simulation mode.")
        else:
            logger.info(
                f"Agent '{agent.key}' has no api_base/api_key configured (model={agent.model}); "
                f"using the offline simulator."
            )

        # Fallback Simulation Mode
        return await self._run_simulated_turn(agent, formatted_messages, tools, on_tool_call)

    def _apply_show_steps(self, agent: Agent, content: str) -> str:
        """Strips the reasoning steps when sequential_thinking.show_steps is disabled."""
        st = agent.sequential_thinking
        if not content or not st.enabled or st.show_steps:
            return content
        for marker in CONCLUSION_MARKERS:
            idx = content.find(marker)
            if idx != -1:
                return content[idx:].strip()
        return content

    def build_completion_kwargs(
        self,
        agent: Agent,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Maps the agent configuration onto LiteLLM completion parameters."""
        kwargs: Dict[str, Any] = {
            "model": agent.model,
            "messages": messages,
            "temperature": agent.temperature,
            "max_tokens": agent.max_tokens,
        }

        # Endpoint / credentials
        api_key = (agent.api_key or "").strip()
        if not api_key and agent.api_base:
            # Keyless local servers still need a non-empty value for OpenAI-compatible clients
            api_key = LOCAL_API_KEY_PLACEHOLDER
        if api_key:
            kwargs["api_key"] = api_key
        if agent.api_base:
            kwargs["api_base"] = agent.api_base
        if agent.api_version:
            kwargs["api_version"] = agent.api_version
        if agent.provider:
            kwargs["custom_llm_provider"] = agent.provider

        # Sampling & transport options
        if agent.top_p is not None:
            kwargs["top_p"] = agent.top_p
        if agent.timeout:
            kwargs["timeout"] = agent.timeout
        if agent.num_retries:
            kwargs["num_retries"] = agent.num_retries
        if agent.drop_params:
            kwargs["drop_params"] = True
        if agent.extra_headers:
            kwargs["extra_headers"] = dict(agent.extra_headers)
        if agent.extra_body:
            kwargs["extra_body"] = dict(agent.extra_body)

        # Native (provider-side) sequential thinking
        st = agent.sequential_thinking
        if st.enabled and st.mode == "native":
            if st.reasoning_effort:
                kwargs["reasoning_effort"] = st.reasoning_effort
            if st.thinking_budget_tokens:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": st.thinking_budget_tokens}
                # Anthropic extended thinking requires temperature = 1
                if "claude" in agent.model.lower() or "anthropic" in agent.model.lower():
                    kwargs["temperature"] = 1.0
                if agent.max_tokens <= st.thinking_budget_tokens:
                    kwargs["max_tokens"] = st.thinking_budget_tokens + agent.max_tokens

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        return kwargs

    def _compose_content(self, agent: Agent, message: Any) -> str:
        """Merges provider-side reasoning traces (native thinking) with the answer text."""
        content = getattr(message, "content", None) or ""
        if isinstance(content, list):  # some providers return content blocks
            content = "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block) for block in content
            )

        st = agent.sequential_thinking
        reasoning = getattr(message, "reasoning_content", None) or ""
        if st.enabled and st.mode == "native" and st.show_steps and reasoning:
            content = f"> **[Sequential Thinking]**\n>\n> {reasoning.strip().replace(chr(10), chr(10) + '> ')}\n\n{content}"

        return content

    async def _run_litellm_loop(
        self,
        agent: Agent,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_tool_call: Optional[Callable[[Dict[str, Any]], Any]] = None,
        max_tool_iterations: Optional[int] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        tool_logs: List[Dict[str, Any]] = []
        current_messages = list(messages)
        iterations = max_tool_iterations or agent.max_tool_iterations

        for iteration in range(iterations):
            kwargs = self.build_completion_kwargs(agent, current_messages, tools)
            response = await litellm.acompletion(**kwargs)
            choice = response.choices[0]
            message = choice.message

            # Check for tool calls
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                return self._compose_content(agent, message), tool_logs

            # Append assistant message with tool calls to context
            current_messages.append(message.model_dump() if hasattr(message, "model_dump") else dict(message))

            # Execute all requested tool calls
            for tc in tool_calls:
                fn_name = tc.function.name
                fn_args_raw = tc.function.arguments
                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                except Exception:
                    fn_args = {"raw": fn_args_raw}

                output, status = await self.mcp_manager.execute_tool(fn_name, fn_args)
                call_log = {
                    "tool_name": fn_name,
                    "arguments": fn_args,
                    "output": output,
                    "status": status,
                }
                tool_logs.append(call_log)
                if on_tool_call:
                    if asyncio.iscoroutinefunction(on_tool_call):
                        await on_tool_call(call_log)
                    else:
                        on_tool_call(call_log)

                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": output,
                })

        return "Tool execution iterations reached maximum limit.", tool_logs

    async def _run_simulated_turn(
        self,
        agent: Agent,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_tool_call: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Generates contextual simulated response and mock tool calls when LLM API keys are absent."""
        await asyncio.sleep(0.6)  # Simulate network latency

        tool_logs: List[Dict[str, Any]] = []

        # Find latest user prompt
        user_prompt = "요청사항"
        for m in reversed(messages):
            if m.get("role") == "user":
                user_prompt = m.get("content", "요청사항")
                break

        # Simulate MCP tool call for coder or orchestrator if filesystem/search is available
        if agent.key in ["coder", "architect", "orchestrator"] and tools:
            target_tool = tools[0]["function"]["name"]
            sample_args = {"path": "./workspace/main.py"} if "file" in target_tool else {"query": user_prompt[:30]}
            output, status = await self.mcp_manager.execute_tool(target_tool, sample_args)
            call_log = {
                "tool_name": target_tool,
                "arguments": sample_args,
                "output": output,
                "status": status,
            }
            tool_logs.append(call_log)
            if on_tool_call:
                if asyncio.iscoroutinefunction(on_tool_call):
                    await on_tool_call(call_log)
                else:
                    on_tool_call(call_log)

        # Generate realistic domain response based on agent persona
        if agent.key == "orchestrator":
            response_content = (
                f"### [Orchestrator 분석 & 조율]\n\n"
                f"사용자의 요청 **「{user_prompt}」**을 분석했습니다.\n\n"
                f"1. **목표 정의**: 요구사항을 충족하는 최적의 아키텍처 설계 및 구현 코드 도출\n"
                f"2. **전문가 발언 배정**:\n"
                f"   - **System Architect**: 핵심 모듈 설계, 기술 스택 및 데이터 흐름 다이어그램 작성\n"
                f"   - **Senior Python Engineer**: 클린 코드 기반 실제 핵심 구현 및 인터페이스 작성\n"
                f"   - **Security & Quality Critic**: 안정성, 보안 취약점 및 엣지 케이스 검증\n\n"
                f"먼저 System Architect께서 전체적인 구조와 Mermaid 다이어그램을 제안해 주십시오."
            )
        elif agent.key == "architect":
            response_content = (
                f"### [System Architect 아키텍처 설계]\n\n"
                f"**「{user_prompt}」**을 위한 고가용성/확장형 모듈 구조를 제안합니다.\n\n"
                f"#### 1. 핵심 컴포넌트 구조\n"
                f"- **Core Controller**: 이벤트 디스패치 및 비즈니스 로직 제어\n"
                f"- **Data Layer**: 비동기 I/O 및 데이터 무결성 보장\n"
                f"- **Service Interface**: 확장 가능한 플러그인/모듈형 API\n\n"
                f"```mermaid\n"
                f"graph TD\n"
                f"    Client[Client / Interface] --> API[FastAPI Controller]\n"
                f"    API --> Core[Domain Core Engine]\n"
                f"    Core --> Worker[Async Task Worker]\n"
                f"    Core --> Repo[(Storage Repository)]\n"
                f"```\n\n"
                f"시니어 엔지니어께서는 이 구조를 바탕으로 구현체를 작성해 주시고, 크리틱의 검토를 받도록 하겠습니다."
            )
        elif agent.key == "coder":
            response_content = (
                f"### [Senior Python Engineer 구현]\n\n"
                f"아키텍트의 설계를 반영하여 타입 힌팅과 비동기 처리가 완비된 핵심 모듈을 구현했습니다.\n\n"
                f"```python\n"
                f"from typing import Any, Dict, List, Optional\n"
                f"import asyncio\n"
                f"import logging\n\n"
                f"logger = logging.getLogger(__name__)\n\n"
                f"class CoreServiceEngine:\n"
                f"    \"\"\"핵심 비즈니스 로직 및 비동기 파이프라인 처리기\"\"\"\n\n"
                f"    def __init__(self, config: Optional[Dict[str, Any]] = None):\n"
                f"        self.config = config or {{}}\n"
                f"        self.is_running = False\n\n"
                f"    async def initialize(self) -> None:\n"
                f"        logger.info('Initializing service components...')\n"
                f"        self.is_running = True\n\n"
                f"    async def process_task(self, task_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:\n"
                f"        if not self.is_running:\n"
                f"            raise RuntimeError('Engine is not initialized')\n"
                f"        logger.info(f'Processing: {{task_name}}')\n"
                f"        await asyncio.sleep(0.05)  # Async non-blocking execution\n"
                f"        return {{'status': 'success', 'task': task_name, 'result': payload}}\n"
                f"```\n\n"
                f"MCP 파일시스템에 스켈레톤 작성을 완료했습니다. Security Critic의 코드 리뷰를 부탁드립니다."
            )
        elif agent.key == "critic":
            response_content = (
                f"### [Security & Quality Critic 리뷰]\n\n"
                f"제시된 설계와 소스코드를 철저히 검토했습니다.\n\n"
                f"#### 검토 결과 및 제안사항:\n"
                f"1. **리소스 해제 보장**: `CoreServiceEngine`에 컨텍스트 매니저(`__aenter__`, `__aexit__`) 및 `shutdown()` 메서드를 추가하여 우아한 종료(Graceful Shutdown)를 보장해야 합니다.\n"
                f"2. **입력 유효성 검증**: `process_task`의 payload에 대해 Pydantic 스키마 검증을 적용하여 예외 상황을 선제 방어하세요.\n"
                f"3. **종합 평가**: 전반적인 구조와 모듈 분리는 우수하며, 위 예외 핸들링을 보완하면 배포 준비가 완료됩니다."
            )
        else:
            response_content = f"[{agent.name} 발언]\n요청 사항 **「{user_prompt}」**에 대해 전문적인 관점에서 검토 및 의견을 제출합니다."

        return response_content, tool_logs
