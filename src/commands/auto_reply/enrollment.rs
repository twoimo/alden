//! The CLI enrollment contract: the writer that records each room's cursor
//! authority and the validators the worker gate applies to it.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use super::bindings::{AutoReplyAuthorBinding, AutoReplyBindingEvidence};
use super::queue::validate_stopped_clean_queue;
use super::state_root::{
    read_bounded_json_file, write_private_auto_reply_enrollment, AUTO_REPLY_READINESS_MAX_BYTES,
};
use super::MAX_INT64;
use crate::auto_reply_runtime::{leftover_queue_has_unknown_send, validate_private_regular_file};
use crate::local_db;

pub(super) const AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION: i64 = 4;
pub(super) const AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION: i64 = 1;
pub(super) const AUTO_REPLY_CURSOR_FRESH_KIND: &str = "fresh_attested_tail";
pub(super) const AUTO_REPLY_CURSOR_REPLAY_KIND: &str = "stopped_clean_ack_replay";
pub(super) const AUTO_REPLY_CURSOR_LEFTOVER_KIND: &str = "fenced_leftover_ack_resume";

fn is_empty_json_array(value: Option<&serde_json::Value>) -> bool {
    value
        .and_then(serde_json::Value::as_array)
        .is_some_and(Vec::is_empty)
}

fn leftover_supervisor_is_live(room_root: &Path, target: &local_db::LocalChat) -> Result<bool> {
    let status_path = room_root.join("supervisor-status.json");
    validate_private_regular_file(&status_path, AUTO_REPLY_READINESS_MAX_BYTES)?;
    let status = read_bounded_json_file(&status_path)?;
    let state = status
        .get("state")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    Ok(
        status.get("schema_version") == Some(&serde_json::Value::from(1))
            && status.get("mode")
                == Some(&serde_json::Value::String(
                    "database_authoritative".to_string(),
                ))
            && status.get("target_chat_id") == Some(&serde_json::Value::from(target.chat_id))
            && status.get("target_chat_name")
                == Some(&serde_json::Value::String(target.chat_name.clone()))
            && matches!(state, "running" | "starting" | "stopping")
            && status.get("all_children_exited") != Some(&serde_json::Value::Bool(true)),
    )
}

fn leftover_in_flight_is_absent_or_orphaned(state: &serde_json::Value) -> bool {
    match state.get("in_flight_candidate") {
        None | Some(serde_json::Value::Null) => true,
        Some(candidate) => {
            let persisted_owner = state.get("owner_id").and_then(serde_json::Value::as_str);
            let in_flight_owner = candidate
                .get("owner_id")
                .and_then(serde_json::Value::as_str);
            matches!(
                (persisted_owner, in_flight_owner),
                (Some(owner), Some(in_flight)) if !owner.is_empty() && owner == in_flight
            )
        }
    }
}

fn leftover_supervisor_is_terminal(
    room_root: &Path,
    target: &local_db::LocalChat,
    state: &serde_json::Value,
) -> Result<()> {
    let status_path = room_root.join("supervisor-status.json");
    validate_private_regular_file(&status_path, AUTO_REPLY_READINESS_MAX_BYTES)?;
    let status = read_bounded_json_file(&status_path)?;
    let _owner = state
        .get("owner_id")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty())
        .context("AutoReply leftover owner is invalid")?;
    let _epoch = state
        .get("source_epoch")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| (1..MAX_INT64).contains(value))
        .context("AutoReply leftover epoch is invalid")?;
    let child_states = status
        .get("child_states")
        .and_then(serde_json::Value::as_object)
        .context("AutoReply leftover child states are missing")?;
    if status.get("schema_version") != Some(&serde_json::Value::from(1))
        || status.get("mode")
            != Some(&serde_json::Value::String(
                "database_authoritative".to_string(),
            ))
        || status.get("state") != Some(&serde_json::Value::String("stopped".to_string()))
        || status.get("shutdown_state")
            != Some(&serde_json::Value::String("stopped_unclean".to_string()))
        || status.get("all_children_exited") != Some(&serde_json::Value::Bool(true))
        || status
            .get("owner")
            .and_then(serde_json::Value::as_str)
            .is_none_or(str::is_empty)
        || status
            .get("source_epoch")
            .and_then(serde_json::Value::as_i64)
            .is_none_or(|value| !(1..MAX_INT64).contains(&value))
        || status.get("target_chat_id") != Some(&serde_json::Value::from(target.chat_id))
        || status.get("target_chat_name")
            != Some(&serde_json::Value::String(target.chat_name.clone()))
        || ["ax_watch", "db_watch", "reply_worker"].iter().any(|role| {
            child_states.get(*role) != Some(&serde_json::Value::String("exited".to_string()))
        })
    {
        anyhow::bail!("AutoReply leftover supervisor proof is invalid");
    }
    leftover_queue_has_unknown_send(&room_root.join("reply-queue.sqlite3"), target.chat_id)
}

fn validate_stopped_clean_supervisor(
    room_root: &Path,
    target: &local_db::LocalChat,
    state: &serde_json::Value,
) -> Result<()> {
    let status_path = room_root.join("supervisor-status.json");
    validate_private_regular_file(&status_path, AUTO_REPLY_READINESS_MAX_BYTES)?;
    let status = read_bounded_json_file(&status_path)?;
    let owner = state
        .get("owner_id")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty())
        .context("AutoReply stopped-clean owner is invalid")?;
    let epoch = state
        .get("source_epoch")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| (1..MAX_INT64).contains(value))
        .context("AutoReply stopped-clean epoch is invalid")?;
    let expected_roles = ["ax_watch", "db_watch", "reply_worker"];
    let child_states = status
        .get("child_states")
        .and_then(serde_json::Value::as_object)
        .context("AutoReply stopped-clean child states are missing")?;
    if status.get("schema_version") != Some(&serde_json::Value::from(1))
        || status.get("mode")
            != Some(&serde_json::Value::String(
                "database_authoritative".to_string(),
            ))
        || status.get("state") != Some(&serde_json::Value::String("stopped".to_string()))
        || status.get("shutdown_state")
            != Some(&serde_json::Value::String("stopped_clean".to_string()))
        || status.get("all_children_exited") != Some(&serde_json::Value::Bool(true))
        || status.get("readiness") != Some(&serde_json::Value::String("fenced".to_string()))
        || status.get("fence_reason")
            != Some(&serde_json::Value::String("stopped_clean".to_string()))
        || status.get("owner") != Some(&serde_json::Value::String(owner.to_string()))
        || status.get("source_epoch") != Some(&serde_json::Value::from(epoch))
        || status.get("target_chat_id") != Some(&serde_json::Value::from(target.chat_id))
        || status.get("target_chat_name")
            != Some(&serde_json::Value::String(target.chat_name.clone()))
        || child_states.len() != expected_roles.len()
        || expected_roles.iter().any(|role| {
            child_states.get(*role) != Some(&serde_json::Value::String("exited".to_string()))
        })
    {
        anyhow::bail!("AutoReply stopped-clean supervisor proof is invalid");
    }
    validate_stopped_clean_queue(&room_root.join("reply-queue.sqlite3"), target.chat_id)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct AutoReplyCursorAuthority {
    pub(super) kind: &'static str,
    pub(super) cursor_floor: i64,
    pub(super) attested_db_last_log_id: i64,
    pub(super) prior_owner_id: Option<String>,
    pub(super) prior_source_epoch: Option<i64>,
}

pub(super) fn enrollment_cursor_authority_for_target(
    root: &Path,
    target: &local_db::LocalChat,
    freshly_attested_watermark: i64,
) -> Result<AutoReplyCursorAuthority> {
    if !(0..MAX_INT64).contains(&freshly_attested_watermark)
        || freshly_attested_watermark != target.last_log_id
    {
        anyhow::bail!("AutoReply fresh enrollment watermark is invalid");
    }
    let room_root = root.join("rooms").join(target.chat_id.to_string());
    let state_path = room_root.join("db-watch-state.json");
    if let Ok(metadata) = fs::symlink_metadata(&state_path) {
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            anyhow::bail!("AutoReply room state is not a regular file");
        }
        let state = read_bounded_json_file(&state_path)?;
        let cursor_floor = state
            .get("cursor_floor")
            .and_then(serde_json::Value::as_i64)
            .filter(|value| 0 <= *value && *value < MAX_INT64)
            .context("AutoReply room cursor floor is invalid")?;
        let acked_watermark = state
            .get("acked_watermark")
            .and_then(serde_json::Value::as_i64)
            .filter(|value| 0 <= *value && *value < MAX_INT64)
            .context("AutoReply room ACK watermark is invalid")?;
        let last_observed = state
            .get("last_observed_log_id")
            .and_then(serde_json::Value::as_i64)
            .filter(|value| 0 <= *value && *value < MAX_INT64)
            .context("AutoReply room observed watermark is invalid")?;
        let parse_ids = |key: &str| -> Result<std::collections::BTreeSet<i64>> {
            let values = state
                .get(key)
                .and_then(serde_json::Value::as_array)
                .filter(|values| values.len() <= 500)
                .with_context(|| format!("AutoReply room {key} is invalid"))?;
            let parsed = values
                .iter()
                .map(|value| {
                    value
                        .as_i64()
                        .filter(|id| 0 < *id && *id < MAX_INT64)
                        .with_context(|| format!("AutoReply room {key} contains an invalid ID"))
                })
                .collect::<Result<std::collections::BTreeSet<_>>>()?;
            if parsed.len() != values.len() {
                anyhow::bail!("AutoReply room {key} contains duplicate IDs");
            }
            Ok(parsed)
        };
        let observed = parse_ids("observed_log_ids")?;
        let acked = parse_ids("acked_log_ids")?;
        let stopped_clean_state = state.get("capability_state")
            == Some(&serde_json::Value::String("stopped_clean".to_string()))
            && state.get("delivery_enabled") == Some(&serde_json::Value::Bool(false))
            && state.get("fence") == Some(&serde_json::Value::String("stopped_clean".to_string()))
            && state.get("fence_reason") == Some(&serde_json::Value::String(String::new()));
        let leftover_fenced_state = state.get("schema_version")
            == Some(&serde_json::Value::from(3))
            && state.get("target_chat_id") == Some(&serde_json::Value::from(target.chat_id))
            && state.get("target_chat_name")
                == Some(&serde_json::Value::String(target.chat_name.clone()))
            && state.get("capability_state")
                == Some(&serde_json::Value::String("fenced".to_string()))
            && state.get("delivery_enabled") == Some(&serde_json::Value::Bool(false))
            && matches!(
                state
                    .get("candidate_phase")
                    .and_then(serde_json::Value::as_str),
                Some("idle" | "hooking")
            )
            && leftover_in_flight_is_absent_or_orphaned(&state)
            && state
                .get("owner_id")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|value| !value.is_empty())
            && state
                .get("source_epoch")
                .and_then(serde_json::Value::as_i64)
                .is_some_and(|value| (1..MAX_INT64).contains(&value))
            && cursor_floor <= acked_watermark
            && acked_watermark == acked.iter().next_back().copied().unwrap_or(0)
            && last_observed == observed.iter().next_back().copied().unwrap_or(0)
            && acked.is_subset(&observed)
            && acked_watermark > 0
            && acked_watermark <= freshly_attested_watermark;
        // Shared shape for a leftover room whose cursor is idle and whose acked
        // watermark is still the last confirmed delivery boundary.
        let leftover_idle_shape_ok = matches!(
            state
                .get("candidate_phase")
                .and_then(serde_json::Value::as_str),
            // The watcher's own recovery (`_reconcile_ingress_journal`) accepts a
            // candidate in any of these pre-delivery phases, so refusing to
            // start on them asked for a manual reconciliation the system could
            // perform itself. `sending` and later stay excluded: delivery may
            // already have begun there.
            Some("idle" | "pending" | "hooking" | "acknowledging")
        ) && leftover_in_flight_is_absent_or_orphaned(&state)
            && (is_empty_json_array(state.get("pending_gaps"))
                || state.get("pending_gaps") == Some(&serde_json::json!(["reconcile_required"])))
            && state
                .get("owner_id")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|value| !value.is_empty())
            && state
                .get("source_epoch")
                .and_then(serde_json::Value::as_i64)
                .is_some_and(|value| (1..MAX_INT64).contains(&value))
            && cursor_floor <= acked_watermark
            && acked_watermark == acked.iter().next_back().copied().unwrap_or(0)
            && last_observed == observed.iter().next_back().copied().unwrap_or(0)
            && acked.is_subset(&observed)
            && acked_watermark > 0
            && acked_watermark <= freshly_attested_watermark;
        let leftover_identity_ok = state.get("schema_version") == Some(&serde_json::Value::from(3))
            && state.get("target_chat_id") == Some(&serde_json::Value::from(target.chat_id))
            && state.get("target_chat_name")
                == Some(&serde_json::Value::String(target.chat_name.clone()));
        let leftover_ready_idle = leftover_identity_ok
            && leftover_idle_shape_ok
            && state.get("capability_state")
                == Some(&serde_json::Value::String("ready".to_string()))
            && state.get("delivery_enabled") == Some(&serde_json::Value::Bool(true))
            && state.get("fence") == Some(&serde_json::Value::String("ready".to_string()));
        // A start that was interrupted, or that fenced on a retryable condition
        // such as `context_sync_transient`, leaves the persisted capability at
        // "starting". Nothing is in flight and the acked watermark is still the
        // last confirmed boundary, so it is exactly as resumable as a "ready"
        // leftover. Without this branch the room stayed blocked on "requires
        // reconciliation before restart" until an operator intervened.
        let leftover_starting_idle = leftover_identity_ok
            && leftover_idle_shape_ok
            && state.get("capability_state")
                == Some(&serde_json::Value::String("starting".to_string()))
            && state.get("delivery_enabled") == Some(&serde_json::Value::Bool(false))
            && state.get("fence") == Some(&serde_json::Value::String("starting".to_string()));
        let live_supervisor = leftover_supervisor_is_live(&room_root, target).unwrap_or(false);
        if live_supervisor
            && state.get("schema_version") == Some(&serde_json::Value::from(3))
            && state.get("target_chat_id") == Some(&serde_json::Value::from(target.chat_id))
            && state.get("target_chat_name")
                == Some(&serde_json::Value::String(target.chat_name.clone()))
            && state
                .get("owner_id")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|value| !value.is_empty())
            && state
                .get("source_epoch")
                .and_then(serde_json::Value::as_i64)
                .is_some_and(|value| (1..MAX_INT64).contains(&value))
            && acked_watermark > 0
            && acked_watermark <= freshly_attested_watermark
        {
            leftover_queue_has_unknown_send(
                &room_root.join("reply-queue.sqlite3"),
                target.chat_id,
            )?;
            validate_private_regular_file(&state_path, 256 * 1024)?;
            return Ok(AutoReplyCursorAuthority {
                kind: AUTO_REPLY_CURSOR_LEFTOVER_KIND,
                cursor_floor: acked_watermark,
                attested_db_last_log_id: freshly_attested_watermark,
                prior_owner_id: state
                    .get("owner_id")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string),
                prior_source_epoch: state
                    .get("source_epoch")
                    .and_then(serde_json::Value::as_i64),
            });
        }
        if (leftover_ready_idle || leftover_starting_idle) && !live_supervisor {
            leftover_supervisor_is_terminal(&room_root, target, &state)?;
            validate_private_regular_file(&state_path, 256 * 1024)?;
            return Ok(AutoReplyCursorAuthority {
                kind: AUTO_REPLY_CURSOR_LEFTOVER_KIND,
                cursor_floor: acked_watermark,
                attested_db_last_log_id: freshly_attested_watermark,
                prior_owner_id: state
                    .get("owner_id")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string),
                prior_source_epoch: state
                    .get("source_epoch")
                    .and_then(serde_json::Value::as_i64),
            });
        }
        if leftover_fenced_state && !stopped_clean_state {
            leftover_supervisor_is_terminal(&room_root, target, &state)?;
            validate_private_regular_file(&state_path, 256 * 1024)?;
            return Ok(AutoReplyCursorAuthority {
                kind: AUTO_REPLY_CURSOR_LEFTOVER_KIND,
                cursor_floor: acked_watermark,
                attested_db_last_log_id: freshly_attested_watermark,
                prior_owner_id: state
                    .get("owner_id")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string),
                prior_source_epoch: state
                    .get("source_epoch")
                    .and_then(serde_json::Value::as_i64),
            });
        }
        if state.get("schema_version") != Some(&serde_json::Value::from(3))
            || state.get("target_chat_id") != Some(&serde_json::Value::from(target.chat_id))
            || state.get("target_chat_name")
                != Some(&serde_json::Value::String(target.chat_name.clone()))
            || !is_empty_json_array(state.get("pending_log_ids"))
            || !is_empty_json_array(state.get("pending_gaps"))
            || state.get("candidate_phase") != Some(&serde_json::Value::String("idle".to_string()))
            || state.get("in_flight_candidate") != Some(&serde_json::Value::Null)
            || !stopped_clean_state
            || state
                .get("owner_id")
                .and_then(serde_json::Value::as_str)
                .is_none_or(str::is_empty)
            || state
                .get("source_epoch")
                .and_then(serde_json::Value::as_i64)
                .is_none_or(|value| !(1..MAX_INT64).contains(&value))
            || cursor_floor > acked_watermark
            || observed != acked
            || acked_watermark != acked.iter().next_back().copied().unwrap_or(0)
            || last_observed != observed.iter().next_back().copied().unwrap_or(0)
        {
            anyhow::bail!(
                "AutoReply room {} requires reconciliation before restart",
                target.chat_name
            );
        }
        validate_private_regular_file(&state_path, 256 * 1024)?;
        validate_stopped_clean_supervisor(&room_root, target, &state)?;
        if acked_watermark > freshly_attested_watermark {
            anyhow::bail!(
                "AutoReply room {} database tail regressed below its authoritative ACK",
                target.chat_name
            );
        }
        // A fresh DB/AX attestation proves which room is currently open; it
        // is not an acknowledgement of rows that arrived while this watcher
        // was stopped.  Resume a fully attested stopped-clean generation at
        // its durable ACK so local-poll can replay those intervening rows.
        return Ok(AutoReplyCursorAuthority {
            kind: AUTO_REPLY_CURSOR_REPLAY_KIND,
            cursor_floor: acked_watermark,
            attested_db_last_log_id: freshly_attested_watermark,
            prior_owner_id: state
                .get("owner_id")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
            prior_source_epoch: state
                .get("source_epoch")
                .and_then(serde_json::Value::as_i64),
        });
    } else if let Err(error) = fs::symlink_metadata(&state_path) {
        if error.kind() != std::io::ErrorKind::NotFound {
            return Err(error)
                .with_context(|| format!("inspect AutoReply room state {}", state_path.display()));
        }
    }

    let enrollment_path = root.join("enrollment.json");
    let enrollment_metadata = match fs::symlink_metadata(&enrollment_path) {
        Ok(metadata) => Some(metadata),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => {
            return Err(error).with_context(|| format!("inspect {}", enrollment_path.display()));
        }
    };
    if let Some(metadata) = enrollment_metadata {
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            anyhow::bail!("AutoReply enrollment is not a regular file");
        }
        let enrollment = read_bounded_json_file(&enrollment_path)?;
        let targets = enrollment
            .get("targets")
            .and_then(serde_json::Value::as_array)
            .context("AutoReply enrollment targets are missing")?;
        if enrollment.get("schema_version")
            != Some(&serde_json::Value::from(
                AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION,
            ))
            || targets.is_empty()
            || targets.len() > 32
        {
            anyhow::bail!("AutoReply enrollment authority is invalid");
        }
        let matches = targets
            .iter()
            .filter(|item| {
                item.get("chat_id").and_then(serde_json::Value::as_i64) == Some(target.chat_id)
            })
            .collect::<Vec<_>>();
        if matches.len() > 1 {
            anyhow::bail!("AutoReply enrollment target is duplicated");
        }
        if !matches.is_empty() {
            anyhow::bail!(
                "AutoReply room {} has enrollment authority but no clean v3 DB state",
                target.chat_name
            );
        }
    }
    Ok(AutoReplyCursorAuthority {
        kind: AUTO_REPLY_CURSOR_FRESH_KIND,
        cursor_floor: freshly_attested_watermark,
        attested_db_last_log_id: freshly_attested_watermark,
        prior_owner_id: None,
        prior_source_epoch: None,
    })
}

pub(super) fn enrollment_floor_for_target(
    root: &Path,
    target: &local_db::LocalChat,
    freshly_attested_watermark: i64,
) -> Result<i64> {
    Ok(
        enrollment_cursor_authority_for_target(root, target, freshly_attested_watermark)?
            .cursor_floor,
    )
}

pub(super) fn write_auto_reply_enrollment(
    root: &Path,
    selector_values: &[String],
    targets: &[local_db::LocalChat],
    binding_evidence: &[AutoReplyBindingEvidence],
    author_bindings: &std::collections::BTreeMap<i64, Vec<AutoReplyAuthorBinding>>,
    runtime_root: &Path,
) -> Result<(Vec<i64>, String)> {
    if targets.iter().any(|target| {
        author_bindings
            .get(&target.chat_id)
            .is_none_or(|bindings| bindings.is_empty())
    }) {
        anyhow::bail!("AutoReply numeric reply-author enrollment is incomplete");
    }
    let fresh_watermarks = targets
        .iter()
        .map(|target| {
            binding_evidence
                .iter()
                .find(|item| item.chat_id == target.chat_id)
                .map_or(target.last_log_id, |item| item.attested_db_last_log_id)
        })
        .collect::<Vec<_>>();
    let cursor_authorities = targets
        .iter()
        .zip(fresh_watermarks.iter())
        .map(|(target, fresh_watermark)| {
            enrollment_cursor_authority_for_target(root, target, *fresh_watermark)
        })
        .collect::<Result<Vec<_>>>()?;
    if targets
        .iter()
        .zip(cursor_authorities.iter())
        .any(|(target, authority)| {
            matches!(
                authority.kind,
                AUTO_REPLY_CURSOR_REPLAY_KIND | AUTO_REPLY_CURSOR_LEFTOVER_KIND
            ) && binding_evidence
                .iter()
                .all(|binding| binding.chat_id != target.chat_id)
        })
    {
        anyhow::bail!("AutoReply replay enrollment requires fresh AX transcript authority");
    }
    let payload = serde_json::json!({
        "schema_version": AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION,
        "activation": "foreground",
        "selectors": selector_values,
        "runtime_root": runtime_root,
        "created_at": chrono::Utc::now().to_rfc3339(),
        "targets": targets.iter().zip(cursor_authorities.iter()).map(|(target, authority)| {
            let binding = binding_evidence.iter().find(|item| item.chat_id == target.chat_id);
            let identity = if let Some(binding) = binding {
                serde_json::json!({
                    "schema_version": 1,
                    "kind": "ax_transcript",
                    "local_name": binding.local_name,
                    "ax_name": binding.ax_name,
                    "matched_log_ids": binding.matched_log_ids,
                    "matched_count": binding.matched_count,
                    "matched_utf8_bytes": binding.matched_utf8_bytes,
                    "transcript_sha256": binding.transcript_sha256,
                    "attested_db_last_log_id": binding.attested_db_last_log_id,
                })
            } else {
                serde_json::json!({
                    "schema_version": 1,
                    "kind": "local_name",
                    "local_name": target.chat_name,
                    "ax_name": target.chat_name,
                })
            };
            serde_json::json!({
                "chat_id": target.chat_id,
                "chat_name": target.chat_name,
                "last_log_id": authority.cursor_floor,
                "room_state_root": root.join("rooms").join(target.chat_id.to_string()),
                "identity": identity,
                "cursor_authority": {
                    "schema_version": AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION,
                    "kind": authority.kind,
                    "cursor_floor": authority.cursor_floor,
                    "attested_db_last_log_id": authority.attested_db_last_log_id,
                    "prior_owner_id": authority.prior_owner_id,
                    "prior_source_epoch": authority.prior_source_epoch,
                },
                "reply_author_bindings": author_bindings.get(&target.chat_id),
            })
        }).collect::<Vec<_>>(),
    });
    let bytes = serde_json::to_vec_pretty(&payload)?;
    let digest = hex::encode(Sha256::digest(&bytes));
    write_private_auto_reply_enrollment(root, &bytes)?;
    Ok((
        cursor_authorities
            .iter()
            .map(|authority| authority.cursor_floor)
            .collect(),
        digest,
    ))
}

fn is_lower_hex_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
}

pub(super) fn validate_cli_enrollment_digest(raw: &[u8], expected: &str) -> Result<()> {
    if !is_lower_hex_sha256(expected) {
        anyhow::bail!("CLI enrollment authority digest missing");
    }
    let actual = hex::encode(Sha256::digest(raw));
    if actual != expected {
        anyhow::bail!("CLI enrollment authority digest mismatch");
    }
    Ok(())
}

fn json_object_has_exact_keys(value: &serde_json::Value, expected: &[&str]) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    object.len() == expected.len() && expected.iter().all(|key| object.contains_key(*key))
}

fn cli_enrollment_reply_author_bindings(
    enrolled: &serde_json::Value,
) -> Result<std::collections::BTreeMap<String, i64>> {
    let values = enrolled
        .get("reply_author_bindings")
        .and_then(serde_json::Value::as_array)
        .filter(|values| !values.is_empty() && values.len() <= 64)
        .context("CLI enrollment reply-author bindings missing")?;
    let mut bindings = std::collections::BTreeMap::new();
    let mut author_ids = std::collections::BTreeSet::new();
    let mut previous_nickname: Option<&str> = None;
    for value in values {
        if !json_object_has_exact_keys(value, &["nickname", "author_id"]) {
            anyhow::bail!("CLI enrollment reply-author binding schema invalid");
        }
        let nickname = value
            .get("nickname")
            .and_then(serde_json::Value::as_str)
            .filter(|nickname| {
                !nickname.is_empty()
                    && nickname.len() <= 1024
                    && nickname.trim() == *nickname
                    && !nickname.chars().any(char::is_control)
            })
            .context("CLI enrollment reply-author nickname invalid")?;
        let author_id = value
            .get("author_id")
            .and_then(serde_json::Value::as_i64)
            .filter(|author_id| 0 < *author_id && *author_id < MAX_INT64)
            .context("CLI enrollment reply-author ID invalid")?;
        if previous_nickname.is_some_and(|previous| previous >= nickname)
            || bindings.insert(nickname.to_string(), author_id).is_some()
            || !author_ids.insert(author_id)
        {
            anyhow::bail!("CLI enrollment reply-author bindings are ambiguous or unsorted");
        }
        previous_nickname = Some(nickname);
    }
    Ok(bindings)
}

fn cli_enrollment_cursor_authority(
    enrolled: &serde_json::Value,
    cursor_floor: i64,
) -> Result<(&str, i64)> {
    let authority = enrolled
        .get("cursor_authority")
        .and_then(serde_json::Value::as_object)
        .context("CLI enrollment cursor authority missing")?;
    let expected_keys = [
        "schema_version",
        "kind",
        "cursor_floor",
        "attested_db_last_log_id",
        "prior_owner_id",
        "prior_source_epoch",
    ];
    if authority.len() != expected_keys.len()
        || expected_keys
            .iter()
            .any(|key| !authority.contains_key(*key))
        || authority.get("schema_version")
            != Some(&serde_json::Value::from(
                AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION,
            ))
        || authority
            .get("cursor_floor")
            .and_then(serde_json::Value::as_i64)
            != Some(cursor_floor)
    {
        anyhow::bail!("CLI enrollment cursor authority invalid");
    }
    let kind = authority
        .get("kind")
        .and_then(serde_json::Value::as_str)
        .context("CLI enrollment cursor authority kind missing")?;
    let attested_tail = authority
        .get("attested_db_last_log_id")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| 0 <= *value && *value < MAX_INT64)
        .context("CLI enrollment cursor authority tail invalid")?;
    let prior_owner = authority.get("prior_owner_id");
    let prior_epoch = authority.get("prior_source_epoch");
    match kind {
        AUTO_REPLY_CURSOR_FRESH_KIND => {
            if cursor_floor != attested_tail
                || prior_owner != Some(&serde_json::Value::Null)
                || prior_epoch != Some(&serde_json::Value::Null)
            {
                anyhow::bail!("CLI fresh cursor authority invalid");
            }
        }
        AUTO_REPLY_CURSOR_REPLAY_KIND | AUTO_REPLY_CURSOR_LEFTOVER_KIND => {
            if cursor_floor > attested_tail
                || prior_owner
                    .and_then(serde_json::Value::as_str)
                    .is_none_or(str::is_empty)
                || prior_epoch
                    .and_then(serde_json::Value::as_i64)
                    .is_none_or(|value| !(1..MAX_INT64).contains(&value))
            {
                anyhow::bail!("CLI replay cursor authority invalid");
            }
        }
        _ => anyhow::bail!("CLI enrollment cursor authority kind invalid"),
    }
    Ok((kind, attested_tail))
}

pub(super) fn validate_cli_enrollment_authority(
    enrollment: &serde_json::Value,
    target: i64,
    chat_name: &str,
    cursor_floor: i64,
    expected_room_root: &Path,
) -> Result<()> {
    if enrollment.get("schema_version")
        != Some(&serde_json::Value::from(
            AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION,
        ))
    {
        anyhow::bail!("CLI enrollment authority schema invalid");
    }
    let targets = enrollment
        .get("targets")
        .and_then(serde_json::Value::as_array)
        .filter(|targets| !targets.is_empty() && targets.len() <= 32)
        .context("CLI enrollment targets invalid")?;
    let matching_targets = targets
        .iter()
        .filter(|item| item.get("chat_id").and_then(serde_json::Value::as_i64) == Some(target))
        .collect::<Vec<_>>();
    if matching_targets.len() != 1 {
        anyhow::bail!("CLI enrollment target missing or ambiguous");
    }
    let enrolled = matching_targets[0];
    if !json_object_has_exact_keys(
        enrolled,
        &[
            "chat_id",
            "chat_name",
            "last_log_id",
            "room_state_root",
            "identity",
            "cursor_authority",
            "reply_author_bindings",
        ],
    ) {
        anyhow::bail!("CLI enrollment target schema invalid");
    }
    if enrolled
        .get("chat_name")
        .and_then(serde_json::Value::as_str)
        != Some(chat_name)
        || enrolled
            .get("last_log_id")
            .and_then(serde_json::Value::as_i64)
            != Some(cursor_floor)
    {
        anyhow::bail!("CLI enrollment target identity does not match DB readiness");
    }
    let enrolled_room_root = enrolled
        .get("room_state_root")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .context("CLI enrollment room root missing")?;
    let enrolled_room_root =
        fs::canonicalize(enrolled_room_root).context("canonicalize CLI enrollment room root")?;
    let expected_room_root =
        fs::canonicalize(expected_room_root).context("canonicalize CLI DB state room root")?;
    if enrolled_room_root != expected_room_root {
        anyhow::bail!("CLI enrollment room root does not match DB readiness");
    }
    let (cursor_kind, cursor_attested_tail) =
        cli_enrollment_cursor_authority(enrolled, cursor_floor)?;

    let identity = enrolled
        .get("identity")
        .filter(|value| value.is_object())
        .context("CLI enrollment identity missing")?;
    if identity.get("schema_version") != Some(&serde_json::Value::from(1))
        || identity.get("ax_name").and_then(serde_json::Value::as_str) != Some(chat_name)
    {
        anyhow::bail!("CLI enrollment identity invalid");
    }
    let kind = identity
        .get("kind")
        .and_then(serde_json::Value::as_str)
        .context("CLI enrollment identity kind missing")?;
    let local_name = identity
        .get("local_name")
        .and_then(serde_json::Value::as_str)
        .context("CLI enrollment local identity missing")?;
    match kind {
        "local_name" => {
            if local_name != chat_name
                || !json_object_has_exact_keys(
                    identity,
                    &["schema_version", "kind", "local_name", "ax_name"],
                )
            {
                anyhow::bail!("CLI enrollment local-name identity invalid");
            }
        }
        "ax_transcript" => {
            let expected_keys = [
                "schema_version",
                "kind",
                "local_name",
                "ax_name",
                "matched_log_ids",
                "matched_count",
                "matched_utf8_bytes",
                "transcript_sha256",
                "attested_db_last_log_id",
            ];
            let matched_log_ids = identity
                .get("matched_log_ids")
                .and_then(serde_json::Value::as_array)
                .context("CLI enrollment transcript log IDs missing")?;
            let parsed_log_ids = matched_log_ids
                .iter()
                .map(|value| {
                    value
                        .as_i64()
                        .filter(|id| 0 < *id && *id < MAX_INT64)
                        .context("CLI enrollment transcript log ID invalid")
                })
                .collect::<Result<Vec<_>>>()?;
            let unique_log_ids = parsed_log_ids
                .iter()
                .copied()
                .collect::<std::collections::BTreeSet<_>>();
            let matched_utf8_bytes = identity
                .get("matched_utf8_bytes")
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(-1);
            let attested_db_last_log_id = identity
                .get("attested_db_last_log_id")
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(-1);
            let transcript_sha256 = identity
                .get("transcript_sha256")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if !local_name.is_empty()
                || !json_object_has_exact_keys(identity, &expected_keys)
                || !(2..=20).contains(&parsed_log_ids.len())
                || unique_log_ids.len() != parsed_log_ids.len()
                || identity
                    .get("matched_count")
                    .and_then(serde_json::Value::as_i64)
                    != Some(parsed_log_ids.len() as i64)
                || matched_utf8_bytes < 0
                || (parsed_log_ids.len() >= 3 && matched_utf8_bytes < 24)
                || !(0 < attested_db_last_log_id && attested_db_last_log_id < MAX_INT64)
                || attested_db_last_log_id != cursor_attested_tail
                || attested_db_last_log_id
                    < parsed_log_ids.iter().copied().max().unwrap_or(MAX_INT64)
                || !is_lower_hex_sha256(transcript_sha256)
            {
                anyhow::bail!("CLI enrollment transcript identity invalid");
            }
        }
        _ => anyhow::bail!("CLI enrollment identity kind invalid"),
    }
    if matches!(
        cursor_kind,
        AUTO_REPLY_CURSOR_REPLAY_KIND | AUTO_REPLY_CURSOR_LEFTOVER_KIND
    ) && kind != "ax_transcript"
    {
        anyhow::bail!("CLI leftover or replay cursor requires transcript identity");
    }
    cli_enrollment_reply_author_bindings(enrolled)?;
    Ok(())
}

pub(super) fn require_cli_enrolled_reply_author(
    enrollment: &serde_json::Value,
    target: i64,
    author_id: i64,
    nickname: &str,
) -> Result<()> {
    let targets = enrollment
        .get("targets")
        .and_then(serde_json::Value::as_array)
        .context("CLI enrollment targets invalid")?;
    let matches = targets
        .iter()
        .filter(|item| item.get("chat_id").and_then(serde_json::Value::as_i64) == Some(target))
        .collect::<Vec<_>>();
    if matches.len() != 1 {
        anyhow::bail!("CLI enrollment target missing or ambiguous");
    }
    let bindings = cli_enrollment_reply_author_bindings(matches[0])?;
    if bindings.get(nickname) != Some(&author_id) {
        anyhow::bail!("scheduled reply author identity drifted; re-enrollment is required");
    }
    Ok(())
}
