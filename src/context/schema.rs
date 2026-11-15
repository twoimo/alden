//! Context database tables, the retrieval-index version marker, and every
//! migration that brings an older database up to date.
//! Schema version strings stay `&str` and must not be normalized.

use super::live::RecipientStyleAccumulator;
use super::response_time::{
    fit_response_time_distribution, response_time_distribution_is_valid, ResponseTimeDistribution,
    RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION,
};
use super::store::open_db;
use super::style::{
    classify_style_message, insert_style_profile, StyleProfileAccumulator, STYLE_POLICY_VERSION,
    STYLE_USER,
};
use super::topics::{backfill_message_topics, ensure_topic_lexicon};
use anyhow::Result;
use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use std::cmp::Ordering;
use std::collections::BTreeMap;
use std::path::Path;

pub(super) const RETRIEVAL_INDEX_SCHEMA_VERSION: &str = "2";

pub(super) const CONTEXT_RETRIEVAL_MIGRATION_REQUIRED: &str =
    "context retrieval index migration required; run context-index";

pub(super) const LIVE_CONTEXT_SCHEMA_VERSION: &str = "2";

/// Apply the additive context/FTS/live-index migrations without ingesting or
/// deleting any context rows. Safe to call before a first sync state lookup.
pub fn ensure_live_context_schema(db_path: &Path) -> Result<()> {
    let _ = open_db(db_path)?;
    Ok(())
}

/// Rename legacy `choi_yeonwoo_*` style tables to their `owner_*` names when an
/// older database is opened, so its data survives the owner-scope
/// generalization (R10.6). Only renames when the legacy table exists and the
/// renamed table does not, so it is safe to run on every open and on fresh
/// databases (where it does nothing).
fn rename_legacy_owner_tables(conn: &Connection) -> Result<()> {
    const RENAMES: [(&str, &str); 4] = [
        ("choi_yeonwoo_style", "owner_style"),
        ("choi_yeonwoo_style_profile", "owner_style_profile"),
        (
            "choi_yeonwoo_recipient_style_profile",
            "owner_recipient_style_profile",
        ),
        (
            "choi_yeonwoo_recipient_style_samples",
            "owner_recipient_style_samples",
        ),
    ];
    let table_present = |name: &str| -> Result<bool> {
        Ok(conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
                params![name],
                |_| Ok(()),
            )
            .optional()?
            .is_some())
    };
    for (legacy, renamed) in RENAMES {
        if table_present(legacy)? && !table_present(renamed)? {
            conn.execute_batch(&format!("ALTER TABLE {legacy} RENAME TO {renamed}"))?;
        }
    }
    Ok(())
}

pub(super) fn migrate(conn: &Connection, was_missing: bool) -> Result<()> {
    // Owner-scope rename (R10.6): before the `CREATE TABLE IF NOT EXISTS owner_*`
    // statements run, rename any legacy `choi_yeonwoo_*` tables so an upgrading
    // database keeps its style/context data instead of orphaning it under the
    // old names. `profile::migrate_owner_scope` later adds the `owner_key`
    // column and records the migration marker once the owner is confirmed.
    rename_legacy_owner_tables(conn)?;
    conn.execute_batch("PRAGMA secure_delete = ON;
        PRAGMA journal_size_limit = 0;
        PRAGMA wal_autocheckpoint = 1000;
        CREATE TABLE IF NOT EXISTS context_messages(id INTEGER PRIMARY KEY, source TEXT NOT NULL, chat TEXT NOT NULL, date TEXT NOT NULL, user_name TEXT NOT NULL, message TEXT NOT NULL, vector BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_context_messages_chat_source ON context_messages(chat, source);
        CREATE TABLE IF NOT EXISTS context_retrieval_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS context_messages_fts USING fts5(message, user_name, chat, content='context_messages', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS context_messages_ai AFTER INSERT ON context_messages BEGIN INSERT INTO context_messages_fts(rowid,message,user_name,chat) VALUES(new.id,new.message,new.user_name,new.chat); END;
        CREATE TRIGGER IF NOT EXISTS context_messages_ad AFTER DELETE ON context_messages BEGIN INSERT INTO context_messages_fts(context_messages_fts,rowid,message,user_name,chat) VALUES('delete',old.id,old.message,old.user_name,old.chat); END;
        CREATE TRIGGER IF NOT EXISTS context_messages_au AFTER UPDATE ON context_messages BEGIN INSERT INTO context_messages_fts(context_messages_fts,rowid,message,user_name,chat) VALUES('delete',old.id,old.message,old.user_name,old.chat); INSERT INTO context_messages_fts(rowid,message,user_name,chat) VALUES(new.id,new.message,new.user_name,new.chat); END;
        CREATE TABLE IF NOT EXISTS owner_style(id INTEGER PRIMARY KEY, source TEXT NOT NULL, chat TEXT NOT NULL, date TEXT NOT NULL, user_name TEXT NOT NULL CHECK(user_name = '최연우'), message TEXT NOT NULL, vector BLOB NOT NULL, source_row INTEGER NOT NULL DEFAULT 0, content_kind TEXT NOT NULL DEFAULT 'legacy', style_eligible INTEGER NOT NULL DEFAULT 1, policy_version TEXT NOT NULL DEFAULT 'legacy-v1', features_json TEXT NOT NULL DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS idx_owner_style_chat_source ON owner_style(chat, source);
        CREATE TABLE IF NOT EXISTS owner_style_profile(
            chat TEXT NOT NULL,
            source TEXT NOT NULL,
            user_name TEXT NOT NULL CHECK(user_name = '최연우'),
            sample_count INTEGER NOT NULL,
            average_character_length REAL NOT NULL,
            median_character_length REAL NOT NULL,
            p90_character_length REAL NOT NULL,
            casual_ending_count INTEGER NOT NULL,
            casual_ending_counts_json TEXT NOT NULL DEFAULT '{}',
            question_count INTEGER NOT NULL,
            emoji_count INTEGER NOT NULL,
            punctuation_count INTEGER NOT NULL,
            common_endings_json TEXT NOT NULL DEFAULT '{}',
            common_tokens_json TEXT NOT NULL DEFAULT '{}',
            policy_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(chat, source, user_name)
        );
        CREATE INDEX IF NOT EXISTS idx_owner_style_profile_chat_user ON owner_style_profile(chat, user_name);
        CREATE TABLE IF NOT EXISTS response_time_stats(chat TEXT NOT NULL, source TEXT NOT NULL, user_name TEXT NOT NULL CHECK(user_name = '최연우'), sample_count INTEGER NOT NULL, average_seconds REAL NOT NULL, median_seconds REAL NOT NULL, p90_seconds REAL NOT NULL, min_seconds REAL NOT NULL, max_seconds REAL NOT NULL, max_window_seconds INTEGER NOT NULL, stddev_seconds REAL NOT NULL DEFAULT 0.0, distribution_schema_version INTEGER NOT NULL DEFAULT 0, distribution_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(chat, source, user_name));
        CREATE INDEX IF NOT EXISTS idx_response_time_stats_chat_user ON response_time_stats(chat, user_name);
        CREATE TABLE IF NOT EXISTS reply_decisions(
            event_id TEXT PRIMARY KEY,
            chat TEXT NOT NULL,
            author TEXT NOT NULL,
            received_at TEXT NOT NULL,
            message TEXT NOT NULL,
            vector BLOB NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('reply', 'skip')),
            reason TEXT NOT NULL,
            category TEXT NOT NULL,
            context_match_count INTEGER NOT NULL,
            style_match_count INTEGER NOT NULL,
            best_context_score REAL NOT NULL,
            best_style_score REAL NOT NULL,
            prior_similarity REAL NOT NULL,
            scheduled_delay_seconds REAL NOT NULL,
            status TEXT NOT NULL,
            reply TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_reply_decisions_chat_created ON reply_decisions(chat, created_at);
        CREATE INDEX IF NOT EXISTS idx_reply_decisions_chat_status ON reply_decisions(chat, status);")?;
    let style_columns = conn
        .prepare("PRAGMA table_info(owner_style)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (name, declaration) in [
        ("source_row", "INTEGER NOT NULL DEFAULT 0"),
        ("content_kind", "TEXT NOT NULL DEFAULT 'legacy'"),
        ("style_eligible", "INTEGER NOT NULL DEFAULT 1"),
        ("policy_version", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
        ("features_json", "TEXT NOT NULL DEFAULT '{}'"),
    ] {
        if !style_columns.iter().any(|column| column == name) {
            conn.execute(
                &format!("ALTER TABLE owner_style ADD COLUMN {name} {declaration}"),
                [],
            )?;
        }
    }
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_owner_style_eligible
         ON owner_style(chat, source, style_eligible)",
        [],
    )?;
    let response_time_columns = conn
        .prepare("PRAGMA table_info(response_time_stats)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if !response_time_columns
        .iter()
        .any(|name| name == "stddev_seconds")
    {
        conn.execute(
            "ALTER TABLE response_time_stats ADD COLUMN stddev_seconds REAL NOT NULL DEFAULT 0.0",
            [],
        )?;
    }
    if !response_time_columns
        .iter()
        .any(|name| name == "distribution_schema_version")
    {
        conn.execute(
            "ALTER TABLE response_time_stats ADD COLUMN distribution_schema_version INTEGER NOT NULL DEFAULT 0",
            [],
        )?;
    }
    if !response_time_columns
        .iter()
        .any(|name| name == "distribution_json")
    {
        conn.execute(
            "ALTER TABLE response_time_stats ADD COLUMN distribution_json TEXT NOT NULL DEFAULT '{}'",
            [],
        )?;
    }
    let reply_decision_columns = conn
        .prepare("PRAGMA table_info(reply_decisions)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if !reply_decision_columns
        .iter()
        .any(|name| name == "evidence_json")
    {
        conn.execute(
            "ALTER TABLE reply_decisions ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'",
            [],
        )?;
    }
    if was_missing {
        conn.execute(
            "INSERT INTO context_retrieval_meta(key, value) VALUES ('fts_schema', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [RETRIEVAL_INDEX_SCHEMA_VERSION],
        )?;
    }
    migrate_live_context_schema(conn)?;
    migrate_style_policy(conn)?;
    migrate_response_time_distribution_schema(conn)?;
    Ok(())
}

fn migrate_style_policy(conn: &Connection) -> Result<()> {
    const MIGRATION_KEY: &str = "style_policy_version";
    let current: Option<String> = conn
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = ?1",
            [MIGRATION_KEY],
            |row| row.get(0),
        )
        .optional()?;
    if current.as_deref() == Some(STYLE_POLICY_VERSION) {
        return Ok(());
    }

    let tx = conn.unchecked_transaction()?;
    let style_rows = {
        let mut stmt = tx.prepare("SELECT id, message FROM owner_style ORDER BY id ASC")?;
        let rows = stmt
            .query_map([], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };
    for (id, message) in style_rows {
        let features = classify_style_message(&message);
        tx.execute(
            "UPDATE owner_style
             SET content_kind = ?2, style_eligible = ?3,
                 policy_version = ?4, features_json = ?5
             WHERE id = ?1",
            params![
                id,
                features.content_kind,
                features.style_eligible as i64,
                STYLE_POLICY_VERSION,
                features.features_json,
            ],
        )?;
    }
    tx.execute(
        "DELETE FROM owner_recipient_style_samples
         WHERE style_message_id IN (
             SELECT id FROM owner_style
             WHERE style_eligible <> 1 OR policy_version <> ?1
         )",
        [STYLE_POLICY_VERSION],
    )?;

    let now = Utc::now().to_rfc3339();
    tx.execute("DELETE FROM owner_style_profile", [])?;
    let room_sources = {
        let mut stmt = tx.prepare(
            "SELECT DISTINCT source, chat FROM owner_style
             WHERE user_name = ?1 AND style_eligible = 1
               AND policy_version = ?2
             ORDER BY source ASC, chat ASC",
        )?;
        let rows = stmt
            .query_map(params![STYLE_USER, STYLE_POLICY_VERSION], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };
    for (source, chat) in room_sources {
        let messages = {
            let mut stmt = tx.prepare(
                "SELECT message FROM owner_style
                 WHERE source = ?1 AND chat = ?2 AND user_name = ?3
                   AND style_eligible = 1 AND policy_version = ?4
                 ORDER BY source_row ASC, id ASC",
            )?;
            let rows = stmt
                .query_map(
                    params![source, chat, STYLE_USER, STYLE_POLICY_VERSION],
                    |row| row.get::<_, String>(0),
                )?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            rows
        };
        let mut accumulator = StyleProfileAccumulator::default();
        for message in messages {
            let features = classify_style_message(&message);
            if features.style_eligible {
                accumulator.add(&message, &features);
            }
        }
        if let Some(profile) = accumulator.finish(&chat, &source, STYLE_USER) {
            insert_style_profile(&tx, &profile, &now)?;
        }
    }

    tx.execute("DELETE FROM owner_recipient_style_profile", [])?;
    let recipient_rows = {
        let mut stmt = tx.prepare(
            "SELECT samples.source, samples.chat_id, sources.chat,
                    samples.recipient, samples.confidence, styles.message
             FROM owner_recipient_style_samples samples
             JOIN owner_style styles ON styles.id = samples.style_message_id
             JOIN context_sources sources ON sources.source = samples.source
             WHERE styles.user_name = ?1 AND styles.style_eligible = 1
               AND styles.policy_version = ?2
             ORDER BY samples.source ASC, samples.chat_id ASC,
                      samples.recipient ASC, samples.reply_log_id ASC",
        )?;
        let rows = stmt
            .query_map(params![STYLE_USER, STYLE_POLICY_VERSION], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, f64>(4)?,
                    row.get::<_, String>(5)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };
    let mut recipient_accumulators: BTreeMap<
        (String, i64, String, String),
        RecipientStyleAccumulator,
    > = BTreeMap::new();
    for (source, chat_id, chat, recipient, confidence, message) in recipient_rows {
        if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) || confidence == 0.0 {
            anyhow::bail!("recipient style confidence is malformed");
        }
        let features = classify_style_message(&message);
        if !features.style_eligible {
            continue;
        }
        let accumulator = recipient_accumulators
            .entry((source, chat_id, chat, recipient))
            .or_default();
        accumulator.style.add(&message, &features);
        accumulator.sample_count += 1;
        accumulator.confidence_sum += confidence;
    }
    for ((source, chat_id, chat, recipient), accumulator) in recipient_accumulators {
        let Some(profile) = accumulator.style.finish(&chat, &source, STYLE_USER) else {
            continue;
        };
        tx.execute(
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

    tx.execute(
        "INSERT INTO context_retrieval_meta(key, value) VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![MIGRATION_KEY, STYLE_POLICY_VERSION],
    )?;
    tx.commit()?;
    Ok(())
}

fn migrate_response_time_distribution_schema(conn: &Connection) -> Result<()> {
    let migration_key = "response_time_distribution_schema";
    let expected_version = RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION.to_string();
    let current: Option<String> = conn
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = ?1",
            [migration_key],
            |row| row.get(0),
        )
        .optional()?;
    let mut live_rows = conn
        .prepare(
            "SELECT stats.chat, stats.source, sources.chat_id,
                    stats.distribution_schema_version, stats.distribution_json
             FROM response_time_stats stats
             JOIN context_sources sources ON sources.source = stats.source
             WHERE stats.user_name = ?1
             ORDER BY stats.source ASC",
        )?
        .query_map([STYLE_USER], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, String>(4)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    live_rows.sort();
    let valid = |schema_version: i64, distribution_json: &str| {
        schema_version == RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION as i64
            && serde_json::from_str::<ResponseTimeDistribution>(distribution_json)
                .ok()
                .as_ref()
                .is_some_and(response_time_distribution_is_valid)
    };
    if current.as_deref() == Some(expected_version.as_str())
        && live_rows
            .iter()
            .all(|(_, _, _, schema_version, distribution_json)| {
                valid(*schema_version, distribution_json)
            })
    {
        return Ok(());
    }
    let tx = conn.unchecked_transaction()?;
    for (_chat, source, chat_id, schema_version, distribution_json) in live_rows {
        if current.as_deref() == Some(expected_version.as_str())
            && valid(schema_version, &distribution_json)
        {
            continue;
        }
        let mut delays = tx
            .prepare(
                "SELECT delay_seconds FROM response_time_samples
                 WHERE source = ?1 AND chat_id = ?2
                 ORDER BY delay_seconds ASC, reply_log_id ASC",
            )?
            .query_map(params![source, chat_id], |row| row.get::<_, f64>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        delays.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
        let distribution = fit_response_time_distribution(&delays);
        let (schema_version, distribution_json) = match distribution {
            Some(distribution) => (
                distribution.schema_version as i64,
                serde_json::to_string(&distribution)?,
            ),
            None => (0, "{}".to_string()),
        };
        tx.execute(
            "UPDATE response_time_stats
             SET distribution_schema_version = ?3, distribution_json = ?4
             WHERE source = ?1 AND user_name = ?2",
            params![source, STYLE_USER, schema_version, distribution_json],
        )?;
    }
    tx.execute(
        "INSERT INTO context_retrieval_meta(key, value) VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![migration_key, expected_version],
    )?;
    tx.commit()?;
    Ok(())
}

fn migrate_live_context_schema(conn: &Connection) -> Result<()> {
    let tx = conn.unchecked_transaction()?;
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS context_sources(
            source TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind = 'local_db'),
            account_fingerprint TEXT NOT NULL,
            chat_id INTEGER NOT NULL CHECK(chat_id > 0),
            chat TEXT NOT NULL,
            authoritative INTEGER NOT NULL DEFAULT 0 CHECK(authoritative IN (0, 1)),
            checkpoint_log_id INTEGER NOT NULL DEFAULT 0 CHECK(checkpoint_log_id >= 0),
            pending_human_log_id INTEGER,
            pending_human_sent_at INTEGER,
            pending_human_name TEXT,
            pending_burst_json TEXT NOT NULL DEFAULT '[]',
            summary_dirty INTEGER NOT NULL DEFAULT 0 CHECK(summary_dirty IN (0, 1)),
            sync_status TEXT NOT NULL DEFAULT 'ready',
            updated_at TEXT NOT NULL,
            UNIQUE(account_fingerprint, chat_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_context_sources_authoritative_chat
            ON context_sources(chat) WHERE authoritative = 1;
        CREATE INDEX IF NOT EXISTS idx_context_sources_chat
            ON context_sources(chat, authoritative, updated_at);
        CREATE TABLE IF NOT EXISTS context_live_events(
            source TEXT NOT NULL,
            chat_id INTEGER NOT NULL CHECK(chat_id > 0),
            log_id INTEGER NOT NULL CHECK(log_id > 0),
            sent_at INTEGER NOT NULL,
            sender_name TEXT NOT NULL,
            message_digest TEXT NOT NULL,
            disposition TEXT NOT NULL,
            auto_generated INTEGER NOT NULL CHECK(auto_generated IN (0, 1)),
            context_message_id INTEGER,
            style_message_id INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY(source, chat_id, log_id),
            FOREIGN KEY(source) REFERENCES context_sources(source)
        );
        CREATE INDEX IF NOT EXISTS idx_context_live_events_context_message
            ON context_live_events(context_message_id);
        CREATE INDEX IF NOT EXISTS idx_context_live_events_style_message
            ON context_live_events(style_message_id);
        CREATE TABLE IF NOT EXISTS response_time_samples(
            source TEXT NOT NULL,
            chat_id INTEGER NOT NULL CHECK(chat_id > 0),
            reply_log_id INTEGER NOT NULL CHECK(reply_log_id > 0),
            prompt_log_id INTEGER NOT NULL CHECK(prompt_log_id > 0),
            recipient TEXT NOT NULL,
            delay_seconds REAL NOT NULL,
            PRIMARY KEY(source, chat_id, reply_log_id),
            FOREIGN KEY(source) REFERENCES context_sources(source)
        );
        CREATE INDEX IF NOT EXISTS idx_response_time_samples_source_chat
            ON response_time_samples(source, chat_id, reply_log_id);
        CREATE TABLE IF NOT EXISTS owner_recipient_style_samples(
            source TEXT NOT NULL,
            chat_id INTEGER NOT NULL CHECK(chat_id > 0),
            reply_log_id INTEGER NOT NULL CHECK(reply_log_id > 0),
            recipient TEXT NOT NULL,
            style_message_id INTEGER NOT NULL,
            burst_size INTEGER NOT NULL CHECK(burst_size > 0),
            confidence REAL NOT NULL CHECK(confidence > 0.0 AND confidence <= 1.0),
            PRIMARY KEY(source, chat_id, reply_log_id, recipient),
            FOREIGN KEY(source) REFERENCES context_sources(source)
        );
        CREATE INDEX IF NOT EXISTS idx_recipient_style_samples_recipient
            ON owner_recipient_style_samples(source, chat_id, recipient);
        CREATE INDEX IF NOT EXISTS idx_recipient_style_samples_lookup
            ON owner_recipient_style_samples(
                source, recipient, reply_log_id DESC, style_message_id DESC
            );
        CREATE TABLE IF NOT EXISTS owner_recipient_style_profile(
            source TEXT NOT NULL,
            chat_id INTEGER NOT NULL CHECK(chat_id > 0),
            chat TEXT NOT NULL,
            recipient TEXT NOT NULL,
            user_name TEXT NOT NULL CHECK(user_name = '최연우'),
            sample_count INTEGER NOT NULL,
            confidence_sum REAL NOT NULL,
            average_character_length REAL NOT NULL,
            median_character_length REAL NOT NULL,
            p90_character_length REAL NOT NULL,
            casual_ending_count INTEGER NOT NULL,
            casual_ending_counts_json TEXT NOT NULL DEFAULT '{}',
            question_count INTEGER NOT NULL,
            emoji_count INTEGER NOT NULL,
            punctuation_count INTEGER NOT NULL,
            common_endings_json TEXT NOT NULL DEFAULT '{}',
            common_tokens_json TEXT NOT NULL DEFAULT '{}',
            policy_version TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source, chat_id, recipient)
        );
        CREATE INDEX IF NOT EXISTS idx_recipient_style_profile_lookup
            ON owner_recipient_style_profile(chat, recipient, sample_count);
        CREATE TABLE IF NOT EXISTS context_message_topics(
            message_id INTEGER NOT NULL,
            topic TEXT NOT NULL CHECK(length(topic) > 0),
            PRIMARY KEY(message_id, topic)
        );
        CREATE INDEX IF NOT EXISTS idx_context_message_topics_topic
            ON context_message_topics(topic, message_id);
        CREATE TABLE IF NOT EXISTS context_topic_stats(
            chat TEXT NOT NULL,
            source TEXT NOT NULL,
            topic TEXT NOT NULL,
            message_count INTEGER NOT NULL CHECK(message_count >= 0),
            last_date TEXT NOT NULL,
            PRIMARY KEY(chat, source, topic)
        );
        CREATE TABLE IF NOT EXISTS context_reference_packs(
            id INTEGER PRIMARY KEY,
            pack_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            chat TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            start_log_id INTEGER NOT NULL,
            end_log_id INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            image_count INTEGER NOT NULL,
            quality_score INTEGER NOT NULL,
            topics TEXT NOT NULL DEFAULT '',
            what_text TEXT NOT NULL,
            how_text TEXT NOT NULL,
            why_text TEXT NOT NULL,
            body TEXT NOT NULL,
            vector BLOB NOT NULL,
            context_message_id INTEGER,
            policy_version TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_reference_packs_chat
            ON context_reference_packs(chat, quality_score DESC, end_log_id DESC);",
    )?;
    tx.execute(
        "INSERT OR IGNORE INTO context_retrieval_meta(key, value)
         VALUES ('live_context_schema', ?1)",
        [LIVE_CONTEXT_SCHEMA_VERSION],
    )?;
    let version: Option<String> = tx
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = 'live_context_schema'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    match version.as_deref() {
        Some("1") => {
            backfill_message_topics(&tx)?;
            tx.execute(
                "UPDATE context_retrieval_meta
                 SET value = ?1
                 WHERE key = 'live_context_schema'",
                [LIVE_CONTEXT_SCHEMA_VERSION],
            )?;
            ensure_topic_lexicon(&tx)?;
        }
        Some(value) if value == LIVE_CONTEXT_SCHEMA_VERSION => {
            ensure_topic_lexicon(&tx)?;
        }
        _ => anyhow::bail!("live context index migration required"),
    }
    tx.commit()?;
    Ok(())
}

pub(super) fn ensure_retrieval_index_current(conn: &Connection) -> Result<()> {
    let version = match conn
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = 'fts_schema'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()
    {
        Ok(version) => version,
        Err(error) if error.to_string().contains("no such table") => {
            anyhow::bail!("{CONTEXT_RETRIEVAL_MIGRATION_REQUIRED}");
        }
        Err(error) => return Err(error.into()),
    };
    if version.as_deref() != Some(RETRIEVAL_INDEX_SCHEMA_VERSION) {
        anyhow::bail!("{CONTEXT_RETRIEVAL_MIGRATION_REQUIRED}");
    }
    Ok(())
}

pub(super) fn rebuild_retrieval_index(conn: &Connection) -> Result<()> {
    conn.execute(
        "INSERT INTO context_messages_fts(context_messages_fts) VALUES ('rebuild')",
        [],
    )?;
    conn.execute(
        "INSERT INTO context_retrieval_meta(key, value) VALUES ('fts_schema', ?1)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [RETRIEVAL_INDEX_SCHEMA_VERSION],
    )?;
    Ok(())
}
