//! Exported chat CSV import into the context index.

use super::embedding::{encode_vector, vector_to_bytes};
use super::response_time::{
    is_response_participant, parse_chat_date, summarize_response_delays, MAX_RESPONSE_DELAY_SECONDS,
};
use super::schema::rebuild_retrieval_index;
use super::self_send::{
    confirmed_self_sends_from_queue, confirmed_self_sends_with_connection, mark_bot_sent_features,
    match_confirmed_send,
};
use super::store::{checkpoint_context_wal_truncate, open_db, set_context_db_permissions};
use super::style::{
    classify_style_message, StyleProfileAccumulator, STYLE_POLICY_VERSION, STYLE_USER,
};
use super::topics::insert_message_topics;
use anyhow::{Context, Result};
use chrono::{NaiveDateTime, Utc};
use csv::ReaderBuilder;
use rusqlite::params;
use std::path::Path;

pub fn index_csv(db_path: &Path, chat: &str, input: &Path) -> Result<usize> {
    index_csv_with_queue(db_path, chat, input, None)
}

/// Import one chat history CSV, excluding replies the assistant itself sent.
///
/// A CSV row carries no room log id, so the send authorities are matched by
/// exact text inside [`SELF_SEND_REPAIR_WINDOW_SECONDS`](super::SELF_SEND_REPAIR_WINDOW_SECONDS): the decision ledger
/// plus, when the caller can name it, the room delivery queue. Both are needed
/// because a reply that only the queue recorded as `sent` would otherwise be
/// learned as an owner sample on the next import.
pub fn index_csv_with_queue(
    db_path: &Path,
    chat: &str,
    input: &Path,
    queue_path: Option<&Path>,
) -> Result<usize> {
    if chat.trim().is_empty() {
        anyhow::bail!("chat name must not be empty");
    }
    let source = input
        .canonicalize()
        .with_context(|| format!("failed to resolve CSV {}", input.display()))?
        .display()
        .to_string();
    let conn = open_db(db_path)?;
    let mut reader = ReaderBuilder::new()
        .flexible(true)
        .from_path(input)
        .with_context(|| format!("failed to open CSV {}", input.display()))?;
    let headers = reader.headers().context("CSV has no header row")?.clone();
    let date_idx = headers
        .iter()
        .position(|h| h.trim_start_matches('\u{feff}') == "Date");
    let user_idx = headers.iter().position(|h| h == "User");
    let message_idx = headers.iter().position(|h| h == "Message");
    let (Some(date_idx), Some(user_idx), Some(message_idx)) = (date_idx, user_idx, message_idx)
    else {
        anyhow::bail!("CSV must contain Date, User, and Message columns");
    };
    let required = date_idx.max(user_idx).max(message_idx);
    // A bot reply that was already confirmed must not be re-learned as an owner
    // style sample when the chat history is imported from CSV.
    let mut confirmed_sends = confirmed_self_sends_with_connection(&conn, chat).unwrap_or_default();
    if let Some(queue) = queue_path {
        for send in confirmed_self_sends_from_queue(queue)? {
            if !confirmed_sends
                .iter()
                .any(|existing| existing.event_id == send.event_id)
            {
                confirmed_sends.push(send);
            }
        }
    }
    let tx = conn.unchecked_transaction()?;
    tx.execute("DELETE FROM context_messages WHERE source = ?1", [&source])?;
    tx.execute("DELETE FROM owner_style WHERE source = ?1", [&source])?;
    tx.execute(
        "DELETE FROM owner_style_profile WHERE source = ?1",
        [&source],
    )?;
    let mut previous_human_at: Option<NaiveDateTime> = None;
    let mut response_delays = Vec::new();
    let mut count = 0;
    let mut profile_accumulator = StyleProfileAccumulator::default();
    for (source_row, row) in reader.records().enumerate() {
        let row = row.context("invalid CSV record")?;
        if row.len() <= required {
            anyhow::bail!("CSV record has fewer fields than its header");
        }
        let message = row.get(message_idx).unwrap_or_default().trim().to_string();
        if message.is_empty() {
            continue;
        }
        let date = row.get(date_idx).unwrap_or_default().to_string();
        let user = row.get(user_idx).unwrap_or_default().to_string();
        let row_sent_at = parse_chat_date(&date).map(|value| value.and_utc().timestamp());
        // Identify a bot-sent row *before* it can become style, pacing or
        // recipient evidence. A confirmed send is not a human reply, so it must
        // not produce a response-time sample either.
        let bot_send = if user == STYLE_USER {
            row_sent_at.and_then(|sent_at| {
                let (matched, ambiguous) =
                    match_confirmed_send(&confirmed_sends, &message, sent_at);
                matched
                    .filter(|_| !ambiguous)
                    .map(|send| (send, sent_at - send.sent_at))
            })
        } else {
            None
        };
        if let Some(current_at) = parse_chat_date(&date) {
            if user == STYLE_USER {
                if bot_send.is_some() {
                    // Drop the pending prompt instead of pairing a later human
                    // reply with it: this turn was the assistant, not the owner.
                    previous_human_at = None;
                } else if let Some(previous_at) = previous_human_at.take() {
                    let delay = (current_at - previous_at).num_seconds();
                    if (0..=MAX_RESPONSE_DELAY_SECONDS).contains(&delay) {
                        response_delays.push(delay as f64);
                    }
                }
            } else if is_response_participant(&user) {
                previous_human_at = Some(current_at);
            }
        }
        let vector = encode_vector(&format!("{} {}", user, message));
        // `tx.execute` re-parses this INSERT on every CSV row; the prepared
        // statement cache keeps the parse cost to one per connection.
        tx.prepare_cached("INSERT INTO context_messages(source, chat, date, user_name, message, vector) VALUES (?1, ?2, ?3, ?4, ?5, ?6)")
            .and_then(|mut stmt| {
                stmt.execute(params![source, chat, date, user, message, vector_to_bytes(&vector)])
            })?;
        insert_message_topics(&tx, tx.last_insert_rowid(), chat, &source, &date, &message)?;
        if user == STYLE_USER {
            let mut features = classify_style_message(&message);
            if let Some((send, offset)) = bot_send {
                features.style_eligible = false;
                features.content_kind = "bot_sent_reply";
                features.features_json =
                    mark_bot_sent_features(&features.features_json, &send.event_id, offset);
            }
            tx.prepare_cached(
                "INSERT INTO owner_style(source, chat, date, user_name, message, vector, source_row, content_kind, style_eligible, policy_version, features_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            )
            .and_then(|mut stmt| {
                stmt.execute(params![
                    source,
                    chat,
                    date,
                    user,
                    message,
                    vector_to_bytes(&encode_vector(&message)),
                    source_row as i64 + 1,
                    features.content_kind,
                    features.style_eligible as i64,
                    STYLE_POLICY_VERSION,
                    features.features_json,
                ])
            })?;
            if features.style_eligible {
                profile_accumulator.add(&message, &features);
            }
        }
        count += 1;
    }
    tx.execute(
        "DELETE FROM response_time_stats WHERE chat = ?1 AND source = ?2 AND user_name = ?3",
        params![chat, source, STYLE_USER],
    )?;
    if let Some(profile) = profile_accumulator.finish(chat, &source, STYLE_USER) {
        tx.execute(
            "INSERT INTO owner_style_profile(
                chat, source, user_name, sample_count, average_character_length,
                median_character_length, p90_character_length, casual_ending_count,
                casual_ending_counts_json, question_count, emoji_count, punctuation_count,
                common_endings_json, common_tokens_json, policy_version, created_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                profile.chat,
                profile.source,
                profile.user,
                profile.sample_count as i64,
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
                profile.policy_version,
                Utc::now().to_rfc3339(),
            ],
        )?;
    }
    if let Some(stats) = summarize_response_delays(chat, &source, STYLE_USER, response_delays) {
        tx.execute(
            "INSERT INTO response_time_stats(chat, source, user_name, sample_count, average_seconds, median_seconds, p90_seconds, min_seconds, max_seconds, max_window_seconds, stddev_seconds, distribution_schema_version, distribution_json) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                stats.chat,
                stats.source,
                stats.user,
                stats.sample_count as i64,
                stats.average_seconds,
                stats.median_seconds,
                stats.p90_seconds,
                stats.min_seconds,
                stats.max_seconds,
                stats.max_window_seconds,
                stats.stddev_seconds,
                stats
                    .distribution
                    .as_ref()
                    .map(|distribution| distribution.schema_version as i64)
                    .unwrap_or(0),
                stats
                    .distribution
                    .as_ref()
                    .map(serde_json::to_string)
                    .transpose()?
                    .unwrap_or_else(|| "{}".to_string()),
            ],
        )?;
    }
    tx.commit()?;
    rebuild_retrieval_index(&conn)?;
    checkpoint_context_wal_truncate(&conn)?;
    set_context_db_permissions(db_path)?;
    Ok(count)
}
