# aria2 sidecar (GPL-2+)

نسخهٔ پین‌شده: **1.37.0** — فرایند جدا؛ به crate لینک نمی‌شود ([`NOTICE`](../NOTICE)).

باینری در git نیست. قبل از build:

```bash
mkdir -p sidecars
curl -fsSL -o sidecars/aria2c \
  https://github.com/abcfy2/aria2-static-build/releases/download/1.37.0/aria2c-x86_64-linux-musl
chmod +x sidecars/aria2c
sha256sum -c sidecars/aria2c.sha256
bash sidecars/prepare-tauri-external.sh
```

Windows / macOS: طبق `.github/workflows/release.yml` باینری را در `sidecars/aria2c` (یا `aria2c.exe`) بگذارید، سپس همان اسکریپت prepare.

| در git | نقش |
|--------|-----|
| `VERSION`, `SOURCE.txt` | pin و provenance |
| `aria2c.sha256` | hash لینوکس musl بالا |
| `prepare-tauri-external.sh` | کپی به `aria2c-$TARGET_TRIPLE` برای Tauri `externalBin` |
