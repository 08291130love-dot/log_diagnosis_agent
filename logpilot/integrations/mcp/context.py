"""Private input snapshot for a single diagnostic subprocess. Never stores API keys."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


TOOL_GROUPS = {
    "logs": ("search_logs", "get_log_context", "count_errors"),
    "sources": ("list_source_files", "get_source_context", "search_source_code"),
    "knowledge": ("search_knowledge_base",),
}


class DiagnosisSnapshot:
    def __init__(self, repository, source_repository=None, knowledge_base=None):
        self.request_id = uuid4().hex
        self.source_enabled = bool(source_repository)
        self.knowledge_enabled = False
        self.knowledge_error = False
        knowledge = None
        if knowledge_base is not None:
            try:
                self.knowledge_enabled = bool(knowledge_base.stats()["chunks"])
                if self.knowledge_enabled:
                    knowledge = {
                        "directory": str(knowledge_base.persist_directory.resolve()),
                        "collection": knowledge_base.collection_name,
                    }
            except Exception:
                self.knowledge_error = True
        self.payload = {
            "request_id": self.request_id,
            "records": [r.to_dict() for r in repository.records],
            "sources": dict(source_repository.files) if source_repository else {},
            "knowledge": knowledge,
        }
        self._directory = None
        self.path = None

    def __enter__(self):
        self._directory = TemporaryDirectory(prefix="logpilot-mcp-")
        try:
            self.path = Path(self._directory.name) / "context.json"
            self.path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
            self.path.chmod(0o600)
            return self
        except BaseException:
            self._directory.cleanup()
            raise

    def __exit__(self, *args):
        if self._directory is not None:
            self._directory.cleanup()

