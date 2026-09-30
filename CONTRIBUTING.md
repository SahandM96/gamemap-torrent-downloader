# مشارکت

1. Issue یا PR در [مخزن](https://github.com/SahandM96/gamemap-torrent-downloader).
2. قبل از PR: `cd _legacy && python3 selftest.py` و در صورت امکان `npm run tauri dev`.
3. RPC aria2 فقط localhost؛ مسیر دانلود مطلق؛ `open_url` فقط `https://`.
4. aria2 فقط به‌صورت sidecar — لینک به libaria2 ممنوع ([`NOTICE`](NOTICE)).
5. تغییر منطق دانلود: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) را هم به‌روز کنید.
