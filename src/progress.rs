use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ProgressScope {
    Run,
    Job {
        id: String,
        label: String,
    },
    ChunkProgress {
        job_id: String,
        total: u64,
    },
    ChunkDetail {
        job_id: String,
        index: u64,
        total: u64,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ProgressStage {
    Discover,
    Normalize,
    Transcribe,
    Write,
}

#[derive(Debug, Clone)]
pub struct Progress {
    pub scope: ProgressScope,
    pub stage: ProgressStage,
    pub current: u64,
    pub total: u64,
    pub status: String,
    pub finished: bool,
}

impl ProgressStage {
    pub fn label(&self) -> &'static str {
        match self {
            ProgressStage::Discover => "discover",
            ProgressStage::Normalize => "normalize",
            ProgressStage::Transcribe => "transcribe",
            ProgressStage::Write => "write",
        }
    }
}

impl fmt::Display for ProgressScope {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ProgressScope::Run => write!(f, "overall"),
            ProgressScope::Job { label, .. } => write!(f, "{label}"),
            ProgressScope::ChunkProgress { total, .. } => write!(f, "chunks ({} total)", total),
            ProgressScope::ChunkDetail { index, total, .. } => {
                write!(f, "chunk {} / {}", index + 1, total)
            }
        }
    }
}
