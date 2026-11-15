use anyhow::Result;

use crate::ax_send;

pub struct AxReadOptions {
    pub chat_name: String,
    pub count: usize,
    pub json: bool,
}

/// Read recent messages via AX automation (scrapes the open KakaoTalk chat
/// window) — no local SQLCipher DB access required. See `src/ax_send.rs`.
pub fn cmd_ax_read(opts: AxReadOptions) -> Result<()> {
    let AxReadOptions {
        ref chat_name,
        count,
        json,
    } = opts;

    let messages = ax_send::read_via_ax(chat_name, count)?;

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
