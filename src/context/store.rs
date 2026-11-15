//! The context database file: where it lives, how it is opened, and the
//! fail-closed guards around it. Writers get a WAL connection with private
//! file permissions; readers get a read-only handle that refuses a missing or
//! symlinked database instead of creating or following it.

use super::schema::{migrate, CONTEXT_RETRIEVAL_MIGRATION_REQUIRED};
use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

pub fn default_db_path() -> PathBuf {
    let base = dirs::data_local_dir()
        .or_else(dirs::home_dir)
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    base.join("openkakao").join("context.sqlite3")
}

/// How long a context-DB connection waits for a competing writer before
/// failing with SQLITE_BUSY.
///
/// Every AutoReply room syncs the same context database, and on a multi-hundred
/// megabyte database a concurrent `context-sync-local` run can hold the write
/// lock for longer than five seconds. Timing out there fences the room and
/// exits its watcher, so wait the contention out instead.
pub(super) const CONTEXT_DB_BUSY_TIMEOUT_SECS: u64 = 30;

pub(super) fn set_context_db_permissions(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700)).with_context(|| {
                format!("secure context database directory {}", parent.display())
            })?;
        }

        for file in [
            path.to_path_buf(),
            sqlite_sidecar_path(path, "-wal"),
            sqlite_sidecar_path(path, "-shm"),
        ] {
            let metadata = match fs::symlink_metadata(&file) {
                Ok(metadata) => metadata,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => {
                    return Err(error).with_context(|| {
                        format!("inspect context database file {}", file.display())
                    })
                }
            };
            if !metadata.file_type().is_file() {
                anyhow::bail!(
                    "context database path is not a regular file: {}",
                    file.display()
                );
            }
            fs::set_permissions(&file, fs::Permissions::from_mode(0o600))
                .with_context(|| format!("secure context database file {}", file.display()))?;
        }
    }
    Ok(())
}

pub(super) fn sqlite_sidecar_path(path: &Path, suffix: &str) -> PathBuf {
    let mut sidecar = path.as_os_str().to_os_string();
    sidecar.push(suffix);
    PathBuf::from(sidecar)
}

pub(super) fn checkpoint_context_wal_truncate(conn: &Connection) -> Result<()> {
    let (busy, log_frames, checkpointed_frames): (i64, i64, i64) =
        conn.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        })?;
    if busy != 0 {
        anyhow::bail!(
            "context WAL truncate checkpoint remained busy ({checkpointed_frames}/{log_frames} frames checkpointed)"
        );
    }
    Ok(())
}

pub(super) fn open_db_readonly(path: &Path) -> Result<Connection> {
    if path.is_symlink() || !path.is_file() {
        anyhow::bail!("{CONTEXT_RETRIEVAL_MIGRATION_REQUIRED}");
    }
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .with_context(|| format!("open context database read-only: {}", path.display()))?;
    conn.busy_timeout(Duration::from_secs(CONTEXT_DB_BUSY_TIMEOUT_SECS))?;
    Ok(conn)
}

pub(super) fn open_db(path: &Path) -> Result<Connection> {
    if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    set_context_db_permissions(path)?;
    let was_missing = !path.exists();
    let conn = Connection::open(path)?;
    conn.busy_timeout(Duration::from_secs(CONTEXT_DB_BUSY_TIMEOUT_SECS))?;
    // Workers open read-only context snapshots while the watcher updates the
    // shared index. WAL keeps those readers on their committed snapshot.
    let journal_mode: String = conn.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
    if !journal_mode.eq_ignore_ascii_case("wal") {
        let enabled_mode: String =
            conn.query_row("PRAGMA journal_mode = WAL", [], |row| row.get(0))?;
        if !enabled_mode.eq_ignore_ascii_case("wal") {
            anyhow::bail!("context database could not enter WAL mode");
        }
    }
    set_context_db_permissions(path)?;
    migrate(&conn, was_missing)?;
    set_context_db_permissions(path)?;
    Ok(conn)
}
