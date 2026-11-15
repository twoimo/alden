//! Owner reply timing: response-delay samples, the fitted delay
//! distribution, and the stored `response_time_stats` rows.

use super::live::preferred_context_source;
use super::schema::ensure_retrieval_index_current;
use super::self_send::{match_confirmed_send, ConfirmedSelfSend};
use super::store::open_db_readonly;
use super::style::{
    classify_style_message, insert_style_profile, StyleProfileAccumulator, STYLE_POLICY_VERSION,
    STYLE_USER,
};
use anyhow::Result;
use chrono::{NaiveDateTime, Utc};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::path::Path;

pub(super) const MAX_RESPONSE_DELAY_SECONDS: i64 = 24 * 60 * 60;

pub(super) const RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION: u32 = 2;

pub(super) const RESPONSE_TIME_DISTRIBUTION_POLICY_VERSION: &str =
    "empirical-log1p-three-means-p90-v1";

pub(super) const RESPONSE_TIME_DISTRIBUTION_MODEL_KIND: &str = "bounded-normal-mixture";

pub(super) const RESPONSE_TIME_DISTRIBUTION_FIT_TRANSFORM: &str = "log1p";

pub(super) const RESPONSE_TIME_DISTRIBUTION_COMPONENTS: usize = 3;

pub(super) const RESPONSE_TIME_DISTRIBUTION_MIN_SAMPLES: usize = 32;

pub(super) const RESPONSE_TIME_DISTRIBUTION_MIN_COMPONENT_SAMPLES: usize = 8;

pub(super) const MIN_SCHEDULED_RESPONSE_DELAY_SECONDS: f64 = 5.0;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ResponseTimeComponent {
    pub name: String,
    pub sample_count: usize,
    pub weight: f64,
    pub normal_location_seconds: f64,
    pub normal_scale_seconds: f64,
    pub lower_seconds: f64,
    pub upper_seconds: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ResponseTimeDistribution {
    pub schema_version: u32,
    pub policy_version: String,
    pub model_kind: String,
    pub fit_transform: String,
    pub sample_count: usize,
    pub retained_sample_count: usize,
    pub tail_winsorized_count: usize,
    pub split_seconds: Vec<f64>,
    pub global_upper_seconds: f64,
    pub components: Vec<ResponseTimeComponent>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ResponseTimeStats {
    pub chat: String,
    pub source: String,
    pub user: String,
    pub sample_count: usize,
    pub average_seconds: f64,
    pub median_seconds: f64,
    pub p90_seconds: f64,
    pub min_seconds: f64,
    pub max_seconds: f64,
    pub max_window_seconds: i64,
    pub stddev_seconds: f64,
    pub distribution: Option<ResponseTimeDistribution>,
}

pub(super) fn parse_chat_date(value: &str) -> Option<NaiveDateTime> {
    NaiveDateTime::parse_from_str(value.trim(), "%Y-%m-%d %H:%M:%S")
        .or_else(|_| NaiveDateTime::parse_from_str(value.trim(), "%Y-%m-%d %H:%M"))
        .ok()
}

pub(super) fn is_response_participant(user: &str) -> bool {
    let user = user.trim();
    !user.is_empty()
        && user != STYLE_USER
        && !user.ends_with('봇')
        && !matches!(
            user,
            "드리고" | "뉴스봇" | "채팅봇" | "주식봇" | "날씨날씨" | "인아웃" | "채팅도구"
        )
}

pub(super) fn insert_response_time_stats(
    conn: &Connection,
    stats: &ResponseTimeStats,
) -> Result<()> {
    conn.execute(
        "INSERT INTO response_time_stats(
            chat, source, user_name, sample_count, average_seconds, median_seconds,
            p90_seconds, min_seconds, max_seconds, max_window_seconds, stddev_seconds,
            distribution_schema_version, distribution_json
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
        params![
            stats.chat,
            stats.source,
            stats.user,
            stats.sample_count as i64,
            stats.average_seconds,
            stats.median_seconds,
            stats.p90_seconds,
            stats.min_seconds,
            stats.max_seconds,
            stats.max_window_seconds,
            stats.stddev_seconds,
            stats
                .distribution
                .as_ref()
                .map(|distribution| distribution.schema_version as i64)
                .unwrap_or(0),
            stats
                .distribution
                .as_ref()
                .map(serde_json::to_string)
                .transpose()?
                .unwrap_or_else(|| "{}".to_string()),
        ],
    )?;
    Ok(())
}

/// Recompute the pacing aggregate of an imported (CSV / legacy) source.
///
/// An imported source stores `response_time_stats` directly and keeps no
/// per-reply `response_time_samples` rows, so a bot reply that was measured
/// before the guard existed cannot be removed by deleting samples. The only way
/// to retire its contribution is to rebuild the aggregate from the source's own
/// messages with the same ordering rules the importer uses, skipping every row
/// that a confirmed send identifies as the assistant.
pub(super) fn recompute_imported_response_time_stats(
    conn: &Connection,
    source: &str,
    chat: &str,
    confirmed_sends: &[ConfirmedSelfSend],
) -> Result<usize> {
    let rows = {
        let mut stmt = conn.prepare(
            "SELECT date, user_name, message FROM context_messages
             WHERE source = ?1 AND chat = ?2 ORDER BY date ASC, id ASC",
        )?;
        let mapped = stmt.query_map(params![source, chat], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?;
        mapped.collect::<rusqlite::Result<Vec<_>>>()?
    };
    let mut delays = Vec::new();
    let mut previous_human_at: Option<NaiveDateTime> = None;
    for (date, user, message) in rows {
        let Some(current_at) = parse_chat_date(&date) else {
            continue;
        };
        if user == STYLE_USER {
            let sent_at = current_at.and_utc().timestamp();
            let (matched, ambiguous) =
                match_confirmed_send(confirmed_sends, message.trim(), sent_at);
            if matched.is_some() && !ambiguous {
                // A confirmed assistant reply is not a human response, and the
                // pending prompt it consumed is gone with it.
                previous_human_at = None;
                continue;
            }
            if let Some(previous_at) = previous_human_at.take() {
                let delay = (current_at - previous_at).num_seconds();
                if (0..=MAX_RESPONSE_DELAY_SECONDS).contains(&delay) {
                    delays.push(delay as f64);
                }
            }
        } else if is_response_participant(&user) {
            previous_human_at = Some(current_at);
        }
    }
    conn.execute(
        "DELETE FROM response_time_stats WHERE chat = ?1 AND source = ?2 AND user_name = ?3",
        params![chat, source, STYLE_USER],
    )?;
    let sample_count = delays.len();
    if let Some(stats) = summarize_response_delays(chat, source, STYLE_USER, delays) {
        insert_response_time_stats(conn, &stats)?;
    }
    // The source also owns a style profile. Rebuilding only the pacing numbers
    // would leave the style aggregate describing rows the repair just changed.
    let style_messages = {
        let mut stmt = conn.prepare(
            "SELECT message FROM owner_style
             WHERE source = ?1 AND chat = ?2 AND user_name = ?3
               AND style_eligible = 1 AND policy_version = ?4
             ORDER BY source_row ASC, id ASC",
        )?;
        let mapped = stmt.query_map(
            params![source, chat, STYLE_USER, STYLE_POLICY_VERSION],
            |row| row.get::<_, String>(0),
        )?;
        mapped.collect::<rusqlite::Result<Vec<_>>>()?
    };
    let mut accumulator = StyleProfileAccumulator::default();
    for message in &style_messages {
        let features = classify_style_message(message);
        if features.style_eligible {
            accumulator.add(message, &features);
        }
    }
    conn.execute(
        "DELETE FROM owner_style_profile WHERE chat = ?1 AND source = ?2 AND user_name = ?3",
        params![chat, source, STYLE_USER],
    )?;
    if let Some(profile) = accumulator.finish(chat, source, STYLE_USER) {
        insert_style_profile(conn, &profile, &Utc::now().to_rfc3339())?;
    }
    Ok(sample_count)
}

pub(super) fn response_delay_percentile(sorted: &[f64], ratio: f64) -> f64 {
    let position = (sorted.len() - 1) as f64 * ratio;
    let lower = position.floor() as usize;
    let upper = position.ceil() as usize;
    if lower == upper {
        sorted[lower]
    } else {
        sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower as f64)
    }
}

fn response_time_segment_sse(
    prefix_sum: &[f64],
    prefix_square_sum: &[f64],
    start: usize,
    end: usize,
) -> f64 {
    let count = (end - start) as f64;
    let sum = prefix_sum[end] - prefix_sum[start];
    let square_sum = prefix_square_sum[end] - prefix_square_sum[start];
    (square_sum - sum * sum / count).max(0.0)
}

/// Smallest admissible `second_split` for every `first_candidates` entry.
///
/// The result is the index into `second_candidates` of the smallest minimiser
/// of `sse(first, s) + sse(s, total)`, or `None` when the strict-increase
/// window `[first + minimum, total - minimum]` holds no admissible boundary.
///
/// The smallest constrained minimiser is monotone non-decreasing in
/// `first_split` because the segment SSE satisfies the quadrangle inequality
///     sse(a, c) + sse(b, d) <= sse(a, d) + sse(b, c)   for a <= b <= c <= d,
/// so divide-and-conquer optimisation is exact here. A ternary search over the
/// middle boundary would not be: the objective is not unimodal, and a shape as
/// ordinary as a dense uniform spread already has a local minimum that is not
/// the global one.
fn response_time_distribution_second_splits(
    prefix_sum: &[f64],
    prefix_square_sum: &[f64],
    first_candidates: &[usize],
    second_candidates: &[usize],
    total: usize,
    minimum: usize,
) -> Vec<Option<usize>> {
    #[allow(clippy::too_many_arguments)]
    // The loop variable is the answer, not an iteration counter: the winning
    // position is stored in optima and reused as a boundary bound in the
    // recursive calls, so enumerating the slice would only rename it.
    #[allow(clippy::needless_range_loop)]
    fn search(
        prefix_sum: &[f64],
        prefix_square_sum: &[f64],
        first_candidates: &[usize],
        second_candidates: &[usize],
        total: usize,
        minimum: usize,
        first_start: usize,
        first_end: usize,
        second_start: usize,
        second_end: usize,
        optima: &mut [Option<usize>],
    ) {
        if first_start > first_end {
            return;
        }
        if second_start > second_end {
            // No admissible boundary is left for this first_split or any later
            // one, because the lower bound only moves right with first_split.
            for slot in &mut optima[first_start..=first_end] {
                *slot = None;
            }
            return;
        }
        let first_midpoint = first_start + (first_end - first_start) / 2;
        let first_split = first_candidates[first_midpoint];
        let lower = second_candidates.partition_point(|boundary| *boundary < first_split + minimum);
        let scan_start = second_start.max(lower);
        let mut best: Option<(f64, usize)> = None;
        for position in scan_start..=second_end {
            let second_split = second_candidates[position];
            let sse = response_time_segment_sse(prefix_sum, prefix_square_sum, 0, first_split)
                + response_time_segment_sse(
                    prefix_sum,
                    prefix_square_sum,
                    first_split,
                    second_split,
                )
                + response_time_segment_sse(prefix_sum, prefix_square_sum, second_split, total);
            if best.is_none_or(|(best_sse, _)| sse < best_sse) {
                best = Some((sse, position));
            }
        }
        optima[first_midpoint] = best.map(|(_, position)| position);
        match best {
            Some((_, best_position)) => {
                if first_midpoint > first_start {
                    search(
                        prefix_sum,
                        prefix_square_sum,
                        first_candidates,
                        second_candidates,
                        total,
                        minimum,
                        first_start,
                        first_midpoint - 1,
                        second_start,
                        best_position,
                        optima,
                    );
                }
                if first_midpoint < first_end {
                    search(
                        prefix_sum,
                        prefix_square_sum,
                        first_candidates,
                        second_candidates,
                        total,
                        minimum,
                        first_midpoint + 1,
                        first_end,
                        best_position,
                        second_end,
                        optima,
                    );
                }
            }
            None => {
                if first_midpoint < first_end {
                    for slot in &mut optima[first_midpoint + 1..=first_end] {
                        *slot = None;
                    }
                }
                if first_midpoint > first_start {
                    search(
                        prefix_sum,
                        prefix_square_sum,
                        first_candidates,
                        second_candidates,
                        total,
                        minimum,
                        first_start,
                        first_midpoint - 1,
                        second_start,
                        second_end,
                        optima,
                    );
                }
            }
        }
    }
    let mut optima = vec![None; first_candidates.len()];
    if !first_candidates.is_empty() && !second_candidates.is_empty() {
        search(
            prefix_sum,
            prefix_square_sum,
            first_candidates,
            second_candidates,
            total,
            minimum,
            0,
            first_candidates.len() - 1,
            0,
            second_candidates.len() - 1,
            &mut optima,
        );
    }
    optima
}

pub(super) fn fit_response_time_distribution(delays: &[f64]) -> Option<ResponseTimeDistribution> {
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
        response_time_segment_sse(&prefix_sum, &prefix_square_sum, start, end)
    };
    // Exact three-way split search in O(N log N) instead of the O(N^2) pair
    // scan, with an identical result. The per-first_split objective
    //     g(s) = sse(first, s) + sse(s, N)
    // is not unimodal, so a ternary or gradient walk does not find its minimum;
    // the quadrangle inequality of the segment SSE is what makes the smallest
    // minimiser monotone in first_split and divide-and-conquer exact. Walking
    // first_split ascending while replacing only on a strict improvement keeps
    // the exhaustive scan's lexicographically smallest `(first, second)` tie:
    // the earliest improving first_split wins with its own smallest second_split.
    let minimum = RESPONSE_TIME_DISTRIBUTION_MIN_COMPONENT_SAMPLES;
    let total = delays.len();
    let first_candidates = (minimum..=total - 2 * minimum)
        .filter(|split| delays[split - 1] < delays[*split])
        .collect::<Vec<_>>();
    let second_candidates = (minimum..=total - minimum)
        .filter(|split| delays[split - 1] < delays[*split])
        .collect::<Vec<_>>();
    let optima = response_time_distribution_second_splits(
        &prefix_sum,
        &prefix_square_sum,
        &first_candidates,
        &second_candidates,
        total,
        minimum,
    );
    let mut best: Option<(f64, usize, usize)> = None;
    for (position, first_split) in first_candidates.into_iter().enumerate() {
        let Some(second_position) = optima[position] else {
            continue;
        };
        let second_split = second_candidates[second_position];
        let sse = segment_sse(0, first_split)
            + segment_sse(first_split, second_split)
            + segment_sse(second_split, total);
        if best.as_ref().is_none_or(|(best_sse, _, _)| sse < *best_sse) {
            best = Some((sse, first_split, second_split));
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

pub(super) fn response_time_distribution_is_valid(distribution: &ResponseTimeDistribution) -> bool {
    if distribution.schema_version != RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION
        || distribution.policy_version != RESPONSE_TIME_DISTRIBUTION_POLICY_VERSION
        || distribution.model_kind != RESPONSE_TIME_DISTRIBUTION_MODEL_KIND
        || distribution.fit_transform != RESPONSE_TIME_DISTRIBUTION_FIT_TRANSFORM
        || distribution.sample_count < RESPONSE_TIME_DISTRIBUTION_MIN_SAMPLES
        || distribution.retained_sample_count != distribution.sample_count
        || distribution.tail_winsorized_count >= distribution.sample_count
        || distribution.components.len() != RESPONSE_TIME_DISTRIBUTION_COMPONENTS
        || distribution.split_seconds.len() != RESPONSE_TIME_DISTRIBUTION_COMPONENTS - 1
        || distribution
            .split_seconds
            .iter()
            .any(|split| !split.is_finite())
        || !distribution.global_upper_seconds.is_finite()
        || distribution.split_seconds[0] < MIN_SCHEDULED_RESPONSE_DELAY_SECONDS
        || distribution.global_upper_seconds > MAX_RESPONSE_DELAY_SECONDS as f64
        || distribution.split_seconds[0] >= distribution.split_seconds[1]
        || distribution.split_seconds[1] >= distribution.global_upper_seconds
    {
        return false;
    }
    let expected_names = ["immediate", "short", "delayed"];
    let mut represented_samples = 0usize;
    let mut total_weight = 0.0;
    for (component, expected_name) in distribution.components.iter().zip(expected_names) {
        if component.name != expected_name
            || component.sample_count < RESPONSE_TIME_DISTRIBUTION_MIN_COMPONENT_SAMPLES
            || !component.weight.is_finite()
            || component.weight <= 0.0
            || !component.normal_location_seconds.is_finite()
            || !component.normal_scale_seconds.is_finite()
            || component.normal_scale_seconds <= 0.0
            || !component.lower_seconds.is_finite()
            || !component.upper_seconds.is_finite()
            || component.lower_seconds < MIN_SCHEDULED_RESPONSE_DELAY_SECONDS
            || component.lower_seconds >= component.upper_seconds
            || component.upper_seconds > distribution.global_upper_seconds
            || !(component.lower_seconds..=component.upper_seconds)
                .contains(&component.normal_location_seconds)
            || (component.weight - component.sample_count as f64 / distribution.sample_count as f64)
                .abs()
                > 1e-12
        {
            return false;
        }
        represented_samples = match represented_samples.checked_add(component.sample_count) {
            Some(value) => value,
            None => return false,
        };
        total_weight += component.weight;
    }
    if represented_samples != distribution.sample_count
        || (total_weight - 1.0).abs() > 1e-12
        || distribution.components[0].upper_seconds != distribution.split_seconds[0]
        || distribution.components[1].upper_seconds != distribution.split_seconds[1]
        || distribution.components[0].upper_seconds >= distribution.components[1].lower_seconds
        || distribution.components[1].upper_seconds >= distribution.components[2].lower_seconds
    {
        return false;
    }
    distribution.global_upper_seconds
        == distribution
            .components
            .iter()
            .map(|component| component.upper_seconds)
            .fold(0.0, f64::max)
}

pub(super) fn summarize_response_delays(
    chat: &str,
    source: &str,
    user: &str,
    mut delays: Vec<f64>,
) -> Option<ResponseTimeStats> {
    if delays.is_empty()
        || delays.iter().any(|delay| {
            !delay.is_finite() || !(0.0..=MAX_RESPONSE_DELAY_SECONDS as f64).contains(delay)
        })
    {
        return None;
    }
    delays.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    let average_seconds = delays.iter().sum::<f64>() / delays.len() as f64;
    let stddev_seconds = if delays.len() > 1 {
        (delays
            .iter()
            .map(|delay| (delay - average_seconds).powi(2))
            .sum::<f64>()
            / (delays.len() - 1) as f64)
            .sqrt()
    } else {
        0.0
    };
    let distribution = fit_response_time_distribution(&delays);
    Some(ResponseTimeStats {
        chat: chat.to_string(),
        source: source.to_string(),
        user: user.to_string(),
        sample_count: delays.len(),
        average_seconds,
        median_seconds: response_delay_percentile(&delays, 0.5),
        p90_seconds: response_delay_percentile(&delays, 0.9),
        min_seconds: delays[0],
        max_seconds: *delays.last().unwrap_or(&delays[0]),
        max_window_seconds: MAX_RESPONSE_DELAY_SECONDS,
        stddev_seconds,
        distribution,
    })
}

pub fn response_time_stats(
    db_path: &Path,
    chat: &str,
    user: &str,
    source: Option<&str>,
) -> Result<Option<ResponseTimeStats>> {
    let conn = open_db_readonly(db_path)?;
    ensure_retrieval_index_current(&conn)?;
    response_time_stats_with_connection(&conn, chat, user, source)
}

pub(super) fn response_time_stats_with_connection(
    conn: &Connection,
    chat: &str,
    user: &str,
    source: Option<&str>,
) -> Result<Option<ResponseTimeStats>> {
    ensure_retrieval_index_current(conn)?;
    let preferred_source = preferred_context_source(conn, Some(chat), source)?;
    let mut stmt = conn.prepare(
        "SELECT chat, source, user_name, sample_count, average_seconds, median_seconds, p90_seconds, min_seconds, max_seconds, max_window_seconds, stddev_seconds, distribution_schema_version, distribution_json
         FROM response_time_stats
         WHERE chat = ?1 AND user_name = ?2 AND (?3 IS NULL OR source = ?3)
         ORDER BY sample_count DESC
         LIMIT 1",
    )?;
    match stmt.query_row(params![chat, user, preferred_source.as_deref()], |row| {
        let distribution_schema_version = row.get::<_, i64>(11)?;
        let distribution_json = row.get::<_, String>(12)?;
        let distribution =
            if distribution_schema_version == RESPONSE_TIME_DISTRIBUTION_SCHEMA_VERSION as i64 {
                serde_json::from_str::<ResponseTimeDistribution>(&distribution_json)
                    .ok()
                    .filter(response_time_distribution_is_valid)
            } else {
                None
            };
        Ok(ResponseTimeStats {
            chat: row.get(0)?,
            source: row.get(1)?,
            user: row.get(2)?,
            sample_count: row.get::<_, i64>(3)? as usize,
            average_seconds: row.get(4)?,
            median_seconds: row.get(5)?,
            p90_seconds: row.get(6)?,
            min_seconds: row.get(7)?,
            max_seconds: row.get(8)?,
            max_window_seconds: row.get(9)?,
            stddev_seconds: row.get(10)?,
            distribution,
        })
    }) {
        Ok(stats) => Ok(Some(stats)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(error) => Err(error.into()),
    }
}
