## 0) Hotfixes to land first

**Fix capability gate**

```diff
# lecture_summarizer/pipeline.py
def _transcribe_pdf_with_progress(...):
-    if not self.llm.supports(model, "video"):
-        raise ValueError(f"Model {model} does not support video inputs")
+    # Capability checks are performed per feeding mode (pdf/image) below.

# later when using images:
+    if not self.llm.supports(model, "image"):
+        raise ValueError(f"Model {model} does not support image inputs")
# and when using PDFs:
# already checks: if strategy == PDFMode.PDF and not self.llm.supports(model, "pdf"): raise
```

**Fix default prompt strings**

Remove embedded quotes from all `DEFAULT_PROMPTS` in `templates.py` and the new `prompts/*`.

---

## 1) Goals

One engine for all inputs. Provider agnostic. Deterministic outputs. Strong telemetry. Resumable runs. Simple defaults. Expert controls.

---

## 2) Scope decisions

* Inputs: filesystem, http(s) URL, YouTube URL, Google Drive file.
* Provider: start with Gemini. Keep interface for future backends.
* Replace old pipeline and CLI.

> Notes on feasibility:
>
> * Gemini accepts three video input methods: Files API upload, inline base64 for small files, and YouTube URLs. Default frame sampling is 1 fps. Place the media part before text. ([Google AI for Developers][1])
> * Use Files API when total request size exceeds 20 MB. Files auto delete after 48 hours. ([Google AI for Developers][2]) ([Google AI for Developers][2])
> * Files API usage limits: 20 GB project storage and 2 GB per file. ([Google AI for Developers][2])
> * Batch input files up to 2 GB are supported by the Batch API.

---

## 3) New repository layout

```
lecture_summarizer/
  core/              # types, contracts, errors
  engine/            # orchestration
  ingest/            # discovery, normalization, caching, downloads
    sources/         # fs, http, youtube, drive
    video/           # ffmpeg wrapper, chunker, manifest
  prompts/           # strategies and registry
  providers/         # gemini adapter
  render/            # LaTeX, Markdown, JSON, SRT, VTT
  output/            # writers, telemetry, costs
  cli/               # Typer CLI
  templates/         # default and user overrides
  tests/
```

---

## 4) Core contracts

**`core/types.py`**

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

class Kind(str, Enum):
    SLIDES="slides"; LECTURE="lecture"; DOCUMENT="document"; IMAGE="image"; VIDEO="video"

class PdfMode(str, Enum):
    AUTO="auto"; IMAGES="images"; PDF="pdf"

class SourceType(str, Enum):
    FILE="file"; URL="url"; YOUTUBE="youtube"; DRIVE="drive"

@dataclass(frozen=True)
class Asset:
    path: Path | None            # local on-disk if available
    uri: str | None              # remote locator if applicable
    media: Literal["pdf","image","video","audio"]
    page_index: int | None = None

@dataclass(frozen=True)
class Job:
    source: str                  # path, http(s), youtube, drive file id or url
    recursive: bool
    kind: Kind | None
    pdf_mode: PdfMode
    output_dir: Path | None
    model: str
```

**`core/contracts.py`**

```python
from typing import Protocol
from pathlib import Path
from .types import Asset, Kind, PdfMode, SourceType

class SourceResolver(Protocol):
    def detect(self, source: str) -> SourceType: ...

class Ingestor(Protocol):
    def discover(self, source: str, recursive: bool) -> list[Asset]: ...

class Normalizer(Protocol):
    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]: ...

class PromptStrategy(Protocol):
    kind: Kind
    def preamble(self) -> str: ...
    def instruction(self, preamble: str) -> str: ...

class Provider(Protocol):
    def supports(self, capability: str) -> bool: ...
    def transcribe(self, *, instruction: str, assets: list[Asset], modality: str, meta: dict) -> str: ...

class Writer(Protocol):
    def write_latex(self, *, base: Path, name: str, preamble: str, body: str) -> Path: ...
```

---

## 5) Engine

**Responsibilities**

Discover. Normalize. Choose modality. Prompt. Call provider. Render. Write. Telemetry.

**`engine/engine.py`**

```python
from dataclasses import dataclass
from pathlib import Path
from ..core.types import Job, Kind, PdfMode, Asset
from ..core.contracts import SourceResolver, Ingestor, Normalizer, PromptStrategy, Provider, Writer
from ..output.telemetry import RunMonitor
from ..output.cost import CostEstimator

@dataclass
class Engine:
    resolver: SourceResolver
    ingestor: Ingestor
    normalizer: Normalizer
    prompts: dict[Kind, PromptStrategy]
    provider: Provider
    writer: Writer
    monitor: RunMonitor
    cost: CostEstimator

    def run(self, job: Job) -> Path | None:
        stype = self.resolver.detect(job.source)
        assets = self.ingestor.discover(job.source, job.recursive)
        if not assets:
            return None
        k = job.kind or self._infer_kind(job.source, assets)
        assets = self.normalizer.normalize(assets, job.pdf_mode)
        modality = self._modality_for(assets, job.pdf_mode)
        strat = self.prompts[k]
        pre = strat.preamble()
        instr = strat.instruction(pre)
        text = self.provider.transcribe(
            instruction=instr,
            assets=assets,
            modality=modality,
            meta={"kind": k.value, "source": job.source, "source_type": stype.value}
        )
        base = (job.output_dir or Path(job.source).parent if stype==stype.FILE else Path.cwd() / "output") / self._slug(Path(job.source).stem if stype==stype.FILE else self._slug(job.source))
        name = f"{self._slug(Path(job.source).stem if stype==stype.FILE else 'remote')}-transcribed"
        out = self.writer.write_latex(base=base, name=name, preamble=pre, body=text)
        self.monitor.flush_summary(to=base / "run-summary.json", cost=self.cost)
        return out

    def _infer_kind(self, source: str, assets: list[Asset]) -> Kind:
        from ..ingest.pdf import guess_kind_from_pdf
        p = Path(source)
        if p.suffix.lower()==".pdf":
            return Kind(guess_kind_from_pdf(p))
        return Kind.DOCUMENT

    def _modality_for(self, assets: list[Asset], pdf_mode: PdfMode) -> str:
        if assets and assets[0].media=="video":
            return "video"
        return "pdf" if pdf_mode==PdfMode.PDF else "image"

    @staticmethod
    def _slug(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "-" for c in s).strip("-")
```

---

## 6) Ingestion and normalization

### 6.1 Source resolver

**`ingest/sources/resolver.py`**

* Detect by scheme and domain.

  * `file://` or path → `FILE`.
  * `http(s)://` YouTube domains → `YOUTUBE`.
  * `http(s)://` others → `URL`.
  * `drive://<file-id>` or `https://drive.google.com/...` → `DRIVE`.
* Expose `resolve_to_local(asset)` to fetch remote to a cache when needed.

### 6.2 Discovery

**`ingest/discover.py`**

* Filesystem: recurse if asked. Recognize pdf, image, video, audio.
* URL:

  * For PDFs, images, audio, small videos ≤20 MB read into memory and emit `Asset(uri=url, path=None)`.
  * For larger or unknown size download to cache directory for Files API usage.
  * Respect `Content-Type` if present.
* YouTube:

  * Do not download by default. Emit `Asset(uri=youtube_url, media="video")`. Provider will pass `file_data.file_uri` directly. ([Google AI for Developers][1])
  * Optional `--youtube-download` to normalize locally via ffmpeg if user prefers.
* Drive:

  * Use Drive API `files.get` with `alt=media` for binary. Use `files.export` for Google Docs to PDF. Store bytes in cache. Then proceed as for URL or local. Scopes: `drive.readonly`.

### 6.3 PDF pipeline

**`ingest/pdf.py`**

* `rasterize_pdf_if_needed(assets, pdf_mode)`:

  * If `pdf_mode==PDF` and provider supports `pdf`, keep as PDF.
  * Else rasterize to `page-images/<stem>-N.png`.
* Keep `guess_kind_from_pdf`.

### 6.4 Video pipeline

**`ingest/video/ffmpeg.py`**

* Normalize to MP4 H.264 AAC with `-movflags +faststart`.
* Detect duration, fps, width, height, codecs. Compute hash.
* Hardware encoder selection based on config.

**`ingest/video/chunker.py`**

* Plan by seconds, byte size, and estimated tokens.
  Defaults:

  * `max_chunk_seconds` 7200.
  * `max_chunk_bytes` 524288000.
  * Token budgeting configured but calibrated at runtime with `count_tokens` if available. Do not hard code throughput.
* Emit ISO 8601 offsets.

**Manifest schema** `chunks.json`

```json
{
  "version": 1,
  "source": "path-or-uri",
  "source_hash": "sha256:...",
  "normalized": "path/to/normalized.mp4",
  "normalized_hash": "sha256:...",
  "duration_seconds": 1234.5,
  "size_bytes": 123456789,
  "fps": 29.97,
  "video_codec": "h264",
  "audio_codec": "aac",
  "encoder": {"requested": "auto","effective": "nvenc","accelerated": true},
  "model": "gemini-2.5-flash",
  "token_plan": {"limit": null, "estimator": "count_tokens|heuristic"},
  "chunks": [
    {
      "index": 0,
      "start_seconds": 0.0,
      "end_seconds": 3600.0,
      "start_iso": "PT0S",
      "end_iso": "PT1H",
      "path": "pickles/video-chunks/foo-chunk00.mp4",
      "transcript_path": "full-response/chunks/foo-chunk00.txt"
    }
  ]
}
```

**Resume semantics**

* If `source_hash` and `normalized_hash` match, reuse.
* If a chunk has `transcript_path` and `--skip-existing`, reuse its output.
* If hashes mismatch, rebuild artifacts and manifest.

---

## 7) Provider abstraction for Gemini

**`providers/gemini.py`**

* Map assets to request parts.

  * PDF path or blob → Files API or inline if ≤20 MB. ([Google AI for Developers][2])
  * Image paths → inline or Files API as needed.
  * Video:

    * YouTube: pass `{"file_data":{"file_uri": youtube_url}}`. Optional `videoMetadata` with `start_offset` and `end_offset` to clip. ([Google AI for Developers][1])
    * Local video chunks: upload with Files API, poll `files.get` until `ACTIVE`, then reference `file.uri`. ([Google AI for Developers][2])
* Always place the media part before instructions. ([Google AI for Developers][1])
* Use streaming for long text assembly when supported.
* Optional `response_schema` for structured timelines.

**Retries and backoff**

* On `FAILED_PRECONDITION` for file still processing, poll with backoff and jitter.
* On `RATE_LIMIT_EXCEEDED`, exponential backoff with cap.
* On size violation, switch to Files API and replan chunks.

---

## 8) Prompts and templates

* Move defaults into `prompts/*.py` with Jinja2-ready strings in `templates/`.
* New video prompts:

  * transcript only.
  * timeline summary with `[MM:SS]` stamps.
  * slides plus visual descriptions.
* System instruction follows media-first order.
* Allow `response_schema` for JSON timeline.

---

## 9) Chunk assembly and outputs

* Define merge order. Sort by `chunk.index`. Normalize time codes to `MM:SS`.
* Insert `\section*{Chunk N (HH:MM:SS–HH:MM:SS)}` in LaTeX.
* LaTeX cleanup handles `[Slide]` markers and math.

**Extra exports**

* SRT and VTT optional.
  If no utterance granularity, one block per chunk with chunk bounds.

**Output tree**

```
<out>/<slug>/
  page-images/
  pickles/video-chunks/
  full-response/
    combined.txt        # optional
    chunks/<name>-chunkNN.txt
  <name>.tex
  <name>.srt            # opt
  <name>.vtt            # opt
  run-summary.json
  chunks.json
```

---

## 10) Concurrency, quotas, cost

* Thread pool for per file. Separate pool for per video chunk.
* Shared token bucket across workers.
* Instrument each call with model, modality, timestamps, chunk id.
* Quota monitor tracks RPM and TPM per model.
* Cost estimator multiplies observed token usage by pricing table. Store per run totals.
* Preflight `count_tokens` where available to throttle before calls.

---

## 11) Configuration

**`lecture-summarizer.yaml`**

```yaml
model: gemini-2.5-flash
pdf_mode: auto
output_dir: ./output

workers:
  files: 4
  video_chunks: 3

sources:
  allow_url: true
  allow_youtube: true
  allow_drive: true
  cache_dir: ./.cache/ingest
  youtube_download: false         # if true, download then treat as local video

video:
  max_chunk_seconds: 7200
  max_chunk_bytes: 524288000
  encoder: auto                   # auto, cpu, nvenc, videotoolbox, qsv, amf
  clip: null                      # "MM:SS-MM:SS" applied for YouTube or local

save:
  full_response: false
  intermediates: true

pricing_file: ./pricing.yaml
templates_dir: ./templates
logging:
  level: info

drive:
  credentials_path: ~/.config/lecture_summarizer/google.json
  use_service_account: false
```

Precedence: CLI flags override YAML. Env only for API keys and OAuth secrets.

---

## 12) CLI

**Primary**

```
summarize SOURCE
  [--kind auto|slides|lecture|document|image|video]
  [--pdf-mode auto|images|pdf]
  [--recursive]
  [--model ...]
  [--output-dir ...]
  [--export srt|vtt]
  [--skip-existing/--no-skip-existing]
  [--clip "MM:SS-MM:SS"]                   # for youtube or local video
  [--allow-url/--no-allow-url]
  [--allow-youtube/--no-allow-youtube]
  [--allow-drive/--no-allow-drive]
  [--youtube-download/--no-youtube-download]
```

**Utility**

```
plan SOURCE --json
init
convert md PATH
convert json PATH
```

**SOURCE forms**

* Path or file://path
* http(s)://example.com/file.pdf
* [https://www.youtube.com/watch?v=ID](https://www.youtube.com/watch?v=ID)
* drive://<file-id> or a share URL

---

## 13) Rate limiting and robustness

* TokenBucket with condition variables for both sync and async flows.
* Apply preflight `count_tokens` on text prompts and small media to adjust concurrency.
* Throttle uploads to stay under concurrent Files API processing limits.
* Clear error messages on upload failure with remediation steps.

---

## 14) Telemetry

* `RunMonitor` writes `run-summary.json` with:

  * files processed and durations.
  * per call tokens and cost estimates.
  * RPM and TPM utilizations.
  * retry counts and causes.
* NDJSON event stream optional for dashboards.

---

## 15) Error handling matrix

* URL fetch failure → retry with backoff. Final error includes HTTP status and size seen.
* YouTube access blocked or private → instruct to download or provide a public link.
  Gemini supports public YouTube only. ([Google AI for Developers][1])
* Files API state `PROCESSING` for too long → exponential backoff up to max wait. Then fail with hint to split or reupload. ([Google AI for Developers][2])
* Files API size limits → switch to chunked local files. Limits per file 2 GB and per project 20 GB. ([Google AI for Developers][2])
* 48 hour retention → warn users that cached `file_uri` expires. Persist local copies. ([Google AI for Developers][2])

---

## 16) Security and privacy

* Never log API keys or OAuth tokens.
* Redact URLs that contain tokens.
* Drive scopes limited to read only. Cache expires on a schedule.
* Respect 48 hour Files API retention by not relying on remote URI beyond a run. ([Google AI for Developers][2])

---

## 17) Testing

**Unit**

* Resolver detects source type from path, URL, YouTube, Drive URL.
* PDF modality selection and rasterize logic.
* Video chunk math and ISO offsets.
* Manifest IO and resume.
* Prompt rendering.

**Integration**

* Two page PDF both pdf and images modes.
* One PNG.
* 30 second MP4 normalized and chunked.
* YouTube public URL with `--clip 00:40-01:20` using direct `file_uri`.
* URL PDF fetched and summarized.
* Drive binary file downloaded and summarized.
* Resume with `--skip-existing`.

**Fixtures**

* ≤200 KB PDF, ≤200 KB PNG, ≤2 MB MP4, one public YouTube id.

---

## 18) Documentation

* README quick start for local files, URLs, YouTube, Drive.
* How resumability works with manifest examples.
* Files API limits and retention section. Link to docs. ([Google AI for Developers][2])
* Video understanding usage and media first prompt order. ([Google AI for Developers][1])
* When to use inline base64 vs Files API. ([Google AI for Developers][2])

---

## 19) PR sequence

1. Hotfixes. Add smoke tests.
2. Core contracts, Engine, Writer. `plan` command.
3. FS ingest and PDF rasterization.
4. Gemini provider for PDF and images. Minimal `summarize`.
5. Video normalization, chunker, manifest. Provider(video).
6. URL ingest. Cache and size detection. Inline vs Files API switch.
7. YouTube ingest. Direct `file_uri`. Optional clip. Provider wiring.
8. Drive ingest. OAuth. Download or export. Cache.
9. Telemetry, pricing, run summary.
10. Exports SRT and VTT. Presets. `init`.
11. Test matrix and docs. Release notes.

Each PR must run lint, unit, and integration fixtures and must write `run-summary.json`.

---

## 20) Acceptance criteria

* One `summarize` command handles pdf, image, video, URL, YouTube, Drive.
* Video runs resumable per chunk.
* `run-summary.json` and `chunks.json` always written.
* LaTeX outputs compile with defaults.
* `--pdf-mode auto` prefers pdf only when provider supports it.
* Costs recorded even when estimated.
* `--skip-existing` reuses per chunk transcripts.
* YouTube works without download when public. ([Google AI for Developers][1])

---

## 21) Items dropped

* Legacy pipeline and CLI.
* Back compat shims.
* New env toggles for non secrets. Use YAML.

---

## 22) Implementation notes for the three new sources

**URL**

* Small file ≤20 MB → inline base64. Use `inline_data` in a single request. ([Google AI for Developers][2])
* Larger → download to cache then Files API upload and reference `file.uri`. ([Google AI for Developers][2])
* Content sniffing with `Content-Type` first, magic bytes fallback.

**YouTube**

* Preferred path: do not download. Use `file_data.file_uri` with the YouTube URL. Add `videoMetadata` `start_offset` and `end_offset` if `--clip` was set. ([Google AI for Developers][1])
* Fallback: `--youtube-download` for private or rate limited cases. Treat as local video.

**Drive**

* Accept `drive://<file-id>` or share URL. If Google Docs or Slides use `files.export` to PDF. If binary use `files.get?alt=media`.
* Store to cache, then follow URL path rules.

---

## 23) Example provider calls

**YouTube clip**

```python
parts = [
  {"file_data": {"file_uri": youtube_url}},
  {"text": prompt},
]
# Optionally add:
# parts[0]["video_metadata"] = {"start_offset": "40s", "end_offset": "80s"}
# Then call models.generate_content with media first.
```

**Files API video**

```python
video_file = client.files.upload(file=normalized_path)
file = client.files.get(name=video_file.name)  # poll until ACTIVE
parts = [{"file_data": {"file_uri": file.uri}}, {"text": prompt}]
```

References: media first, three ingestion methods including YouTube URLs, and Files API lifecycle. ([Google AI for Developers][1]) ([Google AI for Developers][2])

---

## 24) Cost model source of truth

* Keep `pricing.yaml` under version control.
* Update with model names and per token rates.
* Store per request usage and totals in `run-summary.json`.
* Do not embed hard token throughput for video. Calibrate with `count_tokens` and measured usage.
