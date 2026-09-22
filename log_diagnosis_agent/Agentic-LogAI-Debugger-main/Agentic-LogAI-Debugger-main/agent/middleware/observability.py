import logging

from langchain_core.callbacks import BaseCallbackHandler


logger = logging.getLogger("spring_log_agent")


class AgentObservabilityCallback(BaseCallbackHandler):
    """Record model and tool lifecycle events without logging user data."""

    def __init__(self):
        self.events: list[dict] = []
        self.model_calls = 0
        self.total_tokens = 0

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        self.model_calls += 1

    def on_llm_end(self, response, **kwargs) -> None:
        usage = (response.llm_output or {}).get("token_usage", {})
        tokens = usage.get("total_tokens")
        if tokens is None:
            # Streaming LangChain responses attach usage to the assembled message.
            tokens = sum(
                int((getattr(g.message, "usage_metadata", None) or {}).get("total_tokens", 0) or 0)
                for group in response.generations for g in group
                if hasattr(g, "message")
            )
        self.total_tokens += int(tokens or 0)

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs) -> None:
        tool_name = serialized.get("name", "unknown")
        self.events.append({"event": "tool_start", "tool": tool_name})
        logger.info("Agent tool started: %s", tool_name)

    def on_tool_end(self, output, **kwargs) -> None:
        self.events.append({"event": "tool_end"})

    def on_tool_error(self, error: BaseException, **kwargs) -> None:
        self.events.append({"event": "tool_error", "error_type": type(error).__name__})
        logger.warning("Agent tool failed: %s", type(error).__name__)

    def on_llm_error(self, error: BaseException, **kwargs) -> None:
        self.events.append({"event": "model_error", "error_type": type(error).__name__})
        logger.warning("Agent model failed: %s", type(error).__name__)
