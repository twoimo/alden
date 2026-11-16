mod auth;
mod auth_flow;
use openkakao_cli::ax_send;
mod auto_reply_runtime;
mod commands;
mod config;
mod context_sync_replica;
mod credentials;
use openkakao_cli::error;
mod export;
use openkakao_cli::local_db;
use openkakao_cli::loco;
mod loco_helpers;
use openkakao_cli::media;
use openkakao_cli::message_db;
use openkakao_cli::model;
mod rest;
mod state;
mod util;
use openkakao_cli::room_catalog;

use std::io;
use std::sync::atomic::Ordering;

use anyhow::{Context, Result};
use clap::{CommandFactory, Parser, Subcommand};
use clap_complete::{generate, Shell};

use crate::auth_flow::{set_auth_policy, AuthPolicy};
use crate::commands::local_send::{require_allowed_send_chat, require_ax_send};
use crate::commands::read::ReadCommandOptions;
use crate::commands::send::require_loco_write;
use crate::commands::watch::{WatchOptions, WebhookFormat};
use crate::config::load_config;
use crate::util::{format_outgoing_message, NO_COLOR, VERSION};
use openkakao_cli::auto_reply_service;

fn validate_service_bootstrap_paths(command: &Commands) -> Result<Option<&std::path::Path>> {
    match command {
        Commands::AxWatch {
            service_mode: true,
            status_path: Some(status_path),
            log_path: Some(log_path),
            ..
        } => {
            auto_reply_service::validate_watch_runtime_paths(
                std::path::Path::new(status_path),
                std::path::Path::new(log_path),
            )?;
            Ok(Some(std::path::Path::new(status_path)))
        }
        _ => Ok(None),
    }
}

fn parse_local_poll_interval(value: &str) -> std::result::Result<f64, String> {
    let interval = value
        .parse::<f64>()
        .map_err(|_| "interval must be a number".to_string())?;
    if !interval.is_finite() || !(0.05..=60.0).contains(&interval) {
        return Err("interval must be between 0.05 and 60 seconds".to_string());
    }
    Ok(interval)
}
fn parse_local_poll_count(value: &str) -> std::result::Result<usize, String> {
    let count = value
        .parse::<usize>()
        .map_err(|_| "count must be a positive integer".to_string())?;
    if !(1..=local_db::LOCAL_POLL_MAX_ROWS).contains(&count) {
        return Err(format!(
            "count must be between 1 and {}",
            local_db::LOCAL_POLL_MAX_ROWS
        ));
    }
    Ok(count)
}
fn parse_local_poll_chat_id(value: &str) -> std::result::Result<i64, String> {
    let chat_id = value
        .parse::<i64>()
        .map_err(|_| "chat-id must be a positive integer".to_string())?;
    if chat_id <= 0 {
        return Err("chat-id must be a positive integer".to_string());
    }
    Ok(chat_id)
}
fn parse_context_log_id(value: &str) -> std::result::Result<i64, String> {
    let log_id = value
        .parse::<i64>()
        .map_err(|_| "log ID must be a positive integer".to_string())?;
    if log_id <= 0 {
        return Err("log ID must be a positive integer".to_string());
    }
    Ok(log_id)
}

#[derive(Parser, Debug)]
#[command(name = "openkakao-cli")]
#[command(about = "OpenKakao Rust CLI", long_about = None)]
#[command(version = VERSION)]
struct Cli {
    #[arg(long, global = true, help = "Output as JSON")]
    json: bool,
    #[arg(long, global = true, help = "Disable colored output")]
    no_color: bool,
    #[arg(
        long,
        global = true,
        help = "Explicitly acknowledge unattended or non-interactive operation"
    )]
    unattended: bool,
    #[arg(
        long,
        global = true,
        help = "Allow non-interactive send operations when combined with --unattended"
    )]
    allow_non_interactive_send: bool,
    #[arg(
        long,
        global = true,
        help = "Allow watch read receipts, hooks, and webhooks when combined with --unattended"
    )]
    allow_watch_side_effects: bool,
    #[arg(
        long,
        global = true,
        help = "Do not prepend '🤖 [Sent via openkakao]' prefix to outgoing messages"
    )]
    no_prefix: bool,
    #[arg(
        long,
        global = true,
        help = "Print [DONE] to stdout after command completes successfully"
    )]
    completion_promise: bool,
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Verify token validity
    Auth,
    /// Show persisted auth recovery state and cooldowns
    AuthStatus,
    /// Extract credentials from KakaoTalk cache (or log in with --manual)
    Login {
        #[arg(long)]
        save: bool,
        /// Log in with email + password instead of reading the KakaoTalk cache.
        /// Required on recent KakaoTalk builds that no longer cache the token.
        #[arg(long)]
        manual: bool,
        /// Email or phone number for --manual login (prompted if omitted)
        #[arg(long)]
        email: Option<String>,
        /// Password for --manual login (prompted, hidden, if omitted)
        #[arg(long)]
        password: Option<String>,
        /// Override the app version string used in --manual login headers
        #[arg(long)]
        app_version: Option<String>,
    },
    /// Show own profile
    Me,
    /// List friends
    Friends {
        #[arg(short = 'f', long)]
        favorites: bool,
        #[arg(long)]
        hidden: bool,
        #[arg(short = 's', long)]
        search: Option<String>,
        #[arg(
            long,
            help = "Build a local friend graph from LOCO GETMEM across known chats"
        )]
        local: bool,
        #[arg(
            long,
            help = "When used with --local, only include users seen in this chat"
        )]
        chat_id: Option<i64>,
        #[arg(long, help = "When used with --local, only include this user")]
        user_id: Option<i64>,
    },
    /// List chat rooms
    Chats {
        #[arg(short = 'a', long = "all")]
        show_all: bool,
        #[arg(short = 'u', long)]
        unread: bool,
        #[arg(long, help = "Search chat rooms by title")]
        search: Option<String>,
        #[arg(long = "type", help = "Filter by type: dm, group, memo, open")]
        chat_type: Option<String>,
        #[arg(long, help = "Force REST chat list path instead of LOCO")]
        rest: bool,
    },
    /// Read messages from a chat room
    Read {
        chat_id: i64,
        #[arg(short = 'n', long, default_value_t = 30)]
        count: usize,
        #[arg(long, help = "Before this logId (backward pagination)")]
        before: Option<i64>,
        #[arg(long, help = "Resume from cursor (logId from previous run)")]
        cursor: Option<i64>,
        #[arg(long, help = "Filter messages after this date (YYYY-MM-DD)")]
        since: Option<String>,
        #[arg(long, help = "Fetch all available messages")]
        all: bool,
        #[arg(
            long,
            default_value_t = 100,
            help = "Delay between LOCO batches in ms (ignored for --rest)"
        )]
        delay_ms: u64,
        #[arg(long, help = "Allow LOCO full-history reads on open chats")]
        force: bool,
        #[arg(long, help = "Force REST read path instead of LOCO")]
        rest: bool,
    },
    /// List members of a chat room
    Members {
        chat_id: i64,
        #[arg(long, help = "Force REST member list path instead of LOCO")]
        rest: bool,
        #[arg(long, help = "Show richer LOCO member profile fields")]
        full: bool,
    },
    /// Get detailed information about a chat room
    Chatinfo { chat_id: i64 },
    /// Show account settings
    Settings,
    /// Get link preview (OG tags) for a URL
    Scrap { url: String },
    /// Show a friend's profile
    Profile {
        user_id: i64,
        #[arg(long, help = "Use chat-scoped LOCO member profile for this chat")]
        #[arg(conflicts_with = "local")]
        chat_id: Option<i64>,
        #[arg(
            long,
            help = "Resolve from the local LOCO friend graph built from known chats"
        )]
        local: bool,
    },
    /// Add a friend to favorites
    Favorite { user_id: i64 },
    /// Remove a friend from favorites
    Unfavorite { user_id: i64 },
    /// Hide a friend
    Hide { user_id: i64 },
    /// Unhide a friend
    Unhide { user_id: i64 },
    /// List profile cards (multi-profile)
    Profiles,
    /// Show notification alarm keywords
    Keywords,
    /// Show unread chat summary
    Unread,
    /// Export chat messages
    Export {
        chat_id: i64,
        #[arg(long, default_value = "txt", help = "Output format: json, csv, txt")]
        format: String,
        #[arg(short = 'o', long, help = "Output file (default: stdout)")]
        output: Option<String>,
    },
    /// Search messages in a chat room
    Search { chat_id: i64, query: String },
    /// Show chat statistics (message counts, activity, top participants)
    Stats {
        chat_id: i64,
        #[arg(
            long,
            help = "Number of recent messages to analyze (default: all available)"
        )]
        limit: Option<usize>,
        #[arg(long, help = "Only count messages after this date (YYYY-MM-DD)")]
        since: Option<String>,
    },
    /// Generate shell completions
    Completions {
        #[arg(value_enum)]
        shell: Shell,
    },
    /// Attempt to renew OAuth token using cached refresh_token
    Renew,
    /// Re-login via login.json to obtain LOCO access_token
    Relogin {
        /// Generate fresh X-VC values instead of using cached one
        #[arg(long)]
        fresh_xvc: bool,
        /// Supply current password (cached password may be expired)
        #[arg(long)]
        password: Option<String>,
        /// Override email/phone from Cache.db
        #[arg(long)]
        email: Option<String>,
    },
    #[command(hide = true)]
    /// Test LOCO protocol connection (legacy command)
    LocoTest,
    /// Send a message via LOCO protocol
    Send {
        chat_id: i64,
        message: String,
        #[arg(long, help = "Allow sending to open chats (higher ban risk)")]
        force: bool,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Send a message to memo chat (나와의 채팅) via LOCO protocol
    SendMe {
        message: String,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Watch real-time messages via LOCO protocol
    Watch {
        #[arg(long, help = "Filter by chat ID")]
        chat_id: Option<i64>,
        #[arg(long, help = "Show raw BSON body")]
        raw: bool,
        #[arg(long, help = "Send read receipts (NOTIREAD) for incoming messages")]
        read_receipt: bool,
        #[arg(
            long,
            default_value_t = 10,
            help = "Max reconnect attempts (0 = no reconnect)"
        )]
        max_reconnect: u32,
        #[arg(
            long,
            default_value_t = 2,
            help = "Initial reconnect backoff delay in seconds (doubles each attempt)"
        )]
        reconnect_delay: u64,
        #[arg(
            long,
            default_value_t = 60,
            help = "Maximum reconnect backoff delay in seconds"
        )]
        reconnect_max_delay: u64,
        #[arg(long, help = "Auto-download media attachments")]
        download_media: bool,
        #[arg(
            long,
            default_value = "downloads",
            help = "Directory for downloaded media"
        )]
        download_dir: String,
        #[arg(long, help = "Run a local shell command for matched events")]
        hook_cmd: Option<String>,
        #[arg(long, help = "POST matched events to a webhook URL")]
        webhook_url: Option<String>,
        #[arg(
            long = "webhook-header",
            help = "Additional webhook header in 'Name: Value' format"
        )]
        webhook_header: Vec<String>,
        #[arg(
            long = "webhook-signing-secret",
            help = "Sign webhook payloads with HMAC-SHA256 and emit X-OpenKakao-Timestamp / X-OpenKakao-Signature"
        )]
        webhook_signing_secret: Option<String>,
        #[arg(long = "hook-chat-id", help = "Only trigger hooks for these chat IDs")]
        hook_chat_id: Vec<i64>,
        #[arg(
            long = "hook-keyword",
            help = "Only trigger hooks when message text contains keyword"
        )]
        hook_keyword: Vec<String>,
        #[arg(
            long = "hook-type",
            help = "Only trigger hooks for these message type codes"
        )]
        hook_type: Vec<i32>,
        #[arg(
            long = "webhook-format",
            help = "Webhook payload format: raw (default), slack, discord"
        )]
        webhook_format: Option<String>,
        #[arg(long, help = "Stop watch when a hook command fails")]
        hook_fail_fast: bool,
        #[arg(long, help = "Resume from last saved watch state (last-seen log IDs)")]
        resume: bool,
        #[arg(
            long,
            help = "Capture unknown push packets as JSON for protocol reverse engineering"
        )]
        capture: bool,
    },
    /// Send a photo via LOCO protocol (alias for send-file)
    SendPhoto {
        chat_id: i64,
        /// Path to image file (JPEG/PNG/GIF)
        file: String,
        #[arg(long, help = "Allow sending to open chats (higher ban risk)")]
        force: bool,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Send a file (photo/video/document) via LOCO protocol
    SendFile {
        chat_id: i64,
        /// Path to file
        file: String,
        #[arg(long, help = "Allow sending to open chats (higher ban risk)")]
        force: bool,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Delete a message via LOCO protocol
    Delete {
        chat_id: i64,
        log_id: i64,
        #[arg(long, help = "Allow deleting in open chats (higher ban risk)")]
        force: bool,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Mark messages as read up to a specific message via LOCO protocol.
    /// Currently research-available; requires allow_loco_write like other writes.
    MarkRead {
        chat_id: i64,
        log_id: i64,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Add a reaction to a message via LOCO ACTION
    React {
        chat_id: i64,
        log_id: i64,
        /// Reaction type (1 = like)
        #[arg(short = 't', long, default_value = "1")]
        reaction_type: i32,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Edit a message via LOCO REWRITE (may return -203 on macOS)
    Edit {
        chat_id: i64,
        log_id: i64,
        message: String,
        #[arg(long, help = "Allow editing in open chats (higher ban risk)")]
        force: bool,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Download media attachment from a specific message
    Download {
        chat_id: i64,
        log_id: i64,
        #[arg(short = 'o', long, help = "Output directory (default: downloads)")]
        output_dir: Option<String>,
        #[arg(
            long,
            help = "Resolve the exact attachment from the read-only local database"
        )]
        local: bool,
        #[arg(
            long,
            requires = "local",
            value_parser = clap::value_parser!(i64).range(1..),
            help = "Require the exact local row to belong to this numeric author"
        )]
        expected_author_id: Option<i64>,
    },
    /// Sync messages to local SQLite cache for offline search
    Cache {
        chat_id: i64,
        #[arg(long, help = "Max messages to sync (default: all)")]
        limit: Option<usize>,
    },
    /// Search locally cached messages
    CacheSearch {
        query: String,
        #[arg(long, help = "Limit search to this chat")]
        chat_id: Option<i64>,
        #[arg(short = 'n', long, default_value_t = 30)]
        count: usize,
    },
    /// Show local cache statistics
    CacheStats,
    #[command(hide = true)]
    /// List chat rooms via LOCO protocol (legacy command)
    LocoChats {
        #[arg(short = 'a', long = "all")]
        show_all: bool,
    },
    #[command(hide = true)]
    /// Read messages via LOCO protocol (legacy command)
    LocoRead {
        chat_id: i64,
        #[arg(short = 'n', long, default_value_t = 30)]
        count: i32,
        #[arg(long, help = "Resume from this logId (cursor from previous run)")]
        cursor: Option<i64>,
        #[arg(long, help = "Filter messages after this date (YYYY-MM-DD)")]
        since: Option<String>,
        #[arg(long, help = "Fetch all available messages")]
        all: bool,
        #[arg(
            long,
            default_value_t = 100,
            help = "Delay between batches in ms (rate limit)"
        )]
        delay_ms: u64,
        #[arg(long, help = "Allow operations on open chats (higher ban risk)")]
        force: bool,
    },
    #[command(hide = true)]
    /// List members of a chat room via LOCO protocol (legacy command)
    LocoMembers { chat_id: i64 },
    #[command(hide = true)]
    /// Get chat room info via LOCO protocol (legacy command)
    LocoChatinfo { chat_id: i64 },
    /// List blocked/hidden-style members via LOCO protocol
    LocoBlocked,
    /// Probe a LOCO method and print the raw response
    Probe {
        method: String,
        #[arg(long, help = "JSON object body to send with the probe")]
        body: Option<String>,
        #[arg(
            long,
            help = "Wait for push packets instead of direct response (extends timeout to 10s)"
        )]
        capture_pushes: bool,
    },
    #[command(hide = true)]
    /// Inspect cached friend/profile hints for LOCO reverse engineering
    ProfileHints {
        #[arg(
            long,
            help = "Include a local KakaoTalk app-state file snapshot for before/after diffing"
        )]
        app_state: bool,
        #[arg(
            long,
            help = "Compare the current app-state snapshot against a previous profile-hints JSON file"
        )]
        app_state_diff: Option<String>,
        #[arg(
            long,
            help = "Also build a local LOCO friend graph and correlate cache hints"
        )]
        local_graph: bool,
        #[arg(long, help = "Generate SYNCMAINPF body candidates for this user")]
        user_id: Option<i64>,
        #[arg(
            long,
            help = "Probe generated SYNCMAINPF candidates in one LOCO session"
        )]
        probe_syncmainpf: bool,
        #[arg(
            long,
            help = "Probe generated UPLINKPROF candidates in one LOCO session"
        )]
        probe_uplinkprof: bool,
    },
    #[command(hide = true)]
    /// Probe an arbitrary LOCO method and print the raw response (legacy command)
    LocoProbe {
        method: String,
        #[arg(long, help = "JSON object body to send with the probe")]
        body: Option<String>,
    },
    /// Watch Cache.db for fresh tokens (poll every N seconds)
    WatchCache {
        #[arg(long, default_value_t = 10)]
        interval: u64,
    },
    /// List chats from local KakaoTalk database (no server contact, safe)
    LocalChats {
        #[arg(short = 'n', long, default_value_t = 50)]
        limit: usize,
        #[arg(long, help = "List KakaoTalk group chat titles only")]
        groups: bool,
    },
    /// Read messages from local KakaoTalk database (no server contact, safe)
    LocalRead {
        chat_id: i64,
        #[arg(short = 'n', long, default_value_t = 30)]
        count: usize,
        #[arg(long, help = "Filter messages after this date (YYYY-MM-DD)")]
        since: Option<String>,
    },
    #[command(name = "local-poll", hide = true)]
    /// Stream bounded local database polls as versioned JSONL (no server contact).
    LocalPoll {
        #[arg(long = "chat-id", value_parser = parse_local_poll_chat_id)]
        chat_id: i64,
        #[arg(long, default_value_t = 50, value_parser = parse_local_poll_count)]
        count: usize,
        #[arg(long, default_value_t = 1.0, value_parser = parse_local_poll_interval)]
        interval: f64,
    },
    /// Search messages in local KakaoTalk database (no server contact, safe)
    LocalSearch {
        query: String,
        #[arg(short = 'n', long, default_value_t = 20)]
        count: usize,
        #[arg(long = "chat-id", value_parser = parse_local_poll_chat_id)]
        chat_id: Option<i64>,
    },
    /// Show local KakaoTalk database schema
    LocalSchema,
    /// Build or refresh a local per-chat context index from a KakaoTalk CSV export
    ContextIndex {
        #[arg(long)]
        input: String,
        #[arg(long)]
        chat: String,
        #[arg(long)]
        db: Option<String>,
        /// Room delivery queue whose terminal `sent` jobs are the second send
        /// authority. A reply confirmed only in the queue would otherwise be
        /// learned as an owner style sample on import.
        #[arg(long)]
        queue: Option<String>,
    },
    #[command(name = "context-sync-local", hide = true)]
    /// Incrementally refresh a chat's context index from the local database.
    ContextSyncLocal {
        #[arg(long = "chat-id", value_parser = parse_local_poll_chat_id)]
        chat_id: i64,
        #[arg(long)]
        chat: String,
        #[arg(long)]
        db: Option<String>,
        /// Room delivery queue whose terminal `sent` jobs prove which outgoing
        /// self rows the assistant wrote. Omitted, only the decision ledger is
        /// consulted.
        #[arg(long)]
        queue: Option<String>,
        /// Index only 최연우 interest topics (stocks/coins/investing/real estate/auction/business/AI).
        #[arg(long = "interest-only")]
        interest_only: bool,
    },
    #[command(name = "context-repair-self-sends", hide = true)]
    /// Mark owner style samples that the assistant itself sent as ineligible.
    ContextRepairSelfSends {
        #[arg(long = "chat-id", value_parser = parse_local_poll_chat_id)]
        chat_id: i64,
        #[arg(long)]
        chat: String,
        #[arg(long)]
        db: Option<String>,
        /// Room delivery queue holding terminal `sent` jobs (delivery authority).
        #[arg(long)]
        queue: Option<String>,
        /// Report matches without writing anything.
        #[arg(long = "dry-run")]
        dry_run: bool,
    },
    /// Search the local per-chat context index without network access
    ContextSearch {
        query: String,
        #[arg(long)]
        chat: Option<String>,
        #[arg(long, default_value = "hybrid", value_parser = ["keyword", "vector", "hybrid"])]
        mode: String,
        #[arg(short = 'n', long, default_value_t = 10)]
        limit: usize,
        #[arg(long)]
        source: Option<String>,
        #[arg(long)]
        db: Option<String>,
    },
    /// Render read-only turn receipts from a room's reply-evidence ledger
    #[command(name = "reply-receipt")]
    ReplyReceipt {
        /// A single room's reply-evidence.jsonl
        #[arg(long)]
        ledger: Option<String>,
        /// State root holding rooms/<chat_id>/reply-evidence.jsonl
        #[arg(long = "state-root")]
        state_root: Option<String>,
        /// Room ids to read under --state-root
        #[arg(long = "chat-id", value_delimiter = ',')]
        chat_id: Vec<String>,
        /// How many recent turns to describe
        #[arg(short = 'n', long, default_value_t = openkakao_cli::reply_receipt::DEFAULT_RECEIPT_LIMIT)]
        limit: usize,
    },
    #[command(name = "context-reply-bundle", hide = true)]
    /// Retrieve one bounded, snapshot-consistent local context evidence bundle.
    ContextReplyBundle {
        query: String,
        #[arg(long)]
        chat: String,
        #[arg(long = "chat-id")]
        chat_id: Option<i64>,
        #[arg(long = "current-log-id", value_parser = parse_context_log_id)]
        current_log_id: Option<i64>,
        /// Additional live burst rows to exclude from context and decision evidence.
        #[arg(
            long = "exclude-log-id",
            value_parser = parse_context_log_id,
            value_delimiter = ','
        )]
        exclude_log_id: Vec<i64>,
        #[arg(long)]
        recipient: Option<String>,
        #[arg(long)]
        source: Option<String>,
        #[arg(long)]
        db: Option<String>,
    },
    /// Search only the dedicated 최연우 style vector table
    ContextStyleSearch {
        query: String,
        #[arg(short = 'n', long, default_value_t = 10)]
        limit: usize,
        #[arg(long)]
        db: Option<String>,
        #[arg(long)]
        chat: Option<String>,
    },
    /// Show Choi Yeonwoo's learned reply register toward one recipient
    ContextRecipientStyle {
        #[arg(long)]
        chat: String,
        #[arg(long)]
        recipient: String,
        #[arg(long)]
        source: Option<String>,
        #[arg(long)]
        db: Option<String>,
    },
    /// Show Choi Yeonwoo's response-time statistics from the local vector database
    ContextResponseTime {
        #[arg(long)]
        chat: String,
        #[arg(long, default_value = "최연우")]
        user: String,
        #[arg(long)]
        source: Option<String>,
        #[arg(long)]
        db: Option<String>,
    },
    /// Search previous reply decisions stored in the local context vector database
    ContextReplySearch {
        query: String,
        #[arg(long)]
        chat: String,
        #[arg(short = 'n', long, default_value_t = 8)]
        limit: usize,
        #[arg(long)]
        db: Option<String>,
    },
    /// Record a structured reply decision in the local context vector database
    ContextReplyRecord {
        #[arg(long)]
        record: String,
        #[arg(long)]
        db: Option<String>,
    },
    /// Update the delivery status of a structured reply decision
    ContextReplyUpdate {
        #[arg(long)]
        event_id: String,
        #[arg(long)]
        status: String,
        #[arg(long)]
        reply: Option<String>,
        #[arg(long)]
        sent_at: Option<String>,
        #[arg(long)]
        db: Option<String>,
    },
    #[command(name = "ax-service-scrape-once", hide = true)]
    AxServiceScrapeOnce,
    /// Send a message via AX automation (no server contact, drives KakaoTalk's UI directly)
    LocalSend {
        chat_name: String,
        message: String,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
        /// Quote an already-visible message via the KakaoTalk context menu
        /// (AXShowMenu + AXPress on "답장"), then send.
        #[arg(long = "reply-to")]
        reply_to: Option<String>,
        #[arg(long, hide = true, conflicts_with = "dry_run")]
        preflight: bool,
    },
    /// Delete a visible message via AX context menu (모두에게서 삭제). No LOCO.
    LocalDelete {
        chat_name: String,
        /// Visible message text or unique substring already shown in the chat window.
        source: String,
        #[arg(long, short = 'y', help = "Skip confirmation prompt")]
        yes: bool,
        #[arg(long, help = "Preview the action without executing")]
        dry_run: bool,
    },
    /// Read recent messages via AX automation (no server contact, no local
    /// DB access — scrapes the open KakaoTalk chat window directly)
    AxRead {
        chat_name: String,
        #[arg(short = 'n', long, default_value_t = 20)]
        count: usize,
    },
    /// Read-only AX diagnostic: report whether an already-open chat window's
    /// message composer is discoverable. Performs no mutation and never opens
    /// a chat window, so it is safe to run against a live session.
    AxProbe {
        #[arg(long)]
        chat: String,
        /// Print the bounded AX subtree of the chat window instead of the
        /// composer summary. Read-only diagnostic; never mutates or opens a chat.
        #[arg(long)]
        dump: bool,
        /// Node budget for --dump. Bounds the walk so a virtualized transcript
        /// cannot stall the caller.
        #[arg(long, default_value_t = 400)]
        max_nodes: usize,
    },
    /// Watch for incoming KakaoTalk messages via AX (no server contact,
    /// background) and fire hooks/webhooks on unread-count increases
    AxWatch {
        #[arg(long, default_value_t = 1)]
        interval: u64,
        #[arg(long)]
        hook_cmd: Option<String>,
        #[arg(long)]
        webhook_url: Option<String>,
        #[arg(long = "webhook-header")]
        webhook_header: Vec<String>,
        #[arg(long)]
        webhook_signing_secret: Option<String>,
        #[arg(long, default_value = "raw")]
        webhook_format: String,
        #[arg(long = "hook-chat")]
        hook_chat: Vec<String>,
        #[arg(long = "hook-keyword")]
        hook_keyword: Vec<String>,
        #[arg(long)]
        hook_fail_fast: bool,
        #[arg(long, hide = true)]
        service_mode: bool,
        #[arg(long, hide = true)]
        status_path: Option<String>,
        #[arg(long, hide = true)]
        log_path: Option<String>,
        #[arg(long, hide = true)]
        hook_path: Option<String>,
    },
    /// Run database-authoritative automatic replies for one or more exact
    /// local chat-room selectors in the foreground.
    AutoReply {
        /// Exact chat selector. Repeat the flag or separate selectors with
        /// commas. Use id:<id>, name:<exact-name>, or bind:<id>:<exact-name>
        /// when KakaoTalk leaves a group room's local database name empty.
        #[arg(long = "chat")]
        chat: Vec<String>,
        /// Resolve and validate targets without probing the model, starting
        /// workers, or sending replies.
        #[arg(long)]
        check: bool,
        /// Override the configured self nickname for this foreground run.
        #[arg(long)]
        self_nickname: Option<String>,
        /// Override the configured reply-author allowlist for this run.
        #[arg(long = "reply-author")]
        reply_author: Vec<String>,
        #[arg(long, default_value_t = 1.0)]
        interval: f64,
        /// Optional reply-model override. Interactive terminals can omit this
        /// and pick from the arrow-key menu. Non-interactive runs must pass
        /// --model or already have a working configured model.
        #[arg(long = "model")]
        model: Option<String>,
    },
    /// Stage or inspect the Kakao-blind session-monitor host. Never owns AX send.
    AutoReplyHost {
        #[arg(long)]
        bake: bool,
        #[arg(long)]
        status: bool,
        #[arg(long)]
        disable: bool,
        /// Kakao-blind LaunchAgent tick. Opens Terminal only; never owns AX.
        #[arg(long)]
        tick: bool,
        /// Exact chat selectors used only when baking a new immutable runtime.
        #[arg(long = "chat")]
        chat: Vec<String>,
        #[arg(long, hide = true)]
        manifest: Option<std::path::PathBuf>,
        #[arg(long = "state-root", hide = true)]
        state_root: Option<std::path::PathBuf>,
    },
    /// Run diagnostic checks on KakaoTalk installation and connectivity
    Doctor {
        /// Also test LOCO booking connectivity (makes network request)
        #[arg(long)]
        loco: bool,
        /// Run the pure-local self-check (닥터): diagnose + bounded auto-repair,
        /// no external contact (R7)
        #[arg(long)]
        self_check: bool,
    },
    /// Build the local RAG dataset from KakaoTalk conversations (no server contact)
    Dataset {
        #[command(subcommand)]
        command: DatasetCommand,
    },
    /// Run LLM quality/speed experiments over the available providers (R4)
    Experiment {
        #[command(subcommand)]
        command: ExperimentCommand,
    },
}

#[derive(Subcommand, Debug)]
enum DatasetCommand {
    /// Build the dataset locally from the KakaoTalk database (R3).
    Build {
        /// Build with a local-only embedder. This is the only supported mode:
        /// the dataset is never sent to an external network (R3.12).
        #[arg(long)]
        local_only: bool,
    },
}

#[derive(Subcommand, Debug)]
enum ExperimentCommand {
    /// Run the fixed question set against every discovered LLM with a prompt
    /// version, recording latency and local quality score (R4.2–R4.6, R4.8).
    Run {
        /// The prompt version to run (e.g. `v1`, `v2`). Re-running with an
        /// improved version enables comparison against earlier results (R4.7).
        #[arg(long)]
        prompt: String,
    },
    /// Compare two previously-run prompt versions by quality and speed (R4.7).
    Compare {
        /// The first prompt version to compare.
        version_a: String,
        /// The second prompt version to compare.
        version_b: String,
    },
}
fn is_local_only_command(command: &Commands) -> bool {
    matches!(
        command,
        Commands::LocalChats { .. }
            | Commands::LocalRead { .. }
            | Commands::LocalPoll { .. }
            | Commands::LocalSearch { .. }
            | Commands::LocalSchema
            | Commands::ContextIndex { .. }
            | Commands::ContextSyncLocal { .. }
            | Commands::ContextSearch { .. }
            | Commands::ReplyReceipt { .. }
            | Commands::ContextReplyBundle { .. }
            | Commands::ContextStyleSearch { .. }
            | Commands::ContextRecipientStyle { .. }
            | Commands::ContextReplySearch { .. }
            | Commands::ContextReplyRecord { .. }
            | Commands::ContextReplyUpdate { .. }
            | Commands::AxServiceScrapeOnce
            | Commands::LocalSend { .. }
            | Commands::LocalDelete { .. }
            | Commands::AxRead { .. }
            | Commands::AxProbe { .. }
            | Commands::AxWatch { .. }
            | Commands::AutoReply { .. }
            | Commands::AutoReplyHost { .. }
            | Commands::Dataset { .. }
            | Commands::Experiment { .. }
    )
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    // The record window's data source must work while the config is broken:
    // "the config is invalid" is exactly one of the facts it has to report.
    if let Commands::ReplyReceipt {
        ledger,
        state_root,
        chat_id,
        limit,
    } = &cli.command
    {
        return commands::reply_receipt::run(
            ledger.clone(),
            state_root.clone(),
            chat_id,
            *limit,
            cli.json,
        );
    }
    let service_bootstrap_status_path = validate_service_bootstrap_paths(&cli.command)?;
    let config = match load_config() {
        Ok(config) => config,
        Err(error) => {
            if let Some(status_path) = service_bootstrap_status_path {
                let _ = auto_reply_service::write_config_invalid_status(status_path);
            }
            return Err(error);
        }
    };
    set_auth_policy(AuthPolicy::from_config(&config.auth));
    let json = cli.json;
    let unattended = cli.unattended || config.mode.unattended;
    let allow_non_interactive_send =
        cli.allow_non_interactive_send || config.send.allow_non_interactive;
    let allow_watch_side_effects = cli.allow_watch_side_effects || config.watch.allow_side_effects;
    let min_unattended_send_interval_secs = config
        .safety
        .min_unattended_send_interval_secs
        .unwrap_or(10);
    let min_hook_interval_secs = config.safety.min_hook_interval_secs.unwrap_or(2);
    let min_webhook_interval_secs = config.safety.min_webhook_interval_secs.unwrap_or(2);
    let hook_timeout_secs = config.safety.hook_timeout_secs.unwrap_or(20);
    let webhook_timeout_secs = config.safety.webhook_timeout_secs.unwrap_or(10);
    let no_prefix = if cli.no_prefix {
        true
    } else {
        matches!(config.send.default_prefix, Some(false))
    };

    // Respect NO_COLOR env var (https://no-color.org/) and --no-color flag
    if cli.no_color || std::env::var("NO_COLOR").is_ok() || json {
        NO_COLOR.store(true, Ordering::Relaxed);
    }

    // Server-login warning — printed to stderr so it never corrupts JSON on
    // stdout. Silenced with OPENKAKAO_CLI_NO_DEPRECATION=1 for scripted
    // local-only use. `local-send`/`ax-read` need neither login nor this
    // warning, since they never touch Kakao's servers.
    if std::env::var_os("OPENKAKAO_CLI_NO_DEPRECATION").is_none()
        && !is_local_only_command(&cli.command)
    {
        eprintln!("⚠️  Server login (login --save / login --manual) is broken on recent");
        eprintln!("   KakaoTalk macOS builds. Do NOT repeatedly retry login on an unregistered");
        eprintln!("   device — it can get your account's sub-device login blocked. Prefer");
        eprintln!(
            "   'local-send'/'ax-read'/'local-chats' — no server contact needed. See README."
        );
    }

    match cli.command {
        Commands::Auth => commands::auth::cmd_auth(json)?,
        Commands::AuthStatus => commands::auth::cmd_auth_status(json)?,
        Commands::Login {
            save,
            manual,
            email,
            password,
            app_version,
        } => {
            if manual {
                commands::auth::cmd_login_manual(save, email, password, app_version)?
            } else {
                commands::auth::cmd_login(save)?
            }
        }
        Commands::Me => commands::rest::cmd_me(json)?,
        Commands::Friends {
            favorites,
            hidden,
            search,
            local,
            chat_id,
            user_id,
        } => commands::rest::cmd_friends(favorites, hidden, search, local, chat_id, user_id, json)?,
        Commands::Chats {
            show_all,
            unread,
            search,
            chat_type,
            rest,
        } => commands::chats::cmd_chats(show_all, unread, search, chat_type, rest, json)?,
        Commands::Read {
            chat_id,
            count,
            before,
            cursor,
            since,
            all,
            delay_ms,
            force,
            rest,
        } => commands::read::cmd_read(
            chat_id,
            ReadCommandOptions {
                count,
                cursor: cursor.or(before),
                since,
                all,
                delay_ms,
                force,
                rest,
                json,
            },
        )?,
        Commands::Members {
            chat_id,
            rest,
            full,
        } => commands::members::cmd_members(chat_id, rest, full, json)?,
        Commands::Chatinfo { chat_id } => commands::rest::cmd_chatinfo(chat_id, json)?,
        Commands::Settings => commands::rest::cmd_settings(json)?,
        Commands::Scrap { url } => commands::rest::cmd_scrap(&url, json)?,
        Commands::Profile {
            user_id,
            chat_id,
            local,
        } => commands::profile::cmd_profile(user_id, chat_id, local, json)?,
        Commands::Favorite { user_id } => commands::rest::cmd_favorite(user_id, json)?,
        Commands::Unfavorite { user_id } => commands::rest::cmd_unfavorite(user_id, json)?,
        Commands::Hide { user_id } => commands::rest::cmd_hide(user_id, json)?,
        Commands::Unhide { user_id } => commands::rest::cmd_unhide(user_id, json)?,
        Commands::Profiles => commands::rest::cmd_profiles(json)?,
        Commands::Keywords => commands::rest::cmd_keywords(json)?,
        Commands::Unread => commands::rest::cmd_unread(json)?,
        Commands::Export {
            chat_id,
            format,
            output,
        } => commands::rest::cmd_export(chat_id, &format, output.as_deref(), json)?,
        Commands::Search { chat_id, query } => commands::rest::cmd_search(chat_id, &query, json)?,
        Commands::Stats {
            chat_id,
            limit,
            since,
        } => commands::analytics::cmd_stats(chat_id, limit, since.as_deref(), json)?,
        Commands::Completions { shell } => {
            generate(
                shell,
                &mut Cli::command(),
                "openkakao-cli",
                &mut io::stdout(),
            );
        }
        Commands::Renew => commands::auth::cmd_renew(json)?,
        Commands::Relogin {
            fresh_xvc,
            password,
            email,
        } => commands::auth::cmd_relogin(json, fresh_xvc, password, email)?,
        Commands::LocoTest => {
            eprintln!("[deprecated] 'loco-test' is now hidden. Prefer 'doctor --loco'.");
            commands::auth::cmd_loco_test()?
        }
        Commands::Send {
            chat_id,
            message,
            force,
            yes,
            dry_run,
        } => {
            let msg = format_outgoing_message(&message, no_prefix);
            if dry_run {
                eprintln!(
                    "[dry-run] Would send to chat {}: \"{}\"",
                    chat_id,
                    util::truncate(&msg, 80)
                );
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true,
                        "action": "send",
                        "chat_id": chat_id,
                        "message": msg,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_send(commands::send::SendOptions {
                    chat_id,
                    message: msg,
                    force,
                    skip_confirm: yes,
                    unattended,
                    allow_non_interactive: allow_non_interactive_send,
                    min_interval_secs: min_unattended_send_interval_secs,
                    json,
                })?
            }
        }
        Commands::SendMe {
            message,
            yes,
            dry_run,
        } => {
            let reader = local_db::LocalDbReader::open()
                .context("Failed to open local DB to find memo chat")?;
            let memo_id = reader
                .find_memo_chat_id()?
                .context("Could not find memo chat (나와의 채팅) in local database")?;
            let msg = format_outgoing_message(&message, no_prefix);
            if dry_run {
                eprintln!(
                    "[dry-run] Would send to memo chat {}: \"{}\"",
                    memo_id,
                    util::truncate(&msg, 80)
                );
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true,
                        "action": "send_me",
                        "chat_id": memo_id,
                        "message": msg,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_send(commands::send::SendOptions {
                    chat_id: memo_id,
                    message: msg,
                    force: false,
                    skip_confirm: yes,
                    unattended,
                    allow_non_interactive: allow_non_interactive_send,
                    min_interval_secs: min_unattended_send_interval_secs,
                    json,
                })?
            }
        }
        Commands::SendPhoto {
            chat_id,
            file,
            force,
            yes,
            dry_run,
        } => {
            if dry_run {
                eprintln!("[dry-run] Would send photo '{}' to chat {}", file, chat_id);
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true, "action": "send_photo", "chat_id": chat_id, "file": file,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_send_file(commands::send::SendFileOptions {
                    chat_id,
                    file_path: file,
                    force,
                    skip_confirm: yes,
                    unattended,
                    allow_non_interactive: allow_non_interactive_send,
                    min_interval_secs: min_unattended_send_interval_secs,
                    json,
                })?
            }
        }
        Commands::SendFile {
            chat_id,
            file,
            force,
            yes,
            dry_run,
        } => {
            if dry_run {
                eprintln!("[dry-run] Would send file '{}' to chat {}", file, chat_id);
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true, "action": "send_file", "chat_id": chat_id, "file": file,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_send_file(commands::send::SendFileOptions {
                    chat_id,
                    file_path: file,
                    force,
                    skip_confirm: yes,
                    unattended,
                    allow_non_interactive: allow_non_interactive_send,
                    min_interval_secs: min_unattended_send_interval_secs,
                    json,
                })?
            }
        }
        Commands::Delete {
            chat_id,
            log_id,
            force,
            yes,
            dry_run,
        } => {
            if dry_run {
                eprintln!(
                    "[dry-run] Would delete message {} from chat {}",
                    log_id, chat_id
                );
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true, "action": "delete", "chat_id": chat_id, "log_id": log_id,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_delete(commands::send::DeleteOptions {
                    chat_id,
                    log_id,
                    force,
                    skip_confirm: yes,
                    unattended,
                    allow_non_interactive: allow_non_interactive_send,
                    min_interval_secs: min_unattended_send_interval_secs,
                    json,
                })?
            }
        }
        Commands::MarkRead {
            chat_id,
            log_id,
            yes: _,
            dry_run,
        } => {
            if dry_run {
                eprintln!(
                    "[dry-run] Would mark chat {} read up to {}",
                    chat_id, log_id
                );
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true, "action": "mark-read", "chat_id": chat_id, "log_id": log_id,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_mark_read(commands::send::MarkReadOptions {
                    chat_id,
                    log_id,
                    json,
                })?
            }
        }
        Commands::React {
            chat_id,
            log_id,
            reaction_type,
            dry_run,
        } => {
            if dry_run {
                eprintln!(
                    "[dry-run] Would react (type={}) to message {} in chat {}",
                    reaction_type, log_id, chat_id
                );
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true, "action": "react", "chat_id": chat_id, "log_id": log_id, "reaction_type": reaction_type,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_react(commands::send::ReactOptions {
                    chat_id,
                    log_id,
                    reaction_type,
                    json,
                })?
            }
        }
        Commands::Edit {
            chat_id,
            log_id,
            message,
            force,
            yes,
            dry_run,
        } => {
            let msg = format_outgoing_message(&message, no_prefix);
            if dry_run {
                eprintln!(
                    "[dry-run] Would edit message {} in chat {}: \"{}\"",
                    log_id,
                    chat_id,
                    util::truncate(&msg, 80)
                );
                if json {
                    util::output_json(&serde_json::json!({
                        "dry_run": true, "action": "edit", "chat_id": chat_id, "log_id": log_id, "message": msg,
                    }))?;
                }
            } else {
                require_loco_write(&config)?;
                commands::send::cmd_edit(commands::send::EditOptions {
                    chat_id,
                    log_id,
                    message: msg,
                    force,
                    skip_confirm: yes,
                    unattended,
                    allow_non_interactive: allow_non_interactive_send,
                    min_interval_secs: min_unattended_send_interval_secs,
                    json,
                })?
            }
        }
        Commands::Watch {
            chat_id,
            raw,
            read_receipt,
            max_reconnect,
            reconnect_delay,
            reconnect_max_delay,
            download_media,
            download_dir,
            hook_cmd,
            webhook_url,
            webhook_header,
            webhook_signing_secret,
            webhook_format,
            hook_chat_id,
            hook_keyword,
            hook_type,
            hook_fail_fast,
            resume,
            capture,
        } => commands::watch::cmd_watch(WatchOptions {
            unattended,
            allow_side_effects: allow_watch_side_effects,
            filter_chat_id: chat_id,
            raw,
            read_receipt,
            max_reconnect: config.watch.default_max_reconnect.unwrap_or(max_reconnect),
            reconnect_delay_secs: reconnect_delay,
            reconnect_max_delay_secs: reconnect_max_delay,
            download_media,
            download_dir,
            hook_cmd,
            webhook_url,
            webhook_headers: webhook_header,
            webhook_signing_secret,
            hook_chat_ids: hook_chat_id,
            hook_keywords: hook_keyword,
            hook_types: hook_type,
            hook_fail_fast,
            min_hook_interval_secs,
            min_webhook_interval_secs,
            hook_timeout_secs,
            webhook_timeout_secs,
            allow_insecure_webhooks: config.safety.allow_insecure_webhooks,
            webhook_format: WebhookFormat::from_str_opt(webhook_format.as_deref())?,
            resume,
            json,
            capture,
        })?,
        Commands::Download {
            chat_id,
            log_id,
            output_dir,
            local,
            expected_author_id,
        } => commands::download::cmd_download(
            chat_id,
            log_id,
            output_dir.as_deref(),
            local,
            expected_author_id,
            json,
        )?,
        Commands::Cache { chat_id, limit } => commands::analytics::cmd_cache(chat_id, limit, json)?,
        Commands::CacheSearch {
            query,
            chat_id,
            count,
        } => commands::analytics::cmd_cache_search(&query, chat_id, count, json)?,
        Commands::CacheStats => commands::analytics::cmd_cache_stats(json)?,
        Commands::LocoChats { show_all } => {
            eprintln!("[deprecated] 'loco-chats' is now hidden. Prefer 'chats' (LOCO by default).");
            commands::chats::cmd_loco_chats(show_all, false, None, None, json)?
        }
        Commands::LocoRead {
            chat_id,
            count,
            cursor,
            since,
            all,
            delay_ms,
            force,
        } => {
            eprintln!("[deprecated] 'loco-read' is now hidden. Prefer 'read' (LOCO by default).");
            commands::read::cmd_loco_read(
                chat_id,
                &commands::read::ReadCommandOptions {
                    count: count as usize,
                    cursor,
                    since,
                    all,
                    delay_ms,
                    force,
                    rest: false,
                    json,
                },
            )?
        }
        Commands::LocoMembers { chat_id } => {
            eprintln!(
                "[deprecated] 'loco-members' is now hidden. Prefer 'members' (LOCO by default)."
            );
            commands::members::cmd_loco_members(chat_id, false, json)?
        }
        Commands::LocoChatinfo { chat_id } => {
            eprintln!("[deprecated] 'loco-chatinfo' is now hidden. Prefer 'chatinfo'.");
            commands::probe::cmd_loco_chatinfo(chat_id, json)?
        }
        Commands::LocoBlocked => commands::members::cmd_loco_blocked(json)?,
        Commands::Probe {
            method,
            body,
            capture_pushes,
        } => commands::probe::cmd_loco_probe(&method, body.as_deref(), json, capture_pushes)?,
        Commands::ProfileHints {
            app_state,
            app_state_diff,
            local_graph,
            user_id,
            probe_syncmainpf,
            probe_uplinkprof,
        } => commands::profile::cmd_profile_hints(
            app_state,
            app_state_diff,
            local_graph,
            user_id,
            probe_syncmainpf,
            probe_uplinkprof,
            json,
        )?,
        Commands::LocoProbe { method, body } => {
            eprintln!("[deprecated] 'loco-probe' is now hidden. Prefer 'probe'.");
            commands::probe::cmd_loco_probe(&method, body.as_deref(), json, false)?
        }
        Commands::LocalChats { limit, groups } => {
            commands::local::cmd_local_chats(limit, groups, json)?;
            // `--groups` has always returned before the completion promise.
            if groups {
                return Ok(());
            }
        }
        Commands::LocalRead {
            chat_id,
            count,
            since,
        } => commands::local::cmd_local_read(chat_id, count, since, json)?,
        Commands::LocalPoll {
            chat_id,
            count,
            interval,
        } => commands::local::cmd_local_poll(chat_id, count, interval)?,
        Commands::LocalSearch {
            query,
            count,
            chat_id,
        } => commands::local::cmd_local_search(query, count, chat_id, json)?,
        Commands::LocalSchema => commands::local::cmd_local_schema(json)?,
        Commands::ContextIndex {
            input,
            chat,
            db,
            queue,
        } => commands::context::cmd_context_index(input, chat, db, queue, json)?,
        Commands::ContextSyncLocal {
            chat_id,
            chat,
            db,
            queue,
            interest_only,
        } => commands::context_sync_local::cmd_context_sync_local(
            chat_id,
            chat,
            db,
            queue,
            interest_only,
            json,
        )?,
        Commands::ContextRepairSelfSends {
            chat_id,
            chat,
            db,
            queue,
            dry_run,
        } => commands::context::cmd_context_repair_self_sends(
            chat_id, chat, db, queue, dry_run, json,
        )?,
        Commands::ContextSearch {
            query,
            chat,
            mode,
            limit,
            source,
            db,
        } => commands::context::cmd_context_search(query, chat, mode, limit, source, db, json)?,
        Commands::ContextReplyBundle {
            query,
            chat,
            chat_id,
            current_log_id,
            exclude_log_id,
            recipient,
            source,
            db,
        } => commands::context::cmd_context_reply_bundle(
            query,
            chat,
            chat_id,
            current_log_id,
            exclude_log_id,
            recipient,
            source,
            db,
        )?,
        Commands::ContextStyleSearch {
            query,
            limit,
            db,
            chat,
        } => commands::context::cmd_context_style_search(query, limit, db, chat, json)?,
        Commands::ContextRecipientStyle {
            chat,
            recipient,
            source,
            db,
        } => commands::context::cmd_context_recipient_style(chat, recipient, source, db, json)?,
        Commands::ContextResponseTime {
            chat,
            user,
            source,
            db,
        } => commands::context::cmd_context_response_time(chat, user, source, db, json)?,
        Commands::ContextReplySearch {
            query,
            chat,
            limit,
            db,
        } => commands::context::cmd_context_reply_search(query, chat, limit, db, json)?,
        Commands::ContextReplyRecord { record, db } => {
            commands::context::cmd_context_reply_record(record, db, json)?
        }
        Commands::ContextReplyUpdate {
            event_id,
            status,
            reply,
            sent_at,
            db,
        } => {
            commands::context::cmd_context_reply_update(event_id, status, reply, sent_at, db, json)?
        }
        Commands::AxServiceScrapeOnce => {
            println!(
                "{}",
                serde_json::to_string(&ax_send::scrape_chat_list_for_service())?
            );
        }
        Commands::LocalSend {
            chat_name,
            message,
            yes,
            dry_run,
            reply_to,
            preflight,
        } => {
            let msg = format_outgoing_message(&message, no_prefix);
            let Some(setup) = commands::auto_reply::worker_gate::prepare_local_send(
                &config, &chat_name, dry_run, preflight, json,
            )?
            else {
                return Ok(());
            };
            commands::local_send::cmd_local_send(commands::local_send::LocalSendOptions {
                chat_name,
                message: msg,
                skip_confirm: yes,
                dry_run,
                preflight,
                json,
                bound_chat: setup.bound_chat,
                reply_to,
            })?
        }
        Commands::LocalDelete {
            chat_name,
            source,
            yes,
            dry_run,
        } => {
            require_ax_send(&config)?;
            require_allowed_send_chat(&config, &chat_name)?;
            commands::local_send::cmd_local_delete(&chat_name, &source, yes, dry_run, json)?
        }
        Commands::AxRead { chat_name, count } => {
            commands::ax_read::cmd_ax_read(
                &config,
                commands::ax_read::AxReadOptions {
                    chat_name,
                    count,
                    json,
                },
            )?
        }
        Commands::AxProbe {
            chat,
            dump,
            max_nodes,
        } => commands::ax_read::cmd_ax_probe(chat, dump, max_nodes, json)?,
        Commands::AxWatch {
            interval,
            hook_cmd,
            webhook_url,
            webhook_header,
            webhook_signing_secret,
            webhook_format,
            hook_chat,
            hook_keyword,
            hook_fail_fast,
            service_mode,
            status_path,
            log_path,
            hook_path,
        } => commands::ax_watch::cmd_ax_watch(commands::ax_watch::AxWatchOptions {
            interval_secs: interval,
            hook_cmd,
            webhook_url,
            webhook_headers: webhook_header,
            webhook_signing_secret,
            webhook_format: commands::watch::WebhookFormat::from_str_opt(Some(&webhook_format))?,
            hook_chats: hook_chat,
            hook_keywords: hook_keyword,
            fail_fast: hook_fail_fast,
            allow_insecure_webhooks: config.safety.allow_insecure_webhooks,
            min_hook_interval_secs,
            min_webhook_interval_secs,
            hook_timeout_secs,
            webhook_timeout_secs,
            json,
            unattended,
            allow_side_effects: allow_watch_side_effects,
            service_mode,
            status_path: status_path.map(Into::into),
            log_path: log_path.map(Into::into),
            hook_path: hook_path.map(Into::into),
        })?,
        Commands::AutoReply {
            chat,
            check,
            self_nickname,
            reply_author,
            interval,
            model,
        } => commands::auto_reply::run_auto_reply(
            &config,
            chat,
            check,
            self_nickname,
            reply_author,
            interval,
            model,
            json,
        )?,
        Commands::AutoReplyHost {
            bake,
            status,
            disable,
            tick,
            chat,
            manifest,
            state_root,
        } => {
            let action = match (bake, status, disable, tick) {
                (true, false, false, false) => commands::auto_reply_host::AutoReplyHostAction::Bake,
                (false, true, false, false) => {
                    commands::auto_reply_host::AutoReplyHostAction::Status
                }
                (false, false, true, false) => {
                    commands::auto_reply_host::AutoReplyHostAction::Disable
                }
                (false, false, false, true) => commands::auto_reply_host::AutoReplyHostAction::Tick,
                (false, false, false, false) => {
                    commands::auto_reply_host::AutoReplyHostAction::Status
                }
                _ => anyhow::bail!("choose one of --bake, --status, --disable, or --tick"),
            };
            commands::auto_reply_host::cmd_auto_reply_host(
                commands::auto_reply_host::AutoReplyHostOptions {
                    action,
                    chats: chat,
                    json,
                    manifest,
                    state_root,
                },
            )?
        }
        Commands::WatchCache { interval } => commands::auth::cmd_watch_cache(interval)?,
        Commands::Doctor { loco, self_check } => {
            if self_check {
                commands::doctor::cmd_doctor_self_check(json, &config)?
            } else {
                commands::doctor::cmd_doctor(json, loco, &config)?
            }
        }
        Commands::Dataset { command } => match command {
            DatasetCommand::Build { local_only } => {
                commands::dataset::cmd_dataset_build(local_only, json)?
            }
        },
        Commands::Experiment { command } => match command {
            ExperimentCommand::Run { prompt } => {
                commands::experiment::cmd_experiment_run(prompt, json, &config)?
            }
            ExperimentCommand::Compare {
                version_a,
                version_b,
            } => commands::experiment::cmd_experiment_compare(version_a, version_b, json)?,
        },
        // Handled before `load_config()` so a broken config cannot hide it.
        Commands::ReplyReceipt { .. } => {
            unreachable!("reply-receipt returns before the config is loaded")
        }
    }

    if cli.completion_promise {
        println!("[DONE]");
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn download_local_requires_and_parses_numeric_author_binding() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "download",
            "42",
            "100",
            "--local",
            "--expected-author-id",
            "700",
            "--output-dir",
            "/tmp/private-media",
        ])
        .expect("local media flags should parse");
        match cli.command {
            Commands::Download {
                chat_id,
                log_id,
                output_dir,
                local,
                expected_author_id,
            } => {
                assert_eq!(chat_id, 42);
                assert_eq!(log_id, 100);
                assert_eq!(output_dir.as_deref(), Some("/tmp/private-media"));
                assert!(local);
                assert_eq!(expected_author_id, Some(700));
            }
            other => panic!("expected download command, got {other:?}"),
        }
        assert!(Cli::try_parse_from([
            "openkakao-cli",
            "download",
            "42",
            "100",
            "--expected-author-id",
            "700",
        ])
        .is_err());
        assert!(Cli::try_parse_from([
            "openkakao-cli",
            "download",
            "42",
            "100",
            "--local",
            "--expected-author-id",
            "0",
        ])
        .is_err());
    }
    use crate::loco_helpers::should_retry_loco_probe_error;
    use crate::util::{require_permission, validate_outbound_message};

    #[test]
    fn outgoing_messages_include_prefix_by_default() {
        assert_eq!(
            format_outgoing_message("hello", false),
            "🤖 [Sent via openkakao] hello"
        );
    }

    #[test]
    fn outgoing_messages_can_disable_prefix() {
        assert_eq!(format_outgoing_message("hello", true), "hello");
    }

    #[test]
    fn send_accepts_global_and_local_flags_after_subcommand() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "--unattended",
            "--allow-non-interactive-send",
            "send",
            "123",
            "hello",
            "--no-prefix",
            "-y",
        ])
        .expect("send should accept global and local flags");

        assert!(cli.no_prefix);
        assert!(cli.unattended);
        assert!(cli.allow_non_interactive_send);
        match cli.command {
            Commands::Send {
                chat_id,
                message,
                yes,
                dry_run,
                ..
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(message, "hello");
                assert!(yes);
                assert!(!dry_run);
            }
            other => panic!("expected send command, got {other:?}"),
        }
    }

    #[test]
    fn unattended_flag_is_available_globally() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "--unattended",
            "--allow-watch-side-effects",
            "watch",
            "--hook-cmd",
            "cat",
        ])
        .expect("global unattended flag should parse");

        assert!(cli.unattended);
        assert!(cli.allow_watch_side_effects);
    }

    #[test]
    fn permission_gate_rejects_missing_opt_in() {
        let err = require_permission(false, "non-interactive send", "set the flags").unwrap_err();
        assert!(
            err.to_string().contains("set the flags"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn watch_accepts_hook_flags() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "--unattended",
            "--allow-watch-side-effects",
            "watch",
            "--hook-cmd",
            "cat >/tmp/openkakao-hook.json",
            "--webhook-url",
            "https://example.com/openkakao",
            "--webhook-header",
            "Authorization: Bearer token",
            "--webhook-signing-secret",
            "super-secret",
            "--hook-chat-id",
            "123",
            "--hook-keyword",
            "urgent",
            "--hook-type",
            "1",
            "--hook-fail-fast",
        ])
        .expect("watch should accept hook flags");

        assert!(cli.unattended);
        assert!(cli.allow_watch_side_effects);
        match cli.command {
            Commands::Watch {
                hook_cmd,
                webhook_url,
                webhook_header,
                webhook_signing_secret,
                hook_chat_id,
                hook_keyword,
                hook_type,
                hook_fail_fast,
                ..
            } => {
                assert_eq!(hook_cmd.as_deref(), Some("cat >/tmp/openkakao-hook.json"));
                assert_eq!(
                    webhook_url.as_deref(),
                    Some("https://example.com/openkakao")
                );
                assert_eq!(
                    webhook_header,
                    vec!["Authorization: Bearer token".to_string()]
                );
                assert_eq!(webhook_signing_secret.as_deref(), Some("super-secret"));
                assert_eq!(hook_chat_id, vec![123]);
                assert_eq!(hook_keyword, vec!["urgent".to_string()]);
                assert_eq!(hook_type, vec![1]);
                assert!(hook_fail_fast);
            }
            other => panic!("expected watch command, got {other:?}"),
        }
    }

    #[test]
    fn read_accepts_transport_flags() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "read",
            "123",
            "--rest",
            "--delay-ms",
            "250",
            "--force",
        ])
        .expect("read should accept transport flags");

        match cli.command {
            Commands::Read {
                chat_id,
                rest,
                delay_ms,
                force,
                ..
            } => {
                assert_eq!(chat_id, 123);
                assert!(rest);
                assert_eq!(delay_ms, 250);
                assert!(force);
            }
            other => panic!("expected read command, got {other:?}"),
        }
    }

    #[test]
    fn chats_accepts_rest_flag() {
        let cli = Cli::try_parse_from(["openkakao-cli", "chats", "--rest", "--unread"])
            .expect("chats should accept --rest");

        match cli.command {
            Commands::Chats { rest, unread, .. } => {
                assert!(rest);
                assert!(unread);
            }
            other => panic!("expected chats command, got {other:?}"),
        }
    }

    #[test]
    fn members_accepts_rest_flag() {
        let cli = Cli::try_parse_from(["openkakao-cli", "members", "123", "--rest", "--full"])
            .expect("members should accept --rest and --full");

        match cli.command {
            Commands::Members {
                chat_id,
                rest,
                full,
            } => {
                assert_eq!(chat_id, 123);
                assert!(rest);
                assert!(full);
            }
            other => panic!("expected members command, got {other:?}"),
        }
    }

    #[test]
    fn profile_accepts_chat_id_flag() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "profile",
            "100000002",
            "--chat-id",
            "900000000000001",
        ])
        .expect("profile should accept --chat-id");

        match cli.command {
            Commands::Profile {
                user_id,
                chat_id,
                local,
            } => {
                assert_eq!(user_id, 100000002);
                assert_eq!(chat_id, Some(900000000000001));
                assert!(!local);
            }
            other => panic!("expected profile command, got {other:?}"),
        }
    }

    #[test]
    fn friends_accepts_local_flag() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "friends",
            "--local",
            "-s",
            "Alice",
            "--chat-id",
            "900000000000003",
            "--user-id",
            "100000003",
        ])
        .expect("friends should accept --local");

        match cli.command {
            Commands::Friends {
                local,
                search,
                favorites,
                hidden,
                chat_id,
                user_id,
            } => {
                assert!(local);
                assert_eq!(search.as_deref(), Some("Alice"));
                assert!(!favorites);
                assert!(!hidden);
                assert_eq!(chat_id, Some(900000000000003));
                assert_eq!(user_id, Some(100000003));
            }
            other => panic!("expected friends command, got {other:?}"),
        }
    }

    #[test]
    fn profile_accepts_local_flag() {
        let cli = Cli::try_parse_from(["openkakao-cli", "profile", "100000002", "--local"])
            .expect("profile should accept --local");

        match cli.command {
            Commands::Profile {
                user_id,
                chat_id,
                local,
            } => {
                assert_eq!(user_id, 100000002);
                assert_eq!(chat_id, None);
                assert!(local);
            }
            other => panic!("expected profile command, got {other:?}"),
        }
    }

    #[test]
    fn chatinfo_command_is_available() {
        let cli = Cli::try_parse_from(["openkakao-cli", "chatinfo", "123"])
            .expect("chatinfo should be available");

        match cli.command {
            Commands::Chatinfo { chat_id } => assert_eq!(chat_id, 123),
            other => panic!("expected chatinfo command, got {other:?}"),
        }
    }

    #[test]
    fn probe_command_is_available() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "probe",
            "BLSYNC",
            "--body",
            "{\"r\":0,\"pr\":0}",
        ])
        .expect("probe should be available");

        match cli.command {
            Commands::Probe { method, body, .. } => {
                assert_eq!(method, "BLSYNC");
                assert_eq!(body.as_deref(), Some("{\"r\":0,\"pr\":0}"));
            }
            other => panic!("expected probe command, got {other:?}"),
        }
    }

    #[test]
    fn profile_hints_command_is_available() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "profile-hints",
            "--local-graph",
            "--user-id",
            "100000003",
            "--probe-syncmainpf",
            "--probe-uplinkprof",
        ])
        .expect("profile-hints should be available");

        match cli.command {
            Commands::ProfileHints {
                app_state,
                app_state_diff,
                local_graph,
                user_id,
                probe_syncmainpf,
                probe_uplinkprof,
            } => {
                assert!(!app_state);
                assert!(app_state_diff.is_none());
                assert!(local_graph);
                assert_eq!(user_id, Some(100000003));
                assert!(probe_syncmainpf);
                assert!(probe_uplinkprof);
            }
            other => panic!("expected profile-hints command, got {other:?}"),
        }
    }

    #[test]
    fn profile_hints_accepts_app_state_diff() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "profile-hints",
            "--app-state",
            "--app-state-diff",
            "/tmp/profile-hints-before.json",
        ])
        .expect("profile-hints should accept --app-state-diff");

        match cli.command {
            Commands::ProfileHints {
                app_state,
                app_state_diff,
                ..
            } => {
                assert!(app_state);
                assert_eq!(
                    app_state_diff.as_deref(),
                    Some("/tmp/profile-hints-before.json")
                );
            }
            other => panic!("expected profile-hints command, got {other:?}"),
        }
    }

    #[test]
    fn probe_retry_helper_covers_common_socket_failures() {
        assert!(should_retry_loco_probe_error(&anyhow::anyhow!("early eof")));
        assert!(should_retry_loco_probe_error(&anyhow::anyhow!(
            "Connection reset by peer (os error 54)"
        )));
        assert!(should_retry_loco_probe_error(&anyhow::anyhow!(
            "broken pipe"
        )));
        assert!(!should_retry_loco_probe_error(&anyhow::anyhow!(
            "status=-203"
        )));
    }

    #[test]
    fn legacy_loco_read_remains_available() {
        let cli = Cli::try_parse_from(["openkakao-cli", "loco-read", "123", "--all"])
            .expect("legacy loco-read should remain available");

        match cli.command {
            Commands::LocoRead { chat_id, all, .. } => {
                assert_eq!(chat_id, 123);
                assert!(all);
            }
            other => panic!("expected loco-read command, got {other:?}"),
        }
    }

    #[test]
    fn legacy_loco_chats_remains_available() {
        let cli = Cli::try_parse_from(["openkakao-cli", "loco-chats", "--all"])
            .expect("legacy loco-chats should remain available");

        match cli.command {
            Commands::LocoChats { show_all } => {
                assert!(show_all);
            }
            other => panic!("expected loco-chats command, got {other:?}"),
        }
    }

    #[test]
    fn legacy_loco_members_remains_available() {
        let cli = Cli::try_parse_from(["openkakao-cli", "loco-members", "123"])
            .expect("legacy loco-members should remain available");

        match cli.command {
            Commands::LocoMembers { chat_id } => assert_eq!(chat_id, 123),
            other => panic!("expected loco-members command, got {other:?}"),
        }
    }

    #[test]
    fn legacy_loco_chatinfo_remains_available() {
        let cli = Cli::try_parse_from(["openkakao-cli", "loco-chatinfo", "123"])
            .expect("legacy loco-chatinfo should remain available");

        match cli.command {
            Commands::LocoChatinfo { chat_id } => assert_eq!(chat_id, 123),
            other => panic!("expected loco-chatinfo command, got {other:?}"),
        }
    }

    #[test]
    fn legacy_loco_probe_remains_available() {
        let cli = Cli::try_parse_from(["openkakao-cli", "loco-probe", "BLSYNC"])
            .expect("legacy loco-probe should remain available");

        match cli.command {
            Commands::LocoProbe { method, body } => {
                assert_eq!(method, "BLSYNC");
                assert!(body.is_none());
            }
            other => panic!("expected loco-probe command, got {other:?}"),
        }
    }

    #[test]
    fn outbound_message_must_not_be_blank() {
        assert!(validate_outbound_message("hello").is_ok());
        assert!(validate_outbound_message("   ").is_err());
    }

    #[test]
    fn stats_command_is_available() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "stats",
            "123",
            "--limit",
            "500",
            "--since",
            "2025-01-01",
        ])
        .expect("stats should accept limit and since");

        match cli.command {
            Commands::Stats {
                chat_id,
                limit,
                since,
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(limit, Some(500));
                assert_eq!(since.as_deref(), Some("2025-01-01"));
            }
            other => panic!("expected stats command, got {other:?}"),
        }
    }

    #[test]
    fn openkakao_error_loco_status_display() {
        use crate::error::OpenKakaoError;
        let err = OpenKakaoError::loco("SYNCMSG", -300);
        assert!(err.to_string().contains("SYNCMSG"));
        assert!(err.to_string().contains("-300"));
        assert!(err.is_retryable());
    }

    #[test]
    fn openkakao_error_token_expired_from_950() {
        use crate::error::OpenKakaoError;
        let err = OpenKakaoError::loco("LOGINLIST", -950);
        assert!(matches!(err, OpenKakaoError::TokenExpired));
        assert!(err.is_retryable());
    }

    #[test]
    fn openkakao_error_non_retryable_status() {
        use crate::error::OpenKakaoError;
        let err = OpenKakaoError::loco("WRITE", -203);
        assert!(!err.is_retryable());
    }

    #[test]
    fn check_loco_status_passes_on_zero() {
        use crate::loco_helpers::check_loco_status;
        let packet = crate::loco::packet::LocoPacket {
            packet_id: 1,
            status_code: 0,
            method: "TEST".into(),
            body_type: 0,
            body: bson::doc! { "status": 0_i32 },
        };
        assert!(check_loco_status("TEST", &packet).is_ok());
    }

    #[test]
    fn check_loco_status_fails_on_nonzero() {
        use crate::loco_helpers::check_loco_status;
        let packet = crate::loco::packet::LocoPacket {
            packet_id: 1,
            status_code: 0,
            method: "SYNCMSG".into(),
            body_type: 0,
            body: bson::doc! { "status": -300_i32 },
        };
        let err = check_loco_status("SYNCMSG", &packet).unwrap_err();
        assert!(err.to_string().contains("SYNCMSG"));
        assert!(err.to_string().contains("-300"));
    }

    #[test]
    fn watch_capture_flag_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "watch", "--capture"])
            .expect("watch should accept --capture");

        match cli.command {
            Commands::Watch { capture, .. } => {
                assert!(capture);
            }
            other => panic!("expected watch command, got {other:?}"),
        }
    }

    #[test]
    fn probe_capture_pushes_flag_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "probe", "PING", "--capture-pushes"])
            .expect("probe should accept --capture-pushes");

        match cli.command {
            Commands::Probe {
                method,
                capture_pushes,
                ..
            } => {
                assert_eq!(method, "PING");
                assert!(capture_pushes);
            }
            other => panic!("expected probe command, got {other:?}"),
        }
    }

    #[test]
    fn delete_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "delete", "123", "456", "--force", "-y"])
            .expect("delete should parse");
        match cli.command {
            Commands::Delete {
                chat_id,
                log_id,
                force,
                yes,
                dry_run,
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(log_id, 456);
                assert!(force);
                assert!(yes);
                assert!(!dry_run);
            }
            other => panic!("expected delete, got {other:?}"),
        }
    }

    #[test]
    fn mark_read_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "mark-read", "123", "456"])
            .expect("mark-read should parse");
        match cli.command {
            Commands::MarkRead {
                chat_id,
                log_id,
                yes,
                dry_run,
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(log_id, 456);
                assert!(!yes);
                assert!(!dry_run);
            }
            other => panic!("expected mark-read, got {other:?}"),
        }
    }

    #[test]
    fn send_me_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "send-me", "test message"])
            .expect("send-me should parse");
        match cli.command {
            Commands::SendMe { message, .. } => {
                assert_eq!(message, "test message");
            }
            other => panic!("expected send-me, got {other:?}"),
        }
    }

    #[test]
    fn send_accepts_dry_run_flag() {
        let cli = Cli::try_parse_from(["openkakao-cli", "send", "123", "hello", "--dry-run"])
            .expect("send --dry-run should parse");
        match cli.command {
            Commands::Send {
                chat_id, dry_run, ..
            } => {
                assert_eq!(chat_id, 123);
                assert!(dry_run);
            }
            other => panic!("expected send, got {other:?}"),
        }
    }

    #[test]
    fn delete_accepts_dry_run_flag() {
        let cli = Cli::try_parse_from(["openkakao-cli", "delete", "123", "456", "--dry-run"])
            .expect("delete --dry-run should parse");
        match cli.command {
            Commands::Delete {
                chat_id,
                log_id,
                dry_run,
                ..
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(log_id, 456);
                assert!(dry_run);
            }
            other => panic!("expected delete, got {other:?}"),
        }
    }

    #[test]
    fn edit_accepts_dry_run_flag() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "edit",
            "123",
            "456",
            "new text",
            "--dry-run",
        ])
        .expect("edit --dry-run should parse");
        match cli.command {
            Commands::Edit {
                chat_id,
                log_id,
                message,
                dry_run,
                ..
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(log_id, 456);
                assert_eq!(message, "new text");
                assert!(dry_run);
            }
            other => panic!("expected edit, got {other:?}"),
        }
    }

    #[test]
    fn react_accepts_dry_run_flag() {
        let cli = Cli::try_parse_from(["openkakao-cli", "react", "123", "456", "--dry-run"])
            .expect("react --dry-run should parse");
        match cli.command {
            Commands::React {
                chat_id,
                log_id,
                dry_run,
                ..
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(log_id, 456);
                assert!(dry_run);
            }
            other => panic!("expected react, got {other:?}"),
        }
    }

    #[test]
    fn local_chats_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "local-chats", "-n", "10"])
            .expect("local-chats should parse");
        match cli.command {
            Commands::LocalChats { limit, groups } => {
                assert_eq!(limit, 10);
                assert!(!groups);
            }
            other => panic!("expected local-chats, got {other:?}"),
        }
    }

    #[test]
    fn auto_reply_command_accepts_plain_chat_name() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "auto-reply",
            "--chat",
            "부자멘토멘티",
            "--model",
            "gemini-3.6-flash",
        ])
        .expect("plain chat names should parse");
        match cli.command {
            Commands::AutoReply { chat, model, .. } => {
                assert_eq!(chat, vec!["부자멘토멘티"]);
                assert_eq!(model.as_deref(), Some("gemini-3.6-flash"));
            }
            other => panic!("expected auto-reply, got {other:?}"),
        }
        let selectors = local_db::parse_chat_selectors(&["부자멘토멘티".into()])
            .expect("plain names become name selectors");
        assert!(matches!(
            selectors.as_slice(),
            [local_db::ChatSelector::Name(name)] if name == "부자멘토멘티"
        ));
    }
    #[test]
    fn auto_reply_host_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "auto-reply-host", "--status"])
            .expect("auto-reply-host should parse");
        match cli.command {
            Commands::AutoReplyHost {
                bake,
                status,
                disable,
                tick,
                ref chat,
                manifest: _,
                state_root: _,
            } => {
                assert!(!bake);
                assert!(status);
                assert!(!disable);
                assert!(!tick);
                assert!(chat.is_empty());
            }
            other => panic!("expected auto-reply-host, got {other:?}"),
        }
        assert!(is_local_only_command(&cli.command));
    }

    #[test]
    fn catalog_merge_appends_auto_reply_rooms() {
        let merged = room_catalog::merge_configured_and_catalog_selectors(
            &["bind:42:부자멘토멘티".into()],
            &[99],
            &[
                local_db::LocalChat {
                    chat_id: 42,
                    chat_type: 1,
                    chat_name: "부자멘토멘티".into(),
                    database_chat_name: None,
                    active_members_count: 4,
                    last_log_id: 1,
                    last_updated_at: 0,
                    unread_count: 0,
                    display_name: "부자멘토멘티".into(),
                },
                local_db::LocalChat {
                    chat_id: 99,
                    chat_type: 1,
                    chat_name: "kakao-test".into(),
                    database_chat_name: None,
                    active_members_count: 2,
                    last_log_id: 2,
                    last_updated_at: 0,
                    unread_count: 0,
                    display_name: "kakao-test".into(),
                },
            ],
        )
        .expect("merge");
        assert_eq!(
            merged,
            vec![
                "bind:42:부자멘토멘티".to_string(),
                "bind:99:kakao-test".to_string()
            ]
        );
    }

    #[test]
    fn auto_reply_command_accepts_repeated_chat_selectors() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "auto-reply",
            "--chat",
            "id:42",
            "--chat",
            "name:투자 공부방",
            "--self-nickname",
            "self",
            "--reply-author",
            "author",
            "--check",
        ])
        .expect("auto-reply should parse");
        match cli.command {
            Commands::AutoReply {
                chat,
                check,
                self_nickname,
                reply_author,
                ..
            } => {
                assert_eq!(chat, vec!["id:42", "name:투자 공부방"]);
                assert_eq!(self_nickname.as_deref(), Some("self"));
                assert_eq!(reply_author, vec!["author"]);
                assert!(check);
            }
            other => panic!("expected auto-reply, got {other:?}"),
        }
    }

    #[test]
    fn local_read_command_parses() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "local-read",
            "123",
            "-n",
            "50",
            "--since",
            "2025-01-01",
        ])
        .expect("local-read should parse");
        match cli.command {
            Commands::LocalRead {
                chat_id,
                count,
                since,
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(count, 50);
                assert_eq!(since.as_deref(), Some("2025-01-01"));
            }
            other => panic!("expected local-read, got {other:?}"),
        }
    }
    #[test]
    fn local_poll_command_parses_bounded_options() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "local-poll",
            "--chat-id",
            "123",
            "--count",
            "13",
            "--interval",
            "0.5",
        ])
        .expect("local-poll should parse");
        match cli.command {
            Commands::LocalPoll {
                chat_id,
                count,
                interval,
            } => {
                assert_eq!(chat_id, 123);
                assert_eq!(count, 13);
                assert_eq!(interval, 0.5);
            }
            other => panic!("expected local-poll, got {other:?}"),
        }
        assert!(Cli::try_parse_from([
            "openkakao-cli",
            "local-poll",
            "--chat-id",
            "123",
            "--interval",
            "0.01",
        ])
        .is_err());
    }

    #[test]
    fn local_search_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "local-search", "hello", "-n", "10"])
            .expect("local-search should parse");
        match cli.command {
            Commands::LocalSearch {
                query,
                count,
                chat_id,
            } => {
                assert_eq!(query, "hello");
                assert_eq!(count, 10);
                assert_eq!(chat_id, None);
            }
            other => panic!("expected local-search, got {other:?}"),
        }
    }

    #[test]
    fn local_search_command_parses_chat_id() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "local-search",
            "번호",
            "-n",
            "5",
            "--chat-id",
            "417780809780519",
        ])
        .expect("local-search should parse --chat-id");
        match cli.command {
            Commands::LocalSearch {
                query,
                count,
                chat_id,
            } => {
                assert_eq!(query, "번호");
                assert_eq!(count, 5);
                assert_eq!(chat_id, Some(417780809780519));
            }
            other => panic!("expected local-search, got {other:?}"),
        }
    }

    #[test]
    fn local_schema_command_parses() {
        Cli::try_parse_from(["openkakao-cli", "local-schema"]).expect("local-schema should parse");
    }

    #[test]
    fn local_send_command_parses() {
        let cli = Cli::try_parse_from(["openkakao-cli", "local-send", "나와의 채팅", "hi", "-y"])
            .expect("local-send should parse");
        match cli.command {
            Commands::LocalSend {
                chat_name,
                message,
                yes,
                dry_run,
                reply_to,
                preflight,
            } => {
                assert_eq!(chat_name, "나와의 채팅");
                assert_eq!(message, "hi");
                assert!(yes);
                assert!(!dry_run);
                assert!(reply_to.is_none());
                assert!(!preflight);
            }
            other => panic!("expected local-send, got {other:?}"),
        }
    }

    #[test]
    fn local_send_hidden_preflight_parses_and_conflicts_with_dry_run() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "local-send",
            "부자멘토멘티",
            "probe",
            "--preflight",
        ])
        .expect("hidden worker preflight should parse");
        match cli.command {
            Commands::LocalSend {
                preflight, dry_run, ..
            } => {
                assert!(preflight);
                assert!(!dry_run);
            }
            other => panic!("expected local-send, got {other:?}"),
        }

        assert!(Cli::try_parse_from([
            "openkakao-cli",
            "local-send",
            "부자멘토멘티",
            "probe",
            "--preflight",
            "--dry-run",
        ])
        .is_err());
    }

    #[test]
    fn local_only_commands_suppress_deprecation_warning() {
        let ax_read = Cli::try_parse_from(["openkakao-cli", "ax-read", "나와의 채팅"])
            .expect("ax-read should parse");
        assert!(is_local_only_command(&ax_read.command));

        let local_send = Cli::try_parse_from(["openkakao-cli", "local-send", "나와의 채팅", "hi"])
            .expect("local-send should parse");
        assert!(is_local_only_command(&local_send.command));
        let local_delete = Cli::try_parse_from([
            "openkakao-cli",
            "local-delete",
            "나와의 채팅",
            "draft",
            "-y",
        ])
        .expect("local-delete should parse");
        assert!(is_local_only_command(&local_delete.command));
        let bundle = Cli::try_parse_from([
            "openkakao-cli",
            "context-reply-bundle",
            "query",
            "--chat",
            "chat-a",
        ])
        .expect("context-reply-bundle should parse");
        assert!(is_local_only_command(&bundle.command));

        let login = Cli::try_parse_from(["openkakao-cli", "login"]).expect("login should parse");
        assert!(!is_local_only_command(&login.command));
    }

    #[test]
    fn context_reply_bundle_parses_repeatable_and_csv_exclusions() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "context-reply-bundle",
            "query",
            "--chat",
            "chat-a",
            "--chat-id",
            "7",
            "--current-log-id",
            "11",
            "--exclude-log-id",
            "12,13",
            "--exclude-log-id",
            "14",
            "--recipient",
            "민수",
        ])
        .expect("burst exclusions should parse");
        match cli.command {
            Commands::ContextReplyBundle {
                current_log_id,
                exclude_log_id,
                ..
            } => {
                assert_eq!(current_log_id, Some(11));
                assert_eq!(exclude_log_id, vec![12, 13, 14]);
            }
            other => panic!("expected context-reply-bundle, got {other:?}"),
        }
        assert!(Cli::try_parse_from([
            "openkakao-cli",
            "context-reply-bundle",
            "query",
            "--chat",
            "chat-a",
            "--current-log-id",
            "0",
        ])
        .is_err());
        assert!(Cli::try_parse_from([
            "openkakao-cli",
            "context-reply-bundle",
            "query",
            "--chat",
            "chat-a",
            "--exclude-log-id",
            "-1",
        ])
        .is_err());
    }

    #[test]
    fn ax_watch_command_parses() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "ax-watch",
            "--interval",
            "5",
            "--hook-keyword",
            "긴급",
            "--hook-chat",
            "정훈",
        ])
        .expect("ax-watch should parse");
        match cli.command {
            Commands::AxWatch {
                interval,
                hook_keyword,
                hook_chat,
                ..
            } => {
                assert_eq!(interval, 5);
                assert_eq!(hook_keyword, vec!["긴급".to_string()]);
                assert_eq!(hook_chat, vec!["정훈".to_string()]);
            }
            other => panic!("expected ax-watch, got {other:?}"),
        }
    }

    #[test]
    fn ax_watch_service_mode_parses_hidden_flags() {
        let cli = Cli::try_parse_from([
            "openkakao-cli",
            "ax-watch",
            "--service-mode",
            "--interval",
            "5",
            "--status-path",
            "/tmp/watch-status.json",
            "--log-path",
            "/tmp/watch.log",
            "--hook-path",
            "/tmp/hook",
        ])
        .expect("service ax-watch should parse");
        match cli.command {
            Commands::AxWatch {
                service_mode,
                interval,
                status_path,
                log_path,
                hook_path,
                ..
            } => {
                assert!(service_mode);
                assert_eq!(interval, 5);
                assert_eq!(status_path.as_deref(), Some("/tmp/watch-status.json"));
                assert_eq!(log_path.as_deref(), Some("/tmp/watch.log"));
                assert_eq!(hook_path.as_deref(), Some("/tmp/hook"));
            }
            other => panic!("expected ax-watch, got {other:?}"),
        }
    }
}
