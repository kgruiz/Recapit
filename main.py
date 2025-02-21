import os
import pickle
import re
import shutil
import time
from collections import deque
from pathlib import Path

import PIL.Image
from google import genai
from google.genai import types
from pdf2image import convert_from_path
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
# TODO: Add other slide formats

console = Console()

# Global deque to track request times across instances
GLOBAL_REQUEST_TIMES = deque()

# Define rate limit constants
RATE_LIMIT_PER_MINUTE = 15
RATE_LIMIT_WINDOW = 60

INPUT_DIR = Path("input")

if not INPUT_DIR.exists():

    raise FileNotFoundError(f"Input Directory {INPUT_DIR} not found")

OUTPUT_DIR = Path("output")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PICKLE_DIR = Path(OUTPUT_DIR, "response-pickles")

PICKLE_DIR.mkdir(parents=True, exist_ok=True)

LATEX_PREAMBLE_PATH = Path("utils", "slide-template.txt")

if not LATEX_PREAMBLE_PATH.exists():

    raise FileNotFoundError(
        f"Slides latex preamble file {LATEX_PREAMBLE_PATH} not found"
    )

LATEX_PREAMBLE = LATEX_PREAMBLE_PATH.read_text()

LECTURE_LATEX_PREAMBLE_PATH = Path("utils", "lecture-template.txt")

if not LATEX_PREAMBLE_PATH.exists():

    raise FileNotFoundError(
        f"Lecture latex preamble file {LECTURE_LATEX_PREAMBLE_PATH} not found"
    )

LECTURE_LATEX_PREAMBLE = LECTURE_LATEX_PREAMBLE_PATH.read_text()


def PDFToPNG(pdfPath: Path, pagesDir: Path = None, progress=None):
    """
    Converts a PDF file to PNG images, saving them in the specified directory.

    Parameters
    ----------
    pdfPath : Path, optional
        The path to the PDF file.
    pagesDir : Path, optional
        The directory where the PNG images will be saved.
        Defaults to OUTPUT_DIR / f"{pdfPath.stem}-pages" if not provided.
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


def SleepWithProgress(progress, task, sleepTime, defaultDescription):
    """
    Sleeps for sleepTime seconds while updating the progress bar description.

    Parameters
    ----------
    progress : Progress
        The progress object.
    task : TaskID
        The task to update.
    sleepTime : float
        The total sleep time in seconds.
    defaultDescription : str
        The description to revert to after sleeping.
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


def CleanResponse(
    combinedResponse: str,
    preamble: str,
    title: str | None = "",
    author: str | None = "",
    date: str | None = "",
) -> str:

    preambleLines = preamble.splitlines()

    for line in preambleLines:

        combinedResponse = re.sub(
            rf"^{re.escape(line)}$", "", combinedResponse, flags=re.MULTILINE
        )

    END_DOCUMENT_LINE = r"\end{document}"

    combinedResponse = re.sub(
        rf"^{re.escape(END_DOCUMENT_LINE)}$", "", combinedResponse, flags=re.MULTILINE
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


def TranscribeSlideImages(
    imageDir: Path,
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str = "response",
    progress=None,
):
    """
    Processes images and queries an API, optionally rate-limiting the calls if the number
    of images exceeds the defined rate limit. Rate limiting is tracked globally across instances.

    Parameters
    ----------
    imageDir : Path
        Path to the directory containing the input images.
    limiterMethod : str, optional
        The rate limiting method to use when len(images) >= RATE_LIMIT_PER_MINUTE.
        Supported values:
            - "fixedDelay": Sleeps for a fixed delay between each call.
            - "tracking": Tracks request timestamps and sleeps only if needed.
        Defaults to "tracking".
    outputDir : Path, optional
        Path to the directory where outputs will be stored.
        Defaults to OUTPUT_DIR.
    outputName : str, optional
        Base name for the output files.
        Defaults to "response".
    progress : Progress, optional
        A rich Progress instance to update the UI.
    """

    global GLOBAL_REQUEST_TIMES

    # Use the provided imageDir for image directory.
    IMAGE_DIR = Path(imageDir)
    images = [PIL.Image.open(imagePath) for imagePath in IMAGE_DIR.glob("*.png")]

    apiKey = os.getenv("GEMINI_API_KEY")
    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = []
    defaultDescription = "Querying images"

    useRateLimit = len(images) >= RATE_LIMIT_PER_MINUTE
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE

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

        task = progress.add_task(defaultDescription, total=len(images))

        for image in images:
            currentTime = time.time()

            if useRateLimit:
                if limiterMethod == "fixedDelay":
                    startTime = currentTime
                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, "
                                f"ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', "
                                f"etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. "
                                f"If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and "
                                f"describe the contents.\n\nLatex Preamble:{LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )
                    responses.append(response)
                    elapsed = time.time() - startTime

                    if elapsed < delayBetweenCalls:
                        sleepTime = delayBetweenCalls - elapsed
                        SleepWithProgress(progress, task, sleepTime, defaultDescription)

                elif limiterMethod == "tracking":
                    # Remove timestamps that are outside the current window
                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
                    ):
                        GLOBAL_REQUEST_TIMES.popleft()

                    if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:
                        sleepTime = RATE_LIMIT_WINDOW - (
                            currentTime - GLOBAL_REQUEST_TIMES[0]
                        )
                        SleepWithProgress(progress, task, sleepTime, defaultDescription)
                        currentTime = time.time()
                        while (
                            GLOBAL_REQUEST_TIMES
                            and currentTime - GLOBAL_REQUEST_TIMES[0]
                            >= RATE_LIMIT_WINDOW
                        ):
                            GLOBAL_REQUEST_TIMES.popleft()

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            (
                                f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, "
                                f"ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', "
                                f"etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. "
                                f"If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and "
                                f"describe the contents.\n\nLatex Preamble:{LATEX_PREAMBLE}"
                            ),
                            image,
                        ],
                    )
                    responses.append(response)
                    GLOBAL_REQUEST_TIMES.append(time.time())
                else:
                    raise ValueError(
                        "Invalid limiterMethod. Use 'fixedDelay' or 'tracking'."
                    )
            else:
                client = genai.Client(api_key=apiKey)
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        (
                            f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, "
                            f"ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', "
                            f"etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. "
                            f"If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and "
                            f"describe the contents.\n\nLatex Preamble:{LATEX_PREAMBLE}"
                        ),
                        image,
                    ],
                )
                responses.append(response)

            progress.update(task, advance=1)

    # Save responses as pickle
    localPickleDir = Path(outputDir, f"{outputName}-pickles")
    localPickleDir.mkdir(parents=True, exist_ok=True)

    try:

        picklePath = Path(localPickleDir, f"{outputName}.pkl")
        if picklePath.exists():
            uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
            picklePath = picklePath.with_name(uniquePath)
            if picklePath.exists():
                raise FileExistsError(
                    f"File {picklePath} already exists. Attempt to create unique file failed."
                )
        with picklePath.open("wb") as file:
            pickle.dump(responses, file)
    except Exception as e:
        console.print(f"{e}\n\n\n[bold red]Failed to save responses[/bold red]")

    combinedResponse = ""
    for i, response in enumerate(responses):

        responseText: str | list[str] | None = response.text

        if responseText is None:

            combinedResponse += f"\n\\begin{{frame}}\n\\frametitle{{Slide {i}}}\n\nError: Slide text content is None\n\n\\end{{frame}}"
            continue

        if isinstance(responseText, str):
            responseText = responseText.splitlines()

        if responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText[-1].strip() == "```":
            responseText = responseText[:-1]
        combinedResponse += "\n".join(responseText) + "\n"

    Path(outputDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = CleanResponse(
        combinedResponse=combinedResponse, preamble=LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


def TranscribeLectureImages(
    imageDir: Path,
    limiterMethod: str = "tracking",
    outputDir: Path = OUTPUT_DIR,
    outputName: str = "response",
    progress=None,
):
    """
    Processes lecture images and queries an API with global rate limiting.

    Parameters
    ----------
    imageDir : Path
        Path to the directory containing the input images.
    limiterMethod : str, optional
        The rate limiting method to use when len(images) >= RATE_LIMIT_PER_MINUTE.
        Supported values:
            - "fixedDelay": Sleeps for a fixed delay between each call.
            - "tracking": Tracks request timestamps and sleeps only if needed.
        Defaults to "tracking".
    outputDir : Path, optional
        Directory to store outputs.
        Defaults to OUTPUT_DIR.
    outputName : str, optional
        Base name for output files.
        Defaults to "response".
    progress : Progress, optional
        A rich Progress instance to update the UI.
    """

    global GLOBAL_REQUEST_TIMES

    IMAGE_DIR = Path(imageDir)
    images = [PIL.Image.open(imagePath) for imagePath in IMAGE_DIR.glob("*.png")]

    apiKey = os.getenv("GEMINI_API_KEY")
    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = []
    defaultDescription = "Querying images"

    useRateLimit = len(images) >= RATE_LIMIT_PER_MINUTE
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE

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

        task = progress.add_task(defaultDescription, total=len(images))

        for image in images:

            currentTime = time.time()

            if useRateLimit:

                if limiterMethod == "fixedDelay":

                    startTime = currentTime
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
                    responses.append(response)
                    elapsed = time.time() - startTime

                    if elapsed < delayBetweenCalls:

                        sleepTime = delayBetweenCalls - elapsed
                        SleepWithProgress(progress, task, sleepTime, defaultDescription)

                elif limiterMethod == "tracking":

                    while (
                        GLOBAL_REQUEST_TIMES
                        and currentTime - GLOBAL_REQUEST_TIMES[0] >= RATE_LIMIT_WINDOW
                    ):
                        GLOBAL_REQUEST_TIMES.popleft()

                    if len(GLOBAL_REQUEST_TIMES) >= RATE_LIMIT_PER_MINUTE:

                        sleepTime = RATE_LIMIT_WINDOW - (
                            currentTime - GLOBAL_REQUEST_TIMES[0]
                        )

                        SleepWithProgress(progress, task, sleepTime, defaultDescription)
                        currentTime = time.time()

                        while (
                            GLOBAL_REQUEST_TIMES
                            and currentTime - GLOBAL_REQUEST_TIMES[0]
                            >= RATE_LIMIT_WINDOW
                        ):
                            GLOBAL_REQUEST_TIMES.popleft()
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
                    responses.append(response)
                    GLOBAL_REQUEST_TIMES.append(time.time())

                else:

                    raise ValueError(
                        "Invalid limiterMethod. Use 'fixedDelay' or 'tracking'."
                    )
            else:

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
                responses.append(response)
            progress.update(task, advance=1)

    localPickleDir = Path(outputDir, f"{outputName}-pickles")
    localPickleDir.mkdir(parents=True, exist_ok=True)

    try:

        picklePath = Path(localPickleDir, f"{outputName}.pkl")

        if picklePath.exists():

            uniquePath = picklePath.stem + f"-{int(time.time())}.pkl"
            picklePath = picklePath.with_name(uniquePath)

            if picklePath.exists():

                raise FileExistsError(
                    f"File {picklePath} already exists. Unique file creation failed."
                )

        with picklePath.open("wb") as file:
            pickle.dump(responses, file)

    except Exception as e:

        console.print(f"{e}\n\n\n[bold red]Failed to save responses[/bold red]")

    combinedResponse = ""

    for i, response in enumerate(responses):

        responseText: str | list[str] | None = response.text

        if responseText is None:

            combinedResponse += (
                f"\n\\section{{Page {i}}}\n\nError: Text content is None"
            )
            continue

        if isinstance(responseText, str):
            responseText = responseText.splitlines()
        if responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText[-1].strip() == "```":
            responseText = responseText[:-1]
        combinedResponse += "\n".join(responseText) + "\n"

    Path(outputDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = CleanResponse(
        combinedResponse=combinedResponse, preamble=LECTURE_LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


def BulkSlideTranscribe(excludeSlideNums: list[int] = []):

    SLIDES_DIR = Path("/Users/kadengruizenga/Documents/School/W25/Math465/Slides")

    slideFiles = list(SLIDES_DIR.glob("*.pdf"))

    cleanedSlideFiles = []

    for slideFile in slideFiles:

        parts = slideFile.name.split(" ")

        numParts = parts[-1].replace(".pdf", "")

        try:

            num = int(numParts)

        except ValueError:

            console.print(f"Error extracting slide number from {slideFile.name}")

            raise

        if num not in excludeSlideNums:

            cleanedSlideFiles.append(slideFile)

    slideFiles = cleanedSlideFiles

    numSlideFiles = len(slideFiles)

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

        for slideFile in slideFiles:

            progress.update(
                task, description=f"Transcribing {slideFile.name}", refresh=True
            )

            PDFToPNG(pdfPath=slideFile, progress=progress)

            TranscribeSlideImages(
                imageDir=Path(OUTPUT_DIR, f"{slideFile.stem}-pages"),
                limiterMethod="tracking",
                outputDir=Path(OUTPUT_DIR, f"{slideFile.stem}-output"),
                outputName=f"{slideFile.stem}-response",
                progress=progress,
            )

            progress.update(
                task,
                description=f"Transcribed {slideFile.name}",
                advance=1,
                refresh=True,
            )


def FinishSlidePickle(picklePath: Path, outputDir: Path, outputName: Path):

    with picklePath.open("rb") as file:

        responses = pickle.load(file)

    combinedResponse = ""

    i = 0

    for response in responses:

        responseText: str | list[str] = response.text
        # print(responseText)
        # print(type(responseText))
        if isinstance(responseText, str):
            responseText = responseText.splitlines()
        if responseText is None:

            # print(response)

            print(i)
            i += 1
            continue

        i += 1
        # print(responseText)
        # print(type(responseText))
        if responseText[0].strip().startswith("```"):
            responseText = responseText[1:]
        if responseText[-1].strip() == "```":
            responseText = responseText[:-1]
        combinedResponse += "\n".join(responseText) + "\n"

    Path(outputDir, f"{outputName}.txt").write_text(combinedResponse)
    cleanedResponse = CleanResponse(
        combinedResponse=combinedResponse, preamble=LECTURE_LATEX_PREAMBLE
    )
    Path(outputDir, f"{outputName}.tex").write_text(cleanedResponse)


if __name__ == "__main__":

    # PDFToPNG(
    #     pdfPath=Path(INPUT_DIR, "465-Lecture-1.pdf"),
    #     # pagesDir=Path(OUTPUT_DIR, "465-Lecture-1-pages"),
    # )

    # TranscribeSlideImages(
    #     imageDir=Path(OUTPUT_DIR, "465-Lecture-1-pages"), limiterMethod="tracking"
    # )

    BulkSlideTranscribe(excludeSlideNums=[])
