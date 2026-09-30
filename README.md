# GameMap Torrent Desk

جست‌وجوی محلی در ایندکس‌های عمومی تورنت و مدیریت دانلود با **aria2c** (فرایند جدا، GPL-2+).

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-gamemap--torrent--downloader-181717?logo=github)](https://github.com/SahandM96/gamemap-torrent-downloader)

نگهداری: **GameMap Studio** · مستندات: [`docs/`](docs/README.md)

## ویژگی‌ها

- رابط فارسی RTL (HTML/CSS/JS بدون فریم‌ورک سنگین)
- جست‌وجو در چند منبع عمومی (پل Python در v0.1)
- Magnet / `.torrent`، صف، pause/resume، اسکن `*.aria2`
- aria2c به‌صورت **sidecar** — به باینری اپ لینک نمی‌شود ([`NOTICE`](NOTICE))

## پیش‌نیاز

| مورد | توضیح |
|------|--------|
| Rust (stable) | `rustup` |
| Node.js 18+ | CLI و وابستگی‌های Tauri |
| Python 3 | پل جست‌وجو |
| aria2c | [دانلود sidecar](sidecars/README.md) یا PATH |
| Linux deps | [پیش‌نیازهای Tauri 2](https://v2.tauri.app/start/prerequisites/) |

جزئیات: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)

## اجرا

```bash
npm install
# یک‌بار: باینری aria2 را طبق sidecars/README.md بگیرید
npm run tauri dev
```

ساخت بسته:

```bash
npm run tauri build
```

خروجی: `src-tauri/target/release/bundle/`

## مجوز و حمایت

| | |
|--|--|
| کد اپ | [MIT](LICENSE) |
| aria2c | [GPL-2+ sidecar](NOTICE) |
| حمایت | [DONATE.md](DONATE.md) · تب «حمایت» در اپ |

## مخزن

https://github.com/SahandM96/gamemap-torrent-downloader
