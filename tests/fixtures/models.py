"""Offline chat models and knowledge responses shared by tests."""
import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


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
