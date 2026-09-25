"""Bridge synchronous LangChain workers to one request-scoped async MCP session."""
import json
import os
import sys
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import ExitStack
from logpilot.config.paths import PROJECT_ROOT
from time import monotonic

import anyio
from anyio.from_thread import start_blocking_portal
from langchain_core.tools import StructuredTool
from mcp import Client, StdioServerParameters

from logpilot.integrations.mcp.context import DiagnosisSnapshot, TOOL_GROUPS


class MCPConnectionError(RuntimeError):
    """Safe user-facing connection error, without subprocess/provider details."""


class MCPToolSession:
    def __init__(self, repository, source_repository=None, knowledge_base=None,
                 api_key="", base_url="", embedding_model="text-embedding-v4",
                 startup_timeout=30, call_timeout=150):
        self.snapshot = DiagnosisSnapshot(repository, source_repository, knowledge_base)
        self._credentials = {"LOGPILOT_MCP_API_KEY": api_key, "LOGPILOT_MCP_BASE_URL": base_url,
                             "LOGPILOT_MCP_EMBEDDING_MODEL": embedding_model}
        self.startup_timeout = startup_timeout
        self.call_timeout = call_timeout
        self._stack = None
        self._portal = None
        self._lifetime = None
        self._closed = True
        self.events = []
        self._tools = {}

    async def _connect(self, params, *, task_status=anyio.TASK_STATUS_IGNORED):
        # Keep enter/exit in the same task, including cancellation and startup failure.
        with anyio.fail_after(self.startup_timeout) as startup:
            async with Client(params, read_timeout_seconds=self.call_timeout) as client:
                self._client = client
                self._stop = anyio.Event()
                discovered = await client.list_tools()
                startup.deadline = float("inf")  # Handshake complete; per-call budgets take over.
                task_status.started(discovered.tools)
                await self._stop.wait()

    def __enter__(self):
        self._stack = ExitStack()
        started = monotonic()
        try:
            snapshot = self._stack.enter_context(self.snapshot)
            env = {
                "LOGPILOT_MCP_CONTEXT": str(snapshot.path),
                "LOGPILOT_MCP_REQUEST_ID": snapshot.request_id,
                "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "ANONYMIZED_TELEMETRY": "False", **self._credentials,
            }
            # Honor the user's networking configuration without copying all secrets.
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
                if key in os.environ:
                    env[key] = os.environ[key]
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "logpilot.integrations.mcp.server"],
                cwd=str(PROJECT_ROOT), env=env,
            )
            self._portal = self._stack.enter_context(start_blocking_portal(name="logpilot-mcp"))
            self._lifetime, discovered = self._portal.start_task(self._connect, params)
            expected = {name for names in TOOL_GROUPS.values() for name in names}
            self._tools = {tool.name: tool for tool in discovered}
            if set(self._tools) != expected:
                raise ValueError("Unexpected tool contract")
            self._closed = False
            self.startup_elapsed = round(monotonic() - started, 2)
            return self
        except BaseException as exc:
            self.close()
            if not isinstance(exc, Exception):
                raise
            raise MCPConnectionError("MCP 工具服务启动失败，请检查项目依赖与 Python 环境后重试。") from None
        finally:
            self._credentials.clear()

    def close(self):
        self._closed = True
        try:
            if self._portal and self._lifetime and not self._lifetime.done():
                self._portal.call(self._stop.set)
                try:
                    self._lifetime.result(timeout=5)
                except FutureTimeout:
                    self._lifetime.cancel()
        except Exception:
            pass
        finally:
            if self._stack:
                self._stack.close()
                self._stack = None
            self._portal = None

    def __exit__(self, *args):
        self.close()

    async def _call(self, name, arguments):
        with anyio.fail_after(self.call_timeout):
            return await self._client.call_tool(name, arguments)

    def call(self, name, arguments):
        started = monotonic()
        code = "completed"
        try:
            if self._closed or self._lifetime.done():
                raise ConnectionError()
            if name not in self._tools:
                raise ValueError()
            result = self._portal.call(self._call, name, arguments)
            if result.is_error:
                data = {"error": "MCP 工具执行失败，请检查参数或资料。", "code": "mcp_tool_error"}
            elif isinstance(result.structured_content, dict):
                data = result.structured_content
            else:
                data = json.loads("\n".join(c.text for c in result.content if c.type == "text"))
                if not isinstance(data, dict):
                    raise ValueError()
            code = data.get("code", "tool_error") if data.get("error") else "completed"
        except TimeoutError:
            code = "mcp_timeout"
            data = {"error": "MCP 工具调用超时，请稍后重试。", "code": code}
        except Exception:
            code = "mcp_unavailable"
            data = {"error": "MCP 工具服务不可用，请重新发起诊断。", "code": code}
        self.events.append({"tool": name, "status": code, "elapsed": round(monotonic() - started, 3)})
        return json.dumps(data, ensure_ascii=False)

    def get_tools(self, group):
        if group == "all":
            return [tool for role in TOOL_GROUPS for tool in self.get_tools(role)]
        if group == "sources" and not self.snapshot.source_enabled:
            return []
        if group == "knowledge":
            if self.snapshot.knowledge_error:
                raise MCPConnectionError("知识库不可用，请检查本地知识库。")
            if not self.snapshot.knowledge_enabled:
                return []
        return [self._adapt(self._tools[name]) for name in TOOL_GROUPS[group]]

    def _adapt(self, definition):
        def invoke(**kwargs):
            return self.call(definition.name, kwargs)

        return StructuredTool(
            name=definition.name, description=definition.description or definition.name,
            args_schema=definition.input_schema, func=invoke,
            metadata={"transport": "mcp", "request_id": self.snapshot.request_id},
        )

    def metrics(self):
        return {"request_id": self.snapshot.request_id, "startup_elapsed": self.startup_elapsed,
                "tool_calls": len(self.events), "events": list(self.events)}
