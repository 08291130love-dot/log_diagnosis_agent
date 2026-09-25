from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(slots=True)
class LogRecord:
    id: int
    line_start: int
    line_end: int
    timestamp: Optional[str]
    level: str
    service: Optional[str]
    trace_id: Optional[str]
    thread: Optional[str]
    logger: Optional[str]
    message: str
    exception_type: Optional[str]
    stack_trace: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)
