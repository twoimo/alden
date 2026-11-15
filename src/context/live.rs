//! Live local-DB sync: validated event batches, per-source checkpoint and
//! burst state (`LiveSourceRow`), the summaries refreshed after ingest, and
//! which source is authoritative for a chat.

use super::embedding::{encode_vector, vector_to_bytes};
use super::response_time::{
    insert_response_time_stats, is_response_participant, summarize_response_delays,
    MAX_RESPONSE_DELAY_SECONDS,
};
use super::schema::LIVE_CONTEXT_SCHEMA_VERSION;
use super::store::{open_db, open_db_readonly};
use super::style::{
    classify_style_message, insert_style_profile, StyleProfileAccumulator, STYLE_POLICY_VERSION,
    STYLE_USER,
};
use super::topics::{insert_message_topics, message_has_interest_topic};
use anyhow::{Context, Result};
use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::Path;

pub(super) const LIVE_CONTEXT_BATCH_MAX_ROWS: usize = 200;

pub(super) const LIVE_CONTEXT_MAX_FIELD_BYTES: usize = 256 * 1024;

const LIVE_CONTEXT_MAX_PENDING_RECIPIENTS: usize = 64;

const LIVE_CONTEXT_SOURCE_PREFIX: &str = "local-db";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LiveContextEvent {
    pub chat_id: i64,
    pub log_id: i64,
    pub sender_name: String,
    pub message: String,
    pub sent_at: i64,
    #[serde(default)]
    pub is_self: bool,
    #[serde(default)]
    pub exclude_from_learning: bool,
    #[serde(default)]
    pub auto_generated: bool,
    #[serde(default)]
    pub attachment: String,
    #[serde(default)]
    pub message_type: i32,
    #[serde(default)]
    pub interest_only: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LiveContextSyncState {
    pub source: String,
    pub chat_id: i64,
    pub chat: String,
    pub checkpoint_log_id: i64,
    pub authoritative: bool,
    pub summary_dirty: bool,
    pub sync_status: String,
}

impl LiveContextSyncState {
    /// Startup and `--check` may proceed while summaries are dirty.
    ///
    /// `summary_dirty` means a pending burst still needs a summary refresh. The
    /// reply path already runs `context-sync-local` before grounded drafts, so
    /// blocking the whole worker on that flag leaves authorized inbound stuck
    /// behind a fence the worker itself is supposed to clear.
    pub fn allows_auto_reply_startup(&self, chat_id: i64, chat_name: &str) -> bool {
        // 로컬 DB에 이름이 없는 그룹방은 '그룹:<id>' 자리표시자로 저장된다. 화면
        // 이름으로 시작을 요청해도 같은 방으로 보고 통과시킨다.
        let placeholder = format!("그룹:{chat_id}");
        let name_matches =
            self.chat == chat_name || self.chat == placeholder || chat_name.trim() == placeholder;
        self.chat_id == chat_id
            && name_matches
            && self.authoritative
            && matches!(self.sync_status.as_str(), "ready" | "partial")
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LiveContextIngestResult {
    pub source: String,
    pub chat_id: i64,
    pub chat: String,
    pub checkpoint_log_id: i64,
    pub inserted_events: usize,
    pub duplicate_events: usize,
    pub indexed_messages: usize,
    pub style_messages: usize,
    pub response_samples: usize,
    pub recipient_style_samples: usize,
    pub summary_refreshed: bool,
    pub authoritative: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct PendingRecipient {
    recipient: String,
    log_id: i64,
}

#[derive(Debug, Default)]
pub(super) struct RecipientStyleAccumulator {
    pub(super) style: StyleProfileAccumulator,
    pub(super) sample_count: usize,
    pub(super) confidence_sum: f64,
}

fn push_unique_fragment(parts: &mut Vec<String>, value: &str) {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return;
    }
    if parts.iter().any(|part| part == trimmed) {
        return;
    }
    parts.push(trimmed.to_string());
}

fn collect_attachment_fragments(value: &serde_json::Value, parts: &mut Vec<String>) {
    match value {
        serde_json::Value::String(text) => {
            let trimmed = text.trim();
            if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
                push_unique_fragment(parts, trimmed);
            }
        }
        serde_json::Value::Array(items) => {
            for item in items {
                collect_attachment_fragments(item, parts);
            }
        }
        serde_json::Value::Object(map) => {
            for (key, child) in map {
                let key_lower = key.to_ascii_lowercase();
                if matches!(
                    key_lower.as_str(),
                    "urls"
                        | "url"
                        | "src_message"
                        | "name"
                        | "filename"
                        | "title"
                        | "t"
                        | "d"
                        | "description"
                        | "text"
                        | "message"
                        | "content"
                ) {
                    match child {
                        serde_json::Value::String(text) => push_unique_fragment(parts, text),
                        serde_json::Value::Array(items) => {
                            for item in items {
                                if let Some(text) = item.as_str() {
                                    push_unique_fragment(parts, text);
                                } else {
                                    collect_attachment_fragments(item, parts);
                                }
                            }
                        }
                        _ => collect_attachment_fragments(child, parts),
                    }
                } else {
                    collect_attachment_fragments(child, parts);
                }
            }
        }
        _ => {}
    }
}

pub(super) fn live_index_text(message: &str, attachment: &str, message_type: i32) -> String {
    let mut parts = Vec::new();
    push_unique_fragment(&mut parts, message);
    match message_type {
        2 => push_unique_fragment(&mut parts, "사진"),
        3 => push_unique_fragment(&mut parts, "동영상"),
        18 | 16 => push_unique_fragment(&mut parts, "파일"),
        27 => push_unique_fragment(&mut parts, "이모티콘"),
        71 => push_unique_fragment(&mut parts, "샵검색"),
        _ => {}
    }
    let trimmed_attachment = attachment.trim();
    if !trimmed_attachment.is_empty() {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed_attachment) {
            collect_attachment_fragments(&value, &mut parts);
        }
    }
    let mut text = parts.join("\n");
    text.truncate(text.floor_char_boundary(LIVE_CONTEXT_MAX_FIELD_BYTES));
    text
}

#[derive(Debug)]
struct LiveSourceRow {
    account_fingerprint: String,
    chat_id: i64,
    chat: String,
    authoritative: bool,
    checkpoint_log_id: i64,
    pending_human_log_id: Option<i64>,
    pending_human_sent_at: Option<i64>,
    pending_human_name: Option<String>,
    pending_burst: Vec<PendingRecipient>,
    summary_dirty: bool,
}

pub fn live_context_source_id(account_fingerprint: &str, chat_id: i64) -> Result<String> {
    let fingerprint = account_fingerprint.trim().to_ascii_lowercase();
    if fingerprint.len() != 64 || !fingerprint.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        anyhow::bail!("live context account fingerprint must be 64 hexadecimal characters");
    }
    if chat_id <= 0 {
        anyhow::bail!("live context chat ID must be positive");
    }
    Ok(format!(
        "{LIVE_CONTEXT_SOURCE_PREFIX}:{fingerprint}:{chat_id}"
    ))
}

pub(super) fn live_event_digest(event: &LiveContextEvent) -> Result<String> {
    let payload = serde_json::to_vec(&serde_json::json!({
        "chat_id": event.chat_id,
        "log_id": event.log_id,
        "sender_name": event.sender_name,
        "message": event.message,
        "sent_at": event.sent_at,
        "is_self": event.is_self,
        "exclude_from_learning": event.exclude_from_learning,
        "auto_generated": event.auto_generated,
    }))?;
    Ok(hex::encode(Sha256::digest(payload)))
}

pub(super) fn live_event_date(sent_at: i64) -> Result<String> {
    let date = chrono::DateTime::<Utc>::from_timestamp(sent_at, 0)
        .context("live context sent_at is outside the supported timestamp range")?;
    Ok(date.format("%Y-%m-%d %H:%M:%S").to_string())
}

fn validate_live_context_batch(
    chat_id: i64,
    chat: &str,
    expected_checkpoint: i64,
    events: &[LiveContextEvent],
    batch_complete: bool,
    promote_authoritative: bool,
) -> Result<()> {
    if chat_id <= 0 || expected_checkpoint < 0 {
        anyhow::bail!("live context chat ID and checkpoint are invalid");
    }
    if chat.trim().is_empty() || chat.len() > LIVE_CONTEXT_MAX_FIELD_BYTES {
        anyhow::bail!("live context chat name is invalid");
    }
    if events.len() > LIVE_CONTEXT_BATCH_MAX_ROWS {
        anyhow::bail!("live context batch exceeds {LIVE_CONTEXT_BATCH_MAX_ROWS} messages");
    }
    if promote_authoritative && !batch_complete {
        anyhow::bail!("live context source can only be promoted after a complete batch");
    }
    let mut previous_log_id = expected_checkpoint;
    for event in events {
        if event.chat_id != chat_id
            || event.log_id <= expected_checkpoint
            || event.log_id <= previous_log_id
            || ((event.auto_generated || event.exclude_from_learning) && !event.is_self)
            || event.sender_name.len() > LIVE_CONTEXT_MAX_FIELD_BYTES
            || event.message.len() > LIVE_CONTEXT_MAX_FIELD_BYTES
        {
            anyhow::bail!("live context batch contains an invalid or out-of-order event");
        }
        live_event_date(event.sent_at)?;
        previous_log_id = event.log_id;
    }
    Ok(())
}

fn load_live_source(conn: &Connection, source: &str) -> Result<Option<LiveSourceRow>> {
    conn.query_row(
        "SELECT account_fingerprint, chat_id, chat, authoritative,
                checkpoint_log_id, pending_human_log_id, pending_human_sent_at,
                pending_human_name, pending_burst_json, summary_dirty
         FROM context_sources WHERE source = ?1",
        [source],
        |row| {
            let pending_burst_json: String = row.get(8)?;
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(4)?,
                row.get::<_, Option<i64>>(5)?,
                row.get::<_, Option<i64>>(6)?,
                row.get::<_, Option<String>>(7)?,
                pending_burst_json,
                row.get::<_, i64>(9)?,
            ))
        },
    )
    .optional()?
    .map(
        |(
            account_fingerprint,
            chat_id,
            chat,
            authoritative,
            checkpoint_log_id,
            pending_human_log_id,
            pending_human_sent_at,
            pending_human_name,
            pending_burst_json,
            summary_dirty,
        )| {
            let pending_burst: Vec<PendingRecipient> = serde_json::from_str(&pending_burst_json)
                .context("live context pending recipient burst is malformed")?;
            let burst_is_invalid = pending_burst.len() > LIVE_CONTEXT_MAX_PENDING_RECIPIENTS
                || pending_burst.iter().any(|item| {
                    item.recipient.trim().is_empty()
                        || item.log_id <= 0
                        || item.log_id > checkpoint_log_id
                })
                || pending_burst
                    .windows(2)
                    .any(|items| items[0].log_id >= items[1].log_id);
            let pending_is_invalid = match (
                pending_human_log_id,
                pending_human_sent_at,
                pending_human_name.as_deref(),
            ) {
                (None, None, None) => !pending_burst.is_empty(),
                (Some(log_id), Some(sent_at), Some(name)) => {
                    log_id <= 0
                        || log_id > checkpoint_log_id
                        || name.trim().is_empty()
                        || live_event_date(sent_at).is_err()
                        || pending_burst
                            .last()
                            .is_none_or(|item| item.log_id != log_id || item.recipient != name)
                }
                _ => true,
            };
            if burst_is_invalid || pending_is_invalid {
                anyhow::bail!("live context pending recipient burst is invalid");
            }
            Ok(LiveSourceRow {
                account_fingerprint,
                chat_id,
                chat,
                authoritative: authoritative != 0,
                checkpoint_log_id,
                pending_human_log_id,
                pending_human_sent_at,
                pending_human_name,
                pending_burst,
                summary_dirty: summary_dirty != 0,
            })
        },
    )
    .transpose()
}

fn clear_pending_participant_burst(source: &mut LiveSourceRow) {
    source.pending_human_log_id = None;
    source.pending_human_sent_at = None;
    source.pending_human_name = None;
    source.pending_burst.clear();
}

fn recipient_confidences(burst: &[PendingRecipient]) -> Vec<(String, f64)> {
    let mut latest_by_recipient = BTreeMap::new();
    for item in burst {
        latest_by_recipient.insert(item.recipient.clone(), item.log_id);
    }
    let mut recipients = latest_by_recipient.into_iter().collect::<Vec<_>>();
    recipients.sort_by(|(left_name, left_log), (right_name, right_log)| {
        right_log
            .cmp(left_log)
            .then_with(|| left_name.cmp(right_name))
    });
    let total_weight = recipients
        .iter()
        .enumerate()
        .map(|(rank, _)| 1.0 / (rank as f64 + 1.0))
        .sum::<f64>();
    recipients
        .into_iter()
        .enumerate()
        .map(|(rank, (recipient, _))| (recipient, (1.0 / (rank as f64 + 1.0)) / total_weight))
        .collect()
}

pub(super) fn refresh_live_context_summaries(
    conn: &Connection,
    source: &str,
    chat_id: i64,
    chat: &str,
) -> Result<()> {
    let now = Utc::now().to_rfc3339();
    let style_messages = {
        let mut stmt = conn.prepare(
            "SELECT message FROM owner_style
             WHERE source = ?1 AND chat = ?2 AND user_name = ?3
               AND style_eligible = 1 AND policy_version = ?4
             ORDER BY source_row ASC, id ASC",
        )?;
        let rows = stmt.query_map(
            params![source, chat, STYLE_USER, STYLE_POLICY_VERSION],
            |row| row.get::<_, String>(0),
        )?;
        rows.collect::<rusqlite::Result<Vec<_>>>()?
    };
    let mut style_accumulator = StyleProfileAccumulator::default();
    for message in &style_messages {
        let features = classify_style_message(message);
        if features.style_eligible {
            style_accumulator.add(message, &features);
        }
    }
    conn.execute(
        "DELETE FROM owner_style_profile
         WHERE chat = ?1 AND source = ?2 AND user_name = ?3",
        params![chat, source, STYLE_USER],
    )?;
    if let Some(profile) = style_accumulator.finish(chat, source, STYLE_USER) {
        insert_style_profile(conn, &profile, &now)?;
    }

    let response_delays = {
        let mut stmt = conn.prepare(
            "SELECT delay_seconds FROM response_time_samples
             WHERE source = ?1 AND chat_id = ?2 ORDER BY reply_log_id ASC",
        )?;
        let rows = stmt.query_map(params![source, chat_id], |row| row.get::<_, f64>(0))?;
        rows.collect::<rusqlite::Result<Vec<_>>>()?
    };
    conn.execute(
        "DELETE FROM response_time_stats
         WHERE chat = ?1 AND source = ?2 AND user_name = ?3",
        params![chat, source, STYLE_USER],
    )?;
    if let Some(stats) = summarize_response_delays(chat, source, STYLE_USER, response_delays) {
        insert_response_time_stats(conn, &stats)?;
    }

    let recipient_rows = {
        let mut stmt = conn.prepare(
            "SELECT samples.recipient, samples.confidence, styles.message
             FROM owner_recipient_style_samples samples
             JOIN owner_style styles ON styles.id = samples.style_message_id
             WHERE samples.source = ?1 AND samples.chat_id = ?2
               AND styles.style_eligible = 1 AND styles.policy_version = ?3
             ORDER BY samples.recipient ASC, samples.reply_log_id ASC",
        )?;
        let rows = stmt.query_map(params![source, chat_id, STYLE_POLICY_VERSION], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, f64>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?;
        rows.collect::<rusqlite::Result<Vec<_>>>()?
    };
    let mut recipient_accumulators: BTreeMap<String, RecipientStyleAccumulator> = BTreeMap::new();
    for (recipient, confidence, message) in recipient_rows {
        if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) || confidence == 0.0 {
            anyhow::bail!("recipient style confidence is malformed");
        }
        let features = classify_style_message(&message);
        if !features.style_eligible {
            continue;
        }
        let accumulator = recipient_accumulators.entry(recipient).or_default();
        accumulator.style.add(&message, &features);
        accumulator.sample_count += 1;
        accumulator.confidence_sum += confidence;
    }
    conn.execute(
        "DELETE FROM owner_recipient_style_profile
         WHERE source = ?1 AND chat_id = ?2",
        params![source, chat_id],
    )?;
    for (recipient, accumulator) in recipient_accumulators {
        let Some(profile) = accumulator.style.finish(chat, source, STYLE_USER) else {
            continue;
        };
        conn.execute(
            "INSERT INTO owner_recipient_style_profile(
                source, chat_id, chat, recipient, user_name, sample_count,
                confidence_sum, average_character_length, median_character_length,
                p90_character_length, casual_ending_count, casual_ending_counts_json,
                question_count, emoji_count, punctuation_count, common_endings_json,
                common_tokens_json, policy_version, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
            params![
                source,
                chat_id,
                chat,
                recipient,
                STYLE_USER,
                accumulator.sample_count as i64,
                accumulator.confidence_sum,
                profile.average_character_length,
                profile.median_character_length,
                profile.p90_character_length,
                profile.casual_ending_count as i64,
                profile.casual_ending_counts_json,
                profile.question_count as i64,
                profile.emoji_count as i64,
                profile.punctuation_count as i64,
                profile.common_endings_json,
                profile.common_tokens_json,
                STYLE_POLICY_VERSION,
                now,
            ],
        )?;
    }
    Ok(())
}

fn live_source_has_required_summaries(conn: &Connection, source: &str, chat: &str) -> Result<bool> {
    let style_samples = conn
        .query_row(
            "SELECT sample_count FROM owner_style_profile
             WHERE chat = ?1 AND source = ?2 AND user_name = ?3
               AND policy_version = ?4",
            params![chat, source, STYLE_USER, STYLE_POLICY_VERSION],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
        .unwrap_or(0);
    let timing_samples = conn
        .query_row(
            "SELECT sample_count FROM response_time_stats
             WHERE chat = ?1 AND source = ?2 AND user_name = ?3",
            params![chat, source, STYLE_USER],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
        .unwrap_or(0);
    Ok(style_samples > 0 && timing_samples >= 2)
}

/// Atomically ingest one bounded `local-poll` page into the live context index.
///
/// `expected_checkpoint` must be the `after_log_id` used for the source poll.
/// Replaying a committed page is idempotent when every event digest matches.
/// `batch_complete` should only be true for a snapshot-complete page; expensive
/// style/timing summaries are refreshed at that boundary. Promotion makes the
/// complete live source take precedence over legacy CSV sources for the chat.
#[allow(clippy::too_many_arguments)]
pub fn ingest_live_context_events(
    db_path: &Path,
    account_fingerprint: &str,
    chat_id: i64,
    chat: &str,
    expected_checkpoint: i64,
    events: &[LiveContextEvent],
    batch_complete: bool,
    promote_authoritative: bool,
) -> Result<LiveContextIngestResult> {
    validate_live_context_batch(
        chat_id,
        chat,
        expected_checkpoint,
        events,
        batch_complete,
        promote_authoritative,
    )?;
    let fingerprint = account_fingerprint.trim().to_ascii_lowercase();
    let source_id = live_context_source_id(&fingerprint, chat_id)?;
    let mut conn = open_db(db_path)?;
    let tx = conn.transaction()?;
    let now = Utc::now().to_rfc3339();
    let mut source = match load_live_source(&tx, &source_id)? {
        Some(source) => source,
        None => {
            tx.execute(
                "INSERT INTO context_sources(
                    source, kind, account_fingerprint, chat_id, chat, authoritative,
                    checkpoint_log_id, pending_burst_json, summary_dirty, sync_status,
                    updated_at
                 ) VALUES (?1, 'local_db', ?2, ?3, ?4, 0, ?5, '[]', 0, 'ready', ?6)",
                params![
                    source_id,
                    fingerprint,
                    chat_id,
                    chat,
                    expected_checkpoint,
                    now,
                ],
            )?;
            LiveSourceRow {
                account_fingerprint: fingerprint.clone(),
                chat_id,
                chat: chat.to_string(),
                authoritative: false,
                checkpoint_log_id: expected_checkpoint,
                pending_human_log_id: None,
                pending_human_sent_at: None,
                pending_human_name: None,
                pending_burst: Vec::new(),
                summary_dirty: false,
            }
        }
    };
    // 이름 없는 그룹방은 '그룹:<id>' 자리표시자로 저장된다. 화면 이름으로 동기화해도
    // 같은 방이므로 통과시킨다.
    let placeholder = format!("그룹:{chat_id}");
    let name_matches = source.chat == chat || source.chat == placeholder;
    if source.account_fingerprint != fingerprint || source.chat_id != chat_id || !name_matches {
        anyhow::bail!("live context source identity changed");
    }
    if source.checkpoint_log_id < expected_checkpoint {
        anyhow::bail!("live context checkpoint is behind the caller checkpoint");
    }

    let mut inserted_events = 0usize;
    let mut duplicate_events = 0usize;
    let mut indexed_messages = 0usize;
    let mut style_messages = 0usize;
    let mut response_samples = 0usize;
    let mut recipient_style_samples = 0usize;
    let mut summaries_changed = source.summary_dirty;

    for event in events {
        let digest = live_event_digest(event)?;
        let existing_digest = tx
            .query_row(
                "SELECT message_digest FROM context_live_events
                 WHERE source = ?1 AND chat_id = ?2 AND log_id = ?3",
                params![source_id, chat_id, event.log_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if let Some(existing_digest) = existing_digest {
            if existing_digest != digest {
                anyhow::bail!("live context replay digest mismatch");
            }
            if event.log_id > source.checkpoint_log_id {
                anyhow::bail!("live context event exists beyond its checkpoint");
            }
            duplicate_events += 1;
            continue;
        }
        if event.log_id <= source.checkpoint_log_id {
            anyhow::bail!("live context checkpoint skipped an unrecorded event");
        }

        let date = live_event_date(event.sent_at)?;
        let sender = event.sender_name.trim();
        let message = event.message.trim();
        let mut context_message_id = None;
        let mut style_message_id = None;
        let disposition;

        if event.auto_generated || event.exclude_from_learning {
            disposition = if event.auto_generated {
                "auto_generated"
            } else {
                "ambiguous_auto_candidate"
            };
            clear_pending_participant_burst(&mut source);
        } else if sender.is_empty() {
            disposition = "unknown_author";
            clear_pending_participant_burst(&mut source);
        } else {
            let index_text = live_index_text(message, &event.attachment, event.message_type);
            let should_index = !index_text.is_empty()
                && (!event.interest_only || message_has_interest_topic(&index_text));
            if should_index {
                tx.execute(
                    "INSERT INTO context_messages(
                        source, chat, date, user_name, message, vector
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        source_id,
                        chat,
                        date,
                        sender,
                        index_text,
                        vector_to_bytes(&encode_vector(&format!("{sender} {index_text}"))),
                    ],
                )?;
                context_message_id = Some(tx.last_insert_rowid());
                insert_message_topics(
                    &tx,
                    context_message_id.unwrap_or(0),
                    chat,
                    &source_id,
                    &date,
                    &index_text,
                )?;
                indexed_messages += 1;
            }

            if event.is_self {
                disposition = if !should_index {
                    "filtered_noise"
                } else if message.is_empty() {
                    "empty"
                } else {
                    "style"
                };
                if should_index && !message.is_empty() {
                    let features = classify_style_message(message);
                    tx.execute(
                        "INSERT INTO owner_style(
                            source, chat, date, user_name, message, vector, source_row,
                            content_kind, style_eligible, policy_version, features_json
                         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                        params![
                            source_id,
                            chat,
                            date,
                            STYLE_USER,
                            message,
                            vector_to_bytes(&encode_vector(message)),
                            event.log_id,
                            features.content_kind,
                            features.style_eligible as i64,
                            STYLE_POLICY_VERSION,
                            features.features_json,
                        ],
                    )?;
                    style_message_id = Some(tx.last_insert_rowid());
                    style_messages += 1;
                    summaries_changed = true;

                    if let (Some(prompt_log_id), Some(prompt_sent_at), Some(recipient)) = (
                        source.pending_human_log_id,
                        source.pending_human_sent_at,
                        source.pending_human_name.as_deref(),
                    ) {
                        let delay = event.sent_at - prompt_sent_at;
                        if (0..=MAX_RESPONSE_DELAY_SECONDS).contains(&delay) {
                            response_samples += tx.execute(
                                "INSERT OR IGNORE INTO response_time_samples(
                                    source, chat_id, reply_log_id, prompt_log_id,
                                    recipient, delay_seconds
                                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                                params![
                                    source_id,
                                    chat_id,
                                    event.log_id,
                                    prompt_log_id,
                                    recipient,
                                    delay as f64,
                                ],
                            )?;
                        }
                    }
                    if features.style_eligible {
                        let confidences = recipient_confidences(&source.pending_burst);
                        let burst_size = confidences.len();
                        for (recipient, confidence) in confidences {
                            recipient_style_samples += tx.execute(
                                "INSERT OR IGNORE INTO owner_recipient_style_samples(
                                    source, chat_id, reply_log_id, recipient, style_message_id,
                                    burst_size, confidence
                                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                                params![
                                    source_id,
                                    chat_id,
                                    event.log_id,
                                    recipient,
                                    style_message_id,
                                    burst_size as i64,
                                    confidence,
                                ],
                            )?;
                        }
                    }
                    clear_pending_participant_burst(&mut source);
                }
            } else if is_response_participant(sender) {
                disposition = if message.is_empty() {
                    "empty"
                } else {
                    "context"
                };
                if !message.is_empty() {
                    source.pending_human_log_id = Some(event.log_id);
                    source.pending_human_sent_at = Some(event.sent_at);
                    source.pending_human_name = Some(sender.to_string());
                    source.pending_burst.push(PendingRecipient {
                        recipient: sender.to_string(),
                        log_id: event.log_id,
                    });
                    if source.pending_burst.len() > LIVE_CONTEXT_MAX_PENDING_RECIPIENTS {
                        let remove =
                            source.pending_burst.len() - LIVE_CONTEXT_MAX_PENDING_RECIPIENTS;
                        source.pending_burst.drain(0..remove);
                    }
                }
            } else {
                disposition = if message.is_empty() {
                    "empty"
                } else {
                    "context"
                };
                clear_pending_participant_burst(&mut source);
            }
        }

        tx.execute(
            "INSERT INTO context_live_events(
                source, chat_id, log_id, sent_at, sender_name, message_digest,
                disposition, auto_generated, context_message_id, style_message_id,
                created_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                source_id,
                chat_id,
                event.log_id,
                event.sent_at,
                sender,
                digest,
                disposition,
                event.auto_generated as i64,
                context_message_id,
                style_message_id,
                now,
            ],
        )?;
        source.checkpoint_log_id = event.log_id;
        inserted_events += 1;
    }

    let summary_refreshed = batch_complete && summaries_changed;
    if summary_refreshed {
        refresh_live_context_summaries(&tx, &source_id, chat_id, chat)?;
        source.summary_dirty = false;
    } else {
        source.summary_dirty = summaries_changed;
    }
    if promote_authoritative {
        if !live_source_has_required_summaries(&tx, &source_id, chat)? {
            anyhow::bail!("live context source lacks required style or timing samples");
        }
        tx.execute(
            "UPDATE context_sources SET authoritative = 0
             WHERE chat = ?1 AND source <> ?2",
            params![chat, source_id],
        )?;
        source.authoritative = true;
    }
    tx.execute(
        "UPDATE context_sources
         SET authoritative = ?2, checkpoint_log_id = ?3,
             pending_human_log_id = ?4, pending_human_sent_at = ?5,
             pending_human_name = ?6, pending_burst_json = ?7,
             summary_dirty = ?8, sync_status = ?9, updated_at = ?10
         WHERE source = ?1",
        params![
            source_id,
            source.authoritative as i64,
            source.checkpoint_log_id,
            source.pending_human_log_id,
            source.pending_human_sent_at,
            source.pending_human_name,
            serde_json::to_string(&source.pending_burst)?,
            source.summary_dirty as i64,
            if batch_complete { "ready" } else { "partial" },
            now,
        ],
    )?;
    tx.commit()?;
    Ok(LiveContextIngestResult {
        source: source_id,
        chat_id,
        chat: chat.to_string(),
        checkpoint_log_id: source.checkpoint_log_id,
        inserted_events,
        duplicate_events,
        indexed_messages,
        style_messages,
        response_samples,
        recipient_style_samples,
        summary_refreshed,
        authoritative: source.authoritative,
    })
}

pub fn live_context_sync_state(
    db_path: &Path,
    account_fingerprint: &str,
    chat_id: i64,
) -> Result<Option<LiveContextSyncState>> {
    let source = live_context_source_id(account_fingerprint, chat_id)?;
    let conn = open_db_readonly(db_path)?;
    let has_schema = conn
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = 'live_context_schema'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .as_deref()
        == Some(LIVE_CONTEXT_SCHEMA_VERSION);
    if !has_schema {
        anyhow::bail!("live context index migration required");
    }
    conn.query_row(
        "SELECT source, chat_id, chat, checkpoint_log_id, authoritative,
                summary_dirty, sync_status
         FROM context_sources WHERE source = ?1",
        [source],
        |row| {
            Ok(LiveContextSyncState {
                source: row.get(0)?,
                chat_id: row.get(1)?,
                chat: row.get(2)?,
                checkpoint_log_id: row.get(3)?,
                authoritative: row.get::<_, i64>(4)? != 0,
                summary_dirty: row.get::<_, i64>(5)? != 0,
                sync_status: row.get(6)?,
            })
        },
    )
    .optional()
    .map_err(Into::into)
}

pub(super) fn preferred_context_source(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
) -> Result<Option<String>> {
    if let Some(source) = source {
        return Ok(Some(source.to_string()));
    }
    let Some(chat) = chat else {
        return Ok(None);
    };
    let has_sources_table = conn.query_row(
        "SELECT EXISTS(
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'context_sources'
         )",
        [],
        |row| row.get::<_, i64>(0),
    )? != 0;
    if !has_sources_table {
        return Ok(None);
    }
    conn.query_row(
        "SELECT source FROM context_sources
         WHERE chat = ?1 AND authoritative = 1
         ORDER BY updated_at DESC, source ASC LIMIT 1",
        [chat],
        |row| row.get::<_, String>(0),
    )
    .optional()
    .map_err(Into::into)
}
