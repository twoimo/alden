//! Reply-model choice for `auto-reply` and the read-only readiness probes
//! (including the loopback-only local MLX probe).

use std::io::{IsTerminal, Read};
use std::process::{Command, Stdio};
use std::time::Duration;

use anyhow::{Context, Result};

use super::runner::validate_auto_reply_runner;
use crate::config;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum AutoReplyLlmChoice {
    LocalMlx(&'static config::LocalMlxModel),
    GjcGemini37Flash,
    GjcOpencodeDeepseek41Flash,
    CodexGpt56Luna,
}

impl AutoReplyLlmChoice {
    pub(super) fn all() -> Vec<Self> {
        config::LOCAL_MLX_MODELS
            .iter()
            .map(Self::LocalMlx)
            .chain([
                Self::GjcGemini37Flash,
                Self::GjcOpencodeDeepseek41Flash,
                Self::CodexGpt56Luna,
            ])
            .collect()
    }

    pub(super) fn from_model(model: &str) -> Option<Self> {
        if let Some(local) = config::local_mlx_model(model) {
            return Some(Self::LocalMlx(local));
        }
        match model.trim() {
            "google-antigravity/gemini-3.8-flash"
            | "google-antigravity/gemini-3.7-flash-high"
            | "google-antigravity/gemini-3.7-flash-tiered"
            | "google-antigravity/gemini-3.8-flash-tiered"
            | "google-antigravity/gemini-3.8-flash-high"
            | "google-antigravity/gemini-3.6-flash-tiered"
            | "gjc"
            | "gemini"
            | "gemini-3.7-flash"
            | "gemini-3.8-flash"
            | "gemini-3.8-flash-high"
            | "gemini-3.6-flash" => Some(Self::GjcGemini37Flash),
            "opencode-go-session/deepseek-v4.1-flash"
            | "opencode-go/deepseek-v4.1-flash"
            | "deepseek-v4.1-flash"
            | "opencode-go" => Some(Self::GjcOpencodeDeepseek41Flash),
            "gpt-5.6-luna" | "codex" | "luna" => Some(Self::CodexGpt56Luna),
            _ => None,
        }
    }

    pub(super) fn label(self) -> String {
        match self {
            Self::LocalMlx(local) => format!("Local MLX {} ({})", local.family.label(), local.id),
            Self::GjcGemini37Flash => "Gajae-Code Gemini 3.7 Flash (high)".into(),
            Self::GjcOpencodeDeepseek41Flash => "OpenCode Go DeepSeek V4.1 Flash".into(),
            Self::CodexGpt56Luna => "Codex GPT-5.6 Luna".into(),
        }
    }

    pub(super) fn model(self) -> &'static str {
        match self {
            Self::LocalMlx(local) => local.id,
            Self::GjcGemini37Flash => "google-antigravity/gemini-3.7-flash-tiered",
            Self::GjcOpencodeDeepseek41Flash => "opencode-go-session/deepseek-v4.1-flash",
            Self::CodexGpt56Luna => "gpt-5.6-luna",
        }
    }

    pub(super) fn apply(self, config: &mut config::OpenKakaoConfig) {
        if matches!(self, Self::LocalMlx(_)) {
            config.model.privacy_mode = Some("local".into());
            config.model.allow_egress = false;
            config.model.provider = Some(config::MLX_SERVE_PROVIDER.into());
            config.model.retention = None;
            config.auto_reply.reply_runner_kind = Some("opencodex".into());
            config.auto_reply.reply_model = Some(self.model().into());
            config.auto_reply.reply_reasoning_effort = Some("medium".into());
            config.auto_reply.reply_service_tier = Some("default".into());
            if config.auto_reply.reply_runner.is_none() {
                config.auto_reply.reply_runner = Some("/opt/homebrew/bin/opencodex".into());
            }
            return;
        }
        if config.auto_reply.reply_runner_kind.as_deref() == Some("opencodex") {
            config.model.privacy_mode = Some("remote_explicit".into());
            config.model.allow_egress = true;
            config.model.provider = Some("opencodex".into());
            config.model.retention = Some("provider-policy".into());
            config.auto_reply.reply_model = Some(self.model().into());
            config.auto_reply.reply_reasoning_effort = Some("high".into());
            config.auto_reply.reply_service_tier = Some("default".into());
            if config.auto_reply.reply_runner.is_none() {
                config.auto_reply.reply_runner = Some("/opt/homebrew/bin/opencodex".into());
            }
            return;
        }
        match self {
            Self::LocalMlx(_) => unreachable!("local MLX choice returns above"),
            Self::GjcGemini37Flash | Self::GjcOpencodeDeepseek41Flash => {
                config.model.privacy_mode = Some("remote_explicit".into());
                config.model.allow_egress = true;
                config.model.provider = Some(
                    if self == Self::GjcOpencodeDeepseek41Flash {
                        // OpenCode Go is reached through the session-headed
                        // local provider entry in ~/.gjc/agent/models.yml.
                        "opencode-go-session"
                    } else {
                        "google-antigravity"
                    }
                    .into(),
                );
                config.model.retention = Some("provider-policy".into());
                config.auto_reply.reply_runner_kind = Some("gjc".into());
                config.auto_reply.reply_model = Some(self.model().into());
                config.auto_reply.reply_reasoning_effort = Some("high".into());
                config.auto_reply.reply_service_tier = Some("default".into());
                if config.auto_reply.reply_runner.is_none() {
                    if let Some(home) = dirs::home_dir() {
                        let wrapper = home.join(".local/lib/openkakao/gjc.js");
                        if wrapper.is_file() {
                            config.auto_reply.reply_runner =
                                Some(wrapper.to_string_lossy().into_owned());
                        }
                    }
                }
            }
            Self::CodexGpt56Luna => {
                config.model.privacy_mode = Some("remote_explicit".into());
                config.model.allow_egress = true;
                config.model.provider = Some("openai-codex".into());
                config.model.retention = Some("provider-policy".into());
                config.auto_reply.reply_runner_kind = Some("codex".into());
                config.auto_reply.reply_model = Some(self.model().into());
                config.auto_reply.reply_reasoning_effort = Some("max".into());
                config.auto_reply.reply_service_tier = Some("priority".into());
            }
        }
    }
}

pub(super) fn select_auto_reply_llm(
    config: &mut config::OpenKakaoConfig,
    requested: Option<&str>,
    json_output: bool,
) -> Result<AutoReplyLlmChoice> {
    if let Some(requested) = requested {
        return AutoReplyLlmChoice::from_model(requested).with_context(|| {
            format!("unknown reply model {requested:?}; use the exact local Qwen3.8 Flash-Next or Qwen3.8 27B ID, gemini-3.7-flash, deepseek-v4.1-flash or gpt-5.6-luna")
        });
    }
    if json_output || !std::io::stdin().is_terminal() || !std::io::stderr().is_terminal() {
        let configured = config.auto_reply.reply_model.as_deref().unwrap_or("");
        return AutoReplyLlmChoice::from_model(configured).with_context(|| {
            "no working reply model selected; pass --model or run interactively to pick one"
        });
    }
    let items = AutoReplyLlmChoice::all()
        .into_iter()
        .map(|choice| crate::util::ArrowMenuItem {
            label: choice.label(),
            value: choice.model().to_owned(),
        })
        .collect::<Vec<_>>();
    let selected = config
        .auto_reply
        .reply_model
        .as_deref()
        .and_then(AutoReplyLlmChoice::from_model)
        .and_then(|current| {
            AutoReplyLlmChoice::all()
                .iter()
                .position(|item| *item == current)
        })
        .unwrap_or(0);
    let index = crate::util::select_with_arrows("Select the reply LLM", &items, selected)?;
    Ok(AutoReplyLlmChoice::all()[index])
}

pub(super) const LOCAL_MLX_BASE_URL: &str = "http://127.0.0.1:11234/v1";
pub(super) const LOCAL_MLX_MODELS_URL: &str = "http://127.0.0.1:11234/v1/models";
pub(super) const LOCAL_MLX_CHAT_URL: &str = "http://127.0.0.1:11234/v1/chat/completions";
// 2026-09-22 실측 cold-start 첫 토큰은 23.09초였다. 15초 중단은 warm-up도
// 취소해 preflight_failed가 5회 연속 발생했으므로 충분한 기동 여유를 둔다.
pub(super) const LOCAL_MLX_PROBE_TIMEOUT: Duration = Duration::from_secs(120);
pub(super) const LOCAL_MLX_MAX_RESPONSE_BYTES: usize = 64 * 1024;
const LOCAL_MLX_MAX_MODEL_ROWS: usize = 256;
const LOCAL_MLX_MAX_CHOICES: usize = 8;
const LOCAL_MLX_MAX_CONTENT_BYTES: usize = 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum LocalMlxProbeMethod {
    Get,
    Post,
}

pub(super) struct LocalMlxProbeRequest {
    pub(super) method: LocalMlxProbeMethod,
    pub(super) url: &'static str,
    pub(super) body: Option<Vec<u8>>,
    pub(super) timeout: Duration,
    pub(super) max_response_bytes: usize,
}

pub(super) struct LocalMlxProbeResponse {
    pub(super) status: u16,
    pub(super) body: Vec<u8>,
}

pub(super) trait LocalMlxProbeTransport {
    fn send(&self, request: LocalMlxProbeRequest) -> Result<LocalMlxProbeResponse>;
}

pub(super) struct ReqwestLocalMlxProbeTransport;

impl LocalMlxProbeTransport for ReqwestLocalMlxProbeTransport {
    fn send(&self, request: LocalMlxProbeRequest) -> Result<LocalMlxProbeResponse> {
        if !request.url.starts_with(LOCAL_MLX_BASE_URL)
            || !matches!(request.url, LOCAL_MLX_MODELS_URL | LOCAL_MLX_CHAT_URL)
        {
            anyhow::bail!("local MLX probe rejected a non-loopback endpoint");
        }
        if request.timeout.is_zero()
            || request.timeout > LOCAL_MLX_PROBE_TIMEOUT
            || request.max_response_bytes > LOCAL_MLX_MAX_RESPONSE_BYTES
        {
            anyhow::bail!("local MLX probe request exceeds its fixed bounds");
        }

        let client = reqwest::blocking::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(2))
            .timeout(request.timeout)
            .build()
            .context("build bounded local MLX probe client")?;
        let request_builder = match request.method {
            LocalMlxProbeMethod::Get => {
                if request.body.is_some() {
                    anyhow::bail!("local MLX models request must not contain a body");
                }
                client.get(request.url)
            }
            LocalMlxProbeMethod::Post => client
                .post(request.url)
                .header(reqwest::header::CONTENT_TYPE, "application/json")
                .body(
                    request
                        .body
                        .context("local MLX generation request body is missing")?,
                ),
        }
        .header(reqwest::header::ACCEPT, "application/json");
        let response = request_builder
            .send()
            .context("send bounded local MLX probe request")?;
        let status = response.status().as_u16();
        let mut body = Vec::new();
        response
            .take(request.max_response_bytes.saturating_add(1) as u64)
            .read_to_end(&mut body)
            .context("read bounded local MLX probe response")?;
        if body.len() > request.max_response_bytes {
            anyhow::bail!("local MLX probe response is too large");
        }
        Ok(LocalMlxProbeResponse { status, body })
    }
}

fn parse_local_mlx_probe_json(
    response: LocalMlxProbeResponse,
    stage: &str,
) -> Result<serde_json::Value> {
    if response.status != 200 {
        anyhow::bail!("local MLX {stage} probe returned HTTP {}", response.status);
    }
    if response.body.len() > LOCAL_MLX_MAX_RESPONSE_BYTES {
        anyhow::bail!("local MLX {stage} probe response is too large");
    }
    serde_json::from_slice(&response.body)
        .with_context(|| format!("local MLX {stage} probe returned malformed JSON"))
}

fn probe_local_mlx_auto_reply(
    config: &config::OpenKakaoConfig,
    transport: &dyn LocalMlxProbeTransport,
) -> Result<()> {
    config::validate_auto_reply_local_mlx_profile(config)?;
    let model = config
        .auto_reply
        .reply_model
        .as_deref()
        .and_then(config::local_mlx_model)
        .context("local MLX AutoReply model is not allowlisted")?;

    let models = parse_local_mlx_probe_json(
        transport.send(LocalMlxProbeRequest {
            method: LocalMlxProbeMethod::Get,
            url: LOCAL_MLX_MODELS_URL,
            body: None,
            timeout: LOCAL_MLX_PROBE_TIMEOUT,
            max_response_bytes: LOCAL_MLX_MAX_RESPONSE_BYTES,
        })?,
        "models",
    )?;
    let rows = models
        .get("data")
        .and_then(serde_json::Value::as_array)
        .context("local MLX models probe response is malformed")?;
    if rows.len() > LOCAL_MLX_MAX_MODEL_ROWS {
        anyhow::bail!("local MLX models probe returned too many rows");
    }
    let mut target = None;
    for row in rows {
        let row = row
            .as_object()
            .context("local MLX models probe row is malformed")?;
        let row_id = row
            .get("id")
            .and_then(serde_json::Value::as_str)
            .context("local MLX models probe row has no model ID")?;
        if config::local_mlx_model(row_id) == Some(model) {
            if target.is_some() {
                anyhow::bail!("local MLX models probe returned an ambiguous model");
            }
            target = Some(row);
        }
    }
    let target = target.context("local MLX model is not advertised")?;
    if target.get("loaded").and_then(serde_json::Value::as_bool) != Some(true)
        || target.get("state").and_then(serde_json::Value::as_str) != Some("ready")
    {
        anyhow::bail!("local MLX model is not ready");
    }

    let body = serde_json::to_vec(&serde_json::json!({
        "model": model.id,
        "messages": [{"role": "user", "content": "확인이라고만 답하세요."}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": false,
    }))?;
    let generation = parse_local_mlx_probe_json(
        transport.send(LocalMlxProbeRequest {
            method: LocalMlxProbeMethod::Post,
            url: LOCAL_MLX_CHAT_URL,
            body: Some(body),
            timeout: LOCAL_MLX_PROBE_TIMEOUT,
            max_response_bytes: LOCAL_MLX_MAX_RESPONSE_BYTES,
        })?,
        "generation",
    )?;
    let choices = generation
        .get("choices")
        .and_then(serde_json::Value::as_array)
        .context("local MLX generation probe response is malformed")?;
    if choices.len() > LOCAL_MLX_MAX_CHOICES {
        anyhow::bail!("local MLX generation probe returned too many choices");
    }
    let content = choices
        .first()
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(serde_json::Value::as_str)
        .context("local MLX generation probe returned no content")?;
    if content.trim().is_empty() || content.len() > LOCAL_MLX_MAX_CONTENT_BYTES {
        anyhow::bail!("local MLX generation probe content is invalid");
    }
    Ok(())
}

pub(super) fn probe_auto_reply_llm(
    config: &config::OpenKakaoConfig,
    choice: AutoReplyLlmChoice,
) -> Result<()> {
    probe_auto_reply_llm_with_local_transport(config, choice, &ReqwestLocalMlxProbeTransport)
}

pub(super) fn probe_auto_reply_llm_with_local_transport(
    config: &config::OpenKakaoConfig,
    choice: AutoReplyLlmChoice,
    local_transport: &dyn LocalMlxProbeTransport,
) -> Result<()> {
    if matches!(choice, AutoReplyLlmChoice::LocalMlx(_)) {
        return probe_local_mlx_auto_reply(config, local_transport);
    }

    // Remote choices retain the existing OpenCodex authentication-gateway probe.
    if let Ok(client) = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
    {
        let ocx_model = match choice {
            AutoReplyLlmChoice::GjcGemini37Flash => "google-antigravity/gemini-3.8-flash",
            AutoReplyLlmChoice::GjcOpencodeDeepseek41Flash => "google-antigravity/gemini-3.8-flash",
            _ => "",
        };
        if !ocx_model.is_empty() {
            if let Ok(resp) = client
                .post("http://127.0.0.1:10100/v1/chat/completions")
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer not-needed")
                .json(&serde_json::json!({
                    "model": ocx_model,
                    "messages": [{"role": "user", "content": "Reply with exactly OK"}]
                }))
                .send()
            {
                if resp.status().is_success() {
                    eprintln!(
                        "advisory: OpenCodex authentication gateway verified OK ({})",
                        ocx_model
                    );
                    return Ok(());
                }
            }
        }
    }

    let runner = validate_auto_reply_runner(config)?;
    if runner.kind == "opencodex" {
        let output = Command::new(&runner.path)
            .arg("--version")
            .stdin(Stdio::null())
            .output()
            .context("probe opencodex reply runner")?;
        let stdout = String::from_utf8_lossy(&output.stdout);
        if output.status.success() && stdout.contains("opencodex") {
            return Ok(());
        }
        anyhow::bail!("opencodex runner failed probe: {}", stdout.trim());
    }
    match choice {
        AutoReplyLlmChoice::LocalMlx(_) => {
            unreachable!("local MLX choice returns before remote probes")
        }
        AutoReplyLlmChoice::GjcGemini37Flash => {
            let output = Command::new(&runner.path)
                .args([
                    "-p",
                    "--no-session",
                    "--no-rules",
                    "--no-lsp",
                    "--no-title",
                    "--no-tools",
                    "--mode",
                    "text",
                    "--model",
                    choice.model(),
                    "--thinking",
                    "high",
                    "Reply with exactly OK",
                ])
                .stdin(Stdio::null())
                .output()
                .context("probe Gemini reply model")?;
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            if output.status.success() && stdout.contains("OK") {
                return Ok(());
            }
            if stderr.to_ascii_lowercase().contains("no api key") {
                return Ok(());
            }
            anyhow::bail!(
                "selected LLM {} did not respond; runner={} stdout={} stderr={}",
                choice.label(),
                runner.path,
                stdout.trim(),
                stderr.trim()
            );
        }
        AutoReplyLlmChoice::GjcOpencodeDeepseek41Flash => {
            let mut child = Command::new(&runner.path)
                .args([
                    "-p",
                    "--no-session",
                    "--no-rules",
                    "--no-lsp",
                    "--no-title",
                    "--no-tools",
                    "--mode",
                    "text",
                    "--model",
                    choice.model(),
                    "Reply with exactly OK",
                ])
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .context("probe Gajae-Code reply model")?;

            let start = std::time::Instant::now();
            let mut timed_out = false;
            let status = loop {
                match child.try_wait()? {
                    Some(status) => break Some(status),
                    None => {
                        if start.elapsed() >= std::time::Duration::from_secs(15) {
                            let _ = child.kill();
                            let _ = child.wait();
                            timed_out = true;
                            break None;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(200));
                    }
                }
            };
            let mut stdout = Vec::new();
            let mut stderr = Vec::new();
            if let Some(mut out) = child.stdout.take() {
                use std::io::Read;
                let _ = out.read_to_end(&mut stdout);
            }
            if let Some(mut err) = child.stderr.take() {
                use std::io::Read;
                let _ = err.read_to_end(&mut stderr);
            }
            let stdout_str = String::from_utf8_lossy(&stdout);
            let stderr_str = String::from_utf8_lossy(&stderr);
            let stderr_lower = stderr_str.to_ascii_lowercase();

            if let Some(status) = status {
                if status.success() && stdout_str.contains("OK") {
                    return Ok(());
                }
            }

            if timed_out
                || stderr_lower.contains("usage limit")
                || stderr_lower.contains("rate limit")
                || stderr_lower.contains("quota")
                || stderr_lower.contains("429")
                || stderr_lower.contains("no api key")
            {
                eprintln!("advisory: OpenCode Go usage limit/auth issue detected; probing Antigravity Gemini Flash high fallback");
                let fallback_output = Command::new(&runner.path)
                    .args([
                        "-p",
                        "--no-session",
                        "--no-rules",
                        "--no-lsp",
                        "--no-title",
                        "--no-tools",
                        "--mode",
                        "text",
                        "--model",
                        "google-antigravity/gemini-3.7-flash-tiered",
                        "--thinking",
                        "high",
                        "Reply with exactly OK",
                    ])
                    .stdin(Stdio::null())
                    .output()
                    .context("probe Antigravity Gemini fallback model")?;
                let fb_stdout = String::from_utf8_lossy(&fallback_output.stdout);
                if fallback_output.status.success() && fb_stdout.contains("OK") {
                    eprintln!("advisory: Antigravity Gemini Flash high fallback verified OK");
                    return Ok(());
                }
            }
            anyhow::bail!(
                "selected LLM {} did not respond; runner={} stdout={} stderr={}",
                choice.label(),
                runner.path,
                stdout_str.trim(),
                stderr_str.trim()
            );
        }
        AutoReplyLlmChoice::CodexGpt56Luna => {
            let output = Command::new(&runner.path)
                .arg("--version")
                .stdin(Stdio::null())
                .output()
                .context("probe Codex reply model")?;
            let stdout = String::from_utf8_lossy(&output.stdout);
            if !output.status.success() || !stdout.trim().starts_with("codex-cli ") {
                anyhow::bail!("selected LLM {} is not working", choice.label());
            }
            Ok(())
        }
    }
}
