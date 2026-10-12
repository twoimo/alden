//! One-shot capture of owned WKWebViews. Both modes admit only fixed
//! read-only persisted graph commands; no inference or production writes.
//! No tray, shortcut, caller-selected script, selector or output filename.
use block2::RcBlock;
use objc2::runtime::AnyObject;
use objc2::{AnyThread, MainThreadMarker};
use objc2_app_kit::{
    NSBitmapImageFileType, NSBitmapImageRep, NSFloatingWindowLevel, NSImage, NSWindow,
};
use objc2_foundation::{NSDictionary, NSError, NSSize, NSString};
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
    atomic::{AtomicBool, AtomicI32, AtomicU8, Ordering},
    mpsc, Arc,
};
use std::time::{Duration, Instant};
use tauri::Manager;

const FLAG: &str = "--audit-own-webview";
const SETTINGS_FLAG: &str = "--audit-own-settings";
const WORKSPACES_FLAG: &str = "--audit-own-workspaces";
const ASSISTANT_FLAG: &str = "--audit-own-assistant-input";
const ASSISTANT_INVALID_FLAG: &str = "--audit-own-assistant-invalid-input";
const ENTITY_FLAG: &str = "--audit-own-entity-review";
pub struct Request {
    pub directory: PathBuf,
    pub settings: bool,
    pub workspaces: bool,
    pub assistant: bool,
    pub assistant_invalid: bool,
    pub entity: bool,
    pub focus_node: Option<String>,
}
struct AuditPolicy {
    workspaces: bool,
    entity: bool,
    focus_node: Option<String>,
}
struct AssistantPageReady { ready: Arc<AtomicBool>, phase: Arc<AtomicU8> }

#[tauri::command]
fn audit_assistant_ready(window: tauri::WebviewWindow, ready: tauri::State<'_, AssistantPageReady>, phase: String) {
    if window.label() == "settings" {
        let code = match phase.as_str() {"injected"=>1, "dom"=>2, "composer"=>3, _=>return};
        ready.phase.store(code, Ordering::Release);
        if code == 3 {ready.ready.store(true, Ordering::Release);}
        eprintln!("Alden assistant readiness phase {code}");
    }
}

const ASSISTANT_READY_SCRIPT: &str = r#"
;(() => {
  const start = () => {
  const signal = phase => window.__TAURI_INTERNALS__.invoke('audit_assistant_ready', {phase}).catch(() => {});
  let sent = false;
  const ready = () => {
    const state = document.getElementById('app')?.dataset.state;
    if (!sent && (state === 'ready' || state === 'unavailable') && document.getElementById('assistant-composer-text')) {
      sent = true; observer.disconnect(); signal('composer');
    }
  };
  const observer = new MutationObserver(ready);
  observer.observe(document, {childList:true, subtree:true, attributes:true, attributeFilter:['data-state']});
  signal('dom'); ready();
  };
  // append_invoke_initialization_script runs before Tauri's metadata/core
  // scripts. The public invoke function is available after DOMContentLoaded.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else queueMicrotask(start);
})();
"#;
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

pub fn parse_args(args: impl Iterator<Item = OsString>) -> Result<Option<Request>, String> {
    let args: Vec<_> = args.collect();
    if !args.iter().any(|arg| {
        [FLAG, SETTINGS_FLAG, WORKSPACES_FLAG, ASSISTANT_FLAG, ASSISTANT_INVALID_FLAG, ENTITY_FLAG]
            .iter()
            .any(|flag| arg.to_string_lossy().starts_with(flag))
    }) {
        return Ok(None);
    }
    let focus_node = if args.len() == 4 && args[0] == WORKSPACES_FLAG && args[2] == "--focus-node" {
        let value = args[3].to_str().ok_or("focus node must be UTF-8")?;
        let parts: Vec<_> = value
            .strip_prefix("osk:")
            .unwrap_or("")
            .split('-')
            .collect();
        let alnum = |s: &str| {
            s.bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        };
        let osk = parts.len() == 3
            && parts[0].len() == 6
            && parts[0].bytes().all(|b| b.is_ascii_digit())
            && parts[1].len() == 4
            && alnum(parts[1])
            && [4, 8].contains(&parts[2].len())
            && alnum(parts[2]);
        let collection = value.split_once(':').is_some_and(|(prefix, hash)| {
            ["graph", "youtube", "threads", "files"].contains(&prefix)
                && hash.len() == 64
                && hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        });
        if !osk && !collection {
            return Err("focus node must be one canonical OSK or collection ID".into());
        }
        Some(value.to_string())
    } else {
        None
    };
    if (args.len() != 2 && focus_node.is_none())
        || args[0] == ENTITY_FLAG && focus_node.is_some()
        || ![FLAG, SETTINGS_FLAG, WORKSPACES_FLAG, ASSISTANT_FLAG, ASSISTANT_INVALID_FLAG, ENTITY_FLAG]
            .iter()
            .any(|flag| args[0] == *flag)
    {
        return Err("expected --audit-own-webview /absolute/private/directory".into());
    }
    Ok(Some(Request {
        directory: PathBuf::from(&args[1]),
        settings: args[0] != FLAG,
        workspaces: args[0] == WORKSPACES_FLAG || args[0] == ASSISTANT_FLAG || args[0] == ASSISTANT_INVALID_FLAG || args[0] == ENTITY_FLAG,
        assistant: args[0] == ASSISTANT_FLAG || args[0] == ASSISTANT_INVALID_FLAG,
        assistant_invalid: args[0] == ASSISTANT_INVALID_FLAG,
        entity: args[0] == ENTITY_FLAG,
        focus_node,
    }))
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
                | "alden-settings-default.png"
                | "alden-settings-compact.png"
                | "alden-note-default.png"
                | "alden-note-compact.png"
                | "alden-connections-default.png"
                | "alden-connections-compact.png"
                | "assistant-turn-1.png"
                | "assistant-turn-2.png"
                | "assistant-turn-3.png"
                | "alden-model-menu-default.png"
                | "alden-model-menu-compact.png"
                | "workspace-memory-default.png"
                | "workspace-conversation-default.png"
                | "workspace-reply-default.png"
                | "workspace-geeknews-default.png"
                | "workspace-voice-default.png"
                | "workspace-history-default.png"
                | "workspace-settings-default.png"
                | "workspace-memory-compact.png"
                | "workspace-conversation-compact.png"
                | "workspace-reply-compact.png"
                | "workspace-geeknews-compact.png"
                | "workspace-voice-compact.png"
                | "workspace-history-compact.png"
                | "collection-schedule-default.png"
                | "collection-schedule-compact.png"
                | "collection-source-default.png"
                | "collection-source-compact.png"
                | "workspace-settings-compact.png"
                | "settings-layout.json"
                | "workspace-readback.json"
                | "knowledge-quality-default.png"
                | "knowledge-quality-compact.png"
                | "entity-compare-default.png"
                | "entity-compare-compact.png"
                | "entity-applied-default.png"
                | "entity-applied-compact.png"
                | "entity-reverted-default.png"
                | "entity-reverted-compact.png"
                | "activity-ready.json"
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
    collect_script(window, COLLECT.to_string(), deadline)
}

fn collect_script(
    window: &tauri::WebviewWindow,
    script: String,
    deadline: Instant,
) -> Result<Value, String> {
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
                &NSString::from_str(&script),
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

/// A settled view legitimately stops after one frame. A restore must draw
/// something new, but it must not be forced into a continuous idle loop.
fn rendered_since(value: &Value, before: u64) -> bool {
    value["ready"] == true && count(value) > before && value["canvas"]["contextLost"] == false
}

fn resumed_graph(value: &Value, before: u64) -> bool {
    rendered_since(value, before)
        && (value["navigation"]["source"] != "collection"
            || value["activityJournal"]["active"] == true)
}

fn fresh_pause(state: &Value, previous: f64) -> bool {
    state["loop"]["running"] == false
        && state["loop"]["pendingFrame"] == false
        && state["pause"]["state"] == "hidden"
        && state["pause"]["eventAtMs"]
            .as_f64()
            .is_some_and(|event| event > previous)
}
fn css_extent(value: &Value, expected: f64) -> bool {
    // WKWebView quantizes CSS layout to 1/64 px, unlike Chromium's integral
    // rectangle here. Admit one quantum; keep the logical window exact.
    value
        .as_f64()
        .is_some_and(|number| number.is_finite() && (number - expected).abs() <= 1.0 / 64.0)
}
fn validate_layout(state: &Value) -> Result<(), String> {
    if state["ready"] != true
        || state["canvas"]["contextLost"] != false
        || state["width"] != 560
        || state["height"] != 420
        || state["scrollWidth"] != 560
        || state["scrollHeight"] != 420
        || !css_extent(&state["canvas"]["cssWidth"], 558.0)
        || !css_extent(&state["canvas"]["cssHeight"], 418.0)
        || !css_extent(&state["canvas"]["x"], 1.0)
        || !css_extent(&state["canvas"]["y"], 1.0)
    {
        return Err(format!(
            "native panel layout or WebGL context invalid: {state}"
        ));
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
    let Some(css_width) = state["canvas"]["cssWidth"].as_f64() else {
        return false;
    };
    let Some(css_height) = state["canvas"]["cssHeight"].as_f64() else {
        return false;
    };
    let width = (css_width.floor() * dpr.min(2.0)).floor() as u64;
    let height = (css_height.floor() * dpr.min(2.0)).floor() as u64;
    state["canvas"]["width"].as_u64() == Some(width)
        && state["canvas"]["height"].as_u64() == Some(height)
}

#[tauri::command]
async fn fetch_settings_action(
    bridge: tauri::State<'_, super::PythonBridge>,
    policy: tauri::State<'_, AuditPolicy>,
    action: String,
    query: Option<String>,
    node_id: Option<String>,
    chat_id: Option<String>,
    explicit_opt_in: Option<bool>,
) -> Result<Value, String> {
    let workspace = policy.workspaces;
    let entity_mutation = policy.workspaces && policy.entity && explicit_opt_in == Some(true)
        && matches!(action.as_str(), "collection-resolution-apply" | "collection-resolution-revert");
    if !audit_read_action(&action, workspace) && !entity_mutation {
        return Err("audit admits explicit read actions only".into());
    }
    let bridge = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        if workspace {
            bridge.fetch_settings_action(
                &action,
                query.as_deref(),
                node_id.as_deref(),
                chat_id.as_deref(),
                None,
                if entity_mutation { Some(true) } else { None },
                None,
            )
        } else {
            bridge.fetch_persisted_graph()
        }
    })
    .await
    .map_err(|_| "graph read worker failed".to_string())?
    .map_err(|error| error.to_string())
}
fn audit_read_action(action: &str, workspace: bool) -> bool {
    matches!(action, "knowledge-graph" | "knowledge-graph-status")
        || workspace
            && matches!(
                action,
                "knowledge-graph-focus"
                    | "history-rooms"
                    | "history-messages"
                    | "voice-history-sessions"
                    | "voice-history-messages"
                    | "db-sync-history"
                    | "collection-history"
                    | "collection-projects"
                    | "collection-resolution-preview"
                    | "collection-resolution-history"
                    | "collection-scheduler-status"
                    | "routed-models"
                    | "collection-graph"
                    | "collection-affinity"
                    | "collection-quality"
                    | "reply-history"
                    | "geeknews-history"
                    | "room-catalog"
            )
}

fn wait_for_workspace(
    page: &str,
    size: &str,
    deadline: Instant,
    mut sample: impl FnMut() -> Result<Value, String>,
    mut paint: impl FnMut() -> Result<(), String>,
) -> Result<Value, String> {
    let started = Instant::now();
    let mut last_state = Value::Null;
    let mut paint_after_data = false;
    let result = (|| loop {
        live(deadline)?;
        last_state = sample()?;
        live(deadline)?;
        if last_state["page"] == page && last_state["settled"] == true {
            if !paint_after_data {
                paint()?;
                live(deadline)?;
                paint_after_data = true;
                continue;
            }
            return Ok(last_state.clone());
        }
        std::thread::sleep(
            Duration::from_millis(50).min(deadline.saturating_duration_since(Instant::now())),
        );
    })();
    result.map_err(|error: String| {
        format!(
            "workspace {page}/{size} readiness failed after {:.0}ms: {error}; last observed state={last_state}",
            started.elapsed().as_secs_f64() * 1000.0
        )
    })
}

// Capture geometry includes every clipping ancestor, not only the browser viewport.
// This is audit-only: normal navigation keeps its per-page scroll restoration.
const WORKSPACE_TITLE_LAYOUT: &str = r#"(()=>{
  const page=document.querySelector('.settings-shell')?.dataset.settingsPage;
  const panel=document.querySelector(`#settings-page-${page}`);
  const title=panel?.querySelector('h1');
  const rect=title?.getBoundingClientRect();
  const clip={top:0,bottom:innerHeight,left:0,right:innerWidth};
  const offsets=[];
  for(let node=panel;node;node=node.parentElement){
    offsets.push({tag:node.tagName,id:node.id,top:node.scrollTop,left:node.scrollLeft});
  }
  for(let node=title?.parentElement;node;node=node.parentElement){
    const style=getComputedStyle(node),box=node.getBoundingClientRect();
    if(/^(auto|scroll|hidden|clip)$/.test(style.overflowY)){
      clip.top=Math.max(clip.top,box.top+node.clientTop);
      clip.bottom=Math.min(clip.bottom,box.top+node.clientTop+node.clientHeight);
    }
    if(/^(auto|scroll|hidden|clip)$/.test(style.overflowX)){
      clip.left=Math.max(clip.left,box.left+node.clientLeft);
      clip.right=Math.min(clip.right,box.left+node.clientLeft+node.clientWidth);
    }
  }
  return {title:rect?{top:rect.top,bottom:rect.bottom,left:rect.left,right:rect.right,
    width:rect.width,height:rect.height}:null,clip,offsets,windowX:scrollX,windowY:scrollY};
})()"#;

fn validate_workspace_title(state: &Value, size: &str) -> Result<(), String> {
    let layout = &state["titleLayout"];
    let number = |value: &Value| value.as_f64().filter(|number| number.is_finite());
    let at_origin = layout["offsets"].as_array().is_some_and(|offsets| {
        !offsets.is_empty()
            && offsets.iter().all(|offset| {
                ["top", "left"]
                    .iter()
                    .all(|key| number(&offset[key]).is_some_and(|value| value.abs() <= 0.5))
            })
    }) && ["windowX", "windowY"]
        .iter()
        .all(|key| number(&layout[key]).is_some_and(|value| value.abs() <= 0.5));
    let title = &layout["title"];
    let clip = &layout["clip"];
    let visible = ["width", "height"]
        .iter()
        .all(|key| number(&title[key]).is_some_and(|value| value > 0.0))
        && [("top", "bottom"), ("left", "right")]
            .iter()
            .all(|(start, end)| {
                match (
                    number(&title[start]),
                    number(&title[end]),
                    number(&clip[start]),
                    number(&clip[end]),
                ) {
                    (Some(a), Some(b), Some(c), Some(d)) => {
                        b > a && d > c && a >= c - 0.5 && b <= d + 0.5
                    }
                    _ => false,
                }
            });
    if !at_origin || !visible {
        return Err(format!(
            "workspace {}/{size} title capture invalid: at_origin={at_origin}, fully_visible={visible}; observed={layout}",
            state["page"].as_str().unwrap_or("unknown")
        ));
    }
    Ok(())
}

fn capture_workspaces(
    window: &tauri::WebviewWindow,
    output: &Output,
    size: &str,
    deadline: Instant,
) -> Result<Vec<Value>, String> {
    let mut states = Vec::new();
    for page in [
        "memory",
        "conversation",
        "reply",
        "geeknews",
        "voice",
        "history",
        "settings",
    ] {
        collect_script(
            window,
            format!(
                r#"JSON.stringify((()=>{{
          const clickedAtMs=performance.now();document.querySelector('#settings-tab-{page}').click();
          const before={WORKSPACE_TITLE_LAYOUT};
          for(let node=document.querySelector('#settings-page-{page}');node;node=node.parentElement){{
            node.scrollTop=0;node.scrollLeft=0;
          }}
          const paint=window.__aldenAuditWorkspacePaint={{page:'{page}',ready:false,clickedAtMs,
            firstFrameAtMs:null,captureOrigin:{{before,after:{WORKSPACE_TITLE_LAYOUT}}}}};
          requestAnimationFrame(()=>{{paint.firstFrameAtMs=performance.now();requestAnimationFrame(()=>{{paint.ready=true;}});}});
          return {{selected:true}};
        }})())"#
            ),
            deadline,
        )?;
        let mut state = wait_for_workspace(
            page,
            size,
            deadline,
            || {
                collect_script(window,r#"JSON.stringify((()=>{
              const p=document.querySelector('.settings-shell')?.dataset.settingsPage;
              const prompt=(p==='voice'||p==='conversation')?document.querySelector(`#${p}-placeholder`):null;
              const pending=(p==='voice'||p==='conversation')&&prompt&&!prompt.hidden&&prompt.querySelector('strong')?.textContent.includes('불러옵니다');
              const loadingSettings=p==='settings'&&document.querySelector('#automation-count')?.textContent==='확인 중'&&!document.querySelector('#automation-status')?.textContent;
              const loadingHistory=p==='history'&&document.querySelector('#db-current-title')?.textContent==='갱신 상태 확인 중';
              const collection=document.querySelector('.collection-history');
              const loadingCollection=p==='history'&&collection?.getAttribute('aria-busy')==='true';
              const schedule=document.querySelector('.collection-schedule');
              const loadingSchedule=p==='history'&&schedule?.getAttribute('aria-busy')==='true';
              const loadingGraph=p==='memory'&&(document.querySelector('#knowledge-filter-form')?.getAttribute('aria-busy')==='true'||document.querySelector('#knowledge-summary')?.textContent==='불러오는 중');
              const loadingAutomation=(p==='reply'||p==='geeknews')&&document.querySelector(`#${p}-status`)?.textContent==='기록을 불러옵니다.';
              const panel=document.querySelector(`#settings-page-${p}`);
              const list=panel?.querySelector('.message-scroll');const r=list?.getBoundingClientRect();
              const rows=[...(list?.querySelectorAll('.message-row')??[])].map(n=>n.getBoundingClientRect());
              const listPending=!!r&&r.height>0&&rows.length===0&&prompt?.hidden===true;
              const statusId={memory:'knowledge-summary',conversation:'conversation-history-status',reply:'reply-status',geeknews:'geeknews-status',voice:'voice-history-status',history:'db-current-title',settings:'automation-status'}[p];
              const status=panel?.querySelector('#'+statusId)?.textContent??'';
              const paint=window.__aldenAuditWorkspacePaint;
              const pendingReasons=[pending&&'messages',loadingSettings&&'settings',loadingHistory&&'history',
                loadingCollection&&'collection',loadingSchedule&&'schedule',loadingGraph&&'graph',
                loadingAutomation&&'automation',listPending&&'message-layout',
                (paint?.page!==p||paint?.ready!==true)&&'paint'].filter(Boolean);
              return {page:p,width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
                settled:pendingReasons.length===0,pendingReasons,
                graphProject:p==='memory'?document.querySelector('#knowledge-project')?.value:null,
                graphScope:p==='memory'?document.querySelector('#knowledge-scope')?.textContent:null,
                graphDisplayedNodes:p==='memory'?window.__knowledgeRenderDiagnostics?.nodeCount:null,
                graphDisplayedEdges:p==='memory'?window.__knowledgeRenderDiagnostics?.edgeCount:null,
                collectionState:p==='history'?collection?.dataset.state:null,
                collectionRenderedRows:p==='history'?collection?.querySelectorAll('.collection-event-row').length:0,
                collectionSummary:p==='history'?collection?.querySelector('.collection-summary')?.textContent:null,
                collectionScheduleState:p==='history'?schedule?.dataset.state:null,
                collectionScheduleTargets:p==='history'?schedule?.dataset.targetCount:null,
                navigationFirstFrameMs:typeof paint?.firstFrameAtMs==='number'?paint.firstFrameAtMs-paint.clickedAtMs:null,
                transcriptViewportHeight:r?.height??0,
                fullyVisibleMessages:r?rows.filter(n=>n.top>=r.top-.5&&n.bottom<=r.bottom+.5).length:0,
                errorVisible:/불러오지 못|접근 확인 필요|확인하지 못/.test(status),
                placeholderVisible:!!prompt&&!prompt.hidden,
                graphRenders:window.__knowledgeRenderCount??0,
                captureOrigin:paint?.captureOrigin,titleLayout:__AUDIT_TITLE_LAYOUT__};
            })())"#.replace("__AUDIT_TITLE_LAYOUT__",WORKSPACE_TITLE_LAYOUT),deadline)
            },
            || {
                collect_script(window,"JSON.stringify((()=>{const paint=window.__aldenAuditWorkspacePaint;paint.ready=false;requestAnimationFrame(()=>requestAnimationFrame(()=>{paint.ready=true;}));return {armed:true};})())".into(),deadline).map(|_| ())
            },
        )?;
        if state["scrollWidth"].as_u64().unwrap_or(u64::MAX) > state["width"].as_u64().unwrap_or(0)
        {
            return Err(format!("workspace overflow: {page}"));
        }
        validate_workspace_title(&state, size)?;
        if page == "memory" {
            state["physicsObservation"] = observe_graph_rest(window, deadline)?;
            state["labelGeometryObservation"] =
                collect_script(window, LABEL_GEOMETRY_COLLECT.into(), deadline)?;
        }
        publish(
            output,
            &format!("workspace-{page}-{size}.png"),
            &snapshot(window, deadline)?,
            deadline,
        )?;
        if page == "memory" {
            collect_script(window,r#"JSON.stringify((()=>{
                document.querySelector('#knowledge-info').open=true;
                const button=document.querySelector('#knowledge-quality-run');
                if(!button||button.disabled)throw new Error('quality control unavailable');
                button.click();return {requested:true};})())"#.into(),deadline)?;
            let quality=loop {
                let state=collect_script(window,r#"JSON.stringify((()=>{
                    const panel=document.querySelector('#knowledge-quality-report');
                    return {state:panel.dataset.state,status:document.querySelector('#knowledge-quality-status').textContent,
                        hidden:panel.hidden,width:innerWidth,scrollWidth:document.documentElement.scrollWidth};})())"#.into(),deadline)?;
                if state["state"]=="complete" {break state;}
                if state["state"]=="unavailable" {return Err(format!("quality inspection unavailable: {state}"));}
                live(deadline)?;std::thread::sleep(Duration::from_millis(100));
            };
            if quality["hidden"]!=false || quality["width"]!=quality["scrollWidth"] {
                return Err("quality report hidden or horizontal overflow".into());
            }
            collect_script(window,r#"JSON.stringify((()=>{
                const box=document.querySelector('.knowledge-info .knowledge-scope-block');
                box.scrollTop=document.querySelector('#knowledge-quality').offsetTop-box.offsetTop;
                window.__aldenQualityPaint=false;requestAnimationFrame(()=>requestAnimationFrame(()=>{window.__aldenQualityPaint=true;}));
                return {scrolled:true};})())"#.into(),deadline)?;
            loop {
                let state=collect_script(window,"JSON.stringify({ready:window.__aldenQualityPaint===true})".into(),deadline)?;
                if state["ready"]==true {break;}live(deadline)?;std::thread::sleep(Duration::from_millis(25));
            }
            publish(output,&format!("knowledge-quality-{size}.png"),&snapshot(window,deadline)?,deadline)?;
            collect_script(window,"JSON.stringify((()=>{document.querySelector('#knowledge-info').open=false;return {closed:true};})())".into(),deadline)?;
            state["qualityObservation"]=quality;
        }
        if page == "history" {
            collect_script(window, r#"JSON.stringify((()=>{
              const schedule=document.querySelector('.collection-schedule');
              const editor=schedule?.querySelector('.collection-interval');if(editor)editor.open=true;
              schedule?.scrollIntoView({block:'start'});return {ready:!!schedule};
            })())"#.into(), deadline)?;
            std::thread::sleep(Duration::from_millis(100));
            publish(
                output,
                &format!("collection-schedule-{size}.png"),
                &snapshot(window, deadline)?,
                deadline,
            )?;
            let source_control = collect_script(
                window,
                r#"JSON.stringify((()=>{
              const source=document.querySelector('.collection-source-toggle:not([hidden])');
              const editor=source?.closest('details');if(editor)editor.open=true;
              source?.closest('li')?.scrollIntoView({block:'center'});return {ready:!!source};
            })())"#
                    .into(),
                deadline,
            )?;
            if source_control["ready"].as_bool().unwrap_or(false) {
                std::thread::sleep(Duration::from_millis(100));
                publish(
                    output,
                    &format!("collection-source-{size}.png"),
                    &snapshot(window, deadline)?,
                    deadline,
                )?;
            }
            collect_script(window, r#"JSON.stringify((()=>{
              for(const editor of document.querySelectorAll('.collection-schedule .collection-interval'))editor.open=false;
              document.querySelector('#settings-page-history .settings-page-heading')?.scrollIntoView({block:'start'});
              return {restored:true};
            })())"#.into(), deadline)?;
        }
        if page == "settings" {
            collect_script(window,"JSON.stringify((()=>{const p=document.querySelector('#routed-model-picker');p?.scrollIntoView({block:'center'});if(p)p.open=true;return {opened:!!p};})())".into(),deadline)?;
            let ready_by = Instant::now() + Duration::from_secs(5);
            loop {
                let models = collect_script(window,"JSON.stringify({count:document.querySelectorAll('#routed-model-list [data-route-model]').length})".into(),deadline)?;
                if models["count"].as_u64().unwrap_or(0) > 0 {
                    break;
                }
                if Instant::now() >= ready_by {
                    return Err("native model catalog did not settle".into());
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            publish(
                output,
                &format!("alden-model-menu-{size}.png"),
                &snapshot(window, deadline)?,
                deadline,
            )?;
            collect_script(window,"JSON.stringify((()=>{const p=document.querySelector('#routed-model-picker');if(p)p.open=false;return {closed:true};})())".into(),deadline)?;
        }
        states.push(state);
    }
    graph_step(window, "memory-page", deadline)?;
    Ok(states)
}

// Audit-only DOM measurements. Never emit label text, record IDs or bodies, and
// never run this from a frame loop. Element boxes are not font-ink geometry.
const LABEL_GEOMETRY_COLLECT: &str = r#"JSON.stringify((()=>{
  const canvas=document.querySelector('#knowledge-graph-canvas');
  const area=canvas?.getBoundingClientRect();
  const all=[...document.querySelectorAll('.knowledge-node-label')];
  const boxes=all.slice(0,24).map((el,index)=>{
    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
    return {index,positioned:!!el.style.transform,visible:!el.hidden&&s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0,
      focused:el.dataset.focused==='true',view:el.dataset.view??null,
      left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height,
      fontSize:s.fontSize,lineHeight:s.lineHeight,fontWeight:s.fontWeight};
  });
  const visible=boxes.filter(b=>b.visible);let overlapPairs=0,clipped=0;
  for(let a=0;a<visible.length;a++){
    const r=visible[a];
    if(area&&(r.left<area.left-.5||r.top<area.top-.5||r.right>area.right+.5||r.bottom>area.bottom+.5))clipped++;
    for(let b=a+1;b<visible.length;b++){
      const q=visible[b];
      if(Math.min(r.right,q.right)-Math.max(r.left,q.left)>.5&&Math.min(r.bottom,q.bottom)-Math.max(r.top,q.top)>.5)overlapPairs++;
    }
  }
  return {scope:'audit-only bounded element rectangles; no label text/IDs or physical/font-ink proof',
    totalLabels:all.length,sampledLabels:boxes.length,truncated:all.length>24,visibleLabels:visible.length,
    unpositionedLabels:boxes.filter(b=>!b.positioned).length,
    visibleUnpositionedLabels:visible.filter(b=>!b.positioned).length,
    framePending:window.__knowledgeRenderDiagnostics?.pendingFrame??null,
    overlapPairs,clippedLabels:clipped,boxes,devicePixelRatio};
})())"#;

const GRAPH_COLLECT: &str = r#"JSON.stringify((() => {
  const d=window.__knowledgeRenderDiagnostics, p=window.__knowledgeRenderPause;
  const c=document.querySelector('#knowledge-graph-canvas'), r=c?.getBoundingClientRect();
  return {ready:!!d, width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
    documentVisibility:document.visibilityState,documentFocused:document.hasFocus(),
    scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,
    settingsState:document.getElementById('app')?.dataset.state,settingsPage:document.querySelector('.settings-shell')?.dataset.settingsPage,
    graphPending:document.querySelector('#knowledge-filter-form')?.getAttribute('aria-busy')==='true'||document.querySelector('#knowledge-summary')?.textContent==='불러오는 중',
    renderCount:d?.renderCount??0,loop:d?{running:d.running,pendingFrame:d.pendingFrame}:null,
    graphRead:d?d.graphRead:null, navigation:d?{focused:d.focused,focusId:d.focusId,source:d.source,focusSlot:d.focusSlot,hops:d.hops,canGoBack:d.canGoBack,targets:d.targets,nodeCount:d.nodeCount,edgeCount:d.edgeCount,overviewLimit:d.overviewLimit,overviewOffset:d.overviewOffset,hasMoreContexts:d.hasMoreContexts}:null,
    regions:d?.regions??null,synapses:d?.synapses??null,
    motion:d?.motion??null,geometry:d?{detailMode:d.detailMode,neuronGlyphs:d.neuronGlyphs,orbGlyphs:d.orbCount,geometries:d.geometries,drawCalls:d.drawCalls}:null,
    depthLegend:(()=>{const e=document.querySelector('.knowledge-depth-legend');const r=e?.getBoundingClientRect();return e?{hidden:e.hidden,entries:e.children.length,left:r.left,top:r.top,width:r.width,height:r.height,scrollWidth:e.scrollWidth}:null;})(),
    nodeActivity:d?.nodeActivity??null,activityJournal:d?.activityJournal??null,
    canvas:r?{width:r.width,height:r.height,contextLost:c.getContext('webgl2')?.isContextLost()??null}:null,
    notePane:(()=>{const n=document.querySelector('#knowledge-note-pane');const b=n?.getBoundingClientRect();return n&&b?{hidden:n.hidden,x:b.x,y:b.y,width:b.width,height:b.height,scrollWidth:n.scrollWidth,focusState:n.dataset.focusState,nodeId:n.dataset.nodeId,bodyChars:document.querySelector('#knowledge-node-body')?.textContent?.length??0}:null;})(),
    backDisabled:document.querySelector('#knowledge-back')?.disabled,
    overviewDisabled:document.querySelector('#knowledge-overview')?.disabled,
    expandDisabled:document.querySelector('#knowledge-expand-hop')?.disabled,
    clearedDetail:document.querySelector('#knowledge-relations')?.children.length===0
      &&document.querySelector('#knowledge-focus-title')?.textContent==='항목을 선택하면 관련 정보를 보여드립니다.',
    accessibleNodes:document.querySelectorAll('.knowledge-a11y-node').length,
    labelGeometryObservation:JSON.parse(__AUDIT_LABEL_GEOMETRY__),
    pause:p?{state:p.state,eventAtMs:p.eventAtMs,rendersAfterEvent:p.rendersAfterEvent}:null};
})())"#;

fn graph_collect_script() -> String {
    GRAPH_COLLECT.replace("__AUDIT_LABEL_GEOMETRY__", LABEL_GEOMETRY_COLLECT)
}

fn observe_connections(window: &tauri::WebviewWindow, deadline: Instant) -> Result<Value, String> {
    let bound = deadline.min(Instant::now() + Duration::from_secs(3));
    loop {
        let state = collect_script(window, graph_collect_script(), deadline)?;
        if state["motion"]["camera"] == false || Instant::now() >= bound {
            return Ok(state);
        }
        live(deadline)?;
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn observe_graph_rest(window: &tauri::WebviewWindow, deadline: Instant) -> Result<Value, String> {
    let script = r#"JSON.stringify((()=>{const d=window.__knowledgeRenderDiagnostics;
      return {observedAtMs:performance.now(),renderCount:d?.renderCount??0,
        displayedNodes:d?.nodeCount??0,physics:d?.physics??null,
        loop:d?{running:d.running,pendingFrame:d.pendingFrame}:null,
        motion:d?.motion??null,regions:d?.regions??null,synapses:d?.synapses??null,
        semantic:d?.semanticLayout??null,
        affinity:d?.graphRead?.affinity??null};})())"#;
    let before = collect_script(window, script.into(), deadline)?;
    let bound = deadline.min(Instant::now() + Duration::from_secs(15));
    let rested = loop {
        let state = collect_script(window, script.into(), deadline)?;
        let worker_ready = matches!(
            state["semantic"]["worker"]["state"].as_str(),
            Some("ready" | "cached")
        );
        let affinity_ready = matches!(
            state["affinity"]["state"].as_str(),
            Some("bounded_ready" | "bounded_partial" | "unavailable")
        );
        if (state["physics"]["moving"] == false
            && state["semantic"]["pendingTargets"] == 0
            && worker_ready
            && affinity_ready
            && state["loop"]["running"] == false
            && state["loop"]["pendingFrame"] == false)
            || Instant::now() >= bound
        {
            break state;
        }
        live(deadline)?;
        std::thread::sleep(Duration::from_millis(200));
    };
    live(deadline)?;
    std::thread::sleep(Duration::from_secs(2));
    let after = collect_script(window, script.into(), deadline)?;
    Ok(
        json!({"scope":"owned visible graph; observational only, not primary/GPU/battery or semantic proof",
        "before":before,"restObserved":rested["physics"]["moving"] == false,
        "layoutObserved":matches!(rested["semantic"]["worker"]["state"].as_str(),Some("ready" | "cached")) && rested["semantic"]["pendingTargets"] == 0,
        "affinityObserved":matches!(rested["affinity"]["state"].as_str(),Some("bounded_ready" | "bounded_partial")),
        "renderIdleObserved":rested["loop"]["running"] == false
            && rested["loop"]["pendingFrame"] == false
            && after["loop"]["running"] == false
            && after["loop"]["pendingFrame"] == false
            && rested["renderCount"] == after["renderCount"],
        "rested":rested,"afterTwoSeconds":after}),
    )
}

fn graph_step(
    window: &tauri::WebviewWindow,
    action: &str,
    deadline: Instant,
) -> Result<Value, String> {
    // Internal fixed actions only; no caller-selected script or selector.
    let focus = window.state::<AuditPolicy>().focus_node.clone();
    let targeted = focus
        .as_deref()
        .map(|id| serde_json::to_string(id).expect("string serializes"));
    let script = match (action, targeted) {
        ("first", Some(id)) => format!("(()=>{{const b=[...document.querySelectorAll('.knowledge-a11y-node')].find(n=>n.dataset.nodeId==={id});if(!b)throw Error('requested canonical note is not visible');b.click();}})()"),
        ("second", Some(id)) => format!("[...document.querySelectorAll('.knowledge-a11y-node')].find(n=>n.dataset.nodeId!=={id})?.click()"),
        (action, _) => match action {
        "memory-page" if focus.as_deref().is_some_and(|id| id.starts_with("osk:")) => "(()=>{document.querySelector('#settings-tab-memory')?.click();const source=document.querySelector('#knowledge-project');if(source){source.value='legacy';source.dispatchEvent(new Event('change'));}})()",
        "memory-page" => "document.querySelector('#settings-tab-memory')?.click()",
        "first" => "document.querySelectorAll('.knowledge-a11y-node')[0]?.click()",
        "second" => "document.querySelectorAll('.knowledge-a11y-node')[1]?.click()",
        "expand" => "document.querySelector('#knowledge-expand-hop')?.click()",
        "back" => "document.querySelector('#knowledge-back')?.click()",
        "overview" => "document.querySelector('#knowledge-overview')?.click()",
        "scroll" => {
            "document.querySelector('#settings-knowledge-card')?.scrollIntoView({block:'center'})"
        }
        _ => return Err("unknown internal graph action".into()),
        }.to_string(),
    };
    collect_script(window, format!("{script};{}", graph_collect_script()), deadline)?;
    std::thread::sleep(Duration::from_millis(250));
    // Match bounded source-read budgets; readiness is observed, never forced.
    let settle = deadline.min(Instant::now() + Duration::from_secs(25));
    loop {
        let state = collect_script(window, graph_collect_script(), deadline)?;
        let graph_pending = collect_script(window,"JSON.stringify({pending:document.querySelector('#knowledge-filter-form')?.getAttribute('aria-busy')==='true'||document.querySelector('#knowledge-summary')?.textContent==='불러오는 중'})".into(),deadline)?;
        if graph_pending["pending"] != true
            && state["graphRead"]["navigationPending"] != true
            && (state["notePane"]["hidden"] != false
                || state["notePane"]["focusState"] != "loading")
        {
            return Ok(state);
        }
        if Instant::now() >= settle {
            return Err(format!(
                "graph action {action} did not settle: pending={}, focusState={}, remainingDeadlineMs={}",
                graph_pending["pending"], state["notePane"]["focusState"], deadline.saturating_duration_since(Instant::now()).as_millis()
            ));
        }
        live(deadline)?;
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn native_settings_size(window: &tauri::WebviewWindow, deadline: Instant) -> Result<Value, String> {
    live(deadline)?;
    let (tx, rx) = mpsc::sync_channel(1);
    let target = window.clone();
    window
        .with_webview(move |platform| {
            let result = live(deadline).and_then(|_| {
                let native = target
                    .ns_window()
                    .map_err(|_| "settings native window missing")?;
                let native = unsafe { &*native.cast::<NSWindow>() };
                let content = native
                    .contentView()
                    .ok_or("settings content view missing")?
                    .frame();
                let webview = unsafe { &*platform.inner().cast::<WKWebView>() };
                let bounds = webview.bounds();
                let layout = native.contentLayoutRect();
                Ok(
                    json!({"contentWidth":content.size.width,"contentHeight":content.size.height,
                "layoutWidth":layout.size.width,"layoutHeight":layout.size.height,
                "webviewWidth":bounds.size.width,"webviewHeight":bounds.size.height}),
                )
            });
            let _ = tx.try_send(result);
        })
        .map_err(|_| "settings size dispatch failed")?;
    receive(rx, deadline)
}

fn audit_settings(
    app: &tauri::AppHandle,
    output: &Output,
    deadline: Instant,
    workspaces: bool,
) -> Result<Value, String> {
    use super::workspace_visibility::{WorkspaceCounters, WorkspaceNote};
    let window = app
        .get_webview_window("settings")
        .ok_or("settings window missing")?;
    // Only this short-lived audit instance: keep its settings renderer
    // unoccluded without activating the app or changing the user's key window.
    live(deadline)?;
    let target = window.clone();
    let (tx, rx) = mpsc::sync_channel(1);
    window
        .run_on_main_thread(move || {
            let result = live(deadline).and_then(|_| {
                let native = target
                    .ns_window()
                    .map_err(|_| "settings native window missing")?;
                live(deadline)?;
                unsafe {
                    (&*native.cast::<NSWindow>()).setLevel(NSFloatingWindowLevel);
                    // This audit uses fixed JS actions. Physical mouse input
                    // must continue to the user's existing app, not rotate our
                    // floating graph between the saved navigation snapshots.
                    (&*native.cast::<NSWindow>()).setIgnoresMouseEvents(true);
                }
                Ok(())
            });
            let _ = tx.try_send(result);
        })
        .map_err(|_| "settings level dispatch failed")?;
    receive(rx, deadline)?;
    visibility(&window, true, deadline)?;
    loop {
        let mounted = collect_script(
            &window,
            "JSON.stringify({mounted:!!document.querySelector('#settings-tab-memory')})".into(),
            deadline,
        )?;
        if mounted["mounted"] == true {
            break;
        }
        live(deadline)?;
        std::thread::sleep(Duration::from_millis(50));
    }
    graph_step(&window, "memory-page", deadline)?;
    let initial = loop {
        let state = collect_script(&window, graph_collect_script(), deadline)?;
        if rendered_since(&state, 0) && state["graphPending"] != true {
            break state;
        }
        live(deadline)?;
        std::thread::sleep(Duration::from_millis(50));
    };
    if initial["accessibleNodes"].as_u64().unwrap_or(0) < 2
        || initial["navigation"]["focused"] != false
    {
        return Err("persisted graph has insufficient nodes or invalid overview".into());
    }
    // A debug checkout can observe an external test importer in an isolated
    // state root. The native audit itself remains strictly read-only.
    if cfg!(debug_assertions) {
        if let Ok(expected) = std::env::var("ALDEN_AUDIT_EXPECT_RUN") {
            if expected.is_empty() || expected.len() > 128 {
                return Err("activity test run ID is invalid".into());
            }
            publish(output, "activity-ready.json", b"{\"ready\":true}", deadline)?;
            let waited_by = deadline.min(Instant::now() + Duration::from_secs(15));
            loop {
                let state = collect_script(&window, graph_collect_script(), waited_by)?;
                if state["nodeActivity"]["last"]["run_id"] == expected
                    && state["nodeActivity"]["last"]["success"] == true
                    && state["nodeActivity"]["active"].as_u64().unwrap_or(0) > 0
                    && state["graphPending"] != true
                {
                    break;
                }
                live(waited_by)?;
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
    graph_step(&window, "scroll", deadline)?;
    publish(
        output,
        "alden-settings-default.png",
        &snapshot(&window, deadline)?,
        deadline,
    )?;
    // The native capture flushes layout/ResizeObserver work. Navigation must
    // restore the pose actually shown at click time, not a pre-layout sample.
    let initial = collect_script(&window, graph_collect_script(), deadline)?;
    let overview_more = if initial["expandDisabled"] == false {
        let more = graph_step(&window, "expand", deadline)?;
        if more["navigation"]["focused"] != false
            || more["navigation"]["overviewLimit"]
                .as_u64()
                .unwrap_or(u64::MAX)
                > 2048
            || more["navigation"]["overviewOffset"].as_u64().unwrap_or(0)
                <= initial["navigation"]["overviewOffset"]
                    .as_u64()
                    .unwrap_or(0)
            || more["navigation"]["nodeCount"].as_u64().unwrap_or(u64::MAX) > 2048
        {
            return Err("native overview expansion did not expose bounded extra contexts".into());
        }
        let restored = graph_step(&window, "back", deadline)?;
        if restored["navigation"] != initial["navigation"] {
            return Err(format!("native overview expansion did not restore prior context selection: before={}, restored={}",
                initial["navigation"], restored["navigation"]));
        }
        more
    } else {
        Value::Null
    };
    let first = graph_step(&window, "first", deadline)?;
    if window.state::<AuditPolicy>().focus_node.is_some()
        && (first["notePane"]["focusState"] != "ready"
            || first["notePane"]["bodyChars"].as_u64().unwrap_or(0) == 0)
    {
        return Err(format!(
            "requested canonical note did not return its saved body: {}",
            first["notePane"]
        ));
    }
    if first["notePane"]["hidden"] != false
        || first["notePane"]["width"].as_f64().unwrap_or(0.0) <= 0.0
        || first["navigation"]["nodeCount"] != initial["navigation"]["nodeCount"]
    {
        return Err("native note reader lost its graph or did not open".into());
    }
    if first["navigation"]["source"] == "collection"
        && (first["nodeActivity"]["last"]["kind"] != "read"
            || first["nodeActivity"]["last"]["document_id"] != first["notePane"]["nodeId"]
            || first["nodeActivity"]["last"]["success"] != true)
    {
        return Err("native source detail did not emit its confirmed read receipt".into());
    }
    publish(
        output,
        "alden-note-default.png",
        &snapshot(&window, deadline)?,
        deadline,
    )?;
    let expanded = graph_step(&window, "expand", deadline)?;
    let expanded_capture = observe_connections(&window, deadline)?;
    publish(output, "alden-connections-default.png", &snapshot(&window, deadline)?, deadline)?;
    let second = graph_step(&window, "second", deadline)?;
    // A real isolated source has no second node to select. That action is a
    // no-op and must not invent an extra history entry or an extra Back click.
    let secondary_required = expanded["navigation"]["nodeCount"]
        .as_u64()
        .is_some_and(|count| count > 1);
    let back_expanded = if secondary_required {
        Some(graph_step(&window, "back", deadline)?)
    } else {
        None
    };
    let back_first = graph_step(&window, "back", deadline)?;
    let back_overview = graph_step(&window, "back", deadline)?;
    if first["navigation"]["focusSlot"].as_i64().unwrap_or(-1) < 0
        || first["navigation"]["hops"] != 0
        || expanded["navigation"]["hops"]
            != if initial["navigation"]["source"] == "collection" {
                1
            } else {
                2
            }
        || second["navigation"]["focusSlot"].as_i64().unwrap_or(-1) < 0
        || (secondary_required && second["navigation"]["focusId"] == first["navigation"]["focusId"])
        || (!secondary_required && second["navigation"] != expanded["navigation"])
        || back_expanded
            .as_ref()
            .is_some_and(|state| state["navigation"] != expanded["navigation"])
        || back_first["navigation"] != first["navigation"]
        || back_overview["navigation"] != initial["navigation"]
        || back_overview["backDisabled"] != true
        || back_overview["clearedDetail"] != true
    {
        return Err(format!(
            "native graph back navigation did not restore view and targets: {}",
            json!({
                "initial":initial["navigation"],"first":first["navigation"],"expanded":expanded["navigation"],
                "second":second["navigation"],"backExpanded":back_expanded.as_ref().map(|state| &state["navigation"]),
                "backFirst":back_first["navigation"],"backOverview":back_overview["navigation"],
                "backDisabled":back_overview["backDisabled"],"clearedDetail":back_overview["clearedDetail"]
                ,"read":back_overview["graphRead"],"graphPending":back_overview["graphPending"]
            })
        ));
    }
    graph_step(&window, "first", deadline)?;
    let reset = graph_step(&window, "overview", deadline)?;
    let workspace_default = if workspaces {
        capture_workspaces(&window, output, "default", deadline)?
    } else {
        Vec::new()
    };
    if reset["navigation"]["focused"] != false
        || reset["clearedDetail"] != true
        || reset["overviewDisabled"] != true
    {
        return Err("native graph overview did not clear selection".into());
    }
    live(deadline)?;
    let resize_window = window.clone();
    let (tx, rx) = mpsc::sync_channel(1);
    window
        .run_on_main_thread(move || {
            let result = live(deadline).and_then(|_| {
                let native = resize_window
                    .ns_window()
                    .map_err(|_| "settings native window missing")?;
                // Tao set_size requeues through GCD even on main. Mutate this
                // owned NSWindow synchronously inside the deadline gate.
                live(deadline)?;
                unsafe {
                    (&*native.cast::<NSWindow>()).setContentSize(NSSize::new(640.0, 680.0));
                }
                Ok(())
            });
            let _ = tx.try_send(result);
        })
        .map_err(|_| "settings resize dispatch failed")?;
    receive(rx, deadline)?;
    let first_layout = native_settings_size(&window, deadline)?;
    publish(
        output,
        "settings-layout.json",
        &serde_json::to_vec_pretty(&first_layout).map_err(|_| "settings size JSON failed")?,
        deadline,
    )?;
    let native_compact = {
        let settled_by = deadline.min(Instant::now() + Duration::from_secs(2));
        loop {
            let compact = collect_script(&window, graph_collect_script(), settled_by)?;
            let native = native_settings_size(&window, settled_by)?;
            if compact["width"].as_f64() == native["layoutWidth"].as_f64()
                && compact["height"].as_f64() == native["layoutHeight"].as_f64()
            {
                live(settled_by)?;
                break native;
            }
            if Instant::now() >= settled_by {
                return Err(format!(
                    "settings viewport did not settle: {compact}; native: {native}"
                ));
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    };
    visibility(&window, true, deadline)?;
    let compact = graph_step(&window, "scroll", deadline)?;
    if native_compact["contentWidth"].as_f64() != Some(640.0)
        || native_compact["contentHeight"].as_f64() != Some(680.0)
        || compact["width"].as_f64() != native_compact["layoutWidth"].as_f64()
        || compact["height"].as_f64() != native_compact["layoutHeight"].as_f64()
        || compact["scrollWidth"].as_u64().unwrap_or(u64::MAX) > 640
        || compact["canvas"]["contextLost"] != false
    {
        return Err(format!(
            "compact native settings overflow or context loss: {compact}; native: {native_compact}"
        ));
    }
    publish(
        output,
        "alden-settings-compact.png",
        &snapshot(&window, deadline)?,
        deadline,
    )?;
    let compact_note = graph_step(&window, "first", deadline)?;
    if compact_note["notePane"]["hidden"] != false
        || compact_note["scrollWidth"].as_u64().unwrap_or(u64::MAX) > 640
    {
        return Err("native compact note reader is hidden or overflows".into());
    }
    publish(
        output,
        "alden-note-compact.png",
        &snapshot(&window, deadline)?,
        deadline,
    )?;
    let compact_connections = graph_step(&window, "expand", deadline)?;
    let compact_connections_capture = observe_connections(&window, deadline)?;
    publish(output, "alden-connections-compact.png", &snapshot(&window, deadline)?, deadline)?;
    graph_step(&window, "overview", deadline)?;
    let workspace_compact = if workspaces {
        capture_workspaces(&window, output, "compact", deadline)?
    } else {
        Vec::new()
    };
    publish(output, "workspace-readback.json", &serde_json::to_vec_pretty(&json!({
        "schema":1,"scope":"owned read-only WKWebView with persisted application data; not the primary process, physical voice, lock or production",
        "workspaceDefault":workspace_default,"workspaceCompact":workspace_compact,
        "navigation":{"initial":initial["navigation"],"expanded":expanded["navigation"],"backOverview":back_overview["navigation"]},
        "connectionsDefault":expanded_capture,"connectionsCompact":compact_connections_capture,
        "compactConnectionsNavigation":compact_connections["navigation"]
    })).map_err(|_| "workspace readback JSON failed")?,deadline)?;
    let counters = app.state::<WorkspaceCounters>();
    let mut hidden_samples = Vec::new();
    let mut last_hidden_count = count(&compact);
    visibility(&window, false, deadline)?;
    for note in WorkspaceNote::ALL {
        // orderOut's DOM event is asynchronous. Do not measure a resume from
        // a baseline taken before the renderer acknowledged the previous hide.
        let hide_by = Instant::now() + Duration::from_secs(2);
        let hidden_baseline = loop {
            let state = collect_script(&window, graph_collect_script(), deadline)?;
            if state["pause"]["state"] == "hidden"
                && state["loop"]["running"] == false
                && state["loop"]["pendingFrame"] == false
                && state["activityJournal"]["active"] != true
            {
                break state;
            }
            if Instant::now() >= hide_by {
                return Err("native graph hide was not acknowledged before restore".into());
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        let show_baseline = count(&hidden_baseline);
        visibility(&window, true, deadline)?;
        let resume_deadline = Instant::now() + Duration::from_secs(2);
        let mut visible_since = None;
        let before = loop {
            let sample = collect_script(&window, graph_collect_script(), deadline)?;
            // A queued DOM hide can follow the first resumed frame. Observe a
            // stable active consumer before measuring a new native hide note.
            if resumed_graph(&sample, show_baseline) && window.is_visible().ok() == Some(true) {
                let started = visible_since.get_or_insert_with(Instant::now);
                if started.elapsed() >= Duration::from_millis(100) {
                    break sample;
                }
            } else {
                visible_since = None;
            }
            if Instant::now() >= resume_deadline {
                break sample;
            }
            std::thread::sleep(Duration::from_millis(25));
        };
        if !resumed_graph(&before, show_baseline)
            || visible_since.is_none_or(|at| at.elapsed() < Duration::from_millis(100))
            || window.is_visible().ok() != Some(true)
        {
            return Err(format!(
                "settings graph did not draw before notification: {before}"
            ));
        }
        let previous = before["pause"]["eventAtMs"].as_f64().unwrap_or(-1.0);
        let seen = counters.count(note);
        let started = Instant::now();
        let (tx, rx) = mpsc::sync_channel(1);
        window
            .run_on_main_thread(move || {
                let result = live(deadline)
                    .and_then(|_| super::workspace_visibility::post_process_local_audit_note(note));
                let _ = tx.try_send(result);
            })
            .map_err(|_| "settings note dispatch failed")?;
        receive(rx, deadline)?;
        let stopped_by = Instant::now() + Duration::from_secs(2);
        let stopped = loop {
            let state = collect_script(&window, graph_collect_script(), deadline)?;
            if counters.count(note) > seen
                && fresh_pause(&state, previous)
                && window.is_visible().ok() == Some(false)
            {
                break state;
            }
            if Instant::now() >= stopped_by {
                return Err(format!("settings notification {} did not settle: observedCount={}, previousPause={}, nativeVisible={}, state={}",
                    note.code(),counters.count(note)-seen,previous,window.is_visible().ok()==Some(true),state));
            }
            live(deadline)?;
            std::thread::sleep(Duration::from_millis(10));
        };
        let latency = started.elapsed().as_secs_f64() * 1000.0;
        std::thread::sleep(Duration::from_millis(350));
        let hidden = collect_script(&window, graph_collect_script(), deadline)?;
        if count(&hidden) != count(&stopped)
            || hidden["pause"]["rendersAfterEvent"] != 0
            || hidden["nodeActivity"]["active"] != 0
            || hidden["activityJournal"]["active"] != false
        {
            return Err("settings graph rendered while hidden".into());
        }
        last_hidden_count = count(&hidden);
        hidden_samples.push(json!({"signal":note.code(),"scope":"process-local synthetic notification; no physical sleep/session/lock test","postToStoppedObservationUpperBoundMs":latency,"hiddenRenderDelta":0,"sampleMs":350}));
    }
    visibility(&window, true, deadline)?;
    std::thread::sleep(Duration::from_millis(250));
    let reopened = collect_script(&window, graph_collect_script(), deadline)?;
    if !rendered_since(&reopened, last_hidden_count) {
        return Err("settings graph did not resume".into());
    }
    visibility(&window, false, deadline)?;
    Ok(
        json!({"initial":initial,"first":first,"expanded":expanded,"second":second,"backExpanded":back_expanded,
        "backFirst":back_first,"backOverview":back_overview,"reset":reset,"compact":compact,"nativeCompact":native_compact,"workspaceSignals":hidden_samples,"reopened":reopened,
        "workspaceDefault":workspace_default,"workspaceCompact":workspace_compact,
        "overviewMore":overview_more,"compactNote":compact_note,
        "scope":"real persisted graph and renderer/navigation in owned floating audit window without key-window activation; runtime snapshot/GraphRAG focus lookup intentionally unavailable; captures private"}),
    )
}

fn entity_wait(window: &tauri::WebviewWindow, script: &str, deadline: Instant) -> Result<Value, String> {
    loop {
        live(deadline)?;
        let state = collect_script(window, script.into(), deadline)?;
        if state["ready"] == true { return Ok(state); }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn audit_entity(app: &tauri::AppHandle, output: &Output, deadline: Instant) -> Result<Value, String> {
    if app.get_webview_window("settings").is_some() { return Err("entity audit refuses an existing shared webview".into()); }
    let window = tauri::WebviewWindowBuilder::new(app, "settings",
        tauri::WebviewUrl::App("index.html?view=settings".into()))
        .incognito(true).title("Alden · owned entity review audit")
        .inner_size(1200.0, 800.0).visible(true).build().map_err(|e| e.to_string())?;
    entity_wait(&window, r#"JSON.stringify({ready:document.getElementById('app')?.dataset.state==='ready'
        && !!document.querySelector('#settings-tab-history')})"#, deadline)?;
    let mut states = Vec::new();
    for size in ["default", "compact"] {
        if size == "compact" {
            let target = window.clone(); let (tx, rx) = mpsc::sync_channel(1);
            window.run_on_main_thread(move || {
                let result = (|| { let native=target.ns_window().map_err(|_| "entity audit native window missing")?;
                    unsafe { (&*native.cast::<NSWindow>()).setContentSize(NSSize::new(640.0,680.0)); }
                    Ok::<(),String>(()) })();
                let _ = tx.try_send(result);
            }).map_err(|_| "entity resize dispatch failed")?;
            receive(rx,deadline)?;
        }
        collect_script(&window,r#"JSON.stringify((()=>{
          document.querySelector('#settings-tab-history').click();
          const s=document.querySelector('.entity-resolution');s.open=true;s.dispatchEvent(new Event('toggle'));
          return {opened:true};})())"#.into(),deadline)?;
        entity_wait(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          return {ready:!!s&&[...s.querySelector('select').options].some(o=>o.value==='audit')};})())"#,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution'),p=s.querySelector('select');
          p.value='audit';p.dispatchEvent(new Event('change'));return {selected:true};})())"#.into(),deadline)?;
        entity_wait(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution'),
          b=[...s.querySelectorAll('button')].find(b=>b.textContent==='자료 찾기');
          return {ready:!!b&&!b.disabled};})())"#,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          [...s.querySelectorAll('button')].find(b=>b.textContent==='자료 찾기').click();return {requested:true};})())"#.into(),deadline)?;
        entity_wait(&window,r#"JSON.stringify({ready:document.querySelectorAll('.entity-resolution-list input').length===3})"#,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          for(const label of s.querySelectorAll('.entity-resolution-list label')){
            if(['a','b'].includes(label.childNodes[1]?.textContent)){const b=label.querySelector('input');b.click();}}
          const t=s.querySelector('textarea');t.value='Reviewed two owned synthetic copies';t.dispatchEvent(new Event('input'));
          [...s.querySelectorAll('button')].find(b=>b.textContent==='선택한 자료 비교').click();return {compared:true};})())"#.into(),deadline)?;
        let compared = entity_wait(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          return {ready:s.querySelectorAll('.entity-resolution-comparison pre').length===2
            &&!s.querySelector('.entity-resolution-approval input').disabled,
            records:s.querySelectorAll('.entity-resolution-comparison pre').length,
            width:innerWidth,scrollWidth:document.documentElement.scrollWidth};})())"#,deadline)?;
        publish(output,&format!("entity-compare-{size}.png"),&snapshot(&window,deadline)?,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          s.querySelector('.entity-resolution-approval input').click();
          [...s.querySelectorAll('button')].find(b=>b.textContent==='같은 지식으로 묶기').click();
          return {applied:true};})())"#.into(),deadline)?;
        entity_wait(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          return {ready:[...s.querySelectorAll('.entity-resolution-history button')].some(b=>b.textContent==='되돌리기')};})())"#,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{document.querySelector('#settings-tab-memory').click();
          const p=document.querySelector('#knowledge-project');p.value='project:audit';p.dispatchEvent(new Event('change'));
          return {graph:true};})())"#.into(),deadline)?;
        let applied = entity_wait(&window,r#"JSON.stringify((()=>{const d=window.__knowledgeRenderDiagnostics;
          return {ready:d?.graphRead?.nodes===2&&!d.graphPending,nodes:d?.graphRead?.nodes,
            edges:d?.edgeCount,width:innerWidth,scrollWidth:document.documentElement.scrollWidth};})())"#,deadline)?;
        publish(output,&format!("entity-applied-{size}.png"),&snapshot(&window,deadline)?,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{document.querySelector('#settings-tab-history').click();
          const s=document.querySelector('.entity-resolution');s.open=true;
          [...s.querySelectorAll('.entity-resolution-history button')].find(b=>b.textContent==='되돌리기').click();
          return {revertReview:true};})())"#.into(),deadline)?;
        entity_wait(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          return {ready:[...s.querySelectorAll('.entity-resolution-history button')].some(b=>b.textContent==='되돌리기 확인')};})())"#,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          [...s.querySelectorAll('.entity-resolution-history button')].find(b=>b.textContent==='되돌리기 확인').click();
          return {reverted:true};})())"#.into(),deadline)?;
        entity_wait(&window,r#"JSON.stringify((()=>{const s=document.querySelector('.entity-resolution');
          return {ready:!s.querySelector('select').disabled&&!s.querySelectorAll('.entity-resolution-history button').length};})())"#,deadline)?;
        collect_script(&window,r#"JSON.stringify((()=>{document.querySelector('#settings-tab-memory').click();return {graph:true};})())"#.into(),deadline)?;
        let reverted = entity_wait(&window,r#"JSON.stringify((()=>{const d=window.__knowledgeRenderDiagnostics;
          return {ready:d?.graphRead?.nodes===3&&!d.graphPending,nodes:d?.graphRead?.nodes,
            edges:d?.edgeCount,width:innerWidth,scrollWidth:document.documentElement.scrollWidth};})())"#,deadline)?;
        publish(output,&format!("entity-reverted-{size}.png"),&snapshot(&window,deadline)?,deadline)?;
        if [compared.clone(),applied.clone(),reverted.clone()].iter().any(|s|s["width"]!=s["scrollWidth"]) {
            return Err("entity workflow horizontal overflow".into());
        }
        states.push(json!({"size":size,"compared":compared,"applied":applied,"reverted":reverted}));
    }
    let preservation=app.state::<super::PythonBridge>().verify_entity_audit().map_err(|e|e.to_string())?;
    if preservation["ok"]!=true || preservation["active_groups"]!=0 || preservation["resolution_events"]!=4 {
        return Err("entity workflow source/revert verification failed".into());
    }
    Ok(json!({"states":states,"preservation":preservation,"incognito":true,
        "scope":"native controls with real CLI/SQLite over owned synthetic sources; human entity truth and production mutation unverified"}))
}

fn workspace_notification_audit(
    app: &tauri::AppHandle,
    window: &tauri::WebviewWindow,
    deadline: Instant,
) -> Result<Vec<Value>, String> {
    use super::workspace_visibility::{WorkspaceCounters, WorkspaceNote};
    let settings = app
        .get_webview_window("settings")
        .ok_or("settings native window missing")?;
    let counters = app
        .try_state::<WorkspaceCounters>()
        .ok_or("workspace observers not installed")?;
    let mut results = Vec::new();
    visibility(window, false, deadline)?;
    for note in WorkspaceNote::ALL {
        let show_baseline = count(&collect(window, deadline)?);
        visibility(window, true, deadline)?;
        visibility(&settings, true, deadline)?;
        std::thread::sleep(Duration::from_millis(250));
        if window.is_visible().ok() != Some(true) || settings.is_visible().ok() != Some(true) {
            return Err("workspace audit windows not visible before injection".into());
        }
        let prior = collect(window, deadline)?;
        if !rendered_since(&prior, show_baseline) {
            return Err("workspace audit renderer did not draw before injection".into());
        }
        let previous = prior["pause"]["eventAtMs"].as_f64().unwrap_or(-1.0);
        let seen = counters.count(note);
        live(deadline)?;
        let (tx, rx) = mpsc::sync_channel(1);
        let started = Instant::now();
        window
            .run_on_main_thread(move || {
                let result = live(deadline)
                    .and_then(|_| super::workspace_visibility::post_process_local_audit_note(note));
                let _ = tx.try_send(result);
            })
            .map_err(|_| "workspace audit dispatch failed")?;
        receive(rx, deadline)?;
        let stopped = loop {
            let state = collect(window, deadline)?;
            if counters.count(note) > seen
                && fresh_pause(&state, previous)
                && window.is_visible().ok() == Some(false)
                && settings.is_visible().ok() == Some(false)
            {
                break state;
            }
            live(deadline)?;
            std::thread::sleep(Duration::from_millis(10));
        };
        let stop_observed_ms = started.elapsed().as_secs_f64() * 1000.0;
        std::thread::sleep(Duration::from_millis(350));
        let hidden = collect(window, deadline)?;
        if count(&hidden) != count(&stopped) || hidden["pause"]["rendersAfterEvent"] != 0 {
            return Err("workspace notification left rendering active".into());
        }
        results.push(json!({"signal":note.code(),"scope":"process-local synthetic NSWorkspace notification; not physical sleep, switch or lock",
            "notificationCountDelta":counters.count(note)-seen,"bothNativeWindowsHidden":true,
            "postToStoppedObservationUpperBoundMs":stop_observed_ms,"hiddenSampleMs":350,
            "hiddenRenderDelta":count(&hidden)-count(&stopped),"pause":hidden["pause"],
            "settingsRenderScope":"native visibility only; backend-free settings renderer unexercised"}));
    }
    let hidden_count = count(&collect(window, deadline)?);
    visibility(window, true, deadline)?;
    std::thread::sleep(Duration::from_millis(250));
    let resumed = collect(window, deadline)?;
    if !rendered_since(&resumed, hidden_count) {
        return Err("renderer failed to reopen after workspace signals".into());
    }
    Ok(results)
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
    while !rendered_since(&before, 0) {
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
        if !rendered_since(&restored, count(&hidden)) {
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
    let workspace_signals = workspace_notification_audit(app, &window, deadline)?;
    visibility(&window, false, deadline)?;
    Ok(json!({"before":before,"restored":restored,"cycles":cycles,
        "nativeScaleFactor":initial_scale,"retina":retina,
        "workspaceSignals":workspace_signals,
        "monitorScaleFactors":monitors.iter().map(|monitor|monitor.scale_factor()).collect::<Vec<_>>()}))
}

fn audit_settings_page(label: &str, url: &tauri::Url) -> bool {
    label == "settings" && url.port().is_none() && url.path() == "/index.html"
        && url.query() == Some("view=settings")
        && ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
            || (matches!(url.scheme(), "http"|"https") && url.host_str() == Some("tauri.localhost")))
}

fn audit_assistant(app: &tauri::AppHandle, output: &Output, deadline: Instant, invalid_only: bool, loaded: &AtomicBool, asset: &AtomicBool, phase: &AtomicU8) -> Result<Value,String> {
    let window = app.get_webview_window("settings").ok_or("settings window missing")?;
    visibility(&window,true,deadline)?;
    let mount_deadline = deadline.min(Instant::now() + Duration::from_secs(30));
    // Do not evaluate JavaScript in WKWebView's initial about:blank while the
    // owned asset navigation is starting. Its callback can be lost on navigation.
    while !loaded.load(Ordering::Acquire) || !asset.load(Ordering::Acquire) {
        if Instant::now() >= mount_deadline {return Err(format!("assistant asset modules and composer did not finish loading: phase {}, asset {}",phase.load(Ordering::Acquire),asset.load(Ordering::Acquire)));}
        std::thread::sleep(Duration::from_millis(20));
    }
    // Reannounce the verified native visibility after the frontend installed
    // its lifecycle listeners and completed the initial status handshake.
    visibility(&window,true,deadline)?;
    let target = window.clone();
    let (tx, rx) = mpsc::sync_channel(1);
    window.run_on_main_thread(move || {
        let result = live(deadline).and_then(|_| {
            // This explicit input audit uses the production show/focus path on
            // only its own window. Never fake document visibility to admit input.
            super::set_window_visible(&target, true);
            target.set_focus().map_err(|_| "owned assistant window focus failed".into())
        });
        let _ = tx.try_send(result);
    }).map_err(|_| "assistant focus dispatch failed")?;
    receive(rx,deadline)?;
    let mut first_probe = true;
    loop {
        let s=collect_script(&window,r#"JSON.stringify({mounted:!!document.querySelector('#assistant-composer-text'),
            documentState:document.readyState,appState:document.querySelector('#app')?.dataset.state,
            voiceStart:!!document.querySelector('#voice-start'),reader:!!document.querySelector('#settings-page-voice .history-reader'),
            scriptCount:document.scripts.length})"#.into(),mount_deadline)
            .map_err(|error|format!("assistant mount check: {error}"))?;
        if first_probe { eprintln!("Alden assistant bootstrap: {s}"); first_probe = false; }
        if s["mounted"]==true { break; }
        if Instant::now() >= mount_deadline {return Err(format!("assistant composer did not mount: {s}"));}
        live(deadline)?;std::thread::sleep(Duration::from_millis(50));
    }
    collect_script(&window,"JSON.stringify((()=>{document.querySelector('#settings-tab-voice').click();return {selected:true};})())".into(),deadline)?;
    let activation_deadline = deadline.min(Instant::now() + Duration::from_secs(3));
    let selected = loop {
        let selected = collect_script(&window,r#"JSON.stringify({page:document.querySelector('.settings-shell')?.dataset.settingsPage,
            hidden:document.querySelector('#assistant-composer').hidden,disabled:document.querySelector('#assistant-composer-images').disabled,
            documentVisible:document.visibilityState})"#.into(),activation_deadline)?;
        if selected["page"] == "voice" && selected["hidden"] == false && selected["disabled"] == false {break selected;}
        if Instant::now() >= activation_deadline {return Err(format!("assistant composer is not active: {selected}"));}
        std::thread::sleep(Duration::from_millis(20));
    };
    eprintln!("Alden assistant selection: {selected}");
    if selected["page"] != "voice" || selected["hidden"] != false || selected["disabled"] != false {
        return Err(format!("assistant composer is not active: {selected}"));
    }
    if invalid_only {
        let mut receipts = Vec::new();
        for index in 0..12 {
            collect_script(&window,format!(r#"JSON.stringify((()=>{{
                const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array([1,2,3])],'audit-invalid.png',{{type:'image/png'}}));
                const picker=document.querySelector('#assistant-composer-images');picker.files=transfer.files;picker.dispatchEvent(new Event('change'));
                const text=document.querySelector('#assistant-composer-text');text.value='검수 '+{index};text.dispatchEvent(new Event('input'));
                return {{prepared:true}};}})())"#),deadline)?;
            loop {
                let s=collect_script(&window,"JSON.stringify({ready:document.querySelector('#assistant-composer-status').textContent.includes('1개 이미지를 첨부')})".into(),deadline)?;
                if s["ready"]==true {break;} live(deadline)?;std::thread::sleep(Duration::from_millis(50));
            }
            collect_script(&window,"JSON.stringify((()=>{document.querySelector('#assistant-composer').requestSubmit();return {submitted:true};})())".into(),deadline)?;
            let s=loop {
                let s=collect_script(&window,r#"JSON.stringify({status:document.querySelector('#assistant-composer-status').textContent,
                    busy:document.querySelector('#assistant-composer').getAttribute('aria-busy')})"#.into(),deadline)?;
                if s["busy"]=="false" && s["status"].as_str().is_some_and(|t|t.contains("오류 코드:")||t.contains("못했")||t.contains("취소")) {break s;}
                live(deadline)?;std::thread::sleep(Duration::from_millis(50));
            };
            if !s["status"].as_str().is_some_and(|t|t.contains("오류 코드: assistant_image_invalid")) {
                return Err(format!("assistant invalid-input admission failed at {index}: {s}"));
            }
            receipts.push(s);
        }
        visibility(&window,false,deadline)?;
        return Ok(json!({"receipts":receipts,"scope":"twelve invalid-image WKWebView requests; decoder rejects before model admission; no inference, microphone, TTS or sends"}));
    }
    collect_script(&window,r#"JSON.stringify((()=>{
        const canvas=document.createElement('canvas');canvas.width=128;canvas.height=128;
        const ctx=canvas.getContext('2d');ctx.fillStyle='#ff0000';ctx.fillRect(0,0,128,128);
        ctx.fillStyle='#ffffff';ctx.beginPath();ctx.arc(64,64,12,0,Math.PI*2);ctx.fill();
        const data=atob(canvas.toDataURL('image/png').split(',')[1]);const bytes=Uint8Array.from(data,c=>c.charCodeAt(0));
        const transfer=new DataTransfer();transfer.items.add(new File([bytes],'audit-public.png',{type:'image/png'}));
        const picker=document.querySelector('#assistant-composer-images');picker.files=transfer.files;picker.dispatchEvent(new Event('change'));
        return {started:true};})())"#.into(),deadline)?;
    loop {
        let s=collect_script(&window,"JSON.stringify({ready:document.querySelector('#assistant-composer-status').textContent.includes('1개 이미지를 첨부')})".into(),deadline)?;
        if s["ready"]==true {break;} live(deadline)?;std::thread::sleep(Duration::from_millis(50));
    }
    let mut receipts=Vec::new();
    for (index,text) in [(1,"검수 번호는 42입니다. 이 사진의 배경 색상 이름만 한국어로 답해주세요."),
        (2,"내가 말한 검수 번호는 무엇인가요? 숫자만 답해주세요."),
        (3,"앞서 첨부한 사진의 작은 도형과 이번 사진의 작은 도형을 그 순서대로 색과 모양만 말해주세요. 답변 형식: 색 모양, 색 모양")] {
        if index==3 {
            collect_script(&window,r#"JSON.stringify((()=>{
                const canvas=document.createElement('canvas');canvas.width=128;canvas.height=128;
                const ctx=canvas.getContext('2d');ctx.fillStyle='#0000ff';ctx.fillRect(0,0,128,128);
                ctx.fillStyle='#ffff00';ctx.fillRect(52,52,24,24);
                const data=atob(canvas.toDataURL('image/png').split(',')[1]);
                const transfer=new DataTransfer();transfer.items.add(new File([Uint8Array.from(data,c=>c.charCodeAt(0))],'audit-blue.png',{type:'image/png'}));
                const picker=document.querySelector('#assistant-composer-images');picker.files=transfer.files;picker.dispatchEvent(new Event('change'));
                return {prepared:true};})())"#.into(),deadline)?;
            loop {
                let s=collect_script(&window,"JSON.stringify({ready:document.querySelector('#assistant-composer-status').textContent.includes('1개 이미지를 첨부')})".into(),deadline)?;
                if s["ready"]==true {break;}live(deadline)?;std::thread::sleep(Duration::from_millis(50));
            }
        }
        let text=serde_json::to_string(text).map_err(|_| "audit text encoding")?;
        collect_script(&window,format!(r#"JSON.stringify((()=>{{
            const text=document.querySelector('#assistant-composer-text');text.value={text};text.dispatchEvent(new Event('input'));
            document.querySelector('#assistant-composer').requestSubmit();return {{submitted:true}};}})())"#),deadline)?;
        let mut s=loop {
            let s=collect_script(&window,r#"JSON.stringify((()=>{
                const status=document.querySelector('#assistant-composer-status').textContent;
                const replies=[...document.querySelectorAll('#voice-message-list .message-row[data-self="false"] .message-text')].map(x=>x.textContent);
                return {status,reply:replies.at(-1)??'',replies:replies.length,conversationId:document.querySelector('#voice-start').dataset.conversationId,
                    busy:document.querySelector('#assistant-composer').getAttribute('aria-busy'),width:innerWidth,scrollWidth:document.documentElement.scrollWidth};})())"#.into(),deadline)?;
            if s["status"]=="답변이 저장되었습니다." && s["replies"].as_u64()==Some(index) {break s;}
            if s["busy"]=="false" && s["status"].as_str().is_some_and(|t|t.contains("못했")||t.contains("취소")||t.contains("오류 코드:")) {
                return Err(format!("assistant input did not complete: {s}"));
            }
            live(deadline)?;std::thread::sleep(Duration::from_millis(50));
        };
        let reply=s["reply"].as_str().ok_or("missing assistant reply")?;
        let answer = reply.trim().trim_matches(['*','`']).trim().to_lowercase();
        if index==1 && !["빨간색","빨강","빨강색","붉은색","red"].contains(&answer.as_str()) { return Err("configured model did not identify the red fixture".into()); }
        if index==2 && answer!="42" {return Err("followup did not retain confirmed input".into());}
        if index==3 {
            let parts:Vec<_>=answer.split(',').map(str::trim).collect();
            if parts.len()!=2 || !["흰색 원","하얀색 원","흰색 동그라미","하얀색 동그라미"].contains(&parts[0])
                || !["노란색 사각형","노란 사각형","노란색 정사각형","노란 정사각형"].contains(&parts[1]) {
                return Err("configured model did not compare the two controlled image shapes".into());
            }
        }
        if s["width"]!=s["scrollWidth"] {return Err("assistant viewport horizontal overflow".into());}
        collect_script(&window,"JSON.stringify((()=>{window.__aldenAuditAssistantPaint=false;requestAnimationFrame(()=>requestAnimationFrame(()=>{window.__aldenAuditAssistantPaint=true;}));return {armed:true};})())".into(),deadline)?;
        let paint=loop {
            let p=collect_script(&window,r#"JSON.stringify((()=>{
                const host=document.querySelector('#voice-message-list'),h=host.getBoundingClientRect();
                const last=[...host.querySelectorAll('.message-row[data-self="false"]')].at(-1)?.getBoundingClientRect();
                return {ready:window.__aldenAuditAssistantPaint===true,scrollTop:host.scrollTop,scrollHeight:host.scrollHeight,
                    viewportHeight:host.clientHeight,latestTop:last?.top,latestBottom:last?.bottom,
                    latestVisible:!!last&&last.top>=h.top-1&&last.bottom<=h.bottom+1};})())"#.into(),deadline)?;
            if p["ready"]==true {break p;}live(deadline)?;std::thread::sleep(Duration::from_millis(25));
        };
        if paint["latestVisible"]!=true {return Err(format!("latest assistant answer is outside the transcript viewport: {paint}"));}
        s["paintObservation"]=paint;
        publish(output,&format!("assistant-turn-{index}.png"),&snapshot(&window,deadline)?,deadline)?;
        receipts.push(s);
    }
    if receipts.iter().any(|r|r["conversationId"]!=receipts[0]["conversationId"]) {return Err("assistant conversation changed on followup".into());}
    visibility(&window,false,deadline)?;
    Ok(json!({"turns":receipts,"scope":"three explicit programmatic WKWebView inputs, controlled red/white-circle and blue/yellow-square PNGs with text and visual followups; isolated history and copied saved model selection; no microphone/TTS/send, original source-store or physical input proof"}))
}

pub fn run(request: Request, mut context: tauri::Context<tauri::Wry>) {
    // This audit creates one incognito view itself; configured persistent
    // views must never be created or adopted by the fixture workflow.
    if request.entity { context.config_mut().app.windows.clear(); }
    let output = Output::open(&request.directory).unwrap_or_else(|error| {
        eprintln!("Alden render audit: {error}");
        std::process::exit(2);
    });
    let status = Arc::new(AtomicI32::new(1));
    let worker_status = Arc::clone(&status);
    let settings_mode = request.settings;
    let workspaces_mode = request.workspaces;
    let assistant_mode = request.assistant;
    let invalid_input_mode = request.assistant_invalid;
    let entity_mode = request.entity;
    let settings_loaded = Arc::new(AtomicBool::new(false));
    let asset_loaded = Arc::new(AtomicBool::new(false));
    let ready_phase = Arc::new(AtomicU8::new(0));
    let page_loaded = Arc::clone(&asset_loaded);
    let page_dom = Arc::clone(&settings_loaded);
    let bridge = if entity_mode { super::PythonBridge::for_entity_audit(request.directory.join("resolution-state"))
        .unwrap_or_else(|error|{eprintln!("Alden entity audit: {error}");std::process::exit(2);}) }
        else if assistant_mode { super::PythonBridge::for_assistant_audit(request.directory.join("input-state"))
        .unwrap_or_else(|error|{eprintln!("Alden assistant audit: {error}");std::process::exit(2);}) } else {super::PythonBridge::new()};
    let builder = {
        let builder = tauri::Builder::default()
            .manage(bridge)
            .manage(AssistantPageReady {ready:Arc::clone(&settings_loaded),phase:Arc::clone(&ready_phase)})
            .manage(AuditPolicy {
                workspaces: workspaces_mode,
                entity: entity_mode,
                focus_node: request.focus_node.clone(),
            });
        if assistant_mode {
            builder.append_invoke_initialization_script(ASSISTANT_READY_SCRIPT).invoke_handler(tauri::generate_handler![audit_assistant_ready,super::window_is_visible,super::fetch_runtime_snapshot,
                super::fetch_voice_status,super::fetch_knowledge_revision,super::fetch_emergency_state,
                fetch_settings_action,super::submit_assistant_input,super::cancel_assistant_input])
        } else if workspaces_mode {
            builder.invoke_handler(tauri::generate_handler![
                super::window_is_visible,
                super::fetch_runtime_snapshot,
                super::fetch_voice_status,
                super::fetch_knowledge_revision,
                super::fetch_emergency_state,
                fetch_settings_action
            ])
        } else {
            builder.invoke_handler(tauri::generate_handler![
                super::window_is_visible,
                super::fetch_knowledge_revision,
                fetch_settings_action
            ])
        }
    };
    let app = builder
        .on_page_load(move |view, payload| {
            if view.label() == "settings" {
                let finished = payload.event() == tauri::webview::PageLoadEvent::Finished;
                page_loaded.store(finished && audit_settings_page(view.label(), payload.url()), Ordering::Release);
                if !finished {page_dom.store(false, Ordering::Release);}
            }
        })
        .on_window_event(super::handle_window_event)
        .setup(move |app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let started = Instant::now();
                let deadline = started + Duration::from_secs(if entity_mode {160} else if assistant_mode {180} else if workspaces_mode {96}else{40});
                let result = if entity_mode {audit_entity(&handle,&output,deadline)} else if assistant_mode {audit_assistant(&handle,&output,deadline,invalid_input_mode,&settings_loaded,&asset_loaded,&ready_phase)} else if settings_mode { audit_settings(&handle, &output, deadline,workspaces_mode) } else { audit(&handle, &output, deadline) };
                let success = result.as_ref().is_ok_and(|result| result["retina"]["available"] != true || result["retina"]["densityMatches"] == true) && live(deadline).is_ok();
                let error = result.as_ref().err().cloned().or_else(|| if success { None } else { Some("density validation or final deadline failed".into()) });
                let report = json!({"schema":1,"pid":std::process::id(),
                    "mode":if entity_mode {"isolated-entity-review-fixture"}else if invalid_input_mode {"isolated-assistant-invalid-input"}else if assistant_mode {"isolated-assistant-input-generation"}else if workspaces_mode {"isolated-workspaces-authoritative-read-only"}else if settings_mode {"isolated-settings-persisted-graph-read-only"} else {"isolated-popover-persisted-graph-read-only"},
                    "executable":std::env::current_exe().ok(),"version":env!("CARGO_PKG_VERSION"),"elapsedMs":started.elapsed().as_millis(),
                    "result":result.ok(),"success":success,"error":error,
                    "scope":if entity_mode {"owned incognito WKWebView; compare/apply/revert only in a newly created private synthetic collection; no original source, production write, model inference, send or physical-input attestation"}else if invalid_input_mode {"owned WKWebView with twelve corrupt-image requests; isolated derived state; decoder rejection before inference; no microphone, TTS, sends or physical input attestation"}else if assistant_mode {"owned WKWebView with explicit image and contextual text requests using the saved selected model; isolated derived history/cache/cancellation writes and actual inference allowed; no primary-process, physical input, microphone, TTS or sending attestation"}else if workspaces_mode {"owned seven-page WKWebView using actual OSK and read-only history/catalog/status adapters; write/start/swap/stop commands unregistered; private captures; no primary/tray/physical voice/shortcut/OS-lock attestation"}else if settings_mode {"owned settings renderer with real persisted graph; other settings unavailable; no GraphRAG lookup/inference/production commands; no primary-process/physical-transition/tray/shortcut/voice attestation"} else {"owned native graph WKWebView with fixed persisted graph reads; no existing-process, OS-lock, tray click, shortcut, voice or inference attestation"}});
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
        .build(context)
        .expect("error while running Alden render audit");
    let observers = super::workspace_visibility::install(app.handle())
        .expect("error while observing Alden audit workspace");
    let exit_code = app.run_return(|_, _| {});
    drop(observers);
    let audit_status = status.load(Ordering::Acquire);
    std::process::exit(if audit_status == 0 {
        exit_code
    } else {
        audit_status
    });
}

#[cfg(test)]
mod tests {
    #[test]
    fn entity_fixture_mode_has_an_explicit_owned_scope_without_a_caller_focus() {
        let request = parse_args([OsString::from(ENTITY_FLAG), OsString::from("/private/tmp/test")].into_iter())
            .unwrap().unwrap();
        assert!(request.settings && request.workspaces && request.entity && !request.assistant);
        assert!(parse_args([OsString::from(ENTITY_FLAG), OsString::from("/private/tmp/test"),
            OsString::from("--focus-node"), OsString::from(format!("graph:{}", "a".repeat(64)))]
            .into_iter()).is_err());
    }

    #[test]
    fn ordinary_read_audits_never_admit_entity_mutation_or_fixture_seed() {
        for action in ["collection-resolution-apply","collection-resolution-revert",
                       "collection-resolution-fixture","collection-resolution-fixture-verify"] {
            assert!(!audit_read_action(action,true));
            assert!(!audit_read_action(action,false));
        }
    }
    #[test]
    fn resumed_collection_requires_its_visible_consumer_not_only_an_old_frame() {
        let mut state = serde_json::json!({"ready":true,"renderCount":11,"canvas":{"contextLost":false},
            "navigation":{"source":"collection"},"activityJournal":{"active":false}});
        assert!(super::rendered_since(&state, 10));
        assert!(!super::resumed_graph(&state, 10));
        state["activityJournal"]["active"] = serde_json::json!(true);
        assert!(super::resumed_graph(&state, 10));
        assert!(!super::resumed_graph(&state, 11));
    }
    use super::*;
    fn title_capture_state() -> Value {
        json!({"page":"history","width":640,"scrollWidth":640,"titleLayout":{
            "title":{"top":25,"bottom":55,"left":170,"right":300,"width":130,"height":30},
            "clip":{"top":0,"bottom":648,"left":155,"right":640},
            "offsets":[{"tag":"DIV","top":0,"left":0}],"windowX":0,"windowY":0
        }})
    }

    #[test]
    fn workspace_title_accepts_visible_origin_capture() {
        validate_workspace_title(&title_capture_state(), "compact").unwrap();
    }

    #[test]
    fn workspace_title_rejects_vertical_clipping_despite_no_horizontal_overflow() {
        let mut state = title_capture_state();
        state["titleLayout"]["title"]["top"] = json!(-5);
        state["titleLayout"]["title"]["bottom"] = json!(25);
        let error = validate_workspace_title(&state, "compact").unwrap_err();
        assert!(error.contains("workspace history/compact title capture invalid"));
        assert!(error.contains("fully_visible=false"));
        assert_eq!(state["width"], state["scrollWidth"]);
    }

    #[test]
    fn workspace_title_rejects_clipping_ancestor_and_bottom_edge() {
        let mut state = title_capture_state();
        state["titleLayout"]["clip"]["top"] = json!(26);
        assert!(validate_workspace_title(&state, "compact").is_err());
        state["titleLayout"]["clip"]["top"] = json!(0);
        state["titleLayout"]["clip"]["bottom"] = json!(54);
        assert!(validate_workspace_title(&state, "compact").is_err());
    }

    #[test]
    fn workspace_title_rejects_restored_scroll_even_when_title_is_visible() {
        for key in ["top", "left"] {
            let mut state = title_capture_state();
            state["titleLayout"]["offsets"][0][key] = json!(5);
            let error = validate_workspace_title(&state, "compact").unwrap_err();
            assert!(error.contains("at_origin=false"));
        }
        for key in ["windowX", "windowY"] {
            let mut state = title_capture_state();
            state["titleLayout"][key] = json!(1);
            assert!(validate_workspace_title(&state, "compact").is_err());
        }
    }

    #[test]
    fn workspace_title_requires_complete_nonempty_geometry() {
        for field in ["title", "clip", "offsets", "windowY"] {
            let mut state = title_capture_state();
            state["titleLayout"][field] = Value::Null;
            assert!(validate_workspace_title(&state, "compact").is_err());
        }
        let mut state = title_capture_state();
        state["titleLayout"]["title"]["height"] = json!(0);
        assert!(validate_workspace_title(&state, "compact").is_err());
        state["titleLayout"]["title"]["height"] = json!("30");
        assert!(validate_workspace_title(&state, "compact").is_err());
    }

    #[test]
    fn workspace_readiness_requires_a_post_data_paint_and_second_observation() {
        let mut samples = 0;
        let mut paints = 0;
        let state = wait_for_workspace(
            "conversation",
            "default",
            Instant::now() + Duration::from_secs(2),
            || {
                samples += 1;
                Ok(json!({"page":"conversation","settled":true,"pendingReasons":[]}))
            },
            || {
                paints += 1;
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(samples, 2);
        assert_eq!(paints, 1);
        assert_eq!(state["settled"], true);
    }

    #[test]
    fn expired_workspace_does_not_sample_or_arm_a_late_paint() {
        let error = wait_for_workspace(
            "voice",
            "compact",
            Instant::now() - Duration::from_millis(1),
            || panic!("expired audit must not query WebKit"),
            || panic!("expired audit must not arm WebKit"),
        )
        .unwrap_err();
        assert!(error.contains("workspace voice/compact readiness failed"));
        assert!(error.contains("native audit deadline exceeded"));
        assert!(error.contains("last observed state=null"));
    }

    #[test]
    fn workspace_callback_failure_retains_the_last_pending_conditions() {
        let mut samples = 0;
        let error = wait_for_workspace(
            "conversation",
            "default",
            Instant::now() + Duration::from_secs(2),
            || {
                samples += 1;
                if samples == 1 {
                    Ok(json!({"page":"conversation","settled":false,"pendingReasons":["messages"]}))
                } else {
                    Err("native callback deadline exceeded".into())
                }
            },
            || panic!("unsettled data must not arm the final paint"),
        )
        .unwrap_err();
        assert_eq!(samples, 2);
        assert!(error.contains("native callback deadline exceeded"));
        assert!(error.contains("\"pendingReasons\":[\"messages\"]"));
        assert!(error.contains("workspace conversation/default"));
    }

    #[test]
    fn workspace_paint_failure_is_not_converted_to_success_or_retried() {
        let mut samples = 0;
        let mut paints = 0;
        let error = wait_for_workspace(
            "history",
            "default",
            Instant::now() + Duration::from_secs(2),
            || {
                samples += 1;
                Ok(json!({"page":"history","settled":true}))
            },
            || {
                paints += 1;
                Err("WebKit dispatch failed".into())
            },
        )
        .unwrap_err();
        assert_eq!((samples, paints), (1, 1));
        assert!(error.contains("WebKit dispatch failed"));
        assert!(error.contains("\"page\":\"history\""));
    }

    #[test]
    fn workspace_capture_admits_reads_but_never_commands() {
        for action in [
            "room-upsert",
            "room-delete",
            "model-set",
            "model-swap",
            "model-prepare",
            "start_voice_session",
            "send",
            "stop",
            "collection-scheduler-control",
            "collection-resolution-apply",
            "collection-resolution-revert",
        ] {
            assert!(!audit_read_action(action, true));
        }
        assert!(audit_read_action("history-rooms", true));
        assert!(!audit_read_action("history-rooms", false));
        for action in [
            "collection-history",
            "collection-projects",
            "collection-graph",
            "collection-affinity",
            "collection-quality",
            "collection-scheduler-status",
            "collection-resolution-preview",
            "collection-resolution-history",
        ] {
            assert!(audit_read_action(action, true));
            assert!(!audit_read_action(action, false));
        }
        let request = parse_args(
            [
                OsString::from(WORKSPACES_FLAG),
                OsString::from("/private/tmp/test"),
            ]
            .into_iter(),
        )
        .unwrap()
        .unwrap();
        assert!(request.workspaces && request.settings);
    }
    #[test]
    fn assistant_load_gate_accepts_only_the_owned_settings_asset() {
        for url in ["tauri://localhost/index.html?view=settings", "http://tauri.localhost/index.html?view=settings", "https://tauri.localhost/index.html?view=settings"] {
            let url = tauri::Url::parse(url).unwrap();
            assert!(audit_settings_page("settings", &url));
            assert!(!audit_settings_page("main", &url));
        }
        for url in ["about:blank", "https://example.com/index.html?view=settings", "tauri://localhost/index.html", "tauri://localhost/index.html?view=other", "http://localhost:1420/index.html?view=settings", "tauri://localhost/other.html?view=settings"] {
            assert!(!audit_settings_page("settings", &tauri::Url::parse(url).unwrap()));
        }
    }
    #[test]
    fn invalid_assistant_audit_has_an_explicit_separate_mode() {
        for (flag, invalid) in [(ASSISTANT_FLAG, false), (ASSISTANT_INVALID_FLAG, true)] {
            let request = parse_args([OsString::from(flag), OsString::from("/private/tmp/test")].into_iter())
                .unwrap().unwrap();
            assert!(request.assistant && request.settings && request.workspaces);
            assert_eq!(request.assistant_invalid, invalid);
        }
        assert!(parse_args([OsString::from(format!("{ASSISTANT_INVALID_FLAG}=true")), OsString::from("/private/tmp/test")].into_iter()).is_err());
    }
    #[test]
    fn focused_capture_requires_a_canonical_id_and_workspace_mode() {
        let args = |mode: &str, id: &str| {
            [
                OsString::from(mode),
                OsString::from("/private/tmp/test"),
                OsString::from("--focus-node"),
                OsString::from(id),
            ]
            .into_iter()
        };
        let request = parse_args(args(WORKSPACES_FLAG, "osk:261005-012a-abcdefgh"))
            .unwrap()
            .unwrap();
        assert_eq!(
            request.focus_node.as_deref(),
            Some("osk:261005-012a-abcdefgh")
        );
        assert!(parse_args(args(SETTINGS_FLAG, "osk:261005-012a-abcdefgh")).is_err());
        assert!(parse_args(args(WORKSPACES_FLAG, "osk:261005-012a-a');alert(1)//")).is_err());
        assert!(parse_args(args(WORKSPACES_FLAG, "../node")).is_err());
        for prefix in ["graph", "youtube", "threads", "files"] {
            let id = format!("{}:{}", prefix, "a".repeat(64));
            assert_eq!(
                parse_args(args(WORKSPACES_FLAG, &id))
                    .unwrap()
                    .unwrap()
                    .focus_node,
                Some(id)
            );
        }
        assert!(parse_args(args(WORKSPACES_FLAG, &format!("graph:{}", "g".repeat(64)))).is_err());
        assert!(parse_args(args(WORKSPACES_FLAG, "graph:short")).is_err());
    }
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
    fn webkit_quantization_keeps_real_bounds_and_backing_density() {
        let mut state = json!({"ready":true,"width":560,"height":420,"scrollWidth":560,"scrollHeight":420,"dpr":1,"canvas":{"contextLost":false,"cssWidth":558,"cssHeight":417.984375,"x":1,"y":1,"width":558,"height":417}});
        assert!(validate_layout(&state).is_ok());
        assert!(density_matches(&state));
        state["canvas"]["cssHeight"] = json!(417.5);
        assert!(validate_layout(&state).is_err());
    }
    #[test]
    fn layout_rejects_context_loss_overflow_and_wrong_backing_density() {
        let mut state = json!({"ready":true,"width":560,"height":420,"scrollWidth":560,"scrollHeight":420,"dpr":2,"canvas":{"contextLost":false,"cssWidth":558,"cssHeight":418,"x":1,"y":1,"width":558,"height":418}});
        assert!(validate_layout(&state).is_ok());
        assert!(!density_matches(&state));
        state["canvas"]["width"] = json!(1116);
        state["canvas"]["height"] = json!(836);
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
    fn static_rendering_is_evidence_without_an_idle_loop() {
        let mut state = json!({"ready":true,"renderCount":11,"canvas":{"contextLost":false},"loop":{"running":false,"pendingFrame":false}});
        assert!(rendered_since(&state, 10));
        assert!(!rendered_since(&state, 11));
        state["canvas"]["contextLost"] = json!(true);
        assert!(!rendered_since(&state, 10));
        state["canvas"]["contextLost"] = json!(false);
        state["ready"] = json!(false);
        assert!(!rendered_since(&state, 10));
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
