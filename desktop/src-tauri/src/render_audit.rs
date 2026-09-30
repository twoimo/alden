//! One-shot capture of this instance's real WKWebView. No production bridge,
//! shortcut, tray, arbitrary JavaScript, selectors, or caller-selected files.
use block2::RcBlock;
use objc2::runtime::AnyObject;
use objc2::{AnyThread, MainThreadMarker};
use objc2_app_kit::{NSBitmapImageFileType, NSBitmapImageRep, NSImage, NSWindow};
use objc2_foundation::{NSDictionary, NSError, NSString};
use objc2_web_kit::{WKSnapshotConfiguration, WKWebView};
use serde_json::{json, Value};
use std::ffi::{CString, OsString};
use std::fs::File;
use std::io::Write;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};
use std::sync::{
    atomic::{AtomicI32, Ordering},
    mpsc, Arc,
};
use std::time::{Duration, Instant};
use tauri::Manager;

const FLAG: &str = "--audit-own-webview";
const MAX_BYTES: usize = 2 * 1024 * 1024;
const COLLECT: &str = r#"JSON.stringify((() => {
  const c = document.querySelector('canvas.alden-core');
  const r = c?.getBoundingClientRect();
  const p = window.__aldenRenderPause;
  const d = window.__aldenRenderDiagnostics;
  const ready = document.getElementById('app')?.dataset.state === 'ready';
  const g = ready ? c?.getContext('webgl2') : null;
  return {ready,
    width: innerWidth, height: innerHeight, dpr: devicePixelRatio,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    canvas: c && r ? {width:c.width, height:c.height, x:r.x, y:r.y,
      cssWidth:r.width, cssHeight:r.height, contextLost:g?.isContextLost() ?? null} : null,
    renderCount: window.__aldenRenderCount ?? 0,
    loop: d ? {running:d.running, pendingFrame:d.pendingFrame} : null,
    pause: p ? {state:p.state, eventAtMs:p.eventAtMs, observedAtMs:p.observedAtMs,
      rendersAfterEvent:p.rendersAfterEvent, lastRenderAtMs:p.lastRenderAtMs,
      eventToLastFrameMs:p.eventToLastFrameMs, running:p.running,
      pendingFrame:p.pendingFrame} : null};
})())"#;

pub fn parse_args(args: impl Iterator<Item = OsString>) -> Result<Option<PathBuf>, String> {
    let args: Vec<_> = args.collect();
    if !args
        .iter()
        .any(|arg| arg.to_string_lossy().starts_with(FLAG))
    {
        return Ok(None);
    }
    if args.len() != 2 || args[0] != FLAG {
        return Err("expected --audit-own-webview /absolute/private/directory".into());
    }
    Ok(Some(PathBuf::from(&args[1])))
}

/// Hold the actual directory inode. Each path component is opened relative to
/// its parent with NOFOLLOW; output is exclusive and never overwrites a file.
struct Output(File);
impl Output {
    fn open(path: &Path) -> Result<Self, String> {
        let path_bytes = path.as_os_str().as_bytes();
        if !path.is_absolute() {
            return Err("output directory must be absolute".into());
        }
        if path_bytes
            .split(|byte| *byte == b'/')
            .any(|part| part == b"." || part == b"..")
        {
            return Err("output directory contains a dot component".into());
        }
        let fd = unsafe {
            libc::open(
                c"/".as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err("root directory unavailable".into());
        }
        let mut dir = unsafe { File::from_raw_fd(fd) };
        for component in path.components().skip(1) {
            let Component::Normal(name) = component else {
                return Err("output directory contains a dot component".into());
            };
            let name = CString::new(name.as_bytes()).map_err(|_| "invalid directory name")?;
            let fd = unsafe {
                libc::openat(
                    dir.as_raw_fd(),
                    name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err("output directory unavailable or symlinked".into());
            }
            dir = unsafe { File::from_raw_fd(fd) };
        }
        let meta = dir.metadata().map_err(|_| "output metadata unavailable")?;
        if meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o077 != 0 {
            return Err("output directory must be owned by this user and private".into());
        }
        Ok(Self(dir))
    }

    fn write(&self, name: &str, bytes: &[u8]) -> Result<(), String> {
        if !matches!(
            name,
            "alden-core-before.png"
                | "alden-core-restored.png"
                | "alden-core-retina.png"
                | "render-audit.json"
        ) || bytes.len() > MAX_BYTES
        {
            return Err("output name or byte budget invalid".into());
        }
        let name = CString::new(name).map_err(|_| "invalid output name")?;
        let fd = unsafe {
            libc::openat(
                self.0.as_raw_fd(),
                name.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return Err("output exists or cannot be created".into());
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| "output write failed")?;
        self.0
            .sync_all()
            .map_err(|_| "output directory sync failed".to_string())
    }
}

fn receive<T>(rx: mpsc::Receiver<Result<T, String>>, deadline: Instant) -> Result<T, String> {
    live(deadline)?;
    let remaining = deadline
        .saturating_duration_since(Instant::now())
        .min(Duration::from_secs(4));
    let result = rx
        .recv_timeout(remaining)
        .map_err(|_| "native callback deadline exceeded".to_string())?;
    live(deadline)?;
    result
}

fn live(deadline: Instant) -> Result<(), String> {
    if Instant::now() >= deadline {
        Err("native audit deadline exceeded".into())
    } else {
        Ok(())
    }
}
fn publish(output: &Output, name: &str, bytes: &[u8], deadline: Instant) -> Result<(), String> {
    live(deadline)?;
    output.write(name, bytes)?;
    live(deadline)
}

fn collect(window: &tauri::WebviewWindow, deadline: Instant) -> Result<Value, String> {
    live(deadline)?;
    let (tx, rx) = mpsc::sync_channel(1);
    window
        .with_webview(move |view| unsafe {
            if live(deadline).is_err() {
                let _ = tx.try_send(Err("native audit deadline exceeded".into()));
                return;
            }
            // Tauri dispatches this borrowed platform webview on the main thread.
            let webview = &*view.inner().cast::<WKWebView>();
            let callback = RcBlock::new(move |result: *mut AnyObject, error: *mut NSError| {
                let parsed = if !error.is_null() || result.is_null() {
                    Err("WebKit metric collection failed".into())
                } else if let Some(value) = (&*result).downcast_ref::<NSString>() {
                    let value = value.to_string();
                    if value.len() > 16384 {
                        Err("metric byte budget exceeded".into())
                    } else {
                        serde_json::from_str(&value).map_err(|_| "invalid metric JSON".into())
                    }
                } else {
                    Err("unexpected WebKit metric type".into())
                };
                // A timed-out receiver is dropped. Late callbacks cannot publish.
                let _ = tx.try_send(parsed);
            });
            webview.evaluateJavaScript_completionHandler(
                &NSString::from_str(COLLECT),
                Some(&callback),
            );
        })
        .map_err(|_| "WebKit dispatch failed")?;
    receive(rx, deadline)
}

fn snapshot(window: &tauri::WebviewWindow, deadline: Instant) -> Result<Vec<u8>, String> {
    live(deadline)?;
    let (tx, rx) = mpsc::sync_channel(1);
    window
        .with_webview(move |view| unsafe {
            if live(deadline).is_err() {
                let _ = tx.try_send(Err("native audit deadline exceeded".into()));
                return;
            }
            let webview = &*view.inner().cast::<WKWebView>();
            let config =
                WKSnapshotConfiguration::new(MainThreadMarker::new().expect("Tauri main thread"));
            config.setAfterScreenUpdates(true);
            let callback = RcBlock::new(move |image: *mut NSImage, error: *mut NSError| {
                let bytes = (|| {
                    if !error.is_null() || image.is_null() {
                        return Err("WebKit snapshot failed".into());
                    }
                    let tiff = (&*image)
                        .TIFFRepresentation()
                        .ok_or("snapshot TIFF unavailable")?;
                    if tiff.length() > MAX_BYTES * 4 {
                        return Err("snapshot TIFF budget exceeded".into());
                    }
                    let bitmap = NSBitmapImageRep::initWithData(NSBitmapImageRep::alloc(), &tiff)
                        .ok_or("snapshot bitmap unavailable")?;
                    if bitmap.pixelsWide() <= 0
                        || bitmap.pixelsHigh() <= 0
                        || bitmap.pixelsWide() > 2048
                        || bitmap.pixelsHigh() > 2048
                    {
                        return Err("snapshot pixel budget exceeded".into());
                    }
                    let data = bitmap
                        .representationUsingType_properties(
                            NSBitmapImageFileType::PNG,
                            &NSDictionary::new(),
                        )
                        .ok_or("snapshot PNG unavailable")?;
                    if data.length() > MAX_BYTES {
                        return Err("snapshot PNG budget exceeded".into());
                    }
                    Ok(data.to_vec())
                })();
                let _ = tx.try_send(bytes);
            });
            webview.takeSnapshotWithConfiguration_completionHandler(Some(&config), &callback);
        })
        .map_err(|_| "snapshot dispatch failed")?;
    receive(rx, deadline)
}

fn visibility(
    window: &tauri::WebviewWindow,
    visible: bool,
    deadline: Instant,
) -> Result<(), String> {
    live(deadline)?;
    let (tx, rx) = mpsc::sync_channel(1);
    let target = window.clone();
    window
        .run_on_main_thread(move || {
            if live(deadline).is_err() {
                let _ = tx.try_send(Err("native audit deadline exceeded".into()));
                return;
            }
            // Native orderFront preserves the key window; ordinary hide and the
            // same visibility event still exercise the production pause path.
            let result = if visible {
                target.ns_window().map(|native| unsafe {
                    (&*native.cast::<NSWindow>()).orderFront(None);
                })
            } else {
                target.hide()
            };
            let result = result
                .map_err(|_| "native visibility change failed".to_string())
                .and_then(|_| {
                    if target.is_visible().ok() != Some(visible) {
                        return Err("native visibility readback failed".into());
                    }
                    super::announce_visibility(target.app_handle(), target.label(), visible);
                    Ok(())
                });
            let _ = tx.try_send(result);
        })
        .map_err(|_| "visibility dispatch failed")?;
    receive(rx, deadline)
}

fn count(value: &Value) -> u64 {
    value["renderCount"].as_u64().unwrap_or(0)
}

fn fresh_pause(state: &Value, previous: f64) -> bool {
    state["loop"]["running"] == false
        && state["loop"]["pendingFrame"] == false
        && state["pause"]["state"] == "hidden"
        && state["pause"]["eventAtMs"]
            .as_f64()
            .is_some_and(|event| event > previous)
}
fn validate_layout(state: &Value) -> Result<(), String> {
    if state["ready"] != true
        || state["canvas"]["contextLost"] != false
        || state["width"] != 276
        || state["height"] != 260
        || state["scrollWidth"] != 276
        || state["scrollHeight"] != 260
        || state["canvas"]["cssWidth"] != 236
        || state["canvas"]["cssHeight"] != 236
        || state["canvas"]["x"] != 20
        || state["canvas"]["y"] != 12
    {
        return Err("native panel layout or WebGL context invalid".into());
    }
    Ok(())
}
fn density_matches(state: &Value) -> bool {
    let Some(dpr) = state["dpr"]
        .as_f64()
        .filter(|dpr| dpr.is_finite() && *dpr > 0.0)
    else {
        return false;
    };
    let pixels = (236.0 * dpr.min(2.0)).floor() as u64;
    state["canvas"]["width"].as_u64() == Some(pixels)
        && state["canvas"]["height"].as_u64() == Some(pixels)
}

fn audit(app: &tauri::AppHandle, output: &Output, deadline: Instant) -> Result<Value, String> {
    let window = app
        .get_webview_window("alden")
        .ok_or("Alden webview missing")?;
    let initial_scale = window
        .scale_factor()
        .map_err(|_| "native scale unavailable")?;
    visibility(&window, true, deadline)?;
    let mut before = collect(&window, deadline)?;
    while before["ready"] != true || count(&before) < 3 {
        if Instant::now() >= deadline {
            return Err("renderer readiness deadline exceeded".into());
        }
        std::thread::sleep(Duration::from_millis(100));
        before = collect(&window, deadline)?;
    }
    validate_layout(&before)?;
    if !density_matches(&before) {
        return Err("initial backing density invalid".into());
    }
    publish(
        output,
        "alden-core-before.png",
        &snapshot(&window, deadline)?,
        deadline,
    )?;
    let mut cycles = Vec::new();
    for index in 0..10 {
        let prior = collect(&window, deadline)?;
        let previous = prior["pause"]["eventAtMs"].as_f64().unwrap_or(-1.0);
        let hide_started = Instant::now();
        visibility(&window, false, deadline)?;
        let hide_return_ms = hide_started.elapsed().as_secs_f64() * 1000.0;
        let stopped = loop {
            let state = collect(&window, deadline)?;
            if fresh_pause(&state, previous) {
                break state;
            }
            if Instant::now() >= deadline {
                return Err("render stop deadline exceeded".into());
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        let stop_observed_ms = hide_started.elapsed().as_secs_f64() * 1000.0;
        std::thread::sleep(Duration::from_millis(350));
        let hidden = collect(&window, deadline)?;
        if count(&hidden) != count(&stopped) || hidden["pause"]["rendersAfterEvent"] != 0 {
            return Err("hidden renderer continued producing frames".into());
        }
        visibility(&window, true, deadline)?;
        std::thread::sleep(Duration::from_millis(250));
        let restored = collect(&window, deadline)?;
        if count(&restored) <= count(&hidden) || restored["loop"]["running"] != true {
            return Err("renderer did not resume".into());
        }
        cycles.push(
            json!({"index":index, "hideRequestToNativeReturnMs":hide_return_ms,
            "hideRequestToStoppedObservationUpperBoundMs":stop_observed_ms,
            "hiddenSampleMs":350, "hiddenRenderDelta":count(&hidden)-count(&stopped),
            "pause":hidden["pause"], "resumeRenderDelta":count(&restored)-count(&hidden)}),
        );
    }
    let restored = collect(&window, deadline)?;
    validate_layout(&restored)?;
    publish(
        output,
        "alden-core-restored.png",
        &snapshot(&window, deadline)?,
        deadline,
    )?;
    let monitors = window
        .available_monitors()
        .map_err(|_| "native monitor readback failed")?;
    let mut retina = json!({"available":false});
    if let Some(monitor) = monitors
        .iter()
        .find(|monitor| monitor.scale_factor() >= 2.0)
    {
        let area = monitor.work_area();
        live(deadline)?;
        let position = tauri::PhysicalPosition::new(area.position.x + 100, area.position.y + 100);
        let (tx, rx) = mpsc::sync_channel(1);
        let target = window.clone();
        window
            .run_on_main_thread(move || {
                let result = live(deadline).and_then(|_| {
                    target
                        .set_position(position)
                        .map_err(|_| "owned window monitor move failed".into())
                });
                let _ = tx.try_send(result);
            })
            .map_err(|_| "monitor move dispatch failed")?;
        receive(rx, deadline)?;
        let settle_deadline = deadline.min(Instant::now() + Duration::from_secs(3));
        let metrics = loop {
            let state = collect(&window, settle_deadline)?;
            if state["dpr"].as_f64() == Some(monitor.scale_factor()) {
                break state;
            }
            if Instant::now() >= settle_deadline {
                return Err("native Retina scale did not reach webview".into());
            }
            std::thread::sleep(Duration::from_millis(50));
        };
        std::thread::sleep(Duration::from_millis(250));
        let observed = collect(&window, deadline)?;
        validate_layout(&observed)?;
        if count(&observed) <= count(&metrics) {
            return Err("Retina renderer did not advance after movement".into());
        }
        publish(
            output,
            "alden-core-retina.png",
            &snapshot(&window, deadline)?,
            deadline,
        )?;
        retina = json!({"available":true,"nativeScaleFactor":window.scale_factor().map_err(|_| "native scale unavailable")?,
            "transition":metrics,"observed":observed,"densityMatches":density_matches(&observed)});
    }
    visibility(&window, false, deadline)?;
    Ok(json!({"before":before,"restored":restored,"cycles":cycles,
        "nativeScaleFactor":initial_scale,"retina":retina,
        "monitorScaleFactors":monitors.iter().map(|monitor|monitor.scale_factor()).collect::<Vec<_>>()}))
}

pub fn run(directory: PathBuf, context: tauri::Context<tauri::Wry>) {
    let output = Output::open(&directory).unwrap_or_else(|error| {
        eprintln!("Alden render audit: {error}");
        std::process::exit(2);
    });
    let status = Arc::new(AtomicI32::new(1));
    let worker_status = Arc::clone(&status);
    tauri::Builder::default()
        // Deliberately no PythonBridge or production command handler. The real
        // packaged frontend renders idle; runtime requests are unavailable.
        .invoke_handler(tauri::generate_handler![super::window_is_visible])
        .on_window_event(super::handle_window_event)
        .setup(move |app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(40);
                let result = audit(&handle, &output, deadline);
                let success = result.as_ref().is_ok_and(|result| result["retina"]["available"] != true || result["retina"]["densityMatches"] == true) && live(deadline).is_ok();
                let error = result.as_ref().err().cloned().or_else(|| if success { None } else { Some("density validation or final deadline failed".into()) });
                let report = json!({"schema":1,"pid":std::process::id(),
                    "mode":"isolated-own-webview-no-production-bridge",
                    "executable":std::env::current_exe().ok(),"version":env!("CARGO_PKG_VERSION"),
                    "result":result.ok(),"success":success,"error":error,
                    "scope":"own native WKWebView; no existing-process, OS-lock, tray click, shortcut, voice or inference attestation"});
                let bytes = serde_json::to_vec_pretty(&report).expect("audit JSON");
                let written = if success { publish(&output,"render-audit.json", &bytes,deadline) } else { output.write("render-audit.json", &bytes) };
                let delivered = written.is_ok();
                if let Err(error) = written { eprintln!("Alden render audit: {error}"); }
                let code = if success && delivered && live(deadline).is_ok() { 0 } else { 1 };
                worker_status.store(code,Ordering::Release);
                handle.exit(code);
            });
            Ok(())
        })
        .run(context)
        .expect("error while running Alden render audit");
    std::process::exit(status.load(Ordering::Acquire));
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};
    #[test]
    fn expired_callbacks_and_stale_pause_records_are_rejected() {
        let (tx, rx) = mpsc::sync_channel(1);
        tx.send(Ok(42)).unwrap();
        assert!(receive(rx, Instant::now() - Duration::from_millis(1)).is_err());
        let mut state = json!({"loop":{"running":false,"pendingFrame":false},"pause":{"state":"hidden","eventAtMs":10}});
        assert!(!fresh_pause(&state, 10.0));
        state["pause"]["eventAtMs"] = json!(11);
        assert!(fresh_pause(&state, 10.0));
        state["loop"]["running"] = json!(true);
        assert!(!fresh_pause(&state, 10.0));
    }
    #[test]
    fn layout_rejects_context_loss_overflow_and_wrong_backing_density() {
        let mut state = json!({"ready":true,"width":276,"height":260,"scrollWidth":276,"scrollHeight":260,"dpr":2,"canvas":{"contextLost":false,"cssWidth":236,"cssHeight":236,"x":20,"y":12,"width":236,"height":236}});
        assert!(validate_layout(&state).is_ok());
        assert!(!density_matches(&state));
        state["canvas"]["width"] = json!(472);
        state["canvas"]["height"] = json!(472);
        assert!(density_matches(&state));
        state["canvas"]["contextLost"] = json!(true);
        assert!(validate_layout(&state).is_err());
        state["canvas"]["contextLost"] = json!(false);
        state["scrollWidth"] = json!(277);
        assert!(validate_layout(&state).is_err());
    }
    fn root() -> PathBuf {
        let path = PathBuf::from(format!(
            "/private/tmp/alden-render-audit-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        path
    }
    #[test]
    fn arguments_preserve_normal_startup_and_reject_ambiguous_audits() {
        assert!(parse_args([OsString::from("-psn_0_123")].into_iter())
            .unwrap()
            .is_none());
        assert!(parse_args([OsString::from(FLAG)].into_iter()).is_err());
        assert!(parse_args([OsString::from(format!("{FLAG}=x"))].into_iter()).is_err());
        assert!(
            parse_args([OsString::from(FLAG), OsString::from("/private/tmp/x")].into_iter())
                .unwrap()
                .is_some()
        );
    }
    #[test]
    fn output_rejects_shared_paths_symlinks_and_overwrites() {
        let path = root();
        let output = Output::open(&path).unwrap();
        assert!(publish(
            &output,
            "alden-core-before.png",
            b"test",
            Instant::now() - Duration::from_millis(1)
        )
        .is_err());
        assert!(!path.join("alden-core-before.png").exists());
        output.write("render-audit.json", b"{}").unwrap();
        assert!(output.write("render-audit.json", b"changed").is_err());
        assert_eq!(
            std::fs::read(path.join("render-audit.json")).unwrap(),
            b"{}"
        );
        assert_eq!(
            std::fs::metadata(path.join("render-audit.json"))
                .unwrap()
                .mode()
                & 0o777,
            0o600
        );
        assert!(output.write("other", b"{}").is_err());
        assert!(output
            .write("alden-core-before.png", &vec![0; MAX_BYTES + 1])
            .is_err());
        symlink(&path, path.join("link")).unwrap();
        assert!(Output::open(&path.join("link")).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(Output::open(&path).is_err());
        assert!(Output::open(Path::new("relative")).is_err());
        std::fs::remove_dir_all(path).unwrap();
    }
}
