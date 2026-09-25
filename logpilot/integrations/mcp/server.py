"""Seven read-only MCP tools. Run with python -m logpilot.integrations.mcp.server."""
import json
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any

from mcp.server import MCPServer
from mcp_types import ToolAnnotations

from logpilot.core.models import LogRecord
from logpilot.repositories.log_repository import LogRepository
from logpilot.repositories.source_repository import SourceRepository


class LazyKnowledge:
    """Open the configured collection on demand, without blocking log investigation."""
    def __init__(self, config, api_key, base_url, embedding_model):
        self.config = config
        self.api_key = api_key
        self.base_url = base_url
        self.embedding_model = embedding_model
        self._store = None
        self._lock = Lock()

    def search(self, query, limit=3):
        if not self.config:
            return {"total": 0, "results": []}
        try:
            with self._lock:
                if self._store is None:
                    from logpilot.rag.knowledge_base import KnowledgeBase

                    directory = Path(self.config["directory"])
                    if not (directory / "chroma.sqlite3").is_file():
                        return {"error": "知识库文件不存在，请重新检查知识库。", "code": "knowledge_unavailable"}
                    self._store = KnowledgeBase(directory, self.config["collection"])
            return self._store.search(query, self.api_key, self.base_url, self.embedding_model, limit)
        except Exception:
            # Neither API credentials nor raw provider responses cross the tool boundary.
            return {"error": "知识库检索失败，请检查 Embedding 配置、网络及知识库。", "code": "knowledge_unavailable"}


def create_server(repository, sources=None, knowledge=None):
    server = MCPServer("LogPilot Diagnostic Tools")
    sources = sources or SourceRepository()
    readonly = ToolAnnotations(read_only_hint=True, destructive_hint=False)

    @server.tool(annotations=readonly)
    def search_logs(keyword: str = "", level: str = "", trace_id: str = "", limit: int = 20) -> dict[str, Any]:
        """按关键词、日志级别或 traceId 搜索本次诊断日志。首次调查通常先用该工具。"""
        return repository.search(keyword, level, trace_id, limit)

    @server.tool(annotations=readonly)
    def get_log_context(record_id: int, window: int = 3) -> dict[str, Any]:
        """查询日志上下文；有 traceId 时关联同一请求，否则返回相邻日志。"""
        return repository.context(record_id, window)

    @server.tool(annotations=readonly)
    def count_errors(group_by: str = "exception_type") -> dict[str, Any]:
        """按 exception_type、level 或 service 分组统计当前日志。"""
        return repository.count_errors(group_by)

    @server.tool(annotations=readonly)
    def list_source_files(query: str = "") -> dict[str, Any]:
        """列出本次诊断上传的 Java 文件，可按文件名关键词过滤。"""
        return sources.list_files(query)

    @server.tool(annotations=readonly)
    def get_source_context(file_name: str, line_number: int, window: int = 8) -> dict[str, Any]:
        """按堆栈文件名和行号读取已上传的 Java 源码，不读取任意磁盘文件。"""
        return sources.read_context(file_name, line_number, window)

    @server.tool(annotations=readonly)
    def search_source_code(keyword: str, limit: int = 20) -> dict[str, Any]:
        """在本次上传的 Java 源码中搜索类名、方法名或代码关键词。"""
        return sources.search(keyword, limit)

    @server.tool(annotations=readonly)
    def search_knowledge_base(query: str, limit: int = 3) -> dict[str, Any]:
        """从配置的故障知识库语义检索历史故障、运维手册与解决方案。"""
        if knowledge is None:
            return {"total": 0, "results": []}
        try:
            return knowledge.search(query=query, limit=limit)
        except Exception:
            return {"error": "知识库检索失败，请检查配置或网络。", "code": "knowledge_unavailable"}

    return server


def main():
    try:
        snapshot = Path(os.environ["LOGPILOT_MCP_CONTEXT"])
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if payload["request_id"] != os.environ["LOGPILOT_MCP_REQUEST_ID"]:
            raise ValueError("Request mismatch")
        snapshot.unlink()  # Loaded inputs now live only in this child process.
        repository = LogRepository([LogRecord(**r) for r in payload["records"]])
        sources = SourceRepository(payload["sources"])
        knowledge = LazyKnowledge(
            payload.get("knowledge"), os.environ.get("LOGPILOT_MCP_API_KEY", ""),
            os.environ.get("LOGPILOT_MCP_BASE_URL", ""),
            os.environ.get("LOGPILOT_MCP_EMBEDDING_MODEL", "text-embedding-v4"),
        )
        create_server(repository, sources, knowledge).run(transport="stdio")
    except Exception as exc:
        print(f"LogPilot MCP service stopped ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
