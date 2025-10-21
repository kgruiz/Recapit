# Recapit

Recapit is a Rust-first rewrite of the original Python toolkit for turning slide decks, lectures, PDFs, and standalone images into structured summaries with Google Gemini. The binary ships with the same pipelines—YouTube ingestion, preset-aware defaults, telemetry, and quota management—but now bundles a single CLI that orchestrates normalization, chunk planning, uploads, and post-processing exports.

## Highlights

- **Unified pipelines** – one orchestration layer handles PDF-to-image fan out, optional direct PDF ingestion, Gemini calls, and LaTeX cleanup for slides, lectures, documents, and ad-hoc images.
- **Preset-aware defaults** – merge `recapit.yaml` presets, built-in profiles (`basic`, `speed`, `quality`), and CLI flags; every run records which overrides were active.
- **Configurable persistence** – flip `save_full_response`/`save_intermediates` to capture raw chunk text, aggregated transcripts, manifests, and normalized assets alongside finished exports.
- **Parallel + quota safe** – worker pools for normalization and Gemini uploads honor `max_workers`/`max_video_workers`, while shared token buckets respect published RPM/TPM limits and add jittered backoff for 429/5xx responses.
- **Telemetry & cost tracking** – every request logs retry metadata, quota sleeps, and usage numbers. CLI runs emit NDJSON event streams plus a roll-up JSON summary that `recapit report cost` can format as text or JSON.
- **Extensible exports** – `--export` dispatches Markdown, JSON, plaintext, and subtitle helpers (SRT/VTT) so post-processing stays declarative.
- **Drop-in CLI** – a single binary exposes planner previews, ingestion, reporting, cleanup, and conversion utilities for Markdown/JSON, mirroring the Python Typer commands.

## Requirements

- Rust 1.78+ (or a recent stable toolchain) and `cargo` for building the binary.
- Google Gemini access and a `GEMINI_API_KEY` with permissions for the `gemini-2.x` family.
- `ffmpeg` (video/audio normalization) and `poppler` (`pdftoppm` for PDF rasterization).
- Optional but recommended: `yt-dlp` for resilient YouTube downloads.

## Installation

Clone the repository and build the binary with Cargo:

```shell
# clone first
cd Recapit

# build or install locally
cargo build --release
# or
cargo install --path .
```

During development, run the CLI directly with `cargo run -- summarize …` while preserving the usual debug symbols.

## Configuration

Configuration is layered: **CLI flags > environment variables > `recapit.yaml`**. Generate a starter config with `recapit init` and tweak defaults (model, output directories, exports, presets, video settings).

### Core environment variables

| Setting | Description |
| --- | --- |
| `GEMINI_API_KEY` | Required. API key consumed by the CLI and telemetry recorder. |
| `RECAPIT_CONFIG` | Optional. Explicit path to a configuration file (defaults to `recapit.yaml` if present). |
| `RECAPIT_DEFAULT_MODEL` | Override the default transcription model (defaults to `gemini-2.5-flash-lite`). |
| `RECAPIT_OUTPUT_DIR` | Override the base output directory for summaries (defaults to `./output`). |
| `RECAPIT_TEMPLATES_DIR` | Point to an alternate prompt template directory. |
| `RECAPIT_SAVE_FULL_RESPONSE` | Persist aggregated transcripts under `full-response/<slug>.txt` and per-chunk responses when truthy. |
| `RECAPIT_SAVE_INTERMEDIATES` | Persist manifests, normalized MP4s, and chunk metadata under `intermediates/`. |
| `RECAPIT_MAX_WORKERS` | Cap concurrent normalization/upload workers (defaults to `4`). |
| `RECAPIT_MAX_VIDEO_WORKERS` | Cap concurrent video chunk workers (defaults to `3`). |

### Video & media tuning

| Setting | Description |
| --- | --- |
| `RECAPIT_VIDEO_TOKEN_LIMIT` | Override the per-video token budget (defaults to `300000`). |
| `RECAPIT_TOKENS_PER_SECOND` | Expected token rate for video chunks (defaults to `300`). |
| `RECAPIT_VIDEO_MAX_CHUNK_SECONDS` | Maximum chunk length in seconds (defaults to `7200`). |
| `RECAPIT_VIDEO_MAX_CHUNK_BYTES` | Maximum chunk size in bytes (defaults to `524288000`). |
| `RECAPIT_VIDEO_MEDIA_RESOLUTION` | Force media resolution hints (`default` or `low`). |
| `RECAPIT_VIDEO_ENCODER` | Force an FFmpeg encoder (`auto`, `cpu`, `nvenc`, `videotoolbox`, `qsv`, `amf`). |

Legacy variables prefixed with `LECTURE_SUMMARIZER_` remain supported for backwards compatibility.

## CLI overview

Export `GEMINI_API_KEY` before running the binary:

```shell
export GEMINI_API_KEY="sk-…"
```

### Primary workflows

```shell
# Preview ingestion and chunk planning (no Gemini calls)
recapit planner plan input/video.mp4 --json
recapit planner ingest syllabus/ --recursive

# Run the unified engine
recapit summarize lectures/week1 --preset quality --export srt,json
recapit summarize https://www.youtube.com/watch?v=abc123 --preset speed --export markdown
```

### Conversion utilities

```shell
# LaTeX ➜ Markdown or JSON using Gemini
recapit convert latex-to-md notes/**/*.tex --recursive --skip-existing
recapit convert latex-to-json tables.tex --output-dir output/json
```

### Reporting & maintenance

```shell
# Summaries and cost reporting
recapit report cost --summary run-summary.json --events run-events.ndjson

# Clean cached normalization artefacts or generated manifests
recapit cleanup caches --dry-run
recapit cleanup artifacts ./output/lectures
```

Each command prints contextual help (`--help`) and accepts `--config` to point at alternate YAML files.

### Export formats

Use `--export` (or preset-defined exports) to generate additional artefacts:

- `srt`, `vtt` – subtitle tracks for video/audio sources.
- `markdown`, `md` – aggregated transcript written beside the LaTeX output.
- `json` – structured summary containing the preamble, cleaned text, and chunk metadata.
- `text`, `txt` – plain-text transcript without LaTeX markup.

Exports are deduplicated automatically; unsupported formats emit `export.unsupported` telemetry events.

## Saved artefacts

Every run creates a slugified directory under the selected output root:

```
output/
  lecture01/
    lecture01-transcribed.tex
    run-summary.json
    run-events.ndjson
    full-response/
      lecture01-transcribed.txt        # only when save_full_response=true
      chunks/lecture01-chunk00.txt     # per-chunk when full responses are saved
    intermediates/
      normalized-assets.json           # when save_intermediates=true
      chunks.json
    lecture01-transcribed.srt          # when --export srt
    lecture01-transcribed.json         # when --export json
```

Key directories:

- `full-response/` – aggregated transcript plus per-chunk outputs (guarded by `save_full_response`).
- `intermediates/` – manifests and normalized assets for reruns/debugging (guarded by `save_intermediates`).
- `run-summary.json` – aggregate telemetry/costs for `recapit report cost`.
- `run-events.ndjson` – detailed per-request logs for auditing and retries.

## Migration from the Python CLI

| Capability | Rust CLI status | Notes |
| --- | --- | --- |
| Preset-aware defaults & config precedence | ✅ | Same `CLI > ENV > YAML` precedence; presets ship with `basic`, `speed`, `quality`. |
| YouTube ingestion with caching & fallbacks | ✅ | Requires `yt-dlp` and `ffmpeg`; manifests mirror the Python layout. |
| Planner previews & ingestion listings | ✅ | `recapit planner plan/ingest` replace the Typer `plan`/`planner` commands. |
| Markdown/JSON exports | ✅ | Use `--export markdown,json` or presets; subtitles remain available via `srt`/`vtt`. |
| Telemetry & manifest notes | ✅ | Retry/quota waits are recorded as monitor events; manifests track chunk status and URIs. |
| Cleanup & reporting utilities | ✅ | `recapit report cost` and `recapit cleanup caches|artifacts` mirror the Python helpers. |
| Python API | ❌ (not ported) | The Rust binary currently exposes only the CLI; integrate via command invocations.

The Rust toolchain eliminates the need for a managed Python environment—build once with `cargo` and distribute the binary. Dependency differences are primarily limited to system packages (`ffmpeg`, `poppler`, optional `yt-dlp`).

## Development

- Format with `cargo fmt` and test with `cargo test` (warnings are treated as regressions).
- Follow the workflow documented in [CONTRIBUTING.md](CONTRIBUTING.md).
- The project still vendors template files and prompt strategies under `templates/` and `src/prompts/`—customize them as needed.

## Roadmap Ideas

- Add resumable job metadata for long-running transcripts.
- Expose streaming progress events for upstream integrations.
- Ship optional Markdown/JSON schema validators.

## License

Released under the [GNU General Public License v3.0](LICENSE).
