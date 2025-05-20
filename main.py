import os
import pickle
import re
import time
from collections import OrderedDict
from pathlib import Path

import PIL.Image
from google import genai
from natsort import natsorted
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from lecture_summarizer.config import (
    console,
    GLOBAL_REQUEST_TIMES,
    GEMINI_2_FLASH,
    GEMINI_2_FLASH_THINKING_EXPERIMENTAL,
    RATE_LIMITS,
    RATE_LIMIT_WINDOW_SEC,
    RATE_LIMIT_PER_MINUTE,
    OUTPUT_DIR,
    SLIDE_LATEX_PREAMBLE,
    LECTURE_LATEX_PREAMBLE,
    DOCUMENT_LATEX_PREAMBLE,
    IMAGE_LATEX_PREAMBLE,
    LATEX_TO_MARKDOWN_PROMPT,
    LATEX_TO_JSON_PROMPT,
)
from lecture_summarizer.utils.file_utils import get_total_page_count as _GetTotalPageCount, pdf_to_png as PDFToPNG
from lecture_summarizer.utils.text_utils import remove_code_block_syntax as _RemoveCodeBlockSyntax, clean_response as _CleanResponse
from lecture_summarizer.utils.rate_limiter import sleep_with_progress as _SleepWithProgress



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
    # if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
    #     currentLimiterMethod = "fixedDelay"
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
    # if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
    #     currentLimiterMethod = "fixedDelay"
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
    model: str = GEMINI_2_FLASH,
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

    if not isinstance(model, str):

        raise TypeError(
            f'Parameter "model" must be a string. Given type: "{type(model).__name__}"'
        )

    if model not in RATE_LIMITS:

        raise ValueError(
            f'Model "{model}" is not an available model.\nAvailable Models:\n{"\n".join(list(RATE_LIMITS.keys()))}'
        )

    rateLimit = RATE_LIMITS.get(model)

    currentLimiterMethod = limiterMethod
    # if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
    #     currentLimiterMethod = "fixedDelay"
    delayBetweenCalls = 60 / rateLimit

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
                                f"Transcribe the document image, including all math, in LaTeX format. Put tables into latex tables."
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
                                f"Escape special characters appropriately. No need to manually add in the page numbers."
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

    # if len(imageTuples) < RATE_LIMIT_PER_MINUTE:
    #     currentLimiterMethod = "fixedDelay"

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


def _LatexToJSON(
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
        outputName += ".json"

    outputPath = Path(outputDir, outputName)

    apiKey = os.getenv("GEMINI_API_KEY")

    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    defaultDescription = "Converting LaTeX File to JSON"

    currentLimiterMethod = limiterMethod

    # Kept to align with other functions, might remove
    # if 1 < rateLimit:
    #     currentLimiterMethod = "fixedDelay"

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
                            f"Instructions:\n{LATEX_TO_JSON_PROMPT}\n\n```\n{latexContent}\n```",
                        ],
                    )

            except Exception as e:

                console.print(
                    f"[bold red]Error during LaTeX to JSON conversion of {latexSource.name}: {e}[/bold red]"
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
                            f"Instructions:\n{LATEX_TO_JSON_PROMPT}\n\n```\n{latexContent}\n```",
                        ],
                    )

            except Exception as e:

                console.print(
                    f"[bold red]Error during LaTeX to JSON transcription of {latexSource.name}: {e}[/bold red]"
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
    model: str = GEMINI_2_FLASH_THINKING_EXPERIMENTAL,
):

    # TODO: Get thinking working. Response is blank currently.

    if not isinstance(model, str):

        raise TypeError(
            f'Parameter "model" must be a string. Given type: "{type(model).__name__}"'
        )

    if model not in RATE_LIMITS:

        raise ValueError(
            f'Model "{model}" is not an available model.\nAvailable Models:\n{"\n".join(list(RATE_LIMITS.keys()))}'
        )

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        sourceConverted = []
        for item in source:
            if isinstance(item, str):
                sourceConverted.append(Path(item))
            elif isinstance(item, Path):
                sourceConverted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = sourceConverted

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
        sourceConverted = []
        for item in source:
            if isinstance(item, str):
                sourceConverted.append(Path(item))
            elif isinstance(item, Path):
                sourceConverted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = sourceConverted

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

        task = progress.add_task("Transcribing slide files", total=numSlideFiles)
        allPagesTask = progress.add_task("Transcribing slide pages", total=totalPages)

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

                progress.update(
                    allPagesTask,
                    description=f"Skipping {slideFile.name}",
                    advance=_GetTotalPageCount(slideFile),
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
        sourceConverted = []
        for item in source:
            if isinstance(item, str):
                sourceConverted.append(Path(item))
            elif isinstance(item, Path):
                sourceConverted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = sourceConverted

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

                progress.update(
                    allPagesTask,
                    description=f"Skipping {lectureFile.name}",
                    advance=_GetTotalPageCount(lectureFile),
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
    outputName: str | None = None,
    recursive: bool = False,
    model: str = GEMINI_2_FLASH,
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
        sourceConverted = []
        for item in source:
            if isinstance(item, str):
                sourceConverted.append(Path(item))
            elif isinstance(item, Path):
                sourceConverted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = sourceConverted

    if isinstance(source, Path):
        if not source.exists():
            raise FileNotFoundError(f"Path {source} does not exist.")
        if source.is_file():
            pdfFiles = [source]
            inputDir = source.parent
        elif recursive:
            pdfFiles = natsorted(list(source.rglob("*.pdf")))
            inputDir = source
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

    if outputName is not None:

        if not isinstance(outputName, str):

            raise TypeError(
                f"outputName must be a string. Given type: {type(outputName).__name__}"
            )

        if "." in outputName:

            raise ValueError(
                f"outputName should not contain a file extension. Given: {outputName}"
            )

        if "/" in outputName:

            raise ValueError(
                f"outputName should not contain a path separator. Given: {outputName}"
            )

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

            if outputName is None:

                outputName = f"{pdfFile.stem}-transcribed"

            outputPath = Path(baseOutputDir, outputName)

            if skipExisting and outputPath.with_suffix(".tex").exists():

                progress.update(
                    task,
                    description=f"Skipping {pdfFile.name}",
                    advance=1,
                    refresh=True,
                )

                progress.update(
                    allPagesTask,
                    description=f"Skipping {pdfFile.name}",
                    advance=_GetTotalPageCount(pdfFile),
                    refresh=True,
                )

                continue

            PDFToPNG(
                pdfPath=pdfFile,
                pagesDir=pagesDir,
                progress=progress,
                outputName=outputName,
            )

            _TranscribeDocumentImages(
                imageDir=pagesDir,
                limiterMethod="tracking",
                outputDir=baseOutputDir,
                outputName=outputName,
                fullResponseDir=fullResponseDir,
                progress=progress,
                bulkPagesTask=allPagesTask,
                model=model,
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
        sourceConverted = []
        for item in source:
            if isinstance(item, str):
                sourceConverted.append(Path(item))
            elif isinstance(item, Path):
                sourceConverted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = sourceConverted

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


def LatexToJson(
    source: Path | list[Path] | str | list[str],
    outputDir: Path = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
    model: str = GEMINI_2_FLASH_THINKING_EXPERIMENTAL,
    recursive=False,
):

    # TODO: Get thinking working. Response is blank currently.

    if not isinstance(model, str):

        raise TypeError(
            f'Parameter "model" must be a string. Given type: "{type(model).__name__}"'
        )

    if model not in RATE_LIMITS:

        raise ValueError(
            f'Model "{model}" is not an available model.\nAvailable Models:\n{"\n".join(list(RATE_LIMITS.keys()))}'
        )

    # Convert string input(s) to Path(s) if needed.
    if isinstance(source, str):
        source = Path(source)
    elif isinstance(source, list):
        sourceConverted = []
        for item in source:
            if isinstance(item, str):
                sourceConverted.append(Path(item))
            elif isinstance(item, Path):
                sourceConverted.append(item)
            else:
                raise ValueError("List items must be strings or Path objects.")
        source = sourceConverted

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
        elif recursive:

            latexFiles = natsorted(list(source.rglob(filePattern)))
            inputDir = source

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

        defaultDescription = "Converting Latex Files to JSON"

    else:

        defaultDescription = "Converting Latex File to JSON"

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

            outputPath = Path(outputSubDir, f"{outputName}.json")

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

            _LatexToJSON(
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
            f"[bold green]Converted [bold yellow]{totalLatexFiles}[/bold yellow] LaTeX files to JSON[/bold green]"
        )


# ```latex
# <INSERT YOUR TABLE HERE>
# ```


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


def FinishPickleLatexToMarkdown(
    picklePath: Path,
    outputDir: Path,
    outputName: str,
):

    if not isinstance(picklePath, Path):
        raise TypeError(
            f'Parameter "picklePath" must be a Path. Given type: "{type(picklePath).__name__}"'
        )

    if not isinstance(outputDir, Path):
        raise TypeError(
            f'Parameter "outputDir" must be a Path. Given type: "{type(outputDir).__name__}"'
        )

    if not isinstance(outputName, str):
        raise TypeError(
            f'Parameter "outputName" must be a str. Given type: "{type(outputName).__name__}"'
        )

    with picklePath.open("rb") as file:

        response = pickle.load(file)

    # Ensure outputName has an extension
    if not Path(outputName).suffix:
        outputName += ".md"

    outputPath = Path(outputDir, outputName)

    if response is None:

        raise ValueError(f"Response is {response}")

    print(f"{response=}")

    responseText = response.text

    responseText = _RemoveCodeBlockSyntax(responseText)

    outputPath.write_text(responseText)


if __name__ == "__main__":

    math465Lectures = [
        Path(
            "/Users/kadengruizenga/Documents/School/W25/Math465/Slides/465 Lecture 18.pdf"
        ),
        Path(
            "/Users/kadengruizenga/Documents/School/W25/Math465/Slides/465 Lecture 19.pdf"
        ),
    ]

    TranscribeSlides(
        math465Lectures,
        outputDir=Path(
            "/Users/kadengruizenga/Documents/School/W25/Math465/Summaries/Lectures/Transcribed"
        ),
    )
