# GameMap Torrent Desk

**GameMap Studio** — جست‌وجوی محلی در ایندکس‌های عمومی تورنت + مدیریت دانلود با **aria2c** (فرایند جدا، GPL-2+).

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)

مستندات کامل: [`docs/README.md`](docs/README.md)

## ویژگی‌ها

- رابط فارسی RTL، بدون وابستگی به فریم‌ورک فرانت‌اند سنگین
- جست‌وجو در چند منبع عمومی (از طریق پل Python در v0.1؛ قابل جایگزینی با Rust)
- دانلود magnet / فایل `.torrent`، صف، pause/resume، اسکن `*.aria2`
- **aria2c** به‌صورت sidecar (نه لینک داخل باینری اپ) — جزئیات در [`NOTICE`](NOTICE)

## نیازمندی‌ها (توسعه)

- Rust stable، Node.js 18+
- Linux: برای `tauri build` معمولاً `libwebkit2gtk-4.1-dev` و وابستگی‌های Tauri ([راهنما](https://v2.tauri.app/start/prerequisites/))
- `python3` برای پل جست‌وجو (`scripts/search_bridge.py` → `_legacy/sources.py`)
- باینری aria2 را طبق [`sidecars/README.md`](sidecars/README.md) بگیرید (یا PATH سیستم)

## اجرای dev

```bash
npm install
npm run tauri dev
```

## ساخت release

```bash
npm run tauri build
```

خروجی در `src-tauri/target/release/bundle/`.

## حمایت

[دونیت و لینک‌ها](DONATE.md) — داخل اپ تب **حمایت**.

## مجوز

کد اپ: **MIT** — [`LICENSE`](LICENSE).  
aria2c: **GPL-2+** — [`NOTICE`](NOTICE).

## ارتباط

مخزن: [`SahandM96/gamemap-torrent-downloader`](https://github.com/SahandM96/gamemap-torrent-downloader)
