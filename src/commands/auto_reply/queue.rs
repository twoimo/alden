//! Read-only attestation that a stopped room's reply queue has the exact
//! schema and only terminal rows before a restart may resume it.

use std::path::Path;

use anyhow::{Context, Result};

use super::MAX_INT64;
use crate::auto_reply_runtime::validate_private_regular_file;

pub(super) const AUTO_REPLY_QUEUE_LEGACY_USER_VERSION: i64 = 0;
pub(super) const AUTO_REPLY_QUEUE_JOURNAL_USER_VERSION: i64 = 2;
pub(super) const AUTO_REPLY_QUEUE_JOURNAL_MAX_ROWS: i64 = 4096;

const AUTO_REPLY_REPLY_JOBS_TABLE_SQL: &str = r#"
CREATE TABLE reply_jobs(
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    status TEXT NOT NULL,
    due_at REAL,
    decision TEXT,
    reason TEXT,
    category TEXT,
    reply TEXT,
    scheduled_delay_seconds REAL,
    error_class TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"#;
pub(super) const AUTO_REPLY_REPLY_JOBS_V2_TABLE_SQL: &str = r#"
CREATE TABLE reply_jobs(
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    status TEXT NOT NULL,
    due_at REAL,
    decision TEXT,
    reason TEXT,
    category TEXT,
    reply TEXT,
    scheduled_delay_seconds REAL,
    error_class TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0 CHECK(attempt_no BETWEEN 0 AND 1000000)
);
"#;
pub(super) const AUTO_REPLY_REPLY_JOBS_STATUS_INDEX_SQL: &str =
    "CREATE INDEX idx_reply_jobs_status_due ON reply_jobs(status, due_at);";
const AUTO_REPLY_REPLY_JOB_TOMBSTONES_TABLE_SQL: &str = r#"
CREATE TABLE reply_job_tombstones(
    event_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    archived_at REAL NOT NULL
);
"#;
const AUTO_REPLY_REPLY_JOB_SUPERSESSIONS_TABLE_SQL: &str = r#"
CREATE TABLE reply_job_supersessions(
    event_id TEXT PRIMARY KEY,
    superseded_by_event_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    CHECK(event_id <> superseded_by_event_id)
);
"#;
const AUTO_REPLY_MODEL_CIRCUIT_BREAKER_TABLE_SQL: &str = r#"
CREATE TABLE model_circuit_breaker(
    model_key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    open_until REAL NOT NULL,
    lease_token TEXT,
    updated_at REAL NOT NULL
);
"#;

pub(super) const AUTO_REPLY_PIPELINE_TRANSITIONS_TABLE_SQL: &str = r#"
CREATE TABLE pipeline_transitions(
    seq INTEGER PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    event_id TEXT NOT NULL CHECK(
        length(event_id) BETWEEN 6 AND 80
        AND substr(event_id, 1, 3) = 'db:'
        AND instr(substr(event_id, 4), ':') BETWEEN 2 AND 20
        AND instr(
            substr(substr(event_id, 4), instr(substr(event_id, 4), ':') + 1),
            ':'
        ) = 0
        AND substr(event_id, 4, 1) BETWEEN '1' AND '9'
        AND substr(
            substr(event_id, 4),
            instr(substr(event_id, 4), ':') + 1,
            1
        ) BETWEEN '1' AND '9'
        AND substr(
            substr(event_id, 4),
            1,
            instr(substr(event_id, 4), ':') - 1
        ) NOT GLOB '*[^0-9]*'
        AND substr(
            substr(event_id, 4),
            instr(substr(event_id, 4), ':') + 1
        ) NOT GLOB '*[^0-9]*'
        AND CAST(
            substr(substr(event_id, 4), 1, instr(substr(event_id, 4), ':') - 1)
            AS INTEGER
        ) BETWEEN 1 AND 9223372036854775806
        AND CAST(
            substr(substr(event_id, 4), instr(substr(event_id, 4), ':') + 1)
            AS INTEGER
        ) BETWEEN 1 AND 9223372036854775806
    ),
    attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 0 AND 1000000),
    component TEXT NOT NULL CHECK(component IN ('authorization','ax','burst','context','delay','ingress','media','model','pre_send','projection','queue','recovery','terminal')),
    from_state TEXT NOT NULL CHECK(from_state IN ('acknowledging','deferred','delivery_unknown','detected','failed','hooking','idle','none','pending','poison','processing','projection_pending','ready','reconcile_required','scheduled','sending','sent','skipped')),
    to_state TEXT NOT NULL CHECK(to_state IN ('acknowledging','deferred','delivery_unknown','detected','failed','hooking','idle','none','pending','poison','processing','projection_pending','ready','reconcile_required','scheduled','sending','sent','skipped')),
    code TEXT NOT NULL CHECK(code IN ('authorization_allowed','authorization_rejected','ax_mutation_authorized','candidate_persisted','context_lookup','cursor_advance_persisting','cursor_advanced','custom_redacted','delay_scheduled','enqueued','hook_ack_received','hook_dispatch_intent','local_db_confirmed','media_acquire_failed','media_acquire_ready','media_acquire_started','media_policy_rejected','model_call','model_result','pre_send_check','projection_written','reconciled','recovery_completed','recovery_started','status_changed','terminal_committed')),
    source_epoch INTEGER CHECK(source_epoch IS NULL OR source_epoch BETWEEN 1 AND 9223372036854775806),
    occurred_at_ns INTEGER NOT NULL CHECK(occurred_at_ns BETWEEN 1 AND 9223372036854775806)
);
"#;

pub(super) const AUTO_REPLY_PIPELINE_TRANSITIONS_INDEX_SQL: &str =
    "CREATE INDEX idx_pipeline_transitions_event_seq ON pipeline_transitions(event_id, seq);";

pub(super) const AUTO_REPLY_PIPELINE_TRANSITIONS_INSERT_TRIGGER_SQL: &str = r#"
CREATE TRIGGER trg_reply_jobs_transition_insert
AFTER INSERT ON reply_jobs
BEGIN
  INSERT INTO pipeline_transitions(
    schema_version,event_id,attempt_no,component,from_state,to_state,
    code,source_epoch,occurred_at_ns
  ) VALUES(
    1,NEW.event_id,NEW.attempt_no,'queue','none',NEW.status,
    'enqueued',CASE WHEN json_valid(NEW.event_json) AND json_type(NEW.event_json, '$.source_epoch') = 'integer' AND json_extract(NEW.event_json, '$.source_epoch') BETWEEN 1 AND 9223372036854775806 THEN json_extract(NEW.event_json, '$.source_epoch') ELSE NULL END,CAST((julianday('now') - 2440587.5) * 86400000000000 AS INTEGER)
  );
END;
"#;

pub(super) const AUTO_REPLY_PIPELINE_TRANSITIONS_UPDATE_TRIGGER_SQL: &str = r#"
CREATE TRIGGER trg_reply_jobs_transition_update
AFTER UPDATE OF status ON reply_jobs
WHEN OLD.status IS NOT NEW.status
BEGIN
  UPDATE reply_jobs
  SET attempt_no = OLD.attempt_no + 1
  WHERE event_id = NEW.event_id AND NEW.status = 'processing';
  INSERT INTO pipeline_transitions(
    schema_version,event_id,attempt_no,component,from_state,to_state,
    code,source_epoch,occurred_at_ns
  ) VALUES(
    1,NEW.event_id,
    CASE WHEN NEW.status = 'processing' THEN OLD.attempt_no + 1
      ELSE NEW.attempt_no END,
    'queue',OLD.status,NEW.status,'status_changed',
    CASE WHEN json_valid(NEW.event_json) AND json_type(NEW.event_json, '$.source_epoch') = 'integer' AND json_extract(NEW.event_json, '$.source_epoch') BETWEEN 1 AND 9223372036854775806 THEN json_extract(NEW.event_json, '$.source_epoch') ELSE NULL END,CAST((julianday('now') - 2440587.5) * 86400000000000 AS INTEGER)
  );
END;
"#;

pub(super) const AUTO_REPLY_PIPELINE_TRANSITIONS_CAP_TRIGGER_SQL: &str = r#"
CREATE TRIGGER trg_pipeline_transitions_cap
AFTER INSERT ON pipeline_transitions
BEGIN
  DELETE FROM pipeline_transitions
  WHERE seq < COALESCE(
    (SELECT seq FROM pipeline_transitions ORDER BY seq DESC
     LIMIT 1 OFFSET 4095),
    0
  );
END;
"#;

/// Canonicalize SQLite schema text without weakening quoted literals. SQLite
/// preserves the submitted DDL in `sqlite_master`, so comparing normalized
/// bodies detects trigger/check/index tampering while ignoring formatting and
/// SQL-keyword case only.
fn normalize_sqlite_schema_sql(sql: &str) -> String {
    let mut normalized = String::with_capacity(sql.len());
    let mut quote = None;
    let mut pending_space = false;
    let mut characters = sql.chars().peekable();
    while let Some(character) = characters.next() {
        if let Some(terminator) = quote {
            normalized.push(character);
            if character == terminator {
                if matches!(terminator, '\'' | '"' | '`') && characters.peek() == Some(&terminator)
                {
                    normalized.push(characters.next().expect("peeked quoted character"));
                } else {
                    quote = None;
                }
            }
            continue;
        }
        match character {
            '\'' | '"' | '`' => {
                if pending_space
                    && !normalized.is_empty()
                    && !normalized.ends_with(['(', ',', '=', '<', '>', ';', '+', '*'])
                {
                    normalized.push(' ');
                }
                pending_space = false;
                quote = Some(character);
                normalized.push(character);
            }
            '[' => {
                if pending_space
                    && !normalized.is_empty()
                    && !normalized.ends_with(['(', ',', '=', '<', '>', ';', '+', '*'])
                {
                    normalized.push(' ');
                }
                pending_space = false;
                quote = Some(']');
                normalized.push(character);
            }
            value if value.is_ascii_whitespace() => pending_space = true,
            value if matches!(value, '(' | ')' | ',' | '=' | '<' | '>' | ';' | '+' | '*') => {
                while normalized.ends_with(' ') {
                    normalized.pop();
                }
                normalized.push(value);
                pending_space = false;
            }
            value => {
                if pending_space
                    && !normalized.is_empty()
                    && !normalized.ends_with(['(', ',', '=', '<', '>', ';', '+', '*'])
                {
                    normalized.push(' ');
                }
                pending_space = false;
                normalized.push(value.to_ascii_lowercase());
            }
        }
    }
    while normalized.ends_with(';') {
        normalized.pop();
    }
    normalized
}

pub(super) fn parse_auto_reply_transition_event_id(value: &str) -> Option<(i64, i64)> {
    if !(6..=80).contains(&value.len()) || !value.is_ascii() {
        return None;
    }
    let mut fields = value.split(':');
    if fields.next() != Some("db") {
        return None;
    }
    let chat_id = fields.next()?;
    let log_id = fields.next()?;
    if fields.next().is_some() {
        return None;
    }
    let parse_field = |field: &str| {
        (!field.is_empty()
            && field.len() <= 19
            && field.as_bytes()[0].is_ascii_digit()
            && field.as_bytes()[0] != b'0'
            && field.bytes().all(|byte| byte.is_ascii_digit())
            && field
                .parse::<i64>()
                .ok()
                .is_some_and(|number| (1..i64::MAX).contains(&number)))
        .then(|| field.parse::<i64>().expect("validated decimal i64"))
    };
    Some((parse_field(chat_id)?, parse_field(log_id)?))
}

pub(super) fn validate_stopped_clean_queue(queue_path: &Path, expected_chat_id: i64) -> Result<()> {
    const MAX_QUEUE_BYTES: u64 = 64 * 1024 * 1024;
    let expected_room_name = expected_chat_id.to_string();
    if !(1..i64::MAX).contains(&expected_chat_id)
        || queue_path
            .parent()
            .and_then(Path::file_name)
            .and_then(|value| value.to_str())
            != Some(expected_room_name.as_str())
    {
        anyhow::bail!("AutoReply stopped-clean queue room binding is invalid");
    }
    validate_private_regular_file(queue_path, MAX_QUEUE_BYTES)?;
    let connection = rusqlite::Connection::open_with_flags(
        queue_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| format!("open stopped-clean queue {}", queue_path.display()))?;
    connection.execute_batch("PRAGMA query_only = ON; PRAGMA busy_timeout = 5000;")?;
    let quick_check: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
    if quick_check != "ok" {
        anyhow::bail!("AutoReply stopped-clean queue quick_check failed");
    }
    let user_version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    let has_transition_journal = match user_version {
        AUTO_REPLY_QUEUE_LEGACY_USER_VERSION => false,
        AUTO_REPLY_QUEUE_JOURNAL_USER_VERSION => true,
        _ => anyhow::bail!("AutoReply stopped-clean queue version is unsupported"),
    };
    let tables = connection
        .prepare(
            "SELECT name FROM sqlite_master \
             WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )?
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let legacy_tables = vec![
        "reply_job_supersessions".to_string(),
        "reply_job_tombstones".to_string(),
        "reply_jobs".to_string(),
    ];
    let mut legacy_tables_with_breaker = legacy_tables.clone();
    legacy_tables_with_breaker.insert(0, "model_circuit_breaker".to_string());
    let mut expected_tables = legacy_tables.clone();
    let mut expected_tables_with_breaker = legacy_tables_with_breaker.clone();
    if has_transition_journal {
        expected_tables.insert(0, "pipeline_transitions".to_string());
        expected_tables_with_breaker.insert(1, "pipeline_transitions".to_string());
    }
    let has_model_circuit_breaker = if tables == expected_tables {
        false
    } else if tables == expected_tables_with_breaker {
        true
    } else {
        anyhow::bail!("AutoReply stopped-clean queue schema is invalid");
    };
    let validate_columns = |table: &str, expected: &[(&str, &str, i64, i64)]| -> Result<()> {
        let sql = format!("PRAGMA table_info({table})");
        let actual = connection
            .prepare(&sql)?
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?.to_ascii_uppercase(),
                    row.get::<_, i64>(3)?,
                    row.get::<_, i64>(5)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let expected = expected
            .iter()
            .map(|(name, kind, not_null, primary_key)| {
                (
                    (*name).to_string(),
                    (*kind).to_string(),
                    *not_null,
                    *primary_key,
                )
            })
            .collect::<Vec<_>>();
        if actual != expected {
            anyhow::bail!("AutoReply stopped-clean queue schema is invalid");
        }
        Ok(())
    };
    let legacy_reply_job_columns = [
        ("event_id", "TEXT", 0, 1),
        ("event_json", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("due_at", "REAL", 0, 0),
        ("decision", "TEXT", 0, 0),
        ("reason", "TEXT", 0, 0),
        ("category", "TEXT", 0, 0),
        ("reply", "TEXT", 0, 0),
        ("scheduled_delay_seconds", "REAL", 0, 0),
        ("error_class", "TEXT", 0, 0),
        ("created_at", "REAL", 1, 0),
        ("updated_at", "REAL", 1, 0),
    ];
    let mut v2_reply_job_columns = legacy_reply_job_columns.to_vec();
    v2_reply_job_columns.push(("attempt_no", "INTEGER", 1, 0));
    validate_columns(
        "reply_jobs",
        if has_transition_journal {
            &v2_reply_job_columns
        } else {
            &legacy_reply_job_columns
        },
    )?;
    validate_columns(
        "reply_job_tombstones",
        &[
            ("event_id", "TEXT", 0, 1),
            ("status", "TEXT", 1, 0),
            ("archived_at", "REAL", 1, 0),
        ],
    )?;
    validate_columns(
        "reply_job_supersessions",
        &[
            ("event_id", "TEXT", 0, 1),
            ("superseded_by_event_id", "TEXT", 1, 0),
            ("created_at", "REAL", 1, 0),
        ],
    )?;
    if has_model_circuit_breaker {
        validate_columns(
            "model_circuit_breaker",
            &[
                ("model_key", "TEXT", 0, 1),
                ("state", "TEXT", 1, 0),
                ("failure_class", "TEXT", 1, 0),
                ("consecutive_failures", "INTEGER", 1, 0),
                ("open_until", "REAL", 1, 0),
                ("lease_token", "TEXT", 0, 0),
                ("updated_at", "REAL", 1, 0),
            ],
        )?;
    }
    if has_transition_journal {
        validate_columns(
            "pipeline_transitions",
            &[
                ("seq", "INTEGER", 0, 1),
                ("schema_version", "INTEGER", 1, 0),
                ("event_id", "TEXT", 1, 0),
                ("attempt_no", "INTEGER", 1, 0),
                ("component", "TEXT", 1, 0),
                ("from_state", "TEXT", 1, 0),
                ("to_state", "TEXT", 1, 0),
                ("code", "TEXT", 1, 0),
                ("source_epoch", "INTEGER", 0, 0),
                ("occurred_at_ns", "INTEGER", 1, 0),
            ],
        )?;
        let table_sql: String = connection.query_row(
            "SELECT sql FROM sqlite_master \
             WHERE type = 'table' AND name = 'pipeline_transitions'",
            [],
            |row| row.get(0),
        )?;
        if normalize_sqlite_schema_sql(&table_sql)
            != normalize_sqlite_schema_sql(AUTO_REPLY_PIPELINE_TRANSITIONS_TABLE_SQL)
        {
            anyhow::bail!("AutoReply stopped-clean journal table is invalid");
        }
    }

    let schema_objects = connection
        .prepare(
            "SELECT type, name, sql FROM sqlite_master \
             WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL \
             ORDER BY type, name",
        )?
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut expected_schema_objects = vec![
        (
            "index",
            "idx_reply_jobs_status_due",
            AUTO_REPLY_REPLY_JOBS_STATUS_INDEX_SQL,
        ),
        (
            "table",
            "reply_job_supersessions",
            AUTO_REPLY_REPLY_JOB_SUPERSESSIONS_TABLE_SQL,
        ),
        (
            "table",
            "reply_job_tombstones",
            AUTO_REPLY_REPLY_JOB_TOMBSTONES_TABLE_SQL,
        ),
        (
            "table",
            "reply_jobs",
            if has_transition_journal {
                AUTO_REPLY_REPLY_JOBS_V2_TABLE_SQL
            } else {
                AUTO_REPLY_REPLY_JOBS_TABLE_SQL
            },
        ),
    ];
    if has_model_circuit_breaker {
        expected_schema_objects.insert(
            1,
            (
                "table",
                "model_circuit_breaker",
                AUTO_REPLY_MODEL_CIRCUIT_BREAKER_TABLE_SQL,
            ),
        );
    }
    if has_transition_journal {
        expected_schema_objects.extend([
            (
                "index",
                "idx_pipeline_transitions_event_seq",
                AUTO_REPLY_PIPELINE_TRANSITIONS_INDEX_SQL,
            ),
            (
                "table",
                "pipeline_transitions",
                AUTO_REPLY_PIPELINE_TRANSITIONS_TABLE_SQL,
            ),
            (
                "trigger",
                "trg_pipeline_transitions_cap",
                AUTO_REPLY_PIPELINE_TRANSITIONS_CAP_TRIGGER_SQL,
            ),
            (
                "trigger",
                "trg_reply_jobs_transition_insert",
                AUTO_REPLY_PIPELINE_TRANSITIONS_INSERT_TRIGGER_SQL,
            ),
            (
                "trigger",
                "trg_reply_jobs_transition_update",
                AUTO_REPLY_PIPELINE_TRANSITIONS_UPDATE_TRIGGER_SQL,
            ),
        ]);
    }
    expected_schema_objects.sort_by_key(|(kind, name, _)| (*kind, *name));
    if schema_objects.len() != expected_schema_objects.len()
        || schema_objects.iter().zip(&expected_schema_objects).any(
            |((kind, name, sql), (expected_kind, expected_name, expected_sql))| {
                kind != expected_kind
                    || name != expected_name
                    || normalize_sqlite_schema_sql(sql) != normalize_sqlite_schema_sql(expected_sql)
            },
        )
    {
        anyhow::bail!("AutoReply stopped-clean queue schema SQL is invalid");
    }

    let expected_index_names = if has_transition_journal {
        vec![
            "idx_pipeline_transitions_event_seq",
            "idx_reply_jobs_status_due",
        ]
    } else {
        vec!["idx_reply_jobs_status_due"]
    };
    let index_names = schema_objects
        .iter()
        .filter(|(kind, _, _)| kind == "index")
        .map(|(_, name, _)| name.as_str())
        .collect::<Vec<_>>();
    if index_names != expected_index_names {
        anyhow::bail!("AutoReply stopped-clean queue index set is invalid");
    }
    let status_index_columns = connection
        .prepare("PRAGMA index_info(idx_reply_jobs_status_due)")?
        .query_map([], |row| row.get::<_, String>(2))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if status_index_columns != ["status", "due_at"] {
        anyhow::bail!("AutoReply stopped-clean queue index is invalid");
    }
    if has_transition_journal {
        let journal_index_columns = connection
            .prepare("PRAGMA index_info(idx_pipeline_transitions_event_seq)")?
            .query_map([], |row| row.get::<_, String>(2))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        if journal_index_columns != ["event_id", "seq"] {
            anyhow::bail!("AutoReply stopped-clean journal index is invalid");
        }
    }

    let triggers = connection
        .prepare(
            "SELECT name, tbl_name, sql FROM sqlite_master \
             WHERE type = 'trigger' ORDER BY name",
        )?
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, Option<String>>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if has_transition_journal {
        let expected_triggers = [
            (
                "trg_pipeline_transitions_cap",
                "pipeline_transitions",
                AUTO_REPLY_PIPELINE_TRANSITIONS_CAP_TRIGGER_SQL,
            ),
            (
                "trg_reply_jobs_transition_insert",
                "reply_jobs",
                AUTO_REPLY_PIPELINE_TRANSITIONS_INSERT_TRIGGER_SQL,
            ),
            (
                "trg_reply_jobs_transition_update",
                "reply_jobs",
                AUTO_REPLY_PIPELINE_TRANSITIONS_UPDATE_TRIGGER_SQL,
            ),
        ];
        if triggers.len() != expected_triggers.len()
            || triggers.iter().zip(expected_triggers).any(
                |((name, table, sql), (expected_name, expected_table, expected_sql))| {
                    name != expected_name
                        || table != expected_table
                        || sql.as_deref().map(normalize_sqlite_schema_sql)
                            != Some(normalize_sqlite_schema_sql(expected_sql))
                },
            )
        {
            anyhow::bail!("AutoReply stopped-clean journal triggers are invalid");
        }
        let journal_rows: i64 =
            connection.query_row("SELECT COUNT(*) FROM pipeline_transitions", [], |row| {
                row.get(0)
            })?;
        if !(0..=AUTO_REPLY_QUEUE_JOURNAL_MAX_ROWS).contains(&journal_rows) {
            anyhow::bail!("AutoReply stopped-clean journal row cap is invalid");
        }
        let journal_event_ids = connection
            .prepare("SELECT event_id FROM pipeline_transitions ORDER BY seq")?
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        if journal_event_ids.iter().any(|event_id| {
            parse_auto_reply_transition_event_id(event_id)
                .is_none_or(|(chat_id, _)| chat_id != expected_chat_id)
        }) {
            anyhow::bail!("AutoReply stopped-clean journal identity is invalid");
        }
        let invalid_journal_rows: i64 = connection.query_row(
            "SELECT COUNT(*) FROM pipeline_transitions WHERE \
             typeof(schema_version) != 'integer' OR schema_version != 1 OR \
             typeof(attempt_no) != 'integer' OR attempt_no NOT BETWEEN 0 AND 1000000 OR \
             component NOT IN ('authorization','ax','burst','context','delay','ingress','media','model','pre_send','projection','queue','recovery','terminal') OR \
             from_state NOT IN ('acknowledging','deferred','delivery_unknown','detected','failed','hooking','idle','none','pending','poison','processing','projection_pending','ready','reconcile_required','scheduled','sending','sent','skipped') OR \
             to_state NOT IN ('acknowledging','deferred','delivery_unknown','detected','failed','hooking','idle','none','pending','poison','processing','projection_pending','ready','reconcile_required','scheduled','sending','sent','skipped') OR \
             code NOT IN ('authorization_allowed','authorization_rejected','ax_mutation_authorized','candidate_persisted','context_lookup','cursor_advance_persisting','cursor_advanced','custom_redacted','delay_scheduled','enqueued','hook_ack_received','hook_dispatch_intent','local_db_confirmed','media_acquire_failed','media_acquire_ready','media_acquire_started','media_policy_rejected','model_call','model_result','pre_send_check','projection_written','reconciled','recovery_completed','recovery_started','status_changed','terminal_committed') OR \
             (source_epoch IS NOT NULL AND (typeof(source_epoch) != 'integer' OR source_epoch NOT BETWEEN 1 AND 9223372036854775806)) OR \
             typeof(occurred_at_ns) != 'integer' OR occurred_at_ns NOT BETWEEN 1 AND 9223372036854775806",
            [],
            |row| row.get(0),
        )?;
        if invalid_journal_rows != 0 {
            anyhow::bail!("AutoReply stopped-clean journal metadata is invalid");
        }
    } else if !triggers.is_empty() {
        anyhow::bail!("AutoReply stopped-clean legacy queue has triggers");
    }

    let reply_identities = connection
        .prepare("SELECT event_id,event_json FROM reply_jobs ORDER BY event_id")?
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (event_id, event_json) in reply_identities {
        let Some((chat_id, log_id)) = parse_auto_reply_transition_event_id(&event_id) else {
            anyhow::bail!("AutoReply stopped-clean queue event identity is invalid");
        };
        if chat_id != expected_chat_id {
            anyhow::bail!("AutoReply stopped-clean queue event belongs to another room");
        }
        let event: serde_json::Value = serde_json::from_str(&event_json)
            .context("parse stopped-clean queue event identity")?;
        let event = event
            .as_object()
            .context("AutoReply stopped-clean queue event identity is malformed")?;
        for key in ["event_id", "canonical_event_id"] {
            if event
                .get(key)
                .is_some_and(|value| value.as_str() != Some(event_id.as_str()))
            {
                anyhow::bail!("AutoReply stopped-clean queue event identity mismatches");
            }
        }
        let event_log_id = event.get("log_id").and_then(serde_json::Value::as_i64);
        let proactive = event.get("proactive") == Some(&serde_json::Value::Bool(true));
        if event
            .get("chat_id")
            .is_some_and(|value| value.as_i64() != Some(expected_chat_id))
            || if proactive {
                event_log_id.is_some_and(|value| !(1..MAX_INT64).contains(&value))
            } else {
                event_log_id.is_some_and(|value| value != log_id)
            }
        {
            anyhow::bail!("AutoReply stopped-clean queue JSON identity mismatches");
        }
    }
    let tombstone_identities = connection
        .prepare("SELECT event_id FROM reply_job_tombstones ORDER BY event_id")?
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if tombstone_identities.iter().any(|event_id| {
        parse_auto_reply_transition_event_id(event_id)
            .is_none_or(|(chat_id, _)| chat_id != expected_chat_id)
    }) {
        anyhow::bail!("AutoReply stopped-clean queue tombstone identity is invalid");
    }
    let supersession_identities = connection
        .prepare(
            "SELECT event_id,superseded_by_event_id \
             FROM reply_job_supersessions ORDER BY event_id",
        )?
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if supersession_identities
        .iter()
        .any(|(event_id, successor_id)| {
            [event_id, successor_id].into_iter().any(|value| {
                parse_auto_reply_transition_event_id(value)
                    .is_none_or(|(chat_id, _)| chat_id != expected_chat_id)
            })
        })
    {
        anyhow::bail!("AutoReply stopped-clean queue supersession identity is invalid");
    }
    if has_transition_journal {
        let invalid_attempts: i64 = connection.query_row(
            "SELECT COUNT(*) FROM reply_jobs WHERE \
             typeof(attempt_no) != 'integer' OR attempt_no NOT BETWEEN 0 AND 1000000",
            [],
            |row| row.get(0),
        )?;
        if invalid_attempts != 0 {
            anyhow::bail!("AutoReply stopped-clean queue attempt authority is invalid");
        }
    }

    for table in ["reply_jobs", "reply_job_tombstones"] {
        let sql = format!(
            "SELECT COUNT(*) FROM {table} \
             WHERE status IS NULL OR status NOT IN ('sent', 'skipped')"
        );
        let nonterminal: i64 = connection.query_row(&sql, [], |row| row.get(0))?;
        if nonterminal != 0 {
            anyhow::bail!("AutoReply stopped-clean queue has nonterminal rows");
        }
    }
    Ok(())
}
