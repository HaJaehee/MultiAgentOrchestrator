import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
import litellm
from app.agents.base import Agent
from app.mcp.manager import MCPManager, get_mcp_manager

logger = logging.getLogger(__name__)

# Suppress litellm verbose logging
litellm.suppress_debug_info = True

# Placeholder key for endpoints that require no authentication (Ollama, vLLM, LM Studio...)
LOCAL_API_KEY_PLACEHOLDER = "sk-no-key-required"

# Marker emitted by the sequential-thinking protocol, used to hide steps when show_steps = false
CONCLUSION_MARKERS = ("## 최종 결론", "## Final Conclusion", "## 최종결론")


class LLMUnavailableError(RuntimeError):
    """LLM 엔드포인트에 닿지 못했을 때 올라옵니다.

    예전에는 여기서 내장 시뮬레이터가 그럴듯한 페르소나 답변을 지어냈습니다.
    엔드포인트가 500 을 돌려준 뒤에도 토론은 멀쩡히 굴러가는 것처럼 보였고,
    그 지어낸 발언이 다음 에이전트의 입력과 최종 합성 보고서까지 오염시켰습니다.
    모르는 것은 모른다고 말하는 편이 낫습니다.
    """

    def __init__(self, agent: Agent, reason: str):
        self.agent_key = agent.key
        self.agent_name = agent.name
        self.model = agent.model
        self.endpoint = agent.endpoint_label
        self.reason = reason.strip() or "원인을 확인할 수 없습니다"
        super().__init__(f"{agent.name} ({agent.model} @ {self.endpoint}): {self.reason}")


class ToolCallLog(dict):
    """Dictionary representing a single tool call and its execution result."""
    pass


class LLMCaller:
    """Executes LLM completions with an MCP tool-calling loop.

    호출이 실패하면 `LLMUnavailableError` 를 올립니다. 대체 응답을 만들어 내지 않습니다.
    """

    def __init__(self, mcp_manager: Optional[MCPManager] = None):
        self.mcp_manager = mcp_manager or get_mcp_manager()

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
        on_chunk: Optional[Callable[[str], Any]] = None,
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
        if not agent.is_live:
            raise LLMUnavailableError(
                agent,
                "api_base 도 api_key 도 설정되어 있지 않습니다. conf.toml 의 [llm] 또는 "
                f"[agents.{agent.key}] 에 엔드포인트를 지정하세요.",
            )

        try:
            content, logs = await self._run_litellm_loop(
                agent, formatted_messages, tools, on_tool_call, on_chunk=on_chunk
            )
        except LLMUnavailableError:
            raise
        except Exception as e:
            logger.error(
                f"LLM call failed for {agent.name} "
                f"(model={agent.model}, api_base={agent.api_base or 'provider default'}): {e}"
            )
            raise LLMUnavailableError(agent, f"{type(e).__name__}: {e}") from e

        return self._apply_show_steps(agent, content), logs

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
        on_chunk: Optional[Callable[[str], Any]] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        tool_logs: List[Dict[str, Any]] = []
        current_messages = list(messages)
        iterations = max_tool_iterations or agent.max_tool_iterations

        for iteration in range(iterations):
            kwargs = self.build_completion_kwargs(agent, current_messages, tools)
            streamed_any = False
            try:
                response = await litellm.acompletion(**kwargs, stream=True)
                chunks = []
                async for chunk in response:
                    chunks.append(chunk)
                    content_delta = (
                        chunk.choices[0].delta.content
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content
                        else None
                    )
                    if content_delta and on_chunk:
                        streamed_any = True
                        if asyncio.iscoroutinefunction(on_chunk):
                            await on_chunk(content_delta)
                        else:
                            on_chunk(content_delta)
                complete_response = litellm.stream_chunk_builder(chunks, messages=current_messages)
                choice = complete_response.choices[0]
                message = choice.message
            except Exception as exc:
                # 이미 화면에 흘려보낸 조각이 있으면 비스트리밍으로 다시 부르지 않습니다.
                # 같은 답변이 두 번 붙어 버리고, 무엇보다 이 실패는 삼킬 것이 아니라
                # 발언자에게 그대로 전달되어야 합니다 (LLMUnavailableError).
                if streamed_any:
                    raise
                logger.warning(
                    f"Streaming completion failed or not supported for {agent.name} ({exc}); "
                    f"retrying without stream"
                )
                response = await litellm.acompletion(**kwargs)
                choice = response.choices[0]
                message = choice.message
                if message.content and on_chunk:
                    if asyncio.iscoroutinefunction(on_chunk):
                        await on_chunk(message.content)
                    else:
                        on_chunk(message.content)

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

        raise LLMUnavailableError(
            agent,
            f"MCP 도구 호출을 {iterations}회 반복했는데도 최종 답변이 나오지 않았습니다 "
            f"(max_tool_iterations).",
        )
