use anyhow::{Context, Result};

use crate::ax_send;
use crate::commands::local_send::require_ax_send;
use crate::config;

pub struct AxReadOptions {
    pub chat_name: String,
    pub count: usize,
    pub json: bool,
}

/// Read recent messages via AX automation (scrapes the open KakaoTalk chat
/// window) — no local SQLCipher DB access required. See `src/ax_send.rs`.
pub fn cmd_ax_read(config: &config::OpenKakaoConfig, opts: AxReadOptions) -> Result<()> {
    let AxReadOptions {
        ref chat_name,
        count,
        json,
    } = opts;

    let messages = gated_ax_read(config, || ax_send::read_via_ax(chat_name, count))?;

    if json {
        let items: Vec<_> = messages
            .iter()
            .map(|m| {
                serde_json::json!({
                    "time": m.time,
                    "text": m.text,
                })
            })
            .collect();
        crate::util::output_json(&serde_json::json!({
            "chat_name": chat_name,
            "messages": items,
        }))?;
    } else if messages.is_empty() {
        println!("No visible messages found in chat \"{chat_name}\".");
    } else {
        for m in &messages {
            match &m.time {
                Some(t) => println!("[{t}] {}", m.text),
                None => println!("{}", m.text),
            }
        }
        println!(
            "\n{} messages (AX scrape, no server contact)",
            messages.len()
        );
    }

    Ok(())
}

/// `read_via_ax` opens a closed chat by posting Return to KakaoTalk, and the
/// keystroke lands in whichever window is key, where it can submit a draft.
fn gated_ax_read<T>(
    config: &config::OpenKakaoConfig,
    read: impl FnOnce() -> Result<T>,
) -> Result<T> {
    require_ax_send(config).context("ax-read may press Return in KakaoTalk to open the chat")?;
    read()
}

pub fn cmd_ax_probe(chat: String, dump: bool, max_nodes: usize, json: bool) -> Result<()> {
    if dump {
        for line in ax_send::dump_ax(&chat, max_nodes)? {
            println!("{line}");
        }
    } else {
        let report = ax_send::probe_ax(&chat)?;
        if json {
            crate::util::output_json(&report)?;
        } else {
            println!(
                "chat={:?} window_found={} composer_found={} elapsed_ms={} label={:?}",
                report.chat_name,
                report.window_found,
                report.composer_found,
                report.elapsed_ms,
                report.label
            );
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ax_read_reaches_kakaotalk_only_with_the_ax_send_opt_in() {
        let mut reached = false;
        let refused = gated_ax_read(&config::OpenKakaoConfig::default(), || {
            reached = true;
            Ok(())
        });
        assert!(!reached, "ax-read drove KakaoTalk without allow_ax_send");
        assert!(refused.is_err());

        let mut opted_in = config::OpenKakaoConfig::default();
        opted_in.safety.allow_ax_send = true;
        gated_ax_read(&opted_in, || {
            reached = true;
            Ok(())
        })
        .expect("an opted-in read runs");
        assert!(reached);
    }
}
