use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use rand::Rng;
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
const MAX_RETRIES: usize = 3;
const BACKOFF_BASE_SECONDS: f64 = 1.0;
const BACKOFF_CAP_SECONDS: f64 = 8.0;

pub struct GeminiProvider {
    api_key: String,
    model: String,
    http: Client,
    monitor: RunMonitor,
    upload_cache: Mutex<HashMap<String, CachedUpload>>,
    quota: Option<crate::quota::QuotaMonitor>,
}

#[derive(Clone)]
struct CachedUpload {
    uri: String,
    mime_type: String,
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
                },
            );
        }
        metadata.insert("file_uri".into(), Value::String(upload.uri.clone()));
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
        let start_payload = json!({"file": {"display_name": display_name}});

        let upload_url = {
            let mut attempt = 0;
            loop {
                self.apply_quota_delay("files");

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

                match self
                    .http
                    .post(&start_url)
                    .headers(headers)
                    .json(&start_payload)
                    .send()
                {
                    Ok(resp) => {
                        if resp.status().is_success() {
                            if let Some(header) = resp
                                .headers()
                                .get("X-Goog-Upload-URL")
                                .or_else(|| resp.headers().get("x-goog-upload-url"))
                            {
                                break header
                                    .to_str()
                                    .context("parsing upload URL header")?
                                    .to_string();
                            }
                            return Err(anyhow!("missing X-Goog-Upload-URL header"));
                        }

                        if should_retry_status(resp.status()) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.files.upload_start",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "status": resp.status().as_u16(),
                                    "path": asset.path,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            continue;
                        }

                        let status = resp.status();
                        let text = resp.text().unwrap_or_default();
                        return Err(anyhow!(
                            "files:upload start failed with status {}: {}",
                            status,
                            text
                        ));
                    }
                    Err(err) => {
                        if is_retryable_error(&err) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.files.upload_start",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "error": err.to_string(),
                                    "path": asset.path,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            continue;
                        }
                        return Err(err).context("starting resumable upload");
                    }
                }
            }
        };

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

        let finalize_resp = {
            let mut attempt = 0;
            loop {
                self.apply_quota_delay("files");
                match self
                    .http
                    .post(&upload_url)
                    .headers(upload_headers.clone())
                    .body(bytes.to_owned())
                    .send()
                {
                    Ok(resp) => {
                        if resp.status().is_success() {
                            break resp;
                        }

                        if should_retry_status(resp.status()) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.files.upload_finalize",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "status": resp.status().as_u16(),
                                    "path": asset.path,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            continue;
                        }

                        let status = resp.status();
                        let text = resp.text().unwrap_or_default();
                        return Err(anyhow!(
                            "files:upload finalize failed with status {}: {}",
                            status,
                            text
                        ));
                    }
                    Err(err) => {
                        if is_retryable_error(&err) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.files.upload_finalize",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "error": err.to_string(),
                                    "path": asset.path,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            continue;
                        }
                        return Err(err).context("uploading file data");
                    }
                }
            }
        };

        drop(guard);

        let response_value: Value = finalize_resp.json().context("decoding upload response")?;
        let mut file_value = response_value
            .get("file")
            .cloned()
            .ok_or_else(|| anyhow!("upload response missing file object"))?;

        if let Some(name) = file_value
            .get("name")
            .and_then(|value| value.as_str())
            .filter(|value| !value.is_empty())
        {
            if let Some(state) = file_value
                .get("state")
                .and_then(|value| value.as_str())
                .filter(|state| is_retryable_file_state(state))
            {
                self.monitor.note_event(
                    "retry.files.await_active",
                    json!({
                        "state": state,
                        "name": name,
                        "path": asset.path,
                    }),
                );
                file_value = self.await_active_file(name)?;
            }
        }

        let final_state = file_value
            .get("state")
            .and_then(|value| value.as_str())
            .unwrap_or("STATE_UNSPECIFIED");
        if final_state != "ACTIVE" {
            return Err(anyhow!(
                "files:upload returned non-ACTIVE state {}",
                final_state
            ));
        }

        let uri = file_value
            .get("uri")
            .and_then(|value| value.as_str())
            .ok_or_else(|| anyhow!("upload response missing file.uri"))?
            .to_string();

        Ok(CachedUpload {
            uri,
            mime_type: mime.to_string(),
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

        let (payload, started, finished, retries) = {
            let mut attempt = 0;
            let mut retries = 0;
            loop {
                self.apply_quota_delay(&self.model);
                let started_at = OffsetDateTime::now_utc();
                match self
                    .http
                    .post(&url)
                    .query(&[("key", self.api_key.as_str())])
                    .json(&request)
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
                                    "model": self.model,
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
                                    "model": self.model,
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
        event_metadata.insert("retries".into(), Value::from(retries as u64));
        if let Some(uri) = asset_metadata
            .iter()
            .find_map(|meta| meta.get("file_uri").and_then(|v| v.as_str()))
        {
            event_metadata
                .entry("file_uri".to_string())
                .or_insert(Value::String(uri.to_string()));
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

    fn await_active_file(&self, name: &str) -> Result<Value> {
        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/{}?key={}",
            name, self.api_key
        );
        let mut attempt = 0;
        loop {
            self.apply_quota_delay("files");
            match self.http.get(&url).send() {
                Ok(resp) => {
                    if resp.status().is_success() {
                        let value: Value = resp.json().context("parsing files.get response")?;
                        let state = value
                            .get("state")
                            .and_then(|v| v.as_str())
                            .unwrap_or("STATE_UNSPECIFIED");
                        if state == "ACTIVE" {
                            return Ok(value);
                        }
                        if is_retryable_file_state(state) && attempt < MAX_RETRIES {
                            let delay = backoff_delay(attempt);
                            self.monitor.note_event(
                                "retry.files.await_active",
                                json!({
                                    "attempt": attempt + 1,
                                    "delay_ms": delay.as_millis(),
                                    "state": state,
                                    "name": name,
                                }),
                            );
                            thread::sleep(delay);
                            attempt += 1;
                            continue;
                        }
                        return Err(anyhow!("file {} returned terminal state {}", name, state));
                    }
                    if should_retry_status(resp.status()) && attempt < MAX_RETRIES {
                        let delay = backoff_delay(attempt);
                        self.monitor.note_event(
                            "retry.files.await_active",
                            json!({
                                "attempt": attempt + 1,
                                "delay_ms": delay.as_millis(),
                                "status": resp.status().as_u16(),
                                "name": name,
                            }),
                        );
                        thread::sleep(delay);
                        attempt += 1;
                        continue;
                    }
                    let status = resp.status();
                    let text = resp.text().unwrap_or_default();
                    return Err(anyhow!("files.get failed with status {}: {}", status, text));
                }
                Err(err) => {
                    if is_retryable_error(&err) && attempt < MAX_RETRIES {
                        let delay = backoff_delay(attempt);
                        self.monitor.note_event(
                            "retry.files.await_active",
                            json!({
                                "attempt": attempt + 1,
                                "delay_ms": delay.as_millis(),
                                "error": err.to_string(),
                                "name": name,
                            }),
                        );
                        thread::sleep(delay);
                        attempt += 1;
                        continue;
                    }
                    return Err(err).context("polling file state");
                }
            }
        }
    }

    fn apply_quota_delay(&self, bucket: &str) {
        if let Some(quota) = &self.quota {
            if let Some(delay) = quota.register_request(bucket) {
                thread::sleep(delay);
            }
        }
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
        let skip_existing = meta_bool(meta, "skip_existing").unwrap_or(false);
        let chunk_dir = base.join("full-response").join("chunks");
        ensure_dir(&chunk_dir)?;

        let manifest_path = assets
            .iter()
            .filter_map(|asset| meta_string(&asset.meta, "manifest_path"))
            .map(PathBuf::from)
            .next()
            .unwrap_or_else(|| base.join("chunks.json"));

        let mut manifest = load_manifest(&manifest_path);
        let chunks_array = manifest_chunks(&mut manifest)?;
        let mut chunk_index_lookup = HashMap::new();
        for (idx, entry) in chunks_array.iter().enumerate() {
            if let Some(index) = entry.get("index").and_then(|v| v.as_u64()) {
                chunk_index_lookup.insert(index, idx);
            }
        }

        let mut responses = Vec::new();
        for asset in assets {
            let chunk_index = meta_u64(&asset.meta, "chunk_index").unwrap_or(0);
            let entry_index = chunk_index_lookup.get(&chunk_index).copied();
            let entry = if let Some(idx) = entry_index {
                chunks_array.get_mut(idx).unwrap()
            } else {
                chunks_array.push(Value::Object(Map::new()));
                let idx = chunks_array.len() - 1;
                chunk_index_lookup.insert(chunk_index, idx);
                chunks_array.get_mut(idx).unwrap()
            };

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

            let response_path = entry_obj
                .get("response_path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| chunk_dir.join(format!("{name}-chunk{chunk_index:02}.txt")));
            entry_obj.insert(
                "response_path".into(),
                Value::String(response_path.to_string_lossy().to_string()),
            );

            if skip_existing && response_path.exists() {
                let text = fs::read_to_string(&response_path)?;
                responses.push(text.trim().to_string());
                entry_obj.insert("status".into(), Value::String("done".into()));
                self.monitor.note_event(
                    "chunk.skip",
                    json!({
                        "chunk_index": chunk_index,
                        "manifest_path": manifest_path,
                        "response_path": response_path,
                    }),
                );
                continue;
            }

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
            chunk_meta_map.insert(
                "response_path".into(),
                Value::String(response_path.to_string_lossy().to_string()),
            );

            let chunk_meta_value = Value::Object(chunk_meta_map.clone());
            let (text, event_assets) = self.generate(
                instruction,
                std::slice::from_ref(asset),
                modality,
                &chunk_meta_value,
            )?;
            save_chunk_text(&response_path, &text)?;
            entry_obj.insert("status".into(), Value::String("done".into()));
            if let Some(file_uri) = event_assets
                .first()
                .and_then(|meta| meta.get("file_uri"))
                .and_then(|v| v.as_str())
            {
                entry_obj.insert("file_uri".into(), Value::String(file_uri.to_string()));
            }
            responses.push(text.trim().to_string());
        }

        write_manifest(&manifest_path, &mut manifest)?;
        Ok(responses.join("\n\n"))
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

fn is_retryable_file_state(state: &str) -> bool {
    matches!(state, "PROCESSING" | "INTERNAL")
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
