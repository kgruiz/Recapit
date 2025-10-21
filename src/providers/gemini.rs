use std::cmp;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use rand::{thread_rng, Rng};
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderValue, CONTENT_LENGTH, CONTENT_TYPE};
use reqwest::StatusCode;
use serde_json::{json, Map, Value};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

use crate::constants::{model_capabilities, DEFAULT_MODEL};
use crate::core::{Asset, Provider, SourceKind};
use crate::telemetry::{RequestEvent, RunMonitor};
use crate::utils::ensure_dir;

const INLINE_THRESHOLD_BYTES: usize = 20 * 1024 * 1024;
const MAX_RETRIES: u32 = 3;
const BACKOFF_BASE_SECS: f64 = 1.0;
const BACKOFF_CAP_SECS: f64 = 8.0;
const FILE_POLL_INTERVAL_SECS: f64 = 2.0;
const FILE_POLL_TIMEOUT_SECS: f64 = 600.0;

pub struct GeminiProvider {
    api_key: String,
    model: String,
    http: Client,
    monitor: RunMonitor,
    upload_cache: Mutex<HashMap<String, CachedUpload>>,
    quota: Option<crate::quota::QuotaMonitor>,
    cleanup_queue: Mutex<HashMap<String, CleanupEntry>>,
}

#[derive(Clone)]
struct CachedUpload {
    uri: String,
    mime_type: String,
    name: Option<String>,
}

#[derive(Clone)]
struct CleanupEntry {
    uri: String,
}

impl GeminiProvider {
    pub fn new(
        api_key: String,
        model: String,
        monitor: RunMonitor,
        quota: Option<crate::quota::QuotaMonitor>,
    ) -> Self {
        let http = Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()
            .expect("failed to build reqwest client");
        Self {
            api_key,
            model,
            http,
            monitor,
            upload_cache: Mutex::new(HashMap::new()),
            quota,
            cleanup_queue: Mutex::new(HashMap::new()),
        }
    }

    fn part_for_asset(&self, asset: &Asset) -> Result<(Value, Map<String, Value>)> {
        let mut metadata = Map::new();
        if let Some(obj) = asset.meta.as_object() {
            for (key, value) in obj {
                metadata.insert(key.clone(), value.clone());
            }
        }

        let mime = asset
            .mime
            .clone()
            .or_else(|| {
                mime_guess::from_path(&asset.path)
                    .first_raw()
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "application/octet-stream".to_string());

        if asset.source_kind == SourceKind::Youtube
            && asset.meta.get("pass_through").and_then(|v| v.as_bool()) == Some(true)
        {
            if let Some(url) = asset
                .meta
                .get("source_url")
                .and_then(|value| value.as_str())
            {
                metadata.insert("file_uri".into(), Value::String(url.to_string()));
                let part = json!({
                    "file_data": {
                        "file_uri": url,
                        "mime_type": mime,
                    }
                });
                return Ok((part, metadata));
            }
        }

        if let Some(inline_bytes) = asset
            .meta
            .get("inline_bytes")
            .and_then(|value| value.as_str())
        {
            let part = json!({
                "inline_data": {
                    "data": inline_bytes,
                    "mime_type": mime,
                }
            });
            return Ok((part, metadata));
        }

        let bytes = fs::read(&asset.path)
            .with_context(|| format!("reading asset {}", asset.path.display()))?;
        if bytes.len() <= INLINE_THRESHOLD_BYTES {
            let encoded = BASE64.encode(&bytes);
            let part = json!({
                "inline_data": {
                    "data": encoded,
                    "mime_type": mime,
                }
            });
            return Ok((part, metadata));
        }

        if let Some(key) = asset.meta.get("upload_cache_key").and_then(|v| v.as_str()) {
            if let Some(cached) = self.upload_cache.lock().unwrap().get(key).cloned() {
                let part = json!({
                    "file_data": {
                        "file_uri": cached.uri,
                        "mime_type": cached.mime_type,
                    }
                });
                metadata.insert("file_uri".into(), Value::String(cached.uri));
                if let Some(name) = cached.name.as_ref() {
                    metadata.insert("file_name".into(), Value::String(name.clone()));
                }
                return Ok((part, metadata));
            }
        }

        let upload = self.upload_file(asset, &bytes, &mime)?;
        if let Some(cache_key) = asset.meta.get("upload_cache_key").and_then(|v| v.as_str()) {
            self.upload_cache.lock().unwrap().insert(
                cache_key.to_string(),
                CachedUpload {
                    uri: upload.uri.clone(),
                    mime_type: upload.mime_type.clone(),
                    name: upload.name.clone(),
                },
            );
        }
        metadata.insert("file_uri".into(), Value::String(upload.uri.clone()));
        if let Some(name) = upload.name.as_ref() {
            metadata.insert("file_name".into(), Value::String(name.clone()));
        }
        let part = json!({
            "file_data": {
                "file_uri": upload.uri,
                "mime_type": upload.mime_type,
            }
        });
        Ok((part, metadata))
    }

    fn upload_file(&self, asset: &Asset, bytes: &[u8], mime: &str) -> Result<CachedUpload> {
        let start_url = format!(
            "https://generativelanguage.googleapis.com/v1beta/files:upload?key={}",
            self.api_key
        );

        let display_name = asset
            .path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or_else(|| "upload");

        let mut headers = HeaderMap::new();
        headers.insert(
            "X-Goog-Upload-Protocol",
            HeaderValue::from_static("resumable"),
        );
        headers.insert("X-Goog-Upload-Command", HeaderValue::from_static("start"));
        let start_length = bytes.len().to_string();
        headers.insert(
            "X-Goog-Upload-Header-Content-Length",
            HeaderValue::from_str(&start_length)?,
        );
        headers.insert(
            "X-Goog-Upload-Header-Content-Type",
            HeaderValue::from_str(mime)?,
        );

        let start_body = json!({"file": {"display_name": display_name}});
        let (start_resp, start_retries) =
            self.send_with_retry("files.upload.start", Some("files"), || {
                let builder = self
                    .http
                    .post(&start_url)
                    .headers(headers.clone())
                    .json(&start_body);
                builder.send().context("starting resumable upload")
            })?;

        let upload_url = start_resp
            .headers()
            .get("X-Goog-Upload-URL")
            .or_else(|| start_resp.headers().get("x-goog-upload-url"))
            .ok_or_else(|| anyhow!("missing X-Goog-Upload-URL header"))?
            .to_str()
            .context("parsing upload URL header")?
            .to_string();

        let mut upload_headers = HeaderMap::new();
        upload_headers.insert(
            "X-Goog-Upload-Command",
            HeaderValue::from_static("upload, finalize"),
        );
        upload_headers.insert("X-Goog-Upload-Offset", HeaderValue::from_static("0"));
        upload_headers.insert(CONTENT_TYPE, HeaderValue::from_str(mime)?);
        let upload_length = bytes.len().to_string();
        upload_headers.insert(CONTENT_LENGTH, HeaderValue::from_str(&upload_length)?);

        let guard = match &self.quota {
            Some(quota) => {
                Some(quota.track_upload(&asset.path.to_string_lossy(), bytes.len() as u64)?)
            }
            None => None,
        };
        let payload = bytes.to_vec();
        let (finalize_resp, finalize_retries) =
            self.send_with_retry("files.upload.finalize", Some("files"), || {
                let builder = self
                    .http
                    .post(&upload_url)
                    .headers(upload_headers.clone())
                    .body(payload.clone());
                builder.send().context("uploading file data")
            })?;

        drop(guard);

        let value: Value = finalize_resp.json().context("decoding upload response")?;
        let mut file_obj = value
            .get("file")
            .and_then(|f| f.as_object())
            .cloned()
            .ok_or_else(|| anyhow!("upload response missing file object"))?;

        if let Some(state) = file_obj.get("state").and_then(|v| v.as_str()) {
            if matches!(state, "PROCESSING" | "INTERNAL") {
                if let Some(name) = file_obj.get("name").and_then(|v| v.as_str()) {
                    let active = self.await_active_file(name)?;
                    if let Some(obj) = active.as_object() {
                        file_obj = obj.clone();
                    }
                }
            }
        }

        let uri = file_obj
            .get("uri")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow!("upload response missing file.uri"))?
            .to_string();
        let name = file_obj
            .get("name")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow!("upload response missing file.name"))?
            .to_string();

        self.cleanup_queue
            .lock()
            .unwrap()
            .entry(name.clone())
            .or_insert_with(|| CleanupEntry { uri: uri.clone() });

        if start_retries > 0 || finalize_retries > 0 {
            self.monitor.note_event(
                "gemini.retry_summary",
                json!({
                    "operation": "files.upload",
                    "start_retries": start_retries,
                    "finalize_retries": finalize_retries,
                    "path": asset.path.to_string_lossy(),
                }),
            );
        }

        Ok(CachedUpload {
            uri,
            mime_type: mime.to_string(),
            name: Some(name),
        })
    }

    fn generate(
        &self,
        instruction: &str,
        assets: &[&Asset],
        modality: &str,
        meta: &Value,
    ) -> Result<(String, Vec<Map<String, Value>>)> {
        let mut parts = Vec::new();
        let mut asset_metadata = Vec::new();
        let mut event_metadata = meta.as_object().cloned().unwrap_or_default();

        for asset in assets {
            let (part, metadata) = self.part_for_asset(asset)?;
            for (key, value) in metadata.iter() {
                event_metadata.entry(key.clone()).or_insert(value.clone());
            }
            asset_metadata.push(metadata);
            parts.push(part);
        }
        parts.push(json!({"text": instruction}));

        let request = json!({
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ]
        });

        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent",
            self.model
        );

        let started = OffsetDateTime::now_utc();
        let (response, retry_count) =
            self.send_with_retry("models.generateContent", Some(&self.model), || {
                let payload = request.clone();
                let builder = self
                    .http
                    .post(&url)
                    .query(&[("key", self.api_key.as_str())])
                    .json(&payload);
                builder.send().context("calling generateContent")
            })?;

        let finished = OffsetDateTime::now_utc();
        let payload: Value = response
            .json()
            .context("parsing generateContent response")?;
        let text = payload
            .get("candidates")
            .and_then(|candidates| candidates.as_array())
            .and_then(|array| array.first())
            .and_then(|cand| cand.get("content"))
            .and_then(|content| content.get("parts"))
            .and_then(|parts| parts.as_array())
            .map(|parts| {
                parts
                    .iter()
                    .filter_map(|part| part.get("text").and_then(|t| t.as_str()))
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_default();

        let usage = payload.get("usageMetadata");
        let input_tokens = usage
            .and_then(|u| u.get("promptTokenCount"))
            .and_then(|v| v.as_u64())
            .map(|v| v as u32);
        let output_tokens = usage
            .and_then(|u| u.get("candidatesTokenCount"))
            .and_then(|v| v.as_u64())
            .map(|v| v as u32);
        let total_tokens = usage
            .and_then(|u| u.get("totalTokenCount"))
            .and_then(|v| v.as_u64())
            .map(|v| v as u32);

        let asset_values: Vec<Value> = asset_metadata
            .iter()
            .map(|meta| Value::Object(meta.clone()))
            .collect();
        event_metadata.insert("assets".into(), Value::Array(asset_values));
        if let Some(uri) = asset_metadata
            .iter()
            .find_map(|meta| meta.get("file_uri").and_then(|v| v.as_str()))
        {
            event_metadata
                .entry("file_uri".to_string())
                .or_insert(Value::String(uri.to_string()));
        }
        event_metadata.insert("retry_count".into(), Value::from(retry_count));
        if let Some(first_meta) = asset_metadata.first_mut() {
            first_meta.insert("retry_count".into(), Value::from(retry_count));
        }
        if retry_count > 0 {
            self.monitor.note_event(
                "gemini.retry_summary",
                json!({
                    "operation": "models.generateContent",
                    "retries": retry_count,
                    "model": self.model,
                    "modality": modality,
                }),
            );
        }

        let metadata_map: HashMap<String, Value> = event_metadata.clone().into_iter().collect();
        let event = RequestEvent {
            model: self.model.clone(),
            modality: modality.to_string(),
            started_at: started,
            finished_at: finished,
            input_tokens,
            output_tokens,
            total_tokens,
            metadata: metadata_map,
        };
        self.monitor.record(event.clone());
        if let Some(quota) = &self.quota {
            quota.register_tokens(&self.model, event.total_tokens);
        }

        Ok((text, asset_metadata))
    }

    fn transcribe_chunks(
        &self,
        instruction: &str,
        assets: &[&Asset],
        modality: &str,
        meta: &Value,
    ) -> Result<String> {
        if assets.is_empty() {
            return Ok(String::new());
        }

        let base = meta_string(meta, "output_base")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("output"));
        let name = meta_string(meta, "output_name").unwrap_or_else(|| "output".into());
        let mut skip_existing = meta_bool(meta, "skip_existing").unwrap_or(false);
        let save_full_response = meta_bool(meta, "save_full_response").unwrap_or(false);
        let save_intermediates = meta_bool(meta, "save_intermediates").unwrap_or(false);
        if !save_full_response {
            skip_existing = false;
        }
        let chunk_dir = if save_full_response {
            let dir = base.join("full-response").join("chunks");
            ensure_dir(&dir)?;
            Some(dir)
        } else {
            None
        };

        let manifest_path = assets
            .iter()
            .filter_map(|asset| meta_string(&asset.meta, "manifest_path"))
            .map(PathBuf::from)
            .next()
            .unwrap_or_else(|| base.join("chunks.json"));

        let mut manifest = load_manifest(&manifest_path);
        let mut chunk_index_lookup = HashMap::new();
        {
            let chunks = manifest_chunks(&mut manifest)?;
            for (idx, entry) in chunks.iter().enumerate() {
                if let Some(index) = entry.get("index").and_then(|v| v.as_u64()) {
                    chunk_index_lookup.insert(index, idx);
                }
            }
        }

        struct ChunkEntry {
            asset: Asset,
            chunk_index: u64,
            manifest_idx: usize,
            response_path: Option<PathBuf>,
            chunk_meta: Value,
            text: Option<String>,
            event_assets: Vec<Map<String, Value>>,
            skipped: bool,
        }

        let mut entries: Vec<ChunkEntry> = Vec::new();
        let mut job_indices = Vec::new();
        for asset in assets {
            let chunk_index = meta_u64(&asset.meta, "chunk_index").unwrap_or(0);
            let entry_idx = match chunk_index_lookup.get(&chunk_index).copied() {
                Some(idx) => idx,
                None => {
                    let idx = {
                        let chunks = manifest_chunks(&mut manifest)?;
                        chunks.push(Value::Object(Map::new()));
                        chunks.len() - 1
                    };
                    chunk_index_lookup.insert(chunk_index, idx);
                    self.monitor.note_event(
                        "manifest.warning",
                        json!({
                            "chunk_index": chunk_index,
                            "manifest_path": manifest_path.to_string_lossy(),
                            "reason": "missing_chunk_entry",
                        }),
                    );
                    idx
                }
            };

            if save_intermediates {
                let chunks = manifest_chunks(&mut manifest)?;
                let entry = chunks.get_mut(entry_idx).unwrap();
                let entry_obj = entry
                    .as_object_mut()
                    .ok_or_else(|| anyhow!("manifest chunk entry not object"))?;
                entry_obj.insert("index".into(), Value::from(chunk_index));
                entry_obj.insert(
                    "path".into(),
                    Value::String(asset.path.to_string_lossy().to_string()),
                );
                entry_obj.insert(
                    "start_seconds".into(),
                    meta_f64(&asset.meta, "chunk_start_seconds")
                        .map(Value::from)
                        .unwrap_or(Value::Null),
                );
                entry_obj.insert(
                    "end_seconds".into(),
                    meta_f64(&asset.meta, "chunk_end_seconds")
                        .map(Value::from)
                        .unwrap_or(Value::Null),
                );
                entry_obj.insert("status".into(), Value::String("pending".into()));
            }

            let response_path = if let Some(dir) = &chunk_dir {
                let path = {
                    let chunks = manifest_chunks(&mut manifest)?;
                    let entry = chunks.get_mut(entry_idx).unwrap();
                    let entry_obj = entry
                        .as_object_mut()
                        .ok_or_else(|| anyhow!("manifest chunk entry not object"))?;
                    let path = entry_obj
                        .get("response_path")
                        .and_then(|v| v.as_str())
                        .map(PathBuf::from)
                        .unwrap_or_else(|| dir.join(format!("{name}-chunk{chunk_index:02}.txt")));
                    entry_obj.insert(
                        "response_path".into(),
                        Value::String(path.to_string_lossy().to_string()),
                    );
                    path
                };
                Some(path)
            } else {
                None
            };

            let mut chunk_meta_map = meta.as_object().cloned().unwrap_or_default();
            chunk_meta_map.insert("chunk_index".into(), Value::from(chunk_index));
            chunk_meta_map.insert("chunk_total".into(), Value::from(assets.len() as u64));
            if let Some(start) = meta_f64(&asset.meta, "chunk_start_seconds") {
                chunk_meta_map.insert("chunk_start_seconds".into(), Value::from(start));
            }
            if let Some(end) = meta_f64(&asset.meta, "chunk_end_seconds") {
                chunk_meta_map.insert("chunk_end_seconds".into(), Value::from(end));
            }
            chunk_meta_map.insert(
                "manifest_path".into(),
                Value::String(manifest_path.to_string_lossy().to_string()),
            );
            if let Some(path) = response_path.as_ref() {
                chunk_meta_map.insert(
                    "response_path".into(),
                    Value::String(path.to_string_lossy().to_string()),
                );
            }

            let mut entry = ChunkEntry {
                asset: (*asset).clone(),
                chunk_index,
                manifest_idx: entry_idx,
                response_path,
                chunk_meta: Value::Object(chunk_meta_map),
                text: None,
                event_assets: Vec::new(),
                skipped: false,
            };

            let entry_slot = entries.len();
            if skip_existing {
                if let Some(path) = entry.response_path.as_ref() {
                    if path.exists() {
                        let text = fs::read_to_string(path)?;
                        entry.text = Some(text.trim().to_string());
                        entry.skipped = true;
                        if save_intermediates {
                            let chunks = manifest_chunks(&mut manifest)?;
                            let manifest_entry = chunks.get_mut(entry_idx).unwrap();
                            let entry_obj = manifest_entry
                                .as_object_mut()
                                .ok_or_else(|| anyhow!("manifest chunk entry not object"))?;
                            entry_obj.insert("status".into(), Value::String("skipped".into()));
                        }
                        self.monitor.note_event(
                            "chunk.skip",
                            json!({
                                "chunk_index": chunk_index,
                                "manifest_path": manifest_path.to_string_lossy(),
                                "response_path": path,
                            }),
                        );
                        entries.push(entry);
                        continue;
                    }
                }
            }

            if save_intermediates {
                let chunks = manifest_chunks(&mut manifest)?;
                let manifest_entry = chunks.get_mut(entry_idx).unwrap();
                let entry_obj = manifest_entry
                    .as_object_mut()
                    .ok_or_else(|| anyhow!("manifest chunk entry not object"))?;
                entry_obj.insert("status".into(), Value::String("processing".into()));
            }

            job_indices.push(entry_slot);
            entries.push(entry);
        }

        let worker_limit = meta_u64(meta, "max_video_workers")
            .or_else(|| meta_u64(meta, "max_workers"))
            .unwrap_or(1);
        let worker_limit = cmp::max(usize::try_from(worker_limit).unwrap_or(usize::MAX), 1);

        if !job_indices.is_empty() {
            let pool = ThreadPoolBuilder::new()
                .num_threads(worker_limit)
                .build()
                .context("building Gemini chunk worker pool")?;
            let results = pool.install(|| {
                job_indices
                    .par_iter()
                    .map(|&idx| -> Result<(usize, String, Vec<Map<String, Value>>)> {
                        let entry = &entries[idx];
                        let asset_ref = [&entry.asset];
                        let (text, event_assets) =
                            self.generate(instruction, &asset_ref, modality, &entry.chunk_meta)?;
                        Ok((idx, text, event_assets))
                    })
                    .collect::<Result<Vec<_>>>()
            })?;

            for (idx, text, event_assets) in results {
                let entry = entries
                    .get_mut(idx)
                    .ok_or_else(|| anyhow!("missing chunk entry"))?;
                if let Some(path) = entry.response_path.as_ref() {
                    save_chunk_text(path, &text)?;
                }
                entry.text = Some(text.trim().to_string());
                entry.event_assets = event_assets;
            }
        }

        entries.sort_by_key(|entry| entry.chunk_index);
        let mut responses = Vec::new();
        for entry in entries.iter_mut() {
            let text = entry
                .text
                .as_ref()
                .ok_or_else(|| anyhow!("missing chunk text for index {}", entry.chunk_index))?
                .trim()
                .to_string();
            if save_intermediates {
                let chunks = manifest_chunks(&mut manifest)?;
                let manifest_entry = chunks
                    .get_mut(entry.manifest_idx)
                    .ok_or_else(|| anyhow!("manifest entry missing"))?;
                let entry_obj = manifest_entry
                    .as_object_mut()
                    .ok_or_else(|| anyhow!("manifest chunk entry not object"))?;
                let status = if entry.skipped { "skipped" } else { "done" };
                entry_obj.insert("status".into(), Value::String(status.into()));
                if let Some(path) = entry.response_path.as_ref() {
                    entry_obj.insert(
                        "response_path".into(),
                        Value::String(path.to_string_lossy().to_string()),
                    );
                }
                if let Some(file_uri) = entry
                    .event_assets
                    .first()
                    .and_then(|meta| meta.get("file_uri"))
                    .and_then(|v| v.as_str())
                {
                    entry_obj.insert("file_uri".into(), Value::String(file_uri.to_string()));
                }
                if let Some(file_name) = entry
                    .event_assets
                    .first()
                    .and_then(|meta| meta.get("file_name"))
                    .and_then(|v| v.as_str())
                {
                    entry_obj.insert("file_name".into(), Value::String(file_name.to_string()));
                }
                if let Some(retries) = entry
                    .event_assets
                    .first()
                    .and_then(|meta| meta.get("retry_count"))
                    .cloned()
                {
                    entry_obj.insert("retry_count".into(), retries);
                }
            }
            responses.push(text);
        }

        if save_intermediates {
            write_manifest(&manifest_path, &mut manifest)?;
        }

        Ok(responses.join("\n\n"))
    }
    fn send_with_retry<F>(
        &self,
        operation: &str,
        quota_key: Option<&str>,
        mut make_request: F,
    ) -> Result<(reqwest::blocking::Response, u32)>
    where
        F: FnMut() -> Result<reqwest::blocking::Response>,
    {
        let mut attempt: u32 = 0;
        loop {
            if let Some(key) = quota_key {
                self.apply_quota_delay(key, operation);
            }

            match make_request() {
                Ok(response) => {
                    let status = response.status();
                    if status.is_success() {
                        return Ok((response, attempt));
                    }

                    if attempt >= MAX_RETRIES || !should_retry_status(status) {
                        let body = response.text().unwrap_or_default();
                        return Err(anyhow!(
                            "{operation} failed with status {}: {}",
                            status,
                            body
                        ));
                    }

                    let delay = backoff_delay(attempt);
                    tracing::warn!(
                        operation,
                        attempt = attempt + 1,
                        wait_seconds = delay.as_secs_f64(),
                        status = status.as_u16(),
                        "retrying Gemini request"
                    );
                    self.monitor.note_event(
                        "gemini.retry",
                        json!({
                            "operation": operation,
                            "status": status.as_u16(),
                            "attempt": attempt + 1,
                            "wait_seconds": delay.as_secs_f64(),
                        }),
                    );
                    thread::sleep(delay);
                    attempt += 1;
                }
                Err(err) => {
                    if attempt >= MAX_RETRIES {
                        return Err(err);
                    }
                    let delay = backoff_delay(attempt);
                    tracing::warn!(
                        operation,
                        attempt = attempt + 1,
                        wait_seconds = delay.as_secs_f64(),
                        error = %err,
                        "retrying Gemini request"
                    );
                    self.monitor.note_event(
                        "gemini.retry",
                        json!({
                            "operation": operation,
                            "error": err.to_string(),
                            "attempt": attempt + 1,
                            "wait_seconds": delay.as_secs_f64(),
                        }),
                    );
                    thread::sleep(delay);
                    attempt += 1;
                }
            }
        }
    }

    fn apply_quota_delay(&self, key: &str, operation: &str) {
        if let Some(quota) = &self.quota {
            if let Some(delay) = quota.register_request(key) {
                if delay > Duration::ZERO {
                    self.monitor.note_event(
                        "gemini.quota_wait",
                        json!({
                            "operation": operation,
                            "resource": key,
                            "wait_seconds": delay.as_secs_f64(),
                        }),
                    );
                    thread::sleep(delay);
                }
            }
        }
    }

    fn await_active_file(&self, name: &str) -> Result<Value> {
        let deadline = Instant::now() + Duration::from_secs_f64(FILE_POLL_TIMEOUT_SECS);
        let url = format!("https://generativelanguage.googleapis.com/v1beta/{}", name);
        loop {
            if Instant::now() > deadline {
                bail!("Timed out waiting for file {name} to become ACTIVE");
            }

            let (response, _) = self.send_with_retry("files.get", Some("files"), || {
                let builder = self.http.get(&url).query(&[("key", self.api_key.as_str())]);
                builder.send().context("polling file state")
            })?;
            let payload: Value = response.json().context("decoding file status")?;
            let state = payload
                .get("state")
                .and_then(|v| v.as_str())
                .unwrap_or("ACTIVE");

            match state {
                "ACTIVE" => return Ok(payload),
                "PROCESSING" | "INTERNAL" => {
                    self.monitor
                        .note_event("gemini.file_wait", json!({ "file": name, "state": state }));
                    thread::sleep(Duration::from_secs_f64(FILE_POLL_INTERVAL_SECS));
                }
                other => {
                    bail!("File {name} failed with state {other}");
                }
            }
        }
    }

    fn cleanup_uploads(&self) -> Result<()> {
        let entries: Vec<(String, CleanupEntry)> = {
            let mut queue = self.cleanup_queue.lock().unwrap();
            queue.drain().collect()
        };

        for (name, entry) in entries {
            let CleanupEntry { uri } = entry;
            let delete_url = format!(
                "https://generativelanguage.googleapis.com/v1beta/{}?key={}",
                name, self.api_key
            );
            let file_name = name.clone();
            let file_uri = uri.clone();
            match self.send_with_retry("files.delete", Some("files"), || {
                let builder = self.http.delete(&delete_url);
                builder.send().context("deleting uploaded file")
            }) {
                Ok((_resp, retries)) => {
                    self.monitor.note_event(
                        "gemini.cleanup",
                        json!({
                            "file_name": file_name.clone(),
                            "file_uri": file_uri.clone(),
                            "retries": retries,
                        }),
                    );
                }
                Err(err) => {
                    tracing::warn!(
                        file_name = %file_name,
                        file_uri = %file_uri,
                        error = %err,
                        "failed to delete Gemini upload"
                    );
                    self.monitor.note_event(
                        "gemini.cleanup_failed",
                        json!({
                            "file_name": file_name.clone(),
                            "file_uri": file_uri,
                            "error": err.to_string(),
                        }),
                    );
                }
            }
        }

        Ok(())
    }
}

impl Provider for GeminiProvider {
    fn supports(&self, capability: &str) -> bool {
        let table = model_capabilities();
        table
            .get(self.model.as_str())
            .or_else(|| table.get(DEFAULT_MODEL))
            .map(|caps| caps.iter().any(|cap| *cap == capability))
            .unwrap_or(true)
    }

    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        meta: &serde_json::Value,
    ) -> Result<String> {
        let mut chunk_assets: Vec<&Asset> = assets
            .iter()
            .filter(|asset| meta_u64(&asset.meta, "chunk_index").is_some())
            .collect();
        if !chunk_assets.is_empty() {
            chunk_assets.sort_by_key(|asset| meta_u64(&asset.meta, "chunk_index").unwrap_or(0));
            return self.transcribe_chunks(instruction, &chunk_assets, modality, meta);
        }

        let asset_refs: Vec<&Asset> = assets.iter().collect();
        let (text, _) = self.generate(instruction, &asset_refs, modality, meta)?;
        Ok(text)
    }

    fn cleanup(&self) -> Result<()> {
        self.cleanup_uploads()
    }
}

fn meta_u64(value: &Value, key: &str) -> Option<u64> {
    value.as_object()?.get(key)?.as_u64()
}

fn meta_f64(value: &Value, key: &str) -> Option<f64> {
    value.as_object()?.get(key)?.as_f64()
}

fn meta_bool(value: &Value, key: &str) -> Option<bool> {
    value.as_object()?.get(key)?.as_bool()
}

fn meta_string(value: &Value, key: &str) -> Option<String> {
    value.as_object()?.get(key)?.as_str().map(|s| s.to_string())
}

fn load_manifest(path: &Path) -> Value {
    if let Ok(text) = fs::read_to_string(path) {
        serde_json::from_str(&text).unwrap_or_else(|_| json!({"version": 1, "chunks": []}))
    } else {
        json!({"version": 1, "chunks": []})
    }
}

fn manifest_chunks(manifest: &mut Value) -> Result<&mut Vec<Value>> {
    let obj = manifest
        .as_object_mut()
        .ok_or_else(|| anyhow!("manifest payload must be an object"))?;
    let entry = obj
        .entry("chunks")
        .or_insert_with(|| Value::Array(Vec::new()));
    entry
        .as_array_mut()
        .ok_or_else(|| anyhow!("manifest chunks must be an array"))
}

fn save_chunk_text(path: &Path, text: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        ensure_dir(parent)?;
    }
    let mut content = text.trim_end_matches('\n').to_string();
    content.push('\n');
    fs::write(path, content)?;
    Ok(())
}

fn should_retry_status(status: StatusCode) -> bool {
    status == StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
}

fn backoff_delay(attempt: u32) -> Duration {
    let exponential = BACKOFF_BASE_SECS * 2f64.powi(attempt as i32);
    let capped = exponential.min(BACKOFF_CAP_SECS);
    let jitter = thread_rng().gen_range(0.5..=1.5);
    let seconds = (capped * jitter).min(BACKOFF_CAP_SECS);
    Duration::from_secs_f64(seconds)
}

fn write_manifest(path: &Path, manifest: &mut Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        ensure_dir(parent)?;
    }
    let now = OffsetDateTime::now_utc().format(&Rfc3339)?;
    if let Some(obj) = manifest.as_object_mut() {
        obj.entry("created_utc")
            .or_insert_with(|| Value::String(now.clone()));
        obj.insert("updated_utc".into(), Value::String(now));
    }
    fs::write(path, serde_json::to_string_pretty(manifest)?)?;
    Ok(())
}
