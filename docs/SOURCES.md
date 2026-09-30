# منابع جست‌وجو

پیاده‌سازی: `_legacy/sources.py` (از طریق `scripts/search_bridge.py`).

## دسته‌بندی مشکلات

| نوع | علامت | کار |
|-----|--------|-----|
| باگ extractor / mirror order | بعضی منابع همیشه ۰ نتیجه | fix در `sources.py` |
| بلاک شبکه (CF، DNS ایران) | probe ❌ | `prefs.proxy` یا `mirrors.json` |

## پروکسی

در تنظیمات اپ یا `POST /api/settings`:

```json
{"proxy": "socks5h://127.0.0.1:1080"}
```

## mirrors.json

در **ریشه مخزن** (اختیاری). `_apply_mirror_overrides()` **کل** لیست آینهٔ یک منبع را جایگزین می‌کند:

```json
{
  "1337x": ["https://mirror-that-works.example"],
  "yts": ["https://yts-mirror.example"],
  "fitgirl": ["https://fitgirl-repacks.site"]
}
```

بعد: «بررسی همه سایت‌ها» در UI یا `POST /api/sources/probe`.

## EZTV

API سرچ ندارد؛ فقط آخرین ~۴۰۰ عنوان. برای کوئری غیر TV طبیعی است نتیجه خالی باشد — برای سریال **در حال پخش** تست کن.

## یادداشت‌های منبع (نمونه وضعیت 2026-09-28)

- معمولاً پاسخ‌دهنده با fix شبکه/کد: archive، thepiratebay، torrentdownloads، limetorrents، nyaa (با HTML fallback)
- اغلب نیاز به پروکسی: 1337x، yts، fitgirl، torrentgalaxy
