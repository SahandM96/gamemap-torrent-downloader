# معماری

## نمای کلی

| لایه | فناوری | نقش |
|------|---------|-----|
| UI | `ui/` (HTML/CSS/JS) | RTL فارسی؛ `invoke('desk_api', …)` |
| Core | Rust / Tauri 2 (`src-tauri/`) | دانلود، صف، تنظیمات، RPC به aria2 |
| Search | `scripts/search_bridge.py` → `_legacy/sources.py` | جست‌وجو و probe منابع |
| Engine | aria2c sidecar | فرایند جدا؛ GPL-2+ — [`NOTICE`](../NOTICE) |

```
├── ui/                 # فرانت‌اند
├── src-tauri/src/      # desk, aria, store, net, sources, sidecar
├── sidecars/           # متادیتای aria2؛ باینری در git نیست
├── scripts/            # پل جست‌وجو
├── _legacy/            # مرجع Python + منابع جست‌وجو
└── docs/
```

## دادهٔ محلی (runtime)

مسیر app-data سیستم‌عامل (در git نیست):

| فایل | کاربرد |
|------|--------|
| `state.json` | دانلودها، prefs، recent_dirs |
| `run/aria2.session` | بازیابی صف aria2 |
| `run/rpc.secret` | توکن RPC محلی |
| `run/aria2.log` | لاگ daemon |
| `bin/aria2c` | کپی sidecar در صورت نیاز |

مقصد دانلود فقط مسیر **مطلق** پذیرفته می‌شود.

## UI → backend

`ui/app.js` مسیرهای `/api/*` را از طریق `desk_api` صدا می‌زند. پاسخ موفق: `{ success, data }`. دیالوگ پوشه: native Tauri (`pick_folder`)، نه browse روی LAN.

## چرخهٔ دانلود (ثابت‌های دامنه)

پیاده‌سازی اصلی: `src-tauri/src/desk.rs`.

1. متادیتای روی دیسک: `<sha1(torrent-bytes)>.torrent` — هر دو `info_hash` و `torrent_sha1` ذخیره شوند.
2. مسیرهای یک آیتم: داده + `.aria2` + `.torrent` برای move/delete.
3. وقتی GID زنده است از aria2 snapshot بگیر؛ بعد از stop/purge GID از بین می‌رود.
4. **Move:** مسیر مطلق، جابه‌جایی candidateها، remap `files[].path`، re-add برای resume.
5. **Delete:** stop + purge + unlink + حذف ریشهٔ آیتم + prune پوشهٔ خالی.
6. **Partials:** اسکن roots برای `*.aria2` و اتصال به `download_id`.
7. **Resume بدون GID:** re-add با `continue=true`؛ بدون magnet قابل resume نیست.
8. **Magnet:** پس از `[METADATA]`، GID محتوا را از `followedBy` دنبال کن (نمایش ۱۰۰٪ جعلی ممنوع).

`_legacy/torrent_desk.py` فقط مرجع تاریخی است؛ محصول shipping = Tauri + `ui/`.
