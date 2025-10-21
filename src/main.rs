mod cli;
mod config;
mod core;
mod engine;
mod ingest;
mod pdf;
mod providers;
mod render;
mod telemetry;
mod templates;
mod tui;
mod video;

use clap::Parser;
use core::{Job, Kind, PdfMode};
use engine::{Engine, Progress, ProgressKind};
use providers::gemini::GeminiProvider;
use render::writer::LatexWriter;
use telemetry::RunMonitor;
use tokio::sync::mpsc;

struct SimplePrompt {
    kind: Kind,
}

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
        let kind = match self.kind {
            Kind::Slides => "slides",
            Kind::Lecture => "lecture",
            Kind::Image => "image",
            Kind::Video => "video",
            Kind::Document => "document",
        };
        templates::default_prompt(kind, preamble)
    }
}

struct Normalizer;

impl core::Normalizer for Normalizer {
    fn normalize(
        &self,
        assets: &[core::Asset],
        pdf_mode: PdfMode,
    ) -> anyhow::Result<Vec<core::Asset>> {
        let _ = pdf_mode;
        Ok(assets.to_vec())
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let cli = cli::Cli::parse();
    match cli.cmd {
        cli::Command::Summarize {
            source,
            output_dir,
            kind,
            pdf_mode,
            model,
            skip_existing,
            export,
        } => {
            let cfg = config::AppConfig::load(None)?;
            let model = model.unwrap_or(cfg.default_model.clone());
            let (tx, rx) = mpsc::unbounded_channel::<Progress>();

            let tui_handle = tokio::spawn(tui::run_tui(rx));

            let ingestor = ingest::CompositeIngestor;
            let normalizer = Normalizer;
            let provider = GeminiProvider::new(cfg.api_key.clone(), model.clone());
            let writer = LatexWriter::new();
            let engine = Engine {
                ingestor,
                normalizer,
                provider,
                writer,
                monitor: RunMonitor::new(),
                tx: tx.clone(),
            };

            tx.send(Progress {
                task: "bootstrap".into(),
                kind: ProgressKind::Discover,
                current: 1,
                total: 3,
                status: "init".into(),
            })
            .ok();

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

            let prompt = SimplePrompt {
                kind: job.kind.unwrap_or(Kind::Document),
            };
            let result = engine.run(&job, &prompt).await?;
            if let Some(path) = result {
                tx.send(Progress {
                    task: "done".into(),
                    kind: ProgressKind::Write,
                    current: 1,
                    total: 1,
                    status: path.display().to_string(),
                })
                .ok();
            }

            tui_handle.await??;
        }
        cli::Command::Plan {
            source,
            kind,
            pdf_mode,
        } => {
            println!("Source: {source}");
            println!("Kind:   {kind}");
            println!("PDF:    {pdf_mode}");
        }
    }

    Ok(())
}

fn parse_kind(input: &str) -> Option<Kind> {
    match input.to_lowercase().as_str() {
        "slides" => Some(Kind::Slides),
        "lecture" => Some(Kind::Lecture),
        "document" => Some(Kind::Document),
        "image" => Some(Kind::Image),
        "video" => Some(Kind::Video),
        _ => None,
    }
}

fn parse_pdf_mode(input: &str) -> PdfMode {
    match input.to_lowercase().as_str() {
        "images" => PdfMode::Images,
        "pdf" => PdfMode::Pdf,
        _ => PdfMode::Auto,
    }
}
