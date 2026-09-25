import json
import re
from typing import Iterable

from logpilot.core.models import LogRecord


TEXT_LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{3})?)\s+"
    r"(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\s+"
    r"(?:\[(?P<context>[^]]+)\]\s+)?"
    r"(?:\[(?P<thread>[^]]+)\]\s+)?"
    r"(?:(?P<logger>[\w.$-]+)\s*[:-]\s*)?"
    r"(?P<message>.*)$"
)

EXCEPTION_PATTERN = re.compile(
    r"^(?:Caused by:\s*|Suppressed:\s*)?"
    r"(?P<type>(?:[a-zA-Z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error|Timeout))"
    r"(?::\s*(?P<message>.*))?\s*$",
    re.MULTILINE,
)


def _context_fields(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    fields = [part.strip() for part in raw.split(",")]
    if len(fields) >= 3:
        return fields[0] or None, fields[1] or None
    trace_match = re.search(r"(?:traceId|trace_id)\s*[=:]\s*([\w-]+)", raw, re.I)
    service_match = re.search(r"(?:service|app)\s*[=:]\s*([\w.-]+)", raw, re.I)
    return (
        service_match.group(1) if service_match else None,
        trace_match.group(1) if trace_match else None,
    )


def _exception_from(text: str) -> str | None:
    matches = list(EXCEPTION_PATTERN.finditer(text))
    return matches[-1].group("type") if matches else None


def _record_from_json(line: str, record_id: int, line_number: int) -> LogRecord | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not any(key in payload for key in ("message", "msg", "level")):
        return None
    message = str(payload.get("message") or payload.get("msg") or "")
    stack = payload.get("stack_trace") or payload.get("stackTrace") or payload.get("exception")
    full_text = f"{message}\n{stack or ''}"
    return LogRecord(
        id=record_id,
        line_start=line_number,
        line_end=line_number,
        timestamp=str(payload.get("timestamp") or payload.get("@timestamp") or "") or None,
        level=str(payload.get("level") or "INFO").upper().replace("WARNING", "WARN"),
        service=payload.get("service") or payload.get("application"),
        trace_id=payload.get("traceId") or payload.get("trace_id"),
        thread=payload.get("thread"),
        logger=payload.get("logger") or payload.get("logger_name"),
        message=message,
        exception_type=_exception_from(full_text),
        stack_trace=str(stack) if stack else None,
    )


def parse_spring_boot_logs(content: str) -> list[LogRecord]:
    """Parse text/JSON Spring Boot logs and merge multiline Java stack traces."""
    lines = content.splitlines()
    records: list[LogRecord] = []
    current: dict | None = None

    def flush(end_line: int) -> None:
        nonlocal current
        if not current:
            return
        continuation = current.pop("continuation")
        stack = "\n".join(continuation) if continuation else None
        full_text = f"{current['message']}\n{stack or ''}"
        records.append(
            LogRecord(
                id=len(records) + 1,
                line_end=end_line,
                exception_type=_exception_from(full_text),
                stack_trace=stack,
                **current,
            )
        )
        current = None

    for line_number, line in enumerate(lines, 1):
        json_record = _record_from_json(line, len(records) + 1, line_number)
        if json_record:
            flush(line_number - 1)
            json_record.id = len(records) + 1
            records.append(json_record)
            continue

        match = TEXT_LOG_PATTERN.match(line)
        if match:
            flush(line_number - 1)
            service, trace_id = _context_fields(match.group("context"))
            current = {
                "line_start": line_number,
                "timestamp": match.group("timestamp"),
                "level": match.group("level").replace("WARNING", "WARN"),
                "service": service,
                "trace_id": trace_id,
                "thread": match.group("thread"),
                "logger": match.group("logger"),
                "message": match.group("message"),
                "continuation": [],
            }
        elif current is not None:
            current["continuation"].append(line)
        elif line.strip():
            current = {
                "line_start": line_number,
                "timestamp": None,
                "level": "UNKNOWN",
                "service": None,
                "trace_id": None,
                "thread": None,
                "logger": None,
                "message": line,
                "continuation": [],
            }

    flush(len(lines))
    return records


def serialize_records(records: Iterable[LogRecord]) -> list[dict]:
    return [record.to_dict() for record in records]
