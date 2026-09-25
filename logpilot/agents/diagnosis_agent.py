import json
import os
from time import monotonic

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from logpilot.agents.middleware import AgentObservabilityCallback
from logpilot.agents.tools import build_agent_tools
from logpilot.llm.factory import create_chat_model
from logpilot.rag.knowledge_base import KnowledgeBase
from logpilot.repositories.log_repository import LogRepository
from logpilot.repositories.source_repository import SourceRepository
from logpilot.prompts.loader import load_prompt


def run_diagnosis(
    repository: LogRepository,
    question: str,
    api_key: str,
    base_url: str,
    model: str,
    source_repository: SourceRepository | None = None,
    knowledge_base: KnowledgeBase | None = None,
    chat_history: list[dict] | None = None,
    embedding_model: str = "text-embedding-v4",
    mode: str = "multi",
    tool_transport: str | None = None,
) -> dict:
    """UI and CLI entry point. MCP is default; local is an explicit comparison path."""
    if mode not in {"single", "multi"}:
        raise ValueError("不支持的诊断模式")
    if not api_key.strip():
        raise ValueError("请先填写百炼 API Key。")
    if not question.strip():
        raise ValueError("请输入需要排查的问题。")
    transport = tool_transport or os.getenv("LOGPILOT_TOOL_TRANSPORT", "mcp")
    if transport not in {"mcp", "local"}:
        raise ValueError("工具通信方式必须为 mcp 或 local")
    from logpilot.agents.workflow import run_multi_diagnosis

    execute = run_multi_diagnosis if mode == "multi" else run_single_diagnosis
    args = (repository, question, api_key, base_url, model,
            source_repository, knowledge_base, chat_history, embedding_model)
    if transport == "local":
        result = execute(*args)
    else:
        from logpilot.integrations.mcp.client import MCPToolSession

        started = monotonic()
        with MCPToolSession(repository, source_repository, knowledge_base,
                            api_key, base_url, embedding_model) as session:
            result = execute(*args, tool_provider=session.get_tools)
            result["mcp"] = session.metrics()
        result["elapsed"] = round(monotonic() - started, 2)
    result["tool_transport"] = transport
    return result


def run_single_diagnosis(
    repository: LogRepository,
    question: str,
    api_key: str,
    base_url: str,
    model: str,
    source_repository: SourceRepository | None = None,
    knowledge_base: KnowledgeBase | None = None,
    chat_history: list[dict] | None = None,
    embedding_model: str = "text-embedding-v4",
    tool_provider=None,
) -> dict:
    started = monotonic()
    if not api_key.strip():
        raise ValueError("请先填写百炼 API Key。")
    llm = create_chat_model(api_key, base_url, model)
    tools = tool_provider("all") if tool_provider else build_agent_tools(
        repository,
        source_repository,
        knowledge_base,
        api_key,
        base_url,
        embedding_model,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", load_prompt("diagnosis_prompt.txt")),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    executor = AgentExecutor(
        agent=create_openai_tools_agent(llm, tools, prompt),
        tools=tools,
        max_iterations=10,
        max_execution_time=180,
        return_intermediate_steps=True,
        verbose=False,
        handle_parsing_errors=True,
    )
    history_messages = []
    for turn in (chat_history or [])[-4:]:
        history_messages.append(HumanMessage(content=str(turn.get("question", ""))))
        history_messages.append(AIMessage(content=str(turn.get("output", ""))))
    observability = AgentObservabilityCallback()
    result = executor.invoke(
        {"input": question, "chat_history": history_messages},
        config={"callbacks": [observability]},
    )
    result["observability_events"] = observability.events
    result["mode"] = "single"
    output = str(result.get("output", "")).strip()
    result["status"] = "partial" if not output or "Agent stopped" in output else "completed"
    for _, observation in result.get("intermediate_steps", []):
        try:
            data = json.loads(observation) if isinstance(observation, str) else observation
            if isinstance(data, dict) and data.get("error"):
                result["status"] = "partial"
        except (TypeError, ValueError):
            result["status"] = "partial"
    result["elapsed"] = round(monotonic() - started, 2)
    result["model_calls"] = observability.model_calls
    result["total_tokens"] = observability.total_tokens
    return result
