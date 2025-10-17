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
