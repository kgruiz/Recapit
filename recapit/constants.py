from pathlib import Path
from typing import Final

# Models
GEMINI_2_5_FLASH: Final = "gemini-2.5-flash"
GEMINI_2_5_FLASH_LITE: Final = "gemini-2.5-flash-lite"
GEMINI_2_5_PRO: Final = "gemini-2.5-pro"
GEMINI_2_FLASH: Final = "gemini-2.0-flash"
GEMINI_2_FLASH_THINKING_EXP: Final = "gemini-2.0-flash-thinking-exp-01-21"

AVAILABLE_MODELS: Final = (
    GEMINI_2_5_FLASH,
    GEMINI_2_5_FLASH_LITE,
    GEMINI_2_5_PRO,
    GEMINI_2_FLASH,
    GEMINI_2_FLASH_THINKING_EXP,
)

# Capabilities advertised by Google Gemini models.
MODEL_CAPABILITIES = {
    GEMINI_2_5_FLASH: frozenset({"text", "image", "audio", "video"}),
    GEMINI_2_5_FLASH_LITE: frozenset({"text", "image", "audio", "video", "pdf"}),
    GEMINI_2_5_PRO: frozenset({"text", "image", "audio", "video", "pdf"}),
    GEMINI_2_FLASH: frozenset({"text", "image"}),
    GEMINI_2_FLASH_THINKING_EXP: frozenset({"text", "image"}),
}

# Rate limits per minute (conservative defaults; override via env if needed)
RATE_LIMITS = {
    GEMINI_2_5_FLASH: 20,
    GEMINI_2_5_FLASH_LITE: 10,
    GEMINI_2_5_PRO: 6,
    GEMINI_2_FLASH: 15,
    GEMINI_2_FLASH_THINKING_EXP: 10,
}
RATE_LIMIT_WINDOW_SEC: Final = 60

TOKEN_LIMITS_PER_MINUTE = {
    GEMINI_2_5_FLASH: 600_000,
    GEMINI_2_5_FLASH_LITE: 600_000,
    GEMINI_2_5_PRO: 600_000,
    GEMINI_2_FLASH: 600_000,
    GEMINI_2_FLASH_THINKING_EXP: 400_000,
}

MODEL_PRICING = {
    GEMINI_2_5_PRO: {
        "text": {"input": 3.50, "output": 10.00},
        "audio_video": {"input": 3.00, "output": 15.00},
    },
    GEMINI_2_5_FLASH: {
        "text": {"input": 0.35, "output": 1.05},
        "audio_video": {"input": 0.70, "output": 2.10},
    },
    GEMINI_2_5_FLASH_LITE: {
        "text": {"input": 0.10, "output": 0.40},
        "audio_video": {"input": 0.30, "output": 1.20},
    },
    GEMINI_2_FLASH: {
        "text": {"input": 0.10, "output": 0.40},
        "audio_video": {"input": 0.70, "output": 2.80},
    },
    GEMINI_2_FLASH_THINKING_EXP: {
        "text": {"input": 0.15, "output": 0.50},
        "audio_video": {"input": 0.70, "output": 2.80},
    },
    "default": {
        "text": {"input": 0.0, "output": 0.0},
        "audio_video": {"input": 0.0, "output": 0.0},
    },
}

# Defaults
DEFAULT_MODEL: Final = GEMINI_2_5_FLASH_LITE
OUTPUT_DIR = Path("output")
TEMPLATES_DIR = Path("templates")
PICKLES_DIRNAME = "pickles"
CACHE_DIR = Path(".cache")
VIDEO_CACHE_DIR = CACHE_DIR / "video"
FULL_RESPONSE_DIRNAME = "full-response"
PAGE_IMAGES_DIRNAME = "page-images"
DEFAULT_VIDEO_TOKEN_LIMIT: Final = 300_000
DEFAULT_VIDEO_TOKENS_PER_SECOND: Final = 300
DEFAULT_MAX_WORKERS: Final = 4
DEFAULT_MAX_VIDEO_WORKERS: Final = 3
