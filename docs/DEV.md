# توسعه و build

## پیش‌نیازها

- Rust (rustup stable)، Node.js 18+
- [Tauri 2 prerequisites](https://v2.tauri.app/start/prerequisites/)

### Pop!_OS / Ubuntu جدید

از **Ayatana** استفاده کن، نه `libappindicator3-dev`:

```bash
sudo apt update
sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
  build-essential curl wget file libxdo-dev libssl-dev
```

## اجرا

```bash
npm install
npm run tauri dev
```

CLI: `~/.cargo/bin/cargo-tauri` (یا `npm run tauri`).

## Sidecar aria2

باینری‌ها در git نیستند — ببین [`sidecars/README.md`](../sidecars/README.md).

- بعد از دانلود: `sha256sum -c sidecars/aria2c.sha256` سپس `npm run prepare:sidecar`
- Tauri `externalBin` به کپی با target-triple نیاز دارد؛ `npm run dev` / `npm run build` خودشان `prepare:sidecar` را می‌زنند
- Override: env `TORRENT_DESK_ARIA_BIN=/path/to/aria2c`

## پل جست‌وجو

نیاز به `python3` و `_legacy/sources.py`. بدون Python، جست‌وجو/probe خطا می‌دهد؛ دانلود/aria2 همچنان کار می‌کند.

## Self-test (Python legacy)

منطق aria2 end-to-end (localhost Range server):

```bash
cd _legacy && python3 selftest.py   # expect SELFTEST PASS
```

## Release

```bash
npm run tauri build
```

GitHub Actions: `.github/workflows/release.yml` (tag `v*`).

## آیکون

Placeholder در `src-tauri/icons/`. برای ویندوز ICO واقعی:

```bash
cargo tauri icon path/to/source.png
```
