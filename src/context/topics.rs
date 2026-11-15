//! The topic lexicon and the `context_message_topics` rows it produces.

use anyhow::Result;
use rusqlite::{params, Connection, OptionalExtension};

pub(super) const TOPIC_LEXICON: &[(&str, &[&str])] = &[
    (
        "contact",
        &[
            "번호",
            "연락처",
            "전화번호",
            "폰번호",
            "핸드폰",
            "휴대폰",
            "전화",
        ],
    ),
    (
        "ax_macos",
        &[
            "ax api",
            "axapi",
            "손쉬운 사용",
            "accessibility",
            "axui",
            "axtextarea",
            "axshowmenu",
        ],
    ),
    (
        "computer_use",
        &[
            "컴퓨터 유즈",
            "computer use",
            "computer-use",
            "스크린샷",
            "픽셀",
            "마우스",
        ],
    ),
    (
        "llm_tools",
        &[
            "토큰",
            "cursor",
            "grok",
            "gemini",
            "claude",
            "chatgpt",
            "가재코드",
            "모델",
        ],
    ),
    (
        "kakao_auto",
        &["자동답", "local-send", "reply-to", "카톡 답", "워커"],
    ),
    (
        "business",
        &[
            "사업자",
            "지원",
            "세금",
            "신청서",
            "사업",
            "창업",
            "매출",
            "영업",
            "법인",
            "스타트업",
        ],
    ),
    ("infra", &["리눅스", "linux", "가상머신"]),
    ("news", &["긱뉴스", "geeknews", "hada.io"]),
    ("identity", &["나임", "연우지", "사람이지"]),
    (
        "stocks",
        &[
            "주식",
            "증권",
            "코스피",
            "코스닥",
            "나스닥",
            "다우",
            "배당",
            "상장",
            "공모주",
            "etf",
            "양도세",
            "매수",
            "매도",
            "주가",
        ],
    ),
    (
        "coins",
        &[
            "코인",
            "비트코인",
            "이더리움",
            "알트",
            "업비트",
            "빗썸",
            "바이낸스",
            "김치프리미엄",
            "김프",
            "btc",
            "eth",
            "blockchain",
            "블록체인",
        ],
    ),
    (
        "investing",
        &[
            "투자",
            "수익률",
            "포트폴리오",
            "자산배분",
            "적립",
            "펀드",
            "재테크",
            "시드",
        ],
    ),
    (
        "real_estate",
        &[
            "부동산",
            "아파트",
            "전세",
            "월세",
            "매매",
            "청약",
            "분양",
            "등기",
            "전세사기",
            "집값",
        ],
    ),
    ("auction", &["경매", "공매", "낙찰", "입찰", "경매물건"]),
    (
        "ai",
        &[
            "인공지능",
            "챗gpt",
            "chatgpt",
            "지피티",
            "llm",
            "그록",
            "grok",
            "클로드",
            "claude",
            "제미니",
            "gemini",
            "머신러닝",
            "딥러닝",
            "openai",
            "anthropic",
        ],
    ),
];

const INTEREST_TOPICS: &[&str] = &[
    "stocks",
    "coins",
    "investing",
    "real_estate",
    "auction",
    "business",
    "ai",
];

const TOPIC_LEXICON_VERSION: &str = "2";

pub(super) fn classify_message_topics(message: &str) -> Vec<&'static str> {
    let text = message.trim();
    if text.is_empty() {
        return Vec::new();
    }
    let lower = text.to_ascii_lowercase();
    let mut topics = Vec::new();
    for (topic, needles) in TOPIC_LEXICON {
        let matched = needles.iter().any(|needle| {
            if needle.bytes().all(|byte| byte.is_ascii()) {
                // See `topic_lexicon_ascii_needles_are_lowercase`: every ASCII
                // needle is already lowercase, so matching the precomputed
                // lowercase haystack avoids one String per needle per message.
                lower.contains(*needle)
            } else {
                text.contains(needle)
            }
        });
        if matched {
            topics.push(*topic);
        }
    }
    topics
}

pub(super) fn message_has_interest_topic(message: &str) -> bool {
    classify_message_topics(message)
        .iter()
        .any(|topic| INTEREST_TOPICS.contains(topic))
}

pub(super) fn ensure_topic_lexicon(tx: &rusqlite::Transaction<'_>) -> Result<()> {
    let current: Option<String> = tx
        .query_row(
            "SELECT value FROM context_retrieval_meta WHERE key = 'topic_lexicon_version'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if current.as_deref() == Some(TOPIC_LEXICON_VERSION) {
        return Ok(());
    }
    backfill_message_topics(tx)?;
    tx.execute(
        "INSERT INTO context_retrieval_meta(key, value)
         VALUES ('topic_lexicon_version', ?1)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [TOPIC_LEXICON_VERSION],
    )?;
    Ok(())
}

pub(super) fn insert_message_topics(
    conn: &Connection,
    message_id: i64,
    chat: &str,
    source: &str,
    date: &str,
    message: &str,
) -> Result<()> {
    if message_id <= 0 {
        return Ok(());
    }
    for topic in classify_message_topics(message) {
        let added = conn.execute(
            "INSERT OR IGNORE INTO context_message_topics(message_id, topic)
             VALUES (?1, ?2)",
            params![message_id, topic],
        )?;
        if added == 0 {
            continue;
        }
        conn.execute(
            "INSERT INTO context_topic_stats(
                chat, source, topic, message_count, last_date
             ) VALUES (?1, ?2, ?3, 1, ?4)
             ON CONFLICT(chat, source, topic) DO UPDATE SET
                message_count = message_count + 1,
                last_date = CASE
                    WHEN excluded.last_date > last_date THEN excluded.last_date
                    ELSE last_date
                END",
            params![chat, source, topic, date],
        )?;
    }
    Ok(())
}

pub(super) fn backfill_message_topics(conn: &Connection) -> Result<()> {
    let mut last_id = 0_i64;
    loop {
        let mut stmt = conn.prepare(
            "SELECT id, chat, source, date, message
             FROM context_messages
             WHERE id > ?1
             ORDER BY id ASC
             LIMIT 1000",
        )?;
        let batch = stmt
            .query_map([last_id], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        drop(stmt);
        if batch.is_empty() {
            break;
        }
        for (id, chat, source, date, message) in batch {
            last_id = id;
            insert_message_topics(conn, id, &chat, &source, &date, &message)?;
        }
    }
    Ok(())
}

pub(super) fn context_topic_tables_ready(conn: &Connection) -> Result<bool> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master
         WHERE type = 'table' AND name = 'context_message_topics'",
        [],
        |row| row.get(0),
    )?;
    Ok(count == 1)
}
