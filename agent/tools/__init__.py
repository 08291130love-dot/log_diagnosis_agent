from rag.knowledge_base import KnowledgeBase
from repositories.log_repository import LogRepository
from repositories.source_repository import SourceRepository

from .knowledge_tools import build_knowledge_tools
from .log_tools import build_log_tools
from .source_tools import build_source_tools


def build_agent_tools(
    repository: LogRepository,
    source_repository: SourceRepository | None = None,
    knowledge_base: KnowledgeBase | None = None,
    api_key: str = "",
    base_url: str = "",
    embedding_model: str = "text-embedding-v4",
):
    tools = build_log_tools(repository)
    tools.extend(build_source_tools(source_repository))
    tools.extend(
        build_knowledge_tools(
            knowledge_base,
            api_key,
            base_url,
            embedding_model,
        )
    )
    return tools


__all__ = ["build_agent_tools"]
