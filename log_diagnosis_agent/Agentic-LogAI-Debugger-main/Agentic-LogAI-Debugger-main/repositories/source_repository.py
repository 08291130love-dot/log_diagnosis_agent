import re
from dataclasses import dataclass


STACK_FRAME_PATTERN = re.compile(
    r"\bat\s+(?P<class_name>[\w.$]+)\.(?P<method>[\w$<>]+)"
    r"\((?P<file_name>[^():]+\.java):(?P<line_number>\d+)\)"
)


@dataclass(frozen=True, slots=True)
class StackFrame:
    class_name: str
    method: str
    file_name: str
    line_number: int


def extract_stack_frames(stack_trace: str | None) -> list[StackFrame]:
    """Extract Java source locations from a stack trace in their original order."""
    if not stack_trace:
        return []
    return [
        StackFrame(
            class_name=match.group("class_name"),
            method=match.group("method"),
            file_name=match.group("file_name"),
            line_number=int(match.group("line_number")),
        )
        for match in STACK_FRAME_PATTERN.finditer(stack_trace)
    ]


class SourceRepository:
    """Read-only in-memory repository for user-supplied Java source files."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = {
            path.replace("\\", "/"): content
            for path, content in (files or {}).items()
            if path.lower().endswith(".java")
        }

    def __bool__(self) -> bool:
        return bool(self.files)

    def list_files(self, query: str = "") -> dict:
        query = query.casefold().strip()
        paths = [path for path in sorted(self.files) if not query or query in path.casefold()]
        return {"total": len(paths), "files": paths[:100], "truncated": len(paths) > 100}

    def find_file(self, file_name: str) -> list[str]:
        requested = file_name.replace("\\", "/").casefold().strip()
        requested_base = requested.rsplit("/", 1)[-1]
        exact_paths = [path for path in self.files if path.casefold() == requested]
        if exact_paths:
            return exact_paths
        return [
            path
            for path in self.files
            if path.rsplit("/", 1)[-1].casefold() == requested_base
        ]

    def read_context(self, file_name: str, line_number: int, window: int = 8) -> dict:
        matches = self.find_file(file_name)
        if not matches:
            return {"error": f"未找到源码文件：{file_name}"}
        if len(matches) > 1:
            return {"error": f"存在多个同名文件，请使用完整路径：{file_name}", "matches": matches}

        path = matches[0]
        lines = self.files[path].splitlines()
        if not lines:
            return {"error": f"源码文件为空：{path}"}
        if line_number < 1 or line_number > len(lines):
            return {
                "error": f"行号超出范围：{path}:{line_number}",
                "file": path,
                "total_lines": len(lines),
            }

        window = max(1, min(window, 30))
        start = max(1, line_number - window)
        end = min(len(lines), line_number + window)
        numbered_code = "\n".join(
            f"{number:>4} | {lines[number - 1]}" for number in range(start, end + 1)
        )
        return {
            "file": path,
            "requested_line": line_number,
            "lines": f"{start}-{end}",
            "code": numbered_code,
        }

    def search(self, keyword: str, limit: int = 20) -> dict:
        keyword = keyword.strip()
        if not keyword:
            return {"error": "源码搜索关键词不能为空"}
        matches = []
        for path, content in self.files.items():
            for line_number, line in enumerate(content.splitlines(), 1):
                if keyword.casefold() in line.casefold():
                    matches.append({"file": path, "line": line_number, "code": line.strip()})
        limit = max(1, min(limit, 50))
        return {
            "total": len(matches),
            "matches": matches[:limit],
            "truncated": len(matches) > limit,
        }
