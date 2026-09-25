"""Application paths resolved independently of the current working directory."""
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PROJECT_ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "chroma_db"
EVALUATION_RESULTS_DIR = DATA_DIR / "evaluation_results"
SAMPLES_DIR = PROJECT_ROOT / "samples"
PROMPT_DIR = PACKAGE_ROOT / "prompts"
EVALUATION_CASES = PACKAGE_ROOT / "evaluation" / "cases.json"
