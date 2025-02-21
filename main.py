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

console = Console()

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

    raise FileNotFoundError(f"Latex preamble file {LATEX_PREAMBLE_PATH} not found")

LATEX_PREAMBLE = LATEX_PREAMBLE_PATH.read_text()


def PDFToPNG():

    pdfPath = Path(input, "465-Lecture-1.pdf")
    images = convert_from_path(pdfPath)
    outputDir = Path(OUTPUT_DIR, f"{pdfPath.stem}-pages")

    if outputDir.exists():
        shutil.rmtree(outputDir)

    outputDir.mkdir(parents=True, exist_ok=True)

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

        task = progress.add_task(f"Converting {pdfPath.name} to PNG", total=len(images))

        for i, image in enumerate(images):
            image.save(Path(outputDir, f"{pdfPath.stem}-{i}.png"), "PNG")
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
    title: str = "",
    author: str = "",
    date: str = "",
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

    preamble = re.sub(
        r"^\\title\{(.*)\}", rf"\\title{{{title}}}", preamble, flags=re.MULTILINE
    )
    preamble = re.sub(
        r"^\\author\{(.*)\}", rf"\\author{{{author}}}", preamble, flags=re.MULTILINE
    )
    preamble = re.sub(
        r"^\\date\{(.*)\}", rf"\\date{{{date}}}", preamble, flags=re.MULTILINE
    )

    cleanedResponse = f"{preamble}\n{combinedResponse}\n{END_DOCUMENT_LINE}"

    return cleanedResponse


def ImageQuery(limiterMethod: str = "tracking"):
    """
    Processes images and queries an API, optionally rate-limiting the calls if the number
    of images exceeds the defined rate limit.

    Parameters
    ----------
    limiterMethod : str, optional
        The rate limiting method to use when len(images) >= RATE_LIMIT_PER_MINUTE.
        Supported values:
            - "fixedDelay": Sleeps for a fixed delay between each call.
            - "tracking": Tracks request timestamps and sleeps only if needed.
        Defaults to "tracking".
    """

    IMAGE_DIR = Path(OUTPUT_DIR, "465-Lecture-1-pages")
    images = [PIL.Image.open(imagePath) for imagePath in IMAGE_DIR.glob("*.png")]

    apiKey = os.getenv("GEMINI_API_KEY")
    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = []
    defaultDescription = "Querying images"
    useRateLimit = len(images) >= RATE_LIMIT_PER_MINUTE
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE
    requestTimes = deque()

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[bold blue]{defaultDescription}", justify="left"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        expand=True,
    ) as progress:

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
                    while (
                        requestTimes
                        and currentTime - requestTimes[0] >= RATE_LIMIT_WINDOW
                    ):
                        requestTimes.popleft()

                    if len(requestTimes) >= RATE_LIMIT_PER_MINUTE:
                        sleepTime = RATE_LIMIT_WINDOW - (currentTime - requestTimes[0])
                        SleepWithProgress(progress, task, sleepTime, defaultDescription)
                        currentTime = time.time()
                        while (
                            requestTimes
                            and currentTime - requestTimes[0] >= RATE_LIMIT_WINDOW
                        ):
                            requestTimes.popleft()

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
                    requestTimes.append(time.time())
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

    try:

        picklePath = Path(PICKLE_DIR, "responses.pkl")

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

    for response in responses:

        responseText: str | list[str] = response.text

        if isinstance(responseText, str):

            responseText = responseText.splitlines()

            if responseText[0].strip().startswith("```"):
                responseText = responseText[1:]

            if responseText[-1].strip() == "```":
                responseText = responseText[:-1]

        combinedResponse += ("\n".join(responseText) + "\n").strip()

    # print(combinedResponse)
    Path(OUTPUT_DIR, "response.txt").write_text(combinedResponse)

    cleanedResponse = CleanResponse(
        combinedResponse=combinedResponse, preamble=LATEX_PREAMBLE
    )

    Path(OUTPUT_DIR, "response.tex").write_text(cleanedResponse)


if __name__ == "__main__":

    ImageQuery(limiterMethod="tracking")
