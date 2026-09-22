import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agent.diagnosis_agent import run_diagnosis
from config.settings import (
    DEFAULT_BASE_URL,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    MAX_KNOWLEDGE_BYTES,
    MAX_LOG_FILE_BYTES,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILES,
)
from core.parser import parse_spring_boot_logs
from evaluation.evaluator import evaluate_cases
from rag.knowledge_base import KnowledgeBase
from repositories.log_repository import LogRepository
from repositories.source_repository import SourceRepository, extract_stack_frames
from utils.document_loader import read_uploaded_documents


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

st.set_page_config(
    page_title="Spring Boot 日志诊断 Agent",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .metric-card {border:1px solid #e5e7eb;border-radius:12px;padding:16px;text-align:center;
                  box-shadow:0 2px 7px rgba(0,0,0,.06);background:white}
    .metric-value {font-size:28px;font-weight:700;margin-bottom:4px}
    .metric-label {font-size:14px;color:#667085}
    .evidence {border-left:4px solid #4f46e5;padding:8px 12px;background:#f8f7ff;margin:8px 0}
    .block-container {padding-top:3.25rem !important;padding-bottom:2rem !important}
    /* 缩短侧栏顶部和简介后的留白，保留原生折叠按钮。 */
    [data-testid="stSidebarHeader"] {
        min-height:0 !important;height:38px !important;
        padding:4px 12px !important;margin-bottom:0 !important;
    }
    [data-testid="stSidebarUserContent"] {padding-top:0 !important}
    [data-testid="stSidebar"] h1 {padding-top:0 !important;padding-bottom:8px !important}
    .sidebar-brand {display:flex;align-items:center;gap:10px}
    .sidebar-brand-icon {display:flex;align-items:center;justify-content:center;
        width:32px;height:32px;flex:0 0 32px;border-radius:9px;background:#eaf0ff;color:#2563eb}
    [data-testid="stSidebar"] .sidebar-brand h1 {font-size:23px;font-weight:700;
        line-height:1.4;margin:0;padding:0 !important;letter-spacing:-.4px}
    [data-testid="stSidebar"] hr {margin:4px 0 !important}
    .upload-title {font-size:18px;font-weight:650;color:#202939;line-height:1.6;margin-bottom:0}
    .upload-help {font-size:14px;color:#667085;line-height:1.8;margin:0;overflow-wrap:anywhere}
    /* 不依赖不同 Streamlit 版本的主区节点名称，侧栏在下方单独覆盖。 */
    [data-testid="stFileUploaderDropzoneInstructions"] {display:none !important}
    [data-testid="stFileUploaderDropzone"] {
        display:flex;flex-wrap:wrap;gap:12px;
        min-height:80px !important;
        padding:16px 18px !important;
        align-items:center !important;
        border:1px dashed #c2cedd !important;
        border-radius:10px;
        background:#f8fafc !important;
    }
    [data-testid="stFileUploaderDropzone"]::before {
        content:"拖拽文件到这里，或点击选择文件";
        color:#475467;
        font-size:14px;line-height:1.8;
        flex:1 1 200px;min-width:0;
    }
    [data-testid="stFileUploaderDropzone"] button {
        margin-left:auto !important;
        min-width:104px;flex-shrink:0;
        font-size:0 !important;
    }
    [data-testid="stFileUploaderDropzone"] button > * {display:none !important}
    [data-testid="stFileUploaderDropzone"] button::after {
        content:"选择文件";
        font-size:14px;line-height:1.5;
    }
    /* 仅压缩侧栏上传框，保留原生文件选择、拖放和删除行为。 */
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] {
        display:none !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
        display:flex;flex-direction:column;align-items:stretch;gap:12px;
        padding:16px !important;min-height:0 !important;
        background:#fff;border:1px dashed #ccd5e1;border-radius:12px;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"]::before {
        content:"拖拽文件到此处";font-size:13px;line-height:1.6;
        color:#667085;text-align:center;flex:0 0 auto;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button {
        width:100%;min-height:36px;margin:0;padding:7px 12px;
        border:1px solid #d5deea;border-radius:8px;background:#f8fafc;
        color:#344054;font-size:0 !important;line-height:1.5;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button > * {
        display:none !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button::after {
        content:"选择文件";font-size:14px;line-height:1.5;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button:hover {
        border-color:#94a9c4;background:#eef3fa;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button:focus-visible {
        outline:2px solid #6091de;outline-offset:2px;
    }
    .sidebar-upload-help {font-size:12px;line-height:1.7;color:#667085;margin:-4px 0 2px;overflow-wrap:anywhere}
    /* 侧栏导航保留原生单选交互，仅替换视觉样式。 */
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(> [data-testid="stRadio"]) {
        width:100% !important;max-width:none !important;align-self:stretch;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] {
        width:100%;max-width:none;align-self:stretch;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] > label p {
        font-size:12px;color:#8592a6;line-height:1.6;padding-left:14px;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {
        display:flex;flex-direction:column;align-items:stretch;gap:6px;padding:4px 0;width:100% !important;max-width:none;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] {
        box-sizing:border-box;display:flex;align-items:center;gap:14px;
        width:100% !important;max-width:none !important;align-self:stretch;flex:0 0 auto;
        min-height:48px;margin:0;padding:12px 16px;
        border-radius:12px;background:transparent;color:#4e6685;
        transition:background .15s ease,color .15s ease;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] > div:first-of-type {
        display:none;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] > div:last-of-type {
        padding:0;margin:0;min-width:0;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] p {
        font-size:15px;line-height:1.6;white-space:nowrap;color:inherit;margin:0;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]::before {
        display:inline-flex;align-items:center;justify-content:center;
        flex:0 0 18px;width:18px;font-size:18px;line-height:1;color:inherit;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:nth-of-type(1)::before {content:"◇"}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:nth-of-type(2)::before {content:"≡"}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:nth-of-type(3)::before {content:"▤"}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:nth-of-type(4)::before {content:"▥"}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:hover {
        background:#f3f6fc;color:#2563eb;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) {
        background:#eaf0ff;color:#2563eb;box-shadow:inset 3px 0 0 #2563eb;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:focus-visible) {
        outline:2px solid #90b2fb;outline-offset:2px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def parse_content(content: str):
    return parse_spring_boot_logs(content)


@st.cache_resource(show_spinner=False)
def get_knowledge_base():
    return KnowledgeBase(ROOT / "chroma_db")


def load_input() -> tuple[str, str, bool]:
    uploaded = st.sidebar.file_uploader(
        "上传 Spring Boot 日志",
        type=["log", "txt", "json"],
        help="支持普通文本日志、Java 多行异常堆栈和 JSON 日志，单个文件最大 10 MB。",
    )
    st.sidebar.markdown(
        '<div class="sidebar-upload-help">LOG / TXT / JSON · 单个文件不超过 10 MB</div>',
        unsafe_allow_html=True,
    )
    if uploaded:
        if uploaded.size > MAX_LOG_FILE_BYTES:
            st.error("文件超过 10 MB，请截取故障时间段后重新上传。")
            st.stop()
        return uploaded.name, uploaded.getvalue().decode("utf-8-sig", errors="replace"), False
    sample = ROOT / "samples" / "spring_boot_demo.log"
    return sample.name, sample.read_text(encoding="utf-8"), True


def load_sources(use_sample: bool) -> tuple[dict[str, str], str]:
    uploaded_files = st.sidebar.file_uploader(
        "上传相关 Java 源码",
        type=["java"],
        accept_multiple_files=True,
        help="可同时选择多个 .java 文件；Agent 只会读取源码，不会修改文件。",
        key="java_sources",
    )
    st.sidebar.markdown(
        '<div class="sidebar-upload-help">JAVA · 最多 30 个文件 · 总大小不超过 5 MB</div>',
        unsafe_allow_html=True,
    )
    if uploaded_files:
        if len(uploaded_files) > MAX_SOURCE_FILES:
            st.error("一次最多上传 30 个 Java 文件。")
            st.stop()
        names = [item.name for item in uploaded_files]
        if len(set(names)) != len(names):
            st.error("上传文件中存在同名 Java 文件，请重命名后重新上传。")
            st.stop()
        if sum(item.size for item in uploaded_files) > MAX_SOURCE_BYTES:
            st.error("Java 源码总大小不能超过 5 MB。")
            st.stop()
        files = {
            item.name: item.getvalue().decode("utf-8-sig", errors="replace")
            for item in uploaded_files
        }
        return files, f"已上传 {len(files)} 个 Java 文件"
    if use_sample:
        sample_dir = ROOT / "samples" / "java"
        files = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(sample_dir.glob("*.java"))
        }
        return files, f"已加载 {len(files)} 个演示源码文件"
    return {}, "未上传 Java 源码"


def metric_card(column, value, label, color="#344054"):
    with column:
        st.markdown(
            f'<div class="metric-card"><div class="metric-value" style="color:{color}">{value}</div>'
            f'<div class="metric-label">{label}</div></div>',
            unsafe_allow_html=True,
        )


def record_rows(records):
    return [
        {
            "ID": r.id,
            "时间": r.timestamp or "-",
            "级别": r.level,
            "服务": r.service or "-",
            "traceId": r.trace_id or "-",
            "异常类型": r.exception_type or "-",
            "消息": r.message,
            "原始行": f"{r.line_start}-{r.line_end}",
        }
        for r in records
    ]


def friendly_diagnosis_error(exc: Exception, api_key: str) -> str:
    raw = str(exc).replace(api_key, "[已隐藏]")
    lowered = raw.casefold()
    if "connection error" in lowered or "winerror 10013" in lowered:
        return "无法连接千问接口，请检查网络连接、防火墙或代理设置。"
    if "timed out" in lowered or "timeout" in lowered:
        return "千问接口响应超时，请稍后重试或减少上传内容。"
    if "401" in lowered or "invalid_api_key" in lowered or "authentication" in lowered:
        return "百炼 API Key 无效或已过期，请检查左侧千问配置。"
    if "model" in lowered and ("not found" in lowered or "invalid" in lowered):
        return "当前账号无法使用该模型，请检查模型名称或百炼模型权限。"
    return raw[:1200]


st.sidebar.markdown(
    '<div class="sidebar-brand">'
    '<span class="sidebar-brand-icon" aria-hidden="true">'
    '<svg width="21" height="21" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M5 3h9l5 5v13H5z"/><path d="M14 3v5h5"/>'
    '<path d="M8 14h2l1-3 2 6 1-3h2"/></svg></span>'
    '<h1>日志诊断 Agent</h1></div>',
    unsafe_allow_html=True,
)
st.sidebar.caption("面向 Spring Boot 应用的故障调查助手")
st.sidebar.divider()
source_name, log_content, using_sample_log = load_input()
st.sidebar.caption(f"当前数据：{source_name}")
source_files, source_status = load_sources(using_sample_log)
st.sidebar.caption(source_status)

selected_page = st.sidebar.radio(
    "导航",
    ["智能诊断", "日志中心", "故障知识库", "项目评估"],
    index=None,
    key="navigation_page",
)
page = selected_page or "仪表盘"

with st.sidebar.expander("千问配置"):
    api_key = st.text_input(
        "百炼 API Key",
        value=os.getenv("DASHSCOPE_API_KEY", ""),
        type="password",
        help="仅在点击开始诊断后使用。",
    )
    base_url = st.text_input(
        "接口地址",
        value=os.getenv("QWEN_BASE_URL", DEFAULT_BASE_URL),
    )
    model = st.text_input("模型", value=os.getenv("QWEN_MODEL", DEFAULT_CHAT_MODEL))
    embedding_model = st.text_input(
        "Embedding 模型",
        value=os.getenv("QWEN_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
    )

records = parse_content(log_content)
repository = LogRepository(records)
source_repository = SourceRepository(source_files)
knowledge_base = get_knowledge_base()
knowledge_stats = knowledge_base.stats()
hash_content = log_content + "".join(
    f"\n{path}\n{content}" for path, content in sorted(source_files.items())
)
source_hash = hashlib.sha256(hash_content.encode("utf-8")).hexdigest()[:12]


if page != "仪表盘":
    def return_to_dashboard():
        st.session_state.navigation_page = None

    st.button("← 返回仪表盘", on_click=return_to_dashboard)


if page == "仪表盘":
    st.title("Spring Boot 日志诊断仪表盘")
    st.caption("日志在本地完成解析和统计；此页面不会调用大模型。")

    error_count = sum(r.level in {"ERROR", "FATAL"} for r in records)
    warning_count = sum(r.level == "WARN" for r in records)
    trace_count = len({r.trace_id for r in records if r.trace_id})
    exception_count = sum(bool(r.exception_type) for r in records)
    columns = st.columns(4)
    metric_card(columns[0], error_count, "错误日志", "#dc3545")
    metric_card(columns[1], warning_count, "警告日志", "#f59e0b")
    metric_card(columns[2], trace_count, "请求链路", "#2563eb")
    metric_card(columns[3], exception_count, "异常堆栈", "#7c3aed")

    st.subheader("最近日志")
    table = pd.DataFrame(record_rows(records[-100:]))
    st.dataframe(table, width="stretch", hide_index=True)

elif page == "智能诊断":
    st.title("智能故障诊断")
    st.caption("Agent 会联合调查日志、Java 源码和历史故障知识库，并支持基于上轮报告继续追问。")
    diagnosis_mode = st.selectbox(
        "诊断模式", ["multi", "single"],
        format_func=lambda value: "多 Agent 协同诊断" if value == "multi" else "单 Agent 诊断（对照）",
        help="多 Agent 先分析日志，再按需并行查源码和知识库，最后汇总。通常会增加模型调用和耗时。",
    )
    if source_repository:
        st.success(f"已准备 {len(source_repository.files)} 个 Java 源码文件，可进行日志与源码联合诊断。")
    else:
        st.info("当前没有 Java 源码。仍可诊断日志；上传源码后可获得代码级定位和修复片段。")
    if knowledge_stats["chunks"]:
        st.success(
            f"知识库已启用：{len(knowledge_stats['sources'])} 份资料，"
            f"{knowledge_stats['chunks']} 个文本块。"
        )
    else:
        st.info("故障知识库为空，可先在“故障知识库”页面导入运维手册或历史案例。")

    conversation = st.session_state.get("diagnosis_conversation")
    if not conversation or conversation.get("source_hash") != source_hash or conversation.get("mode") != diagnosis_mode:
        conversation = {"source_hash": source_hash, "mode": diagnosis_mode, "turns": []}
        st.session_state.diagnosis_conversation = conversation
        st.session_state.pop("diagnosis", None)
    if conversation["turns"]:
        with st.expander(f"查看多轮诊断记录（{len(conversation['turns'])} 轮）"):
            for index, turn in enumerate(conversation["turns"], 1):
                st.markdown(f"**第 {index} 轮问题**：{turn['question']}")
                st.markdown(turn["output"])
        if st.button("清空本次对话"):
            conversation["turns"] = []
            st.session_state.pop("diagnosis", None)
            st.rerun()

    question = st.text_area(
        "排查问题" if not conversation["turns"] else "继续追问",
        value=(
            "请分析这段日志中订单或查询失败的主要原因，并给出有日志证据的排查建议。"
            if not conversation["turns"]
            else ""
        ),
        height=100,
        key=f"diagnosis_question_{source_hash}_{len(conversation['turns'])}",
    )
    if st.button("开始诊断" if not conversation["turns"] else "发送追问", type="primary"):
        if not api_key.strip():
            st.warning("请先在左侧“千问配置”中填写百炼 API Key。")
        elif not question.strip():
            st.warning("请输入需要排查的问题。")
        else:
            try:
                with st.spinner("Agent 正在联合调查日志、源码和知识库…", show_time=True):
                    result = run_diagnosis(
                        repository,
                        question,
                        api_key,
                        base_url,
                        model,
                        source_repository=source_repository,
                        knowledge_base=knowledge_base if knowledge_stats["chunks"] else None,
                        chat_history=conversation["turns"],
                        embedding_model=embedding_model,
                        mode=diagnosis_mode,
                    )
                report = {
                    "source_hash": source_hash,
                    "question": question,
                    "output": str(result.get("output", "未生成报告")),
                    "mode": diagnosis_mode,
                    "status": result.get("status", "completed"),
                    "stages": result.get("stages", []),
                    "elapsed": result.get("elapsed", 0),
                    "model_calls": result.get("model_calls", 0),
                    "total_tokens": result.get("total_tokens", 0),
                    "steps": [
                        {
                            "tool": action.tool,
                            "input": action.tool_input,
                            "output": str(observation),
                        }
                        for action, observation in result.get("intermediate_steps", [])
                    ],
                }
                if report["status"] == "completed":
                    conversation["turns"].append(
                        {"question": question, "output": report["output"]}
                    )
                st.session_state.diagnosis = report
                st.rerun()
            except Exception as exc:
                st.error("诊断失败：" + friendly_diagnosis_error(exc, api_key))

    report = st.session_state.get("diagnosis")
    if report and report.get("source_hash") == source_hash:
        st.subheader("诊断报告")
        if report.get("status") != "completed":
            st.warning("本次调查有阶段未完整完成，请结合阶段状态查看报告；本轮未加入追问历史。")
        st.caption(f"耗时 {report.get('elapsed', 0):.1f} 秒 · 模型调用 {report.get('model_calls', 0)} 次 · 模型返回 Token 用量 {report.get('total_tokens', 0)}（不含 Embedding；未返回用量时为 0）")
        st.markdown(report["output"])
        if report.get("stages"):
            with st.expander("查看 Agent 协作过程"):
                status_labels = {"completed": "完成", "partial": "部分完成", "failed": "失败", "skipped": "跳过", "no_evidence": "无匹配证据"}
                for stage in report["stages"]:
                    st.markdown(f"**{stage['agent']} · {status_labels.get(stage['status'], stage['status'])}**")
                    st.caption(f"{stage['elapsed']:.1f} 秒 · {stage['tool_calls']} 次工具调用")
                    if stage["reason"]:
                        st.write(stage["reason"])
                    if stage["findings"]:
                        st.markdown(stage["findings"])
        with st.expander(f"查看调查过程（{len(report['steps'])} 次工具调用）"):
            for index, step in enumerate(report["steps"], 1):
                st.markdown(f"**{index}. {step['tool']}**")
                st.json(step["input"])
                st.code(step["output"], language="json")
        st.download_button(
            "下载诊断报告",
            report["output"],
            file_name="spring-boot-diagnosis.md",
            mime="text/markdown",
        )

elif page == "日志中心":
    st.title("日志中心")
    col1, col2, col3 = st.columns([2, 1, 2])
    keyword = col1.text_input("关键词")
    levels = ["全部"] + sorted({record.level for record in records})
    level = col2.selectbox("日志级别", levels)
    trace_id = col3.text_input("traceId")
    result = repository.search(
        keyword=keyword,
        level="" if level == "全部" else level,
        trace_id=trace_id,
        limit=50,
    )
    st.caption(f"共匹配 {result['total']} 条，当前最多展示 50 条。")
    matched_ids = {item["id"] for item in result["records"]}
    matched = [record for record in records if record.id in matched_ids]
    st.dataframe(pd.DataFrame(record_rows(matched)), width="stretch", hide_index=True)
    if matched:
        selected_id = st.selectbox("查看完整日志记录", [record.id for record in matched])
        selected = next(record for record in matched if record.id == selected_id)
        st.code(
            f"{selected.message}\n{selected.stack_trace or ''}".rstrip(),
            language="text",
        )
        source_matches = []
        for frame in extract_stack_frames(selected.stack_trace):
            if source_repository.find_file(frame.file_name):
                source_matches.append(frame)
        if source_matches:
            frame = source_matches[0]
            source_context = source_repository.read_context(frame.file_name, frame.line_number)
            st.subheader("关联源码")
            st.caption(
                f"根据堆栈定位：{source_context.get('file', frame.file_name)}:"
                f"{frame.line_number} · {frame.class_name}.{frame.method}"
            )
            st.code(source_context.get("code", "未找到对应源码"), language="java")

elif page == "故障知识库":
    st.title("故障知识库")
    st.caption("导入历史故障、运维手册和解决方案，通过 RAG 进行知识问答并辅助 Agent 诊断。")
    source_col, chunk_col = st.columns(2)
    metric_card(source_col, len(knowledge_stats["sources"]), "知识文档", "#2563eb")
    metric_card(chunk_col, knowledge_stats["chunks"], "向量文本块", "#7c3aed")

    with st.container(border=True):
        st.markdown('<div class="upload-title notranslate" translate="no">📚 导入知识文档</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="upload-help notranslate" translate="no">支持 TXT、Markdown 和 PDF，可同时选择多个文件，总大小不超过 20 MB。</div>',
            unsafe_allow_html=True,
        )
        uploaded_knowledge = st.file_uploader(
            "选择知识文档",
            type=["txt", "md", "pdf"],
            accept_multiple_files=True,
            key="knowledge_documents",
            label_visibility="collapsed",
        )
        import_col, sample_col, _ = st.columns([1, 1, 3])
        if uploaded_knowledge:
            import_clicked = import_col.button("写入知识库", type="primary", use_container_width=True)
        else:
            import_col.caption("选择文件后可写入")
            import_clicked = False
        sample_clicked = sample_col.button("导入演示手册", use_container_width=True)

        if import_clicked:
            if not api_key.strip():
                st.warning("请先在左侧“千问配置”中填写百炼 API Key。")
            elif sum(item.size for item in uploaded_knowledge) > MAX_KNOWLEDGE_BYTES:
                st.error("知识文档总大小不能超过 20 MB。")
            else:
                try:
                    with st.spinner("正在切分文档并生成向量…", show_time=True):
                        documents = read_uploaded_documents(uploaded_knowledge)
                        result = knowledge_base.add_documents(
                            documents,
                            api_key,
                            base_url,
                            embedding_model,
                        )
                    st.success(
                        f"已写入 {len(result['sources'])} 份资料、{result['chunks']} 个文本块。"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error("知识库写入失败：" + friendly_diagnosis_error(exc, api_key))

        if sample_clicked:
            if not api_key.strip():
                st.warning("请先在左侧“千问配置”中填写百炼 API Key。")
            else:
                try:
                    sample_path = ROOT / "samples" / "knowledge" / "spring-boot-runbook.md"
                    with st.spinner("正在写入演示知识…", show_time=True):
                        result = knowledge_base.add_documents(
                            {sample_path.name: sample_path.read_text(encoding="utf-8")},
                            api_key,
                            base_url,
                            embedding_model,
                        )
                    st.success(f"已写入 {result['chunks']} 个演示文本块。")
                    st.rerun()
                except Exception as exc:
                    st.error("知识库写入失败：" + friendly_diagnosis_error(exc, api_key))

    if knowledge_stats["sources"]:
        with st.expander(f"已入库资料（{len(knowledge_stats['sources'])} 份）"):
            for source in knowledge_stats["sources"]:
                st.markdown(f"- `{source}`")

        st.subheader("知识库问答")
        st.caption("回答仅依据已入库资料，并在结论中标注资料来源。")
        if "knowledge_chat" not in st.session_state:
            st.session_state.knowledge_chat = []
        if st.session_state.knowledge_chat and st.button("清空问答记录"):
            st.session_state.knowledge_chat = []
            st.rerun()
        for turn in st.session_state.knowledge_chat:
            with st.chat_message("user"):
                st.write(turn["question"])
            with st.chat_message("assistant"):
                st.markdown(turn["answer"])
                with st.expander("查看召回依据"):
                    for item in turn["results"]:
                        st.markdown(
                            f"**{item['source']} · 文本块 {item['chunk_index']}**"
                        )
                        st.write(item["content"])

        knowledge_question = st.chat_input("向故障知识库提问，例如：Redis 连接失败应该检查什么？")
        if knowledge_question:
            with st.chat_message("user"):
                st.write(knowledge_question)
            try:
                with st.chat_message("assistant"):
                    with st.spinner("正在检索资料并生成回答…"):
                        answer_result = knowledge_base.answer_question(
                            question=knowledge_question,
                            api_key=api_key,
                            base_url=base_url,
                            chat_model=model,
                            embedding_model=embedding_model,
                            chat_history=st.session_state.knowledge_chat,
                        )
                    st.markdown(answer_result["answer"])
                    with st.expander("查看召回依据"):
                        for item in answer_result["results"]:
                            st.markdown(
                                f"**{item['source']} · 文本块 {item['chunk_index']}**"
                            )
                            st.write(item["content"])
                st.session_state.knowledge_chat.append(
                    {
                        "question": knowledge_question,
                        "answer": answer_result["answer"],
                        "results": answer_result["results"],
                    }
                )
            except Exception as exc:
                st.error("知识库问答失败：" + friendly_diagnosis_error(exc, api_key))
    else:
        st.info("知识库目前为空，请先导入资料，再开始问答。")

elif page == "项目评估":
    st.title("项目评估")
    st.caption("使用固定真值案例验证日志检索、根因异常识别和 traceId 上下文关联；评估过程不调用大模型。")
    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    evaluation = evaluate_cases(repository, cases)
    pass_col, total_col, rate_col = st.columns(3)
    metric_card(pass_col, evaluation["passed"], "通过案例", "#198754")
    metric_card(total_col, evaluation["total"], "案例总数", "#2563eb")
    metric_card(rate_col, f"{evaluation['pass_rate']:.0%}", "通过率", "#7c3aed")
    result_rows = [
        {
            "案例": item["name"],
            "结果": "通过" if item["passed"] else "失败",
            "检索命中数": item["matches"],
            "根因异常命中": "是" if item["exception_hit"] else "否",
            "traceId 命中": "是" if item["trace_hit"] else "否",
            "上下文命中": "是" if item["context_hit"] else "否",
        }
        for item in evaluation["results"]
    ]
    st.dataframe(pd.DataFrame(result_rows), width="stretch", hide_index=True)
    st.info("该结果只衡量本地解析和工具检索，不代表大模型最终诊断准确率。")

    st.divider()
    st.subheader("项目能力")
    st.markdown(
        """
        这个项目面向 Spring Boot 应用故障排查，当前包含：

        - Java 多行异常堆栈合并与结构化解析；
        - 按关键词、日志级别和 traceId 查询日志；
        - 按同一请求链路或相邻记录补查上下文；
        - 按异常类型、级别和服务统计错误；
        - 根据 Java 堆栈中的文件名和行号读取相关源码；
        - 结合日志与源码生成修改建议和修复代码片段；
        - 使用 Chroma 保存历史故障和运维手册，通过 Embedding 进行语义检索；
        - 支持基于上一轮诊断报告继续追问；
        - 千问工具调用 Agent，以及带日志行号、源码行号的证据报告。
        - 固定故障案例评估，验证检索、异常识别和上下文关联。

        页面打开和本地筛选不会调用模型。诊断、知识库写入和语义检索会调用千问或 Embedding 接口。系统不会自动修改源码文件。
        """
    )

st.divider()
st.caption("Spring Boot 日志诊断 Agent · 本地 MVP")
