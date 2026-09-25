"""Real MCP protocol/subprocess tests with offline model and knowledge fixtures."""
import asyncio
import json
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from langchain_core.messages import AIMessage
from mcp import Client, StdioServerParameters

from logpilot.agents.diagnosis_agent import run_diagnosis
from logpilot.agents.specialists import collect_evidence, run_specialist
from logpilot.core.parser import parse_spring_boot_logs
from logpilot.integrations.mcp.client import MCPConnectionError, MCPToolSession
from logpilot.integrations.mcp.context import DiagnosisSnapshot, TOOL_GROUPS
from logpilot.integrations.mcp.server import create_server
from logpilot.rag.knowledge_base import KnowledgeBase
from logpilot.repositories.log_repository import LogRepository
from logpilot.repositories.source_repository import SourceRepository
from tests.fixtures.logs import SAMPLE
from tests.fixtures.models import ScenarioModel, ToolModel


def repository(text=SAMPLE):
    return LogRepository(parse_spring_boot_logs(text))


class MCPProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = repository()
        cls.sources = SourceRepository({"Stock.java": "class Stock {\n  void check() {}\n}"})
        cls.session = MCPToolSession(cls.repo, cls.sources).__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def call(self, name, arguments):
        return json.loads(self.session.call(name, arguments))

    def test_seven_discovered_tools_and_six_local_results_match(self):
        self.assertEqual(set(self.session._tools), {n for ns in TOOL_GROUPS.values() for n in ns})
        checks = [
            ("search_logs", {"keyword": "Redis"}, self.repo.search(keyword="Redis")),
            ("get_log_context", {"record_id": 2}, self.repo.context(2)),
            ("count_errors", {"group_by": "level"}, self.repo.count_errors("level")),
            ("list_source_files", {}, self.sources.list_files()),
            ("get_source_context", {"file_name": "Stock.java", "line_number": 2}, self.sources.read_context("Stock.java", 2)),
            ("search_source_code", {"keyword": "check"}, self.sources.search("check")),
        ]
        for name, args, expected in checks:
            with self.subTest(tool=name):
                self.assertEqual(self.call(name, args), expected)
        self.assertEqual(self.call("search_knowledge_base", {"query": "Redis"}), {"total": 0, "results": []})

    def test_role_isolation_and_discovered_schema(self):
        for group in ("logs", "sources"):
            tools = self.session.get_tools(group)
            self.assertEqual({t.name for t in tools}, set(TOOL_GROUPS[group]))
            self.assertTrue(all(t.metadata["transport"] == "mcp" for t in tools))
        self.assertEqual(self.session.get_tools("knowledge"), [])
        context = self.session.get_tools("sources")[1]
        self.assertIn("line_number", context.args_schema["required"])
        self.assertEqual(json.loads(context.invoke({"file_name": "Stock.java", "line_number": 1}))["file"], "Stock.java")

    def test_parallel_calls_share_connection_without_mixing_responses(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(self.call, "search_logs", {"trace_id": f"trace-{i % 2 + 1}"}) for i in range(12)]
            for i, future in enumerate(futures):
                rows = future.result()["records"]
                self.assertTrue(rows)
                self.assertTrue(all(r["trace_id"] == f"trace-{i % 2 + 1}" for r in rows))

    def test_bad_parameters_and_paths_do_not_produce_evidence(self):
        invalid = self.call("get_log_context", {"record_id": "not-an-integer"})
        self.assertEqual(invalid["code"], "mcp_tool_error")
        denied = self.call("get_source_context", {"file_name": "../../.env", "line_number": 1})
        self.assertIn("error", denied)
        self.assertNotIn("code", denied)

    def test_independent_requests_and_cleanup(self):
        with MCPToolSession(repository(re.sub("redis", "IsolatedMarker", SAMPLE, flags=re.I))) as other:
            self.assertNotEqual(other.snapshot.request_id, self.session.snapshot.request_id)
            self.assertEqual(other.get_tools("sources"), [])
            self.assertEqual(json.loads(other.call("search_logs", {"keyword": "Redis"}))["total"], 0)
            self.assertGreater(self.call("search_logs", {"keyword": "Redis"})["total"], 0)
        self.assertFalse(other.snapshot.path.parent.exists())
        self.assertTrue(other._lifetime.done())
        self.assertEqual(json.loads(other.call("search_logs", {}))["code"], "mcp_unavailable")

    def test_timeout_and_connection_errors_are_redacted(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)

        previous = self.session.call_timeout
        try:
            self.session.call_timeout = 0.02
            with patch.object(self.session._client, "call_tool", side_effect=slow):
                self.assertEqual(self.call("search_logs", {})["code"], "mcp_timeout")
            with patch.object(self.session._client, "call_tool", side_effect=ConnectionError("secret-key")):
                result = self.call("search_logs", {})
                self.assertEqual(result["code"], "mcp_unavailable")
                self.assertNotIn("secret-key", str(result))
        finally:
            self.session.call_timeout = previous
        self.assertGreater(self.call("search_logs", {"keyword": "Redis"})["total"], 0)

    def test_knowledge_tool_success_and_failure_over_protocol(self):
        class Knowledge:
            def search(self, query, limit):
                if query == "fail":
                    raise RuntimeError("secret-key")
                return {"total": 1, "results": [{"source": "manual.md", "content": query}]}

        async def check():
            async with Client(create_server(self.repo, self.sources, Knowledge())) as client:
                result = await client.call_tool("search_knowledge_base", {"query": "Redis"})
                self.assertEqual(result.structured_content["results"][0]["source"], "manual.md")
                failure = await client.call_tool("search_knowledge_base", {"query": "fail"})
                self.assertEqual(failure.structured_content["code"], "knowledge_unavailable")
                self.assertNotIn("secret-key", str(failure))
        asyncio.run(check())

    def test_default_multi_agent_path_really_uses_mcp(self):
        with patch("logpilot.agents.specialists.create_chat_model", side_effect=lambda *args: ScenarioModel()), \
                patch("logpilot.agents.workflow.synthesize", return_value="依据日志和源码生成报告"):
            result = run_diagnosis(self.repo, "Redis 故障", "offline-key", "unused", "offline",
                                   source_repository=self.sources, tool_transport="mcp")
        self.assertEqual(result["tool_transport"], "mcp")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mcp"]["tool_calls"], 2)
        self.assertEqual([s["status"] for s in result["stages"]], ["completed", "completed", "skipped", "completed"])
        self.assertTrue(collect_evidence(result["intermediate_steps"]))

    def test_single_agent_also_uses_mcp(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"keyword": "Redis"}, "id": "one"}]),
            AIMessage(content="日志 #2 显示连接失败"),
        ])
        with patch("logpilot.agents.diagnosis_agent.create_chat_model", return_value=model):
            result = run_diagnosis(self.repo, "Redis 故障", "offline-key", "unused", "offline", mode="single", tool_transport="mcp")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mcp"]["tool_calls"], 1)

    def test_tool_failure_after_evidence_marks_stage_partial(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"keyword": "Redis"}, "id": "one"}]),
            AIMessage(content="", tool_calls=[{"name": "get_log_context", "args": {"record_id": 99999}, "id": "two"}]),
            AIMessage(content="部分证据"),
        ])
        with patch("logpilot.agents.specialists.create_chat_model", return_value=model):
            result = run_specialist("日志分析", "log_specialist.txt", self.session.get_tools("logs"), {}, "key", "unused", "offline")
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.evidence)


class MCPLifecycleTests(unittest.TestCase):
    def test_chroma_retrieval_and_all_three_agents_through_subprocess(self):
        def fixture_parameters(**kwargs):
            kwargs["args"] = ["-m", "tests.fixtures.mcp_embedding_server"]
            return StdioServerParameters(**kwargs)

        with TemporaryDirectory(prefix="logpilot-mcp-chroma-test-") as folder:
            knowledge = KnowledgeBase(folder)
            try:
                knowledge.collection.upsert(ids=["manual-1"], documents=["Redis connection refused: check address and connectivity"],
                                            embeddings=[[1.0, 0.0, 0.0]], metadatas=[{"source": "manual.md", "chunk_index": 0}])
                with patch("logpilot.integrations.mcp.client.StdioServerParameters", side_effect=fixture_parameters), \
                        patch("logpilot.agents.specialists.create_chat_model", side_effect=lambda *args: ScenarioModel()), \
                        patch("logpilot.agents.workflow.create_chat_model", side_effect=lambda *args: ScenarioModel()):
                    result = run_diagnosis(repository(), "Redis 故障", "offline-key", "https://embedding.invalid/v1", "offline",
                                           SourceRepository({"Stock.java": "class Stock {}"}), knowledge,
                                           embedding_model="offline-embedding", tool_transport="mcp")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["mcp"]["tool_calls"], 3)
                self.assertEqual(result["model_calls"], 7)
                self.assertEqual(result["stages"][2]["evidence"][0]["data"]["results"][0]["source"], "manual.md")
                self.assertNotIn("offline-key", json.dumps(result, ensure_ascii=False, default=str))
            finally:
                knowledge.client._system.stop()

    def test_startup_failure_cleans_snapshot_and_hides_details(self):
        session = MCPToolSession(repository(), api_key="secret-key")
        with patch("logpilot.integrations.mcp.client.Client", side_effect=RuntimeError("secret-key")):
            with self.assertRaises(MCPConnectionError) as caught:
                session.__enter__()
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertFalse(session.snapshot.path.parent.exists())
        self.assertEqual(session._credentials, {})

    def test_startup_timeout_is_bounded_and_removes_snapshot(self):
        class HangingClient:
            async def __aenter__(self):
                await asyncio.sleep(10)

            async def __aexit__(self, *args):
                pass

        session = MCPToolSession(repository(), startup_timeout=0.02)
        with patch("logpilot.integrations.mcp.client.Client", return_value=HangingClient()):
            with self.assertRaises(MCPConnectionError):
                session.__enter__()
        self.assertFalse(session.snapshot.path.parent.exists())

    def test_context_file_has_no_credential_fields(self):
        with DiagnosisSnapshot(repository()) as snapshot:
            payload = json.loads(snapshot.path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"request_id", "records", "sources", "knowledge"})
        self.assertFalse(snapshot.path.parent.exists())

    def test_mcp_failure_does_not_silently_fallback_to_local(self):
        with patch("logpilot.integrations.mcp.client.MCPToolSession.__enter__", side_effect=MCPConnectionError("MCP 不可用")), \
                patch("logpilot.agents.diagnosis_agent.run_single_diagnosis") as execute:
            with self.assertRaises(MCPConnectionError):
                run_diagnosis(repository(), "排查", "key", "unused", "offline", mode="single", tool_transport="mcp")
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
