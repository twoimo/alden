//! Context retrieval: keyword, vector, topic and hybrid candidates
//! (`ContextCandidate`) over context rows and owner style rows.

use super::embedding::{
    bytes_to_vector, cosine, encode_vector, Embedder, LocalHashEmbedder, VECTOR_DIM,
};
use super::live::preferred_context_source;
use super::schema::ensure_retrieval_index_current;
use super::store::open_db_readonly;
use super::style::{STYLE_POLICY_VERSION, STYLE_USER};
use super::topics::{classify_message_topics, context_topic_tables_ready};
use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use serde::Serialize;
use std::cmp::Ordering;
use std::collections::BTreeSet;
use std::path::Path;

pub(super) const CONTEXT_KEYWORD_CANDIDATE_CAP: usize = 256;

pub(super) const CONTEXT_VECTOR_CANDIDATE_CAP: usize = 256;

const CONTEXT_TOPIC_CANDIDATE_CAP: usize = 64;

pub(super) const STYLE_VECTOR_CANDIDATE_CAP: usize = 256;

pub(super) const MAX_CONTEXT_RETRIEVAL_SCORE: f32 = 2.0;

pub(super) const RECIPIENT_STYLE_SEARCH_SQL: &str =
    "SELECT styles.id, styles.chat, styles.source, styles.date,
            styles.user_name, styles.message, styles.vector, samples.confidence
     FROM owner_recipient_style_samples samples
     JOIN owner_style styles ON styles.id = samples.style_message_id
     WHERE samples.source = ?1
       AND samples.recipient = ?2
       AND styles.chat = ?3
       AND styles.source = samples.source
       AND styles.user_name = ?4
       AND styles.style_eligible = 1
       AND styles.policy_version = ?5
     ORDER BY samples.reply_log_id DESC, samples.style_message_id DESC
     LIMIT ?6";

#[derive(Debug, Clone, Serialize)]
pub struct ContextResult {
    pub chat: String,
    pub source: String,
    pub date: String,
    pub user: String,
    pub message: String,
    pub score: f32,
    pub mode: String,
}

#[derive(Debug, Clone)]
struct ContextCandidate {
    id: i64,
    result: ContextResult,
}

pub fn search(
    db_path: &Path,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    mode: &str,
    limit: usize,
) -> Result<Vec<ContextResult>> {
    // 기본 경로는 결정적 로컬 해시 임베더를 주입해 리팩터링 이전과 동일한
    // 검색 결과·정렬을 그대로 유지한다.
    search_with_embedder(
        db_path,
        chat,
        source,
        query,
        mode,
        limit,
        &LocalHashEmbedder,
    )
}

/// 착탈식 임베더를 주입받는 검색 진입점. 기본 `search`는 `LocalHashEmbedder`를
/// 주입해 동작 동일성을 보장한다(R11.1, R11.2).
pub fn search_with_embedder(
    db_path: &Path,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    mode: &str,
    limit: usize,
    embedder: &dyn Embedder,
) -> Result<Vec<ContextResult>> {
    if query.trim().is_empty() {
        anyhow::bail!("query must not be empty");
    }
    if limit == 0 {
        return Ok(Vec::new());
    }
    let conn = open_db_readonly(db_path)?;
    ensure_retrieval_index_current(&conn)?;
    search_with_connection_excluding(
        &conn,
        chat,
        source,
        query,
        mode,
        limit,
        &BTreeSet::new(),
        embedder,
    )
}

// Mirrors the sibling search entry points' parameter shape (scope, query,
// mode, limit, exclusion, embedder); bundling them is an API change, not a fix.
#[allow(
    clippy::too_many_arguments,
    reason = "search entry points share one signature"
)]
pub(super) fn search_with_connection_excluding(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    mode: &str,
    limit: usize,
    excluded_context_ids: &BTreeSet<i64>,
    embedder: &dyn Embedder,
) -> Result<Vec<ContextResult>> {
    ensure_retrieval_index_current(conn)?;
    let preferred_source = preferred_context_source(conn, chat, source)?;
    let candidates = match mode {
        "keyword" => keyword_search_excluding(
            conn,
            chat,
            preferred_source.as_deref(),
            query,
            excluded_context_ids,
        )?,
        "vector" => vector_search_excluding(
            conn,
            chat,
            preferred_source.as_deref(),
            query,
            excluded_context_ids,
            embedder,
        )?,
        "hybrid" => {
            return hybrid_search_excluding(
                conn,
                chat,
                preferred_source.as_deref(),
                query,
                limit,
                excluded_context_ids,
                embedder,
            )
        }
        other => {
            anyhow::bail!("unknown context search mode '{other}' (use keyword, vector, or hybrid)")
        }
    };
    Ok(candidates
        .into_iter()
        .take(limit)
        .map(|candidate| candidate.result)
        .collect())
}

pub fn style_search(
    db_path: &Path,
    chat: Option<&str>,
    query: &str,
    limit: usize,
) -> Result<Vec<ContextResult>> {
    if query.trim().is_empty() {
        anyhow::bail!("query must not be empty");
    }
    if limit == 0 {
        return Ok(Vec::new());
    }
    let conn = open_db_readonly(db_path)?;
    ensure_retrieval_index_current(&conn)?;
    style_search_with_connection(&conn, chat, None, query, limit, true)
}

pub(super) fn style_search_with_connection(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    limit: usize,
    require_policy: bool,
) -> Result<Vec<ContextResult>> {
    ensure_retrieval_index_current(conn)?;
    let preferred_source = preferred_context_source(conn, chat, source)?;
    let query_vector = encode_vector(query);
    let tokenless = query_vector.iter().all(|value| *value == 0.0);
    // Style retrieval intentionally uses a bounded eligible recency/diversity
    // pool. This keeps policy filtering ahead of vector scoring without an
    // unbounded fallback when no lexical style index is available.
    let mut stmt = conn.prepare(
        "SELECT id,chat,source,date,user_name,message,vector
         FROM owner_style
         WHERE user_name = ?5
           AND style_eligible = 1
           AND (?1 IS NULL OR chat = ?1)
           AND (?2 IS NULL OR source = ?2)
           AND (?3 = 0 OR policy_version = ?4)
         ORDER BY date DESC, source_row DESC, id DESC
         LIMIT ?6",
    )?;
    let rows = stmt.query_map(
        params![
            chat,
            preferred_source.as_deref(),
            require_policy as i64,
            STYLE_POLICY_VERSION,
            STYLE_USER,
            STYLE_VECTOR_CANDIDATE_CAP as i64,
        ],
        |row| {
            let bytes: Vec<u8> = row.get(6)?;
            if bytes.len() != VECTOR_DIM * 4 {
                return Err(rusqlite::Error::InvalidColumnType(
                    6,
                    "vector".into(),
                    rusqlite::types::Type::Blob,
                ));
            }
            Ok((
                row.get::<_, i64>(0)?,
                ContextResult {
                    chat: row.get(1)?,
                    source: row.get(2)?,
                    date: row.get(3)?,
                    user: row.get(4)?,
                    message: row.get(5)?,
                    score: 0.0,
                    mode: "vector_style".into(),
                },
                bytes_to_vector(&bytes),
            ))
        },
    )?;
    let mut results = Vec::new();
    for row in rows {
        let (id, mut result, vector) = row?;
        crate::reply_policy::validate_auto_reply_laughter(&result.message)
            .context("style search contains disallowed laughter evidence")?;
        result.score = if tokenless {
            0.0
        } else {
            cosine(&query_vector, &vector)
        };
        if tokenless {
            result.mode = "recency_style".into();
        }
        results.push(ContextCandidate { id, result });
    }
    if !tokenless {
        results.sort_by(|a, b| {
            b.result
                .score
                .partial_cmp(&a.result.score)
                .unwrap_or(Ordering::Equal)
                .then_with(|| a.id.cmp(&b.id))
        });
    }
    Ok(results
        .into_iter()
        .take(limit.min(STYLE_VECTOR_CANDIDATE_CAP))
        .map(|candidate| candidate.result)
        .collect())
}

pub(super) fn recipient_style_search_with_connection(
    conn: &Connection,
    chat: &str,
    source: &str,
    recipient: &str,
    query: &str,
    limit: usize,
) -> Result<Vec<ContextResult>> {
    ensure_retrieval_index_current(conn)?;
    let query_vector = encode_vector(query);
    let tokenless = query_vector.iter().all(|value| *value == 0.0);
    let mut stmt = conn.prepare(RECIPIENT_STYLE_SEARCH_SQL)?;
    let rows = stmt.query_map(
        params![
            source,
            recipient,
            chat,
            STYLE_USER,
            STYLE_POLICY_VERSION,
            STYLE_VECTOR_CANDIDATE_CAP as i64,
        ],
        |row| {
            let bytes: Vec<u8> = row.get(6)?;
            if bytes.len() != VECTOR_DIM * 4 {
                return Err(rusqlite::Error::InvalidColumnType(
                    6,
                    "vector".into(),
                    rusqlite::types::Type::Blob,
                ));
            }
            let confidence: f32 = row.get(7)?;
            if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) || confidence == 0.0 {
                return Err(rusqlite::Error::InvalidColumnType(
                    7,
                    "confidence".into(),
                    rusqlite::types::Type::Real,
                ));
            }
            Ok((
                row.get::<_, i64>(0)?,
                ContextResult {
                    chat: row.get(1)?,
                    source: row.get(2)?,
                    date: row.get(3)?,
                    user: row.get(4)?,
                    message: row.get(5)?,
                    score: 0.0,
                    mode: "vector_style_recipient".into(),
                },
                bytes_to_vector(&bytes),
                confidence,
            ))
        },
    )?;
    let mut results = Vec::new();
    for row in rows {
        let (id, mut result, vector, confidence) = row?;
        crate::reply_policy::validate_auto_reply_laughter(&result.message)
            .context("recipient style search contains disallowed laughter evidence")?;
        result.score = if tokenless {
            0.0
        } else {
            cosine(&query_vector, &vector) * confidence
        };
        if tokenless {
            result.mode = "recency_style_recipient".into();
        }
        results.push(ContextCandidate { id, result });
    }
    if !tokenless {
        results.sort_by(|left, right| {
            right
                .result
                .score
                .partial_cmp(&left.result.score)
                .unwrap_or(Ordering::Equal)
                .then_with(|| left.id.cmp(&right.id))
        });
    }
    Ok(results
        .into_iter()
        .take(limit.min(STYLE_VECTOR_CANDIDATE_CAP))
        .map(|candidate| candidate.result)
        .collect())
}

fn keyword_search_excluding(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    excluded_context_ids: &BTreeSet<i64>,
) -> Result<Vec<ContextCandidate>> {
    let match_query = query
        .split_whitespace()
        .map(|term| format!("\"{}\"", term.replace('"', "")))
        .collect::<Vec<_>>()
        .join(" OR ");
    if match_query.trim().is_empty() {
        return Ok(Vec::new());
    }
    let mut stmt = conn.prepare(
        "SELECT m.id,m.chat,m.source,m.date,m.user_name,m.message,
                bm25(context_messages_fts)
         FROM context_messages_fts f
         JOIN context_messages m ON m.id=f.rowid
         WHERE context_messages_fts MATCH ?1
           AND (?2 IS NULL OR m.chat=?2)
           AND (?3 IS NULL OR m.source=?3)
         ORDER BY bm25(context_messages_fts), m.id ASC
         LIMIT ?4",
    )?;
    let candidate_limit = CONTEXT_KEYWORD_CANDIDATE_CAP.saturating_add(excluded_context_ids.len());
    let rows = stmt.query_map(
        params![match_query, chat, source, candidate_limit as i64],
        |row| {
            Ok(ContextCandidate {
                id: row.get(0)?,
                result: ContextResult {
                    chat: row.get(1)?,
                    source: row.get(2)?,
                    date: row.get(3)?,
                    user: row.get(4)?,
                    message: row.get(5)?,
                    score: -row.get::<_, f64>(6)? as f32,
                    mode: "keyword".into(),
                },
            })
        },
    )?;
    let mut results = Vec::new();
    for row in rows {
        let row = row?;
        if !excluded_context_ids.contains(&row.id) {
            results.push(row);
        }
        if results.len() == CONTEXT_KEYWORD_CANDIDATE_CAP {
            break;
        }
    }
    Ok(results)
}

fn vector_search_excluding(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    excluded_context_ids: &BTreeSet<i64>,
    embedder: &dyn Embedder,
) -> Result<Vec<ContextCandidate>> {
    let query_vector = embedder.embed(query);
    if query_vector.iter().all(|value| *value == 0.0) {
        return Ok(Vec::new());
    }

    // Restrict semantic scoring to the bounded lexical candidate set. A
    // missing lexical match is an explicit no-match result.
    let match_query = query
        .split_whitespace()
        .map(|term| format!("\"{}\"", term.replace('"', "")))
        .collect::<Vec<_>>()
        .join(" OR ");
    let mut candidates = Vec::new();
    if !match_query.trim().is_empty() {
        let mut stmt = conn.prepare(
            "SELECT m.id,m.chat,m.source,m.date,m.user_name,m.message,m.vector
             FROM context_messages_fts f
             JOIN context_messages m ON m.id=f.rowid
             WHERE context_messages_fts MATCH ?1
               AND (?2 IS NULL OR m.chat=?2)
               AND (?3 IS NULL OR m.source=?3)
             ORDER BY bm25(context_messages_fts), m.id ASC
             LIMIT ?4",
        )?;
        let candidate_limit =
            CONTEXT_VECTOR_CANDIDATE_CAP.saturating_add(excluded_context_ids.len());
        let rows = stmt.query_map(
            params![match_query, chat, source, candidate_limit as i64],
            |row| {
                let bytes: Vec<u8> = row.get(6)?;
                if bytes.len() != VECTOR_DIM * 4 {
                    return Err(rusqlite::Error::InvalidColumnType(
                        6,
                        "vector".into(),
                        rusqlite::types::Type::Blob,
                    ));
                }
                Ok((
                    row.get::<_, i64>(0)?,
                    ContextResult {
                        chat: row.get(1)?,
                        source: row.get(2)?,
                        date: row.get(3)?,
                        user: row.get(4)?,
                        message: row.get(5)?,
                        score: 0.0,
                        mode: "vector".into(),
                    },
                    bytes_to_vector(&bytes),
                ))
            },
        )?;
        for row in rows {
            let (id, mut result, vector) = row?;
            if excluded_context_ids.contains(&id) {
                continue;
            }
            result.score = cosine(&query_vector, &vector);
            candidates.push(ContextCandidate { id, result });
            if candidates.len() == CONTEXT_VECTOR_CANDIDATE_CAP {
                break;
            }
        }
    }

    candidates.sort_by(|a, b| {
        b.result
            .score
            .partial_cmp(&a.result.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    Ok(candidates)
}

fn topic_search_excluding(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    excluded_context_ids: &BTreeSet<i64>,
) -> Result<Vec<ContextCandidate>> {
    if !context_topic_tables_ready(conn)? {
        return Ok(Vec::new());
    }
    let topics = classify_message_topics(query);
    if topics.is_empty() {
        return Ok(Vec::new());
    }
    let mut stmt = conn.prepare(
        "SELECT m.id, m.chat, m.source, m.date, m.user_name, m.message
         FROM context_message_topics t
         JOIN context_messages m ON m.id = t.message_id
         WHERE t.topic = ?1
           AND (?2 IS NULL OR m.chat = ?2)
           AND (?3 IS NULL OR m.source = ?3)
         ORDER BY m.id DESC
         LIMIT ?4",
    )?;
    let candidate_limit = CONTEXT_TOPIC_CANDIDATE_CAP.saturating_add(excluded_context_ids.len());
    let mut results = Vec::new();
    let mut seen = BTreeSet::new();
    for topic in topics {
        let rows = stmt.query_map(
            params![topic, chat, source, candidate_limit as i64],
            |row| {
                Ok(ContextCandidate {
                    id: row.get(0)?,
                    result: ContextResult {
                        chat: row.get(1)?,
                        source: row.get(2)?,
                        date: row.get(3)?,
                        user: row.get(4)?,
                        message: row.get(5)?,
                        score: 1.0,
                        mode: "topic".into(),
                    },
                })
            },
        )?;
        for row in rows {
            let row = row?;
            if excluded_context_ids.contains(&row.id) || !seen.insert(row.id) {
                continue;
            }
            results.push(row);
            if results.len() == CONTEXT_TOPIC_CANDIDATE_CAP {
                return Ok(results);
            }
        }
    }
    Ok(results)
}

fn hybrid_search_excluding(
    conn: &Connection,
    chat: Option<&str>,
    source: Option<&str>,
    query: &str,
    limit: usize,
    excluded_context_ids: &BTreeSet<i64>,
    embedder: &dyn Embedder,
) -> Result<Vec<ContextResult>> {
    let keyword = keyword_search_excluding(conn, chat, source, query, excluded_context_ids)?;
    // An empty vector query is a fail-closed hybrid result: keyword evidence
    // remains available, while no synthetic vector score is introduced.
    let vector = if embedder.embed(query).iter().all(|value| *value == 0.0) {
        Vec::new()
    } else {
        vector_search_excluding(conn, chat, source, query, excluded_context_ids, embedder)?
    };
    let mut merged = keyword
        .into_iter()
        .enumerate()
        .map(|(rank, mut item)| {
            item.result.score = 1.0 / (rank as f32 + 1.0);
            item.result.mode = "hybrid".into();
            item
        })
        .collect::<Vec<_>>();
    for (rank, item) in vector.into_iter().enumerate() {
        let score = 1.0 / (rank as f32 + 1.0);
        if let Some(existing) = merged.iter_mut().find(|existing| existing.id == item.id) {
            existing.result.score += score;
        } else {
            let mut item = item;
            item.result.score = score;
            item.result.mode = "hybrid".into();
            merged.push(item);
        }
    }
    let topics = topic_search_excluding(conn, chat, source, query, excluded_context_ids)?;
    for (rank, item) in topics.into_iter().enumerate() {
        let score = 1.0 / (rank as f32 + 1.0);
        if let Some(existing) = merged.iter_mut().find(|existing| existing.id == item.id) {
            existing.result.score += score;
        } else {
            let mut item = item;
            item.result.score = score;
            item.result.mode = "hybrid".into();
            merged.push(item);
        }
    }
    const REFERENCE_PREFIX: &str = "[설명자료]";
    for item in &mut merged {
        if item.result.message.starts_with(REFERENCE_PREFIX) {
            item.result.score += 0.35;
        }
    }
    merged.sort_by(|a, b| {
        b.result
            .score
            .partial_cmp(&a.result.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    Ok(merged
        .into_iter()
        .take(limit.min(CONTEXT_KEYWORD_CANDIDATE_CAP))
        .map(|candidate| candidate.result)
        .collect())
}
