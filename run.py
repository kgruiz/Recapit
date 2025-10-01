from lecture_summarizer import TranscribeDocuments
from pathlib import Path

if __name__ == "__main__":
    german_sources = list(
        Path("/Users/kadengruizenga/Documents/School/w25/German322/podcast/sources").glob("*.pdf")
    )
    TranscribeDocuments(german_sources)
