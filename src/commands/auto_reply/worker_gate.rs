//! What the AutoReply worker must prove before `local-send` mutates anything:
//! its locks, persisted readiness, the CLI enrollment, and the source message.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use super::enrollment::{
    require_cli_enrolled_reply_author, validate_cli_enrollment_authority,
    validate_cli_enrollment_digest,
};
use super::state_root::{read_bounded_file, read_bounded_json_file};
use super::MAX_INT64;
#[cfg(unix)]
use crate::auto_reply_runtime::acquire_worker_setup_lock;
use crate::commands::local_send::{require_allowed_send_chat, require_ax_send};
use crate::{ax_send, commands, config, local_db};
use openkakao_cli::auto_reply_service;

const AUTO_REPLY_READINESS_MAX_AGE_SECONDS: f64 = 15.0;

fn readiness_fresh(value: &serde_json::Value, now: f64) -> bool {
    let stamp = value.as_f64().or_else(|| value.as_i64().map(|v| v as f64));
    stamp.is_some_and(|stamp| {
        stamp.is_finite()
            && -5.0 <= now - stamp
            && now - stamp <= AUTO_REPLY_READINESS_MAX_AGE_SECONDS
    })
}

fn require_persisted_auto_reply_readiness(
    expected_owner: Option<&str>,
    expected_epoch: Option<i64>,
    expected_target: Option<i64>,
    expected_chat_name: Option<&str>,
    expected_last_observed: Option<i64>,
) -> Result<()> {
    let home = dirs::home_dir().context("cannot resolve home directory")?;
    let base = auto_reply_service::default_state_root(&home);
    let supervisor_path = std::env::var_os("OPENKAKAO_SUPERVISOR_STATUS")
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| base.join("supervisor-status.json"));
    let db_state_path = std::env::var_os("OPENKAKAO_DB_WATCH_STATE")
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| base.join("db-watch-state.json"));
    let supervisor = read_bounded_json_file(&supervisor_path)?;
    let db_state = read_bounded_json_file(&db_state_path)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .context("system clock before UNIX epoch")?
        .as_secs_f64();
    let owner = supervisor
        .get("owner")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .context("supervisor owner missing")?;
    if expected_owner.is_some_and(|expected| owner != expected) {
        anyhow::bail!("persisted supervisor owner does not match event");
    }
    let source_epoch = supervisor
        .get("source_epoch")
        .context("supervisor source epoch missing")?
        .as_i64()
        .filter(|value| 0 < *value && *value < MAX_INT64)
        .context("supervisor source epoch invalid")?;
    let target = supervisor
        .get("target_chat_id")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| 0 < *value && *value < MAX_INT64)
        .context("supervisor target chat missing")?;
    let chat_name = supervisor
        .get("target_chat_name")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .context("supervisor target chat name missing")?;
    if expected_epoch.is_some_and(|expected| source_epoch != expected) {
        anyhow::bail!("persisted supervisor epoch does not match event");
    }
    if expected_target.is_some_and(|expected| target != expected) {
        anyhow::bail!("persisted supervisor target does not match event");
    }
    if expected_chat_name.is_some_and(|expected| chat_name != expected) {
        anyhow::bail!("persisted supervisor target name does not match event");
    }
    let privacy_digest = supervisor
        .get("privacy_digest")
        .and_then(serde_json::Value::as_str)
        .context("supervisor privacy attestation missing")?;
    if privacy_digest.len() != 64 || !privacy_digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        anyhow::bail!("supervisor privacy attestation invalid");
    }
    let config_path = std::env::var_os("OPENKAKAO_CONFIG")
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            dirs::home_dir()
                .unwrap_or_else(|| Path::new(".").to_path_buf())
                .join(".config/openkakao/config.toml")
        });
    let config_digest = hex::encode(Sha256::digest(
        fs::read(&config_path).with_context(|| "read privacy config")?,
    ));
    if config_digest != privacy_digest
        || std::env::var("OPENKAKAO_PRIVACY_ATTESTATION")
            .ok()
            .is_some_and(|value| value != privacy_digest)
    {
        anyhow::bail!("privacy attestation changed");
    }
    if supervisor.get("schema_version") != Some(&serde_json::Value::from(1))
        || supervisor.get("mode")
            != Some(&serde_json::Value::String(
                "database_authoritative".to_string(),
            ))
    {
        anyhow::bail!("supervisor readiness schema invalid");
    }
    if supervisor.get("readiness") != Some(&serde_json::Value::String("ready".to_string()))
        || supervisor.get("state") != Some(&serde_json::Value::String("running".to_string()))
        || supervisor.get("database_started") != Some(&serde_json::Value::Bool(true))
        || supervisor.get("auto_reply_enabled") != Some(&serde_json::Value::Bool(true))
        || supervisor.get("ax_state") != Some(&serde_json::Value::String("healthy".to_string()))
        || supervisor
            .get("ax_pid")
            .and_then(serde_json::Value::as_i64)
            .filter(|value| 0 < *value && *value < MAX_INT64)
            .is_none()
        || supervisor.get("ax_allow_send") != Some(&serde_json::Value::Bool(false))
        || supervisor.get("ax_delivery_state")
            != Some(&serde_json::Value::String(
                "fenced_db_authoritative".to_string(),
            ))
        || supervisor.get("delivery_state")
            != Some(&serde_json::Value::String(
                "fenced_db_authoritative".to_string(),
            ))
        || supervisor
            .get("watcher_fence")
            .and_then(serde_json::Value::as_object)
            .and_then(|value| value.get("ax_allow_send"))
            != Some(&serde_json::Value::Bool(false))
        || supervisor
            .get("fence_reason")
            .and_then(serde_json::Value::as_str)
            != Some("")
        || !readiness_fresh(
            supervisor
                .get("updated_at")
                .context("supervisor heartbeat missing")?,
            now,
        )
    {
        anyhow::bail!("persisted supervisor readiness is fenced");
    }
    let db_owner = db_state
        .get("owner_id")
        .and_then(serde_json::Value::as_str)
        .context("DB owner missing")?;
    if db_owner != owner {
        anyhow::bail!("persisted DB owner mismatch");
    }
    let db_epoch = db_state
        .get("source_epoch")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| 0 < *value && *value < MAX_INT64)
        .context("DB source epoch invalid")?;
    let db_target = db_state
        .get("target_chat_id")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| 0 < *value && *value < MAX_INT64)
        .context("DB target chat missing")?;
    let cli_state_v3 = std::env::var("OPENKAKAO_AUTO_REPLY_CLI").as_deref() == Ok("1");
    let expected_db_schema = if cli_state_v3 { 3 } else { 2 };
    if db_state.get("schema_version") != Some(&serde_json::Value::from(expected_db_schema))
        || db_state.get("target_chat_name")
            != Some(&serde_json::Value::String(chat_name.to_string()))
    {
        anyhow::bail!("DB readiness schema or target name invalid");
    }
    if db_epoch != source_epoch
        || db_target != target
        || db_state.get("capability_state") != Some(&serde_json::Value::String("ready".to_string()))
        || db_state.get("delivery_enabled") != Some(&serde_json::Value::Bool(true))
        || db_state.get("fence") != Some(&serde_json::Value::String("ready".to_string()))
        || db_state
            .get("fence_reason")
            .and_then(serde_json::Value::as_str)
            != Some("")
        || !readiness_fresh(
            db_state
                .get("heartbeat_at")
                .context("DB heartbeat missing")?,
            now,
        )
        || db_state.get("pending_log_ids") != Some(&serde_json::Value::Array(Vec::new()))
        || db_state.get("pending_gaps") != Some(&serde_json::Value::Array(Vec::new()))
    {
        anyhow::bail!("persisted DB readiness is fenced");
    }
    let watermark = db_state
        .get("acked_watermark")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| 0 <= *value && *value < MAX_INT64)
        .context("DB watermark invalid")?;
    if cli_state_v3 {
        let cursor_floor = db_state
            .get("cursor_floor")
            .and_then(serde_json::Value::as_i64)
            .filter(|value| 0 <= *value && *value < MAX_INT64)
            .context("CLI DB cursor floor invalid")?;
        if cursor_floor > watermark
            || db_state.get("candidate_phase")
                != Some(&serde_json::Value::String("idle".to_string()))
            || db_state.get("in_flight_candidate") != Some(&serde_json::Value::Null)
        {
            anyhow::bail!("CLI DB candidate state is not idle");
        }
        let enrollment_path = std::env::var_os("OPENKAKAO_ENROLLMENT_PATH")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .context("CLI enrollment authority missing")?;
        let enrollment_raw = read_bounded_file(&enrollment_path)?;
        let expected_enrollment_digest =
            std::env::var("OPENKAKAO_ENROLLMENT_SHA256").unwrap_or_default();
        validate_cli_enrollment_digest(&enrollment_raw, expected_enrollment_digest.trim())?;
        let enrollment: serde_json::Value =
            serde_json::from_slice(&enrollment_raw).context("malformed CLI enrollment JSON")?;
        let expected_room_root = db_state_path
            .parent()
            .context("CLI DB state room root missing")?;
        validate_cli_enrollment_authority(
            &enrollment,
            target,
            chat_name,
            cursor_floor,
            expected_room_root,
        )?;
    }
    let persisted_last_observed = db_state
        .get("last_observed_log_id")
        .and_then(serde_json::Value::as_i64)
        .filter(|value| watermark <= *value && *value < MAX_INT64)
        .context("DB observed watermark invalid")?;
    if expected_last_observed.is_some_and(|expected| persisted_last_observed < expected) {
        anyhow::bail!("scheduled reply source is not in the persisted watcher tail");
    }
    let observed = db_state
        .get("observed_log_ids")
        .and_then(serde_json::Value::as_array)
        .context("DB observed IDs missing")?;
    let acked = db_state
        .get("acked_log_ids")
        .and_then(serde_json::Value::as_array)
        .context("DB acked IDs missing")?;
    let pending = db_state
        .get("pending_log_ids")
        .and_then(serde_json::Value::as_array)
        .context("DB pending IDs missing")?;
    let parse_ids = |values: &[serde_json::Value]| -> Result<Vec<i64>> {
        values
            .iter()
            .map(|value| {
                value
                    .as_i64()
                    .filter(|id| 0 < *id && *id < MAX_INT64)
                    .context("DB readiness ID invalid")
            })
            .collect()
    };
    let observed_ids = parse_ids(observed)?;
    let acked_ids = parse_ids(acked)?;
    let pending_ids = parse_ids(pending)?;
    let observed_set: std::collections::BTreeSet<i64> = observed_ids.iter().copied().collect();
    let acked_set: std::collections::BTreeSet<i64> = acked_ids.iter().copied().collect();
    let pending_set: std::collections::BTreeSet<i64> = pending_ids.iter().copied().collect();
    let expected_last_observed = observed_set.iter().next_back().copied().unwrap_or(0);
    if !acked_set.is_subset(&observed_set)
        || pending_set != observed_set.difference(&acked_set).copied().collect()
        || !pending_set.is_disjoint(&acked_set)
        || watermark != acked_set.iter().next_back().copied().unwrap_or(0)
        || db_state
            .get("last_observed_log_id")
            .and_then(serde_json::Value::as_i64)
            != Some(expected_last_observed)
    {
        anyhow::bail!("DB readiness cursor sets invalid");
    }
    Ok(())
}

/// Resolve the scheduled send's source row for its author attestation.
///
/// The bound send attests that the row it answers has the expected author, and
/// it used to look for that row only in the newest 20 messages. A scheduled
/// reply fires after a debounce window, so in an active room the source row is
/// usually older than those 20 rows; the preflight then failed with "scheduled
/// reply source row is unavailable" and the job died as stale_backlog
/// (2026-09-22, room 417: 70 of 202 preflight attempts). Prefer the page that
/// was already read, then fall back to a direct log-id lookup on the same
/// read-only reader. A row that is genuinely gone still fails closed.
pub(super) fn resolve_bound_source_message<F>(
    windowed: &[local_db::LocalMessage],
    expected_source_log_id: i64,
    lookup: F,
) -> Result<local_db::LocalMessage>
where
    F: FnOnce(i64) -> Result<Option<local_db::LocalMessage>>,
{
    if let Some(message) = windowed
        .iter()
        .find(|message| message.log_id == expected_source_log_id)
    {
        return Ok(message.clone());
    }
    lookup(expected_source_log_id)
        .context("read the scheduled reply source row")?
        .context("scheduled reply source row is unavailable")
}
pub(super) fn require_expected_local_source_tail(
    target_chat_id: i64,
    expected_source_log_id: i64,
    source_fence: &local_db::LocalPollEnvelope,
) -> Result<()> {
    let completeness = &source_fence.completeness;
    // Later room rows must not cancel an earlier unanswered inbound. The
    // poll starts after the scheduled source; an empty page means the
    // source is still the tail, and a complete later page is another
    // per-message job, not a reason to drop this send.
    if source_fence.chat.chat_id != target_chat_id
        || completeness.after_log_id != expected_source_log_id
        || completeness.has_gap
        || completeness.chat_last_log_id < expected_source_log_id
    {
        anyhow::bail!("scheduled reply source tail is unavailable");
    }
    if completeness.status == "empty" {
        if completeness.has_more
            || completeness.first_log_id.is_some()
            || completeness.last_log_id.is_some()
            || completeness.available_max_log_id.is_some()
            || !source_fence.messages.is_empty()
            || completeness.chat_last_log_id != expected_source_log_id
        {
            anyhow::bail!("scheduled reply source tail is unavailable");
        }
        return Ok(());
    }
    if completeness.status != "complete" && completeness.status != "partial" {
        anyhow::bail!("scheduled reply source tail is unavailable");
    }
    if source_fence.messages.is_empty()
        || completeness.first_log_id != source_fence.messages.first().map(|row| row.log_id)
        || completeness.last_log_id != source_fence.messages.last().map(|row| row.log_id)
        || source_fence
            .messages
            .iter()
            .any(|row| row.log_id <= expected_source_log_id || row.chat_id != target_chat_id)
    {
        anyhow::bail!("scheduled reply source tail is unavailable");
    }
    Ok(())
}

pub(super) fn require_auto_reply_worker_preflight(
    preflight: bool,
    worker_identity: bool,
) -> Result<()> {
    if preflight && !worker_identity {
        anyhow::bail!(
            "local-send --preflight is only available to the database-authoritative AutoReply worker"
        );
    }
    Ok(())
}

pub(super) fn finish_worker_bound_local_send_setup<T>(
    setup: Result<T>,
    is_auto_reply_worker: bool,
    preflight: bool,
    chat_name: &str,
    json: bool,
) -> Result<Option<T>> {
    match setup {
        Ok(value) => Ok(Some(value)),
        Err(_error) if is_auto_reply_worker && !preflight => {
            commands::local_send::emit_pre_send_unavailable(chat_name, json)?;
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

/// What `local-send` holds for its whole run. An AutoReply worker holds the
/// owner-generation and AX send locks until this drops, and carries the
/// database-authoritative binding the AX sender re-attests. A plain run holds
/// neither lock and carries no binding.
pub struct LocalSendSetup {
    _generation_lock: Option<fs::File>,
    _send_lock: Option<fs::File>,
    pub bound_chat: Option<commands::local_send::BoundLocalSend>,
}

/// Run the AutoReply worker's lock, readiness, and binding gates before a
/// `local-send` mutates anything. `None` means the worker setup failed and the
/// pre-send unavailable result was already emitted.
pub fn prepare_local_send(
    config: &config::OpenKakaoConfig,
    chat_name: &str,
    dry_run: bool,
    preflight: bool,
    json: bool,
) -> Result<Option<LocalSendSetup>> {
    let worker_identity = std::env::var("OPENKAKAO_AUTO_REPLY_WORKER")
        .or_else(|_| std::env::var("OPENKAKAO_BUJAMENTOR_WORKER"))
        .as_deref()
        == Ok("1");
    require_auto_reply_worker_preflight(preflight, worker_identity)?;
    let is_auto_reply_worker = !dry_run && worker_identity;
    let setup = (|| -> Result<_> {
        let _generation_lock = if is_auto_reply_worker && !preflight {
            let home = dirs::home_dir().context("cannot resolve home directory")?;
            let lock_path = std::env::var_os("OPENKAKAO_AUTO_REPLY_GENERATION_LOCK")
                .or_else(|| std::env::var_os("OPENKAKAO_AUTO_REPLY_LOCK"))
                .or_else(|| std::env::var_os("OPENKAKAO_BUJAMENTOR_GENERATION_LOCK"))
                .or_else(|| std::env::var_os("OPENKAKAO_BUJAMENTOR_LOCK"))
                .filter(|value| !value.is_empty())
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| {
                    auto_reply_service::default_state_root(&home).join(".owner-generation.lock")
                });
            let lock = fs::OpenOptions::new()
                .create(true)
                .truncate(false)
                .read(true)
                .write(true)
                .open(lock_path)?;
            #[cfg(unix)]
            acquire_worker_setup_lock(
                &lock,
                "owner-generation lock",
                std::time::Duration::from_millis(400),
            )?;
            Some(lock)
        } else {
            None
        };
        let _send_lock = if is_auto_reply_worker && !preflight {
            if let Some(lock_path) =
                std::env::var_os("OPENKAKAO_AUTO_REPLY_SEND_LOCK").filter(|value| !value.is_empty())
            {
                let lock = fs::OpenOptions::new()
                    .create(true)
                    .truncate(false)
                    .read(true)
                    .write(true)
                    .open(lock_path)?;
                #[cfg(unix)]
                acquire_worker_setup_lock(
                    &lock,
                    "AX send lock",
                    std::time::Duration::from_millis(400),
                )?;
                Some(lock)
            } else {
                None
            }
        } else {
            None
        };
        let mut bound_chat = None;
        if !dry_run {
            let mut worker_target = None;
            if is_auto_reply_worker {
                config::validate_auto_reply(config)?;
                let expected_owner = std::env::var("OPENKAKAO_SUPERVISOR_OWNER")
                    .ok()
                    .filter(|value| !value.trim().is_empty())
                    .context("worker owner missing")?;
                let expected_epoch = std::env::var("OPENKAKAO_DB_SOURCE_EPOCH")
                    .ok()
                    .and_then(|value| value.parse::<i64>().ok())
                    .filter(|value| 0 < *value && *value < MAX_INT64)
                    .context("worker source epoch invalid")?;
                let expected_target = std::env::var("OPENKAKAO_TARGET_CHAT_ID")
                    .ok()
                    .and_then(|value| value.parse::<i64>().ok())
                    .filter(|value| 0 < *value && *value < MAX_INT64)
                    .context("worker target chat invalid")?;
                let expected_last_observed = std::env::var("OPENKAKAO_EXPECTED_SOURCE_LOG_ID")
                    .ok()
                    .and_then(|value| value.parse::<i64>().ok())
                    .filter(|value| 0 < *value && *value < MAX_INT64)
                    .context("worker scheduled source log ID invalid")?;
                let expected_author_id = std::env::var("OPENKAKAO_EXPECTED_SOURCE_AUTHOR_ID")
                    .ok()
                    .and_then(|value| value.parse::<i64>().ok())
                    .filter(|value| 0 < *value && *value < MAX_INT64)
                    .context("worker scheduled source author ID invalid")?;
                let expected_author_nickname =
                    std::env::var("OPENKAKAO_EXPECTED_SOURCE_AUTHOR_NICKNAME")
                        .ok()
                        .filter(|value| {
                            !value.is_empty()
                                && value.len() <= 1024
                                && value.trim() == value
                                && !value.chars().any(char::is_control)
                        })
                        .context("worker scheduled source author nickname invalid")?;
                let proactive_send = std::env::var("OPENKAKAO_PROACTIVE_SEND")
                    .ok()
                    .is_some_and(|value| value == "1");
                require_persisted_auto_reply_readiness(
                    Some(expected_owner.as_str()),
                    Some(expected_epoch),
                    Some(expected_target),
                    Some(chat_name),
                    Some(expected_last_observed),
                )?;
                worker_target = Some((
                    expected_target,
                    expected_last_observed,
                    expected_author_id,
                    expected_author_nickname,
                    proactive_send,
                ));
            }
            require_ax_send(config)?;
            require_allowed_send_chat(config, chat_name)?;
            if let Some((
                target_chat_id,
                expected_last_observed,
                expected_author_id,
                expected_author_nickname,
                proactive_send,
            )) = worker_target
            {
                // Fetch the authoritative local tail only after both
                // process locks and all persisted/safety gates are held.
                // The AX sender will re-attest this numeric ID's latest
                // suffix against the exact open window before mutation.
                let reader = local_db::LocalDbReader::open_no_mutation()
                    .context("open local database for bound AX send attestation")?;
                let mut messages = reader.read_messages(target_chat_id, 20, None)?;
                let source_fence =
                    reader.poll_after(target_chat_id, 1, Some(expected_last_observed))?;
                require_expected_local_source_tail(
                    target_chat_id,
                    expected_last_observed,
                    &source_fence,
                )?;
                let source_message =
                    resolve_bound_source_message(&messages, expected_last_observed, |log_id| {
                        reader.read_message_by_log_id(target_chat_id, log_id)
                    })?;
                if proactive_send {
                    if !source_message.is_self
                        && (source_message.author_id != expected_author_id
                            || source_message.sender_name.trim() != expected_author_nickname)
                    {
                        anyhow::bail!(
                                    "scheduled reply source author identity drifted; re-enrollment is required"
                                );
                    }
                } else if source_message.is_self
                    || source_message.author_id != expected_author_id
                    || source_message.sender_name.trim() != expected_author_nickname
                {
                    anyhow::bail!(
                        "scheduled reply source author identity drifted; re-enrollment is required"
                    );
                }
                let enrollment_path = std::env::var_os("OPENKAKAO_ENROLLMENT_PATH")
                    .filter(|value| !value.is_empty())
                    .map(PathBuf::from)
                    .context("CLI enrollment authority missing")?;
                let enrollment_raw = read_bounded_file(&enrollment_path)?;
                let expected_enrollment_digest =
                    std::env::var("OPENKAKAO_ENROLLMENT_SHA256").unwrap_or_default();
                validate_cli_enrollment_digest(&enrollment_raw, expected_enrollment_digest.trim())?;
                let enrollment: serde_json::Value = serde_json::from_slice(&enrollment_raw)
                    .context("malformed CLI enrollment JSON")?;
                require_cli_enrolled_reply_author(
                    &enrollment,
                    target_chat_id,
                    expected_author_id,
                    &expected_author_nickname,
                )?;
                messages.reverse();
                let local_tail = ax_send::normalize_local_binding_suffix(&messages)
                    .into_iter()
                    .map(|(_, token)| token)
                    .collect();
                bound_chat = Some(commands::local_send::BoundLocalSend {
                    chat_id: target_chat_id,
                    expected_source_log_id: expected_last_observed,
                    local_tail,
                });
            }
        }
        Ok(LocalSendSetup {
            _generation_lock,
            _send_lock,
            bound_chat,
        })
    })();
    finish_worker_bound_local_send_setup(setup, is_auto_reply_worker, preflight, chat_name, json)
}
