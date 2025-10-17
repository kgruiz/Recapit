from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import os

from .constants import (
    DEFAULT_MODEL,
    TEMPLATES_DIR,
    DEFAULT_VIDEO_TOKEN_LIMIT,
    DEFAULT_MAX_WORKERS,
    DEFAULT_MAX_VIDEO_WORKERS,
)
from .video import VideoEncoderPreference


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    output_dir: Optional[Path] = None
    templates_dir: Path = TEMPLATES_DIR
    default_model: str = DEFAULT_MODEL
    save_full_response: bool = False
    save_intermediates: bool = False
    video_token_limit: int = DEFAULT_VIDEO_TOKEN_LIMIT
    max_workers: int = DEFAULT_MAX_WORKERS
    max_video_workers: int = DEFAULT_MAX_VIDEO_WORKERS
    video_encoder_preference: VideoEncoderPreference = VideoEncoderPreference.AUTO

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
        save_intermediate_raw = os.getenv("LECTURE_SUMMARIZER_SAVE_INTERMEDIATES", "0").strip().lower()
        save_intermediates = save_intermediate_raw in {"1", "true", "yes", "on"}
        video_token_limit_raw = os.getenv("LECTURE_SUMMARIZER_VIDEO_TOKEN_LIMIT")
        video_token_limit = DEFAULT_VIDEO_TOKEN_LIMIT
        if video_token_limit_raw:
            try:
                video_token_limit = max(1, int(video_token_limit_raw))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid LECTURE_SUMMARIZER_VIDEO_TOKEN_LIMIT '{video_token_limit_raw}'; expected integer"
                ) from exc

        def _parse_workers(env_var: str, default: int) -> int:
            raw = os.getenv(env_var)
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"Invalid {env_var} '{raw}'; expected integer") from exc
            if value <= 0:
                raise ValueError(f"{env_var} must be a positive integer, got {raw}")
            return value

        max_workers = _parse_workers("LECTURE_SUMMARIZER_MAX_WORKERS", DEFAULT_MAX_WORKERS)
        max_video_workers = _parse_workers("LECTURE_SUMMARIZER_MAX_VIDEO_WORKERS", DEFAULT_MAX_VIDEO_WORKERS)
        video_encoder_pref_raw = os.getenv("LECTURE_SUMMARIZER_VIDEO_ENCODER")
        video_encoder_preference = VideoEncoderPreference.parse(video_encoder_pref_raw)

        return AppConfig(
            api_key=api_key,
            output_dir=output_dir,
            templates_dir=templates_dir,
            default_model=default_model,
            save_full_response=save_full_response,
            save_intermediates=save_intermediates,
            video_token_limit=video_token_limit,
            max_workers=max_workers,
            max_video_workers=max_video_workers,
            video_encoder_preference=video_encoder_preference,
        )
