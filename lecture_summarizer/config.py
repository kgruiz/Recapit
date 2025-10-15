from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import os

from .constants import DEFAULT_MODEL, TEMPLATES_DIR, DEFAULT_VIDEO_TOKEN_LIMIT


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    output_dir: Optional[Path] = None
    templates_dir: Path = TEMPLATES_DIR
    default_model: str = DEFAULT_MODEL
    save_full_response: bool = False
    video_token_limit: int = DEFAULT_VIDEO_TOKEN_LIMIT

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
        video_token_limit_raw = os.getenv("LECTURE_SUMMARIZER_VIDEO_TOKEN_LIMIT")
        video_token_limit = DEFAULT_VIDEO_TOKEN_LIMIT
        if video_token_limit_raw:
            try:
                video_token_limit = max(1, int(video_token_limit_raw))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid LECTURE_SUMMARIZER_VIDEO_TOKEN_LIMIT '{video_token_limit_raw}'; expected integer"
                ) from exc

        return AppConfig(
            api_key=api_key,
            output_dir=output_dir,
            templates_dir=templates_dir,
            default_model=default_model,
            save_full_response=save_full_response,
            video_token_limit=video_token_limit,
        )
