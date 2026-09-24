import json

from langchain_core.tools import tool

from repositories.source_repository import SourceRepository


def build_source_tools(source_repository: SourceRepository | None):
    if not source_repository:
        return []

    @tool
    def list_source_files(query: str = "") -> str:
        """列出已上传的 Java 源码文件，可用文件名关键词过滤。"""
        return json.dumps(source_repository.list_files(query), ensure_ascii=False)

    @tool
    def get_source_context(file_name: str, line_number: int, window: int = 8) -> str:
        """根据 Java 堆栈中的文件名和行号，读取故障行附近的带行号源码。"""
        result = source_repository.read_context(file_name, line_number, window)
        return json.dumps(result, ensure_ascii=False)

    @tool
    def search_source_code(keyword: str, limit: int = 20) -> str:
        """在已上传的 Java 源码中搜索类名、方法名或代码关键词。"""
        result = source_repository.search(keyword, limit)
        return json.dumps(result, ensure_ascii=False)

    return [list_source_files, get_source_context, search_source_code]
