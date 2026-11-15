//! Foreground `auto-reply`: resolve exact rooms, attest them, enroll them,
//! and supervise one Python supervisor per room until Ctrl-C.

mod bindings;
mod enrollment;
mod llm;
mod process;
mod queue;
mod runner;
mod state_root;
#[cfg(test)]
mod tests;
pub mod worker_gate;

use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::Ordering;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::{config, local_db, room_catalog};
use bindings::{
    auto_reply_attest_explicit_bindings, auto_reply_bind_author_ids,
    configure_auto_reply_supervisor_author_policy,
};
use enrollment::{enrollment_floor_for_target, write_auto_reply_enrollment};
use llm::{probe_auto_reply_llm, select_auto_reply_llm};
use process::{
    configure_auto_reply_process_group, install_auto_reply_signal_handlers,
    start_auto_reply_guardian_liveness_monitor, AutoReplyChildrenGuard,
    AUTO_REPLY_GUARDIAN_LIVENESS_ENV, AUTO_REPLY_GUARDIAN_LOST, AUTO_REPLY_STOP,
};
use runner::{
    resolve_auto_reply_supervisor, validate_auto_reply_executable, validate_auto_reply_runner,
    AUTO_REPLY_PYTHON_ISOLATION_ARGS,
};
use state_root::{
    acquire_auto_reply_owner_lock, auto_reply_legacy_conflict, auto_reply_state_root,
    configure_auto_reply_supervisor_shared_state, prepare_auto_reply_room_state,
    write_auto_reply_aggregate,
};

fn auto_reply_selector_values(
    config: &config::OpenKakaoConfig,
    cli_values: Vec<String>,
    chats: &[local_db::LocalChat],
    state_root: &Path,
    group_titles: &[(i64, String)],
) -> Result<Vec<String>> {
    let configured = config.auto_reply.chats.clone();
    let catalog_ids = room_catalog::catalog_auto_reply_chat_ids(state_root)?;
    let values = if cli_values.is_empty() {
        room_catalog::merge_configured_and_catalog_selectors_named(
            &configured,
            &catalog_ids,
            chats,
            group_titles,
        )?
    } else {
        // 명시한 방만 검사한다. 카탈로그의 다른 방까지 합치면 인증이 되지 않는
        // 방 하나가 모든 방의 검사를 실패시킨다.
        let expanded = expand_plain_chat_names_with_configured_bindings(cli_values, &configured);
        room_catalog::merge_configured_and_catalog_selectors_named(
            &expanded,
            &[],
            chats,
            group_titles,
        )?
    };
    if values.is_empty() {
        anyhow::bail!("no chat selected; pass --chat 부자멘토멘티, configure [auto_reply].chats, or enable a menubar room");
    }
    Ok(values)
}

fn expand_plain_chat_names_with_configured_bindings(
    cli_values: Vec<String>,
    configured: &[String],
) -> Vec<String> {
    let bindings = configured
        .iter()
        .filter_map(|value| {
            let selector = value.trim();
            let binding = selector.strip_prefix("bind:")?;
            let (_id, name) = binding.split_once(':')?;
            let name = name.trim();
            if name.is_empty() {
                None
            } else {
                Some((name.to_owned(), selector.to_owned()))
            }
        })
        .collect::<std::collections::BTreeMap<_, _>>();
    cli_values
        .into_iter()
        .map(|value| {
            let trimmed = value.trim();
            if trimmed.starts_with("id:")
                || trimmed.starts_with("name:")
                || trimmed.starts_with("bind:")
                || trimmed.bytes().all(|byte| byte.is_ascii_digit())
            {
                return value;
            }
            bindings.get(trimmed).cloned().unwrap_or(value)
        })
        .collect()
}

fn emit_auto_reply_preflight(
    json_output: bool,
    check: bool,
    targets: &[local_db::LocalChat],
    root: &Path,
    valid: bool,
    error: Option<&str>,
    workers_started: bool,
) {
    let payload = serde_json::json!({
        "command": "auto-reply",
        "valid": valid,
        "check": check,
        "network": false,
        "will_send": !check && workers_started,
        "workers_started": workers_started,
        "ax_runtime_readiness": if check { "not_evaluated" } else { "starting" },
        "state_root": root,
        "targets": targets.iter().map(|target| serde_json::json!({
            "chat_id": target.chat_id,
            "chat_name": target.chat_name,
            "last_log_id": target.last_log_id,
            "room_state_root": root.join("rooms").join(target.chat_id.to_string()),
        })).collect::<Vec<_>>(),
        "error": error,
    });
    if json_output {
        println!(
            "{}",
            serde_json::to_string(&payload).unwrap_or_else(|_| "{}".to_string())
        );
    } else {
        println!("auto-reply: {} target(s)", targets.len());
        for target in targets {
            println!("  {} ({})", target.chat_name, target.chat_id);
        }
        if let Some(error) = error {
            println!("  error: {error}");
        } else if check {
            println!("  static check passed; AX runtime readiness not evaluated");
        } else if workers_started {
            println!("  foreground workers started; press Ctrl-C to stop");
        }
    }
}

fn validate_auto_reply_context(
    reader: &local_db::LocalDbReader,
    targets: &[local_db::LocalChat],
) -> Result<()> {
    let db_path = openkakao_cli::context::default_db_path();
    for target in targets {
        let state = openkakao_cli::context::live_context_sync_state(
            &db_path,
            reader.account_fingerprint(),
            target.chat_id,
        )?
        .with_context(|| {
            format!(
                "authoritative live context is unavailable for {:?}",
                target.chat_name
            )
        })?;
        if !state.allows_auto_reply_startup(target.chat_id, &target.chat_name) {
            anyhow::bail!(
                "authoritative live context is not ready for {:?}",
                target.chat_name
            );
        }
        // 이름 없는 그룹방은 '그룹:<id>' 이름으로 프로파일이 쌓인다. 화면 이름으로
        // 조회가 비면 자리표시자 이름으로 한 번 더 시도한다.
        let mut profile_chat = target.chat_name.clone();
        let placeholder = format!("그룹:{}", target.chat_id);
        if openkakao_cli::context::style_profile(
            &db_path,
            &profile_chat,
            "최연우",
            Some(&state.source),
        )?
        .is_none()
            && openkakao_cli::context::style_profile(
                &db_path,
                &placeholder,
                "최연우",
                Some(&state.source),
            )?
            .is_some()
        {
            profile_chat = placeholder;
        }
        let profile = openkakao_cli::context::style_profile(
            &db_path,
            &profile_chat,
            "최연우",
            Some(&state.source),
        )?
        .with_context(|| format!("style profile is unavailable for {:?}", target.chat_name))?;
        if profile.source != state.source
            || profile.sample_count == 0
            || profile.policy_version != openkakao_cli::context::STYLE_POLICY_VERSION
        {
            anyhow::bail!("style profile is invalid for {:?}", target.chat_name);
        }
        let timing = openkakao_cli::context::response_time_stats(
            &db_path,
            &profile_chat,
            "최연우",
            Some(&state.source),
        )?
        .with_context(|| {
            format!(
                "response-time distribution is unavailable for {:?}",
                target.chat_name
            )
        })?;
        if timing.source != state.source
            || timing.sample_count == 0
            || !timing.average_seconds.is_finite()
            || !timing.median_seconds.is_finite()
            || !timing.p90_seconds.is_finite()
            || !timing.stddev_seconds.is_finite()
            || timing.average_seconds < 0.0
            || timing.median_seconds < 0.0
            || timing.p90_seconds < 0.0
            || timing.stddev_seconds < 0.0
        {
            anyhow::bail!(
                "response-time distribution is invalid for {:?}",
                target.chat_name
            );
        }
        if timing.sample_count >= 32
            && (timing.distribution.is_none()
                || (timing.stddev_seconds == 0.0
                    && timing.p90_seconds <= timing.average_seconds
                    && timing.median_seconds == timing.average_seconds))
        {
            anyhow::bail!(
                "response-time distribution is invalid for {:?}",
                target.chat_name
            );
        }
    }
    Ok(())
}

// Each parameter maps one-to-one onto a `Commands::AutoReply` CLI flag and the
// command has a single call site, so bundling them would only add a translation
// layer between clap and the run.
#[allow(
    clippy::too_many_arguments,
    reason = "AutoReply CLI flags map one-to-one onto the run parameters"
)]
pub fn run_auto_reply(
    config: &config::OpenKakaoConfig,
    selector_values: Vec<String>,
    check: bool,
    self_nickname_override: Option<String>,
    reply_author_overrides: Vec<String>,
    interval: f64,
    requested_model: Option<String>,
    json_output: bool,
) -> Result<()> {
    let mut effective_config = config.clone();
    let choice = select_auto_reply_llm(
        &mut effective_config,
        requested_model.as_deref(),
        json_output,
    )?;
    choice.apply(&mut effective_config);
    let reply_author_override_active = !reply_author_overrides.is_empty();
    if self_nickname_override.is_some() {
        effective_config.auto_reply.self_nickname = self_nickname_override;
    }
    if reply_author_override_active {
        effective_config.auto_reply.reply_authors = reply_author_overrides.clone();
    }
    let config = &effective_config;
    if !interval.is_finite() || !(0.2..=60.0).contains(&interval) {
        anyhow::bail!("auto-reply interval must be between 0.2 and 60 seconds");
    }
    AUTO_REPLY_STOP.store(false, Ordering::Release);
    AUTO_REPLY_GUARDIAN_LOST.store(false, Ordering::Release);
    let _guardian_liveness = start_auto_reply_guardian_liveness_monitor(check)?;
    let root = auto_reply_state_root(config)?;
    let (canonical_config_path, config_digest) = match config::verify_config_attestation(config) {
        Ok(attestation) => attestation,
        Err(error) => {
            emit_auto_reply_preflight(
                json_output,
                check,
                &[],
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
    };
    let reader = match local_db::LocalDbReader::open_no_mutation() {
        Ok(reader) => reader,
        Err(error) => {
            emit_auto_reply_preflight(
                json_output,
                check,
                &[],
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
    };
    let chats = match reader.list_all_chats() {
        Ok(chats) => chats,
        Err(error) => {
            emit_auto_reply_preflight(
                json_output,
                check,
                &[],
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
    };
    let group_titles = reader
        .list_group_chats(10_000)
        .unwrap_or_default()
        .into_iter()
        .map(|chat| (chat.chat_id, chat.title))
        .collect::<Vec<_>>();
    let selector_values =
        match auto_reply_selector_values(config, selector_values, &chats, &root, &group_titles) {
            Ok(values) => values,
            Err(error) => {
                emit_auto_reply_preflight(
                    json_output,
                    check,
                    &[],
                    &root,
                    false,
                    Some(&error.to_string()),
                    false,
                );
                return Err(error);
            }
        };
    let selectors = match local_db::parse_chat_selectors(&selector_values) {
        Ok(selectors) => selectors,
        Err(error) => {
            emit_auto_reply_preflight(
                json_output,
                check,
                &[],
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
    };
    let targets = match local_db::resolve_chat_selectors(&chats, &selectors) {
        Ok(targets) => targets,
        Err(error) => {
            emit_auto_reply_preflight(
                json_output,
                check,
                &[],
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
    };
    let group_titles = reader
        .list_group_chats(10_000)
        .unwrap_or_default()
        .into_iter()
        .map(|chat| (chat.chat_id, chat.title))
        .collect::<std::collections::BTreeMap<_, _>>();
    let mut targets = targets;
    for target in &mut targets {
        if target.chat_name.trim().is_empty() {
            if let Some(title) = group_titles.get(&target.chat_id) {
                target.chat_name = title.clone();
            } else if !target.display_name.trim().is_empty() {
                target.chat_name = target.display_name.clone();
            }
        }
    }
    let target_names = targets
        .iter()
        .map(|target| target.chat_name.clone())
        .collect::<Vec<_>>();
    let target_ids = targets
        .iter()
        .map(|target| target.chat_id)
        .collect::<Vec<_>>();
    if let Err(error) = config::validate_auto_reply_startup(config, &target_names, &target_ids) {
        emit_auto_reply_preflight(
            json_output,
            check,
            &targets,
            &root,
            false,
            Some(&error.to_string()),
            false,
        );
        return Err(error);
    }
    let target_ids = targets
        .iter()
        .map(|target| target.chat_id)
        .collect::<Vec<_>>();
    let mut reply_authors_by_room =
        match config::auto_reply_reply_authors_by_room(config, &target_ids) {
            Ok(values) => values,
            Err(error) => {
                emit_auto_reply_preflight(
                    json_output,
                    check,
                    &targets,
                    &root,
                    false,
                    Some(&error.to_string()),
                    false,
                );
                return Err(error);
            }
        };
    if reply_author_override_active {
        let override_authors =
            match config::validate_auto_reply_reply_author_override(&reply_author_overrides) {
                Ok(values) => values,
                Err(error) => {
                    emit_auto_reply_preflight(
                        json_output,
                        check,
                        &targets,
                        &root,
                        false,
                        Some(&error.to_string()),
                        false,
                    );
                    return Err(error);
                }
            };
        for authors in reply_authors_by_room.values_mut() {
            *authors = override_authors.clone();
        }
    }
    let binding_evidence = match auto_reply_attest_explicit_bindings(&reader, &selectors, &targets)
    {
        Ok(evidence) => evidence,
        Err(error) => {
            emit_auto_reply_preflight(
                json_output,
                check,
                &targets,
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
    };
    let author_bindings =
        match auto_reply_bind_author_ids(&reader, &targets, &reply_authors_by_room) {
            Ok(bindings) => bindings,
            Err(error) => {
                emit_auto_reply_preflight(
                    json_output,
                    check,
                    &targets,
                    &root,
                    false,
                    Some(&error.to_string()),
                    false,
                );
                return Err(error);
            }
        };
    if let Err(error) = validate_auto_reply_context(&reader, &targets) {
        emit_auto_reply_preflight(
            json_output,
            check,
            &targets,
            &root,
            false,
            Some(&error.to_string()),
            false,
        );
        return Err(error);
    }
    let binary = std::env::current_exe().context("resolve current openkakao binary")?;
    let (supervisor, runtime_root) = resolve_auto_reply_supervisor(&binary)?;
    let python = validate_auto_reply_executable(
        config.auto_reply.python_interpreter.as_deref(),
        "AutoReply python_interpreter",
        "python3",
        !check,
    )?;
    let runner = validate_auto_reply_runner(config)?;
    if check {
        if let Err(error) = auto_reply_legacy_conflict(&root) {
            emit_auto_reply_preflight(
                json_output,
                true,
                &targets,
                &root,
                false,
                Some(&error.to_string()),
                false,
            );
            return Err(error);
        }
        for target in &targets {
            let fresh_watermark = binding_evidence
                .iter()
                .find(|item| item.chat_id == target.chat_id)
                .map_or(target.last_log_id, |item| item.attested_db_last_log_id);
            if let Err(error) = enrollment_floor_for_target(&root, target, fresh_watermark) {
                emit_auto_reply_preflight(
                    json_output,
                    true,
                    &targets,
                    &root,
                    false,
                    Some(&error.to_string()),
                    false,
                );
                return Err(error);
            }
        }
        emit_auto_reply_preflight(json_output, true, &targets, &root, true, None, false);
        return Ok(());
    }

    if let Err(error) = probe_auto_reply_llm(config, choice) {
        emit_auto_reply_preflight(
            json_output,
            false,
            &targets,
            &root,
            false,
            Some(&error.to_string()),
            false,
        );
        return Err(error);
    }

    auto_reply_legacy_conflict(&root)?;
    let _owner_lock = acquire_auto_reply_owner_lock(&root)?;
    let self_nickname = config::auto_reply_self_nickname(config)
        .context("AutoReply self nickname is not configured")?;
    let (enrollment_floors, enrollment_digest) = write_auto_reply_enrollment(
        &root,
        &selector_values,
        &targets,
        &binding_evidence,
        &author_bindings,
        &runtime_root,
    )?;

    install_auto_reply_signal_handlers();
    let mut children = AutoReplyChildrenGuard::new(targets.len());
    for (target, enrollment_floor) in targets.iter().zip(enrollment_floors.iter()) {
        if AUTO_REPLY_STOP.load(Ordering::Relaxed) {
            anyhow::bail!("auto-reply activation interrupted before worker startup");
        }
        let room_root = prepare_auto_reply_room_state(&root, target)?;
        let target_author_bindings = serde_json::to_string(
            author_bindings
                .get(&target.chat_id)
                .context("AutoReply numeric reply-author enrollment is incomplete")?,
        )?;
        let target_reply_authors = author_bindings
            .get(&target.chat_id)
            .context("AutoReply numeric reply-author enrollment is incomplete")?
            .iter()
            .map(|binding| binding.nickname.clone())
            .collect::<Vec<_>>();
        let _ = reply_authors_by_room
            .get(&target.chat_id)
            .context("AutoReply per-room reply-author allowlist is incomplete")?;
        let mut command = Command::new(&python);
        command
            .current_dir(&runtime_root)
            .args(AUTO_REPLY_PYTHON_ISOLATION_ARGS)
            .arg(&supervisor)
            .arg("--interval")
            .arg(interval.to_string())
            .arg("--state-root")
            .arg(&room_root)
            .arg("--target-chat-id")
            .arg(target.chat_id.to_string())
            .arg("--target-chat-name")
            .arg(&target.chat_name)
            .arg("--self-nickname")
            .arg(&self_nickname)
            .env("OPENKAKAO_AUTO_REPLY_CLI", "1")
            .env("OPENKAKAO_CONFIG", &canonical_config_path)
            .env("OPENKAKAO_CONFIG_SHA256", &config_digest)
            .env("OPENKAKAO_ENROLLMENT_PATH", root.join("enrollment.json"))
            .env("OPENKAKAO_ENROLLMENT_SHA256", &enrollment_digest)
            .env("OPENKAKAO_TARGET_CHAT_ID", target.chat_id.to_string())
            .env("OPENKAKAO_TARGET_CHAT_NAME", &target.chat_name)
            .env("OPENKAKAO_INITIAL_CURSOR", enrollment_floor.to_string())
            .env(
                "OPENKAKAO_GEEKNEWS_ENABLED",
                if room_catalog::catalog_geeknews_chat_ids(&root)
                    .ok()
                    .is_some_and(|ids| ids.contains(&target.chat_id))
                {
                    "1"
                } else {
                    "0"
                },
            )
            .env("OPENKAKAO_SELF_NICKNAME", &self_nickname)
            .env(
                "OPENKAKAO_ALLOW_LINK_FETCH",
                if config.auto_reply.allow_link_fetch {
                    "1"
                } else {
                    "0"
                },
            )
            .env(
                "OPENKAKAO_ALLOW_IMAGE_ANALYSIS",
                if config.auto_reply.allow_image_analysis {
                    "1"
                } else {
                    "0"
                },
            )
            .env_remove(AUTO_REPLY_GUARDIAN_LIVENESS_ENV)
            .env("OPENKAKAO_BINARY", &binary)
            .env("OPENKAKAO_PYTHON", &python)
            .env("OPENKAKAO_AUTO_REPLY_RUNTIME_ROOT", &runtime_root)
            .stdout(if json_output {
                Stdio::null()
            } else {
                Stdio::inherit()
            })
            .stderr(if json_output {
                Stdio::null()
            } else {
                Stdio::inherit()
            });
        configure_auto_reply_supervisor_shared_state(&mut command, &root);
        configure_auto_reply_supervisor_author_policy(
            &mut command,
            &target_reply_authors,
            &target_author_bindings,
        );
        command
            .env("OPENKAKAO_REPLY_RUNNER", &runner.path)
            .env("OPENKAKAO_REPLY_RUNNER_KIND", &runner.kind)
            .env("OPENKAKAO_REPLY_RUNNER_SHA256", &runner.sha256)
            .env("OPENKAKAO_REPLY_MODEL", &runner.model)
            .env("OPENKAKAO_REPLY_REASONING_EFFORT", &runner.reasoning_effort)
            .env("OPENKAKAO_REPLY_SERVICE_TIER", &runner.service_tier);
        if let Some(codex_home) = &runner.codex_home {
            command.env("OPENKAKAO_REPLY_CODEX_HOME", codex_home);
        }
        configure_auto_reply_process_group(&mut command);
        match command.spawn() {
            Ok(child) => children.children.push(child),
            Err(error) => {
                anyhow::bail!("start AutoReply worker for {}: {error}", target.chat_name);
            }
        }
    }

    if AUTO_REPLY_STOP.load(Ordering::Relaxed) {
        anyhow::bail!("auto-reply activation interrupted during worker startup");
    }
    write_auto_reply_aggregate(&root, &targets, &children.children, "running")?;
    emit_auto_reply_preflight(json_output, false, &targets, &root, true, None, true);

    let mut failed = false;
    let mut next_aggregate_update = std::time::Instant::now();
    loop {
        if AUTO_REPLY_STOP.load(Ordering::Relaxed) {
            children.stop();
            break;
        }
        if std::time::Instant::now() >= next_aggregate_update {
            // Aggregate status is observational only; a transient dashboard
            // write must not stop otherwise healthy per-room safety workers.
            let _ = write_auto_reply_aggregate(&root, &targets, &children.children, "running");
            next_aggregate_update = std::time::Instant::now() + Duration::from_secs(1);
        }
        let mut child_exited = false;
        for child in &mut children.children {
            if child.try_wait()?.is_some() {
                // A room supervisor is persistent. Any exit before the parent
                // receives its own stop signal means that room lost coverage,
                // even when the child happened to return status 0.
                failed = true;
                child_exited = true;
            }
        }
        if child_exited {
            children.stop();
            break;
        }
        thread::sleep(Duration::from_millis(200));
    }
    children.stop();
    for child in &mut children.children {
        let status = child.wait()?;
        if !status.success() {
            failed = true;
        }
    }
    write_auto_reply_aggregate(&root, &targets, &children.children, "stopped")?;
    children.disarm();
    if AUTO_REPLY_GUARDIAN_LOST.load(Ordering::Acquire) {
        anyhow::bail!("session guardian liveness was lost; all AutoReply workers were stopped");
    }
    if failed {
        anyhow::bail!("one or more AutoReply workers exited unsuccessfully");
    }
    Ok(())
}
const MAX_INT64: i64 = i64::MAX;
