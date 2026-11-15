//! Owner style: per-message style features, the honorific and formality
//! signals, and the stored owner and recipient style profiles.

use super::live::preferred_context_source;
use super::redact::provenance_id;
use super::schema::ensure_retrieval_index_current;
use super::store::open_db_readonly;
use anyhow::{Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use std::collections::BTreeMap;
use std::path::Path;

pub(super) const STYLE_USER: &str = "최연우";

pub const STYLE_POLICY_VERSION: &str = "ordinary-conversation-v3";

pub(super) const RECIPIENT_STYLE_MIN_SAMPLES: usize = 3;

pub(super) const RECIPIENT_STYLE_MIN_CONFIDENCE: f64 = 2.0;

#[derive(Debug, Clone, Serialize)]
pub struct RecipientStyleProfile {
    pub recipient: String,
    pub direct_sample_count: usize,
    pub confidence_sum: f64,
    pub used_fallback: bool,
    /// 존댓말/반말 파생 필드. 종결어미 통계에서 규칙 기반으로 도출한다(R3.3).
    pub honorific: Honorific,
    /// 격식/비격식 파생 필드. 종결어미와 이모지·구두점·축약 토큰 통계를
    /// 결합해 규칙 기반으로 도출한다(R3.3).
    pub formality: Formality,
    pub profile: StyleProfile,
}

/// 최연우가 특정 상대방에게 쓰는 말투가 존댓말인지 반말인지 구분한다(R3.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Honorific {
    /// 존댓말(예: `-요`, `-습니다`).
    Honorific,
    /// 반말(예: `-어`, `-야`).
    Casual,
}

/// 말투가 격식체인지 비격식체인지 구분한다(R3.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Formality {
    /// 격식체(합쇼체 위주, 이모지·축약이 적음).
    Formal,
    /// 비격식체(해요체·반말, 이모지·축약이 섞임).
    Informal,
}

/// 종결어미 하나의 분류. 존댓말/격식 파생에만 쓰는 내부 신호다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StyleEndingClass {
    /// 합쇼체 종결어미(`습니다`/`합니다`/`됩니다`) — 격식·존댓말 신호.
    Deferential,
    /// 해요체 종결어미(`요`/`죠`) — 존댓말 신호.
    Honorific,
    /// 웃음·축약 종결(`ㅋㅋㅋ`/`ㅠㅠ` 등) — 비격식 신호.
    Contraction,
    /// 그 밖의 반말 종결(`어`/`아`/`야`/`지` 등).
    Casual,
}

/// 종결어미 문자열을 존댓말/격식 파생용 분류로 매핑한다. 기존 말투 통계에
/// 등장하는 종결어미만 다룬다.
pub fn classify_style_ending_class(ending: &str) -> StyleEndingClass {
    match ending {
        "습니다" | "합니다" | "됩니다" => StyleEndingClass::Deferential,
        "요" | "죠" => StyleEndingClass::Honorific,
        "ㅋㅋㅋ" | "ㅠㅠ" | "ㅜㅜ" | "ㅠ" | "ㅜ" => StyleEndingClass::Contraction,
        _ => StyleEndingClass::Casual,
    }
}

/// 토큰이 웃음·축약 토큰(`ㅋㅋ`, `ㅎㅎ`, `ㅠㅠ`, `ㅜㅜ` 계열)인지 판단한다.
/// 자음/모음 축약만으로 이루어진 토큰을 비격식 신호로 본다.
pub fn is_contraction_token(token: &str) -> bool {
    let trimmed = token.trim();
    !trimmed.is_empty()
        && trimmed
            .chars()
            .all(|ch| matches!(ch, 'ㅋ' | 'ㅎ' | 'ㅠ' | 'ㅜ' | 'ㅡ' | 'ㅗ' | 'ㄷ' | 'ㅇ'))
}

/// 존댓말/격식 파생에 쓰는 규칙 기반 신호 묶음. 종결어미·이모지·구두점·축약
/// 토큰 통계에서만 도출하며 원문은 담지 않는다(R3.3).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct StyleSignals {
    pub sample_count: usize,
    pub honorific_ending_count: usize,
    pub deferential_ending_count: usize,
    pub casual_ending_count: usize,
    pub emoji_count: usize,
    pub punctuation_count: usize,
    pub contraction_count: usize,
}

impl StyleSignals {
    /// 이미 집계된 말투 프로필의 종결어미·이모지·구두점·축약 통계에서 신호를
    /// 뽑아낸다. `common_endings_json`으로 존댓말/반말/격식 종결을 분류하고,
    /// `casual_ending_counts_json`·`common_tokens_json`에서 축약 신호를 더한다.
    pub fn from_style_profile(profile: &StyleProfile) -> Self {
        let mut signals = StyleSignals {
            sample_count: profile.sample_count,
            emoji_count: profile.emoji_count,
            punctuation_count: profile.punctuation_count,
            ..StyleSignals::default()
        };
        if let Ok(map) =
            serde_json::from_str::<BTreeMap<String, usize>>(&profile.common_endings_json)
        {
            for (ending, count) in map {
                match classify_style_ending_class(&ending) {
                    StyleEndingClass::Deferential => signals.deferential_ending_count += count,
                    StyleEndingClass::Honorific => signals.honorific_ending_count += count,
                    StyleEndingClass::Contraction => signals.contraction_count += count,
                    StyleEndingClass::Casual => signals.casual_ending_count += count,
                }
            }
        }
        // 축약/웃음 신호는 별도 통계에서만 추가로 집계한다(반말 종결과 중복 계산 방지).
        if let Ok(map) =
            serde_json::from_str::<BTreeMap<String, usize>>(&profile.casual_ending_counts_json)
        {
            for (ending, count) in map {
                if classify_style_ending_class(&ending) == StyleEndingClass::Contraction {
                    signals.contraction_count += count;
                }
            }
        }
        if let Ok(map) =
            serde_json::from_str::<BTreeMap<String, usize>>(&profile.common_tokens_json)
        {
            for (token, count) in map {
                if is_contraction_token(&token) {
                    signals.contraction_count += count;
                }
            }
        }
        signals
    }
}

/// 규칙 기반으로 존댓말/반말과 격식/비격식을 도출한다(R3.3).
///
/// - 존댓말/반말: 존댓말 종결(해요체+합쇼체) 신호가 반말 종결 신호보다 많으면
///   존댓말로 본다.
/// - 격식/비격식: 존댓말이면서 합쇼체 종결이 있고, 그 합쇼체 신호가
///   비격식 신호(이모지+축약+반말 종결)보다 우세할 때만 격식체로 본다.
///   나머지는 모두 비격식체다(해요체·반말·이모지 섞인 존댓말 포함).
pub fn derive_honorific_formality(signals: &StyleSignals) -> (Honorific, Formality) {
    let honorific_signal = signals.honorific_ending_count + signals.deferential_ending_count;
    let casual_signal = signals.casual_ending_count;
    let honorific = if honorific_signal > casual_signal {
        Honorific::Honorific
    } else {
        Honorific::Casual
    };

    let informal_signal =
        signals.emoji_count + signals.contraction_count + signals.casual_ending_count;
    let formal_signal = signals.deferential_ending_count;
    let formality = if honorific == Honorific::Honorific
        && formal_signal > 0
        && formal_signal >= informal_signal
    {
        Formality::Formal
    } else {
        Formality::Informal
    };

    (honorific, formality)
}

/// 말투 프로필에서 곧바로 존댓말/격식을 도출하는 편의 함수.
pub fn derive_style_profile_honorific_formality(profile: &StyleProfile) -> (Honorific, Formality) {
    derive_honorific_formality(&StyleSignals::from_style_profile(profile))
}

#[derive(Debug, Clone, Serialize)]
pub struct StyleProfile {
    pub chat: String,
    pub source: String,
    pub user: String,
    pub sample_count: usize,
    pub average_character_length: f64,
    pub median_character_length: f64,
    pub p90_character_length: f64,
    pub casual_ending_count: usize,
    pub casual_ending_counts_json: String,
    pub question_count: usize,
    pub emoji_count: usize,
    pub punctuation_count: usize,
    pub common_endings_json: String,
    pub common_tokens_json: String,
    pub policy_version: String,
}

pub(super) fn validate_style_profile_laughter(profile: &StyleProfile, label: &str) -> Result<()> {
    for encoded in [
        &profile.casual_ending_counts_json,
        &profile.common_endings_json,
        &profile.common_tokens_json,
    ] {
        let value: serde_json::Value =
            serde_json::from_str(encoded).with_context(|| format!("{label} JSON is invalid"))?;
        let values = value
            .as_object()
            .ok_or_else(|| anyhow::anyhow!("{label} JSON is not an object"))?;
        if values
            .keys()
            .any(|key| crate::reply_policy::validate_auto_reply_laughter(key).is_err())
        {
            anyhow::bail!("{label} contains disallowed laughter evidence");
        }
    }
    Ok(())
}

#[derive(Debug, Clone)]
pub(super) struct StyleMessageFeatures {
    pub(super) style_eligible: bool,
    pub(super) content_kind: &'static str,
    character_length: usize,
    casual_ending: Option<&'static str>,
    question_count: usize,
    emoji_count: usize,
    punctuation_count: usize,
    pub(super) features_json: String,
}

#[derive(Debug, Default)]
pub(super) struct StyleProfileAccumulator {
    lengths: Vec<usize>,
    casual_endings: BTreeMap<String, usize>,
    endings: BTreeMap<String, usize>,
    question_count: usize,
    emoji_count: usize,
    punctuation_count: usize,
    tokens: BTreeMap<String, usize>,
}

impl StyleProfileAccumulator {
    pub(super) fn add(&mut self, message: &str, features: &StyleMessageFeatures) {
        self.lengths.push(features.character_length);
        if let Some(ending) = features.casual_ending {
            *self.casual_endings.entry(ending.to_string()).or_default() += 1;
        }
        if let Some(ending) = common_style_ending(message) {
            *self.endings.entry(ending.to_string()).or_default() += 1;
        }
        self.question_count += features.question_count;
        self.emoji_count += features.emoji_count;
        self.punctuation_count += features.punctuation_count;
        for token in message.split_whitespace().filter_map(normalize_style_token) {
            *self.tokens.entry(token).or_default() += 1;
        }
    }

    pub(super) fn finish(self, chat: &str, source: &str, user: &str) -> Option<StyleProfile> {
        if self.lengths.is_empty() {
            return None;
        }
        let mut lengths = self.lengths;
        lengths.sort_unstable();
        let percentile = |ratio: f64| {
            let position = (lengths.len() - 1) as f64 * ratio;
            let lower = position.floor() as usize;
            let upper = position.ceil() as usize;
            if lower == upper {
                lengths[lower] as f64
            } else {
                lengths[lower] as f64
                    + (lengths[upper] - lengths[lower]) as f64 * (position - lower as f64)
            }
        };
        let casual_ending_count = self.casual_endings.values().sum();
        let casual_ending_counts_json =
            serde_json::to_string(&top_counts(&self.casual_endings, 12))
                .unwrap_or_else(|_| "{}".into());
        let common_endings_json =
            serde_json::to_string(&top_counts(&self.endings, 12)).unwrap_or_else(|_| "{}".into());
        let common_tokens_json =
            serde_json::to_string(&top_counts(&self.tokens, 32)).unwrap_or_else(|_| "{}".into());
        Some(StyleProfile {
            chat: chat.to_string(),
            source: source.to_string(),
            user: user.to_string(),
            sample_count: lengths.len(),
            average_character_length: lengths.iter().sum::<usize>() as f64 / lengths.len() as f64,
            median_character_length: percentile(0.5),
            p90_character_length: percentile(0.9),
            casual_ending_count,
            casual_ending_counts_json,
            question_count: self.question_count,
            emoji_count: self.emoji_count,
            punctuation_count: self.punctuation_count,
            common_endings_json,
            common_tokens_json,
            policy_version: STYLE_POLICY_VERSION.to_string(),
        })
    }
}

fn top_counts(counts: &BTreeMap<String, usize>, limit: usize) -> BTreeMap<String, usize> {
    let mut values = counts.iter().collect::<Vec<_>>();
    values.sort_by(|(left_name, left_count), (right_name, right_count)| {
        right_count
            .cmp(left_count)
            .then_with(|| left_name.cmp(right_name))
    });
    values
        .into_iter()
        .take(limit)
        .map(|(name, count)| (name.clone(), *count))
        .collect()
}

fn normalize_style_token(token: &str) -> Option<String> {
    let normalized = token
        .trim_matches(|ch: char| {
            ch.is_ascii_punctuation()
                || matches!(
                    ch,
                    '。' | '，' | '！' | '？' | '～' | '…' | '·' | '「' | '」' | '『' | '』'
                )
        })
        .trim();
    (!normalized.is_empty()).then(|| normalized.to_string())
}

fn trim_style_tail(message: &str) -> &str {
    message.trim_end_matches(|ch: char| {
        ch.is_whitespace()
            || is_emoji(ch)
            || ch.is_ascii_punctuation()
            || matches!(ch, '。' | '！' | '？' | '～' | '…')
    })
}

fn style_ending(message: &str) -> Option<&'static str> {
    let message = trim_style_tail(message);
    [
        "잖아",
        "거든",
        "같아",
        "ㅋㅋㅋ",
        "ㅠㅠ",
        "ㅜㅜ",
        "요",
        "죠",
        "네",
        "지",
        "까",
        "어",
        "아",
        "야",
        "래",
        "ㅠ",
        "ㅜ",
    ]
    .into_iter()
    .find(|ending| message.ends_with(ending))
}

fn common_style_ending(message: &str) -> Option<&'static str> {
    let message = trim_style_tail(message);
    [
        "잖아",
        "거든",
        "같아",
        "ㅋㅋㅋ",
        "ㅠㅠ",
        "ㅜㅜ",
        "습니다",
        "합니다",
        "됩니다",
        "요",
        "죠",
        "네",
        "지",
        "까",
        "어",
        "아",
        "야",
        "래",
        "다",
        "해",
        "함",
        "임",
    ]
    .into_iter()
    .find(|ending| message.ends_with(ending))
}

fn is_emoji(ch: char) -> bool {
    matches!(
        ch as u32,
        0x1F000..=0x1FAFF | 0x2600..=0x27BF
    )
}

fn is_generic_ack_style(message: &str) -> bool {
    let remainder: String = message
        .chars()
        .filter(|ch| {
            !ch.is_whitespace()
                && !matches!(
                    *ch,
                    'ㅋ' | 'ㅎ' | 'ㄷ' | ',' | '.' | '!' | '?' | '？' | '～' | '~' | ';'
                )
        })
        .collect();
    matches!(
        remainder.as_str(),
        "" | "ㅇㅇ" | "응" | "네" | "맞아" | "ㅇ" | "음" | "어" | "웅"
    )
}

pub(super) fn is_assistant_tell_style(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    const TELLS: &[&str] = &[
        "답할 수 있어",
        "답할 수 있습니다",
        "도와드릴",
        "무엇을 도와",
        "맥락에 맞게 답",
        "i can help",
        "as an ai",
        "i'm an ai",
        "i am an ai",
    ];
    // Every tell is either non-ASCII (Hangul, untouched by ASCII lowercasing)
    // or already ASCII-lowercase, so `lower.contains(tell)` is exactly the
    // original `message.contains(tell) || lower.contains(tell_lower)` test
    // without allocating a throwaway String per tell.
    TELLS.iter().any(|tell| lower.contains(*tell))
}

fn is_spectator_narration_style(message: &str) -> bool {
    let compact: String = message.chars().filter(|ch| !ch.is_whitespace()).collect();
    compact.ends_with("알아보나보네")
        || compact.ends_with("하나보네")
        || compact.ends_with("인가보네")
        || compact.ends_with("쪽인가보네")
}

pub(super) fn classify_style_message(message: &str) -> StyleMessageFeatures {
    let normalized = message.trim();
    let char_count = normalized.chars().count();
    let lower = normalized.to_ascii_lowercase();
    let lines = normalized.lines().map(str::trim).collect::<Vec<_>>();
    let starts_list = lines.first().is_some_and(|line| {
        line.chars()
            .next()
            .is_some_and(|ch| matches!(ch, '*' | '-' | '•' | '⭐' | '📌' | '#'))
            || line.starts_with("Q.")
            || line.starts_with("A.")
            || line.starts_with('①')
            || line.starts_with('②')
            || line.starts_with('③')
            || (line.chars().next().is_some_and(|ch| ch.is_ascii_digit())
                && line
                    .chars()
                    .nth(1)
                    .is_some_and(|ch| matches!(ch, '.' | ')' | ':')))
    });
    let pasted_markers = [
        "복사",
        "붙여넣",
        "정보 전달",
        "출처",
        "요약",
        "핵심 내용",
        "web발신",
        "공지사항",
        "속보",
        "공유드립니다",
        "전달드립니다",
    ];
    let formal_prefixes = ["공지", "안내", "알려드립니다", "필독", "[공지]", "[안내]"];
    let formal_suffixes = [
        "습니다",
        "니다",
        "합니다",
        "됩니다",
        "바랍니다",
        "드립니다",
        "하십시오",
    ];
    let metadata = normalized.is_empty()
        || normalized.starts_with('@')
        || matches!(normalized, "사진" | "동영상" | "파일" | "이모티콘")
        || (normalized.contains("님이") && normalized.contains("되었습니다"))
        || normalized
            .chars()
            .all(|ch| ch.is_ascii_punctuation() || ch.is_whitespace())
        || normalized.chars().any(|ch| ch.is_ascii_digit())
        || normalized.contains(':');
    let formal_target = trim_style_tail(normalized);
    let (style_eligible, content_kind) =
        if crate::reply_policy::validate_auto_reply_laughter(normalized).is_err() {
            (false, "reply_laughter_policy_excluded")
        } else if char_count < 2 {
            (false, "metadata_or_noise")
        } else if lower.contains("http://") || lower.contains("https://") || lower.contains("www.")
        {
            (false, "url")
        } else if lines.len() > 1 {
            (false, "multiline")
        } else if starts_list {
            (false, "list_or_numbered")
        } else if metadata {
            (false, "metadata_or_noise")
        } else if char_count > 60
            || formal_prefixes
                .iter()
                .any(|prefix| normalized.starts_with(prefix))
            || formal_suffixes
                .iter()
                .any(|suffix| formal_target.ends_with(suffix))
            || normalized.contains("하시기 바랍니다")
        {
            (false, "long_or_formal")
        } else if pasted_markers
            .iter()
            .any(|marker| normalized.contains(marker))
        {
            (false, "pasted_information")
        } else if is_generic_ack_style(normalized) {
            (false, "generic_ack")
        } else if is_assistant_tell_style(normalized) {
            (false, "assistant_tell")
        } else if is_spectator_narration_style(normalized) {
            (false, "spectator_narration")
        } else {
            (true, "ordinary_conversation")
        };
    let question_count = normalized
        .chars()
        .filter(|ch| matches!(ch, '?' | '？'))
        .count();
    let emoji_count = normalized.chars().filter(|ch| is_emoji(*ch)).count();
    let punctuation_count = normalized
        .chars()
        .filter(|ch| {
            ch.is_ascii_punctuation()
                || matches!(
                    ch,
                    '。' | '，' | '！' | '？' | '～' | '…' | '·' | '「' | '」' | '『' | '』'
                )
        })
        .count();
    let casual_ending = style_ending(normalized);
    let features_json = serde_json::json!({
        "character_length": char_count,
        "casual_ending": casual_ending,
        "question_count": question_count,
        "emoji_count": emoji_count,
        "punctuation_count": punctuation_count,
        "content_kind": content_kind,
        "style_eligible": style_eligible,
        "policy_version": STYLE_POLICY_VERSION,
    })
    .to_string();
    StyleMessageFeatures {
        style_eligible,
        content_kind,
        character_length: char_count,
        casual_ending,
        question_count,
        emoji_count,
        punctuation_count,
        features_json,
    }
}

pub(super) fn insert_style_profile(
    conn: &Connection,
    profile: &StyleProfile,
    created_at: &str,
) -> Result<()> {
    conn.execute(
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
            created_at,
        ],
    )?;
    Ok(())
}

pub fn style_profile(
    db_path: &Path,
    chat: &str,
    user: &str,
    source: Option<&str>,
) -> Result<Option<StyleProfile>> {
    let conn = open_db_readonly(db_path)?;
    ensure_retrieval_index_current(&conn)?;
    style_profile_with_connection(&conn, chat, user, source, true)
}

pub(super) fn style_profile_with_connection(
    conn: &Connection,
    chat: &str,
    user: &str,
    source: Option<&str>,
    require_policy: bool,
) -> Result<Option<StyleProfile>> {
    ensure_retrieval_index_current(conn)?;
    let preferred_source = preferred_context_source(conn, Some(chat), source)?;
    let mut stmt = conn.prepare(
        "SELECT chat, source, user_name, sample_count, average_character_length,
                median_character_length, p90_character_length, casual_ending_count,
                casual_ending_counts_json, question_count, emoji_count, punctuation_count,
                common_endings_json, common_tokens_json, policy_version
         FROM owner_style_profile
         WHERE chat = ?1 AND user_name = ?2 AND (?3 IS NULL OR source = ?3)
           AND (?4 = 0 OR policy_version = ?5)
         ORDER BY sample_count DESC, source ASC
         LIMIT 1",
    )?;
    match stmt.query_row(
        params![
            chat,
            user,
            preferred_source.as_deref(),
            require_policy as i64,
            STYLE_POLICY_VERSION
        ],
        |row| {
            Ok(StyleProfile {
                chat: row.get(0)?,
                source: row.get(1)?,
                user: row.get(2)?,
                sample_count: row.get::<_, i64>(3)? as usize,
                average_character_length: row.get(4)?,
                median_character_length: row.get(5)?,
                p90_character_length: row.get(6)?,
                casual_ending_count: row.get::<_, i64>(7)? as usize,
                casual_ending_counts_json: row.get(8)?,
                question_count: row.get::<_, i64>(9)? as usize,
                emoji_count: row.get::<_, i64>(10)? as usize,
                punctuation_count: row.get::<_, i64>(11)? as usize,
                common_endings_json: row.get(12)?,
                common_tokens_json: row.get(13)?,
                policy_version: row.get(14)?,
            })
        },
    ) {
        Ok(profile) => {
            validate_style_profile_laughter(&profile, "style profile")?;
            Ok(Some(profile))
        }
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(error) => Err(error.into()),
    }
}

pub fn recipient_style_profile(
    db_path: &Path,
    chat: &str,
    recipient: &str,
    source: Option<&str>,
) -> Result<Option<RecipientStyleProfile>> {
    let conn = open_db_readonly(db_path)?;
    recipient_style_profile_with_connection(&conn, chat, recipient, source)
}

pub(super) fn recipient_style_profile_with_connection(
    conn: &Connection,
    chat: &str,
    recipient: &str,
    source: Option<&str>,
) -> Result<Option<RecipientStyleProfile>> {
    if chat.trim().is_empty() || recipient.trim().is_empty() {
        anyhow::bail!("recipient style chat and recipient must not be empty");
    }
    ensure_retrieval_index_current(conn)?;
    let preferred_source = preferred_context_source(conn, Some(chat), source)?;
    let has_recipient_table = conn.query_row(
        "SELECT EXISTS(
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'owner_recipient_style_profile'
         )",
        [],
        |row| row.get::<_, i64>(0),
    )? != 0;
    let direct = if has_recipient_table {
        let mut stmt = conn.prepare(
            "SELECT source, sample_count, confidence_sum, average_character_length,
                    median_character_length, p90_character_length, casual_ending_count,
                    casual_ending_counts_json, question_count, emoji_count,
                    punctuation_count, common_endings_json, common_tokens_json,
                    policy_version
             FROM owner_recipient_style_profile
             WHERE chat = ?1 AND recipient = ?2
               AND (?3 IS NULL OR source = ?3)
               AND policy_version = ?4
             ORDER BY sample_count DESC, confidence_sum DESC, source ASC
             LIMIT 1",
        )?;
        stmt.query_row(
            params![
                chat,
                recipient,
                preferred_source.as_deref(),
                STYLE_POLICY_VERSION
            ],
            |row| {
                let source: String = row.get(0)?;
                let sample_count = row.get::<_, i64>(1)? as usize;
                let confidence_sum: f64 = row.get(2)?;
                Ok((
                    sample_count,
                    confidence_sum,
                    StyleProfile {
                        chat: chat.to_string(),
                        source,
                        user: STYLE_USER.to_string(),
                        sample_count,
                        average_character_length: row.get(3)?,
                        median_character_length: row.get(4)?,
                        p90_character_length: row.get(5)?,
                        casual_ending_count: row.get::<_, i64>(6)? as usize,
                        casual_ending_counts_json: row.get(7)?,
                        question_count: row.get::<_, i64>(8)? as usize,
                        emoji_count: row.get::<_, i64>(9)? as usize,
                        punctuation_count: row.get::<_, i64>(10)? as usize,
                        common_endings_json: row.get(11)?,
                        common_tokens_json: row.get(12)?,
                        policy_version: row.get(13)?,
                    },
                ))
            },
        )
        .optional()?
    } else {
        None
    };
    if let Some((_, _, profile)) = direct.as_ref() {
        validate_style_profile_laughter(profile, "recipient style profile")?;
    }
    if let Some((sample_count, confidence_sum, profile)) = direct.as_ref() {
        if *sample_count >= RECIPIENT_STYLE_MIN_SAMPLES
            && confidence_sum.is_finite()
            && *confidence_sum >= RECIPIENT_STYLE_MIN_CONFIDENCE
        {
            let (honorific, formality) = derive_style_profile_honorific_formality(profile);
            return Ok(Some(RecipientStyleProfile {
                recipient: recipient.to_string(),
                direct_sample_count: *sample_count,
                confidence_sum: *confidence_sum,
                used_fallback: false,
                honorific,
                formality,
                profile: profile.clone(),
            }));
        }
    }
    // 최소표본/최소신뢰도 미만이면 방 전체 폴백 프로필을 유지하고, 존댓말/격식은
    // 그 폴백 프로필의 통계에서 그대로 도출한다(R3.3).
    let fallback =
        style_profile_with_connection(conn, chat, STYLE_USER, preferred_source.as_deref(), true)?;
    Ok(fallback.map(|profile| {
        let (honorific, formality) = derive_style_profile_honorific_formality(&profile);
        RecipientStyleProfile {
            recipient: recipient.to_string(),
            direct_sample_count: direct.as_ref().map(|value| value.0).unwrap_or(0),
            confidence_sum: direct.as_ref().map(|value| value.1).unwrap_or(0.0),
            used_fallback: true,
            honorific,
            formality,
            profile,
        }
    }))
}

pub fn recipient_style_profile_json(
    db_path: &Path,
    chat: &str,
    recipient: &str,
    source: Option<&str>,
) -> Result<Option<String>> {
    match recipient_style_profile(db_path, chat, recipient, source)? {
        Some(mut profile) => {
            profile.profile.source = provenance_id(&profile.profile.source);
            Ok(Some(serde_json::to_string(&profile)?))
        }
        None => Ok(None),
    }
}

pub fn style_profile_json(
    db_path: &Path,
    chat: &str,
    user: &str,
    source: Option<&str>,
) -> Result<Option<String>> {
    match style_profile(db_path, chat, user, source)? {
        Some(mut profile) => {
            profile.source = provenance_id(&profile.source);
            Ok(Some(serde_json::to_string(&profile)?))
        }
        None => Ok(None),
    }
}
