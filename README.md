# Lecture Summarizer

Lecture Summarizer is a modular toolkit for turning slide decks, lecture handouts, PDFs, and standalone images into cleaned LaTeX, Markdown, or JSON outputs using Google Gemini models. It provides a drop-in CLI, a reusable Python API, and pipelines that handle image conversion, per-model rate limiting, and template-driven prompts.

## Highlights

- **Unified pipelines** – one orchestration layer handles PDF-to-image fan out, direct PDF ingestion, LLM interactions, and LaTeX cleanup for slides, lectures, documents, and ad-hoc images.
- **Per-model throttling** – built-in token bucket rate limiter respects conservative RPM caps for current Gemini models.
- **Smart defaults** – works out of the box with built-in prompts and LaTeX preambles, but you can drop override files in `templates/` when you need fine control.
- **Drop-in CLI & library** – invoke the Typer CLI from the shell or call the same functionality from Python without global state.
- **Structured outputs** – every run captures raw model responses and cleaned LaTeX in deterministic directories under `output/`.

## Requirements

- Python 3.10+
- Google Gemini access and a `GEMINI_API_KEY` with permissions for the latest models (e.g. `gemini-2.5-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-pro`).
- Poppler (needed by `pdf2image` when using image-based PDF transcription)

## Installation

```shell
# clone the repository first
cd lecture-summarizer

# Recommended: uv for editable installs
uv pip install -e .

# or fallback to pip
python -m pip install -e .
```

To work on the codebase locally, create a virtual environment (e.g., `uv venv` or `python -m venv .venv`) before installing dependencies.

## Configuration

Environment variables:

| Setting | Description |
| --- | --- |
| `GEMINI_API_KEY` | Required. API key picked up by the CLI and Python API via `AppConfig.from_env`. |
| `LECTURE_SUMMARIZER_DEFAULT_MODEL` | Optional. Override the default transcription model (defaults to `gemini-2.5-flash-lite`). |
| `LECTURE_SUMMARIZER_OUTPUT_DIR` | Optional. Override the base output directory (`output/` by default). |
| `LECTURE_SUMMARIZER_TEMPLATES_DIR` | Optional. Point to an alternate prompt template directory. |

All prompt and preamble files are optional: the app ships with reasonable built-in defaults. Drop files into `templates/` when you want to override them (e.g., `document-template.txt`, `document-prompt.txt`).

## CLI Usage

After installation the `lecture-summarizer` command becomes available. Every command accepts a path to a file or directory; directories are enumerated with natural sorting.

```shell
export GEMINI_API_KEY="..."

# Slides and lecture decks
lecture-summarizer slides /path/to/slides --pdf-mode images
lecture-summarizer lectures /path/to/lectures --exclude 3,5

# Generic PDFs (documents, worksheets, papers)
lecture-summarizer documents /path/to/pdfs --recursive --pdf-mode pdf

# Static images (PNG by default)
lecture-summarizer images /path/to/imgs --pattern "*.jpg" --separate false

# Post-processing helpers
lecture-summarizer latex-md /path/to/tex
lecture-summarizer latex-json /path/to/tex --recursive
```

Run `lecture-summarizer --help` or `lecture-summarizer <command> --help` for parameter details.

## Python API

Every CLI command is backed by the same importable API. Common entry points:

```python
from lecture_summarizer import (
    TranscribeDocuments,
    TranscribeLectures,
    LatexToMarkdown,
)

docs = "/path/to/pdfs"
TranscribeDocuments(docs, recursive=True)
```

An API call automatically:
1. Loads configuration from the environment.
2. Applies per-model rate limits.
3. Chooses between direct PDF ingestion (if the selected model supports it) or PDF-to-image fan out.
4. Writes combined raw output (`full-response/{name}.txt`) and cleaned LaTeX (`{name}.tex`).

## Output Structure

Each source asset produces a slugified directory inside `output/`. For example, a `Lecture01.pdf` transcription yields:
```
output/
  lecture01/
    page-images/
      Lecture01-transcribed-0.png
      ...
    full-response/
      Lecture01-transcribed.txt
    Lecture01-transcribed.tex
```

Markdown (`*.md`) and JSON (`*.json`) files are written alongside the LaTeX when you run the conversion utilities.

## Development

- Follow the workflow documented in [CONTRIBUTING.md](CONTRIBUTING.md).
- Linting & formatting: use `python -m compileall` for quick syntax checks, and run any project-specific linters/tests added in the future.
- Preferred package tooling: `uv` for dependency management, `pnpm` for any JS tooling, `cargo`/`just` for Rust integrations.

## Roadmap Ideas

- Add resumable job metadata for long-running transcripts.
- Expose streaming progress events for upstream integrations.
- Ship optional Markdown/JSON schema validators.

## License

Released under the [GNU General Public License v3.0](LICENSE).
