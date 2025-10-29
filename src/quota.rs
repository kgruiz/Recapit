use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{bail, Result};
use tracing::warn;

#[derive(Debug, Clone)]
pub struct QuotaConfig {
    pub request_limits: HashMap<String, u32>,
    pub token_limits: HashMap<String, u32>,
    pub rpm_warn_threshold: f64,
    pub rpm_sleep_threshold: f64,
    pub token_warn_threshold: f64,
    pub storage_limit_bytes: u64,
    pub upload_limit_bytes: u64,
    pub concurrency_limit: u32,
    pub warn_cooldown: Duration,
    pub max_preemptive_sleep: Duration,
    pub request_window: Duration,
}

impl QuotaConfig {
    pub fn new(request_limits: HashMap<String, u32>, token_limits: HashMap<String, u32>) -> Self {
        Self {
            request_limits,
            token_limits,
            rpm_warn_threshold: 0.8,
            rpm_sleep_threshold: 0.9,
            token_warn_threshold: 0.8,
            storage_limit_bytes: 20 * 1024 * 1024 * 1024,
            upload_limit_bytes: 2 * 1024 * 1024 * 1024,
            concurrency_limit: 100,
            warn_cooldown: Duration::from_secs(10),
            max_preemptive_sleep: Duration::from_millis(500),
            request_window: Duration::from_secs(60),
        }
    }
}

#[derive(Default)]
struct QuotaState {
    request_windows: HashMap<String, VecDeque<Instant>>,
    last_rpm_warn: HashMap<String, Instant>,
    token_windows: HashMap<String, VecDeque<(Instant, u32)>>,
    last_token_warn: HashMap<String, Instant>,
    uploaded_bytes: u64,
    active_uploads: u32,
}

#[derive(Clone)]
pub struct QuotaMonitor {
    config: Arc<QuotaConfig>,
    state: Arc<Mutex<QuotaState>>,
}

impl QuotaMonitor {
    pub fn new(config: QuotaConfig) -> Self {
        Self {
            config: Arc::new(config),
            state: Arc::new(Mutex::new(QuotaState::default())),
        }
    }

    pub fn register_request(&self, model: &str) -> Option<Duration> {
        let per_minute = match self.config.request_limits.get(model) {
            Some(value) if *value > 0 => *value,
            _ => return None,
        };
        let mut state = self.state.lock().unwrap();
        let window = state.request_windows.entry(model.to_string()).or_default();
        let now = Instant::now();
        window.push_back(now);
        while let Some(front) = window.front() {
            if now.duration_since(*front) > self.config.request_window {
                window.pop_front();
            } else {
                break;
            }
        }
        let utilization = window.len() as f64 / per_minute as f64;
        if utilization >= self.config.rpm_warn_threshold {
            let entry = state
                .last_rpm_warn
                .entry(model.to_string())
                .or_insert(now - self.config.warn_cooldown * 2);
            if now.duration_since(*entry) >= self.config.warn_cooldown {
                warn!(
                    "model {} request rate at {:.0}% of limit ({} RPM)",
                    model,
                    utilization * 100.0,
                    per_minute
                );
                *entry = now;
            }
        }
        if utilization >= self.config.rpm_sleep_threshold {
            let window_seconds = self.config.request_window.as_secs_f64();
            let per_request = if per_minute > 0 {
                window_seconds / per_minute as f64
            } else {
                0.0
            };
            let sleep = self
                .config
                .max_preemptive_sleep
                .as_secs_f64()
                .min(per_request);
            if sleep > 0.0 {
                return Some(Duration::from_secs_f64(sleep));
            }
        }
        None
    }

    pub fn register_tokens(&self, model: &str, total_tokens: Option<u32>) {
        let Some(total_tokens) = total_tokens else {
            return;
        };
        let limit = match self.config.token_limits.get(model) {
            Some(value) if *value > 0 => *value,
            _ => return,
        };
        let mut state = self.state.lock().unwrap();
        let window = state.token_windows.entry(model.to_string()).or_default();
        let now = Instant::now();
        window.push_back((now, total_tokens));
        while let Some((instant, _)) = window.front() {
            if now.duration_since(*instant) > self.config.request_window {
                window.pop_front();
            } else {
                break;
            }
        }
        let used: u64 = window.iter().map(|(_, tokens)| *tokens as u64).sum();
        let utilization = used as f64 / limit as f64;
        if utilization >= self.config.token_warn_threshold {
            let entry = state
                .last_token_warn
                .entry(model.to_string())
                .or_insert(now - self.config.warn_cooldown * 2);
            if now.duration_since(*entry) >= self.config.warn_cooldown {
                warn!(
                    "model {} token usage at {:.0}% of per-minute quota ({} tokens/min)",
                    model,
                    utilization * 100.0,
                    limit
                );
                *entry = now;
            }
        }
    }

    pub fn track_upload(&self, path: &str, size_bytes: u64) -> Result<UploadGuard> {
        if size_bytes > self.config.upload_limit_bytes {
            bail!(
                "Upload {} exceeds per-file upload limit of {} GB",
                path,
                self.config.upload_limit_bytes as f64 / (1024.0 * 1024.0 * 1024.0)
            );
        }
        let mut state = self.state.lock().unwrap();
        state.uploaded_bytes += size_bytes;
        state.active_uploads += 1;

        let storage_util = state.uploaded_bytes as f64 / self.config.storage_limit_bytes as f64;
        if storage_util >= self.config.token_warn_threshold {
            warn!(
                "Uploads this run total {:.2} GB ({:.0}% of {:.0} GB Files API window)",
                state.uploaded_bytes as f64 / (1024.0 * 1024.0 * 1024.0),
                storage_util * 100.0,
                self.config.storage_limit_bytes as f64 / (1024.0 * 1024.0 * 1024.0)
            );
        }

        let concurrency = state.active_uploads as f64 / self.config.concurrency_limit as f64;
        if concurrency >= self.config.token_warn_threshold {
            warn!(
                "Concurrent uploads at {}/{} ({:.0}% of limit)",
                state.active_uploads,
                self.config.concurrency_limit,
                concurrency * 100.0
            );
        }

        Ok(UploadGuard {
            monitor: self.clone(),
            size_bytes,
        })
    }

    fn finish_upload(&self, size_bytes: u64) {
        let mut state = self.state.lock().unwrap();
        state.active_uploads = state.active_uploads.saturating_sub(1);
        state.uploaded_bytes = state.uploaded_bytes.saturating_sub(size_bytes);
    }
}

pub struct UploadGuard {
    monitor: QuotaMonitor,
    size_bytes: u64,
}

impl Drop for UploadGuard {
    fn drop(&mut self) {
        self.monitor.finish_upload(self.size_bytes);
    }
}
