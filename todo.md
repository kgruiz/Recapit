## Outstanding Parity Tasks

- [ ] **YouTube ingestion parity**
  - Re-create the Python `YouTubeDownloader` flow: invoke yt‑dlp, cache MP4 outputs, persist metadata (id, duration, size hash), and fall back gracefully when downloads fail.
  - Normalize downloaded videos (ffmpeg faststart, diagnostics) before chunk planning, updating manifests with actual file paths and “downloaded” flags.
  - Surface user-facing warnings when yt‑dlp/FFmpeg are missing, matching the Python CLI messaging.

- [ ] **Preset-aware CLI defaults**
  - Port the `_merge_presets` logic and `--preset` flag so `summarize` inherits preset-specific overrides (kind/pdf_mode/exports/media resolution) prior to job creation.
  - Support preset-defined `model`, `recursive`, and export expansions exactly as the Typer command does, including custom presets from `recapit.yaml`.
  - Update help text to list preset names and indicate which fields they override.

- [ ] **Resilient Gemini retries**
  - Implement exponential backoff with jitter for 429/5xx responses, reusing the Python delay caps and logging.
  - Respect quota sleeps + backoff in both the provider and conversion utilities; capture retry counts in telemetry notes.
  - Detect transient Files API states (PROCESSING, INTERNAL) and retry uploads with the same guardrails as `LLMClient._await_active_file`.

- [ ] **Telemetry & manifest polish**
  - Update manifest entries with response file URIs/status transitions after each chunk, mirroring `_transcribe_chunks` behavior.
  - Record run-monitor “note” events for skips, retries, quota sleeps, and manifest warnings.
  - Emit Files API cleanup hooks (delete temporary uploads where Python does) and include them in run summaries.

- [ ] **Ancillary CLI utilities**
  - Port Typer commands: `init`, `planner plan`, `planner ingest`, `report cost`, cleanup commands, and any markdown/json post-process helpers.
  - Ensure cost/report commands read the new telemetry outputs and format results identically (including colorized terminal output).
  - Wire command aliases/help descriptions to match the existing docs.

- [ ] **Config toggles & exports**
  - Honor `save_full_response`/`save_intermediates` by persisting raw model outputs & intermediates under the configured directories.
  - Respect `max_workers`/`max_video_workers` by introducing thread pool limits for normalization/upload tasks, matching Python concurrency semantics.
  - Recreate export pipeline hooks (Markdown/JSON post-processing, subtitles) so exports declared in config/presets dispatch appropriately.

- [ ] **Documentation refresh**
  - Update README and CLI usage guides to reflect the Rust commands, environment variables, conversion utilities, and quota requirements.
  - Provide migration notes for users switching from the Python CLI (feature parity matrix, outstanding gaps, dependency differences).
  - Add examples for `recapit convert`, preset usage, and YouTube workflows once the remaining tasks above land.

Below is a Rust scaffold that mirrors your Python architecture and renders live progress with `ratatui`. It keeps FFmpeg/Poppler CLI behavior, preserves the provider/engine/prompts split, and leaves the Gemini HTTP calls in a single module you can flesh out.

---

### `Cargo.toml`

```toml
[package]
name = "recapit"
version = "0.1.0"
edition = "2021"

[dependencies]
anyhow = "1"
thiserror = "1"
tokio = { version = "1", features = ["full"] }
clap = { version = "4", features = ["derive"] }
serde = { version = "1", features = ["derive"] }
serde_yaml = "0.9"
serde_json = "1"
reqwest = { version = "0.12", features = ["json", "multipart", "stream", "gzip", "brotli", "deflate"] }
bytes = "1"
mime = "0.3"
url = "2"
regex = "1"
which = "6"
tempfile = "3"
sha2 = "0.10"
hex = "0.4"
humantime = "2"
bytesize = "1.3"

# TUI
ratatui = "0.27"
crossterm = "0.27"

# FS and time helpers
walkdir = "2"
time = { version = "0.3", features = ["parsing", "formatting", "macros"] }

# Logging (optional)
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["fmt", "env-filter"] }
```

---

### Layout

```
src/
  main.rs
  cli.rs
  config.rs
  core.rs
  engine.rs
  templates.rs
  telemetry.rs
  cost.rs
  pdf.rs
  video.rs
  tui.rs
  providers/
    mod.rs
    gemini.rs
  ingest/
    mod.rs
    local.rs
    url.rs
    youtube.rs
    drive.rs
  render/
    mod.rs
    writer.rs
    subtitles.rs
```

---

### `src/core.rs`

```rust
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum Kind { Slides, Lecture, Document, Image, Video }

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum PdfMode { Auto, Images, Pdf }

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum SourceKind { Local, Url, Youtube, Drive }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Asset {
    pub path: PathBuf,
    pub media: &'static str, // "pdf" | "image" | "video" | "audio"
    pub page_index: Option<u32>,
    pub source_kind: SourceKind,
    pub mime: Option<String>,
    pub meta: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct Job {
    pub source: String,
    pub recursive: bool,
    pub kind: Option<Kind>,
    pub pdf_mode: PdfMode,
    pub output_dir: Option<PathBuf>,
    pub model: String,
    pub preset: Option<String>,
    pub export: Vec<String>,
    pub skip_existing: bool,
    pub media_resolution: Option<String>, // ratified to provider
}

pub trait Ingestor: Send + Sync {
    fn discover(&self, job: &Job) -> anyhow::Result<Vec<Asset>>;
}
pub trait Normalizer: Send + Sync {
    fn prepare(&self, _job: &Job) -> anyhow::Result<()> { Ok(()) }
    fn normalize(&self, assets: &[Asset], pdf_mode: PdfMode) -> anyhow::Result<Vec<Asset>>;
    fn chunk_descriptors(&self) -> Vec<serde_json::Value> { vec![] }
    fn artifact_paths(&self) -> Vec<PathBuf> { vec![] }
}
pub trait PromptStrategy: Send + Sync {
    fn preamble(&self) -> String;
    fn instruction(&self, preamble: &str) -> String;
}
pub trait Provider: Send + Sync {
    fn supports(&self, capability: &str) -> bool;
    fn transcribe(&self, instruction: &str, assets: &[Asset], modality: &str, meta: &serde_json::Value)
        -> anyhow::Result<String>;
}
pub trait Writer: Send + Sync {
    fn write_latex(&self, base: &std::path::Path, name: &str, preamble: &str, body: &str) -> anyhow::Result<PathBuf>;
}
```

---

### `src/config.rs`

```rust
use serde::Deserialize;
use std::{env, path::PathBuf};

#[derive(Debug, Deserialize, Clone, Default)]
pub struct Defaults { pub model: Option<String>, pub output_dir: Option<PathBuf>, pub exports: Option<Vec<String>> }

#[derive(Debug, Deserialize, Clone, Default)]
pub struct Save { pub full_response: Option<bool>, pub intermediates: Option<bool> }

#[derive(Debug, Deserialize, Clone, Default)]
pub struct VideoCfg {
    pub token_limit: Option<u64>,
    pub tokens_per_second: Option<f64>,
    pub max_chunk_seconds: Option<f64>,
    pub max_chunk_bytes: Option<u64>,
    pub encoder: Option<String>,
    pub media_resolution: Option<String>,
}

#[derive(Debug, Deserialize, Clone, Default)]
pub struct Root {
    pub defaults: Option<Defaults>,
    pub save: Option<Save>,
    pub video: Option<VideoCfg>,
    pub presets: Option<serde_yaml::Value>,
    pub templates_dir: Option<PathBuf>,
    pub pricing_file: Option<PathBuf>,
}

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub api_key: String,
    pub default_model: String,
    pub output_dir: Option<PathBuf>,
    pub exports: Vec<String>,
    pub templates_dir: Option<PathBuf>,
    pub pricing_file: Option<PathBuf>,
    pub video_media_resolution: Option<String>,
}

impl AppConfig {
    pub fn load(path: Option<&str>) -> anyhow::Result<Self> {
        let api_key = env::var("GEMINI_API_KEY")
            .map_err(|_| anyhow::anyhow!("GEMINI_API_KEY not set"))?;
        let root: Root = match path {
            Some(p) => serde_yaml::from_reader(std::fs::File::open(p)?)?,
            None => {
                for candidate in ["recapit.yaml", "recapit.yml"] {
                    if std::path::Path::new(candidate).exists() {
                        let f = std::fs::File::open(candidate)?;
                        return Self::_from_yaml(Some(serde_yaml::from_reader(f)?), api_key);
                    }
                }
                return Self::_from_yaml(None, api_key);
            }
        };
        Self::_from_yaml(Some(root), api_key)
    }

    fn _from_yaml(root: Option<Root>, api_key: String) -> anyhow::Result<Self> {
        let r = root.unwrap_or_default();
        let d = r.defaults.unwrap_or_default();
        let model = std::env::var("RECAPIT_DEFAULT_MODEL")
            .ok().or(d.model).unwrap_or_else(|| "gemini-2.5-flash-lite".into());
        let output_dir = std::env::var("RECAPIT_OUTPUT_DIR").ok()
            .map(PathBuf::from).or(d.output_dir);
        let exports = d.exports.unwrap_or_default();
        Ok(Self{
            api_key,
            default_model: model,
            output_dir,
            exports,
            templates_dir: r.templates_dir,
            pricing_file: r.pricing_file,
            video_media_resolution: r.video.and_then(|v| v.media_resolution),
        })
    }
}
```

---

### `src/templates.rs`

```rust
pub fn slide_preamble() -> &'static str { include_str!("../templates/slide-template.txt") }
pub fn lecture_preamble() -> &'static str { include_str!("../templates/lecture-template.txt") }
pub fn document_preamble() -> &'static str { include_str!("../templates/document-template.txt") }
pub fn image_preamble() -> &'static str { include_str!("../templates/image-template.txt") }
pub fn video_preamble() -> &'static str { include_str!("../templates/video-template.txt") }

pub fn default_prompt(kind: &str, preamble: &str) -> String {
    match kind {
        "slides" => format!("{preamble}\nSummarize slide content. Preserve order and math."),
        "lecture" => format!("{preamble}\nProduce a lecture summary with [MM:SS] timestamps."),
        "image" => format!("{preamble}\nDescribe the image and transcribe text."),
        "video" => format!("{preamble}\nTranscript with [MM:SS] and visual events."),
        _ => format!("{preamble}\nSummarize the document. Keep math."),
    }
}
```

Place your existing `templates/*.txt` next to `src/templates.rs` in a `templates/` folder.

---

### `src/pdf.rs`

```rust
use anyhow::Context;
use std::{path::{Path, PathBuf}, process::Command, fs};

pub fn pdf_to_png(pdf: &Path, out_dir: &Path, prefix: Option<&str>) -> anyhow::Result<Vec<PathBuf>> {
    if out_dir.exists() { fs::remove_dir_all(out_dir).ok(); }
    std::fs::create_dir_all(out_dir)?;
    let pdftoppm = which::which("pdftoppm")
        .context("pdftoppm not found; install poppler utils")?;
    let stem = prefix.unwrap_or(pdf.file_stem().and_then(|s| s.to_str()).unwrap_or("page"));
    let out_base = out_dir.join(stem);
    let status = Command::new(pdftoppm)
        .arg("-png")
        .arg(pdf)
        .arg(&out_base)
        .status()?;
    if !status.success() { anyhow::bail!("pdftoppm failed"); }
    let mut pages = vec![];
    for entry in walkdir::WalkDir::new(out_dir).min_depth(1).max_depth(1) {
        let e = entry?;
        if e.file_type().is_file() && e.path().extension().and_then(|s| s.to_str()) == Some("png") {
            pages.push(e.into_path());
        }
    }
    pages.sort();
    Ok(pages)
}
```

---

### `src/video.rs`

```rust
use anyhow::Context;
use serde::{Deserialize, Serialize};
use std::{path::{Path, PathBuf}, process::Command};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct VideoMeta {
    pub path: PathBuf,
    pub duration_seconds: f64,
    pub size_bytes: u64,
    pub fps: Option<f64>,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub video_codec: Option<String>,
    pub audio_codec: Option<String>,
}

pub fn ffprobe(path: &Path) -> anyhow::Result<VideoMeta> {
    let ffprobe = which::which("ffprobe").context("ffprobe not found")?;
    let out = Command::new(ffprobe)
        .args(["-v","error","-print_format","json","-show_streams","-show_format"])
        .arg(path)
        .output()?;
    if !out.status.success() { anyhow::bail!("ffprobe failed"); }
    let v: serde_json::Value = serde_json::from_slice(&out.stdout)?;
    let fmt = v.get("format").cloned().unwrap_or_default();
    let streams = v.get("streams").cloned().unwrap_or_default();
    let duration = fmt.get("duration").and_then(|x| x.as_str()).and_then(|s| s.parse().ok()).unwrap_or(0.0);
    let size_bytes = fmt.get("size").and_then(|x| x.as_str()).and_then(|s| s.parse().ok()).unwrap_or(0u64);
    let mut meta = VideoMeta{
        path: path.to_path_buf(), duration_seconds: duration, size_bytes,
        fps: None, width: None, height: None,
        video_codec: None, audio_codec: None
    };
    if let Some(arr) = streams.as_array() {
        for s in arr {
            match s.get("codec_type").and_then(|x| x.as_str()) {
                Some("video") => {
                    meta.video_codec = s.get("codec_name").and_then(|x| x.as_str()).map(|s| s.to_string());
                    meta.width = s.get("width").and_then(|x| x.as_u64()).map(|x| x as u32);
                    meta.height = s.get("height").and_then(|x| x.as_u64()).map(|x| x as u32);
                    let rate = s.get("avg_frame_rate").and_then(|x| x.as_str()).unwrap_or("0/0");
                    meta.fps = parse_rate(rate);
                }
                Some("audio") => {
                    meta.audio_codec = s.get("codec_name").and_then(|x| x.as_str()).map(|s| s.to_string());
                }
                _ => {}
            }
        }
    }
    Ok(meta)
}

fn parse_rate(r: &str) -> Option<f64> {
    if let Some((n,d)) = r.split_once('/') {
        let n: f64 = n.parse().ok()?;
        let d: f64 = d.parse().ok()?;
        if d > 0.0 { return Some(n/d); }
        return None
    }
    r.parse().ok()
}

pub fn ffmpeg_normalize(source: &Path, out_dir: &Path) -> anyhow::Result<PathBuf> {
    std::fs::create_dir_all(out_dir)?;
    let dst = out_dir.join(format!("{}-normalized.mp4", source.file_stem().unwrap().to_string_lossy()));
    let ffmpeg = which::which("ffmpeg").context("ffmpeg not found")?;
    let status = Command::new(ffmpeg)
        .args([
            "-y","-i", source.to_str().unwrap(),
            "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p",
            "-movflags","+faststart","-c:a","aac","-b:a","192k",
            dst.to_str().unwrap()
        ]).status()?;
    if !status.success() { anyhow::bail!("ffmpeg failed"); }
    Ok(dst)
}

pub fn plan_chunks(meta: &VideoMeta, max_seconds: f64, max_bytes: u64, tokens_per_second: f64, token_limit: Option<u64>)
-> Vec<(f64,f64)> {
    let duration = meta.duration_seconds.max(0.0);
    if duration == 0.0 { return vec![(0.0, 0.0)]; }
    let bps = if duration > 0.0 { meta.size_bytes as f64 / duration } else { meta.size_bytes as f64 };
    let mut eff = max_seconds;
    if max_bytes > 0 && bps > 0.0 {
        eff = eff.min(max_bytes as f64 / bps);
    }
    if let Some(limit) = token_limit {
        let seconds_by_tokens = (limit as f64 / tokens_per_second).max(1.0);
        eff = eff.min(seconds_by_tokens);
    }
    eff = eff.max(1.0);
    let mut out = vec![];
    let mut start = 0.0;
    while start < duration {
        let end = (start + eff).min(duration);
        out.push((start, end));
        start = end;
    }
    if let Some(last) = out.last_mut() { last.1 = duration; }
    out
}
```

---

### `src/providers/mod.rs`

```rust
pub mod gemini;
```

### `src/providers/gemini.rs`

```rust
use crate::core::{Asset, Provider};
use anyhow::Context;

pub struct GeminiProvider {
    api_key: String,
    model: String,
    http: reqwest::Client,
}
impl GeminiProvider {
    pub fn new(api_key: String, model: String) -> Self {
        Self { api_key, model, http: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600)).build().unwrap() }
    }
}
impl Provider for GeminiProvider {
    fn supports(&self, cap: &str) -> bool {
        matches!(cap, "text" | "image" | "video" | "pdf")
    }

    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        _meta: &serde_json::Value,
    ) -> anyhow::Result<String> {
        // Inline <= ~20MB: send as multipart; larger: switch to Files API.
        // Endpoint sketch: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
        // Headers: x-goog-api-key: <KEY>
        // Build JSON parts: [{"text": instruction}, {"inline_data": {"mime_type": "...","data":"<base64>"}}...]
        // Return resp.text if present.
        let _ = (instruction, assets, modality); // silence until wired
        anyhow::bail!("Gemini HTTP wiring TODO: implement generateContent + Files upload");
    }
}
```

---

### `src/render/writer.rs`

```rust
use anyhow::Context;
use std::path::{Path, PathBuf};

pub struct LatexWriter;
impl LatexWriter {
    pub fn new() -> Self { Self }
}
impl crate::core::Writer for LatexWriter {
    fn write_latex(&self, base: &Path, name: &str, preamble: &str, body: &str) -> anyhow::Result<PathBuf> {
        std::fs::create_dir_all(base)?;
        let p = base.join(format!("{name}.tex"));
        let mut s = String::new();
        s.push_str("\\documentclass{article}\n\\usepackage{hyperref,amsmath}\n\\begin{document}\n");
        s.push_str(preamble.trim());
        s.push_str("\n\n");
        s.push_str(body.trim());
        s.push_str("\n\\end{document}\n");
        std::fs::write(&p, s).with_context(|| format!("write {:?}", p))?;
        Ok(p)
    }
}
```

---

### `src/telemetry.rs`

```rust
use serde::Serialize;
use std::time::SystemTime;

#[derive(Debug, Clone, Serialize)]
pub struct RequestEvent {
    pub model: String,
    pub modality: String,
    pub started_at: SystemTime,
    pub finished_at: SystemTime,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub total_tokens: Option<u64>,
    pub metadata: serde_json::Value,
}

#[derive(Default)]
pub struct RunMonitor {
    events: parking_lot::Mutex<Vec<RequestEvent>>,
    notes: parking_lot::Mutex<Vec<serde_json::Value>>,
}
impl RunMonitor {
    pub fn new() -> Self { Self::default() }
    pub fn record(&self, ev: RequestEvent) { self.events.lock().push(ev); }
    pub fn note(&self, name: &str, payload: serde_json::Value) {
        self.notes.lock().push(serde_json::json!({ "name": name, "payload": payload, "ts": chrono::Utc::now() }));
    }
    pub fn events(&self) -> Vec<RequestEvent> { self.events.lock().clone() }
}
```

Add `parking_lot = "0.12"` and `chrono = { version = "0.4", features = ["serde"] }` to `Cargo.toml` if you keep this file verbatim. If you prefer, replace with `std::sync::Mutex` and `time`.

---

### `src/engine.rs`

```rust
use crate::core::*;
use crate::telemetry::RunMonitor;
use std::path::PathBuf;
use tokio::sync::mpsc::UnboundedSender;

#[derive(Clone, Debug)]
pub enum ProgressKind { Discover, Normalize, Upload, Transcribe, Write }

#[derive(Clone, Debug)]
pub struct Progress {
    pub task: String,
    pub kind: ProgressKind,
    pub current: u64,
    pub total: u64,
    pub status: String,
}

pub struct Engine<I,N,P,W> {
    pub ingestor: I,
    pub normalizer: N,
    pub provider: P,
    pub writer: W,
    pub monitor: RunMonitor,
    pub tx: UnboundedSender<Progress>,
}

impl<I,N,P,W> Engine<I,N,P,W>
where
    I: Ingestor, N: Normalizer, P: Provider, W: Writer
{
    pub async fn run(&self, job: &Job, prompt: &dyn PromptStrategy) -> anyhow::Result<Option<PathBuf>> {
        self.normalizer.prepare(job)?;
        self.tx.send(Progress{ task: "discover".into(), kind: ProgressKind::Discover, current:0, total:1, status:"start".into() }).ok();
        let assets = self.ingestor.discover(job)?;
        if assets.is_empty() { return Ok(None); }
        self.tx.send(Progress{ task: "normalize".into(), kind: ProgressKind::Normalize, current:0, total:assets.len() as u64, status:"queue".into() }).ok();
        let normalized = self.normalizer.normalize(&assets, job.pdf_mode)?;
        let modality = match normalized.first().map(|a| a.media) {
            Some("video"|"audio") => "video",
            Some("pdf") => "pdf",
            _ => "image",
        };
        let preamble = prompt.preamble();
        let instruction = prompt.instruction(&preamble);

        self.tx.send(Progress{ task: "transcribe".into(), kind: ProgressKind::Transcribe, current:0, total:normalized.len() as u64, status:modality.into() }).ok();
        let text = self.provider.transcribe(&instruction, &normalized, modality, &serde_json::json!({
            "kind": job.kind.map(|k| format!("{:?}", k)),
            "source": job.source,
            "media_resolution": job.media_resolution,
            "skip_existing": job.skip_existing,
        }))?;

        let base = job.output_dir.clone().unwrap_or_else(|| std::path::Path::new("output").to_path_buf());
        let source_slug = slugify(if job.source.contains("://") { "remote" } else { std::path::Path::new(&job.source).file_stem().unwrap().to_str().unwrap() });
        let base_dir = base.join(source_slug);
        let out_name = format!("{}-transcribed", std::path::Path::new(&job.source).file_stem().unwrap().to_string_lossy());
        self.tx.send(Progress{ task: "write".into(), kind: ProgressKind::Write, current:0, total:1, status:"latex".into() }).ok();
        let path = self.writer.write_latex(&base_dir, &out_name, &preamble, &text)?;
        Ok(Some(path))
    }
}

fn slugify(s: &str) -> String {
    s.chars().map(|c| if c.is_alphanumeric() || "-_.".contains(c) { c } else { '-' }).collect::<String>().trim_matches('-').to_string()
}
```

---

### `src/tui.rs`

```rust
use crossterm::{event, execute, terminal};
use ratatui::{prelude::*, widgets::*};
use std::{collections::HashMap, io::Stdout};
use tokio::sync::mpsc::UnboundedReceiver;

use crate::engine::{Progress, ProgressKind};

#[derive(Default)]
struct RowState {
    kind: ProgressKind,
    cur: u64,
    total: u64,
    status: String,
}

pub async fn run_tui(mut rx: UnboundedReceiver<Progress>) -> anyhow::Result<()> {
    // setup
    let mut stdout = std::io::stdout();
    terminal::enable_raw_mode()?;
    execute!(stdout, terminal::EnterAlternateScreen, event::EnableMouseCapture)?;
    let mut term = Terminal::new(CrosstermBackend::new(stdout))?;
    let mut rows: HashMap<String, RowState> = HashMap::new();

    loop {
        // draw
        term.draw(|f| {
            let size = f.size();
            let block = Block::default().title("recapit").borders(Borders::ALL);
            let inner = block.inner(size);
            f.render_widget(block, size);

            let mut items: Vec<ListItem> = Vec::new();
            for (task, st) in rows.iter() {
                let pct = if st.total>0 { (st.cur as f64 / st.total as f64).min(1.0) } else { 0.0 };
                let bar = format!("{:>3}%", (pct*100.0) as u64);
                let line = format!("{task:10}  [{:50}]  {bar}  {}", progress_bar(pct), st.status);
                items.push(ListItem::new(line));
            }
            let list = List::new(items).block(Block::default().title("progress").borders(Borders::ALL));
            f.render_widget(list, inner);
        })?;

        // input or events
        if crossterm::event::poll(std::time::Duration::from_millis(33))? {
            if let event::Event::Key(k) = event::read()? {
                if k.code == event::KeyCode::Char('q') { break; }
            }
        }
        while let Ok(evt) = rx.try_recv() {
            let entry = rows.entry(evt.task.clone()).or_default();
            entry.kind = evt.kind;
            entry.cur = evt.current;
            entry.total = evt.total.max(1);
            entry.status = evt.status;
        }
        // quit when nothing left to update and no key input? Keep simple, user presses q.
    }

    // teardown
    terminal::disable_raw_mode()?;
    execute!(term.backend_mut(), terminal::LeaveAlternateScreen, event::DisableMouseCapture)?;
    term.show_cursor()?;
    Ok(())
}

fn progress_bar(p: f64) -> String {
    let width = 50usize;
    let filled = (p * width as f64).round() as usize;
    let mut s = String::with_capacity(width);
    for i in 0..width { s.push(if i < filled { '█' } else { ' ' }); }
    s
}
```

---

### `src/ingest/mod.rs` (minimal)

```rust
use crate::core::{Asset, Ingestor, Job, SourceKind};
use std::path::PathBuf;

pub struct CompositeIngestor;
impl Ingestor for CompositeIngestor {
    fn discover(&self, job: &Job) -> anyhow::Result<Vec<Asset>> {
        let p = std::path::Path::new(&job.source);
        if p.exists() && p.is_file() {
            let media = match p.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase().as_str() {
                "pdf" => "pdf", "png"|"jpg"|"jpeg"|"gif"|"bmp"|"tif"|"tiff" => "image",
                "mp4"|"mov"|"mkv" => "video", "mp3"|"wav"|"m4a" => "audio", _ => return Ok(vec![]),
            };
            return Ok(vec![Asset{
                path: PathBuf::from(p),
                media,
                page_index: None,
                source_kind: SourceKind::Local,
                mime: None,
                meta: serde_json::json!({}),
            }]);
        }
        // URL/YT/Drive omitted for brevity
        Ok(vec![])
    }
}
```

---

### `src/cli.rs`

```rust
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name="recapit", version, about="Rust rewrite with ratatui progress")]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Command,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// Unified engine
    Summarize {
        source: String,
        #[arg(short='o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value="auto")]
        kind: String,
        #[arg(long, default_value="auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value_t=true)]
        skip_existing: bool,
        #[arg(long)]
        export: Vec<String>,
    },
    /// Plan only
    Plan {
        source: String,
        #[arg(long, default_value="auto")]
        kind: String,
        #[arg(long, default_value="auto")]
        pdf_mode: String,
    }
}
```

---

### `src/main.rs`

```rust
mod core;
mod cli;
mod config;
mod templates;
mod telemetry;
mod pdf;
mod video;
mod tui;
mod engine;
mod providers { pub mod gemini; }
mod ingest { pub mod mod_; pub use mod_::CompositeIngestor as CompositeIngestor; }

use clap::Parser;
use core::{Job, Kind, PdfMode};
use engine::{Engine, Progress, ProgressKind};
use providers::gemini::GeminiProvider;
use render::writer::LatexWriter;
use telemetry::RunMonitor;
use tokio::sync::mpsc;

mod render { pub mod writer; pub mod subtitles {} }

struct SimplePrompt { kind: Kind }
impl core::PromptStrategy for SimplePrompt {
    fn preamble(&self) -> String {
        match self.kind {
            Kind::Slides => templates::slide_preamble().to_string(),
            Kind::Lecture => templates::lecture_preamble().to_string(),
            Kind::Image => templates::image_preamble().to_string(),
            Kind::Video => templates::video_preamble().to_string(),
            Kind::Document => templates::document_preamble().to_string(),
        }
    }
    fn instruction(&self, preamble: &str) -> String {
        templates::default_prompt(&format!("{:?}", self.kind).to_lowercase(), preamble)
    }
}

// Dummy normalizer: pass-through; add your pdf/video logic here
struct Normalizer;
impl core::Normalizer for Normalizer {
    fn normalize(&self, assets: &[core::Asset], pdf_mode: PdfMode) -> anyhow::Result<Vec(core::Asset)> {
        let _ = pdf_mode;
        Ok(assets.to_vec())
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env()).init();

    let cli = cli::Cli::parse();
    match cli.cmd {
        cli::Command::Summarize { source, output_dir, kind, pdf_mode, model, skip_existing, export } => {
            let cfg = config::AppConfig::load(None)?;
            let model = model.unwrap_or(cfg.default_model.clone());
            let (tx, rx) = mpsc::unbounded_channel::<Progress>();

            // TUI task
            let tui_handle = tokio::spawn(tui::run_tui(rx));

            // Engine
            let ingestor = ingest::CompositeIngestor;
            let normalizer = Normalizer;
            let provider = GeminiProvider::new(cfg.api_key.clone(), model.clone());
            let writer = render::writer::LatexWriter::new();
            let engine = Engine {
                ingestor, normalizer, provider, writer,
                monitor: RunMonitor::new(), tx: tx.clone(),
            };

            // Fire a couple sample progress signals to show the UI immediately
            tx.send(Progress{ task:"bootstrap".into(), kind:ProgressKind::Discover, current:1, total:3, status:"init".into() }).ok();

            let job = Job {
                source: source.clone(),
                recursive: false,
                kind: parse_kind(&kind),
                pdf_mode: parse_pdf_mode(&pdf_mode),
                output_dir,
                model,
                preset: None,
                export,
                skip_existing,
                media_resolution: cfg.video_media_resolution.clone(),
            };

            let prompt = SimplePrompt { kind: job.kind.unwrap_or(Kind::Document) };
            let result = engine.run(&job, &prompt).await?;
            if let Some(path) = result {
                tx.send(Progress{ task:"done".into(), kind:ProgressKind::Write, current:1, total:1, status:path.display().to_string() }).ok();
            }
            // wait for user to press 'q'
            tui_handle.await??;
        }
        cli::Command::Plan { source, kind, pdf_mode } => {
            println!("Source: {source}");
            println!("Kind:   {}", kind);
            println!("PDF:    {}", pdf_mode);
            // wire real planner here
        }
    }
    Ok(())
}

fn parse_kind(s: &str) -> Option<Kind> {
    match s.to_lowercase().as_str() {
        "slides" => Some(Kind::Slides),
        "lecture" => Some(Kind::Lecture),
        "document" => Some(Kind::Document),
        "image" => Some(Kind::Image),
        "video" => Some(Kind::Video),
        _ => None,
    }
}
fn parse_pdf_mode(s: &str) -> PdfMode {
    match s.to_lowercase().as_str() {
        "images" => PdfMode::Images,
        "pdf" => PdfMode::Pdf,
        _ => PdfMode::Auto,
    }
}
```

---

### How to run

```bash
# prerequisites: ffmpeg, ffprobe, pdftoppm
cargo run -- summarize path/to/file.pdf -o output
# TUI opens; press q to exit
```

---

### Notes

* Replace `GeminiProvider::transcribe` with your actual `generateContent` + Files upload. Keep inline for small payloads, upload for large, mirror your Python “skip_existing” and manifest behavior if desired.
* Extend `Normalizer` to call `pdf::pdf_to_png` when `PdfMode::Images` and to normalize + chunk video with `video::ffmpeg_normalize` + `video::plan_chunks`.
* Keep cost and telemetry parity by recording events in `RunMonitor` where you make HTTP calls.
* The TUI receives `Progress` events from all stages. Push granular updates from ingestion, normalization, uploads, and per-chunk transcription.
