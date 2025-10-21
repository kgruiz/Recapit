use clap::{ArgAction, Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(
    name = "recapit",
    version,
    about = "Rust rewrite with ratatui progress"
)]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Command,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// Unified engine
    Summarize {
        source: String,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, action = ArgAction::SetTrue, help = "Recurse into directories when summarizing.")]
        recursive: bool,
        #[arg(
            long = "no-recursive",
            action = ArgAction::SetTrue,
            help = "Disable directory recursion explicitly."
        )]
        no_recursive: bool,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        export: Vec<String>,
        #[arg(
            long,
            value_name = "NAME",
            default_value = "basic",
            help = "Preset profile (basic|speed|quality). Presets may override kind, pdf_mode, exports, media resolution, model, and recursion settings."
        )]
        preset: String,
        #[arg(
            long,
            value_name = "LEVEL",
            help = "Override media resolution (default|low)."
        )]
        media_resolution: Option<String>,
        #[arg(
            long,
            value_name = "PATH",
            help = "Load configuration from an explicit file path."
        )]
        config: Option<PathBuf>,
    },
    /// Plan only
    Plan {
        source: String,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, action = ArgAction::SetTrue)]
        recursive: bool,
        #[arg(long, action = ArgAction::SetTrue)]
        json: bool,
        #[arg(long)]
        config: Option<PathBuf>,
    },
    /// Conversion utilities
    Convert {
        #[command(subcommand)]
        command: ConvertCommand,
    },
    /// Create a starter configuration file
    Init {
        #[arg(long, value_name = "PATH", default_value = "recapit.yaml")]
        path: PathBuf,
        #[arg(long, action = ArgAction::SetTrue, help = "Overwrite an existing file")]
        force: bool,
    },
    /// Planner utilities
    Planner {
        #[command(subcommand)]
        command: PlannerCommand,
    },
    /// Reporting utilities
    Report {
        #[command(subcommand)]
        command: ReportCommand,
    },
    /// Cleanup utilities
    Cleanup {
        #[command(subcommand)]
        command: CleanupCommand,
    },
}

#[derive(Subcommand, Debug)]
pub enum ConvertCommand {
    /// Convert LaTeX sources to Markdown using Gemini
    LatexToMd {
        source: PathBuf,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "*.tex")]
        file_pattern: String,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value_t = false)]
        recursive: bool,
    },
    /// Convert LaTeX tables or structured content to JSON using Gemini
    LatexToJson {
        source: PathBuf,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "*.tex")]
        file_pattern: String,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value_t = false)]
        recursive: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum PlannerCommand {
    /// Preview ingestion and chunk planning without running transcription
    Plan {
        source: String,
        #[arg(long, action = ArgAction::SetTrue)]
        recursive: bool,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, action = ArgAction::SetTrue)]
        json: bool,
        #[arg(long)]
        config: Option<PathBuf>,
    },
    /// List the discovered assets without normalization or transcription
    Ingest {
        source: String,
        #[arg(long, action = ArgAction::SetTrue)]
        recursive: bool,
        #[arg(long, action = ArgAction::SetTrue)]
        json: bool,
        #[arg(long)]
        config: Option<PathBuf>,
    },
}

#[derive(Subcommand, Debug)]
pub enum ReportCommand {
    /// Summarize token usage and estimated cost from telemetry outputs
    Cost {
        #[arg(long, value_name = "FILE", default_value = "run-summary.json")]
        summary: PathBuf,
        #[arg(long, value_name = "FILE", default_value = "run-events.ndjson")]
        events: PathBuf,
        #[arg(long, value_name = "FILE")]
        pricing: Option<PathBuf>,
        #[arg(long, action = ArgAction::SetTrue)]
        json: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum CleanupCommand {
    /// Remove cached normalization artifacts from the system temp directory
    Caches {
        #[arg(long, action = ArgAction::SetTrue)]
        dry_run: bool,
    },
    /// Remove generated manifests and chunk artifacts under a directory
    Artifacts {
        #[arg(long, value_name = "DIR", default_value = ".")]
        root: PathBuf,
        #[arg(long, action = ArgAction::SetTrue)]
        dry_run: bool,
    },
}
