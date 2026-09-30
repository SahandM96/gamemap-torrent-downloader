# aria2 sidecars (GPL-2+)

Pinned version: **1.37.0** (mere aggregation — separate process, not linked).

**باینری‌ها داخل git نیستند.** قبل از `npm run dev` / `npm run build` یکی را بگیرید:

```bash
# Linux x86_64 (musl static) — همان فایل CI
mkdir -p sidecars
curl -fsSL -o sidecars/aria2c \
  https://github.com/abcfy2/aria2-static-build/releases/download/1.37.0/aria2c-x86_64-linux-musl
chmod +x sidecars/aria2c
sha256sum -c sidecars/aria2c.sha256
bash sidecars/prepare-tauri-external.sh
```

Windows / macOS: اسکریپت‌های `.github/workflows/release.yml` یا باینری رسمی aria2 را در `sidecars/aria2c` (یا `aria2c.exe`) بگذارید، بعد `prepare-tauri-external.sh`.

| File in git | Purpose |
|-------------|---------|
| `VERSION` / `SOURCE.txt` | Pin + provenance |
| `aria2c.sha256` | Expected hash for Linux musl build above |
| `prepare-tauri-external.sh` | Copies `aria2c` → `aria2c-$TARGET_TRIPLE` for Tauri `externalBin` |

Do not link this binary into the Rust crate. See root `NOTICE`.
