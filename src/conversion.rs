use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use glob::Pattern;
use rand::Rng;
use reqwest::blocking::Client;
use reqwest::StatusCode;
use serde_json::{json, Map, Value};
use time::OffsetDateTime;
use walkdir::WalkDir;

use crate::quota::QuotaMonitor;
use crate::telemetry::{RequestEvent, RunMonitor};

pub struct LatexConverter {
    http: Client,
    api_key: String,
    monitor: RunMonitor,
    quota: Option<QuotaMonitor>,
}

const MAX_RETRIES: usize = 3;
const BACKOFF_BASE_SECONDS: f64 = 1.0;
const BACKOFF_CAP_SECONDS: f64 = 8.0;

impl LatexConverter {
    pub fn new(api_key: String, monitor: RunMonitor, quota: Option<QuotaMonitor>) -> Result<Self> {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()?;
        Ok(Self {
            http: client,
            api_key,
            monitor,
            quota,
        })
    }

    pub fn latex_to_markdown(
        &self,
        model: &str,
        prompt: &str,
        latex_text: &str,
        metadata: Map<String, Value>,
    ) -> Result<String> {
        if latex_text.trim().is_empty() {
            return Ok(String::new());
        }
        let body_text = format!("Instructions:\n{prompt}\n\nLaTeX:\n{latex_text}");
        self.generate(model, &body_text, "latex_to_markdown", metadata)
    }

    pub fn latex_to_json(
        &self,
        model: &str,
        prompt: &str,
        latex_text: &str,
        metadata: Map<String, Value>,
    ) -> Result<String> {
        if latex_text.trim().is_empty() {
            return Ok("[]".to_string());
        }
        let body_text = format!("Instructions:\n{prompt}\n\n```\n{latex_text}\n```");
        self.generate(model, &body_text, "latex_to_json", metadata)
    }

    fn generate(
        &self,
        model: &str,
        user_text: &str,
        modality: &str,
        metadata: Map<String, Value>,
    ) -> Result<String> {
        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent",
            model
        );
        let request_body = json!({
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": user_text}
                    ]
                }
            ]
        });

        let (payload, started, finished, retries) = {
            let mut attempt = 0;
            let mut retries = 0;
            loop {
                self.apply_quota_delay(model);
                let started_at = OffsetDateTime::now_utc();
                match self
                    .http
                    .post(&url)
                    .query(&[("key", self.api_key.as_str())])
                    .json(&request_body)
                    .send()
                {
                    Ok(resp) => {
                        if resp.status().is_success() {
                            let finished_at = OffsetDateTime::now_utc();
                            let payload: Value =
                                resp.json().context("parsing generateContent response")?;
                            break (payload, started_at, finished_at, retries);
                        }

                        if should_retry_status(resp.status()) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.generateContent",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "status": resp.status().as_u16(),
                                    "model": model,
                                    "operation": modality,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            retries += 1;
                            continue;
                        }

                        let status = resp.status();
                        let text = resp.text().unwrap_or_default();
                        return Err(anyhow!(
                            "generateContent failed with status {}: {}",
                            status,
                            text
                        ));
                    }
                    Err(err) => {
                        if is_retryable_error(&err) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.generateContent",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "error": err.to_string(),
                                    "model": model,
                                    "operation": modality,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            retries += 1;
                            continue;
                        }
                        return Err(err).context("calling generateContent");
                    }
                }
            }
        };

        let text =
            extract_text(&payload).ok_or_else(|| anyhow!("response missing candidate text"))?;
        let usage = payload.get("usageMetadata");
        let (input_tokens, output_tokens, total_tokens) = extract_usage(usage);

        let mut meta_value = metadata.clone();
        meta_value.insert("operation".into(), Value::String(modality.to_string()));
        meta_value.insert("retries".into(), Value::from(retries as u64));
        let metadata_map: HashMap<String, Value> = meta_value.into_iter().collect();

        let event = RequestEvent {
            model: model.to_string(),
            modality: modality.to_string(),
            started_at: started,
            finished_at: finished,
            input_tokens,
            output_tokens,
            total_tokens,
            metadata: metadata_map.clone(),
        };
        self.monitor.record(event.clone());
        if let Some(quota) = &self.quota {
            quota.register_tokens(model, event.total_tokens);
        }

        Ok(text.trim().to_string())
    }

    fn apply_quota_delay(&self, bucket: &str) {
        if let Some(quota) = &self.quota {
            if let Some(delay) = quota.register_request(bucket) {
                if !delay.is_zero() {
                    self.monitor.note_event(
                        "quota.sleep",
                        json!({
                            "bucket": bucket,
                            "delay_ms": delay.as_millis(),
                        }),
                    );
                    thread::sleep(delay);
                }
            }
        }
    }
}

fn extract_text(payload: &Value) -> Option<String> {
    let candidates = payload.get("candidates")?.as_array()?;
    let first = candidates.first()?;
    let content = first.get("content")?.get("parts")?.as_array()?;
    let mut pieces = Vec::new();
    for part in content {
        if let Some(text) = part.get("text").and_then(|t| t.as_str()) {
            pieces.push(text);
        }
    }
    if pieces.is_empty() {
        None
    } else {
        Some(pieces.join("\n"))
    }
}

fn should_retry_status(status: StatusCode) -> bool {
    status == StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
}

fn is_retryable_error(err: &reqwest::Error) -> bool {
    if let Some(status) = err.status() {
        if should_retry_status(status) {
            return true;
        }
    }
    err.is_timeout() || err.is_connect() || err.is_request()
}

fn backoff_delay(attempt: usize) -> Duration {
    let exp = BACKOFF_BASE_SECONDS * 2f64.powi(attempt as i32);
    let capped = exp.min(BACKOFF_CAP_SECONDS);
    let mut rng = rand::thread_rng();
    let jitter: f64 = rng.gen_range(0.8..=1.2);
    Duration::from_secs_f64((capped * jitter).min(BACKOFF_CAP_SECONDS))
}

fn extract_usage(usage: Option<&Value>) -> (Option<u32>, Option<u32>, Option<u32>) {
    let Some(usage) = usage else {
        return (None, None, None);
    };
    let prompt = usage
        .get("promptTokenCount")
        .or_else(|| usage.get("inputTokenCount"))
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);
    let output = usage
        .get("candidatesTokenCount")
        .or_else(|| usage.get("outputTokenCount"))
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);
    let total = usage
        .get("totalTokenCount")
        .or_else(|| usage.get("totalTokens"))
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);
    (prompt, output, total)
}

pub fn collect_tex_files(source: &Path, pattern: &str, recursive: bool) -> Result<Vec<PathBuf>> {
    if source.is_file() {
        return Ok(vec![source.to_path_buf()]);
    }
    if !source.is_dir() {
        bail!("source {} is not a file or directory", source.display());
    }
    let glob = Pattern::new(pattern).with_context(|| format!("invalid pattern '{}'", pattern))?;
    let mut files = Vec::new();
    if recursive {
        for entry in WalkDir::new(source)
            .into_iter()
            .filter_map(|entry| entry.ok())
        {
            if entry.file_type().is_file()
                && glob.matches(entry.file_name().to_string_lossy().as_ref())
            {
                files.push(entry.into_path());
            }
        }
    } else {
        for entry in fs::read_dir(source)? {
            let entry = entry?;
            let path = entry.path();
            if path.is_file()
                && glob.matches(
                    path.file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .as_ref(),
                )
            {
                files.push(path);
            }
        }
    }
    files.sort();
    Ok(files)
}
