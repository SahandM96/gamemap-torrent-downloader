# مشارکت

1. Issue یا PR در [GitHub](https://github.com/SahandM96/gamemap-torrent-downloader).
2. قبل از PR: `cd _legacy && python3 selftest.py` و در صورت امکان `npm run tauri dev`.
3. **امنیت:** RPC aria2 فقط localhost؛ مقصد دانلود مطلق؛ لینک donate فقط `https://` در `open_url`.
4. **aria2:** sidecar جدا — لینک مستقیم به libaria2 در Rust ممنوع؛ [`NOTICE`](NOTICE).
5. تغییرات بزرگ منطق desk: [`docs/HANDOFF.md`](docs/HANDOFF.md) را به‌روز کن.
