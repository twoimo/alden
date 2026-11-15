//! Output redaction: provenance hashes and absolute-path stripping applied
//! before context results leave the process.

use super::reply_decision::ReplyDecisionMatch;
use super::response_time::ResponseTimeStats;
use super::search::ContextResult;
use sha2::{Digest, Sha256};
use std::path::Path;

pub fn provenance_id(source: &str) -> String {
    format!("source:{}", hex::encode(Sha256::digest(source.as_bytes())))
}

pub fn redact_absolute_paths(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::String(text) if Path::new(text).is_absolute() => {
            *text = provenance_id(text);
        }
        serde_json::Value::Array(values) => {
            for value in values {
                redact_absolute_paths(value);
            }
        }
        serde_json::Value::Object(values) => {
            for value in values.values_mut() {
                redact_absolute_paths(value);
            }
        }
        _ => {}
    }
}

pub fn redact_context_results(results: &mut [ContextResult]) {
    for result in results {
        result.source = provenance_id(&result.source);
    }
}

pub fn redact_response_time_stats(stats: &mut Option<ResponseTimeStats>) {
    if let Some(stats) = stats {
        stats.source = provenance_id(&stats.source);
    }
}

pub fn redact_reply_decisions(results: &mut [ReplyDecisionMatch]) {
    for result in results {
        if let Ok(mut evidence) = serde_json::from_str::<serde_json::Value>(&result.evidence_json) {
            redact_absolute_paths(&mut evidence);
            result.evidence_json =
                serde_json::to_string(&evidence).unwrap_or_else(|_| "{}".to_string());
        } else {
            result.evidence_json = serde_json::json!({
                "redacted_evidence": provenance_id(&result.evidence_json)
            })
            .to_string();
        }
    }
}
