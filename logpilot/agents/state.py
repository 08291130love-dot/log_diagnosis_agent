"""Per-request results. Evidence comes from tool observations, never model prose."""
from dataclasses import dataclass, field
import json
from typing import Literal


StageStatus = Literal["completed", "partial", "failed", "skipped", "no_evidence"]


@dataclass
class StageResult:
    name: str
    status: StageStatus
    findings: str = ""
    reason: str = ""
    evidence: list[dict] = field(default_factory=list)
    steps: list = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    elapsed: float = 0.0
    model_calls: int = 0
    total_tokens: int = 0

    def handoff(self) -> dict:
        # Retain whole evidence objects, not cut JSON or source lines in half.
        # Full observations remain available in the UI tool trace.
        evidence = []
        budget = 30000
        omitted = 0
        for item in self.evidence:
            data = dict(item["data"])
            for key in ("records", "matches", "results"):
                if isinstance(data.get(key), list):
                    selected = []
                    for entry in data[key]:
                        if len(json.dumps(selected + [entry], ensure_ascii=False)) > 12000:
                            break
                        selected.append(entry)
                    if len(selected) < len(data[key]):
                        data["handoff_truncated"] = True
                    data[key] = selected
            bounded = {**item, "data": data}
            size = len(json.dumps(bounded, ensure_ascii=False))
            if size <= budget:
                evidence.append(bounded)
                budget -= size
            else:
                omitted += 1
        return {
            "agent": self.name, "status": self.status,
            "findings": self.findings[:8000], "reason": self.reason,
            "evidence": evidence,
            "omitted_evidence_groups": omitted,
        }

    def public(self) -> dict:
        return {
            **self.handoff(), "elapsed": round(self.elapsed, 2),
            "tool_calls": len(self.steps),
            "model_calls": self.model_calls, "total_tokens": self.total_tokens,
        }
