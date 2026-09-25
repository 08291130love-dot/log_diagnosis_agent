from logpilot.config.paths import PROMPT_DIR


def load_prompt(file_name: str) -> str:
    path = PROMPT_DIR / file_name
    return path.read_text(encoding="utf-8").strip()
