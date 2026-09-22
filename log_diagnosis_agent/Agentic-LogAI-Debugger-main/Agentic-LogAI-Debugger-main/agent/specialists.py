"""Bounded specialists with separate tool sets and callbacks."""
import json
from time import monotonic

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agent.middleware import AgentObservabilityCallback
from agent.state import StageResult
from model.factory import create_chat_model
from utils.prompt_loader import load_prompt


def collect_evidence(steps: list) -> list[dict]:
    evidence = []
    for action, observation in steps:
        try:
            data = json.loads(observation) if isinstance(observation, str) else observation
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("error"):
            continue
        # File lists and statistics alone are not enough to ground a diagnosis.
        has_content = any(data.get(key) for key in ("records", "code", "matches", "results"))
        if has_content:
            evidence.append({"tool": action.tool, "input": action.tool_input, "data": data})
    return evidence


def run_specialist(name, prompt_file, tools, payload, api_key, base_url, model):
    started = monotonic()
    observer = AgentObservabilityCallback()
    if not tools:
        return StageResult(name, "skipped", reason="没有可用工具")
    try:
        llm = create_chat_model(api_key, base_url, model)
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=load_prompt("specialist_rules.txt") + "\n" + load_prompt(prompt_file)),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        executor = AgentExecutor(
            agent=create_openai_tools_agent(llm, tools, prompt), tools=tools,
            max_iterations=5, max_execution_time=120,
            return_intermediate_steps=True, handle_parsing_errors=True, verbose=False,
        )
        result = executor.invoke(
            {"input": json.dumps(payload, ensure_ascii=False)},
            config={"callbacks": [observer]},
        )
        steps = result.get("intermediate_steps", [])
        evidence = collect_evidence(steps)
        findings = str(result.get("output", "")).strip()
        exhausted = "Agent stopped" in findings or not findings
        status = "no_evidence" if not evidence else ("partial" if exhausted else "completed")
        reason = "未获取可引用证据" if not evidence else ("达到调查预算，保留已取得的工具证据" if exhausted else "")
        return StageResult(
            name, status, findings="" if exhausted or not evidence else findings,
            reason=reason, evidence=evidence, steps=steps, events=observer.events,
            elapsed=monotonic() - started,
            model_calls=observer.model_calls, total_tokens=observer.total_tokens,
        )
    except Exception as exc:
        # Never forward provider error text (which may contain secrets) to another model/UI.
        return StageResult(
            name, "failed", reason=f"阶段调用失败（{type(exc).__name__}），请检查模型配置或网络",
            events=observer.events, elapsed=monotonic() - started,
            model_calls=observer.model_calls, total_tokens=observer.total_tokens,
        )
