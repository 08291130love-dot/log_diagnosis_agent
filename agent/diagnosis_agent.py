from time import monotonic

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agent.middleware import AgentObservabilityCallback
from agent.tools import build_agent_tools
from model.factory import create_chat_model
from rag.knowledge_base import KnowledgeBase
from repositories.log_repository import LogRepository
from repositories.source_repository import SourceRepository
from utils.prompt_loader import load_prompt


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
) -> dict:
    """Stable UI entry point; the original executor remains available as a baseline."""
    if mode == "multi":
        from agent.workflow import run_multi_diagnosis

        return run_multi_diagnosis(
            repository, question, api_key, base_url, model,
            source_repository, knowledge_base, chat_history, embedding_model,
        )
    if mode != "single":
        raise ValueError("不支持的诊断模式")
    return run_single_diagnosis(
        repository, question, api_key, base_url, model,
        source_repository, knowledge_base, chat_history, embedding_model,
    )


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
) -> dict:
    started = monotonic()
    if not api_key.strip():
        raise ValueError("请先填写百炼 API Key。")
    llm = create_chat_model(api_key, base_url, model)
    tools = build_agent_tools(
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
    result["elapsed"] = round(monotonic() - started, 2)
    result["model_calls"] = observability.model_calls
    result["total_tokens"] = observability.total_tokens
    return result
