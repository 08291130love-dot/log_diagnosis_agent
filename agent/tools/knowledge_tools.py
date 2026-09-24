import json

from langchain_core.tools import tool

from rag.knowledge_base import KnowledgeBase


def build_knowledge_tools(
    knowledge_base: KnowledgeBase | None,
    api_key: str,
    base_url: str,
    embedding_model: str,
):
    if not knowledge_base or not knowledge_base.stats()["chunks"]:
        return []

    @tool
    def search_knowledge_base(query: str, limit: int = 3) -> str:
        """从历史故障、运维手册和解决方案中语义检索相关经验。"""
        try:
            result = knowledge_base.search(
                query=query,
                api_key=api_key,
                base_url=base_url,
                embedding_model=embedding_model,
                limit=limit,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": f"知识库检索失败：{exc}"}, ensure_ascii=False)

    return [search_knowledge_base]
