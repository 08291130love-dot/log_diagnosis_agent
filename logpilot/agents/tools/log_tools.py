import json

from langchain_core.tools import tool

from logpilot.repositories.log_repository import LogRepository


def build_log_tools(repository: LogRepository):
    @tool
    def search_logs(keyword: str = "", level: str = "", trace_id: str = "", limit: int = 20) -> str:
        """按关键词、日志级别或 traceId 搜索日志。首次调查通常先用该工具。"""
        result = repository.search(keyword=keyword, level=level, trace_id=trace_id, limit=limit)
        return json.dumps(result, ensure_ascii=False)

    @tool
    def get_log_context(record_id: int, window: int = 3) -> str:
        """查询某条日志的上下文；有 traceId 时返回同一请求链路，否则返回相邻日志。"""
        result = repository.context(record_id=record_id, window=window)
        return json.dumps(result, ensure_ascii=False)

    @tool
    def count_errors(group_by: str = "exception_type") -> str:
        """统计错误，可按 exception_type、level 或 service 分组。"""
        result = repository.count_errors(group_by=group_by)
        return json.dumps(result, ensure_ascii=False)

    return [search_logs, get_log_context, count_errors]
