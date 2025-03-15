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

console = Console()

# Global deque to track request times across instances
GLOBAL_REQUEST_TIMES = deque()

# Define rate limit
RATE_LIMIT_PER_MINUTE = 15
RATE_LIMIT_WINDOW = 60

OUTPUT_DIR = Path("output")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load latex preambles
SLIDE_LATEX_PREAMBLE_PATH = Path("utils", "slide-template.txt")

if not SLIDE_LATEX_PREAMBLE_PATH.exists():

    raise FileNotFoundError(
        f"Slides latex preamble file {SLIDE_LATEX_PREAMBLE_PATH} not found"
    )

SLIDE_LATEX_PREAMBLE = SLIDE_LATEX_PREAMBLE_PATH.read_text()

LECTURE_LATEX_PREAMBLE_PATH = Path("utils", "lecture-template.txt")

if not LECTURE_LATEX_PREAMBLE_PATH.exists():

    raise FileNotFoundError(
        f"Lecture latex preamble file {LECTURE_LATEX_PREAMBLE_PATH} not found"
    )

LECTURE_LATEX_PREAMBLE = LECTURE_LATEX_PREAMBLE_PATH.read_text()

DOCUMENT_LATEX_PREAMBLE_PATH = Path("utils", "document-template.txt")

if not DOCUMENT_LATEX_PREAMBLE_PATH.exists():

    raise FileNotFoundError(
        f"Document latex preamble file {DOCUMENT_LATEX_PREAMBLE_PATH} not found"
    )

DOCUMENT_LATEX_PREAMBLE = DOCUMENT_LATEX_PREAMBLE_PATH.read_text()

IMAGE_LATEX_PREAMBLE_PATH = Path("utils", "image-template.txt")

if not IMAGE_LATEX_PREAMBLE_PATH.exists():

    raise FileNotFoundError(
        f"Image latex preamble file {IMAGE_LATEX_PREAMBLE_PATH} not found"
    )

IMAGE_LATEX_PREAMBLE = IMAGE_LATEX_PREAMBLE_PATH.read_text()

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
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
                ):
                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )

                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
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
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
                ):
                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )

                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
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
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
                ):
                    GLOBAL_REQUEST_TIMES.popleft()

                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                    sleepTime = RATE_LIMIT_WINDOW - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )

                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
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
    defaultDescription = "Transcribing Image"

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
                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
            elif currentLimiterMethod == "tracking":
                while (
                    GLOBAL_REQUEST_TIMES
                    and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
                ):
                    GLOBAL_REQUEST_TIMES.popleft()
                if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:
                    sleepTime = RATE_LIMIT_WINDOW - (
                        currentTime - GLOBAL_REQUEST_TIMES[0]
                    )
                    _SleepWithProgress(progress, task, sleepTime, defaultDescription)
                    currentTime = time.time()
                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
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
            progress.update(task, advance=1)
            if bulkPagesTask is not None:
                progress.update(bulkPagesTask, advance=1)

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


def TranscribeSlides(
    source: Path | list[Path],
    outputDir: Path = None,
    lectureNumPattern: str = r".*(\d+).*",
    excludeLectureNums: list[int] = [],
):
    """
    Process and transcribe slide PDFs from a directory or a list of PDF file paths.

    Parameters
    ----------
    source : Path or list[Path]
        A directory containing PDF files or a list of PDF file paths.
    outputDir : Path, optional
        Directory where transcribed outputs will be stored. If not provided, a default directory is used.
    lectureNumPattern : str, optional
        Regular expression pattern used to extract the lecture number from the PDF file name.
    excludeLectureNums : list[int], optional
        A list of lecture numbers to exclude from processing.

    Returns
    -------
    None
    """

    # Determine the list of PDF files from either a directory or an explicit list.
    if isinstance(source, Path) and source.is_dir():

        slideFiles = natsorted(list(source.glob("*.pdf")))

        if outputDir is None:

            inputDir = source

    elif isinstance(source, list):

        slideFiles = source

        if outputDir is None:

            inputDirs = []

            for slideFile in slideFiles:

                inputDirs.append(slideFile.parent)

            if len(set(inputDirs)) > 1:

                inputDir = inputDirs[0]

                console.print(
                    f"Multiple input directories found: {set(inputDirs)}.\nUsing the first, {inputDir}, for the location of the ouput directory."
                )

            else:

                inputDir = inputDirs[0]

    else:
        raise ValueError(
            "Parameter 'source' must be a directory path or a list of PDF file paths."
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

            PDFToPNG(pdfPath=slideFile, pagesDir=pagesDir, progress=progress)

            _TranscribeSlideImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=f"{slideFile.stem}-transcribed",
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
    source: Path | list[Path],
    outputDir: Path = None,
    lectureNumPattern: str = r".*(\d+).*",
    excludeLectureNums: list[int] = [],
):
    """
    Process and transcribe lecture PDFs from a directory or a list of PDF file paths.

    Parameters
    ----------
    source : Path or list[Path]
        A directory containing PDF files or a list of PDF file paths.
    outputDir : Path, optional
        Directory where transcribed outputs will be stored. If not provided, defaults to a subdirectory within the input directory.
    lectureNumPattern : str, optional
        Regular expression pattern used to extract the lecture number from the PDF file name.
    excludeLectureNums : list[int], optional
        A list of lecture numbers to exclude from processing.

    Returns
    -------
    None
    """

    # Determine the list of PDF files from either a directory or an explicit list.
    if isinstance(source, Path) and source.is_dir():

        lectureFiles = natsorted(list(source.glob("*.pdf")))

        if outputDir is None:

            inputDir = source

    elif isinstance(source, list):

        lectureFiles = source

        if outputDir is None:

            inputDirs = []

            for lectureFile in lectureFiles:

                inputDirs.append(lectureFile.parent)

            if len(set(inputDirs)) > 1:

                inputDir = inputDirs[0]

                console.print(
                    f"Multiple input directories found: {set(inputDirs)}.\nUsing the first, {inputDir}, for the location of the ouput directory."
                )

            else:

                inputDir = inputDirs[0]

    else:

        raise ValueError(
            "Parameter 'source' must be a directory path or a list of PDF file paths."
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

            PDFToPNG(pdfPath=lectureFile, pagesDir=pagesDir, progress=progress)

            _TranscribeSlideImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=f"{lectureFile.stem}-transcribed",
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


def TranscribeDocuments(source: Path | list[Path], outputDir: Path = None):
    """
    Process and transcribe document PDFs from a directory or a list of PDF file paths.
    For each PDF, create an output directory named after the PDF (with spaces replaced by hyphens)
    as a subdirectory of the specified output directory. If no outputDir is provided, defaults to a directory
    named "transcribed-documents" within the input directory.

    Parameters
    ----------
    source : Path or list[Path]
        A directory containing PDF files or a list of PDF file paths.
    outputDir : Path, optional
        Parent directory where transcribed outputs will be stored.

    Returns
    -------
    None
    """

    if isinstance(source, Path) and source.is_dir():

        pdfFiles = natsorted(list(source.glob("*.pdf")))

        if outputDir is None:

            inputDir = source

    elif isinstance(source, list):

        pdfFiles = source

        if outputDir is None:

            inputDirs = []

            for pdfFile in pdfFiles:

                inputDirs.append(pdfFile.parent)

            if len(set(inputDirs)) > 1:

                inputDir = inputDirs[0]

                console.print(
                    f"Multiple input directories found: {inputDirs}.\nUsing the first one, {inputDir}, for the location of the output directory."
                )

            else:

                inputDir = inputDirs[0]

    else:
        raise ValueError(
            "Parameter 'source' must be a directory path or a list of PDF file paths."
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

            PDFToPNG(pdfPath=pdfFile, pagesDir=pagesDir, progress=progress)

            _TranscribeDocumentImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=f"{pdfFile.stem}-transcribed",
                fullResponseDir=fullResponseDir,
                progress=progress,
                bulkPagesTask=allPagesTask,
            )

            progress.update(
                task, description=f"Transcribed {pdfFile.name}", advance=1, refresh=True
            )


def TranscribeImages(
    source: Path | list[Path],
    outputDir: Path = None,
    filePattern: str = "*.png",
    separateOutputs: bool = True,
):
    """
    Process and transcribe image files from a directory or a list of image file paths.
    This function collects the image files and then calls _TranscribeImage.

    Parameters
    ----------
    source : Path or list[Path]
        A directory containing image files or a list of image file paths.
    outputDir : Path, optional
        Parent directory where transcribed outputs will be stored.
        If not provided, defaults to a 'transcribed-images' subdirectory within the input directory.
    filePattern : str, optional
        Glob pattern for image files. Defaults to "*.png".

    Returns
    -------
    None
    """
    # Determine the list of image files and base input directory.
    if isinstance(source, Path):

        if not source.exists():

            raise FileNotFoundError(f"Directory {source} does not exist.")

        elif not source.is_dir():

            raise NotADirectoryError(f"{source} is not a directory.")

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

            raise FileNotFoundError(
                f"Files do not exist: {nonexistant}\nNot files: {nonfiles}"
            )

        elif len(nonexistant) > 0:

            raise FileNotFoundError(f"Files do not exist: {nonexistant}")

        elif len(nonfiles) > 0:

            raise ValueError(f"Not files: {nonfiles}")

        inputDirs = [img.parent for img in imageFiles]
        inputDir = inputDirs[0]
    else:
        raise ValueError(
            "Parameter 'source' must be a directory path or a list of image file paths."
        )

    # Determine the output directory.
    if outputDir is None:
        bulkOutputDir = Path(inputDir, "transcribed-images")
    else:
        bulkOutputDir = outputDir

    bulkOutputDir.mkdir(parents=True, exist_ok=True)

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
        ) as progress:
            task = progress.add_task(
                "Transcribing individual image files", total=len(imageFiles)
            )
            for imgPath in imageFiles:
                # Create a subdirectory for each image output using its stem
                outputSubDir = bulkOutputDir / imgPath.stem
                outputSubDir.mkdir(parents=True, exist_ok=True)
                _TranscribeImage(
                    imageSource=[imgPath],
                    limiterMethod="tracking",
                    outputDir=outputSubDir,
                    outputName=imgPath.stem + "-transcribed",
                    fullResponseDir=outputSubDir,
                    progress=progress,
                )
                progress.update(task, advance=1)
    else:
        # Combined output: process all images together
        numImageFiles = len(imageFiles)
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
                "Bulk transcribing image files", total=numImageFiles
            )
            allPagesTask = progress.add_task(
                "Transcribing image pages", total=numImageFiles
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
            progress.update(task, advance=numImageFiles)


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

    a = Path(
        "/Users/kadengruizenga/Developer/Projects/Export-Apple-Reminders/eventkit-docs/screenshots/objective-c"
    )

    TranscribeImages(a)

    pass
