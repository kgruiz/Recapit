use crate::constants::ModelPricing;
use crate::telemetry::RequestEvent;
use crate::video::DEFAULT_TOKENS_PER_SECOND;
use anyhow::{Context, Result};
use serde::Serialize;
use serde_yaml;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Serialize)]
pub struct CostSummary {
    pub total_input_cost: f64,
    pub total_output_cost: f64,
    pub total_cost: f64,
    pub per_model: HashMap<String, ModelCostBreakdown>,
    pub estimated: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ModelCostBreakdown {
    pub input_cost: f64,
    pub output_cost: f64,
    pub total_cost: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

impl Default for CostSummary {
    fn default() -> Self {
        Self {
            total_input_cost: 0.0,
            total_output_cost: 0.0,
            total_cost: 0.0,
            per_model: HashMap::new(),
            estimated: false,
        }
    }
}

pub struct CostEstimator {
    pricing: HashMap<String, ModelPricing>,
}

impl CostEstimator {
    pub fn from_path(path: Option<&Path>, defaults: HashMap<String, ModelPricing>) -> Result<Self> {
        if let Some(path) = path {
            if path.exists() {
                let text = fs::read_to_string(path)
                    .with_context(|| format!("reading pricing file {}", path.display()))?;
                let map: HashMap<String, ModelPricing> = serde_yaml::from_str(&text)
                    .with_context(|| format!("parsing pricing file {}", path.display()))?;
                return Ok(Self { pricing: map });
            }
        }
        Ok(Self { pricing: defaults })
    }

    pub fn estimate(&self, events: &[RequestEvent]) -> CostSummary {
        let mut summary = CostSummary::default();
        for event in events {
            if should_skip_modality(&event.modality) {
                continue;
            }
            let price_key = modality_price_key(&event.modality);
            let pricing = self
                .pricing
                .get(&event.model)
                .or_else(|| self.pricing.get("default"));
            let Some(model_pricing) = pricing else {
                continue;
            };
            let pair = match price_key {
                PriceKey::Text => &model_pricing.text,
                PriceKey::AudioVideo => &model_pricing.audio_video,
            };

            let input_tokens =
                determine_input_tokens(event).unwrap_or_else(|| estimate_tokens(event));
            let output_tokens =
                determine_output_tokens(event).unwrap_or_else(|| estimate_tokens(event));

            if input_tokens == 0 && output_tokens == 0 {
                continue;
            }
            if event.input_tokens.is_none() && event.output_tokens.is_none() {
                summary.estimated = true;
            }

            let input_cost = (input_tokens as f64 / 1_000_000.0) * pair.input;
            let output_cost = (output_tokens as f64 / 1_000_000.0) * pair.output;
            summary.total_input_cost += input_cost;
            summary.total_output_cost += output_cost;
            summary.total_cost += input_cost + output_cost;

            let bucket =
                summary
                    .per_model
                    .entry(event.model.clone())
                    .or_insert(ModelCostBreakdown {
                        input_cost: 0.0,
                        output_cost: 0.0,
                        total_cost: 0.0,
                        input_tokens: 0,
                        output_tokens: 0,
                    });
            bucket.input_cost += input_cost;
            bucket.output_cost += output_cost;
            bucket.total_cost += input_cost + output_cost;
            bucket.input_tokens += input_tokens as u64;
            bucket.output_tokens += output_tokens as u64;
        }
        summary
    }
}

fn should_skip_modality(modality: &str) -> bool {
    matches!(modality, "video_token_count")
}

enum PriceKey {
    Text,
    AudioVideo,
}

fn modality_price_key(modality: &str) -> PriceKey {
    match modality {
        "video" => PriceKey::AudioVideo,
        _ => PriceKey::Text,
    }
}

fn determine_input_tokens(event: &RequestEvent) -> Option<u32> {
    if let Some(value) = event.input_tokens {
        return Some(value);
    }
    if let (Some(total), Some(output)) = (event.total_tokens, event.output_tokens) {
        return total.checked_sub(output);
    }
    None
}

fn determine_output_tokens(event: &RequestEvent) -> Option<u32> {
    if let Some(value) = event.output_tokens {
        return Some(value);
    }
    if let (Some(total), Some(input)) = (event.total_tokens, event.input_tokens) {
        return total.checked_sub(input);
    }
    event.total_tokens
}

fn estimate_tokens(event: &RequestEvent) -> u32 {
    if event.modality != "video" {
        return 0;
    }
    let start = event
        .metadata
        .get("chunk_start_seconds")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let end = event
        .metadata
        .get("chunk_end_seconds")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    if end <= start {
        return 0;
    }
    ((end - start) * DEFAULT_TOKENS_PER_SECOND) as u32
}
