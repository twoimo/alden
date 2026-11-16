//! Read-only `local-*` commands over the local KakaoTalk database: chats,
//! messages, polling, search, and schema. None of them contact a server.

use std::io::{self, Write};

use anyhow::Result;
use chrono::TimeZone;

use crate::{local_db, util};

pub fn cmd_local_chats(limit: usize, groups: bool, json: bool) -> Result<()> {
    let reader = local_db::LocalDbReader::open()?;
    if groups {
        let chats = reader.list_group_chats(limit)?;
        if json {
            println!("{}", serde_json::to_string_pretty(&chats)?);
        } else if chats.is_empty() {
            println!("No group chats found in local database.");
        } else {
            for c in &chats {
                println!(
                    "  {} | {} | {} ({})",
                    c.chat_id, c.chat_type, c.title, c.members,
                );
            }
            println!(
                "\n{} group chats (local DB, no server contact)",
                chats.len()
            );
        }
        return Ok(());
    }
    let chats = reader.list_chats(limit)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&chats)?);
    } else {
        if chats.is_empty() {
            println!("No chats found in local database.");
        } else {
            let chat_type_label = |t: i32| -> &'static str {
                match t {
                    0 => "DM",
                    1 => "Group",
                    _ => "Other",
                }
            };
            for c in &chats {
                let ts = chrono::Local
                    .timestamp_opt(c.last_updated_at, 0)
                    .single()
                    .map(|dt| dt.format("%Y-%m-%d %H:%M").to_string())
                    .unwrap_or_default();
                let unread = if c.unread_count > 0 {
                    format!(" [{}]", c.unread_count)
                } else {
                    String::new()
                };
                println!(
                    "  {} | {} | {} ({}){} | {}",
                    c.chat_id,
                    chat_type_label(c.chat_type),
                    c.chat_name,
                    c.active_members_count,
                    unread,
                    ts,
                );
            }
            println!("\n{} chats (local DB, no server contact)", chats.len());
        }
    }
    Ok(())
}

pub fn cmd_local_read(chat_id: i64, count: usize, since: Option<String>, json: bool) -> Result<()> {
    let since_ts = util::parse_since_date(since.as_deref())?;
    let reader = local_db::LocalDbReader::open()?;
    let mut messages = reader.read_messages(chat_id, count, since_ts)?;
    messages.reverse(); // chronological order
    if json {
        println!("{}", serde_json::to_string_pretty(&messages)?);
    } else {
        if messages.is_empty() {
            println!("No messages found in local database for chat {}.", chat_id);
        } else {
            for m in &messages {
                let ts = chrono::Local
                    .timestamp_opt(m.sent_at, 0)
                    .single()
                    .map(|dt| dt.format("%H:%M:%S").to_string())
                    .unwrap_or_default();
                let sender = if m.sender_name.is_empty() {
                    format!("{}", m.author_id)
                } else {
                    m.sender_name.clone()
                };
                let type_tag = util::message_type_label(m.message_type);
                if m.message_type == 1 {
                    println!("  [{}] {}: {}", ts, sender, m.message);
                } else {
                    println!("  [{}] {}: [{}] {}", ts, sender, type_tag, m.message);
                }
            }
            println!(
                "\n{} messages (local DB, no server contact)",
                messages.len()
            );
        }
    }
    Ok(())
}

pub fn cmd_local_poll(chat_id: i64, count: usize, interval: f64) -> Result<()> {
    let reader = local_db::LocalDbReader::open()?;
    loop {
        let envelope = reader.poll(chat_id, count)?;
        println!("{}", serde_json::to_string(&envelope)?);
        io::stdout().flush()?;
        std::thread::sleep(std::time::Duration::from_secs_f64(interval));
    }
}

pub fn cmd_local_search(
    query: String,
    count: usize,
    chat_id: Option<i64>,
    json: bool,
) -> Result<()> {
    let reader = local_db::LocalDbReader::open()?;
    let results = reader.search_messages(&query, count, chat_id)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&results)?);
    } else {
        if results.is_empty() {
            println!("No messages matching '{}' in local database.", query);
        } else {
            for m in &results {
                let ts = chrono::Local
                    .timestamp_opt(m.sent_at, 0)
                    .single()
                    .map(|dt| dt.format("%Y-%m-%d %H:%M").to_string())
                    .unwrap_or_default();
                let sender = if m.sender_name.is_empty() {
                    format!("{}", m.author_id)
                } else {
                    m.sender_name.clone()
                };
                println!(
                    "  [{}] chat={} {}: {}",
                    ts,
                    m.chat_id,
                    sender,
                    util::truncate(&m.message, 80)
                );
            }
            println!("\n{} results (local DB, no server contact)", results.len());
        }
    }
    Ok(())
}

pub fn cmd_local_schema(json: bool) -> Result<()> {
    let reader = local_db::LocalDbReader::open()?;
    let tables = reader.schema()?;
    if json {
        let items: Vec<serde_json::Value> = tables
            .iter()
            .map(|(name, sql)| serde_json::json!({"name": name, "sql": sql}))
            .collect();
        println!("{}", serde_json::to_string_pretty(&items)?);
    } else {
        for (name, sql) in &tables {
            println!("-- {}", name);
            println!("{}\n", sql);
        }
    }
    Ok(())
}
