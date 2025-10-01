from main import *

if __name__ == "__main__":

    # TODO: Fix bug: all tex files have same name?
    # TODO: Auto restart with rate/other errors

    German322PodcastSources = Path(
        "/Users/kadengruizenga/Documents/School/w25/German322/podcast/sources"
    ).glob("*.pdf")

    German322PodcastSources = list(German322PodcastSources)

    TranscribeDocuments(
        German322PodcastSources,
    )
