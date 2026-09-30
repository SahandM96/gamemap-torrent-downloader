//! HTTP + magnet + torrent parse (port of net.py).

use regex::Regex;
use serde_json::Value;
use sha1::{Digest, Sha1};
use std::time::Duration;
use thiserror::Error;
use urlencoding::encode;

pub const USER_AGENT: &str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

pub const TRACKERS: &[&str] = &[
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
];

#[derive(Debug, Error)]
#[error("{0}")]
pub struct SourceError(pub String);

pub fn magnet_from_hash(info_hash: &str, name: &str) -> String {
    let mut parts = vec![format!("magnet:?xt=urn:btih:{}", info_hash.to_uppercase())];
    if !name.is_empty() {
        parts.push(format!("dn={}", encode(name)));
    }
    for t in TRACKERS {
        parts.push(format!("tr={}", encode(t)));
    }
    parts.join("&")
}

pub fn normalize_magnet(raw: &str) -> String {
    raw.trim().replace("&amp;", "&")
}

pub fn magnet_first(value: &str) -> String {
    // ponytail: raw #"... "# so " and ' can appear in the char class without ending the Rust string
    let re = Regex::new(r#"magnet:\?xt=urn:btih:[0-9a-fA-F]{40}[^"'\s<>\\}\]]*"#).unwrap();
    re.find(value)
        .map(|m| normalize_magnet(m.as_str()))
        .unwrap_or_default()
}

pub fn magnet_name(magnet: &str) -> String {
    let re = Regex::new(r"[?&]dn=([^&]+)").unwrap();
    re.captures(magnet)
        .and_then(|c| c.get(1))
        .map(|m| {
            urlencoding::decode(m.as_str())
                .unwrap_or_default()
                .replace('_', " ")
                .trim()
                .to_string()
        })
        .unwrap_or_default()
}

pub fn magnet_infohash(magnet: &str) -> String {
    let re = Regex::new(r"xt=urn:btih:([0-9a-fA-F]{40})").unwrap();
    re.captures(magnet)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_lowercase())
        .unwrap_or_default()
}

pub fn parse_size(text: &str) -> Option<i64> {
    let re = Regex::new(r"(?i)([\d.,]+)\s*(TB|GB|MB|KB|TiB|GiB|MiB|KiB|B)\b").unwrap();
    let caps = re.captures(text)?;
    let value: f64 = caps.get(1)?.as_str().replace(',', "").parse().ok()?;
    let unit = caps.get(2)?.as_str().to_uppercase();
    let mult = match unit.as_str() {
        "B" => 1.0,
        "KB" | "KIB" => 1024.0,
        "MB" | "MIB" => 1024f64.powi(2),
        "GB" | "GIB" => 1024f64.powi(3),
        "TB" | "TIB" => 1024f64.powi(4),
        _ => 1.0,
    };
    Some((value * mult) as i64)
}

pub fn format_size(num: Option<i64>) -> String {
    let Some(n) = num.filter(|n| *n > 0) else {
        return "-".into();
    };
    let mut value = n as f64;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if value < 1024.0 || unit == "TB" {
            return if unit == "B" {
                format!("{n} B")
            } else {
                format!("{value:.2} {unit}")
            };
        }
        value /= 1024.0;
    }
    "-".into()
}

pub fn clean_text(raw: &str) -> String {
    let re = Regex::new(r"<[^>]+>").unwrap();
    html_escape::decode_html_entities(&re.replace_all(raw, " "))
        .trim()
        .to_string()
}

fn client(proxy: &str) -> Result<reqwest::blocking::Client, SourceError> {
    let mut b = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(14))
        .user_agent(USER_AGENT)
        .redirect(reqwest::redirect::Policy::limited(8))
        .gzip(true);
    if !proxy.is_empty() {
        let p = reqwest::Proxy::all(proxy).map_err(|e| SourceError(e.to_string()))?;
        b = b.proxy(p);
    }
    b.build().map_err(|e| SourceError(e.to_string()))
}

pub fn fetch(url: &str, proxy: &str, timeout_secs: u64) -> Result<String, SourceError> {
    let mut b = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(timeout_secs))
        .user_agent(USER_AGENT)
        .redirect(reqwest::redirect::Policy::limited(8))
        .gzip(true);
    if !proxy.is_empty() {
        let p = reqwest::Proxy::all(proxy).map_err(|e| SourceError(e.to_string()))?;
        b = b.proxy(p);
    }
    let c = b.build().map_err(|e| SourceError(e.to_string()))?;
    let resp = c
        .get(url)
        .header("Accept-Language", "en-US,en;q=0.9")
        .send()
        .map_err(|e| SourceError(e.to_string()))?;
    let status = resp.status();
    let body = resp.text().map_err(|e| SourceError(e.to_string()))?;
    if !status.is_success() {
        return Err(SourceError(format!("HTTP {}", status.as_u16())));
    }
    let head = &body[..body.len().min(4000)];
    if head.contains("Just a moment") || head.contains("cf-chl") || head.contains("challenge-platform")
    {
        return Err(SourceError(
            "Cloudflare چلنج — پروکسی/مرورگر لازم است".into(),
        ));
    }
    Ok(body)
}

pub fn fetch_json(url: &str, proxy: &str, timeout_secs: u64) -> Result<Value, SourceError> {
    let body = fetch(url, proxy, timeout_secs)?;
    serde_json::from_str(&body).map_err(|_| SourceError("پاسخ JSON نبود".into()))
}

pub fn fetch_bytes(url: &str, proxy: &str, timeout_secs: u64) -> Result<Vec<u8>, SourceError> {
    let c = if proxy.is_empty() {
        reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .user_agent(USER_AGENT)
            .build()
            .map_err(|e| SourceError(e.to_string()))?
    } else {
        client(proxy)?
    };
    let resp = c.get(url).send().map_err(|e| SourceError(e.to_string()))?;
    if !resp.status().is_success() {
        return Err(SourceError(format!("HTTP {}", resp.status().as_u16())));
    }
    Ok(resp.bytes().map_err(|e| SourceError(e.to_string()))?.to_vec())
}

pub fn try_mirrors<T, F>(mirrors: &[String], mut call: F, attempts: usize) -> Result<T, SourceError>
where
    F: FnMut(&str) -> Result<T, SourceError>,
{
    let mut errors = Vec::new();
    for base in mirrors.iter().take(attempts) {
        match call(base) {
            Ok(v) => return Ok(v),
            Err(e) => errors.push(format!("{base}: {e}")),
        }
    }
    Err(SourceError(
        if errors.is_empty() {
            "میان‌بری جواب نداد".into()
        } else {
            errors.join(" | ")
        },
    ))
}

/// Minimal bencode decode for info-dict slice hashing.
fn bdecode(data: &[u8], mut idx: usize) -> Result<(BValue, usize), ()> {
    if idx >= data.len() {
        return Err(());
    }
    match data[idx] {
        b'i' => {
            let end = data[idx..].iter().position(|&b| b == b'e').ok_or(())? + idx;
            let n: i64 = std::str::from_utf8(&data[idx + 1..end])
                .map_err(|_| ())?
                .parse()
                .map_err(|_| ())?;
            Ok((BValue::Int(n), end + 1))
        }
        b'l' => {
            idx += 1;
            let mut res = Vec::new();
            while data.get(idx) != Some(&b'e') {
                let (v, nidx) = bdecode(data, idx)?;
                res.push(v);
                idx = nidx;
            }
            Ok((BValue::List(res), idx + 1))
        }
        b'd' => {
            idx += 1;
            let mut res = Vec::new();
            while data.get(idx) != Some(&b'e') {
                let (k, nidx) = bdecode(data, idx)?;
                let (v, nidx2) = bdecode(data, nidx)?;
                if let BValue::Bytes(kb) = k {
                    res.push((kb, v));
                }
                idx = nidx2;
            }
            Ok((BValue::Dict(res), idx + 1))
        }
        b'0'..=b'9' => {
            let colon = data[idx..].iter().position(|&b| b == b':').ok_or(())? + idx;
            let len: usize = std::str::from_utf8(&data[idx..colon])
                .map_err(|_| ())?
                .parse()
                .map_err(|_| ())?;
            let start = colon + 1;
            let end = start + len;
            if end > data.len() {
                return Err(());
            }
            Ok((BValue::Bytes(data[start..end].to_vec()), end))
        }
        _ => Err(()),
    }
}

enum BValue {
    Int(i64),
    Bytes(Vec<u8>),
    List(Vec<BValue>),
    Dict(Vec<(Vec<u8>, BValue)>),
}

pub fn parse_torrent_bytes(data: &[u8]) -> Option<serde_json::Map<String, Value>> {
    let pos = data.windows(6).position(|w| w == b"4:info")?;
    let start = pos + 6;
    let (info_dict, end) = bdecode(data, start).ok()?;
    let raw_info = &data[start..end];
    let info_hash = hex::encode(Sha1::digest(raw_info));
    let torrent_sha1 = hex::encode(Sha1::digest(data));
    let mut name = String::new();
    if let BValue::Dict(entries) = info_dict {
        for (k, v) in entries {
            if (k == b"name" || k == b"name.utf-8") && matches!(&v, BValue::Bytes(_)) {
                if let BValue::Bytes(b) = v {
                    name = String::from_utf8_lossy(&b).to_string();
                    break;
                }
            }
        }
    }
    let mut m = serde_json::Map::new();
    m.insert("info_hash".into(), Value::String(info_hash));
    m.insert("torrent_sha1".into(), Value::String(torrent_sha1));
    m.insert("name".into(), Value::String(name));
    Some(m)
}

// tiny html entity helper without extra crate
mod html_escape {
    pub fn decode_html_entities(s: &str) -> String {
        s.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
    }
}
