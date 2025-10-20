from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any
import os
import yaml

from .constants import (
    DEFAULT_MODEL,
    TEMPLATES_DIR,
    DEFAULT_VIDEO_TOKEN_LIMIT,
    DEFAULT_MAX_WORKERS,
    DEFAULT_MAX_VIDEO_WORKERS,
)
from .video import (
    VideoEncoderPreference,
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_MAX_CHUNK_BYTES,
    DEFAULT_TOKENS_PER_SECOND,
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected integer value, got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"Expected integer ≥ {minimum}, got {parsed}")
    return parsed


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    output_dir: Optional[Path] = None
    templates_dir: Path = TEMPLATES_DIR
    default_model: str = DEFAULT_MODEL
    save_full_response: bool = False
    save_intermediates: bool = False
    video_token_limit: int | None = DEFAULT_VIDEO_TOKEN_LIMIT
    video_tokens_per_second: float = DEFAULT_TOKENS_PER_SECOND
    video_max_chunk_seconds: float = DEFAULT_MAX_CHUNK_SECONDS
    video_max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES
    media_resolution: str = "default"
    max_workers: int = DEFAULT_MAX_WORKERS
    max_video_workers: int = DEFAULT_MAX_VIDEO_WORKERS
    video_encoder_preference: VideoEncoderPreference = VideoEncoderPreference.AUTO
    presets: dict[str, dict[str, object]] = field(default_factory=dict)
    exports: list[str] = field(default_factory=list)
    config_path: Path | None = None
    pricing_file: Path | None = None

    @staticmethod
    def from_sources(config_path: Path | None = None) -> "AppConfig":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        env_config = os.getenv("LECTURE_SUMMARIZER_CONFIG")
        candidates: list[Path] = []
        if config_path is not None:
            candidates.append(Path(config_path).expanduser())
        elif env_config:
            candidates.append(Path(env_config).expanduser())
        else:
            candidates.extend(
                [
                    Path("lecture-summarizer.yaml"),
                    Path("lecture-summarizer.yml"),
                ]
            )

        resolved_config: Path | None = None
        config_data: dict[str, Any] = {}
        for candidate in candidates:
            if candidate.exists():
                resolved_config = candidate
                try:
                    config_data = yaml.safe_load(candidate.read_text()) or {}
                except yaml.YAMLError as exc:  # pragma: no cover - invalid user input
                    raise ValueError(f"Failed to parse configuration file {candidate}: {exc}") from exc
                break

        if config_path is not None and resolved_config is None:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        defaults_cfg = config_data.get("defaults", {}) if isinstance(config_data, dict) else {}
        save_cfg = config_data.get("save", {}) if isinstance(config_data, dict) else {}
        video_cfg = config_data.get("video", {}) if isinstance(config_data, dict) else {}
        presets_cfg = config_data.get("presets", {}) if isinstance(config_data, dict) else {}

        def _coerce_path(value: Any) -> Path | None:
            if value in {None, "", False}:  # pragma: no branch - explicit for clarity
                return None
            return Path(str(value)).expanduser()

        output_dir = _coerce_path(defaults_cfg.get("output_dir"))
        templates_dir = _coerce_path(config_data.get("templates_dir")) or Path(TEMPLATES_DIR)
        default_model = str(defaults_cfg.get("model", DEFAULT_MODEL))
        exports_raw = defaults_cfg.get("exports", [])
        exports: list[str] = []
        if isinstance(exports_raw, (list, tuple)):
            exports = [str(item) for item in exports_raw if str(item).strip()]

        save_full_response = _as_bool(save_cfg.get("full_response"))
        save_intermediates = _as_bool(save_cfg.get("intermediates"))

        video_token_limit = _as_int(video_cfg.get("token_limit"), minimum=1)
        if video_token_limit is None:
            video_token_limit = DEFAULT_VIDEO_TOKEN_LIMIT
        tokens_per_second = float(video_cfg.get("tokens_per_second", DEFAULT_TOKENS_PER_SECOND))
        max_chunk_seconds = float(video_cfg.get("max_chunk_seconds", DEFAULT_MAX_CHUNK_SECONDS))
        max_chunk_bytes = int(video_cfg.get("max_chunk_bytes", DEFAULT_MAX_CHUNK_BYTES))
        media_resolution = str(video_cfg.get("media_resolution", "default")).strip().lower()
        if media_resolution not in {"default", "low"}:
            media_resolution = "default"

        encoder_pref = video_cfg.get("encoder")

        pricing_file = _coerce_path(config_data.get("pricing_file"))

        # Environment overrides
        output_dir_env = os.getenv("LECTURE_SUMMARIZER_OUTPUT_DIR")
        if output_dir_env:
            output_dir = Path(output_dir_env).expanduser()

        templates_dir_env = os.getenv("LECTURE_SUMMARIZER_TEMPLATES_DIR")
        if templates_dir_env:
            templates_dir = Path(templates_dir_env).expanduser()

        default_model_env = os.getenv("LECTURE_SUMMARIZER_DEFAULT_MODEL")
        if default_model_env:
            default_model = default_model_env

        save_full_env = os.getenv("LECTURE_SUMMARIZER_SAVE_FULL_RESPONSE")
        if save_full_env is not None:
            save_full_response = _as_bool(save_full_env)

        save_inter_env = os.getenv("LECTURE_SUMMARIZER_SAVE_INTERMEDIATES")
        if save_inter_env is not None:
            save_intermediates = _as_bool(save_inter_env)

        video_token_env = os.getenv("LECTURE_SUMMARIZER_VIDEO_TOKEN_LIMIT")
        if video_token_env:
            token_override = _as_int(video_token_env, minimum=1)
            if token_override is not None:
                video_token_limit = token_override

        def _parse_workers(env_var: str, default: int) -> int:
            raw = os.getenv(env_var)
            if not raw:
                return default
            parsed = _as_int(raw, minimum=1)
            assert parsed is not None
            return parsed

        max_workers = _parse_workers("LECTURE_SUMMARIZER_MAX_WORKERS", DEFAULT_MAX_WORKERS)
        max_video_workers = _parse_workers("LECTURE_SUMMARIZER_MAX_VIDEO_WORKERS", DEFAULT_MAX_VIDEO_WORKERS)

        encoder_env = os.getenv("LECTURE_SUMMARIZER_VIDEO_ENCODER", encoder_pref)
        video_encoder_preference = VideoEncoderPreference.parse(encoder_env)

        # Presets from configuration (lowercase keys)
        presets: dict[str, dict[str, object]] = {}
        if isinstance(presets_cfg, dict):
            for key, value in presets_cfg.items():
                if isinstance(value, dict):
                    presets[str(key).lower()] = value

        return AppConfig(
            api_key=api_key,
            output_dir=output_dir,
            templates_dir=templates_dir,
            default_model=default_model,
            save_full_response=save_full_response,
            save_intermediates=save_intermediates,
            video_token_limit=video_token_limit,
            video_tokens_per_second=tokens_per_second,
            video_max_chunk_seconds=max_chunk_seconds,
            video_max_chunk_bytes=max_chunk_bytes,
            media_resolution=media_resolution,
            max_workers=max_workers,
            max_video_workers=max_video_workers,
            video_encoder_preference=video_encoder_preference,
            presets=presets,
            exports=exports,
            config_path=resolved_config,
            pricing_file=pricing_file,
        )

    @staticmethod
    def from_env(config_path: Path | None = None) -> "AppConfig":
        return AppConfig.from_sources(config_path=config_path)
