use super::*;
use std::fs;
#[cfg(unix)]
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::Ordering;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use super::bindings::{
    configure_auto_reply_supervisor_author_policy, resolve_auto_reply_author_bindings,
    retry_bounded, AutoReplyAuthorBinding, AutoReplyBindingEvidence, AUTO_REPLY_ATTEST_ATTEMPTS,
    AUTO_REPLY_ATTEST_RETRY_PAUSE,
};
use super::enrollment::{
    enrollment_cursor_authority_for_target, enrollment_floor_for_target,
    require_cli_enrolled_reply_author, validate_cli_enrollment_authority,
    validate_cli_enrollment_digest, write_auto_reply_enrollment, AutoReplyCursorAuthority,
    AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION, AUTO_REPLY_CURSOR_FRESH_KIND,
    AUTO_REPLY_CURSOR_LEFTOVER_KIND, AUTO_REPLY_CURSOR_REPLAY_KIND,
    AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION,
};
use super::llm::{
    probe_auto_reply_llm_with_local_transport, select_auto_reply_llm, AutoReplyLlmChoice,
    LocalMlxProbeMethod, LocalMlxProbeRequest, LocalMlxProbeResponse, LocalMlxProbeTransport,
    ReqwestLocalMlxProbeTransport, LOCAL_MLX_BASE_URL, LOCAL_MLX_CHAT_URL,
    LOCAL_MLX_MAX_RESPONSE_BYTES, LOCAL_MLX_MODELS_URL, LOCAL_MLX_PROBE_TIMEOUT,
};
use super::process::{
    configure_auto_reply_process_group, start_auto_reply_guardian_liveness_monitor,
    validate_auto_reply_guardian_liveness_fd, AutoReplyChildrenGuard,
    AUTO_REPLY_GUARDIAN_LIVENESS_ENV, AUTO_REPLY_GUARDIAN_LOST, AUTO_REPLY_STOP,
};
use super::queue::{
    parse_auto_reply_transition_event_id, validate_stopped_clean_queue,
    AUTO_REPLY_PIPELINE_TRANSITIONS_CAP_TRIGGER_SQL, AUTO_REPLY_PIPELINE_TRANSITIONS_INDEX_SQL,
    AUTO_REPLY_PIPELINE_TRANSITIONS_INSERT_TRIGGER_SQL, AUTO_REPLY_PIPELINE_TRANSITIONS_TABLE_SQL,
    AUTO_REPLY_PIPELINE_TRANSITIONS_UPDATE_TRIGGER_SQL, AUTO_REPLY_QUEUE_JOURNAL_MAX_ROWS,
    AUTO_REPLY_QUEUE_JOURNAL_USER_VERSION, AUTO_REPLY_QUEUE_LEGACY_USER_VERSION,
    AUTO_REPLY_REPLY_JOBS_STATUS_INDEX_SQL, AUTO_REPLY_REPLY_JOBS_V2_TABLE_SQL,
};
use super::runner::{
    is_homebrew_cellar_version_path, is_homebrew_opt_python_keg_path,
    validate_auto_reply_codex_auth, validate_auto_reply_executable,
    AUTO_REPLY_PYTHON_ISOLATION_ARGS,
};
use super::state_root::{
    acquire_auto_reply_owner_lock, auto_reply_legacy_conflict,
    configure_auto_reply_supervisor_shared_state, prepare_auto_reply_room_state,
    read_bounded_json_file, write_auto_reply_aggregate, write_private_auto_reply_enrollment,
};
use super::worker_gate::{
    finish_worker_bound_local_send_setup, require_auto_reply_worker_preflight,
    require_expected_local_source_tail, resolve_bound_source_message,
};
use crate::{config, local_db};

struct FakeLocalMlxProbeTransport {
    responses: std::cell::RefCell<std::collections::VecDeque<LocalMlxProbeResponse>>,
    requests: std::cell::RefCell<Vec<LocalMlxProbeRequest>>,
}

impl FakeLocalMlxProbeTransport {
    fn new(responses: Vec<LocalMlxProbeResponse>) -> Self {
        Self {
            responses: std::cell::RefCell::new(responses.into()),
            requests: std::cell::RefCell::new(Vec::new()),
        }
    }

    fn json(status: u16, value: serde_json::Value) -> LocalMlxProbeResponse {
        LocalMlxProbeResponse {
            status,
            body: serde_json::to_vec(&value).expect("serialize fake local MLX response"),
        }
    }
}

impl LocalMlxProbeTransport for FakeLocalMlxProbeTransport {
    fn send(&self, request: LocalMlxProbeRequest) -> Result<LocalMlxProbeResponse> {
        self.requests.borrow_mut().push(request);
        self.responses
            .borrow_mut()
            .pop_front()
            .context("missing fake local MLX response")
    }
}

#[test]
fn retry_bounded_stops_at_the_first_success() {
    let mut calls = 0;
    let value = retry_bounded(3, std::time::Duration::ZERO, || {
        calls += 1;
        if calls < 3 {
            anyhow::bail!("transient failure {calls}");
        }
        Ok(calls)
    })
    .expect("third attempt should succeed");
    assert_eq!(value, 3);
    assert_eq!(calls, 3, "retry must stop once an attempt succeeds");
}

#[test]
fn retry_bounded_surfaces_the_last_error_after_the_budget() {
    let mut calls = 0;
    let error = retry_bounded::<()>(3, std::time::Duration::ZERO, || {
        calls += 1;
        anyhow::bail!("failure {calls}")
    })
    .expect_err("an exhausted budget must fail");
    assert_eq!(calls, 3, "retry must spend the whole budget");
    assert!(
        error.to_string().contains("failure 3"),
        "the last error must be surfaced, got {error}"
    );
}

#[test]
fn retry_bounded_always_attempts_once_and_never_exceeds_the_budget() {
    let mut zero_budget_calls = 0;
    retry_bounded::<()>(0, std::time::Duration::ZERO, || {
        zero_budget_calls += 1;
        Ok(())
    })
    .expect("a zero budget still attempts once");
    assert_eq!(zero_budget_calls, 1);

    let mut first_success_calls = 0;
    retry_bounded(3, std::time::Duration::ZERO, || {
        first_success_calls += 1;
        Ok(7)
    })
    .expect("the first attempt succeeds");
    assert_eq!(first_success_calls, 1, "a success must not retry");
}

#[test]
fn auto_reply_attestation_retry_budget_is_bounded() {
    assert!((2..=5).contains(&AUTO_REPLY_ATTEST_ATTEMPTS));
    assert!(AUTO_REPLY_ATTEST_RETRY_PAUSE <= std::time::Duration::from_secs(2));
}

#[cfg(unix)]
use crate::auto_reply_runtime::acquire_worker_setup_lock_nonblocking;
use crate::commands::context_sync_local::tests::local_source_fence;

#[cfg(unix)]
#[test]
fn codex_auth_rejects_symlink_and_hardlink_credentials() {
    use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};

    let root = tempfile::tempdir().expect("temporary Codex home");
    let source = root.path().join("auth-source.json");
    fs::write(&source, b"{}").expect("write auth fixture");
    fs::set_permissions(&source, fs::Permissions::from_mode(0o600))
        .expect("make auth fixture private");
    let uid = fs::symlink_metadata(&source).expect("auth metadata").uid();
    validate_auto_reply_codex_auth(&source, uid)
        .expect("a private single-link regular credential must pass");

    let linked = root.path().join("auth-symlink.json");
    symlink(&source, &linked).expect("create symlink fixture");
    assert!(validate_auto_reply_codex_auth(&linked, uid).is_err());

    let hardlinked = root.path().join("auth-hardlink.json");
    fs::hard_link(&source, &hardlinked).expect("create hardlink fixture");
    assert!(validate_auto_reply_codex_auth(&source, uid).is_err());
    assert!(validate_auto_reply_codex_auth(&hardlinked, uid).is_err());
    fs::remove_file(&hardlinked).expect("remove hardlink");
    fs::write(&source, b"").expect("empty the auth fixture");
    assert!(validate_auto_reply_codex_auth(&source, uid).is_err());
}

#[test]
fn final_local_source_fence_allows_later_rows_after_scheduled_source() {
    let watcher_last_observed = 100;
    let current = local_source_fence(42, watcher_last_observed, watcher_last_observed, &[]);
    require_expected_local_source_tail(42, watcher_last_observed, &current)
        .expect("an exact local source tail should remain eligible");

    // A later inbound is its own job. It must not cancel the earlier
    // scheduled send once the source row itself is still present.
    let source_db_advanced = local_source_fence(42, watcher_last_observed, 101, &[101]);
    require_expected_local_source_tail(42, watcher_last_observed, &source_db_advanced)
        .expect("later room rows must not fence an earlier unanswered inbound");

    let gap = local_source_fence(42, watcher_last_observed, 101, &[101]);
    let mut gap = gap;
    gap.completeness.has_gap = true;
    let error = require_expected_local_source_tail(42, watcher_last_observed, &gap)
        .expect_err("a gapped poll after the source remains fail-closed");
    assert!(error
        .to_string()
        .contains("scheduled reply source tail is unavailable"));
}

#[test]
fn proactive_local_source_fence_accepts_exact_self_tail() {
    let tail = 3908781794201088001i64;
    let current = local_source_fence(42, tail, tail, &[]);
    require_expected_local_source_tail(42, tail, &current)
        .expect("a quiet self tail remains an exact empty after-cursor");
    let advanced = local_source_fence(42, tail, tail + 1, &[tail + 1]);
    require_expected_local_source_tail(42, tail, &advanced)
        .expect("a newer tail after a quiet self row still keeps the source eligible");
}

#[test]
fn auto_reply_supervisor_python_is_environment_and_site_isolated() {
    assert_eq!(AUTO_REPLY_PYTHON_ISOLATION_ARGS, ["-E", "-B", "-S"]);
}

#[test]
fn auto_reply_llm_aliases_map_to_attested_models() {
    for exact in [
        config::MLX_FLASH_NEXT_MODEL_ID,
        config::MLX_FLASH_NEXT_PREFIXED_MODEL_ID,
    ] {
        assert_eq!(
            AutoReplyLlmChoice::from_model(exact),
            Some(AutoReplyLlmChoice::MlxQwen38FlashNext)
        );
    }
    assert_eq!(
        AutoReplyLlmChoice::from_model(&format!(" {} ", config::MLX_FLASH_NEXT_MODEL_ID)),
        None,
        "the local model allowlist must remain exact"
    );
    for exact in [config::MLX_27B_MODEL_ID, config::MLX_27B_PREFIXED_MODEL_ID] {
        assert_eq!(
            AutoReplyLlmChoice::from_model(exact),
            Some(AutoReplyLlmChoice::MlxQwen38TwentySevenB)
        );
    }
    let mut json_config = config::OpenKakaoConfig::default();
    json_config.auto_reply.reply_model = Some(config::MLX_FLASH_NEXT_PREFIXED_MODEL_ID.into());
    assert_eq!(
        select_auto_reply_llm(&mut json_config, None, true)
            .expect("JSON mode should select the configured exact local model"),
        AutoReplyLlmChoice::MlxQwen38FlashNext
    );
    assert_eq!(
        AutoReplyLlmChoice::from_model("gemini-3.7-flash")
            .expect("gemini alias")
            .model(),
        "google-antigravity/gemini-3.7-flash-tiered"
    );
    assert_eq!(
        AutoReplyLlmChoice::from_model("gpt-5.6-luna")
            .expect("luna alias")
            .model(),
        "gpt-5.6-luna"
    );
    assert_eq!(
        AutoReplyLlmChoice::from_model("deepseek-v4.1-flash")
            .expect("opencode deepseek alias")
            .model(),
        "opencode-go-session/deepseek-v4.1-flash"
    );
    let mut config = config::OpenKakaoConfig::default();
    AutoReplyLlmChoice::from_model("deepseek-v4.1-flash")
        .expect("opencode deepseek alias")
        .apply(&mut config);
    assert_eq!(
        config.model.provider.as_deref(),
        Some("opencode-go-session")
    );
    assert_eq!(config.auto_reply.reply_runner_kind.as_deref(), Some("gjc"));
    assert_eq!(AutoReplyLlmChoice::all().len(), 5);
}

#[test]
fn auto_reply_local_mlx_choice_applies_fail_closed_profile() {
    let mut config = config::OpenKakaoConfig::default();
    config.model.privacy_mode = Some("remote_explicit".into());
    config.model.allow_egress = true;
    config.model.provider = Some("openai-codex".into());
    config.model.retention = Some("provider-policy".into());

    AutoReplyLlmChoice::MlxQwen38FlashNext.apply(&mut config);

    assert_eq!(config.model.privacy_mode.as_deref(), Some("local"));
    assert!(!config.model.allow_egress);
    assert_eq!(
        config.model.provider.as_deref(),
        Some(config::MLX_SERVE_PROVIDER)
    );
    assert!(config.model.retention.is_none());
    assert_eq!(
        config.auto_reply.reply_model.as_deref(),
        Some(config::MLX_FLASH_NEXT_MODEL_ID)
    );
    assert_eq!(
        config.auto_reply.reply_runner_kind.as_deref(),
        Some("opencodex")
    );
    assert_eq!(
        config.auto_reply.reply_runner.as_deref(),
        Some("/opt/homebrew/bin/opencodex")
    );
    config::validate_auto_reply_local_mlx_profile(&config)
        .expect("applied local MLX profile must validate");
}

#[test]
fn auto_reply_local_mlx_probe_uses_only_bounded_loopback_requests() {
    let mut config = config::OpenKakaoConfig::default();
    AutoReplyLlmChoice::MlxQwen38FlashNext.apply(&mut config);
    let transport = FakeLocalMlxProbeTransport::new(vec![
        FakeLocalMlxProbeTransport::json(
            200,
            serde_json::json!({
                "data": [{
                    "id": config::MLX_FLASH_NEXT_PREFIXED_MODEL_ID,
                    "loaded": true,
                    "state": "ready"
                }]
            }),
        ),
        FakeLocalMlxProbeTransport::json(
            200,
            serde_json::json!({"choices": [{"message": {"content": "확인"}}]}),
        ),
    ]);

    probe_auto_reply_llm_with_local_transport(
        &config,
        AutoReplyLlmChoice::MlxQwen38FlashNext,
        &transport,
    )
    .expect("ready local MLX model should pass the bounded probe");

    let requests = transport.requests.borrow();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].method, LocalMlxProbeMethod::Get);
    assert_eq!(requests[0].url, LOCAL_MLX_MODELS_URL);
    assert!(requests[0].body.is_none());
    assert_eq!(requests[1].method, LocalMlxProbeMethod::Post);
    assert_eq!(requests[1].url, LOCAL_MLX_CHAT_URL);
    assert!(requests.iter().all(|request| {
        request.timeout == LOCAL_MLX_PROBE_TIMEOUT
            && request.max_response_bytes == LOCAL_MLX_MAX_RESPONSE_BYTES
            && request.url.starts_with(LOCAL_MLX_BASE_URL)
            && !request.url.contains("10100")
            && !request.url.contains("/load")
            && !request.url.contains("/unload")
            && !request.url.contains("/swap")
    }));
    let generation: serde_json::Value =
        serde_json::from_slice(requests[1].body.as_deref().expect("generation probe body"))
            .expect("parse generation probe body");
    assert_eq!(
        generation.get("model").and_then(serde_json::Value::as_str),
        Some(config::MLX_FLASH_NEXT_MODEL_ID)
    );
    assert_eq!(
        generation
            .get("max_tokens")
            .and_then(serde_json::Value::as_u64),
        Some(8)
    );
    assert_eq!(
        generation
            .pointer("/messages/0/content")
            .and_then(serde_json::Value::as_str),
        Some("확인이라고만 답하세요.")
    );
}

#[test]
fn auto_reply_local_mlx_probe_fails_closed_without_generation_or_remote_fallback() {
    let mut config = config::OpenKakaoConfig::default();
    AutoReplyLlmChoice::MlxQwen38FlashNext.apply(&mut config);
    let not_ready = FakeLocalMlxProbeTransport::new(vec![FakeLocalMlxProbeTransport::json(
        200,
        serde_json::json!({
            "data": [{
                "id": config::MLX_FLASH_NEXT_MODEL_ID,
                "loaded": false,
                "state": "unloaded"
            }]
        }),
    )]);
    let error = probe_auto_reply_llm_with_local_transport(
        &config,
        AutoReplyLlmChoice::MlxQwen38FlashNext,
        &not_ready,
    )
    .expect_err("an unloaded model must fail before generation");
    assert_eq!(error.to_string(), "local MLX model is not ready");
    assert_eq!(not_ready.requests.borrow().len(), 1);

    let malformed = FakeLocalMlxProbeTransport::new(vec![LocalMlxProbeResponse {
        status: 200,
        body: b"{not-json".to_vec(),
    }]);
    assert!(probe_auto_reply_llm_with_local_transport(
        &config,
        AutoReplyLlmChoice::MlxQwen38FlashNext,
        &malformed,
    )
    .is_err());
    assert_eq!(malformed.requests.borrow().len(), 1);

    let redirected = FakeLocalMlxProbeTransport::new(vec![FakeLocalMlxProbeTransport::json(
        302,
        serde_json::json!({"redirect": true}),
    )]);
    let redirect_error = probe_auto_reply_llm_with_local_transport(
        &config,
        AutoReplyLlmChoice::MlxQwen38FlashNext,
        &redirected,
    )
    .expect_err("redirects must fail closed");
    assert!(redirect_error.to_string().contains("HTTP 302"));
    assert_eq!(redirected.requests.borrow().len(), 1);

    config.auto_reply.reply_model = Some("google-antigravity/gemini-3.8-flash".into());
    let invalid_profile = FakeLocalMlxProbeTransport::new(Vec::new());
    assert!(probe_auto_reply_llm_with_local_transport(
        &config,
        AutoReplyLlmChoice::MlxQwen38FlashNext,
        &invalid_profile,
    )
    .is_err());
    assert!(invalid_profile.requests.borrow().is_empty());
}

#[test]
fn auto_reply_local_mlx_transport_rejects_non_fixed_endpoints_before_network() {
    for url in [
        "http://127.0.0.1:10100/v1/models",
        "https://models.example.invalid/v1/models",
    ] {
        let error = match ReqwestLocalMlxProbeTransport.send(LocalMlxProbeRequest {
            method: LocalMlxProbeMethod::Get,
            url,
            body: None,
            timeout: LOCAL_MLX_PROBE_TIMEOUT,
            max_response_bytes: LOCAL_MLX_MAX_RESPONSE_BYTES,
        }) {
            Ok(_) => panic!("non-fixed endpoint must be rejected before transport"),
            Err(error) => error,
        };
        assert!(error.to_string().contains("non-loopback endpoint"));
    }
}

#[test]
fn beginner_plain_name_uses_configured_bind_selector() {
    let expanded = expand_plain_chat_names_with_configured_bindings(
        vec!["부자멘토멘티".into()],
        &["bind:417780809780519:부자멘토멘티".into()],
    );
    assert_eq!(expanded, vec!["bind:417780809780519:부자멘토멘티"]);
    let untouched = expand_plain_chat_names_with_configured_bindings(
        vec!["name:다른방".into()],
        &["bind:417780809780519:부자멘토멘티".into()],
    );
    assert_eq!(untouched, vec!["name:다른방"]);
}

fn enrollment_fixture(room_root: &Path, identity: serde_json::Value) -> serde_json::Value {
    serde_json::json!({
        "schema_version": AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION,
        "activation": "foreground",
        "selectors": ["bind:42:부자멘토멘티"],
        "runtime_root": room_root.parent().and_then(Path::parent).unwrap(),
        "created_at": "2026-08-12T00:00:00Z",
        "targets": [{
            "chat_id": 42,
            "chat_name": "부자멘토멘티",
            "last_log_id": 100,
            "room_state_root": room_root,
            "identity": identity,
            "cursor_authority": {
                "schema_version": AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION,
                "kind": AUTO_REPLY_CURSOR_FRESH_KIND,
                "cursor_floor": 100,
                "attested_db_last_log_id": 100,
                "prior_owner_id": null,
                "prior_source_epoch": null,
            },
            "reply_author_bindings": [{"nickname": "현준", "author_id": 700}],
        }],
    })
}

#[test]
fn cli_enrollment_v4_local_name_authority_matches_final_send_gate() {
    let root = tempfile::tempdir().expect("temporary state root");
    let room_root = root.path().join("rooms/42");
    fs::create_dir_all(&room_root).expect("create room root");
    let enrollment = enrollment_fixture(
        &room_root,
        serde_json::json!({
            "schema_version": 1,
            "kind": "local_name",
            "local_name": "부자멘토멘티",
            "ax_name": "부자멘토멘티",
        }),
    );
    validate_cli_enrollment_authority(&enrollment, 42, "부자멘토멘티", 100, &room_root)
        .expect("v4 local-name enrollment should pass the final gate");

    let raw = serde_json::to_vec(&enrollment).expect("serialize enrollment");
    let digest = hex::encode(Sha256::digest(&raw));
    validate_cli_enrollment_digest(&raw, &digest).expect("matching digest should pass");
    assert!(validate_cli_enrollment_digest(&raw, &"A".repeat(64)).is_err());
    assert!(validate_cli_enrollment_digest(&raw, &"0".repeat(64)).is_err());
}

#[test]
fn cli_enrollment_v4_transcript_and_replay_authority_are_strict() {
    let root = tempfile::tempdir().expect("temporary state root");
    let room_root = root.path().join("rooms/42");
    fs::create_dir_all(&room_root).expect("create room root");
    let identity = serde_json::json!({
        "schema_version": 1,
        "kind": "ax_transcript",
        "local_name": "",
        "ax_name": "부자멘토멘티",
        "matched_log_ids": [98, 99, 100],
        "matched_count": 3,
        "matched_utf8_bytes": 24,
        "transcript_sha256": "a".repeat(64),
        "attested_db_last_log_id": 100,
    });
    let enrollment = enrollment_fixture(&room_root, identity.clone());
    validate_cli_enrollment_authority(&enrollment, 42, "부자멘토멘티", 100, &room_root)
        .expect("strict transcript enrollment should pass the final gate");

    let two_row = enrollment_fixture(
        &room_root,
        serde_json::json!({
            "schema_version": 1,
            "kind": "ax_transcript",
            "local_name": "",
            "ax_name": "부자멘토멘티",
            "matched_log_ids": [99, 100],
            "matched_count": 2,
            "matched_utf8_bytes": 19,
            "transcript_sha256": "a".repeat(64),
            "attested_db_last_log_id": 100,
        }),
    );
    validate_cli_enrollment_authority(&two_row, 42, "부자멘토멘티", 100, &room_root)
        .expect("two-row distinct suffix enrollment must match is_strong bind");
    let one_row = enrollment_fixture(
        &room_root,
        serde_json::json!({
            "schema_version": 1,
            "kind": "ax_transcript",
            "local_name": "",
            "ax_name": "부자멘토멘티",
            "matched_log_ids": [100],
            "matched_count": 1,
            "matched_utf8_bytes": 19,
            "transcript_sha256": "a".repeat(64),
            "attested_db_last_log_id": 100,
        }),
    );
    assert!(
        validate_cli_enrollment_authority(&one_row, 42, "부자멘토멘티", 100, &room_root).is_err()
    );

    let mut malformed = enrollment_fixture(&room_root, identity);
    malformed["targets"][0]["identity"]["matched_count"] = serde_json::Value::from(2);
    assert!(
        validate_cli_enrollment_authority(&malformed, 42, "부자멘토멘티", 100, &room_root,)
            .is_err()
    );
    malformed["targets"][0]["identity"]["matched_count"] = serde_json::Value::from(3);
    malformed["schema_version"] = serde_json::Value::from(3);
    assert!(
        validate_cli_enrollment_authority(&malformed, 42, "부자멘토멘티", 100, &room_root,)
            .is_err()
    );

    let mut replay_floor = enrollment_fixture(
        &room_root,
        serde_json::json!({
            "schema_version": 1,
            "kind": "ax_transcript",
            "local_name": "",
            "ax_name": "부자멘토멘티",
            "matched_log_ids": [10, 11, 12],
            "matched_count": 3,
            "matched_utf8_bytes": 24,
            "transcript_sha256": "a".repeat(64),
            "attested_db_last_log_id": 101,
        }),
    );
    replay_floor["targets"][0]["last_log_id"] = serde_json::Value::from(100);
    replay_floor["targets"][0]["cursor_authority"] = serde_json::json!({
        "schema_version": AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION,
        "kind": AUTO_REPLY_CURSOR_REPLAY_KIND,
        "cursor_floor": 100,
        "attested_db_last_log_id": 101,
        "prior_owner_id": "prior-owner",
        "prior_source_epoch": 7,
    });
    validate_cli_enrollment_authority(&replay_floor, 42, "부자멘토멘티", 100, &room_root)
        .expect("an ACK floor before the fresh attested tail must replay safely");

    let mut missing_replay_contract = replay_floor.clone();
    missing_replay_contract["targets"][0]["cursor_authority"] = serde_json::json!({
        "schema_version": AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION,
        "kind": AUTO_REPLY_CURSOR_FRESH_KIND,
        "cursor_floor": 100,
        "attested_db_last_log_id": 101,
        "prior_owner_id": null,
        "prior_source_epoch": null,
    });
    assert!(validate_cli_enrollment_authority(
        &missing_replay_contract,
        42,
        "부자멘토멘티",
        100,
        &room_root,
    )
    .is_err());

    let mut old_runtime_schema = replay_floor.clone();
    old_runtime_schema["schema_version"] = serde_json::Value::from(3);
    assert!(validate_cli_enrollment_authority(
        &old_runtime_schema,
        42,
        "부자멘토멘티",
        100,
        &room_root,
    )
    .is_err());

    replay_floor["targets"][0]["identity"]["attested_db_last_log_id"] = serde_json::Value::from(11);
    assert!(
        validate_cli_enrollment_authority(&replay_floor, 42, "부자멘토멘티", 100, &room_root,)
            .is_err()
    );
}

#[test]
fn numeric_reply_author_binding_fails_closed_on_collision_self_and_drift() {
    let configured = std::collections::BTreeSet::from(["문승현".to_string(), "현준".to_string()]);
    let identities = vec![
        local_db::LocalAuthorIdentity {
            author_id: 700,
            nickname: "문승현".to_string(),
            is_self: false,
        },
        local_db::LocalAuthorIdentity {
            author_id: 701,
            nickname: "현준".to_string(),
            is_self: false,
        },
    ];
    let bindings =
        resolve_auto_reply_author_bindings("부자멘토멘티", 999, &identities, &configured)
            .expect("unique non-self identities should enroll");
    assert_eq!(
        bindings,
        vec![
            AutoReplyAuthorBinding {
                nickname: "문승현".to_string(),
                author_id: 700,
            },
            AutoReplyAuthorBinding {
                nickname: "현준".to_string(),
                author_id: 701,
            },
        ]
    );

    let collision = [
        identities.clone(),
        vec![local_db::LocalAuthorIdentity {
            author_id: 702,
            nickname: "현준".to_string(),
            is_self: false,
        }],
    ]
    .concat();
    assert!(
        resolve_auto_reply_author_bindings("부자멘토멘티", 999, &collision, &configured,).is_err()
    );

    let ambiguous_name_rows = vec![
        local_db::LocalAuthorIdentity {
            author_id: 701,
            nickname: "현준".to_string(),
            is_self: false,
        },
        local_db::LocalAuthorIdentity {
            author_id: 701,
            nickname: "현준(다른표시명)".to_string(),
            is_self: false,
        },
    ];
    assert!(resolve_auto_reply_author_bindings(
        "부자멘토멘티",
        999,
        &ambiguous_name_rows,
        &std::collections::BTreeSet::from(["현준".to_string()]),
    )
    .is_err());

    let self_bound = vec![local_db::LocalAuthorIdentity {
        author_id: 999,
        nickname: "현준".to_string(),
        is_self: true,
    }];
    assert!(resolve_auto_reply_author_bindings(
        "부자멘토멘티",
        999,
        &self_bound,
        &std::collections::BTreeSet::from(["현준".to_string()]),
    )
    .is_err());

    let inconsistent_self_proof = vec![local_db::LocalAuthorIdentity {
        author_id: 701,
        nickname: "현준".to_string(),
        is_self: true,
    }];
    assert!(resolve_auto_reply_author_bindings(
        "부자멘토멘티",
        999,
        &inconsistent_self_proof,
        &std::collections::BTreeSet::from(["현준".to_string()]),
    )
    .is_err());
}

#[test]
fn cli_enrollment_v4_reply_author_binding_is_strict() {
    let root = tempfile::tempdir().expect("temporary state root");
    let room_root = root.path().join("rooms/42");
    fs::create_dir_all(&room_root).expect("create room root");
    let identity = serde_json::json!({
        "schema_version": 1,
        "kind": "local_name",
        "local_name": "부자멘토멘티",
        "ax_name": "부자멘토멘티",
    });
    let enrollment = enrollment_fixture(&room_root, identity);
    require_cli_enrolled_reply_author(&enrollment, 42, 700, "현준")
        .expect("matching numeric identity should pass");
    assert!(require_cli_enrolled_reply_author(&enrollment, 42, 701, "현준").is_err());
    assert!(require_cli_enrolled_reply_author(&enrollment, 42, 700, "동명이인").is_err());

    let mut duplicate_id = enrollment.clone();
    duplicate_id["targets"][0]["reply_author_bindings"] = serde_json::json!([
        {"nickname": "문승현", "author_id": 700},
        {"nickname": "현준", "author_id": 700}
    ]);
    assert!(
        validate_cli_enrollment_authority(&duplicate_id, 42, "부자멘토멘티", 100, &room_root,)
            .is_err()
    );
}

#[test]
fn multi_room_enrollment_keeps_numeric_author_bindings_disjoint() {
    let root = tempfile::tempdir().expect("temporary state root");
    let root_path = root.path().canonicalize().expect("canonical state root");
    let targets = [
        local_db::LocalChat {
            chat_id: 42,
            chat_type: 0,
            chat_name: "room-a".to_string(),
            database_chat_name: Some("room-a".to_string()),
            active_members_count: 2,
            last_log_id: 100,
            last_updated_at: 0,
            unread_count: 0,
            display_name: "room-a".to_string(),
        },
        local_db::LocalChat {
            chat_id: 84,
            chat_type: 0,
            chat_name: "room-b".to_string(),
            database_chat_name: Some("room-b".to_string()),
            active_members_count: 2,
            last_log_id: 200,
            last_updated_at: 0,
            unread_count: 0,
            display_name: "room-b".to_string(),
        },
    ];
    for target in &targets {
        fs::create_dir_all(root_path.join("rooms").join(target.chat_id.to_string()))
            .expect("create independent room root");
    }
    let author_bindings = std::collections::BTreeMap::from([
        (
            42,
            vec![AutoReplyAuthorBinding {
                nickname: "alice".to_string(),
                author_id: 700,
            }],
        ),
        (
            84,
            vec![AutoReplyAuthorBinding {
                nickname: "bob".to_string(),
                author_id: 800,
            }],
        ),
    ]);
    let (floors, _) = write_auto_reply_enrollment(
        &root_path,
        &["id:42".to_string(), "id:84".to_string()],
        &targets,
        &[],
        &author_bindings,
        &root_path.join("runtime"),
    )
    .expect("write independent multi-room enrollment");
    assert_eq!(floors, [100, 200]);
    let enrollment = read_bounded_json_file(&root_path.join("enrollment.json"))
        .expect("read multi-room enrollment");
    assert_eq!(
        enrollment["targets"][0]["room_state_root"],
        serde_json::json!(root_path.join("rooms/42"))
    );
    assert_eq!(
        enrollment["targets"][1]["room_state_root"],
        serde_json::json!(root_path.join("rooms/84"))
    );
    assert_eq!(
        enrollment["targets"][0]["reply_author_bindings"],
        serde_json::json!([{"nickname": "alice", "author_id": 700}])
    );
    assert_eq!(
        enrollment["targets"][1]["reply_author_bindings"],
        serde_json::json!([{"nickname": "bob", "author_id": 800}])
    );
    assert_ne!(
        enrollment["targets"][0]["reply_author_bindings"],
        enrollment["targets"][1]["reply_author_bindings"]
    );
}

#[test]
fn multi_room_supervisor_commands_keep_author_policy_disjoint() {
    fn env_value(command: &Command, key: &str) -> String {
        command
            .get_envs()
            .find(|(candidate, _)| *candidate == std::ffi::OsStr::new(key))
            .and_then(|(_, value)| value)
            .expect("expected supervisor environment entry")
            .to_string_lossy()
            .into_owned()
    }
    fn args(command: &Command) -> Vec<String> {
        command
            .get_args()
            .map(|value| value.to_string_lossy().into_owned())
            .collect()
    }

    let root = tempfile::tempdir().expect("temporary shared state root");
    let mut room_a = Command::new("supervisor");
    configure_auto_reply_supervisor_shared_state(&mut room_a, root.path());
    configure_auto_reply_supervisor_author_policy(
        &mut room_a,
        &["alice".to_string()],
        r#"[{"nickname":"alice","author_id":700}]"#,
    );
    let mut room_b = Command::new("supervisor");
    configure_auto_reply_supervisor_shared_state(&mut room_b, root.path());
    configure_auto_reply_supervisor_author_policy(
        &mut room_b,
        &["bob".to_string()],
        r#"[{"nickname":"bob","author_id":800}]"#,
    );

    assert_eq!(args(&room_a), ["--reply-author", "alice"]);
    assert_eq!(args(&room_b), ["--reply-author", "bob"]);
    assert_eq!(env_value(&room_a, "OPENKAKAO_REPLY_AUTHORS"), "alice");
    assert_eq!(env_value(&room_b, "OPENKAKAO_REPLY_AUTHORS"), "bob");
    let expected_circuit = root.path().join("model-circuit.sqlite3");
    assert_eq!(
        env_value(&room_a, "OPENKAKAO_MODEL_CIRCUIT_DB"),
        expected_circuit.to_string_lossy()
    );
    assert_eq!(
        env_value(&room_b, "OPENKAKAO_MODEL_CIRCUIT_DB"),
        expected_circuit.to_string_lossy()
    );
    assert_eq!(
        env_value(&room_a, "OPENKAKAO_REPLY_AUTHOR_BINDINGS"),
        r#"[{"nickname":"alice","author_id":700}]"#
    );
    assert_eq!(
        env_value(&room_b, "OPENKAKAO_REPLY_AUTHOR_BINDINGS"),
        r#"[{"nickname":"bob","author_id":800}]"#
    );
    assert!(!args(&room_a).iter().any(|value| value.contains("bob")));
    assert!(!args(&room_b).iter().any(|value| value.contains("alice")));
}

#[test]
fn auto_reply_enrollment_floor_seeds_fresh_install_and_rejects_dirty_state() {
    let root = tempfile::tempdir().expect("temporary state root");
    let root_path = root.path().canonicalize().expect("canonical state root");
    let room_root = root_path.join("rooms/42");
    fs::create_dir_all(&room_root).expect("create room root");
    let target = local_db::LocalChat {
        chat_id: 42,
        chat_type: 0,
        chat_name: "부자멘토멘티".to_string(),
        database_chat_name: Some("부자멘토멘티".to_string()),
        active_members_count: 4,
        last_log_id: 120,
        last_updated_at: 0,
        unread_count: 0,
        display_name: "부자멘토멘티".to_string(),
    };
    let state_path = room_root.join("db-watch-state.json");
    let clean_state = |watermark: i64| {
        serde_json::json!({
            "schema_version": 3,
            "target_chat_id": 42,
            "target_chat_name": "부자멘토멘티",
            "cursor_floor": 50,
            "acked_watermark": watermark,
            "last_observed_log_id": watermark,
            "observed_log_ids": [watermark],
            "acked_log_ids": [watermark],
            "pending_log_ids": [],
            "pending_gaps": [],
            "candidate_phase": "idle",
            "in_flight_candidate": null,
            "capability_state": "ready",
            "delivery_enabled": true,
            "fence": "ready",
            "fence_reason": "",
            "owner_id": "terminal-owner",
            "source_epoch": 7,
        })
    };

    assert_eq!(
        enrollment_floor_for_target(&root_path, &target, 120)
            .expect("a fresh install must seed at the attested DB tail"),
        120
    );
    let binding = AutoReplyBindingEvidence {
        chat_id: 42,
        ax_name: "부자멘토멘티".to_string(),
        local_name: String::new(),
        matched_log_ids: vec![78, 79, 80],
        matched_count: 3,
        matched_utf8_bytes: 24,
        transcript_sha256: "a".repeat(64),
        attested_db_last_log_id: 120,
    };
    let (written_floors, _) = write_auto_reply_enrollment(
        &root_path,
        &["bind:42:부자멘토멘티".to_string()],
        std::slice::from_ref(&target),
        &[binding],
        &std::collections::BTreeMap::from([(
            42,
            vec![AutoReplyAuthorBinding {
                nickname: "현준".to_string(),
                author_id: 700,
            }],
        )]),
        &root_path.join("runtime"),
    )
    .expect("write enrollment from the fresh binding attestation");
    assert_eq!(written_floors, vec![120]);
    let written_enrollment = read_bounded_json_file(&root_path.join("enrollment.json"))
        .expect("read written enrollment");
    assert_eq!(
        written_enrollment["targets"][0]["last_log_id"],
        serde_json::Value::from(120)
    );
    assert_eq!(
        written_enrollment["targets"][0]["identity"]["attested_db_last_log_id"],
        serde_json::Value::from(120)
    );
    assert_eq!(
        written_enrollment["schema_version"],
        serde_json::Value::from(AUTO_REPLY_ENROLLMENT_SCHEMA_VERSION)
    );
    assert_eq!(
        written_enrollment["targets"][0]["cursor_authority"],
        serde_json::json!({
            "schema_version": AUTO_REPLY_CURSOR_AUTHORITY_SCHEMA_VERSION,
            "kind": AUTO_REPLY_CURSOR_FRESH_KIND,
            "cursor_floor": 120,
            "attested_db_last_log_id": 120,
            "prior_owner_id": null,
            "prior_source_epoch": null,
        })
    );

    fs::write(
        &state_path,
        serde_json::to_vec(&clean_state(130)).expect("serialize running state"),
    )
    .expect("write running state");
    assert!(enrollment_floor_for_target(&root_path, &target, 140).is_err());

    let mut unresolved = clean_state(130);
    unresolved["pending_gaps"] = serde_json::json!(["reconcile_required"]);
    fs::write(
        &state_path,
        serde_json::to_vec(&unresolved).expect("serialize unresolved state"),
    )
    .expect("rewrite unresolved state");
    assert!(enrollment_floor_for_target(&root_path, &target, 140).is_err());
}

fn stopped_clean_queue_fixture(
    path: &Path,
    include_supersessions: bool,
    breaker_schema: &str,
    extra_schema: &str,
    job_status: &str,
) {
    let supersessions = if include_supersessions {
        "CREATE TABLE reply_job_supersessions(
                event_id TEXT PRIMARY KEY,
                superseded_by_event_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                CHECK(event_id <> superseded_by_event_id)
            );"
    } else {
        ""
    };
    let sql = format!(
        "
            CREATE TABLE reply_jobs(
                event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL,
                status TEXT NOT NULL, due_at REAL, decision TEXT,
                reason TEXT, category TEXT, reply TEXT,
                scheduled_delay_seconds REAL, error_class TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX idx_reply_jobs_status_due
                ON reply_jobs(status, due_at);
            CREATE TABLE reply_job_tombstones(
                event_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                archived_at REAL NOT NULL
            );
            {supersessions}
            {breaker_schema}
            {extra_schema}
            INSERT INTO reply_jobs VALUES(
                'db:42:1', '{{\"chat_id\":42,\"log_id\":1}}', '{job_status}', NULL, 'skip', 'test',
                'test', NULL, NULL, NULL, 1.0, 1.0
            );
            INSERT INTO reply_job_tombstones VALUES('db:42:2', 'sent', 1.0);
            "
    );
    let connection = rusqlite::Connection::open(path).expect("create queue fixture");
    connection
        .execute_batch(&sql)
        .expect("initialize queue fixture");
    drop(connection);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }
}

const EXACT_BREAKER_SCHEMA: &str = "
        CREATE TABLE model_circuit_breaker(
            model_key TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            open_until REAL NOT NULL,
            lease_token TEXT,
            updated_at REAL NOT NULL
        );";

fn stopped_clean_queue_test_room(root: &Path) -> PathBuf {
    let room = root.join("42");
    fs::create_dir_all(&room).expect("create stopped-clean test room");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&room, fs::Permissions::from_mode(0o700))
            .expect("make stopped-clean test room private");
    }
    room
}

fn stopped_clean_queue_v2_fixture_with_table(
    path: &Path,
    include_breaker: bool,
    journal_table_sql: &str,
) {
    stopped_clean_queue_fixture(
        path,
        true,
        if include_breaker {
            EXACT_BREAKER_SCHEMA
        } else {
            ""
        },
        "",
        "sent",
    );
    let connection = rusqlite::Connection::open(path).expect("open v2 queue fixture");
    connection
        .execute_batch(
            "DROP INDEX idx_reply_jobs_status_due;
                 ALTER TABLE reply_jobs RENAME TO reply_jobs_v0;",
        )
        .expect("prepare reply_jobs v2 rebuild");
    connection
        .execute_batch(AUTO_REPLY_REPLY_JOBS_V2_TABLE_SQL)
        .expect("create reply_jobs v2 table");
    connection
        .execute_batch(
            "INSERT INTO reply_jobs(
                   event_id,event_json,status,due_at,decision,reason,category,reply,
                   scheduled_delay_seconds,error_class,created_at,updated_at,attempt_no
                 ) SELECT event_id,event_json,status,due_at,decision,reason,category,reply,
                          scheduled_delay_seconds,error_class,created_at,updated_at,0
                   FROM reply_jobs_v0;
                 DROP TABLE reply_jobs_v0;",
        )
        .expect("copy reply_jobs v2 rows");
    connection
        .execute_batch(AUTO_REPLY_REPLY_JOBS_STATUS_INDEX_SQL)
        .expect("recreate reply_jobs status index");
    connection
        .execute_batch(journal_table_sql)
        .expect("create transition journal table");
    connection
        .execute_batch(AUTO_REPLY_PIPELINE_TRANSITIONS_INDEX_SQL)
        .expect("create transition journal index");
    connection
        .execute_batch(AUTO_REPLY_PIPELINE_TRANSITIONS_INSERT_TRIGGER_SQL)
        .expect("create transition insert trigger");
    connection
        .execute_batch(AUTO_REPLY_PIPELINE_TRANSITIONS_UPDATE_TRIGGER_SQL)
        .expect("create transition update trigger");
    connection
        .execute_batch(AUTO_REPLY_PIPELINE_TRANSITIONS_CAP_TRIGGER_SQL)
        .expect("create transition cap trigger");
    connection
        .pragma_update(None, "user_version", AUTO_REPLY_QUEUE_JOURNAL_USER_VERSION)
        .expect("set v2 queue user_version");
}

fn stopped_clean_queue_v2_fixture(path: &Path, include_breaker: bool) {
    stopped_clean_queue_v2_fixture_with_table(
        path,
        include_breaker,
        AUTO_REPLY_PIPELINE_TRANSITIONS_TABLE_SQL,
    );
}

#[test]
fn stopped_clean_queue_accepts_exact_legacy_v0_and_journal_v2_read_only() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let legacy = room.join("legacy.sqlite3");
    stopped_clean_queue_fixture(&legacy, true, "", "", "skipped");
    validate_stopped_clean_queue(&legacy, 42).expect("exact legacy queue must be accepted");
    let connection =
        rusqlite::Connection::open_with_flags(&legacy, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    let tables = connection
        .prepare(
            "SELECT name FROM sqlite_master
                 WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
        .unwrap()
        .query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert_eq!(
        tables,
        [
            "reply_job_supersessions",
            "reply_job_tombstones",
            "reply_jobs",
        ]
    );
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        AUTO_REPLY_QUEUE_LEGACY_USER_VERSION
    );
    drop(connection);

    let legacy_with_breaker = room.join("legacy-with-breaker.sqlite3");
    stopped_clean_queue_fixture(&legacy_with_breaker, true, EXACT_BREAKER_SCHEMA, "", "sent");
    validate_stopped_clean_queue(&legacy_with_breaker, 42)
        .expect("exact legacy queue with breaker must be accepted");

    for include_breaker in [false, true] {
        let queue = room.join(format!("v2-{include_breaker}.sqlite3"));
        stopped_clean_queue_v2_fixture(&queue, include_breaker);
        validate_stopped_clean_queue(&queue, 42).expect("exact v2 queue must be accepted");
        let connection = rusqlite::Connection::open_with_flags(
            &queue,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        assert_eq!(
            connection
                .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            AUTO_REPLY_QUEUE_JOURNAL_USER_VERSION
        );
        assert_eq!(
            connection
                .query_row("SELECT COUNT(*) FROM pipeline_transitions", [], |row| {
                    row.get::<_, i64>(0)
                })
                .unwrap(),
            0
        );
    }

    let canonical_python_style_v2 = room.join("canonical-python-style-v2.sqlite3");
    stopped_clean_queue_v2_fixture(&canonical_python_style_v2, true);
    let connection = rusqlite::Connection::open(&canonical_python_style_v2).unwrap();
    for (index, code) in [
        "authorization_allowed",
        "authorization_rejected",
        "media_acquire_started",
        "media_acquire_ready",
        "media_acquire_failed",
        "media_policy_rejected",
    ]
    .into_iter()
    .enumerate()
    {
        connection
            .execute(
                "INSERT INTO pipeline_transitions(
                         schema_version,event_id,attempt_no,component,from_state,
                         to_state,code,source_epoch,occurred_at_ns
                     ) VALUES(1,?1,0,'authorization','none','ready',?2,NULL,?3)",
                rusqlite::params![format!("db:42:{}", index + 10), code, index + 1],
            )
            .unwrap();
    }
    drop(connection);
    validate_stopped_clean_queue(&canonical_python_style_v2, 42)
        .expect("canonical helper-created v2 shape and vocabulary must be accepted");
}

#[test]
fn stopped_clean_queue_exactly_matches_python_helper_v2_schema() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let queue = room.join("reply-queue.sqlite3");
    let scripts = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let script = r#"
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
import auto_reply_transition_journal as journal
queue = pathlib.Path(sys.argv[2])
connection = journal.open_queue(queue, create=True, expected_chat_id=42)
connection.execute(
    "INSERT INTO reply_jobs("
    "event_id,event_json,status,due_at,decision,reason,category,reply,"
    "scheduled_delay_seconds,error_class,created_at,updated_at"
    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
    ("db:42:1", '{"chat_id":42,"log_id":1}', "sent", None, "skip",
     "test", "test", None, None, None, 1.0, 1.0),
)
connection.execute(
    "INSERT INTO reply_job_tombstones VALUES('db:42:2','sent',1.0)"
)
connection.execute(
    "INSERT INTO reply_job_supersessions VALUES('db:42:3','db:42:4',1.0)"
)
connection.commit()
connection.close()
"#;
    let output = Command::new("python3")
        .args(["-I", "-c", script])
        .arg(&scripts)
        .arg(&queue)
        .output()
        .expect("run Python transition helper");
    assert!(
        output.status.success(),
        "Python helper failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    validate_stopped_clean_queue(&queue, 42)
        .expect("Rust must accept the exact Python helper v2 schema");
}

#[test]
fn stopped_clean_queue_rejects_cross_room_path_and_durable_identities() {
    let root = tempfile::tempdir().unwrap();
    let room_42 = stopped_clean_queue_test_room(root.path());
    let queue_42 = room_42.join("reply-queue.sqlite3");
    stopped_clean_queue_v2_fixture(&queue_42, true);
    validate_stopped_clean_queue(&queue_42, 42).expect("matching room must validate");
    assert!(validate_stopped_clean_queue(&queue_42, 84).is_err());

    let room_84 = root.path().join("84");
    fs::create_dir_all(&room_84).unwrap();
    let queue_84 = room_84.join("reply-queue.sqlite3");
    fs::copy(&queue_42, &queue_84).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&queue_84, fs::Permissions::from_mode(0o600)).unwrap();
    }
    assert!(validate_stopped_clean_queue(&queue_84, 84).is_err());

    let connection = rusqlite::Connection::open(&queue_42).unwrap();
    connection
        .execute(
            "INSERT INTO reply_job_supersessions VALUES(\
                 'db:42:3','db:84:4',1.0)",
            [],
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue_42, 42).is_err());

    let connection = rusqlite::Connection::open(&queue_42).unwrap();
    connection
        .execute("DELETE FROM reply_job_supersessions", [])
        .unwrap();
    connection
        .execute("UPDATE reply_job_tombstones SET event_id='db:84:2'", [])
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue_42, 42).is_err());

    let connection = rusqlite::Connection::open(&queue_42).unwrap();
    connection
        .execute("UPDATE reply_job_tombstones SET event_id='db:42:2'", [])
        .unwrap();
    connection
        .execute(
            "UPDATE reply_jobs SET event_json='{\"chat_id\":42.0,\"log_id\":true}'",
            [],
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue_42, 42).is_err());

    let connection = rusqlite::Connection::open(&queue_42).unwrap();
    connection
        .execute(
            "UPDATE reply_jobs SET event_json='{\"chat_id\":42,\"log_id\":1}'",
            [],
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO pipeline_transitions(\
                 schema_version,event_id,attempt_no,component,from_state,to_state,\
                 code,source_epoch,occurred_at_ns) VALUES(\
                 1,'db:84:5',0,'queue','none','sent','enqueued',NULL,1)",
            [],
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue_42, 42).is_err());
}

#[test]
fn stopped_clean_queue_rejects_nonexact_or_malformed_table_sets() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let missing = room.join("missing.sqlite3");
    stopped_clean_queue_fixture(&missing, false, "", "", "sent");
    assert!(validate_stopped_clean_queue(&missing, 42).is_err());

    let extra = room.join("extra.sqlite3");
    stopped_clean_queue_fixture(
        &extra,
        true,
        "",
        "CREATE TABLE unexpected_table(value TEXT);",
        "sent",
    );
    assert!(validate_stopped_clean_queue(&extra, 42).is_err());

    let malformed = room.join("malformed.sqlite3");
    stopped_clean_queue_fixture(
        &malformed,
        true,
        "
                CREATE TABLE model_circuit_breaker(
                    model_key TEXT PRIMARY KEY, state TEXT NOT NULL,
                    failure_class TEXT NOT NULL,
                    consecutive_failures INTEGER NOT NULL,
                    open_until REAL NOT NULL, lease_token TEXT,
                    updated_at TEXT NOT NULL
                );",
        "",
        "sent",
    );
    assert!(validate_stopped_clean_queue(&malformed, 42).is_err());
}

#[test]
fn stopped_clean_queue_rejects_unknown_or_mismatched_user_versions() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    for version in [1, AUTO_REPLY_QUEUE_JOURNAL_USER_VERSION, 3] {
        let queue = room.join(format!("legacy-version-{version}.sqlite3"));
        stopped_clean_queue_fixture(&queue, true, "", "", "sent");
        let connection = rusqlite::Connection::open(&queue).unwrap();
        connection
            .pragma_update(None, "user_version", version)
            .unwrap();
        drop(connection);
        assert!(validate_stopped_clean_queue(&queue, 42).is_err());
    }

    for version in [AUTO_REPLY_QUEUE_LEGACY_USER_VERSION, 1, 3] {
        let queue = room.join(format!("v2-version-{version}.sqlite3"));
        stopped_clean_queue_v2_fixture(&queue, false);
        let connection = rusqlite::Connection::open(&queue).unwrap();
        connection
            .pragma_update(None, "user_version", version)
            .unwrap();
        drop(connection);
        assert!(validate_stopped_clean_queue(&queue, 42).is_err());
    }
}

#[test]
fn stopped_clean_queue_rejects_v2_table_index_and_trigger_tampering() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());

    let table = room.join("table.sqlite3");
    let tampered_table_sql = AUTO_REPLY_PIPELINE_TRANSITIONS_TABLE_SQL.replacen(
        "CHECK(schema_version = 1)",
        "CHECK(schema_version IN (1))",
        1,
    );
    stopped_clean_queue_v2_fixture_with_table(&table, false, &tampered_table_sql);
    assert!(validate_stopped_clean_queue(&table, 42).is_err());

    let index = room.join("index.sqlite3");
    stopped_clean_queue_v2_fixture(&index, false);
    let connection = rusqlite::Connection::open(&index).unwrap();
    connection
        .execute_batch(
            "DROP INDEX idx_pipeline_transitions_event_seq;
                 CREATE INDEX idx_pipeline_transitions_event_seq
                 ON pipeline_transitions(event_id, seq DESC);",
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&index, 42).is_err());

    let trigger = room.join("trigger.sqlite3");
    stopped_clean_queue_v2_fixture(&trigger, false);
    let connection = rusqlite::Connection::open(&trigger).unwrap();
    connection
        .execute_batch(
            "DROP TRIGGER trg_reply_jobs_transition_insert;
                 CREATE TRIGGER trg_reply_jobs_transition_insert
                 AFTER INSERT ON reply_jobs BEGIN SELECT 1; END;",
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&trigger, 42).is_err());

    let extra_table = room.join("extra-table.sqlite3");
    stopped_clean_queue_v2_fixture(&extra_table, false);
    let connection = rusqlite::Connection::open(&extra_table).unwrap();
    connection
        .execute_batch("CREATE TABLE unexpected_table(value TEXT);")
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&extra_table, 42).is_err());

    let extra_index = room.join("extra-index.sqlite3");
    stopped_clean_queue_v2_fixture(&extra_index, false);
    let connection = rusqlite::Connection::open(&extra_index).unwrap();
    connection
        .execute_batch("CREATE INDEX idx_unexpected ON reply_jobs(event_id);")
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&extra_index, 42).is_err());

    let extra_trigger = room.join("extra-trigger.sqlite3");
    stopped_clean_queue_v2_fixture(&extra_trigger, false);
    let connection = rusqlite::Connection::open(&extra_trigger).unwrap();
    connection
        .execute_batch(
            "CREATE TRIGGER trg_unexpected AFTER DELETE ON reply_jobs
                 BEGIN SELECT 1; END;",
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&extra_trigger, 42).is_err());
}

#[test]
fn stopped_clean_queue_rejects_v2_journal_above_hard_cap() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let queue = room.join("over-cap.sqlite3");
    stopped_clean_queue_v2_fixture(&queue, false);
    let mut connection = rusqlite::Connection::open(&queue).unwrap();
    connection
        .execute_batch("DROP TRIGGER trg_pipeline_transitions_cap;")
        .unwrap();
    let transaction = connection.transaction().unwrap();
    {
        let mut insert = transaction
            .prepare(
                "INSERT INTO pipeline_transitions(
                         schema_version,event_id,attempt_no,component,from_state,
                         to_state,code,source_epoch,occurred_at_ns
                     ) VALUES(1,?1,0,'queue','none','pending','enqueued',NULL,?2)",
            )
            .unwrap();
        for sequence in 1..=AUTO_REPLY_QUEUE_JOURNAL_MAX_ROWS + 1 {
            insert
                .execute(rusqlite::params![format!("db:42:{sequence}"), sequence])
                .unwrap();
        }
    }
    transaction.commit().unwrap();
    connection
        .execute_batch(AUTO_REPLY_PIPELINE_TRANSITIONS_CAP_TRIGGER_SQL)
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue, 42).is_err());
}

#[test]
fn stopped_clean_queue_rejects_v2_invalid_journal_metadata() {
    assert!(parse_auto_reply_transition_event_id("db:1:1").is_some());
    assert!(
        parse_auto_reply_transition_event_id("db:9223372036854775806:9223372036854775806")
            .is_some()
    );
    for invalid in [
        "db:0:1",
        "db:01:1",
        "db:1:01",
        "db:1x:2",
        "db:1:2x",
        "db:1:1:2",
        "DB:1:1",
        "db:9223372036854775807:1",
        "db:1:9223372036854775807",
    ] {
        assert!(parse_auto_reply_transition_event_id(invalid).is_none());
    }
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let queue = room.join("invalid-journal-metadata.sqlite3");
    stopped_clean_queue_v2_fixture(&queue, false);
    let connection = rusqlite::Connection::open(&queue).unwrap();
    connection
        .execute_batch("PRAGMA ignore_check_constraints = ON;")
        .unwrap();
    connection
        .execute(
            "INSERT INTO pipeline_transitions(
                     schema_version,event_id,attempt_no,component,from_state,
                     to_state,code,source_epoch,occurred_at_ns
                 ) VALUES(1,'db:42:99',0,'queue','none','pending',
                          'private-free-form-code',NULL,1)",
            [],
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue, 42).is_err());
}

#[test]
fn stopped_clean_queue_rejects_v2_invalid_reply_job_attempt() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let queue = room.join("invalid-reply-job-attempt.sqlite3");
    stopped_clean_queue_v2_fixture(&queue, false);
    let connection = rusqlite::Connection::open(&queue).unwrap();
    connection
        .execute_batch(
            "PRAGMA ignore_check_constraints = ON;
                 UPDATE reply_jobs SET attempt_no = 1000001
                 WHERE event_id = 'db:42:1';",
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&queue, 42).is_err());
}

#[test]
fn stopped_clean_queue_rejects_nonterminal_rows() {
    let root = tempfile::tempdir().unwrap();
    let room = stopped_clean_queue_test_room(root.path());
    let queue = room.join("nonterminal.sqlite3");
    stopped_clean_queue_fixture(&queue, true, "", "", "delivery_unknown");
    assert!(validate_stopped_clean_queue(&queue, 42).is_err());

    let tombstone_queue = room.join("nonterminal-tombstone.sqlite3");
    stopped_clean_queue_fixture(&tombstone_queue, true, "", "", "sent");
    let connection = rusqlite::Connection::open(&tombstone_queue).unwrap();
    connection
        .execute(
            "UPDATE reply_job_tombstones SET status = 'delivery_unknown'",
            [],
        )
        .unwrap();
    drop(connection);
    assert!(validate_stopped_clean_queue(&tombstone_queue, 42).is_err());
}

#[test]
fn auto_reply_accepts_only_fully_attested_stopped_clean_state() {
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().expect("temporary state root");
    let room_root = root.path().join("rooms/42");
    fs::create_dir_all(&room_root).expect("create room root");
    let target = local_db::LocalChat {
        chat_id: 42,
        chat_type: 0,
        chat_name: "부자멘토멘티".to_string(),
        database_chat_name: Some("부자멘토멘티".to_string()),
        active_members_count: 4,
        last_log_id: 140,
        last_updated_at: 0,
        unread_count: 0,
        display_name: "부자멘토멘티".to_string(),
    };
    let state_path = room_root.join("db-watch-state.json");
    let status_path = room_root.join("supervisor-status.json");
    let queue_path = room_root.join("reply-queue.sqlite3");
    let stopped_state = serde_json::json!({
        "schema_version": 3,
        "target_chat_id": 42,
        "target_chat_name": "부자멘토멘티",
        "cursor_floor": 100,
        "acked_watermark": 130,
        "last_observed_log_id": 130,
        "observed_log_ids": [130],
        "acked_log_ids": [130],
        "pending_log_ids": [],
        "pending_gaps": [],
        "candidate_phase": "idle",
        "in_flight_candidate": null,
        "capability_state": "stopped_clean",
        "delivery_enabled": false,
        "fence": "stopped_clean",
        "fence_reason": "",
        "owner_id": "terminal-owner",
        "source_epoch": 7,
    });
    let stopped_status = serde_json::json!({
        "schema_version": 1,
        "mode": "database_authoritative",
        "state": "stopped",
        "shutdown_state": "stopped_clean",
        "all_children_exited": true,
        "readiness": "fenced",
        "fence_reason": "stopped_clean",
        "owner": "terminal-owner",
        "source_epoch": 7,
        "target_chat_id": 42,
        "target_chat_name": "부자멘토멘티",
        "child_states": {
            "ax_watch": "exited",
            "db_watch": "exited",
            "reply_worker": "exited",
        },
    });
    fs::write(&state_path, serde_json::to_vec(&stopped_state).unwrap()).unwrap();
    fs::write(&status_path, serde_json::to_vec(&stopped_status).unwrap()).unwrap();
    let connection = rusqlite::Connection::open(&queue_path).expect("create queue");
    connection
        .execute_batch(
            "
                CREATE TABLE reply_jobs(
                    event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL,
                    status TEXT NOT NULL, due_at REAL, decision TEXT,
                    reason TEXT, category TEXT, reply TEXT,
                    scheduled_delay_seconds REAL, error_class TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX idx_reply_jobs_status_due
                    ON reply_jobs(status, due_at);
                CREATE TABLE reply_job_tombstones(
                    event_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    archived_at REAL NOT NULL
                );
                CREATE TABLE reply_job_supersessions(
                    event_id TEXT PRIMARY KEY,
                    superseded_by_event_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    CHECK(event_id <> superseded_by_event_id)
                );
                CREATE TABLE model_circuit_breaker(
                    model_key TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    failure_class TEXT NOT NULL,
                    consecutive_failures INTEGER NOT NULL,
                    open_until REAL NOT NULL,
                    lease_token TEXT,
                    updated_at REAL NOT NULL
                );
                INSERT INTO reply_jobs VALUES(
                    'db:42:1', '{\"chat_id\":42,\"log_id\":1}',
                    'skipped', NULL, 'skip', 'test',
                    'test', NULL, NULL, NULL, 1.0, 1.0
                );
                ",
        )
        .expect("initialize queue schema");
    drop(connection);
    #[cfg(unix)]
    for path in [&state_path, &status_path, &queue_path] {
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }
    assert_eq!(
        enrollment_floor_for_target(root.path(), &target, 140)
            .expect("complete stopped-clean proof must be accepted"),
        130
    );
    let regressed_target = local_db::LocalChat {
        last_log_id: 120,
        ..target.clone()
    };
    assert!(enrollment_floor_for_target(root.path(), &regressed_target, 120).is_err());
    assert_eq!(
        enrollment_cursor_authority_for_target(root.path(), &target, 140)
            .expect("stopped-clean replay authority must be explicit"),
        AutoReplyCursorAuthority {
            kind: AUTO_REPLY_CURSOR_REPLAY_KIND,
            cursor_floor: 130,
            attested_db_last_log_id: 140,
            prior_owner_id: Some("terminal-owner".to_string()),
            prior_source_epoch: Some(7),
        }
    );

    let connection = rusqlite::Connection::open(&queue_path).unwrap();
    connection
        .execute(
            "UPDATE reply_jobs SET status = 'delivery_unknown' \
                 WHERE event_id = 'db:42:1'",
            [],
        )
        .unwrap();
    drop(connection);
    assert!(enrollment_floor_for_target(root.path(), &target, 140).is_err());
    let connection = rusqlite::Connection::open(&queue_path).unwrap();
    connection
        .execute(
            "UPDATE reply_jobs SET status = 'sent' WHERE event_id = 'db:42:1'",
            [],
        )
        .unwrap();
    drop(connection);

    let mut bad_status = stopped_status.clone();
    bad_status["all_children_exited"] = serde_json::Value::Bool(false);
    fs::write(&status_path, serde_json::to_vec(&bad_status).unwrap()).unwrap();
    #[cfg(unix)]
    fs::set_permissions(&status_path, fs::Permissions::from_mode(0o600)).unwrap();
    assert!(enrollment_floor_for_target(root.path(), &target, 140).is_err());

    let mut leftover_status = stopped_status.clone();
    leftover_status["shutdown_state"] = serde_json::json!("stopped_unclean");
    leftover_status["fence_reason"] = serde_json::json!("db_watch_exited");
    leftover_status["readiness"] = serde_json::json!("fenced");
    leftover_status["all_children_exited"] = serde_json::json!(true);
    leftover_status["child_states"] = serde_json::json!({
        "ax_watch": "exited",
        "db_watch": "exited",
        "reply_worker": "exited",
    });
    fs::write(&status_path, serde_json::to_vec(&leftover_status).unwrap()).unwrap();
    let mut raw_poll_fence = stopped_state;
    raw_poll_fence["capability_state"] = serde_json::json!("fenced");
    raw_poll_fence["fence"] = serde_json::json!("db_unavailable");
    raw_poll_fence["fence_reason"] = serde_json::json!("poll_fence");
    raw_poll_fence["delivery_enabled"] = serde_json::json!(false);
    fs::write(&state_path, serde_json::to_vec(&raw_poll_fence).unwrap()).unwrap();
    #[cfg(unix)]
    for path in [&state_path, &status_path] {
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }
    assert_eq!(
        enrollment_cursor_authority_for_target(root.path(), &target, 140)
            .expect("terminal leftover ACK resume must be accepted")
            .kind,
        AUTO_REPLY_CURSOR_LEFTOVER_KIND
    );
    raw_poll_fence["candidate_phase"] = serde_json::json!("hooking");
    raw_poll_fence["in_flight_candidate"] = serde_json::json!({
        "event_id": "db:1:130",
        "log_id": 130,
        "owner_id": raw_poll_fence.get("owner_id").cloned().unwrap_or(serde_json::json!("")),
        "in_flight": true,
        "pending": true
    });
    raw_poll_fence["pending_log_ids"] = serde_json::json!([130]);
    fs::write(&state_path, serde_json::to_vec(&raw_poll_fence).unwrap()).unwrap();
    #[cfg(unix)]
    fs::set_permissions(&state_path, fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(
        enrollment_cursor_authority_for_target(root.path(), &target, 140)
            .expect("orphaned hooking leftover ACK resume must be accepted")
            .kind,
        AUTO_REPLY_CURSOR_LEFTOVER_KIND
    );
    raw_poll_fence["capability_state"] = serde_json::json!("ready");
    raw_poll_fence["delivery_enabled"] = serde_json::json!(true);
    raw_poll_fence["fence"] = serde_json::json!("ready");
    raw_poll_fence["fence_reason"] = serde_json::json!("");
    fs::write(&state_path, serde_json::to_vec(&raw_poll_fence).unwrap()).unwrap();
    #[cfg(unix)]
    fs::set_permissions(&state_path, fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(
        enrollment_cursor_authority_for_target(root.path(), &target, 140)
            .expect("ready hooking leftover ACK resume must be accepted")
            .kind,
        AUTO_REPLY_CURSOR_LEFTOVER_KIND
    );
    let leftover = read_bounded_json_file(&state_path).expect("leftover state remains");
    assert_eq!(leftover["acked_watermark"], 130);
    assert_eq!(leftover["last_observed_log_id"], 130);
    assert_ne!(leftover["fence"], "stopped_clean");
    leftover_status["all_children_exited"] = serde_json::json!(false);
    fs::write(&status_path, serde_json::to_vec(&leftover_status).unwrap()).unwrap();
    #[cfg(unix)]
    fs::set_permissions(&status_path, fs::Permissions::from_mode(0o600)).unwrap();
    assert!(enrollment_floor_for_target(root.path(), &target, 140).is_err());
}

#[test]
fn auto_reply_rejects_residual_legacy_state_without_status() {
    let root = tempfile::tempdir().expect("temporary state root");
    fs::write(root.path().join("reply-queue.sqlite3"), b"legacy").expect("write residual queue");
    assert!(auto_reply_legacy_conflict(root.path()).is_err());
}

#[test]
#[cfg(unix)]
fn auto_reply_owner_lock_is_private_exclusive_and_no_follow() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempfile::tempdir().expect("temporary state root");
    let root_path = root.path().canonicalize().expect("canonical state root");
    let lock_path = root_path.join("supervisor.owner.lock");
    fs::write(&lock_path, b"old").unwrap();
    fs::set_permissions(&lock_path, fs::Permissions::from_mode(0o644)).unwrap();
    let lock = acquire_auto_reply_owner_lock(&root_path).expect("acquire owner lock");
    assert_eq!(
        fs::metadata(&lock_path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert!(acquire_auto_reply_owner_lock(&root_path).is_err());
    drop(lock);
    fs::remove_file(&lock_path).unwrap();
    let victim = root_path.join("victim");
    fs::write(&victim, b"do-not-touch").unwrap();
    std::os::unix::fs::symlink(&victim, &lock_path).unwrap();
    assert!(acquire_auto_reply_owner_lock(&root_path).is_err());
    assert_eq!(fs::read(&victim).unwrap(), b"do-not-touch");
}

#[test]
#[cfg(unix)]
fn auto_reply_private_state_and_enrollment_ignore_umask_022() {
    use std::os::unix::fs::PermissionsExt;

    struct UmaskGuard(libc::mode_t);
    impl Drop for UmaskGuard {
        fn drop(&mut self) {
            unsafe {
                libc::umask(self.0);
            }
        }
    }

    let parent = tempfile::tempdir().expect("temporary state parent");
    let parent_path = parent
        .path()
        .canonicalize()
        .expect("canonical state parent");
    let root = parent_path.join("foreground-state");
    let previous = unsafe { libc::umask(0o022) };
    let _umask = UmaskGuard(previous);
    let lock = acquire_auto_reply_owner_lock(&root).expect("acquire private owner lock");
    let target = local_db::LocalChat {
        chat_id: 42,
        chat_type: 0,
        chat_name: "부자멘토멘티".to_string(),
        database_chat_name: Some("부자멘토멘티".to_string()),
        active_members_count: 4,
        last_log_id: 100,
        last_updated_at: 0,
        unread_count: 0,
        display_name: "부자멘토멘티".to_string(),
    };
    let room =
        prepare_auto_reply_room_state(&root, &target).expect("prepare private foreground room");
    write_private_auto_reply_enrollment(&root, br#"{"schema_version":4}"#)
        .expect("write private enrollment authority");
    write_private_auto_reply_enrollment(&root, br#"{"schema_version":5}"#)
        .expect("atomically replace private enrollment authority");

    for directory in [&root, &root.join("rooms"), &room] {
        assert_eq!(
            fs::symlink_metadata(directory)
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }
    for file in [
        root.join("supervisor.owner.lock"),
        root.join("enrollment.json"),
    ] {
        assert_eq!(
            fs::symlink_metadata(file).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    assert_eq!(
        fs::read(root.join("enrollment.json")).unwrap(),
        br#"{"schema_version":5}"#
    );
    assert!(fs::read_dir(&root).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".enrollment.")
    }));
    drop(lock);
}

#[test]
#[cfg(unix)]
fn auto_reply_private_state_rejects_symlinks_and_enrollment_hardlinks() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let parent = tempfile::tempdir().expect("temporary state parent");
    let parent_path = parent
        .path()
        .canonicalize()
        .expect("canonical state parent");
    let redirected_root = parent_path.join("redirected-root");
    fs::create_dir(&redirected_root).expect("create redirected root");
    fs::set_permissions(&redirected_root, fs::Permissions::from_mode(0o755)).unwrap();
    let root_link = parent_path.join("state-link");
    symlink(&redirected_root, &root_link).expect("create state-root symlink");
    assert!(acquire_auto_reply_owner_lock(&root_link).is_err());
    assert_eq!(
        fs::metadata(&redirected_root).unwrap().permissions().mode() & 0o777,
        0o755
    );

    let intermediate = parent_path.join("intermediate-link");
    symlink(&redirected_root, &intermediate).expect("create intermediate symlink");
    assert!(acquire_auto_reply_owner_lock(&intermediate.join("nested-state")).is_err());
    assert!(!redirected_root.join("nested-state").exists());

    let root = parent_path.join("state");
    let lock = acquire_auto_reply_owner_lock(&root).expect("acquire private root");
    let rooms = root.join("rooms");
    fs::create_dir(&rooms).expect("create rooms directory");
    let redirected_room = parent_path.join("redirected-room");
    fs::create_dir(&redirected_room).expect("create redirected room");
    fs::set_permissions(&redirected_room, fs::Permissions::from_mode(0o755)).unwrap();
    symlink(&redirected_room, rooms.join("42")).expect("create room symlink");
    let target = local_db::LocalChat {
        chat_id: 42,
        chat_type: 0,
        chat_name: "부자멘토멘티".to_string(),
        database_chat_name: Some("부자멘토멘티".to_string()),
        active_members_count: 4,
        last_log_id: 100,
        last_updated_at: 0,
        unread_count: 0,
        display_name: "부자멘토멘티".to_string(),
    };
    assert!(prepare_auto_reply_room_state(&root, &target).is_err());
    assert_eq!(
        fs::metadata(&redirected_room).unwrap().permissions().mode() & 0o777,
        0o755
    );

    let victim = parent_path.join("enrollment-victim");
    fs::write(&victim, b"do-not-touch").expect("write enrollment victim");
    fs::set_permissions(&victim, fs::Permissions::from_mode(0o600)).unwrap();
    let enrollment = root.join("enrollment.json");
    symlink(&victim, &enrollment).expect("create enrollment symlink");
    assert!(write_private_auto_reply_enrollment(&root, b"replacement").is_err());
    assert_eq!(fs::read(&victim).unwrap(), b"do-not-touch");

    fs::remove_file(&enrollment).expect("remove enrollment symlink");
    fs::hard_link(&victim, &enrollment).expect("create enrollment hard link");
    assert!(write_private_auto_reply_enrollment(&root, b"replacement").is_err());
    assert_eq!(fs::read(&victim).unwrap(), b"do-not-touch");
    drop(lock);
}

#[test]
fn auto_reply_accepts_only_attested_stopped_legacy_state() {
    let root = tempfile::tempdir().expect("temporary state root");
    let status_path = root.path().join("supervisor-status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&serde_json::json!({
            "state": "stopped",
            "legacy_drained": true,
        }))
        .expect("serialize status"),
    )
    .expect("write status");
    assert!(auto_reply_legacy_conflict(root.path()).is_ok());
    fs::write(
        &status_path,
        serde_json::to_vec(&serde_json::json!({
            "state": "stopped",
        }))
        .expect("serialize status"),
    )
    .expect("rewrite status");
    assert!(auto_reply_legacy_conflict(root.path()).is_err());
}

#[cfg(unix)]
#[test]
fn auto_reply_check_does_not_probe_python_executable() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().expect("temporary validation root");
    let executable = root.path().join("python3");
    let marker = root.path().join("executed");
    fs::write(
        &executable,
        format!("#!/bin/sh\nprintf x > '{}'\nexit 1\n", marker.display()),
    )
    .expect("write fake interpreter");
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700))
        .expect("set executable permissions");
    let configured = executable.to_str().expect("executable path");
    let resolved =
        validate_auto_reply_executable(Some(configured), "test python", "python3", false)
            .expect("static validation should not execute the interpreter");
    assert_eq!(resolved, configured);
    assert!(!marker.exists());
}

#[cfg(unix)]
#[test]
fn python_interpreter_rejects_cellar_version_path_and_keeps_keg_string() {
    let cellar = Path::new(
            "/opt/homebrew/Cellar/python@3.11/3.11.15_4/Frameworks/Python.framework/Versions/3.11/bin/python3.11",
        );
    let error = validate_auto_reply_executable(
        Some(cellar.to_str().expect("cellar path")),
        "AutoReply python_interpreter",
        "python3",
        false,
    )
    .expect_err("Cellar version paths must be rejected");
    assert!(error
        .to_string()
        .contains("must not be a Homebrew Cellar version path"));
    assert!(is_homebrew_cellar_version_path(cellar));
    assert!(is_homebrew_opt_python_keg_path(Path::new(
        "/opt/homebrew/opt/python@3.11/bin/python3.11"
    )));
}

#[cfg(unix)]
#[test]
fn codex_auth_rejects_empty_credential_file() {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let root = tempfile::tempdir().expect("temporary Codex home");
    let empty = root.path().join("auth.json");
    fs::write(&empty, b"").expect("write empty auth");
    fs::set_permissions(&empty, fs::Permissions::from_mode(0o600)).expect("chmod auth");
    let uid = fs::symlink_metadata(&empty).expect("auth metadata").uid();
    assert!(validate_auto_reply_codex_auth(&empty, uid).is_err());
}

#[cfg(unix)]
#[test]
fn auto_reply_aggregate_tracks_live_per_room_readiness() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().expect("temporary aggregate root");
    let room_root = root.path().join("rooms/42");
    fs::create_dir_all(&room_root).expect("create room root");
    let status_path = room_root.join("supervisor-status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&serde_json::json!({
            "target_chat_id": 42,
            "target_chat_name": "부자멘토멘티",
            "state": "running",
            "readiness": "ready",
            "fence_reason": "",
            "updated_at": chrono::Utc::now().timestamp_millis() as f64 / 1_000.0,
        }))
        .unwrap(),
    )
    .unwrap();
    fs::set_permissions(&status_path, fs::Permissions::from_mode(0o600)).unwrap();
    let target = local_db::LocalChat {
        chat_id: 42,
        chat_type: 0,
        chat_name: "부자멘토멘티".to_string(),
        database_chat_name: Some("부자멘토멘티".to_string()),
        active_members_count: 4,
        last_log_id: 100,
        last_updated_at: 0,
        unread_count: 0,
        display_name: "부자멘토멘티".to_string(),
    };
    let mut child = Command::new("/bin/sh")
        .args(["-c", "sleep 30"])
        .spawn()
        .expect("spawn aggregate fixture child");
    write_auto_reply_aggregate(
        root.path(),
        std::slice::from_ref(&target),
        std::slice::from_ref(&child),
        "running",
    )
    .expect("write live aggregate");
    let aggregate: serde_json::Value =
        serde_json::from_slice(&fs::read(root.path().join("aggregate-status.json")).unwrap())
            .unwrap();
    assert_eq!(aggregate["schema_version"], 2);
    assert_eq!(aggregate["readiness"], "ready");
    assert_eq!(aggregate["authoritative"], true);
    assert_eq!(aggregate["room_count"], 1);
    assert_eq!(aggregate["ready_room_count"], 1);
    assert_eq!(aggregate["targets"][0]["ready"], true);
    assert_eq!(
        fs::metadata(root.path().join("aggregate-status.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    child.kill().ok();
    child.wait().ok();
}

#[cfg(unix)]
#[test]
fn auto_reply_children_guard_reaps_partial_startup_processes() {
    let mut guard = AutoReplyChildrenGuard::new(1);
    let mut command = Command::new("/bin/sh");
    command.args(["-c", "sleep 30"]);
    configure_auto_reply_process_group(&mut command);
    let child = command.spawn().expect("spawn fake supervisor");
    let pid = child.id() as libc::pid_t;
    guard.children.push(child);
    drop(guard);
    assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
}

#[cfg(unix)]
#[test]
fn auto_reply_children_guard_kills_descendants_after_group_leader_exits() {
    let mut guard = AutoReplyChildrenGuard::new(1);
    let mut command = Command::new("/bin/sh");
    command.args([
        "-c",
        "trap 'exit 0' TERM; (trap '' TERM HUP; exec sleep 30) & wait",
    ]);
    configure_auto_reply_process_group(&mut command);
    let child = command.spawn().expect("spawn fake process group");
    let process_group = child.id() as libc::pid_t;
    guard.children.push(child);
    drop(guard);
    assert_eq!(unsafe { libc::kill(-process_group, 0) }, -1);
}

#[cfg(unix)]
#[test]
fn auto_reply_guardian_liveness_accepts_only_read_pipe_and_sets_cloexec() {
    let mut descriptors = [-1; 2];
    assert_eq!(unsafe { libc::pipe(descriptors.as_mut_ptr()) }, 0);
    let read_fd = descriptors[0];
    let write_fd = descriptors[1];

    let read_pipe = validate_auto_reply_guardian_liveness_fd(read_fd)
        .expect("read-only pipe should be accepted");
    let descriptor_flags = unsafe { libc::fcntl(read_pipe.as_raw_fd(), libc::F_GETFD) };
    assert_ne!(descriptor_flags & libc::FD_CLOEXEC, 0);
    assert!(validate_auto_reply_guardian_liveness_fd(write_fd).is_err());

    assert_eq!(unsafe { libc::close(write_fd) }, 0);
    drop(read_pipe);
}

#[cfg(unix)]
#[test]
fn guardian_eof_stops_auto_reply_root_and_descendant_group() {
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _environment_guard = ENV_LOCK.lock().expect("lock liveness environment");
    std::env::remove_var(AUTO_REPLY_GUARDIAN_LIVENESS_ENV);
    AUTO_REPLY_STOP.store(false, Ordering::Release);
    AUTO_REPLY_GUARDIAN_LOST.store(false, Ordering::Release);

    let mut descriptors = [-1; 2];
    assert_eq!(unsafe { libc::pipe(descriptors.as_mut_ptr()) }, 0);
    let read_fd = descriptors[0];
    let write_fd = descriptors[1];
    let write_flags = unsafe { libc::fcntl(write_fd, libc::F_GETFD) };
    assert!(write_flags >= 0);
    assert_eq!(
        unsafe { libc::fcntl(write_fd, libc::F_SETFD, write_flags | libc::FD_CLOEXEC) },
        0
    );
    std::env::set_var(AUTO_REPLY_GUARDIAN_LIVENESS_ENV, read_fd.to_string());
    let monitor = start_auto_reply_guardian_liveness_monitor(false)
        .expect("start liveness monitor")
        .expect("guarded mode should start a monitor");
    assert!(std::env::var_os(AUTO_REPLY_GUARDIAN_LIVENESS_ENV).is_none());

    let mut guard = AutoReplyChildrenGuard::new(1);
    let mut command = Command::new("/bin/sh");
    command.args([
        "-c",
        "trap 'exit 0' TERM; (trap '' TERM HUP; exec sleep 30) & wait",
    ]);
    configure_auto_reply_process_group(&mut command);
    let child = command.spawn().expect("spawn fake worker group");
    let process_group = child.id() as libc::pid_t;
    guard.children.push(child);

    assert_eq!(unsafe { libc::close(write_fd) }, 0);
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    while !AUTO_REPLY_STOP.load(Ordering::Acquire) && std::time::Instant::now() < deadline {
        thread::sleep(Duration::from_millis(10));
    }
    assert!(AUTO_REPLY_GUARDIAN_LOST.load(Ordering::Acquire));
    assert!(AUTO_REPLY_STOP.load(Ordering::Acquire));
    guard.stop();
    monitor.join().expect("liveness monitor should exit");
    assert_eq!(unsafe { libc::kill(-process_group, 0) }, -1);

    AUTO_REPLY_STOP.store(false, Ordering::Release);
    AUTO_REPLY_GUARDIAN_LOST.store(false, Ordering::Release);
}

#[test]
fn local_send_preflight_requires_auto_reply_worker_identity() {
    assert!(require_auto_reply_worker_preflight(true, false).is_err());
    require_auto_reply_worker_preflight(true, true)
        .expect("the AutoReply worker may run the read-only preflight");
    require_auto_reply_worker_preflight(false, false)
        .expect("ordinary sends are governed by their existing gates");
}

#[test]
fn worker_actual_send_setup_failure_finishes_before_ax_call() {
    let setup = finish_worker_bound_local_send_setup::<()>(
        Err(anyhow::anyhow!("readiness unavailable")),
        true,
        false,
        "target",
        true,
    )
    .expect("a worker setup failure has a structured no-mutation outcome");
    let mut ax_called = false;
    if setup.is_some() {
        ax_called = true;
    }
    assert!(setup.is_none());
    assert!(!ax_called);

    assert!(finish_worker_bound_local_send_setup::<()>(
        Err(anyhow::anyhow!("preflight readiness unavailable")),
        true,
        true,
        "target",
        true,
    )
    .is_err());
}

#[cfg(unix)]
#[test]
fn worker_setup_lock_contention_is_immediate_and_never_reaches_ax() {
    let root = tempfile::tempdir().expect("temporary worker lock root");
    let path = root.path().join("worker.lock");
    let open_lock = || {
        fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&path)
            .expect("open worker lock")
    };
    let holder = open_lock();
    acquire_worker_setup_lock_nonblocking(&holder, "test holder")
        .expect("acquire initial worker lock");
    let actual_contender = open_lock();
    let preflight_contender = open_lock();

    // Release after a short bound so a regression to blocking flock fails
    // as an acquired lock instead of hanging the test process forever.
    let releaser = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(250));
        assert_eq!(unsafe { libc::flock(holder.as_raw_fd(), libc::LOCK_UN) }, 0);
    });
    let actual_error =
        acquire_worker_setup_lock_nonblocking(&actual_contender, "contended AX send lock")
            .expect_err("worker lock contention must fail without waiting");
    let preflight_error = acquire_worker_setup_lock_nonblocking(
        &preflight_contender,
        "contended owner-generation lock",
    )
    .expect_err("preflight lock contention must fail without waiting");
    releaser.join().expect("release worker lock");

    let setup =
        finish_worker_bound_local_send_setup::<()>(Err(actual_error), true, false, "target", true)
            .expect("actual worker contention has a structured no-mutation outcome");
    let ax_called = setup.is_some();
    assert!(setup.is_none());
    assert!(!ax_called);
    assert!(finish_worker_bound_local_send_setup::<()>(
        Err(preflight_error),
        true,
        true,
        "target",
        true,
    )
    .is_err());
}

fn bound_source_message(log_id: i64, author_id: i64, sender_name: &str) -> local_db::LocalMessage {
    local_db::LocalMessage {
        log_id,
        chat_id: 417_780_809_780_519,
        author_id,
        is_self: false,
        sender_name: sender_name.to_string(),
        message: "body".to_string(),
        attachment: String::new(),
        message_type: 1,
        sent_at: 1_700_000_000,
    }
}

#[test]
fn bound_source_message_prefers_the_page_that_was_already_read() {
    let windowed = vec![bound_source_message(7, 4242, "권준혁")];
    let mut lookups = 0;
    let resolved = resolve_bound_source_message(&windowed, 7, |_| {
        lookups += 1;
        Ok(None)
    })
    .expect("a row inside the page resolves without a second read");
    assert_eq!(resolved.log_id, 7);
    assert_eq!(resolved.sender_name, "권준혁");
    assert_eq!(lookups, 0, "the window hit must not re-query the DB");
}

#[test]
fn bound_source_message_falls_back_to_a_log_id_lookup() {
    // The newest page holds only later rows, which is the normal shape of a
    // scheduled reply in an active room.
    let windowed = vec![bound_source_message(9, 1, "다른 사람")];
    let resolved = resolve_bound_source_message(&windowed, 7, |log_id| {
        assert_eq!(log_id, 7);
        Ok(Some(bound_source_message(7, 4242, "권준혁")))
    })
    .expect("the direct lookup resolves an older source row");
    assert_eq!(resolved.log_id, 7);
    assert_eq!(resolved.author_id, 4242);
}

#[test]
fn bound_source_message_fails_closed_when_the_row_is_gone() {
    let windowed = vec![bound_source_message(9, 1, "다른 사람")];
    let error = resolve_bound_source_message(&windowed, 7, |_| Ok(None))
        .expect_err("a missing source row must not become a send");
    assert!(error
        .to_string()
        .contains("scheduled reply source row is unavailable"));
}

#[test]
fn bound_source_message_reports_a_lookup_failure() {
    let windowed: Vec<local_db::LocalMessage> = Vec::new();
    let error = resolve_bound_source_message(&windowed, 7, |_| anyhow::bail!("database gone"))
        .expect_err("a read failure must surface instead of a silent miss");
    // The context wraps the read error, so the cause is on the chain
    // rather than in the outermost message.
    let chain = format!("{error:#}");
    assert!(chain.contains("database gone"), "{chain}");
    assert!(
        chain.contains("read the scheduled reply source row"),
        "{chain}"
    );
}
