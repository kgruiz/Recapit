use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Result};
use serde_json::{json, Map, Value};
use time::OffsetDateTime;
use tracing::warn;

use super::youtube::{YouTubeDownload, YouTubeDownloadError, YouTubeDownloader};
use crate::core::{Asset, Job, PdfMode, SourceKind};
use crate::pdf::pdf_to_png;
use crate::utils::{ensure_dir, slugify};
use crate::video::{
    plan_video_chunks, probe_video, select_encoder_chain, sha256sum, VideoChunkPlan,
    VideoEncoderPreference, DEFAULT_MAX_CHUNK_BYTES, DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_TOKENS_PER_SECOND,
};

pub struct CompositeNormalizer {
    video_root: PathBuf,
    encoder_preference: VideoEncoderPreference,
    max_chunk_seconds: f64,
    max_chunk_bytes: u64,
    token_limit: Option<u32>,
    tokens_per_second: f64,
    supports: Box<dyn Fn(&str) -> bool + Send + Sync>,
    job: Option<Job>,
    chunk_info: Vec<Value>,
    manifest_path: Option<PathBuf>,
    youtube_downloader: YouTubeDownloader,
}

impl CompositeNormalizer {
    pub fn new(
        video_root: Option<PathBuf>,
        encoder_preference: VideoEncoderPreference,
        max_chunk_seconds: Option<f64>,
        max_chunk_bytes: Option<u64>,
        token_limit: Option<u32>,
        tokens_per_second: Option<f64>,
        capability_checker: Option<Box<dyn Fn(&str) -> bool + Send + Sync>>,
    ) -> Result<Self> {
        let video_root = video_root.unwrap_or_else(|| std::env::temp_dir().join("recapit-video"));
        ensure_dir(&video_root)?;
        Ok(Self {
            video_root,
            encoder_preference,
            max_chunk_seconds: max_chunk_seconds.unwrap_or(DEFAULT_MAX_CHUNK_SECONDS),
            max_chunk_bytes: max_chunk_bytes.unwrap_or(DEFAULT_MAX_CHUNK_BYTES),
            token_limit,
            tokens_per_second: tokens_per_second.unwrap_or(DEFAULT_TOKENS_PER_SECOND),
            supports: capability_checker.unwrap_or_else(|| Box::new(|_| true)),
            job: None,
            chunk_info: Vec::new(),
            manifest_path: None,
            youtube_downloader: YouTubeDownloader::new(None)?,
        })
    }

    fn normalize_inner(&mut self, assets: &[Asset], pdf_mode: PdfMode) -> Result<Vec<Asset>> {
        self.chunk_info.clear();
        self.manifest_path = None;
        let resolved = self.resolve_pdf_mode(pdf_mode)?;
        let mut normalized = Vec::new();
        for asset in assets {
            match asset.media.as_str() {
                "pdf" => normalized.extend(self.normalize_pdf(asset, resolved.clone())?),
                "video" | "audio" => normalized.extend(self.normalize_video(asset)?),
                _ => normalized.push(asset.clone()),
            }
        }
        Ok(normalized)
    }

    fn normalize_pdf(&self, asset: &Asset, mode: PdfMode) -> Result<Vec<Asset>> {
        match mode {
            PdfMode::Pdf => Ok(vec![asset.clone()]),
            PdfMode::Auto => Ok(vec![asset.clone()]),
            PdfMode::Images => {
                let output_dir = self.pdf_output_dir(asset);
                let prefix = asset
                    .path
                    .file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_else(|| "page".into());
                let pages = match pdf_to_png(&asset.path, &output_dir, Some(&prefix)) {
                    Ok(pages) => pages,
                    Err(_) => return Ok(vec![asset.clone()]),
                };
                let mut result = Vec::new();
                for (idx, page) in pages.iter().enumerate() {
                    result.push(Asset {
                        path: page.clone(),
                        media: "image".into(),
                        page_index: Some(idx as u32),
                        source_kind: asset.source_kind,
                        mime: Some("image/png".into()),
                        meta: json!({
                            "source_pdf": asset.path,
                            "page_index": idx,
                            "page_total": pages.len(),
                        }),
                    });
                }
                Ok(result)
            }
        }
    }

    fn pdf_output_dir(&self, asset: &Asset) -> PathBuf {
        let slug = asset
            .path
            .file_stem()
            .map(|s| slugify(&s.to_string_lossy()))
            .unwrap_or_else(|| "document".into());
        self.job_root().join("page-images").join(slug)
    }

    fn job_root(&self) -> PathBuf {
        if let Some(job) = &self.job {
            if let Some(output_dir) = &job.output_dir {
                let slug = if job.source.contains("://") {
                    "remote".to_string()
                } else {
                    job.source
                        .rsplit_once('/')
                        .map(|(_, tail)| tail.to_string())
                        .unwrap_or_else(|| job.source.clone())
                };
                return output_dir.join(slugify(slug));
            }
        }
        self.video_root.clone()
    }

    fn resolve_pdf_mode(&self, requested: PdfMode) -> Result<PdfMode> {
        if let PdfMode::Auto = requested {
            if (self.supports)("pdf") {
                return Ok(PdfMode::Pdf);
            }
            if (self.supports)("image") {
                return Ok(PdfMode::Images);
            }
            bail!("Provider does not support PDF or image ingestion");
        }
        Ok(requested)
    }

    fn normalize_video(&mut self, asset: &Asset) -> Result<Vec<Asset>> {
        let realized = self.materialize_video(asset)?;
        if realized
            .meta
            .as_object()
            .and_then(|meta| meta.get("pass_through"))
            .and_then(|value| value.as_bool())
            .unwrap_or(false)
        {
            return Ok(vec![realized]);
        }

        let job_root = self.job_root();
        ensure_dir(&job_root)?;
        let slug = realized
            .path
            .file_stem()
            .map(|s| slugify(&s.to_string_lossy()))
            .unwrap_or_else(|| "video".into());
        let normalized_dir = job_root
            .join("pickles")
            .join("video-chunks")
            .join(slug.clone());
        ensure_dir(&normalized_dir)?;

        let encoder_specs = select_encoder_chain(self.encoder_preference.clone());
        let normalization =
            crate::video::normalize_video(&realized.path, &normalized_dir, &encoder_specs)?;
        let normalized_path = normalization.path.clone();
        let metadata = probe_video(&normalized_path)?;
        let manifest_path = job_root.join("manifests").join(format!("{slug}.json"));

        ensure_dir(manifest_path.parent().unwrap())?;
        let chunk_plan = plan_video_chunks(
            &metadata,
            &normalized_path,
            self.max_chunk_seconds,
            self.max_chunk_bytes,
            self.token_limit,
            self.tokens_per_second,
            &normalized_dir.join("chunks"),
            self.job
                .as_ref()
                .map(|job| job.max_video_workers)
                .unwrap_or(1),
        )?;
        self.write_manifest(&chunk_plan, &realized, &manifest_path)?;
        self.manifest_path = Some(manifest_path.clone());

        let chunk_total = chunk_plan.chunks.len();
        let mut outputs = Vec::new();
        for chunk in &chunk_plan.chunks {
            let meta = json!({
                "chunk_index": chunk.index,
                "chunk_total": chunk_total,
                "chunk_start_seconds": chunk.start_seconds,
                "chunk_end_seconds": chunk.end_seconds,
                "manifest_path": manifest_path,
                "normalized_path": chunk_plan.normalized_path,
                "source_video": realized.path,
            });
            outputs.push(Asset {
                path: chunk.path.clone(),
                media: "video".into(),
                page_index: None,
                source_kind: realized.source_kind,
                mime: Some("video/mp4".into()),
                meta: meta.clone(),
            });
            self.chunk_info.push(meta);
        }
        Ok(outputs)
    }

    fn materialize_video(&mut self, asset: &Asset) -> Result<Asset> {
        if asset.source_kind != SourceKind::Youtube {
            return Ok(asset.clone());
        }

        let meta_map = value_to_map(&asset.meta);
        if meta_map
            .get("pass_through")
            .and_then(|value| value.as_bool())
            .unwrap_or(false)
        {
            return Ok(asset.clone());
        }

        let source_url = meta_map
            .get("source_url")
            .and_then(|value| value.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| asset.path.to_string_lossy().to_string());

        let downloads_dir = self.job_root().join("downloads").join("youtube");
        ensure_dir(&downloads_dir)?;

        match self
            .youtube_downloader
            .download(&source_url, Some(&downloads_dir))
        {
            Ok(download) => {
                let updated = apply_download_metadata(meta_map, &download, &source_url);
                let mut realized = asset.clone();
                realized.path = download.path.clone();
                realized.mime = Some(download.mime.clone());
                realized.meta = Value::Object(updated);
                Ok(realized)
            }
            Err(YouTubeDownloadError::MissingYtDlp | YouTubeDownloadError::MissingFfmpeg) => {
                warn!(
                    target: "recapit::ingest::youtube",
                    "YouTube download prerequisites missing for {}",
                    source_url
                );
                let mut meta_map = meta_map;
                meta_map.insert("pass_through".into(), Value::Bool(true));
                meta_map.insert("downloaded".into(), Value::Bool(false));
                meta_map.insert(
                    "warning".into(),
                    Value::String("YouTube download prerequisites missing".into()),
                );
                let mut fallback = asset.clone();
                fallback.path = PathBuf::from(source_url.clone());
                fallback.mime = Some("video/*".into());
                fallback.meta = Value::Object(meta_map);
                Ok(fallback)
            }
            Err(err) => {
                warn!(
                    target: "recapit::ingest::youtube",
                    "YouTube download failed for {}: {}",
                    source_url,
                    err
                );
                let mut meta_map = meta_map;
                meta_map.insert("pass_through".into(), Value::Bool(true));
                meta_map.insert("downloaded".into(), Value::Bool(false));
                meta_map.insert("warning".into(), Value::String(err.to_string()));
                let mut fallback = asset.clone();
                fallback.path = PathBuf::from(source_url.clone());
                fallback.mime = Some("video/*".into());
                fallback.meta = Value::Object(meta_map);
                Ok(fallback)
            }
        }
    }

    fn write_manifest(
        &self,
        plan: &VideoChunkPlan,
        asset: &Asset,
        manifest_path: &Path,
    ) -> Result<()> {
        ensure_dir(manifest_path.parent().unwrap())?;
        let mut chunks = Vec::<Value>::new();
        for chunk in &plan.chunks {
            chunks.push(json!({
                "index": chunk.index,
                "start_seconds": chunk.start_seconds,
                "end_seconds": chunk.end_seconds,
                "start_iso": crate::video::seconds_to_iso(chunk.start_seconds),
                "end_iso": crate::video::seconds_to_iso(chunk.end_seconds),
                "path": chunk.path,
                "status": "pending",
            }));
        }
        let source_hash = sha256sum(&asset.path)?;
        let normalized_hash = sha256sum(&plan.normalized_path)?;
        let downloaded = asset
            .meta
            .as_object()
            .and_then(|meta| meta.get("downloaded"))
            .and_then(|value| value.as_bool())
            .unwrap_or(false);
        let source_url_value = asset
            .meta
            .as_object()
            .and_then(|meta| meta.get("source_url"))
            .cloned()
            .unwrap_or(Value::Null);
        let youtube_id_value = asset
            .meta
            .as_object()
            .and_then(|meta| meta.get("youtube_id"))
            .cloned()
            .unwrap_or(Value::Null);
        let payload = json!({
            "version": 1,
            "source": asset.path,
            "source_hash": format!("sha256:{source_hash}"),
            "source_kind": asset.source_kind,
            "source_url": source_url_value,
            "downloaded": downloaded,
            "youtube_id": youtube_id_value,
            "normalized": plan.normalized_path,
            "normalized_hash": format!("sha256:{normalized_hash}"),
            "duration_seconds": plan.metadata.duration_seconds,
            "size_bytes": plan.metadata.size_bytes,
            "fps": plan.metadata.fps,
            "tokens_per_second": self.tokens_per_second,
            "created_utc": OffsetDateTime::now_utc(),
            "updated_utc": OffsetDateTime::now_utc(),
            "chunks": chunks,
        });
        fs::write(manifest_path, serde_json::to_string_pretty(&payload)?)?;
        Ok(())
    }
}

impl crate::core::Normalizer for CompositeNormalizer {
    fn prepare(&mut self, job: &Job) -> Result<()> {
        self.job = Some(job.clone());
        Ok(())
    }

    fn normalize(&mut self, assets: &[Asset], pdf_mode: PdfMode) -> Result<Vec<Asset>> {
        self.normalize_inner(assets, pdf_mode)
    }

    fn chunk_descriptors(&self) -> Vec<Value> {
        self.chunk_info.clone()
    }

    fn artifact_paths(&self) -> Vec<PathBuf> {
        self.manifest_path.clone().into_iter().collect()
    }
}

fn value_to_map(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_else(Map::new)
}

fn apply_download_metadata(
    mut meta: Map<String, Value>,
    download: &YouTubeDownload,
    source_url: &str,
) -> Map<String, Value> {
    meta.insert("source_url".into(), Value::String(source_url.to_string()));
    meta.insert("pass_through".into(), Value::Bool(false));
    meta.insert("downloaded".into(), Value::Bool(true));
    meta.remove("warning");

    if let Some(id) = download
        .metadata
        .as_object()
        .and_then(|map| map.get("id"))
        .and_then(|value| value.as_str())
    {
        meta.insert("youtube_id".into(), Value::String(id.to_string()));
    }
    if let Some(duration) = extract_duration(&download.metadata) {
        meta.insert("duration_seconds".into(), Value::from(duration));
    }
    if let Some(bytes) = download.size_bytes {
        meta.insert("size_bytes".into(), Value::from(bytes));
    }
    if let Some(hash) = download.sha256.as_ref() {
        meta.insert("size_hash".into(), Value::String(format!("sha256:{hash}")));
    }
    if let Some(title) = download
        .metadata
        .as_object()
        .and_then(|map| map.get("title"))
        .and_then(|value| value.as_str())
    {
        meta.insert("title".into(), Value::String(title.to_string()));
    }
    meta.insert("download_cached".into(), Value::Bool(download.cached));
    meta
}

fn extract_duration(metadata: &Value) -> Option<f64> {
    metadata
        .as_object()
        .and_then(|map| map.get("duration"))
        .and_then(|value| {
            value
                .as_f64()
                .or_else(|| value.as_i64().map(|v| v as f64))
                .or_else(|| value.as_u64().map(|v| v as f64))
        })
}
