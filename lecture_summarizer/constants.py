from pathlib import Path
from typing import Final

# Models
GEMINI_2_FLASH: Final = "gemini-2.0-flash"
GEMINI_2_FLASH_THINKING_EXP: Final = "gemini-2.0-flash-thinking-exp-01-21"

AVAILABLE_MODELS = (GEMINI_2_FLASH, GEMINI_2_FLASH_THINKING_EXP)

# Rate limits per minute
RATE_LIMITS = {
    GEMINI_2_FLASH: 15,
    GEMINI_2_FLASH_THINKING_EXP: 10,
}
RATE_LIMIT_WINDOW_SEC: Final = 60

# Defaults
OUTPUT_DIR = Path("output")
TEMPLATES_DIR = Path("templates")
PICKLES_DIRNAME = "pickles"
FULL_RESPONSE_DIRNAME = "full-response"
PAGE_IMAGES_DIRNAME = "page-images"
