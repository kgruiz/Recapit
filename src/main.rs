mod cli;
mod config;
mod constants;
mod core;
mod cost;
mod engine;
mod ingest;
mod pdf;
mod prompts;
mod providers;
mod render;
mod telemetry;
mod templates;
mod tui;
mod utils;
mod video;

use clap::Parser;
use core::{Job, Kind, PdfMode};
use engine::{Engine, Progress, ProgressKind};
use ingest::{CompositeIngestor, CompositeNormalizer};
use providers::gemini::GeminiProvider;
use render::writer::LatexWriter;
use tokio::sync::mpsc;

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

            let monitor = telemetry::RunMonitor::new();
            let provider = GeminiProvider::new(cfg.api_key.clone(), model.clone(), monitor.clone());
            let normalizer = CompositeNormalizer::new(
                None,
                None,
                cfg.video_encoder_preference,
                Some(cfg.video_max_chunk_seconds),
                Some(cfg.video_max_chunk_bytes),
                cfg.video_token_limit,
                Some(cfg.video_tokens_per_second),
                None,
            )?;
            let ingestor = CompositeIngestor::new()?;
            let cost = cost::CostEstimator::from_path(
                cfg.pricing_file.as_deref(),
                cfg.pricing_defaults.clone(),
            )?;
            let exports = if export.is_empty() {
                cfg.exports.clone()
            } else {
                export.clone()
            };
            let mut engine = Engine::new(
                Box::new(ingestor),
                Box::new(normalizer),
                Box::new(provider),
                Box::new(LatexWriter::new()),
                tx.clone(),
                monitor.clone(),
                cost,
                &cfg,
            )?;

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
                export: exports,
                skip_existing,
                media_resolution: Some(cfg.media_resolution.clone()),
            };

            let result = engine.run(&job).await?;
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
