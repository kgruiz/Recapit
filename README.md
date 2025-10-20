# Recapit

Recapit is a modular toolkit for turning slide decks, lecture handouts, PDFs, and standalone images into cleaned LaTeX, Markdown, or JSON outputs using Google Gemini models. It provides a drop-in CLI, a reusable Python API, and pipelines that handle image conversion, per-model rate limiting, and template-driven prompts.

## Highlights

- **Unified pipelines** – one orchestration layer handles PDF-to-image fan out, optional direct PDF ingestion, LLM interactions, and LaTeX cleanup for slides, lectures, documents, and ad-hoc images.
- **Parallel processing** – document/image transcription and video chunk uploads run across configurable worker pools to shrink wall-clock time on larger batches.
- **Quota-aware throttling** – shared token buckets and a quota monitor keep per-model RPM/TPM and upload concurrency within Gemini’s published limits, automatically backing off when 429s appear.
- **Telemetry & cost tracking** – every request records tokens, duration, and metadata; CLI runs print a summary (with optional per-model breakdowns) and persist a JSON report with token usage and estimated spend.
- **Smart defaults** – works out of the box with built-in prompts and LaTeX preambles; override prompts via `templates/` or custom strategies in `prompts/` when you need fine control.
- **Resumable video ingestion** – manifests record normalized MP4 hashes, chunk ranges, and file URIs so reruns with `--skip-existing` only process dirty chunks.
- **Auto classification** – invoke the tool without subcommands (or via `transcribe`) and heuristics choose the right prompt for slides, notes, worksheets, or documents.
- **Image-first PDF handling** – every PDF is rasterized to per-page PNGs by default for consistent transcription; opt into direct PDF ingestion with `--pdf-mode pdf` or `PDFMode.PDF` when your chosen model supports it.
- **Drop-in CLI** – invoke the Typer CLI from the shell with zero boilerplate and steer behaviour through presets or configuration files.
- **Structured outputs** – cleaned LaTeX lands beside the source file by default; flip `RECAPIT_SAVE_FULL_RESPONSE` on if you also want raw model dumps.

## Requirements

- Python 3.10+
- Google Gemini access and a `GEMINI_API_KEY` with permissions for the latest models (e.g. `gemini-2.5-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-pro`).
- Poppler (required because PDFs are rasterized to images by default)

## Installation

```shell
# clone the repository first
cd recapit

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
| `RECAPIT_DEFAULT_MODEL` | Optional. Override the default transcription model (defaults to `gemini-2.5-flash-lite`). |
| `RECAPIT_OUTPUT_DIR` | Optional. Override the base output directory (defaults to each input's parent directory). |
| `RECAPIT_TEMPLATES_DIR` | Optional. Point to an alternate prompt template directory. |
| `RECAPIT_SAVE_FULL_RESPONSE` | Optional. Set to `1`/`true` to also write raw model text under `full-response/`. |
| `RECAPIT_SAVE_INTERMEDIATES` | Optional. Set to `1`/`true` to retain normalized videos, chunk MP4s, and manifests for debugging/re-use. |
| `RECAPIT_MAX_WORKERS` | Optional. Control the maximum number of parallel document/image workers (defaults to `4`). |
| `RECAPIT_MAX_VIDEO_WORKERS` | Optional. Control the maximum number of parallel video chunk workers (defaults to `3`). |
| `RECAPIT_VIDEO_ENCODER` | Optional. Override the encoder used for video normalization (`auto`, `cpu`, `nvenc`, `videotoolbox`, `qsv`, `amf`). `auto` probes available FFmpeg hardware encoders and prefers GPU paths when they work. |

Legacy environment variables prefixed with `LECTURE_SUMMARIZER_` remain supported for backward compatibility.

All prompt and preamble files are optional: the app ships with reasonable built-in defaults. Drop files into `templates/` when you want to override them (e.g., `document-template.txt`, `document-prompt.txt`). Strategy classes live under `recapit/prompts/` and look for matching `*-prompt.txt` files before falling back to the compiled defaults. The auto classifier inspects filenames and the first-page aspect ratio to decide between slide-, lecture-, or document-style prompts. For ambiguous cases, force a mode with `--kind slides|lecture|document`.

Prefer configuration files? Run `recapit init` to create `recapit.yaml`; it stores defaults for `default_model`, `output_dir`, `exports`, video chunk parameters, and per-preset overrides. CLI flags override environment variables, and environment variables override the YAML file, giving you explicit precedence: `CLI > ENV > YAML`.

## CLI Usage

After installation the `recapit` command becomes available. Export `GEMINI_API_KEY` first, then explore the commands below.

```shell
export GEMINI_API_KEY="..."

# Inspect how an asset will be processed (no API calls)
recapit plan input/video.mp4
recapit plan https://example.com/report.pdf --json

# Run the new engine with sensible defaults
recapit summarize input/lecture.mp4 --export srt --preset quality

# Generate a starter config
recapit init

# Post-processing helpers
recapit convert md output/course-notes
recapit convert json output/course-notes --recursive
```

`recapit summarize` accepts the same `--kind`/`--pdf-mode` overrides as the legacy pipeline, plus:

- `--export srt|vtt` to emit subtitle tracks using chunk boundaries.
- `--preset speed|quality|basic` to adjust defaults (e.g., `speed` forces rasterized PDFs; `quality` prefers native PDF ingestion when the model supports it).

Every run writes:

- `<slug>/<slug>-transcribed.tex` – cleaned LaTeX body content.
- `run-summary.json` – totals, estimated spend, and a list of output artifacts.
- `run-events.ndjson` – per-request telemetry (one JSON object per API call).
- `chunks.json` – manifest for normalized video assets (video inputs only); manifests include hashes and chunk response paths so reruns with `--skip-existing` honor prior work.
- Optional `.srt`/`.vtt` files when `--export` is provided.

Use `--hide-summary`, `--detailed-costs`, and `--summary-path` to adjust the console summary behaviour.

## Output Structure

Each source asset produces a slugified directory next to the input. For example, a `Lecture01.pdf` transcription now yields:
```
path/to/slides/
  lecture01/
    page-images/
      Lecture01-transcribed-0.png
      ...
    Lecture01-transcribed.tex
```

If `RECAPIT_SAVE_FULL_RESPONSE` (or the legacy `LECTURE_SUMMARIZER_SAVE_FULL_RESPONSE`) is enabled, you'll also see `full-response/lecture01-transcribed.txt` alongside the cleaned LaTeX.

Markdown (`*.md`) and JSON (`*.json`) files are written alongside the LaTeX when you run the conversion utilities.

Video inputs produce chunk-aware LaTeX: each chunk is emitted as `\section*{Chunk N (HH:MM:SS–HH:MM:SS)}` inside `<stem>-transcribed.tex`. When `--save-full-response` is active, every raw chunk response is also captured under `full-response/chunks/`. Intermediates such as normalized MP4s and chunk slices are discarded by default unless you pass `--save-intermediates` (or set `RECAPIT_SAVE_INTERMEDIATES=1`, legacy `LECTURE_SUMMARIZER_SAVE_INTERMEDIATES=1`).
Hardware acceleration is enabled automatically when FFmpeg exposes GPU encoders; fall back to `--video-encoder cpu` if you run into driver issues.

Every CLI run additionally writes a JSON telemetry report (default `run-summary.json`). The report contains:

- Aggregate token counts (input/output/total) and request durations.
- Per-model breakdowns covering requests, tokens, and estimated cost.
- A flag noting whether any costs were estimated (e.g., when the API omits token usage and the tool infers values from video duration).

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
