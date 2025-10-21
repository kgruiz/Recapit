use crate::core::Job;
use crate::cost::CostEstimator;
use crate::utils::ensure_dir;
use serde::Serialize;
use serde_json::json;
use std::collections::HashMap;
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

#[derive(Debug, Clone, Serialize)]
pub struct RequestEvent {
    pub model: String,
    pub modality: String,
    #[serde(with = "time::serde::rfc3339")]
    pub started_at: OffsetDateTime,
    #[serde(with = "time::serde::rfc3339")]
    pub finished_at: OffsetDateTime,
    pub input_tokens: Option<u32>,
    pub output_tokens: Option<u32>,
    pub total_tokens: Option<u32>,
    #[serde(default)]
    pub metadata: HashMap<String, serde_json::Value>,
}

impl RequestEvent {
    pub fn duration_seconds(&self) -> f64 {
        (self.finished_at - self.started_at)
            .as_seconds_f64()
            .max(0.0)
    }
}

#[derive(Debug, Default, Serialize)]
pub struct RunSummary {
    pub total_requests: usize,
    pub total_input_tokens: u64,
    pub total_output_tokens: u64,
    pub total_tokens: u64,
    pub total_duration_seconds: f64,
    pub by_model: HashMap<String, SummaryBucket>,
    pub by_modality: HashMap<String, SummaryBucket>,
}

#[derive(Debug, Default, Serialize)]
pub struct SummaryBucket {
    pub requests: usize,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub total_tokens: u64,
    pub total_duration_seconds: f64,
}

#[derive(Clone)]
pub struct RunMonitor {
    inner: Arc<Mutex<RunState>>,
}

impl Default for RunMonitor {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(RunState::default())),
        }
    }
}

#[derive(Default)]
struct RunState {
    events: Vec<RequestEvent>,
    notes: Vec<Note>,
    first_started: Option<OffsetDateTime>,
    last_finished: Option<OffsetDateTime>,
}

#[derive(Debug, Clone, Serialize)]
struct Note {
    name: String,
    payload: serde_json::Value,
    #[serde(with = "time::serde::rfc3339")]
    timestamp: OffsetDateTime,
}

impl RunMonitor {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record(&self, event: RequestEvent) {
        let mut state = self.inner.lock().unwrap();
        if state.first_started.is_none()
            || event.started_at < state.first_started.unwrap_or(event.started_at)
        {
            state.first_started = Some(event.started_at);
        }
        if state.last_finished.is_none()
            || event.finished_at > state.last_finished.unwrap_or(event.finished_at)
        {
            state.last_finished = Some(event.finished_at);
        }
        state.events.push(event);
    }

    pub fn note_event(&self, name: &str, payload: serde_json::Value) {
        let mut state = self.inner.lock().unwrap();
        state.notes.push(Note {
            name: name.to_string(),
            payload,
            timestamp: OffsetDateTime::now_utc(),
        });
    }

    pub fn events(&self) -> Vec<RequestEvent> {
        self.inner.lock().unwrap().events.clone()
    }

    pub fn summarize(&self) -> RunSummary {
        let state = self.inner.lock().unwrap();
        let mut summary = RunSummary::default();
        summary.total_requests = state.events.len();
        for event in &state.events {
            let input = event.input_tokens.unwrap_or_else(|| {
                event
                    .total_tokens
                    .and_then(|total| event.output_tokens.map(|out| total.saturating_sub(out)))
                    .unwrap_or(0)
            }) as u64;
            let output = event
                .output_tokens
                .or_else(|| {
                    event
                        .total_tokens
                        .and_then(|total| event.input_tokens.map(|inp| total.saturating_sub(inp)))
                })
                .unwrap_or(event.total_tokens.unwrap_or(0)) as u64;
            let total = event.total_tokens.unwrap_or((input + output) as u32) as u64;

            summary.total_input_tokens += input;
            summary.total_output_tokens += output;
            summary.total_tokens += total;
            summary.total_duration_seconds += event.duration_seconds();

            update_bucket(
                summary.by_model.entry(event.model.clone()).or_default(),
                input,
                output,
                total,
                event.duration_seconds(),
            );
            update_bucket(
                summary
                    .by_modality
                    .entry(event.modality.clone())
                    .or_default(),
                input,
                output,
                total,
                event.duration_seconds(),
            );
        }
        summary
    }

    pub fn flush_summary(
        &self,
        to: &Path,
        cost: &CostEstimator,
        job: &Job,
        files: &[PathBuf],
        limits: &HashMap<&str, Option<u32>>,
        ndjson: Option<&Path>,
    ) -> anyhow::Result<()> {
        if let Some(parent) = to.parent() {
            ensure_dir(parent)?;
        }
        let summary = self.summarize();
        let events = self.events();
        let costs = cost.estimate(&events);
        let state = self.inner.lock().unwrap();
        let start = state.first_started.map(|t| t.format(&Rfc3339).unwrap());
        let end = state.last_finished.map(|t| t.format(&Rfc3339).unwrap());
        let elapsed = match (state.first_started, state.last_finished) {
            (Some(s), Some(f)) => (f - s).max(Duration::ZERO).as_seconds_f64(),
            _ => 0.0,
        };

        let payload = json!({
            "job": {
                "source": job.source,
                "kind": job.kind.map(|k| k.as_str().to_string()),
                "model": job.model,
            },
            "totals": {
                "requests": summary.total_requests,
                "input_tokens": summary.total_input_tokens,
                "output_tokens": summary.total_output_tokens,
                "est_cost_usd": (costs.total_cost * 1_000_000.0).round() / 1_000_000.0,
            },
            "time": {
                "start": start,
                "end": end,
                "elapsed_sec": elapsed,
            },
            "limits": limits.iter().map(|(k, v)| (k.to_string(), v)).collect::<HashMap<_, _>>(),
            "files": files.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
            "warnings": if costs.estimated { vec!["costs include estimates".to_string()] } else { Vec::new() },
            "notes": state.notes.clone(),
        });

        let mut file = File::create(to)?;
        file.write_all(serde_json::to_string_pretty(&payload)?.as_bytes())?;

        if let Some(ndjson_path) = ndjson {
            if let Some(parent) = ndjson_path.parent() {
                ensure_dir(parent)?;
            }
            let mut ndjson_file = File::create(ndjson_path)?;
            for event in events {
                let line = json!({
                    "model": event.model,
                    "modality": event.modality,
                    "chunk_index": event.metadata.get("chunk_index"),
                    "start_utc": event.started_at.format(&Rfc3339).ok(),
                    "end_utc": event.finished_at.format(&Rfc3339).ok(),
                    "latency_ms": (event.duration_seconds() * 1000.0).round() as i64,
                    "tokens_in": event.input_tokens,
                    "tokens_out": event.output_tokens,
                    "video_start": event.metadata.get("chunk_start_seconds"),
                    "video_end": event.metadata.get("chunk_end_seconds"),
                    "file_uri": event.metadata.get("file_uri"),
                    "manifest_path": event.metadata.get("manifest_path"),
                    "response_path": event.metadata.get("response_path"),
                });
                ndjson_file.write_all(serde_json::to_string(&line)?.as_bytes())?;
                ndjson_file.write_all(b"\n")?;
            }
        }
        Ok(())
    }
}

fn update_bucket(bucket: &mut SummaryBucket, input: u64, output: u64, total: u64, duration: f64) {
    bucket.requests += 1;
    bucket.input_tokens += input;
    bucket.output_tokens += output;
    bucket.total_tokens += total;
    bucket.total_duration_seconds += duration;
}
