//! Numeric reply-author bindings and the bounded AX attestation of explicit
//! `bind:` room selectors.

use std::process::Command;

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use super::MAX_INT64;
use crate::{ax_send, local_db};

#[derive(Debug, Clone)]
pub(super) struct AutoReplyBindingEvidence {
    pub(super) chat_id: i64,
    pub(super) ax_name: String,
    pub(super) local_name: String,
    pub(super) matched_log_ids: Vec<i64>,
    pub(super) matched_count: usize,
    pub(super) matched_utf8_bytes: usize,
    pub(super) transcript_sha256: String,
    pub(super) attested_db_last_log_id: i64,
}

#[derive(Debug, Clone, serde::Serialize, PartialEq, Eq)]
pub(super) struct AutoReplyAuthorBinding {
    pub(super) nickname: String,
    pub(super) author_id: i64,
}

pub(super) fn auto_reply_bind_author_ids(
    reader: &local_db::LocalDbReader,
    targets: &[local_db::LocalChat],
    reply_authors_by_room: &std::collections::BTreeMap<i64, Vec<String>>,
) -> Result<std::collections::BTreeMap<i64, Vec<AutoReplyAuthorBinding>>> {
    let mut by_chat = std::collections::BTreeMap::new();
    for target in targets {
        let reply_authors = reply_authors_by_room
            .get(&target.chat_id)
            .context("AutoReply per-room reply-author allowlist is incomplete")?;
        let configured = reply_authors
            .iter()
            .map(|nickname| nickname.trim().to_string())
            .collect::<std::collections::BTreeSet<_>>();
        if configured.is_empty()
            || configured.len() != reply_authors.len()
            || configured.iter().any(|nickname| {
                nickname.is_empty()
                    || nickname.len() > 128
                    || nickname.chars().any(char::is_control)
            })
        {
            anyhow::bail!(
                "AutoReply reply-author allowlist for room {} is invalid or duplicated",
                target.chat_id
            );
        }
        let identities = reader.room_author_identities(target.chat_id)?;
        let present = identities
            .iter()
            .map(|identity| identity.nickname.trim().to_string())
            .filter(|nickname| configured.contains(nickname))
            .collect::<std::collections::BTreeSet<_>>();
        let effective = if present.is_empty() {
            identities
                .iter()
                .filter(|identity| !identity.is_self && !identity.nickname.trim().is_empty())
                .map(|identity| identity.nickname.trim().to_string())
                .collect::<std::collections::BTreeSet<_>>()
        } else {
            present
        };
        if effective.is_empty() {
            anyhow::bail!(
                "AutoReply reply author allowlist for room {:?} has no bindable members",
                target.chat_name
            );
        }
        let bindings = resolve_auto_reply_author_bindings(
            &target.chat_name,
            reader.account_user_id(),
            &identities,
            &effective,
        )?;
        by_chat.insert(target.chat_id, bindings);
    }
    Ok(by_chat)
}

pub(super) fn configure_auto_reply_supervisor_author_policy(
    command: &mut Command,
    reply_authors: &[String],
    author_bindings_json: &str,
) {
    command
        .env("OPENKAKAO_REPLY_AUTHORS", reply_authors.join(","))
        .env("OPENKAKAO_REPLY_AUTHOR_BINDINGS", author_bindings_json);
    for author in reply_authors {
        command.arg("--reply-author").arg(author);
    }
}

pub(super) fn resolve_auto_reply_author_bindings(
    room_name: &str,
    account_user_id: i64,
    identities: &[local_db::LocalAuthorIdentity],
    configured: &std::collections::BTreeSet<String>,
) -> Result<Vec<AutoReplyAuthorBinding>> {
    let mut ids_by_nickname =
        std::collections::BTreeMap::<String, std::collections::BTreeSet<i64>>::new();
    let mut nicknames_by_id =
        std::collections::BTreeMap::<i64, std::collections::BTreeSet<String>>::new();
    for identity in identities {
        if identity.author_id <= 0
            || identity.author_id == MAX_INT64
            || identity.is_self != (identity.author_id == account_user_id)
        {
            anyhow::bail!("AutoReply room author identity proof is inconsistent");
        }
        nicknames_by_id
            .entry(identity.author_id)
            .or_default()
            .insert(identity.nickname.clone());
        if configured.contains(&identity.nickname) {
            ids_by_nickname
                .entry(identity.nickname.clone())
                .or_default()
                .insert(identity.author_id);
        }
    }
    let mut bindings = Vec::new();
    let mut bound_ids = std::collections::BTreeSet::new();
    for nickname in configured {
        let ids = ids_by_nickname.get(nickname).cloned().unwrap_or_default();
        if ids.is_empty() {
            // A configured member simply not present in this room is skipped,
            // not an error.
            continue;
        }
        if ids.len() > 1 {
            // Fail closed: a configured display name that resolves to more than
            // one author ID is ambiguous and could bind the wrong person.
            anyhow::bail!(
                "AutoReply reply author {nickname:?} in room {room_name:?} maps to multiple author IDs"
            );
        }
        let author_id = *ids.iter().next().expect("one author ID");
        match nicknames_by_id.get(&author_id) {
            // The bound author must carry exactly this one display name.
            Some(names) if names.len() == 1 && names.contains(nickname) => {}
            // Display-name drift: the same author ID appears under more than one
            // name. Fail closed rather than risk binding a renamed identity.
            Some(names) if names.contains(nickname) => anyhow::bail!(
                "AutoReply reply author {nickname:?} (ID {author_id}) in room {room_name:?} has drifting display names"
            ),
            _ => continue,
        }
        if author_id == account_user_id {
            continue;
        }
        if !bound_ids.insert(author_id) {
            continue;
        }
        bindings.push(AutoReplyAuthorBinding {
            nickname: nickname.clone(),
            author_id,
        });
    }
    if bindings.is_empty() {
        anyhow::bail!(
            "AutoReply reply author allowlist for room {room_name:?} has no bindable members"
        );
    }
    Ok(bindings)
}

/// Bound on transient read-only attestation failures.
///
/// KakaoTalk can expose a chat window mid-repaint, where one read returns rows
/// from another view or none at all. The unattended host treated the single
/// read as final and fenced every bound room when that landed (2026-09-18
/// flapping). Each attempt re-reads both sides and re-runs the same read-only
/// match, so the safety property is unchanged.
pub(super) const AUTO_REPLY_ATTEST_ATTEMPTS: usize = 3;
pub(super) const AUTO_REPLY_ATTEST_RETRY_PAUSE: std::time::Duration =
    std::time::Duration::from_millis(500);

/// Run one bounded operation until it succeeds or the attempt budget is spent.
///
/// Returns the last error so callers keep the original diagnostics, and always
/// runs at least once even when the budget is configured to zero.
pub(super) fn retry_bounded<T>(
    attempts: usize,
    pause: std::time::Duration,
    mut op: impl FnMut() -> Result<T>,
) -> Result<T> {
    let budget = attempts.max(1);
    let mut last_error: Option<anyhow::Error> = None;
    for index in 0..budget {
        match op() {
            Ok(value) => return Ok(value),
            Err(error) => {
                last_error = Some(error);
                if index + 1 < budget {
                    std::thread::sleep(pause);
                }
            }
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow::anyhow!("bounded retry produced no attempt")))
}

pub(super) fn auto_reply_attest_explicit_bindings(
    reader: &local_db::LocalDbReader,
    selectors: &[local_db::ChatSelector],
    targets: &[local_db::LocalChat],
) -> Result<Vec<AutoReplyBindingEvidence>> {
    retry_bounded(
        AUTO_REPLY_ATTEST_ATTEMPTS,
        AUTO_REPLY_ATTEST_RETRY_PAUSE,
        || auto_reply_attest_explicit_bindings_once(reader, selectors, targets),
    )
}

fn auto_reply_attest_explicit_bindings_once(
    reader: &local_db::LocalDbReader,
    selectors: &[local_db::ChatSelector],
    targets: &[local_db::LocalChat],
) -> Result<Vec<AutoReplyBindingEvidence>> {
    const AX_ATTEST_COUNT: usize = 40;
    let mut seen = std::collections::BTreeMap::<i64, String>::new();
    let mut evidence = Vec::new();
    for selector in selectors {
        let local_db::ChatSelector::Binding { id, name } = selector else {
            continue;
        };
        if let Some(previous) = seen.insert(*id, name.clone()) {
            if previous != *name {
                anyhow::bail!("chat ID {id} has conflicting explicit AX bindings");
            }
            continue;
        }
        let target = targets
            .iter()
            .find(|target| target.chat_id == *id && target.chat_name == *name)
            .context("explicit chat binding did not resolve to one local target")?;
        let ax_messages = ax_send::read_open_exact_via_ax(name, AX_ATTEST_COUNT)
            .with_context(|| format!("read exact AX transcript for {name:?}"))?;
        let ax_texts = ax_messages
            .iter()
            .map(|item| ax_send::normalize_binding_message(&item.text))
            .filter(|item| !item.is_empty())
            .collect::<Vec<_>>();
        let mut local_messages = reader.read_messages(target.chat_id, AX_ATTEST_COUNT, None)?;
        local_messages.reverse();
        let local_pairs = ax_send::normalize_local_binding_suffix(&local_messages);
        let local_texts = local_pairs
            .iter()
            .map(|(_, token)| token.text.clone())
            .collect::<Vec<_>>();
        let suffix_match = ax_send::match_local_binding_suffix(&ax_texts, &local_pairs);
        let matched_pairs = local_pairs
            .iter()
            .rev()
            .take(suffix_match.matched_count)
            .cloned()
            .collect::<Vec<_>>();
        let matched_texts = matched_pairs
            .iter()
            .map(|(_, token)| token.text.clone())
            .collect::<Vec<_>>();
        // Operator-confirmed rooms with a short transcript may pass with one
        // exact row; the flag is off unless the operator sets it deliberately.
        if !suffix_match.is_acceptable(openkakao_cli::ax_send::manual_attestation_enabled()) {
            anyhow::bail!(
                "explicit chat binding {id}:{name} failed read-only AX/local transcript attestation \
                 (matched={}, distinct={}, utf8_bytes={}, ax_rows={}, local_rows={}, ax_tail={}, local_tail={})",
                suffix_match.matched_count,
                suffix_match.matched_distinct,
                suffix_match.matched_utf8_bytes,
                ax_texts.len(),
                local_texts.len(),
                ax_send::binding_kind_tail(&ax_texts, 4),
                ax_send::binding_kind_tail(&local_texts, 4)
            );
        }
        let matched_log_ids = matched_pairs
            .iter()
            .map(|(log_id, _)| *log_id)
            .collect::<Vec<_>>();
        let mut transcript_hasher = Sha256::new();
        for (log_id, text) in matched_log_ids.iter().zip(matched_texts.iter()) {
            transcript_hasher.update(log_id.to_be_bytes());
            transcript_hasher.update((text.len() as u64).to_be_bytes());
            transcript_hasher.update(text.as_bytes());
        }
        evidence.push(AutoReplyBindingEvidence {
            chat_id: *id,
            ax_name: name.clone(),
            local_name: target.database_chat_name.clone().unwrap_or_default(),
            matched_log_ids,
            matched_count: suffix_match.matched_count,
            matched_utf8_bytes: suffix_match.matched_utf8_bytes,
            transcript_sha256: hex::encode(transcript_hasher.finalize()),
            attested_db_last_log_id: target.last_log_id,
        });
    }
    Ok(evidence)
}
