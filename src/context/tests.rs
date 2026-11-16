use super::embedding::{encode_vector, vector_to_bytes};
use super::live::{live_event_digest, live_index_text, LIVE_CONTEXT_MAX_FIELD_BYTES};
use super::reply_bundle::{
    live_event_exclusions, CONTEXT_REPLY_BUNDLE_CONTEXT_LIMIT, CONTEXT_REPLY_BUNDLE_DECISION_LIMIT,
    CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION, CONTEXT_REPLY_BUNDLE_STYLE_LIMIT,
    RECIPIENT_CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION,
};
use super::reply_decision::REPLY_DECISION_CANDIDATE_CAP;
use super::response_time::{
    fit_response_time_distribution, response_delay_percentile, response_time_distribution_is_valid,
    summarize_response_delays, MAX_RESPONSE_DELAY_SECONDS, MIN_SCHEDULED_RESPONSE_DELAY_SECONDS,
    RESPONSE_TIME_DISTRIBUTION_COMPONENTS, RESPONSE_TIME_DISTRIBUTION_FIT_TRANSFORM,
    RESPONSE_TIME_DISTRIBUTION_MIN_COMPONENT_SAMPLES, RESPONSE_TIME_DISTRIBUTION_MIN_SAMPLES,
    RESPONSE_TIME_DISTRIBUTION_MODEL_KIND, RESPONSE_TIME_DISTRIBUTION_POLICY_VERSION,
    RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION,
};
use super::schema::{
    rebuild_retrieval_index, CONTEXT_RETRIEVAL_MIGRATION_REQUIRED, LIVE_CONTEXT_SCHEMA_VERSION,
};
use super::search::{
    style_search_with_connection, CONTEXT_VECTOR_CANDIDATE_CAP, RECIPIENT_STYLE_SEARCH_SQL,
    STYLE_VECTOR_CANDIDATE_CAP,
};
use super::self_send::AUTO_GENERATED_MATCH_WINDOW_SECONDS;
use super::store::{
    checkpoint_context_wal_truncate, open_db, open_db_readonly, set_context_db_permissions,
    sqlite_sidecar_path,
};
use super::style::{classify_style_message, is_assistant_tell_style, STYLE_USER};
use super::topics::{classify_message_topics, message_has_interest_topic, TOPIC_LEXICON};
use super::*;
use anyhow::Result;
use chrono::Utc;
use rusqlite::{params, Connection};
use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use tempfile::tempdir;

fn is_conversational_style_message(message: &str) -> bool {
    classify_style_message(message).style_eligible
}

const TEST_ACCOUNT_FINGERPRINT: &str =
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

#[test]
fn context_database_wal_allows_readers_during_a_writer_transaction() -> Result<()> {
    let dir = tempdir()?;
    let db = dir.path().join("context-wal.sqlite3");
    let writer = open_db(&db)?;
    let journal_mode: String = writer.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
    assert_eq!(journal_mode.to_ascii_lowercase(), "wal");

    writer.execute_batch("BEGIN EXCLUSIVE")?;
    let reader = open_db_readonly(&db)?;
    let messages: i64 = reader.query_row("SELECT COUNT(*) FROM context_messages", [], |row| {
        row.get(0)
    })?;
    writer.execute_batch("ROLLBACK")?;
    assert_eq!(messages, 0);
    Ok(())
}

#[test]
fn context_wal_truncate_checkpoint_clears_deleted_message_pages() -> Result<()> {
    let dir = tempdir()?;
    let db = dir.path().join("context-wal-private.sqlite3");
    let conn = open_db(&db)?;
    let retired_message = "retired-message-marker-7d28b";
    conn.execute(
        "INSERT INTO context_messages(source, chat, date, user_name, message, vector)
             VALUES ('source', 'chat', '2026-09-24', 'user', ?1, ?2)",
        params![
            retired_message,
            vector_to_bytes(&encode_vector(retired_message))
        ],
    )?;
    checkpoint_context_wal_truncate(&conn)?;

    conn.execute(
        "DELETE FROM context_messages WHERE message = ?1",
        [retired_message],
    )?;
    checkpoint_context_wal_truncate(&conn)?;

    let wal = fs::read(sqlite_sidecar_path(&db, "-wal")).unwrap_or_default();
    let main_db = fs::read(&db)?;
    assert_eq!(wal.len(), 0);
    assert!(!wal
        .windows(retired_message.len())
        .any(|w| w == retired_message.as_bytes()));
    assert!(!main_db
        .windows(retired_message.len())
        .any(|w| w == retired_message.as_bytes()));
    Ok(())
}

#[cfg(unix)]
#[test]
fn context_database_and_wal_sidecars_use_private_permissions() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let dir = tempdir()?;
    let db = dir.path().join("permissions.sqlite3");
    let conn = open_db(&db)?;
    conn.execute(
        "INSERT INTO context_messages(source, chat, date, user_name, message, vector)
             VALUES ('source', 'chat', '2026-09-24', 'user', 'permission probe', ?1)",
        [vector_to_bytes(&encode_vector("permission probe"))],
    )?;
    set_context_db_permissions(&db)?;

    assert_eq!(
        fs::metadata(dir.path())?.permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(fs::metadata(&db)?.permissions().mode() & 0o777, 0o600);
    for suffix in ["-wal", "-shm"] {
        let sidecar = sqlite_sidecar_path(&db, suffix);
        if sidecar.exists() {
            assert_eq!(fs::metadata(sidecar)?.permissions().mode() & 0o777, 0o600);
        }
    }
    Ok(())
}

#[test]
fn context_reads_fail_closed_on_a_missing_database_without_creating_it() {
    let dir = tempdir().unwrap();
    let parent = dir.path().join("absent");
    let db = parent.join("context.sqlite3");
    let assert_migration_required = |label: &str, result: Result<()>| {
        assert_eq!(
            result.unwrap_err().to_string(),
            CONTEXT_RETRIEVAL_MIGRATION_REQUIRED,
            "{label}"
        );
    };

    assert_migration_required(
        "search",
        search(&db, Some("방"), None, "probe", "hybrid", 5).map(|_| ()),
    );
    assert_migration_required(
        "style_search",
        style_search(&db, Some("방"), "probe", 5).map(|_| ()),
    );
    assert_migration_required(
        "style_profile",
        style_profile(&db, "방", STYLE_USER, None).map(|_| ()),
    );
    assert_migration_required(
        "recipient_style_profile",
        recipient_style_profile(&db, "방", "민수", None).map(|_| ()),
    );
    assert_migration_required(
        "response_time_stats",
        response_time_stats(&db, "방", STYLE_USER, None).map(|_| ()),
    );
    assert_migration_required(
        "reply_decision_search",
        reply_decision_search(&db, "방", "probe", 5).map(|_| ()),
    );
    assert_migration_required(
        "get_reply_decision",
        get_reply_decision(&db, "db:7:42").map(|_| ()),
    );
    assert_migration_required(
        "live_context_sync_state",
        live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, 7).map(|_| ()),
    );
    assert_migration_required(
        "context_reply_bundle",
        context_reply_bundle(&db, "방", "probe", None).map(|_| ()),
    );
    assert_migration_required(
        "context_reply_bundle_for_recipient_excluding_live_events",
        context_reply_bundle_for_recipient_excluding_live_events(
            &db,
            "방",
            "probe",
            None,
            "민수",
            7,
            &[42],
        )
        .map(|_| ()),
    );

    assert!(
        !parent.exists(),
        "read paths must not create the context database or its directory"
    );
}

#[cfg(unix)]
#[test]
fn context_reads_refuse_a_symlinked_database() {
    let dir = tempdir().unwrap();
    let real = dir.path().join("real.sqlite3");
    let csv = fixture(dir.path(), "chat.csv", "symlink probe");
    index_csv(&real, "방", &csv).unwrap();
    let link = dir.path().join("link.sqlite3");
    std::os::unix::fs::symlink(&real, &link).unwrap();

    assert_eq!(
        search(&real, Some("방"), None, "symlink", "keyword", 5)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        search(&link, Some("방"), None, "symlink", "keyword", 5)
            .unwrap_err()
            .to_string(),
        CONTEXT_RETRIEVAL_MIGRATION_REQUIRED
    );
    assert_eq!(
        context_reply_bundle(&link, "방", "symlink", None)
            .unwrap_err()
            .to_string(),
        CONTEXT_RETRIEVAL_MIGRATION_REQUIRED
    );
}

#[test]
fn read_only_context_connection_rejects_writes() -> Result<()> {
    let dir = tempdir()?;
    let db = dir.path().join("readonly.sqlite3");
    let csv = fixture(dir.path(), "chat.csv", "read only probe");
    index_csv(&db, "방", &csv)?;

    let reader = open_db_readonly(&db)?;
    let write = reader.execute(
        "INSERT INTO context_messages(source, chat, date, user_name, message, vector)
             VALUES ('source', '방', '2026-01-01', '민수', 'write probe', x'')",
        [],
    );
    assert!(
        matches!(
            write,
            Err(rusqlite::Error::SqliteFailure(ref error, _))
                if error.code == rusqlite::ErrorCode::ReadOnly
        ),
        "{write:?}"
    );
    let count: i64 = reader.query_row("SELECT COUNT(*) FROM context_messages", [], |row| {
        row.get(0)
    })?;
    assert_eq!(count, 1);
    Ok(())
}

#[cfg(unix)]
#[test]
fn context_database_open_refuses_a_symlinked_wal_sidecar() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let dir = tempdir()?;
    let outside = dir.path().join("outside.bin");
    fs::write(&outside, b"not a wal")?;
    fs::set_permissions(&outside, fs::Permissions::from_mode(0o644))?;
    let db_dir = dir.path().join("ctx");
    fs::create_dir(&db_dir)?;
    let db = db_dir.join("context.sqlite3");
    std::os::unix::fs::symlink(&outside, db_dir.join("context.sqlite3-wal"))?;
    let csv = fixture(dir.path(), "chat.csv", "sidecar probe");

    assert!(index_csv(&db, "방", &csv).is_err());
    assert!(!db.exists());
    assert_eq!(fs::metadata(&outside)?.permissions().mode() & 0o777, 0o644);
    assert_eq!(fs::read(&outside)?, b"not a wal");
    Ok(())
}

#[test]
fn csv_reindex_truncates_the_wal_and_scrubs_replaced_text() -> Result<()> {
    let dir = tempdir()?;
    let db = dir.path().join("context.sqlite3");
    let retired = "retiredphrase7d28b";
    let csv = fixture(dir.path(), "chat.csv", retired);
    index_csv(&db, "방", &csv)?;

    let concurrent_reader = open_db_readonly(&db)?;
    let rows: i64 =
        concurrent_reader.query_row("SELECT COUNT(*) FROM context_messages", [], |row| {
            row.get(0)
        })?;
    assert_eq!(rows, 1);
    fs::write(&csv, "Date,User,Message\n2026-01-01,민수,replacement\n")?;
    index_csv(&db, "방", &csv)?;

    let wal = fs::read(dir.path().join("context.sqlite3-wal")).unwrap_or_default();
    assert_eq!(wal.len(), 0);
    let main_db = fs::read(&db)?;
    assert!(!main_db
        .windows(retired.len())
        .any(|window| window == retired.as_bytes()));
    drop(concurrent_reader);
    Ok(())
}

#[cfg(unix)]
#[test]
fn queue_loader_never_follows_a_symlink_or_creates_a_missing_queue() {
    let dir = tempdir().unwrap();
    let queue = dir.path().join("reply-queue.sqlite3");
    {
        let conn = Connection::open(&queue).unwrap();
        conn.execute_batch(
            "CREATE TABLE reply_jobs(event_id TEXT, status TEXT, reply TEXT, updated_at REAL);
                 INSERT INTO reply_jobs VALUES('a','sent','보낸 문장',1000.0);",
        )
        .unwrap();
    }
    let before = fs::read(&queue).unwrap();
    assert_eq!(confirmed_self_sends_from_queue(&queue).unwrap().len(), 1);
    assert_eq!(fs::read(&queue).unwrap(), before);

    let link = dir.path().join("linked-queue.sqlite3");
    std::os::unix::fs::symlink(&queue, &link).unwrap();
    assert!(confirmed_self_sends_from_queue(&link).unwrap().is_empty());

    let missing_parent = dir.path().join("missing");
    let absent = missing_parent.join("reply-queue.sqlite3");
    assert!(confirmed_self_sends_from_queue(&absent).unwrap().is_empty());
    assert!(!missing_parent.exists());
}

fn style_profile_with_stats(
    common_endings: &[(&str, usize)],
    emoji_count: usize,
    punctuation_count: usize,
    common_tokens: &[(&str, usize)],
) -> StyleProfile {
    let endings: BTreeMap<String, usize> = common_endings
        .iter()
        .map(|(k, v)| (k.to_string(), *v))
        .collect();
    let tokens: BTreeMap<String, usize> = common_tokens
        .iter()
        .map(|(k, v)| (k.to_string(), *v))
        .collect();
    let sample_count = common_endings.iter().map(|(_, v)| *v).sum::<usize>().max(1);
    StyleProfile {
        chat: "부자멘토멘티".to_string(),
        source: "local-db".to_string(),
        user: STYLE_USER.to_string(),
        sample_count,
        average_character_length: 10.0,
        median_character_length: 10.0,
        p90_character_length: 12.0,
        casual_ending_count: 0,
        casual_ending_counts_json: "{}".to_string(),
        question_count: 0,
        emoji_count,
        punctuation_count,
        common_endings_json: serde_json::to_string(&endings).unwrap(),
        common_tokens_json: serde_json::to_string(&tokens).unwrap(),
        policy_version: STYLE_POLICY_VERSION.to_string(),
    }
}

#[test]
fn derive_deferential_endings_are_formal_honorific() {
    let profile = style_profile_with_stats(&[("습니다", 8), ("합니다", 4), ("요", 2)], 0, 5, &[]);
    let (honorific, formality) = derive_style_profile_honorific_formality(&profile);
    assert_eq!(honorific, Honorific::Honorific);
    assert_eq!(formality, Formality::Formal);
}

#[test]
fn derive_polite_with_emoji_is_informal_honorific() {
    // 해요체 위주 + 이모지/축약이 섞이면 존댓말이지만 비격식체.
    let profile = style_profile_with_stats(&[("요", 10), ("죠", 3)], 6, 2, &[("ㅋㅋ", 4)]);
    let (honorific, formality) = derive_style_profile_honorific_formality(&profile);
    assert_eq!(honorific, Honorific::Honorific);
    assert_eq!(formality, Formality::Informal);
}

#[test]
fn derive_casual_endings_are_informal_casual() {
    let profile = style_profile_with_stats(
        &[("어", 9), ("야", 4), ("지", 3), ("요", 1)],
        1,
        0,
        &[("ㅋㅋㅋ", 5)],
    );
    let (honorific, formality) = derive_style_profile_honorific_formality(&profile);
    assert_eq!(honorific, Honorific::Casual);
    assert_eq!(formality, Formality::Informal);
}

#[test]
fn derive_no_ending_evidence_defaults_to_informal_casual() {
    let profile = style_profile_with_stats(&[], 0, 0, &[]);
    let (honorific, formality) = derive_style_profile_honorific_formality(&profile);
    assert_eq!(honorific, Honorific::Casual);
    assert_eq!(formality, Formality::Informal);
}

#[test]
fn derive_deferential_overwhelmed_by_emoji_is_informal() {
    // 합쇼체가 있어도 이모지·축약 신호가 우세하면 비격식체로 본다.
    let profile = style_profile_with_stats(&[("습니다", 2), ("요", 6)], 20, 1, &[("ㅋㅋㅋ", 10)]);
    let (honorific, formality) = derive_style_profile_honorific_formality(&profile);
    assert_eq!(honorific, Honorific::Honorific);
    assert_eq!(formality, Formality::Informal);
}

#[test]
fn contraction_token_detection() {
    assert!(is_contraction_token("ㅋㅋ"));
    assert!(is_contraction_token("ㅠㅠㅠ"));
    assert!(!is_contraction_token("회의"));
    assert!(!is_contraction_token(""));
}

fn fixture(dir: &Path, name: &str, message: &str) -> PathBuf {
    let path = dir.join(name);
    let mut file = fs::File::create(&path).unwrap();
    writeln!(file, "Date,User,Message\n2026-01-01,민수,{}", message).unwrap();
    path
}
fn insert_context_row(conn: &Connection, source: &str, chat: &str, date: &str, message: &str) {
    conn.execute(
        "INSERT INTO context_messages(
                source, chat, date, user_name, message, vector
             ) VALUES (?1, ?2, ?3, '민수', ?4, ?5)",
        params![
            source,
            chat,
            date,
            message,
            vector_to_bytes(&encode_vector(message)),
        ],
    )
    .unwrap();
}

fn live_event(
    chat_id: i64,
    log_id: i64,
    sender_name: &str,
    message: &str,
    sent_at: i64,
) -> LiveContextEvent {
    LiveContextEvent {
        chat_id,
        log_id,
        sender_name: sender_name.to_string(),
        message: message.to_string(),
        sent_at,
        is_self: sender_name == STYLE_USER,
        exclude_from_learning: false,
        auto_generated: false,
        attachment: String::new(),
        message_type: 1,
        interest_only: false,
    }
}

fn record_sent_reply(db: &Path, event_id: &str, chat: &str, reply: &str, sent_at: &str) {
    let record = serde_json::json!({
        "event_id": event_id,
        "chat": chat,
        "author": "민수",
        "received_at": "2026-01-01T00:00:00Z",
        "message": format!("{event_id} incoming"),
        "decision": "reply",
        "reason": "direct_question",
        "category": "question",
        "context_match_count": 1,
        "style_match_count": 1,
        "best_context_score": 0.5,
        "best_style_score": 0.5,
        "prior_similarity": 0.0,
        "scheduled_delay_seconds": 1.0,
        "status": "scheduled",
        "reply": reply,
        "evidence_ids": ["context:test"],
        "style_policy_version": STYLE_POLICY_VERSION,
    });
    assert!(record_reply_decision(db, &record.to_string()).unwrap());
    assert!(update_reply_decision(db, event_id, "sent", Some(reply), Some(sent_at)).unwrap());
}

#[test]
fn conversational_style_filter_excludes_pasted_information() {
    assert!(is_conversational_style_message("ㅋㅋㅋ 이건 좀 세긴 하네"));
    assert!(!is_conversational_style_message("ㅋㅋ 이건 좀 세긴 하네"));
    assert!(!is_conversational_style_message("ㅎㅎㅎ 이건 좀 세긴 하네"));
    assert!(!is_conversational_style_message(
        "1) 핵심 내용\n2) 세부 내용\n3) 참고 자료"
    ));
    assert!(!is_conversational_style_message(
        "긴 정보 전달문 https://example.com 자세한 내용이 이어집니다"
    ));
    assert!(!is_conversational_style_message(
        "이 문장은 실제 대화보다 정보 전달에 가까운 긴 문장이라서 말투 학습에서 제외해야 합니다."
    ));
    assert!(!is_conversational_style_message("1차 실무 면접"));
    assert!(!is_conversational_style_message("사진"));
    assert!(!is_conversational_style_message("@뉴스봇 환율"));
    assert!(!is_conversational_style_message(
        "문승현님이 부방장이 되었습니다."
    ));
    assert!(is_conversational_style_message("ㅇㅇ 저장해둘게"));
    assert!(is_conversational_style_message(
        "사용량 많으면 플러스가 낫긴 하지"
    ));
    assert!(!is_conversational_style_message("ㅇㅇ"));
    assert!(!is_conversational_style_message("ㅇㅇㅋㅋㅋㅋ"));
    assert!(!is_conversational_style_message(
        "응, 사진 메시지도 확인해서 맥락에 맞게 답할 수 있어!"
    ));
    assert!(!is_conversational_style_message("대만 여행 알아보나 보네"));
    assert!(!is_conversational_style_message(
        "재정 관리 빡세게 하나보네"
    ));
    assert!(is_conversational_style_message(
        "사용량 많으면 플러스가 낫긴 하지"
    ));
}
#[test]
fn topic_lexicon_ascii_needles_are_lowercase() {
    // `classify_message_topics` matches ASCII needles against the already
    // lowercased haystack without re-lowercasing them (saves one String per
    // needle per message). That shortcut is only correct while every ASCII
    // needle is stored lowercase; this guards future lexicon edits.
    for (topic, needles) in TOPIC_LEXICON {
        for needle in *needles {
            if needle.bytes().all(|byte| byte.is_ascii()) {
                assert_eq!(
                    *needle,
                    needle.to_ascii_lowercase(),
                    "topic `{topic}` has a non-lowercase ASCII needle `{needle}`"
                );
            }
        }
    }
}

#[test]
fn assistant_tell_style_matches_case_insensitively() {
    assert!(is_assistant_tell_style("I CAN HELP with that"));
    assert!(is_assistant_tell_style("도와드릴 수 있어요"));
    assert!(!is_assistant_tell_style("오늘 저녁에 뭐 해?"));
}

#[test]
fn classify_message_topics_assigns_contact_and_ax() {
    assert_eq!(
        classify_message_topics("연락처 저장함 010-1234-5678"),
        vec!["contact"]
    );
    assert!(classify_message_topics("어제 번호 이야기해줫던거 머더라").contains(&"contact"));
    assert!(classify_message_topics("AX API가 손쉬운 사용으로 카톡 조작함").contains(&"ax_macos"));
    assert!(classify_message_topics("토큰 부자ㄷㄷ").contains(&"llm_tools"));
    assert!(classify_message_topics("ㅋㅋㅋ").is_empty());
    assert!(classify_message_topics("코스피 배당주 투자").contains(&"stocks"));
    assert!(classify_message_topics("업비트 비트코인").contains(&"coins"));
    assert!(classify_message_topics("전세 부동산 경매").contains(&"real_estate"));
    assert!(classify_message_topics("전세 부동산 경매").contains(&"auction"));
    assert!(classify_message_topics("그록 인공지능").contains(&"ai"));
    assert!(message_has_interest_topic("공모주 넣었어요"));
    assert!(!message_has_interest_topic("ㅋㅋㅋ"));
    let indexed = live_index_text(
        "사진",
        r#"{"urls":["https://grok.com/supergrok"],"src_message":"67% 슈퍼그록"}"#,
        2,
    );
    assert!(indexed.contains("https://grok.com/supergrok"));
    assert!(indexed.contains("슈퍼그록"));
    assert!(message_has_interest_topic(&indexed));
}

#[test]
fn live_index_text_caps_korean_text_at_a_character_boundary() {
    let message = format!("{}ab", "가".repeat((LIVE_CONTEXT_MAX_FIELD_BYTES - 2) / 3));
    let joined = format!("{message}\n사진");
    assert!(message.len() <= LIVE_CONTEXT_MAX_FIELD_BYTES);
    assert!(!joined.is_char_boundary(LIVE_CONTEXT_MAX_FIELD_BYTES));

    assert_eq!(live_index_text(&message, "", 2), format!("{message}\n"));
}

#[test]
fn interest_only_indexes_investing_and_skips_chatter() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("interest-only.sqlite3");
    let chat_id = 415878504092105;
    let chat = "변우중";
    let mut keep = live_event(chat_id, 11, "변우중", "비트코인 지금 들어가도 됨?", 100);
    keep.interest_only = true;
    let mut skip = live_event(chat_id, 12, "변우중", "ㅋㅋㅋ", 110);
    skip.interest_only = true;
    let mut photo = live_event(chat_id, 13, "변우중", "사진", 120);
    photo.interest_only = true;
    photo.message_type = 2;
    photo.attachment = r#"{"src_message":"강남 아파트 경매 보셈"}"#.into();
    ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &[keep, skip, photo],
        true,
        false,
    )
    .unwrap();
    let conn = open_db(&db).unwrap();
    let messages: Vec<String> = conn
        .prepare("SELECT message FROM context_messages ORDER BY id")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<rusqlite::Result<_>>()
        .unwrap();
    assert_eq!(messages.len(), 2);
    assert!(messages.iter().any(|row| row.contains("비트코인")));
    assert!(messages.iter().any(|row| row.contains("경매")));
    assert!(!messages.iter().any(|row| row == "ㅋㅋㅋ"));
    let topics: Vec<String> = conn
        .prepare("SELECT DISTINCT topic FROM context_message_topics ORDER BY topic")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<rusqlite::Result<_>>()
        .unwrap();
    assert!(topics.iter().any(|topic| topic == "coins"));
}

#[test]
fn topic_overlay_recalls_contact_thread_without_shared_keyword() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("topics.sqlite3");
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
2026-01-01 10:00:00,문승현,연락처 저장함 010-1234-5678\n\
2026-01-01 10:00:10,최연우,ㅇㅇ 저장해둘게\n\
2026-01-01 12:00:00,문승현,AX API가 손쉬운 사용으로 카톡 조작함\n\
2026-01-01 12:00:05,최연우,컴퓨터 유즈보다 AX가 나음\n\
2026-01-01 13:00:00,문승현,토큰 부자ㄷㄷ\n",
    )
    .unwrap();
    assert_eq!(index_csv(&db, "부자멘토멘티", &path).unwrap(), 5);

    let recalled = search(
        &db,
        Some("부자멘토멘티"),
        None,
        "어제 번호 이야기해줫던거 머더라",
        "hybrid",
        5,
    )
    .unwrap();
    assert!(
        recalled
            .iter()
            .any(|row| row.message.contains("010-") || row.message.contains("연락처")),
        "{recalled:?}"
    );

    let ax = search(
        &db,
        Some("부자멘토멘티"),
        None,
        "AX API 손쉬운 사용",
        "hybrid",
        5,
    )
    .unwrap();
    assert!(
        ax.iter()
            .any(|row| row.message.contains("손쉬운 사용") || row.message.contains("AX")),
        "{ax:?}"
    );
    assert!(!ax.iter().any(|row| row.message.contains("010-")), "{ax:?}");

    let conn = open_db(&db).unwrap();
    conn.execute("DELETE FROM context_message_topics", [])
        .unwrap();
    conn.execute("DELETE FROM context_topic_stats", []).unwrap();
    conn.execute(
        "UPDATE context_retrieval_meta SET value = '1' WHERE key = 'live_context_schema'",
        [],
    )
    .unwrap();
    drop(conn);
    let conn = open_db(&db).unwrap();
    let version: String = conn
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = 'live_context_schema'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(version, LIVE_CONTEXT_SCHEMA_VERSION);
    let n: i64 = conn
        .query_row("SELECT COUNT(*) FROM context_message_topics", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert!(n >= 3, "backfill topics {n}");
    let contact: i64 = conn
        .query_row(
            "SELECT message_count FROM context_topic_stats
                 WHERE chat = '부자멘토멘티' AND topic = 'contact'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(contact >= 1, "contact stats {contact}");
}

#[test]
fn indexes_and_scopes_by_chat_and_source() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("index.sqlite3");
    let first = fixture(dir.path(), "first.csv", "세금 신고 일정");
    let second = fixture(dir.path(), "second.csv", "점심 김치찌개");
    assert_eq!(index_csv(&db, "같은 이름", &first).unwrap(), 1);
    assert_eq!(index_csv(&db, "같은 이름", &second).unwrap(), 1);
    assert_eq!(
        search(&db, Some("같은 이름"), None, "세금", "keyword", 5)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        search(
            &db,
            Some("같은 이름"),
            Some(&second.canonicalize().unwrap().display().to_string()),
            "세금",
            "keyword",
            5
        )
        .unwrap()
        .len(),
        0
    );
}
#[test]
fn indexes_choi_style_rows_in_separate_vector_table() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("index.sqlite3");
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n2026-01-01,최연우,ㅋㅋㅋ 이건 좀 세긴 하네\n2026-01-01,문승현,다른 사람 메시지\n",
    )
    .unwrap();

    assert_eq!(index_csv(&db, "부자멘토멘티", &path).unwrap(), 2);
    let results = style_search(&db, Some("부자멘토멘티"), "세긴 하네", 5).unwrap();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].user, STYLE_USER);
    assert_eq!(results[0].message, "ㅋㅋㅋ 이건 좀 세긴 하네");

    let conn = open_db(&db).unwrap();
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM owner_style", [], |row| row.get(0))
        .unwrap();
    assert_eq!(count, 1);
}

#[test]
fn indexes_and_reads_response_time_from_vector_db() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("index.sqlite3");
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
             2026-01-01 00:00:00,문승현,질문 하나\n\
             2026-01-01 00:00:10,최연우,답변 하나\n\
             2026-01-01 00:01:00,민수,질문 둘\n\
             2026-01-01 00:03:00,최연우,답변 둘\n",
    )
    .unwrap();

    index_csv(&db, "부자멘토멘티", &path).unwrap();
    let stats = response_time_stats(&db, "부자멘토멘티", STYLE_USER, None)
        .unwrap()
        .unwrap();
    assert_eq!(stats.sample_count, 2);
    assert!((stats.average_seconds - 65.0).abs() < f64::EPSILON);
    assert!((stats.median_seconds - 65.0).abs() < f64::EPSILON);
    assert_eq!(stats.p90_seconds, 109.0);
    assert_eq!(stats.min_seconds, 10.0);
    assert_eq!(stats.max_seconds, 120.0);
    assert!(stats.stddev_seconds > 0.0);
    assert!(stats.distribution.is_none());
}

#[test]
fn response_time_distribution_finds_three_empirical_modes() {
    let mut delays = Vec::new();
    delays.extend((0..16).map(|index| index as f64));
    delays.extend((0..8).map(|index| 40.0 + index as f64 * 10.0));
    delays.extend((0..8).map(|index| 500.0 + index as f64 * 100.0));
    let stats = summarize_response_delays("방", "source", STYLE_USER, delays).unwrap();
    let distribution = stats.distribution.unwrap();
    assert_eq!(distribution.schema_version, 2);
    assert_eq!(distribution.model_kind, "bounded-normal-mixture");
    assert_eq!(distribution.sample_count, 32);
    assert_eq!(distribution.retained_sample_count, 32);
    assert_eq!(distribution.components.len(), 3);
    assert_eq!(distribution.components[0].name, "immediate");
    assert_eq!(distribution.components[1].name, "short");
    assert_eq!(distribution.components[2].name, "delayed");
    assert_eq!(
        distribution.components[0].sample_count
            + distribution.components[1].sample_count
            + distribution.components[2].sample_count,
        32
    );
    assert_eq!(
        distribution.components[0].upper_seconds,
        distribution.split_seconds[0]
    );
    assert_eq!(
        distribution.components[1].upper_seconds,
        distribution.split_seconds[1]
    );
    assert_eq!(
        distribution.components[2].upper_seconds,
        distribution.global_upper_seconds
    );
    assert_eq!(distribution.split_seconds, vec![15.0, 110.0]);
    assert!((distribution.global_upper_seconds - 890.0).abs() < 1e-9);
    assert_eq!(distribution.tail_winsorized_count, 4);
    assert_eq!(distribution.components[0].weight, 0.5);
    assert_eq!(distribution.components[1].weight, 0.25);
    assert_eq!(distribution.components[2].weight, 0.25);
    assert!(response_time_distribution_is_valid(&distribution));
}

#[test]
fn response_time_distribution_fails_closed_for_weak_or_degenerate_samples() {
    assert!(fit_response_time_distribution(&vec![1.0; 31]).is_none());
    assert!(fit_response_time_distribution(&vec![1.0; 32]).is_none());
    let mut only_two_modes = vec![1.0; 16];
    only_two_modes.extend(vec![100.0; 16]);
    assert!(fit_response_time_distribution(&only_two_modes).is_none());
}

/// Exhaustive reference for the shipped exact split search. Kept in the
/// test module so every randomized case can assert the optimized path
/// returns the identical distribution.
fn exhaustive_response_time_distribution(delays: &[f64]) -> Option<ResponseTimeDistribution> {
    if delays.len() < RESPONSE_TIME_DISTRIBUTION_MIN_SAMPLES
        || delays.iter().any(|delay| {
            !delay.is_finite() || !(0.0..=MAX_RESPONSE_DELAY_SECONDS as f64).contains(delay)
        })
    {
        return None;
    }
    let transformed = delays.iter().map(|delay| delay.ln_1p()).collect::<Vec<_>>();
    let mut prefix_sum = vec![0.0; transformed.len() + 1];
    let mut prefix_square_sum = vec![0.0; transformed.len() + 1];
    for (index, value) in transformed.iter().enumerate() {
        prefix_sum[index + 1] = prefix_sum[index] + value;
        prefix_square_sum[index + 1] = prefix_square_sum[index] + value * value;
    }
    let segment_sse = |start: usize, end: usize| {
        let count = (end - start) as f64;
        let sum = prefix_sum[end] - prefix_sum[start];
        let square_sum = prefix_square_sum[end] - prefix_square_sum[start];
        (square_sum - sum * sum / count).max(0.0)
    };
    let mut best: Option<(f64, usize, usize)> = None;
    let minimum = RESPONSE_TIME_DISTRIBUTION_MIN_COMPONENT_SAMPLES;
    for first_split in minimum..=delays.len() - 2 * minimum {
        if delays[first_split - 1] >= delays[first_split] {
            continue;
        }
        for second_split in first_split + minimum..=delays.len() - minimum {
            if delays[second_split - 1] >= delays[second_split] {
                continue;
            }
            let sse = segment_sse(0, first_split)
                + segment_sse(first_split, second_split)
                + segment_sse(second_split, delays.len());
            if best.as_ref().is_none_or(|(best_sse, _, _)| sse < *best_sse) {
                best = Some((sse, first_split, second_split));
            }
        }
    }
    let (_, first_split, second_split) = best?;
    let global_upper_seconds = response_delay_percentile(delays, 0.9);
    let immediate_upper_seconds = delays[first_split - 1].min(global_upper_seconds);
    let short_lower_seconds = delays[first_split].max(MIN_SCHEDULED_RESPONSE_DELAY_SECONDS);
    let short_upper_seconds = delays[second_split - 1].min(global_upper_seconds);
    let delayed_lower_seconds = delays[second_split].max(MIN_SCHEDULED_RESPONSE_DELAY_SECONDS);
    if immediate_upper_seconds < MIN_SCHEDULED_RESPONSE_DELAY_SECONDS
        || short_lower_seconds > short_upper_seconds
        || delayed_lower_seconds > global_upper_seconds
        || immediate_upper_seconds >= short_lower_seconds
        || short_upper_seconds >= delayed_lower_seconds
    {
        return None;
    }
    let bounds = [
        (
            MIN_SCHEDULED_RESPONSE_DELAY_SECONDS,
            immediate_upper_seconds,
        ),
        (short_lower_seconds, short_upper_seconds),
        (delayed_lower_seconds, global_upper_seconds),
    ];
    let ranges = [
        (0, first_split),
        (first_split, second_split),
        (second_split, delays.len()),
    ];
    let mut components = Vec::with_capacity(RESPONSE_TIME_DISTRIBUTION_COMPONENTS);
    for ((start, end), ((lower_seconds, upper_seconds), name)) in ranges
        .into_iter()
        .zip(bounds.into_iter().zip(["immediate", "short", "delayed"]))
    {
        let values = delays[start..end]
            .iter()
            .map(|delay| delay.clamp(lower_seconds, upper_seconds))
            .collect::<Vec<_>>();
        let mean = values.iter().sum::<f64>() / values.len() as f64;
        let stddev = (values
            .iter()
            .map(|value| (value - mean).powi(2))
            .sum::<f64>()
            / values.len() as f64)
            .sqrt();
        if !stddev.is_finite() || stddev <= 0.0 {
            return None;
        }
        components.push(ResponseTimeComponent {
            name: name.to_string(),
            sample_count: values.len(),
            weight: values.len() as f64 / delays.len() as f64,
            normal_location_seconds: mean,
            normal_scale_seconds: stddev,
            lower_seconds,
            upper_seconds,
        });
    }
    let distribution = ResponseTimeDistribution {
        schema_version: RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION,
        policy_version: RESPONSE_TIME_DISTRIBUTION_POLICY_VERSION.to_string(),
        model_kind: RESPONSE_TIME_DISTRIBUTION_MODEL_KIND.to_string(),
        fit_transform: RESPONSE_TIME_DISTRIBUTION_FIT_TRANSFORM.to_string(),
        sample_count: delays.len(),
        retained_sample_count: delays.len(),
        tail_winsorized_count: delays
            .iter()
            .filter(|delay| **delay > global_upper_seconds)
            .count(),
        split_seconds: vec![immediate_upper_seconds, short_upper_seconds],
        global_upper_seconds,
        components,
    };
    response_time_distribution_is_valid(&distribution).then_some(distribution)
}

fn deterministic_delays(seed: u64, count: usize) -> Vec<f64> {
    let mut state = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    let mut next = move || {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (state >> 11) as f64 / (1u64 << 53) as f64
    };
    let mut values = (0..count)
        .map(|index| {
            // Three latent modes with heavy duplicates so the strict-increase
            // boundary rule and the tie-break both get exercised.
            let mode = index % 3;
            let base = match mode {
                0 => 12.0,
                1 => 90.0,
                _ => 900.0,
            };
            let jitter = (next() * 40.0).floor();
            let duplicate = if next() < 0.35 { 0.0 } else { 1.0 };
            base + jitter * duplicate
        })
        .collect::<Vec<_>>();
    values.sort_by(|left, right| left.partial_cmp(right).unwrap());
    values
}

#[test]
fn response_time_distribution_split_search_matches_exhaustive() {
    for count in [32usize, 33, 40, 100, 257, 1000] {
        for seed in 1u64..=8 {
            let delays = deterministic_delays(seed, count);
            let fast = fit_response_time_distribution(&delays);
            let reference = exhaustive_response_time_distribution(&delays);
            assert_eq!(
                fast.is_some(),
                reference.is_some(),
                "presence diverged at count={count} seed={seed}"
            );
            let (Some(fast), Some(reference)) = (fast, reference) else {
                continue;
            };
            assert_eq!(
                fast.split_seconds, reference.split_seconds,
                "split diverged at count={count} seed={seed}"
            );
            assert_eq!(fast.global_upper_seconds, reference.global_upper_seconds);
            assert_eq!(fast.tail_winsorized_count, reference.tail_winsorized_count);
            assert_eq!(fast.sample_count, reference.sample_count);
            for (optimized, naive) in fast.components.iter().zip(reference.components.iter()) {
                assert_eq!(optimized.name, naive.name);
                assert_eq!(optimized.sample_count, naive.sample_count);
                assert_eq!(optimized.weight, naive.weight);
                assert_eq!(
                    optimized.normal_location_seconds,
                    naive.normal_location_seconds
                );
                assert_eq!(optimized.normal_scale_seconds, naive.normal_scale_seconds);
                assert_eq!(optimized.lower_seconds, naive.lower_seconds);
                assert_eq!(optimized.upper_seconds, naive.upper_seconds);
            }
        }
    }
}

#[test]
fn response_time_distribution_split_search_handles_plateaus() {
    // A fully flat sample set has no strict-increase boundary, so the fit
    // must fail closed exactly like the exhaustive scan.
    assert!(fit_response_time_distribution(&vec![7.0; 64]).is_none());
    // Duplicate runs at the boundaries leave only a narrow strict-increase
    // window; the optimized search must still find the same pair.
    let mut delays = vec![3.0; 12];
    delays.extend((0..12).map(|index| 20.0 + index as f64));
    delays.extend(vec![400.0; 12]);
    delays.extend((0..12).map(|index| 800.0 + index as f64));
    assert_eq!(
        fit_response_time_distribution(&delays).map(|item| item.split_seconds),
        exhaustive_response_time_distribution(&delays).map(|item| item.split_seconds),
    );
    // Heavy duplication ties many candidate pairs on SSE, so the tie-break
    // (lowest first then lowest second) is what keeps the two paths equal.
    for seed in 1u64..=16 {
        let delays = duplicate_heavy_delays(seed, 200);
        assert_eq!(
            fit_response_time_distribution(&delays).map(|item| item.split_seconds),
            exhaustive_response_time_distribution(&delays).map(|item| item.split_seconds),
            "tie-break diverged at seed={seed}"
        );
    }
}

fn duplicate_heavy_delays(seed: u64, count: usize) -> Vec<f64> {
    let mut state = seed
        .wrapping_mul(2862933555777941757)
        .wrapping_add(3037000493);
    let mut next = move || {
        state = state
            .wrapping_mul(2862933555777941757)
            .wrapping_add(3037000493);
        (state >> 33) as f64 / (1u64 << 31) as f64
    };
    // Only six distinct levels, so most segment SSEs tie and the boundary
    // rule plus tie-break decide the winner.
    let levels = [10.0, 30.0, 30.0, 120.0, 600.0, 600.0];
    let mut values = (0..count)
        .map(|_| levels[(next() * levels.len() as f64) as usize % levels.len()])
        .collect::<Vec<_>>();
    values.sort_by(|left, right| left.partial_cmp(right).unwrap());
    values
}

#[test]
fn response_time_distribution_split_search_scales_to_live_sample_counts() {
    // The live chats carry up to ~20k response samples, where the previous
    // O(N^2) pair scan was the slow path. A dense continuous shape is the
    // worst case for it, because no duplicate run removes candidate
    // boundaries: the exhaustive scan takes ~660 ms at 20,174 samples on
    // this machine, and the O(N log N) search takes ~2.4 ms. The bound
    // below is 100x the measured search while still failing an O(N^2)
    // regression.
    let delays = shaped_delays(99, 20_000, 2);
    let started = std::time::Instant::now();
    let fitted = fit_response_time_distribution(&delays).expect("live shape must fit");
    let elapsed = started.elapsed();
    assert_eq!(fitted.sample_count, 20_000);
    assert!(
        elapsed < std::time::Duration::from_millis(250),
        "exact split search took {elapsed:?}"
    );
}

#[test]
fn response_time_distribution_split_search_matches_exhaustive_across_shapes() {
    // The divide-and-conquer split search rests on the quadrangle
    // inequality of the segment SSE, a property of sorted shapes rather
    // than of arbitrary inputs, so the equivalence is pinned against the
    // exhaustive scan over a wide battery instead of assumed: every
    // strict-increase window, every plateau, and every duplicate tie is
    // compared on both paths, at counts around the minimum window (32, 33,
    // 34) as well as at live scale.
    for seed in 1u64..=24 {
        for count in [32usize, 33, 34, 40, 47, 55, 64, 96, 150, 311, 640, 1000] {
            for shape in 0..6u64 {
                let delays = shaped_delays(seed * 7 + shape, count, shape);
                let fast = fit_response_time_distribution(&delays);
                let reference = exhaustive_response_time_distribution(&delays);
                assert_eq!(
                    fast.is_some(),
                    reference.is_some(),
                    "presence diverged at count={count} seed={seed} shape={shape}"
                );
                let (Some(fast), Some(reference)) = (fast, reference) else {
                    continue;
                };
                assert_eq!(
                    fast.split_seconds, reference.split_seconds,
                    "split diverged at count={count} seed={seed} shape={shape}"
                );
                assert_eq!(fast.global_upper_seconds, reference.global_upper_seconds);
                assert_eq!(fast.tail_winsorized_count, reference.tail_winsorized_count);
                for (optimized, naive) in fast.components.iter().zip(reference.components.iter()) {
                    assert_eq!(optimized.name, naive.name);
                    assert_eq!(optimized.sample_count, naive.sample_count);
                    assert_eq!(optimized.weight, naive.weight);
                    assert_eq!(
                        optimized.normal_location_seconds,
                        naive.normal_location_seconds
                    );
                    assert_eq!(optimized.normal_scale_seconds, naive.normal_scale_seconds);
                    assert_eq!(optimized.lower_seconds, naive.lower_seconds);
                    assert_eq!(optimized.upper_seconds, naive.upper_seconds);
                }
            }
        }
    }
}

/// Six sorted shapes: three equal modes, a coarse staircase, a dense
/// uniform spread, a heavy-tail sweep, a six-level duplicate ladder, and a
/// stair of long duplicate runs. The last two collapse most candidate
/// boundaries, so they exercise the fail-closed paths and the tie-break.
fn shaped_delays(seed: u64, count: usize, shape: u64) -> Vec<f64> {
    let mut state = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    let mut next = move || {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (state >> 11) as f64 / (1u64 << 53) as f64
    };
    let mut values = (0..count)
        .map(|index| match shape {
            0 => {
                let base = match index % 3 {
                    0 => 8.0,
                    1 => 90.0,
                    _ => 700.0,
                };
                base + (next() * 30.0).floor()
            }
            1 => (index / 4) as f64 * 25.0 + (next() * 6.0).floor(),
            2 => next() * 900.0,
            3 => (next().powf(2.5)) * 900.0,
            4 => {
                const LEVELS: [f64; 6] = [10.0, 30.0, 30.0, 120.0, 600.0, 600.0];
                LEVELS[(next() * LEVELS.len() as f64) as usize % LEVELS.len()]
            }
            _ => (index / 6) as f64 * 120.0,
        })
        .collect::<Vec<_>>();
    values.sort_by(|left, right| left.partial_cmp(right).unwrap());
    values
}

#[test]
fn response_time_distribution_migration_is_additive_backfills_and_repairs() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("migration.sqlite3");
    let legacy = Connection::open(&db).unwrap();
    legacy
        .execute_batch(
            "CREATE TABLE response_time_stats(
                    chat TEXT NOT NULL, source TEXT NOT NULL, user_name TEXT NOT NULL,
                    sample_count INTEGER NOT NULL, average_seconds REAL NOT NULL,
                    median_seconds REAL NOT NULL, p90_seconds REAL NOT NULL,
                    min_seconds REAL NOT NULL, max_seconds REAL NOT NULL,
                    max_window_seconds INTEGER NOT NULL,
                    stddev_seconds REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY(chat, source, user_name)
                );",
        )
        .unwrap();
    drop(legacy);
    let conn = open_db(&db).unwrap();
    let columns = conn
        .prepare("PRAGMA table_info(response_time_stats)")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert!(columns.contains(&"distribution_schema_version".to_string()));
    assert!(columns.contains(&"distribution_json".to_string()));
    let source = "local-db:migration";
    conn.execute(
        "INSERT INTO context_sources(
                source,kind,account_fingerprint,chat_id,chat,authoritative,
                checkpoint_log_id,pending_burst_json,summary_dirty,sync_status,updated_at
             ) VALUES(?1,'local_db',?2,42,'방',1,64,'[]',0,'ready',?3)",
        params![source, TEST_ACCOUNT_FINGERPRINT, Utc::now().to_rfc3339()],
    )
    .unwrap();
    let mut delays = Vec::new();
    delays.extend((0..16).map(|index| index as f64));
    delays.extend((0..8).map(|index| 40.0 + index as f64 * 10.0));
    delays.extend((0..8).map(|index| 500.0 + index as f64 * 100.0));
    for (index, delay) in delays.iter().enumerate() {
        conn.execute(
            "INSERT INTO response_time_samples(
                    source,chat_id,reply_log_id,prompt_log_id,recipient,delay_seconds
                 ) VALUES(?1,42,?2,?3,'민수',?4)",
            params![source, 100 + index as i64, 10 + index as i64, delay],
        )
        .unwrap();
    }
    conn.execute(
        "INSERT INTO response_time_stats(
                chat,source,user_name,sample_count,average_seconds,median_seconds,
                p90_seconds,min_seconds,max_seconds,max_window_seconds,stddev_seconds,
                distribution_schema_version,distribution_json
             ) VALUES('방',?1,?2,32,0,0,890,0,1200,86400,1,1,'{}')",
        params![source, STYLE_USER],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO context_retrieval_meta(key,value)
             VALUES('response_time_distribution_schema',?1)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION.to_string()],
    )
    .unwrap();
    drop(conn);

    let repaired = open_db(&db).unwrap();
    let (version, json): (i64, String) = repaired
        .query_row(
            "SELECT distribution_schema_version,distribution_json
                 FROM response_time_stats WHERE source=?1",
            [source],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(version, RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION as i64);
    let distribution: ResponseTimeDistribution = serde_json::from_str(&json).unwrap();
    assert!(response_time_distribution_is_valid(&distribution));
    drop(repaired);
    let reopened = open_db(&db).unwrap();
    let unchanged: String = reopened
        .query_row(
            "SELECT distribution_json FROM response_time_stats WHERE source=?1",
            [source],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(unchanged, json);
}
#[test]
fn reindex_removes_old_rows_and_vector_is_deterministic() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("index.sqlite3");
    let path = fixture(dir.path(), "chat.csv", "old phrase");
    index_csv(&db, "방", &path).unwrap();
    fs::write(&path, "Date,User,Message\n2026-01-01,민수,new phrase\n").unwrap();
    index_csv(&db, "방", &path).unwrap();
    assert!(search(&db, Some("방"), None, "old", "keyword", 5)
        .unwrap()
        .is_empty());
    assert_eq!(encode_vector("same"), encode_vector("same"));
}

#[test]
fn relative_db_and_invalid_vector_query_are_safe() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("relative.sqlite3");
    let path = fixture(dir.path(), "chat.csv", "hello");
    index_csv(&db, "방", &path).unwrap();
    // Token-less queries (`!!!`) no longer fail the whole vector lookup:
    // they return an empty hit list so style/timing bundles keep flowing.
    let hits = search(&db, Some("방"), None, "!!!", "vector", 5).unwrap();
    assert!(hits.is_empty());
}
#[test]
fn records_and_searches_reply_decisions() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("index.sqlite3");
    let record = serde_json::json!({
        "event_id": "event-1",
        "chat": "부자멘토멘티",
        "author": "민수",
        "received_at": "2026-01-01T00:00:00Z",
        "message": "세금 신고 일정이 궁금해",
        "decision": "reply",
        "reason": "direct_question",
        "category": "question",
        "context_match_count": 2,
        "style_match_count": 3,
        "best_context_score": 0.8,
        "best_style_score": 0.9,
        "prior_similarity": 0.0,
        "scheduled_delay_seconds": 15.0,
        "status": "scheduled",
        "reply": "지난번처럼 일정 확인해보면 돼",
        "evidence_ids": [
            "context:abc",
            "style:def",
            "timing:2:empirical-log1p-three-means-p90-v1:immediate:w0.5:lo5:hi17"
        ],
        "style_policy_version": "ordinary-conversation-v3",
        "provenance": {
            "rerank": {"fallback": "none", "policy_rejections": []},
            "response_timing": {"component": "immediate", "component_weight": 0.5}
        }
    });
    record_reply_decision(&db, &record.to_string()).unwrap();
    let matches = reply_decision_search(&db, "부자멘토멘티", "세금 신고", 5).unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].decision, "reply");
    assert_eq!(matches[0].status, "scheduled");
    assert!(update_reply_decision(
        &db,
        "event-1",
        "sent",
        Some("답변 완료"),
        Some("2026-01-01T00:00:15Z")
    )
    .unwrap());
    let updated = reply_decision_search(&db, "부자멘토멘티", "세금 신고", 5).unwrap();
    assert_eq!(updated[0].status, "sent");
    assert!(updated[0]
        .evidence_json
        .contains("ordinary-conversation-v3"));
    assert!(updated[0]
        .evidence_json
        .contains("timing:2:empirical-log1p-three-means-p90-v1:immediate:w0.5:lo5:hi17"));
    assert!(updated[0].evidence_json.contains("response_timing"));
    assert!(updated[0].evidence_json.contains("immediate"));
}
#[test]
fn reply_decision_boundary_validation_and_stale_noop() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("reply-validation.sqlite3");
    let base = serde_json::json!({
        "event_id": "event-1",
        "chat": "chat-a",
        "author": "민수",
        "received_at": "2026-01-01T00:00:00Z",
        "message": "질문",
        "decision": "reply",
        "reason": "question",
        "category": "question",
        "context_match_count": 1,
        "style_match_count": 1,
        "best_context_score": 0.5,
        "best_style_score": 0.5,
        "prior_similarity": 0.5,
        "scheduled_delay_seconds": 1.0,
        "status": "scheduled",
        "reply": "답변",
        "evidence_ids": ["context:1"],
        "style_policy_version": STYLE_POLICY_VERSION,
    });
    let mut invalid = base.clone();
    invalid["evidence_ids"] = serde_json::json!([]);
    assert!(record_reply_decision(&db, &invalid.to_string()).is_err());
    let mut invalid = base.clone();
    invalid["style_policy_version"] = serde_json::json!("legacy-v1");
    assert!(record_reply_decision(&db, &invalid.to_string()).is_err());
    let mut invalid = base.clone();
    invalid["best_context_score"] = serde_json::json!(-0.1);
    assert!(record_reply_decision(&db, &invalid.to_string()).is_err());
    let mut invalid = base.clone();
    invalid["scheduled_delay_seconds"] = serde_json::json!(-1.0);
    assert!(record_reply_decision(&db, &invalid.to_string()).is_err());
    for reply in ["ㅋ", "ㅋㅋ", "ㅎ", "ㅎㅎ", "ㅎㅎㅎ"] {
        let mut invalid = base.clone();
        invalid["reply"] = serde_json::json!(reply);
        assert!(record_reply_decision(&db, &invalid.to_string()).is_err());
    }

    assert!(record_reply_decision(&db, &base.to_string()).unwrap());
    assert!(update_reply_decision(&db, "event-1", "sent", Some("ㅎㅎㅎ"), None).is_err());
    let mut stale = base;
    stale["status"] = serde_json::json!("pending");
    assert!(!record_reply_decision(&db, &stale.to_string()).unwrap());

    let mut skip = serde_json::json!({
        "event_id": "skip-1",
        "chat": "chat-a",
        "author": "민수",
        "received_at": "2026-01-01T00:00:00Z",
        "message": "공지",
        "decision": "skip",
        "reason": "not_actionable",
        "category": "other",
        "context_match_count": 0,
        "style_match_count": 0,
        "best_context_score": 0.0,
        "best_style_score": 0.0,
        "prior_similarity": 0.0,
        "scheduled_delay_seconds": 0.0,
        "status": "skipped",
        "reply": null,
        "evidence_ids": [],
        "style_policy_version": "",
    });
    assert!(record_reply_decision(&db, &skip.to_string()).unwrap());
    skip["evidence_ids"] = serde_json::json!([""]);
    assert!(record_reply_decision(&db, &skip.to_string()).is_err());
}

#[test]
fn terminal_skip_clears_a_previously_scheduled_reply_and_sent_marker() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("reply-terminal-skip.sqlite3");
    let mut record = serde_json::json!({
        "event_id": "event-skip",
        "chat": "chat-a",
        "author": "민수",
        "received_at": "2026-01-01T00:00:00Z",
        "message": "질문",
        "decision": "reply",
        "reason": "question",
        "category": "question",
        "context_match_count": 1,
        "style_match_count": 1,
        "best_context_score": 0.5,
        "best_style_score": 0.5,
        "prior_similarity": 0.5,
        "scheduled_delay_seconds": 1.0,
        "status": "scheduled",
        "reply": "답변",
        "evidence_ids": ["context:1"],
        "style_policy_version": STYLE_POLICY_VERSION,
    });
    assert!(record_reply_decision(&db, &record.to_string()).unwrap());
    let conn = Connection::open(&db).unwrap();
    conn.execute(
        "UPDATE reply_decisions SET sent_at='2026-01-01T00:00:01Z' WHERE event_id='event-skip'",
        [],
    )
    .unwrap();
    drop(conn);

    record["decision"] = serde_json::json!("skip");
    record["reason"] = serde_json::json!("stale_backlog");
    record["category"] = serde_json::json!("policy");
    record["scheduled_delay_seconds"] = serde_json::json!(0.0);
    record["status"] = serde_json::json!("skipped");
    record["reply"] = serde_json::Value::Null;
    record["evidence_ids"] = serde_json::json!([]);
    record["style_policy_version"] = serde_json::json!("");
    assert!(record_reply_decision(&db, &record.to_string()).unwrap());

    let conn = Connection::open(&db).unwrap();
    let row = conn
        .query_row(
            "SELECT decision,status,reason,category,reply,sent_at FROM reply_decisions WHERE event_id='event-skip'",
            [],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, Option<String>>(5)?,
                ))
            },
        )
        .unwrap();
    assert_eq!(
        row,
        (
            "skip".to_string(),
            "skipped".to_string(),
            "stale_backlog".to_string(),
            "policy".to_string(),
            None,
            None,
        )
    );
}

#[test]
fn exact_reply_decisions_precede_bounded_similarity_candidates() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("reply-exact.sqlite3");
    let make_record = |event_id: String, message: String| {
        serde_json::json!({
            "event_id": event_id,
            "chat": "chat-a",
            "author": "민수",
            "received_at": "2026-01-01T00:00:00Z",
            "message": message,
            "decision": "skip",
            "reason": "not_actionable",
            "category": "other",
            "context_match_count": 0,
            "style_match_count": 0,
            "best_context_score": 0.0,
            "best_style_score": 0.0,
            "prior_similarity": 0.0,
            "scheduled_delay_seconds": 0.0,
            "status": "skipped",
            "reply": null,
            "evidence_ids": [],
            "style_policy_version": "",
        })
    };
    record_reply_decision(
        &db,
        &make_record("exact-old".into(), "target duplicate".into()).to_string(),
    )
    .unwrap();
    for index in 0..(REPLY_DECISION_CANDIDATE_CAP + 16) {
        record_reply_decision(
            &db,
            &make_record(
                format!("event-{index:03}"),
                format!("other message {index}"),
            )
            .to_string(),
        )
        .unwrap();
    }
    let results = reply_decision_search(&db, "chat-a", "target duplicate", 5).unwrap();
    assert_eq!(
        results.first().map(|result| result.event_id.as_str()),
        Some("exact-old")
    );
    assert_eq!(results.first().map(|result| result.score), Some(1.0));
}

#[test]
fn reply_projection_reports_updates_and_rejects_regression() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("reply.sqlite3");
    let first = project_reply_decision(&db, "db:7:42", "scheduled", r#"{"source":"db"}"#).unwrap();
    assert!(first.applied);
    assert!(first.updated);
    let sent = project_reply_decision(&db, "db:7:42", "sent", r#"{"confirmed":true}"#).unwrap();
    assert!(sent.updated);
    let stale = project_reply_decision(&db, "db:7:42", "pending", "{}").unwrap();
    assert!(!stale.updated);
    assert_eq!(
        get_reply_decision(&db, "db:7:42").unwrap().unwrap().status,
        "sent"
    );
}
#[test]
fn delivery_unknown_and_reconcile_required_are_terminal() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("reply-terminal.sqlite3");

    for (index, terminal) in ["delivery_unknown", "reconcile_required"]
        .into_iter()
        .enumerate()
    {
        let event_id = format!("terminal-{index}");
        assert!(
            project_reply_decision(&db, &event_id, "scheduled", "{}")
                .unwrap()
                .updated
        );
        let terminal_update =
            project_reply_decision(&db, &event_id, terminal, r#"{"unknown":true}"#).unwrap();
        assert!(terminal_update.applied);
        assert!(terminal_update.updated);

        let idempotent =
            project_reply_decision(&db, &event_id, terminal, r#"{"unknown":true}"#).unwrap();
        assert!(idempotent.applied);
        assert!(!idempotent.updated);

        for next_status in [
            "sent",
            "skipped",
            "poison",
            "delivery_unknown",
            "reconcile_required",
        ] {
            if next_status == terminal {
                continue;
            }
            let blocked = project_reply_decision(&db, &event_id, next_status, "{}").unwrap();
            assert!(!blocked.applied);
            assert!(!blocked.updated);
            assert_eq!(
                blocked.reason.as_deref(),
                Some("terminal_or_newer_status_cannot_regress")
            );
            assert!(!update_reply_decision(&db, &event_id, next_status, None, None).unwrap());
        }
    }
}

#[test]
fn retrieval_families_require_current_migration_marker() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("retrieval-marker.sqlite3");
    let path = fixture(dir.path(), "chat.csv", "marker query");
    index_csv(&db, "chat-a", &path).unwrap();

    let conn = open_db(&db).unwrap();
    conn.execute(
        "UPDATE context_retrieval_meta SET value = 'stale' WHERE key = 'fts_schema'",
        [],
    )
    .unwrap();
    drop(conn);

    let assert_migration_required = |result: Result<()>| {
        assert_eq!(
            result.unwrap_err().to_string(),
            CONTEXT_RETRIEVAL_MIGRATION_REQUIRED
        );
    };
    assert_migration_required(
        search(&db, Some("chat-a"), None, "marker", "keyword", 5).map(|_| ()),
    );
    assert_migration_required(style_search(&db, Some("chat-a"), "marker", 5).map(|_| ()));
    assert_migration_required(style_profile(&db, "chat-a", STYLE_USER, None).map(|_| ()));
    assert_migration_required(response_time_stats(&db, "chat-a", STYLE_USER, None).map(|_| ()));
    assert_migration_required(reply_decision_search(&db, "chat-a", "marker", 5).map(|_| ()));
    assert_migration_required(context_reply_bundle(&db, "chat-a", "marker", None).map(|_| ()));
}

#[test]
fn builds_rebuilds_and_serializes_style_profile_from_eligible_rows() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("profile.sqlite3");
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
             2026-01-01,최연우,ㅋㅋㅋ 오늘 좋다\n\
             2026-01-01,최연우,뭐해요? 😊\n\
             2026-01-01,최연우,1) 복사한 정보\n\
             2026-01-01,최연우,https://example.com 안내\n\
             2026-01-01,최연우,공지드립니다 합니다\n\
             2026-01-01,민수,일반 대화\n",
    )
    .unwrap();

    let source = path.canonicalize().unwrap().display().to_string();
    assert_eq!(index_csv(&db, "프로필방", &path).unwrap(), 6);

    let profile = style_profile(&db, "프로필방", STYLE_USER, Some(&source))
        .unwrap()
        .unwrap();
    assert_eq!(profile.sample_count, 2);
    assert_eq!(profile.question_count, 1);
    assert_eq!(profile.emoji_count, 1);
    assert_eq!(profile.punctuation_count, 1);
    assert_eq!(profile.casual_ending_count, 1);
    assert!(profile.median_character_length > 0.0);
    assert!(profile.p90_character_length >= profile.median_character_length);
    assert_eq!(profile.policy_version, STYLE_POLICY_VERSION);
    let endings: serde_json::Value = serde_json::from_str(&profile.common_endings_json).unwrap();
    assert_eq!(endings["요"], 1);
    let serialized = style_profile_json(&db, "프로필방", STYLE_USER, Some(&source))
        .unwrap()
        .unwrap();
    let serialized: serde_json::Value = serde_json::from_str(&serialized).unwrap();
    assert_eq!(serialized["sample_count"], 2);

    let conn = open_db(&db).unwrap();
    let rows = conn
        .prepare(
            "SELECT source_row, content_kind, style_eligible
                 FROM owner_style WHERE source = ?1 ORDER BY source_row",
        )
        .unwrap()
        .query_map([&source], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
            ))
        })
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert_eq!(rows.len(), 5);
    assert_eq!(rows[0], (1, "ordinary_conversation".into(), 1));
    assert_eq!(rows[2], (3, "list_or_numbered".into(), 0));
    assert_eq!(rows[3], (4, "url".into(), 0));
    assert_eq!(rows[4], (5, "long_or_formal".into(), 0));

    fs::write(&path, "Date,User,Message\n2026-01-01,최연우,새 말투네\n").unwrap();
    index_csv(&db, "프로필방", &path).unwrap();
    let rebuilt = style_profile(&db, "프로필방", STYLE_USER, Some(&source))
        .unwrap()
        .unwrap();
    assert_eq!(rebuilt.sample_count, 1);
    assert!(rebuilt.common_tokens_json.contains("새"));
    assert!(!rebuilt.common_tokens_json.contains("오늘"));

    fs::write(
        &path,
        "Date,User,Message\n2026-01-01,최연우,https://example.com\n",
    )
    .unwrap();
    index_csv(&db, "프로필방", &path).unwrap();
    assert!(style_profile(&db, "프로필방", STYLE_USER, Some(&source))
        .unwrap()
        .is_none());
    assert_eq!(
        open_db(&db)
            .unwrap()
            .query_row(
                "SELECT COUNT(*) FROM owner_style_profile
                     WHERE chat = ?1 AND source = ?2",
                params!["프로필방", source],
                |row| row.get::<_, i64>(0),
            )
            .unwrap(),
        0
    );
}
#[test]
fn context_reply_bundle_is_versioned_and_shape_stable() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("bundle.sqlite3");
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
             2026-01-01 00:00:00,민수,세금 일정 알려줘\n\
             2026-01-01 00:00:10,최연우,ㅋㅋㅋ 일정 확인해요\n",
    )
    .unwrap();
    index_csv(&db, "부자멘토멘티", &path).unwrap();

    let bundle = context_reply_bundle(&db, "부자멘토멘티", "세금 일정", None).unwrap();
    assert_eq!(bundle.schema_version, CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION);
    assert!(bundle.context.len() <= CONTEXT_REPLY_BUNDLE_CONTEXT_LIMIT);
    assert!(bundle.styles.len() <= CONTEXT_REPLY_BUNDLE_STYLE_LIMIT);
    assert!(bundle.prior_decisions.len() <= CONTEXT_REPLY_BUNDLE_DECISION_LIMIT);
    assert_eq!(
        bundle.style_profile.as_ref().unwrap().policy_version,
        STYLE_POLICY_VERSION
    );
    let value: serde_json::Value = serde_json::from_str(
        &context_reply_bundle_json(&db, "부자멘토멘티", "세금 일정", None).unwrap(),
    )
    .unwrap();
    let serialized = serde_json::to_string(&value).unwrap();
    let home = dirs::home_dir().unwrap().display().to_string();
    assert!(!serialized.contains(&home));
    assert!(!serialized.contains(&path.display().to_string()));
    assert_eq!(
        value
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect::<Vec<_>>(),
        vec![
            "schema_version",
            "context",
            "styles",
            "prior_decisions",
            "style_profile",
            "response_time",
        ]
    );
}

#[test]
fn punctuation_only_query_keeps_style_and_timing() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("tokenless.sqlite3");
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
             2026-01-01 00:00:00,민수,세금 일정 알려줘\n\
             2026-01-01 00:00:10,최연우,ㅋㅋㅋ 일정 확인해요\n",
    )
    .unwrap();
    index_csv(&db, "부자멘토멘티", &path).unwrap();
    let bundle = context_reply_bundle(&db, "부자멘토멘티", "???", None).unwrap();
    assert!(bundle.context.is_empty());
    assert!(!bundle.styles.is_empty());
    assert!(bundle.styles.iter().all(|row| row.mode == "recency_style"));
    assert!(bundle.style_profile.is_some());
    assert!(bundle.response_time.is_some());
}

#[test]
fn recipient_bundle_excludes_every_burst_row_and_decision() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("burst-exclusion.sqlite3");
    let chat_id = 77;
    let chat = "부자멘토멘티";
    let events = (101..=107)
        .map(|log_id| {
            let disposition = if log_id == 107 {
                "retained"
            } else {
                "excluded"
            };
            live_event(
                chat_id,
                log_id,
                "민수",
                &format!("burstmarker {disposition} context {log_id}"),
                1_000 + log_id,
            )
        })
        .collect::<Vec<_>>();
    let ingested = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &events,
        true,
        false,
    )
    .unwrap();

    for log_id in 101..=107 {
        let disposition = if log_id == 107 {
            "retained"
        } else {
            "excluded"
        };
        let record = serde_json::json!({
            "event_id": format!("db:{chat_id}:{log_id}"),
            "chat": chat,
            "author": "민수",
            "received_at": "2026-01-01T00:00:00Z",
            "message": format!("burstmarker {disposition} decision {log_id}"),
            "decision": "skip",
            "reason": "duplicate",
            "category": "duplicate",
            "context_match_count": 0,
            "style_match_count": 0,
            "best_context_score": 0.0,
            "best_style_score": 0.0,
            "prior_similarity": 0.0,
            "scheduled_delay_seconds": 0.0,
            "status": "skipped",
            "reply": null,
            "evidence_ids": [],
            "style_policy_version": "",
        });
        assert!(record_reply_decision(&db, &record.to_string()).unwrap());
    }

    let unfiltered = context_reply_bundle_for_recipient_excluding_live_events(
        &db,
        chat,
        "burstmarker",
        Some(&ingested.source),
        "민수",
        chat_id,
        &[999],
    )
    .unwrap();
    assert!(unfiltered
        .context
        .iter()
        .any(|row| row.message.contains("excluded context 101")));
    assert!(unfiltered
        .prior_decisions
        .iter()
        .any(|row| row.event_id == format!("db:{chat_id}:101")));

    let bundle = context_reply_bundle_for_recipient_excluding_live_events(
        &db,
        chat,
        "burstmarker",
        Some(&ingested.source),
        "민수",
        chat_id,
        &[101, 102, 103, 104, 105, 106],
    )
    .unwrap();
    assert!(!bundle
        .context
        .iter()
        .any(|row| row.message.contains("excluded context")));
    assert!(bundle
        .context
        .iter()
        .any(|row| row.message.contains("retained context 107")));
    assert!(!bundle
        .prior_decisions
        .iter()
        .any(|row| (101..=106).any(|log_id| row.event_id == format!("db:{chat_id}:{log_id}"))));
    assert!(bundle
        .prior_decisions
        .iter()
        .any(|row| row.event_id == format!("db:{chat_id}:107")));
}

#[test]
fn burst_exclusion_ids_are_positive_unique_and_bounded() {
    assert!(live_event_exclusions(7, &[1]).is_ok());
    assert!(live_event_exclusions(0, &[1]).is_err());
    assert!(live_event_exclusions(7, &[]).is_err());
    assert!(live_event_exclusions(7, &[0]).is_err());
    assert!(live_event_exclusions(7, &[-1]).is_err());
    assert!(live_event_exclusions(7, &[1, 1]).is_err());
    assert!(live_event_exclusions(7, &[1, 2, 3, 4, 5, 6]).is_ok());
    assert!(live_event_exclusions(7, &[1, 2, 3, 4, 5, 6, 7]).is_err());
    assert!(live_event_exclusions(7, &[i64::MAX]).is_ok());
}

#[test]
fn retrieval_candidate_pools_are_bounded_and_deterministic() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("bounded.sqlite3");
    let conn = open_db(&db).unwrap();
    for index in 0..(CONTEXT_VECTOR_CANDIDATE_CAP + 32) {
        insert_context_row(
            &conn,
            "source-a",
            "chat-a",
            "2026-01-01",
            &format!("bounded candidate {index}"),
        );
    }
    for index in 0..(STYLE_VECTOR_CANDIDATE_CAP + 32) {
        conn.execute(
            "INSERT INTO owner_style(
                    source, chat, date, user_name, message, vector, source_row,
                    content_kind, style_eligible, policy_version, features_json
                 ) VALUES (?1, 'chat-a', '2026-01-01', ?2, ?3, ?4, ?5,
                           'ordinary_conversation', 1, ?6, '{}')",
            params![
                "source-a",
                STYLE_USER,
                format!("style candidate {index}"),
                vector_to_bytes(&encode_vector("style candidate")),
                index as i64,
                STYLE_POLICY_VERSION,
            ],
        )
        .unwrap();
    }
    drop(conn);

    let first = search(
        &db,
        Some("chat-a"),
        Some("source-a"),
        "bounded",
        "vector",
        CONTEXT_VECTOR_CANDIDATE_CAP * 2,
    )
    .unwrap();
    let second = search(
        &db,
        Some("chat-a"),
        Some("source-a"),
        "bounded",
        "vector",
        CONTEXT_VECTOR_CANDIDATE_CAP * 2,
    )
    .unwrap();
    assert_eq!(first.len(), CONTEXT_VECTOR_CANDIDATE_CAP);
    assert_eq!(
        serde_json::to_string(&first).unwrap(),
        serde_json::to_string(&second).unwrap()
    );

    let conn = open_db(&db).unwrap();
    let styles = style_search_with_connection(
        &conn,
        Some("chat-a"),
        Some("source-a"),
        "style",
        STYLE_VECTOR_CANDIDATE_CAP * 2,
        true,
    )
    .unwrap();
    assert_eq!(styles.len(), STYLE_VECTOR_CANDIDATE_CAP);
}

#[test]
fn fts_rebuild_recovers_deleted_index_rows() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("fts-rebuild.sqlite3");
    let path = fixture(dir.path(), "chat.csv", "rebuild marker");
    index_csv(&db, "chat-a", &path).unwrap();

    let conn = open_db(&db).unwrap();
    conn.execute("DELETE FROM context_messages_fts", [])
        .unwrap();
    conn.execute("DELETE FROM context_retrieval_meta", [])
        .unwrap();
    drop(conn);

    let error = search(&db, Some("chat-a"), None, "rebuild", "keyword", 5).unwrap_err();
    assert_eq!(error.to_string(), CONTEXT_RETRIEVAL_MIGRATION_REQUIRED);
    rebuild_retrieval_index(&open_db(&db).unwrap()).unwrap();
    let results = search(&db, Some("chat-a"), None, "rebuild", "keyword", 5).unwrap();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].message, "rebuild marker");
}

#[test]
fn style_policy_and_source_filters_are_applied_before_scoring() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("style-policy.sqlite3");
    let first = dir.path().join("first.csv");
    let second = dir.path().join("second.csv");
    fs::write(
        &first,
        "Date,User,Message\n2026-01-01,최연우,current style\n",
    )
    .unwrap();
    fs::write(
        &second,
        "Date,User,Message\n2026-01-01,최연우,other source style\n",
    )
    .unwrap();
    index_csv(&db, "chat-a", &first).unwrap();
    index_csv(&db, "chat-a", &second).unwrap();
    let source = first.canonicalize().unwrap().display().to_string();

    let conn = open_db(&db).unwrap();
    conn.execute(
        "INSERT INTO owner_style(
                source, chat, date, user_name, message, vector, source_row,
                content_kind, style_eligible, policy_version, features_json
             ) VALUES (?1, 'chat-a', '2027-01-01', ?2, 'legacy style',
                       ?3, 999, 'ordinary_conversation', 1, 'legacy-v1', '{}')",
        params![
            source,
            STYLE_USER,
            vector_to_bytes(&encode_vector("legacy style")),
        ],
    )
    .unwrap();

    let results =
        style_search_with_connection(&conn, Some("chat-a"), Some(&source), "legacy", 10, true)
            .unwrap();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].message, "current style");
    assert_eq!(results[0].source, source);
}

#[test]
fn retrieval_without_bounded_candidates_fails_closed() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("empty.sqlite3");
    let path = fixture(dir.path(), "chat.csv", "known phrase");
    index_csv(&db, "chat-a", &path).unwrap();
    assert!(
        search(&db, Some("chat-a"), None, "completely missing", "vector", 5)
            .unwrap()
            .is_empty()
    );
    assert!(
        search(&db, Some("missing-chat"), None, "query", "vector", 5)
            .unwrap()
            .is_empty()
    );
    assert!(style_search(&db, Some("missing-chat"), "query", 5)
        .unwrap()
        .is_empty());
    assert!(context_reply_bundle(&db, "missing-chat", "query", None)
        .unwrap()
        .context
        .is_empty());
}

#[test]
fn live_schema_bootstrap_is_additive_and_idempotent() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("live-schema.sqlite3");

    ensure_live_context_schema(&db).unwrap();
    ensure_live_context_schema(&db).unwrap();
    assert!(live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, 77)
        .unwrap()
        .is_none());

    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT value FROM context_retrieval_meta WHERE key='live_context_schema'",
            [],
            |row| row.get::<_, String>(0),
        )
        .unwrap(),
        LIVE_CONTEXT_SCHEMA_VERSION
    );
    for table in [
        "context_sources",
        "context_live_events",
        "response_time_samples",
        "owner_recipient_style_samples",
        "owner_recipient_style_profile",
        "context_message_topics",
        "context_topic_stats",
        "context_reference_packs",
    ] {
        assert_eq!(
            conn.query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1",
                [table],
                |row| row.get::<_, i64>(0),
            )
            .unwrap(),
            1
        );
    }
    assert_eq!(
        conn.query_row("SELECT COUNT(*) FROM context_messages", [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap(),
        0
    );
    drop(conn);

    let csv = fixture(dir.path(), "legacy.csv", "legacy fts marker");
    assert_eq!(index_csv(&db, "legacy-chat", &csv).unwrap(), 1);
    assert_eq!(
        search(&db, Some("legacy-chat"), None, "legacy", "keyword", 5,)
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn auto_reply_startup_allows_dirty_partial_authoritative_identity() {
    let dirty_ok = LiveContextSyncState {
        source: "local-db:test:42".to_string(),
        chat_id: 42,
        chat: "부자멘토멘티".to_string(),
        checkpoint_log_id: 7,
        authoritative: true,
        summary_dirty: true,
        sync_status: "partial".to_string(),
    };
    assert!(dirty_ok.allows_auto_reply_startup(42, "부자멘토멘티"));

    let mut not_auth = dirty_ok.clone();
    not_auth.authoritative = false;
    assert!(!not_auth.allows_auto_reply_startup(42, "부자멘토멘티"));

    let mut bad_sync = dirty_ok.clone();
    bad_sync.sync_status = "stale".to_string();
    assert!(!bad_sync.allows_auto_reply_startup(42, "부자멘토멘티"));

    assert!(!dirty_ok.allows_auto_reply_startup(43, "부자멘토멘티"));
    assert!(!dirty_ok.allows_auto_reply_startup(42, "다른방"));

    let mut clean_ready = dirty_ok.clone();
    clean_ready.summary_dirty = false;
    clean_ready.sync_status = "ready".to_string();
    assert!(clean_ready.allows_auto_reply_startup(42, "부자멘토멘티"));
}

#[test]
fn live_ingest_is_idempotent_and_rolls_back_a_late_conflict() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("live-idempotent.sqlite3");
    let chat_id = 77;
    let chat = "부자멘토멘티";
    let page = vec![
        live_event(chat_id, 1, "민수", "첫 질문", 100),
        live_event(chat_id, 2, STYLE_USER, "좋아요", 110),
    ];

    let first = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &page,
        true,
        false,
    )
    .unwrap();
    assert_eq!(first.inserted_events, 2);
    assert_eq!(first.duplicate_events, 0);
    assert_eq!(first.checkpoint_log_id, 2);
    assert!(first.summary_refreshed);

    let replay = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &page,
        true,
        false,
    )
    .unwrap();
    assert_eq!(replay.inserted_events, 0);
    assert_eq!(replay.duplicate_events, 2);
    assert_eq!(replay.checkpoint_log_id, 2);

    let mut changed = page.clone();
    changed[0].message = "바뀐 질문".to_string();
    assert!(ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &changed,
        true,
        false,
    )
    .unwrap_err()
    .to_string()
    .contains("digest mismatch"));

    let source = live_context_source_id(TEST_ACCOUNT_FINGERPRINT, chat_id).unwrap();
    let future = live_event(chat_id, 4, "민수", "원본", 130);
    let conn = open_db(&db).unwrap();
    conn.execute(
        "INSERT INTO context_live_events(
                source, chat_id, log_id, sent_at, sender_name, message_digest,
                disposition, auto_generated, context_message_id, style_message_id, created_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'context', 0, NULL, NULL, ?7)",
        params![
            source,
            chat_id,
            future.log_id,
            future.sent_at,
            future.sender_name,
            live_event_digest(&future).unwrap(),
            Utc::now().to_rfc3339(),
        ],
    )
    .unwrap();
    drop(conn);

    let conflict_page = vec![
        live_event(chat_id, 3, "민수", "롤백 표식", 120),
        live_event(chat_id, 4, "민수", "변경된 원본", 130),
    ];
    assert!(ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        2,
        &conflict_page,
        false,
        false,
    )
    .unwrap_err()
    .to_string()
    .contains("digest mismatch"));
    assert_eq!(
        live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, chat_id)
            .unwrap()
            .unwrap()
            .checkpoint_log_id,
        2
    );
    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM context_live_events WHERE source=?1 AND log_id=3",
            [&source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM context_messages WHERE source=?1 AND message='롤백 표식'",
            [&source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );
}

#[test]
fn live_ingest_prefix_checkpoint_cannot_skip_a_deferred_self_row() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("live-deferred-prefix.sqlite3");
    let chat_id = 78;
    let chat = "부자멘토멘티";

    let prefix = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &[live_event(chat_id, 10, "민수", "직전 질문", 1_000)],
        false,
        false,
    )
    .unwrap();
    assert_eq!(prefix.checkpoint_log_id, 10);

    let skipped = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        20,
        &[live_event(chat_id, 30, "민수", "다음 질문", 1_020)],
        true,
        false,
    )
    .unwrap_err();
    assert!(skipped
        .to_string()
        .contains("checkpoint is behind the caller checkpoint"));
    assert_eq!(
        live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, chat_id)
            .unwrap()
            .unwrap()
            .checkpoint_log_id,
        10
    );

    let mut deferred_self = live_event(chat_id, 20, STYLE_USER, "확정된 직접 답변", 1_010);
    deferred_self.is_self = true;
    let resumed = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        10,
        &[deferred_self],
        false,
        false,
    )
    .unwrap();
    assert_eq!(resumed.checkpoint_log_id, 20);
}

#[test]
fn live_ingest_carries_bursts_across_pages_and_excludes_automatic_self_rows() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("live-pages.sqlite3");
    let chat_id = 88;
    let chat = "부자멘토멘티";

    let partial = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &[live_event(chat_id, 10, "민수", "페이지 경계 질문", 1_000)],
        false,
        false,
    )
    .unwrap();
    assert_eq!(partial.checkpoint_log_id, 10);
    let partial_state = live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, chat_id)
        .unwrap()
        .unwrap();
    assert_eq!(partial_state.sync_status, "partial");

    let complete = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        10,
        &[live_event(chat_id, 20, STYLE_USER, "경계도 좋아요", 1_012)],
        true,
        false,
    )
    .unwrap();
    assert_eq!(complete.response_samples, 1);
    assert_eq!(complete.recipient_style_samples, 1);
    assert!(complete.summary_refreshed);
    let stats = response_time_stats(&db, chat, STYLE_USER, Some(&complete.source))
        .unwrap()
        .unwrap();
    assert_eq!(stats.sample_count, 1);
    assert_eq!(stats.average_seconds, 12.0);
    let recipient = recipient_style_profile(&db, chat, "민수", Some(&complete.source))
        .unwrap()
        .unwrap();
    assert_eq!(recipient.direct_sample_count, 1);
    assert!(recipient.used_fallback);

    let mut automatic = live_event(chat_id, 40, STYLE_USER, "자동출력표식", 2_010);
    automatic.auto_generated = true;
    let excluded = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        20,
        &[
            live_event(chat_id, 30, "민수", "자동 직전 질문", 2_000),
            automatic,
        ],
        true,
        false,
    )
    .unwrap();
    assert_eq!(excluded.indexed_messages, 1);
    assert_eq!(excluded.style_messages, 0);
    assert_eq!(excluded.response_samples, 0);
    assert_eq!(excluded.recipient_style_samples, 0);
    assert!(search(
        &db,
        Some(chat),
        Some(&excluded.source),
        "자동출력표식",
        "keyword",
        5,
    )
    .unwrap()
    .is_empty());
    assert_eq!(
        response_time_stats(&db, chat, STYLE_USER, Some(&excluded.source))
            .unwrap()
            .unwrap()
            .sample_count,
        1
    );
    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM context_live_events
                 WHERE source=?1 AND log_id=40 AND auto_generated=1
                   AND context_message_id IS NULL AND style_message_id IS NULL",
            [&excluded.source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        1
    );
}

#[test]
fn live_ingest_does_not_treat_a_nickname_collision_as_self() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("live-nickname-collision.sqlite3");
    let chat_id = 89;
    let mut collision = live_event(chat_id, 1, STYLE_USER, "동명이인 메시지", 1_000);
    collision.is_self = false;
    let result = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        "부자멘토멘티",
        0,
        &[collision],
        true,
        false,
    )
    .unwrap();
    assert_eq!(result.indexed_messages, 1);
    assert_eq!(result.style_messages, 0);
    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM owner_style WHERE source=?1",
            [&result.source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );

    let mut unresolved = live_event(chat_id, 2, STYLE_USER, "확정 전 출력", 1_010);
    unresolved.is_self = true;
    unresolved.exclude_from_learning = true;
    let excluded = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        "부자멘토멘티",
        1,
        &[unresolved],
        true,
        false,
    )
    .unwrap();
    assert_eq!(excluded.indexed_messages, 0);
    assert_eq!(excluded.style_messages, 0);
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM context_live_events
                 WHERE source=?1 AND log_id=2 AND disposition='ambiguous_auto_candidate'
                   AND auto_generated=0 AND context_message_id IS NULL
                   AND style_message_id IS NULL",
            [&result.source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        1
    );
}

#[test]
fn promotion_requires_complete_summaries_and_is_transactional() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("live-promotion-rollback.sqlite3");
    let chat_id = 99;
    let page = vec![
        live_event(chat_id, 1, "민수", "질문", 100),
        live_event(chat_id, 2, STYLE_USER, "좋아요", 110),
    ];
    assert!(ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        "방",
        0,
        &page,
        true,
        true,
    )
    .unwrap_err()
    .to_string()
    .contains("lacks required"));
    assert!(
        live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, chat_id)
            .unwrap()
            .is_none()
    );
    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row("SELECT COUNT(*) FROM context_messages", [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap(),
        0
    );
}

#[test]
fn authoritative_live_source_and_recipient_bundle_use_the_right_register() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("recipient-bundle.sqlite3");
    let legacy = dir.path().join("legacy.csv");
    fs::write(
        &legacy,
        "Date,User,Message\n2026-01-01,민수,legacytoken\n2026-01-01,최연우,예전 말투예요\n",
    )
    .unwrap();
    index_csv(&db, "부자멘토멘티", &legacy).unwrap();

    let chat_id = 123;
    let events = vec![
        live_event(chat_id, 1, "민수", "시장 질문 알파", 100),
        live_event(chat_id, 2, STYLE_USER, "알파는 좋아요", 110),
        live_event(chat_id, 3, "민수", "시장 질문 베타", 200),
        live_event(chat_id, 4, STYLE_USER, "베타도 봐요", 210),
        live_event(chat_id, 5, "민수", "시장 질문 감마", 300),
        live_event(chat_id, 6, STYLE_USER, "감마도 좋아요", 310),
        live_event(chat_id, 7, "영희", "시장 질문 델타", 400),
        live_event(chat_id, 8, STYLE_USER, "델타는 천천히요", 410),
        live_event(chat_id, 9, "민수", "현재질문표식", 500),
    ];
    let ingested = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        "부자멘토멘티",
        0,
        &events,
        true,
        true,
    )
    .unwrap();
    assert!(ingested.authoritative);
    assert!(
        search(&db, Some("부자멘토멘티"), None, "legacytoken", "keyword", 5,)
            .unwrap()
            .is_empty()
    );
    let live = search(
        &db,
        Some("부자멘토멘티"),
        None,
        "현재질문표식",
        "keyword",
        5,
    )
    .unwrap();
    assert_eq!(live.len(), 1);
    assert_eq!(live[0].source, ingested.source);
    assert_eq!(
        style_profile(&db, "부자멘토멘티", STYLE_USER, None)
            .unwrap()
            .unwrap()
            .source,
        ingested.source
    );
    assert_eq!(
        response_time_stats(&db, "부자멘토멘티", STYLE_USER, None)
            .unwrap()
            .unwrap()
            .sample_count,
        4
    );

    let direct = recipient_style_profile(&db, "부자멘토멘티", "민수", None)
        .unwrap()
        .unwrap();
    assert_eq!(direct.direct_sample_count, 3);
    assert_eq!(direct.confidence_sum, 3.0);
    assert!(!direct.used_fallback);
    let fallback = recipient_style_profile(&db, "부자멘토멘티", "영희", None)
        .unwrap()
        .unwrap();
    assert_eq!(fallback.direct_sample_count, 1);
    assert!(fallback.used_fallback);
    assert_eq!(fallback.profile.sample_count, 4);

    let conn = open_db_readonly(&db).unwrap();
    let mut plan_stmt = conn
        .prepare(&format!("EXPLAIN QUERY PLAN {RECIPIENT_STYLE_SEARCH_SQL}"))
        .unwrap();
    let plan = plan_stmt
        .query_map(
            params![
                &ingested.source,
                "민수",
                "부자멘토멘티",
                STYLE_USER,
                STYLE_POLICY_VERSION,
                STYLE_VECTOR_CANDIDATE_CAP as i64,
            ],
            |row| row.get::<_, String>(3),
        )
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap()
        .join("\n");
    assert!(plan.contains("idx_recipient_style_samples_lookup"));
    assert!(!plan.contains("SCAN styles"));
    assert!(!plan.contains("USE TEMP B-TREE"));

    let direct_bundle = context_reply_bundle_for_recipient_excluding_live_events(
        &db,
        "부자멘토멘티",
        "현재질문표식",
        None,
        "민수",
        chat_id,
        &[9],
    )
    .unwrap();
    assert_eq!(
        direct_bundle.schema_version,
        RECIPIENT_CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION
    );
    assert!(direct_bundle.context.is_empty());
    assert!(direct_bundle
        .recipient_style_profile
        .as_ref()
        .is_some_and(|profile| !profile.used_fallback));
    assert_eq!(direct_bundle.styles.len(), 3);
    assert!(direct_bundle
        .styles
        .iter()
        .all(|style| style.mode == "vector_style_recipient"));
    assert!(!direct_bundle
        .styles
        .iter()
        .any(|style| style.message.contains("델타")));
    assert!(direct_bundle
        .styles
        .iter()
        .all(|style| style.source.starts_with("source:")));

    let fallback_bundle = context_reply_bundle_for_recipient_excluding_live_events(
        &db,
        "부자멘토멘티",
        "현재질문표식",
        None,
        "영희",
        chat_id,
        &[9],
    )
    .unwrap();
    assert!(fallback_bundle
        .recipient_style_profile
        .as_ref()
        .is_some_and(|profile| profile.used_fallback));
    assert!(fallback_bundle
        .styles
        .iter()
        .any(|style| style.message.contains("델타")));
    assert!(fallback_bundle
        .styles
        .iter()
        .all(|style| style.mode == "vector_style"));

    let json = context_reply_bundle_for_recipient_excluding_live_events_json(
        &db,
        "부자멘토멘티",
        "현재질문표식",
        None,
        "민수",
        chat_id,
        &[9],
    )
    .unwrap();
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(
        value["schema_version"],
        RECIPIENT_CONTEXT_REPLY_BUNDLE_SCHEMA_VERSION
    );
    assert_eq!(value["recipient"], "민수");
    assert!(value.get("recipient_style_profile").is_some());
    assert_eq!(value.as_object().unwrap().len(), 8);
    let state = live_context_sync_state(&db, TEST_ACCOUNT_FINGERPRINT, chat_id)
        .unwrap()
        .unwrap();
    assert_eq!(state.checkpoint_log_id, 9);
    assert!(state.authoritative);
    assert_eq!(state.sync_status, "ready");
}

#[test]
fn automatic_self_event_matching_is_exact_bounded_and_fail_closed() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("self-classification.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let timestamp = |seconds| {
        chrono::DateTime::<Utc>::from_timestamp(seconds, 0)
            .unwrap()
            .to_rfc3339()
    };
    record_sent_reply(&db, "unique", "방", "유일 답변", &timestamp(1_000));
    record_sent_reply(&db, "reused", "방", "재사용 답변", &timestamp(2_000));
    record_sent_reply(&db, "many-a", "방", "중복 답변", &timestamp(3_000));
    record_sent_reply(&db, "many-b", "방", "중복 답변", &timestamp(3_010));
    record_sent_reply(&db, "malformed", "방", "시간 오류", "not-a-time");

    let outgoing = vec![
        OutgoingSelfEvent {
            chat_id: 7,
            log_id: 1,
            message: "유일 답변".into(),
            sent_at: 1_020,
        },
        OutgoingSelfEvent {
            chat_id: 7,
            log_id: 2,
            message: "재사용 답변".into(),
            sent_at: 1_990,
        },
        OutgoingSelfEvent {
            chat_id: 7,
            log_id: 3,
            message: "재사용 답변".into(),
            sent_at: 2_010,
        },
        OutgoingSelfEvent {
            chat_id: 7,
            log_id: 4,
            message: "중복 답변".into(),
            sent_at: 3_005,
        },
        OutgoingSelfEvent {
            chat_id: 7,
            log_id: 5,
            message: "시간 오류".into(),
            sent_at: 4_000,
        },
        OutgoingSelfEvent {
            chat_id: 7,
            log_id: 6,
            message: "사람이 직접 쓴 말".into(),
            sent_at: 5_000,
        },
    ];
    let classified = classify_auto_generated_self_events(&db, "방", &outgoing, &[]).unwrap();
    assert_eq!(classified.len(), outgoing.len());
    assert!(classified[0].auto_generated);
    assert_eq!(classified[0].matched_event_id.as_deref(), Some("unique"));
    assert_eq!(classified[0].reason, "unique_exact_sent_match");
    assert!(!classified[1].auto_generated);
    assert!(!classified[2].auto_generated);
    assert_eq!(classified[1].reason, "sent_match_reused_in_batch");
    assert_eq!(classified[2].reason, "sent_match_reused_in_batch");
    assert!(!classified[3].auto_generated);
    assert_eq!(classified[3].reason, "multiple_sent_matches");
    assert!(!classified[4].auto_generated);
    assert_eq!(classified[4].reason, "unparseable_candidate_time");
    assert!(!classified[5].auto_generated);
    assert_eq!(classified[5].reason, "no_exact_sent_match");

    let outside_window = [OutgoingSelfEvent {
        chat_id: 7,
        log_id: 7,
        message: "유일 답변".into(),
        sent_at: 1_121,
    }];
    assert!(
        !classify_auto_generated_self_events(&db, "방", &outside_window, &[]).unwrap()[0]
            .auto_generated
    );
}

#[test]
fn ambiguous_automatic_candidates_never_feed_context_style_or_timing() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("ambiguous-self-classification.sqlite3");
    let chat = "부자멘토멘티";
    let chat_id = 91;
    let now = Utc::now().timestamp();
    ensure_live_context_schema(&db).unwrap();

    let statuses = [
        "scheduled",
        "sending",
        "accepted_unconfirmed",
        "delivery_unknown",
        "reconcile_required",
    ];
    let outgoing = statuses
        .iter()
        .enumerate()
        .map(|(index, status)| OutgoingSelfEvent {
            chat_id,
            log_id: index as i64 + 2,
            message: format!("자동 후보 {status}"),
            sent_at: now,
        })
        .collect::<Vec<_>>();

    let before_projection = classify_auto_generated_self_events(&db, chat, &outgoing, &[]).unwrap();
    assert!(before_projection.iter().all(|item| {
        !item.auto_generated
            && item.matched_event_id.is_none()
            && item.reason == "no_exact_sent_match"
    }));

    for (index, status) in statuses.iter().enumerate() {
        let event_id = format!("ambiguous-{index}");
        let record = serde_json::json!({
            "event_id": event_id,
            "chat": chat,
            "author": "민수",
            "received_at": chrono::DateTime::<Utc>::from_timestamp(now, 0)
                .unwrap()
                .to_rfc3339(),
            "message": format!("incoming {index}"),
            "decision": "reply",
            "reason": "direct_question",
            "category": "question",
            "context_match_count": 1,
            "style_match_count": 1,
            "best_context_score": 0.5,
            "best_style_score": 0.5,
            "prior_similarity": 0.0,
            "scheduled_delay_seconds": 0.0,
            "status": status,
            "reply": outgoing[index].message,
            "evidence_ids": ["context:test"],
            "style_policy_version": STYLE_POLICY_VERSION,
        });
        assert!(record_reply_decision(&db, &record.to_string()).unwrap());
    }
    assert!(update_reply_decision(&db, "ambiguous-0", "delivery_unknown", None, None,).unwrap());

    let classified = classify_auto_generated_self_events(&db, chat, &outgoing, &[]).unwrap();
    assert_eq!(classified.len(), statuses.len());
    for (index, item) in classified.iter().enumerate() {
        assert!(!item.auto_generated);
        assert_eq!(item.reason, "ambiguous_auto_candidate");
        assert_eq!(
            item.matched_event_id.as_deref(),
            Some(format!("ambiguous-{index}").as_str())
        );
    }
    let mut outside_window = outgoing[1].clone();
    outside_window.log_id = 100;
    outside_window.sent_at = now + AUTO_GENERATED_MATCH_WINDOW_SECONDS + 10;
    let outside = classify_auto_generated_self_events(&db, chat, &[outside_window], &[]).unwrap();
    assert!(!outside[0].auto_generated);
    assert_eq!(outside[0].reason, "no_exact_sent_match");

    let mut live_events = vec![live_event(chat_id, 1, "민수", "직전 질문", now - 10)];
    live_events.extend(outgoing.iter().zip(&classified).map(|(event, item)| {
        let mut live = live_event(
            event.chat_id,
            event.log_id,
            STYLE_USER,
            &event.message,
            event.sent_at,
        );
        live.exclude_from_learning = !item.auto_generated && item.reason != "no_exact_sent_match";
        live.auto_generated = item.auto_generated;
        live
    }));
    let result = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &live_events,
        true,
        false,
    )
    .unwrap();
    assert_eq!(result.indexed_messages, 1);
    assert_eq!(result.style_messages, 0);
    assert_eq!(result.response_samples, 0);
    assert_eq!(result.recipient_style_samples, 0);

    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM context_live_events
                 WHERE source=?1 AND disposition='ambiguous_auto_candidate'
                   AND auto_generated=0 AND context_message_id IS NULL
                   AND style_message_id IS NULL",
            [&result.source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        statuses.len() as i64
    );
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM response_time_samples WHERE source=?1",
            [&result.source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );
}
#[test]
fn queue_confirmed_send_excludes_a_loopback_self_row() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("queue-send.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let chat_id = 7;
    let chat = "방";
    let text = "제목은 폭탄인데 내용은 지원이네요";
    assert!(classify_style_message(text).style_eligible);
    let sent = ConfirmedSelfSend {
        event_id: "queue:417780809780519:1".into(),
        text: text.into(),
        sent_at: 1_000,
    };
    let outgoing = vec![OutgoingSelfEvent {
        chat_id,
        log_id: 11,
        message: text.into(),
        sent_at: 1_004,
    }];
    let classified =
        classify_auto_generated_self_events(&db, chat, &outgoing, std::slice::from_ref(&sent))
            .unwrap();
    assert!(classified[0].auto_generated);
    assert_eq!(classified[0].reason, "unique_exact_sent_match");
    assert_eq!(
        classified[0].matched_event_id.as_deref(),
        Some(sent.event_id.as_str())
    );

    let mut events = vec![live_event(
        chat_id,
        10,
        "민수",
        "지원은 어디까지 해봤어",
        990,
    )];
    let mut loopback = live_event(chat_id, 11, STYLE_USER, text, 1_004);
    loopback.auto_generated = classified[0].auto_generated;
    events.push(loopback);
    let result = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &events,
        true,
        false,
    )
    .unwrap();
    assert_eq!(result.style_messages, 0);
    assert_eq!(result.response_samples, 0);
    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM owner_style WHERE chat=?1 AND message=?2",
            params![chat, text],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM context_live_events WHERE disposition='auto_generated'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        1
    );
    drop(conn);

    // The same text without a delivered send stays an owner sample.
    let unmatched = classify_auto_generated_self_events(&db, chat, &outgoing, &[]).unwrap();
    assert!(!unmatched[0].auto_generated);
    assert_eq!(unmatched[0].reason, "no_exact_sent_match");
}

#[test]
fn queue_and_decision_sends_must_agree_on_one_identity() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("self-classification-conflict.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let timestamp = |seconds| {
        chrono::DateTime::<Utc>::from_timestamp(seconds, 0)
            .unwrap()
            .to_rfc3339()
    };
    record_sent_reply(&db, "decided", "방", "같은 문장", &timestamp(1_000));
    let outgoing = vec![OutgoingSelfEvent {
        chat_id: 7,
        log_id: 21,
        message: "같은 문장".into(),
        sent_at: 1_001,
    }];
    let queue = ConfirmedSelfSend {
        event_id: "queue:9".into(),
        text: "같은 문장".into(),
        sent_at: 1_002,
    };
    let classified =
        classify_auto_generated_self_events(&db, "방", &outgoing, std::slice::from_ref(&queue))
            .unwrap();
    assert!(!classified[0].auto_generated);
    assert_eq!(classified[0].reason, "multiple_sent_matches");
    assert!(classified[0].matched_event_id.is_none());
}

#[test]
fn queue_loader_reads_only_terminal_sent_jobs() {
    let dir = tempdir().unwrap();
    let queue = dir.path().join("reply-queue.sqlite3");
    {
        let conn = Connection::open(&queue).unwrap();
        conn.execute_batch(
            "CREATE TABLE reply_jobs(event_id TEXT, status TEXT, reply TEXT, updated_at REAL);",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO reply_jobs VALUES('a','sent','보낸 문장',1000.4)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO reply_jobs VALUES('b','skipped','버려진 문장',1001.0)",
            [],
        )
        .unwrap();
        conn.execute("INSERT INTO reply_jobs VALUES('c','sent','   ',1002.0)", [])
            .unwrap();
        conn.execute(
            "INSERT INTO reply_jobs VALUES('d','sent','시간 없음',NULL)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO reply_jobs VALUES('e','delivery_unknown','불확실',1003.0)",
            [],
        )
        .unwrap();
    }
    let sends = confirmed_self_sends_from_queue(&queue).unwrap();
    assert_eq!(sends.len(), 1);
    assert_eq!(sends[0].event_id, "a");
    assert_eq!(sends[0].text, "보낸 문장");
    assert_eq!(sends[0].sent_at, 1_000);

    let without_jobs = dir.path().join("other-queue.sqlite3");
    Connection::open(&without_jobs)
        .unwrap()
        .execute_batch("CREATE TABLE unrelated(x)")
        .unwrap();
    assert!(confirmed_self_sends_from_queue(&without_jobs)
        .unwrap()
        .is_empty());
    assert!(
        confirmed_self_sends_from_queue(&dir.path().join("absent.sqlite3"))
            .unwrap()
            .is_empty()
    );
}

#[test]
fn repair_reclassifies_bot_sent_style_rows_and_rebuilds_aggregates() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("repair.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let chat_id = 7;
    let chat = "방";
    let bot_text = "제목은 폭탄인데 내용은 지원이네요";
    let events = vec![
        live_event(chat_id, 1, "민수", "오늘 뭐 해", 1_000),
        live_event(chat_id, 2, STYLE_USER, "그냥 집에 있었어", 1_030),
        live_event(chat_id, 3, "민수", "지원은 어디까지 해봤어", 2_000),
        live_event(chat_id, 4, STYLE_USER, bot_text, 2_050),
    ];
    let ingested = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &events,
        true,
        false,
    )
    .unwrap();
    assert_eq!(ingested.style_messages, 2);
    assert_eq!(ingested.response_samples, 2);
    let eligible_before = {
        let conn = open_db_readonly(&db).unwrap();
        conn.query_row(
            "SELECT COUNT(*) FROM owner_style WHERE style_eligible=1",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap()
    };
    assert_eq!(eligible_before, 2);

    let bot_style_id: i64 = {
        let conn = open_db_readonly(&db).unwrap();
        conn.query_row(
            "SELECT id FROM owner_style WHERE message=?1",
            params![bot_text],
            |row| row.get::<_, i64>(0),
        )
        .unwrap()
    };
    let sends = vec![ConfirmedSelfSend {
        event_id: "queue:2".into(),
        text: bot_text.into(),
        sent_at: 2_051,
    }];

    let dry = repair_bot_sent_style_samples(&db, chat, chat_id, &sends, true).unwrap();
    assert_eq!(dry.matched_rows, 1);
    assert_eq!(dry.already_ineligible, 0);
    assert!(dry.dry_run);
    {
        let conn = open_db_readonly(&db).unwrap();
        assert_eq!(
            conn.query_row(
                "SELECT COUNT(*) FROM owner_style WHERE content_kind='bot_sent_reply'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .unwrap(),
            0
        );
        assert_eq!(
            conn.query_row("SELECT sample_count FROM owner_style_profile", [], |row| {
                row.get::<_, i64>(0)
            },)
                .unwrap(),
            eligible_before
        );
    }

    let report = repair_bot_sent_style_samples(&db, chat, chat_id, &sends, false).unwrap();
    assert_eq!(report.matched_rows, 1);
    assert_eq!(report.already_ineligible, 0);
    assert_eq!(report.ambiguous_rows, 0);
    assert_eq!(report.deleted_response_samples, 1);
    assert_eq!(report.rebuilt_sources.len(), 1);
    assert_eq!(report.matches[0].offset_seconds, -1);
    {
        let conn = open_db_readonly(&db).unwrap();
        let (kind, eligible) = conn
            .query_row(
                "SELECT content_kind, style_eligible FROM owner_style WHERE message=?1",
                params![bot_text],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)),
            )
            .unwrap();
        assert_eq!(kind, "bot_sent_reply");
        assert_eq!(eligible, 0);
        assert_eq!(
            conn.query_row("SELECT sample_count FROM owner_style_profile", [], |row| {
                row.get::<_, i64>(0)
            },)
                .unwrap(),
            eligible_before - 1
        );
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM response_time_samples", [], |row| row
                .get::<_, i64>(
                0
            ),)
                .unwrap(),
            1
        );
        assert_eq!(
            conn.query_row(
                "SELECT COUNT(*) FROM owner_recipient_style_samples WHERE style_message_id = ?1",
                params![bot_style_id],
                |row| row.get::<_, i64>(0),
            )
            .unwrap(),
            0
        );
    }

    // Idempotent: the second pass only sees an already ineligible row.
    let again = repair_bot_sent_style_samples(&db, chat, chat_id, &sends, false).unwrap();
    assert_eq!(again.matched_rows, 1);
    assert_eq!(again.already_ineligible, 1);
    assert!(again.rebuilt_sources.is_empty());
}
#[test]
fn csv_import_does_not_learn_a_confirmed_bot_reply() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("csv-bot-send.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let timestamp = |seconds: i64| {
        chrono::DateTime::<Utc>::from_timestamp(seconds, 0)
            .unwrap()
            .to_rfc3339()
    };
    let sent_at = chrono::NaiveDateTime::parse_from_str("2026-01-01 10:00:00", "%Y-%m-%d %H:%M:%S")
        .unwrap()
        .and_utc()
        .timestamp();
    record_sent_reply(
        &db,
        "bot-1",
        "부자멘토멘티",
        "제목은 폭탄인데 내용은 지원이네요",
        &timestamp(sent_at),
    );
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
2026-01-01 09:59:30,문승현,기사 봤어\n\
2026-01-01 10:00:04,최연우,제목은 폭탄인데 내용은 지원이네요\n\
2026-01-01 11:00:00,최연우,그건 좀 세긴 하네\n",
    )
    .unwrap();
    assert_eq!(index_csv(&db, "부자멘토멘티", &path).unwrap(), 3);

    let conn = open_db_readonly(&db).unwrap();
    let (kind, eligible): (String, i64) = conn
        .query_row(
            "SELECT content_kind, style_eligible FROM owner_style WHERE message=?1",
            params!["제목은 폭탄인데 내용은 지원이네요"],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(kind, "bot_sent_reply");
    assert_eq!(eligible, 0);
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM owner_style WHERE style_eligible=1",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        1
    );
    let sample_count: i64 = conn
        .query_row(
            "SELECT sample_count FROM owner_style_profile WHERE chat=?1",
            params!["부자멘토멘티"],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(sample_count, 1);
}

#[test]
fn csv_import_excludes_a_queue_only_confirmed_send() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("csv-queue-send.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let reply = "역시 세긴 하네요";
    let sent_at = chrono::NaiveDateTime::parse_from_str("2026-01-01 10:00:04", "%Y-%m-%d %H:%M:%S")
        .unwrap()
        .and_utc()
        .timestamp();
    let queue = dir.path().join("reply-queue.sqlite3");
    {
        let conn = Connection::open(&queue).unwrap();
        conn.execute_batch(
            "CREATE TABLE reply_jobs(event_id TEXT, status TEXT, reply TEXT, updated_at REAL);",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO reply_jobs VALUES('queue-only','sent',?1,?2)",
            params![reply, sent_at as f64],
        )
        .unwrap();
    }
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
2026-01-01 10:00:00,문승현,그건 좀 세네\n\
2026-01-01 10:00:04,최연우,역시 세긴 하네요\n",
    )
    .unwrap();
    // No decision-ledger row exists for this reply: only the room queue
    // proves the assistant wrote it.
    assert_eq!(
        index_csv_with_queue(&db, "부자멘토멘티", &path, Some(&queue)).unwrap(),
        2
    );
    let conn = open_db_readonly(&db).unwrap();
    let (kind, eligible): (String, i64) = conn
        .query_row(
            "SELECT content_kind, style_eligible FROM owner_style WHERE message=?1",
            params![reply],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(kind, "bot_sent_reply");
    assert_eq!(eligible, 0);
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM owner_style WHERE style_eligible=1",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );
}

#[test]
fn csv_import_does_not_learn_pacing_from_a_bot_reply() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("csv-bot-pacing.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let bot_reply = "역시 세긴 하네요";
    let bot_sent_at =
        chrono::NaiveDateTime::parse_from_str("2026-01-01 10:00:04", "%Y-%m-%d %H:%M:%S")
            .unwrap()
            .and_utc()
            .timestamp();
    record_sent_reply(
        &db,
        "bot-pacing",
        "부자멘토멘티",
        bot_reply,
        &chrono::DateTime::<Utc>::from_timestamp(bot_sent_at, 0)
            .unwrap()
            .to_rfc3339(),
    );
    let path = dir.path().join("chat.csv");
    fs::write(
        &path,
        "Date,User,Message\n\
2026-01-01 09:59:30,문승현,그건 좀 세네\n\
2026-01-01 10:00:04,최연우,역시 세긴 하네요\n\
2026-01-01 10:01:00,문승현,다음 얘기 하자\n\
2026-01-01 10:01:30,최연우,그건 좀 웃기네\n",
    )
    .unwrap();
    index_csv(&db, "부자멘토멘티", &path).unwrap();
    let conn = open_db_readonly(&db).unwrap();
    // Only the genuine human pair (10:01:00 -> 10:01:30) may be measured.
    let sample_count: i64 = conn
        .query_row(
            "SELECT sample_count FROM response_time_stats WHERE chat=?1",
            params!["부자멘토멘티"],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(sample_count, 1);
}

#[test]
fn repair_rebuilds_a_source_whose_matched_rows_were_already_ineligible() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("repair-ineligible.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let chat_id = 7;
    let chat = "방";
    let link = "https://example.test/notice";
    let events = vec![
        live_event(chat_id, 1, "민수", "이거 봐", 1_000),
        live_event(chat_id, 2, STYLE_USER, link, 1_030),
    ];
    let ingested = ingest_live_context_events(
        &db,
        TEST_ACCOUNT_FINGERPRINT,
        chat_id,
        chat,
        0,
        &events,
        true,
        false,
    )
    .unwrap();
    assert_eq!(ingested.style_messages, 1);
    assert_eq!(ingested.response_samples, 1);
    {
        let conn = open_db_readonly(&db).unwrap();
        let eligible: i64 = conn
            .query_row(
                "SELECT style_eligible FROM owner_style WHERE message=?1",
                params![link],
                |row| row.get(0),
            )
            .unwrap();
        // A URL row is style-ineligible but still produced a pacing sample.
        assert_eq!(eligible, 0);
    }
    let sends = vec![ConfirmedSelfSend {
        event_id: "queue:ineligible".into(),
        text: link.into(),
        sent_at: 1_031,
    }];
    let report = repair_bot_sent_style_samples(&db, chat, chat_id, &sends, false).unwrap();
    assert_eq!(report.matched_rows, 1);
    assert_eq!(report.already_ineligible, 1);
    assert_eq!(report.deleted_response_samples, 1);
    assert_eq!(report.rebuilt_sources.len(), 1);
    let conn = open_db_readonly(&db).unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM response_time_samples WHERE chat_id=?1",
            params![chat_id],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0
    );
}

#[test]
fn repair_recomputes_an_imported_source_pacing_aggregate() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("repair-imported.sqlite3");
    ensure_live_context_schema(&db).unwrap();
    let source = "/tmp/legacy-chat.csv";
    let chat = "부자멘토멘티";
    let bot_reply = "역시 세긴 하네요";
    let human_reply = "그건 좀 웃기네";
    let bot_sent_at =
        chrono::NaiveDateTime::parse_from_str("2026-01-01 10:00:04", "%Y-%m-%d %H:%M:%S")
            .unwrap()
            .and_utc()
            .timestamp();
    {
        let conn = open_db(&db).unwrap();
        for (date, user, message) in [
            ("2026-01-01 09:59:30", "문승현", "그건 좀 세네"),
            ("2026-01-01 10:00:04", STYLE_USER, bot_reply),
            ("2026-01-01 10:01:00", "문승현", "다음 얘기 하자"),
            ("2026-01-01 10:01:30", STYLE_USER, human_reply),
        ] {
            conn.execute(
                "INSERT INTO context_messages(source, chat, date, user_name, message, vector)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    source,
                    chat,
                    date,
                    user,
                    message,
                    vector_to_bytes(&encode_vector(message))
                ],
            )
            .unwrap();
        }
        // Both owner rows were eligible when the legacy importer ran, so the
        // style profile starts at two samples even though one is the bot.
        for (index, (date, message)) in [
            ("2026-01-01 10:00:04", bot_reply),
            ("2026-01-01 10:01:30", human_reply),
        ]
        .iter()
        .enumerate()
        {
            conn.execute(
                "INSERT INTO owner_style(source, chat, date, user_name, message, vector,
                        source_row, content_kind, style_eligible, policy_version, features_json)
                     VALUES (?1, ?2, ?3, '최연우', ?4, ?5, ?6, 'ordinary_conversation', 1, ?7, '{}')",
                params![
                    source,
                    chat,
                    date,
                    message,
                    vector_to_bytes(&encode_vector(message)),
                    index as i64 + 1,
                    STYLE_POLICY_VERSION
                ],
            )
            .unwrap();
        }
        conn.execute(
            "INSERT INTO owner_style_profile(
                    chat, source, user_name, sample_count, average_character_length,
                    median_character_length, p90_character_length, casual_ending_count,
                    casual_ending_counts_json, question_count, emoji_count, punctuation_count,
                    common_endings_json, common_tokens_json, policy_version, created_at
                 ) VALUES (?1, ?2, '최연우', 2, 8.0, 8.0, 8.0, 0, '{}', 0, 0, 0, '{}', '{}', ?3, '2026-01-01')",
            params![chat, source, STYLE_POLICY_VERSION],
        )
        .unwrap();
        // The importer wrote the pacing aggregate itself and kept no sample
        // rows: the 34s entry is really the bot, the 30s entry is human.
        conn.execute(
            "INSERT INTO response_time_stats(
                    chat, source, user_name, sample_count, average_seconds, median_seconds,
                    p90_seconds, min_seconds, max_seconds, max_window_seconds, stddev_seconds,
                    distribution_schema_version, distribution_json
                 ) VALUES (?1, ?2, '최연우', 2, 32.0, 32.0, 34.0, 30.0, 34.0, 34.0, 2.0, 0, '{}')",
            params![chat, source],
        )
        .unwrap();
    }
    let sends = vec![ConfirmedSelfSend {
        event_id: "legacy".into(),
        text: bot_reply.into(),
        sent_at: bot_sent_at,
    }];
    let report = repair_bot_sent_style_samples(&db, chat, 7, &sends, false).unwrap();
    assert_eq!(report.matched_rows, 1);
    assert_eq!(report.deleted_response_samples, 0);
    assert_eq!(report.rebuilt_sources, vec![source.to_string()]);
    let conn = open_db_readonly(&db).unwrap();
    // The bot sentence leaves the style profile: two samples become one.
    assert_eq!(
        conn.query_row(
            "SELECT sample_count FROM owner_style_profile WHERE source = ?1",
            params![source],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        1
    );
    // The pacing aggregate keeps only the human pair (30s).
    let (samples, average): (i64, f64) = conn
        .query_row(
            "SELECT sample_count, average_seconds FROM response_time_stats WHERE source = ?1",
            params![source],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(samples, 1);
    assert_eq!(average, 30.0);
    let (kind, eligible): (String, i64) = conn
        .query_row(
            "SELECT content_kind, style_eligible FROM owner_style WHERE message = ?1",
            params![bot_reply],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(kind, "bot_sent_reply");
    assert_eq!(eligible, 0);
}
