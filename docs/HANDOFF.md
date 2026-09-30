# HANDOFF — منطق حیاتی دانلود

> برای توسعه‌دهندگانی که روی move/delete/partials کار می‌کنند.

## مأموریت

جست‌وجوی محلی در ایندکس‌های عمومی + dispatch به aria2c با **مقصد per-download** + مدیریت صف و فایل‌های `.aria2`.

## state.json (runtime — در git نیست)

`downloads[]`: `id, gid, name, magnet, torrent_url, info_hash, torrent_sha1, dir, files[], status, …`  
همچنین `recent_dirs`, `source_dirs`, `prefs`. مسیر تقریبی: app data dir سیستم‌عامل.

## منطق حیاتی (Rust — `desk.rs`)

1. متادیتای aria2 روی دیسک: `<sha1-of-torrent-bytes>.torrent` — هر دو `info_hash` و `torrent_sha1` ذخیره شوند.
2. `_item_paths` / معادل Rust: data + `.aria2` + `.torrent` برای move/delete.
3. **Snapshot** فایل‌ها وقتی GID زنده است؛ بعد از stop/purge GID از بین می‌رود.
4. **Move:** مسیر مطلق، جابه‌جایی همهٔ candidateها، remap `files[].path`، re-add برای resume.
5. **Delete:** stop + purge + unlink + rmtree ریشهٔ آیتم + prune پوشه‌های خالی.
6. **Partials:** walk روی roots شناخته‌شده؛ لینک control به download_id.
7. **Resume بعد از از دست رفتن GID:** re-add با `continue=true`؛ بدون magnet قابل resume نیست.
8. **Magnet → followedBy:** بعد از کامل شدن `[METADATA]`، GID واقعی محتوا را دنبال کن (UI نباید ۱۰۰٪ جعلی نشان دهد).

## Legacy Python

`_legacy/torrent_desk.py` سرور HTTP روی `:8811` — **فقط مرجع**؛ محصول shipping = Tauri + `ui/`.

## چک سریع

```bash
npm run tauri dev
# در اپ: health، یک magnet دستی، pause/resume
cd _legacy && python3 selftest.py
```

## TODO فنی (اختیاری)

- پورت کامل `sources.py` به Rust (حذف پل Python)
- parity کامل move/delete با E2E Python
- ICO واقعی برای Windows bundle
