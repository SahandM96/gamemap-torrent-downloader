# API — `desk_api`

ورود از UI:

```js
invoke('desk_api', { req: { path, method, body } })
```

- موفق: `{ success: true, data }`
- خطا: `Err(String)` از invoke
- `path` می‌تواند با `/api/` شروع شود
- `body` برای POST شیء JSON است

## مسیرها

| Method | Path | توضیح |
|--------|------|--------|
| GET | `/api/health` | وضعیت aria2 و `default_dir` |
| GET | `/api/sources` | فهرست منابع |
| GET | `/api/downloads` | `{ aria2, items[] }` |
| GET | `/api/partials?root=` | اسکن `*.aria2` |
| GET | `/api/settings` | prefs و مسیرها |
| POST | `/api/search` | `{ query, sources?, limit? }` |
| POST | `/api/sources/probe` | تأخیر آینه‌ها |
| POST | `/api/download` | `{ magnet \| torrent_url, dir, … }` — `dir` مطلق |
| POST | `/api/downloads/{id}/pause` | |
| POST | `/api/downloads/{id}/resume` | |
| POST | `/api/downloads/{id}/stop` | |
| POST | `/api/downloads/{id}/reveal` | |
| POST | `/api/downloads/{id}/redir` | `{ dir, create_dir? }` |
| POST | `/api/downloads/{id}/delete` | `{ remove_files? }` |
| POST | `/api/partials/delete` | `{ control, remove_target? }` |
| POST | `/api/settings` | patch prefs |
| POST | `/api/daemon` | `{ action: start\|stop\|save\|purge\|log }` |
| POST | `/api/mkdir` | `{ parent, name }` |
| POST | `/api/proxy/test` | تست پروکسی |
| GET | `/api/proxy/detect` | تشخیص پورت‌های رایج پروکسی محلی |

## دستورات جدا

| Command | قرارداد |
|---------|---------|
| `pick_folder` | دیالوگ native |
| `open_url` | فقط `https://` |
| `donate_info` | آدرس TRX و متادیتای دونیت |
| `create_donation` | `{ amountIrr, description? }` → `{ payment_url, amount_irr }` |

دونیت ریالی از طریق `https://api.gamemap.ir/api/v1/donations/create` ساخته می‌شود.
