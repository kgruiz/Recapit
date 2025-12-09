# Recapit

Recapit is a Rust CLI for turning slide decks, lecture handouts, PDFs, YouTube videos, and standalone images into cleaned Markdown or LaTeX using Google Gemini models. It bundles asset discovery, ffmpeg/yt-dlp normalization, quota-aware retries, and prompt preambles into one binary. The default model is `gemini-3-pro-preview`, with full support for the generally available Gemini 2.5 family (`gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`).

## Highlights

- **Unified pipelines** – one orchestration layer handles PDF-to-image fan out, optional direct PDF ingestion, LLM interactions, and Markdown structuring for slides, lectures, documents, and ad-hoc images.
- **Parallel processing** – document/image transcription and video chunk uploads run across configurable worker pools to shrink wall-clock time on larger batches.
- **Quota-aware throttling** – shared token buckets and a quota monitor keep per-model RPM/TPM and upload concurrency within Gemini’s published limits, automatically backing off when 429s appear.
- **Telemetry & cost tracking** – every request records tokens, duration, and metadata; CLI runs print a summary (with optional per-model breakdowns) and persist a JSON report with token usage and estimated spend.
- **Smart defaults** – works out of the box with built-in prompts and Markdown headers; override prompts via `templates/` or custom strategies in `prompts/` when you need fine control.
- **Resumable video ingestion** – manifests record normalized MP4 hashes, chunk ranges, and file URIs so reruns with `--skip-existing` only process dirty chunks.
- **Auto classification** – invoke the tool without subcommands (or via `transcribe`) and heuristics choose the right prompt for slides, notes, worksheets, or documents.
- **Image-first PDF handling** – every PDF is rasterized to per-page PNGs at 200 DPI by default for consistent transcription; change DPI with `--pdf-dpi` (or `pdf.dpi` in `recapit.yaml`) or opt into direct PDF ingestion with `--pdf-mode pdf`/`PDFMode.PDF` when your chosen model supports it.
- **Drop-in CLI** – invoke the Typer CLI from the shell with zero boilerplate and steer behaviour through presets or configuration files.
- **Structured outputs** – cleaned Markdown lands beside the source file by default; choose `--format latex` (or set the config default) when you want direct LaTeX instead. Flip `RECAPIT_SAVE_FULL_RESPONSE` on if you also want raw model dumps and `RECAPIT_SAVE_INTERMEDIATES` to keep normalized/manifest artifacts.
- **Preset-aware CLI** – compose presets in `recapit.yaml` and layer them with command-line overrides to adjust models, exports, concurrency, and media resolution without duplicating flags.

## Quick Start

```shell
./install
export GEMINI_API_KEY="your-key"
recapit input.pdf
```

The command above installs the CLI, sets your API key, and transcribes `input.pdf` to Markdown beside the source file. Add `--format latex` for LaTeX output or `--export srt vtt` for subtitles.

## Requirements

- Rust 1.79+ and Cargo.
- Google Gemini access and a `GEMINI_API_KEY` with permissions for `gemini-3-pro-preview` (default) or the GA Gemini 2.5 family (`gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`).
- Poppler (`pdftoppm`, `pdfinfo`) and FFmpeg; yt-dlp is required for YouTube URLs.

## Installation

Clone the repository, then run the helper script to install (or update) the CLI into your Cargo bin directory:

```shell
./install
```

The installer also drops `man/recapit.1` into `${MANPREFIX:-~/.local/share/man}/man1`, so `man recapit` works after installation (set `MANPREFIX` to change the target).

The script ensures `cargo` is available, warns if external tools such as `ffmpeg`, `yt-dlp`, or Poppler’s `pdftoppm`/`pdfinfo` are missing, and finally executes `cargo install --path . --locked --force`. Any extra flags you pass to `./install` are forwarded to `cargo install`.

Prefer to invoke Cargo directly? Run:

```shell
cargo install --path . --locked --force
```

Or run directly without installing:

```shell
cargo run -- transcribe input.pdf --export srt
```

## Configuration

Environment variables:

| Setting | Description |
| --- | --- |
| `GEMINI_API_KEY` | Required. API key consumed by the CLI via `AppConfig::load`. |
| `RECAPIT_DEFAULT_MODEL` | Optional. Override the default transcription model (defaults to `gemini-3-pro-preview`). |
| `RECAPIT_OUTPUT_DIR` | Optional. Override the base output directory (defaults to each input's parent directory). |
| `RECAPIT_PDF_DPI` | Optional. DPI to use when rasterizing PDFs to PNGs (defaults to `200`). |
| `RECAPIT_TEMPLATES_DIR` | Optional. Point to an alternate prompt template directory. |
| `RECAPIT_SAVE_FULL_RESPONSE` | Optional. Set to `1`/`true` to also write raw model text under `full-response/`. |
| `RECAPIT_SAVE_INTERMEDIATES` | Optional. Set to `1`/`true` to retain normalized videos, chunk MP4s, and manifests for debugging/re-use. |
| `RECAPIT_MAX_WORKERS` | Optional. Control the maximum number of parallel document/image workers (defaults to `4`). |
| `RECAPIT_MAX_VIDEO_WORKERS` | Optional. Control the maximum number of parallel video chunk workers (defaults to `3`). |
| `RECAPIT_TOKENS_PER_SECOND` | Optional. Override the effective tokens-per-second budget used to slice video/audio inputs. |
| `RECAPIT_VIDEO_MAX_CHUNK_SECONDS` | Optional. Cap per-chunk duration when planning video segments (defaults to `7200`). |
| `RECAPIT_VIDEO_MAX_CHUNK_BYTES` | Optional. Cap per-chunk size in bytes (defaults to `524288000`). |
| `RECAPIT_VIDEO_MEDIA_RESOLUTION` | Optional. Force Gemini media resolution hints: `default`, `low`, `medium`, `high`, `unspecified`. |
| `RECAPIT_VIDEO_ENCODER` | Optional. Override the encoder used for video normalization (`auto`, `cpu`, `nvenc`, `videotoolbox`, `qsv`, `amf`). `auto` probes available FFmpeg hardware encoders and prefers GPU paths when they work. |

Environment variables prefixed with `LECTURE_SUMMARIZER_` remain supported for compatibility with older configurations, but new setups should prefer the `RECAPIT_` variants.

All prompt and preamble files are optional: the app ships with reasonable built-in defaults. Drop files into `templates/` when you want to override them (e.g., `document-template.txt`, `document-prompt.txt`). The auto classifier inspects filenames and the first-page aspect ratio to decide between slide-, lecture-, or document-style prompts. For ambiguous cases, force a mode with `--kind slides|lecture|document`.

Prefer configuration files? Create `recapit.yaml` in the repo root to store defaults for `default_model`, `output_dir`, `exports`, video chunk parameters, and per-preset overrides. CLI flags override environment variables, and environment variables override the YAML file, giving you explicit precedence: `CLI > ENV > YAML`.

## CLI Usage

After installation the `recapit` command becomes available. Export `GEMINI_API_KEY` first, then explore the commands below.

### Command Overview

| Command | Purpose | Highlights |
| --- | --- | --- |
| `recapit <SOURCE>` | Default transcribe workflow | Honors presets/config, supports exports (`srt`, `vtt`, `markdown`, `json`), YouTube URLs, directory recursion |
| `recapit <SOURCE> --dry-run [--json]` | Preview ingestion + normalization only | No Gemini calls; shows assets/chunks; `--json` for machine-readable output |
| `recapit <SOURCE> --to markdown|json [--from auto|latex|markdown]` | Batch-convert existing LaTeX/Markdown to Markdown or JSON via Gemini | Supports `--file-pattern`, `--recursive`, `--skip-existing` |
| `recapit report cost` | Summarize token/cost telemetry from a previous run | Works on `run-summary.json` or directories |
| `recapit cleanup cache|downloads` | Remove cached downloads or normalized artifacts | Safe-by-default; pass `--yes` to apply |

All commands support `--config` to point at an alternate YAML file. Presets from `recapit.yaml` automatically merge with CLI flags.

### Common Patterns

```shell
export GEMINI_API_KEY="..."

# Inspect how an asset will be processed (no API calls)
recapit input/video.mp4 --dry-run
recapit https://example.com/report.pdf --dry-run --json

# Transcribe a deck with the “speed” preset, keeping raw responses, JSON exports, and LaTeX output
RECAPIT_SAVE_FULL_RESPONSE=1 recapit slides/deck.pdf \
  --preset speed \
  --format latex \
  --export json \
  --output-dir output/decks

# Transcribe a YouTube lecture, keeping intermediates for reuse and forcing low-res media hints
RECAPIT_SAVE_INTERMEDIATES=1 recapit "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --preset quality \
  --media-resolution low \
  --export srt vtt

# Post-processing helpers powered by the conversion utilities
# Convert legacy LaTeX transcripts to Markdown
recapit output/course-notes --to markdown --file-pattern "*.tex" --recursive
# Convert freshly-generated Markdown into JSON tables
recapit output/course-notes --to json --file-pattern "*.md" --skip-existing

# Review the cost of a prior run
recapit report cost output/course-notes/run-summary.json

# Periodically prune caches (dry-run by default)
recapit cleanup cache
recapit cleanup downloads --yes
```

`recapit transcribe` (and the shorthand `recapit <SOURCE>`) accept the standard `--kind`/`--pdf-mode` overrides, plus:

- `--preset <name>` to preload overrides from `recapit.yaml` (e.g., select models, exports, concurrency).
- `--format markdown|latex` to choose the primary transcript format (defaults to Markdown).
- `--export srt|vtt|markdown|json` to emit additional artifacts. Markdown is already the default output (the flag is retained for compatibility), and JSON exports use the new conversion pipeline under the hood.
- Save toggles (`save_full_response`, `save_intermediates`) follow precedence `CLI preset > config file > environment`. Set `RECAPIT_SAVE_FULL_RESPONSE=1` or `RECAPIT_SAVE_INTERMEDIATES=1` (or edit the preset) to turn them on for a run.
- `--media-resolution default|low|medium|high|unspecified` forwards Gemini media hints, matching preset/environment behaviour.

Every run writes:

- `<slug>/<slug>-transcribed.md|tex` – primary transcript (Markdown by default, LaTeX when you use `--format latex`).
- `run-summary.json` – totals, estimated spend, and a list of output artifacts.
- `run-events.ndjson` – per-request telemetry (one JSON object per API call).
- `chunks.json` – manifest for normalized video assets (video inputs only); manifests include hashes and chunk response paths so reruns with `--skip-existing` honor prior work.
- Optional `.srt`/`.vtt` subtitle files or `.json` exports when `--export` is provided.
- Optional `full-response/` artifacts and chunk intermediates when the corresponding save toggles are enabled.

Use `--hide-summary`, `--detailed-costs`, and `--summary-path` to adjust the console summary behaviour.

## Output Structure

Each source asset produces a slugified directory next to the input. For example, a `Lecture01.pdf` transcription now yields:
```
path/to/slides/
  lecture01/
    page-images/
      Lecture01-transcribed-0.png
      ...
    Lecture01-transcribed.md
```

Switching to `--format latex` replaces the primary artifact with `Lecture01-transcribed.tex` while keeping the same directory structure.

If `RECAPIT_SAVE_FULL_RESPONSE` (or its `LECTURE_SUMMARIZER_SAVE_FULL_RESPONSE` alias) is enabled, you'll also see `full-response/lecture01-transcribed.txt` alongside the cleaned transcript.

JSON (`*.json`) exports are written beside the primary transcript when you enable the export hooks.

Video inputs produce chunk-aware transcripts: with Markdown you get headings such as `## Chunk N (HH:MM:SS–HH:MM:SS)` inside `<stem>-transcribed.md`; with LaTeX the sections mirror the same structure inside `<stem>-transcribed.tex`. When the `save_full_response` toggle is enabled (via presets, `recapit.yaml`, or environment variables), every raw chunk response is also captured under `full-response/chunks/`. Intermediates such as normalized MP4s and chunk slices are discarded by default unless you enable `save_intermediates` (e.g., `RECAPIT_SAVE_INTERMEDIATES=1` or `LECTURE_SUMMARIZER_SAVE_INTERMEDIATES=1`). Concurrency is bounded by `max_video_workers` so you can align ffmpeg load with your hardware budget.


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
