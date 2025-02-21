import os
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

console = Console()

# Define rate limit constants
RATE_LIMIT_PER_MINUTE = 15
RATE_LIMIT_WINDOW = 60


def PDFToPNG():

    pdfPath = Path("465-Lecture-1.pdf")

    images = convert_from_path(pdfPath)

    outputDir = Path(f"{pdfPath.stem}-pages")

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

    IMAGE_DIR = Path("465-Lecture-1-pages")
    images = [PIL.Image.open(imagePath) for imagePath in IMAGE_DIR.glob("*.png")]

    apiKey = os.getenv("GEMINI_API_KEY")
    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    responses = []

    # Determine if rate limiting is needed based on number of images.
    useRateLimit = len(images) >= RATE_LIMIT_PER_MINUTE

    # For fixed delay method
    delayBetweenCalls = 60 / RATE_LIMIT_PER_MINUTE

    # For tracking method, initialize a deque to store timestamps.
    requestTimes = deque()

    defaultDescription = "Querying images"
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

            # If rate limiting is needed, enforce the chosen method
            if useRateLimit:
                if limiterMethod == "fixedDelay":
                    startTime = currentTime
                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and describe the contents.\n\nLatex Preamble:{Path('slide-template.txt').read_text()}",
                            image,
                        ],
                    )
                    responses.append(response)
                    elapsed = time.time() - startTime

                    if elapsed < delayBetweenCalls:
                        sleepTime = delayBetweenCalls - elapsed
                        progress.update(
                            task,
                            description=f"Sleeping for {sleepTime:.1f} sec due to rate limit",
                            refresh=True,
                        )
                        time.sleep(sleepTime)
                        progress.update(task, description=defaultDescription)

                elif limiterMethod == "tracking":
                    # Remove timestamps older than the window.
                    while (
                        requestTimes
                        and currentTime - requestTimes[0] >= RATE_LIMIT_WINDOW
                    ):
                        requestTimes.popleft()

                    # If adding a new request would breach the rate limit, sleep until it's safe.
                    if len(requestTimes) >= RATE_LIMIT_PER_MINUTE:
                        sleepTime = RATE_LIMIT_WINDOW - (currentTime - requestTimes[0])
                        progress.update(
                            task,
                            description=f"Sleeping for {sleepTime:.1f} sec due to rate limit",
                            refresh=True,
                        )
                        time.sleep(sleepTime)
                        currentTime = time.time()
                        while (
                            requestTimes
                            and currentTime - requestTimes[0] >= RATE_LIMIT_WINDOW
                        ):
                            requestTimes.popleft()
                        progress.update(task, description=defaultDescription)

                    client = genai.Client(api_key=apiKey)
                    response = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[
                            f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and describe the contents.\n\nLatex Preamble:{Path('slide-template.txt').read_text()}",
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

                # No rate limiting needed; simply make the API call.
                client = genai.Client(api_key=apiKey)
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        f"Transcribe the image, including all math, in latex format. Use the given preamble as a base, ensuring any other needed packages or other things are added if needed. Ensure characters like '&', '%', etc, are escaped properly in the latex document. Don't attempt to include any outside files, images, etc. If there's a graphic or illustration, either attempt to recreate it with tikz or just leave a placeholder and describe the contents.\n\nLatex Preamble:{Path('slide-template.txt').read_text()}",
                        image,
                    ],
                )
                responses.append(response)

            progress.update(task, advance=1)

    # Combine responses for final output.
    combinedResponse = ""
    for response in responses:
        responseText: str = response.text
        if responseText.splitlines()[0].strip() == "```":
            responseText = responseText.splitlines()[1:]
        if responseText.splitlines()[-1].strip() == "```":
            responseText = responseText.splitlines()[:-1]
        combinedResponse += "\n".join(responseText) + "\n"

    print(combinedResponse)
    Path("response.txt").write_text(combinedResponse)


if __name__ == "__main__":
    ImageQuery(limiterMethod="tracking")
