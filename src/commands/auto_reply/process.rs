//! Process lifecycle for `auto-reply`: signal handling, the session-guardian
//! liveness pipe, and reaping supervisor process groups.

use std::fs;
use std::io::{self, Read};
#[cfg(unix)]
use std::os::unix::io::FromRawFd;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::process::{Child, Command};
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};

pub(super) static AUTO_REPLY_STOP: AtomicBool = AtomicBool::new(false);
pub(super) static AUTO_REPLY_GUARDIAN_LOST: AtomicBool = AtomicBool::new(false);

pub(super) const AUTO_REPLY_GUARDIAN_LIVENESS_ENV: &str = "OPENKAKAO_SESSION_GUARDIAN_LIVENESS_FD";

extern "C" fn handle_auto_reply_signal(_: i32) {
    AUTO_REPLY_STOP.store(true, Ordering::Relaxed);
}

#[cfg(unix)]
pub(super) fn validate_auto_reply_guardian_liveness_fd(fd: libc::c_int) -> Result<fs::File> {
    if fd <= libc::STDERR_FILENO {
        anyhow::bail!("session guardian liveness descriptor is invalid");
    }

    let mut metadata = std::mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstat(fd, metadata.as_mut_ptr()) } != 0 {
        anyhow::bail!("session guardian liveness descriptor is unavailable");
    }
    let metadata = unsafe { metadata.assume_init() };
    let status_flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if status_flags < 0 {
        anyhow::bail!("session guardian liveness descriptor is unavailable");
    }
    if metadata.st_mode & libc::S_IFMT != libc::S_IFIFO
        || status_flags & libc::O_ACCMODE != libc::O_RDONLY
    {
        anyhow::bail!("session guardian liveness descriptor is not a read-only pipe");
    }

    let descriptor_flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
    if descriptor_flags < 0
        || unsafe { libc::fcntl(fd, libc::F_SETFD, descriptor_flags | libc::FD_CLOEXEC) } != 0
    {
        anyhow::bail!("session guardian liveness descriptor cannot be isolated");
    }

    let mut readiness = libc::pollfd {
        fd,
        events: libc::POLLIN | libc::POLLHUP | libc::POLLERR,
        revents: 0,
    };
    let poll_result = unsafe { libc::poll(&mut readiness, 1, 0) };
    if poll_result < 0 {
        anyhow::bail!("session guardian liveness handshake failed");
    }
    if poll_result > 0 {
        anyhow::bail!("session guardian liveness was lost before activation");
    }

    // SAFETY: validation above proves this process owns an open descriptor.
    // The returned File becomes its sole Rust owner and closes it on drop.
    Ok(unsafe { fs::File::from_raw_fd(fd) })
}

#[cfg(unix)]
pub(super) fn start_auto_reply_guardian_liveness_monitor(
    check: bool,
) -> Result<Option<thread::JoinHandle<()>>> {
    let Some(raw_value) = std::env::var_os(AUTO_REPLY_GUARDIAN_LIVENESS_ENV) else {
        return Ok(None);
    };
    if check {
        anyhow::bail!("session guardian liveness is invalid in read-only check mode");
    }
    let value = raw_value
        .to_str()
        .context("session guardian liveness descriptor is not valid UTF-8")?;
    if value.is_empty()
        || value.len() > 10
        || !value.bytes().all(|byte| byte.is_ascii_digit())
        || (value.len() > 1 && value.starts_with('0'))
    {
        anyhow::bail!("session guardian liveness descriptor is malformed");
    }
    let fd = value
        .parse::<libc::c_int>()
        .context("session guardian liveness descriptor is out of range")?;
    let mut pipe = validate_auto_reply_guardian_liveness_fd(fd)?;

    // Do not expose even the descriptor number to worker environments. The
    // descriptor itself is already FD_CLOEXEC before any worker can spawn.
    std::env::remove_var(AUTO_REPLY_GUARDIAN_LIVENESS_ENV);
    let monitor = thread::Builder::new()
        .name("guardian-liveness".to_string())
        .spawn(move || {
            let mut byte = [0_u8; 1];
            loop {
                match pipe.read(&mut byte) {
                    Ok(0) | Ok(_) => break,
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => break,
                }
            }
            AUTO_REPLY_GUARDIAN_LOST.store(true, Ordering::Release);
            AUTO_REPLY_STOP.store(true, Ordering::Release);
        })
        .context("start session guardian liveness monitor")?;
    Ok(Some(monitor))
}

#[cfg(not(unix))]
pub(super) fn start_auto_reply_guardian_liveness_monitor(
    _check: bool,
) -> Result<Option<thread::JoinHandle<()>>> {
    if std::env::var_os(AUTO_REPLY_GUARDIAN_LIVENESS_ENV).is_some() {
        anyhow::bail!("session guardian liveness is unsupported on this platform");
    }
    Ok(None)
}

pub(super) struct AutoReplyChildrenGuard {
    pub(super) children: Vec<Child>,
    pub(super) disarmed: bool,
}

impl AutoReplyChildrenGuard {
    pub(super) fn new(capacity: usize) -> Self {
        Self {
            children: Vec::with_capacity(capacity),
            disarmed: false,
        }
    }

    pub(super) fn disarm(&mut self) {
        self.disarmed = true;
    }

    pub(super) fn stop(&mut self) {
        if self.disarmed || self.children.is_empty() {
            return;
        }
        stop_auto_reply_children(&mut self.children);
        self.disarm();
    }
}

impl Drop for AutoReplyChildrenGuard {
    fn drop(&mut self) {
        if !self.disarmed && !self.children.is_empty() {
            stop_auto_reply_children(&mut self.children);
        }
    }
}

fn stop_auto_reply_children(children: &mut [Child]) {
    #[cfg(unix)]
    fn process_group_alive(pid: libc::pid_t) -> bool {
        unsafe {
            libc::kill(-pid, 0) == 0
                || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
        }
    }

    #[cfg(unix)]
    let process_group_ids = children
        .iter()
        .map(|child| child.id() as libc::pid_t)
        .collect::<Vec<_>>();

    #[cfg(unix)]
    for pid in &process_group_ids {
        unsafe {
            if libc::kill(-*pid, libc::SIGTERM) != 0 {
                libc::kill(*pid, libc::SIGTERM);
            }
        }
    }
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    while std::time::Instant::now() < deadline {
        let direct_children_stopped = children
            .iter_mut()
            .all(|child| child.try_wait().ok().flatten().is_some());
        #[cfg(unix)]
        let process_groups_stopped = process_group_ids
            .iter()
            .all(|pid| !process_group_alive(*pid));
        #[cfg(not(unix))]
        let process_groups_stopped = true;
        if direct_children_stopped && process_groups_stopped {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }

    #[cfg(unix)]
    for pid in &process_group_ids {
        if process_group_alive(*pid) {
            unsafe {
                if libc::kill(-*pid, libc::SIGKILL) != 0 {
                    let _ = libc::kill(*pid, libc::SIGKILL);
                }
            }
        }
    }
    for child in children.iter_mut() {
        if child.try_wait().ok().flatten().is_none() {
            #[cfg(unix)]
            let _ = child.kill();
            #[cfg(not(unix))]
            let _ = child.kill();
        }
        let _ = child.wait();
    }

    // `SIGKILL` is asynchronous, and a grandchild that ignored `SIGTERM` is not
    // our direct child: reaping the leader can therefore complete while the rest
    // of the group is still exiting. Drain the groups so `stop` returns with the
    // guarantee callers and tests rely on, that no descendant is left behind.
    #[cfg(unix)]
    {
        let drain_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while std::time::Instant::now() < drain_deadline
            && process_group_ids
                .iter()
                .any(|pid| process_group_alive(*pid))
        {
            thread::sleep(Duration::from_millis(20));
        }
    }
}

pub(super) fn install_auto_reply_signal_handlers() {
    #[cfg(unix)]
    unsafe {
        let handler = handle_auto_reply_signal as *const () as libc::sighandler_t;
        libc::signal(libc::SIGINT, handler);
        libc::signal(libc::SIGTERM, handler);
    }
}

pub(super) fn configure_auto_reply_process_group(command: &mut Command) {
    #[cfg(unix)]
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}
