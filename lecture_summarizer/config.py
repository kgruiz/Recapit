from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import os

from .constants import DEFAULT_MODEL, TEMPLATES_DIR


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    output_dir: Optional[Path] = None
    templates_dir: Path = TEMPLATES_DIR
    default_model: str = DEFAULT_MODEL
    save_full_response: bool = False

    @staticmethod
    def from_env() -> "AppConfig":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        output_dir_raw = os.getenv("LECTURE_SUMMARIZER_OUTPUT_DIR")
        output_dir = Path(output_dir_raw).expanduser() if output_dir_raw else None
        templates_dir = Path(os.getenv("LECTURE_SUMMARIZER_TEMPLATES_DIR", TEMPLATES_DIR))
        default_model = os.getenv("LECTURE_SUMMARIZER_DEFAULT_MODEL", DEFAULT_MODEL)
        save_full_raw = os.getenv("LECTURE_SUMMARIZER_SAVE_FULL_RESPONSE", "0").strip().lower()
        save_full_response = save_full_raw in {"1", "true", "yes", "on"}

        return AppConfig(
            api_key=api_key,
            output_dir=output_dir,
            templates_dir=templates_dir,
            default_model=default_model,
            save_full_response=save_full_response,
        )
