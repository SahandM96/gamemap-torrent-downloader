# معماری

## خلاصه

اپ دسکتاپ **Tauri 2** (Rust) + UI استاتیک فارسی در `ui/`. منطق دانلود و aria2 در Rust (`src-tauri/src/desk.rs`, `aria.rs`, `store.rs`, `net.rs`). جست‌وجوی منابع در v0.1 از **پل Python** (`scripts/search_bridge.py` → `_legacy/sources.py`) تا پورت کامل Rust آماده شود.

**aria2c** همیشه **sidecar** است (فرایند جدا، GPL-2+) — در باینری Rust لینک نمی‌شود. جزئیات: [`NOTICE`](../NOTICE).

```
gamemap-torrent-desk/
├── ui/                    # index.html, app.js, app.css — invoke → desk_api
├── src-tauri/
│   ├── src/
│   │   ├── lib.rs         # Tauri entry, plugins (dialog, shell)
│   │   ├── commands.rs    # desk_api, pick_folder, open_url, donate_info
│   │   ├── desk.rs        # downloads, move/delete/partials, settings
│   │   ├── aria.rs        # daemon + JSON-RPC
│   │   ├── store.rs       # state.json + prefs
│   │   ├── net.rs         # fetch, magnet, bencode
│   │   ├── sidecar.rs     # resolve bundled aria2c
│   │   └── sources.rs     # bridge to Python search
│   └── tauri.conf.json
├── sidecars/              # meta + sha256؛ باینری را لوکال/CI بگیرید (README)
├── scripts/search_bridge.py
├── _legacy/               # Python stdlib stack (reference + bridge)
└── docs/                  # این پوشه
```

## دادهٔ محلی

- **App data** (Tauri): `state.json`, `run/aria2.session`, `run/rpc.secret`, `run/aria2.log`, `bin/aria2c` (کپی sidecar)
- **مسیرها:** فقط **مطلق** برای مقصد دانلود (امنیت: بدون browse HTTP روی LAN)

## UI → backend

مرورگر embedded دیگر `fetch('/api/...')` ندارد. `ui/app.js` با `invoke('desk_api', { req: { path, method, body } })` همان مسیرهای `/api/*` را صدا می‌زند؛ پاسخ `{ success, data }`.
