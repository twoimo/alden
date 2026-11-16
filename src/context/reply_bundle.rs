//! The versioned reply bundle (`ContextReplyBundle`,
//! `RecipientContextReplyBundle`) read in one snapshot, with live-burst
//! exclusions and a JSON size bound.

use super::embedding::LocalHashEmbedder;
use super::live::preferred_context_source;
use super::redact::{provenance_id, redact_absolute_paths, redact_reply_decisions};
use super::reply_decision::{reply_decision_search_with_connection_excluding, ReplyDecisionMatch};
use super::response_time::{response_time_stats_with_connection, ResponseTimeStats};
use super::schema::ensure_retrieval_index_current;
use super::search::{
    recipient_style_search_with_connection, search_with_connection_excluding,
    style_search_with_connection, ContextResult,
};
use super::store::open_db_readonly;
use super::style::{
    recipient_style_profile_with_connection, style_profile_with_connection,
    validate_style_profile_laughter, RecipientStyleProfile, StyleProfile,
    RECIPIENT_STYLE_MIN_CONFIDENCE, RECIPIENT_STYLE_MIN_SAMPLES, STYLE_POLICY_VERSION, STYLE_USER,
};
use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use serde::Serialize;
use std::collections::BTreeSet;
use std::path::Path;

pub const CONTEXT_REPLY_BUNDLE_MAX_EXCLUDED_LOG_IDS: usize = 6;

pub(super) const CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION: u32 = 2;

pub(super) const RECIPIENT_CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION: u32 = 3;

pub(super) const CONTEXT_REPLY_BUNDLE_CONTEXT_LIMIT: usize = 8;

pub(super) const CONTEXT_REPLY_BUNDLE_STYLE_LIMIT: usize = 12;

pub(super) const CONTEXT_REPLY_BUNDLE_DECISION_LIMIT: usize = 6;

const CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Serialize)]
pub struct ContextReplyBundle {
    pub schema_version: u32,
    pub context: Vec<ContextResult>,
    pub styles: Vec<ContextResult>,
    pub prior_decisions: Vec<ReplyDecisionMatch>,
    pub style_profile: Option<StyleProfile>,
    pub response_time: Option<ResponseTimeStats>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RecipientContextReplyBundle {
    pub schema_version: u32,
    pub recipient: String,
    pub context: Vec<ContextResult>,
    pub styles: Vec<ContextResult>,
    pub prior_decisions: Vec<ReplyDecisionMatch>,
    pub style_profile: Option<StyleProfile>,
    pub recipient_style_profile: Option<RecipientStyleProfile>,
    pub response_time: Option<ResponseTimeStats>,
}

impl ContextReplyBundle {
    fn validate_json_size(&self) -> Result<()> {
        if self
            .styles
            .iter()
            .any(|style| crate::reply_policy::validate_auto_reply_laughter(&style.message).is_err())
        {
            anyhow::bail!("context reply bundle contains disallowed style evidence");
        }
        if let Some(profile) = &self.style_profile {
            if profile.sample_count == 0 || profile.user != STYLE_USER {
                anyhow::bail!("context reply bundle style profile is invalid");
            }
            if profile.policy_version != STYLE_POLICY_VERSION {
                anyhow::bail!("context reply bundle style policy mismatch");
            }
            validate_style_profile_laughter(profile, "context reply bundle style profile")?;
        }
        for prior in &self.prior_decisions {
            let evidence: serde_json::Value = serde_json::from_str(&prior.evidence_json)
                .context("context reply bundle evidence JSON is invalid")?;
            if !evidence.is_object() {
                anyhow::bail!("context reply bundle evidence JSON is not an object");
            }
        }
        let bytes = serde_json::to_vec(self)?;
        if bytes.len() > CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES {
            anyhow::bail!(
                "context reply bundle exceeds {} bytes",
                CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES
            );
        }
        Ok(())
    }
    fn redact_provenance(&mut self) {
        for result in self.context.iter_mut().chain(self.styles.iter_mut()) {
            result.source = provenance_id(&result.source);
        }
        if let Some(profile) = self.style_profile.as_mut() {
            profile.source = provenance_id(&profile.source);
        }
        if let Some(response_time) = self.response_time.as_mut() {
            response_time.source = provenance_id(&response_time.source);
        }
        for prior in &mut self.prior_decisions {
            if let Ok(mut evidence) =
                serde_json::from_str::<serde_json::Value>(&prior.evidence_json)
            {
                redact_absolute_paths(&mut evidence);
                if let Ok(serialized) = serde_json::to_string(&evidence) {
                    prior.evidence_json = serialized;
                }
            }
        }
    }
}

impl RecipientContextReplyBundle {
    fn validate_json_size(&self) -> Result<()> {
        if self.schema_version != RECIPIENT_CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION
            || self.recipient.trim().is_empty()
        {
            anyhow::bail!("recipient context reply bundle identity is invalid");
        }
        ContextReplyBundle {
            schema_version: CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION,
            context: self.context.clone(),
            styles: self.styles.clone(),
            prior_decisions: self.prior_decisions.clone(),
            style_profile: self.style_profile.clone(),
            response_time: self.response_time.clone(),
        }
        .validate_json_size()?;
        if let Some(recipient_profile) = &self.recipient_style_profile {
            if recipient_profile.recipient != self.recipient
                || recipient_profile.profile.sample_count == 0
                || recipient_profile.profile.user != STYLE_USER
                || recipient_profile.profile.policy_version != STYLE_POLICY_VERSION
                || !recipient_profile.confidence_sum.is_finite()
                || recipient_profile.confidence_sum < 0.0
            {
                anyhow::bail!("recipient context reply bundle style profile is invalid");
            }
            validate_style_profile_laughter(
                &recipient_profile.profile,
                "recipient context reply bundle style profile",
            )?;
            if !recipient_profile.used_fallback
                && (recipient_profile.direct_sample_count < RECIPIENT_STYLE_MIN_SAMPLES
                    || recipient_profile.confidence_sum < RECIPIENT_STYLE_MIN_CONFIDENCE)
            {
                anyhow::bail!("recipient context reply bundle direct style evidence is weak");
            }
        }
        let bytes = serde_json::to_vec(self)?;
        if bytes.len() > CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES {
            anyhow::bail!(
                "recipient context reply bundle exceeds {} bytes",
                CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES
            );
        }
        Ok(())
    }

    fn redact_provenance(&mut self) {
        for result in self.context.iter_mut().chain(self.styles.iter_mut()) {
            result.source = provenance_id(&result.source);
        }
        if let Some(profile) = self.style_profile.as_mut() {
            profile.source = provenance_id(&profile.source);
        }
        if let Some(profile) = self.recipient_style_profile.as_mut() {
            profile.profile.source = provenance_id(&profile.profile.source);
        }
        if let Some(response_time) = self.response_time.as_mut() {
            response_time.source = provenance_id(&response_time.source);
        }
        redact_reply_decisions(&mut self.prior_decisions);
    }
}

#[derive(Debug)]
pub(super) struct LiveEventExclusions {
    chat_id: i64,
    log_ids: BTreeSet<i64>,
    decision_event_ids: BTreeSet<String>,
}

pub(super) fn live_event_exclusions(chat_id: i64, log_ids: &[i64]) -> Result<LiveEventExclusions> {
    if chat_id <= 0 {
        anyhow::bail!("live context exclusion chat ID must be positive");
    }
    if log_ids.is_empty() || log_ids.len() > CONTEXT_REPLY_BUNDLE_MAX_EXCLUDED_LOG_IDS {
        anyhow::bail!(
            "live context exclusions must contain between 1 and {} log IDs",
            CONTEXT_REPLY_BUNDLE_MAX_EXCLUDED_LOG_IDS
        );
    }
    let mut unique_log_ids = BTreeSet::new();
    for log_id in log_ids {
        if *log_id <= 0 {
            anyhow::bail!("live context exclusion log IDs must be positive");
        }
        if !unique_log_ids.insert(*log_id) {
            anyhow::bail!("live context exclusion log IDs must be unique");
        }
    }
    let decision_event_ids = unique_log_ids
        .iter()
        .map(|log_id| format!("db:{chat_id}:{log_id}"))
        .collect();
    Ok(LiveEventExclusions {
        chat_id,
        log_ids: unique_log_ids,
        decision_event_ids,
    })
}

fn context_ids_for_excluded_live_events(
    conn: &Connection,
    chat: &str,
    preferred_source: Option<&str>,
    exclusions: &LiveEventExclusions,
) -> Result<BTreeSet<i64>> {
    let has_live_events = conn.query_row(
        "SELECT EXISTS(
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'context_live_events'
         )",
        [],
        |row| row.get::<_, i64>(0),
    )? != 0;
    if !has_live_events {
        return Ok(BTreeSet::new());
    }
    let mut stmt = conn.prepare(
        "SELECT events.context_message_id
         FROM context_live_events events
         JOIN context_messages messages
           ON messages.id = events.context_message_id
          AND messages.source = events.source
         WHERE events.chat_id = ?1 AND events.log_id = ?2
           AND messages.chat = ?3
           AND (?4 IS NULL OR events.source = ?4)
         ORDER BY events.source ASC, events.context_message_id ASC",
    )?;
    let mut context_ids = BTreeSet::new();
    for log_id in &exclusions.log_ids {
        let rows = stmt.query_map(
            params![exclusions.chat_id, log_id, chat, preferred_source],
            |row| row.get::<_, i64>(0),
        )?;
        let mut mapped_context_id = None;
        for row in rows {
            let context_id = row?;
            if mapped_context_id.replace(context_id).is_some() {
                anyhow::bail!("live context exclusion mapping is ambiguous");
            }
        }
        if let Some(context_id) = mapped_context_id {
            context_ids.insert(context_id);
        }
    }
    Ok(context_ids)
}

pub fn context_reply_bundle(
    db_path: &Path,
    chat: &str,
    query: &str,
    source: Option<&str>,
) -> Result<ContextReplyBundle> {
    if chat.trim().is_empty() {
        anyhow::bail!("chat name must not be empty");
    }
    // Punctuation-only or blank inbound still needs style/timing evidence.

    let conn = open_db_readonly(db_path)?;
    conn.execute_batch("BEGIN DEFERRED TRANSACTION")?;
    let result = (|| {
        ensure_retrieval_index_current(&conn)?;
        let preferred_source = preferred_context_source(&conn, Some(chat), source)?;
        let context = search_with_connection_excluding(
            &conn,
            Some(chat),
            preferred_source.as_deref(),
            query,
            "hybrid",
            CONTEXT_REPLY_BUNDLE_CONTEXT_LIMIT,
            &BTreeSet::new(),
            &LocalHashEmbedder,
        )?;
        let styles = style_search_with_connection(
            &conn,
            Some(chat),
            preferred_source.as_deref(),
            query,
            CONTEXT_REPLY_BUNDLE_STYLE_LIMIT,
            true,
        )?;
        let prior_decisions = reply_decision_search_with_connection_excluding(
            &conn,
            chat,
            query,
            CONTEXT_REPLY_BUNDLE_DECISION_LIMIT,
            &BTreeSet::new(),
        )?;
        let style_profile = style_profile_with_connection(
            &conn,
            chat,
            STYLE_USER,
            preferred_source.as_deref(),
            true,
        )?;
        let response_time = response_time_stats_with_connection(
            &conn,
            chat,
            STYLE_USER,
            preferred_source.as_deref(),
        )?;
        let mut bundle = ContextReplyBundle {
            schema_version: CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION,
            context,
            styles,
            prior_decisions,
            style_profile,
            response_time,
        };
        bundle.redact_provenance();
        bundle.validate_json_size()?;
        Ok(bundle)
    })();

    match result {
        Ok(bundle) => {
            conn.execute_batch("COMMIT")?;
            Ok(bundle)
        }
        Err(error) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(error)
        }
    }
}

/// Build the version-2 reply bundle for a specific participant in one read
/// snapshot. Every live row of the incoming burst (`excluded_log_ids`) is
/// excluded from context retrieval and prior decisions. Direct
/// recipient-linked style evidence is used only when its profile clears the
/// sample/confidence floor; otherwise both profile and style examples fall
/// back to the authoritative room-wide register.
pub fn context_reply_bundle_for_recipient_excluding_live_events(
    db_path: &Path,
    chat: &str,
    query: &str,
    source: Option<&str>,
    recipient: &str,
    chat_id: i64,
    excluded_log_ids: &[i64],
) -> Result<RecipientContextReplyBundle> {
    if chat.trim().is_empty() || recipient.trim().is_empty() {
        anyhow::bail!("recipient context reply bundle chat and recipient must not be empty");
    }
    // Recipient bundles keep style/timing when the inbound has no tokens.
    let exclusions = live_event_exclusions(chat_id, excluded_log_ids)?;

    let conn = open_db_readonly(db_path)?;
    conn.execute_batch("BEGIN DEFERRED TRANSACTION")?;
    let result = (|| {
        ensure_retrieval_index_current(&conn)?;
        let preferred_source = preferred_context_source(&conn, Some(chat), source)?;
        let excluded_context_ids = context_ids_for_excluded_live_events(
            &conn,
            chat,
            preferred_source.as_deref(),
            &exclusions,
        )?;
        let context = search_with_connection_excluding(
            &conn,
            Some(chat),
            preferred_source.as_deref(),
            query,
            "hybrid",
            CONTEXT_REPLY_BUNDLE_CONTEXT_LIMIT,
            &excluded_context_ids,
            &LocalHashEmbedder,
        )?;
        let recipient_style_profile = recipient_style_profile_with_connection(
            &conn,
            chat,
            recipient,
            preferred_source.as_deref(),
        )?;
        let styles = if let Some(profile) = recipient_style_profile
            .as_ref()
            .filter(|profile| !profile.used_fallback)
        {
            recipient_style_search_with_connection(
                &conn,
                chat,
                &profile.profile.source,
                recipient,
                query,
                CONTEXT_REPLY_BUNDLE_STYLE_LIMIT,
            )?
        } else {
            style_search_with_connection(
                &conn,
                Some(chat),
                preferred_source.as_deref(),
                query,
                CONTEXT_REPLY_BUNDLE_STYLE_LIMIT,
                true,
            )?
        };
        let prior_decisions = reply_decision_search_with_connection_excluding(
            &conn,
            chat,
            query,
            CONTEXT_REPLY_BUNDLE_DECISION_LIMIT,
            &exclusions.decision_event_ids,
        )?;
        let style_profile = style_profile_with_connection(
            &conn,
            chat,
            STYLE_USER,
            preferred_source.as_deref(),
            true,
        )?;
        let response_time = response_time_stats_with_connection(
            &conn,
            chat,
            STYLE_USER,
            preferred_source.as_deref(),
        )?;
        let mut bundle = RecipientContextReplyBundle {
            schema_version: RECIPIENT_CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION,
            recipient: recipient.to_string(),
            context,
            styles,
            prior_decisions,
            style_profile,
            recipient_style_profile,
            response_time,
        };
        bundle.redact_provenance();
        bundle.validate_json_size()?;
        Ok(bundle)
    })();

    match result {
        Ok(bundle) => {
            conn.execute_batch("COMMIT")?;
            Ok(bundle)
        }
        Err(error) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(error)
        }
    }
}

pub fn context_reply_bundle_for_recipient_excluding_live_events_json(
    db_path: &Path,
    chat: &str,
    query: &str,
    source: Option<&str>,
    recipient: &str,
    chat_id: i64,
    excluded_log_ids: &[i64],
) -> Result<String> {
    let bundle = context_reply_bundle_for_recipient_excluding_live_events(
        db_path,
        chat,
        query,
        source,
        recipient,
        chat_id,
        excluded_log_ids,
    )?;
    let output = serde_json::to_string(&bundle)?;
    if output.len() > CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES {
        anyhow::bail!(
            "recipient context reply bundle exceeds {} bytes",
            CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES
        );
    }
    Ok(output)
}

pub fn context_reply_bundle_json(
    db_path: &Path,
    chat: &str,
    query: &str,
    source: Option<&str>,
) -> Result<String> {
    let bundle = context_reply_bundle(db_path, chat, query, source)?;
    let output = serde_json::to_string(&bundle)?;
    if output.len() > CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES {
        anyhow::bail!(
            "context reply bundle exceeds {} bytes",
            CONTEXT_REPLY_BUNDLE_MAX_JSON_BYTES
        );
    }
    Ok(output)
}
