import os
import shutil
from pathlib import Path

import PIL.Image
from google import genai
from google.genai import types
from pdf2image import convert_from_path


def PDFToPNG():

    pdfPath = Path("465-Lecture-1.pdf")

    images = convert_from_path(pdfPath)

    outputDir = Path(f"{pdfPath.stem}-pages")

    if outputDir.exists():

        shutil.rmtree(outputDir)

    outputDir.mkdir(parents=True, exist_ok=True)

    for i, image in enumerate(images):

        image.save(Path(outputDir, f"{pdfPath.stem}-{i}.png"), "PNG")


def ImageQuery():

    # IMAGE_PATH = Path("puppy.png")

    # IMAGE_PATH = Path("465-Lecture-1-pages/465-Lecture-1-21.png")

    IMAGE_DIR = Path("465-Lecture-1-pages")

    images = []

    for imagePath in IMAGE_DIR.glob("*.png"):

        image = PIL.Image.open(imagePath)

        images.append(image)

    # image = PIL.Image.open(IMAGE_PATH)

    apiKey = os.getenv("GEMINI_API_KEY")

    if apiKey is None:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    client = genai.Client(api_key=apiKey)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            "Transcribe each image, including all math, in latex format. Every slide should be a different section in a single final latex document.",
            images,
        ],
    )

    print(response.text)

    Path("response.txt").write_text(response.text)


if __name__ == "__main__":

    ImageQuery()
