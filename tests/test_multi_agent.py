import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage

from logpilot.agents.diagnosis_agent import run_diagnosis
from logpilot.agents.middleware import AgentObservabilityCallback
from logpilot.agents.specialists import collect_evidence, run_specialist
from logpilot.agents.state import StageResult
from logpilot.agents.tools.log_tools import build_log_tools
from logpilot.agents.workflow import run_multi_diagnosis
from logpilot.core.parser import parse_spring_boot_logs
from logpilot.repositories.log_repository import LogRepository
from logpilot.repositories.source_repository import SourceRepository
from tests.fixtures.logs import SAMPLE
from tests.fixtures.models import FakeKnowledge, ScenarioModel, ToolModel


class MultiAgentTests(unittest.TestCase):
    def test_streaming_usage_is_counted_without_double_counting(self):
        observer = AgentObservabilityCallback()
        message = AIMessage(content="result", usage_metadata={"input_tokens": 8, "output_tokens": 2, "total_tokens": 10})
        response = SimpleNamespace(llm_output=None, generations=[[SimpleNamespace(message=message)]])
        observer.on_llm_end(response)
        self.assertEqual(observer.total_tokens, 10)
        response.llm_output = {"token_usage": {"total_tokens": 10}}
        observer.on_llm_end(response)
        self.assertEqual(observer.total_tokens, 20)

    def setUp(self):
        self.repo = LogRepository(parse_spring_boot_logs(SAMPLE))
        self.sources = SourceRepository({"Stock.java": "class Stock {}"})
        self.kwargs = dict(repository=self.repo, question="为什么失败", api_key="test-key", base_url="test", model="test")

    def log_result(self):
        return StageResult("日志分析", "completed", findings="日志 #2（原始第 2-6 行）",
                           evidence=[{"tool": "search_logs", "data": self.repo.search(keyword="Redis")}])

    def test_real_executor_calls_tool_and_hands_off_observation(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"keyword": "Redis"}, "id": "search-1"}]),
            AIMessage(content="日志 #2（原始第 2-6 行）显示连接失败。"),
        ])
        with patch("logpilot.agents.specialists.create_chat_model", return_value=model):
            result = run_specialist("日志分析", "log_specialist.txt", build_log_tools(self.repo),
                                    {"question": "Redis 故障"}, "test", "test", "test")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.evidence[0]["data"]["records"][0]["id"], 2)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(len(result.steps), 1)

    def test_full_workflow_uses_all_executors_tools_and_synthesis(self):
        with patch("logpilot.agents.specialists.create_chat_model", side_effect=lambda *args: ScenarioModel()), patch("logpilot.agents.workflow.create_chat_model", side_effect=lambda *args: ScenarioModel()):
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources, knowledge_base=FakeKnowledge())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["intermediate_steps"]), 3)
        self.assertEqual(result["model_calls"], 7)
        self.assertEqual(result["total_tokens"], 70)
        self.assertEqual([s["status"] for s in result["stages"]], ["completed"] * 4)

    def test_unsupported_model_answer_is_not_evidence(self):
        with patch("logpilot.agents.specialists.create_chat_model", return_value=ToolModel(responses=[AIMessage(content="数据库已经宕机")] )):
            result = run_specialist("日志分析", "log_specialist.txt", build_log_tools(self.repo), {}, "test", "test", "test")
        self.assertEqual(result.status, "no_evidence")
        self.assertEqual(result.findings, "")

    def test_iteration_limit_preserves_evidence_without_claiming_completion(self):
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"keyword": "Redis"}, "id": "again"}])])
        with patch("logpilot.agents.specialists.create_chat_model", return_value=model):
            result = run_specialist("日志分析", "log_specialist.txt", build_log_tools(self.repo), {}, "test", "test", "test")
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.steps), 5)
        self.assertTrue(result.evidence)
        self.assertEqual(result.findings, "")

    def test_handoff_keeps_whole_records_and_declares_truncation(self):
        stage = StageResult("日志分析", "completed", evidence=[{
            "tool": "search_logs", "data": {"records": [{"id": n, "message": "x" * 4000} for n in range(50)]}
        }])
        data = stage.handoff()["evidence"][0]["data"]
        self.assertTrue(data["handoff_truncated"])
        self.assertEqual(len(data["records"][0]["message"]), 4000)
        self.assertLess(len(data["records"]), 50)

    def test_optional_workers_run_in_parallel_with_isolated_tools(self):
        barrier = threading.Barrier(2, timeout=3)
        received = {}

        def worker(name, prompt, tools, payload, *args):
            received[name] = ({t.name for t in tools}, payload)
            if name == "日志分析":
                return self.log_result()
            barrier.wait()  # A sequential implementation fails this test.
            return StageResult(name, "completed", evidence=[{"data": {"code": "proof"}}])

        with patch("logpilot.agents.workflow.run_specialist", side_effect=worker), patch("logpilot.agents.workflow.synthesize", return_value="报告") as summary:
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources, knowledge_base=FakeKnowledge())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(received["日志分析"][0], {"search_logs", "get_log_context", "count_errors"})
        self.assertEqual(received["源码定位"][0], {"get_source_context", "list_source_files", "search_source_code"})
        self.assertEqual(received["故障知识"][0], {"search_knowledge_base"})
        self.assertIn("log_investigation", received["源码定位"][1])
        self.assertEqual(len(summary.call_args.args[0]["investigations"]), 3)

    def test_missing_inputs_skip_without_worker_calls(self):
        with patch("logpilot.agents.workflow.run_specialist", return_value=self.log_result()) as worker, patch("logpilot.agents.workflow.synthesize", return_value="报告"):
            result = run_multi_diagnosis(**self.kwargs)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual([s["status"] for s in result["stages"]], ["completed", "skipped", "skipped", "completed"])

    def test_log_failure_prevents_downstream_fabrication(self):
        with patch("logpilot.agents.workflow.run_specialist", return_value=StageResult("日志分析", "failed", reason="超时")) as worker, patch("logpilot.agents.workflow.synthesize") as summary:
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources, knowledge_base=FakeKnowledge())
        self.assertEqual(worker.call_count, 1)
        summary.assert_not_called()
        self.assertEqual(result["status"], "partial")

    def test_optional_failure_still_generates_limited_report(self):
        def worker(name, *args):
            return self.log_result() if name == "日志分析" else StageResult(name, "failed", reason="超时")
        with patch("logpilot.agents.workflow.run_specialist", side_effect=worker), patch("logpilot.agents.workflow.synthesize", return_value="缺少源码依据") as summary:
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["output"], "缺少源码依据")
        self.assertEqual(summary.call_args.args[0]["investigations"][1]["status"], "failed")

    def test_summary_failure_preserves_evidence_and_hides_secret(self):
        with patch("logpilot.agents.workflow.run_specialist", return_value=self.log_result()), patch("logpilot.agents.workflow.synthesize", side_effect=RuntimeError("secret-key")):
            result = run_multi_diagnosis(**self.kwargs)
        self.assertEqual(result["stages"][-1]["status"], "failed")
        self.assertTrue(result["stages"][0]["evidence"])
        self.assertNotIn("secret-key", json.dumps(result))

    def test_history_is_bounded_and_only_context(self):
        history = [{"question": str(i), "output": "previous answer"} for i in range(7)]
        with patch("logpilot.agents.workflow.run_specialist", return_value=self.log_result()) as worker, patch("logpilot.agents.workflow.synthesize", return_value="报告"):
            run_multi_diagnosis(**self.kwargs, chat_history=history)
        self.assertEqual([t["question"] for t in worker.call_args.args[3]["history_for_reference_only"]], ["3", "4", "5", "6"])

    def test_error_or_malformed_observation_is_not_evidence(self):
        action = SimpleNamespace(tool="search_knowledge_base", tool_input={})
        self.assertEqual(collect_evidence([(action, '{"error":"timeout"}'), (action, 'invalid'), (action, '{"results":[]}')]), [])

    def test_single_mode_dispatch_is_preserved(self):
        with patch("logpilot.agents.diagnosis_agent.run_single_diagnosis", return_value={"output": "single"}) as single:
            self.assertEqual(run_diagnosis(**self.kwargs, mode="single", tool_transport="local")["output"], "single")
            single.assert_called_once()
        with self.assertRaises(ValueError):
            run_diagnosis(**self.kwargs, mode="unknown")


if __name__ == "__main__":
    unittest.main()
