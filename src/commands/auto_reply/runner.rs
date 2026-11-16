//! Resolve and validate the Python supervisor and the configured reply-runner
//! executables before any child starts.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use crate::config;

pub(super) const AUTO_REPLY_PYTHON_ISOLATION_ARGS: [&str; 3] = ["-E", "-B", "-S"];

pub(super) fn resolve_auto_reply_supervisor(binary: &Path) -> Result<(PathBuf, PathBuf)> {
    let mut candidates = Vec::new();
    if let Some(runtime_root) =
        std::env::var_os("OPENKAKAO_AUTO_REPLY_RUNTIME_ROOT").filter(|value| !value.is_empty())
    {
        candidates.push(PathBuf::from(runtime_root).join("scripts"));
    }
    if let Some(parent) = binary.parent() {
        candidates.push(parent.join("scripts"));
        if let Some(prefix) = parent.parent() {
            candidates.push(prefix.join("libexec").join("scripts"));
        }
    }
    candidates.push(Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts"));

    for scripts_root in candidates {
        let scripts_metadata = match fs::symlink_metadata(&scripts_root) {
            Ok(metadata) => metadata,
            Err(_) => continue,
        };
        if !scripts_metadata.is_dir() || scripts_metadata.file_type().is_symlink() {
            continue;
        }
        let supervisor = scripts_root.join("auto-reply-supervisor.py");
        let runtime_files = [
            "auto-reply-supervisor.py",
            "auto-reply-db-watch.py",
            "auto-reply-worker.py",
            "auto-reply-apple-watch.py",
            "auto_reply_ax_ui.py",
            "auto_reply_metrics.py",
            "auto-reply-schema.json",
        ];
        let trusted = runtime_files.iter().all(|name| {
            let path = scripts_root.join(name);
            let Ok(metadata) = fs::symlink_metadata(path) else {
                return false;
            };
            if !metadata.is_file() || metadata.file_type().is_symlink() {
                return false;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if metadata.mode() & 0o022 != 0 {
                    return false;
                }
            }
            true
        });
        if trusted {
            let runtime_root = scripts_root
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")));
            return Ok((supervisor, runtime_root));
        }
    }
    anyhow::bail!(
        "AutoReply runtime assets are missing; expected auto-reply-supervisor.py beside the binary or in the packaged scripts directory"
    )
}

pub(super) fn validate_auto_reply_executable(
    value: Option<&str>,
    label: &str,
    default_name: &str,
    probe_python_version: bool,
) -> Result<String> {
    let value = value
        .context(format!("{label} must be configured explicitly"))?
        .trim();
    if value.is_empty() || value.chars().any(|ch| ch.is_control()) {
        anyhow::bail!("{label} must be a non-empty executable path");
    }
    let configured = Path::new(value);
    if !configured.is_absolute() {
        anyhow::bail!("{label} must be an absolute path");
    }
    if default_name == "python3" && is_homebrew_cellar_version_path(configured) {
        anyhow::bail!("{label} must not be a Homebrew Cellar version path");
    }
    let configured_metadata = fs::symlink_metadata(configured).with_context(|| {
        if default_name == "python3" {
            format!("python_interpreter_missing: {label} is unavailable")
        } else {
            format!("{label} does not resolve to a readable executable")
        }
    })?;
    if configured.is_symlink() {
        if default_name != "python3" && default_name != "opencodex" {
            anyhow::bail!("{label} must not be a symlink");
        }
        if default_name == "python3" && !is_homebrew_opt_python_keg_path(configured) {
            anyhow::bail!("{label} symlink must be a Homebrew opt python keg path");
        }
    } else if !configured_metadata.is_file() {
        anyhow::bail!("{label} is not a regular file");
    }
    let target = fs::canonicalize(configured).with_context(|| {
        if default_name == "python3" {
            format!("python_interpreter_missing: {label} target is unavailable")
        } else {
            format!("{label} does not resolve to a readable executable")
        }
    })?;
    let metadata = fs::symlink_metadata(&target)?;
    if !metadata.is_file() {
        anyhow::bail!("{label} is not a regular file");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o022 != 0 || metadata.mode() & 0o111 == 0 {
            anyhow::bail!("{label} has unsafe ownership or executable permissions");
        }
    }
    if default_name == "python3" && probe_python_version {
        let output = Command::new(&target)
            .args([
                "-I",
                "-B",
                "-S",
                "-c",
                "import sys; print(f'{sys.implementation.name} {sys.version_info.major}.{sys.version_info.minor}')",
            ])
            .output()
            .with_context(|| format!("probe {label} version"))?;
        let version = String::from_utf8_lossy(&output.stdout);
        let trusted_version = ["cpython 3.11", "cpython 3.12", "cpython 3.13"]
            .iter()
            .any(|prefix| version.trim() == *prefix);
        if !output.status.success() || !trusted_version || !output.stderr.is_empty() {
            anyhow::bail!("{label} must be CPython 3.11, 3.12, or 3.13");
        }
    }
    if default_name == "python3" {
        Ok(configured.to_string_lossy().into_owned())
    } else {
        Ok(target.to_string_lossy().into_owned())
    }
}

pub(super) fn is_homebrew_opt_python_keg_path(path: &Path) -> bool {
    path.to_str().is_some_and(|value| {
        matches!(
            value,
            "/opt/homebrew/opt/python@3.11/bin/python3.11"
                | "/opt/homebrew/opt/python@3.12/bin/python3.12"
                | "/opt/homebrew/opt/python@3.13/bin/python3.13"
        )
    })
}

pub(super) fn is_homebrew_cellar_version_path(path: &Path) -> bool {
    path.to_str()
        .is_some_and(|value| value.contains("/Cellar/python@"))
}

#[derive(Debug, Clone)]
pub(super) struct AutoReplyRunner {
    pub(super) path: String,
    pub(super) kind: String,
    pub(super) model: String,
    pub(super) reasoning_effort: String,
    pub(super) service_tier: String,
    pub(super) sha256: String,
    pub(super) codex_home: Option<String>,
}

fn sha256_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)
        .with_context(|| format!("open executable for digest: {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .with_context(|| format!("read executable for digest: {}", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

#[cfg(unix)]
pub(super) fn validate_auto_reply_codex_auth(path: &Path, uid: u32) -> Result<()> {
    use std::os::unix::fs::MetadataExt;

    let metadata = fs::symlink_metadata(path)
        .context("AutoReply reply_codex_home/auth.json is unavailable")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.uid() != uid
        || metadata.nlink() != 1
        || metadata.mode() & 0o077 != 0
        || metadata.len() == 0
    {
        anyhow::bail!("AutoReply Codex auth must be a private non-empty user-owned regular file");
    }
    Ok(())
}

pub(super) fn validate_auto_reply_runner(
    config: &config::OpenKakaoConfig,
) -> Result<AutoReplyRunner> {
    let runner = config
        .auto_reply
        .reply_runner
        .as_deref()
        .context("AutoReply reply_runner must be configured explicitly")?;
    let kind = config
        .auto_reply
        .reply_runner_kind
        .as_deref()
        .unwrap_or("gjc");
    if !matches!(kind, "codex" | "gjc" | "opencodex") {
        anyhow::bail!("AutoReply reply_runner_kind must be codex, gjc or opencodex");
    }
    let resolved =
        validate_auto_reply_executable(Some(runner), "AutoReply reply_runner", kind, false)?;
    let home = dirs::home_dir()
        .context("resolve home directory for AutoReply reply_runner")?
        .canonicalize()
        .context("resolve canonical home directory for AutoReply reply_runner")?;
    let resolved_path = Path::new(&resolved);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let uid = unsafe { libc::geteuid() };
        let metadata = fs::metadata(resolved_path)?;
        if metadata.uid() != uid {
            anyhow::bail!("AutoReply reply_runner must be owned by the current user");
        }
        if resolved_path.starts_with(&home) {
            let mut current = resolved_path.parent();
            while let Some(path) = current {
                let metadata = fs::metadata(path)?;
                if metadata.uid() != uid || metadata.mode() & 0o022 != 0 {
                    anyhow::bail!(
                        "AutoReply reply_runner parent has unsafe ownership or permissions"
                    );
                }
                if path == home {
                    break;
                }
                current = path.parent();
            }
        } else {
            let codex_prefix = Path::new("/opt/homebrew/lib/node_modules/@openai/codex/");
            let ocx_prefix = Path::new("/opt/homebrew/lib/node_modules/@bitkyc08/opencodex/");
            if kind == "opencodex" {
                if !resolved_path.starts_with(ocx_prefix)
                    && !resolved_path.starts_with("/opt/homebrew/")
                {
                    anyhow::bail!(
                        "AutoReply reply_runner for opencodex must be in Homebrew prefix"
                    );
                }
            } else if kind != "codex"
                || !resolved_path.starts_with(codex_prefix)
                || resolved_path.file_name().and_then(|value| value.to_str()) != Some("codex")
            {
                anyhow::bail!(
                    "AutoReply reply_runner outside the current user's home must be the native Homebrew Codex CLI"
                );
            }
        }
    }
    let model = config
        .auto_reply
        .reply_model
        .as_deref()
        .unwrap_or(if kind == "codex" {
            "gpt-5.6-luna"
        } else if kind == "gjc" {
            "google-antigravity/gemini-3.7-flash-tiered"
        } else {
            ""
        })
        .trim()
        .to_owned();
    if matches!(kind, "codex" | "gjc")
        && (model.is_empty()
            || model.len() > 128
            || !model
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-/".contains(&byte)))
    {
        anyhow::bail!("AutoReply reply_model is invalid");
    }
    let reasoning_effort = config
        .auto_reply
        .reply_reasoning_effort
        .as_deref()
        .unwrap_or(if kind == "codex" { "max" } else { "medium" })
        .trim()
        .to_owned();
    if kind == "codex"
        && !matches!(
            reasoning_effort.as_str(),
            "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
        )
    {
        anyhow::bail!("AutoReply reply_reasoning_effort is invalid");
    }
    let service_tier = config
        .auto_reply
        .reply_service_tier
        .as_deref()
        .unwrap_or(if kind == "codex" {
            "priority"
        } else {
            "default"
        })
        .trim()
        .to_owned();
    if kind == "codex" && !matches!(service_tier.as_str(), "default" | "priority" | "flex") {
        anyhow::bail!("AutoReply reply_service_tier is invalid");
    }
    if kind == "codex" {
        let output = Command::new(resolved_path)
            .arg("--version")
            .stdin(Stdio::null())
            .output()
            .context("probe AutoReply Codex reply runner")?;
        let version = String::from_utf8_lossy(&output.stdout);
        if !output.status.success()
            || !version.trim().starts_with("codex-cli ")
            || !output.stderr.is_empty()
        {
            // Name the runner and its observed version: selecting the Codex
            // model switches `reply_runner_kind` but keeps the configured
            // `reply_runner`, so a gjc binary left in place is the most likely
            // cause and a bare "probe failed" would hide it.
            anyhow::bail!(
                "AutoReply Codex reply runner version probe failed: runner={} status={} stdout={:?} stderr={:?}",
                resolved_path.display(),
                output.status,
                version.trim(),
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
    }
    let codex_home = if kind == "codex" {
        let configured = config
            .auto_reply
            .reply_codex_home
            .as_deref()
            .context("AutoReply reply_codex_home must be configured for Codex")?;
        let path = Path::new(configured);
        if !path.is_absolute() {
            anyhow::bail!("AutoReply reply_codex_home must be an absolute path");
        }
        let resolved_home =
            fs::canonicalize(path).context("AutoReply reply_codex_home must already exist")?;
        if !resolved_home.starts_with(&home) {
            anyhow::bail!("AutoReply reply_codex_home must stay inside the current user's home");
        }
        let metadata = fs::symlink_metadata(&resolved_home)?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            anyhow::bail!("AutoReply reply_codex_home must be a real directory");
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let uid = unsafe { libc::geteuid() };
            if metadata.uid() != uid || metadata.mode() & 0o077 != 0 {
                anyhow::bail!("AutoReply reply_codex_home must be private and user-owned");
            }
            validate_auto_reply_codex_auth(&resolved_home.join("auth.json"), uid)?;
        }
        Some(resolved_home.to_string_lossy().into_owned())
    } else {
        None
    };
    Ok(AutoReplyRunner {
        path: resolved.clone(),
        kind: kind.to_owned(),
        model,
        reasoning_effort,
        service_tier,
        sha256: sha256_file(resolved_path)?,
        codex_home,
    })
}
