//! The private AutoReply state root: no-follow directory walks, the owner lock,
//! atomic enrollment writes, the aggregate status file, and bounded reads.

use std::fs;
use std::io::Write;
#[cfg(unix)]
use std::os::unix::io::AsRawFd;
#[cfg(unix)]
use std::os::unix::io::FromRawFd;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};

use anyhow::{Context, Result};

use crate::{config, local_db};
use openkakao_cli::auto_reply_service;

pub(super) fn auto_reply_state_root(
    config: &config::OpenKakaoConfig,
) -> Result<std::path::PathBuf> {
    let root = config
        .auto_reply
        .state_root
        .as_deref()
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            dirs::home_dir()
                .map(|home| auto_reply_service::default_state_root(&home))
                .unwrap_or_else(|| std::path::PathBuf::from("auto-reply"))
        });
    if !root.is_absolute() {
        anyhow::bail!("AutoReply state_root must be an absolute path");
    }
    Ok(root)
}

pub(super) fn auto_reply_legacy_conflict(root: &Path) -> Result<()> {
    let status_path = root.join("supervisor-status.json");
    let status_metadata = match fs::symlink_metadata(&status_path) {
        Ok(metadata) => Some(metadata),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => {
            return Err(error).with_context(|| format!("inspect {}", status_path.display()));
        }
    };
    if status_metadata.is_none() {
        for residual in [
            "db-watch-state.json",
            "reply-state.json",
            "reply-queue.sqlite3",
            "reply-queue.sqlite3-wal",
            "reply-queue.sqlite3-shm",
        ] {
            let path = root.join(residual);
            if fs::symlink_metadata(&path).is_ok() {
                anyhow::bail!(
                    "legacy AutoReply residual state exists without a drained supervisor status: {}",
                    path.display()
                );
            }
        }
        return Ok(());
    }
    if status_metadata.as_ref().is_some_and(|metadata| {
        metadata.file_type().is_symlink() || !metadata.file_type().is_file()
    }) {
        anyhow::bail!("existing AutoReply supervisor status is not a regular file");
    }
    let value = read_bounded_json_file(&status_path)?;
    let active_state = value.get("state").and_then(serde_json::Value::as_str);
    if matches!(
        active_state,
        Some("starting" | "running" | "stopping" | "fenced")
    ) {
        anyhow::bail!(
            "existing AutoReply supervisor is running at {}; stop/drain it before CLI activation",
            status_path.display()
        );
    }
    if active_state == Some("stopped")
        && value.get("legacy_drained") != Some(&serde_json::Value::Bool(true))
    {
        anyhow::bail!(
            "existing AutoReply supervisor at {} is stopped but not explicitly drained; resolve terminal queue state before CLI activation",
            status_path.display()
        );
    }
    if active_state != Some("stopped") {
        anyhow::bail!(
            "existing AutoReply supervisor status at {} is not a recognized drained state",
            status_path.display()
        );
    }
    Ok(())
}

#[cfg(unix)]
fn walk_auto_reply_directory_no_follow(path: &Path, create: bool) -> Result<std::fs::File> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    use std::path::Component;

    if !path.is_absolute() {
        anyhow::bail!("AutoReply directory must be an absolute path");
    }
    let mut options = fs::OpenOptions::new();
    options
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_DIRECTORY);
    let mut directory = options.open("/").context("open filesystem root")?;
    let mut saw_component = false;
    for component in path.components() {
        let name = match component {
            Component::RootDir => continue,
            Component::Normal(name) => name,
            Component::CurDir | Component::ParentDir | Component::Prefix(_) => {
                anyhow::bail!("AutoReply directory path is not normalized")
            }
        };
        saw_component = true;
        let name = CString::new(name.as_bytes()).context("AutoReply directory contains NUL")?;
        let open_component = || unsafe {
            libc::openat(
                directory.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_DIRECTORY,
            )
        };
        let mut descriptor = open_component();
        let mut created = false;
        if descriptor < 0 {
            let error = std::io::Error::last_os_error();
            if !create || error.raw_os_error() != Some(libc::ENOENT) {
                return Err(error).with_context(|| {
                    format!(
                        "open AutoReply directory component {}",
                        name.to_string_lossy()
                    )
                });
            }
            if unsafe { libc::mkdirat(directory.as_raw_fd(), name.as_ptr(), 0o700) } != 0 {
                let mkdir_error = std::io::Error::last_os_error();
                if mkdir_error.raw_os_error() != Some(libc::EEXIST) {
                    return Err(mkdir_error).with_context(|| {
                        format!(
                            "create AutoReply directory component {}",
                            name.to_string_lossy()
                        )
                    });
                }
            } else {
                created = true;
            }
            descriptor = open_component();
            if descriptor < 0 {
                return Err(std::io::Error::last_os_error()).with_context(|| {
                    format!(
                        "open created AutoReply directory component {}",
                        name.to_string_lossy()
                    )
                });
            }
        }
        // SAFETY: openat returned a newly owned descriptor on success.
        let next = unsafe { std::fs::File::from_raw_fd(descriptor) };
        let metadata = next.metadata()?;
        if !metadata.file_type().is_dir() || metadata.nlink() == 0 {
            anyhow::bail!("AutoReply directory component is unsafe");
        }
        if created {
            let uid = unsafe { libc::geteuid() };
            if metadata.uid() != uid || unsafe { libc::fchmod(next.as_raw_fd(), 0o700) } != 0 {
                anyhow::bail!("created AutoReply directory component is unsafe");
            }
            let private = next.metadata()?;
            if private.uid() != uid || private.mode() & 0o777 != 0o700 {
                anyhow::bail!("created AutoReply directory component is not private");
            }
        }
        directory = next;
    }
    if !saw_component {
        anyhow::bail!("AutoReply state root cannot be the filesystem root");
    }
    Ok(directory)
}

#[cfg(unix)]
fn open_private_auto_reply_directory(
    path: &Path,
    label: &str,
    create: bool,
) -> Result<std::fs::File> {
    use std::os::unix::fs::MetadataExt;

    if !path.is_absolute() {
        anyhow::bail!("AutoReply {label} must be an absolute path");
    }
    let directory = walk_auto_reply_directory_no_follow(path, create)
        .with_context(|| format!("open AutoReply {label} {}", path.display()))?;
    let uid = unsafe { libc::geteuid() };
    let initial = directory
        .metadata()
        .with_context(|| format!("inspect open AutoReply {label} {}", path.display()))?;
    if !initial.file_type().is_dir() || initial.uid() != uid || initial.nlink() == 0 {
        anyhow::bail!("AutoReply {label} is unsafe: {}", path.display());
    }
    if unsafe { libc::fchmod(directory.as_raw_fd(), 0o700) } != 0 {
        return Err(std::io::Error::last_os_error())
            .with_context(|| format!("privatize AutoReply {label} {}", path.display()));
    }
    let private = directory.metadata()?;
    let current_directory = walk_auto_reply_directory_no_follow(path, false)
        .with_context(|| format!("revalidate AutoReply {label} {}", path.display()))?;
    let current = current_directory.metadata()?;
    if !private.file_type().is_dir()
        || private.uid() != uid
        || private.nlink() == 0
        || private.mode() & 0o777 != 0o700
        || !current.file_type().is_dir()
        || current.uid() != uid
        || current.nlink() == 0
        || current.mode() & 0o777 != 0o700
        || (private.dev(), private.ino()) != (current.dev(), current.ino())
    {
        anyhow::bail!("AutoReply {label} is not private: {}", path.display());
    }
    Ok(directory)
}

#[cfg(not(unix))]
fn ensure_private_auto_reply_directory(path: &Path, label: &str, create: bool) -> Result<()> {
    if !path.is_absolute() {
        anyhow::bail!("AutoReply {label} must be an absolute path");
    }
    if create {
        fs::create_dir_all(path)
            .with_context(|| format!("create AutoReply {label} {}", path.display()))?;
    }
    let metadata = fs::symlink_metadata(path)
        .with_context(|| format!("inspect AutoReply {label} {}", path.display()))?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_dir() {
        anyhow::bail!(
            "AutoReply {label} must be a real directory: {}",
            path.display()
        );
    }
    Ok(())
}

pub(super) fn acquire_auto_reply_owner_lock(root: &Path) -> Result<std::fs::File> {
    #[cfg(unix)]
    let root_directory = open_private_auto_reply_directory(root, "state root", true)?;
    #[cfg(not(unix))]
    ensure_private_auto_reply_directory(root, "state root", true)?;
    let path = root.join("supervisor.owner.lock");

    #[cfg(unix)]
    let file = {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;

        let name = CString::new(
            path.file_name()
                .context("AutoReply owner lock name missing")?
                .as_bytes(),
        )
        .context("AutoReply owner lock name contains NUL")?;
        let descriptor = unsafe {
            libc::openat(
                root_directory.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDWR | libc::O_CREAT | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                0o600,
            )
        };
        if descriptor < 0 {
            return Err(std::io::Error::last_os_error())
                .with_context(|| format!("open AutoReply owner lock {}", path.display()));
        }
        // SAFETY: openat returned a newly owned descriptor on success.
        unsafe { std::fs::File::from_raw_fd(descriptor) }
    };

    #[cfg(not(unix))]
    let file = {
        let mut options = fs::OpenOptions::new();
        options.create(true).truncate(false).read(true).write(true);
        options
            .open(&path)
            .with_context(|| format!("open AutoReply owner lock {}", path.display()))?
    };
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = file
            .metadata()
            .with_context(|| format!("inspect AutoReply owner lock {}", path.display()))?;
        let uid = unsafe { libc::geteuid() };
        if !metadata.file_type().is_file() || metadata.uid() != uid || metadata.nlink() != 1 {
            anyhow::bail!("AutoReply owner lock is unsafe: {}", path.display());
        }
        if unsafe { libc::fchmod(file.as_raw_fd(), 0o600) } != 0 {
            return Err(std::io::Error::last_os_error())
                .with_context(|| format!("privatize AutoReply owner lock {}", path.display()));
        }
        let metadata = file.metadata()?;
        if metadata.mode() & 0o777 != 0o600 {
            anyhow::bail!("AutoReply owner lock is not private: {}", path.display());
        }
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            anyhow::bail!(
                "AutoReply owner lock is held by another supervisor: {}",
                path.display()
            );
        }
    }
    Ok(file)
}

#[cfg(unix)]
fn validate_private_auto_reply_file_at(
    directory: &std::fs::File,
    name: &std::ffi::CStr,
    expected_identity: Option<(u64, u64)>,
) -> Result<std::fs::File> {
    use std::os::unix::fs::MetadataExt;

    let descriptor = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK,
        )
    };
    if descriptor < 0 {
        return Err(std::io::Error::last_os_error()).context("open private AutoReply file");
    }
    // SAFETY: openat returned a newly owned descriptor on success.
    let file = unsafe { std::fs::File::from_raw_fd(descriptor) };
    let metadata = file.metadata()?;
    let uid = unsafe { libc::geteuid() };
    let entry_descriptor = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK,
        )
    };
    if entry_descriptor < 0 {
        return Err(std::io::Error::last_os_error())
            .context("inspect private AutoReply file entry");
    }
    // SAFETY: openat returned a newly owned descriptor on success.
    let entry = unsafe { std::fs::File::from_raw_fd(entry_descriptor) };
    let entry_metadata = entry.metadata()?;
    if !metadata.file_type().is_file()
        || metadata.uid() != uid
        || metadata.nlink() != 1
        || metadata.mode() & 0o777 != 0o600
        || !entry_metadata.file_type().is_file()
        || entry_metadata.uid() != uid
        || entry_metadata.nlink() != 1
        || entry_metadata.mode() & 0o777 != 0o600
        || (metadata.dev(), metadata.ino()) != (entry_metadata.dev(), entry_metadata.ino())
        || expected_identity.is_some_and(|identity| identity != (metadata.dev(), metadata.ino()))
    {
        anyhow::bail!("unsafe private AutoReply file");
    }
    Ok(file)
}

#[cfg(unix)]
pub(super) fn write_private_auto_reply_enrollment(root: &Path, bytes: &[u8]) -> Result<()> {
    use rand::RngCore;
    use std::ffi::CString;
    use std::os::unix::fs::MetadataExt;

    let directory = open_private_auto_reply_directory(root, "state root", false)?;
    let final_name = CString::new("enrollment.json").expect("static enrollment name");

    // Refuse rather than silently normalize an existing unsafe enrollment
    // authority. Replacing a symlink or hard link would obscure evidence that
    // another pathname had become part of the foreground safety boundary.
    let existing_descriptor = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            final_name.as_ptr(),
            libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK,
        )
    };
    if existing_descriptor >= 0 {
        // SAFETY: openat returned a newly owned descriptor on success.
        let existing = unsafe { std::fs::File::from_raw_fd(existing_descriptor) };
        drop(existing);
        validate_private_auto_reply_file_at(&directory, &final_name, None)
            .context("existing AutoReply enrollment authority is unsafe")?;
    } else {
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ENOENT) {
            return Err(error).context("inspect existing AutoReply enrollment authority");
        }
    }

    let mut random = [0_u8; 16];
    rand::thread_rng().fill_bytes(&mut random);
    let temporary_name = CString::new(format!(
        ".enrollment.{}.{}.tmp",
        std::process::id(),
        hex::encode(random)
    ))?;
    let descriptor = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            temporary_name.as_ptr(),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            0o600,
        )
    };
    if descriptor < 0 {
        return Err(std::io::Error::last_os_error())
            .context("create private AutoReply enrollment temporary file");
    }
    // SAFETY: openat returned a newly owned descriptor on success.
    let mut output = unsafe { std::fs::File::from_raw_fd(descriptor) };
    let result = (|| -> Result<()> {
        if unsafe { libc::fchmod(output.as_raw_fd(), 0o600) } != 0 {
            return Err(std::io::Error::last_os_error())
                .context("privatize AutoReply enrollment temporary file");
        }
        let metadata = output.metadata()?;
        let uid = unsafe { libc::geteuid() };
        if !metadata.file_type().is_file()
            || metadata.uid() != uid
            || metadata.nlink() != 1
            || metadata.mode() & 0o777 != 0o600
        {
            anyhow::bail!("AutoReply enrollment temporary file is unsafe");
        }
        let identity = (metadata.dev(), metadata.ino());
        output.write_all(bytes)?;
        output.sync_all()?;
        validate_private_auto_reply_file_at(&directory, &temporary_name, Some(identity))?;
        if unsafe {
            libc::renameat(
                directory.as_raw_fd(),
                temporary_name.as_ptr(),
                directory.as_raw_fd(),
                final_name.as_ptr(),
            )
        } != 0
        {
            return Err(std::io::Error::last_os_error())
                .context("install private AutoReply enrollment authority");
        }
        validate_private_auto_reply_file_at(&directory, &final_name, Some(identity))?;
        directory.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        unsafe {
            libc::unlinkat(directory.as_raw_fd(), temporary_name.as_ptr(), 0);
        }
    }
    result
}

#[cfg(not(unix))]
pub(super) fn write_private_auto_reply_enrollment(root: &Path, bytes: &[u8]) -> Result<()> {
    ensure_private_auto_reply_directory(root, "state root", false)?;
    let path = root.join("enrollment.json");
    let tmp = root.join(format!(".enrollment.{}.tmp", std::process::id()));
    fs::write(&tmp, bytes)?;
    fs::rename(tmp, path)?;
    Ok(())
}

pub(super) fn write_auto_reply_aggregate(
    root: &Path,
    targets: &[local_db::LocalChat],
    children: &[Child],
    state: &str,
) -> Result<()> {
    let now = chrono::Utc::now();
    let now_unix = now.timestamp_millis() as f64 / 1_000.0;
    let summaries = targets
        .iter()
        .zip(children.iter())
        .map(|(target, child)| {
            let room_root = root.join("rooms").join(target.chat_id.to_string());
            let status_path = room_root.join("supervisor-status.json");
            let status = read_bounded_json_file(&status_path).ok();
            let status_target_matches = status.as_ref().is_some_and(|value| {
                value.get("target_chat_id") == Some(&serde_json::json!(target.chat_id))
                    && value.get("target_chat_name")
                        == Some(&serde_json::json!(target.chat_name))
            });
            let heartbeat_age_seconds = status
                .as_ref()
                .and_then(|value| value.get("updated_at"))
                .and_then(serde_json::Value::as_f64)
                .filter(|stamp| stamp.is_finite())
                .map(|stamp| now_unix - stamp);
            let ready = state == "running"
                && status_target_matches
                && status.as_ref().is_some_and(|value| {
                    value.get("state") == Some(&serde_json::json!("running"))
                        && value.get("readiness") == Some(&serde_json::json!("ready"))
                        && value
                            .get("fence_reason")
                            .is_none_or(|reason| reason.as_str().is_some_and(str::is_empty))
                })
                && heartbeat_age_seconds
                    .is_some_and(|age| (-5.0..=15.0).contains(&age));
            serde_json::json!({
                "chat_id": target.chat_id,
                "chat_name": target.chat_name,
                "pid": child.id(),
                "room_state_root": room_root,
                "ready": ready,
                "state": status.as_ref().and_then(|value| value.get("state")).cloned(),
                "readiness": status.as_ref().and_then(|value| value.get("readiness")).cloned(),
                "fence_reason": status.as_ref().and_then(|value| value.get("fence_reason")).cloned(),
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "status_available": status.is_some(),
                "target_identity_matches": status_target_matches,
            })
        })
        .collect::<Vec<_>>();
    let ready_count = summaries
        .iter()
        .filter(|summary| summary.get("ready") == Some(&serde_json::Value::Bool(true)))
        .count();
    let all_ready = state == "running" && !summaries.is_empty() && ready_count == summaries.len();
    let payload = serde_json::json!({
        "schema_version": 2,
        "state": state,
        "readiness": if all_ready { "ready" } else if state == "running" { "starting" } else { "fenced" },
        "authoritative": all_ready,
        "updated_at": now.to_rfc3339(),
        "updated_at_unix": now_unix,
        "room_count": summaries.len(),
        "ready_room_count": ready_count,
        "targets": summaries,
    });
    let path = root.join("aggregate-status.json");
    let tmp = root.join(format!(".aggregate-status.{}.tmp", std::process::id()));
    let mut options = fs::OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
    }
    let mut output = options
        .open(&tmp)
        .with_context(|| format!("open aggregate status temporary file {}", tmp.display()))?;
    output.write_all(&serde_json::to_vec(&payload)?)?;
    output.write_all(b"\n")?;
    output.sync_all()?;
    drop(output);
    fs::rename(tmp, path)?;
    fs::File::open(root)?.sync_all()?;
    Ok(())
}

pub(super) fn prepare_auto_reply_room_state(
    root: &Path,
    chat: &local_db::LocalChat,
) -> Result<PathBuf> {
    #[cfg(unix)]
    let _root_directory = open_private_auto_reply_directory(root, "state root", false)?;
    #[cfg(not(unix))]
    ensure_private_auto_reply_directory(root, "state root", false)?;
    let rooms_root = root.join("rooms");
    #[cfg(unix)]
    let _rooms_directory = open_private_auto_reply_directory(&rooms_root, "rooms directory", true)?;
    #[cfg(not(unix))]
    ensure_private_auto_reply_directory(&rooms_root, "rooms directory", true)?;
    let room_root = root.join("rooms").join(chat.chat_id.to_string());
    #[cfg(unix)]
    let _room_directory = open_private_auto_reply_directory(&room_root, "room state", true)?;
    #[cfg(not(unix))]
    ensure_private_auto_reply_directory(&room_root, "room state", true)?;
    Ok(room_root)
}

pub(super) fn configure_auto_reply_supervisor_shared_state(command: &mut Command, root: &Path) {
    command
        .env("OPENKAKAO_AUTO_REPLY_SEND_LOCK", root.join(".ax-send.lock"))
        .env(
            "OPENKAKAO_MODEL_CIRCUIT_DB",
            root.join("model-circuit.sqlite3"),
        );
}

pub(super) const AUTO_REPLY_READINESS_MAX_BYTES: u64 = 64 * 1024;

pub(super) fn read_bounded_file(path: &Path) -> Result<Vec<u8>> {
    let metadata = fs::symlink_metadata(path)
        .with_context(|| format!("read readiness metadata: {}", path.display()))?;
    if !metadata.file_type().is_file() || metadata.len() > AUTO_REPLY_READINESS_MAX_BYTES {
        anyhow::bail!("invalid readiness file");
    }
    let raw = fs::read(path).with_context(|| format!("read readiness file: {}", path.display()))?;
    if raw.len() as u64 > AUTO_REPLY_READINESS_MAX_BYTES {
        anyhow::bail!("readiness file exceeds bound");
    }
    Ok(raw)
}

pub(super) fn read_bounded_json_file(path: &Path) -> Result<serde_json::Value> {
    let raw = read_bounded_file(path)?;
    serde_json::from_slice(&raw).context("malformed readiness JSON")
}
