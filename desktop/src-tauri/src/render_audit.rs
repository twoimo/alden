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
    atomic::{AtomicI32, Ordering},
    mpsc, Arc,
};
use std::time::{Duration, Instant};
use tauri::Manager;

const FLAG: &str = "--audit-own-webview";
const SETTINGS_FLAG: &str = "--audit-own-settings";
const WORKSPACES_FLAG: &str = "--audit-own-workspaces";
pub struct Request {
    pub directory: PathBuf,
    pub settings: bool,
    pub workspaces: bool,
    pub focus_node: Option<String>,
}
struct AuditPolicy {
    workspaces: bool,
    focus_node: Option<String>,
}
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
        [FLAG, SETTINGS_FLAG, WORKSPACES_FLAG]
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
        || ![FLAG, SETTINGS_FLAG, WORKSPACES_FLAG]
            .iter()
            .any(|flag| args[0] == *flag)
    {
        return Err("expected --audit-own-webview /absolute/private/directory".into());
    }
    Ok(Some(Request {
        directory: PathBuf::from(&args[1]),
        settings: args[0] == SETTINGS_FLAG || args[0] == WORKSPACES_FLAG,
        workspaces: args[0] == WORKSPACES_FLAG,
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
) -> Result<Value, String> {
    let workspace = policy.workspaces;
    if !audit_read_action(&action, workspace) {
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
                None,
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
                    | "collection-scheduler-status"
                    | "routed-models"
                    | "collection-graph"
                    | "collection-affinity"
                    | "reply-history"
                    | "geeknews-history"
                    | "room-catalog"
            )
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
        collect_script(window,format!("JSON.stringify((()=>{{const clickedAtMs=performance.now();document.querySelector('#settings-tab-{page}').click();const paint=window.__aldenAuditWorkspacePaint={{page:'{page}',ready:false,clickedAtMs,firstFrameAtMs:null}};requestAnimationFrame(()=>{{paint.firstFrameAtMs=performance.now();requestAnimationFrame(()=>{{paint.ready=true;}});}});return {{selected:true}};}})())"),deadline)?;
        let mut paint_after_data = false;
        let mut state = loop {
            let state=collect_script(window,r#"JSON.stringify((()=>{
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
              return {page:p,width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
                settled:!pending&&!loadingSettings&&!loadingHistory&&!loadingCollection&&!loadingSchedule&&!loadingGraph&&!loadingAutomation&&!listPending&&paint?.page===p&&paint?.ready===true,
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
                graphRenders:window.__knowledgeRenderCount??0};
            })())"#.into(),deadline)?;
            if state["page"] == page && state["settled"] == true {
                if !paint_after_data {
                    collect_script(window,"JSON.stringify((()=>{const paint=window.__aldenAuditWorkspacePaint;paint.ready=false;requestAnimationFrame(()=>requestAnimationFrame(()=>{paint.ready=true;}));return {armed:true};})())".into(),deadline)?;
                    paint_after_data = true;
                    continue;
                }
                break state;
            }
            live(deadline)?;
            std::thread::sleep(Duration::from_millis(50));
        };
        if state["scrollWidth"].as_u64().unwrap_or(u64::MAX) > state["width"].as_u64().unwrap_or(0)
        {
            return Err(format!("workspace overflow: {page}"));
        }
        if page == "memory" {
            state["physicsObservation"] = observe_graph_rest(window, deadline)?;
        }
        publish(
            output,
            &format!("workspace-{page}-{size}.png"),
            &snapshot(window, deadline)?,
            deadline,
        )?;
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
    nodeActivity:d?.nodeActivity??null,activityJournal:d?.activityJournal??null,
    canvas:r?{width:r.width,height:r.height,contextLost:c.getContext('webgl2')?.isContextLost()??null}:null,
    notePane:(()=>{const n=document.querySelector('#knowledge-note-pane');const b=n?.getBoundingClientRect();return n&&b?{hidden:n.hidden,x:b.x,y:b.y,width:b.width,height:b.height,scrollWidth:n.scrollWidth,focusState:n.dataset.focusState,nodeId:n.dataset.nodeId,bodyChars:document.querySelector('#knowledge-node-body')?.textContent?.length??0}:null;})(),
    backDisabled:document.querySelector('#knowledge-back')?.disabled,
    overviewDisabled:document.querySelector('#knowledge-overview')?.disabled,
    expandDisabled:document.querySelector('#knowledge-expand-hop')?.disabled,
    clearedDetail:document.querySelector('#knowledge-relations')?.children.length===0
      &&document.querySelector('#knowledge-focus-title')?.textContent==='항목을 선택하면 관련 정보를 보여드립니다.',
    accessibleNodes:document.querySelectorAll('.knowledge-a11y-node').length,
    pause:p?{state:p.state,eventAtMs:p.eventAtMs,rendersAfterEvent:p.rendersAfterEvent}:null};
})())"#;

fn observe_graph_rest(window: &tauri::WebviewWindow, deadline: Instant) -> Result<Value, String> {
    let script = r#"JSON.stringify((()=>{const d=window.__knowledgeRenderDiagnostics;
      return {observedAtMs:performance.now(),renderCount:d?.renderCount??0,
        displayedNodes:d?.nodeCount??0,physics:d?.physics??null,
        semantic:window.__knowledgeView?.semanticLayout??null,
        affinity:window.__knowledgeView?.graphRead?.affinity??null};})())"#;
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
            && affinity_ready)
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
    collect_script(window, format!("{script};{GRAPH_COLLECT}"), deadline)?;
    std::thread::sleep(Duration::from_millis(250));
    let settle = Instant::now() + Duration::from_secs(5);
    loop {
        let state = collect_script(window, GRAPH_COLLECT.into(), deadline)?;
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
                "graph action {action} did not settle: pending={}, focusState={}",
                graph_pending["pending"], state["notePane"]["focusState"]
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
        let state = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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
                let state = collect_script(&window, GRAPH_COLLECT.into(), waited_by)?;
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
    let initial = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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
            let compact = collect_script(&window, GRAPH_COLLECT.into(), settled_by)?;
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
    graph_step(&window, "overview", deadline)?;
    let workspace_compact = if workspaces {
        capture_workspaces(&window, output, "compact", deadline)?
    } else {
        Vec::new()
    };
    publish(output, "workspace-readback.json", &serde_json::to_vec_pretty(&json!({
        "schema":1,"scope":"owned read-only WKWebView with persisted application data; not the primary process, physical voice, lock or production",
        "workspaceDefault":workspace_default,"workspaceCompact":workspace_compact,
        "navigation":{"initial":initial["navigation"],"expanded":expanded["navigation"],"backOverview":back_overview["navigation"]}
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
            let state = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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
            let sample = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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
            let state = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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
        let hidden = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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
    let reopened = collect_script(&window, GRAPH_COLLECT.into(), deadline)?;
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

pub fn run(request: Request, context: tauri::Context<tauri::Wry>) {
    let output = Output::open(&request.directory).unwrap_or_else(|error| {
        eprintln!("Alden render audit: {error}");
        std::process::exit(2);
    });
    let status = Arc::new(AtomicI32::new(1));
    let worker_status = Arc::clone(&status);
    let settings_mode = request.settings;
    let workspaces_mode = request.workspaces;
    let builder = {
        let builder = tauri::Builder::default()
            .manage(super::PythonBridge::new())
            .manage(AuditPolicy {
                workspaces: workspaces_mode,
                focus_node: request.focus_node.clone(),
            });
        if workspaces_mode {
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
        .on_window_event(super::handle_window_event)
        .setup(move |app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(if workspaces_mode {96}else{40});
                let result = if settings_mode { audit_settings(&handle, &output, deadline,workspaces_mode) } else { audit(&handle, &output, deadline) };
                let success = result.as_ref().is_ok_and(|result| result["retina"]["available"] != true || result["retina"]["densityMatches"] == true) && live(deadline).is_ok();
                let error = result.as_ref().err().cloned().or_else(|| if success { None } else { Some("density validation or final deadline failed".into()) });
                let report = json!({"schema":1,"pid":std::process::id(),
                    "mode":if workspaces_mode {"isolated-workspaces-authoritative-read-only"}else if settings_mode {"isolated-settings-persisted-graph-read-only"} else {"isolated-popover-persisted-graph-read-only"},
                    "executable":std::env::current_exe().ok(),"version":env!("CARGO_PKG_VERSION"),
                    "result":result.ok(),"success":success,"error":error,
                    "scope":if workspaces_mode {"owned seven-page WKWebView using actual OSK and read-only history/catalog/status adapters; write/start/swap/stop commands unregistered; private captures; no primary/tray/physical voice/shortcut/OS-lock attestation"}else if settings_mode {"owned settings renderer with real persisted graph; other settings unavailable; no GraphRAG lookup/inference/production commands; no primary-process/physical-transition/tray/shortcut/voice attestation"} else {"owned native graph WKWebView with fixed persisted graph reads; no existing-process, OS-lock, tray click, shortcut, voice or inference attestation"}});
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
            "collection-scheduler-status",
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
