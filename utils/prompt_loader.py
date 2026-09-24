from pathlib import Path


PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(file_name: str) -> str:
    path = PROMPT_ROOT / file_name
    return path.read_text(encoding="utf-8").strip()
