# توسعه

## پیش‌نیاز

- Rust (rustup stable)
- Node.js 18+
- Python 3
- [پیش‌نیازهای سیستم Tauri 2](https://v2.tauri.app/start/prerequisites/)

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
  build-essential curl wget file libxdo-dev libssl-dev
```

از `libappindicator3-dev` روی نسخه‌های جدید استفاده نکنید؛ بستهٔ Ayatana کافی است.

## اجرا

```bash
npm install
# باینری aria2: sidecars/README.md
npm run tauri dev
```

`npm run dev` و `npm run build` خودشان `prepare:sidecar` را اجرا می‌کنند.

| متغیر | کاربرد |
|-------|--------|
| `TORRENT_DESK_ARIA_BIN` | مسیر صریح aria2c |

بدون Python، جست‌وجو/probe خطا می‌دهد؛ دانلود و aria2 همچنان کار می‌کند.

## تست

```bash
cd _legacy && python3 selftest.py   # انتظار: SELFTEST PASS
```

## انتشار

```bash
npm run tauri build
```

CI: `.github/workflows/release.yml` روی تگ `v*`.

آیکون‌ها در `src-tauri/icons/`. برای تولید مجدد:

```bash
cargo tauri icon path/to/source.png
```
