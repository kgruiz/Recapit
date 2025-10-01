from dataclasses import dataclass
from pathlib import Path
import os

from .constants import OUTPUT_DIR, TEMPLATES_DIR


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    output_dir: Path = OUTPUT_DIR
    templates_dir: Path = TEMPLATES_DIR

    @staticmethod
    def from_env() -> "AppConfig":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        return AppConfig(api_key=api_key)
