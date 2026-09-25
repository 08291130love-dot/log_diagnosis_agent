"""Independent MCP client smoke check; no LLM/Embedding calls or API key required."""
import json
from concurrent.futures import ThreadPoolExecutor
from logpilot.config.paths import PROJECT_ROOT

from logpilot.core.parser import parse_spring_boot_logs
from logpilot.integrations.mcp.client import MCPToolSession
from logpilot.integrations.mcp.context import TOOL_GROUPS
from logpilot.repositories.log_repository import LogRepository
from logpilot.repositories.source_repository import SourceRepository


def main():
    root = PROJECT_ROOT
    repository = LogRepository(parse_spring_boot_logs(
        (root / "samples" / "spring_boot_demo.log").read_text(encoding="utf-8-sig")))
    sources = SourceRepository({p.name: p.read_text(encoding="utf-8-sig")
                                for p in (root / "samples" / "java").glob("*.java")})
    with MCPToolSession(repository, sources) as session:
        names = {name for group in TOOL_GROUPS.values() for name in group}
        assert set(session._tools) == names
        calls = [
            ("search_logs", {"keyword": "Redis"}),
            ("get_log_context", {"record_id": 1}),
            ("count_errors", {}),
            ("list_source_files", {}),
            ("get_source_context", {"file_name": "StockService.java", "line_number": 1}),
            ("search_source_code", {"keyword": "class"}),
            ("search_knowledge_base", {"query": "Redis"}),
        ]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [(name, pool.submit(session.call, name, args)) for name, args in calls]
            for name, future in futures:
                data = json.loads(future.result())
                if data.get("error"):
                    raise RuntimeError(f"MCP check failed: {name}")
                print(f"PASS {name}")
        assert session.metrics()["tool_calls"] == 7
    assert not session.snapshot.path.exists()
    print("PASS service closed and temporary inputs removed")
    print("Knowledge retrieval used an empty store; external Embedding was not called.")


if __name__ == "__main__":
    main()
