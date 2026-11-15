//! Reply decision records (`reply_decisions` rows), their monotonic status
//! order, and similarity search over prior decisions.

use super::embedding::{bytes_to_vector, cosine, encode_vector, vector_to_bytes, VECTOR_DIM};
use super::response_time::{parse_chat_date, MAX_RESPONSE_DELAY_SECONDS};
use super::schema::ensure_retrieval_index_current;
use super::search::{
    CONTEXT_KEYWORD_CANDIDATE_CAP, MAX_CONTEXT_RETRIEVAL_SCORE, STYLE_VECTOR_CANDIDATE_CAP,
};
use super::store::{open_db, open_db_readonly};
use super::style::STYLE_POLICY_VERSION;
use anyhow::{Context, Result};
use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::BTreeSet;
use std::path::Path;

pub(super) const REPLY_DECISION_CANDIDATE_CAP: usize = 128;

const MAX_REPLY_EVIDENCE_IDS: usize = 64;

const MAX_REPLY_EVIDENCE_ID_BYTES: usize = 256;

#[derive(Debug, Clone, Deserialize)]
struct ReplyDecisionInput {
    event_id: String,
    chat: String,
    author: String,
    received_at: String,
    message: String,
    decision: String,
    reason: String,
    category: String,
    context_match_count: usize,
    style_match_count: usize,
    best_context_score: f32,
    best_style_score: f32,
    prior_similarity: f32,
    scheduled_delay_seconds: f64,
    status: String,
    reply: Option<String>,
    #[serde(default)]
    evidence_ids: Vec<String>,
    #[serde(default)]
    style_policy_version: String,
    #[serde(default)]
    provenance: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ReplyDecisionMatch {
    pub event_id: String,
    pub chat: String,
    pub author: String,
    pub received_at: String,
    pub message: String,
    pub decision: String,
    pub reason: String,
    pub category: String,
    pub status: String,
    pub reply: Option<String>,
    pub evidence_json: String,
    pub score: f32,
}

fn validate_reply_decision_input(input: &ReplyDecisionInput) -> Result<()> {
    if input.event_id.trim().is_empty()
        || input.chat.trim().is_empty()
        || input.message.trim().is_empty()
    {
        anyhow::bail!("reply decision event_id, chat, and message must not be empty");
    }
    if !matches!(input.decision.as_str(), "reply" | "skip") {
        anyhow::bail!("reply decision must be 'reply' or 'skip'");
    }
    if input.context_match_count > CONTEXT_KEYWORD_CANDIDATE_CAP
        || input.style_match_count > STYLE_VECTOR_CANDIDATE_CAP
    {
        anyhow::bail!("reply decision match counts exceed retrieval caps");
    }
    for (name, score, maximum) in [
        (
            "best_context_score",
            input.best_context_score,
            MAX_CONTEXT_RETRIEVAL_SCORE,
        ),
        ("best_style_score", input.best_style_score, 1.0),
        ("prior_similarity", input.prior_similarity, 1.0),
    ] {
        if !score.is_finite() || !(0.0..=maximum).contains(&score) {
            anyhow::bail!("reply decision {name} must be finite and in the range 0..={maximum}");
        }
    }
    if !input.scheduled_delay_seconds.is_finite()
        || !(0.0..=(MAX_RESPONSE_DELAY_SECONDS as f64)).contains(&input.scheduled_delay_seconds)
    {
        anyhow::bail!(
            "reply decision scheduled_delay_seconds must be finite and in the range 0..={MAX_RESPONSE_DELAY_SECONDS}"
        );
    }
    if input.evidence_ids.len() > MAX_REPLY_EVIDENCE_IDS {
        anyhow::bail!("reply decision evidence_ids exceeds {MAX_REPLY_EVIDENCE_IDS} identifiers");
    }
    if input.evidence_ids.iter().any(|id| {
        id.trim().is_empty()
            || id.len() > MAX_REPLY_EVIDENCE_ID_BYTES
            || !id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
    }) {
        anyhow::bail!("reply decision evidence_ids must be bounded opaque identifiers");
    }
    if input.decision == "reply" {
        if input.evidence_ids.is_empty() {
            anyhow::bail!("reply decision requires at least one evidence identifier");
        }
        if input.style_policy_version != STYLE_POLICY_VERSION {
            anyhow::bail!("reply decision style policy must be {STYLE_POLICY_VERSION}");
        }
        if input
            .reply
            .as_deref()
            .map(str::trim)
            .unwrap_or("")
            .is_empty()
        {
            anyhow::bail!("reply decision reply must not be empty");
        }
        crate::reply_policy::validate_auto_reply_laughter(
            input.reply.as_deref().unwrap_or_default(),
        )?;
    }
    Ok(())
}

pub fn record_reply_decision(db_path: &Path, record_json: &str) -> Result<bool> {
    let input: ReplyDecisionInput =
        serde_json::from_str(record_json).context("invalid reply decision JSON")?;
    validate_reply_decision_input(&input)?;
    let mut conn = open_db(db_path)?;
    let tx = conn.transaction()?;
    let incoming_rank = reply_status_rank(&input.status)
        .ok_or_else(|| anyhow::anyhow!("unknown reply decision status '{}'", input.status))?;
    let now = Utc::now().to_rfc3339();
    let existing_status = tx
        .query_row(
            "SELECT status FROM reply_decisions WHERE event_id=?1",
            [&input.event_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if let Some(previous_status) = existing_status {
        let previous_rank = reply_status_rank(&previous_status).unwrap_or(6);
        if !can_apply_reply_status(
            &previous_status,
            &input.status,
            previous_rank,
            incoming_rank,
        ) {
            return Ok(false);
        }
    }
    let mut evidence_map = serde_json::Map::new();
    evidence_map.insert(
        "evidence_ids".into(),
        serde_json::to_value(&input.evidence_ids)?,
    );
    evidence_map.insert(
        "style_policy_version".into(),
        serde_json::to_value(&input.style_policy_version)?,
    );
    if let Some(prov) = &input.provenance {
        evidence_map.insert("provenance".into(), prov.clone());
    }
    let evidence_json = serde_json::Value::Object(evidence_map).to_string();
    if evidence_json.len() > 16 * 1024 {
        anyhow::bail!("reply evidence exceeds 16 KiB");
    }
    tx.execute(
        "INSERT INTO reply_decisions(
            event_id, chat, author, received_at, message, vector, decision, reason,
            category, context_match_count, style_match_count, best_context_score,
            best_style_score, prior_similarity, scheduled_delay_seconds, status,
            reply, sent_at, created_at, updated_at, evidence_json
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, NULL, ?18, ?18, ?19)
        ON CONFLICT(event_id) DO UPDATE SET
            chat=excluded.chat,
            author=excluded.author,
            received_at=excluded.received_at,
            message=excluded.message,
            vector=excluded.vector,
            decision=excluded.decision,
            reason=excluded.reason,
            category=excluded.category,
            context_match_count=excluded.context_match_count,
            style_match_count=excluded.style_match_count,
            best_context_score=excluded.best_context_score,
            best_style_score=excluded.best_style_score,
            prior_similarity=excluded.prior_similarity,
            scheduled_delay_seconds=excluded.scheduled_delay_seconds,
            status=excluded.status,
            reply=CASE
                WHEN excluded.decision='skip' AND excluded.status='skipped' THEN NULL
                ELSE COALESCE(excluded.reply, reply)
            END,
            sent_at=CASE
                WHEN excluded.decision='skip' AND excluded.status='skipped' THEN NULL
                ELSE sent_at
            END,
            evidence_json=excluded.evidence_json,
            updated_at=excluded.updated_at",
        params![
            input.event_id,
            input.chat,
            input.author,
            input.received_at,
            input.message,
            vector_to_bytes(&encode_vector(&input.message)),
            input.decision,
            input.reason,
            input.category,
            input.context_match_count as i64,
            input.style_match_count as i64,
            input.best_context_score,
            input.best_style_score,
            input.prior_similarity,
            input.scheduled_delay_seconds,
            input.status,
            input.reply,
            now,
            evidence_json,
        ],
    )?;
    tx.commit()?;
    Ok(true)
}

pub fn update_reply_decision(
    db_path: &Path,
    event_id: &str,
    status: &str,
    reply: Option<&str>,
    sent_at: Option<&str>,
) -> Result<bool> {
    if event_id.trim().is_empty() || status.trim().is_empty() {
        anyhow::bail!("reply decision event_id and status must not be empty");
    }
    if let Some(reply) = reply {
        crate::reply_policy::validate_auto_reply_laughter(reply)?;
    }
    let mut conn = open_db(db_path)?;
    let tx = conn.transaction()?;
    let incoming_rank = reply_status_rank(status)
        .ok_or_else(|| anyhow::anyhow!("unknown reply decision status '{status}'"))?;
    let previous_status = tx
        .query_row(
            "SELECT status FROM reply_decisions WHERE event_id=?1",
            [event_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    let Some(previous_status) = previous_status else {
        return Ok(false);
    };
    let previous_rank = reply_status_rank(&previous_status).unwrap_or(6);
    if !can_apply_reply_status(&previous_status, status, previous_rank, incoming_rank) {
        return Ok(false);
    }
    let changed = tx.execute(
        "UPDATE reply_decisions
         SET status = ?2,
             reply = COALESCE(?3, reply),
             sent_at = COALESCE(?4, sent_at),
             updated_at = ?5
         WHERE event_id = ?1",
        params![event_id, status, reply, sent_at, Utc::now().to_rfc3339()],
    )?;
    tx.commit()?;
    Ok(changed > 0)
}

pub(super) fn parse_reply_sent_at_seconds(value: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(value.trim())
        .ok()
        .map(|value| value.timestamp())
        .or_else(|| parse_chat_date(value).map(|value| value.and_utc().timestamp()))
}

pub fn reply_decision_search(
    db_path: &Path,
    chat: &str,
    query: &str,
    limit: usize,
) -> Result<Vec<ReplyDecisionMatch>> {
    if chat.trim().is_empty() || query.trim().is_empty() || limit == 0 {
        return Ok(Vec::new());
    }
    let conn = open_db_readonly(db_path)?;
    ensure_retrieval_index_current(&conn)?;
    reply_decision_search_with_connection_excluding(&conn, chat, query, limit, &BTreeSet::new())
}

pub(super) fn reply_decision_search_with_connection_excluding(
    conn: &Connection,
    chat: &str,
    query: &str,
    limit: usize,
    excluded_event_ids: &BTreeSet<String>,
) -> Result<Vec<ReplyDecisionMatch>> {
    ensure_retrieval_index_current(conn)?;
    let result_limit = limit.min(REPLY_DECISION_CANDIDATE_CAP);
    if result_limit == 0 {
        return Ok(Vec::new());
    }
    let mut results = Vec::new();
    let mut exact_stmt = conn.prepare(
        "SELECT event_id, chat, author, received_at, message, decision, reason,
                category, status, reply, substr(evidence_json, 1, 16384)
         FROM reply_decisions
         WHERE chat = ?1 AND message = ?2
         ORDER BY created_at DESC, status ASC, event_id ASC
         LIMIT ?3",
    )?;
    let exact_candidate_limit = result_limit.saturating_add(excluded_event_ids.len());
    let exact_rows =
        exact_stmt.query_map(params![chat, query, exact_candidate_limit as i64], |row| {
            Ok(ReplyDecisionMatch {
                event_id: row.get(0)?,
                chat: row.get(1)?,
                author: row.get(2)?,
                received_at: row.get(3)?,
                message: row.get(4)?,
                decision: row.get(5)?,
                reason: row.get(6)?,
                category: row.get(7)?,
                status: row.get(8)?,
                reply: row.get(9)?,
                evidence_json: row.get(10)?,
                score: 1.0,
            })
        })?;
    for row in exact_rows {
        let row = row?;
        if !excluded_event_ids.contains(&row.event_id) {
            results.push(row);
        }
        if results.len() == result_limit {
            break;
        }
    }
    if results.len() == result_limit {
        return Ok(results);
    }

    let query_vector = encode_vector(query);
    if query_vector.iter().all(|value| *value == 0.0) {
        return Ok(results);
    }
    let mut stmt = conn.prepare(
        "SELECT event_id, chat, author, received_at, message, decision, reason,
                category, status, reply, substr(evidence_json, 1, 16384), vector
         FROM reply_decisions
         WHERE chat = ?1
         ORDER BY created_at DESC, event_id ASC
         LIMIT ?2",
    )?;
    let similarity_candidate_limit =
        REPLY_DECISION_CANDIDATE_CAP.saturating_add(excluded_event_ids.len());
    let rows = stmt.query_map(params![chat, similarity_candidate_limit as i64], |row| {
        let evidence_json: String = row.get(10)?;
        let bytes: Vec<u8> = row.get(11)?;
        if bytes.len() != VECTOR_DIM * 4 {
            return Err(rusqlite::Error::InvalidColumnType(
                11,
                "vector".into(),
                rusqlite::types::Type::Blob,
            ));
        }
        Ok((
            ReplyDecisionMatch {
                event_id: row.get(0)?,
                chat: row.get(1)?,
                author: row.get(2)?,
                received_at: row.get(3)?,
                message: row.get(4)?,
                decision: row.get(5)?,
                reason: row.get(6)?,
                category: row.get(7)?,
                status: row.get(8)?,
                reply: row.get(9)?,
                evidence_json,
                score: 0.0,
            },
            bytes_to_vector(&bytes),
        ))
    })?;
    let exact_event_ids = results
        .iter()
        .map(|result| result.event_id.as_str())
        .collect::<std::collections::BTreeSet<_>>();
    let mut similar = Vec::new();
    for row in rows {
        let (mut result, vector) = row?;
        if excluded_event_ids.contains(&result.event_id)
            || exact_event_ids.contains(result.event_id.as_str())
        {
            continue;
        }
        result.score = cosine(&query_vector, &vector);
        similar.push(result);
    }
    similar.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| a.event_id.cmp(&b.event_id))
    });
    results.extend(similar.into_iter().take(result_limit - results.len()));
    Ok(results)
}

#[derive(Debug, Clone, Serialize)]
pub struct ReplyDecision {
    pub event_id: String,
    pub status: String,
    pub evidence_json: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ReplyDecisionUpdate {
    pub applied: bool,
    pub updated: bool,
    pub decision: Option<ReplyDecision>,
    pub reason: Option<String>,
}

fn reply_status_rank(status: &str) -> Option<u8> {
    Some(match status {
        "pending" => 0,
        "processing" => 1,
        "projection_pending" => 2,
        "scheduled" => 3,
        "sending" => 4,
        "accepted_unconfirmed" => 5,
        "sent" | "skipped" | "poison" => 6,
        "failed" => 4,
        "delivery_unknown" | "reconcile_required" => 6,
        _ => return None,
    })
}

/// Apply a monotonic audit projection. The queue remains the delivery authority.
pub fn project_reply_decision(
    db_path: &Path,
    event_id: &str,
    status: &str,
    evidence_json: &str,
) -> Result<ReplyDecisionUpdate> {
    if event_id.trim().is_empty() {
        anyhow::bail!("reply event identity must not be empty");
    }
    if evidence_json.len() > 16 * 1024 {
        anyhow::bail!("reply evidence exceeds 16 KiB");
    }
    let incoming_rank = reply_status_rank(status)
        .ok_or_else(|| anyhow::anyhow!("unknown reply decision status '{status}'"))?;
    let mut conn = open_db(db_path)?;
    let tx = conn.transaction()?;
    let now = chrono::Utc::now().to_rfc3339();
    let existing = tx
        .query_row(
            "SELECT status,evidence_json,updated_at FROM reply_decisions WHERE event_id=?1",
            [event_id],
            |row| {
                Ok(ReplyDecision {
                    event_id: event_id.to_string(),
                    status: row.get(0)?,
                    evidence_json: row.get(1)?,
                    updated_at: row.get(2)?,
                })
            },
        )
        .optional()?;
    if let Some(previous) = existing {
        let previous_rank = reply_status_rank(&previous.status).unwrap_or(6);
        if !can_apply_reply_status(&previous.status, status, previous_rank, incoming_rank) {
            return Ok(ReplyDecisionUpdate {
                applied: false,
                updated: false,
                decision: Some(previous),
                reason: Some("terminal_or_newer_status_cannot_regress".into()),
            });
        }
        if previous.status == status && previous.evidence_json == evidence_json {
            return Ok(ReplyDecisionUpdate {
                applied: true,
                updated: false,
                decision: Some(previous),
                reason: Some("already_current".into()),
            });
        }
        tx.execute(
            "UPDATE reply_decisions
             SET status=?2,evidence_json=?3,updated_at=?4
             WHERE event_id=?1",
            params![event_id, status, evidence_json, now],
        )?;
    } else {
        tx.execute(
            "INSERT INTO reply_decisions(
                event_id, chat, author, received_at, message, vector, decision,
                reason, category, context_match_count, style_match_count,
                best_context_score, best_style_score, prior_similarity,
                scheduled_delay_seconds, status, reply, sent_at, created_at,
                updated_at, evidence_json
             ) VALUES(
                ?1, '__projection__', '', ?2, ?1, ?3, 'skip', 'projection',
                'uncertain', 0, 0, 0, 0, 0, 0, ?4, NULL, NULL, ?2, ?2, ?5
             )",
            params![
                event_id,
                now,
                vector_to_bytes(&encode_vector(event_id)),
                status,
                evidence_json,
            ],
        )?;
    }
    tx.commit()?;
    Ok(ReplyDecisionUpdate {
        applied: true,
        updated: true,
        decision: Some(ReplyDecision {
            event_id: event_id.to_string(),
            status: status.to_string(),
            evidence_json: evidence_json.to_string(),
            updated_at: now,
        }),
        reason: None,
    })
}

fn can_apply_reply_status(
    previous: &str,
    incoming: &str,
    previous_rank: u8,
    incoming_rank: u8,
) -> bool {
    if incoming_rank < previous_rank {
        return false;
    }
    if previous == incoming {
        return true;
    }
    if is_terminal_reply_status(previous) {
        return false;
    }
    true
}

fn is_terminal_reply_status(status: &str) -> bool {
    matches!(
        status,
        "sent" | "skipped" | "poison" | "delivery_unknown" | "reconcile_required"
    )
}

pub fn get_reply_decision(db_path: &Path, event_id: &str) -> Result<Option<ReplyDecision>> {
    let conn = open_db_readonly(db_path)?;
    ensure_retrieval_index_current(&conn)?;
    conn.query_row(
        "SELECT event_id,status,evidence_json,updated_at FROM reply_decisions WHERE event_id=?1",
        [event_id],
        |row| {
            Ok(ReplyDecision {
                event_id: row.get(0)?,
                status: row.get(1)?,
                evidence_json: row.get(2)?,
                updated_at: row.get(3)?,
            })
        },
    )
    .optional()
    .map_err(Into::into)
}
