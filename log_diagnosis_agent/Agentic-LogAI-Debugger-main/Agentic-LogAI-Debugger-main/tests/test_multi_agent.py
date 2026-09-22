import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.diagnosis_agent import run_diagnosis
from agent.middleware import AgentObservabilityCallback
from agent.specialists import collect_evidence, run_specialist
from agent.state import StageResult
from agent.tools.log_tools import build_log_tools
from agent.workflow import run_multi_diagnosis
from core.parser import parse_spring_boot_logs
from repositories.log_repository import LogRepository
from repositories.source_repository import SourceRepository
from tests.test_log_diagnosis_agent import SAMPLE


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class ScenarioModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "offline-scenario"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = messages[0].content
        if "最终汇总节点" in system:
            payload = json.loads(messages[-1].content)
            assert len(payload["investigations"]) == 3
            assert all(s["evidence"] for s in payload["investigations"])
            message = AIMessage(content="当前日志 #2 需要核对连接配置；源码 Stock.java:1，知识库《manual.md》。")
        elif any(isinstance(m, ToolMessage) for m in messages):
            message = AIMessage(content="工具证据已取得，根因仍需运行环境验证。")
        else:
            if "你的角色是日志分析" in system:
                tool, args = "search_logs", {"keyword": "Redis"}
            elif "你的角色是源码定位" in system:
                tool, args = "get_source_context", {"file_name": "Stock.java", "line_number": 1}
            else:
                tool, args = "search_knowledge_base", {"query": "Redis refused"}
            message = AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": "call-1"}])
        return ChatResult(generations=[ChatGeneration(message=message)], llm_output={"token_usage": {"total_tokens": 10}})


class FakeKnowledge:
    def stats(self):
        return {"chunks": 1}

    def search(self, **kwargs):
        return {"results": [{"source": "manual.md", "content": "connection refused"}]}


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
        with patch("agent.specialists.create_chat_model", return_value=model):
            result = run_specialist("日志分析", "log_specialist.txt", build_log_tools(self.repo),
                                    {"question": "Redis 故障"}, "test", "test", "test")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.evidence[0]["data"]["records"][0]["id"], 2)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(len(result.steps), 1)

    def test_full_workflow_uses_all_executors_tools_and_synthesis(self):
        with patch("agent.specialists.create_chat_model", side_effect=lambda *args: ScenarioModel()), patch("agent.workflow.create_chat_model", side_effect=lambda *args: ScenarioModel()):
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources, knowledge_base=FakeKnowledge())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["intermediate_steps"]), 3)
        self.assertEqual(result["model_calls"], 7)
        self.assertEqual(result["total_tokens"], 70)
        self.assertEqual([s["status"] for s in result["stages"]], ["completed"] * 4)

    def test_unsupported_model_answer_is_not_evidence(self):
        with patch("agent.specialists.create_chat_model", return_value=ToolModel(responses=[AIMessage(content="数据库已经宕机")] )):
            result = run_specialist("日志分析", "log_specialist.txt", build_log_tools(self.repo), {}, "test", "test", "test")
        self.assertEqual(result.status, "no_evidence")
        self.assertEqual(result.findings, "")

    def test_iteration_limit_preserves_evidence_without_claiming_completion(self):
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"keyword": "Redis"}, "id": "again"}])])
        with patch("agent.specialists.create_chat_model", return_value=model):
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

        with patch("agent.workflow.run_specialist", side_effect=worker), patch("agent.workflow.synthesize", return_value="报告") as summary:
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources, knowledge_base=FakeKnowledge())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(received["日志分析"][0], {"search_logs", "get_log_context", "count_errors"})
        self.assertEqual(received["源码定位"][0], {"get_source_context", "list_source_files", "search_source_code"})
        self.assertEqual(received["故障知识"][0], {"search_knowledge_base"})
        self.assertIn("log_investigation", received["源码定位"][1])
        self.assertEqual(len(summary.call_args.args[0]["investigations"]), 3)

    def test_missing_inputs_skip_without_worker_calls(self):
        with patch("agent.workflow.run_specialist", return_value=self.log_result()) as worker, patch("agent.workflow.synthesize", return_value="报告"):
            result = run_multi_diagnosis(**self.kwargs)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual([s["status"] for s in result["stages"]], ["completed", "skipped", "skipped", "completed"])

    def test_log_failure_prevents_downstream_fabrication(self):
        with patch("agent.workflow.run_specialist", return_value=StageResult("日志分析", "failed", reason="超时")) as worker, patch("agent.workflow.synthesize") as summary:
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources, knowledge_base=FakeKnowledge())
        self.assertEqual(worker.call_count, 1)
        summary.assert_not_called()
        self.assertEqual(result["status"], "partial")

    def test_optional_failure_still_generates_limited_report(self):
        def worker(name, *args):
            return self.log_result() if name == "日志分析" else StageResult(name, "failed", reason="超时")
        with patch("agent.workflow.run_specialist", side_effect=worker), patch("agent.workflow.synthesize", return_value="缺少源码依据") as summary:
            result = run_multi_diagnosis(**self.kwargs, source_repository=self.sources)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["output"], "缺少源码依据")
        self.assertEqual(summary.call_args.args[0]["investigations"][1]["status"], "failed")

    def test_summary_failure_preserves_evidence_and_hides_secret(self):
        with patch("agent.workflow.run_specialist", return_value=self.log_result()), patch("agent.workflow.synthesize", side_effect=RuntimeError("secret-key")):
            result = run_multi_diagnosis(**self.kwargs)
        self.assertEqual(result["stages"][-1]["status"], "failed")
        self.assertTrue(result["stages"][0]["evidence"])
        self.assertNotIn("secret-key", json.dumps(result))

    def test_history_is_bounded_and_only_context(self):
        history = [{"question": str(i), "output": "previous answer"} for i in range(7)]
        with patch("agent.workflow.run_specialist", return_value=self.log_result()) as worker, patch("agent.workflow.synthesize", return_value="报告"):
            run_multi_diagnosis(**self.kwargs, chat_history=history)
        self.assertEqual([t["question"] for t in worker.call_args.args[3]["history_for_reference_only"]], ["3", "4", "5", "6"])

    def test_error_or_malformed_observation_is_not_evidence(self):
        action = SimpleNamespace(tool="search_knowledge_base", tool_input={})
        self.assertEqual(collect_evidence([(action, '{"error":"timeout"}'), (action, 'invalid'), (action, '{"results":[]}')]), [])

    def test_single_mode_dispatch_is_preserved(self):
        with patch("agent.diagnosis_agent.run_single_diagnosis", return_value={"output": "single"}) as single:
            self.assertEqual(run_diagnosis(**self.kwargs, mode="single")["output"], "single")
            single.assert_called_once()
        with self.assertRaises(ValueError):
            run_diagnosis(**self.kwargs, mode="unknown")


if __name__ == "__main__":
    unittest.main()
