import typer
from pathlib import Path

from .api import (
    TranscribeSlides,
    TranscribeLectures,
    TranscribeDocuments,
    TranscribeImages,
    LatexToMarkdown,
    LatexToJson,
)
from .constants import GEMINI_2_FLASH, GEMINI_2_FLASH_THINKING_EXP


app = typer.Typer(add_completion=False)


@app.command()
def slides(
    source: Path,
    output_dir: Path | None = None,
    model: str = GEMINI_2_FLASH,
    exclude: str = "",
    pattern: str = r".*(\d+).*",
    skip_existing: bool = True,
):
    ex = [int(x) for x in exclude.split(",") if x.strip()] if exclude else []
    TranscribeSlides(
        source,
        outputDir=output_dir,
        lectureNumPattern=pattern,
        excludeLectureNums=ex,
        skipExisting=skip_existing,
        model=model,
    )


@app.command()
def lectures(
    source: Path,
    output_dir: Path | None = None,
    model: str = GEMINI_2_FLASH,
    exclude: str = "",
    pattern: str = r".*(\d+).*",
    skip_existing: bool = True,
):
    ex = [int(x) for x in exclude.split(",") if x.strip()] if exclude else []
    TranscribeLectures(
        source,
        outputDir=output_dir,
        lectureNumPattern=pattern,
        excludeLectureNums=ex,
        skipExisting=skip_existing,
        model=model,
    )


@app.command()
def documents(
    source: Path,
    output_dir: Path | None = None,
    model: str = GEMINI_2_FLASH,
    recursive: bool = False,
    skip_existing: bool = True,
    output_name: str | None = None,
):
    TranscribeDocuments(
        source,
        outputDir=output_dir,
        skipExisting=skip_existing,
        outputName=output_name,
        recursive=recursive,
        model=model,
    )


@app.command()
def images(
    source: Path,
    output_dir: Path | None = None,
    model: str = GEMINI_2_FLASH,
    pattern: str = "*.png",
    separate: bool = True,
    skip_existing: bool = True,
):
    TranscribeImages(
        source,
        outputDir=output_dir,
        filePattern=pattern,
        separateOutputs=separate,
        skipExisting=skip_existing,
        model=model,
    )


@app.command("latex-md")
def latex_md(
    source: Path,
    output_dir: Path | None = None,
    pattern: str = "*.tex",
    skip_existing: bool = True,
    model: str = GEMINI_2_FLASH_THINKING_EXP,
):
    LatexToMarkdown(
        source,
        outputDir=output_dir,
        filePattern=pattern,
        skipExisting=skip_existing,
        model=model,
    )


@app.command("latex-json")
def latex_json(
    source: Path,
    output_dir: Path | None = None,
    pattern: str = "*.tex",
    skip_existing: bool = True,
    recursive: bool = False,
    model: str = GEMINI_2_FLASH_THINKING_EXP,
):
    LatexToJson(
        source,
        outputDir=output_dir,
        filePattern=pattern,
        skipExisting=skip_existing,
        recursive=recursive,
        model=model,
    )


def main():
    app()


if __name__ == "__main__":
    main()
