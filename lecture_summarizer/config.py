from collections import deque
from pathlib import Path
from rich.console import Console

console = Console()

# Global deque to track request times across instances
GLOBAL_REQUEST_TIMES = deque()

# Define models
GEMINI_2_FLASH = "gemini-2.0-flash"
GEMINI_2_FLASH_THINKING_EXPERIMENTAL = "gemini-2.0-flash-thinking-exp-01-21"

# Define rate limits
RATE_LIMITS = {GEMINI_2_FLASH: 15, GEMINI_2_FLASH_THINKING_EXPERIMENTAL: 10}
RATE_LIMIT_WINDOW_SEC = 60

# TODO: Deprecated
RATE_LIMIT_PER_MINUTE = 15

MAX_USED_RATE_LIMIT = None

AVAILABLE_MODELS = [GEMINI_2_FLASH, GEMINI_2_FLASH_THINKING_EXPERIMENTAL]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load latex preambles
SLIDE_LATEX_PREAMBLE_PATH = Path("templates", "slide-template.txt")
if not SLIDE_LATEX_PREAMBLE_PATH.exists():
    raise FileNotFoundError(
        f"Slides latex preamble file {SLIDE_LATEX_PREAMBLE_PATH} not found"
    )
SLIDE_LATEX_PREAMBLE = SLIDE_LATEX_PREAMBLE_PATH.read_text()

LECTURE_LATEX_PREAMBLE_PATH = Path("templates", "lecture-template.txt")
if not LECTURE_LATEX_PREAMBLE_PATH.exists():
    raise FileNotFoundError(
        f"Lecture latex preamble file {LECTURE_LATEX_PREAMBLE_PATH} not found"
    )
LECTURE_LATEX_PREAMBLE = LECTURE_LATEX_PREAMBLE_PATH.read_text()

DOCUMENT_LATEX_PREAMBLE_PATH = Path("templates", "document-template.txt")
if not DOCUMENT_LATEX_PREAMBLE_PATH.exists():
    raise FileNotFoundError(
        f"Document latex preamble file {DOCUMENT_LATEX_PREAMBLE_PATH} not found"
    )
DOCUMENT_LATEX_PREAMBLE = DOCUMENT_LATEX_PREAMBLE_PATH.read_text()

IMAGE_LATEX_PREAMBLE_PATH = Path("templates", "image-template.txt")
if not IMAGE_LATEX_PREAMBLE_PATH.exists():
    raise FileNotFoundError(
        f"Image latex preamble file {IMAGE_LATEX_PREAMBLE_PATH} not found"
    )
IMAGE_LATEX_PREAMBLE = IMAGE_LATEX_PREAMBLE_PATH.read_text()

LATEX_TO_MARKDOWN_PROMPT_PATH = Path("templates", "latex-to-md-template.txt")
if not LATEX_TO_MARKDOWN_PROMPT_PATH.exists():
    raise FileNotFoundError(
        f"Latex to Markdown prompt file {LATEX_TO_MARKDOWN_PROMPT_PATH} not found"
    )
LATEX_TO_MARKDOWN_PROMPT = LATEX_TO_MARKDOWN_PROMPT_PATH.read_text()

LATEX_TO_JSON_PROMPT_PATH = Path("templates", "latex-to-json-template.txt")
if not LATEX_TO_JSON_PROMPT_PATH.exists():
    raise FileNotFoundError(
        f"Latex to JSON prompt file {LATEX_TO_JSON_PROMPT_PATH} not found"
    )
LATEX_TO_JSON_PROMPT = LATEX_TO_JSON_PROMPT_PATH.read_text()

# Common slide dirs and patterns
MATH_465_SLIDES_DIR = Path("/Users/kadengruizenga/Documents/School/W25/Math465/Slides")
MATH_425_SLIDES_DIR = Path("/Users/kadengruizenga/Documents/School/W25/Math425/Slides")
EECS_476_SLIDES_DIR = Path("/Users/kadengruizenga/Documents/School/W25/EECS476/Lecture-Notes")

MATH_465_PATTERN = r"465 Lecture (\d+).pdf"
MATH_425_PATTERN = r"Lecture(\d+).pdf"
EECS_476_PATTERN = r"lec(\d+).*"
