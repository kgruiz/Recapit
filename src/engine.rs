use crate::core::{Ingestor, Job, Normalizer, PromptStrategy, Provider, Writer};
use crate::telemetry::RunMonitor;
use anyhow::Result;
use std::path::{Path, PathBuf};
use tokio::sync::mpsc::UnboundedSender;

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub enum ProgressKind {
    Discover,
    Normalize,
    Upload,
    Transcribe,
    Write,
}

#[derive(Clone, Debug)]
pub struct Progress {
    pub task: String,
    pub kind: ProgressKind,
    pub current: u64,
    pub total: u64,
    pub status: String,
}

pub struct Engine<I, N, P, W>
where
    I: Ingestor,
    N: Normalizer,
    P: Provider,
    W: Writer,
{
    pub ingestor: I,
    pub normalizer: N,
    pub provider: P,
    pub writer: W,
    pub monitor: RunMonitor,
    pub tx: UnboundedSender<Progress>,
}

impl<I, N, P, W> Engine<I, N, P, W>
where
    I: Ingestor,
    N: Normalizer,
    P: Provider,
    W: Writer,
{
    pub async fn run(&self, job: &Job, prompt: &dyn PromptStrategy) -> Result<Option<PathBuf>> {
        let _ = self.monitor.elapsed();
        self.normalizer.prepare(job)?;
        self.tx
            .send(Progress {
                task: "discover".into(),
                kind: ProgressKind::Discover,
                current: 0,
                total: 1,
                status: "start".into(),
            })
            .ok();

        let assets = self.ingestor.discover(job)?;
        if assets.is_empty() {
            return Ok(None);
        }

        self.tx
            .send(Progress {
                task: "normalize".into(),
                kind: ProgressKind::Normalize,
                current: 0,
                total: assets.len() as u64,
                status: "queue".into(),
            })
            .ok();
        let normalized = self.normalizer.normalize(&assets, job.pdf_mode)?;

        let modality = normalized
            .first()
            .map(|a| match a.media.as_str() {
                "video" | "audio" => "video",
                "pdf" => "pdf",
                _ => "image",
            })
            .unwrap_or("image");

        let preamble = prompt.preamble();
        let instruction = prompt.instruction(&preamble);

        self.tx
            .send(Progress {
                task: "transcribe".into(),
                kind: ProgressKind::Transcribe,
                current: 0,
                total: normalized.len() as u64,
                status: modality.into(),
            })
            .ok();
        let _ = self.provider.supports(modality);
        let text = self.provider.transcribe(
            &instruction,
            &normalized,
            modality,
            &serde_json::json!({
                "kind": job.kind.map(|k| format!("{:?}", k)),
                "source": job.source,
                "media_resolution": job.media_resolution,
                "skip_existing": job.skip_existing,
            }),
        )?;

        let base = job
            .output_dir
            .clone()
            .unwrap_or_else(|| Path::new("output").to_path_buf());
        let source_slug = if job.source.contains("://") {
            slugify("remote")
        } else {
            let source_path = Path::new(&job.source);
            slugify(
                source_path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("source"),
            )
        };
        let base_dir = base.join(source_slug);
        let out_name = Path::new(&job.source)
            .file_stem()
            .and_then(|s| s.to_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| "transcript".to_string());

        self.tx
            .send(Progress {
                task: "write".into(),
                kind: ProgressKind::Write,
                current: 0,
                total: 1,
                status: "latex".into(),
            })
            .ok();
        let path = self
            .writer
            .write_latex(&base_dir, &out_name, &preamble, &text)?;
        Ok(Some(path))
    }
}

fn slugify(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
                c
            } else {
                '-'
            }
        })
        .collect::<String>()
        .trim_matches('-')
        .to_string()
}
