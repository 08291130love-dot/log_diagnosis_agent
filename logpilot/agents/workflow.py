"""Log-first workflow, parallel optional investigations, one synthesis call."""
import json
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

from langchain_core.messages import HumanMessage, SystemMessage

from logpilot.agents.specialists import run_specialist
from logpilot.agents.middleware import AgentObservabilityCallback
from logpilot.agents.state import StageResult
from logpilot.agents.tools.log_tools import build_log_tools
from logpilot.agents.tools.source_tools import build_source_tools
from logpilot.agents.tools.knowledge_tools import build_knowledge_tools
from logpilot.llm.factory import create_chat_model
from logpilot.prompts.loader import load_prompt


def fallback_report(stages):
    lines = ["## 诊断未完整完成", "以下为实际调查阶段的状态，不代表根因已确认。"]
    for stage in stages:
        lines.append(f"- **{stage.name}**：{stage.status}。{stage.reason}")
    lines.append("\n请在调查过程里检查已有工具证据；失败阶段可在检查配置后重试。")
    return "\n".join(lines)


def synthesize(payload, api_key, base_url, model, observer):
    llm = create_chat_model(api_key, base_url, model)
    response = llm.invoke([
        SystemMessage(content=load_prompt("diagnosis_prompt.txt") + "\n" + load_prompt("synthesis_prompt.txt")),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ], config={"callbacks": [observer]})
    if not isinstance(response.content, str) or not response.content.strip():
        raise ValueError("汇总响应为空")
    return response.content


def run_multi_diagnosis(repository, question, api_key, base_url, model,
                        source_repository=None, knowledge_base=None,
                        chat_history=None, embedding_model="text-embedding-v4", tool_provider=None):
    if not api_key.strip():
        raise ValueError("请先填写百炼 API Key。")
    if not question.strip():
        raise ValueError("请输入需要排查的问题。")
    started = monotonic()
    history = [
        {"question": str(t.get("question", ""))[:2000], "answer": str(t.get("output", ""))[:6000]}
        for t in (chat_history or [])[-4:]
    ]
    payload = {"question": question, "history_for_reference_only": history}
    log_tools = tool_provider("logs") if tool_provider else build_log_tools(repository)
    logs = run_specialist("日志分析", "log_specialist.txt", log_tools,
                          payload, api_key, base_url, model)
    stages = [logs]
    if not logs.evidence:
        stages.extend([
            StageResult("源码定位", "skipped", reason="日志阶段没有取得证据"),
            StageResult("故障知识", "skipped", reason="日志阶段没有取得证据"),
            StageResult("诊断汇总", "skipped", reason="缺少当前日志证据，不生成根因结论"),
        ])
        output = fallback_report(stages)
    else:
        handoff = {**payload, "log_investigation": logs.handoff()}
        optional = {}
        # Each worker owns a model instance and callback; no Streamlit calls in threads.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="diagnosis") as pool:
            source_tools = tool_provider("sources") if tool_provider else build_source_tools(source_repository)
            if source_tools:
                optional["源码定位"] = pool.submit(
                    run_specialist, "源码定位", "source_specialist.txt", source_tools,
                    handoff, api_key, base_url, model,
                )
            else:
                optional["源码定位"] = StageResult("源码定位", "skipped", reason="没有上传 Java 源码")
            try:
                knowledge_tools = tool_provider("knowledge") if tool_provider else build_knowledge_tools(knowledge_base, api_key, base_url, embedding_model)
                if knowledge_tools:
                    optional["故障知识"] = pool.submit(
                        run_specialist, "故障知识", "knowledge_specialist.txt", knowledge_tools,
                        handoff, api_key, base_url, model,
                    )
                else:
                    optional["故障知识"] = StageResult("故障知识", "skipped", reason="知识库为空或未启用")
            except Exception as exc:
                optional["故障知识"] = StageResult("故障知识", "failed", reason=f"知识库不可用（{type(exc).__name__}）")
            for value in optional.values():
                stages.append(value if isinstance(value, StageResult) else value.result())
        summary_started = monotonic()
        observer = AgentObservabilityCallback()
        try:
            output = synthesize({**payload, "investigations": [s.handoff() for s in stages]}, api_key, base_url, model, observer)
            stages.append(StageResult("诊断汇总", "completed", elapsed=monotonic() - summary_started, model_calls=observer.model_calls, total_tokens=observer.total_tokens))
        except Exception as exc:
            stages.append(StageResult("诊断汇总", "failed", reason=f"报告汇总失败（{type(exc).__name__}）", elapsed=monotonic() - summary_started, model_calls=observer.model_calls, total_tokens=observer.total_tokens))
            output = fallback_report(stages)
    return {
        "output": output, "mode": "multi",
        "status": "completed" if all(s.status in {"completed", "skipped"} for s in stages) and stages[-1].status == "completed" else "partial",
        "stages": [s.public() for s in stages],
        "elapsed": round(monotonic() - started, 2),
        "model_calls": sum(s.model_calls for s in stages),
        "total_tokens": sum(s.total_tokens for s in stages),
        "intermediate_steps": [step for s in stages for step in s.steps],
        "observability_events": [{**event, "agent": s.name} for s in stages for event in s.events],
    }
