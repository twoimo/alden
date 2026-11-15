use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::context_sync_replica;
use crate::local_db;

pub fn cmd_context_sync_local(
    chat_id: i64,
    chat: String,
    db: Option<String>,
    queue: Option<String>,
    interest_only: bool,
    json: bool,
) -> Result<()> {
    if chat.trim().is_empty() || chat.len() > 512 || chat.chars().any(char::is_control) {
        anyhow::bail!("context sync chat name is invalid");
    }
    let db_path = db
        .map(std::path::PathBuf::from)
        .unwrap_or_else(openkakao_cli::context::default_db_path);
    openkakao_cli::context::ensure_live_context_schema(&db_path)?;
    let confirmed_sends = match queue.as_deref() {
        Some(path) => {
            openkakao_cli::context::confirmed_self_sends_from_queue(std::path::Path::new(path))?
        }
        None => Vec::new(),
    };
    let source = context_sync_replica::ContextSyncReplicaSource::discover()?;
    let account_fingerprint = source.account_fingerprint().to_owned();
    let account_user_id = source.account_user_id();
    let state =
        openkakao_cli::context::live_context_sync_state(&db_path, &account_fingerprint, chat_id)?;
    if let Some(state) = state.as_ref() {
        let placeholder = format!("그룹:{chat_id}");
        // 자리표시자로 저장된 이름 없는 그룹방은 화면 이름으로 동기화해도
        // 같은 방이다. 다른 방이면 그대로 거부한다.
        if (state.chat != chat && state.chat != placeholder) || state.chat_id != chat_id {
            anyhow::bail!("context sync source identity changed");
        }
    }
    let mut checkpoint = state
        .as_ref()
        .map(|state| state.checkpoint_log_id)
        .unwrap_or(0);
    let mut totals = serde_json::Map::new();
    for key in [
        "inserted_events",
        "duplicate_events",
        "indexed_messages",
        "style_messages",
        "response_samples",
        "recipient_style_samples",
    ] {
        totals.insert(key.to_string(), serde_json::Value::from(0_u64));
    }
    let mut pages = 0_u64;
    let mut deferred = None;
    let authoritative;
    loop {
        let envelope = context_sync_local_poll_page_with_bounded_retry(
            chat_id,
            &chat,
            checkpoint,
            |expected_chat_id, expected_checkpoint| {
                let replica = source.open_fresh()?;
                replica.reader().poll_after(
                    expected_chat_id,
                    local_db::LOCAL_POLL_MAX_ROWS,
                    Some(expected_checkpoint),
                )
            },
            thread::sleep,
        )?;

        let outgoing = envelope
            .messages
            .iter()
            .filter(|message| message.author_id == account_user_id)
            .map(|message| openkakao_cli::context::OutgoingSelfEvent {
                chat_id: message.chat_id,
                log_id: message.log_id,
                message: message.message.trim().to_string(),
                sent_at: message.sent_at,
            })
            .collect::<Vec<_>>();
        let classifications = openkakao_cli::context::classify_auto_generated_self_events(
            &db_path,
            &chat,
            &outgoing,
            &confirmed_sends,
        )?;
        let classifications = classifications
            .into_iter()
            .map(|item| (item.log_id, item))
            .collect::<std::collections::BTreeMap<_, _>>();
        let now = chrono::Utc::now().timestamp();
        let deferred_event = envelope
            .messages
            .iter()
            .enumerate()
            .find_map(|(index, message)| {
                if message.author_id != account_user_id {
                    return None;
                }
                self_classification_retry_after(
                    message.sent_at,
                    now,
                    classifications.get(&message.log_id),
                )
                .map(|retry_after_seconds| (index, retry_after_seconds))
            });
        let ingest_count = deferred_event
            .map(|(index, _)| index)
            .unwrap_or(envelope.messages.len());
        let events = envelope
            .messages
            .iter()
            .take(ingest_count)
            .map(|message| openkakao_cli::context::LiveContextEvent {
                chat_id: message.chat_id,
                log_id: message.log_id,
                sender_name: message.sender_name.trim().to_string(),
                message: message.message.trim().to_string(),
                sent_at: message.sent_at,
                is_self: local_db::is_self_author(message.author_id, account_user_id),
                exclude_from_learning: classifications.get(&message.log_id).is_some_and(|item| {
                    !item.auto_generated && item.reason != "no_exact_sent_match"
                }),
                auto_generated: classifications
                    .get(&message.log_id)
                    .is_some_and(|item| item.auto_generated),
                attachment: message.attachment.clone(),
                message_type: message.message_type,
                interest_only,
            })
            .collect::<Vec<_>>();
        let complete = deferred_event.is_none()
            && !envelope.completeness.has_more
            && matches!(envelope.completeness.status.as_str(), "complete" | "empty");
        if !complete && events.is_empty() && deferred_event.is_none() {
            anyhow::bail!("reconcile_required");
        }
        let result = openkakao_cli::context::ingest_live_context_events(
            &db_path,
            &account_fingerprint,
            chat_id,
            &chat,
            checkpoint,
            &events,
            complete,
            complete && !interest_only,
        )?;
        pages += 1;
        for (key, value) in [
            ("inserted_events", result.inserted_events),
            ("duplicate_events", result.duplicate_events),
            ("indexed_messages", result.indexed_messages),
            ("style_messages", result.style_messages),
            ("response_samples", result.response_samples),
            ("recipient_style_samples", result.recipient_style_samples),
        ] {
            let current = totals
                .get(key)
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0);
            totals.insert(
                key.to_string(),
                serde_json::Value::from(current + value as u64),
            );
        }
        checkpoint = result.checkpoint_log_id;
        if let Some((index, retry_after_seconds)) = deferred_event {
            deferred = Some(serde_json::json!({
                "reason": "fresh_unmatched_self",
                "log_id": envelope.messages[index].log_id,
                "retry_after_seconds": retry_after_seconds,
            }));
            authoritative = result.authoritative;
            break;
        }
        if complete {
            authoritative = result.authoritative;
            break;
        }
    }
    let output = serde_json::json!({
        "schema_version": 1,
        "action": "context_sync_local",
        "chat_id": chat_id,
        "chat": chat,
        "checkpoint_log_id": checkpoint,
        "pages": pages,
        "authoritative": authoritative,
        "deferred": deferred,
        "totals": totals,
        "network": false,
    });
    if json {
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Synchronized {} local pages for '{}' through log {} (authoritative={}).",
            pages, chat, checkpoint, authoritative
        );
    }
    Ok(())
}

const SELF_CLASSIFICATION_GRACE_SECONDS: i64 = 180;

const SELF_CLASSIFICATION_RETRY_MAX_SECONDS: i64 = 5;

// KakaoTalk can publish NTChatRoom.lastLogId just before the matching
// NTChatMessage row is visible to a new read-only SQLite snapshot.  Keep this
// retry window deliberately short and fixed: context sync owns no delivery
// capability, and it must not advance its durable checkpoint from a gap page.
const CONTEXT_SYNC_LOCAL_POLL_RETRY_DELAYS_MS: [u64; 3] = [250, 500, 1_000];

fn local_message_snapshot_eq(
    left: &local_db::LocalMessage,
    right: &local_db::LocalMessage,
) -> bool {
    left.log_id == right.log_id
        && left.chat_id == right.chat_id
        && left.author_id == right.author_id
        && left.is_self == right.is_self
        && left.sender_name == right.sender_name
        && left.message == right.message
        && left.attachment == right.attachment
        && left.message_type == right.message_type
        && left.sent_at == right.sent_at
}

/// Validate one context-sync page and return whether it is the one narrowly
/// retriable SQLite snapshot race.  Any other malformed or incomplete proof
/// remains fail-closed.
fn validate_context_sync_local_poll_page(
    envelope: &local_db::LocalPollEnvelope,
    expected_chat_id: i64,
    expected_chat_name: &str,
    expected_checkpoint: i64,
) -> Result<bool> {
    let completeness = &envelope.completeness;
    if expected_chat_id <= 0
        || expected_checkpoint < 0
        || envelope.schema_version != local_db::LOCAL_POLL_SCHEMA_VERSION
        || envelope.chat.chat_id != expected_chat_id
        || completeness.after_log_id != expected_checkpoint
        || envelope.chat.last_log_id != completeness.chat_last_log_id
        || completeness.chat_last_log_id < 0
        || completeness.id_domain != "global_sparse"
        || completeness.returned_count != envelope.messages.len() as i64
        || completeness.row_count < completeness.returned_count
    {
        anyhow::bail!("reconcile_required");
    }
    let database_name = envelope
        .chat
        .database_chat_name
        .as_deref()
        .unwrap_or(&envelope.chat.chat_name)
        .trim();
    if !database_name.is_empty() && database_name != expected_chat_name {
        anyhow::bail!("context sync chat name no longer matches the local database");
    }

    if envelope
        .messages
        .iter()
        .enumerate()
        .any(|(index, message)| {
            message.chat_id != expected_chat_id
                || message.log_id <= expected_checkpoint
                || message.log_id > completeness.chat_last_log_id
                || index
                    .checked_sub(1)
                    .is_some_and(|previous| envelope.messages[previous].log_id >= message.log_id)
        })
    {
        anyhow::bail!("reconcile_required");
    }
    let first_log_id = envelope.messages.first().map(|message| message.log_id);
    let last_log_id = envelope.messages.last().map(|message| message.log_id);
    if completeness.first_log_id != first_log_id || completeness.last_log_id != last_log_id {
        anyhow::bail!("reconcile_required");
    }

    if completeness.has_gap {
        let visible_tail = last_log_id.unwrap_or(expected_checkpoint);
        let retriable = completeness.status == "gap"
            && completeness.proof == "reconcile_required"
            && !completeness.has_more
            && completeness.row_count == completeness.returned_count
            && completeness.available_max_log_id == last_log_id
            && completeness.chat_last_log_id > visible_tail;
        if !retriable {
            anyhow::bail!("reconcile_required");
        }
        return Ok(true);
    }

    if completeness.proof != "sqlite_snapshot_rowset" {
        anyhow::bail!("reconcile_required");
    }
    let valid = match completeness.status.as_str() {
        "partial" => {
            completeness.has_more
                && !envelope.messages.is_empty()
                && completeness.row_count > completeness.returned_count
                && completeness
                    .available_max_log_id
                    .zip(last_log_id)
                    .is_some_and(|(available, returned)| available > returned)
        }
        "complete" => {
            !completeness.has_more
                && !envelope.messages.is_empty()
                && completeness.row_count == completeness.returned_count
                && completeness.available_max_log_id == last_log_id
                && completeness.chat_last_log_id == last_log_id.unwrap_or_default()
        }
        "empty" => {
            !completeness.has_more
                && envelope.messages.is_empty()
                && completeness.row_count == 0
                && completeness.available_max_log_id.is_none()
                && completeness.chat_last_log_id == expected_checkpoint
        }
        _ => false,
    };
    if !valid {
        anyhow::bail!("reconcile_required");
    }
    Ok(false)
}

/// Fetch one page without ever mutating `expected_checkpoint` between retry
/// attempts.  A retry snapshot must extend the previous one monotonically and
/// keep the exact numeric chat/checkpoint identity.
fn context_sync_local_poll_page_with_bounded_retry<P, S>(
    expected_chat_id: i64,
    expected_chat_name: &str,
    expected_checkpoint: i64,
    mut poll: P,
    mut sleep: S,
) -> Result<local_db::LocalPollEnvelope>
where
    P: FnMut(i64, i64) -> Result<local_db::LocalPollEnvelope>,
    S: FnMut(Duration),
{
    let mut previous_gap: Option<local_db::LocalPollEnvelope> = None;
    for retry_delay_ms in CONTEXT_SYNC_LOCAL_POLL_RETRY_DELAYS_MS
        .into_iter()
        .map(Some)
        .chain(std::iter::once(None))
    {
        let envelope = poll(expected_chat_id, expected_checkpoint)?;
        let retriable_gap = validate_context_sync_local_poll_page(
            &envelope,
            expected_chat_id,
            expected_chat_name,
            expected_checkpoint,
        )?;

        if let Some(previous) = previous_gap.as_ref() {
            let previous_available = previous.completeness.available_max_log_id.unwrap_or(0);
            let current_available = envelope.completeness.available_max_log_id.unwrap_or(0);
            let monotonic = envelope.completeness.chat_last_log_id
                >= previous.completeness.chat_last_log_id
                && envelope.completeness.row_count >= previous.completeness.row_count
                && current_available >= previous_available
                && envelope.messages.len() >= previous.messages.len()
                && previous
                    .messages
                    .iter()
                    .zip(&envelope.messages)
                    .all(|(left, right)| local_message_snapshot_eq(left, right));
            if !monotonic {
                anyhow::bail!("reconcile_required");
            }
        }

        if !retriable_gap {
            return Ok(envelope);
        }
        let retry_delay_ms = retry_delay_ms.context("context_sync_snapshot_retry_exhausted")?;
        previous_gap = Some(envelope);
        sleep(Duration::from_millis(retry_delay_ms));
    }
    unreachable!("bounded context-sync retry loop must return or fail")
}

/// Return a short caller-managed retry delay while a fresh outgoing row may
/// still be racing the durable `reply_decisions(status='sent')` projection.
/// The sync command must never sleep here: its caller owns liveness and the
/// unclassified row must remain strictly beyond the committed checkpoint.
fn self_classification_retry_after(
    sent_at: i64,
    now: i64,
    classification: Option<&openkakao_cli::context::AutoGeneratedSelfEventClassification>,
) -> Option<i64> {
    let classification = classification?;
    if classification.auto_generated || classification.reason != "no_exact_sent_match" {
        return None;
    }
    let remaining = sent_at
        .saturating_add(SELF_CLASSIFICATION_GRACE_SECONDS)
        .saturating_sub(now);
    (remaining > 0).then(|| remaining.clamp(1, SELF_CLASSIFICATION_RETRY_MAX_SECONDS))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn local_source_fence(
        chat_id: i64,
        after_log_id: i64,
        chat_last_log_id: i64,
        newer_log_ids: &[i64],
    ) -> local_db::LocalPollEnvelope {
        let messages = newer_log_ids
            .iter()
            .map(|log_id| local_db::LocalMessage {
                log_id: *log_id,
                chat_id,
                author_id: 7,
                is_self: false,
                sender_name: String::new(),
                message: String::new(),
                attachment: String::new(),
                message_type: 1,
                sent_at: 0,
            })
            .collect::<Vec<_>>();
        let first_log_id = messages.first().map(|message| message.log_id);
        let last_log_id = messages.last().map(|message| message.log_id);
        local_db::LocalPollEnvelope {
            schema_version: local_db::LOCAL_POLL_SCHEMA_VERSION,
            chat: local_db::LocalChat {
                chat_id,
                chat_type: 0,
                chat_name: "부자멘토멘티".to_string(),
                database_chat_name: None,
                active_members_count: 4,
                last_log_id: chat_last_log_id,
                last_updated_at: 0,
                unread_count: 0,
                display_name: String::new(),
            },
            messages,
            completeness: local_db::LocalPollCompleteness {
                status: if newer_log_ids.is_empty() {
                    "empty".to_string()
                } else {
                    "complete".to_string()
                },
                after_log_id,
                first_log_id,
                last_log_id,
                chat_last_log_id,
                row_count: newer_log_ids.len() as i64,
                returned_count: newer_log_ids.len() as i64,
                available_max_log_id: last_log_id,
                id_domain: "global_sparse".to_string(),
                has_gap: false,
                has_more: false,
                proof: "sqlite_snapshot_rowset".to_string(),
            },
        }
    }

    fn transient_local_source_gap(
        chat_id: i64,
        after_log_id: i64,
        chat_last_log_id: i64,
        visible_log_ids: &[i64],
    ) -> local_db::LocalPollEnvelope {
        let mut envelope =
            local_source_fence(chat_id, after_log_id, chat_last_log_id, visible_log_ids);
        envelope.completeness.status = "gap".to_string();
        envelope.completeness.has_gap = true;
        envelope.completeness.proof = "reconcile_required".to_string();
        envelope
    }

    #[test]
    fn context_sync_local_retries_gap_without_mutating_checkpoint() {
        let gap = transient_local_source_gap(42, 100, 101, &[]);
        let complete = local_source_fence(42, 100, 101, &[101]);
        let mut responses = std::collections::VecDeque::from([gap, complete]);
        let mut calls = Vec::new();
        let mut sleeps = Vec::new();

        let envelope = context_sync_local_poll_page_with_bounded_retry(
            42,
            "부자멘토멘티",
            100,
            |chat_id, checkpoint| {
                calls.push((chat_id, checkpoint));
                Ok(responses.pop_front().expect("bounded fake poll response"))
            },
            |delay| sleeps.push(delay.as_millis()),
        )
        .expect("a monotonic complete snapshot should recover the transient gap");

        assert_eq!(calls, [(42, 100), (42, 100)]);
        assert_eq!(sleeps, [250]);
        assert_eq!(envelope.completeness.status, "complete");
        assert_eq!(envelope.completeness.after_log_id, 100);
        assert_eq!(envelope.completeness.last_log_id, Some(101));
    }

    #[test]
    fn context_sync_local_fails_closed_on_persistent_gap() {
        let gap = transient_local_source_gap(42, 100, 101, &[]);
        let mut responses =
            std::collections::VecDeque::from([gap.clone(), gap.clone(), gap.clone(), gap]);
        let mut calls = Vec::new();
        let mut sleeps = Vec::new();

        let error = context_sync_local_poll_page_with_bounded_retry(
            42,
            "부자멘토멘티",
            100,
            |chat_id, checkpoint| {
                calls.push((chat_id, checkpoint));
                Ok(responses.pop_front().expect("bounded fake poll response"))
            },
            |delay| sleeps.push(delay.as_millis()),
        )
        .expect_err("a persistent source gap must remain fail-closed");

        assert_eq!(error.to_string(), "context_sync_snapshot_retry_exhausted");
        assert_eq!(calls, [(42, 100); 4]);
        assert_eq!(sleeps, [250, 500, 1_000]);
    }

    #[test]
    fn context_sync_local_retry_rejects_chat_or_checkpoint_identity_drift() {
        let cases = [("chat", local_source_fence(43, 100, 101, &[101])), {
            let mut checkpoint_drift = local_source_fence(42, 100, 101, &[101]);
            checkpoint_drift.completeness.after_log_id = 99;
            ("checkpoint", checkpoint_drift)
        }];

        for (label, drifted) in cases {
            let gap = transient_local_source_gap(42, 100, 101, &[]);
            let mut responses = std::collections::VecDeque::from([gap, drifted]);
            let mut calls = Vec::new();
            let mut sleeps = Vec::new();
            let error = context_sync_local_poll_page_with_bounded_retry(
                42,
                "부자멘토멘티",
                100,
                |chat_id, checkpoint| {
                    calls.push((chat_id, checkpoint));
                    Ok(responses.pop_front().expect("bounded fake poll response"))
                },
                |delay| sleeps.push(delay.as_millis()),
            )
            .expect_err("poll identity drift must terminate the retry");
            assert_eq!(error.to_string(), "reconcile_required", "{label}");
            assert_eq!(calls, [(42, 100), (42, 100)], "{label}");
            assert_eq!(sleeps, [250], "{label}");
        }
    }

    #[test]
    fn context_sync_local_retry_rejects_invalid_or_non_monotonic_snapshots() {
        let mut invalid_schema = transient_local_source_gap(42, 100, 101, &[]);
        invalid_schema.schema_version += 1;
        let mut invalid_gap = transient_local_source_gap(42, 100, 101, &[]);
        invalid_gap.completeness.status = "unknown".to_string();
        for invalid in [invalid_schema, invalid_gap] {
            let mut sleeps = Vec::new();
            let error = context_sync_local_poll_page_with_bounded_retry(
                42,
                "부자멘토멘티",
                100,
                |_chat_id, _checkpoint| Ok(invalid.clone()),
                |delay| sleeps.push(delay.as_millis()),
            )
            .expect_err("an invalid first snapshot must not enter the retry path");
            assert_eq!(error.to_string(), "reconcile_required");
            assert!(sleeps.is_empty());
        }

        let first = transient_local_source_gap(42, 100, 103, &[101]);
        let regressed = transient_local_source_gap(42, 100, 103, &[]);
        let mut responses = std::collections::VecDeque::from([first, regressed]);
        let mut sleeps = Vec::new();
        let error = context_sync_local_poll_page_with_bounded_retry(
            42,
            "부자멘토멘티",
            100,
            |_chat_id, _checkpoint| Ok(responses.pop_front().expect("bounded fake poll response")),
            |delay| sleeps.push(delay.as_millis()),
        )
        .expect_err("a retry snapshot may not regress its visible row prefix");
        assert_eq!(error.to_string(), "reconcile_required");
        assert_eq!(sleeps, [250]);
    }

    #[test]
    fn fresh_unmatched_self_classification_defers_without_blocking() {
        let unmatched = openkakao_cli::context::AutoGeneratedSelfEventClassification {
            chat_id: 7,
            log_id: 11,
            auto_generated: false,
            matched_event_id: None,
            reason: "no_exact_sent_match".to_string(),
        };
        assert_eq!(
            self_classification_retry_after(1_000, 1_001, Some(&unmatched)),
            Some(SELF_CLASSIFICATION_RETRY_MAX_SECONDS)
        );
        assert_eq!(
            self_classification_retry_after(1_000, 1_179, Some(&unmatched)),
            Some(1)
        );
        assert_eq!(
            self_classification_retry_after(1_000, 1_180, Some(&unmatched)),
            None
        );

        let mut matched = unmatched.clone();
        matched.auto_generated = true;
        matched.reason = "unique_exact_sent_match".to_string();
        assert_eq!(
            self_classification_retry_after(1_000, 1_001, Some(&matched)),
            None
        );

        let mut ambiguous = unmatched;
        ambiguous.reason = "multiple_sent_matches".to_string();
        assert_eq!(
            self_classification_retry_after(1_000, 1_001, Some(&ambiguous)),
            None
        );
    }
}
