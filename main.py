import os
from pathlib import Path

import PIL.Image
from google import genai
from google.genai import types

IMAGE_PATH = Path("puppy.png")

image = PIL.Image.open(IMAGE_PATH)

apiKey = os.getenv("GEMINI_API_KEY")

if apiKey is None:
    raise ValueError("GEMINI_API_KEY environment variable not set")

client = genai.Client(api_key=apiKey)
response = client.models.generate_content(
    model="gemini-2.0-flash", contents=["What is this image?", image]
)

print(response.text)
