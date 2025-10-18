## Reference summary (constraints you must honor)

* **Video inputs supported three ways:** Files API upload, inline base64 ≤ 20 MB, or direct **YouTube URL** ingestion (preview; public videos only). Video is sampled at ~1 fps; `videoMetadata` supports `start_offset`, `end_offset`, and `fps`. Throughput ≈ 300 tokens/sec at default media resolution or ≈ 100 tokens/sec in low resolution. Place media **before** instructions in `contents`. ([Google AI for Developers][1])
* **Files API lifecycle:** resumable upload; states `PROCESSING` → `ACTIVE`. Size ≤ 2 GB per file, storage cap ~20 GB, automatic **48‑hour** retention; keep your own local copy. Inline uploads are best only when total request size ≤ 20 MB. ([Google AI for Developers][2])
* **Document and image ingestion:** PDFs and images can be passed inline or via Files API; you may also fetch bytes from a URL and send inline. ([Google AI for Developers][3])
* **URL ingestion (web pages and some hosted media):** the API exposes URL retrieval metadata and supports URL parts via the tools system; prefer **Files API** or client‑side fetch for large media to control chunking. ([Google AI for Developers][4])

---

## 0) Hotfixes to land first (explicit code, no external diffs)

**0.1 Fix capability gating in legacy pipeline to stop false video errors while you migrate**

```python
# lecture_summarizer/pipeline.py
from typing import Iterable

class Pipeline:

    def _ensure_capability(self, model: str, need: str) -> None:
        # self.llm.supports(model, capability: Literal["pdf","image","video","audio"])
        if not self.llm.supports(model, need):
            raise ValueError(f"Model '{model}' does not support {need} inputs")

    def _transcribe_pdf_with_progress(self, *, model: str, pdf_mode: str, pages: Iterable, images: Iterable, **kw):
        # Capability checks per feeding mode
        if pdf_mode == "pdf":
            self._ensure_capability(model, "pdf")
            return self._transcribe_pdf_native(model=model, pages=pages, **kw)
        elif pdf_mode == "images":
            self._ensure_capability(model, "image")
            return self._transcribe_pdf_as_images(model=model, images=images, **kw)
        else:
            # auto: prefer native PDF if supported, otherwise images
            if self.llm.supports(model, "pdf"):
                return self._transcribe_pdf_native(model=model, pages=pages, **kw)
            self._ensure_capability(model, "image")
            return self._transcribe_pdf_as_images(model=model, images=images, **kw)
```

**0.2 Fix stray quotes in default prompts**

```python
# lecture_summarizer/templates.py  (temporary until new templates land)
DEFAULT_PROMPTS = {
    "slides": "Summarize slide content. Keep math as LaTeX.",
    "lecture": "Summarize the lecture with [MM:SS] timestamps. Include visual cues.",
    "document": "Summarize the document. Preserve headings. Extract key equations.",
    "image": "Describe the image with technical details. Convert text to LaTeX if math.",
    "video": "Transcribe audio and summarize visuals with [MM:SS] timestamps."
}
```

Ship a tiny test to assert prompts contain no `"` characters and no trailing whitespace.

---

## 1) Goals

* One engine for all inputs. Local files, URLs, YouTube, and Google Drive.
* Provider‑agnostic. Gemini first; clean adapter for others.
* Simple defaults. Expert controls for chunking, costs, and quotas.
* Deterministic, structured outputs. Strong telemetry. Resumable at **chunk** level.

---

## 2) Scope and ingestion matrix

* **File inputs:** pdf, images, video/audio.
* **URLs:** http(s) to PDF/image/audio/video; fetch bytes client‑side when >20 MB or when chunking needed; else inline.
* **YouTube:** either pass **YouTube URL** directly to Gemini for short tasks or download with `yt-dlp` for long videos and fine chunk control. ([Google AI for Developers][1])
* **Drive:** read via Google Drive API, stream to disk, then treat as local file.
* Keep Gemini provider; abstract via `Provider` interface.

---

## 3) Repository layout

```
lecture_summarizer/
  core/              # types + contracts + errors
  engine/            # orchestration + execution
  ingest/            # file/URL/YouTube/Drive discovery + normalization + caching
  prompts/           # per-kind strategies + registry
  providers/         # LLM backends (gemini first)
  render/            # LaTeX/MD/JSON postprocessing
  output/            # writers + telemetry + costs
  cli/               # Typer CLI
  templates/         # user-overrides (text/Jinja2)
  tests/
```

---

## 4) Core contracts (exact types)

```python
# core/types.py
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TypedDict

class Kind(str, Enum):
    SLIDES="slides"; LECTURE="lecture"; DOCUMENT="document"; IMAGE="image"; VIDEO="video"

class PdfMode(str, Enum):
    AUTO="auto"; IMAGES="images"; PDF="pdf"

class SourceKind(str, Enum):
    LOCAL="local"; URL="url"; YOUTUBE="youtube"; DRIVE="drive"

@dataclass(frozen=True)
class Asset:
    path: Path
    media: Literal["pdf","image","video","audio"]
    page_index: int | None = None    # for rasterized PDFs
    source_kind: SourceKind = SourceKind.LOCAL
    mime: str | None = None
    meta: dict = None                # e.g., {"start_seconds":0.0,"end_seconds":60.0,"fps":1}

@dataclass(frozen=True)
class Job:
    source: str                      # PATH or URL or drive://FILE_ID or yt://<id>|https://youtu...
    recursive: bool
    kind: Kind | None
    pdf_mode: PdfMode
    output_dir: Path | None
    model: str
    preset: str | None = None
    export: list[str] | None = None  # ["srt","vtt"]
    skip_existing: bool = True
```

```python
# core/contracts.py
from typing import Protocol
from pathlib import Path
from .types import Asset, Kind, PdfMode, Job

class Ingestor(Protocol):
    def discover(self, job: Job) -> list[Asset]: ...

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

## 5) Engine orchestration

```python
# engine/engine.py
from dataclasses import dataclass
from pathlib import Path
from ..core.types import Job, Kind, PdfMode, Asset
from ..core.contracts import Ingestor, Normalizer, PromptStrategy, Provider, Writer
from ..output.telemetry import RunMonitor
from ..output.cost import CostEstimator

@dataclass
class Engine:
    ingestor: Ingestor
    normalizer: Normalizer
    prompts: dict[Kind, PromptStrategy]
    provider: Provider
    writer: Writer
    monitor: RunMonitor
    cost: CostEstimator

    def run(self, job: Job) -> Path | None:
        assets = self.ingestor.discover(job)
        if not assets:
            self.monitor.note_event("discover.empty", {"source": job.source})
            return None

        k = job.kind or self._infer_kind(assets)
        assets = self.normalizer.normalize(assets, job.pdf_mode)
        modality = self._modality_for(assets, job.pdf_mode)
        strat = self.prompts[k]; pre = strat.preamble(); instr = strat.instruction(pre)

        text = self.provider.transcribe(
            instruction=instr, assets=assets, modality=modality,
            meta={"kind": k.value, "source": job.source}
        )

        base = (job.output_dir or Path(".") / "output") / self._slug(Path(job.source).stem if "://" not in job.source else "remote")
        name = f"{self._slug(Path(job.source).stem)}-transcribed"
        out = self.writer.write_latex(base=base, name=name, preamble=pre, body=text)

        self.monitor.flush_summary(to=base / "run-summary.json", cost=self.cost)
        return out

    def _infer_kind(self, assets: list[Asset]) -> Kind:
        # Basic heuristic; refined in PDF module if needed
        if assets and assets[0].media == "video":
            return Kind.LECTURE
        if assets and assets[0].media == "image":
            return Kind.SLIDES
        return Kind.DOCUMENT

    def _modality_for(self, assets: list[Asset], pdf_mode: PdfMode) -> str:
        if assets and assets[0].media in ("video","audio"): return "video"
        return "pdf" if pdf_mode == PdfMode.PDF else "image"

    @staticmethod
    def _slug(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "-" for c in s).strip("-")
```

---

## 6) Ingestion and normalization (local, URL, YouTube, Drive)

**6.1 Discovery (`ingest/discover.py`)**

* Accept `job.source` as:

  * Local path or directory.
  * `http(s)://…` URL.
  * `yt://<id>` or full YouTube URL.
  * `drive://<FILE_ID>` or `gdrive://<FILE_ID>`.
* Return list of `Asset` with `source_kind` set. For dirs, recurse if `job.recursive`.

**6.2 URL ingestion (`ingest/url.py`)**

* For **PDF/image/audio/video** URLs:

  * If `Content-Length` ≤ 20 MB, fetch bytes, return `Asset` with temp file and `media` inferred, for possible **inline** send. ([Google AI for Developers][5])
  * If > 20 MB or unknown length: stream to disk, return local `Asset`. Prefer Files API upload path. ([Google AI for Developers][2])
* Optional: support **URL Context** tool for web pages when you want the model to fetch directly; not for long media. Store `meta={"url_context": True}` if selected. ([Google AI for Developers][4])

**6.3 YouTube ingestion (`ingest/youtube.py`)**

* Two modes:

  1. **Direct to Gemini**: emit virtual `Asset` with `media="video"`, `source_kind=YOUTUBE`, `meta={"pass_through": True}`. Provider will send a `file_data.file_uri` with the YouTube URL and optional `videoMetadata` clip. Best for short analyses or clips. ([Google AI for Developers][1])
  2. **Download**: use `yt-dlp` to get MP4 (H.264/AAC), then normalize and chunk locally for long videos, resumability, or precise cost control.

**6.4 Drive ingestion (`ingest/drive.py`)**

* Use Google Drive API:

  * Resolve file by ID or by path via search.
  * Download with `files.get(fileId, alt="media")` streaming to a temp file.
  * Emit local `Asset` with inferred `media`.
* Authentication via OAuth or service account; store no refresh tokens in repo.

**6.5 Normalization**

* **PDF (`ingest/pdf.py`)**

  * If `pdf_mode=="pdf"` and provider supports native PDF, pass through; else rasterize to PNGs under `<out>/<slug>/page-images/<stem>-p{N}.png`.
  * Guess `Kind` from PDF outline or aspect ratio if `job.kind` is None.

* **Images (`ingest/image.py`)**

  * Validate and keep original; optional downscale to ≤3072 px on long edge.

* **Video/Audio (`ingest/video.py`)**

  * Always normalize to MP4 H.264 + AAC with `+faststart`.

    * Example ffmpeg:

      ```
      ffmpeg -y -i INPUT -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
             -c:a aac -b:a 128k -movflags +faststart -map 0:v:0? -map 0:a:0? OUTPUT.mp4
      ```
  * Probe duration, fps. Default Gemini sampling is ~1 fps; keep originals for archival. ([Google AI for Developers][1])
  * **Chunk planning**:

    * Defaults: `max_seconds=7200`, `max_bytes=524288000`, `token_limit=300_000`, `tokens_per_second=300` (use 100 if `mediaResolution=LOW`). ([Google AI for Developers][1])
    * Compute `max_by_tokens = floor(token_limit / tokens_per_second)`.
    * Chunk length = `min(max_seconds, max_by_tokens, by_size_estimate)`.
  * Emit a **manifest** and per‑chunk MP4s under `pickles/video-chunks/`.

**6.6 Manifest schema (resumable)**

```json
{
  "version": 1,
  "source": "path/or/url",
  "source_hash": "sha256:...",          "source_kind": "local|url|youtube|drive",
  "normalized": "path/to/normalized.mp4",
  "normalized_hash": "sha256:...",
  "duration_seconds": 1234.5,
  "size_bytes": 123456789,
  "fps": 29.97,
  "model": "gemini-2.5-flash",
  "token_limit": 300000,
  "tokens_per_second": 300.0,
  "video_metadata_defaults": {"fps": 1},
  "chunks": [
    {
      "index": 0,
      "start_seconds": 0.0,
      "end_seconds": 3600.0,
      "start_iso": "PT0S",
      "end_iso": "PT1H0M0S",
      "path": "pickles/video-chunks/foo-000.mp4",
      "status": "pending|done|failed",
      "response_path": "full-response/chunks/foo-000.txt"
    }
  ],
  "created_utc": "2025-10-15T12:34:56Z"
}
```

**Resume rules**

* If `source_hash` and `normalized_hash` match, reuse normalization and chunks.
* If `response_path` exists and `--skip-existing`, skip re‑transcription.
* If mismatch, purge artifacts and rebuild.

---

## 7) Prompt strategies and templates

* Move defaults to `prompts/*.py`; user‑overrides in `templates/`.
* Ship text templates:

  * `video-preamble.txt`, `video-instruction.txt`
  * `slide/lecture/document/image` equivalents.
* **Video instruction baseline** (pseudocode text):

  ```
  Task: Produce a transcript with [MM:SS] timestamps and a timeline of salient visual events.
  Include: visual descriptions, slide titles, equations in LaTeX.
  Output: Markdown. Headings: "Transcript", "Timeline", "Key Terms".
  ```
* System instruction: “Media first” in `contents` ordering. ([Google AI for Developers][1])

---

## 8) Gemini provider adapter

```python
# providers/gemini.py
from google import genai
from google.genai import types

class GeminiProvider:
    def __init__(self, api_key: str, model: str, **opts):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.opts = opts

    def supports(self, capability: str) -> bool:
        # minimal static map; extend as needed
        caps = {
            "pdf": True, "image": True, "video": True, "audio": True
        }
        return caps.get(capability, False)

    def _file_part(self, uri: str, mime: str):
        return types.Part(file_data=types.FileData(file_uri=uri, mime_type=mime))

    def _inline_part(self, bytes_: bytes, mime: str):
        return types.Part(inline_data=types.Blob(data=bytes_, mime_type=mime))

    def transcribe(self, *, instruction: str, assets, modality: str, meta: dict) -> str:
        # Build 'parts' with media first
        parts = []
        # Strategy per source_kind
        for a in assets:
            if a.source_kind.name == "YOUTUBE" and a.meta.get("pass_through"):
                # pass YouTube URL directly
                parts.append(types.Part(file_data=types.FileData(file_uri=a.path.as_posix())))
            elif a.meta and a.meta.get("file_uri"):
                parts.append(self._file_part(a.meta["file_uri"], a.mime or "video/mp4"))
            elif a.meta and a.meta.get("inline_bytes"):
                parts.append(self._inline_part(a.meta["inline_bytes"], a.mime or "video/mp4"))
            else:
                # Upload via Files API and wait for ACTIVE
                f = self.client.files.upload(file=str(a.path))
                while f.state.name == "PROCESSING":
                    f = self.client.files.get(name=f.name)
                if f.state.name != "ACTIVE":
                    raise RuntimeError(f"File failed: {f.state.name}")
                parts.append(self._file_part(f.uri, f.mime_type))

            # Apply optional clip/FPS
            if a.meta and any(k in a.meta for k in ("start_offset","end_offset","fps")):
                parts[-1].video_metadata = types.VideoMetadata(
                    start_offset=a.meta.get("start_offset"),
                    end_offset=a.meta.get("end_offset"),
                    fps=a.meta.get("fps"),
                )

        parts.append(types.Part(text=instruction))

        cfg = types.GenerateContentConfig(
            # set thinking budgets as needed
            # response schemas configurable by strategy
        )
        resp = self.client.models.generate_content(
            model=self.model, contents=types.Content(parts=parts), config=cfg
        )
        return resp.text or ""
```

* For **chunked video**, call once per chunk, write raw text per chunk, then combine.
* Use `file_data.file_uri` for **YouTube** when not downloading. ([Google AI for Developers][1])
* Respect Files API **processing** states before using a file; expect 48‑hour retention. ([Google AI for Developers][2])

---

## 9) Concurrency, rate limits, and backpressure

**Token bucket (thread‑safe, async‑friendly)**

```python
# output/limits.py
import time, threading

class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: int):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_per_sec = refill_per_sec
        self.lock = threading.Lock()
        self.last = time.monotonic()

    def acquire(self, n: int = 1):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
            self.last = now
            if self.tokens < n:
                sleep_s = (n - self.tokens) / self.refill_per_sec
                time.sleep(max(0, sleep_s))
                self.tokens = n
            self.tokens -= n
```

* Maintain RPM and TPM per model from config. Sleep before calls to avoid `429`.
* For video, throttle by **estimated tokens** = `tokens_per_second * duration` to pre‑budget the bucket. Use 300 t/s default, 100 t/s if `mediaResolution=LOW`. Calibrate with `models.count_tokens` when needed. ([Google AI for Developers][1])
* Exponential backoff on `429` and `FAILED_PRECONDITION` (file still processing). Files API states must be `ACTIVE` before use. ([Google AI for Developers][2])

---

## 10) Cost tracking

* Load pricing from `pricing.yaml` with fallbacks.
* For each request capture:

  * timestamps, model, modality, chunk index, tokens in/out when provided, or duration‑based estimate for media.
* Estimated cost = `input_tokens * price_in + output_tokens * price_out` (thinking tokens included per model’s pricing tier from your pricing file).

---

## 11) Chunk assembly and outputs

**LaTeX writer (video aware)**

```python
# render/writer.py
from pathlib import Path

class LatexWriter:
    def write_latex(self, *, base: Path, name: str, preamble: str, body: str) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        tex = base / f"{name}.tex"
        with tex.open("w", encoding="utf-8") as f:
            f.write("\\documentclass{article}\n")
            f.write("\\usepackage{hyperref,amsmath}\n")
            f.write("\\begin{document}\n")
            f.write(preamble + "\n\n")
            f.write(body + "\n")
            f.write("\\end{document}\n")
        return tex
```

**Video merge rules**

* For each chunk `i`, write:

  ```
  \section*{Chunk i (HH:MM:SS–HH:MM:SS)}
  <chunk text>
  ```
* Normalize timestamps to `MM:SS`. Simple helper:

```python
def to_mmss(seconds: float) -> str:
    m = int(seconds // 60); s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"
```

**SRT/VTT generation (optional)**

* If the chunk response lacks utterance timing, emit one block per chunk window with the chunk text.

---

## 12) Output tree

```
<out>/<slug>/
  page-images/                # rasterized PDFs
  pickles/video-chunks/       # chunk mp4s
  full-response/
    combined.txt              # optional combined raw
    chunks/<name>-chunkNN.txt
  <name>.tex
  <name>.srt (opt)
  <name>.vtt (opt)
  run-summary.json
  chunks.json                 # manifest
```

---

## 13) Configuration

**`lecture-summarizer.yaml`**

```yaml
model: gemini-2.5-flash
pdf_mode: auto
output_dir: ./output

workers:
  files: 4
  video_chunks: 3

video:
  token_limit: 300000         # per request budget
  tokens_per_second: 300      # 100 if mediaResolution=LOW
  max_chunk_seconds: 7200
  max_chunk_bytes: 524288000
  encoder: auto               # auto|cpu|nvenc|videotoolbox|qsv|amf
  use_youtube_passthrough: true
  media_resolution: default   # default|low

ingest:
  allow_url: true
  allow_drive: true
  allow_youtube: true

save:
  full_response: false
  intermediates: true

pricing_file: ./pricing.yaml
templates_dir: ./templates

logging:
  level: info
```

Precedence: CLI > env (only API key) > YAML.

---

## 14) CLI (Typer)

* `summarize SOURCE [--kind auto|slides|lecture|document|image|video] [--pdf-mode auto|images|pdf] [--recursive] [--model ...] [--output-dir ...] [--export srt|vtt] [--skip-existing/--no-skip-existing] [--preset basic|speed|quality] [--config PATH] [--media-resolution default|low]`

  * `SOURCE` can be:

    * Path or directory
    * `https://...` URL
    * `yt://<id>` or full YouTube URL
    * `drive://<FILE_ID>`
* `plan SOURCE --json` → prints discovered assets, chosen modality, planned chunks.
* `init` → writes sample YAML and templates.
* `convert md PATH` | `convert json PATH` → post‑processing exporters.

Examples:

```
lecture-summarizer summarize yt://9hE5-98ZeCg --kind lecture --export srt
lecture-summarizer summarize https://site/file.pdf --pdf-mode auto
lecture-summarizer summarize drive://1AbCDefGhIJk --output-dir out
```

---

## 15) Error handling

* **CapabilityError:** clear message with required modality.
* **Files API failure:** show `state`, advise retry or re‑upload; remind 2 GB limit and 48‑hour retention. ([Google AI for Developers][2])
* **Oversize inline:** suggest Files API instead of base64 when > 20 MB. ([Google AI for Developers][1])
* **429 / quota:** exponential backoff; log RPM/TPM and next attempt ETA.
* **YouTube private/unavailable:** fall back to `yt-dlp` download path and local chunking.

---

## 16) Telemetry schema

**`run-summary.json`** example:

```json
{
  "job": {"source":"yt://9hE5-98ZeCg","kind":"lecture","model":"gemini-2.5-flash"},
  "totals": {"requests": 4, "input_tokens": 280000, "output_tokens": 6200, "est_cost_usd": 0.93},
  "time": {"start":"...Z","end":"...Z","elapsed_sec": 412.3},
  "limits": {"rpm": 1000, "tpm": 1000000},
  "files": [".../chunks.json",".../combined.txt",".../name.tex"],
  "warnings": []
}
```

Emit NDJSON per request with `{model, modality, chunk_index, start_utc, end_utc, latency_ms, tokens_in, tokens_out, video_start, video_end, file_uri}`.

---

## 17) Testing

**Unit**

* Kind inference; modality selection; slug; manifest IO; chunk math; token‑rate planner; prompt rendering; timestamp utils; URL and Drive detection.

**Integration**

* Small PDF (≤200 KB) → `.tex` + images in `images` mode.
* Single PNG → `.tex`.
* 20–30 s MP4 → normalization, chunking, manifest, `.tex`, and raw chunks.
* YouTube passthrough (public short video) → single call path. ([Google AI for Developers][1])
* URL PDF fetch inline (<20 MB) and Files API path (>20 MB). ([Google AI for Developers][3])
* Resume with `--skip-existing` and verify reuse.

**Fixtures**

* Tiny assets with stable content. Golden files for `.tex` headers and chunk headings.

---

## 18) Documentation

* README:

  * Install, auth for Gemini and Drive, ffmpeg requirement.
  * How **YouTube passthrough** works and when to download. ([Google AI for Developers][1])
  * Files API constraints: 2 GB/file, 20 GB storage, 48 h retention. ([Google AI for Developers][2])
  * Inline ≤ 20 MB guidance and media‑first ordering. ([Google AI for Developers][1])
* CLI `--help` examples.
* “Resumability and manifests” page with diagram.

---

## 19) PR sequence (mergeable)

1. [x] Hotfixes (capability gate + prompt strings) + smoke tests.
2. [x] Core contracts + Engine + LaTeX Writer + `plan` command.
3. [x] Ingest: local discovery + URL fetch; PDF rasterization + image passthrough.
4. [x] Provider: Gemini wrapper; wire PDF/image; minimal CLI `summarize`.
5. [x] Video normalization + manifest + chunking; YouTube passthrough; Drive download.
6. [x] Telemetry + pricing YAML + run summary.
7. [x] Exports (SRT/VTT) + presets + `init`.
8. [x] Tests + docs + release notes.

---

## 20) Acceptance criteria

* One `summarize` handles file/URL/YouTube/Drive.
* Video runs resumable at **chunk** granularity.
* `run-summary.json` and `chunks.json` always written.
* LaTeX compiles by default.
* `--pdf-mode auto` uses native PDF when supported, else images.
* Costs reported even when video uses duration‑based estimates.
* `--skip-existing` reuses per‑chunk transcripts.

---

## 21) Items deliberately out of scope

* Non‑YouTube streaming platforms without direct download support.
* Full web crawling. Limit to explicit URLs.
* Persistent caches beyond 48 h Files API retention; keep local artifacts. ([Google AI for Developers][2])

---

## 22) Security and privacy

* Do not embed API keys in logs.
* Strip URLs, file IDs, and personally identifying data from telemetry unless `--debug`.
* Respect 48‑hour Files API retention. Delete files early if not needed. ([Google AI for Developers][2])

---

## 23) Implementation details to copy‑paste

**23.1 CLI source parsing**

```python
from urllib.parse import urlparse

def parse_source(s: str):
    if s.startswith(("yt://","https://www.youtube.com/","https://youtu.be/")):
        return ("youtube", s)
    if s.startswith(("drive://","gdrive://")):
        return ("drive", s.split("://",1)[1])
    u = urlparse(s)
    if u.scheme in ("http","https"):
        return ("url", s)
    return ("local", s)
```

**23.2 YouTube passthrough asset**

```python
from pathlib import Path
from core.types import Asset, SourceKind

def youtube_passthrough(url: str) -> Asset:
    return Asset(
        path=Path(url), media="video",
        source_kind=SourceKind.YOUTUBE,
        mime="video/*",
        meta={"pass_through": True}
    )
```

**23.3 Apply clip windows to `videoMetadata`** (ISO‑8601 or seconds strings like `"1250s"`). ([Google AI for Developers][1])

```python
def sec_to_iso(s: float) -> str:
    # e.g., "PT20S" for 20 seconds
    return f"PT{int(s)}S"
```

**23.4 Count‑tokens preflight (optional throttle)**

```python
def estimate_media_tokens(seconds: float, tps: int = 300) -> int:
    return int(seconds * tps)
```

---

## 24) Risks and mitigations

* **YouTube preview behavior changes:** keep local download path as fallback. ([Google AI for Developers][1])
* **Files API storage exhaustion (20 GB):** purge normalized and chunk files after merge; configurable retention. ([Google AI for Developers][2])
* **Inline oversize (>20 MB):** always check `Content-Length` and choose Files API. ([Google AI for Developers][1])
* **Token cost drift:** enable `models.count_tokens` sampling on one chunk per run; adjust `tokens_per_second`.

---

### Notes

* Video understanding best practice: media first, explicit request for transcript + visuals, optional higher `fps` for fast visuals. ([Google AI for Developers][1])
* For images/PDFs from URLs, prefer client‑side fetch to bytes then inline or Files upload. ([Google AI for Developers][3])

This plan is self‑contained. It includes URL, YouTube, and Drive ingestion, Files API rules, chunking, quotas, and outputs, aligned with current Gemini docs.

[1]: https://ai.google.dev/gemini-api/docs/video-understanding?hl=fr "Compréhension des vidéos  |  Gemini API  |  Google AI for Developers"
[2]: https://ai.google.dev/gemini-api/docs/files?hl=fr "API Files  |  Gemini API  |  Google AI for Developers"
[3]: https://ai.google.dev/gemini-api/docs/document-processing?utm_source=chatgpt.com "Document understanding | Gemini API"
[4]: https://ai.google.dev/api/generate-content?hl=fr "Generating content  |  Gemini API  |  Google AI for Developers"
[5]: https://ai.google.dev/gemini-api/docs/image-understanding?utm_source=chatgpt.com "Image understanding | Gemini API | Google AI for Developers"
