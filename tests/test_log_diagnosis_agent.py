import unittest

from logpilot.agents.tools import build_agent_tools
from logpilot.agents.tools.log_tools import build_log_tools
from logpilot.core.parser import parse_spring_boot_logs
from logpilot.evaluation.evaluator import evaluate_cases
from logpilot.rag.knowledge_base import split_text
from logpilot.repositories.log_repository import LogRepository
from logpilot.repositories.source_repository import SourceRepository, extract_stack_frames


from tests.fixtures.logs import SAMPLE


class SpringLogParserTests(unittest.TestCase):
    def test_mixed_json_and_text_ids_are_unique_and_context_is_correct(self):
        records = parse_spring_boot_logs(
            '2026-09-19 10:00:00 INFO [shop,text-trace,s1] demo.Order : first\n'
            '{"level":"ERROR","message":"second","traceId":"json-trace"}\n'
            '2026-09-19 10:00:01 INFO [shop,third-trace,s1] demo.Order : third\n'
            '{"level":"WARNING","message":"fourth"}\n'
        )
        self.assertEqual([r.id for r in records], [1, 2, 3, 4])
        self.assertEqual(records[-1].level, "WARN")
        self.assertEqual(LogRepository(records).context(1)["records"][0]["message"], "first")

    def test_long_trace_keeps_the_requested_anchor(self):
        content = '\n'.join(
            f'2026-09-19 10:00:00 INFO [shop,same-trace,s1] demo.Order : record {i}'
            for i in range(80)
        )
        context = LogRepository(parse_spring_boot_logs(content)).context(75)
        self.assertTrue(context["truncated"])
        self.assertEqual(context["total"], 80)
        self.assertEqual(len(context["records"]), 50)
        self.assertIn(75, [r["id"] for r in context["records"]])

    def setUp(self):
        self.records = parse_spring_boot_logs(SAMPLE)
        self.repository = LogRepository(self.records)

    def test_merges_java_stack_trace_and_keeps_line_numbers(self):
        self.assertEqual(3, len(self.records))
        error = self.records[1]
        self.assertEqual("trace-1", error.trace_id)
        self.assertEqual("shop", error.service)
        self.assertEqual(2, error.line_start)
        self.assertEqual(6, error.line_end)
        self.assertEqual("io.lettuce.core.RedisConnectionException", error.exception_type)
        self.assertIn("Caused by", error.stack_trace)

    def test_search_and_context_use_trace_id(self):
        result = self.repository.search(keyword="Redis", level="ERROR")
        self.assertEqual(1, result["total"])
        context = self.repository.context(result["records"][0]["id"])
        self.assertEqual("trace_id", context["mode"])
        self.assertEqual(2, len(context["records"]))

    def test_counts_errors_by_exception(self):
        counts = self.repository.count_errors("exception_type")["counts"]
        self.assertEqual(1, counts["io.lettuce.core.RedisConnectionException"])

    def test_evaluation_requires_exception_trace_and_context(self):
        result = evaluate_cases(
            self.repository,
            [
                {
                    "id": "redis",
                    "name": "Redis",
                    "keyword": "RedisConnectionFailureException",
                    "level": "ERROR",
                    "expected_exception": "io.lettuce.core.RedisConnectionException",
                    "expected_trace_id": "trace-1",
                }
            ],
        )
        self.assertEqual(1, result["passed"])
        self.assertEqual(1.0, result["pass_rate"])

    def test_extracts_java_stack_frame_and_reads_source_line(self):
        frames = extract_stack_frames(self.records[1].stack_trace)
        self.assertEqual("Stock.java", frames[0].file_name)
        self.assertEqual(42, frames[0].line_number)
        sources = SourceRepository(
            {"src/main/java/demo/Stock.java": "class Stock {\n  void check() {\n    load();\n  }\n}"}
        )
        context = sources.read_context("Stock.java", 3, window=1)
        self.assertEqual("src/main/java/demo/Stock.java", context["file"])
        self.assertIn("3 |     load();", context["code"])

    def test_adds_source_tools_only_when_source_is_available(self):
        without_source = {item.name for item in build_log_tools(self.repository)}
        with_source = {
            item.name
            for item in build_agent_tools(
                self.repository,
                SourceRepository({"Stock.java": "class Stock {}"}),
            )
        }
        self.assertNotIn("get_source_context", without_source)
        self.assertIn("get_source_context", with_source)
        self.assertIn("search_source_code", with_source)

    def test_splits_knowledge_document_with_overlap(self):
        text = "A" * 500 + "\n" + "Redis connection troubleshooting " * 30
        chunks = split_text(text, chunk_size=300, overlap=50)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(chunk.strip() for chunk in chunks))

    def test_adds_knowledge_tool_when_database_has_content(self):
        class FakeKnowledgeBase:
            def stats(self):
                return {"chunks": 2, "sources": ["runbook.md"]}

            def search(self, **kwargs):
                return {"total": 1, "results": [{"source": "runbook.md"}]}

        tool_names = {
            item.name
            for item in build_agent_tools(
                self.repository,
                knowledge_base=FakeKnowledgeBase(),
                api_key="test-key",
            )
        }
        self.assertIn("search_knowledge_base", tool_names)


if __name__ == "__main__":
    unittest.main()
