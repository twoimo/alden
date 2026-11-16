//! Offline `context-*` commands over the local context index: indexing,
//! search, reply bundles, style and response-time profiles, and the
//! reply-decision ledger.

use anyhow::Result;

use crate::util;

/// The context index named by `--db`, or the default index when it is omitted.
pub fn context_db_path(db: Option<String>) -> std::path::PathBuf {
    db.map(std::path::PathBuf::from)
        .unwrap_or_else(openkakao_cli::context::default_db_path)
}

pub fn cmd_context_index(
    input: String,
    chat: String,
    db: Option<String>,
    queue: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let queue_path = queue.as_deref().map(std::path::Path::new);
    let count = openkakao_cli::context::index_csv_with_queue(
        &db_path,
        &chat,
        std::path::Path::new(&input),
        queue_path,
    )?;
    if json {
        println!(
            "{}",
            serde_json::json!({
                "action": "index",
                "chat": chat,
                "input": openkakao_cli::context::provenance_id(&input),
                "db": openkakao_cli::context::provenance_id(
                    &db_path.display().to_string()
                ),
                "messages": count,
                "network": false
            })
        );
    } else {
        println!(
            "Indexed {} messages for '{}' into {} (offline).",
            count,
            chat,
            db_path.display()
        );
    }
    Ok(())
}

pub fn cmd_context_repair_self_sends(
    chat_id: i64,
    chat: String,
    db: Option<String>,
    queue: Option<String>,
    dry_run: bool,
    json: bool,
) -> Result<()> {
    if chat.trim().is_empty() || chat.len() > 512 || chat.chars().any(char::is_control) {
        anyhow::bail!("style repair chat name is invalid");
    }
    let db_path = context_db_path(db);
    openkakao_cli::context::ensure_live_context_schema(&db_path)?;
    let confirmed_sends = match queue.as_deref() {
        Some(path) => {
            openkakao_cli::context::confirmed_self_sends_from_queue(std::path::Path::new(path))?
        }
        None => Vec::new(),
    };
    let report = openkakao_cli::context::repair_bot_sent_style_samples(
        &db_path,
        &chat,
        chat_id,
        &confirmed_sends,
        dry_run,
    )?;
    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        println!(
                    "{}: {} matched of {} scanned rows ({} already ineligible, {} ambiguous), {} response samples and {} recipient samples dropped, {} sources rebuilt (dry_run={}).",
                    chat,
                    report.matched_rows,
                    report.scanned_rows,
                    report.already_ineligible,
                    report.ambiguous_rows,
                    report.deleted_response_samples,
                    report.deleted_recipient_samples,
                    report.rebuilt_sources.len(),
                    report.dry_run,
                );
        for matched in &report.matches {
            println!(
                "  [{}|{}s] {} <- {}",
                matched.log_id,
                matched.offset_seconds,
                matched.message.chars().take(60).collect::<String>(),
                matched.matched_event_id,
            );
        }
    }
    Ok(())
}

pub fn cmd_context_search(
    query: String,
    chat: Option<String>,
    mode: String,
    limit: usize,
    source: Option<String>,
    db: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let mut results = openkakao_cli::context::search(
        &db_path,
        chat.as_deref(),
        source.as_deref(),
        &query,
        &mode,
        limit,
    )?;
    if json {
        openkakao_cli::context::redact_context_results(&mut results);
        println!("{}", serde_json::to_string_pretty(&results)?);
    } else {
        for result in &results {
            println!(
                "[{:.3}] {} [{}] {}: {}",
                result.score,
                result.chat,
                result.date,
                result.user,
                util::truncate(&result.message, 160)
            );
        }
        println!("{} results (offline {} search)", results.len(), mode);
    }
    Ok(())
}

#[allow(
    clippy::too_many_arguments,
    reason = "context-reply-bundle CLI flags map one-to-one onto the parameters"
)]
pub fn cmd_context_reply_bundle(
    query: String,
    chat: String,
    chat_id: Option<i64>,
    current_log_id: Option<i64>,
    exclude_log_id: Vec<i64>,
    recipient: Option<String>,
    source: Option<String>,
    db: Option<String>,
) -> Result<()> {
    let db_path = context_db_path(db);
    let output = match (recipient.as_deref(), chat_id, current_log_id) {
                (Some(recipient), Some(chat_id), Some(current_log_id)) => {
                    let mut excluded_log_ids = Vec::with_capacity(1 + exclude_log_id.len());
                    excluded_log_ids.push(current_log_id);
                    excluded_log_ids.extend(exclude_log_id);
                    openkakao_cli::context::context_reply_bundle_for_recipient_excluding_live_events_json(
                        &db_path,
                        &chat,
                        &query,
                        source.as_deref(),
                        recipient,
                        chat_id,
                        &excluded_log_ids,
                    )?
                }
                (None, None, None) if exclude_log_id.is_empty() => openkakao_cli::context::context_reply_bundle_json(
                    &db_path,
                    &chat,
                    &query,
                    source.as_deref(),
                )?,
                _ => anyhow::bail!(
                    "recipient, chat-id, and current-log-id must be supplied together; exclude-log-id requires that recipient bundle mode"
                ),
            };
    println!("{output}");
    Ok(())
}

pub fn cmd_context_style_search(
    query: String,
    limit: usize,
    db: Option<String>,
    chat: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let mut results =
        openkakao_cli::context::style_search(&db_path, chat.as_deref(), &query, limit)?;
    if json {
        openkakao_cli::context::redact_context_results(&mut results);
        println!("{}", serde_json::to_string_pretty(&results)?);
    } else {
        for result in &results {
            println!(
                "[{:.3}] {} [{}] {}: {}",
                result.score,
                result.chat,
                result.date,
                result.user,
                util::truncate(&result.message, 160)
            );
        }
        println!(
            "{} results (offline 최연우 style vector search)",
            results.len()
        );
    }
    Ok(())
}

pub fn cmd_context_recipient_style(
    chat: String,
    recipient: String,
    source: Option<String>,
    db: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let profile = openkakao_cli::context::recipient_style_profile(
        &db_path,
        &chat,
        &recipient,
        source.as_deref(),
    )?;
    if json {
        match openkakao_cli::context::recipient_style_profile_json(
            &db_path,
            &chat,
            &recipient,
            source.as_deref(),
        )? {
            Some(payload) => println!("{payload}"),
            None => println!("null"),
        }
    } else if let Some(profile) = profile {
        println!(
            "{} -> {}: {} direct samples (fallback={}, avg {:.1} chars)",
            profile.profile.user,
            profile.recipient,
            profile.direct_sample_count,
            profile.used_fallback,
            profile.profile.average_character_length
        );
    } else {
        println!(
            "No recipient style profile for '{}' in '{}'.",
            recipient, chat
        );
    }
    Ok(())
}

pub fn cmd_context_response_time(
    chat: String,
    user: String,
    source: Option<String>,
    db: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let stats =
        openkakao_cli::context::response_time_stats(&db_path, &chat, &user, source.as_deref())?;
    if json {
        let mut redacted_stats = stats;
        openkakao_cli::context::redact_response_time_stats(&mut redacted_stats);
        println!("{}", serde_json::to_string_pretty(&redacted_stats)?);
    } else if let Some(stats) = stats {
        println!(
            "{} average response: {:.1}s (median {:.1}s, p90 {:.1}s, {} samples)",
            stats.user,
            stats.average_seconds,
            stats.median_seconds,
            stats.p90_seconds,
            stats.sample_count
        );
    } else {
        println!("No response-time samples for '{}' in '{}'.", user, chat);
    }
    Ok(())
}

pub fn cmd_context_reply_search(
    query: String,
    chat: String,
    limit: usize,
    db: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let mut results =
        openkakao_cli::context::reply_decision_search(&db_path, &chat, &query, limit)?;
    if json {
        openkakao_cli::context::redact_reply_decisions(&mut results);
        println!("{}", serde_json::to_string_pretty(&results)?);
    } else {
        for result in &results {
            println!(
                "[{:.3}] {} {} [{}] {}: {}",
                result.score,
                result.decision,
                result.status,
                result.category,
                result.author,
                util::truncate(&result.message, 160)
            );
        }
        println!(
            "{} results (offline reply-decision vector search)",
            results.len()
        );
    }
    Ok(())
}

pub fn cmd_context_reply_record(record: String, db: Option<String>, json: bool) -> Result<()> {
    let db_path = context_db_path(db);
    let applied = openkakao_cli::context::record_reply_decision(&db_path, &record)?;
    if json {
        println!(
            "{}",
            serde_json::json!({
                "recorded": applied,
                "applied": applied,
                "db": openkakao_cli::context::provenance_id(
                    &db_path.display().to_string()
                ),
                "network": false
            })
        );
    } else if applied {
        println!(
            "Recorded reply decision in {} (offline).",
            db_path.display()
        );
    } else {
        println!("Reply decision was stale and was not recorded (offline).");
    }
    Ok(())
}

pub fn cmd_context_reply_update(
    event_id: String,
    status: String,
    reply: Option<String>,
    sent_at: Option<String>,
    db: Option<String>,
    json: bool,
) -> Result<()> {
    let db_path = context_db_path(db);
    let updated = openkakao_cli::context::update_reply_decision(
        &db_path,
        &event_id,
        &status,
        reply.as_deref(),
        sent_at.as_deref(),
    )?;
    if json {
        println!(
            "{}",
            serde_json::json!({
                "updated": updated,
                "applied": updated,
                "event_id": event_id,
                "db": openkakao_cli::context::provenance_id(
                    &db_path.display().to_string()
                ),
                "network": false
            })
        );
    } else if updated {
        println!("Updated reply decision {} (offline).", event_id);
    } else {
        println!("Reply decision {} was not found.", event_id);
    }
    Ok(())
}
