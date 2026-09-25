from collections import Counter

from logpilot.core.models import LogRecord


class LogRepository:
    def __init__(self, records: list[LogRecord]):
        self.records = records
        self._by_id = {record.id: record for record in records}

    def search(
        self,
        keyword: str = "",
        level: str = "",
        trace_id: str = "",
        limit: int = 20,
    ) -> dict:
        keyword_folded = keyword.casefold().strip()
        level = level.upper().strip()
        matches = []
        for record in self.records:
            searchable = "\n".join(filter(None, [record.message, record.stack_trace]))
            if keyword_folded and keyword_folded not in searchable.casefold():
                continue
            if level and record.level != level:
                continue
            if trace_id and record.trace_id != trace_id:
                continue
            matches.append(record)
        capped = matches[: max(1, min(limit, 50))]
        return {
            "total": len(matches),
            "truncated": len(matches) > len(capped),
            "records": [self._evidence(record) for record in capped],
        }

    def context(self, record_id: int, window: int = 3) -> dict:
        record = self._by_id.get(record_id)
        if not record:
            return {"error": f"日志记录 #{record_id} 不存在"}
        if record.trace_id:
            related = [item for item in self.records if item.trace_id == record.trace_id]
            mode = "trace_id"
        else:
            position = self.records.index(record)
            window = max(1, min(window, 10))
            related = self.records[max(0, position - window) : position + window + 1]
            mode = "adjacent"
        total = len(related)
        if total > 50:
            anchor = related.index(record)
            start = max(0, min(anchor - 25, total - 50))
            related = related[start:start + 50]
        return {
            "mode": mode,
            "anchor_id": record_id,
            "total": total,
            "truncated": total > 50,
            "records": [self._evidence(item) for item in related],
        }

    def count_errors(self, group_by: str = "exception_type") -> dict:
        if group_by == "level":
            values = [record.level for record in self.records]
        elif group_by == "service":
            values = [record.service or "unknown" for record in self.records]
        else:
            values = [
                record.exception_type or record.message.split(":", 1)[0][:80]
                for record in self.records
                if record.level in {"ERROR", "FATAL"} or record.exception_type
            ]
        counts = Counter(values)
        return {"group_by": group_by, "counts": dict(counts.most_common(30))}

    @staticmethod
    def _evidence(record: LogRecord) -> dict:
        return {
            "id": record.id,
            "lines": f"{record.line_start}-{record.line_end}",
            "timestamp": record.timestamp,
            "level": record.level,
            "service": record.service,
            "trace_id": record.trace_id,
            "message": record.message[:1000],
            "exception_type": record.exception_type,
            "stack_trace": (record.stack_trace or "")[:4000] or None,
        }
