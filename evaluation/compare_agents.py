"""Opt-in real model comparison: python -m evaluation.compare_agents --mode both."""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agent.diagnosis_agent import run_diagnosis
from config.settings import DEFAULT_BASE_URL, DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL
from core.parser import parse_spring_boot_logs
from repositories.log_repository import LogRepository
from repositories.source_repository import SourceRepository
from rag.knowledge_base import KnowledgeBase


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="调用真实模型，对比相同输入的诊断结果、耗时与 Token；会消耗 API 额度。")
    parser.add_argument("--mode", choices=["single", "multi", "both"], default="both")
    parser.add_argument("--log", type=Path, default=ROOT / "samples" / "spring_boot_demo.log")
    parser.add_argument("--sources", type=Path, default=ROOT / "samples" / "java")
    parser.add_argument("--question", default="请分析订单或查询失败的主要原因，引用日志与源码证据并给出修复建议。")
    parser.add_argument("--knowledge", action="store_true", help="使用本地已有 Chroma 知识库")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    repository = LogRepository(parse_spring_boot_logs(args.log.read_text(encoding="utf-8-sig")))
    sources = SourceRepository({str(p.relative_to(args.sources)): p.read_text(encoding="utf-8-sig") for p in args.sources.rglob("*.java")})
    knowledge = KnowledgeBase(ROOT / "chroma_db") if args.knowledge else None
    output_dir = ROOT / "evaluation" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output_dir.mkdir(parents=True)
    rows = []
    for mode in (["single", "multi"] if args.mode == "both" else [args.mode]):
        result = run_diagnosis(
            repository, args.question, os.getenv("DASHSCOPE_API_KEY", ""),
            os.getenv("QWEN_BASE_URL", DEFAULT_BASE_URL), os.getenv("QWEN_MODEL", DEFAULT_CHAT_MODEL),
            sources, knowledge, embedding_model=os.getenv("QWEN_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL), mode=mode,
        )
        (output_dir / f"{mode}.md").write_text(result["output"], encoding="utf-8")
        row = {"mode": mode, "status": result.get("status", "completed"),
               "elapsed": result["elapsed"], "model_calls": result["model_calls"],
               "reported_tokens": result["total_tokens"], "tool_calls": len(result["intermediate_steps"]),
               "stages": [{k: s[k] for k in ("agent", "status", "reason", "elapsed", "tool_calls")} for s in result.get("stages", [])]}
        rows.append(row)
        (output_dir / "metrics.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False))
    print(f"报告目录：{output_dir}")


if __name__ == "__main__":
    main()
