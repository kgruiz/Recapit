import enum
import os
import pickle
import re
import shutil
import time
from collections import OrderedDict, deque
from pathlib import Path

import PIL.Image
from google import genai
from google.genai import types
from natsort import natsorted
from pdf2image import convert_from_path
from PyPDF2 import PdfReader
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# TODO: If a rate limit error is raised, keep going after waiting.
# TODO: Add other slide formats (German 322)
# TODO: Change default output dir for transribe image functions to be a new directory in same parent dir as input images
# TODO: Add different models and auto change rate limit
# TODO: Include function in console error message
# TODO: Add config for models? Ex:
# config=types.GenerateContentConfig(
#         temperature=0.7,
#         max_output_tokens=150
# )


# TODO: Add model param to all "_" functions

# TODO: Always ensure models arent deprectaed

# TODO: Verify type and value of all params, especially limiter type

# TODO: Also limit TPM and RPD

# TODO: Convert functions to use code block removal function

# TODO: Need to handle multiple rate limits used in a run

# TODO: Except and keep going:
# ⠼ Transcribing individual image files  93% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╸━━━━━━━ 25/27 • 0:03:06 • 0:00:26
# ⠼ Transcribing setting-alarm.png        0% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 0/1   • 0:00:00 • -:--:--
# Traceback (most recent call last):
#   File "/Users/kadengruizenga/Developer/Projects/LectureSummarizer/main.py", line 2311, in <module>
#     TranscribeImages(a)
#   File "/Users/kadengruizenga/Developer/Projects/LectureSummarizer/main.py", line 2108, in TranscribeImages
#     _TranscribeImage(
#   File "/Users/kadengruizenga/Developer/Projects/LectureSummarizer/main.py", line 1363, in _TranscribeImage
#     response = client.models.generate_content(
#                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/models.py", line 4410, in generate_content
#     response = self._generate_content(
#                ^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/models.py", line 3669, in _generate_content
#     response_dict = self.api_client.request(
#                     ^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/_api_client.py", line 374, in request
#     response = self._request(http_request, stream=False)
#                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/_api_client.py", line 309, in _request
#     return self._request_unauthorized(http_request, stream)
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/_api_client.py", line 332, in _request_unauthorized
#     errors.APIError.raise_for_response(response)
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/errors.py", line 102, in raise_for_response
#     raise ServerError(status_code, response)
# google.genai.errors.ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The service is currently unavailable.', 'status': 'UNAVAILABLE'}}
# (basic) 11:41:25 ~/Developer/Projects/LectureSummarizer %

# TODO: Same as above, except and keep going:
# ⠧ Transcribing 465 Lecture 1.pdf               0% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  0/16   • 0:00:38 • -:--:--
# ⠧ Transcribing slides from 465 Lecture 1.pdf   5% ━━━━━━╸━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  14/272 • 0:00:38 • 0:11:13
# Traceback (most recent call last):
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 787, in urlopen
#     response = self._make_request(
#                ^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 488, in _make_request
#     raise new_e
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 464, in _make_request
#     self._validate_conn(conn)
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 1093, in _validate_conn
#     conn.connect()
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connection.py", line 741, in connect
#     sock_and_verified = _ssl_wrap_socket_and_match_hostname(
#                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connection.py", line 920, in _ssl_wrap_socket_and_match_hostname
#     ssl_sock = ssl_wrap_socket(
#                ^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/util/ssl_.py", line 460, in ssl_wrap_socket
#     ssl_sock = _ssl_wrap_socket_impl(sock, context, tls_in_tls, server_hostname)
#                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/util/ssl_.py", line 504, in _ssl_wrap_socket_impl
#     return ssl_context.wrap_socket(sock, server_hostname=server_hostname)
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/ssl.py", line 455, in wrap_socket
#     return self.sslsocket_class._create(
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/ssl.py", line 1042, in _create
#     self.do_handshake()
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/ssl.py", line 1320, in do_handshake
#     self._sslobj.do_handshake()
# ConnectionResetError: [Errno 54] Connection reset by peer

# During handling of the above exception, another exception occurred:

# Traceback (most recent call last):
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/requests/adapters.py", line 667, in send
#     resp = conn.urlopen(
#            ^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 841, in urlopen
#     retries = retries.increment(
#               ^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/util/retry.py", line 474, in increment
#     raise reraise(type(error), error, _stacktrace)
#           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/util/util.py", line 38, in reraise
#     raise value.with_traceback(tb)
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 787, in urlopen
#     response = self._make_request(
#                ^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 488, in _make_request
#     raise new_e
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 464, in _make_request
#     self._validate_conn(conn)
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connectionpool.py", line 1093, in _validate_conn
#     conn.connect()
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connection.py", line 741, in connect
#     sock_and_verified = _ssl_wrap_socket_and_match_hostname(
#                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/connection.py", line 920, in _ssl_wrap_socket_and_match_hostname
#     ssl_sock = ssl_wrap_socket(
#                ^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/util/ssl_.py", line 460, in ssl_wrap_socket
#     ssl_sock = _ssl_wrap_socket_impl(sock, context, tls_in_tls, server_hostname)
#                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/urllib3/util/ssl_.py", line 504, in _ssl_wrap_socket_impl
#     return ssl_context.wrap_socket(sock, server_hostname=server_hostname)
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/ssl.py", line 455, in wrap_socket
#     return self.sslsocket_class._create(
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/ssl.py", line 1042, in _create
#     self.do_handshake()
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/ssl.py", line 1320, in do_handshake
#     self._sslobj.do_handshake()
# urllib3.exceptions.ProtocolError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))

# During handling of the above exception, another exception occurred:

# Traceback (most recent call last):
#   File "/Users/kadengruizenga/Developer/Projects/LectureSummarizer/main.py", line 2467, in <module>
#     TranscribeSlides(math465SlidesPath)
#   File "/Users/kadengruizenga/Developer/Projects/LectureSummarizer/main.py", line 1722, in TranscribeSlides
#     _TranscribeSlideImages(
#   File "/Users/kadengruizenga/Developer/Projects/LectureSummarizer/main.py", line 870, in _TranscribeSlideImages
#     response = client.models.generate_content(
#                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/models.py", line 4410, in generate_content
#     response = self._generate_content(
#                ^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/models.py", line 3669, in _generate_content
#     response_dict = self.api_client.request(
#                     ^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/_api_client.py", line 374, in request
#     response = self._request(http_request, stream=False)
#                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/_api_client.py", line 309, in _request
#     return self._request_unauthorized(http_request, stream)
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/google/genai/_api_client.py", line 324, in _request_unauthorized
#     response = http_session.request(
#                ^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/requests/sessions.py", line 589, in request
#     resp = self.send(prep, **send_kwargs)
#            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/requests/sessions.py", line 703, in send
#     r = adapter.send(request, **kwargs)
#         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   File "/Users/kadengruizenga/anaconda3/envs/basic/lib/python3.12/site-packages/requests/adapters.py", line 682, in send
#     raise ConnectionError(err, request=request)
# requests.exceptions.ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))
# (basic) 19:36:57 ~/Developer/Projects/LectureSummarizer %


# ### Best Gemini Models by Category with Stats

# #### LaTeX-Generated Math
# - Best Model: Gemini 2.0 Pro Exp. 02-05
#   - Stats: RPM: 2 | TPM: 1,000,000 | RPD: 50
# - Second Best: Gemini 2.0 Flash
#   - Stats: RPM: 15 | TPM: 1,000,000 | RPD: 1,500

# #### Full Text (Printed OCR)
# - Best Model: Gemini 2.0 Flash
#   - Stats: RPM: 15 | TPM: 1,000,000 | RPD: 1,500
# - Second Best: Gemini 2.0 Flash-Lite
#   - Stats: RPM: 30 | TPM: 1,000,000 | RPD: 1,500

# #### All Handwriting
# - Best Model: Gemini 2.0 Flash Thinking Exp. 01-21
#   - Stats: RPM: 10 | TPM: 4,000,000 | RPD: 1,500
# - Second Best: Gemini 2.0 Pro Exp. 02-05
#   - Stats: RPM: 2 | TPM: 1,000,000 | RPD: 50

# #### LaTeX Math + Full Text
# - Best Model: Gemini 2.0 Pro Exp. 02-05
#   - Stats: RPM: 2 | TPM: 1,000,000 | RPD: 50
# - Second Best: Gemini 2.0 Flash
#   - Stats: RPM: 15 | TPM: 1,000,000 | RPD: 1,500

# #### LaTeX Math + Handwriting
# - Best Model: Gemini 2.0 Pro Exp. 02-05
#   - Stats: RPM: 2 | TPM: 1,000,000 | RPD: 50
# - Second Best: Gemini 2.0 Flash Thinking Exp. 01-21
#   - Stats: RPM: 10 | TPM: 4,000,000 | RPD: 1,500

# #### All Three (Math + Text + Handwriting)
# - Best Model: Gemini 2.0 Pro Exp. 02-05
#   - Stats: RPM: 2 | TPM: 1,000,000 | RPD: 50
# - Second Best: Gemini 2.0 Flash Thinking Exp. 01-21
#   - Stats: RPM: 10 | TPM: 4,000,000 | RPD: 1,500

# ### Key Takeaways
# - Best for Math & LaTeX OCR → Pro Exp. 02-05 (Most accurate, but slowest)
# - Best for Handwriting OCR → Flash Thinking Exp. 01-21 (More context-aware)
# - Best for Fast General Text OCR → Flash & Flash-Lite (Faster, but less accurate for math/handwriting)

console = Console()

# Global deque to track request times across instances
GLOBAL_REQUEST_TIMES = deque()

# Define models
GEMINI_2_FLASH = "gemini-2.0-flash"
GEMINI_2_FLASH_THINKING_EXPERIMENTAL = "gemini-2.0-flash-thinking-exp"

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

# Common slide dirs and patterns
MATH_465_SLIDES_DIR = Path("/Users/kadengruizenga/Documents/School/W25/Math465/Slides")

MATH_425_SLIDES_DIR = Path("/Users/kadengruizenga/Documents/School/W25/Math425/Slides")

EECS_476_SLIDES_DIR = Path(
    "/Users/kadengruizenga/Documents/School/W25/EECS476/Lecture-Notes"
)


MATH_465_PATTERN = r"465 Lecture (\d+).pdf"
MATH_425_PATTERN = r"Lecture(\d+).pdf"
EECS_476_PATTERN = r"lec(\d+).*"


def _GetTotalPageCount(pdfFiles: list[Path]) -> int:
    """
    Compute the total number of pages across multiple PDF files.

    Parameters
    ----------
    pdfFiles : list[Path]
        A list of PDF file paths.

    Returns
    -------
    int
        The sum of all pages in the provided PDF files.
    """

    runningTotal = 0

    for pdfFile in pdfFiles:

        with pdfFile.open("rb") as pdf:

            reader = PdfReader(pdf)

            runningTotal += len(reader.pages)

    return runningTotal


def _RemoveCodeBlockSyntax(string: str | list[str]) -> str:

    if not isinstance(string, (str, list)):

        raise TypeError(
            f'Parameter "string" must be a str or list[str]. Given type: "{type(string).__name__}"'
        )

    if isinstance(string, str):
        responseText = responseText.splitlines()

    # if responseText[0].strip().startswith("```"):
    #     responseText = responseText[1:]
    # if responseText[-1].strip() == "```":
    #     responseText = responseText[:-1]

    # Strip code block markers only if both the first and last lines are "```",
    # ensuring the entire document is wrapped while preserving any internal code blocks
    # that should remain intact.
    if responseText[0].strip().startswith("```") and responseText[-1].strip() == "```":

        responseText[1:-1]

    return "\n".join(responseText) + "\n"


def PDFToPNG(pdfPath: Path, pagesDir: Path = None, progress=None):
    """
    Convert a PDF file to PNG images and save them to the specified directory.

    Parameters
    ----------
    pdfPath : Path
        The path to the PDF file.
    pagesDir : Path, optional
        The directory where the PNG images will be saved.
        Defaults to OUTPUT_DIR / f"{pdfPath.stem}-pages" if not provided.
    progress : Progress, optional
        A rich Progress instance for displaying progress.

    Returns
    -------
    None
    """

    if pagesDir is None:

        pagesDir = Path(OUTPUT_DIR, f"{pdfPath.stem}-pages")

    images = convert_from_path(pdfPath)

    if pagesDir.exists():
        shutil.rmtree(pagesDir)

    pagesDir.mkdir(parents=True, exist_ok=True)

    if progress is None:

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )

    elif not isinstance(progress, Progress):

        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:

        task = progress.add_task(f"Converting {pdfPath.name} to png", total=len(images))

        for i, image in enumerate(images):

            image.save(Path(pagesDir, f"{pdfPath.stem}-{i}.png"), "png")
            progress.update(task, advance=1)

        progress.remove_task(task)


def _SleepWithProgress(progress, task, sleepTime, defaultDescription):
    """
    Sleep for a specified duration while updating the progress bar.

    Parameters
    ----------
    progress : Progress
        The progress object.
    task : TaskID
        The task identifier to update.
    sleepTime : float
        The total time in seconds to sleep.
    defaultDescription : str
        The description to revert to after sleeping.

    Returns
    -------
    None
    """

    targetTime = time.time() + sleepTime

    while True:
        remaining = targetTime - time.time()
        if remaining <= 0:
            break
        progress.update(
            task, description=f"Sleeping for {remaining:.1f} sec due to rate limit"
        )
        time.sleep(min(0.5, remaining))
    progress.update(task, description=defaultDescription)


def _CleanResponse(
    combinedResponse: str,
    preamble: str,
    title: str | None = "",
    author: str | None = "",
    date: str | None = "",
) -> str:
    """
    Clean the combined LaTeX response by removing duplicate preamble lines and fixing formatting issues.

    Parameters
    ----------
    combinedResponse : str
        The raw combined LaTeX content.
    preamble : str
        The LaTeX preamble to use.
    title : str, optional
        Title to insert into the preamble.
    author : str, optional
        Author to insert into the preamble.
    date : str, optional
        Date to insert into the preamble.

    Returns
    -------
    str
        The cleaned LaTeX content.
    """

    preambleLines = preamble.splitlines()

    for line in preambleLines:

        combinedResponse = re.sub(
            rf"^{re.escape(line)}$", "", combinedResponse, flags=re.MULTILINE
        )

    END_DOCUMENT_LINE = r"\end{document}"

    combinedResponse = re.sub(
        rf"^{re.escape(END_DOCUMENT_LINE)}$",
        r"\\newpage",
        combinedResponse,
        flags=re.MULTILINE,
    )

    TITLE_LINE = r"\\title\{.*\}"
    AUTHOR_LINE = r"\\author\{.*\}"
    DATE_LINE = r"\\date\{.*\}"

    OTHER_LINES = [
        TITLE_LINE,
        AUTHOR_LINE,
        DATE_LINE,
    ]

    for line in OTHER_LINES:

        combinedResponse = re.sub(
            rf"^{line}$", "", combinedResponse, flags=re.MULTILINE
        )

    combinedResponse = combinedResponse.strip()

    combinedResponse = re.sub(r"\n{2,}", "\n\n", combinedResponse)

    remainingPackages = re.findall(
        r"^\\usepackage\{([^\}]*)\}", combinedResponse, flags=re.MULTILINE
    )

    for package in remainingPackages:

        preambleLines.insert(1, f"\\usepackage{{{package}}}")

    preamble = "\n".join(preambleLines)

    if re.search(r"^\\title\{.*\}", preamble, flags=re.MULTILINE) is None:

        if title is not None:

            preambleLines = preamble.splitlines()

            preambleLines.insert(1, f"\\title{{{title}}}")

            preamble = "\n".join(preambleLines)

    elif title is not None:

        preamble = re.sub(
            r"^\\title\{(.*)\}", rf"\\title{{{title}}}", preamble, flags=re.MULTILINE
        )

    else:

        preamble = re.sub(r"^\\title\{.*\}", "", preamble, flags=re.MULTILINE)

    if re.search(r"^\\author\{.*\}", preamble, flags=re.MULTILINE) is None:

        if author is not None:

            preambleLines = preamble.splitlines()

            preambleLines.insert(2, f"\\author{{{author}}}")

            preamble = "\n".join(preambleLines)

    elif author is not None:

        preamble = re.sub(
            r"^\\author\{(.*)\}", rf"\\author{{{author}}}", preamble, flags=re.MULTILINE
        )

    else:

        preamble = re.sub(r"^\\author\{.*\}", "", preamble, flags=re.MULTILINE)

    if re.search(r"^\\date\{.*\}", preamble, flags=re.MULTILINE) is None:

        if date is not None:

            preambleLines = preamble.splitlines()

            preambleLines.insert(3, f"\\date{{{date}}}")

            preamble = "\n".join(preambleLines)

    elif date is not None:

        preamble = re.sub(
            r"^\\date\{(.*)\}", rf"\\date{{{date}}}", preamble, flags=re.MULTILINE
        )

    else:

        preamble = re.sub(r"^\\date\{.*\}", "", preamble, flags=re.MULTILINE)

    cleanedResponse = f"{preamble}\n{combinedResponse}\n{END_DOCUMENT_LINE}"

    return cleanedResponse


def _TranscribeSlideImages(
    imageDir: Path,
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str = "transcribed",
    fullResponseDir: Path = None,
    progress=None,
    bulkPagesTask=None,
):
    """
    Transcribe slide images to LaTeX format using the API.

    Parameters
    ----------
    imageDir : Path
        Path to the directory containing the slide images.
    limiterMethod : str, optional
        Rate limiting method, either "fixedDelay" or "tracking". Defaults to "tracking".
    outputDir : Path, optional
        Directory where cleaned output (.tex) will be stored.
    outputName : str, optional
        Base name for the output files. Defaults to "transcribed".
    fullResponseDir : Path, optional
        Directory where full responses (.txt) and pickles will be stored.
        If not provided, defaults to outputDir.
    progress : Progress, optional
        A rich Progress instance for displaying progress.
    bulkPagesTask : TaskID, optional
        Additional task for tracking bulk page progress.

    Returns
    -------
    None
    """

    global GLOBAL_REQUEST_TIMES

    imageTuples = [
        (imagePath, PIL.Image.open(imagePath))
        for imagePath in natsorted(list(Path(imageDir).glob("*.png")))
    ]

    apiKey = os.getenv("GEMINI_API_KEY")
    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = OrderedDict()
    defaultDescription = "Transcribing Slides"

    currentLimiterMethod = limiterMethod
    if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
        currentLimiterMethod = "fixedDelay"
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE

    runID = time.time()

    if progress is None:

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )

    elif not isinstance(progress, Progress):

        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:

        task = progress.add_task(defaultDescription, total=len(imageTuples))

        for imagePath, image in imageTuples:

            currentTime = time.time()

            if currentLimiterMethod == "fixedDelay":

                startTime = currentTime

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, "
                                f"ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', "
                                f"etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. "
                                f"If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and "
                                f"describe the contents.\n\nLatex Preamble:{SLIDE_LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )

                except:

                    console.print(
                        f"n[bold red]Error during transcription of {imageDir}[/bold red]"
                    )

                    raise

                responses[imagePath.name] = response

                # Save responses as pickle in case of error
                if fullResponseDir is None:
                    fullResponseDir = outputDir
                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    # if picklePath.exists():

                    #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
                    #     picklePath = picklePath.with_name(uniquePath)

                    #     if picklePath.exists():
                    #         raise FileExistsError(
                    #             f"File {picklePath} already exists. Attempt to create unique file failed."
                    #         )

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(
                        f"{e}\n\n\n[bold red]Failed to save responses[/bold red]"
                    )

                elapsed = time.time() - startTime

                if elapsed < delayBetweenCalls:

                    sleepTime = delayBetweenCalls - elapsed
                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)

            elif currentLimiterMethod == "tracking":

                while (
                    GLOBAL_REQUEST_TIMES
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW_SEC
                ):
                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW_SEC - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )

                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0]
                        >= RATE_LIMIT_WINDOW_SEC
                    ):
                        GLOBAL_REQUEST_TIMES.popleft()

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, "
                                f"ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', "
                                f"etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. "
                                f"If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and "
                                f"describe the contents.\n\nLatex Preamble:{SLIDE_LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )

                except:

                    console.print(
                        f"n[bold red]Error during transcription of {imageDir}[/bold red]"
                    )

                    raise

                responses[imagePath.name] = response

                GLOBAL_REQUEST_TIMES.append(time.time())

                # Save responses as pickle in case of error
                if fullResponseDir is None:
                    fullResponseDir = outputDir
                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    # if picklePath.exists():

                    #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
                    #     picklePath = picklePath.with_name(uniquePath)

                    #     if picklePath.exists():
                    #         raise FileExistsError(
                    #             f"File {picklePath} already exists. Attempt to create unique file failed."
                    #         )

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(
                        f"{e}\n\n\n[bold red]Failed to save responses[/bold red]"
                    )

            else:
                raise ValueError(
                    "Invalid limiterMethod. Use 'fixedDelay' or 'tracking'."
                )

            progress.update(task, advance=1)

            if bulkPagesTask is not None:

                progress.update(bulkPagesTask, advance=1)

        # Save responses as pickle in case of error
        if fullResponseDir is None:
            fullResponseDir = outputDir
        localPickleDir = Path(fullResponseDir, "pickles")
        localPickleDir.mkdir(parents=True, exist_ok=True)

        try:

            picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

            # if picklePath.exists():

            #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
            #     picklePath = picklePath.with_name(uniquePath)

            #     if picklePath.exists():
            #         raise FileExistsError(
            #             f"File {picklePath} already exists. Attempt to create unique file failed."
            #         )

            with picklePath.open("wb") as file:
                pickle.dump(responses, file)

        except Exception as e:
            console.print(f"{e}\n\n\n[bold red]Failed to save responses[/bold red]")

        combinedResponse = ""

        for slideNum, (imageName, response) in enumerate(responses.items()):

            responseText: str | list[str] | None = response.text

            if responseText is None:

                combinedResponse += f"\n\\begin{{frame}}\n\\frametitle{{Slide {slideNum}: {imageName}}}\n\nError: Slide text content is None\n\n\\end{{frame}}"
                continue

            if isinstance(responseText, str):
                responseText = responseText.splitlines()

            if responseText[0].strip().startswith("```"):
                responseText = responseText[1:]
            if responseText[-1].strip() == "```":
                responseText = responseText[:-1]

            combinedResponse += "\n".join(responseText) + "\n"

        Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
        cleanedResponse = _CleanResponse(
            combinedResponse=combinedResponse, preamble=SLIDE_LATEX_PREAMBLE
        )
        Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)

        progress.remove_task(task)


def _TranscribeLectureImages(
    imageDir: Path,
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str = "transcribed",
    fullResponseDir: Path = None,
    progress=None,
    bulkPagesTask=None,
):
    """
    Transcribe lecture images to LaTeX format using the API.

    Parameters
    ----------
    imageDir : Path
        Path to the directory containing the lecture images.
    limiterMethod : str, optional
        Rate limiting method, either "fixedDelay" or "tracking". Defaults to "tracking".
    outputDir : Path, optional
        Directory where cleaned output (.tex) will be stored.
    outputName : str, optional
        Base name for the output files. Defaults to "transcribed".
    fullResponseDir : Path, optional
        Directory where full responses (.txt) and pickles will be stored.
        If not provided, defaults to outputDir.
    progress : Progress, optional
        A rich Progress instance for displaying progress.
    bulkPagesTask : TaskID, optional
        Additional task for tracking bulk page progress.

    Returns
    -------
    None
    """

    global GLOBAL_REQUEST_TIMES

    imageTuples = [
        (imagePath, PIL.Image.open(imagePath))
        for imagePath in natsorted(list(Path(imageDir).glob("*.png")))
    ]

    apiKey = os.getenv("GEMINI_API_KEY")
    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = OrderedDict()
    defaultDescription = "Transcribing Lecture"

    currentLimiterMethod = limiterMethod
    if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
        currentLimiterMethod = "fixedDelay"
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE

    runID = time.time()

    if progress is None:

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )

    elif not isinstance(progress, Progress):

        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:

        task = progress.add_task(defaultDescription, total=len(imageTuples))

        for imagePath, image in imageTuples:

            currentTime = time.time()

            if currentLimiterMethod == "fixedDelay":

                startTime = currentTime

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in latex format. Use the given lecture preamble as a base, "
                                f"ensuring any other needed packages or details are added. Escape characters like '&', '%', etc., properly. "
                                f"Do not include outside files. For graphics, either recreate with tikz or leave a placeholder.\n\nLatex Preamble:{LECTURE_LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )

                except:

                    console.print(
                        f"n[bold red]Error during transcription of {imageDir}[/bold red]"
                    )

                    raise

                responses[imagePath.name] = response

                # Save responses as pickle in case of error
                if fullResponseDir is None:
                    fullResponseDir = outputDir
                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    # if picklePath.exists():

                    #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
                    #     picklePath = picklePath.with_name(uniquePath)

                    #     if picklePath.exists():
                    #         raise FileExistsError(
                    #             f"File {picklePath} already exists. Attempt to create unique file failed."
                    #         )

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(
                        f"{e}\n\n\n[bold red]Failed to save responses[/bold red]"
                    )

                elapsed = time.time() - startTime

                if elapsed < delayBetweenCalls:

                    sleepTime = delayBetweenCalls - elapsed
                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)

            elif currentLimiterMethod == "tracking":

                while (
                    GLOBAL_REQUEST_TIMES
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW_SEC
                ):
                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW_SEC - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )

                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0]
                        >= RATE_LIMIT_WINDOW_SEC
                    ):
                        GLOBAL_REQUEST_TIMES.popleft()

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in latex format. Use the given lecture preamble as a base, "
                                f"ensuring any other needed packages or details are added. Escape characters like '&', '%', etc., properly. "
                                f"Do not include outside files. For graphics, either recreate with tikz or leave a placeholder.\n\nLatex Preamble:{LECTURE_LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )

                except:

                    console.print(
                        f"n[bold red]Error during transcription of {imageDir}[/bold red]"
                    )

                    raise

                responses[imagePath.name] = response

                GLOBAL_REQUEST_TIMES.append(time.time())

                # Save responses as pickle in case of error
                if fullResponseDir is None:
                    fullResponseDir = outputDir
                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    # if picklePath.exists():

                    #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
                    #     picklePath = picklePath.with_name(uniquePath)

                    #     if picklePath.exists():
                    #         raise FileExistsError(
                    #             f"File {picklePath} already exists. Attempt to create unique file failed."
                    #         )

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(
                        f"{e}\n\n\n[bold red]Failed to save responses[/bold red]"
                    )

            else:
                raise ValueError(
                    "Invalid limiterMethod. Use 'fixedDelay' or 'tracking'."
                )

            progress.update(task, advance=1)

            if bulkPagesTask is not None:

                progress.update(bulkPagesTask, advance=1)

        # Save responses as pickle in case of error
        if fullResponseDir is None:
            fullResponseDir = outputDir
        localPickleDir = Path(fullResponseDir, "pickles")
        localPickleDir.mkdir(parents=True, exist_ok=True)

        try:

            picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

            # if picklePath.exists():

            #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
            #     picklePath = picklePath.with_name(uniquePath)

            #     if picklePath.exists():
            #         raise FileExistsError(
            #             f"File {picklePath} already exists. Unique file creation failed."
            #         )

            with picklePath.open("wb") as file:
                pickle.dump(responses, file)

        except Exception as e:
            console.print(f"{e}\n\n\n[bold red]Failed to save responses[/bold red]")

        combinedResponse = ""

        for pageNum, (imageName, response) in enumerate(responses.items()):

            responseText: str | list[str] | None = response.text

            if responseText is None:

                combinedResponse += f"\n\\section{{Page {pageNum}: {imageName}}}\n\nError: Text content is None"
                continue

            if isinstance(responseText, str):
                responseText = responseText.splitlines()
            if responseText[0].strip().startswith("```"):
                responseText = responseText[1:]
            if responseText[-1].strip() == "```":
                responseText = responseText[:-1]

            combinedResponse += "\n".join(responseText) + "\n"

        Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
        cleanedResponse = _CleanResponse(
            combinedResponse=combinedResponse, preamble=LECTURE_LATEX_PREAMBLE
        )
        Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)

        progress.remove_task(task)


def _TranscribeDocumentImages(
    imageDir: Path,
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str = "transcribed",
    fullResponseDir: Path = None,
    progress=None,
    bulkPagesTask=None,
):
    """
    Transcribe document images to LaTeX format using the API.

    Parameters
    ----------
    imageDir : Path
        Path to the directory containing the document images.
    limiterMethod : str, optional
        Rate limiting method, either "fixedDelay" or "tracking". Defaults to "tracking".
    outputDir : Path, optional
        Directory where cleaned output (.tex) will be stored.
    outputName : str, optional
        Base name for the output files. Defaults to "transcribed".
    fullResponseDir : Path, optional
        Directory where full responses (.txt) and pickles will be stored.
        If not provided, defaults to outputDir.
    progress : Progress, optional
        A rich Progress instance for displaying progress.
    bulkPagesTask : TaskID, optional
        Additional task for tracking bulk page progress.

    Returns
    -------
    None
    """

    global GLOBAL_REQUEST_TIMES

    imageTuples = [
        (imagePath, PIL.Image.open(imagePath))
        for imagePath in natsorted(list(Path(imageDir).glob("*.png")))
    ]

    apiKey = os.getenv("GEMINI_API_KEY")

    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = OrderedDict()
    defaultDescription = "Transcribing Document"

    currentLimiterMethod = limiterMethod
    if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
        currentLimiterMethod = "fixedDelay"
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE

    runID = time.time()

    if progress is None:

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )

    elif not isinstance(progress, Progress):

        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:

        task = progress.add_task(defaultDescription, total=len(imageTuples))

        for imagePath, image in imageTuples:

            currentTime = time.time()

            if currentLimiterMethod == "fixedDelay":

                startTime = currentTime

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the document image, including all math, in LaTeX format. "
                                f"Use the document preamble as a base and add any necessary packages. "
                                f"Escape special characters appropriately. "
                                f"If there is a graphic, recreate it with tikz or leave a placeholder.\n\nLatex Preamble:{DOCUMENT_LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )

                except:

                    console.print(
                        f"n[bold red]Error during transcription of {imageDir}[/bold red]"
                    )

                    raise

                responses[imagePath.name] = response

                # Save responses as pickle in case of error
                if fullResponseDir is None:
                    fullResponseDir = outputDir
                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    # if picklePath.exists():

                    #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
                    #     picklePath = picklePath.with_name(uniquePath)

                    #     if picklePath.exists():
                    #         raise FileExistsError(
                    #             f"File {picklePath} already exists. Attempt to create unique file failed."
                    #         )

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(
                        f"{e}\n\n\n[bold red]Failed to save responses[/bold red]"
                    )

                elapsed = time.time() - startTime

                if elapsed < delayBetweenCalls:

                    sleepTime = delayBetweenCalls - elapsed
                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)

            elif currentLimiterMethod == "tracking":

                while (
                    GLOBAL_REQUEST_TIMES
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW_SEC
                ):
                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW_SEC - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )

                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0]
                        >= RATE_LIMIT_WINDOW_SEC
                    ):
                        GLOBAL_REQUEST_TIMES.popleft()

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the document image, including all math, in LaTeX format. "
                                f"Use the document preamble as a base and add any necessary packages. "
                                f"Escape special characters appropriately. "
                                f"If there is a graphic, recreate it with tikz or leave a placeholder.\n\nLatex Preamble:{DOCUMENT_LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )

                except:

                    console.print(
                        f"n[bold red]Error during transcription of {imageDir}[/bold red]"
                    )

                    raise

                responses[imagePath.name] = response

                GLOBAL_REQUEST_TIMES.append(time.time())

                # Save responses as pickle in case of error
                if fullResponseDir is None:
                    fullResponseDir = outputDir
                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    # if picklePath.exists():

                    #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
                    #     picklePath = picklePath.with_name(uniquePath)

                    #     if picklePath.exists():
                    #         raise FileExistsError(
                    #             f"File {picklePath} already exists. Attempt to create unique file failed."
                    #         )

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(
                        f"{e}\n\n\n[bold red]Failed to save responses[/bold red]"
                    )

            else:
                raise ValueError(
                    "Invalid limiterMethod. Use 'fixedDelay' or 'tracking'."
                )

            progress.update(task, advance=1)

            if bulkPagesTask is not None:

                progress.update(bulkPagesTask, advance=1)

        # Save responses as pickle in case of error
        if fullResponseDir is None:
            fullResponseDir = outputDir

        localPickleDir = Path(fullResponseDir, "pickles")
        localPickleDir.mkdir(parents=True, exist_ok=True)

        try:

            picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

            # if picklePath.exists():

            #     uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
            #     picklePath = picklePath.with_name(uniquePath)

            #     if picklePath.exists():
            #         raise FileExistsError(
            #             f"File {picklePath} already exists. Unique file creation failed."
            #         )

            with picklePath.open("wb") as file:

                pickle.dump(responses, file)
        except Exception as e:

            console.print(f"{e}\n\n\n[bold red]Failed to save responses[/bold red]")

        combinedResponse = ""

        for pageNum, (imageName, response) in enumerate(responses.items()):

            responseText: str | list[str] | None = response.text

            if responseText is None:
                combinedResponse += f"\n\\section{{Page {pageNum}: {imageName}}}\n\nError: Text content is None"
                continue

            if isinstance(responseText, str):
                responseText = responseText.splitlines()

            if responseText[0].strip().startswith("```"):
                responseText = responseText[1:]
            if responseText[-1].strip() == "```":
                responseText = responseText[:-1]

            combinedResponse += "\n".join(responseText) + "\n"

        Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
        cleanedResponse = _CleanResponse(
            combinedResponse=combinedResponse, preamble=DOCUMENT_LATEX_PREAMBLE
        )
        Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)

        progress.remove_task(task)


def _TranscribeImage(
    imageSource: Path | list[Path],
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str = "transcribed",
    fullResponseDir: Path = None,
    progress=None,
    bulkPagesTask=None,
):
    """
    Transcribe image files (assumed to be already in image format) to LaTeX using the API.

    Parameters
    ----------
    imageSource : Path or list[Path]
        A directory containing image files or a list of image file paths.
    limiterMethod : str, optional
        Rate limiting method ("fixedDelay" or "tracking"). Defaults to "tracking".
    outputDir : Path, optional
        Directory where cleaned output (.tex) will be stored.
    outputName : str, optional
        Base name for the output files.
    fullResponseDir : Path, optional
        Directory where full responses (.txt) and pickles will be stored.
    progress : Progress, optional
        A rich Progress instance for displaying progress.
    bulkPagesTask : TaskID, optional
        Additional task for tracking bulk page progress.

    Returns
    -------
    None
    """

    # Determine list of image files.
    if isinstance(imageSource, Path) and imageSource.is_dir():

        imagePaths = natsorted(list(imageSource.glob("*.png")))

    elif isinstance(imageSource, list):

        imagePaths = imageSource

    else:

        raise ValueError(
            "Parameter 'imageSource' must be a directory path or a list of image file paths."
        )

    imageTuples = [(imgPath, PIL.Image.open(imgPath)) for imgPath in imagePaths]

    apiKey = os.getenv("GEMINI_API_KEY")

    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = OrderedDict()
    defaultDescription = "Transcribing Image Files"

    currentLimiterMethod = limiterMethod

    if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
        currentLimiterMethod = "fixedDelay"

    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE
    runID = time.time()

    if progress is None:

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )

    elif not isinstance(progress, Progress):

        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:

        task = progress.add_task(defaultDescription, total=len(imageTuples))

        for imagePath, image in imageTuples:

            specificDescription = f"Transcribing {imagePath.name}"

            progress.update(
                task,
                description=specificDescription,
                advance=0,
                refresh=True,
            )

            currentTime = time.time()

            if currentLimiterMethod == "fixedDelay":

                startTime = currentTime

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in LaTeX format. Use the given preamble as a base, "
                                f"ensuring any other needed packages or details are added. Escape characters like '&', '%', etc., properly. "
                                f"If there's a graphic, either recreate it with tikz or leave a placeholder.\n\nLatex Preamble:{IMAGE_LATEX_PREAMBLE}",
                                image,
                            ),
                        ],
                    )

                except Exception as e:

                    console.print(
                        f"[bold red]Error during transcription of {imagePath.name}: {e}[/bold red]"
                    )
                    raise

                responses[imagePath.name] = response

                # Save responses as pickle in case of error.
                if fullResponseDir is None:

                    fullResponseDir = outputDir

                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    with picklePath.open("wb") as file:
                        pickle.dump(responses, file)

                except Exception as e:

                    console.print(f"[bold red]Failed to save responses: {e}[/bold red]")
                elapsed = time.time() - startTime

                if elapsed < delayBetweenCalls:

                    sleepTime = delayBetweenCalls - elapsed
                    _SleepWithProgress(progress, task, sleepTime, specificDescription)

            elif currentLimiterMethod == "tracking":

                while (
                    GLOBAL_REQUEST_TIMES
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW_SEC
                ):

                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW_SEC - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )
                    _SleepWithProgress(progress, task, sleepTime, specificDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0]
                        >= RATE_LIMIT_WINDOW_SEC
                    ):

                        GLOBAL_REQUEST_TIMES.popleft()

                try:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in LaTeX format. Use the given preamble as a base, "
                                f"ensuring any other needed packages or details are added. Escape characters like '&', '%', etc., properly. "
                                f"If there's a graphic, either recreate it with tikz or leave a placeholder.\n\nLatex Preamble:{IMAGE_LATEX_PREAMBLE}",
                                image,
                            ),
                        ],
                    )

                except Exception as e:

                    console.print(
                        f"[bold red]Error during transcription of {imagePath.name}: {e}[/bold red]"
                    )
                    raise

                responses[imagePath.name] = response
                GLOBAL_REQUEST_TIMES.append(time.time())

                if fullResponseDir is None:

                    fullResponseDir = outputDir

                localPickleDir = Path(fullResponseDir, "pickles")
                localPickleDir.mkdir(parents=True, exist_ok=True)

                try:

                    picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                    with picklePath.open("wb") as file:

                        pickle.dump(responses, file)

                except Exception as e:
                    console.print(f"[bold red]Failed to save responses: {e}[/bold red]")

            else:

                raise ValueError(
                    "Invalid limiterMethod. Use 'fixedDelay' or 'tracking'."
                )

            progress.update(task, advance=1, refresh=True)

            if bulkPagesTask is not None:
                progress.update(bulkPagesTask, advance=1, refresh=True)

        # Combine responses into a single LaTeX document.
        combinedResponse = ""

        for idx, (imageName, response) in enumerate(responses.items()):

            responseText: str | list[str] | None = response.text

            if responseText is None:

                combinedResponse += f"\n\\section{{Image {idx}: {imageName}}}\n\nError: Text content is None\n"
                continue

            if isinstance(responseText, str):
                responseText = responseText.splitlines()

            if responseText and responseText[0].strip().startswith("```"):
                responseText = responseText[1:]

            if responseText and responseText[-1].strip() == "```":
                responseText = responseText[:-1]

            combinedResponse += "\n".join(responseText) + "\n"

        Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)

        cleanedResponse = _CleanResponse(
            combinedResponse=combinedResponse, preamble=IMAGE_LATEX_PREAMBLE
        )

        Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)

        progress.remove_task(task)


def _LatexToMarkdown(
    latexSource: Path,
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str | None = None,
    fullResponseDir: Path = None,
    progress=None,
    bulkConversionTask=None,
    model: str = GEMINI_2_FLASH_THINKING_EXPERIMENTAL,
):
    """
    Transcribe a LaTeX file to Markdown using the API.
    """

    # Determine list of latex files.
    if isinstance(latexSource, Path):

        pass

    elif isinstance(latexSource, str):

        latexSource = Path(latexSource)

    else:

        raise ValueError(
            f'Parameter "latexSource" must be a str or a path to a .tex file.\nGiven latexSource: "{latexSource}"'
        )

    if latexSource.is_file() and latexSource.suffix == ".tex":

        pass

    elif latexSource.is_dir():

        raise ValueError(
            f'Parameter "latexSource" must be a .tex file, not a directory.\nGiven directory: "{latexSource}"'
        )

    elif latexSource.suffix != ".tex":

        raise ValueError(
            f'Parameter "latexSource" must have a .tex extension.\nGiven file: "{latexSource}"\nGiven Extension: "{latexSource.suffix}"'
        )

    if not latexSource.exists():

        raise FileNotFoundError(f'Given "latexSource" file "{latexSource}" not found.')

    if not isinstance(model, str):

        raise TypeError(
            f'Parameter "model" must be a string. Given type: "{type(model).__name__}"'
        )

    if model not in RATE_LIMITS:

        raise ValueError(
            f'Model "{model}" is not an available model.\nAvailable Models:\n{"\n".join(list(RATE_LIMITS.keys()))}'
        )

    rateLimit = RATE_LIMITS.get(model)

    if outputName is None:

        outputName = latexSource.stem

    # Ensure outputName has an extension
    if not Path(outputName).suffix:
        outputName += ".md"

    outputPath = Path(outputDir, outputName)

    apiKey = os.getenv("GEMINI_API_KEY")

    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    defaultDescription = "Converting LaTeX File to Markdown"

    currentLimiterMethod = limiterMethod

    # Kept to align with other functions, might remove
    if 1 < rateLimit:
        currentLimiterMethod = "fixedDelay"

    delayBetweenCalls = 60 / rateLimit
    runID = time.time()

    response = None

    if progress is None:

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )

    elif not isinstance(progress, Progress):

        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:

        task = progress.add_task(defaultDescription, total=1)

        specificDescription = f"Converting {latexSource.name}"

        progress.update(
            task,
            description=specificDescription,
            advance=0,
            refresh=True,
        )

        currentTime = time.time()

        if currentLimiterMethod == "fixedDelay":

            startTime = currentTime

            try:

                latexContent = latexSource.read_text()

                if latexContent == "":

                    response = ""

                else:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model=model,
                        contents=[
                            f"Instructions:\n{LATEX_TO_MARKDOWN_PROMPT}\n\nLaTeX:\n{latexContent}",
                        ],
                    )

            except Exception as e:

                console.print(
                    f"[bold red]Error during LaTeX to Markdown conversion of {latexSource.name}: {e}[/bold red]"
                )
                raise

            # Save responses as pickle in case of error.
            if fullResponseDir is None:

                fullResponseDir = outputDir

            localPickleDir = Path(fullResponseDir, "pickles")
            localPickleDir.mkdir(parents=True, exist_ok=True)

            try:

                picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                with picklePath.open("wb") as file:
                    pickle.dump(response, file)

            except Exception as e:

                console.print(f"[bold red]Failed to save response: {e}[/bold red]")
            elapsed = time.time() - startTime

            if elapsed < delayBetweenCalls:

                sleepTime = delayBetweenCalls - elapsed
                _SleepWithProgress(progress, task, sleepTime, specificDescription)

        elif currentLimiterMethod == "tracking":

            while (
                GLOBAL_REQUEST_TIMES
                and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW_SEC
            ):

                GLOBAL_REQUEST_TIMES.popleft()

            if len(GLOBAL_REQUEST_TIMES) >= rateLimit:

                sleepTime = RATE_LIMIT_WINDOW_SEC - (
                    currentTime - GLOBAL_REQUEST_TIMES[0]
                )
                _SleepWithProgress(progress, task, sleepTime, specificDescription)
                currentTime = time.time()

                while (
                    GLOBAL_REQUEST_TIMES
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW_SEC
                ):

                    GLOBAL_REQUEST_TIMES.popleft()

            try:

                latexContent = latexSource.read_text()

                if latexContent == "":

                    response = ""

                else:

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model=model,
                        contents=[
                            f"Instructions:\n{LATEX_TO_MARKDOWN_PROMPT}\n\nLaTeX:\n{latexContent}",
                        ],
                    )

            except Exception as e:

                console.print(
                    f"[bold red]Error during LaTeX to Markdown transcription of {latexSource.name}: {e}[/bold red]"
                )
                raise

            GLOBAL_REQUEST_TIMES.append(time.time())

            if fullResponseDir is None:

                fullResponseDir = outputDir

            localPickleDir = Path(fullResponseDir, "pickles")
            localPickleDir.mkdir(parents=True, exist_ok=True)

            try:

                picklePath = Path(localPickleDir, f"{outputName}-{runID}.pkl")

                with picklePath.open("wb") as file:

                    pickle.dump(response, file)

            except Exception as e:
                console.print(f"[bold red]Failed to save response: {e}[/bold red]")

        else:

            raise ValueError("Invalid limiterMethod. Use 'fixedDelay' or 'tracking'.")

        progress.update(task, advance=1, refresh=True)

        if bulkConversionTask is not None:
            progress.update(bulkConversionTask, advance=1, refresh=True)

        if response is None:

            raise ValueError(f"Response is {response}")

        responseText = response.text

        responseText = _RemoveCodeBlockSyntax(responseText)

        outputPath.write_text(responseText)

        progress.remove_task(task)


def LatexToMarkdown(
    source: Path | list[Path] | str | list[str],
    outputDir: Path = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
):

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        source_converted = []
        for item in source:
            if isinstance(item, str):
                source_converted.append(Path(item))
            elif isinstance(item, Path):
                source_converted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = source_converted

    # Determine the list of latex files and base input directory.
    if isinstance(source, Path):

        if not source.exists():

            console.print(
                f"[bold red]Path [bold yellow]{source}[/bold yellow] does not exist.[/bold red]"
            )

            raise FileNotFoundError(f"Path {source} does not exist.")

        if source.is_file():

            latexFiles = [source]
            inputDir = source.parent

        # source is a directory
        else:

            latexFiles = natsorted(list(source.glob(filePattern)))
            inputDir = source

    elif isinstance(source, list):

        latexFiles = source

        nonexistant = list()
        nonfiles = list()

        for entry in latexFiles:

            if not entry.exists():

                nonexistant.append(entry)

            elif not entry.is_file():

                nonfiles.append(entry)

        if len(nonexistant) > 0 and len(nonfiles) > 0:

            console.print(
                f"[bold red]Files do not exist: [bold yellow]{nonexistant}[/bold yellow]\nNot files: [bold yellow]{nonfiles}[/bold yellow][/bold red]"
            )

            raise FileNotFoundError(
                f"Files do not exist: {nonexistant}\nNot files: {nonfiles}"
            )

        elif len(nonexistant) > 0:

            console.print(
                f"[bold red]Files do not exist: [bold yellow]{nonexistant}[/bold yellow][/bold red]"
            )

            raise FileNotFoundError(f"Files do not exist: {nonexistant}")

        elif len(nonfiles) > 0:

            console.print(
                f"[bold red]Not files: [bold yellow]{nonfiles}[/bold yellow][/bold red]"
            )

            raise ValueError(f"Not files: {nonfiles}")

        inputDirs = [file.parent for file in latexFiles]
        inputDir = inputDirs[0]

    else:

        console.print(
            "[bold red]Parameter [bold yellow]'source'[/bold yellow] must be a [bold yellow]directory path[/bold yellow] or a [bold yellow]list of latex file paths[/bold yellow].[/bold red]"
        )

        raise ValueError(
            "Parameter 'source' must be a directory path or a list of latex file paths."
        )

    # Determine the output directory.
    if outputDir is None:
        bulkOutputDir = Path(inputDir, "converted-latex")
    else:
        bulkOutputDir = outputDir

    bulkOutputDir.mkdir(parents=True, exist_ok=True)

    totalLatexFiles = len(latexFiles)
    skippedLatexFiles = 0

    if totalLatexFiles > 1:

        defaultDescription = "Converting Latex Files to Markdown"

    else:

        defaultDescription = "Converting Latex File to Markdown"

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}", justify="left"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        expand=True,
        transient=True,
    ) as progress:

        task = progress.add_task(defaultDescription, total=totalLatexFiles)

        for latexPath in latexFiles:
            # Create a subdirectory for each latex output using its stem
            outputSubDir = bulkOutputDir / latexPath.stem
            outputSubDir.mkdir(parents=True, exist_ok=True)

            outputName = f"{latexPath.stem}"

            outputPath = Path(outputSubDir, f"{outputName}.md")

            if skipExisting and outputPath.exists():

                progress.update(
                    task,
                    description=f"Skipping {latexPath.name}",
                    advance=1,
                    refresh=True,
                )

                progress.update(
                    task,
                    description=defaultDescription,
                    advance=0,
                    refresh=True,
                )

                skippedLatexFiles += 1

                continue

            _LatexToMarkdown(
                latexSource=latexPath,
                limiterMethod="tracking",
                outputDir=outputSubDir,
                outputName=outputName,
                fullResponseDir=outputSubDir,
                progress=progress,
            )
            progress.update(task, advance=1)

    if skippedLatexFiles > 0:

        console.print(
            f"[bold green]Converted [bold yellow]{totalLatexFiles - skippedLatexFiles}[/bold yellow] latex files, Skipped [bold yellow]{skippedLatexFiles}[/bold yellow] latex files ([bold yellow]{totalLatexFiles}[/bold yellow] total)[/bold green]"
        )

    else:

        console.print(
            f"[bold green]Converted [bold yellow]{totalLatexFiles}[/bold yellow] LaTeX files to Markdown[/bold green]"
        )


def TranscribeSlides(
    source: Path | list[Path] | str | list[str],
    outputDir: Path = None,
    lectureNumPattern: str = r".*(\d+).*",
    excludeLectureNums: list[int] = [],
    skipExisting: bool = True,
):
    """
    Process and transcribe slide PDFs from a directory, a single PDF file, or a list of PDF file paths.
    This function converts string inputs to Path objects if possible.

    Parameters
    ----------
    source : Path or list[Path] or str or list[str]
        A directory containing PDF files, a single PDF file, or a list of PDF file paths.
    outputDir : Path, optional
        Directory where transcribed outputs will be stored. If not provided, a default directory is used.
    lectureNumPattern : str, optional
        Regular expression pattern used to extract the lecture number from the PDF file name.
    excludeLectureNums : list[int], optional
        A list of lecture numbers to exclude from processing.
    skipExisting : bool, optional
        If True, skip PDF files that already have transcribed output.

    Returns
    -------
    None
    """

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        source_converted = []
        for item in source:
            if isinstance(item, str):
                source_converted.append(Path(item))
            elif isinstance(item, Path):
                source_converted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = source_converted

    if isinstance(source, Path):
        if not source.exists():
            raise FileNotFoundError(f"Path {source} does not exist.")
        if source.is_file():
            slideFiles = [source]
            inputDir = source.parent
        else:  # source is a directory
            slideFiles = natsorted(list(source.glob("*.pdf")))
            inputDir = source
    elif isinstance(source, list):
        slideFiles = source
        if outputDir is None:
            inputDirs = [slideFile.parent for slideFile in slideFiles]
            if len(set(inputDirs)) > 1:
                inputDir = inputDirs[0]
                console.print(
                    f"Multiple input directories found: {set(inputDirs)}.\nUsing the first, {inputDir}, for the location of the output directory."
                )
            else:
                inputDir = inputDirs[0]
    else:
        raise ValueError(
            "Parameter 'source' must be a directory path, a single file, or a list of PDF file paths."
        )

    cleanedSlideFiles = []

    for slideFile in slideFiles:

        lectureNum = re.findall(lectureNumPattern, slideFile.name)

        if not lectureNum:

            console.print(f'Error extracting lecture number from "{slideFile.name}"')

            raise ValueError(f'Error extracting lecture number from "{slideFile.name}"')

        elif len(lectureNum) > 1:

            console.print(f'Multiple lecture numbers found in "{slideFile.name}"')

            raise ValueError(f'Multiple lecture numbers found in "{slideFile.name}"')

        try:

            lectureNum = int(lectureNum[0])

        except ValueError:

            console.print(
                f'Error extracting lecture number from "{slideFile.name}". Extracted: "{lectureNum}"'
            )

            raise

        if lectureNum not in excludeLectureNums:

            cleanedSlideFiles.append(slideFile)

    slideFiles = natsorted(cleanedSlideFiles)

    numSlideFiles = len(slideFiles)

    totalPages = _GetTotalPageCount(slideFiles)

    if outputDir is None:

        bulkOutputDir = Path(inputDir, "transcribed-slides")

    else:

        bulkOutputDir = outputDir

    bulkOutputDir.mkdir(parents=True, exist_ok=True)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}", justify="left"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        expand=True,
        transient=True,
    ) as progress:

        task = progress.add_task(f"Transcribing slide files", total=numSlideFiles)
        allPagesTask = progress.add_task(f"Transcribing slide pages", total=totalPages)

        for slideFile in slideFiles:

            progress.update(
                task, description=f"Transcribing {slideFile.name}", refresh=True
            )
            progress.update(
                allPagesTask,
                description=f"Transcribing slides from {slideFile.name}",
                refresh=True,
            )

            baseOutputDir = bulkOutputDir / slideFile.stem.replace(" ", "-")
            pagesDir = baseOutputDir / "page-images"
            fullResponseDir = baseOutputDir / "full-response"
            fullResponseDir.mkdir(parents=True, exist_ok=True)
            fullResponseDir.joinpath("pickles").mkdir(parents=True, exist_ok=True)
            pagesDir.mkdir(parents=True, exist_ok=True)

            outputName = f"{slideFile.stem}-transcribed"

            outputPath = Path(baseOutputDir, outputName)

            if skipExisting and outputPath.with_suffix(".tex").exists():

                progress.update(
                    task,
                    description=f"Skipping {slideFile.name}",
                    advance=1,
                    refresh=True,
                )

                continue

            PDFToPNG(pdfPath=slideFile, pagesDir=pagesDir, progress=progress)

            _TranscribeSlideImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=outputName,
                fullResponseDir=fullResponseDir,
                progress=progress,
                bulkPagesTask=allPagesTask,
            )

            progress.update(
                task,
                description=f"Transcribed {slideFile.name}",
                advance=1,
                refresh=True,
            )


def TranscribeLectures(
    source: Path | list[Path] | str | list[str],
    outputDir: Path = None,
    lectureNumPattern: str = r".*(\d+).*",
    excludeLectureNums: list[int] = [],
    skipExisting: bool = True,
):
    """
    Process and transcribe lecture PDFs from a directory, a single PDF file, or a list of PDF file paths.
    This function converts string inputs to Path objects if possible.

    Parameters
    ----------
    source : Path or list[Path] or str or list[str]
        A directory containing PDF files, a single PDF file, or a list of PDF file paths.
    outputDir : Path, optional
        Directory where transcribed outputs will be stored. If not provided, defaults to a subdirectory within the input directory.
    lectureNumPattern : str, optional
        Regular expression pattern used to extract the lecture number from the PDF file name.
    excludeLectureNums : list[int], optional
        A list of lecture numbers to exclude from processing.
    skipExisting : bool, optional
        If True, skip PDF files that already have transcribed output.

    Returns
    -------
    None
    """

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        source_converted = []
        for item in source:
            if isinstance(item, str):
                source_converted.append(Path(item))
            elif isinstance(item, Path):
                source_converted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = source_converted

    if isinstance(source, Path):
        if not source.exists():
            raise FileNotFoundError(f"Path {source} does not exist.")
        if source.is_file():
            lectureFiles = [source]
            inputDir = source.parent
        else:
            lectureFiles = natsorted(list(source.glob("*.pdf")))
            inputDir = source
    elif isinstance(source, list):
        lectureFiles = source
        if outputDir is None:
            inputDirs = [lectureFile.parent for lectureFile in lectureFiles]
            if len(set(inputDirs)) > 1:
                inputDir = inputDirs[0]
                console.print(
                    f"Multiple input directories found: {set(inputDirs)}.\nUsing the first, {inputDir}, for the location of the output directory."
                )
            else:
                inputDir = inputDirs[0]
    else:
        raise ValueError(
            "Parameter 'source' must be a directory path, a single file, or a list of PDF file paths."
        )

    cleanedLectureFiles = []

    for lectureFile in lectureFiles:

        lectureNum = re.findall(lectureNumPattern, lectureFile.name)

        if not lectureNum:

            console.print(f'Error extracting lecture number from "{lectureFile.name}"')

            raise ValueError(
                f'Error extracting lecture number from "{lectureFile.name}"'
            )

        elif len(lectureNum) > 1:

            console.print(f'Multiple lecture numbers found in "{lectureFile.name}"')

            raise ValueError(f'Multiple lecture numbers found in "{lectureFile.name}"')

        try:

            lectureNum = int(lectureNum[0])

        except ValueError:

            console.print(
                f'Error extracting lecture number from "{lectureFile.name}". Extracted: "{lectureNum}"'
            )

            raise

        if lectureNum not in excludeLectureNums:

            cleanedLectureFiles.append(lectureFile)

    lectureFiles = natsorted(cleanedLectureFiles)

    numLectureFiles = len(lectureFiles)

    totalPages = _GetTotalPageCount(lectureFiles)

    if outputDir is None:

        bulkOutputDir = Path(inputDir, "transcribed-lectures")

    else:

        bulkOutputDir = outputDir

    bulkOutputDir.mkdir(parents=True, exist_ok=True)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}", justify="left"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        expand=True,
        transient=True,
    ) as progress:

        task = progress.add_task("Transcribing lecture files", total=numLectureFiles)
        allPagesTask = progress.add_task("Transcribing lecture pages", total=totalPages)

        for lectureFile in lectureFiles:

            progress.update(
                task, description=f"Transcribing {lectureFile.name}", refresh=True
            )
            progress.update(
                allPagesTask,
                description=f"Transcribing pages from {lectureFile.name}",
                refresh=True,
            )

            baseOutputDir = bulkOutputDir / lectureFile.stem.replace(" ", "-")
            pagesDir = baseOutputDir / "page-images"
            fullResponseDir = baseOutputDir / "full-response"
            fullResponseDir.mkdir(parents=True, exist_ok=True)
            fullResponseDir.joinpath("pickles").mkdir(parents=True, exist_ok=True)
            pagesDir.mkdir(parents=True, exist_ok=True)

            outputName = f"{lectureFile.stem}-transcribed"

            outputPath = Path(baseOutputDir, outputName)

            if skipExisting and outputPath.with_suffix(".tex").exists():

                progress.update(
                    task,
                    description=f"Skipping {lectureFile.name}",
                    advance=1,
                    refresh=True,
                )

                continue

            PDFToPNG(pdfPath=lectureFile, pagesDir=pagesDir, progress=progress)

            _TranscribeLectureImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=outputName,
                fullResponseDir=fullResponseDir,
                progress=progress,
                bulkPagesTask=allPagesTask,
            )

            progress.update(
                task,
                description=f"Transcribed {lectureFile.name}",
                advance=1,
                refresh=True,
            )


def TranscribeDocuments(
    source: Path | list[Path] | str | list[str],
    outputDir: Path = None,
    skipExisting: bool = True,
):
    """
    Process and transcribe document PDFs from a directory, a single PDF file, or a list of PDF file paths.
    This function converts string inputs to Path objects if possible.
    For each PDF, create an output directory named after the PDF (with spaces replaced by hyphens)
    as a subdirectory of the specified output directory. If no outputDir is provided, defaults to a directory
    named "transcribed-documents" within the input directory.

    Parameters
    ----------
    source : Path or list[Path] or str or list[str]
        A directory containing PDF files, a single PDF file, or a list of PDF file paths.
    outputDir : Path, optional
        Parent directory where transcribed outputs will be stored.

    Returns
    -------
    None
    """

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        source_converted = []
        for item in source:
            if isinstance(item, str):
                source_converted.append(Path(item))
            elif isinstance(item, Path):
                source_converted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = source_converted

    if isinstance(source, Path):
        if not source.exists():
            raise FileNotFoundError(f"Path {source} does not exist.")
        if source.is_file():
            pdfFiles = [source]
            inputDir = source.parent
        else:
            pdfFiles = natsorted(list(source.glob("*.pdf")))
            inputDir = source
    elif isinstance(source, list):
        pdfFiles = source
        if outputDir is None:
            inputDirs = [pdfFile.parent for pdfFile in pdfFiles]
            if len(set(inputDirs)) > 1:
                inputDir = inputDirs[0]
                console.print(
                    f"Multiple input directories found: {inputDirs}.\nUsing the first one, {inputDir}, for the location of the output directory."
                )
            else:
                inputDir = inputDirs[0]
    else:
        raise ValueError(
            "Parameter 'source' must be a directory path, a single file, or a list of PDF file paths."
        )

    if outputDir is None:
        parentOutputDir = Path(inputDir, "transcribed-documents")
    else:
        parentOutputDir = outputDir

    parentOutputDir.mkdir(parents=True, exist_ok=True)
    totalPages = _GetTotalPageCount(pdfFiles)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}", justify="left"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        expand=True,
        transient=True,
    ) as progress:

        task = progress.add_task("Transcribing document files", total=len(pdfFiles))
        allPagesTask = progress.add_task(
            "Transcribing document pages", total=totalPages
        )

        for pdfFile in pdfFiles:

            progress.update(
                task, description=f"Transcribing {pdfFile.name}", refresh=True
            )
            progress.update(
                allPagesTask,
                description=f"Transcribing pages from {pdfFile.name}",
                refresh=True,
            )

            baseOutputDir = parentOutputDir / pdfFile.stem.replace(" ", "-")
            pagesDir = baseOutputDir / "page-images"
            fullResponseDir = baseOutputDir / "full-response"
            fullResponseDir.mkdir(parents=True, exist_ok=True)
            fullResponseDir.joinpath("pickles").mkdir(parents=True, exist_ok=True)
            pagesDir.mkdir(parents=True, exist_ok=True)

            outputName = f"{pdfFile.stem}-transcribed"

            outputPath = Path(baseOutputDir, outputName)

            if skipExisting and outputPath.with_suffix(".tex").exists():

                progress.update(
                    task,
                    description=f"Skipping {pdfFile.name}",
                    advance=1,
                    refresh=True,
                )

                continue

            PDFToPNG(pdfPath=pdfFile, pagesDir=pagesDir, progress=progress)

            _TranscribeDocumentImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=outputName,
                fullResponseDir=fullResponseDir,
                progress=progress,
                bulkPagesTask=allPagesTask,
            )

            progress.update(
                task, description=f"Transcribed {pdfFile.name}", advance=1, refresh=True
            )


def TranscribeImages(
    source: Path | list[Path] | str | list[str],
    outputDir: Path = None,
    filePattern: str = "*.png",
    separateOutputs: bool = True,
    skipExisting: bool = True,
):
    """
    Process and transcribe image files from a directory, a single image file, or a list of image file paths.
    This function converts string inputs to Path objects if possible.

    Parameters
    ----------
    source : Path or list[Path] or str or list[str]
        A directory, a single image file, or a list of image file paths.
    outputDir : Path, optional
        Parent directory where transcribed outputs will be stored.
        If not provided, defaults to a 'transcribed-images' subdirectory within the input directory.
    filePattern : str, optional
        Glob pattern for image files. Defaults to "*.png".
    separateOutputs : bool, optional
        If True, process each image file separately in its own subdirectory; otherwise, process them in bulk.
    skipExisting : bool, optional
        If True, skip image files that already have transcribed output.

    Returns
    -------
    None
    """

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        source_converted = []
        for item in source:
            if isinstance(item, str):
                source_converted.append(Path(item))
            elif isinstance(item, Path):
                source_converted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = source_converted

    # Determine the list of image files and base input directory.
    if isinstance(source, Path):

        if not source.exists():

            console.print(
                f"[bold red]Path [bold yellow]{source}[/bold yellow] does not exist.[/bold red]"
            )

            raise FileNotFoundError(f"Path {source} does not exist.")

        if source.is_file():

            imageFiles = [source]
            inputDir = source.parent

        else:  # source is a directory

            imageFiles = natsorted(list(source.glob(filePattern)))
            inputDir = source

    elif isinstance(source, list):

        imageFiles = source

        nonexistant = list()
        nonfiles = list()

        for entry in imageFiles:

            if not entry.exists():

                nonexistant.append(entry)

            elif not entry.is_file():

                nonfiles.append(entry)

        if len(nonexistant) > 0 and len(nonfiles) > 0:

            console.print(
                f"[bold red]Files do not exist: [bold yellow]{nonexistant}[/bold yellow]\nNot files: [bold yellow]{nonfiles}[/bold yellow][/bold red]"
            )

            raise FileNotFoundError(
                f"Files do not exist: {nonexistant}\nNot files: {nonfiles}"
            )

        elif len(nonexistant) > 0:

            console.print(
                f"[bold red]Files do not exist: [bold yellow]{nonexistant}[/bold yellow][/bold red]"
            )

            raise FileNotFoundError(f"Files do not exist: {nonexistant}")

        elif len(nonfiles) > 0:

            console.print(
                f"[bold red]Not files: [bold yellow]{nonfiles}[/bold yellow][/bold red]"
            )

            raise ValueError(f"Not files: {nonfiles}")

        inputDirs = [img.parent for img in imageFiles]
        inputDir = inputDirs[0]

    else:

        console.print(
            "[bold red]Parameter [bold yellow]'source'[/bold yellow] must be a [bold yellow]directory path[/bold yellow] or a [bold yellow]list of image file paths[/bold yellow].[/bold red]"
        )

        raise ValueError(
            "Parameter 'source' must be a directory path or a list of image file paths."
        )

    # Determine the output directory.
    if outputDir is None:
        bulkOutputDir = Path(inputDir, "transcribed-images")
    else:
        bulkOutputDir = outputDir

    bulkOutputDir.mkdir(parents=True, exist_ok=True)

    defaultDescription = "Transcribing Individual Image Files"

    totalImageFiles = len(imageFiles)
    skippedImageFiles = 0

    if separateOutputs:
        # Process each image file separately, each in its own subdirectory
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
            transient=True,
        ) as progress:

            task = progress.add_task(defaultDescription, total=totalImageFiles)

            for imgPath in imageFiles:
                # Create a subdirectory for each image output using its stem
                outputSubDir = bulkOutputDir / imgPath.stem
                outputSubDir.mkdir(parents=True, exist_ok=True)

                outputName = f"{imgPath.stem}-transcribed"

                outputPath = Path(outputSubDir, outputName)

                if skipExisting and outputPath.with_suffix(".tex").exists():

                    progress.update(
                        task,
                        description=f"Skipping {imgPath.name}",
                        advance=1,
                        refresh=True,
                    )

                    progress.update(
                        task,
                        description=defaultDescription,
                        advance=0,
                        refresh=True,
                    )

                    skippedImageFiles += 1

                    continue

                _TranscribeImage(
                    imageSource=[imgPath],
                    limiterMethod="tracking",
                    outputDir=outputSubDir,
                    outputName=outputName,
                    fullResponseDir=outputSubDir,
                    progress=progress,
                )
                progress.update(task, advance=1)

    else:

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        ) as progress:

            task = progress.add_task(
                "Bulk transcribing image files", total=totalImageFiles
            )
            allPagesTask = progress.add_task(
                "Transcribing image pages", total=totalImageFiles
            )
            _TranscribeImage(
                imageSource=imageFiles,
                limiterMethod="tracking",
                outputDir=bulkOutputDir,
                outputName="bulk-transcribed",
                fullResponseDir=bulkOutputDir,
                progress=progress,
                bulkPagesTask=allPagesTask,
            )
            progress.update(task, advance=totalImageFiles)

    if skippedImageFiles > 0:

        console.print(
            f"[bold green]Transcribed [bold yellow]{totalImageFiles - skippedImageFiles}[/bold yellow] image files, Skipped [bold yellow]{skippedImageFiles}[/bold yellow] image files ([bold yellow]{totalImageFiles}[/bold yellow] total)[/bold green]"
        )

    else:

        console.print(
            f"[bold green]Transcribed [bold yellow]{totalImageFiles}[/bold yellow] image files[/bold green]"
        )


def FinishPickleSlides(
    picklePath: Path, fullResponseDir: Path, outputDir: Path, outputName: str
):

    with picklePath.open("rb") as file:

        responses = pickle.load(file)

    combinedResponse = ""

    for slideNum, (imageName, response) in enumerate(responses.items()):

        responseText: str | list[str] | None = response.text

        if responseText is None:

            combinedResponse += f"\n\\begin{{frame}}\n\\frametitle{{Slide {slideNum}: {imageName}}}\n\nError: Slide text content is None\n\n\\end{{frame}}"
            continue

        if isinstance(responseText, str):
            responseText = responseText.splitlines()

        if responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText[-1].strip() == "```":
            responseText = responseText[:-1]

        combinedResponse += "\n".join(responseText) + "\n"

    Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = _CleanResponse(
        combinedResponse=combinedResponse, preamble=SLIDE_LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


def FinishPickleLecture(
    picklePath: Path, fullResponseDir: Path, outputDir: Path, outputName: str
):

    with picklePath.open("rb") as file:

        responses = pickle.load(file)

    combinedResponse = ""

    for pageNum, (imageName, response) in enumerate(responses.items()):

        responseText: str | list[str] | None = response.text

        if responseText is None:

            combinedResponse += f"\n\\section{{Page {pageNum}: {imageName}}}\n\nError: Text content is None"
            continue

        if isinstance(responseText, str):
            responseText = responseText.splitlines()
        if responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText[-1].strip() == "```":
            responseText = responseText[:-1]

        combinedResponse += "\n".join(responseText) + "\n"

    Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = _CleanResponse(
        combinedResponse=combinedResponse, preamble=LECTURE_LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


def FinishPickleDocument(
    picklePath: Path, fullResponseDir: Path, outputDir: Path, outputName: str
):

    with picklePath.open("rb") as file:

        responses = pickle.load(file)

    combinedResponse = ""

    for pageNum, (imageName, response) in enumerate(responses.items()):

        responseText: str | list[str] | None = response.text

        if responseText is None:
            combinedResponse += f"\n\\section{{Page {pageNum}: {imageName}}}\n\nError: Text content is None"
            continue

        if isinstance(responseText, str):
            responseText = responseText.splitlines()

        if responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText[-1].strip() == "```":
            responseText = responseText[:-1]

        combinedResponse += "\n".join(responseText) + "\n"

    Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = _CleanResponse(
        combinedResponse=combinedResponse, preamble=DOCUMENT_LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


def FinishPickleImage(
    picklePath: Path,
    fullResponseDir: Path,
    outputDir: Path,
    outputName: str,
):
    """
    Process a pickle file containing image transcription responses, combine them into a single text string,
    clean the LaTeX output using the given preamble, and write both a text file and a cleaned .tex file.

    Parameters
    ----------
    picklePath : Path
        The path to the pickle file containing responses.
    fullResponseDir : Path
        Directory where the combined text output (.txt) will be stored.
    outputDir : Path
        Directory where the cleaned LaTeX output (.tex) will be stored.
    outputName : str
        Base name for the output files.

    Returns
    -------
    None
    """
    with picklePath.open("rb") as file:
        responses = pickle.load(file)

    combinedResponse = ""
    for idx, (imageName, response) in enumerate(responses.items()):
        responseText: str | list[str] | None = response.text
        if responseText is None:
            combinedResponse += f"\n\\section{{Image {idx}: {imageName}}}\n\nError: Text content is None\n"
            continue
        if isinstance(responseText, str):
            responseText = responseText.splitlines()
        if responseText and responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText and responseText[-1].strip() == "```":
            responseText = responseText[:-1]
        combinedResponse += "\n".join(responseText) + "\n"

    Path(fullResponseDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = _CleanResponse(
        combinedResponse=combinedResponse, preamble=IMAGE_LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


if __name__ == "__main__":

    math465SlidesPath = Path(
        "/Users/kadengruizenga/Documents/School/W25/Math465/Slides"
    )

    TranscribeSlides(math465SlidesPath)

    pass
